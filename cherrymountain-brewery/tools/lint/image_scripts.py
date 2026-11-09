"""Read phenix Image configs and pull out the shell and systemd units they embed.

A phenix Image config keeps its build scripts as strings in ``spec.scripts``.
Scripts named ``POSTBUILD_*`` are verbatim copies of the defaults that
``phenix image create`` adds (src/go/api/image/constants.go), so the linters skip
them. Every other script is owned by this repository.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

#: Scripts with this prefix are phenix defaults, copied verbatim.
VERBATIM_PREFIX: str = "POSTBUILD_"

_UNIT_HEREDOC: re.Pattern[str] = re.compile(
    r"^[ \t]*cat[ \t]*>[ \t]*/etc/systemd/system/(?P<name>[\w@.-]+\.service)"
    r"[ \t]*<<[ \t]*(?P<quote>['\"]?)(?P<tag>\w+)(?P=quote)[ \t]*\n"
    r"(?P<body>.*?)\n(?P=tag)[ \t]*$",
    re.MULTILINE | re.DOTALL,
)


class ImageSpec(BaseModel):
    """The parts of an Image ``spec`` the linters read."""

    model_config = ConfigDict(extra="ignore")

    scripts: dict[str, str] = Field(default_factory=dict)
    script_order: list[str] = Field(default_factory=list)
    variant: str = "minbase"
    packages: list[str] = Field(default_factory=list)


class ImageConfig(BaseModel):
    """A phenix Image config file."""

    model_config = ConfigDict(extra="ignore")

    kind: str
    spec: ImageSpec


class Script(BaseModel):
    """One shell script of an image, with its place in the YAML file."""

    name: str
    text: str
    #: 1-based line of the file on which the script body starts.
    first_line: int


class Unit(BaseModel):
    """A systemd unit written by a heredoc inside an image script."""

    name: str
    text: str
    script: str
    #: 1-based line of the file on which the unit body starts.
    first_line: int


def load_image(path: Path) -> ImageConfig:
    """Read and validate an image config."""
    return ImageConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def own_scripts(path: Path) -> list[Script]:
    """Return the scripts of an image that this repository owns, in file order."""
    lines: list[str] = path.read_text(encoding="utf-8").splitlines()
    scripts: list[Script] = []
    for name, text in load_image(path).spec.scripts.items():
        if name.startswith(VERBATIM_PREFIX):
            continue
        key: re.Pattern[str] = re.compile(rf"^\s+{re.escape(name)}:\s*[|>]")
        index: int = next((i for i, line in enumerate(lines) if key.match(line)), 0)
        scripts.append(Script(name=name, text=text, first_line=index + 2))
    return scripts


def embedded_units(path: Path) -> list[Unit]:
    """Return the systemd units that the image scripts write with a heredoc."""
    units: list[Unit] = []
    for script in own_scripts(path):
        for match in _UNIT_HEREDOC.finditer(script.text):
            before: int = script.text.count("\n", 0, match.start("body"))
            body: str = match.group("body")
            if not match.group("quote"):
                # An unquoted heredoc is expanded by the shell: undo the escapes.
                body = body.replace("\\$", "$").replace("\\`", "`")
            units.append(
                Unit(
                    name=match.group("name"),
                    text=body + "\n",
                    script=script.name,
                    first_line=script.first_line + before,
                )
            )
    return units
