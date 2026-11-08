"""Command line: generate, check, or verify committed plantgen outputs."""

from __future__ import annotations

import argparse
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

from plantgen.allocate import allocate
from plantgen.checks import file_checks, plant_checks
from plantgen.model import PlantError, load
from plantgen.render import render_all

#: cherrymountain-brewery/ (this file is tools/plantgen/plantgen/cli.py).
TOPOLOGY_DIR: Path = Path(__file__).resolve().parents[3]
DEFAULT_PLANT: Path = TOPOLOGY_DIR / "plant" / "plant.yaml"


def generate(plant_path: Path, variant: str) -> dict[str, str]:
    """Load, allocate, render, and check; raises PlantError on any problem."""
    plant = load(plant_path, variant)
    alloc = allocate(plant)
    _raise_if(plant_checks(plant, alloc))
    files = render_all(plant, alloc)
    _raise_if(file_checks(plant, alloc, files))
    return files


def _raise_if(errors: list[str]) -> None:
    if errors:
        raise PlantError("consistency checks failed:\n  " + "\n  ".join(errors))


def write(files: dict[str, str], out_dir: Path) -> None:
    """Write ``files`` under ``out_dir``, removing stale generated files."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in sorted(out_dir.rglob("*"), reverse=True):
        rel = old.relative_to(out_dir).as_posix()
        if old.is_file() and rel not in files:
            old.unlink()
        elif old.is_dir() and not any(old.iterdir()):
            old.rmdir()
    for rel, text in files.items():
        path = out_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")


def diff(expected: Path, committed: Path) -> list[str]:
    """Paths that differ between two output trees (relative POSIX paths)."""

    def tree(root: Path) -> dict[str, bytes]:
        if not root.is_dir():
            return {}
        return {
            p.relative_to(root).as_posix(): p.read_bytes()
            for p in root.rglob("*")
            if p.is_file()
        }

    want, have = tree(expected), tree(committed)
    out = [f"missing: {p}" for p in sorted(want.keys() - have.keys())]
    out += [f"unexpected: {p}" for p in sorted(have.keys() - want.keys())]
    out += [
        f"stale: {p}" for p in sorted(want.keys() & have.keys()) if want[p] != have[p]
    ]
    return out


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for ``python -m plantgen`` and the ``plantgen`` script."""
    ap = argparse.ArgumentParser(prog="plantgen", description=__doc__)
    ap.add_argument("--plant", type=Path, default=DEFAULT_PLANT, help="plant.yaml")
    ap.add_argument("--variant", default="ci", help="variant name (default: ci)")
    ap.add_argument(
        "--out",
        type=Path,
        help="output directory (default: <plant dir>/generated/<variant>)",
    )
    ap.add_argument(
        "--check",
        action="store_true",
        help="regenerate to a temp dir and fail if committed outputs differ",
    )
    args = ap.parse_args(argv)
    plant_path: Path = args.plant
    out_dir: Path = args.out or plant_path.parent / "generated" / args.variant
    try:
        files = generate(plant_path, args.variant)
    except PlantError as exc:
        print(f"plantgen: {exc}", file=sys.stderr)
        return 1
    if args.check:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            write(files, tmp_dir)
            problems = diff(tmp_dir, out_dir)
        if problems:
            print(f"plantgen: {out_dir} is out of date:", file=sys.stderr)
            for p in problems:
                print(f"  {p}", file=sys.stderr)
            print("run `python -m plantgen` and commit the result", file=sys.stderr)
            return 1
        print(f"plantgen: {out_dir} is up to date ({len(files)} files)")
        return 0
    write(files, out_dir)
    print(f"plantgen: wrote {len(files)} files to {out_dir}")
    return 0
