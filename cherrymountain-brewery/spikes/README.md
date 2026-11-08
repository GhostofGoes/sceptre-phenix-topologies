# Phase 0 spike kit

Runnable checks for the Phase 0 spikes in [DESIGN.md §18](../DESIGN.md). The
openplc-v4, fuxa, otsim-noble and bacnet kits were run in Docker containers on
a no-KVM host. The VM spikes (malcolm, vyos-ipsec, autotunnel, serial, bbmd)
run in CI on GitHub-hosted runners with KVM: workflow
[`cherrymountain-spikes.yml`](../../.github/workflows/cherrymountain-spikes.yml),
one job per spike, with the phenix and minimega setup of the phenix fork's E2E
tests, images built by `ci/build_image.sh`, and evidence uploaded as
`evidence-<spike>` artifacts (run URLs are in each README's Result). Each
spike can also be run by hand on your own phenix/minimega host. Every
remaining guess about an upstream API is marked `TODO(verify):`.

Each spike writes a go/no-go decision into the **Result** section of its own
README. Phase 1 should not start until the "blocking" spikes have a decision.

| Spike | Question | Unblocks | Est. time | Status |
|---|---|---|---|---|
| [openplc-v4](openplc-v4/README.md) | Can OpenPLC v4 be provisioned unattended (ZIP upload, plugin configs, OPC UA rebind) within the RAM budget? | v4 vs v3 decision (§12.1) | 3-4 h | partial |
| [fuxa](fuxa/README.md) | Do offline plugins load, and does the secure sign-in, password, `/api/project` flow work? Do FUXA OPC UA and S7 clients talk to the v4 plugins? | FUXA image and boot script (§12.2) | 3 h | partial |
| [otsim-noble](otsim-noble/README.md) | Does ot-sim run on noble: Python HELICS federate and C++ DNP3 master/outstation? | `cm-otsim` base image or focal fallback for DNP3 nodes (§12.3) | 3-4 h | pass |
| [bacnet](bacnet/README.md) | Can a BACnet device answer ReadProperty on device 3003 and be read by FUXA `node-bacnet`? | BACnet module scope or bennu `hvac` fallback (§8.1, §15.2) | 4-6 h | pass |
| [malcolm](malcolm/README.md) | Does Malcolm fit on the host, and does it capture through the mirror app's `setupOVS`? | Malcolm profile and `lite` variant (§14) | 3-4 h | MALCOLM_STATUS |
| [vyos-ipsec](vyos-ipsec/README.md) | Does phenix's `vyatta.tmpl` IPsec block commit on VyOS 1.5, and does DNP3 cross the tunnel? | Patch phenix or push IPsec via cc (§10.3, §19) | 2-3 h | pass (as is; rolling build) |
| [autotunnel](autotunnel/README.md) | Can the host reach white-cell services through `phenix/startup-autotunnel` with the unique port plan? | Annotations in §15.3 | 1-2 h | pass (host ports are random) |
| [serial](serial/README.md) (stretch) | Does ot-sim Modbus RTU work over minimega serial? | `serial` app scope (§15.2) | 2-3 h | pass (ot-sim RTU untested) |
| [bbmd](bbmd/README.md) | Do BACpypes3 BBMDs let FUXA's broadcast Who-Is find BAS device 3003 across the VyOS OT firewall? | BACnet conduit rules and BBMD nodes (§10.3, §11) | 2-3 h | pass (more ACL rules than assumed) |

## Conventions

- Scripts are bash (`set -euo pipefail`) or Python 3 and take hosts and
  credentials from environment variables, with defaults noted in each
  README.
- Topology and scenario YAML in the spike directories are minimal test
  fixtures, not the production files. Image names are placeholders; set them
  to images you have built (`TODO(verify):` markers say where).
- Attach evidence (command output, screenshots, pcap names) in each README's
  Result section.
