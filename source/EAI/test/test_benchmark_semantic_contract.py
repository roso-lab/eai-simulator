"""Author-facing semantics audit: declarations never become execution evidence."""

from copy import deepcopy
import json

import pytest

from demo.benchmark.semantic_contract import (
    build_semantic_design_catalog, implementation_support, objective_semantics_for,
    review_semantic_design, validate_semantic_task_contract,
)


SCENES = ("factory", "warehouse_nvd", "modern_house_nvd")


def scenario(scene):
    from demo.benchmark.scene_challenge import generate_scene_scenario
    return generate_scene_scenario(scene_key=scene, task_name="semantic_audit", seed=17, origin=(0, 0))


def codes(review):
    return {item["code"] for item in review["findings"]}


@pytest.mark.parametrize("scene", SCENES)
def test_validation_only_qualifies_metadata_and_support_is_not_execution(scene):
    contract = build_semantic_design_catalog(scene)
    before = deepcopy(contract)
    result = validate_semantic_task_contract(contract, scene_key=scene)
    assert result["validated"] and result["validation_scope"] == "metadata_only"
    assert result["semantic_version"] == 2 and not result["execution_validated"]
    assert contract == before
    assert contract["assurance"]["compiler_mapping"] == "integrated_exact_objective_mapping"
    for group in ("communication", "lifecycle"):
        assert all(not row["execution_validated"] for row in contract["implementation_support"][group].values())
    assert contract["implementation_support"]["lifecycle"]["member_join"]["status"] == "runtime_callback_boundary"
    expected = "protocol_component" if scene == "factory" else "runtime_component"
    assert contract["implementation_support"]["communication"]["bandwidth_limited"]["status"] == expected


@pytest.mark.parametrize("scene", SCENES)
def test_support_claims_cannot_be_promoted_in_payload_or_override(scene):
    contract = build_semantic_design_catalog(scene)
    contract["implementation_support"]["lifecycle"]["member_join"]["status"] = "implemented"
    with pytest.raises(ValueError, match="unsupported"):
        validate_semantic_task_contract(contract, scene_key=scene)
    with pytest.raises(ValueError, match="cannot override"):
        build_semantic_design_catalog(scene, lifecycle={"execution_validated": True})
    with pytest.raises(ValueError, match="cannot override"):
        build_semantic_design_catalog(scene, communication={"bandwidth_window": 1024})


@pytest.mark.parametrize("field", ["objective_contracts", "assurance", "factors", "artifacts"])
def test_missing_v2_support_fields_rejected(field):
    contract = build_semantic_design_catalog("warehouse_nvd")
    del contract[field]
    with pytest.raises(ValueError):
        validate_semantic_task_contract(contract, scene_key="warehouse_nvd")


def test_extra_success_claims_are_not_catalog_extensions():
    contract = build_semantic_design_catalog("factory")
    contract["release_ready"] = True
    with pytest.raises(ValueError, match="unreviewed"):
        validate_semantic_task_contract(contract, scene_key="factory")


def test_old_frozen_metadata_remains_readable_but_unverified():
    from demo.benchmark.semantic_contract import semantic_for_scene
    contract = semantic_for_scene("factory")
    for key in ("semantic_version", "objective_contracts", "assurance", "implementation_support"):
        contract.pop(key, None)
    result = validate_semantic_task_contract(contract, scene_key="factory")
    assert result["validated"]
    assert result["assurance"] == "legacy_declarations_unverified"
    assert not result["execution_validated"]


@pytest.mark.parametrize("scene", ("warehouse_nvd", "modern_house_nvd"))
def test_scene_scaffolds_receive_distinct_roles_and_honest_deliverables(scene):
    data = scenario(scene)
    original = deepcopy(data)
    rows = [objective_semantics_for(scene, node) for node in data["phase_graph"]["nodes"]]
    count = 10 if scene == "warehouse_nvd" else 11
    assert len(rows) == len({row["role"] for row in rows}) == count
    assert len({row["deliverable"] for row in rows}) == count
    assert all(row["role"] != row["skill"] for row in rows)
    assert all(row["domain_check"]["machine_evaluator"] for row in rows
               if row["skill"] in {"transport_object", "joint_transport"})
    review = review_semantic_design(data)
    assert not review["missing_template_objectives"]
    assert "domain_props_are_primitive_proxies" not in codes(review)
    assert all(row["asset_key"] != "primitive_box" for row in data["composition"]["entities"])
    assert "domain_predicates_pending" in codes(review)
    assert "duplicated_mission_semantics" not in codes(review)  # Raw nodes contain no copied prose.
    assert not review["release_ready"]
    assert data == original


def test_factory_mapping_distinguishes_evidence_capture_from_dispatch_and_control():
    from demo.factory_benchmark.challenge import DEFAULT_SCENARIO, build_public_task_spec
    from EAI.scenario_spec import load_scenario
    public = build_public_task_spec(load_scenario(DEFAULT_SCENARIO), scenario_sha256="test")
    original = deepcopy(public)
    review = review_semantic_design(public)
    rows = {row["id"]: row for row in review["objective_mapping"]}
    assert len(rows) == len({r["role"] for r in rows.values()}) == 4
    assert rows["ground_view"]["runtime_check"]["id"] == "timed_hold"
    assert rows["route_confirmation"]["runtime_check"]["id"] == "marker_dispatch_arrival"
    assert review["model_controlled_objectives"] == ["route_confirmation"]
    assert set(review["benchmark_managed_objectives"]) == {"aerial_view", "ground_view", "ground_approach"}
    assert "duplicated_mission_semantics" not in codes(review)
    assert "objective_mapping_not_integrated" not in codes(review)
    assert public == original


