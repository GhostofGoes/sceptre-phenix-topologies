"""post-start, running, and cleanup on the ci experiment, with minimega and
the sim-ctl event API faked."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from cherrymountain_apps import simctl
from cherrymountain_apps.cherrymountain import kpi
from cherrymountain_apps.cherrymountain.app import CherryMountain
from cherrymountain_apps.cherrymountain.tests.conftest import RunApp, app_block


class FakeVms:
    """Answers the provisioning checks the app runs through cc."""

    def __init__(self, states: dict[str, str]) -> None:
        self.states: dict[str, str] = states
        self.commands: list[tuple[str, str]] = []

    def __call__(
        self, app: CherryMountain, vm: str, cmd: str, timeout: float, grace: float
    ) -> dict[str, Any]:
        self.commands.append((vm, cmd))
        if vm not in self.states:
            raise TimeoutError("no C2 client")
        units = (
            cmd.split("systemctl is-active ", 1)[1].split(" ||", 1)[0].split()
            if "is-active" in cmd
            else []
        )
        return {
            "exitcode": 0,
            "stdout": "\n".join(self.states[vm] for _ in units) or self.states[vm],
            "stderr": "",
        }


@pytest.fixture
def mm(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    fake = MagicMock(name="minimega")
    monkeypatch.setattr(CherryMountain, "mm", property(lambda self: fake))
    return fake


def patch_exec(monkeypatch: pytest.MonkeyPatch, fake: FakeVms) -> None:
    monkeypatch.setattr(CherryMountain, "_exec", lambda self, *args: fake(self, *args))


def status(app: CherryMountain) -> dict[str, Any]:
    return app.experiment.status.apps.cherrymountain.to_dict()


def test_post_start_records_pending_and_no_kpis(
    run_app: RunApp, mm: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeVms(
        {"plc-cellar": "activating", "scada-fuxa": "inactive", "sim-process": "active"}
    )
    patch_exec(monkeypatch, fake)
    mm.cc_tunnel_list.return_value = []
    app = run_app("post-start")
    st = status(app)
    assert st["stage"] == "post-start"
    prov = st["provisioning"]
    assert prov["plc-cellar"]["state"] == "pending"
    assert prov["sim-process"] == {"state": "provisioned"}
    assert prov["sim-ctl"] == {"state": "not-checked"}
    assert prov["rio-cellar"] == {"state": "not-checked"}
    assert st["provisioned"] == "1/5"
    assert st["kpis"]["available"] is False
    assert "no tunnel to sim-ctl:8080" in st["kpis"]["reason"]
    assert (
        "sim-process",
        "systemctl is-active cm-federate@cellar.service || true",
    ) in fake.commands


def test_running_reads_kpis_through_the_tunnel(
    run_app: RunApp, mm: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_exec(
        monkeypatch,
        FakeVms(dict.fromkeys(("plc-cellar", "scada-fuxa", "sim-process"), "active")),
    )
    mm.cc_tunnel_list.return_value = [
        {
            "Host": "phenix-host",
            "Header": ["id", "src port", "dst", "dst port"],
            "Tabular": [
                ["1", "57571", "127.0.0.1", "8080"],
                ["2", "52424", "127.0.0.1", "3000"],
            ],
        }
    ]
    monkeypatch.setattr(simctl, "is_headnode", lambda mm, host: True)
    seen: dict[str, str] = {}

    def kpis(self: simctl.EventApi) -> dict[str, Any]:
        seen["url"] = self.base_url
        return {
            "batches_completed": 2,
            "bottles_packaged": 3968,
            "kegs_packaged": 2,
            "energy_kwh": 120.0,
            "demand_kw": 88.5,
        }

    monkeypatch.setattr(simctl.EventApi, "kpis", kpis)
    app = run_app("running")
    st = status(app)
    assert seen["url"] == "http://127.0.0.1:57571"
    assert st["provisioned"] == "3/5"
    k = st["kpis"]
    assert k["available"] is True
    assert k["batches"] == 2
    assert k["bottles"] == 3968
    # 3968 bottles x 12 oz = 12 bbl, plus 2 half-barrel kegs = 13 bbl.
    assert k["packaged_bbl"] == 13.0
    assert k["kwh_per_bbl"] == round(120.0 / 13.0, 2)
    assert k["demand_kw"] == 88.5


def test_failed_unit_is_reported(
    run_app: RunApp, mm: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_exec(monkeypatch, FakeVms({"plc-cellar": "failed"}))
    mm.cc_tunnel_list.return_value = []
    st = status(run_app("running"))
    assert st["provisioning"]["plc-cellar"] == {
        "state": "failed",
        "detail": "cm-openplc-provision.service",
    }
    assert st["provisioning"]["scada-fuxa"]["state"] == "unreachable"


def test_dry_run_observes_nothing(run_app: RunApp, mm: MagicMock) -> None:
    st = status(run_app("post-start", dryrun=True))
    assert st["dryRun"] is True
    mm.cc_tunnel_list.assert_not_called()


def test_cleanup_exports_to_files(
    run_app: RunApp, mm: MagicMock, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PHENIX_FILES_DIR", str(tmp_path / "files"))
    ran: list[tuple[str, str]] = []

    def fake_exec(
        app: CherryMountain, vm: str, cmd: str, timeout: float, grace: float
    ) -> dict[str, Any]:
        ran.append((vm, cmd))
        return {"exitcode": 0, "stdout": "", "stderr": ""}

    copied: list[tuple[str, str, str]] = []
    monkeypatch.setattr(CherryMountain, "_exec", fake_exec)
    monkeypatch.setattr(
        "phenix_apps.common.utils.mm_recv",
        lambda mm, vm, src, dst: copied.append((vm, src, dst)),
    )
    st = status(run_app("cleanup"))
    assert ran == [("scada-fuxa", "/usr/local/bin/cm-export-fuxa /tmp/cm-export")]
    dest = str(tmp_path / "files" / "cherrymountain" / "scada-fuxa-fuxa-export.tar.gz")
    assert copied == [("scada-fuxa", "/tmp/cm-export/fuxa-export.tar.gz", dest)]
    assert st["exports"] == {"scada-fuxa": dest}


def test_cleanup_failure_is_recorded_not_raised(
    run_app: RunApp, mm: MagicMock, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PHENIX_FILES_DIR", str(tmp_path / "files"))
    monkeypatch.setattr(CherryMountain, "_exec", lambda *a, **k: {"exitcode": 1})
    st = status(run_app("cleanup"))
    assert st["exports"] == {"scada-fuxa": "failed: RuntimeError"}


def test_cleanup_can_be_turned_off(
    run_app: RunApp,
    mm: MagicMock,
    experiment: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_block(experiment)["metadata"]["exportOnCleanup"] = False
    calls: list[Any] = []
    monkeypatch.setattr(CherryMountain, "_exec", lambda *a, **k: calls.append(a))
    run_app("cleanup", exp=experiment)
    assert calls == []


def test_status_keeps_what_an_earlier_stage_wrote(
    run_app: RunApp,
    mm: MagicMock,
    experiment: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    experiment["status"]["apps"]["cherrymountain"] = {"exports": {"x": "y"}}
    patch_exec(monkeypatch, FakeVms({}))
    mm.cc_tunnel_list.return_value = []
    st = status(run_app("running", exp=experiment))
    assert st["exports"] == {"x": "y"}
    assert "provisioning" in st


# ---------------------------------------------------------------- KPI reader


def test_kpi_reader_reports_a_bad_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        simctl,
        "resolve",
        lambda *a, **k: simctl.Endpoint(
            vm="sim-ctl", guest_port=8080, cluster_host="h", host="127.0.0.1", port=1
        ),
    )
    monkeypatch.setattr(simctl.EventApi, "kpis", lambda self: {"bottles_packaged": -4})
    got = kpi.read(MagicMock(), "sim-ctl", 8080, 1.0)
    assert got.available is False
    assert got.reason == "event API: the KPI reply has the wrong shape"


def test_kpi_reader_reports_http_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        simctl,
        "resolve",
        lambda *a, **k: simctl.Endpoint(
            vm="sim-ctl", guest_port=8080, cluster_host="h", host="127.0.0.1", port=1
        ),
    )

    def boom(self: simctl.EventApi) -> Any:
        raise simctl.ApiError("GET /api/v1/kpis: HTTP 404")

    monkeypatch.setattr(simctl.EventApi, "kpis", boom)
    got = kpi.read(MagicMock(), "sim-ctl", 8080, 1.0)
    assert got.reason == "event API: GET /api/v1/kpis: HTTP 404"


def test_kpis_json_round_trip() -> None:
    k = kpi.derive(kpi.SimKpis(bottles_packaged=0, energy_kwh=10.0), "now")
    assert json.loads(k.model_dump_json())["kwh_per_bbl"] is None
