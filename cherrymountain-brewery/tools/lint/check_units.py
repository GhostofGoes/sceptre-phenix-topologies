#!/usr/bin/env python3
"""Verify systemd units with ``systemd-analyze``.

Checks every ``*.service`` file given, and every service unit that a phenix
Image config writes with a heredoc (``cat > /etc/systemd/system/x.service``).
The verbatim ``POSTBUILD_*`` defaults of an image are skipped (see
``image_scripts``).

For each unit this runs:

* ``systemd-analyze verify``: syntax, unknown or deprecated directives, and
  ordering and dependency mistakes. The one finding it is told to ignore is a
  missing ``ExecStart=`` binary, because the binaries exist only in the built
  image or VM, not in the repository.
* ``systemd-analyze security --offline --threshold``: fails when the unit's
  exposure score is above the limit, which keeps the hardening directives from
  being removed. Units that wrap an upstream project's own unit file are
  listed in ``UPSTREAM_UNITS`` and are only verified.

``systemd-analyze`` is part of systemd (the ``systemd`` package on Debian and
Ubuntu); the hook cannot install it. When it is missing, the hook prints a
warning and passes, so a commit on macOS or Windows is not blocked. When the
``CI`` environment variable is set, a missing ``systemd-analyze`` fails the
hook. The dev container (``.devcontainer/``) has it.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from image_scripts import embedded_units

#: Fail when ``systemd-analyze security`` scores a unit above this (0 safest, 10
#: most exposed). The units injected into VMs score 1.2 to 1.3.
DEFAULT_THRESHOLD: float = 3.0
#: Per-unit limits for units that cannot take every hardening directive.
THRESHOLDS: dict[str, float] = {
    # Long-running servers in the images. They score 3.2 and 4.1: V8 (FUXA) needs
    # executable memory, and ot-sim binds Modbus on port 502 and writes outside
    # a locked-down tree, so neither can use every directive.
    "fuxa.service": 3.5,
    "ot-sim.service": 4.5,
}
#: Units that mirror an unmodified upstream unit file. They are verified only.
UPSTREAM_UNITS: frozenset[str] = frozenset({"openplc-runtime.service"})

_MISSING_BINARY: re.Pattern[str] = re.compile(
    r"Command \S+ is not executable: No such file or directory"
)
#: ``systemd-analyze security --threshold`` needs systemd 250.
_MIN_SECURITY_VERSION: int = 250


def systemd_version(analyze: str) -> int:
    """Return the major version of the installed systemd."""
    out: str = subprocess.run(
        [analyze, "--version"], capture_output=True, text=True, check=True
    ).stdout
    return int(out.split()[1])


def verify(analyze: str, unit: Path) -> list[str]:
    """Return the ``systemd-analyze verify`` findings, minus missing binaries."""
    proc: subprocess.CompletedProcess[str] = subprocess.run(
        [analyze, "verify", "--man=no", str(unit)],
        capture_output=True,
        text=True,
        check=False,
    )
    # systemd-analyze also loads every unit on the machine and complains about
    # those (the runner's xfs_scrub and walinuxagent units, for example). Keep
    # only the findings about this unit.
    lines: list[str] = [
        line.replace(str(unit), unit.name)
        for line in (proc.stdout + proc.stderr).splitlines()
    ]
    # verify exits 1 when it has findings; anything else (a crash, a signal, a
    # usage error) must not read as a clean unit.
    if proc.returncode not in (0, 1):
        return [f"{unit.name}: systemd-analyze verify exited {proc.returncode}"]
    return list(
        dict.fromkeys(
            line
            for line in lines
            if line.startswith(unit.name) and not _MISSING_BINARY.search(line)
        )
    )


def security(analyze: str, unit: Path, threshold: float) -> list[str]:
    """Return the exposure findings, empty when the score is within the limit."""
    proc: subprocess.CompletedProcess[str] = subprocess.run(
        [
            analyze,
            "security",
            "--offline=true",
            "--no-pager",
            f"--threshold={round(threshold * 10)}",  # systemd scales 0-10 to 0-100
            str(unit),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode == 0:
        return []
    overall: list[str] = [
        line for line in proc.stdout.splitlines() if "Overall exposure" in line
    ]
    return [
        *overall,
        f"{unit.name}: exposure is above {threshold}; add hardening directives",
    ]


def check_unit(
    analyze: str, name: str, text: str, origin: str, *, can_score: bool
) -> list[str]:
    """Check one unit's text. ``origin`` prefixes each finding."""
    findings: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        unit: Path = Path(tmp) / name
        unit.write_text(text, encoding="utf-8")
        findings.extend(f"{origin}: {line}" for line in verify(analyze, unit))
        if can_score and name not in UPSTREAM_UNITS:
            limit: float = THRESHOLDS.get(name, DEFAULT_THRESHOLD)
            findings.extend(f"{origin}: {line}" for line in security(analyze, unit, limit))
    return findings


def main(argv: list[str]) -> int:
    analyze: str | None = shutil.which("systemd-analyze")
    if analyze is None:
        if os.environ.get("CI"):
            print(
                "systemd-analyze not found. Install systemd, for example:\n"
                "  sudo apt-get install systemd"
            )
            return 2
        print(
            "warning: systemd-analyze not found, so the units are not checked "
            "(CI checks them). To check them here, run:\n"
            "  make container-run CMD='prek run cm-systemd-units --all-files'"
        )
        return 0
    can_score: bool = systemd_version(analyze) >= _MIN_SECURITY_VERSION
    if not can_score:
        print("warning: systemd is older than 250; skipping the exposure check")
    findings: list[str] = []
    for arg in argv:
        path: Path = Path(arg)
        if path.suffix == ".service":
            findings.extend(
                check_unit(
                    analyze,
                    path.name,
                    path.read_text(encoding="utf-8"),
                    str(path),
                    can_score=can_score,
                )
            )
        else:
            for unit in embedded_units(path):
                origin: str = f"{path}:{unit.first_line} [{unit.script}]"
                findings.extend(
                    check_unit(analyze, unit.name, unit.text, origin, can_score=can_score)
                )
    print("\n".join(findings))
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
