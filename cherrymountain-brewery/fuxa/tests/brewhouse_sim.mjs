// A small simulator of what the batch engine talks to, for the offline tests:
//
//  - plc-brewhouse: a port of the ISA-88 handshake of plc/plc-brewhouse/
//    plc-brewhouse.st (Start, Hold, Restart, Abort, Reset; the Holding and
//    Restarting scans; the entry lockout; the phase and its parameter latched at
//    Start; a command register the program clears), scanned every 100 ms.
//    What the process does inside a phase is reduced to how long the phase runs
//    (see DURATIONS) and whether it can complete. test_plc_port.py runs the same
//    command traces against the compiled program.
//  - plc-cellar: the status the engine reads of each fermenter.
//  - the brewhouse panel: the request counter and the engine's accepted echo.
//  - how FUXA sees them: OPC UA values arrive through a subscription polled once
//    a second, so a read shows the last poll, never the write just made.

import { opcuaRead, opcuaWrite } from './fuxa_stub.mjs';

/** Seconds a phase runs, from its parameter (kg or minutes), at the sim's pace. */
export const DURATIONS = {
  1: (p) => (p > 0 ? p * 2 : 1020),               // MILL: 2 s per kg of the grain bill, else 17 min
  2: () => 1500,                                  // HLT_HEAT
  3: () => 600,                                   // MASH_IN
  4: (p) => 300 + (p > 0 ? p : 60) * 60,          // MASH_STEP: heat-up, then the hold
  5: () => 600,                                   // VORLAUF
  6: () => 5400,                                  // LAUTER
  7: (p) => 1500 + (p > 0 ? p : 60) * 60,         // BOIL: to the boil, then the minutes
  8: (p) => (p > 0 ? p : 20) * 60,                // WHIRLPOOL
  9: () => 1400,                                  // KNOCKOUT
  10: () => 0,                                    // CIP_READY: everything off, done
};

export const PHASE_NAMES = ['', 'MILL', 'HLT_HEAT', 'MASH_IN', 'MASH_STEP', 'VORLAUF',
  'LAUTER', 'BOIL', 'WHIRLPOOL', 'KNOCKOUT', 'CIP_READY'];

/** The brewhouse PLC program (state machine and phases), scanned every 0.1 s. */
export class BrewhousePlc {
  constructor(durations = {}) {
    this.durations = { ...DURATIONS, ...durations };
    this.hang = new Set();          // phases that never complete
    this.useConditions = false;     // a test drives the condition phases (2, 3, 6, 9) by hand:
    this.satisfied = new Set();     //   they complete while their number is in this set
    this.atTemperature = true;      // the mash rest counts only at temperature
    // What the program did, kept across a restart of it, for the tests.
    this.starts = [];               // every phase run: {n, param, hlt, mlt, ko, batch}
    this.commands = [];             // every command the program applied
    this.refused = [];              // Start and Restart refused for a vessel in entry
    this.knockoutFv = 0;
    this.power();
  }

  /** (Re)start the program: memory words are zero, the first scan loads defaults. */
  power() {
    this.reg = {
      phase: 0, cmd: 0, state: 0, step: 0, batch: 0, param: 0, accepted: 0,
      hlt: 1680, mlt: 1520, ko: 640,
    };
    this.active = 0;
    this.param = 0;
    this.elapsed = 0;               // ms; a timed step counts only the scans it runs, so a hold keeps it
    this.boil = 0;                  // ms; the boil timer survives a hold, as KI01 does
    this.lockM = false;             // the entry permits (HS01, HS02)
    this.lockK = false;
    this.lockedM = false;           // the permits of the scan before (the program's XS01.LOCKED, XS02.LOCKED)
    this.lockedK = false;
    this.violation = { mlt: false, ktl: false };   // the XA01, XA02 latches
  }

