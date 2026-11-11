#!/usr/bin/env python3
"""Shared driver for the Cherry Mountain Phase 0 VM spikes.

Talks to a real minimega over its command socket and to a real phenix (CLI and
`phenix ui` REST), the same way the phenix end-to-end tests do (see
src/go/e2e in the sceptre-phenix `ci-e2e-minimega` branch). The spike
`ci.py` files import this module; it needs pydantic, and root on a runner with
the setup from `.github/actions/cm-lab`.
"""

from __future__ import annotations

import base64
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

MM_BASE: Path = Path(os.environ.get("E2E_MM_BASE", "/tmp/minimega"))
IMAGES_DIR: Path = Path(os.environ.get("E2E_IMAGES_DIR", "/phenix/images"))
STORE: str = os.environ.get("E2E_STORE", "bolt:///etc/phenix/store.bdb")
PHENIX_BIN: str = os.environ.get("E2E_PHENIX", "/usr/local/bin/phenix")
UI_ADDR: str = os.environ.get("E2E_UI_ADDR", "127.0.0.1:3080")
EVIDENCE_ROOT: Path = Path(os.environ.get("CM_EVIDENCE_DIR", "evidence"))

GUEST_BOOT_TIMEOUT_S: float = 300.0
CC_WAIT_S: float = 120.0


def log(message: str) -> None:
    """Print a timestamped line that shows up in the job log immediately."""
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


class MmResponse(BaseModel):
    """One minimega response row set."""

    model_config = ConfigDict(populate_by_name=True)

    host: str = Field("", alias="Host")
    response: str = Field("", alias="Response")
    header: list[str] | None = Field(None, alias="Header")
    tabular: list[list[str]] | None = Field(None, alias="Tabular")
    error: str = Field("", alias="Error")
    data: Any = Field(None, alias="Data")


class MmReply(BaseModel):
    """One message on the minimega socket."""

    model_config = ConfigDict(populate_by_name=True)

    resp: list[MmResponse] = Field(default_factory=list, alias="Resp")
    more: bool = Field(False, alias="More")
    status: bool = Field(False, alias="Status")


class Check(BaseModel):
    """One pass/fail criterion of a spike."""

    name: str
    passed: bool
    detail: str = ""


class Evidence(BaseModel):
    """What a spike run proved; written as result.json next to the logs."""

    spike: str
    checks: list[Check] = Field(default_factory=list)
    versions: dict[str, str] = Field(default_factory=dict)
    facts: dict[str, str] = Field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return bool(self.checks) and all(c.passed for c in self.checks)


class CcResult(BaseModel):
    """The answer of a guest to one cc command."""

    stdout: str
    exit_code: int | None = None


def _iter_json(sock: socket.socket) -> Any:
    """Yield the JSON values a minimega socket sends, one per message."""
    decoder: json.JSONDecoder = json.JSONDecoder()
    buf: str = ""
    while True:
        chunk: bytes = sock.recv(65536)
        if not chunk:
            return
        buf += chunk.decode("utf-8", errors="replace")
        while buf.strip():
            try:
                value, end = decoder.raw_decode(buf.lstrip())
            except json.JSONDecodeError:
                break
            buf = buf.lstrip()[end:]
            yield value


class Minimega:
    """Client for the minimega command socket."""

    def __init__(self, base: Path = MM_BASE) -> None:
        self.sock_path: Path = base / "minimega"

    def run(self, command: str, ns: str | None = None, timeout: float = 300.0) -> list[MmResponse]:
        """Run a command, inside namespace ns when given; raise on a minimega error."""
        if ns:
            command = f'namespace "{ns}" {command}'
        responses: list[MmResponse] = []
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(str(self.sock_path))
            sock.sendall(json.dumps({"Command": command}).encode() + b"\n")
            for raw in _iter_json(sock):
                reply: MmReply = MmReply.model_validate(raw)
                if reply.status:
                    continue
                responses.extend(reply.resp)
                if not reply.more:
                    break
        errors: list[str] = [r.error for r in responses if r.error]
        if errors:
            raise RuntimeError(f"minimega {command!r}: {'; '.join(errors)}")
        return responses

    def text(self, command: str, ns: str | None = None) -> str:
        return "\n".join(r.response for r in self.run(command, ns) if r.response)

    def table(self, command: str, ns: str | None = None) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        for resp in self.run(command, ns):
            for tab in resp.tabular or []:
                row: dict[str, str] = {h: tab[i] for i, h in enumerate(resp.header or []) if i < len(tab)}
                row.setdefault("host", resp.host)
                rows.append(row)
        return rows


