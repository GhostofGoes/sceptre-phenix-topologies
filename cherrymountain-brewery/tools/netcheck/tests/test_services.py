"""The BBMD service and its per-node settings."""

from __future__ import annotations

from pathlib import Path

import cm_bbmd
import pytest

from netcheck import plan


def env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in path.read_text().splitlines():
        if line and not line.startswith("#"):
            key, _, value = line.partition("=")
            out[key] = value
    return out


def test_both_nodes_list_both_bbmds(repo: Path) -> None:
    ops = cm_bbmd.BbmdConfig.from_env(env_file(repo / "phenix-injects/bbmd/ot-svc.env"))
    bas = cm_bbmd.BbmdConfig.from_env(env_file(repo / "phenix-injects/bbmd/bas-bbmd.env"))
    want: list[str] = [
        f"{plan.NODE_BY_NAME['ot-svc'].primary_ip}:47808",
        f"{plan.NODE_BY_NAME['bas-bbmd'].primary_ip}:47808",
    ]
    assert ops.bdt == want
    assert bas.bdt == want


def test_the_ops_bbmd_listens_on_a_zero_prefix(repo: Path) -> None:
    ops = cm_bbmd.BbmdConfig.from_env(env_file(repo / "phenix-injects/bbmd/ot-svc.env"))
    bas = cm_bbmd.BbmdConfig.from_env(env_file(repo / "phenix-injects/bbmd/bas-bbmd.env"))
    assert ops.address == "10.20.30.13/0:47808"
    assert bas.address == "10.30.10.5/24:47808"


def test_arguments_for_bacpypes3() -> None:
    cfg = cm_bbmd.BbmdConfig(
        name="b", instance=7, address="10.0.0.1/24:47808", bdt=["10.0.0.1:47808", "10.0.1.1:47808"]
    )
    assert cfg.bacpypes_args() == [
        "--name", "b", "--instance", "7", "--address", "10.0.0.1/24:47808",
        "--bbmd", "10.0.0.1:47808", "10.0.1.1:47808",
    ]  # fmt: skip


@pytest.mark.parametrize(
    "bdt",
    [
        ["10.0.0.9:47808", "10.0.1.1:47808"],
        ["10.0.0.1:47808"],
        ["10.0.0.1:99999", "10.0.1.1:47808"],
    ],
)
def test_bad_tables_are_refused(bdt: list[str]) -> None:
    with pytest.raises(ValueError, match="BDT|port|at least"):
        cm_bbmd.BbmdConfig(name="b", instance=7, address="10.0.0.1/24:47808", bdt=bdt)


def test_the_bbmd_keeps_readline_out(monkeypatch: pytest.MonkeyPatch) -> None:
    """readline's ncurses calls setfsuid(), which cm-bbmd.service filters; a
    filtered call kills the BBMD, so BACpypes3 must not get to import it."""
    import sys  # noqa: PLC0415

    monkeypatch.delitem(sys.modules, "readline", raising=False)
    cm_bbmd.block_readline()
    with pytest.raises(ImportError):
        import readline  # noqa: F401, PLC0415
    monkeypatch.delitem(sys.modules, "readline", raising=False)


def test_the_bbmd_unit_filters_what_readline_calls(repo: Path) -> None:
    """The reason block_readline exists: keep the test honest if the unit changes."""
    unit = (repo / "phenix-injects/bbmd/cm-bbmd.service").read_text()
    assert "SystemCallFilter=~@privileged" in unit


def test_main_reports_missing_settings(capsys: pytest.CaptureFixture[str]) -> None:
    assert cm_bbmd.main(["--check"], {}) == 2
    assert "missing setting" in capsys.readouterr().err


def test_main_check_prints_the_arguments(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    env = env_file(repo / "phenix-injects/bbmd/bas-bbmd.env")
    assert cm_bbmd.main(["--check"], env) == 0
    assert "--bbmd 10.20.30.13:47808 10.30.10.5:47808" in capsys.readouterr().out


def test_probe_packets_match_the_design() -> None:
    from netcheck import conduits  # noqa: PLC0415

    assert conduits.bacnet_read_device_name(3003) == "gQoAEQEEAAUBDAwCAAu7GU0="
    assert conduits.bvlc_read_bdt() == "gQIABA=="
