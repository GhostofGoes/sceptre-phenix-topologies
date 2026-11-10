# plantgen

plantgen reads the plant tag database, `plant/plant.yaml`
([DESIGN.md §12.6](../../DESIGN.md#126-plant-tag-database)), validates it with
Pydantic, assigns every address, and writes the derived files for each topology
variant under `plant/generated/<variant>/`. The outputs are committed, and
`--check` fails when they are stale.

```shell
cd tools/plantgen
python -m plantgen                    # every variant in plant.yaml (ci, full, lite)
python -m plantgen --variant full     # one variant
python -m plantgen --check            # fail if any committed variant is stale
python -m pytest                      # unit and consistency tests
```

`--out DIR` writes one variant somewhere else (it needs `--variant`).

## Variants

| Variant | Hosts | What it contains |
|---|---:|---|
| `ci` | 5 | The cellar fermentation cell (`CEL` units CLK and FV101..FV104), the cellar federate only. Its outputs must stay byte-identical unless a fix needs a change, because the PLC program ZIP, the FUXA project, and the integration test are built from them |
| `full` | 43 | Every area, federate, and field device; SoH, WireGuard, and SCORCH lists |
| `lite` | 37 | `full` without the SOC (malcolm, wazuh, soc-analyst) and vendor remote access (vendor-ws, dmz-vpn, dmz-jump); no WireGuard |

A variant lists its areas, an optional per-area unit allow-list (`units`), its
federates and hosts, and switches for the `soh`, `wireguard`, and `scorch`
outputs.

## Outputs

Paths are relative to `plant/generated/<variant>/`. Files marked *ci* exist in
every variant; the others only where the variant has such devices or switches
them on. JSON is indented with two spaces, YAML is block style (prek checks it),
and every file starts with or carries `generator` and `variant`.

| File | Kind | Consumer |
|---|---|---|
| `tags.json` | ci | Expanded tag database, alarm bits included |
| `registers.json`, `registers.csv` | ci | Every Modbus tag in all addressing conventions |
| `alarms.json` | ci | Rationalized alarms (PLC programs, FUXA alarm definitions) |
| `helics.json` | ci | Federation: broker, federates with pub/sub/endpoint lists, ot-sim I/O federates |
| `phenix/ot-sim.yaml` | ci | The scenario's `ot-sim` app block |
| `phenix/ntp.yaml` | ci | The scenario's `ntp` app block (app mode, every host listed) |
| `phenix/cherrymountain.yaml` | ci | The scenario's `cherrymountain` app block |
| `phenix/helics.yaml` | `helics.app_block: true` (full, lite) | The scenario's `helics` app block (root broker); ci writes its own |
| `phenix/soh.yaml` | `soh: true` | The scenario's `soh` app block |
| `phenix/wireguard.yaml` | `wireguard: true` | The scenario's `wireguard` app block |
| `openplc/<plc>/modbus_master.json` | ci | OpenPLC v4 `modbus_master` plugin config |
| `openplc/<plc>/modbus_slave.json` | Modbus northbound PLCs | OpenPLC v4 `modbus_slave` plugin config |
| `openplc/<plc>/located_vars.st` | ci | Located-variable declarations for the PLC program |
| `fuxa/<host>-tags.json` | ci | FUXA devices and tags, one file per FUXA host (scada-fuxa, bas-fuxa) |
| `bacnet/<host>.json` | BACnet controllers | Object table of one ot-sim BACnet module |
| `dnp3/<fep>.json` | FEPs | DNP3 point map the FEP outstation serves |
| `scorch/pcap.yaml` | `scorch: true` | SCORCH `pcap` component VM lists per run |

The `phenix/*.yaml` files are complete app entries (`name`, `metadata`,
`hosts`) that `sync.py` embeds verbatim into
`phenix-configs/scenario-<variant>.yml`.

### Addressing conventions

See [DESIGN.md §12.5](../../DESIGN.md#125-addressing-conventions-high-off-by-one-risk)
and the docstring of `plantgen/allocate.py`. In short:

- **ot-sim** numbers registers per space (coil 1, discrete 10001, input
  30001, holding 40001) in host device order, then point order. Analog points
  are `uint16`, or `int16` for points marked `signed`.
- **OpenPLC `modbus_master`** reads the RIO into `%IX100.0`, `%QX100.0`,
  `%IW100`, and `%QW100`, using the ot-sim literal numbers as PDU offsets. A
  holding register another host writes (`written_by`, for example the utility
  limit `rtu-der` sets on the PV inverter) is read with FC3 into the next
  `%IW` word, so the PLC never overwrites it.
- **OpenPLC `modbus_slave`** (v4.3.2): holding registers are `%QW`, then
  `%MW` at `qw_count`, then `%MD` and `%ML`. The `buffer_mapping` counts are
  tight, so the runtime clamps nothing.
- **FUXA Modbus** uses `memaddress` plus a 1-based `address`.
- **BACnet** objects are numbered from 1 per object type on each controller,
  in device order then point order. FUXA addresses them as
  `<object type number>-<instance>` with `memaddress` set to the remote device
  instance.

### `helics.json`

```text
{generator, variant,
 broker: {host, interface, address, core_type},
 end_time_s,
 clock?: {start, timezone, speedup},          # full and lite
 ground_truth?: {endpoint, index_base},       # when the variant has ground truth
 federates: {<name>: {
   host, period_s, endpoint,                  # endpoint is "<name>/updates"
   accepted_updates: ["<device>.<point>", ...],
   publications: [{key, type, units, tag?, unit?, device?, device_type?, zone?, point?, output?}],
   subscriptions: [{key, type, units}],
   input_defaults: {<key>: value},            # inputs whose publisher is not in the variant
   ambient_psia,
   loads?: [{name, bus, rated_kw, key, desc}],   # power only
   sim: {...}}},                              # cellar: compression, noise, seed, initial_state;
                                              # others: the variant's federate_sim entry
 io_federates: {<field host>: {federate_name, subscriptions, endpoint, sends: [{tag, endpoint}]}},
 federate_count}
```

Keys follow the phenix ot-sim app: `<federate>/<device>.<point>` for device
points, `<federate>/<output>` for other outputs. Loads
(`plant.yaml` `loads`) add `<federate>/load.<name>.kw` publications, matching
power subscriptions, and `power/bus.<bus>.energized` publications that each
load's federate subscribes to. `unit_outputs` add `<federate>/<unit>.<key>`
publications (unit id in lower case, `cellar/fv101.level_pct`), and `snapshot`
adds subscriptions to the snapshot federate (below). `federate_count` counts the sim federates plus
one I/O federate per HELICS-backed field host; the `cherrymountain` app uses it
for the `helics` app's broker.

### `phenix/ot-sim.yaml`

The app's own schema, plus three things:

- **Federate endpoints.** The app's default writes endpoint is
  `<metadata.helics.federate>/updates`. A device of another federate sets
  `endpoint: updates`, which the app resolves to `<its federate>/updates`.
- **FEP and DNP3 master.** `rtu-der` is `type: fep` with `upstream`
  (relay-main, pv-inverter, bess-pcs), `downstream: dnp3`, and
  `dnp3.interface`. `utility-derms` is `type: fd-client` with
  `connected_rtus: [rtu-der]`.
- **BACnet controllers (proposed shape).** The phenix ot-sim app and the ot-sim
  BACnet module do not exist upstream yet
  ([DESIGN.md §16](../../DESIGN.md#16-new-and-extended-software)). plantgen
  emits the shape this design proposes, so the app extension and the module
  can be written against it:

  ```yaml
  # host metadata
  - hostname: bas-vent
    metadata:
      type: fd-server
      bacnet:
        interface: IF0:47808      # NIC and port; the module binds the NIC
                                  # address with its prefix (see bacnet/<host>.json)
        instance: 3003            # BACnet device instance
        name: BAS-VENT-1          # device object name
        devices:
        - name: building/bas-vent # <federate>/<device>, as for modbus
          type: vent
          endpoint: updates
  # infrastructure point attributes
  infrastructures:
    brewery:
      vent:
        co2_1_ppm:
          type: analog-read
          bacnet:
            object-type: analog-input    # analog-input|-output|-value,
                                         # binary-input|-output|-value,
                                         # multi-state-value
            units: partsPerMillion       # BACnet engineering-units name
        occ_mode:
          type: analog-read-write
          bacnet:
            object-type: multi-state-value
            states: [occupied, unoccupied, standby]
            commandable: true            # a value that takes writes
        purge_fan_1:
          type: binary-read-write
          bacnet:
            object-type: binary-output
            commandable: true            # priority array (every output)
  ```

  Rules for the module: object instances are numbered from 1 per object type
  in device order, then point order; each object maps to the message-bus tag
  `<device>.<point>`, and writes go to the device's HELICS endpoint, exactly as
  for Modbus. `commandable` is set on every read-write point: an output, or a
  value object that takes writes (a setpoint, a mode). The phenix `ot-sim` app
  does not know `bacnet`, so it renders these hosts without an I/O module; the
  `cherrymountain` app adds the I/O module and the BACnet module
  (`phenix-injects/otsim/`) to their config from `helics.json` and
  `bacnet/<host>.json`.

### `bacnet/<host>.json`

The resolved object table of one controller, which is what the module config
(the [DESIGN.md §16](../../DESIGN.md#16-new-and-extended-software) XML sketch)
carries:

```text
{generator, variant, host,
 endpoint: {interface, address, prefix, port},
 device: {instance, name},
 objects: [{object_type, instance, name, description, tag, helics_key,
            commandable, units?, states?, range?}]}
```

`name` is the plant tag (`FAC.VENT.AT01.PV`), `tag` is the ot-sim message-bus
tag (`bas-vent.co2_1_ppm`), and `helics_key` the federate key.

### `dnp3/<fep>.json`

```text
{generator, variant, host, endpoint: {interface, address, port},
 outstation_address: 1024, master_address: 1,
 points: [{kind: ai|ao|bi|bo, index, tag, plant_tag, upstream, scaling}]}
```

The numbering replicates the phenix app: upstream devices in order, then device
and point order, from 0 per kind. ot-sim scales analog points by
`10^-scaling` on the Modbus side, so DNP3 analogs carry engineering values.

### `fuxa/<host>-tags.json`

```text
{generator, variant, notes, devices: [
  {name, type: ModbusTCP, property: {address, port, slaveid}, polling,
   tags: [{id, name, label, type, memaddress, address, access, divisor?, units?, range?}]},
  {name, type: OPCUA, property: {address: "opc.tcp://<ip>:4840/openplc/opcua"}, polling,
   tags: [{id, name, label, type, address: null, variable, plc_iec, access, divisor?, ...}]},
  {name, type: SiemensS7, property: {address, port: 102, rack: 0, slot: 1}, polling,
   tags: [... same as OPCUA ...]},
  {name: "<host>-bacnet", type: BACnet, property: {address: <own IP>, broadcastAddress}, polling,
   tags: [{id, name, label, type: Real|Bool, memaddress: <device instance>,
           address: "<type>-<instance>", access, object_type, remote_device, units?, range?}]}]}
```

- Tag IDs are `t_<uuid5(tag name)>`, stable across regenerations.
- Modbus analogs are raw registers: engineering value = raw / `divisor`.
  `Int16` marks signed registers.
- OPC UA and S7 tags leave `address` null. `fuxa/build_project.py` fills it in:
  the OPC UA node id is `ns=2;s=<tag name>` (the node id the `cherrymountain`
  app gives the variable in the `opcua` plugin's config,
  [DESIGN.md §12.1](../../DESIGN.md#121-openplc-v4-runtime-v3-fallback)), and
  the S7 address comes from `plc_iec`. FUXA applies `divisor` to Modbus tags
  only, so the builder gives OPC UA and S7 tags the same factor as a linear
  `scale`.
- `access` is `read-write` for a PLC-memory setpoint or command (not for a RIO
  output, which the program drives every scan). A BACnet tag is `read-write`
  only on the BAS front end (`app.role: bas`), and only for an analog or
  binary object, the types FUXA's driver can write; scada-fuxa never writes
  BACnet. The builder gives every `read-write` tag an input box.
- A FUXA host polls the devices in its `fuxa` list in plant.yaml (default: all
  PLCs), filtered by tag patterns. scada-fuxa reads only bas-vent's CO₂
  objects.
- BACnet tags carry engineering values (BACnet REAL), so they have no divisor.

### `alarms.json`

One entry per alarm: `name` (the alarm bit tag `<AREA>.<UNIT>.<INSTR>.ALM_<KIND>`),
`source`, `kind` (`deviation`, `high`, `low`, `highhigh`, `lowlow`, `state`),
`limit`, `units`, `setpoint`, `delay_s`, `deadband`, `off_delay_s`, `priority`, `fuxa_severity`, `cause`,
`consequence`, `action`, `response_time_s`, `plc`, `plc_iec`, `fuxa_tag_id`,
`text`, and when set `suppress` (a bit that suppresses the alarm while it is 0,
for ISA-18.2 state-based suppression). `deadband` is the hysteresis in
engineering units (plant.yaml `deadband`, default 1 % of the tag's range span
and at most a quarter of a deviation limit; 0 for `state`), and `off_delay_s`
holds the alarm after its condition clears (plant.yaml `off_delay`, default 0).
`plc/common/plcbuild.py` generates the PLC alarm logic from these fields. A `state` alarm is active while the
source equals `limit`. Alarms on BACnet points have no PLC bit: `plc` and
`plc_iec` are null, `evaluator` is `fuxa`, `owner` is the controller, and
`fuxa_tag_id` is the source tag's ID.

### `phenix/cherrymountain.yaml`

The `cherrymountain` app entry: `runPeriodically: 5m` makes phenix run the
app's `running` stage every five minutes, which keeps the provisioning states
and plant KPIs in `status.apps.cherrymountain` current (`APP_RUN_PERIOD` in
`plantgen/apps.py`; `phenix ui` schedules it for an experiment it starts,
which `phenix workflow apply` is). `metadata` is `{variant: <name>, ...}` plus
the variant's `app` block in plant.yaml (ci: `verify.timeout`; full:
`wazuh.manager`), and `hosts` lists every host the app provisions or checks
with its `metadata.type`, grouped in the order sim-ctl, sim-federate, rio,
openplc, fuxa, historian, hmi, guacamole, wazuh-manager, bbmd. A host's
metadata is `hosts.<name>.app` in plant.yaml when set (bas-fuxa, the HMIs,
dmz-jump, wazuh, the BBMD hosts), else derived from its role: `sim` →
`sim-federate` (the HELICS broker host is `sim-ctl`), `rio`/`field`/`bas`/
`fep`/`dnp3-master` → `rio`, `plc` → `openplc`, `scada` → `fuxa`, `historian`
→ `historian`. The shapes are the app's metadata models
(`apps/cherrymountain_apps/cherrymountain/metadata.py`); the tests compare the
block with the app's `app-<variant>.yml` fixtures.

### `phenix/soh.yaml`

`startupDelay`, `c2Timeout`, `testReachability: "off"`, `skipHosts` (routers
and firewalls), `testCustomReachability`, `hostProcesses`, `hostListeners`, and
`dockerContainers`. Reachability entries are derived from the plant (each PLC
to its field devices, each FUXA to what it polls on its northbound port or with a
BACnet ReadProperty UDP probe, the FEP to its upstream devices, the DNP3 master
to the FEP) plus the conduits listed under `soh.conduits`. Flows with a host
outside the variant are dropped. Processes, listeners, and containers come
from per-role profiles and per-host additions in plant.yaml.

### `phenix/wireguard.yaml`

The phenix `wireguard` app's host metadata for dmz-vpn and vendor-ws
(`boot: true`, `interface`, `peers`). The keys are derived from
`wireguard.seed` in plant.yaml (`plantgen/wgkeys.py`, RFC 7748 X25519), so
`--check` can regenerate them. They are **public, range-only throwaway keys**:
anyone can recompute them, and they must not be used outside the range.
vendor-ws routes only dmz-jump (10.10.20.11/32) into the tunnel.

### `scorch/pcap.yaml`

```yaml
runs:
  <run name>:
    type: pcap
    metadata:
      vms:
      - hostname: plc-power
        interface: IF1
```

Each run's list is the SCORCH `pcap` component metadata
([DESIGN.md §15.6](../../DESIGN.md#156-scorch-pipelines)), filtered to the
variant's hosts.

## plant.yaml schema notes

The file's header comments describe tags, types, and scaling. Beyond the ci
slice it has:

- `networks`: VLAN subnets and gateways. The NTP check uses them: a client can
  use a source on another VLAN only when both networks are routed.
- `loads`: electrical loads and buses for the power federate.
- Units may override the area's field host (`rio`) and federate, for the
  chiller's OEM controller, each power device, and each BAS controller.
- A PLC-memory tag may name `peer: <tag>`, a bit another PLC computes (a
  `binary-read` coil in its `modbus_slave`). plantgen places it at `%IX120.0`
  up and adds that PLC as a device of the reading PLC's `modbus_master.json`
  (FC1 over CTRL, `error_handling: set-to-zero`, so a lost peer reads 0); the
  tag's `source` in `tags.json` is `peer`. plc-utilities reads plc-cellar's
  transfer and CIP-permit bits this way (DESIGN.md 6.6 #4).
- Device-type points may be `signed` (int16), `written_by` another host (read
  with FC3), or `bacnet` objects. A device type is all BACnet or all Modbus.
- Host roles: `sim`, `rio`, `field` (other Modbus field devices), `bas`, `fep`,
  `dnp3-master`, `plc` (with `northbound` `modbus`, `opcua`, or `s7comm`),
  `scada` (FUXA, with a `fuxa` source list), and infrastructure roles used for
  NTP and SoH.
- Alarms may name `suppress`, a bit in the same unit (`XS01.ACTIVE`), area
  (`LINE.XS01.RUN`), or by full name.
- Federate inputs and outputs may be limited to some `variants`.
- `unit_outputs` on a federate publish one value per unit whose `model` is
  listed, for truth no tag carries (a fermenter's fill, its relief-valve vent;
  DESIGN.md 9.5). They follow the variant's unit list.
- `snapshot` (`federate`, `keys`): the federate subscribes to every other
  federate's publication whose key matches a `keys` pattern (`fnmatch`). The
  weather federate serves those values as `GET /api/v1/state` for the white
  cell's 3D view, and the KPI keys feed `GET /api/v1/kpis` (DESIGN.md 15.10).
  plantgen fails on a `unit_outputs` model no unit uses, on a federate that
  would publish one key twice (a truth output shadowing a device point), and on
  a snapshot pattern that matches no publication.
- The ntp block may give defaults per host role (`roles`).

## Checks

`plantgen` runs these on every generation, and the tests run them on every
variant ([DESIGN.md §17](../../DESIGN.md#17-testing-and-validation)):

- Unique tag names; every tag has an owner (a PLC, or the BACnet controller);
  every field point is bound to exactly one tag.
- Analog ranges fit `uint16` (or `int16` for signed points) after scaling.
- Every alarm is rationalized (priority, cause, consequence, action, response
  time), has a limit inside its range, at most one alarm per kind per tag,
  and an existing suppression bit of the same owner.
- No register gaps in any block; ot-sim, modbus_master, modbus_slave, and
  FUXA addresses agree, each re-derived from the rendered files; BACnet
  instances agree between the app block, the FUXA tags, and the object table.
- Interface addresses are inside their VLAN, and unique; NTP sources are
  servers the client can reach; HELICS lists are closed (every subscription
  has a publisher, every field write is accepted by its federate).
