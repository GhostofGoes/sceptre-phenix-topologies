"""The ot-sim BACnet module (phenix-injects/otsim/cm_otsim_bacnet.py) against
a real BACpypes3 client and real ZeroMQ sockets, all on loopback."""

from __future__ import annotations

import asyncio
import socket
import struct
import xml.etree.ElementTree as ET
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
from bacnet_lab import CONFIG_XML, Lab, lab, module, status_envelope

DEVICE: str = "device,3003"


def run(test: Callable[[Lab], Awaitable[None]], **settings: float) -> None:
    async def body() -> None:
        async with lab(**settings) as bench:
            await test(bench)

    asyncio.run(asyncio.wait_for(body(), 60))


def config(text: str | None = None, **changes: str) -> Any:
    xml: str = text or CONFIG_XML.format(pull=1, pub=2, port=47808, stale=30)
    for old, new in changes.items():
        assert old in xml, old
        xml = xml.replace(old, new)
    return module.BacnetConfig.from_xml(ET.fromstring(xml))  # noqa: S314 - our own text


# ---------------------------------------------------------------- settings


def test_the_settings_come_from_the_bacnet_element() -> None:
    cfg = config()
    assert cfg.endpoint == "127.0.0.1/8:47808"
    assert (cfg.device_instance, cfg.device_name) == (3003, "BAS-VENT-1")
    assert (cfg.pull_endpoint, cfg.pub_endpoint) == ("tcp://127.0.0.1:1", "tcp://127.0.0.1:2")
    by_tag = {o.tag: o for o in cfg.objects}
    assert by_tag["bas-vent.exhaust_pct"].commandable and by_tag["bas-vent.exhaust_pct"].maximum == 100
    assert by_tag["bas-coldroom.setpoint_f"].commandable
    assert by_tag["bas-rtu-taproom.occ_mode"].states == ("occupied", "unoccupied", "standby")
    assert not by_tag["bas-vent.co2_1_ppm"].commandable and not by_tag["bas-vent.co2_tier_1"].commandable


def test_the_port_defaults_to_47808() -> None:
    assert config(**{"127.0.0.1/8:47808": "10.30.10.22/24"}).endpoint == "10.30.10.22/24:47808"


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ("127.0.0.1/8:47808", "0.0.0.0/0:47808", "not 0.0.0.0"),
        ("127.0.0.1/8:47808", "10.30.10.22:47808", "ip/prefix"),
        ('instance="3003"', 'instance="4194303"', "device_instance"),
        ('tag="bas-vent.co2_1_ppm"', 'tag="bas-vent.exhaust_pct"', "duplicate tag"),
        ('name="FAC.VENT.AT01.PV"', 'name="FAC.VENT.EF01.CMD"', "duplicate object name"),
        (
            'type="analog-input" instance="1" name="FAC.VENT.AT01.PV"',
            'type="analog-input" commandable="true" instance="1" name="FAC.VENT.AT01.PV"',
            "input object cannot be commandable",
        ),
        ('units="percent" commandable="true" min="0" max="100"', 'units="percent"', "output object must be commandable"),
        ('min="30" max="45"', 'min="45" max="30"', "min is above max"),
        ("<state>occupied</state><state>unoccupied</state><state>standby</state>", "", "needs its states"),
        ('units="percent"', 'units="percent" colour="red"', "colour"),
    ],
)
def test_bad_settings_are_refused(old: str, new: str, message: str) -> None:
    with pytest.raises(module.ConfigError, match=message):
        config(**{old: new})


def test_a_config_without_the_element_is_an_error() -> None:
    with pytest.raises(module.ConfigError, match="no <bacnet> element"):
        config("<ot-sim><cpu/></ot-sim>")


