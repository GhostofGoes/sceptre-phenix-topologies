"""cm-kiosk, the HMI kiosk (DESIGN.md 12.2), against a fake of FUXA's sign-in
(``fuxa_fake.py``) and a fake of Chromium's debugging pipe
(``kiosk_fake_browser.py``). No browser is run.

The script runs as a process, as the autostart entry runs it, with its waits cut
to tens of milliseconds. What these tests prove is its logic: when it signs in, what
it hands the browser, that the password goes nowhere else, when it retries, and how it
recovers. That the real Chromium takes what the script hands it is for the run by hand
that the app's README describes (it ran against the image's Chromium and a real FUXA).
"""

from __future__ import annotations

import base64
import importlib.machinery
import importlib.util
import itertools
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from cherrymountain_apps.cherrymountain import render, soh
from cherrymountain_apps.cherrymountain.tests.fuxa_fake import FakeFuxa, User

SCRIPT: Path = render.template("scripts", "cm-kiosk")
FAKE_BROWSER: Path = Path(__file__).with_name("kiosk_fake_browser.py")
PASSWORD: str = "operator-range-secret-BBB"


def load_kiosk() -> ModuleType:
    loader = importlib.machinery.SourceFileLoader("cm_kiosk", str(SCRIPT))
    spec = importlib.util.spec_from_loader("cm_kiosk", loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    # dataclasses look their module up by name.
    sys.modules["cm_kiosk"] = module
    loader.exec_module(module)
    return module


kiosk: ModuleType = load_kiosk()


# ---------------------------------------------------------------- the process


class Kiosk:
    """``cm-kiosk`` as a process, with the fake browser as its browser."""

    def __init__(
        self,
        tmp: Path,
        url: str,
        credentials: bool = True,
        **environment: str,
    ) -> None:
        self.log: Path = tmp / "browser.jsonl"
        self.hang: Path = tmp / "hang"
        self.person: Path = tmp / "person"
        self.state: Path = tmp / "state"
        browser: Path = tmp / "chromium"
        browser.write_text(
            f'#!/bin/sh\nexec "{sys.executable}" "{FAKE_BROWSER}" "$@"\n'
        )
        browser.chmod(0o755)
        settings: Path = tmp / "cm-kiosk"
        settings.write_text(f"KIOSK_URL={url}\nKIOSK_BROWSER={browser}\n")
        sign_in: Path = tmp / "cm-kiosk-credentials"
        if credentials:
            sign_in.write_text(f"KIOSK_USER=operator\nKIOSK_PASSWORD={PASSWORD}\n")
            sign_in.chmod(0o600)
        env: dict[str, str] = {
            **os.environ,
            "FAKE_BROWSER_LOG": str(self.log),
            "FAKE_BROWSER_HANG": str(self.hang),
            "FAKE_BROWSER_SIGNIN": str(self.person),
            "KIOSK_POLL": "0.05",
            "KIOSK_RETRY_MIN": "0.1",
            "KIOSK_RETRY_MAX": "0.4",
            "KIOSK_WAIT": "1",
            **environment,
        }
        self.stderr_path: Path = tmp / "kiosk.err"
        with self.stderr_path.open("w") as err:
            self.process: subprocess.Popen[bytes] = subprocess.Popen(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--env",
                    str(settings),
                    "--credentials",
                    str(sign_in),
                    "--state-dir",
                    str(self.state),
                ],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=err,
            )

    def events(self, kind: str) -> list[dict[str, Any]]:
        if not self.log.exists():
            return []
        lines = [json.loads(x) for x in self.log.read_text().splitlines()]
        return [x for x in lines if x["event"] == kind]

    def starts(self) -> list[dict[str, Any]]:
        return self.events("start")

    def commands(self, method: str | None = None) -> list[dict[str, Any]]:
        return [c for c in self.events("command") if method in (None, c["method"])]

    def status(self) -> dict[str, str]:
        path: Path = self.state / "status"
        if not path.exists():
            return {}
        return dict(x.split("=", 1) for x in path.read_text().splitlines())

    def stderr(self) -> str:
        return self.stderr_path.read_text()

    def wait_for(
        self, check: Callable[[], bool], what: str, timeout: float = 20.0
    ) -> None:
        end: float = time.monotonic() + timeout
        while time.monotonic() < end:
            if check():
                return
            time.sleep(0.02)
        pytest.fail(
            f"timed out waiting for {what}; status {self.status()}; "
            f"kiosk log:\n{self.stderr()[-1500:]}"
        )

    def operator(self) -> bool:
        return self.status().get("state") == "operator"

    def stop(self) -> None:
        if self.process.poll() is None:
            self.process.send_signal(signal.SIGTERM)
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        # A browser the kiosk could not stop would otherwise outlive the test.
        for start in self.starts():
            try:
                os.kill(start["pid"], signal.SIGKILL)
            except ProcessLookupError:
                pass


