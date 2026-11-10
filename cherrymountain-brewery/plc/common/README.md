# PLC program builder (full and lite variants)

Builds the OpenPLC v4 program ZIPs of the five PLCs for the full and lite
variants ([DESIGN.md 12.1](../../DESIGN.md#121-openplc-v4-runtime-v3-fallback)).
The ci slice's plc-cellar ZIP keeps its own builder in `plc/plc-cellar/`.

| File | Purpose |
|---|---|
| `plcbuild.py` | Splices `located_vars.st` and the alarm instances into `plc/<plc>/<program>.st`, generates the alarm logic from `alarms.json`, checks the program, and writes one compilable ST file |
| `cm_alarm.st` | `CmAlarm`, the alarm function block: deadband, on-delay, off-delay, suppression |
| `gen_program.mjs` | Compiles with strucpp (pinned) and writes the C++ sources and `debug_map.json` |
| `pack_zip.py` | Packs the ZIP reproducibly (sorted entries, fixed timestamps) |
| `zipdiff.py` | Compares two ZIPs by entries, for `--check` |
| `build_zip.sh` | Runs the four steps for one PLC and variant, or `--all` |

The programs are `plc/plc-brewhouse/plc-brewhouse.st` (ISA-88 phases and their
parameter for the FUXA batch engine, the batch request handshake with the panel,
the liquor tanks, boil detection), `plc/plc-cellar/
plc-cellar-plant.st` (glycol zones, the fermentation profile, spunding,
carbonation, the CO2 interlock,
the dry-hop sequence, the peer status bits), `plc/plc-packaging/
plc-packaging.st` (the PackML line over the machines, the operator panel's
command, OEE, conveyors),
`plc/plc-utilities/plc-utilities.st` (glycol, steam, wastewater, the CIP
sequence with plc-cellar's permit read peer to peer), and
`plc/plc-power/plc-power.st` (load shed and staged restoration, BESS dispatch
on the demand cap, the on-peak shift, the overnight and brew-day recharge, the
monthly demand reset, and the weekly genset exercise, all from the ATS
controller's clock, which the program distrusts when it stops or jumps). Each
was compiled by strucpp and its C++ checked with `g++ -fsyntax-only` against the
strucpp runtime headers; none has run in an OpenPLC runtime yet. plc-power's C++
is also built and run scan by scan against the federates' power model by
`sim/tests/test_plc_power.py` and `test_plant.py`, and plc-brewhouse's against the
brewhouse federate and the FUXA batch engine by
`sim/tests/test_brewhouse_control_path.py` (`sim/README.md`, `fuxa/README.md`).

```shell
plc/common/build_zip.sh plc-utilities --variant full    # writes phenix-injects/openplc/full/plc-utilities.zip
plc/common/build_zip.sh --all                           # every PLC, full and lite
plc/common/build_zip.sh --all --check                   # fail if a committed ZIP is stale
python3 -I plc/common/plcbuild.py plc-power --check-only  # the program checks only (no Node)
```

## The program contract

Each program has two marker comments, one `LOCATED_VARS` in the `PROGRAM`
header and one `ALARMS` in its body. `plcbuild.py` fails when:

- the program assigns an alarm bit (they are generated from `alarms.json`, with
  the limit, deadband, on-delay, off-delay, and suppression bit of each alarm,
  so the PLC and the FUXA alarm list cannot drift; DESIGN.md 6.5);
- a RIO output (`%QX100.0` and up, `%QW100` and up) is never assigned; or
- a PLC-computed tag (a plant.yaml tag without `io` whose type is
  `analog-read` or `binary-read`) is never assigned.

Operator setpoints and commands (`analog-read-write`, `binary-read-write`
without `io`) belong to FUXA and the EWS; programs load defaults on the first
scan and clamp them.

The ZIPs carry `debug_map.json` (the strucpp debug map), which the
`cherrymountain` app reads for the OPC UA plugin config of plc-brewhouse. The
northbound configs of plc-brewhouse (`opcua`) and plc-packaging (`s7comm`) are
added by the app at pre-start; the ZIP carries plantgen's `modbus_master.json`
and, where the PLC has one, `modbus_slave.json`.

`sync.py --check` checks every committed ZIP without Node: `defines.h` holds
the MD5 of the program as assembled now, and `conf/` equals the generated
plugin configs. Rebuild after any change to `plant.yaml`, plantgen, or a
program (`python -m plantgen`, `python sync.py`, then `build_zip.sh --all`).
