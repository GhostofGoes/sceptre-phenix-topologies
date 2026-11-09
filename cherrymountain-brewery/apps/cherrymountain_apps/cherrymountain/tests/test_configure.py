"""configure: metadata validation, plant.yaml against the topology, the
helics/federate annotation, and the sohProfiles."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from phenix_apps.common.logger import logger

from cherrymountain_apps.cherrymountain.tests.conftest import RunApp, app_block, node


@pytest.fixture
def warnings() -> Iterator[list[str]]:
    lines: list[str] = []
    sink: int = logger.add(
        lambda m: lines.append(str(m)), level="WARNING", format="{message}"
    )
    yield lines
    logger.remove(sink)


def host_md(app: Any, hostname: str) -> Any:
    return next(h for h in app.app.hosts if h.hostname == hostname).metadata


def test_configure_ci_is_clean(run_app: RunApp, warnings: list[str]) -> None:
    app = run_app("configure")
    assert warnings == []
    assert set(app.hosts_md) == {
        "sim-ctl",
        "sim-process",
        "rio-cellar",
        "plc-cellar",
        "scada-fuxa",
    }


def test_configure_annotates_the_federate_node(run_app: RunApp) -> None:
    app = run_app("configure")
    annotation = app.extract_node("sim-process").annotations["helics/federate"]
    assert annotation.to_list() == [{"broker": "sim-ctl|mgmt", "fed-count": 1}]
    # sim-ctl runs no federate in ci, so it gets no annotation.
    assert "helics/federate" not in (app.extract_node("sim-ctl").annotations or {})


def test_configure_writes_soh_profiles(run_app: RunApp) -> None:
    app = run_app("configure")
    plc = host_md(app, "plc-cellar").sohProfile
    assert plc.listeners == [":8443", ":502"]
    names = [t.name for t in plc.customTests]
    assert names == ["openplc-program-running", "modbus-read-rio-cellar"]
    modbus = plc.customTests[1]
    assert "10.20.12.10" in modbus.testScript
    assert modbus.testStdout == "modbus-ok"

    assert host_md(app, "scada-fuxa").sohProfile.listeners == [":1881"]
    assert host_md(app, "rio-cellar").sohProfile.processes == [
        "ot-sim-cpu-module",
        "ot-sim-modbus-module",
    ]
    sim_ctl = host_md(app, "sim-ctl").sohProfile
    assert sim_ctl.processes == ["helics_broker"]
    # rio-cellar and sim-process each run a federate.
    assert "want=2" in sim_ctl.customTests[0].testScript


def test_soh_profiles_hold_no_secret(run_app: RunApp) -> None:
    app = run_app("configure")
    stored = (app.out_dir / "secrets.json").read_text()
    profiles = str([h.metadata.get("sohProfile") for h in app.app.hosts])
    for value in __import__("json").loads(stored).values():
        assert value not in profiles


def test_reconfigure_accepts_its_own_soh_profiles(
    run_app: RunApp, warnings: list[str]
) -> None:
    first = run_app("configure")
    run_app("configure", exp=first.experiment.to_dict())
    assert warnings == []


def test_unknown_keys_warn_with_did_you_mean(
    run_app: RunApp, experiment: dict[str, Any], warnings: list[str]
) -> None:
    block = app_block(experiment)
    block["metadata"]["simCtrl"] = {"hostname": "sim-ctl"}
    block["hosts"][3]["metadata"]["nortbound"] = "opcua"
    run_app("configure", exp=experiment)
    (text,) = warnings
    assert "metadata.simCtrl: unknown key, ignored" in text
    assert "did you mean 'simCtl'?" in text
    assert "plc-cellar" in text
    assert "metadata.nortbound: unknown key, ignored" in text
    assert "did you mean 'northbound'?" in text


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        (lambda b: b["metadata"].pop("variant"), "app  metadata.variant: is required"),
        (lambda b: b["metadata"].update({"variant": "nope"}), "unknown variant"),
        (
            lambda b: b["hosts"][3]["metadata"].update({"type": "openplcc"}),
            "metadata.type: not a known host type",
        ),
        (
            lambda b: b["hosts"][3]["metadata"].update({"northbound": "bacnet"}),
            "metadata.northbound: Input should be",
        ),
        (
            lambda b: b["hosts"][3]["metadata"].update({"restPort": "8443"}),
            "metadata.restPort: Input should be a valid integer",
        ),
        (
            lambda b: b["hosts"].append(
                {"hostname": "plc-ghost", "metadata": {"type": "openplc"}}
            ),
            "missing from the topology",
        ),
        (
            lambda b: b["metadata"].update({"mgmt": {"enabled": True, "ports": [1]}}),
            "metadata.mgmt.ports: unknown key",
        ),
        (
            lambda b: b["metadata"].update({"simCtl": {"hostname": "sim-ctrl"}}),
            "did you mean 'sim-ctl'?",
        ),
    ],
    ids=[
        "missing-variant",
        "bad-variant",
        "bad-type",
        "bad-literal",
        "strict-int",
        "ghost-host",
        "closed-shape",
        "bad-reference",
    ],
)
def test_metadata_errors_fail_configure(
    run_app: RunApp, experiment: dict[str, Any], change: Any, expected: str
) -> None:
    from phenix_apps.common.error import AppError

    change(app_block(experiment))
    lines: list[str] = []
    sink: int = logger.add(
        lambda m: lines.append(str(m)), level="ERROR", format="{message}"
    )
    try:
        with pytest.raises(AppError, match="cherrymountain app check failed"):
            run_app("configure", exp=experiment)
    finally:
        logger.remove(sink)
    assert expected in "\n".join(lines)


def test_errors_never_echo_values(run_app: RunApp, experiment: dict[str, Any]) -> None:
    from phenix_apps.common.error import AppError

    block = app_block(experiment)
    block["metadata"]["secrets"] = {
        "openplcPassword": 12345678,
        "fuxaPassword": "hunter2-very-secret",
    }
    block["metadata"]["sourceDir"] = block["metadata"]["sourceDir"] + "/../x y"
    lines: list[str] = []
    sink: int = logger.add(
        lambda m: lines.append(str(m)), level="DEBUG", format="{message}"
    )
    try:
        with pytest.raises(AppError):
            run_app("configure", exp=experiment)
    finally:
        logger.remove(sink)
    text = "\n".join(lines)
    assert "metadata.secrets.openplcPassword" in text
    assert "12345678" not in text
    assert "hunter2" not in text
    assert "x y" not in text


def test_topology_address_must_match_plant(
    run_app: RunApp, experiment: dict[str, Any]
) -> None:
    from phenix_apps.common.error import AppError

    plc = node(experiment, "plc-cellar")
    plc["network"]["interfaces"][1]["address"] = "10.20.12.2"
    lines: list[str] = []
    sink: int = logger.add(
        lambda m: lines.append(str(m)), level="ERROR", format="{message}"
    )
    try:
        with pytest.raises(AppError):
            run_app("configure", exp=experiment)
    finally:
        logger.remove(sink)
    text = "\n".join(lines)
    assert (
        "network.interfaces[IF1].address: differs from plant.yaml hosts.plc-cellar.interfaces.IF1.ip"
        in text
    )
    assert "10.20.12.2" not in text


def test_missing_plant_host_and_interface(
    run_app: RunApp, experiment: dict[str, Any]
) -> None:
    from phenix_apps.common.error import AppError

    experiment["spec"]["topology"]["nodes"] = [
        n
        for n in experiment["spec"]["topology"]["nodes"]
        if n["general"]["hostname"] != "rio-cellar"
    ]
    node(experiment, "scada-fuxa")["network"]["interfaces"][0]["name"] = "eth0"
    app_block(experiment)["hosts"] = [
        h for h in app_block(experiment)["hosts"] if h["hostname"] != "rio-cellar"
    ]
    lines: list[str] = []
    sink: int = logger.add(
        lambda m: lines.append(str(m)), level="ERROR", format="{message}"
    )
    try:
        with pytest.raises(AppError):
            run_app("configure", exp=experiment)
    finally:
        logger.remove(sink)
    text = "\n".join(lines)
    assert "rio host is missing from the topology" in text
    assert "network.interfaces[IF0]: interface is missing" in text


def test_unprovisioned_plant_host_warns(
    run_app: RunApp, experiment: dict[str, Any], warnings: list[str]
) -> None:
    app_block(experiment)["hosts"] = [
        h for h in app_block(experiment)["hosts"] if h["hostname"] != "plc-cellar"
    ]
    run_app("configure", exp=experiment)
    (text,) = warnings
    assert "plc-cellar" in text
    assert "the app will not provision it" in text
