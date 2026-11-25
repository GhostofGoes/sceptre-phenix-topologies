"""A loopback lab for the ot-sim BACnet module: the module itself, a stand-in
for ot-sim's message bus on real ZeroMQ sockets, and a BACpypes3 client.

A module of its own (not a conftest.py), for the reason tests/soc/soc_paths.py
gives. Nothing here needs ot-sim, HELICS, or a network beyond loopback.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import socket
import sys
import xml.etree.ElementTree as ET
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

ROOT: Path = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "phenix-injects" / "otsim"))

import cm_otsim_bacnet as module  # noqa: E402 - after the path

TOPIC: bytes = b"RUNTIME"
#: One of every object kind and access the BAS controllers use.
CONFIG_XML: str = """\
<ot-sim>
  <message-bus>
    <pull-endpoint>tcp://127.0.0.1:{pull}</pull-endpoint>
    <pub-endpoint>tcp://127.0.0.1:{pub}</pub-endpoint>
  </message-bus>
  <bacnet name="bacnet-server" mode="server">
    <endpoint>127.0.0.1/8:{port}</endpoint>
    <device instance="3003" name="BAS-VENT-1"/>
    <stale-after>{stale}</stale-after>
    <object type="analog-input" instance="1" name="FAC.VENT.AT01.PV" tag="bas-vent.co2_1_ppm"
            units="partsPerMillion" description="Cellar CO2 sensor 1"/>
    <object type="analog-output" instance="1" name="FAC.VENT.EF01.CMD" tag="bas-vent.exhaust_pct"
            units="percent" commandable="true" min="0" max="100"/>
    <object type="analog-value" instance="1" name="FAC.CR.TIC01.SP" tag="bas-coldroom.setpoint_f"
            units="degreesFahrenheit" commandable="true" min="30" max="45"/>
    <object type="binary-input" instance="1" name="FAC.VENT.FAN01.PV" tag="bas-vent.purge_fan_1_status"/>
    <object type="binary-output" instance="1" name="FAC.VENT.FAN01.CMD" tag="bas-vent.purge_fan_1"
            commandable="true"/>
    <object type="binary-value" instance="1" name="FAC.VENT.AY01.TIER1" tag="bas-vent.co2_tier_1"/>
    <object type="multi-state-value" instance="1" name="FAC.RTU1.HS01.MODE" tag="bas-rtu-taproom.occ_mode"
            commandable="true"><state>occupied</state><state>unoccupied</state><state>standby</state></object>
  </bacnet>
</ot-sim>
"""


def free_port(kind: int) -> int:
    """A free loopback port (TCP for ZeroMQ, UDP for BACnet)."""
    with socket.socket(socket.AF_INET, kind) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def status_envelope(values: dict[str, float], sender: str = "helics-federate") -> bytes:
    """A Status envelope as ot-sim's I/O module pushes it (io.py)."""
    return json.dumps(
        {
            "version": "v1",
            "kind": "Status",
            "metadata": {"sender": sender},
            "contents": {
                "measurements": [{"tag": t, "value": float(v), "ts": 0} for t, v in values.items()]
            },
        }
    ).encode()


class Lab:
    """The running module with the bus's two sockets and a BACnet client."""

    def __init__(self, server: Any, client: Any, pub: Any, pull: Any, port: int) -> None:
        from bacpypes3.pdu import Address  # noqa: PLC0415

        self.server: Any = server
        self.client: Any = client
        self.pub: Any = pub
        self.pull: Any = pull
        self.port: int = port
        self.address: Any = Address(f"127.0.0.1:{port}")

    def obj(self, tag: str) -> Any:
        return self.server.points[tag].obj

    async def status(self, values: dict[str, float]) -> None:
        """Publish a Status until the module has applied it (a ZeroMQ
        subscriber misses what is sent before its connection is up)."""
        points: dict[str, Any] = self.server.points
        marks: dict[str, float | None] = {t: points[t].seen for t in values if t in points}
        frame: bytes = status_envelope(values)
        for _ in range(100):
            await self.pub.send_multipart((TOPIC, frame))
            await asyncio.sleep(0.02)
            if all(points[t].seen != mark for t, mark in marks.items()):
                return
        raise AssertionError(f"the module never applied {values}")

    async def update(self, timeout: float = 2.0) -> dict[str, Any]:
        """The next Update the module pushed, as ``{"tag", "value"}``."""
        frames: list[bytes] = await asyncio.wait_for(self.pull.recv_multipart(), timeout)
        assert frames[0] == TOPIC
        envelope: Any = json.loads(frames[1])
        assert envelope["version"] == "v1" and envelope["kind"] == "Update"
        assert envelope["metadata"]["sender"] == self.server.config.name
        (point,) = envelope["contents"]["updates"]
        return {"tag": point["tag"], "value": point["value"]}

    async def no_update(self, wait: float = 0.3) -> None:
        with contextlib.suppress(asyncio.TimeoutError):
            frames: list[bytes] = await asyncio.wait_for(self.pull.recv_multipart(), wait)
            raise AssertionError(f"unexpected bus message {frames[1]!r}")

    async def read(self, obj: str, prop: str = "present-value") -> Any:
        from bacpypes3.primitivedata import ObjectIdentifier  # noqa: PLC0415

        return await self.client.read_property(self.address, ObjectIdentifier(obj), prop)

    async def write(self, obj: str, value: Any, prop: str = "present-value", priority: int | None = None) -> Any:
        """Write a property; the ErrorPDU's ``(class, code)`` when refused."""
        from bacpypes3.apdu import ErrorRejectAbortNack  # noqa: PLC0415
        from bacpypes3.primitivedata import ObjectIdentifier  # noqa: PLC0415

        try:
            result: Any = await self.client.write_property(
                self.address, ObjectIdentifier(obj), prop, value, priority=priority
            )
        except ErrorRejectAbortNack as exc:
            result = exc
        if result is None:
            return None
        return (str(getattr(result, "errorClass", "")), str(getattr(result, "errorCode", result)))


@contextlib.asynccontextmanager
async def lab(stale: float = 30.0) -> AsyncIterator[Lab]:
    import zmq  # noqa: PLC0415
    import zmq.asyncio  # noqa: PLC0415
    from bacpypes3.app import Application  # noqa: PLC0415
    from bacpypes3.argparse import SimpleArgumentParser  # noqa: PLC0415

    pull_port, pub_port = free_port(socket.SOCK_STREAM), free_port(socket.SOCK_STREAM)
    port: int = free_port(socket.SOCK_DGRAM)
    root: ET.Element = ET.fromstring(  # noqa: S314 - our own text
        CONFIG_XML.format(pull=pull_port, pub=pub_port, port=port, stale=stale)
    )
    context: Any = zmq.asyncio.Context()
    # The bus's side: it binds where the modules connect (ot-sim-message-bus).
    pull: Any = context.socket(zmq.PULL)
    pull.bind(f"tcp://127.0.0.1:{pull_port}")
    pub: Any = context.socket(zmq.PUB)
    pub.bind(f"tcp://127.0.0.1:{pub_port}")
    server: Any = module.Server(module.BacnetConfig.from_xml(root))
    server.build()
    server.connect()
    client: Any = Application.from_args(
        SimpleArgumentParser().parse_args(
            ["--name", "client", "--instance", "999", "--address", f"127.0.0.2/8:{free_port(socket.SOCK_DGRAM)}"]
        )
    )
    await asyncio.sleep(0.2)  # the datagram endpoints bind in tasks
    try:
        yield Lab(server, client, pub, pull, port)
    finally:
        client.close()
        await server.close()
        context.destroy(linger=0)
