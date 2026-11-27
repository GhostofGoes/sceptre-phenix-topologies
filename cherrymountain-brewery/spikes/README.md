# Phase 0 spike kit

This directory has runnable checks for the Phase 0 spikes in
[DESIGN.md §18](../docs/DESIGN.md). A spike is a small test that answers one
design question. A kit is the set of files that runs one spike. The fuxa,
otsim-noble, and bacnet kits ran in Docker containers on a host with no KVM.

The VM spikes (malcolm, vyos-ipsec, autotunnel, serial, bbmd) run in CI on
GitHub-hosted runners with KVM. The Malcolm job and the netns BBMD jobs need
Docker only, and the BBMD jobs run FUXA in Docker. The workflow is
[`cherrymountain-spikes.yml`](../../.github/workflows/cherrymountain-spikes.yml).
It has one job for each spike. Each job uses the phenix and minimega setup of
the E2E tests of the phenix fork.

`ci/build_image.sh` builds the images. Each job uploads its evidence as an
`evidence-<spike>` artifact. The Result section of each README has the run
URLs. You can also run each spike by hand on your own phenix/minimega host.
The marker `TODO(verify):` shows every remaining guess about an upstream API.

Each spike writes a go/no-go decision into the Result section of its own
README. Every spike has its decision, and the project built the phases that
the spikes unblocked ([DESIGN.md §18](../docs/DESIGN.md)). A kit stays here
while it is the only runnable proof of its answer, or while the spike workflow
still runs it. The other kits are under [Retired kits](#retired-kits).

| Spike | Question | Unblocks | Est. time | Status |
|---|---|---|---|---|
| [fuxa](fuxa/README.md) | Do offline plugins load, and does the secure sign-in, password, `/api/project` flow work? Do FUXA OPC UA and S7 clients talk to the v4 plugins? | FUXA image and boot script (§12.2) | 3 h | partial |
| [otsim-noble](otsim-noble/README.md) | Does ot-sim run on noble: Python HELICS federate and C++ DNP3 master/outstation? | `cm-otsim` base image or focal fallback for DNP3 nodes (§12.3) | 3-4 h | pass |
| [bacnet](bacnet/README.md) | Can a BACnet device answer ReadProperty on device 3003, and can FUXA `node-bacnet` read it? | BACnet module scope or bennu `hvac` fallback (§8.1, §15.2) | 4-6 h | pass |
| [malcolm](malcolm/README.md) | Does Malcolm fit on the host, and does it capture through the mirror app's `setupOVS`? Does a pcap in `pcap/upload` reach OpenSearch as Zeek logs and Suricata alerts? | Malcolm profile and `lite` variant (§14) | 3-4 h | pass (Zeek and Suricata fit, the upload hand-off works with four setup fixes, and the mirror needs two fixes) |
| [vyos-ipsec](vyos-ipsec/README.md) | Does phenix's `vyatta.tmpl` IPsec block commit on VyOS 1.5, and does DNP3 cross the tunnel? | Patch phenix or push IPsec through cc (§10.3, §19) | 2-3 h | pass (as is, on a rolling build) |
| [autotunnel](autotunnel/README.md) | Can the host reach white-cell services through `phenix/startup-autotunnel` with the unique port plan? Does this also work for an experiment that the phenix CLI started? | Annotations in §15.3 | 1-2 h | pass (host ports are random, and a CLI-started experiment gets its tunnels) |
| [serial](serial/README.md) (stretch) | Does ot-sim Modbus RTU work over the phenix serial app (and over phenix PR 130's QEMU-socket serial app)? | `serial` app scope (§15.2) | 2-3 h | pass (ot-sim's own RTU works on the current app, and PR 130 works until you redeploy a VM) |
| [bbmd](bbmd/README.md) | Do BACpypes3 BBMDs let a real FUXA's broadcast Who-Is find BAS device 3003 across the VyOS OT firewall? Does the design need the BAS-to-OPS BBMD rule? | BACnet conduit rules and BBMD nodes (§10.3, §11) | 2-3 h | pass (real FUXA reads 3003, rule 3 is only for unsolicited I-Ams, and `broadcastAddress` removes the BBMDs) |
| [thehive](thehive/README.md) | Does the SOC need a case-management tool next to Wazuh and Malcolm, and is it TheHive? | A fourth SOC VM and the Wazuh-to-cases path (§11, §14, §20) | 1 day | no to TheHive (its license fails §2/§20). Conditional go for DFIR-IRIS 2.4 as optional `soc-ir` in `full`, deferred to future work (§18) |

## Round 2 (real FUXA, broadcast I-Am, Malcolm hand-off, CLI autotunnel, ot-sim RTU)

| Gap | Result | Runs |
|---|---|---|
| Real FUXA through the BBMDs | go: FUXA 1.3.5 reads 3003 behind the stateful ACL on VyOS and Linux routers. FUXA's Who-Is follows its `broadcastAddress` property | [42540790499](https://github.com/GhostofGoes/sceptre-phenix-topologies/actions/runs/42540790499) (VyOS), [42543352420](https://github.com/GhostofGoes/sceptre-phenix-topologies/actions/runs/42543352420) (netns) |
| Rule 3 (broadcast I-Am) | needed only for unsolicited I-Ams (2 to 5 packets hit the rule). A Who-Is answer passes on the established rule | same runs |
| Malcolm hand-off | fixed: missing `zeek/custom/__load__.zeek`, hidden logs, ZeroMQ subscribe race, sid clash. Zeek and Suricata reach OpenSearch in 142 s, with 8.4 GiB of containers | [42547389340](https://github.com/GhostofGoes/sceptre-phenix-topologies/actions/runs/42547389340) |
| CLI-started autotunnel | go: tunnels appear and survive a `phenix ui` restart | [42540790499](https://github.com/GhostofGoes/sceptre-phenix-topologies/actions/runs/42540790499) |
| ot-sim RTU over serial | go: ot-sim supports RTU natively, with 0 errors in about 680 polls. PR 130's serial app also carries it, but its link does not survive a VM redeploy | [42543352420](https://github.com/GhostofGoes/sceptre-phenix-topologies/actions/runs/42543352420), [42545422322](https://github.com/GhostofGoes/sceptre-phenix-topologies/actions/runs/42545422322), [42547389340](https://github.com/GhostofGoes/sceptre-phenix-topologies/actions/runs/42547389340) |

The workflow's `serial-pr130` job (spike name `serial-pr130`) applies
`serial/pr130-serial.patch` to the fork's phenix. The job is part of the default
set.

## Multi-site spikes

These kits are not Phase 0 spikes. They support the options of the plan for a
variant with four breweries and a head office
([docs/multisite/PLANS.md](../docs/multisite/PLANS.md)). A variant is one
version of the range. The first, the third, and the fourth kit ran in a
container with no VM. The second is a job of the spike workflow
(`wan-bgp-vyos`, once for each kind of tunnel). It booted VyOS routers under
phenix and minimega.

Each kit keeps the summary of its last run in its own `evidence/` directory.

| Spike | Question | Unblocks | Status |
|---|---|---|---|
| [wan-bgp](wan-bgp/README.md) | Can four sites keep a VPN to their head office over a mock internet of BGP networks when an uplink or a whole ISP fails? | The WAN of the multi-site variant, and how a site ties a tunnel to an uplink | pass (namespaces with FRR and wireguard-go, and 28 of 28 checks) |
| [wan-bgp on VyOS](wan-bgp/README.md#the-same-wan-on-vyos-routers) | Does a VyOS router take that design from phenix at first boot, and behave as the lab did, with WireGuard and with route-based IPsec? | Where the tunnels and BGP run at a site, how they get their configuration, and which tunnel | pass in CI (four VyOS routers, and 30 of 30 checks for each tunnel after one fix to the design) |
| [multisite-stamp](multisite-stamp/README.md) | Is it possible to stamp out the plant as four sites in one experiment? What does each site mix cost, and how does it sit on a cluster? | How to generate and size the multi-site variant, and how to place it on nodes | pass (plantgen needs one changed line, and no VM ran) |
| [site-profiles](site-profiles/README.md) | Can the generator make four sites that differ in size and in how their networks are cut? Do they fit in one experiment, and what do they cost? | What each site is, how to describe a site to the generator, and where each site runs | pass (all four generate after three kinds of edit to the plant file, and no VM ran) |
| [site-brokers](site-brokers/README.md) | Can each site run its own HELICS federation, with its own broker and the same federate and topic names? | How the fleet runs its simulations, and what the app must change to start a broker at each site | pass (go, on two conditions: the network must keep the sites apart, and a site restarts as a whole. No VM ran) |

`wan-bgp/patches/` has a change to phenix that adds a `config` list of VyOS
commands to the `vrouter` app. The change is two patches. One patch is for
sceptre-phenix `main` at `86fd732`, and the other is for the fork's
`ci-e2e-minimega` at `afbcba7`. Each patch passes `go test ./app ./tmpl` on its
branch. The `wan-bgp-vyos` job applies the second patch before it builds
phenix. The first patch is the draft pull request
[GhostofGoes/sceptre-phenix#27](https://github.com/GhostofGoes/sceptre-phenix/pull/27).

## Retired kits

The project removed these kits when the range's own code and tests replaced
them. Their decisions and measurements are in DESIGN.md. The kits themselves
are in the history of this repository (last present in commit `9cf6dea`).

| Spike | Decision | Replaced by |
|---|---|---|
| openplc-v4 | Go for OpenPLC v4: unattended provisioning works, the plugin configurations travel in the program ZIP, and the runtime fits the RAM budget (DESIGN.md §12.1) | `plc/common/` and `plc/plc-cellar/` build the ZIPs. `phenix-injects/openplc/cm-openplc-provision.sh` provisions a PLC. `tests/integration/` runs a real runtime in Docker with the spike's provisioning script (`openplc_provision.sh`) |
| viz3d | Go for a 3D white-cell view: three.js renders with no GPU when Chromium opts in, and the federates can drive it (DESIGN.md §15.10) | `viz/` (`cm-viz`), with its own tests and a headless browser check |

## Conventions

- Scripts are bash (`set -euo pipefail`) or Python 3. They take hosts and
  credentials from environment variables. Each README gives the defaults.
- The topology and scenario YAML files in the spike directories are minimal
  test fixtures, not the production files. Image names are placeholders. Set
  them to images that you built. The `TODO(verify):` markers show where.
- Attach evidence (command output, screenshots, pcap names) in the Result
  section of each README.
