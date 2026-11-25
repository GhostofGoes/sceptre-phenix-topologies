"""The "Fire of the Phenix" recipe (DESIGN.md §3.2) and its BeerJSON file.

The recipe lives at ``recipes/fire-of-the-phenix.json`` as BeerJSON 1.0. This
module reads the parts the simulation uses (batch size, gravities, mash
steps, hop schedule, fermentation temperatures) and computes the bitterness
with the altitude derating, so the tests can show that the recipe meets its
68 IBU target on site and would overshoot it at sea level.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from cherrymountain_sim.brewing import (
    c_to_f,
    hop_stand_ibu,
    isomerization_rate_ratio,
    tinseth_ibu,
    wort_boiling_point_c,
)
from cherrymountain_sim.fermentation import plato_from_sg, sg_from_plato

#: recipes/fire-of-the-phenix.json from the topology directory.
RECIPE_PATH: Path = (
    Path(__file__).resolve().parents[2] / "recipes" / ("fire-of-the-phenix.json")
)
LITERS_PER_BBL: float = 117.348
SITE_KPA: float = 83.4
SEA_LEVEL_KPA: float = 101.325


class HopAddition(BaseModel):
    """One hop addition."""

    name: str
    alpha: float  # fraction, e.g. 0.15
    grams: float
    use: Literal["boil", "whirlpool", "dry-hop"]
    minutes: float = 0.0  # boil time remaining, or stand duration
    temp_c: float | None = None  # whirlpool stand temperature


class MashStep(BaseModel):
    """One mash step."""

    name: str
    temp_c: float
    minutes: float


class Recipe(BaseModel):
    """The simulation's view of a BeerJSON recipe."""

    name: str = "Fire of the Phenix"
    batch_l: float = 15 * LITERS_PER_BBL
    og_plato: float = 16.1
    fg_plato: float = 3.1
    ibu_target: float = 68.0
    boil_minutes: float = 60.0
    grain_kg: float = 503.0
    mash_ratio_l_kg: float = 2.6
    mash: list[MashStep] = Field(default_factory=list)
    hops: list[HopAddition] = Field(default_factory=list)
    pitch_temp_c: float = 17.8
    ferment_temp_c: float = 18.9

    @property
    def average_gravity(self) -> float:
        """Average kettle gravity during the boil (Tinseth's G).

        The pre-boil wort is diluted by the evaporation, so the average sits
        a little below the original gravity.
        """
        og = sg_from_plato(self.og_plato)
        pre = 1.0 + (og - 1.0) * 0.91
        return (og + pre) / 2.0

    def ibu(self, ambient_kpa: float = SITE_KPA, method: str = "hosom") -> float:
        """Recipe bitterness at an ambient pressure (Tinseth plus mIBU)."""
        g = self.average_gravity
        boil_c = wort_boiling_point_c(ambient_kpa, plato_from_sg(g))
        ratio = isomerization_rate_ratio(boil_c, method)  # type: ignore[arg-type]
        total = 0.0
        for h in self.hops:
            if h.use == "boil":
                total += tinseth_ibu(
                    h.alpha, h.grams, self.batch_l, g, h.minutes, 0.04 * ratio
                )
            elif h.use == "whirlpool":
                temps = [h.temp_c or 82.2] * round(h.minutes)
                total += hop_stand_ibu(h.alpha, h.grams, self.batch_l, g, temps)
        return total


def _value(node: Any, unit_key: str = "value") -> float:
    return float(node[unit_key])


def _to_c(node: dict[str, Any]) -> float:
    value = float(node["value"])
    return (value - 32.0) / 1.8 if node["unit"] == "F" else value


def _to_kg(node: dict[str, Any]) -> float:
    factors = {"kg": 1.0, "g": 0.001, "lb": 0.45359237, "oz": 0.028349523}
    return float(node["value"]) * factors[node["unit"]]


def _to_l(node: dict[str, Any]) -> float:
    factors = {"l": 1.0, "gal": 3.785411784, "bbl": LITERS_PER_BBL}
    return float(node["value"]) * factors[node["unit"]]


