// FUXA's server-side script runtime as far as the batch engine uses it, for the
// offline tests (frangoteam/FUXA at b2972583, server/runtime). The behavior is
// copied from the source, not guessed; each part cites where.
//
//  - scripts/msm.js:99-137 _scriptsToModule: a script becomes
//    `async function <name>(<params>) { try { <code> \n} catch (fuxaError) {
//    console.log(fuxaError); return JSON.stringify(fuxaError); } }` in one module,
//    so an error inside the code never reaches the scheduler.
//  - scripts/index.js:257-275 _getSystemFunctions and msm.js:29-34 init: the
//    system functions are globals. $getTag, $setTag, and $getTagId take and give
//    tag ids; the script never sees a name until it asks $getTagId for an id.
//  - devices/index.js:242-298: $getTag gives the value, or null when the device
//    is not running; $setTag is async and gives what the driver's setValue gives
//    (true when written, false when not), or null when the device is not running.
//  - devices/opcua/index.js:342-347 (read), 370-408 (write), 549-597 (monitored
//    items, polled once a second): OPC UA values arrive through a subscription,
//    so a write is visible to the next read only after the next poll; there is
//    no divisor (modbus/index.js:325,693,797 has the only one).
//  - devices/fuxaserver/index.js:97-103, 124-129, 152-161: the internal tags of
//    the "FUXA Server" device read and write at once; a tag marked to be restored
//    starts as null until FUXA restores its stored value (and stays null if none
//    was stored).
import Module from 'node:module';

/**
 * What the OPC UA driver makes of a register: devices/device-utils.js
 * tagValueCompose (the tag's linear scale, then `format` decimals) on a read, and on a
 * write opcua/index.js setValue (`_toValue` turns the value into an integer for an
 * integer tag) then tagRawCalculator (the inverse of the scale). `tag` is the project's
 * tag ({scale, format}); the register is what the PLC holds.
 */
export function opcuaRead(tag, raw) {
  if (typeof raw !== 'number') { return raw; }
  let value = raw;
  const s = tag.scale;
  if (s && s.mode === 'linear') {
    value = ((s.scaledHigh - s.scaledLow) * (value - s.rawLow)) / (s.rawHigh - s.rawLow) + s.scaledLow;
  }
  return tag.format ? +value.toFixed(tag.format) : value;
}

export function opcuaWrite(tag, value) {
  if (typeof value === 'boolean') { return value; }
  let raw = parseInt(value, 10);
  const s = tag.scale;
  if (s && s.mode === 'linear') {
    raw = s.rawLow + ((s.rawHigh - s.rawLow) * (raw - s.scaledLow)) / (s.scaledHigh - s.scaledLow);
  }
  return Math.trunc(raw);
}

/** A clock the tests move; Date.now() follows it while installed. */
export class Clock {
  constructor(startMs) {
    this.ms = startMs;
    this.real = Date.now;
  }

  install() { Date.now = () => this.ms; }

  uninstall() { Date.now = this.real; }

  advance(seconds) { this.ms += seconds * 1000; }

  get seconds() { return Math.floor(this.ms / 1000); }
}

/**
 * Compile a project script as FUXA does and return its function. `script` is
 * the project's script record ({name, code, sync, parameters}).
 */
export function compileScript(script) {
  const params = (script.parameters || []).map((p) => p.name).join(',');
  const asyncText = script.sync ? '' : 'async';
  const functions = `${asyncText} function ${script.name} (${params}) { try { ${script.code} \n} catch (fuxaError) { console.log(fuxaError); return JSON.stringify(fuxaError); } }`;
  const src = `const path = require('path'); ${functions} module.exports = { ${script.name}: ${script.name}, };`;
  const m = new Module();
  m._compile(src, `${process.cwd()}/msm-scripts.js`);
  return m.exports[script.name];
}

/**
 * The devices of a project and the script API over them. A device is
 * { name, up(), tags: Map(id -> {name}), read(id), write(id, value) }; `read`
 * gives the value the driver holds now (null when it holds none), `write` gives
 * true or false.
 */
export class Fuxa {
  constructor() {
    this.devices = [];
    this.calls = { getTag: 0, setTag: 0 };
    this.failRead = null;
  }

  add(device) {
    this.devices.push(device);
    return device;
  }

  owner(id) { return this.devices.find((d) => d.tags.has(id)); }

  /** Install the system functions as globals, as msm.js init does. */
  install() {
    global.$getTagId = (name, deviceName) => {
      for (const d of this.devices) {
        if (deviceName && d.name !== deviceName) { continue; }
        for (const [id, tag] of d.tags) {
          if (tag.name === name) { return id; }
        }
      }
      return null;
    };
    global.$getTag = (id) => {
      this.calls.getTag += 1;
      if (this.failRead) { throw new Error(this.failRead); }
      const d = this.owner(id);
      if (!d || !d.up()) { return null; }
      const v = d.read(id);
      return v === undefined ? null : v;
    };
    global.$setTag = async (id, value) => {
      this.calls.setTag += 1;
      const d = this.owner(id);
      if (!d || !d.up()) { return null; }
      return d.write(id, value);
    };
  }

  uninstall() {
    delete global.$getTag;
    delete global.$setTag;
    delete global.$getTagId;
    delete global.__cmBatch;
  }
}

/** The "FUXA Server" device: tags that read and write at once. */
export class ServerDevice {
  /** `tags`: [{id, name, type, init, restored}] from the project. */
  constructor(tags) {
    this.name = 'FUXA Server';
    this.tags = new Map(tags.map((t) => [t.id, t]));
    this.stored = new Map();                 // what FUXA keeps of the restored tags
    this.values = new Map();
    this.boot();
  }

  up() { return true; }

  /** (Re)start FUXA: restored tags are null until `restore`; the others take their init. */
  boot() {
    this.values = new Map();
    for (const [id, t] of this.tags) {
      if (t.restored) {
        this.values.set(id, null);
      } else if (t.type === 'number') {
        this.values.set(id, Number(t.init) || 0);
      } else {
        this.values.set(id, t.init === undefined ? '' : t.init);
      }
    }
  }

  /** FUXA has restored the stored values (those that exist). */
  restore() {
    for (const [id, v] of this.stored) { this.values.set(id, v); }
  }

  read(id) { return this.values.get(id); }

  write(id, value) {
    const t = this.tags.get(id);
    if (!t) { return false; }
    this.values.set(id, t.type === 'number' ? parseFloat(value) : value);
    if (t.restored) { this.stored.set(id, this.values.get(id)); }
    return true;
  }
}
