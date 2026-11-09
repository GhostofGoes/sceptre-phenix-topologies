"""Per-host ``sohProfile`` entries (DESIGN.md 15.4).

phenix's soh app reads ``sohProfile`` from the metadata of every host of every
app (its ``appMetadataProfileKey``) and runs the processes, listeners, and
custom tests in it. Custom test scripts run as root through miniccc and are
written to minimega's file store, so they never hold a credential: a check
that needs one sources the root-only settings file the app injected.
"""

from __future__ import annotations

import json
import shlex
from typing import Any, Final

from cherrymountain_apps.cherrymountain.metadata import (
    BbmdHost,
    FuxaHost,
    GuacamoleHost,
    HistorianHost,
    HmiHost,
    HostBase,
    OpenPlcHost,
    RioHost,
    SimCtlHost,
    SimFederateHost,
    WazuhManagerHost,
)
from cherrymountain_apps.cherrymountain.render import (
    BACNET_PROCESSES,
    Context,
    bacnet_endpoint,
    fuxa_daq,
    northbound,
)

#: Modbus function codes a read test may use (coils, discrete, holding, input).
READ_FCS: Final[frozenset[int]] = frozenset({1, 2, 3, 4})
#: The ``pgrep -f`` pattern of the HMI kiosk. cm-kiosk waits for FUXA and then
#: replaces itself with the browser (``exec chromium --kiosk ... <URL>``), so
#: its own name is on a command line only until the view opens: the pattern
#: matches the launcher while it waits and the browser's ``--kiosk`` after.
KIOSK_PROCESS: Final[str] = "kiosk"
#: plant.yaml roles of the ot-sim devices that serve Modbus from an I/O module.
IO_MODULE_ROLES: Final[frozenset[str]] = frozenset({"rio", "field"})

MODBUS_READ: Final[str] = """\
python3 - {host} {port} {unit} {fc} {offset} <<'PY'
import socket, struct, sys
host, port, unit, fc, offset = sys.argv[1], *map(int, sys.argv[2:])
with socket.create_connection((host, port), timeout=5) as s:
    s.sendall(struct.pack(">HHHBBHH", 7, 0, 6, unit, fc, offset, 1))
    reply = s.recv(260)
ok = len(reply) >= 9 and reply[7] == fc
print("modbus-ok" if ok else "modbus-bad reply %r" % reply[:9])
PY
"""

FEDERATE_COUNT: Final[str] = """\
want={want}
have=$(ss -Htn state established '( sport = :{port} )' | awk '{{split($4, a, ":"); print a[1]}}' | sort -u | wc -l)
if [ "${{have}}" -ge "${{want}}" ]; then echo "federates-ok ${{have}}/${{want}}"; else echo "federates-low ${{have}}/${{want}}"; fi
"""

BUCKET_FRESH: Final[str] = """\
set -a; . /etc/default/cm-historian; set +a
if influx query --raw 'from(bucket: "{bucket}") |> range(start: -10m) |> limit(n: 1)' | grep -q '_value'; then
  echo "fresh-{bucket}"
fi
"""


#: The PLC's own status: sign in with the range credentials from the root-only
#: settings file (on stdin, not the command line) and read /api/status over the
#: self-signed TLS endpoint. Not the provisioning unit's state: a program that
#: stopped after provisioning fails this check.
OPENPLC_RUNNING: Final[str] = """\
set -a; . /etc/default/cm-openplc; set +a
base="https://127.0.0.1:${{OPENPLC_PORT:-{port}}}/api"
token=$(python3 -c 'import json, os; print(json.dumps({{"username": os.environ.get("OPENPLC_USER", "admin"), "password": os.environ["OPENPLC_PASSWORD"]}}))' \\
  | curl -ksS --max-time 10 -H 'Content-Type: application/json' --data-binary @- "${{base}}/login" \\
  | python3 -c 'import json, sys; print(json.load(sys.stdin).get("access_token", ""))' 2>/dev/null)
[ -n "${{token}}" ] || {{ echo "openplc-no-login"; exit 0; }}
printf 'Authorization: Bearer %s\\n' "${{token}}" | curl -ksS --max-time 10 -H @- "${{base}}/status" | grep -o 'STATUS:RUNNING' || echo "openplc-not-running"
"""