def _to_min(node: dict[str, Any]) -> float:
    factors = {"min": 1.0, "hr": 60.0, "day": 1440.0}
    return float(node["value"]) * factors[node["unit"]]


def load(path: Path = RECIPE_PATH) -> Recipe:
    """Read the first recipe of a BeerJSON file."""
    doc = json.loads(path.read_text(encoding="utf-8"))
    r = doc["beerjson"]["recipes"][0]
    ing = r["ingredients"]
    grain = sum(_to_kg(f["amount"]) for f in ing["fermentable_additions"])
    boil_steps = r["boil"].get("boil_steps", [])
    hops = []
    for h in ing["hop_additions"]:
        timing = h["timing"]
        use = "dry-hop" if timing["use"] == "add_to_fermentation" else "boil"
        temp = None
        # BeerJSON has no whirlpool use: an addition tied to a boil step named
        # "whirlpool" is a hop stand at that step's temperature.
        step = timing.get("step")
        if (
            use == "boil"
            and step
            and "whirlpool" in boil_steps[step - 1]["name"].lower()
        ):
            use = "whirlpool"
            temp = _to_c(boil_steps[step - 1]["start_temperature"])
        hops.append(
            HopAddition(
                name=h["name"],
                alpha=_value(h["alpha_acid"]) / 100.0,
                grams=_to_kg(h["amount"]) * 1000.0,
                use=use,  # type: ignore[arg-type]
                minutes=_to_min(timing["duration"]) if "duration" in timing else 0.0,
                temp_c=temp,
            )
        )
    steps = r["mash"]["mash_steps"]
    batch = _to_l(r["batch_size"])
    infusion = _to_l(steps[0]["amount"]) if "amount" in steps[0] else 2.6 * grain
    ferm = r["fermentation"]["fermentation_steps"]
    return Recipe(
        name=r["name"],
        batch_l=batch,
        og_plato=plato_from_sg(_value(r["original_gravity"])),
        fg_plato=plato_from_sg(_value(r["final_gravity"])),
        ibu_target=_value(r["ibu_estimate"]),
        boil_minutes=_to_min(r["boil"]["boil_time"]),
        grain_kg=grain,
        mash_ratio_l_kg=infusion / grain,
        mash=[
            MashStep(
                name=s["name"],
                temp_c=_to_c(s["step_temperature"]),
                minutes=_to_min(s["step_time"]),
            )
            for s in steps
        ],
        hops=hops,
        pitch_temp_c=_to_c(ferm[0]["start_temperature"]),
        ferment_temp_c=_to_c(ferm[0]["end_temperature"]),
    )


def default() -> Recipe:
    """The committed recipe, or the built-in copy when the file is absent."""
    if RECIPE_PATH.exists():
        return load(RECIPE_PATH)
    return Recipe(
        mash=[
            MashStep(name="Saccharification", temp_c=66.7, minutes=60),
            MashStep(name="Mash out", temp_c=75.6, minutes=10),
        ],
        hops=[
            HopAddition(
                name="Columbus", alpha=0.15, grams=2720, use="boil", minutes=60
            ),
            HopAddition(
                name="Centennial", alpha=0.10, grams=1360, use="boil", minutes=10
            ),
            HopAddition(
                name="Citra",
                alpha=0.12,
                grams=3400,
                use="whirlpool",
                minutes=20,
                temp_c=82.2,
            ),
            HopAddition(
                name="Simcoe",
                alpha=0.13,
                grams=3400,
                use="whirlpool",
                minutes=20,
                temp_c=82.2,
            ),
        ],
    )


def describe(recipe: Recipe) -> str:
    """One line for logs: name, IBU on site and at sea level."""
    return (
        f"{recipe.name}: {recipe.ibu():.1f} IBU on site "
        f"(boil {c_to_f(wort_boiling_point_c(SITE_KPA, recipe.og_plato)):.1f} degF), "
        f"{recipe.ibu(SEA_LEVEL_KPA):.1f} IBU at sea level"
    )
