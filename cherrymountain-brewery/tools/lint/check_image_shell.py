#!/usr/bin/env python3
"""Run shellcheck on the shell scripts embedded in phenix Image configs.

phenix keeps image build scripts as YAML strings, so the repo-wide shellcheck
hook cannot see them. This hook extracts each script that the repository owns
(not the verbatim ``POSTBUILD_*`` defaults) and runs ``shellcheck`` on it,
mapping every finding back to a line of the YAML file.

It also checks what only shows up when ``phenix image build`` runs (the
sceptre-phenix sources are ``src/go/types/version/v1/image.go``,
``src/go/tmpl/templates/vmdb.tmpl``, and ``src/go/api/image/image.go``):

* phenix joins the scripts of ``script_order`` into one script, and vmdb2 runs
  it with ``sh -ec`` in the image's chroot. On Ubuntu ``sh`` is dash, so the
  scripts are checked as dash, not bash.
* vmdb2 renders that script with Jinja2 first, so ``{{``, ``{%``, and ``{#``
  (as in ``${#name}``) start a template tag and must not appear.
* ``phenix config create`` replaces each ``${NAME}`` and ``${NAME:default}``
  with the value from the phenix host environment (``common.ParseEnv``), or
  with an empty string, so a shell variable must be written ``$NAME``.
* Every ``script_order`` entry needs its script, and every script its entry.
* ``phenix config create`` stores the file as it is. Only ``phenix image
  create`` adds the desktop packages of the ``mingui`` variant, so a stored
  ``mingui`` config must list them, or ``POSTBUILD_GUI`` fails on the missing
  ``/etc/lightdm``.

``shellcheck`` comes from the ``shellcheck-py`` package, which the prek hook
installs next to this script.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

from image_scripts import ImageSpec, Script, load_image, own_scripts

SHELLCHECK_ARGS: list[str] = [
    "--shell=dash",
    "--severity=style",
    "--format=gcc",
    "--exclude=SC1090,SC1091",
]
_FINDING: re.Pattern[str] = re.compile(r"^-:(?P<line>\d+):(?P<col>\d+): (?P<rest>.*)$")
#: The start of a Jinja2 expression, statement, or comment.
_JINJA: re.Pattern[str] = re.compile(r"\{\{|\{%|\{#")
#: A placeholder that phenix fills from its environment (common.EnvPlaceholder).
_ENV_PLACEHOLDER: re.Pattern[str] = re.compile(r"\$\{(\w+)(?::([^}]*))?\}")
#: What ``phenix image create --variant mingui`` adds to an Ubuntu image
#: (UbuntuMinGUIPackages in src/go/api/image/constants.go).
MINGUI_PACKAGES: tuple[str, ...] = ("wmctrl", "xdotool", "xubuntu-desktop")


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


def check_config(path: Path, spec: ImageSpec) -> list[str]:
    """Return what would stop ``phenix image build`` before or while it runs the scripts."""
    findings: list[str] = []
    for name in spec.script_order:
        if name not in spec.scripts:
            findings.append(f"{path}: script_order names {name}, which spec.scripts does not have")
    for name, text in spec.scripts.items():
        if name not in spec.script_order:
            findings.append(f"{path}: {name} is not in script_order, so it never runs")
        for number, line in enumerate(text.splitlines(), start=1):
            match: re.Match[str] | None = _JINJA.search(line)
            if match is not None:
                findings.append(
                    f"{path}: [{name}] line {number}: '{match.group()}' starts a Jinja2 tag "
                    "(vmdb2 renders the build script as a template)"
                )
            for env in _ENV_PLACEHOLDER.finditer(line):
                findings.append(
                    f"{path}: [{name}] line {number}: phenix config create replaces "
                    f"'{env.group()}' from its own environment; write ${env[1]}"
                )
    if spec.variant == "mingui":
        missing: list[str] = [pkg for pkg in MINGUI_PACKAGES if pkg not in spec.packages]
        if missing:
            findings.append(
                f"{path}: variant mingui needs {', '.join(missing)} in spec.packages "
                "(only `phenix image create` adds them)"
            )
    return findings


def main(argv: list[str]) -> int:
    shellcheck: str | None = shutil.which("shellcheck")
    if shellcheck is None:
        print("shellcheck not found; the hook installs it from shellcheck-py")
        return 2
    findings: list[str] = []
    for name in argv:
        path: Path = Path(name)
        findings.extend(check_config(path, load_image(path).spec))
        for script in own_scripts(path):
            findings.extend(check_script(shellcheck, path, script))
    print("\n".join(findings))
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
