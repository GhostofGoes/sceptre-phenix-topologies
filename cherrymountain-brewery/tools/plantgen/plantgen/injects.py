"""The injected files of a site that hold the site's own names and addresses.

A node of a site injects the files that the node of the single site injects
(phenix-injects/). Most of them are the same at every site. A few hold an
address or a hostname of the plant, and a site needs its own copy of those.
This module renders the copies. `RENDERERS` names the files, and every other
file is injected as it is.

Some files belong to a part that only the head office has: the vendor's
access, or the SOC. `SCOPES` holds a decision for every injected file, each
with its reason, and a node of a site injects a file only where the decision
allows it.

A copy is the hand-written file with a few lines set again from the site's
plant description (`edit`). Each such line is found by how it starts, and it
must be there exactly once: when the hand-written file changes its shape, the
generator stops with the name of the line. `ot-svc/plant.hosts` has no line to
keep, so it is written whole.

One part is not in the hand-written modules: the accounts of the company's own
staff at a site's jump host (`SiteSpec.staff`). `staff_injections` gives the
two nodes their files, and the roster `guacamole/staff.env` is a copy with the
site's accounts.

The other such part is the site's network sensor, a node that plantgen makes
(`topology.sensor_node`). `sensor_injections` gives it the first-boot files of
phenix-injects/sensor, the capture scripts of `malcolm`, and the site's copy
of `sensor/cm-sensor.env`, which names the sensor.

plantgen writes the copies to `plant/generated/fleet/<code>/injects/`, next to
the site's other generated files. `sync.py` copies them to
`phenix-injects/fleet/<code>/`, because phenix injects only what is under
phenix-injects. The site's topology file names the copy there.
"""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import string
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, NamedTuple

from plantgen.checks import routed
from plantgen.model import Plant, PlantError
from plantgen.render import bacnet_writers
from plantgen.sites import EDGE, EWS, JUMP, SiteSpec

#: How a topology names a file under phenix-injects.
PREFIX: Final[str] = "/phenix/injects/${BRANCH_NAME}/"
#: The copies in a site's generated directory.
DIRECTORY: Final[str] = "injects"
#: The copies under phenix-injects, where sync.py puts them.
SITE_DIRECTORY: Final[str] = "fleet/{code}"
#: The DNS domain of the plant (phenix-injects/ot-svc/dnsmasq-plant.conf).
DOMAIN: Final[str] = "cherrymountain.test"
#: The BACnet/IP port of the two BBMDs.
BACNET_PORT: Final[int] = 47808
#: The two BBMDs of a site: the one of the operations network and the one of
#: the building network.
OPS_BBMD: Final[str] = "ot-svc"
BAS_BBMD: Final[str] = "bas-bbmd"

#: The roster of a site's staff accounts, and the two scripts that read it.
STAFF_ENV: Final[str] = "guacamole/staff.env"
STAFF_SQL: Final[str] = "guacamole/cm_staff_sql.py"
STAFF_HOOK: Final[str] = "ews/71-ews-staff.sh"
#: Where the two nodes have the roster.
STAFF_TARGET: Final[str] = "/etc/cherrymountain/staff.env"

