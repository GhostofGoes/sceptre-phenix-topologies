# Spike: BACnet BBMDs across the OT firewall

**Question.** FUXA's BACnet driver (node-bacnet 0.2.4) sends its Who-Is only to
`255.255.255.255:47808`, which a router never forwards, so a FUXA on OPS cannot
discover BAS devices behind `fw-ot`. If a BACpypes3 BBMD on each side (each with
a BDT that lists the other) forwards the broadcast, does a Who-Is from OPS find
device 3003, and does ReadProperty then work through a stateful default-drop
ACL that allows UDP 47808 between the BBMDs and the replies? **Decides:** the
BACnet conduit rules in DESIGN §10.3, the BBMD nodes in §11, and whether
`scada-fuxa` can reach BAS devices at all (follow-up of the
[bacnet](../bacnet/README.md) spike).

## Fixture

```text
OPS 10.20.30.0/24                      BAS 10.20.60.0/24
ops-client .30  ops-bbmd .5  --[ router .1 | .1 ]--  bas-bbmd .5  bas-dev .13
(node-bacnet    (BACpypes3,   default-drop ACL,      (BACpypes3,   (responder.py,
 stand-in)       BDT: both)   UDP 47808 rules)        BDT: both)    device 3003)
```

- `whois_client.py` behaves like node-bacnet 0.2.4: it binds UDP 47808 on its
  own address, broadcasts a Who-Is to 255.255.255.255, resolves the device from
  the I-Am, and then reads `object-name` with a unicast ReadProperty.
- `bbmd.py` is a BACpypes3 BBMD (`--bbmd <BDT>`); `../bacnet/responder.py` is
  the device.
- `scenario.py` holds the steps and the pass/fail checks. Two carriers run it:
  - `netns_lab.py`: Linux network namespaces and an nftables router. Runs
    anywhere with root (`sudo -E python netns_lab.py`).
  - `ci.py`: phenix and minimega, the router is a **VyOS** VM (`vyos.qc2`) and
    the ACL is a VyOS 1.5 `firewall ipv4 forward filter` ruleset pushed through
    cc. The hosts are `cm-spike` guests with BACpypes3 in `/opt/bacnet`.
- `fuxa_probe.py`: a real FUXA 1.3.5 in Docker (node-bacnet 0.2.4) joined to
  the OPS subnet, provisioned with `../fuxa/secure_flow.sh` and
  `../fuxa/make_project.py`; the scenario also runs it (round 2 in the Result).
- `responder.py --broadcast-iam [--announce N]` makes the device answer a Who-Is
  (or announce itself) with a broadcast I-Am.
- Workflow jobs: `bbmd-netns` and `bbmd-vyos` in
  `.github/workflows/cherrymountain-spikes.yml`. They need Docker on the
  runner for FUXA (GitHub-hosted runners have it).

## Steps (what `scenario.py` does)

1. ACL stage `unicast-only` (established/related plus client to device
   UDP 47808): ReadProperty straight to 3003 works; a Who-Is broadcast finds
   nothing.
2. Start the BBMDs. Stage `bbmd-one-way` (adds ops-bbmd to bas-bbmd): record
   whether discovery works.
3. Stage `bbmd-both-ways` (adds bas-bbmd to ops-bbmd): record the result.
4. Stage `bbmd-both-ways-device-replies` (adds bas-dev to the client, UDP
   47808): Who-Is finds 3003, ReadProperty works. This is the pass criterion.
5. Back to `unicast-only`: discovery fails again, so the BBMD rules are what
   makes it work.

## Pass / fail

- [ ] Without BBMDs a Who-Is from OPS does not find 3003 (the problem exists).
- [ ] With a BBMD on each side and the ACL of stage 4, the Who-Is finds 3003 and
      ReadProperty returns `bas-vent` through the router.
- [ ] Removing the BBMD rules breaks discovery again.
- [ ] Same result on VyOS 1.5 as on the Linux router.

Fails on both routers: **no-go** for FUXA on OPS reading BAS devices by
discovery. Fall back to FUXA in the BAS broadcast domain (`bas-fuxa` only), or a
foreign-device registration the driver does not do.

## Result

Two rounds. Round 2 (real FUXA and devices that broadcast their I-Am) is first; round 1 (the socket stand-in client) follows unchanged.

### Round 2: real FUXA, and a device that broadcasts its I-Am

