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

## Result, round 2: the upload hand-off and Suricata (full stack on the runner)

- **Decision:** **go.** A pcap dropped in `pcap/upload` the documented way now
  yields Zeek logs (modbus, dnp3, bacnet and the rest) **and Suricata alerts** in
  OpenSearch with nothing fed by hand, in 142 s on a 4 vCPU / 16 GB runner. The
  hand-off was never broken in Malcolm; the setup was. Suricata fits: it added
  917 MiB (peak) and one core to the trimmed stack.
- **Why the 12-minute run had no Zeek logs** (found by running the pcap-monitor
  and zeek images with the compose file's own environment and bind mounts, a
  pcap in `pcap/upload`, and the processor's logging turned up):
  1. **`zeek -r pcap local` died.** `local.zeek` loads
     `site/custom`, and the compose file bind-mounts the checkout's
     `./zeek/custom` (read-only) over the image's copy. In a checkout that
     directory holds only a `.gitignore`, so the image's empty `__load__.zeek` is
     hidden: Zeek prints `fatal error in .../local.zeek, line 209: can't open
     .../custom/__load__.zeek`, exits 1 and writes no logs. `pcap_zeek_processor`
     reports it as "generated no log files" at warning level. Fix: `touch
     zeek/custom/__load__.zeek` before starting (the spike does). Direct
     `zeek-offline` runs in the earlier spikes passed because they did not mount
     that directory.
  2. **Nothing was visible.** `PCAP_PIPELINE_VERBOSITY` is set empty in
     `config/upload-common.env`, which the pcap-monitor, zeek and suricata
     services read after `process.env`; the spike's earlier `-vv` in
     `process.env` was overridden. Put it in `upload-common.env`; the flag and the
     variable both count, so `-vv` is DEBUG. With it, `docker logs` shows every
     file (`👓`, `📩`, `📫`, `📨`, `🔎`, `✅`).
  3. **Suricata alerts: two traps.** (a) pcap-monitor publishes a pcap **once** on
     a ZeroMQ PUB socket (port 30441, no replay), so a processor that subscribes
     afterwards never sees the file. Suricata's processor sleeps 45 s and loads
     the rules twice, and in one run it subscribed 8 s after the publication
     (Zeek and Arkime had been subscribed for 50 s): Suricata read nothing. Drop
     the pcap only when all processors log `subscribed to topic at 30441`, or
     restart pcap-monitor afterwards (it re-publishes what is in `pcap/processed`
     unless `PCAP_PIPELINE_IGNORE_PREEXISTING=true`); the spike now waits (117 s
     after the services started). (b) Custom rules must not use sids 1000001 and
     up: Malcolm's default OT rules (ELITEWOLF) use them and Suricata rejected all
     three custom rules as `Duplicate signature`.
- **Evidence.** Four runs of job "Spike malcolm (Docker on the runner)", artifact
  `evidence-malcolm-docker` (Malcolm v26.10.0, Suricata 8.0.7, OpenSearch heap
  3 GB, Logstash heap 2 GB, services OpenSearch, dashboards-helper, Dashboards,
  Arkime, Logstash, Filebeat, pcap-monitor, Zeek, Suricata):
  - [42540790499](https://github.com/GhostofGoes/sceptre-phenix-topologies/actions/runs/42540790499): `touch __load__.zeek` and `-vv` in place. Zeek modbus 6,
    dnp3 2, bacnet 4 (and conn, known_*) in OpenSearch 191 s after the drop, no
    Suricata event.
  - [42543352420](https://github.com/GhostofGoes/sceptre-phenix-topologies/actions/runs/42543352420): Zeek in 142 s; Suricata loaded the rules but
    rejected them (`Duplicate signature`, sid clash).
  - [42545422322](https://github.com/GhostofGoes/sceptre-phenix-topologies/actions/runs/42545422322): rules loaded (`53387 rules successfully loaded, 0
    failed`); Suricata subscribed after the publication and saw no pcap.
  - [42547389340](https://github.com/GhostofGoes/sceptre-phenix-topologies/actions/runs/42547389340): **all PASS.** Zeek records in 142 s after the drop
    (`event.provider` zeek 34, arkime 4, suricata 12). Suricata alerts: "CM spike
    TCP to Modbus port" 10, "CM spike Modbus write single register" 1, "CM spike
    DNP3 direct operate" 1 (`eve.json` 12 alerts, 53 387 rules loaded, the
    ELITEWOLF and ET rules among them; none of the default rules matched the
    synthetic traffic).
- **Resource fit** (run 42547389340): containers peaked at **8.4 GiB** summed
  (OpenSearch 4252 MiB, Logstash 2611, Suricata 917, Zeek 727, Arkime 483,
  Dashboards 448, Filebeat 114, pcap-monitor 80, dashboards-helper 43; CPU peaks:
  OpenSearch 284%, Logstash 376%, Suricata 106%, Zeek 95%) and the host used
  **9.2 GiB of 15.98 GB**, on 4 vCPU. The images are 8.6 GB plus Suricata's.
  Not run: NetBox with Postgres and Redis, Strelka, nginx and the upload
  containers, and the default heaps (OpenSearch 10 GB, Logstash 3 GB).
- **Follow-ups:**
  - DESIGN §14 and §11: the "not yet verified" and "cause unknown" statements are
    resolved, the spike's "archive the Zeek logs for Filebeat" workaround is not
    needed, and Suricata is measured (917 MiB peak, one core) with the trimmed
    stack; the `cm-malcolm` boot script must create `zeek/custom/__load__.zeek`,
    set `PCAP_PIPELINE_VERBOSITY` in `upload-common.env`, and either start the
    processors before the first upload or restart pcap-monitor afterwards.
  - Custom Suricata rules for the range: sids above 9000000, never 1000001 and up.
  - Still open from round 1: the real `cm-malcolm` VM with live capture.


### Round 1 result (below, unchanged)

## Result (CI: Zeek in Docker, and the mirror app into a stand-in capture VM)

Two CI jobs of `.github/workflows/cherrymountain-spikes.yml`: **Spike malcolm
(Docker on the runner)** (`docker_fit.sh`) and **Spike malcolm (mirror
capture)** (`ci_mirror.py`).

- **Decision:**
  - Parsing and resource fit: **go.** Malcolm 26.10.0's own Zeek image (Zeek
    9.0.1 with the ICSNPP packages) logs Modbus/TCP, DNP3 and BACnet/IP from
    the test pcap. That one container is all the pass criteria need, and
    its peak on a bulk pcap is **281 MiB of memory and 118% of one core** (run [42534059315](https://github.com/GhostofGoes/sceptre-phenix-topologies/actions/runs/42534059315): 52 MB, 20000 copies of the test traffic, processed in 32 s; `docker stats` showed 200 MiB). The container image is 1.4 GB.
  - Capture through `setupOVS`: **go with two fixes.** (1) A flow in the
    capture VM that sends everything from the GRE port to the capture port;
    (2) the host's GRE tunnel needs `options:df_default=false`. Without
    them the capture misses frames. Details below.
- **Which services CI runs, and what it drops.** CI runs only `zeek`
  (Malcolm's image, `zeek-offline`) and asserts on the Zeek log files. Not
  run: OpenSearch, Logstash, Filebeat, Dashboards and its helper, Arkime, the
  pcap-monitor, Suricata, NetBox with Postgres and Redis, file scanning
  (Strelka), htadmin, the upload and API containers and nginx. None is needed
  to see the Zeek logs. `FULL_STACK=1 ./docker_fit.sh` still starts the
  trimmed pipeline (OpenSearch, Logstash, Filebeat, pcap-monitor, Zeek,
  Arkime, Dashboards, dashboards-helper) for anyone who wants it.
- **What the full deployment adds** (estimate, not measured here): Malcolm's
  documented requirement is a minimum of 8 CPU cores and 24 GB of RAM, with
  16+ cores and 32+ GB recommended; its installer warns below 24 GB with a local
  OpenSearch. In an earlier CI run of the trimmed pipeline above (run
  [42520793788](https://github.com/GhostofGoes/sceptre-phenix-topologies/actions/runs/42520793788), 3 GB OpenSearch heap, 2 GB Logstash heap) the
  containers peaked at 7.6 GiB (OpenSearch 4.1, Logstash 2.6, Dashboards 0.5,
  Arkime 0.25, Zeek 0.19, Filebeat 0.1, pcap-monitor 0.09) on 4 vCPU. The full
  stack adds NetBox with Postgres and Redis, Strelka, Suricata and the default
  heap sizes of the example configuration (OpenSearch 10 GB, Logstash 3 GB),
  which is why Malcolm asks for 24 GB and DESIGN §11 budgets that for the VM;
  the extra services were not measured. The 8.6 GB of images and the parsing
  worked on a 16 GB runner.
- **Resource-fit evidence for the minimal set** (run [42534059315](https://github.com/GhostofGoes/sceptre-phenix-topologies/actions/runs/42534059315), same
  job): `zeek-offline` on `ics-load.pcap` (54 660 024 bytes, 20000 repetitions
  of the three protocols' traffic, each from its own client address) took 32
  s, peaked at 281 MiB (cgroup `memory.peak`) and 118% CPU of one core on the
  4 vCPU runner, and wrote 120 000 `modbus.log`, 40 000 `dnp3.log` and
  80 000 `bacnet.log` records. The host used 1.4 GiB at its peak, with the
  runner's own processes.
- **Evidence, parsing** (run [42528455486](https://github.com/GhostofGoes/sceptre-phenix-topologies/actions/runs/42528455486), job "Spike malcolm (Docker on
  the runner)", artifact `evidence-malcolm-docker`): runner 4 vCPU, 15982 MB
  RAM (86 GB free on a 145 GB disk, not the ~14 GB assumed). `make_pcap.py`
  wrote a 2757-byte pcap with full TCP sessions of Modbus/TCP (read holding
  registers, write register, read coils), DNP3 (class 0 read, CROB direct
  operate, valid link CRCs) and BACnet/IP (Who-Is, I-Am 3003, ReadProperty and
  ack). `zeek-offline -C -r` wrote `modbus.log`, `modbus_detailed.log`,
  `dnp3.log`, `dnp3_control.log`, `dnp3_objects.log`, `bacnet.log`,
  `bacnet_discovery.log` and `bacnet_property.log` (95 lines in all). Run 8
  also showed the records in OpenSearch (`arkime_sessions3-*`, `event.dataset`:
  modbus 6, dnp3 2, bacnet 4, bacnet_discovery 2, bacnet_property 2, conn 4) once
  Filebeat unpacked the Zeek logs as an archive.
- **Evidence, mirror capture** (run [42528455486](https://github.com/GhostofGoes/sceptre-phenix-topologies/actions/runs/42528455486), job "Spike malcolm
  (mirror capture)", artifact `evidence-malcolm-mirror`; phenix `b361816`, the
  mirror app from GhostofGoes/sceptre-phenix-apps `771e8d8`; `cm-spike` guest
  as capture VM; all checks PASS):
  - The mirror app ran at post-start, the host OVS had its mirror and a GRE
    port to the capture VM, and `setupOVS` created bridge `phenix` with a
    `sniffer` GRE port in the guest.
  - **Stock `setupOVS` does not deliver every mirrored frame to the capture
    port.** `tcpdump -i phenix` saw 22 to 25 frames (the first flooded
    ones) and none of the 20 same-VLAN pings; the bridge learns the mirrored
    MACs on the `sniffer` port and then drops frames for them. Adding
    `ovs-ofctl add-flow phenix priority=1000,in_port=sniffer,actions=output:phenix`
    (and `ip link set phenix up`) fixed that.
  - **With the flow: once per direction, no duplicates.** Same-VLAN ICMP:
    20 requests and 20 replies of 20 pings; routed flow (gen-a to the router to
    gen-c): 20 and 20; 10 of 10 TCP/502 SYNs. This confirms DESIGN §14's
    claim that a routed flow is captured once per direction at the receiving
    host.
  - Mirrored frames carry an **802.1Q tag** (`vlan 101`). A pcap filter such as
    `icmp` or `tcp port 5201` does not match them without `vlan and`; Zeek and
    Malcolm handle tagged frames.
  - **Full-size frames were lost, root cause confirmed.** ICMP payloads up
    to 1400 bytes were captured (20 of 20), 1472 (a 1500-byte IP packet) and
    4000 were not (0 of 20), and a 10 Mbit/s flow of 200-byte UDP datagrams was
    captured whole (58723 of 58723), while a 100 Mbit/s iperf3 TCP run
    delivered 250 085 376 bytes and the capture held 99.43% less (the first
    1.4 MB). Changing the MTU to 9000 on the capture port, the capture VM's
    mirror NIC, its tap and the host route device changed nothing. Setting
    `ovs-vsctl set interface <gre port> options:df_default=false` on the host's
    GRE port did: 20 of 20 1472-byte pings, and the iperf3 run captured
    250 085 930 payload bytes in 177 479 frames (**0.00% loss**, tcpdump
    kernel drops 0). The mirror's GRE packets carry the don't-fragment bit, so
    any mirrored frame that does not fit in the 1500-byte underlay after the 42
    bytes of GRE are dropped silently (the GRE port reported no errors).
- **Follow-ups:**
  - Put the two capture-VM fixes in the `cm-malcolm` boot script (the flow) and
    in the mirror setup (`df_default=false` on the host's GRE port), or raise
    the underlay MTU, and say so in DESIGN §14: Without them the design's
    "Malcolm captures on the in-guest OVS port" only works for small frames.
    The mirror app (`phenix-app-mirror`) should set `df_default=false` itself.
  - Run the real `cm-malcolm` VM (Docker plus Malcolm 26.10.0 pre-pulled) and
    capture on its `phenix` port with `zeek-live`; this spike used a
    stand-in guest and Zeek from files.
  - The native hand-off pcap-monitor to Zeek offline produced no Zeek logs
    within 12 minutes in the full pipeline runs, although Arkime indexed the
    pcap (4 sessions) and pcap-monitor moved the file; the cause is unknown
    (the processor's log output never reached `docker logs`). Check this before
    relying on pcap uploads in the range.
  - Install notes for the image: Malcolm's scripts refuse to run as root and
    need `ruamel.yaml`, `python-dotenv` and `kubernetes` in their Python; the
    OpenSearch keystore must be created by hand when starting a subset of
    services; `config/process.env` has no trailing newline; Logstash waits for
    an index template that dashboards-helper creates only after Dashboards and
    Arkime are up.
