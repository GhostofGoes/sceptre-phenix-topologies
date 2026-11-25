// cm-viz page: three scenes (cellar CO2, fermenters, plant power) driven by the
// bridge's WebSocket frames. Read only: the page sends nothing to the server.
//
// URL options: ?scene=cellar|fermenters|power, ?view=truth|operator,
// ?fps=N caps the render rate (default 15; 0 = uncapped), ?aa=1 antialiasing,
// ?dpr=N the pixel ratio (default 1: software GL pays for every pixel).
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { buildCellar } from './scenes/cellar.js';
import { fmt } from './scenes/common.js';
import { buildPower } from './scenes/power.js';

const params = new URLSearchParams(location.search);
const FPS_CAP = Number(params.get('fps') ?? 15);
// Grey out after this long without a message, or when the state is older
// than this; the same 5 s after which the server marks a snapshot stale. The
// server sends at least one message a second.
const STALE_MS = 5000;
const SCENES = ['cellar', 'fermenters', 'power'];
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);

// Test and measurement hooks (the Playwright smoke test reads them).
const viz = (window.__viz = {
  ready: false, error: null, frames: 0, msgs: 0, renderer: '', webgl: 0,
  scene: '', view: '', stale: true, last: null, applied: 0,
});

// -- renderer ----------------------------------------------------------------------------
const canvas = $('scene');
let renderer;
try {
  renderer = new THREE.WebGLRenderer({ canvas, antialias: params.get('aa') === '1' });
} catch (err) {
  viz.error = String(err);
  $('nogl').hidden = false;
  throw err;
}
renderer.setPixelRatio(Math.min(Number(params.get('dpr') ?? 1), 3));
renderer.setSize(innerWidth, innerHeight, false);
{
  const gl = renderer.getContext();
  const dbg = gl.getExtension('WEBGL_debug_renderer_info');
  viz.renderer = String(gl.getParameter(dbg ? dbg.UNMASKED_RENDERER_WEBGL : gl.RENDERER));
  viz.webgl = typeof WebGL2RenderingContext !== 'undefined' && gl instanceof WebGL2RenderingContext ? 2 : 1;
}

const camera = new THREE.PerspectiveCamera(45, innerWidth / innerHeight, 0.1, 300);
const controls = new OrbitControls(camera, canvas);
const worlds = { cellar: buildCellar(), power: buildPower() };
const worldOf = (s) => (s === 'power' ? worlds.power : worlds.cellar);

let current = SCENES.includes(params.get('scene')) ? params.get('scene') : 'cellar';
let view = params.get('view') === 'operator' ? 'operator' : 'truth';

function aim(s) {
  const preset = worldOf(s).cameras[s];
  camera.position.set(...preset.pos);
  controls.target.set(...preset.target);
  controls.update();
}

function setScene(s) {
  current = s;
  viz.scene = s;
  worlds.cellar.setDetail(s);
  aim(s);
  document.querySelectorAll('[data-scene]').forEach((b) => b.classList.toggle('on', b.dataset.scene === s));
  const url = new URL(location.href);
  url.searchParams.set('scene', s);
  history.replaceState(null, '', url);
  if (viz.last) applyFrame(viz.last);
}

function setView(v) {
  view = v;
  viz.view = v;
  document.querySelectorAll('[data-view]').forEach((b) => b.classList.toggle('on', b.dataset.view === v));
  if (viz.last) applyFrame(viz.last);
}

document.querySelectorAll('[data-scene]').forEach((b) => b.addEventListener('click', () => setScene(b.dataset.scene)));
document.querySelectorAll('[data-view]').forEach((b) => b.addEventListener('click', () => setView(b.dataset.view)));
addEventListener('keydown', (e) => {
  const i = ['1', '2', '3'].indexOf(e.key);
  if (i >= 0) setScene(SCENES[i]);
  if (e.key === 'o') setView(view === 'truth' ? 'operator' : 'truth');
});

// -- side panel ----------------------------------------------------------------------------
const avg = (xs) => (xs && xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null);
const tierText = (t) => (!t || !t.length ? '–' : t[2] ? 'Tier 3 (≥30,000)' : t[1] ? 'Tier 2 (≥15,000)' : t[0] ? 'Tier 1 (≥5,000)' : 'Normal');
const onOff = (xs) => (xs && xs.length ? xs.map((x) => (x ? 'on' : 'off')).join(' / ') : '–');
const relayText = (x) => (x === false ? 'OPEN (carbonation inhibited)' : x ? 'closed (healthy)' : '–');
const NOT_MEASURED = 'not measured';

