"""The hand-rendered ot-sim config equals what the phenix ot-sim app writes.

Run with the integration venv (``python -m pytest tests/integration``); the
comparison is skipped when ``phenix-apps`` is not installed.
"""

from __future__ import annotations

import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

import render_otsim  # noqa: E402

ROOT: Path = Path(__file__).resolve().parents[2]
OT_SIM_YAML: Path = ROOT / "plant" / "generated" / "ci" / "phenix" / "ot-sim.yaml"
NODES: list[render_otsim.Node] = [
    render_otsim.Node(hostname="rio-cellar", interface="IF0", address="10.20.12.10"),
    render_otsim.Node(hostname="sim-ctl", interface="mgmt", address="127.0.0.1"),
]


def canonical(element: ET.Element) -> tuple[Any, ...]:
    """Tag, attributes, text, and children, ignoring whitespace."""
    return (
        element.tag,
        tuple(sorted(element.attrib.items())),
        (element.text or "").strip(),
        [canonical(child) for child in element],
    )


def test_hand_render_equals_phenix_app() -> None:
    pytest.importorskip("phenix_apps.apps.otsim.app")
    app_block: dict[str, Any] = yaml.safe_load(OT_SIM_YAML.read_text())
    with tempfile.TemporaryDirectory() as tmp:
        written: list[Path] = render_otsim.render(app_block, NODES, Path(tmp))
        assert [p.name for p in written] == ["rio-cellar.xml"]
        from_app: ET.Element = ET.parse(written[0]).getroot()
    by_hand: ET.Element = ET.fromstring(render_otsim.hand_render(app_block, "rio-cellar", NODES))
    assert canonical(from_app) == canonical(by_hand)


def test_hand_render_register_numbering() -> None:
    """The e2e tag is input register 30002; the spunding setpoint is holding 40001."""
    app_block: dict[str, Any] = yaml.safe_load(OT_SIM_YAML.read_text())
    root: ET.Element = ET.fromstring(render_otsim.hand_render(app_block, "rio-cellar", NODES))
    regs: dict[str, tuple[str, str]] = {
        reg.findtext("tag", ""): (reg.get("type", ""), reg.findtext("address", ""))
        for reg in root.iterfind("modbus/register")
    }
    assert regs["fv101-side.temp_f"] == ("input", "30002")
    assert regs["fv101.spund_sp_psia"] == ("holding", "40001")
    assert regs["fv101-side.valve"] == ("coil", "1")
    assert root.findtext("modbus/endpoint") == "10.20.12.10:502"
    assert root.findtext("io/broker-endpoint") == "127.0.0.1"
