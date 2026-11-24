#!/usr/bin/env python3
"""Keep every variant's copies of generated files in step with their sources.

phenix injects only what is under ``phenix-injects/`` (``phenix workflow apply``
copies that directory to the server), so files that plantgen and the FUXA
builder write elsewhere have to be copied there. This script owns those copies.
It also embeds the app blocks that plantgen writes to
``plant/generated/<variant>/phenix/<app>.yaml`` into the variant's scenario,
and checks that they stay current.

A site of the fleet (``plant/sites.yaml``) is different: sync.py writes its
whole scenario, ``phenix-configs/scenario-site-<code>.yml``, and no other tool
writes that file. The app blocks come from plantgen
(``plant/generated/fleet/<code>/phenix/``). The ``vrouter`` app and the network
tests of the ``soh`` app come from netcheck, which computes them from the
conduit matrix (``tools/netcheck``, ``netcheck.sitegen``) and then checks the
committed file. sync.py also copies the site's own injected files from
``plant/generated/fleet/<code>/injects/`` to ``phenix-injects/fleet/<code>/``.

Variants: ``ci``, ``full``, and ``lite``. Each scenario marks where a generated
block goes::

    # sync:begin ntp
    ...
    # sync:end ntp

Between the markers sync.py writes the generated block verbatim (a list item
under ``spec.apps``). When the variant has no generated file for that app yet
(plantgen has not emitted it), sync.py skips it with a message and leaves the
placeholder comment, and ``--check`` does not fail.

The ``soh`` block is the exception: its checks are written by hand (the conduit
reachability and blocked-path tests), so a generated ``soh.yaml`` is merged
into it. Map entries (per-host lists) are merged per host, lists are
appended without duplicates, and everything else stays as written. The merged
block replaces the region, comments inside it included.

The ci variant also checks that the committed OpenPLC program ZIP
(``plc/plc-cellar/build_zip.sh``) matches the sources it is built from.

The brand is the other thing this script copies. ``brand/`` is the one source of the
mark and the colors; the screens take copies of its files: every file a VM or a
page uses goes to ``phenix-injects/brand/``, the few cm-viz shows go to
``viz/cherrymountain_viz/web/brand/``, and the colors of ``brand/tokens.json`` become
a ``tokens.css`` (custom properties) next to each set, and Grafana's sign-in background (a plain page
in the dark page color) is made from them too. The Guacamole branding
extension, a ``.jar`` that Guacamole only loads from ``extensions/``, is built here
from its sources in ``phenix-injects/guacamole/branding/`` and the same brand files,
stored without compression so the bytes are the same on every machine. ``--check``
fails when any of them drifted from ``brand/``.

Usage:
    python sync.py                   # copy files, embed blocks
    python sync.py --check           # fail if a copy or an embedded block drifted
    python sync.py --variant full    # one variant (repeatable)
    python sync.py --site fc         # one site of the fleet (repeatable)
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import shutil
import sys
import zipfile
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

ROOT: Path = Path(__file__).resolve().parent
INJECTS: Path = ROOT / "phenix-injects"
CONFIGS: Path = ROOT / "phenix-configs"
#: The repository's check-added-large-files limit.
MAX_ZIP_BYTES: int = 4096 * 1024
#: Apps whose order in a scenario matters (DESIGN.md 15.2).
APP_ORDER: tuple[str, ...] = ("ot-sim", "cherrymountain", "helics")
#: The app whose generated block is merged into the hand-written one.
OVERLAY_APP: str = "soh"
#: Keys of the soh metadata that hold per-host maps and lists.
SOH_HOST_MAPS: tuple[str, ...] = (
    "hostProcesses",
    "hostListeners",
    "hostServices",
    "hostFiles",
    "hostFilesAbsent",
    "dockerContainers",
    "hostCustomTests",
)

BEGIN: re.Pattern[str] = re.compile(r"^  # sync:begin (?P<name>[\w-]+)(?: .*)?$")
END: re.Pattern[str] = re.compile(r"^  # sync:end (?P<name>[\w-]+)\s*$")


class Copy(BaseModel):
    """One file copied from its source of truth into phenix-injects/."""

    src: Path
    dst: Path


class Variant(BaseModel):
    """One variant's scenario, generated files, and copies."""

    name: str
    scenario: Path
    generated: Path
    copies: list[Copy] = []
    check_plc_zip: bool = False

    @property
    def phenix_dir(self) -> Path:
        return self.generated / "phenix"


def _ci_copies(generated: Path) -> list[Copy]:
    return [
        Copy(src=generated / "helics.json", dst=INJECTS / "sim" / "helics.json"),
        Copy(
            src=generated / "openplc" / "plc-cellar" / "modbus_master.json",
            dst=INJECTS / "openplc" / "plc-cellar" / "conf" / "modbus_master.json",
        ),
        Copy(
            src=generated / "openplc" / "plc-cellar" / "modbus_slave.json",
            dst=INJECTS / "openplc" / "plc-cellar" / "conf" / "modbus_slave.json",
        ),
        Copy(
            src=ROOT / "fuxa" / "scada-fuxa-project.json",
            dst=INJECTS / "fuxa" / "scada-fuxa-project.json",
        ),
    ]


