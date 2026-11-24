# plantgen

plantgen reads the plant tag database, `plant/plant.yaml`
([DESIGN.md §12.6](../../docs/DESIGN.md#126-plant-tag-database)). A tag is a
named value of the plant. The tool uses Pydantic to make sure that the database
is valid. Then it assigns every address and writes the derived files for each
topology variant under `plant/generated/<variant>/`. A variant is one version
of the topology. It also writes the files of each site of the fleet under
`plant/generated/fleet/<code>/`, and the phenix topology file of each site
(see [Sites](#sites)).

The outputs are committed files, and `--check` fails when they are stale. An
output is stale when a new generation gives a different file.

```shell
cd tools/plantgen
python -m plantgen                    # every variant (ci, full, lite) and every site
python -m plantgen --variant full     # one variant, and no site
python -m plantgen --site fc          # one site, and no variant
python -m plantgen --check            # fail if a committed variant or site is stale
python -m pytest                      # unit and consistency tests
```

`--out DIR` writes one variant or one site to a different directory. This
option needs `--variant` or `--site`, and not both. With `--site`, the
topology file of the site goes to `DIR`, too.

## Variants

| Variant | Hosts | What it contains |
|---|---:|---|
| `ci` | 5 | The cellar fermentation cell (`CEL` units CLK and FV101..FV104), with the cellar federate only. A federate is one simulator that connects to HELICS. Its outputs must stay byte-identical unless a fix needs a change. The PLC program ZIP, the FUXA project, and the integration test come from these outputs |
| `full` | 44 | Every area, federate, and field device, with the SoH (state of health), WireGuard, and SCORCH lists |
| `lite` | 37 | `full` without the SOC, the security operations center (malcolm, wazuh, soc-analyst, soc-purple), and without vendor remote access (vendor-ws, dmz-vpn, dmz-jump). It has no WireGuard |

A variant lists its areas and, as an option, the allowed units of each area
(`units`). It also lists its federates and hosts, and it has switches for the
`soh`, `wireguard`, and `scorch` outputs. `slice: true` marks a variant that
keeps only a part of the plant, as `ci` does.

## Sites

`plant/sites.yaml` describes the four breweries of the fleet
([PLANS.md §4.1](../../docs/multisite/PLANS.md#41-what-each-site-is)). A site
is the plant of `plant.yaml` with parts left out and with its networks moved.
For each site, the file gives the areas and the hosts that the site does not
have. It also gives where the hosts of each VLAN go, the uplinks, and the time
source.

plantgen reads the file on each run (`plantgen/sites.py`) and stops when an
entry does not fit.

| Check | What it refuses |
|---|---|
| Names | An area, a host, a VLAN, or a PLC that `plant.yaml` does not have. A time source or a gateway that the site left out. A site code that is the name of a variant |
| Addresses | Two hosts with one address after two VLANs merge. The message tells you to give a move a `shift` |
| `wan/wan.yaml` | Uplinks that are not the links of the site's edge there, in the same order. A routed network outside the blocks of the site. A different subnet for the LAN that `wan.yaml` puts a host on |

A test makes sure that the file equals the profiles that the
[site-profiles spike](../../spikes/site-profiles/README.md) ran.

### The files of a site

plantgen writes the files of each site under `plant/generated/fleet/<code>/`.
A site has the files that a variant has ([Outputs](#outputs)). In these
files, `variant` holds the code of the site.

| Site | Hosts | PLCs | Tags | Files | Copies of injected files |
|---|---:|---:|---:|---:|---:|
| `fr` | 39 | 5 | 516 | 29 | 8 |
| `ep` | 34 | 5 | 516 | 29 | 8 |
| `cs` | 25 | 4 | 458 | 25 | 6 |
| `fc` | 18 | 3 | 365 | 22 | 1 |

The copies are under `injects/` in the directory of the site
([The injected files of a site](#the-injected-files-of-a-site)).

`plantgen/fleet.py` builds a plant description of the site from `plant.yaml`
and the entry of the site. The description has one variant, and the name of
that variant is the code of the site. Then the steps of a variant run on the
description: plantgen assigns the addresses, runs the checks, and writes the
files.

| Step | What `fleet.py` does |
|---|---|
| Variant | It starts from the `full` variant. It keeps the hosts and the areas of the site, and the federates of those areas |
| References | It removes each reference to a host that the site does not have. Examples are a FUXA source, a conduit, and a Wazuh agent |
| FUXA sources | A site can leave out a FUXA host and keep the devices of that host. The one SCADA host of the site then takes those devices |
| Clocks | The clocks follow the `time_server` of the site. The edge firewall and the office PC keep `inet-traffic` as their source, with its address in the fleet. The NTP block also lists the sensor, because the app needs every node |
| Floor | The office and each network with a PLC stay different networks. The gateway of the office network is a firewall. A site description that breaks this rule stops the run |
| Snapshot | The snapshot keeps the key patterns of the federates that the site runs |
| Zones | Each interface goes to the VLAN and the subnet that `moves` and `cells` give. A router gets a leg in each new network that it routes. A network with no host goes away |
| Names | A hostname gets the prefix of the site, and a VLAN alias gets the prefix in capitals. The `10.` blocks of the company and MGMT move by the index of the site |
| Uplinks | The edge gets one network for each uplink, with its address from `sites.yaml`. The alias and the subnet are those of `wan/wan.yaml`, for example `WAN-SC-FC` |

- The steps change fields, not text. A name that is also a kind keeps its
  meaning. Examples are the role `ews`, the device type `pv-inverter`, and the
  HELICS device `bas-vent`.
- Federate names, HELICS keys, and tag names stay the same at every site. Each
  site has its own federation.
- A description (`desc`, the name of a unit, the cause of an alarm) is prose.
  This is the one place where plantgen replaces hostnames in text.
- `inet-traffic` is a host of the internet, not of a site. The variant of a
  site names it under `ntp.external`, and the NTP check then accepts it as a
  source.
- A source outside the site carries the address of its server. The ntp app of
  phenix looks a source up by its hostname. When the topology has no such
  node, the app gives the client the default source and prints nothing. The
  code is `src/go/app/ntp.go`, lines 41 to 44 and 138 to 145. The office PC
  then asks a plant host, and the firewall drops the request. With an
  address, the app uses the address as it is.
- The address is `198.18.110.11`, the host of the internet in `wan/wan.yaml`.
  It is not the `203.0.113.10` of `plant.yaml`. In the fleet, that address is
  the uplink of `fc`. At a site alone, no host answers at the new address.
- `fr` is the `full` variant with the names of the site. Only the entries of
  the hosts of the internet are absent. A test compares the two.
- `fc` has no `bas-fuxa`, and it keeps the building controllers. Thus
  `fc-scada-fuxa` reads all of their points and writes them. The source has
  `write: true`. plantgen applies this rule from the hosts of the site, not
  from its code.
- `fuxa/build_project.py` builds the FUXA projects of each site from these
  files ([FUXA README](../../fuxa/README.md#the-projects-of-the-sites)).

A site does not have these parts yet. Later steps of the plan add them:

- The WireGuard block and the SCORCH capture lists. One end of the tunnel is
  `vendor-ws`, which is a host of the internet.
- The entries of the hosts of the internet in the app blocks. Examples are
  the DNP3 client `utility-derms` and each SoH flow that crosses the internet.
- The flows between sites. An example is the Wazuh agents of `ep`, which
  report to `fr-wazuh`.
- The values that differ between sites in the simulation. Examples are the
  elevation, the weather file, and the tariff.

### The topology file of a site

plantgen writes `phenix-configs/topology-site-<code>.yml` for each site
(`plantgen/topology.py`). The file has one node for each host of the site. A
site with `sensor: true` has one more node, the network sensor.

| Site | Nodes | Sensor |
|---|---:|---|
| `fr` | 39 | None. The SOC is at this site |
| `ep` | 35 | In the SOC zone, behind both firewalls |
| `cs` | 26 | In the plant network |
| `fc` | 19 | In the plant network |

The source of a node is the node of the same host in `topology-core.yml`,
`topology-soc.yml`, or `topology-remote-access.yml`. The fields that name a
host or an address follow the plant description of the site.

| Field of a node | What it becomes |
|---|---|
| `general.hostname`, `delay.c2` | The name of the host at the site. A node does not wait for a host that the site does not have |
| `general.description`, the `description` of an injection | The same text, with the prefix of the site on each hostname |
| `network.interfaces` | The interfaces of the plant description. A router names them `eth0`, `eth1`, and so on. The gateway is the address of the router of the VLAN |
| `dns` of an interface | The address of `ot-svc` at the site. At `fc`, which has no `ot-svc`, it is the gateway. The office PC gets none, because its name server is a host of the internet |
| `network.routes` | The edge firewall and the DMZ hosts get a route to each network behind the OT firewall. The OT firewall gets a default route through the edge. At `fr`, the jump host gets a route to the WireGuard network |
| `labels` | `zone` is the first network of the host, and a router keeps its label. `site` is the code of the site |
| `phenix/startup-autotunnel` | The port of the head node moves by 100 for each step of the index of the site. `fr` keeps 11881, and `ep` gets 12081 |
| `injections` | The injections that the site can have. The files of the vendor's access and of the SOC stay at the head office ([The injected files of a site](#the-injected-files-of-a-site)) |
| `src` of an injection | The copy of the site, when the file holds a name or an address of the plant. For each other file, no change |
| `hardware`, `type`, `commands`, the `dst` of an injection | No change |

- The uplinks of the edge are its first interfaces. They have the VLAN alias,
  the address, and the mask that `topology-wan.yml` gives the same router. A
  test compares the two files.
- The sensor is a stand-in. It has the image name `cm-sensor.qc2`, 4 vCPUs,
  16 GB, and host number 60, as in the site-profiles spike. No such image
  exists.
- `fr` gets the interfaces, the labels, and the routes that the three modules
  have today. A test compares them, apart from the WAN side of the edge.
- Across the four files, no hostname and no address occurs twice. Each VLAN
  alias has one subnet.

A topology file is not a finished site. The header of each file says so.

- The firewall rulesets are not in the topology file. They are in
  `phenix-configs/scenario-site-<code>.yml`, for the conduits inside the site.
  netcheck computes them, and `sync.py` writes the scenario
  ([netcheck README](../netcheck/README.md#a-site-of-the-fleet)).
- The edge has no default route and no route to a different site. The WAN
  generator writes the WAN side of the edge.

### The workflow file of a site

plantgen writes `phenix-site-<code>.yml` for each site, next to `phenix.yml`
(`plantgen/workflow.py`). `phenix workflow apply` takes this file. It names the
topology and the scenario of the site, and it gives an ID to each VLAN alias
of the topology file.

The IDs follow the plan of the fleet
([PLANS.md §3](../../docs/multisite/PLANS.md#3-what-every-plan-shares)). A site then
keeps its IDs when the four sites become one experiment.

| VLAN | Its ID | Example |
|---|---|---|
| A zone that `phenix.yml` has | The hundred of the site, and the last two digits from `phenix.yml` | `OPS` is 106 there, and `EP-OPS` is 306 |
| A zone that `phenix.yml` does not have | The hundred of the site, and the number of the zone in the table `OWN_ZONES` (20 and up) | `EP-CTRL-BH` is 320, and `FC-PLANT` is 221 |
| An access link to an ISP | 500 and up, in the order of the links in `wan/wan.yaml` | `WAN-SC-FR` is 500 |

- The hundred of the first site is 100. Thus `fr` keeps the IDs of
  `phenix.yml`.
- The WAN generator pins the access links and one zone of each site too
  (`tools/wangen`). A test compares each such ID with `phenix-wan.yml`.
- Each site has its own bridge, for example `cherrymtn-fc`. phenix lets only
  one stored experiment use a bridge that is not `phenix`.
- plantgen stops when two aliases get one ID, or when an alias is not a zone
  of the site and not an access link.
- The number of a zone in `OWN_ZONES` belongs to the name of the zone. Thus
  no ID changes when a site gets a zone or loses one. A new zone needs a new
  entry in the table.
- The plan of the IDs holds four sites. The index of a site is 0 to 3,
  because the WAN takes the IDs from 500 up.
- plantgen compares the IDs of each site with `phenix-wan.yml`. It stops when
  an ID has two aliases, or when an alias has two IDs.

### The injected files of a site

A node of a site injects the files that the node of the single site injects.
Most of these files are the same at each site. Some hold an address or a
hostname of the plant, and a site needs its own copy of them.

Some files belong to a part that only the head office has. `SCOPES` in
`plantgen/injects.py` holds a decision for each injected file, with its
reason. plantgen stops at an injection that has no decision.

| Files | Which site injects them | Why |
|---|---|---|
| `guacamole/ridgeline.env`, `guacamole/cm_vendor_sql.py`, `ews/70-ews-vendor.sh` | A site with the `remote` module: `fr` | They hold and create the accounts of the technicians of the vendor. The vendor reaches the head office and no other site |
| `wireguard/` | A site with the `remote` module | The tunnel of the vendor ends at the head office |
| `malcolm/`, `wazuh/`, the console of the analyst | A site with the `soc` module: `fr` | The fleet has one SOC |
| `inet-traffic/` | No site | They are the files of a host of the internet |
| All other files | Each site whose node injects them today | They hold no account and no part of the head office |

`ep` has its own jump host. That host runs the same Guacamole and gets no
account of the vendor. The accounts of the company for that host are not
designed yet, and the header of `topology-site-ep.yml` says so.

`plantgen/injects.py` also renders the copies. A copy is the hand-written file
with a small number of lines set again from the plant description of the
site. The generator finds each line by its first characters. When the
hand-written file does not have the line exactly one time, the generator
stops and names the line.

| File under `phenix-injects/` | What the copy of a site sets |
|---|---|
| `vrouter/cm-vyos-config.sh` | The names of the routers of the site, and the address of the time server. The script drops the prefix of the site from the hostname. Then its steps for `fw-edge` and `fw-ot` apply. The copy does not have the two steps for the IPsec of the utility |
| `bbmd/ot-svc.env`, `bbmd/bas-bbmd.env` | The address of the BBMD, and the table with the addresses of both BBMDs |
| `ot-svc/dnsmasq-plant.conf` | The address that the name server listens on |
| `ot-svc/plant.hosts` | The whole file: each address of each node of the site |
| `ot-svc/60-ot-svc.sh` | The networks of the hosts that take their time from `ot-svc`, and the network of the sensor |
| `guacamole/cm-guacamole-start`, `guacamole/nginx.conf` | The name and the address of the jump host in its certificate, and the name that its web server answers to |

- plantgen writes a copy only when a node of the site injects the file. `fc`
  has one router and no `ot-svc`, so it has one copy.
- The copies go to `plant/generated/fleet/<code>/injects/`. `sync.py` copies
  them to `phenix-injects/fleet/<code>/`, and the topology file of the site
  names them there.
- `python -m plantgen --check` fails when a copy is out of date. A script
  keeps its permission to run.
- The syslog target stays `10.40.10.11`, because the fleet has one Wazuh, at
  `fr`.
- The two steps for the IPsec of the utility belong to the WAN side of the
  edge. No scenario of a site gives its edge that IPsec yet, and the step
  fails on a router that has no such IKE group. The steps come back for `fr`
  and `ep` when those sites get the tunnel to the utility.
- At `fr`, the files of the SOC and of the vendor's tunnel are not rendered
  yet. They still hold the names of the single site. A test in
  `tools/netcheck/tests/test_site_set.py` lists them.
- The program ZIP that `ews` gets (`openplc/full/plc-cellar.zip`) holds the
  addresses of the Front Range remote I/O. A site needs its own build of that
  ZIP.

## Outputs

Paths are relative to `plant/generated/<variant>/`, or to
`plant/generated/fleet/<code>/` for a site. Files that the Kind column marks as
"ci" exist in every variant. The other files exist only in a variant that has
such devices or that switches them on.

JSON files have an indent of two spaces. YAML files use block style, and prek
makes sure that they do. Every file starts with `generator` and `variant`, or
contains them.

| File | Kind | Consumer |
|---|---|---|
| `tags.json` | ci | The expanded tag database, which includes the alarm bits |
| `registers.json`, `registers.csv` | ci | Every Modbus tag in all addressing conventions |
| `alarms.json` | ci | The rationalized alarms (PLC programs, FUXA alarm definitions) |
| `helics.json` | ci | The federation: the broker, the federates with their publication, subscription, and endpoint lists, and the ot-sim I/O federates |
| `phenix/ot-sim.yaml` | ci | The scenario's `ot-sim` app block |
| `phenix/ntp.yaml` | ci | The scenario's `ntp` app block (app mode, with every host in the list) |
| `phenix/cherrymountain.yaml` | ci | The scenario's `cherrymountain` app block |
| `phenix/helics.yaml` | `helics.app_block: true` (full, lite) | The scenario's `helics` app block (root broker). The ci scenario writes its own block |
| `phenix/soh.yaml` | `soh: true` | The scenario's `soh` app block |
| `phenix/wireguard.yaml` | `wireguard: true` | The scenario's `wireguard` app block |
| `openplc/<plc>/modbus_master.json` | ci | The configuration of the OpenPLC v4 `modbus_master` plugin |
| `openplc/<plc>/modbus_slave.json` | PLCs whose northbound protocol (toward SCADA) is Modbus | The configuration of the OpenPLC v4 `modbus_slave` plugin |
| `openplc/<plc>/located_vars.st` | ci | The declarations of the located variables for the PLC program |
| `fuxa/<host>-tags.json` | ci | The FUXA devices and tags, with one file for each FUXA host (scada-fuxa, bas-fuxa) |
| `bacnet/<host>.json` | BACnet controllers | The object table of one ot-sim BACnet module |
| `dnp3/<fep>.json` | FEPs (front-end processors) | The DNP3 point map that the FEP outstation serves |
| `scorch/pcap.yaml` | `scorch: true` | The VM lists of the SCORCH `pcap` component for each run |

The `phenix/*.yaml` files are complete app entries (`name`, `metadata`,
`hosts`). `sync.py` copies them without changes into
`phenix-configs/scenario-<variant>.yml`.

### Addressing conventions

See [DESIGN.md §12.5](../../docs/DESIGN.md#125-addressing-conventions-high-off-by-one-risk)
and the docstring of `plantgen/allocate.py`. A summary follows:

- ot-sim numbers the registers for each space (coil 1, discrete 10001, input
  30001, and holding 40001). The order is the device order of the host, and
  then the point order. Analog points are `uint16`. Points that have the
  `signed` mark are `int16`.
- The OpenPLC `modbus_master` plugin reads the RIO (remote I/O) into
  `%IX100.0`, `%QX100.0`, `%IW100`, and `%QW100`. It uses the ot-sim literal
  numbers as PDU offsets. A different host can write a holding register
  (`written_by`). An example is the utility limit that `rtu-der` sets on the
  PV inverter. The plugin reads such a register with FC3 into the next `%IW`
  word, so the PLC never overwrites it.
- OpenPLC `modbus_slave` plugin (v4.3.2): the holding registers are `%QW`,
  then `%MW` at `qw_count`, then `%MD` and `%ML`. The `buffer_mapping` counts
  are tight, so the runtime clamps nothing.
- FUXA Modbus uses `memaddress` plus a 1-based `address`.
- BACnet: plantgen numbers the objects from 1 for each object type on each
  controller. The order is the device order, and then the point order. FUXA
  addresses them as `<object type number>-<instance>`. In these tags,
  `memaddress` is the remote device instance.

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

The keys follow the phenix ot-sim app. A device point has the key
`<federate>/<device>.<point>`, and each other output has the key
`<federate>/<output>`.

Loads (`plant.yaml` `loads`) add `<federate>/load.<name>.kw` publications and
the matching subscriptions of the power federate. Loads also add
`power/bus.<bus>.energized` publications, and the federate of each load
subscribes to them.

`unit_outputs` add `<federate>/<unit>.<key>` publications. The unit id is in
lower case, for example `cellar/fv101.level_pct`. `snapshot` adds
subscriptions to the snapshot federate. The schema notes below describe it.

`federate_count` counts the sim federates plus one I/O federate for each field
host that uses HELICS. The `cherrymountain` app uses this count for the broker
of the `helics` app.

### `phenix/ot-sim.yaml`

This file uses the schema of the app and adds three things:

- Federate endpoints: the default endpoint of the app for writes is
  `<metadata.helics.federate>/updates`. A device of a different federate sets
  `endpoint: updates`. The app resolves this value to
  `<its federate>/updates`.
- FEP and DNP3 master: `rtu-der` is `type: fep` with `upstream`
  (relay-main, pv-inverter, bess-pcs), `downstream: dnp3`, and
  `dnp3.interface`. `utility-derms` is `type: fd-client` with
  `connected_rtus: [rtu-der]`.
- BACnet controllers: the phenix `ot-sim` app does not know BACnet, and the
  upstream ot-sim project has no BACnet module
  ([DESIGN.md §16](../../docs/DESIGN.md#16-new-and-extended-software)). The
  module is in this repository (`phenix-injects/otsim/`). For this module, and
  for the app extension in the upstream proposal, plantgen emits this shape:

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

  These rules apply to the module. Object instances have numbers from 1 for
  each object type, in device order and then point order. Each object maps to
  the message-bus tag `<device>.<point>`. Writes go to the HELICS endpoint of
  the device, exactly as for Modbus. Every read-write point has `commandable`
  set. A read-write point is an output, or a value object that takes writes (a
  setpoint, a mode).

  The phenix `ot-sim` app does not know `bacnet`, so it renders these hosts
  without an I/O module. The `cherrymountain` app adds the I/O module and the
  BACnet module (`phenix-injects/otsim/`) to the configuration of these hosts.
  It takes the data from `helics.json` and `bacnet/<host>.json`.

### `bacnet/<host>.json`

This file is the resolved object table of one controller. The module
configuration (the XML sketch in
[DESIGN.md §16](../../docs/DESIGN.md#16-new-and-extended-software)) carries
this table:

```text
{generator, variant, host,
 endpoint: {interface, address, prefix, port},
 device: {instance, name},
 objects: [{object_type, instance, name, description, tag, helics_key,
            commandable, units?, states?, range?}]}
```

`name` is the plant tag (`FAC.VENT.AT01.PV`), `tag` is the ot-sim message-bus
tag (`bas-vent.co2_1_ppm`), and `helics_key` is the federate key.

### `dnp3/<fep>.json`

```text
{generator, variant, host, endpoint: {interface, address, port},
 outstation_address: 1024, master_address: 1,
 points: [{kind: ai|ao|bi|bo, index, tag, plant_tag, upstream, scaling}]}
```

The numbering is the same as in the phenix app. It uses the upstream devices
in order, then the device order, and then the point order. The numbers start
from 0 for each kind.

On the Modbus side, ot-sim scales analog points by `10^-scaling`. Thus DNP3
analogs carry engineering values (values in physical units).

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

- Tag IDs are `t_<uuid5(tag name)>`. They stay the same when you generate the
  files again.
- Modbus analogs are raw registers. The engineering value is the raw value
  divided by `divisor`. `Int16` marks signed registers.
- OPC UA and S7 tags leave `address` null. `fuxa/build_project.py`, the
  builder, fills it in. The OPC UA node id is `ns=2;s=<tag name>`. The
  `cherrymountain` app gives the variable this node id in the configuration of
  the `opcua` plugin
  ([DESIGN.md §12.1](../../docs/DESIGN.md#121-openplc-v4-runtime-v3-fallback)).
  The S7 address comes from `plc_iec`.

  FUXA applies `divisor` to Modbus tags only. Thus the builder gives OPC UA
  and S7 tags the same factor as a linear `scale`.
- `access` is `read-write` for a setpoint or command in PLC memory. A RIO
  output does not get this value, because the PLC program drives the output on
  every scan.

  A BACnet tag is `read-write` only on the front end of the building
  automation system (BAS, `app.role: bas`), and only for an analog or binary
  object. These are the object types that the FUXA driver can write. The host
  scada-fuxa never writes BACnet.

  The builder gives every `read-write` tag an input box.
- A FUXA host polls the devices in its `fuxa` list in plant.yaml (default: all
  PLCs), and tag patterns filter the tags. The host scada-fuxa reads only the
  CO₂ objects of bas-vent.
- BACnet tags carry engineering values (BACnet REAL), so they have no divisor.

### `alarms.json`

The file has one entry for each alarm. An entry has `name` (the alarm bit tag
`<AREA>.<UNIT>.<INSTR>.ALM_<KIND>`) and `source`. It has `kind`, which is one
of `deviation`, `high`, `low`, `highhigh`, `lowlow`, and `state`. It also has
`limit`, `units`, `setpoint`, `delay_s`, `deadband`, `off_delay_s`,
`priority`, `fuxa_severity`, `cause`, and `consequence`. Further fields are
`action`, `response_time_s`, `plc`, `plc_iec`, `fuxa_tag_id`, and `text`.

When it is set, an entry also has `suppress`. This bit suppresses the alarm
while the bit is 0, for the state-based suppression of ISA-18.2.

`deadband` is the hysteresis in engineering units (plant.yaml `deadband`). The
default is 1 % of the range span of the tag, and at most a quarter of a
deviation limit. For `state`, the value is 0. `off_delay_s` holds the alarm
after its condition clears (plant.yaml `off_delay`, default 0).

`plc/common/plcbuild.py` generates the PLC alarm logic from these fields. A
`state` alarm is active while the source equals `limit`.

Alarms on BACnet points have no PLC bit. For these alarms, `plc` and `plc_iec`
are null, `evaluator` is `fuxa`, and `owner` is the controller. `fuxa_tag_id`
is the ID of the source tag.

### `phenix/cherrymountain.yaml`

This file is the `cherrymountain` app entry.

`runPeriodically: 5m` makes phenix run the `running` stage of the app every
five minutes. This run keeps the provisioning states and the plant KPIs in
`status.apps.cherrymountain` current. The period is `APP_RUN_PERIOD` in
`plantgen/apps.py`. `phenix ui` schedules the run for each experiment that it
starts. An experiment from `phenix workflow apply` is such an experiment.

`metadata` is `{variant: <name>, ...}` plus the `app` block of the variant in
plant.yaml. For ci, this block has `verify.timeout`. For full, it has
`wazuh.manager` and `wazuh.agents`.

`hosts` lists every host that the app provisions or checks, with its
`metadata.type`. The list has groups in this order: sim-ctl, sim-federate,
rio, openplc, fuxa, historian, hmi, guacamole, wazuh-manager, bbmd, ews.

When `hosts.<name>.app` is set in plant.yaml, it is the metadata of the host
(bas-fuxa, the HMIs, dmz-jump, wazuh, the BBMD hosts). If it is not set, the
metadata comes from the role of the host: `sim` → `sim-federate` (the HELICS
broker host is `sim-ctl`), `rio`/`field`/`bas`/`fep`/`dnp3-master` → `rio`,
`plc` → `openplc`, `scada` → `fuxa`, `historian` → `historian`, `ews` → `ews`.

The shapes are the metadata models of the app
(`phenix_apps/apps/cherrymountain/metadata.py` in sceptre-phenix-apps). When
the phenix-apps package is installed, the tests compare the block with the
`app-<variant>.yml` fixtures of the app. These fixtures are part of the
phenix-apps package.

### `phenix/soh.yaml`

This file contains `startupDelay`, `c2Timeout`, `testReachability: "off"`,
`skipHosts` (routers and firewalls), `testCustomReachability`,
`hostProcesses`, `hostListeners`, and `dockerContainers`.

The reachability entries come from the plant, plus the conduits in the
`soh.conduits` list. A conduit is a permitted path between network zones. The
plant gives these flows:

- Each PLC to its field devices.
- Each FUXA to each device that it polls. The test uses the northbound port of
  the device or a BACnet ReadProperty UDP probe.
- The FEP to its upstream devices.
- The DNP3 master to the FEP.

The file does not contain flows that have a host outside the variant. The
processes, listeners, and containers come from plant.yaml: a profile for each
role, and additions for each host.

### `phenix/wireguard.yaml`

This file is the host metadata of the phenix `wireguard` app for dmz-vpn and
vendor-ws (`boot: true`, `interface`, `peers`). The keys come from
`wireguard.seed` in plant.yaml (`plantgen/wgkeys.py`, RFC 7748 X25519), so
`--check` can generate them again.

Do not use these keys outside the range. They are public throwaway keys for
the range only, and anyone can compute them again.

The host vendor-ws routes only dmz-jump (10.10.20.11/32) into the tunnel.

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

The list of each run is the metadata of the SCORCH `pcap` component
([DESIGN.md §15.6](../../docs/DESIGN.md#156-scorch-pipelines)). The list
contains only the hosts of the variant.

## plant.yaml schema notes

The header comments of the file describe tags, types, and scaling. Beyond the
ci slice, the file has:

- `networks`: the VLAN subnets and gateways. The NTP check uses them. A client
  can use a source on a different VLAN only when both networks are routed.
- `loads`: the electrical loads and buses for the power federate.
- A unit can override the field host (`rio`) and the federate of its area. The
  chiller's OEM controller, each power device, and each BAS controller use
  this override.
- A tag in PLC memory can name `peer: <tag>`. The peer tag is a bit that a
  different PLC computes (a `binary-read` coil in its `modbus_slave`). For
  such a tag, plantgen assigns an address from `%IX120.0` up. It also adds the
  other PLC as a device in the `modbus_master.json` of the PLC that reads the
  bit. The read uses FC1 over CTRL with `error_handling: set-to-zero`, so a
  lost peer reads 0.

  The `source` of the tag in `tags.json` is `peer`. The PLC plc-utilities
  reads the transfer and CIP-permit bits of plc-cellar this way
  (DESIGN.md 6.6 #4).
- A point of a device type can be `signed` (int16), `written_by` a different
  host (read with FC3), or a `bacnet` object. A device type is all BACnet or
  all Modbus.
- The host roles are `sim`, `rio`, `field` (other Modbus field devices),
  `bas`, `fep`, and `dnp3-master`. Further roles are `plc` (with `northbound`
  `modbus`, `opcua`, or `s7comm`) and `scada` (FUXA, with a `fuxa` source
  list). There are also infrastructure roles, which NTP and SoH use.
- A `fuxa` source can say `write: true`. That FUXA host can then write the
  BACnet objects of the device. The BAS front end (`app.role: bas`) writes all
  of its devices without this key.
- An alarm can name `suppress`. This is a bit in the same unit
  (`XS01.ACTIVE`), a bit in the same area (`LINE.XS01.RUN`), or a bit by its
  full name.
- A federate input or output can say `in_slice: false`. A variant with
  `slice: true` (`ci`) does not get such a key. Every other variant gets it,
  and so does every site.
- `unit_outputs` on a federate publish one value for each unit whose `model`
  is in the list. They are for truth values (the true state in the simulation)
  that no tag carries. Examples are the fill of a fermenter and its
  relief-valve vent (DESIGN.md 9.5). They follow the unit list of the variant.
- `snapshot` (`federate`, `keys`): the federate subscribes to each publication
  of the other federates when the key of the publication matches a `keys`
  pattern (`fnmatch`). The weather federate serves those values as
  `GET /api/v1/state` for the 3D view of the white cell (the exercise control
  team). The KPI keys feed `GET /api/v1/kpis` (DESIGN.md 15.10).

  A plantgen run fails in three cases. The first case is a `unit_outputs`
  model that no unit uses. The second case is a federate that publishes one
  key twice (a truth output that shadows a device point). The third case is a
  snapshot pattern that matches no publication.
- The ntp block can give defaults for each host role (`roles`). It can also
  name time servers that are not hosts of the variant (`external`). A client
  can use such a server as its source.

## Checks

`plantgen` runs these checks on every generation, and the tests run them on
every variant
([DESIGN.md §17](../../docs/DESIGN.md#17-testing-and-validation)):

- Tag names are unique. Every tag has an owner (a PLC, or the BACnet
  controller). Every field point has a binding to exactly one tag.
- Analog ranges fit `uint16` (or `int16` for signed points) after scaling.
- Every alarm is rationalized: it has a priority, a cause, a consequence, an
  action, and a response time. Every alarm has a limit inside its range. A tag
  has at most one alarm of each kind. If an alarm names a suppression bit, the
  bit exists and has the same owner.
- No block has register gaps. The ot-sim, modbus_master, modbus_slave, and
  FUXA addresses agree. The check derives each address again from the rendered
  files. BACnet instances agree between the app block, the FUXA tags, and the
  object table. An alarm that FUXA evaluates has a FUXA host that reads its
  tag.
- Interface addresses are inside their VLAN, and they are unique. NTP sources
  are servers that the client can reach. A client of an `external` server
  needs a routed network. HELICS lists are closed: every
  subscription has a publisher, and every field write has a federate that
  accepts it.
