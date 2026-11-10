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
then `../sync.py` (it copies the project into `phenix-injects/fuxa/`).

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
- **Views.** The hand-laid Cellar view (in the full and lite plants it also
  shows each fermenter's profile stage and has the buttons that put it on the
  PLC's fermentation profile or take it off), and a generated view per device:
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

- **Start** adds one brew to the queue. It starts when the brewhouse PLC is Idle,
  neither brewhouse vessel is in confined-space entry, and a fermenter is empty
  and lined up; otherwise the Status line says why and, after 10 s, the *Refused*
  alarm lights. The brew stays queued and starts when the cause clears.
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

**Fermenters.** A fermenter is free when plc-cellar reports it empty (extract
meter at 0) and lined up (not in entry, CIP permitted). A brew does not start
without one. Knockout then reserves it; if it was lost since the start, the batch
waits in KNOCKOUT with the *Nofv* alarm after 10 min and the wort stays in the
kettle. A brew day's two brews go into the same fermenter, which the view then
shows as full. The reservation is the engine's check and display: the wort goes
where the cellar federate sends it (the fermenter it is already filling, else
the first empty one), which is the same vessel unless a lower-numbered one is
empty but in entry.

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

`test_batch_engine.py` runs 25 scenarios (an unattended brew, a double brew day,
Hold and Restart, Abort, a phase that never completes, no fermenter at the start
or at knockout, refused starts, a lost link, a PLC or FUXA restart, a request
while FUXA is down, a failing script), `test_batch_project.py` checks what the
builder writes, and
`test_brand.py` checks the frame and the process views against `brand/tokens.json`, and
`test_plc_port.py` holds the simulator's handshake to plc-brewhouse's compiled
program scan by scan (it needs strucpp and g++, as `sim/tests/st_engine.py` does,
and skips without them). `sim/tests/test_brewhouse_control_path.py` runs the script
against the compiled PLC and the federates.

## The brand on FUXA's screens

Both FUXA hosts wear the brewery's frame ([brand/README.md](../brand/README.md)): the
header is ink with paper text, the brewery's name, and a 3 px ember stripe under it; the
menu is coal with paper text and the full mark (48 px) at its top; the tab has the
brewery's icon and the title "CherryMountain Brewery SCADA" (or "... BAS"); and the sign-in
dialog is a coal card with the mark above its title, ink fields, and an ember OK button.
The process views keep their gray background and their own colors, and the alarm levels keep
the reserved colors. Where each piece comes from:

| Piece | Mechanism | Source |
|---|---|---|
| Header and menu colors, the mark in the menu | `layout.header.bkcolor`/`fgcolor`/`height`, `layout.navigation.bkcolor`/`fgcolor`/`logo` (a URL), written by `build_layout` from `brand/tokens.json` roles (`header_bg`, `header_fg`, `menu_bg`, `menu_fg`) | `client/src/app/_models/hmi.ts` (`HeaderSettings`, `NavigationSettings`); the menu template `sidenav.component.html` shows `logo` in place of FUXA's title |
| The name in the header | A header item of type `label` with the name in `property.text`. FUXA reads `header.title` but its home template never shows it | `home.component.html` (`header-items-button`), `home.component.ts` `loadHeaderItems` |
| The ember stripe, the signed-in icon, the mark's row, the sign-in dialog | `layout.customStyles`, a CSS string FUXA adds to every page; `frame_css()` writes it with the token colors, and every rule starts at `.header`, `.sidenav-menu`, `.mat-mdc-dialog-container:has(app-login)`, or `app-login`, so no view takes one | `app.component.ts` `applyCustomCss`; the dialog is Angular Material 18's MDC dialog, themed by its own custom properties (`--mdc-dialog-container-color`, `--mdc-protected-button-container-color`) |
| The mark as a file | `brand/icon.svg`, injected to `/opt/fuxa/server/_images/Brand/icon.svg`; FUXA serves `_images` at `/_images` with no sign-in | `server/main.js` (`imagesFileDir`, `app.use('/_images', ...)`) |
| The tab icon | `brand/favicon.ico`, injected over `/opt/fuxa/client/dist/favicon.ico` | `client/dist/index.html` links `favicon.ico` |
| The page title | `66-fuxa-brand.sh` rewrites `<title>FUXA</title>` in `client/dist/index.html` from `FUXA_TITLE` (the app writes it from the host's role; the ci slice's `fuxa.env` holds it). FUXA has no setting for it | `client/dist/index.html` at the pin |

Everything on the table that is a file comes from `brand/` through `sync.py`'s copies in
`phenix-injects/brand/`; the cherrymountain app injects it (`brand.py`) and the ci topology
injects the same files. `tests/test_brand.py` checks that the frame colors are the tokens,
that the CSS names only brand colors (and no alarm color), that every rule starts in the
frame, that no view paints a brand color, that the alarm levels keep their reserved colors,
and that the Batch view's buttons keep their own readable style.

Seen rendered, in a real FUXA 1.3.6 (the pinned commit, with the `node-opcua` and
`modbus-serial` drivers, `secureEnabled` on), through the real `cm-fuxa-provision.sh` on a
fresh user directory (seeded admin, operator account, project import), with `66-fuxa-brand.sh`
run on the client's `index.html`, the two files injected, and a fake PLC behind it: the
guest view, the sign-in dialog, the operator's Cellar, Batch, Brewhouse, Cellar points, and
Packaging views, three active alarms (high, medium, and message levels in their reserved
colors) with the alarm list open, the tab's icon and title as the server serves them, and no
request to anything outside FUXA. Pressing Start on the Batch view as the signed-in operator
started a batch (the engine ran MILL), which also settles the open question below about the
buttons with `secureEnabled` on.

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
- Layout and brand: `NavigationSettings.logo` is the menu's image (`sidenav.component.ts`
  `setLayout` copies it, the template shows it instead of the title), a header `label`
  item takes its text from `property.text` (`loadHeaderItems`), `layout.customStyles` is
  appended as a `<style>` (`applyCustomCss`), the header's account icon is colored
  `rgb(59, 144, 255)` inline once someone signed in (`home.component.html`), `index.html`
  names the page `FUXA` and links `favicon.ico`, and `server/_images` is served at `/_images`
  without sign-in (`main.js`).
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

