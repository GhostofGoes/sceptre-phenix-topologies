# FUXA projects

`build_project.py` builds every FUXA project of the range from plantgen's tag
lists and alarm list. This file describes the ci project first, then
[the full and lite projects](#the-full-and-lite-projects).

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