function rowsCellar(f, op) {
  const a = f.cellar.air;
  const truthAvg = avg(a.sensors_ppm);
  const opAvg = op ? avg(op.co2_sensors_ppm) : null;
  const inhibit = a.inhibit_effective === null ? 'not asked (relay closed)' : a.inhibit_effective ? 'effective: stones shut' : 'DEFEATED: a stone is open';
  return [
    ['CO₂ at the sensors, avg ppm', fmt(truthAvg), op ? fmt(opAvg) : null, op && Math.abs(opAvg - truthAvg) > 0.1 * truthAvg],
    ['CO₂ in the room (well mixed)', fmt(a.co2_mixed_ppm), NOT_MEASURED, false],
    ['CO₂ alarm', tierText(a.tiers), op ? tierText(op.tiers) : null, op && tierText(op.tiers) !== tierText(a.tiers)],
    ['Purge fans running', onOff(a.fans.map((x) => x.running)), op ? onOff(op.fans_running) : null,
      op && onOff(op.fans_running) !== onOff(a.fans.map((x) => x.running))],
    ['Purge fans commanded', onOff(a.fans.map((x) => x.commanded)), op ? onOff(op.fans_commanded) : null,
      op && onOff(op.fans_commanded) !== onOff(a.fans.map((x) => x.commanded))],
    ['Gas-detection relay', relayText(a.relay_ok), op ? relayText(op.relay_ok) : null, op && op.relay_ok !== a.relay_ok],
    ['Carbonation inhibit', inhibit, '', false],
    ['CO₂ into the room', `${fmt(a.co2_into_room_gps, 1)} g/s`, NOT_MEASURED, false],
  ];
}

function rowsFermenters(f, op) {
  const rows = [];
  for (const v of f.cellar.vessels) {
    const opT = op ? op.vessel_temp_f[v.id] : null;
    const opP = op ? op.vessel_pressure_psig[v.id] : null;
    rows.push([`${v.label} temperature, °F`, fmt(v.temp_f, 1), op ? fmt(opT, 1) : null, op && Math.abs(opT - v.temp_f) > 1]);
    rows.push([`${v.label} head, psig`, fmt(v.pressure_psig, 1) + (v.psv_lifted ? ' · PSV LIFTED' : ''), op ? fmt(opP, 1) : null,
      op && Math.abs(opP - v.pressure_psig) > 0.5]);
    rows.push([`${v.label} fill`, `${fmt(v.level_pct)} %`, v.level_measured ? (op ? `${fmt(v.level_pct)} % (LT01)` : null) : NOT_MEASURED, false]);
  }
  return rows;
}

function rowsPower(f) {
  const p = f.power;
  return [
    ['Utility', p.utility_ok ? 'available' : 'LOST', '', false],
    ['Main breaker', p.utility_breaker_closed ? 'closed' : 'OPEN', '', false],
    ['ATS', p.ats_on_emergency ? 'EMERGENCY (genset)' : 'normal', '', false],
    ['Genset', p.gen_running ? `running ${fmt(p.gen_kw)} kW` : 'stopped', '', false],
    ['PV', `${fmt(p.pv_kw)} kW (limit ${fmt(p.pv_limit_pct)} %)`, '', false],
    ['BESS', `${fmt(p.bess_soc_pct)} % SoC, ${fmt(p.bess_kw)} kW`, '', false],
    ['Utility import', `${fmt(p.grid_kw)} kW`, '', false],
    ['Main bus load', `${fmt(p.load_kw)} kW`, '', false],
    ['Demand / month peak', `${fmt(p.demand_kw)} / ${fmt(p.peak_kw)} kW`, '', false],
    ['Tariff', `${p.onpeak ? 'ON-PEAK' : 'off-peak'} $${fmt(p.rate_usd_kwh, 3)}/kWh`, '', false],
  ];
}

function renderSide(f) {
  const op = f.operator;
  const isPower = current === 'power';
  const rows = isPower ? rowsPower(f) : current === 'fermenters' ? rowsFermenters(f, op) : rowsCellar(f, op);
  $('side-title').textContent = isPower ? 'Plant power (truth)' : 'Truth and the operator\'s view';
  $('op-head').textContent = isPower ? '' : op ? `HMI shows${op.frozen ? ' (frozen)' : ''}` : 'Operator view';
  $('compare').innerHTML = rows.map(([name, a, b, diff]) => {
    const other = b === null ? '<td class="muted">not connected</td>' : `<td class="${diff ? 'diff' : ''}">${esc(b)}</td>`;
    return `<tr><th>${esc(name)}</th><td>${esc(a)}</td>${other}</tr>`;
  }).join('');
  const scene = current;
  const items = f.findings.filter((x) => x.scene === scene || (scene !== 'power' && x.scene !== 'power'));
  $('findings').innerHTML = items.map((x) => `<li class="${esc(x.level)}">${esc(x.text)}</li>`).join('');
  $('notes').textContent = [op && op.frozen ? 'HMI values are frozen (replay).' : '', ...f.notes].filter(Boolean).join(' · ');
}