PROJECT: dict[str, Any] = {"hmi": {"views": [{"name": "Overview"}, {"name": "Cellar"}]}}


def make_fuxa(lifetime: int = 3600, imported: bool = True) -> FakeFuxa:
    """A FUXA with the operator account, and the project unless it is yet to be imported."""
    fake = FakeFuxa(lifetime=lifetime)
    fake.users["operator"] = User(PASSWORD, 2, "Operator", "{}")
    if imported:
        fake.project = PROJECT
    return fake


@pytest.fixture
def fuxa() -> Iterator[FakeFuxa]:
    fake = make_fuxa()
    fake.start()
    yield fake
    fake.stop()


@pytest.fixture
def start_kiosk(tmp_path: Path) -> Iterator[Callable[..., Kiosk]]:
    runs: list[Kiosk] = []

    def start(url: str, **kwargs: Any) -> Kiosk:
        run = Kiosk(tmp_path, url, **kwargs)
        runs.append(run)
        return run

    yield start
    for run in runs:
        run.stop()


def view(fake: FakeFuxa) -> str:
    return f"{fake.url}/home?viewName=Overview"


# ---------------------------------------------------------------- what it does


def test_the_view_opens_signed_in_and_the_password_stays_out_of_the_browser(
    fuxa: FakeFuxa, start_kiosk: Callable[..., Kiosk]
) -> None:
    run = start_kiosk(view(fuxa))
    run.wait_for(run.operator, "the operator session")
    (start,) = run.starts()
    argv: list[str] = start["argv"]
    # The flags the kiosk has always had, the view last, and a pipe, not a port.
    assert list(kiosk.BROWSER_FLAGS) == argv[: len(kiosk.BROWSER_FLAGS)]
    assert argv[-1] == view(fuxa)
    assert "--remote-debugging-pipe" in argv
    assert not [a for a in argv if "remote-debugging-port" in a or "remote-allow" in a]
    assert f"--user-data-dir={run.state}/profile" in argv
    # Page.enable first (Chromium runs a new-document script only after it), then the
    # script, then the reload that makes the page read it.
    methods: list[str] = [c["method"] for c in run.commands()]
    enable: int = methods.index("Page.enable")
    add: int = methods.index("Page.addScriptToEvaluateOnNewDocument")
    assert enable < add < methods.index("Page.reload")
    (script,) = run.commands("Page.addScriptToEvaluateOnNewDocument")
    source: str = script["params"]["source"]
    (token,) = fuxa.tokens
    assert token in source
    assert fuxa.url in source
    assert [name for _, name in fuxa.signins] == ["operator"]
    # Nothing but the token reaches the browser, and nothing is logged.
    for text in (run.log.read_text(), run.stderr(), json.dumps(start)):
        assert PASSWORD not in text
    assert token not in run.stderr()
    assert PASSWORD not in (run.state / "status").read_text()


