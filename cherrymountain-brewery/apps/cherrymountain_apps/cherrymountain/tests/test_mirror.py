"""post-start and running set ``options:df_default=false`` on the GRE ports of
the mirror app's mirror (DESIGN.md 14), against a fake minimega.

The fake stands in for minimega's control socket: it takes the one command the
step sends, ``namespace <exp> [mesh send <host>] shell <command>``, and runs it
against a model of each cluster host's Open vSwitch. The command line the app
sends and the status the mirror app records are what the Go source gives
(mirror.py has the references); a VM-less test cannot show that a real minimega
and a real Open vSwitch take them, which the first boot has to.
"""

from __future__ import annotations

import copy
import re
import shlex
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
import yaml
from phenix_apps.common import utils

from cherrymountain_apps.cherrymountain import mirror
from cherrymountain_apps.cherrymountain.app import CherryMountain
from cherrymountain_apps.cherrymountain.tests.conftest import ROOT, RunApp
from cherrymountain_apps.cherrymountain.tests.test_observe import (
    FakeVms,
    mm,  # noqa: F401 - fixture
    patch_exec,
    status,
)

EXP: str = "cherrymountain-ci"
#: The mirror app's port names: 15 random characters.
PORT: str = "kQ7xT2mLb9NcR4d"
OTHER_EXPERIMENT_PORT: str = "Zz9yY8xX7wW6vV5"
BRIDGE: str = "cherrymtn"
SET_COMMAND: str = (
    f"ovs-vsctl --timeout=10 set interface {PORT} options:df_default=false"
)


class FakeMinimega:
    """The control socket of a mesh of two cluster hosts, ``head`` (minimega's
    headnode) and ``node2``, each with its own Open vSwitch."""

    def __init__(self) -> None:
        #: host -> port -> its options.
        self.ovs: dict[str, dict[str, dict[str, str]]] = {
            host: {
                PORT: {"remote_ip": "172.30.255.254"},
                # A GRE port of another experiment on the same host.
                OTHER_EXPERIMENT_PORT: {"remote_ip": "172.30.255.253"},
            }
            for host in ("head", "node2")
        }
        #: Hosts the mesh has no route to: minimega answers nothing for them.
        self.unreachable: set[str] = set()
        #: Every command the socket was given.
        self.commands: list[str] = []
        self.fail_socket: bool = False

    def __call__(self, cmd: str, ignore_error: bool = False) -> list[dict[str, Any]]:
        self.commands.append(cmd)
        if self.fail_socket:
            raise ConnectionRefusedError("no minimega socket")
        match = re.fullmatch(r"namespace (\S+) (?:mesh send (\S+) )?shell (.+)", cmd)
        assert match is not None, f"not a command the step may send: {cmd}"
        assert match.group(1) == EXP
        host: str = match.group(2) or "head"
        if host in self.unreachable:
            return []
        argv: list[str] = shlex.split(match.group(3))
        assert argv[0] == "ovs-vsctl", argv
        assert argv[1].startswith("--timeout="), "ovs-vsctl waits forever without one"
        verb, table, name, assignment = argv[2:]
        assert (verb, table) == ("set", "interface")
        port: dict[str, str] | None = self.ovs[host].get(name)
        if port is None:
            return [
                {
                    "Host": host,
                    "Response": "",
                    "Error": f'ovs-vsctl: no row "{name}" in table Interface',
                }
            ]
        key, _, value = assignment.partition("=")
        assert key.startswith("options:")
        port[key.removeprefix("options:")] = value
        return [{"Host": host, "Response": "", "Error": ""}]

    def df_default(self, host: str, port: str = PORT) -> str | None:
        return self.ovs[host][port].get("df_default")


@pytest.fixture
def minimega(mm: MagicMock, monkeypatch: pytest.MonkeyPatch) -> FakeMinimega:  # noqa: F811
    fake: FakeMinimega = FakeMinimega()
    monkeypatch.setattr(utils, "_mm_socket_cmd", fake)
    # minimega's first host is the headnode (phenix's Headnode()); mm_compute_cmd
    # runs ``shell`` on it directly and ``mesh send`` on any other.
    mm.host.return_value = [
        {"Host": host, "Header": ["name"], "Tabular": [[host]]}
        for host in ("head", "node2")
    ]
    mm.cc_tunnel_list.return_value = []
    monkeypatch.setattr(utils.socket, "gethostname", lambda: "head")
    patch_exec(
        monkeypatch,
        FakeVms(dict.fromkeys(("plc-cellar", "scada-fuxa", "sim-process"), "active")),
    )
    return fake


