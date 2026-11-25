#!/usr/bin/env python3
"""A real FUXA (frangoteam/fuxa in Docker) on the OPS subnet of a BBMD lab.

FUXA 1.3.5 and its BACnet driver (node-bacnet 0.2.4) run unchanged in a
container on the runner. The container starts on Docker's default network (npm
needs it to install the driver), is provisioned through its API (sign-in,
password change, BACnet plugin, project with one BACnet device), and is then
re-homed: a veth pair puts it on the OPS subnet of the lab, whatever carries
that subnet (a Linux bridge in a namespace, or an OVS VLAN of minimega), and
Docker's own interface is removed so the only path out is through the lab's
router.

Everything here runs on the host and reaches the container's network namespace
with `nsenter`, so FUXA needs no published port.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel

HERE: Path = Path(__file__).resolve().parent
FUXA_DIR: Path = HERE.parent / "fuxa"

IMAGE: str = "frangoteam/fuxa:1.3.5"
NAME: str = "cm-fuxa"
TAG_ID: str = "t_bac_co2"
HOST_END: str = "fxh0"
CONTAINER_END: str = "fxc0"
API: str = "http://127.0.0.1:1881"
SETTINGS_IN_IMAGE: str = "/usr/src/app/FUXA/server/_appdata/mysettings.json"


class TagReading(BaseModel):
    """What GET /api/getTagValue returned for the BACnet tag."""

    value: object | None = None
    raw: str = ""

    @property
    def has_value(self) -> bool:
        return isinstance(self.value, (int, float)) and not isinstance(self.value, bool)


def sh(argv: list[str], timeout_s: float = 120, check: bool = True, env: dict[str, str] | None = None) -> str:
    try:
        proc: subprocess.CompletedProcess[str] = subprocess.run(  # noqa: S603
            argv, capture_output=True, text=True, timeout=timeout_s, check=False, env=env
        )
    except FileNotFoundError:
        if check:
            raise
        return f"{argv[0]}: not installed"
    out: str = proc.stdout + proc.stderr
    if check and proc.returncode != 0:
        raise RuntimeError(f"{' '.join(argv)} exited {proc.returncode}: {out[-800:]}")
    return out


class FuxaProbe:
    """One FUXA container and the helpers to provision, re-home and read it."""

    def __init__(self, evidence: Path, ip: str) -> None:
        self.evidence: Path = evidence
        self.ip: str = ip
        self.pid: int = 0
        self.capture: subprocess.Popen[bytes] | None = None
        self.evidence.mkdir(parents=True, exist_ok=True)

    # -- plumbing ------------------------------------------------------------

    def ns(self, argv: list[str], timeout_s: float = 120, check: bool = True, env: dict[str, str] | None = None) -> str:
        """Run a host command inside the container's network namespace."""
        return sh(["nsenter", "-t", str(self.pid), "-n", "--", *argv], timeout_s, check, env)

    def start(self) -> None:
        """Start FUXA on the default Docker network and provision it (sign-in, driver)."""
        sh(["docker", "rm", "-f", NAME], check=False)
        extra: list[str] = shlex.split(os.environ.get("CM_FUXA_DOCKER_ARGS", ""))  # for example a proxy CA in a sandbox
        sh(["docker", "pull", "-q", IMAGE], timeout_s=600)
        sh(["docker", "run", "-d", "--name", NAME, "-v", f"{FUXA_DIR / 'mysettings.json'}:{SETTINGS_IN_IMAGE}:ro", *extra, IMAGE])
        self.pid = int(sh(["docker", "inspect", "-f", "{{.State.Pid}}", NAME]).strip())
        deadline: float = time.monotonic() + 90
        while time.monotonic() < deadline:
            if "version" in self.ns(["curl", "-sS", "-m", "5", f"{API}/api/settings"], check=False):
                break
            time.sleep(2)
        else:
            raise TimeoutError("FUXA did not answer on :1881")
        token_out: str = "/tmp/cm-fuxa-token"
        self.save("secure-flow.txt", self.ns(["bash", str(FUXA_DIR / "secure_flow.sh")], env={**os.environ, "FUXA": API, "TOKEN_OUT": token_out}))
        token: str = Path(token_out).read_text().strip()
        self.save(
            "install-plugins.txt",
            self.ns(["bash", str(FUXA_DIR / "install_plugins.sh")], timeout_s=900, env={**os.environ, "FUXA": API, "TOKEN": token, "ONLY": "node-bacnet"}),
        )

    def plumb(self, gateway: str, routes: list[str], attach_host_end: Callable[[str], None]) -> None:
        """Give the container its OPS address on a veth pair and drop Docker's interface.

        attach_host_end wires the host's end of the pair to the lab's OPS subnet.
        """
        sh(["ip", "link", "del", HOST_END], check=False)
        sh(["ip", "link", "add", HOST_END, "type", "veth", "peer", "name", CONTAINER_END])
        sh(["ip", "link", "set", CONTAINER_END, "netns", str(self.pid)])
        attach_host_end(HOST_END)
        for cmd in (
            f"ip link set {CONTAINER_END} name eth1",
            f"ip addr add {self.ip}/24 dev eth1",
            "ip link set eth1 up",
            *(f"ip route add {net} via {gateway}" for net in routes),
        ):
            self.ns(cmd.split())
        sh(["docker", "network", "disconnect", "bridge", NAME], check=False)
        self.save("container-network.txt", self.ns(["ip", "-br", "addr"]) + self.ns(["ip", "route"]))

    # -- project -------------------------------------------------------------

    def post_project(self, python: str, broadcast_address: str = "none") -> str:
        """POST a project with the BACnet device (FUXA restarts its runtime and connects)."""
        project: Path = Path("/tmp/cm-fuxa-project.json")
        sh([python, str(FUXA_DIR / "make_project.py"), str(project), "--bacnet", "10.20.60.13", "--local-ip", self.ip,
            "--bacnet-only", "--broadcast-address", broadcast_address])  # fmt: skip
        out: str = self.ns(["bash", str(FUXA_DIR / "secure_flow.sh")], env={**os.environ, "FUXA": API, "PROJECT": str(project)})
        return out[-600:]

    def tag_reading(self) -> TagReading:
        token: str = Path("/tmp/cm-fuxa-token").read_text().strip()
        raw: str = self.ns(
            ["curl", "-sS", "-m", "8", "-H", f"x-access-token: {token}", "-G", f"{API}/api/getTagValue", "--data-urlencode", f"ids=[\"{TAG_ID}\"]"],
            check=False,
        ).strip()
        try:
            data: object = json.loads(raw)
        except json.JSONDecodeError:
            return TagReading(raw=raw[:300])
        rows: list[dict] = data if isinstance(data, list) else [data] if isinstance(data, dict) else []
        for row in rows:
            if isinstance(row, dict) and row.get("id") == TAG_ID:
                return TagReading(value=row.get("value"), raw=raw[:300])
        return TagReading(raw=raw[:300])

    def wait_value(self, timeout_s: float) -> TagReading:
        """Poll until the tag has a numeric value, or timeout_s passed; return the last reading."""
        deadline: float = time.monotonic() + timeout_s
        reading: TagReading = self.tag_reading()
        while not reading.has_value and time.monotonic() < deadline:
            time.sleep(3)
            reading = self.tag_reading()
        return reading

    # -- evidence ------------------------------------------------------------

    def start_capture(self, name: str) -> None:
        self.stop_capture()
        self.capture = subprocess.Popen(  # noqa: S603
            ["nsenter", "-t", str(self.pid), "-n", "--", "tcpdump", "-ni", "eth1", "-l", "udp port 47808"],  # noqa: S607
            stdout=(self.evidence / name).open("wb"), stderr=subprocess.STDOUT,
        )  # fmt: skip
        time.sleep(1)

    def stop_capture(self) -> None:
        if self.capture is not None:
            self.capture.terminate()
            self.capture.wait(timeout=10)
            self.capture = None

    def logs(self) -> str:
        return sh(["docker", "logs", "--tail", "200", NAME], check=False)

    def save(self, name: str, content: str) -> Path:
        path: Path = self.evidence / name
        path.write_text(content)
        return path

    def remove(self) -> None:
        self.stop_capture()
        sh(["docker", "rm", "-f", NAME], check=False)
        sh(["ovs-vsctl", "--if-exists", "del-port", HOST_END], check=False)
        sh(["ip", "link", "del", HOST_END], check=False)
