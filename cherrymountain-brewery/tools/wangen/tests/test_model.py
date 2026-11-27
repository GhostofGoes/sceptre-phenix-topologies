"""wan.yaml loads, is consistent, and equals the model that the wan-bgp spike ran."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

from wangen import model

SPIKE: Path = Path(__file__).resolve().parents[3] / "spikes" / "wan-bgp"


@pytest.fixture(autouse=True)
def _loaded() -> None:
    model.load()


def test_wan_yaml_loads() -> None:
    assert len(model.ROUTERS) == 10
    assert model.BY_NAME[model.HUB].asn == 64505
    assert [tunnel.name for tunnel in model.TUNNELS] == ["fc-a", "fc-b", "ep-a", "ep-b", "cs-a"]
    assert model.KIND == model.VTI


def test_wan_yaml_equals_the_spikes_model() -> None:
    """The spike's runs are the evidence for these values, so the two must not drift."""
    sys.path.insert(0, str(SPIKE))
    try:
        import netns_lab as spike
        import vyos_sketch
    finally:
        sys.path.remove(str(SPIKE))
    # `default_requires` is the generator's own: the spike's ISPs have no condition.
    ours = [r.model_dump(exclude={"default_requires"}) for r in model.ROUTERS]
    assert ours == [r.model_dump() for r in spike.ROUTERS]
    assert [link.model_dump() for link in model.LINKS] == [link.model_dump() for link in spike.LINKS]
    # The spike's LANs have no VLAN: it names its own.
    assert [lan.model_dump(exclude={"vlan", "vlan_id"}) for lan in model.LANS] == [
        lan.model_dump() for lan in spike.LANS
    ]
    assert [t.model_dump() for t in model.TUNNELS] == [t.model_dump() for t in spike.TUNNELS]
    assert model.SITE_BLOCKS == spike.SITE_BLOCKS
    assert model.PRIVATE == spike.PRIVATE
    assert (model.HUB_ENDPOINT, model.HQ_PREFIX) == (spike.HUB_ENDPOINT, spike.HQ_PREFIX)
    assert (model.CORE_KEEPALIVE_S, model.CORE_HOLD_S, model.BFD_MULTIPLIER) == (
        spike.CORE_KEEPALIVE_S,
        spike.CORE_HOLD_S,
        spike.BFD_MULTIPLIER,
    )
    assert (model.BFD_INTERVAL_MS, model.VTI_MTU) == (vyos_sketch.BFD_INTERVAL_MS, vyos_sketch.VTI_MTU)
    for tunnel, other in zip(model.TUNNELS, spike.TUNNELS, strict=True):
        assert (tunnel.mark(), tunnel.hub_address(), tunnel.site_address()) == (
            other.mark(),
            other.hub_address(),
            other.site_address(),
        )


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda doc: doc["links"][0].update(b="nobody"), "an end is not a router"),
        (lambda doc: doc["links"][0].update(relation="friend"), "relation 'friend'"),
        (lambda doc: doc["tunnels"][0].update(via="bristlecone"), "the site has no link to bristlecone"),
        (lambda doc: doc["tunnels"][1].update(port=51821), "port 51821 is used twice"),
        (lambda doc: doc["tunnels"][0].update(net="10.255.0.0/30"), "is not a /31"),
        (lambda doc: doc["vpn"].update(kind="gre"), "kind 'gre'"),
        (lambda doc: doc["site_blocks"].update({"nobody": ["10.9.0.0/16"]}), "'nobody' is not a router"),
        (lambda doc: doc["routers"][1].update(name="cb-west"), "a router name is used twice"),
    ],
)
def test_a_wrong_entry_is_refused_by_name(tmp_path: Path, change, message: str) -> None:  # noqa: ANN001
    doc = yaml.safe_load(model.DEFAULT_PATH.read_text(encoding="utf-8"))
    change(doc)
    path = tmp_path / "wan.yaml"
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    with pytest.raises(model.WanError, match=message):
        model.load(path)


def test_an_unknown_key_is_refused(tmp_path: Path) -> None:
    doc = yaml.safe_load(model.DEFAULT_PATH.read_text(encoding="utf-8"))
    doc["routers"][0]["colour"] = "red"
    path = tmp_path / "wan.yaml"
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    with pytest.raises(ValueError, match="colour"):
        model.load(path)
