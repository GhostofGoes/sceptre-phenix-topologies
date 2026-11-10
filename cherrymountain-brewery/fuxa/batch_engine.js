// cmBatchEngine: the ISA-88 batch engine of scada-fuxa (DESIGN.md 12.2).
//
// fuxa/build_project.py puts this file into the scada-fuxa project as one
// server script, with the placeholder on the CFG line below replaced by the
// recipe, the phase list, the timings, and the tag names. FUXA wraps the body in
//   async function cmBatchEngine() { try { <this file> } catch (fuxaError) {...} }
// (server/runtime/scripts/msm.js) and runs it every second from its scheduler
// (scripts/index.js), without waiting for the run before to end. An error is
// caught by that wrapper and only logged, so this body catches its own and
// raises the BATCH.ALM_ENGINE alarm. Nothing in a function survives a run: the
// whole batch state is in FUXA's own tags (device "FUXA Server"), which FUXA
// restores after a restart; `global` holds only what may be lost (tag ids, a
// run-in-progress flag, the time of the last command).
//
// What it does, one tick at a time, from what the PLC reports (level-triggered,
// so a restart or a lost write repeats nothing and misses nothing):
//   queue    brews from the Batch view (Start) and from the brewhouse panel
//   start    refused unless the brewhouse PLC is Idle and no vessel is in entry
//   per phase  PREP (setpoints, phase parameter, phase number) -> START (command
//            1) -> RUN (wait for Complete, with a timeout) -> RESET (command 5)
//   KNOCKOUT first reserves a fermenter that plc-cellar reports empty and lined up
//   Hold / Restart / Abort from the Batch view, a timeout held with an alarm
//
// FUXA API used (server/runtime/scripts/index.js, devices/index.js): $getTagId
// (tag name -> id), $getTag (id -> value, null when the device is down),
// $setTag (id, value -> true when written). The OPC UA driver reads and writes
// what a tag's linear scale makes of the register (build_project.py linear_scale),
// so the PLC's setpoints are degF here, and whole degrees: the driver turns a
// value into an integer before it scales it (devices/opcua/index.js setValue).
// The other PLC tags it uses (phase, command, state, parameter, counters) are
// unscaled registers.

const CFG = __CFG__;
const T = CFG.timing;
const SEQ = CFG.sequence;
const NAMES = CFG.names;

// Batch states, steps, hold reasons (BATCH.STATE, BATCH.STEP, BATCH.WHY).
const IDLE = 0, RUNNING = 1, HELD_ST = 2, COMPLETE = 3, ABORTED = 4;
const PREP = 1, START = 2, RUN = 3, RESET = 4, FVWAIT = 5, HOLDING = 6, HELD = 7,
  RESTARTING = 8, ABORTING = 9, ABORT_RESET = 10;
const W_OPERATOR = 1, W_TIMEOUT = 2, W_HANDSHAKE = 3, W_PLC_HELD = 4, W_PLC_ABORT = 5,
  W_PLC_LOST = 6, W_LINK = 7, W_NOFV = 10, W_NOT_IDLE = 11, W_LOCKOUT = 12,
  W_NEEDS_RESET = 13, W_NO_LINK = 14, W_ABORTED = 15, W_ENTRY_HOLD = 16;
// The PLC's phase states and commands (plc-brewhouse.st).
const P_IDLE = 0, P_RUNNING = 1, P_HOLDING = 2, P_HELD = 3, P_RESTARTING = 4,
  P_COMPLETE = 5, P_ABORTED = 6;
const C_START = 1, C_HOLD = 2, C_RESTART = 3, C_ABORT = 4, C_RESET = 5;

const STATE_TEXT = ['Idle', 'Running', 'Held', 'Complete', 'Aborted'];
const WHY_TEXT = {
  1: 'held by the operator',
  2: 'phase timed out',
  3: 'the brewhouse PLC did not answer a command',
  4: 'the brewhouse PLC held the phase',
  5: 'the brewhouse PLC aborted the phase',
  6: 'the brewhouse PLC lost the phase (restarted?)',
  7: 'the brewhouse PLC link is down',
  10: 'waiting for a fermenter that is empty and lined up',
  11: 'start refused: the brewhouse is not Idle',
  12: 'start refused: a brewhouse vessel is in entry',
  13: 'start refused: the last batch needs a Reset',
  14: 'start refused: no link to the brewhouse PLC',
  15: 'aborted',
  16: 'held: a brewhouse vessel is in entry',
};

