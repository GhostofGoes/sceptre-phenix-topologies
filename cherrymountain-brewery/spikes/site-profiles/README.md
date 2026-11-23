# Spike: four sites that are not copies

**Question.** The four breweries are to differ in size and in how their
networks are cut, as the sites of a real company do. Can the generator make
such sites from the one plant, do they fit in one phenix experiment, and what
do they cost? **Decides:** what each site is, how a site is described to the
generator, and where each site runs on the cluster
([PLANS.md](../../docs/multisite/PLANS.md) sections 2, 4.1, 4.8, and 6).

Like the [stamp spike](../multisite-stamp/README.md) this kit is a probe, not
a generator. It reuses that spike's stamp (a site's prefix on hostnames and
VLAN aliases, the site's number in its addresses).

## The four sites

| | `fr` Front Range | `ws` Western Slope | `gc` Gulf Coast | `hc` High Country |
|---|---|---|---|---|
| What it is | Flagship brewery, taproom, head office | Production brewery | Brewery and taproom, bought from another company | Brewpub |
| Process areas | All six | All six, without the taproom's rooftop unit | Brewhouse, cellar, packaging, utilities, building | Brewhouse, cellar, utilities, building |
| PLCs | 5 | 5 | 4 | 3 |
| Firewalls | 2, with a DMZ between them | 2, with a DMZ and its own jump host | 1 | 1 |
| Control network | One VLAN for the PLCs | One VLAN for each process cell: 5 | One flat plant VLAN: PLCs, HMIs, SCADA, historian, EWS | One flat plant VLAN, with the building controllers on it too |
| Address plan | The company's | The company's | Its own, `192.168.0.0/16` | The company's |
| Historian and EWS on site | Both | Both | Both | Neither |
| Uplinks | 2 | 2 | 1 | 2 |

## Prerequisites

- Python 3.12 or later with `pyyaml` and `pydantic`.
- For the schema checks: `check-jsonschema` 0.38.0 on `PATH`, and the
  `schemas/` directory of a sceptre-phenix checkout at the commit `prek.toml`
  pins (`6cc94b2`).

## Steps

```shell
cd cherrymountain-brewery/spikes/site-profiles
python profiles.py --schema ~/sceptre-phenix/schemas
```

`profiles.py` does three things and writes `summary.md` and `result.json`
under `$CM_EVIDENCE_DIR/site-profiles` (default `evidence/`):

1. **plantgen.** For each site it edits a copy of `plant.yaml` in seven steps,
   stamps the copy with the site's prefix, and runs the real plantgen after
   each step. The record shows what plantgen said after each step and which
   generated files the step changed.
2. **Topology.** It builds each site's phenix nodes from today's nodes and the
   interfaces of the site's plant file, adds a sensor where the site has one,
   checks the merged node list against the rules a phenix experiment enforces
   (no duplicate hostname, no duplicate address, one VLAN ID for each alias),
   and validates each file against phenix's Topology schema.
3. **Size.** It adds up VMs, vCPUs, and memory for each site and for the
   range, spreads them over the three nodes in three ways, and writes a
   workflow that names a node for every site VM.

`--out DIR` keeps the four topology files and the workflow.

The seven steps, in order:

| # | Step | What it edits |
|---|---|---|
| 1 | The site as a variant | A variant with the site's hosts, areas, and federates. Nothing else |
| 2 | Clocks | The time server of every host is a host of the site |
| 3 | Snapshot | The ground-truth snapshot asks only for federates the site runs |
| 4 | Variant names | The site's variant is added to the five federate keys that name `full` and `lite` |
| 5 | References | Everything that names a host the site lacks is taken out: FUXA sources, DNP3 upstreams, conduits, agents, loads |
| 6 | Uplinks | The edge gets an access link to each ISP in place of the INET VLAN |
| 7 | Zones | The site's own zones: VLANs merged, split, renamed, or renumbered, and the firewalls' legs to match |

## Pass / fail

- [x] plantgen generates all four sites, and no generated file of a site
      other than `fr` holds a hostname or an address of `fr`.
- [x] The four sites have no duplicate hostname and no duplicate address, and
      their topology files validate against the Topology schema.
- [x] On the three-node cluster no node carries more than one vCPU for each
      thread, and a workflow that names a node for every site VM validates
      against the Workflow schema.

## Result

- **Decision:** **go.** A site that differs is still data. plantgen generated
  all four sites with the one line changed that the stamp spike found, and
  with three kinds of edit to the plant file that a site's description has to
  carry. Cutting a network differently (one firewall, a flat plant VLAN, a
  VLAN for each cell, another address plan) needed no change to plantgen.
