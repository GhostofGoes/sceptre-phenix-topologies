#!/usr/bin/env python3
"""A BACnet/IP server module for ot-sim devices (the BAS controllers).

ot-sim has Modbus and DNP3 modules but no BACnet one, so this module gives an
ot-sim device its BACnet/IP face (DESIGN.md sections 8.1, 12.3, and 16). The
ot-sim CPU module starts it like any other module, from a ``module`` entry in
``/etc/ot-sim/config.xml``, and it reads its settings from the ``bacnet``
element of the same file::

    <bacnet name="bacnet-server" mode="server">
      <endpoint>10.30.10.22/24:47808</endpoint>
      <device instance="3003" name="BAS-VENT-1"/>
      <object type="analog-input" instance="1" name="FAC.VENT.AT01.PV"
              tag="bas-vent.co2_1_ppm" units="partsPerMillion"/>
      <object type="binary-output" instance="1" name="FAC.VENT.FAN01.CMD"
              tag="bas-vent.purge_fan_1" commandable="true"/>
    </bacnet>

Values come from the ot-sim message bus: the I/O module publishes a Status
message with every tag each HELICS step, and the object's present-value
follows it. A BACnet write goes the other way, as an Update message that the
I/O module sends on to the federate.

* **Access.** Only present-value of an object marked ``commandable`` can be
  written. Everything else, the Device and Network Port objects included,
  answers ``write-access-denied`` (BACpypes3 alone accepts any write).
* **Commandable objects** have a priority array. A command at any priority
  overrides the controller's own logic for that point; writing NULL to the last
  commanded priority relinquishes it, and the module sends an Update with a
  null value so the federate hands the point back to that logic. While nothing
  is commanded, present-value is the relinquish default, which follows the bus.
  Outputs are always commandable, and so is every value object that accepts a
  write (a setpoint, a mode).
* **Stale values.** An object whose tag has had no Status for ``stale-after``
  seconds reports ``reliability communication-failure`` (the fault flag).
* **Address.** The endpoint is the NIC's own address with its prefix, never
  0.0.0.0: a BACpypes3 node hears broadcasts only on its subnet's directed
  broadcast address, which is where the BBMD sends a forwarded Who-Is.

The message format is ot-sim's (``src/python/otsim/msgbus`` at the pinned
commit): two ZeroMQ frames, the topic ``RUNTIME`` and a JSON envelope. It is
spoken here with pyzmq directly, so the module needs no thread.

Requires bacpypes3, pydantic, and pyzmq (the cm-otsim image installs them in
/opt/otsim-venv). ``--check`` validates the settings and prints the objects.
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import json
import logging
import math
import re
import signal
import sys
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, ClassVar, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

#: ot-sim's exit status for "do not restart me" (src/go/util/exit.go).
EXIT_NO_RESTART: Final[int] = 101
#: The message-bus topic every ot-sim module uses for Status and Update.
TOPIC: Final[bytes] = b"RUNTIME"
DEFAULT_PULL: Final[str] = "tcp://127.0.0.1:1234"
DEFAULT_PUB: Final[str] = "tcp://127.0.0.1:5678"
DEFAULT_PORT: Final[int] = 47808
#: Seconds to wait before exiting after a failed start, so the CPU module's
#: restart loop (which has no delay of its own) does not spin.
RESTART_PAUSE_S: Final[float] = 5.0
#: The largest magnitude a BACnet Real (IEEE 754 single) holds. Until a
#: federate publishes a value, HELICS gives the I/O module -1e49 for it, which
#: is no measurement and does not fit.
REAL_MAX: Final[float] = 3.4028234663852886e38

ObjectKind = Literal[
    "analog-input",
    "analog-output",
    "analog-value",
    "binary-input",
    "binary-output",
    "binary-value",
    "multi-state-value",
]
ANALOG: Final[frozenset[str]] = frozenset({"analog-input", "analog-output", "analog-value"})
BINARY: Final[frozenset[str]] = frozenset({"binary-input", "binary-output", "binary-value"})
INPUTS: Final[frozenset[str]] = frozenset({"analog-input", "binary-input"})
#: Output objects always have a priority array (ASHRAE 135 clause 19.2).
OUTPUTS: Final[frozenset[str]] = frozenset({"analog-output", "binary-output"})
#: Object types that can have a priority array.
COMMANDABLE: Final[frozenset[str]] = OUTPUTS | {"analog-value", "binary-value", "multi-state-value"}

_ENDPOINT: Final[re.Pattern[str]] = re.compile(
    r"^(?P<ip>\d+\.\d+\.\d+\.\d+)/(?P<prefix>\d+)(?::(?P<port>\d+))?$"
)


class ConfigError(ValueError):
    """The module's settings are wrong; a restart cannot fix them."""