def test_a_session_is_renewed_before_its_token_expires(
    start_kiosk: Callable[..., Kiosk],
) -> None:
    fake = make_fuxa(lifetime=3)
    fake.start()
    try:
        run = start_kiosk(view(fake))
        run.wait_for(lambda: len(fake.signins) >= 3, "two renewals")
        first, second, *_ = fake.tokens
        _, first_exp = kiosk.token_times(first)
        # The second sign-in came while the first token was still good, so the
        # page never held an expired one.
        assert fake.signins[1][0] < first_exp
        scripts = run.commands("Page.addScriptToEvaluateOnNewDocument")
        assert second in scripts[1]["params"]["source"]
        # The new script replaces the old one; the page is reloaded to read it.
        assert run.commands("Page.removeScriptToEvaluateOnNewDocument")
        assert len(run.commands("Page.reload")) >= 2
    finally:
        fake.stop()


def test_a_fuxa_that_is_down_at_start_leaves_the_view_open_as_a_guest(
    fuxa: FakeFuxa, start_kiosk: Callable[..., Kiosk]
) -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
    url: str = f"http://127.0.0.1:{port}/home?viewName=Overview"
    run = start_kiosk(url, KIOSK_WAIT="0.3")
    # After the wait, the browser starts on the view although nothing answers, and
    # the kiosk says why it is a guest.
    run.wait_for(lambda: len(run.starts()) == 1, "the browser")
    run.wait_for(
        lambda: "ConnectionRefusedError" in run.status().get("detail", ""),
        "a failed sign-in",
    )
    assert run.status()["state"] == "guest"
    assert not run.commands("Page.addScriptToEvaluateOnNewDocument")
    # FUXA comes up: the kiosk signs in and opens the view again.
    fuxa.users["operator"] = User(PASSWORD, 2, "Operator", "{}")
    fuxa.stop()
    fuxa.start(port)
    run.wait_for(run.operator, "the operator session after FUXA came up")
    navigate = run.commands("Page.navigate")
    assert navigate[-1]["params"]["url"] == url
    methods = [c["method"] for c in run.commands()]
    last_navigate = max(i for i, m in enumerate(methods) if m == "Page.navigate")
    assert methods.index("Page.addScriptToEvaluateOnNewDocument") < last_navigate


def test_the_kiosk_waits_until_fuxa_has_the_view_it_opens(
    start_kiosk: Callable[..., Kiosk],
) -> None:
    """cm-fuxa-provision makes the account first and imports the project after it,
    and a page that loaded the project before keeps the old one."""
    fake = make_fuxa(imported=False)
    fake.start()
    try:
        run = start_kiosk(view(fake), KIOSK_WAIT="30")
        time.sleep(0.5)
        assert not run.starts()
        assert run.status()["state"] == "starting"
        # A project that has other views does not do.
        fake.project = {"hmi": {"views": [{"name": "Cellar"}]}}
        time.sleep(0.5)
        assert not run.starts()
        fake.project = PROJECT
        run.wait_for(lambda: len(run.starts()) == 1, "the browser")
        run.wait_for(run.operator, "the operator session")
    finally:
        fake.stop()


@pytest.mark.parametrize(
    ("project", "ready"),
    [
        (None, False),
        ({"hmi": {"views": []}}, False),
        ({"hmi": {"views": [{"name": "Cellar"}]}}, False),
        ({"hmi": {"views": [{"name": "Cellar points"}]}}, True),
        ({"hmi": {"views": {"v1": {"name": "Cellar points"}}}}, True),
        # A project this script does not know does not hold the screen.
        ({"hmi": "other"}, True),
        ({"devices": {}}, True),
    ],
)
def test_the_view_is_ready_when_fuxas_project_has_it(
    project: dict[str, Any] | None, ready: bool
) -> None:
    fake = FakeFuxa()
    fake.project = project
    fake.start()
    try:
        url = f"{fake.url}/home?viewName=Cellar%20points"
        assert kiosk.view_ready(kiosk.Settings(url=url)) is ready
    finally:
        fake.stop()
    # No answer is no.
    assert kiosk.view_ready(kiosk.Settings(url=url)) is False