def with_mirror(experiment: dict[str, Any], **changes: Any) -> dict[str, Any]:
    """The ci experiment with a ``mirror`` app, the way phenix hands it over after
    the mirror app's post-start: its status, and the VMs' hosts."""
    exp: dict[str, Any] = copy.deepcopy(experiment)
    app: dict[str, Any] = {
        "name": "mirror",
        "metadata": {
            "version": "v1",
            "mirrorNet": "172.30.0.0/16",
            "mirrorVLAN": "MIRROR",
            "mirrorBridge": BRIDGE,
        },
        "hosts": [{"hostname": "plc-cellar", "metadata": {"interface": "mirror"}}],
    }
    app.update({k: v for k, v in changes.items() if k in ("disabled", "metadata")})
    exp["spec"]["scenario"]["apps"].insert(0, app)
    exp["status"]["schedules"] = {
        "sim-ctl": "head",
        "sim-process": "node2",
        "plc-cellar": "node2",
        "scada-fuxa": "head",
    }
    if changes.get("recorded", True):
        exp["status"]["apps"]["mirror"] = {
            "tapName": "ab12cd34-mirror",
            "subnet": "172.30.0.0/16",
            "mirrors": {
                "plc-cellar": {
                    "mirrorName": PORT,
                    "mirrorBridge": BRIDGE,
                    "ip": "172.30.255.254",
                }
            },
        }
    return exp


def record(app: CherryMountain) -> dict[str, Any]:
    return status(app)["mirror"]


# ------------------------------------------------------------------ the happy path


def test_post_start_sets_df_default_on_every_cluster_host(
    run_app: RunApp, experiment: dict[str, Any], minimega: FakeMinimega
) -> None:
    app = run_app("post-start", exp=with_mirror(experiment))
    # The mirror's port, on both hosts; the other experiment's port is left alone.
    assert minimega.df_default("head") == "false"
    assert minimega.df_default("node2") == "false"
    assert minimega.df_default("head", OTHER_EXPERIMENT_PORT) is None
    assert minimega.df_default("node2", OTHER_EXPERIMENT_PORT) is None
    # The commands are the Go app's way to reach a host: shell on the headnode,
    # mesh send on the other.
    assert minimega.commands == [
        f"namespace {EXP} shell {SET_COMMAND}",
        f"namespace {EXP} mesh send node2 shell {SET_COMMAND}",
    ]
    rec = record(app)
    assert rec["dfDefault"] == "set"
    assert rec["ports"] == {
        PORT: {
            "target": "plc-cellar",
            "bridge": BRIDGE,
            "hosts": {"head": "set", "node2": "set"},
        }
    }
    assert rec["updated"]
    # The provisioning check of the stage still ran and recorded its own.
    assert status(app)["stage"] == "post-start"
    assert status(app)["provisioned"] == "3/5"


def test_every_target_gets_its_port_set(
    run_app: RunApp, experiment: dict[str, Any], minimega: FakeMinimega
) -> None:
    exp = with_mirror(experiment)
    exp["status"]["apps"]["mirror"]["mirrors"]["ews"] = {
        "mirrorName": "Qw3eR4tY5uI6oP7",
        "mirrorBridge": BRIDGE,
        "ip": "172.30.255.253",
    }
    for host in ("head", "node2"):
        minimega.ovs[host]["Qw3eR4tY5uI6oP7"] = {}
    app = run_app("post-start", exp=exp)
    assert sorted(record(app)["ports"]) == sorted([PORT, "Qw3eR4tY5uI6oP7"])
    for host in ("head", "node2"):
        assert minimega.df_default(host) == "false"
        assert minimega.df_default(host, "Qw3eR4tY5uI6oP7") == "false"


def test_repeating_the_step_sends_nothing_again(
    run_app: RunApp, experiment: dict[str, Any], minimega: FakeMinimega
) -> None:
    """A second pass over the same run (the running stage, or a re-triggered
    post-start) finds every port recorded as set."""
    first = run_app("post-start", exp=with_mirror(experiment))
    sent = list(minimega.commands)
    assert len(sent) == 2
    again = run_app("running", exp=first.experiment.to_dict())
    assert minimega.commands == sent
    assert record(again) == record(first) | {"updated": record(again)["updated"]}
    assert minimega.df_default("head") == minimega.df_default("node2") == "false"


# ------------------------------------------------------------------ nothing to do


