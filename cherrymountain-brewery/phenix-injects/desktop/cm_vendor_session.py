#!/usr/bin/env python3
"""The vendor technician's scripted Guacamole session (DESIGN.md 13 and 15.6).

Installed on vendor-ws as /usr/local/lib/cm-desktop/cm_vendor_session.py and run
by the SCORCH `vendor-maintenance` run through cc. It does what Ridgeline's
technician does in a browser, over the WireGuard tunnel:

1. check that the tunnel to dmz-jump answers on TCP 443,
2. sign in to Guacamole with the user name, the password, and a TOTP code
   (REST ``POST /api/tokens``),
3. find the ``ews-rdp`` connection, open a session to it through the web socket
   tunnel, and keep it open for ``--hold`` seconds, answering the server's sync
   messages and moving the mouse now and then, so the session is a real, recorded
   one (the step passes when the remote screen draws, and fails on an error),
4. sign out.

Each step is written to ``--log`` (JSON lines) and the exit code is 0 only if every
step passed, so a cc step can check it. The credentials and the TOTP seed come from
ridgeline.env (injected on vendor-ws, mode 0600; range-only values). The server's
certificate is self-signed, so it is not verified (a range-only choice, noted in the
log). Standard library plus pydantic.

Usage: cm_vendor_session.py [--url https://10.10.20.11] [--hold 60] [--connection ews-rdp]
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import socket
import ssl
import struct
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Annotated, ClassVar
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

DEFAULT_ENV: Path = Path("/etc/cherrymountain/ridgeline.env")
DEFAULT_LOG: Path = Path("/var/log/cm-vendor-session.jsonl")
DEFAULT_URL: str = "https://10.10.20.11"
WEBSOCKET_GUID: str = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
TOTP_PERIOD: int = 30
TOTP_DIGITS: int = 6

Name = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")]
Secret = Annotated[str, StringConstraints(min_length=8, max_length=128)]
Base32 = Annotated[str, StringConstraints(pattern=r"^[A-Z2-7]{16,64}$")]


class Vendor(BaseModel):
    """The part of ridgeline.env that the laptop uses."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore", frozen=True)

    VENDOR_GUAC_USER: Name
    VENDOR_GUAC_PASSWORD: Secret
    VENDOR_TOTP_SECRET: Base32

    @classmethod
    def from_file(cls, path: Path) -> Vendor:
        values: dict[str, str] = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                key, _, value = line.partition("=")
                values[key] = value
        return cls.model_validate(values)


class Step(BaseModel):
    time: str
    step: str
    ok: bool
    detail: str = ""


class Session(BaseModel):
    """What happened, step by step."""

    steps: list[Step] = Field(default_factory=list)

    def record(self, step: str, ok: bool, detail: str = "", log: Path | None = None) -> bool:
        entry: Step = Step(time=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), step=step, ok=ok, detail=detail)
        self.steps.append(entry)
        line: str = entry.model_dump_json()
        print(line, flush=True)
        if log is not None:
            with log.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        return ok

    @property
    def passed(self) -> bool:
        return bool(self.steps) and all(step.ok for step in self.steps)


