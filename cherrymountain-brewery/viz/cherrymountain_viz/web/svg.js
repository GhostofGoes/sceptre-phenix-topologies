// cm-viz 2.5D fallback: the same frames as the 3D page, drawn as SVG and
// redrawn only when a frame arrives (about 1 % of a core, no WebGL). Read only.
const params = new URLSearchParams(location.search);
const SCENES = ['cellar', 'fermenters', 'power'];
let scene = SCENES.includes(params.get('scene')) ? params.get('scene') : 'cellar';
const STALE_MS = 3000;
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
const fmt = (x, d = 0) => (x === null || x === undefined ? '–'
  : Number(x).toLocaleString('en-US', { maximumFractionDigits: d, minimumFractionDigits: d }));
const viz = (window.__viz = { ready: true, frames: 0, msgs: 0, last: null, stale: true, scene, renderer: 'svg', webgl: 0 });

const tierColor = (ppm) => (ppm >= 30000 ? '#ff2b2b' : ppm >= 15000 ? '#ff8c1a' : ppm >= 5000 ? '#ffe04a' : '#4cc38a');
const tempColor = (t) => (t === null ? '#555' : t < 40 ? '#37a6d9' : t < 60 ? '#d9b54a' : t < 72 ? '#e39a2d' : '#e0602a');
const pressColor = (p) => (p === null || p < 0.5 ? '#7d8790' : p < 13.5 ? '#4cc38a' : p < 14.9 ? '#ffb020' : '#ff3b30');
const text = (x, y, s, cls = '', fill = '') => `<text x="${x}" y="${y}" class="${cls}"${fill ? ` style="fill:${fill}"` : ''}>${esc(s)}</text>`;

