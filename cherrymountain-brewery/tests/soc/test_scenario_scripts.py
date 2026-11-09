"""Every cm-* script a scenario step runs on a VM is on that VM (DESIGN.md 15.4, 15.6).

The SoH custom tests and the SCORCH `cc` steps of the full scenario call scripts by
their installed path. A script reaches a VM in one of two ways: the topology injects
it, or the ``cherrymountain`` app renders it at pre-start. A path that is neither
fails the step with "command not found", which only a VM run would show.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import yaml

from soc_paths import CONFIGS, INJECTS, ROOT, topology_nodes  # noqa: F401 - fixture

#: A cm-* executable or library file under /usr/local, as a step names it.
SCRIPT: re.Pattern[str] = re.compile(r"/usr/local/(?:s?bin|lib/cm-[\w-]+)/cm[-_][\w.-]+")
#: The apps whose metadata runs commands on VMs.
APPS: frozenset[str] = frozenset({"soh", "scorch", "mirror"})
RENDER: Path = ROOT / "apps" / "cherrymountain_apps" / "cherrymountain" / "render.py"


def _steps(obj: Any, host: str | None = None) -> Iterator[tuple[str, str]]:
    """(hostname, script path) for every cm-* path under a ``hostname`` key or
    under a map keyed by hostname."""
    if isinstance(obj, dict):
        host = obj.get("hostname", host) if isinstance(obj.get("hostname", host), str) else host
        for key, value in obj.items():
            yield from _steps(value, host)
    elif isinstance(obj, list):
        for value in obj:
            yield from _steps(value, host)
    elif isinstance(obj, str) and host is not None:
        for match in SCRIPT.finditer(obj):
            yield host, match.group(0)


def _host_keyed(metadata: dict[str, Any], nodes: dict[str, dict]) -> Iterator[tuple[str, str]]:
    """The soh maps keyed by hostname (hostCustomTests and the like)."""
    for value in metadata.values():
        if isinstance(value, dict):
            for host, entry in value.items():
                if host in nodes:
                    yield from _steps(entry, host)


def test_every_script_a_step_runs_is_on_its_vm(topology_nodes: dict[str, dict]) -> None:  # noqa: F811
    scenario = yaml.safe_load((CONFIGS / "scenario-full.yml").read_text(encoding="utf-8"))
    rendered: str = RENDER.read_text(encoding="utf-8")
    found: set[tuple[str, str]] = set()
    for app in scenario["spec"]["apps"]:
        if app["name"] not in APPS:
            continue
        found.update(_steps(app))
        found.update(_host_keyed(app.get("metadata") or {}, topology_nodes))
    assert found, "the full scenario runs no cm-* script; the pattern is wrong"
    missing: list[str] = []
    for host, path in sorted(found):
        if host not in topology_nodes:
            continue
        injected: set[str] = {i["dst"] for i in topology_nodes[host].get("injections") or []}
        if path not in injected and f'"{path}"' not in rendered:
            missing.append(f"{host}: {path}")
    assert not missing, "scripts a step runs but nothing puts on the VM: " + ", ".join(missing)


def test_every_injected_soc_script_exists(topology_nodes: dict[str, dict]) -> None:  # noqa: F811
    """The reverse: every topology injection has its source file."""
    prefix: str = "/phenix/injects/${BRANCH_NAME}/"
    missing: list[str] = [
        f"{host}: {inject['src']}"
        for host, node in sorted(topology_nodes.items())
        for inject in node.get("injections") or []
        if not (INJECTS / str(inject["src"]).removeprefix(prefix)).is_file()
    ]
    assert not missing, "injections without a source file: " + ", ".join(missing)
