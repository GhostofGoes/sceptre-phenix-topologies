"""Write the wan variant's phenix files, or check that the committed ones are current.

  python -m wangen            write the files
  python -m wangen --check    exit 1 when a file differs from what wan.yaml gives
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Final

from wangen import model, phenix

#: cherrymountain-brewery/
ROOT: Final[Path] = Path(__file__).resolve().parents[3]
OUTPUTS: Final[dict[str, Path]] = {
    "topology": ROOT / "phenix-configs" / "topology-wan.yml",
    "scenario": ROOT / "phenix-configs" / "scenario-wan.yml",
    "workflow": ROOT / "phenix-wan.yml",
}


def stale() -> list[Path]:
    """The output files that differ from what the loaded model gives."""
    return [
        path
        for kind, path in OUTPUTS.items()
        if not path.is_file() or path.read_text(encoding="utf-8") != phenix.render(kind)
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wangen", description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="compare the committed files, and write nothing")
    args = parser.parse_args(argv)
    model.load()
    if args.check:
        differ = stale()
        for path in differ:
            print(f"{path.relative_to(ROOT)} is not current: run `python -m wangen` in tools/wangen", file=sys.stderr)
        return 1 if differ else 0
    for kind, path in OUTPUTS.items():
        path.write_text(phenix.render(kind), encoding="utf-8")
        print(f"wrote {path.relative_to(ROOT)}")
    return 0
