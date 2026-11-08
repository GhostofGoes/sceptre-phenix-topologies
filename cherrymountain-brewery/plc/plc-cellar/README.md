# plc-cellar program (ci variant)

IEC 61131-3 Structured Text for the cellar PLC ([DESIGN.md
12.1](../../DESIGN.md#121-openplc-v4-runtime-v3-fallback)).

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

## Build the OpenPLC Editor ZIP

ZIPs come from the Editor and are not committed by hand. The boot script
repacks the ZIP with this node's plugin configs
(`phenix-injects/openplc/plc-cellar/conf/*.json`), so the Editor project only
needs the program.

1. Start the OpenPLC Editor (the `ews` VM, or a desktop build) and create a
   project for the OpenPLC v4 runtime, target board "OpenPLC Runtime".
2. Add a program named `plc_cellar`, language Structured Text.
3. Add the two function blocks from `plc-cellar.st` (`ZoneControl` and
   `PressureAlarm`) as FUNCTION_BLOCK POUs in ST, pasting each variable
   declaration and body.
4. For `plc_cellar`, create the variables from the `VAR` blocks in
   `build/plc-cellar-full.st` (run `assemble.py` first). The located variables
   come from `plant/generated/ci/openplc/plc-cellar/located_vars.st`. Enter each
   row's name, type, and location exactly as listed (for example
   `CEL_FV101_TT01_PV`, `UINT`, `%IW101`). Paste the program body.
5. Set the task to a 100 ms cyclic interval and the instance to `plc_cellar`
   (the `CONFIGURATION` block at the end of the assembled file shows the
   values).
6. Compile in the Editor, then export the program ZIP.
7. Commit it through Git LFS as
   `cherrymountain-brewery/phenix-injects/openplc/plc-cellar.zip`. The ci
   topology injects that path.

TODO(verify): the Editor's variable table and ZIP layout were not exercised
here. If the Editor can import the plain-text file directly, step 3 to 5
collapse into one import of `build/plc-cellar-full.st`.

## Modbus layout

| Direction | PLC address | Use |
|---|---|---|
| RIO to PLC | `%IX100.0`, `%IW100` | Polled by `modbus_master` (FC2 and FC4) |
| PLC to RIO | `%QX100.0`, `%QW100` | Valve commands (FC15) and spunding setpoints (FC16) |
| PLC to FUXA | `%QX0.0` upward, `%IW100`, `%MW0` upward | Served by `modbus_slave` on port 502 |

`plant/generated/ci/registers.csv` lists every address in all three formats.
