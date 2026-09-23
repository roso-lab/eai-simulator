"""Versioned deployment-assessment authoring cases, separate from frozen packs.

These are declared-contract fixtures, not native admission evidence. Hidden
labels and event schedules stay in this private authoring package. The public
task still contains only goals, capabilities and environment facts.
"""

from copy import deepcopy
from typing import Any

from .goal_deployment_feasibility import scene_requirements
from .goal_driven_tasks import build_goal_task_package
from .provenance import canonical_sha256


_CAPABILITY = {"factory": "observe", "warehouse_nvd": "pick",
               "modern_house_nvd": "pick", "city": "observe", "restaurant": "relay"}


def build_deployment_cases(scene_key: str) -> dict[str, Any]:
    """Build initial loss and unique-provider exit/degradation case pairs.

    These diagnostic cases have no formal truth until a preregistered complete
    remaining-task oracle has passed quality validation at every measurement
    point. No actor assignment is exposed to the participant.
    """
    from .goal_deployment_assessment import enable_deployment_assessment
    from .goal_driven_contract import validate_goal_task_spec

    if scene_key not in _CAPABILITY:
        raise ValueError(f"unsupported scene {scene_key!r}")
    baseline = enable_deployment_assessment(build_goal_task_package(scene_key)["public_task"])
    capability = _CAPABILITY[scene_key]
    provider = next(row["id"] for row in baseline["candidate_robot_pool"]
                    if capability in row["public_capabilities"])
    rows = []
    for kind in ("initial_infeasible", "unique_provider_exit", "unique_provider_degraded"):
        spec = deepcopy(baseline)
        for robot in spec["candidate_robot_pool"]:
            if kind == "initial_infeasible" or robot["id"] != provider:
                robot["public_capabilities"] = [c for c in robot["public_capabilities"] if c != capability]
        validate_goal_task_spec(spec)
        event = None
        if kind != "initial_infeasible":
            robot = next(r for r in spec["candidate_robot_pool"] if r["id"] == provider)
            event = {"type": "member_exit" if kind == "unique_provider_exit" else "capability_change",
                     "agent_id": provider, "removed_capability": capability,
                     "remaining_capabilities": [c for c in robot["public_capabilities"] if c != capability],
                     "trigger": "preregistered_before_relevant_outcome_completion"}
        rows.append({
            "case_id": f"{scene_key}:{kind}:deployment-v1", "kind": kind,
            "public_task": spec, "public_task_sha256": canonical_sha256(spec),
            "private_evaluation": {
                "visibility": "author_evaluator_only", "scope": "declared_contract_only",
                "requirements": scene_requirements(spec), "external_event": event,
                "oracle_status": "failed", "feasibility_label": None,
                "quality_failure": "remaining_task_oracle_not_yet_certified",
                "initial_internal_diagnostic": "not_deployable" if event is None else "unverified",
                "post_event_internal_diagnostic": "not_deployable" if event is not None else None,
                "reason_codes": ["missing_capability"] if event is None else ["lost_unique_capability"],
                "removed_capability": capability,
                "label_tool_condition": (
                    "no_supplementary_observation_provider" if capability == "observe"
                    else "reviewed_declared_execution_channels"),
                "validity_condition": "affected_required_outcome_still_incomplete",
                "validation_rule": "evaluate_deployment_feasibility_on_trusted_available_pool",
            },
            "execution_validated": False, "business_outcomes_validated": False, "scoreable": False,
        })
    return {"schema": "eai.benchmark.deployment_cases", "version": 1,
            "scene_key": scene_key, "cases": rows,
            "baseline_public_task_sha256": canonical_sha256(baseline),
            "release_status": "authoring_only_requires_native_and_positive_certificate"}
