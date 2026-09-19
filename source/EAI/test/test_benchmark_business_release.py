import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize(
    "module_name,smoke_name,full_name",
    [
        (
            "demo.warehouse_benchmark.challenge",
            "demo/warehouse_benchmark/scenarios/navigation_smoke.json",
            "demo/warehouse_benchmark/scenarios/business_logistics_01.json",
        ),
        (
            "demo.modern_house_benchmark.challenge",
            "demo/modern_house_benchmark/scenarios/navigation_smoke.json",
            "demo/modern_house_benchmark/scenarios/home_recovery_01.json",
        ),
    ],
)
def test_formal_business_compiler_rejects_navigation_smoke(module_name, smoke_name, full_name):
    module = __import__(module_name, fromlist=["compile_challenge"])

    with pytest.raises(ValueError, match="navigation_smoke.*cannot be published"):
        module.compile_challenge(ROOT / smoke_name)

    bundle = module.compile_challenge(ROOT / full_name)
    assert bundle["public_task"]["domain_requirements"]["required_for_business_success"] is True


@pytest.mark.parametrize(
    "module_name,required_assets,required_nodes",
    [
        (
            "demo.warehouse_benchmark.challenge",
            {"wooden_crate_a", "cardbox_c"},
            {"oversized_crate_transfer", "priority_carton_dispatch"},
        ),
        (
            "demo.modern_house_benchmark.challenge",
            {"mh_armchair", "mh_bookstack"},
            {"coordinated_furniture_reposition", "keepsake_relocation"},
        ),
    ],
)
def test_public_business_packet_contains_safe_regeneration_skeleton(
    module_name, required_assets, required_nodes
):
    module = __import__(module_name, fromlist=["compile_challenge"])
    public = module.compile_challenge()["public_task"]
    assert public["release_gate"]["authoring_status"] == "passed"
    assert public["release_gate"]["execution_status"] == "external_pending"
    skeleton = public["business_rules"]["task_skeleton"]

    assert skeleton["schema"] == "eai.benchmark.public_task_skeleton"
    assert skeleton["autonomous_decomposition_evaluated"] is False
    assert set(required_nodes) <= set(skeleton["required_objective_roles"])
    assert set(required_nodes) <= set(public_objective_ids := {
        objective["id"] for objective in public["objectives"]
    })
    assert set(skeleton["causal_predecessors"]) <= public_objective_ids
    assert required_assets <= {
        artifact["asset_key"] for artifact in skeleton["scene_artifacts"]
    }
    assert required_assets <= {
        entity["asset_key"] for entity in public["scene_objects"]
    }
    assert set(skeleton["lifecycle"]["events"]) == {
        "member_join", "member_exit", "capability_change"
    }
    public_axes = {
        axis["id"]: axis for axis in public["factor_manifest"]["axes"]
    }
    assert skeleton["communication"]["conditions"] == public_axes[
        "communication_quality"
    ]["levels"]

    encoded = json.dumps(skeleton, ensure_ascii=False, sort_keys=True)
    for forbidden in (
        "preferred_agent",
        "reference_assignment",
        "reference_decision",
        "hidden_fault_time_s",
        "message_delivery_schedule",
        "reserve_identity",
    ):
        assert forbidden not in encoded


def test_default_formal_inputs_are_business_files():
    from demo.modern_house_benchmark.challenge import DEFAULT_SCENARIO as house
    from demo.warehouse_benchmark.challenge import DEFAULT_SCENARIO as warehouse

    assert house.name == "home_recovery_01.json"
    assert warehouse.name == "business_logistics_01.json"
    assert "navigation_smoke" not in str(house)
    assert "navigation_smoke" not in str(warehouse)
