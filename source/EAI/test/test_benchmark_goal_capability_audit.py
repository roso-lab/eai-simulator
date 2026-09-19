"""Witness-independent local capability audits using the real constraint model."""

from copy import deepcopy
import json

import pytest

from demo.benchmark import goal_capability_audit as audit
from demo.benchmark.goal_constraints import transport_options
from demo.benchmark.skill_ontology import skill_names


def _robot(identifier, robot_type, *, payload=8.0):
    aerial = robot_type == "iris"
    manipulator = robot_type == "m20"
    capabilities = (["aerial_navigation", "observe"] if aerial else
                    ["ground_navigation", "cooperative_carry"])
    interfaces = ["camera"] if aerial else ["cooperative_transport"]
    if manipulator:
        capabilities += ["pick", "stair_traversal"]
        interfaces += ["manipulator"]
    return {
        "id": identifier, "type": robot_type, "mobility": "aerial" if aerial else "ground",
        "public_capabilities": capabilities, "interfaces": interfaces,
        "physical_profile": {
            "configuration_id": f"test_{robot_type}_v1",
            "body_size_m": [0.8, 0.5, 0.9],
            "effective_payload_kg": 0.0 if aerial else payload,
            "reach_m": 0.0 if aerial else 0.8,
            "grasp_interfaces": ([] if aerial else
                                 ["support_socket", "fixed_gripper"] if manipulator else ["support_socket"]),
            "max_step_height_m": 0.2 if manipulator else 0.0,
        },
    }


def _object(identifier, mass, *, joint):
    points = [{"id": "center", "position_m": [0.0, 0.0, 0.1], "interface": "fixed_gripper"}]
    modes = [{"id": "solo", "slots": [{"point_id": "center", "load_fraction": 1.0}],
              "envelope_m": [1.2, 0.8, 1.2]}]
    if joint:
        points += [{"id": side, "position_m": [0.0, offset, 0.1], "interface": "support_socket"}
                   for side, offset in (("left", -0.3), ("right", 0.3))]
        modes.append({"id": "paired", "slots": [
            {"point_id": "left", "load_fraction": 0.5},
            {"point_id": "right", "load_fraction": 0.5},
        ], "envelope_m": [1.5, 1.8, 1.2]})
    return {"id": identifier, "physical_properties": {
        "mass_kg": mass, "size_m": [0.4, 0.3, 0.3], "initial_region_id": "work",
        "grasp_points": points, "handling_modes": modes,
    }}


@pytest.fixture
def task_spec():
    # No scene-specific task generator and no author witness are involved.
    return {
        "task_id": "independent-screen",
        "candidate_robot_pool": [
            _robot("m20_a", "m20"), _robot("m20_b", "m20"),
            _robot("support", "carter", payload=10.0), _robot("camera", "iris"),
        ],
        "public_world": {
            "regions": [{"id": "work", "resource_capacity": 2, "access_modes": ["ground_navigation"]}],
            "objects": [_object("crate", 12.0, joint=True), _object("parcel", 3.0, joint=False)],
            "routes": [], "vertical_links": [],
        },
        "execution_contract": {
            "schema": "eai.benchmark.reliable_skills", "version": 1,
            "parameter_basis": "benchmark_defined", "scene_binding_status": "pending",
            "guarantee": "conditional_deterministic", "collision_policy": "hold_and_report",
            "observation_policy": "exact_within_declared_visibility",
            "skill_durations_s": {name: 1.0 for name in skill_names()},
            "clearance_margin_m": 0.05,
        },
        "budgets": {"max_active_agents": 3},
    }


def _object_result(screen, object_id):
    return next(row for row in screen["object_handling"] if row["object_id"] == object_id)


def _removal(report, agent_id):
    return next(row for row in report["agent_removals"] if row["removed_agent_id"] == agent_id)


def _capability_change(report, agent_id, capability):
    return next(row for row in report["capability_changes"]
                if row["agent_id"] == agent_id and row["removed_capability"] == capability)


