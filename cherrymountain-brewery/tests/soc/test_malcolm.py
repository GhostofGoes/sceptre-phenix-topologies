"""The Malcolm sensor scripts against stand-ins (DESIGN.md 14, 15.4).

cm-malcolm-health runs with a stand-in ``docker`` and a fake sysfs tree; the
sid guard of cm-malcolm-provision runs on its own, cut out of the script.
Neither proves Malcolm's behavior; a VM run does.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

from soc_paths import INJECTS

MALCOLM: Path = INJECTS / "malcolm"

#: docker ps/exec: the container list from $FAKE_PS, the Zeek count from $FAKE_COUNT.
DOCKER: str = """#!/bin/sh
case "$1" in
  ps) cat "$FAKE_PS" ;;
  exec) printf '{"count": %s}\\n' "$FAKE_COUNT" ;;
  *) exit 1 ;;
esac
"""


class Sensor:
    """A stand-in malcolm VM: profile, containers, and the capture port."""

    def __init__(self, tmp: Path) -> None:
        self.tmp: Path = tmp
        (tmp / "state").mkdir()
        (tmp / "state" / "profile").write_text("slim\n")
        (tmp / "src").mkdir()
        (tmp / "src" / "profiles.sh").write_text((MALCOLM / "profiles.sh").read_text())
        bin_dir: Path = tmp / "bin"
        bin_dir.mkdir()
        (bin_dir / "docker").write_text(DOCKER)
        (bin_dir / "docker").chmod(0o755)
        self.iface: Path = tmp / "net" / "phenix"
        (self.iface / "statistics").mkdir(parents=True)
        self.env: dict[str, str] = {
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "CM_MALCOLM_STATE": str(tmp / "state"),
            "CM_MALCOLM_SRC": str(tmp / "src"),
            "CM_SYSFS_NET": str(tmp / "net"),
            "CM_CAPTURE_SAMPLE": "1",
            "FAKE_PS": str(tmp / "ps.txt"),
            "FAKE_COUNT": "42",
        }

    def services(self) -> list[str]:
        out = subprocess.run(
            ["bash", "-c", f'. "{MALCOLM / "profiles.sh"}"; cm_malcolm_services slim'],
            capture_output=True, text=True, check=True,
        )
        return ["opensearch", *out.stdout.split()]

    def containers(self, status: dict[str, str] | None = None, skip: str = "") -> None:
        lines = [
            f"malcolm-{s}-1|{(status or {}).get(s, 'Up 2 hours (healthy)')}"
            for s in self.services()
            if s != skip
        ]
        (self.tmp / "ps.txt").write_text("\n".join(lines) + "\n")

    def port(self, flags: int, rx: int, grow: bool = True) -> None:
        (self.iface / "flags").write_text(f"{flags:#x}\n")
        (self.iface / "statistics" / "rx_packets").write_text(f"{rx}\n")
        if grow:
            # A background writer bumps the counter during the sample.
            subprocess.Popen(
                ["bash", "-c", f"sleep 0.3; echo {rx + 500} > {self.iface / 'statistics' / 'rx_packets'}"]
            )

    def run(self, **env: str) -> tuple[int, str]:
        proc = subprocess.run(
            ["bash", str(MALCOLM / "cm-malcolm-health")],
            env={**self.env, **env}, capture_output=True, text=True, timeout=60, check=False,
        )
        return proc.returncode, proc.stdout.strip()


@pytest.fixture
def sensor(tmp_path: Path) -> Sensor:
    s = Sensor(tmp_path)
    s.containers()
    s.port(0x1103, 1000, grow=False)
    return s


def test_healthy(sensor: Sensor) -> None:
    sensor.port(0x1103, 1000)
    rc, out = sensor.run()
    assert rc == 0, out
    assert out.startswith("HEALTHY: profile slim") and "42 Zeek records" in out


def test_a_missing_container_is_unhealthy(sensor: Sensor) -> None:
    sensor.containers(skip="zeek-live")
    rc, out = sensor.run()
    assert (rc, out) == (1, "UNHEALTHY: container malcolm-zeek-live-1 is not running")


def test_an_unhealthy_container_is_reported(sensor: Sensor) -> None:
    sensor.containers(status={"opensearch": "Up 5 minutes (unhealthy)"})
    rc, out = sensor.run()
    assert (rc, out) == (1, "UNHEALTHY: container malcolm-opensearch-1 is unhealthy")


def test_the_capture_port_must_be_promiscuous(sensor: Sensor) -> None:
    sensor.port(0x1003, 1000)
    rc, out = sensor.run()
    assert (rc, out) == (1, "UNHEALTHY: capture port phenix is not in promiscuous mode")


def test_the_capture_port_must_receive(sensor: Sensor) -> None:
    sensor.port(0x1103, 1000, grow=False)
    rc, out = sensor.run()
    assert rc == 1 and "received no packets" in out


def test_no_zeek_records_is_unhealthy(sensor: Sensor) -> None:
    sensor.port(0x1103, 1000)
    rc, out = sensor.run(FAKE_COUNT="0")
    assert rc == 1 and "no Zeek records" in out


def sid_guard() -> str:
    """The sid guard of cm-malcolm-provision, cut out of the script."""
    text = (MALCOLM / "cm-malcolm-provision").read_text()
    m = re.search(r"^# Fix 4:.*?^fi\n(?:.*?^fi\n)?", text, re.MULTILINE | re.DOTALL)
    assert m is not None
    return m.group(0)


@pytest.mark.parametrize(
    ("rules", "rc", "message"),
    [
        ('alert ip any any -> any any (msg:"a"; sid:9000101; rev:1;)\n', 0, ""),
        ('alert ip any any -> any any (msg:"a"; sid:1000001; rev:1;)\n', 1, "below 9000101: 1000001"),
        ("# no rules here\n", 1, "holds no sid"),
    ],
)
def test_sid_guard(tmp_path: Path, rules: str, rc: int, message: str) -> None:
    (tmp_path / "suricata" / "rules").mkdir(parents=True)
    (tmp_path / "suricata" / "rules" / "cherrymountain.rules").write_text(rules)
    # Another rules file with low sids is not ours and is not checked.
    (tmp_path / "suricata" / "rules" / "other.rules").write_text("alert ip any any -> any any (sid:5; rev:1;)\n")
    script = 'log() { echo "$*"; }\n' + sid_guard() + 'echo checked\n'
    # With the options of cm-malcolm-provision: under errexit and pipefail a grep that
    # finds no sid would end the script before it says why.
    proc = subprocess.run(["bash", "-euo", "pipefail", "-c", script], cwd=tmp_path, capture_output=True, text=True, check=False)
    assert proc.returncode == rc, proc.stdout
    assert message in proc.stdout
    if rc == 0:
        assert proc.stdout.strip() == "checked"


def test_the_shipped_rules_pass_the_sid_guard(tmp_path: Path) -> None:
    (tmp_path / "suricata" / "rules").mkdir(parents=True)
    (tmp_path / "suricata" / "rules" / "cherrymountain.rules").write_text((MALCOLM / "cherrymountain.rules").read_text())
    script = 'log() { echo "$*"; }\n' + sid_guard() + 'echo checked\n'
    proc = subprocess.run(["bash", "-c", script], cwd=tmp_path, capture_output=True, text=True, check=False)
    assert (proc.returncode, proc.stdout.strip()) == (0, "checked")
