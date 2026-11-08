#!/usr/bin/env python3
"""Run shellcheck on the shell scripts embedded in phenix Image configs.

phenix keeps image build scripts as YAML strings, so the repo-wide shellcheck
hook cannot see them. This hook extracts each script that the repository owns
(not the verbatim ``POSTBUILD_*`` defaults) and runs ``shellcheck`` on it,
mapping every finding back to a line of the YAML file.

``shellcheck`` comes from the ``shellcheck-py`` package, which the prek hook
installs next to this script.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

from image_scripts import Script, own_scripts

SHELLCHECK_ARGS: list[str] = [
    "--shell=bash",
    "--severity=style",
    "--format=gcc",
    "--exclude=SC1090,SC1091",
]
_FINDING: re.Pattern[str] = re.compile(r"^-:(?P<line>\d+):(?P<col>\d+): (?P<rest>.*)$")


def check_script(shellcheck: str, path: Path, script: Script) -> list[str]:
    """Return the shellcheck findings of one script, as ``file:line:col`` lines."""
    proc: subprocess.CompletedProcess[str] = subprocess.run(
        [shellcheck, *SHELLCHECK_ARGS, "-"],
        input=script.text,
        capture_output=True,
        text=True,
        check=False,
    )
    findings: list[str] = []
    for raw in proc.stdout.splitlines():
        match: re.Match[str] | None = _FINDING.match(raw)
        if match is None:
            findings.append(f"{path}: {script.name}: {raw}")
            continue
        line: int = script.first_line + int(match["line"]) - 1
        findings.append(
            f"{path}:{line}:{match['col']}: [{script.name}] {match['rest']}"
        )
    if proc.returncode not in (0, 1):
        findings.append(f"{path}: {script.name}: shellcheck failed: {proc.stderr}")
    return findings


def main(argv: list[str]) -> int:
    shellcheck: str | None = shutil.which("shellcheck")
    if shellcheck is None:
        print("shellcheck not found; the hook installs it from shellcheck-py")
        return 2
    findings: list[str] = []
    for name in argv:
        path: Path = Path(name)
        for script in own_scripts(path):
            findings.extend(check_script(shellcheck, path, script))
    print("\n".join(findings))
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
