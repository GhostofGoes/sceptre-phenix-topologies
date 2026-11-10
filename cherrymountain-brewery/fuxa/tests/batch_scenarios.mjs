// Scenarios for the batch engine script of the generated scada-fuxa project,
// run against FUXA's script runtime (fuxa_stub.mjs) and a simulator of the
// brewhouse PLC, the cellar, and the panel (brewhouse_sim.mjs), under a clock the
// scenario moves. tests/test_batch_engine.py runs each one:
//
//   node batch_scenarios.mjs --project project.json --list
//   node batch_scenarios.mjs --project project.json <scenario>
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { Clock, Fuxa, ServerDevice, compileScript } from './fuxa_stub.mjs';
import { Plant } from './brewhouse_sim.mjs';

const IDLE = 0, RUNNING = 1, HELD = 2, COMPLETE = 3, ABORTED = 4;
const OPERATOR = { start: 1, hold: 2, restart: 3, abort: 4, reset: 5 };
const WHOLE_BREW = [1, 2, 3, 4, 4, 5, 6, 7, 8, 9, 10];     // MASH_STEP runs once per mash step
const HOUR = 3600;

/** FUXA, the plant, and the engine on a fake clock. */
class Rig {
  constructor(project, options = {}) {
    this.options = options;
    this.clock = new Clock(Date.UTC(2026, 6, 15, 10, 30, 0));
    this.clock.install();
    this.fuxa = new Fuxa();
    this.plant = new Plant(options);
    const devices = Object.values(project.devices);
    const byName = (name) => devices.find((d) => d.name === name);
    const tagMap = (dev) => new Map(Object.entries(dev.tags).map(([id, t]) => [id, {
      name: t.name, scale: t.scale, format: t.format,
    }]));
    const plcTags = tagMap(byName('plc-brewhouse'));
    const cellarTags = tagMap(byName('plc-cellar'));
    this.names = {
      plc: [...plcTags.values()].map((t) => t.name),
      cellar: [...cellarTags.values()].map((t) => t.name),
    };
    this.server = new ServerDevice(Object.values(byName('FUXA Server').tags).map((t) => ({
      id: t.id, name: t.name, type: t.type, init: t.init, restored: t.daq.restored,
    })));
    this.fuxa.add(this.server);
    this.fuxa.add(this.plant.plcDevice(plcTags));
    this.fuxa.add(this.plant.cellarDevice(cellarTags));
    this.fuxa.install();
    // The knockout valves are lined to the fermenter the engine reserved.
    this.plant.routeTo = () => this.tag('BATCH.FV');
    this.script = project.scripts.find((s) => s.name === 'cmBatchEngine');
    this.engine = compileScript(this.script);
    this.up = true;                     // FUXA (the engine's host) is running
    this.restoreAt = null;
    this.logs = [];
    this.realLog = console.log;
    console.log = (...args) => { this.logs.push(args.join(' ')); };
    this.t = 0;
    this.results = [];
  }

  close() {
    console.log = this.realLog;
    this.fuxa.uninstall();
    this.clock.uninstall();
  }

  /** The value of a FUXA Server tag, by name. */
  tag(name) {
    for (const [id, t] of this.server.tags) {
      if (t.name === name) { return this.server.values.get(id); }
    }
    throw new Error(`no tag ${name}`);
  }

  setTag(name, value) {
    for (const [id, t] of this.server.tags) {
      if (t.name === name) { this.server.write(id, value); return; }
    }
    throw new Error(`no tag ${name}`);
  }

  /** The operator presses a button of the Batch view. */
  press(button) { this.setTag('BATCH.CMD', OPERATOR[button]); }

  get status() { return this.tag('BATCH.STATUS'); }

  get state() { return this.tag('BATCH.STATE'); }

  get plc() { return this.plant.plc; }

  alarms() {
    return Object.fromEntries(['TIMEOUT', 'PLC', 'REFUSED', 'NOFV', 'ENGINE']
      .map((k) => [k, this.tag(`BATCH.ALM_${k}`)]));
  }

