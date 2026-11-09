#!/usr/bin/env python3
"""Keep every variant's copies of generated files in step with their sources.

phenix injects only what is under ``phenix-injects/`` (``phenix workflow apply``
copies that directory to the server), so files that plantgen and the FUXA
builder write elsewhere have to be copied there. This script owns those copies.
It also embeds the app blocks that plantgen writes to
``plant/generated/<variant>/phenix/<app>.yaml`` into the variant's scenario,
and checks that they stay current.

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

Usage:
    python sync.py                   # copy files, embed blocks
    python sync.py --check           # fail if a copy or an embedded block drifted
    python sync.py --variant full    # one variant (repeatable)
"""

from __future__ import annotations

import argparse
import hashlib
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
    problems += check_order(v, "\n".join(new_lines) if not check else text)
    return problems


def check_order(v: Variant, text: str) -> list[str]:
    """ot-sim, cherrymountain, helics: the helics app counts the federate
    annotations that the other two add."""
    try:
        doc: Any = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return [f"{rel(v.scenario)}: {exc}"]
    names: list[str] = [a["name"] for a in doc["spec"]["apps"]]
    idx: list[int] = [names.index(a) for a in APP_ORDER if a in names]
    if idx != sorted(idx):
        return [f"{rel(v.scenario)}: apps must be ordered ot-sim, cherrymountain, helics"]
    return []


# --- file copies --------------------------------------------------------------


def sync_copies(v: Variant, check: bool) -> list[str]:
    problems: list[str] = []
    for c in v.copies:
        if not c.src.exists():
            problems.append(f"missing source {rel(c.src)}")
            continue
        if c.dst.exists() and c.dst.read_bytes() == c.src.read_bytes():
            continue
        if check:
            problems.append(f"{rel(c.dst)} differs from {rel(c.src)}")
        else:
            c.dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(c.src, c.dst)
            print(f"copied {rel(c.src)} -> {rel(c.dst)}")
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


def main(argv: list[str] | None = None) -> int:
    ap: argparse.ArgumentParser = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true")
    ap.add_argument(
        "--variant",
        action="append",
        choices=["ci", "full", "lite"],
        help="variant to sync (repeatable; default: all)",
    )
    args: argparse.Namespace = ap.parse_args(argv)

    chosen: dict[str, Variant] = {
        n: v for n, v in variants().items() if not args.variant or n in args.variant
    }
    problems: list[str] = []
    for v in chosen.values():
        problems += sync_copies(v, args.check)
        problems += sync_scenario(v, args.check)
        if v.check_plc_zip:
            problems += check_zip(v)

    for p in problems:
        print(f"FAIL: {p}", file=sys.stderr)
    if not problems:
        print(f"{', '.join(chosen)}: copies and embedded blocks are in sync")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
