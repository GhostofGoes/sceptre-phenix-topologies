#!/usr/bin/env python3
"""Pack the OpenPLC v4 program ZIP for plc-cellar, byte-for-byte reproducibly.

Layout (verified against OpenPLC runtime v4.3.2, ``scripts/compile.sh`` and
``webserver/plcapp_management.py``):

    generated.cpp  generated.hpp  generated_debug.cpp   strucpp output
    defines.h                                           ``PROGRAM_MD5``
    strucpp_runtime/include/**                          strucpp runtime headers
    conf/<plugin>.json                                  one file per plugin

Entries are sorted and carry a fixed timestamp and mode, so the same inputs
always give the same ZIP. Called by ``build_zip.sh``.

Usage:
    python pack_zip.py --generated DIR --runtime-include DIR --conf DIR \\
        --md5 HEX --out plc-cellar.zip
"""

from __future__ import annotations

import argparse
import re
import sys
import zipfile
from pathlib import Path

from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

#: Fixed timestamp for every entry (the earliest the ZIP format allows).
ZIP_EPOCH: tuple[int, int, int, int, int, int] = (1980, 1, 1, 0, 0, 0)
#: The strucpp output files the runtime compiles.
GENERATED_FILES: tuple[str, ...] = ("generated.cpp", "generated.hpp", "generated_debug.cpp")
#: Plugin configs a ZIP for a modbus_master + modbus_slave PLC must carry.
REQUIRED_CONF: tuple[str, ...] = ("modbus_master.json", "modbus_slave.json")
#: The repository's check-added-large-files limit.
MAX_ZIP_BYTES: int = 4096 * 1024


class PackInputs(BaseModel):
    """Everything that goes into the ZIP."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    generated: Path
    runtime_include: Path
    conf: Path
    md5: str = Field(pattern=r"^[0-9a-f]{32}$")


def collect(inputs: PackInputs) -> dict[str, bytes]:
    """Return ``{archive name: bytes}`` for every entry, in sorted order."""
    entries: dict[str, bytes] = {}
    for name in GENERATED_FILES:
        entries[name] = (inputs.generated / name).read_bytes()
    entries["defines.h"] = f'#pragma once\n#define PROGRAM_MD5 "{inputs.md5}"\n'.encode()
    include_files: list[Path] = sorted(p for p in inputs.runtime_include.rglob("*") if p.is_file())
    if not include_files:
        raise SystemExit(f"no strucpp runtime headers under {inputs.runtime_include}")
    for path in include_files:
        rel: str = path.relative_to(inputs.runtime_include).as_posix()
        entries[f"strucpp_runtime/include/{rel}"] = path.read_bytes()
    conf_files: dict[str, Path] = {p.name: p for p in inputs.conf.glob("*.json")}
    missing: list[str] = [n for n in REQUIRED_CONF if n not in conf_files]
    if missing:
        raise SystemExit(f"missing plugin configs in {inputs.conf}: {', '.join(missing)}")
    for name, path in sorted(conf_files.items()):
        entries[f"conf/{name}"] = path.read_bytes()
    return dict(sorted(entries.items()))


def write_zip(entries: dict[str, bytes], out: Path) -> None:
    """Write ``entries`` as a deterministic ZIP. It is written next to ``out`` and
    moved into place only once it is within the size limit, so a failed build never
    leaves a bad ZIP where the committed one was."""
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp: Path = out.with_name(out.name + ".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for name, data in entries.items():
            info: zipfile.ZipInfo = zipfile.ZipInfo(name, date_time=ZIP_EPOCH)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            info.create_system = 3
            zf.writestr(info, data, compresslevel=9)
    size: int = tmp.stat().st_size
    if size > MAX_ZIP_BYTES:
        tmp.unlink()
        raise SystemExit(f"{out} would be {size} bytes, over the {MAX_ZIP_BYTES} byte limit")
    tmp.replace(out)


def program_md5(defines_h: bytes) -> str:
    """Extract ``PROGRAM_MD5`` from a ``defines.h``."""
    m: re.Match[str] | None = re.search(r'#define PROGRAM_MD5 "([0-9a-f]{32})"', defines_h.decode())
    if m is None:
        raise ValueError("defines.h has no PROGRAM_MD5")
    return m.group(1)


def main(argv: list[str] | None = None) -> int:
    ap: argparse.ArgumentParser = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--generated", type=Path, required=True)
    ap.add_argument("--runtime-include", type=Path, required=True)
    ap.add_argument("--conf", type=Path, required=True)
    ap.add_argument("--md5", required=True)
    ap.add_argument("--out", type=Path, required=True)
    args: argparse.Namespace = ap.parse_args(argv)
    inputs: PackInputs = PackInputs(
        generated=args.generated,
        runtime_include=args.runtime_include,
        conf=args.conf,
        md5=args.md5,
    )
    entries: dict[str, bytes] = collect(inputs)
    write_zip(entries, args.out)
    print(f"wrote {args.out} ({args.out.stat().st_size} bytes, {len(entries)} entries)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
