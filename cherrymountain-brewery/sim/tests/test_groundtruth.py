"""The ground-truth writer against a stand-in OpenSearch, the published
value types, and the event API's health states."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, ClassVar

import pytest

from cherrymountain_sim import simctl
from cherrymountain_sim.federate import HelicsConfig, build
from cherrymountain_sim.groundtruth import TruthWriter, index_template
from cherrymountain_sim.simctl import EventApi, Kpis

FULL: Path = Path(__file__).resolve().parents[2] / "plant" / "generated" / "full" / "helics.json"


class FakeOpenSearch:
    """Records the template and the bulk documents; can be told to answer a
    bulk request with a per-document error."""

    server: ThreadingHTTPServer
    thread: threading.Thread
    templates: dict[str, Any]
    docs: list[tuple[str, dict[str, Any]]]
    reject: bool

    def __init__(self) -> None:
        self.templates = {}
        self.docs = []
        self.reject = False
        store = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version: ClassVar[str] = "HTTP/1.1"

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                return

            def _reply(self, doc: dict[str, Any]) -> None:
                body = json.dumps(doc).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _body(self) -> bytes:
                return self.rfile.read(int(self.headers.get("Content-Length") or 0))

            def do_PUT(self) -> None:
                store.templates[self.path] = json.loads(self._body())
                self._reply({"acknowledged": True})

            def do_POST(self) -> None:
                lines = self._body().decode().splitlines()
                items = []
                for action, doc in zip(lines[0::2], lines[1::2], strict=True):
                    index = json.loads(action)["index"]["_index"]
                    if store.reject:
                        error = {"type": "mapper_parsing_exception", "reason": "no"}
                        items.append({"index": {"status": 400, "error": error}})
                    else:
                        store.docs.append((index, json.loads(doc)))
                        items.append({"index": {"status": 201}})
                self._reply({"errors": store.reject, "items": items})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        """The base URL."""
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self) -> None:
        """Stop serving."""
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def opensearch() -> Iterator[FakeOpenSearch]:
    """A stand-in OpenSearch on a free port."""
    store = FakeOpenSearch()
    yield store
    store.close()


def writer_for(url: str) -> TruthWriter:
    """A writer whose background thread is stopped: the test flushes."""
    writer = TruthWriter(url, "cellar", "full")
    writer.stop.set()
    assert writer.thread is not None
    writer.thread.join(10)
    return writer


def test_the_template_maps_both_kinds_of_document() -> None:
    """One template covers the federates' documents and the ot-sim
    ground-truth module's: dates, keywords to group by, and doubles."""
    template = index_template("cherrymountain-truth")
    assert template["index_patterns"] == ["cherrymountain-truth-*"]
    assert template["priority"] > 0
    props = template["template"]["mappings"]["properties"]
    assert props["@timestamp"] == {"type": "date"}
    assert props["federate"] == {"type": "keyword"}
    assert props["field"] == {"type": "keyword"} and props["value"] == {"type": "double"}
    dynamic = template["template"]["mappings"]["dynamic_templates"][0]["numbers_are_doubles"]
    assert dynamic == {"match_mapping_type": "long", "mapping": {"type": "double"}}


def test_the_writer_puts_the_template_then_the_documents(opensearch: FakeOpenSearch) -> None:
    """The template goes first, once; the documents follow in bulk."""
    fed = build(HelicsConfig.load(FULL), "cellar")
    writer = writer_for(opensearch.url)
    for _ in range(3):
        writer.record(fed)
        fed.step(10.0)
    writer._flush()
    writer._flush()
    assert list(opensearch.templates) == ["/_index_template/cherrymountain-truth-cm"]
    assert len(opensearch.docs) == 3
    index, doc = opensearch.docs[0]
    assert index.startswith("cherrymountain-truth-cellar-")
    assert doc["federate"] == "cellar" and doc["t_s"] == 0.0


def test_documents_wait_while_opensearch_is_down(opensearch: FakeOpenSearch) -> None:
    """OpenSearch on sim-ctl starts after the federates do. Documents taken
    meanwhile are kept and sent once it answers; they used to be dropped."""
    fed = build(HelicsConfig.load(FULL), "cellar")
    url = opensearch.url
    writer = writer_for("http://127.0.0.1:9")  # the discard port: refused
    for _ in range(4):
        writer.record(fed)
        fed.step(10.0)
    writer._flush()
    assert not writer.template_done and writer.docs.qsize() == 4
    writer.endpoint = url
    writer._flush()
    assert writer.template_done and len(opensearch.docs) == 4
    # A batch that fails in transit is sent again, ahead of newer documents.
    writer.record(fed)
    writer.endpoint = "http://127.0.0.1:9"
    writer._flush()
    assert len(writer.retry) == 1 and len(opensearch.docs) == 4
    fed.step(10.0)
    writer.record(fed)
    writer.endpoint = url
    writer._flush()
    assert not writer.retry and len(opensearch.docs) == 6
    assert opensearch.docs[4][1]["t_s"] < opensearch.docs[5][1]["t_s"]


def test_rejected_documents_are_logged(
    opensearch: FakeOpenSearch, caplog: pytest.LogCaptureFixture
) -> None:
    """A _bulk request succeeds even when OpenSearch rejects its documents;
    the writer logs the count and the first reason."""
    fed = build(HelicsConfig.load(FULL), "cellar")
    writer = writer_for(opensearch.url)
    writer.record(fed)
    opensearch.reject = True
    with caplog.at_level("WARNING"):
        writer._flush()
    assert "rejected 1 of 1 documents" in caplog.text
    assert "mapper_parsing_exception" in caplog.text


@pytest.mark.parametrize("name", ["brewhouse", "utilities", "packaging", "power", "building"])
def test_values_keep_their_type_when_a_plc_writes(name: str) -> None:
    """ot-sim sends a coil write as 1.0. The published value, and so the
    ground-truth document, stays a boolean for a boolean publication and a
    float for a double one, whoever wrote the command."""
    cfg = HelicsConfig.load(FULL)
    fed = build(cfg, name)
    fed.step(float(cfg.federates[name].period_s))
    types = {p.key: p.type for p in cfg.federates[name].publications}

    def check() -> None:
        for key, value in fed.values().items():
            want = bool if types[key] == "boolean" else str if types[key] == "string" else float
            assert type(value) is want, (key, value)

    check()
    writes = [{"tag": tag, "value": 1.0} for tag in cfg.federates[name].accepted_updates]
    assert fed.handle_message(json.dumps(writes))
    fed.step(float(cfg.federates[name].period_s))
    check()


def test_health_tells_a_running_federate_from_a_waiting_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The API answers before the HELICS federation starts and after it
    stalls; ``status`` is ``ok`` only while the federate steps."""
    api = EventApi("full")
    assert api.health()["status"] == "starting"
    assert api.health()["step_age_s"] is None
    api.update("2026-07-15T04:30:01-06:00", Kpis())
    health = api.health()
    assert health["status"] == "ok" and health["sim_time"] == "2026-07-15T04:30:01-06:00"
    now = time.time()
    monkeypatch.setattr(simctl.time, "time", lambda: now + simctl.STALLED_AFTER_S + 5.0)
    assert api.health()["status"] == "stalled"
    assert api.health()["step_age_s"] > simctl.STALLED_AFTER_S
