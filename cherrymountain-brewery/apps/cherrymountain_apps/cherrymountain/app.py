"""phenix-app-cherrymountain: provisioning for the CherryMountain Brewery
topology (DESIGN.md 16).

Stages:

* configure: validate the metadata and plant.yaml against the topology,
  annotate the sim-* federate nodes with ``helics/federate``, and write each
  host's ``sohProfile``.
* pre-start: render the per-host files and inject them.
* post-start, running: check provisioning on the VMs and read the plant KPIs
  into ``status.apps.cherrymountain``.
* cleanup: export FUXA alarm history and DAQ data and an InfluxDB backup to
  the experiment's Files directory.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, ClassVar, Final

from box import Box
from phenix_apps.apps import AppBase
from phenix_apps.common import settings, utils
from phenix_apps.common.error import AppError
from phenix_apps.common.logger import logger
from pydantic import BaseModel

from cherrymountain_apps.cherrymountain import kpi, soh
from cherrymountain_apps.cherrymountain.credentials import RangeSecrets, resolve
from cherrymountain_apps.cherrymountain.metadata import (
    AppMetadata,
    FuxaHost,
    HistorianHost,
    HostBase,
    OpenPlcHost,
    RioHost,
    SimCtlHost,
    SimFederateHost,
)
from cherrymountain_apps.cherrymountain.render import (
    OTSIM_CONFIG,
    Context,
    Injection,
    helics_annotation,
    rekey_wireguard,
    bacnet_hosts,
    render_all,
    render_otsim,
)
from cherrymountain_apps.cherrymountain.sources import SourceError, Sources
from cherrymountain_apps.cherrymountain.validation import (
    Problem,
    check_references,
    check_topology,
    enforce,
    parse,
)

#: Where the cleanup exports land inside a VM before they are copied out.
EXPORT_DIR: Final[str] = "/tmp/cm-export"


class HostCheck(BaseModel):
    """One host's provisioning state, as post-start and running saw it."""

    state: str
    detail: str = ""


