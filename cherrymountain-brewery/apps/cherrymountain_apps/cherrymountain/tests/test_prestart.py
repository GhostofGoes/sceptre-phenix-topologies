"""pre-start on the ci experiment: what is rendered and injected where."""

from __future__ import annotations

import json
import stat
import zipfile
from pathlib import Path
from typing import Any

import pytest
from phenix_apps.common.error import AppError

from cherrymountain_apps.cherrymountain.tests.conftest import (
    ROOT,
    RunApp,
    app_block,
    node,
)


def injections(app: Any, hostname: str) -> dict[str, Any]:
    return {i.dst: i for i in app.extract_node(hostname).get("injections") or []}


def test_prestart_injects_every_ci_host(run_app: RunApp) -> None:
    app = run_app("pre-start")
    plc = injections(app, "plc-cellar")
    for dst in (
        "/opt/cherrymountain/openplc/plc-cellar.zip",
        "/opt/cherrymountain/openplc/conf/modbus_master.json",
        "/opt/cherrymountain/openplc/conf/modbus_slave.json",
        "/etc/default/cm-openplc",
        "/usr/local/bin/cm-openplc-provision",
        "/etc/systemd/system/cm-openplc-provision.service",
        "/etc/phenix/startup/70-openplc-provision.sh",
    ):
        assert dst in plc
    fuxa = injections(app, "scada-fuxa")
    assert fuxa["/opt/fuxa/server/_appdata/mysettings.json"].permissions == "0600"
    assert "/usr/local/bin/cm-export-fuxa" in fuxa
    sim = injections(app, "sim-process")
    assert "/etc/systemd/system/cm-federate@.service" in sim
    assert Path(sim["/etc/cherrymountain/federates"].src).read_text() == "cellar\n"
    for host in ("sim-ctl", "sim-process", "rio-cellar"):
        assert "/etc/cherrymountain/nftables-mgmt.nft" in injections(app, host)
    # plc-cellar and scada-fuxa have no MGMT NIC.
    assert "/etc/cherrymountain/nftables-mgmt.nft" not in plc


def test_rendered_files_replace_static_topology_injections(run_app: RunApp) -> None:
    app = run_app("pre-start")
    dsts = [i.dst for i in app.extract_node("plc-cellar").injections]
    assert len(dsts) == len(set(dsts))
    env = injections(app, "plc-cellar")["/etc/default/cm-openplc"]
    assert env.src.startswith(str(app.out_dir))
    # Every source is a copy under the experiment directory.
    for n in app.experiment.spec.topology.nodes:
        for inj in n.get("injections") or []:
            if "/phenix/injects/" not in inj.src:
                assert inj.src.startswith(str(app.out_dir)), inj.src


def test_injection_sources_exist_and_secret_files_are_private(run_app: RunApp) -> None:
    app = run_app("pre-start")
    for n in app.experiment.spec.topology.nodes:
        for inj in n.get("injections") or []:
            if inj.src.startswith(str(app.out_dir)):
                path = Path(inj.src)
                assert path.is_file(), inj.src
                if inj.permissions == "0600":
                    assert stat.S_IMODE(path.stat().st_mode) == 0o600, inj.src


def test_openplc_env_carries_the_generated_password(run_app: RunApp) -> None:
    app = run_app("pre-start")
    secrets = json.loads((app.out_dir / "secrets.json").read_text())
    env = Path(injections(app, "plc-cellar")["/etc/default/cm-openplc"].src).read_text()
    assert f"OPENPLC_PASSWORD={secrets['openplc_password']}" in env
    assert "CM_PLC_CONF=/opt/cherrymountain/openplc/conf" in env


def test_secrets_survive_a_restart(run_app: RunApp, experiment: dict[str, Any]) -> None:
    first = json.loads((run_app("pre-start").out_dir / "secrets.json").read_text())
    second = json.loads(
        (run_app("pre-start", exp=experiment).out_dir / "secrets.json").read_text()
    )
    assert first == second


def test_metadata_secret_wins(run_app: RunApp, experiment: dict[str, Any]) -> None:
    app_block(experiment)["metadata"]["secrets"] = {"fuxaPassword": "range-fuxa-pw"}
    app = run_app("pre-start", exp=experiment)
    env = Path(injections(app, "scada-fuxa")["/etc/default/cm-fuxa"].src).read_text()
    assert "FUXA_ADMIN_PASSWORD=range-fuxa-pw" in env
    assert "FUXA_EXPECT_DEVICES=plc-cellar" in env


def test_fuxa_project_is_the_builders(run_app: RunApp) -> None:
    app = run_app("pre-start")
    project = json.loads(
        Path(
            injections(app, "scada-fuxa")["/etc/cherrymountain/fuxa/project.json"].src
        ).read_text()
    )
    committed = json.loads((ROOT / "fuxa" / "scada-fuxa-project.json").read_text())
    assert project == committed
    settings = json.loads(
        Path(
            injections(app, "scada-fuxa")[
                "/opt/fuxa/server/_appdata/mysettings.json"
            ].src
        ).read_text()
    )
    assert settings["secureEnabled"] is True
    assert settings["nodeRedEnabled"] is False
    # ci has no historian, so DAQ stays in FUXA's SQLite.
    assert settings["daqstore"] == {"type": "SQlite"}