#: The FUXA project as FUXA holds it now: sign in with the range password and
#: check that every expected device is in GET /api/project.
FUXA_PROJECT: Final[str] = """\
set -a; . /etc/default/cm-fuxa; set +a
url="${{FUXA_URL:-http://127.0.0.1:{port}}}"
token=$(python3 -c 'import json, os; print(json.dumps({{"username": "admin", "password": os.environ["FUXA_ADMIN_PASSWORD"]}}))' \\
  | curl -sS --max-time 10 -H 'Content-Type: application/json' --data-binary @- "${{url}}/api/signin" \\
  | python3 -c 'import json, sys; print(json.load(sys.stdin).get("data", {{}}).get("token", ""))' 2>/dev/null)
[ -n "${{token}}" ] || {{ echo "fuxa-no-login"; exit 0; }}
printf 'x-access-token: %s\\n' "${{token}}" | curl -sS --max-time 10 -H @- "${{url}}/api/project" \\
  | python3 -c '
import json, os, sys
names = {{d.get("name") for d in json.load(sys.stdin).get("devices", {{}}).values()}}
want = set(os.environ.get("FUXA_EXPECT_DEVICES", "").split())
print("project-ok" if names and want <= names else "project-missing " + " ".join(sorted(want - names)))
'
"""


def _test(name: str, script: str, stdout: str) -> dict[str, str]:
    return {
        "name": name,
        "executor": "bash",
        "testScript": script,
        "testStdout": stdout,
    }


def _modbus_tests(ctx: Context, hostname: str, md: OpenPlcHost) -> list[dict[str, str]]:
    """A Modbus read from the PLC to each device its modbus_master polls."""
    program: str = md.program or hostname
    path = ctx.sources.plc_conf_dir(program) / "modbus_master.json"
    if not path.is_file():
        return []
    tests: list[dict[str, str]] = []
    for dev in json.loads(path.read_text(encoding="utf-8")):
        cfg: Any = dev.get("config") or {}
        point: Any = next(
            (p for p in cfg.get("io_points") or [] if p.get("fc") in READ_FCS), None
        )
        if point is None:
            continue
        script: str = MODBUS_READ.format(
            host=shlex.quote(str(cfg["host"])),
            port=int(cfg.get("port", 502)),
            unit=int(cfg.get("slave_id", 1)),
            fc=int(point["fc"]),
            offset=int(point["offset"]),
        )
        tests.append(_test(f"modbus-read-{dev['name']}", script, "modbus-ok"))
    return tests


