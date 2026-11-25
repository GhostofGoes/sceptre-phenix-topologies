// Browser smoke test for cm-viz (run by test_browser.py): node browser_smoke.cjs <base URL>
//
// Loads each 3D scene and the SVG fallback in headless Chromium and checks that
// WebGL came up, frames render, and the data binding works (the HUD shows the
// frame's simulated time, the side panel has rows, labels follow the scene).
// Prints one JSON line per page; exits non-zero on the first failure.
const assert = require('node:assert/strict');
const { chromium } = require('playwright');

const base = process.argv[2];
const staleBase = process.argv[3];

// A stale feed greys the page out and shows the NO DATA banner, and the
// layout stays as it was (body.stale must not pick up the banner's styles).
async function checkStale(browser, path) {
  const page = await browser.newPage({ viewport: { width: 1280, height: 720 } });
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  await page.goto(`${staleBase}/${path}?scene=cellar`);
  await page.waitForFunction(
    () => window.__viz && window.__viz.last && window.__viz.stale === true
      && document.body.classList.contains('stale'),
    null, { timeout: 30000 },
  );
  const banner = page.locator('#stale');
  assert.ok(await banner.isVisible(), 'the NO DATA banner shows');
  assert.match(await banner.textContent(), /NO DATA/);
  const bb = await banner.boundingBox();
  assert.ok(bb.width > 200 && bb.width < 900, `banner width ${bb.width}`);
  const body = await page.evaluate(() => {
    const r = document.body.getBoundingClientRect();
    return { w: r.width, pos: getComputedStyle(document.body).position };
  });
  assert.equal(body.w, 1280, 'the body keeps the viewport width');
  assert.notEqual(body.pos, 'fixed');
  if (path === 'svg.html') {
    const w = (await page.locator('#drawing').boundingBox()).width;
    assert.ok(w > 1000, `the 2.5D drawing keeps its width (${w})`);
  } else {
    const c = await page.locator('#scene').boundingBox();
    assert.equal(c.width, 1280, 'the canvas fills the viewport');
    const side = await page.locator('.side').boundingBox();
    assert.ok(side.x > 800, `the side panel stays on the right (${side.x})`);
  }
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ path, stale_layout: 'ok' }));
  await page.close();
}

async function check(browser, path, scene, extra) {
  const page = await browser.newPage({ viewport: { width: 1280, height: 720 }, bypassCSP: false });
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
  await page.goto(`${base}/${path}?scene=${scene}`);
  await page.waitForFunction(() => window.__viz && window.__viz.last && window.__viz.frames > 3, null, { timeout: 30000 });
  const r = await page.evaluate(() => ({
    webgl: window.__viz.webgl, renderer: window.__viz.renderer, frames: window.__viz.frames,
    scene: window.__viz.scene, simTime: window.__viz.last.sim_time, stale: window.__viz.stale,
    shown: document.getElementById('simtime').textContent,
  }));
  assert.equal(r.scene, scene);
  assert.equal(r.stale, false);
  assert.equal(r.shown, r.simTime.replace('T', ' ').slice(0, 19), 'the HUD shows the frame time');
  await extra(page, r);
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ path, scene, ...r }));
  await page.close();
}

(async () => {
  const browser = await chromium.launch();
  try {
    for (const scene of ['cellar', 'fermenters', 'power']) {
      await check(browser, 'index.html', scene, async (page, r) => {
        assert.ok(r.webgl >= 1, 'a WebGL context');
        assert.ok(r.renderer.length > 0);
        const rows = await page.locator('#compare tr').count();
        assert.ok(rows >= (scene === 'fermenters' ? 6 : 8), `side panel rows for ${scene}: ${rows}`);
        const title = await page.locator('#side-title').textContent();
        assert.equal(title.startsWith('Plant power'), scene === 'power');
        // The rendered canvas is not blank.
        const png = await page.locator('#scene').screenshot();
        assert.ok(png.length > 20000, `canvas image ${png.length} bytes`);
      });
    }
    // The view toggle redraws from the operator's values.
    await check(browser, 'index.html', 'cellar', async (page) => {
      await page.click('[data-view="operator"]');
      await page.waitForFunction(() => window.__viz.view === 'operator');
      assert.match(await page.locator('#banner').textContent(), /OPERATOR VIEW/);
    });
    await check(browser, 'svg.html', 'power', async (page) => {
      assert.ok(await page.locator('#drawing svg').count() === 1);
      assert.match(await page.locator('#drawing').textContent(), /MSB load/);
    });
    await checkStale(browser, 'index.html');
    await checkStale(browser, 'svg.html');
  } finally {
    await browser.close();
  }
})().catch((e) => { console.error(e); process.exit(1); });