def guest_script(script: str) -> list[str]:
    """argv that runs script with bash in a guest.

    minimega expands $name and ${...} in cc arguments and fetches URL-looking
    ones, so the script travels base64 encoded (as in the phenix e2e tests).
    """
    enc: str = base64.b64encode(script.encode()).decode()
    return ["bash", "-c", f'eval "$(echo {enc} | base64 -d)"']


def quote_args(argv: Sequence[str]) -> str:
    return " ".join(json.dumps(a) if any(ch.isspace() for ch in a) else a for a in argv)


def wait_for(
    what: str, timeout_s: float, interval_s: float, probe: Callable[[], tuple[bool, str]]
) -> str:
    """Poll probe until it returns (True, detail); raise TimeoutError with the last detail."""
    deadline: float = time.monotonic() + timeout_s
    detail: str = ""
    while True:
        try:
            ok, detail = probe()
        except Exception as exc:  # noqa: BLE001 - a probe may fail while a guest boots
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        if ok:
            return detail
        if time.monotonic() >= deadline:
            raise TimeoutError(f"timed out after {timeout_s:.0f}s waiting for {what}: {detail}")
        time.sleep(interval_s)


def sh(argv: Sequence[str], timeout_s: float = 600.0, check: bool = False) -> subprocess.CompletedProcess[str]:
    """Run a host command and capture its combined output."""
    proc: subprocess.CompletedProcess[str] = subprocess.run(
        list(argv), capture_output=True, text=True, timeout=timeout_s, check=False
    )
    proc.stdout = (proc.stdout or "") + (proc.stderr or "")
    if check and proc.returncode != 0:
        raise RuntimeError(f"{' '.join(argv)} exited {proc.returncode}:\n{proc.stdout}")
    return proc


