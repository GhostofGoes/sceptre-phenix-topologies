"""Assign every Modbus address from the plant database (DESIGN.md §12.5).

Conventions (one source, four views of each tag):

- **ot-sim (RIO).** The phenix ot-sim app numbers registers itself, in host
  device order then device-type point order, from coil 1, discrete input
  10001, input register 30001, and holding register 40001. ot-sim keys its
  register table by that literal number and compares it with the raw PDU
  address, so a client reads input register "30001" with FC4 at PDU
  address 30001 (not 30000, not 0).
- **OpenPLC ``modbus_master``** (PLC polling its RIO). Each block is a
  function code, a PDU offset equal to the ot-sim literal number, and an
  IEC location. RIO I/O lands at ``%IX100.0``, ``%QX100.0``, ``%IW100``,
  and ``%QW100``, which is also where the OpenPLC v3 fallback maps slave
  devices, so the PLC program is the same under v3 and v4.
- **OpenPLC ``modbus_slave``** (northbound). PDU addresses from 0: coil n is
  ``%QX(n//8).(n%8)``, discrete input n is ``%IX``, input register n is
  ``%IWn``, holding register n is ``%QWn`` below 1024 and ``%MW(n-1024)``
  from 1024. PLC-memory words (setpoints, computed values) use ``%MW``;
  PLC-memory bits (alarm bits, commands) use ``%QX0.0`` upward.
- **FUXA.** ``memaddress`` area plus a 1-based ``address`` (FUXA subtracts
  one), so ``address = PDU + 1``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from plantgen.model import Device, Plant, PlantError, Tag, is_analog, is_writable

#: First literal register number per space in ot-sim (phenix ot-sim app).
OTSIM_BASE: dict[str, int] = {
    "coil": 1,
    "discrete": 10001,
    "input": 30001,
    "holding": 40001,
}
#: Register space for each tag type (the phenix app's mapping).
SPACE_FOR_TYPE: dict[str, str] = {
    "binary-read-write": "coil",
    "binary-read": "discrete",
    "analog-read": "input",
    "analog-read-write": "holding",
}
#: modbus_master function codes per RIO space (read or write).
MASTER_FC: dict[str, int] = {"coil": 15, "discrete": 2, "input": 4, "holding": 16}
#: IEC area/size for RIO-mapped I/O per space.
IO_IEC: dict[str, tuple[str, str]] = {
    "coil": ("Q", "X"),
    "discrete": ("I", "X"),
    "input": ("I", "W"),
    "holding": ("Q", "W"),
}
#: First IEC index for RIO-mapped I/O: bit 800 is %X100.0; word 100 is %W100.
IO_BIT_BASE: int = 800
IO_WORD_BASE: int = 100
#: PLC-memory bits must stay below the RIO block.
MEM_BIT_LIMIT: int = IO_BIT_BASE
#: modbus_slave holding registers at and above this offset map to %MW.
MW_OFFSET: int = 1024
MW_LIMIT: int = 1024
#: FUXA memaddress per Modbus space.
FUXA_MEMADDRESS: dict[str, str] = {
    "coil": "000000",
    "discrete": "100000",
    "input": "300000",
    "holding": "400000",
}
#: Read function code per Modbus space (for documentation columns).
READ_FC: dict[str, int] = {"coil": 1, "discrete": 2, "input": 4, "holding": 3}


class IecAddress(BaseModel):
    """An IEC 61131-3 located-variable address."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    area: str  # I, Q, M
    size: str  # X, W
    index: int  # bit index for X, word index for W

    def __str__(self) -> str:
        if self.size == "X":
            return f"%{self.area}X{self.index // 8}.{self.index % 8}"
        return f"%{self.area}W{self.index}"

    @classmethod
    def parse(cls, text: str) -> IecAddress:
        """Parse ``%IX100.0`` or ``%MW3``."""
        if not text.startswith("%") or len(text) < 4:
            raise ValueError(f"bad IEC address {text!r}")
        area, size, rest = text[1], text[2], text[3:]
        if size == "X":
            byte, _, bit = rest.partition(".")
            if not bit or not 0 <= int(bit) <= 7:
                raise ValueError(f"bad IEC bit address {text!r}")
            return cls(area=area, size=size, index=int(byte) * 8 + int(bit))
        return cls(area=area, size=size, index=int(rest))


def iec_to_slave(iec: IecAddress) -> tuple[str, int]:
    """Northbound ``modbus_slave`` (space, PDU address) for an IEC address."""
    match (iec.area, iec.size):
        case ("Q", "X"):
            return "coil", iec.index
        case ("I", "X"):
            return "discrete", iec.index
        case ("I", "W"):
            return "input", iec.index
        case ("Q", "W"):
            return "holding", iec.index
        case ("M", "W"):
            return "holding", MW_OFFSET + iec.index
    raise ValueError(f"{iec} is not exposed by modbus_slave")


