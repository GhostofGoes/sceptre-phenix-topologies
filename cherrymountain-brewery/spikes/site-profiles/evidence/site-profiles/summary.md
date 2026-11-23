# site-profiles

## What plantgen said after each step

### `fr`: Front Range: flagship brewery, taproom, head office

| Step | Result | plantgen said |
|---|---|---|
| 1. The site's hosts, areas, and federates as a variant; nothing else changed | **fail** |   ntp: util-rtr is not a variant host /   ntp: fr-corp-ws source inet-traffic is not a server /   ntp: fr-fw-edge source inet-traffic is not a server |
| 2. Every clock follows a server of the site | **fail** | plantgen: site: consistency checks failed: /   HELICS: no publisher for cellar/co2.draw_slpm /   HELICS: no publisher for cellar/beer.supply_psia |
| 3. The ground-truth snapshot asks only for federates the site runs | **fail** | plantgen: site: consistency checks failed: /   HELICS: no publisher for cellar/co2.draw_slpm /   HELICS: no publisher for cellar/beer.supply_psia |
| 4. The site's variant added to the federate keys that name `full` and `lite` | pass | 29 files generated |
| 5. Take out what names a host the site does not have | pass | 29 files generated; 0 differ from the step before |
| 6. An access link to each ISP in place of the INET VLAN | pass | 29 files generated; 0 differ from the step before |
| 7. The site's own zones: VLANs merged, split, renamed, or renumbered | pass | 29 files generated; 0 differ from the step before |

### `ws`: Western Slope: production brewery

| Step | Result | plantgen said |
|---|---|---|
| 1. The site's hosts, areas, and federates as a variant; nothing else changed | **fail** |   ntp: util-rtr is not a variant host /   ntp: ws-corp-ws source inet-traffic is not a server /   ntp: ws-fw-edge source inet-traffic is not a server |
| 2. Every clock follows a server of the site | **fail** | plantgen: site: consistency checks failed: /   HELICS: no publisher for cellar/co2.draw_slpm /   HELICS: no publisher for cellar/beer.supply_psia |
| 3. The ground-truth snapshot asks only for federates the site runs | **fail** | plantgen: site: consistency checks failed: /   HELICS: no publisher for cellar/co2.draw_slpm /   HELICS: no publisher for cellar/beer.supply_psia |
| 4. The site's variant added to the federate keys that name `full` and `lite` | pass | 28 files generated |
| 5. Take out what names a host the site does not have | pass | 28 files generated; 0 differ from the step before |
| 6. An access link to each ISP in place of the INET VLAN | pass | 28 files generated; 0 differ from the step before |
| 7. The site's own zones: VLANs merged, split, renamed, or renumbered | pass | 28 files generated; 2 differ from the step before (fuxa/ws-scada-fuxa-tags.json, openplc/ws-plc-utilities/modbus_master.json); no first-site hostname or address in the configuration of 28 files; 82 mentions in descriptions and comments only |

### `gc`: Gulf Coast: brewery and taproom, bought from another company

| Step | Result | plantgen said |
|---|---|---|
| 1. The site's hosts, areas, and federates as a variant; nothing else changed | **fail** |   ntp: gc-fw-ot is not a variant host /   ntp: gc-corp-ws source inet-traffic is not a server /   ntp: gc-fw-edge source inet-traffic is not a server |
| 2. Every clock follows a server of the site | **fail** | plantgen: site: snapshot: key pattern(s) ['power/*'] match no publication of another federate in variant site |
| 3. The ground-truth snapshot asks only for federates the site runs | **fail** | plantgen: site: consistency checks failed: /   HELICS: no publisher for cellar/co2.draw_slpm /   HELICS: no publisher for cellar/beer.supply_psia |
| 4. The site's variant added to the federate keys that name `full` and `lite` | pass | 25 files generated |
| 5. Take out what names a host the site does not have | pass | 25 files generated; 0 differ from the step before |
| 6. An access link to each ISP in place of the INET VLAN | pass | 25 files generated; 0 differ from the step before |
| 7. The site's own zones: VLANs merged, split, renamed, or renumbered | pass | 25 files generated; 9 differ from the step before (bacnet/gc-bas-coldroom.json, bacnet/gc-bas-rtu-taproom.json, bacnet/gc-bas-vent.json, fuxa/gc-bas-fuxa-tags.json, ...); no first-site hostname or address in the configuration of 25 files; 70 mentions in descriptions and comments only |

