"""The process graphics (fuxa/hmi_kit.py, fuxa/hmi_views.py) as they are in the
generated projects: which screens a host gets, that each fits an HMI station,
and that every live element is bound to something the project has.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest
from fuxa_test_support import ROOT, build_project, load_builder

GRAPHICS: dict[tuple[str, str], list[str]] = {
    ("full", "scada-fuxa"): ["Overview", "Brewhouse", "Cellar", "Packaging", "Utilities", "Power", "Cellar CO2"],
    ("lite", "scada-fuxa"): ["Overview", "Brewhouse", "Cellar", "Packaging", "Utilities", "Power", "Cellar CO2"],
    ("ci", "scada-fuxa"): ["Cellar"],
    ("full", "bas-fuxa"): ["Building"],
    ("lite", "bas-fuxa"): ["Building"],
}
PROJECTS: list[tuple[str, str]] = list(GRAPHICS)


@pytest.fixture(scope="module", params=PROJECTS, ids=lambda p: f"{p[0]}-{p[1]}")
def built(request: pytest.FixtureRequest) -> tuple[str, str, dict[str, Any]]:
    variant, host = request.param
    return variant, host, build_project(variant, host)


def graphics(variant: str, host: str, project: dict[str, Any]) -> list[dict[str, Any]]:
    by_name: dict[str, dict[str, Any]] = {v["name"]: v for v in project["hmi"]["views"]}
    return [by_name[name] for name in GRAPHICS[(variant, host)]]


def plant_tags(variant: str, host: str) -> dict[str, dict[str, Any]]:
    """Tag id -> the plantgen record of a host's tag."""
    path = ROOT / "plant" / "generated" / variant / "fuxa" / f"{host}-tags.json"
    return {t["id"]: t for d in json.loads(path.read_text())["devices"] for t in d["tags"]}


