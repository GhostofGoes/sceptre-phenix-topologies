#!/usr/bin/env python3
"""Dependency-free BACnet/IP ReadProperty(device 3003, object-name).

Builds the same frame as the SoH probe in DESIGN §15.4 (base64
gQoAEQEEAAUBDAwCAAu7GU0=) and decodes the object-name from the reply.

Usage: readprop_raw.py HOST [--port 47808] [--device 3003] [--prop 77]
Exit 0 only if a ComplexACK with a character string arrives.
"""
import argparse
import base64
import socket
import sys

DESIGN_PACKET = "gQoAEQEEAAUBDAwCAAu7GU0="  # device 3003, property 77


def build(device: int, prop: int, invoke_id: int = 1) -> bytes:
    obj = (8 << 22) | device  # object type 8 = device
    apdu = bytes([0x00, 0x05, invoke_id, 0x0C, 0x0C]) + obj.to_bytes(4, "big")
    apdu += bytes([0x19, prop]) if prop < 256 else bytes([0x1A]) + prop.to_bytes(2, "big")
    npdu = bytes([0x01, 0x04])  # version 1, expecting reply
    body = npdu + apdu
    return bytes([0x81, 0x0A]) + (4 + len(body)).to_bytes(2, "big") + body


def decode_string(resp: bytes) -> str:
    """Return the first character string after the opening tag 3 (0x3E)."""
    i = resp.find(b"\x3e")
    if i < 0 or i + 2 >= len(resp):
        return ""
    tag = resp[i + 1]
    if tag >> 4 != 7:  # application tag 7 = CharacterString
        return ""
    ln = tag & 0x07
    pos = i + 2
    if ln == 5:
        ln = resp[pos]
        pos += 1
    enc = resp[pos]  # 0 = UTF-8
    return resp[pos + 1 : pos + ln].decode("utf-8", "replace") if enc == 0 else f"<enc {enc}>"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("host")
    ap.add_argument("--port", type=int, default=47808)
    ap.add_argument("--device", type=int, default=3003)
    ap.add_argument("--prop", type=int, default=77)
    a = ap.parse_args()

    pkt = build(a.device, a.prop)
    if (a.device, a.prop) == (3003, 77):
        assert pkt == base64.b64decode(DESIGN_PACKET), "frame differs from DESIGN packet"
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(3)
    try:
        s.sendto(pkt, (a.host, a.port))
        resp, src = s.recvfrom(1500)
    except OSError as exc:
        print(f"FAIL: no reply ({exc})")
        return 1
    print(f"reply from {src}: {resp.hex()}")
    # BVLC 0x81, NPDU version 1, APDU type 3 (ComplexACK) is 0x30 in the high nibble.
    is_ack = len(resp) > 8 and resp[0] == 0x81 and (resp[6] & 0xF0) == 0x30
    name = decode_string(resp) if is_ack else ""
    if is_ack and name:
        print(f"PASS: device {a.device} object-name = {name!r}")
        return 0
    print("FAIL: not a ComplexACK with an object-name (Error/Reject/Abort, or parse miss)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
