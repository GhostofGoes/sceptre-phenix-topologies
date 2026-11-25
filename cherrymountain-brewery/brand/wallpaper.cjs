// The desktop wallpaper as one SVG string: the Front Range at dusk in the brand's colors, the
// phoenix of the mark rising in front of the sun, and the brewery in the foothills. It is drawn
// by this code from a fixed seed, so it comes out the same every time. render_assets.cjs turns it
// into wallpaper.png (3840x2160) and wallpaper-1080p.png.
//
// Layout, for a desktop: the upper left is dark sky (desktop icons stay readable there), the
// picture sits right of center, and the name is at the lower left.

const W = 3840;
const H = 2160;

function rng(seed) { // mulberry32
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6D2B79F5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const hex = (c) => [1, 3, 5].map((i) => parseInt(c.slice(i, i + 2), 16));
const mix = (a, b, t) => {
  const [x, y] = [hex(a), hex(b)];
  return '#' + x.map((v, i) => Math.round(v + (y[i] - v) * t).toString(16).padStart(2, '0')).join('');
};
/** The color at t (0 to 1) of a list of [position, color] stops. */
function ramp(stops, t) {
  for (let i = 1; i < stops.length; i += 1) {
    if (t <= stops[i][0]) {
      const [t0, c0] = stops[i - 1];
      const [t1, c1] = stops[i];
      return mix(c0, c1, (t - t0) / (t1 - t0));
    }
  }
  return stops[stops.length - 1][1];
}

/** Smooth 1-D value noise with octaves, 0 to 1. */
function noise1(seed) {
  const r = rng(seed);
  const table = Array.from({ length: 512 }, () => r());
  const at = (x) => {
    const i = Math.floor(x);
    const f = x - i;
    const s = f * f * (3 - 2 * f);
    return table[i & 511] * (1 - s) + table[(i + 1) & 511] * s;
  };
  return (x, octaves = 5, ridged = false) => {
    let sum = 0, amp = 1, total = 0, freq = 1;
    for (let o = 0; o < octaves; o += 1) {
      const v = at(x * freq);
      sum += (ridged ? 1 - Math.abs(2 * v - 1) : v) * amp; total += amp; amp *= 0.5; freq *= 2.03;
    }
    return sum / total;
  };
}

function wallpaperSvg(tokens) {
  const c = tokens.colors;
  const out = [];
  const add = (s) => out.push(s);
  const CX = 2080;            // the summit and the phoenix
  const SUN = { x: CX, y: 1000, r: 500 };

  // ---- sky: flat rings of dusk around the sun, ember near it and ink far away
  const SKY = [[0, '#F9C768'], [0.05, c.flame], [0.1, '#EC7A31'], [0.17, c.ember], [0.27, '#A8421F'],
    [0.38, '#6E2F22'], [0.5, '#472628'], [0.64, '#2E2228'], [0.8, '#211D23'], [1, c.ink]];
  add(`<rect width="${W}" height="${H}" fill="${c.ink}"/>`);
  const RMAX = 3000;
  const RINGS = 40;
  for (let i = RINGS; i >= 1; i -= 1) {
    const t = i / RINGS;
    // rings are flattened: dusk light spreads along the horizon
    add(`<ellipse cx="${SUN.x}" cy="${SUN.y + 260}" rx="${(RMAX * t * 1.25).toFixed(0)}" ry="${(RMAX * t * 0.62).toFixed(0)}" fill="${ramp(SKY, Math.pow(t, 0.8))}"/>`);
  }

  // ---- stars, where the sky is dark
  const rs = rng(1806);
  for (let i = 0; i < 520; i += 1) {
    const x = rs() * W;
    const y = rs() * 1250;
    const dx = (x - SUN.x) / 1.25;
    const dy = (y - SUN.y - 260) / 0.62;
    const far = Math.hypot(dx, dy) / RMAX;
    const size = 1.2 + rs() * rs() * 4.2;
    if (far < 0.5) { continue; }
    const fade = Math.min(1, (far - 0.5) / 0.25);
    add(`<circle cx="${x.toFixed(0)}" cy="${y.toFixed(0)}" r="${size.toFixed(1)}" fill="${rs() < 0.14 ? c.flame : c.paper}" opacity="${(fade * (0.35 + rs() * 0.6)).toFixed(2)}"/>`);
  }

  // ---- the sun
  add(`<circle cx="${SUN.x}" cy="${SUN.y}" r="${SUN.r + 46}" fill="#F9C768" opacity=".35"/>`);
  add(`<circle cx="${SUN.x}" cy="${SUN.y}" r="${SUN.r}" fill="#FBD489"/>`);

  // ---- far ridges, lighter and warmer with distance
  const ridge = (seed, base, amp, color, step = 8, scale = 520, octaves = 5, lift = null, ridged = false) => {
    const n = noise1(seed);
    const pts = [];
    for (let x = -20; x <= W + 20; x += step) {
      let y = base - amp * n(x / scale + 3.1, octaves, ridged);
      if (lift) { y -= lift(x); }
      pts.push([x, y]);
    }
    add(`<path d="M-20 ${H} L${pts.map(([x, y]) => `${x} ${y.toFixed(1)}`).join(' L')} L${W + 20} ${H} Z" fill="${color}"/>`);
    return pts;
  };
  ridge(11, 1560, 330, '#8A4530', 6, 620, 6, null, true);
  ridge(23, 1650, 360, '#6B3A33', 6, 500, 6, null, true);
  ridge(37, 1730, 340, '#503136', 6, 420, 6, null, true);

  // ---- the phoenix of the mark, in front of the sun (coordinates of icon.svg, scaled)
  const S = 27;
  const place = `translate(${SUN.x} ${SUN.y - 20}) scale(${S}) translate(-32 -24)`;
  const EMBER_DEEP = '#B93D1C';
  add(`<g transform="${place}">
    <g transform="translate(0 2)">
      <path d="M31 30 C29 36 25 41 19.5 44.5 C22.5 39 24.5 35 27 29.5 Z" fill="${c.ember}"/>
      <path d="M33 30 C35 36 39 41 44.5 44.5 C41.5 39 39.5 35 37 29.5 Z" fill="${c.ember}"/>
      <path d="M32 29 C35 36 34.5 43 32 49 C29.5 43 29 36 32 29 Z" fill="${EMBER_DEEP}"/>
    </g>
    <path d="M30.5 25 C26 22 21 18 17.5 12 C17.5 17 18.5 20 20 23 C17.5 22 15.5 22 13.5 22.5 C16 26 18.5 28 21.5 29.5 C19.5 30 18 30.5 16.5 31.5 C21 34 26 33.5 30.5 31 Z" fill="${c.ember}"/>
    <path d="M33.5 25 C38 22 43 18 46.5 12 C46.5 17 45.5 20 44 23 C46.5 22 48.5 22 50.5 22.5 C48 26 45.5 28 42.5 29.5 C44.5 30 46 30.5 47.5 31.5 C43 34 38 33.5 33.5 31 Z" fill="${c.ember}"/>
    <path d="M30.5 25 C26 22 21 18 17.5 12 C18.6 17.4 20.6 20.6 23.4 23.6 C25.6 25 28 25.4 30.5 25 Z" fill="${EMBER_DEEP}"/>
    <path d="M33.5 25 C38 22 43 18 46.5 12 C45.4 17.4 43.4 20.6 40.6 23.6 C38.4 25 36 25.4 33.5 25 Z" fill="${EMBER_DEEP}"/>
    <path d="M30.5 26.5 C27 24.5 23.5 21.5 21 17.5 C21.5 21.5 23 24.5 25.5 27.5 C27 28.5 29 28.5 30.5 28 Z" fill="${c.ink}"/>
    <path d="M33.5 26.5 C37 24.5 40.5 21.5 43 17.5 C42.5 21.5 41 24.5 38.5 27.5 C37 28.5 35 28.5 33.5 28 Z" fill="${c.ink}"/>
    <path d="M32 19 C35.2 22 35.6 28 32 36 C28.4 28 28.8 22 32 19 Z" fill="${c.ember}"/>
    <g transform="translate(-1 3)">
      <path d="M31 13.2 C29 11 28 8.6 28.6 5.6 C29.8 8.2 31.4 10 33.8 12.4 Z M29.6 14.6 C27 13.4 25 11.4 24.4 8.6 C26.4 10.6 28.2 11.8 30.4 13.4 Z M29 16.6 C26.6 16.4 24.6 15.4 23.2 13.4 C25.2 14.4 27 14.8 29.2 15 Z" fill="${c.ember}"/>
      <path d="M30.2 24 L29.6 19.5 C29.2 17 29.6 14.6 31.2 13.4 C32.8 12.2 35.2 12.4 37 14 L40.4 15.4 C41.4 16.2 41.8 17.4 41.4 19 C40.9 18.2 40.4 17.8 39.8 17.6 L37.6 17.9 C37.2 18.8 36.6 19.4 35.8 19.8 L34.4 20.6 L34 24 Z" fill="${c.ember}" stroke="#FBD489" stroke-width=".7" stroke-linejoin="round" paint-order="stroke"/>
      <path d="M37 14 L40.4 15.4 C41.4 16.2 41.8 17.4 41.4 19 C40.9 18.2 40.4 17.8 39.8 17.6 L37.6 17.9 C37.6 16.6 37.4 15.2 37 14 Z" fill="${c.ink}"/>
      <path d="M33.5 15.1 L36.7 15.3 L34.4 16.9 Z" fill="${c.paper}"/>
    </g>
  </g>`);

  // ---- the summit: a faceted peak in front of the sun, with snow
  const AP = { x: CX, y: 1235 };
  const face = (pts, fill, opacity = 1) => add(`<path d="M${pts.map(([x, y]) => `${x} ${y}`).join(' L')} Z" fill="${fill}"${opacity < 1 ? ` opacity="${opacity}"` : ''}/>`);
  const DARK = '#1E1C22';
  const LIT = '#4A3A40';
  face([[AP.x, AP.y], [AP.x - 1040, 1960], [AP.x + 150, 1960], [AP.x - 40, 1640], [AP.x + 62, 1440]], DARK);
  face([[AP.x, AP.y], [AP.x + 62, 1440], [AP.x - 40, 1640], [AP.x + 150, 1960], [AP.x + 1060, 1960]], LIT);
  // spurs and facets on both faces
  face([[AP.x + 62, 1440], [AP.x + 330, 1650], [AP.x + 250, 1800], [AP.x + 470, 1960], [AP.x + 150, 1960], [AP.x - 40, 1640]], '#3A2E35');
  face([[AP.x + 250, 1492], [AP.x + 520, 1700], [AP.x + 430, 1790], [AP.x + 330, 1650]], '#5A444A');
  face([[AP.x + 430, 1612], [AP.x + 760, 1830], [AP.x + 640, 1960], [AP.x + 470, 1960], [AP.x + 250, 1800], [AP.x + 330, 1650], [AP.x + 430, 1790], [AP.x + 520, 1700]], '#42343B');
  face([[AP.x - 300, 1444], [AP.x - 430, 1700], [AP.x - 260, 1830], [AP.x - 520, 1960], [AP.x - 1040, 1960]], '#17161B');
  face([[AP.x - 120, 1318], [AP.x - 210, 1560], [AP.x - 90, 1700], [AP.x - 40, 1640], [AP.x + 62, 1440], [AP.x, AP.y]], '#26232A');
  // snow: lit side warm, shaded side cool
  face([[AP.x, AP.y], [AP.x - 150, 1340], [AP.x - 96, 1376], [AP.x - 122, 1432], [AP.x - 54, 1398], [AP.x - 22, 1470], [AP.x + 24, 1392], [AP.x + 62, 1440]], '#C9C4CB');
  face([[AP.x, AP.y], [AP.x + 62, 1440], [AP.x + 96, 1388], [AP.x + 150, 1470], [AP.x + 176, 1402], [AP.x + 250, 1492], [AP.x + 213, 1420]], '#FCE3C4');
  face([[AP.x - 300, 1444], [AP.x - 352, 1492], [AP.x - 318, 1518], [AP.x - 340, 1560], [AP.x - 262, 1512]], '#A9A5AE');

  // ---- nearer ridges, darker, with pines along them
  const pines = (pts, color, r, every, hMin, hMax) => {
    const parts = [];
    for (let i = 0; i < pts.length; i += 1) {
      if (r() > every) { continue; }
      const [x, y] = pts[i];
      const h = hMin + r() * (hMax - hMin);
      const w = h * (0.3 + r() * 0.12);
      const base = y + 6;
      for (let k = 0; k < 3; k += 1) {
        const top = base - h + k * h * 0.2;
        const bot = base - h * 0.62 + k * h * 0.26;
        const half = w * (0.42 + k * 0.29);
        parts.push(`M${x.toFixed(0)} ${top.toFixed(0)} L${(x - half).toFixed(1)} ${bot.toFixed(0)} L${(x + half).toFixed(1)} ${bot.toFixed(0)} Z`);
      }
      parts.push(`M${(x - w * 0.07).toFixed(1)} ${(base - h * 0.2).toFixed(0)} h${(w * 0.14).toFixed(1)} v${(h * 0.24).toFixed(0)} h${(-w * 0.14).toFixed(1)} Z`);
    }
    add(`<path d="${parts.join(' ')}" fill="${color}"/>`);
  };
  const rp = rng(4840);
  const r4 = ridge(53, 1830, 250, '#2A252C', 6, 380, 5);
  pines(r4, '#2A252C', rp, 0.55, 26, 60);
  const r5 = ridge(71, 1960, 230, '#1E1C22', 6, 330, 5);
  pines(r5, '#1E1C22', rp, 0.6, 44, 96);

  // ---- the brewery, on a shelf of the nearest hill
  const BX = 2480, BY = 1905;                 // left end and floor line
  const BLACK = '#101013';
  const STEEL = '#3A3A44';
  const b = [];
  // the shelf it stands on
  b.push(`<path d="M${BX - 260} ${BY + 60} C${BX - 120} ${BY - 4} ${BX + 60} ${BY} ${BX + 200} ${BY} H${BX + 980} C${BX + 1090} ${BY} ${BX + 1180} ${BY + 22} ${BX + 1280} ${BY + 70} V${H} H${BX - 260} Z" fill="#17161A"/>`);
  // grain silo with a ladder and a spout
  b.push(`<rect x="${BX + 40}" y="${BY - 330}" width="92" height="330" fill="${BLACK}"/><path d="M${BX + 40} ${BY - 330} L${BX + 86} ${BY - 384} L${BX + 132} ${BY - 330} Z" fill="${BLACK}"/>`);
  b.push(`<path d="M${BX + 58} ${BY - 322} V${BY - 8} M${BX + 70} ${BY - 322} V${BY - 8}" stroke="${STEEL}" stroke-width="2"/>`);
  for (let y = BY - 312; y < BY - 10; y += 16) { b.push(`<path d="M${BX + 58} ${y} H${BX + 70}" stroke="${STEEL}" stroke-width="2"/>`); }
  b.push(`<path d="M${BX + 132} ${BY - 300} L${BX + 196} ${BY - 262} V${BY - 250} L${BX + 132} ${BY - 286} Z" fill="${BLACK}"/>`);
  // brewhouse: tall gabled hall with a stack and lit windows
  b.push(`<path d="M${BX + 176} ${BY} V${BY - 236} L${BX + 336} ${BY - 322} L${BX + 496} ${BY - 236} V${BY} Z" fill="${BLACK}"/>`);
  b.push(`<rect x="${BX + 410}" y="${BY - 420}" width="30" height="170" fill="${BLACK}"/><rect x="${BX + 404}" y="${BY - 426}" width="42" height="10" fill="${BLACK}"/>`);
  const win = (x, y, w, h, color = c.flame, o = 1) => b.push(`<rect x="${x}" y="${y}" width="${w}" height="${h}" fill="${color}" opacity="${o}"/>`);
  for (let i = 0; i < 4; i += 1) { win(BX + 208 + i * 68, BY - 190, 40, 76, i === 2 ? '#F9C768' : c.flame, 0.95); }
  for (let i = 0; i < 4; i += 1) { b.push(`<path d="M${BX + 228 + i * 68} ${BY - 190} v76 M${BX + 208 + i * 68} ${BY - 152} h40" stroke="${BLACK}" stroke-width="3"/>`); }
  win(BX + 312, BY - 276, 48, 30, c.ember, 0.9);
  b.push(`<rect x="${BX + 300}" y="${BY - 88}" width="72" height="88" fill="#F9C768"/><path d="M${BX + 336} ${BY - 88} V${BY}" stroke="${BLACK}" stroke-width="4"/>`);
  // steam from the kettle stack
  [[0, 0, 26], [16, -44, 34], [-6, -96, 42], [22, -158, 52], [-10, -230, 60]].forEach(([dx, dy, r], i) => {
    b.push(`<circle cx="${BX + 425 + dx}" cy="${BY - 446 + dy}" r="${r}" fill="${c.paper}" opacity="${(0.2 - i * 0.03).toFixed(2)}"/>`);
  });
  // cellar: five cylindroconical fermenters on legs, with manways, a catwalk, and a glycol header
  for (let i = 0; i < 5; i += 1) {
    const x = BX + 540 + i * 78;
    const top = BY - 268;
    b.push(`<path d="M${x} ${top + 26} C${x} ${top - 8} ${x + 62} ${top - 8} ${x + 62} ${top + 26} V${BY - 96} L${x + 37} ${BY - 40} H${x + 25} L${x} ${BY - 96} Z" fill="#4A4A55"/>`);
    b.push(`<path d="M${x + 44} ${top + 22} V${BY - 100}" stroke="#8C8C9A" stroke-width="6" opacity=".9"/>`);
    b.push(`<path d="M${x + 6} ${top + 30} V${BY - 100}" stroke="#2C2C34" stroke-width="8" opacity=".8"/>`);
    b.push(`<path d="M${x + 4} ${BY - 96} V${BY} M${x + 58} ${BY - 96} V${BY} M${x + 31} ${BY - 40} V${BY - 14}" stroke="${BLACK}" stroke-width="5"/>`);
    b.push(`<circle cx="${x + 20}" cy="${top + 86}" r="8" fill="${BLACK}"/><rect x="${x + 16}" y="${BY - 170}" width="30" height="6" fill="${c.ember}" opacity=".85"/>`);
  }
  b.push(`<path d="M${BX + 530} ${BY - 286} H${BX + 926} M${BX + 530} ${BY - 306} H${BX + 926}" stroke="${BLACK}" stroke-width="5"/>`);
  for (let x = BX + 534; x <= BX + 926; x += 28) { b.push(`<path d="M${x} ${BY - 306} V${BY - 286}" stroke="${BLACK}" stroke-width="3"/>`); }
  b.push(`<path d="M${BX + 540} ${BY - 132} H${BX + 918}" stroke="${c.steel}" stroke-width="5" opacity=".7"/>`);
  // taproom: low building with a row of warm windows, a sign with the mark, and string lights
  b.push(`<path d="M${BX + 940} ${BY} V${BY - 150} L${BX + 1180} ${BY - 176} V${BY} Z" fill="${BLACK}"/>`);
  for (let i = 0; i < 5; i += 1) { win(BX + 962 + i * 42, BY - 112, 28, 62, i % 2 ? '#F9C768' : c.flame, 0.95); }
  b.push(`<circle cx="${BX + 1060}" cy="${BY - 212}" r="34" fill="${BLACK}"/><circle cx="${BX + 1060}" cy="${BY - 212}" r="27" fill="none" stroke="${c.flame}" stroke-width="3"/>`);
  b.push(`<path d="M${BX + 1042} ${BY - 198} L${BX + 1060} ${BY - 226} L${BX + 1078} ${BY - 198} Z" fill="${c.ember}"/><path d="M${BX + 1060} ${BY - 178} V${BY - 164}" stroke="${BLACK}" stroke-width="6"/>`);
  b.push(`<path d="M${BX + 1180} ${BY - 170} Q${BX + 1250} ${BY - 110} ${BX + 1330} ${BY - 150}" fill="none" stroke="${BLACK}" stroke-width="2"/>`);
  for (let i = 0; i <= 8; i += 1) {
    const t = i / 8;
    const x = (1 - t) * (1 - t) * (BX + 1180) + 2 * (1 - t) * t * (BX + 1250) + t * t * (BX + 1330);
    const y = (1 - t) * (1 - t) * (BY - 170) + 2 * (1 - t) * t * (BY - 110) + t * t * (BY - 150);
    b.push(`<circle cx="${x.toFixed(0)}" cy="${(y + 5).toFixed(0)}" r="4.5" fill="${i % 3 ? c.flame : c.ember}"/>`);
  }
  b.push(`<rect x="${BX + 1326}" y="${BY - 156}" width="7" height="226" fill="${BLACK}"/>`);
  b.push(`<path d="M${BX + 1333} ${BY - 150} Q${BX + 1360} ${BY - 120} ${W + 20} ${BY - 138}" fill="none" stroke="${BLACK}" stroke-width="2"/>`);
  // solar on the brewhouse roof and the service pole with its transformer
  for (let i = 0; i < 5; i += 1) {
    const x0 = BX + 352 + i * 26, y0 = BY - 308 + i * 14;
    b.push(`<path d="M${x0} ${y0} l22 12 l-3 16 l-22 -12 Z" fill="${c.steel}" opacity=".55"/>`);
  }
  b.push(`<rect x="${BX - 150}" y="${BY - 250}" width="9" height="290" fill="${BLACK}"/><rect x="${BX - 178}" y="${BY - 238}" width="66" height="7" fill="${BLACK}"/><rect x="${BX - 138}" y="${BY - 206}" width="24" height="36" rx="5" fill="${BLACK}"/>`);
  b.push(`<path d="M${BX - 176} ${BY - 236} Q${BX - 60} ${BY - 196} ${BX + 40} ${BY - 300} M${BX - 114} ${BY - 236} Q${BX - 20} ${BY - 206} ${BX + 42} ${BY - 284}" fill="none" stroke="${BLACK}" stroke-width="2"/>`);
  b.push(`<path d="M-20 ${BY - 110} Q${(BX - 176) / 2} ${BY - 60} ${BX - 176} ${BY - 236}" fill="none" stroke="${BLACK}" stroke-width="2" opacity=".8"/>`);
  add(b.join(''));

  // ---- foreground: the nearest slope and its tall pines, in front of everything
  const rf = rng(502);
  const front = ridge(97, 2110, 190, '#131316', 6, 300, 4, (x) => (x < 1500 ? (1500 - x) * 0.09 : 0) + (x > 3500 ? (x - 3500) * 0.2 : 0));
  pines(front.filter(([x]) => x < BX - 200), '#131316', rf, 0.5, 70, 190);

  // ---- the name, lower left
  add(`<g transform="translate(170 1790)">
    <text x="0" y="0" font-family="'DejaVu Sans','Liberation Sans',Arial,sans-serif" font-size="104" font-weight="700" fill="${c.paper}" letter-spacing="-1">CherryMountain Brewery</text>
    <rect x="2" y="34" width="150" height="8" fill="${c.ember}"/>
    <text x="176" y="52" font-family="'DejaVu Sans','Liberation Sans',Arial,sans-serif" font-size="40" fill="${c.flame}">Colorado Front Range</text>
  </g>`);

  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}">${out.join('\n')}</svg>`;
}

module.exports = { wallpaperSvg, W, H };
