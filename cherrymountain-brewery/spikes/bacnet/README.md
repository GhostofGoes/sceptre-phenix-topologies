# Spike: BACnet module and FUXA node-bacnet

**Question.** Can an ot-sim-hosted BACnet device (new module, or a stand-in)
answer ReadProperty for device 3003 (`bas-vent`) and be read by FUXA's
`node-bacnet` driver, over UDP 47808? **Decides:** build the ot-sim BACnet
module, or use the documented bennu `hvac` fallback (DESIGN §8.1, §15.2).

## Prerequisites

- A BACnet/IP responder with device instance 3003, objectName set, and at
  least one AI (CO2) object. Options in order of preference: the ot-sim
  BACnet module proof of concept; any BACpypes3 sample device bound to the
  BAS VLAN address (for example `pip install bacpypes3` and its sample
  server, TODO(verify): sample name for the installed version).
- A client in the same broadcast domain or reaching it unicast (BACnet
  broadcasts do not cross VyOS; use unicast Read, so no BBMD needed).
- `bas-fuxa` from the `fuxa` spike, with `node-bacnet` installed.

## Steps

1. Start the responder on IP `10.20.60.13`, UDP 47808.
2. From a client: `./readprop_raw.py 10.20.60.13`. It must print the device
   object-name (the same frame the SoH probe sends, DESIGN §15.4).
3. Cross-check with BAC0: `./bac0_read.py 10.20.60.99/24 10.20.60.13`.
4. On the FUXA VM: `node node_bacnet_probe.js 10.20.60.13` (run from
   `server/` so `node-bacnet` resolves).
5. In the FUXA editor add a BACnet client device (IP, device 3003, port
   47808) and one tag for `analogInput,0 presentValue`. Confirm a live value.
   TODO(verify): FUXA BACnet tag address syntax at the pinned release.
6. Optional: capture on the client (`tcpdump -ni any udp port 47808`) to
   confirm FUXA sends unicast ReadProperty/ReadPropertyMultiple only, and
   check which one the ot-sim module must support.
7. Note which services the responder had to implement for steps 2-5
   (ReadProperty, ReadPropertyMultiple, WhoIs/IAm, SubscribeCOV).

## Pass / fail

- [ ] `readprop_raw.py` PASS (ComplexACK with object-name) against the
      responder.
- [ ] `node_bacnet_probe.js` PASS.
- [ ] FUXA shows a live changing value for an AI object.
- [ ] The list of services FUXA requires (step 6-7) is small enough to build
      in the module in the Phase 2 budget.

All pass: **go** (BACnet module). Steps 1-2 only: go for Wazuh/Malcolm
detection but keep scope; FUXA step failing: investigate client config before
calling no-go. Responder needs services you will not build: **no-go**, use
bennu `hvac`.

## Result

- **Decision:** go (ot-sim BACnet module) / no-go (bennu hvac)
- **Evidence:** (raw reply hex, FUXA screenshot, service list)
- **Follow-ups:** (supported services, object types needed, tag syntax)
