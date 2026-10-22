"""Frozen rerun eligibility; scientific identity is owned by the caller.

An execution attempt is not a new statistical sample. In measured scopes the
collector may only repeat explicitly registered infrastructure recovery, with
readback of the same checkpoint and random stream. Method failures remain
outcomes. Fresh-process reconstruction and skill-substream reruns require new
upstream registration; this module cannot infer their scientific identity.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .models import BranchJob, MEASURED_SCOPES


METHOD_TERMINALS = frozenset({
    "method_timeout", "malformed_action", "budget_overrun", "missing_response",
    "safe_stop", "team_state_infeasible", "right_censored", "terminal_failure",
    "method_failure",
})
INFRASTRUCTURE_STATUSES = frozenset({"infrastructure_invalid", "simulator_error", "evaluator_error"})
REGISTRATION_KEY = "registered_recovery"
SCHEMA = "heterocoord.registered_recovery.v1"
REQUIRED_PARTICIPANTS = frozenset({
    "world", "clock", "controller", "skill", "task", "algorithm",
    "transport", "locks", "events", "rng",
})


def is_method_outcome(row: Mapping[str, Any]) -> bool:
    status = row.get("method_status", row.get("status"))
    return (status in METHOD_TERMINALS
            or (row.get("method_completed") is False and status not in INFRASTRUCTURE_STATUSES))


@dataclass(frozen=True)
class RecoveryDecision:
    retry: bool
    reason: str


def recovery_registration(job: BranchJob) -> Mapping[str, Any] | None:
    value = job.metadata.get(REGISTRATION_KEY)
    if value is None:
        return None
    if not isinstance(value, Mapping) or value.get("schema") != SCHEMA:
        raise ValueError("invalid registered_recovery schema")
    if value.get("mode") != "same_checkpoint_random_stream":
        raise ValueError("fresh-process/skill-substream retries require new upstream registration")
    for name in ("registration_id", "checkpoint_digest", "random_stream_digest"):
        if not isinstance(value.get(name), str) or not value[name].strip():
            raise ValueError(f"registered recovery requires {name}")
    reasons = value.get("infrastructure_reasons")
    if (not isinstance(reasons, (list, tuple)) or not reasons
            or any(not isinstance(item, str) or not item.strip() for item in reasons)):
        raise ValueError("registered recovery requires infrastructure_reasons")
    participants = value.get("participant_state_digests")
    if (not isinstance(participants, Mapping) or not participants
            or any(not isinstance(key, str) or not key.strip() or not isinstance(digest, str) or not digest.strip()
                   for key, digest in participants.items())):
        raise ValueError("registered recovery requires participant_state_digests")
    missing = REQUIRED_PARTICIPANTS.difference(participants)
    if missing:
        raise ValueError(f"registered recovery missing participants: {sorted(missing)}")
    if value["checkpoint_digest"] != job.checkpoint_digest:
        raise ValueError("registered recovery checkpoint differs from frozen job")
    maximum = value.get("max_attempts")
    if type(maximum) is not int or maximum < 2:
        raise ValueError("registered recovery max_attempts must include the original and at least one rerun")
    if job.metadata.get("checkpoint_receipt_mode") == "fresh_initialization_v1":
        raise ValueError("fresh initialization cannot use same-checkpoint recovery")
    return value


def decide_recovery(job: BranchJob, prior: Mapping[str, Any] | None, *, next_attempt: int) -> RecoveryDecision:
    """Decide against the last attempt; callers also retain earlier failures."""
    if prior is not None and prior.get("completed") is True:
        return RecoveryDecision(False, "completed_outcome_retained")
    if job.execution_scope not in MEASURED_SCOPES:
        return RecoveryDecision(True, "synthetic_retry")
    if prior is None:
        return RecoveryDecision(False, "unknown_attempt_requires_upstream_review")
    # A failed evaluator/cleanup cannot erase a sealed method failure.
    if is_method_outcome(prior):
        return RecoveryDecision(False, "method_outcome_retained")
    if prior.get("status") not in INFRASTRUCTURE_STATUSES:
        return RecoveryDecision(False, "unclassified_outcome_requires_upstream_review")
    registration = recovery_registration(job)
    if registration is None:
        return RecoveryDecision(False, "registered_recovery_required")
    if prior.get("infrastructure_reason") not in registration["infrastructure_reasons"]:
        return RecoveryDecision(False, "infrastructure_reason_not_registered")
    if next_attempt >= registration["max_attempts"]:
        return RecoveryDecision(False, "registered_attempt_limit_reached")
    return RecoveryDecision(True, "registered_infrastructure_recovery")


def verify_recovery_readback(job: BranchJob, receipt: Mapping[str, Any]) -> bool:
    registration = recovery_registration(job)
    return bool(registration and receipt.get("restore_passed") is True
                and receipt.get("readback_digest") == registration["checkpoint_digest"]
                and receipt.get("recovery_registration_id") == registration["registration_id"]
                and receipt.get("random_stream_digest") == registration["random_stream_digest"]
                and receipt.get("participant_state_digests") == registration["participant_state_digests"]
                and receipt.get("recovery_mode") == registration["mode"])
