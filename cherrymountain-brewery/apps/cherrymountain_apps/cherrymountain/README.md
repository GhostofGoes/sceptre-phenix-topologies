# cherrymountain App

phenix user app that provisions the CherryMountain Brewery topology
([DESIGN.md 16](../../../DESIGN.md#16-new-and-extended-software)): it checks
the scenario against `plant/plant.yaml`, renders every per-host file the VMs
need at boot, watches provisioning, and exports the run's data at the end.

```
name:   cherrymountain
exe:    phenix-app-cherrymountain
stages: configure, pre-start, post-start, running, cleanup
```

## Where it goes in the scenario

After `ot-sim` and before `helics`. ot-sim annotates the field devices with
`helics/federate`, this app annotates the sim-* federate nodes, and helics
counts both ([DESIGN.md 15.2](../../../DESIGN.md#152-apps)).

In the full scenario the `mirror` app comes before this one: phenix runs the
apps of every stage in the order of the scenario's list, and this app's post-start
needs the ports that the mirror app records in its own post-start
([Mirror ports](#mirror-ports)).

plantgen emits the block with `runPeriodically: 5m`, so phenix runs the
`running` stage every five minutes and the status stays current. The schedule
belongs to the process that starts the experiment:

- `phenix ui` keeps it for an experiment it starts: a `phenix workflow apply`
  (which starts the experiment through the server) or a start from the UI.
- A plain `phenix experiment start <name>` from the CLI does not. Pass
  `--honor-run-periodically` (the command then stays in the foreground until
  the experiment stops), or run the stage by hand:
  `phenix experiment trigger running <name> cherrymountain`. Name the app:
  without it the command runs the `running` stage of every app, and the
  `scorch` app's is SCORCH run 0.

## Stages

| Stage | What it does |
|---|---|
| configure | Validates the metadata (strict Pydantic models), loads `plant.yaml` for `variant` through plantgen, and checks it against the topology: every plant host of the variant exists, and each of its interfaces has the plant.yaml name, address, prefix length, and VLAN. Annotates each sim node that runs federates with `helics/federate: [{broker: sim-ctl\|mgmt, fed-count: N}]`. Writes each host's `sohProfile` (processes, listeners, custom tests) into this app's host metadata, where phenix's soh app reads it |
| pre-start | Validates again, then renders the files below into `<experiment dir>/cherrymountain/<host>/` and injects them. An injection replaces any other injection to the same destination, so a rendered file wins over a static one in the topology. `injections.json` lists what went where |
| post-start, running | Runs `systemctl is-active` for each host's provisioning units through cc (short timeouts; never blocks on a VM that has not booted) and reads the plant KPIs from the sim-ctl event API. Writes both to `status.apps.cherrymountain`. miniccc runs a command without a shell, so the check is the plain unit list. phenix stops the experiment when a post-start stage fails, so post-start records an error it cannot work around (`error: <type>` in the status) instead of failing; `running` reports it to its caller. When the scenario has a `mirror` app, both stages first set `options:df_default=false` on its GRE ports ([Mirror ports](#mirror-ports)) |
| cleanup | Runs the export scripts on the FUXA hosts and the historian, and copies `fuxa-export.tar.gz` (alarm history from the API, plus FUXA's `_db` with the alarm and SQLite DAQ stores) and `influx-backup.tar.gz` to `<files>/cherrymountain/<host>-<file>`. A failed export is logged and recorded, and does not fail the stage |

## Inputs it reads

Everything comes from `sourceDir`, the cherrymountain-brewery directory inside
the phenix container (`phenix workflow apply` reads it from there):

| Input | Used for |
|---|---|
| `plant/plant.yaml` (through `tools/plantgen`) | Hosts, interfaces, roles, federates, northbound protocols |
| `plant/generated/<variant>/` | `helics.json`, `openplc/<plc>/modbus_*.json`, `tags.json`, `registers.json` |
| `phenix-injects/openplc/<program>.zip` (ci), `phenix-injects/openplc/<variant>/<program>.zip` (full, lite) | The program ZIPs that `plc/plc-cellar/build_zip.sh` and `plc/common/build_zip.sh` build (Git LFS; a pointer file fails pre-start with a message that says to run `git lfs pull`) |
| `fuxa/build_project.py` | The FUXA project of each FUXA host (scada-fuxa, and bas-fuxa in full and lite), built for the variant |
| `phenix-injects/openplc/`, `phenix-injects/fuxa/` | The provisioning scripts and units, injected as they are |
| `phenix-injects/brand/`, `phenix-injects/grafana/` | The brewery's mark, icons, and colors (copies of `brand/` that `sync.py` keeps), and Grafana's image script. `brand.py` says which file goes where on which host |

plantgen and the FUXA builder are imported from `sourceDir`, not installed in
the image, so the code always matches the plant data next to it.

## Metadata

App block:

| Field | Type | Default | Meaning |
|---|---|---|---|
| `variant` | name | required | plant.yaml variant (`ci`, `lite`, `full`) |
| `sourceDir` | absolute path | `/phenix/topologies/cherrymountain-brewery` | The topology directory |
| `simCtl.hostname` | hostname | `sim-ctl` | HELICS root broker and event API host |
| `simCtl.eventPort` | port | `8080` | Event API port inside sim-ctl |
| `simCtl.timeout` | seconds | `10` | Event API request timeout |
| `simCtl.vizPort` | port | `8090` | cm-viz, the white cell's 3D view, on 127.0.0.1 in sim-ctl (DESIGN.md 15.10) |
| `mgmt.enabled` | bool | `true` | Inject the MGMT nftables policy |
| `mgmt.interface` | interface | `mgmt` | The topology's name of the MGMT interface. It is not the guest's device name (phenix does not rename NICs): the startup hook finds the device by the host's MGMT address |
| `mgmt.brokerPorts` | ports | `[23404, 23405, 23406]` | HELICS broker ports on sim-ctl |
| `mgmt.federatePortMin`, `mgmt.federatePortMax` | ports | `23407`, `23999` | HELICS federate core ports, which the broker connects back to. Nothing pins them: the broker assigns each core one, counted up per host from just above its own ports (23414 in HELICS 3.6), so keep the range starting right after the broker ports |
| `mgmt.opensearchPort` | port | `9200` | Ground-truth OpenSearch on sim-ctl |
| `mgmt.exclude` | hostnames | `[]` | MGMT members that keep no policy |
| `historian.org` | name | `cherrymountain` | InfluxDB organization |
| `historian.buckets` | names | `[scada, bas, oee]` | InfluxDB buckets |
| `historian.retentionHours` | integer | `720` (30 days) | Bucket retention; `0` keeps data forever |
| `historian.recordsBucket` | name | `records` | The TTB records bucket (DESIGN.md 12.4): a daily InfluxDB task copies the last value of each `recordsTags` series from `scada` into it; empty turns it off |
| `historian.recordsRetentionHours` | integer | `26280` (3 years) | The records bucket's retention (27 CFR 25.300, C.R.S. 44-3-503(6)) |
| `historian.recordsTags` | Flux regular expression | packaging counts and rejects, BBT levels, fermenter extract | The tag names copied into the records bucket |
| `historian.influxPort`, `grafanaPort`, `mqttPort` | ports | `8086`, `3000`, `1883` | Historian services |
| `historian.mqttTopicRoot` | topic | `cherrymountain/frontrange` | PackTags topic root |
| `wazuh.manager` | hostname | `wazuh` | Wazuh manager; no `wazuh` block means no Wazuh files |
| `wazuh.interface`, `wazuh.enrollmentPort` | interface, port | `IF0`, `1515` | Where agents enroll |
| `wazuh.agents` | hostnames | `[]` | The hosts that run an agent and get the enrollment password: an explicit allowlist (plantgen writes it from plant.yaml `app.wazuh.agents`). Empty enrolls nobody |
| `secrets.*` | string | generated | `openplcPassword`, `fuxaPassword`, `fuxaOperatorPassword`, `fuxaJwtSecret`, `influxPassword`, `influxToken`, `grafanaPassword`, `guacamolePassword`, `wazuhEnrollment`, `malcolmPassword`, `wazuhPassword`, `vendorGuacPassword`, `vendorTotpSecret` (Base32), `vendorEwsPassword` |
| `wireguardKeys` | `generate` or `scenario` | `generate` | `generate` replaces the scenario's `wireguard` keys (plantgen's fixed-seed keys) with per-experiment ones at configure |
| `verify.timeout`, `verify.clientGrace` | seconds | `15`, `5` | Per-check timeouts in post-start and running |
| `verify.recheck` | `unprovisioned` or `all` | `unprovisioned` | Which hosts `running` asks again. A check sets minimega's cc filter, which is one per namespace, for a moment; a command that phenix's soh app or a SCORCH component sends from another process in that moment goes to the wrong VM. So by default a host is not asked again once it is `provisioned` (the state is then kept until the next start), and a settled plant gets no cc commands from this app. `all` asks every host on every pass. State of health is the check for a service that stops later |
| `vendorTechnicians` | list of short names | `[akim, jdiaz]` | Ridgeline's technicians: each gets a named Guacamole account (`ridgeline-<name>`) with its own TOTP key and its own EWS account (DESIGN.md 13). The first is the one vendor-ws's scripted session signs in as |
| `exportOnCleanup` | bool | `true` | Run the cleanup exports |

Host metadata, selected by `type`:

| `type` | Fields (defaults) | Renders |
|---|---|---|
| `openplc` | `program` (the hostname), `northbound` (plant.yaml's: `modbus_slave`, `opcua`, `s7comm`), `opcua.port` (4840), `opcua.interface` (IF0), `opcua.debugMap`, `opcua.cycleTimeMs` (100), `s7comm.port` (102), `restPort` (8443), `compileTimeout` (900) | Program ZIP, `conf/modbus_master.json` and `modbus_slave.json` (plantgen), `conf/opcua.json` (from the compiled program's debug map) or `conf/s7comm.json`, `/etc/default/cm-openplc`, the provisioning script, unit, and boot hook |
| `fuxa` | `role` (`scada`), `project` (built), `expectDevices` (every project device), `daq` (`auto`: InfluxDB when the app has a historian, else SQLite), `bucket` (the role), `port` (1881) | Project JSON, `mysettings.json` (secure mode, JWT secret, DAQ store, no Node-RED), `/etc/default/cm-fuxa` (the `admin` and `operator` passwords), the provisioning script, unit, and hook, `cm-export-fuxa`, and the brand: the tab icon, the mark, and `66-fuxa-brand.sh` with `FUXA_TITLE` ("CherryMountain Brewery SCADA" or "... BAS") |
| `rio` | `telnet` (`interface` IF0, `port` 23, `banner` default) | Nothing, unless `telnet` is set: then the ot-sim `config.xml` the ot-sim app rendered gets the telnet module (relay-main). Checked otherwise (ot-sim devices of any role) |
| `sim-ctl` | `federates` (plant.yaml's for this host) | As `sim-federate` when it runs any. Where the ground-truth Grafana runs (`ground-truth.env`): its dashboard with the brand's series colors, and the brand's Grafana images and script |
| `sim-federate` | `federates` (plant.yaml's for this host), `logLevel` (`INFO`) | `helics.json`, `/etc/cherrymountain/federates`, `/etc/default/cm-federate-<name>`, `cm-federate@.service`, `60-cm-federates.sh` |
| `historian` | `interface` (IF0) | `/etc/default/cm-historian`, Telegraf, Mosquitto, Grafana data sources and dashboard, systemd drop-ins, `cm-historian-provision` and its unit and hook, `cm-export-historian`, and the brand's Grafana images and script (the plant dashboard's lines take the series colors of `brand/tokens.json`) |
| `hmi` | `fuxa` (`scada-fuxa`), `view` (`Overview`), `interface` (the FUXA host's, IF0) | `/etc/default/cm-kiosk` (the view's URL), `/etc/default/cm-kiosk-credentials` (0600: the `operator` sign-in), `cm-kiosk`, XDG autostart entry ([HMI kiosk](#hmi-kiosk)) |
| `guacamole` | `connections` (`ews-rdp`, `ews-ssh`), `recording` (true), `totp` (true) | Guacamole initdb SQL (salted hash only), `/etc/default/cm-guacamole` |
| `wazuh-manager` | | With the app's `wazuh` block: the enrollment and dashboard admin passwords (`/etc/default/cm-wazuh-manager`) and the boot hook |
| `bbmd` | | Nothing; the topology injects cm-bbmd.service and its settings from `phenix-injects/bbmd/`. Checked only (SoH: the `cm_bbmd.py` process) |
| `ews` | | `/etc/default/cm-ews` (0600): the OpenPLC REST account `cm_plc_tool.py` uses, the same one the app creates on every PLC |

Every topology node with a `mgmt` NIC, listed in the app or not, gets
`/etc/cherrymountain/nftables-mgmt.nft`, `/etc/default/cm-mgmt` (its MGMT
address), and the startup hook `4-cm-mgmt-nftables.sh`. The policy only matches
the MGMT interface and allows ICMP echo, HELICS, OpenSearch, and NTP to sim-ctl,
and HELICS from the broker to the federate cores; everything else on MGMT is
dropped ([DESIGN.md 10.1](../../../DESIGN.md#101-zones-and-conduits-purdue-model--isaiec-62443)).
phenix does not rename a guest's NICs, so the hook runs after
`3_interfaces-start.sh`, finds the device that holds the MGMT address, and
passes its name to the ruleset (`nft -D mgmt_if=<device>`).

## Example

The ci variant:

```yaml
- name: cherrymountain
  metadata:
    variant: ci
  hosts:
    - hostname: sim-ctl
      metadata:
        type: sim-ctl
    - hostname: sim-process
      metadata:
        type: sim-federate
    - hostname: rio-cellar
      metadata:
        type: rio
    - hostname: plc-cellar
      metadata:
        type: openplc
    - hostname: scada-fuxa
      metadata:
        type: fuxa
```

A fuller plant (excerpt):

```yaml
- name: cherrymountain
  metadata:
    variant: full
    wazuh:
      manager: wazuh
  hosts:
    - hostname: plc-brewhouse
      metadata:
        type: openplc
        opcua:
          debugMap: plc/plc-brewhouse/debug_map.json
    - hostname: historian
      metadata:
        type: historian
    - hostname: hmi-brewhouse
      metadata:
        type: hmi
        view: Brewhouse
    - hostname: dmz-jump
      metadata:
        type: guacamole
    - hostname: wazuh
      metadata:
        type: wazuh-manager
    - hostname: ot-svc
      metadata:
        type: bbmd
    - hostname: bas-bbmd
      metadata:
        type: bbmd
```

## Status

`status.apps.cherrymountain` after post-start or running:

```json
{
  "stage": "running",
  "updated": "2026-11-09T01:00:00+00:00",
  "provisioned": "5/5",
  "provisioning": {"plc-cellar": {"state": "provisioned"}, "scada-fuxa": {"state": "pending", "detail": "cm-fuxa-provision.service=activating"}},
  "kpis": {"available": true, "batches": 2, "bottles": 3968, "packaged_bbl": 13.0, "energy_kwh": 120.0, "kwh_per_bbl": 9.23, "demand_kw": 88.5}
}
```

Host states: `provisioned`, `pending`, `failed`, `unreachable` (no C2 client
yet), `not-checked` (no provisioning unit). After cleanup, `exports` maps each
host to the exported file, or to `failed: <error type>`. With a `mirror` app in
the scenario the status also has a `mirror` entry
([Mirror ports](#mirror-ports)).

Read it from the stored experiment:

```shell
docker exec phenix phenix config get experiment/<name> -o json -p
```

and look under `status.apps.cherrymountain`. `updated` says when the last
`running` (or post-start) pass wrote it. Right after a start most hosts are
`pending` or `unreachable` and `kpis.available` is false with the reason; the
next `running` pass fills them in.

## Mirror ports

The full variant mirrors the plant's VLANs to Malcolm with phenix's `mirror` app,
which adds a GRE tunnel port to the Open vSwitch bridge of every cluster host. A
mirrored frame that fills the path MTU grows past it inside the tunnel, and Open
vSwitch sets DF on the outer header by default, so full-size frames are lost
unless the port has `options:df_default=false`. The mirror app does not set it,
and a person had to run an `ovs-vsctl` loop in the minimega container after every
start. This app does it:

- **Which ports.** The ones the mirror app recorded in `status.apps.mirror`
  (`mirrors.<target>.mirrorName`, the tunnel port, which is also the mirror's
  name), not every GRE port on the host. **Which hosts.** Every cluster host of
  `status.schedules`, as the mirror app creates a port on each.
- **How.** `ovs-vsctl --timeout=10 set interface <port> options:df_default=false`
  through minimega's `shell`, in the experiment's namespace: on the headnode
  `shell`, on another host `mesh send <host> shell`, which is what the mirror app's
  own `ovs-vsctl` calls do (`mm.MeshShell`). minimega runs the command in its
  container, where Open vSwitch is, without a shell.
- **When.** post-start, and again at every `running` pass. The mirror app has to
  record its status first, so the full scenario lists it before this app. If its
  status is not there (the app is listed after this one, or its post-start
  failed), the step records `dfDefault: waiting` and `running` sets the option as
  soon as the status appears.
- **Never fails the stage.** A failed start stops the experiment, so a host that
  does not answer, a missing port, or a dead minimega socket is logged and
  recorded, and the next `running` pass tries those again.
- **Safe to repeat.** Setting an option to the value it has changes nothing in
  Open vSwitch. A port that the status records as set on a host is not sent
  again, so a settled experiment gets no commands from the `running` stage; a
  mirror app that ran again made new ports (new random names), which are set.
- **Nothing to do** without a `mirror` app (lite and ci), with it disabled, in a
  dry run, or when the mirror uses ERSPAN instead of GRE (`skipped`).

The record, under `status.apps.cherrymountain.mirror`:

```json
{
  "dfDefault": "set",
  "updated": "2026-11-10T01:00:00+00:00",
  "ports": {
    "kQ7xT2mLb9NcR4d": {"target": "malcolm", "bridge": "cherrymtn", "hosts": {"phenix-host": "set", "node2": "set"}}
  }
}
```

`dfDefault` is `set` (every port on every host), `incomplete` (a host shows
`failed: <reason>`), `waiting`, or `skipped`. To check by hand, in the minimega
container of a cluster host: `ovs-vsctl get interface <port> options:df_default`
prints `"false"`.

## Credentials

Range-only. A value set under `secrets` wins; every other one is generated
once per experiment into `<experiment dir>/cherrymountain/secrets.json` (mode
0600) and reused on restarts, so the VMs and their already-provisioned
services agree. Files that hold one are written and injected mode 0600.

- **Vendor persona.** `ridgeline.env` (per technician of `vendorTechnicians`:
  the Guacamole password and TOTP key and the EWS password; the first one's
  from `secrets.vendor*`, the others' kept in `<experiment dir>/cherrymountain/
  vendor.json`, 0600) is rendered for every node the
  topology gives it (dmz-jump, ews, vendor-ws) and replaces the committed
  copy, so the three always agree and change with each experiment.
- **FUXA accounts.** Both FUXA hosts get two accounts. `admin` is the editor
  account (`fuxaPassword`). `operator` (`fuxaOperatorPassword`) is in FUXA's
  Operator group (`groups: 2`): it may write tags from a view and acknowledge
  alarms, and cannot open the editor or call the project, users, settings, or
  device APIs, which FUXA keeps for administrators (`groups` -1 or 255). A client
  that opens `/home` with no session is the guest: it sees the views and FUXA
  refuses its writes (the settings leave `secureOnlyEditor` and `userRole` unset,
  and the app never sets them). The HMI kiosks sign themselves in as `operator`
  ([HMI kiosk](#hmi-kiosk)), so an operator station writes values and presses
  buttons with nobody at it; any other browser signs in by hand (the account icon
  at the top right of the header, or a write, which opens the same dialog) with the
  password from `secrets.json`, and the session ends after `tokenExpiresIn` (12 h)
  or at sign-out. `cm-fuxa-provision`
  creates the account after it sets the admin password, resets its password and
  group when they differ, and fails when FUXA lets the account use the users API.
  A re-run (a reboot) and a project re-import leave it alone. The SoH check
  `fuxa-operator-account` signs in as `operator` and checks the same.
- **Web consoles.** `malcolm_password` (Malcolm's user `malcolm`) and
  `wazuh_password` (the Wazuh dashboard's user `admin`) are generated like the
  rest, so the passwords are in `secrets.json` and nobody has to open a VM's
  console to read them. Both are 24 characters of letters and digits; the Wazuh
  one also has one of `.+-`, because the Wazuh passwords tool takes only 8 to 64
  characters with an upper case letter, a lower case letter, a digit, and one of
  `.*+?-` (it also puts the password into an `eval`, so the app leaves out `*` and
  `?`). A value set under `secrets` that breaks its rule is refused at
  configure, and so is a hand-edited `secrets.json` (delete the key to
  regenerate). Where they go:
  - Malcolm: for every node the topology gives `cm-malcolm-provision` (malcolm),
    `/etc/default/cm-malcolm-credentials` (0600, `MALCOLM_ADMIN_PASSWORD`),
    which only `cm-malcolm-provision.service` reads, after the topology's own
    `/etc/default/cm-malcolm` (the sizing profile, which the app does not touch).
  - Wazuh: `WAZUH_ADMIN_PASSWORD` in `/etc/default/cm-wazuh-manager` (0600),
    read by `cm-wazuh-provision.service`. The script has the passwords tool
    set the `admin` user's password from a root-only file (`-f`), so it is on no
    command line, and the tool still generates the other users' (the indexer
    service accounts and the Wazuh API users); all of them are in
    `/var/lib/cm-wazuh/passwords.txt` on the VM.
  Without the app's value (the scripts run by hand), each script generates its own
  as before. The VMs keep a root-only copy either way
  (`/var/lib/cm-malcolm/admin-password`, `/var/lib/cm-wazuh/passwords.txt`), so
  the console path still works.
- **WireGuard.** With `wireguardKeys: generate`, configure replaces each
  `wireguard` host's private key with one kept in
  `<experiment dir>/cherrymountain/wireguard.json` (0600) and rewrites every
  peer entry that named the old public key. The app must come before
  `wireguard` in the scenario, as it does.
SoH test scripts never hold one: a check that needs one sources the root-only
settings file on the VM. Errors and warnings name the host and field, never
the value.

## HMI kiosk

`hmi-brewhouse` and `hmi-packaging` sign themselves in to FUXA as `operator`, so
an operator station writes values and presses buttons with nobody at it, for the
whole run. `/usr/local/bin/cm-kiosk` does it. It is a Python 3 script (standard
library only: noble's `python3-pydantic` is 1.x), started at login by
`/etc/xdg/autostart/cm-kiosk.desktop` as root, because the desktops autologin as
root.

1. It waits until FUXA's project has the view (`KIOSK_WAIT`, 10 minutes, then it
   goes on anyway). `cm-fuxa-provision` makes the account first and imports the
   project after it, and a page that loaded the old project keeps it: FUXA's
   client does not notice an import (seen, below).
2. It starts Chromium on the view with the flags it always had, plus
   `--remote-debugging-pipe` and a new `--user-data-dir` in `/run/cm-kiosk`. The
   debugging protocol then runs over two inherited file descriptors (3 and 4): no
   port, no socket.
3. It signs in with `POST /api/signin` itself, so the password never enters the
   browser, and hands the browser the profile FUXA answered. FUXA's client keeps
   its session in `sessionStorage` (`currentUser`), per tab, and reads it once
   when the page starts (`auth.service.ts`, constructor); it opens its socket with
   the token (`hmi.service.ts`), and the server checks the token when the socket
   connects (`server/runtime/index.js`). So the script registers a snippet with
   `Page.addScriptToEvaluateOnNewDocument` that writes the profile into
   `sessionStorage` before the page's own scripts run (for the view's origin, in
   the top frame only), and reloads the page. FUXA reloads the page itself after
   a server restart or a sign-out; the snippet restores the session then too.
4. Every `KIOSK_POLL` seconds it asks the page what it holds. When the token has
   spent two thirds of its life (8 h of 12 h), it signs in again, replaces the
   snippet, and reloads the page, so a half-typed value is lost and the view
   flickers about every 8 hours. FUXA's own renewal does not do this for a screen
   nobody touches: `/api/heartbeat` renews only after a click, and at expiry it
   answers `guest`, whereupon the page signs out and reloads as a guest.

| Situation | What the kiosk does |
|---|---|
| FUXA is down, or has no project yet, at start | Waits `KIOSK_WAIT`, then opens the view anyway (Chromium's error page while FUXA is down) |
| A sign-in fails: FUXA down, the account not made yet (401), a wrong password | The view stays, as a guest, which cannot write. The status says `sign-in as operator failed: <reason>`. It tries again after 10, 20, 40, 80, then every 120 s (`KIOSK_RETRY_MIN`, `KIOSK_RETRY_MAX`), and turns an error page into the view when FUXA answers |
| FUXA restarts, the token is still good | The page's socket reconnects with its token; if FUXA reloads the page, the snippet restores the session |
| A person signs in as another account at the kiosk | Signed back in as `operator` at the next poll: the kiosk is an operator station (the editor is for `ews`). To let another live session stand, make `judge()` in the script accept it |
| FUXA is down longer than the token lives | The page is a guest from its first reconnect (`jwt expired`); the next sign-in after FUXA answers restores it, within `KIOSK_RETRY_MAX` |
| The browser exits (crash, kill) | Started again after 10 s, 20 s, ... 120 s while it keeps exiting within a minute; signs in again |
| The browser does not answer for 60 s (a tab crash: "Aw, Snap!") | Killed and started again |
| The debugging pipe is unusable (Chromium exits at once, or never answers) | The next browser starts without the pipe: the view, as a guest. The one after it tries the pipe again |
| No `/etc/default/cm-kiosk-credentials`, or no user or password in it | A guest kiosk, as before |

Files:

| File | Holds |
|---|---|
| `/etc/default/cm-kiosk` (0644) | `KIOSK_URL`; optional `KIOSK_BROWSER` (`chromium`), `KIOSK_WAIT` (600), `KIOSK_POLL` (5), `KIOSK_RETRY_MIN` (10), `KIOSK_RETRY_MAX` (120), seconds |
| `/etc/default/cm-kiosk-credentials` (0600) | `KIOSK_USER` (`operator`) and `KIOSK_PASSWORD` (`fuxa_operator_password`). Only the HMI hosts get it, like the Malcolm credentials file only the Malcolm sensor does. The kiosk's user is root; the file is root-only |
| `/run/cm-kiosk/status` | `state` (`operator`, `guest`, `starting`, `down`), `detail`, `updated`. No secret. The SoH check `kiosk-operator-session` reads it (and fails when it is older than two minutes); the SoH process check is the browser (`chromium.*--kiosk`), because `cm-kiosk` stays up to restart it |

The password is never on a command line or in the browser's environment, and the
script logs only the reason a sign-in failed. The browser holds the operator's token
(12 h, renewed), which any script in the FUXA page can read anyway. The sign-in is
an HTTP `POST /api/signin` on port 1881, in clear text like a person's, so the
HMI VLAN's capture shows it at each start and renewal, and a kiosk that cannot
sign in shows as a `401` every 2 minutes.

**Why a pipe.** `--remote-debugging-port` would let any local process and user
drive the browser: read the token, run script in the FUXA page as the operator,
open `file://` pages. A pipe has no address: only the two processes that hold its
descriptors can use it (`/proc/<pid>/fd/3` and `4` are readable by root only), and
both run as root, who can read the credentials file anyway. Nothing listens on the
network or on loopback (`ss -ltunp` shows no Chromium socket). The packaged
`/usr/bin/chromium` (xtradeb 156) keeps descriptors 3 and 4 through its `exec`.
Not used: an extension with a content script (with the image's Chromium and
`--incognito`, its content script did not run, and without `--incognito` it did;
"allow in incognito" is a profile setting with no flag, and dropping `--incognito`
changes what the kiosk keeps on disk); FUXA's refresh-cookie mode (`enableRefreshCookieAuth`
changes every client's sign-in, the cookie is `HttpOnly` with path `/api/refresh`
and lasts 7 days, so seeding it needs the browser's cookie store or the debugging
protocol anyway, and `POST /api/signout` clears it); typing into the dialog with
`xdotool` (blind: it depends on focus, layout, and timing, and every X client on
the display can watch the keystrokes); a proxy on loopback that adds the token
(the page would still think it is a guest, and any local request would get
operator rights).

**By hand, in a real browser** (no VM; this is what was run). You need a FUXA 1.3.6
checkout with `node_modules`, node, Xvfb, `xdotool`, and the image's Chromium: on
a noble machine, `apt-get install chromium` from the xtradeb source of
`phenix-images/cm-desktop.yml`, or `dpkg-deb -x` its `chromium` and
`chromium-common` packages into a directory and run `usr/lib/chromium/chromium`
with the five libraries a plain noble container lacked (`libopenh264-7`,
`libdouble-conversion3`, `libharfbuzz-subset0`, `libxnvctrl0`, `libminizip1t64`).

1. In an empty user directory, `_appdata/mysettings.json` with `secureEnabled: true`,
   a `secretCode`, `tokenExpiresIn` (`12h`, or `1m` to see the renewal), and `uiPort`;
   start `node main.js --userDir <dir> --port <port>` in the checkout's `server/`.
2. `FUXA_URL=http://127.0.0.1:<port> FUXA_ADMIN_PASSWORD=... FUXA_OPERATOR_PASSWORD=...
   FUXA_PROJECT=fuxa/full/scada-fuxa-project.json bash phenix-injects/fuxa/cm-fuxa-provision.sh`
   (its output ends `PASS: project imported`). Without PLCs the project's devices
   show errors and the batch engine refuses a Start, but the Batch view's buttons
   still write FUXA's own `BATCH.CMD`. The run below had stand-in PLCs (an OPC UA
   server for plc-brewhouse and a Modbus server for plc-cellar, on loopback, with
   those two devices' addresses changed in the project and the others disabled).
3. Write the two settings files as the app renders them (`KIOSK_URL=http://127.0.0.1:<port>/home?viewName=Batch`,
   `KIOSK_BROWSER=<chromium>`; `KIOSK_USER`, `KIOSK_PASSWORD`, 0600) and run
   `DISPLAY=:99 python3.12 cm-kiosk --env <file> --credentials <file> --state-dir <dir>`
   on `Xvfb :99 -screen 0 1440x900x24`, with `KIOSK_*` tuning in the environment.
4. Click Start with `DISPLAY=:99 xdotool mousemove 380 457 click 1` and read FUXA's
   log (`api-signin`, `Client connected with authenticated token`, `unauthorized
   write attempt from guest`), the account icon (blue when signed in), and the
   engine tags with `GET /api/getTagValue` as `admin`.

What it showed, with Chromium 156.0.8078.73 from the image's package, Python 3.12,
and FUXA 1.3.6 in secure mode:

| Run | Seen |
|---|---|
| Today's kiosk (the old `cm-kiosk` flags, no script), Start | `device-values: unauthorized write attempt from guest`, the sign-in dialog, `BATCH.CMD` stays 0 |
| `cm-kiosk`, 12 h tokens, Start | No dialog. FUXA: `api-signin: operator`, then `Client connected with authenticated token` (after one guest connection while the first page load ran). `BATCH.CMD` 1, then the engine: `Batch FOP-0001 running: MILL (1 of 11)`. No `unauthorized` line |
| `tokenExpiresIn` 1m, 2.5 minutes, then Start | A sign-in every 40 s, each followed by an authenticated connection. Start, 1.5 minutes after the first token expired, ran the batch |
| Same, FUXA stopped for 80 s (the token expired meanwhile) | Kiosk log: `operator`, `sign-in as operator failed: ConnectionRefusedError`, `guest`, `operator`. FUXA: `Token error: TokenExpiredError: jwt expired` at the page's reconnect, a sign-in 3 s later. Abort then aborted the batch |
| FUXA stopped, then the kiosk started | No browser, status `starting`; about 5 s after FUXA started, the browser was up and signed in |
| `KIOSK_WAIT=5`, FUXA stopped | The error page ("This site can't be reached"), `sign-in as operator failed: ConnectionRefusedError`; after FUXA started, the next try turned it into the signed-in view |
| Fresh FUXA (no account, no project), default wait | No browser until the import finished; the browser started 1 s after, signed in 0.2 s later |
| Fresh FUXA, `KIOSK_WAIT=5` | The guest view; `HTTP 401` at 0, 10, 30, and 70 s (FUXA's log); after provisioning, the try at 150 s signed in and reloaded |
| `SIGKILL` of the browser | Started again after 10 s, signed in |
| `SIGSEGV` of its renderer | "Aw, Snap!"; no answer for 60 s; browser killed, started again, signed in |
| A launcher that closes descriptors 3 and 4 | Chromium: `Remote debugging pipe file descriptors are not open.` and exits; the next browser ran without the pipe, the view as a guest (black account icon) |
| Control: the script signs in once and never again (`KIOSK_POLL=100000`), 1m tokens | At +5:00 FUXA's heartbeat: `POST /api/heartbeat 200`, `POST /api/signout 204`, a reload, two guest connections. FUXA's own renewal does not keep a screen nobody touches signed in |
| A project POSTed while a page shows the old one | The page kept the old project (the sidebar still had the renamed view) |
| `ss -ltunp`, `/proc/<pid>/fd` | No Chromium socket; `fd/3` and `fd/4` are pipes; no process has the password in its command line or environment |

Two things in that run are Chromium 156's: a `Page.addScriptToEvaluateOnNewDocument`
script is registered but never runs until `Page.enable` has been called on the
session (the script calls it first), and the top-level target list also holds
`browser_ui` targets (the script takes the one of type `page`).

Not seen yet, and for the first boot of an HMI VM to show: that the XDG autostart
entry starts `cm-kiosk` when the XFCE session can run Chromium (`DISPLAY` set,
`/run` writable) and that `cm-kiosk` is then running as root with the packaged
`python3` (3.12); that a VM's clock agrees with FUXA's to within the token's life
(the page and the script each judge a token by their own clock); that a real
screen at 1440x900 under xfwm4 shows the view full screen, and that the reload at
each renewal looks acceptable there (the harness had no window manager); that
SoH's `pgrep -f chromium.*--kiosk` finds the browser through miniccc, and the
`kiosk-operator-session` check passes once the account exists; and that the
kiosk's sign-ins and `401`s raise nothing in Wazuh, Suricata, or Zeek that the
blue team would take for an attack.

## Troubleshooting

| Message | Cause and fix |
|---|---|
| `metadata.<field>: unknown key, ignored -> did you mean '<key>'?` | A misspelled key. Fix it. |
| `metadata.type: not a known host type` | Use one of the types above. |
| `network.interfaces[IF1].address: differs from plant.yaml hosts.<host>.interfaces.IF1.ip` | The topology and plant.yaml disagree. Fix one, then rerun plantgen. |
| `<role> host is missing from the topology` | plant.yaml's variant lists a host the topology lacks. |
| `plant.yaml plc host is not in the app's hosts; the app will not provision it` | Add the host with `type: openplc` (a warning). |
| `metadata.northbound: differs from plant.yaml hosts.<host>.northbound` | Leave `northbound` out to use plant.yaml's. |
| `phenix-injects/openplc/<program>.zip is a Git LFS pointer` | Run `git lfs pull` in the topology directory. |
| `no debug map for <program>` | OPC UA needs the compiled program's `debug_map.json`, in the ZIP or at `plc/<program>/debug_map.json`, or set `opcua.debugMap`. |
| warning `the <program> ZIP was built with other plugin configs than plant/generated/<variant>` | The ZIP was built for another variant (for example the ci ZIP in full). Rebuild it with `plc/<program>/build_zip.sh` for this variant. |
| `the debug map of <program> is not from the program in its ZIP` | Rebuild the ZIP and the debug map together. |
| `<n> host(s) could not be rendered:` and a line per host | pre-start renders every host before failing; fix each listed input. |
| status `unreachable` for a host | Its C2 client is not up yet; `running` checks again. |

## Tests

```shell
cd cherrymountain-brewery/apps
python -m pytest
```

The tests run every stage on `tests/fixtures/experiment-ci.json`, which
`tests/build_fixtures.py` records from `phenix-configs/topology-ci.yml`,
`scenario-ci.yml`, `phenix-ci.yml`, and `tests/fixtures/app-ci.yml` (a copy of
the scenario's own block; `test_fixtures.py` fails when the two differ). They read
the real plantgen output, program ZIP, and FUXA builder from this repository.
`test_chain.py` runs the real phenix user apps of each variant's scenario
(`ot-sim`, this app, `helics`, `protonuke`, `wireguard`) in scenario order, as
phenix does, through configure and two starts.
`fixtures/debug_map-plc-cellar.json` is the strucpp (`fcccfbdf`) debug map of
the committed plc-cellar program, recorded to test the OPC UA config.
`test_kiosk.py` runs `cm-kiosk` as a process against `fuxa_fake.py` (FUXA's
sign-in, with real-looking tokens) and `kiosk_fake_browser.py` (Chromium's
debugging pipe), with its waits cut to milliseconds: no browser. It proves the
script's logic (when it signs in and renews, what the browser is given, that the
password goes nowhere else, the retries and their growing waits, the restarts, the
fallback without the pipe); the snippet's JavaScript is run by node when node is
installed. That the real Chromium takes what the script hands it is the run by hand
in [HMI kiosk](#hmi-kiosk).

## Limitations

- **Not run on a VM.** Every image the app renders for exists
  (`phenix-images/`), but none of it has booted: the historian, Guacamole,
  and Wazuh render and pass their tests only. The HMI kiosk's script ran against
  a real FUXA and the image's Chromium on a virtual display, not on its VM
  ([HMI kiosk](#hmi-kiosk) lists what a first boot must still show). TODO(verify) notes
  in `render.py` mark guesses: FUXA's InfluxDB DAQ store keys, the Grafana query
  fields, the s7comm mapping types beyond `int_output`/`int_input`, and the
  Guacamole schema version.
- The KPIs need `GET /api/v1/kpis` from the sim-ctl event API (the weather
  federate serves it in full and lite); until it answers, the status says
  `available: false` and why. Its compliance figures (TTB barrels, fill, CIP,
  wastewater, and the genset's run and test hours that a `genset-exercise`
  event advances) go under `kpis.compliance`, with the test hours left under
  the 40 CFR 60.4211(f) cap.
- The Guacamole init SQL is injected 0644, not 0600 like the other rendered
  files that came from a secret: PostgreSQL's entrypoint runs it as the
  container's `postgres` user. It holds a salted hash, no password.
- A federate that the topology still starts with its own unit (the ci slice's
  `cm-cellar.service`) is left to that unit; the status check accepts either.
- The HELICS federate-count SoH check counts distinct peers connected to the
  broker port, an approximation of the federate count.
