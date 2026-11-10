# FUXA projects

`build_project.py` builds every FUXA project of the range from plantgen's tag
lists and alarm list. This file describes the ci project first, then
[the full and lite projects](#the-full-and-lite-projects) and the
[brewhouse batch engine](#the-brewhouse-batch-engine) they carry on scada-fuxa.

## The ci project

`scada-fuxa-project.json` is the project that `cm-fuxa-provision` POSTs to
FUXA's `/api/project` on scada-fuxa ([DESIGN.md
12.2](../DESIGN.md#122-fuxa-two-instances)). It is generated; do not edit it by
hand.

```shell
python3 -I build_project.py          # rewrite scada-fuxa-project.json
python3 -I build_project.py --check  # fail if it is stale
```

The builder reads `plant/generated/ci/fuxa/scada-fuxa-tags.json` and
`plant/generated/ci/alarms.json`, so tag IDs, addresses, and alarm texts always
come from `plant.yaml`. After a change, run plantgen, then `build_project.py`,
then `../sync_ci.py` (it copies the project into `phenix-injects/fuxa/`).

## Contents

- **Device `plc-cellar`:** Modbus TCP to `10.20.10.12`, unit 1, polled every
  second, with the 61 generated tags. Addresses are 1-based, as FUXA expects
  (it subtracts 1), and analog values use FUXA's `divisor` (raw / 100).
- **View `Cellar`:** one column per FV-101 to FV-104 showing side and cone
  temperature, setpoint (writable), glycol valve, head pressure, spunding
  setpoint and valve, and extract, plus a lamp for each of the five alarm bits.
  Pressures are absolute (psia), as stored in the PLC.
- **Alarms:** one FUXA alarm per PLC alarm bit (20 in ci). It is active while the
  bit is 1. The PLC applies the on-delay, so FUXA's own delay is 0. The severity
  comes from `fuxa_severity` in `alarms.json`.
- **DAQ:** PV tags store to FUXA's built-in SQLite (no historian in ci).

## The full and lite projects

`build_project.py --all` also writes `full/` and `lite/`: one project for
scada-fuxa and one for bas-fuxa. The cherrymountain app builds the same
projects at pre-start, so these files are for review and for the `--check`.

- **Devices.** One per PLC, by its northbound plugin: Modbus TCP (plc-cellar,
  plc-utilities, plc-power), OPC UA (plc-brewhouse, node ids `ns=2;s=<tag>`),
  and S7 (plc-packaging, `DB<n>.DBW<byte>` and `DB<n>.DBX<byte>.<bit>`), plus
  one BACnet client device for the building controllers.
- **Scaling.** FUXA applies `divisor` in its Modbus driver only. OPC UA and S7
  tags carry the same factor as a linear `scale` (`rawHigh` = the factor),
  which every driver applies on reads and inverts on writes. BACnet values
  are engineering values already.
- **Views.** The hand-laid Cellar view, and a generated view per device:
  Brewhouse, Cellar points, Packaging, Utilities, Power, and Cellar CO2 on
  scada-fuxa; Building on bas-fuxa. Each row is a label and a value or lamp.
  A tag the operator may write (`access: read-write` in the plantgen tag
  list) also has an input box: a number in the tag's range, or 1 and 0 for a
  bit. That is how an operator resets the CO2 supply shutoff
  (`UTL.CO2.XC01.RESET` on Utilities), sets an entry permit
  (`CEL.ENTRY.HS0n.ENTRY` on Cellar points, `BH.ENTRY.HS0n.ENTRY` on
  Brewhouse), or gives a phase, CIP, or line command.
- **Who writes BACnet.** Only bas-fuxa. The BACnet tags of scada-fuxa are
  read-only (DESIGN.md 10.3), and so is the multi-state occupancy mode on
  bas-fuxa, because FUXA's driver writes analog and binary objects only.
- **Alarms.** The four plant priorities map to FUXA's four levels (critical
  to `highhigh`). Every level but the lowest must be acknowledged. A PLC alarm
  bit carries its own on-delay; an alarm FUXA evaluates on a BACnet value gets
  the rationalized on-delay as `timedelay`. FUXA has no deadband.

## The brewhouse batch engine

The scada-fuxa project of `full` and `lite` (not ci, which has no brewhouse, and
not bas-fuxa) carries one server script, `cmBatchEngine`, from `batch_engine.js`,
and a **Batch** view after Brewhouse ([DESIGN.md
12.2](../DESIGN.md#122-fuxa-two-instances)). The script runs every second and
sequences the ten phases of plc-brewhouse, one brew after another: set the
phase's setpoints and parameter, command Start, wait for Complete, command Reset,
go on to the next. The recipe's numbers (grain bill, mash steps, boil and
whirlpool minutes, temperatures) are read from `../recipes/fire-of-the-phenix.json`
by the builder and embedded in the script, so changing the recipe and running
`build_project.py` changes the brew. All batch state is in tags of FUXA's own
`FUXA Server` device (`BATCH.*`), which FUXA restores after a restart, so a FUXA
restart resumes the batch where it was; the Batch view shows those tags.

**Operator, on the Batch view** (the buttons write `BATCH.CMD`; the engine takes
the command and clears it):

- **Start** adds one brew to the queue. It starts when the brewhouse PLC is Idle
  and neither brewhouse vessel is in confined-space entry; otherwise the Status
  line says why and, after 10 s, the *Refused* alarm lights.
- **Hold** is taken at the next safe point: at once between phases, or by
  commanding Hold of the running PLC phase (every phase output off; a timed step
  keeps the time it has served). **Restart** continues from Held, and is refused
  while a vessel is in entry.
- **Abort** aborts the PLC phase, resets it, empties the queue, and ends the batch
  *Aborted*; **Reset** acknowledges a Complete or Aborted batch, clears the queue
  and the engine's alarms, and resets the PLC.

**Unattended.** The `start-batch` plantevent (SCORCH `baseline-brew-day`) reaches
the brewhouse federate, which counts a request at the panel
(`BH.BATCH.XI02.REQUESTS`, through rio-brewhouse and plc-brewhouse over OPC UA).
The engine queues every new count, writes the count it has taken to
`BH.BATCH.XC02.ACCEPTED` (the PLC copies it back to the panel's
`BH.BATCH.XI03.ACCEPTED`), and starts the brew when the brewhouse is ready, so a
request made while FUXA is down waits at the panel and is taken when it is back.
Without a PLC the federate runs its own autopilot instead (`sim/README.md`).

**Knockout** first reserves a fermenter that plc-cellar reports empty (extract
meter at 0) and lined up (not in entry, CIP permitted); with none, the batch waits
in KNOCKOUT with the *Fermenter* alarm after 10 min and the wort stays in the
kettle. A brew day's two brews go into the same fermenter.

**Faults.** A phase that does not complete in its timeout holds the PLC phase,
raises the *Timeout* alarm, and holds the batch; so do a PLC that does not answer
a command in 30 s, one that aborts or loses the phase, and a PLC link down for
60 s (*PLC* alarm). A script that fails three runs in a row raises the *Engine*
alarm. Every hold names its cause on the Status line, and Restart or Abort clears
it.

`tests/` runs the generated script under Node (18 or newer) against a stub of
FUXA's script API and a simulator of plc-brewhouse and the cellar's status:

```shell
python3 -m pytest fuxa/tests         # from cherrymountain-brewery/
```

`test_batch_engine.py` runs 23 scenarios (an unattended brew, a double brew day,
Hold and Restart, Abort, a phase that never completes, no fermenter, refused
starts, a lost link, a PLC or FUXA restart, a request while FUXA is down, a failing
script), `test_batch_project.py` checks what the builder writes, and
`test_plc_port.py` holds the simulator's handshake to plc-brewhouse's compiled
program scan by scan (it needs strucpp and g++, as `sim/tests/st_engine.py` does,
and skips without them). `sim/tests/test_brewhouse_control_path.py` runs the script
against the compiled PLC and the federates.

## Checked against FUXA source

Checked against `frangoteam/FUXA` at `b2972583` (server 1.3.6), the commit the
`cm-fuxa` image pins:

- Project sections and how `setProject` stores them (`server/runtime/project`).
- Modbus tag fields, memory areas, and the 1-based `address`
  (`server/runtime/devices/modbus`). The Modbus TCP port is read from an
  `address:port` suffix and defaults to 502, so the `port` property is unused.
- Alarm records and their `ackmode` keys (`float`, `ackactive`, `ackpassive`),
  as the runtime compares them (`server/runtime/alarms`), and `timedelay` in
  seconds.
- Tag scaling: `divisor` is read by `server/runtime/devices/modbus` alone;
  `scale` with `mode: linear` by `device-utils.js` for every driver.
- The S7 address forms and types (`WORD`, `INT`, `BOOL`) in
  `server/runtime/devices/s7`, and what a 1 or 0 written to a bit becomes in
  the Modbus, OPC UA, S7, and BACnet drivers.
- The BACnet driver writes `REAL` to analog objects and `ENUMERATED` to
  binary objects at priority 16, and a NULL to any other type.
- `POST /api/signin`, `POST /api/users` (body `{params: {...}}`, `info` a JSON
  string), and `POST /api/project`. This resolves the spike's body-shape
  question.
- `mysettings.json` lives in `<working dir>/_appdata/` and accepts
  `secureEnabled`, `secretCode`, `tokenExpiresIn`, `nodeRedEnabled`, and
  `swaggerEnabled`.
- Server scripts: the body is wrapped in `async function name () { try {...}
  catch (fuxaError) {...} }` (`server/runtime/scripts/msm.js`), so an error is
  logged and never stops the schedule; a scheduled script runs when more than
  `interval` seconds have passed, checked once a second, without waiting for the
  run before to end (`scripts/index.js`); `$getTag`, `$setTag`, and `$getTagId`
  are `devices.getTagValue`, `setTagValue`, and `getTagId`
  (`scripts/index.js`, `devices/index.js`).
- The `FuxaServer` device's tags: a tag with `daq.restored` reads null until FUXA
  has restored it, and one without starts at its `init` or 0
  (`devices/fuxaserver/index.js` `load`). The OPC UA driver turns a written value
  into an integer before applying the linear scale (`devices/opcua/index.js`
  `setValue`: `_toValue`, then `tagRawCalculator`), so the engine writes whole
  degrees.
- The project imported on a local FUXA 1.3.6 through `cm-fuxa-provision`, the
  device loaded with the `modbus-serial` plugin installed, and the admin
  password change worked on a first run and a re-run.

## Unverified

TODO(verify): the view gauges (`svg-ext-value`, `svg-ext-html_input`,
`svg-ext-gauge_semaphore`) are written from the client source and the demo
project, not exported from the editor. Open the project in the FUXA editor once,
confirm the Cellar view renders and the values update, and re-export it if the
widget properties differ.

TODO(verify): the unit and digits ranges use `type: 1` (`PropertyType.output`)
with `fractionDigits`; the semaphore colors use ranges 0 and 1.

TODO(verify): layout enum spellings. Navigation `mode: fix` and `type: inline`
follow the demo project (enum keys). The header `alarms: fix` and the layout
`zoom: enabled` follow the same convention but no sample confirms them.

TODO(verify): the batch engine and its Batch view have run only against the
stubs in `tests/` and the compiled PLC, never in a live FUXA. On the first boot of
the range, open the Batch view as the operator, press Start with the PLC Idle, and
watch `BATCH.STATUS`, the `FUXA Server` tags, and the FUXA log for
`[cmBatchEngine]` lines; confirm that the `FUXA Server` tags keep their values
across a FUXA restart (`daq.restored`), that the script runs once a second, that
the buttons write `BATCH.CMD`, and that an anonymous or operator OPC UA session may
write the plc-brewhouse command, phase, parameter, and setpoint tags.

TODO(verify): alarm records omit `permission` and `permissionRoles`. Confirm
all signed-in users see the alarms with `secureEnabled` on.

TODO(verify): writing a setpoint from the `html_input` gauge in the browser.
The write path itself is checked: `tests/integration/ci_slice_docker.sh` calls
FUXA's `POST /api/setTagValue` for `CEL.FV101.TT01.SP` (60.0), and FUXA multiplies
by the divisor and writes holding register 104 (`%MW0`) on a live OpenPLC v4.3.2.
`%MW0` to `%MW7` are holding registers 104 to 111: v4 serves `%MW` right after the
`%QW` segment, whose length is `qw_count` in `modbus_slave.json`.

TODO(verify): the `plc-cellar` tags' `access: read` in the plantgen file is
ignored here, because FUXA does not use it.
