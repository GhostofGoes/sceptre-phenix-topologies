// Shared helpers for the cm-viz scenes: colors, text labels, particle plumes,
// and flow arrows. Geometry is built in code; there are no binary assets.
import * as THREE from 'three';

export const clamp = (x, lo, hi) => Math.min(hi, Math.max(lo, x));

export const fmt = (x, d = 0) => {
  if (x === null || x === undefined || Number.isNaN(Number(x))) return '–';
  const r = Number(Number(x).toFixed(d));
  return (r === 0 ? 0 : r).toLocaleString('en-US', { maximumFractionDigits: d, minimumFractionDigits: d });
};

export const lambert = (color, extra = {}) => new THREE.MeshLambertMaterial({ color, ...extra });
export const basic = (color, extra = {}) => new THREE.MeshBasicMaterial({ color, ...extra });

// Piecewise-linear color ramp over [value, hex] stops.
export function ramp(stops, x) {
  if (x <= stops[0][0]) return new THREE.Color(stops[0][1]);
  for (let i = 1; i < stops.length; i++) {
    const [x1, c1] = stops[i];
    const [x0, c0] = stops[i - 1];
    if (x <= x1) return new THREE.Color(c0).lerp(new THREE.Color(c1), (x - x0) / (x1 - x0));
  }
  return new THREE.Color(stops[stops.length - 1][1]);
}

export const COLORS = {
  ok: 0x4cc38a, warn: 0xffb020, bad: 0xff3b30, off: 0x555b62, info: 0x6aa9ff,
  glycol: 0x37a6d9, steel: 0xaab4be, text: '#e6e9ed', muted: '#9aa4ae',
};
export const hex = (c) => `#${c.toString(16).padStart(6, '0')}`;
export const TEMP_STOPS = [[28, 0x2f6fe0], [40, 0x37a6d9], [55, 0xd9b54a], [66, 0xe39a2d], [75, 0xe0602a], [85, 0xd22f2f]];
export const GAS_STOPS = [[1000, 0x8fd0ff], [5000, 0xffe04a], [15000, 0xff8c1a], [30000, 0xff2b2b]];
export const tierColor = (ppm) =>
  (ppm >= 30000 ? 0xff2b2b : ppm >= 15000 ? 0xff8c1a : ppm >= 5000 ? 0xffe04a : COLORS.ok);
// Head pressure against the 15 psig MAWP and PSV set point.
export const pressureColor = (psig) =>
  (psig === null || psig < 0.5 ? COLORS.off : psig < 13.5 ? COLORS.ok : psig < 14.9 ? COLORS.warn : COLORS.bad);

// A camera-facing text label drawn on a canvas; draw([[text, color], ...]).
// ``scale`` is its width in metres, or with ``screen`` a fraction of the view
// height, so it reads the same at any distance. ``rows`` fixes the line height,
// so labels with fewer lines keep the same text size.
export function makeLabel(width = 320, height = 120, scale = 3.4, { screen = false, rows = 0 } = {}) {
  const c = document.createElement('canvas');
  c.width = width;
  c.height = height;
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  const sprite = new THREE.Sprite(new THREE.SpriteMaterial({
    map: tex, depthTest: false, transparent: true, sizeAttenuation: !screen,
  }));
  sprite.scale.set(scale, (scale * height) / width, 1);
  sprite.renderOrder = 10;
  let lastKey = '';
  const draw = (lines, bg = 'rgba(16,19,23,0.80)') => {
    const key = JSON.stringify([lines, bg]);
    if (key === lastKey) return; // redraw only on change (cheap on software GL)
    lastKey = key;
    const ctx = c.getContext('2d');
    ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = bg;
    ctx.fillRect(0, 0, width, height);
    const h = height / Math.max(rows || lines.length, 1);
    lines.forEach(([text, color], i) => {
      ctx.fillStyle = color || COLORS.text;
      ctx.font = `${i === 0 ? 'bold ' : ''}${Math.floor(h * 0.6)}px system-ui, sans-serif`;
      ctx.fillText(text, 10, h * (i + 0.72));
    });
    tex.needsUpdate = true;
  };
  return { sprite, draw };
}

const DOT = (() => {
  const c = document.createElement('canvas');
  c.width = c.height = 32;
  const ctx = c.getContext('2d');
  const g = ctx.createRadialGradient(16, 16, 0, 16, 16, 16);
  g.addColorStop(0, 'rgba(255,255,255,1)');
  g.addColorStop(1, 'rgba(255,255,255,0)');
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, 32, 32);
  return new THREE.CanvasTexture(c);
})();