def totp(secret_b32: str, now: float, period: int = TOTP_PERIOD, digits: int = TOTP_DIGITS) -> str:
    """RFC 6238 time-based one-time password (HMAC-SHA1), as Guacamole's TOTP extension checks it."""
    key: bytes = base64.b32decode(secret_b32 + "=" * (-len(secret_b32) % 8))
    counter: bytes = struct.pack(">Q", int(now) // period)
    digest: bytes = hmac.new(key, counter, hashlib.sha1).digest()
    offset: int = digest[-1] & 0x0F
    code: int = (struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF) % (10**digits)
    return str(code).zfill(digits)


# --- the Guacamole protocol ---------------------------------------------------------


def encode_instruction(opcode: str, *args: str) -> str:
    """``5.mouse,1.0;``: every element is its length in characters, a dot, and the text."""
    return ",".join(f"{len(part)}.{part}" for part in (opcode, *args)) + ";"


def decode_instructions(text: str) -> list[list[str]]:
    """The instructions in a chunk of the stream, each as [opcode, *args]. An incomplete tail is ignored."""
    instructions: list[list[str]] = []
    position: int = 0
    current: list[str] = []
    while position < len(text):
        dot: int = text.find(".", position)
        if dot < 0:
            break
        length: int = int(text[position:dot])
        end: int = dot + 1 + length
        if end >= len(text):
            break
        current.append(text[dot + 1 : end])
        terminator: str = text[end]
        position = end + 1
        if terminator == ";":
            instructions.append(current)
            current = []
        elif terminator != ",":
            raise ValueError("a Guacamole element must end with , or ;")
    return instructions


# --- a minimal web socket client (RFC 6455) -----------------------------------------


class WebSocket:
    """Just enough of a client for the Guacamole tunnel: text messages, ping, close."""

    def __init__(self, url: str, subprotocol: str, context: ssl.SSLContext, timeout: float) -> None:
        parts = urlsplit(url)
        port: int = parts.port or (443 if parts.scheme == "wss" else 80)
        raw: socket.socket = socket.create_connection((parts.hostname or "", port), timeout=timeout)
        self.sock: socket.socket = context.wrap_socket(raw, server_hostname=parts.hostname) if parts.scheme == "wss" else raw
        self.buffer: bytes = b""
        key: str = base64.b64encode(os.urandom(16)).decode()
        target: str = parts.path + ("?" + parts.query if parts.query else "")
        request: str = (
            f"GET {target} HTTP/1.1\r\nHost: {parts.netloc}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\nSec-WebSocket-Protocol: {subprotocol}\r\n"
            f"Origin: {parts.scheme.replace('ws', 'http')}://{parts.netloc}\r\n\r\n"
        )
        self.sock.sendall(request.encode())
        header: bytes = self.read_until(b"\r\n\r\n")
        status: str = header.split(b"\r\n", 1)[0].decode(errors="replace")
        if " 101 " not in status:
            raise OSError(f"the web socket upgrade was refused: {status}")
        accept: str = base64.b64encode(hashlib.sha1((key + WEBSOCKET_GUID).encode()).digest()).decode()
        if f"sec-websocket-accept: {accept}".lower() not in header.decode(errors="replace").lower():
            raise OSError("the server's Sec-WebSocket-Accept is wrong")

    def read_until(self, marker: bytes) -> bytes:
        while marker not in self.buffer:
            chunk: bytes = self.sock.recv(4096)
            if not chunk:
                raise OSError("the connection closed during the handshake")
            self.buffer += chunk
        head, _, self.buffer = self.buffer.partition(marker)
        return head

    def read_exactly(self, count: int) -> bytes:
        while len(self.buffer) < count:
            chunk: bytes = self.sock.recv(65536)
            if not chunk:
                raise OSError("the connection closed")
            self.buffer += chunk
        data, self.buffer = self.buffer[:count], self.buffer[count:]
        return data

    def send(self, opcode: int, payload: bytes) -> None:
        """A single masked frame (clients must mask)."""
        length: int = len(payload)
        head: bytes = bytes([0x80 | opcode])
        if length < 126:
            head += bytes([0x80 | length])
        elif length < 65536:
            head += bytes([0x80 | 126]) + struct.pack(">H", length)
        else:
            head += bytes([0x80 | 127]) + struct.pack(">Q", length)
        mask: bytes = os.urandom(4)
        masked: bytes = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        self.sock.sendall(head + mask + masked)

    def receive(self) -> tuple[int, bytes]:
        """The next frame as (opcode, payload); fragments are not used by the Guacamole server."""
        first, second = self.read_exactly(2)
        length: int = second & 0x7F
        if length == 126:
            length = struct.unpack(">H", self.read_exactly(2))[0]
        elif length == 127:
            length = struct.unpack(">Q", self.read_exactly(8))[0]
        mask: bytes = self.read_exactly(4) if second & 0x80 else b""
        payload: bytes = self.read_exactly(length)
        if mask:
            payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        return first & 0x0F, payload

    def close(self) -> None:
        try:
            self.send(0x8, struct.pack(">H", 1000))
        except OSError:
            pass
        self.sock.close()


# --- the session -------------------------------------------------------------------


def request_json(url: str, context: ssl.SSLContext, data: dict[str, str] | None = None, method: str | None = None) -> object:
    body: bytes | None = urllib.parse.urlencode(data).encode() if data is not None else None
    request = urllib.request.Request(url, data=body, method=method)
    with urllib.request.urlopen(request, context=context, timeout=30) as response:
        payload: bytes = response.read()
    return json.loads(payload) if payload else {}


def sign_in(base: str, vendor: Vendor, context: ssl.SSLContext) -> dict[str, object]:
    """POST /api/tokens with a fresh TOTP code. Guacamole accepts only the code of the current 30 s
    step, so a code made just before a step ends can arrive too late: try again with a new one."""
    last: Exception | None = None
    for _ in range(3):
        code: str = totp(vendor.VENDOR_TOTP_SECRET, time.time())
        try:
            token = request_json(
                f"{base}/api/tokens",
                context,
                {"username": vendor.VENDOR_GUAC_USER, "password": vendor.VENDOR_GUAC_PASSWORD, "guac-totp": code},
            )
            if isinstance(token, dict) and "authToken" in token:
                return token
        except urllib.error.HTTPError as error:
            last = error
            if error.code not in (400, 401, 403):
                raise
        time.sleep(2)
    raise PermissionError(f"Guacamole refused the sign in ({last})")


def find_connection(base: str, token: str, data_source: str, name: str, context: ssl.SSLContext) -> str:
    connections = request_json(f"{base}/api/session/data/{data_source}/connections?token={urllib.parse.quote(token)}", context)
    assert isinstance(connections, dict)
    for entry in connections.values():
        if isinstance(entry, dict) and entry.get("name") == name:
            return str(entry["identifier"])
    raise LookupError(f"the account has no connection named {name}")


#: Instructions of the remote screen. Seeing one means the remote end answered.
DRAWING: frozenset[str] = frozenset({"size", "rect", "img", "sync", "cursor"})


def hold_session(base: str, token: str, data_source: str, identifier: str, hold: float, context: ssl.SSLContext) -> str:
    """Open the tunnel and keep it for ``hold`` seconds. Returns the tunnel's id.

    The web application does the Guacamole handshake with guacd itself, so the client sees
    no ``ready``. The first message carries the tunnel's id (an instruction with an empty
    opcode), and the screen follows: drawing instructions mean guacd reached the remote
    desktop. An ``error`` instruction means it did not.
    """
    query: str = urllib.parse.urlencode(
        {
            "token": token,
            "GUAC_DATA_SOURCE": data_source,
            "GUAC_ID": identifier,
            "GUAC_TYPE": "c",
            "GUAC_WIDTH": "1280",
            "GUAC_HEIGHT": "800",
            "GUAC_DPI": "96",
            "GUAC_TIMEZONE": "UTC",
            "GUAC_IMAGE": "image/png",
            "GUAC_AUDIO": "audio/L16",
        }
    )
    ws_base: str = base.replace("https://", "wss://", 1).replace("http://", "ws://", 1)
    socket_: WebSocket = WebSocket(f"{ws_base}/websocket-tunnel?{query}", "guacamole", context, timeout=30)
    tunnel: str = ""
    drawn: bool = False
    deadline: float = time.monotonic() + hold
    next_mouse: float = 0.0
    try:
        socket_.sock.settimeout(5)
        while time.monotonic() < deadline:
            try:
                opcode, payload = socket_.receive()
            except TimeoutError:
                payload, opcode = b"", 1
            if opcode == 0x8:
                break
            if opcode == 0x9:
                socket_.send(0xA, payload)
            for instruction in decode_instructions(payload.decode(errors="replace")):
                if instruction[0] == "" and len(instruction) > 1:
                    tunnel = instruction[1]
                elif instruction[0] == "error":
                    raise ConnectionError(f"the tunnel reported an error: {instruction[1:]}")
                elif instruction[0] == "sync" and len(instruction) > 1:
                    socket_.send(0x1, encode_instruction("sync", instruction[1]).encode())
                if instruction[0] in DRAWING:
                    drawn = True
            if tunnel and time.monotonic() >= next_mouse:
                x: int = 100 + int(time.monotonic()) % 200
                socket_.send(0x1, encode_instruction("mouse", str(x), "120", "0").encode())
                next_mouse = time.monotonic() + 5
    finally:
        socket_.close()
    if not tunnel:
        raise ConnectionError("the web application never opened the tunnel")
    if not drawn:
        raise ConnectionError("the tunnel opened but the remote screen never drew anything")
    return tunnel


def run(url: str, hold: float, connection: str, env: Path, log: Path | None) -> int:
    session: Session = Session()
    context: ssl.SSLContext = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    session.record("tls-unverified", True, "the jump host's certificate is self-signed; range-only", log)
    try:
        vendor: Vendor = Vendor.from_file(env)
    except (OSError, ValueError) as error:
        session.record("credentials", False, f"cannot read {env}: {type(error).__name__}", log)
        return 1
    host: str = urlsplit(url).hostname or ""
    try:
        socket.create_connection((host, urlsplit(url).port or 443), timeout=10).close()
        session.record("tunnel", True, f"{host} answers on TCP 443", log)
    except OSError as error:
        session.record("tunnel", False, f"{host} does not answer on TCP 443: {error}", log)
        return 1
    token: str = ""
    try:
        auth = sign_in(url, vendor, context)
        token = str(auth["authToken"])
        data_source: str = str(auth.get("dataSource", "postgresql"))
        session.record("sign-in", True, f"signed in as {vendor.VENDOR_GUAC_USER} with TOTP", log)
        identifier: str = find_connection(url, token, data_source, connection, context)
        session.record("connection", True, f"{connection} is connection {identifier}", log)
        tunnel_id: str = hold_session(url, token, data_source, identifier, hold, context)
        session.record("session", True, f"{connection} was open for {hold:.0f} s (tunnel {tunnel_id})", log)
    except (OSError, ValueError, LookupError, PermissionError, ConnectionError, urllib.error.URLError) as error:
        session.record("session", False, f"{type(error).__name__}: {error}", log)
    finally:
        if token:
            try:
                request_json(f"{url}/api/tokens/{urllib.parse.quote(token)}", context, method="DELETE")
                session.record("sign-out", True, "token deleted", log)
            except (OSError, urllib.error.URLError) as error:
                session.record("sign-out", False, f"{type(error).__name__}: {error}", log)
    return 0 if session.passed else 1


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--hold", type=float, default=60.0, help="seconds to keep the session open")
    parser.add_argument("--connection", default="ews-rdp")
    parser.add_argument("--env", type=Path, default=DEFAULT_ENV)
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG)
    args: argparse.Namespace = parser.parse_args(argv)
    return run(args.url.rstrip("/"), args.hold, args.connection, args.env, args.log)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
