"""Versioned opt-in contract for participant deployment assessment.

This module defines public protocol vocabulary and trusted evaluation records.
It does not decide whether a task is feasible; hidden evaluators own that
judgement and their labels never enter participant-visible events.
"""

from __future__ import annotations

from copy import deepcopy
import math
from typing import Any, Mapping


DEPLOYMENT_CONTRACT_VERSION = 3
DEPLOYMENT_DECISIONS = ("deployable", "not_deployable")
DEPLOYMENT_REASON_CODES = (
    "missing_capability",
    "unavailable_robot_type",
    "lost_unique_capability",
    "no_redundant_member",
    "capacity_or_load_exhausted",
    "current_team_resource_conflict",
)
DEPLOYMENT_REASSESSMENT_EVENTS = (
    "member_join",
    "member_exit",
    "capability_change",
    "resource_status",
    "deployment_change",
)
DEPLOYMENT_ORACLE_STATUSES = ("verified", "failed")


def deployment_assessment_contract() -> dict[str, Any]:
    return {
        "required": True,
        "decisions": list(DEPLOYMENT_DECISIONS),
        "reason_codes": list(DEPLOYMENT_REASON_CODES),
        "reassessment_events": list(DEPLOYMENT_REASSESSMENT_EVENTS),
        "termination_event": "human_assistance_requested",
    }


def enable_deployment_assessment(task_spec: Mapping[str, Any]) -> dict[str, Any]:
    """Return a validated v3 copy while leaving the caller's v2 task untouched."""
    from .goal_driven_contract import validate_goal_task_spec

    validate_goal_task_spec(task_spec)
    if task_spec.get("version") == DEPLOYMENT_CONTRACT_VERSION:
        return deepcopy(dict(task_spec))
    upgraded = deepcopy(dict(task_spec))
    upgraded["version"] = DEPLOYMENT_CONTRACT_VERSION
    upgraded["deployment_assessment"] = deployment_assessment_contract()
    validate_goal_task_spec(upgraded)
    return upgraded


def validate_deployment_assessment_contract(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("deployment_assessment must be an object")
    expected = {"required", "decisions", "reason_codes", "reassessment_events",
                "termination_event"}
    if set(value) != expected:
        raise ValueError("deployment_assessment fields mismatch")
    if dict(value) != deployment_assessment_contract():
        raise ValueError("deployment_assessment contract differs from v3")
    return deepcopy(dict(value))


def validate_assessment_evaluation(
    value: Any, *, assessment_id: str, assessment_epoch: int,
) -> dict[str, Any]:
    """Validate a trusted hidden label without exposing it to the participant."""
    if not isinstance(value, Mapping):
        raise ValueError("deployment assessment evaluation must be an object")
    expected = {"assessment_id", "assessment_epoch", "feasibility_label",
                "oracle_status", "platform_valid", "evidence_refs", "evaluator_ref"}
    if set(value) != expected:
        raise ValueError("deployment assessment evaluation fields mismatch")
    if (not isinstance(value["assessment_id"], str)
            or not value["assessment_id"]
            or type(value["assessment_epoch"]) is not int
            or value["assessment_epoch"] < 0
            or value["assessment_id"] != assessment_id
            or value["assessment_epoch"] != assessment_epoch):
        raise ValueError("deployment assessment evaluation is bound to a stale epoch")
    label = value["feasibility_label"]
    status = value["oracle_status"]
    if label is not None and label not in DEPLOYMENT_DECISIONS:
        raise ValueError("deployment assessment feasibility_label is invalid")
    if status not in DEPLOYMENT_ORACLE_STATUSES:
        raise ValueError("deployment assessment oracle_status is invalid")
    if type(value["platform_valid"]) is not bool:
        raise ValueError("deployment assessment platform_valid must be boolean")
    refs = value["evidence_refs"]
    if (not isinstance(refs, list)
            or any(not isinstance(ref, str) or not ref.strip() for ref in refs)
            or len(refs) != len(set(refs))):
        raise ValueError("deployment assessment evaluation evidence_refs are invalid")
    if (not isinstance(value["evaluator_ref"], str)
            or not value["evaluator_ref"].strip()):
        raise ValueError("deployment assessment evaluator_ref must be nonempty")
    if status == "failed" and label is not None:
        raise ValueError("failed deployment oracle must use a null feasibility_label")
    if status == "verified":
        if not value["platform_valid"]:
            raise ValueError("verified deployment oracle requires a valid platform")
        if label not in DEPLOYMENT_DECISIONS:
            raise ValueError("verified deployment oracle requires a binary feasibility_label")
        if not refs:
            raise ValueError("verified deployment oracle requires evidence_refs")
    return deepcopy(dict(value))


def finite_nonnegative(value: Any, label: str) -> float:
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value < 0):
        raise ValueError(f"{label} must be a finite nonnegative number")
    return float(value)


__all__ = [
    "DEPLOYMENT_CONTRACT_VERSION", "DEPLOYMENT_DECISIONS",
    "DEPLOYMENT_REASON_CODES", "DEPLOYMENT_REASSESSMENT_EVENTS",
    "DEPLOYMENT_ORACLE_STATUSES", "deployment_assessment_contract",
    "enable_deployment_assessment", "validate_deployment_assessment_contract",
    "validate_assessment_evaluation",
]
