"""The FUXA projects of the fleet's sites (``fuxa/fleet/<code>/<host>-project.json``).

A site is the plant with parts left out and with its own hostnames
(``plant/sites.yaml``). The builder gets a site's tag lists from
``plant/generated/fleet/<code>/`` and knows no hostname, so these tests hold
it to two things: a project refers to nothing that it does not define, and a
site's project names the site's own hosts.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

import pytest

from fuxa_test_support import FUXA_DIR, build_project, load_builder

BUILDER = load_builder()
SITES: tuple[str, ...] = ("fr", "ep", "cs", "fc")
#: Every project the builder writes: (variant or site code, host).
JOBS: list[tuple[str, str]] = [
    (name, host) for name in ("ci", "full", "lite", *SITES) for host in BUILDER.fuxa_hosts(name)
]
SITE_JOBS: list[tuple[str, str]] = [job for job in JOBS if job[0] in SITES]
#: An id as the builder makes one: a prefix for the kind, then a UUID.
ID: str = r"_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"


@pytest.fixture(scope="module")
def projects() -> dict[tuple[str, str], dict[str, Any]]:
    return {job: build_project(*job) for job in JOBS}


def view(project: dict[str, Any], name: str) -> dict[str, Any]:
    return next(v for v in project["hmi"]["views"] if v["name"] == name)


def tag_names(project: dict[str, Any]) -> set[str]:
    return {t["name"] for d in project["devices"].values() for t in d["tags"].values()}


def engine_config(project: dict[str, Any]) -> dict[str, Any]:
    """The data the batch engine script is built with."""
    found = re.search(r"const CFG = (\{.*?\n\});", project["scripts"][0]["code"], re.S)
    assert found is not None
    config: dict[str, Any] = json.loads(found.group(1))
    return config


def dangling(project: dict[str, Any]) -> list[str]:
    """What a project refers to and does not define.

    A view item, an alarm, a chart line, a link, the menu, and the start view
    name a tag, a view, or a chart by its id. A chart line also names its
    device, and the batch engine names its tags. Every id in the project has
    the builder's form, so the walk takes each id anywhere in the project's
    text, not only where an item is known to keep one."""
    tags: set[str] = {t for d in project["devices"].values() for t in d["tags"]}
    views: set[str] = {v["id"] for v in project["hmi"]["views"]}
    charts: set[str] = {c["id"] for c in project["charts"]}
    scripts: set[str] = {s["id"] for s in project["scripts"]}
    devices: set[str] = set(project["devices"])
    device_names: set[str] = {d["name"] for d in project["devices"].values()}
    names: set[str] = tag_names(project)
    text: str = json.dumps(project)
    missing: list[str] = []
    for prefix, defined in (("t", tags), ("v", views), ("c", charts), ("s", scripts), ("d", devices)):
        used: set[str] = set(re.findall(rf"(?<![A-Za-z0-9]){prefix}{ID}", text))
        missing += sorted(f"{i} is used and not defined" for i in used - defined)
    for chart in project["charts"]:
        for line in chart["lines"]:
            if line["device"] not in device_names:
                missing.append(f'{chart["name"]}: no device {line["device"]}')
            if line["name"] not in names:
                missing.append(f'{chart["name"]}: no tag {line["name"]}')
    for alarm in project["alarms"]:
        if alarm["property"]["variableId"] not in tags:
            missing.append(f'alarm {alarm["name"]}: no tag')
    for script in project["scripts"]:
        missing += sorted(
            f"the batch engine: no tag {name}"
            for name in set(engine_config(project)["names"].values()) - names
        )
        assert script["code"].count("__CFG__") == 0
    layout: dict[str, Any] = project["hmi"]["layout"]
    for target in (layout["start"], *(item["view"] for item in layout["navigation"]["items"])):
        if target not in views:
            missing.append(f"the menu: no view {target}")
    return missing


# -- the files ----------------------------------------------------------------------


def test_the_builder_finds_the_fuxa_hosts_of_each_site() -> None:
    """A FUXA host is a tag list in the site's directory: the process SCADA
    first, then a building front end where the site has one."""
    assert BUILDER.site_codes() == sorted(SITES)
    assert {name: BUILDER.fuxa_hosts(name) for name in ("ci", "full", "fr", "cs", "fc")} == {
        "ci": ["scada-fuxa"],
        "full": ["scada-fuxa", "bas-fuxa"],
        "fr": ["fr-scada-fuxa", "fr-bas-fuxa"],
        "cs": ["cs-scada-fuxa", "cs-bas-fuxa"],
        "fc": ["fc-scada-fuxa"],
    }
    assert BUILDER.default_host("full") == "scada-fuxa"
    assert BUILDER.default_host("fc") == "fc-scada-fuxa"
    assert BUILDER.output("fc", "fc-scada-fuxa") == FUXA_DIR / "fleet" / "fc" / "fc-scada-fuxa-project.json"
    assert BUILDER.output("full", "bas-fuxa") == FUXA_DIR / "full" / "bas-fuxa-project.json"
    assert BUILDER.output("ci", "scada-fuxa") == FUXA_DIR / "scada-fuxa-project.json"
    with pytest.raises(SystemExit, match="named 'zz'"):
        BUILDER.generated_dir("zz")
    with pytest.raises(SystemExit, match="named 'fleet'"):
        BUILDER.generated_dir("fleet")


@pytest.mark.parametrize("job", JOBS, ids=lambda job: f"{job[0]}-{job[1]}")
def test_the_committed_project_is_current(job: tuple[str, str]) -> None:
    path: Path = BUILDER.output(*job)
    assert path.read_text(encoding="utf-8") == BUILDER.render(BUILDER.build_project(*job))


def test_the_fleet_directory_holds_the_projects_of_the_sites_and_no_other() -> None:
    have: set[Path] = set((FUXA_DIR / "fleet").rglob("*"))
    files: set[Path] = {BUILDER.output(*job) for job in SITE_JOBS}
    assert {p for p in have if p.is_file()} == files
    assert {p for p in have if p.is_dir()} == {FUXA_DIR / "fleet" / code for code in SITES}


def test_check_fails_on_a_project_of_no_site(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                              capsys: pytest.CaptureFixture[str]) -> None:
    """``--all --check`` covers the sites, and a project that belongs to no
    FUXA host of a site is stale. Without ``--check`` the builder removes it."""
    here: Path = tmp_path / "fuxa"
    for job in JOBS:
        target: Path = here / BUILDER.output(*job).relative_to(FUXA_DIR)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(BUILDER.output(*job).read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(BUILDER, "HERE", here)
    assert BUILDER.main(["--all", "--check"]) == 0
    assert str(here / "fleet" / "fc" / "fc-scada-fuxa-project.json") in capsys.readouterr().out
    left: Path = here / "fleet" / "fc" / "fc-bas-fuxa-project.json"
    left.write_text("{}\n", encoding="utf-8")
    stale: Path = here / "fleet" / "ep" / "ep-bas-fuxa-project.json"
    stale.write_text("{}\n", encoding="utf-8")
    assert BUILDER.main(["--all", "--check"]) == 1
    err: str = capsys.readouterr().err
    assert f"{left} belongs to no FUXA host of a site" in err
    assert f"{stale} is stale" in err
    assert BUILDER.main(["--all"]) == 0
    assert not left.exists()
    assert BUILDER.main(["--all", "--check"]) == 0
    assert BUILDER.main(["--site", "fc", "--check"]) == 0
    with pytest.raises(SystemExit, match="no plantgen output for the site 'zz'"):
        BUILDER.main(["--site", "zz"])


# -- a project refers to nothing that it does not define ----------------------------


@pytest.mark.parametrize("job", JOBS, ids=lambda job: f"{job[0]}-{job[1]}")
def test_a_project_defines_every_tag_view_chart_and_device_that_it_uses(
    job: tuple[str, str], projects: dict[tuple[str, str], dict[str, Any]]
) -> None:
    assert dangling(projects[job]) == []


def test_the_walk_finds_what_a_project_does_not_define(
    projects: dict[tuple[str, str], dict[str, Any]],
) -> None:
    """The walk above can fail: each kind of reference is found when its
    target is taken away."""
    whole: dict[str, Any] = projects[("fc", "fc-scada-fuxa")]

    def without(change: Any) -> list[str]:
        project: dict[str, Any] = copy.deepcopy(whole)
        change(project)
        return dangling(project)

    def drop_tag(name: str) -> Any:
        def change(project: dict[str, Any]) -> None:
            for device in project["devices"].values():
                device["tags"] = {i: t for i, t in device["tags"].items() if t["name"] != name}
        return change

    # a tag of a view and a trend, a tag of an alarm, and a tag of the batch engine
    assert len(without(drop_tag("CEL.FV101.TT01.PV"))) >= 2
    assert any("alarm" in found for found in without(drop_tag("BH.HLT.LT01.ALM_LOW")))
    assert "the batch engine: no tag BH.BATCH.XS01.STATE" in without(drop_tag("BH.BATCH.XS01.STATE"))

    def drop_view(project: dict[str, Any]) -> None:
        project["hmi"]["views"] = [v for v in project["hmi"]["views"] if v["name"] != "Building"]

    # the Overview's link to the Building, and the menu's item
    assert len(without(drop_view)) == 2
    assert any("the menu" in found for found in without(drop_view))
    assert len(without(lambda project: project["charts"].clear())) >= 1

    def rename_device(project: dict[str, Any]) -> None:
        for device in project["devices"].values():
            device["name"] = device["name"].removeprefix("fc-")

    assert any("no device fc-plc-cellar" in found for found in without(rename_device))


# -- the screens follow the site's areas ---------------------------------------------


def test_a_site_has_the_screens_of_its_areas(projects: dict[tuple[str, str], dict[str, Any]]) -> None:
    """The pub has no packaging and no power, Colorado Springs no power: no
    screen, no table of points, no menu item, and no tag of such an area."""
    names: dict[str, list[str]] = {
        code: [v["name"] for v in projects[(code, f"{code}-scada-fuxa")]["hmi"]["views"]] for code in SITES
    }
    assert names["fc"] == [
        "Overview", "Brewhouse", "Batch", "Cellar", "Utilities", "Cellar CO2", "Building",
        "Brewhouse points", "Cellar points", "Utilities points", "Building points",
    ]
    assert [n for n in names["cs"] if not n.endswith(" points")] == [
        "Overview", "Brewhouse", "Batch", "Cellar", "Packaging", "Utilities", "Cellar CO2",
    ]
    assert "Power points" not in names["cs"] and "Packaging points" in names["cs"]
    assert names["fr"] == names["ep"] == [v["name"] for v in projects[("full", "scada-fuxa")]["hmi"]["views"]]
    areas: dict[str, set[str]] = {
        code: {n.split(".")[0] for n in tag_names(projects[(code, f"{code}-scada-fuxa")])} for code in SITES
    }
    assert areas["fc"] == {"BH", "CEL", "UTL", "FAC", "BATCH"}
    assert areas["cs"] == {"BH", "CEL", "PKG", "UTL", "FAC", "BATCH"}
    assert areas["fr"] == areas["ep"] == {"BH", "CEL", "PKG", "UTL", "PWR", "FAC", "BATCH"}
    for code in SITES:
        menu = projects[(code, f"{code}-scada-fuxa")]["hmi"]["layout"]["navigation"]["items"]
        assert [item["text"] for item in menu] == names[code]


def test_the_overview_shows_the_panels_of_the_sites_areas(
    projects: dict[tuple[str, str], dict[str, Any]],
) -> None:
    """A panel opens its area. The pub's Overview has no Packaging and no
    Power panel, and the Building has the place of the Power panel."""

    def panels(code: str) -> list[str]:
        overview: dict[str, Any] = view(projects[(code, f"{code}-scada-fuxa")], "Overview")
        return [i["name"].removeprefix("open ") for i in overview["items"].values() if i["name"].startswith("open ")]

    def trends(code: str) -> list[str]:
        project: dict[str, Any] = projects[(code, f"{code}-scada-fuxa")]
        return [c["name"] for c in project["charts"] if c["name"].startswith("Overview: ")]

    whole: list[str] = ["Brewhouse", "Cellar", "Packaging", "Utilities", "Power", "Cellar CO2"]
    assert panels("fr") == panels("ep") == whole
    assert panels("cs") == ["Brewhouse", "Cellar", "Packaging", "Utilities", "Cellar CO2"]
    assert panels("fc") == ["Brewhouse", "Cellar", "Utilities", "Building", "Cellar CO2"]
    assert len(trends("fr")) == 2 and "Overview: Power kW, last hour" in trends("fr")
    assert trends("cs") == trends("fc") == ["Overview: Fermenters °F, last hour"]
    svg: str = view(projects[("fc", "fc-scada-fuxa")], "Overview")["svgcontent"]
    assert ">BUILDING<" in svg and ">PACKAGING<" not in svg and ">POWER<" not in svg
    assert ">PACKAGING<" in view(projects[("cs", "cs-scada-fuxa")], "Overview")["svgcontent"]


def test_the_pubs_scada_is_also_its_building_front_end(
    projects: dict[tuple[str, str], dict[str, Any]],
) -> None:
    """The pub has no building front end, so its one SCADA host has the
    Building screen and commands the building's controllers, as the front end
    of the whole plant does. It has no BBMD: the BACnet device finds the
    controllers with the directed broadcast of its own network, which is how
    the front end finds them on the building's network (plantgen ``render.py``,
    ``fuxa_doc``: ``broadcastAddress`` when a controller is on FUXA's subnet)."""
    pub: dict[str, Any] = projects[("fc", "fc-scada-fuxa")]
    front_end: dict[str, Any] = projects[("full", "bas-fuxa")]

    def bacnet(project: dict[str, Any]) -> dict[str, Any]:
        (device,) = (d for d in project["devices"].values() if d["type"] == "BACnet")
        return device

    assert bacnet(pub)["name"] == "fc-scada-fuxa-bacnet"
    assert bacnet(pub)["property"] == {"address": "10.21.10.40", "broadcastAddress": "10.21.10.255"}
    assert set(bacnet(front_end)["property"]) == {"address", "broadcastAddress"}
    # The process SCADA of the whole plant is on another network: no broadcast address.
    assert set(bacnet(projects[("full", "scada-fuxa")])["property"]) == {"address"}
    # The same 39 points, and the front end's screen. The pub's host also reads
    # the two PLCs, so its screen shows what the relays did there, too.
    assert [t["name"] for t in bacnet(pub)["tags"].values()] == [
        t["name"] for t in bacnet(front_end)["tags"].values()
    ]
    ours, theirs = view(pub, "Building"), view(front_end, "Building")
    assert {i["name"] for i in theirs["items"].values()} < {i["name"] for i in ours["items"].values()}
    by_id: dict[str, str] = {i: t["name"] for i, t in bacnet(pub)["tags"].items()}
    commanded: set[str] = {
        by_id[i["property"]["variableId"]] for i in ours["items"].values() if i["type"] == "svg-ext-html_button"
    }
    assert commanded == {"FAC.VENT.FAN01.CMD", "FAC.VENT.FAN02.CMD", "FAC.VENT.XA01.CMD"}
    # The building's two alarms, which FUXA evaluates, are in the pub's one project.
    alarms: set[str] = {a["name"] for a in pub["alarms"]}
    assert {"FAC.RTU1.TT01.ALM_HIGH", "FAC.CR.TT01.ALM_HIGH"} <= alarms
    # Its read-only CO2 screen stays as it is, with nothing to operate.
    co2: dict[str, Any] = view(pub, "Cellar CO2")
    assert not [i for i in co2["items"].values() if i["type"] in ("svg-ext-html_button", "svg-ext-html_input")]


