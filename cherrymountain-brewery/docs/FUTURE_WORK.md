# Future work

This file lists all the work that is planned or considered for CherryMountain
Brewery and is not done. [DESIGN.md](DESIGN.md) describes the range (the
simulated brewery network for cyber exercises) as its authors designed and built
it. The [Status](DESIGN.md#status) section of DESIGN.md says how far each part
ran. Where the design mentions work that is still to do, it points to this file.
A limit that is part of the design (something that the model leaves out on
purpose) stays in DESIGN.md. You find it in the section that it belongs to and
in [§19](DESIGN.md#19-risks-and-open-questions).

- [The first run on a phenix host](#the-first-run-on-a-phenix-host)
- [Checks that need no VM](#checks-that-need-no-vm)
- [Control and safety](#control-and-safety)
- [Operator tools](#operator-tools)
- [Simulation](#simulation)
- [Network and the range's own security](#network-and-the-ranges-own-security)
- [Exercise support](#exercise-support)
- [The white cell's 3D view](#the-white-cells-3d-view)
- [Upstream](#upstream)
- [Images and builds](#images-and-builds)
- [Sources still to read](#sources-still-to-read)
- [Evaluated, not scheduled](#evaluated-not-scheduled)
- [Decided against](#decided-against)

## The first run on a phenix host

No part of the range ran on a phenix host or in a VM yet. This section lists
what that first run must show. The [Known limits](../USER_GUIDE.md#known-limits)
section of the user guide says what to look at on the first start.

Network ([§10.3](DESIGN.md#103-conduit-matrix-default-deny-stateful-logged)):

- VyOS accepts the rendered rulesets. Under SoH (the state-of-health tests of
  phenix), every conduit passes and every blocked path fails. A conduit is an
  allowed network path between two zones. `netcheck` tests the rules that phenix
  renders, not what VyOS does with them.
- The post-boot script `cm-vyos-config.sh` runs on VyOS. The authors rewrote it
  after a review showed that its first form never reached VyOS, because the
  commands are shell aliases. The new form ran only against a stand-in (a
  substitute for the real system). The first run must show that
  `/opt/vyatta/sbin/my_set` and `my_delete` take the paths, the syslog target,
  and `default-log`. They must also take the IKEv2 configuration and the IPsec
  match on the utility's DNP3 rule. With the vyconf backend, the commands are
  `vy_set` and `vy_delete`. The run must also show whether the first commit of
  fw-ot finishes inside the 10 minute wait on 1 vCPU.
- The ot-sim DNP3 traffic crosses the IPsec tunnel. The spike (a short trial of
  an idea) used a link-status stub.
- The MGMT nftables hook finds the guest's device on every MGMT member, and the
  `nft` of noble takes `-D`.
- `cm-bbmd` runs under systemd on ot-svc and bas-bbmd, and foreign-device
  registration works. The bas-fuxa instance of FUXA reads the BAS controllers by
  the directed broadcast `10.30.10.255`
  ([§8.1](DESIGN.md#81-spaces-equipment-and-bacnet-devices)).

PLCs ([§12.1](DESIGN.md#121-openplc-v4-runtime-v3-fallback)):

- The full and lite programs run in an OpenPLC runtime, and plc-utilities runs
  against the federates. A federate is one simulation program in the HELICS
  federation. The CIP sequence of plc-utilities ran with its inputs set by hand.
- What a stopped or faulted runtime does to its outputs. If the runtime drives
  them to 0, make 0 the safe state of every command. As an alternative, make the
  remote I/O hold when it loses its master. Then correct
  [§6.7](DESIGN.md#67-cellar-co2-life-safety-interlock)
  ([§19](DESIGN.md#19-risks-and-open-questions)).
- A VM reboot, a program that starts by itself on a later boot, and the memory
  of a full-size compile. A three-variable program used 161 MiB steady and
  361 MiB at the peak.
- Whether `compilation-status` reports the previous build immediately after an
  upload.
- The autopilot hand-off. The autopilot is the built-in operator of the
  simulation. A running PLC writes its outputs every cycle, so the hand-off uses
  no PLC heartbeat tag
  ([§9.6](DESIGN.md#96-implementation-status-and-validation)).
- The path of the line operator through the Modbus server of rio-packaging.

FUXA and the HMI stations ([§12.2](DESIGN.md#122-fuxa-two-instances)):

- FUXA writes to plc-brewhouse over OPC UA. The run must show whether the
  OpenPLC server lets the FUXA session write the tags of the batch engine. It
  must show the namespace index and the tag types that the project assumes. It
  must also show whether the phase timeouts outlast the real phases.
- FUXA reads the BAS controllers through the BBMDs. The run must also show the
  null value that the BACnet module sends on a relinquish.
- The trends with the InfluxDB store of FUXA: the first hour of a trend
  ([fuxa/README.md](../fuxa/README.md#the-process-graphics)).
- The kiosk script on a kiosk VM.
- The panels and the records task of the historian while FUXA writes. The
  queries now filter on `name`, the tag key that the FUXA store writes. The
  authors compared this key with the FUXA source and with an InfluxDB 2.9.1 that
  held points from FUXA's own module. The queries did not run in Grafana yet
  ([§12.4](DESIGN.md#124-historian-and-engineering-workstation)).
- `fuxa.service` with `XDG_CONFIG_HOME` under systemd. The OPC UA client of FUXA
  then keeps its certificates in the data directory of FUXA. An emulation of the
  sandbox of the unit showed this behavior.

ot-sim ([§12.3](DESIGN.md#123-ot-sim-field-devices)):

- ot-sim and the BACnet module under `ot-sim.service` in a VM image, and COV.
- An offline image build. Pre-seed the Go toolchain that the build downloads, or
  vendor the modules.

SOC and remote access ([§13](DESIGN.md#13-vendor-remote-access),
[§14](DESIGN.md#14-blue-team-monitoring)):

- A real `cm-malcolm` VM with the feed of the mirror app and with its units
  under systemd. The VM also has Logstash, NetBox, and Strelka, which no run
  started. The run must show the memory of the VM with the full stack in 48 GB.
  The authors measured live Zeek, live Suricata, OpenSearch, and Arkime, and
  they estimated the rest ([§11](DESIGN.md#11-node-inventory-and-resources)).
- The Wazuh rules on live traffic. The Suricata rules (all 40) and the Zeek
  notices (all 16 that need no TLS session) fired in Malcolm's own 26.10.0
  containers. Those containers ran Suricata 8.0.7 with `cm-suricata.yaml`. The
  authors re-tagged the test traffic and replayed it into a stand-in for the
  mirror port.
- What the mirror delivers: one VLAN tag per direction of a routed flow
  (`tcpdump -e -nn -i phenix vlan`). The stand-in was a veth pair, not the port
  of Open vSwitch.
- The first files of Arkime after a start. If a pcap file closes while Malcolm
  still starts, Arkime can receive the file before Arkime is ready. Arkime then
  does not index the file. The container run showed this problem, because Arkime
  started slowly there.
- Whether the mirror NIC coalesces segments (GRO) before Open vSwitch hands them
  to the capture port. The interface tweak of Malcolm turns offloading off on
  the capture port only. If large merged segments show up in the pcap, turn GRO
  off on the mirror NIC in `cm-malcolm-capture`.
- The Wazuh fixes that came from a read of the 4.14.10 source, in
  `wazuh-logtest` and on the manager:
  - The `[cm-wg-drop]` prefix after a day of uptime
  - Rule 100104, which counts by group
  - `cm-wazuh-selftest` without `-q`
  - The password tool under the capabilities of the unit
  - `filebeat test output` in `cm-wazuh-health`
- The Wazuh first-boot setup under systemd with the corrected system-call
  filter. `runuser` and the Node tool of the dashboard ran under an emulation of
  that filter. The run must also show the sign-in to the dashboard after the
  password tool runs. It must show the agents without their journald reader:
  each event arrives once, from the files.
- Whether a recording that grows during a session raises Wazuh's own rule 550
  (level 7) on dmz-jump for every change.
- The agents and syslog into Wazuh, the second run of `cm-wazuh-provision`, and
  the admin password that `wazuh-passwords-tool.sh` sets.
- The retention configuration. The run must show the prune limits of Malcolm on
  26.10.0 and the Wazuh archive volume under a scan (the disk is 32 GB now). It
  must also show the growth of the ground truth over a week. The ground truth is
  the simulation's record of the true plant state.
- Guacamole under systemd, and the path of the vendor end to end with a real
  tunnel and a recorded RDP session. The stack itself ran on the four pinned
  images with the unmodified start script. That run covered first boot, the
  schema and both SQL files, and password and TOTP sign-in through nginx. It
  also covered the websocket, the brand, and a second boot that changed nothing.

phenix, images, and operations ([§15](DESIGN.md#15-phenix-integration)):

- A build of every image on a phenix host. Watch the first build and the first
  boot for services that start in the build chroot and for a wait on
  `systemd-networkd-wait-online`.
- The hardened systemd units:
  - `DynamicUser` with `StateDirectory` (the federate units)
  - `ProtectSystem=strict` with `ReadWritePaths` (`fuxa.service`,
    `cm-load-images`, `cm-malcolm-capture.service`, the Wazuh provisioning unit)
  - `MemoryDenyWriteExecute` (`cm-load-images`, `cm-malcolm-capture.service`)
- The path of the `df_default` command through minimega on a cluster
  ([§15.9](DESIGN.md#159-operations)).
- The outage and CO₂ scenarios, the four SCORCH runs, and an e2e (end-to-end)
  run.
- The boot times of each layer, which are estimates.
- The brand on Guacamole, Grafana, the desktops, and xrdp as it looks on the
  screen. The authors examined it offline. The Guacamole extension also loaded
  in a run of the stack, but nobody looked at the page
  ([§15.11](DESIGN.md#1511-brand)).
- After the `cherrymountain` app runs on a VM, drop the static simulation,
  OpenPLC, and FUXA injects from `topology-ci.yml`.

## Checks that need no VM

- A 24 h soak (a long continuous run) under HELICS: The plant ran offline
  through the harness, but the federation itself did not run for a day
  ([§9.6](DESIGN.md#96-implementation-status-and-validation)).
- A 10-minute memory soak of FUXA ([§12.2](DESIGN.md#122-fuxa-two-instances)).
- Tests that are missing: `cm-load-images` and the ot-svc chrony and dnsmasq
  hooks.
- Tests that CI does not run ([§17](DESIGN.md#17-testing-and-validation)):
  - The Docker integration test of the ci slice
  - `build_zip.sh --check`
  - The pcap replays through Suricata and Zeek
  - The compiled-program tests (they need Node, strucpp, and g++)
  - The browser test of cm-viz
- The phenix Docker image with the apps of the range: Nobody built this image
  yet. It is the phenix fork built with `APPS_BRANCH=feat-cherrymountain-apps`.
- Lint of the injected files of the app: The `cherrymountain` app moved to
  sceptre-phenix-apps. As a result, the shellcheck, `systemd-analyze`, and
  `desktop-file-validate` hooks of this repository no longer examine its
  scripts, units, and `.desktop` file. The apps repository runs none of these
  hooks.
- Brewtarget as a second check of the IBU model: The authors compared the model
  with the published Tinseth formula only.

## Control and safety

- A heartbeat from each remote I/O (RIO): The Modbus master of OpenPLC keeps the
  last value of an input when a RIO is unreachable. As a result, an interlock
  input holds its last state. With a counter that the RIO increments and the PLC
  watches, each PLC can fail its interlocks safe on a stale count
  ([§6.7](DESIGN.md#67-cellar-co2-life-safety-interlock),
  [§19](DESIGN.md#19-risks-and-open-questions)).
- The brewhouse in the CIP route interlock: No interlock ties CIP circuit 1 to
  the brewhouse entry permits, because plc-brewhouse serves OPC UA only. The
  circuit needs a peer path or a hardwired route interlock
  ([§6.8](DESIGN.md#68-confined-space-entry-lockout)). A peer path is a Modbus
  server on plc-brewhouse, or permit bits that FUXA mirrors.
- Device logic in ot-sim `logic` blocks: chiller staging, the BAS controllers'
  local interlocks, the PV limit, and the main relay's overcurrent trip. The
  first three run in the federates today
  ([§12.3](DESIGN.md#123-ot-sim-field-devices)).
- PLC programs closer to their targets
  ([§12.1](DESIGN.md#121-openplc-v4-runtime-v3-fallback)): The work is PID loops
  in plc-brewhouse, a transfer sequence in plc-cellar, and the Beckhoff PackML
  port in plc-packaging. Today, plc-brewhouse uses proportional valves.
- plc-power ([§7.3](DESIGN.md#73-power-control-plc-power)):
  - Retain the peak of the month, the last exercise day, and the rollover state
    across a restart.
  - Set the brew-day flag (`PWR.DMD.XS03.BREWDAY`) that the demand reserve waits
    for.
  - Shed loads within EP-1 on the generator.
- The first scan of plc-cellar writes a spunding setpoint before it reads its
  inputs. This is the one defect that stays from the
  [PLC review](plc-secure-coding-review.md).
- Alarm rationalization: The 98 alarms are not yet at the 5/15/80 priority
  split, because the authors rationalized only the important ones
  ([§6.5](DESIGN.md#65-alarm-philosophy-isa-182)). The hand-written plc-cellar
  program of the ci slice has on-delays only.
- The security practices for the PLC programs are under
  [Evaluated, not scheduled](#plc-programs-that-follow-the-top-20-secure-plc-coding-practices).

## Operator tools

- A daily production report from scada-fuxa: a PDF with the barrels packaged.
  The report is also the TTB record of record
  ([§12.2](DESIGN.md#122-fuxa-two-instances),
  [§12.4](DESIGN.md#124-historian-and-engineering-workstation)).
- A PackTags and OEE publisher that sends MQTT to the Mosquitto of the
  historian. Telegraf already subscribes.
- Alarm shelving (ISA-18.2) as a FUXA server script: a timed shelve, with a log
  entry in the historian ([§15.9](DESIGN.md#159-operations)).
- Stale values marked on the HMI: FUXA tags that show the age of their last
  update. Nothing marks a value as stale today.
- Grafana dashboards for energy (kWh/bbl), the demand cost of the month, and
  glycol health. Only the starter `cm-plant` dashboard exists.
- The Batch view shows `NaN` in its numbers for about 9 s after FUXA starts,
  until the first pass of the engine.

## Simulation

- Ale fermentation: The authors fitted the model to lager. Tune it until
  attenuation from 16.1 to about 3 °P takes 5 to 6 days at 66 to 70 °F. Compare
  the hop-creep rate and the altitude derating with brewer data
  ([§9.3](DESIGN.md#93-models)).
- A grid-forming battery: PV and the battery do not serve EP-1 in an outage
  ([§7.3](DESIGN.md#73-power-control-plc-power)).
- Tariff details: holidays in the on-peak window, and riders
  ([§7.4](DESIGN.md#74-utility-tariff-from-open-data)).
- Fermentation curves as CSV in the Fermentrack/iSpindel schema
  ([§9.5](DESIGN.md#95-scenario-events-and-ground-truth)).
- Weather files: The EPW files are not in the repository. Before anyone commits
  them, make sure that their terms allow redistribution
  ([§20](DESIGN.md#20-licensing)). Without an EPW file, the weather federate
  synthesizes Denver weather.

## Network and the range's own security

- A utility-only DNP3 map on rtu-der: The map exposes the main breaker, the
  feeder commands, and the site's own setpoints to the utility master. Restrict
  it in the `dnp3/rtu-der.json` of plantgen and in the ot-sim FEP configuration
  ([§7.5](DESIGN.md#75-utility-der-link)).
- Syslog from the hosts that have no Wazuh agent: The relay on ot-svc and its
  conduit exist, but no host forwards to the relay. The PLC and ot-sim images
  have no rsyslog, and the other hosts send through their agent
  ([§14](DESIGN.md#14-blue-team-monitoring)).
- Scoped InfluxDB tokens: One all-access operator token serves both FUXA
  servers, Telegraf, and Grafana. The anonymous viewer of Grafana can send any
  Flux, writes included. As a result, the "read-only Grafana" conduit from the
  HMI zone is not read-only, and bas-fuxa holds full control of the historian.
  Make `cm-historian-provision` create a read token for Grafana and write tokens
  per bucket. As an alternative, accept this as a weakness of the range and say
  so in [§13](DESIGN.md#13-vendor-remote-access).
- Agent enrollment that cannot displace a connected agent: Any host that holds
  the enrollment password (all 11 agent hosts) can enroll under the name of
  another agent and replace it at once. The `<force>` block of the manager
  allows this, so that a redeployed VM enrolls without a wait.
  `disconnected_time enabled="yes"` closes this hole. The price is about 15
  minutes before the manager takes a redeployed agent.
- Early syslog over TCP: The relay on ot-svc forwards over UDP, so it loses what
  it relays before Wazuh listens. With TCP 514 on the conduit, the relay, and
  the Wazuh listener, the queue of the relay holds those messages.
- Fixed HELICS ports, so that the MGMT policy can name exact ports instead of
  23407 to 23999. The change is `--localport` in `cherrymountain_sim` and in the
  configuration of the ot-sim `io` module
  ([§19](DESIGN.md#19-risks-and-open-questions)).
- Authentication on the ground truth: OpenSearch security, and a token for the
  event API ([§15.9](DESIGN.md#159-operations)).
- Autotunnel forwards bound to `127.0.0.1` in the loop of the fork. They listen
  on every interface of the host today.
- A named OPC UA account for FUXA in place of the anonymous operator session, if
  the range must not have that weakness.
- Wazuh: the analysisd events-per-second limit on the manager. A second item is
  the detection of a PLC program change from the EWS that does not go through
  `cm_plc_tool`. An auditd `execve` rule, or a rule on the REST paths of
  OpenPLC, can detect it.

## Exercise support

- The Builder package `builder/cherrymountain.package.yaml`, with the zone
  groups, notes, and icons ([§15.8](DESIGN.md#158-builder-package)).
- A packet-capture SoH scenario `scenario-soh-capture.yml`, with about 7 more
  VMs and 7 GB ([§15.4](DESIGN.md#154-state-of-health)). A second item is a FUXA
  BACnet live-value check on scada-fuxa. No other check can exercise BBMD
  discovery.
- SCORCH: randomized event times and magnitudes (`loop`, `replace`), vmstats and
  hoststats, and a collector stage that takes OEE from InfluxDB
  ([§15.6](DESIGN.md#156-scorch-pipelines)).
- A `cm-reset` run that clears the evidence stores without a restart
  ([§15.9](DESIGN.md#159-operations)).
- Ground truth against Malcolm: a script that reports each event as detected,
  late, or missed.
- Attack injects as SCORCH steps. The range deploys no adversary tooling
  ([§2](DESIGN.md#2-goals-and-non-goals)). Caldera with caldera-ot and business
  IT (ERP, LIMS, a directory, mail) have their attach points, and nobody plans
  them. The injects are:
  - A rogue Modbus write from the DMZ
  - A rewrite of the table of a BBMD
  - A DNP3 breaker operate
  - A PLC program change through the vendor path

## The white cell's 3D view

The white cell is the staff that controls the exercise. These items come from
[§15.10](DESIGN.md#1510-white-cell-3d-view-cm-viz):

- The run in the range: the view through a real cc tunnel, and the frame age.
  The run must also show what the 191 subscriptions of the view do to the
  federation's timing over an hour.
- The operator-view relay: a relay on the phenix host that reads the `scada`
  bucket of the historian and fills the operator layer.
- Replay from the ground-truth OpenSearch, and a brewhouse scene.
- Labels overlap in the 2.5D view of the cellar scene during the CO₂ attack.

## Upstream

Until the upstream projects (the original projects of the forks) accept these
changes, the phenix image of the range installs the forked
`sceptre-phenix-apps`. It uses the branch `feat-cherrymountain-apps`, which also
holds the `cherrymountain` app and `plantevent`. The range runs on the phenix
fork. [§16](DESIGN.md#16-new-and-extended-software) has each change in full.

- The BACnet module of ot-sim: A version for upstream is on the fork branch
  `feat-bacnet-module`. The support for it in the phenix `ot-sim` app is on
  `feat-otsim-bacnet` of the phenix-apps fork.
- The phenix `ot-sim` app: render the `telnet` module, which the
  `cherrymountain` app injects today.
- The phenix `mirror` app: `df_default=false` on the GRE port and the capture-VM
  flow in `setupOVS` mode.
- The phenix autotunnel: the loop of the fork, and a way to read the host port.
  The loop makes tunnels for experiments that start from the CLI, and it
  re-creates them after a VM restart.
- The phenix `vrouter` app and the VyOS image:
  - IKEv2 `key-exchange`
  - Rule logging
  - A syslog target
  - An NTP source for each router
  - Filtering of router-local traffic
  - Fractional netem loss
  - An IPsec policy match
  - The `build-vyos.sh` fixes
- The phenix serial app (PR 130): After a redeploy of a VM, reconnect the
  `socat` of the host. Find why a restarted VM did not register its C2 client.
- SoH probes for Modbus, BACnet, and OPC UA as first-class checks (a proposal).
- The e2e harness entry for the ci slice, on the branch `ci-e2e-minimega` of the
  phenix fork.
- Images: `cm-otsim`, `cm-openplc`, and `cm-fuxa` are candidates for
  sceptre-phenix-images ([§15.7](DESIGN.md#157-images)).

## Images and builds

- The Ubuntu update pockets: The build takes every image from the release pocket
  of noble alone, so the base packages are those of April 2024. The vendor
  reaches the EWS on SSH and RDP. The EWS has `openssh-server` 1:9.6p1-3ubuntu13
  (13.3 fixes CVE-2024-6387), `xrdp` 0.9.24-4, and kernel 6.8.0-31. That is not
  one of the intended weaknesses of the range. One choice is to add
  `noble-updates` and `noble-security` and to upgrade at the start of each
  `INSTALL_*` script. A kernel upgrade inside the build chroot needs a build to
  show that the image still boots. The other choice is to list the old packages
  as accepted.
- Pinned Python and npm dependencies: The dependencies below float (they have no
  fixed version), so two builds on different days differ. A pip constraints file
  per image, taken from a build that works, and a lockfile for FUXA can fix
  them. The dependencies are:
  - The Python dependencies of ot-sim (numpy, pandas, pyzmq)
  - The dependencies of cm-sim (pydantic, SimPy, OpenDSSDirect's own)
  - The `requirements.txt` of OpenPLC
  - The npm tree of FUXA past its lockfile (`npm ci` fails at the pin, so the
    build uses `npm install`)
- The ot-sim Go build: `make install` builds all eight modules. The rule of the
  sunspec module runs `go install github.com/atombender/go-jsonschema@latest`
  and regenerates a source file. That rule pulls a newer Go toolchain. The range
  uses four of the binaries, and a build of only those four needs neither
  action.
- A health check for the runtime of FUXA: `cm-fuxa-provision` reports a project
  POST that got no answer. A later run finds the stored project, but it has no
  way to see a dead runtime. The reason is that FUXA exposes device status only
  over its socket.

## Sources still to read

[§18A](DESIGN.md#18a-regulatory-and-standards-context) rests on secondary
sources for the texts below, and cites no clause of them:

- The NFPA texts (110 among them)
- ASHRAE 15, 62.1, and 90.1
- CGA G-6.5 (nobody made sure of the edition)
- ASME Section VIII U-1(c)(2) (the open question is whether the 15 psig unitanks
  are in its scope)
- TTB Ruling 2020-2
- The CDPHE stormwater permit COR900000
- The water-supply rules

These items are also open in that section:

- The ethanol emissions and the APEN determination of the generator
- The CIP tank sizes against Tier II reporting
- The CIRCIA final rule, which CISA did not issue before the review

Nobody reviewed the ST programs and FUXA screens of rsb-lab
([§21.6](DESIGN.md#216-prior-art-ics-ranges-and-testbeds)) for reuse.

## Evaluated, not scheduled

The authors examined these items, and nobody decided to do them.

### PLC programs that follow the Top 20 Secure PLC Coding Practices

A review compared the five PLC programs with the
[Top 20 Secure PLC Coding Practices](https://plc-security.com/content/Top_20_Secure_PLC_Coding_Practices_V1.0.pdf)
(version 1.0). The file
[docs/plc-secure-coding-review.md](plc-secure-coding-review.md) rates each
practice for each program and gives the evidence. The programs are sound as
control programs: they have one generated alarm block, range-limited setpoints,
and a defined start. They have no security layer on top, and there is no
practice that all five programs meet. The authors fixed the plain defects that
the review found. The practices themselves stay as open work, in the order of
importance of the review:

- State that survives a restart and an outside write (practices 10, 15, 20): The
  entry permits, the entry-violation latches, and the CO₂ shutoff latch are
  plain coils. A restart clears them, and one Modbus write clears them. Keep
  each in an internal, retained variable and publish a copy.
- What a stopped PLC does to its outputs (practice 15): Nobody made sure of this
  behavior, and it can be the opposite of what
  [§6.7](DESIGN.md#67-cellar-co2-life-safety-interlock) assumes
  ([§19](DESIGN.md#19-risks-and-open-questions)).
- Timeouts and permissives in the PLC, not the HMI (practices 3, 12): A
  brewhouse phase that waits on a process value has no PLC timeout, and the
  destination check of the knockout is in the batch engine.
- Plausibility of inputs (practices 7, 11, 12): The items are a command against
  its feedback, and a level switch against its transmitter. Two more items are a
  stuck or out-of-range value, and a maximum dose time.
- Process limits on what an operator can write (practices 6, 8), generated from
  `plant.yaml`, with a counter for refused writes.
- Alarms that nobody can miss or forge (practice 20): The means are an off-delay
  on every alarm, and a latch on the critical ones. A third means is a
  comparison of each alarm bit with what the block computed.
- The PLC's own health on the HMI (practices 2, 5, 16 to 19): The values are a
  scan counter, the time since start, a restart count, and the hash of the
  loaded program. The HMI must trend them and raise alarms on them.
- Structure (practices 1, 4, 10): function blocks for the hand-written logic,
  error counters, and writable registers apart from computed ones.

Not all of this work belongs on this range. Some weaknesses are its attack
surface by decision. Examples are the anonymous OPC UA operator role, and a PLC
image that any client on its port can write. Section 3 of the review lists them.

### A conduit for soc-purple's active tools

The soc-purple node ([§14](DESIGN.md#14-blue-team-monitoring)) has PEAT, which
pulls configuration and logic from OT devices. It also has parseus, which
analyzes Modbus registers that PEAT or its own ingestors collect. Both tools
must reach a device, and the SOC has no conduit into OPS, CTRL, or the field.
The SOC watches a mirror and reads logs. With Modbus TCP from soc-purple to the
PLCs, the tools become useful against the live plant. But that conduit also puts
a host in the SOC that can write to every PLC. The reason is that a port filter
cannot tell a Modbus read from a write.

There are three choices, and nobody took one:

- Leave it as it is: analysis of what Malcolm captured.
- Add the conduit and a Suricata rule for a write from soc-purple.
- Move the polling to a host that already has the conduit. The EWS runs
  `peat pull`, and someone carries the results to soc-purple.

Three points are open with it, and no run covered them:

- Whether PEAT has a module that the OpenPLC and ot-sim devices of the range
  answer
- The memory of parseus on less than its stated 16 GB
- The first build and boot of the image

The parseus tool on soc-purple brings the Elasticsearch, Kibana, and Logstash
images of Elastic. These images are under the Elastic License 2.0, which is not
an OSI license ([§20](DESIGN.md#20-licensing)). If the range must stay OSI-only,
remove the node or point parseus at an OpenSearch.

### SOC case management with DFIR-IRIS

The SOC can triage alerts in Wazuh and Malcolm, but no tool covers the response
side. The response side covers who owns an alert, how alerts group into an
incident, and playbook tasks. It also covers a timeline that the white cell can
score against ground truth. The
[case-management spike](../spikes/thehive/README.md) rejected TheHive 5/6,
because it is closed source. In addition, its Community license is
non-commercial and has a limit of 2 users
([§2](DESIGN.md#2-goals-and-non-goals), [§20](DESIGN.md#20-licensing)). The
spike recommends DFIR-IRIS 2.4 (LGPL-3.0) as the replacement. In its proposal:

- DFIR-IRIS runs on one optional VM, `soc-ir` (2 vCPU, 3 GB RAM, 16 GB disk, on
  the SOC VLAN), in the `full` variant only. The `ci` and `lite` variants do not
  change.
- A custom Wazuh integration files CherryMountain alerts of level 7 and up as
  IRIS alerts. It maps the severity, removes duplicates, and picks a case
  template from the rule group.
- The alerts of Malcolm reach IRIS through Wazuh.
- ICS playbooks, such as a PLC program change or the CO₂ life-safety event,
  become IRIS case templates.

The spike proved the integration in Docker against a real Wazuh 4.14.1 manager.
Its plan takes 8 to 12 days in five phases, with a go/no-go decision after the
image phase. If the VM needs more than 3 GB or cannot boot offline, stop. The
spike also lists SOC gaps that apply with or without a case tool. For example,
the detections of Malcolm do not reach Wazuh, and FUXA process alarms have no
Wazuh rules.

### A fleet of four breweries and a head office

The proposed `fleet` variant puts four breweries in one experiment. A
site-to-site VPN joins them to a head office over a mock internet of five BGP
networks. The four are not copies. They are a flagship with the head office, a
newer production brewery, a brewery that the company bought, and a brewpub. Each
has the network that such a site has in practice.

[multisite/PLANS.md](multisite/PLANS.md) has three plans for the variant and the
choices behind them. It also says where the VMs of the variant run on a cluster
of minimega nodes. It lists nineteen decisions, and the authors took nine of
them. Four spikes back the variant:

- A [lab of the WAN](../spikes/wan-bgp/README.md) in network namespaces. In this
  lab, the site on a failed ISP lost about one second of traffic.
- [The core of that WAN on four VyOS routers](../spikes/wan-bgp/README.md#the-same-wan-on-vyos-routers)
  in CI. The routers got their configuration from phenix through a patch to its
  `vrouter` app. The site lost two to three seconds, with WireGuard and with
  route-based IPsec.
- A [stamp of the plant](../spikes/multisite-stamp/README.md) as four sites,
  which plantgen generates after one line changes.
- [Four sites that differ](../spikes/site-profiles/README.md) in size and in the
  division of their networks. After three more kinds of edit to the plant file,
  plantgen generates them.

No site ran next to that WAN yet, and no part ran on more than one node. The
variant depends on
[the first run of the single site](#the-first-run-on-a-phenix-host).

## Decided against

The authors considered these items and do not plan them. They are here so that
nobody plans them twice.

- A per-session approval record and time-boxed enablement of the vendor's
  WireGuard peer: The range owner excluded this item. The approved windows stay
  time-of-day rules ([§13](DESIGN.md#13-vendor-remote-access)).
- TheHive for case management: It is closed source, and its Community license
  does not fit ([above](#soc-case-management-with-dfir-iris)).
- A shared `cm-lib.sh` for the boot scripts: If a fourth copy of a helper
  appears, look again ([§15.9](DESIGN.md#159-operations)).
- Alternatives kept in reserve and not built
  ([§8.3](DESIGN.md#83-recommendation),
  [§19](DESIGN.md#19-risks-and-open-questions)):
  - pandapower in place of OpenDSS
  - The BAS BBMD inside the BACnet module of `bas-vent` (it removes the
    `bas-bbmd` VM)
  - BOPTEST for the office
  - BACnet/SC
