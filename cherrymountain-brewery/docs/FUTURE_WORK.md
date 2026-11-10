# Future work

Everything that is planned or considered for CherryMountain Brewery and not done
is in this file. [DESIGN.md](DESIGN.md) describes the range as it is designed
and built, and its [Status](DESIGN.md#status) section says how far each part has
been run; where the design mentions something that is still to do, it points
here.

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