// Heavier-than-air CO2: particles leave the source, fall, and spread on the
// floor. A picture of a flow rate, not a dispersion model.
export class Plume {
  constructor(n, { color, size, opacity, life, speed, up, bounds }) {
    Object.assign(this, { n, life, speed, up, bounds });
    this.pos = new Float32Array(n * 3).fill(-100);
    this.vel = new Float32Array(n * 3);
    this.age = new Float32Array(n).fill(life);
    this.next = 0;
    this.carry = 0;
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.BufferAttribute(this.pos, 3));
    this.points = new THREE.Points(geo, new THREE.PointsMaterial({
      color, size, map: DOT, transparent: true, opacity, depthWrite: false,
    }));
    this.points.frustumCulled = false;
  }

  update(dt, rate, origin) {
    this.carry += rate * dt;
    while (this.carry >= 1) {
      this.carry -= 1;
      const i = this.next;
      this.next = (this.next + 1) % this.n;
      const a = Math.random() * Math.PI * 2;
      this.pos.set([origin.x, origin.y, origin.z], i * 3);
      this.vel.set([Math.cos(a) * this.speed * (0.4 + Math.random()), this.up * (0.5 + Math.random()),
        Math.sin(a) * this.speed * (0.4 + Math.random())], i * 3);
      this.age[i] = 0;
    }
    const [bx, bz] = this.bounds;
    for (let i = 0; i < this.n; i++) {
      const j = i * 3;
      if (this.age[i] >= this.life) { this.pos[j + 1] = -100; continue; }
      this.age[i] += dt;
      this.vel[j + 1] -= 0.9 * dt;
      this.pos[j] += this.vel[j] * dt;
      this.pos[j + 1] += this.vel[j + 1] * dt;
      this.pos[j + 2] += this.vel[j + 2] * dt;
      if (this.pos[j + 1] < 0.08) {
        this.pos[j + 1] = 0.08;
        this.vel[j + 1] = 0;
        this.vel[j] *= 1 - 0.6 * dt;
        this.vel[j + 2] *= 1 - 0.6 * dt;
      }
      this.pos[j] = clamp(this.pos[j], -bx, bx);
      this.pos[j + 2] = clamp(this.pos[j + 2], -bz, bz);
    }
    this.points.geometry.attributes.position.needsUpdate = true;
  }
}

// Power flowing along a polyline: chevrons that travel at a speed set by the
// flow (kW), reverse with its sign, and grey out when the line is dead.
export class FlowLine {
  constructor(points, { width = 0.12, count = 10, y = 0 } = {}) {
    this.curve = new THREE.CurvePath();
    const pts = points.map(([x, z, yy]) => new THREE.Vector3(x, yy ?? y, z));
    for (let i = 1; i < pts.length; i++) this.curve.add(new THREE.LineCurve3(pts[i - 1], pts[i]));
    this.group = new THREE.Group();
    const lineGeo = new THREE.BufferGeometry().setFromPoints(pts);
    this.lineMat = new THREE.LineBasicMaterial({ color: COLORS.off });
    this.group.add(new THREE.Line(lineGeo, this.lineMat));
    const cone = new THREE.ConeGeometry(width, width * 2.6, 8).rotateZ(-Math.PI / 2);
    this.arrowMat = basic(0xffd25a);
    this.arrows = [];
    for (let i = 0; i < count; i++) {
      const m = new THREE.Mesh(cone, this.arrowMat);
      this.arrows.push(m);
      this.group.add(m);
    }
    this.phase = 0;
    this.kw = 0;
    this.live = false;
  }

  set(kw, live) {
    this.kw = kw ?? 0;
    this.live = !!live;
  }

  update(dt) {
    const mag = Math.abs(this.kw);
    const moving = this.live && mag > 0.2;
    this.lineMat.color.setHex(this.live ? 0xb08a2e : 0x3a3f45);
    this.arrowMat.color.setHex(moving ? 0xffd25a : 0x50565d);
    this.phase += (moving ? Math.sign(this.kw) : 0) * clamp(0.04 + mag / 900, 0.04, 0.6) * dt;
    const n = this.arrows.length;
    const ahead = new THREE.Vector3();
    this.arrows.forEach((m, i) => {
      const u = (((i / n + this.phase) % 1) + 1) % 1;
      m.visible = moving || i % 3 === 0;
      const p = this.curve.getPointAt(u);
      this.curve.getPointAt(Math.min(u + 0.001, 1), ahead);
      m.position.copy(p);
      const dir = ahead.sub(p);
      if (dir.lengthSq() > 0) m.rotation.y = Math.atan2(-dir.z, dir.x) + (this.kw < 0 ? Math.PI : 0);
    });
  }
}
