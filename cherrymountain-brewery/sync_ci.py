#!/usr/bin/env python3
"""Keep the ci variant's copies of generated files in step with their sources.

phenix injects only what is under ``phenix-injects/`` (``phenix workflow apply``
copies that directory to the server), so files that plantgen and the FUXA
builder write elsewhere have to be copied there. This script owns those copies
and checks that ``phenix-configs/scenario-ci.yml`` embeds the generated ot-sim
and ntp blocks verbatim. It also checks that the committed OpenPLC program ZIP
(``plc/plc-cellar/build_zip.sh``) matches the sources it is built from.

Usage:
    python sync_ci.py            # copy the files
    python sync_ci.py --check    # fail if a copy or an embedded block drifted
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import zipfile
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

ROOT: Path = Path(__file__).resolve().parent
GENERATED: Path = ROOT / "plant" / "generated" / "ci"
INJECTS: Path = ROOT / "phenix-injects"
SCENARIO: Path = ROOT / "phenix-configs" / "scenario-ci.yml"
PLC_DIR: Path = ROOT / "plc" / "plc-cellar"
PLC_GENERATED: Path = GENERATED / "openplc" / "plc-cellar"
PLC_ZIP: Path = INJECTS / "openplc" / "plc-cellar.zip"
#: The repository's check-added-large-files limit.
MAX_ZIP_BYTES: int = 4096 * 1024


class Copy(BaseModel):
    """One file copied from its source of truth into phenix-injects/."""

    src: Path
    dst: Path


COPIES: list[Copy] = [
    Copy(src=GENERATED / "helics.json", dst=INJECTS / "sim" / "helics.json"),
    Copy(
        src=GENERATED / "openplc" / "plc-cellar" / "modbus_master.json",
        dst=INJECTS / "openplc" / "plc-cellar" / "conf" / "modbus_master.json",
    ),
    Copy(
        src=GENERATED / "openplc" / "plc-cellar" / "modbus_slave.json",
        dst=INJECTS / "openplc" / "plc-cellar" / "conf" / "modbus_slave.json",
    ),
    Copy(
        src=ROOT / "fuxa" / "scada-fuxa-project.json",
        dst=INJECTS / "fuxa" / "scada-fuxa-project.json",
    ),
]

EMBEDDED: dict[str, Path] = {
    "ot-sim": GENERATED / "phenix" / "ot-sim.yaml",
    "ntp": GENERATED / "phenix" / "ntp.yaml",
}


def check_embedded() -> list[str]:
    """Compare the scenario's app blocks with the generated YAML."""
    scenario: dict[str, Any] = yaml.safe_load(SCENARIO.read_text())
    apps: dict[str, Any] = {a["name"]: a for a in scenario["spec"]["apps"]}
    problems: list[str] = []
    for name, path in EMBEDDED.items():
        want: Any = yaml.safe_load(path.read_text())
        if apps.get(name) != want:
            problems.append(f"{SCENARIO.name}: app {name} differs from {path.relative_to(ROOT)}")
    order: list[str] = [a["name"] for a in scenario["spec"]["apps"]]
    if order.index("ot-sim") > order.index("helics"):
        problems.append("ot-sim must come before helics in the scenario")
    return problems


def assembled_program_md5() -> str:
    """MD5 of the single-file ST program ``assemble.py`` builds (the one
    ``build_zip.sh`` compiles); also checks the program against the plant."""
    sys.path.insert(0, str(PLC_DIR))
    try:
        import assemble  # noqa: PLC0415 - sibling script, not a package
    finally:
        sys.path.remove(str(PLC_DIR))
    full: str
    problems: list[str]
    full, problems = assemble.assemble("ci")
    if problems:
        raise ValueError("; ".join(problems))
    return hashlib.md5(full.encode()).hexdigest()  # noqa: S324 - OpenPLC's program id


def check_zip() -> list[str]:
    """The committed program ZIP carries this program and these plugin configs.

    A full rebuild needs strucpp (``build_zip.sh --check`` does it); this
    catches the usual drift without Node: the MD5 in ``defines.h`` is the MD5
    of the program as assembled now, ``conf/`` equals the generated plugin
    configs, and the file fits the repository's size limit.
    """
    if not PLC_ZIP.exists():
        return [f"missing {PLC_ZIP.relative_to(ROOT)}; run plc/plc-cellar/build_zip.sh"]
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
            want: str = assembled_program_md5()
            if f'#define PROGRAM_MD5 "{want}"' not in defines:
                problems.append(
                    f"{PLC_ZIP.name}: defines.h PROGRAM_MD5 is not the MD5 ({want}) of "
                    "the assembled plc-cellar program; rerun plc/plc-cellar/build_zip.sh"
                )
        for conf in ("modbus_master.json", "modbus_slave.json"):
            src: Path = PLC_GENERATED / conf
            if f"conf/{conf}" not in names:
                problems.append(f"{PLC_ZIP.name}: missing conf/{conf}")
            elif json.loads(zf.read(f"conf/{conf}")) != json.loads(src.read_text()):
                problems.append(
                    f"{PLC_ZIP.name}: conf/{conf} differs from {src.relative_to(ROOT)}; "
                    "rerun plc/plc-cellar/build_zip.sh"
                )
    return problems


def main(argv: list[str] | None = None) -> int:
    ap: argparse.ArgumentParser = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true")
    args: argparse.Namespace = ap.parse_args(argv)

    problems: list[str] = []
    for c in COPIES:
        if not c.src.exists():
            problems.append(f"missing source {c.src.relative_to(ROOT)}")
            continue
        same: bool = c.dst.exists() and c.dst.read_bytes() == c.src.read_bytes()
        if same:
            continue
        if args.check:
            problems.append(f"{c.dst.relative_to(ROOT)} differs from {c.src.relative_to(ROOT)}")
        else:
            c.dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(c.src, c.dst)
            print(f"copied {c.src.relative_to(ROOT)} -> {c.dst.relative_to(ROOT)}")
    problems += check_embedded()
    problems += check_zip()

    for p in problems:
        print(f"FAIL: {p}", file=sys.stderr)
    if not problems:
        print("ci copies and embedded blocks are in sync")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