#: The settings of a site's sensor: its name, its address, and Malcolm's.
SENSOR_ENV: Final[str] = "sensor/cm-sensor.env"
#: The files of a site's sensor: (path under phenix-injects, where the sensor
#: has it, its mode, what it is). The image is cm-sensor, which has the paths
#: of cm-malcolm. The baseline detections of malcolm (`cherrymountain.rules`,
#: `zeek/cherrymountain.zeek`) are not here: they name the hosts of the Front
#: Range site by address, and a site's copy moves only the BACnet writers.
SENSOR_FILES: Final[tuple[tuple[str, str, str, str], ...]] = (
    (
        "malcolm/99-zz-capture-flow.sh",
        "/etc/phenix/startup/99-zz-capture-flow.sh",
        "0755",
        "start the unit that adds the OpenFlow capture flow after the mirror app's "
        "OVS setup",
    ),
    (
        SENSOR_ENV,
        "/etc/default/cm-sensor",
        "0644",
        "the sensor's name, its address, and the address of Malcolm",
    ),
    (
        "sensor/malcolm-config-sensor.yaml",
        "/opt/cherrymountain/malcolm/malcolm-config-sensor.yaml",
        "0644",
        "settings of the sensor profile for Malcolm's configurator",
    ),
    (
        "malcolm/cm-suricata.yaml",
        "/opt/cherrymountain/malcolm/cm-suricata.yaml",
        "0644",
        "Suricata flow settings for mirrored traffic (no VLAN in the flow key, "
        "one-sided streams)",
    ),
    (
        "sensor/cm-sensor-acl",
        "/usr/local/sbin/cm-sensor-acl",
        "0755",
        "host firewall: Arkime's viewer for Malcolm only, GRE only on the mirror "
        "interface",
    ),
    (
        "sensor/cm-sensor-provision",
        "/usr/local/sbin/cm-sensor-provision",
        "0755",
        "sensor first-boot configuration",
    ),
    (
        "sensor/cm-sensor-start",
        "/usr/local/sbin/cm-sensor-start",
        "0755",
        "start the sensor's services",
    ),
    (
        "sensor/cm-sensor-stop",
        "/usr/local/sbin/cm-sensor-stop",
        "0755",
        "stop the sensor's services",
    ),
    (
        "sensor/cm-sensor-health",
        "/usr/local/sbin/cm-sensor-health",
        "0755",
        "sensor health check for SoH and SCORCH",
    ),
    (
        "malcolm/cm-malcolm-capture",
        "/usr/local/sbin/cm-malcolm-capture",
        "0755",
        "OpenFlow flow and port setup for the mirror feed",
    ),
    (
        "docker/cm-load-images",
        "/usr/local/sbin/cm-load-images",
        "0755",
        "load the pre-pulled container images into Docker",
    ),
    (
        "docker/cm-load-images.service",
        "/etc/systemd/system/cm-load-images.service",
        "0644",
        "image loading unit",
    ),
    (
        "sensor/cm-sensor-acl.service",
        "/etc/systemd/system/cm-sensor-acl.service",
        "0644",
        "host firewall unit",
    ),
    (
        "sensor/cm-sensor-provision.service",
        "/etc/systemd/system/cm-sensor-provision.service",
        "0644",
        "sensor first-boot configuration unit",
    ),
    (
        "sensor/cm-sensor.service",
        "/etc/systemd/system/cm-sensor.service",
        "0644",
        "sensor unit",
    ),
    (
        "malcolm/cm-malcolm-capture.service",
        "/etc/systemd/system/cm-malcolm-capture.service",
        "0644",
        "capture port unit",
    ),
    (
        "sensor/66-sensor.sh",
        "/etc/phenix/startup/66-sensor.sh",
        "0755",
        "load the images, close the host firewall, configure, and start the sensor",
    ),
)

NOTE: Final[str] = (
    "# Generated by plantgen for the site {code} from phenix-injects/{path}. "
    "Do not edit."
)


class Scope(NamedTuple):
    """Which sites inject the files of one path of phenix-injects, and why."""

    #: A file, or a directory with `/` at its end.
    path: str
    #: The module of the head office that a site needs for the files
    #: (`sites.MODULES`). None: every site whose node injects them today.
    module: str | None
    why: str


