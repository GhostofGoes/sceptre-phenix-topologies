#!/usr/bin/env python3
"""Assemble and check a PLC program for the full and lite variants.

Each PLC has a hand-written Structured Text program under ``plc/<plc>/`` with
two marker comments. This script:

1. splices the plantgen ``located_vars.st`` VAR block, plus a VAR block with
   one ``CmAlarm`` instance per alarm of the PLC, in at the LOCATED_VARS
   marker,
2. generates the alarm logic from ``plant/generated/<variant>/alarms.json``
   (limit, deadband, on-delay, off-delay, suppression; DESIGN.md 6.5) at the
   ALARMS marker, so the program and the FUXA alarm list cannot drift,
3. checks that the hand-written part assigns every RIO output and every
   PLC-computed status tag and never assigns an alarm bit itself,
4. prepends ``cm_alarm.st`` and appends the CONFIGURATION block, and writes
   ``<out>/<plc>-full.st``, which ``gen_program.mjs`` (strucpp) compiles.

The ci variant's plc-cellar program keeps its own ``plc/plc-cellar/assemble.py``
(hand-written alarm constants, checked against alarms.json).

Usage:
    python plcbuild.py PLC [--variant full] [--out DIR] [--check-only]
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict

HERE: Path = Path(__file__).resolve().parent
PLC_ROOT: Path = HERE.parent
GENERATED: Path = PLC_ROOT.parent / "plant" / "generated"
LOCATED_MARKER: str = "(* LOCATED_VARS: plcbuild.py splices the located variables and alarm instances here. *)"
ALARM_MARKER: str = "(* ALARMS: plcbuild.py generates the alarm logic here. *)"
SCAN_INTERVAL: str = "T#100ms"
#: Variants this builder serves (ci has its own assemble.py).
VARIANTS: tuple[str, ...] = ("full", "lite")
#: Program source per PLC, relative to plc/<plc>/ (default: <plc>.st).
SOURCES: dict[str, str] = {"plc-cellar": "plc-cellar-plant.st"}

AlarmKind = Literal["high", "low", "highhigh", "lowlow", "deviation", "state"]


class Alarm(BaseModel):
    """One alarms.json record (the fields the generator uses)."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore")

    name: str
    source: str
    kind: AlarmKind
    limit: float
    setpoint: str | None = None
    delay_s: float
    deadband: float = 0.0
    off_delay_s: float = 0.0
    suppress: str | None = None
    plc: str | None = None
    plc_iec: str | None = None


