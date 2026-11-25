// The batch engine script as a process another program drives, for the
// software-in-the-loop test (sim/tests/test_brewhouse_control_path.py): Python
// holds the plant (the compiled PLC and the HELICS federates' models) and tells
// this process, once a second, what FUXA's drivers would hold; this process runs the
// script once, as FUXA's scheduler does, and answers with the writes it made.
//
// One JSON object per line on stdin, one JSON reply per line on stdout:
//   {"op": "init", "project": path, "writable": [PLC tag names], "t": epoch seconds}
//   {"op": "tick", "t": epoch seconds, "plc": {tag: value}, "cellar": {tag: value}}
//       -> {"result": "ok", "writes": [[tag, value]], "tags": {FUXA Server tag: value}}
//   {"op": "set", "tag": FUXA Server tag, "value": v}      (the Batch view's buttons)
//   {"op": "restart", "restore": bool}                     (FUXA restarts)
import fs from 'node:fs';
import readline from 'node:readline';
import { Clock, Fuxa, ServerDevice, compileScript, opcuaRead, opcuaWrite } from './fuxa_stub.mjs';

let clock, fuxa, server, script, engine, plcValues, cellarValues, writes, writable;
const logs = [];
const realLog = console.log;
console.log = (...args) => { logs.push(args.join(' ')); };

/**
 * A device of the project. `opcua`: the values Python sends are registers, and the
 * driver's linear scale applies on a read and inverts on a write (`writes` carries
 * registers); a Modbus device's values arrive as the driver gives them.
 */
function device(dev, values, canWrite, opcua) {
  const tags = new Map(Object.entries(dev.tags).map(([id, t]) => [id, {
    name: t.name, scale: t.scale, format: t.format,
  }]));
  return {
    name: dev.name,
    tags,
    up: () => values.size > 0,
    read: (id) => {
      const tag = tags.get(id);
      if (!values.has(tag.name)) { return null; }
      return opcua ? opcuaRead(tag, values.get(tag.name)) : values.get(tag.name);
    },
    write: (id, value) => {
      const tag = tags.get(id);
      if (!canWrite(tag.name)) { return false; }
      writes.push([tag.name, opcua ? opcuaWrite(tag, value) : value]);
      return true;
    },
  };
}

function init(msg) {
  const project = JSON.parse(fs.readFileSync(msg.project, 'utf8'));
  clock = new Clock(msg.t * 1000);
  clock.install();
  fuxa = new Fuxa();
  plcValues = new Map();
  cellarValues = new Map();
  writes = [];
  writable = new Set(msg.writable);
  const devices = Object.values(project.devices);
  const byName = (n) => devices.find((d) => d.name === n);
  server = new ServerDevice(Object.values(byName('FUXA Server').tags).map((t) => ({
    id: t.id, name: t.name, type: t.type, init: t.init, restored: t.daq.restored,
  })));
  fuxa.add(server);
  fuxa.add(device(byName('plc-brewhouse'), plcValues, (n) => writable.has(n), true));
  fuxa.add(device(byName('plc-cellar'), cellarValues, () => false, false));
  fuxa.install();
  script = project.scripts.find((s) => s.name === 'cmBatchEngine');
  engine = compileScript(script);
}

function load(map, obj) {
  map.clear();
  for (const [k, v] of Object.entries(obj)) { map.set(k, v); }
}

function tagValues() {
  const out = {};
  for (const [id, t] of server.tags) { out[t.name] = server.values.get(id); }
  return out;
}

async function handle(msg) {
  switch (msg.op) {
    case 'init':
      init(msg);
      return { ok: true };
    case 'tick': {
      clock.ms = msg.t * 1000;
      load(plcValues, msg.plc);
      load(cellarValues, msg.cellar);
      writes = [];
      logs.length = 0;
      const result = await engine();
      return { result, writes, logs: [...logs], tags: tagValues() };
    }
    case 'set':
      for (const [id, t] of server.tags) {
        if (t.name === msg.tag) { server.write(id, msg.value); }
      }
      return { ok: true };
    case 'restart':
      delete global.__cmBatch;
      server.boot();
      if (msg.restore) { server.restore(); }
      engine = compileScript(script);
      return { ok: true };
    default:
      return { error: `unknown op ${msg.op}` };
  }
}

const rl = readline.createInterface({ input: process.stdin });
// One request at a time, in order, even though the engine awaits.
let queue = Promise.resolve();
rl.on('line', (line) => {
  queue = queue.then(async () => {
    let reply;
    try {
      reply = await handle(JSON.parse(line));
    } catch (err) {
      reply = { error: String(err && err.stack ? err.stack : err) };
    }
    realLog(JSON.stringify(reply));
  });
});
rl.on('close', () => { queue.then(() => process.exit(0)); });