function cellar(f, detail) {
  const a = f.cellar.air;
  const avg = a.sensors_ppm.length ? a.sensors_ppm.reduce((x, y) => x + y, 0) / a.sensors_ppm.length : 0;
  const W = 1200;
  const H = 560;
  const floor = 470;
  const layer = 20 + 260 * Math.min(Math.max((avg - 1000) / 39000, 0), 1);
  let s = `<svg viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg">`;
  s += `<rect x="20" y="40" width="${W - 40}" height="${floor - 40}" fill="#22282f" stroke="#3a4149"/>`;
  s += `<rect x="20" y="${floor - layer}" width="${W - 40}" height="${layer}" fill="${tierColor(avg)}" opacity="${0.12 + 0.3 * Math.min(avg / 30000, 1)}"/>`;
  if (f.operator) {
    const hmi = f.operator.co2_sensors_ppm.reduce((x, y) => x + y, 0) / 4;
    const hl = 20 + 260 * Math.min(Math.max((hmi - 1000) / 39000, 0), 1);
    if (Math.abs(hmi - avg) > 0.1 * avg) {
      s += `<line x1="20" x2="${W - 20}" y1="${floor - hl}" y2="${floor - hl}" stroke="#fff" stroke-dasharray="8 6"/>`;
      s += text(30, floor - hl - 6, `HMI shows ${fmt(hmi)} ppm`, 'big', '#ff8a80');
    }
  }
  f.cellar.vessels.forEach((v, i) => {
    const x = 80 + i * 175;
    const w = v.kind === 'fv' ? 110 : 90;
    const h = v.kind === 'fv' ? 260 : 200;
    const top = floor - 40 - h;
    const fill = (h * Math.min(Math.max(v.level_pct ?? 0, 0), 100)) / 100;
    s += `<rect x="${x}" y="${top + h - fill}" width="${w}" height="${fill}" fill="${tempColor(v.temp_f)}"/>`;
    s += `<rect x="${x}" y="${top}" width="${w}" height="${h}" class="shell"/>`;
    s += `<rect x="${x - 4}" y="${top + 10}" width="${w + 8}" height="8" fill="${pressColor(v.pressure_psig)}"/>`;
    v.glycol_valves.forEach((open, j) => {
      s += `<rect x="${x - 6}" y="${top + h * (0.45 + 0.35 * j)}" width="${w + 12}" height="6" fill="${open ? '#37a6d9' : '#555b62'}"/>`;
    });
    s += `<circle cx="${x + 18}" cy="${top - 10}" r="7" fill="${v.psv_lifted ? '#ff3b30' : '#555b62'}"/>`;
    if (v.psv_lifted) s += text(x + 30, top - 6, `PSV ${fmt(v.psv_gps, 1)} g/s`, '', '#ff6b5e');
    s += text(x, top - 40, v.label, 'big');
    s += text(x, top - 22, `${fmt(v.temp_f, 1)} °F · ${fmt(v.pressure_psig, 1)} psig`);
    s += text(x, floor - 10, `${fmt(v.level_pct)} % ${v.level_measured ? '' : '(truth)'}`, 'small');
    if (detail && v.kind === 'fv') s += text(x, floor + 8, `spund SP ${fmt(v.spund_sp_psig, 1)} psig`, 'small');
    if (v.leak_gps > 0) s += text(x, floor - 30, `LEAK ${fmt(v.leak_gps)} g/s`, '', '#ff6b5e');
  });
  a.sensors_ppm.forEach((ppm, i) => {
    const x = 120 + i * 260;
    s += `<rect x="${x}" y="${floor - 30}" width="16" height="16" fill="${tierColor(ppm)}"/>`;
    s += text(x + 22, floor - 17, `CO₂-${i + 1} ${fmt(ppm)} ppm`, '', tierColor(ppm));
  });
  a.fans.forEach((fan, i) => {
    s += `<circle cx="${45}" cy="${200 + i * 120}" r="20" fill="${fan.running ? '#4cc38a' : '#555b62'}"/>`;
    s += text(70, 205 + i * 120, `Purge fan ${i + 1}: ${fan.running ? 'running' : fan.commanded ? 'commanded, stopped' : 'off'}`, 'small');
  });
  const relay = a.relay_ok === false ? 'OPEN: carbonation inhibited' : 'closed';
  s += text(W - 420, 70, `Gas-detection relay ${relay}`, 'big', a.relay_ok === false ? '#ff6b5e' : '#5cc98a');
  s += text(W - 420, 92, `Well mixed ${fmt(a.co2_mixed_ppm)} ppm · into the room ${fmt(a.co2_into_room_gps, 1)} g/s`, 'small');
  const tier = a.tiers[2] ? 'Tier 3: evacuate' : a.tiers[1] ? 'Tier 2' : a.tiers[0] ? 'Tier 1' : 'Normal';
  s += text(W - 420, 112, `Alarm: ${tier}`, '', a.tiers[1] ? '#ff6b5e' : '#e6e9ed');
  return `${s}</svg>`;
}

