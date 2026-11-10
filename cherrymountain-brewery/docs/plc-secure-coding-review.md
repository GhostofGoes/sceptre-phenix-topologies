# CherryMountain Brewery PLC logic against the Top 20 Secure PLC Coding Practices

This is a review of the range's PLC programs against the [Top 20 Secure PLC Coding
Practices](https://plc-security.com/content/Top_20_Secure_PLC_Coding_Practices_V1.0.pdf)
(version 1.0, the PLC Security project). It was made on 2026-11-10 by an AI
reviewer (a Claude agent) that read the practices' full text, compiled every
program, and ran it. Nobody has checked its findings one by one except where "Since
the review" says a finding was reproduced and fixed. Bringing the programs into line
with the practices is future work
([FUTURE_WORK.md](FUTURE_WORK.md#plc-programs-that-follow-the-top-20-secure-plc-coding-practices)).

File and line references are to the tree at commit `d5520d1`, before the fixes
below; `git show d5520d1:<path>` shows a cited line.

## Since the review

The review looked for security practice, and on the way found defects that have
nothing to do with an attacker. Those are fixed, each with tests that run the
compiled program and that fail on the program as it was:

| Finding | What was wrong | Fix | Tests |
|---|---|---|---|
| F3 | plc-utilities: a CIP wash with no return flow stopped and restarted its pumps every 30 s for good, never advanced, and raised no alarm | The lost flow is latched (`UTL.CIP.XA01.PV`, a new medium-priority alarm). The pumps stay off until the operator commands Hold and Resume, or Abort | `sim/tests/test_plc_utilities.py` |
| F7 (part) | plc-utilities: a CIP circuit number above 7 was cut to 7, so 8 selected BBT-202's circuit | A number above 7 selects no circuit (0), which has no route. The tag's range is 0 to 7 | `sim/tests/test_plc_utilities.py` |
| F3 (part) | `UTL.CIP.XS02.ROUTE_OK` was described as "route verified and return flow proven"; the program never included the flow switch in it | The description now says what the bit is: the panel is on the circuit and the cellar permits it | - |
| F5 | plc-brewhouse: the mash deviation alarm was enabled only at temperature, so a mash that stayed cold (no steam) never alarmed and its rest never counted | The alarm is also enabled once a mash step has run 15 minutes, wherever the temperature is. The rest still counts only at temperature | `sim/tests/test_plc_brewhouse_mash.py`; the whole-brew test checks that a normal brew does not raise it |
| F6 (part) | plc-cellar: one scan of 0 from a fermenter's density meter made the vessel "empty", which put a crashed beer back to 66 degF and then through a second rest | A reading must hold for 60 s before it ends a stage (empty, or the end of the fermentation) | `sim/tests/test_plc_cellar.py` |

Looked at and left as they are:

- **The OEE's quality term falls back to 1.0** when the line's bottle counter is
  below its reject counter (practice 4). The counters are 16-bit and would have to
  wrap for that, which takes more than 36 hours of filling at 30 bottles a minute.
- **First-scan values** (practice 15: the spunding setpoints and the caustic pump
  for one scan before the first I/O poll). They were seen with every input at 0 in
  the test harness. Whether the OpenPLC runtime scans before its first poll is
  not known, and one scan of either does nothing to the plant.

Everything else in this document is as the reviewer wrote it, and is security
practice: it is the list for the future work.


Reviewed: the five OpenPLC v4 programs of `cherrymountain-brewery/` (plc-brewhouse, plc-cellar full/lite and ci, plc-packaging, plc-utilities, plc-power), the generator in `plc/common/`, the OpenPLC configs and image, the FUXA batch engine, and the firewall rules that reach the PLCs.

## Status of this review

- **Method tags.**
  - `[V]` means shown by running the compiled program.
  - `[C]` means read in code or config, with the line cited.
  - `[I]` means inferred from documentation or other files, not run.
- **Nothing ran in an OpenPLC runtime.** The repo says the same (`plc/common/README.md:30-31`). Runtime statements are `[C]` or `[I]`.
- **Lite equals full.** `plant/generated/lite/openplc/<plc>/located_vars.st` differs from full only in its header comment for all five PLCs, and `alarms.json` has the same 97 records. Findings for "full" hold for "lite". The ci variant (plc-cellar only) is rated separately.
- **File abbreviations:**
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

Legend: **Met**, **Partly**, **Not met**, **N/A** (nothing for the practice to apply to), **Deliberate** (recorded decision, section 3).

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

- Most "Partly" and "Not met" entries come from how the generator and programs are built, not from one program. `plc/common/plcbuild.py` checks that outputs are assigned and alarm bits are not. It does not check that a host-writable tag is clamped, that a latch is protected, or that an input is used (`plcbuild.py:187-207`).
- The strong points are real (section 1.3). The programs stop at the operational layer and have no security layer.

## 1.2 The findings that matter most

Severity is judged for a cyber range. "Recorded" means DESIGN.md already says it. Each finding below is something DESIGN.md does not say, or says the opposite of. Recorded items are in section 3.

### F1. A PLC restart silently removes the confined-space lockout and the CO2 shutoff latch, and one write clears them (High)

- **What is wrong.** These are plain non-retained coils. The program reads them back as state:
  - the six cellar entry permits (`CEL_ENTRY_HS01..06_ENTRY`, `%QX4.3` to `%QX5.0`, LV cellar:119-124);
  - the two brewhouse permits (`%QX1.1`, `%QX1.2`, LV brewhouse:75-76);
  - the CO2 supply latch (`UTL_CO2_XS02_SHUTOFF`, `%QX1.1`, LV utilities:47);
  - the Critical entry-violation latches (`XAnn_PV`, for example `%QX6.5`).

  A restart resets all of them to 0. Any Modbus client can write any of them.
- **Where.** CEL:310-336 and 405-436; BH:124-130 and 326-345; UTL:104-117. All six compiled programs have `retain_var_count = 0` (`build/*/gen/generated_debug.cpp`).
- **How it hurts.** The PDF calls forcing a restart "the most basic attack vector for a PLC" (practices 15, 17).
  - After a restart `[V]` (CEL-4): a permit set before reads 0, `LOCKED` is 0, and the entered fermenter's glycol valve is commanded again on the first scans.
  - After a restart `[V]` (UTL-2): the CO2 latch is gone. With the relay healthy, the valve command returns with no operator reset and no 60 s healthy period.
  - Without a restart `[V]` (CEL-4, UTL-2):
    - Writing 0 to `%QX1.1` on plc-utilities bypasses the CO2 latch (valve command goes 0 to 1 at once).
    - Writing 0 to an `XAnn_PV` coil erases a latched Critical alarm while the vessel is still locked.
  - The brewhouse node is read-only over OPC UA (`apps/cherrymountain_apps/cherrymountain/render.py:197-210`). BH-10 shows the erasure only on the raw image.
  - Network detection does not cover these writes. `phenix-injects/malcolm/zeek/cherrymountain.zeek:135-144` watches writes to the CO2 reset coil and the permits, not the latch coils.
- **Not recorded.**
  - DESIGN.md:4404 accepts that "a stopped PLC, or a write straight to the RIO, bypasses" the lockout. That is a different case from "a restart forgets the permit" and "a latch lives in a writable coil".
  - DESIGN.md:598-600 records the restart effect for the fermentation profile only.
- **Confidence.**
  - `[V]` for what the programs do.
  - `[I]` that the servers accept those writes (OpenPLC `core/src/drivers/README.md`, Modbus slave: function codes 05, 06, 0F, 10).
- **Smallest fix.**
  - (a) Keep each latch in an unlocated internal variable and publish a copy each scan. An outside write is then overwritten. Prototype in `fixes.py` (`shutoff_l`), verified in `logs/fixes.log` FIX-1b.
  - (b) Declare permits and latches `VAR RETAIN`.
    - The pinned strucpp accepts `RETAIN` on a located variable and emits a retain table (`retain-probe/out/generated_debug.cpp`: `LEAF_FLAG_RETAIN`, `retain_var_count = 2`).
    - The OpenPLC v4 docs describe a built-in retain store. I did not verify that the runtime persists a located `%QX` across a restart.
    - Fallback: FUXA re-asserts the permit state after a PLC restart.
  - (c) Add plc-utilities coil 9 and the `XAnn_PV` coils to the Zeek watched-writes table.

### F2. A stopped or crashed PLC probably drives its outputs to 0, the opposite of what DESIGN.md says (High if confirmed) `[I]`

- **What is wrong.**
  - DESIGN.md:824 says "plc-cellar stopped: Its outputs stop updating; rio-cellar keeps the last commands."
  - OpenPLC v4 docs at the pinned commit say otherwise (`docs/ARCHITECTURE.md`, "Watchdog System", layers 2 and 3):
    - "On every stop ... all `%Q` outputs are written to 0 and pushed by a final I/O cycle."
    - After a watchdog fault the runtime reboots with `--safe-mode --fault`, which "does not load the program" and drives outputs to 0.
- **How it hurts.** If the docs are right, a stop pushes 0 through `modbus_master` to every RIO and device. A stop can come from REST `stop-plc` on the EWS or from a watchdog fault.
  - For plc-power this includes `PWR_MAIN_HS01_CMD`, described as "52-M close command (0 opens)" (LV power:17; driven TRUE every scan at PWR:146). Stopping the power PLC would open the site main breaker.
  - The chiller, glycol pump, boiler, air and RO enables (UTL:92-101, 118) and the PV and BESS enables (PWR:213-217) also drop.
  - After a fault the safe-mode boot does not reload the program. The plant stays that way until someone starts it.
- **Why I am not sure.** I did not run the runtime. The docs may describe intended behavior. The ot-sim RIO may treat a written 0 differently from hardware. DESIGN.md:4398 and 4417 record that the first VM run is still open.
- **Smallest fix.** One VM run: stop plc-power and read the breaker.
  - If confirmed, change the polarity of anything that must keep running to a "trip" or "inhibit" output whose 0 is the safe state, or configure the RIO to hold on master loss.
  - Correct DESIGN.md:824 and §7.3 either way.

### F3. The CIP "return flow not proven" hold releases itself, so a wash with no return flow stalls forever with no alarm (Medium-High) `[V]`

- **What is wrong.**
  - UTL:179-180 holds the wash when `UTL_CIP_HS02_CMD AND cip_timer.ET > T#30s AND NOT UTL_CIP_FS01_PV`.
  - The hold turns the return pump command off (UTL:192). That makes the term false on the next scan, which releases the hold.
  - The step timer resets on every hold (UTL:186).
- **Result `[V]`** (UTL-1):
  - With the flow switch never made, the return pump command dropped 13 times in 400 s (first at 29.9 s, then every 30.3 s).
  - The step stayed at 1, the route stayed OK, and no CIP alarm was raised.
  - With the flow switch made, the step advances after its 300 s.
  - LV utilities:89 describes `UTL_CIP_XS02_ROUTE_OK` as "route verified and return flow proven", but UTL:163 does not include the flow switch.
- **Why nobody saw it.** plc-utilities has no compiled test. Its only tests are regular-expression checks (`tools/netcheck/tests/test_plcbuild.py:123-136`).
- **Smallest fix** (prototyped and verified, FIX-1a):
  - a separate 30 s timer on `HS02_CMD AND NOT FS01`;
  - a latched `flow_lost` flag, cleared when the step is 0 or done;
  - `cip_hold := ... OR flow_lost`.

  After one drop the pumps stay off until the operator aborts. Add an alarm bit for the latched state.

### F4. Brewhouse phases that wait on a process value have no PLC timeout, and the timeouts and knockout permissive live in the FUXA script (Medium) `[V]`

- **What is wrong.**
  - Phases 2, 3, 6, 7 and 9 complete only when a process value reaches a threshold (BH:281, 286, 303, 306, 321). The PLC has no step timer or alarm for them.
  - The only timeout is `ph.timeout_s` in `fuxa/batch_engine.js:393-401`, which sends a Hold.
  - The same script owns the start refusal (`batch_engine.js:254-261`) and the knockout destination reservation (`batch_engine.js:347-377`).
  - The PDF names exactly this as code to keep out of the HMI: "interlocks, timers, holds or permissives" (practice 3).
- **Results `[V]`** (`logs/brewhouse.log`):
  - BH-6: MASH_IN with the MLT level stuck at 0 for 2 h stays Running, with the strike valve and rake on.
  - BH-9: PHASE 9 with CMD 1 on a cold PLC sets the wort pump, knockout valve and pump speed (700) at once. The program reads nothing from plc-cellar.
  - BH-5: HLT level transmitter stuck at 0 for 1 h leaves the HLT fill valve open. The HLT has no high-level switch in `located_vars.st`, and the only alarm is the low-priority "low level".
- **How it hurts.** If FUXA is down, or an attacker writes `XC01_PHASE` and `XC01_CMD` directly, the destination check and every timeout are bypassed. Both tags are HMI-writable by design, and the anonymous OPC UA write is recorded at DESIGN.md:4422. The knockout moves product toward a vessel that may be under confined-space entry.
- **Partly recorded.** DESIGN.md:768 states the FUXA-side reservation as the design. DESIGN.md:4417 flags the timeouts as estimates. Neither says the PLC has no timeout of its own.
- **Smallest fix.**
  - A PLC timer per waiting phase (constants next to BH:68-72) that Holds the phase and raises a state alarm.
  - Either mirror the cellar's "lined up" bit to the brewhouse (the peer path DESIGN.md:4403 already asks for), or make KNOCKOUT refuse to start until FUXA's reservation bit is set in the PLC.

### F5. The mash deviation alarm cannot fire when the mash is too cold, and a mash that loses steam stalls without an alarm (Medium) `[V]`

- **What is wrong.**
  - `BH_MLT_XS01_ACTIVE := PV + 2.0 >= SP` (BH:291) is the suppression condition of `BH.MLT.TT01.ALM_DEVIATION`. See `alarms.json` (`suppress: BH.MLT.XS01.ACTIVE`), `plcbuild.py:154-156`, `cm_alarm.st:37-42`.
  - With the PV more than 2.0 degF below setpoint, ACTIVE is 0, the alarm is suppressed, and its timers reset.
  - The same bit gates the rest timer (BH:292), so the rest never counts either.
- **Result `[V]`** (BH-7):
  - 2 h at SP minus 5 degF: state Running, `STEP_MS` 0, ACTIVE 0, alarm 0.
  - At SP plus 3 degF the alarm raises after 600 s.
  - At SP minus 3 degF for 1,200 s it does not.
- **Smallest fix** (verified, FIX-2):
  - A 15 minute ramp-allowance timer enables the alarm even when the PV is under setpoint.
  - The rest clock still counts only at temperature.
  - The alarm then raises at 25 minutes (15 plus the 10 minute on-delay) and stays suppressed during the ramp.

### F6. No program checks an input for plausibility, and about 90 instrumented inputs are never read (Medium) `[V]` `[C]`

- **What is wrong.**
  - Every control decision uses one sensor value directly. No program tests range, rate of change, stuck value, or agreement between instruments.
  - Counts of located inputs that no statement of the assembled program reads (alarm logic included): BH 12, CEL 11, CI 9, PKG 26, UTL 17, PWR 15. See `logs/cellar.log` CEL-8, `logs/packaging_utilities.log` PKG-6 and UTL-9, `logs/power_ci.log` PWR-4, `logs/misc.log`.
  - Unread second instruments include:
    - spunding valve open `ZSO01` x4;
    - hop port closed `ZSC01`;
    - carbonation stone flow `FT01`;
    - BBT-201 level;
    - mill and rake running feedback;
    - kettle high-level switch `LSH01`;
    - neutralization tank high-level switch;
    - CO2 valve limit switch `ZSO01`;
    - every feeder, BESS and PV connection status in plc-power.
- **Results `[V]`:**
  - Cellar (CEL-3, CEL:147-169):
    - One scan of `DT01 = 0` sends a crashed fermenter's setpoint to 66 degF.
    - During a diacetyl rest it restarts the rest timer (`rest_ms` 100200 to 100).
    - Mid-crash above 60 degF it sends the beer back to the 70 degF rest.
    - DESIGN.md:598-600 records this for a PLC restart only.
  - Brewhouse (BH-5, BH-8):
    - HLT temperature transmitter reads 0 for 1 h: steam valve 100%, no alarm.
    - Barometric `PT01 = 0`: boil never detected, phase stuck, no alarm.
    - With a healthy `PT01`, one temperature reading 0.3 degF under the computed boiling point completes a 60 minute boil.
  - Packaging (PKG-3, PKG:157-162): the keg wash heater stays on indefinitely when `TT01` reads 0, and the keg washer has no temperature alarm.
  - Utilities:
    - UTL-5 (UTL:121-134): a pH probe stuck at 3.00 runs the caustic pump for 3 h. Stuck at 12.00, the acid pump runs. There is no maximum dose time.
    - UTL-2: the CO2 valve limit switch is never compared with the command. There is no alarm for "commanded open, not open" or "commanded closed, still open".
  - Power (PWR-4): boiler feeder commanded closed, position input reads open for 700 s, no alarm.
- **Smallest fix:**
  - Add the agreement checks where the instrument pair already exists: each `ZSO01`, `ZSC01` or `YS01` against its command, with a travel-time timer and a state alarm.
  - Compare the level switch with the analog level.
  - Add a maximum dose time on the neutralization pumps.
  - Add a debounce of a few scans on `DT01 < EMPTY` in `FermentProfile`.
  - A generated range check per analog input in `plcbuild.py`, with limits from `plant.yaml` `range`, would make the out-of-range case uniform.

### F7. Writable values are bounded to instrument ranges, not process ranges, and out-of-range writes are clamped silently (Medium) `[V]`

- **Results `[V]`:**
  - `BH.BATCH.XC03.PARAM` for MILL: 65535 gives a 36.4 hour mill run (131,070,000 ms, BH:170-171). Minute parameters are capped at 600, upper bound only (BH:75, 163).
  - The HLT setpoint accepts up to 205.0 degF (BH:118). Its high alarm is 190.0 degF, and the program's own boil model gives 202.7 degF at 12.1 psia (BH-8). A legal setpoint is above the alarm and above boiling.
  - Fermenter and bright-tank setpoints accept 20.0 degF (CEL:252-261, CI:145-152).
    - With the PV following, no alarm is raised: the deviation alarm tracks the setpoint and there is no cold alarm (CEL-2).
    - `PROFILE.KC01 = 0` skips the diacetyl rest (MISC-1).
    - The glycol pressure setpoint accepts 100.0 psig (UTL:87).
    - The demand cap accepts 600 kW, which switches off BESS cap discharge for a site importing 400 kW (PWR-1).
  - `UTL_CIP_XC01_CIRCUIT` is saturated, not rejected: 8 and 9999 become 7, a real circuit (UTL:89, UTL-3).
  - Out-of-range writes leave no flag, counter or alarm anywhere.
- **Smallest fix:**
  - Generate the clamp from `plant.yaml` `range` for every `*-read-write` tag in `plcbuild.py` and fail the build when one is missing. The generator already has the tag list (`plcbuild.py:77-92`).
  - Use process ranges in `plant.yaml`: HLT at most 200 degF, fermenters about 28 to 80 degF, glycol pressure about 40 psig. These are my suggestions, not data-sheet values.
  - Cap `XC03.PARAM` at the real grain bill.
  - Reject and count instead of saturating a selector.

### F8. Critical and high alarms can be missed, and nothing traps a falsified alarm bit (Medium) `[C]` `[V]`

- **What is wrong:**
  - All 79 PLC alarms have `off_delay_s = 0`, and 22 have `delay_s = 0` (`plant/generated/full/alarms.json`).
  - FUXA polls every PLC once a second (`"polling": 1000` per PLC device in `fuxa/full/scada-fuxa-project.json`) and binds each alarm to the bit with `timedelay` 0.
  - Twelve High or Critical alarms have zero on-delay and no latch in the program:
    - the six PSV-lifted alarms;
    - `UTL.CHL.YA01`, `UTL.STM.LSL01`, `PWR.MAIN.YA01`, `PWR.GEN.ZS01`;
    - `PWR.BESS.TT01.ALM_HIGHHIGH`;
    - the Critical `PWR.BESS.AT01.ALM_HIGH` (BESS off-gas).

    A condition shorter than the poll can come and go between reads `[I]`.
  - Deviation alarms use the host-writable setpoint as reference (`plcbuild.py:142-147`), so a legal setpoint change moves the alarm with it (CEL-2).
  - The ci program has no deadband or off-delay (CI-2 `[V]`):
    - A PV swinging between 2.01 and 1.99 degF off setpoint every minute for an hour never raises the 900 s deviation alarm.
    - The full program raises it at 15 minutes.
    - DESIGN.md:755 records the limitation. The effect is verified here.
  - The only trap in any program is the entry-violation check (BH:124-130, CEL:310-321). Nothing compares an alarm bit with its own condition.
  - A raw write to an alarm bit shows for one scan (CEL-7).
- **Smallest fix:**
  - Set `off_delay` of at least 2 s on every alarm. This is one line in `plant.yaml`, and the generator and `CmAlarm` already support it.
  - Latch the Critical and High state alarms.
  - In `plcbuild.alarm_call`, before each `alm_n(...)` call, add `IF {bit} <> alm_n.ALM THEN tamper := tamper + 1;` and publish `tamper`. The bit at the top of a scan must equal what the block computed last scan, so a difference is an outside write. That is the practice 20 trap, one generated line per alarm.

### F9. Nothing monitors the PLCs themselves: no cycle time, uptime, restart, stop or memory data reaches the HMI (Medium) `[C]`

- **What is wrong.**
  - Practices 16 to 19 have no implementation in any program or in the FUXA project.
  - OpenPLC v4 tracks scan count, scan time, cycle time, latency and overruns but only writes them to its log every 5 s (`docs/ARCHITECTURE.md`, "Performance Monitoring").
  - `/api/status` returns one word (EMPTY, INIT, RUNNING, STOPPED, ERROR). None of this is readable by a program, Modbus client or OPC UA client.
  - The repo reads the status word only in the phenix state-of-health probe (`apps/cherrymountain_apps/cherrymountain/soh.py:77-84, 190-196`), which is not an operator view.
  - A restart reaches FUXA only as the batch engine's "the brewhouse PLC lost the phase (restarted?)" while a batch runs (`batch_engine.js:409-411`, text at 60).
  - Timed steps count scans, not time (BH:346-348, CEL:167). A cycle overrun silently stretches a mash rest or diacetyl rest, which is the case cycle-time trending exists for.
- **Smallest fix:**
  - Add a `SYS` block to `plant.yaml` and have `plcbuild.py` add one fragment to every PLC: a scan counter (heartbeat), seconds since start, and a retained restart counter. FUXA trends them and alarms on a stall or a reset. This also gives the PLC-side half of the open RIO heartbeat (DESIGN.md:4402).
  - For practice 5, add a state-of-health test that reads the loaded program's MD5 over the debug interface (`DEBUG_GET_MD5`, OpenPLC `docs/DEBUG_PROTOCOL.md`) and compares it with `defines.h`.

## 1.3 What is in good shape

- **One alarm block.** `CmAlarm` (`plc/common/cm_alarm.st:14-43`) is generated per alarm from `alarms.json` (`plcbuild.py:130-165`), so the PLC and FUXA cannot drift. FUXA alarms watch the PLC bit, not a threshold of their own. Alarm bits cannot be assigned by hand (`plcbuild.py:194-196`).
- **Range-limited setpoints.** Every host-writable setpoint is range-limited in the PLC, to the HMI's range or narrower (49 read-write tags covered; exceptions in F7). Clamps are applied in place before use (BH:118-120, CEL:252-267, PKG:69-70, UTL:86-89, PWR:126-127).
- **Deliberate restart behavior:**
  - Brewhouse outputs default off each scan (BH:251-270).
  - The cellar CO2 inhibit starts TRUE and releases 60 s after the relay is healthy (CEL:340-341, verified: 1 at 30 s, 0 at 61 s).
  - The profile re-derives each fermenter's stage (CEL:147-157).
  - A lost cellar peer reads 0, so CIP fails safe (`modbus_master.json` `set-to-zero`, `tools/plantgen/plantgen/render.py:820`, verified UTL-7).
- **plc-power's clock trust** (PWR:152-180) is a real plausibility check: range, step limit, stale and jump detection, re-baselining. It is the best example in the repo of practices 11 and 12.
- **CIP start logic** drops a refused start (UTL:165-176). Both CASE statements have defaults (UTL:146, 158).
- **Guarded divisions and counters.** Division sites are guarded (BH:238, PKG:136, 141). Counters are guarded against underflow (PKG:84-85, PWR:141-145).
- **Minimal plugins.** Each committed ZIP carries only the plugins its PLC needs.
- **Firewall.** Each PLC is reachable only on its own protocol port, from SCADA and the EWS only. `netcheck` checks this (`tools/netcheck/netcheck/conduits.py:276-352, 489-542`).

---

# 2. Practice by practice

## Practice 1. Modularize PLC code

**Asks:** split into function blocks, monitor execution time and size, test modules independently.

- **All.** Alarm logic is one reusable block, `CmAlarm` (`cm_alarm.st:14-43`), instantiated once per alarm (`plcbuild.py:130-165, 257-267`). Nothing monitors execution time or size.
- **BH: Partly.** One function `PValve` (BH:54-61), used four times. A PROGRAM of about 290 lines with the ten phases inline in one CASE (BH:273-324).
- **CEL: Partly.**
  - Three function blocks (`GlycolZone` L57, `Carbonation` L74, `FermentProfile` L127) are reused across four fermenters and two bright tanks.
  - The entry lockout (L310-336, 405-436) and spunding setpoints (L344-382) are copied per vessel.
- **CI: Partly.**
  - `ZoneControl` (L23) and `PressureAlarm` (L64) are reused.
  - Control and alarm are mixed in `ZoneControl`.
  - The alarm limits are hard-coded (L91-100) and compared with `alarms.json` only by `plc/plc-cellar/assemble.py:85-115`.
- **PKG, UTL, PWR: Not met** for hand-written logic. One PROGRAM each (PKG:37-165, UTL:36-203, PWR:51-257), no function block. PWR's clock-trust section (L152-180) and BESS dispatch (L217-248) are obvious blocks.
- **Tests.**
  - Compiled tests exist for BH (`sim/tests/test_plc_brewhouse.py`, `test_brewhouse_control_path.py`), CEL (`test_plc_cellar.py`), PKG (`test_plc_packaging.py`) and PWR (`test_plc_power.py`). They drive whole programs, not modules.
  - UTL has none, only regex checks (`tools/netcheck/tests/test_plcbuild.py:123-136`). F3 is in the untested program.

## Practice 2. Track operating modes

**Asks:** keep the PLC in RUN, alarm operators when it is not, re-alert at shift end.

- **Platform.**
  - OpenPLC v4 has no RUN/PROG switch.
  - State (EMPTY, INIT, RUNNING, STOPPED, ERROR) comes from `GET /api/status`.
  - Upload, start and stop need only the admin JWT (OpenPLC `docs/API.md`).
  - That path is a recorded EWS conduit (DESIGN.md:2098).
- **Nothing tells the HMI.**
  - The FUXA alarm list is the PLC alarm bits plus five `BATCH.*` alarms. There is no alarm for a stopped, errored or unreachable PLC.
  - The `openplc-program-running` probe (`soh.py:77-84`) is phenix infrastructure, not an operator alarm.
- **BH: Partly.** `BATCH.ALM_PLC` fires when the link is down for `comms_s` (`batch_engine.js:205-207`) or a running phase reads Idle (`409-411`), but only while a batch runs.
- **CEL, CI, PKG, UTL, PWR: Not met.** PKG's PackML "mode" (L70, 96-101) is a process mode.

## Practice 3. Leave operational logic in the PLC wherever feasible

**Asks:** totalizing, interlocks, timers, holds, permissives and alarm thresholds in the PLC.

- **All: Met for alarms.** Thresholds, delays and suppression are generated into the PLC. Each FUXA alarm binds to the PLC bit (`fuxa/full/scada-fuxa-project.json`: `variableId` = the ALM tag, min = max = 1).
- **CEL, CI, PKG, UTL, PWR: Met.** Interlocks and sequences are in the PLC:
  - CO2 inhibit (CEL:337-341), dry-hop (CEL:443-466), profile (CEL:147-179, 270-301);
  - entry lockout, OEE (PKG:135-147), CIP (UTL:136-200);
  - the whole power schedule, from the ATS clock (PWR:152-248).
- **BH: Partly.** The PLC runs one phase per Start. Sequence, phase timeouts, the "no fermenter empty and lined up" start refusal and the knockout destination reservation are in the FUXA script (`batch_engine.js:254-261, 347-377, 387-401`). See F4. `[V]` BH-6, BH-9.

## Practice 4. Use PLC flags as integrity checks

**Asks:** counters on error flags (divide by zero, counter overflow, negative preset, scan overrun).

- **All: Not met.**
  - No program counts an error condition.
  - No program has an array or pointer.
  - Divisions are guarded, so they cannot fault: BH:238 (`p_kpa > 10.0`), PKG:136 and 141, constants elsewhere.
  - A tripped guard takes a silent fallback. PKG:141-146 sets quality to 1.0 when the filler counter has wrapped below the reject count (PKG-5: OEE 900 becomes 1000).
- **Platform.** strucpp documents no math error flags (`docs/IEC_COMPLIANCE.md`, `docs/RUNTIME.md` in the strucpp checkout). The practice can only be met by counting guard hits and out-of-range writes.
- **Checked, no defect.** I tested a suspected integer wrap in `BH_KO_FT01_PV * 2 / 5` (BH:320) for inputs up to 65535 and found none (BH-11).

## Practice 5. Cryptographic or checksum integrity checks for PLC code

**Asks:** hash or checksum of the code, alarm when it changes.

- **Build time: Met.**
  - `defines.h` carries the MD5 of the assembled program (`plc/common/pack_zip.py:66`).
  - `sync.py:359-480` and `build_zip.sh --check` catch a stale ZIP.
  - `render.py:360-363` checks the OPC UA debug map against it.
  - I verified all six committed ZIPs: each `PROGRAM_MD5` equals the MD5 of what I assembled from the current sources.
- **Run time: Not met.**
  - Nothing reads the loaded program's hash. OpenPLC offers it (`DEBUG_GET_MD5`, JWT required), but the probe only greps for `STATUS:RUNNING` (`soh.py:84`).
  - OpenPLC itself states the stored project has "no integrity guarantee" (`docs/SECURITY.md`).
- **Network side.** Uploads from a non-EWS host or an upload-sized session are detected (Suricata 9000150/9000151, Zeek `PLC_Program_Upload_Suspected`, DESIGN.md:3013-3027). That shows an upload happened, not that the running code differs from the reference.
- **All six: Partly.**

## Practice 6. Validate timers and counters

**Asks:** validate presets written from outside, no direct HMI write to a timer, check backward counts.

- **BH: Partly.**
  - The only host-writable preset is `XC03.PARAM`.
  - Minutes are capped at 600 (BH:75, 163, 175, 181, 186). MILL kilograms are not: 65535 kg gives `limit_ms` 131,070,000 = 36.4 h (BH:170-171, BH-2).
  - Presets are latched at Start (BH:161).
- **CEL: Partly.** `PROFILE.KC01` is capped at 6000 (L267) with no lower bound: 0 skips the rest (MISC-1). Other presets are constants (L193-197).
- **CI: N/A.** No host-writable timer or counter (CI:93-100).
- **PKG, UTL, PWR: Met.** Nothing host-writable is a preset (PKG:47, UTL:53, PWR:55-74). Counters are guarded against underflow (PKG:84-85, 141-142; PWR:141-145, 171).
- **Machine counters** wrap at 65535 (LV packaging:12-13, 20-21). BH handles the panel counter's wrap in the engine (`batch_engine.js:186`). PKG only partly (PKG-5).

## Practice 7. Validate and alert for paired inputs and outputs

**Asks:** paired signals never together, alarm on infeasible I/O states, independent start/stop, restart delays, no rapid toggling.

- **BH: Not met.**
  - Mill `HS01_CMD` vs `YS01_PV` and rake `HS01_CMD` vs `YS01_PV` are never compared. The feedbacks are unreferenced (`logs/misc.log`).
  - Runoff and sparge valves are never compared with their flow transmitters.
  - No toggle delay on any output.
- **CEL: Not met.**
  - `FCV01_OUT` vs `FT01_PV`, spunding setpoint vs `ZSO01_PV`, and dry-hop valves vs `ZSC01_PV` are never compared (CEL-8).
  - The dry-hop purge and dose open with no hop-port-closed check (CEL:462-465).
  - The transfer pump command is hard-coded FALSE (CEL:469).
- **CI: Not met.** `ZSO01` x4 unread.
- **PKG: Partly.**
  - A conveyor runs only while its machine executes and its jam eye is clear (PKG:150-153).
  - The line command has no anti-toggle. A host alternating Start and Stop passes every change to every machine each scan: `[2, 3, 2, 3, 2, 3, 2, 3]` (PKG-1, PKG:81-95). Only the machines' own PackML state machines limit it.
- **UTL: Partly.**
  - Caustic and acid pumps are never on together. The pH hysteresis cannot overlap, and a one-scan jump from 6.40 to 8.60 gives caustic off, acid on (UTL-5).
  - CIP caustic and acid steps are exclusive by step number (UTL:193-194).
  - The one command-vs-feedback check, return pump vs flow switch, is broken (F3).
  - The CO2 valve command is never compared with `ZSO01` (F6).
- **PWR: Not met.**
  - The BESS has one signed setpoint, so charge and discharge cannot be asserted together (PWR:244-248). That is the single-output design the PDF prefers.
  - No command-vs-position check on feeders, 52-M, genset start or BESS enable (`PWR_MAIN_ZS02..04`, `PWR_BESS_ZS01_PV` never read, PWR-4).
  - No slew limit or dwell on `PWR_BESS_JC01_SP`.

## Practice 8. Validate HMI input variables at the PLC level

**Asks:** the PLC cross-checks every HMI-written value against its acceptable range, and either substitutes a default or last good value, or alerts.

All 49 tags with `scada_access = read-write` (`plant/generated/full/registers.csv`):

| Tag(s) | HMI range | PLC check | Result |
|---|---|---|---|
| BH `HLT.TT01.SP`, `MLT.TT01.SP`, `KO.TT02.SP` | 32-212, 32-212, 32-100 degF | `LIMIT` BH:118-120 to 32-205, 32-180, 32-100 | clamps `[V]` BH-1; HLT max 205 exceeds the alarm limit 190 (F7) |
| BH `XC01.PHASE` | 0-10 | start accepts 1-10 only (BH:158-159), latched | out-of-range ignored silently `[V]` BH-3 |
| BH `XC01.CMD` | 0-5 | `CASE` with no `ELSE`; register cleared (BH:150, 156-214) | other values dropped silently `[V]` BH-3 |
| BH `XC03.PARAM` | 0-65535 | minutes `MIN(,600)` BH:163; MILL kg unbounded BH:170 | 36.4 h mill `[V]` BH-2 |
| BH `XI01.BATCH`, `XC02.ACCEPTED` | 0-65535 | none (`XI01` unused; `XC02` copied to the panel, BH:133) | unvalidated |
| BH `ENTRY.HS01/HS02` | bit | none; drives the lockout (BH:129-130) | deliberate surface, F1 |
| CEL 8 fermenter `TT01/TT02.SP`, 2 BBT `TT01.SP` | 20-90 degF | `LIMIT` CEL:252-261 | clamps `[V]` CEL-1; implausible range (F7) |
| CEL `BBT20n.AT01.SP` | 0-5 vol | `MIN(,350)` CEL:262-263, then `MIN(,VOL_MAX)` CEL:391, 401 | clamps; temperature-dependent maximum `[V]` (94 at 90 degF) |
| CEL `PROFILE.TC01-03.SP` | 20-90 degF | `LIMIT` CEL:264-266 | clamps; no ordering check |
| CEL `PROFILE.KC01.SP` | 0-6000 min | `MIN(,6000)` CEL:267 | 0 skips the rest `[V]` MISC-1 |
| CEL `PROFILE.HS01-04.AUTO`, `DH.XC01.CMD` | bit | gated by entry and CO2 where it matters (CEL:445-466) | ok |
| CEL `ENTRY.HS01-06.ENTRY` | bit | none | deliberate surface, F1 |
| CI 8 `TT01/TT02.SP` | 20-90 degF | `LIMIT` CI:145-152 | clamps `[V]` CI-1 |
| PKG `PACKML.CMD` | 0-9 | accepted 1-9 only (PKG:81), then cleared (PKG:89) | invalid dropped `[V]` PKG-2 |
| PKG `PACKML.MODE_CMD` | 0-3 | `LIMIT(1,.,3)` PKG:70 | 0 becomes 1 `[V]` PKG-2 |
| PKG `PACKML.SPEED_SP` | 0-60/min | `MIN(,600)` PKG:69 | clamps `[V]` PKG-2 |
| UTL `CHL.TT01.SP` | 0-100 degF | `LIMIT(200,.,400)` UTL:86 | 20-40 degF `[V]` UTL-4 |
| UTL `GLY.PT01.SP` | 0-100 psig | `MIN(,1000)` UTL:87 | 100 psig drives the pump to 100% `[V]` UTL-4 |
| UTL `CIP.TT01.SP` | 32-212 | `LIMIT(320,.,2000)` UTL:88 | 32-200 degF `[V]` UTL-4 |
| UTL `CIP.XC01.CIRCUIT` | 0-8 | `MIN(,7)` UTL:89 | 8 and 9999 select circuit 7 `[V]` UTL-3 |
| UTL `CIP.XC01.CMD` | 0-3 | start and abort handled (UTL:165-176); hold is a level | other values stay in the register, ignored |
| UTL `CO2.XC01.RESET` | bit | accepted only after 60 s healthy (UTL:111-116) | ok |
| PWR `DMD.JY01.SP` | 0-1000 kW | `LIMIT(50,.,600)` PWR:126 | clamps `[V]` PWR-1; 600 kW disables cap discharge |
| PWR `DMD.EY01.SP` | 0-100 % | `MIN(,100)` PWR:127 | clamps `[V]` PWR-1 |
| PWR `DMD.XS03.BREWDAY` | bit | none, no timeout (DESIGN.md:4400: nothing sets it yet) | ok |

Beyond the 49: the Modbus and S7 servers let a host write every coil and holding register. Values the program reads back as state and does not reassign each scan are the dangerous ones:
- the latches and permits in F1;
- `PWR_DMD_JY02_PV`, the peak (PWR:251-254): a write of 65535 freezes it and a write of 0 resets it (PWR-3);
- `BH_BATCH_XS01_STATE` and `BH_KTL_KI01_PV` (read-only over OPC UA).

**Rating.**
- Met where every writable value is bounded to the range the HMI uses (CEL, CI, PKG, PWR). The PDF's "prevent, or alert on" is satisfied by clamping.
- Partly for BH (unbounded MILL parameter; unvalidated `XC02` and `XI01`) and UTL (a saturated selector; a wide pressure bound).
- No program substitutes a default or last good value, and none alerts. Out-of-range writes leave no trace.

## Practice 9. Validate indirections

**Asks:** poison array ends, validate computed indices and selectors.

- **No program has an array, pointer or `REF_TO`** (static scan of all six).
- **Computed selectors:**
  - BH: `CASE cmd` (BH:156) and `CASE active` (BH:166, 273) have no `ELSE`. `active` is validated 1-10 before latching (BH:158-160). `cmd` is unvalidated and silently cleared.
  - CEL: `CASE dh_step` has `ELSE` (CEL:459-460) and is internal.
  - UTL: both CIP `CASE`s have `ELSE` (UTL:146, 158). The selector is saturated first (UTL:89, see practice 8).
  - PKG, PWR, CI: no selector.
- **Ratings.** BH Partly. CEL and UTL Met. CI, PKG and PWR N/A.

## Practice 10. Assign designated register blocks by function

**Asks:** separate blocks for read, write, validate and calculation, so unauthorized or out-of-bounds writes cannot reach controller data and a firewall can say "read only".

- **Layout.**
  - Variables are located in generated order, so setpoints, computed values, alarms and state sit side by side.
  - Cellar PLC memory: 69 coils, 11 writable by design (6 permits, 4 AUTO bits, the dry-hop request) and 58 alarm, state and latch bits. 23 holding registers `%MW0-22`, 16 writable by design and 7 computed or stage values.
  - The Modbus server serves 816 coils and 129 holding registers, all writable (`modbus_slave.json`: `qx_bits` 816, `qw_count` 106, `mw_count` 23).
  - UTL: 816 coils, 110 registers, 6 writable tags. PWR: 808 coils, 105 registers, 3 writable tags.
  - PKG's S7 plugin exposes five data blocks over the whole `%QW`, `%IW`, `%MW`, `%IX` and `%QX` images (`render.py:267-300`). Whether the input blocks reject writes is unverified (`render.py:273`, TODO).
  - The RIO output image (`%QX100+`, `%QW100+`) is inside the exposed range on every Modbus PLC. DESIGN.md:2730 states the exposure.
- **Platform.**
  - The OpenPLC Modbus slave maps all coils and registers with all write function codes. It has no read-only or per-range option (OpenPLC `core/src/drivers/README.md`, "Modbus TCP Slave").
  - The firewall is port-granular (DESIGN.md:2077-2108).
  - The only lever in the repo is how the PLC lays out and validates writes. The PDF's pattern (a write block the PLC validates and copies into live variables) is not used.
- **BH: Partly.**
  - The OPC UA config makes every node read-only except the 10 read-write tags for the operator and engineer roles (`render.py:197-210`).
  - Anonymous sessions get the operator role (`render.py:243`, deliberate, DESIGN.md:4422).
  - This is the one real write allow-list in the repo `[C]`. DESIGN.md:4417 says the runtime's enforcement is untested.
- **CEL, CI, PKG, UTL, PWR: Not met.**
- **Self-healing.** Most computed registers are reassigned every scan, so an outside write lasts one scan (CEL-7). Exceptions are listed under practice 8.

## Practice 11. Instrument for plausibility checks

**Asks:** cross-check different measurements.

- **BH: Partly.**
  - Two independent level switches act as permissives: HLT steam only while `LSL01` is made (BH:230-234, fail-safe sense), and CLT fill stops on `LSH01` even when `LT01` reads low (BH-5).
  - The disagreement raises no alarm.
  - Boil detection uses barometric pressure and one temperature (BH:237-243).
- **CEL, CI, PKG, UTL: Not met.** Instrument pairs exist and are unused (F6).
- **PWR: Partly.**
  - The ATS clock is validated (PWR:152-180, `test_plc_power.py`).
  - Day and month are never cross-checked. Changing only the month register from 7 to 8 resets the billing peak and the clock stays trusted (PWR-3, PWR:186-195).
  - Site import is not compared with feeder, BESS or PV readings.

## Practice 12. Validate inputs based on physical plausibility

**Asks:** limit entry to what is practical, time operations and alarm on deviation, alarm on inactivity.

- **Limits.** Bounded to instrument ranges, not process ranges, in all six (F7). The physical ones: packaging speed at most 60/min (PKG:46, 69) and glycol supply 20-40 degF (UTL:40-41).
- **Durations and inactivity.**
  - CIP wash steps are fixed 300 s timers with a hold (UTL:53, 181-189). The mill, vorlauf, whirlpool and hop dose have timers (BH:68-72).
  - No program has a step-timeout alert, a valve travel time, or an inactivity alert.
  - A stuck PV is never detected (CEL-6: PV frozen at setpoint for 3 h, no alarm).
  - Phases waiting on a process value have no PLC timeout (F4).
  - The only inactivity-like alert is the low-priority packaging "Held for 10 minutes" (`PKG.LINE.PACKML.ALM_STATE`).
- **Done well.** The carbonation target is clamped to the highest value the head pressure can reach (CEL:105-107).
- **All six: Partly.**

## Practice 13. Disable unneeded communication ports and protocols

**Asks:** list the required protocols per port, disable the rest.

- **Plugins: Met.** Each ZIP enables only `modbus_master` plus the one northbound plugin the PLC needs (checked in the committed ZIPs and `render.py:358-403`). A plugin with no config is disabled (DESIGN.md:2433-2434).
- **Port-level firewall: Met.** Each PLC is reachable only on its own protocol port from SCADA and the EWS, plus TCP 8443 from the EWS (DESIGN.md:2077-2108, `conduits.py:276-352`).
- **OPC UA profile: Deliberate.** One profile: policy None, mode None, anonymous, role operator (`render.py:231-243`, DESIGN.md:4422).
- **Open on every PLC `[C]` `[I]`:**
  - `sshd` is installed with `PermitRootLogin yes`, `PermitEmptyPasswords yes` and a deleted root password (`phenix-images/cm-openplc.yml:105-112`).
    - Recorded as an accepted lab-only risk (DESIGN.md:3528-3539).
    - DESIGN says `fw-ot` passes TCP 22 only from dmz-jump to the EWS.
  - Intra-zone CTRL traffic is not filtered (DESIGN.md:2108). I found no host firewall rule for a PLC's CTRL interface. The nftables policy in `render.py:1852-1930` is for the MGMT interface.
  - OpenPLC's REST API on 8443 and its WebSocket debug interface (variable inspection and forcing, behind the admin JWT) are on every PLC. A UDP discovery reply on 33333 carries the stored project name without authentication (`docs/SECURITY.md`). OpenPLC has no option to turn these off.
  - `tcpdump`, `ncat`, `vim` and `build-essential` are in the image. The compiler is needed because the runtime compiles uploaded programs (`cm-openplc.yml:63-65`).
- **Ratings.** Partly for all. BH adds Deliberate for the anonymous, unencrypted OPC UA profile.

## Practice 14. Restrict third-party data interfaces

**Asks:** limit connection types and data for third parties, watchdog on communications.

- **Connections.**
  - The third parties here are the vendor (WireGuard, Guacamole, EWS, then PLC REST), the utility (DNP3 to rtu-der) and SCADA.
  - Connection types are restricted per source and port (`conduits.py:276-352, 489-542`, DESIGN.md:2077-2108).
  - The firewall cannot restrict function codes or registers.
- **Data.** Not restricted on the Modbus and S7 PLCs (practice 10). Restricted per node on the brewhouse OPC UA server (anonymous operator, deliberate).
- **Utility.**
  - `rtu-der` exposes breaker commands and setpoints to the utility master (DESIGN.md:4401, open).
  - plc-power reads the utility's PV and BESS limits (LV power:29, 38) but never uses them (PWR-4 list), so it has no dependency on that interface.
- **Watchdog.** No PLC watches its RIO or peer for staleness (recorded, DESIGN.md:4402-4403). Only the batch engine watches the brewhouse PLC (`batch_engine.js:205`).
- **Partly for all.** BH adds Deliberate. Detection of writes from a non-SCADA, non-EWS host is a recorded use case (DESIGN.md:3030).

## Practice 15. Define a safe process state in case of a PLC restart

**Asks:** define the state (energize, de-energize, keep). If not practical, alert and issue no new commands. Document the procedure.

- **BH: Partly.**
  - First scan resets batch state, phase and command (BH:109-117). Phase outputs default off each scan (BH:251-270). Setpoints reload to 168/152/64 degF.
  - `[V]` BH-4: a running MILL reads Idle, mill off, VFD reference 0, and HLT setpoint 150 returns to 168.
  - The PLC does not report the interrupted batch. FUXA reads it only while it runs a batch.
  - Permits are lost (F1).
- **CEL: Partly.**
  - Defaults load (CEL:226-250). The CO2 inhibit starts TRUE for 60 s (CEL-5). Stage is re-derived (CEL:147-157). The restart effects are recorded at DESIGN.md:598-600.
  - Before the first RIO poll all inputs read 0, so all four spunding setpoints are written at 12 psig (24.10 psia) for the first scan (CEL-5).
  - Permits are lost (F1).
- **CI: Partly.** Setpoints reload to hard-coded defaults (CI:132-142). Nothing else to lose.
- **PKG: Partly.** No command is issued at start, but mode and speed reset to Production and 30/min and are pushed to every machine on the first scan (PKG:63-68, 96-106, PKG-4: 2/450 become 1/300).
- **UTL: Partly.**
  - CIP aborts to step 0. Chiller, glycol, air and RO enables return at once (UTL:92-101).
  - The CO2 latch is lost (F1).
  - Before the first poll the pH input reads 0, so the caustic pump is commanded on for the first scan (UTL-8, UTL:121-125).
- **PWR: Partly (recorded).**
  - The PLC sheds for its first scans and recloses boiler, packaging and RTU-1 at 61, 122 and 183 s `[V]` (PWR-2).
  - DESIGN.md:4400 records this and the lost peak, exercise day and rollover state.
- **Platform `[I]`.** See F2. The restart behavior the programs assume ("RIO keeps the last command") may be wrong.

## Practice 16. Summarize PLC cycle times and trend them on the HMI

- **All: Not met.**
  - No program publishes a scan statistic.
  - The task interval is fixed at 100 ms (`plcbuild.py:41-44, 172-180`), and `plcbuild.py:190-192` makes a scan-counting program declare the same interval.
  - BH:346-348, CEL:164-167 and PKG:47 count scans, so timed steps are only as accurate as the scan.
- **Platform.** OpenPLC logs scan time, cycle time, latency and overruns every 5 s and has a stuck-task watchdog (10 missed periods). None is readable by a program or the HMI. A program can only publish a heartbeat counter (F9).

## Practice 17. Log PLC uptime and trend it on the HMI

- **All: Not met.**
  - Every program has a first-scan flag (`started`: BH:87, CEL:201, PKG:53, UTL:62, PWR:78, CI:109) that is neither counted nor exposed.
  - No uptime or restart tag exists in `plant.yaml`.
  - OpenPLC v4 has no SNMP and no uptime variable.
  - FUXA sees a restart only through the batch engine (F9).

## Practice 18. Log PLC hard stops and trend them on the HMI

- **All: Not met.**
  - OpenPLC records a fault by writing `/run/runtime/watchdog_fault`, booting into safe mode and reporting ERROR (`docs/ARCHITECTURE.md`). The runtime log holds the events.
  - Nothing forwards them. There is no syslog from the PLCs, no Wazuh agent on a PLC host (no conduit from CTRL, DESIGN.md:2104-2108; `conduits.py:529-530` blocks PLCs initiating upward), and no tag.
  - Time sync: the image carries `chrony` (`cm-openplc.yml:28`). I did not check that it is configured.

## Practice 19. Monitor PLC memory usage and trend it on the HMI

- **All: Not met.**
  - No memory baseline or trend for a PLC.
  - The programs allocate nothing dynamically (no arrays, no strings).
  - DESIGN.md:2460-2466 measured the runtime at 161 MiB steady and 361 MiB peak for a three-variable program and records that a brewhouse-size compile is unmeasured. That is a compile measurement, not a run-time trend.
  - OpenPLC exposes no memory figure to a program.

## Practice 20. Trap false negatives and false positives for critical alerts

- **BH, CEL: Partly.**
  - The entry-violation alarm traps a false negative of the outputs: a locked output found on at the top of a scan was written from outside (BH:124-130, 143-149; CEL:310-321).
  - `[V]` It works: a coil write to a locked valve latches the Critical alarm (CEL-4, BH-10).
  - Two gaps `[V]`: the latch is itself a writable coil, so one write erases it. Withdrawing the permit raises nothing.
  - There is no false-positive trap anywhere.
- **CI, PKG, UTL, PWR: Not met.**
  - The Critical BESS off-gas alarm is computed from one input with no on-delay and no hold (F8).
  - The CO2 shutoff state alarm is latched, but the latch is erasable (F1).
- **All.**
  - The alarm bit is assigned from the block's output every scan, so an outside write lasts at most one scan `[V]` (CEL-7). A 1 s poller can see that window (F8).
  - OpenPLC's debug interface can force variables (README, "WebSocket Debug Interface"). A forced alarm bit has no trap either.

---

# 3. Deliberate and recorded weaknesses

- **Decision:** the range owner chose it.
- **Open:** DESIGN.md lists it as unfinished work, not a decision. I treat it as recorded, do not count it as a new finding, and still rate the practice by the facts.
- **Documented limit:** stated in DESIGN.md as how the system is.

§19 table rows have no numbers, so I cite the DESIGN.md line.

| Item | Where recorded | Kind | Practices | Notes |
|---|---|---|---|---|
| Anonymous OPC UA session on plc-brewhouse gets the operator role. Any host reaching 4840 can write the brewhouse's 10 read-write tags | §19, DESIGN.md:4422 | Decision | 8, 10, 13, 14 | `render.py:231-243`; those 10 nodes are the only writable ones |
| Confined-space lockout lives in the PLC. A stopped PLC or a write straight to the RIO bypasses it | §19, DESIGN.md:4404; §6.8, DESIGN.md:925-927 | Decision (accepted) | 3, 15, 20 | F1 is the case this row does not cover |
| `modbus_master` keeps the last input on a lost RIO link. RIO heartbeat not built | §19, DESIGN.md:4402; §6.7, DESIGN.md:823, 828; DESIGN.md:3685-3689 | Open | 3, 7, 11, 14, 15 | `keep-last-value` verified for every RIO and device in `modbus_master.json`; peers use `set-to-zero` |
| CO2 shutoff latch may miss a trip while rio-utilities is unreachable. Brewhouse CIP circuit is not interlocked to brewhouse permits | §19, DESIGN.md:4403 | Open | 3, 7 | `[V]` UTL-7: circuit 1 route OK with every peer bit 0 |
| plc-power: peak, exercise day and rollover state not retained. Sheds at start, recloses feeders 60 s apart | §19, DESIGN.md:4400 | Open | 10, 15 | `[V]` PWR-2: 61, 122, 183 s |
| Batch engine has not met OpenPLC. Phase timeouts are estimates | §19, DESIGN.md:4417 | Open | 3, 12 | F4 adds that the PLC has no timeout of its own |
| rtu-der DNP3 map exposes breaker commands and setpoints to the utility master | §19, DESIGN.md:4401 | Open (detected) | 14 | field-device level, not in the PLC programs |
| Modbus and S7 writes from a host other than scada-fuxa or the EWS are a detection use case. The PLC image is writable by protocol | §14, DESIGN.md:3030; §12.5, DESIGN.md:2730; §10.3, DESIGN.md:2108 | Documented limit (detect, not prevent) | 8, 10, 14 | the layout (state beside setpoints) is not discussed |
| Clearing a permit unlocks an occupied vessel (Suricata 9000173, Zeek `Life_Safety_Write` detect it) | §6.8, DESIGN.md:921-924 | Documented limit | 10, 20 | detection covers permits and the CO2 reset coil only (`cherrymountain.zeek:135-144`) |
| Passwordless root console and root SSH with empty password on every image, PLCs included | §15.7, DESIGN.md:3528-3539; §13, DESIGN.md:2867-2872 | Decision (accepted lab-only risk) | 13 | `cm-openplc.yml:105-112` |
| The ci program has alarm on-delays only (no deadband, off-delay, suppression) | §6.5, DESIGN.md:755 | Documented limit | 20 | effect shown in CI-2 |
| A restart during a diacetyl rest repeats it. A restart early in a crash warms the beer for another rest | §6.3, DESIGN.md:598-600 | Documented limit | 11, 15 | the same effect from one input glitch (F6) is not recorded |
| Neither the PLCs nor FUXA mark a stale value (TODO: heartbeat, tag age) | DESIGN.md:3685-3689 | Open | 11, 14, 15 | |
| None of the five programs has run in an OpenPLC runtime | `plc/common/README.md:30-31`; §19, DESIGN.md:4398 | Documented limit | all | limits what I could verify |
| Peer permit reads 0 on a lost plc-cellar (fail-safe) | UTL:26-29 (header comment); `plantgen/render.py:820` | Design, not a weakness | 15 | included to show the rating |

---

# 4. What I could not evaluate, and why

- **OpenPLC runtime behavior.**
  - No runtime was available, and the repo has never run these programs in one.
  - Not verified:
    - that Modbus, OPC UA and S7 writes land in the image as I assumed;
    - that the OPC UA node permissions are enforced as `render.py:197-210` configures;
    - that the S7 input blocks reject writes (`render.py:273` TODO);
    - that a located `%QX` marked `RETAIN` survives a restart;
    - what a stop does to `%Q` (F2);
    - which sockets the runtime and image actually open. I know `sshd`, 8443, UDP 33333 and the plugin ports from the image config and vendor docs, not from a listener list.
  - All of these need one VM run.
- **Closed-loop plant behavior.**
  - I scanned the compiled programs with hand-set inputs.
  - I did not run them against the federates, and I did not re-run the repo's pytest suites because the `cherrymountain_sim` package is not installed here.
  - My results show what the program does with an input, not whether the simulated plant would then reach the bad state.
- **FUXA at run time.**
  - I read the generated projects and the batch engine, not a running FUXA.
  - How FUXA treats a bit that is true for less than a poll, and whether it shows a disconnected device as an alarm, is inferred from the project (polling 1000 ms, alarm bindings).
  - The FUXA builder was being rewritten by another process during the review.
- **Network controls.** I read `conduits.py`, DESIGN §10.3 and `render.py`. I did not run `netcheck`, any VyOS ruleset, Zeek or Suricata.
- **Process limits.** The "process ranges" in F7 are reasonable engineering numbers, not values from the plant's data sheets. The HLT boiling limit comes from the program's own boil model at 12.1 psia. The real tolerances are for the range owner.
- **PDF figures.** I did not view the diagrams for practices 16 and 20. The text states the method fully, so I do not expect a difference.
- **Practices 16 to 19.** They are rated from the absence of anything in the programs, the FUXA project and the image. I did not look for a monitoring path outside the repo (a historian query, a Wazuh decoder on runtime logs) because none is referenced.

---

# Appendix: evidence

The review compiled each program with the pinned strucpp and g++ through
`sim/tests/st_engine.py` and scanned it with hand-set inputs, in about 60 scenarios
(the BH-n, CEL-n, PKG-n, UTL-n, PWR-n, CI-n, MISC-n, and FIX-n labels above). Those
scripts and their logs were scratch files and are not in the repository. The
scenarios behind the findings that were then fixed are now tests (see "Since the
review" at the top); the others can be rebuilt from the descriptions with the same
harness, as `sim/tests/test_plc_utilities.py` does.

Checks that found nothing wrong:
- Out-of-range writes to every analog setpoint (all clamp as coded).
- Invalid brewhouse and packaging commands (dropped, no side effect).
- UINT arithmetic at BH:320 (no wrap).
- Caustic and acid pumps are never on together.
- CO2 interlock inhibit timing.
- The cellar peer permit failing safe.
- The ATS clock trust sequence. I read it; `test_plc_power.py` covers it.
