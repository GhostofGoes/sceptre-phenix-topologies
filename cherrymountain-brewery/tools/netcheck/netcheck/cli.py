"""Command line: ``python -m netcheck`` checks the committed configs."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from netcheck import checks
from netcheck.model import ROOT


def _site_codes(root: Path) -> list[str]:
    """The sites of plant/sites.yaml, or none where the tree has no such file."""
    if not (root / "plant" / "sites.yaml").is_file():
        return []
    # Imported here: only a site needs plantgen on the import path.
    from netcheck import site  # noqa: PLC0415

    return site.codes(root)


def main(argv: Sequence[str] | None = None) -> int:
    ap: argparse.ArgumentParser = argparse.ArgumentParser(
        description="Check the topology, scenario, and workflow against the "
        "DESIGN.md IP plan (10.2) and conduit matrix (10.3)."
    )
    ap.add_argument("--root", type=Path, default=ROOT, help="cherrymountain-brewery/")
    ap.add_argument(
        "--variant",
        choices=["full", "lite", "ci", "all"],
        default=None,
        help="variant to check (default: all, and then every site)",
    )
    ap.add_argument(
        "--site",
        default=None,
        help="a site code of plant/sites.yaml, or `all`: check that site alone "
        "(its workflow, its topology file, and its scenario)",
    )
    ap.add_argument("-q", "--quiet", action="store_true", help="hide notes")
    args: argparse.Namespace = ap.parse_args(argv)

    known: list[str] = _site_codes(args.root)
    variant: str | None = args.variant
    sites: list[str] = []
    if args.site is not None:
        sites = known if args.site in (None, "all") else [args.site]
        if args.site not in (None, "all", *known):
            print(f"netcheck: no site {args.site!r}; the sites are {known}", file=sys.stderr)
            return 2
    elif variant is None:
        variant, sites = "all", known

    findings: list[checks.Finding] = []
    if variant == "all":
        findings += checks.run_all(args.root)
    elif variant == "ci":
        findings += checks.run_ci(args.root)
    elif variant is not None:
        findings += checks.run_variant(variant, args.root)
    for code in sites:
        findings += checks.run_site(code, args.root)

    errors: list[checks.Finding] = [f for f in findings if f.severity == "error"]
    for f in findings:
        if f.severity == "note" and args.quiet:
            continue
        print(f"{f.severity.upper():5} [{f.check}] {f.message}", file=sys.stderr)
    if errors:
        print(f"netcheck: {len(errors)} error(s)", file=sys.stderr)
        return 1
    print("netcheck: topology, scenario, and workflow match the plan and the conduit matrix")
    return 0