def test_no_mirror_app_does_nothing(run_app: RunApp, minimega: FakeMinimega) -> None:
    """The ci and lite scenarios have no mirror app."""
    for stage in ("post-start", "running"):
        app = run_app(stage)
        assert "mirror" not in status(app)
    assert minimega.commands == []


def test_a_disabled_mirror_app_does_nothing(
    run_app: RunApp, experiment: dict[str, Any], minimega: FakeMinimega
) -> None:
    """phenix skips a disabled app, so its status never appears."""
    app = run_app(
        "post-start", exp=with_mirror(experiment, disabled=True, recorded=False)
    )
    assert "mirror" not in status(app)
    assert minimega.commands == []


def test_a_dry_run_does_nothing(
    run_app: RunApp, experiment: dict[str, Any], minimega: FakeMinimega
) -> None:
    app = run_app("post-start", exp=with_mirror(experiment), dryrun=True)
    assert "mirror" not in status(app)
    assert status(app)["dryRun"] is True
    assert minimega.commands == []


def test_erspan_ports_are_left_alone(
    run_app: RunApp, experiment: dict[str, Any], minimega: FakeMinimega
) -> None:
    """The step is for GRE ports."""
    exp = with_mirror(
        experiment,
        metadata={
            "version": "v1",
            "mirrorBridge": BRIDGE,
            "erspan": {"enabled": True, "version": 2},
        },
    )
    app = run_app("post-start", exp=exp)
    assert record(app)["dfDefault"] == "skipped"
    assert "ERSPAN" in record(app)["reason"]
    assert minimega.commands == []


def test_before_the_mirror_app_has_recorded_its_ports_the_step_waits(
    run_app: RunApp, experiment: dict[str, Any], minimega: FakeMinimega
) -> None:
    """With the mirror app after this one in the scenario, its status is not
    there at post-start; the running stage finds it once it is."""
    waiting = run_app("post-start", exp=with_mirror(experiment, recorded=False))
    assert record(waiting)["dfDefault"] == "waiting"
    assert "not recorded yet" in record(waiting)["reason"]
    assert minimega.commands == []
    exp = waiting.experiment.to_dict()
    exp["status"]["apps"]["mirror"] = with_mirror(experiment)["status"]["apps"][
        "mirror"
    ]
    done = run_app("running", exp=exp)
    assert record(done)["dfDefault"] == "set"
    assert minimega.df_default("head") == minimega.df_default("node2") == "false"


# ------------------------------------------------------------------ failures


def test_a_host_that_does_not_answer_is_recorded_and_running_sets_it_later(
    run_app: RunApp, experiment: dict[str, Any], minimega: FakeMinimega
) -> None:
    minimega.unreachable.add("node2")
    app = run_app("post-start", exp=with_mirror(experiment))
    rec = record(app)
    assert rec["dfDefault"] == "incomplete"
    assert rec["ports"][PORT]["hosts"] == {
        "head": "set",
        "node2": "failed: no reply from minimega",
    }
    assert minimega.df_default("head") == "false"
    # The stage did not fail: its other work is in the status.
    assert status(app)["stage"] == "post-start"
    # The host answers now: running sends the command to it only.
    minimega.unreachable.clear()
    minimega.commands.clear()
    later = run_app("running", exp=app.experiment.to_dict())
    assert minimega.commands == [f"namespace {EXP} mesh send node2 shell {SET_COMMAND}"]
    assert record(later)["dfDefault"] == "set"
    assert minimega.df_default("node2") == "false"


def test_a_port_that_is_not_there_is_recorded_with_openvswitchs_message(
    run_app: RunApp, experiment: dict[str, Any], minimega: FakeMinimega
) -> None:
    del minimega.ovs["node2"][PORT]
    app = run_app("post-start", exp=with_mirror(experiment))
    rec = record(app)
    assert rec["dfDefault"] == "incomplete"
    assert rec["ports"][PORT]["hosts"]["head"] == "set"
    assert (
        rec["ports"][PORT]["hosts"]["node2"]
        == f'failed: ovs-vsctl: no row "{PORT}" in table Interface'
    )


def test_a_dead_minimega_socket_does_not_fail_post_start(
    run_app: RunApp, experiment: dict[str, Any], minimega: FakeMinimega
) -> None:
    """phenix stops the experiment when a post-start stage fails."""
    minimega.fail_socket = True
    app = run_app("post-start", exp=with_mirror(experiment))
    rec = record(app)
    assert rec["dfDefault"] == "incomplete"
    assert set(rec["ports"][PORT]["hosts"].values()) == {"failed: RuntimeError"}
    assert status(app)["stage"] == "post-start"


