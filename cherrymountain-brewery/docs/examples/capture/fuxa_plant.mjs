// A stand-in plant for a real FUXA server running the range's generated project.
//
// It serves what the project's devices read, from a snapshot file written by
// fuxa_snapshot.py (values of the offline simulation), on loopback:
//   plc-brewhouse (OPC UA, 4840), plc-cellar, plc-utilities, plc-power
//   (Modbus TCP, 1502-1504), plc-packaging (S7, 102), and the BACnet devices
//   (UDP 47808 on 127.0.0.2).
// A value is the snapshot's engineering value times the factor the project's tag
// uses (divisor, or the linear scale), so FUXA shows the snapshot's number. A
// tag the snapshot does not hold reads 0. A write from FUXA is kept until the
// next snapshot.
//
// With --live-batch the brewhouse's batch handshake (phase, command, state,
// step, request counter) is the tests' port of plc-brewhouse's program
// (fuxa/tests/brewhouse_sim.mjs), with 4 s phases, and the fermenter status on
// plc-cellar comes from the tests' cellar model; that is what the real batch
// engine in the project talks to.
//
//   node fuxa_plant.mjs --root DIR --fuxa DIR --pkg DIR --project FILE
//                       --snapshot FILE [--live-batch] [--control 18181]
//
// root: the cherrymountain-brewery directory. fuxa: FUXA's server directory
// (with node_modules: node-opcua, modbus-serial). pkg: a directory with
// node_modules holding node-bacnet and node-snap7. Control port, on 127.0.0.1:
//   GET /snapshot?file=PATH   serve another snapshot
//   GET /request?n=1          press the brewhouse panel's request button (live batch)
//   GET /status               what FUXA wrote (a JSON list)
import { createRequire } from 'node:module';
import fs from 'node:fs';
import http from 'node:http';
import { fork } from 'node:child_process';

const arg = (name, dflt) => {
  const i = process.argv.indexOf('--' + name);
  return i < 0 ? dflt : process.argv[i + 1];
};
const root = arg('root');
const live = process.argv.includes('--live-batch');
const control = Number(arg('control', '18181'));
const requireFuxa = createRequire(arg('fuxa') + '/package.json');
const requirePkg = createRequire(arg('pkg') + '/package.json');
const opcua = requireFuxa('node-opcua');
const ModbusRTU = requireFuxa('modbus-serial');
const project = JSON.parse(fs.readFileSync(arg('project'), 'utf8'));
const devices = Object.values(project.devices);
const device = (name) => devices.find((d) => d.name === name);

let snap = { values: {} };
const loadSnapshot = (file) => { snap = JSON.parse(fs.readFileSync(file, 'utf8')); written.clear(); };
const written = new Map(); // tag name -> raw value FUXA wrote
const writes = [];

// Raw value of a tag: what the PLC register holds.
const factor = (tag) => {
  if (tag.divisor) { return tag.divisor; }
  const s = tag.scale;
  return s && s.mode === 'linear' ? (s.rawHigh - s.rawLow) / (s.scaledHigh - s.scaledLow) : 1;
};
const boolType = (tag) => tag.type === 'Bool' || tag.type === 'Boolean';

