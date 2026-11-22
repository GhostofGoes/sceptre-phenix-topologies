# multisite-stamp

## plantgen on a stamped plant.yaml

| Run | Result | Detail |
|---|---|---|
| ci: plant.yaml as it is (control) | pass | 12 files generated |
| ci: site hc subnets and VLAN aliases, same hostnames | pass | 12 files generated |
| ci: site hc subnets, VLAN aliases, and hostnames | pass | 12 files generated; no first-site hostname or address in the configuration of 12 files |
| lite: plant.yaml as it is (control) | pass | 30 files generated |
| lite: site hc subnets and VLAN aliases, same hostnames | pass | 30 files generated |
| lite: site hc subnets, VLAN aliases, and hostnames | **fail** | plantgen/render.py, line 348, in helics_doc: KeyError: 'sim-ctl' |
| lite: the same, with plantgen's one `sim-ctl` lookup taken from the broker host | pass | 30 files generated; no first-site hostname or address in the configuration of 30 files; 82 mentions in descriptions and comments only |
| full: plant.yaml as it is (control) | pass | 31 files generated |
| full: site hc subnets and VLAN aliases, same hostnames | pass | 31 files generated |
| full: site hc subnets, VLAN aliases, and hostnames | **fail** | plantgen/render.py, line 348, in helics_doc: KeyError: 'sim-ctl' |
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
| subnets of hc | 10.11.10.0/24, 10.11.20.0/24, 10.21.10.0/24, 10.21.11.0/24, 10.21.12.0/24, 10.21.13.0/24, 10.21.14.0/24, 10.21.15.0/24, 10.21.16.0/24, 10.21.20.0/24, 10.21.30.0/24, 10.31.10.0/24, 10.41.10.0/24, 172.17.0.0/16, 203.0.113.0/24 |
| subnets of ws | 10.12.10.0/24, 10.12.20.0/24, 10.22.10.0/24, 10.22.11.0/24, 10.22.12.0/24, 10.22.13.0/24, 10.22.14.0/24, 10.22.15.0/24, 10.22.16.0/24, 10.22.20.0/24, 10.22.30.0/24, 10.32.10.0/24, 10.42.10.0/24, 172.18.0.0/16, 203.0.113.0/24 |
| subnets of gc | 10.13.10.0/24, 10.13.20.0/24, 10.23.10.0/24, 10.23.11.0/24, 10.23.12.0/24, 10.23.13.0/24, 10.23.14.0/24, 10.23.15.0/24, 10.23.16.0/24, 10.23.20.0/24, 10.23.30.0/24, 10.33.10.0/24, 10.43.10.0/24, 172.19.0.0/16, 203.0.113.0/24 |
| phenix Topology schema | ok (4 files) |

## Building blocks

| Block | VMs | vCPUs | Memory (GB) |
|---|---:|---:|---:|
| site, full plant (core without the shared internet nodes) | 33 | 41 | 63 |
| shared internet nodes of the single-site core | 4 | 4 | 6 |
| SOC module (malcolm, wazuh, soc-analyst, soc-purple) | 4 | 18 | 84 |
| remote-access module (vendor-ws, dmz-vpn, dmz-jump) | 3 | 5 | 7 |
| site, brewpub (brewhouse, cellar, BAS) | 20 | 26 | 44 |
| site, cellar slice (the ci variant behind a firewall) | 8 | 10 | 19 |
| mock internet, VyOS routers (6 routers, 6 small hosts) | 12 | 12 | 18 |
| mock internet, FRR routers (6 routers, 6 small hosts) | 12 | 12 | 9 |
| head office additions (fleet historian, corporate server, second office desktop) | 3 | 6 | 12 |
| network sensor at a site (Hedgehog-style, 4 vCPU, 16 GB) | 1 | 4 | 16 |

## Site mixes on a host with 48 cores and 96 threads

| Mix | VMs | vCPUs | Memory (GB) | vCPUs per thread | vCPUs per core |
|---|---:|---:|---:|---:|---:|
| M1: four full plants, central SOC | 154 | 205 | 364 | 2.1 | 4.3 |
| M2: four full plants, central SOC, a network sensor at each of the other three sites | 157 | 217 | 412 | 2.3 | 4.5 |
| M3: two full plants and two brewpubs, central SOC | 128 | 175 | 326 | 1.8 | 3.6 |
| M4: one full plant and three cellar slices, central SOC | 79 | 112 | 232 | 1.2 | 2.3 |
| M5: four full plants, no SOC and no vendor access | 147 | 182 | 273 | 1.9 | 3.8 |