# -- a site's project names the site's hosts -----------------------------------------


def strings(node: Any, path: str = "") -> list[tuple[str, str]]:
    """(where, text) of each string of a project, apart from the batch engine's
    code. Its comments are hand-written, and its data is in ``engine_config``."""
    if isinstance(node, dict):
        return [found for key, value in node.items() for found in strings(value, f"{path}/{key}")]
    if isinstance(node, list):
        return [found for n, value in enumerate(node) for found in strings(value, f"{path}/{n}")]
    if isinstance(node, str) and not re.fullmatch(r"/scripts/\d+/code", path):
        return [(path, node)]
    return []


@pytest.mark.parametrize("job", SITE_JOBS, ids=lambda job: f"{job[0]}-{job[1]}")
def test_a_sites_project_names_only_the_sites_hosts(
    job: tuple[str, str], projects: dict[tuple[str, str], dict[str, Any]]
) -> None:
    """A device, a caption, an alarm text, and a trend's legend have the site's
    prefix on a hostname. No address is one of another site's blocks."""
    code, host = job
    project: dict[str, Any] = projects[job]
    hostname = re.compile(r"(?<![A-Za-z0-9-])((?:[a-z]{2}-)?(?:plc|scada|bas|rio|sim)-[a-z][a-z0-9-]*)")
    found: list[str] = [m.group(1) for _where, text in strings(project) for m in hostname.finditer(text)]
    assert found and all(name.startswith(f"{code}-") for name in found), sorted(set(found))
    assert project["name"] == f"cherrymountain-{code}-{host}"
    devices: list[str] = [d["name"] for d in project["devices"].values() if d["type"] != "FuxaServer"]
    assert devices and all(name.startswith(f"{code}-") for name in devices)
    if project["scripts"]:
        config: dict[str, Any] = engine_config(project)
        assert not [m for _where, text in strings(config) for m in hostname.finditer(text)]


