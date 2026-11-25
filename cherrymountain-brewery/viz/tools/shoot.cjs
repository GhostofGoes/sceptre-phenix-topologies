// Screenshot one cm-viz page with headless Chromium (Playwright).
//
//   node shoot.cjs '<json>'
//
// The JSON names: url, out (PNG path), width, height, scale (deviceScaleFactor),
// wait (a JavaScript expression over `f`, the latest frame, that must hold
// before the shot; default: any frame), settle_ms (time for easing and
// particles after the condition holds), timeout_ms.
// Prints one JSON line with the WebGL renderer and the frame that was shown.
const { chromium } = require('playwright');

async function main() {
  const o = { width: 1600, height: 900, scale: 2, wait: 'true', settle_ms: 2500, timeout_ms: 60000, ...JSON.parse(process.argv[2]) };
  const browser = await chromium.launch();
  try {
    // bypassCSP: the wait condition is evaluated in the page, and the page's
    // CSP forbids eval (it stays in force for real viewers).
    const page = await browser.newPage({
      viewport: { width: o.width, height: o.height }, deviceScaleFactor: o.scale, bypassCSP: true,
    });
    const errors = [];
    page.on('pageerror', (e) => errors.push(String(e)));
    await page.goto(o.url);
    await page.waitForFunction(
      `(() => { const v = window.__viz; if (!v || !v.ready || !v.last) return false;
                const f = v.last; return Boolean(${o.wait}); })()`,
      undefined,
      { timeout: o.timeout_ms, polling: 200 },
    );
    await page.waitForTimeout(o.settle_ms);
    await page.screenshot({ path: o.out });
    const info = await page.evaluate(() => ({
      renderer: window.__viz.renderer, webgl: window.__viz.webgl, scene: window.__viz.scene,
      sim_time: window.__viz.last.sim_time, stale: window.__viz.stale,
    }));
    console.log(JSON.stringify({ out: o.out, ...info, errors }));
  } finally {
    await browser.close();
  }
}

main().catch((e) => { console.error(e); process.exit(1); });
