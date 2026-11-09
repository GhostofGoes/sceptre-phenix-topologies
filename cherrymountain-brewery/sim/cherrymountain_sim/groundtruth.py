"""Ground truth to the in-experiment OpenSearch on sim-ctl (DESIGN.md §9.5).

Each federate writes its true state (every published value plus model state
from :meth:`Federate.truth`) every ``interval_s`` simulated seconds to the
index ``<index_base>-<federate>-<YYYY.MM.DD>`` through the OpenSearch
(Elasticsearch-compatible) ``_bulk`` API. Writes happen in a background
thread, so a slow or absent OpenSearch never stalls the HELICS real-time
loop; failures are logged and the oldest documents are dropped when the
buffer is full.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import urllib.error
import urllib.request
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from cherrymountain_sim.federate import Federate, GroundTruthConfig

LOG: logging.Logger = logging.getLogger("cherrymountain_sim.groundtruth")
#: Documents buffered before the oldest are dropped.
MAX_BUFFER: int = 10000


class TruthWriter:
    """Buffers ground-truth documents and bulk-indexes them."""

    endpoint: str | None
    index_base: str
    federate: str
    variant: str
    interval_s: float
    next_t: float
    docs: queue.Queue[tuple[str, dict[str, Any]]]
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

    def document(self, fed: Federate) -> tuple[str, dict[str, Any]]:
        """(index, document) for the federate's current state."""
        now = fed.now
        doc = {
            "@timestamp": now.isoformat(),
            "federate": self.federate,
            "variant": self.variant,
            "t_s": fed.t,
            **fed.truth(),
        }
        return f"{self.index_base}-{self.federate}-{now:%Y.%m.%d}", doc

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
    def bulk_body(items: list[tuple[str, dict[str, Any]]]) -> bytes:
        """NDJSON body of a ``_bulk`` request."""
        lines = []
        for index, doc in items:
            lines.append(json.dumps({"index": {"_index": index}}))
            lines.append(json.dumps(doc, default=str))
        return ("\n".join(lines) + "\n").encode()

    def _flush(self) -> None:
        items = []
        while not self.docs.empty() and len(items) < 500:
            items.append(self.docs.get_nowait())
        if not items:
            return
        req = urllib.request.Request(  # noqa: S310 - http to the MGMT OpenSearch
            f"{self.endpoint}/_bulk",
            data=self.bulk_body(items),
            headers={"Content-Type": "application/x-ndjson"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310
                resp.read()
        except (urllib.error.URLError, OSError) as exc:
            LOG.warning("ground truth: %d documents not written: %s", len(items), exc)

    def _loop(self) -> None:
        while not self.stop.wait(5.0):
            self._flush()

    def close(self) -> None:
        """Flush what is left and stop the thread."""
        self.stop.set()
        if self.thread is not None:
            self._flush()
