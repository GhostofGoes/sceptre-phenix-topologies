"""fuxa/build_project.py: the full and lite projects, as FUXA 1.3.5 reads them.

The expectations come from FUXA's source at the commit the cm-fuxa image pins:
the Modbus driver alone applies ``divisor``; every driver applies a ``linear``
``scale`` (``server/runtime/devices/device-utils.js``); an alarm level raises
``timedelay`` seconds after its value enters ``min`` to ``max`` and stays
listed until acknowledged unless its ``ackmode`` is ``float``
(``server/runtime/alarms``).
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from plantgen.cli import DEFAULT_PLANT

ROOT: Path = DEFAULT_PLANT.parents[1]
GENERATED: Path = DEFAULT_PLANT.parent / "generated"


def _builder() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "cm_fuxa_build_project", ROOT / "fuxa" / "build_project.py"
    )
    assert spec is not None and spec.loader is not None
    module: ModuleType = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


BUILDER: ModuleType = _builder()


def project(variant: str, host: str) -> dict[str, Any]:
    return json.loads(BUILDER.render(BUILDER.build_project(variant, host)))


@pytest.fixture(scope="module")
def scada() -> dict[str, Any]:
    return project("full", "scada-fuxa")


@pytest.fixture(scope="module")
def bas() -> dict[str, Any]:
    return project("full", "bas-fuxa")


def device(doc: dict[str, Any], name: str) -> dict[str, Any]:
    return next(d for d in doc["devices"].values() if d["name"] == name)


def tag(doc: dict[str, Any], name: str) -> dict[str, Any]:
    return next(
        t for d in doc["devices"].values() for t in d["tags"].values() if t["name"] == name
    )


def fuxa_read(t: dict[str, Any], raw: float, modbus: bool) -> float:
    """The value FUXA shows for ``raw`` (modbus convertValue, then tagValueCompose)."""
    if modbus and t.get("divisor"):
        raw /= t["divisor"]
    s = t.get("scale")
    if s and s["mode"] == "linear":
        span: float = s["rawHigh"] - s["rawLow"]
        raw = (s["scaledHigh"] - s["scaledLow"]) * (raw - s["rawLow"]) / span + s["scaledLow"]
    return raw


@pytest.mark.parametrize(
    ("name", "plc", "raw", "shown"),
    [
        ("BH.HLT.TT01.PV", "plc-brewhouse", 1680, 168.0),  # OPC UA, x10
        ("BH.MLT.PDT01.PV", "plc-brewhouse", 120, 1.2),  # OPC UA, x100
        ("PKG.LINE.PACKML.SPEED_SP", "plc-packaging", 300, 30.0),  # S7, x10
        ("CEL.FV101.TT01.PV", "plc-cellar", 6600, 66.0),  # Modbus, x100
        ("UTL.CHL.TT01.PV", "plc-utilities", 280, 28.0),  # Modbus, x10
    ],
)
def test_every_driver_shows_engineering_values(
    scada: dict[str, Any], name: str, plc: str, raw: int, shown: float
) -> None:
    dev = device(scada, plc)
    t = tag(scada, name)
    modbus: bool = dev["type"] == "ModbusTCP"
    assert fuxa_read(t, raw, modbus) == pytest.approx(shown)
    # Never both: FUXA's Modbus driver would then scale twice.
    assert ("divisor" in t) != ("scale" in t)
    assert ("divisor" in t) == modbus


def test_unscaled_and_bit_tags_carry_no_scale(scada: dict[str, Any]) -> None:
    assert "scale" not in tag(scada, "PKG.LINE.PACKML.STATE")  # S7 word, x1
    assert "scale" not in tag(scada, "BH.BATCH.XC01.CMD")  # OPC UA word, x1
    assert "scale" not in tag(scada, "BH.ENTRY.HS01.ENTRY")


@pytest.mark.parametrize("host", ["scada-fuxa", "bas-fuxa"])
def test_critical_alarms_need_an_acknowledgement(host: str) -> None:
    doc = project("full", host)
    critical = [a["highhigh"] for a in doc["alarms"] if a["highhigh"]["enabled"]]
    assert critical, "the plant has critical alarms on both front ends"
    for level in critical:
        assert level["ackmode"] == "ackactive"
        assert level["bkcolor"] not in ("#FFFFFF", "")
    lowest = [a["info"] for a in doc["alarms"] if a["info"]["enabled"]]
    assert {level["ackmode"] for level in lowest} == {"float"}


def test_fuxa_evaluated_alarms_keep_their_on_delay(bas: dict[str, Any]) -> None:
    """A BAS alarm has no PLC bit, so FUXA must apply the rationalized delay."""
    plant = {
        a["name"]: a
        for a in json.loads((GENERATED / "full" / "alarms.json").read_text())["alarms"]
    }
    seen: int = 0
    for alarm in bas["alarms"]:
        spec = plant[alarm["name"]]
        level = next(alarm[k] for k in ("highhigh", "high", "low", "info") if alarm[k]["enabled"])
        assert spec.get("evaluator") == "fuxa"
        assert level["timedelay"] == round(spec["delay_s"])
        seen += level["timedelay"] > 0
    assert seen, "some BAS alarms are delayed"
    high = next(a for a in bas["alarms"] if a["name"] == "FAC.VENT.AT01.ALM_HIGH")
    assert (high["high"]["min"], high["high"]["timedelay"]) == (15000.0, 30)


def test_plc_alarm_bits_are_not_delayed_twice(scada: dict[str, Any]) -> None:
    shutoff = next(a for a in scada["alarms"] if a["name"] == "UTL.CO2.XS02.ALM_STATE")
    assert (shutoff["high"]["min"], shutoff["high"]["max"]) == (1, 1)
    assert shutoff["high"]["timedelay"] == 0


def inputs(view: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Tag id -> input gauge of a view."""
    return {
        g["property"]["variableId"]: g
        for g in view["items"].values()
        if g["type"] == "svg-ext-html_input"
    }


