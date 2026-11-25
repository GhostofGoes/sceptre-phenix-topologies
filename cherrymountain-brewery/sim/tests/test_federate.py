"""Cellar federate logic against plantgen's generated HELICS lists."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from cherrymountain_sim.federate import CellarFederate, HelicsConfig

HELICS_JSON: Path = (
    Path(__file__).resolve().parents[2] / "plant" / "generated" / "ci" / "helics.json"
)


@pytest.fixture
def config() -> HelicsConfig:
    """The committed ci variant HELICS config."""
    return HelicsConfig.load(HELICS_JSON)


@pytest.fixture
def fed(config: HelicsConfig) -> CellarFederate:
    """A cellar federate for the ci variant."""
    return CellarFederate(config.federates["cellar"])


def test_helics_not_needed_for_logic(fed: CellarFederate) -> None:
    """The federate logic imports without pyhelics (it is imported lazily)."""
    assert "cherrymountain_sim.federate" in sys.modules
    assert set(fed.cellar.fermenters) == {"FV101", "FV102", "FV103", "FV104"}


def test_publishes_every_listed_key(fed: CellarFederate, config: HelicsConfig) -> None:
    """Values exist for every key rio-cellar subscribes to."""
    values = fed.values()
    raw = json.loads(HELICS_JSON.read_text(encoding="utf-8"))
    for key in raw["io_federates"]["rio-cellar"]["subscriptions"]:
        assert key in values
    assert "cellar/co2.vent_gps" in values and "cellar/clock.sim_hours" in values
    for pub in config.federates["cellar"].publications:
        v = values[pub.key]
        assert isinstance(v, bool) if pub.type == "boolean" else v >= 0.0


def test_fv101_temperature_changes(fed: CellarFederate) -> None:
    """The e2e check needs a changing CEL.FV101.TT01.PV (2-decimal register)."""
    key = "cellar/fv101-side.temp_f"
    seen = set()
    for _ in range(30):
        fed.step(1.0)
        seen.add(round(float(fed.values()[key]), 2))
    assert len(seen) > 5
    assert all(60.0 < v < 72.0 for v in seen)


def test_writes_through_updates_endpoint(fed: CellarFederate) -> None:
    """ot-sim writes arrive as JSON lists; read-only points are rejected."""
    msg = json.dumps(
        [
            {"tag": "fv101-side.valve", "value": True},
            {"tag": "fv101.spund_sp_psia", "value": 24.1},
            {"tag": "fv101-side.temp_f", "value": 10.0},
        ]
    )
    assert fed.handle_message(msg) == ["fv101-side.valve", "fv101.spund_sp_psia"]
    values = fed.values()
    assert values["cellar/fv101-side.valve"] is True
    assert values["cellar/fv101-cone.valve"] is False
    assert values["cellar/fv101.spund_sp_psia"] == pytest.approx(24.1)
    assert fed.handle_message("not json") == []


def test_open_valve_cools_zone(fed: CellarFederate) -> None:
    """Commanding the side glycol valve pulls the side zone down."""
    t0 = fed.cellar.fermenters["FV101"].zone_temp_f("side")
    fed.handle_message(json.dumps([{"tag": "fv101-side.valve", "value": True}]))
    for _ in range(600):
        fed.step(1.0)
    assert fed.cellar.fermenters["FV101"].zone_temp_f("side") < t0 - 0.5
    assert fed.cellar.glycol_load_kw > 5.0


def test_input_defaults_and_updates(fed: CellarFederate) -> None:
    """Absent federates fall back to defaults; subscriptions update the env."""
    env = fed.cellar.env
    assert env.glycol_supply_f == 28.0 and env.ambient_f == 68.0
    assert env.ambient_psia == pytest.approx(12.1, abs=0.01)  # 83.4 kPa
    fed.apply_input("utilities/glycol.supply_f", 34.0)
    assert env.glycol_supply_f == 34.0


def test_initial_state_from_config(fed: CellarFederate) -> None:
    """FV-103 is dry hopped mid-fermentation; FV-104 is near terminal."""
    fvs = fed.cellar.fermenters
    assert fvs["FV103"].state is not None and fvs["FV103"].state.hop_extract > 0
    assert fvs["FV104"].gravity_plato < 4.0
    assert fvs["FV101"].gravity_plato > fvs["FV102"].gravity_plato