  /** One second: the plant runs, FUXA polls, the engine runs, the clock moves. */
  async tick() {
    this.plant.step(1);
    this.plant.poll(this.names);
    if (this.up) {
      if (this.restoreAt !== null && this.t >= this.restoreAt) {
        this.server.restore();
        this.restoreAt = null;
      }
      this.results.push(await this.engine());
    }
    this.clock.advance(1);
    this.t += 1;
  }

  async run(seconds) {
    for (let i = 0; i < seconds; i += 1) { await this.tick(); }
  }

  async until(predicate, maxSeconds, what) {
    for (let i = 0; i < maxSeconds; i += 1) {
      if (predicate()) { return i; }
      await this.tick();
    }
    if (predicate()) { return maxSeconds; }
    throw new Error(`timed out after ${maxSeconds} s waiting for ${what}; status: ${this.status}; `
      + `state ${this.state}, PLC ${this.plc.reg.state}, phase ${this.plc.active}`);
  }

  /** FUXA stops (the engine stops running) and starts again `after` seconds later. */
  async restartFuxa({ restore = true, down = 0, restoreAfter = 2 } = {}) {
    this.up = false;
    await this.run(down);
    delete global.__cmBatch;
    this.server.boot();
    this.plant.shownPlc.clear();
    this.plant.shownCellar.clear();
    this.engine = compileScript(this.script);
    this.restoreAt = restore ? this.t + restoreAfter : null;
    this.up = true;
  }

  phases() { return this.plc.starts.map((s) => s.n); }
}

/** Run a scenario with a rig and close it. */
async function using(project, options, body) {
  const r = new Rig(project, options);
  try {
    await body(r);
  } finally {
    r.close();
  }
}

