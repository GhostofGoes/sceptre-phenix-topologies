#!/usr/bin/env python3
"""CI driver for the wan-bgp spike on VyOS: the lab's WAN on four real routers.

Run as root on a runner prepared by .github/actions/cm-lab, with phenix
patched by patches/vrouter-config-passthrough.ci-e2e-minimega.patch:
  sudo -E /opt/cm-spike-venv/bin/python vyos_ci.py

Four VyOS routers and two hosts boot under phenix and minimega: two ISPs that
peer (shadecast, tumbleweed), the head office's edge on both (fr-fw-edge), and
the High Country edge on both (hc-fw-edge). Every router gets the commands of
vyos_sketch.py as the `config` list of the vrouter app, so phenix writes them
into the script the image runs at first boot.

Checked, in order:
  1. phenix renders the list after its own configuration, before the commit;
  2. every router commits it at first boot (when one does not,
     vyos_commit_check.sh names the lines it refuses);
  3. BGP between the ISPs and to the head office, the head office's two
     default routes and their preferred source;
  4. the two WireGuard tunnels, the mark that ties each to an uplink, and BGP
     with BFD inside them;
  5. a host at the site reaches a host at the head office through the tunnel.
"""

from __future__ import annotations

import importlib.util
import ipaddress
import json
import re
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

HERE: Path = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "ci"))
sys.path.insert(0, str(HERE))

import netns_lab as model  # noqa: E402
import vyos_sketch  # noqa: E402
from vmlab import Lab, log, main_guard, wait_for  # noqa: E402

EXP: str = "spike-wan-bgp"
BOOT_DIR: Path = Path("/phenix/experiments") / EXP / "vrouter"
WORK: Path = Path("/tmp/cm-wan-bgp-vyos")
WGKEYS: Path = HERE.parents[1] / "tools" / "plantgen" / "plantgen" / "wgkeys.py"
# Anyone can derive these keys from this seed: they protect nothing but this run.
KEY_SEED: str = "cherrymountain-wan-bgp-spike-only"
VBASH: str = "source /opt/vyatta/etc/functions/script-template"
SECRET: re.Pattern[str] = re.compile(r"(private-key|public-key) '[^']+'")


def load_wgkeys() -> ModuleType:
    spec = importlib.util.spec_from_file_location("cm_wgkeys", WGKEYS)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {WGKEYS}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["cm_wgkeys"] = module
    spec.loader.exec_module(module)
    return module


def scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    text = str(value)
    return text if re.fullmatch(r"[A-Za-z][A-Za-z0-9_./-]*", text) else json.dumps(text)


def emit(value: Any, indent: int = 0) -> str:
    """Block-style YAML of dicts, lists, strings, and integers."""
    pad = "  " * indent
    lines: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, (dict, list)) and item:
                lines += [f"{pad}{key}:", emit(item, indent + 1)]
            else:
                lines.append(f"{pad}{key}: {scalar(item)}")
    else:
        for item in value:
            if isinstance(item, (dict, list)):
                body = emit(item, indent + 1).splitlines()
                lines += [f"{pad}- {body[0].strip()}", *body[1:]]
            else:
                lines.append(f"{pad}- {scalar(item)}")
    return "\n".join(lines)


def vlan(link: model.Link) -> str:
    return f"{model.BY_NAME[link.a].short}-{model.BY_NAME[link.b].short}".upper()


def interface(name: str, vlan_name: str, address: str, mask: int, gateway: str = "") -> dict[str, Any]:
    entry: dict[str, Any] = {"name": name, "vlan": vlan_name, "address": address, "mask": mask}
    if gateway:
        entry["gateway"] = gateway
    return {**entry, "proto": "static", "type": "ethernet"}


