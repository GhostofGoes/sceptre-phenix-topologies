#!/usr/bin/env python3
"""One HELICS broker for each site: federations with the same names, side by side.

Needs root, iproute2 (`ip netns`, `ss`), and a Python 3.12 interpreter that has
`helics` 3.6 and the needs of `sim/` (pydantic, simpy, opendssdirect.py):
  sudo -E <venv>/bin/python site_brokers.py              # build, check, tear down
  sudo -E <venv>/bin/python site_brokers.py --only ci    # one part: ci, full, fleet, or shared
  sudo -E <venv>/bin/python site_brokers.py --down       # remove a lab that a killed run left

A site is two network namespaces joined by one veth link, which stands for the
site's MGMT VLAN: `cmsb-<site>-ctl` is `sim-ctl` (the broker, and the weather
federate with the event API), and `cmsb-<site>-sim` is the other simulation
hosts and the field devices. No link joins two sites. The federates are the real
`cherrymountain_sim` federates, started with the command line of the
`cm-federate@.service` unit. The brokers are `helics_broker` processes with the
command line that the phenix `helics` app writes for a root broker. The field
devices (ot-sim I/O modules) are stand-ins: this script runs one HELICS federate
for each, with the name, subscriptions, and endpoint that `helics.json` lists.

The parts:
  ci      the smallest federation at every site (the cellar federate and one
          field device), held value by value against the same model with no HELICS
  full    the whole plant of the `full` variant at every site: all names equal
  fleet   each site from its own file under plant/generated/fleet/
  shared  two sites in one namespace: what HELICS does by default, and what
          keeps the federations apart there
Two studies run next to the parts, each on a network of its own: a site whose
federates restart alone, and a site whose broker restarts alone.

README.md has the question and the result.
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import ctypes
import datetime as dt
import itertools
import json
import os
import platform
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from types import FrameType
from typing import Any, Final

from pydantic import BaseModel, Field

# No __pycache__ in sim/ or spikes/ci/: this kit writes only in its own directory.
sys.dont_write_bytecode = True

HERE: Final[Path] = Path(__file__).resolve().parent
RANGE: Final[Path] = HERE.parent.parent
SIM_DIR: Final[Path] = RANGE / "sim"
GENERATED: Final[Path] = RANGE / "plant" / "generated"
sys.path.insert(0, str(HERE.parent / "ci"))
sys.path.insert(0, str(SIM_DIR))

from vmlab import Check, Evidence  # noqa: E402

EVIDENCE: Final[Path] = Path(os.environ.get("CM_EVIDENCE_DIR", HERE / "evidence")) / "site-brokers"
NS_PREFIX: Final[str] = "cmsb-"
SHARED_NS: Final[str] = f"{NS_PREFIX}shared"
BROKER_BIN: Final[Path] = Path(sys.executable).parent / "helics_broker"
#: The zmq core's default broker port, and the port of the request socket next to it.
BROKER_PORT: Final[int] = 23404
#: A value that a federate publishes in step k reaches a subscriber in step k + 1
#: (DESIGN.md 9.2).
LATENCY_STEPS: Final[int] = 1
PERIOD_S: Final[float] = 1.0
#: At 1 Hz in real time, a gap of two periods between two steps is a missed step.
MISSED_STEP_GAP_S: Final[float] = 2.0 * PERIOD_S
#: Steps of the offline reference (the same model, stepped without HELICS).
REFERENCE_STEPS: Final[int] = 600
BASELINE_STEPS: Final[int] = 12
DOWNTIME_S: Final[float] = 6.0
AFTER_RESTART_STEPS: Final[int] = 10
#: The first values arrive in step 2, so a run of BASELINE_STEPS holds two steps less.
MIN_COMPARED_STEPS: Final[int] = BASELINE_STEPS - 2
#: In the full plant, a value that depends on another federate's value settles after these steps.
WARM_UP_T: Final[float] = 4.0
START_TIMEOUT_S: Final[float] = 180.0
#: `RestartSec` of cm-federate@.service.
RESTART_SEC: Final[float] = 5.0
#: How long the run watches a site whose federates restart while its broker stays, at least.
STUDY_MIN_S: Final[float] = 60.0

Value = float | bool | str


class LabError(RuntimeError):
    """A part of the lab did not come up, or a wait ran out."""


class Site(BaseModel):
    """One brewery of the fleet (plant/sites.yaml), with the inputs that make it differ."""

    code: str
    index: int
    title: str
    #: The site's elevation, which sets the weather federate's pressure.
    elevation_m: float
    #: The site's own calendar: the day on which its simulation starts.
    clock_start: str

    @property
    def ctl_ns(self) -> str:
        return f"{NS_PREFIX}{self.code}-ctl"

    @property
    def sim_ns(self) -> str:
        return f"{NS_PREFIX}{self.code}-sim"

    @property
    def broker_address(self) -> str:
        """`sim-ctl` on the site's MGMT network: 172.16.10.1, moved by the site's number."""
        return f"172.{16 + self.index}.10.1"

    @property
    def sim_address(self) -> str:
        """`sim-process` on the site's MGMT network."""
        return f"172.{16 + self.index}.10.2"

    @property
    def broker_name(self) -> str:
        return f"{self.code}-sim-ctl"

    @property
    def clock_day(self) -> int:
        """The site's local start date as days since 1970-01-01 (`power/gen-ats.clock_day`)."""
        return (dt.datetime.fromisoformat(self.clock_start).date() - dt.date(1970, 1, 1)).days

    @property
    def spund_sp_psia(self) -> float:
        """The spunding setpoint that the site's cellar publishes: 1 psig over the site's own air pressure.

        The weather federate turns the site's elevation into a pressure, and the
        cellar federate adds the setpoint of its built-in operator. The value
        marks the site, and it reaches a field device through two federates.
        """
        from cherrymountain_sim.federate import PA_PER_PSI  # noqa: PLC0415
        from cherrymountain_sim.weather import standard_pressure_kpa  # noqa: PLC0415

        return float(standard_pressure_kpa(self.elevation_m) * 1000.0 / PA_PER_PSI + 1.0)


#: Two more networks, outside the fleet, for the studies of a partial restart.
STUDY_SITES: Final[dict[str, Site]] = {
    "federates": Site(
        code="rs",
        index=4,
        title="Restart of the federates",
        elevation_m=1600.0,
        clock_start="2026-07-19T04:30:00-06:00",
    ),
    "broker": Site(
        code="rb", index=5, title="Restart of the broker", elevation_m=1600.0, clock_start="2026-07-20T04:30:00-06:00"
    ),
}

SITES: Final[list[Site]] = [
    Site(code="fr", index=0, title="Front Range", elevation_m=1585.0, clock_start="2026-07-15T04:30:00-06:00"),
    Site(code="fc", index=1, title="Fort Collins", elevation_m=1525.0, clock_start="2026-07-16T04:30:00-06:00"),
    Site(code="ep", index=2, title="Eastern Plains", elevation_m=1300.0, clock_start="2026-07-17T04:30:00-06:00"),
    Site(code="cs", index=3, title="Colorado Springs", elevation_m=1839.0, clock_start="2026-07-18T04:30:00-06:00"),
]


class Observation(BaseModel):
    """What HELICS did in one case. An observation records a fact; it does not gate the result."""

    name: str
    observed: str
    #: True when the fact is a way for one site to harm or join another.
    hazard: bool = False


class Result(Evidence):
    """The evidence of this spike: the checks, and what HELICS did by default."""

    observations: list[Observation] = Field(default_factory=list)
    tables: dict[str, list[dict[str, str]]] = Field(default_factory=dict)


def log(message: str) -> None:
    print(f"[site-brokers] {message}", flush=True)


def run(argv: Sequence[str], check: bool = True, timeout_s: float = 30.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(argv), check=check, capture_output=True, text=True, timeout=timeout_s)


def child_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """The environment of every child: the sim package on the path, and no bytecode files.

    The HELICS_* variables of the caller are dropped. HELICS reads them in every
    broker and core, so a stray HELICS_BROKER_ADDRESS turns a root broker into a
    sub-broker.
    """
    env: dict[str, str] = {k: v for k, v in os.environ.items() if not k.startswith("HELICS_")}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = str(SIM_DIR)
    env.update(extra or {})
    return env


def tail(path: Path, lines: int = 6) -> str:
    if not path.exists():
        return ""
    return " / ".join(path.read_text(errors="replace").strip().splitlines()[-lines:])


# ---------------------------------------------------------------- the lab


