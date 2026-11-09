// The cellar room (DESIGN.md §6.3, §6.7): four 30 bbl unitanks, two bright
// tanks, the four low CO2 sensors, two purge fans, beacons and a horn, the
// bas-vent gas-detection relay and the rio-cellar contact, and the CO2 layer.
// One world serves two scenes: "cellar" (the life-safety view) and
// "fermenters" (a closer look at the vessels with detailed labels).
//
// The gas layer is a picture of a number: the room is one well-mixed zone with
// a constant stratification factor (DESIGN.md §8.3), not a dispersion model.
import * as THREE from 'three';
import {
  basic, clamp, COLORS, fmt, GAS_STOPS, hex, lambert, makeLabel, Plume, pressureColor, ramp,
  TEMP_STOPS, tierColor,
} from './common.js';

const ROOM = { w: 22, d: 12, h: 5.5 };
const BOUNDS = [ROOM.w / 2 - 0.2, ROOM.d / 2 - 0.2];
const SMOOTH_S = 0.4; // values glide toward each frame; the labels show the frame's own numbers
const FV = { R: 1.0, H: 3.0, coneH: 0.9, legH: 0.6 };
const BBT = { R: 0.8, H: 2.4, coneH: 0.4, legH: 0.5 };
// Layer height (m) for a sensed ppm: 0.2 m at 1,000 ppm to 3 m at 40,000.
const layerHeight = (ppm) => 0.2 + 2.8 * clamp((ppm - 1000) / 39000, 0, 1);