def test_an_account_that_does_not_exist_yet_is_tried_again_with_growing_waits(
    fuxa: FakeFuxa, start_kiosk: Callable[..., Kiosk]
) -> None:
    del fuxa.users["operator"]
    run = start_kiosk(view(fuxa), KIOSK_RETRY_MIN="0.2", KIOSK_RETRY_MAX="0.8")
    run.wait_for(lambda: len(fuxa.attempts) >= 6, "six attempts")
    # The view is open as a guest and the status says what FUXA answered.
    assert len(run.starts()) == 1
    assert run.status()["state"] == "guest"
    assert "HTTP 401" in run.status()["detail"]
    gaps: list[float] = [b - a for a, b in itertools.pairwise(fuxa.attempts)]
    # 0.2, 0.4, 0.8, 0.8, ...: never faster than the first wait, and growing.
    assert min(gaps) >= 0.18
    assert gaps[2] > gaps[0] * 1.5
    assert all(later >= earlier * 0.8 for earlier, later in itertools.pairwise(gaps))
    # The provisioning finishes: the next attempt signs in.
    fuxa.users["operator"] = User(PASSWORD, 2, "Operator", "{}")
    run.wait_for(run.operator, "the operator session once the account exists")
    assert len(run.starts()) == 1


def test_a_session_of_another_account_is_replaced_by_the_operators(
    fuxa: FakeFuxa, start_kiosk: Callable[..., Kiosk]
) -> None:
    """The kiosk is an operator station: a person who signs in as someone else at
    it is signed back in as the operator (the editor is for the engineering
    workstation)."""
    run = start_kiosk(view(fuxa))
    run.wait_for(run.operator, "the operator session")
    now = int(time.time())
    admin = {"username": "admin", "token": fixed_token(now, now + 3600)}
    run.person.write_text(json.dumps(admin))
    run.wait_for(lambda: len(fuxa.signins) == 2, "a sign-in as the operator again")
    run.wait_for(lambda: len(run.commands("Page.reload")) == 2, "a reload")
    assert [name for _, name in fuxa.signins] == ["operator", "operator"]
    run.wait_for(run.operator, "the operator session once more")


def test_a_browser_that_exits_is_started_again_and_signed_in_again(
    fuxa: FakeFuxa, start_kiosk: Callable[..., Kiosk]
) -> None:
    run = start_kiosk(view(fuxa))
    run.wait_for(run.operator, "the operator session")
    os.kill(run.starts()[0]["pid"], signal.SIGKILL)
    run.wait_for(lambda: len(run.starts()) == 2, "the browser again")
    run.wait_for(lambda: len(fuxa.signins) == 2 and run.operator(), "a new session")
    # A new browser is a new tab with no session: it got its own script.
    assert len(run.commands("Page.addScriptToEvaluateOnNewDocument")) == 2
    assert run.starts()[1]["pipe"] is True
    assert "starting it again" in run.stderr()


def test_a_browser_that_stops_answering_is_replaced(
    fuxa: FakeFuxa, start_kiosk: Callable[..., Kiosk]
) -> None:
    run = start_kiosk(view(fuxa))
    run.wait_for(run.operator, "the operator session")
    run.hang.write_text(str(run.starts()[0]["pid"]))
    run.wait_for(lambda: len(run.starts()) == 2, "a replacement browser")
    run.wait_for(run.operator, "a session in the replacement")
    assert "still does not answer" in run.stderr()


def test_a_browser_whose_pipe_does_not_work_is_replaced_by_one_without_it(
    fuxa: FakeFuxa, start_kiosk: Callable[..., Kiosk]
) -> None:
    """The view must stay open: the pipe is for the sign-in only."""
    run = start_kiosk(view(fuxa), FAKE_BROWSER_DEAF="1")
    run.wait_for(lambda: len(run.starts()) == 2, "the second browser")
    first, second = run.starts()
    assert first["pipe"] is True
    assert second["pipe"] is False
    assert second["argv"][-1] == view(fuxa)
    run.wait_for(
        lambda: "without a debugging pipe" in run.status().get("detail", ""),
        "the status of the guest browser",
    )
    # Its crash gives the pipe another try.
    os.kill(second["pid"], signal.SIGKILL)
    run.wait_for(lambda: len(run.starts()) == 3, "the third browser")
    assert run.starts()[2]["pipe"] is True