class Lab:
    """One spike run: a phenix experiment, minimega access and evidence files."""

    def __init__(self, spike: str, experiment: str) -> None:
        self.spike: str = spike
        self.exp: str = experiment
        self.mm: Minimega = Minimega()
        self.dir: Path = EVIDENCE_ROOT / spike
        self.dir.mkdir(parents=True, exist_ok=True)
        self.evidence: Evidence = Evidence(spike=spike)
        self.ui: subprocess.Popen[bytes] | None = None
        self._ui_log: Path = self.dir / "phenix-ui.log"

    # -- recording ----------------------------------------------------------

    def check(self, name: str, passed: bool, detail: str = "") -> bool:
        """Record a pass/fail criterion and log it."""
        self.evidence.checks.append(Check(name=name, passed=passed, detail=detail[:2000]))
        log(f"{'PASS' if passed else 'FAIL'}: {name}" + (f" -- {detail[:300]}" if detail else ""))
        return passed

    def fact(self, key: str, value: str) -> None:
        """Record an observation that is not a pass/fail criterion."""
        self.evidence.facts[key] = value[:4000]
        log(f"fact {key}: {value[:300]}")

    def save(self, name: str, content: str) -> Path:
        path: Path = self.dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return path

    # -- phenix -------------------------------------------------------------

    def phenix(self, *args: str, check: bool = True, timeout_s: float = 900.0) -> str:
        argv: list[str] = [PHENIX_BIN, "--store.endpoint", STORE, "--base-dir.minimega", str(MM_BASE), *args]
        log("phenix " + " ".join(args))
        proc: subprocess.CompletedProcess[str] = sh(argv, timeout_s=timeout_s)
        if check and proc.returncode != 0:
            self.save(f"phenix-failed-{args[0]}-{args[1] if len(args) > 1 else ''}.log", proc.stdout)
            raise RuntimeError(f"phenix {' '.join(args)} exited {proc.returncode}:\n{proc.stdout}")
        return proc.stdout

    def create_experiment(
        self, topology: Path, scenario: Path | None = None, default_bridge: str | None = None
    ) -> None:
        """Create the topology (and scenario) configs and the experiment."""
        self.phenix("config", "create", str(topology))
        args: list[str] = ["experiment", "create", self.exp, "--topology", self._config_name(topology)]
        if scenario is not None:
            self.phenix("config", "create", str(scenario))
            args += ["--scenario", self._config_name(scenario)]
        if default_bridge:
            args += ["--default-bridge", default_bridge]
        self.phenix(*args)

    @staticmethod
    def _config_name(path: Path) -> str:
        for line in path.read_text().splitlines():
            if line.startswith("  name:"):
                return line.split(":", 1)[1].strip()
        raise ValueError(f"{path} has no metadata.name")

    def start_cli(self) -> str:
        out: str = self.phenix("experiment", "start", self.exp, timeout_s=1500)
        self.save("experiment-start.log", out)
        return out

    def start_ui(self) -> None:
        """Start `phenix ui` (needed for autotunnels and the REST API)."""
        argv: list[str] = [
            PHENIX_BIN, "--store.endpoint", STORE, "--base-dir.minimega", str(MM_BASE),
            "ui", "--listen-endpoint", UI_ADDR,
        ]  # fmt: skip
        self.ui = subprocess.Popen(argv, stdout=self._ui_log.open("wb"), stderr=subprocess.STDOUT)  # noqa: S603

        def probe() -> tuple[bool, str]:
            with urllib.request.urlopen(f"http://{UI_ADDR}/version", timeout=2) as resp:  # noqa: S310
                return resp.status == 200, f"status {resp.status}"

        wait_for("phenix ui", 90, 2, probe)
        log("phenix ui is up")

    def stop_ui(self) -> None:
        if self.ui is not None:
            self.ui.terminate()
            try:
                self.ui.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.ui.kill()
            self.ui = None

    def api(self, method: str, path: str, body: Any = None, timeout_s: float = 900.0) -> tuple[int, str]:
        data: bytes | None = json.dumps(body).encode() if body is not None else None
        req: urllib.request.Request = urllib.request.Request(  # noqa: S310
            f"http://{UI_ADDR}/api/v1{path}", data=data, method=method,
            headers={"Content-Type": "application/json"} if data else {},
        )  # fmt: skip
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:  # noqa: S310
                return resp.status, resp.read().decode(errors="replace")
        except urllib.error.HTTPError as err:
            return err.code, err.read().decode(errors="replace")

    def start_rest(self) -> None:
        code, body = self.api("POST", f"/experiments/{self.exp}/start")
        self.save("experiment-start-rest.log", f"{code}\n{body}")
        if code // 100 != 2:
            raise RuntimeError(f"REST start of {self.exp}: {code} {body}")

    def stop_experiment(self) -> None:
        try:
            if self.ui is not None:
                self.api("POST", f"/experiments/{self.exp}/stop")
            else:
                self.phenix("experiment", "stop", self.exp, check=False, timeout_s=600)
        except Exception as exc:  # noqa: BLE001
            log(f"stop failed: {exc}")

    # -- minimega and guests -----------------------------------------------

    def vm_info(self) -> dict[str, dict[str, str]]:
        return {row["name"]: row for row in self.mm.table("vm info", self.exp)}

    def taps(self, vm: str) -> list[str]:
        """Host tap names of vm's interfaces, in interface order (minimega's `vm info` tap column)."""
        return re.findall(r"[A-Za-z0-9_\-]+", self.vm_info()[vm].get("tap", ""))

    def wait_running(self, *vms: str, timeout_s: float = 120) -> None:
        def probe() -> tuple[bool, str]:
            info: dict[str, dict[str, str]] = self.vm_info()
            states: dict[str, str] = {v: info.get(v, {}).get("state", "missing") for v in vms}
            return all(s == "RUNNING" for s in states.values()), str(states)

        wait_for(f"VMs {vms} running", timeout_s, 3, probe)

    def cc_clients(self) -> dict[str, str]:
        """hostname -> uuid of every registered miniccc client."""
        return {r["hostname"]: r.get("uuid", "") for r in self.mm.table("cc clients", self.exp)}

    def wait_cc(self, *hostnames: str, timeout_s: float = GUEST_BOOT_TIMEOUT_S) -> None:
        def probe() -> tuple[bool, str]:
            have: dict[str, str] = self.cc_clients()
            return all(h in have for h in hostnames), f"have {sorted(have)}"

        wait_for(f"cc clients {hostnames}", timeout_s, 3, probe)

    def wait_cc_vm(self, vm: str, timeout_s: float = GUEST_BOOT_TIMEOUT_S) -> None:
        """Wait for a client by VM UUID, for guests whose hostname phenix does not set."""

        def probe() -> tuple[bool, str]:
            uuid: str = self.vm_info()[vm]["uuid"].lower()
            have: list[str] = [u.lower() for u in self.cc_clients().values()]
            return uuid in have, f"want {uuid}, have {have}"

        wait_for(f"cc client of {vm}", timeout_s, 3, probe)

    def cc(self, vm: str, argv: Sequence[str], wait_s: float = CC_WAIT_S, nonce: str = "") -> CcResult:
        """Run argv in a guest through minimega cc and wait for its answer."""
        uuid: str = self.vm_info()[vm]["uuid"].lower()
        self.mm.run(f"cc filter uuid={uuid}", self.exp)
        try:
            resp: list[MmResponse] = self.mm.run(f"cc exec {quote_args(argv)}", self.exp)
        finally:
            self.mm.run("clear cc filter", self.exp)
        cmd_id: str = str(resp[0].data if resp[0].data is not None else resp[0].response).strip().strip('"')
        int(cmd_id)
        base: Path = IMAGES_DIR / self.exp / "miniccc_responses" / cmd_id
        marker: str = nonce + "\n"

        def find_dir() -> Path | None:
            if not base.is_dir():
                return None
            for entry in base.iterdir():
                if entry.is_dir() and entry.name.lower() == uuid:
                    return entry
            return None

        state: dict[str, bool] = {"exit_seen": False}
        result: dict[str, str] = {}

        def probe() -> tuple[bool, str]:
            d: Path | None = find_dir()
            if d is None:
                return False, f"no response from {uuid} in {base}"
            out_file: Path = d / "stdout"
            out: str = out_file.read_text(errors="replace") if out_file.exists() else ""
            if nonce and not out.startswith(marker):
                state["exit_seen"] = False
                return False, f"stdout {out[:80]!r} is not this command's answer"
            if out.endswith("\n") or state["exit_seen"]:
                result["stdout"] = out[len(marker):] if nonce else out
                code_file: Path = d / "exitcode"
                result["exit"] = code_file.read_text().strip() if code_file.exists() else ""
                err_file: Path = d / "stderr"
                result["stderr"] = err_file.read_text(errors="replace") if err_file.exists() else ""
                return True, ""
            state["exit_seen"] = (d / "exitcode").exists()
            return False, f"stdout {out[:80]!r} has no final newline; exit code recorded: {state['exit_seen']}"

        try:
            wait_for(f"cc response of {vm}", wait_s, 1, probe)
        finally:
            try:
                self.mm.run(f"cc delete command {cmd_id}", self.exp)
            except RuntimeError as exc:
                log(f"cc delete command {cmd_id}: {exc}")
        stdout: str = result["stdout"] + (("\n[stderr]\n" + result["stderr"]) if result.get("stderr") else "")
        code: int | None = int(result["exit"]) if result.get("exit", "").lstrip("-").isdigit() else None
        return CcResult(stdout=stdout, exit_code=code)

    def shell(self, vm: str, script: str, wait_s: float = CC_WAIT_S) -> str:
        """Run a bash script in a guest; return its stdout (and stderr)."""
        nonce: str = "cm-cc-" + secrets.token_hex(8)
        return self.cc(vm, guest_script(f"echo {nonce}\n{script}"), wait_s, nonce).stdout

    def shell_result(self, vm: str, script: str, wait_s: float = CC_WAIT_S) -> CcResult:
        nonce: str = "cm-cc-" + secrets.token_hex(8)
        return self.cc(vm, guest_script(f"echo {nonce}\n{script}"), wait_s, nonce)

    def put_file(self, vm: str, guest_path: str, source: Path, mode: str = "0755") -> None:
        """Write a (small) local file into a guest through a cc script."""
        enc: str = base64.b64encode(source.read_bytes()).decode()
        self.shell(vm, f"echo {enc} | base64 -d > {guest_path} && chmod {mode} {guest_path} && echo ok")

    # -- evidence -------------------------------------------------------------

    def collect(self) -> None:
        """Copy minimega, phenix and experiment state into the evidence directory."""
        # A spike that dies on an exception still leaves what it recorded so far.
        self.save("result.json", self.evidence.model_dump_json(indent=2))
        steps: list[tuple[str, Callable[[], str]]] = [
            ("minimega-vm-info.txt", lambda: json.dumps(self.vm_info(), indent=1)),
            ("minimega-cc-clients.txt", lambda: self.mm.text("cc clients", self.exp)),
            ("minimega-cc-tunnels.txt", lambda: "\n".join(f"== {vm}\n{self.mm.text(f'cc tunnel list {vm}', self.exp)}" for vm in self.vm_info())),
            ("minimega-vlans.txt", lambda: self.mm.text("vlans", self.exp)),
            ("experiment.json", lambda: self.phenix("config", "get", f"experiment/{self.exp}", "--output", "json", check=False)),
            ("ovs-show.txt", lambda: sh(["ovs-vsctl", "show"]).stdout),
        ]  # fmt: skip
        for name, fn in steps:
            try:
                self.save(name, fn())
            except Exception as exc:  # noqa: BLE001
                self.save(name, f"unavailable: {exc}\n")
        for src in (MM_BASE / "minimega.log", MM_BASE / "minimega.stdout"):
            if src.exists():
                self.save(src.name, src.read_text(errors="replace"))
        exp_dir: Path = Path("/phenix/experiments") / self.exp
        if exp_dir.is_dir():
            sh(["cp", "-r", str(exp_dir), str(self.dir / "experiment-files")])
        phenix_log: Path = Path("/var/log/phenix")
        if phenix_log.is_dir():
            sh(["cp", "-r", str(phenix_log), str(self.dir / "phenix-log")])

    def versions(self) -> None:
        cmds: dict[str, list[str]] = {
            "phenix": [PHENIX_BIN, "version"],
            "minimega": ["minimega", "-base", str(MM_BASE), "-e", "version"],
            "qemu": ["qemu-system-x86_64", "--version"],
            "ovs": ["ovs-vsctl", "--version"],
        }
        for key, argv in cmds.items():
            try:
                text: str = sh(argv).stdout.strip()
                self.evidence.versions[key] = text.splitlines()[-1] if text else ""
            except Exception as exc:  # noqa: BLE001
                self.evidence.versions[key] = f"unavailable: {exc}"
        for key, env in (("phenix-ref", "CM_PHENIX_REF"), ("images-ref", "CM_IMAGES_REF"), ("apps-ref", "CM_APPS_REF")):
            if os.environ.get(env):
                self.evidence.versions[key] = os.environ[env]

    def finish(self) -> int:
        """Write result.json and summary.md; return the process exit code."""
        self.save("result.json", self.evidence.model_dump_json(indent=2))
        lines: list[str] = [f"### Spike {self.spike}: {'PASS' if self.evidence.passed else 'FAIL'}", ""]
        lines += [f"- {'PASS' if c.passed else 'FAIL'}: {c.name}" + (f" ({c.detail[:200]})" if c.detail else "") for c in self.evidence.checks]
        lines += ["", "Versions:"] + [f"- {k}: {v}" for k, v in self.evidence.versions.items()]
        self.save("summary.md", "\n".join(lines) + "\n")
        summary: str | None = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with Path(summary).open("a") as fh:
                fh.write("\n".join(lines) + "\n")
        return 0 if self.evidence.passed else 1


def main_guard(fn: Callable[[], int]) -> None:
    """Run a spike main; an unexpected exception is a failure with a traceback."""
    try:
        code: int = fn()
    except Exception:  # noqa: BLE001
        import traceback

        traceback.print_exc()
        code = 2
    sys.exit(code)
