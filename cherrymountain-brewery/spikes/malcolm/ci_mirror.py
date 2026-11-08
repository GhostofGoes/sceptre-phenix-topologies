#!/usr/bin/env python3
"""CI driver for the Malcolm spike's capture half: the mirror app with setupOVS.

Boots generators, a router and a capture VM, lets phenix-app-mirror mirror the
OPS and CTRL VLANs over GRE into the capture VM's OVS bridge, and counts what
arrives, per direction. Run as root on a runner prepared by
.github/actions/cm-lab (with the apps):
  sudo -E /opt/cm-spike-venv/bin/python ci_mirror.py
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

HERE: Path = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "ci"))

from vmlab import Lab, log, main_guard, sh, wait_for  # noqa: E402

EXP: str = "spike-malcolm"
PINGS: int = 20
A, B, C = "10.20.30.30", "10.20.30.31", "10.20.40.40"
CAP_IF: str = "phenix"  # bridge created by phenix-app-mirror's setup-ovs.sh


def count(lab: Lab, pcap: str, regex: str) -> int:
    """Count decoded packets matching regex. Text matching instead of a pcap filter:
    a filter such as `icmp` does not match frames that carry a VLAN tag."""
    out: str = lab.shell("malcolm", f"tcpdump -nn -r {pcap} 2>/dev/null | grep -cE '{regex}' || true")
    return int(out.strip().splitlines()[-1])


def iface_stats(lab: Lab, vm: str, dev: str) -> int:
    out: str = lab.shell(vm, f"cat /sys/class/net/{dev}/statistics/rx_packets")
    return int(out.strip().splitlines()[-1])


def run() -> int:
    lab: Lab = Lab("malcolm-mirror", EXP)
    lab.versions()
    try:
        lab.create_experiment(HERE / "topology.yml", HERE / "scenario.yml", default_bridge="cherrymtn")
        out: str = lab.start_cli()
        lab.check("experiment start reports the mirror app ran", "mirror" in out and "post-start" in out, out[-500:])
        lab.wait_running("gen-a", "gen-b", "gen-c", "rtr", "malcolm")
        lab.wait_cc("gen-a", "gen-b", "gen-c", "malcolm")
        lab.wait_cc_vm("rtr")

        # Host side: what the mirror app built.
        host_mirrors: str = sh(["ovs-vsctl", "list", "mirror"]).stdout
        lab.save("host-ovs-mirrors.txt", host_mirrors)
        lab.check("host OVS has a mirror and a GRE port from the mirror app", "select_dst_port" in host_mirrors and "gre" in sh(["ovs-vsctl", "show"]).stdout, host_mirrors[:400])

        # Router: addresses and forwarding through cc.
        lab.shell(
            "rtr",
            "ip -br link; ip addr add 10.20.30.1/24 dev eth0; ip addr add 10.20.40.1/24 dev eth1; "
            "ip link set eth0 up; ip link set eth1 up; sysctl -w net.ipv4.ip_forward=1; ip -br addr",
        )
        lab.fact("rtr_addresses", lab.shell("rtr", "ip -br addr"))

        # setupOVS: the bridge and sniffer port exist in the guest, the way the mirror app promises.
        def ovs_ready() -> tuple[bool, str]:
            shown: str = lab.shell("malcolm", "ovs-vsctl show 2>&1; ip -br link")
            return "sniffer" in shown, shown

        try:
            shown: str = wait_for("malcolm's OVS bridge and sniffer port", 120, 5, ovs_ready)
            lab.check("setupOVS creates the in-guest bridge and GRE sniffer port at boot", True, shown)
        except TimeoutError as exc:
            lab.check("setupOVS creates the in-guest bridge and GRE sniffer port at boot", False, str(exc))
            lab.fact("startup_logs", lab.shell("malcolm", "ls -l /etc/phenix/startup; journalctl -u phenix --no-pager | tail -40; systemctl status openvswitch-switch --no-pager | head -20"))
            return lab.finish()
        lab.fact("malcolm_ovs", lab.shell("malcolm", "ovs-vsctl show; ovs-vsctl list-ports phenix; ip -br addr"))
        lab.fact("malcolm_phenix_link_before", lab.shell("malcolm", f"ip -br link show {CAP_IF}"))
        # The script does not bring the bridge's internal port up; capture needs it.
        lab.shell("malcolm", f"ip link set {CAP_IF} up; ip -br link show {CAP_IF}")

        lab.shell("gen-a", f"ping -c 2 -W 2 {B} >/dev/null 2>&1; ping -c 2 -W 2 {C} >/dev/null 2>&1; echo warm")
        lab.shell("gen-b", "setsid nohup iperf3 -s >/tmp/iperf-s.log 2>&1 </dev/null & sleep 1; echo up")

        # Capture, then known traffic. The stock setupOVS bridge floods only frames whose
        # destination it has not learned, so run once as shipped and once with a flow that
        # sends everything arriving on the GRE port to the capture port.
        def capture_counts(label: str) -> dict[str, int]:
            pc: str = f"/tmp/flows-{label}.pcap"
            lab.shell("malcolm", f"rm -f {pc}; (setsid nohup timeout 90 tcpdump -ni {CAP_IF} -U -w {pc} >/tmp/tcpdump-{label}.log 2>&1 </dev/null &); sleep 2; echo started")
            lab.shell("gen-a", f"ping -c {PINGS} -i 0.3 -W 2 {B} | tail -2; ping -c {PINGS} -i 0.3 -W 2 {C} | tail -2; "
                      f"for i in $(seq 1 10); do timeout 1 bash -c 'echo -n probe > /dev/tcp/{B}/502' 2>/dev/null || true; done; echo generated", wait_s=180)
            time.sleep(5)
            lab.shell("malcolm", "pkill -INT tcpdump; sleep 2; echo stopped")
            lab.fact(f"tcpdump_{label}", lab.shell("malcolm", f"cat /tmp/tcpdump-{label}.log"))
            lab.fact(f"decode_{label}", lab.shell("malcolm", f"tcpdump -nn -e -r {pc} 2>/dev/null | head -12"))
            result: dict[str, int] = {
                "same_vlan_req": count(lab, pc, f"IP {A} > {B}: ICMP echo request"),
                "same_vlan_rep": count(lab, pc, f"IP {B} > {A}: ICMP echo reply"),
                "routed_req": count(lab, pc, f"IP {A} > {C}: ICMP echo request"),
                "routed_rep": count(lab, pc, f"IP {C} > {A}: ICMP echo reply"),
                "tcp502_syn": count(lab, pc, f"IP {A}\\.[0-9]+ > {B}\\.502: Flags \\[S\\],"),
                "total": count(lab, pc, "."),
            }
            lab.fact(f"capture_counts_{label}", str(result))
            return result

        stock: dict[str, int] = capture_counts("stock")
        stock_ok: bool = stock["same_vlan_req"] == PINGS and stock["same_vlan_rep"] == PINGS and stock["routed_req"] == PINGS
        lab.fact("stock_setupovs_delivers_every_frame", f"{stock_ok}: {stock}")
        lab.fact("malcolm_fdb", lab.shell("malcolm", "ovs-appctl fdb/show phenix | head -20; ovs-ofctl dump-flows phenix"))
        flow: str = lab.shell("malcolm", f"ovs-ofctl add-flow phenix 'priority=1000,in_port=sniffer,actions=output:{CAP_IF}' && ovs-ofctl dump-flows phenix")
        lab.fact("capture_flow", flow)
        counts: dict[str, int] = capture_counts("flow")
        lab.check("mirrored packets are visible on the in-guest capture interface", counts["total"] > 0, str(counts))
        same_ok: bool = counts["same_vlan_req"] == PINGS and counts["same_vlan_rep"] == PINGS
        lab.check(
            "with the capture flow, same-VLAN flow is captured once per direction (no duplicates)",
            same_ok,
            f"want {PINGS}/{PINGS}, got {counts['same_vlan_req']}/{counts['same_vlan_rep']}",
        )
        routed_ok: bool = counts["routed_req"] == PINGS and counts["routed_rep"] == PINGS
        lab.check(
            "with the capture flow, routed flow is captured once per direction (DESIGN section 14 claim)",
            routed_ok,
            f"want {PINGS}/{PINGS}, got {counts['routed_req']}/{counts['routed_rep']}",
        )

        # Capture loss under iperf3: packets delivered to gen-b vs packets captured for gen-b.
        # GRE adds 42 bytes to every mirrored frame, so full-size frames need an underlay MTU
        # above 1500 (the tunnel does not fragment): run once as is, once with MTU 9000 on the
        # host path to the capture VM and on the VM's mirror NIC.
        def iperf_loss(label: str, rate: str) -> tuple[float, int, int]:
            lab.shell("malcolm", f"rm -f /tmp/iperf.pcap; (setsid nohup timeout 120 tcpdump -ni {CAP_IF} -U -s 96 -w /tmp/iperf.pcap >/tmp/tcpdump-iperf.log 2>&1 </dev/null &); sleep 2; echo started")
            rx0: int = iface_stats(lab, "gen-b", "eth0")
            res: str = lab.shell("gen-a", f"iperf3 -c {B} -t 20 -b {rate} 2>&1 | tail -6", wait_s=120)
            rx1: int = iface_stats(lab, "gen-b", "eth0")
            time.sleep(3)
            lab.shell("malcolm", "pkill -INT tcpdump; sleep 2; echo stopped")
            kernel: str = lab.shell("malcolm", "cat /tmp/tcpdump-iperf.log")
            captured: int = count(lab, "/tmp/iperf.pcap", f" > {B}\\.5201: ")
            delivered: int = rx1 - rx0
            loss: float = 100.0 * max(delivered - captured, 0) / max(delivered, 1)
            m = re.search(r"(\d+) packets dropped by kernel", kernel)
            lab.fact(f"iperf_{label}", f"delivered_to_gen_b={delivered} captured={captured} loss={loss:.2f}% tcpdump_kernel_drops={m.group(1) if m else 'n/a'} | {res.strip()[-160:]}")
            return loss, captured, delivered

        loss, captured, delivered = iperf_loss("mtu1500_100M", "100M")
        lab.fact("capture_loss_mtu1500_100M", f"{loss:.2f}% ({captured}/{delivered})")
        route: str = sh(["ip", "route", "get", "172.30.255.254"]).stdout
        lab.fact("host_route_to_capture_vm", route.strip())
        match = re.search(r"dev (\S+)", route)
        for dev in ({match.group(1)} if match else set()) | set(lab.taps("malcolm")):
            sh(["ip", "link", "set", dev, "mtu", "9000"])
        lab.shell("malcolm", "ip link set eth1 mtu 9000; ip -br link show eth1")
        lab.fact("mtu_after", sh(["ip", "-br", "link"]).stdout + lab.shell("malcolm", "ip -br link"))
        loss, captured, delivered = iperf_loss("mtu9000_100M", "100M")
        lab.check("with MTU 9000 on the mirror path, capture loss under the 100 Mbit/s iperf3 run is under 1%", loss < 1.0, f"{loss:.2f}% ({captured}/{delivered})")
        iperf_loss("mtu9000_unlimited", "0")
        lab.fact("malcolm_link_stats", lab.shell("malcolm", "ip -s link"))
    finally:
        lab.collect()
        lab.stop_experiment()
        for name in ("topology.yml", "scenario.yml"):
            sh(["cp", "-f", str(HERE / name), str(lab.dir / name)])
    log("done")
    return lab.finish()


if __name__ == "__main__":
    main_guard(run)
