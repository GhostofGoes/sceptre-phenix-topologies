"""Run the synthetic traffic through Suricata and Zeek (DESIGN.md 14), where they are installed.

Skipped when scapy, Suricata, or Docker with Malcolm's Zeek image is missing, so the prek
hook skips it. Run it by hand on a machine that has them:

    pip install scapy
    apt-get install suricata        # the rules pass on 7.0.3, 8.0.2, and Malcolm 26.10.0's 8.0.7
    docker pull ghcr.io/idaholab/malcolm/zeek:26.10.0
    python3 -m pytest cherrymountain-brewery/tests/soc/test_detection_traffic.py
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from soc_paths import INJECTS

scapy = pytest.importorskip("scapy")
import detection_pcaps  # noqa: E402

ZEEK_IMAGE: str = "ghcr.io/idaholab/malcolm/zeek:26.10.0"
RULES: Path = INJECTS / "malcolm" / "cherrymountain.rules"
ZEEK_SCRIPTS: Path = INJECTS / "malcolm" / "zeek"


@pytest.mark.skipif(shutil.which("suricata") is None, reason="suricata is not installed")
def test_every_rule_fires_on_its_traffic(tmp_path: Path) -> None:
    pcap = tmp_path / "t.pcap"
    detection_pcaps.suricata_pcap(pcap)
    log = tmp_path / "log"
    log.mkdir()
    result = subprocess.run(
        [
            "suricata", "-r", str(pcap), "-c", "/etc/suricata/suricata.yaml", "-S", str(RULES), "-l", str(log), "-k", "none",
            "--set", "app-layer.protocols.modbus.enabled=yes", "--set", "app-layer.protocols.dnp3.enabled=yes",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    fired: set[int] = set()
    for line in (log / "eve.json").read_text().splitlines():
        event = json.loads(line)
        if event.get("event_type") == "alert":
            fired.add(int(event["alert"]["signature_id"]))
    assert detection_pcaps.EXPECTED_SIDS <= fired, f"silent rules: {sorted(detection_pcaps.EXPECTED_SIDS - fired)}"


def zeek_image_present() -> bool:
    if shutil.which("docker") is None:
        return False
    result = subprocess.run(["docker", "image", "inspect", ZEEK_IMAGE], capture_output=True, check=False)
    return result.returncode == 0


@pytest.mark.skipif(not zeek_image_present(), reason="Malcolm's Zeek image is not available")
def test_every_notice_fires_on_its_traffic(tmp_path: Path) -> None:
    detection_pcaps.zeek_pcap(tmp_path / "t.pcap")
    tmp_path.chmod(0o777)
    result = subprocess.run(
        [
            "docker", "run", "--rm", "--user", "root", "-v", f"{tmp_path}:/work", "-v",
            f"{ZEEK_SCRIPTS}:/usr/local/zeek/share/zeek/site/custom:ro", "-w", "/work",
            "--entrypoint", "/usr/local/zeek/bin/zeek-offline", ZEEK_IMAGE, "-C", "-r", "t.pcap", "local",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    notice = (tmp_path / "notice.log").read_text().splitlines()
    fields = next(line for line in notice if line.startswith("#fields")).split("\t")[1:]
    column = fields.index("note")
    raised = {line.split("\t")[column] for line in notice if not line.startswith("#")}
    assert detection_pcaps.EXPECTED_NOTICES <= raised, f"silent notices: {sorted(detection_pcaps.EXPECTED_NOTICES - raised)}"
