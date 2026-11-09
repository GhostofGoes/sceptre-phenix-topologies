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
cellar has no autopilot: the ci slice's PLC controls it.

**Time.** Everything runs in wall time except the cellar's fermentation
kinetics, which run 48x faster; exported fluxes stay real-time-equivalent
(DESIGN.md §9.4). The clock starts at helics.json's `clock.start` (a summer
weekday, 04:30).

## Event API (sim-ctl)

The weather federate serves it on `127.0.0.1:8080` (the autotunnel forwards
from there). The contract is `apps/cherrymountain_apps/simctl.py`'s:

| Request | Reply |
|---|---|
| `GET /api/v1/health` | `200 {"status": "ok", "variant", "sim_time"}` |
| `POST /api/v1/events` (or `POST /events`) with `{"event": <kind>, ...}` | `202 {"accepted": true, "id", "event", "params", "target", "received"}`, `400 {"error"}` for an unknown kind or bad parameters |
| `GET /api/v1/events` | `200 {"events": [...]}` |
| `GET /api/v1/kpis` | `200 {"sim_time", "batches_completed", "batches_in_progress", "bottles_packaged", "kegs_packaged", "energy_kwh", "demand_kw", "peak_demand_kw"}` |

Events (the plantevent README's table): `start-batch` (brewhouse),
`utility-breaker` `action: open|close` (power), `chiller-fault` `compressor`,
`action: fail|clear` (utilities), `co2-release` `fv`, `rate: low|medium|high`,
`duration` s (cellar), `jam` `machine`, `duration` s (packaging),
`weather-step` `delta_f`, `duration` s (weather), and `cip-panel` `circuit`
(utilities: sets the swing panel). The weather federate forwards each event to
`<target>/updates` as `{"event": {"type", "params", "id"}}`.

## Ground truth

With `ground_truth` in helics.json, every federate writes its published values
and model state every 10 s to the in-experiment OpenSearch
(`cherrymountain-truth-<federate>-YYYY.MM.DD`, `_bulk` API) from a background
thread (`groundtruth.py`).

## Validation (`tests/`)

| Test | Check |
|---|---|
| `test_brewing.py` | Boiling points (94.6 °C water, 94.9 °C wort at 83.4 kPa), strike water, Tinseth against the Brewtarget/brewday formula (20.73 IBU reference), altitude derating (0.698 Hosom, 0.643 Malowicki), the recipe's 68.0 IBU on site vs 74.9 at sea level, ε-NTU hand calculations and `ht`, mash fermentability trend |
| `test_carbonation.py` | Zahm-Nagel 10.2 psig (sea level) and 12.8 psig (site) for 2.6 vol at 34 °F, Henry's law within 6 % over 32-50 °F and 2.0-3.0 vol, BBT carbonation and serving |
| `test_packml.py` | Transition table equals PackMLpy's, illegal commands refused, a 5,000-command fuzz and a packaging shift (jam, power loss) with legal transitions only, OEE |
| `test_power.py` | MSB, MCC-1, LP-1, and EP-1 within ANSI C84.1 Range A from no load to 329 kW, BESS peak shaving, outage and return, the tariff's $19,140 per 50 kW-year, a monthly bill |
| `test_plant.py` | Cellar CO₂ steady state hand calculation, purge tiers and BACnet override, RTU and cold room, weather, glycol and a chiller fault, wastewater neutralization, a brew day, ground truth documents, the event API, and all seven federates together for 20 minutes with an outage and a CO₂ release |
| `test_fermentation.py`, `test_thermal.py`, `test_timebase.py`, `test_federate.py` | The cellar slice's existing tests |

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
