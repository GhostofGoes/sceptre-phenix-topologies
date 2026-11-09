// viz3d spike: measure the pages in Chromium with no GPU (Playwright).
//
//   node measure.cjs --base http://127.0.0.1:8090 --cases a,b --label 4cpu \
//     --out results-4cpu.json [--shots DIR] [--prefix NAME-] [--seconds 10]
//
// For each case it launches Chromium, loads the page, waits 3 s, then counts
// rendered frames, the browser's CPU time (all its processes, from /proc), the
// bridge's CPU time (/stats.json), and the age of each WebSocket frame on
// arrival. Run it under `taskset -c 0,1` to emulate a 2-vCPU VM and under
// `xvfb-run` for the headed cases.
const { chromium } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const { execSync } = require('node:child_process');

const CLK_TCK = Number(execSync('getconf CLK_TCK').toString().trim());
const HD = { width: 1280, height: 720 };
const FHD = { width: 1920, height: 1080 };
const NO_SWIFTSHADER = { ignoreDefaultArgs: ['--enable-unsafe-swiftshader'] };
const LLVMPIPE = { args: ['--use-gl=angle', '--use-angle=gl', '--ignore-gpu-blocklist'], ...NO_SWIFTSHADER };
// The flags cm-desktop's /etc/chromium.d/cm-range sets for every range desktop.
const CM_DESKTOP = ['--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage'];

const CASES = {
  'webgl-720': { page: '/', viewport: HD, shot: 'cellar-3d-720.png', gltf: true },
  'webgl-1080': { page: '/', viewport: FHD },
  'webgl-720-fps15': { page: '/?fps=15', viewport: HD },
  'webgl-720-aa': { page: '/?aa=1', viewport: HD },
  'webgl-no-swiftshader-flag': { page: '/', viewport: HD, launch: NO_SWIFTSHADER },
  'svg-720': { page: '/svg.html', viewport: HD, shot: 'cellar-svg-720.png' },
  'headed-default-720': { page: '/', viewport: HD, headless: false },
  'headed-llvmpipe-720': { page: '/', viewport: HD, headless: false, launch: LLVMPIPE },
  'headed-llvmpipe-720-fps15': { page: '/?fps=15', viewport: HD, headless: false, launch: LLVMPIPE },
  'headed-no-swiftshader-flag': {
    page: '/', viewport: HD, headless: false, launch: NO_SWIFTSHADER, shot: 'no-webgl-headed.png',
  },
  'headed-cm-desktop-flags': { page: '/', viewport: HD, headless: false, launch: { args: CM_DESKTOP, ...NO_SWIFTSHADER } },
  // No ignoreDefaultArgs here: it would also strip the flag passed in args.
  'headed-cm-desktop-flags-swiftshader': {
    page: '/', viewport: HD, headless: false, launch: { args: [...CM_DESKTOP, '--enable-unsafe-swiftshader'] },
  },
  'fanout-20': { fanout: 20 },
};

function arg(name, fallback) {
  const i = process.argv.indexOf(`--${name}`);
  return i > 0 ? process.argv[i + 1] : fallback;
}

// All Chromium processes Playwright started (they run from its browser cache).
function browserProcs() {
  const procs = [];
  for (const pid of fs.readdirSync('/proc').filter((d) => /^\d+$/.test(d))) {
    try {
      const exe = fs.readlinkSync(`/proc/${pid}/exe`);
      if (!/pw-browsers|ms-playwright/.test(exe)) continue;
      const stat = fs.readFileSync(`/proc/${pid}/stat`, 'utf8');
      const f = stat.slice(stat.lastIndexOf(')') + 2).split(' ');
      const status = fs.readFileSync(`/proc/${pid}/status`, 'utf8');
      const rss = Number((status.match(/VmRSS:\s+(\d+)/) || [0, 0])[1]);
      const cmd = fs.readFileSync(`/proc/${pid}/cmdline`, 'utf8').split('\0');
      procs.push({ pid, ticks: Number(f[11]) + Number(f[12]), rssKiB: rss, cmd });
    } catch { /* the process went away */ }
  }
  return procs;
}
const cpuSeconds = () => browserProcs().reduce((a, p) => a + p.ticks, 0) / CLK_TCK;
const pct = (xs, q) => {
  if (!xs.length) return null;
  const s = [...xs].sort((a, b) => a - b);
  return Math.round(s[Math.min(s.length - 1, Math.floor(q * s.length))]);
};
const stats = async (base) => (await fetch(`${base}/stats.json`)).json();
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function fanout(base, n, seconds) {
  const s0 = await stats(base);
  const url = base.replace(/^http/, 'ws') + '/ws';
  let got = 0;
  const sockets = Array.from({ length: n }, () => {
    const ws = new WebSocket(url);
    ws.onmessage = () => { got += 1; };
    return ws;
  });
  await sleep(1000);
  const s1 = await stats(base);
  const t1 = Date.now();
  await sleep(seconds * 1000);
  const s2 = await stats(base);
  const dt = (Date.now() - t1) / 1000;
  sockets.forEach((ws) => ws.close());
  return {
    clients: n, messages_received: got,
    bridge_cpu_pct: +((100 * (s2.cpu_s - s1.cpu_s)) / dt).toFixed(1),
    bridge_kbit_per_s: +((8 * (s2.bytes_sent - s1.bytes_sent)) / dt / 1000).toFixed(1),
    frame_bytes: s2.last_frame_bytes, bridge_rss_mib: +(s2.max_rss_kib / 1024).toFixed(1),
    baseline_messages_sent: s0.messages_sent,
  };
}