def test_all_fixed_point_assignments_include_homogeneous_m20_pair(task_spec):
    report = audit.build_capability_audit(task_spec)
    crate = _object_result(report["baseline"], "crate")
    assert crate["min_carriers"] == 2
    assert crate["homogeneous_feasible"] is True
    assert crate["heterogeneous_feasible"] is True
    assert crate["cross_mobility_feasible"] is False
    # Six ordered pairs retain both possible left/right grasp-point bindings.
    options = [row["option"] for row in crate["feasible_options"]]
    assert len(options) == 6
    assert any(option["assigned_agents"] == ["m20_a", "m20_b"] for option in options)
    assert any(option["assigned_agents"] == ["m20_b", "m20_a"] for option in options)
    assert sorted(options, key=lambda row: json.dumps(row, sort_keys=True)) == sorted(
        transport_options(task_spec, "crate"), key=lambda row: json.dumps(row, sort_keys=True))
    assert report["homogeneity_basis"] == "declared_robot_type"


def test_lighter_object_enumerates_solo_and_joint_options(task_spec):
    task_spec["public_world"]["objects"][0]["physical_properties"]["mass_kg"] = 4.0
    crate = _object_result(audit.build_capability_audit(task_spec)["baseline"], "crate")
    assert crate["min_carriers"] == 1
    assert {row["option"]["handling_mode_id"] for row in crate["feasible_options"]} == {"solo", "paired"}
    assert len(crate["feasible_options"]) == 8


def test_removing_support_robot_reassigns_both_slots_to_homogeneous_pair(task_spec):
    report = audit.build_capability_audit(task_spec)
    removal = _removal(report, "support")
    crate = _object_result(removal["remaining_supply"], "crate")
    assert crate["min_carriers"] == 2
    assert crate["homogeneous_feasible"] is True
    assert crate["heterogeneous_feasible"] is False
    assert removal["lost_handling_object_ids"] == []
    assert removal["task_recovery_validated"] is False


def test_removing_one_m20_keeps_a_different_joint_team_and_one_picker(task_spec):
    removal = _removal(audit.build_capability_audit(task_spec), "m20_a")
    crate = _object_result(removal["remaining_supply"], "crate")
    assert crate["homogeneous_feasible"] is False
    assert crate["heterogeneous_feasible"] is True
    assert removal["remaining_supply"]["capability_supply"]["pick"] == ["m20_b"]
    assert all("m20_a" not in row["option"]["assigned_agents"] for row in crate["feasible_options"])


def test_mobility_removal_does_not_use_witness_dependency_propagation(task_spec):
    report = audit.build_capability_audit(task_spec)
    variants = {row["removed_mobility"]: row for row in report["mobility_removals"]}
    assert variants["aerial"]["remaining_supply"]["object_handling"] == report["baseline"]["object_handling"]
    assert variants["aerial"]["lost_handling_object_ids"] == []
    assert variants["ground"]["lost_handling_object_ids"] == ["crate", "parcel"]
    crate = _object_result(variants["ground"]["remaining_supply"], "crate")
    assert crate["min_carriers"] is None
    assert crate["homogeneous_feasible"] is False


def test_zero_remaining_candidates_is_a_valid_counterfactual(task_spec):
    task_spec["candidate_robot_pool"] = [task_spec["candidate_robot_pool"][0]]
    removal = _removal(audit.build_capability_audit(task_spec), "m20_a")
    assert removal["remaining_supply"]["available_agent_ids"] == []
    assert removal["lost_capabilities"] == ["pick", "stair_traversal"]
    # The crate was already infeasible before removal, so only the parcel is newly lost.
    assert removal["lost_handling_object_ids"] == ["parcel"]


def test_pick_loss_reenumerates_solo_options_without_removing_joint_support(task_spec):
    report = audit.build_capability_audit(task_spec)
    change = _capability_change(report, "m20_a", "pick")
    parcel = _object_result(change["remaining_supply"], "parcel")
    assert [row["option"]["assigned_agents"] for row in parcel["feasible_options"]] == [["m20_b"]]
    assert change["remaining_supply"]["capability_supply"]["pick"] == ["m20_b"]
    assert _object_result(change["remaining_supply"], "crate") == _object_result(report["baseline"], "crate")
    assert change["task_recovery_validated"] is False


