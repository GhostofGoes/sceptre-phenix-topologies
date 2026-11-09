"""A PLC in the loop of the offline federation (DESIGN.md §9.6, §12.1).

:class:`cherrymountain_sim.local.LocalFederation` runs the federates without
HELICS, ot-sim, or OpenPLC. :class:`PlcLink` closes the loop for one PLC: each
federation step it reads the PLC's input tags from the federation's values,
runs one scan of an *engine*, and writes the PLC's output tags to the federate
that owns the field point. Which tags those are, and how a register is scaled,
comes from plantgen's ``tags.json``, the same table ot-sim and the PLC's
``modbus_master`` are generated from:

- an input is a field point the PLC reads (``analog-read``, a read-only
  ``analog-read-write``, ``binary-read``): the value times ``10**scaling``,
  rounded and held to the 16-bit register range (signed when the tag is), or the
  bit;
- an output is a field point the PLC writes (``analog-read-write`` or
  ``binary-read-write`` that no other host writes): the register divided by
  ``10**scaling``, or the bit.

An engine is anything with ``set``, ``get``, and ``scan``: the compiled ST of the
program (``tests/st_engine.py``) or a Python model of it
(``tests/plc_power_model.py``). The loop does not model the Modbus polling
delay, so it runs one scan per federation step (the program's task runs every
100 ms and its polls take about a second in the plant).
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import ClassVar, Literal, Protocol

from pydantic import BaseModel, ConfigDict

from cherrymountain_sim.federate import Federate, Value

UINT16_MAX: int = 0xFFFF
INT16_MIN: int = -0x8000
INT16_MAX: int = 0x7FFF


class PlcEngine(Protocol):
    """What the loop needs of a PLC program."""

    def set(self, name: str, value: int | bool) -> None:
        """Set a located input, or operator memory."""

    def get(self, name: str) -> int:
        """A variable of the program."""

    def scan(self, time_ns: int) -> None:
        """Run the program once, with the scan-cycle time ``TIME()`` reads."""


class TagRecord(BaseModel):
    """The fields of a ``tags.json`` record the loop uses."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore")

    name: str
    type: str
    scaling: int = 0
    signed: bool = False
    source: str
    plc: str | None = None
    federate: str | None = None
    device: str | None = None
    point: str | None = None
    helics_key: str | None = None
    written_by: str | None = None


class TagsFile(BaseModel):
    """``plant/generated/<variant>/tags.json``."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore")

    tags: list[TagRecord]


class PlcPoint(BaseModel):
    """One field tag a PLC exchanges with the federation."""

    var: str
    direction: Literal["in", "out"]
    analog: bool
    signed: bool
    scaling: int
    key: str
    federate: str
    target: str

    def to_register(self, value: Value) -> int:
        """A federation value as the register the PLC reads."""
        if not self.analog:
            return int(bool(value))
        raw = round(float(value) * 10**self.scaling)
        low, high = (INT16_MIN, INT16_MAX) if self.signed else (0, UINT16_MAX)
        return min(max(raw, low), high)

    def from_register(self, raw: int) -> Value:
        """What the PLC wrote, as the value the federate is written."""
        if not self.analog:
            return bool(raw)
        return raw / 10**self.scaling


class PlcLink:
    """One PLC engine wired to the federates by plantgen's tag table."""

    plc: str
    engine: PlcEngine
    points: list[PlcPoint]
    scans: int

    def __init__(self, plc: str, engine: PlcEngine, tags_json: Path) -> None:
        self.plc = plc
        self.engine = engine
        self.scans = 0
        table = TagsFile.model_validate_json(tags_json.read_text(encoding="utf-8"))
        self.points = [self._point(r) for r in table.tags if self._field_tag(r)]

    def _field_tag(self, tag: TagRecord) -> bool:
        return tag.source == "io" and tag.plc == self.plc and bool(tag.helics_key)

    @staticmethod
    def _point(tag: TagRecord) -> PlcPoint:
        writes = tag.type.endswith("-write") and not tag.written_by
        return PlcPoint(
            var=tag.name.replace(".", "_"),
            direction="out" if writes else "in",
            analog=tag.type.startswith("analog"),
            signed=tag.signed,
            scaling=tag.scaling,
            key=tag.helics_key or "",
            federate=tag.federate or "",
            target=f"{tag.device}.{tag.point}",
        )

    def scan(
        self, values: Mapping[str, Value], feds: Mapping[str, Federate], t_s: float
    ) -> None:
        """Read the inputs, run one scan at federation time ``t_s``, write outputs."""
        for point in self.points:
            if point.direction == "in" and point.key in values:
                self.engine.set(point.var, point.to_register(values[point.key]))
        self.engine.scan(round(t_s * 1e9))
        self.scans += 1
        for point in self.points:
            if point.direction == "out" and point.federate in feds:
                feds[point.federate].write(
                    point.target, point.from_register(self.engine.get(point.var))
                )
