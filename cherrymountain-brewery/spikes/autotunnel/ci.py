#!/usr/bin/env python3
"""CI driver for the autotunnel spike: phenix ui, real guests, host port checks.

Run as root on a runner prepared by .github/actions/cm-lab:
  sudo -E /opt/cm-spike-venv/bin/python ci.py
"""

from __future__ import annotations

import shutil
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE: Path = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "ci"))

from vmlab import Lab, log, main_guard, sh, wait_for  # noqa: E402

EXP: str = "spike-autotunnel"
EXP_CLI: str = "spike-autotunnel-cli"
STUB: Path = HERE / "stub_server.py"
# (vm, guest port) of every tunnel the topology's annotations ask for, and the
# sport the annotation names for it. phenix does not listen on the sport: the
# minimega tunnel gets a random host port in 50000-64999, found with `cc tunnel
# list` (see the spike README).
PLAN: dict[tuple[str, int], int] = {
    ("scada-fuxa", 1881): 11881,
    ("sim-ctl", 3000): 13001,
    ("sim-ctl", 8080): 18080,
    ("sim-ctl", 9200): 19200,
}


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


def host_port(lab: Lab, vm: str, guest_port: int) -> int | None:
    """Host port of the minimega tunnel to guest_port of vm, from `cc tunnel list <vm>`."""
    try:
        rows: list[dict[str, str]] = lab.mm.table(f"cc tunnel list {vm}", lab.exp)
    except RuntimeError:  # "no such client": the VM has no C2 client now
        return None
    for row in rows:
        if row.get("dst") == "127.0.0.1" and row.get("dst port") == str(guest_port):
            src: str = row.get("src port", "")
            return int(src) if src.isdigit() else None
    return None


def fetch_via_tunnel(lab: Lab, vm: str, guest_port: int, timeout_s: float = 120) -> tuple[int | None, str]:
    """Wait for the tunnel to vm:guest_port, then read the guest's banner through it."""
    found: dict[str, int] = {}

    def probe() -> tuple[bool, str]:
        port: int | None = host_port(lab, vm, guest_port)
        if port is None:
            return False, f"no tunnel to {vm}:{guest_port} in `cc tunnel list {vm}`"
        found["port"] = port
        return reachable(port)

    try:
        banner: str = wait_for(f"tunnel to {vm}:{guest_port}", timeout_s, 3, probe)
    except TimeoutError as exc:
        banner = f"NO REPLY ({exc})"
    return found.get("port"), banner


def start_stubs(lab: Lab, vm: str, ports: list[int]) -> None:
    lab.put_file(vm, "/tmp/stub_server.py", STUB)
    out: str = lab.shell(
        vm,
        f"setsid nohup python3 /tmp/stub_server.py {' '.join(map(str, ports))} >/tmp/stub.log 2>&1 </dev/null & sleep 2; ss -ltn | grep -c ':{ports[0]} '",
    )
    log(f"stubs on {vm}: listening count {out.strip()}")