REMOTE: Final[str] = "remote"
SOC: Final[str] = "soc"
#: Not a module of a site: the files of a host of the internet.
INTERNET: Final[str] = "internet"
#: A decision for every injection of the hand-written modules. The first entry
#: that fits a path decides, so a file stands before its directory. A path
#: with no entry stops the generator: a new injection needs a decision.
SCOPES: Final[tuple[Scope, ...]] = (
    Scope(
        "guacamole/ridgeline.env",
        REMOTE,
        "the accounts of the vendor's technicians: their names, passwords, and "
        "TOTP keys. The vendor reaches the head office and no other site",
    ),
    Scope(
        "guacamole/cm_vendor_sql.py",
        REMOTE,
        "creates the Guacamole accounts of the vendor's technicians on the jump host",
    ),
    Scope(
        "ews/70-ews-vendor.sh",
        REMOTE,
        "creates the local accounts of the vendor's technicians on the workstation",
    ),
    Scope("wireguard/", REMOTE, "the vendor's tunnel ends at the head office"),
    Scope("malcolm/", SOC, "the fleet has one SOC, at the head office"),
    Scope("wazuh/", SOC, "the fleet has one SOC, at the head office"),
    Scope("desktop/cm-analyst-console", SOC, "the console of the SOC's analyst"),
    Scope("desktop/cm-analyst.desktop", SOC, "the console of the SOC's analyst"),
    Scope(
        "guacamole/",
        None,
        "the jump host itself. A site with a jump host of its own runs the same "
        "Guacamole, with no account of the vendor",
    ),
    Scope("ews/", None, "the engineering tools and the login screen of a workstation"),
    Scope("bbmd/", None, "the BBMD service. A site's copy has its addresses"),
    Scope("brand/", None, "the brewery's mark and colors, the same at every site"),
    Scope("desktop/", None, "the wallpaper of a desktop"),
    Scope("docker/", None, "loads the container images that a host brings along"),
    Scope(
        "openplc/",
        None,
        "the engineer's copy of a PLC program. It still holds the Front Range "
        "field addresses",
    ),
    Scope(
        "ot-svc/",
        None,
        "time, names, and the syslog relay of a site. The relay sends to the "
        "one Wazuh, at the head office",
    ),
    Scope("vrouter/", None, "the VyOS steps of a site's own routers"),
    Scope(
        "inet-traffic/",
        INTERNET,
        "the services of a host of the internet, which no site has",
    ),
)


def scope(source: str) -> Scope:
    """The decision for the `src` of an injection."""
    path = source.removeprefix(PREFIX)
    for entry in SCOPES:
        if path == entry.path or (
            entry.path.endswith("/") and path.startswith(entry.path)
        ):
            return entry
    raise PlantError(
        f"injects: no entry of SCOPES decides which sites inject {source}; "
        "add one with its reason"
    )


def at_site(site: SiteSpec, source: str) -> bool:
    """Whether a node of the site injects the file `source`."""
    module = scope(source).module
    return module is None or module in site.modules


class Line(NamedTuple):
    """One line of a hand-written file: how it starts, and its text at a site.

    Without a text, the site's copy does not have the line."""

    start: str
    text: str | None


class Cut(NamedTuple):
    """One step of a hand-written script that a site's copy leaves out."""

    name: str
    #: How one line inside the step starts. No other line of the file starts so.
    inside: str
    #: The step's last line, which has no indentation (`esac`, `fi`).
    last: str
    why: str


@dataclass(frozen=True)
class Context:
    """What a renderer knows: the site, its plant, its nodes, and one file.

    `nodes` are the nodes of the site's topology file. `targets` are the names
    of the nodes that inject the file `path`, and `text` is the hand-written
    file.
    """

    site: SiteSpec
    plant: Plant
    nodes: list[dict[str, Any]]
    path: str
    targets: list[str]
    text: str

    def address(self, host: str) -> str:
        """The address of the first interface of a host of the site, with its mask."""
        found = self.plant.file.hosts.get(host)
        if found is None or not found.interfaces:
            raise PlantError(
                f"{self.path}: the site has no host {host} with an address"
            )
        return next(iter(found.interfaces.values())).ip

    def ip(self, host: str) -> str:
        """The address of the first interface of a host of the site."""
        return self.address(host).split("/")[0]

    def target(self) -> str:
        """The one node that injects the file."""
        if len(self.targets) != 1:
            raise PlantError(
                f"{self.path}: one host takes this file, not {self.targets}"
            )
        return self.targets[0]


def edit(context: Context, lines: list[Line], cuts: Sequence[Cut] = ()) -> str:
    """The hand-written file with each of `lines` set, without each of `cuts`,
    and with a note on who wrote it.

    A line keeps its indentation. The note goes under the `#!` line of a
    script, and at the top of any other file. A line for each cut follows it.
    """
    out = context.text.splitlines()
    for cut in cuts:
        out = _without(context, out, cut)
    for line in lines:
        hits = [n for n, old in enumerate(out) if old.lstrip().startswith(line.start)]
        if len(hits) != 1:
            raise PlantError(
                f"phenix-injects/{context.path}: {len(hits)} lines start with "
                f"{line.start!r}, and the site's copy sets exactly one"
            )
        old = out[hits[0]]
        if line.text is None:
            del out[hits[0]]
        else:
            out[hits[0]] = old[: len(old) - len(old.lstrip())] + line.text
    notes = [NOTE.format(code=context.site.code, path=context.path)]
    notes += [f"# Left out of this copy: {cut.name}. {cut.why}" for cut in cuts]
    first = 1 if out and out[0].startswith("#!") else 0
    out[first:first] = notes
    return "\n".join(out) + "\n"


