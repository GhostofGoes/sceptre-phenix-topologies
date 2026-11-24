# CherryMountain Brewery PLC logic against the Top 20 Secure PLC Coding Practices

This document reviews the PLC programs of the range against the [Top 20 Secure PLC Coding
Practices](https://plc-security.com/content/Top_20_Secure_PLC_Coding_Practices_V1.0.pdf)
(version 1.0, the PLC Security project). A PLC (programmable logic controller) is an
industrial control computer. The range is a cyber range, a simulated network for security
exercises. An AI reviewer (a Claude agent) made the review on 2026-11-10. The reviewer
read the full text of the practices, compiled every program, and ran it. Nobody examined
its findings one by one. The exceptions are the findings that "Since the review" reports
as reproduced and fixed. The work to bring the programs into line with the practices is
future work
([FUTURE_WORK.md](FUTURE_WORK.md#plc-programs-that-follow-the-top-20-secure-plc-coding-practices)).

File and line references are to the tree at commit `d5520d1`, before the fixes
below. The command `git show d5520d1:<path>` shows a cited line.

## Since the review

The review looked for security practice. On the way, it found defects that have no
relation to an attacker. Those defects are now fixed. Each fix has tests that run the
compiled program, and the tests fail on the program as it was:

| Finding | What was wrong | Fix | Tests |
|---|---|---|---|
| F3 | plc-utilities: CIP (clean-in-place) washes vessels and pipes without disassembly. A CIP wash with no return flow stopped and restarted its pumps every 30 s without end. It never advanced, and it raised no alarm | The program latches the lost flow (`UTL.CIP.XA01.PV`, a new medium-priority alarm). To latch a value is to hold it until a reset. The pumps stay off until the operator commands Hold and Resume, or Abort | `sim/tests/test_plc_utilities.py` |
| F7 (part) | plc-utilities: the program cut a CIP circuit number above 7 to 7, so 8 selected the circuit of BBT-202 | A number above 7 selects no circuit (0), which has no route. The range of the tag is 0 to 7 | `sim/tests/test_plc_utilities.py` |
| F3 (part) | The description of `UTL.CIP.XS02.ROUTE_OK` was "route verified and return flow proven". The program never included the flow switch in the bit | The description now says what the bit is: the panel is on the circuit and the cellar permits it | None |
| F5 | plc-brewhouse: the program enabled the mash deviation alarm only at temperature. A mash that stayed cold (no steam) never alarmed, and its rest never counted | The program also enables the alarm after a mash step runs for 15 minutes, at any temperature. The rest still counts only at temperature | `sim/tests/test_plc_brewhouse_mash.py`. The whole-brew test makes sure that a normal brew does not raise the alarm |
| F6 (part) | plc-cellar: a scan is one run of the PLC program. One scan of 0 from the density meter of a fermenter made the vessel "empty". That put a crashed beer back to 66 degF and then through a second rest | A reading must hold for 60 s before it ends a stage (empty, or the end of the fermentation) | `sim/tests/test_plc_cellar.py` |

A second pass fixed the two things that the first pass left:

| Finding | What was wrong | Fix | Tests |
|---|---|---|---|
| Practices 4 and 6 (PKG-5) | plc-packaging: OEE (overall equipment effectiveness) measures how well the line runs. The "bottles this shift" count of the line, its rejects, and the shift OEE came from the lifetime counters of the machines. Those counters are 16 bits and never restart. After about four shifts, the counter of the filler wrapped, and the OEE read 100 % and then too low | The program adds up what each machine counted since the last scan, and it allows for a wrap. It starts the counts again when the line starts after a stop of four hours | `sim/tests/test_plc_packaging.py` |
| Practice 15 (UTL-8) | plc-utilities: before the first poll of the remote I/O, the pH reads 0. That reading ran the caustic pump for the first scans after a start | The program does not dose for the first 5 s. plc-packaging waits the same 5 s before it counts bottles | `sim/tests/test_plc_utilities.py`, `sim/tests/test_plc_packaging.py` |

One thing stays as it is. For one scan before the first poll, plc-cellar writes the
12 psig spunding setpoint to every fermenter. A setpoint is the target value of a
control loop. The cause is that an input of 0 reads as beer at terminal gravity. A
setpoint that lasts one scan moves no valve.

All other content of this document comes from the reviewer and is security practice.
It is the list for the future work.


The review covered these items:

- The five OpenPLC v4 programs of `cherrymountain-brewery/`: plc-brewhouse, plc-cellar (full/lite and ci), plc-packaging, plc-utilities, and plc-power.
- The generator in `plc/common/`.
- The OpenPLC configurations and image.
- The FUXA batch engine.
- The firewall rules that reach the PLCs.

## Status of this review

- Method tags:
  - `[V]` means that a run of the compiled program showed it.
  - `[C]` means that the reviewer read it in code or configuration. The citation gives the line.
  - `[I]` means that the reviewer inferred it from documentation or other files and did not run it.
- Nothing ran in an OpenPLC runtime. The repository says the same (`plc/common/README.md:30-31`). Runtime statements are `[C]` or `[I]`.
- Lite equals full. For all five PLCs, `plant/generated/lite/openplc/<plc>/located_vars.st` differs from full only in its header comment. `alarms.json` has the same 97 records. Findings for "full" apply to "lite". The ci variant (plc-cellar only) has a separate rating.
- File abbreviations:
  - BH: `plc/plc-brewhouse/plc-brewhouse.st`
  - CEL: `plc/plc-cellar/plc-cellar-plant.st`
  - CI: `plc/plc-cellar/plc-cellar.st`
  - PKG: `plc/plc-packaging/plc-packaging.st`
  - UTL: `plc/plc-utilities/plc-utilities.st`
  - PWR: `plc/plc-power/plc-power.st`
  - LV: `plant/generated/full/openplc/<plc>/located_vars.st`
  - DESIGN: `DESIGN.md`
- Test log names (BH-1, CEL-4, UTL-1 and so on) refer to sections in `logs/*.log`.

---

# 1. Summary

## 1.1 Ratings

Legend: Met, Partly, Not met, N/A (the practice has nothing to apply to), Deliberate (recorded decision, section 3).

| # | Practice | BH | CEL | CI | PKG | UTL | PWR |
|---|---|---|---|---|---|---|---|
| 1 | Modularize PLC code | Partly | Partly | Partly | Not met | Not met | Not met |
| 2 | Track operating modes | Partly | Not met | Not met | Not met | Not met | Not met |
| 3 | Leave operational logic in the PLC | Partly | Met | Met | Met | Met | Met |
| 4 | PLC flags as integrity checks | Not met | Not met | Not met | Not met | Not met | Not met |
| 5 | Integrity checks for PLC code | Partly | Partly | Partly | Partly | Partly | Partly |
| 6 | Validate timers and counters | Partly | Partly | N/A | Met | Met | Met |
| 7 | Validate and alert on paired I/O | Not met | Not met | Not met | Partly | Partly | Not met |
| 8 | Validate HMI inputs in the PLC | Partly | Met | Met | Met | Partly | Met |
| 9 | Validate indirections | Partly | Met | N/A | N/A | Met | N/A |
| 10 | Designated register blocks | Partly | Not met | Not met | Not met | Not met | Not met |
| 11 | Instrument for plausibility checks | Partly | Not met | Not met | Not met | Not met | Partly |
| 12 | Validate inputs by physical plausibility | Partly | Partly | Partly | Partly | Partly | Partly |
| 13 | Disable unneeded ports and protocols | Partly (+Deliberate) | Partly | Partly | Partly | Partly | Partly |
| 14 | Restrict third-party data interfaces | Partly (+Deliberate) | Partly | Partly | Partly | Partly | Partly |
| 15 | Safe process state after a restart | Partly | Partly | Partly | Partly | Partly | Partly |
| 16 | Cycle times, trended on the HMI | Not met | Not met | Not met | Not met | Not met | Not met |
| 17 | Uptime, trended on the HMI | Not met | Not met | Not met | Not met | Not met | Not met |
| 18 | Hard stops, trended on the HMI | Not met | Not met | Not met | Not met | Not met | Not met |
| 19 | Memory use, trended on the HMI | Not met | Not met | Not met | Not met | Not met | Not met |
| 20 | Trap false negatives and positives | Partly | Partly | Not met | Not met | Not met | Not met |

The table hides two things.

- Most "Partly" and "Not met" entries come from the design of the generator and the programs, not from one program. `plc/common/plcbuild.py` makes sure that each program assigns its outputs and does not assign alarm bits. To clamp a value is to limit it to a range. The generator does not make sure that a program clamps a host-writable tag, protects a latch, or uses an input (`plcbuild.py:187-207`).
- The strong points are real (section 1.3). The programs stop at the operational layer and have no security layer.

## 1.2 The findings that matter most

I judged severity for a cyber range. "Recorded" means that DESIGN.md already says it. Each finding below is something that DESIGN.md does not say, or something that DESIGN.md contradicts. Section 3 lists the recorded items.

### F1. A PLC restart silently removes the confined-space lockout and the CO2 shutoff latch, and one write clears them (High)

- What is wrong: These are plain non-retained coils. A coil is one writable bit in Modbus. The program reads them back as state:
  - The six cellar entry permits (`CEL_ENTRY_HS01..06_ENTRY`, `%QX4.3` to `%QX5.0`, LV cellar:119-124).
  - The two brewhouse permits (`%QX1.1`, `%QX1.2`, LV brewhouse:75-76).
  - The CO2 supply latch (`UTL_CO2_XS02_SHUTOFF`, `%QX1.1`, LV utilities:47).
  - The Critical entry-violation latches (`XAnn_PV`, for example `%QX6.5`).

  A restart resets all of them to 0. Any Modbus client can write any of them.
- Where: CEL:310-336 and 405-436. BH:124-130 and 326-345. UTL:104-117. All six compiled programs have `retain_var_count = 0` (`build/*/gen/generated_debug.cpp`).
- How it hurts: The PDF calls forcing a restart "the most basic attack vector for a PLC" (practices 15, 17).
  - After a restart `[V]` (CEL-4): A permit set before the restart reads 0, and `LOCKED` is 0. On the first scans, the program commands the glycol valve of the entered fermenter again.
  - After a restart `[V]` (UTL-2): The CO2 latch is gone. With the relay healthy, the valve command returns with no operator reset and no 60 s healthy period.
  - Without a restart `[V]` (CEL-4, UTL-2):
    - A write of 0 to `%QX1.1` on plc-utilities bypasses the CO2 latch. The valve command goes from 0 to 1 at once.
    - A write of 0 to an `XAnn_PV` coil erases a latched Critical alarm while the vessel is still locked.
  - The brewhouse node is read-only over OPC UA (`phenix_apps/apps/cherrymountain/render.py:198-211` in sceptre-phenix-apps). BH-10 shows the erasure only on the raw image.
  - Network detection does not cover these writes. `phenix-injects/malcolm/zeek/cherrymountain.zeek:135-144` watches writes to the CO2 reset coil and the permits, not the latch coils.
- Not recorded:
  - DESIGN.md:4404 accepts that "a stopped PLC, or a write straight to the RIO, bypasses" the lockout. That case is different from "a restart forgets the permit" and "a latch lives in a writable coil".
  - DESIGN.md:598-600 records the restart effect for the fermentation profile only.
- Confidence:
  - `[V]` for what the programs do.
  - `[I]` that the servers accept those writes (OpenPLC `core/src/drivers/README.md`, Modbus slave: function codes 05, 06, 0F, 10).
- Smallest fix:
  - (a) Keep each latch in an unlocated internal variable and publish a copy each scan. The program then overwrites an outside write. The prototype is in `fixes.py` (`shutoff_l`). I made sure that it works (`logs/fixes.log` FIX-1b).
  - (b) Declare permits and latches `VAR RETAIN`.
    - The pinned strucpp accepts `RETAIN` on a located variable and emits a retain table (`retain-probe/out/generated_debug.cpp`: `LEAF_FLAG_RETAIN`, `retain_var_count = 2`).
    - The OpenPLC v4 documentation describes a built-in retain store. I did not make sure that the runtime persists a located `%QX` across a restart.
    - Fallback: FUXA re-asserts the permit state after a PLC restart.
  - (c) Add plc-utilities coil 9 and the `XAnn_PV` coils to the Zeek watched-writes table.

### F2. A stopped or crashed PLC probably drives its outputs to 0, the opposite of what DESIGN.md says (High if confirmed) `[I]`

- What is wrong:
  - DESIGN.md:824 says "plc-cellar stopped: Its outputs stop updating; rio-cellar keeps the last commands."
  - The OpenPLC v4 documentation at the pinned commit says otherwise (`docs/ARCHITECTURE.md`, "Watchdog System", layers 2 and 3):
    - "On every stop ... all `%Q` outputs are written to 0 and pushed by a final I/O cycle."
    - After a watchdog fault, the runtime reboots with `--safe-mode --fault`, which "does not load the program" and drives outputs to 0.
- How it hurts: If the documentation is right, a stop pushes 0 through `modbus_master` to every RIO (remote I/O unit) and device. A stop can come from REST `stop-plc` on the EWS (engineering workstation) or from a watchdog fault.
  - For plc-power, this includes `PWR_MAIN_HS01_CMD`, described as "52-M close command (0 opens)" (LV power:17). PWR:146 drives it TRUE every scan. If the documentation is right, a stop of the power PLC opens the site main breaker.
  - The chiller, glycol pump, boiler, air and RO enables (UTL:92-101, 118) also drop. The PV (solar) and BESS (battery) enables (PWR:213-217) drop too.
  - After a fault, the safe-mode boot does not reload the program. The plant stays in that state until someone starts it.
- Why I am not sure: I did not run the runtime. It is possible that the documentation describes intended behavior. It is possible that the ot-sim RIO treats a written 0 differently from hardware. DESIGN.md:4398 and 4417 record that the first VM run is still open.
- Smallest fix: One VM run. Stop plc-power and read the breaker.
  - If the run shows the behavior, change the polarity of anything that must keep running. Use a "trip" or "inhibit" output whose 0 is the safe state, or configure the RIO to hold on master loss.
  - Correct DESIGN.md:824 and §7.3 in both cases.

### F3. The CIP "return flow not proven" hold releases itself, so a wash with no return flow stalls forever with no alarm (Medium-High) `[V]`

- What is wrong:
  - UTL:179-180 holds the wash when `UTL_CIP_HS02_CMD AND cip_timer.ET > T#30s AND NOT UTL_CIP_FS01_PV`.
  - The hold turns the return pump command off (UTL:192). That makes the term false on the next scan, and the false term releases the hold.
  - The step timer resets on every hold (UTL:186).
- Result `[V]` (UTL-1):
  - With the flow switch never made, the return pump command dropped 13 times in 400 s (first at 29.9 s, then every 30.3 s).
  - The step stayed at 1, the route stayed OK, and the program raised no CIP alarm.
  - With the flow switch made, the step advances after its 300 s.
  - LV utilities:89 describes `UTL_CIP_XS02_ROUTE_OK` as "route verified and return flow proven", but UTL:163 does not include the flow switch.
- Why nobody saw it: plc-utilities has no compiled test. Its only tests are regular-expression checks (`tools/netcheck/tests/test_plcbuild.py:123-136`).
- Smallest fix (I made a prototype and made sure that it works, FIX-1a):
  - A separate 30 s timer on `HS02_CMD AND NOT FS01`.
  - A latched `flow_lost` flag that the program clears when the step is 0 or done.
  - `cip_hold := ... OR flow_lost`.

  After one drop, the pumps stay off until the operator aborts. Add an alarm bit for the latched state.

### F4. Brewhouse phases that wait on a process value have no PLC timeout, and the timeouts and knockout permissive live in the FUXA script (Medium) `[V]`

- What is wrong:
  - Phases 2, 3, 6, 7 and 9 complete only when a process value reaches a threshold (BH:281, 286, 303, 306, 321). The PLC has no step timer or alarm for them.
  - The only timeout is `ph.timeout_s` in `fuxa/batch_engine.js:393-401`, which sends a Hold.
  - The same script owns the start refusal (`batch_engine.js:254-261`) and the knockout destination reservation (`batch_engine.js:347-377`).
  - The PDF names exactly this as code to keep out of the HMI (the operator interface): "interlocks, timers, holds or permissives" (practice 3).
- Results `[V]` (`logs/brewhouse.log`):
  - BH-6: MASH_IN with the MLT level stuck at 0 for 2 h stays Running, with the strike valve and rake on.
  - BH-9: PHASE 9 with CMD 1 on a cold PLC sets the wort pump, knockout valve and pump speed (700) at once. The program reads nothing from plc-cellar.
  - BH-5: With the HLT level transmitter stuck at 0 for 1 h, the HLT fill valve stays open. The HLT has no high-level switch in `located_vars.st`, and the only alarm is the low-priority "low level".
- How it hurts: If FUXA is down, or an attacker writes `XC01_PHASE` and `XC01_CMD` directly, the PLC applies no destination check and no timeout. Both tags are HMI-writable by design, and DESIGN.md:4422 records the anonymous OPC UA write. The knockout moves product toward a vessel that is possibly under confined-space entry.
- Partly recorded: DESIGN.md:768 states the FUXA-side reservation as the design. DESIGN.md:4417 flags the timeouts as estimates. Neither says that the PLC has no timeout of its own.
- Smallest fix:
  - Add a PLC timer per waiting phase (constants next to BH:68-72) that Holds the phase and raises a state alarm.
  - Do one of these two things. Mirror the "lined up" bit of the cellar to the brewhouse, which is the peer path that DESIGN.md:4403 already asks for. Or make KNOCKOUT refuse to start until FUXA sets its reservation bit in the PLC.

### F5. The mash deviation alarm cannot fire when the mash is too cold, and a mash that loses steam stalls without an alarm (Medium) `[V]`

- What is wrong:
  - `BH_MLT_XS01_ACTIVE := PV + 2.0 >= SP` (BH:291) is the suppression condition of `BH.MLT.TT01.ALM_DEVIATION`. See `alarms.json` (`suppress: BH.MLT.XS01.ACTIVE`), `plcbuild.py:154-156`, `cm_alarm.st:37-42`.
  - With the PV (process value) more than 2.0 degF below setpoint, ACTIVE is 0. The program then suppresses the alarm, and the alarm timers reset.
  - The same bit gates the rest timer (BH:292), so the rest never counts either.
- Result `[V]` (BH-7):
  - 2 h at SP minus 5 degF: state Running, `STEP_MS` 0, ACTIVE 0, alarm 0.
  - At SP plus 3 degF, the alarm raises after 600 s.
  - At SP minus 3 degF for 1,200 s, it does not.
- Smallest fix (I made sure that it works, FIX-2):
  - A 15 minute ramp-allowance timer enables the alarm even when the PV is under setpoint.
  - The rest clock still counts only at temperature.
  - The alarm then raises at 25 minutes (15 plus the 10 minute on-delay) and stays suppressed during the ramp.

### F6. No program checks an input for plausibility, and about 90 instrumented inputs are never read (Medium) `[V]` `[C]`

- What is wrong:
  - Every control decision uses one sensor value directly. No program tests range, rate of change, stuck value, or agreement between instruments.
  - These are the counts of located inputs that no statement of the assembled program reads (alarm logic included): BH 12, CEL 11, CI 9, PKG 26, UTL 17, PWR 15. See `logs/cellar.log` CEL-8, `logs/packaging_utilities.log` PKG-6 and UTL-9, `logs/power_ci.log` PWR-4, `logs/misc.log`.
  - Unread second instruments include:
    - Spunding valve open `ZSO01` (four inputs).
    - Hop port closed `ZSC01`.
    - Carbonation stone flow `FT01`.
    - BBT-201 level.
    - Mill and rake running feedback.
    - Kettle high-level switch `LSH01`.
    - Neutralization tank high-level switch.
    - CO2 valve limit switch `ZSO01`.
    - Every feeder, BESS and PV connection status in plc-power.
- Results `[V]`:
  - Cellar (CEL-3, CEL:147-169):
    - One scan of `DT01 = 0` sends the setpoint of a crashed fermenter to 66 degF.
    - During a diacetyl rest, it restarts the rest timer (`rest_ms` 100200 to 100).
    - Mid-crash above 60 degF, it sends the beer back to the 70 degF rest.
    - DESIGN.md:598-600 records this for a PLC restart only.
  - Brewhouse (BH-5, BH-8):
    - The HLT temperature transmitter reads 0 for 1 h: steam valve 100%, no alarm.
    - Barometric `PT01 = 0`: the program never detects the boil, the phase is stuck, and there is no alarm.
    - With a healthy `PT01`, one temperature reading 0.3 degF under the computed boiling point completes a 60 minute boil.
  - Packaging (PKG-3, PKG:157-162): The keg wash heater stays on indefinitely when `TT01` reads 0, and the keg washer has no temperature alarm.
  - Utilities:
    - UTL-5 (UTL:121-134): A pH probe stuck at 3.00 runs the caustic pump for 3 h. With the probe stuck at 12.00, the acid pump runs. There is no maximum dose time.
    - UTL-2: The program never compares the CO2 valve limit switch with the command. There is no alarm for "commanded open, not open" or "commanded closed, still open".
  - Power (PWR-4): The program commands the boiler feeder closed, the position input reads open for 700 s, and there is no alarm.
- Smallest fix:
  - Add the agreement checks where the instrument pair already exists. Compare each `ZSO01`, `ZSC01` or `YS01` with its command, with a travel-time timer and a state alarm.
  - Compare the level switch with the analog level.
  - Add a maximum dose time on the neutralization pumps.
  - Add a debounce of a few scans on `DT01 < EMPTY` in `FermentProfile`.
  - A generated range check per analog input in `plcbuild.py`, with limits from `plant.yaml` `range`, makes the out-of-range case uniform.

### F7. Writable values are bounded to instrument ranges, not process ranges, and out-of-range writes are clamped silently (Medium) `[V]`

- Results `[V]`:
  - `BH.BATCH.XC03.PARAM` for MILL: 65535 gives a 36.4 hour mill run (131,070,000 ms, BH:170-171). The program caps minute parameters at 600, upper bound only (BH:75, 163).
  - The HLT setpoint accepts up to 205.0 degF (BH:118). Its high alarm is 190.0 degF, and the boil model of the program gives 202.7 degF at 12.1 psia (BH-8). A legal setpoint is above the alarm and above boiling.
  - Fermenter and bright-tank setpoints accept 20.0 degF (CEL:252-261, CI:145-152).
    - With the PV following, there is no alarm. The deviation alarm tracks the setpoint, and there is no cold alarm (CEL-2).
    - `PROFILE.KC01 = 0` skips the diacetyl rest (MISC-1).
    - The glycol pressure setpoint accepts 100.0 psig (UTL:87).
    - The demand cap accepts 600 kW, which switches off BESS cap discharge for a site importing 400 kW (PWR-1).
  - The program saturates `UTL_CIP_XC01_CIRCUIT` and does not reject it: 8 and 9999 become 7, a real circuit (UTL:89, UTL-3).
  - Out-of-range writes leave no flag, counter or alarm anywhere.
- Smallest fix:
  - Generate the clamp from `plant.yaml` `range` for every `*-read-write` tag in `plcbuild.py`. If a clamp is missing, fail the build. The generator already has the tag list (`plcbuild.py:77-92`).
  - Use process ranges in `plant.yaml`: HLT at most 200 degF, fermenters about 28 to 80 degF, glycol pressure about 40 psig. These are my suggestions, not data-sheet values.
  - Cap `XC03.PARAM` at the real grain bill.
  - For a selector, reject and count. Do not saturate.

### F8. Critical and high alarms can be missed, and nothing traps a falsified alarm bit (Medium) `[C]` `[V]`

- What is wrong:
  - All 79 PLC alarms have `off_delay_s = 0`, and 22 have `delay_s = 0` (`plant/generated/full/alarms.json`).
  - FUXA polls every PLC once a second (`"polling": 1000` per PLC device in `fuxa/full/scada-fuxa-project.json`) and binds each alarm to the bit with `timedelay` 0.
  - Twelve High or Critical alarms have zero on-delay and no latch in the program:
    - The six PSV-lifted alarms.
    - `UTL.CHL.YA01`, `UTL.STM.LSL01`, `PWR.MAIN.YA01`, `PWR.GEN.ZS01`.
    - `PWR.BESS.TT01.ALM_HIGHHIGH`.
    - The Critical `PWR.BESS.AT01.ALM_HIGH` (BESS off-gas).

    A condition shorter than the poll can come and go between reads `[I]`.
  - Deviation alarms use the host-writable setpoint as reference (`plcbuild.py:142-147`), so a legal setpoint change moves the alarm with it (CEL-2).
  - The ci program has no deadband or off-delay (CI-2 `[V]`):
    - A PV that swings between 2.01 and 1.99 degF off setpoint every minute for an hour never raises the 900 s deviation alarm.
    - The full program raises it at 15 minutes.
    - DESIGN.md:755 records the limitation. Here I made sure that the effect occurs.
  - The only trap in any program is the entry-violation check (BH:124-130, CEL:310-321). Nothing compares an alarm bit with its own condition.
  - A raw write to an alarm bit shows for one scan (CEL-7).
- Smallest fix:
  - Set `off_delay` of at least 2 s on every alarm. This is one line in `plant.yaml`, and the generator and `CmAlarm` already support it.
  - Latch the Critical and High state alarms.
  - In `plcbuild.alarm_call`, before each `alm_n(...)` call, add `IF {bit} <> alm_n.ALM THEN tamper := tamper + 1;` and publish `tamper`. The bit at the top of a scan must equal what the block computed last scan, so a difference is an outside write. That is the practice 20 trap, one generated line per alarm.

### F9. Nothing monitors the PLCs themselves: no cycle time, uptime, restart, stop or memory data reaches the HMI (Medium) `[C]`

- What is wrong:
  - Practices 16 to 19 have no implementation in any program or in the FUXA project.
  - OpenPLC v4 tracks scan count, scan time, cycle time, latency and overruns, but only writes them to its log every 5 s (`docs/ARCHITECTURE.md`, "Performance Monitoring").
  - `/api/status` returns one word (EMPTY, INIT, RUNNING, STOPPED, ERROR). No program, Modbus client or OPC UA client can read any of this.
  - The repository reads the status word only in the phenix state-of-health probe (`phenix_apps/apps/cherrymountain/soh.py:77-84, 190-196` in sceptre-phenix-apps), which is not an operator view.
  - A restart reaches FUXA only as the batch engine message "the brewhouse PLC lost the phase (restarted?)" while a batch runs (`batch_engine.js:409-411`, text at 60).
  - Timed steps count scans, not time (BH:346-348, CEL:167). A cycle overrun silently stretches a mash rest or diacetyl rest. Cycle-time trending exists for that case.
- Smallest fix:
  - Add a `SYS` block to `plant.yaml`. Have `plcbuild.py` add one fragment to every PLC: a scan counter (heartbeat), seconds since start, and a retained restart counter. FUXA trends them and alarms on a stall or a reset. This also gives the PLC-side half of the open RIO heartbeat (DESIGN.md:4402).
  - For practice 5, add a state-of-health test that reads the MD5 of the loaded program over the debug interface (`DEBUG_GET_MD5`, OpenPLC `docs/DEBUG_PROTOCOL.md`). The test compares the MD5 with `defines.h`.

## 1.3 What is in good shape

- One alarm block: The generator creates `CmAlarm` (`plc/common/cm_alarm.st:14-43`) per alarm from `alarms.json` (`plcbuild.py:130-165`), so the PLC and FUXA cannot drift. FUXA alarms watch the PLC bit, not a threshold of their own. Hand-written code cannot assign alarm bits (`plcbuild.py:194-196`).
- Range-limited setpoints: The PLC limits the range of every host-writable setpoint, to the range of the HMI or narrower (49 read-write tags covered, exceptions in F7). The programs apply clamps in place before use (BH:118-120, CEL:252-267, PKG:69-70, UTL:86-89, PWR:126-127).
- Deliberate restart behavior:
  - Brewhouse outputs default off each scan (BH:251-270).
  - The cellar CO2 inhibit starts TRUE and releases 60 s after the relay is healthy (CEL:340-341). I made sure of this: 1 at 30 s, 0 at 61 s.
  - The profile re-derives the stage of each fermenter (CEL:147-157).
  - A lost cellar peer reads 0, so CIP fails safe (`modbus_master.json` `set-to-zero`, `tools/plantgen/plantgen/render.py:820`). I made sure of this in UTL-7.
- The clock trust of plc-power (PWR:152-180) is a real plausibility check: range, step limit, stale and jump detection, re-baselining. It is the best example in the repository of practices 11 and 12.
- CIP start logic drops a refused start (UTL:165-176). Both CASE statements have defaults (UTL:146, 158).
- Guarded divisions and counters: The programs guard division sites (BH:238, PKG:136, 141). They guard counters against underflow (PKG:84-85, PWR:141-145).
- Minimal plugins: Each committed ZIP carries only the plugins that its PLC needs.
- Firewall: Each PLC is reachable only on its own protocol port, from SCADA and the EWS only. `netcheck` makes sure of this (`tools/netcheck/netcheck/conduits.py:276-352, 489-542`).

---

# 2. Practice by practice

## Practice 1. Modularize PLC code

Asks: split into function blocks, monitor execution time and size, test modules independently.

- All: Alarm logic is one reusable block, `CmAlarm` (`cm_alarm.st:14-43`), with one instance per alarm (`plcbuild.py:130-165, 257-267`). Nothing monitors execution time or size.
- BH: Partly. The program has one function, `PValve` (BH:54-61), and uses it four times. The PROGRAM has about 290 lines, with the ten phases inline in one CASE (BH:273-324).
- CEL: Partly.
  - Four fermenters and two bright tanks reuse three function blocks (`GlycolZone` L57, `Carbonation` L74, `FermentProfile` L127).
  - The program copies the entry lockout (L310-336, 405-436) and spunding setpoints (L344-382) per vessel.
- CI: Partly.
  - The program reuses `ZoneControl` (L23) and `PressureAlarm` (L64).
  - `ZoneControl` mixes control and alarm.
  - The alarm limits are hard-coded (L91-100). Only `plc/plc-cellar/assemble.py:85-115` compares them with `alarms.json`.
- PKG, UTL, PWR: Not met for hand-written logic. Each has one PROGRAM (PKG:37-165, UTL:36-203, PWR:51-257) and no function block. The clock-trust section of PWR (L152-180) and its BESS dispatch (L217-248) are obvious blocks.
- Tests:
  - Compiled tests exist for BH (`sim/tests/test_plc_brewhouse.py`, `test_brewhouse_control_path.py`), CEL (`test_plc_cellar.py`), PKG (`test_plc_packaging.py`) and PWR (`test_plc_power.py`). They drive whole programs, not modules.
  - UTL has none, only regular-expression checks (`tools/netcheck/tests/test_plcbuild.py:123-136`). F3 is in the untested program.

## Practice 2. Track operating modes

Asks: keep the PLC in RUN, alarm operators when it is not, re-alert at shift end.

- Platform:
  - OpenPLC v4 has no RUN/PROG switch.
  - State (EMPTY, INIT, RUNNING, STOPPED, ERROR) comes from `GET /api/status`.
  - Upload, start and stop need only the admin JWT (OpenPLC `docs/API.md`).
  - That path is a recorded EWS conduit (DESIGN.md:2098).
- Nothing tells the HMI:
  - The FUXA alarm list is the PLC alarm bits plus five `BATCH.*` alarms. There is no alarm for a stopped, errored or unreachable PLC.
  - The `openplc-program-running` probe (`soh.py:77-84`) is phenix infrastructure, not an operator alarm.
- BH: Partly. `BATCH.ALM_PLC` fires when the link is down for `comms_s` (`batch_engine.js:205-207`) or a running phase reads Idle (`409-411`), but only while a batch runs.
- CEL, CI, PKG, UTL, PWR: Not met. The PackML "mode" of PKG (L70, 96-101) is a process mode.

## Practice 3. Leave operational logic in the PLC wherever feasible

Asks: totalizing, interlocks, timers, holds, permissives and alarm thresholds in the PLC.

- All: Met for alarms. The generator puts thresholds, delays and suppression into the PLC. Each FUXA alarm binds to the PLC bit (`fuxa/full/scada-fuxa-project.json`: `variableId` = the ALM tag, min = max = 1).
- CEL, CI, PKG, UTL, PWR: Met. Interlocks and sequences are in the PLC:
  - CO2 inhibit (CEL:337-341), dry-hop (CEL:443-466), profile (CEL:147-179, 270-301).
  - Entry lockout, OEE (PKG:135-147), CIP (UTL:136-200).
  - The whole power schedule, from the ATS clock (PWR:152-248).
- BH: Partly. The PLC runs one phase per Start. Sequence, phase timeouts, the "no fermenter empty and lined up" start refusal and the knockout destination reservation are in the FUXA script (`batch_engine.js:254-261, 347-377, 387-401`). See F4. `[V]` BH-6, BH-9.

## Practice 4. Use PLC flags as integrity checks

Asks: counters on error flags (divide by zero, counter overflow, negative preset, scan overrun).

- All: Not met.
  - No program counts an error condition.
  - No program has an array or pointer.
  - The programs guard divisions, so the divisions cannot fault: BH:238 (`p_kpa > 10.0`), PKG:136 and 141, constants elsewhere.
  - A tripped guard takes a silent fallback. PKG:141-146 sets quality to 1.0 when a wrap puts the filler counter below the reject count (PKG-5: OEE 900 becomes 1000).
- Platform: strucpp documents no math error flags (`docs/IEC_COMPLIANCE.md`, `docs/RUNTIME.md` in the strucpp checkout). The only way to meet the practice is to count guard hits and out-of-range writes.
- Examined, no defect: I tested a suspected integer wrap in `BH_KO_FT01_PV * 2 / 5` (BH:320) for inputs up to 65535 and found none (BH-11).

## Practice 5. Cryptographic or checksum integrity checks for PLC code

Asks: hash or checksum of the code, alarm when it changes.

- Build time: Met.
  - `defines.h` carries the MD5 of the assembled program (`plc/common/pack_zip.py:66`).
  - `sync.py:359-480` and `build_zip.sh --check` catch a stale ZIP.
  - `render.py:360-363` makes sure that the OPC UA debug map agrees with it.
  - I examined all six committed ZIPs. I made sure that each `PROGRAM_MD5` equals the MD5 of what I assembled from the current sources.
- Run time: Not met.
  - Nothing reads the hash of the loaded program. OpenPLC offers it (`DEBUG_GET_MD5`, JWT required), but the probe only greps for `STATUS:RUNNING` (`soh.py:84`).
  - OpenPLC itself states that the stored project has "no integrity guarantee" (`docs/SECURITY.md`).
- Network side: The range detects uploads from a non-EWS host or an upload-sized session (Suricata 9000150/9000151, Zeek `PLC_Program_Upload_Suspected`, DESIGN.md:3013-3027). That shows that an upload occurred, not that the running code differs from the reference.
- All six: Partly.

## Practice 6. Validate timers and counters

Asks: make sure that presets written from outside are valid, no direct HMI write to a timer, look for backward counts.

- BH: Partly.
  - The only host-writable preset is `XC03.PARAM`.
  - The program caps minutes at 600 (BH:75, 163, 175, 181, 186). It does not cap MILL kilograms: 65535 kg gives `limit_ms` 131,070,000 = 36.4 h (BH:170-171, BH-2).
  - The program latches presets at Start (BH:161).
- CEL: Partly. The program caps `PROFILE.KC01` at 6000 (L267) with no lower bound: 0 skips the rest (MISC-1). Other presets are constants (L193-197).
- CI: N/A. No timer or counter is host-writable (CI:93-100).
- PKG, UTL, PWR: Met. No preset is host-writable (PKG:47, UTL:53, PWR:55-74). The programs guard counters against underflow (PKG:84-85, 141-142, and PWR:141-145, 171).
- Machine counters wrap at 65535 (LV packaging:12-13, 20-21). BH handles the wrap of the panel counter in the engine (`batch_engine.js:186`). PKG handles it only partly (PKG-5).

## Practice 7. Validate and alert for paired inputs and outputs

Asks: paired signals never together, alarm on infeasible I/O states, independent start/stop, restart delays, no rapid toggling.

- BH: Not met.
  - The program never compares mill `HS01_CMD` with `YS01_PV`, or rake `HS01_CMD` with `YS01_PV`. Nothing references the feedbacks (`logs/misc.log`).
  - The program never compares the runoff and sparge valves with their flow transmitters.
  - No output has a toggle delay.
- CEL: Not met.
  - The program never compares `FCV01_OUT` with `FT01_PV`, the spunding setpoint with `ZSO01_PV`, or the dry-hop valves with `ZSC01_PV` (CEL-8).
  - The dry-hop purge and dose open with no hop-port-closed check (CEL:462-465).
  - The transfer pump command is hard-coded FALSE (CEL:469).
- CI: Not met. The program does not read the four `ZSO01` inputs.
- PKG: Partly.
  - A conveyor runs only while its machine executes and its jam eye is clear (PKG:150-153).
  - The line command has no anti-toggle. When a host alternates Start and Stop, the program passes every change to every machine each scan: `[2, 3, 2, 3, 2, 3, 2, 3]` (PKG-1, PKG:81-95). Only the PackML state machines of the machines limit it.
- UTL: Partly.
  - Caustic and acid pumps are never on together. The pH hysteresis cannot overlap, and a one-scan jump from 6.40 to 8.60 gives caustic off, acid on (UTL-5).
  - CIP caustic and acid steps are exclusive by step number (UTL:193-194).
  - The one check of a command against its feedback, return pump against flow switch, is broken (F3).
  - The program never compares the CO2 valve command with `ZSO01` (F6).
- PWR: Not met.
  - The BESS has one signed setpoint, so the program cannot assert charge and discharge together (PWR:244-248). That is the single-output design that the PDF prefers.
  - There is no check of command against position on feeders, 52-M, genset start or BESS enable. The program never reads `PWR_MAIN_ZS02..04` or `PWR_BESS_ZS01_PV` (PWR-4).
  - `PWR_BESS_JC01_SP` has no slew limit or dwell.

## Practice 8. Validate HMI input variables at the PLC level

Asks: the PLC compares every HMI-written value with its acceptable range. The PLC then either substitutes a default or last good value, or alerts.

The table lists all 49 tags with `scada_access = read-write` (`plant/generated/full/registers.csv`):

| Tag(s) | HMI range | PLC check | Result |
|---|---|---|---|
| BH `HLT.TT01.SP`, `MLT.TT01.SP`, `KO.TT02.SP` | 32-212, 32-212, 32-100 degF | `LIMIT` BH:118-120 to 32-205, 32-180, 32-100 | Clamps `[V]` BH-1. HLT max 205 exceeds the alarm limit 190 (F7) |
| BH `XC01.PHASE` | 0-10 | Start accepts 1-10 only (BH:158-159), latched | The program ignores out-of-range values silently `[V]` BH-3 |
| BH `XC01.CMD` | 0-5 | `CASE` with no `ELSE`. The program clears the register (BH:150, 156-214) | The program drops other values silently `[V]` BH-3 |
| BH `XC03.PARAM` | 0-65535 | Minutes `MIN(,600)` BH:163. MILL kg unbounded BH:170 | 36.4 h mill `[V]` BH-2 |
| BH `XI01.BATCH`, `XC02.ACCEPTED` | 0-65535 | None (`XI01` unused, and the program copies `XC02` to the panel, BH:133) | No check |
| BH `ENTRY.HS01/HS02` | bit | None. It drives the lockout (BH:129-130) | A deliberate surface, F1 |
| CEL 8 fermenter `TT01/TT02.SP`, 2 BBT `TT01.SP` | 20-90 degF | `LIMIT` CEL:252-261 | Clamps `[V]` CEL-1. Implausible range (F7) |
| CEL `BBT20n.AT01.SP` | 0-5 vol | `MIN(,350)` CEL:262-263, then `MIN(,VOL_MAX)` CEL:391, 401 | Clamps. Temperature-dependent maximum `[V]` (94 at 90 degF) |
| CEL `PROFILE.TC01-03.SP` | 20-90 degF | `LIMIT` CEL:264-266 | Clamps. No ordering check |
| CEL `PROFILE.KC01.SP` | 0-6000 min | `MIN(,6000)` CEL:267 | 0 skips the rest `[V]` MISC-1 |
| CEL `PROFILE.HS01-04.AUTO`, `DH.XC01.CMD` | bit | Gated by entry and CO2 where it matters (CEL:445-466) | OK |
| CEL `ENTRY.HS01-06.ENTRY` | bit | None | A deliberate surface, F1 |
| CI 8 `TT01/TT02.SP` | 20-90 degF | `LIMIT` CI:145-152 | Clamps `[V]` CI-1 |
| PKG `PACKML.CMD` | 0-9 | Accepts 1-9 only (PKG:81), then clears (PKG:89) | Drops invalid values `[V]` PKG-2 |
| PKG `PACKML.MODE_CMD` | 0-3 | `LIMIT(1,.,3)` PKG:70 | 0 becomes 1 `[V]` PKG-2 |
| PKG `PACKML.SPEED_SP` | 0-60/min | `MIN(,600)` PKG:69 | Clamps `[V]` PKG-2 |
| UTL `CHL.TT01.SP` | 0-100 degF | `LIMIT(200,.,400)` UTL:86 | 20-40 degF `[V]` UTL-4 |
| UTL `GLY.PT01.SP` | 0-100 psig | `MIN(,1000)` UTL:87 | 100 psig drives the pump to 100% `[V]` UTL-4 |
| UTL `CIP.TT01.SP` | 32-212 | `LIMIT(320,.,2000)` UTL:88 | 32-200 degF `[V]` UTL-4 |
| UTL `CIP.XC01.CIRCUIT` | 0-8 | `MIN(,7)` UTL:89 | 8 and 9999 select circuit 7 `[V]` UTL-3 |
| UTL `CIP.XC01.CMD` | 0-3 | The program handles start and abort (UTL:165-176). Hold is a level | Other values stay in the register, and the program ignores them |
| UTL `CO2.XC01.RESET` | bit | Accepted only after 60 s healthy (UTL:111-116) | OK |
| PWR `DMD.JY01.SP` | 0-1000 kW | `LIMIT(50,.,600)` PWR:126 | Clamps `[V]` PWR-1. 600 kW disables cap discharge |
| PWR `DMD.EY01.SP` | 0-100 % | `MIN(,100)` PWR:127 | Clamps `[V]` PWR-1 |
| PWR `DMD.XS03.BREWDAY` | bit | None, no timeout (DESIGN.md:4400: nothing sets it yet) | OK |

Beyond the 49: the Modbus and S7 servers let a host write every coil and holding register. The dangerous values are those that the program reads back as state and does not reassign each scan:
- The latches and permits in F1.
- `PWR_DMD_JY02_PV`, the peak (PWR:251-254): a write of 65535 freezes it and a write of 0 resets it (PWR-3).
- `BH_BATCH_XS01_STATE` and `BH_KTL_KI01_PV` (read-only over OPC UA).

Rating:
- Met where the program bounds every writable value to the range that the HMI uses (CEL, CI, PKG, PWR). Clamping satisfies the "prevent, or alert on" of the PDF.
- Partly for BH (unbounded MILL parameter, and no check of `XC02` and `XI01`) and UTL (a saturated selector, and a wide pressure bound).
- No program substitutes a default or last good value, and none alerts. Out-of-range writes leave no trace.

## Practice 9. Validate indirections

Asks: poison array ends, make sure that computed indices and selectors are valid.

- No program has an array, pointer or `REF_TO` (static scan of all six).
- Computed selectors:
  - BH: `CASE cmd` (BH:156) and `CASE active` (BH:166, 273) have no `ELSE`. The program makes sure that `active` is 1-10 before it latches the value (BH:158-160). It does not make sure that `cmd` is valid, and it silently clears the value.
  - CEL: `CASE dh_step` has `ELSE` (CEL:459-460) and is internal.
  - UTL: both CIP `CASE`s have `ELSE` (UTL:146, 158). The program saturates the selector first (UTL:89, see practice 8).
  - PKG, PWR, CI: no selector.
- Ratings: BH Partly. CEL and UTL Met. CI, PKG and PWR N/A.

## Practice 10. Assign designated register blocks by function

Asks: separate blocks for read, write, validate and calculation. Then unauthorized or out-of-bounds writes cannot reach controller data, and a firewall can say "read only".

- Layout:
  - The generator locates variables in generated order, so setpoints, computed values, alarms and state sit side by side.
  - Cellar PLC memory has 69 coils. Of these, 11 are writable by design (6 permits, 4 AUTO bits, the dry-hop request) and 58 are alarm, state and latch bits. It has 23 holding registers `%MW0-22`. Of these, 16 are writable by design and 7 are computed or stage values.
  - The Modbus server serves 816 coils and 129 holding registers, all writable (`modbus_slave.json`: `qx_bits` 816, `qw_count` 106, `mw_count` 23).
  - UTL: 816 coils, 110 registers, 6 writable tags. PWR: 808 coils, 105 registers, 3 writable tags.
  - The S7 plugin of PKG exposes five data blocks over the whole `%QW`, `%IW`, `%MW`, `%IX` and `%QX` images (`render.py:267-300`). I did not make sure that the input blocks reject writes (`render.py:273`, TODO).
  - The RIO output image (`%QX100+`, `%QW100+`) is inside the exposed range on every Modbus PLC. DESIGN.md:2730 states the exposure.
- Platform:
  - The OpenPLC Modbus slave maps all coils and registers with all write function codes. It has no read-only or per-range option (OpenPLC `core/src/drivers/README.md`, "Modbus TCP Slave").
  - The firewall is port-granular (DESIGN.md:2077-2108).
  - The only lever in the repository is the way that the PLC lays out writes and makes sure that they are valid. The PDF pattern is a write block: the PLC makes sure that its values are valid and copies them into live variables. No program uses that pattern.
- BH: Partly.
  - The OPC UA configuration makes every node read-only except the 10 read-write tags for the operator and engineer roles (`render.py:197-210`).
  - Anonymous sessions get the operator role (`render.py:243`, deliberate, DESIGN.md:4422).
  - This is the one real write allow-list in the repository `[C]`. DESIGN.md:4417 says that nobody tested the enforcement by the runtime.
- CEL, CI, PKG, UTL, PWR: Not met.
- Self-healing: The programs reassign most computed registers every scan, so an outside write lasts one scan (CEL-7). Practice 8 lists the exceptions.

## Practice 11. Instrument for plausibility checks

Asks: cross-check different measurements.

- BH: Partly.
  - Two independent level switches act as permissives. HLT steam runs only while `LSL01` is made (BH:230-234, fail-safe sense). CLT fill stops on `LSH01` even when `LT01` reads low (BH-5).
  - The disagreement raises no alarm.
  - Boil detection uses barometric pressure and one temperature (BH:237-243).
- CEL, CI, PKG, UTL: Not met. Instrument pairs exist, and the programs do not use them (F6).
- PWR: Partly.
  - The program makes sure that the ATS clock is valid (PWR:152-180, `test_plc_power.py`).
  - Nothing compares day and month. A change of only the month register from 7 to 8 resets the billing peak, and the clock stays trusted (PWR-3, PWR:186-195).
  - Nothing compares site import with feeder, BESS or PV readings.

## Practice 12. Validate inputs based on physical plausibility

Asks: limit entry to what is practical, time operations and alarm on deviation, alarm on inactivity.

- Limits: All six bound values to instrument ranges, not process ranges (F7). The physical limits are packaging speed at most 60/min (PKG:46, 69) and glycol supply 20-40 degF (UTL:40-41).
- Durations and inactivity:
  - CIP wash steps are fixed 300 s timers with a hold (UTL:53, 181-189). The mill, vorlauf, whirlpool and hop dose have timers (BH:68-72).
  - No program has a step-timeout alert, a valve travel time, or an inactivity alert.
  - No program detects a stuck PV (CEL-6: PV frozen at setpoint for 3 h, no alarm).
  - Phases that wait on a process value have no PLC timeout (F4).
  - The only inactivity-like alert is the low-priority packaging "Held for 10 minutes" (`PKG.LINE.PACKML.ALM_STATE`).
- Done well: The program clamps the carbonation target to the highest value that the head pressure can reach (CEL:105-107).
- All six: Partly.

## Practice 13. Disable unneeded communication ports and protocols

Asks: list the required protocols per port, disable the rest.

- Plugins: Met. Each ZIP enables only `modbus_master` plus the one northbound plugin that the PLC needs (I examined the committed ZIPs and `render.py:358-403`). A plugin with no configuration is disabled (DESIGN.md:2433-2434).
- Port-level firewall: Met. Each PLC is reachable only on its own protocol port from SCADA and the EWS, plus TCP 8443 from the EWS (DESIGN.md:2077-2108, `conduits.py:276-352`).
- OPC UA profile: Deliberate. One profile: policy None, mode None, anonymous, role operator (`render.py:231-243`, DESIGN.md:4422).
- Open on every PLC `[C]` `[I]`:
  - The image installs `sshd` with `PermitRootLogin yes`, `PermitEmptyPasswords yes` and a deleted root password (`phenix-images/cm-openplc.yml:105-112`).
    - DESIGN.md:3528-3539 records it as an accepted lab-only risk.
    - DESIGN says that `fw-ot` passes TCP 22 only from dmz-jump to the EWS.
  - Nothing filters intra-zone CTRL traffic (DESIGN.md:2108). I found no host firewall rule for the CTRL interface of a PLC. The nftables policy in `render.py:1852-1930` is for the MGMT interface.
  - The REST API of OpenPLC on 8443 and its WebSocket debug interface (variable inspection and forcing, behind the admin JWT) are on every PLC. A UDP discovery reply on 33333 carries the stored project name without authentication (`docs/SECURITY.md`). OpenPLC has no option to turn these off.
  - `tcpdump`, `ncat`, `vim` and `build-essential` are in the image. The runtime compiles uploaded programs, so it needs the compiler (`cm-openplc.yml:63-65`).
- Ratings: Partly for all. BH adds Deliberate for the anonymous, unencrypted OPC UA profile.

## Practice 14. Restrict third-party data interfaces

Asks: limit connection types and data for third parties, watchdog on communications.

- Connections:
  - The third parties here are the vendor (WireGuard, Guacamole, EWS, then PLC REST), the utility (DNP3 to rtu-der) and SCADA.
  - The firewall restricts connection types per source and port (`conduits.py:276-352, 489-542`, DESIGN.md:2077-2108).
  - The firewall cannot restrict function codes or registers.
- Data: The Modbus and S7 PLCs do not restrict data (practice 10). The brewhouse OPC UA server restricts data per node (anonymous operator, deliberate).
- Utility:
  - `rtu-der` exposes breaker commands and setpoints to the utility master (DESIGN.md:4401, open).
  - plc-power reads the PV and BESS limits of the utility (LV power:29, 38) but never uses them (PWR-4 list). So it has no dependency on that interface.
- Watchdog: No PLC watches its RIO or peer for staleness (recorded, DESIGN.md:4402-4403). Only the batch engine watches the brewhouse PLC (`batch_engine.js:205`).
- Partly for all. BH adds Deliberate. Detection of writes from a non-SCADA, non-EWS host is a recorded use case (DESIGN.md:3030).

## Practice 15. Define a safe process state in case of a PLC restart

Asks: define the state (energize, de-energize, keep). If that is not practical, alert and issue no new commands. Document the procedure.

- BH: Partly.
  - The first scan resets batch state, phase and command (BH:109-117). Phase outputs default off each scan (BH:251-270). Setpoints reload to 168/152/64 degF.
  - `[V]` BH-4: a running MILL reads Idle, mill off, VFD reference 0, and HLT setpoint 150 returns to 168.
  - The PLC does not report the interrupted batch. FUXA reads it only while it runs a batch.
  - The restart loses the permits (F1).
- CEL: Partly.
  - Defaults load (CEL:226-250). The CO2 inhibit starts TRUE for 60 s (CEL-5). The program re-derives the stage (CEL:147-157). DESIGN.md:598-600 records the restart effects.
  - Before the first RIO poll, all inputs read 0. The program then writes all four spunding setpoints at 12 psig (24.10 psia) for the first scan (CEL-5).
  - The restart loses the permits (F1).
- CI: Partly. Setpoints reload to hard-coded defaults (CI:132-142). The program has nothing else to lose.
- PKG: Partly. The program issues no command at start. But mode and speed reset to Production and 30/min, and the first scan pushes them to every machine (PKG:63-68, 96-106, PKG-4: 2/450 become 1/300).
- UTL: Partly.
  - CIP aborts to step 0. Chiller, glycol, air and RO enables return at once (UTL:92-101).
  - The restart loses the CO2 latch (F1).
  - Before the first poll, the pH input reads 0, so the program commands the caustic pump on for the first scan (UTL-8, UTL:121-125).
- PWR: Partly (recorded).
  - The PLC sheds for its first scans and recloses boiler, packaging and RTU-1 at 61, 122 and 183 s `[V]` (PWR-2).
  - DESIGN.md:4400 records this and the lost peak, exercise day and rollover state.
- Platform `[I]`: See F2. The restart behavior that the programs assume ("RIO keeps the last command") is possibly wrong.

## Practice 16. Summarize PLC cycle times and trend them on the HMI

- All: Not met.
  - No program publishes a scan statistic.
  - The task interval is fixed at 100 ms (`plcbuild.py:41-44, 172-180`), and `plcbuild.py:190-192` makes a scan-counting program declare the same interval.
  - BH:346-348, CEL:164-167 and PKG:47 count scans, so timed steps are only as accurate as the scan.
- Platform: OpenPLC logs scan time, cycle time, latency and overruns every 5 s and has a stuck-task watchdog (10 missed periods). Neither a program nor the HMI can read any of them. A program can only publish a heartbeat counter (F9).

## Practice 17. Log PLC uptime and trend it on the HMI

- All: Not met.
  - Every program has a first-scan flag (`started`: BH:87, CEL:201, PKG:53, UTL:62, PWR:78, CI:109). No program counts the flag or exposes it.
  - No uptime or restart tag exists in `plant.yaml`.
  - OpenPLC v4 has no SNMP and no uptime variable.
  - FUXA sees a restart only through the batch engine (F9).

## Practice 18. Log PLC hard stops and trend them on the HMI

- All: Not met.
  - To record a fault, OpenPLC writes `/run/runtime/watchdog_fault`, boots into safe mode and reports ERROR (`docs/ARCHITECTURE.md`). The runtime log holds the events.
  - Nothing forwards them. The PLCs send no syslog. No PLC host has a Wazuh agent (no conduit from CTRL, DESIGN.md:2104-2108, and `conduits.py:529-530` blocks connections that PLCs initiate upward). No tag exists.
  - Time sync: The image carries `chrony` (`cm-openplc.yml:28`). I did not make sure that it is configured.

## Practice 19. Monitor PLC memory usage and trend it on the HMI

- All: Not met.
  - No PLC has a memory baseline or trend.
  - The programs allocate nothing dynamically (no arrays, no strings).
  - DESIGN.md:2460-2466 measured the runtime at 161 MiB steady and 361 MiB peak for a three-variable program. It records that a brewhouse-size compile is not measured. That is a compile measurement, not a run-time trend.
  - OpenPLC exposes no memory figure to a program.

## Practice 20. Trap false negatives and false positives for critical alerts

- BH, CEL: Partly.
  - The entry-violation alarm traps a false negative of the outputs. If a locked output is on at the top of a scan, an outside write set it (BH:124-130, 143-149, and CEL:310-321).
  - `[V]` It works: a coil write to a locked valve latches the Critical alarm (CEL-4, BH-10).
  - Two gaps `[V]`: The latch is itself a writable coil, so one write erases it. A withdrawal of the permit raises nothing.
  - No program has a false-positive trap.
- CI, PKG, UTL, PWR: Not met.
  - The program computes the Critical BESS off-gas alarm from one input, with no on-delay and no hold (F8).
  - The CO2 shutoff state alarm is latched, but the latch is erasable (F1).
- All:
  - The program assigns the alarm bit from the output of the block every scan, so an outside write lasts at most one scan `[V]` (CEL-7). A 1 s poller can see that window (F8).
  - The debug interface of OpenPLC can force variables (README, "WebSocket Debug Interface"). A forced alarm bit has no trap either.

---

# 3. Deliberate and recorded weaknesses

- Decision: The range owner chose it.
- Open: DESIGN.md lists it as unfinished work, not a decision. I treat it as recorded, do not count it as a new finding, and still rate the practice by the facts.
- Documented limit: DESIGN.md states it as how the system is.

§19 table rows have no numbers, so I cite the DESIGN.md line.

| Item | Where recorded | Kind | Practices | Notes |
|---|---|---|---|---|
| An anonymous OPC UA session on plc-brewhouse gets the operator role. Any host that reaches 4840 can write the 10 read-write tags of the brewhouse | §19, DESIGN.md:4422 | Decision | 8, 10, 13, 14 | `render.py:231-243`. Those 10 nodes are the only writable ones |
| The confined-space lockout lives in the PLC. A stopped PLC or a write straight to the RIO bypasses it | §19 (DESIGN.md:4404) and §6.8 (DESIGN.md:925-927) | Decision (accepted) | 3, 15, 20 | F1 is the case that this row does not cover |
| `modbus_master` keeps the last input on a lost RIO link. The RIO heartbeat is not built | §19 (DESIGN.md:4402), §6.7 (DESIGN.md:823, 828), and DESIGN.md:3685-3689 | Open | 3, 7, 11, 14, 15 | I made sure that `modbus_master.json` has `keep-last-value` for every RIO and device. Peers use `set-to-zero` |
| The CO2 shutoff latch can miss a trip while rio-utilities is unreachable. The brewhouse CIP circuit is not interlocked to brewhouse permits | §19, DESIGN.md:4403 | Open | 3, 7 | `[V]` UTL-7: circuit 1 route OK with every peer bit 0 |
| plc-power does not retain the peak, the exercise day and the rollover state. It sheds at start and recloses feeders 60 s apart | §19, DESIGN.md:4400 | Open | 10, 15 | `[V]` PWR-2: 61, 122, 183 s |
| The batch engine never ran against OpenPLC. Phase timeouts are estimates | §19, DESIGN.md:4417 | Open | 3, 12 | F4 adds that the PLC has no timeout of its own |
| The rtu-der DNP3 map exposes breaker commands and setpoints to the utility master | §19, DESIGN.md:4401 | Open (detected) | 14 | Field-device level, not in the PLC programs |
| Modbus and S7 writes from a host other than scada-fuxa or the EWS are a detection use case. The PLC image is writable by protocol | §14 (DESIGN.md:3030), §12.5 (DESIGN.md:2730), and §10.3 (DESIGN.md:2108) | Documented limit (detect, not prevent) | 8, 10, 14 | DESIGN.md does not discuss the layout (state beside setpoints) |
| A cleared permit unlocks an occupied vessel (Suricata 9000173 and Zeek `Life_Safety_Write` detect it) | §6.8, DESIGN.md:921-924 | Documented limit | 10, 20 | Detection covers permits and the CO2 reset coil only (`cherrymountain.zeek:135-144`) |
| Every image, PLCs included, has a passwordless root console and root SSH with an empty password | §15.7 (DESIGN.md:3528-3539) and §13 (DESIGN.md:2867-2872) | Decision (accepted lab-only risk) | 13 | `cm-openplc.yml:105-112` |
| The ci program has alarm on-delays only (no deadband, off-delay, suppression) | §6.5, DESIGN.md:755 | Documented limit | 20 | CI-2 shows the effect |
| A restart during a diacetyl rest repeats it. A restart early in a crash warms the beer for another rest | §6.3, DESIGN.md:598-600 | Documented limit | 11, 15 | DESIGN.md does not record the same effect from one input glitch (F6) |
| Neither the PLCs nor FUXA mark a stale value (TODO: heartbeat, tag age) | DESIGN.md:3685-3689 | Open | 11, 14, 15 | |
| None of the five programs ran in an OpenPLC runtime | `plc/common/README.md:30-31` and §19 (DESIGN.md:4398) | Documented limit | all | This limits what I was able to make sure of |
| The peer permit reads 0 on a lost plc-cellar (fail-safe) | UTL:26-29 (header comment) and `plantgen/render.py:820` | Design, not a weakness | 15 | Included to show the rating |

---

# 4. What I could not evaluate, and why

- OpenPLC runtime behavior:
  - No runtime was available, and the repository never ran these programs in one.
  - I did not make sure of these points:
    - That Modbus, OPC UA and S7 writes land in the image as I assumed.
    - That the runtime enforces the OPC UA node permissions as `render.py:197-210` configures them.
    - That the S7 input blocks reject writes (`render.py:273` TODO).
    - That a located `%QX` marked `RETAIN` survives a restart.
    - What a stop does to `%Q` (F2).
    - Which sockets the runtime and image actually open. I know `sshd`, 8443, UDP 33333 and the plugin ports from the image configuration and vendor documentation, not from a listener list.
  - All of these need one VM run.
- Closed-loop plant behavior:
  - I scanned the compiled programs with hand-set inputs.
  - I did not run them against the federates. I did not re-run the pytest suites of the repository because the `cherrymountain_sim` package is not installed here.
  - My results show what the program does with an input. They do not show whether the simulated plant then reaches the bad state.
- FUXA at run time:
  - I read the generated projects and the batch engine, not a running FUXA.
  - I inferred two things from the project (polling 1000 ms, alarm bindings). The first is how FUXA treats a bit that is true for less than a poll. The second is whether FUXA shows a disconnected device as an alarm.
  - During the review, another process was at work on a rewrite of the FUXA builder.
- Network controls: I read `conduits.py`, DESIGN §10.3 and `render.py`. I did not run `netcheck`, any VyOS ruleset, Zeek or Suricata.
- Process limits: The "process ranges" in F7 are reasonable engineering numbers, not values from the data sheets of the plant. The HLT boiling limit comes from the boil model of the program at 12.1 psia. The real tolerances are a decision for the range owner.
- PDF figures: I did not view the diagrams for practices 16 and 20. The text states the method fully, so I do not expect a difference.
- Practices 16 to 19: The ratings come from the absence of anything in the programs, the FUXA project and the image. I did not look for a monitoring path outside the repository (a historian query, a Wazuh decoder on runtime logs) because the repository references none.

---

# Appendix: evidence

The review compiled each program with the pinned strucpp and g++ through
`sim/tests/st_engine.py`. It scanned each program with hand-set inputs, in about 60
scenarios (the BH-n, CEL-n, PKG-n, UTL-n, PWR-n, CI-n, MISC-n, and FIX-n labels above).
Those scripts and their logs were scratch files and are not in the repository. The
scenarios behind the fixed findings are now tests (see "Since the review" at the
top). You can rebuild the others from the descriptions with the same harness, as
`sim/tests/test_plc_utilities.py` does.

Checks that found nothing wrong:
- Out-of-range writes to every analog setpoint (all clamp as coded).
- Invalid brewhouse and packaging commands (dropped, no side effect).
- UINT arithmetic at BH:320 (no wrap).
- Caustic and acid pumps are never on together.
- CO2 interlock inhibit timing.
- The cellar peer permit failing safe.
- The ATS clock trust sequence. I read it, and `test_plc_power.py` covers it.
