#!/usr/bin/env python3
"""The engineer's OpenPLC REST helper on the EWS (DESIGN.md 13, 14, and 15.6).

Installed on ews as /usr/local/lib/cm-ews/cm_plc_tool.py. The engineering
workstation is the one host that may talk to the PLCs' REST port (TCP 8443, over
TLS with a self-signed certificate; DESIGN.md 10.3). This tool does what an
engineer does there, and the SCORCH `vendor-maintenance` run uses it for the
benign program re-upload:

    cm_plc_tool.py upload --plc plc-cellar --host 10.20.10.12 --zip /opt/cherrymountain/ews/programs/plc-cellar.zip
    cm_plc_tool.py start|stop|status --plc plc-cellar --host 10.20.10.12

``upload`` signs in, uploads the program ZIP, waits for the runtime to compile it, and
starts the PLC; it passes only if the runtime reports STATUS:RUNNING. Every action
goes to syslog as ``cm-plc-tool: event=<action> plc=<name> host=<address>
user=<operator>`` (events plc_upload, plc_start, plc_stop, plc_failed), which the Wazuh
agent on the EWS ships (decoder cm-plc-tool, rules 100410 to 100413). The traffic
itself is encrypted, so this log is where a host-side detection of a program change
comes from; Malcolm sees the session and its size.

The password comes from /etc/default/cm-ews (OPENPLC_USER, OPENPLC_PASSWORD; the
cherrymountain app renders it with the PLCs' credential), never from the command
line. The certificate is not verified (self-signed, range-only). Standard library
plus pydantic.
"""

from __future__ import annotations

import argparse
import getpass
import json
import logging
import logging.handlers
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Annotated, ClassVar

from pydantic import BaseModel, ConfigDict, StringConstraints

DEFAULT_SETTINGS: Path = Path("/etc/default/cm-ews")
REST_PORT: int = 8443
COMPILE_TIMEOUT: float = 900.0

Name = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")]
Address = Annotated[str, StringConstraints(pattern=r"^\d{1,3}(\.\d{1,3}){3}$")]

logger: logging.Logger = logging.getLogger("cm-plc-tool")


