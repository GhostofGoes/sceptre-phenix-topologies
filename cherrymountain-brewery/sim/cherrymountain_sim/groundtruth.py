"""Ground truth to the in-experiment OpenSearch on sim-ctl (DESIGN.md §9.5).

Each federate writes its true state (every published value plus model state
from :meth:`Federate.truth`) every ``interval_s`` federate seconds to the
index ``<index_base>-<federate>-<YYYY.MM.DD>`` through the OpenSearch
(Elasticsearch-compatible) ``_bulk`` API.

**Time.** ``@timestamp`` is the wall clock (UTC) when the document was taken,
and so is the date in the index name: the same clock Malcolm, Wazuh, and the
ot-sim ground-truth module stamp their records with, so "what happened" and
"what was observed" line up without a conversion, and the Grafana dashboard's
default range (around now) shows the run. The simulated date and time is in
``sim_time`` and the federate seconds in ``t_s``.

**Mapping.** Before its first write the writer puts an index template for
``<index_base>-*``. It covers this module's documents and those of the ot-sim
ground-truth module, which uses the same index base: ``@timestamp`` is a date
(the ot-sim module sends epoch milliseconds, which map to a number without a
template), ``federate``, ``source``, and ``field`` are keywords (a dashboard
groups by them), and every number is a double (a value first seen as ``0``
would otherwise make the field an integer and truncate what follows). The
ot-sim module's own template uses an Elasticsearch-only type and OpenSearch
refuses it; this one has a higher priority in any case.

Writes happen in a background thread, so a slow or absent OpenSearch never
stalls the HELICS real-time loop. A batch that cannot be sent (OpenSearch is
still starting, or restarting) is kept and sent again; the oldest documents
are dropped only when the buffer is full. Documents OpenSearch refuses are
logged with its reason.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import queue
import threading
import urllib.error
import urllib.request
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from cherrymountain_sim.federate import Federate, GroundTruthConfig

LOG: logging.Logger = logging.getLogger("cherrymountain_sim.groundtruth")
#: Documents buffered before the oldest are dropped.
MAX_BUFFER: int = 10000
#: Documents in one ``_bulk`` request.
BATCH: int = 500
#: Priority of the index template (the ot-sim module's has none, which is 0).
TEMPLATE_PRIORITY: int = 100

type Item = tuple[str, dict[str, Any]]


def index_template(index_base: str) -> dict[str, Any]:
    """The index template for ``<index_base>-*`` (see the module docstring)."""
    return {
        "index_patterns": [f"{index_base}-*"],
        "priority": TEMPLATE_PRIORITY,
        "template": {
            "settings": {"number_of_replicas": 0},
            "mappings": {
                "dynamic_templates": [
                    {
                        "numbers_are_doubles": {
                            "match_mapping_type": "long",
                            "mapping": {"type": "double"},
                        }
                    }
                ],
                "properties": {
                    "@timestamp": {"type": "date"},
                    "sim_time": {"type": "date"},
                    "federate": {"type": "keyword"},
                    "variant": {"type": "keyword"},
                    "t_s": {"type": "double"},
                    # The ot-sim ground-truth module's documents.
                    "source": {"type": "keyword"},
                    "field": {"type": "keyword"},
                    "value": {"type": "double"},
                },
            },
        },
    }


class TruthWriter:
    """Buffers ground-truth documents and bulk-indexes them."""

    endpoint: str | None
    index_base: str
    federate: str
    variant: str
    interval_s: float
    next_t: float
    docs: queue.Queue[Item]
    #: A batch that could not be sent, to send again before anything newer.
    retry: list[Item]
    template_done: bool
    thread: threading.Thread | None
    stop: threading.Event

    def __init__(
        self,
        endpoint: str | None,
        federate: str,
        variant: str,
        index_base: str = "cherrymountain-truth",
        interval_s: float = 10.0,
    ) -> None:
        self.endpoint = endpoint.rstrip("/") if endpoint else None
        self.index_base = index_base
        self.federate = federate
        self.variant = variant
        self.interval_s = interval_s
        self.next_t = 0.0
        self.docs = queue.Queue(MAX_BUFFER)
        self.retry = []
        self.template_done = False
        self.stop = threading.Event()
        self.thread = None
        if self.endpoint:
            self.thread = threading.Thread(target=self._loop, name="truth", daemon=True)
            self.thread.start()

    @classmethod
    def of(
        cls, cfg: GroundTruthConfig | None, federate: str, variant: str
    ) -> TruthWriter:
        """A writer for helics.json's ``ground_truth`` (inactive when absent)."""
        if cfg is None:
            return cls(None, federate, variant)
        return cls(cfg.endpoint, federate, variant, cfg.index_base)

    def document(self, fed: Federate, wall: dt.datetime | None = None) -> Item:
        """(index, document) for the federate's current state, stamped with
        the wall clock ``wall`` (now, UTC, when not given)."""
        wall = wall or dt.datetime.now(dt.UTC)
        doc = {
            "@timestamp": wall.isoformat(),
            "sim_time": fed.now.isoformat(),
            "federate": self.federate,
            "variant": self.variant,
            "t_s": fed.t,
            **fed.truth(),
        }
        return f"{self.index_base}-{self.federate}-{wall:%Y.%m.%d}", doc

    def record(self, fed: Federate) -> None:
        """Queue a document every ``interval_s`` federate seconds."""
        if self.endpoint is None or fed.t < self.next_t:
            return
        self.next_t = fed.t + self.interval_s
        item = self.document(fed)
        try:
            self.docs.put_nowait(item)
        except queue.Full:
            # Drop the oldest document. The flush thread may drain the queue
            # between the two calls, so neither may raise into the sim loop.
            try:
                self.docs.get_nowait()
            except queue.Empty:
                pass
            try:
                self.docs.put_nowait(item)
            except queue.Full:
                pass

    @staticmethod
    def bulk_body(items: list[Item]) -> bytes:
        """NDJSON body of a ``_bulk`` request."""
        lines = []
        for index, doc in items:
            lines.append(json.dumps({"index": {"_index": index}}))
            lines.append(json.dumps(doc, default=str))
        return ("\n".join(lines) + "\n").encode()

    def _send(
        self, method: str, path: str, body: bytes, ctype: str
    ) -> tuple[Literal["ok", "refused", "unreachable"], bytes]:
        """One request to OpenSearch: ``ok`` with the reply, ``refused`` (an
        HTTP error) with the reply, or ``unreachable`` with the reason."""
        req = urllib.request.Request(  # noqa: S310 - http to the MGMT OpenSearch
            f"{self.endpoint}{path}",
            data=body,
            headers={"Content-Type": ctype},
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310
                return "ok", resp.read()
        except urllib.error.HTTPError as exc:
            return "refused", exc.read()[:500]
        except (urllib.error.URLError, OSError) as exc:
            return "unreachable", str(exc).encode()

    def _put_template(self) -> bool:
        """Put the index template; False while OpenSearch cannot be reached.
        A refusal is logged and not tried again (the writes then go ahead with
        OpenSearch's own mapping)."""
        body = json.dumps(index_template(self.index_base)).encode()
        path = f"/_index_template/{self.index_base}-cm"
        state, reply = self._send("PUT", path, body, "application/json")
        if state == "unreachable":
            LOG.info("ground truth: OpenSearch is not reachable yet: %s", reply.decode())
            return False
        if state == "refused":
            LOG.warning("ground truth: index template refused: %s", reply.decode(errors="replace"))
        return True

    def _flush(self) -> None:
        """Send one batch: the batch to retry, or else up to :data:`BATCH`
        queued documents. A batch that cannot be sent is kept for the next
        call."""
        if not self.template_done:
            self.template_done = self._put_template()
            if not self.template_done:
                return
        items = self.retry
        self.retry = []
        while len(items) < BATCH:
            try:
                items.append(self.docs.get_nowait())
            except queue.Empty:
                break
        if not items:
            return
        state, reply = self._send("POST", "/_bulk", self.bulk_body(items), "application/x-ndjson")
        if state == "unreachable":
            self.retry = items
            LOG.warning(
                "ground truth: %d documents kept to send again: %s", len(items), reply.decode()
            )
            return
        if state == "refused":
            LOG.warning(
                "ground truth: %d documents refused: %s",
                len(items),
                reply.decode(errors="replace"),
            )
            return
        self._log_item_errors(reply, len(items))

    @staticmethod
    def _log_item_errors(reply: bytes, sent: int) -> None:
        """Log the documents a ``_bulk`` reply reports as failed (a mapping
        conflict, a full disk): the request itself succeeds either way."""
        try:
            doc = json.loads(reply)
        except ValueError:
            return
        if not isinstance(doc, dict) or not doc.get("errors"):
            return
        failed = [
            entry.get("index", {}).get("error")
            for entry in doc.get("items", [])
            if isinstance(entry, dict) and entry.get("index", {}).get("error")
        ]
        if failed:
            LOG.warning(
                "ground truth: OpenSearch rejected %d of %d documents; first reason: %s",
                len(failed),
                sent,
                json.dumps(failed[0])[:300],
            )

    def _loop(self) -> None:
        while not self.stop.wait(5.0):
            try:
                self._flush()
            except Exception:
                # The writer must outlive any one bad batch or reply.
                LOG.exception("ground truth: flush failed")

    def close(self) -> None:
        """Flush what is left and stop the thread."""
        self.stop.set()
        if self.thread is not None:
            self._flush()
