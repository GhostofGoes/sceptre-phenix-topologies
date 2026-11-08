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
- Workflow jobs: `bbmd-netns` and `bbmd-vyos` in
  `.github/workflows/cherrymountain-spikes.yml`.

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