async function runCase(base, name, c, seconds, shots, prefix) {
  if (c.fanout) return { name, ...(await fanout(base, c.fanout, seconds)) };
  c = c.shot ? { ...c, shot: prefix + c.shot } : c;
  const out = { name, page: c.page, viewport: `${c.viewport.width}x${c.viewport.height}`, headless: c.headless !== false };
  const browser = await chromium.launch({ headless: c.headless !== false, ...(c.launch || {}) });
  try {
    out.browser_version = browser.version();
    const main = browserProcs().find((p) => !p.cmd.some((a) => a.startsWith('--type=')));
    out.flags = main ? main.cmd.filter((a) => /swiftshader|angle|use-gl|headless|gpu/i.test(a)) : [];
    const page = await browser.newPage({ viewport: c.viewport, deviceScaleFactor: 1 });
    const consoleErrors = [];
    page.on('console', (m) => { if (m.type() === 'error') consoleErrors.push(m.text().slice(0, 200)); });
    page.on('pageerror', (e) => consoleErrors.push(e.message.slice(0, 200)));
    const loadT0 = Date.now();
    await page.goto(base + c.page);
    await page.waitForFunction(() => window.__viz && (window.__viz.ready || window.__viz.error), null, { timeout: 30000 })
      .catch(() => {});
    out.first_frame_ms = Date.now() - loadT0;
    const info = await page.evaluate(() => ({
      ready: !!(window.__viz && window.__viz.ready), error: window.__viz ? window.__viz.error : 'no __viz',
      renderer: window.__viz ? window.__viz.renderer : '', webgl: window.__viz ? window.__viz.webgl : 0,
    }));
    Object.assign(out, info);
    out.console_errors = consoleErrors.slice(0, 3);
    if (!info.ready) {
      if (c.shot && shots) await page.screenshot({ path: path.join(shots, c.shot) });
      return out;
    }
    await sleep(3000);
    const v0 = await page.evaluate(() => ({ frames: __viz.frames, msgs: __viz.msgs }));
    const cpu0 = cpuSeconds();
    const b0 = await stats(base);
    const t0 = Date.now();
    await sleep(seconds * 1000);
    const v1 = await page.evaluate(() => ({ frames: __viz.frames, msgs: __viz.msgs, ages: __viz.ages, stateAges: __viz.stateAges }));
    const cpu1 = cpuSeconds();
    const b1 = await stats(base);
    const dt = (Date.now() - t0) / 1000;
    const n = v1.msgs - v0.msgs;
    Object.assign(out, {
      seconds: +dt.toFixed(1),
      fps: +((v1.frames - v0.frames) / dt).toFixed(1),
      browser_cpu_cores: +((cpu1 - cpu0) / dt).toFixed(2),
      browser_rss_mib: Math.round(browserProcs().reduce((a, p) => a + p.rssKiB, 0) / 1024),
      frames_received_per_s: +(n / dt).toFixed(2),
      transport_age_ms_p50: pct(v1.ages.slice(-n), 0.5),
      transport_age_ms_p95: pct(v1.ages.slice(-n), 0.95),
      state_age_ms_p50: pct(v1.stateAges.slice(-n), 0.5),
      state_age_ms_p95: pct(v1.stateAges.slice(-n), 0.95),
      bridge_cpu_pct: +((100 * (b1.cpu_s - b0.cpu_s)) / dt).toFixed(1),
      ws_kbit_per_s: +((8 * (b1.bytes_sent - b0.bytes_sent)) / dt / 1000).toFixed(1),
      frame_bytes: b1.last_frame_bytes,
    });
    // One full frame as JPEG quality 80: a rough upper bound for what a VNC
    // or Guacamole session would send per frame of continuous animation.
    out.jpeg_q80_frame_bytes = (await page.screenshot({ type: 'jpeg', quality: 80 })).length;
    if (c.gltf) out.gltf = await page.evaluate(gltfRoundTrip);
    if (c.shot && shots) await page.screenshot({ path: path.join(shots, c.shot) });
  } finally {
    await browser.close();
  }
  return out;
}

// In the page: export the vessels to binary glTF and load them back, to show the
// same viewer takes Blender-authored glTF assets.
async function gltfRoundTrip() {
  const { GLTFExporter } = await import('three/addons/exporters/GLTFExporter.js');
  const { GLTFLoader } = await import('three/addons/loaders/GLTFLoader.js');
  const groups = __viz.scene.children.filter((o) => o.type === 'Group');
  const meshes = (root) => { let n = 0; root.traverse((o) => { if (o.isMesh) n += 1; }); return n; };
  const before = groups.reduce((a, g) => a + meshes(g), 0);
  const t0 = performance.now();
  const glb = await new GLTFExporter().parseAsync(groups, { binary: true });
  const t1 = performance.now();
  const gltf = await new GLTFLoader().parseAsync(glb, '');
  const t2 = performance.now();
  return {
    groups: groups.length, meshes_exported: before, meshes_loaded: meshes(gltf.scene),
    glb_bytes: glb.byteLength, export_ms: Math.round(t1 - t0), load_ms: Math.round(t2 - t1),
  };
}

(async () => {
  const base = arg('base', 'http://127.0.0.1:8090');
  const names = arg('cases', 'webgl-720').split(',');
  const seconds = Number(arg('seconds', '10'));
  const shots = arg('shots', '');
  if (shots) fs.mkdirSync(shots, { recursive: true });
  const results = { label: arg('label', ''), nproc: Number(execSync('nproc').toString()), cases: [] };
  for (const name of names) {
    const c = CASES[name];
    if (!c) throw new Error(`unknown case ${name}; have ${Object.keys(CASES).join(', ')}`);
    const r = await runCase(base, name, c, seconds, shots, arg('prefix', ''));
    console.log(JSON.stringify(r));
    results.cases.push(r);
  }
  fs.writeFileSync(arg('out', 'results.json'), JSON.stringify(results, null, 2) + '\n');
})();
