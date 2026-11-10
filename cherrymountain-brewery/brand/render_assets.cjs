// Renders the raster files of this folder from icon.svg, icon-small.svg, and tokens.json:
// icon-<n>.png, favicon.ico (16, 32, 48), and the wallpaper (wallpaper.png at 3840x2160 and
// wallpaper-1080p.png), which wallpaper.cjs draws. Run by hand after a change
// to either SVG or the tokens:  node brand/render_assets.cjs   (needs Playwright with Chromium).
// The outputs are committed, so nothing on the range needs a browser.
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');
const { wallpaperSvg, W, H } = require('./wallpaper.cjs');

const here = __dirname;
const tokens = JSON.parse(fs.readFileSync(path.join(here, 'tokens.json'), 'utf8'));
const c = tokens.colors;
const svg = (name) => fs.readFileSync(path.join(here, name), 'utf8');
const uri = (name) => 'data:image/svg+xml;base64,' + Buffer.from(svg(name)).toString('base64');
// The small drawing up to 32 px, the full one above.
const SIZES = [[16, 'icon-small.svg'], [32, 'icon-small.svg'], [48, 'icon.svg'], [64, 'icon.svg'],
  [128, 'icon.svg'], [256, 'icon.svg'], [512, 'icon.svg']];

function ico(pngs) { // [[size, Buffer]] -> an ICO that holds the PNGs as they are
  const head = Buffer.alloc(6 + 16 * pngs.length);
  head.writeUInt16LE(0, 0); head.writeUInt16LE(1, 2); head.writeUInt16LE(pngs.length, 4);
  let offset = head.length;
  pngs.forEach(([size, data], i) => {
    const at = 6 + 16 * i;
    head.writeUInt8(size, at); head.writeUInt8(size, at + 1); head.writeUInt8(0, at + 2); head.writeUInt8(0, at + 3);
    head.writeUInt16LE(1, at + 4); head.writeUInt16LE(32, at + 6);
    head.writeUInt32LE(data.length, at + 8); head.writeUInt32LE(offset, at + 12);
    offset += data.length;
  });
  return Buffer.concat([head, ...pngs.map(([, data]) => data)]);
}

(async () => {
  const browser = await chromium.launch();
  const made = {};
  for (const [size, source] of SIZES) {
    const page = await browser.newPage({ viewport: { width: size, height: size }, deviceScaleFactor: 1 });
    await page.setContent(`<body style="margin:0;background:transparent"><img src="${uri(source)}" style="display:block;width:${size}px;height:${size}px"></body>`);
    made[size] = await page.screenshot({ omitBackground: true });
    fs.writeFileSync(path.join(here, `icon-${size}.png`), made[size]);
    await page.close();
  }
  fs.writeFileSync(path.join(here, 'favicon.ico'), ico([16, 32, 48].map((n) => [n, made[n]])));

  // The wallpaper is drawn by wallpaper.cjs; rendered at 3840x2160 and again at 1920x1080.
  const art = 'data:image/svg+xml;base64,' + Buffer.from(wallpaperSvg(tokens)).toString('base64');
  for (const [w, h, name] of [[W, H, 'wallpaper.png'], [1920, 1080, 'wallpaper-1080p.png']]) {
    const page = await browser.newPage({ viewport: { width: w, height: h }, deviceScaleFactor: 1 });
    await page.setContent(`<body style="margin:0;background:${c.ink}"><img src="${art}" style="display:block;width:${w}px;height:${h}px"></body>`);
    await page.waitForFunction(() => document.images[0].complete && document.images[0].naturalWidth > 0);
    fs.writeFileSync(path.join(here, name), await page.screenshot());
    await page.close();
  }
  await browser.close();
  console.log('wrote', SIZES.map(([n]) => `icon-${n}.png`).join(' '), 'favicon.ico wallpaper.png wallpaper-1080p.png');
})();