def test_stair_loss_is_supply_only_without_claiming_route_or_recovery_feasibility(task_spec):
    report = audit.build_capability_audit(task_spec)
    change = _capability_change(report, "m20_a", "stair_traversal")
    assert change["remaining_supply"]["capability_supply"]["stair_traversal"] == ["m20_b"]
    assert change["remaining_supply"]["object_handling"] == report["baseline"]["object_handling"]
    assert report["task_feasibility_validated"] is False
    assert report["task_recovery_validated"] is False
    assert report["heterogeneity_necessity_validated"] is False
    assert report["execution_validated"] is False


def test_asymmetric_loads_remove_homogeneous_options_without_summing_payload(task_spec):
    slots = task_spec["public_world"]["objects"][0]["physical_properties"]["handling_modes"][1]["slots"]
    slots[0]["load_fraction"], slots[1]["load_fraction"] = 0.75, 0.25
    report = audit.build_capability_audit(task_spec)
    crate = _object_result(report["baseline"], "crate")
    assert crate["homogeneous_feasible"] is False
    assert crate["heterogeneous_feasible"] is True
    assert crate["min_carriers"] == 2
    assert all(row["option"]["assigned_agents"][0] == "support" for row in crate["feasible_options"])
    assert _removal(report, "support")["lost_handling_object_ids"] == ["crate"]


def test_impossible_load_is_not_reported_as_unknown_or_as_task_impossibility(task_spec):
    task_spec["public_world"]["objects"][0]["physical_properties"]["mass_kg"] = 100.0
    report = audit.build_capability_audit(task_spec)
    crate = _object_result(report["baseline"], "crate")
    assert crate["status"] == "no_declared_feasible_option"
    assert crate["min_carriers"] is None
    assert report["unscreened_objects"] == []
    assert report["task_feasibility_validated"] is False
    assert all("crate" not in row["lost_handling_object_ids"] for row in report["agent_removals"])


def test_empty_modes_are_distinct_from_absent_physical_declarations(task_spec):
    for obj in task_spec["public_world"]["objects"]:
        obj["physical_properties"]["handling_modes"] = []
    report = audit.build_capability_audit(task_spec)
    assert report["screened_object_ids"] == ["crate", "parcel"]
    assert report["unscreened_objects"] == []
    assert all(row["min_carriers"] is None for row in report["baseline"]["object_handling"])
    for obj in task_spec["public_world"]["objects"]:
        del obj["physical_properties"]
    report = audit.build_capability_audit(task_spec)
    assert report["screened_object_ids"] == []
    assert report["baseline"]["object_handling"] == []
    assert report["unscreened_objects"] == [
        {"object_id": name, "reason": "handling_modes_not_declared"} for name in ("crate", "parcel")]


def test_local_options_do_not_establish_global_team_budget_feasibility(task_spec):
    task_spec["budgets"]["max_active_agents"] = 1
    report = audit.build_capability_audit(task_spec)
    crate = _object_result(report["baseline"], "crate")
    assert crate["min_carriers"] == 2
    assert report["task_feasibility_validated"] is False
    assert any("budgets" in text for text in report["limitations"])


def test_report_is_deterministic_serializable_and_does_not_mutate_input(task_spec):
    original = deepcopy(task_spec)
    first = audit.build_capability_audit(task_spec)
    assert task_spec == original
    assert first == audit.build_capability_audit(task_spec)
    json.dumps(first, allow_nan=False)
    first["baseline"]["object_handling"][0]["feasible_options"][0]["option"]["assigned_agents"].clear()
    assert task_spec == original
    assert audit.build_capability_audit(task_spec)["baseline"]["object_handling"][0]["feasible_options"]


def test_invalid_physical_model_fails_instead_of_becoming_a_negative_certificate(task_spec):
    task_spec["public_world"]["objects"][0]["physical_properties"]["mass_kg"] = float("nan")
    with pytest.raises(ValueError, match="finite"):
        audit.build_capability_audit(task_spec)


@pytest.mark.parametrize("assigned", [[], ["ghost"], ["m20_a", "m20_a"]])
def test_invalid_shared_enumerator_assignment_fails_closed(task_spec, monkeypatch, assigned):
    monkeypatch.setattr(audit, "transport_options", lambda *args, **kwargs: [{"assigned_agents": assigned}])
    with pytest.raises(ValueError, match="unique available assigned_agents"):
        audit.build_capability_audit(task_spec)
