"""The phenix app protocol, end to end: stage on argv, the experiment on stdin,
only the updated experiment on stdout, JSON log lines on stderr, nonzero on
failure. Also checks the README examples against the metadata models."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from cherrymountain_apps.cherrymountain.tests.conftest import app_block
from cherrymountain_apps.cherrymountain.validation import Problem, parse

README: Path = Path(__file__).parents[1] / "README.md"


def run_cli(
    stage: str, experiment: dict[str, Any], *args: str
) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PHENIX_LOG_FILE": "stderr", "PHENIX_LOG_LEVEL": "INFO"}
    return subprocess.run(
        [sys.executable, "-m", "cherrymountain_apps.cherrymountain", stage, *args],
        input=json.dumps(experiment),
        capture_output=True,
        text=True,
        env=env,
        cwd=Path(__file__).parents[3],
        timeout=120,
        check=False,
    )


def test_configure_over_stdin_and_stdout(experiment: dict[str, Any]) -> None:
    proc = run_cli("configure", experiment)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    hosts = {h["hostname"]: h["metadata"] for h in app_block(out)["hosts"]}
    assert "sohProfile" in hosts["plc-cellar"]
    logs = [json.loads(line) for line in proc.stderr.splitlines()]
    assert {"level", "msg"} <= set(logs[0])
    assert any(
        "Configured user application: cherrymountain" in entry["msg"] for entry in logs
    )


def test_the_app_leaves_no_bytecode_in_the_topology_directory(
    experiment: dict[str, Any], tmp_path: Path
) -> None:
    """The app runs as root in the phenix container and imports plantgen and
    the FUXA builder from the topology directory, the user's checkout on the
    host: a ``__pycache__`` there would be root-owned files in it."""
    import shutil

    from cherrymountain_apps.cherrymountain.tests.conftest import ROOT

    source: Path = tmp_path / "cherrymountain-brewery"
    for part in ("plant", "tools/plantgen", "fuxa", "phenix-injects"):
        shutil.copytree(
            ROOT / part,
            source / part,
            ignore=shutil.ignore_patterns("__pycache__", "tests"),
        )
    app_block(experiment)["metadata"]["sourceDir"] = str(source)
    for stage in ("configure", "pre-start"):
        proc = run_cli(stage, experiment)
        assert proc.returncode == 0, proc.stderr
        experiment = json.loads(proc.stdout)
    assert not sorted(source.rglob("__pycache__"))


def test_failure_exits_nonzero_and_logs_json(experiment: dict[str, Any]) -> None:
    app_block(experiment)["hosts"][3]["metadata"]["type"] = "plc"
    proc = run_cli("configure", experiment)
    assert proc.returncode == 1
    assert proc.stdout == ""
    messages = [json.loads(line)["msg"] for line in proc.stderr.splitlines()]
    assert any("metadata.type: not a known host type" in m for m in messages)


def test_unknown_stage_is_refused(experiment: dict[str, Any]) -> None:
    assert run_cli("start", experiment).returncode == 2


def readme_blocks() -> list[Any]:
    text = README.read_text()
    section = text.split("\n## Example\n", 1)[1].split("\n## ", 1)[0]
    return [
        yaml.safe_load(b)
        for b in section.split("```yaml\n")[1:]
        for b in [b.split("```", 1)[0]]
    ]


def test_readme_has_two_examples() -> None:
    assert len(readme_blocks()) == 2


@pytest.mark.parametrize("index", [0, 1])
def test_readme_examples_validate_cleanly(index: int) -> None:
    (block,) = readme_blocks()[index]
    problems: list[Problem] = []
    parsed = parse(block["metadata"], block["hosts"], problems)
    assert problems == []
    assert parsed.app is not None
    assert len(parsed.hosts) == len(block["hosts"])
