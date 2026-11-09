// viz3d spike: the cellar in three.js, driven by the bridge's WebSocket frames.
//
// Geometry is built in code (no binary assets), sized roughly from DESIGN.md:
// four 30 bbl unitanks, two bright tanks, four low CO2 sensors, two purge fans.
// URL options: ?fps=N caps the render rate, ?aa=1 turns on antialiasing.
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

const params = new URLSearchParams(location.search);
const FPS_CAP = Number(params.get('fps') || 0);
const STALE_MS = 3000;
const SMOOTH_S = 0.4; // values glide to each new frame instead of jumping

const viz = (window.__viz = {
  ready: false, error: null, frames: 0, msgs: 0, ages: [], stateAges: [],
  renderer: '', webgl: 0, last: null, scene: null, THREE,
});

// -- renderer --------------------------------------------------------------------------
const canvas = document.getElementById('scene');
let renderer;
try {
  renderer = new THREE.WebGLRenderer({ canvas, antialias: params.get('aa') === '1' });
} catch (err) {
  viz.error = String(err);
  document.getElementById('nogl').hidden = false;
  throw err;
}
renderer.setPixelRatio(1); // software GL: never render above CSS pixels
renderer.setSize(innerWidth, innerHeight, false);
{
  const gl = renderer.getContext();
  const dbg = gl.getExtension('WEBGL_debug_renderer_info');
  viz.renderer = String(gl.getParameter(dbg ? dbg.UNMASKED_RENDERER_WEBGL : gl.RENDERER));
  viz.webgl = typeof WebGL2RenderingContext !== 'undefined' && gl instanceof WebGL2RenderingContext ? 2 : 1;
}

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x1b2026);
viz.scene = scene;
const camera = new THREE.PerspectiveCamera(45, innerWidth / innerHeight, 0.1, 200);
camera.position.set(3, 11.5, 17.5);
const controls = new OrbitControls(camera, canvas);
controls.target.set(0.5, 1.6, -1.8);
controls.update();
scene.add(new THREE.HemisphereLight(0xdfe8f0, 0x30353a, 1.7));
const sun = new THREE.DirectionalLight(0xffffff, 1.3);
sun.position.set(6, 14, 9);
scene.add(sun);

// -- helpers ---------------------------------------------------------------------------
const lambert = (color, extra = {}) => new THREE.MeshLambertMaterial({ color, ...extra });
const basic = (color, extra = {}) => new THREE.MeshBasicMaterial({ color, ...extra });
const clamp = (x, lo, hi) => Math.min(hi, Math.max(lo, x));
const fmt = (x, d = 0) => {
  const r = Number(Number(x).toFixed(d));
  return (r === 0 ? 0 : r).toLocaleString('en-US', { maximumFractionDigits: d, minimumFractionDigits: d });
};

function ramp(stops, x) {
  if (x <= stops[0][0]) return new THREE.Color(stops[0][1]);
  for (let i = 1; i < stops.length; i++) {
    const [x1, c1] = stops[i];
    const [x0, c0] = stops[i - 1];
    if (x <= x1) return new THREE.Color(c0).lerp(new THREE.Color(c1), (x - x0) / (x1 - x0));
  }
  return new THREE.Color(stops[stops.length - 1][1]);
}
const TEMP_STOPS = [[30, 0x2f6fe0], [40, 0x37a6d9], [55, 0xd9b54a], [66, 0xe39a2d], [75, 0xe0602a], [85, 0xd22f2f]];
const GAS_STOPS = [[1000, 0x8fd0ff], [5000, 0xffe04a], [15000, 0xff8c1a], [30000, 0xff2b2b]];
const pressureColor = (psig) =>
  psig < 0.5 ? 0x7d8790 : psig < 12.5 ? 0x4cc38a : psig < 14.5 ? 0xffb020 : 0xff3b30; // MAWP and relief 15 psig
const tierColor = (ppm) => (ppm >= 30000 ? 0xff2b2b : ppm >= 15000 ? 0xff8c1a : ppm >= 5000 ? 0xffe04a : 0x4cc38a);

