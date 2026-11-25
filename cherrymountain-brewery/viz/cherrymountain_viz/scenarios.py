"""Scripted scenarios for the offline harness (development and screenshots).

Each step happens at a federate time. ``event`` is what the sim-ctl event API
would forward (the SCORCH ``plantevent`` component sends the same), ``write``
is a protocol write as ot-sim would deliver it to the federate (here, what a
compromised host could send), and ``freeze-hmi`` stands in for a replay on the
supervisory path: the operator view keeps the values of that moment.

- ``none``: normal running.
- ``co2-leak``: a 40 g/s release at FV-102 (the SCORCH ``cellar-co2-leak`` run).
- ``co2-attack``: the same release, then a BACnet write from a compromised
  bas-fuxa that stops both purge fans, and the HMI frozen at that moment.
- ``spunding-attack``: a rogue Modbus master on FIELD-CEL raises FV-102's
  spunding setpoint to 30 psig, above the 15 psig MAWP, so its relief valve
  lifts. It rewrites every minute and holds both carbonation valves shut, as
  the PLC it displaces would: the cellar federate follows written values once
  writes arrive (the autopilot hand-off, DESIGN.md §9.6).
- ``outage``: the utility breaker opens (``utility-breaker``), the genset
  starts and the ATS moves EP-1.
"""

from __future__ import annotations

from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

#: Site ambient, psia (DESIGN.md §3.1): setpoints are written as absolute.
SITE_PSIA: float = 12.1


class Step(BaseModel):
    """One timed action against the offline federation."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    at_s: float
    kind: Literal["event", "write", "freeze-hmi"]
    federate: str = ""
    payload: dict[str, Any] | list[dict[str, Any]] = Field(default_factory=dict)
    note: str
    #: Repeat the step this often (s), as an attacker keeps writing; the
    #: cellar's autopilot takes control back 300 s after the last write.
    every_s: float | None = None


LEAK: Step = Step(
    at_s=60.0,
    kind="event",
    federate="cellar",
    payload={"type": "co2-release", "params": {"fv": "FV-102", "rate": "high", "duration": 3600}},
    note="co2-release FV-102 rate=high (40 g/s)",
)
FANS_OFF: Step = Step(
    at_s=90.0,
    kind="write",
    federate="building",
    payload=[{"tag": f"bas-vent.purge_fan_{i}", "value": False} for i in (1, 2)],
    note="BACnet write from bas-fuxa: purge fans off",
)

SCENARIOS: dict[str, tuple[Step, ...]] = {
    "none": (),
    "co2-leak": (LEAK,),
    "co2-attack": (
        LEAK,
        FANS_OFF,
        Step(at_s=90.0, kind="freeze-hmi", note="HMI values frozen (replay)"),
    ),
    "spunding-attack": (
        Step(
            at_s=30.0,
            kind="write",
            federate="cellar",
            payload=[
                {"tag": "fv102.spund_sp_psia", "value": SITE_PSIA + 30.0},
                {"tag": "bbt201.co2_valve_pct", "value": 0.0},
                {"tag": "bbt202.co2_valve_pct", "value": 0.0},
            ],
            note="Modbus write: FV-102 spunding setpoint 30 psig (repeated each minute)",
            every_s=60.0,
        ),
    ),
    "outage": (
        Step(
            at_s=30.0,
            kind="event",
            federate="power",
            payload={"type": "utility-breaker", "params": {"action": "open"}},
            note="utility-breaker open (outage)",
        ),
    ),
}