  /** One scan of `dt` seconds (the ST program's order of steps). */
  scan(dt, onPhaseStart) {
    const r = this.reg;
    let cmd = r.cmd;
    // A latch holds only while its vessel was locked at the scan before; the lock itself
    // follows the permit at once.
    this.violation.mlt = this.lockedM && this.violation.mlt;
    this.violation.ktl = this.lockedK && this.violation.ktl;
    this.lockedM = this.lockM;
    this.lockedK = this.lockK;
    const want = cmd === 1 ? r.phase : this.active;
    const blockedM = this.lockM && want >= 3 && want <= 6;
    const blockedK = this.lockK && want >= 6 && want <= 9;
    if ((cmd === 1 || cmd === 3) && (blockedM || blockedK)) {
      this.violation.mlt = this.violation.mlt || blockedM;
      this.violation.ktl = this.violation.ktl || blockedK;
      this.refused.push({ cmd, phase: want });
      cmd = 0;
    }
    r.cmd = 0;
    if (r.state === 2) { r.state = 3; } else if (r.state === 4) { r.state = 1; }
    switch (cmd) {
      case 1:
        if ((r.state === 0 || r.state === 5 || r.state === 6) && r.phase >= 1 && r.phase <= 10) {
          this.active = r.phase;
          this.param = r.param;
          r.state = 1; r.step = 1;
          this.elapsed = 0;
          if (this.active === 7) { this.boil = 0; }
          this.starts.push({ n: r.phase, param: r.param, hlt: r.hlt, mlt: r.mlt, ko: r.ko, batch: r.batch });
          this.commands.push(1);
          if (onPhaseStart) { onPhaseStart(this.active); }
        }
        break;
      case 2:
        if (r.state === 1) { r.state = 2; this.commands.push(2); }
        break;
      case 3:
        if (r.state === 3) { r.state = 4; this.commands.push(3); }
        break;
      case 4:
        if (r.state >= 1 && r.state <= 4) { r.state = 6; this.commands.push(4); }
        break;
      case 5:
        if (r.state === 5 || r.state === 6) {
          r.state = 0; r.step = 0; this.active = 0; this.param = 0;
          this.commands.push(5);
        }
        break;
      default:
        break;
    }
    const running = r.state === 1;
    let complete = false;
    let timing = false;
    const ms = Math.round(dt * 1000);
    if (running) {
      const n = this.active;
      const need = this.durations[n](this.param) * 1000;
      timing = n !== 4 || this.atTemperature;
      // As the program does: the time served is compared first, then this scan's is added.
      if (n === 7) {
        complete = this.boil >= need;                  // KI01 counts only while boiling
      } else if (this.useConditions && [2, 3, 6, 9].includes(n)) {
        complete = this.satisfied.has(n);
      } else {
        complete = this.elapsed >= need;
      }
      if (this.hang.has(n)) { complete = false; }
    }
    // Entry lockout: the locked vessel's phase is held.
    if (running && ((this.lockM && this.active >= 3 && this.active <= 6)
        || (this.lockK && this.active >= 6 && this.active <= 9))) {
      r.state = 2;
      complete = false;
      timing = false;
    }
    if (timing) {
      if (this.active === 7) { this.boil += ms; } else { this.elapsed += ms; }
    }
    if (complete) {
      r.state = 5;
      r.step = 0;
    }
  }
}

/** The fermenters as plc-cellar reports them (extract in degP; 0 when empty). */
export class Cellar {
  constructor() {
    // FV-101 is empty, 102 to 104 are full of beer (DESIGN.md 9.4).
    this.fv = [
      { extract: 0.0, locked: false, cipOk: true, brews: 0 },
      { extract: 9.8, locked: false, cipOk: true, brews: 2 },
      { extract: 6.1, locked: false, cipOk: true, brews: 2 },
      { extract: 3.1, locked: false, cipOk: true, brews: 2 },
    ];
    this.linkUp = true;
  }

  /** Wort reaches a fermenter (what a knockout does). */
  fill(n) { this.fv[n - 1].extract = 16.1; }

  /** Put an FV in or out of confined-space entry (the permit also withdraws CIP). */
  entry(n, on) {
    this.fv[n - 1].locked = on;
    this.fv[n - 1].cipOk = !on;
  }

  firstEmpty() {
    const i = this.fv.findIndex((f) => f.extract < 0.5 && !f.locked);
    return i < 0 ? 0 : i + 1;
  }
}

/** The plant as FUXA sees it: PLC, cellar, panel, and how often FUXA polls them. */
export class Plant {
  constructor(options = {}) {
    this.plc = new BrewhousePlc(options.durations);
    this.cellar = new Cellar();
    this.plcLinkUp = true;
    this.requests = 0;                  // the panel's request counter
    this.routeTo = null;                // () => the fermenter the knockout valves are lined to
    this.knockoutCounted = false;
    this.shownPlc = new Map();
    this.shownCellar = new Map();
    this.plcWritable = new Set([
      'BH.BATCH.XC01.PHASE', 'BH.BATCH.XC01.CMD', 'BH.BATCH.XC03.PARAM', 'BH.BATCH.XI01.BATCH',
      'BH.BATCH.XC02.ACCEPTED', 'BH.HLT.TT01.SP', 'BH.MLT.TT01.SP', 'BH.KO.TT02.SP',
    ]);
    this.log = [];                      // [t, what] writes, for the tests
  }

  /** A press of the panel's request button (the brew-day schedule or the event). */
  request(n) { this.requests = (this.requests + n) % 65536; }

  get pending() { return (this.requests - this.plc.reg.accepted + 65536) % 65536; }

