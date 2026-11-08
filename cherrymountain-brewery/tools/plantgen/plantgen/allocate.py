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
- **OpenPLC ``modbus_slave``** (northbound, v4.3.2). PDU addresses from 0.
  Coil n is ``%QX(n//8).(n%8)``, discrete input n is ``%IX``, input register
  n is ``%IWn``. Holding registers are **segmented back to back** in the order
  ``%QW``, ``%MW``, ``%MD`` (2 registers each), ``%ML`` (4 each), and every
  segment's length is its ``buffer_mapping`` count, so ``%MWn`` is at
  ``qw_count + n``. The runtime clamps each count to the length of the image
  table the program's located variables create (highest index + 1), so
  plantgen asks for exactly that (:class:`SlaveLayout`) and nothing moves.
  PLC-memory words (setpoints, computed values) use ``%MW``; PLC-memory bits
  (alarm bits, commands) use ``%QX0.0`` upward.
- **FUXA.** ``memaddress`` area plus a 1-based ``address`` (FUXA subtracts
  one), so ``address = PDU + 1``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
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
#: OpenPLC v4 ``modbus_slave`` ceiling for one Modbus table (a PDU address is
#: 16 bits). The composed holding block ``qw + mw + 2*md + 4*ml`` must fit.
MAX_TABLE: int = 65536
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


class SlaveLayout(BaseModel):
    """The ``modbus_slave`` ``buffer_mapping`` of one PLC (OpenPLC v4.3.2).

    The v4 plugin lays holding registers out as ``%QW`` (``qw_count``), then
    ``%MW`` (``mw_count``), then ``%MD`` (two registers each), then ``%ML``
    (four each). Coils are ``%QX`` bits then ``%MX`` bits; discrete inputs are
    ``%IX``; input registers are ``%IW`` only. Counts above the program's own
    image tables are clamped (and a segment's start address moves when an
    earlier segment shrinks), so :meth:`from_addresses` asks for exactly the
    highest located index + 1 per table: nothing is clamped and the layout is
    the same however the runtime sizes its image.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    qw_count: int = Field(ge=0)
    mw_count: int = Field(ge=0)
    md_count: int = Field(default=0, ge=0)
    ml_count: int = Field(default=0, ge=0)
    qx_bits: int = Field(ge=0)
    mx_bits: int = Field(default=0, ge=0)
    ix_bits: int = Field(ge=0)
    iw_count: int = Field(ge=0)

    @property
    def holding_registers(self) -> int:
        """Total holding registers: ``qw + mw + 2*md + 4*ml``."""
        return self.qw_count + self.mw_count + 2 * self.md_count + 4 * self.ml_count

    @property
    def mw_start(self) -> int:
        """PDU address of ``%MW0``: it follows the ``%QW`` segment."""
        return self.qw_count

    @classmethod
    def from_addresses(cls, addresses: Iterable[IecAddress]) -> SlaveLayout:
        """The smallest layout that exposes every address (bit counts round
        up to whole bytes, because the runtime's bool tables are bytes)."""
        top: dict[tuple[str, str], int] = {}
        for iec in addresses:
            key = (iec.area, iec.size)
            top[key] = max(top.get(key, -1), iec.index)

        def words(area: str) -> int:
            return top.get((area, "W"), -1) + 1

        def bits(area: str) -> int:
            return -(-(top.get((area, "X"), -1) + 1) // 8) * 8

        layout = cls(
            qw_count=words("Q"),
            mw_count=words("M"),
            qx_bits=bits("Q"),
            ix_bits=bits("I"),
            iw_count=words("I"),
        )
        for name, total in (
            ("holding registers", layout.holding_registers),
            ("coils", layout.qx_bits + layout.mx_bits),
            ("discrete inputs", layout.ix_bits),
            ("input registers", layout.iw_count),
        ):
            if total > MAX_TABLE:
                raise PlantError(
                    f"{total} {name} exceed one Modbus table ({MAX_TABLE})"
                )
        return layout

    def to_slave(self, iec: IecAddress) -> tuple[str, int]:
        """``(space, PDU address)`` of an IEC address under this layout."""
        match (iec.area, iec.size):
            case ("Q", "X") if iec.index < self.qx_bits:
                return "coil", iec.index
            case ("I", "X") if iec.index < self.ix_bits:
                return "discrete", iec.index
            case ("I", "W") if iec.index < self.iw_count:
                return "input", iec.index
            case ("Q", "W") if iec.index < self.qw_count:
                return "holding", iec.index
            case ("M", "W") if iec.index < self.mw_count:
                return "holding", self.mw_start + iec.index
        raise ValueError(f"{iec} is not exposed by modbus_slave layout {self}")

    def buffer_mapping(self) -> dict[str, dict[str, int]]:
        """The ``buffer_mapping`` object of ``modbus_slave.json``."""
        return {
            "holding_registers": {
                "qw_count": self.qw_count,
                "mw_count": self.mw_count,
                "md_count": self.md_count,
                "ml_count": self.ml_count,
            },
            "coils": {"qx_bits": self.qx_bits, "mx_bits": self.mx_bits},
            "discrete_inputs": {"ix_bits": self.ix_bits},
            "input_registers": {"iw_count": self.iw_count},
        }


def iec_to_slave(iec: IecAddress, layout: SlaveLayout) -> tuple[str, int]:
    """Northbound ``modbus_slave`` (space, PDU address) for an IEC address."""
    return layout.to_slave(iec)


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
    # PLC -> its modbus_slave buffer_mapping
    layouts: dict[str, SlaveLayout] = Field(default_factory=dict)


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
        # (tag, IEC address, RIO space, RIO register) in allocation order; the
        # modbus_slave layout needs every address before any can be mapped.
        located: list[tuple[Tag, IecAddress, str | None, int | None]] = []
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
                    located.append((reg.tag, iec, reg.space, reg.address))
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
            located.append((t, iec, None, None))
        if mem_bit > MEM_BIT_LIMIT:
            raise PlantError(f"{plc}: {mem_bit} memory bits overflow into %QX100.0")
        layout = SlaveLayout.from_addresses(iec for _, iec, _, _ in located)
        alloc.layouts[plc] = layout
        for t, iec, rio_space, rio_address in located:
            s_space, s_addr = layout.to_slave(iec)
            alloc.tags[t.name] = TagAddress(
                tag=t,
                plc_iec=iec,
                slave_space=s_space,
                slave_address=s_addr,
                rio_space=rio_space,
                rio_address=rio_address,
            )
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
