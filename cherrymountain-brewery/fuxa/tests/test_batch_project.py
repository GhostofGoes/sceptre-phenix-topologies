"""What fuxa/build_project.py puts into a project for the batch engine: the script,
the FUXA Server tags, the alarms, the Batch view, and the recipe's parameters."""

from __future__ import annotations

import json
import re
from typing import Any

import pytest

from fuxa_test_support import FUXA_DIR, ROOT, build_project, load_builder

BUILDER = load_builder()
ENGINE_TAGS = {t.name for t in BUILDER.BATCH_TAGS}


@pytest.fixture(scope="module", params=["full", "lite"])
def scada(request: pytest.FixtureRequest) -> dict[str, Any]:
    return build_project(request.param)


def all_tags(project: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {t["id"]: t for d in project["devices"].values() for t in d.get("tags", {}).values()}


def test_scada_fuxa_gets_the_engine_script(scada: dict[str, Any]) -> None:
    (script,) = scada["scripts"]
    assert script["name"] == "cmBatchEngine"
    assert re.fullmatch(r"[A-Za-z_$][\w$]*", script["name"])  # FUXA makes it a function name
    assert script["mode"] == "SERVER" and script["sync"] is False and script["parameters"] == []
    # FUXA's scheduler (server/runtime/scripts/index.js) runs `interval` scripts every N s
    assert script["scheduling"] == {"mode": "interval", "interval": 1}
    assert script["id"].startswith("s_")
    assert "__CFG__" not in script["code"] and "const CFG = {" in script["code"]
    assert script["code"].count("const CFG") == 1


def test_only_scada_fuxa_with_the_brewhouse_gets_it() -> None:
    assert build_project("full", "bas-fuxa")["scripts"] == []
    assert build_project("lite", "bas-fuxa")["scripts"] == []
    ci = build_project("ci")
    assert ci["scripts"] == []
    assert {d["name"] for d in ci["devices"].values()} == {"plc-cellar"}


def test_the_script_config_names_only_tags_the_project_has(scada: dict[str, Any]) -> None:
    config = json.loads(re.search(r"const CFG = (\{.*?\n\});", scada["scripts"][0]["code"], re.S).group(1))
    names = {t["name"] for t in all_tags(scada).values()}
    assert set(config["names"].values()) <= names
    assert len(set(config["names"].values())) == len(config["names"])  # one logical name each
    # the brewhouse and cellar tags it reads and writes are on the devices FUXA polls
    by_device = {d["name"]: {t["name"] for t in d["tags"].values()} for d in scada["devices"].values()}
    assert "BH.BATCH.XS01.STATE" in by_device["plc-brewhouse"]
    assert {"CEL.FV101.DT01.PV", "CEL.ENTRY.XS01.LOCKED", "CEL.ENTRY.XS11.CIP_OK"} <= by_device["plc-cellar"]


def test_the_engine_tags_are_on_the_fuxa_server_device(scada: dict[str, Any]) -> None:
    server = scada["devices"]["0"]
    # server/runtime/devices/fuxaserver/index.js: type FuxaServer, id 0, tags number or string
    assert (server["id"], server["name"], server["type"]) == ("0", "FUXA Server", "FuxaServer")
    assert scada["server"]["id"] == "0"  # the top-level key stays, as the earlier projects had it
    tags = server["tags"]
    assert {t["name"] for t in tags.values()} == ENGINE_TAGS
    assert all(k == t["id"] for k, t in tags.items())
    assert {t["type"] for t in tags.values()} == {"number", "string"}
    ids = [t["id"] for d in scada["devices"].values() for t in d["tags"].values()]
    assert len(ids) == len(set(ids)), "tag ids are unique across the project"
    by_name = {t["name"]: t for t in tags.values()}
    # the state survives a FUXA restart (FUXA stores restored tags as current values);
    # the operator's command does not (a stale Abort must never run again)
    for name in ("BATCH.STATE", "BATCH.QUEUE", "BATCH.REQUESTS_HANDLED", "BATCH.NUMBER", "BATCH.FV"):
        assert by_name[name]["daq"]["restored"] is True
    assert by_name["BATCH.CMD"]["daq"] == {"enabled": False, "changed": False, "interval": 60, "restored": False}
    # FUXA's influx store writes a float field: a string must not go to the DAQ store
    assert all(not t["daq"]["enabled"] and not t["daq"]["restored"] for t in tags.values() if t["type"] == "string")
    # what the historian shows of a batch
    assert {"BATCH.STATE", "BATCH.PHASE", "BATCH.QUEUE", "BATCH.FV", "BATCH.ALM_TIMEOUT"} <= {
        t["name"] for t in tags.values() if t["daq"]["enabled"]
    }


def test_engine_tag_ids_are_stable() -> None:
    first = {t["name"]: t["id"] for t in build_project("full")["devices"]["0"]["tags"].values()}
    second = {t["name"]: t["id"] for t in build_project("lite")["devices"]["0"]["tags"].values()}
    assert first == second and all(i.startswith("t_") for i in first.values())


def test_each_engine_alarm_watches_its_tag(scada: dict[str, Any]) -> None:
    by_id = all_tags(scada)
    alarms = {a["name"]: a for a in scada["alarms"] if a["name"].startswith("BATCH.")}
    assert set(alarms) == {f"BATCH.ALM_{k}" for k in ("TIMEOUT", "PLC", "ENGINE", "NOFV", "REFUSED")}
    for name, alarm in alarms.items():
        assert by_id[alarm["property"]["variableId"]]["name"] == name
        active = [lvl for lvl in ("highhigh", "high", "low", "info") if alarm[lvl]["enabled"]]
        assert len(active) == 1
        sub = alarm[active[0]]
        assert (sub["min"], sub["max"]) == (1, 1) and sub["group"] == "Batch" and sub["text"]
    # ISA-18.2: what holds a batch is High, wort waiting is Medium (FUXA low), a refusal Low (info)
    assert alarms["BATCH.ALM_TIMEOUT"]["high"]["enabled"] and alarms["BATCH.ALM_PLC"]["high"]["enabled"]
    assert alarms["BATCH.ALM_NOFV"]["low"]["enabled"] and alarms["BATCH.ALM_REFUSED"]["info"]["enabled"]


def test_the_batch_view_has_the_operator_controls(scada: dict[str, Any]) -> None:
    views = {v["name"]: v for v in scada["hmi"]["views"]}
    assert "Batch" in views
    nav = [i["text"] for i in scada["hmi"]["layout"]["navigation"]["items"]]
    assert "Batch" in nav and len(set(nav)) == len(nav)
    view = views["Batch"]
    by_id = all_tags(scada)
    items = view["items"].values()
    buttons = {i["name"]: i for i in items if i["type"] == "svg-ext-html_button"}
    assert set(buttons) == {"Start", "Hold", "Restart", "Abort", "Reset"}
    cmd = next(t["id"] for t in by_id.values() if t["name"] == "BATCH.CMD")
    for code, name in enumerate(("Start", "Hold", "Restart", "Abort", "Reset"), start=1):
        prop = buttons[name]["property"]
        assert prop["variableId"] == cmd
        # the editor's "set" button action: one click writes the command code
        assert prop["buttonAction"] == {"mode": "set", "setValue": str(code)}
    # every gauge is bound to a tag that exists in the project, and every id is in the SVG
    for item in view["items"].values():
        assert item["property"]["variableId"] in by_id, item["name"]
        assert f'id="{item["id"]}"' in view["svgcontent"]
    shown = {by_id[i["property"]["variableId"]]["name"] for i in view["items"].values()}
    assert {"BATCH.ID", "BATCH.STATE_TEXT", "BATCH.PHASE_NAME", "BATCH.STATUS", "BATCH.FV_NAME", "BATCH.QUEUE",
            "BATCH.ALM_TIMEOUT", "BATCH.PHASE"} <= shown
    # one lamp per phase, lit while BATCH.PHASE holds its number
    lamps = [i for i in view["items"].values() if i["name"].startswith("BATCH.PHASE=")]
    assert sorted(int(i["name"].split("=")[1]) for i in lamps) == list(range(1, 11))
    assert "lb" in view["svgcontent"] and " F" in view["svgcontent"]  # US customary units on the HMI


def test_other_hosts_get_no_batch_view() -> None:
    for variant, host in (("full", "bas-fuxa"), ("lite", "bas-fuxa"), ("ci", "scada-fuxa")):
        project = build_project(variant, host)
        assert all(v["name"] != "Batch" for v in project["hmi"]["views"])
        assert "0" not in project["devices"]


def test_the_recipe_gives_the_phase_parameters() -> None:
    recipe = BUILDER.load_batch_recipe()
    assert recipe.name == "Fire of the Phenix"
    assert recipe.grain_kg == pytest.approx(503.0, abs=0.1)
    assert [(s.name, s.temp_f, s.minutes) for s in recipe.mash] == [
        ("Saccharification", 152.0, 60), ("Mash out", 168.0, 10)]
    assert (recipe.boil_minutes, recipe.whirlpool_minutes, recipe.whirlpool_temp_f) == (60, 20, 180.0)
    assert recipe.pitch_temp_f == 64.0
    assert recipe.sparge_temp_f == 168.0
    # Palmer: 0.41 / (1,310 L / 503 kg) x (66.7 C - 18.3 C) + 66.7 C, plus 1 K, is 167.4 F;
    # the setpoint is a whole degree (FUXA's OPC UA driver integer-parses a write)
    assert recipe.strike_temp_f == 167.0
    seq = BUILDER.batch_sequence(recipe)
    assert [s.n for s in seq] == [1, 2, 3, 4, 4, 5, 6, 7, 8, 9, 10]
    assert [s.param for s in seq] == [503, 0, 0, 60, 10, 0, 0, 60, 20, 0, 0]
    assert [s.hlt for s in seq][:4] == [167, 167, 167, 168]
    assert [s.mlt for s in seq][:5] == [152, 152, 152, 152, 168]
    assert {s.ko for s in seq} == {64}
    # an hour of heat-up allowance plus the parameter's minutes for a mash step and the boil
    assert [s.timeout_s for s in seq if s.n in (4, 7, 8)] == [7200, 4200, 7200, 2100]
    # every setpoint is a whole degree inside the PLC's limits (32 to 205, 180, and 100 F,
    # plc-brewhouse.st) and every parameter fits its register
    assert all(0 <= s.param <= 65535 for s in seq)
    assert all(32 <= s.hlt <= 205 and 32 <= s.mlt <= 180 and 32 <= s.ko <= 100 for s in seq)


def test_a_recipe_the_builder_cannot_read_is_an_error(tmp_path: Any) -> None:
    bad = tmp_path / "recipe.json"
    bad.write_text("{}", encoding="utf-8")
    with pytest.raises(SystemExit, match="cannot read the recipe"):
        BUILDER.load_batch_recipe(bad)


def test_the_engine_source_has_its_config_marker_once() -> None:
    source = (FUXA_DIR / "batch_engine.js").read_text(encoding="utf-8")
    assert source.count("const CFG = __CFG__;") == 1
    assert "\t" not in source


def test_the_recipe_file_is_the_one_in_the_repository() -> None:
    assert BUILDER.RECIPE_PATH == ROOT / "recipes" / "fire-of-the-phenix.json"
    assert BUILDER.RECIPE_PATH.is_file()
