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
from cherrymountain_apps.cherrymountain.render import Context, fuxa_daq, northbound

#: Modbus function codes a read test may use (coils, discrete, holding, input).
READ_FCS: Final[frozenset[int]] = frozenset({1, 2, 3, 4})

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
                "test -f /var/lib/cm-openplc/provisioned && systemctl is-active cm-openplc-provision.service\n",
                "active",
            )
        )
        tests += _modbus_tests(ctx, hostname, md)
    elif isinstance(md, FuxaHost):
        processes.append("/opt/fuxa/server/main.js")
        listeners.append(f":{md.port}")
        tests.append(
            _test(
                "fuxa-project-imported",
                "systemctl is-active cm-fuxa-provision.service\n",
                "active",
            )
        )
    elif isinstance(md, RioHost):
        processes.append("ot-sim-cpu-module")
        planned: Any = ctx.sources.plant().hosts.get(hostname)
        modbus: Any = getattr(planned, "modbus", None)
        if modbus is not None:
            processes.append("ot-sim-modbus-module")
            listeners.append(f":{modbus.port}")
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
        processes.append("cm-kiosk")
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


def expected_federate_hosts(ctx: Context) -> int:
    """Hosts that run a HELICS federate: the sim nodes with federates, plus
    every remote I/O device that plant.yaml backs with a federate."""
    plant: Any = ctx.sources.plant()
    sims: set[str] = {
        name
        for name, md in ctx.hosts.items()
        if isinstance(md, (SimCtlHost, SimFederateHost))
        and ctx.federates_of(name, md)
        and name != ctx.app.sim_ctl.hostname
    }
    rios: set[str] = {
        name for name, h in plant.hosts.items() if h.role == "rio" and h.federate
    }
    return len(sims | rios)
