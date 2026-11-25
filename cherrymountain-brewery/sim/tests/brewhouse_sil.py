"""Software in the loop for the brewhouse: the real control path, offline.

* the PLC is plc-brewhouse's Structured Text, compiled by strucpp and g++ and run
  by ``st_engine`` (what OpenPLC runs, minus its image-table binding), scanned every
  100 ms through :class:`~cherrymountain_sim.plcloop.PlcLink`, which wires it to the
  brewhouse federate by plantgen's tag table;
* the plant is the brewhouse and cellar federates (:class:`LocalFederation`);
* the batch engine is the script of the generated scada-fuxa project, run by
  ``fuxa/tests/engine_runner.mjs`` under Node against a stub of FUXA's script API.

Only the transports are replaced: the Modbus link of the RIO (register values copied
with ot-sim's scaling), the OPC UA link of FUXA (the registers as the driver holds
them, once a second), and HELICS (LocalFederation's one-step routing).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import st_engine

from cherrymountain_sim.brewhouse import BrewhouseFederate
from cherrymountain_sim.federate import HelicsConfig
from cherrymountain_sim.local import LocalFederation
from cherrymountain_sim.plcloop import PlcLink

ROOT: Path = Path(__file__).resolve().parents[2]
RUNNER: Path = ROOT / "fuxa" / "tests" / "engine_runner.mjs"
SCANS_PER_SECOND: int = 10  # the PLC task's 100 ms interval; timed steps count scans


def skip_reason() -> str | None:
    """Why the SIL cannot run here, or None."""
    return st_engine.toolchain_gap() or None


class NodeEngine:
    """The engine script in ``engine_runner.mjs``: one request, one reply."""

    def __init__(self, project: Path, writable: list[str], epoch: float) -> None:
        node = shutil.which("node")
        assert node is not None
        self.proc: subprocess.Popen[str] = subprocess.Popen(  # noqa: S603
            [node, str(RUNNER)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1
        )
        self.send({"op": "init", "project": str(project), "writable": writable, "t": epoch})

    def send(self, message: dict[str, Any]) -> dict[str, Any]:
        assert self.proc.stdin is not None and self.proc.stdout is not None
        self.proc.stdin.write(json.dumps(message) + "\n")
        self.proc.stdin.flush()
        reply: dict[str, Any] = json.loads(self.proc.stdout.readline())
        assert "error" not in reply, reply["error"]
        return reply

    def close(self) -> None:
        if self.proc.stdin is not None:
            self.proc.stdin.close()
        self.proc.wait(timeout=10)


class ControlPath:
    """The brewhouse federate and the cellar, the compiled PLC, and the engine,
    advanced together one second at a time."""

    def __init__(
        self,
        cfg: HelicsConfig,
        plc: st_engine.CompiledPlc,
        project: Path,
        *,
        engine_every_s: int = 2,
        epoch: float = 1_784_000_000.0,
    ) -> None:
        self.lf: LocalFederation = LocalFederation(cfg, names=["brewhouse", "cellar"])
        bh = self.lf.feds["brewhouse"]
        assert isinstance(bh, BrewhouseFederate)
        self.bh: BrewhouseFederate = bh
        self.plc: st_engine.CompiledPlc = plc
        tags_json = ROOT / "plant" / "generated" / cfg.variant / "tags.json"
        self.link: PlcLink = PlcLink("plc-brewhouse", plc, tags_json)
        self.epoch: float = epoch
        self.engine_every_s: int = engine_every_s
        self.t: int = 0
        self.engine_up: bool = True
        self.last: dict[str, Any] = {}
        self.max_ibu: float = 0.0
        self.entry: dict[str, bool] = {}   # plc-cellar's entry bits, by tag name (default: clear)
        fuxa: dict[str, Any] = json.loads(
            (ROOT / "plant" / "generated" / cfg.variant / "fuxa" / "scada-fuxa-tags.json").read_text(encoding="utf-8")
        )
        plc_dev: dict[str, Any] = next(d for d in fuxa["devices"] if d["name"] == "plc-brewhouse")
        self.opcua: dict[str, dict[str, Any]] = {t["name"]: t for t in plc_dev["tags"]}
        self.writable: list[str] = [n for n, t in self.opcua.items() if t["access"] == "read-write"]
        self.engine: NodeEngine = NodeEngine(project, self.writable, epoch)

    # -- the OPC UA and Modbus views FUXA has -------------------------------------

    def _opcua_snapshot(self) -> dict[str, Any]:
        """What FUXA's OPC UA driver holds of plc-brewhouse: the registers (the script
        stub applies the tags' linear scale, as the driver does)."""
        out: dict[str, Any] = {}
        for name, t in self.opcua.items():
            raw = self.plc.get(t["variable"])
            out[name] = bool(raw) if t["type"] == "Bool" else raw
        return out

    def _cellar_snapshot(self) -> dict[str, Any]:
        """What FUXA's Modbus driver holds of plc-cellar for the four fermenters."""
        out: dict[str, Any] = {}
        for n in range(1, 5):
            extract = float(self.lf.values[f"cellar/fv10{n}.gravity_plato"])
            out[f"CEL.FV10{n}.DT01.PV"] = round(extract * 100) / 100
            lock = self.entry.get(f"CEL.ENTRY.XS0{n}.LOCKED", False)
            out[f"CEL.ENTRY.XS0{n}.LOCKED"] = lock
            out[f"CEL.ENTRY.XS1{n}.CIP_OK"] = not lock
        return out

    # -- time ---------------------------------------------------------------------

    def second(self) -> None:
        """One second of the whole plant: the federates, ten PLC scans, the engine."""
        self.lf.step(1.0)
        self.t += 1
        # The RIO is polled about once a second (plcloop.py): the inputs are read before
        # the last scan of the second and the outputs written after it.
        for k in range(SCANS_PER_SECOND - 1):
            self.plc.scan(round((self.t + k / SCANS_PER_SECOND) * 1e9))
        self.link.scan(self.lf.values, self.lf.feds, self.t + (SCANS_PER_SECOND - 1) / SCANS_PER_SECOND)
        self.max_ibu = max(self.max_ibu, self.bh.ibu)
        if self.engine_up and self.t % self.engine_every_s == 0:
            reply = self.engine.send({
                "op": "tick", "t": self.epoch + self.t,
                "plc": self._opcua_snapshot(), "cellar": self._cellar_snapshot(),
            })
            self.last = reply
            for name, raw in reply["writes"]:
                self.plc.set(self.opcua[name]["variable"], raw)

    def run(self, seconds: int) -> None:
        for _ in range(seconds):
            self.second()

    def until(self, predicate: Any, max_seconds: int, what: str) -> None:
        for _ in range(max_seconds):
            if predicate():
                return
            self.second()
        if not predicate():
            raise AssertionError(f"timed out after {max_seconds} s waiting for {what}: "
                                 f"{self.last.get('tags', {}).get('BATCH.STATUS')}")

    def event(self, kind: str, **params: Any) -> None:
        """A scenario event, as the weather federate forwards it to the brewhouse."""
        self.bh.handle_message(json.dumps({"event": {"type": kind, "params": params}}))

    def tag(self, name: str) -> Any:
        return self.last.get("tags", {}).get(name)

    def close(self) -> None:
        self.engine.close()
