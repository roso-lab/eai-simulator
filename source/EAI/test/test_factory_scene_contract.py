"""Factory scene contract checks without starting Isaac."""

from types import SimpleNamespace

from demo.factory_benchmark.scene_contract import (
    build_factory_scene_contract,
    validate_factory_task_contract,
)


def _template():
    zones = (SimpleNamespace(id="storage_west", role="storage", kind="storage", aabb=(-1, -1, 1, 1), level=0),
             SimpleNamespace(id="midband", role="corridor", kind="corridor", aabb=(-1, -1, 1, 1), level=0),
             SimpleNamespace(id="assembly_east", role="workshop", kind="workshop", aabb=(-1, -1, 1, 1), level=0),
             SimpleNamespace(id="yard_south", role="yard", kind="yard", aabb=(-1, -1, 1, 1), level=0),
             SimpleNamespace(id="dock_north", role="dock", kind="dock", aabb=(-1, -1, 1, 1), level=0))
    anchors = tuple(SimpleNamespace(x=0.0, y=0.0, zones=(zone.id,)) for zone in zones)
    return SimpleNamespace(scene_key="factory", key="factory", map_sha256="m" * 64,
                           zones=zones, anchors=anchors)


def test_factory_contract_distinguishes_navigation_from_business_semantics():
    contract = build_factory_scene_contract(_template())
    assert contract["base_world"]["semantic_binding_status"].startswith("zone_and_occupancy")
    assert all(site["quality_status"] == "geometric_site_only"
               for site in contract["observation_sites"])
    assert validate_factory_task_contract(contract, "F-TRAFFIC-01")["eligible"]


def test_factory_inspection_fails_closed_when_required_prop_is_missing(monkeypatch):
    import demo.factory_benchmark.scene_contract as module
    monkeypatch.delitem(module.ASSETS, "safety_beam", raising=False)
    contract = build_factory_scene_contract(_template())
    result = validate_factory_task_contract(contract, "F-INS-01")
    assert result["eligible"] is False
    assert "safety_beam" in result["missing_assets"]
