"""A stand-in for Chromium, for the kiosk tests (test_kiosk.py).

It is started by ``cm-kiosk`` as the browser. With ``--remote-debugging-pipe`` it
speaks the debugging protocol over the two inherited descriptors (3 in, 4 out; JSON
messages ended by a NUL byte), and it keeps one tab that behaves as Chromium 156 did
in the run that the app's README describes:

* a page load (start, ``Page.navigate``, ``Page.reload``) of an address that does
  not answer is the error page (``chrome-error://chromewebdata/``), which has no
  sessionStorage;
* a script from ``Page.addScriptToEvaluateOnNewDocument`` runs at each load, in the
  session that registered it, only after that session called ``Page.enable``, and
  goes with its session;
* the script is the one ``cm-kiosk`` writes: it puts its profile into sessionStorage
  when ``location.origin`` is its own origin. It is not run as JavaScript (the node
  test in test_kiosk.py does that); the two JSON strings in it are read.

Every command is appended, as a JSON line, to ``$FAKE_BROWSER_LOG``, with the
arguments the browser started with. ``$FAKE_BROWSER_DEAF`` makes it exit at once when
it is asked for a pipe (as Chromium does when descriptors 3 and 4 are not open), and
``$FAKE_BROWSER_HANG`` names a file: while it holds this process's id,
``Runtime.evaluate`` is not answered (a tab that crashed). ``$FAKE_BROWSER_SIGNIN``
names a file: when it exists, its text becomes the page's sessionStorage entry at the
next ``Runtime.evaluate``, and the file goes (a person signing in at the kiosk).
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

ERROR_PAGE = "chrome-error://chromewebdata/"


def log(event: str, **fields: Any) -> None:
    line = {"event": event, "pid": os.getpid(), "time": time.time(), **fields}
    with open(os.environ["FAKE_BROWSER_LOG"], "a", encoding="utf-8") as out:
        out.write(json.dumps(line) + "\n")


def literal_after(script: str, marker: str) -> str:
    """The JSON string literal that follows ``marker`` in the script."""
    start = script.index(marker) + len(marker)
    value, _ = json.JSONDecoder().raw_decode(script[start:])
    return str(value)


def answers(href: str) -> bool:
    try:
        urllib.request.urlopen(href, timeout=2).close()
    except urllib.error.HTTPError:
        return True
    except OSError:
        return False
    return True


class Tab:
    def __init__(self, href: str) -> None:
        self.sessions: dict[str, bool] = {}
        #: identifier -> (session, source)
        self.scripts: dict[str, tuple[str, str]] = {}
        self.href = href
        #: The address of the last navigation: what a reload of the error page loads.
        self.address = href
        self.storage: str | None = None
        self.counter = 0
        self.load(href)

    def load(self, href: str) -> None:
        self.href = href if answers(href) else ERROR_PAGE
        self.storage = None
        if self.href == ERROR_PAGE:
            return
        for session, source in self.scripts.values():
            if not self.sessions.get(session):
                continue
            origin = literal_after(source, "location.origin !== ")
            if urlsplit(self.href)[:2] == urlsplit(origin)[:2]:
                self.storage = literal_after(
                    source, "sessionStorage.setItem('currentUser', "
                )

    def handle(
        self, method: str, params: dict[str, Any], session: str | None
    ) -> dict[str, Any]:
        if method == "Target.getTargets":
            return {
                "targetInfos": [
                    {"targetId": "UI", "type": "browser_ui"},
                    {"targetId": "PAGE", "type": "page", "url": self.href},
                ]
            }
        if method == "Target.attachToTarget":
            name = f"S{len(self.sessions) + 1}"
            self.sessions[name] = False
            return {"sessionId": name}
        if method == "Target.detachFromTarget":
            gone = params["sessionId"]
            self.sessions.pop(gone, None)
            self.scripts = {k: v for k, v in self.scripts.items() if v[0] != gone}
            return {}
        if session not in self.sessions:
            raise LookupError(f"No session with given id: {session}")
        if method == "Page.enable":
            self.sessions[session] = True
            return {}
        if method == "Page.addScriptToEvaluateOnNewDocument":
            self.counter += 1
            ident = str(self.counter)
            self.scripts[ident] = (session, params["source"])
            return {"identifier": ident}
        if method == "Page.removeScriptToEvaluateOnNewDocument":
            self.scripts.pop(params["identifier"], None)
            return {}
        if method == "Page.reload":
            self.load(self.address)
            return {}
        if method == "Page.navigate":
            self.address = params["url"]
            self.load(self.address)
            return {"frameId": "PAGE"}
        if method == "Runtime.evaluate":
            hang = Path(os.environ.get("FAKE_BROWSER_HANG", "/nonexistent"))
            while hang.exists() and hang.read_text().strip() == str(os.getpid()):
                time.sleep(0.1)
            typed = Path(os.environ.get("FAKE_BROWSER_SIGNIN", "/nonexistent"))
            if typed.exists():
                self.storage = typed.read_text()
                typed.unlink()
            value = {"href": self.href, "user": self.storage}
            return {"result": {"type": "object", "value": value}}
        raise NotImplementedError(method)


def serve(tab: Tab) -> None:
    buffer = b""
    while True:
        chunk = os.read(3, 65536)
        if not chunk:
            return
        buffer += chunk
        while b"\0" in buffer:
            raw, _, buffer = buffer.partition(b"\0")
            command = json.loads(raw)
            log(
                "command",
                method=command["method"],
                params=command.get("params"),
                session=command.get("sessionId"),
            )
            reply: dict[str, Any] = {"id": command["id"]}
            try:
                reply["result"] = tab.handle(
                    command["method"],
                    command.get("params") or {},
                    command.get("sessionId"),
                )
            except (LookupError, NotImplementedError) as ex:
                reply["error"] = {"code": -32000, "message": str(ex)}
            os.write(4, json.dumps(reply).encode() + b"\0")


def main() -> int:
    argv = sys.argv[1:]
    pipe = "--remote-debugging-pipe" in argv
    log("start", argv=argv, pipe=pipe)
    if pipe and os.environ.get("FAKE_BROWSER_DEAF"):
        print("Remote debugging pipe file descriptors are not open.", file=sys.stderr)
        return 1
    tab = Tab(argv[-1])
    if not pipe:
        while True:
            time.sleep(1)
    serve(tab)
    return 0


if __name__ == "__main__":
    sys.exit(main())