class CherryMountain(AppBase):
    """The cherrymountain phenix user app."""

    #: Provisioning units by host type, checked with ``systemctl is-active``.
    UNITS: ClassVar[dict[type[HostBase], str]] = {
        OpenPlcHost: "cm-openplc-provision.service",
        FuxaHost: "cm-fuxa-provision.service",
        HistorianHost: "cm-historian-provision.service",
    }

    def __init__(self, name: str, stage: str, dryrun: bool = False) -> None:
        super().__init__(name, stage, dryrun)
        self.out_dir: Path = Path(self.exp_dir) / "cherrymountain"
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.config: AppMetadata | None = None
        self.hosts_md: dict[str, HostBase] = {}
        self.sources: Sources | None = None

    # ------------------------------------------------------------ helpers

    def _nodes(self) -> dict[str, Any]:
        return {
            n.general.hostname: n for n in self.experiment.spec.topology.nodes or []
        }

    def _validate(self) -> tuple[AppMetadata, Sources]:
        """Parse and check everything; raise AppError on a fatal problem."""
        problems: list[Problem] = []
        raw_hosts: list[Any] = [
            h.to_dict() if isinstance(h, Box) else h
            for h in self.app.get("hosts") or []
        ]
        raw_md: Any = (
            self.metadata.to_dict() if isinstance(self.metadata, Box) else self.metadata
        )
        parsed = parse(raw_md, raw_hosts, problems)
        nodes: dict[str, Any] = self._nodes()
        sources: Sources | None = None
        if parsed.app is not None:
            sources = Sources(Path(parsed.app.source_dir), parsed.app.variant)
            try:
                plant: Any = sources.plant()
            except SourceError as ex:
                problems.append(
                    Problem(hostname=None, field="metadata.sourceDir", message=str(ex))
                )
            else:
                check_topology(plant, parsed.hosts, nodes, problems)
            check_references(parsed.app, parsed.hosts, nodes, problems)
        enforce(problems)
        if parsed.app is None or sources is None:  # enforce raised already
            raise AppError(f"{self.name} app: metadata did not validate")
        self.config, self.hosts_md, self.sources = parsed.app, parsed.hosts, sources
        return parsed.app, sources

    def _creds(self, config: AppMetadata) -> RangeSecrets:
        return resolve(config.secrets, self.out_dir / "secrets.json")

    def _context(self, config: AppMetadata, sources: Sources) -> Context:
        return Context(
            config,
            self.hosts_md,
            sources,
            self._creds(config),
            self.out_dir,
            self._nodes(),
        )

    def _annotate_federates(self, ctx: Context) -> None:
        for name, md in self.hosts_md.items():
            if isinstance(md, (SimCtlHost, SimFederateHost)):
                count: int = len(ctx.federates_of(name, md))
                if count:
                    self.add_annotation(
                        name, "helics/federate", helics_annotation(ctx, count)
                    )
        # The ot-sim app annotates the devices it gives an I/O module. It gives
        # a BACnet controller none, so this app adds the module (pre-start) and
        # the annotation the helics app counts the federates from.
        for name in bacnet_hosts(ctx):
            self.add_annotation(name, "helics/federate", helics_annotation(ctx, 1))

    def _rekey_wireguard(self, ctx: Context) -> None:
        """Per-experiment keys for the scenario's ``wireguard`` app (DESIGN.md
        13). The app runs after this one, so it renders these keys."""
        if ctx.app.wireguard_keys != "generate":
            return
        scenario: Any = (self.experiment.get("spec") or {}).get("scenario") or {}
        for app in scenario.get("apps") or []:
            if app.get("name") == "wireguard":
                count: int = rekey_wireguard(ctx, list(app.get("hosts") or []))
                if count:
                    logger.info(f"{self.name}: generated WireGuard keys for {count} host(s)")

    def put_inject(self, hostname: str, inject: Injection) -> None:
        """Add an injection, replacing any other one to the same destination,
        so a rendered file wins over a static topology injection."""
        node: Any = self.require_node(hostname)
        kept: list[Any] = [
            i for i in node.get("injections") or [] if i.get("dst") != inject.dst
        ]
        if len(kept) != len(node.get("injections") or []):
            logger.debug(
                f"{hostname}: replacing the topology injection to {inject.dst}"
            )
        node["injections"] = [*kept, inject.as_phenix()]

    def _set_status(self, values: dict[str, Any]) -> None:
        status: Any = self.experiment.get("status")
        if not status:
            self.experiment["status"] = {}
            status = self.experiment["status"]
        apps: Any = status.get("apps")
        if not apps:
            status["apps"] = {}
            apps = status["apps"]
        current: Any = apps.get(self.name) or {}
        merged: dict[str, Any] = {
            **(current.to_dict() if isinstance(current, Box) else current),
            **values,
        }
        apps[self.name] = merged

    def _exec(self, vm: str, cmd: str, timeout: float, grace: float) -> dict[str, Any]:
        return dict(
            utils.mm_exec_wait(self.mm, vm, cmd, timeout=timeout, client_grace=grace)
        )

    # ------------------------------------------------------------ stages

    def configure(self) -> None:
        logger.info(f"Configuring user application: {self.name}")
        config, sources = self._validate()
        ctx: Context = self._context(config, sources)
        self._annotate_federates(ctx)
        self._rekey_wireguard(ctx)
        for host in self.app.get("hosts") or []:
            md: HostBase | None = self.hosts_md.get(host.hostname)
            if md is None:
                continue
            profile: dict[str, Any] | None = soh.profile(ctx, host.hostname, md)
            if profile is None:
                host.metadata.pop("sohProfile", None)
            else:
                host.metadata["sohProfile"] = profile
        logger.info(f"Configured user application: {self.name}")

    def pre_start(self) -> None:
        logger.info(f"Starting user application: {self.name}")
        config, sources = self._validate()
        ctx: Context = self._context(config, sources)
        self._annotate_federates(ctx)
        try:
            plan: dict[str, list[Injection]] = render_all(ctx)
        except SourceError as ex:
            raise AppError(f"{self.name} app: {ex}") from None
        count: int = 0
        for hostname, injects in plan.items():
            for inject in injects:
                self.put_inject(hostname, inject)
                count += 1
        # The ot-sim app ran before this one, so its config.xml injections exist:
        # add the modules it cannot render (telnet; I/O and BACnet for a BAS
        # controller) where a device needs them.
        for hostname, md in self.hosts_md.items():
            if isinstance(md, RioHost):
                node: Any = self.require_node(hostname)
                rendered: str | None = next(
                    (str(i.get("src")) for i in node.get("injections") or [] if i.get("dst") == OTSIM_CONFIG),
                    None,
                )
                try:
                    extras: list[Injection] = render_otsim(ctx, hostname, md, rendered)
                except SourceError as ex:
                    raise AppError(f"{self.name} app: {hostname}: {ex}") from None
                for inject in extras:
                    self.put_inject(hostname, inject)
                    plan.setdefault(hostname, []).append(inject)
                    count += 1
        manifest: dict[str, list[str]] = {
            h: [i.dst for i in injects] for h, injects in sorted(plan.items())
        }
        (self.out_dir / "injections.json").write_text(
            Box(manifest).to_json(indent=2) + "\n", encoding="utf-8"
        )
        logger.info(
            f"Started user application: {self.name} ({count} injections on {len(plan)} hosts)"
        )

    def post_start(self) -> None:
        self._observe("post-start")

    def running(self) -> None:
        self._observe("running")

    def _observe(self, stage: str) -> None:
        config, _ = self._validate()
        if self.dryrun:
            self._set_status(
                {"stage": stage, "provisioning": {}, "kpis": None, "dryRun": True}
            )
            return
        checks: dict[str, HostCheck] = {
            name: self._check_host(name, md, config)
            for name, md in self.hosts_md.items()
        }
        kpis: kpi.Kpis = kpi.read(
            self.mm,
            config.sim_ctl.hostname,
            config.sim_ctl.event_port,
            config.sim_ctl.timeout,
        )
        done: int = sum(1 for c in checks.values() if c.state == "provisioned")
        self._set_status(
            {
                "stage": stage,
                "updated": utils.utc_now().isoformat(),
                "provisioning": {
                    name: c.model_dump(exclude_defaults=True)
                    for name, c in sorted(checks.items())
                },
                "provisioned": f"{done}/{len(checks)}",
                "kpis": kpis.model_dump(),
            }
        )
        logger.info(
            f"{self.name}: {done} of {len(checks)} hosts provisioned; KPIs available: {kpis.available}"
        )

    def _check_host(self, name: str, md: HostBase, config: AppMetadata) -> HostCheck:
        units: list[str] = []
        #: unit -> the older unit that may run in its place. A topology that
        #: still injects cm-<federate>.service (the ci slice) runs that one, and
        #: 60-cm-federates.sh then leaves cm-federate@<federate> alone.
        legacy: dict[str, str] = {}
        for kind, unit in self.UNITS.items():
            if isinstance(md, kind):
                units.append(unit)
        if isinstance(md, (SimCtlHost, SimFederateHost)) and self.sources is not None:
            ctx_feds: list[str] = (
                md.federates
                if md.federates is not None
                else [
                    f
                    for f, spec in self.sources.plant().federates.items()
                    if spec.host == name
                ]
            )
            for fed in ctx_feds:
                units.append(f"cm-federate@{fed}.service")
                legacy[units[-1]] = f"cm-{fed}.service"
        if not units:
            return HostCheck(state="not-checked")
        asked: list[str] = [*units, *legacy.values()]
        try:
            resp: dict[str, Any] = self._exec(
                name,
                "systemctl is-active " + " ".join(asked) + " || true",
                config.verify.timeout,
                config.verify.client_grace,
            )
        except Exception as ex:
            return HostCheck(state="unreachable", detail=type(ex).__name__)
        answers: list[str] = [
            s.strip() for s in str(resp.get("stdout", "")).splitlines() if s.strip()
        ]
        states: list[str] = answers[: len(units)]
        if len(answers) == len(asked):
            # systemctl prints one state per unit, in order: a federate counts
            # as its legacy unit's state when only that one runs (or failed).
            seen: dict[str, str] = dict(zip(asked, answers, strict=True))
            states = [
                seen[legacy[u]]
                if u in legacy and s != "active" and seen[legacy[u]] in ("active", "failed")
                else s
                for u, s in zip(units, states, strict=True)
            ]
        if len(states) == len(units) and all(s == "active" for s in states):
            return HostCheck(state="provisioned")
        if any(s == "failed" for s in states):
            return HostCheck(
                state="failed",
                detail=", ".join(
                    u for u, s in zip(units, states, strict=False) if s == "failed"
                ),
            )
        return HostCheck(
            state="pending",
            detail=", ".join(f"{u}={s}" for u, s in zip(units, states, strict=False)),
        )

    def cleanup(self) -> None:
        logger.info(f"Cleaning up user application: {self.name}")
        config, _ = self._validate()
        if not config.export_on_cleanup or self.dryrun:
            return
        files: Path = Path(
            os.getenv("PHENIX_FILES_DIR")
            or Path(settings.PHENIX_DIR) / "images" / self.exp_name / "files"
        )
        dest_dir: Path = files / "cherrymountain"
        exports: dict[str, str] = {}
        for name, md in sorted(self.hosts_md.items()):
            if isinstance(md, FuxaHost):
                exports[name] = self._export(
                    name, "cm-export-fuxa", "fuxa-export.tar.gz", dest_dir
                )
            elif isinstance(md, HistorianHost):
                exports[name] = self._export(
                    name, "cm-export-historian", "influx-backup.tar.gz", dest_dir
                )
        self._set_status({"exports": exports})
        logger.info(f"Cleaned up user application: {self.name}")

    def _export(self, vm: str, script: str, artifact: str, dest_dir: Path) -> str:
        """Run an export script on ``vm`` and copy its artifact to Files.

        A failure is logged and recorded, not raised: cleanup goes on for the
        other hosts and apps.
        """
        dest: Path = dest_dir / f"{vm}-{artifact}"
        try:
            resp: dict[str, Any] = self._exec(
                vm, f"/usr/local/bin/{script} {EXPORT_DIR}", 600.0, 30.0
            )
            if resp.get("exitcode") not in (0, None):
                raise RuntimeError(f"{script} exited {resp.get('exitcode')}")
            dest_dir.mkdir(parents=True, exist_ok=True)
            utils.mm_recv(self.mm, vm, f"{EXPORT_DIR}/{artifact}", str(dest))
        except Exception as ex:
            logger.warning(
                f"{self.name}: export from {vm} failed: {type(ex).__name__}: {ex}"
            )
            return f"failed: {type(ex).__name__}"
        logger.info(f"{self.name}: exported {artifact} from {vm} to {dest}")
        return str(dest)