- **Run:** in a container with no VM, on `feat-cherrymountain-multisite`,
  Python 3.13.18, pydantic 2.13.5, check-jsonschema 0.38.0, phenix schemas at
  `6cc94b2`. [`evidence/site-profiles/summary.md`](evidence/site-profiles/summary.md)
  has the full record.

### What plantgen needed

| Step | `fr` | `ws` | `gc` | `hc` |
|---|---|---|---|---|
| 1. The site as a variant | fail | fail | fail | fail |
| 2. Clocks | fail | fail | fail | fail |
| 3. Snapshot | fail | fail | fail | fail |
| 4. Variant names | pass, 29 files | pass, 28 files | pass, 25 files | pass, 22 files |
| 5. References | pass, no file changed | pass, no file changed | pass, no file changed | pass, no file changed |
| 6. Uplinks | pass, no file changed | pass, no file changed | pass, no file changed | pass, no file changed |
| 7. Zones | pass, no file changed | pass, 2 files changed | pass, 9 files changed | pass, 4 files changed |

- **Clocks (step 2).** plantgen refuses a time source that is not a host of
  the variant. Today the edge firewall and the office PC follow
  `inet-traffic`, which is a host of the internet in a fleet. The kit pointed
  them at the site's own server to get past the check. A real edge follows a
  server outside the site, so plantgen must learn to accept one.
- **Snapshot (step 3).** The snapshot's key list names federates. A site
  without packaging or power needs its own list. `fr` and `ws` did not need
  the step.