def test_a_caption_names_a_controller_as_the_site_does(
    projects: dict[tuple[str, str], dict[str, Any]],
) -> None:
    """The name comes from the plant's tag list, also for a controller that the
    host does not read: the building front end names the two PLCs behind the
    CO2 relays."""
    def captions(job: tuple[str, str], name: str) -> str:
        text: str = view(projects[job], name)["svgcontent"]
        return text

    assert "The cellar relay opens: plc-cellar stops carbonating" in captions(("full", "bas-fuxa"), "Building")
    assert "The cellar relay opens: cs-plc-cellar stops carbonating" in captions(("cs", "cs-bas-fuxa"), "Building")
    assert "cs-plc-utilities closes the CO2 supply" in captions(("cs", "cs-bas-fuxa"), "Building")
    assert "Carbonation inhibited (fc-plc-cellar)" in captions(("fc", "fc-scada-fuxa"), "Building")
    assert "Carbonation inhibited (ep-plc-cellar)" in captions(("ep", "ep-scada-fuxa"), "Cellar CO2")
    texts: dict[str, str] = {
        code: next(
            a["high"]["text"] for a in projects[(code, f"{code}-scada-fuxa")]["alarms"] if a["name"] == "BATCH.ALM_PLC"
        )
        for code in SITES
    }
    assert "check fc-plc-brewhouse, then Restart or Abort" in texts["fc"]
    full: str = next(
        a["high"]["text"] for a in projects[("full", "scada-fuxa")]["alarms"] if a["name"] == "BATCH.ALM_PLC"
    )
    assert "check plc-brewhouse, then Restart or Abort" in full
    titles: list[str] = re.findall(r">([A-Za-z ]+ points: [a-z-]+)<", json.dumps(projects[("fc", "fc-scada-fuxa")]))
    assert titles == [
        "Brewhouse points: fc-plc-brewhouse",
        "Cellar points: fc-plc-cellar",
        "Utilities points: fc-plc-utilities",
        "Building points: fc-scada-fuxa-bacnet",
    ]


