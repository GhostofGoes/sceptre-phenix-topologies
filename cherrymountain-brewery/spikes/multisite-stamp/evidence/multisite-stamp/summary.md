# multisite-stamp

## plantgen on a stamped plant.yaml

| Run | Result | Detail |
|---|---|---|
| ci: plant.yaml as it is (control) | pass | 12 files generated |
| ci: site fc subnets and VLAN aliases, same hostnames | pass | 12 files generated |
| ci: site fc subnets, VLAN aliases, and hostnames | pass | 12 files generated; no first-site hostname or address in the configuration of 12 files |
| lite: plant.yaml as it is (control) | pass | 30 files generated |
| lite: site fc subnets and VLAN aliases, same hostnames | pass | 30 files generated |
| lite: site fc subnets, VLAN aliases, and hostnames | **fail** | plantgen/render.py, line 348, in helics_doc: KeyError: 'sim-ctl' |
| lite: the same, with plantgen's one `sim-ctl` lookup taken from the broker host | pass | 30 files generated; no first-site hostname or address in the configuration of 30 files; 82 mentions in descriptions and comments only |
| full: plant.yaml as it is (control) | pass | 31 files generated |
| full: site fc subnets and VLAN aliases, same hostnames | pass | 31 files generated |
| full: site fc subnets, VLAN aliases, and hostnames | **fail** | plantgen/render.py, line 348, in helics_doc: KeyError: 'sim-ctl' |
| full: the same, with plantgen's one `sim-ctl` lookup taken from the broker host | pass | 31 files generated; no first-site hostname or address in the configuration of 31 files; 82 mentions in descriptions and comments only |

## Four stamped site topologies in one experiment

| Item | Value |
|---|---|
| site topology files | 4 |
| nodes in the four sites | 132 |
| duplicate hostnames | [] |
| duplicate addresses | ["203.0.113.2"] |
| VLAN aliases in the four sites | 57 |
| longest hostname | fr-bas-rtu-taproom (18 characters; phenix allows 63) |
| subnets of fr | 10.10.10.0/24, 10.10.20.0/24, 10.20.10.0/24, 10.20.11.0/24, 10.20.12.0/24, 10.20.13.0/24, 10.20.14.0/24, 10.20.15.0/24, 10.20.16.0/24, 10.20.20.0/24, 10.20.30.0/24, 10.30.10.0/24, 10.40.10.0/24, 172.16.0.0/16, 203.0.113.0/24 |
| subnets of fc | 10.11.10.0/24, 10.11.20.0/24, 10.21.10.0/24, 10.21.11.0/24, 10.21.12.0/24, 10.21.13.0/24, 10.21.14.0/24, 10.21.15.0/24, 10.21.16.0/24, 10.21.20.0/24, 10.21.30.0/24, 10.31.10.0/24, 10.41.10.0/24, 172.17.0.0/16, 203.0.113.0/24 |
| subnets of ep | 10.12.10.0/24, 10.12.20.0/24, 10.22.10.0/24, 10.22.11.0/24, 10.22.12.0/24, 10.22.13.0/24, 10.22.14.0/24, 10.22.15.0/24, 10.22.16.0/24, 10.22.20.0/24, 10.22.30.0/24, 10.32.10.0/24, 10.42.10.0/24, 172.18.0.0/16, 203.0.113.0/24 |
| subnets of cs | 10.13.10.0/24, 10.13.20.0/24, 10.23.10.0/24, 10.23.11.0/24, 10.23.12.0/24, 10.23.13.0/24, 10.23.14.0/24, 10.23.15.0/24, 10.23.16.0/24, 10.23.20.0/24, 10.23.30.0/24, 10.33.10.0/24, 10.43.10.0/24, 172.19.0.0/16, 203.0.113.0/24 |
| phenix Topology schema | ok (4 files) |

## Building blocks