# ---------------------------------------------------------------- settings


class ObjectConfig(BaseModel):
    """One BACnet object and the message-bus tag it follows."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    type: ObjectKind
    instance: int = Field(ge=0, le=4194302)
    name: str = Field(min_length=1)
    tag: str = Field(min_length=1)
    description: str = ""
    #: A BACnet engineering unit name, as BACpypes3 spells it (``percent``).
    units: str | None = None
    #: present-value accepts a BACnet write, through a priority array.
    commandable: bool = False
    #: Engineering limits for a write (analog objects).
    minimum: float | None = None
    maximum: float | None = None
    #: State texts of a multi-state object; state 1 is the first.
    states: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _consistent(self) -> ObjectConfig:
        if self.commandable and self.type not in COMMANDABLE:
            raise ValueError(f"{self.name}: an input object cannot be commandable")
        if self.type in OUTPUTS and not self.commandable:
            raise ValueError(f"{self.name}: an output object must be commandable")
        if self.type == "multi-state-value" and not self.states:
            raise ValueError(f"{self.name}: a multi-state object needs its states")
        if self.type != "multi-state-value" and self.states:
            raise ValueError(f"{self.name}: only a multi-state object has states")
        if (
            self.minimum is not None
            and self.maximum is not None
            and self.minimum > self.maximum
        ):
            raise ValueError(f"{self.name}: min is above max")
        return self

    @property
    def key(self) -> tuple[str, int]:
        return (self.type, self.instance)


class BacnetConfig(BaseModel):
    """The ``bacnet`` element of an ot-sim config.xml."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    #: The module's name on the message bus (the envelope's sender).
    name: str = Field(default="bacnet", min_length=1)
    #: ``ip/prefix[:port]``: the NIC's own address and its prefix.
    endpoint: str
    device_instance: int = Field(ge=0, le=4194302)
    device_name: str = Field(min_length=1)
    pull_endpoint: str = DEFAULT_PULL
    pub_endpoint: str = DEFAULT_PUB
    #: Seconds without a Status before an object reports a fault; 0 turns it off.
    stale_after_s: float = Field(default=30.0, ge=0.0)
    objects: tuple[ObjectConfig, ...]

    @field_validator("endpoint")
    @classmethod
    def _endpoint(cls, value: str) -> str:
        match: re.Match[str] | None = _ENDPOINT.match(value)
        if match is None:
            raise ValueError(f"not an ip/prefix[:port] address: {value!r}")
        address: ipaddress.IPv4Address = ipaddress.IPv4Address(match["ip"])
        if address.is_unspecified:
            raise ValueError("the endpoint must be the NIC's own address, not 0.0.0.0")
        if not 1 <= int(match["prefix"]) <= 32:
            raise ValueError(f"prefix out of range in {value!r}")
        if match["port"] and not 1 <= int(match["port"]) <= 65535:
            raise ValueError(f"port out of range in {value!r}")
        return value if match["port"] else f"{value}:{DEFAULT_PORT}"

    @model_validator(mode="after")
    def _unique(self) -> BacnetConfig:
        for label, values in (
            ("object", [o.key for o in self.objects]),
            ("object name", [o.name for o in self.objects]),
            ("tag", [o.tag for o in self.objects]),
        ):
            seen: set[Any] = set()
            for value in values:
                if value in seen:
                    raise ValueError(f"duplicate {label}: {value}")
                seen.add(value)
        return self

    @classmethod
    def from_xml(cls, root: ET.Element) -> BacnetConfig:
        """The settings from an ot-sim config document (its ``ot-sim`` root)."""
        element: ET.Element | None = root.find("bacnet")
        if element is None:
            raise ConfigError("the config has no <bacnet> element")
        device: ET.Element | None = element.find("device")
        if device is None:
            raise ConfigError("<bacnet> has no <device> element")
        bus: ET.Element | None = root.find("message-bus")

        def endpoint(name: str, default: str) -> str:
            own: str | None = element.findtext(name)
            shared: str | None = bus.findtext(name) if bus is not None else None
            return (own or shared or default).strip()

        values: dict[str, Any] = {
            "name": element.get("name", "bacnet"),
            "endpoint": (element.findtext("endpoint") or "").strip(),
            "device_instance": device.get("instance"),
            "device_name": device.get("name"),
            "pull_endpoint": endpoint("pull-endpoint", DEFAULT_PULL),
            "pub_endpoint": endpoint("pub-endpoint", DEFAULT_PUB),
            "objects": [_object_values(o) for o in element.findall("object")],
        }
        stale: str | None = element.findtext("stale-after")
        if stale is not None:
            values["stale_after_s"] = stale.strip()
        try:
            return cls.model_validate(values)
        except ValidationError as exc:
            raise ConfigError(str(exc)) from exc

    @classmethod
    def load(cls, path: Path) -> BacnetConfig:
        try:
            root: ET.Element = ET.parse(path).getroot()  # noqa: S314 - our own config file
        except (OSError, ET.ParseError) as exc:
            raise ConfigError(f"cannot read {path}: {exc}") from exc
        return cls.from_xml(root)