def topology() -> dict[str, Any]:
    """The reduced lab as a phenix topology. A router's interfaces are in the order of its links."""
    nodes: list[dict[str, Any]] = []
    for router in model.ROUTERS:
        interfaces = [
            interface(eth, vlan(link), own, ipaddress.ip_network(link.net).prefixlen)
            for eth, link, _other, _gateway, own in vyos_sketch.uplinks(router)
        ]
        for lan in model.LANS:
            if lan.router == router.name:
                interfaces.append(interface(f"eth{len(interfaces)}", f"{router.short.upper()}-LAN", lan.gateway(), 24))
        nodes.append(
            {
                "type": "Router",
                "general": {"hostname": router.name},
                "hardware": {
                    "os_type": "vyos",
                    "drives": [{"image": "vyos.qc2", "inject_partition": 3}],
                    "memory": 2048,
                    "vcpus": 2,
                },
                "network": {"interfaces": interfaces},
            }
        )
    for lan in model.LANS:
        short = model.BY_NAME[lan.router].short.upper()
        nodes.append(
            {
                "type": "VirtualMachine",
                "general": {"hostname": lan.host},
                "hardware": {
                    "os_type": "linux",
                    "drives": [{"image": "cm-spike.qc2", "inject_partition": 1}],
                    "memory": 1024,
                    "vcpus": 1,
                },
                "network": {"interfaces": [interface("eth0", f"{short}-LAN", lan.host_address(), 24, lan.gateway())]},
            }
        )
    return {
        "apiVersion": "phenix.sandia.gov/v1",
        "kind": "Topology",
        "metadata": {"name": EXP},
        "spec": {"nodes": nodes},
    }


def scenario(configs: dict[str, list[str]]) -> dict[str, Any]:
    return {
        "apiVersion": "phenix.sandia.gov/v2",
        "kind": "Scenario",
        "metadata": {"name": EXP, "annotations": {"topology": EXP}},
        "spec": {
            "apps": [
                {
                    "name": "vrouter",
                    "hosts": [{"hostname": name, "metadata": {"config": lines}} for name, lines in configs.items()],
                }
            ]
        },
    }


def vyos_op(lab: Lab, vm: str, command: str) -> str:
    """Run a VyOS operational command in a router."""
    script = (
        f"cat > /tmp/op.sh <<'OPEOF'\n#!/bin/vbash\n{VBASH}\nrun {command}\nOPEOF\n"
        "chmod +x /tmp/op.sh; sg vyattacfg -c /tmp/op.sh 2>&1"
    )
    return lab.shell(vm, script, wait_s=180)


def bgp_states(lab: Lab, vm: str) -> dict[str, str]:
    """Neighbor -> state, from FRR on a router."""
    out = lab.shell(vm, "vtysh -c 'show bgp ipv4 unicast summary json' 2>&1")
    try:
        peers = json.loads(out[out.index("{") :]).get("peers", {})
    except ValueError:
        return {"(no answer)": out.strip()[:200]}
    return {f"{address} ({peer.get('desc', '')})": peer.get("state", "?") for address, peer in peers.items()}


def expected_sessions(router: model.Router) -> int:
    count = sum(1 for link in model.LINKS if router.name in (link.a, link.b) and link.relation != "static")
    return count + sum(1 for tunnel in model.TUNNELS if router.name in (tunnel.hub, tunnel.site))


