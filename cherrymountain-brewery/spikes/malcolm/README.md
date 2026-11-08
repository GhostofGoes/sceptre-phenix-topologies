# Spike: Malcolm resource fit and mirror capture

**Question.** (a) Does Malcolm fit on the host next to the rest of the range,
and with which profile? (b) Does the `mirror` app with `setupOVS: true`
deliver mirrored frames to Malcolm's capture interface, once per direction,
without duplicates? **Decides:** Malcolm profile, `lite` variant contents,
and the §14 capture design.

## Prerequisites

- CI does not need a Malcolm VM image: the resource-fit half runs Malcolm in
  Docker on the runner (`docker_fit.sh`) and the capture half uses the
  `cm-spike` guest as a stand-in for the capture VM (`ci_mirror.py`). A real
  VM run is still needed for the Phase 5 image.
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

## Result (CI, Docker plus a stand-in capture VM)

Two CI jobs: **Spike malcolm (Docker on the runner)** (`docker_fit.sh`, resource
fit and Zeek/ICSNPP parsing) and **Spike malcolm (mirror capture)**
(`ci_mirror.py`, the mirror app with `setupOVS`). Workflow
`.github/workflows/cherrymountain-spikes.yml`.

- **Decision:**
  - Resource fit: **go.** A trimmed Malcolm stack (OpenSearch, Logstash,
    Filebeat, pcap-monitor, Zeek offline, Arkime, Dashboards, dashboards-helper)
    peaked at **7.6 GiB** of container memory (host used peak 8.4 GiB) on a
    4 vCPU, 16 GB GitHub runner with OpenSearch at a 3 GB heap and Logstash at
    2 GB. That is far under the 24 GB VM in DESIGN §11; a `lite` variant is
    not needed for memory, only the heap settings.
  - Parsing: **go.** Malcolm 26.10.0's Zeek (9.0.1) with the ICSNPP packages
    logs Modbus/TCP, DNP3 and BACnet/IP from the test pcap, and the records
    reach OpenSearch.
  - Capture through `setupOVS`: **go with a fix.** The stock bridge and GRE
    port do not deliver all mirrored frames to a capture port. See the mirror
    section below.
- **Malcolm release and size:** `v26.10.0`
  (`36bb3a61217b8555bb9fe8918acd3b45927a5458`, images
  `ghcr.io/idaholab/malcolm/*:26.10.0`), 8 images, 8.6 GB on disk.
- **Evidence** (run [42520793788](https://github.com/GhostofGoes/sceptre-phenix-topologies/actions/runs/42520793788), job "Spike malcolm (Docker on the
  runner)", artifact `evidence-malcolm-docker`):
  - Runner: 4 vCPU, 15982 MB RAM. The runner had **86 GB free on a 145 GB
    root disk before cleanup**, not the ~14 GB assumed in the plan; the script
    deletes the preinstalled toolchains anyway (118 GB free after).
  - `make_pcap.py` (scapy 2.6.1) wrote a 2757-byte pcap with full TCP sessions
    of Modbus/TCP (read holding registers, write register, read coils), DNP3
    (class 0 read, CROB direct operate, valid link CRCs) and BACnet/IP (Who-Is,
    I-Am 3003, ReadProperty and its ack).
  - Zeek alone (Malcolm's image, `zeek-offline -C -r`): `modbus.log`,
    `modbus_detailed.log`, `dnp3.log`, `dnp3_control.log`, `dnp3_objects.log`,
    `bacnet.log`, `bacnet_discovery.log`, `bacnet_property.log` all written.
  - In OpenSearch (`arkime_sessions3-*`, `event.dataset`): modbus 6,
    modbus_detailed 3, known_modbus 2, dnp3 2, dnp3_control 2, dnp3_objects 2,
    bacnet 4, bacnet_discovery 2, bacnet_property 2, conn 4, plus 4 Arkime
    `session` documents.
  - `docker stats` peak per container (MiB): opensearch 4060, logstash 2597,
    dashboards 485, arkime 254, zeek 191, filebeat 100, pcap-monitor 94,
    dashboards-helper 41. OpenSearch reached a healthy cluster about a minute
    after start; the first Logstash start waits for the index templates that
    dashboards-helper creates after Dashboards and Arkime are up (about 3
    minutes after the services started in run 5).
  - Not met: **the native hand-off pcap-monitor to Zeek offline never produced
    Zeek logs within 12 minutes**, though pcap-monitor moved the file to
    `pcap/processed` and Arkime indexed it (4 sessions). The processor's log
    output never reached `docker logs`, so the cause is not known. The check
    passed through the fallback in the script: the Zeek logs from the direct
    run were packed as `zeek-logs/upload/cherrymountain-ics.tar.gz`, which
    Filebeat unpacked, and Logstash indexed them (`fallback_feed_used` fact).
    Plain loose log files in `zeek-logs/upload/` are ignored; Filebeat takes
    archives.
- **Follow-ups:**
  - Find why the Zeek offline processor did not pick up the pcap (suspects:
    its ZeroMQ subscription to pcap-monitor, or the start order of the
    containers) before relying on `pcap` uploads in the range; live capture
    on the mirror NIC uses `zeek-live` and was not exercised.
  - Not run: Suricata, Malcolm's NetBox and file scanning (disabled), and a
    real VM image.
  - Install notes for the `cm-malcolm` image: Malcolm's scripts refuse to run
    as root, need `ruamel.yaml`, `python-dotenv`, `kubernetes` in the Python
    used, need the OpenSearch keystore created by hand when starting a subset of
    services, and `config/process.env` has no trailing newline.
