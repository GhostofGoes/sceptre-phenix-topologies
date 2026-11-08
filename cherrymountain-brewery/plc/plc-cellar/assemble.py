#!/usr/bin/env python3
"""Assemble and check the plc-cellar Structured Text program.

The program in ``plc-cellar.st`` leaves a marker comment where the plantgen
``located_vars.st`` VAR block belongs. This script:

1. splices the located variables in at the marker,
2. checks that the alarm limits and delays in the program match
   ``plant/generated/<variant>/alarms.json``, that every alarm bit and every
   spunding setpoint is assigned, and that every temperature valve is driven,
3. writes ``build/plc-cellar-full.st`` (program plus a CONFIGURATION block),
   which ``iec2c`` (matiec) can compile, and
4. runs ``iec2c`` on it when ``--iec2c`` is given.

Usage:
    python assemble.py [--variant ci] [--out build] [--iec2c /path/to/iec2c]
                       [--matiec-lib /path/to/matiec/lib]
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

from pydantic import BaseModel, ConfigDict

HERE: Path = Path(__file__).resolve().parent
PLANT_GENERATED: Path = HERE.parents[1] / "plant" / "generated"
MARKER: str = "(* LOCATED_VARS: assemble.py splices located_vars.st here. *)"
SCAN_INTERVAL: str = "T#100ms"

CONFIGURATION: str = f"""
CONFIGURATION Config0
  RESOURCE Res0 ON PLC
    TASK task0(INTERVAL := {SCAN_INTERVAL}, PRIORITY := 0);
    PROGRAM instance0 WITH task0 : plc_cellar;
  END_RESOURCE
END_CONFIGURATION
"""


class Alarm(BaseModel):
    """One alarm record from alarms.json (only the fields this check uses)."""

    model_config = ConfigDict(extra="ignore")

    name: str
    kind: str
    limit: float
    delay_s: float
    plc_iec: str


class AlarmFile(BaseModel):
    model_config = ConfigDict(extra="ignore")

    alarms: list[Alarm]


def st_name(tag: str) -> str:
    """Tag name as it appears in located_vars.st (dots become underscores)."""
    return tag.replace(".", "_")


def constant(source: str, name: str) -> str:
    """Return the initial value text of ``name`` in the VAR CONSTANT block."""
    m: re.Match[str] | None = re.search(
        rf"^\s*{name}\s*:\s*\w+\s*:=\s*([^;]+);", source, re.MULTILINE
    )
    if m is None:
        raise SystemExit(f"constant {name} not found in plc-cellar.st")
    return m.group(1).strip()


def seconds(text: str) -> float:
    m: re.Match[str] | None = re.fullmatch(r"T#(\d+)s", text)
    if m is None:
        raise SystemExit(f"unsupported TIME literal {text}; use T#<n>s")
    return float(m.group(1))


def check_alarms(source: str, alarms: list[Alarm]) -> list[str]:
    """Compare the ST constants with alarms.json; return the problems found."""
    problems: list[str] = []
    def check(label: str, kind: str, instr: str, limit_const: str, delay_const: str) -> None:
        matches: list[Alarm] = [
            a for a in alarms if a.kind == kind and f".{instr}." in a.name
        ]
        if not matches:
            problems.append(f"{label}: no {kind} alarm on {instr} in alarms.json")
            return
        for a in matches:
            want_limit: int = round(a.limit * 100)
            have_limit: int = int(constant(source, limit_const))
            have_delay: float = seconds(constant(source, delay_const))
            if want_limit != have_limit:
                problems.append(f"{a.name}: limit {want_limit} != {limit_const} {have_limit}")
            if a.delay_s != have_delay:
                problems.append(f"{a.name}: delay {a.delay_s}s != {delay_const} {have_delay}s")

    check("side deviation", "deviation", "TT01", "TEMP_DEV_LIMIT", "TEMP_DEV_DELAY_SIDE")
    check("cone deviation", "deviation", "TT02", "TEMP_DEV_LIMIT", "TEMP_DEV_DELAY_CONE")
    check("side high", "high", "TT01", "TEMP_HIGH_LIMIT", "TEMP_HIGH_DELAY")
    check("pressure high", "high", "PT01", "PRESS_HIGH_LIMIT", "PRESS_HIGH_DELAY")
    check("pressure low", "low", "PT01", "PRESS_LOW_LIMIT", "PRESS_LOW_DELAY")

    for a in alarms:
        target: str = st_name(a.name)
        if not re.search(rf"^\s*{target}\s*:=", source, re.MULTILINE):
            problems.append(f"{a.name}: alarm bit is never assigned")
    return problems


def check_outputs(source: str, located: str) -> list[str]:
    """Every %QW spunding setpoint and %QX valve command must be assigned."""
    problems: list[str] = []
    for m in re.finditer(r"^\s*(\w+)\s+AT\s+%(?:QW|QX)10\d", located, re.MULTILINE):
        var: str = m.group(1)
        if not re.search(rf"^\s*{var}\s*:=", source, re.MULTILINE):
            problems.append(f"{var}: RIO output is never assigned")
    return problems


def assemble(variant: str) -> tuple[str, list[str]]:
    gen: Path = PLANT_GENERATED / variant
    located: str = (gen / "openplc" / "plc-cellar" / "located_vars.st").read_text()
    source: str = (HERE / "plc-cellar.st").read_text()
    if MARKER not in source:
        raise SystemExit("marker comment missing from plc-cellar.st")
    alarms: AlarmFile = AlarmFile.model_validate_json((gen / "alarms.json").read_text())
    problems: list[str] = check_alarms(source, alarms.alarms)
    problems += check_outputs(source, located)
    full: str = source.replace(MARKER, located.strip()) + CONFIGURATION
    return full, problems


def main(argv: list[str] | None = None) -> int:
    ap: argparse.ArgumentParser = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--variant", default="ci")
    ap.add_argument("--out", type=Path, default=HERE / "build")
    ap.add_argument("--iec2c", type=Path, help="matiec compiler to run on the result")
    ap.add_argument("--matiec-lib", type=Path, help="matiec lib directory (-I)")
    args: argparse.Namespace = ap.parse_args(argv)

    full, problems = assemble(args.variant)
    for p in problems:
        print(f"FAIL: {p}", file=sys.stderr)
    if problems:
        return 1
    args.out.mkdir(parents=True, exist_ok=True)
    out_file: Path = args.out / "plc-cellar-full.st"
    out_file.write_text(full)
    print(f"wrote {out_file}")

    if args.iec2c is not None:
        cmd: list[str] = [str(args.iec2c)]
        if args.matiec_lib is not None:
            cmd += ["-I", str(args.matiec_lib)]
        cmd += ["-T", str(args.out / "c"), str(out_file)]
        (args.out / "c").mkdir(exist_ok=True)
        result: subprocess.CompletedProcess[str] = subprocess.run(
            cmd, capture_output=True, text=True, check=False
        )
        sys.stdout.write(result.stdout)
        sys.stderr.write(result.stderr)
        if result.returncode != 0:
            return result.returncode
        print("iec2c: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