def test_front_range_has_the_whole_plants_project_under_its_own_names(
    projects: dict[tuple[str, str], dict[str, Any]],
) -> None:
    """`fr` leaves nothing out. Its tags have the ids of the whole plant's, and
    its screens have the same elements: only a hostname differs."""
    for host in ("scada-fuxa", "bas-fuxa"):
        ours, theirs = projects[("fr", f"fr-{host}")], projects[("full", host)]
        assert tag_names(ours) == tag_names(theirs)
        assert [a["name"] for a in ours["alarms"]] == [a["name"] for a in theirs["alarms"]]
        for mine, whole in zip(ours["hmi"]["views"], theirs["hmi"]["views"], strict=True):
            assert mine["name"] == whole["name"]
            assert [i["name"] for i in mine["items"].values()] == [i["name"] for i in whole["items"].values()]


def test_the_batch_engine_needs_no_tag_that_a_small_site_lacks(
    projects: dict[tuple[str, str], dict[str, Any]],
) -> None:
    """The engine reads and writes the brewhouse PLC and the cellar PLC only,
    so the pub runs the same script on the same data as the whole plant."""
    whole: dict[str, Any] = engine_config(projects[("full", "scada-fuxa")])
    assert engine_config(projects[("fc", "fc-scada-fuxa")]) == whole
    plant_tags: set[str] = {n for n in whole["names"].values() if not n.startswith("BATCH.")}
    assert {n.split(".")[0] for n in plant_tags} == {"BH", "CEL"}
    assert projects[("cs", "cs-bas-fuxa")]["scripts"] == []
