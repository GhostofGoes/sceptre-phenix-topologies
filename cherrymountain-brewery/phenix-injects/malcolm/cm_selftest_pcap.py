#!/usr/bin/env python3
"""Write the pcap that cm-malcolm-selftest drops into Malcolm's pcap/upload.

Installed as /usr/local/lib/cm-malcolm/cm_selftest_pcap.py. The pcap holds a few
BACnet/IP datagrams that the range's baseline detections must flag, so one
upload shows the whole pipeline works: pcap-monitor, the Zeek processor with the
ICSNPP BACnet parser and cherrymountain.zeek, the Suricata processor with
cherrymountain.rules, and Filebeat and Logstash into OpenSearch.

The hosts are inventions in the DMZ and BAS address ranges that no real VM uses
(10.10.20.250 and 10.30.10.250), so test records never look like plant traffic.
Standard library plus pydantic (the cm-malcolm image installs it).

Usage: cm_selftest_pcap.py OUTPUT.pcap
"""

from __future__ import annotations

import socket
import struct
import sys
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

#: The pcap file format: magic, version 2.4, no timezone offset, snaplen, Ethernet.
PCAP_HEADER: bytes = struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 1)
#: A fixed timestamp keeps the file identical from run to run.
START_TIME: int = 1_700_000_000


class Datagram(BaseModel):
    """One UDP datagram."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    src: str
    dst: str
    sport: int = Field(ge=1, le=65535)
    dport: int = Field(ge=1, le=65535)
    payload: bytes

    def frame(self) -> bytes:
        """The Ethernet frame (fixed MAC addresses, IPv4 header checksum, UDP checksum 0)."""
        udp: bytes = struct.pack(">HHHH", self.sport, self.dport, 8 + len(self.payload), 0) + self.payload
        total: int = 20 + len(udp)
        head: bytes = struct.pack(
            ">BBHHHBBH4s4s",
            0x45,
            0,
            total,
            0x1234,
            0,
            64,
            17,
            0,
            socket.inet_aton(self.src),
            socket.inet_aton(self.dst),
        )
        checksum: int = ip_checksum(head)
        head = head[:10] + struct.pack(">H", checksum) + head[12:]
        eth: bytes = bytes.fromhex("02cc00000001") + bytes.fromhex("02cc00000002") + b"\x08\x00"
        return eth + head + udp


def ip_checksum(header: bytes) -> int:
    total: int = sum(struct.unpack(">10H", header))
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return ~total & 0xFFFF


#: Read-BDT, Write-BDT, Register-Foreign-Device, and Read-Foreign-Device-Table
#: (BVLC type 0x81 plus the function code, then the length), and a Who-Is
#: broadcast (a normal BACnet message that must not alert).
BVLC_READ_BDT: bytes = bytes.fromhex("81020004")
BVLC_WRITE_BDT: bytes = bytes.fromhex("81010004")
BVLC_REGISTER_FD: bytes = bytes.fromhex("81050006003c")
BVLC_READ_FDT: bytes = bytes.fromhex("81060004")
WHO_IS: bytes = bytes.fromhex("810b000c0120ffff00ff1008")

#: Not a BBMD, and not in the plant.
PROBE: str = "10.10.20.250"
BBMD: str = "10.30.10.5"


def datagrams() -> list[Datagram]:
    return [
        Datagram(src=PROBE, dst=BBMD, sport=47808, dport=47808, payload=payload)
        for payload in (BVLC_READ_BDT, BVLC_WRITE_BDT, BVLC_REGISTER_FD, BVLC_READ_FDT)
    ] + [Datagram(src="10.30.10.250", dst="10.30.10.255", sport=47808, dport=47808, payload=WHO_IS)]


def write_pcap(path: Path) -> int:
    out: bytearray = bytearray(PCAP_HEADER)
    frames: list[bytes] = [d.frame() for d in datagrams()]
    for index, frame in enumerate(frames):
        out += struct.pack("<IIII", START_TIME + index, 0, len(frame), len(frame)) + frame
    path.write_bytes(bytes(out))
    return len(frames)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    count: int = write_pcap(Path(argv[1]))
    print(f"wrote {count} BACnet datagrams to {argv[1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