- **Decision:** **go.** A real FUXA 1.3.5 (`frangoteam/fuxa:1.3.5`, digest
  `sha256:931781ce0557304d5b3b51e3d2ddb180dd2252b5e4b9936be7fd935bf5c62a2a`,
  node-bacnet 0.2.4 installed through `POST /api/plugins`, BACnet device
  provisioned with `make_project.py` and `secure_flow.sh`) on the OPS subnet
  connects through the OPS BBMD and reads a live present value from device 3003
  behind the stateful default-drop ACL, on a VyOS VM and on a Linux router. Rule 3
  (BAS BBMD to OPS BBMD) is **needed only for unsolicited broadcast I-Ams** and
  is hit then; a Who-Is answered with a broadcast I-Am passes without it. Two
  FUXA behaviors change how the design should use BACnet (below).
- **Fixture.** FUXA runs in Docker on the runner. It starts on Docker's network
  (npm needs it for the driver), is provisioned through its API, then moves to
  the OPS subnet on a veth pair (a Linux bridge in the netns job; an OVS port on
  OPS's VLAN tag in the VyOS job) and loses Docker's interface, so its only path
  out is `fw-ot`. `fuxa_probe.py` does this; `scenario.py` runs the steps.
- **Evidence.**
  - VyOS router: run [42540790499](https://github.com/GhostofGoes/sceptre-phenix-topologies/actions/runs/42540790499), job "Spike bbmd (VyOS router)", artifact `evidence-bbmd-vyos`
    (VyOS `999.202611090036`, phenix `7b59270`, BACpypes3 0.0.110; all 14
    checks PASS). Linux router (netns): run [42543352420](https://github.com/GhostofGoes/sceptre-phenix-topologies/actions/runs/42543352420), job "Spike bbmd (Linux router,
    network namespaces)", artifact `evidence-bbmd-netns` (kernel
    7.0.0-1017-azure, nftables 1.0.9; all PASS). The counters below are
    identical on both routers.
  - **FUXA reads 3003.** Packet capture on the container (`fuxa-capture-full.txt`):
    FUXA sends its 8-byte Who-Is to `255.255.255.255:47808`; the OPS BBMD
    answers with its own I-Am (instance 9001) and **device 3003 answers with a
    unicast I-Am from 10.20.60.13 across the router**; FUXA then polls
    ReadPropertyMultiple (19 bytes) to 10.20.60.13 once a second and gets
    25-byte acks. `GET /api/getTagValue` returned 600, later 900 (VyOS) and
    600, later 1000 (netns): the responder's 400 to 1200 ppm ramp.
  - **ACL rules hit** (stage `bbmd-both-ways-device-replies`, ACL written for
    FUXA's address, counters from the router: VyOS `show firewall ipv4 forward
    filter`, nft counters): rule 10 (established) 18 packets; **rule 30 (OPS BBMD
    to BAS BBMD) 1**, the Forwarded-NPDU; **rule 50 (device 3003 to FUXA) 1**, the
    device's I-Am; rule 40 (BAS BBMD to OPS BBMD) 0; rule 20 (FUXA to device) 0,
    because the I-Am's connection-tracking entry (same port pair) already covers
    the reads; default drop 1 (probably the BAS BBMD's own I-Am, instance 9002, to FUXA; not
    captured on the BAS side).
  - **Without the BBMD rules** (stage `unicast-only`, FUXA reconnects): the tag has
    no value (`null`). The rule-20 counter shows 1 packet and the default drop 45
    (FUXA's polling and the forwarded Who-Is that the ACL stops).
  - **Rule 3 and a device that broadcasts its I-Am** (`responder.py
    --broadcast-iam`, stand-in client bound to 0.0.0.0, both routers):
    - Answering a Who-Is: found through the OPS BBMD (`via` 10.20.30.5) with
      only rules 20 and 30 and no rule 3: the Who-Is opened the OPS-to-BAS BBMD
      flow (UDP 47808 to 47808), so the forwarded I-Am is that flow's reply
      direction and the established rule passes it. Rule 40 matched 0.
    - Unsolicited (`--announce 3`, connection tracking flushed): with rule 3
      absent the client hears nothing (default drop 3 packets on netns, 6 on
      VyOS); with rule 3 it hears the I-Am through the OPS BBMD, reads the
      device, and **rule 40 matched 2 packets (netns) and 5 (VyOS)**.
    - A client bound to its own address (what FUXA does, see below) never hears
      the OPS BBMD's rebroadcast to 255.255.255.255, so devices that broadcast
      their I-Am are not found by FUXA even with rule 3 (stand-in
      `bound_to_own_address` and the FUXA run `fuxa_bcast_iam_device`: no value).
- **FUXA and node-bacnet behavior found** (read from the driver and shown by the
  captures):
  1. **The Who-Is goes to the device's `broadcastAddress` property.** Without it
     the address is 255.255.255.255 and the BBMDs are needed. With
     `broadcastAddress` set to the device's own IP (what `make_project.py` did
     by default) the Who-Is is a **unicast** to the device: FUXA reads 3003 with
     only rule 20 and the established rule, no BBMD, no device-to-client rule
     (value 700 on netns, 500 on VyOS, counters `est` 7 and 10, rule 20 1). The
     `bacnet` spike's statement that `whoIs()` always goes to 255.255.255.255 was
     true only for a project without that property.
  2. **FUXA reports "connected" on the first I-Am from anything**, including the
     OPS BBMD's own (instance 9001). A device that never answers leaves the tag
     `null` while FUXA says connected, and FUXA sends the Who-Is once, at connect:
     a device whose ACL rule appears later is not found until the runtime
     reconnects (post the project again). Use the tag value, not the connection
     state, as the health check.
  3. **A device FUXA has not discovered is polled by broadcast**: one
     ReadPropertyMultiple to 255.255.255.255 per second per tag group (a local run
     counted 157 forwarded packets on rule 30 in about two and a half minutes).
  4. node-bacnet binds the FUXA host's own address, so it hears unicast only.
- **ACL rules the design needs** (updates round 1): rules 1, 2 and 4 stand as
  listed there. **Rule 3 becomes: required only for devices that send
  unsolicited broadcast I-Ams (power-up, periodic), and FUXA cannot use those
  anyway**; for Who-Is answers it never fires. If every BAS device that FUXA
  reads is configured by IP (one FUXA device per BACnet device, `broadcastAddress`
  = its IP), no BBMD and only rules 4 and 2's reply direction (established) are
  needed: two rules instead of four, and no BBMD nodes.
- **Follow-ups:**
  - DESIGN §10.3, §10.4, §11 and §15.2: record the `broadcastAddress` option as
    the alternative to BBMDs and decide between them; state that rule 3 is for
    unsolicited I-Ams only and is untestable with FUXA as the client.
  - `make_project.py` writes `broadcastAddress` = device IP unless called with
    `--broadcast-address none`; the FUXA boot script must choose on purpose.
  - Decide whether one FUXA BACnet device per BAS device is acceptable (the
    unicast option needs it; tags keep `memaddress` = the device instance).
  - Not tested: BBMD on `ot-svc` itself, FUXA on the BAS side, foreign-device
    registration, FUXA restart or an ACL change while connected beyond what is
    listed.

### Round 1: socket stand-in client

- **Decision:** **go, with a BBMD on each side and more than the ACL the
  question assumed.** FUXA on OPS discovers BAS device 3003 across the
  firewall, and ReadProperty works. A stateful ACL that only allows "UDP 47808
  between the BBMDs and replies" is **not enough**: the device answers the
  Who-Is with a *new* BAS-to-OPS flow (see the rules below).
- **Router:** both a VyOS VM (the primary result) and a Linux router (network
  namespaces, nftables) gave identical results. VyOS was
  `VyOS 999.202611081848` (rolling, flavor `qemu`; see the vyos-ipsec spike for
  why rolling is not "1.5"), with `firewall ipv4 forward filter`, default
  action `drop`.
- **Evidence** (run [42513925243](https://github.com/GhostofGoes/sceptre-phenix-topologies/actions/runs/42513925243), jobs "Spike bbmd (VyOS router)" and
  "Spike bbmd (Linux router, network namespaces)", artifacts
  `evidence-bbmd-vyos` and `evidence-bbmd-netns`; BACpypes3 0.0.110; all
  checks PASS on both):
  - Without BBMDs, a node-bacnet-style Who-Is from ops-client finds nothing
    (only ops-bbmd, instance 9001, answers); a direct unicast ReadProperty to
    10.20.60.13 works through the ACL (`object-name = 'bas-vent'`).
  - The final run [42534059315](https://github.com/GhostofGoes/sceptre-phenix-topologies/actions/runs/42534059315) (phenix `041b001`, VyOS `999.202611082315` rebuilt without the NIC patch) passed both jobs again with the same results.
  - Stage `bbmd-one-way` (ops-bbmd to bas-bbmd allowed) and `bbmd-both-ways`
    (plus bas-bbmd to ops-bbmd): **discovery still fails.** The VyOS counters
    show why: rule 30 (OPS BBMD to BAS BBMD) matched 1 packet (46 bytes, the
    Forwarded-NPDU) and the default drop matched 2 (98 bytes): the device's
    I-Am.
  - Stage `bbmd-both-ways-device-replies` (adds rule 50, device 3003 to
    ops-client, UDP 47808): the Who-Is finds 3003 at 10.20.60.13 (the I-Am
    arrives from the device itself, not through a BBMD) and ReadProperty
    returns `bas-vent`. Counters: rule 30 1 packet, rule 50 1 packet (49 bytes),
    rule 10 (established) 2 packets, rule 40 (BAS BBMD to OPS BBMD) **0**.
  - Back to the unicast-only ACL: discovery fails again.
  - Why the device's reply is a new flow: BACpypes3 answers a Who-Is with a
    *unicast* I-Am to the request's source address. A BBMD forwards the Who-Is
    as a Forwarded-NPDU that carries the original sender's address, so the
    device replies straight to ops-client:47808 across the router. That is a
    BAS-to-OPS flow no BBMD-to-BBMD rule covers. (Devices that broadcast their
    I-Am would instead go through rule 40; not tested because the available
    stack does not do it.)
- **ACL rules the design needs** (replacing "scada-fuxa to BAS devices UDP
  47808" in DESIGN §10.3; all stateful, return traffic by established/related):
  1. `ops-bbmd` to `bas-bbmd`, UDP 47808 (Forwarded-NPDU, BDT and foreign
     device management).
  2. `bas-bbmd` to `ops-bbmd`, UDP 47808 (for devices that broadcast their
     I-Am). Not exercised by 3003 (0 packets); recommended, untested.
  3. BAS devices to the FUXA host (and the EWS), UDP 47808 (unicast I-Am,
     and COV or any device-initiated message).
  4. FUXA host (and the EWS) to BAS devices, UDP 47808 (ReadProperty,
     ReadPropertyMultiple, WriteProperty). Rule 20 matched 0 packets in the run
     because the I-Am's connection-tracking entry (same port pair, reversed)
     already covered the read; keep the rule for reads after that entry
     expires (not tested).
- **Other findings:**
  - A BACpypes3 node only listens for broadcasts on its subnet's directed
    broadcast address. FUXA's limited broadcast (255.255.255.255) is not heard
    by a BBMD started with a normal `/24` address: in a local namespace run the
    Who-Is found nothing. Start the OPS-side BBMD with a `/0` prefix
    (`--address 10.20.30.5/0:47808`), so its "broadcast address" is
    255.255.255.255. CI uses `/0` throughout; the `/24` failure is from a local
    run only, not CI.
  - BDT entries without a mask (`10.20.60.5:47808`) give two-hop delivery:
    the sending BBMD unicasts to the peer, which rebroadcasts locally. That is
    the mode that crosses a router that does not forward directed broadcasts.
  - A client bound to 0.0.0.0 and one bound to its own address both worked
    here (`client_bound_0000_result`), so the node-bacnet `interface` setting
    does not change the outcome.
  - The BBMD cannot run on the FUXA host: node-bacnet binds UDP 47808 there. Use
    separate BBMD nodes (a small Linux guest each, or a BBMD on `ot-svc` and on
    a BAS host).
  - VyOS 1.5-style syntax `state established` and `state related` (valueless)
    commits on the rolling build.
- **Follow-ups:**
  - Add the four rules and the two BBMD nodes (with BDTs listing both) to
    DESIGN §10.3, §11 and the BAS module; the `bacnet` spike's follow-up (FUXA
    connect needs a Who-Is broadcast) is resolved by this design.
  - Test real FUXA (node-bacnet 0.2.4 inside the FUXA container) against the
    BBMD instead of the socket stand-in, and a device that broadcasts its I-Am.
  - Decide whether Wazuh should alert on UDP 47808 between zones other than
    those four paths (BBMD traffic is a new lateral path).