def _without(context: Context, lines: list[str], cut: Cut) -> list[str]:
    """`lines` without one step: its comment, its lines, and one empty line.

    The step starts at the last line with no indentation before `cut.inside`,
    and its comment is the comment lines right above that line.
    """
    where = f"phenix-injects/{context.path}: {cut.name}"
    hits = [n for n, line in enumerate(lines) if line.lstrip().startswith(cut.inside)]
    if len(hits) != 1:
        raise PlantError(
            f"{where}: {len(hits)} lines start with {cut.inside!r}, and the step "
            "has exactly one"
        )
    start = hits[0]
    while start > 0 and lines[start][:1] in (" ", "\t"):
        start -= 1
    while start > 0 and lines[start - 1].startswith("#"):
        start -= 1
    end = next((n for n in range(hits[0], len(lines)) if lines[n] == cut.last), None)
    if end is None:
        raise PlantError(f"{where}: no line {cut.last!r} ends the step")
    if end + 1 < len(lines) and not lines[end + 1].strip():
        end += 1
    return lines[:start] + lines[end + 1 :]


def _vyos_script(context: Context) -> str:
    """vrouter/cm-vyos-config.sh: the routers' names and the time server.

    The script knows a router by its plain name (`fw-edge`), so the copy waits
    for the site's names and then drops the site's prefix. The syslog target
    stays: the fleet has one Wazuh, at the head office. The steps of the
    edge's WAN side are left out (`WAN_SIDE_STEPS`).
    """
    prefix = context.site.host(EDGE).removesuffix(EDGE)
    return edit(
        context,
        [
            Line("OT_SVC=", f"OT_SVC={context.ip(_time_server(context))}"),
            Line(
                "isp-rtr | util-rtr | fw-edge | fw-ot)",
                " | ".join(context.targets) + ")",
            ),
            Line("host=$(hostname)", f"host=$(hostname) && host=${{host#{prefix}}}"),
            # Only the step of the IPsec match reads this address.
            Line("UTILITY_DERMS=", None),
        ],
        WAN_SIDE_STEPS,
    )


_WAN_SIDE: Final[str] = (
    "It is a step of the edge's WAN side, which the files of a site do not have yet."
)
#: The steps of vrouter/cm-vyos-config.sh that change the utility's IPsec at
#: the edge. No site's scenario gives its edge that IPsec: the WAN generator
#: writes the WAN side (tools/wangen), and the utility's tunnel is a later
#: stage. On a router with no such IKE group, the step would fail. When a
#: site with a utility link (`fr`, `ep`) gets its tunnel, these steps return
#: for that site.
WAN_SIDE_STEPS: Final[tuple[Cut, ...]] = (
    Cut("IKEv2 for the utility's IPsec", "fw-edge | util-rtr)", "esac", _WAN_SIDE),
    Cut(
        "the IPsec match on the utility's DNP3 rule",
        '[ "$src" = "$UTILITY_DERMS" ] || continue',
        "fi",
        _WAN_SIDE,
    ),
)


def _time_server(context: Context) -> str:
    return context.site.host(context.site.time_server)


def _bbmd_table(context: Context) -> str:
    """The broadcast distribution table: both BBMDs, without masks."""
    peers = [context.site.host(OPS_BBMD), context.site.host(BAS_BBMD)]
    return ",".join(f"{context.ip(peer)}:{BACNET_PORT}" for peer in peers)


def _bbmd_ops(context: Context) -> str:
    """bbmd/ot-svc.env: the `/0` prefix makes the broadcast 255.255.255.255."""
    own = context.ip(context.target())
    return edit(
        context,
        [
            Line("CM_BBMD_ADDRESS=", f"CM_BBMD_ADDRESS={own}/0:{BACNET_PORT}"),
            Line("CM_BBMD_BDT=", f"CM_BBMD_BDT={_bbmd_table(context)}"),
        ],
    )