// The state that persists (restored by FUXA), as logical name -> tag.
const PERSIST = ['st', 'step', 'seq_ix', 'resume', 'why', 'number', 'completed', 'queue',
  'handled', 'fv', 'fv_brews', 'hold_req', 't_batch', 't_phase', 't_step', 't_fv',
  'alm_timeout', 'alm_plc', 'alm_refused', 'alm_nofv', 'alm_engine'];

const G = global.__cmBatch || (global.__cmBatch = {
  t0: Date.now(), ids: null, busy: 0, lastOk: Date.now(), cmdT: 0, cmdCode: 0,
  refuseT: 0, errors: 0, logged: {},
});
const log = (msg) => console.log('[cmBatchEngine] ' + msg);
const num = (v) => (typeof v === 'number' && isFinite(v) ? v : null);
const flag = (v) => (v === true || v === 1 || v === '1' ? true : (v === false || v === 0 || v === '0' ? false : null));
const get = (key) => $getTag(G.ids[key]);
const put = async (key, value) => (await $setTag(G.ids[key], value)) === true;

// FUXA does not serialise interval runs; a run that is still going blocks the next.
const started = Date.now();
if (G.busy && started - G.busy < T.busy_s * 1000) {
  return 'busy';
}
G.busy = started;
try {
  await tick(Math.floor(started / 1000));
  G.errors = 0;
} catch (err) {
  G.errors += 1;
  log('error: ' + (err && err.stack ? err.stack : err));
  if (G.ids && G.errors >= T.error_alarm) {
    try { await put('alm_engine', 1); } catch (e2) { /* nothing more to do */ }
  }
} finally {
  G.busy = 0;
}
return 'ok';

