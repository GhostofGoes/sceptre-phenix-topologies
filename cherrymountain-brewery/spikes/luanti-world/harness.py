"""Spike harness: cm-viz's offline harness plus an allowlisted control relay.

It runs cm-viz unchanged (LocalSource, the page, /frame.json, /ws on 127.0.0.1:8090)
and adds a relay on 127.0.0.1:8092 that turns a short list of named actions into
scenario steps. This stands in for the range's relay to the sim-ctl event API
(DESIGN.md §9.5): the game never writes to the plant directly, only names an
action from the allowlist, and every accepted action is logged with who sent it.

    POST /control {"action": "fans-on", "by": "whitecell"} -> 202
    GET  /control                                          -> 200 {"actions": [...], "log": [...]}
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from cherrymountain_viz.scenarios import SCENARIOS, SITE_PSIA, Step
from cherrymountain_viz.server import run
from cherrymountain_viz.sources import LocalSource

LOG = logging.getLogger("spike-harness")


def _write(federate: str, tags: dict[str, object], note: str) -> Step:
    return Step(
        at_s=0,
        kind="write",
        federate=federate,
        payload=[{"tag": t, "value": v} for t, v in tags.items()],
        note=note,
    )


def _event(federate: str, kind: str, params: dict[str, object], note: str) -> Step:
    return Step(
        at_s=0, kind="event", federate=federate, payload={"type": kind, "params": params}, note=note
    )


#: The whole allowlist. Anything else is refused with a 400.
ACTIONS: dict[str, Step] = {
    "fans-on": _write(
        "building",
        {"bas-vent.purge_fan_1": True, "bas-vent.purge_fan_2": True},
        "control: purge fans on",
    ),
    "fans-off": _write(
        "building",
        {"bas-vent.purge_fan_1": False, "bas-vent.purge_fan_2": False},
        "control: purge fans off",
    ),
    "co2-release": _event(
        "cellar",
        "co2-release",
        {"fv": "FV-102", "rate": "high", "duration": 3600},
        "control: CO2 release at FV-102",
    ),
    "utility-open": _event(
        "power", "utility-breaker", {"action": "open"}, "control: utility breaker open"
    ),
    "utility-close": _event(
        "power", "utility-breaker", {"action": "close"}, "control: utility breaker close"
    ),
    "spunding-attack": _write(
        "cellar",
        {"fv102.spund_sp_psia": SITE_PSIA + 30.0},
        "control: FV-102 spunding setpoint 30 psig",
    ),
}


class Relay:
    def __init__(self, source: LocalSource) -> None:
        self.source = source
        self.log: list[dict[str, object]] = []
        self.lock = threading.Lock()

    def accept(self, action: str, by: str) -> dict[str, object]:
        step = ACTIONS[action].model_copy(update={"at_s": self.source.fed.t})
        # The federation thread applies due steps at its next advance.
        self.source.steps = sorted([*self.source.steps, step], key=lambda s: s.at_s)
        entry = {"action": action, "by": by, "sim_t_s": self.source.fed.t, "wall": time.time()}
        with self.lock:
            self.log.append(entry)
        LOG.info("control %s by %s at t=%.0f", action, by, self.source.fed.t)
        return entry

    def serve(self, port: int) -> ThreadingHTTPServer:
        relay = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: object) -> None:
                pass

            def _reply(self, code: int, doc: object) -> None:
                body = json.dumps(doc).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:
                with relay.lock:
                    self._reply(200, {"actions": sorted(ACTIONS), "log": relay.log})

            def do_POST(self) -> None:
                try:
                    doc = json.loads(
                        self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}"
                    )
                    action, by = str(doc.get("action", "")), str(doc.get("by", "?"))[:32]
                except (ValueError, AttributeError):
                    self._reply(400, {"error": "bad JSON"})
                    return
                if action not in ACTIONS:
                    self._reply(
                        400, {"error": f"unknown action {action!r}", "actions": sorted(ACTIONS)}
                    )
                    return
                self._reply(202, relay.accept(action, by))

        httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return httpd


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--helics", type=Path, required=True)
    ap.add_argument("--scenario", choices=sorted(SCENARIOS), default="none")
    ap.add_argument("--speed", type=float, default=5.0)
    ap.add_argument("--fast-forward", type=float, default=0.0)
    ap.add_argument("--port", type=int, default=8090)
    ap.add_argument("--control-port", type=int, default=8092)
    args = ap.parse_args()
    logging.basicConfig(level="INFO", format="%(asctime)s %(name)s %(message)s")
    source = LocalSource(
        args.helics, speed=args.speed, scenario=args.scenario, fast_forward_s=args.fast_forward
    )
    source.start()
    Relay(source).serve(args.control_port)
    asyncio.run(run(source, "127.0.0.1", args.port, 2.0))


if __name__ == "__main__":
    main()
