"""The plantevent component: metadata, tunnel lookup, and a stage against a
local stand-in for the sim-ctl event API."""

from __future__ import annotations

import io
import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from phenix_apps.apps.scorch.app import validate_metadata
from phenix_apps.testing.readme import readme_components

from cherrymountain_apps import simctl
from cherrymountain_apps.plantevent.plantevent import PlantEvent, PlantEventMetadata

README: Path = Path(__file__).parents[1] / "README.md"


# ---------------------------------------------------------------- metadata


def test_readme_has_examples() -> None:
    assert len(readme_components(README, "plantevent")) == 4


@pytest.mark.parametrize(
    "cmp", readme_components(README, "plantevent"), ids=lambda c: c["name"]
)
def test_readme_examples_are_valid_and_clean(cmp: dict[str, Any]) -> None:
    _, warnings = validate_metadata(PlantEventMetadata, cmp["metadata"], cmp["name"])
    assert warnings == []


def test_defaults() -> None:
    config, _ = validate_metadata(
        PlantEventMetadata, {"events": [{"event": "jam"}]}, "p"
    )
    assert (
        config.sim_ctl.hostname,
        config.sim_ctl.port,
        config.sim_ctl.create_tunnel,
    ) == ("sim-ctl", 8080, True)
    assert config.events[0].model_dump() == {
        "event": "jam",
        "machine": "labeler",
        "duration": 300.0,
    }


@pytest.mark.parametrize(
    ("metadata", "expected"),
    [
        ({}, "events: Field required"),
        ({"events": []}, "events: List should have at least 1 item"),
        ({"events": [{"event": "outage"}]}, "does not match any of the expected tags"),
        (
            {"events": [{"event": "utility-breaker"}]},
            "events.0.utility-breaker.action: Field required",
        ),
        (
            {"events": [{"event": "weather-step", "delta_f": "5"}]},
            "delta_f: Input should be a valid number",
        ),
        (
            {"events": [{"event": "co2-release", "fv": "fv102"}]},
            "fv: String should match pattern",
        ),
        (
            {"events": [{"event": "jam"}], "sim_ctl": {"port": 0}},
            "sim_ctl.port: Input should be greater than or equal to 1",
        ),
        (
            {"events": [{"event": "jam"}], "check_health": "false"},
            "check_health: Input should be a valid boolean",
        ),
    ],
)
def test_bad_metadata_fails(metadata: dict[str, Any], expected: str) -> None:
    with pytest.raises(ValueError, match="invalid metadata for component 'p'") as info:
        validate_metadata(PlantEventMetadata, metadata, "p")
    assert expected in str(info.value)


def test_unknown_keys_warn_with_did_you_mean() -> None:
    _, warnings = validate_metadata(
        PlantEventMetadata,
        {
            "events": [{"event": "co2-release", "fv": "FV-102", "rat": "low"}],
            "intrval": 3,
        },
        "p",
    )
    assert warnings == [
        "intrval: unknown key, ignored. Fix: remove it (did you mean 'interval'?)",
        "events.0.rat: unknown key, ignored. Fix: remove it (did you mean 'rate'?)",
    ]


# ---------------------------------------------------------------- tunnel lookup


def tunnels(*rows: list[str], host: str = "mm-head") -> list[dict[str, Any]]:
    return [
        {
            "Host": host,
            "Header": ["id", "src port", "dst", "dst port"],
            "Tabular": [list(r) for r in rows],
        }
    ]


def test_resolve_finds_the_random_host_port(monkeypatch: pytest.MonkeyPatch) -> None:
    mm = MagicMock()
    mm.cc_tunnel_list.return_value = tunnels(
        ["1", "52424", "127.0.0.1", "3000"], ["2", "57571", "127.0.0.1", "8080"]
    )
    monkeypatch.setattr(simctl, "is_headnode", lambda mm, host: True)
    ep = simctl.resolve(mm, "sim-ctl", 8080, create=False)
    assert (ep.host, ep.port, ep.cluster_host, ep.created) == (
        "127.0.0.1",
        57571,
        "mm-head",
        False,
    )
    mm.cc_tunnel.assert_not_called()


