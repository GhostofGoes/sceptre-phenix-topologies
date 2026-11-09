"""phenix-injects/vrouter/cm-vyos-config.sh against a stand-in VyOS shell.

The stand-in template records every configuration command, and stand-in
``cli-shell-api`` and ``hostname`` commands answer from a small config tree,
so the script runs unchanged (DESIGN.md 10.3). This checks the commands it
issues per router, the run-once marker, the dry run, and the failure path. It
does not prove VyOS accepts them; that needs a VM.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

SCRIPT: Path = Path(__file__).resolve().parents[3] / "phenix-injects" / "vrouter" / "cm-vyos-config.sh"

#: The VyOS configuration functions the script calls; each records its call.
TEMPLATE: str = """
record() { printf '%s\\n' "$*" >> "$FAKE_LOG"; }
configure() { record configure; }
set() {
  record "set $*"
  case " $* " in *" $FAKE_REJECT "*) return 1 ;; esac
  return 0
}
delete() { record "delete $*"; }
commit() { record commit; return "${FAKE_COMMIT_RC:-0}"; }
save() { record save; }
compare() { record compare; }
discard() { record discard; }
"""

#: cli-shell-api: listEffectiveNodes and returnEffectiveValue from $FAKE_TREE.
CLI_SHELL_API: str = """#!/usr/bin/env python3
import json, os, sys
tree = json.load(open(os.environ["FAKE_TREE"]))
op, *path = sys.argv[1:]
node = tree
for part in path:
    node = node.get(part, {}) if isinstance(node, dict) else {}
if op == "listEffectiveNodes":
    print(" ".join(f"'{k}'" for k in node) if isinstance(node, dict) else "")
elif op == "returnEffectiveValue":
    print(node if isinstance(node, str) else "")
"""

FW_EDGE_TREE: dict = {
    "firewall": {
        "ipv4": {
            "name": {
                "FROM-INET": {
                    "rule": {
                        "10": {"destination": {"address": "10.10.20.10"}},
                        "20": {"source": {"address": "192.0.2.10"}},
                    }
                },
                "FROM-CORP": {"rule": {"10": {"source": {"address": "10.10.10.0/24"}}}},
            }
        }
    },
    "service": {"ntp": {"server": {"203.0.113.10": {}}}},
}


class Router:
    """A stand-in router: runs the script and returns the recorded commands."""

    def __init__(self, tmp: Path, host: str, tree: dict) -> None:
        self.tmp: Path = tmp
        self.log: Path = tmp / "commands.log"
        bin_dir: Path = tmp / "bin"
        bin_dir.mkdir(exist_ok=True)
        (tmp / "template.sh").write_text(TEMPLATE)
        (tmp / "tree.json").write_text(json.dumps(tree))
        api: Path = bin_dir / "cli-shell-api"
        api.write_text(CLI_SHELL_API)
        api.chmod(0o755)
        hostname: Path = bin_dir / "hostname"
        hostname.write_text(f"#!/bin/sh\necho {host}\n")
        hostname.chmod(0o755)
        self.env: dict[str, str] = {
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "CM_VYOS_TEMPLATE": str(tmp / "template.sh"),
            "CM_VYOS_RUN_DIR": str(tmp),
            "FAKE_LOG": str(self.log),
            "FAKE_TREE": str(tmp / "tree.json"),
            "FAKE_REJECT": "__nothing__",
        }

    def run(self, *args: str, **env: str) -> tuple[int, list[str]]:
        self.log.unlink(missing_ok=True)
        proc = subprocess.run(
            ["bash", str(SCRIPT), *args],
            env={**self.env, **env},
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        lines = self.log.read_text().splitlines() if self.log.exists() else []
        return proc.returncode, lines


def test_fw_edge_logs_ikev2_and_ipsec_match(tmp_path: Path) -> None:
    router = Router(tmp_path, "fw-edge", FW_EDGE_TREE)
    rc, cmds = router.run()
    assert rc == 0, cmds
    assert "set firewall ipv4 name FROM-INET default-log" in cmds
    assert "set firewall ipv4 name FROM-CORP default-log" in cmds
    assert "set system syslog remote 10.40.10.11 facility all level warning" in cmds
    assert "set vpn ipsec ike-group IKE-1W key-exchange ikev2" in cmds
    # Only the rule whose source is utility-derms gets the IPsec policy match.
    assert "set firewall ipv4 name FROM-INET rule 20 ipsec match-ipsec-in" in cmds
    assert not any("rule 10 ipsec" in c for c in cmds)
    assert not any(c.startswith("set service ntp") for c in cmds)
    assert cmds[-2:] == ["commit", "save"]
    assert (tmp_path / "cm-vyos-config.done").read_text() == ""
    # The marker makes the next miniccc connect a no-op.
    rc, cmds = router.run()
    assert (rc, cmds) == (0, [])


def test_older_builds_get_match_ipsec(tmp_path: Path) -> None:
    router = Router(tmp_path, "fw-edge", FW_EDGE_TREE)
    rc, cmds = router.run(FAKE_REJECT="match-ipsec-in")
    assert rc == 0
    assert "set firewall ipv4 name FROM-INET rule 20 ipsec match-ipsec" in cmds


def test_fw_ot_takes_time_from_ot_svc(tmp_path: Path) -> None:
    router = Router(tmp_path, "fw-ot", FW_EDGE_TREE)
    rc, cmds = router.run()
    assert rc == 0
    assert "delete service ntp server 203.0.113.10" in cmds
    assert "set service ntp server 10.20.30.13 prefer" in cmds
    assert not any("ike-group" in c or "ipsec match" in c for c in cmds)


def test_util_rtr_sets_only_ikev2(tmp_path: Path) -> None:
    router = Router(tmp_path, "util-rtr", {})
    rc, cmds = router.run()
    assert rc == 0
    assert [c for c in cmds if c.startswith("set")] == ["set vpn ipsec ike-group IKE-1W key-exchange ikev2"]


def test_dry_run_compares_and_discards(tmp_path: Path) -> None:
    router = Router(tmp_path, "fw-edge", FW_EDGE_TREE)
    rc, cmds = router.run("--dry-run")
    assert rc == 0
    assert cmds[-2:] == ["compare", "discard"]
    assert "commit" not in cmds
    assert not (tmp_path / "cm-vyos-config.done").exists()


def test_a_failed_step_is_committed_and_recorded(tmp_path: Path) -> None:
    router = Router(tmp_path, "fw-ot", FW_EDGE_TREE)
    rc, cmds = router.run(FAKE_REJECT="prefer")
    assert rc == 1
    assert "commit" in cmds
    assert "set service ntp server 10.20.30.13 prefer" in (tmp_path / "cm-vyos-config.done").read_text()


def test_a_failed_commit_leaves_no_marker(tmp_path: Path) -> None:
    router = Router(tmp_path, "fw-edge", FW_EDGE_TREE)
    rc, _ = router.run(FAKE_COMMIT_RC="1")
    assert rc == 1
    assert not (tmp_path / "cm-vyos-config.done").exists()


@pytest.mark.parametrize("arg", ["--bogus", "extra"])
def test_bad_arguments(tmp_path: Path, arg: str) -> None:
    router = Router(tmp_path, "fw-edge", FW_EDGE_TREE)
    rc, cmds = router.run(arg)
    assert (rc, cmds) == (2, [])
