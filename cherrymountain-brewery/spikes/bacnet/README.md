# Spike: BACnet module and FUXA node-bacnet

**Question.** Can an ot-sim-hosted BACnet device (new module, or a stand-in)
answer ReadProperty for device 3003 (`bas-vent`) and be read by FUXA's
`node-bacnet` driver, over UDP 47808? **Decides:** build the ot-sim BACnet
module, or use the documented bennu `hvac` fallback (DESIGN §8.1, §15.2).

## Prerequisites

- A BACnet/IP responder with device instance 3003, objectName set, and at
  least one AI (CO2) object. Options in order of preference: the ot-sim
  BACnet module proof of concept; the BACpypes3 stand-in `responder.py` in
  this directory (`pip install bacpypes3`; derived from the upstream
  `samples/mini-device-revisited.py`, which is not in the PyPI sdist).
- A client in the same broadcast domain or reaching it unicast (BACnet
  broadcasts do not cross VyOS; use unicast Read, so no BBMD needed).
- `bas-fuxa` from the `fuxa` spike, with `node-bacnet` installed.

## Steps

1. Start the responder on IP `10.20.60.13`, UDP 47808:
   `./responder.py --name bas-vent --instance 3003 --address 10.20.60.13/24:47808`
   (it binds the given interface address only; probing `127.0.0.1` gets no reply).
2. From a client: `./readprop_raw.py 10.20.60.13`. It must print the device
   object-name (the same frame the SoH probe sends, DESIGN §15.4).
3. Cross-check with BAC0: `./bac0_read.py 10.20.60.99/24 10.20.60.13`.
4. On the FUXA VM: `node node_bacnet_probe.js 10.20.60.13` (run from
   `server/` so `node-bacnet` resolves).
5. In the FUXA editor add a BACnet client device and one tag for
   `analogInput` 0 `presentValue`. Confirm a live value. At FUXA 1.3.5 the
   device `property.address` is the FUXA host's own interface IP (not the
   remote device), the tag `address` is `<objectType>-<instance>` (`0-0`) and
   `memaddress` is the remote device instance (`3003`); `../fuxa/make_project.py`
   writes such a device and `../fuxa/secure_flow.sh` posts it.
6. Optional: capture on the client (`tcpdump -ni any udp port 47808`) to
   confirm FUXA sends unicast ReadProperty/ReadPropertyMultiple only, and
   check which one the ot-sim module must support.
7. Note which services the responder had to implement for steps 2-5
   (ReadProperty, ReadPropertyMultiple, WhoIs/IAm, SubscribeCOV).

## Pass / fail

- [x] `readprop_raw.py` PASS (ComplexACK with object-name) against the
      responder.
- [x] `node_bacnet_probe.js` PASS.
- [x] FUXA shows a live changing value for an AI object.
- [x] The list of services FUXA requires (step 6-7) is small enough to build
      in the module in the Phase 2 budget.

All pass: **go** (BACnet module). Steps 1-2 only: go for Wazuh/Malcolm
detection but keep scope; FUXA step failing: investigate client config before
calling no-go. Responder needs services you will not build: **no-go**, use
bennu `hvac`.

## Result

Run in Docker (python:3.12-slim containers on one bridge network; no VMs).

- **Decision:** go for the BACnet module scope (BACpypes3 stand-in answers
  everything FUXA calls), with one blocking caveat for `scada-fuxa`: FUXA
  1.3.5 cannot connect to a BACnet device it cannot reach by broadcast
  Who-Is. See Follow-ups. Status: pass for the checklist, caveat recorded.
- **Versions:** bacpypes3 0.0.110 (PyPI; samples from
  `JoelBender/bacpypes3` commit `bd24f51508f617fdcd64b606e023fe476b85c886`),
  BAC0 2026.7.25, node-bacnet 0.2.4 (the version FUXA 1.3.5 pins), Python
  3.12 (`python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b`).
- **Evidence:**
  - `readprop_raw.py 172.18.0.2` -> `reply from ('172.18.0.2', 47808):
    810a001d010030010c0c02000bbb194d3e7509006261732d76656e743f` then `PASS:
    device 3003 object-name = 'bas-vent'`. The built frame equals the DESIGN
    §15.4 packet (the script's assert passed).
  - `bac0_read.py 172.18.0.3/16 172.18.0.2 3003` -> `bas-vent`, `800.0`,
    `PASS`. Two fixes: `presentValue` must be read from `analogInput 0`
    (BAC0 raises `UnknownPropertyError` for device/presentValue), and the
    asyncio API (`async with BAC0.start(ip=...)`, awaitable `read`) is right
    for this version.
  - `node_bacnet_probe.js 172.18.0.2` run inside the FUXA container ->
    `PASS readProperty object-name: [{"type":7,"value":"bas-vent",...}]`,
    `PASS readPropertyMultiple AI0 presentValue ... "value":600`,
    `PASS iAm (unicast whoIs)`. The callback-style API assumed in the old
    script is correct for 0.2.4.
  - **FUXA's own driver** (FUXA 1.3.5, BACnet device + tag `0-0`,
    `memaddress` 3003) read the CO2 value live through
    `GET /api/getTagValue`: 600, 700, ... 1200 as the responder ramped.
  - **Services FUXA calls** (`server/runtime/devices/bacnet/index.js`):
    Who-Is/I-Am, ReadProperty (object-name, object-list for browsing),
    ReadPropertyMultiple (present-value polling), WriteProperty (present-value,
    priority 16). No SubscribeCOV. BACpypes3 handled all of them with no code.
- **Follow-ups:**
  - **Resolved and corrected by the [bbmd](../bbmd/README.md) spike (round 2,
    real FUXA through BBMDs on a VyOS router).** FUXA's Who-Is goes to its
    device `broadcastAddress` property: 255.255.255.255 when the property is
    absent (the case below), a unicast to the device when it holds the device's
    IP. The statement below that `whoIs()` always broadcasts held only for a
    project without that property.
  - **FUXA connect needs an I-Am from a Who-Is broadcast.** `_connect()`
    sends `client.whoIs()` and resolves only on an `iAm` event; the
    "explicit" second call `whoIs({deviceIPAddress: <broadcastAddress>})` is
    not honored by node-bacnet 0.2.4. tcpdump showed it goes to
    `255.255.255.255:47808`, while `whoIs({address: ip})` or `whoIs(ip)` does
    unicast. A limited broadcast does not cross VyOS, so a FUXA on another
    subnet than the BACnet device never connects, even though unicast
    ReadProperty works. The DESIGN firewall table routes scada-fuxa to the BAS devices
    (UDP 47808 through fw-ot); that path needs a different answer (FUXA in the BAS
    broadcast domain, a BBMD/foreign-device registration the driver does not
    do, a device that sends a unicast I-Am to FUXA, or reading CO2 through
    another protocol). Not tested across a router here.
  - `property.address` of a FUXA BACnet device must be an IP that exists on
    the FUXA host (checked against its interfaces); `broadcastAddress` is
    optional.
  - The responder binds only the interface address passed to `--address`;
    BACpypes3's `--debug` needs a registered logger name, so the script does
    not use it.
  - Not run: the ot-sim BACnet module itself (does not exist yet), COV, and
    a BBMD.
