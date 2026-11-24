# Spike: four sites from one plant

Question: Is it possible to stamp out the single-site plant as four sites in
one phenix experiment? To stamp out a plant is to copy it with new names and
addresses. What does each site mix cost, and how does the largest mix sit on a
cluster? A site mix is one combination of site sizes and modules.

Status: This kit does not run on the current plantgen. The kit patches one
line of plantgen, and that line moved when plantgen got the fix. Now plantgen
writes the files of each site itself
([tools/plantgen](../../tools/plantgen/README.md#sites)). The evidence below is
from the last run of the kit, before that change.

A spike is a small test that answers a design question. This spike decides
three things for sections 4 and 6 of
[PLANS.md](../../docs/multisite/PLANS.md). It decides how to generate the
multi-site variant (the version of the range with four sites) and which site
mixes fit one host. It also decides where each site runs on a cluster of
minimega nodes.

A kit is the set of files that runs one spike. This kit rewrites strings. It
is a probe, not a generator. It finds every place that still assumes one
plant, because it changes the names and addresses under that place.

## Prerequisites

- Python 3.12 or later with `pyyaml` and `pydantic`.
- For the schema check: `check-jsonschema` 0.38.0 on `PATH`, and the `schemas/`
  directory of a sceptre-phenix checkout at the commit that `prek.toml` pins
  (`6cc94b2`).

## Steps

```shell
cd cherrymountain-brewery/spikes/multisite-stamp
python stamp.py --schema ~/sceptre-phenix/schemas
```

`stamp.py` writes `summary.md` and `result.json` under
`$CM_EVIDENCE_DIR/multisite-stamp` (the default is `evidence/`). The script
does four things:

1. plantgen: The script writes a `plant.yaml` for a second site (`fc`). It
   moves every private subnet to the site's block, and it prefixes every VLAN
   alias and hostname. It runs the real plantgen on that file for the `ci`,
   `lite`, and `full` variants. Then it scans the output for names and
   addresses of the first site.
2. Topology: The script stamps the site part of `topology-core.yml` four times
   (`fr`, `fc`, `ep`, `cs`). It makes sure that the merged node list obeys the
   three rules that a phenix experiment enforces. The rules are no duplicate
   hostname, no duplicate address, and one VLAN ID for each alias. It makes
   sure that each file agrees with phenix's Topology schema.
3. Budget: The script adds up VMs, vCPUs, and memory for the building blocks
   and for five site mixes.
4. Cluster: The script spreads the largest mix (M2) over one to five nodes of
   48 cores and 512 GB. It writes a workflow that names a node of the
   three-node cluster for each site VM. With `--schema`, it makes sure that the
   workflow file agrees with phenix's Workflow schema.

`--out DIR` keeps the four stamped topology files and the workflow.

## The stamping rule

| Item | First site (`fr`) | Site n (0-based) |
|---|---|---|
| Hostname | `fr-plc-cellar` | `<code>-plc-cellar` |
| VLAN alias | `FR-CTRL` | `<CODE>-CTRL` |
| IT, OT, BAS, and SOC blocks | `10.10`, `10.20`, `10.30`, `10.40` | The second octet plus n: `10.21.10.0/24` is CTRL at `fc` |
| MGMT | `172.16.0.0/16` | `172.(16+n).0.0/16`, with its own VLAN |
| Shared, not stamped | `isp-rtr`, `inet-traffic`, `util-rtr`, `utility-derms`, `vendor-ws`, and the VLANs INET, VENDOR, UTIL, MIRROR | The same nodes |

The first site keeps the address plan of DESIGN.md §10.2 unchanged.

## Pass / fail

- [x] plantgen generates a renumbered site with the first site's hostnames.
- [x] plantgen generates a site with prefixed hostnames, and the output holds
      no name or address of the first site.
- [x] Four stamped site topologies have no duplicate hostname and agree with
      the Topology schema.
- [x] At least one site mix stays near the single-site design's overcommit
      on 96 threads. Overcommit is the ratio of vCPUs to host threads.
- [x] On the three-node cluster, no node carries more than one vCPU for each
      thread. A workflow that names a node for every site VM agrees with the
      Workflow schema.

## Result

- Decision: go. A site is data. plantgen needs one changed line to generate a
  site with its own hostnames, VLAN aliases, and subnets. The topology files,
  `netcheck`, the `cherrymountain` app, the FUXA builder, and the simulation
  need more changes (see below).
- Run: The kit ran in a container with no VM, on `feat-secret-topo` at
  `fb43c7f`. The versions were Python 3.13.18, pydantic 2.13.5,
  check-jsonschema 0.38.0, and the phenix schemas at `6cc94b2`.

### plantgen

| Run | `ci` | `lite` | `full` |
|---|---|---|---|
| `plant.yaml` as it is (control) | pass, 12 files | pass, 30 files | pass, 31 files |
| Site `fc` subnets and VLAN aliases, same hostnames | pass | pass | pass |
| Site `fc` subnets, VLAN aliases, and hostnames | pass | fail | fail |
| The same, with one lookup changed | not needed | pass | pass |

- The one failure is `render.py` line 348. There, the ground-truth endpoint
  reads `plant.hosts['sim-ctl']`. Ground truth is the real state of the plant.
  The broker host two lines above that line is already data. When the endpoint
  comes from the broker host, `lite` and `full` generate. `ci` has no ground
  truth, so it passes unchanged.
- The output is the site's own. The stamped `full` output (31 files) holds no
  hostname and no address of the first site in its configuration. The broker
  is `fc-sim-ctl` at `172.17.10.1`. The names of the files come from the
  site's hosts (`openplc/fc-plc-cellar/…`, `fuxa/fc-scada-fuxa-tags.json`).
  Tag descriptions and Structured Text comments still hold 82 mentions of
  first-site names, such as "peer status for plc-utilities". Those mentions
  are prose.
- Some names are both a host and a kind. `ews`, `historian`, and `sim-ctl` are
  hostnames and also a role or an app type. `pv-inverter`, `bess-pcs`, and
  `gen-ats` are hostnames and device types. A generator must prefix the host
  and keep the kind. The first version of this kit did not do that, and
  plantgen refused the file.
- The kit did not prefix HELICS names. A federate is one model in a HELICS
  co-simulation. Federate names (`cellar`, `power`) and topic keys stay as
  they are. That is right only if each site has its own federation
  ([PLANS.md](../../docs/multisite/PLANS.md) section 4.7).

### Four sites in one experiment

| Item | Value |
|---|---|
| Nodes in the four site files | 132 (33 for each site) |
| Duplicate hostnames | none |
| Duplicate addresses | one: `203.0.113.2`, the WAN address of `fw-edge`. The stamp does not touch public addresses. The WAN plan assigns them |
| VLAN aliases | 57 (14 for each site, and INET). The single-site range pins 18 IDs in `vlanRange` 100 to 199, so the range must grow |
| Longest hostname | `fr-bas-rtu-taproom`, 18 characters (phenix allows 63) |
| phenix Topology schema | ok, 4 files |

### Building blocks

The sizes come from `topology-core.yml`, `topology-soc.yml`, and
`topology-remote-access.yml`. The brewpub and the cellar slice are subsets of
the core's nodes. Each subset keeps a `historian` and an `ews`, because the
head office's conduits end there. A conduit is an allowed network path between
security zones. The sizes of the mock internet, the head-office additions, and
the sensor are estimates.

| Block | VMs | vCPUs | Memory (GB) |
|---|---:|---:|---:|
| Site, full plant (the core without the shared internet nodes) | 33 | 41 | 63 |
| Site, brewpub (brewhouse, cellar, the utilities that serve them, and BAS, with no packaging line, no generation, and no DER link) | 23 | 30 | 50 |
| Site, cellar slice (the `ci` variant behind the two firewalls, with a `historian` and an `ews`) | 10 | 14 | 29 |
| SOC module | 4 | 18 | 84 |
| Remote-access module | 3 | 5 | 7 |
| Mock internet, VyOS routers (6 routers at 2 GB, 6 small hosts) | 12 | 12 | 18 |
| Mock internet, FRR routers (6 routers at 512 MB, 6 small hosts) | 12 | 12 | 9 |
| Head-office additions (fleet historian, corporate server, second office desktop) | 3 | 6 | 12 |
| Network sensor at a site (4 vCPU, 16 GB) | 1 | 4 | 16 |

### Site mixes on 48 cores and 96 threads

Every mix includes the mock internet as VyOS routers and the head-office
additions. The single-site design runs 68 vCPUs on 32 or more cores. That is
at most 2.1 vCPUs per core, or about 1.1 per thread.

| Mix | VMs | vCPUs | Memory (GB) | vCPUs per thread | vCPUs per core |
|---|---:|---:|---:|---:|---:|
| M1: four full plants, central SOC | 154 | 205 | 373 | 2.1 | 4.3 |
| M2: M1 and a network sensor at each of the other three sites | 157 | 217 | 421 | 2.3 | 4.5 |
| M3: two full plants and two brewpubs, central SOC | 134 | 183 | 347 | 1.9 | 3.8 |
| M4: one full plant and three cellar slices, central SOC | 85 | 124 | 271 | 1.3 | 2.6 |
| M5: four full plants, no SOC and no vendor access | 147 | 182 | 282 | 1.9 | 3.8 |

M4 is the only mix near the single-site design's overcommit (1.3 vCPUs per
thread against about 1.1). M1 doubles it. Every mix needs more than 256 GB of
memory at these sizes. DESIGN.md §11 sizes the full variant for a 512 GB host.

### Mix M2 on a cluster

A node has 48 cores, 96 threads, and 512 GB. The term "the shared part" means
what the range has once. It is the mock internet, the head office's additions,
the SOC, and the vendor's remote access (22 VMs, 41 vCPUs, 121 GB).

| Cluster | Node | Runs | VMs | vCPUs | Memory (GB) | vCPUs per thread | Memory used |
|---|---|---|---:|---:|---:|---:|---:|
| One node | 1 | Everything | 157 | 217 | 421 | 2.26 | 82% |
| Two nodes | 1 | The shared part, `fr`, `fc` | 89 | 127 | 263 | 1.32 | 51% |
| | 2 | `ep`, `cs` | 68 | 90 | 158 | 0.94 | 31% |
| Three nodes, with the head node (the planned cluster) | head node | `ep` | 34 | 45 | 79 | 0.47 | 15% |
| | 2 | The shared part, `fr` | 55 | 82 | 184 | 0.85 | 36% |
| | 3 | `fc`, `cs` | 68 | 90 | 158 | 0.94 | 31% |
| Four nodes | 1 | The shared part, `fr` | 55 | 82 | 184 | 0.85 | 36% |
| | 2, 3, 4 | `fc`, `ep`, `cs`: one on each node | 34 | 45 | 79 | 0.47 | 15% |
| Five nodes | 1 | The shared part | 22 | 41 | 121 | 0.43 | 24% |
| | 2 | `fr` | 33 | 41 | 63 | 0.43 | 12% |
| | 3, 4, 5 | `fc`, `ep`, `cs`: one on each node | 34 | 45 | 79 | 0.47 | 15% |

The planned cluster for the range has three nodes, and its head node runs
VMs. No node there carries more than one vCPU for each thread. Memory is never
the limit, because the whole mix uses 82% of the memory of one node. The head
node gets the lightest share, because it also runs phenix and serves the
images.

### A workflow that pins each site to a node

The kit writes `phenix-fleet.yml` next to the stamped topologies. The file has
the VLAN IDs, `schedules`, and `deployMode: all`. For the VLAN IDs, a site
keeps the single-site IDs plus 100 for each site after the first. The
schedules give a node to each of the 132 site VMs: 33 on `head-node`, 33 on
`node-2`, and 66 on `node-3`. These node names stand in for the cluster's own
names.

The file does not set `useGREMesh`, because the cluster bridges VLANs between
its nodes itself.

- No VLAN ID occurs twice, and every alias of the site topologies has an ID
  (60 aliases: 14 for each site and 4 shared).
- The file agrees with phenix's Workflow schema.
- The bridge name `cherrymtn-fleet` has 15 characters, which is as many as
  Open vSwitch allows.

## What the kit did not test

- The kit used no cluster. The kit made up the node names, phenix did not read
  the workflow, and no VLAN crossed between two nodes. The placement table counts
  vCPUs. It does not measure a node.

- No VM ran. The vCPU figures are counts, not load. No part of the range ran
  on a phenix host (DESIGN.md, Status), so the load of one site is unknown.
- phenix did not merge the files. The duplicate checks are this kit's own
  reading of phenix's rules. phenix ran neither `includeTopologies` nor the
  workflow.
- Only plantgen ran. The kit did not run `sync.py`, `netcheck`, the
  `cherrymountain` app, the FUXA builder, the PLC ZIP builder, or the
  federates on a stamped site. A reading of their code found single-plant
  assumptions in each ([PLANS.md](../../docs/multisite/PLANS.md) section 6).
- The brewpub and the cellar slice are node lists. No plantgen variant exists
  for either behind a site firewall.
