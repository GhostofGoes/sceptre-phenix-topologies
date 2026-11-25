"""``options:df_default=false`` on the GRE ports of the mirror (DESIGN.md 14).

The full variant mirrors the plant's VLANs to Malcolm with phenix's ``mirror``
app, which adds one GRE tunnel port per mirror target to the Open vSwitch bridge
of every cluster host. A mirrored frame that fills the path MTU grows past it in
the tunnel, and Open vSwitch sets DF on the outer header by default, so full-size
frames are dropped; ``options:df_default=false`` on the port lets them fragment.
The mirror app does not set it. This app does, in its post-start stage, for the
ports of this experiment's mirror only (not every GRE port on the host, as the
manual one-liner did).

What the mirror app records (phenix-apps ``cmd/phenix-app-mirror``, ``types.go``
``MirrorAppStatus`` and ``MirrorConfig``, stored through phenix's ``SetAppStatus``,
which keeps their ``structs`` tags as the JSON keys; phenix's e2e test reads the
same ones)::

    status.apps.mirror = {
        "tapName": "<8 characters>-mirror",
        "subnet": "172.30.0.0/16",
        "mirrors": {"<target hostname>": {"mirrorName": "<15 characters>",
                                          "mirrorBridge": "<bridge>",
                                          "ip": "<target address>"}},
    }

``mirrorName`` is the tunnel port's name (``setupTunnelAndMirror`` and
``createGRETunnel``: ``ovs-vsctl add-port <bridge> <name> -- set interface <name>
type=gre options:remote_ip=<ip>``) and also the OVS mirror's name. The app adds the
port on every host of ``status.schedules`` (``cluster``), through minimega's
``shell`` (``mm.MeshShell``: ``shell <command>`` on the headnode, ``mesh send
<host> shell <command>`` on another host). minimega's ``shell`` runs the command
without a shell, so ``ovs-vsctl`` runs in the minimega container, where Open
vSwitch lives.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Annotated, Any, ClassVar, Final, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

#: An Open vSwitch port or bridge name: one token of at most 15 characters
#: (IFNAMSIZ), safe to splice into an ``ovs-vsctl`` command.
OvsName = Annotated[str, Field(pattern=r"^[A-Za-z0-9_.-]{1,15}$")]

#: Seconds ``ovs-vsctl`` waits for the database: by default it waits forever, and
#: a command that never returns would hold up the experiment's start.
OVS_TIMEOUT: Final[int] = 10
#: A host's result when the option is set.
SET: Final[str] = "set"
#: The longest minimega error kept in the status.
ERROR_LIMIT: Final[int] = 160

#: Runs ``command`` through minimega's ``shell`` on cluster host ``host`` and
#: returns minimega's replies (one per host that answered).
Shell = Callable[[str, str], list[dict[str, Any]]]


class _Camel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="ignore",
        alias_generator=to_camel,
        validate_by_alias=True,
        validate_by_name=True,
    )


class MirrorTarget(_Camel):
    """One entry of ``status.apps.mirror.mirrors``."""

    mirror_name: OvsName
    mirror_bridge: OvsName
    ip: str = ""


class MirrorStatus(_Camel):
    """``status.apps.mirror``."""

    mirrors: dict[str, MirrorTarget] = Field(default_factory=dict)


class PortRecord(_Camel):
    """One mirror port in the status: where the option was set, by host."""

    target: str
    bridge: str
    #: cluster host -> ``set`` or ``failed: <reason>``.
    hosts: dict[str, str] = Field(default_factory=dict)


class MirrorRecord(_Camel):
    """What this app records under ``status.apps.cherrymountain.mirror``."""

    #: ``set``: every port on every cluster host. ``incomplete``: some host failed
    #: (``running`` tries those again). ``waiting``: the mirror app has recorded no
    #: ports yet. ``skipped``: nothing to do for this mirror.
    df_default: Literal["set", "incomplete", "waiting", "skipped"]
    reason: str = ""
    updated: str = ""
    #: Port name -> its result.
    ports: dict[str, PortRecord] = Field(default_factory=dict)
    #: A failure of the step itself (not of one host).
    error: str = ""


def uses_erspan(metadata: Mapping[str, Any]) -> bool:
    """Whether the mirror app's metadata asks for ERSPAN tunnels instead of GRE
    (``erspan.enabled``, or ``directGRE.erspan.enabled`` in the older shape)."""
    for block in (metadata, metadata.get("directGRE") or {}):
        erspan: Any = block.get("erspan") if isinstance(block, Mapping) else None
        if isinstance(erspan, Mapping) and erspan.get("enabled"):
            return True
    return False


def cluster_hosts(schedules: Mapping[str, str]) -> list[str]:
    """The cluster hosts that run VMs of the experiment, as the mirror app
    counts them (``cluster``: the values of ``status.schedules``)."""
    return sorted({str(host) for host in schedules.values() if host})


def command(port: str) -> str:
    """The ``ovs-vsctl`` call, for minimega's ``shell`` (no shell syntax)."""
    return f"ovs-vsctl --timeout={OVS_TIMEOUT} set interface {port} options:df_default=false"


def set_on_host(shell: Shell, host: str, port: str) -> str:
    """Set the option on ``port`` of ``host``: ``set``, or ``failed: <reason>``.

    A reply that holds an error is a failure: ``ovs-vsctl set`` prints nothing
    when it succeeds, and minimega reports a nonzero exit as the error. No reply
    at all means minimega has no route to the host. Nothing is raised."""
    try:
        replies: list[dict[str, Any]] = shell(host, command(port))
    except Exception as ex:
        return f"failed: {type(ex).__name__}"
    if not replies:
        return "failed: no reply from minimega"
    errors: list[str] = [
        str(reply.get("Error") or "").strip()
        for reply in replies
        if isinstance(reply, dict)
    ]
    first: str = next((e for e in errors if e), "")
    if first:
        return "failed: " + first[:ERROR_LIMIT]
    return SET


def apply(
    shell: Shell,
    status: MirrorStatus,
    hosts: list[str],
    previous: MirrorRecord | None,
    now: str,
) -> MirrorRecord:
    """Set ``options:df_default=false`` on every port of ``status`` on every host.

    Safe to repeat: setting an option to the value it has changes nothing in
    Open vSwitch, and a port that ``previous`` records as set on a host is not
    sent again. A mirror app that ran again made new ports (new random names), so
    those are set."""
    if not status.mirrors:
        return MirrorRecord(
            df_default="skipped",
            reason="the mirror app recorded no target",
            updated=now,
        )
    if not hosts:
        return MirrorRecord(
            df_default="waiting",
            reason="status.schedules lists no cluster host",
            updated=now,
        )
    ports: dict[str, PortRecord] = {}
    for target, cfg in sorted(status.mirrors.items()):
        done: dict[str, str] = (
            previous.ports[cfg.mirror_name].hosts
            if previous is not None and cfg.mirror_name in previous.ports
            else {}
        )
        ports[cfg.mirror_name] = PortRecord(
            target=target,
            bridge=cfg.mirror_bridge,
            hosts={
                host: SET
                if done.get(host) == SET
                else set_on_host(shell, host, cfg.mirror_name)
                for host in hosts
            },
        )
    everywhere: bool = all(
        result == SET for port in ports.values() for result in port.hosts.values()
    )
    return MirrorRecord(
        df_default="set" if everywhere else "incomplete", updated=now, ports=ports
    )
