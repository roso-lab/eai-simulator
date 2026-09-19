"""Offline checks for the maintained Factory USD semantic bindings."""

from pathlib import Path

import pytest

from demo.factory_benchmark.prim_bindings import audit_factory_prim_bindings
from demo.factory_benchmark.scene_contract import build_factory_scene_contract
from EAI.scenario_spec.templates import Anchor, SceneTemplate, Zone


def test_factory_prim_bindings_match_local_stage():
    root = Path(__file__).resolve().parents[3]
    usd_path = root / "usd/scene/factory/factory.usd"
    if not usd_path.is_file():
        pytest.skip("Local Factory USD is not installed")
    report = audit_factory_prim_bindings(usd_path)
    assert report["validated"]
    assert report["metadata_validated"]
    assert all(item["valid"] for item in report["bindings"])
    target = next(item for item in report["bindings"] if item["id"] == "inspection_target_box")
    assert target["role"] == "inspection_target_visual_reference"
    assert target["world_bounds_m"]["max"][2] > target["world_bounds_m"]["min"][2]
    assert len(report["usd_sha256"]) == 64
    template = SceneTemplate(
        "factory", "factory", (Zone("storage_west", (0, 0, 2, 2), "storage"),),
        (Anchor("a1", 1, 1, ("storage_west",)),), "map-hash",
    )
    contract = build_factory_scene_contract(template, report)
    assert contract["base_world"]["usd_sha256"] == report["usd_sha256"]
    assert contract["prim_bindings"]
    assert contract["inspection_targets"][0]["ground_truth_status"] == "evaluator_only_pending"


def test_factory_prim_binding_fails_closed_for_missing_stage(tmp_path):
    with pytest.raises(ValueError, match="does not exist"):
        audit_factory_prim_bindings(tmp_path / "missing.usd")
