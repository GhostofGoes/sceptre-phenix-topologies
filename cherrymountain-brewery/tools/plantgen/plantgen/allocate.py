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
- **Read-only holding registers.** A holding register another host writes
  (``written_by``, for example the utility limit the DER gateway sets on the
  PV inverter) is polled with FC3 into ``%IW``, so ``modbus_master`` never
  overwrites it. It takes the next ``%IW`` word after the RIO's input
  registers.
- **BACnet.** Objects are numbered from 1 per object type, in host device
  order then point order (the same rule the phenix app applies to Modbus
  registers), and addressed in FUXA as ``<type number>-<instance>`` with
  ``memaddress`` set to the remote device instance.
"""

from __future__ import annotations

from collections import defaultdict
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
#: modbus_master function code for holding registers the PLC only reads.
READ_HOLDING_FC: int = 3
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
#: First IEC bit of the peer reads (bits another PLC computes): bit 960 is
#: %IX120.0, clear of any RIO discrete inputs.
PEER_BIT_BASE: int = 960
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
    read_only: bool = False  # holding register the PLC polls with FC3


class BacnetObject(BaseModel):
    """One BACnet object on a BACnet controller (ot-sim BACnet module)."""

    host: str
    object_type: str
    type_number: int
    instance: int
    device: Device
    point: str
    tag: Tag | None

    @property
    def fuxa_address(self) -> str:
        """FUXA BACnet tag address: ``<type number>-<instance>``."""
        return f"{self.type_number}-{self.instance}"


class MasterBlock(BaseModel):
    """One ``modbus_master`` io_point: a run of registers with one FC."""

    rio: str
    space: str
    fc: int
    start: int  # first ot-sim literal register
    count: int
    iec: IecAddress  # IEC location of the first register


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
        if self.tag.written_by:
            return READ_HOLDING_FC
        return MASTER_FC[self.rio_space] if self.rio_space else None


class PeerRead(BaseModel):
    """One bit a PLC reads from another PLC's coils (FC1)."""

    tag: str  # the reading PLC's tag
    iec: IecAddress  # where it lands, %IX120.0 up
    source: str  # the other PLC's tag
    source_plc: str
    coil: int  # PDU address of the source tag in the other PLC's modbus_slave


class Allocation(BaseModel):
    """All addresses for a variant."""

    rio_registers: dict[str, list[RioRegister]] = Field(default_factory=dict)
    tags: dict[str, TagAddress] = Field(default_factory=dict)
    # PLC -> ordered list of RIO hosts it polls
    plc_rios: dict[str, list[str]] = Field(default_factory=dict)
    # PLC -> its modbus_slave buffer_mapping
    layouts: dict[str, SlaveLayout] = Field(default_factory=dict)
    # BACnet controller -> its objects
    bacnet: dict[str, list[BacnetObject]] = Field(default_factory=dict)
    # PLC -> the bits it reads from other PLCs (east-west peer interlocks)
    peer_reads: dict[str, list[PeerRead]] = Field(default_factory=dict)



def rio_devices(plant: Plant, rio: str) -> list[Device]:
    """Devices served by ``rio``, in plant order (= phenix host device order)."""
    return [d for d in plant.devices if d.rio == rio]


def master_blocks(alloc: Allocation, rio: str) -> list[MasterBlock]:
    """The modbus_master io_points of one RIO, in space order.

    Each space is one block, except that holding registers split into runs
    the PLC writes (FC16 from ``%QW``) and runs it only reads (FC3 into
    ``%IW``). A run breaks where the register or the IEC index is not
    consecutive.
    """
    blocks: list[MasterBlock] = []
    for space in ("discrete", "input", "coil", "holding"):
        regs = sorted(
            (r for r in alloc.rio_registers[rio] if r.space == space and r.tag),
            key=lambda r: r.address,
        )
        for reg in regs:
            fc = READ_HOLDING_FC if reg.read_only else MASTER_FC[space]
            iec = alloc.tags[reg.tag.name].plc_iec  # type: ignore[union-attr]
            last = blocks[-1] if blocks else None
            if (
                last is not None
                and last.space == space
                and last.fc == fc
                and last.start + last.count == reg.address
                and last.iec.area == iec.area
                and last.iec.index + last.count == iec.index
            ):
                blocks[-1] = last.model_copy(update={"count": last.count + 1})
            else:
                blocks.append(
                    MasterBlock(
                        rio=rio, space=space, fc=fc, start=reg.address, count=1, iec=iec
                    )
                )
    return blocks


