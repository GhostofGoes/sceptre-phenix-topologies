# Phase 0 spike kit

Runnable checks for the Phase 0 spikes in [DESIGN.md §18](../DESIGN.md). The
fuxa, otsim-noble and bacnet kits were run in Docker containers on
a no-KVM host. The VM spikes (malcolm, vyos-ipsec, autotunnel, serial, bbmd)
run in CI on GitHub-hosted runners with KVM (the Malcolm and the netns BBMD jobs need Docker only, and the BBMD jobs run FUXA in Docker): workflow
[`cherrymountain-spikes.yml`](../../.github/workflows/cherrymountain-spikes.yml),
one job per spike, with the phenix and minimega setup of the phenix fork's E2E
tests, images built by `ci/build_image.sh`, and evidence uploaded as
`evidence-<spike>` artifacts (run URLs are in each README's Result). Each
spike can also be run by hand on your own phenix/minimega host. Every
remaining guess about an upstream API is marked `TODO(verify):`.

Each spike writes a go/no-go decision into the **Result** section of its own
README. Every spike has its decision, and the phases they unblocked are built
([DESIGN.md §18](../DESIGN.md)). A kit stays here while it is the only runnable
proof of its answer or the spike workflow still runs it; the others are under
[Retired kits](#retired-kits).

| Spike | Question | Unblocks | Est. time | Status |
|---|---|---|---|---|
| [fuxa](fuxa/README.md) | Do offline plugins load, and does the secure sign-in, password, `/api/project` flow work? Do FUXA OPC UA and S7 clients talk to the v4 plugins? | FUXA image and boot script (§12.2) | 3 h | partial |
| [otsim-noble](otsim-noble/README.md) | Does ot-sim run on noble: Python HELICS federate and C++ DNP3 master/outstation? | `cm-otsim` base image or focal fallback for DNP3 nodes (§12.3) | 3-4 h | pass |
| [bacnet](bacnet/README.md) | Can a BACnet device answer ReadProperty on device 3003 and be read by FUXA `node-bacnet`? | BACnet module scope or bennu `hvac` fallback (§8.1, §15.2) | 4-6 h | pass |
| [malcolm](malcolm/README.md) | Does Malcolm fit on the host, does it capture through the mirror app's `setupOVS`, and does a pcap in `pcap/upload` reach OpenSearch as Zeek logs and Suricata alerts? | Malcolm profile and `lite` variant (§14) | 3-4 h | pass (Zeek and Suricata fit; the upload hand-off works with three setup fixes; mirror needs two fixes) |
| [vyos-ipsec](vyos-ipsec/README.md) | Does phenix's `vyatta.tmpl` IPsec block commit on VyOS 1.5, and does DNP3 cross the tunnel? | Patch phenix or push IPsec via cc (§10.3, §19) | 2-3 h | pass (as is; rolling build) |
| [autotunnel](autotunnel/README.md) | Can the host reach white-cell services through `phenix/startup-autotunnel` with the unique port plan, including for an experiment started by the phenix CLI? | Annotations in §15.3 | 1-2 h | pass (host ports are random; a CLI-started experiment gets its tunnels) |
| [serial](serial/README.md) (stretch) | Does ot-sim Modbus RTU work over the phenix serial app (and over phenix PR 130's QEMU-socket serial app)? | `serial` app scope (§15.2) | 2-3 h | pass (ot-sim's own RTU works on the current app; PR 130 works until a VM is redeployed) |
| [bbmd](bbmd/README.md) | Do BACpypes3 BBMDs let a real FUXA's broadcast Who-Is find BAS device 3003 across the VyOS OT firewall, and is the BAS-to-OPS BBMD rule needed? | BACnet conduit rules and BBMD nodes (§10.3, §11) | 2-3 h | pass (real FUXA reads 3003; rule 3 only for unsolicited I-Ams; `broadcastAddress` removes the BBMDs) |
| [thehive](thehive/README.md) | Should the SOC add a case-management tool next to Wazuh and Malcolm, and is it TheHive? | A fourth SOC VM and the Wazuh-to-cases path (§11, §14, §20) | 1 day | no to TheHive (license fails §2/§20); conditional go for DFIR-IRIS 2.4 as optional `soc-ir` in `full`, deferred to future work (§18) |

## Round 2 (real FUXA, broadcast I-Am, Malcolm hand-off, CLI autotunnel, ot-sim RTU)

| Gap | Result | Runs |
|---|---|---|
| Real FUXA through the BBMDs | go: FUXA 1.3.5 reads 3003 behind the stateful ACL on VyOS and Linux routers; FUXA's Who-Is follows its `broadcastAddress` property | [42540790499](https://github.com/GhostofGoes/sceptre-phenix-topologies/actions/runs/42540790499) (VyOS), [42543352420](https://github.com/GhostofGoes/sceptre-phenix-topologies/actions/runs/42543352420) (netns) |
| Rule 3 (broadcast I-Am) | needed only for unsolicited I-Ams (hit 2 to 5 packets); a Who-Is answer passes on the established rule | same runs |
| Malcolm hand-off | fixed: missing `zeek/custom/__load__.zeek`, hidden logs, ZeroMQ subscribe race, sid clash; Zeek and Suricata reach OpenSearch in 142 s, 8.4 GiB of containers | [42547389340](https://github.com/GhostofGoes/sceptre-phenix-topologies/actions/runs/42547389340) |
| CLI-started autotunnel | go: tunnels appear and survive a `phenix ui` restart | [42540790499](https://github.com/GhostofGoes/sceptre-phenix-topologies/actions/runs/42540790499) |
| ot-sim RTU over serial | go: ot-sim supports RTU natively; 0 errors in about 680 polls; PR 130's serial app also carries it but its link does not survive a VM redeploy | [42543352420](https://github.com/GhostofGoes/sceptre-phenix-topologies/actions/runs/42543352420), [42545422322](https://github.com/GhostofGoes/sceptre-phenix-topologies/actions/runs/42545422322), [42547389340](https://github.com/GhostofGoes/sceptre-phenix-topologies/actions/runs/42547389340) |

The workflow's `serial-pr130` job (spike name `serial-pr130`) applies
`serial/pr130-serial.patch` to the fork's phenix; it is part of the default set.

## Retired kits

These kits were removed once the range's own code and tests replaced them. Their
decisions and measurements are in DESIGN.md, and the kits themselves are in the
history of this repository (last present in commit `9cf6dea`).

| Spike | Decision | Replaced by |
|---|---|---|
| openplc-v4 | Go for OpenPLC v4: unattended provisioning works, the plugin configs travel in the program ZIP, and the runtime fits the RAM budget (DESIGN.md §12.1) | `plc/common/` and `plc/plc-cellar/` build the ZIPs, `phenix-injects/openplc/cm-openplc-provision.sh` provisions a PLC, and `tests/integration/` runs a real runtime in Docker with the spike's provisioning script (`openplc_provision.sh`) |
| viz3d | Go for a 3D white-cell view: three.js renders with no GPU when Chromium opts in, and the federates can drive it (DESIGN.md §15.10) | `viz/` (`cm-viz`), with its own tests and a headless browser check |

## Conventions

- Scripts are bash (`set -euo pipefail`) or Python 3 and take hosts and
  credentials from environment variables, with defaults noted in each
  README.
- Topology and scenario YAML in the spike directories are minimal test
  fixtures, not the production files. Image names are placeholders; set them
  to images you have built (`TODO(verify):` markers say where).
- Attach evidence (command output, screenshots, pcap names) in each README's
  Result section.