// The batch handshake (--live-batch), as in the tests.
let plc = null; let cellar = null; let requests = 0; let knockoutFv = 0;
const liveTags = {};
if (live) {
  const { BrewhousePlc, Cellar } = await import(root + '/fuxa/tests/brewhouse_sim.mjs');
  const quick = {}; for (let n = 1; n <= 10; n += 1) { quick[n] = () => (n === 10 ? 0 : 4); }
  plc = new BrewhousePlc(quick);
  cellar = new Cellar();
  Object.assign(liveTags, {
    'BH.BATCH.XC01.PHASE': [() => plc.reg.phase, (v) => { plc.reg.phase = v; }],
    'BH.BATCH.XC01.CMD': [() => plc.reg.cmd, (v) => { plc.reg.cmd = v; }],
    'BH.BATCH.XS01.STATE': [() => plc.reg.state, null],
    'BH.BATCH.XS01.STEP': [() => plc.reg.step, null],
    'BH.BATCH.XI01.BATCH': [() => plc.reg.batch, (v) => { plc.reg.batch = v; }],
    'BH.BATCH.XC03.PARAM': [() => plc.reg.param, (v) => { plc.reg.param = v; }],
    'BH.BATCH.XC02.ACCEPTED': [() => plc.reg.accepted, (v) => { plc.reg.accepted = v; }],
    'BH.BATCH.XI03.ACCEPTED': [() => plc.reg.accepted, null],
    'BH.BATCH.XI02.REQUESTS': [() => requests, null],
    'BH.ENTRY.XS01.LOCKED': [() => plc.lockM, null],
    'BH.ENTRY.XS02.LOCKED': [() => plc.lockK, null],
    'BH.HLT.TT01.SP': [() => plc.reg.hlt, (v) => { plc.reg.hlt = v; }],
    'BH.MLT.TT01.SP': [() => plc.reg.mlt, (v) => { plc.reg.mlt = v; }],
    'BH.KO.TT02.SP': [() => plc.reg.ko, (v) => { plc.reg.ko = v; }],
  });
}
const num = (v) => (typeof v === 'boolean' ? (v ? 1 : 0) : Number(v) || 0);

const rawOf = (tag) => {
  const l = liveTags[tag.name];
  if (l) { return num(l[0]()); }
  if (written.has(tag.name)) { return written.get(tag.name); }
  const v = snap.values[tag.name];
  if (v === undefined) { return 0; }
  return boolType(tag) ? (v ? 1 : 0) : Math.round(v * factor(tag));
};
const store = (tag, raw) => {
  writes.push([new Date().toISOString(), tag.name, raw]);
  const l = liveTags[tag.name];
  if (l) { if (l[1]) { l[1](raw); } return l[1] !== null; }
  written.set(tag.name, raw);
  return true;
};

if (device('plc-brewhouse')) {
// OPC UA: plc-brewhouse.
const TYPES = { UInt16: 'UInt16', Int16: 'Int16', Boolean: 'Boolean' };
const server = new opcua.OPCUAServer({
  port: 4840, resourcePath: '/openplc/opcua', hostname: '127.0.0.1',
  buildInfo: { productName: 'stand-in-plc-brewhouse' },
  securityModes: [opcua.MessageSecurityMode.None], securityPolicies: [opcua.SecurityPolicy.None],
  allowAnonymous: true,
});
await server.initialize();
const space = server.engine.addressSpace;
const ns = space.registerNamespace('urn:cherrymountain:stand-in-plc');
if (ns.index !== 2) { throw new Error('namespace index is ' + ns.index); }
const folder = ns.addFolder(space.rootFolder.objects, { browseName: 'plc-brewhouse' });
for (const tag of Object.values(device('plc-brewhouse').tags)) {
  const m = /^ns=2;s=(.+)$/.exec(tag.address);
  const type = TYPES[tag.type];
  if (!m || !type) { throw new Error('unexpected OPC UA tag ' + tag.name + ' ' + tag.type + ' ' + tag.address); }
  ns.addVariable({
    componentOf: folder, nodeId: 's=' + m[1], browseName: m[1], dataType: type,
    accessLevel: 'CurrentRead | CurrentWrite', userAccessLevel: 'CurrentRead | CurrentWrite',
    value: {
      get: () => new opcua.Variant({ dataType: opcua.DataType[type], value: type === 'Boolean' ? Boolean(rawOf(tag)) : rawOf(tag) }),
      set: (variant) => (store(tag, num(variant.value)) ? opcua.StatusCodes.Good : opcua.StatusCodes.BadNotWritable),
    },
  });
}
await server.start();
console.log('opcua up', server.getEndpointUrl());
}