class Lab:
    """The namespaces and processes of one run. It removes only what it made."""

    def __init__(self, work: Path) -> None:
        self.work: Path = work
        self.namespaces: list[str] = []
        self.procs: list[subprocess.Popen[bytes]] = []

    def namespace(self, name: str) -> None:
        run(["ip", "netns", "add", name])
        self.namespaces.append(name)
        run(["ip", "-n", name, "link", "set", "lo", "up"])

    def site_network(self, site: Site) -> None:
        """Two namespaces and the veth link between them: one site's MGMT network."""
        self.namespace(site.ctl_ns)
        self.namespace(site.sim_ns)
        run(
            [
                "ip",
                "link",
                "add",
                "mgmt",
                "netns",
                site.ctl_ns,
                "type",
                "veth",
                "peer",
                "name",
                "mgmt",
                "netns",
                site.sim_ns,
            ]
        )
        for ns, address in ((site.ctl_ns, site.broker_address), (site.sim_ns, site.sim_address)):
            run(["ip", "-n", ns, "addr", "add", f"{address}/16", "dev", "mgmt"])
            run(["ip", "-n", ns, "link", "set", "mgmt", "up"])

    def shared_network(self, sites: Sequence[Site]) -> None:
        """One namespace that holds the broker address of several sites: no separation at all."""
        self.namespace(SHARED_NS)
        run(["ip", "-n", SHARED_NS, "link", "add", "mgmt", "type", "bridge"])
        for site in sites:
            run(["ip", "-n", SHARED_NS, "addr", "add", f"{site.broker_address}/16", "dev", "mgmt"])
        run(["ip", "-n", SHARED_NS, "link", "set", "mgmt", "up"])

    def spawn(
        self, ns: str, argv: Sequence[str], out: Path, env: dict[str, str] | None = None
    ) -> subprocess.Popen[bytes]:
        """Start a process in a namespace. `ip netns exec` execs it, so the PID is the process."""
        with out.open("ab") as stream:
            proc: subprocess.Popen[bytes] = subprocess.Popen(
                ["ip", "netns", "exec", ns, *argv],
                stdout=stream,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                env=child_env(env),
                start_new_session=True,
            )
        self.procs.append(proc)
        return proc

    @staticmethod
    def signal_group(proc: subprocess.Popen[bytes], signum: int) -> None:
        """Signal a process and its children, as systemd signals the control group of a unit.

        `helics_broker` of the pip wheel is a Python wrapper that runs the real
        broker as a child. A signal to the wrapper alone leaves the broker up.
        """
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signum)

    @classmethod
    def stop(cls, procs: Iterable[subprocess.Popen[bytes]], grace_s: float = 5.0) -> None:
        """SIGTERM to each process group, as `systemctl stop` sends, and SIGKILL for what is left."""
        waiting: list[subprocess.Popen[bytes]] = list(procs)
        for proc in waiting:
            cls.signal_group(proc, signal.SIGTERM)
        deadline: float = time.monotonic() + grace_s
        for proc in waiting:
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=max(deadline - time.monotonic(), 0.05))
            cls.signal_group(proc, signal.SIGKILL)
            proc.wait(timeout=5.0)

    def listeners(self, ns: str) -> list[str]:
        """The listening TCP sockets of a namespace, as `address:port`."""
        text: str = run(["ip", "netns", "exec", ns, "ss", "-ltnH"]).stdout
        return sorted(line.split()[3] for line in text.splitlines() if line.strip())

    def down(self) -> None:
        self.stop(self.procs, grace_s=3.0)
        for name in self.namespaces:
            # Everything in a namespace of this run is a process of this run.
            for pid in run(["ip", "netns", "pids", name], check=False).stdout.split():
                with contextlib.suppress(ProcessLookupError):
                    os.kill(int(pid), signal.SIGKILL)
            run(["ip", "netns", "del", name], check=False)
        self.namespaces.clear()
        self.procs.clear()
        reap_orphans()


def adopt_orphans() -> None:
    """Make this process the parent of every descendant that loses its own parent.

    The real broker is a child of the wrapper script. When both get a signal and
    the script exits first, the broker would pass to PID 1 and stay as a zombie
    until PID 1 collects it. With this, the run collects it itself.
    """
    pr_set_child_subreaper: int = 36
    ctypes.CDLL(None, use_errno=True).prctl(pr_set_child_subreaper, 1, 0, 0, 0)


def reap_orphans(wait_s: float = 3.0) -> None:
    """Collect every child that exited, and wait a moment for those that still exit."""
    deadline: float = time.monotonic() + wait_s
    while True:
        try:
            pid, _ = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return  # no child is left
        if pid == 0:
            if time.monotonic() > deadline:
                return
            time.sleep(0.1)


def lab_names() -> list[str]:
    return [SHARED_NS, *[ns for site in (*SITES, *STUDY_SITES.values()) for ns in (site.ctl_ns, site.sim_ns)]]


def existing_namespaces() -> list[str]:
    return [line.split()[0] for line in run(["ip", "netns", "list"]).stdout.splitlines() if line.strip()]


def remove_left_over() -> None:
    """`--down`: remove the namespaces of a run that died before it cleaned up."""
    lab: Lab = Lab(Path(tempfile.gettempdir()))
    lab.namespaces = [name for name in existing_namespaces() if name in lab_names()]
    log(f"removing {lab.namespaces or 'nothing'}")
    lab.down()