def _bbmd_bas(context: Context) -> str:
    """bbmd/bas-bbmd.env: the BBMD's own address with the mask of its network."""
    own = context.address(context.target())
    return edit(
        context,
        [
            Line("CM_BBMD_ADDRESS=", f"CM_BBMD_ADDRESS={own}:{BACNET_PORT}"),
            Line("CM_BBMD_BDT=", f"CM_BBMD_BDT={_bbmd_table(context)}"),
        ],
    )


def _resolver(context: Context) -> str:
    """ot-svc/dnsmasq-plant.conf: the address that dnsmasq listens on."""
    own = context.ip(context.target())
    return edit(context, [Line("listen-address=", f"listen-address={own}")])


def _hosts_file(context: Context) -> str:
    """ot-svc/plant.hosts: every address of every node of the site.

    The first interface of a node has the node's name, and each other one is
    `<node>-<interface>`, as in the hand-written file.
    """
    code = context.site.code
    out = [
        f"# Host names of the site {code} for the dnsmasq zone on {context.target()}.",
        f"# dnsmasq appends {DOMAIN} to each name (expand-hosts). The first",
        "# interface of a host has the plain name; the others are <host>-<interface>.",
        f"# Generated by plantgen from the nodes of topology-site-{code}.yml. "
        "Do not edit.",
    ]
    for node in context.nodes:
        name: str = node["general"]["hostname"]
        for number, interface in enumerate(node["network"]["interfaces"]):
            alias = name if number == 0 else f"{name}-{interface['name'].lower()}"
            out.append(f"{interface['address']} {alias}")
    return "\n".join(out) + "\n"


def time_clients(context: Context, server: str) -> list[str]:
    """The networks of the hosts that take their time from `server`.

    A client asks from the network that it shares with the server, or else
    from its first routed network. A node that the plant does not describe
    (the sensor) asks from the network of its first interface.
    """
    plant = context.plant
    near = plant.hosts[server].vlans()
    found: set[ipaddress.IPv4Network] = set()
    for name, host in plant.hosts.items():
        entry = plant.ntp_entry(name)
        if entry is None or not entry.client:
            continue
        source = entry.source or plant.spec.ntp.default_source
        if source.hostname != server:
            continue
        vlans = [interface.vlan for interface in host.interfaces.values()]
        shared = [vlan for vlan in vlans if vlan in near]
        through = [vlan for vlan in vlans if routed(plant, vlan)]
        for vlan in (shared or through)[:1]:
            network = plant.network(vlan)
            if network is not None:
                found.add(ipaddress.IPv4Network(network.subnet))
    if plant.spec.ntp.default_source.hostname == server:
        for node in context.nodes:
            if node["general"]["hostname"] in plant.spec.ntp.other_nodes:
                leg = node["network"]["interfaces"][0]
                found.add(
                    ipaddress.IPv4Network(
                        f"{leg['address']}/{leg['mask']}", strict=False
                    )
                )
    return [str(network) for network in sorted(found)]


def _time_hook(context: Context) -> str:
    """ot-svc/60-ot-svc.sh: the networks that chrony serves."""
    networks = time_clients(context, context.target())
    if not networks:
        raise PlantError(f"{context.path}: no host takes its time from this server")
    return edit(
        context,
        [
            Line(
                'echo "# Clients:',
                'echo "# Clients: the networks that take their time here (${marker})."',
            ),
            Line("for net in ", f"for net in {' '.join(networks)}; do"),
        ],
    )


def _jump_certificate(context: Context) -> str:
    """guacamole/cm-guacamole-start: the name and address in the TLS certificate."""
    own = context.target()
    name = f"{own}.{DOMAIN}"
    return edit(
        context,
        [
            Line("-subj ", f'-subj "/CN={name}/O=CherryMountain Brewery" \\'),
            Line(
                '-addext "subjectAltName=',
                f'-addext "subjectAltName=DNS:{name},IP:{context.ip(own)}"',
            ),
        ],
    )


def _jump_server_name(context: Context) -> str:
    """guacamole/nginx.conf: the name that the jump host's web server answers to."""
    return edit(
        context,
        [Line("server_name ", f"server_name {context.target()}.{DOMAIN};")],
    )