class AlarmFile(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore")

    alarms: list[Alarm]


class Tag(BaseModel):
    """One tags.json record (the fields the checks use)."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore")

    name: str
    type: str
    scaling: int = 0
    source: str = "io"
    plc: str | None = None


class TagFile(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore")

    tags: list[Tag]


class Located(BaseModel):
    """One located variable: ``NAME AT %IW100 : UINT;``."""

    name: str
    iec: str
    iec_type: str


def st_name(tag: str) -> str:
    """Tag name as plantgen writes it in located_vars.st (dots become underscores)."""
    return tag.replace(".", "_")


def parse_located(text: str) -> dict[str, Located]:
    out: dict[str, Located] = {}
    for m in re.finditer(r"^\s*(\w+)\s+AT\s+(%[IQM][XWDL][\d.]+)\s*:\s*(\w+)\s*;", text, re.MULTILINE):
        out[m.group(1)] = Located(name=m.group(1), iec=m.group(2), iec_type=m.group(3))
    return out


def time_literal(seconds: float) -> str:
    ms: int = round(seconds * 1000)
    return f"T#{ms // 1000}s" if ms % 1000 == 0 else f"T#{ms}ms"


def as_dint(var: Located) -> str:
    if var.iec_type == "BOOL":
        raise ValueError(f"{var.name} is a BOOL, not a number")
    return f"{var.iec_type}_TO_DINT({var.name})"


def raw(value: float, scaling: int) -> int:
    return round(value * 10**scaling)


def alarm_call(idx: int, alarm: Alarm, located: dict[str, Located], tags: dict[str, Tag]) -> str:
    """The ST that evaluates one alarm and writes its bit."""
    src: Located = located[st_name(alarm.source)]
    scaling: int = tags[alarm.source].scaling
    bit: str = st_name(alarm.name)
    lim: int = raw(alarm.limit, scaling)
    db: int = raw(alarm.deadband, scaling)
    match alarm.kind:
        case "high" | "highhigh":
            on, clear = f"{as_dint(src)} > {lim}", f"{as_dint(src)} <= {lim - db}"
        case "low" | "lowlow":
            on, clear = f"{as_dint(src)} < {lim}", f"{as_dint(src)} >= {lim + db}"
        case "deviation":
            if alarm.setpoint is None:
                raise ValueError(f"{alarm.name}: deviation alarm without a setpoint")
            sp: Located = located[st_name(alarm.setpoint)]
            err: str = f"ABS({as_dint(src)} - {as_dint(sp)})"
            on, clear = f"{err} > {lim}", f"{err} <= {lim - db}"
        case "state":
            if src.iec_type == "BOOL":
                on = src.name if alarm.limit else f"NOT {src.name}"
            else:
                on = f"{as_dint(src)} = {round(alarm.limit)}"
            clear = f"NOT ({on})"
    suppress: str = "FALSE"
    if alarm.suppress is not None:
        suppress = f"NOT {located[st_name(alarm.suppress)].name}"
    units: str = f"limit {alarm.limit:g}, deadband {alarm.deadband:g}"
    return (
        f"  (* {alarm.name}: {alarm.kind}, {units}, on {alarm.delay_s:g} s, off {alarm.off_delay_s:g} s *)\n"
        f"  alm_{idx}(COND_ON := {on},\n"
        f"    COND_CLEAR := {clear},\n"
        f"    SUPPRESS := {suppress}, ON_DELAY := {time_literal(alarm.delay_s)}, "
        f"OFF_DELAY := {time_literal(alarm.off_delay_s)});\n"
        f"  {bit} := alm_{idx}.ALM;\n"
    )


def program_name(plc: str) -> str:
    return plc.replace("-", "_")


def configuration(plc: str) -> str:
    return f"""
CONFIGURATION Config0
  RESOURCE Res0 ON PLC
    TASK task0(INTERVAL := {SCAN_INTERVAL}, PRIORITY := 0);
    PROGRAM instance0 WITH task0 : {program_name(plc)};
  END_RESOURCE
END_CONFIGURATION
"""


def assigned(source: str, var: str) -> bool:
    return re.search(rf"^\s*{re.escape(var)}\s*:=", source, re.MULTILINE) is not None


def check(source: str, located: dict[str, Located], tags: dict[str, Tag], alarms: list[Alarm]) -> list[str]:
    """Problems in the hand-written program (before the alarm logic is added)."""
    problems: list[str] = []
    alarm_bits: set[str] = {st_name(a.name) for a in alarms}
    for name in sorted(alarm_bits):
        if assigned(source, name):
            problems.append(f"{name}: alarm bits are generated; the program must not assign them")
    by_st: dict[str, Tag] = {st_name(t.name): t for t in tags.values()}
    for var in located.values():
        if var.name in alarm_bits:
            continue
        rio_out: bool = bool(re.fullmatch(r"%Q[XW]1\d\d(\.\d)?", var.iec))
        tag: Tag | None = by_st.get(var.name)
        computed: bool = tag is not None and tag.source == "plc" and tag.type in ("analog-read", "binary-read")
        if (rio_out or computed) and not assigned(source, var.name):
            kind: str = "RIO output" if rio_out else "PLC-computed tag"
            problems.append(f"{var.name}: {kind} is never assigned")
    return problems


def assemble(plc: str, variant: str) -> tuple[str, list[str]]:
    gen: Path = GENERATED / variant
    located_text: str = (gen / "openplc" / plc / "located_vars.st").read_text(encoding="utf-8")
    located: dict[str, Located] = parse_located(located_text)
    src_path: Path = PLC_ROOT / plc / SOURCES.get(plc, f"{plc}.st")
    source: str = src_path.read_text(encoding="utf-8")
    for marker in (LOCATED_MARKER, ALARM_MARKER):
        if source.count(marker) != 1:
            raise SystemExit(f"{src_path.name}: expected exactly one marker {marker}")
    alarms: list[Alarm] = [
        a for a in AlarmFile.model_validate_json((gen / "alarms.json").read_text(encoding="utf-8")).alarms
        if a.plc == plc and a.plc_iec is not None
    ]
    tags: dict[str, Tag] = {
        t.name: t for t in TagFile.model_validate_json((gen / "tags.json").read_text(encoding="utf-8")).tags
    }
    problems: list[str] = check(source, located, tags, alarms)
    instances: str = "\n".join(f"    alm_{i} : CmAlarm; (* {a.name} *)" for i, a in enumerate(alarms))
    var_block: str = located_text.strip()
    if alarms:
        var_block += "\n  VAR\n" + instances + "\n  END_VAR"
    calls: list[str] = []
    for i, a in enumerate(alarms):
        try:
            calls.append(alarm_call(i, a, located, tags))
        except (KeyError, ValueError) as ex:
            problems.append(f"{a.name}: cannot generate the alarm logic ({ex})")
    body: str = "(* Generated from alarms.json by plcbuild.py. *)\n" + "".join(calls)
    common: str = (HERE / "cm_alarm.st").read_text(encoding="utf-8")
    full: str = (
        common
        + "\n"
        + source.replace(LOCATED_MARKER, var_block).replace(ALARM_MARKER, body.rstrip())
        + configuration(plc)
    )
    return full, problems


def main(argv: list[str] | None = None) -> int:
    ap: argparse.ArgumentParser = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("plc")
    ap.add_argument("--variant", default="full", choices=VARIANTS)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--check-only", action="store_true", help="run the checks, write nothing")
    args: argparse.Namespace = ap.parse_args(argv)
    full, problems = assemble(args.plc, args.variant)
    for p in problems:
        print(f"FAIL: {args.plc} ({args.variant}): {p}", file=sys.stderr)
    if problems:
        return 1
    if args.check_only:
        print(f"PASS: {args.plc} ({args.variant})")
        return 0
    out_dir: Path = args.out or PLC_ROOT / args.plc / "build"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file: Path = out_dir / f"{args.plc}-full.st"
    out_file.write_text(full, encoding="utf-8")
    print(f"wrote {out_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
