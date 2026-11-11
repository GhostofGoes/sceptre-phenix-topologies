"""cm_wg_watch (dmz-vpn) and the gateway's forward policy (DESIGN.md 13)."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import cm_wg_watch
import pytest
import yaml

from soc_paths import INJECTS, repo  # noqa: F401 (the fixtures are used by name)

DUMP: str = (
    "PRIVATE=\tSERVERPUB=\t51820\toff\n"
    "7bqjO1NUCGG0NcAVcv0DNXTB0gmriUGOy3ZpOd2j5T8=\t(none)\t198.51.100.10:51820\t10.99.0.2/32\t{handshake}\t100\t200\toff\n"
)
KEY: str = "7bqjO1NUCGG0NcAVcv0DNXTB0gmriUGOy3ZpOd2j5T8="


def peers(handshake: int, endpoint: str = "198.51.100.10:51820") -> list[cm_wg_watch.Peer]:
    text = DUMP.format(handshake=handshake).replace("198.51.100.10:51820", endpoint)
    return cm_wg_watch.parse_dump(text)


def test_parse_dump_reads_the_peer() -> None:
    (peer,) = peers(1_700_000_000)
    assert peer.public_key == KEY
    assert peer.endpoint == "198.51.100.10:51820"
    assert peer.latest_handshake == 1_700_000_000
    assert peer.ident == "7bqjO1NU"


def test_a_peer_that_never_connected_has_no_endpoint() -> None:
    (peer,) = cm_wg_watch.parse_dump("PRIVATE=\tPUB=\t51820\toff\n" + KEY + "\t(none)\t(none)\t10.99.0.2/32\t0\t0\t0\toff\n")
    assert peer.endpoint is None
    assert peer.latest_handshake == 0


def test_seeding_does_not_report_a_connected_peer() -> None:
    """A watcher restart must not turn a live session into a new peer_up."""
    watcher = cm_wg_watch.Watcher("wg0", down_after=200)
    now = 1_700_000_000.0
    watcher.seed(peers(int(now) - 3), now)
    assert watcher.step(peers(int(now) - 3), now + 5) == []
    assert [e.name for e in watcher.interface_gone()] == ["peer_down"]


def test_a_removed_peer_that_was_up_goes_down() -> None:
    watcher = cm_wg_watch.Watcher("wg0", down_after=200)
    now = 1_700_000_000.0
    assert [e.name for e in watcher.step(peers(int(now) - 3), now)] == ["peer_up"]
    gone = watcher.step([], now + 5)
    assert [e.name for e in gone] == ["peer_down"]
    assert gone[0].peer == "7bqjO1NU"
    assert watcher.step([], now + 10) == []


def test_the_event_sequence_of_a_vendor_session() -> None:
    watcher = cm_wg_watch.Watcher("wg0", down_after=200)
    now = 1_700_000_000.0
    assert watcher.step(peers(0), now) == []  # never connected: nothing to report
    up = watcher.step(peers(int(now) - 3), now)
    assert [e.name for e in up] == ["peer_up"]
    assert watcher.step(peers(int(now) - 3), now + 5) == []  # still the same session
    assert watcher.step(peers(int(now) + 100), now + 105) == []  # a rekey is not an event
    # The laptop moves to another address inside the session: no new handshake.
    roam = watcher.step(peers(int(now) + 100, "198.51.100.77:40000"), now + 200)
    assert [e.name for e in roam] == ["peer_roam"]
    assert watcher.step(peers(int(now) + 230, "198.51.100.77:40000"), now + 232) == []  # its next rekey
    assert roam[0].endpoint == "198.51.100.77:40000"
    down = watcher.step(peers(int(now) + 230, "198.51.100.77:40000"), now + 470)
    assert [e.name for e in down] == ["peer_down"]
    again = watcher.step(peers(int(now) + 600, "198.51.100.77:40000"), now + 602)
    assert [e.name for e in again] == ["peer_up"]


def test_a_client_restart_inside_a_live_session_is_a_new_session() -> None:
    watcher = cm_wg_watch.Watcher("wg0", down_after=200)
    now = 1_700_000_000.0
    assert [e.name for e in watcher.step(peers(int(now) - 3), now)] == ["peer_up"]
    # The vendor's laptop runs wg-quick down and up: a handshake 40 s after the last one.
    again = watcher.step(peers(int(now) + 37), now + 40)
    assert [e.name for e in again] == ["peer_up"]
    # A rekey of a live session comes 120 s later and is not an event.
    assert watcher.step(peers(int(now) + 160), now + 162) == []


def test_a_late_client_restart_shows_by_its_new_source_port() -> None:
    """`systemctl restart wg-quick@wg0` (the SCORCH vendor-tunnel step) 110 s after
    the last handshake looks like a rekey by its timing; the new source port tells."""
    watcher = cm_wg_watch.Watcher("wg0", down_after=200)
    now = 1_700_000_000.0
    assert [e.name for e in watcher.step(peers(int(now) - 3), now)] == ["peer_up"]
    again = watcher.step(peers(int(now) + 107, "198.51.100.10:43017"), now + 110)
    assert [e.name for e in again] == ["peer_up"]
    assert again[0].endpoint == "198.51.100.10:43017"


def test_the_interface_vanishing_takes_the_peers_down() -> None:
    watcher = cm_wg_watch.Watcher("wg0", down_after=200)
    now = 1_700_000_000.0
    watcher.step(peers(int(now) - 3), now)
    gone = watcher.interface_gone()
    assert [e.name for e in gone] == ["peer_down"]
    assert gone[0].peer == "7bqjO1NU"
    assert watcher.interface_gone() == []


def test_the_log_line_matches_the_wazuh_decoder() -> None:
    event = cm_wg_watch.Event(name="peer_up", interface="wg0", peer="7bqjO1NU", endpoint="198.51.100.10:51820")
    line = event.line("dmz-vpn", 1_700_000_000.0)
    pattern = r"^\w{3} [ \d]\d \d\d:\d\d:\d\d dmz-vpn cm-wg-watch: event=(\w+) interface=(\S+) peer=(\S+) endpoint=(\d+\.\d+\.\d+\.\d+):(\d+)$"
    match = re.match(pattern, line)
    assert match
    assert match.group(1) == "peer_up"
    decoder = (INJECTS / "wazuh/cherrymountain_decoders.xml.in").read_text()
    assert "event=(\\w+) interface=(\\S+) peer=(\\S+) endpoint=(\\d+\\.\\d+\\.\\d+\\.\\d+):(\\d+)" in decoder


@pytest.mark.skipif(shutil.which("nft") is None, reason="nft is not installed")
def test_the_forward_policy_parses() -> None:
    result = subprocess.run(
        ["nft", "-c", "-f", str(INJECTS / "wireguard/nftables-wireguard.nft")],
        capture_output=True,
        text=True,
        check=False,
    )
    if "Operation not permitted" in result.stderr:
        pytest.skip("nft cannot load rules here")
    assert result.returncode == 0, result.stderr


def test_the_forward_policy_pins_the_vendor_to_dmz_jump(repo: Path) -> None:
    policy = (INJECTS / "wireguard/nftables-wireguard.nft").read_text()
    wireguard = yaml.safe_load((repo / "plant/generated/full/phenix/wireguard.yaml").read_text())
    vendor = next(h for h in wireguard["hosts"] if h["hostname"] == "vendor-ws")
    server = next(h for h in wireguard["hosts"] if h["hostname"] == "dmz-vpn")
    tunnel_address = vendor["metadata"]["interface"]["address"].split("/")[0]
    (client_peer,) = vendor["metadata"]["peers"]
    jump = client_peer["allowed_ips"].split("/")[0]  # what the laptop's AllowedIPs admit: dmz-jump only
    assert f"ip saddr {tunnel_address} ip daddr {jump} tcp dport 443 accept" in policy
    assert server["metadata"]["peers"][0]["allowed_ips"] == f"{tunnel_address}/32"
    assert "policy drop" in policy


def test_once_prints_each_peer(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """--once is a debugging snapshot: one line per peer, nothing logged."""
    now = 1_700_000_000
    dump = "priv\tpub\t51820\toff\n" + "\t".join(["AAAAAAAAkey", "(none)", "198.51.100.10:51820", "10.99.0.2/32", str(now - 30), "1", "2", "25"]) + "\n" + "\t".join(["BBBBBBBBkey", "(none)", "(none)", "10.99.0.3/32", "0", "0", "0", "off"]) + "\n"
    monkeypatch.setattr(cm_wg_watch, "read_dump", lambda interface: dump)
    monkeypatch.setattr(cm_wg_watch.time, "time", lambda: float(now))
    assert cm_wg_watch.main(["--once"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out == [
        "peer=AAAAAAAA endpoint=198.51.100.10:51820 handshake_age=30s state=up",
        "peer=BBBBBBBB endpoint=none handshake_age=never state=down",
    ]


def test_the_event_log_is_rotated(repo: Path) -> None:
    text = (repo / "phenix-injects" / "wireguard" / "cm-wireguard.logrotate").read_text()
    assert "/var/log/cm-wireguard/events.log {" in text and "maxsize" in text
    # The watcher is a dynamic user: a file that logrotate creates as root, it cannot append to.
    unit = (repo / "phenix-injects" / "wireguard" / "cm-wg-watch.service").read_text()
    assert "DynamicUser=yes" in unit
    assert re.search(r"^\s*nocreate$", text, re.MULTILINE) and not re.search(r"^\s*create\b", text, re.MULTILINE)
    topology = (repo / "phenix-configs" / "topology-remote-access.yml").read_text()
    assert "dst: /etc/logrotate.d/cm-wireguard" in topology