def rest_case(lab: Lab) -> None:
    """The experiment is started through phenix ui's REST API (the process that also holds the forwards)."""
    lab.create_experiment(HERE / "topology.yml")
    started: float = time.monotonic()
    lab.start_rest()
    lab.wait_running("sim-ctl", "scada-fuxa", "late-vm")
    # Pause late-vm before its C2 client can register: phenix waits for the client for
    # DefaultC2Timeout (5 min) and then gives up on the tunnel. (A VM killed this early
    # did not boot again in run 4, so it is paused, not killed.)
    lab.mm.run("vm stop late-vm", EXP)
    log("late-vm paused before C2 registration")

    lab.wait_cc("sim-ctl", "scada-fuxa")
    lab.fact("c2_registered_after_s", f"{time.monotonic() - started:.0f}")
    start_stubs(lab, "sim-ctl", [3000, 8080, 9200])
    start_stubs(lab, "scada-fuxa", [1881])

    # Every annotated tunnel appears (phenix waits for the C2 client) and carries data.
    t0: float = time.monotonic()
    ports: dict[tuple[str, int], int | None] = {}
    for (vm, gport), sport in PLAN.items():
        port, banner = fetch_via_tunnel(lab, vm, gport)
        ports[(vm, gport)] = port
        lab.check(f"the tunnel to {vm}:{gport} carries data from the host", banner == f"stub {vm}:{gport}", f"host port {port}: {banner}")
    lab.fact("tunnels_answered_within_s", f"{time.monotonic() - t0:.0f}")
    lab.fact("host_ports", str({f"{vm}:{g}": p for (vm, g), p in ports.items()}))
    lab.save("tunnels-after-boot.txt", "\n".join(lab.mm.text(f"cc tunnel list {vm}", EXP) for vm in ("sim-ctl", "scada-fuxa")))
    lab.save("forwards-rest.txt", "\n".join(f"{vm}: {lab.api('GET', f'/experiments/{EXP}/vms/{vm}/forwards')}" for vm in ("sim-ctl", "scada-fuxa")))

    # The annotation's first number is not the host port.
    in_range: bool = all(p is not None and 50000 <= p <= 64999 for p in ports.values())
    same: list[str] = [f"{vm}:{g}" for (vm, g), sport in PLAN.items() if ports[(vm, g)] == sport]
    listening: dict[int, bool] = {sport: reachable(sport)[0] for sport in PLAN.values()}
    lab.fact("annotation_sport_is_host_port", f"{bool(same)} (equal for {same}); host ports in 50000-64999: {in_range}; annotation ports answering: {listening}")
    lab.check("every host port can be discovered with `cc tunnel list` (the annotation sport is not the host port)", all(p is not None for p in ports.values()), str(ports))

    sim_event_port: int | None = ports[("sim-ctl", 8080)]
    try:
        posted: str = http("POST", sim_event_port or 0, '{"event":"start-batch"}')
    except (urllib.error.URLError, OSError) as exc:
        posted = f"error {exc}"
    lab.check("POST to the sim-ctl event API stand-in works from the host", posted.startswith("posted sim-ctl:8080"), posted)

    # phenix's own UI is unaffected.
    ui_code, _ = lab.api("GET", "/experiments")
    lab.check("phenix ui still answers while tunnels are open", ui_code == 200, str(ui_code))

    # phenix at fa8338d re-creates forwards that drop, once C2 returns (a 5 s loop in phenix ui).
    def tunnel_back(vm: str, gport: int, banner: str, what: str, timeout_s: float = 240) -> None:
        port, got = fetch_via_tunnel(lab, vm, gport, timeout_s)
        lab.fact(f"{what}_tunnel", f"port {port}: {got}")
        lab.check(f"{what}: phenix re-creates the tunnel to {vm}:{gport} by itself", got == banner, f"port {port}: {got}")

    # vm kill / vm start
    lab.mm.run("vm kill sim-ctl", EXP)
    time.sleep(3)
    lab.fact("after_vm_kill", f"tunnel port {host_port(lab, 'sim-ctl', 3000)}")
    lab.mm.run("vm start sim-ctl", EXP)
    time.sleep(20)
    wait_for("sim-ctl C2 after restart", 300, 5, lambda: ("sim-ctl" in lab.cc_clients(), str(sorted(lab.cc_clients()))))
    start_stubs(lab, "sim-ctl", [3000, 8080, 9200])
    tunnel_back("sim-ctl", 3000, "stub sim-ctl:3000", "vm_restart")
    lab.save("tunnels-after-restart.txt", lab.mm.text("cc tunnel list sim-ctl", EXP))

    # Guest reboot
    # The reboot is delayed so the cc answer reaches the host first.
    lab.shell("scada-fuxa", "setsid nohup sh -c 'sleep 15; reboot' >/dev/null 2>&1 </dev/null & echo rebooting")
    try:
        wait_for("scada-fuxa C2 to drop", 120, 3, lambda: ("scada-fuxa" not in lab.cc_clients(), str(sorted(lab.cc_clients()))))
    except TimeoutError as exc:
        lab.fact("guest_reboot_c2_drop", f"C2 entry never dropped: {exc}")
    time.sleep(10)
    wait_for("scada-fuxa C2 after reboot", 300, 5, lambda: ("scada-fuxa" in lab.cc_clients(), str(sorted(lab.cc_clients()))))
    start_stubs(lab, "scada-fuxa", [1881])
    tunnel_back("scada-fuxa", 1881, "stub scada-fuxa:1881", "guest_reboot")

    # Late VM: paused since start, so its C2 client is more than the old 5 minutes late.
    time.sleep(310)
    lab.fact("late_vm_state_before_resume", str(lab.vm_info().get("late-vm", {}).get("state")))
    ui_log: str = (lab.dir / "phenix-ui.log").read_text(errors="replace")
    lab.fact("late_vm_logs_before_resume", "\n".join(ln for ln in ui_log.splitlines() if "late-vm" in ln)[-1500:])
    lab.check("a paused VM has no tunnel while it has no C2 client", host_port(lab, "late-vm", 8080) is None)
    lab.mm.run("vm start late-vm", EXP)
    try:
        wait_for("late-vm C2", 300, 5, lambda: ("late-vm" in lab.cc_clients(), str(sorted(lab.cc_clients()))))
    except TimeoutError as exc:
        lab.fact("late_vm_c2_timeout", f"{exc}; vm info: {lab.vm_info().get('late-vm')}")
        lab.check("a late VM (C2 after more than 5 minutes) gets its tunnel by itself", False, str(exc))
        return
    start_stubs(lab, "late-vm", [8080])
    tunnel_back("late-vm", 8080, "stub late-vm:8080", "late_vm")
    lab.save("tunnels-after-late-boot.txt", lab.mm.text("cc tunnel list late-vm", EXP))
    ui_log = (lab.dir / "phenix-ui.log").read_text(errors="replace")
    lab.save("phenix-ui-autotunnel-lines.txt", "\n".join(ln for ln in ui_log.splitlines() if "tunnel" in ln.lower()))