def test_plantgen_configs_are_copied_verbatim(run_app: RunApp) -> None:
    app = run_app("pre-start")
    for name in ("modbus_master.json", "modbus_slave.json"):
        src = injections(app, "plc-cellar")[
            f"/opt/cherrymountain/openplc/conf/{name}"
        ].src
        want = ROOT / "plant" / "generated" / "ci" / "openplc" / "plc-cellar" / name
        assert Path(src).read_bytes() == want.read_bytes()
    zip_src = injections(app, "plc-cellar")[
        "/opt/cherrymountain/openplc/plc-cellar.zip"
    ].src
    assert zipfile.is_zipfile(zip_src)


def test_sim_federate_env(run_app: RunApp) -> None:
    app = run_app("pre-start")
    env = Path(
        injections(app, "sim-process")["/etc/default/cm-federate-cellar"].src
    ).read_text()
    assert "HELICS_BROKER_ADDRESS=172.16.10.1" in env
    assert "CM_HELICS_CONFIG=/etc/cherrymountain/helics.json" in env


def test_ci_has_no_ground_truth(run_app: RunApp) -> None:
    """The ci variant has no ground-truth OpenSearch (DESIGN.md 11)."""
    app = run_app("pre-start")
    for host in ("sim-ctl", "sim-process"):
        assert "/etc/cherrymountain/ground-truth.env" not in injections(app, host)


def test_mgmt_policy_hub_and_member(run_app: RunApp) -> None:
    app = run_app("pre-start")
    hub = Path(
        injections(app, "sim-ctl")["/etc/cherrymountain/nftables-mgmt.nft"].src
    ).read_text()
    member = Path(
        injections(app, "rio-cellar")["/etc/cherrymountain/nftables-mgmt.nft"].src
    ).read_text()
    assert "ip saddr @peers icmp type echo-request accept" in hub
    assert "ip daddr 172.16.10.1 icmp type echo-request accept" in member
    assert "elements = { 172.16.10.1, 172.16.10.2, 172.16.20.12 }" in member
    # The device name is a variable: the topology's name (mgmt) is not the
    # guest's device name, so the hook passes the real one (nft -D).
    assert "iifname != $mgmt_if accept" in member
    assert "oifname != $mgmt_if accept" in member
    assert '"mgmt"' not in member
    # The hook finds the device by the host's MGMT address, after phenix's
    # 3_interfaces-start.sh and before the helics app's 5-wait-broker.sh.
    rio = injections(app, "rio-cellar")
    assert "CM_MGMT_ADDRESS=172.16.20.12" in Path(rio["/etc/default/cm-mgmt"].src).read_text()
    assert "/etc/phenix/startup/4-cm-mgmt-nftables.sh" in rio
    hook = "4-cm-mgmt-nftables.sh"
    assert sorted(["3_interfaces-start.sh", hook, "5-wait-broker.sh"])[1] == hook


def test_mgmt_policy_can_be_disabled(
    run_app: RunApp, experiment: dict[str, Any]
) -> None:
    app_block(experiment)["metadata"]["mgmt"] = {"enabled": False}
    app = run_app("pre-start", exp=experiment)
    assert "/etc/cherrymountain/nftables-mgmt.nft" not in injections(app, "rio-cellar")


def test_lfs_pointer_zip_fails(
    run_app: RunApp, experiment: dict[str, Any], tmp_path: Path
) -> None:
    fake = tmp_path / "src"
    fake.mkdir()
    for part in ("plant", "tools", "fuxa"):
        (fake / part).symlink_to(ROOT / part)
    (fake / "phenix-injects" / "openplc").mkdir(parents=True)
    for sub in ("fuxa", "sim"):
        (fake / "phenix-injects" / sub).symlink_to(ROOT / "phenix-injects" / sub)
    for f in (ROOT / "phenix-injects" / "openplc").iterdir():
        if f.is_file() and f.suffix != ".zip":
            (fake / "phenix-injects" / "openplc" / f.name).symlink_to(f)
    (fake / "phenix-injects" / "openplc" / "plc-cellar.zip").write_text(
        "version https://git-lfs.github.com/spec/v1\n"
    )
    app_block(experiment)["metadata"]["sourceDir"] = str(fake)
    with pytest.raises(AppError, match="Git LFS pointer"):
        run_app("pre-start", exp=experiment)


def test_dry_run_prestart_renders_too(run_app: RunApp) -> None:
    app = run_app("pre-start", dryrun=True)
    assert (app.out_dir / "injections.json").is_file()


def test_bad_node_is_reported_before_rendering(
    run_app: RunApp, experiment: dict[str, Any]
) -> None:
    node(experiment, "sim-ctl")["network"]["interfaces"][0]["name"] = "eth9"
    with pytest.raises(AppError, match="app check failed"):
        run_app("pre-start", exp=experiment)
