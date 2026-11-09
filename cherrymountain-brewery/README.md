# CherryMountain Brewery

> **Status:** in progress; nothing has run on a VM yet. The design is complete
> and the Phase 0 spikes are done (all nine are go; DESIGN.md has the decisions).
> The **ci variant** (the cellar slice, five VMs) is built and its integration
> test passes in Docker (ot-sim, OpenPLC, FUXA). **Phase 1 (network)** is built
> for the full and lite variants and checked statically by `tools/netcheck`.
> **Phase 2 (simulation)** is largely built: `plant.yaml` covers every area,
> plantgen writes the ci, full, and lite outputs, and seven federates pass their
> tests (remaining: ot-sim logic blocks and a 24 h soak).
> **Phase 3 (control)** is in progress: all five PLC programs (with the
> hardwired CO₂ interlock and the peer reads), their full and lite ZIPs, and the
> scada-fuxa and bas-fuxa projects are generated; the FUXA batch engine and any
> run in a real OpenPLC runtime are next. **Phase 5
> (SOC and remote access)** is built (images, provisioning, detections, and
> per-experiment secrets) but not run. Every image config exists
> ([phenix-images/README.md](phenix-images/README.md)); the ot-sim BACnet module
> does not yet.

The CherryMountain Brewery topology models a notional SCADA system for a
Colorado microbrewery and taproom. Its flagship West Coast IPA is "Fire of the
Phenix". It is an all open-source OT cyber range covering:

- **Process:** brewhouse, cellar, bottling and kegging.
- **Utilities:** glycol, steam, compressed air, CO₂, water treatment,
  wastewater neutralization, and clean-in-place (CIP).
- **Plant power:** an internal power-distribution model with PV, battery
  storage, and a standby generator.
- **Building automation:** a building automation system (BAS).
- **Security:** blue-team monitoring and a vendor remote-access path.

