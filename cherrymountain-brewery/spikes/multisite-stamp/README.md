# Spike: four sites from one plant

**Question.** Can the single-site plant be stamped out as four sites in one
phenix experiment, and what does each site mix cost? **Decides:** how the
multi-site variant is generated, and which site mixes fit the host
([PLANS.md](../../docs/multisite/PLANS.md) sections 4 and 6).

The kit rewrites strings. It is a probe, not a generator: it finds every place
that still assumes one plant by changing the names and addresses under it.

## Prerequisites

- Python 3.12 or later with `pyyaml` and `pydantic`.
- For the schema check: `check-jsonschema` 0.38.0 on `PATH`, and the `schemas/`
  directory of a sceptre-phenix checkout at the commit `prek.toml` pins
  (`6cc94b2`).

## Steps

```shell
cd cherrymountain-brewery/spikes/multisite-stamp
python stamp.py --schema ~/sceptre-phenix/schemas
```

`stamp.py` does three things and writes `summary.md` and `result.json` under
`$CM_EVIDENCE_DIR/multisite-stamp` (default `evidence/`):

1. **plantgen.** It writes a `plant.yaml` for a second site (`hc`): every
   private subnet moved to the site's block, every VLAN alias and hostname
   prefixed. It runs the real plantgen on it for the `ci`, `lite`, and `full`
   variants, then scans the output for names and addresses of the first site.
2. **Topology.** It stamps the site part of `topology-core.yml` four times
   (`fr`, `hc`, `ws`, `gc`), checks the merged node list against the three
   rules a phenix experiment enforces (no duplicate hostname, no duplicate
   address, one VLAN ID per alias), and validates each file against phenix's
   Topology schema.
3. **Budget.** It adds up VMs, vCPUs, and memory for the building blocks and
   for five site mixes.

`--out DIR` keeps the four stamped topology files.

## The stamping rule

| Item | First site (`fr`) | Site n (0-based) |
|---|---|---|
| Hostname | `fr-plc-cellar` | `<code>-plc-cellar` |
| VLAN alias | `FR-CTRL` | `<CODE>-CTRL` |
| IT, OT, BAS, and SOC blocks | `10.10`, `10.20`, `10.30`, `10.40` | second octet plus n: `10.21.10.0/24` is CTRL at `hc` |
| MGMT | `172.16.0.0/16` | `172.(16+n).0.0/16`, its own VLAN |
| Shared, not stamped | `isp-rtr`, `inet-traffic`, `util-rtr`, `utility-derms`, `vendor-ws`; VLANs INET, VENDOR, UTIL, MIRROR | the same nodes |

The first site keeps the address plan of DESIGN.md §10.2 unchanged.

## Pass / fail

- [x] plantgen generates a renumbered site with the first site's hostnames.
- [x] plantgen generates a site with prefixed hostnames, and the output holds
      no name or address of the first site.
- [x] Four stamped site topologies have no duplicate hostname and validate
      against the Topology schema.
- [x] At least one site mix stays near the single-site design's overcommit
      on 96 threads.

## Result

- **Decision:** **go.** A site is data. plantgen needs one line changed to
  generate a site with its own hostnames, VLAN aliases, and subnets. The
  topology files, `netcheck`, the `cherrymountain` app, the FUXA builder, and
  the simulation need more (below).
- **Run:** in a container with no VM, on `feat-secret-topo` at `fb43c7f`,
  Python 3.13.18, pydantic 2.13.5, check-jsonschema 0.38.0, phenix schemas at
  `6cc94b2`.

### plantgen

| Run | `ci` | `lite` | `full` |
|---|---|---|---|
| `plant.yaml` as it is (control) | pass, 12 files | pass, 30 files | pass, 31 files |
| Site `hc` subnets and VLAN aliases, same hostnames | pass | pass | pass |
| Site `hc` subnets, VLAN aliases, and hostnames | pass | **fail** | **fail** |
| The same, with one lookup changed | not needed | pass | pass |

- **The one failure** is `render.py` line 348: the ground-truth endpoint reads
  `plant.hosts['sim-ctl']`. The broker host two lines above it is already data.
  With the endpoint taken from the broker host, `lite` and `full` generate.
  `ci` has no ground truth, so it passes unchanged.
- **The output is the site's own.** The stamped `full` output (31 files) holds
  no hostname and no address of the first site in its configuration. The
  broker is `hc-sim-ctl` at `172.17.10.1`, and the files are named for the
  site's hosts (`openplc/hc-plc-cellar/…`, `fuxa/hc-scada-fuxa-tags.json`).
  82 mentions remain in tag descriptions and Structured Text comments, such
  as "peer status for plc-utilities". They are prose.
