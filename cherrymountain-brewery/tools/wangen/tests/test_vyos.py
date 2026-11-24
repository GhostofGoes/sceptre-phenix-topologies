"""The configuration lists equal those of the wan-bgp spike, which VyOS routers committed in CI."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from wangen import model, vyos

SPIKE: Path = Path(__file__).resolve().parents[3] / "spikes" / "wan-bgp"
ROUTERS: list[str] = [
    "cb-west",
    "cb-south",
    "shadecast",
    "tumbleweed",
    "marmot",
    "bristlecone",
    "fr-fw-edge",
    "fc-fw-edge",
    "ep-fw-edge",
    "cs-fw-edge",
]


@pytest.fixture
def spike():  # noqa: ANN201
    """The spike's sketch module, with its own model."""
    sys.path.insert(0, str(SPIKE))
    try:
        import vyos_sketch
    finally:
        sys.path.remove(str(SPIKE))
    before = vyos_sketch.HUB_OPENS
    yield vyos_sketch
    vyos_sketch.HUB_OPENS = before


@pytest.fixture(autouse=True)
def _loaded():  # noqa: ANN202
    model.load()
    yield
    model.load()


@pytest.mark.parametrize("name", ROUTERS)
@pytest.mark.parametrize("kind", [model.WIREGUARD, model.VTI])
@pytest.mark.parametrize("hub_opens", [False, True])
def test_the_list_equals_the_spikes(spike, name: str, kind: str, hub_opens: bool) -> None:  # noqa: ANN001
    spike.HUB_OPENS = hub_opens
    model.HUB_OPENS = hub_opens
    theirs = spike.commands(spike.lab.BY_NAME[name], kind=kind)
    ours = vyos.commands(model.BY_NAME[name], kind=kind)
    assert ours == theirs


def test_the_default_kind_is_the_one_wan_yaml_names() -> None:
    edge = model.BY_NAME["fc-fw-edge"]
    assert vyos.commands(edge) == vyos.commands(edge, kind=model.KIND)
    assert any(line.startswith("set vpn ipsec ") for line in vyos.commands(edge))


def test_every_line_is_one_the_vrouter_app_takes() -> None:
    for name in ROUTERS:
        for line in vyos.commands(model.BY_NAME[name]):
            assert vyos.refused(line) == "", line
