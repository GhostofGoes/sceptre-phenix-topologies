#!/usr/bin/env python3
"""Who-Is and ReadProperty client that behaves like FUXA's node-bacnet 0.2.4.

node-bacnet 0.2.4 sends its Who-Is to 255.255.255.255:47808 from UDP 47808 and
resolves a device from the I-Am it gets back. This script does the same with
plain sockets (no BACpypes3, so it runs on any guest), then reads the
object-name of the first device found with a unicast ReadProperty.

Usage: whois_client.py [--wait 6] [--device 3003] [--bind 0.0.0.0]
Prints one JSON line:
  {"iams": [{"instance": 3003, "address": "10.20.60.13", "via": "10.20.30.5"}],
   "read": {"address": "10.20.60.13", "object_name": "bas-vent"}}
Exit 0 when the wanted device answered the Who-Is and the read worked.
"""

import argparse
import json
import socket
import sys
import time

PORT: int = 47808
WHO_IS: bytes = bytes.fromhex("810b000c0120ffff00ff1008")


def parse_iam(data: bytes, sender: tuple[str, int]) -> tuple[int, str] | None:
    """Return (device instance, originating address) of an I-Am datagram."""
    if len(data) < 6 or data[0] != 0x81:
        return None
    func: int = data[1]
    origin: str = sender[0]
    if func == 0x04:  # Forwarded-NPDU: 6-byte originating address follows
        origin = socket.inet_ntoa(data[4:8])
        npdu: bytes = data[10:]
    elif func in (0x0A, 0x0B):
        npdu = data[4:]
    else:
        return None
    if len(npdu) < 2 or npdu[0] != 1:
        return None
    pos: int = 2
    control: int = npdu[1]
    if control & 0x20:  # DNET, DLEN, DADR, hop count
        pos += 2 + 1 + npdu[pos + 2] + 1
    if control & 0x08:  # SNET, SLEN, SADR
        pos += 2 + 1 + npdu[pos + 2]
    apdu: bytes = npdu[pos:]
    if len(apdu) < 7 or apdu[0] != 0x10 or apdu[1] != 0x00 or apdu[2] != 0xC4:
        return None
    obj: int = int.from_bytes(apdu[3:7], "big")
    if obj >> 22 != 8:
        return None
    return obj & 0x3FFFFF, origin


def read_property(sock: socket.socket, host: str, device: int, prop: int = 77) -> str:
    obj: int = (8 << 22) | device
    apdu: bytes = bytes([0x00, 0x05, 0x01, 0x0C, 0x0C]) + obj.to_bytes(4, "big") + bytes([0x19, prop])
    body: bytes = bytes([0x01, 0x04]) + apdu
    sock.sendto(bytes([0x81, 0x0A]) + (4 + len(body)).to_bytes(2, "big") + body, (host, PORT))
    sock.settimeout(4)
    data, _ = sock.recvfrom(1500)
    i: int = data.find(b"\x3e")
    if i < 0 or data[i + 1] >> 4 != 7:
        raise ValueError(f"no character string in {data.hex()}")
    length: int = data[i + 1] & 7
    pos: int = i + 2
    if length == 5:
        length, pos = data[pos], pos + 1
    return data[pos + 1 : pos + length].decode("utf-8", "replace")


def main() -> int:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wait", type=float, default=6.0)
    parser.add_argument("--device", type=int, default=3003)
    parser.add_argument("--bind", default="0.0.0.0")  # noqa: S104
    args: argparse.Namespace = parser.parse_args()

    sock: socket.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.bind((args.bind, PORT))
    sock.sendto(WHO_IS, ("255.255.255.255", PORT))

    iams: list[dict[str, object]] = []
    deadline: float = time.monotonic() + args.wait
    while time.monotonic() < deadline:
        sock.settimeout(max(0.1, deadline - time.monotonic()))
        try:
            data, sender = sock.recvfrom(1500)
        except TimeoutError:
            break
        found: tuple[int, str] | None = parse_iam(data, sender)
        if found and not any(i["instance"] == found[0] for i in iams):
            iams.append({"instance": found[0], "address": found[1], "via": sender[0]})

    result: dict[str, object] = {"iams": iams, "read": None}
    wanted: list[dict[str, object]] = [i for i in iams if i["instance"] == args.device]
    ok: bool = False
    if wanted:
        address: str = str(wanted[0]["address"])
        try:
            result["read"] = {"address": address, "object_name": read_property(sock, address, args.device)}
            ok = True
        except (OSError, ValueError) as exc:
            result["read"] = {"address": address, "error": str(exc)}
    print(json.dumps(result), flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
