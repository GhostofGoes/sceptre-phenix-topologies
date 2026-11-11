#!/usr/bin/env python3
"""The engineer's OpenPLC REST helper on the EWS (DESIGN.md 13, 14, and 15.6).

Installed on ews as /usr/local/lib/cm-ews/cm_plc_tool.py. The engineering
workstation is the one host that may talk to the PLCs' REST port (TCP 8443, over
TLS with a self-signed certificate; DESIGN.md 10.3). This tool does what an
engineer does there, and the SCORCH `vendor-maintenance` run uses it for the
benign program re-upload:

    cm_plc_tool.py upload --plc plc-cellar --host 10.20.10.12 --zip /opt/cherrymountain/ews/programs/plc-cellar.zip
    cm_plc_tool.py start|stop|status --plc plc-cellar --host 10.20.10.12 [--json]

``upload`` signs in, uploads the program ZIP, waits for the runtime to compile it, and
starts the PLC; it passes only if the runtime reports STATUS:RUNNING. Transient REST
errors while polling (an error page, a dropped connection) are retried a few times.
Every action goes to syslog as ``cm-plc-tool: event=<action> plc=<name> host=<address>
user=<account>`` (events plc_upload, plc_start, plc_stop, plc_failed), which the Wazuh
agent on the EWS ships (decoder cm-plc-tool, rules 100410 to 100413). ``user`` is the
account that ran the tool (SUDO_USER, else the login), which a caller cannot choose;
``--operator`` only adds an ``operator=`` field after it, and ``plc_failed`` adds a
``reason=``. The traffic
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
import http.client
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
#: Consecutive failed polls tolerated before a wait gives up.
POLL_FAILURES: int = 6
#: Errors a REST call can raise when the runtime is busy or restarting.
TRANSIENT: tuple[type[BaseException], ...] = (OSError, ValueError, http.client.HTTPException)

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

    #: Seconds between compilation-status polls.
    POLL_S: float = 5.0
    #: Right after an upload, compilation-status may still report the previous
    #: build's SUCCESS or FAILED (unverified on v4.3.2). A final state counts only
    #: once the runtime has shown a busy state for this upload, or after this
    #: grace, which is longer than any compile can be.
    STALE_GRACE_S: float = 60.0

    def wait_compiled(self, timeout: float) -> None:
        start: float = time.monotonic()
        deadline: float = start + timeout
        failures: int = 0
        seen_busy: bool = False
        while time.monotonic() < deadline:
            try:
                reply: object = json.loads(self.call("compilation-status"))
                status: str = str(reply.get("status", "")) if isinstance(reply, dict) else ""
                failures = 0
            except TRANSIENT:
                failures += 1
                if failures > POLL_FAILURES:
                    raise
                status = ""
            final: bool = status in ("SUCCESS", "FAILED")
            if status and not final:
                seen_busy = True
            if final and (seen_busy or time.monotonic() - start >= self.STALE_GRACE_S):
                if status == "FAILED":
                    raise RuntimeError("the runtime failed to compile the program")
                return
            time.sleep(self.POLL_S)
        raise TimeoutError(f"the program did not compile within {timeout:.0f} s")

    def status(self) -> str:
        return self.call("status").strip()

    def wait_running(self, attempts: int = 12) -> None:
        for _ in range(attempts):
            time.sleep(5)
            try:
                if "STATUS:RUNNING" in self.status():
                    return
            except TRANSIENT:
                continue  # busy right after start-plc; the next poll decides
        raise RuntimeError("the PLC is not running after the start")


def account() -> str:
    """The account that ran the tool: the sudo caller, else the login."""
    return os.environ.get("SUDO_USER") or getpass.getuser()


def log_event(event: str, plc: Plc, operator: str, reason: str = "") -> None:
    user: str = account()
    extra: str = f" operator={operator}" if operator and operator != user else ""
    if reason:
        extra += " reason=" + reason.replace(" ", "_")[:120]
    logger.info("event=%s plc=%s host=%s user=%s%s", event, plc.name, plc.host, user, extra)


def report(as_json: bool, action: str, plc: Plc, status: str, error: str = "") -> None:
    """The outcome on stdout: the runtime's status line, or one JSON object."""
    if as_json:
        print(json.dumps({"action": action, "plc": plc.name, "host": plc.host, "ok": not error, "status": status, "error": error}))
    elif not error:
        print(status)


def run(
    action: str, plc: Plc, settings: Settings, program: Path | None, operator: str, timeout: float, as_json: bool = False
) -> int:
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
        report(as_json, action, plc, runtime.status())
    except (
        OSError,
        ValueError,
        RuntimeError,
        TimeoutError,
        PermissionError,
        urllib.error.URLError,
        http.client.HTTPException,
    ) as error:
        log_event("plc_failed", plc, operator, type(error).__name__)
        print(f"cm-plc-tool: {action} on {plc.name} failed: {type(error).__name__}: {error}", file=sys.stderr)
        report(as_json, action, plc, "", type(error).__name__)
        return 1
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("action", choices=("upload", "start", "stop", "status"))
    parser.add_argument("--plc", required=True, help="the PLC's host name, for the log")
    parser.add_argument("--host", required=True, help="the PLC's address on CTRL")
    parser.add_argument("--zip", type=Path, default=None, dest="program", help="the program ZIP (upload)")
    parser.add_argument("--settings", type=Path, default=DEFAULT_SETTINGS)
    parser.add_argument("--operator", default="", help="who the action is for (logged as operator=)")
    parser.add_argument("--compile-timeout", type=float, default=COMPILE_TIMEOUT)
    parser.add_argument("--json", action="store_true", help="print the outcome as one JSON object")
    args: argparse.Namespace = parser.parse_args(argv)

    logger.setLevel(logging.INFO)
    try:
        handler = logging.handlers.SysLogHandler(address="/dev/log")
        handler.setFormatter(logging.Formatter("cm-plc-tool: %(message)s"))
        logger.addHandler(handler)
    except OSError:
        pass  # no syslog socket (a test box): the console still shows the outcome
    # With --json, stdout carries only the JSON object; the log goes to stderr.
    logger.addHandler(logging.StreamHandler(sys.stderr if args.json else sys.stdout))
    plc: Plc = Plc(name=args.plc, host=args.host)
    try:
        settings: Settings = Settings.load(args.settings)
    except ValueError:
        print(f"cm-plc-tool: no OPENPLC_PASSWORD in {args.settings} or the environment", file=sys.stderr)
        return 2
    except OSError as error:
        print(f"cm-plc-tool: cannot read {args.settings}: {error.strerror}", file=sys.stderr)
        return 2
    return run(args.action, plc, settings, args.program, args.operator, args.compile_timeout, args.json)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