| Block | VMs | vCPUs | Memory (GB) |
|---|---:|---:|---:|
| site, full plant (core without the shared internet nodes) | 33 | 41 | 63 |
| shared internet nodes of the single-site core | 4 | 4 | 6 |
| SOC module (malcolm, wazuh, soc-analyst, soc-purple) | 4 | 18 | 84 |
| remote-access module (vendor-ws, dmz-vpn, dmz-jump) | 3 | 5 | 7 |
| site, brewpub (brewhouse, cellar, utilities, BAS) | 23 | 30 | 50 |
| site, cellar slice (the ci variant behind the firewalls, with a historian and an EWS) | 10 | 14 | 29 |
| mock internet, VyOS routers (6 routers, 6 small hosts) | 12 | 12 | 18 |
| mock internet, FRR routers (6 routers, 6 small hosts) | 12 | 12 | 9 |
| head office additions (fleet historian, corporate server, second office desktop) | 3 | 6 | 12 |
| network sensor at a site (Hedgehog-style, 4 vCPU, 16 GB) | 1 | 4 | 16 |

## Site mixes on one node with 48 cores and 96 threads

| Mix | VMs | vCPUs | Memory (GB) | vCPUs per thread | vCPUs per core |
|---|---:|---:|---:|---:|---:|
| M1: four full plants, central SOC | 154 | 205 | 373 | 2.1 | 4.3 |
| M2: four full plants, central SOC, a network sensor at each of the other three sites | 157 | 217 | 421 | 2.3 | 4.5 |
| M3: two full plants and two brewpubs, central SOC | 134 | 183 | 347 | 1.9 | 3.8 |
| M4: one full plant and three cellar slices, central SOC | 85 | 124 | 271 | 1.3 | 2.6 |
| M5: four full plants, no SOC and no vendor access | 147 | 182 | 282 | 1.9 | 3.8 |

## Mix M2 on a cluster of nodes with 48 cores, 96 threads, and 512 GB

| Cluster | Node | Runs | VMs | vCPUs | Memory (GB) | vCPUs per thread | Memory used |
|---|---|---|---:|---:|---:|---:|---:|
| one node | node 1 | internet, head office, SOC, vendor access; site fr; site fc; site ep; site cs | 157 | 217 | 421 | 2.26 | 82% |
| two nodes | node 1 | internet, head office, SOC, vendor access; site fr; site fc | 89 | 127 | 263 | 1.32 | 51% |
| two nodes | node 2 | site ep; site cs | 68 | 90 | 158 | 0.94 | 31% |
| three nodes, with the head node | head node | site ep | 34 | 45 | 79 | 0.47 | 15% |
| three nodes, with the head node | node 2 | internet, head office, SOC, vendor access; site fr | 55 | 82 | 184 | 0.85 | 36% |
| three nodes, with the head node | node 3 | site fc; site cs | 68 | 90 | 158 | 0.94 | 31% |
| four nodes, one for each site | node 1 | internet, head office, SOC, vendor access; site fr | 55 | 82 | 184 | 0.85 | 36% |
| four nodes, one for each site | node 2 | site fc | 34 | 45 | 79 | 0.47 | 15% |
| four nodes, one for each site | node 3 | site ep | 34 | 45 | 79 | 0.47 | 15% |
| four nodes, one for each site | node 4 | site cs | 34 | 45 | 79 | 0.47 | 15% |
| five nodes, one for each site and one for what the range has once | node 1 | internet, head office, SOC, vendor access | 22 | 41 | 121 | 0.43 | 24% |
| five nodes, one for each site and one for what the range has once | node 2 | site fr | 33 | 41 | 63 | 0.43 | 12% |
| five nodes, one for each site and one for what the range has once | node 3 | site fc | 34 | 45 | 79 | 0.47 | 15% |
| five nodes, one for each site and one for what the range has once | node 4 | site ep | 34 | 45 | 79 | 0.47 | 15% |
| five nodes, one for each site and one for what the range has once | node 5 | site cs | 34 | 45 | 79 | 0.47 | 15% |

## A workflow that pins each site to a node

| Item | Value |
|---|---|
| VMs with a node in `schedules` | 132 of 132 (the four sites; the shared nodes are not stamped) |
| VMs for each node | {"head-node": 33, "node-2": 33, "node-3": 66} |
| VLAN aliases with an ID | 60 |
| VLAN IDs given twice | [] |
| aliases of the site topologies without an ID | [] |
| VLAN range | 100 to 599 |
| default bridge | cherrymtn-fleet (15 characters; OVS allows 15) |
| phenix Workflow schema | ok |
