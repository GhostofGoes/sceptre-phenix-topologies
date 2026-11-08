# Spike: Malcolm resource fit and mirror capture

**Question.** (a) Does Malcolm fit on the host next to the rest of the range,
and with which profile? (b) Does the `mirror` app with `setupOVS: true`
deliver mirrored frames to Malcolm's capture interface, once per direction,
without duplicates? **Decides:** Malcolm profile, `lite` variant contents,
and the §14 capture design.

## Prerequisites

- A Malcolm VM image (ISO-installed or built; record the release) with
  openvswitch in the guest and a second NIC for capture.
- phenix with the user `mirror` app (Go) available. The fixture mirrors the
  schema from the repo's wind plant `nids` scenario and DESIGN §14.
- Host: root on the minimega host for `resource_check.sh`.

## Steps

1. `./resource_check.sh` with the current free memory, and again with the
   full topology running (record the numbers).
2. Apply `topology.yml` + `scenario.yml` as an experiment (adjust images):
   `phenix config create topology.yml scenario.yml`, then `phenix exp create
   -t spike-malcolm -s spike-malcolm spike-malcolm` and `phenix exp start`.
3. Boot Malcolm and wait for its stack to settle; note idle RSS
   (`docker stats`, `free -m`) and boot time to first dashboard.
4. On malcolm: `./capture_check.sh ovs` to see the OVS bridge and mirror port.
5. On gen-a: `./capture_check.sh gen`. On malcolm: `./capture_check.sh seen`.
6. In Malcolm, find the generated flows (Arkime and Dashboards). Check the
   count: gen-a to gen-b ICMP should appear once per direction.
7. Add a VyOS router between two VLANs (add the second VLAN to `vlans:`),
   repeat 5-6 for a routed flow and confirm it is captured once per
   direction at the receiving side (DESIGN §14 claim).
8. Under load: run `iperf3` between gen-a and gen-b for 60 s; record dropped
   packets on malcolm's capture interface (`ip -s link`).

## Pass / fail

- [ ] Host `FIT: yes` with Malcolm at its chosen size (24 GB or `lite`).
- [ ] Mirrored packets are visible on the in-guest capture interface.
- [ ] Zeek/Arkime shows the test flows, one record per direction, no duplicates.
- [ ] Routed flow captured once per direction.
- [ ] Capture loss under the 60 s iperf run is under 1%.

Fit fails but capture works: **go with `lite`**, list what is dropped.
Capture fails: investigate the mirror app config (`TODO(verify):` bridge
naming in step 4) before calling no-go; the fallback is a tap-based capture
VM per VLAN.

## Result

- **Decision:** go (full) / go (lite) / no-go
- **Malcolm release and size:**
- **Evidence:** (resource_check output, OVS output, Arkime counts)
- **Follow-ups:**
