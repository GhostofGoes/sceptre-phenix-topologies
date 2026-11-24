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

#: The VyOS script template. The session commands are ALIASES of vyatta_cfg_run, as
#: vyatta-cfg makes them (functions/interpreter/vyatta-cfg-run), and `exit` is the
#: alias of functions/wrapper/script-template that closes the session without ending
#: the script. A stand-in with shell functions hid that the script's wrappers never
#: reached VyOS: bash expands an alias only at command position. `set` through the
#: alias reports success whatever happened, and `commit` returns 1 when nothing changed.
TEMPLATE: str = """
record() { printf '%s\\n' "$*" >> "$FAKE_LOG"; }
vyatta_cfg_run() {
  case "$1" in
    set | delete) record "alias-$*" ;;
    commit)
      if [ ! -e "$FAKE_CHANGED" ]; then
        echo "No configuration changes to commit"
        return 1
      fi
      record commit
      return "${FAKE_COMMIT_RC:-0}"
      ;;
    *) record "$*" ;;
  esac
}
for cmd in commit compare delete discard save set; do
  eval alias $cmd=\\'vyatta_cfg_run $cmd\\'
done
shopt -s expand_aliases
vyatta_configure() { record configure >&2; echo true; }
vyatta_exit_configure() { record teardown; echo -n 'export -n VYATTA_EDIT_LEVEL; '; }
alias configure='eval $(vyatta_configure)'
alias exit='eval $(vyatta_exit_configure)'
"""

#: my_set and my_delete, the programs behind the aliases: they exit 1 on a path
#: VyOS does not take, and a change makes the session differ from the running config.
BACKEND: str = """#!/bin/sh
cmd=${0##*/my_}
printf '%s\\n' "$cmd $*" >> "$FAKE_LOG"
case " $* " in *" $FAKE_REJECT "*) echo "Set failed" ; exit 1 ;; esac
: > "$FAKE_CHANGED"
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
        for name in ("my_set", "my_delete"):
            (bin_dir / name).write_text(BACKEND)
            (bin_dir / name).chmod(0o755)
        # The two boot markers the script waits for: VyOS's boot config status
        # and the flag of the image's boot hook. A booted router has both.
        self.boot_status: Path = tmp / "vyos-config-status"
        self.boot_flag: Path = tmp / "phenix-run-flag"
        self.boot_status.write_text("0\n")
        self.boot_flag.write_text("phenix startup completed\n")
        self.stderr: str = ""
        self.env: dict[str, str] = {
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "CM_VYOS_TEMPLATE": str(tmp / "template.sh"),
            "CM_VYOS_BACKEND": str(bin_dir / "my_"),
            "CM_VYOS_RUN_DIR": str(tmp),
            "CM_VYOS_BOOT_STATUS": str(self.boot_status),
            "CM_VYOS_BOOT_FLAG": str(self.boot_flag),
            "CM_VYOS_WAIT_TRIES": "2",
            "CM_VYOS_WAIT_SLEEP": "0",
            "FAKE_LOG": str(self.log),
            "FAKE_TREE": str(tmp / "tree.json"),
            "FAKE_REJECT": "__nothing__",
            "FAKE_CHANGED": str(tmp / "session-changed"),
        }

    def run(self, *args: str, script: Path = SCRIPT, **env: str) -> tuple[int, list[str]]:
        self.log.unlink(missing_ok=True)
        (self.tmp / "session-changed").unlink(missing_ok=True)
        proc = subprocess.run(
            ["bash", str(script), *args],
            env={**self.env, **env},
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        self.stderr = proc.stderr
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
    assert cmds[-3:] == ["commit", "save", "teardown"]
    # No change went to bash's own `set` or to the alias, which hides a refusal.
    assert not any(c.startswith("alias-") for c in cmds)
    assert (tmp_path / "cm-vyos-config.done").read_text() == ""
    # The marker makes the next miniccc connect a no-op.
    rc, cmds = router.run()
    assert (rc, cmds) == (0, [])


def test_a_router_with_nothing_to_change_is_done(tmp_path: Path) -> None:
    """`commit` returns 1 when the session holds no change: on isp-rtr, where the
    script sets nothing, and on every router after a reboot (the first boot saved
    the changes). That is not a failed commit, and the marker is written."""
    router = Router(tmp_path, "isp-rtr", {})
    rc, cmds = router.run()
    assert rc == 0, router.stderr
    assert "commit" not in cmds and "save" not in cmds and cmds[-1] == "teardown"
    assert (tmp_path / "cm-vyos-config.done").read_text() == ""


def test_the_script_ends_with_its_status_after_the_template(tmp_path: Path) -> None:
    """After the template, `exit` is an alias that does not end the script and takes
    no argument: `exit 1` printed "1: command not found" and the run ended with 0."""
    router = Router(tmp_path, "fw-edge", FW_EDGE_TREE)
    rc, cmds = router.run(FAKE_COMMIT_RC="1")
    assert rc == 1 and "command not found" not in router.stderr
    assert cmds[-1] == "teardown" and "save" not in cmds


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
    assert cmds[-3:] == ["compare", "discard", "teardown"]
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


@pytest.mark.parametrize("marker", ["boot_status", "boot_flag"])
def test_waits_for_both_boot_markers(tmp_path: Path, marker: str) -> None:
    """The host name is set first in a commit, before the firewall and IPsec
    nodes exist, so the script must not configure until VyOS has loaded its
    boot config and the boot hook has finished the vrouter config."""
    router = Router(tmp_path, "fw-edge", FW_EDGE_TREE)
    missing: Path = getattr(router, marker)
    missing.unlink()
    rc, cmds = router.run()
    assert (rc, cmds) == (1, [])
    assert str(missing) in router.stderr
    assert not (tmp_path / "cm-vyos-config.done").exists()
    # The next miniccc connect, once the router has booted, does the work.
    missing.write_text("")
    rc, cmds = router.run()
    assert rc == 0
    assert cmds[-3:] == ["commit", "save", "teardown"]


def test_boot_flag_defaults_to_the_script_directory(tmp_path: Path) -> None:
    """The image's boot hook writes phenix-run-flag next to the custom scripts
    (scripts/vyos/build-vyos.sh in sceptre-phenix-images), which is where the
    topology injects this script."""
    custom: Path = tmp_path / "boot" / "vyos" / "rw" / "config" / "scripts" / "custom"
    custom.mkdir(parents=True)
    script: Path = custom / SCRIPT.name
    script.write_text(SCRIPT.read_text())
    router = Router(tmp_path, "util-rtr", {})
    del router.env["CM_VYOS_BOOT_FLAG"]
    rc, cmds = router.run(script=script)
    assert (rc, cmds) == (1, [])
    assert str(custom / "phenix-run-flag") in router.stderr
    (custom / "phenix-run-flag").write_text("phenix startup completed\n")
    rc, cmds = router.run(script=script)
    assert rc == 0
    assert "set vpn ipsec ike-group IKE-1W key-exchange ikev2" in cmds


@pytest.mark.parametrize("arg", ["--bogus", "extra"])
def test_bad_arguments(tmp_path: Path, arg: str) -> None:
    router = Router(tmp_path, "fw-edge", FW_EDGE_TREE)
    rc, cmds = router.run(arg)
    assert (rc, cmds) == (2, [])


# --- a site's own copy (plantgen renders it, sync.py puts it under fleet/<code>/) ---

SITE_COPIES: Path = SCRIPT.parents[1] / "fleet"


def test_a_sites_copy_knows_the_sites_firewalls(tmp_path: Path) -> None:
    """The copy of `ep` waits for the site's names, drops the site's prefix,
    and then does what the script does for fw-ot, with the site's time server."""
    router = Router(tmp_path, "ep-fw-ot", FW_EDGE_TREE)
    rc, cmds = router.run(script=SITE_COPIES / "ep" / "vrouter" / SCRIPT.name)
    assert rc == 0, router.stderr
    assert "delete service ntp server 203.0.113.10" in cmds
    assert "set service ntp server 10.22.30.13 prefer" in cmds
    assert "set firewall ipv4 name FROM-INET default-log" in cmds
    assert not any("ike-group" in c or "ipsec match" in c for c in cmds)
    assert cmds[-3:] == ["commit", "save", "teardown"]


