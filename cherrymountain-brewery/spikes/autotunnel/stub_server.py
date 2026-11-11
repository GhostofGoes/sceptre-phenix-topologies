#!/usr/bin/env python3
"""Stand-in service for the autotunnel spike (runs ON a guest, stdlib only).

Serves a one-line banner on GET and echoes the body of a POST, on every port
given, so the host can check read and write paths through a tunnel (Grafana
3000, event API 8080, OpenSearch 9200, FUXA 1881 stand-ins).

Usage: stub_server.py 3000 8080 9200
"""

import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Stub(BaseHTTPRequestHandler):
    def _reply(self, text: str) -> None:
        body: bytes = (text + "\n").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        self._reply(f"stub {socket.gethostname()}:{self.server.server_port}")

    def do_POST(self) -> None:  # noqa: N802
        length: int = int(self.headers.get("Content-Length", "0"))
        payload: str = self.rfile.read(length).decode(errors="replace")
        self._reply(f"posted {socket.gethostname()}:{self.server.server_port} {payload}")

    def log_message(self, fmt: str, *args: object) -> None:
        pass


def main(ports: list[int]) -> None:
    for port in ports:
        server: ThreadingHTTPServer = ThreadingHTTPServer(("0.0.0.0", port), Stub)  # noqa: S104
        threading.Thread(target=server.serve_forever, daemon=True).start()
    threading.Event().wait()


if __name__ == "__main__":
    main([int(p) for p in sys.argv[1:]])
