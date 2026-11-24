# cherrymountain_sim

This package is the CherryMountain Brewery simulation
([DESIGN.md §9](../docs/DESIGN.md#9-simulation-and-co-simulation)). It has one
HELICS federate per process area. A federate is one simulation program in a
HELICS co-simulation. The package also has the process, power, and building
models behind the federates, and the sim-ctl event API. Every federate publishes
and subscribes exactly the keys that plantgen lists in
`plant/generated/<variant>/helics.json`.

```shell
pip install -e '.[helics,power,test]'
python -m pytest
cherrymountain-sim --config ../plant/generated/full/helics.json --federate brewhouse
cherrymountain-local --config ../plant/generated/full/helics.json --hours 2   # all federates, no HELICS
```

`python -m cherrymountain_sim` is the same as `cherrymountain-sim`. Its default
federate is `cellar`, so the existing `cherrymountain-cellar` command of the ci
slice still works.

## Federates

| Federate | Host | Step | Module | Models |
|---|---|---|---|---|
| `weather` | sim-ctl | 1 s (weather changes each minute) | `weather.py`, `simctl.py` | EPW (TMY3) or synthetic Denver weather, barometric correction to 1,585 m, groundwater curve, the event API, KPIs |
| `brewhouse` | sim-process | 1 s | `brewhouse.py`, `brewing.py`, `recipe.py` | Mill, CLT/HLT, mash (two-enzyme kinetics), lauter, kettle with the local wort boiling point and live Tinseth/mIBU bitterness, two-stage ε-NTU knockout HX, O₂ |
| `cellar` | sim-process | 1 s (kinetics 48x) | `federate.py`, `cellar.py`, `fermentation.py`, `thermal.py`, `carbonation.py` | de Andrés-Toro fermentation, glycol jackets, tank spunding, knockout filling, bright tanks with Zahm-Nagel carbonation, CO₂ releases |
| `utilities` | sim-process | 1 s | `utilities.py` | Glycol reservoir and two-stage chiller, steam boiler, air, bulk CO₂, RO, wastewater pH, CIP |
| `packaging` | sim-process | 1 s | `packaging.py`, `packml.py` | SimPy bottling line and keg filler, PackML machines, jams, starve/block, filler bowl interlock, OEE |
| `power` | sim-power | 1 s | `power.py`, `tariff.py` | OpenDSS 480 V feeder, breakers, ATS and genset island, PV and BESS (IEEE 1547 trip and return), SV-SGT meter and bill |
| `building` | sim-building | 10 s | `building.py` | Taproom 3R2C with RTU-1 and CO₂, cold room 1R1C, cellar CO₂ mass balance, hood exhaust and MUA |

Control authority: Until a PLC writes, the brewhouse, utilities, and packaging
federates run a built-in operator, the autopilot (value `autopilot: auto` in the
sim configuration). No PLC writes in the offline harness without a PLC, or in a
range whose PLC did not start yet.

The built-in operator runs a double-brew day at 05:00 on brew days. It holds the
utilities at their setpoints, and it runs the packaging shift 06:00-14:30 on
weekdays. The first PLC write hands control over. The value `on` keeps the
autopilot, and `off` never runs it.

The power and building federates use per-point authority instead. A point that
the PLC, the DER gateway, or a BACnet client wrote follows its last write. The
other points follow the built-in controller. The built-in controller is the
plc-power dispatch of DESIGN.md §7.3 and the local logic of the BAS controllers.

The autopilot of the cellar runs the recipe profile by kinetic hours since pitch
(the addition of the yeast). The profile is 66 °F, 70 °F and sealed spunding
from day 4, and a 34 °F crash (a fast cool-down) from day 10. With sealed
spunding, the tank keeps its fermentation gas under pressure. The autopilot
holds the bright tanks at 34 °F. It carbonates toward 2.6 vol only while the CO2
relay (`building/vent.co2_relay_ok`) is healthy. On its first write, plc-cellar
takes over.

Who starts a brew day: The `start-batch` event and the 05:00 schedule of a brew
day are a request for brews (`brews`, default 2). When no PLC writes, the
request goes to the autopilot. When plc-brewhouse drives, the federate raises
the request counter of the brewhouse panel (`panel.requests`,
`BH.BATCH.XI02.REQUESTS`). The FUXA batch engine (`fuxa/README.md`) reads the
counter through the PLC and queues the brews. It answers with the count that it
accepted (`BH.BATCH.XC02.ACCEPTED`). The PLC echoes that count to the
`panel.accepted` of the panel.

If the engine never answers (FUXA is down), the request stays at the panel. A
warning for the request goes to the log after 5 minutes, and the request shows
as `batch.panel.pending` in the ground truth. The ground truth is the
simulation's record of the true plant state. When the engine returns, it takes
the request. The autopilot does not adopt the request, even if the PLC stops.
The counters are 16-bit and wrap.

Who starts the packaging shift: When no PLC writes, the built-in operator runs
the shift (06:00 to 14:30 on weekdays) and commands the machines itself. The PLC
plc-packaging only relays a line command. Thus, when it drives, the line
operator of the federate works the shift at the operator panel at the filler
(`linepanel.cmd`, `PKG.PANEL.HS01.PV`). The panel point holds the PackML code of
the pressed button. The operator holds each press 3 s, and the presses are at
least 5 s apart. The PLC takes each press as one line command.

- When the shift starts: Reset, then Start. If a machine is Aborted, Clear comes
  first.
- When the shift ends: Stop, if the shift of this operator is the one that runs.
  If someone started the line from the HMI outside the shift, the operator lets
  it run.
- During the shift, when the line stood Stopped, Aborted, or Idle for
  `operator_react_s`: the same start sequence. This time is a value in the sim
  configuration. Its default is 120, and 0 turns this reaction off. After a
  power cut, the time runs from the return of the power.
- Never: undo a Hold or a Suspend. A jam and a low bowl clear themselves, and a
  commanded Hold is somebody's choice.

The ground truth records who commands the line (`packaging.operator`:
`autopilot`, `panel`, or `none`) and the count of presses
(`packaging.panel_presses`).

Who moves the beer: The cellar crew (`crew.py`, value `crew` in the sim
configuration) does the hand work, with or without a PLC. No PLC point does it.

| Job | When | What happens |
|---|---|---|
| Rack a fermenter into BBT-201 | The beer is `ready_age_h` (264) kinetic hours old, both zones are at or below `ready_temp_f` (40), and BBT-201 is empty and clean. The oldest ready fermenter goes first | 10 minutes of set-up (`setup_min`), then 30 gal/min (`pump_gpm`) with the pump in Hand. 7 % stays behind (`rack_leave_pct`), and the crew dumps it. The beer keeps 80 % of its equilibrium CO2 |
| Move BBT-201's beer to BBT-202 | BBT-201 is bright (`bright_vol` 2.4, `bright_temp_f` 38) and BBT-202 is at or below `refill_below_pct` (10) | The same set-up and rate, until BBT-201 is empty or BBT-202 is full |
| Clean an emptied fermenter or BBT-201 | It is empty and not clean | 45 minutes by hand (`clean_min`). The crew does not use the CIP skid |

The crew does one job at a time and never changes a setpoint. To rack a
fermenter is to move its beer off the yeast. Somebody must crash a fermenter
before the crew racks it. The built-in operator does this from day 10, and the
fermentation profile of plc-cellar does it after a three-hour rest.
`crew: { enabled: false }` turns the crew off.

The points of the transfer panel show the work (`route_ok`, `pump_running`,
`flow_gpm`), and the ground truth has it under `crew` and `bright_tanks`.
`knockout_lost_bbl` counts wort that arrived when its fermenter was full. The
reason is that a knockout (the transfer of wort to a fermenter) keeps to the
fermenter that it started in.

The supply of the filler is `cellar/beer.supply_psia`. Its value is the head of
the bright tank whose outlet is open and that holds beer, and 0 otherwise.
Without product, the filler holds on its low bowl. It unholds when a tank is on
line again.

What stays manual with a PLC in control: A PLC writes field points. Some jobs
have no field point, so the model does them when the state of the plant calls
for them:

- Brewhouse: The brewer adds the boil hops of the recipe by the boil time left.
  The brewer adds the whirlpool hops when the PLC opens the hop doser. The
  brewer grains out (removes the spent grain) after the mash ran off and the
  kettle is on steam for the boil. The brewer dumps the trub (the sediment) that
  stays in the kettle when a knockout stops at or below 6 %. For comparison,
  plc-brewhouse ends KNOCKOUT at 5 %. The model also counts a brew at that
  moment.
- Packaging: A starved or blocked machine unsuspends when it has a bottle to
  take or room to put one. The line operator clears a jam and unholds the
  machine when the time of the jam is up. The filler unholds from a low bowl
  when the beer line has pressure again. With the product valve shut, the filler
  runs 10 s on what its bowl holds. That gives plc-packaging time to open the
  valve after it reads Execute. A commanded Hold or Suspend stays.

The start of a batch (the phases of plc-brewhouse) stays with whoever commands
the PLCs. The same is true for the start, stop, and reset of the packaging line.
After a PLC writes, no part of the simulation does these jobs.
`tests/test_plc_brewhouse.py` and `tests/test_plc_packaging.py` run both
federates against Python ports of the two PLC programs.

Time: Everything runs in wall time except the fermentation kinetics of the
cellar, which run 48x faster. Exported fluxes stay real-time-equivalent
(DESIGN.md §9.4). The clock starts at the `clock.start` of helics.json (a summer
weekday, 04:30). `clock.speedup` moves the calendar faster than the physics.

There are two clocks. The `clock.sim_hours` of the cellar (`CEL.CLK.KI01`) is
the fermentation recipe clock. It counts kinetic hours since the start, 48x
compressed, and it is not a date.

The calendar is `SimClock`. It is `clock.start` plus federate time times
`clock.speedup`, with a fixed UTC offset, so it has no daylight-saving shift.
The weather federate publishes it as `weather/clock.epoch_s`, and the tariff
meter bills by it. The ATS controller (`gen-ats`) shows it to plc-power as three
16-bit registers (`power.ClockRegisters`):

- `clock_min` is the minute of the local day (0-1439).
- `clock_day` is the local date as days since 1970-01-01. It is monotonic, so a
  reader can tell a stopped or jumping clock from a running one. The weekday is
  `(day + 4) mod 7`, where 0 = Sunday.
- `clock_month` is the billing month (1-12).

The registers are `PWR.GEN.KI01` to `KI03` in `plant.yaml`. From them, plc-power
schedules the SV-SGT on-peak window, the brew-day reserve, the monthly demand
reset, and the weekly genset exercise. It distrusts them when they stop or jump
(`plc/plc-power/plc-power.st`).

## Event API (sim-ctl)

The weather federate serves the API on `127.0.0.1:8080`, and the autotunnel
forwards from there. The contract is that of the client of the `cherrymountain`
app, `phenix_apps/apps/cherrymountain/simctl.py` in sceptre-phenix-apps:

| Request | Reply |
|---|---|
| `GET /api/v1/health` | `200 {"status", "variant", "sim_time", "step_age_s"}`. `status` is `ok` while the federate steps. It is `starting` before the first step. In that state, the HELICS federation did not start yet, because the broker still waits for a federate. It is `stalled` when the last step is more than 10 s old. The API answers in all three states, so look at `status`, not only at the 200 |
| `POST /api/v1/events` (or `POST /events`) with `{"event": <kind>, ...}` | `202 {"accepted": true, "id", "event", "params", "target", "received"}`, `400 {"error"}` for an unknown kind or bad parameters |
| `GET /api/v1/events` | `200 {"events": [...]}` |
| `GET /api/v1/kpis` | `200 {"sim_time", "batches_completed", "batches_in_progress", "bottles_packaged", "kegs_packaged", "energy_kwh", "demand_kw", "peak_demand_kw", "ttb_bbl_packaged", "fill_mean_floz", "fill_under_mav_rejects", "wastewater_inhibit_events", "cip_runs_verified", "cip_runs_unverified", "genset_hours_year", "genset_test_hours_year"}` |
| `GET /api/v1/state` | `200 {"seq", "t_s", "sim_time", "wall_time", "variant", "values"}`: the ground-truth snapshot that cm-viz reads (every key of plant.yaml `snapshot`, by HELICS key) |

The table lists the events, with the federate that applies each. A `duration` is
in seconds, more than 0 and at most 86,400:

| Event | Parameters | Federate |
|---|---|---|
| `start-batch` | `brews` 1-4 (default 2), `batch_id`, `recipe` (`fire-of-the-phenix`). The event queues brews for the autopilot. With plc-brewhouse in control, it raises the request counter of the panel, and the FUXA batch engine starts the brews | brewhouse |
| `utility-breaker` | `action`: `open` or `close` (required) | power |
| `genset-exercise` | `action`: `start` (default) or `stop`. `load_bank_kw` 0-100. `duration` (default 1,800) | power |
| `bess-thermal` | `action`: `start` (default) or `clear` | power |
| `chiller-fault` | `action`: `fail` (default) or `clear`. `compressor` 1 (default) or 2 | utilities |
| `refrigerant-leak` | `action`: `leak` (default), `repair`, or `recharge`. `rate`: `low`, `medium` (default), `high` | utilities |
| `cip-panel` | `circuit`: `bh`, `fv101` to `fv104`, `bbt201`, `bbt202`, or `none` (the default for a direct POST). The `plantevent` component requires this parameter | utilities |
| `co2-release` | `fv` (required, for example `FV-102`). `rate`: `low`, `medium`, `high` (default). The rates are 2, 10, and 40 g/s. `duration` (default 600) | cellar |
| `co2-leak` | `zone`: `tank`, `filler`, or `draft` (required). `rate` as above. `duration` (default 600) | building |
| `gas-detection` | `action`: `fail` (default) or `restore` | building |
| `jam` | `machine`: `rinser`, `filler`, `coder`, `labeler` (default), `packer`, `kegger`. The API accepts `capper` and `depalletizer` as names for the filler and the rinser. The `plantevent` component offers those two, `rinser`, `filler`, `labeler`, and `packer`. `duration` (default 300) | packaging |
| `weather-step` | `delta_f` -60 to 60 (required). `duration` (default 0: hold until the next step) | weather |

The weather federate forwards each event to `<target>/updates` as
`{"event": {"type", "params", "id"}}`. The API accepts an event (202) while the
federation does not run, and the target federate applies the event when the
federation starts.

## Ground truth

With `ground_truth` in helics.json, every federate writes its published values
and model state every 10 s to the in-experiment OpenSearch. The index is
`cherrymountain-truth-<federate>-YYYY.MM.DD`, and the federate uses the `_bulk`
API. A background thread does the writes (`groundtruth.py`).

- Time: `@timestamp` and the date in the index name are the wall clock (UTC),
  which is the clock that Malcolm and Wazuh use. As a result, the records line
  up without a conversion, and the default range of Grafana shows the run.
  `sim_time` holds the simulated date and time, and `t_s` holds the federate
  seconds.
- Mapping: Before its first write, each writer puts the index template
  `cherrymountain-truth-cm` for `cherrymountain-truth-*`. The template has
  dates, keywords for `federate`, `source`, and `field`, and doubles for every
  number. It also covers the documents that the ground-truth module of ot-sim
  can write (`cherrymountain-truth-YYYY.MM.DD`, one per point per second).
  OpenSearch refuses the template that this module brings. The range does not
  turn that module on (DESIGN.md §9.5).
- Outages: The writer keeps the documents that it takes while OpenSearch is down
  or still starts (up to 10,000 per federate). The writer sends them when
  OpenSearch answers. The writer logs the documents that OpenSearch rejects,
  with the reason from OpenSearch.

## Compliance records (DESIGN.md §18A)

The federates keep the records that a real brewery keeps for its regulators, and
the ground-truth documents carry them. None of them is a new HELICS key, so
`plant.yaml` does not change.

| Federate | Record | Rule it stands in for |
|---|---|---|
| `power` | `genset`: an hour meter (total, calendar year, and test hours this year against the 100 h cap) and a per-run log. The log has the kind (`emergency` or `exercise`), loaded time, average and peak kW, and load bank. It also says whether the run met the monthly-test criteria (30 min under load at 30 % of nameplate). A remote start (`gen-ats.gen_start`) or a `genset-exercise` event runs the set under load. The ATS transfers EP-1 to the set, and the load bank of the event adds kW | NFPA 110 (monthly exercise, records), 40 CFR 60.4211(f) (100 h/yr of testing on an emergency engine) |
| `utilities` | `wastewater`: the permit band, the discharged volume of today, and the pH range seen during discharge. The record has how long the discharge valve had a command while the pH was out of band, and how often that began. Last, it has hourly pH samples | 40 CFR 403.5 and the POTW's rules (Metro Water Recovery forbids pH below 5.0, and the permit band of the brewery is 6.0-9.0) |
| `utilities` | `cip`: one record per circuit run (circuit, chemical, peak supply temperature, peak return conductivity, proven-flow time, verified) | The CIP verification record of DESIGN.md §5.3, and FDA CGMP sanitation records |
| `packaging` | `fill`: count, mean, standard deviation, and the bottles that the fill-level inspector rejected. The inspector rejects a bottle that is more than the 0.5 fl oz MAV under 12 fl oz. `ttb`: packaged barrels per the 27 CFR 25.158 case table and half-barrel kegs, and the quarterly net-contents variance against the 0.5 % tolerance | NIST Handbook 133 Table 2-6, and 27 CFR 25.142(d), 25.156, 25.158 |

## Validation (`tests/`)

| Test | Check |
|---|---|
| `test_brewing.py` | Boiling points (94.6 °C water, 94.9 °C wort at 83.4 kPa), strike water, and Tinseth against the Brewtarget/brewday formula (20.73 IBU reference). Altitude derating (0.698 Hosom, 0.643 Malowicki), and the recipe's 68.0 IBU on site compared with 74.9 at sea level. The ε-NTU hand calculations and `ht`, and the mash fermentability trend |
| `test_carbonation.py` | Zahm-Nagel 10.2 psig (sea level) and 12.8 psig (site) for 2.6 vol at 34 °F. Henry's law within 6 % over 32-50 °F and 2.0-3.0 vol. BBT carbonation and serving |
| `test_packml.py` | The transition table equals that of PackMLpy, and the model refuses illegal commands. A 5,000-command fuzz and a packaging shift (jam, power loss) give legal transitions only. OEE |
| `test_power.py` | MSB, MCC-1, LP-1, and EP-1 within ANSI C84.1 Range A from no load to 329 kW. BESS peak shaving, outage and return, the tariff's $19,140 per 50 kW-year, and a monthly bill. The ATS clock registers at the default start, and across a day, week, month, year, and leap-day boundary. The same registers with a fixed UTC offset, under 1x to 600x compression, and against `Tariff.onpeak` for every minute of a leap year |
| `test_plant.py` | Cellar CO₂ steady state hand calculation, purge tiers and BACnet override, RTU and cold room, weather, glycol and a chiller fault, and wastewater neutralization. A brew day, ground truth documents, and the event API. All seven federates together for 20 minutes with an outage and a CO₂ release. A simulated week with the program of plc-power in the loop. That week has on-peak discharge and overnight recharge, the demand reset at the month rollover, and one genset exercise in the compliance log |
| `test_plc_power.py` | The schedule of plc-power. The program trusts the clock only when the clock is steady. The test cases are a clock that is stopped, torn, garbage, or fast, or that jumps. The on-peak flag equals the tariff window. The monthly demand reset, and every BESS dispatch regime with its clock-free fallback. Load shed versus an exercise transfer, the weekly exercise and what blocks it, and the generated alarms. The compiled ST against its Python model on 40,000 random scans |
| `test_plcloop.py` | The PLC link of the offline federation: tag directions and register scaling from `tags.json`, and one step of read, scan, write |
| `test_compliance.py` | The genset exercise under load and its log. A 33 kW EP-1 meets 30 % of nameplate. The everyday 15 kW does not, until the test adds a 40 kW load bank. An outage during a test becomes an emergency run. The wastewater discharge record: the model inhibits and logs a caustic slug, and the discharge stays in 6.0-9.0. A verified caustic CIP record and an unverified rinse. Fill-level rejects against the Handbook 133 MAV, with the lot mean at or above 12 fl oz and the quarterly variance inside the TTB 0.5 %. The tests of the event API on the parameters of `genset-exercise` |
| `test_fermentation.py`, `test_thermal.py`, `test_timebase.py`, `test_federate.py` | The existing tests of the cellar slice |
| `test_plc_brewhouse.py` | A port of the phases of plc-brewhouse drives two brews. The HLT heats while its jacket is covered, and the model counts each brew. The tun and kettle are empty between brews, and the second mash-in takes as long as the first. The hops go in, and an interrupted knockout counts once |
| `test_brewhouse_panel.py` | The request counter of the panel and its accepted echo. The `start-batch` event and the 05:00 schedule go to the autopilot with no PLC, and to the panel with one. The counters wrap at 16 bits. When nobody accepts a request, the model warns about it and keeps it, and the autopilot does not adopt it |
| `test_brewhouse_control_path.py` | The whole control path offline. A `start-batch` event brews a double-brew day through the compiled plc-brewhouse, the FUXA batch engine script (under Node), and the federates. That day is about 11 h of plant time. It has the ten phases of each brew in order, both brews into one fermenter, and the hops in. A request that arrives while FUXA is down waits at the panel. The 05:00 schedule starts the engine without an event |
| `test_plc_packaging.py` | A port of plc-packaging drives the line. The filler runs after the PLC opens its product valve, and it holds on a low supply and recovers. A jam clears after its time, and a commanded Hold stays. The line operator at the panel: the start and end of the shift, and a day without a shift. Also a line that started outside the shift, a Stop during the shift (with and without a reaction time), and a power cut. Against the compiled plc-packaging: the start of the shift again, and one press as one command |
| `test_plc_cellar.py` | The fermentation profile of plc-cellar in the compiled program. The recipe and the first scan, and the stage that a start of the PLC finds for each fermenter. A fill through fermenting, rest, and crash. An emptied fermenter, a fermenter taken off the profile, and the limits of the recipe. Nine hours in control of the cellar federate with the crew, from a fill to a racked fermenter |
| `test_cellar_flow.py` | Beer through the cellar. The rate of a transfer and its two ends, the yeast and CO2 of racking, and the jobs of the crew and their order. A crew that is off, the ci slice, a knockout that keeps to one fermenter, and wort into an uncleaned fermenter. The points of the transfer panel, the pressure of the beer line, and a filler that holds without product and runs again |
| `test_life_safety.py` | The life-safety and equipment-safety points. The CO₂ supply shutoff at tier 3, and CO₂ detection beyond the cellar. The refrigerant detector and charge record of the chiller, the BESS cell temperature and off-gas detector, and the safety valve of the boiler |
| `test_relief.py` | The pressure-relief valves of the vessels and the per-vessel truth outputs |
| `test_groundtruth.py` | The writer against a stand-in OpenSearch: template first, documents kept through an outage, and rejected documents logged. Boolean and float values, whoever wrote the command. The `starting`, `ok`, and `stalled` health of the event API |

## A PLC in the offline federation

`plcloop.py` closes the loop for one PLC in `LocalFederation`
(`plcs=[PlcLink(...)]`). Each step, it reads the input tags of the PLC from the
values of the federation and runs one scan of an engine. Then it writes the
output tags of the PLC to the federate. It takes the scaling and direction of
every tag from `plant/generated/<variant>/tags.json`.

An engine is anything with `set`, `get`, and `scan`. The tests use two engines
for plc-power. The first is `tests/plc_power_model.py`, a Python transcription
of the program. The second is `tests/st_engine.py`, the strucpp C++ of the
program, built with g++ and run through `ctypes`. As a result, what runs is the
program as OpenPLC compiles it:

```shell
plc/common/build_zip.sh plc-power --variant full    # once: clones and builds the pinned strucpp (needs Node 22+)
python -m pytest tests/test_plc_power.py tests/test_plant.py
```

The compiled runs skip, and give the reason, where Node, a built strucpp
checkout (`~/.cache/cherrymountain/strucpp-fcccfbdf5370`, or `$STRUCPP`), or
`g++` is missing. The model runs always, and
`test_compiled_st_agrees_with_the_model` ties the two where both exist.
`tests/brewhouse_sil.py` does the same for plc-brewhouse. It also runs the FUXA
batch engine (`fuxa/tests/engine_runner.mjs`, under Node) against the registers
of the PLC once a second. The test holds the registers as the OPC UA driver of
FUXA holds them.

Neither covers the OpenPLC runtime: the image-table binding, the polling of the
Modbus master and its torn reads, and scan jitter. The loop scans once per
federation step, not every 100 ms.

## Simplifications and tuned values

- The authors tuned the mash enzyme constants and the brewhouse extract yield,
  and did not fit them. The well-mixed lauter washes the bed out less completely
  than a fly sparge, so the authors calibrated the yield constant to reach
  16 °P. The live kettle IBU continues to isomerize through the whirlpool and
  knockout, so it ends above the stand-only 68 IBU estimate of the recipe.
- The chiller's OEM staging runs in the utilities federate, because the
  chiller's points expose stage status, not stage commands.
- CO₂ is a bulk tank with a regulator. There is no recovery plant.
- Glycol uses a constant c_p, and the model does not require CoolProp.
- The EPW weather files are not in the repository. Without an EPW file, the
  weather is synthetic.