def run() -> int:
    lab = Lab("wan-bgp-vyos", EXP)
    lab.versions()
    vyos_sketch.reduce_lab()
    model.PIN = model.PIN_MARK
    wgkeys = load_wgkeys()

    def keys(tunnel: model.Tunnel, side: str) -> tuple[str, str]:
        other = "site" if side == "hub" else "hub"
        return (
            wgkeys.derive(KEY_SEED, f"{tunnel.name}-{side}").private_key,
            wgkeys.derive(KEY_SEED, f"{tunnel.name}-{other}").public_key,
        )

    routers = [router.name for router in model.ROUTERS]
    hosts = [lan.host for lan in model.LANS]
    configs = {router.name: vyos_sketch.commands(router, keys) for router in model.ROUTERS}
    WORK.mkdir(parents=True, exist_ok=True)
    topology_file, scenario_file = WORK / "topology.yml", WORK / "scenario.yml"
    topology_file.write_text(emit(topology()) + "\n")
    scenario_file.write_text(emit(scenario(configs)) + "\n")
    lab.save("topology.yml", topology_file.read_text())
    lab.save("scenario.yml", SECRET.sub(r"\1 'REDACTED'", scenario_file.read_text()))
    lab.fact("commands", json.dumps({name: len(lines) for name, lines in configs.items()}))

    try:
        lab.create_experiment(topology_file, scenario_file)
        lab.start_cli()
        lab.wait_running(*routers, *hosts, timeout_s=300)
        for vm in routers:
            lab.wait_cc_vm(vm, timeout_s=1200)
        lab.wait_cc(*hosts, timeout_s=600)

        # 1. What phenix rendered.
        for vm in routers:
            boot = BOOT_DIR / f"{vm}.boot"
            text = boot.read_text() if boot.exists() else ""
            lab.save(f"{vm}.boot", SECRET.sub(r"\1 'REDACTED'", text))
            extra = text.split("Extra config", 1)[1] if "Extra config" in text else ""
            lab.check(
                f"phenix rendered the config list of {vm} after its own configuration, before the commit",
                all(line in extra for line in configs[vm]) and extra.rstrip().endswith("commit\nsave\nexit"),
                f"{len(configs[vm])} commands; {boot}",
            )

        version = vyos_op(lab, "fr-fw-edge", "show version")
        lab.fact("fr-fw-edge show version", version)
        lab.fact(
            "fr-fw-edge kernel, FRR, WireGuard",
            lab.shell(
                "fr-fw-edge",
                "uname -r; vtysh -c 'show version' 2>&1 | head -1; "
                "modinfo wireguard 2>/dev/null | grep -E '^(filename|version|intree)' || echo 'wireguard: no module info'",
            ),
        )

        # 2. The commit at first boot. The boot script runs after the cc client registers, so poll.
        probe = (
            f"cat > /tmp/op.sh <<'OPEOF'\n#!/bin/vbash\n{VBASH}\nrun show configuration commands\nOPEOF\n"
            "chmod +x /tmp/op.sh; sg vyattacfg -c /tmp/op.sh 2>&1 | grep -c 'protocols bgp'; "
            "journalctl -u vyos-router --no-pager 2>/dev/null | grep -iE 'vyos.script|error|fail|commit' | tail -20"
        )
        committed: dict[str, bool] = {}
        for vm in routers:

            def applied(v: str = vm) -> tuple[bool, str]:
                out = lab.shell(v, probe, wait_s=180)
                return bool(re.match(r"\s*([1-9]\d*)", out)), out

            try:
                detail = wait_for(f"phenix's config on {vm}", 300, 10, applied)
                committed[vm] = True
            except TimeoutError as exc:
                detail, committed[vm] = str(exc), False
            lab.check(f"{vm} committed the rendered configuration at first boot", committed[vm], detail[:600])
            if not committed[vm]:
                # Find out which lines VyOS refuses, and leave the router configured if it takes the rest.
                lab.put_file(vm, "/tmp/vyos_commit_check.sh", HERE / "vyos_commit_check.sh")
                lab.put_file(vm, "/tmp/generated.sh", BOOT_DIR / f"{vm}.boot")
                out = lab.shell(
                    vm, "sg vyattacfg -c 'vbash /tmp/vyos_commit_check.sh /tmp/generated.sh' 2>&1", wait_s=600
                )
                lab.save(f"commit-check-{vm}.txt", SECRET.sub(r"\1 'REDACTED'", out))
                lab.fact(f"{vm} commit check", SECRET.sub(r"\1 'REDACTED'", out)[-3500:])

        # 3. BGP.
        for router in model.ROUTERS:
            want = expected_sessions(router)

            def established(name: str = router.name, count: int = want) -> tuple[bool, str]:
                states = bgp_states(lab, name)
                return len(states) == count and set(states.values()) == {"Established"}, json.dumps(states)

            try:
                detail, ok = wait_for(f"BGP on {router.name}", 240, 10, established), True
            except TimeoutError as exc:
                detail, ok = str(exc), False
            lab.check(f"all {want} BGP sessions of {router.name} are Established", ok, detail[:900])

        default = lab.shell("fr-fw-edge", "ip route show default 2>&1")
        lab.check(
            "the head office installs a default route through each ISP, with its endpoint as the source",
            default.count("nexthop via") == 2 and f"src {model.HUB_ENDPOINT}" in default,
            " ".join(default.split()),
        )
        paths = lab.shell(
            "shadecast",
            f"vtysh -c 'show bgp ipv4 unicast {model.HQ_PREFIX}' 2>&1 | head -20; "
            "vtysh -c 'show bgp ipv4 unicast 198.18.101.0/24' 2>&1 | head -12",
        )
        lab.fact("shadecast's routes to the head office and to Tumbleweed", paths)

        # 4. The tunnels and the pin.
        site = "hc-fw-edge"
        rules = lab.shell(site, "ip rule 2>&1")
        pins: dict[str, str] = {}
        for tunnel in model.TUNNELS:
            link = model.link_of(site, tunnel.via)
            eth = next(e for e, entry, *_rest in vyos_sketch.uplinks(model.BY_NAME[site]) if entry is link)
            got = lab.shell(site, f"ip route get {model.HUB_ENDPOINT} mark {tunnel.mark()} 2>&1")
            pins[tunnel.name] = f"wants {eth}: {' '.join(got.split())}"
            lab.check(
                f"the mark of {tunnel.name} sends its packets out {eth}, the uplink to {tunnel.via}",
                f"dev {eth} " in got and f"fwmark {hex(tunnel.mark())}" in rules,
                pins[tunnel.name],
            )
        lab.fact("hc-fw-edge ip rule", rules)
        wg = lab.shell(site, "wg show all endpoints 2>&1; wg show all latest-handshakes 2>&1; wg show all fwmark 2>&1")
        lab.fact("hc-fw-edge wg", wg)
        endpoints = re.findall(r"(wg\d+)\s+\S+\s+(\S+:\d+)", wg)
        handshakes = [int(stamp) for stamp in re.findall(r"wg\d+\s+\S+\s+(\d+)\s*$", wg, flags=re.M)]
        lab.check(
            "both tunnels have a handshake, to the endpoint the site was given",
            len(endpoints) == len(model.TUNNELS)
            and all(address.startswith(f"{model.HUB_ENDPOINT}:") for _wg, address in endpoints)
            and len([stamp for stamp in handshakes if stamp > 0]) >= len(model.TUNNELS),
            f"endpoints {endpoints}, handshakes {handshakes}",
        )
        lab.fact("fr-fw-edge wg", lab.shell("fr-fw-edge", "wg show all endpoints 2>&1"))
        route = lab.shell(site, "ip route get 10.20.30.11 2>&1; ip route show 10.20.0.0/16 2>&1")
        lab.check(
            "the site reaches the head office's block through its primary tunnel",
            "dev wg101" in route,
            " ".join(route.split()),
        )
        back = lab.shell("fr-fw-edge", "ip route show 10.21.0.0/16 2>&1")
        lab.check("the head office reaches the site's block through the same tunnel", "dev wg101" in back, back.strip())
        lab.fact("hc-fw-edge bfd", lab.shell(site, "vtysh -c 'show bfd peers brief' 2>&1"))

        # 5. End to end.
        ping = lab.shell("hc-historian", "ping -c 10 -i 0.2 -W 2 10.20.30.11 2>&1 | tail -3")
        lab.check("a host at the site reaches a host at the head office", " 0% packet loss" in ping, ping.strip())

        for vm in routers:
            lab.save(
                f"state-{vm}.txt",
                lab.shell(
                    vm,
                    "for c in 'show bgp ipv4 unicast summary' 'show bgp ipv4 unicast' 'show ip route' "
                    '\'show bfd peers\'; do echo "== $c"; vtysh -c "$c" 2>&1; done; '
                    "echo '== ip rule'; ip rule; echo '== ip route show table all'; "
                    "ip route show table all | grep -v ' table local' | head -60; echo '== wg show'; "
                    "wg show 2>&1 | grep -v 'private key'",
                    wait_s=180,
                ),
            )
        log("done")
    finally:
        lab.collect()
        lab.stop_experiment()
    return lab.finish()


if __name__ == "__main__":
    main_guard(run)