def test_names_or_skill_labels_do_not_launder_wrong_target_into_a_semantic_role():
    node = {"id": "dock_lane_check", "skill": "navigate", "target_ref": "outbound_dock_zone"}
    assert objective_semantics_for("warehouse_nvd", node)["role"] == "dock_approach_checker"
    for changes in ({"id": "renamed_navigate"}, {"skill": "wait"}, {"target_ref": "rack_scan_zone"}):
        with pytest.raises(ValueError):
            objective_semantics_for("warehouse_nvd", {**node, **changes})


def test_scenario_semantics_do_not_depend_on_hidden_assignment_or_identity():
    node = {"id": "rack_overview_scan", "skill_key": "aerial_scan", "target_ref": "rack_scan_zone"}
    original = objective_semantics_for("warehouse_nvd", node)
    assert objective_semantics_for("warehouse_nvd", {**node, "preferred_agent": "secret",
        "object_ref": "private-identity", "hidden_fault_time_s": 123}) == original
    encoded = json.dumps(build_semantic_design_catalog("warehouse_nvd"))
    assert "private-identity" not in encoded and "preferred_agent" not in encoded


@pytest.mark.parametrize("scene", ("warehouse_nvd", "modern_house_nvd"))
def test_single_navigation_release_cannot_inherit_full_domain_deliverables(scene):
    task = {"task_id": "navigation_smoke", "scene": {"scene_key": scene},
        "semantic_task": build_semantic_design_catalog(scene), "objectives": [
            {"id": "approach", "skill": "navigate", "target_ref": "destination",
             "deliverable": "entire business mission complete"}],
        "participant": {"controlled_objectives": ["approach"],
                        "allowed_action_types": ["allocate", "navigate_goal", "wait", "reassign", "consume_message"]}}
    result = review_semantic_design(task)
    assert result["classification"] == "navigation_smoke"
    assert len(result["missing_template_objectives"]) == (10 if scene == "warehouse_nvd" else 11)
    assert {"navigation_smoke_only", "objective_mapping_not_integrated", "declared_actions_not_applied"} <= codes(result)
    assert result["objective_mapping"][0]["scope"] == "navigation_smoke"
    assert not result["semantic_contract_ready"]


def test_all_succeeded_is_not_business_acceptance_even_with_domain_names():
    from demo.benchmark.scene_challenge import evaluate_objectives
    data = scenario("modern_house_nvd")
    # Demonstrates current evaluator boundary; no pose/identity/clearance evidence.
    report = {"scheduling": {"states": {
        n["id"]: {"status": "succeeded"} for n in data["phase_graph"]["nodes"]}}}
    assert evaluate_objectives(report, scenario=data)["task_success"]
    assert not review_semantic_design(data)["release_ready"]


def test_five_communication_labels_do_not_mean_five_executed_conditions():
    from demo.factory_benchmark.inventory_dispatch import delivered_messages
    msg = {"message_id": "m1", "published_at_s": 0, "valid_until_s": 2}
    assert delivered_messages([msg], mode="delayed", delay_s=1, time_s=.5)[0] == []
    assert delivered_messages([msg], mode="delayed", delay_s=1, time_s=1.5)[0] == [msg]
    assert delivered_messages([msg], mode="shared", delay_s=0, time_s=3)[0] == []
    assert delivered_messages([msg], mode="withheld", delay_s=0, time_s=1)[0] == []
    with pytest.raises(ValueError):
        delivered_messages([msg], mode="bandwidth_limited", delay_s=0, time_s=1)
    assert implementation_support("factory")["communication"]["dropped"]["status"] == "protocol_component"
    assert implementation_support("warehouse_nvd")["communication"]["delayed"]["status"] == "runtime_component"


def test_mixed_aerial_template_cannot_claim_member_exit_support():
    from demo.benchmark.runtime import execution_contract
    data = scenario("warehouse_nvd")
    data["fault_events"] = [{"id": "exit", "kind": "member_exit", "agent": "carter_1",
                            "replacement_agent": "m20_1", "time_s": 3}]
    with pytest.raises(ValueError, match="ground-only"):
        execution_contract(data, transport_mode="kinematic")


def test_public_api_lists_only_actions_applied_by_the_selected_runtime():
    from demo.benchmark.scene_challenge import build_scene_runtime_contract
    public, api = build_scene_runtime_contract(scenario("warehouse_nvd"))
    review = review_semantic_design(public, participant_api=api)
    assert "action_advertisement_mismatch" not in codes(review)
    assert "declared_actions_not_applied" not in codes(review)
    assert "consume_message" in public["participant"]["allowed_action_types"]
    assert "lifecycle_event_api_pending" not in codes(review)
    assert not review["execution_validated"]


def test_return_values_are_independent_and_unknown_scene_fails():
    contract = build_semantic_design_catalog("factory")
    contract["objective_contracts"]["aerial_view"]["deliverable"] = "changed"
    assert build_semantic_design_catalog("factory")["objective_contracts"]["aerial_view"]["deliverable"] != "changed"
    with pytest.raises(ValueError):
        build_semantic_design_catalog("unregistered_scene")