### `hc`: High Country: brewpub

| Step | Result | plantgen said |
|---|---|---|
| 1. The site's hosts, areas, and federates as a variant; nothing else changed | **fail** |   ntp: hc-hmi-brewhouse source hc-ot-svc is not a server /   ntp: hc-plc-brewhouse source hc-ot-svc is not a server /   ntp: hc-plc-utilities source hc-ot-svc is not a server |
| 2. Every clock follows a server of the site | **fail** | plantgen: site: snapshot: key pattern(s) ['power/*', 'packaging/bottles.total', 'packaging/kegs.total', 'packaging/ttb.bbl', 'packaging/fill.*'] match no publication of another federate in variant site |
| 3. The ground-truth snapshot asks only for federates the site runs | **fail** | plantgen: site: consistency checks failed: /   HELICS: no publisher for cellar/co2.draw_slpm |
| 4. The site's variant added to the federate keys that name `full` and `lite` | pass | 22 files generated |
| 5. Take out what names a host the site does not have | pass | 22 files generated; 0 differ from the step before |
| 6. An access link to each ISP in place of the INET VLAN | pass | 22 files generated; 0 differ from the step before |
| 7. The site's own zones: VLANs merged, split, renamed, or renumbered | pass | 22 files generated; 4 differ from the step before (bacnet/hc-bas-coldroom.json, bacnet/hc-bas-rtu-taproom.json, bacnet/hc-bas-vent.json, fuxa/hc-scada-fuxa-tags.json); no first-site hostname or address in the configuration of 22 files; 70 mentions in descriptions and comments only |

## The four sites

| Site | Hosts in its plant file | PLCs | Tags | VLANs | VMs | vCPUs | Memory (GB) | With its sensor |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| `fr` | 39 | 5 | 516 | 16 | 39 | 62 | 151 | none |
| `ws` | 33 | 5 | 504 | 20 | 33 | 42 | 65 | 34, 46, 81 |
| `gc` | 25 | 4 | 458 | 9 | 25 | 33 | 53 | 26, 37, 69 |
| `hc` | 17 | 3 | 365 | 8 | 17 | 21 | 33 | 18, 25, 49 |

## VLANs of each site

| Site | VLANs |
|---|---|
| `fr` | FR-BAS, FR-CORP, FR-CTRL, FR-DERLINK, FR-DMZ, FR-FIELD-BH, FR-FIELD-CEL, FR-FIELD-PKG, FR-FIELD-PWR, FR-FIELD-UTL, FR-HMI, FR-MGMT, FR-OPS, FR-SOC, FR-WAN-A, FR-WAN-B |
| `ws` | WS-BAS, WS-CORP, WS-CTRL-BH, WS-CTRL-CEL, WS-CTRL-PKG, WS-CTRL-PWR, WS-CTRL-UTL, WS-DERLINK, WS-DMZ, WS-FIELD-BH, WS-FIELD-CEL, WS-FIELD-PKG, WS-FIELD-PWR, WS-FIELD-UTL, WS-HMI, WS-MGMT, WS-OPS, WS-SOC, WS-WAN-A, WS-WAN-B |
| `gc` | GC-BAS, GC-FIELD-BH, GC-FIELD-CEL, GC-FIELD-PKG, GC-FIELD-UTL, GC-MGMT, GC-OFFICE, GC-PLANT, GC-WAN-A |
| `hc` | HC-FIELD-BH, HC-FIELD-CEL, HC-FIELD-UTL, HC-MGMT, HC-OFFICE, HC-PLANT, HC-WAN-A, HC-WAN-B |

## The four sites in one experiment