def test_resolve_uses_the_compute_node_for_a_remote_vm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mm = MagicMock()
    mm.cc_tunnel_list.return_value = tunnels(
        ["2", "61480", "127.0.0.1", "8080"], host="compute-3"
    )
    monkeypatch.setattr(simctl, "is_headnode", lambda mm, host: False)
    assert simctl.resolve(mm, "sim-ctl", 8080).url == "http://compute-3:61480"


def test_resolve_without_tunnel_and_without_create_fails() -> None:
    mm = MagicMock()
    mm.cc_tunnel_list.return_value = tunnels(["1", "52424", "127.0.0.1", "3000"])
    with pytest.raises(simctl.TunnelError, match="no cc tunnel to sim-ctl:8080"):
        simctl.resolve(mm, "sim-ctl", 8080, create=False)


def test_resolve_creates_a_tunnel_like_phenix(monkeypatch: pytest.MonkeyPatch) -> None:
    mm = MagicMock()
    created: list[tuple[Any, ...]] = []

    def tunnel(vm: str, src: int, host: str, dst: int) -> None:
        if not created:
            created.append(("taken", src))
            raise RuntimeError("listen tcp :x: bind: address already in use")
        created.append((vm, src, host, dst))
        mm.cc_tunnel_list.return_value = tunnels(["1", str(src), host, str(dst)])

    mm.cc_tunnel_list.return_value = []
    mm.cc_tunnel.side_effect = tunnel
    monkeypatch.setattr(
        simctl.utils, "mm_cc_client_locate", lambda mm, vm, grace: ("uuid", "mm-head")
    )
    monkeypatch.setattr(simctl, "is_headnode", lambda mm, host: True)
    ep = simctl.resolve(mm, "sim-ctl", 8080)
    vm, src, host, dst = created[-1]
    assert (vm, host, dst) == ("sim-ctl", "127.0.0.1", 8080)
    assert simctl.PORT_BASE <= src < simctl.PORT_BASE + simctl.PORT_RANGE
    assert (ep.port, ep.created) == (src, True)


def test_resolve_without_c2_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    mm = MagicMock()
    mm.cc_tunnel_list.side_effect = RuntimeError("no such client")

    def no_client(mm: Any, vm: str, grace: float) -> Any:
        raise TimeoutError(vm)

    monkeypatch.setattr(simctl.utils, "mm_cc_client_locate", no_client)
    with pytest.raises(simctl.TunnelError, match="no active C2 client"):
        simctl.resolve(mm, "sim-ctl", 8080)


# ---------------------------------------------------------------- a stage


class StandIn(BaseHTTPRequestHandler):
    """The event API contract, recorded."""

    posted: list[Any] = []  # noqa: RUF012 - shared by the handler instances
    fail: bool = False

    def _reply(self, code: int, body: Any) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        self._reply(200 if self.path == "/api/v1/health" else 404, {"status": "ok"})

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path != "/api/v1/events" or StandIn.fail:
            self._reply(400, {"error": "refused"})
            return
        StandIn.posted.append(body)
        self._reply(202, {"id": f"ev-{len(StandIn.posted)}", "accepted": True})

    def log_message(self, *args: Any) -> None:
        pass


@pytest.fixture
def api() -> Iterator[int]:
    StandIn.posted = []
    StandIn.fail = False
    server = ThreadingHTTPServer(("127.0.0.1", 0), StandIn)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_address[1]
    server.shutdown()