The batch engine and its Batch view were run once by hand in a real FUXA server
(1.3.6 at the pinned commit, with the `node-opcua` and `modbus-serial` drivers),
not in CI: the generated full scada-fuxa project was imported through
`POST /api/project` with only the two device addresses changed, against an OPC UA
server that ran the tests' port of plc-brewhouse's handshake and a Modbus server
for the fermenters' status. Seen there:

- FUXA runs the script, and a request at the panel counter becomes a batch that
  goes through all eleven phase runs, with the setpoints and the parameter
  arriving at the PLC as the registers the tests expect (167 F as 1670).
- The script's hand-over to the PLC costs about 10 s a phase run, 2 min a brew.
- Knockout reserves FV-101 from the real Modbus reads, and the second brew goes
  into the same fermenter.
- FUXA restores all 21 state tags after it is stopped in the boil and started
  again, and the batch goes on without a second Start of the boil.
- The Batch view renders in Chromium, and Start, Hold, Restart, Abort, and Reset
  each reach the engine and the PLC. That run found the buttons dead as first
  generated (see `ViewBuilder.button`) and the view too short for its last line.

TODO(verify): still open on the first boot of the range: that OpenPLC's OPC UA
server lets FUXA's session write the plc-brewhouse command, phase, parameter,
accepted-count, and setpoint tags; and that the phase timeouts, which are
estimates, are longer than the real phases. (The buttons do work for the signed-in
`operator` with `secureEnabled` on: the HMI kiosk's script signed in, and Start
and Abort reached the engine, in a real FUXA 1.3.6 with the image's Chromium; see
the app's README, "HMI kiosk".)

TODO(verify): on the first boot, that the injected files land where FUXA reads them
(`/opt/fuxa/client/dist/favicon.ico`, `/opt/fuxa/server/_images/Brand/icon.svg`: the
service's working directory is `/opt/fuxa/server`, and the unit may write only under
`/opt/fuxa`), and that `66-fuxa-brand.sh` runs before anyone opens the page. The harness
ran FUXA with `--userDir`, so its `_images` was in the user directory.

`cm-fuxa-provision` does not import a project again when FUXA already holds the same
devices and tags, so a layout-only change (as the brand is) does not reach a FUXA that
was provisioned before it: redeploy the experiment, or POST the project by hand.

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