def test_the_pubs_copy_does_the_edge_steps_on_its_one_firewall(tmp_path: Path) -> None:
    router = Router(tmp_path, "fc-fw-edge", FW_EDGE_TREE)
    rc, cmds = router.run(script=SITE_COPIES / "fc" / "vrouter" / SCRIPT.name)
    assert rc == 0, router.stderr
    assert "set system syslog remote 10.40.10.11 facility all level warning" in cmds
    assert "set firewall ipv4 name FROM-INET default-log" in cmds
    assert not any(c.startswith("set service ntp") for c in cmds)
    # The steps of the utility's IPsec are the edge's WAN side: not in a site's copy.
    assert not any("ike-group" in c or "ipsec" in c for c in cmds)


@pytest.mark.parametrize("code", ["fr", "ep", "cs", "fc"])
def test_a_sites_copy_sets_nothing_of_the_utilitys_ipsec(tmp_path: Path, code: str) -> None:
    """The tree of the stand-in has the rule that the single site's script
    gives the IPsec match. A site's copy leaves that rule and the IKE group alone."""
    router = Router(tmp_path, f"{code}-fw-edge", FW_EDGE_TREE)
    rc, cmds = router.run(script=SITE_COPIES / code / "vrouter" / SCRIPT.name)
    assert rc == 0, router.stderr
    assert not any("ipsec" in c or "ike" in c for c in cmds)
    assert cmds[-3:] == ["commit", "save", "teardown"]


@pytest.mark.parametrize(
    ("host", "script"),
    [
        ("fc-fw-edge", SCRIPT),
        ("ep-fw-edge", SITE_COPIES / "fc" / "vrouter" / SCRIPT.name),
        ("fw-edge", SITE_COPIES / "fc" / "vrouter" / SCRIPT.name),
    ],
    ids=["single-site-script", "another-sites-copy", "plain-name"],
)
def test_a_script_does_nothing_on_a_router_that_is_not_its_own(
    tmp_path: Path, host: str, script: Path
) -> None:
    """This is why a site needs its own copy: the single site's script waits
    for the plain names, and never sees them on a site's router."""
    router = Router(tmp_path, host, FW_EDGE_TREE)
    rc, cmds = router.run(script=script)
    assert (rc, cmds) == (1, [])
    assert "the router never got its host name" in router.stderr
