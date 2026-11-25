"""A stand-in for the FUXA REST endpoints that cm-fuxa-provision calls.

It follows the server at the cm-fuxa pin (frangoteam/FUXA ``b2972583``):

* ``POST /api/signin`` checks the password and answers ``data.token`` and
  ``data.groups`` (server/api/auth/index.js); a request with no token is the
  guest.
* ``/api/users`` and ``POST /api/project`` are for administrators, a user whose
  groups value is -1 or 255 (server/api/jwt-helper.js ``adminGroups``); any
  other caller gets HTTP 401. ``POST /api/users`` takes ``params.info`` as a JSON
  string (server/runtime/users/index.js ``setUsers`` parses it) and creates the
  account or, when it exists, replaces its password, groups, and info.
* A project import does not touch the users (``users.fuxap.db`` is opened once,
  at start-up).
* ``GET /api/project`` answers any caller, the guest included.
* With ``lifetime`` set, a token is a JWT (unsigned here) with ``iat`` and ``exp``
  claims, as ``jsonwebtoken`` makes them in server/api/auth/index.js
  ``buildAccessToken``; otherwise it is an opaque string.

It is a model of what the script relies on, not FUXA: whether the real server
behaves so is what the first boot has to show.
"""

from __future__ import annotations

import base64
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Final

#: server/api/jwt-helper.js adminGroups.
ADMIN_GROUPS: Final[tuple[int, int]] = (-1, 255)
SEED_PASSWORD: Final[str] = "123456"


class User:
    """One row of users.fuxap.db."""

    def __init__(
        self, password: str, groups: int, fullname: str = "", info: str | None = None
    ) -> None:
        self.password: str = password
        self.groups: int = groups
        self.fullname: str = fullname
        self.info: str | None = info


class FakeFuxa:
    """The server state and a counter for every write."""

    def __init__(self, lifetime: int | None = None) -> None:
        #: Seconds a token lives (a JWT with iat and exp), or None for opaque tokens.
        self.lifetime: int | None = lifetime
        #: The time of every sign-in request, and of those it accepted (time, user).
        self.attempts: list[float] = []
        self.signins: list[tuple[float, str]] = []
        #: The seeded administrator of a fresh users database.
        self.users: dict[str, User] = {
            "admin": User(SEED_PASSWORD, -1, "Administrator Account")
        }
        self.project: dict[str, Any] | None = None
        self.tokens: dict[str, str] = {}
        self.user_posts: int = 0
        self.project_posts: int = 0
        #: Make the server treat this user as an administrator whatever its
        #: groups say (a FUXA that had no group check).
        self.always_admin: set[str] = set()
        #: Answer every POST /api/users with HTTP 500.
        self.refuse_users: bool = False
        self.server: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        assert self.server is not None
        host, port = self.server.server_address[:2]
        return f"http://{host!s}:{port}"

    def token(self, name: str, groups: int) -> str:
        if self.lifetime is None:
            return f"token-{len(self.tokens)}-{name}"

        def part(doc: dict[str, Any]) -> str:
            raw: bytes = json.dumps(doc).encode()
            return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

        now: int = int(time.time())
        claims: dict[str, Any] = {
            "id": name,
            "groups": groups,
            "iat": now,
            "exp": now + self.lifetime,
        }
        return (
            f"{part({'alg': 'HS256', 'typ': 'JWT'})}.{part(claims)}.{len(self.tokens)}"
        )

    def start(self, port: int = 0) -> None:
        fake: FakeFuxa = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002, ARG002
                return

            # --------------------------------------------------- plumbing

            def _json(self) -> Any:
                length: int = int(self.headers.get("Content-Length") or 0)
                return json.loads(self.rfile.read(length) or b"null")

            def _send(self, code: int, body: Any = None) -> None:
                data: bytes = json.dumps(body).encode() if body is not None else b""
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _caller(self) -> str | None:
                """The signed-in user of the request, None for the guest."""
                return fake.tokens.get(self.headers.get("x-access-token") or "")

            def _is_admin(self) -> bool:
                user: str | None = self._caller()
                if user is None or user not in fake.users:
                    return False
                return (
                    user in fake.always_admin or fake.users[user].groups in ADMIN_GROUPS
                )

            def _unauthorized(self) -> None:
                self._send(
                    401, {"error": "unauthorized_error", "message": "Unauthorized!"}
                )

            # --------------------------------------------------- routes

            def do_GET(self) -> None:
                if self.path == "/api/version":
                    self._send(200, "1.0.0")
                elif self.path == "/api/users":
                    if not self._is_admin():
                        self._unauthorized()
                        return
                    rows: list[dict[str, Any]] = [
                        {
                            "username": n,
                            "fullname": u.fullname,
                            "groups": u.groups,
                            "info": u.info,
                        }
                        for n, u in fake.users.items()
                    ]
                    self._send(200, rows)
                elif self.path == "/api/project":
                    if fake.project is None:
                        self._send(404)
                    else:
                        self._send(200, fake.project)
                else:
                    self._send(404)

            def do_POST(self) -> None:
                body: Any = self._json()
                if self.path == "/api/signin":
                    self._signin(body)
                elif self.path == "/api/users":
                    self._users(body)
                elif self.path == "/api/project":
                    if not self._is_admin():
                        self._unauthorized()
                        return
                    fake.project_posts += 1
                    fake.project = body
                    self._send(200)
                else:
                    self._send(404)

            def _signin(self, body: Any) -> None:
                fake.attempts.append(time.time())
                name: Any = (body or {}).get("username")
                user: User | None = (
                    fake.users.get(name) if isinstance(name, str) else None
                )
                if user is None or user.password != (body or {}).get("password"):
                    self._send(
                        401,
                        {
                            "status": "error",
                            "message": "Invalid email/password!!!",
                            "data": None,
                        },
                    )
                    return
                token: str = fake.token(name, user.groups)
                fake.tokens[token] = name
                fake.signins.append((time.time(), name))
                self._send(
                    200,
                    {
                        "status": "success",
                        "message": "user found!!!",
                        "data": {
                            "username": name,
                            "fullname": user.fullname,
                            "groups": user.groups,
                            "info": user.info,
                            "token": token,
                        },
                    },
                )

            def _users(self, body: Any) -> None:
                if not self._is_admin():
                    self._unauthorized()
                    return
                if fake.refuse_users:
                    self._send(500, {"error": "unexpected_error", "message": "refused"})
                    return
                params: Any = (body or {}).get("params") or {}
                name: Any = params.get("username")
                info: Any = params.get("info")
                try:
                    if not isinstance(name, str) or not name:
                        raise ValueError("no username")
                    # setUsers parses info, and a value that is not a JSON string
                    # leaves the permission cache without the user.
                    json.loads(info)
                except (TypeError, ValueError):
                    self._send(
                        400, {"error": "unexpected_error", "message": "bad user"}
                    )
                    return
                fake.user_posts += 1
                existing: User | None = fake.users.get(name)
                password: Any = params.get("password")
                if existing is None:
                    fake.users[name] = User(
                        password or "",
                        params.get("groups", 0),
                        params.get("fullname", ""),
                        info,
                    )
                else:
                    if password:
                        existing.password = password
                    existing.groups = params.get("groups", existing.groups)
                    existing.fullname = params.get("fullname", existing.fullname)
                    existing.info = info
                self._send(200)

        self.server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            kwargs={"poll_interval": 0.02},
            daemon=True,
        )
        self.thread.start()

    def stop(self) -> None:
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
        if self.thread is not None:
            self.thread.join(timeout=5)
