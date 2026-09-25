"""Explicit raw scoring facts for the full-eight, dual-time Restaurant archive.

This extension does not reinterpret synchronous progress-v1 or authenticate an
arbitrary file as native evidence. It recomputes the business-v3 fact chain and
retains its physical and participant-availability clocks separately.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import json
import math
from pathlib import Path
from typing import Any

from .provenance import canonical_sha256


ARCHIVE_SCHEMA = "eai.restaurant.business_fact_sources"
FACT_SCHEMA = "eai.restaurant.business_scoring_facts"
ARCHIVE_NAME = "restaurant_business_facts.json"
FLAGS = {"scoreable": False, "execution_validated": False,
         "business_outcomes_validated": False, "feasibility_oracle_verified": False,
         "release_ready": False}
AVAILABILITY_EVENTS = frozenset({
    "public_observation_host_consumed", "participant_result_host_consumed",
})
INPUT_KEYS = frozenset({
    "public_task", "deployment_companion", "episode_metadata", "context_report",
    "session_report", "audit_journal", "audit_replay", "pump_report",
    "communication_report", "communication_replay", "archived_source_sha256",
    "expected_source_sha256",
})


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a nonempty string")
    return value


def _time(value: Any, label: str) -> float:
    if (isinstance(value, bool) or not isinstance(value, (float, int))
            or not math.isfinite(value) or value < 0):
        raise ValueError(f"{label} must be finite and nonnegative")
    return float(value)


def _refs(value: Any, label: str) -> list[str]:
    if (not isinstance(value, list) or not value
            or any(not isinstance(ref, str) or not ref.strip() for ref in value)
            or len(set(value)) != len(value)):
        raise ValueError(f"{label} must have unique nonempty source references")
    return deepcopy(value)


def build_restaurant_business_facts(
    public_task: Mapping[str, Any], *, episode_id: str,
    verifier_inputs: Mapping[str, Any],
) -> dict[str, Any]:
    """Recompute a source-bound, unweighted set of observed milestone facts.

    A milestone fact says what was measured at that instant, not that its truth
    persisted continuously afterwards. Source hash equality is an integrity
    check; independent native provenance and formal qualification remain open.
    """
    from .goal_restaurant_business_v3 import verify_restaurant_business_v3

    episode_id = _text(episode_id, "episode_id")
    if not isinstance(public_task, Mapping):
        raise ValueError("public_task must be an object")
    if not isinstance(verifier_inputs, Mapping) or set(verifier_inputs) != INPUT_KEYS:
        raise ValueError("business fact sources must have the exact verifier inputs")
    if any(not isinstance(value, Mapping) for value in verifier_inputs.values()):
        raise ValueError("business fact input documents must be objects")
    task_hash = canonical_sha256(public_task)
    if canonical_sha256(verifier_inputs["public_task"]) != task_hash:
        raise ValueError("business fact source belongs to a different public task")
    if verifier_inputs["session_report"].get("episode_id") != episode_id:
        raise ValueError("business fact source belongs to a different episode")
    review = verify_restaurant_business_v3(**verifier_inputs)
    if (review.get("episode_id") != episode_id
            or review.get("archive_facts_verified") is not True):
        raise ValueError("business fact source lacks a matching verified review")
    if any(review.get(key) is not False for key in FLAGS):
        raise ValueError("business fact source must not promote release flags")
    predicates = [_text(row.get("id"), "required outcome id")
                  for row in public_task["required_outcomes"]]
    milestones = review.get("milestones")
    if (len(predicates) != len(set(predicates)) or not predicates
            or not isinstance(milestones, Mapping)
            or set(milestones) != set(predicates)):
        raise ValueError("business facts must cover every required outcome exactly")
    facts = []
    unknown = []
    unavailable = []
    for predicate_id in sorted(predicates):
        row = milestones[predicate_id]
        if not isinstance(row, Mapping):
            raise ValueError("business milestone must be an object")
        status = row.get("status")
        if status == "missing_evidence":
            if row.get("physical_time_s") is not None or row.get("available_at_s") is not None:
                raise ValueError("missing business evidence cannot supply measured times")
            unknown.append(predicate_id)
            continue
        if status != "verified":
            raise ValueError("unknown business milestone status")
        physical = _time(row.get("physical_time_s"), "milestone physical time")
        availability_status = row.get("availability_status")
        if availability_status == "delivered":
            available = _time(row.get("available_at_s"), "milestone available time")
            if available < physical:
                raise ValueError("business evidence cannot be available before physical measurement")
        elif availability_status == "not_delivered" and row.get("available_at_s") is None:
            available = None
            unavailable.append(predicate_id)
        else:
            raise ValueError("business availability status and measurement disagree")
        refs = _refs(row.get("evidence_refs"), "business milestone evidence_refs")
        available_kind = row.get("availability_event_kind")
        if not isinstance(available_kind, str) or available_kind not in AVAILABILITY_EVENTS:
            raise ValueError("unknown business milestone availability event kind")
        facts.append({"predicate_id": predicate_id, "satisfied_at_measurement": True,
                      "physical_time_s": physical, "available_at_s": available,
                      "availability_status": availability_status,
                      "availability_event_kind": available_kind,
                      "result_availability_lag_s": (
                          available - physical if available is not None else None),
                      "evidence_refs": refs,
                      "task_ids": _refs(row.get("task_ids"), "business milestone task_ids"),
                      "message_ids": _refs(row.get("message_ids"), "business milestone message_ids")})
    facts.sort(key=lambda row: (row["physical_time_s"], row["predicate_id"]))
    return {
        "schema": FACT_SCHEMA, "version": 1, "episode_id": episode_id,
        "public_task_sha256": task_hash, "status": "partial" if unknown else "observed",
        "raw_only": True, "not_protocol_score": True, **FLAGS,
        "formal_statistics_eligible": False,
        "business_review": deepcopy(review),
        "business_review_sha256": canonical_sha256(review),
        "source_sha256": {key: canonical_sha256(value)
                          for key, value in sorted(verifier_inputs.items())},
        "physical_milestone_facts": facts,
        "unknown_predicate_ids": unknown,
        "unavailable_predicate_ids": unavailable,
        "public_availability_complete": not unknown and not unavailable,
        "initial_predicate_truth": {key: None for key in sorted(predicates)},
        "sampling_scope": "verified_milestone_instants_not_continuous_truth",
        "clock_scope": "native_physical_completion_and_host_public_availability",
        "integrity_scope": "recomputed_archive_consistency_not_native_authentication",
        "weighted_progress": None, "protocol_score": None,
        "missing_inputs": ["verified_initial_predicate_truth", "continuous_transition_coverage",
                           "frozen_milestone_weights", "independent_native_qualification",
                           "task_exact_communication_treatment", "registered_binary_oracle"],
    }


def build_restaurant_business_fact_archive(
    public_task: Mapping[str, Any], *, episode_id: str,
    verifier_inputs: Mapping[str, Any],
) -> dict[str, Any]:
    facts = build_restaurant_business_facts(
        public_task, episode_id=episode_id, verifier_inputs=verifier_inputs)
    result = {"schema": ARCHIVE_SCHEMA, "version": 1, "episode_id": episode_id,
              "public_task_sha256": canonical_sha256(public_task),
              "verifier_inputs": deepcopy(dict(verifier_inputs)), "facts": facts,
              "raw_only": True, "not_protocol_score": True, **FLAGS}
    result["archive_sha256"] = canonical_sha256(result)
    return result


def replay_restaurant_business_fact_archive(
    archive: Mapping[str, Any], public_task: Mapping[str, Any], *, episode_id: str,
) -> dict[str, Any]:
    if not isinstance(archive, Mapping):
        raise ValueError("business fact archive must be an object")
    # Replay the actual verifier; rehashing edited output cannot make it valid.
    expected = build_restaurant_business_fact_archive(
        public_task, episode_id=episode_id, verifier_inputs=archive.get("verifier_inputs"))
    if canonical_sha256(archive) != canonical_sha256(expected):
        raise ValueError("business fact archive differs from independent recomputation")
    return expected["facts"]


def restaurant_business_facts_requested(output_root: str | Path) -> bool:
    root = Path(output_root)
    if (root / ARCHIVE_NAME).exists():
        return True
    try:
        manifest = json.loads((root / "episode_manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(manifest, Mapping) and "restaurant_business_facts" in manifest


def restaurant_business_fact_observations(
    output_root: str | Path, public_task: Mapping[str, Any], *, episode_id: str,
) -> dict[str, Any]:
    """An optional source failure is isolated from other scoring-sidecar facts."""
    base = {"raw_only": True, "not_protocol_score": True, **FLAGS,
            "formal_statistics_eligible": False}
    path = Path(output_root) / ARCHIVE_NAME
    if not path.is_file():
        return {**base, "status": "missing", "report": None,
                "quality_failure": "restaurant_business_fact_archive_missing"}
    try:
        archive = json.loads(path.read_text(encoding="utf-8"))
        facts = replay_restaurant_business_fact_archive(
            archive, public_task, episode_id=episode_id)
    except (OSError, ValueError, TypeError, KeyError, IndexError) as exc:
        return {**base, "status": "invalid", "report": None,
                "quality_failure": f"restaurant_business_fact_archive_invalid:{type(exc).__name__}"}
    return {**base, "status": facts["status"], "report": facts,
            "archive_sha256": archive["archive_sha256"], "quality_failure": None}


def export_restaurant_business_facts(
    output_root: str | Path, public_task: Mapping[str, Any], *, episode_id: str,
    verifier_inputs: Mapping[str, Any],
) -> dict[str, Any]:
    """Export a verified diagnostic into a *new* standard scoring directory.

    Original native archives remain caller-owned and unchanged. This operation
    exports observed host/transport facts, never missing activation or compute
    measurements, and refuses to overwrite any existing output directory.
    """
    from .goal_cost_evidence import build_cost_evidence, build_cost_source_bindings
    from .goal_scoring_adapter import build_scoring_adapter_metadata

    archive = build_restaurant_business_fact_archive(
        public_task, episode_id=episode_id, verifier_inputs=verifier_inputs)
    session = verifier_inputs["session_report"]
    payloads = {
        ARCHIVE_NAME: archive,
        "public_task.json": public_task,
        "goal_session_report.json": {"episode_id": episode_id, "report": session},
        "goal_session_audit.json": verifier_inputs["audit_journal"],
        "goal_session_audit_replay.json": verifier_inputs["audit_replay"],
        "communication_transport_report.json": verifier_inputs["communication_report"],
        "communication_transport_replay.json": verifier_inputs["communication_replay"],
        "participant_trace.json": {"episode_id": episode_id,
                                   "events": session["events"], "actions": session["actions"]},
        "skill_receipts.json": [row["receipt"] for row in session["tasks"].values()
                                if row.get("receipt") is not None],
        "episode_manifest.json": {
            "schema": "eai.restaurant.business_fact_export", "version": 1,
            "episode_id": episode_id, "public_task_sha256": canonical_sha256(public_task),
            "restaurant_business_facts": {"version": 1, "archive": ARCHIVE_NAME},
            "source_sha256": archive["facts"]["source_sha256"],
            "scope": "offline_diagnostic_facts_not_formal_native_qualification", **FLAGS,
        },
    }
    encoded = {name: json.dumps(value, ensure_ascii=False, allow_nan=False) + "\n"
               for name, value in payloads.items()}
    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=False)
    for name, value in encoded.items():
        (root / name).write_text(value, encoding="utf-8")

    def write(name: str, value: Any) -> None:
        (root / name).write_text(json.dumps(value, ensure_ascii=False, allow_nan=False) + "\n",
                                 encoding="utf-8")

    task_hash = canonical_sha256(public_task)
    write("goal_cost_source_bindings.json", build_cost_source_bindings(
        root, episode_id=episode_id, task_sha256=task_hash))
    write("cost_evidence.json", build_cost_evidence(
        root, episode_id=episode_id, task_sha256=task_hash))
    metadata = build_scoring_adapter_metadata(public_task, episode_id=episode_id, output_root=root)
    write("scoring_adapter_metadata.json", metadata)
    return metadata
