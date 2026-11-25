"""Lay out the phenix topology as a city for the cm_world mod (topology.json).

Purdue levels are terraces: the internet sits highest and the field networks sit
on the ground, so a path down the city is a path into the plant. Each VLAN is a
platform; each host is a tower on the platform of its first non-MGMT VLAN.
Routers, firewalls and dual-homed hosts get a cable to every other VLAN they
touch. MGMT (out of band, every host) is left out so the cables mean something.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml

LEVELS: list[tuple[str, list[str]]] = [
    ("Internet and partners", ["INET", "VENDOR", "UTIL"]),
    ("Enterprise and DMZ", ["CORP", "DMZ"]),
    ("Operations and SOC", ["OPS", "SOC"]),
    ("Supervisory", ["HMI", "BAS", "DERLINK"]),
    ("Control", ["CTRL"]),
    ("Field", ["FIELD-BH", "FIELD-CEL", "FIELD-PKG", "FIELD-UTL", "FIELD-PWR"]),
]
SPACING = 5  # blocks between towers
ROW_GAP = 14  # blocks between terraces (z)
RISE = 6  # blocks of height per level
GAP = 6  # blocks between platforms in a row


def role(host: str, kind: str) -> str:
    if kind in ("Router", "Firewall"):
        return kind.lower()
    for prefix, r in (
        ("plc", "plc"),
        ("rio", "rio"),
        ("rtu", "rio"),
        ("relay", "rio"),
        ("hmi", "hmi"),
        ("scada", "hmi"),
        ("bas", "hmi"),
        ("sim", "sim"),
        ("malcolm", "soc"),
        ("wazuh", "soc"),
        ("soc", "soc"),
        ("historian", "server"),
        ("ews", "server"),
        ("dmz", "server"),
    ):
        if host.startswith(prefix):
            return r
    return "server"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("topologies", nargs="+", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--origin", default="0,0,-150")
    args = ap.parse_args()
    ox, oy, oz = (int(v) for v in args.origin.split(","))
    nodes = [
        n for f in args.topologies for n in (yaml.safe_load(f.read_text())["spec"]["nodes"] or [])
    ]
    vlans_of = {}
    for n in nodes:
        vl = [
            i["vlan"]
            for i in n.get("network", {}).get("interfaces", [])
            if i.get("vlan") and i["vlan"] != "MGMT"
        ]
        vlans_of[n["general"]["hostname"]] = (n, list(dict.fromkeys(vl)))
    level_of = {v: li for li, (_, vs) in enumerate(LEVELS) for v in vs}

    # Home platform: routers and firewalls sit on their highest VLAN, PLCs on CTRL.
    home: dict[str, list[str]] = {v: [] for _, vs in LEVELS for v in vs}
    for host, (_n, vl) in vlans_of.items():
        if not vl:
            continue
        h = min(vl, key=lambda v: level_of.get(v, 99))
        home.setdefault(h, []).append(host)

    platforms, towers, cables, levels = [], [], [], []
    plat_at: dict[str, dict] = {}
    for li, (title, vs) in enumerate(LEVELS):
        y = oy + (len(LEVELS) - 1 - li) * RISE
        z0 = oz + li * ROW_GAP
        widths = [max(2, len(home.get(v, []))) * SPACING + 2 for v in vs]
        x = ox - (sum(widths) + GAP * (len(vs) - 1)) // 2
        levels.append({"title": title, "x": x - 6, "y": y, "z": z0 + 4})
        for v, w in zip(vs, widths, strict=True):
            p = {"vlan": v, "level": li, "x0": x, "x1": x + w - 1, "z0": z0, "z1": z0 + 8, "y": y}
            platforms.append(p)
            plat_at[v] = p
            for k, host in enumerate(sorted(home.get(v, []))):
                n, vl = vlans_of[host]
                r = role(host, n["type"])
                height = {"router": 7, "firewall": 9}.get(
                    r, 2 + min(6, int(n.get("hardware", {}).get("vcpus", 1)) * 2)
                )
                t = {
                    "host": host,
                    "role": r,
                    "x": x + 3 + k * SPACING,
                    "z": z0 + 4,
                    "y": y + 1,
                    "h": height,
                    "vlans": vl,
                }
                towers.append(t)
            x += w + GAP
    for t in towers:
        for v in t["vlans"][1:]:
            p = plat_at.get(v)
            if p:
                cables.append(
                    {
                        "from": [t["x"], t["y"] + t["h"] - 1, t["z"]],
                        "to": [(p["x0"] + p["x1"]) // 2, p["y"] + 1, (p["z0"] + p["z1"]) // 2],
                        "vlan": v,
                        "role": t["role"],
                    }
                )
    args.out.write_text(
        json.dumps(
            {"platforms": platforms, "towers": towers, "cables": cables, "levels": levels}, indent=1
        )
    )
    print(f"{len(platforms)} platforms, {len(towers)} towers, {len(cables)} cables -> {args.out}")


if __name__ == "__main__":
    main()