def test_a_status_the_step_cannot_read_does_not_fail_post_start(
    run_app: RunApp, experiment: dict[str, Any], minimega: FakeMinimega
) -> None:
    exp = with_mirror(experiment)
    exp["status"]["apps"]["mirror"]["mirrors"]["plc-cellar"]["mirrorName"] = (
        "not a port name"
    )
    app = run_app("post-start", exp=exp)
    rec = record(app)
    assert rec["dfDefault"] == "incomplete"
    assert rec["error"] == "ValidationError"
    assert "not a port name" not in str(rec)
    assert minimega.commands == []
    assert status(app)["stage"] == "post-start"


def test_a_running_pass_that_fails_still_reports_provisioning(
    run_app: RunApp, experiment: dict[str, Any], minimega: FakeMinimega
) -> None:
    minimega.fail_socket = True
    app = run_app("running", exp=with_mirror(experiment))
    assert record(app)["dfDefault"] == "incomplete"
    assert status(app)["stage"] == "running"
    assert status(app)["provisioned"] == "3/5"


# ------------------------------------------------------------------ the pieces


def test_the_command_is_plain_words_for_minimegas_shell() -> None:
    cmd = mirror.command(PORT)
    assert cmd == SET_COMMAND
    assert not set(cmd) & set("|&;<>$`'\"\\#")


def test_cluster_hosts_are_the_hosts_of_the_schedule_once() -> None:
    assert mirror.cluster_hosts({"a": "h2", "b": "h1", "c": "h2", "d": ""}) == [
        "h1",
        "h2",
    ]
    assert mirror.cluster_hosts({}) == []


@pytest.mark.parametrize(
    ("metadata", "erspan"),
    [
        ({"mirrorBridge": "b"}, False),
        ({"erspan": {"enabled": False}}, False),
        ({"erspan": {"enabled": True}}, True),
        ({"directGRE": {"enabled": True, "erspan": {"enabled": True}}}, True),
        ({"directGRE": {"enabled": True}}, False),
    ],
)
def test_erspan_is_read_from_both_metadata_shapes(
    metadata: dict[str, Any], erspan: bool
) -> None:
    assert mirror.uses_erspan(metadata) is erspan


@pytest.mark.parametrize("name", ["", "has space", "a;b", "x" * 16, "a'b"])
def test_a_port_name_that_is_not_one_token_is_refused(name: str) -> None:
    with pytest.raises(ValueError, match=r"mirrorName|mirror_name"):
        mirror.MirrorStatus.model_validate(
            {"mirrors": {"m": {"mirrorName": name, "mirrorBridge": BRIDGE}}}
        )


def test_the_mirror_status_is_read_as_the_go_app_writes_it() -> None:
    """The keys of phenix's stored status.apps.mirror (the structs tags of
    MirrorAppStatus and MirrorConfig); other keys, the tap name and subnet, are
    not needed."""
    parsed = mirror.MirrorStatus.model_validate(
        {
            "tapName": "ab12cd34-mirror",
            "subnet": "172.30.0.0/16",
            "mirrors": {
                "malcolm": {
                    "mirrorName": PORT,
                    "mirrorBridge": BRIDGE,
                    "ip": "172.30.255.254",
                }
            },
        }
    )
    assert parsed.mirrors["malcolm"].mirror_name == PORT
    assert parsed.mirrors["malcolm"].mirror_bridge == BRIDGE


def test_the_full_scenario_runs_the_mirror_app_before_this_one() -> None:
    """Apps run each stage in scenario order, so the mirror's post-start (which
    records the ports) has to come first for this app's post-start to see them."""
    scenario = yaml.safe_load(
        (ROOT / "phenix-configs" / "scenario-full.yml").read_text(encoding="utf-8")
    )
    names: list[str] = [a["name"] for a in scenario["spec"]["apps"]]
    assert names.index("mirror") < names.index("cherrymountain")
    # The contract of the other apps is unchanged.
    assert names.index("ot-sim") < names.index("cherrymountain") < names.index("helics")


def test_ci_and_lite_have_no_mirror_app() -> None:
    for variant in ("ci", "lite"):
        text = Path(ROOT / "phenix-configs" / f"scenario-{variant}.yml").read_text(
            encoding="utf-8"
        )
        scenario = yaml.safe_load(text)
        assert "mirror" not in [a["name"] for a in scenario["spec"]["apps"]], variant