def http(ns: str, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    """One request to the event API of a site, from inside its `sim-ctl` namespace."""
    argv: list[str] = ["ip", "netns", "exec", ns, sys.executable, str(Path(__file__).resolve()), "_http", method, path]
    if body is not None:
        argv.append(json.dumps(body))
    proc: subprocess.CompletedProcess[str] = subprocess.run(
        argv, check=False, capture_output=True, text=True, timeout=20.0, env=child_env()
    )
    if proc.returncode != 0:
        return {"error": (proc.stderr or proc.stdout).strip()[-200:]}
    reply: dict[str, Any] = json.loads(proc.stdout)
    return reply


def can_connect(ns: str, address: str, port: int, bind: bool = False) -> str:
    """Whether a TCP connection from a namespace to an address opens: `open`, or the error.

    With `bind`: whether a server in the namespace can take that address and port.
    """
    argv: list[str] = [
        "ip",
        "netns",
        "exec",
        ns,
        sys.executable,
        str(Path(__file__).resolve()),
        "_probe",
        address,
        str(port),
        *(["--bind"] if bind else []),
    ]
    return subprocess.run(
        argv, check=False, capture_output=True, text=True, timeout=20.0, env=child_env()
    ).stdout.strip()


def wait_until(
    what: str, done: Callable[[], bool], timeout_s: float, context: Callable[[], str] | None = None
) -> float:
    """Poll until `done`. Return the seconds it took."""
    start: float = time.monotonic()
    while not done():
        if time.monotonic() - start > timeout_s:
            raise LabError(f"timed out after {timeout_s:.0f} s: {what}" + (f" ({context()})" if context else ""))
        time.sleep(0.25)
    return time.monotonic() - start


# ---------------------------------------------------------------- site configuration


def site_config(base: dict[str, Any], site: Site) -> dict[str, Any]:
    """`helics.json` of a variant, as a site's own file: its broker, and its own inputs.

    Federate names, topic keys, and endpoint names are not touched. The inputs
    differ so that every site's values differ: the seeds, the fermenters' start
    temperatures, the calendar, and the elevation.
    """
    doc: dict[str, Any] = copy.deepcopy(base)
    doc["broker"]["address"] = site.broker_address
    # The ground-truth store (OpenSearch on sim-ctl) is not part of the question.
    doc.pop("ground_truth", None)
    if "clock" in doc:
        doc["clock"]["start"] = site.clock_start
    for name, fed in doc["federates"].items():
        sim: dict[str, Any] = fed.setdefault("sim", {})
        if "seed" in sim:
            sim["seed"] = int(sim["seed"]) + 1000 * site.index
        if name == "cellar":
            for state in sim.get("initial_state", {}).values():
                if state.get("phase") == "fermenting":
                    state["temp_f"] = float(state["temp_f"]) + 0.5 * site.index
        if name == "weather":
            sim["site_elevation_m"] = site.elevation_m
            # The image has the EPW file; this machine does not. Both use the site's elevation.
            sim["epw"] = None
    return doc


def reference(config: Path, name: str, steps: int) -> list[dict[str, Value]]:
    """The values that a federate publishes in each step, from the same model with no HELICS.

    This is the loop of `cherrymountain_sim.federate.run` without the transport.
    It is only a reference for a federate that takes no input from another one.
    """
    from cherrymountain_sim.federate import HelicsConfig, build, typed  # noqa: PLC0415

    hcfg = HelicsConfig.load(config)
    fcfg = hcfg.federates[name]
    logic = build(hcfg, name)
    out: list[dict[str, Value]] = []
    for _ in range(steps):
        logic.step(fcfg.period_s)
        out.append({pub.key: typed(pub, logic.value(pub)) for pub in fcfg.publications})
    return out


# ---------------------------------------------------------------- what a stand-in recorded


class IoLog:
    """The record of one stand-in process: one JSON object for each line."""

    def __init__(self, path: Path) -> None:
        self.created: list[str] = []
        self.executing: bool = False
        self.federates: list[str] = []
        self.errors: list[str] = []
        self.sent: list[dict[str, Any]] = []
        #: federate -> [(granted time, wall time)]
        self.steps: dict[str, list[tuple[float, float]]] = {}
        #: federate -> key -> [(granted time, value)]
        self.values: dict[str, dict[str, list[tuple[float, Value]]]] = {}
        if not path.exists():
            return
        for line in path.read_text().splitlines():
            try:
                rec: dict[str, Any] = json.loads(line)
            except json.JSONDecodeError:
                continue  # a line that the writer did not finish yet
            kind: str = rec.get("e", "")
            if kind == "created":
                self.created.append(rec["f"])
            elif kind == "executing":
                self.executing = True
            elif kind == "query":
                self.federates = sorted(rec["federates"])
            elif kind == "error":
                self.errors.append(rec["msg"])
            elif kind == "sent":
                self.sent.append(rec)
            elif kind == "step":
                self.steps.setdefault(rec["f"], []).append((rec["t"], rec["wall"]))
                series = self.values.setdefault(rec["f"], {})
                for key, value in rec["v"].items():
                    series.setdefault(key, []).append((rec["t"], value))

    def count(self, fed: str) -> int:
        return len(self.steps.get(fed, []))

    def last_t(self, fed: str) -> float:
        return self.steps[fed][-1][0] if self.steps.get(fed) else 0.0


class Cadence(BaseModel):
    """How a federate stepped over a stretch of wall time."""

    steps: int
    first_t: float
    last_t: float
    #: Steps whose granted time is not the one before it plus one period.
    irregular: int
    max_gap_s: float

    def text(self) -> str:
        return (
            f"{self.steps} steps, t {self.first_t:.0f} to {self.last_t:.0f}, "
            f"{self.irregular} irregular, longest gap {self.max_gap_s:.2f} s"
        )


def cadence(steps: Sequence[tuple[float, float]], wall_from: float = 0.0, wall_to: float = float("inf")) -> Cadence:
    """The steps whose wall time is in a window, with the step before the window as the start."""
    inside: list[int] = [i for i, (_, wall) in enumerate(steps) if wall_from <= wall <= wall_to]
    if not inside:
        return Cadence(steps=0, first_t=0.0, last_t=0.0, irregular=0, max_gap_s=float("inf"))
    lo: int = max(inside[0] - 1, 0)
    span: Sequence[tuple[float, float]] = steps[lo : inside[-1] + 1]
    irregular: int = sum(1 for a, b in itertools.pairwise(span) if abs(b[0] - a[0] - PERIOD_S) > 1e-9)
    gaps: list[float] = [b[1] - a[1] for a, b in itertools.pairwise(span)]
    return Cadence(
        steps=len(inside),
        first_t=steps[inside[0]][0],
        last_t=steps[inside[-1]][0],
        irregular=irregular,
        max_gap_s=round(max(gaps, default=0.0), 3),
    )


class Match(BaseModel):
    """Values that a stand-in received, held against a reference trajectory."""

    steps: int = 0
    compared: int = 0
    mismatched: int = 0
    #: Steps in which every received value equals the reference.
    equal_steps: int = 0
    first_mismatch: str = ""


def compare(
    values: dict[str, list[tuple[float, Value]]], ref: Sequence[dict[str, Value]], until_t: float = float("inf")
) -> Match:
    """Hold each received value against the reference of the step that published it."""
    by_step: dict[int, dict[str, Value]] = {}
    for key, series in values.items():
        for t, value in series:
            if t < until_t:
                by_step.setdefault(round(t / PERIOD_S), {})[key] = value
    out: Match = Match()
    for step in sorted(by_step):
        index: int = step - LATENCY_STEPS - 1
        if not 0 <= index < len(ref):
            continue
        out.steps += 1
        wrong: int = 0
        for key, value in by_step[step].items():
            out.compared += 1
            if ref[index].get(key) != value:
                wrong += 1
                if not out.first_mismatch:
                    out.first_mismatch = f"{key} at t={step}: got {value!r}, reference {ref[index].get(key)!r}"
        out.mismatched += wrong
        out.equal_steps += wrong == 0
    return out


# ---------------------------------------------------------------- one federation


class Federation:
    """One site's broker, real federates, and stand-ins for its field devices."""

    def __init__(
        self,
        lab: Lab,
        site: Site,
        label: str,
        tag: str,
        source: Path | None = None,
        ctl_ns: str | None = None,
        sim_ns: str | None = None,
        broker_args: Sequence[str] = ("--ipv4",),
        broker_port: int | None = None,
        broker_key: str | None = None,
        log_level: str = "summary",
        supervised: bool = False,
    ) -> None:
        self.lab: Lab = lab
        self.site: Site = site
        self.label: str = label
        self.tag: str = tag
        #: The generated `helics.json` that this site's file comes from.
        self.source: Path = source or GENERATED / label / "helics.json"
        self.ctl_ns: str = ctl_ns or site.ctl_ns
        self.sim_ns: str = sim_ns or site.sim_ns
        self.broker_args: list[str] = list(broker_args)
        self.broker_port: int | None = broker_port
        self.broker_key: str | None = broker_key
        self.log_level: str = log_level
        #: Run each federate as a unit with `Restart=always` and `RestartSec=5`, as the range does.
        self.supervised: bool = supervised
        self.dir: Path = lab.work / f"{label}-{site.code}-{tag}"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.doc: dict[str, Any] = site_config(json.loads(self.source.read_text()), site)
        self.config: Path = self.dir / "helics.json"
        self.config.write_text(json.dumps(self.doc, indent=1))
        self.io_out: Path = self.dir / "io.jsonl"
        self.io_cmd: Path = self.dir / "io.cmd"
        self.io_cmd.write_text("")
        self.procs: dict[str, subprocess.Popen[bytes]] = {}

    @property
    def real(self) -> list[str]:
        return list(self.doc["federates"])

    @property
    def stand_ins(self) -> list[str]:
        return list(self.doc["io_federates"])

    def device(self, name: str) -> str | None:
        """The stand-in of a field device, by the device's name without the site's prefix."""
        return next((n for n in self.stand_ins if n in (name, f"{self.site.code}-{name}")), None)

    @property
    def count(self) -> int:
        """What the `helics` app counts from the annotations: every federate of the site."""
        return int(self.doc["federate_count"])

    def broker_command(self) -> list[str]:
        """The root broker as templates/broker.mako of the phenix `helics` app writes it."""
        argv: list[str] = [
            str(BROKER_BIN),
            "--name",
            self.site.broker_name,
            f"-f{self.count}",
            *self.broker_args,
            "--loglevel",
            self.log_level,
            "--logfile",
            str(self.dir / "helics-root-broker.log"),
            "--autorestart",
        ]
        if self.broker_port is not None:
            argv += ["--port", str(self.broker_port)]
        if self.broker_key is not None:
            argv += ["--brokerkey", self.broker_key]
        return argv

    def federate_env(self) -> dict[str, str]:
        """/etc/default/cm-federate-<name>, plus what a site adds through HELICS's own variables."""
        env: dict[str, str] = {
            "CM_HELICS_CONFIG": str(self.config),
            "HELICS_BROKER_ADDRESS": self.site.broker_address,
            "CM_LOG_LEVEL": "INFO",
        }
        if self.broker_port is not None:
            env["HELICS_BROKER_PORT"] = str(self.broker_port)
        if self.broker_key is not None:
            env["HELICS_BROKER_KEY"] = self.broker_key
        return env

    def federate_command(self, name: str) -> list[str]:
        """ExecStart of cm-federate@.service."""
        return [
            sys.executable,
            "-m",
            "cherrymountain_sim",
            "--config",
            str(self.config),
            "--federate",
            name,
            "--broker-address",
            self.site.broker_address,
            "--log-level",
            "INFO",
        ]

    def as_unit(self, argv: list[str]) -> list[str]:
        if not self.supervised:
            return argv
        return [sys.executable, str(Path(__file__).resolve()), "_unit", "--restart-sec", str(RESTART_SEC), "--", *argv]

    def start_broker(self) -> subprocess.Popen[bytes]:
        proc = self.lab.spawn(self.ctl_ns, self.broker_command(), self.dir / "broker.out")
        self.procs["broker"] = proc
        return proc

    def start_federate(self, name: str, env: dict[str, str] | None = None) -> subprocess.Popen[bytes]:
        # The federate that plant.yaml puts on the broker host runs next to the broker.
        on_ctl: bool = self.doc["federates"][name]["host"] == self.doc["broker"]["host"]
        proc = self.lab.spawn(
            self.ctl_ns if on_ctl else self.sim_ns,
            self.as_unit(self.federate_command(name)),
            self.dir / f"{name}.out",
            self.federate_env() if env is None else env,
        )
        self.procs[name] = proc
        return proc

    def start_io(self, init: str | None = None, address: str | None = None) -> subprocess.Popen[bytes]:
        """One process with a stand-in federate for each field device of the site."""
        extra: list[str] = []
        if self.broker_port is not None:
            extra.append(f"--brokerport={self.broker_port}")
        if self.broker_key is not None:
            extra.append(f"--brokerkey={self.broker_key}")
        argv: list[str] = [
            sys.executable,
            str(Path(__file__).resolve()),
            "_io",
            "--config",
            str(self.config),
            "--broker-address",
            address or self.site.broker_address,
            # One word: an init string starts with `--`, which argparse takes for an option.
            "--init=" + (" ".join(extra) if init is None else init),
            "--out",
            str(self.io_out),
            "--commands",
            str(self.io_cmd),
        ]
        proc = self.lab.spawn(self.sim_ns, self.as_unit(argv), self.dir / "io.out")
        self.procs["io"] = proc
        return proc

    def start(self) -> None:
        self.start_broker()
        self.start_io()
        for name in self.real:
            self.start_federate(name)

    def stop(self) -> None:
        self.lab.stop(self.procs.values())

    def io(self) -> IoLog:
        return IoLog(self.io_out)

    def alive(self) -> list[str]:
        return [name for name, proc in self.procs.items() if proc.poll() is None]

    def dead(self) -> list[str]:
        return [f"{name} (exit {proc.returncode})" for name, proc in self.procs.items() if proc.poll() is not None]

    def send(self, endpoint: str, payload: dict[str, Any], sender: str | None = None) -> None:
        """Ask a stand-in to send one message, as ot-sim's I/O module sends a write."""
        with self.io_cmd.open("a") as stream:
            stream.write(json.dumps({"from": sender or self.stand_ins[0], "to": endpoint, "payload": payload}) + "\n")

    def diagnose(self) -> str:
        parts: list[str] = [f"{self.site.code}: dead {self.dead() or 'none'}"]
        for name in ("broker", "io", *self.real):
            text: str = tail(self.dir / f"{name}.out", 2)
            if text:
                parts.append(f"{name}: {text[-300:]}")
        return " | ".join(parts)


def wait_steps(feds: Sequence[Federation], steps: int, timeout_s: float, what: str) -> float:
    """Wait until the first stand-in of each federation recorded that many steps."""
    return wait_until(
        what,
        lambda: all(f.io().count(f.stand_ins[0]) >= steps for f in feds),
        timeout_s,
        lambda: " ; ".join(f.diagnose() for f in feds if f.io().count(f.stand_ins[0]) < steps),
    )


# ---------------------------------------------------------------- the checks


class Checks:
    """Collects the checks, prints each, and keeps the evidence."""

    def __init__(self) -> None:
        self.result: Result = Result(spike="site-brokers")

    def check(self, name: str, passed: bool, detail: str = "") -> bool:
        self.result.checks.append(Check(name=name, passed=passed, detail=detail))
        print(f"{'PASS' if passed else 'FAIL'}  {name}" + (f"\n      {detail}" if detail else ""), flush=True)
        return passed

    def observe(self, name: str, observed: str, hazard: bool = False) -> None:
        self.result.observations.append(Observation(name=name, observed=observed, hazard=hazard))
        print(f"{'NOTE' if not hazard else 'RISK'}  {name}\n      {observed}", flush=True)

    def table(self, name: str, rows: list[dict[str, str]]) -> None:
        self.result.tables[name] = rows


def restart_alone(
    ev: Checks, label: str, feds: dict[str, Federation], victim: str, lab: Lab
) -> tuple[Federation, float, float]:
    """Stop one site's broker and federates, start them again, and watch the other sites.

    Returns the new federation of the site, and the wall-clock window of the restart.
    """
    old: Federation = feds[victim]
    others: list[Federation] = [f for code, f in feds.items() if code != victim]
    before: dict[str, float] = {f.site.code: f.io().last_t(f.stand_ins[0]) for f in feds.values()}
    wall_from: float = time.time()
    log(f"{label}: stopping every process of {victim} (broker, {len(old.real)} federates, the stand-ins)")
    old.stop()
    time.sleep(DOWNTIME_S)
    new: Federation = Federation(lab, old.site, old.label, "again", source=old.source)
    new.start()
    took: float = wait_steps([new], AFTER_RESTART_STEPS, START_TIMEOUT_S, f"{victim} runs again")
    wall_to: float = time.time()
    rows: list[dict[str, str]] = []
    undisturbed: list[bool] = []
    for fed in others:
        io: IoLog = fed.io()
        name: str = fed.stand_ins[0]
        during: Cadence = cadence(io.steps.get(name, []), wall_from, wall_to)
        base: Cadence = cadence(io.steps.get(name, []), 0.0, wall_from)
        ok: bool = (
            not fed.dead()
            and during.steps >= int(wall_to - wall_from) - 2
            and during.irregular == 0
            and during.max_gap_s < MISSED_STEP_GAP_S
        )
        undisturbed.append(ok)
        rows.append(
            {
                "Site": fed.site.code,
                "Before the restart": base.text(),
                "During the restart": during.text(),
                "Processes that exited": ", ".join(fed.dead()) or "none",
            }
        )
    again: IoLog = new.io()
    rows.append(
        {
            "Site": f"{victim} (restarted)",
            "Before the restart": f"stopped at t {before[victim]:.0f}",
            "During the restart": f"down {DOWNTIME_S:.0f} s, then {took:.1f} s until step {AFTER_RESTART_STEPS}; "
            f"its time starts again at {again.steps[new.stand_ins[0]][0][0]:.0f}",
            "Processes that exited": ", ".join(new.dead()) or "none",
        }
    )
    ev.table(f"{label}: one site restarts", rows)
    # If the sites shared a broker, the stop of one broker would stop every site's time:
    # the others would record no step in the window, or a gap as long as the downtime,
    # or their federates would exit with a HELICS error.
    ev.check(
        f"{label}: {victim} restarts alone, and the other sites go on",
        all(undisturbed) and not new.dead() and again.steps[new.stand_ins[0]][0][0] <= 2.0,
        f"window {wall_to - wall_from:.1f} s; "
        + "; ".join(f"{row['Site']}: {row['During the restart']}" for row in rows),
    )
    feds[victim] = new
    return new, wall_from, wall_to


def part_ci(lab: Lab, ev: Checks, sites: Sequence[Site]) -> None:
    """The smallest federation at every site: the real cellar federate and one stand-in."""
    feds: dict[str, Federation] = {site.code: Federation(lab, site, "ci", "first") for site in sites}
    refs: dict[str, list[dict[str, Value]]] = {
        code: reference(fed.config, "cellar", REFERENCE_STEPS) for code, fed in feds.items()
    }
    for fed in feds.values():
        fed.start()
    took: float = wait_steps(list(feds.values()), BASELINE_STEPS, START_TIMEOUT_S, "ci federations run")
    log(f"ci: {len(feds)} federations run after {took:.1f} s")
    first: Federation = feds[sites[0].code]
    ev.result.facts["ci: broker command"] = " ".join(first.broker_command())
    ev.result.facts["ci: federate command"] = " ".join(first.federate_command("cellar"))
    ev.result.facts["ci: listeners in each sim-ctl namespace"] = "; ".join(
        f"{fed.ctl_ns}: {', '.join(lab.listeners(fed.ctl_ns))}" for fed in feds.values()
    )
    other: Site = sites[1]
    ev.result.facts[f"ci: TCP from {first.sim_ns} to the broker of {other.code}"] = can_connect(
        first.sim_ns, other.broker_address, BROKER_PORT
    )
    ev.result.facts[f"ci: TCP from {first.sim_ns} to its own broker"] = can_connect(
        first.sim_ns, first.site.broker_address, BROKER_PORT
    )

    # 1. The same names in every federation.
    names: dict[str, list[str]] = {code: fed.io().federates for code, fed in feds.items()}
    expect: list[str] = sorted([*first.real, *first.stand_ins])
    # If equal names could not run side by side, a broker would refuse a federate
    # ("duplicate federate name"), and a federation would never leave the wait.
    ev.check(
        f"ci: {len(feds)} federations with the same federate names run at once, one broker each",
        all(found == expect for found in names.values()) and not any(fed.dead() for fed in feds.values()),
        f"each root broker lists {expect}; " + ", ".join(f"{code}: {found}" for code, found in names.items()),
    )

    # 2. One site restarts alone.
    victim: str = sites[2].code if len(sites) > 2 else sites[-1].code
    stopped: Federation = feds[victim]
    _, wall_from, wall_to = restart_alone(ev, "ci", feds, victim, lab)

    # 3. A write to an endpoint that has the same name at every site.
    writers: dict[str, float] = {sites[0].code: 17.25, sites[-1].code: 18.5}
    for code, value in writers.items():
        feds[code].send("cellar/updates", {"tag": "fv101.spund_sp_psia", "value": value})
    time.sleep(7.0)
    for fed in feds.values():
        fed.stop()

    logs: dict[str, IoLog] = {code: fed.io() for code, fed in feds.items()}
    rows: list[dict[str, str]] = []
    own_ok: list[bool] = []
    apart_ok: list[bool] = []
    for code, fed in feds.items():
        io: IoLog = logs[code]
        name: str = fed.stand_ins[0]
        until: float = min((rec["t"] for rec in io.sent), default=float("inf"))
        own: Match = compare(io.values.get(name, {}), refs[code], until)
        most_alike: int = 0
        for other_code in feds:
            if other_code != code:
                most_alike = max(most_alike, compare(io.values.get(name, {}), refs[other_code], until).equal_steps)
        own_ok.append(own.steps >= MIN_COMPARED_STEPS and own.mismatched == 0)
        apart_ok.append(most_alike == 0)
        rows.append(
            {
                "Site": code,
                "Steps": str(own.steps),
                "Values held against its own reference": str(own.compared),
                "Not equal": str(own.mismatched) + (f" ({own.first_mismatch})" if own.first_mismatch else ""),
                "Steps equal to another site's reference": str(most_alike),
            }
        )
    old_io: IoLog = stopped.io()
    old_own: Match = compare(old_io.values.get(stopped.stand_ins[0], {}), refs[victim])
    rows.append(
        {
            "Site": f"{victim}, before its restart",
            "Steps": str(old_own.steps),
            "Values held against its own reference": str(old_own.compared),
            "Not equal": str(old_own.mismatched),
            "Steps equal to another site's reference": "not counted",
        }
    )
    ev.table("ci: received values against the offline reference", rows)
    # If a federation saw another site's values, a stand-in would receive a value
    # that its own site's model did not compute: "Not equal" would be above 0, or a
    # step would equal the reference of another site.
    ev.check(
        "ci: each federation receives exactly the values of its own site's cellar, through the restart of a neighbor",
        all(own_ok) and all(apart_ok) and old_own.mismatched == 0 and old_own.steps >= MIN_COMPARED_STEPS,
        "; ".join(
            f"{row['Site']}: {row['Values held against its own reference']} values over {row['Steps']} steps, "
            f"{row['Not equal']} not equal"
            for row in rows
        )
        + f"; the comparison covers the {wall_to - wall_from:.0f} s of the neighbor's restart",
    )

    key: str = "cellar/fv101.spund_sp_psia"
    finals: dict[str, Value] = {}
    for code, fed in feds.items():
        series = logs[code].values.get(fed.stand_ins[0], {}).get(key, [])
        finals[code] = series[-1][1] if series else "nothing received"
    # The setpoint that each site's own model holds when nobody writes.
    unwritten: dict[str, Value] = {code: refs[code][0][key] for code in feds}
    # If the endpoints of two sites were one, a write at one site would also move
    # the setpoint at another site, or a site would get the other writer's value.
    ev.check(
        "ci: a write to `cellar/updates` changes the setpoint at the site that sent it, and at no other",
        all(finals[code] == value for code, value in writers.items())
        and all(finals[code] == unwritten[code] for code in feds if code not in writers),
        f"`{key}` at the end: {finals}; written: {writers}; "
        f"the setpoint of each site's model with no write: {unwritten}",
    )


def part_full(lab: Lab, ev: Checks, sites: Sequence[Site]) -> None:
    """The whole plant at every site: seven real federates and twelve stand-ins, all names equal."""
    plant(lab, ev, sites, "full", {site.code: GENERATED / "full" / "helics.json" for site in sites})


def part_fleet(lab: Lab, ev: Checks, sites: Sequence[Site]) -> None:
    """Each site from its own generated file: the fleet as plantgen writes it today."""
    sources: dict[str, Path] = {site.code: GENERATED / "fleet" / site.code / "helics.json" for site in sites}
    missing: list[str] = [str(path.relative_to(RANGE)) for path in sources.values() if not path.exists()]
    if missing:
        ev.observe("The fleet part did not run", f"plantgen did not write {', '.join(missing)} in this checkout.")
        return
    plant(lab, ev, sites, "fleet", sources)


def plant(lab: Lab, ev: Checks, sites: Sequence[Site], label: str, sources: dict[str, Path]) -> None:
    """Every site's whole federation at once: names, values, an event, and a restart."""
    feds: dict[str, Federation] = {
        site.code: Federation(lab, site, label, "first", source=sources[site.code]) for site in sites
    }
    for fed in feds.values():
        fed.start()
    took: float = wait_steps(list(feds.values()), BASELINE_STEPS, START_TIMEOUT_S, f"{label} federations run")
    log(f"{label}: {len(feds)} federations run after {took:.1f} s")
    time.sleep(3.0)
    health: dict[str, dict[str, Any]] = {code: http(fed.ctl_ns, "GET", "/api/v1/health") for code, fed in feds.items()}
    names: dict[str, list[str]] = {code: fed.io().federates for code, fed in feds.items()}
    shared: list[str] = sorted(set.intersection(*[set(found) for found in names.values()]))
    sizes: str = ", ".join(str(fed.count) for fed in feds.values())
    ev.table(
        f"{label}: the federations",
        [
            {
                "Site": code,
                "Source": str(fed.source.relative_to(RANGE)),
                "Broker": f"{fed.site.broker_name} at {fed.site.broker_address}",
                "Real federates": ", ".join(fed.real),
                "Stand-ins for field devices": ", ".join(fed.stand_ins),
                "Federates that the root broker lists": str(len(names[code])),
            }
            for code, fed in feds.items()
        ],
    )
    # As in the ci part: a refused federate, or a federation that never starts.
    ev.check(
        f"{label}: {len(feds)} federations ({sizes} federates) run at once, "
        f"and {len(shared)} federate names are in all of them",
        all(names[code] == sorted([*fed.real, *fed.stand_ins]) for code, fed in feds.items())
        and all(h.get("status") == "ok" for h in health.values())
        and not any(fed.dead() for fed in feds.values())
        and len(shared) >= min(len(fed.real) for fed in feds.values()),
        f"in every federation: {', '.join(shared)}; "
        + "; ".join(
            f"{code}: {len(fed.real)} real federates and {len(fed.stand_ins)} stand-ins, "
            f"health {health[code].get('status')}"
            for code, fed in feds.items()
        ),
    )

    # Each site's own inputs, as its own federation hands them on.
    rows: list[dict[str, str]] = []
    marks_ok: list[bool] = []
    wanted: list[float] = []
    for code, fed in feds.items():
        site: Site = fed.site
        io: IoLog = fed.io()
        # The first values are from the steps before the weather's pressure reached the cellar.
        ats: str | None = fed.device("gen-ats")
        spund = [
            v
            for t, v in io.values.get(fed.device("rio-cellar") or "", {}).get("cellar/fv102.spund_sp_psia", [])
            if t > WARM_UP_T
        ]
        days = [v for _, v in io.values.get(ats or "", {}).get("power/gen-ats.clock_day", [])]
        want_spund: float = site.spund_sp_psia
        wanted.append(round(want_spund, 6))
        want_day: float = float(site.clock_day)
        sim_time: str = str(health[code].get("sim_time", ""))
        ok: bool = (
            len(spund) >= MIN_COMPARED_STEPS - 2
            and (len(days) >= MIN_COMPARED_STEPS - 2 or ats is None)
            and all(abs(float(v) - want_spund) < 1e-9 for v in spund)
            and all(float(v) == want_day for v in days)
            and sim_time.startswith(site.clock_start[:10])
        )
        marks_ok.append(ok)
        rows.append(
            {
                "Site": code,
                "Elevation (m)": f"{site.elevation_m:.0f}",
                "FV-102 setpoint for that elevation (psia)": f"{want_spund:.6f}",
                "Received by `rio-cellar`": f"{sorted({round(float(v), 6) for v in spund})} in {len(spund)} values",
                "Calendar day, configured": f"{want_day:.0f}",
                "Received by `gen-ats`": f"{sorted({float(v) for v in days})} in {len(days)} values"
                if ats
                else "the site has no power area",
                "`sim_time` of the event API": sim_time,
            }
        )
    ev.table(f"{label}: each site's own inputs, as its federation delivers them", rows)
    # If two federations mixed, a stand-in would receive the setpoint or the
    # calendar day of another site among its values.
    ev.check(
        f"{label}: every stand-in receives its own site's setpoint and calendar day, and never another site's",
        all(marks_ok) and len(set(wanted)) == len(feds),
        "; ".join(
            f"{row['Site']}: setpoint {row['Received by `rio-cellar`']}, day {row['Received by `gen-ats`']}"
            for row in rows
        ),
    )

    # An event at one site, through that site's real event API.
    targets: dict[str, str] = {sites[0].code: "FV-102"}
    if len(sites) > 1:
        targets[sites[-1].code] = "FV-103"
    accepted: dict[str, Any] = {}
    for code, unit in targets.items():
        accepted[code] = http(
            feds[code].ctl_ns,
            "POST",
            "/api/v1/events",
            {"event": "co2-release", "fv": unit, "rate": "high", "duration": 120},
        ).get("accepted")
    time.sleep(8.0)
    leak_rows: list[dict[str, str]] = []
    leak_ok: list[bool] = []
    for code, fed in feds.items():
        state: dict[str, Any] = http(fed.ctl_ns, "GET", "/api/v1/state").get("values", {})
        events: list[Any] = http(fed.ctl_ns, "GET", "/api/v1/events").get("events", [])
        leaks: dict[str, float] = {
            unit: float(state.get(f"cellar/{unit.replace('-', '').lower()}.leak_gps", -1.0))
            for unit in ("FV-102", "FV-103")
        }
        want: dict[str, bool] = {unit: targets.get(code) == unit for unit in leaks}
        leak_ok.append(
            all((leaks[unit] > 0.0) == want[unit] and leaks[unit] >= 0.0 for unit in leaks)
            and len(events) == (code in targets)
        )
        leak_rows.append(
            {
                "Site": code,
                "Event sent": targets.get(code, "none"),
                "Events in its API": str(len(events)),
                "FV-102 leak (g/s)": f"{leaks['FV-102']:g}",
                "FV-103 leak (g/s)": f"{leaks['FV-103']:g}",
            }
        )
    ev.table(f"{label}: a CO2 release at one site", leak_rows)
    # If a message crossed to another federation, the leak would also show at a
    # site that got no event, or the other site's fermenter would leak.
    ev.check(
        f"{label}: a scenario event at one site acts on that site's plant, and on no other",
        all(leak_ok) and all(accepted.values()),
        "; ".join(
            f"{row['Site']}: event {row['Event sent']}, FV-102 {row['FV-102 leak (g/s)']} g/s, "
            f"FV-103 {row['FV-103 leak (g/s)']} g/s"
            for row in leak_rows
        ),
    )

    # One site restarts alone.
    victim: str = sites[2].code if len(sites) > 2 else sites[-1].code
    survivors: list[str] = [code for code in feds if code != victim]
    before: dict[str, dict[str, Any]] = {code: http(feds[code].ctl_ns, "GET", "/api/v1/state") for code in survivors}
    new, _, _ = restart_alone(ev, label, feds, victim, lab)
    time.sleep(2.0)
    after: dict[str, dict[str, Any]] = {code: http(feds[code].ctl_ns, "GET", "/api/v1/state") for code in survivors}
    health = {code: http(fed.ctl_ns, "GET", "/api/v1/health") for code, fed in feds.items()}
    back: dict[str, Any] = http(new.ctl_ns, "GET", "/api/v1/state")
    clock_rows: list[dict[str, str]] = [
        {
            "Site": code,
            "`t_s` before": f"{float(before[code].get('t_s', -1)):.0f}",
            "`t_s` after": f"{float(after[code].get('t_s', -1)):.0f}",
            "Health after": f"{health[code].get('status')}, {health[code].get('sim_time')}",
        }
        for code in survivors
    ]
    clock_rows.append(
        {
            "Site": f"{victim} (restarted)",
            "`t_s` before": "stopped",
            "`t_s` after": f"{float(back.get('t_s', -1)):.0f}",
            "Health after": f"{health[victim].get('status')}, {health[victim].get('sim_time')}",
        }
    )
    ev.table(f"{label}: the event API of each site around the restart", clock_rows)
    # If the restart reached another site, that site's event API would report
    # `stalled`, or its time would start again at zero with the restarted site.
    ev.check(
        f"{label}: after the restart of {victim}, every other site's time went on and its event API reports `ok`",
        all(health[code].get("status") == "ok" for code in feds)
        and all(float(after[code].get("t_s", -1)) > float(before[code].get("t_s", 1e12)) for code in survivors)
        and 0.0 <= float(back.get("t_s", -1)) < min(float(after[code].get("t_s", 0)) for code in survivors),
        "; ".join(
            f"{row['Site']}: t_s {row['`t_s` before']} to {row['`t_s` after']}, {row['Health after']}"
            for row in clock_rows
        ),
    )
    for fed in feds.values():
        fed.stop()


class RestartStudy:
    """A site of which only a part restarts: its federates, or its broker.

    The federates run as units that start again 5 s after each exit, as
    `cm-federate@.service` does. A study runs next to the other parts, on a
    network of its own, so that it can watch for minutes at no cost in time.
    """

    def __init__(self, lab: Lab, part: str) -> None:
        self.part: str = part
        self.lab: Lab = lab
        lab.site_network(STUDY_SITES[part])
        self.fed: Federation = Federation(lab, STUDY_SITES[part], "ci", "units", supervised=True)
        self.fed.start()
        self.restarted_wall: float | None = None
        self.steps_before: int = 0

    def restart(self) -> None:
        self.steps_before = self.fed.io().count(self.fed.stand_ins[0])
        if self.steps_before < MIN_COMPARED_STEPS:
            return  # the federation never ran: `report` says so
        self.restarted_wall = time.time()
        if self.part == "federates":
            # `systemctl restart` of the federate units: each unit stops its process and starts it again.
            for name in ("cellar", "io"):
                os.kill(self.fed.procs[name].pid, signal.SIGUSR1)
        else:
            # The broker stops and starts again with the same command. The federate units stay.
            self.lab.stop([self.fed.procs["broker"]])
            time.sleep(2.0)
            self.fed.start_broker()

    def report(self, ev: Checks) -> None:
        title: str = (
            "A restart of the federates alone, with the broker left up"
            if self.part == "federates"
            else "A restart of the broker alone, with the federates left up"
        )
        if self.restarted_wall is None:
            ev.observe(title, f"The study did not run: {self.steps_before} steps. {self.fed.diagnose()}")
            return
        time.sleep(max(self.restarted_wall + STUDY_MIN_S - time.time(), 0.0))
        watched: float = time.time() - self.restarted_wall
        logs: dict[str, str] = {
            name: (self.fed.dir / f"{name}.out").read_text(errors="replace") for name in ("cellar", "io")
        }
        starts: dict[str, int] = {
            name: sum(
                1
                for line in text.splitlines()
                if line.startswith('{"unit"') and json.loads(line)["started"] > self.restarted_wall
            )
            for name, text in logs.items()
        }
        forced: int = logs["cellar"].count("forced Granted Time")
        io: IoLog = self.fed.io()
        after: list[tuple[float, float]] = [
            step for step in io.steps.get(self.fed.stand_ins[0], []) if step[1] > self.restarted_wall + 1.0
        ]
        back: str = (
            f"The federation ran again {after[0][1] - self.restarted_wall:.0f} s after the restart."
            if after
            else "The federation did not run again."
        )
        answer: str = (io.errors[-1] if io.errors else "none")[-120:]
        if self.part == "federates":
            text: str = (
                f"The federation ran {self.steps_before} steps. Then both federate units restarted "
                f"(`Restart=always`, `RestartSec={RESTART_SEC:.0f}`), and the broker (`--autorestart`) stayed. "
                f"In {watched:.0f} s, `cellar` started {starts['cellar']} times and the stand-in {starts['io']} times. "
                f"The broker's answer: {answer}. {back}"
            )
        else:
            text = (
                f"The federation ran {self.steps_before} steps. Then the broker stopped, and it started again 2 s "
                f"later with the same command. In {watched:.0f} s, the `cellar` unit started its process again "
                f"{starts['cellar']} times. The federate logged {forced} lines with `forced Granted Time`: with "
                "HELICS's real-time flag it steps on alone, and it does not register at the new broker. "
                f"The stand-in started again {starts['io']} times ({answer}). {back}"
            )
        ev.observe(title, text)
        self.fed.stop()


def settle(procs: Sequence[subprocess.Popen[bytes]], seconds: float) -> None:
    """Wait for processes to exit, up to a limit."""
    deadline: float = time.monotonic() + seconds
    while time.monotonic() < deadline and any(p.poll() is None for p in procs):
        time.sleep(0.2)


def part_shared(lab: Lab, ev: Checks, sites: Sequence[Site]) -> None:
    """Two sites on one network: what HELICS does by default, and which options separate them."""
    a, b = sites[0], sites[2] if len(sites) > 2 else sites[-1]
    lab.shared_network([a, b])
    refs: dict[str, list[dict[str, Value]]] = {}

    def fed(site: Site, tag: str, **kwargs: Any) -> Federation:
        made: Federation = Federation(lab, site, "ci", f"shared-{tag}", ctl_ns=SHARED_NS, sim_ns=SHARED_NS, **kwargs)
        if site.code not in refs:
            refs[site.code] = reference(made.config, "cellar", REFERENCE_STEPS)
        return made

    def separate(label: str, fa: Federation, fb: Federation, how: str) -> None:
        """Both federations in one namespace: each must receive its own values only."""
        fa.start()
        fb.start()
        try:
            wait_steps([fa, fb], BASELINE_STEPS, 60.0, label)
            ran: bool = True
            why: str = ""
        except LabError as exc:
            ran, why = False, str(exc)
        listening: list[str] = lab.listeners(SHARED_NS)
        fa.stop()
        fb.stop()
        own: dict[str, Match] = {
            f.site.code: compare(f.io().values.get(f.stand_ins[0], {}), refs[f.site.code]) for f in (fa, fb)
        }
        cross: dict[str, Match] = {
            fa.site.code: compare(fa.io().values.get(fa.stand_ins[0], {}), refs[fb.site.code]),
            fb.site.code: compare(fb.io().values.get(fb.stand_ins[0], {}), refs[fa.site.code]),
        }
        # If the option did not separate the sites, the second broker would not
        # start, a federate would be refused, or a stand-in would receive the
        # other site's values.
        ev.check(
            label,
            ran
            and all(m.steps >= MIN_COMPARED_STEPS and m.mismatched == 0 for m in own.values())
            and all(m.equal_steps == 0 for m in cross.values()),
            f"{how}; listeners: {', '.join(listening)}; "
            + "; ".join(
                f"{code}: {m.compared} values over {m.steps} steps, {m.mismatched} not equal" for code, m in own.items()
            )
            + (f"; {why}" if why else ""),
        )

    # --- the broker of the pip wheel is a Python wrapper around the real program
    lone: Federation = fed(a, "wrapper")
    wrapper = lone.start_broker()
    time.sleep(1.5)
    wrapper.terminate()  # the PID that `$!` gives a start script, and that `kill` or `pkill -x` finds
    settle([wrapper], 5.0)
    time.sleep(1.0)
    still: list[str] = lab.listeners(SHARED_NS)
    ev.observe(
        "A SIGTERM to the process that the command `helics_broker` starts",
        f"`helics_broker` of the pip wheel is a Python script that runs the broker as a child. The script exited "
        f"(code {wrapper.returncode}), and the broker went on: it still listens on {', '.join(still) or 'nothing'}. "
        "A signal to the whole process group, as systemd sends to a unit, stops both.",
    )
    lone.stop()
    time.sleep(1.0)

    # --- where a broker listens
    plain: Federation = fed(a, "plain", broker_args=())
    plain.start_broker()
    time.sleep(1.5)
    plain_listens: list[str] = lab.listeners(SHARED_NS)
    plain.stop()
    time.sleep(1.0)
    # templates/helics_broker.mako of the phenix `ot-sim` app.
    web_args: tuple[str, ...] = ("--ipv4", "--web", "--http_server_args=--http_port=8080 --external")
    web: Federation = fed(a, "web", broker_args=web_args)
    web.start_broker()
    time.sleep(2.5)
    web_listens: list[str] = lab.listeners(SHARED_NS)
    api_bind: str = can_connect(SHARED_NS, "127.0.0.1", 8080, bind=True)
    web.stop()
    time.sleep(1.0)
    ev.observe(
        "Where a broker listens",
        f"With no interface option: {', '.join(plain_listens)}. With `--ipv4`, as the `helics` app starts it: every "
        f"address (see the next row). With the options of the `ot-sim` app (`{' '.join(web_args)}`): "
        f"{', '.join(web_listens)}. A server that then asks for 127.0.0.1:8080, as the event API of `sim-ctl` does, "
        f"gets: {api_bind}.",
    )

    # --- by default: the command line of the `helics` app, twice on one network
    first: Federation = fed(a, "default", log_level="connections")
    second: Federation = fed(b, "default")
    first.start_broker()
    time.sleep(1.5)
    began: float = time.monotonic()
    second_broker = second.start_broker()
    settle([second_broker], 20.0)
    ev.observe(
        "By default, a second broker on the same network and port",
        f"`{' '.join(second.broker_command()[1:5])}` exited with code {second_broker.returncode} after "
        f"{time.monotonic() - began:.1f} s ({tail(second.dir / 'broker.out', 3)[-180:]}). "
        f"The first broker listens on {', '.join(lab.listeners(SHARED_NS))}. The app starts the broker with `&`, "
        "so nothing reports the exit.",
        hazard=True,
    )
    # The first site's cellar waits at its broker. The second site's field device
    # is pointed at its own site's broker address, where the first broker answers.
    first.start_federate("cellar")
    time.sleep(3.0)
    second.start_io()
    try:
        wait_steps([second], 6, 40.0, "the second site's stand-in runs in the first site's federation")
        joined: bool = True
    except LabError:
        joined = False
    stray: IoLog = second.io()
    as_first: Match = compare(stray.values.get(second.stand_ins[0], {}), refs[a.code])
    as_own: Match = compare(stray.values.get(second.stand_ins[0], {}), refs[b.code])
    ev.observe(
        "By default, a field device of one site that reaches the waiting broker of another site",
        f"`rio-cellar` of {b.code}, pointed at {b.broker_address}, joined the federation of `{a.broker_name}`: "
        f"{joined}. It received {as_first.compared} values over {as_first.steps} steps. In {as_first.equal_steps} of "
        f"those steps, every value equals the reference of {a.code}. In {as_own.equal_steps} steps, every value "
        f"equals the reference of its own site. The broker's federates: {stray.federates}. "
        "No message names the wrong site.",
        hazard=joined,
    )
    # Now the first site's own field device arrives. Its name is taken, and the federation runs.
    late_io: Federation = fed(a, "late")
    late = late_io.start_io()
    settle([late], 30.0)
    time.sleep(3.0)
    refusal: str = "; ".join(late_io.io().errors)[-200:] or tail(late_io.dir / "io.out", 2)[-200:]
    cellar_state: str = (
        "still runs" if first.procs["cellar"].poll() is None else f"exited with code {first.procs['cellar'].returncode}"
    )
    dropped: list[str] = [
        line.split("] ", 2)[-1]
        for line in (first.dir / "broker.out").read_text(errors="replace").splitlines()
        if "disconnect" in line
    ]
    ev.observe(
        "A federate that a running broker refuses",
        f"`rio-cellar` of {a.code} came late to its own broker and got: {refusal}. "
        f"After the refusal, the first federate of that federation (`cellar` of {a.code}) {cellar_state} "
        f"({tail(first.dir / 'cellar.out', 1)[-160:]}). The broker's log: "
        f"{' / '.join(dropped[:2]) or 'no disconnect'}. "
        "The broker dropped the first core that had registered, not the one that it refused.",
        hazard=first.procs["cellar"].poll() is not None,
    )
    for f in (first, second, late_io):
        f.stop()

    # --- a broker key
    keyed: Federation = fed(a, "key", broker_key=f"{a.code}-key")
    stranger: Federation = fed(b, "key", broker_key=f"{b.code}-key")
    keyed.start_broker()
    time.sleep(1.5)
    early = stranger.start_io()
    settle([early], 30.0)
    early_error: str = "; ".join(stranger.io().errors)[-160:] or tail(stranger.dir / "io.out", 2)[-160:]
    keyed.start_federate("cellar")
    time.sleep(4.0)
    stranger2: Federation = fed(b, "key2", broker_key=f"{b.code}-key")
    wrong = stranger2.start_io()
    settle([wrong], 30.0)
    time.sleep(3.0)
    victim_state: str = (
        "still runs" if keyed.procs["cellar"].poll() is None else f"exited with code {keyed.procs['cellar'].returncode}"
    )
    ev.observe(
        "A broker key (`--brokerkey`, or HELICS_BROKER_KEY for a federate)",
        f"The broker of {a.code} has the key `{a.code}-key`. A stand-in of {b.code} with the key `{b.code}-key` "
        f"got: {early_error}. It did not join (federates created: {stranger.io().created}). "
        f"The same refusal after `cellar` of {a.code} "
        f"registered with the right key from its environment: that cellar {victim_state} "
        f"({tail(keyed.dir / 'cellar.out', 1)[-120:]}). A key stops the join. It does not protect the federation "
        "that the stranger reached.",
        hazard=keyed.procs["cellar"].poll() is not None,
    )
    for f in (keyed, stranger, stranger2):
        f.stop()
    right: Federation = fed(a, "key3", broker_key=f"{a.code}-key")
    right.start()
    try:
        wait_steps([right], 8, 60.0, "a federation with a key runs")
        key_ok: bool = True
    except LabError:
        key_ok = False
    right.stop()
    key_match: Match = compare(right.io().values.get(right.stand_ins[0], {}), refs[a.code])
    ev.observe(
        "The real federate takes the broker key from its environment",
        f"With HELICS_BROKER_KEY in the federate's environment file and no change to `federate.py`, "
        f"the federation ran: {key_ok} ({key_match.compared} values, {key_match.mismatched} not equal to the "
        "reference). The port check does the same with HELICS_BROKER_PORT.",
    )

    # --- a broker name
    named: Federation = fed(a, "name")
    named.start_broker()
    named.start_federate("cellar")
    named.start_io(init=f"--brokername={b.broker_name}")
    try:
        wait_steps([named], 6, 40.0, "a federation whose stand-in names another broker runs")
        name_joined: bool = True
    except LabError:
        name_joined = False
    named.stop()
    ev.observe(
        "A broker name (`--name` at the broker, `--brokername` at the federate)",
        f"A stand-in that asked for the broker `{b.broker_name}` at the address of `{a.broker_name}` joined: "
        f"{name_joined}. The zmq core finds its broker by address and port. It does not compare the name.",
        hazard=name_joined,
    )

    # --- what separates two sites on one network
    separate(
        "shared network: each broker bound to its own site's address keeps two federations apart on the default port",
        fed(a, "bound", broker_args=("--ipv4", "--local_interface", a.broker_address)),
        fed(b, "bound", broker_args=("--ipv4", "--local_interface", b.broker_address)),
        "brokers with `--local_interface <the site's sim-ctl address>`, federates with their site's `--broker_address`",
    )
    separate(
        "shared network: a port for each site keeps two federations apart on one address",
        fed(a, "port", broker_port=BROKER_PORT),
        fed(b, "port", broker_port=BROKER_PORT + 100),
        f"brokers with `--port {BROKER_PORT}` and `--port {BROKER_PORT + 100}` on all addresses, "
        "federates with HELICS_BROKER_PORT in their environment",
    )


# ---------------------------------------------------------------- helpers that run inside a namespace


def io_main(args: argparse.Namespace) -> int:
    """Stand-ins for the ot-sim I/O modules of one site: one HELICS federate for each device."""
    import helics as h  # type: ignore[import-untyped]  # noqa: PLC0415

    doc: dict[str, Any] = json.loads(Path(args.config).read_text())
    types: dict[str, str] = {
        pub["key"]: pub["type"] for fed in doc["federates"].values() for pub in fed["publications"]
    }
    out = Path(args.out).open("a", buffering=1)  # noqa: SIM115 - open for the whole life of the process

    def emit(record: dict[str, Any]) -> None:
        out.write(json.dumps(record) + "\n")

    stop: list[bool] = []
    signal.signal(signal.SIGTERM, lambda _signum, _frame: stop.append(True))
    feds: dict[str, Any] = {}
    subs: dict[str, dict[str, Any]] = {}
    endpoints: dict[str, Any] = {}
    try:
        for name, spec in doc["io_federates"].items():
            info = h.helicsCreateFederateInfo()
            h.helicsFederateInfoSetCoreTypeFromString(info, doc["broker"]["core_type"])
            h.helicsFederateInfoSetCoreInitString(
                info, f"--federates=1 --broker_address={args.broker_address} {args.init}"
            )
            h.helicsFederateInfoSetTimeProperty(info, h.HELICS_PROPERTY_TIME_PERIOD, PERIOD_S)
            fed = h.helicsCreateCombinationFederate(spec["federate_name"], info)
            feds[name] = fed
            subs[name] = {key: h.helicsFederateRegisterSubscription(fed, key, "") for key in spec["subscriptions"]}
            endpoints[name] = h.helicsFederateRegisterGlobalEndpoint(fed, spec["endpoint"], "")
            emit({"e": "created", "f": name})
        for fed in feds.values():
            h.helicsFederateEnterExecutingModeAsync(fed)
        for fed in feds.values():
            h.helicsFederateEnterExecutingModeComplete(fed)
        emit({"e": "executing", "wall": time.time()})
        asker = next(iter(feds.values()))
        query = h.helicsCreateQuery("root", "federates")
        emit({"e": "query", "federates": h.helicsQueryExecute(query, asker)})
        done: int = 0
        t: float = 0.0
        while not stop:
            for fed in feds.values():
                h.helicsFederateRequestTimeAsync(fed, t + PERIOD_S)
            granted: dict[str, float] = {name: h.helicsFederateRequestTimeComplete(fed) for name, fed in feds.items()}
            t = min(granted.values())
            if max(granted.values()) > float(doc["end_time_s"]) + PERIOD_S:
                # HELICS grants its largest time when the federation is over or broken.
                emit({"e": "ended", "wall": time.time()})
                break
            wall: float = time.time()
            for name in feds:
                got: dict[str, Value] = {}
                for key, sub in subs[name].items():
                    if h.helicsInputIsUpdated(sub):
                        kind: str = types.get(key, "double")
                        if kind == "boolean":
                            got[key] = bool(h.helicsInputGetBoolean(sub))
                        elif kind == "string":
                            got[key] = str(h.helicsInputGetString(sub))
                        else:
                            got[key] = float(h.helicsInputGetDouble(sub))
                emit({"e": "step", "f": name, "t": granted[name], "wall": wall, "v": got})
            lines: list[str] = Path(args.commands).read_text().splitlines() if args.commands else []
            for line in lines[done:]:
                command: dict[str, Any] = json.loads(line)
                h.helicsEndpointSendBytesTo(
                    endpoints[command["from"]], json.dumps(command["payload"]).encode(), command["to"]
                )
                emit({"e": "sent", "f": command["from"], "t": granted[command["from"]], "to": command["to"]})
            done = len(lines)
            if t >= float(doc["end_time_s"]):
                break
        for fed in feds.values():
            h.helicsFederateDisconnect(fed)
    except Exception as exc:  # every HELICS failure is a result to record
        emit({"e": "error", "f": ",".join(feds) or "none", "msg": f"{type(exc).__name__}: {exc}"[:400]})
        return 3
    return 0


def unit_main(args: argparse.Namespace) -> int:
    """A stand-in for a systemd unit with `Restart=always`: start a command again after each exit.

    SIGUSR1 stops the command once, as `systemctl restart` does. SIGTERM ends the unit.
    """
    command: list[str] = [word for word in args.command if word != "--"]
    child: list[subprocess.Popen[bytes]] = []
    ended: list[bool] = []

    def stop_child(_signum: int, _frame: FrameType | None) -> None:
        if child and child[0].poll() is None:
            child[0].terminate()

    def end(signum: int, frame: FrameType | None) -> None:
        ended.append(True)
        stop_child(signum, frame)

    signal.signal(signal.SIGUSR1, stop_child)
    signal.signal(signal.SIGTERM, end)
    while not ended:
        started: float = time.time()
        proc: subprocess.Popen[bytes] = subprocess.Popen(command)
        child[:] = [proc]
        code: int = proc.wait()
        record: dict[str, Any] = {
            "unit": "run",
            "started": started,
            "ran_s": round(time.time() - started, 1),
            "exit": code,
        }
        print(json.dumps(record), flush=True)
        wake: float = time.monotonic() + args.restart_sec
        while not ended and time.monotonic() < wake:
            time.sleep(0.2)
    return 0


def http_main(args: argparse.Namespace) -> int:
    request = urllib.request.Request(
        f"http://127.0.0.1:8080{args.path}",
        method=args.method,
        data=args.body.encode() if args.body else None,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10.0) as reply:
            print(reply.read().decode())
    except (urllib.error.URLError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


def probe_main(args: argparse.Namespace) -> int:
    if args.bind:
        with socket.socket() as server:
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                server.bind((args.address, args.port))
                print("bound")
            except OSError as exc:
                print(f"no bind ({exc})")
        return 0
    try:
        with socket.create_connection((args.address, args.port), timeout=3.0):
            print("open")
    except OSError as exc:
        print(f"no connection ({exc})")
    return 0


# ---------------------------------------------------------------- evidence


def cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def tidy(text: str, work: Path) -> str:
    """A command line without the paths of this machine."""
    return (
        text.replace(str(work), "<work>")
        .replace(str(Path(sys.executable).parent), "<venv>/bin")
        .replace(str(HERE), ".")
    )


def write_evidence(result: Result) -> None:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    (EVIDENCE / "result.json").write_text(result.model_dump_json(indent=2) + "\n")
    lines: list[str] = [f"# {result.spike}", "", "## Checks", "", "| Check | Result | Detail |", "|---|---|---|"]
    for item in result.checks:
        lines.append(f"| {cell(item.name)} | {'pass' if item.passed else '**fail**'} | {cell(item.detail)} |")
    lines += ["", "## What HELICS did", "", "| Case | Observed | A way to harm or join another site |", "|---|---|---|"]
    for note in result.observations:
        lines.append(f"| {cell(note.name)} | {cell(note.observed)} | {'yes' if note.hazard else 'no'} |")
    for title, rows in result.tables.items():
        if not rows:
            continue
        head: list[str] = list(rows[0])
        lines += ["", f"## {title}", "", "| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
        lines += ["| " + " | ".join(cell(row.get(col, "")) for col in head) + " |" for row in rows]
    lines += ["", "## Versions and facts", "", "| Item | Value |", "|---|---|"]
    for key, value in {**result.versions, **result.facts}.items():
        lines.append(f"| {cell(key)} | {cell(value)} |")
    (EVIDENCE / "summary.md").write_text("\n".join(lines) + "\n")


def versions() -> dict[str, str]:
    import helics as h  # noqa: PLC0415

    return {
        "Python": platform.python_version(),
        "helics (pip)": str(h.helicsGetVersion()),
        "helics_broker": run([str(BROKER_BIN), "--version"]).stdout.strip(),
        "Kernel": platform.release(),
        "CPUs": str(os.cpu_count()),
    }


# ---------------------------------------------------------------- main


def raise_interrupt(_signum: int, _frame: FrameType | None) -> None:
    raise KeyboardInterrupt


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument(
        "--down", action="store_true", help="remove the namespaces of a run that did not clean up, and exit"
    )
    parser.add_argument(
        "--only", choices=["ci", "full", "fleet", "shared"], action="append", help="run one part (repeat for more)"
    )
    parser.add_argument(
        "--sites", default=",".join(s.code for s in SITES), help="site codes, in fleet order (default: all four)"
    )
    parser.add_argument("--keep-work", action="store_true", help="keep the logs and site files of the run")
    sub = parser.add_subparsers(dest="helper")
    io = sub.add_parser("_io")
    io.add_argument("--config", required=True)
    io.add_argument("--broker-address", required=True)
    io.add_argument("--init", default="")
    io.add_argument("--out", required=True)
    io.add_argument("--commands", default="")
    web = sub.add_parser("_http")
    web.add_argument("method")
    web.add_argument("path")
    web.add_argument("body", nargs="?")
    unit = sub.add_parser("_unit")
    unit.add_argument("--restart-sec", type=float, default=RESTART_SEC)
    unit.add_argument("command", nargs=argparse.REMAINDER)
    probe = sub.add_parser("_probe")
    probe.add_argument("address")
    probe.add_argument("port", type=int)
    probe.add_argument("--bind", action="store_true")
    args = parser.parse_args()
    if args.helper == "_io":
        return io_main(args)
    if args.helper == "_http":
        return http_main(args)
    if args.helper == "_unit":
        return unit_main(args)
    if args.helper == "_probe":
        return probe_main(args)

    if os.geteuid() != 0:
        print("run as root", file=sys.stderr)
        return 2
    if args.down:
        remove_left_over()
        return 0
    if not BROKER_BIN.exists():
        print(f"{BROKER_BIN} is missing: run this script with the Python of a venv that has `helics`", file=sys.stderr)
        return 2
    left: list[str] = [name for name in existing_namespaces() if name in lab_names()]
    if left:
        print(f"namespaces of an earlier run exist: {left}. Remove them with --down.", file=sys.stderr)
        return 2
    sites: list[Site] = [site for site in SITES if site.code in args.sites.split(",")]
    if len(sites) < 2:
        print("at least two sites", file=sys.stderr)
        return 2
    parts: list[str] = args.only or ["ci", "full", "fleet", "shared"]

    signal.signal(signal.SIGTERM, raise_interrupt)
    adopt_orphans()
    work: Path = Path(tempfile.mkdtemp(prefix="cm-site-brokers-"))
    lab: Lab = Lab(work)
    ev: Checks = Checks()
    ev.result.versions = versions()
    ev.result.facts["Sites"] = ", ".join(f"{s.code} (broker {s.broker_address})" for s in sites)
    ev.result.facts["Parts"] = ", ".join(parts)
    started: float = time.monotonic()
    todo: dict[str, Callable[[Lab, Checks, Sequence[Site]], None]] = {
        "ci": part_ci,
        "full": part_full,
        "fleet": part_fleet,
        "shared": part_shared,
    }
    try:
        if set(parts) - {"shared"}:
            for site in sites:
                lab.site_network(site)
        studies: list[RestartStudy] = [RestartStudy(lab, part) for part in STUDY_SITES]
        for number, part in enumerate(parts):
            log(f"part: {part}")
            running: list[subprocess.Popen[bytes]] = list(lab.procs)
            try:
                todo[part](lab, ev, sites)
            except (LabError, subprocess.SubprocessError, OSError, KeyError, IndexError, ValueError) as exc:
                # A part that cannot finish is a failed check, not a crash without a record.
                ev.check(f"{part}: the part ran to its end", False, f"{type(exc).__name__}: {exc}"[:1500])
            lab.stop([proc for proc in lab.procs if proc not in running])
            if number == 0:
                for study in studies:
                    study.restart()
        for study in studies:
            study.report(ev)
    except KeyboardInterrupt:
        ev.check("the run reached its end", False, "interrupted by a signal")
    finally:
        lab.down()
        left = [name for name in existing_namespaces() if name in lab_names()]
        ev.result.facts["Run time"] = f"{time.monotonic() - started:.0f} s"
        ev.result.facts["Namespaces left after the run"] = ", ".join(left) or "none"
        ev.result.facts = {key: tidy(value, work) for key, value in ev.result.facts.items()}
        for item in ev.result.checks:
            item.detail = tidy(item.detail, work)
        for note in ev.result.observations:
            note.observed = tidy(note.observed, work)
        write_evidence(ev.result)
        if args.keep_work:
            log(f"work directory kept: {work}")
        else:
            shutil.rmtree(work, ignore_errors=True)
    passed: int = sum(1 for c in ev.result.checks if c.passed)
    log(f"evidence in {EVIDENCE}")
    print(f"{passed} of {len(ev.result.checks)} checks passed: {'PASS' if ev.result.passed else 'FAIL'}", flush=True)
    return 0 if ev.result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
