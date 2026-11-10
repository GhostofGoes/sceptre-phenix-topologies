# ot-sim BACnet module

`cm_otsim_bacnet.py` is a BACnet/IP server for ot-sim devices. ot-sim has
Modbus and DNP3 modules but no BACnet one, and the three building controllers
(`bas-rtu-taproom`, `bas-coldroom`, `bas-vent`) must serve BACnet to bas-fuxa
and, through the BBMDs, to scada-fuxa ([DESIGN.md §8.1](../../DESIGN.md#81-spaces-equipment-and-bacnet-devices),
[§16](../../DESIGN.md#16-new-and-extended-software)).

It needs no fork of ot-sim: the ot-sim CPU module starts it like any other
module, and it talks to the device's message bus.

```text
bas-fuxa ──BACnet/IP──> cm_otsim_bacnet.py ──Update──> message bus ──> ot-sim-io-module ──HELICS──> building federate
         <─────────────                    <──Status──             <──                  <────────
```

## Moving it upstream

A version of this module written for ot-sim itself is on the
`feat-bacnet-module` branch of the ot-sim fork (`src/python/otsim/bacnet`), and
the phenix `ot-sim` app's rendering for it is on the `feat-otsim-bacnet` branch
of the phenix-apps fork. Once both are upstream and in the range's pins, this
file can go. Two things differ and have to follow in the range first:

- Its settings are child elements (`<instance>`, `<tag>`, `<units>`), like the
  Modbus and DNP3 modules', not attributes.
- It does not send a null value on a relinquish. It sends the value from before
  the command, and with an `override-tag` set on the object, a second tag that
  is 1 while a command is in force and 0 after. The building and power federates
  would read that tag in place of the null.

## What it does

- **Objects.** A Device object and one analog-input, analog-output,
  analog-value, binary-input, binary-output, binary-value, or
  multi-state-value object per point, with its name, description, and units.
- **Values.** ot-sim's I/O module puts every tag on the message bus each
  HELICS step. Each object's present-value follows its tag.
- **Writes.** Only present-value of a `commandable` object can be written.
  Such an object has a priority array, as a real controller's outputs and
  setpoints do:
  - A command at any priority overrides the controller's own logic for that
    point. The building federate runs that logic.
  - Writing NULL to the last commanded priority relinquishes the point. The
    module then sends a null value, and the federate hands the point back to
    the controller's logic.
  - While nothing is commanded, present-value is the relinquish default, and
    the relinquish default follows the controller's own value.
  - A value outside the object's limits answers `value-out-of-range`.
- **Everything else is read-only.** A write to an input, to a value that is
  only a status, to any other property, or to the Device and Network Port
  objects answers `write-access-denied`. BACpypes3 alone accepts all of them.
- **Stale values.** Before its first value, and when its tag has had no value
  for 30 s, an object reports `reliability communication-failure` and sets the
  fault bit of its status flags. The last value stays readable.
- **Discovery.** It answers Who-Is with I-Am, including a Who-Is that a BBMD
  forwarded, which gets a unicast I-Am to the original sender.

## Settings

The module reads the `bacnet` element of `/etc/ot-sim/config.xml` and the
`message-bus` element beside it:

```xml
<bacnet name="bacnet-server" mode="server">
  <endpoint>10.30.10.22/24:47808</endpoint>
  <device instance="3003" name="BAS-VENT-1"/>
  <object type="analog-input" instance="1" name="FAC.VENT.AT01.PV"
          tag="bas-vent.co2_1_ppm" units="partsPerMillion"
          description="Cellar CO2 sensor 1 (low-mounted)"/>
  <object type="analog-output" instance="1" name="FAC.VENT.EF01.CMD"
          tag="bas-vent.exhaust_pct" units="percent" commandable="true"
          min="0" max="100"/>
  <object type="multi-state-value" instance="1" name="FAC.RTU1.HS01.MODE"
          tag="bas-rtu-taproom.occ_mode" commandable="true">
    <state>occupied</state><state>unoccupied</state><state>standby</state>
  </object>
</bacnet>
```

| Setting | Meaning |
|---|---|
| `endpoint` | The NIC's own address with its prefix and the port (default 47808). Not `0.0.0.0`: a BACpypes3 node hears broadcasts only on its subnet's directed broadcast address, and that is where the BBMD sends a forwarded Who-Is |
| `device` | The Device object's instance and name |
| `object` | `type`, `instance`, `name`, and `tag` (the message-bus tag) are required. `units` is a BACnet engineering-unit name. `commandable="true"` gives the object a priority array and lets present-value be written; every output must have it. `min` and `max` limit a written analog value. A multi-state object lists its `state` texts |
| `stale-after` | Seconds without a value before an object reports a fault (default 30; 0 turns it off) |
| `pull-endpoint`, `pub-endpoint` | The message bus, when it is not the one in `message-bus` |

`cm_otsim_bacnet.py --check <config.xml>` validates the settings and prints the
objects. Bad settings exit 101, which tells the CPU module not to restart the
module.

## How it gets onto a device

The phenix `ot-sim` app knows only Modbus and DNP3. For a host whose metadata
has only a `bacnet` block it renders a `config.xml` with no I/O module and no
protocol module, and it does not give the node the `helics/federate`
annotation. So the `cherrymountain` app, which runs after it, does three
things for every ot-sim host that `plant.yaml` gives a BACnet endpoint
(`render_otsim` in the app's `render.py`):

1. It adds the `io` element and its CPU module entry, built from
   `plant/generated/<variant>/helics.json` as the `ot-sim` app builds them for a
   Modbus device.
2. It adds the `bacnet` element, built from
   `plant/generated/<variant>/bacnet/<host>.json`, and the CPU module entry
   `/opt/otsim-venv/bin/python -I /usr/local/lib/cm-otsim/cm_otsim_bacnet.py {{config_file}}`.
3. It injects this file to `/usr/local/lib/cm-otsim/` and adds the
   `helics/federate` annotation, so the `helics` app starts the broker for
   these three federates too.

The `cm-otsim` image carries only what the module imports (BACpypes3, pydantic,
and the pyzmq that ot-sim's own Python modules use), so a change to the module
needs no image rebuild.

## Check it on a running range

On a BAS controller's console (root, no password):

```shell
journalctl -u ot-sim | grep bacnet-server      # "device 3003 ... with 21 objects", then "first values from the message bus"
ss -lnu 'sport = :47808'                       # the node's address and its broadcast address
/opt/otsim-venv/bin/python -I /usr/local/lib/cm-otsim/cm_otsim_bacnet.py --check /etc/ot-sim/config.xml
```

From the EWS, with BAC0 (run it with `/opt/ews-venv/bin/python`):

```python
import asyncio, BAC0

async def main():
    async with BAC0.start(ip="10.20.30.12/24") as bacnet:
        print(await bacnet.read("10.30.10.22 analogInput 1 presentValue"))         # cellar CO2 sensor 1, ppm
        await bacnet._write("10.30.10.22 binaryOutput 1 presentValue active - 8")  # purge fan 1 on, priority 8
        await asyncio.sleep(15)                                                    # one building-federate step is 10 s
        print(await bacnet.read("10.30.10.22 binaryInput 1 presentValue"))         # fan 1 status: active
        await bacnet._write("10.30.10.22 binaryOutput 1 presentValue null - 8")    # give the fan back

asyncio.run(main())
```

`_write` is BAC0's awaitable write; its `write` only queues the request for
later. These read and write forms were run with BAC0 2026.7.25, the version on
the EWS, against this module on loopback.

The EWS reaches the BAS devices by unicast through `fw-ot`. A write from the
EWS is what the Suricata and Zeek BACnet-write detections watch for
([DESIGN.md §14](../../DESIGN.md#14-blue-team-monitoring)).

## Tests

`tests/bacnet/` runs the module against a real BACpypes3 client and real
ZeroMQ sockets on loopback (the `cm-bacnet-tests` hook):

```shell
python -m pytest cherrymountain-brewery/tests/bacnet
```

The app's `tests/test_bacnet.py` runs the real `ot-sim` app, then the
`cherrymountain` app, then the real `helics` app on the recorded full
experiment, and checks that every rendered `bacnet` element loads in the
module.

Run once by hand, not in CI: the whole loop on loopback with a HELICS broker,
the building federate, ot-sim's own `ot-sim-io-module`, this module, and a
BACnet client. A command at priority 16 reached the federate and the fan
status followed one federate step later; a NULL write gave the point back.

Not run yet: the module under `ot-sim.service` on a VM, and a real FUXA
reading it through the BBMDs (the `bbmd` spike did that with a BACpypes3
stand-in).
