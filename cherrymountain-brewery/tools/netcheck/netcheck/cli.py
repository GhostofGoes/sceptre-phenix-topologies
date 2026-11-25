"""Command line: ``python -m netcheck`` checks the committed configs."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from netcheck import checks
from netcheck.model import ROOT


def main(argv: Sequence[str] | None = None) -> int:
    ap: argparse.ArgumentParser = argparse.ArgumentParser(
        description="Check the topology, scenario, and workflow against the "
        "DESIGN.md IP plan (10.2) and conduit matrix (10.3)."
    )
    ap.add_argument("--root", type=Path, default=ROOT, help="cherrymountain-brewery/")
    ap.add_argument(
        "--variant",
        choices=["full", "lite", "ci", "all"],
        default="all",
        help="variant to check (default: all)",
    )
    ap.add_argument("-q", "--quiet", action="store_true", help="hide notes")
    args: argparse.Namespace = ap.parse_args(argv)

    findings: list[checks.Finding]
    if args.variant == "all":
        findings = checks.run_all(args.root)
    elif args.variant == "ci":
        findings = checks.run_ci(args.root)
    else:
        findings = checks.run_variant(args.variant, args.root)

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
