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


SURICATA_INCLUDE: Path = INJECTS / "malcolm" / "cm-suricata.yaml"


def suricata_alerts(pcap: Path, work: Path, include: Path | None = None) -> set[int]:
    """The sids Suricata raises on ``pcap`` with the range's rules; ``include`` is added
    to the stock configuration the way Malcolm adds it (an include at the end)."""
    work.mkdir()
    config = Path("/etc/suricata/suricata.yaml")
    if include is not None:
        config = work / "suricata.yaml"
        config.write_text(Path("/etc/suricata/suricata.yaml").read_text() + f"\ninclude:\n  - {include}\n")
    result = subprocess.run(
        [
            "suricata", "-r", str(pcap), "-c", str(config), "-S", str(RULES), "-l", str(work), "-k", "none",
            "--set", "app-layer.protocols.modbus.enabled=yes", "--set", "app-layer.protocols.dnp3.enabled=yes",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    fired: set[int] = set()
    for line in (work / "eve.json").read_text().splitlines():
        event = json.loads(line)
        if event.get("event_type") == "alert":
            fired.add(int(event["alert"]["signature_id"]))
    return fired


@pytest.mark.skipif(shutil.which("suricata") is None, reason="suricata is not installed")
def test_every_rule_fires_on_its_traffic(tmp_path: Path) -> None:
    pcap = tmp_path / "t.pcap"
    detection_pcaps.suricata_pcap(pcap)
    fired = suricata_alerts(pcap, tmp_path / "log")
    assert detection_pcaps.EXPECTED_SIDS <= fired, f"silent rules: {sorted(detection_pcaps.EXPECTED_SIDS - fired)}"


@pytest.mark.skipif(shutil.which("suricata") is None, reason="suricata is not installed")
def test_every_rule_fires_on_mirrored_traffic(tmp_path: Path) -> None:
    """The same traffic as the mirror delivers it: one VLAN tag per direction of a
    routed flow, and one direction only of a flow whose other end is on no mirrored
    VLAN. Suricata's defaults (the VLAN in the flow key, both directions needed for an
    established session) lose every rule with flow:established or an application-layer
    keyword on such a flow; cm-suricata.yaml is what makes them fire."""
    plain = tmp_path / "t.pcap"
    detection_pcaps.suricata_pcap(plain)
    pcap = tmp_path / "mirrored.pcap"
    kept, dropped = detection_pcaps.mirrored(plain, pcap)
    assert kept > 0 and dropped > 0, "the pcap has no flow with an end outside the mirrored VLANs"
    fired = suricata_alerts(pcap, tmp_path / "with", include=SURICATA_INCLUDE)
    assert detection_pcaps.EXPECTED_SIDS <= fired, f"silent rules: {sorted(detection_pcaps.EXPECTED_SIDS - fired)}"
    # Without the include the test would not notice its loss: the defaults must miss some.
    default = suricata_alerts(pcap, tmp_path / "without")
    assert detection_pcaps.EXPECTED_SIDS - default, "Suricata's defaults fire every rule: the pcap is not mirror-shaped"


@pytest.mark.skipif(shutil.which("suricata") is None, reason="suricata is not installed")
def test_the_include_keeps_the_rest_of_the_stream_section(tmp_path: Path) -> None:
    """A nested `stream:` in an included file replaces the section Malcolm wrote (its
    memory caps and checksum setting); the dotted keys of cm-suricata.yaml do not."""
    config = tmp_path / "suricata.yaml"
    config.write_text(Path("/etc/suricata/suricata.yaml").read_text() + f"\ninclude:\n  - {SURICATA_INCLUDE}\n")
    dump = subprocess.run(["suricata", "--dump-config", "-c", str(config)], capture_output=True, text=True, check=True).stdout
    values = dict(line.split(" = ", 1) for line in dump.splitlines() if " = " in line)
    assert values["vlan.use-for-tracking"] == "false"
    assert values["stream.async-oneside"] == "true"
    assert "stream.memcap" in values and "stream.reassembly.depth" in values


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
    # The range's own BACnet discovery (Who-Is to the limited broadcast) is not a notice.
    assert not [line for line in notice if "255.255.255.255" in line], "a notice on the range's normal BACnet discovery"