export function buildCellar() {
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x1b2026);
  scene.add(new THREE.HemisphereLight(0xdfe8f0, 0x30353a, 1.7));
  const sun = new THREE.DirectionalLight(0xffffff, 1.3);
  sun.position.set(6, 14, 9);
  scene.add(sun);

  // -- the room ------------------------------------------------------------------
  const floor = new THREE.Mesh(new THREE.PlaneGeometry(ROOM.w, ROOM.d), lambert(0x5b6168));
  floor.rotation.x = -Math.PI / 2;
  const grid = new THREE.GridHelper(ROOM.w, 22, 0x454c55, 0x454c55);
  grid.scale.z = ROOM.d / ROOM.w;
  grid.position.y = 0.004;
  const wallMat = lambert(0x3a4149);
  const back = new THREE.Mesh(new THREE.BoxGeometry(ROOM.w, ROOM.h, 0.2), wallMat);
  back.position.set(0, ROOM.h / 2, -ROOM.d / 2 - 0.1);
  const left = new THREE.Mesh(new THREE.BoxGeometry(0.2, ROOM.h, ROOM.d), wallMat);
  left.position.set(-ROOM.w / 2 - 0.1, ROOM.h / 2, 0);
  const door = new THREE.Mesh(new THREE.PlaneGeometry(1.6, 2.4), lambert(0x22272d));
  door.position.set(9.4, 1.2, -ROOM.d / 2 + 0.01);
  scene.add(floor, grid, back, left, door);

  // Glycol supply header along the back wall; tinted by its temperature.
  const glycolMat = lambert(COLORS.glycol);
  const header = new THREE.Mesh(new THREE.CylinderGeometry(0.07, 0.07, 19, 10).rotateZ(Math.PI / 2), glycolMat);
  header.position.set(-0.5, 4.6, -5.6);
  scene.add(header);

  // -- vessels -------------------------------------------------------------------
  const vessels = new Map();
  const steel = lambert(COLORS.steel);
  function makeVessel(id, x, z, dims, kind) {
    const { R, H, coneH, legH } = dims;
    const g = new THREE.Group();
    g.position.set(x, 0, z);
    const base = legH + coneH;
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
    // Pressure band near the top: green / amber / red against the 15 psig MAWP.
    const bandMat = basic(COLORS.off);
    const band = new THREE.Mesh(new THREE.TorusGeometry(R + 0.04, 0.07, 8, 48), bandMat);
    band.rotation.x = Math.PI / 2;
    band.position.y = base + H - 0.2;
    g.add(band);
    // Glycol jacket zones: a ring per zone, blue while its solenoid is open.
    const jackets = (kind === 'fv' ? [base + H * 0.45, legH + coneH * 0.55] : [base + H * 0.4]).map((y, i) => {
      const mat = basic(COLORS.off);
      const r = i === 1 ? R * 0.62 : R + 0.03;
      const ring = new THREE.Mesh(new THREE.TorusGeometry(r, 0.09, 6, 40), mat);
      ring.rotation.x = Math.PI / 2;
      ring.position.y = y;
      g.add(ring);
      return mat;
    });
    // Liquid: a cylinder scaled to the fill, colored by temperature.
    const liquidMat = lambert(0xe39a2d, { emissive: 0x2a1a05, transparent: true, opacity: 1 });
    const liquidBody = new THREE.Mesh(new THREE.CylinderGeometry(R * 0.95, R * 0.95, 1, 40).translate(0, 0.5, 0), liquidMat);
    liquidBody.position.y = base;
    const liquidCone = new THREE.Mesh(new THREE.ConeGeometry(R * 0.95, coneH * 0.95, 32), liquidMat);
    liquidCone.rotation.x = Math.PI;
    liquidCone.position.y = legH + coneH / 2;
    // The operator's view of an FV's fill is unknown (no transmitter): a wire cage.
    const unknown = new THREE.Mesh(new THREE.CylinderGeometry(R * 0.95, R * 0.95, H, 16, 6, true),
      basic(0x9aa4ae, { wireframe: true, transparent: true, opacity: 0.35 }));
    unknown.position.y = base + H / 2;
    unknown.visible = false;
    g.add(liquidBody, liquidCone, unknown);
    // The PSV on the top plate: a small valve body with a lamp, red when lifted.
    const top = base + H + R * 0.3;
    const psvMat = basic(COLORS.off);
    const psv = new THREE.Mesh(new THREE.CylinderGeometry(0.09, 0.12, 0.35, 10), lambert(0x8c969f));
    psv.position.set(-0.35, top + 0.1, 0.1);
    const psvLamp = new THREE.Mesh(new THREE.SphereGeometry(0.1, 10, 8), psvMat);
    psvLamp.position.set(-0.35, top + 0.36, 0.1);
    g.add(psv, psvLamp);
    if (kind === 'fv') {
      const spund = new THREE.Mesh(new THREE.CylinderGeometry(0.06, 0.06, 0.5, 8), steel);
      spund.position.set(0.35, top + 0.1, 0);
      g.add(spund);
    }
    // The carbonation stone's inhibit lamp on a bright tank (red while the
    // relay holds carbonation off, amber when the stone runs).
    let carbMat = null;
    if (kind === 'bbt') {
      carbMat = basic(COLORS.off);
      const lamp = new THREE.Mesh(new THREE.BoxGeometry(0.22, 0.22, 0.05), carbMat);
      lamp.position.set(0, legH + 0.25, R + 0.05);
      g.add(lamp);
    }
    const label = makeLabel(340, 150, 3.2);
    label.sprite.position.set(0, top + 1.1, 0);
    g.add(label.sprite);
    scene.add(g);
    const plume = (opts) => {
      const p = new Plume(opts.n, { ...opts, bounds: BOUNDS });
      scene.add(p.points);
      return p;
    };
    const v = {
      id, kind, g, H, base, liquidBody, liquidCone, liquidMat, unknown, bandMat, jackets, psvMat, carbMat, label,
      spund: plume({ n: 160, color: 0xe8eef4, size: 0.26, opacity: 0.5, life: 4, speed: 0.25, up: 0.9 }),
      psvPlume: plume({ n: 420, color: 0xffffff, size: 0.42, opacity: 0.6, life: 4.5, speed: 0.9, up: 1.8 }),
      leak: plume({ n: 500, color: 0xd6f0ff, size: 0.55, opacity: 0.32, life: 9, speed: 1.6, up: 0.2 }),
      spundAt: new THREE.Vector3(x + 0.35, top + 0.35, z),
      psvAt: new THREE.Vector3(x - 0.35, top + 0.4, z + 0.1),
      leakAt: new THREE.Vector3(x, legH + 0.2, z + R),
      cur: { level: 0, temp: 60, psig: 0 }, tgt: null, data: null,
    };
    vessels.set(id, v);
  }
  ['FV101', 'FV102', 'FV103', 'FV104'].forEach((id, i) => makeVessel(id, -8 + i * 3.8, -2.6, FV, 'fv'));
  makeVessel('BBT201', 6.9, -3.6, BBT, 'bbt');
  makeVessel('BBT202', 9.2, -3.6, BBT, 'bbt');

  // -- CO2: the layer (truth), the HMI's layer (outline), sensors -----------------
  const gasMat = basic(0x8fd0ff, { transparent: true, opacity: 0, depthWrite: false });
  const gas = new THREE.Mesh(new THREE.BoxGeometry(ROOM.w - 0.3, 1, ROOM.d - 0.3).translate(0, 0.5, 0), gasMat);
  gas.position.y = 0.01;
  gas.renderOrder = 5;
  const hmiEdges = new THREE.LineSegments(
    new THREE.EdgesGeometry(new THREE.BoxGeometry(ROOM.w - 0.2, 1, ROOM.d - 0.2).translate(0, 0.5, 0)),
    new THREE.LineDashedMaterial({ color: 0xffffff, dashSize: 0.35, gapSize: 0.25 }),
  );
  hmiEdges.computeLineDistances();
  hmiEdges.visible = false;
  const hmiTag = makeLabel(360, 56, 3.4);
  hmiTag.sprite.visible = false;
  scene.add(gas, hmiEdges, hmiTag.sprite);

  const sensorSpots = [[-6, -5.85], [1.5, -5.85], [-10.85, 1.0], [5.5, -5.85]];
  const sensors = sensorSpots.map(([x, z]) => {
    const mat = basic(COLORS.ok);
    const box = new THREE.Mesh(new THREE.BoxGeometry(0.3, 0.3, 0.3), mat);
    box.position.set(x, 0.45, z); // 12-24 in above the floor (IFC 5307)
    const label = makeLabel(220, 80, 2.6);
    label.sprite.position.set(x + (x < -10 ? 1.3 : 0), 1.3, z + (x < -10 ? 1.2 : 0.3));
    scene.add(box, label.sprite);
    return { mat, label };
  });

  // -- ventilation and alarms -----------------------------------------------------
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
    const lampMat = basic(COLORS.off);
    const lamp = new THREE.Mesh(new THREE.SphereGeometry(0.1, 12, 8), lampMat);
    lamp.position.set(0.05, 0.85, 0);
    const label = makeLabel(220, 60, 1.9);
    label.sprite.position.set(0.9, 1.5, 0);
    g.add(ring, rotor, lamp, label.sprite);
    scene.add(g);
    return { rotor, lampMat, label, omega: 0, on: false };
  });
  // Strobes inside and over the door, and the horn (tier 3: evacuate).
  const beacons = [[9.4, 2.75, -5.85], [-10.85, 3.2, -1.0]].map(([x, y, z]) => {
    const mat = basic(COLORS.off);
    const m = new THREE.Mesh(new THREE.SphereGeometry(0.2, 16, 10), mat);
    m.position.set(x, y, z);
    scene.add(m);
    return mat;
  });
  const hornMat = lambert(0x6b737c);
  const horn = new THREE.Mesh(new THREE.ConeGeometry(0.22, 0.4, 16, 1, true), hornMat);
  horn.rotation.x = Math.PI / 2;
  horn.position.set(8.2, 2.8, -5.75);
  const hornWaves = [0, 1, 2].map((i) => {
    const m = new THREE.Mesh(new THREE.TorusGeometry(0.3 + i * 0.2, 0.025, 6, 24, Math.PI),
      basic(0xff5a4f, { transparent: true, opacity: 0 }));
    m.position.set(8.2, 2.8, -5.45 + i * 0.05);
    m.rotation.z = -Math.PI / 2;
    scene.add(m);
    return m;
  });
  scene.add(horn);
  // bas-vent's controller panel with the gas-detection relay, and rio-cellar.
  const panel = new THREE.Mesh(new THREE.BoxGeometry(1.0, 1.2, 0.2), lambert(0x707a84));
  panel.position.set(-8.6, 1.9, -5.85);
  const relayMat = basic(COLORS.off);
  const relayLamp = new THREE.Mesh(new THREE.BoxGeometry(0.3, 0.3, 0.05), relayMat);
  relayLamp.position.set(-8.6, 2.1, -5.73);
  const rio = new THREE.Mesh(new THREE.BoxGeometry(0.8, 0.9, 0.2), lambert(0x5a646e));
  rio.position.set(-7.2, 1.9, -5.85);
  const rioMat = basic(COLORS.off);
  const rioLamp = new THREE.Mesh(new THREE.BoxGeometry(0.24, 0.24, 0.05), rioMat);
  rioLamp.position.set(-7.2, 2.05, -5.73);
  const wire = new THREE.Mesh(new THREE.BoxGeometry(0.6, 0.04, 0.04), basic(0xd9b54a));
  wire.position.set(-7.9, 2.1, -5.74);
  const panelLabel = makeLabel(320, 96, 2.6);
  panelLabel.sprite.position.set(-7.9, 3.25, -5.6);
  scene.add(panel, relayLamp, rio, rioLamp, wire, panelLabel.sprite);

  // -- cameras -------------------------------------------------------------------
  const cameras = {
    cellar: { pos: [5, 13, 19.5], target: [2.8, 1.2, -1.2] },
    fermenters: { pos: [2.6, 7.0, 15.5], target: [2.6, 2.3, -3.0] },
  };

  // -- per frame -----------------------------------------------------------------
  let detail = 'cellar';
  let operatorMode = false;
  let frame = null;

  function vesselLines(d, op) {
    const temp = op ? op.vessel_temp_f[d.id] : d.temp_f;
    const psig = op ? op.vessel_pressure_psig[d.id] : d.pressure_psig;
    const fill = op && !d.level_measured ? 'fill ?' : `${fmt(d.level_pct)} % full`;
    const lines = [[`${d.label}  ${fmt(temp, 1)} °F`], [`${fmt(psig, 1)} psig · ${fill}`]];
    if (detail === 'fermenters' && !op) {
      const valves = d.glycol_valves.map((x) => (x ? 'open' : 'shut')).join('/');
      lines.push([`glycol ${valves}${d.kind === 'fv' ? ` · spund SP ${fmt(d.spund_sp_psig, 1)}` : ` · ${fmt(d.co2_vol, 2)} vol`}`, COLORS.muted]);
    }
    if (d.psv_lifted && !op) lines.push([`PSV LIFTED ${fmt(d.psv_gps, 1)} g/s`, '#ff6b5e']);
    else if (d.leak_gps > 0 && !op) lines.push([`CO₂ LEAK ${fmt(d.leak_gps)} g/s`, '#ff6b5e']);
    else if (d.spund_gps > 0.05 && !op) lines.push([`spunding ${fmt(d.spund_gps, 2)} g/s`, '#9fd3ff']);
    else if (d.kind === 'bbt' && frame && frame.cellar.air.relay_ok === false) lines.push(['carbonation inhibited', '#ffb020']);
    else lines.push(['']);
    return lines;
  }

  function apply(f, view) {
    frame = f;
    operatorMode = view === 'operator' && !!f.operator;
    const op = operatorMode ? f.operator : null;
    for (const d of f.cellar.vessels) {
      const m = vessels.get(d.id);
      if (!m) continue;
      m.data = d;
      const temp = op ? op.vessel_temp_f[d.id] ?? d.temp_f : d.temp_f;
      const psig = op ? op.vessel_pressure_psig[d.id] ?? d.pressure_psig : d.pressure_psig;
      m.tgt = { level: d.level_pct ?? 0, temp: temp ?? 60, psig: psig ?? 0 };
      m.label.draw(vesselLines(d, op));
      m.jackets.forEach((mat, i) => mat.color.setHex(d.glycol_valves[i] ? COLORS.glycol : COLORS.off));
      m.psvMat.color.setHex(d.psv_lifted && !op ? COLORS.bad : COLORS.off);
      if (m.carbMat) {
        const pct = d.carb_valve_pct ?? 0;
        const relayOpen = f.cellar.air.relay_ok === false;
        m.carbMat.color.setHex(relayOpen ? (pct > 0 ? 0xff00ff : COLORS.bad) : pct > 0 ? COLORS.warn : COLORS.off);
      }
      m.unknown.visible = !!op && !d.level_measured;
    }
    const air = f.cellar.air;
    const ppms = op ? op.co2_sensors_ppm : air.sensors_ppm;
    ppms.forEach((ppm, i) => {
      if (!sensors[i]) return;
      sensors[i].mat.color.setHex(tierColor(ppm));
      sensors[i].label.draw([[`CO₂-${i + 1}`], [`${fmt(ppm)} ppm`, hex(tierColor(ppm))]]);
    });
    const running = op ? op.fans_running : air.fans.map((x) => x.running);
    running.forEach((on, i) => {
      if (!fans[i]) return;
      fans[i].on = on;
      const cmd = air.fans[i]?.commanded;
      fans[i].label.draw([[`Purge fan ${i + 1}`], [on ? 'running' : cmd ? 'commanded, stopped' : 'off', on ? '#5cc98a' : '#ff6b5e']]);
    });
    const relay = op ? op.relay_ok : air.relay_ok;
    relayMat.color.setHex(relay === false ? COLORS.bad : relay ? COLORS.ok : COLORS.off);
    rioMat.color.setHex(air.relay_at_rio === false ? COLORS.bad : air.relay_at_rio ? COLORS.ok : COLORS.off);
    panelLabel.draw([
      ['bas-vent relay → rio-cellar'],
      [`${relay === false ? 'OPEN: carbonation inhibited' : 'closed: healthy'}`, relay === false ? '#ff6b5e' : '#5cc98a'],
    ]);
    glycolMat.color.copy(ramp(TEMP_STOPS, f.cellar.glycol_supply_f ?? 28));
  }

  const avg = (xs) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : 0);

  function tick(dt, now) {
    const k = 1 - Math.exp(-dt / SMOOTH_S);
    for (const m of vessels.values()) {
      if (!m.tgt) continue;
      for (const key of Object.keys(m.cur)) m.cur[key] += (m.tgt[key] - m.cur[key]) * k;
      const frac = clamp(m.cur.level / 100, 0, 1);
      const hidden = m.unknown.visible;
      m.liquidBody.visible = m.liquidCone.visible = frac > 0.005 && !hidden;
      m.liquidBody.scale.y = Math.max(frac * m.H, 0.001);
      m.liquidMat.color.copy(ramp(TEMP_STOPS, m.cur.temp));
      m.bandMat.color.setHex(pressureColor(m.cur.psig));
      const d = m.data;
      const truth = !operatorMode;
      m.spund.update(dt, truth && d ? clamp(d.spund_gps * 40, 0, 60) : 0, m.spundAt);
      m.psvPlume.update(dt, truth && d && d.psv_lifted ? clamp(40 + d.psv_gps * 6, 40, 200) : 0, m.psvAt);
      m.leak.update(dt, truth && d ? clamp(d.leak_gps * 3, 0, 160) : 0, m.leakAt);
    }
    if (frame) {
      const air = frame.cellar.air;
      const op = operatorMode ? frame.operator : null;
      const shown = avg(op ? op.co2_sensors_ppm : air.sensors_ppm);
      gas.scale.y = layerHeight(shown);
      gasMat.color.copy(ramp(GAS_STOPS, shown));
      gasMat.opacity = 0.03 + 0.24 * clamp((shown - 1500) / 30000, 0, 1);
      // The HMI's layer as a dashed outline, when it disagrees with the truth.
      const hmi = frame.operator ? avg(frame.operator.co2_sensors_ppm) : null;
      const truthPpm = avg(air.sensors_ppm);
      const differs = !operatorMode && hmi !== null && Math.abs(hmi - truthPpm) > 0.1 * truthPpm;
      hmiEdges.visible = hmiTag.sprite.visible = differs;
      if (differs) {
        const h = layerHeight(hmi);
        hmiEdges.scale.y = h;
        hmiTag.sprite.position.set(-6.5, h + 0.35, 5.2);
        hmiTag.draw([[`HMI shows ${fmt(hmi)} ppm`, '#ffffff']], 'rgba(120,20,20,0.85)');
      }
      const tiers = op ? op.tiers : air.tiers;
      const blink = Math.floor(now / 350) % 2 === 0;
      const beacon = tiers[1] ? (blink ? 0xff2b2b : 0x3a1010) : tiers[0] ? 0xffb020 : COLORS.off;
      beacons.forEach((mat) => mat.color.setHex(beacon));
      const sounding = !!tiers[2];
      hornMat.color.setHex(sounding ? 0xff5a4f : 0x6b737c);
      hornWaves.forEach((w, i) => { w.material.opacity = sounding ? 0.25 + 0.6 * ((Math.floor(now / 200) + i) % 3 === 0) : 0; });
    }
    for (const fan of fans) {
      fan.omega += ((fan.on ? 14 : 0) - fan.omega) * (1 - Math.exp(-dt / 1.5));
      fan.rotor.rotation.x += fan.omega * dt;
      fan.lampMat.color.setHex(fan.on ? COLORS.ok : COLORS.off);
    }
  }

  return {
    scene,
    cameras,
    setDetail(d) { detail = d; },
    apply,
    tick,
  };
}
