"""SCORCH component: drive the sim-ctl event API (DESIGN.md 9.5, 15.6).

Each stage the component is listed in posts its ``events``, in order, to the
event API on sim-ctl. The API's host port is random (the autotunnel spike),
so it is looked up at the start of every stage with ``cc tunnel list``; see
:mod:`cherrymountain_apps.simctl`.
"""

from __future__ import annotations

import json
import time
from typing import Annotated, Any, ClassVar, Literal

from phenix_apps.apps.scorch import ComponentBase
from phenix_apps.apps.scorch.app import Hostname, MetadataBlock, Name, Seconds
from phenix_apps.common.logger import logger
from pydantic import AnyHttpUrl, Field

from cherrymountain_apps import simctl

Port = Annotated[int, Field(ge=1, le=65535)]
Vessel = Annotated[str, Field(pattern=r"^(FV|BBT)-\d{3}$")]


class SimCtl(MetadataBlock):
    """Where the event API is."""

    hostname: Hostname = "sim-ctl"
    #: The API's port inside the VM; the host port is looked up per stage.
    port: Port = 8080
    #: Send to this URL instead of looking up the tunnel (a host-side relay).
    url: AnyHttpUrl | None = None
    #: Open a cc tunnel when none exists (phenix without the autotunnel loop).
    create_tunnel: bool = True
    timeout: Seconds = 10.0


class StartBatch(MetadataBlock):
    """Start a brew on the brewhouse."""

    event: Literal["start-batch"]
    recipe: Name = "fire-of-the-phenix"
    batch_id: Name | None = None


class UtilityBreaker(MetadataBlock):
    """Open (utility outage) or close the utility main breaker."""

    event: Literal["utility-breaker"]
    action: Literal["open", "close"]


class ChillerFault(MetadataBlock):
    """Fail or clear a glycol chiller compressor."""

    event: Literal["chiller-fault"]
    compressor: Annotated[int, Field(ge=1, le=8)] = 1
    action: Literal["fail", "clear"] = "fail"


class Co2Release(MetadataBlock):
    """Release CO2 into the cellar from a vessel."""

    event: Literal["co2-release"]
    fv: Vessel
    rate: Literal["low", "medium", "high"] = "high"
    duration: Seconds = 600.0


class Jam(MetadataBlock):
    """Jam a packaging machine."""

    event: Literal["jam"]
    machine: Literal[
        "depalletizer", "rinser", "filler", "capper", "labeler", "packer"
    ] = "labeler"
    duration: Seconds = 300.0


class WeatherStep(MetadataBlock):
    """Step the outside temperature."""

    event: Literal["weather-step"]
    delta_f: Annotated[float, Field(ge=-60, le=60, allow_inf_nan=False)]
    #: Seconds until the weather player returns to its data; 0 holds the step.
    duration: Seconds = 0.0


Event = Annotated[
    StartBatch | UtilityBreaker | ChillerFault | Co2Release | Jam | WeatherStep,
    Field(discriminator="event"),
]


class PlantEventMetadata(MetadataBlock):
    """Metadata for the plantevent component; see README.md."""

    sim_ctl: SimCtl = Field(default_factory=SimCtl)
    events: Annotated[list[Event], Field(min_length=1)]
    #: Seconds to wait between two events.
    interval: Seconds = 0.0
    #: Check GET /api/v1/health before the first event.
    check_health: bool = True


class PlantEvent(ComponentBase):
    """Posts operational events to the sim-ctl event API."""

    metadata_model: ClassVar[type[PlantEventMetadata]] = PlantEventMetadata
    config: PlantEventMetadata

    def __init__(self) -> None:
        ComponentBase.__init__(self, "plantevent")
        self.execute_stage()

    def configure(self) -> str:
        return self._run("configure")

    def start(self) -> str:
        return self._run("start")

    def stop(self) -> str:
        return self._run("stop")

    def cleanup(self) -> str:
        return self._run("cleanup")

    def _endpoint(self) -> str:
        target: SimCtl = self.config.sim_ctl
        if target.url is not None:
            return str(target.url)
        endpoint: simctl.Endpoint = simctl.resolve(
            self.mm, target.hostname, target.port, create=target.create_tunnel
        )
        logger.info(
            f"event API on {target.hostname}:{target.port} is at host port {endpoint.port} "
            f"of {endpoint.cluster_host or 'the headnode'}"
        )
        return endpoint.url

    def _run(self, stage: str) -> str:
        if self.dryrun:
            logger.info(f"dry run: not posting {len(self.config.events)} event(s)")
            return "dry run"
        api: simctl.EventApi = simctl.EventApi(
            self._endpoint(), self.config.sim_ctl.timeout
        )
        if self.config.check_health:
            api.health()
        record: list[dict[str, Any]] = []
        for index, event in enumerate(self.config.events):
            if index and self.config.interval:
                time.sleep(self.config.interval)
            body: dict[str, Any] = event.model_dump(
                mode="json", exclude_none=True, exclude=set(event.model_extra or {})
            )
            logger.info(
                f"posting event {index + 1}/{len(self.config.events)}: {event.event}"
            )
            reply: Any = api.post_event(body)
            record.append(
                {
                    "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "request": body,
                    "reply": reply,
                }
            )
        out = self.base_dir / f"plantevent_{stage}.json"
        out.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        logger.info(f"posted {len(record)} event(s); record in {out}")
        return f"posted {len(record)} event(s)"


def main() -> None:
    PlantEvent()


if __name__ == "__main__":
    main()