def _object_values(element: ET.Element) -> dict[str, Any]:
    """The attributes of an ``object`` element as :class:`ObjectConfig` input."""
    names: dict[str, str] = {"min": "minimum", "max": "maximum"}
    values: dict[str, Any] = {names.get(k, k): v for k, v in element.attrib.items()}
    states: list[str] = [(s.text or "").strip() for s in element.findall("state")]
    if states:
        values["states"] = states
    return values


# ---------------------------------------------------------------- message bus


def update_envelope(sender: str, tag: str, value: float | None) -> bytes:
    """An ot-sim Update envelope for one tag (``msgbus/envelope.py``). A null
    value is this module's own: it relinquishes the point."""
    return json.dumps(
        {
            "version": "v1",
            "kind": "Update",
            "metadata": {"sender": sender},
            "contents": {
                "updates": [{"tag": tag, "value": value, "ts": 0}],
                "recipient": "",
                "confirm": "",
            },
        }
    ).encode()


def status_points(frame: bytes) -> list[tuple[str, float]]:
    """The measurements of a Status envelope; nothing for any other message.
    A value that is not a number a BACnet Real can hold is left out, so its
    object keeps reporting that it has no value."""
    try:
        envelope: Any = json.loads(frame)
    except ValueError:
        return []
    if not isinstance(envelope, dict) or envelope.get("kind") != "Status":
        return []
    points: list[tuple[str, float]] = []
    for point in (envelope.get("contents") or {}).get("measurements") or []:
        try:
            value: float = float(point["value"])
        except (KeyError, TypeError, ValueError):
            continue
        if math.isfinite(value) and abs(value) <= REAL_MAX:
            points.append((str(point.get("tag", "")), value))
    return points


# ---------------------------------------------------------------- objects