def test_bad_settings_exit_without_a_restart(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """ot-sim's CPU module restarts a module that dies, unless it exits 101."""
    path = tmp_path / "config.xml"
    path.write_text("<ot-sim><cpu/></ot-sim>")
    assert module.main([str(path)]) == module.EXIT_NO_RESTART
    assert module.main([str(tmp_path / "missing.xml")]) == module.EXIT_NO_RESTART
    assert module.main([]) == module.EXIT_NO_RESTART
    assert "bad settings" in capsys.readouterr().err


def test_check_prints_the_objects(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "config.xml"
    path.write_text(CONFIG_XML.format(pull=1, pub=2, port=47808, stale=30))
    assert module.main(["--check", str(path)]) == 0
    out = capsys.readouterr().out
    assert "device 3003 BAS-VENT-1 on 127.0.0.1/8:47808" in out
    assert "binary-output,1 FAC.VENT.FAN01.CMD <- bas-vent.purge_fan_1 (commandable)" in out
    assert "analog-input,1 FAC.VENT.AT01.PV <- bas-vent.co2_1_ppm (read-only)" in out


# ---------------------------------------------------------------- the bus


def test_envelopes_match_ot_sims_format() -> None:
    """The Update is ot-sim's envelope (msgbus/envelope.py new_update_envelope),
    and a Status from its I/O module parses; other messages are ignored."""
    import json

    update = json.loads(module.update_envelope("bacnet-server", "bas-vent.purge_fan_1", 1.0))
    assert update == {
        "version": "v1",
        "kind": "Update",
        "metadata": {"sender": "bacnet-server"},
        "contents": {
            "updates": [{"tag": "bas-vent.purge_fan_1", "value": 1.0, "ts": 0}],
            "recipient": "",
            "confirm": "",
        },
    }
    assert json.loads(module.update_envelope("x", "t", None))["contents"]["updates"][0]["value"] is None
    assert module.status_points(status_envelope({"a.b": 1, "c.d": 2.5})) == [("a.b", 1.0), ("c.d", 2.5)]
    assert module.status_points(module.update_envelope("x", "t", 1.0)) == []
    assert module.status_points(b"not json") == []
    assert module.status_points(b'{"kind": "Status", "contents": {"measurements": [{"tag": "t", "value": null}]}}') == []


def test_a_value_helics_has_not_received_yet_is_no_measurement() -> None:
    """Until the federate publishes, HELICS hands the I/O module -1e49 for a
    double, and the I/O module puts it on the bus. A BACnet Real cannot hold
    it: the object must keep its fault flag, not fail the read."""

    async def test(bench: Lab) -> None:
        await bench.pub.send_multipart((b"RUNTIME", status_envelope({"bas-vent.co2_1_ppm": -1e49})))
        await bench.status({"bas-vent.co2_tier_1": 0})  # a later message has arrived, so the first has too
        assert bench.server.points["bas-vent.co2_1_ppm"].seen is None
        assert await bench.read("analog-input,1") == 0.0
        assert str(await bench.read("analog-input,1", "reliability")) == "communication-failure"
        await bench.status({"bas-vent.co2_1_ppm": 525.0})
        assert await bench.read("analog-input,1") == 525.0

    run(test)


def test_the_real_ot_sim_classes_agree_on_the_wire() -> None:
    """With ot-sim's Python package installed (it is in the cm-otsim image,
    not in CI), its own Pusher and Subscriber carry the module's messages."""
    envelope = pytest.importorskip("otsim.msgbus.envelope")
    import json

    theirs = envelope.new_update_envelope(
        "bacnet-server", {"updates": [{"tag": "t", "value": 1.0, "ts": 0}]}
    )
    assert json.loads(module.update_envelope("bacnet-server", "t", 1.0)) == theirs
    status = envelope.new_status_envelope("io", {"measurements": [{"tag": "t", "value": 2.0, "ts": 0}]})
    assert module.status_points(json.dumps(status).encode()) == [("t", 2.0)]


# ---------------------------------------------------------------- reads


def test_present_value_follows_the_bus() -> None:
    async def test(bench: Lab) -> None:
        # Before the first Status every object says its value is not real.
        assert str(await bench.read("analog-input,1", "reliability")) == "communication-failure"
        assert (await bench.read("analog-input,1", "status-flags"))[1] == 1
        await bench.status(
            {
                "bas-vent.co2_1_ppm": 812.5,
                "bas-vent.purge_fan_1_status": 1,
                "bas-vent.co2_tier_1": 1,
                "bas-rtu-taproom.occ_mode": 2,
                "bas-coldroom.setpoint_f": 36,
                "some-other.tag": 5,
            }
        )
        assert await bench.read("analog-input,1") == 812.5
        assert str(await bench.read("binary-input,1")) == "active"
        assert str(await bench.read("binary-value,1")) == "active"
        assert await bench.read("multi-state-value,1") == 2
        assert await bench.read("analog-value,1") == 36.0
        assert str(await bench.read("analog-input,1", "reliability")) == "no-fault-detected"
        assert list(await bench.read("analog-input,1", "status-flags")) == [0, 0, 0, 0]
        await bench.status({"bas-vent.co2_1_ppm": 5100.0, "bas-vent.purge_fan_1_status": 0})
        assert await bench.read("analog-input,1") == 5100.0
        assert str(await bench.read("binary-input,1")) == "inactive"

    run(test)


def test_the_descriptive_properties_fuxa_and_an_engineer_read() -> None:
    async def test(bench: Lab) -> None:
        from bacpypes3.primitivedata import ObjectIdentifier

        assert await bench.read(DEVICE, "object-name") == "BAS-VENT-1"
        assert await bench.read("analog-input,1", "object-name") == "FAC.VENT.AT01.PV"
        assert await bench.read("analog-input,1", "description") == "Cellar CO2 sensor 1"
        assert str(await bench.read("analog-input,1", "units")) == "parts-per-million"
        assert await bench.read("multi-state-value,1", "number-of-states") == 3
        assert list(await bench.read("multi-state-value,1", "state-text")) == ["occupied", "unoccupied", "standby"]
        assert await bench.read("analog-value,1", "max-pres-value") == 45.0
        # FUXA browses with object-list and polls with ReadPropertyMultiple.
        listed = {(str(kind), instance) for kind, instance in await bench.read(DEVICE, "object-list")}
        assert listed == {
            ("device", 3003), ("network-port", 1), ("analog-input", 1), ("analog-output", 1),
            ("analog-value", 1), ("binary-input", 1), ("binary-output", 1), ("binary-value", 1),
            ("multi-state-value", 1),
        }  # fmt: skip
        await bench.status({"bas-vent.co2_1_ppm": 640.0})
        rows = await bench.client.read_property_multiple(
            bench.address,
            [ObjectIdentifier("analog-input,1"), ["present-value", "object-name"], ObjectIdentifier("binary-output,1"), ["present-value"]],
        )
        assert [(str(o[0]), str(p), v if not hasattr(v, "attr") else str(v)) for o, p, _, v in rows] == [
            ("analog-input", "present-value", 640.0),
            ("analog-input", "object-name", "FAC.VENT.AT01.PV"),
            ("binary-output", "present-value", "inactive"),
        ]

    run(test)


def test_a_stale_tag_reports_a_fault() -> None:
    async def test(bench: Lab) -> None:
        await bench.status({"bas-vent.co2_1_ppm": 700.0})
        assert (await bench.read("analog-input,1", "status-flags"))[1] == 0
        await asyncio.sleep(1.6)  # stale-after is 0.5 s; the watch runs every second
        assert str(await bench.read("analog-input,1", "reliability")) == "communication-failure"
        assert (await bench.read("analog-input,1", "status-flags"))[1] == 1
        assert await bench.read("analog-input,1") == 700.0  # the last value stays
        await bench.status({"bas-vent.co2_1_ppm": 705.0})
        assert (await bench.read("analog-input,1", "status-flags"))[1] == 0

    run(test, stale=0.5)


# ---------------------------------------------------------------- writes


def test_a_command_overrides_the_controller_until_it_is_relinquished() -> None:
    async def test(bench: Lab) -> None:
        from bacpypes3.primitivedata import Null

        fan = "bas-vent.purge_fan_1"
        await bench.status({fan: 0})
        assert str(await bench.read("binary-output,1")) == "inactive"
        # FUXA writes present-value at priority 16.
        assert await bench.write("binary-output,1", "active", priority=16) is None
        assert await bench.update() == {"tag": fan, "value": 1.0}
        assert str(await bench.read("binary-output,1")) == "active"
        assert (await bench.read("binary-output,1", "current-command-priority")).unsigned == 16
        # The controller's own logic still says off: the command wins.
        await bench.status({fan: 0})
        assert str(await bench.read("binary-output,1")) == "active"
        # A higher priority takes over, and giving it up falls back to 16.
        assert await bench.write("binary-output,1", "inactive", priority=8) is None
        assert await bench.update() == {"tag": fan, "value": 0.0}
        assert await bench.write("binary-output,1", Null(()), priority=8) is None
        assert await bench.update() == {"tag": fan, "value": 1.0}
        # Relinquishing the last command hands the point back: a null Update.
        assert await bench.write("binary-output,1", Null(()), priority=16) is None
        assert await bench.update() == {"tag": fan, "value": None}
        assert str(await bench.read("binary-output,1")) == "inactive"
        assert (await bench.read("binary-output,1", "current-command-priority")).unsigned is None
        # ... and the controller's value shows through again.
        await bench.status({fan: 1})
        assert str(await bench.read("binary-output,1")) == "active"
        assert str(await bench.read("binary-output,1", "relinquish-default")) == "active"

    run(test)


def test_an_analog_command_keeps_its_limits() -> None:
    async def test(bench: Lab) -> None:
        await bench.status({"bas-vent.exhaust_pct": 20})
        assert await bench.write("analog-output,1", 75.0, priority=8) is None
        assert await bench.update() == {"tag": "bas-vent.exhaust_pct", "value": 75.0}
        assert await bench.read("analog-output,1") == 75.0
        assert await bench.write("analog-output,1", 140.0, priority=8) == ("property", "value-out-of-range")
        assert await bench.write("analog-output,1", -1.0) == ("property", "value-out-of-range")
        assert await bench.read("analog-output,1") == 75.0
        await bench.no_update()

    run(test)


def test_a_setpoint_is_commanded_and_relinquished_like_an_output() -> None:
    """A value object that takes writes (a setpoint) has a priority array too:
    the controller's own setpoint is its relinquish default."""

    async def test(bench: Lab) -> None:
        from bacpypes3.primitivedata import Null

        tag = "bas-coldroom.setpoint_f"
        await bench.status({tag: 36})
        assert await bench.write("analog-value,1", 38.0) is None  # no priority means 16
        assert await bench.update() == {"tag": tag, "value": 38.0}
        # The federate has not applied it yet and still publishes 36.
        await bench.status({tag: 36})
        assert await bench.read("analog-value,1") == 38.0
        assert await bench.write("analog-value,1", 80.0) == ("property", "value-out-of-range")
        assert await bench.write("analog-value,1", Null(()), priority=16) is None
        assert await bench.update() == {"tag": tag, "value": None}
        assert await bench.read("analog-value,1") == 36.0

    run(test)


def test_a_multi_state_write_must_name_a_state() -> None:
    async def test(bench: Lab) -> None:
        await bench.status({"bas-rtu-taproom.occ_mode": 2})
        assert await bench.write("multi-state-value,1", 3) is None
        assert await bench.update() == {"tag": "bas-rtu-taproom.occ_mode", "value": 3.0}
        assert await bench.write("multi-state-value,1", 7) == ("property", "value-out-of-range")
        assert await bench.write("multi-state-value,1", 0) == ("property", "value-out-of-range")
        assert await bench.read("multi-state-value,1") == 3
        from bacpypes3.primitivedata import Null

        assert await bench.write("multi-state-value,1", Null(()), priority=16) is None
        assert await bench.update() == {"tag": "bas-rtu-taproom.occ_mode", "value": None}
        assert await bench.read("multi-state-value,1") == 2

    run(test)


def test_everything_else_refuses_a_write() -> None:
    """BACpypes3 alone accepts all of these; a device does not."""

    async def test(bench: Lab) -> None:
        await bench.status({"bas-vent.co2_1_ppm": 800.0, "bas-vent.co2_tier_1": 1})
        denied = ("property", "write-access-denied")
        assert await bench.write("analog-input,1", 5.0) == denied
        assert await bench.write("binary-input,1", "active") == denied
        assert await bench.write("binary-value,1", "inactive") == denied
        assert await bench.write("analog-input,1", "pwned", prop="object-name") == denied
        assert await bench.write("analog-value,1", "pwned", prop="description") == denied
        assert await bench.write("binary-output,1", "active", prop="relinquish-default") == denied
        assert await bench.write("analog-output,1", True, prop="out-of-service") == denied
        assert await bench.write(DEVICE, "pwned", prop="object-name") == denied
        assert await bench.write("network-port,1", "pwned", prop="object-name") == denied
        assert await bench.read("analog-input,1") == 800.0
        assert str(await bench.read("binary-value,1")) == "active"
        assert await bench.read(DEVICE, "object-name") == "BAS-VENT-1"
        await bench.no_update()

    run(test)


def test_write_property_multiple_takes_the_same_path() -> None:
    async def test(bench: Lab) -> None:
        from bacpypes3.apdu import (
            ErrorRejectAbortNack,
            WriteAccessSpecification,
            WritePropertyMultipleRequest,
        )
        from bacpypes3.basetypes import PropertyValue
        from bacpypes3.constructeddata import Any as BacnetAny
        from bacpypes3.primitivedata import ObjectIdentifier, Real

        def request(obj: str, value: float) -> Any:
            return WritePropertyMultipleRequest(
                listOfWriteAccessSpecs=[
                    WriteAccessSpecification(
                        objectIdentifier=ObjectIdentifier(obj),
                        listOfProperties=[
                            PropertyValue(propertyIdentifier="present-value", value=BacnetAny(Real(value)), priority=8)
                        ],
                    )
                ],
                destination=bench.address,
            )

        await bench.status({"bas-vent.exhaust_pct": 20, "bas-vent.co2_1_ppm": 800.0})
        await bench.client.request(request("analog-output,1", 60.0))
        assert await bench.update() == {"tag": "bas-vent.exhaust_pct", "value": 60.0}
        with pytest.raises(ErrorRejectAbortNack):
            await bench.client.request(request("analog-input,1", 1.0))
        assert await bench.read("analog-input,1") == 800.0
        await bench.no_update()

    run(test)


# ---------------------------------------------------------------- discovery


def test_who_is_gets_an_i_am() -> None:
    async def test(bench: Lab) -> None:
        (i_am,) = await bench.client.who_is(address=bench.address)
        assert i_am.iAmDeviceIdentifier[1] == 3003
        assert await bench.client.who_is(3004, 3010, address=bench.address) == []

    run(test)


def test_a_who_is_forwarded_by_the_bbmd_gets_a_unicast_i_am() -> None:
    """scada-fuxa's Who-Is reaches the BAS subnet as a Forwarded-NPDU that
    bas-bbmd sends to the subnet's directed broadcast address (DESIGN.md
    10.3). The device must hear it there and answer the original sender."""

    async def test(bench: Lab) -> None:
        loop = asyncio.get_running_loop()
        origin = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)  # scada-fuxa
        origin.bind(("127.0.0.3", 0))
        origin.setblocking(False)
        bbmd = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        bbmd.bind(("127.0.0.4", 0))
        bbmd.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        try:
            who_is = bytes([0x01, 0x20, 0xFF, 0xFF, 0x00, 0xFF, 0x10, 0x08])  # global broadcast Who-Is
            source = socket.inet_aton("127.0.0.3") + struct.pack(">H", origin.getsockname()[1])
            frame = bytes([0x81, 0x04]) + struct.pack(">H", 4 + 6 + len(who_is)) + source + who_is
            bbmd.sendto(frame, ("127.255.255.255", bench.port))
            reply, sender = await asyncio.wait_for(loop.sock_recvfrom(origin, 1500), 3)
        finally:
            origin.close()
            bbmd.close()
        assert sender == ("127.0.0.1", bench.port)
        assert reply[:2] == bytes([0x81, 0x0A])  # Original-Unicast-NPDU
        # Unconfirmed I-Am, then the device object identifier (device 3003).
        marker = reply.index(bytes([0x10, 0x00, 0xC4]))
        assert struct.unpack(">I", reply[marker + 3 : marker + 7])[0] == (8 << 22) | 3003

    run(test)
