"""The range's own Python scripts get the pydantic they are written for.

They use pydantic 2 (``ConfigDict``, ``StringConstraints``, ``model_validate``).
Ubuntu noble's ``python3-pydantic`` is 1.10, with which they fail at import, and the
tests here run them under the development environment's pydantic 2, so nothing else
would notice. Every image that carries such a script must install pydantic 2 itself.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

import yaml

from soc_paths import CONFIGS, INJECTS, ROOT, topology_nodes  # noqa: F401

IMAGES: Path = ROOT / "phenix-images"
PREFIX: str = "/phenix/injects/${BRANCH_NAME}/"
USES_PYDANTIC: re.Pattern[str] = re.compile(r"^(?:from|import) pydantic\b", re.MULTILINE)
PIP_PYDANTIC_2: re.Pattern[str] = re.compile(r'pip3? install [^\n]*"pydantic==2\.')


def _images_with_pydantic_scripts(nodes: dict[str, dict]) -> dict[str, set[str]]:
    """Image config name -> the injected scripts on its nodes that import pydantic."""
    found: dict[str, set[str]] = {}
    for node in nodes.values():
        scripts: set[str] = set()
        for inject in node.get("injections") or []:
            source: Path = INJECTS / str(inject["src"]).removeprefix(PREFIX)
            if source.suffix == ".py" and USES_PYDANTIC.search(source.read_text(encoding="utf-8")):
                scripts.add(source.name)
        if scripts:
            for drive in node["hardware"]["drives"]:
                found.setdefault(Path(drive["image"]).stem, set()).update(scripts)
    return found


def test_no_image_installs_the_distribution_pydantic() -> None:
    """noble's package is pydantic 1.10; no image may rely on it."""
    offenders: list[str] = []
    for path in sorted(IMAGES.glob("*.yml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        packages: list[str] = [str(p) for p in (doc.get("spec") or {}).get("packages") or []]
        scripts: str = "\n".join(
            line for line in path.read_text(encoding="utf-8").splitlines() if not line.lstrip().startswith("#")
        )
        if "python3-pydantic" in packages or re.search(r"apt-get install[^\n]*python3-pydantic", scripts):
            offenders.append(path.name)
    assert not offenders, "images that install noble's pydantic 1.10: " + ", ".join(offenders)


def test_every_image_with_a_pydantic_script_installs_pydantic_2(topology_nodes: dict[str, dict]) -> None:  # noqa: F811
    images: dict[str, set[str]] = _images_with_pydantic_scripts(topology_nodes)
    # the vendor session, the PLC tool, the Guacamole SQL writer, the WireGuard
    # watcher, the Malcolm self-test, and the BBMD at least
    assert len(images) >= 5, images
    missing: list[str] = [
        f"{image} ({', '.join(sorted(scripts))})"
        for image, scripts in sorted(images.items())
        if not PIP_PYDANTIC_2.search((IMAGES / f"{image}.yml").read_text(encoding="utf-8"))
    ]
    assert not missing, "images whose scripts import pydantic but that do not pip install pydantic 2: " + ", ".join(missing)


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, check=False)


def test_the_sim_image_installs_the_committed_sim_and_viz() -> None:
    """cm-sim installs sim/ and viz/ from this repository's archive at CM_REF, while
    the app renders helics.json from the working tree. With CM_REF behind, the
    federates of the image do not know the names in it: cellar and packaging stopped
    at their first step (KeyError), and a federate cannot rejoin a running federation.
    The image config records the tree ids of both packages (sim/cherrymountain_sim,
    viz/cherrymountain_viz) at CM_REF; they must be those of the last commit. So a
    change to either package passes in its own commit, and every later commit fails
    until one moves CM_REF and the two ids. Their READMEs and tests are outside the
    two trees and need no move."""
    build = (IMAGES / "cm-sim.yml").read_text(encoding="utf-8")
    pins = dict(re.findall(r"^\s*(CM_REF|CM_SIM_TREE|CM_VIZ_TREE)=([0-9a-f]{40})$", build, re.MULTILINE))
    assert set(pins) == {"CM_REF", "CM_SIM_TREE", "CM_VIZ_TREE"}
    if shutil.which("git") is None or _git("rev-parse", "--git-dir").returncode != 0:
        pytest.skip("not a git checkout")
    prefix = _git("rev-parse", "--show-prefix").stdout.strip()
    for name, directory in (("CM_SIM_TREE", "sim/cherrymountain_sim"), ("CM_VIZ_TREE", "viz/cherrymountain_viz")):
        head = _git("rev-parse", f"HEAD:{prefix}{directory}").stdout.strip()
        assert pins[name] == head, f"{directory}/ changed since CM_REF: set CM_REF to the commit that changed it and {name} to {head}"
        # Where the pinned commit is at hand (not in a shallow CI checkout), it must hold these trees.
        at_ref = _git("rev-parse", "--verify", "--quiet", f"{pins['CM_REF']}:{prefix}{directory}")
        if at_ref.returncode == 0:
            assert at_ref.stdout.strip() == pins[name], f"CM_REF does not hold the recorded {directory}/ tree"