// -- frames -----------------------------------------------------------------------------------
function applyFrame(f) {
  viz.last = f;
  viz.applied += 1;
  worlds.cellar.apply(f, view);
  worlds.power.apply(f);
  $('simtime').textContent = f.sim_time ? f.sim_time.replace('T', ' ').slice(0, 19) : '–';
  $('kinetic').textContent = f.cellar.kinetic_hours === null ? '–' : `${fmt(f.cellar.kinetic_hours, 1)} h (48× compressed)`;
  $('source').textContent = `${f.source}${f.variant ? ` · ${f.variant}` : ''}${f.speed !== 1 ? ` · ×${f.speed}` : ''}`;
  const banner = $('banner');
  if (view === 'operator' && current !== 'power') {
    banner.hidden = false;
    banner.textContent = f.operator
      ? `OPERATOR VIEW: what the HMI shows${f.operator.frozen ? ' (frozen)' : ''}`
      : 'OPERATOR VIEW not connected: showing the truth; open FUXA beside this window';
  } else {
    banner.hidden = true;
  }
  renderSide(f);
}

// Data age uses no browser wall clock: the frame's age when the server sent
// it (both stamps come from the server host's clock) plus the time since it
// arrived here, on this browser's monotonic clock. Clock skew between the
// white cell's machine and sim-ctl does not matter.
let lastMsgAt = 0;
let ageAtSend = 0;
const stateAgeMs = (now) => ageAtSend + (now - lastMsgAt);
function connect() {
  const ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`);
  ws.onmessage = (ev) => {
    lastMsgAt = performance.now();
    viz.msgs += 1;
    const f = JSON.parse(ev.data);
    ageAtSend = Math.max(f.sent_wall_ms - f.sampled_wall_ms, 0);
    applyFrame(f);
  };
  ws.onclose = () => setTimeout(connect, 1000);
}

function staleness(now) {
  const f = viz.last;
  const sinceMsg = lastMsgAt ? now - lastMsgAt : Infinity;
  const stateAge = f ? stateAgeMs(now) : Infinity;
  const stale = !f || f.stale || sinceMsg > STALE_MS || stateAge > STALE_MS;
  viz.stale = stale;
  document.body.classList.toggle('stale', stale);
  const pill = $('age');
  if (!f) {
    pill.textContent = 'no frame';
    pill.className = 'pill dead';
  } else {
    pill.textContent = `${fmt(stateAge / 1000, 1)} s old`;
    pill.className = `pill ${stale ? 'dead' : stateAge > 2500 ? 'late' : 'fresh'}`;
  }
  const banner = $('stale');
  banner.hidden = !stale || !f;
  if (f && stale) {
    banner.textContent = f.stale
      ? 'NO DATA: the simulation snapshot has stopped advancing'
      : sinceMsg > STALE_MS
        ? `NO DATA: no message from cm-viz for ${fmt(sinceMsg / 1000)} s`
        : `NO DATA: the state is ${fmt(stateAge / 1000)} s old`;
  }
}

// -- animation ------------------------------------------------------------------------------
let lastRender = performance.now();
let fpsCount = 0;
let fpsAt = performance.now();
function tick(now) {
  requestAnimationFrame(tick);
  if (FPS_CAP > 0 && now - lastRender < 1000 / FPS_CAP - 1) return;
  const dt = Math.min((now - lastRender) / 1000, 0.1);
  lastRender = now;
  const world = worldOf(current);
  world.tick(dt, now);
  staleness(performance.now());
  renderer.render(world.scene, camera);
  viz.frames += 1;
  viz.ready = true;
  fpsCount += 1;
  if (now - fpsAt >= 1000) {
    const fps = (fpsCount * 1000) / (now - fpsAt);
    fpsCount = 0;
    fpsAt = now;
    $('status').textContent = `${fps.toFixed(0)} fps (cap ${FPS_CAP || 'none'}) · ${viz.renderer} · WebGL ${viz.webgl} · ${viz.msgs} frames received`;
  }
}

addEventListener('resize', () => {
  camera.aspect = innerWidth / innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(innerWidth, innerHeight, false);
});

setScene(current);
setView(view);
connect();
requestAnimationFrame(tick);