// ======================================================================= scenarios
export const scenarios = {
  // The unattended path: a request at the panel, ten phases, no one at the screen.
  async full_unattended_sequence(project) {
    await using(project, {}, async (r) => {
      r.plant.request(1);
      await r.until(() => r.state === COMPLETE, 8 * HOUR, 'the batch to complete');
      assert.deepEqual(r.phases(), WHOLE_BREW);
      const s = r.plc.starts;
      // the recipe's parameters reach the PLC: grain kg, mash minutes, boil, whirlpool
      assert.deepEqual(s.map((x) => x.param), [503, 0, 0, 60, 10, 0, 0, 60, 20, 0, 0]);
      // strike water (167.4 degF by Palmer, set as 167) until the mash is in, then the
      // sparge temperature; the mash steps
      assert.deepEqual(s.map((x) => x.hlt), [1670, 1670, 1670, 1680, 1680, 1680, 1680, 1680, 1680, 1680, 1680]);
      assert.deepEqual(s.map((x) => x.mlt), [1520, 1520, 1520, 1520, 1680, 1680, 1680, 1680, 1680, 1680, 1680]);
      assert.ok(s.every((x) => x.ko === 640 && x.batch === 1));
      // every Start was followed by a Reset, in order, and nothing else was sent
      assert.deepEqual(r.plc.commands, WHOLE_BREW.flatMap(() => [1, 5]));
      assert.equal(r.tag('BATCH.COMPLETED'), 1);
      assert.equal(r.tag('BATCH.QUEUE'), 0);
      assert.equal(r.tag('BATCH.ID'), 'FOP-0001');
      assert.equal(r.plc.reg.state, 0);
      assert.deepEqual(r.alarms(), { TIMEOUT: 0, PLC: 0, REFUSED: 0, NOFV: 0, ENGINE: 0 });
      // the request was accepted and the plant knows: counter, echo, nothing pending
      assert.equal(r.tag('BATCH.REQUESTS_HANDLED'), 1);
      assert.equal(r.plc.reg.accepted, 1);
      assert.equal(r.plant.pending, 0);
      // the fermenter the engine reserved is the one the wort went to
      assert.equal(r.tag('BATCH.FV'), 1);
      assert.equal(r.plc.knockoutFv, 1);
      assert.equal(r.plant.cellar.fv[0].extract, 16.1);
      assert.match(r.status, /complete/i);
    });
  },

  // A double-brew day: two brews, one fermenter, then the next brew picks another.
  async double_brew_fills_one_fermenter(project) {
    await using(project, {}, async (r) => {
      r.plant.cellar.fv[1].extract = 0.0;              // FV-102 is empty too
      r.plant.request(2);
      await r.until(() => r.tag('BATCH.COMPLETED') === 2, 16 * HOUR, 'two brews');
      assert.deepEqual(r.phases(), [...WHOLE_BREW, ...WHOLE_BREW]);
      assert.equal(r.tag('BATCH.FV'), 1);
      assert.equal(r.tag('BATCH.FV_BREWS'), 2);
      assert.equal(r.plant.cellar.fv[1].extract, 0.0);
      r.plant.request(1);                              // FV-101 holds two brews now
      await r.until(() => r.tag('BATCH.COMPLETED') === 3, 10 * HOUR, 'a third brew');
      assert.equal(r.tag('BATCH.FV'), 2);
      assert.equal(r.tag('BATCH.FV_BREWS'), 1);
      assert.equal(r.plant.cellar.fv[1].extract, 16.1);
      assert.equal(r.tag('BATCH.NUMBER'), 3);
    });
  },

  // The operator's Start on the Batch view runs a brew with no panel request.
  async operator_start_runs_a_brew(project) {
    await using(project, {}, async (r) => {
      await r.run(20);                                 // FUXA's restore window passes
      assert.equal(r.state, IDLE);
      r.press('start');
      await r.until(() => r.state === RUNNING, 60, 'the batch to start');
      assert.equal(r.tag('BATCH.CMD'), 0, 'the command is consumed');
      await r.until(() => r.state === COMPLETE, 8 * HOUR, 'the batch to complete');
      assert.deepEqual(r.phases(), WHOLE_BREW);
      assert.equal(r.tag('BATCH.REQUESTS_HANDLED'), 0);
    });
  },

  // Hold stops the phase (every output off), Restart runs it again, and the time
  // in between is not counted against the phase.
  async hold_and_restart(project) {
    await using(project, {}, async (r) => {
      r.plant.request(1);
      await r.until(() => r.plc.active === 4 && r.plc.reg.state === 1, 4 * HOUR, 'the first mash step');
      await r.run(120);
      r.press('hold');
      await r.until(() => r.state === HELD && r.plc.reg.state === 3, 60, 'the phase to be held');
      assert.equal(r.tag('BATCH.REASON'), 1);
      assert.match(r.status, /held/i);
      const before = r.plc.starts.length;
      await r.run(3 * HOUR);                           // far past the phase's own timeout
      assert.equal(r.state, HELD, 'a held batch waits for the operator');
      assert.equal(r.alarms().TIMEOUT, 0);
      assert.equal(r.plc.reg.state, 3);
      assert.equal(r.plc.starts.length, before);
      r.press('restart');
      await r.until(() => r.plc.reg.state === 1 && r.state === RUNNING, 60, 'the phase to run again');
      await r.until(() => r.state === COMPLETE, 8 * HOUR, 'the batch to complete');
      assert.deepEqual(r.phases(), WHOLE_BREW);        // the held phase was not started a second time
      assert.deepEqual(r.plc.commands.filter((c) => c === 2 || c === 3), [2, 3]);
    });
  },

  // A hold requested between phases is taken there, and Restart carries on.
  async hold_between_phases(project) {
    await using(project, {}, async (r) => {
      r.plant.request(1);
      await r.until(() => r.plc.active === 1, 600, 'milling');
      r.press('hold');
      await r.until(() => r.state === HELD && r.plc.reg.state === 3, 120, 'the PLC phase to be held');
      r.press('restart');
      await r.until(() => r.state === RUNNING, 60, 'the restart');
      await r.until(() => r.state === COMPLETE, 8 * HOUR, 'the batch to complete');
      assert.deepEqual(r.phases(), WHOLE_BREW);
    });
  },

  // Abort: the PLC phase is aborted and reset, the queue is dropped, and the engine
  // refuses another batch until the operator resets.
  async abort_then_reset(project) {
    await using(project, {}, async (r) => {
      r.plant.request(2);
      await r.until(() => r.plc.active === 5 && r.plc.reg.state === 1, 6 * HOUR, 'the vorlauf');
      assert.equal(r.tag('BATCH.QUEUE'), 1);
      r.press('abort');
      await r.until(() => r.state === ABORTED, 120, 'the abort');
      assert.equal(r.plc.reg.state, 0, 'the PLC phase is reset after the abort');
      assert.deepEqual(r.plc.commands.slice(-2), [4, 5]);
      assert.equal(r.tag('BATCH.QUEUE'), 0);
      assert.match(r.status, /aborted/i);
      const phases = r.phases().length;
      r.plant.request(1);
      await r.run(120);
      assert.equal(r.state, ABORTED);
      assert.equal(r.phases().length, phases, 'no start while the abort is not reset');
      assert.equal(r.tag('BATCH.REASON'), 13);
      r.press('reset');
      await r.run(3);
      assert.equal(r.tag('BATCH.QUEUE'), 0, 'a reset drops the queue');
      r.press('start');
      await r.until(() => r.state === COMPLETE, 8 * HOUR, 'a new batch to complete');
      assert.equal(r.tag('BATCH.NUMBER'), 2);
    });
  },

  // A phase that never completes: an alarm and a held batch, not a silent stall.
  async phase_timeout_raises_the_alarm(project) {
    await using(project, {}, async (r) => {
      r.plc.hang.add(4);
      r.plant.request(1);
      await r.until(() => r.state === HELD && r.plc.reg.state === 3, 6 * HOUR, 'the timeout');
      assert.equal(r.plc.active, 4);
      assert.equal(r.alarms().TIMEOUT, 1);
      assert.equal(r.tag('BATCH.REASON'), 2);
      assert.equal(r.tag('BATCH.STATE_TEXT'), 'Held', 'the PLC phase is held (outputs off)');
      assert.match(r.status, /timed out/i);
      const started = r.tag('BATCH.T_BATCH');
      assert.ok(r.clock.seconds - started > 3600 + 60 * 60, 'only after the phase timeout');
      await r.run(HOUR);
      assert.equal(r.state, HELD, 'it stays held');
      // the operator fixes the cause and restarts: the alarm clears and the batch finishes
      r.plc.hang.delete(4);
      r.press('restart');
      await r.until(() => r.state === RUNNING, 60, 'the restart');
      assert.equal(r.alarms().TIMEOUT, 0);
      await r.until(() => r.state === COMPLETE, 8 * HOUR, 'the batch to complete');
    });
  },

  // A timeout the operator answers with Abort.
  async phase_timeout_then_abort(project) {
    await using(project, {}, async (r) => {
      r.plc.hang.add(2);
      r.plant.request(1);
      await r.until(() => r.state === HELD, 4 * HOUR, 'the timeout');
      assert.equal(r.alarms().TIMEOUT, 1);
      r.press('abort');
      await r.until(() => r.state === ABORTED, 120, 'the abort');
      assert.equal(r.alarms().TIMEOUT, 0, 'the abort answers the alarm');
      assert.equal(r.plc.reg.state, 0);
    });
  },

  // No fermenter that is empty and lined up: the wort waits in the kettle, then goes.
  async no_fermenter_waits_then_proceeds(project) {
    await using(project, {}, async (r) => {
      r.plant.request(1);
      await r.until(() => r.state === RUNNING, 60, 'the start');
      r.plant.cellar.fv[0].extract = 5.0;              // FV-101 is lost after the start: all hold beer
      await r.until(() => r.tag('BATCH.WAITING') === 1, 6 * HOUR, 'the wait for a fermenter');
      assert.deepEqual(r.phases(), WHOLE_BREW.slice(0, 9));   // up to WHIRLPOOL
      assert.equal(r.tag('BATCH.REASON'), 10);
      assert.match(r.status, /fermenter/i);
      assert.equal(r.alarms().NOFV, 0, 'not yet');
      await r.run(11 * 60);
      assert.equal(r.alarms().NOFV, 1, 'the alarm after ten minutes');
      assert.equal(r.state, RUNNING);
      assert.equal(r.plc.starts.filter((x) => x.n === 9).length, 0, 'no knockout without a fermenter');
      // FV-103 is emptied (and FV-101 is in entry): the knockout goes to FV-103
      r.plant.cellar.entry(1, true);
      r.plant.cellar.fv[2].extract = 0.0;
      await r.until(() => r.tag('BATCH.WAITING') === 0 && r.plc.active === 9, 120, 'the knockout');
      assert.equal(r.tag('BATCH.FV'), 3);
      assert.equal(r.alarms().NOFV, 0);
      await r.until(() => r.state === COMPLETE, 2 * HOUR, 'the batch to complete');
      assert.equal(r.plant.cellar.fv[2].extract, 16.1);
    });
  },

  // A start is refused while no fermenter is empty and lined up: the brewhouse is
  // not touched, the request stays queued, and the brew starts when one is free.
  async start_refused_without_a_fermenter(project) {
    await using(project, {}, async (r) => {
      r.plant.cellar.fv[0].extract = 5.0;              // every fermenter holds beer
      r.plant.request(1);
      await r.run(60);
      assert.equal(r.state, IDLE);
      assert.equal(r.plc.starts.length, 0, 'nothing is milled with nowhere to send the wort');
      assert.deepEqual(r.plc.commands, []);
      assert.equal(r.tag('BATCH.QUEUE'), 1, 'the request stays queued');
      assert.equal(r.tag('BATCH.REASON'), 17);
      assert.equal(r.alarms().REFUSED, 1);
      assert.match(r.status, /refused.*fermenter/i);
      r.plant.cellar.fv[3].extract = 0.0;              // FV-104 is emptied and cleaned
      await r.until(() => r.state === RUNNING, 60, 'the start');
      assert.equal(r.alarms().REFUSED, 0);
      await r.until(() => r.state === COMPLETE, 10 * HOUR, 'the batch to complete');
      assert.equal(r.tag('BATCH.FV'), 4);
      assert.equal(r.plant.cellar.fv[3].extract, 16.1);
    });
  },

  // A fermenter that has taken its two brews is shown as full, and a third brew
  // the same day is not started into nowhere.
  async a_full_fermenter_is_not_offered_again(project) {
    await using(project, {}, async (r) => {
      r.plant.request(3);
      await r.until(() => r.tag('BATCH.COMPLETED') === 2, 16 * HOUR, 'two brews');
      await r.run(60);
      assert.equal(r.tag('BATCH.FV_NAME'), 'FV-101 (full)');
      assert.equal(r.state, COMPLETE);
      assert.equal(r.tag('BATCH.QUEUE'), 1);
      assert.equal(r.tag('BATCH.REASON'), 17);
      assert.deepEqual(r.phases(), [...WHOLE_BREW, ...WHOLE_BREW]);
    });
  },

  // A start is refused while the brewhouse is not Idle: nothing is sent, an alarm
  // follows, and the brew starts once the PLC is idle.
  async start_refused_when_not_idle(project) {
    await using(project, {}, async (r) => {
      r.plc.reg.state = 6;                             // left Aborted by someone
      r.plant.request(1);
      await r.run(60);
      assert.equal(r.state, IDLE);
      assert.equal(r.plc.starts.length, 0);
      assert.deepEqual(r.plc.commands, []);
      assert.equal(r.tag('BATCH.QUEUE'), 1, 'the request stays queued');
      assert.equal(r.tag('BATCH.REASON'), 11);
      assert.equal(r.alarms().REFUSED, 1);
      assert.match(r.status, /refused/i);
      r.plc.reg.state = 0;                             // the PLC is reset
      await r.until(() => r.state === RUNNING, 60, 'the start');
      assert.equal(r.alarms().REFUSED, 0);
      assert.equal(r.tag('BATCH.QUEUE'), 0);
    });
  },

  // Operator Reset sends the Reset to a PLC left Complete or Aborted, and clears the queue.
  async reset_clears_a_stale_plc_state(project) {
    await using(project, {}, async (r) => {
      r.plc.reg.state = 5;
      r.press('start');
      await r.run(30);
      assert.equal(r.tag('BATCH.REASON'), 11);
      r.press('reset');
      await r.run(10);
      assert.equal(r.plc.reg.state, 0);
      assert.equal(r.tag('BATCH.QUEUE'), 0);
      assert.equal(r.alarms().REFUSED, 0);
      assert.equal(r.plc.starts.length, 0);
    });
  },

  // A vessel in entry refuses the start and holds a running batch.
  async entry_lockout_refuses_and_holds(project) {
    await using(project, {}, async (r) => {
      r.plc.lockK = true;                              // the kettle is in entry
      r.plant.request(1);
      await r.run(60);
      assert.equal(r.plc.starts.length, 0);
      assert.equal(r.tag('BATCH.REASON'), 12);
      assert.equal(r.alarms().REFUSED, 1);
      assert.deepEqual(r.plc.refused, [], 'the PLC was never asked, so no violation alarm');
      assert.equal(r.plc.violation.ktl, false);
      r.plc.lockK = false;
      await r.until(() => r.plc.active === 4 && r.plc.reg.state === 1, 4 * HOUR, 'a mash step');
      // the mash/lauter tun goes into entry mid-phase: the PLC holds the phase
      r.plc.lockM = true;
      await r.until(() => r.state === HELD, 60, 'the engine to hold');
      assert.equal(r.tag('BATCH.REASON'), 16);
      assert.equal(r.alarms().TIMEOUT, 0);
      // Restart is refused while the vessel is in entry; no command reaches the PLC
      const sent = r.plc.commands.length;
      r.press('restart');
      await r.run(30);
      assert.equal(r.state, HELD);
      assert.equal(r.plc.commands.length, sent);
      assert.equal(r.plc.violation.mlt, false, 'no PLC violation alarm');
      r.plc.lockM = false;
      r.press('restart');
      await r.until(() => r.state === RUNNING, 60, 'the restart');
      await r.until(() => r.state === COMPLETE, 8 * HOUR, 'the batch to complete');
    });
  },

  // The PLC link goes down: the batch is held with an alarm; on return the operator
  // restarts and the engine picks the phase up from what the PLC reports.
  async plc_link_loss_holds_the_batch(project) {
    await using(project, {}, async (r) => {
      r.plant.request(1);
      await r.until(() => r.plc.active === 6 && r.plc.reg.state === 1, 6 * HOUR, 'the lauter');
      r.plant.plcLinkUp = false;
      await r.run(30);
      assert.equal(r.state, RUNNING, 'a short outage is ridden through');
      await r.run(60);
      assert.equal(r.state, HELD);
      assert.equal(r.tag('BATCH.REASON'), 7);
      assert.equal(r.alarms().PLC, 1);
      r.plant.plcLinkUp = true;
      await r.run(10);
      assert.equal(r.state, HELD, 'it does not resume by itself');
      r.press('restart');
      await r.until(() => r.state === RUNNING && r.plc.reg.state === 1, 120, 'the phase to run');
      assert.equal(r.alarms().PLC, 0);
      await r.until(() => r.state === COMPLETE, 8 * HOUR, 'the batch to complete');
      assert.equal(r.phases().filter((n) => n === 6).length, 1, 'the lauter was held and restarted, not started twice');
    });
  },

  // The PLC restarts and loses its phase: the engine holds with an alarm, and Restart
  // runs the phase again from its start.
  async plc_restart_loses_the_phase(project) {
    await using(project, {}, async (r) => {
      r.plant.request(1);
      await r.until(() => r.plc.active === 5 && r.plc.reg.state === 1, 6 * HOUR, 'the vorlauf');
      r.plc.power();
      await r.until(() => r.state === HELD, 120, 'the engine to notice');
      assert.equal(r.alarms().PLC, 1);
      assert.equal(r.tag('BATCH.REASON'), 6);
      r.press('restart');
      await r.until(() => r.state === COMPLETE, 8 * HOUR, 'the batch to complete');
      assert.equal(r.phases().filter((n) => n === 5).length, 2, 'the lost phase ran again');
      assert.equal(r.plc.reg.accepted, 1, 'the engine wrote its accepted count back to the restarted PLC');
    });
  },

  // The PLC aborts the phase itself (another writer): the batch is aborted with an alarm.
  async plc_aborts_the_phase(project) {
    await using(project, {}, async (r) => {
      r.plant.request(1);
      await r.until(() => r.plc.active === 3 && r.plc.reg.state === 1, 4 * HOUR, 'the mash in');
      r.plc.reg.cmd = 4;                               // someone else aborts
      await r.until(() => r.state === ABORTED, 120, 'the engine to follow');
      assert.equal(r.alarms().PLC, 1);
      assert.equal(r.tag('BATCH.REASON'), 5);
      assert.equal(r.plc.reg.state, 0);
    });
  },

  // FUXA restarts in the middle of a batch: the restored state carries on, no phase
  // is started twice, and no request is taken twice.
  async fuxa_restart_resumes_the_batch(project) {
    await using(project, {}, async (r) => {
      r.plant.request(2);
      await r.until(() => r.plc.active === 7 && r.plc.reg.state === 1, 8 * HOUR, 'the boil');
      const starts = r.plc.starts.length;
      await r.restartFuxa({ restore: true, down: 90 });
      await r.run(30);
      assert.equal(r.state, RUNNING);
      assert.equal(r.tag('BATCH.QUEUE'), 1);
      assert.equal(r.plc.starts.length, starts, 'the boil was not started again');
      await r.until(() => r.tag('BATCH.COMPLETED') === 2, 16 * HOUR, 'both brews');
      assert.deepEqual(r.phases(), [...WHOLE_BREW, ...WHOLE_BREW]);
      assert.equal(r.tag('BATCH.REQUESTS_HANDLED'), 2);
    });
  },

  // A first boot: nothing was stored, the tags stay null, and the engine starts them at 0.
  async first_boot_initialises_the_state(project) {
    await using(project, {}, async (r) => {
      await r.restartFuxa({ restore: false, down: 0 });
      await r.run(5);
      assert.equal(r.tag('BATCH.STATE'), null, 'it waits for the restore');
      assert.equal(r.plc.starts.length, 0);
      await r.run(20);
      assert.equal(r.tag('BATCH.STATE'), IDLE);
      r.plant.request(1);
      await r.until(() => r.state === COMPLETE, 8 * HOUR, 'the batch to complete');
    });
  },

  // The brew-day request arrives while FUXA is down: it stays counted at the panel, the
  // plant sees it unaccepted, and the engine takes it (once) when FUXA is back.
  async request_while_fuxa_is_down_is_not_lost(project) {
    await using(project, {}, async (r) => {
      await r.run(30);
      r.up = false;
      r.plant.request(2);
      await r.run(10 * 60);
      assert.equal(r.plant.pending, 2, 'nobody accepted it');
      assert.equal(r.plc.starts.length, 0);
      await r.restartFuxa({ restore: true, down: 0 });
      await r.until(() => r.plc.starts.length > 0, 120, 'the batch to start');
      assert.equal(r.plant.pending, 0);
      assert.equal(r.tag('BATCH.REQUESTS_HANDLED'), 2);
      await r.until(() => r.tag('BATCH.COMPLETED') === 2, 16 * HOUR, 'both brews');
      assert.equal(r.tag('BATCH.NUMBER'), 2);
    });
  },

  // The panel's counter restarts (the sim restarted): the engine re-synchronises and
  // does not brew sixty thousand batches.
  async panel_counter_restart_is_resynchronised(project) {
    await using(project, {}, async (r) => {
      r.plant.request(1);
      await r.until(() => r.state === COMPLETE, 8 * HOUR, 'the first batch');
      r.plant.requests = 40000;                        // a counter that ran far ahead, then
      await r.run(30);
      assert.equal(r.tag('BATCH.QUEUE'), 0);
      assert.equal(r.tag('BATCH.REQUESTS_HANDLED'), 40000);
      r.plant.request(1);                              // and the next request is taken
      await r.until(() => r.state === COMPLETE && r.tag('BATCH.COMPLETED') === 2, 8 * HOUR, 'the next batch');
    });
  },

  // FUXA runs interval scripts without waiting for the run before: a second run
  // while one is going does nothing.
  async overlapping_runs_are_skipped(project) {
    await using(project, {}, async (r) => {
      r.plant.request(1);
      await r.run(5);
      const first = r.engine();
      const second = r.engine();
      assert.equal(await second, 'busy');
      assert.equal(await first, 'ok');
    });
  },

  // An error inside a run is contained: the script returns, logs, and raises its alarm
  // after three in a row; it recovers on its own.
  async script_errors_are_contained(project) {
    await using(project, {}, async (r) => {
      await r.run(30);
      r.fuxa.failRead = 'device exploded';
      for (let i = 0; i < 4; i += 1) { await r.tick(); }
      assert.equal(r.tag('BATCH.ALM_ENGINE'), 1);
      assert.ok(r.logs.some((l) => l.includes('device exploded')));
      r.fuxa.failRead = null;
      await r.run(3);
      assert.equal(r.tag('BATCH.ALM_ENGINE'), 0);
      assert.equal(r.state, IDLE);
    });
  },

  // A tag the engine needs is missing from the project: it does nothing and says so once.
  async missing_tag_stops_the_engine(project) {
    await using(project, {}, async (r) => {
      const real = global.$getTagId;
      global.$getTagId = (name, dev) => (name === 'BH.BATCH.XC03.PARAM' ? null : real(name, dev));
      r.plant.request(1);
      await r.run(60);
      assert.equal(r.plc.starts.length, 0);
      assert.equal(r.logs.filter((l) => l.includes('tags missing')).length, 1);
    });
  },

  // The PLC does not answer the Start: a handshake alarm and a held batch, no endless retries.
  async unanswered_start_is_a_fault(project) {
    await using(project, {}, async (r) => {
      r.plant.plcWritable.delete('BH.BATCH.XC01.CMD');  // the OPC UA write is refused
      r.plant.request(1);
      await r.until(() => r.state === HELD, 600, 'the handshake fault');
      assert.equal(r.alarms().PLC, 1);
      assert.equal(r.tag('BATCH.REASON'), 3);
      assert.equal(r.plc.starts.length, 0);
    });
  },
};

// ======================================================================= runner
async function main(argv) {
  const at = argv.indexOf('--project');
  if (at < 0 || !argv[at + 1]) {
    console.error('usage: batch_scenarios.mjs --project project.json (--list | scenario)');
    return 2;
  }
  const project = JSON.parse(fs.readFileSync(argv[at + 1], 'utf8'));
  if (argv.includes('--list')) {
    console.log(Object.keys(scenarios).join('\n'));
    return 0;
  }
  const name = argv.find((a, i) => i > at + 1 && !a.startsWith('--'));
  if (!scenarios[name]) {
    console.error(`no scenario ${name}`);
    return 2;
  }
  try {
    await scenarios[name](project);
  } catch (err) {
    console.error(`FAIL ${name}: ${err && err.stack ? err.stack : err}`);
    return 1;
  }
  console.log(`PASS ${name}`);
  return 0;
}

if (import.meta.url === `file://${process.argv[1]}`) {
  process.exitCode = await main(process.argv.slice(2));
}