  // ---- the live values of plc-brewhouse's tags (what the OPC UA server holds)
  plcLive(name) {
    const r = this.plc.reg;
    switch (name) {
      case 'BH.BATCH.XC01.PHASE': return r.phase;
      case 'BH.BATCH.XC01.CMD': return r.cmd;
      case 'BH.BATCH.XS01.STATE': return r.state;
      case 'BH.BATCH.XS01.STEP': return r.step;
      case 'BH.BATCH.XI01.BATCH': return r.batch;
      case 'BH.BATCH.XC03.PARAM': return r.param;
      case 'BH.BATCH.XC02.ACCEPTED': return r.accepted;
      case 'BH.BATCH.XI02.REQUESTS': return this.requests;
      case 'BH.ENTRY.XS01.LOCKED': return this.plc.lockM;
      case 'BH.ENTRY.XS02.LOCKED': return this.plc.lockK;
      case 'BH.HLT.TT01.SP': return r.hlt;
      case 'BH.MLT.TT01.SP': return r.mlt;
      case 'BH.KO.TT02.SP': return r.ko;
      default: return undefined;
    }
  }

  plcWrite(name, value) {
    const r = this.plc.reg;
    const v = Math.trunc(Number(value));         // the driver's parseInt
    switch (name) {
      case 'BH.BATCH.XC01.PHASE': r.phase = v; break;
      case 'BH.BATCH.XC01.CMD': r.cmd = v; break;
      case 'BH.BATCH.XC03.PARAM': r.param = v; break;
      case 'BH.BATCH.XI01.BATCH': r.batch = v; break;
      case 'BH.BATCH.XC02.ACCEPTED': r.accepted = v; break;
      case 'BH.HLT.TT01.SP': r.hlt = v; break;
      case 'BH.MLT.TT01.SP': r.mlt = v; break;
      case 'BH.KO.TT02.SP': r.ko = v; break;
      default: return false;
    }
    return true;
  }

  cellarLive(name) {
    // Only the four fermenters are modeled (the bright tanks' bits are never read).
    const m = /^CEL\.FV10([1-4])\.DT01\.PV$/.exec(name);
    if (m) { return this.cellar.fv[Number(m[1]) - 1].extract; }
    const lock = /^CEL\.ENTRY\.XS0([1-4])\.LOCKED$/.exec(name);
    if (lock) { return this.cellar.fv[Number(lock[1]) - 1].locked; }
    const cip = /^CEL\.ENTRY\.XS1([1-4])\.CIP_OK$/.exec(name);
    if (cip) { return this.cellar.fv[Number(cip[1]) - 1].cipOk; }
    return undefined;
  }

  /**
   * Advance the plant `seconds` (10 scans a second), moving the knockout's fill.
   * The wort goes where the valves are lined: `routeTo()` names the fermenter
   * (the tests wire it to the engine's reservation); the first empty one if unset.
   */
  step(seconds) {
    const scans = Math.round(seconds * 10);
    for (let i = 0; i < scans; i += 1) {
      this.plc.scan(0.1, (n) => {
        if (n === 9) {
          this.plc.knockoutFv = this.routeTo ? this.routeTo() : this.cellar.firstEmpty();
          this.knockoutCounted = false;
        }
      });
      const fv = this.plc.knockoutFv;
      if (this.plc.reg.state === 1 && this.plc.active === 9 && fv > 0 && this.plc.elapsed > 60000) {
        this.cellar.fill(fv);                           // wort has reached the fermenter
      }
      if (this.plc.reg.state === 5 && this.plc.active === 9 && fv > 0 && !this.knockoutCounted) {
        this.cellar.fv[fv - 1].brews += 1;
        this.knockoutCounted = true;
      }
    }
  }

  /** FUXA's poll: each OPC UA and Modbus tag shows what the device holds now. */
  poll(names) {
    for (const name of names.plc) {
      this.shownPlc.set(name, this.plcLive(name));
    }
    for (const name of names.cellar) {
      this.shownCellar.set(name, this.cellarLive(name));
    }
  }

  /** The OPC UA device of plc-brewhouse as FUXA drives it (`tags`: id -> project tag). */
  plcDevice(tags) {
    const plant = this;
    return {
      name: 'plc-brewhouse',
      tags,
      up: () => plant.plcLinkUp,
      read: (id) => {
        const v = plant.shownPlc.get(tags.get(id).name);
        return v === undefined ? null : opcuaRead(tags.get(id), v);
      },
      write: (id, value) => {
        const tag = tags.get(id);
        if (!plant.plcWritable.has(tag.name)) { return false; }
        plant.log.push([tag.name, value]);
        return plant.plcWrite(tag.name, opcuaWrite(tag, value));
      },
    };
  }

  cellarDevice(tags) {
    const plant = this;
    return {
      name: 'plc-cellar',
      tags,
      up: () => plant.cellar.linkUp,
      read: (id) => {
        const v = plant.shownCellar.get(tags.get(id).name);
        return v === undefined ? null : v;
      },
      write: () => false,
    };
  }
}
