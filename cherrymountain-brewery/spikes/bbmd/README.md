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

- **Decision:** (filled in after the runs)
- **Router:**
- **Evidence:**
- **Follow-ups:**