function power(f) {
  const p = f.power;
  const W = 1200;
  const live = (b) => (b === false ? 'dead' : 'live');
  let s = `<svg viewBox="0 0 ${W} 520" xmlns="http://www.w3.org/2000/svg">`;
  const util = p.utility_ok && p.utility_breaker_closed;
  s += `<line x1="40" y1="200" x2="340" y2="200" class="${util ? 'live' : 'dead'}" stroke-width="5"/>`;
  s += text(40, 185, `Utility ${p.utility_ok ? '' : 'LOST '}· breaker ${p.utility_breaker_closed ? 'closed' : 'OPEN'} · ${fmt(p.grid_kw)} kW`);
  s += `<line x1="340" y1="200" x2="900" y2="200" class="${live(p.buses.msb)}" stroke-width="9"/>`;
  s += text(520, 230, `MSB load ${fmt(p.load_kw)} kW · demand ${fmt(p.demand_kw)} kW`);
  s += `<line x1="420" y1="80" x2="420" y2="200" class="${p.pv_connected ? 'live' : 'dead'}" stroke-width="4"/>`;
  s += text(380, 70, `PV ${fmt(p.pv_kw)} kW (limit ${fmt(p.pv_limit_pct)} %)`);
  s += `<line x1="640" y1="80" x2="640" y2="200" class="${p.bess_connected ? 'live' : 'dead'}" stroke-width="4"/>`;
  s += `<rect x="600" y="40" width="${0.8 * (p.bess_soc_pct ?? 0)}" height="14" fill="#4cc38a"/><rect x="600" y="40" width="80" height="14" fill="none" stroke="#aab4be"/>`;
  s += text(690, 52, `BESS ${fmt(p.bess_soc_pct)} % · ${fmt(p.bess_kw)} kW`);
  s += `<line x1="900" y1="200" x2="990" y2="200" class="${p.ats_on_emergency ? 'dead' : 'live'}" stroke-width="5"/>`;
  s += `<line x1="1060" y1="80" x2="1060" y2="200" class="${p.gen_running ? 'live' : 'dead'}" stroke-width="5"/>`;
  s += text(960, 70, `Genset ${p.gen_running ? `RUNNING ${fmt(p.gen_kw)} kW` : 'stopped'}`, '', p.gen_running ? '#ffb020' : '');
  s += `<rect x="990" y="180" width="70" height="40" fill="#707a84"/>`;
  s += text(995, 240, `ATS ${p.ats_on_emergency ? 'EMERGENCY' : 'normal'}`, '', p.ats_on_emergency ? '#ffb020' : '#5cc98a');
  s += `<line x1="1025" y1="220" x2="1025" y2="330" class="${live(p.buses.ep1)}" stroke-width="5"/>`;
  s += text(960, 350, `EP-1 ${fmt(p.ep1_kw)} kW`);
  const buses = [...new Set(p.loads.map((l) => l.bus))].filter((b) => b !== 'ep1');
  buses.forEach((bus, i) => {
    const x = 360 + i * 95;
    const kw = p.loads.filter((l) => l.bus === bus).reduce((a, l) => a + l.kw, 0);
    s += `<line x1="${x}" y1="200" x2="${x}" y2="330" class="${live(p.buses[bus])}" stroke-width="4"/>`;
    s += text(x - 20, 350, `${bus.toUpperCase()} ${fmt(kw)} kW`, 'small');
  });
  s += text(40, 420, `${p.onpeak ? 'ON-PEAK' : 'off-peak'} · $${fmt(p.rate_usd_kwh, 3)}/kWh · month $${fmt(p.cost_usd)}`, 'big', p.onpeak ? '#ff6b5e' : '#5cc98a');
  return `${s}</svg>`;
}

function draw(f) {
  viz.last = f;
  viz.frames += 1;
  $('simtime').textContent = f.sim_time ? f.sim_time.replace('T', ' ').slice(0, 19) : '–';
  $('source').textContent = f.source;
  $('drawing').innerHTML = scene === 'power' ? power(f) : cellar(f, scene === 'fermenters');
  $('findings').innerHTML = f.findings.map((x) => `<li class="${esc(x.level)}">${esc(x.text)}</li>`).join('');
}

function setScene(s) {
  scene = s;
  viz.scene = s;
  document.querySelectorAll('[data-scene]').forEach((b) => b.classList.toggle('on', b.dataset.scene === s));
  if (viz.last) draw(viz.last);
}
document.querySelectorAll('[data-scene]').forEach((b) => b.addEventListener('click', () => setScene(b.dataset.scene)));

let lastMsgAt = 0;
function connect() {
  const ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`);
  ws.onmessage = (ev) => { lastMsgAt = Date.now(); viz.msgs += 1; draw(JSON.parse(ev.data)); };
  ws.onclose = () => setTimeout(connect, 1000);
}
setInterval(() => {
  const f = viz.last;
  const now = Date.now();
  const age = f ? now - f.sampled_wall_ms : Infinity;
  const stale = !f || f.stale || now - lastMsgAt > STALE_MS || age > 5000;
  viz.stale = stale;
  document.body.classList.toggle('stale', stale);
  $('stale').hidden = !stale || !f;
  const pill = $('age');
  pill.textContent = f ? `${fmt(age / 1000, 1)} s old` : 'no frame';
  pill.className = `pill ${stale ? 'dead' : age > 2500 ? 'late' : 'fresh'}`;
}, 500);
setScene(scene);
connect();
