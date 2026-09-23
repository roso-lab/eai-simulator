"""Independent deployment labels must not inherit witness or model claims."""

from copy import deepcopy

import pytest

from demo.benchmark.goal_deployment_feasibility import (
    evaluate_deployment_feasibility, gate_evaluation, scene_requirements,
)
from demo.benchmark.goal_driven_tasks import build_goal_task_package


SCENES = ("factory", "warehouse_nvd", "modern_house_nvd", "city", "restaurant")


def evaluate(task, robots=None, **kwargs):
    robots = task["candidate_robot_pool"] if robots is None else robots
    inputs = dict(episode_id="episode", assessment_id="assessment-0", assessment_epoch=0,
                  roster_version=0, current_robot_pool=robots,
                  capability_versions={r["id"]: 0 for r in robots}, platform_valid=True,
                  platform_evidence_refs=["platform-check:fixture-only"])
    inputs.update(kwargs)
    return evaluate_deployment_feasibility(task, **inputs)


@pytest.mark.parametrize("scene", SCENES)
def test_capability_presence_does_not_prove_full_task_feasibility(scene):
    task = build_goal_task_package(scene)["public_task"]
    before = deepcopy(task)
    result = evaluate(task)
    assert result["label"] == "unverified"
    assert result["failed_necessary_conditions"] == []
    assert result["scoreable"] is False
    assert task == before


@pytest.mark.parametrize("scene,removed", [
    ("factory", {"observe"}), ("city", {"observe"}),
    ("restaurant", {"ground_navigation"}),
    ("warehouse_nvd", {"pick", "cooperative_carry"}),
    ("modern_house_nvd", {"pick", "cooperative_carry"}),
])
def test_each_scene_has_a_provable_initial_team_infeasibility(scene, removed):
    task = build_goal_task_package(scene)["public_task"]
    robots = deepcopy(task["candidate_robot_pool"])
    for robot in robots:
        robot["public_capabilities"] = [c for c in robot["public_capabilities"] if c not in removed]
    result = evaluate(task, robots)
    assert result["label"] == "not_deployable"
    assert result["failed_necessary_conditions"]
    assert result["execution_validated"] is False


def test_factory_service_cart_is_stationary_not_a_grasp_requirement():
    task = build_goal_task_package("factory")["public_task"]
    for robot in task["candidate_robot_pool"]:
        robot["public_capabilities"] = [c for c in robot["public_capabilities"]
                                        if c not in {"pick", "cooperative_carry"}]
    assert evaluate(task)["label"] == "unverified"
    assert all(row["transport_object_id"] is None for row in scene_requirements(task)["conditions"])


def test_single_restaurant_ground_member_may_visit_both_regions_sequentially():
    task = build_goal_task_package("restaurant")["public_task"]
    robot = next(r for r in task["candidate_robot_pool"]
                 if {"ground_navigation", "relay"} <= set(r["public_capabilities"]))
    assert evaluate(task, [robot])["label"] == "unverified"


def test_unique_provider_loss_requires_remaining_outcome_and_new_snapshot():
    task = build_goal_task_package("restaurant")["public_task"]
    robots = deepcopy(task["candidate_robot_pool"])
    provider = next(r for r in robots if "relay" in r["public_capabilities"])
    for robot in robots:
        if robot["id"] != provider["id"]:
            robot["public_capabilities"] = [c for c in robot["public_capabilities"] if c != "relay"]
    before = evaluate(task, robots)
    remaining = [r for r in robots if r["id"] != provider["id"]]
    after = evaluate(task, remaining, assessment_epoch=1, roster_version=1, assessment_id="assessment-1")
    assert before["label"] == "unverified"
    assert after["label"] == "not_deployable"
    assert before["binding_sha256"] != after["binding_sha256"]
    completed = evaluate(task, remaining, completed_outcome_ids=["readiness_receipt_committed"])
    assert completed["label"] == "unverified"


def test_payload_restrictions_preserved_but_reach_and_body_size_do_not_label_model_wrong():
    task = build_goal_task_package("warehouse_nvd")["public_task"]
    robots = deepcopy(task["candidate_robot_pool"])
    for robot in robots:
        robot["physical_profile"]["reach_m"] = 0
        robot["physical_profile"]["body_size_m"] = [10, 10, 10]
    assert evaluate(task, robots)["label"] == "unverified"
    for robot in robots:
        robot["physical_profile"]["effective_payload_kg"] = 0
    assert evaluate(task, robots)["label"] == "not_deployable"


def test_bad_platform_and_zero_survivors_are_distinct():
    task = build_goal_task_package("factory")["public_task"]
    assert evaluate(task, [], platform_valid=False)["label"] == "unverified"
    assert evaluate(task, [])["label"] == "not_deployable"


def test_false_and_stale_positive_certificates_do_not_become_truth():
    task = build_goal_task_package("factory")["public_task"]
    before = evaluate(task)
    certificate = {"binding_sha256": before["binding_sha256"], "proof_ref": "independent-fixture"}
    with pytest.raises(ValueError, match="independent validator"):
        evaluate(task, certificate=certificate)
    assert evaluate(task, certificate=certificate, certificate_validator=lambda c, s: False)["label"] == "unverified"
    assert evaluate(task, certificate=certificate, certificate_validator=lambda c, s: True)["label"] == "deployable"
    with pytest.raises(ValueError, match="different assessment snapshot"):
        evaluate(task, assessment_epoch=1, certificate=certificate, certificate_validator=lambda c, s: True)


def test_private_hook_projection_and_requirement_binding():
    task = build_goal_task_package("factory")["public_task"]
    report = evaluate(task)
    assert set(gate_evaluation(report)) == {"assessment_id", "assessment_epoch", "label", "platform_valid",
                                            "evidence_refs", "evaluator_ref"}
    rules = scene_requirements(task)
    changed = deepcopy(task)
    changed["mission"]["goal"] += " changed"
    with pytest.raises(ValueError, match="different public task"):
        evaluate(changed, requirements=rules)


def test_declaration_errors_do_not_turn_into_infeasibility():
    task = build_goal_task_package("warehouse_nvd")["public_task"]
    robots = deepcopy(task["candidate_robot_pool"])
    robots[0]["physical_profile"]["effective_payload_kg"] = float("nan")
    with pytest.raises(ValueError):
        evaluate(task, robots)
    with pytest.raises(ValueError, match="match uniquely"):
        evaluate(task, capability_versions={})
