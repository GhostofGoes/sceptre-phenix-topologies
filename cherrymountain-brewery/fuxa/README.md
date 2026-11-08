# scada-fuxa FUXA project (ci variant)

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

## Checked against FUXA source

Checked against `frangoteam/FUXA` at `b2972583` (server 1.3.6), the commit the
`cm-fuxa` image pins:

- Project sections and how `setProject` stores them (`server/runtime/project`).
- Modbus tag fields, memory areas, and the 1-based `address`
  (`server/runtime/devices/modbus`). The Modbus TCP port is read from an
  `address:port` suffix and defaults to 502, so the `port` property is unused.
- Alarm records and their `ackmode` keys (`float`, `ackactive`, `ackpassive`),
  as the runtime compares them (`server/runtime/alarms`).
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

TODO(verify): writing a setpoint from the `html_input` gauge. FUXA divides on
read and multiplies on write, and the PLC accepts writes to `%MW0` to `%MW7`
(Modbus holding registers 1024 to 1031). The write path needs a live PLC.

TODO(verify): the `plc-cellar` tags' `access: read` in the plantgen file is
ignored here, because FUXA does not use it.
