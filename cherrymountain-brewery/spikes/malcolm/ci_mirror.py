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

        # Capture loss under iperf3, by bytes. The host mirrors the large segments the guests
        # exchange (TCP segmentation offload), so one captured frame stands for many wire
        # packets and packet counts say nothing; run 5 counted 3% of the packets and the MTU
        # of the mirror path made no difference. Compare the TCP payload bytes captured with
        # the bytes iperf3's receiver reports.
        def iperf_loss(label: str, rate: str) -> tuple[float, int, int]:
            lab.shell("malcolm", f"rm -f /tmp/iperf.pcap; (setsid nohup timeout 120 tcpdump -ni {CAP_IF} -U -s 128 -w /tmp/iperf.pcap >/tmp/tcpdump-iperf.log 2>&1 </dev/null &); sleep 2; echo started")
            res: str = lab.shell(
                "gen-a",
                f"iperf3 -c {B} -t 20 -b {rate} -J >/tmp/iperf.json 2>&1; "
                "python3 -c \"import json;print(json.load(open('/tmp/iperf.json'))['end']['sum_received']['bytes'])\"",
                wait_s=120,
            )
            time.sleep(3)
            lab.shell("malcolm", "pkill -INT tcpdump; sleep 2; echo stopped")
            kernel: str = lab.shell("malcolm", "cat /tmp/tcpdump-iperf.log")
            out: str = lab.shell(
                "malcolm",
                f"tcpdump -nn -r /tmp/iperf.pcap 2>/dev/null | grep -E ' > {B}\\.5201: ' | "
                "awk '{n++; b += $NF} END {print n, b}'",
            )
            frames, payload = (int(x) for x in out.strip().splitlines()[-1].split())
            received: int = int(res.strip().splitlines()[-1])
            loss: float = 100.0 * max(received - payload, 0) / max(received, 1)
            m = re.search(r"(\d+) packets dropped by kernel", kernel)
            lab.fact(
                f"iperf_{label}",
                f"iperf3 received {received} bytes; captured {frames} frames carrying {payload} payload bytes "
                f"(avg {payload // max(frames, 1)} B/frame); loss {loss:.2f}%; tcpdump kernel drops {m.group(1) if m else 'n/a'}",
            )
            return loss, payload, received

        loss, payload, received = iperf_loss("100M", "100M")
        lab.fact("capture_loss_default_mtu_100M", f"{loss:.2f}% ({payload}/{received} bytes)")
        # Run 9 found the cut-off: ICMP payloads up to 1400 bytes are captured, 1472 (a 1500-byte
        # IP packet) and larger are not, while a 10 Mbit/s flow of small UDP datagrams is captured
        # completely. A full-size frame plus its 802.1Q tag (and GRE) exceeds a 1500-byte MTU
        # somewhere on the mirror path; the `phenix` internal port is the candidate in the capture VM.
        sizes: list[int] = [64, 500, 1000, 1400, 1472, 4000]
        lab.shell("malcolm", "rm -f /tmp/ladder.pcap; (setsid nohup timeout 120 tcpdump -ni " + CAP_IF + " -U -w /tmp/ladder.pcap >/dev/null 2>&1 </dev/null &); sleep 2; echo started")
        for size in sizes:
            lab.shell("gen-a", f"ping -c 20 -i 0.2 -W 2 -M dont -s {size} {B} >/dev/null 2>&1; echo done")
        time.sleep(3)
        lab.shell("malcolm", "pkill -INT tcpdump; sleep 2; echo stopped")
        seen: dict[int, int] = {}
        for size in sizes:
            seen[size] = count(lab, "/tmp/ladder.pcap", f"IP {A} > {B}: ICMP echo request, id [0-9]+, seq [0-9]+, length {size + 8}$")
        lab.fact("ping_size_ladder", f"echo requests captured out of 20 per ICMP payload size: {seen}")
        lab.shell("malcolm", "rm -f /tmp/udp.pcap; (setsid nohup timeout 120 tcpdump -ni " + CAP_IF + " -U -w /tmp/udp.pcap >/dev/null 2>&1 </dev/null &); sleep 2; echo started")
        lab.shell("gen-b", "setsid nohup iperf3 -s -p 5202 >/tmp/iperf-udp.log 2>&1 </dev/null & sleep 1; echo up")
        udp_out: str = lab.shell("gen-a", f"iperf3 -c {B} -p 5202 -u -l 200 -b 10M -t 10 2>&1 | tail -4", wait_s=90)
        time.sleep(3)
        lab.shell("malcolm", "pkill -INT tcpdump; sleep 2; echo stopped")
        udp_seen: int = count(lab, "/tmp/udp.pcap", f" > {B}.5202: UDP, length 200")
        lab.fact("udp_small_frames", f"{udp_out.strip()[-300:]} | captured UDP datagrams to {B}:5202 = {udp_seen}")

        def big_frames(label: str) -> None:
            lab.shell("malcolm", "rm -f /tmp/big.pcap; (setsid nohup timeout 60 tcpdump -ni " + CAP_IF + " -U -w /tmp/big.pcap >/dev/null 2>&1 </dev/null &); sleep 2; echo started")
            lab.shell("gen-a", f"ping -c 20 -i 0.2 -W 2 -M dont -s 1472 {B} >/dev/null 2>&1; echo done")
            time.sleep(3)
            lab.shell("malcolm", "pkill -INT tcpdump; sleep 2; echo stopped")
            lab.fact(f"echo_1472_captured_{label}", str(count(lab, "/tmp/big.pcap", f"IP {A} > {B}: ICMP echo request, id [0-9]+, seq [0-9]+, length 1480$")) + " of 20")

        lab.shell("malcolm", f"ip link set {CAP_IF} mtu 9000; ip -br link show {CAP_IF}")
        big_frames("phenix_mtu9000")
        route: str = sh(["ip", "route", "get", "172.30.255.254"]).stdout
        lab.fact("host_route_to_capture_vm", route.strip().splitlines()[0] if route.strip() else "")
        match = re.search(r"dev (\S+)", route)
        for dev in ({match.group(1)} if match else set()) | set(lab.taps("malcolm")):
            sh(["ip", "link", "set", dev, "mtu", "9000"])
        lab.shell("malcolm", "ip link set eth1 mtu 9000; ip -br link show eth1")
        big_frames("phenix_and_path_mtu9000")
        # Run 10: MTU 9000 on the capture port, the capture VM's mirror NIC, its tap and the host
        # route device changed nothing (1472-byte pings still 0 of 20). Look at the host's GRE port
        # for drops, then turn off the don't-fragment bit of the tunnel.
        def gre_ports() -> list[str]:
            out: str = sh(["ovs-vsctl", "--format=csv", "--no-heading", "--columns=name,type", "list", "interface"]).stdout
            return [ln.split(",")[0].strip('"') for ln in out.splitlines() if ln.split(",")[-1].strip('"') == "gre"]

        ports: list[str] = gre_ports()
        lab.fact("host_gre_ports", str(ports))
        for port in ports:
            lab.fact(f"gre_port_{port}_before", sh(["ovs-vsctl", "--columns=statistics,status,options", "list", "interface", port]).stdout)
        big_frames("before_df_off")
        for port in ports:
            lab.fact(f"gre_port_{port}_after_big_pings", sh(["ovs-vsctl", "--columns=statistics,status", "list", "interface", port]).stdout)
            sh(["ovs-vsctl", "set", "interface", port, "options:df_default=false"])
        lab.fact("host_ip_mtu_all", sh(["ip", "-br", "link"]).stdout)
        big_frames("df_off")
        loss, payload, received = iperf_loss("100M_df_off", "100M")
        lab.check("with the GRE don't-fragment bit off, capture loss under the 100 Mbit/s iperf3 run is under 1% (payload bytes)", loss < 1.0, f"{loss:.2f}% ({payload}/{received} bytes)")
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
