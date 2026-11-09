"""Synthetic traffic for the baseline detections of Malcolm (DESIGN.md 14).

Two pcaps that every detection must fire on, built with scapy:

* ``suricata_pcap``: one flow per Suricata rule of phenix-injects/malcolm/cherrymountain.rules
  (a Modbus write from a host that may not write, an S7comm PLC stop from a host
  that is not the EWS, BDT management from a host that is not a BBMD, and so on), next
  to flows that must stay quiet.
* ``zeek_pcap``: the same for cherrymountain.zeek, plus Modbus polls in which the ATS and
  the load-shed bit change.

``test_detection_traffic.py`` runs them through Suricata and through Zeek's image
when those are installed (and is skipped otherwise). Run the module to write the files:

    python3 detection_pcaps.py DIRECTORY

Needs scapy (pip install scapy). The traffic is hand-built and valid at the
protocol level (full TCP sessions, DNP3 link CRCs); it is no model of the plant.
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

from scapy.all import IP, TCP, UDP, Ether, Raw, wrpcap  # type: ignore[import-untyped]

#: What the Suricata pcap must raise: every sid of cherrymountain.rules.
EXPECTED_SIDS: frozenset[int] = frozenset(
    {
        9000110, 9000111, 9000112, 9000113, 9000114, 9000115, 9000116, 9000120, 9000121, 9000122, 9000123, 9000124,
        9000125, 9000126, 9000127, 9000130, 9000131, 9000132, 9000133, 9000134, 9000135, 9000136,
        9000137, 9000138, 9000139,
        9000140, 9000150, 9000151, 9000160, 9000161, 9000162, 9000163, 9000164,
    }
)
#: What the Zeek pcap must raise (the notice types of cherrymountain.zeek that need no TLS).
EXPECTED_NOTICES: frozenset[str] = frozenset(
    {
        "CherryMountain::BACnet_BBMD_Management",
        "CherryMountain::BACnet_Forwarded_NPDU_Unexpected",
        "CherryMountain::BACnet_Cross_Zone_Unexpected",
        "CherryMountain::BACnet_Write_Unexpected",
        "CherryMountain::Field_Host_Unknown",
        "CherryMountain::PLC_Program_Upload_Suspected",
        "CherryMountain::ATS_Transfer_To_Emergency",
        "CherryMountain::ATS_Transfer_To_Utility",
        "CherryMountain::Load_Shed_Active",
        "CherryMountain::Load_Shed_Cleared",
        "CherryMountain::DNP3_Breaker_Command",
    }
)


def crc_dnp(data: bytes) -> bytes:
    crc: int = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA6BC if crc & 1 else crc >> 1
    return struct.pack("<H", ~crc & 0xFFFF)


def dnp3_frame(ctrl: int, dest: int, src: int, user: bytes) -> bytes:
    head: bytes = b"\x05\x64" + bytes([5 + len(user), ctrl]) + struct.pack("<HH", dest, src)
    out: bytes = head + crc_dnp(head)
    for i in range(0, len(user), 16):
        block: bytes = user[i : i + 16]
        out += block + crc_dnp(block)
    return out


class Builder:
    """Collects packets, one tick of 10 ms apart."""

    def __init__(self) -> None:
        self.packets: list = []
        self.now: float = 1_700_000_000.0
        self.port: int = 30000

    def add(self, packet) -> None:
        packet.time = self.now
        self.now += 0.01
        self.packets.append(packet)

    def udp(self, src: str, dst: str, payload: bytes, dport: int = 47808, sport: int = 47808) -> None:
        self.add(Ether() / IP(src=src, dst=dst) / UDP(sport=sport, dport=dport) / Raw(payload))

    def tcp(self, src: str, dst: str, dport: int, request: bytes = b"", response: bytes = b"", sport: int | None = None) -> None:
        """A full session: handshake, one request, one answer, close."""
        if sport is None:
            self.port += 1
            sport = self.port
        seq, ack = 1000, 5000
        self.add(Ether() / IP(src=src, dst=dst) / TCP(sport=sport, dport=dport, flags="S", seq=seq))
        self.add(Ether() / IP(src=dst, dst=src) / TCP(sport=dport, dport=sport, flags="SA", seq=ack, ack=seq + 1))
        seq += 1
        ack += 1
        self.add(Ether() / IP(src=src, dst=dst) / TCP(sport=sport, dport=dport, flags="A", seq=seq, ack=ack))
        for i in range(0, len(request), 1400):
            chunk: bytes = request[i : i + 1400]
            self.add(Ether() / IP(src=src, dst=dst) / TCP(sport=sport, dport=dport, flags="PA", seq=seq, ack=ack) / Raw(chunk))
            seq += len(chunk)
            self.add(Ether() / IP(src=dst, dst=src) / TCP(sport=dport, dport=sport, flags="A", seq=ack, ack=seq))
        if response:
            self.add(Ether() / IP(src=dst, dst=src) / TCP(sport=dport, dport=sport, flags="PA", seq=ack, ack=seq) / Raw(response))
            ack += len(response)
            self.add(Ether() / IP(src=src, dst=dst) / TCP(sport=sport, dport=dport, flags="A", seq=seq, ack=ack))
        self.add(Ether() / IP(src=src, dst=dst) / TCP(sport=sport, dport=dport, flags="FA", seq=seq, ack=ack))
        self.add(Ether() / IP(src=dst, dst=src) / TCP(sport=dport, dport=sport, flags="FA", seq=ack, ack=seq + 1))
        self.add(Ether() / IP(src=src, dst=dst) / TCP(sport=sport, dport=dport, flags="A", seq=seq + 1, ack=ack + 1))

    def modbus_poll(self, src: str, dst: str, function: int, start: int, quantity: int, answers: list[bytes]) -> None:
        """One TCP session with a read per answer, so that a watched bit can change between reads."""
        self.port += 1
        sport: int = self.port
        seq, ack = 1000, 5000
        self.add(Ether() / IP(src=src, dst=dst) / TCP(sport=sport, dport=502, flags="S", seq=seq))
        self.add(Ether() / IP(src=dst, dst=src) / TCP(sport=502, dport=sport, flags="SA", seq=ack, ack=seq + 1))
        seq += 1
        ack += 1
        self.add(Ether() / IP(src=src, dst=dst) / TCP(sport=sport, dport=502, flags="A", seq=seq, ack=ack))
        for tid, data in enumerate(answers, start=1):
            request: bytes = struct.pack(">HHHBBHH", tid, 0, 6, 1, function, start, quantity)
            self.add(Ether() / IP(src=src, dst=dst) / TCP(sport=sport, dport=502, flags="PA", seq=seq, ack=ack) / Raw(request))
            seq += len(request)
            response: bytes = struct.pack(">HHHBBB", tid, 0, 3 + len(data), 1, function, len(data)) + data
            self.add(Ether() / IP(src=dst, dst=src) / TCP(sport=502, dport=sport, flags="PA", seq=ack, ack=seq) / Raw(response))
            ack += len(response)
            self.add(Ether() / IP(src=src, dst=dst) / TCP(sport=sport, dport=502, flags="A", seq=seq, ack=ack))


def mbap(tid: int, pdu: bytes) -> bytes:
    return struct.pack(">HHHB", tid, 0, len(pdu) + 1, 1) + pdu


def s7(function: int, extra: bytes = b"") -> bytes:
    """An S7comm job: TPKT, COTP, the S7 header, and the function as the first parameter byte."""
    param: bytes = bytes([function]) + extra
    header: bytes = bytes.fromhex("3201") + b"\x00\x00" + b"\x00\x01" + struct.pack(">HH", len(param), 0)
    body: bytes = bytes.fromhex("02f080") + header + param
    return b"\x03\x00" + struct.pack(">H", len(body) + 4) + body


READ_PROPERTY: bytes = bytes.fromhex("810a001101040005010c0c02000bb9194d")
WRITE_PROPERTY: bytes = bytes.fromhex("810a001901040005020f0c0100000119553e91013f")
WRITE_PROPERTY_MULTIPLE: bytes = bytes.fromhex("810a001901040005021 00c0100000119553e91013f".replace(" ", ""))
FORWARDED_NPDU: bytes = bytes.fromhex("81040012") + bytes([10, 20, 30, 10, 0xBA, 0xC0]) + bytes.fromhex("0120ffff00ff1008")
WHO_IS: bytes = bytes.fromhex("810b000c0120ffff00ff1008")


def FORWARDED_WRITE(original: bytes) -> bytes:  # noqa: N802 (reads like the constants)
    """An Original-Unicast request rewrapped as a Forwarded-NPDU from 10.20.20.10."""
    npdu: bytes = original[4:]
    return bytes([0x81, 0x04]) + struct.pack(">H", 10 + len(npdu)) + bytes([10, 20, 20, 10, 0xBA, 0xC0]) + npdu


def suricata_pcap(path: Path) -> None:
    b: Builder = Builder()
    write_6: bytes = mbap(1, bytes([6]) + struct.pack(">HH", 2, 999))
    write_15: bytes = mbap(2, bytes([15]) + struct.pack(">HHBB", 0, 1, 1, 1))
    read_3: bytes = mbap(3, bytes([3]) + struct.pack(">HH", 0, 4))
    # Modbus: only the first is a violation.
    b.tcp("10.10.20.11", "10.20.10.12", 502, write_6, write_6)
    b.tcp("10.20.30.12", "10.20.10.12", 502, write_6, write_6)
    b.tcp("10.20.30.10", "10.20.10.12", 502, write_15, b"")
    b.tcp("10.20.12.1", "10.20.12.10", 502, write_15, b"")
    b.tcp("10.20.20.10", "10.20.10.12", 502, read_3, b"")
    # S7comm: a write and a PLC stop from the wrong hosts.
    write_var: bytes = s7(0x05, bytes.fromhex("01120a10020001000184000000") + bytes.fromhex("000400"))
    b.tcp("10.10.20.11", "10.20.10.13", 102, write_var)
    b.tcp("10.20.30.10", "10.20.10.13", 102, write_var)
    b.tcp("10.20.30.10", "10.20.10.13", 102, s7(0x29, b"\x00\x00\x00\x00\x00\x09P_PROGRAM"))
    b.tcp("10.20.30.12", "10.20.10.13", 102, s7(0x29, b"\x00\x00\x00\x00\x00\x09P_PROGRAM"))
    # BACnet: BBMDs talking to each other are fine; the rest is not.
    b.udp("10.20.30.13", "10.30.10.5", FORWARDED_NPDU)
    b.udp("10.30.10.5", "10.20.30.13", FORWARDED_NPDU)
    b.udp("10.20.30.10", "10.30.10.22", READ_PROPERTY)
    b.udp("10.20.30.12", "10.30.10.22", WRITE_PROPERTY)
    # scada-fuxa only reads the BAS devices (DESIGN.md 6.7): its write alerts.
    b.udp("10.20.30.10", "10.30.10.22", WRITE_PROPERTY)
    # A write sent as Distribute-Broadcast-To-Network (0x09) is still a write, and
    # one relayed in a Forwarded-NPDU (here by the BAS BBMD) is never expected.
    b.udp("10.20.20.10", "10.30.10.5", bytes([0x81, 0x09]) + WRITE_PROPERTY[2:])
    b.udp("10.30.10.5", "10.30.10.22", FORWARDED_WRITE(WRITE_PROPERTY))
    b.udp("10.30.10.5", "10.30.10.22", FORWARDED_WRITE(WRITE_PROPERTY_MULTIPLE))
    b.udp("10.20.20.10", "10.30.10.22", WRITE_PROPERTY)
    b.udp("10.20.20.10", "10.30.10.22", WRITE_PROPERTY_MULTIPLE)
    for function in (1, 2, 5, 6, 8):
        b.udp("10.10.20.11", "10.30.10.5", bytes([0x81, function, 0, 4]))
    b.udp("10.20.20.10", "10.30.10.21", FORWARDED_NPDU)
    b.udp("10.30.10.20", "10.20.20.10", READ_PROPERTY)
    # DNP3: control operations from the wrong host and from utility-derms.
    crob: bytes = bytes([0x41, 0x01]) + struct.pack("<II", 100, 100) + bytes([0x00])

    def operate(function: int) -> bytes:
        return dnp3_frame(0xC4, 4, 3, bytes([0xC1, 0xC1, function, 0x0C, 0x01, 0x17, 0x01, 0x02]) + crob)

    for function in (3, 4, 5, 6):
        b.tcp("10.10.20.11", "10.20.15.10", 20000, operate(function))
    # From utility-derms: a CROB on BO 2 (the packaging feeder, 9000137 and 9000138),
    # one on BO 2 with a 2-byte index (9000139), and the analog setpoints of the
    # expected DER dispatch in each Analog Output Block variation (9000134-9000136).
    b.tcp("192.0.2.10", "10.20.15.10", 20000, operate(5))
    b.tcp(
        "192.0.2.10",
        "10.20.15.10",
        20000,
        dnp3_frame(0xC4, 4, 3, bytes([0xC1, 0xC1, 5, 0x0C, 0x01, 0x28, 0x01, 0x00, 0x02, 0x00]) + crob),
    )
    for variation, value in ((1, struct.pack("<i", 500)), (2, struct.pack("<h", 500)), (3, struct.pack("<f", 50.0))):
        ao: bytes = bytes([0xC1, 0xC1, 5, 0x29, variation, 0x17, 0x01, 0x03]) + value + b"\x00"
        b.tcp("192.0.2.10", "10.20.15.10", 20000, dnp3_frame(0xC4, 4, 3, ao))
    # A host that is not in the inventory on a field VLAN, and one that is.
    b.tcp("10.20.11.99", "10.20.11.10", 502, read_3, b"")
    b.tcp("10.20.11.1", "10.20.11.10", 502, read_3, b"")
    # OpenPLC REST, and the vendor path.
    b.tcp("10.10.20.11", "10.20.10.12", 8443, b"A" * 200)
    b.tcp("10.20.30.12", "10.20.10.12", 8443, b"A" * 200)
    b.udp("203.0.113.2", "10.10.20.10", bytes.fromhex("01000000") + b"\x11" * 144, dport=51820, sport=50000)
    b.tcp("10.99.0.2", "10.10.20.11", 443, b"GET / HTTP/1.1\r\n\r\n")
    b.tcp("10.99.0.2", "10.20.30.12", 3389, b"x" * 50)
    b.tcp("10.10.20.11", "10.20.30.12", 3389, b"x" * 50)
    b.tcp("10.10.20.12", "10.20.30.12", 22, b"SSH-2.0-x\r\n")
    wrpcap(str(path), b.packets)


def zeek_pcap(path: Path) -> None:
    b: Builder = Builder()
    read_3: bytes = mbap(3, bytes([3]) + struct.pack(">HH", 0, 4))
    b.udp("10.20.30.13", "10.30.10.5", FORWARDED_NPDU)
    b.udp("10.30.10.5", "10.20.30.13", FORWARDED_NPDU)
    b.udp("10.30.10.10", "10.30.10.22", READ_PROPERTY)
    b.udp("10.20.30.12", "10.30.10.22", WRITE_PROPERTY)
    b.udp("10.30.10.10", "10.30.10.22", WRITE_PROPERTY)
    b.udp("10.10.20.11", "10.30.10.5", bytes.fromhex("81020004"))
    b.udp("10.10.20.11", "10.30.10.5", bytes.fromhex("81010004"))
    b.udp("10.20.30.10", "10.30.10.5", bytes.fromhex("810500060000"))
    b.udp("10.20.20.10", "10.30.10.21", FORWARDED_NPDU)
    b.udp("10.20.20.10", "10.30.10.22", WRITE_PROPERTY)
    b.tcp("10.20.11.99", "10.20.11.10", 502, read_3, b"")
    b.tcp("10.20.11.1", "10.20.11.10", 502, read_3, b"")
    b.tcp("10.20.30.12", "10.20.10.12", 8443, b"A" * 90000)
    # gen-ats discrete inputs 10001 to 10003 (running, on the emergency source, utility ok):
    # the emergency bit goes 0, 0, 1, 1, 0. plc-power's coil 7 (load shed) goes 0, 1, 0.
    b.modbus_poll("10.20.16.1", "10.20.16.23", 2, 10001, 3, [b"\x05", b"\x05", b"\x07", b"\x07", b"\x05"])
    b.modbus_poll("10.20.30.10", "10.20.10.15", 1, 0, 16, [b"\x00\x00", b"\x80\x00", b"\x00\x00"])
    # A DNP3 CROB from utility-derms on rtu-der BO 1 (the boiler feeder).
    crob: bytes = bytes([0x41, 0x01]) + struct.pack("<II", 100, 100) + bytes([0x00])
    b.tcp(
        "192.0.2.10",
        "10.20.15.10",
        20000,
        dnp3_frame(0xC4, 4, 3, bytes([0xC1, 0xC1, 5, 0x0C, 0x01, 0x17, 0x01, 0x01]) + crob),
    )
    wrpcap(str(path), b.packets)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    out: Path = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    suricata_pcap(out / "suricata-detections.pcap")
    zeek_pcap(out / "zeek-detections.pcap")
    print(f"wrote the pcaps to {out}")