- **Names that are both a host and a kind.** `ews`, `historian`, and `sim-ctl`
  are hostnames and also a role or an app type, and `pv-inverter`, `bess-pcs`,
  and `gen-ats` are hostnames and device types. A generator must prefix the
  host and keep the kind. The first version of this kit did not, and plantgen
  refused the file.
- **HELICS names were not prefixed.** Federate names (`cellar`, `power`) and
  topic keys stay as they are, which is right only if each site has its own
  federation ([PLANS.md](../../docs/multisite/PLANS.md) section 4.7).

### Four sites in one experiment

| Item | Value |
|---|---|
| Nodes in the four site files | 132 (33 a site) |
| Duplicate hostnames | none |
| Duplicate addresses | one: `203.0.113.2`, the WAN address of `fw-edge`. The stamp does not touch public addresses; the WAN plan assigns them |
| VLAN aliases | 57 (14 a site, and INET). The single-site range pins 18 IDs in `vlanRange` 100 to 199, so the range must grow |
| Longest hostname | `fr-bas-rtu-taproom`, 18 characters (phenix allows 63) |
| phenix Topology schema | ok, 4 files |

### Building blocks

Sizes are those of `topology-core.yml`, `topology-soc.yml`, and
`topology-remote-access.yml`. The brewpub and the cellar slice are subsets of
the core's nodes. The mock internet, the head-office additions, and the
sensor are estimates.

| Block | VMs | vCPUs | Memory (GB) |
|---|---:|---:|---:|
| Site, full plant (the core without the shared internet nodes) | 33 | 41 | 63 |
| Site, brewpub (brewhouse, cellar, BAS; no packaging line, no utilities PLC, no generation, no DER link) | 20 | 26 | 44 |
| Site, cellar slice (the `ci` variant behind the two firewalls) | 8 | 10 | 19 |
| SOC module | 4 | 18 | 84 |
| Remote-access module | 3 | 5 | 7 |
| Mock internet, VyOS routers (6 routers at 2 GB, 6 small hosts) | 12 | 12 | 18 |
| Mock internet, FRR routers (6 routers at 512 MB, 6 small hosts) | 12 | 12 | 9 |
| Head-office additions (fleet historian, corporate server, second office desktop) | 3 | 6 | 12 |
| Network sensor at a site (4 vCPU, 16 GB) | 1 | 4 | 16 |

### Site mixes on 48 cores and 96 threads

Every mix includes the mock internet as FRR routers and the head-office
additions. The single-site design runs 68 vCPUs on 32 or more cores: at most
2.1 vCPUs per core, or about 1.1 per thread.

| Mix | VMs | vCPUs | Memory (GB) | vCPUs per thread | vCPUs per core |
|---|---:|---:|---:|---:|---:|
| M1: four full plants, central SOC | 154 | 205 | 364 | 2.1 | 4.3 |
| M2: M1 and a network sensor at each of the other three sites | 157 | 217 | 412 | 2.3 | 4.5 |
| M3: two full plants and two brewpubs, central SOC | 128 | 175 | 326 | 1.8 | 3.6 |
| M4: one full plant and three cellar slices, central SOC | 79 | 112 | 232 | 1.2 | 2.3 |
| M5: four full plants, no SOC and no vendor access | 147 | 182 | 273 | 1.9 | 3.8 |

M4 is the only mix near the single-site design's overcommit (1.2 vCPUs per
thread against about 1.1). M1 doubles it. Every mix needs more than 192 GB of
memory, and every mix but M4 more than 256 GB. DESIGN.md §11 sizes the full
variant for a 512 GB host.

## What the kit did not test

- **No VM ran.** The vCPU figures are counts, not load. No part of the range
  has run on a phenix host (DESIGN.md, Status), so the load of one site is not
  known.
- **phenix did not merge the files.** The duplicate checks are this kit's own
  reading of phenix's rules. `includeTopologies` and the workflow were not run.
- **Only plantgen ran.** `sync.py`, `netcheck`, the `cherrymountain` app, the
  FUXA builder, the PLC ZIP builder, and the federates were not run on a
  stamped site. Reading them found single-plant assumptions in each
  ([PLANS.md](../../docs/multisite/PLANS.md) section 6).
- **The brewpub and the cellar slice are node lists.** No plantgen variant
  exists for either behind a site firewall.
