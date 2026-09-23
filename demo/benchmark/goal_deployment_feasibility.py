"""Private, conservative deployment labels for the Goal assessment gate.

This is an evaluator, never a plan-admission filter. Necessary conditions can
prove a negative; passing them leaves feasibility unverified until a trusted
independent solution validator supplies a certificate for the same snapshot.
No assignments, order, witness plans or hidden labels enter the public task.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from itertools import permutations
from typing import Any

from .goal_constraints import validate_constraint_model, validate_robot_profile
from .provenance import canonical_sha256


EVALUATOR_REF = "goal_deployment_necessary_conditions_v1"


def scene_requirements(task_spec: Mapping[str, Any]) -> dict[str, Any]:
    """Register necessary conditions only for the reviewed five scene goals.

    Each condition names an outcome, not a prescribed subtask. Capability
    alternatives are disjunctions; multiple conditions may use the same robot
    sequentially. A stationary factory cart has no transport requirement.
    """
    scene = task_spec["mission"]["scene_key"]
    conditions = {
        "factory": [("anomaly_confirmed", "observe", None)],
        "warehouse_nvd": [
            ("dock_handoff_confirmed", None, "order_crate_01"),
            ("dock_handoff_confirmed", None, "order_carton_01")],
        "modern_house_nvd": [
            ("furniture_repositioned", None, "living_armchair_01"),
            ("belonging_staged", None, "resident_bookstack_01")],
        "city": [("incident_assessed", "observe", None)],
        "restaurant": [
            ("kitchen_service_inspected", "ground_navigation", None),
            ("dining_service_inspected", "ground_navigation", None),
            ("readiness_receipt_committed", "relay", None)],
    }
    if scene not in conditions:
        raise ValueError(f"no reviewed deployment requirements for scene {scene!r}")
    outcomes = {row["id"] for row in task_spec["required_outcomes"]}
    rows = []
    for outcome, capability, object_id in conditions[scene]:
        if outcome not in outcomes:
            raise ValueError(f"reviewed outcome {outcome!r} missing from task")
        rows.append({"outcome_id": outcome,
                     "capability_alternatives": [[capability]] if capability else [],
                     "transport_object_id": object_id})
    return {"visibility": "evaluator_only", "evaluator_ref": EVALUATOR_REF,
            "task_sha256": canonical_sha256(task_spec), "conditions": rows}


def _transport_has_option(task_spec, object_id, robots, max_active_agents):
    """Relaxed necessary screen over every declared mode and distinct carrier.

    Intentionally ignores reach/anchor calibration, approach and collision
    geometry. Those failures belong to execution; they cannot certify that a
    high-level allocation is wrong. Payload, skill and interface differences
    do affect heterogeneous allocation and remain in this screen.
    """
    objects = {obj["id"]: obj for obj in task_spec["public_world"]["objects"]}
    if object_id not in objects:
        raise ValueError("necessary transport condition references unknown object")
    props = objects[object_id]["physical_properties"]
    modes = props["handling_modes"]
    if not modes:
        raise ValueError("necessary transport condition has no declared handling modes")
    points = {row["id"]: row for row in props["grasp_points"]}
    for mode in modes:
        slots = mode["slots"]
        if len(slots) > max_active_agents:
            continue
        skill = "pick" if len(slots) == 1 else "cooperative_carry"
        interface = "manipulator" if len(slots) == 1 else "cooperative_transport"
        for assigned in permutations(robots, len(slots)):
            if all(
                robot["mobility"] == "ground"
                and skill in robot["public_capabilities"]
                and interface in robot["interfaces"]
                and points[slot["point_id"]]["interface"] in robot["physical_profile"]["grasp_interfaces"]
                and props["mass_kg"] * slot["load_fraction"] <= robot["physical_profile"]["effective_payload_kg"]
                for robot, slot in zip(assigned, slots)
            ):
                return True
    return False


def evaluate_deployment_feasibility(
    task_spec: Mapping[str, Any], *, episode_id: str, assessment_id: str,
    assessment_epoch: int, roster_version: int,
    current_robot_pool: Sequence[Mapping[str, Any]],
    capability_versions: Mapping[str, int],
    completed_outcome_ids: Sequence[str] = (),
    platform_valid: bool,
    platform_evidence_refs: Sequence[str],
    resource_versions: Mapping[str, int] | None = None,
    requirements: Mapping[str, Any] | None = None,
    certificate: Mapping[str, Any] | None = None,
    certificate_validator: Callable[[Mapping[str, Any], Mapping[str, Any]], bool] | None = None,
) -> dict[str, Any]:
    """Evaluate a trusted current *available pool*, not the chosen subset.

    ``completed_outcome_ids`` and resource/capability state must be supplied by
    the trusted runtime. Certificate validators must establish a complete
    solution for the bound snapshot independently of the participant claim.
    Exceptions stay infrastructure errors, never apparent correct refusals.
    """
    if type(platform_valid) is not bool:
        raise ValueError("platform_valid must be an explicit boolean")
    if not episode_id or not assessment_id:
        raise ValueError("episode and assessment identities are required")
    for value in (assessment_epoch, roster_version, *capability_versions.values(),
                  *(resource_versions or {}).values()):
        if type(value) is not int or value < 0:
            raise ValueError("snapshot versions must be nonnegative integers")
    if not platform_evidence_refs or any(not isinstance(ref, str) or not ref for ref in platform_evidence_refs):
        raise ValueError("platform validation needs independent evidence references")
    robots = deepcopy(list(current_robot_pool))
    ids = [row["id"] for row in robots]
    if len(ids) != len(set(ids)) or set(ids) != set(capability_versions):
        raise ValueError("current pool and capability versions must match uniquely")
    outcomes = {row["id"] for row in task_spec["required_outcomes"]}
    completed = set(completed_outcome_ids)
    if not completed <= outcomes:
        raise ValueError("unknown completed outcome")
    binding = {
        "episode_id": episode_id, "assessment_id": assessment_id,
        "assessment_epoch": assessment_epoch, "roster_version": roster_version,
        "task_sha256": canonical_sha256(task_spec),
        "current_robot_pool": sorted(robots, key=lambda row: row["id"]),
        "capability_versions": dict(capability_versions),
        "resource_versions": dict(resource_versions or {}),
        "completed_outcome_ids": sorted(completed),
        "team_scope": "current_available_pool_not_selected_subset",
    }
    fingerprint = canonical_sha256(binding)
    report = {"assessment_id": assessment_id, "assessment_epoch": assessment_epoch,
              "label": "unverified", "platform_valid": platform_valid,
              "evidence_refs": list(platform_evidence_refs), "evaluator_ref": EVALUATOR_REF,
              "visibility": "evaluator_only", "binding": binding,
              "binding_sha256": fingerprint, "failed_necessary_conditions": [],
              "scope": "declared_capability_interface_and_payload_only",
              "execution_validated": False, "business_outcomes_validated": False,
              "scoreable": False}
    if not platform_valid:
        return report
    # This validates physical declarations, not current-team feasibility.
    validate_constraint_model(task_spec)
    for robot in robots:
        validate_robot_profile(robot)
    rules = dict(requirements) if requirements is not None else scene_requirements(task_spec)
    if rules.get("task_sha256") != binding["task_sha256"]:
        raise ValueError("requirements belong to a different public task")
    for rule in rules["conditions"]:
        outcome = rule["outcome_id"]
        if outcome not in outcomes:
            raise ValueError("necessary condition references unknown outcome")
        if outcome in completed:
            continue
        alternatives = rule["capability_alternatives"]
        obj = rule["transport_object_id"]
        if alternatives:
            if any(not alternative for alternative in alternatives):
                raise ValueError("empty capability alternative")
            passed = any(set(alternative) <= set(robot["public_capabilities"])
                         for alternative in alternatives for robot in robots)
            reason = "missing_capability"
        elif obj:
            passed = _transport_has_option(task_spec, obj, robots, task_spec["budgets"]["max_active_agents"])
            reason = "capacity_or_load_exhausted"
        else:
            raise ValueError("necessary condition has no test")
        if not passed:
            report["failed_necessary_conditions"].append({**deepcopy(rule), "reason_code": reason})
    if report["failed_necessary_conditions"]:
        report["label"] = "not_deployable"
        report["evidence_refs"].append("necessary_conditions:" + canonical_sha256({
            "binding_sha256": fingerprint, "failures": report["failed_necessary_conditions"]}))
    elif certificate is not None:
        if certificate.get("binding_sha256") != fingerprint:
            raise ValueError("positive certificate belongs to a different assessment snapshot")
        if certificate_validator is None:
            raise ValueError("positive certificate requires an independent validator")
        if certificate_validator(deepcopy(certificate), deepcopy(binding)) is True:
            report["label"] = "deployable"
            report["evidence_refs"].append("validated_certificate:" + canonical_sha256(certificate))
    return report


def gate_evaluation(report: Mapping[str, Any]) -> dict[str, Any]:
    """Project private detailed evidence onto the runner's strict hook schema."""
    return {key: deepcopy(report[key]) for key in (
        "assessment_id", "assessment_epoch", "label", "platform_valid",
        "evidence_refs", "evaluator_ref")}