class Point:
    """One object's link to the bus: conversion, write checks, and staleness.

    It wraps the BACpypes3 object instead of extending it, so the rules can be
    tested without a BACnet stack; :func:`build_object` gives the object a
    ``write_property`` that asks its point first.
    """

    def __init__(
        self,
        config: ObjectConfig,
        send: Callable[[str, float | None], None],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config: ObjectConfig = config
        self.send: Callable[[str, float | None], None] = send
        self.clock: Callable[[], float] = clock
        self.obj: Any = None
        #: When the tag's last Status arrived; None before the first.
        self.seen: float | None = None
        self.fault: bool = True

    # -- conversion

    def to_bacnet(self, value: float) -> Any:
        """A bus value as the object's present-value."""
        kind: str = self.config.type
        if kind in ANALOG:
            return float(value)
        if kind in BINARY:
            return "active" if value >= 0.5 else "inactive"
        return min(max(round(value), 1), len(self.config.states))

    def to_bus(self, value: Any) -> float:
        """A present-value as a bus value."""
        kind: str = self.config.type
        if kind in BINARY:
            return 1.0 if int(value) else 0.0
        return float(value)

    def initial(self) -> Any:
        if self.config.type in ANALOG:
            return float(self.config.minimum or 0.0)
        return self.to_bacnet(0.0)

    # -- from the bus

    def commanded(self) -> bool:
        """True while a priority-array slot holds a command."""
        if not self.config.commandable:
            return False
        return any(slot.null is None for slot in self.obj.priorityArray)

    def measured(self, value: float) -> None:
        """Apply a Status value from the bus."""
        self.seen = self.clock()
        if self.fault:
            self.fault = False
            self.obj.reliability = "noFaultDetected"
        converted: Any = self.to_bacnet(value)
        if not self.config.commandable:
            self.obj.presentValue = converted
        elif not self.commanded():
            # The controller's own value shows through the relinquish default.
            # While a command holds, the federate only echoes that command.
            self.obj.relinquishDefault = converted
            self.obj.recalculating()

    def check_stale(self, stale_after_s: float) -> None:
        if self.fault or self.seen is None or stale_after_s <= 0:
            return
        if self.clock() - self.seen > stale_after_s:
            self.fault = True
            self.obj.reliability = "communicationFailure"

    # -- from BACnet

    def check_write(self, attr: str, value: Any, priority: int | None) -> None:
        """Refuse a write this object does not accept (raises PropertyError)."""
        from bacpypes3.errors import PropertyError  # noqa: PLC0415
        from bacpypes3.primitivedata import Null  # noqa: PLC0415

        if attr != "presentValue" or not self.config.commandable:
            raise PropertyError("writeAccessDenied")
        if isinstance(value, Null):
            if priority is None:  # NULL relinquishes a priority, so it needs one
                raise PropertyError("invalidDataType")
            return
        kind: str = self.config.type
        if kind in ANALOG:
            number: float = float(value)
            low, high = self.config.minimum, self.config.maximum
            if (
                not math.isfinite(number)
                or (low is not None and number < low)
                or (high is not None and number > high)
            ):
                raise PropertyError("valueOutOfRange")
        elif kind == "multi-state-value" and not 1 <= int(value) <= len(self.config.states):
            raise PropertyError("valueOutOfRange")

    def written(self) -> None:
        """Send the result of an accepted write to the bus: the value now in
        force, or null once the last command is relinquished."""
        if self.commanded():
            self.send(self.config.tag, self.to_bus(self.obj.presentValue))
        else:
            self.send(self.config.tag, None)


def build_object(point: Point) -> Any:
    """The BACpypes3 object for a point, with its write guard."""
    from bacpypes3.local import analog, binary, multistate  # noqa: PLC0415
    from bacpypes3.local.cmd import Commandable  # noqa: PLC0415

    config: ObjectConfig = point.config
    plain: dict[str, type] = {
        "analog-input": analog.AnalogInputObject,
        "analog-output": analog.AnalogOutputObject,
        "analog-value": analog.AnalogValueObject,
        "binary-input": binary.BinaryInputObject,
        "binary-output": binary.BinaryOutputObject,
        "binary-value": binary.BinaryValueObject,
        "multi-state-value": multistate.MultiStateValueObject,
    }
    base: type = plain[config.type]
    if config.commandable and not issubclass(base, Commandable):
        # A value object that takes commands gets a priority array too.
        base = type(f"{base.__name__}Cmd", (Commandable, base), {})

    class Guarded(base):  # type: ignore[misc, valid-type]
        """``base`` that asks its point before a write and tells it after."""

        _cm_point: Point

        async def write_property(
            self, attr: Any, value: Any, index: int | None = None, priority: int | None = None
        ) -> None:
            name: str = _attr_name(self, attr)
            self._cm_point.check_write(name, value, priority)
            await super().write_property(attr, value, index, priority)
            self._cm_point.written()

    Guarded.__name__ = Guarded.__qualname__ = f"Guarded{base.__name__}"
    kwargs: dict[str, Any] = {
        "objectIdentifier": (config.type, config.instance),
        "objectName": config.name,
        "description": config.description,
        "statusFlags": [0, 0, 0, 0],
        "eventState": "normal",
        "outOfService": False,
        "reliability": "communicationFailure",
    }
    if config.type in ANALOG:
        kwargs["units"] = config.units or "noUnits"
        kwargs["covIncrement"] = 0.1
        if config.type != "analog-input":
            if config.minimum is not None:
                kwargs["minPresValue"] = config.minimum
            if config.maximum is not None:
                kwargs["maxPresValue"] = config.maximum
    elif config.type == "multi-state-value":
        kwargs["numberOfStates"] = len(config.states)
        kwargs["stateText"] = list(config.states)
    if config.type in {"binary-input", "binary-output"}:
        kwargs["polarity"] = "normal"
    # A commandable object's present-value is its priority array's result, so
    # its first value is the relinquish default.
    kwargs["relinquishDefault" if config.commandable else "presentValue"] = point.initial()
    obj: Any = Guarded(**kwargs)
    obj._cm_point = point  # noqa: SLF001 - our own subclass
    point.obj = obj
    if config.commandable:
        obj.recalculating()
    return obj


def _attr_name(obj: Any, attr: Any) -> str:
    """A property reference in BACpypes3's attribute form (``presentValue``)."""
    if isinstance(attr, str):
        return attr
    named: Any = getattr(attr, "attr", None)
    if isinstance(named, str):
        return named
    return str(obj._property_identifier_class(attr).attr)  # noqa: SLF001


def _read_only(base: type) -> type:
    """``base`` (the Device or Network Port object) refusing every write."""

    class ReadOnly(base):  # type: ignore[misc, valid-type]
        async def write_property(
            self, attr: Any, value: Any, index: int | None = None, priority: int | None = None
        ) -> None:
            from bacpypes3.errors import PropertyError  # noqa: PLC0415

            raise PropertyError("writeAccessDenied")

    ReadOnly.__name__ = ReadOnly.__qualname__ = f"ReadOnly{base.__name__}"
    return ReadOnly


# ---------------------------------------------------------------- the server


class Server:
    """The BACnet application, its points, and the bus sockets."""

    def __init__(self, config: BacnetConfig) -> None:
        self.config: BacnetConfig = config
        self.points: dict[str, Point] = {}
        self.app: Any = None
        self._outbox: asyncio.Queue[bytes] = asyncio.Queue()
        self._tasks: list[asyncio.Task[None]] = []
        self._zmq: Any = None

    def log(self, message: str) -> None:
        print(f"[{self.config.name}] {message}", flush=True)

    def send(self, tag: str, value: float | None) -> None:
        """Queue an Update for the bus (called from a BACnet write)."""
        self._outbox.put_nowait(update_envelope(self.config.name, tag, value))
        self.log(f"write: {tag} -> {'relinquished' if value is None else value}")

    def build(self) -> None:
        """Create the BACnet application and bind its address."""
        from bacpypes3.app import Application  # noqa: PLC0415
        from bacpypes3.local.device import DeviceObject  # noqa: PLC0415
        from bacpypes3.local.networkport import NetworkPortObject  # noqa: PLC0415

        config: BacnetConfig = self.config
        device: Any = _read_only(DeviceObject)(
            objectIdentifier=("device", config.device_instance),
            objectName=config.device_name,
            description=f"ot-sim BACnet server ({config.name})",
            vendorName="CherryMountain range",
            modelName="ot-sim BACnet module",
        )
        port: Any = _read_only(NetworkPortObject)(
            config.endpoint,
            objectIdentifier=("network-port", 1),
            objectName="NetworkPort-1",
            networkNumber=0,
            networkNumberQuality="unknown",
        )
        objects: list[Any] = []
        for spec in config.objects:
            point: Point = Point(spec, self.send)
            objects.append(build_object(point))
            self.points[spec.tag] = point
        self.app = Application.from_object_list([device, port, *objects])

    # -- the bus

    def apply_status(self, frame: bytes) -> int:
        """Apply a bus message; the number of objects it updated."""
        count: int = 0
        for tag, value in status_points(frame):
            point: Point | None = self.points.get(tag)
            if point is not None:
                point.measured(value)
                count += 1
        return count

    async def _receive(self, socket: Any) -> None:
        first: bool = True
        while True:
            frames: list[bytes] = await socket.recv_multipart()
            if len(frames) < 2 or frames[0] != TOPIC:
                continue
            try:
                updated: int = self.apply_status(frames[1])
            except Exception as exc:  # noqa: BLE001 - one bad message must not stop the module
                self.log(f"[ERROR] dropping a bus message: {type(exc).__name__}: {exc}")
                continue
            if first and updated:
                first = False
                self.log(f"first values from the message bus ({updated} objects)")

    async def _transmit(self, socket: Any) -> None:
        while True:
            await socket.send_multipart((TOPIC, await self._outbox.get()))

    async def _watch(self) -> None:
        period: float = max(1.0, min(5.0, self.config.stale_after_s / 3 or 5.0))
        while True:
            await asyncio.sleep(period)
            before: int = sum(1 for p in self.points.values() if p.fault)
            for point in self.points.values():
                point.check_stale(self.config.stale_after_s)
            after: int = sum(1 for p in self.points.values() if p.fault)
            if after > before:
                self.log(f"[ERROR] no values from the message bus for {after} object(s)")

    def connect(self) -> None:
        """Open the bus sockets and start the tasks (inside the event loop)."""
        import zmq  # noqa: PLC0415
        import zmq.asyncio  # noqa: PLC0415

        self._zmq = zmq.asyncio.Context()
        push: Any = self._zmq.socket(zmq.PUSH)
        push.setsockopt(zmq.LINGER, 0)
        push.connect(self.config.pull_endpoint)
        sub: Any = self._zmq.socket(zmq.SUB)
        sub.setsockopt(zmq.LINGER, 0)
        sub.setsockopt(zmq.SUBSCRIBE, TOPIC)
        sub.connect(self.config.pub_endpoint)
        self._tasks = [
            asyncio.create_task(self._receive(sub)),
            asyncio.create_task(self._transmit(push)),
            asyncio.create_task(self._watch()),
        ]

    async def close(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks = []
        if self._zmq is not None:
            self._zmq.destroy(linger=0)
            self._zmq = None
        if self.app is not None:
            self.app.close()
            self.app = None

    async def serve(self) -> None:
        """Run until SIGTERM or SIGINT."""
        stop: asyncio.Event = asyncio.Event()
        loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()
        for number in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(number, stop.set)
        self.build()
        self.connect()
        self.log(
            f"device {self.config.device_instance} ({self.config.device_name}) on "
            f"{self.config.endpoint} with {len(self.points)} objects"
        )
        try:
            await stop.wait()
        finally:
            await self.close()


def describe(config: BacnetConfig) -> str:
    """The settings as text, for ``--check``."""
    lines: list[str] = [
        f"device {config.device_instance} {config.device_name} on {config.endpoint}",
        f"bus pull {config.pull_endpoint} pub {config.pub_endpoint}",
    ]
    for spec in config.objects:
        access: str = "commandable" if spec.commandable else "read-only"
        lines.append(f"{spec.type},{spec.instance} {spec.name} <- {spec.tag} ({access})")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    """``cm_otsim_bacnet.py [--check] <config.xml>``."""
    args: list[str] = list(sys.argv[1:] if argv is None else argv)
    check: bool = "--check" in args
    paths: list[str] = [a for a in args if a != "--check"]
    if len(paths) != 1:
        print("usage: cm_otsim_bacnet.py [--check] <config.xml>", file=sys.stderr)
        return EXIT_NO_RESTART
    try:
        config: BacnetConfig = BacnetConfig.load(Path(paths[0]))
    except ConfigError as exc:
        print(f"[bacnet] [ERROR] bad settings: {exc}", file=sys.stderr)
        return EXIT_NO_RESTART
    if check:
        print(describe(config))
        return 0
    # BACpypes3 reports a request it could not serve through logging only.
    logging.basicConfig(stream=sys.stdout, level=logging.ERROR, format=f"[{config.name}] [ERROR] %(name)s: %(message)s")
    try:
        asyncio.run(Server(config).serve())
    except Exception as exc:  # noqa: BLE001 - report it and let the CPU module restart us
        # BACpypes3 itself keeps retrying a bind that fails (an address that
        # is not on the NIC yet), so this is something unexpected. The CPU
        # module starts the module again at once: pace it.
        print(
            f"[{config.name}] [ERROR] stopped: {type(exc).__name__}: {exc}",
            file=sys.stderr,
            flush=True,
        )
        time.sleep(RESTART_PAUSE_S)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