def variants() -> dict[str, Variant]:
    out: dict[str, Variant] = {}
    for name in ("ci", "full", "lite"):
        generated: Path = ROOT / "plant" / "generated" / name
        out[name] = Variant(
            name=name,
            scenario=CONFIGS / f"scenario-{name}.yml",
            generated=generated,
            # full and lite inject their plant files through the cherrymountain
            # app at pre-start; only ci injects statically.
            copies=_ci_copies(generated) if name == "ci" else [],
            check_plc_zip=name == "ci",
        )
    return out


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


# --- embedding ----------------------------------------------------------------


def embed_lines(text: str) -> list[str]:
    """A generated app block as list-item lines under ``spec.apps``."""
    out: list[str] = []
    first: bool = True
    for line in text.rstrip("\n").splitlines():
        if first and line.startswith("#"):
            out.append(f"  {line}")
        elif first:
            if not line.startswith("name: "):
                raise ValueError(f"a generated app block starts with `name:`, not {line!r}")
            out.append(f"  - {line}")
            first = False
        else:
            out.append(f"    {line}" if line.strip() else line)
    if first:
        raise ValueError("a generated app block needs a name")
    return out


def find_regions(lines: list[str]) -> dict[str, tuple[int, int]]:
    """Name -> (index of the begin line, index of the end line)."""
    regions: dict[str, tuple[int, int]] = {}
    open_name: str | None = None
    start: int = 0
    for idx, line in enumerate(lines):
        b: re.Match[str] | None = BEGIN.match(line)
        e: re.Match[str] | None = END.match(line)
        if b:
            if open_name is not None:
                raise ValueError(f"sync:begin {b['name']} inside sync:begin {open_name}")
            open_name, start = b["name"], idx
        elif e:
            if open_name != e["name"]:
                raise ValueError(f"sync:end {e['name']} does not close sync:begin {open_name}")
            regions[open_name] = (start, idx)
            open_name = None
    if open_name is not None:
        raise ValueError(f"sync:begin {open_name} is never closed")
    return regions


def merge_soh(base: dict[str, Any], gen: dict[str, Any]) -> dict[str, Any]:
    """Merge a generated soh block into the hand-written one (see the module doc)."""
    out: dict[str, Any] = json.loads(json.dumps(base))
    md: dict[str, Any] = out.setdefault("metadata", {})
    gmd: dict[str, Any] = gen.get("metadata") or {}
    for key, value in gmd.items():
        if key in SOH_HOST_MAPS and isinstance(value, dict):
            target: dict[str, Any] = md.setdefault(key, {})
            for host, items in value.items():
                have: list[Any] = target.setdefault(host, [])
                for item in items:
                    if item not in have and not _same_named_test(key, have, item):
                        have.append(item)
        elif key == "testCustomReachability":
            have_r: list[Any] = md.setdefault(key, [])
            for item in value:
                if item not in have_r:
                    have_r.append(item)
        elif key not in md:
            md[key] = value
    return out


def _same_named_test(key: str, have: list[Any], item: Any) -> bool:
    """A custom test with the name of an existing one is the same test."""
    return key == "hostCustomTests" and any(
        isinstance(h, dict) and h.get("name") == item.get("name") for h in have
    )


class _Dumper(yaml.SafeDumper):
    def ignore_aliases(self, data: Any) -> bool:
        return True


def _str_representer(dumper: yaml.SafeDumper, data: str) -> yaml.ScalarNode:
    style: str | None = "|" if "\n" in data else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


_Dumper.add_representer(str, _str_representer)


def dump_app(app: dict[str, Any]) -> list[str]:
    text: str = yaml.dump(
        [app], Dumper=_Dumper, default_flow_style=False, sort_keys=False, width=1000
    )
    return [f"  {ln}" if ln.strip() else ln for ln in text.splitlines()]


def app_block(lines: list[str], span: tuple[int, int]) -> dict[str, Any]:
    """The app inside a region, parsed."""
    body: str = "\n".join(line[2:] if line.startswith("  ") else line for line in lines[span[0] + 1 : span[1]])
    parsed: Any = yaml.safe_load(body)
    if not isinstance(parsed, list) or len(parsed) != 1:
        raise ValueError("a region must hold exactly one app")
    return parsed[0]