def _writer_addresses(context: Context) -> list[str]:
    """The addresses that may write a BACnet object at the site
    (`render.bacnet_writers`): the detection content alerts on any other."""
    writers = bacnet_writers(context.plant)
    if not writers:
        raise PlantError(f"{context.path}: the site has no host that writes BACnet")
    return [writer["address"] for writer in writers]


def _the_line_with(context: Context, words: str) -> str:
    """The one line of the hand-written file that holds `words`."""
    hits = [line for line in context.text.splitlines() if words in line]
    if len(hits) != 1:
        raise PlantError(
            f"phenix-injects/{context.path}: {len(hits)} lines hold {words!r}, and "
            "the site's copy sets exactly one"
        )
    return hits[0]


def _suricata_rules(context: Context) -> str:
    """malcolm/cherrymountain.rules: who may write BACnet (sids 9000113 and
    9000114). The rule's source is every address but the writers."""
    writers = ",".join(_writer_addresses(context))
    lines: list[Line] = []
    for service in ("WriteProperty", "WritePropertyMultiple"):
        old = _the_line_with(context, f'(msg:"CM BACnet {service} from a host other')
        head, sep, rest = old.partition("] any -> any ")
        if not head.startswith("alert udp ![") or not sep:
            raise PlantError(
                f"phenix-injects/{context.path}: the {service} rule does not start "
                "with `alert udp ![<writers>] any -> any`"
            )
        lines.append(Line(old, f"alert udp ![{writers}{sep}{rest}"))
    return edit(context, lines)


def _zeek_script(context: Context) -> str:
    """malcolm/zeek/cherrymountain.zeek: who may write BACnet
    (`BACnet_Write_Unexpected`)."""
    writers = ", ".join(_writer_addresses(context))
    start = "const bacnet_writers: set[addr] = "
    return edit(context, [Line(start, f"{start}{{ {writers} }} &redef;")])


def _placeholder(site: SiteSpec, account: str, use: str) -> bytes:
    """20 bytes for one value of a staff account, the same at every run."""
    words = f"cherrymountain staff {site.code} {account} {use}"
    return hashlib.sha256(words.encode()).digest()[:20]


def _placeholder_password(site: SiteSpec, account: str, use: str) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(
        alphabet[byte % len(alphabet)] for byte in _placeholder(site, account, use)
    )


def _staff_roster(context: Context) -> str:
    """guacamole/staff.env: the accounts of the site's staff.

    The passwords and the TOTP keys are placeholders that come from the site
    and the account, so that the copy does not change from run to run. The
    cherrymountain app makes new ones for each experiment.
    """
    site = context.site
    lines = [f"STAFF_COUNT={len(site.staff)}"]
    for number, person in enumerate(site.staff, start=1):
        key = base64.b32encode(_placeholder(site, person.account, "totp")).decode()
        lines += [
            f"# {person.title}",
            f"STAFF{number}_USER={person.account}",
            f"STAFF{number}_GUAC_PASSWORD="
            + _placeholder_password(site, person.account, "guacamole"),
            f"STAFF{number}_TOTP_SECRET={key}",
            f"STAFF{number}_EWS_PASSWORD="
            + _placeholder_password(site, person.account, "ews"),
        ]
    return edit(context, [Line("STAFF_COUNT=", "\n".join(lines))])


def staff_injections(site: SiteSpec, plain: str) -> list[dict[str, str]]:
    """The injections of the site's staff accounts at the host `plain`.

    The jump host gets the script that creates the Guacamole accounts, the
    workstation gets the one that creates the local accounts, and both get the
    site's roster. No other host, and no site without staff, gets any.
    """
    scripts: dict[str, dict[str, str]] = {
        JUMP: {
            "description": "SQL for the named accounts of the company's staff",
            "dst": "/usr/local/lib/cm-guacamole/cm_staff_sql.py",
            "permissions": "0644",
            "src": f"{PREFIX}{STAFF_SQL}",
        },
        EWS: {
            "description": "create the local accounts of the company's staff",
            "dst": "/etc/phenix/startup/71-ews-staff.sh",
            "permissions": "0755",
            "src": f"{PREFIX}{STAFF_HOOK}",
        },
    }
    if not site.staff or plain not in scripts:
        return []
    roster = {
        "description": "range-only staff accounts (Guacamole account, TOTP seed, "
        "EWS account)",
        "dst": STAFF_TARGET,
        "permissions": "0600",
        "src": f"{PREFIX}{SITE_DIRECTORY.format(code=site.code)}/{STAFF_ENV}",
    }
    return [scripts[plain], roster]