// Modbus TCP: plc-cellar, plc-utilities, plc-power. FUXA's addresses are 1-based.
const MODBUS = [['plc-cellar', 1502], ['plc-utilities', 1503], ['plc-power', 1504]];
for (const [name, port] of MODBUS.filter(([n]) => device(n))) {
  const byAddr = new Map();
  for (const tag of Object.values(device(name).tags)) { byAddr.set(tag.memaddress + ':' + (Number(tag.address) - 1), tag); }
  const read = (table, addr) => {
    const tag = byAddr.get(table + ':' + addr);
    if (!tag) { return table === '000000' || table === '100000' ? false : 0; }
    let raw = rawOf(tag);
    if (cellar && name === 'plc-cellar') { raw = cellarLive(tag, raw); }
    return table === '000000' || table === '100000' ? Boolean(raw) : raw & 0xffff;
  };
  const write = (table, addr, value) => {
    const tag = byAddr.get(table + ':' + addr);
    if (tag) { store(tag, boolType(tag) ? (value ? 1 : 0) : (tag.type === 'Int16' && value > 32767 ? value - 65536 : value)); }
  };
  const vector = {
    getCoil: (a) => read('000000', a),
    getDiscreteInput: (a) => read('100000', a),
    getInputRegister: (a) => read('300000', a),
    getHoldingRegister: (a) => read('400000', a),
    setCoil: (a, v) => write('000000', a, v),
    setRegister: (a, v) => write('400000', a, v),
  };
  const mb = new ModbusRTU.ServerTCP(vector, { host: '127.0.0.1', port, unitID: 1 });
  mb.on('socketError', (e) => console.log('modbus socket error', e.message));
  console.log('modbus up', name, '127.0.0.1:' + port);
}

// The batch engine reads three things of each fermenter from plc-cellar.
function cellarLive(tag, raw) {
  const m = /^CEL\.FV10([1-4])\.DT01\.PV$/.exec(tag.name);
  if (m) { return Math.round(cellar.fv[Number(m[1]) - 1].extract * factor(tag)); }
  const l = /^CEL\.ENTRY\.XS0([1-4])\.LOCKED$/.exec(tag.name);
  if (l) { return cellar.fv[Number(l[1]) - 1].locked ? 1 : 0; }
  const c = /^CEL\.ENTRY\.XS1([1-4])\.CIP_OK$/.exec(tag.name);
  if (c) { return cellar.fv[Number(c[1]) - 1].cipOk ? 1 : 0; }
  return raw;
}

let fillS7 = () => {};
if (device('plc-packaging')) {
// S7: plc-packaging, in a child process (fuxa_s7.cjs; a value FUXA writes there is
// overwritten by the next snapshot values). Addresses are
// DB<n>.DBW<byte> and DB<n>.DBX<byte>.<bit>.
const s7tags = Object.values(device('plc-packaging').tags).map((tag) => {
  const m = /^DB(\d+)\.DB([WX])(\d+)(?:\.(\d))?$/.exec(tag.address);
  if (!m) { throw new Error('unexpected S7 address ' + tag.address); }
  return { tag, db: Number(m[1]), word: m[2] === 'W', byte: Number(m[3]), bit: Number(m[4] || 0) };
});
const dbs = {};
for (const t of s7tags) { dbs[t.db] = Math.max(dbs[t.db] || 0, t.byte + 2); }
const s7 = fork(new URL('./fuxa_s7.cjs', import.meta.url).pathname, [arg('pkg')]);
s7.on('message', (m) => {
  if (m.type === 'started') { console.log('s7 up 127.0.0.1:102', m.ok, 'DBs', Object.keys(dbs).join(',')); }
});
s7.send({ type: 'layout', dbs });
fillS7 = () => s7.send({ type: 'values', items: s7tags.map((t) => [t.db, t.byte, t.bit, t.word, rawOf(t.tag)]) });
fillS7();
setInterval(fillS7, 500);
process.on('exit', () => s7.kill());
process.on('SIGTERM', () => process.exit(0));
}

