# Future work

Everything that is planned or considered for CherryMountain Brewery and not done
is in this file. [DESIGN.md](DESIGN.md) describes the range as it is designed
and built, and its [Status](DESIGN.md#status) section says how far each part has
been run; where the design mentions something that is still to do, it points
here. A limit that is part of the design (something left out of the model on
purpose) stays in DESIGN.md, in the section it belongs to and in
[§19](DESIGN.md#19-risks-and-open-questions).

- [The first run on a phenix host](#the-first-run-on-a-phenix-host)
- [Checks that need no VM](#checks-that-need-no-vm)
- [Control and safety](#control-and-safety)
- [Operator tools](#operator-tools)
- [Simulation](#simulation)
- [Network and the range's own security](#network-and-the-ranges-own-security)
- [Exercise support](#exercise-support)
- [The white cell's 3D view](#the-white-cells-3d-view)
- [Upstream](#upstream)
- [Sources still to read](#sources-still-to-read)
- [Evaluated, not scheduled](#evaluated-not-scheduled)
- [Decided against](#decided-against)

## The first run on a phenix host

Nothing has run on a phenix host or in a VM. This is the list of what that
first run has to show. The user guide's
[Known limits](../USER_GUIDE.md#known-limits) says what to look at on the first
start.

**Network** ([§10.3](DESIGN.md#103-conduit-matrix-default-deny-stateful-logged))

- VyOS accepts the rendered rulesets, and every conduit passes and every
  blocked path fails under SoH. `netcheck` checks the rules phenix renders, not
  what VyOS does with them.
- The post-boot script `cm-vyos-config.sh` on VyOS: the syslog target (it tries
  `remote`, then `host`), the IKEv2 setting, and the IPsec match on the
  utility's DNP3 rule.
- ot-sim DNP3 across the IPsec tunnel (the spike used a link-status stub).
- The MGMT nftables hook finds the guest's device on every MGMT member, and
  noble's `nft` takes `-D`.
- `cm-bbmd` under systemd on ot-svc and bas-bbmd, foreign-device registration,
  and bas-fuxa reading the BAS controllers by the directed broadcast
  `10.30.10.255` ([§8.1](DESIGN.md#81-spaces-equipment-and-bacnet-devices)).

**PLCs** ([§12.1](DESIGN.md#121-openplc-v4-runtime-v3-fallback))

- The full and lite programs in an OpenPLC runtime, and plc-utilities against
  the federates (its CIP sequence ran with its inputs set by hand).
- What a stopped or faulted runtime does to its outputs. If it drives them to
  0, make 0 the safe state of every command or have the remote I/O hold on a
  lost master, and correct [§6.7](DESIGN.md#67-cellar-co2-life-safety-interlock)
  ([§19](DESIGN.md#19-risks-and-open-questions)).
- A VM reboot, the program starting by itself on a later boot, and the memory
  of a full-size compile (161 MiB steady and 361 MiB at the peak for a
  three-variable program).
- Whether `compilation-status` reports the build before it right after an
  upload.
- The autopilot hand-off: a running PLC writes its outputs every cycle, so no
  PLC heartbeat tag is used ([§9.6](DESIGN.md#96-implementation-status-and-validation)).
- The line operator's path through rio-packaging's Modbus server.

**FUXA and the HMI stations** ([§12.2](DESIGN.md#122-fuxa-two-instances))

- FUXA writing to plc-brewhouse over OPC UA: whether OpenPLC's server lets
  FUXA's session write the batch engine's tags, the namespace index and the
  tag types the project assumes, and whether the phase timeouts outlast the
  real phases.
- FUXA reading the BAS controllers through the BBMDs, and the null value the
  BACnet module sends on a relinquish.
- The trends with FUXA's InfluxDB store: the first hour of a trend
  ([fuxa/README.md](../fuxa/README.md#the-process-graphics)).
- The kiosk script on a kiosk VM.
- FUXA's DAQ field layout in InfluxDB, which the historian's records task and
  dashboard assume ([§12.4](DESIGN.md#124-historian-and-engineering-workstation)).

**ot-sim** ([§12.3](DESIGN.md#123-ot-sim-field-devices))

- ot-sim and the BACnet module under `ot-sim.service` in a VM image, and COV.
- An offline image build: pre-seed the Go toolchain the build downloads, or
  vendor the modules.

**SOC and remote access** ([§13](DESIGN.md#13-vendor-remote-access),
[§14](DESIGN.md#14-blue-team-monitoring))

- A real `cm-malcolm` VM with live capture (`zeek-live`), NetBox, and Strelka,
  and its memory with the full stack: the 24 GB default stays until it is
  measured ([§11](DESIGN.md#11-node-inventory-and-resources)).
- The Suricata, Zeek, and Wazuh rules on live traffic, and the Suricata
  `modbus: address` rules on Malcolm's Suricata 8.0.7.
- The agents and syslog into Wazuh, `cm-wazuh-provision`'s second run, and the
  admin password that `wazuh-passwords-tool.sh` sets.
- The retention settings: Malcolm's prune limits on 26.10.0, the Wazuh archive
  volume under a scan, and ground truth's growth over a week.
- Guacamole: `cmadmin` and the `ews-rdp` and `ews-ssh` connections exist after
  the first boot of dmz-jump. The vendor's path end to end.

**phenix, images, and operations** ([§15](DESIGN.md#15-phenix-integration))

- Every image built on a phenix host. Watch the first build and the first boot
  for services that start in the build chroot and for a wait on
  `systemd-networkd-wait-online`.
- The hardened systemd units: `DynamicUser` with `StateDirectory`, read-only
  paths, and `MemoryDenyWriteExecute` on `fuxa.service`, the federate units,
  `cm-load-images`, `cm-malcolm-capture.service`, and the Wazuh provisioning
  unit.
- The `df_default` command's path through minimega on a cluster
  ([§15.9](DESIGN.md#159-operations)).
- The outage and CO₂ scenarios, the four SCORCH runs, and an e2e run.
- The boot times of each layer, which are estimates.
- The brand on Guacamole, Grafana, the desktops, and xrdp, which were checked
  offline ([§15.11](DESIGN.md#1511-brand)).
- Once the `cherrymountain` app has run on a VM, drop the static simulation,
  OpenPLC, and FUXA injects from `topology-ci.yml`.

## Checks that need no VM

- **A 24 h soak under HELICS.** The plant ran offline through the harness; the
  federation itself has not run for a day ([§9.6](DESIGN.md#96-implementation-status-and-validation)).
- **A 10-minute memory soak of FUXA** ([§12.2](DESIGN.md#122-fuxa-two-instances)).
- **Tests that are missing:** `cm_selftest_pcap.py`, `cm-load-images`, and the
  ot-svc chrony and dnsmasq hooks.
- **Tests that CI does not run:** the Docker integration test of the ci slice,
  `build_zip.sh --check`, the pcap replays through Suricata and Zeek, the
  compiled-program tests (they need Node, strucpp, and g++), and cm-viz's
  browser test ([§17](DESIGN.md#17-testing-and-validation)).
- **The derived phenix Docker image**, which has not been built.
- **Brewtarget** as a second check of the IBU model, which is compared with
  the published Tinseth formula only.

## Control and safety

- **A heartbeat from each remote I/O.** OpenPLC's Modbus master keeps the last
  value of an input when a RIO is unreachable, so an interlock input holds its
  last state. A counter that the RIO increments and the PLC watches would let
  each PLC fail its interlocks safe on a stale count
  ([§6.7](DESIGN.md#67-cellar-co2-life-safety-interlock),
  [§19](DESIGN.md#19-risks-and-open-questions)).
- **The brewhouse in the CIP route interlock.** CIP circuit 1 is not
  interlocked to the brewhouse entry permits, because plc-brewhouse serves OPC
  UA only. It needs a peer path (a Modbus server on plc-brewhouse, or the
  permit bits mirrored by FUXA) or a hardwired route interlock
  ([§6.8](DESIGN.md#68-confined-space-entry-lockout)).
- **Device logic in ot-sim `logic` blocks:** chiller staging, the BAS
  controllers' local interlocks, the PV limit, and the main relay's
  overcurrent trip. The first three run in the federates today
  ([§12.3](DESIGN.md#123-ot-sim-field-devices)).
- **PLC programs closer to their targets:** PID loops in plc-brewhouse (it
  uses proportional valves), a transfer sequence in plc-cellar, and the
  Beckhoff PackML port in plc-packaging ([§12.1](DESIGN.md#121-openplc-v4-runtime-v3-fallback)).
- **plc-power:** retain the month's peak, the last exercise day, and the
  rollover state across a restart; set the brew-day flag
  (`PWR.DMD.XS03.BREWDAY`) that the demand reserve waits for; shed loads
  within EP-1 on the generator ([§7.3](DESIGN.md#73-power-control-plc-power)).
- **The first scan of plc-cellar** writes a spunding setpoint before it has
  read its inputs, the one defect left from the
  [PLC review](plc-secure-coding-review.md).
- **Alarm rationalization.** The 98 alarms are not yet at the 5/15/80 priority
  split, because only the important ones are rationalized
  ([§6.5](DESIGN.md#65-alarm-philosophy-isa-182)). The ci slice's hand-written
  plc-cellar program has on-delays only.
- **The security practices for the PLC programs** are under
  [Evaluated, not scheduled](#plc-programs-that-follow-the-top-20-secure-plc-coding-practices).

## Operator tools

- **A daily production report** from scada-fuxa (a PDF with the barrels
  packaged), which is also the TTB record of record
  ([§12.2](DESIGN.md#122-fuxa-two-instances), [§12.4](DESIGN.md#124-historian-and-engineering-workstation)).
- **A PackTags and OEE publisher** that sends MQTT to the historian's
  Mosquitto. Telegraf already subscribes.
- **Alarm shelving** (ISA-18.2) as a FUXA server script: a timed shelve,
  logged to the historian ([§15.9](DESIGN.md#159-operations)).
- **Stale values marked on the HMI:** FUXA tags that show the age of their
  last update. Nothing marks a value as stale today.
- **Grafana dashboards** for energy (kWh/bbl), the month's demand cost, and
  glycol health. Only the starter `cm-plant` dashboard exists.
- **The Batch view shows `NaN`** in its numbers for about 9 s after FUXA
  starts, until the engine's first pass.

## Simulation

- **Ale fermentation.** The model is fitted to lager. Tune it so attenuation
  from 16.1 to about 3 °P takes 5 to 6 days at 66 to 70 °F, and compare the
  hop-creep rate and the altitude derating with brewer data
  ([§9.3](DESIGN.md#93-models)).
- **A grid-forming battery.** PV and the battery do not serve EP-1 in an
  outage ([§7.3](DESIGN.md#73-power-control-plc-power)).
- **Tariff details:** holidays in the on-peak window, and riders
  ([§7.4](DESIGN.md#74-utility-tariff-from-open-data)).
- **Fermentation curves as CSV** in the Fermentrack/iSpindel schema
  ([§9.5](DESIGN.md#95-scenario-events-and-ground-truth)).
- **Weather files.** The EPW files are not committed; confirm their
  redistribution terms first ([§20](DESIGN.md#20-licensing)). Without one the
  weather federate synthesizes Denver weather.

## Network and the range's own security

- **A utility-only DNP3 map on rtu-der.** The map exposes the main breaker,
  the feeder commands, and the site's own setpoints to the utility master.
  Restrict it in plantgen's `dnp3/rtu-der.json` and the ot-sim FEP config
  ([§7.5](DESIGN.md#75-utility-der-link)).
- **Fixed HELICS ports,** so the MGMT policy can name exact ports instead of
  23407 to 23999: `--localport` in `cherrymountain_sim` and in the ot-sim `io`
  module's config ([§19](DESIGN.md#19-risks-and-open-questions)).
- **Authentication on the ground truth:** OpenSearch security, and a token for
  the event API ([§15.9](DESIGN.md#159-operations)).
- **Autotunnel forwards bound to `127.0.0.1`** in the fork's loop. They listen
  on every interface of the host today.
- **A named OPC UA account for FUXA** in place of the anonymous operator
  session, if the range should not have that weakness.
- **Wazuh:** the analysisd events-per-second limit on the manager, and
  detection of a PLC program change from the EWS that does not go through
  `cm_plc_tool` (an auditd `execve` rule, or a rule on OpenPLC's REST paths).

## Exercise support

- **The Builder package** `builder/cherrymountain.package.yaml`, with the zone
  groups, notes, and icons ([§15.8](DESIGN.md#158-builder-package)).
- **A packet-capture SoH scenario** `scenario-soh-capture.yml`, about 7 more
  VMs and 7 GB ([§15.4](DESIGN.md#154-state-of-health)), and a FUXA BACnet
  live-value check on scada-fuxa, the one check that would exercise BBMD
  discovery.
- **SCORCH:** randomized event times and magnitudes (`loop`, `replace`),
  vmstats and hoststats, and a collector stage that takes OEE from InfluxDB
  ([§15.6](DESIGN.md#156-scorch-pipelines)).
- **A `cm-reset` run** that clears the evidence stores without a restart
  ([§15.9](DESIGN.md#159-operations)).
- **Ground truth against Malcolm:** a script that reports each event as
  detected, late, or missed.
- **Attack injects as SCORCH steps:** a rogue Modbus write from the DMZ, a
  rewrite of a BBMD's table, a DNP3 breaker operate, and a PLC program change
  through the vendor path. The range deploys no adversary tooling
  ([§2](DESIGN.md#2-goals-and-non-goals)); Caldera with caldera-ot and business IT
  (ERP, LIMS, a directory, mail) have their attach points and are not planned.

## The white cell's 3D view

From [§15.10](DESIGN.md#1510-white-cell-3d-view-cm-viz):

- **The run in the range:** the view through a real cc tunnel, the frame age,
  and what its 188 subscriptions do to the federation's timing over an hour.
- **The operator-view relay:** a relay on the phenix host that reads the
  historian's `scada` bucket and fills the operator layer.
- **Replay** from the ground-truth OpenSearch, and **a brewhouse scene.**
- **Labels overlap** in the cellar scene's 2.5D view during the CO₂ attack.

## Upstream

Until these land, the derived phenix container installs the forked
`sceptre-phenix-apps` and the range runs on the phenix fork
([§16](DESIGN.md#16-new-and-extended-software) has each change in full).

- **ot-sim: the BACnet module.** A version for upstream is on the fork branch
  `feat-bacnet-module`; the phenix `ot-sim` app's support for it is on
  `feat-otsim-bacnet` of the phenix-apps fork.
- **phenix `ot-sim` app:** render the `telnet` module, which the
  `cherrymountain` app injects today.
- **phenix `mirror` app:** `df_default=false` on the GRE port and the
  capture-VM flow in `setupOVS` mode.
- **phenix autotunnel:** the fork's loop (tunnels for experiments started from
  the CLI, re-created after a VM restart), and a way to read the host port.
- **phenix `vrouter` app and the VyOS image:** IKEv2 `key-exchange`, rule
  logging, a syslog target, an NTP source for each router, filtering of
  router-local traffic, fractional netem loss, an IPsec policy match, and the
  `build-vyos.sh` fixes.
- **phenix serial app (PR 130):** reconnect the host's `socat` when a VM is
  redeployed, and find why a restarted VM did not register its C2 client.
- **SoH probes** for Modbus, BACnet, and OPC UA as first-class checks (a
  proposal).
- **The e2e harness** entry for the ci slice, on the phenix fork's branch
  `ci-e2e-minimega`.
- **Images:** `cm-otsim`, `cm-openplc`, and `cm-fuxa` are candidates for
  sceptre-phenix-images ([§15.7](DESIGN.md#157-images)).
- Move `CM_REF` forward with every `sim/` change an image must carry.

## Sources still to read

[§18A](DESIGN.md#18a-regulatory-and-standards-context) rests on secondary sources for
these, and cites no clause of them: the NFPA texts (110 among them), ASHRAE
15, 62.1, and 90.1, CGA G-6.5 (edition not verified), ASME Section VIII U-1(c)(2)
(whether the 15 psig unitanks are in its scope), TTB Ruling 2020-2, the CDPHE
stormwater permit COR900000, and the water-supply rules. Also open there: the
ethanol emissions and the generator's APEN determination, the CIP tank sizes
against Tier II reporting, and the CIRCIA final rule, which CISA had not issued
at the time of the review. The ST programs and FUXA screens of rsb-lab
([§21.6](DESIGN.md#216-prior-art-ics-ranges-and-testbeds)) have not been reviewed for
reuse.

## Evaluated, not scheduled

These items have been looked into, and nobody has decided to do them.

### PLC programs that follow the Top 20 Secure PLC Coding Practices

The five
PLC programs were reviewed against the [Top 20 Secure PLC Coding
Practices](https://plc-security.com/content/Top_20_Secure_PLC_Coding_Practices_V1.0.pdf)
(version 1.0): [docs/plc-secure-coding-review.md](plc-secure-coding-review.md)
rates each practice for each program and gives the evidence. The programs are
sound as control programs (one generated alarm block, range-limited setpoints,
a defined start) and have no security layer on top: no practice is met by all
of them. The plain defects the review found are fixed; what is left is the
practices themselves, in the review's order of importance:
- **State that survives a restart and an outside write** (practices 10, 15,
  20). The entry permits, the entry-violation latches, and the CO₂ shutoff
  latch are plain coils: a restart clears them, and one Modbus write clears
  them. Keep each in an internal, retained variable and publish a copy.
- **What a stopped PLC does to its outputs** (practice 15). Unverified, and
  it may be the opposite of what [§6.7](DESIGN.md#67-cellar-co2-life-safety-interlock)
  assumes ([§19](DESIGN.md#19-risks-and-open-questions)).
- **Timeouts and permissives in the PLC, not the HMI** (practices 3, 12). A
  brewhouse phase that waits on a process value has no PLC timeout, and the
  knockout's destination check is in the batch engine.
- **Plausibility of inputs** (practices 7, 11, 12): a command against its
  feedback, a level switch against its transmitter, a stuck or out-of-range
  value, a maximum dose time.
- **Process limits on what an operator may write** (practices 6, 8),
  generated from `plant.yaml`, with a counter for refused writes.
- **Alarms that cannot be missed or forged** (practice 20): an off-delay on
  every alarm, a latch on the critical ones, and a check of each alarm bit
  against what the block computed.
- **The PLC's own health on the HMI** (practices 2, 5, 16 to 19): a scan
  counter, the time since start, a restart count, and the loaded program's
  hash, trended and alarmed.
- **Structure** (practices 1, 4, 10): function blocks for the hand-written
  logic, error counters, and writable registers apart from computed ones.

Not all of it should be done on this range. Some weaknesses are its attack
surface by decision (the anonymous OPC UA operator role, a PLC image that any
client on its port may write); the review's section 3 lists them.

### SOC case management with DFIR-IRIS

The SOC can triage alerts in Wazuh
and Malcolm, but no tool covers the response side: who owns an alert,
grouping alerts into an incident, playbook tasks, and a timeline the white
cell can score against ground truth. The
[case-management spike](../spikes/thehive/README.md) rejected TheHive 5/6,
because it is closed source and its Community licence is non-commercial and
capped at 2 users ([§2](DESIGN.md#2-goals-and-non-goals), [§20](DESIGN.md#20-licensing)). It
recommends **DFIR-IRIS 2.4** (LGPL-3.0) instead:
- It would run on one optional VM, `soc-ir` (2 vCPU, 3 GB RAM, 16 GB disk,
  on the SOC VLAN), in the `full` variant only. `ci` and `lite` would not
  change.
- A custom Wazuh integration would file CherryMountain alerts of level 7
  and up as IRIS alerts, with severity mapping, deduplication and a case
  template picked from the rule group.
- Malcolm's alerts would reach IRIS through Wazuh.
- ICS playbooks, such as a PLC program change or the CO₂ life-safety event,
  would become IRIS case templates.

The spike proved the integration in Docker against a real Wazuh 4.14.1
manager. Its plan takes 8–12 days in five phases, with a go/no-go after the
image phase: stop if the VM needs more than 3 GB or can't boot offline. The
spike also lists SOC gaps that apply with or without a case tool. Examples
are that Malcolm's detections don't reach Wazuh and that FUXA process alarms
have no Wazuh rules.

## Decided against

These were considered and are not planned. They are here so that nobody plans
them twice.

- **A per-session approval record and time-boxed enablement of the vendor's
  WireGuard peer.** Excluded by the range owner; the approved windows stay
  time-of-day rules ([§13](DESIGN.md#13-vendor-remote-access)).
- **TheHive** for case management: closed source, and its Community licence
  does not fit ([above](#soc-case-management-with-dfir-iris)).
- **A shared `cm-lib.sh`** for the boot scripts. Look again if a fourth copy
  of a helper appears ([§15.9](DESIGN.md#159-operations)).
- **Alternatives kept in reserve and not built:** pandapower in place of
  OpenDSS, the BAS BBMD inside `bas-vent`'s BACnet module (it would drop the
  `bas-bbmd` VM), BOPTEST for the office, and BACnet/SC
  ([§8.3](DESIGN.md#83-recommendation), [§19](DESIGN.md#19-risks-and-open-questions)).
