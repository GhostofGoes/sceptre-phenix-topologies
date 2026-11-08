#!/usr/bin/env python3
"""CI driver for the autotunnel spike: phenix ui, real guests, host port checks.

Run as root on a runner prepared by .github/actions/cm-lab:
  sudo -E /opt/cm-spike-venv/bin/python ci.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE: Path = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "ci"))

from vmlab import Lab, log, main_guard, sh, wait_for  # noqa: E402

EXP: str = "spike-autotunnel"
STUB: Path = HERE / "stub_server.py"
# host port -> (vm, guest port)
PLAN: dict[int, tuple[str, int]] = {
    11881: ("scada-fuxa", 1881),
    13001: ("sim-ctl", 3000),
    18080: ("sim-ctl", 8080),
    19200: ("sim-ctl", 9200),
}
LATE_PORT: int = 14001
LATE_GAVE_UP: str = "startup autotunnel not created: waiting for the C2 client"


def http(method: str, port: int, body: str | None = None) -> str:
    req: urllib.request.Request = urllib.request.Request(  # noqa: S310
        f"http://127.0.0.1:{port}/", data=body.encode() if body is not None else None, method=method
    )
    with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310
        return resp.read().decode(errors="replace").strip()


def reachable(port: int) -> tuple[bool, str]:
    try:
        return True, http("GET", port)
    except (urllib.error.URLError, OSError) as exc:
        return False, str(exc)


def start_stubs(lab: Lab, vm: str, ports: list[int]) -> None:
    lab.put_file(vm, "/tmp/stub_server.py", STUB)
    out: str = lab.shell(
        vm,
        f"setsid nohup python3 /tmp/stub_server.py {' '.join(map(str, ports))} >/tmp/stub.log 2>&1 </dev/null & sleep 2; ss -ltn | grep -c ':{ports[0]} '",
    )
    log(f"stubs on {vm}: listening count {out.strip()}")


def run() -> int:
    lab: Lab = Lab("autotunnel", EXP)
    lab.versions()
    try:
        # check_ports.sh before: no planned host port is taken.
        env: dict[str, str] = {"PLAN": " ".join(f"{p}:{v}" for p, (v, _) in PLAN.items()) + f" {LATE_PORT}:late-vm"}
        before = subprocess.run(  # noqa: S603
            ["bash", str(HERE / "check_ports.sh"), "before"], capture_output=True, text=True, env={**os.environ, **env}, check=False
        )
        lab.check("planned host ports are free before the experiment", before.returncode == 0, before.stdout)

        lab.start_ui()
        lab.create_experiment(HERE / "topology.yml")
        started: float = time.monotonic()
        lab.start_rest()
        lab.wait_running("sim-ctl", "scada-fuxa", "late-vm")
        # Kill late-vm before its C2 client can register: phenix waits for the
        # client for DefaultC2Timeout (5 min) and then gives up on the tunnel.
        lab.mm.run("vm kill late-vm", EXP)
        log("late-vm killed before C2 registration")

        lab.wait_cc("sim-ctl", "scada-fuxa")
        registered: float = time.monotonic() - started
        lab.fact("c2_registered_after_s", f"{registered:.0f}")

        start_stubs(lab, "sim-ctl", [3000, 8080, 9200])
        start_stubs(lab, "scada-fuxa", [1881])

        # All planned ports answer within 2 minutes of guests having C2.
        t0: float = time.monotonic()
        answers: dict[int, str] = {}
        for port, (vm, gport) in PLAN.items():
            try:
                answers[port] = wait_for(f"port {port} ({vm}:{gport})", 120, 2, lambda p=port: reachable(p))
            except TimeoutError as exc:
                answers[port] = f"NO REPLY ({exc})"
        took: float = time.monotonic() - t0
        for port, (vm, gport) in PLAN.items():
            want: str = f"stub {vm}:{gport}"
            lab.check(f"host :{port} reaches {vm}:{gport}", answers[port] == want, answers[port])
        lab.fact("planned_ports_answered_within_s", f"{took:.0f}")
        lab.save("tunnels-after-boot.txt", lab.mm.text("cc tunnel list", EXP))

        # Read and write paths for the event API stand-in.
        try:
            posted: str = http("POST", 18080, '{"event":"start-batch"}')
        except (urllib.error.URLError, OSError) as exc:
            posted = f"error {exc}"
        lab.check("POST to the sim-ctl event API stand-in works from the host", posted.startswith("posted sim-ctl:8080"), posted)

        # phenix's own UI is unaffected (CI serves it on 3080, the plan never uses 3000).
        ui_code, _ = lab.api("GET", "/experiments")
        lab.check("phenix ui still answers while tunnels are open", ui_code == 200, str(ui_code))
        lab.check("no planned port is 3000", 3000 not in PLAN and LATE_PORT != 3000)

        # Restart a tunnelled VM.
        lab.mm.run("vm kill sim-ctl", EXP)
        time.sleep(3)
        gone: tuple[bool, str] = reachable(13001)
        lab.fact("after_vm_kill_13001", f"{gone}")
        lab.mm.run("vm start sim-ctl", EXP)

        def clients_without_stale() -> tuple[bool, str]:
            return "sim-ctl" in lab.cc_clients(), str(sorted(lab.cc_clients()))

        time.sleep(20)
        wait_for("sim-ctl C2 after restart", 300, 5, clients_without_stale)
        start_stubs(lab, "sim-ctl", [3000, 8080, 9200])
        survived, detail = False, ""
        try:
            detail = wait_for("tunnel 13001 after restart", 90, 3, lambda: reachable(13001))
            survived = detail == "stub sim-ctl:3000"
        except TimeoutError as exc:
            detail = str(exc)
        lab.fact("tunnel_survives_vm_restart", f"{survived}: {detail}")
        lab.save("tunnels-after-restart.txt", lab.mm.text("cc tunnel list", EXP))
        if not survived:
            code, body = lab.api("POST", f"/experiments/{EXP}/vms/sim-ctl/forwards?src=13001&host=127.0.0.1&dst=3000")
            lab.fact("recreate_forward_rest_after_restart", f"{code} {body}")
            ok, banner = reachable(13001)
            lab.check("restarted VM's tunnel can be re-created with the REST forward call", ok and banner == "stub sim-ctl:3000", banner)

        # Late-booting VM: phenix gives up on a tunnel whose VM has no C2 client in 5 minutes.
        def gave_up() -> tuple[bool, str]:
            log_text: str = (lab.dir / "phenix-ui.log").read_text(errors="replace")
            lines: list[str] = [ln for ln in log_text.splitlines() if LATE_GAVE_UP in ln and "late-vm" in ln]
            return bool(lines), f"{len(lines)} lines"

        try:
            wait_for("phenix to give up on late-vm's tunnel", 420, 10, gave_up)
            lab.fact("late_vm_gave_up_logged", "yes")
        except TimeoutError as exc:
            lab.fact("late_vm_gave_up_logged", f"no: {exc}")
        lab.mm.run("vm start late-vm", EXP)
        wait_for("late-vm C2", 300, 5, lambda: ("late-vm" in lab.cc_clients(), str(sorted(lab.cc_clients()))))
        start_stubs(lab, "late-vm", [8080])
        time.sleep(5)
        auto_ok, auto_banner = reachable(LATE_PORT)
        lab.fact("late_vm_gets_autotunnel", f"{auto_ok}: {auto_banner}")
        lab.save("tunnels-after-late-boot.txt", lab.mm.text("cc tunnel list", EXP))
        if not auto_ok:
            code, body = lab.api("POST", f"/experiments/{EXP}/vms/late-vm/forwards?src={LATE_PORT}&host=127.0.0.1&dst=8080")
            lab.fact("recreate_forward_rest_for_late_vm", f"{code} {body}")
            ok, banner = reachable(LATE_PORT)
            lab.check(
                "a late-booting VM gets its tunnel, or the REST forward call creates it",
                ok and banner == "stub late-vm:8080",
                banner,
            )
        else:
            lab.check("a late-booting VM gets its tunnel, or the REST forward call creates it", auto_banner == "stub late-vm:8080", auto_banner)
    finally:
        lab.collect()
        lab.stop_experiment()
        lab.stop_ui()
        sh(["cp", "-f", str(HERE / "topology.yml"), str(lab.dir / "topology.yml")])
    return lab.finish()


if __name__ == "__main__":
    main_guard(run)