async function tick(now) {
  if (!G.ids && !resolveIds()) {
    return;
  }
  // ---- the persisted state; a restored tag reads null until FUXA has restored it
  const S = {};
  let missing = false;
  for (const k of PERSIST) {
    S[k] = num(get(k));
    if (S[k] === null) { missing = true; }
  }
  const S0 = Object.assign({}, S);     // as read: what changed is written back
  if (missing) {
    if (Date.now() - G.t0 < T.init_grace_s * 1000) {
      return;
    }
    for (const k of PERSIST) {
      if (S[k] === null) { S[k] = 0; }   // the first boot: nothing was stored
    }
  }
  if (G.errors === 0 && S.alm_engine) { S.alm_engine = 0; }

  // ---- the brewhouse PLC and the cellar, as read now
  const P = readPlc();
  if (P.ok) { G.lastOk = Date.now(); }
  const lockM = P.lockM === true, lockK = P.lockK === true;
  const anyLock = () => lockM || lockK || P.lockM === null || P.lockK === null;

  // ---- helpers that change S (the state is written back at the end)
  const to = (st, step, why) => {
    S.st = st; S.step = step; S.t_step = now;
    if (why !== undefined) { S.why = why; }
  };
  const hold = (why, resumeStep) => { S.resume = resumeStep; to(HELD_ST, HELD, why); };
  const fault = (why, alarm, resumeStep) => {
    log('fault ' + why + ' in ' + (SEQ[S.seq_ix] ? SEQ[S.seq_ix].label : '-'));
    if (alarm) { S[alarm] = 1; }
    hold(why, resumeStep);
  };
  const plcCmd = async (code) => {
    // The program applies a command in its next scan and clears the register, so
    // a command is written only while the register reads 0 and is repeated only
    // after retry_s without effect.
    if (P.cmd !== 0) { return false; }
    if (G.cmdCode === code && Date.now() - G.cmdT < T.retry_s * 1000) { return false; }
    const ok = await put('plc_cmd', code);
    if (ok) { G.cmdT = Date.now(); G.cmdCode = code; }
    return ok;
  };
  const clearAlarms = () => {
    S.alm_timeout = 0; S.alm_plc = 0; S.alm_refused = 0; S.alm_nofv = 0;
  };
  const finishAbort = () => {
    to(ABORTED, 0, S.why === W_PLC_ABORT ? W_PLC_ABORT : W_ABORTED);
    S.queue = 0; S.fv = 0; S.fv_brews = 0; S.hold_req = 0; S.seq_ix = 0; S.resume = 0;
    S.alm_timeout = 0; S.alm_nofv = 0; S.alm_refused = 0;
    log('batch ' + S.number + ' aborted');
  };
  const advance = () => {
    if (SEQ[S.seq_ix] && SEQ[S.seq_ix].n === 9) { S.fv_brews += 1; }
    S.seq_ix += 1;
    if (S.seq_ix >= SEQ.length) {
      S.completed += 1; S.seq_ix = 0; S.hold_req = 0; S.resume = 0;
      to(COMPLETE, 0, 0);
      log('batch ' + S.number + ' complete');
    } else {
      to(RUNNING, PREP);
    }
  };

  // ---- the operator's command from the Batch view
  const op = num(get('cmd'));
  if (op) {
    await put('cmd', 0);
    await operator(op);
  }

  // ---- brews requested at the brewhouse panel
  if (P.requests !== null) {
    const pending = (P.requests - S.handled + 65536) % 65536;
    if (pending > T.max_pending) {
      // The panel's counter restarted (or ran far ahead); do not brew 64k batches.
      log('panel counter ' + P.requests + ' is far from the handled count ' + S.handled + '; re-synchronised');
      S.handled = P.requests;
    } else if (pending > 0) {
      S.queue = Math.min(S.queue + pending, T.max_queue);
      S.handled = P.requests;
      log(pending + ' brew(s) accepted from the panel, queue ' + S.queue);
    }
    if (P.accepted !== null && P.accepted !== S.handled) {
      await put('plc_accepted', S.handled);
    }
  }

  // ---- the batch: start the next brew, or take the next step of the one running
  if (S.st === RUNNING || S.st === HELD_ST) {
    if (P.ok) {
      await runStep();
    } else if (S.step !== HELD && Date.now() - G.lastOk > T.comms_s * 1000) {
      fault(W_LINK, 'alm_plc', S.step);                 // no link for comms_s: hold the batch
    }
  } else {
    await startIfQueued();
  }
  await publish();

  // ================================================================ operator
  async function operator(code) {
    const busyBatch = S.st === RUNNING || S.st === HELD_ST;
    if (code === 1) {                                   // Start: one more brew in the queue
      S.queue = Math.min(S.queue + 1, T.max_queue);
    } else if (code === 2 && S.st === RUNNING && S.step !== ABORTING && S.step !== ABORT_RESET) {
      S.hold_req = 1;                                   // taken at the next safe point
    } else if (code === 3 && S.st === HELD_ST && S.step === HELD) {
      restart();
    } else if (code === 4 && busyBatch && S.step !== ABORTING && S.step !== ABORT_RESET) {
      S.hold_req = 0;
      S.resume = 0;
      S.why = W_ABORTED;
      S.step = ABORTING; S.t_step = now;
    } else if (code === 5 && !busyBatch) {              // Reset: acknowledge and clear the queue
      S.queue = 0; S.why = 0; S.resume = 0; S.fv = 0; S.fv_brews = 0;
      clearAlarms();
      if (S.st !== IDLE) { to(IDLE, 0, 0); }
      if (P.state === P_COMPLETE || P.state === P_ABORTED) { await plcCmd(C_RESET); }
    }
  }

  function restart() {
    if (anyLock()) { S.why = W_ENTRY_HOLD; return; }    // not into an occupied vessel
    S.alm_timeout = 0; S.alm_plc = 0; S.why = 0;
    const r = S.resume;
    if (r === PREP || r === FVWAIT || r === RESET) {
      to(RUNNING, r);
      return;
    }
    switch (P.state) {
      case P_HOLDING: case P_HELD: case P_RESTARTING: S.step = RESTARTING; S.t_step = now; break;
      case P_RUNNING: to(RUNNING, RUN); break;
      case P_COMPLETE: to(RUNNING, RESET); break;
      case P_ABORTED: to(HELD_ST, ABORT_RESET); S.why = W_PLC_ABORT; break;
      default: to(RUNNING, PREP); break;                // Idle: the phase starts over
    }
  }

  // ================================================================ start
  function startRefusal() {
    if (S.st === ABORTED) { return W_NEEDS_RESET; }
    if (!P.ok) { return W_NO_LINK; }
    if (P.state !== P_IDLE) { return W_NOT_IDLE; }
    if (anyLock()) { return W_LOCKOUT; }
    return 0;
  }

  async function startIfQueued() {
    if (S.queue <= 0) {
      G.refuseT = 0; S.alm_refused = 0;
      if (S.st === IDLE && S.why >= W_NOT_IDLE) { S.why = 0; }
      return;
    }
    const why = startRefusal();
    if (why) {
      S.why = why;
      if (!G.refuseT) { G.refuseT = now; }
      if (now - G.refuseT >= T.refuse_grace_s) { S.alm_refused = 1; }
      return;
    }
    G.refuseT = 0; S.alm_refused = 0;
    S.queue -= 1;
    S.number += 1;
    S.seq_ix = 0; S.hold_req = 0; S.resume = 0;
    S.t_batch = now;
    to(RUNNING, PREP, 0);
    log('batch ' + S.number + ' starts, queue ' + S.queue);
  }

  // ================================================================ sequence
  async function runStep() {
    const ph = SEQ[S.seq_ix];
    if (!ph && S.step !== ABORTING && S.step !== ABORT_RESET) {
      fault(W_HANDSHAKE, 'alm_engine', PREP);
      return;
    }
    // A Hold request is taken where nothing is running, or by holding the PLC phase.
    if (S.hold_req && S.st === RUNNING) {
      if (S.step === PREP || S.step === FVWAIT || S.step === RESET) {
        S.hold_req = 0; hold(W_OPERATOR, S.step);
        return;
      }
      if (S.step === RUN && P.state === P_RUNNING) {
        S.hold_req = 0; S.resume = RUN;
        await plcCmd(C_HOLD);
        to(HELD_ST, HOLDING, W_OPERATOR);
        return;
      }
    }
    switch (S.step) {
      case PREP: await prep(ph); break;
      case FVWAIT: fvWait(); break;
      case START: await startPhase(ph); break;
      case RUN: await runPhase(ph); break;
      case RESET: await resetPhase(); break;
      case HOLDING: await holding(); break;
      case RESTARTING: await restarting(); break;
      case ABORTING: await aborting(); break;
      case ABORT_RESET: await abortReset(); break;
      default: break;                                   // HELD: wait for the operator
    }
  }

  async function prep(ph) {
    if (anyLock()) { hold(W_ENTRY_HOLD, PREP); return; }
    // [tag, value, how far a read-back may be off: the setpoints are scaled floats]
    const want = [
      ['plc_param', ph.param, 0], ['plc_phase', ph.n, 0], ['sp_hlt', ph.hlt, 0.15],
      ['sp_mlt', ph.mlt, 0.15], ['sp_ko', ph.ko, 0.15], ['plc_batch', S.number % 65536, 0],
    ];
    let ready = true;
    for (const [key, value, tolerance] of want) {
      const have = num(get(key));
      if (have === null || Math.abs(have - value) > tolerance) {
        ready = false;
        await put(key, value);
      }
    }
    if (ready) {
      S.t_phase = now;
      if (ph.n === 9) {
        to(RUNNING, FVWAIT);
        fvWait();                                       // so the wait and its reason show at once
      } else {
        to(RUNNING, START);
      }
    } else if (now - S.t_step > T.prep_s) {
      fault(W_HANDSHAKE, 'alm_plc', PREP);
    }
  }

  function fvWait() {
    if (anyLock()) { hold(W_ENTRY_HOLD, FVWAIT); return; }
    const n = chooseFv();
    if (n) {
      if (n !== S.fv) { S.fv = n; S.fv_brews = 0; S.t_fv = now; }
      S.alm_nofv = 0; S.why = 0;
      S.t_phase = now;
      to(RUNNING, START);
      log('fermenter FV-10' + n + ' reserved for batch ' + S.number);
    } else {
      S.why = W_NOFV;
      if (now - S.t_step >= T.nofv_alarm_s) { S.alm_nofv = 1; }
    }
  }

  // A fermenter plc-cellar reports empty (extract meter at 0) and lined up (not
  // in entry, CIP permitted), or the one this brew day already fills.
  function chooseFv() {
    const lined = (n) => flag(get('fv' + n + '_lock')) === false && flag(get('fv' + n + '_cip')) === true;
    const extract = (n) => num(get('fv' + n + '_extract'));
    const empty = (n) => extract(n) !== null && extract(n) < T.empty_plato;
    if (S.fv > 0 && lined(S.fv)) {
      const filling = S.fv_brews > 0 && S.fv_brews < T.brews_per_fv && S.t_fv > 0
        && now - S.t_fv < T.fv_reserve_s;
      if (empty(S.fv) || filling) { return S.fv; }
    }
    for (let n = 1; n <= CFG.fermenters; n += 1) {
      if (empty(n) && lined(n)) { return n; }
    }
    return 0;
  }

  async function startPhase(ph) {
    if (anyLock()) { hold(W_ENTRY_HOLD, PREP); return; }
    if (P.state === P_RUNNING || P.state === P_COMPLETE) { to(RUNNING, RUN); return; }
    if (P.state !== P_IDLE) { fault(W_HANDSHAKE, 'alm_plc', PREP); return; }
    await plcCmd(C_START);
    if (now - S.t_step > T.handshake_s) { fault(W_HANDSHAKE, 'alm_plc', PREP); }
  }

  async function runPhase(ph) {
    switch (P.state) {
      case P_COMPLETE:
        to(RUNNING, RESET);
        break;
      case P_RUNNING:
        if (now - S.t_phase > ph.timeout_s) {
          // Time out into an alarm and a held batch: the PLC phase goes to Held
          // (every phase output off) and the operator decides.
          log(ph.label + ' did not complete in ' + ph.timeout_s + ' s');
          S.alm_timeout = 1; S.resume = RUN;
          await plcCmd(C_HOLD);
          to(HELD_ST, HOLDING, W_TIMEOUT);
        }
        break;
      case P_HOLDING: case P_HELD:
        // The program held it itself (an entry lockout) or someone else did.
        hold(lockM || lockK ? W_ENTRY_HOLD : W_PLC_HELD, RUN);
        break;
      case P_ABORTED:
        S.alm_plc = 1; to(RUNNING, ABORT_RESET, W_PLC_ABORT);
        break;
      case P_IDLE:
        fault(W_PLC_LOST, 'alm_plc', PREP);
        break;
      default:
        break;                                          // Restarting
    }
  }

  async function resetPhase() {
    switch (P.state) {
      case P_IDLE:
        advance();
        break;
      case P_COMPLETE: case P_ABORTED:
        await plcCmd(C_RESET);
        if (now - S.t_step > T.handshake_s) { fault(W_HANDSHAKE, 'alm_plc', RESET); }
        break;
      default:
        fault(W_HANDSHAKE, 'alm_plc', RESET);
        break;
    }
  }

  async function holding() {
    switch (P.state) {
      case P_HELD: S.step = HELD; S.t_step = now; break;
      case P_COMPLETE: S.resume = RESET; S.step = HELD; S.t_step = now; break;
      case P_RUNNING: await plcCmd(C_HOLD); break;
      case P_ABORTED: S.resume = RUN; S.step = HELD; S.t_step = now; break;
      case P_IDLE: S.resume = PREP; S.step = HELD; S.t_step = now; break;
      default: break;
    }
    if (S.step === HOLDING && now - S.t_step > T.handshake_s) {
      S.alm_plc = 1; S.resume = RUN; S.step = HELD; S.t_step = now;
    }
  }

  async function restarting() {
    switch (P.state) {
      case P_RUNNING:
        S.t_phase = now; to(RUNNING, RUN, 0);
        break;
      case P_COMPLETE:
        to(RUNNING, RESET, 0);
        break;
      case P_HOLDING: case P_HELD:
        if (anyLock()) { hold(W_ENTRY_HOLD, RUN); return; }
        await plcCmd(C_RESTART);
        break;
      case P_ABORTED:
        S.alm_plc = 1; to(RUNNING, ABORT_RESET, W_PLC_ABORT);
        return;
      case P_IDLE:
        to(RUNNING, PREP, 0);
        return;
      default:
        break;
    }
    if (S.step === RESTARTING && now - S.t_step > T.handshake_s) {
      fault(W_HANDSHAKE, 'alm_plc', RUN);
    }
  }

  async function aborting() {
    switch (P.state) {
      case P_RUNNING: case P_HOLDING: case P_HELD: case P_RESTARTING:
        await plcCmd(C_ABORT);
        break;
      case P_COMPLETE: case P_ABORTED:
        S.step = ABORT_RESET; S.t_step = now;
        return;
      default:                                          // Idle: nothing to abort
        finishAbort();
        return;
    }
    if (now - S.t_step > T.handshake_s) { S.alm_plc = 1; finishAbort(); }
  }

  async function abortReset() {
    if (P.state === P_IDLE) { finishAbort(); return; }
    if (P.state === P_COMPLETE || P.state === P_ABORTED) { await plcCmd(C_RESET); }
    if (now - S.t_step > T.handshake_s) { S.alm_plc = 1; finishAbort(); }
  }

  // ================================================================ output
  async function publish() {
    const ph = (S.st === RUNNING || S.st === HELD_ST) ? SEQ[S.seq_ix] : null;
    const waiting = S.st === RUNNING && S.step === FVWAIT ? 1 : 0;
    const id = S.number > 0 ? CFG.recipe.code + '-' + String(S.number).padStart(4, '0') : '';
    // The state as the operator should read it: a hold or an abort is under way
    // until the PLC has confirmed it.
    let stateText = STATE_TEXT[S.st] || '?';
    if (S.step === HOLDING) { stateText = 'Holding'; }
    if (S.step === RESTARTING) { stateText = 'Restarting'; }
    if (S.step === ABORTING || S.step === ABORT_RESET) { stateText = 'Aborting'; }
    let status = stateText;
    if (ph) {
      status = 'Batch ' + id + ' ' + stateText.toLowerCase() + ': ' + ph.label
        + ' (' + (S.seq_ix + 1) + ' of ' + SEQ.length + ')';
      if (S.step === RUN && S.t_phase) {
        status += ', ' + Math.max(0, Math.round((now - S.t_phase) / 60)) + ' min in the phase';
      }
    } else if (S.st === COMPLETE) {
      status = 'Batch ' + id + ' complete';
    } else if (S.st === ABORTED) {
      status = 'Batch ' + id + ' aborted';
    }
    if (S.why && WHY_TEXT[S.why]) { status += ' - ' + WHY_TEXT[S.why]; }
    if (S.queue > 0) { status += ' [' + S.queue + ' queued]'; }
    if (!P.ok && P.stateRaw === null) { status += ' [no PLC link]'; }
    const iso = (s) => (s > 0 ? new Date(s * 1000).toISOString() : '');
    const out = {
      state_text: stateText, status, id, recipe: CFG.recipe.name,
      phase: ph ? ph.n : 0, phase_name: ph ? ph.label : '',
      plc_mirror: P.state === null ? -1 : P.state,
      wait: waiting, fv_name: S.fv > 0 ? 'FV-10' + S.fv : 'none',
      start: iso(S.t_batch), phase_start: S.t_phase && ph ? iso(S.t_phase) : '',
      beat: now,
    };
    for (const k of PERSIST) {
      if (S[k] !== S0[k]) { await put(k, S[k]); }
    }
    for (const k of Object.keys(out)) {
      if (get(k) !== out[k]) { await put(k, out[k]); }
    }
  }
}

// ==================================================================== inputs
function resolveIds() {
  const ids = {};
  const lost = [];
  for (const key of Object.keys(NAMES)) {
    const id = $getTagId(NAMES[key]);
    if (id === null || id === undefined) { lost.push(NAMES[key]); } else { ids[key] = id; }
  }
  if (lost.length) {
    if (!G.logged.lost) {
      G.logged.lost = true;
      log('tags missing from the project, the engine does nothing: ' + lost.join(', '));
    }
    return false;
  }
  G.ids = ids;
  return true;
}

function readPlc() {
  const P = {
    stateRaw: num(get('plc_state')), cmd: num(get('plc_cmd')), requests: num(get('requests')),
    accepted: num(get('plc_accepted')), lockM: flag(get('lock_mlt')), lockK: flag(get('lock_ktl')),
  };
  P.state = P.stateRaw;
  // The link is up while the brewhouse PLC answers with its state and command.
  P.ok = P.stateRaw !== null && P.cmd !== null;
  return P;
}