| Item | Value |
|---|---|
| site topology files | 4 |
| nodes in the four sites | 117 |
| duplicate hostnames | [] |
| duplicate addresses | [] |
| VLAN aliases in the four sites | 53 |
| subnets of fr | 10.10.10.0/24, 10.10.20.0/24, 10.20.10.0/24, 10.20.11.0/24, 10.20.12.0/24, 10.20.13.0/24, 10.20.14.0/24, 10.20.15.0/24, 10.20.16.0/24, 10.20.20.0/24, 10.20.30.0/24, 10.30.10.0/24, 10.40.10.0/24, 172.16.0.0/16, 198.18.101.0/30, 203.0.113.0/29 |
| subnets of ws | 10.12.10.0/24, 10.12.20.0/24, 10.22.11.0/24, 10.22.12.0/24, 10.22.13.0/24, 10.22.14.0/24, 10.22.15.0/24, 10.22.16.0/24, 10.22.20.0/24, 10.22.30.0/24, 10.22.41.0/24, 10.22.42.0/24, 10.22.43.0/24, 10.22.44.0/24, 10.22.46.0/24, 10.32.10.0/24, 10.42.10.0/24, 172.18.0.0/16, 198.18.101.8/30, 203.0.113.16/30 |
| subnets of gc | 172.19.0.0/16, 192.168.10.0/24, 192.168.20.0/24, 192.168.21.0/24, 192.168.22.0/24, 192.168.23.0/24, 192.168.24.0/24, 192.168.30.0/24, 198.18.103.0/30 |
| subnets of hc | 10.11.10.0/24, 10.21.10.0/24, 10.21.11.0/24, 10.21.12.0/24, 10.21.14.0/24, 172.17.0.0/16, 198.18.102.0/30, 203.0.113.8/30 |
| phenix Topology schema | ok (4 files) |

## The whole range

| Part | VMs | vCPUs | Memory (GB) |
|---|---:|---:|---:|
| mock internet, VyOS routers (6 routers, 6 small hosts) | 12 | 12 | 18 |
| the vendor's workstation, on the internet | 1 | 2 | 3 |
| head office additions (fleet historian, corporate server, second office desktop) | 3 | 6 | 12 |
| site `fr`, with its sensor if it has one | 39 | 62 | 151 |
| site `ws`, with its sensor if it has one | 34 | 46 | 81 |
| site `gc`, with its sensor if it has one | 26 | 37 | 69 |
| site `hc`, with its sensor if it has one | 18 | 25 | 49 |
| **all of it** | 133 | 190 | 383 |

All of it on one node would be 1.98 vCPUs per thread and 75% of its memory.

## On three nodes of 48 cores, 96 threads, and 512 GB

| Layout | Node | Runs | VMs | vCPUs | Memory (GB) | vCPUs per thread | Memory used |
|---|---|---|---:|---:|---:|---:|---:|
| L1: the head office apart | head node | site ws | 34 | 46 | 81 | 0.48 | 16% |
| L1: the head office apart | node 2 | internet, vendor, head office additions; site fr | 55 | 82 | 184 | 0.85 | 36% |
| L1: the head office apart | node 3 | site gc; site hc | 44 | 62 | 118 | 0.65 | 23% |
| L2: the two small sites on the head node | head node | site gc; site hc | 44 | 62 | 118 | 0.65 | 23% |
| L2: the two small sites on the head node | node 2 | internet, vendor, head office additions; site fr | 55 | 82 | 184 | 0.85 | 36% |
| L2: the two small sites on the head node | node 3 | site ws | 34 | 46 | 81 | 0.48 | 16% |
| L3: the head office alone on a node | head node | internet, vendor, head office additions; site gc | 42 | 57 | 102 | 0.59 | 20% |
| L3: the head office alone on a node | node 2 | site fr | 39 | 62 | 151 | 0.65 | 29% |
| L3: the head office alone on a node | node 3 | site ws; site hc | 52 | 71 | 130 | 0.74 | 25% |

## A workflow that pins each site to a node

| Item | Value |
|---|---|
| layout | L1: the head office apart |
| VMs with a node in `schedules` | 117 |
| VMs for each node | {"head-node": 34, "node-2": 39, "node-3": 44} |
| VLAN aliases with an ID | 54 |
| VLAN IDs given twice | [] |
| aliases of the site topologies without an ID | [] |
| phenix Workflow schema | ok |