def _sensor_settings(context: Context) -> str:
    """sensor/cm-sensor.env: the sensor's name and its address.

    The address of Malcolm stays as the hand-written file has it: the fleet has
    one Malcolm, at the head office, and no site moves it.
    """
    name = context.target()
    node = next(n for n in context.nodes if n["general"]["hostname"] == name)
    address: str = node["network"]["interfaces"][0]["address"]
    return edit(
        context,
        [
            Line("CM_SENSOR_NAME=", f"CM_SENSOR_NAME={name}"),
            Line("CM_SENSOR_ADDRESS=", f"CM_SENSOR_ADDRESS={address}"),
        ],
    )


def sensor_injections(site: SiteSpec) -> list[dict[str, str]]:
    """The injections of the site's sensor (`SENSOR_FILES`).

    A file that holds a name or an address of the site is the site's copy.
    """
    return [
        {
            "description": description,
            "dst": target,
            "permissions": mode,
            "src": site_source(site, f"{PREFIX}{path}"),
        }
        for path, target, mode, description in SENSOR_FILES
    ]


#: The files that a site has its own copy of, by their path under phenix-injects.
RENDERERS: Final[dict[str, Callable[[Context], str]]] = {
    "bbmd/bas-bbmd.env": _bbmd_bas,
    "bbmd/ot-svc.env": _bbmd_ops,
    "guacamole/cm-guacamole-start": _jump_certificate,
    "guacamole/nginx.conf": _jump_server_name,
    STAFF_ENV: _staff_roster,
    "malcolm/cherrymountain.rules": _suricata_rules,
    "malcolm/zeek/cherrymountain.zeek": _zeek_script,
    "ot-svc/60-ot-svc.sh": _time_hook,
    "ot-svc/dnsmasq-plant.conf": _resolver,
    "ot-svc/plant.hosts": _hosts_file,
    SENSOR_ENV: _sensor_settings,
    "vrouter/cm-vyos-config.sh": _vyos_script,
}


def site_source(site: SiteSpec, source: str) -> str:
    """The `src` of an injection at a site: the site's copy, when it has one."""
    path = source.removeprefix(PREFIX)
    if source.startswith(PREFIX) and path in RENDERERS:
        return f"{PREFIX}{SITE_DIRECTORY.format(code=site.code)}/{path}"
    return source


def site_files(
    site: SiteSpec, plant: Plant, nodes: list[dict[str, Any]], sources: Path
) -> dict[str, str]:
    """The site's copies: their text, by their path under phenix-injects.

    `plant` is the site's plant, `nodes` are the nodes of its topology file,
    and `sources` is phenix-injects. A file that no node of the site injects is
    not rendered.
    """
    own = f"{PREFIX}{SITE_DIRECTORY.format(code=site.code)}/"
    targets: dict[str, list[str]] = {}
    for node in nodes:
        for injection in node.get("injections", []):
            source: str = injection["src"]
            if source.startswith(own):
                name: str = node["general"]["hostname"]
                targets.setdefault(source.removeprefix(own), []).append(name)
    files: dict[str, str] = {}
    for path in sorted(targets):
        if path not in RENDERERS:
            raise PlantError(
                f"site {site.code}: {targets[path]} inject {own}{path}, and "
                "plantgen has no renderer for it"
            )
        hand_written = sources / path
        if not hand_written.is_file():
            raise PlantError(
                f"site {site.code}: {hand_written} does not exist; the site's "
                "copy comes from it"
            )
        context = Context(
            site=site,
            plant=plant,
            nodes=nodes,
            path=path,
            targets=targets[path],
            text=hand_written.read_text(encoding="utf-8"),
        )
        try:
            files[path] = RENDERERS[path](context)
        except PlantError as exc:
            raise PlantError(f"site {site.code}: injects: {exc}") from exc
    return files
