"""Collect and review runtime reassessment inputs from episode archives.

The reassessment oracle only labels runtime epochs through explicitly issued
extensions (``eai.restaurant.reassessment_extension``).  This module is the
collection/review half of that issuance pipeline (mirroring the Stage60
qualification review→issue pattern):

1. ``collect_reassessment_epoch_inputs`` replays a formal episode archive's
   deployment-assessment history into per-epoch ``run_state_domain`` rows —
   every field comes from the episode's own recorded snapshot, never from a
   participant declaration.
2. ``review_reassessment_collection`` independently recomputes order,
   version, roster, trigger and budget consistency; any violation is a
   quality failure and the review digest is only issued for a clean
   collection (fail-closed, no partial acceptance).
3. ``build_extension_input`` assembles the issuer-facing input bound to the
   initial certificate digest.  Issuing the extension itself remains a
   separate trusted authority; nothing here promotes release flags.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from .provenance import canonical_sha256

COLLECTION_SCHEMA = "eai.benchmark.restaurant_reassessment_collection"
COLLECTION_VERSION = 1
REVIEW_SCHEMA = "eai.benchmark.restaurant_reassessment_review"
REVIEW_VERSION = 1
EXTENSION_INPUT_SCHEMA = "eai.benchmark.restaurant_reassessment_extension_input"
EXTENSION_INPUT_VERSION = 1
_BUDGET_TOTAL_MS = 240_000
_TRIGGERS = frozenset({"member_exit", "capability_change",
                       "milestone_state_change"})
_RELEASE = {"execution_validated": False, "business_outcomes_validated": False,
            "scoreable": False}


def _unwrap(report: Mapping[str, Any]) -> Mapping[str, Any]:
    if not isinstance(report, Mapping):
        raise ValueError("session report must be a mapping")
    inner = report.get("report")
    return inner if isinstance(inner, Mapping) else report


def collect_reassessment_epoch_inputs(
    session_report: Mapping[str, Any], *, episode_id: str,
) -> dict[str, Any]:
    """Replay deployment history (epoch >= 1) into run-state domain rows."""
    report = _unwrap(session_report)
    resolved = episode_id or report.get("episode_id")
    if not isinstance(resolved, str) or not resolved.strip():
        raise ValueError("episode_id must be a nonempty string")
    if report.get("episode_id") not in (None, resolved):
        raise ValueError("session report episode identity mismatch")
    gate = report.get("deployment_assessment") or {}
    history = gate.get("history") or []
    if not isinstance(history, list):
        raise ValueError("deployment history must be a list")
    rows: list[dict[str, Any]] = []
    for row in history:
        if not isinstance(row, Mapping):
            continue
        epoch = row.get("assessment_epoch")
        if type(epoch) is not int or epoch < 1:
            continue  # runtime epochs only; epoch 0 stays with the initial oracle
        triggered_at = row.get("triggered_at_s")
        if type(triggered_at) not in (int, float):
            raise ValueError(f"epoch {epoch} lacks triggered_at_s")
        agents = row.get("current_agent_ids")
        if not isinstance(agents, list) or not agents:
            raise ValueError(f"epoch {epoch} lacks current_agent_ids")
        rows.append({
            "assessment_epoch": epoch,
            "trigger_event_type": row.get("trigger_event_type"),
            "measurement_id": f"restaurant_runtime_epoch_{epoch}",
            "measurement_point": {
                "required_true": [],  # fact partition fixed at issuance review
                "required_false": [],
                "varying_facts": [],
            },
            "time_s": float(triggered_at),
            "remaining_time_ms": _BUDGET_TOTAL_MS - int(round(
                float(triggered_at) * 1000)),
            "max_active_agents": len(agents),
            "roster_version": row.get("roster_version"),
            "capability_versions": deepcopy(row.get("capability_versions")),
            "current_agent_ids": list(agents),
            "resource_versions": deepcopy(row.get("resource_versions")),
            "deployment_change_versions": deepcopy(
                row.get("deployment_change_versions")),
            "trigger_event_sequence": row.get("trigger_event_sequence"),
        })
    rows.sort(key=lambda item: item["assessment_epoch"])
    return {
        "schema": COLLECTION_SCHEMA, "version": COLLECTION_VERSION,
        "episode_id": resolved, "run_state_domains": rows,
        "epoch_count": len(rows), **_RELEASE,
    }


def review_reassessment_collection(
    collection: Mapping[str, Any], *,
    expected_episode_id: str | None = None,
) -> dict[str, Any]:
    """Independent offline review; a clean pass yields the review digest."""
    if not isinstance(collection, Mapping):
        raise ValueError("collection must be a mapping")
    failures: list[str] = []
    if (collection.get("schema") != COLLECTION_SCHEMA
            or collection.get("version") != COLLECTION_VERSION):
        raise ValueError("unexpected reassessment collection schema")
    episode_id = collection.get("episode_id")
    if expected_episode_id is not None and episode_id != expected_episode_id:
        failures.append("episode_identity_mismatch")
    domains = collection.get("run_state_domains")
    if not isinstance(domains, list) or not domains:
        return {"schema": REVIEW_SCHEMA, "version": REVIEW_VERSION,
                "episode_id": episode_id, "status": "empty_collection",
                "quality_failures": ["no_runtime_epochs_collected"],
                "review_digest": None, **_RELEASE}
    previous_epoch = 0
    previous_sequence: int | None = None
    previous_time = -1.0
    for domain in domains:
        epoch = domain.get("assessment_epoch")
        if type(epoch) is not int or epoch <= previous_epoch:
            failures.append("epoch_order_invalid")
            continue
        previous_epoch = epoch
        if domain.get("trigger_event_type") not in _TRIGGERS:
            failures.append(f"unknown_trigger:{epoch}")
        sequence = domain.get("trigger_event_sequence")
        if type(sequence) is not int:
            failures.append(f"trigger_sequence_missing:{epoch}")
        elif previous_sequence is not None and sequence <= previous_sequence:
            failures.append(f"trigger_sequence_not_increasing:{epoch}")
        else:
            previous_sequence = sequence
        time_s = domain.get("time_s")
        if type(time_s) not in (int, float) or time_s < previous_time:
            failures.append(f"trigger_time_not_monotonic:{epoch}")
        else:
            previous_time = float(time_s)
        remaining = domain.get("remaining_time_ms")
        if (type(remaining) is not int or remaining < 0
                or int(round(float(time_s) * 1000)) + remaining
                != _BUDGET_TOTAL_MS):
            failures.append(f"budget_inconsistent:{epoch}")
        for field in ("roster_version", "capability_versions",
                      "resource_versions", "deployment_change_versions"):
            if domain.get(field) is None:
                failures.append(f"version_field_missing:{epoch}:{field}")
        agents = domain.get("current_agent_ids")
        if not isinstance(agents, list) or not agents:
            failures.append(f"roster_empty:{epoch}")
    review = {
        "schema": REVIEW_SCHEMA, "version": REVIEW_VERSION,
        "episode_id": episode_id,
        "status": "verified" if not failures else "failed",
        "quality_failures": sorted(set(failures)),
        "reviewed_epoch_count": len(domains),
        "review_digest": None,
        **_RELEASE,
    }
    if not failures:
        review["review_digest"] = canonical_sha256({
            "schema": REVIEW_SCHEMA, "version": REVIEW_VERSION,
            "episode_id": episode_id,
            "run_state_domains": deepcopy(list(domains))})
    return review


def build_extension_input(
    collection: Mapping[str, Any], review: Mapping[str, Any], *,
    certificate_sha256: str,
) -> dict[str, Any]:
    """Assemble the issuer-facing extension input (issuance is external)."""
    if not isinstance(certificate_sha256, str) or len(certificate_sha256) != 64:
        raise ValueError("certificate_sha256 must be a 64-hex digest")
    if review.get("status") != "verified" or not review.get("review_digest"):
        raise ValueError("extension input requires a verified review digest")
    if collection.get("episode_id") != review.get("episode_id"):
        raise ValueError("collection/review episode mismatch")
    return {
        "schema": EXTENSION_INPUT_SCHEMA, "version": EXTENSION_INPUT_VERSION,
        "visibility": "evaluator_only",
        "bound_certificate_sha256": certificate_sha256,
        "episode_id": collection["episode_id"],
        "qualification": {
            "review_digest": review["review_digest"],
            "reviewed_epoch_count": review.get("reviewed_epoch_count"),
        },
        "run_state_domains": deepcopy(list(
            collection.get("run_state_domains") or [])),
        **_RELEASE,
    }


__all__ = [
    "COLLECTION_SCHEMA", "COLLECTION_VERSION", "EXTENSION_INPUT_SCHEMA",
    "EXTENSION_INPUT_VERSION", "REVIEW_SCHEMA", "REVIEW_VERSION",
    "build_extension_input", "collect_reassessment_epoch_inputs",
    "review_reassessment_collection",
]