def allocate(plant: Plant) -> Allocation:
    """Assign RIO, PLC, slave, and FUXA addresses for every tag."""
    alloc = Allocation()
    bound = {(t.device.name, t.point): t for t in plant.tags if t.device}

    # 1. ot-sim literal registers, replicating the phenix app's numbering.
    modbus_rios = {d.rio for d in plant.devices if d.protocol == "modbus"}
    for rio in sorted(modbus_rios, key=_order(plant)):
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
                        read_only=point.written_by is not None,
                    )
                )
                counters[space] += 1
        alloc.rio_registers[rio] = regs

    # 1b. BACnet objects: numbered from 1 per object type on each controller.
    bacnet_hosts = {d.rio for d in plant.devices if d.protocol == "bacnet"}
    for host in sorted(bacnet_hosts, key=_order(plant)):
        instances: dict[str, int] = defaultdict(lambda: 1)
        objects: list[BacnetObject] = []
        for dev in rio_devices(plant, host):
            for point in plant.device_types[dev.type].values():
                bac = point.bacnet
                if bac is None:  # pragma: no cover - _protocol forbids mixing
                    raise PlantError(f"{dev.name}.{point.name}: not a BACnet point")
                objects.append(
                    BacnetObject(
                        host=host,
                        object_type=bac.object_type,
                        type_number=bac.type_number,
                        instance=instances[bac.object_type],
                        device=dev,
                        point=point.name,
                        tag=bound.get((dev.name, point.name)),
                    )
                )
                instances[bac.object_type] += 1
        alloc.bacnet[host] = objects

    # 2. PLC image: RIO blocks from %X100.0 / %W100, PLC memory below/at %MW.
    for plc in sorted({t.plc for t in plant.tags if t.plc}, key=_order(plant)):
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
                for reg in alloc.rio_registers.get(rio, []):
                    if reg.space != space:
                        continue
                    # A read-only holding register is read into %IW.
                    target = "input" if reg.read_only else space
                    area, size = IO_IEC[target]
                    iec = IecAddress(area=area, size=size, index=next_io[target])
                    next_io[target] += 1
                    if reg.tag is None:
                        continue  # reported by checks.unbound_points
                    located.append((reg.tag, iec, reg.space, reg.address))
        mem_bit = 0
        mem_word = 0
        peer_bit = PEER_BIT_BASE
        for t in plant.tags:
            if t.plc != plc or t.device:
                continue
            if t.peer:
                iec = IecAddress(area="I", size="X", index=peer_bit)
                peer_bit += 1
                located.append((t, iec, None, None))
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

    # 3. Peer reads: each lands at its %IX bit and is read with FC1 from the
    # other PLC's modbus_slave coil, which exists only now that every PLC has
    # its layout.
    for t in plant.tags:
        if not t.peer or t.plc is None:
            continue
        src = alloc.tags.get(t.peer)
        if src is None:
            raise PlantError(f"{t.name}: peer {t.peer} is not a PLC tag of this variant")
        if src.tag.plc in (None, t.plc) or src.tag.device or src.tag.type != "binary-read":
            raise PlantError(f"{t.name}: peer {t.peer} must be a bit another PLC computes (binary-read, no io)")
        if src.slave_space != "coil":
            raise PlantError(f"{t.name}: peer {t.peer} is not a coil of {src.tag.plc}")
        alloc.peer_reads.setdefault(t.plc, []).append(
            PeerRead(
                tag=t.name,
                iec=alloc.tags[t.name].plc_iec,
                source=t.peer,
                source_plc=src.tag.plc,
                coil=src.slave_address,
            )
        )
    return alloc


def access(tag: Tag) -> str:
    """SCADA access: PLC-memory setpoints and commands are read-write.

    Writable RIO points (valve commands, spunding setpoints) are driven by
    the PLC program every scan, so SCADA only reads them. Writable BACnet
    objects have no PLC in front of them, so the BAS front end writes them.
    """
    if tag.bacnet is not None:
        return "read-write" if is_writable(tag.type) else "read"
    return "read-write" if is_writable(tag.type) and not tag.device else "read"


def _order(plant: Plant) -> Callable[[str], int]:
    """Sort key: position of a host in the variant host list."""
    names = list(plant.spec.hosts)
    return lambda h: names.index(h) if h in names else len(names)