// BACnet: one server per device of the project, on 127.0.0.2, .3, ... (UDP 47808).
// FUXA addresses a device by the address its I-Am came from, and every device
// has objects 0-1, 2-1, ..., so each one needs its own address. The first
// server gets the Who-Is (FUXA's broadcast address is 127.0.0.2) and has all of
// them answer.
const bacnet = requirePkg('node-bacnet');
const bdev = devices.find((d) => d.type === 'BACnet');
if (bdev) {
  const E = bacnet.enum;
  const byDevice = new Map(); // device instance -> Map("<type>-<instance>" -> tag)
  for (const tag of Object.values(bdev.tags)) {
    const id = Number(tag.memaddress);
    if (!byDevice.has(id)) { byDevice.set(id, new Map()); }
    byDevice.get(id).set(tag.address, tag);
  }
  const pv = (tag, type) => {
    const raw = rawOf(tag);
    if (type === E.ObjectType.BINARY_INPUT || type === E.ObjectType.BINARY_OUTPUT || type === E.ObjectType.BINARY_VALUE) {
      return { type: E.ApplicationTag.ENUMERATED, value: raw ? 1 : 0 };
    }
    if (type === E.ObjectType.MULTI_STATE_VALUE || type === E.ObjectType.MULTI_STATE_INPUT || type === E.ObjectType.MULTI_STATE_OUTPUT) {
      return { type: E.ApplicationTag.UNSIGNED_INTEGER, value: Math.round(raw) };
    }
    return { type: E.ApplicationTag.REAL, value: raw };
  };
  const servers = [];
  [...byDevice.keys()].sort().forEach((id, i) => {
    const objs = byDevice.get(id);
    const bc = new bacnet({ interface: '127.0.0.' + (2 + i), port: 47808, broadcastAddress: '127.0.0.255' });
    servers.push({ id, bc });
    bc.on('readPropertyMultiple', (c) => {
      const out = [];
      for (const req of c.payload.properties) {
        const { type, instance } = req.objectId;
        const tag = objs.get(type + '-' + instance);
        const values = req.properties.map((p) => ({
          property: { id: p.id, index: E.ASN1_ARRAY_ALL },
          value: tag && p.id === E.PropertyIdentifier.PRESENT_VALUE ? [pv(tag, type)] : [{ type: E.ApplicationTag.NULL, value: null }],
        }));
        out.push({ objectId: { type, instance }, values });
      }
      bc.readPropertyMultipleResponse(c.header.sender, c.invokeId, out);
    });
    bc.on('writeProperty', (c) => {
      const { type, instance } = c.payload.objectId;
      const tag = objs.get(type + '-' + instance);
      const v = c.payload.value.value[0].value;
      if (tag) { store(tag, Number(v)); }
      bc.simpleAckResponse(c.header.sender, E.ConfirmedServiceChoice.WRITE_PROPERTY, c.invokeId);
    });
  });
  servers[0].bc.on('whoIs', (c) => {
    const lo = c.payload && c.payload.lowLimit; const hi = c.payload && c.payload.highLimit;
    for (const { id, bc } of servers) {
      if ((lo === undefined || id >= lo) && (hi === undefined || id <= hi)) {
        bc.iAmResponse(c.header.sender, id, E.Segmentation.NO_SEGMENTATION, 260);
      }
    }
  });
  console.log('bacnet up, devices', servers.map((s, i) => s.id + '@127.0.0.' + (2 + i)).join(' '));
}

if (process.argv.includes('--snapshot')) { loadSnapshot(arg('snapshot')); }
if (plc) {
  setInterval(() => {
    plc.scan(0.1, (n) => { if (n === 9) { const i = cellar.fv.findIndex((f) => f.extract < 0.5 && !f.locked); knockoutFv = i + 1; } });
    if (plc.reg.state === 1 && plc.active === 9 && knockoutFv > 0 && plc.elapsed > 1000) { cellar.fill(knockoutFv); }
  }, 100);
}

http.createServer((req, res) => {
  const u = new URL(req.url, 'http://x');
  try {
    if (u.pathname === '/snapshot') { loadSnapshot(u.searchParams.get('file')); fillS7(); }
    if (u.pathname === '/request') { requests = (requests + Number(u.searchParams.get('n') || 1)) % 65536; }
  } catch (e) { res.statusCode = 500; res.end(String(e)); return; }
  res.setHeader('content-type', 'application/json');
  res.end(JSON.stringify({ scenario: snap.scenario, requests, state: plc && plc.reg, writes: writes.slice(-50) }));
}).listen(control, '127.0.0.1');
console.log('control up 127.0.0.1:' + control);
