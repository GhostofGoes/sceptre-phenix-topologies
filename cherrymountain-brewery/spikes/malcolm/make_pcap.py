#!/usr/bin/env python3
"""Generate a small pcap with Modbus/TCP, DNP3 and BACnet/IP traffic using scapy.

Used by the Malcolm spike to check that Zeek with the CISA ICSNPP parsers
produces logs for the plant's protocols. The traffic is hand-built, valid at
the protocol level (full TCP sessions, DNP3 link CRCs), and fixed apart from
timestamps.

Usage: make_pcap.py OUTPUT.pcap [REPEAT]
Needs: pip install scapy pydantic
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

from pydantic import BaseModel, Field
from scapy.all import IP, TCP, UDP, Ether, PcapWriter, Raw  # type: ignore[import-untyped]

MAC_A: str = "02:00:00:00:00:0a"
MAC_B: str = "02:00:00:00:00:0b"


class Host(BaseModel):
    ip: str
    mac: str
    port: int


class Exchange(BaseModel):
    """One application message and its response."""

    request: bytes
    response: bytes


class TcpFlow(BaseModel):
    name: str
    client: Host
    server: Host
    exchanges: list[Exchange] = Field(default_factory=list)


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


def dnp3_exchanges() -> list[Exchange]:
    master, outstation = 3, 4
    # Transport FIR|FIN, application FIR|FIN.
    read_class0: bytes = bytes([0xC0, 0xC0, 0x01, 0x3C, 0x01, 0x06])
    # Response: IIN 0, g30v2 (16-bit analog with flag), two points.
    analog: bytes = bytes([0xC0, 0xC0, 0x81, 0x00, 0x00, 0x1E, 0x02, 0x00, 0x00, 0x01]) + bytes([0x01]) + struct.pack("<H", 215) + bytes([0x01]) + struct.pack("<H", 640)
    # Direct operate (function 5): g12v1 CROB, index 2, latch on.
    crob: bytes = bytes([0x41, 0x01]) + struct.pack("<II", 100, 100) + bytes([0x00])
    operate: bytes = bytes([0xC1, 0xC1, 0x05, 0x0C, 0x01, 0x17, 0x01, 0x02]) + crob
    operate_resp: bytes = bytes([0xC1, 0xC1, 0x81, 0x00, 0x00, 0x0C, 0x01, 0x17, 0x01, 0x02]) + crob
    return [
        Exchange(request=dnp3_frame(0xC4, outstation, master, read_class0), response=dnp3_frame(0x44, master, outstation, analog)),
        Exchange(request=dnp3_frame(0xC4, outstation, master, operate), response=dnp3_frame(0x44, master, outstation, operate_resp)),
    ]


def modbus_exchanges() -> list[Exchange]:
    def mbap(tid: int, pdu: bytes) -> bytes:
        return struct.pack(">HHHB", tid, 0, len(pdu) + 1, 1) + pdu

    return [
        Exchange(request=mbap(1, bytes([3]) + struct.pack(">HH", 0, 4)), response=mbap(1, bytes([3, 8]) + struct.pack(">4H", 111, 222, 333, 444))),
        Exchange(request=mbap(2, bytes([6]) + struct.pack(">HH", 2, 999)), response=mbap(2, bytes([6]) + struct.pack(">HH", 2, 999))),
        Exchange(request=mbap(3, bytes([1]) + struct.pack(">HH", 0, 8)), response=mbap(3, bytes([1, 1, 0b00000101]))),
    ]


class Capture:
    """Writes packets with increasing timestamps."""

    def __init__(self, path: Path) -> None:
        self.writer: PcapWriter = PcapWriter(str(path), sync=True)
        self.now: float = 1_760_000_000.0

    def emit(self, pkt: object) -> None:
        self.now += 0.01
        pkt.time = self.now  # type: ignore[attr-defined]
        self.writer.write(pkt)

    def tcp(self, flow: TcpFlow) -> None:
        c, s = flow.client, flow.server
        seq_c: int = 1000
        seq_s: int = 5000

        def seg(from_client: bool, flags: str, payload: bytes = b"") -> None:
            nonlocal seq_c, seq_s
            if from_client:
                pkt = Ether(src=c.mac, dst=s.mac) / IP(src=c.ip, dst=s.ip) / TCP(sport=c.port, dport=s.port, flags=flags, seq=seq_c, ack=seq_s if "A" in flags else 0)
            else:
                pkt = Ether(src=s.mac, dst=c.mac) / IP(src=s.ip, dst=c.ip) / TCP(sport=s.port, dport=c.port, flags=flags, seq=seq_s, ack=seq_c)
            if payload:
                pkt = pkt / Raw(payload)
            self.emit(pkt)
            advance: int = len(payload) + (1 if "S" in flags or "F" in flags else 0)
            if from_client:
                seq_c += advance
            else:
                seq_s += advance

        seg(True, "S")
        seg(False, "SA")
        seg(True, "A")
        for ex in flow.exchanges:
            seg(True, "PA", ex.request)
            seg(False, "A")
            seg(False, "PA", ex.response)
            seg(True, "A")
        seg(True, "FA")
        seg(False, "FA")
        seg(True, "A")

    def udp(self, src: Host, dst: Host, payload: bytes, dst_mac: str | None = None) -> None:
        self.emit(Ether(src=src.mac, dst=dst_mac or dst.mac) / IP(src=src.ip, dst=dst.ip) / UDP(sport=src.port, dport=dst.port) / Raw(payload))


class Datagram(BaseModel):
    from_a: bool
    broadcast: bool = False
    payload: bytes


def bacnet_datagrams() -> list[Datagram]:
    """Who-Is (broadcast), I-Am for device 3003, ReadProperty object-name and its ACK."""
    return [
        Datagram(from_a=True, broadcast=True, payload=bytes.fromhex("810b000c0120ffff00ff1008")),
        Datagram(from_a=False, payload=bytes.fromhex("810a001401001000c402000bbb220" "1e0910321ff")),
        Datagram(from_a=True, payload=bytes.fromhex("810a001101040005010c0c02000bbb194d")),
        Datagram(from_a=False, payload=bytes.fromhex("810a001701003001" "0c0c02000bbb194d3e750300636d3f")),
    ]


def main(out: Path, repeat: int = 1) -> int:
    """Write the traffic once, or `repeat` times with a distinct client address per
    repetition (a bulk load for measuring Zeek's memory and CPU)."""
    cap: Capture = Capture(out)
    for i in range(repeat):
        client_ip: str = "10.20.30.30" if repeat == 1 else f"10.{20 + i // 62500}.{100 + (i // 250) % 250}.{i % 250 + 2}"
        a: Host = Host(ip=client_ip, mac=MAC_A, port=0)
        b: Host = Host(ip="10.20.30.31", mac=MAC_B, port=0)
        cap.tcp(TcpFlow(name="modbus", client=a.model_copy(update={"port": 40502}), server=b.model_copy(update={"port": 502}), exchanges=modbus_exchanges()))
        cap.tcp(TcpFlow(name="dnp3", client=a.model_copy(update={"port": 40020}), server=b.model_copy(update={"port": 20000}), exchanges=dnp3_exchanges()))
        ua: Host = a.model_copy(update={"port": 47808})
        ub: Host = b.model_copy(update={"port": 47808})
        bcast: Host = ub.model_copy(update={"ip": "10.20.30.255"})
        for dg in bacnet_datagrams():
            if dg.from_a and dg.broadcast:
                cap.udp(ua, bcast, dg.payload, dst_mac="ff:ff:ff:ff:ff:ff")
            elif dg.from_a:
                cap.udp(ua, ub, dg.payload)
            else:
                cap.udp(ub, ua, dg.payload)
    cap.writer.close()
    print(f"wrote {out} ({out.stat().st_size} bytes, {repeat} repetition(s))")
    return 0


if __name__ == "__main__":
    if len(sys.argv) not in (2, 3):
        print(__doc__)
        sys.exit(2)
    sys.exit(main(Path(sys.argv[1]), int(sys.argv[2]) if len(sys.argv) == 3 else 1))
