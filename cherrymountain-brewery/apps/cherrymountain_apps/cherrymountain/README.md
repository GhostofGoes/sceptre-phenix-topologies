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
counts both ([DESIGN.md 15.2](../../../DESIGN.md#152-apps)). Run `running`
periodically (`runPeriodically: 5m`) to keep the KPIs current.

## Stages

| Stage | What it does |
|---|---|
| configure | Validates the metadata (strict Pydantic models), loads `plant.yaml` for `variant` through plantgen, and checks it against the topology: every plant host of the variant exists, and each of its interfaces has the plant.yaml name, address, prefix length, and VLAN. Annotates each sim node that runs federates with `helics/federate: [{broker: sim-ctl\|mgmt, fed-count: N}]`. Writes each host's `sohProfile` (processes, listeners, custom tests) into this app's host metadata, where phenix's soh app reads it |
| pre-start | Validates again, then renders the files below into `<experiment dir>/cherrymountain/<host>/` and injects them. An injection replaces any other injection to the same destination, so a rendered file wins over a static one in the topology. `injections.json` lists what went where |
| post-start, running | Runs `systemctl is-active` for each host's provisioning units through cc (short timeouts; never blocks on a VM that has not booted) and reads the plant KPIs from the sim-ctl event API. Writes both to `status.apps.cherrymountain` |
| cleanup | Runs the export scripts on the FUXA hosts and the historian, and copies `fuxa-export.tar.gz` (alarm history from the API, plus FUXA's `_db` with the alarm and SQLite DAQ stores) and `influx-backup.tar.gz` to `<files>/cherrymountain/<host>-<file>`. A failed export is logged and recorded, and does not fail the stage |

## Inputs it reads

Everything comes from `sourceDir`, the cherrymountain-brewery directory inside
the phenix container (`phenix workflow apply` reads it from there):

| Input | Used for |
|---|---|
| `plant/plant.yaml` (through `tools/plantgen`) | Hosts, interfaces, roles, federates, northbound protocols |
| `plant/generated/<variant>/` | `helics.json`, `openplc/<plc>/modbus_*.json`, `tags.json`, `registers.json` |
| `phenix-injects/openplc/<program>.zip` | The program ZIP `plc/<program>/build_zip.sh` builds |
| `fuxa/build_project.py` | The scada FUXA project, built for the variant |
| `phenix-injects/openplc/`, `phenix-injects/fuxa/` | The provisioning scripts and units, injected as they are |

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
| `mgmt.enabled` | bool | `true` | Inject the MGMT nftables policy |
| `mgmt.interface` | interface | `mgmt` | The MGMT NIC name |
| `mgmt.brokerPorts` | ports | `[23404, 23405, 23406]` | HELICS broker ports on sim-ctl |
| `mgmt.federatePortMin`, `mgmt.federatePortMax` | ports | `23500`, `23999` | HELICS federate core ports |
| `mgmt.opensearchPort` | port | `9200` | Ground-truth OpenSearch on sim-ctl |
| `mgmt.exclude` | hostnames | `[]` | MGMT members that keep no policy |
| `historian.org` | name | `cherrymountain` | InfluxDB organization |
| `historian.buckets` | names | `[scada, bas, oee]` | InfluxDB buckets |
| `historian.retentionHours` | integer | `720` (30 days) | Bucket retention; `0` keeps data forever |
| `historian.influxPort`, `grafanaPort`, `mqttPort` | ports | `8086`, `3000`, `1883` | Historian services |
| `historian.mqttTopicRoot` | topic | `cherrymountain/frontrange` | PackTags topic root |
| `wazuh.manager` | hostname | `wazuh` | Wazuh manager; no `wazuh` block means no Wazuh files |
| `wazuh.interface`, `wazuh.enrollmentPort` | interface, port | `IF0`, `1515` | Where agents enroll |
| `wazuh.agents` | hostnames | every Linux host of the app except PLCs | Hosts that get an agent config |
| `secrets.*` | string | generated | `openplcPassword`, `fuxaPassword`, `fuxaJwtSecret`, `influxPassword`, `influxToken`, `grafanaPassword`, `guacamolePassword`, `wazuhEnrollment` |
| `verify.timeout`, `verify.clientGrace` | seconds | `15`, `5` | Per-check timeouts in post-start and running |
| `exportOnCleanup` | bool | `true` | Run the cleanup exports |

Host metadata, selected by `type`:

| `type` | Fields (defaults) | Renders |
|---|---|---|
| `openplc` | `program` (the hostname), `northbound` (plant.yaml's: `modbus_slave`, `opcua`, `s7comm`), `opcua.port` (4840), `opcua.interface` (IF0), `opcua.debugMap`, `opcua.cycleTimeMs` (100), `s7comm.port` (102), `restPort` (8443), `compileTimeout` (900) | Program ZIP, `conf/modbus_master.json` and `modbus_slave.json` (plantgen), `conf/opcua.json` (from the compiled program's debug map) or `conf/s7comm.json`, `/etc/default/cm-openplc`, the provisioning script, unit, and boot hook |
| `fuxa` | `role` (`scada`), `project` (built), `expectDevices` (every project device), `daq` (`auto`: InfluxDB when the app has a historian, else SQLite), `bucket` (the role), `port` (1881) | Project JSON, `mysettings.json` (secure mode, JWT secret, DAQ store, no Node-RED), `/etc/default/cm-fuxa`, the provisioning script, unit, and hook, `cm-export-fuxa` |
| `rio` | | Nothing; checked only (ot-sim devices of any role) |
| `sim-ctl` | `federates` (plant.yaml's for this host) | As `sim-federate` when it runs any |
| `sim-federate` | `federates` (plant.yaml's for this host), `logLevel` (`INFO`) | `helics.json`, `/etc/cherrymountain/federates`, `/etc/default/cm-federate-<name>`, `cm-federate@.service`, `60-cm-federates.sh` |
| `historian` | `interface` (IF0) | `/etc/default/cm-historian`, Telegraf, Mosquitto, Grafana data sources and dashboard, systemd drop-ins, `cm-historian-provision` and its unit and hook, `cm-export-historian` |
| `hmi` | `fuxa` (`scada-fuxa`), `view` (`Overview`), `interface` (the FUXA host's, IF0) | `/etc/default/cm-kiosk`, `cm-kiosk`, XDG autostart entry |
| `guacamole` | `connections` (`ews-rdp`, `ews-ssh`), `recording` (true), `totp` (true) | Guacamole initdb SQL (salted hash only), `/etc/default/cm-guacamole` |
| `wazuh-manager` | | With the app's `wazuh` block: the enrollment password and boot hook |
| `bbmd` | | Nothing; the topology injects cm-bbmd.service and its settings from `phenix-injects/bbmd/`. Checked only (SoH: the `cm_bbmd.py` process) |

Every topology node with a `mgmt` NIC, listed in the app or not, gets
`/etc/cherrymountain/nftables-mgmt.nft` and `04-cm-mgmt-nftables.sh`. The
policy only matches the MGMT interface and allows ICMP echo, HELICS, OpenSearch,
and NTP to sim-ctl, and HELICS between federate cores; everything else on MGMT
is dropped ([DESIGN.md 10.1](../../../DESIGN.md#101-zones-and-conduits-purdue-model--isaiec-62443)).

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
host to the exported file, or to `failed: <error type>`.

## Credentials

Range-only. A value set under `secrets` wins; every other one is generated
once per experiment into `<experiment dir>/cherrymountain/secrets.json` (mode
0600) and reused on restarts, so the VMs and their already-provisioned
services agree. Files that hold one are written and injected mode 0600.
SoH test scripts never hold one: a check that needs one sources the root-only
settings file on the VM. Errors and warnings name the host and field, never
the value.

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
| `a bas FUXA needs metadata.project` | The builder only builds the scada project; commit the BAS project and point `project` at it. |
| `<n> host(s) could not be rendered:` and a line per host | pre-start renders every host before failing; fix each listed input. |
| status `unreachable` for a host | Its C2 client is not up yet; `running` checks again. |

## Tests

```shell
cd cherrymountain-brewery/apps
python -m pytest
```

The tests run every stage on `tests/fixtures/experiment-ci.json`, which
`tests/build_fixtures.py` records from `phenix-configs/topology-ci.yml`,
`scenario-ci.yml`, `phenix-ci.yml`, and `tests/fixtures/app-ci.yml`. They read
the real plantgen output, program ZIP, and FUXA builder from this repository.
`fixtures/debug_map-plc-cellar.json` is the strucpp (`fcccfbdf`) debug map of
the committed plc-cellar program, recorded to test the OPC UA config.

## Limitations

- **Stubs** (render and test only; no image exists yet to run them):
  historian (`cm-historian`), Guacamole (`cm-guacamole`), Wazuh (`cm-wazuh`
  and the agent images), and the HMI kiosk (`cm-desktop`). TODO(verify) notes in
  `render.py` mark guesses: FUXA's InfluxDB DAQ store keys, the Grafana query
  fields, the s7comm mapping types beyond `int_output`/`int_input`, and the
  Guacamole schema version.
- The KPIs need `GET /api/v1/kpis` from the sim-ctl event API, which
  `cherrymountain_sim` does not serve yet; until then the status says
  `available: false` and why.
- `cm-fuxa-provision.sh` still checks for the `plc-cellar` device after
  import; the app already writes `FUXA_EXPECT_DEVICES` for when it reads that.
- The relay-main telnet config (DESIGN.md 12.3) is not rendered.
- The HELICS federate-count SoH check counts distinct peers connected to the
  broker port, an approximation of the federate count.