def cli_case(lab: Lab) -> None:
    """The experiment is started with `phenix experiment start`, a separate process from `phenix ui`.

    The fork's autotunnel loop in phenix ui reads the running experiments from the store, so the tunnels
    should appear although phenix ui did not start the experiment. Checks go to the main lab's evidence.
    """
    cli: Lab = Lab("autotunnel-cli", EXP_CLI)
    try:
        cli.create_experiment(HERE / "topology-cli.yml")
        started: float = time.monotonic()
        out: str = cli.start_cli()
        lab.fact("cli_start", f"`phenix experiment start` returned after {time.monotonic() - started:.0f}s; last lines: {out.strip()[-300:]!r}")
        cli.wait_running("sim-ctl", "scada-fuxa")
        cli.wait_cc("sim-ctl", "scada-fuxa")
        lab.fact("cli_c2_registered_after_s", f"{time.monotonic() - started:.0f}")
        start_stubs(cli, "sim-ctl", [3000, 8080, 9200])
        start_stubs(cli, "scada-fuxa", [1881])

        ports: dict[tuple[str, int], int | None] = {}
        for (vm, gport) in PLAN:
            if vm == "late-vm":
                continue
            port, banner = fetch_via_tunnel(cli, vm, gport)
            ports[(vm, gport)] = port
            lab.check(
                f"CLI-started experiment: the tunnel to {vm}:{gport} appears and carries data from the host",
                banner == f"stub {vm}:{gport}",
                f"host port {port}: {banner}",
            )
        lab.fact("cli_host_ports", str({f"{vm}:{g}": p for (vm, g), p in ports.items()}))
        lab.save("cli-tunnels.txt", "\n".join(cli.mm.text(f"cc tunnel list {vm}", EXP_CLI) for vm in ("sim-ctl", "scada-fuxa")))
        forwards: str = "\n".join(f"{vm}: {cli.api('GET', f'/experiments/{EXP_CLI}/vms/{vm}/forwards')}" for vm in ("sim-ctl", "scada-fuxa"))
        lab.save("cli-forwards-rest.txt", forwards)
        ui_log: str = (lab.dir / "phenix-ui.log").read_text(errors="replace")
        lines: list[str] = [ln for ln in ui_log.splitlines() if EXP_CLI in ln and "autotunnel" in ln.lower()]
        lab.save("cli-phenix-ui-autotunnel-lines.txt", "\n".join(lines))
        lab.check(
            "phenix ui logged the autotunnels of the CLI-started experiment",
            any("requested" in ln for ln in lines),
            "; ".join(lines[:3])[:300],
        )
        listed: bool = all(f"{g}" in forwards for (_, g) in ports)
        lab.check("phenix ui's forwards API lists the CLI-started experiment's tunnels", listed, forwards[:300])

        # A phenix ui restart: the new process adopts the tunnels the old one left open.
        shutil.copy(lab.dir / "phenix-ui.log", lab.dir / "phenix-ui-before-restart.log")
        lab.stop_ui()
        lab.start_ui()
        time.sleep(20)
        adopted: str = "\n".join(f"{vm}: {cli.api('GET', f'/experiments/{EXP_CLI}/vms/{vm}/forwards')}" for vm in ("sim-ctl", "scada-fuxa"))
        lab.save("cli-forwards-after-ui-restart.txt", adopted)
        after: dict[tuple[str, int], str] = {k: fetch_via_tunnel(cli, k[0], k[1], 60)[1] for k in ports}
        lab.check(
            "after a phenix ui restart the CLI-started experiment's tunnels still carry data",
            all(b == f"stub {vm}:{g}" for (vm, g), b in after.items()),
            str(after),
        )
        lab.check("after a phenix ui restart the forwards API lists them again", all(f"{g}" in adopted for (_, g) in ports), adopted[:300])
        lab.fact("cli_ui_restart_log", "\n".join(ln for ln in (lab.dir / "phenix-ui.log").read_text(errors="replace").splitlines() if "autotunnel" in ln.lower())[-1500:])
    finally:
        cli.collect()
        cli.stop_experiment()
        sh(["cp", "-f", str(HERE / "topology-cli.yml"), str(cli.dir / "topology.yml")])


def run() -> int:
    lab: Lab = Lab("autotunnel", EXP)
    lab.versions()
    try:
        lab.start_ui()
        try:
            rest_case(lab)
        finally:
            lab.collect()
            lab.stop_experiment()
            sh(["cp", "-f", str(HERE / "topology.yml"), str(lab.dir / "topology.yml")])
        cli_case(lab)
    finally:
        lab.stop_ui()
        lab.save("result.json", lab.evidence.model_dump_json(indent=2))
    return lab.finish()


if __name__ == "__main__":
    main_guard(run)