def profile(ctx: Context, hostname: str, md: HostBase) -> dict[str, Any] | None:
    """The sohProfile for one host, or None when there is nothing to check."""
    processes: list[str] = []
    listeners: list[str] = []
    tests: list[dict[str, str]] = []

    if isinstance(md, OpenPlcHost):
        listeners.append(f":{md.rest_port}")
        nb: str = northbound(ctx, hostname, md)
        if nb == "modbus_slave":
            listeners.append(":502")
        elif nb == "opcua":
            listeners.append(f":{md.opcua.port}")
        else:
            listeners.append(f":{md.s7comm.port}")
        tests.append(
            _test(
                "openplc-program-running",
                OPENPLC_RUNNING.format(port=md.rest_port),
                "STATUS:RUNNING",
            )
        )
        tests += _modbus_tests(ctx, hostname, md)
    elif isinstance(md, FuxaHost):
        processes.append("/opt/fuxa/server/main.js")
        listeners.append(f":{md.port}")
        tests.append(
            _test(
                "fuxa-project-imported",
                FUXA_PROJECT.format(port=md.port),
                "project-ok",
            )
        )
    elif isinstance(md, RioHost):
        processes.append("ot-sim-cpu-module")
        planned: Any = ctx.sources.plant().hosts.get(hostname)
        modbus: Any = getattr(planned, "modbus", None)
        if modbus is not None:
            processes.append("ot-sim-modbus-module")
            listeners.append(f":{modbus.port}")
        bacnet: Any = bacnet_endpoint(ctx, hostname)
        if bacnet is not None:
            # The two modules this app adds; the listener is UDP, which the
            # soh app's port test covers (ss -lntu).
            processes += BACNET_PROCESSES
            listeners.append(f":{bacnet.port}")
    elif isinstance(md, SimCtlHost):
        processes.append("helics_broker")
        listeners.append(f":{ctx.app.mgmt.broker_ports[0]}")
        want: int = expected_federate_hosts(ctx)
        if want:
            tests.append(
                _test(
                    "helics-federates-connected",
                    FEDERATE_COUNT.format(want=want, port=ctx.app.mgmt.broker_ports[0]),
                    "federates-ok",
                )
            )
    elif isinstance(md, SimFederateHost):
        if ctx.federates_of(hostname, md):
            processes.append("cherrymountain_sim")
    elif isinstance(md, HistorianHost):
        h = ctx.app.historian
        listeners += [f":{h.influx_port}", f":{h.grafana_port}", f":{h.mqtt_port}"]
        tests.append(
            _test(
                "influxdb-provisioned",
                "systemctl is-active cm-historian-provision.service\n",
                "active",
            )
        )
        for fuxa_md in ctx.hosts_of(FuxaHost).values():
            if fuxa_daq(ctx, fuxa_md) == "influxdb":
                bucket: str = fuxa_md.bucket or fuxa_md.role
                tests.append(
                    _test(
                        f"influxdb-fresh-{bucket}",
                        BUCKET_FRESH.format(bucket=bucket),
                        f"fresh-{bucket}",
                    )
                )
    elif isinstance(md, HmiHost):
        processes.append(KIOSK_PROCESS)
    elif isinstance(md, GuacamoleHost):
        listeners.append(":443")
    elif isinstance(md, WazuhManagerHost):
        listeners += [":1514", ":1515"]
    elif isinstance(md, BbmdHost):
        processes.append("cm_bbmd.py")

    if not (processes or listeners or tests):
        return None
    out: dict[str, Any] = {}
    if processes:
        out["processes"] = processes
    if listeners:
        out["listeners"] = listeners
    if tests:
        out["customTests"] = tests
    return out


def io_module_hosts(plant: Any) -> set[str]:
    """The ot-sim devices whose I/O module is a HELICS federate. The phenix
    ot-sim app gives one to every fd-server with Modbus or DNP3 devices (the
    remote I/O and the field devices); this app gives one to every BACnet
    controller (``render_otsim``). Both annotate the node with
    ``helics/federate``."""
    return {
        name
        for name, h in plant.hosts.items()
        if h.federate
        and h.role in IO_MODULE_ROLES
        and (h.modbus is not None or getattr(h, "bacnet", None) is not None)
    }


def expected_federate_hosts(ctx: Context) -> int:
    """Hosts that run a HELICS federate: the sim nodes with federates, plus
    every ot-sim device with an I/O module (:func:`io_module_hosts`).

    ``FEDERATE_COUNT`` counts distinct addresses connected to the broker port,
    so sim-ctl counts as one when it hosts a federate itself (the weather
    federate in the full and lite variants): that federate reaches the broker
    on sim-ctl's own MGMT address, which is one more distinct peer. Leaving
    sim-ctl out made the check pass with one remote federate missing, and
    counting only the ``rio`` role left out the field devices and the BAS
    controllers."""
    sims: set[str] = {
        name
        for name, md in ctx.hosts.items()
        if isinstance(md, (SimCtlHost, SimFederateHost)) and ctx.federates_of(name, md)
    }
    return len(sims | io_module_hosts(ctx.sources.plant()))
