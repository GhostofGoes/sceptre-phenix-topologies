// Renders the raster files of this folder from icon.svg, icon-small.svg, and tokens.json:
// icon-<n>.png, favicon.ico (16, 32, 48), and wallpaper.png (1920x1080). Run by hand after a change
// to either SVG or the tokens:  node brand/render_assets.cjs   (needs Playwright with Chromium).
// The outputs are committed, so nothing on the range needs a browser.
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

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

  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 1 });
  await page.setContent(`<body style="margin:0;width:1920px;height:1080px;background:${c.ink};overflow:hidden;position:relative;
      font-family:'DejaVu Sans','Liberation Sans',Arial,sans-serif;color:${c.paper}">
    <svg viewBox="0 0 1920 1080" style="position:absolute;inset:0" aria-hidden="true">
      <path d="M0 1080 V820 L210 640 L330 740 L560 500 L700 660 L820 580 L1010 800 L1160 690 L1340 860 L1500 760 L1700 900 L1920 780 V1080 Z" fill="${c.coal}"/>
      <rect x="0" y="1068" width="1920" height="12" fill="${c.ember}"/>
      <rect x="0" y="1062" width="1920" height="6" fill="${c.flame}"/>
    </svg>
    <div style="position:absolute;left:150px;top:330px;display:flex;align-items:center;gap:56px">
      <img src="${uri('icon.svg')}" style="width:320px;height:320px">
      <div><div style="font-size:92px;font-weight:700;line-height:1.05;letter-spacing:-1px">CherryMountain<br>Brewery</div>
        <div style="font-size:30px;margin-top:22px;color:${c.flame}">Colorado Front Range</div></div>
    </div></body>`);
  fs.writeFileSync(path.join(here, 'wallpaper.png'), await page.screenshot());
  await browser.close();
  console.log('wrote', SIZES.map(([n]) => `icon-${n}.png`).join(' '), 'favicon.ico wallpaper.png');
})();