def experiment(metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        "spec": {
            "experimentName": "cherrymountain-ci",
            "baseDir": "/tmp/unused",
            "topology": {"nodes": []},
            "scenario": {
                "apps": [
                    {
                        "name": "scorch",
                        "metadata": {
                            "components": [
                                {
                                    "name": "outage",
                                    "type": "plantevent",
                                    "metadata": metadata,
                                }
                            ],
                            "runs": [
                                {
                                    "configure": [],
                                    "start": ["outage"],
                                    "stop": [],
                                    "cleanup": [],
                                }
                            ],
                        },
                    }
                ]
            },
        }
    }


def run_stage(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, metadata: dict[str, Any], mm: Any
) -> Path:
    monkeypatch.setenv("PHENIX_FILES_DIR", str(tmp_path))
    monkeypatch.setattr(
        "sys.argv",
        ["phenix-scorch-component-plantevent", "start", "outage", "0", "0", "0"],
    )
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(experiment(metadata))))
    monkeypatch.setattr(PlantEvent, "mm", property(lambda self: mm))
    PlantEvent()
    return tmp_path / "scorch" / "run-0" / "outage" / "loop-0-count-0"


def test_start_posts_through_the_looked_up_port(
    api: int, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    mm = MagicMock()
    mm.cc_tunnel_list.return_value = tunnels(["2", str(api), "127.0.0.1", "8080"])
    monkeypatch.setattr(simctl, "is_headnode", lambda mm, host: True)
    events = [
        {"event": "utility-breaker", "action": "open"},
        {"event": "co2-release", "fv": "FV-102"},
    ]
    out = run_stage(monkeypatch, tmp_path, {"events": events}, mm)
    assert StandIn.posted == [
        {"event": "utility-breaker", "action": "open"},
        {"event": "co2-release", "fv": "FV-102", "rate": "high", "duration": 600.0},
    ]
    record = json.loads((out / "plantevent_start.json").read_text())
    assert [r["reply"]["id"] for r in record] == ["ev-1", "ev-2"]
    (status,) = out.glob("start-*.json")
    assert json.loads(status.read_text())["return"] == "posted 2 event(s)"
    mm.cc_tunnel_list.assert_called_once_with("sim-ctl")


def test_a_refused_event_fails_the_stage(
    api: int, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    StandIn.fail = True
    with pytest.raises(RuntimeError, match="POST /api/v1/events: HTTP 400"):
        run_stage(
            monkeypatch,
            tmp_path,
            {
                "sim_ctl": {"url": f"http://127.0.0.1:{api}"},
                "events": [{"event": "jam"}],
            },
            MagicMock(),
        )


def test_url_skips_the_tunnel_lookup(
    api: int, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    mm = MagicMock()
    run_stage(
        monkeypatch,
        tmp_path,
        {
            "sim_ctl": {"url": f"http://127.0.0.1:{api}"},
            "events": [{"event": "weather-step", "delta_f": -12.5}],
        },
        mm,
    )
    assert StandIn.posted == [
        {"event": "weather-step", "delta_f": -12.5, "duration": 0.0}
    ]
    mm.cc_tunnel_list.assert_not_called()


def test_unknown_keys_are_not_sent(
    api: int, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run_stage(
        monkeypatch,
        tmp_path,
        {
            "sim_ctl": {"url": f"http://127.0.0.1:{api}"},
            "events": [{"event": "start-batch", "recipee": "x"}],
        },
        MagicMock(),
    )
    assert StandIn.posted == [
        {"event": "start-batch", "recipe": "fire-of-the-phenix", "brews": 2}
    ]


def test_headnode_check_without_the_phenix_apps_helper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Upstream phenix-apps has no is_headnode; the fork does.
    monkeypatch.delattr(simctl.utils, "is_headnode", raising=False)
    monkeypatch.setattr(simctl.socket, "gethostname", lambda: "range-phenix")
    assert simctl.is_headnode(MagicMock(), "range-minimega")
    assert not simctl.is_headnode(MagicMock(), "compute-3")
    assert simctl.connect_host(MagicMock(), "range") == "127.0.0.1"
