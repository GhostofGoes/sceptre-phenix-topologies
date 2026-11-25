# plc-cellar program (ci variant)

IEC 61131-3 Structured Text for the cellar PLC ([DESIGN.md
12.1](../../docs/DESIGN.md#121-openplc-v4-runtime-v3-fallback)).

| File | Purpose |
|---|---|
| `plc-cellar.st` | The program: glycol control, spunding setpoints, alarms |
| `assemble.py` | Splices the plantgen located variables into the program, checks it against `alarms.json`, and builds a compilable single file |

## What the program does

For each of FV-101 to FV-104:

- **Temperature control.** The side and cone glycol valves are on/off with a
  0.5 degF hysteresis half band around the setpoint (`ZoneControl`). Cooling
  only: the valve opens at `SP + 0.5` and closes at `SP - 0.5`.
- **Spunding.** `CEL.FVxxx.PCV01.SP` (`%QW100` to `%QW103`) is written every
  scan from an internal psig value plus site ambient pressure (12.1 psia). The
  values follow the ci initial state (FV-101 and FV-102 at 1.0 psig, FV-103 and
  FV-104 at 12.0 psig).
- **Alarms.** Deviation, high temperature, and head pressure high and low bits
  (`%QX0.0` upward) use on-delay timers. The limits and delays equal
  `plant/generated/ci/alarms.json`; `assemble.py` fails if they drift.
- **Setpoints.** `%MW0` to `%MW7` hold the temperature setpoints. The first
  scan loads defaults (66, 66, 68, 50 degF, side and cone alike). After that
  the operator owns them over Modbus, and the program only clamps them to
  20 to 90 degF.

All analog values are raw registers, engineering value x 100.

## Check it without the Editor

```shell
python3 -I assemble.py                                  # splice and consistency checks
python3 -I assemble.py --iec2c "$MATIEC/build/bin/iec2c" --matiec-lib "$MATIEC/lib"
```

The second form also compiles the result with
[matiec](https://github.com/beremiz/matiec), the compiler OpenPLC v3 uses. The
output goes to `build/` (ignored by Git). The program compiled cleanly with
matiec when this was written.

## Build the program ZIP

The OpenPLC v4.3.x runtime compiles strucpp C++ on the PLC when a ZIP is
uploaded, so the ZIP holds sources. `build_zip.sh` builds it without the
OpenPLC Editor, from the same files the Editor would use:

```shell
plc/plc-cellar/build_zip.sh            # writes phenix-injects/openplc/plc-cellar.zip
plc/plc-cellar/build_zip.sh --check    # rebuild to a temp file; fail if the committed ZIP differs
```

1. `assemble.py` splices `located_vars.st` into `plc-cellar.st` and appends the
   `CONFIGURATION` block (100 ms task), after checking the alarm limits and
   delays against `alarms.json`.
2. `gen_program.mjs` compiles that file with [strucpp](https://github.com/Autonomy-Logic/strucpp)
   pinned to commit `fcccfbdf53704fad1f5e71dd2a05d440b9028b47` (0.7.0), through
   its library API because the CLI does not write `generated_debug.cpp`. The
   IEC standard function blocks (`TON`) come from the checkout's `libs/`.
3. `pack_zip.py` writes `generated.cpp`, `generated.hpp`, `generated_debug.cpp`,
   `defines.h` (`PROGRAM_MD5` is the real MD5 of the assembled program),
   `strucpp_runtime/include/` (from the same strucpp commit), and `conf/`
   (`modbus_master.json` and `modbus_slave.json` from
   `plant/generated/ci/openplc/plc-cellar/`). Entries are sorted with fixed
   timestamps, so the same inputs give the same ZIP.

`build_zip.sh` clones and builds strucpp into
`${XDG_CACHE_HOME:-~/.cache}/cherrymountain/` on first use (git, Node 22 or
later, npm); set `STRUCPP` to use an existing checkout, which must be at the
pinned commit. Run it after every change to `plant.yaml` or the program, and
after `python -m plantgen` and `python sync.py`. `sync.py --check` fails
when the ZIP's `defines.h` MD5 or its `conf/` files are stale, without needing
Node.

The ZIP is a Git LFS file (`.gitattributes`). At about 100 KB it is well under
the repository's 4096 KB `check-added-large-files` limit either way.

`tests/integration/ci_slice_docker.sh` uploads this ZIP to an OpenPLC v4.3.2
runtime and checks the running program (see the topology README).

The Editor still works for hand changes: create a v4 project, import the two
function blocks and the program from `build/plc-cellar-full.st`, and export the
ZIP (Editor 4.3.3 or later for runtime 4.3.2). The headless build is the one
CI and `sync.py` check.

## Modbus layout

| Direction | PLC address | Use |
|---|---|---|
| RIO to PLC | `%IX100.0`, `%IW100` | Polled by `modbus_master` (FC2 and FC4) |
| PLC to RIO | `%QX100.0`, `%QW100` | Valve commands (FC15) and spunding setpoints (FC16) |
| PLC to FUXA | `%QX0.0` upward, `%IW100`, `%MW0` upward | Served by `modbus_slave` on port 502 |

`modbus_slave` (OpenPLC v4.3.2) serves, by PDU address from 0: coil n =
`%QX(n/8).(n%8)`, discrete input n = `%IX`, input register n = `%IWn`, and
holding registers **back to back** as `%QW`, then `%MW`, then `%MD` (2 each),
then `%ML` (4 each). For this program that is holding 0 to 103 = `%QW0` to
`%QW103` and holding **104** to 111 = `%MW0` to `%MW7`. Every segment's length
is its `buffer_mapping` count in `modbus_slave.json`, which plantgen writes as
the highest located index + 1, so the runtime clamps nothing. Adding a `%QW`
moves every `%MW`; regenerate and rebuild.

`plant/generated/ci/registers.csv` lists every address in all three formats.