function makeLabel(width = 256, height = 96, scale = 3.4) {
  const c = document.createElement('canvas');
  c.width = width;
  c.height = height;
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  const sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, depthTest: false, transparent: true }));
  sprite.scale.set(scale, (scale * height) / width, 1);
  sprite.renderOrder = 10;
  const draw = (lines) => {
    const ctx = c.getContext('2d');
    ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = 'rgba(16,19,23,0.78)';
    ctx.fillRect(0, 0, width, height);
    const h = height / lines.length;
    lines.forEach(([text, color], i) => {
      ctx.fillStyle = color || '#e6e9ed';
      ctx.font = `${i === 0 ? 'bold ' : ''}${Math.floor(h * 0.62)}px system-ui, sans-serif`;
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

// Heavier-than-air CO2: particles leave the source, fall, and spread on the floor.
class Plume {
  constructor(n, { color, size, opacity, life, speed, up }) {
    this.n = n; this.life = life; this.speed = speed; this.up = up;
    this.pos = new Float32Array(n * 3).fill(-100);
    this.vel = new Float32Array(n * 3);
    this.age = new Float32Array(n).fill(life);
    this.next = 0; this.carry = 0;
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
    for (let i = 0; i < this.n; i++) {
      const j = i * 3;
      if (this.age[i] >= this.life) { this.pos[j + 1] = -100; continue; }
      this.age[i] += dt;
      this.vel[j + 1] -= 0.9 * dt; // CO2 is denser than air: it sinks
      this.pos[j] += this.vel[j] * dt;
      this.pos[j + 1] += this.vel[j + 1] * dt;
      this.pos[j + 2] += this.vel[j + 2] * dt;
      if (this.pos[j + 1] < 0.08) { // pool and spread along the floor
        this.pos[j + 1] = 0.08;
        this.vel[j + 1] = 0;
        this.vel[j] *= 1 - 0.6 * dt;
        this.vel[j + 2] *= 1 - 0.6 * dt;
      }
      this.pos[j] = clamp(this.pos[j], -10.8, 10.8);
      this.pos[j + 2] = clamp(this.pos[j + 2], -5.8, 5.8);
    }
    this.points.geometry.attributes.position.needsUpdate = true;
  }
}

// -- the room --------------------------------------------------------------------------
const ROOM = { w: 22, d: 12, h: 5.5 };
{
  const floor = new THREE.Mesh(new THREE.PlaneGeometry(ROOM.w, ROOM.d), lambert(0x5b6168));
  floor.rotation.x = -Math.PI / 2;
  scene.add(floor);
  const grid = new THREE.GridHelper(ROOM.w, 22, 0x454c55, 0x454c55);
  grid.scale.z = ROOM.d / ROOM.w;
  grid.position.y = 0.004;
  scene.add(grid);
  const wallMat = lambert(0x3a4149);
  const back = new THREE.Mesh(new THREE.BoxGeometry(ROOM.w, ROOM.h, 0.2), wallMat);
  back.position.set(0, ROOM.h / 2, -ROOM.d / 2 - 0.1);
  const left = new THREE.Mesh(new THREE.BoxGeometry(0.2, ROOM.h, ROOM.d), wallMat);
  left.position.set(-ROOM.w / 2 - 0.1, ROOM.h / 2, 0);
  const door = new THREE.Mesh(new THREE.PlaneGeometry(1.6, 2.4), lambert(0x22272d));
  door.position.set(9.4, 1.2, -ROOM.d / 2 + 0.01);
  scene.add(back, left, door);
}

// -- vessels ---------------------------------------------------------------------------
const vessels = new Map();
function makeVessel(id, x, z, { R, H, coneH, legH, spund }) {
  const g = new THREE.Group();
  g.position.set(x, 0, z);
  const base = legH + coneH;
  const steel = lambert(0xaab4be);
  for (let i = 0; i < 3; i++) {
    const leg = new THREE.Mesh(new THREE.CylinderGeometry(0.06, 0.06, base, 8), steel);
    const a = (i / 3) * Math.PI * 2;
    leg.position.set(Math.cos(a) * R * 0.85, base / 2, Math.sin(a) * R * 0.85);
    g.add(leg);
  }
  const shellMat = lambert(0xc9d1d9, { transparent: true, opacity: 0.2, side: THREE.DoubleSide, depthWrite: false });
  const cone = new THREE.Mesh(new THREE.ConeGeometry(R, coneH, 32, 1, true), shellMat);
  cone.rotation.x = Math.PI;
  cone.position.y = legH + coneH / 2;
  const body = new THREE.Mesh(new THREE.CylinderGeometry(R, R, H, 40, 1, true), shellMat);
  body.position.y = base + H / 2;
  const dome = new THREE.Mesh(new THREE.SphereGeometry(R, 32, 8, 0, Math.PI * 2, 0, Math.PI / 2), steel);
  dome.scale.y = 0.3;
  dome.position.y = base + H;
  g.add(cone, body, dome);
  for (const y of [base, base + H]) {
    const rim = new THREE.Mesh(new THREE.TorusGeometry(R + 0.01, 0.03, 6, 48), lambert(0x8c969f));
    rim.rotation.x = Math.PI / 2;
    rim.position.y = y;
    g.add(rim);
  }
  const bandMat = basic(0x7d8790);
  const band = new THREE.Mesh(new THREE.TorusGeometry(R + 0.04, 0.07, 8, 48), bandMat);
  band.rotation.x = Math.PI / 2;
  band.position.y = base + H - 0.2;
  g.add(band);
  const liquidMat = lambert(0xe39a2d, { emissive: 0x2a1a05 });
  const liquidBody = new THREE.Mesh(new THREE.CylinderGeometry(R * 0.95, R * 0.95, 1, 40).translate(0, 0.5, 0), liquidMat);
  liquidBody.position.y = base;
  const liquidCone = new THREE.Mesh(new THREE.ConeGeometry(R * 0.95, coneH * 0.95, 32), liquidMat);
  liquidCone.rotation.x = Math.PI;
  liquidCone.position.y = legH + coneH / 2;
  g.add(liquidBody, liquidCone);
  const top = base + H + R * 0.3;
  if (spund) {
    const pipe = new THREE.Mesh(new THREE.CylinderGeometry(0.06, 0.06, 0.5, 8), steel);
    pipe.position.set(0.35, top + 0.1, 0);
    g.add(pipe);
  }
  const label = makeLabel();
  label.sprite.position.set(0, top + 0.9, 0);
  g.add(label.sprite);
  scene.add(g);
  const vent = new Plume(160, { color: 0xe8eef4, size: 0.28, opacity: 0.55, life: 4, speed: 0.25, up: 0.9 });
  const leak = new Plume(500, { color: 0xd6f0ff, size: 0.55, opacity: 0.32, life: 9, speed: 1.6, up: 0.2 });
  scene.add(vent.points, leak.points);
  const v = {
    id, g, H, base, liquidBody, liquidCone, liquidMat, bandMat, label, vent, leak,
    ventAt: new THREE.Vector3(x + 0.35, top + 0.35, z), leakAt: new THREE.Vector3(x, legH + 0.2, z + R),
    cur: { level: 0, temp: 60, psig: 0, vent: 0, leak: 0 }, tgt: null,
  };
  vessels.set(id, v);
  return v;
}
const FV = { R: 1.0, H: 3.0, coneH: 0.9, legH: 0.6, spund: true };
const BBT = { R: 0.8, H: 2.4, coneH: 0.4, legH: 0.5, spund: false };
['FV101', 'FV102', 'FV103', 'FV104'].forEach((id, i) => makeVessel(id, -8 + i * 3.8, -2.6, FV));
makeVessel('BBT201', 6.9, -3.6, BBT);
makeVessel('BBT202', 9.2, -3.6, BBT);

// -- CO2: the gas layer, the sensors, the purge fans, the beacon -----------------------
const gasMat = basic(0x8fd0ff, { transparent: true, opacity: 0, depthWrite: false });
const gas = new THREE.Mesh(new THREE.BoxGeometry(ROOM.w - 0.3, 1, ROOM.d - 0.3).translate(0, 0.5, 0), gasMat);
gas.position.y = 0.01;
gas.renderOrder = 5;
scene.add(gas);

const sensors = [[-6, -5.85], [1.5, -5.85], [-10.85, 1.0], [5.5, -5.85]].map(([x, z]) => {
  const mat = basic(0x4cc38a);
  const box = new THREE.Mesh(new THREE.BoxGeometry(0.3, 0.3, 0.3), mat);
  box.position.set(x, 0.45, z);
  const label = makeLabel(192, 64, 2.2);
  label.sprite.position.set(x + (x < -10 ? 0.9 : 0), 1.15, z + (x < -10 ? 0 : 0.3));
  scene.add(box, label.sprite);
  return { mat, label };
});

const fans = [-3.8, 2.6].map((z) => {
  const g = new THREE.Group();
  g.position.set(-ROOM.w / 2 + 0.05, 0.95, z);
  const ring = new THREE.Mesh(new THREE.TorusGeometry(0.6, 0.07, 8, 32), lambert(0x6b737c));
  ring.rotation.y = Math.PI / 2;
  const rotor = new THREE.Group();
  for (let i = 0; i < 4; i++) {
    const blade = new THREE.Mesh(new THREE.BoxGeometry(0.04, 1.0, 0.2), lambert(0xc0c7ce));
    blade.rotation.x = (i * Math.PI) / 4;
    rotor.add(blade);
  }
  rotor.position.x = 0.08;
  const lampMat = basic(0x555b62);
  const lamp = new THREE.Mesh(new THREE.SphereGeometry(0.1, 12, 8), lampMat);
  lamp.position.set(0.05, 0.85, 0);
  g.add(ring, rotor, lamp);
  scene.add(g);
  return { rotor, lampMat, omega: 0, on: false };
});

const beaconMat = basic(0x555b62);
const beacon = new THREE.Mesh(new THREE.SphereGeometry(0.2, 16, 10), beaconMat);
beacon.position.set(9.4, 2.75, -5.85);
scene.add(beacon);

// -- the HUD: truth beside what the HMI shows ------------------------------------------
const $ = (id) => document.getElementById(id);
const avg = (xs) => xs.reduce((a, b) => a + b, 0) / xs.length;
const tierText = (t) => (t[2] ? 'Tier 3 (≥30,000)' : t[1] ? 'Tier 2 (≥15,000)' : t[0] ? 'Tier 1 (≥5,000)' : 'Normal');
const onOff = (xs) => xs.map((x) => (x ? 'on' : 'off')).join(' / ');

function renderCompare(f) {
  const t = f.truth.air;
  const o = f.operator;
  const fv = f.truth.vessels.find((v) => v.id === 'FV102');
  const rows = [
    ['CO₂ at sensors, avg ppm', fmt(avg(t.co2_sensors_ppm)), fmt(avg(o.co2_sensors_ppm)),
      Math.abs(avg(t.co2_sensors_ppm) - avg(o.co2_sensors_ppm)) > 0.1 * avg(t.co2_sensors_ppm)],
    ['CO₂ room (well mixed), ppm', fmt(t.co2_mixed_ppm), 'not measured', false],
    ['CO₂ alarm', tierText(t.tiers), tierText(o.tiers), tierText(t.tiers) !== tierText(o.tiers)],
    ['Purge fans', onOff(t.purge_fans_running), onOff(o.purge_fans_running),
      onOff(t.purge_fans_running) !== onOff(o.purge_fans_running)],
    ['Carbonation permit (relay)', t.relay_ok ? 'closed' : 'open (inhibit)', o.relay_ok ? 'closed' : 'open (inhibit)',
      t.relay_ok !== o.relay_ok],
    ['FV-102 temperature, °F', fmt(fv.temp_f, 1), fmt(o.fv_temp_f.FV102, 1), Math.abs(fv.temp_f - o.fv_temp_f.FV102) > 1],
    ['FV-102 fill (no transmitter)', `${fmt(fv.level_pct)} %`, 'not measured', false],
  ];
  $('compare').innerHTML = rows.map(([name, a, b, diff]) =>
    `<tr><th>${name}</th><td>${a}</td><td class="${diff ? 'diff' : ''}">${b}</td></tr>`).join('');
  $('events').textContent = (o.spoofed ? 'HMI values are frozen (replay). ' : '') + f.notes.join(' · ');
}

function applyFrame(f) {
  viz.last = f;
  $('simtime').textContent = `Sim ${f.sim_time.replace('T', ' ').slice(0, 19)} · ×${f.speed} · ${f.source}`;
  for (const v of f.truth.vessels) {
    const m = vessels.get(v.id);
    if (!m) continue;
    m.tgt = { level: v.level_pct, temp: v.temp_f, psig: v.pressure_psig, vent: v.vent_gps, leak: v.leak_gps };
    const name = v.id.replace(/(\D+)(\d+)/, '$1-$2');
    const lines = [[`${name}  ${fmt(v.temp_f, 1)} °F`], [`${fmt(v.pressure_psig, 1)} psig · ${fmt(v.level_pct)} % full`]];
    if (v.leak_gps > 0) lines.push([`CO₂ LEAK ${fmt(v.leak_gps)} g/s`, '#ff6b5e']);
    else if (v.vent_gps > 0.05) lines.push([`spunding vent ${fmt(v.vent_gps, 2)} g/s`, '#9fd3ff']);
    else lines.push(['']);
    m.label.draw(lines);
  }
  f.truth.air.co2_sensors_ppm.forEach((ppm, i) => {
    sensors[i].mat.color.setHex(tierColor(ppm));
    sensors[i].label.draw([[`CO₂-${i + 1}`], [`${fmt(ppm)} ppm`, `#${tierColor(ppm).toString(16).padStart(6, '0')}`]]);
  });
  f.truth.air.purge_fans_running.forEach((on, i) => { fans[i].on = on; });
  renderCompare(f);
}

// -- data ------------------------------------------------------------------------------
let lastMsgAt = 0;
function connect() {
  const ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`);
  ws.onmessage = (ev) => {
    const f = JSON.parse(ev.data);
    const now = Date.now();
    lastMsgAt = now;
    viz.msgs += 1;
    viz.ages.push(now - f.sent_wall_ms);
    viz.stateAges.push(now - f.sampled_wall_ms);
    if (viz.ages.length > 2000) { viz.ages.shift(); viz.stateAges.shift(); }
    applyFrame(f);
  };
  ws.onclose = () => setTimeout(connect, 1000);
}
connect();

// -- animation -------------------------------------------------------------------------
let lastRender = performance.now();
let fpsCount = 0;
let fpsAt = performance.now();
let fpsShown = 0;
function tick(now) {
  requestAnimationFrame(tick);
  if (FPS_CAP && now - lastRender < 1000 / FPS_CAP - 1) return;
  const dt = Math.min((now - lastRender) / 1000, 0.1);
  lastRender = now;
  const k = 1 - Math.exp(-dt / SMOOTH_S);
  for (const m of vessels.values()) {
    if (!m.tgt) continue;
    for (const key of Object.keys(m.cur)) m.cur[key] += (m.tgt[key] - m.cur[key]) * k;
    const frac = clamp(m.cur.level / 100, 0, 1);
    m.liquidBody.visible = m.liquidCone.visible = frac > 0.005;
    m.liquidBody.scale.y = Math.max(frac * m.H, 0.001);
    m.liquidMat.color.copy(ramp(TEMP_STOPS, m.cur.temp));
    m.bandMat.color.setHex(pressureColor(m.cur.psig));
    m.vent.update(dt, clamp(m.cur.vent * 40, 0, 60) * (m.tgt.vent > 0.01 ? 1 : 0), m.ventAt);
    m.leak.update(dt, clamp(m.cur.leak * 3, 0, 160) * (m.tgt.leak > 0 ? 1 : 0), m.leakAt);
  }
  const f = viz.last;
  if (f) {
    const low = avg(f.truth.air.co2_sensors_ppm);
    gas.scale.y = 0.2 + 2.8 * clamp((low - 1000) / 39000, 0, 1);
    gasMat.color.copy(ramp(GAS_STOPS, low));
    gasMat.opacity = 0.02 + 0.26 * clamp((low - 1500) / 30000, 0, 1);
    const tiers = f.truth.air.tiers;
    const blink = Math.floor(now / 350) % 2 === 0;
    beaconMat.color.setHex(tiers[1] ? (blink ? 0xff2b2b : 0x3a1010) : tiers[0] ? 0xffb020 : 0x555b62);
  }
  for (const fan of fans) {
    fan.omega += ((fan.on ? 14 : 0) - fan.omega) * (1 - Math.exp(-dt / 1.5));
    fan.rotor.rotation.x += fan.omega * dt;
    fan.lampMat.color.setHex(fan.on ? 0x4cc38a : 0x555b62);
  }
  $('stale').hidden = !lastMsgAt || Date.now() - lastMsgAt < STALE_MS;
  renderer.render(scene, camera);
  viz.frames += 1;
  viz.ready = true;
  fpsCount += 1;
  if (now - fpsAt >= 1000) {
    fpsShown = (fpsCount * 1000) / (now - fpsAt);
    fpsCount = 0;
    fpsAt = now;
    const ages = viz.ages.slice(-20).sort((a, b) => a - b);
    $('status').textContent = `${fpsShown.toFixed(0)} fps · ${viz.renderer} · WebGL ${viz.webgl} · ` +
      `${viz.msgs} frames received · transport age p50 ${ages.length ? ages[ages.length >> 1].toFixed(0) : '-'} ms`;
  }
}
requestAnimationFrame(tick);

addEventListener('resize', () => {
  camera.aspect = innerWidth / innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(innerWidth, innerHeight, false);
});