def test_without_a_sign_in_file_the_kiosk_is_a_guest_and_makes_no_attempt(
    fuxa: FakeFuxa, start_kiosk: Callable[..., Kiosk]
) -> None:
    run = start_kiosk(view(fuxa), credentials=False)
    run.wait_for(lambda: len(run.commands("Runtime.evaluate")) >= 3, "some polls")
    assert run.status()["state"] == "guest"
    assert not fuxa.attempts
    assert not run.commands("Page.addScriptToEvaluateOnNewDocument")
    assert "stays a guest" in run.stderr()


def test_the_kiosk_stops_its_browser_when_it_is_stopped(
    fuxa: FakeFuxa, start_kiosk: Callable[..., Kiosk]
) -> None:
    run = start_kiosk(view(fuxa))
    run.wait_for(run.operator, "the operator session")
    pid: int = run.starts()[0]["pid"]
    run.process.send_signal(signal.SIGTERM)
    assert run.process.wait(timeout=15) == 0
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    assert run.status()["state"] == "down"


def test_a_url_that_is_not_http_is_refused(tmp_path: Path) -> None:
    run = Kiosk(tmp_path, "file:///etc/passwd")
    assert run.process.wait(timeout=10) == 1
    assert "KIOSK_URL" in run.stderr()
    assert not run.starts()


# ---------------------------------------------------------------- the pieces


def profile(token: str, user: str = "operator") -> str:
    return json.dumps({"username": user, "groups": 2, "token": token})


def fixed_token(iat: int, exp: int) -> str:
    def part(doc: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(doc).encode()).rstrip(b"=").decode()

    return f"{part({'alg': 'HS256'})}.{part({'iat': iat, 'exp': exp})}.x"


@pytest.mark.parametrize(
    ("now", "verdict"),
    [
        (1000, "ok"),
        (1000 + 39, "ok"),
        (1000 + 40, "renew"),
        (1000 + 59, "renew"),
        (1000 + 60, "none"),
        (5000, "none"),
    ],
)
def test_a_session_is_ok_until_two_thirds_of_its_token_have_passed(
    now: int, verdict: str
) -> None:
    stored = profile(fixed_token(1000, 1060))
    assert kiosk.judge(stored, "operator", now) == verdict


@pytest.mark.parametrize(
    "stored",
    [
        None,
        "",
        "not json",
        "[]",
        json.dumps({"username": "guest", "token": "x"}),
        profile(fixed_token(1000, 1060), user="admin"),
        json.dumps({"username": "operator"}),
        json.dumps({"username": "operator", "token": "a.b.c"}),
        json.dumps({"username": "operator", "token": 5}),
    ],
)
def test_anything_but_the_operators_live_session_is_none(stored: str | None) -> None:
    assert kiosk.judge(stored, "operator", 1000) == "none"


def test_token_times_reads_a_jwt_and_nothing_else() -> None:
    assert kiosk.token_times(fixed_token(7, 9)) == (7.0, 9.0)
    for bad in (
        None,
        3,
        "",
        "a",
        "a.b",
        "a.!!.c",
        fixed_token(7, 9).replace(".", ".x", 1),
    ):
        assert kiosk.token_times(bad) is None


def test_the_waits_double_up_to_a_cap_and_start_again() -> None:
    wait = kiosk.Backoff(10.0, 120.0)
    assert [wait.next() for _ in range(6)] == [10, 20, 40, 80, 120, 120]
    wait.reset()
    assert wait.next() == 10


