// Plant power (DESIGN.md §7): the utility service through T-1 and the main
// breaker (relay-main), the rooftop PV and its inverter, the BESS, the main
// switchboard (MSB) and its feeders, and the genset and ATS that feed the
// emergency panel EP-1. Chevrons on each conductor move with the power flow
// (speed with kW, direction with sign) and grey out on a dead bus.
import * as THREE from 'three';
import { basic, clamp, COLORS, FlowLine, fmt, lambert, makeLabel } from './common.js';

const Y = 0.6; // conductor height

function box(scene, [x, z], [w, h, d], color) {
  const m = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), lambert(color));
  m.position.set(x, h / 2, z);
  scene.add(m);
  return m;
}

// Screen-sized labels: the layout is wide, so world-sized ones would shrink
// with distance. ``s`` is the width as a fraction of the view height.
function label(scene, [x, y, z], w = 300, h = 110, s = 0.2, rows = 3) {
  const l = makeLabel(w, h, s, { screen: true, rows });
  l.sprite.position.set(x, y, z);
  scene.add(l.sprite);
  return l;
}

export function buildPower() {
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x182028);
  scene.add(new THREE.HemisphereLight(0xe6eef6, 0x2a3036, 1.8));
  const sun = new THREE.DirectionalLight(0xffffff, 1.2);
  sun.position.set(-8, 16, 10);
  scene.add(sun);
  const ground = new THREE.Mesh(new THREE.PlaneGeometry(40, 26), lambert(0x3b4a3f));
  ground.rotation.x = -Math.PI / 2;
  scene.add(ground);
  // The building footprint (the loads sit inside it).
  const pad = new THREE.Mesh(new THREE.PlaneGeometry(22, 9), lambert(0x4d545c));
  pad.rotation.x = -Math.PI / 2;
  pad.position.set(3, 0.01, 6.5);
  scene.add(pad);

  // -- equipment -----------------------------------------------------------------
  // Utility pole and pad-mount transformer T-1.
  const pole = new THREE.Mesh(new THREE.CylinderGeometry(0.12, 0.15, 6, 8), lambert(0x6b4f35));
  pole.position.set(-16, 3, -3);
  scene.add(pole);
  box(scene, [-13.5, -3], [1.6, 1.4, 1.4], 0x6f7c66);
  box(scene, [-10, -3], [0.9, 1.6, 0.7], 0x707a84);
  const breakerLamp = new THREE.Mesh(new THREE.BoxGeometry(0.3, 0.3, 0.05), basic(COLORS.off));
  breakerLamp.position.set(-10, 1.1, -2.62);
  scene.add(breakerLamp);
  // Main switchboard bus.
  const msbMat = basic(0xb08a2e);
  const msb = new THREE.Mesh(new THREE.BoxGeometry(14, 0.25, 0.4), msbMat);
  msb.position.set(-0.5, Y, -3);
  scene.add(msb);
  // PV array on the roof line, with the inverter.
  const pvMat = lambert(0x1d3b6e);
  for (let i = 0; i < 6; i++) {
    const p = new THREE.Mesh(new THREE.BoxGeometry(1.6, 0.06, 1.0), pvMat);
    p.position.set(-9 + i * 1.8, 1.4, -9.5);
    p.rotation.x = -0.45;
    scene.add(p);
  }
  box(scene, [-4, -7.5], [0.9, 1.1, 0.5], 0x7c8a96);
  // BESS container with a state-of-charge gauge.
  box(scene, [2.5, -9], [3.4, 1.8, 1.6], 0xd8d8d0);
  const socBack = new THREE.Mesh(new THREE.BoxGeometry(2.6, 0.3, 0.05), basic(0x2a2f35));
  socBack.position.set(2.5, 1.2, -8.18);
  const socMat = basic(COLORS.ok);
  const soc = new THREE.Mesh(new THREE.BoxGeometry(2.6, 0.3, 0.06).translate(1.3, 0, 0), socMat);
  soc.position.set(1.2, 1.2, -8.16);
  scene.add(socBack, soc);
  // Genset (exhaust puffs while it runs), the ATS, and EP-1.
  const gen = box(scene, [12.5, -9], [3.0, 1.6, 1.5], 0x4c7a46);
  const stack = new THREE.Mesh(new THREE.CylinderGeometry(0.1, 0.1, 0.8, 8), lambert(0x333333));
  stack.position.set(13.6, 2.0, -9);
  scene.add(stack);
  const puffs = [0, 1, 2, 3].map(() => {
    const m = new THREE.Mesh(new THREE.SphereGeometry(0.22, 8, 6), basic(0x9aa0a6, { transparent: true, opacity: 0 }));
    scene.add(m);
    return m;
  });
  box(scene, [10, -3], [1.0, 1.8, 0.7], 0x707a84);
  const atsLever = new THREE.Mesh(new THREE.BoxGeometry(0.08, 0.6, 0.08), basic(0xffffff));
  atsLever.position.set(10, 1.3, -2.6);
  scene.add(atsLever);
  box(scene, [12.5, 3], [1.4, 1.6, 0.6], 0x8a6d3b);

  // -- conductors (flow lines) ------------------------------------------------------
  const lines = {
    utility: new FlowLine([[-16, -3], [-13.5, -3], [-10, -3], [-7.5, -3]], { y: Y }),
    pv: new FlowLine([[-4.5, -9.3], [-4, -7.5], [-4, -3.2]], { y: Y, count: 7 }),
    bess: new FlowLine([[2.5, -8.1], [2.5, -3.2]], { y: Y, count: 6 }),
    ats_normal: new FlowLine([[6.5, -3], [9.5, -3]], { y: Y, count: 5 }),
    gen: new FlowLine([[11, -9], [10, -6], [10, -3.4]], { y: Y, count: 6 }),
    ep1: new FlowLine([[10.5, -3], [12.5, -3], [12.5, 2.7]], { y: Y, count: 8 }),
  };
  Object.values(lines).forEach((l) => scene.add(l.group));

  // Feeders from the MSB to each bus's loads (laid out from the frame's loads).
  const feeders = new Map(); // bus -> {line, label, lamp}
  const FEEDER_X = { msb: -6, boiler: -3.5, mcc1: -1, pkg: 1.5, lp1: 4, rtu: 6 };
  function feeder(bus) {
    if (feeders.has(bus)) return feeders.get(bus);
    let entry;
    if (bus === 'ep1') {
      const line = new FlowLine([[12.5, 3.3], [12.5, 7.5]], { y: Y, count: 5 });
      scene.add(line.group);
      entry = { line, label: label(scene, [12.5, 2.6, 8.6], 260, 150, 0.15, 5), x: 12.5 };
    } else {
      const x = FEEDER_X[bus] ?? 7.5;
      const line = new FlowLine([[x, -2.8], [x, 6.5]], { y: Y, count: 7 });
      scene.add(line.group);
      box(scene, [x, 7.3], [1.4, 1.0, 1.0], 0x5f6a74);
      entry = { line, label: label(scene, [x, 2.4, 8.6], 230, 150, 0.125, 5), x };
    }
    feeders.set(bus, entry);
    return entry;
  }

  const labels = {
    utility: label(scene, [-12, 3.6, -3]),
    pv: label(scene, [-6.5, 3.4, -9.5]),
    bess: label(scene, [2.5, 3.6, -9]),
    gen: label(scene, [12.5, 3.6, -9]),
    ats: label(scene, [8.6, 3.0, -1.6], 260, 80, 0.16, 2),
    msb: label(scene, [-0.5, 2.4, -1.2], 400, 80, 0.26, 2),
    tariff: label(scene, [-8.5, 3.0, 9.5], 260, 110, 0.18),
  };


  let genOn = false;

  function apply(f) {

    const p = f.power;
    const msbLive = p.buses.msb ?? true;
    const utilityLive = !!p.utility_ok && !!p.utility_breaker_closed;
    lines.utility.set(p.grid_kw, utilityLive);
    lines.pv.set(p.pv_kw, p.pv_connected && msbLive);
    lines.bess.set(p.bess_kw, p.bess_connected && msbLive);
    const emergency = !!p.ats_on_emergency;
    lines.ats_normal.set(emergency ? 0 : p.ep1_kw, msbLive && !emergency);
    lines.gen.set(p.gen_kw, !!p.gen_running);
    lines.ep1.set(p.ep1_kw, p.buses.ep1 ?? true);
    genOn = !!p.gen_running;
    msbMat.color.setHex(msbLive ? 0xb08a2e : 0x3a3f45);
    breakerLamp.material.color.setHex(p.utility_breaker_closed ? COLORS.ok : COLORS.bad);
    atsLever.rotation.z = emergency ? -0.7 : 0.7;
    atsLever.material.color.setHex(emergency ? COLORS.warn : 0xffffff);
    const socPct = clamp(p.bess_soc_pct ?? 0, 0, 100);
    soc.scale.x = Math.max(socPct / 100, 0.001);
    socMat.color.setHex(socPct < 20 ? COLORS.bad : socPct < 50 ? COLORS.warn : COLORS.ok);
    // Loads per bus.
    const byBus = new Map();
    for (const ld of p.loads) {
      if (!byBus.has(ld.bus)) byBus.set(ld.bus, []);
      byBus.get(ld.bus).push(ld);
    }
    for (const [bus, loads] of byBus) {
      const fd = feeder(bus);
      const kw = loads.reduce((a, l) => a + l.kw, 0);
      const live = p.buses[bus] ?? true;
      fd.line.set(kw, live);
      const head = bus === 'ep1' ? 'EP-1 (emergency)' : bus.toUpperCase();
      fd.label.draw([
        [live ? `${head}  ${fmt(kw)} kW` : `${head}  NO POWER`, live ? COLORS.text : '#ff6b5e'],
        ...loads.slice(0, 4).map((l) => [`${l.name} ${fmt(l.kw, 1)}`, live ? COLORS.muted : '#7a4040']),
      ]);
    }
    labels.utility.draw([
      ['Utility · relay-main'],
      [utilityLive ? `import ${fmt(p.grid_kw)} kW` : p.utility_ok ? 'BREAKER OPEN' : 'UTILITY LOST', utilityLive ? COLORS.text : '#ff6b5e'],
      [`breaker ${p.utility_breaker_closed ? 'closed' : 'open'}${p.utility_tripped ? ' (tripped)' : ''}`, COLORS.muted],
    ]);
    labels.pv.draw([['PV array'], [`${fmt(p.pv_kw)} kW`], [`limit ${fmt(p.pv_limit_pct)} % · ${fmt(p.ghi_wm2)} W/m²`, COLORS.muted]]);
    const bessDir = (p.bess_kw ?? 0) > 0.5 ? 'discharging' : (p.bess_kw ?? 0) < -0.5 ? 'charging' : 'idle';
    labels.bess.draw([['BESS'], [`SoC ${fmt(p.bess_soc_pct)} %`], [`${bessDir} ${fmt(Math.abs(p.bess_kw ?? 0))} kW`, COLORS.muted]]);
    labels.gen.draw([
      ['Genset'],
      [p.gen_running ? `RUNNING ${fmt(p.gen_kw)} kW` : 'stopped', p.gen_running ? '#ffb020' : COLORS.muted],
      [`fuel ${fmt(p.gen_fuel_pct)} %`, COLORS.muted],
    ]);
    labels.ats.draw([['ATS'], [emergency ? 'EMERGENCY (genset)' : 'normal (utility)', emergency ? '#ffb020' : '#5cc98a']]);
    labels.msb.draw([
      ['Main switchboard'],
      [msbLive ? `load ${fmt(p.load_kw)} kW · demand ${fmt(p.demand_kw)} kW` : 'DEAD: no source', msbLive ? COLORS.text : '#ff6b5e'],
    ]);
    labels.tariff.draw(
      [[p.onpeak ? 'ON-PEAK' : 'off-peak', '#ffffff'], [`$${fmt(p.rate_usd_kwh, 3)}/kWh`, '#ffffff'], [`month $${fmt(p.cost_usd)}`, '#dddddd']],
      p.onpeak ? 'rgba(150,30,30,0.9)' : 'rgba(30,90,60,0.9)',
    );
  }

  function tick(dt, now) {
    Object.values(lines).forEach((l) => l.update(dt));
    feeders.forEach((fd) => fd.line.update(dt));
    puffs.forEach((m, i) => {
      const u = ((now / 1600 + i / puffs.length) % 1);
      m.position.set(13.6 + u * 0.6, 2.4 + u * 2.2, -9);
      m.scale.setScalar(0.6 + u * 1.6);
      m.material.opacity = genOn ? 0.5 * (1 - u) : 0;
    });
    gen.position.y = 0.8 + (genOn ? Math.sin(now / 30) * 0.01 : 0);
  }

  return {
    scene,
    cameras: { power: { pos: [6, 20, 21], target: [5.5, 0, -0.5] } },
    apply,
    tick,
  };
}
