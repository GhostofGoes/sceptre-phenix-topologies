"""netcheck on the committed configs, and on copies with one thing broken."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from conftest import edit_yaml, node_of, ruleset, vrouter_host

from netcheck import checks, conduits, plan
from netcheck import model as m
from netcheck.sim import Net, flow, trace


def errors(findings: list[checks.Finding]) -> list[str]:
    return [f"[{f.check}] {f.message}" for f in findings if f.severity == "error"]


def run(tree: Path, variant: str = "full") -> list[str]:
    return errors(checks.run_variant(variant, tree))


def has(found: list[str], *words: str) -> bool:
    return any(all(w in line for w in words) for line in found)


def scenario(tree: Path, variant: str = "full") -> Path:
    return tree / "phenix-configs" / f"scenario-{variant}.yml"


# --- the committed files -------------------------------------------------------


@pytest.mark.parametrize("variant", ["full", "lite"])
def test_committed_variant_is_clean(variant: str) -> None:
    assert errors(checks.run_variant(variant)) == []


def test_committed_ci_is_clean() -> None:
    assert errors(checks.run_ci()) == []


def test_design_totals() -> None:
    assert len(plan.nodes_for("lite")) == 37
    assert plan.totals("lite") == (37, 48, 71)
    assert plan.totals("full") == (43, 67, 113)


def test_every_design_flow_is_traced_both_ways() -> None:
    v: m.Variant = m.load_variant("full")
    net: Net = Net(v.topology)
    seen: int = 0
    for cd in conduits.CONDUITS:
        if not cd.enforced:
            continue
        for src, dst, proto, port in conduits.expand(cd, "full"):
            fwd, rev = flow(net, src, dst, proto, port, dst_ip=cd.dst_ip)
            assert fwd.result == "allowed", (cd.id, src, dst, fwd)
            assert rev is not None
            assert rev.result in ("allowed", "local"), (cd.id, src, dst, rev)
            seen += 1
    assert seen > 100


def test_dnp3_crosses_ipsec_and_both_firewalls() -> None:
    v: m.Variant = m.load_variant("full")
    fwd = trace(Net(v.topology), "utility-derms", "10.20.15.10", "tcp", 20000)
    assert fwd.result == "allowed"
    assert fwd.path == ["utility-derms", "util-rtr", "fw-edge", "fw-ot", "rtu-der"]
    assert fwd.fw_in == {"util-rtr": "eth1", "fw-edge": "eth0", "fw-ot": "eth0"}


def test_snat_is_undone_for_the_reply() -> None:
    v: m.Variant = m.load_variant("full")
    fwd, rev = flow(Net(v.topology), "corp-ws", "inet-nuke", "tcp", 443)
    assert fwd.snat == [("fw-edge", "203.0.113.2", "10.10.10.10")]
    assert rev is not None
    assert rev.result == "allowed"
    assert rev.final_node == "corp-ws"


def test_wireguard_dnat_reaches_dmz_vpn() -> None:
    v: m.Variant = m.load_variant("full")
    fwd = trace(Net(v.topology), "vendor-ws", "203.0.113.2", "udp", 51820)
    assert fwd.result == "allowed"
    assert fwd.final_node == "dmz-vpn"
    assert trace(Net(v.topology), "vendor-ws", "203.0.113.2", "udp", 51821).result == "local"


# --- one thing broken -----------------------------------------------------------


def test_a_missing_rule_fails_its_conduit(tree: Path) -> None:
    def drop_dnp3(doc: dict[str, Any]) -> None:
        rs = ruleset(doc, "fw-ot", "FROM-DMZ")
        rs["rules"] = [r for r in rs["rules"] if not r.get("description", "").startswith("dnp3")]

    edit_yaml(scenario(tree), drop_dnp3)
    found: list[str] = run(tree)
    assert has(found, "utility-derms -> rtu-der", "(dnp3)", "not delivered")
    assert has(found, "fw-ot", "dnp3", "no") or has(found, "(dnp3)")


def test_an_extra_rule_is_an_uncovered_path(tree: Path) -> None:
    def open_hmi_to_plc(doc: dict[str, Any]) -> None:
        rs = ruleset(doc, "fw-ot", "FROM-HMI")
        rs["rules"].append(
            {
                "id": 990,
                "action": "accept",
                "description": "oops: HMI to the cellar PLC",
                "protocol": "tcp",
                "source": {"address": "10.20.20.0/24"},
                "destination": {"address": "10.20.10.12", "port": 502},
            }
        )

    edit_yaml(scenario(tree), open_hmi_to_plc)
    found: list[str] = run(tree)
    assert has(found, "hmi-brewhouse -> plc-cellar tcp/502", "no conduit covers it")
    assert has(found, "rule 990", "matches no conduit flow")


def test_a_default_accept_is_caught(tree: Path) -> None:
    def accept_corp(doc: dict[str, Any]) -> None:
        ruleset(doc, "fw-edge", "FROM-CORP")["default"] = "accept"

    edit_yaml(scenario(tree), accept_corp)
    found: list[str] = run(tree)
    assert has(found, "FROM-CORP", "default is accept")
    assert has(found, "corp-ws", "no conduit covers it")


def test_a_missing_route_is_a_gap(tree: Path) -> None:
    def no_ot_route(doc: dict[str, Any]) -> None:
        n = node_of(doc, "fw-edge")
        n["network"]["routes"] = [
            r for r in n["network"]["routes"] if r["destination"] != "10.20.0.0/16"
        ]

    edit_yaml(tree / "phenix-configs" / "topology-core.yml", no_ot_route)
    found: list[str] = run(tree)
    assert has(found, "utility-derms -> rtu-der", "no-route")
    assert has(found, "fw-edge", "routes is")


def test_a_duplicate_address_is_caught(tree: Path) -> None:
    def clash(doc: dict[str, Any]) -> None:
        node_of(doc, "ews")["network"]["interfaces"][0]["address"] = "10.20.30.11"

    edit_yaml(tree / "phenix-configs" / "topology-core.yml", clash)
    found: list[str] = run(tree)
    assert has(found, "10.20.30.11 on both")
    assert has(found, "ews", "interfaces")


def test_a_wrong_vlan_id_is_caught(tree: Path) -> None:
    edit_yaml(tree / "phenix.yml", lambda d: d["spec"]["vlans"].update({"OPS": 150}))
    assert has(run(tree), "VLAN OPS is 150")


def test_a_removed_probe_is_caught(tree: Path) -> None:
    def drop(doc: dict[str, Any]) -> None:
        app = next(a for a in doc["spec"]["apps"] if a["name"] == "soh")
        tests = app["metadata"]["testCustomReachability"]
        app["metadata"]["testCustomReachability"] = [
            t for t in tests if not (t["src"] == "utility-derms" and t["port"] == 20000)
        ]

    edit_yaml(scenario(tree), drop)
    assert has(run(tree), "no testCustomReachability for utility-derms -> rtu-der")


def test_a_negative_test_for_an_open_path_is_caught(tree: Path) -> None:
    # hmi-brewhouse -> scada-fuxa:1881 is a conduit; claim it is blocked.
    def lie(doc: dict[str, Any]) -> None:
        app = next(a for a in doc["spec"]["apps"] if a["name"] == "soh")
        tests = app["metadata"]["hostCustomTests"]["hmi-brewhouse"]
        tests.append(dict(tests[0], name="blocked-scada-fuxa-1881"))

    edit_yaml(scenario(tree), lie)
    assert has(run(tree), "blocked-scada-fuxa-1881", "not in the blocked-path list")


def test_a_wait_cycle_is_caught(tree: Path) -> None:
    def cycle(doc: dict[str, Any]) -> None:
        node_of(doc, "plc-cellar")["delay"] = {"c2": [{"hostname": "scada-fuxa", "useUUID": False}]}

    edit_yaml(tree / "phenix-configs" / "topology-core.yml", cycle)
    assert has(run(tree), "plc-cellar waits for scada-fuxa", "later in boot order")


def test_the_ntp_label_is_caught(tree: Path) -> None:
    def label(doc: dict[str, Any]) -> None:
        node_of(doc, "ot-svc")["labels"]["ntp-server"] = "IF0"

    edit_yaml(tree / "phenix-configs" / "topology-core.yml", label)
    assert has(run(tree), "ntp-server label")


def test_a_wrong_ipsec_peer_is_caught(tree: Path) -> None:
    def wrong(doc: dict[str, Any]) -> None:
        vrouter_host(doc, "util-rtr")["ipsec"][0]["remote"] = "203.0.113.9"

    edit_yaml(scenario(tree), wrong)
    assert has(run(tree), "util-rtr", "IPsec remote")


def test_a_stale_dns_zone_is_caught(tree: Path) -> None:
    hosts: Path = tree / "phenix-injects" / "ot-svc" / "plant.hosts"
    hosts.write_text(hosts.read_text().replace("10.20.30.12 ews", "10.20.30.99 ews"))
    assert has(run(tree), "plant.hosts", "ews")


def test_a_missing_inject_is_caught(tree: Path) -> None:
    (tree / "phenix-injects" / "bbmd" / "cm_bbmd.py").unlink()
    assert has(run(tree), "missing phenix-injects/bbmd/cm_bbmd.py")


def test_lite_checks_run_without_the_soc(tree: Path) -> None:
    assert run(tree, "lite") == []


def test_a_router_script_run_from_slash_config_is_caught(tree: Path) -> None:
    """VyOS bind-mounts /opt/vyatta/etc/config over /config, so a file injected
    into boot/vyos/rw/config/ is not under /config in the running router."""

    def old_path(doc: dict[str, Any]) -> None:
        node_of(doc, "fw-ot")["commands"] = [
            "exec sg vyattacfg -c /config/scripts/custom/cm-vyos-config.sh"
        ]

    edit_yaml(tree / "phenix-configs" / "topology-core.yml", old_path)
    found: list[str] = run(tree)
    assert has(found, "router-commands", "fw-ot", "/config/scripts/custom/")
    assert has(found, "router-commands", "fw-ot", "no commands entry")


def test_a_router_script_that_nothing_runs_is_caught(tree: Path) -> None:
    def drop(doc: dict[str, Any]) -> None:
        del node_of(doc, "isp-rtr")["commands"]

    edit_yaml(tree / "phenix-configs" / "topology-core.yml", drop)
    assert has(run(tree), "router-commands", "isp-rtr", "no commands entry")


@pytest.mark.parametrize(("variant", "workflow"), [("lite", "phenix-lite.yml"), ("ci", "phenix-ci.yml")])
def test_a_bridge_shared_with_another_variant_is_caught(tree: Path, variant: str, workflow: str) -> None:
    """phenix refuses a second stored experiment on a non-default bridge."""

    def shared(doc: dict[str, Any]) -> None:
        doc["spec"]["defaultBridge"] = plan.BRIDGES["full"]

    edit_yaml(tree / workflow, shared)
    found: list[str] = errors(checks.run_ci(tree)) if variant == "ci" else run(tree, variant)
    assert has(found, f"{variant}/workflow", "defaultBridge must be", plan.BRIDGES[variant])


def test_bridges_are_distinct_and_fit_an_interface_name() -> None:
    assert len(set(plan.BRIDGES.values())) == len(plan.BRIDGES)
    assert all(len(b) <= plan.MAX_BRIDGE_NAME for b in plan.BRIDGES.values())


def scorch_component(doc: dict[str, Any], name: str) -> dict[str, Any]:
    app: dict[str, Any] = next(a for a in doc["spec"]["apps"] if a["name"] == "scorch")
    return next(c for c in app["metadata"]["components"] if c["name"] == name)


def test_a_soh_component_without_network_config_is_caught(tree: Path) -> None:
    """phenix runs the reachability tests only when network-config is checked."""

    def drop(doc: dict[str, Any]) -> None:
        scorch_component(doc, "health-check")["metadata"]["checks"].remove("network-config")

    edit_yaml(scenario(tree), drop)
    assert has(run(tree), "scorch", "health-check", "network-config")


def test_an_unknown_scorch_component_is_caught(tree: Path) -> None:
    def rename(doc: dict[str, Any]) -> None:
        scorch_component(doc, "settle")["name"] = "settle-down"

    edit_yaml(scenario(tree), rename)
    assert has(run(tree), "scorch", "vendor-maintenance", "no component named settle")


def test_a_capture_that_never_stops_is_caught(tree: Path) -> None:
    def never_stop(doc: dict[str, Any]) -> None:
        app: dict[str, Any] = next(a for a in doc["spec"]["apps"] if a["name"] == "scorch")
        run_md: dict[str, Any] = next(
            r for r in app["metadata"]["runs"] if r["name"] == "utility-outage"
        )
        run_md["stop"].remove("pcap-utility-outage")

    edit_yaml(scenario(tree), never_stop)
    assert has(run(tree), "scorch", "pcap-utility-outage", "never stops")


def test_wazuh_agents_are_the_hosts_with_a_conduit_to_the_manager(repo: Path) -> None:
    """plant.yaml's Wazuh agent allowlist (which alone gets the enrollment
    password) is exactly the hosts that reach wazuh on TCP 1514 through a
    conduit, plus the hosts that share its SOC VLAN. bas-bbmd has no conduit
    and is not an agent."""
    import yaml

    doc: dict[str, Any] = yaml.safe_load((repo / "plant" / "plant.yaml").read_text())
    agents: set[str] = set(doc["variants"]["full"]["app"]["wazuh"]["agents"])
    reach: set[str] = {
        src
        for c in conduits.CONDUITS
        for src, dst, proto, port in conduits.expand(c, "full")
        if dst == "wazuh" and proto == "tcp" and port == 1514
    }
    soc: set[str] = {n.name for n in plan.hosts_in("SOC", "full") if not n.is_router} - {"wazuh"}
    assert agents == (reach | soc) - {"wazuh"}
    assert "bas-bbmd" not in agents
