# cherrymountain_sim

The CherryMountain Brewery simulation ([DESIGN.md §9](../DESIGN.md#9-simulation-and-co-simulation)):
one HELICS federate per process area, the process, power, and building models
behind them, and the sim-ctl event API. Every federate publishes and subscribes
exactly the keys plantgen lists in `plant/generated/<variant>/helics.json`.

```shell
pip install -e '.[helics,power,test]'
python -m pytest
cherrymountain-sim --config ../plant/generated/full/helics.json --federate brewhouse
cherrymountain-local --config ../plant/generated/full/helics.json --hours 2   # all federates, no HELICS
```

`python -m cherrymountain_sim` is the same as `cherrymountain-sim`; its default
federate is `cellar`, so the ci slice's existing `cherrymountain-cellar` command
still works.

## Federates

| Federate | Host | Step | Module | Models |
|---|---|---|---|---|
| `weather` | sim-ctl | 1 s (weather changes each minute) | `weather.py`, `simctl.py` | EPW (TMY3) or synthetic Denver weather, barometric correction to 1,585 m, groundwater curve, the event API, KPIs |
| `brewhouse` | sim-process | 1 s | `brewhouse.py`, `brewing.py`, `recipe.py` | Mill, CLT/HLT, mash (two-enzyme kinetics), lauter, kettle with the local wort boiling point and live Tinseth/mIBU bitterness, two-stage ε-NTU knockout HX, O₂ |
| `cellar` | sim-process | 1 s (kinetics 48x) | `federate.py`, `cellar.py`, `fermentation.py`, `thermal.py`, `carbonation.py` | de Andrés-Toro fermentation, glycol jackets, spunding, knockout filling, bright tanks with Zahm-Nagel carbonation, CO₂ releases |
| `utilities` | sim-process | 1 s | `utilities.py` | Glycol reservoir and two-stage chiller, steam boiler, air, bulk CO₂, RO, wastewater pH, CIP |
| `packaging` | sim-process | 1 s | `packaging.py`, `packml.py` | SimPy bottling line and keg filler, PackML machines, jams, starve/block, filler bowl interlock, OEE |
| `power` | sim-power | 1 s | `power.py`, `tariff.py` | OpenDSS 480 V feeder, breakers, ATS and genset island, PV and BESS (IEEE 1547 trip and return), SV-SGT meter and bill |
| `building` | sim-building | 10 s | `building.py` | Taproom 3R2C with RTU-1 and CO₂, cold room 1R1C, cellar CO₂ mass balance, hood exhaust and MUA |

**Control authority.** The PLC programs come in Phase 3. Until a PLC writes, the
brewhouse, utilities, and packaging federates run a built-in operator (sim
setting `autopilot: auto`): a double-brew day at 05:00 on brew days, the
utilities held at their setpoints, and the packaging shift 06:00-14:30 on
weekdays. The first PLC write hands control over (`on` keeps the autopilot,
`off` never runs it). The power and building federates use per-point authority
instead: a point the PLC, the DER gateway, or a BACnet client has written
follows its last write, and the others follow the built-in controller (the
plc-power dispatch of DESIGN.md §7.3, the BAS controllers' local logic). The
cellar's autopilot runs the recipe profile by kinetic hours since pitch
(66 °F, 70 °F and sealed spunding from day 4, a 34 °F crash from day 10), holds
the bright tanks at 34 °F, and carbonates toward 2.6 vol only while the CO2
relay (`building/vent.co2_relay_ok`) is healthy; plc-cellar takes over on its
first write.

**Who starts a brew day.** The `start-batch` event and the 05:00 schedule of a
brew day are a request for brews (`brews`, default 2). With no PLC writing, the
request goes to the autopilot. With plc-brewhouse driving, the federate raises the
brewhouse panel's request counter (`panel.requests`, `BH.BATCH.XI02.REQUESTS`); the
FUXA batch engine (`fuxa/README.md`) reads it through the PLC, queues the brews,
and answers with the count it has accepted (`BH.BATCH.XC02.ACCEPTED`, which the
PLC echoes to the panel's `panel.accepted`). If the engine never answers (FUXA is
down), the request stays at the panel: it is logged as a warning after 5 minutes,
shows as `batch.panel.pending` in the ground truth, and the engine takes it when
it returns. The autopilot does not adopt it, even if the PLC stops. The counters
are 16-bit and wrap.

**Who starts the packaging shift.** With no PLC writing, the built-in operator
runs the shift (06:00 to 14:30 on weekdays) and commands the machines itself.
plc-packaging only relays a line command, so with it driving, the federate's
line operator works the shift at the operator panel at the filler
(`linepanel.cmd`, `PKG.PANEL.HS01.PV`): the PackML code of the button being
pressed, held 3 s, at least 5 s apart. The PLC takes each press as one line
command.

- When the shift starts: Reset, then Start (Clear first if a machine is
  Aborted).
- When it ends: Stop, if this operator's shift is the one running. A line
  started from the HMI outside the shift is left running.
- During the shift, when the line has stood Stopped, Aborted, or Idle for
  `operator_react_s` (sim setting, default 120; 0 turns this off): the same
  start sequence. After a power cut the time runs from the power's return.
- Never: undo a Hold or a Suspend. A jam and a low bowl clear themselves, and a
  commanded Hold is somebody's choice.

The ground truth has who commands the line (`packaging.operator`: `autopilot`,
`panel`, or `none`) and the count of presses (`packaging.panel_presses`).

**Who moves the beer.** The cellar crew (`crew.py`, sim setting `crew`) does the
hand work, with or without a PLC: no PLC point does it.

| Job | When | What happens |
|---|---|---|
| Rack a fermenter into BBT-201 | The beer is `ready_age_h` (264) kinetic hours old, both zones are at or below `ready_temp_f` (40), and BBT-201 is empty and clean. The oldest ready fermenter goes first | 10 minutes of set-up (`setup_min`), then 30 gal/min (`pump_gpm`) with the pump in Hand. 7 % stays behind (`rack_leave_pct`) and is dumped. The beer keeps 80 % of its equilibrium CO2 |
| Move BBT-201's beer to BBT-202 | BBT-201 is bright (`bright_vol` 2.4, `bright_temp_f` 38) and BBT-202 is at or below `refill_below_pct` (10) | The same set-up and rate, until BBT-201 is empty or BBT-202 full |
| Clean an emptied fermenter or BBT-201 | It is empty and not clean | 45 minutes by hand (`clean_min`). The CIP skid is not used |

The crew does one job at a time and never changes a setpoint: somebody has to
crash a fermenter before it is racked (the built-in operator does from day 10;
plc-cellar has no profile, so with it in control the setpoint comes from the
HMI). `crew: { enabled: false }` turns the crew off. The transfer panel's points
show the work (`route_ok`, `pump_running`, `flow_gpm`), the ground truth has it
under `crew` and `bright_tanks`, and `knockout_lost_bbl` counts wort that arrived
with its fermenter full: a knockout keeps to the fermenter it started in.

The filler's supply is `cellar/beer.supply_psia`: the head of the bright tank
whose outlet is open and that holds beer, 0 otherwise. Without product the
filler holds on its low bowl and unholds when a tank is on line again.

**What stays manual with a PLC in control.** A PLC writes field points; some
jobs have none, so the model does them when the plant's state calls for them:

- *Brewhouse.* The brewer adds the recipe's boil hops by the boil time left
  and the whirlpool hops when the PLC opens the hop doser, grains out once the
  mash has run off and the kettle is on steam for the boil, and dumps the trub
  left in the kettle when a knockout stops at or below 6 % (plc-brewhouse ends
  KNOCKOUT at 5 %). That is also when a brew is counted.
- *Packaging.* A starved or blocked machine unsuspends once it has a bottle to
  take or room to put one, the line operator clears a jam and unholds the
  machine when the jam's time is up, and the filler unholds from a low bowl
  when the beer line has pressure again. The filler runs 10 s on what its bowl holds
  with the product valve shut, which gives plc-packaging time to open the
  valve after it reads Execute. A Hold or Suspend that was commanded stays.

Starting a batch (the phases of plc-brewhouse) and starting, stopping, and
resetting the packaging line stay with whoever commands the PLCs: nothing in
the simulation does them once a PLC writes. `tests/test_plc_brewhouse.py` and
`tests/test_plc_packaging.py` run both federates against Python ports of the
two PLC programs.

**Time.** Everything runs in wall time except the cellar's fermentation
kinetics, which run 48x faster; exported fluxes stay real-time-equivalent
(DESIGN.md §9.4). The clock starts at helics.json's `clock.start` (a summer
weekday, 04:30), and `clock.speedup` moves the calendar faster than the physics.

There are two clocks. The cellar's `clock.sim_hours` (`CEL.CLK.KI01`) is the
fermentation recipe clock: kinetic hours since the start, 48x compressed, not a
date. The calendar is `SimClock` (`clock.start` plus federate time times
`clock.speedup`, a fixed UTC offset, so no daylight-saving shift): the weather
federate publishes it as `weather/clock.epoch_s`, the tariff meter bills by it,
and the ATS controller (`gen-ats`) shows it to plc-power as three 16-bit
registers (`power.ClockRegisters`): `clock_min` (minute of the local day,
0-1439), `clock_day` (the local date as days since 1970-01-01, monotonic, so a
reader can tell a stopped or jumping clock from a running one; the weekday is
`(day + 4) mod 7`, 0 = Sunday), and `clock_month` (1-12, the billing month).
They are `PWR.GEN.KI01` to `KI03` in `plant.yaml`. plc-power schedules the
SV-SGT on-peak window, the brew-day reserve, the monthly demand reset, and the
weekly genset exercise from them, and distrusts them when they stop or jump
(`plc/plc-power/plc-power.st`).

## Event API (sim-ctl)

The weather federate serves it on `127.0.0.1:8080` (the autotunnel forwards
from there). The contract is `apps/cherrymountain_apps/simctl.py`'s:

| Request | Reply |
|---|---|
| `GET /api/v1/health` | `200 {"status", "variant", "sim_time", "step_age_s"}`. `status` is `ok` while the federate steps, `starting` before its first step (the HELICS federation has not started: the broker still waits for a federate), and `stalled` when the last step is more than 10 s old. The API answers in all three, so check `status`, not the 200 |
| `POST /api/v1/events` (or `POST /events`) with `{"event": <kind>, ...}` | `202 {"accepted": true, "id", "event", "params", "target", "received"}`, `400 {"error"}` for an unknown kind or bad parameters |
| `GET /api/v1/events` | `200 {"events": [...]}` |
| `GET /api/v1/kpis` | `200 {"sim_time", "batches_completed", "batches_in_progress", "bottles_packaged", "kegs_packaged", "energy_kwh", "demand_kw", "peak_demand_kw", "ttb_bbl_packaged", "fill_mean_floz", "fill_under_mav_rejects", "wastewater_inhibit_events", "cip_runs_verified", "cip_runs_unverified", "genset_hours_year", "genset_test_hours_year"}` |
| `GET /api/v1/state` | `200 {"seq", "t_s", "sim_time", "wall_time", "variant", "values"}`: the ground-truth snapshot cm-viz reads (every key of plant.yaml `snapshot`, by HELICS key) |

Events, with the federate that applies each (a `duration` is in seconds, more
than 0 and at most 86,400):

| Event | Parameters | Federate |
|---|---|---|
| `start-batch` | `brews` 1-4 (default 2), `batch_id`, `recipe` (`fire-of-the-phenix`). It queues brews for the autopilot; with plc-brewhouse in control it raises the panel's request counter, and the FUXA batch engine starts the brews | brewhouse |
| `utility-breaker` | `action`: `open` or `close` (required) | power |
| `genset-exercise` | `action`: `start` (default) or `stop`; `load_bank_kw` 0-100; `duration` (default 1,800) | power |
| `bess-thermal` | `action`: `start` (default) or `clear` | power |
| `chiller-fault` | `action`: `fail` (default) or `clear`; `compressor` 1 (default) or 2 | utilities |
| `refrigerant-leak` | `action`: `leak` (default), `repair`, or `recharge`; `rate`: `low`, `medium` (default), `high` | utilities |
| `cip-panel` | `circuit`: `bh`, `fv101` to `fv104`, `bbt201`, `bbt202`, or `none` (default) | utilities |
| `co2-release` | `fv` (required, for example `FV-102`); `rate`: `low`, `medium`, `high` (default; 2, 10, 40 g/s); `duration` (default 600) | cellar |
| `co2-leak` | `zone`: `tank`, `filler`, or `draft` (required); `rate` as above; `duration` (default 600) | building |
| `gas-detection` | `action`: `fail` (default) or `restore` | building |
| `jam` | `machine`: `rinser`, `filler`, `coder`, `labeler` (default), `packer`, `kegger`; `duration` (default 300) | packaging |
| `weather-step` | `delta_f` -60 to 60 (required); `duration` (default 0: hold until the next step) | weather |

The weather federate forwards each event to `<target>/updates` as
`{"event": {"type", "params", "id"}}`. An event is accepted (202) while the
federation is not running, and applied when it starts.

## Ground truth

With `ground_truth` in helics.json, every federate writes its published values
and model state every 10 s to the in-experiment OpenSearch
(`cherrymountain-truth-<federate>-YYYY.MM.DD`, `_bulk` API) from a background
thread (`groundtruth.py`).

- **Time.** `@timestamp` and the date in the index name are the wall clock
  (UTC), the clock Malcolm and Wazuh use, so
  the records line up without a conversion and Grafana's default range shows
  the run. `sim_time` holds the simulated date and time and `t_s` the federate
  seconds.
- **Mapping.** Before its first write each writer puts the index template
  `cherrymountain-truth-cm` for `cherrymountain-truth-*`: dates, keywords for
  `federate`, `source`, and `field`, and doubles for every number. It also
  covers the ot-sim module's documents (`cherrymountain-truth-YYYY.MM.DD`,
  one per point per second), whose own template OpenSearch refuses.
- **Outages.** Documents taken while OpenSearch is down or still starting are
  kept (up to 10,000 per federate) and sent when it answers. Documents it
  rejects are logged with its reason.

## Compliance records (DESIGN.md §18A)

The records a real brewery keeps for its regulators are kept in the federates
and carried in the ground-truth documents; none of them is a new HELICS key,
so `plant.yaml` is unchanged.

| Federate | Record | Rule it stands in for |
|---|---|---|
| `power` | `genset`: hour meter (total, calendar year, test hours this year against the 100 h cap) and a per-run log with kind (`emergency` or `exercise`), loaded time, average and peak kW, load bank, and whether the run met the monthly-test criteria (30 min under load at 30 % of nameplate). A remote start (`gen-ats.gen_start`) or a `genset-exercise` event runs the set under load: the ATS transfers EP-1 to it, and the event's load bank adds kW | NFPA 110 (monthly exercise, records), 40 CFR 60.4211(f) (100 h/yr of testing on an emergency engine) |
| `utilities` | `wastewater`: the permit band, today's discharged volume and the pH range seen while discharging, the time the discharge valve was commanded while the pH was out of band (and how often that began), and hourly pH samples | 40 CFR 403.5 and the POTW's rules (Metro Water Recovery forbids pH below 5.0; the brewery's permit band is 6.0-9.0) |
| `utilities` | `cip`: one record per circuit run (circuit, chemical, peak supply temperature, peak return conductivity, proven-flow time, verified) | The CIP verification record of DESIGN.md §5.3; FDA CGMP sanitation records |
| `packaging` | `fill`: count, mean, standard deviation, and the bottles the fill-level inspector rejected for being more than the 0.5 fl oz MAV under 12 fl oz; `ttb`: packaged barrels per the 27 CFR 25.158 case table and half-barrel kegs, and the quarterly net-contents variance against the 0.5 % tolerance | NIST Handbook 133 Table 2-6; 27 CFR 25.142(d), 25.156, 25.158 |

## Validation (`tests/`)

| Test | Check |
|---|---|
| `test_brewing.py` | Boiling points (94.6 °C water, 94.9 °C wort at 83.4 kPa), strike water, Tinseth against the Brewtarget/brewday formula (20.73 IBU reference), altitude derating (0.698 Hosom, 0.643 Malowicki), the recipe's 68.0 IBU on site vs 74.9 at sea level, ε-NTU hand calculations and `ht`, mash fermentability trend |
| `test_carbonation.py` | Zahm-Nagel 10.2 psig (sea level) and 12.8 psig (site) for 2.6 vol at 34 °F, Henry's law within 6 % over 32-50 °F and 2.0-3.0 vol, BBT carbonation and serving |
| `test_packml.py` | Transition table equals PackMLpy's, illegal commands refused, a 5,000-command fuzz and a packaging shift (jam, power loss) with legal transitions only, OEE |
| `test_power.py` | MSB, MCC-1, LP-1, and EP-1 within ANSI C84.1 Range A from no load to 329 kW, BESS peak shaving, outage and return, the tariff's $19,140 per 50 kW-year, a monthly bill; the ATS clock registers at the default start, across a day, week, month, year, and leap-day boundary, with a fixed UTC offset, under 1x to 600x compression, and against `Tariff.onpeak` for every minute of a leap year |
| `test_plant.py` | Cellar CO₂ steady state hand calculation, purge tiers and BACnet override, RTU and cold room, weather, glycol and a chiller fault, wastewater neutralization, a brew day, ground truth documents, the event API, all seven federates together for 20 minutes with an outage and a CO₂ release, and a simulated week with plc-power's program in the loop (on-peak discharge and overnight recharge, the demand reset at the month rollover, one genset exercise in the compliance log) |
| `test_plc_power.py` | plc-power's schedule: the clock trusted only when steady (stopped, jumping, torn, garbage, fast), the on-peak flag equal to the tariff window, the monthly demand reset, every BESS dispatch regime and its clock-free fallback, load shed versus an exercise transfer, the weekly exercise and what blocks it, the generated alarms, and the compiled ST against its Python model on 40,000 random scans |
| `test_plcloop.py` | The PLC link of the offline federation: tag directions and register scaling from `tags.json`, and one step of read, scan, write |
| `test_compliance.py` | The genset exercise under load and its log (33 kW EP-1 meets 30 % of nameplate; the everyday 15 kW does not until a 40 kW load bank is added; an outage during a test becomes an emergency run), the wastewater discharge record (a caustic slug is inhibited and logged, the discharge stays in 6.0-9.0), a verified caustic CIP record and an unverified rinse, fill-level rejects against the Handbook 133 MAV with the lot mean at or above 12 fl oz and the quarterly variance inside the TTB 0.5 %, and the event API's validation of `genset-exercise` |
| `test_fermentation.py`, `test_thermal.py`, `test_timebase.py`, `test_federate.py` | The cellar slice's existing tests |
| `test_plc_brewhouse.py` | Two brews driven by a port of plc-brewhouse's phases: the HLT heats while its jacket is covered, each brew is counted, the tun and kettle are empty between brews, the second mash-in takes as long as the first, the hops go in, and an interrupted knockout counts once |
| `test_brewhouse_panel.py` | The panel's request counter and its accepted echo: the `start-batch` event and the 05:00 schedule go to the autopilot with no PLC and to the panel with one, the counters wrap at 16 bits, and a request nobody accepts is warned about, kept, and not adopted by the autopilot |
| `test_brewhouse_control_path.py` | The whole control path offline: a `start-batch` event brews a double-brew day through the compiled plc-brewhouse, the FUXA batch engine script (run under Node), and the federates (about 11 h of plant time: the ten phases of each brew in order, both brews into one fermenter, the hops in), a request made while FUXA is down waits at the panel, and the 05:00 schedule starts the engine without an event |
| `test_plc_packaging.py` | The line driven by a port of plc-packaging: the filler runs once the PLC opens its product valve, holds on a low supply and recovers, a jam clears after its time, and a commanded Hold stays. The line operator at the panel: the shift's start and end, a day without a shift, a line started outside the shift, a Stop during the shift (with and without a reaction time), and a power cut. The shift's start again, and one press being one command, against the compiled plc-packaging |
| `test_cellar_flow.py` | Beer through the cellar: a transfer's rate and its two ends, racking's yeast and CO2, the crew's jobs and their order, a crew that is off, the ci slice, a knockout that keeps to one fermenter, wort into an uncleaned fermenter, the transfer panel's points, the beer line's pressure, and the filler holding without product and running again |
| `test_life_safety.py` | The life-safety and equipment-safety points: the CO₂ supply shutoff at tier 3, CO₂ detection beyond the cellar, the chiller's refrigerant detector and charge record, the BESS cell temperature and off-gas detector, and the boiler's safety valve |
| `test_relief.py` | The vessels' pressure-relief valves and the per-vessel truth outputs |
| `test_groundtruth.py` | The writer against a stand-in OpenSearch (template first, documents kept through an outage, rejected documents logged), boolean and float values whoever wrote the command, and the event API's `starting`, `ok`, and `stalled` health |

## A PLC in the offline federation

`plcloop.py` closes the loop for one PLC in `LocalFederation` (`plcs=[PlcLink(...)]`):
each step it reads the PLC's input tags from the federation's values, runs one
scan of an engine, and writes the PLC's output tags to the federate, with the
scaling and direction of every tag taken from `plant/generated/<variant>/tags.json`.
An engine is anything with `set`, `get`, and `scan`. The tests use two for
plc-power (`tests/plc_power_model.py`, a Python transcription of the program, and
`tests/st_engine.py`, the program's strucpp C++ built with g++ and run through
`ctypes`, so what runs is the program as OpenPLC would compile it):

```shell
plc/common/build_zip.sh plc-power --variant full    # once: clones and builds the pinned strucpp (needs Node 22+)
python -m pytest tests/test_plc_power.py tests/test_plant.py
```

The compiled runs skip, with the reason, where Node, a built strucpp checkout
(`~/.cache/cherrymountain/strucpp-fcccfbdf5370`, or `$STRUCPP`), or `g++` is
missing; the model runs always, and `test_compiled_st_agrees_with_the_model` ties
the two where both exist. `tests/brewhouse_sil.py` does the same for plc-brewhouse
and also runs the FUXA batch engine (`fuxa/tests/engine_runner.mjs`, under Node)
against the PLC's registers once a second, as FUXA's OPC UA driver would hold them.
What neither covers is the OpenPLC runtime: the
image-table binding, the Modbus master's polling and its torn reads, and scan
jitter. The loop scans once per federation step, not every 100 ms.

## Simplifications and tuned values

- Mash enzyme constants and the brewhouse extract yield are tuned, not fitted
  (the well-mixed lauter washes the bed out less completely than a fly sparge,
  so the yield constant is calibrated to reach 16 °P). The live kettle IBU
  keeps isomerizing through the whirlpool and knockout, so it ends above the
  recipe's stand-only 68 IBU estimate.
- The chiller's OEM staging runs in the utilities federate, because the
  chiller's points expose stage status, not stage commands.
- CO₂ is a bulk tank with a regulator; there is no recovery plant.
- Glycol uses a constant c_p (CoolProp is not required).
- EPW weather files are not committed; without one the weather is synthetic.
