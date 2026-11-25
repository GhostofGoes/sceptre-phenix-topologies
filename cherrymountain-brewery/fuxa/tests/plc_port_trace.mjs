// Run command traces through the JavaScript port of plc-brewhouse's handshake
// (brewhouse_sim.mjs) and print, for every scan, what test_plc_port.py compares with
// the compiled program: [state, step, XA01, XA02, command register].
//
// stdin: JSON {"scans": [{"cmd", "phase", "param", "lockM", "lockK", "sat", "temp"}, ...]}
// one entry per 100 ms scan; a field left out keeps its last value, except `cmd`,
// which the program clears and so counts for one scan only.
// stdout: JSON {"rows": [[state, step, xa01, xa02, cmd], ...]}
import fs from 'node:fs';
import { BrewhousePlc } from './brewhouse_sim.mjs';

const input = JSON.parse(fs.readFileSync(0, 'utf8'));
// The program's own run times: the mash rest and the whirlpool stand count minutes
// from the start (the sim's DURATIONS add a heat-up to the rest, to pace a brew day).
const plc = new BrewhousePlc({
  4: (p) => (p > 0 ? p : 60) * 60,
  8: (p) => (p > 0 ? p : 20) * 60,
});
plc.useConditions = true;
const rows = [];
for (const scan of input.scans) {
  plc.reg.cmd = scan.cmd ?? 0;
  if ('phase' in scan) { plc.reg.phase = scan.phase; }
  if ('param' in scan) { plc.reg.param = scan.param; }
  if ('lockM' in scan) { plc.lockM = scan.lockM; }
  if ('lockK' in scan) { plc.lockK = scan.lockK; }
  if ('sat' in scan) { plc.satisfied = new Set(scan.sat); }
  if ('temp' in scan) { plc.atTemperature = scan.temp; }
  plc.scan(0.1);
  rows.push([plc.reg.state, plc.reg.step, Number(plc.violation.mlt), Number(plc.violation.ktl), plc.reg.cmd]);
}
process.stdout.write(JSON.stringify({ rows }));
