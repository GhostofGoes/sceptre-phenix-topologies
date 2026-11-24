"""Command line: generate, check, or verify committed plantgen outputs."""

from __future__ import annotations

import argparse
import functools
import shutil
import sys
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path

from plantgen import fleet, sites
from plantgen.allocate import allocate
from plantgen.checks import file_checks, plant_checks
from plantgen.model import Plant, PlantError, PlantFile, expand, load, load_file
from plantgen.render import render_all
from plantgen.sites import SiteSpec

#: cherrymountain-brewery/ (this file is tools/plantgen/plantgen/cli.py).
TOPOLOGY_DIR: Path = Path(__file__).resolve().parents[3]
DEFAULT_PLANT: Path = TOPOLOGY_DIR / "plant" / "plant.yaml"


def generate(plant_path: Path, variant: str) -> dict[str, str]:
    """Load, allocate, render, and check; raises PlantError on any problem."""
    return render_checked(load(plant_path, variant))


def generate_site(site: SiteSpec, plant: PlantFile) -> dict[str, str]:
    """The files of one site of the fleet; raises PlantError on any problem.

    `plant` is plant.yaml. The site's own plant description goes through the
    steps that a variant goes through.
    """
    expanded = fleet.expand_site(site, plant)
    try:
        return render_checked(expanded)
    except PlantError as exc:
        raise PlantError(f"site {site.code}: {exc}") from exc


def _variant_files(plant: PlantFile, variant: str) -> dict[str, str]:
    """The files of one variant of a plant file that is loaded already."""
    return render_checked(expand(plant, variant))


def render_checked(plant: Plant) -> dict[str, str]:
    """Allocate, render, and check an expanded plant; raises PlantError."""
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
    ap.add_argument(
        "--variant",
        default="all",
        help="variant name, or `all` (default) for every variant in plant.yaml "
        "and, without --site, every site in sites.yaml",
    )
    ap.add_argument(
        "--site",
        help="site code in sites.yaml: that site only (and the --variant, if given)",
    )
    ap.add_argument(
        "--out",
        type=Path,
        help="output directory for one variant or one site (default: "
        "<plant dir>/generated/<variant>, <plant dir>/generated/fleet/<site>)",
    )
    ap.add_argument(
        "--check",
        action="store_true",
        help="regenerate to a temp dir and fail if committed outputs differ",
    )
    args = ap.parse_args(argv)
    plant_path: Path = args.plant
    every: bool = args.variant == "all" and args.site is None
    single: bool = (args.variant == "all") != (args.site is None)
    if args.out is not None and not single:
        print("plantgen: --out needs a single --variant or --site", file=sys.stderr)
        return 2
    # plant.yaml is read once, for the variants and for the sites.
    try:
        plant = load_file(plant_path)
    except PlantError as exc:
        print(f"plantgen: {exc}", file=sys.stderr)
        return 1
    if every:
        variants = list(plant.variants)
    else:
        variants = [] if args.variant == "all" else [args.variant]
    status, found = _sites(plant_path, plant)
    generated: Path = plant_path.parent / "generated"
    if found is not None and fleet.DIRECTORY in plant.variants:
        print(
            f"plantgen: the variant name {fleet.DIRECTORY!r} is the directory of "
            "the sites under generated/; give the variant another name",
            file=sys.stderr,
        )
        return 1
    for variant in variants:
        status = max(
            status,
            _run(
                variant,
                functools.partial(_variant_files, plant, variant),
                args.out or generated / variant,
                check=args.check,
            ),
        )
    if args.site is None and not every:
        return status
    if found is None:
        if args.site is not None and status == 0:
            print(f"plantgen: {plant_path.parent} has no sites.yaml", file=sys.stderr)
            return 1
        return status
    chosen = [site for site in found if args.site in (None, site.code)]
    if not chosen:
        codes = [site.code for site in found]
        print(
            f"plantgen: sites.yaml has no site {args.site!r}; it has {codes}",
            file=sys.stderr,
        )
        return 1
    for site in chosen:
        status = max(
            status,
            _run(
                f"site {site.code}",
                functools.partial(generate_site, site, plant),
                args.out or generated / fleet.DIRECTORY / site.code,
                check=args.check,
            ),
        )
    if every:
        status = max(
            status, _other_sites(generated / fleet.DIRECTORY, found, args.check)
        )
    return status


def _sites(plant_path: Path, plant: PlantFile) -> tuple[int, list[SiteSpec] | None]:
    """Check sites.yaml, when the plant file has one next to it.

    The sites of the fleet must fit plant.yaml and wan/wan.yaml. Returns the
    exit status and the sites. Without a sites.yaml, or with one that does not
    fit, there are no sites to write (None).
    """
    path = plant_path.parent / "sites.yaml"
    if not path.is_file():
        return 0, None
    try:
        found = sites.load(path, plant, plant_path.parent.parent / "wan" / "wan.yaml")
    except PlantError as exc:
        print(f"plantgen: {exc}", file=sys.stderr)
        return 1, None
    print(f"plantgen: {path.name} fits plant.yaml and wan.yaml ({len(found)} sites)")
    return 0, found


def _other_sites(directory: Path, found: list[SiteSpec], check: bool) -> int:
    """Remove, or with `check` report, what sites.yaml no longer has a site for."""
    codes = {site.code for site in found}
    if not directory.is_dir():
        return 0
    others = sorted(path for path in directory.iterdir() if path.name not in codes)
    if not others:
        return 0
    if check:
        print(f"plantgen: {directory} is out of date:", file=sys.stderr)
        for path in others:
            print(f"  unexpected: {path.name}", file=sys.stderr)
        print("run `python -m plantgen` and commit the result", file=sys.stderr)
        return 1
    for path in others:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        print(f"plantgen: removed {path}, which sites.yaml has no site for")
    return 0


def _run(
    label: str, make: Callable[[], dict[str, str]], out_dir: Path, *, check: bool
) -> int:
    """Generate one variant or one site, and write or compare its files."""
    try:
        files = make()
    except PlantError as exc:
        # A site's message names the site already.
        text = str(exc) if str(exc).startswith(label) else f"{label}: {exc}"
        print(f"plantgen: {text}", file=sys.stderr)
        return 1
    if check:
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