class RioRegister(BaseModel):
    """One ot-sim register on a remote I/O host."""

    rio: str
    space: str
    address: int  # ot-sim literal register number (= PDU address)
    device: Device
    point: str
    tag: Tag | None


class TagAddress(BaseModel):
    """Every address of one tag."""

    tag: Tag
    plc_iec: IecAddress
    slave_space: str
    slave_address: int
    rio_space: str | None = None
    rio_address: int | None = None

    @property
    def fuxa_memaddress(self) -> str:
        """FUXA ``memaddress`` area."""
        return FUXA_MEMADDRESS[self.slave_space]

    @property
    def fuxa_address(self) -> int:
        """FUXA 1-based ``address``."""
        return self.slave_address + 1

    @property
    def master_fc(self) -> int | None:
        """modbus_master function code used for this RIO point."""
        return MASTER_FC[self.rio_space] if self.rio_space else None


class Allocation(BaseModel):
    """All addresses for a variant."""

    rio_registers: dict[str, list[RioRegister]] = Field(default_factory=dict)
    tags: dict[str, TagAddress] = Field(default_factory=dict)
    # PLC -> ordered list of RIO hosts it polls
    plc_rios: dict[str, list[str]] = Field(default_factory=dict)


def rio_devices(plant: Plant, rio: str) -> list[Device]:
    """Devices served by ``rio``, in plant order (= phenix host device order)."""
    return [d for d in plant.devices if d.rio == rio]


def allocate(plant: Plant) -> Allocation:
    """Assign RIO, PLC, slave, and FUXA addresses for every tag."""
    alloc = Allocation()
    bound = {(t.device.name, t.point): t for t in plant.tags if t.device}

    # 1. ot-sim literal registers, replicating the phenix app's numbering.
    for rio in sorted({d.rio for d in plant.devices}, key=_order(plant)):
        counters = dict(OTSIM_BASE)
        regs: list[RioRegister] = []
        for dev in rio_devices(plant, rio):
            for point in plant.device_types[dev.type].values():
                space = SPACE_FOR_TYPE[point.type]
                regs.append(
                    RioRegister(
                        rio=rio,
                        space=space,
                        address=counters[space],
                        device=dev,
                        point=point.name,
                        tag=bound.get((dev.name, point.name)),
                    )
                )
                counters[space] += 1
        alloc.rio_registers[rio] = regs

    # 2. PLC image: RIO blocks from %X100.0 / %W100, PLC memory below/at %MW.
    for plc in sorted({t.plc for t in plant.tags}, key=_order(plant)):
        rios = []
        for t in plant.tags:
            if t.plc == plc and t.rio and t.rio not in rios:
                rios.append(t.rio)
        alloc.plc_rios[plc] = rios
        next_io = {"coil": IO_BIT_BASE, "discrete": IO_BIT_BASE}
        next_io.update({"input": IO_WORD_BASE, "holding": IO_WORD_BASE})
        for rio in rios:
            for space in ("discrete", "coil", "input", "holding"):
                for reg in alloc.rio_registers[rio]:
                    if reg.space != space:
                        continue
                    area, size = IO_IEC[space]
                    iec = IecAddress(area=area, size=size, index=next_io[space])
                    next_io[space] += 1
                    if reg.tag is None:
                        continue  # reported by checks.unbound_points
                    s_space, s_addr = iec_to_slave(iec)
                    alloc.tags[reg.tag.name] = TagAddress(
                        tag=reg.tag,
                        plc_iec=iec,
                        slave_space=s_space,
                        slave_address=s_addr,
                        rio_space=reg.space,
                        rio_address=reg.address,
                    )
        mem_bit = 0
        mem_word = 0
        for t in plant.tags:
            if t.plc != plc or t.device:
                continue
            if is_analog(t.type):
                iec = IecAddress(area="M", size="W", index=mem_word)
                mem_word += 1
            else:
                iec = IecAddress(area="Q", size="X", index=mem_bit)
                mem_bit += 1
            s_space, s_addr = iec_to_slave(iec)
            alloc.tags[t.name] = TagAddress(
                tag=t, plc_iec=iec, slave_space=s_space, slave_address=s_addr
            )
        if mem_bit > MEM_BIT_LIMIT:
            raise PlantError(f"{plc}: {mem_bit} memory bits overflow into %QX100.0")
        if mem_word > MW_LIMIT:
            raise PlantError(f"{plc}: {mem_word} memory words exceed %MW0-1023")
    return alloc


def access(tag: Tag) -> str:
    """SCADA access: PLC-memory setpoints and commands are read-write.

    Writable RIO points (valve commands, spunding setpoints) are driven by
    the PLC program every scan, so SCADA only reads them.
    """
    return "read-write" if is_writable(tag.type) and not tag.device else "read"


def _order(plant: Plant) -> Callable[[str], int]:
    """Sort key: position of a host in the variant host list."""
    names = list(plant.spec.hosts)
    return lambda h: names.index(h) if h in names else len(names)