- **Variant names (step 4).** Five federate keys in `plant.yaml` carry
  `variants: [full, lite]`. A variant with another name loses them without a
  word, and the federation check then fails ("no publisher for
  `cellar/co2.draw_slpm`"). This is the step that made every site generate. A
  flag on the variant would say it better than a list of names.
- **References (step 5) changed nothing.** plantgen already leaves out a
  FUXA source, a DNP3 upstream, or a conduit whose host the variant lacks. A
  site's description does not have to repeat what it dropped.
- **Uplinks (step 6) changed nothing in plantgen's output.** The edge's WAN
  side is in the topology and in the routers' configuration, not in the files
  plantgen writes.
- **Zones (step 7) changed only the files that hold an address.** At `ws` the
  FUXA project and the utilities PLC's peer read moved to the cell VLANs. At
  `gc` and `hc` the FUXA projects, the BACnet tables, and the PLCs' Modbus
  files moved to the new subnets. plantgen takes the networks as data: no
  check failed on one firewall, a merged VLAN, or `192.168.0.0/16`.

### The four sites

| Site | Hosts | PLCs | Tags | VLANs | VMs | vCPUs | Memory (GB) | With its sensor |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| `fr` | 39 | 5 | 516 | 16 | 39 | 62 | 151 | the SOC is here |
| `ws` | 33 | 5 | 504 | 20 | 33 | 42 | 65 | 34 VMs, 46 vCPUs, 81 GB |
| `gc` | 25 | 4 | 458 | 9 | 25 | 33 | 53 | 26 VMs, 37 vCPUs, 69 GB |
| `hc` | 17 | 3 | 365 | 8 | 17 | 21 | 33 | 18 VMs, 25 vCPUs, 49 GB |

`fr`'s count includes the SOC (4 VMs, 18 vCPUs, 84 GB) and the vendor's
gateway and jump host. A sensor is 4 vCPUs and 16 GB, an estimate.

| Site | Routed zones | Unrouted |
|---|---|---|
| `fr` | CORP, DMZ, OPS, HMI, CTRL, DERLINK, BAS, SOC | 5 field networks, MGMT |
| `ws` | CORP, DMZ, OPS, HMI, CTRL-BH, CTRL-CEL, CTRL-PKG, CTRL-UTL, CTRL-PWR, DERLINK, BAS, SOC | 5 field networks, MGMT |
| `gc` | OFFICE, PLANT, BAS | 4 field networks, MGMT |
| `hc` | OFFICE, PLANT | 3 field networks, MGMT |

### Four sites in one experiment

| Item | Value |
|---|---|
| Nodes in the four site files | 117 (39, 34, 26, and 18) |
| Duplicate hostnames | none |
| Duplicate addresses | none. Each edge has its own address on each access link |
| VLAN aliases | 53, and MIRROR |
| phenix Topology schema | ok, 4 files |
| Largest router | `ws-fw-ot`, with 11 interfaces |

### The whole range

| Part | VMs | vCPUs | Memory (GB) |
|---|---:|---:|---:|
| Mock internet, VyOS routers (6 routers, 6 small hosts) | 12 | 12 | 18 |
| The vendor's workstation, on the internet | 1 | 2 | 3 |
| Head-office additions (fleet historian, corporate server, second office desktop) | 3 | 6 | 12 |
| `fr` | 39 | 62 | 151 |
| `ws` and its sensor | 34 | 46 | 81 |
| `gc` and its sensor | 26 | 37 | 69 |
| `hc` and its sensor | 18 | 25 | 49 |
| **All of it** | **133** | **190** | **383** |

Four full plants with a sensor at three of them were 157 VMs, 217 vCPUs, and
421 GB (mix M2 of the stamp spike). On one node this range would be 1.98
vCPUs for each thread and 75% of the memory.

### On the three-node cluster

A node has 48 cores, 96 threads, and 512 GB. "The shared part" is the mock
internet, the vendor's workstation, and the head office's additions (16 VMs,
20 vCPUs, 33 GB).

| Layout | Node | Runs | VMs | vCPUs | Memory (GB) | vCPUs per thread | Memory used |
|---|---|---|---:|---:|---:|---:|---:|
| **L1: the head office next to the internet** | head node | `ws` | 34 | 46 | 81 | 0.48 | 16% |
| | 2 | The shared part, `fr` | 55 | 82 | 184 | 0.85 | 36% |
| | 3 | `gc`, `hc` | 44 | 62 | 118 | 0.65 | 23% |
| L2: the two small sites on the head node | head node | `gc`, `hc` | 44 | 62 | 118 | 0.65 | 23% |
| | 2 | The shared part, `fr` | 55 | 82 | 184 | 0.85 | 36% |
| | 3 | `ws` | 34 | 46 | 81 | 0.48 | 16% |
| L3: the head office alone on a node | head node | The shared part, `gc` | 42 | 57 | 102 | 0.59 | 20% |
| | 2 | `fr` | 39 | 62 | 151 | 0.65 | 29% |
| | 3 | `ws`, `hc` | 52 | 71 | 130 | 0.74 | 25% |

Every layout keeps each node under one vCPU for each thread. L1 gives the
head node the least, and it also runs phenix and serves the images. L3 is the
most even. The kit writes the workflow for L1.

### A workflow that pins each site to a node

`phenix-fleet.yml` names a node for each of the 117 site VMs (34 on
`head-node`, 39 on `node-2`, 44 on `node-3`; the names stand in for the
cluster's own) and gives an ID to each of the 54 VLAN aliases.

- A site keeps the single-site IDs plus 100 for each site after the first. A
  VLAN the single-site plan does not have takes the next ID from 20 up in the
  site's hundred (`WS-CTRL-BH` is 320, `GC-PLANT` is 421).
- The seven access links take 500 to 506.
- No ID is given twice, every alias of the site topologies has one, and the
  file validates against phenix's Workflow schema.

## What the kit did not test

- **No VM ran, and no cluster.** The sizes are counts. Nothing here measured
  a site.
- **Only plantgen ran.** `sync.py`, `netcheck`, the `cherrymountain` app, the
  FUXA builder, the PLC ZIP builder, and the federates were not run on any of
  the four sites.
- **Firewall rules.** The kit moves interfaces and addresses. The conduit
  matrix of a site with one firewall, of a flat plant VLAN, and of a VLAN for
  each cell is not written. At `ws` the utilities PLC's read from the cellar
  PLC becomes a conduit through `ws-fw-ot`.
- **Whole areas only.** Every site drops whole process areas, which drops
  whole PLCs. The one unit dropped inside an area is the taproom's rooftop
  unit at `ws`, which is a BACnet controller with no PLC. A site that drops a
  unit inside a PLC's area (a brewpub with two fermenters, a site with a
  generator and no battery) needs a PLC program for that unit list. The
  programs are per PLC today.
- **The brewpub's SCADA.** `hc` has no building SCADA. Its one SCADA server
  would show the taproom's and the cold room's points too. The kit did not
  add them to its FUXA sources.
- **The sensor is a stand-in:** a node with a name, a size, and an address. No
  image exists for it.
- **The size of a plant.** Every site runs the same vessels: 15 bbl brews and
  30 bbl tanks. The simulation holds those sizes as constants.