def alarms(variant: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = json.loads(
        (ROOT / "plant" / "generated" / variant / "alarms.json").read_text()
    )["alarms"]
    return records


def bound_tags(item: dict[str, Any]) -> set[str]:
    prop: dict[str, Any] = item["property"]
    ids: set[str] = {a["variableId"] for a in prop.get("actions", [])}
    if prop.get("variableId"):
        ids.add(prop["variableId"])
    return ids


def test_a_host_gets_its_graphics_first_and_starts_on_the_first(built: tuple[str, str, dict[str, Any]]) -> None:
    variant, host, project = built
    names: list[str] = [v["name"] for v in project["hmi"]["views"]]
    wanted: list[str] = GRAPHICS[(variant, host)]
    assert [n for n in names if n in wanted] == wanted
    assert names[0] == wanted[0]
    assert project["hmi"]["layout"]["start"] == project["hmi"]["views"][0]["id"]
    # every other view is the Batch view or a generated table of a controller's points
    assert all(n in wanted or n == "Batch" or n.endswith(" points") for n in names)


def test_the_kiosks_views_are_graphics() -> None:
    """hmi-brewhouse and hmi-packaging open a view by its name."""
    names: list[str] = [v["name"] for v in build_project("full")["hmi"]["views"]]
    assert "Brewhouse" in names and "Packaging" in names
    assert "Brewhouse points" in names and "Packaging points" in names


def test_a_graphic_fits_an_hmi_station(built: tuple[str, str, dict[str, Any]]) -> None:
    """1440x900 less the 200 px menu and the header."""
    for view in graphics(*built):
        assert view["profile"]["width"] <= 1240, view["name"]
        assert view["profile"]["height"] <= 844, view["name"]


def test_every_item_is_an_element_and_no_id_is_used_twice(built: tuple[str, str, dict[str, Any]]) -> None:
    for view in graphics(*built):
        ids: list[str] = re.findall(r'\bid="([^"]+)"', view["svgcontent"])
        assert len(ids) == len(set(ids)), view["name"]
        assert set(view["items"]) <= set(ids), view["name"]
        for clip in re.findall(r'clip-path="url\(#([^)]+)\)"', view["svgcontent"]):
            assert clip in ids, view["name"]


def test_every_live_element_is_bound_to_a_tag_of_the_project(built: tuple[str, str, dict[str, Any]]) -> None:
    _variant, _host, project = built
    known: set[str] = {t for d in project["devices"].values() for t in d["tags"]}
    for view in graphics(*built):
        for item in view["items"].values():
            if item["type"] == "svg-ext-html_chart":
                continue
            tags: set[str] = bound_tags(item)
            events: list[dict[str, Any]] = item["property"].get("events", [])
            assert tags or events, f'{view["name"]}: {item["name"]} is bound to nothing'
            assert tags <= known, f'{view["name"]}: {item["name"]}'


def test_a_control_is_only_on_a_tag_an_operator_may_write(built: tuple[str, str, dict[str, Any]]) -> None:
    variant, host, _project = built
    tags: dict[str, dict[str, Any]] = plant_tags(variant, host)
    for view in graphics(*built):
        for item in view["items"].values():
            if item["type"] in ("svg-ext-html_input", "svg-ext-html_button"):
                tag: dict[str, Any] = tags[item["property"]["variableId"]]
                assert tag["access"] == "read-write", f'{view["name"]}: {tag["name"]}'


def test_the_kit_refuses_a_control_on_a_read_only_tag() -> None:
    builder = load_builder()
    import hmi_kit  # the builder put fuxa/ on the path

    tag = builder.PlantTag(id="t_x", name="X.Y.PV", label="x", type="Int16", access="read")
    canvas = hmi_kit.Canvas("Test", {"X.Y.PV": tag}, 100, 100)
    with pytest.raises(SystemExit, match="not read-write"):
        canvas.entry(0, 0, "X.Y.PV", 0, 1)
    with pytest.raises(SystemExit, match="not read-write"):
        canvas.button(0, 0, 10, 10, "Go", "X.Y.PV", 1)


def test_the_process_scada_operates_nothing_in_the_building() -> None:
    """scada-fuxa reads the cellar's CO2 from the building controllers; only
    bas-fuxa commands them."""
    view = next(v for v in build_project("full")["hmi"]["views"] if v["name"] == "Cellar CO2")
    kinds: set[str] = {i["type"] for i in view["items"].values()}
    assert not kinds & {"svg-ext-html_input", "svg-ext-html_button"}


CO2_SCREENS: list[tuple[str, str]] = [("scada-fuxa", "Cellar CO2"), ("bas-fuxa", "Building")]


@pytest.mark.parametrize(("host", "name"), CO2_SCREENS)
def test_a_co2_sensor_bar_is_cut_at_the_three_tiers(host: str, name: str) -> None:
    """A sensor's bar is three fills side by side, one for each tier's span, and
    the sensor's own alarm limits are two of the cuts."""
    view = next(v for v in build_project("full", host)["hmi"]["views"] if v["name"] == name)
    limits: dict[str, set[float]] = {}
    for alarm in alarms("full"):
        if alarm["source"].startswith("FAC.VENT.AT0") and alarm.get("limit") is not None:
            limits.setdefault(alarm["source"], set()).add(alarm["limit"])
    for n in range(1, 8):
        tag: str = f"FAC.VENT.AT0{n}.PV"
        parts: list[dict[str, Any]] = sorted(
            (a["options"] for i in view["items"].values() if i["name"] == f"{tag} fill"
             for a in i["property"]["actions"]),
            key=lambda o: o["valueMin"],
        )
        spans: list[tuple[float, float]] = [(o["valueMin"], o["valueMax"]) for o in parts]
        assert spans == [(0, 5000), (5000, 15000), (15000, 30000)], tag
        for before, after in zip(parts, parts[1:], strict=False):
            assert after["positionMin"] == pytest.approx(before["positionMax"]), tag  # no gap, no overlap
        assert limits[tag] <= {hi for _lo, hi in spans}, tag


def test_each_co2_tier_is_shown_with_what_it_does() -> None:
    """On Cellar CO2 a tier's lamp stands over its effects, each read from the
    controller that carries it out, and the plan shows which vessel vents."""
    view = next(v for v in build_project("full")["hmi"]["views"] if v["name"] == "Cellar CO2")
    names: set[str] = {i["name"] for i in view["items"].values()}
    tiers: set[str] = {f"FAC.VENT.AY01.TIER{n}" for n in (1, 2, 3)}
    effects: set[str] = {
        "FAC.VENT.FAN01.PV", "FAC.VENT.FAN02.PV",  # tier 1: the purge fans' current switches
        "CEL.SYS.XS01.CO2_INHIBIT",  # tier 2: plc-cellar's interlock
        "FAC.VENT.XA01.CMD", "UTL.CO2.XS02.SHUTOFF", "UTL.CO2.ZSO01.PV valve",  # tier 3
    }
    relays: set[str] = {"CEL.SYS.XS01.CO2_OK", "FAC.VENT.XS01.SHUTOFF_OK", "UTL.CO2.XS01.CO2_OK"}
    sources: set[str] = {f"CEL.FV10{n}.ZSO01.PV ring" for n in (1, 2, 3, 4)}
    assert tiers | effects | relays | sources <= names
    assert {f"CEL.PSV.ZSH0{n}.ALM_STATE" for n in range(1, 7)} <= names  # a relief valve that lifted


def test_the_building_hmi_commands_the_purge_fans_and_the_horns() -> None:
    view = next(v for v in build_project("full", "bas-fuxa")["hmi"]["views"] if v["name"] == "Building")
    tags: dict[str, dict[str, Any]] = plant_tags("full", "bas-fuxa")
    commanded: set[str] = {
        tags[i["property"]["variableId"]]["name"] for i in view["items"].values() if i["type"] == "svg-ext-html_button"
    }
    assert commanded == {"FAC.VENT.FAN01.CMD", "FAC.VENT.FAN02.CMD", "FAC.VENT.XA01.CMD"}


def test_a_link_opens_a_view_that_exists(built: tuple[str, str, dict[str, Any]]) -> None:
    _variant, _host, project = built
    view_ids: set[str] = {v["id"] for v in project["hmi"]["views"]}
    links: int = 0
    for view in graphics(*built):
        for item in view["items"].values():
            for event in item["property"].get("events", []):
                assert event["action"] == "onpage"
                assert event["actparam"] in view_ids, f'{view["name"]}: {item["name"]}'
                links += 1
    if "Overview" in GRAPHICS[(built[0], built[1])]:
        assert links >= 6  # the overview's panels


def test_a_trend_has_its_chart_and_its_tags_are_stored(built: tuple[str, str, dict[str, Any]]) -> None:
    _variant, _host, project = built
    charts: dict[str, dict[str, Any]] = {c["id"]: c for c in project["charts"]}
    stored: set[str] = {
        t["id"] for d in project["devices"].values() for t in d["tags"].values() if t["daq"]["enabled"]
    }
    trends: int = 0
    for view in graphics(*built):
        for item in view["items"].values():
            if item["type"] != "svg-ext-html_chart":
                continue
            trends += 1
            chart: dict[str, Any] = charts[item["property"]["id"]]
            assert chart["lines"], item["name"]
            for line in chart["lines"]:
                assert line["id"] in stored, f'{view["name"]}: {line["name"]} is not stored, so its trend starts empty'
    assert trends >= 1 or built[1] == "bas-fuxa"
    assert len(charts) == trends


def test_every_alarm_has_a_flag_on_a_graphic(built: tuple[str, str, dict[str, Any]]) -> None:
    """An alarm in the list can be found on the plant's picture."""
    variant, _host, project = built
    known: set[str] = {t for d in project["devices"].values() for t in d["tags"]}
    mine: dict[str, dict[str, Any]] = {a["name"]: a for a in alarms(variant) if a["fuxa_tag_id"] in known}
    flagged: dict[str, dict[str, Any]] = {}
    for view in graphics(*built):
        for item in view["items"].values():
            if item["type"] == "svg-ext-shapes-g" and item.get("hide"):
                flagged[item["name"]] = item
    assert set(mine) <= set(flagged), sorted(set(mine) - set(flagged))
    for name, item in flagged.items():
        show = next(a for a in item["property"]["actions"] if a["type"] == "show")
        alarm: dict[str, Any] = mine[name]
        assert show["variableId"] == alarm["fuxa_tag_id"]
        if alarm.get("evaluator") != "fuxa":
            assert (show["range"]["min"], show["range"]["max"]) == (1, 1)  # a PLC alarm bit
        elif alarm["kind"] in ("high", "highhigh"):
            assert show["range"]["min"] == alarm["limit"]


def test_a_hidden_element_has_a_way_to_show(built: tuple[str, str, dict[str, Any]]) -> None:
    for view in graphics(*built):
        for item in view["items"].values():
            if item.get("hide"):
                kinds: list[str] = [a["type"] for a in item["property"]["actions"]]
                assert "show" in kinds and "hide" in kinds, item["name"]


def test_alarm_colors_are_only_in_alarm_flags(built: tuple[str, str, dict[str, Any]]) -> None:
    """ISA-101: a color that means "alarm" means nothing else on the screen."""
    reserved: set[str] = {c.lower() for c in load_builder().BRAND.reserved_for_alarms.values()}
    for view in graphics(*built):
        flags: set[str] = {k for k, i in view["items"].items() if i["type"] == "svg-ext-shapes-g" and i.get("hide")}
        svg: str = view["svgcontent"]
        for gid in flags:
            svg = re.sub(rf'<g id="{re.escape(gid)}">.*?</g>', "", svg, count=1, flags=re.S)
        rest: str = (svg + json.dumps(view["items"])).lower()
        assert not {c for c in reserved if c in rest}, view["name"]


def test_a_fill_moves_through_its_whole_span(built: tuple[str, str, dict[str, Any]]) -> None:
    """A level or a pointer goes from one end of its travel to the other as its
    tag goes from the low end of its scale to the high end."""
    moves: int = 0
    for view in graphics(*built):
        for item in view["items"].values():
            for action in item["property"].get("actions", []):
                if action["type"] != "moveByTags":
                    continue
                moves += 1
                options: dict[str, Any] = action["options"]
                assert options["valueMin"] < options["valueMax"], item["name"]
                assert options["positionMin"] != options["positionMax"], item["name"]
                assert options["axis"] in ("x", "y")
    assert moves >= 4


def test_the_ci_cellar_needs_only_the_fermenters() -> None:
    """The ci plant has plc-cellar's fermenters and nothing else: the Cellar
    still draws, without the bright tanks and the profile."""
    view = next(v for v in build_project("ci")["hmi"]["views"] if v["name"] == "Cellar")
    names: set[str] = {i["name"] for i in view["items"].values()}
    assert "CEL.FV104.TT01.PV" in names and "CEL.FV101.TT01.SP" in names
    assert not any(n.startswith(("CEL.BBT", "CEL.PROFILE", "UTL.", "BH.")) for n in names)


def test_input_boxes_get_the_layout_setting_fuxa_reads(built: tuple[str, str, dict[str, Any]]) -> None:
    """FUXA 1.3.6 reads ``layout.inputdialog`` with no default while it sets up
    an input box (fuxa-view.component.ts, onBindHtmlEvent)."""
    assert built[2]["hmi"]["layout"]["inputdialog"] == "false"