class Settings(BaseModel):
    """OPENPLC_USER and OPENPLC_PASSWORD, from the settings file or the environment."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore", frozen=True)

    OPENPLC_USER: str = "admin"
    OPENPLC_PASSWORD: str

    @classmethod
    def load(cls, path: Path) -> Settings:
        values: dict[str, str] = {}
        if path.is_file():
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    key, _, value = line.partition("=")
                    values[key] = value.strip('"')
        for key in ("OPENPLC_USER", "OPENPLC_PASSWORD"):
            if key in os.environ:
                values[key] = os.environ[key]
        return cls.model_validate(values)


class Plc(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    name: Name
    host: Address
    port: int = REST_PORT

    @property
    def base(self) -> str:
        return f"https://{self.host}:{self.port}/api"


class Runtime:
    """One OpenPLC v4 runtime over its REST API (the endpoints cm-openplc-provision uses)."""

    def __init__(self, plc: Plc, settings: Settings) -> None:
        self.plc: Plc = plc
        self.settings: Settings = settings
        self.context: ssl.SSLContext = ssl.create_default_context()
        self.context.check_hostname = False
        self.context.verify_mode = ssl.CERT_NONE
        self.token: str = ""

    def call(self, path: str, data: bytes | None = None, headers: dict[str, str] | None = None) -> str:
        request = urllib.request.Request(f"{self.plc.base}/{path}", data=data, headers=headers or {})
        if self.token:
            request.add_header("Authorization", f"Bearer {self.token}")
        with urllib.request.urlopen(request, context=self.context, timeout=60) as response:
            return response.read().decode(errors="replace")

    def sign_in(self) -> None:
        body: bytes = json.dumps({"username": self.settings.OPENPLC_USER, "password": self.settings.OPENPLC_PASSWORD}).encode()
        reply: str = self.call("login", body, {"Content-Type": "application/json"})
        self.token = str(json.loads(reply).get("access_token", ""))
        if not self.token:
            raise PermissionError("the runtime refused the sign in")

    def upload(self, program: Path) -> None:
        boundary: str = uuid.uuid4().hex
        payload: bytes = (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{program.name}\"\r\n"
            "Content-Type: application/zip\r\n\r\n"
        ).encode() + program.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
        reply: str = self.call("upload-file?clean=0", payload, {"Content-Type": f"multipart/form-data; boundary={boundary}"})
        if '"UploadFileFail":""' not in reply:
            raise RuntimeError(f"the runtime rejected the upload: {reply[:200]}")

    def wait_compiled(self, timeout: float) -> None:
        deadline: float = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status: str = str(json.loads(self.call("compilation-status")).get("status", ""))
            if status == "SUCCESS":
                return
            if status == "FAILED":
                raise RuntimeError("the runtime failed to compile the program")
            time.sleep(5)
        raise TimeoutError(f"the program did not compile within {timeout:.0f} s")

    def status(self) -> str:
        return self.call("status").strip()

    def wait_running(self, attempts: int = 12) -> None:
        for _ in range(attempts):
            time.sleep(5)
            if "STATUS:RUNNING" in self.status():
                return
        raise RuntimeError("the PLC is not running after the start")


def log_event(event: str, plc: Plc, operator: str) -> None:
    logger.info("event=%s plc=%s host=%s user=%s", event, plc.name, plc.host, operator)


def run(action: str, plc: Plc, settings: Settings, program: Path | None, operator: str, timeout: float) -> int:
    runtime: Runtime = Runtime(plc, settings)
    event: str = {"upload": "plc_upload", "start": "plc_start", "stop": "plc_stop", "status": "plc_status"}[action]
    try:
        runtime.sign_in()
        if action == "upload":
            if program is None or not program.is_file():
                raise FileNotFoundError("upload needs --zip with an existing program ZIP")
            log_event(event, plc, operator)
            runtime.upload(program)
            runtime.wait_compiled(timeout)
            runtime.call("start-plc")
            runtime.wait_running()
            log_event("plc_start", plc, operator)
        elif action == "start":
            log_event(event, plc, operator)
            runtime.call("start-plc")
            runtime.wait_running()
        elif action == "stop":
            log_event(event, plc, operator)
            runtime.call("stop-plc")
        print(runtime.status())
    except (OSError, ValueError, RuntimeError, TimeoutError, PermissionError, urllib.error.URLError) as error:
        log_event("plc_failed", plc, operator)
        print(f"cm-plc-tool: {action} on {plc.name} failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("action", choices=("upload", "start", "stop", "status"))
    parser.add_argument("--plc", required=True, help="the PLC's host name, for the log")
    parser.add_argument("--host", required=True, help="the PLC's address on CTRL")
    parser.add_argument("--zip", type=Path, default=None, dest="program", help="the program ZIP (upload)")
    parser.add_argument("--settings", type=Path, default=DEFAULT_SETTINGS)
    parser.add_argument("--operator", default=os.environ.get("SUDO_USER") or getpass.getuser())
    parser.add_argument("--compile-timeout", type=float, default=COMPILE_TIMEOUT)
    args: argparse.Namespace = parser.parse_args(argv)

    logger.setLevel(logging.INFO)
    try:
        handler = logging.handlers.SysLogHandler(address="/dev/log")
        handler.setFormatter(logging.Formatter("cm-plc-tool: %(message)s"))
        logger.addHandler(handler)
    except OSError:
        pass  # no syslog socket (a test box): the console still shows the outcome
    logger.addHandler(logging.StreamHandler(sys.stdout))
    plc: Plc = Plc(name=args.plc, host=args.host)
    try:
        settings: Settings = Settings.load(args.settings)
    except ValueError:
        print(f"cm-plc-tool: no OPENPLC_PASSWORD in {args.settings} or the environment", file=sys.stderr)
        return 2
    return run(args.action, plc, settings, args.program, args.operator, args.compile_timeout)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