| Role | Component |
|---|---|
| SCADA and HMI | [FUXA](https://github.com/frangoteam/FUXA) |
| PLCs | [OpenPLC](https://github.com/Autonomy-Logic/openplc-runtime) |
| Field devices | [ot-sim](https://github.com/patsec/ot-sim) |
| Physics co-simulation | [HELICS](https://github.com/GMLC-TDC/HELICS) |
| Network monitoring and SIEM | [Malcolm](https://github.com/cisagov/Malcolm) and [Wazuh](https://github.com/wazuh/wazuh) |

To run it on your own host, follow [USER_GUIDE.md](USER_GUIDE.md): the
repositories to clone, the builds, the first start of each variant, and what to
check.

See [DESIGN.md](DESIGN.md) for the full design: plant and process design,
power and building models, simulation, network architecture, phenix
integration, new software, testing, roadmap, risks, and references.

## What exists

| Path | Contents |
|---|---|
| `plant/` | `plant.yaml` tag database for the whole plant (every area, 43 hosts, 491 tags, 96 alarms) and the plantgen output under `plant/generated/{ci,full,lite}/` |
| `tools/plantgen/` | Generator: tags, addresses, the ot-sim, ntp, soh, wireguard, helics, and cherrymountain app blocks, HELICS lists, OpenPLC, FUXA, BACnet, DNP3, and SCORCH inputs ([README](tools/plantgen/README.md) documents every output) |
| `sim/` | `cherrymountain_sim`: the weather, brewhouse, cellar, utilities, packaging, power (OpenDSS), and building federates, the sim-ctl event API, and an offline harness, with tests ([README](sim/README.md)) |
| `recipes/` | `fire-of-the-phenix.json`, the flagship recipe in BeerJSON |
| `phenix-ci.yml`, `phenix-configs/{topology,scenario}-ci.yml` | Workflow, topology, and scenario for the ci variant |
| `phenix.yml`, `phenix-lite.yml`, `phenix-configs/` | Workflows for the full and lite variants; topology modules `topology-{core,soc,remote-access}.yml`, `topology-{full,lite}.yml` (they include the modules), and `scenario-{full,lite}.yml` |
| `phenix-injects/{ot-svc,bbmd,inet-nuke,vrouter,malcolm}/` | The network phase's injects: ot-svc DNS, syslog relay, and chrony hook; the `cm-bbmd` BACnet BBMD service; inet-nuke DNS; the VyOS settings script; malcolm's capture flow |
| `sync.py` | Embeds plantgen's per-variant app blocks into the scenarios and copies generated files into `phenix-injects/`; aware of Git LFS pointers. It replaces `sync_ci.py`, which now only calls it for `ci` |
| `tools/netcheck/` | Static check of the topology, scenario, and workflow against the IP plan and conduit matrix: every conduit and every default drop, with tests ([README](tools/netcheck/README.md)) |
| `phenix-injects/` | Files the ci scenario injects: federate units, OpenPLC and FUXA provisioning, generated copies |
| `phenix-images/` | Image configs for every role (noble): `cm-sim`, `cm-otsim`, `cm-openplc`, `cm-fuxa`, `cm-svc`, `cm-desktop`, `cm-ews`, `cm-historian`, `cm-guacamole`, `cm-wireguard`, `cm-malcolm`, `cm-wazuh`, with [one README](phenix-images/README.md) that has a section per image |
| `plc/plc-cellar/` | The cellar PLC programs in Structured Text (`plc-cellar.st` for ci, `plc-cellar-plant.st` for full and lite), and the ci `build_zip.sh` |
| `plc/common/` | The per-variant PLC builder: alarm logic generated from `alarms.json`, strucpp, and the ZIPs under `phenix-injects/openplc/<variant>/` ([README](plc/common/README.md)) |
| `tests/integration/` | `ci_slice_docker.sh`: the cellar slice (ot-sim, OpenPLC v4.3.2, FUXA) in Docker, no KVM |
| `tests/soc/` | Tests of the Wazuh rules, the Suricata rules and Zeek script, the vendor-path scripts, and that every script a scenario step runs is on its VM |
| `viz/` | `cherrymountain_viz`: cm-viz, the white cell's 3D view of ground truth (three.js, vendored), with tests ([README](viz/README.md)) |
| `fuxa/` | `build_project.py` and the FUXA projects it builds: scada-fuxa for ci, and scada-fuxa and bas-fuxa for full and lite (`fuxa/<variant>/`) |
| `tools/lint/` | Hook scripts for prek: systemd-analyze on units and shellcheck on shell in image YAML |
| `spikes/` | Phase 0 spike kits, each with its go/no-go result ([summary](spikes/README.md)) |
| `apps/` | The `cherrymountain` phenix app (five stages) and the `plantevent` SCORCH component, each with its own `README.md`, with tests ([README](apps/README.md)) |

## The ci variant

Five VMs, 7 vCPUs, about 8 GB: sim-ctl, sim-process, rio-cellar, plc-cellar,
and scada-fuxa. It needs `/dev/kvm` on the host. The images and the PLC
program ZIP must exist first:

1. Build the four images from `phenix-images/` (each file's header has the
   commands), then add miniccc with `phenix image inject-miniexe`.
2. The OpenPLC program ZIP is `phenix-injects/openplc/plc-cellar.zip` (Git
   LFS), built headless by `plc/plc-cellar/build_zip.sh` (see
   [plc/plc-cellar/README.md](plc/plc-cellar/README.md)). The topology injects
   that path; rebuild it after any change to the plant or the program.
3. Apply the variant with its own workflow branch, from outside the directory:

```shell
docker exec phenix phenix workflow apply -b cherrymountain-ci -c phenix-ci.yml /phenix/topologies/cherrymountain-brewery
```

Host access to FUXA is through the startup autotunnel. The host port is random
(11881 is only the annotation's label): find it with `cc tunnel list scada-fuxa`
(the row with `dst port 1881`). The `cherrymountain` app generates the range
admin password for each experiment: it is `fuxa_password` in
`<experiment directory>/cherrymountain/secrets.json`. The fixed one in
`phenix-injects/fuxa/fuxa.env` applies only where the app does not run (the
Docker integration test).

## Regenerate and test

Run these from this directory after changing `plant/plant.yaml`:

```shell
(cd tools/plantgen && python -m plantgen)          # rewrite plant/generated/*
python sync.py                                       # embed the generated blocks in the scenarios, copy generated files into phenix-injects/
python fuxa/build_project.py --all                   # rebuild the FUXA projects (ci; full and lite for scada-fuxa and bas-fuxa)
python sync.py                                       # ... and copy it (run again after build_project.py)
plc/plc-cellar/build_zip.sh                          # rebuild the ci OpenPLC program ZIP (needs Node 22+)
plc/common/build_zip.sh --all                        # rebuild the full and lite PLC ZIPs (needs Node 22+)
```

Checks (all must pass; `prek run --all-files` covers lint and hygiene):

```shell
(cd tools/plantgen && python -m plantgen --check && python -m pytest)
(cd sim && python -m pytest)                         # model validation and the offline federation
(cd apps && python -m pytest)                        # the cherrymountain app and plantevent (needs phenix-apps, see apps/README.md)
(cd viz && python -m pytest)                         # cm-viz: frames, server, and the sim-ctl source
python -m pytest tests/soc                           # SOC detections, vendor-path scripts, and the scripts scenario steps run
python sync.py --check                               # injects and scenarios match plantgen output (ci, full, lite)
python tools/netcheck/run_netcheck.py                # IP plan, conduit matrix, routes, rules (full, lite)
(cd tools/netcheck && python -m pytest)              # netcheck, sync.py, and BBMD tests
python fuxa/build_project.py --all --check           # FUXA projects are current
python plc/plc-cellar/assemble.py                    # PLC program matches alarms.json and the I/O map
plc/plc-cellar/build_zip.sh --check                  # the committed ZIP is a fresh build (needs Node 22+)
(cd tests/integration && python -m pytest)           # ot-sim hand-render equals the phenix app
```

`assemble.py --iec2c <path>` also compiles the PLC program with matiec.

The integration test runs the slice in Docker (about 10 minutes the first time, for
the image builds; see the header of the script for its environment variables):

```shell
tests/integration/ci_slice_docker.sh
```

It starts ot-sim (`rio-cellar`) with the real `cherrymountain_sim` federate and a
HELICS broker, OpenPLC v4.3.2 running `plc-cellar.zip`, and FUXA 1.3.5 provisioned
with the generated project, then asserts the values flow ot-sim to PLC to FUXA,
that setpoints written through FUXA and Modbus land in the right `%MW` words and
move the valves, that the alarm bits read correctly, and that the spunding outputs
reach ot-sim. It also uploads two deliberately different `modbus_slave.json` files
to pin down how the v4 runtime clamps and segments the holding registers.

## Lint

`prek run --all-files` (also run by CI) checks this directory with:

- **phenix schemas:** every Topology, Scenario, Workflow, and Image config is
  validated with the sceptre-phenix JSON Schemas, fetched from a commit-pinned
  URL (`prek.toml` has the commit).
- **systemd:** `systemd-analyze verify` and a hardening (exposure) limit on
  every `.service` file and on the units that image scripts write.
- **shell:** shellcheck at style level on scripts, including extensionless
  ones, and on the shell embedded in `phenix-images/*.yml`.
- **YAML:** block style for phenix YAML. Only empty `{}` and `[]` may use flow
  syntax. JSON that phenix injects verbatim is exempt.
- **Docker and desktop files:** hadolint, dclint, and `desktop-file-validate`
  run when such files exist.
- **Network plan:** `netcheck` (see [tools/netcheck](tools/netcheck/README.md)),
  its tests, and `sync.py --check` run as local hooks, so CI fails when a
  conduit loses its rule, a rule has no conduit, an address overlaps, or
  plantgen's output was not synced into the scenarios (or a program ZIP is
  stale).
- **Test suites:** the plantgen, `sim/`, `viz/`, `apps/`, and SOC pytest suites
  run as hooks too (`cm-plantgen-tests`, `cm-sim-tests`, `cm-viz-tests`,
  `cm-app-tests`, `cm-soc-tests`). The app hook installs phenix-apps from the pinned fork
  commit. The Docker integration test and `build_zip.sh --check` (Node) are
  run by hand.

`spikes/` and `tests/` are exempt from the stricter checks. The system tools
that some hooks need are listed in
[CONTRIBUTING](../.github/CONTRIBUTING.md#code-quality).

## Network (Phase 1)

The full and lite variants carry the whole network of DESIGN.md section 10:

- **Topology.** `topology-core.yml` has the 37 core nodes (the 43 of the
  design minus the SOC and remote-access modules) with their interfaces, VLANs, addresses, static
  routes, zone labels, boot-order `delay`s, autotunnel entries, and resources.
  `topology-soc.yml` adds malcolm, wazuh, and soc-analyst;
  `topology-remote-access.yml` adds dmz-vpn, dmz-jump, and vendor-ws.
  `topology-full.yml` includes all three (43 VMs, 67 vCPUs, 113 GB) and
  `topology-lite.yml` only the core (37 VMs, 48 vCPUs, 71 GB). phenix appends
  the included nodes in order, so the boot order is the core's, then the SOC,
  then the remote-access nodes. Router interfaces are named `eth0`, `eth1`, ...
  (the VyOS NIC names, because the vrouter app writes netem policies with the
  interface name); hosts use `IF0`, `IF1`, ... and `mgmt`.
- **Conduits.** The `vrouter` app in `scenario-full.yml` holds one ruleset per
  source interface on fw-edge and fw-ot (`FROM-<zone>`): the stateful accept,
  the allowed flows of the conduit matrix, and default drop. `acl.egress` is the
  app's name for the ruleset applied to packets coming in on an interface.
  fw-edge also has the SNAT, the WireGuard DNAT, and the IPsec site to util-rtr;
  isp-rtr and util-rtr have the netem on the WAN legs. `scenario-lite.yml` takes
  the vrouter app from the full scenario with `fromScenario`.
- **Services.** ot-svc has chrony (from the ntp app, limited to the plant's
  prefixes by a startup hook), a dnsmasq zone for the plant
  (`phenix-injects/ot-svc/plant.hosts`, `cherrymountain.test`), the syslog relay
  to Wazuh, and the OPS BBMD. bas-bbmd runs the BAS BBMD. Both run
  `phenix-injects/bbmd/cm_bbmd.py` (BACpypes3, BDT listing both).
- **SoH.** `testCustomReachability` has a probe for every conduit that
  answers (every TCP conduit, the BACnet reads, the BBMD BDT reads, NTP and DNS),
  and `hostCustomTests` has a cc script per blocked path that expects `DROPPED`.
  Syslog and IKE/ESP send no reply, so they are checked statically only.

Known gaps and what to check on the first VM run (each is also a comment where
it matters):

- **Not run on a VM.** `netcheck` models the rules phenix renders; it does not
  prove VyOS behavior. Check the `vyatta.tmpl` output commits with these rulesets,
  that the ACL names (`acl.egress` is the inbound filter) behave as modeled,
  `cm-bbmd.service` under systemd, and that every SoH probe passes. BBMD rule 2
  (the device's I-Am to FUXA) and the syslog flows send no reply, so they have no
  SoH probe and only `netcheck` checks them. BBMD rule 3 is optional (DESIGN 10.3).
- **The vrouter templates cannot do four things the design wants,** so
  `phenix-injects/vrouter/cm-vyos-config.sh` does them after boot, delivered by a
  topology `commands` entry (a `cc exec`): rule logging (`default-log` on every
  ruleset, and the syslog target for Wazuh), `ike-group IKE-1W key-exchange
  ikev2`, and a per-router NTP server. The script is unverified on VyOS (the
  syslog syntax is tried as `remote`, then `host`; the injected path is assumed
  to appear as `/config/scripts/custom/`).
- **phenix issues to report upstream.** The vrouter app keeps the NTP address
  of the first router that has one and uses it for every later router (so
  isp-rtr comes first in the topology, and the script fixes fw-ot's source);
  the rendered rulesets filter only `forward`, so traffic to a router's own
  address (IKE, SSH) is never filtered; rules have no `log` field; netem `loss`
  is an integer percent (the design and files use 1 percent); and
  the template has no `key-exchange`.
- **Malcolm upload pipeline.** A pcap in `pcap/upload` reaches OpenSearch only
  with four setup steps that the cm-malcolm boot script must do (DESIGN 14).
- **Mirror.** `df_default=false` on the host's GRE port still needs the mirror
  app (nothing in a topology can set it). The OpenFlow capture flow is injected
  into malcolm (`phenix-injects/malcolm/99-zz-capture-flow.sh`) until the app or
  the cm-malcolm image does it (DESIGN.md 14).
- **Images.** Every image the full topology names has a config in
  `phenix-images/` except the prebuilt `vyos`, which comes from
  sceptre-phenix-images. None has been built on a phenix host yet.
- **Generated blocks.** `ot-sim`, `cherrymountain`, `helics`, `ntp`, and
  `wireguard` come from plantgen through `sync.py`; the soh checks of plantgen
  are merged into the hand-written ones. Until plantgen emits one, its marker
  holds a placeholder comment.

## Applying the full design

The topology uses the `phenix workflow apply` directory layout
(`phenix.yml`, `phenix-configs/`, `phenix-injects/`). Build the images first
(see [phenix-images/README.md](phenix-images/README.md)); the full plant
deploys with:

```shell
docker exec phenix phenix workflow apply /phenix/topologies/cherrymountain-brewery
```

The lite variant uses its own branch, like ci:

```shell
docker exec phenix phenix workflow apply -b cherrymountain-lite -c phenix-lite.yml /phenix/topologies/cherrymountain-brewery
```

Host access to the web UIs is through the startup autotunnel (DESIGN.md
15.3): the forwards use random host ports, so look the port up with
`cc tunnel list <vm>` each time. phenix's forwards API does not report it (its
`srcPort` is the annotation label).