def view(doc: dict[str, Any], name: str) -> dict[str, Any]:
    return next(v for v in doc["hmi"]["views"] if v["name"] == name)


@pytest.mark.parametrize(
    ("view_name", "tag_name", "lo", "hi"),
    [
        ("Utilities points", "UTL.CO2.XC01.RESET", 0.0, 1.0),  # the CO2 shutoff reset
        ("Cellar points", "CEL.ENTRY.HS01.ENTRY", 0.0, 1.0),  # an entry permit
        ("Brewhouse points", "BH.BATCH.XC01.CMD", 0.0, 5.0),
        ("Brewhouse points", "BH.HLT.TT01.SP", 32.0, 212.0),
        ("Packaging points", "PKG.LINE.PACKML.CMD", 0.0, 9.0),
        ("Power points", "PWR.DMD.JY01.SP", None, None),
    ],
)
def test_an_operator_can_write_every_writable_tag(
    scada: dict[str, Any], view_name: str, tag_name: str, lo: float | None, hi: float | None
) -> None:
    gauge = inputs(view(scada, view_name))[tag(scada, tag_name)["id"]]
    options = gauge["property"]["options"]
    assert options["numeric"] is True
    if lo is not None:
        assert (options["min"], options["max"]) == (lo, hi)


@pytest.mark.parametrize("host", ["scada-fuxa", "bas-fuxa"])
def test_inputs_are_exactly_the_writable_tags(host: str) -> None:
    doc = project("full", host)
    tags = json.loads((GENERATED / "full" / "fuxa" / f"{host}-tags.json").read_text())
    writable: set[str] = {
        t["id"] for d in tags["devices"] for t in d["tags"] if t["access"] == "read-write"
    }
    generated = [v for v in doc["hmi"]["views"] if v["name"].endswith(" points")]
    bound: set[str] = set().union(*(inputs(v).keys() for v in generated))
    assert bound == writable
    # the process graphics put a control on a writable tag only
    for v in doc["hmi"]["views"]:
        if v["name"] != "Batch":
            assert set(inputs(v)) <= writable, v["name"]


def test_the_process_scada_cannot_write_bacnet(scada: dict[str, Any]) -> None:
    assert inputs(view(scada, "Cellar CO2")) == {}


@pytest.mark.parametrize("host", ["scada-fuxa", "bas-fuxa"])
def test_element_ids_are_unique_in_a_view(host: str) -> None:
    for v in project("full", host)["hmi"]["views"]:
        ids: list[str] = re.findall(r'\bid="([^"]+)"', v["svgcontent"])
        assert len(ids) == len(set(ids)), v["name"]
        assert set(v["items"]) <= set(ids)
