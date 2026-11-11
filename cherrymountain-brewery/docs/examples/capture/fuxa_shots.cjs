// Screenshot views of a running FUXA with headless Chromium (Playwright).
//
//   node fuxa_shots.cjs '<json>'
//
// The JSON names: base (FUXA's URL), out (a directory), width and height of the
// page (default 1440x900, the range's desktop), and shots: a list of
//   { name, view,              the PNG's name (no extension) and the FUXA view
//     user,                    "operator" signs in first (needs password)
//     password,
//     alarms,                  true opens the header's alarm list
//     login,                   true opens the sign-in dialog instead of signing in
//     wait }                   milliseconds to wait for values (default 7000)
// It prints one JSON line per shot with the texts it found in the view, so a
// view that never got values is visible in the log.
const { chromium } = require('playwright');

async function main() {
  const o = { width: 1440, height: 900, ...JSON.parse(process.argv[2]) };
  const browser = await chromium.launch();
  try {
    for (const s of o.shots) {
      const ctx = await browser.newContext({ viewport: { width: o.width, height: o.height }, deviceScaleFactor: 1 });
      const page = await ctx.newPage();
      const errors = [];
      page.on('pageerror', (e) => errors.push(String(e).slice(0, 200)));
      await page.goto(`${o.base}/home?viewName=${encodeURIComponent(s.view)}`, { waitUntil: 'networkidle' });
      if (s.user) {
        await page.locator('button:has(mat-icon:text-is("account_circle"))').first().click();
        const dialog = page.locator('mat-dialog-container');
        await dialog.waitFor({ timeout: 10000 });
        const inputs = dialog.locator('input');
        await inputs.nth(0).fill(s.user);
        await inputs.nth(1).fill(s.password);
        await inputs.nth(1).press('Enter');
        await dialog.waitFor({ state: 'detached', timeout: 10000 });
      }
      if (s.login) {
        await page.locator('button:has(mat-icon:text-is("account_circle"))').first().click();
        await page.locator('mat-dialog-container').waitFor({ timeout: 10000 });
      }
      await page.waitForTimeout(s.wait || 7000);
      if (s.alarms) {
        await page.locator('.alarm-button button').first().click();
        await page.waitForTimeout(2500);
      }
      await page.screenshot({ path: `${o.out}/${s.name}.png` });
      const texts = (await page.locator('svg text').allTextContents()).map((t) => t.trim()).filter(Boolean);
      console.log(JSON.stringify({ name: s.name, view: s.view, n_texts: texts.length, sample: texts.slice(0, 14), errors }));
      await ctx.close();
    }
  } finally {
    await browser.close();
  }
}

main().catch((e) => { console.error(e); process.exit(1); });