def test_settings_come_from_the_files_then_the_environment(tmp_path: Path) -> None:
    env_file = tmp_path / "env"
    env_file.write_text(
        '# a comment\nKIOSK_URL="http://h:1881/home?viewName=A%20B"\nKIOSK_POLL=7\n'
    )
    sign_in = tmp_path / "credentials"
    sign_in.write_text(
        "KIOSK_USER=operator\nKIOSK_PASSWORD=pw-1\nKIOSK_RETRY_MIN=nonsense\n"
    )
    cfg = kiosk.Settings.load(env_file, sign_in, {"KIOSK_POLL": "0.5", "HOME": "/x"})
    assert cfg.url == "http://h:1881/home?viewName=A%20B"
    assert cfg.origin == "http://h:1881"
    assert (cfg.user, cfg.password, cfg.poll) == ("operator", "pw-1", 0.5)
    # A value that is not a number, or not positive, is the default.
    assert cfg.retry_min == 10.0
    assert "pw-1" not in repr(cfg)
    with pytest.raises(ValueError, match="KIOSK_URL"):
        kiosk.Settings.load(tmp_path / "missing", sign_in, {})
    # A sign-in needs both halves.
    assert cfg.signs_in
    assert not kiosk.Settings(url=cfg.url, password="pw-1").signs_in
    assert not kiosk.Settings(url=cfg.url, user="operator").signs_in


@pytest.mark.parametrize(
    ("href", "same"),
    [
        ("http://h:1881/home/?viewName=A", True),
        ("http://h:1881", True),
        ("http://h:1882/home", False),
        ("https://h:1881/home", False),
        ("chrome-error://chromewebdata/", False),
        ("about:blank", False),
        (None, False),
    ],
)
def test_a_page_is_the_view_when_it_is_on_the_views_origin(
    href: str | None, same: bool
) -> None:
    assert kiosk.same_origin(href, "http://h:1881/home?viewName=A") is same


# ---------------------------------------------------------------- the snippet

NODE: str | None = shutil.which("node")

RUN_SNIPPET: str = """
const vm = require('node:vm');
const [script, pageOrigin, topLevel] = JSON.parse(process.argv[1]);
const store = {};
const window = {}; window.top = topLevel ? window : {};
vm.runInNewContext(script, {
  window,
  location: { origin: pageOrigin },
  sessionStorage: { setItem: (k, v) => { store[k] = v; } },
});
console.log(JSON.stringify(store));
"""


def run_snippet(
    script: str, page_origin: str, top_level: bool = True
) -> dict[str, str]:
    assert NODE is not None
    out = subprocess.run(
        [NODE, "-e", RUN_SNIPPET, json.dumps([script, page_origin, top_level])],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    stored: dict[str, str] = json.loads(out.stdout)
    return stored


@pytest.mark.skipif(NODE is None, reason="needs node to run the snippet")
def test_the_snippet_sets_the_profile_for_its_own_origin_and_top_frame_only() -> None:
    nasty = {
        "username": "operator",
        "fullname": 'Q"uote </script> ' + chr(0x2028) + r" back\slash " + chr(0xE9),
        "groups": 2,
        "info": "{}",
        "token": "a.b.c",
    }
    script = kiosk.session_script("http://h:1881", nasty)
    stored = run_snippet(script, "http://h:1881")
    assert json.loads(stored["currentUser"]) == nasty
    # Another origin, or a frame of the page: nothing is written.
    assert run_snippet(script, "http://other:1881") == {}
    assert run_snippet(script, "http://h:1882") == {}
    assert run_snippet(script, "http://h:1881", top_level=False) == {}


# ---------------------------------------------------------------- the SoH check


def run_check(status: Path) -> str:
    script: str = soh.KIOSK_SESSION.replace("/run/cm-kiosk/status", str(status))
    return subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, check=True, timeout=30
    ).stdout.strip()


def test_the_soh_check_reports_the_kiosks_own_status(tmp_path: Path) -> None:
    status = tmp_path / "status"
    assert run_check(status) == "kiosk-no-status"
    status.write_text("state=operator\ndetail=\nupdated=1\n")
    assert run_check(status) == "kiosk-operator-ok"
    status.write_text(
        "state=guest\ndetail=sign-in as operator failed: HTTP 401\nupdated=1\n"
    )
    assert run_check(status) == "kiosk-guest sign-in as operator failed: HTTP 401"
    # A status that has not been written for two minutes says nothing.
    old: float = time.time() - 300
    os.utime(status, (old, old))
    assert run_check(status).startswith("kiosk-status-stale")