def sync_scenario(v: Variant, check: bool) -> list[str]:
    """Embed the generated app blocks into the variant's scenario."""
    problems: list[str] = []
    if not v.scenario.exists():
        return [f"missing {rel(v.scenario)}"]
    text: str = v.scenario.read_text(encoding="utf-8")
    lines: list[str] = text.splitlines()
    try:
        regions: dict[str, tuple[int, int]] = find_regions(lines)
    except ValueError as exc:
        return [f"{rel(v.scenario)}: {exc}"]
    generated: dict[str, Path] = {}
    if v.phenix_dir.is_dir():
        for path in sorted(v.phenix_dir.glob("*.yaml")):
            name: str = str((yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("name", ""))
            if not name:
                problems.append(f"{rel(path)} has no app name")
                continue
            generated[name] = path
    for name in generated:
        if name not in regions:
            problems.append(f"{rel(v.scenario)}: no sync:begin {name} marker for {rel(generated[name])}")
    new_lines: list[str] = list(lines)
    # Apply from the bottom so earlier indexes stay valid.
    for name, (start, end) in sorted(regions.items(), key=lambda kv: -kv[1][0]):
        path: Path | None = generated.get(name)
        if path is None:
            print(f"skip {v.name}/{name}: plantgen has not emitted {rel(v.phenix_dir / (name + '.yaml'))}")
            continue
        try:
            if name == OVERLAY_APP:
                gen_app: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
                current: dict[str, Any] = app_block(lines, (start, end))
                merged: dict[str, Any] = merge_soh(current, gen_app)
                if merged == current:
                    continue
                body: list[str] = dump_app(merged)
            else:
                body = embed_lines(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            problems.append(f"{rel(path)}: {exc}")
            continue
        if new_lines[start + 1 : end] == body:
            continue
        if check:
            problems.append(f"{rel(v.scenario)}: app {name} differs from {rel(path)}")
        else:
            new_lines[start + 1 : end] = body
            print(f"embedded {rel(path)} -> {rel(v.scenario)}")
    if not check and new_lines != lines:
        v.scenario.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    problems += check_order(v.scenario, "\n".join(new_lines) if not check else text)
    return problems


def check_order(scenario: Path, text: str) -> list[str]:
    """ot-sim, cherrymountain, helics: the helics app counts the federate
    annotations that the other two add."""
    try:
        doc: Any = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return [f"{rel(scenario)}: {exc}"]
    names: list[str] = [a["name"] for a in doc["spec"]["apps"]]
    idx: list[int] = [names.index(a) for a in APP_ORDER if a in names]
    if idx != sorted(idx):
        return [f"{rel(scenario)}: apps must be ordered ot-sim, cherrymountain, helics"]
    return []


# --- file copies --------------------------------------------------------------


def sync_copies(v: Variant, check: bool) -> list[str]:
    return copy_files(v.copies, check)


def copy_files(copies: list[Copy], check: bool) -> list[str]:
    problems: list[str] = []
    for c in copies:
        if not c.src.exists():
            problems.append(f"missing source {rel(c.src)}")
            continue
        if (
            c.dst.exists()
            and c.dst.read_bytes() == c.src.read_bytes()
            and _executable(c.dst) == _executable(c.src)
        ):
            continue
        if check:
            problems.append(f"{rel(c.dst)} differs from {rel(c.src)}")
        else:
            c.dst.parent.mkdir(parents=True, exist_ok=True)
            # With its mode: a copied script must stay a script.
            shutil.copy(c.src, c.dst)
            print(f"copied {rel(c.src)} -> {rel(c.dst)}")
    return problems


def _executable(path: Path) -> bool:
    return bool(path.stat().st_mode & 0o111)


# --- the sites of the fleet ---------------------------------------------------

#: The apps of a site's scenario that plantgen writes, in the scenario's order.
SITE_PLANT_APPS: tuple[str, ...] = ("ot-sim", "cherrymountain", "helics", "ntp")
#: Where the sites' own copies of injected files go, under phenix-injects/.
SITE_INJECTS: str = "fleet"
SITE_SCENARIO: str = "scenario-site-{code}.yml"

SITE_HEADER: str = """\
# cherrymountain-site-{code} scenario: the site `{code}` alone. Generated by sync.py.
# Do not edit: change the sources and run `python sync.py`.
#
# Where each app comes from:
# - ot-sim, cherrymountain, helics, ntp: the blocks that plantgen writes to
#   plant/generated/fleet/{code}/phenix/.
# - vrouter: the rulesets that netcheck computes from the conduit matrix
#   (tools/netcheck, netcheck.sitegen) and plant/sites.yaml. They carry the
#   conduits whose two ends are hosts of this site, and nothing else:
#   {rules} rules on {routers}.
# - soh: the network tests that netcheck computes from the same conduits, and
#   plantgen's soh block merged into them.
#
# This scenario does not have these parts yet:
# - The conduits to the internet, the vendor, the utility, and other sites,
#   with the edge's NAT and VPN.
# - The mirror app for the site's sensor, and the apps of the head office.
"""


class Site(BaseModel):
    """One site of the fleet: its scenario, its generated files, and its copies."""

    code: str
    scenario: Path
    generated: Path
    injects: Path

    @property
    def phenix_dir(self) -> Path:
        return self.generated / "phenix"


def _netcheck() -> tuple[Any, Any]:
    """netcheck's model of a site and what it computes for the site's scenario.

    netcheck is code, so it comes from this checkout also when ``ROOT`` is a
    copy of the data. It reads plantgen's model of ``plant/sites.yaml``.
    """
    path: str = str(Path(__file__).resolve().parent / "tools" / "netcheck")
    if path not in sys.path:
        sys.path.insert(0, path)
    # Imported here: only a site needs netcheck and plantgen.
    from netcheck import site, sitegen

    return site, sitegen


def sites() -> dict[str, Site]:
    """The sites of ``plant/sites.yaml``; none when the file is not there."""
    if not (ROOT / "plant" / "sites.yaml").is_file():
        return {}
    site_model, _ = _netcheck()
    return {
        code: Site(
            code=code,
            scenario=ROOT / "phenix-configs" / SITE_SCENARIO.format(code=code),
            generated=ROOT / "plant" / "generated" / "fleet" / code,
            injects=ROOT / "phenix-injects" / SITE_INJECTS / code,
        )
        for code in site_model.codes(ROOT)
    }


def site_scenario(s: Site) -> str:
    """The text of a site's scenario file. Raises ValueError when a part is missing."""
    site_model, sitegen = _netcheck()
    try:
        derived: Any = site_model.load(s.code, ROOT)
    except site_model.SiteError as exc:
        raise ValueError(str(exc)) from exc
    computed: dict[str, dict[str, Any]] = sitegen.apps(derived)
    counts: dict[str, int] = sitegen.rule_counts(derived)
    blocks: dict[str, Path] = {p.stem: p for p in sorted(s.phenix_dir.glob("*.yaml"))}
    missing: list[str] = [a for a in (*SITE_PLANT_APPS, OVERLAY_APP) if a not in blocks]
    if missing:
        raise ValueError(f"{rel(s.phenix_dir)} has no block for {missing}; run plantgen")
    extra: list[str] = sorted(set(blocks) - {*SITE_PLANT_APPS, OVERLAY_APP})
    if extra:
        raise ValueError(f"{rel(s.phenix_dir)}: a site's scenario has no place for {extra}")
    name: str = f"cherrymountain-site-{s.code}"
    lines: list[str] = SITE_HEADER.format(
        code=s.code,
        rules=sum(counts.values()),
        routers=" and ".join(f"{router} ({count})" for router, count in counts.items()),
    ).splitlines()
    lines += [
        "apiVersion: phenix.sandia.gov/v2",
        "kind: Scenario",
        "metadata:",
        f"  name: {name}",
        "  annotations:",
        f"    topology: {name}",
        "spec:",
        "  apps:",
        "  # The startup app runs on every node by default; it needs no per-host settings.",
        "  - name: startup",
    ]
    for app in SITE_PLANT_APPS:
        lines += embed_lines(blocks[app].read_text(encoding="utf-8"))
    lines.append("  # Computed by netcheck (tools/netcheck, netcheck.sitegen). Do not edit.")
    lines += dump_app(computed["vrouter"])
    lines.append(
        "  # netcheck's network tests, with plantgen's "
        f"{rel(blocks[OVERLAY_APP])} merged into them. Do not edit."
    )
    overlay: dict[str, Any] = yaml.safe_load(blocks[OVERLAY_APP].read_text(encoding="utf-8"))
    lines += dump_app(merge_soh(computed[OVERLAY_APP], overlay))
    return "\n".join(lines) + "\n"


def sync_site_scenario(s: Site, check: bool) -> list[str]:
    """Write (or check) a site's scenario file."""
    text: str = site_scenario(s)
    if not s.scenario.is_file() or s.scenario.read_text(encoding="utf-8") != text:
        if check:
            return [f"{rel(s.scenario)} is not what sync.py writes for the site; run sync.py"]
        s.scenario.write_text(text, encoding="utf-8")
        print(f"wrote {rel(s.scenario)}")
    return check_order(s.scenario, text)


def sync_site_copies(s: Site, check: bool) -> list[str]:
    """Copy (or check) the site's own injected files, and clear what has no source."""
    source: Path = s.generated / "injects"
    copies: list[Copy] = [
        Copy(src=p, dst=s.injects / p.relative_to(source))
        for p in sorted(source.rglob("*"))
        if p.is_file()
    ]
    problems: list[str] = copy_files(copies, check)
    wanted: set[Path] = {c.dst for c in copies}
    if s.injects.is_dir():
        for old in sorted(s.injects.rglob("*"), reverse=True):
            if old.is_file() and old not in wanted:
                problems += _remove(old, f"{rel(source)} has no file for it", check)
            elif old.is_dir() and not check and not any(old.iterdir()):
                old.rmdir()
    return problems


def _remove(path: Path, why: str, check: bool) -> list[str]:
    if check:
        return [f"{rel(path)} is left over: {why}; run sync.py"]
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()
    print(f"removed {rel(path)}: {why}")
    return []


def sync_site_leftovers(found: dict[str, Site], check: bool) -> list[str]:
    """Remove (or report) the scenario and the copies of a site that sites.yaml lost."""
    problems: list[str] = []
    why: str = "plant/sites.yaml has no such site"
    wanted: set[Path] = {s.scenario for s in found.values()}
    for path in sorted((ROOT / "phenix-configs").glob(SITE_SCENARIO.format(code="*"))):
        if path not in wanted:
            problems += _remove(path, why, check)
    copies: Path = ROOT / "phenix-injects" / SITE_INJECTS
    if copies.is_dir():
        for path in sorted(copies.iterdir()):
            if path.name not in found:
                problems += _remove(path, why, check)
    return problems


# --- the ci OpenPLC ZIP -------------------------------------------------------

PLC_DIR: Path = ROOT / "plc" / "plc-cellar"
PLC_ZIP: Path = INJECTS / "openplc" / "plc-cellar.zip"
#: First line of a Git LFS pointer file, which is what a checkout without LFS
#: (``GIT_LFS_SKIP_SMUDGE=1``, or a CI checkout without ``lfs: true``) holds.
LFS_POINTER: bytes = b"version https://git-lfs.github.com/spec/v1"


def is_lfs_pointer(path: Path) -> bool:
    """Whether ``path`` is a Git LFS pointer stub, not the file itself."""
    with path.open("rb") as fh:
        return fh.read(len(LFS_POINTER)) == LFS_POINTER


def assembled_program_md5(variant: str) -> str:
    """MD5 of the single-file ST program ``assemble.py`` builds (the one
    ``build_zip.sh`` compiles); also checks the program against the plant."""
    sys.path.insert(0, str(PLC_DIR))
    try:
        import assemble  # noqa: PLC0415 - sibling script, not a package
    finally:
        sys.path.remove(str(PLC_DIR))
    full: str
    problems: list[str]
    full, problems = assemble.assemble(variant)
    if problems:
        raise ValueError("; ".join(problems))
    return hashlib.md5(full.encode()).hexdigest()  # noqa: S324 - OpenPLC's program id


def check_zip(v: Variant) -> list[str]:
    """The committed program ZIP carries this program and these plugin configs.

    A full rebuild needs strucpp (``build_zip.sh --check`` does it); this
    catches the usual drift without Node: the MD5 in ``defines.h`` is the MD5
    of the program as assembled now, ``conf/`` equals the generated plugin
    configs, and the file fits the repository's size limit.
    """
    if not PLC_ZIP.exists():
        return [f"missing {rel(PLC_ZIP)}; run plc/plc-cellar/build_zip.sh"]
    if is_lfs_pointer(PLC_ZIP):
        print(
            f"skip {v.name}/zip: {rel(PLC_ZIP)} is a Git LFS pointer (checkout without LFS); "
            "the ZIP contents were not checked"
        )
        return []
    plc_generated: Path = v.generated / "openplc" / "plc-cellar"
    problems: list[str] = []
    if PLC_ZIP.stat().st_size > MAX_ZIP_BYTES:
        problems.append(f"{PLC_ZIP.name} is over {MAX_ZIP_BYTES} bytes")
    with zipfile.ZipFile(PLC_ZIP) as zf:
        names: set[str] = set(zf.namelist())
        for need in ("generated.cpp", "generated.hpp", "generated_debug.cpp", "defines.h"):
            if need not in names:
                problems.append(f"{PLC_ZIP.name}: missing {need}")
        if not any(n.startswith("strucpp_runtime/include/") for n in names):
            problems.append(f"{PLC_ZIP.name}: missing strucpp_runtime/include/")
        if "defines.h" in names:
            defines: str = zf.read("defines.h").decode()
            want: str = assembled_program_md5(v.name)
            if f'#define PROGRAM_MD5 "{want}"' not in defines:
                problems.append(
                    f"{PLC_ZIP.name}: defines.h PROGRAM_MD5 is not the MD5 ({want}) of "
                    "the assembled plc-cellar program; rerun plc/plc-cellar/build_zip.sh"
                )
        for conf in ("modbus_master.json", "modbus_slave.json"):
            src: Path = plc_generated / conf
            if f"conf/{conf}" not in names:
                problems.append(f"{PLC_ZIP.name}: missing conf/{conf}")
            elif json.loads(zf.read(f"conf/{conf}")) != json.loads(src.read_text()):
                problems.append(
                    f"{PLC_ZIP.name}: conf/{conf} differs from {rel(src)}; "
                    "rerun plc/plc-cellar/build_zip.sh"
                )
    return problems


# --- the full and lite OpenPLC ZIPs (plc/common/build_zip.sh) -----------------

COMMON_DIR: Path = ROOT / "plc" / "common"
#: The PLCs of the full and lite variants; each one with a program under
#: plc/<plc>/ must have a current program ZIP.
VARIANT_PLCS: tuple[str, ...] = (
    "plc-brewhouse",
    "plc-cellar",
    "plc-packaging",
    "plc-utilities",
    "plc-power",
)


def variant_program_md5(plc: str, variant: str) -> str:
    """MD5 of the program ``plc/common/plcbuild.py`` assembles; also runs its checks."""
    sys.path.insert(0, str(COMMON_DIR))
    try:
        import plcbuild  # noqa: PLC0415 - sibling script, not a package
    finally:
        sys.path.remove(str(COMMON_DIR))
    full: str
    problems: list[str]
    full, problems = plcbuild.assemble(plc, variant)
    if problems:
        raise ValueError("; ".join(problems))
    return hashlib.md5(full.encode()).hexdigest()  # noqa: S324 - OpenPLC's program id


def check_variant_zips(v: Variant) -> list[str]:
    """Each committed full/lite program ZIP carries the program as assembled now
    (``defines.h``), the generated plugin configs, and the debug map."""
    problems: list[str] = []
    for plc in VARIANT_PLCS:
        source: str = "plc-cellar-plant.st" if plc == "plc-cellar" else f"{plc}.st"
        if not (ROOT / "plc" / plc / source).is_file():
            continue  # no full-variant program for this PLC yet
        zpath: Path = INJECTS / "openplc" / v.name / f"{plc}.zip"
        rebuild: str = f"rerun plc/common/build_zip.sh {plc} --variant {v.name}"
        if not zpath.exists():
            problems.append(f"missing {rel(zpath)}; {rebuild}")
            continue
        if is_lfs_pointer(zpath):
            print(f"skip {v.name}/{plc}: {rel(zpath)} is a Git LFS pointer; the ZIP was not checked")
            continue
        if zpath.stat().st_size > MAX_ZIP_BYTES:
            problems.append(f"{rel(zpath)} is over {MAX_ZIP_BYTES} bytes")
        try:
            want: str = variant_program_md5(plc, v.name)
        except (OSError, ValueError, SystemExit) as exc:
            problems.append(f"{plc} ({v.name}): {exc}")
            continue
        with zipfile.ZipFile(zpath) as zf:
            names: set[str] = set(zf.namelist())
            for need in ("generated.cpp", "generated.hpp", "generated_debug.cpp", "defines.h", "debug_map.json"):
                if need not in names:
                    problems.append(f"{rel(zpath)}: missing {need}")
            if "defines.h" in names and f'#define PROGRAM_MD5 "{want}"' not in zf.read("defines.h").decode():
                problems.append(f"{rel(zpath)}: PROGRAM_MD5 is not the assembled program's ({want}); {rebuild}")
            for src in sorted((v.generated / "openplc" / plc).glob("*.json")):
                entry: str = f"conf/{src.name}"
                if entry not in names:
                    problems.append(f"{rel(zpath)}: missing {entry}; {rebuild}")
                elif json.loads(zf.read(entry)) != json.loads(src.read_text()):
                    problems.append(f"{rel(zpath)}: {entry} differs from {rel(src)}; {rebuild}")
    return problems


# --- the brand ----------------------------------------------------------------

#: Files of ``brand/`` that no screen takes, and why. Every other file is copied.
BRAND_NOT_COPIED: dict[str, str] = {
    "README.md": "documentation",
    "render_assets.cjs": "renders the PNGs, the ICO, and the wallpapers",
    "wallpaper.cjs": "draws the wallpaper",
    "wallpaper.png": "3840x2160; the desktops run 1440x900, so they take wallpaper-1080p.png",
}
#: The brand files cm-viz's page shows: the mark, the tab icon, and the ICO fallback.
VIZ_BRAND_FILES: tuple[str, ...] = ("icon.svg", "icon-small.svg", "favicon.ico")
#: The brand files inside the Guacamole extension, next to the generated tokens.css
#: and the sources in phenix-injects/guacamole/branding/ (the manifest names them).
GUAC_BRAND_FILES: tuple[str, ...] = ("icon.svg", "icon-64.png", "icon-256.png")
GUAC_MANIFEST: str = "guac-manifest.json"
#: A zip's own date for every entry: the extension's bytes depend on its contents only.
ZIP_EPOCH: tuple[int, int, int, int, int, int] = (1980, 1, 1, 0, 0, 0)


def brand_dir() -> Path:
    return ROOT / "brand"


def brand_assets() -> list[Path]:
    """The files of ``brand/`` the screens take."""
    return sorted(
        p for p in brand_dir().iterdir() if p.is_file() and p.name not in BRAND_NOT_COPIED
    )


def brand_tokens() -> dict[str, Any]:
    tokens: Any = json.loads((brand_dir() / "tokens.json").read_text(encoding="utf-8"))
    colors: Any = tokens.get("colors")
    if not isinstance(colors, dict) or not colors:
        raise ValueError("brand/tokens.json has no colors")
    for name, value in colors.items():
        if not re.fullmatch(r"#[0-9A-Fa-f]{6}", str(value)):
            raise ValueError(f"brand/tokens.json: color {name} is {value!r}, not #RRGGBB")
    # The alarm colors are reserved: no brand color may be one of them.
    reserved: dict[str, str] = {
        v.lower(): k for k, v in (tokens.get("reserved_for_alarms") or {}).items()
    }
    for name, value in colors.items():
        if str(value).lower() in reserved:
            raise ValueError(
                f"brand/tokens.json: brand color {name} is {value}, the color reserved for "
                f"the {reserved[str(value).lower()]} alarm"
            )
    return tokens


def tokens_css() -> str:
    """The colors of ``brand/tokens.json`` as CSS custom properties, then each role
    that names one color (``--cm-header-bg: var(--cm-ink)``)."""
    tokens: dict[str, Any] = brand_tokens()
    lines: list[str] = [
        f"/* Generated by sync.py from brand/tokens.json ({tokens.get('name', 'brand')}). Do not edit. */",
        ":root {",
    ]
    lines += [f"  --cm-{name}: {value};" for name, value in tokens["colors"].items()]
    for role, name in (tokens.get("roles") or {}).items():
        if isinstance(name, str):
            if name not in tokens["colors"]:
                raise ValueError(f"brand/tokens.json: role {role} names {name}, which is not a color")
            lines.append(f"  --cm-{role.replace('_', '-')}: var(--cm-{name});")
    lines.append("}")
    return "\n".join(lines) + "\n"


#: The size of the SVG Grafana ships as its sign-in page background (g8_login_dark.svg in
#: 12.4.14): its login page draws it at its own size, top center, behind the sign-in box.
GRAFANA_LOGIN_BACKGROUND_SIZE: tuple[int, int] = (2261, 1272)


def login_background_svg() -> str:
    """A plain page in the brand's dark page color, the size of the picture it replaces in
    Grafana's sign-in page (cm-grafana-brand copies it over g8_login_dark.svg and
    g8_login_light.svg)."""
    tokens: dict[str, Any] = brand_tokens()
    page: str = tokens["colors"][tokens["roles"]["page_bg_dark"]]
    width, height = GRAFANA_LOGIN_BACKGROUND_SIZE
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">\n'
        "  <!-- Generated by sync.py from brand/tokens.json (page_bg_dark). Do not edit. -->\n"
        f'  <rect width="{width}" height="{height}" fill="{page}"/>\n'
        "</svg>\n"
    )


#: Where the topology puts the login logo on ews (brand/xrdp-logo.bmp).
XRDP_LOGO_PATH: str = "/usr/share/xrdp/cherrymountain-logo.bmp"


def xrdp_conf() -> str:
    """The keys of xrdp.ini's [Globals] section that draw its login screen, with the
    brand's values (cm-brand-xrdp sets them on ews). xrdp writes a color as six hex
    digits. The box stays light, because xrdp draws its labels in black."""
    tokens: dict[str, Any] = brand_tokens()
    colors: dict[str, str] = tokens["colors"]
    roles: dict[str, Any] = tokens["roles"]

    def color(name: str) -> str:
        return colors[name].lstrip("#").upper()

    settings: dict[str, str] = {
        "ls_title": "CherryMountain Brewery",
        "ls_top_window_bg_color": color(roles["page_bg_dark"]),  # the screen behind the box
        "ls_bg_color": color("paper"),  # the login box
        "blue": color(roles["primary"]),  # xrdp's accent: the box's title bar
        "ls_logo_filename": XRDP_LOGO_PATH,
    }
    lines: list[str] = ["# Generated by sync.py from brand/tokens.json. Do not edit."]
    lines += [f"{key}={value}" for key, value in settings.items()]
    return "\n".join(lines) + "\n"


def brand_copies() -> list[Copy]:
    """``brand/`` into phenix-injects/brand/, and the files cm-viz shows into its web/brand/."""
    injects: Path = ROOT / "phenix-injects" / "brand"
    viz: Path = ROOT / "viz" / "cherrymountain_viz" / "web" / "brand"
    copies: list[Copy] = [Copy(src=p, dst=injects / p.name) for p in brand_assets()]
    copies += [Copy(src=brand_dir() / n, dst=viz / n) for n in VIZ_BRAND_FILES]
    return copies


def guacamole_jar() -> bytes:
    """The branding extension of Guacamole: ``guac-manifest.json``, the CSS, and the
    translation from ``phenix-injects/guacamole/branding/``, the generated
    ``tokens.css``, and the brand files the manifest names. Stored, not deflated, with
    one date and one set of permissions, so the same inputs give the same bytes."""
    src: Path = ROOT / "phenix-injects" / "guacamole" / "branding"
    entries: dict[str, bytes] = {
        p.relative_to(src).as_posix(): p.read_bytes() for p in sorted(src.rglob("*")) if p.is_file()
    }
    extra: dict[str, bytes] = {"tokens.css": tokens_css().encode()}
    extra |= {n: (brand_dir() / n).read_bytes() for n in GUAC_BRAND_FILES}
    for name, data in extra.items():
        if name in entries:
            raise ValueError(f"guacamole/branding/{name} would be overwritten by the brand file of that name")
        entries[name] = data
    if GUAC_MANIFEST not in entries:
        raise ValueError(f"guacamole/branding/{GUAC_MANIFEST} is missing")
    manifest: dict[str, Any] = json.loads(entries[GUAC_MANIFEST])
    named: list[str] = [
        *manifest.get("css", []),
        *manifest.get("translations", []),
        *manifest.get("resources", {}),
        *[manifest[k] for k in ("smallIcon", "largeIcon") if k in manifest],
    ]
    absent: list[str] = [n for n in named if n not in entries]
    if absent:
        raise ValueError(f"{GUAC_MANIFEST} names files the extension does not hold: {absent}")
    out: io.BytesIO = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as zf:
        for name in sorted(entries, key=lambda n: (n != GUAC_MANIFEST, n)):
            info: zipfile.ZipInfo = zipfile.ZipInfo(name, date_time=ZIP_EPOCH)
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 3
            info.external_attr = 0o644 << 16
            zf.writestr(info, entries[name])
    return out.getvalue()


def brand_generated() -> dict[Path, bytes]:
    """File -> bytes of what is made from the brand, not copied as it is."""
    css: bytes = tokens_css().encode()
    return {
        ROOT / "phenix-injects" / "brand" / "tokens.css": css,
        ROOT / "phenix-injects" / "brand" / "login-background.svg": login_background_svg().encode(),
        ROOT / "phenix-injects" / "brand" / "xrdp.conf": xrdp_conf().encode(),
        ROOT / "viz" / "cherrymountain_viz" / "web" / "brand" / "tokens.css": css,
        ROOT / "phenix-injects" / "guacamole" / "cherrymountain-brand.jar": guacamole_jar(),
    }


def sync_brand(check: bool) -> list[str]:
    """Copy (or check) the brand files and what is generated from them."""
    problems: list[str] = copy_files(brand_copies(), check)
    for dst, data in brand_generated().items():
        if dst.exists() and dst.read_bytes() == data:
            continue
        if check:
            problems.append(f"{rel(dst)} is not what sync.py makes from brand/; run sync.py")
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(data)
            print(f"wrote {rel(dst)}")
    return problems


def main(argv: list[str] | None = None) -> int:
    ap: argparse.ArgumentParser = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true")
    ap.add_argument(
        "--variant",
        action="append",
        choices=["ci", "full", "lite"],
        help="variant to sync (repeatable; default: all, or none with --site)",
    )
    ap.add_argument(
        "--site",
        action="append",
        help="site of plant/sites.yaml to sync (repeatable; default: all, or none with --variant)",
    )
    args: argparse.Namespace = ap.parse_args(argv)

    problems: list[str] = []
    # Without a choice, everything is synced. With one, only what was chosen.
    everything: bool = not args.variant and not args.site
    chosen: dict[str, Variant] = {
        n: v for n, v in variants().items() if everything or n in (args.variant or [])
    }
    found: dict[str, Site] = {}
    try:
        found = sites()
    except (OSError, KeyError, TypeError, ValueError, ImportError) as exc:
        problems.append(f"sites: {type(exc).__name__}: {exc}")
    unknown: list[str] = [code for code in args.site or [] if code not in found]
    if unknown and not problems:
        problems.append(f"plant/sites.yaml has no site {unknown}; it has {list(found)}")
    chosen_sites: dict[str, Site] = {
        code: s for code, s in found.items() if everything or code in (args.site or [])
    }
    for v in chosen.values():
        # A missing or malformed input (plantgen not run, a scenario without
        # spec.apps, ...) is reported as a FAIL line, not a traceback.
        try:
            problems += sync_copies(v, args.check)
            problems += sync_scenario(v, args.check)
            if v.check_plc_zip:
                problems += check_zip(v)
            if v.name in ("full", "lite"):
                problems += check_variant_zips(v)
        except (OSError, KeyError, TypeError, ValueError) as exc:
            problems.append(f"{v.name}: {type(exc).__name__}: {exc}")

    for s in chosen_sites.values():
        try:
            problems += sync_site_copies(s, args.check)
            problems += sync_site_scenario(s, args.check)
        except (OSError, KeyError, TypeError, ValueError) as exc:
            problems.append(f"site {s.code}: {type(exc).__name__}: {exc}")
    if everything and not problems:
        problems += sync_site_leftovers(found, args.check)

    # The brand does not depend on the variant, so it is synced every time.
    try:
        problems += sync_brand(args.check)
    except (OSError, KeyError, TypeError, ValueError) as exc:
        problems.append(f"brand: {type(exc).__name__}: {exc}")

    for p in problems:
        print(f"FAIL: {p}", file=sys.stderr)
    if not problems:
        names: list[str] = [*chosen, *(f"site {code}" for code in chosen_sites)]
        print(f"{', '.join(names)}: copies and embedded blocks are in sync")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
