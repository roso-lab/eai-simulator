"""Raw-only adapter metadata for the protocol scoring sidecar.

The Goal track is still a self-decomposed engineering track.  This module
records what an episode archive contains and which protocol inputs are absent;
it deliberately does not calculate a six-axis score or assign a protocol
branch.  The sidecar is kept outside ``public_task.json`` so task hashes and
the participant contract remain unchanged.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

from .goal_deployment_assessment import DEPLOYMENT_REASSESSMENT_EVENTS
from .provenance import canonical_sha256


SCORING_ADAPTER_SCHEMA = "eai.benchmark.scoring_adapter_metadata"
SCORING_ADAPTER_VERSION = 1
PROTOCOL_MAPPING_REVISION = "protocol-20260925-goal-score-source-bindings-v5"

DIMENSION_STATUS = {
    "task_allocation": "P/X",
    "communication_adaptation": "X/M",
    "temporal_coordination": "M/P",
    "dynamic_restructuring_scale": "M",
    "dynamic_restructuring_failure": "P/M",
    "cross_episode_learning": "M",
    "cost_efficiency": "M/P",
}

SCOREABILITY_REASONS = [
    "raw_observations_are_not_protocol_scores",
    "claim_specific_native_qualification_requires_independent_review",
    "execution_and_business_validation_are_release_gated",
]

# Requirements describe the evidence needed for a claim, not the absence of
# producers or the outcome of an episode. This adapter never issues attestation.
CLAIM_REQUIREMENTS = {
    "baseline_execution": [
        "exact_task_native_execution_and_business_evidence",
        "registered_binary_remaining_feasibility_oracle",
    ],
    "communication_adaptation": [
        "native_transport_and_participant_visible_delivery_evidence",
        "preregistered_link_conditions_and_business_outcome_comparison",
    ],
    "concurrent_coordination": [
        "same_native_clock_execution_overlap_and_completion_evidence",
    ],
    "strict_same_snapshot_comparison": [
        "native_and_participant_state_restore",
        "preregistered_sham_consistency_and_branch_isolation",
    ],
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _count_json(path: Path, key: str, *, parent: str | None = None) -> int | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if parent is not None:
        value = value.get(parent) if isinstance(value, Mapping) else None
    if isinstance(value, list):
        return len(value) if key in {"items", "receipts"} else None
    item = value.get(key) if isinstance(value, Mapping) else None
    return len(item) if isinstance(item, (list, dict)) else None


def communication_analysis_observations(output_root: str | Path) -> dict[str, Any]:
    """Attach an already computed communication analysis as raw evidence.

    Capacity curves and paired distribution effects are computed by
    ``communication_analysis`` before this adapter runs.  The sidecar only
    verifies the archive identity and preserves the report; it never turns a
    curve or threshold into a protocol score.  Missing or malformed analysis
    remains an explicit infrastructure state rather than a zero-cost result.
    """
    path = Path(output_root) / "communication_analysis.json"
    base = {"raw_only": True, "not_protocol_score": True,
            "scoreable": False, "execution_validated": False,
            "business_outcomes_validated": False}
    if not path.is_file():
        return {**base, "status": "missing", "report": None,
                "report_sha256": None, "quality_failure": "analysis_archive_missing"}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            raise ValueError("analysis archive must be an object")
        if payload.get("schema") != "eai.benchmark.communication_analysis":
            raise ValueError("unexpected communication analysis schema")
        if type(payload.get("version")) is not int or payload["version"] < 1:
            raise ValueError("invalid communication analysis version")
        status = payload.get("status")
        if status not in {"ok", "missing_reference"}:
            raise ValueError("communication analysis is not a bounded summary")
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return {**base, "status": "invalid", "report": None,
                "report_sha256": None,
                "quality_failure": f"analysis_archive_invalid:{type(exc).__name__}"}
    return {**base, "status": "observed", "report": deepcopy(payload),
            "report_sha256": _sha256(path)}


def native_oracle_registry_observations(output_root: str | Path) -> dict[str, Any]:
    """Archive the evaluator-side five-scene oracle completeness report.

    The registry audit is a release gate, not a participant outcome.  This
    adapter preserves its verified/failed/invalid status and never converts a
    missing scene or failed measurement into a model classification.
    """
    path = Path(output_root) / "native_oracle_registry_audit.json"
    base = {"raw_only": True, "not_protocol_score": True,
            "scoreable": False, "execution_validated": False,
            "business_outcomes_validated": False}
    if not path.is_file():
        return {**base, "status": "missing", "report": None,
                "report_sha256": None, "quality_failure": "registry_archive_missing"}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            raise ValueError("registry archive must be an object")
        if payload.get("schema") != "eai.benchmark.native_oracle_registry_audit":
            raise ValueError("unexpected native oracle registry schema")
        if type(payload.get("version")) is not int or payload["version"] < 1:
            raise ValueError("invalid native oracle registry version")
        if payload.get("status") not in {"verified", "failed", "invalid"}:
            raise ValueError("registry status must be verified, failed, or invalid")
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return {**base, "status": "invalid", "report": None,
                "report_sha256": None,
                "quality_failure": f"registry_archive_invalid:{type(exc).__name__}"}
    return {**base, "status": "observed", "report": deepcopy(payload),
            "report_sha256": _sha256(path)}


def build_archive_index(output_root: str | Path) -> dict[str, Any]:
    """Index known raw artifacts without interpreting them as protocol scores."""
    root = Path(output_root).absolute()
    names = (
        "episode_manifest.json", "goal_session_report.json", "skill_receipts.json",
        "participant_trace.json", "hidden_evaluator_trace.json", "team_usage.json",
        "business_evidence.json", "pose_tools_report.json", "pose_communication_replay.json",
        "camera_tool_profile.json", "camera_tools_report.json",
        "camera_communication_replay.json", "camera_lifecycle_errors.json",
        "pose_tool_profile.json", "runtime_snapshots.json", "public_task.json",
        "participant_api.json", "result.json",
        "lifecycle_errors.json",
        "camera_render_failures.json",
        "deployment_assessment_report.json",
        "deployment_feasibility_evidence.json", "deployment_protocol_error.json",
        "cost_evidence.json", "goal_cost_source_bindings.json",
        "communication_analysis.json",
        "native_oracle_registry_audit.json",
        "communication_tool_profile.json", "communication_tool_report.json",
        "communication_tool_replay.json", "communication_transport_replay.json",
        "communication_transport_report.json",
        "execution_downlink_profile.json", "execution_downlink_report.json",
        "execution_observer_profile.json", "execution_observer_report.json",
        "goal_session_audit.json", "goal_session_audit_replay.json",
        "goal_progress_evidence.json", "goal_progress_replay.json",
        "goal_city_autonomous_entry.json", "goal_city_autonomous_source.json", "city_acceptance_evidence.json",
        "native_evidence_log.json", "city_progress_sources.json",
        "city_initial_qualification.json",
        "restaurant_business_facts.json",
    )
    files = {}
    for name in names:
        path = root / name
        if path.is_file():
            files[name] = {"sha256": _sha256(path), "bytes": path.stat().st_size}
    counts = {
        "events": _count_json(root / "goal_session_report.json", "events", parent="report"),
        "actions": _count_json(root / "participant_trace.json", "actions"),
        "participant_events": _count_json(root / "participant_trace.json", "events"),
        "plans": _count_json(root / "hidden_evaluator_trace.json", "plans"),
        "tasks": _count_json(root / "hidden_evaluator_trace.json", "tasks"),
        "skill_receipts": _count_json(root / "skill_receipts.json", "receipts"),
    }
    return {"schema": "eai.benchmark.raw_archive_index", "version": 1,
            "raw_only": True, "not_protocol_score": True, "files": files,
            "counts": counts}


_ASSESSMENT_EVALUATION_FIELDS = frozenset({
    "assessment_id", "assessment_epoch", "feasibility_label",
    "oracle_status", "platform_valid", "evidence_refs", "evaluator_ref",
})
_FEASIBILITY_LABELS = frozenset({"deployable", "not_deployable"})
_CLASSIFICATIONS = (
    "correct_deployment", "false_refusal", "false_deployment", "correct_refusal",
)
_LABEL_RISK_COUNT_FIELDS = (
    "required_epoch_count",
    "deployable_required_epoch_count",
    "not_deployable_required_epoch_count",
    "answered_epoch_count",
    "unanswered_epoch_count",
    "unanswered_deployable_count",
    "unanswered_not_deployable_count",
    *(f"{name}_count" for name in _CLASSIFICATIONS),
)


def _assessment_key(value: Any) -> tuple[str, int] | None:
    """Return a canonical epoch key, rejecting bool-as-int and blank IDs."""
    if not isinstance(value, Mapping):
        return None
    assessment_id = value.get("assessment_id")
    epoch = value.get("assessment_epoch")
    if (not isinstance(assessment_id, str) or not assessment_id.strip()
            or type(epoch) is not int or epoch < 0):
        return None
    return assessment_id, epoch


def _valid_oracle_evaluation(value: Any, key: tuple[str, int] | None = None) -> bool:
    """Validate the strict seven-field evaluator contract used by GoalSession."""
    if not isinstance(value, Mapping) or set(value) != _ASSESSMENT_EVALUATION_FIELDS:
        return False
    actual = _assessment_key(value)
    if actual is None or (key is not None and actual != key):
        return False
    refs = value.get("evidence_refs")
    return (
        value.get("feasibility_label") in _FEASIBILITY_LABELS
        and value.get("oracle_status") == "verified"
        and value.get("platform_valid") is True
        and isinstance(refs, list)
        and bool(refs)
        and all(isinstance(ref, str) and ref.strip() for ref in refs)
        and isinstance(value.get("evaluator_ref"), str)
        and bool(value["evaluator_ref"].strip())
    )


def _conditional_risk_rates(counts: Mapping[str, int] | None) -> dict[str, float | None]:
    """Return label-conditional error/missing rates with null zero denominators."""
    names = (
        "correct_deployment_rate", "correct_refusal_rate",
        "false_refusal_rate", "false_deployment_rate", "unanswered_rate",
        "unanswered_deployable_rate", "unanswered_not_deployable_rate",
    )
    if counts is None:
        return {name: None for name in names}

    def ratio(numerator: str, denominator: str) -> float | None:
        bottom = counts[denominator]
        return counts[numerator] / bottom if bottom else None

    return {
        "correct_deployment_rate": ratio(
            "correct_deployment_count", "deployable_required_epoch_count"),
        "correct_refusal_rate": ratio(
            "correct_refusal_count", "not_deployable_required_epoch_count"),
        "false_refusal_rate": ratio(
            "false_refusal_count", "deployable_required_epoch_count"),
        "false_deployment_rate": ratio(
            "false_deployment_count", "not_deployable_required_epoch_count"),
        "unanswered_rate": ratio("unanswered_epoch_count", "required_epoch_count"),
        "unanswered_deployable_rate": ratio(
            "unanswered_deployable_count", "deployable_required_epoch_count"),
        "unanswered_not_deployable_rate": ratio(
            "unanswered_not_deployable_count", "not_deployable_required_epoch_count"),
    }


def deployment_assessment_observations(
    session_report: Mapping[str, Any], *, task_sha256: str,
) -> dict[str, Any]:
    """Archive assessment classification facts without inventing score inputs.

    Timing is the host receipt clock. Physical change/notification transport
    latency and per-epoch byte costs remain unknown until their real sources
    are joined. Unknown or platform-invalid labels never enter accuracy counts.
    """
    if not isinstance(session_report, Mapping):
        return {"status": "invalid_report", "raw_only": True, "records": [],
                "accuracy_denominator": 0,
                "quality_failures": ["session_report_not_mapping"]}
    gate = session_report.get("deployment_assessment")
    if not isinstance(gate, Mapping) or not gate.get("enabled"):
        return {"status": "not_enabled", "raw_only": True, "records": [],
                "accuracy_denominator": None}
    quality_failures: list[str] = []
    raw_events = session_report.get("events", [])
    if not isinstance(raw_events, list):
        quality_failures.append("events_not_list")
        events = []
    else:
        events = raw_events
    raw_actions = session_report.get("actions", [])
    if not isinstance(raw_actions, list):
        quality_failures.append("actions_not_list")
        raw_actions = []
    actions = {row.get("action_id"): row for row in raw_actions
               if isinstance(row, Mapping) and isinstance(row.get("action_id"), str)}
    event_index = {row.get("sequence"): row for row in events
                   if isinstance(row, Mapping) and "sequence" in row}
    termination = gate.get("termination") or {}

    # Required-event payloads are the sample authority. Repeated delivery of
    # the same epoch is idempotent; conflicting identities or trigger types
    # fail closed instead of creating another sample.
    required_by_epoch: dict[tuple[str, int], dict[str, Any]] = {}
    id_epochs: dict[str, int] = {}
    epoch_ids: dict[int, str] = {}
    for event in events:
        if not isinstance(event, Mapping) or event.get("type") != "deployment_assessment_required":
            continue
        payload = event.get("payload")
        key = _assessment_key(payload)
        if key is None:
            quality_failures.append("malformed_required_epoch")
            continue
        assessment_id, epoch = key
        if assessment_id in id_epochs and id_epochs[assessment_id] != epoch:
            quality_failures.append("conflicting_required_epoch_identity")
        if epoch in epoch_ids and epoch_ids[epoch] != assessment_id:
            quality_failures.append("conflicting_required_epoch_identity")
        id_epochs[assessment_id] = epoch
        epoch_ids[epoch] = assessment_id
        trigger_type = payload.get("trigger_event_type")
        supported_triggers = {"episode_started", *DEPLOYMENT_REASSESSMENT_EVENTS}
        if trigger_type is not None and (
                not isinstance(trigger_type, str) or trigger_type not in supported_triggers):
            quality_failures.append("malformed_required_trigger_event_type")
            trigger_type = None
        phase = "initial" if (
            trigger_type == "episode_started" or trigger_type is None and epoch == 0
        ) else "runtime"
        phase_source = (
            "required_event_trigger_event_type"
            if trigger_type is not None else "legacy_epoch_zero_phase_rule"
        )
        previous = required_by_epoch.get(key)
        if previous is not None:
            if previous["payload_sha256"] != canonical_sha256(payload):
                quality_failures.append("conflicting_required_epoch_trigger")
            continue
        required_by_epoch[key] = {
            "phase": phase,
            "phase_source": phase_source,
            "trigger_event_type": trigger_type,
            "payload_sha256": canonical_sha256(payload),
        }
    required = set(required_by_epoch)

    raw_oracles = gate.get("oracle_evaluations", [])
    if not isinstance(raw_oracles, list):
        quality_failures.append("oracle_evaluations_not_list")
        raw_oracles = []
    by_epoch: dict[tuple[str, int], Mapping[str, Any]] = {}
    duplicate_oracle = False
    malformed_oracle = 0
    for oracle in raw_oracles:
        key = _assessment_key(oracle)
        if key is None:
            malformed_oracle += 1
            quality_failures.append("malformed_oracle_evaluation")
            continue
        if key in by_epoch:
            duplicate_oracle = True
            quality_failures.append("duplicate_oracle_epoch")
            continue
        by_epoch[key] = oracle
    valid_oracles = {key for key, oracle in by_epoch.items()
                     if key in required and _valid_oracle_evaluation(oracle, key)}
    missing_or_failed = required - valid_oracles
    if set(by_epoch) != required:
        quality_failures.append("oracle_epoch_set_mismatch")

    records: list[dict[str, Any]] = []
    raw_history = gate.get("history", [])
    if not isinstance(raw_history, list):
        quality_failures.append("history_not_list")
        raw_history = []

    gate_binding_present = "public_task_sha256" in gate
    gate_digest = gate.get("public_task_sha256")
    started_binding_values = [
        event.get("payload", {}).get("public_task_digest")
        for event in events
        if isinstance(event, Mapping) and event.get("type") == "episode_started"
        and isinstance(event.get("payload"), Mapping)
        and "public_task_digest" in event["payload"]
    ]
    history_digests = {
        row.get("public_task_sha256") for row in raw_history
        if isinstance(row, Mapping) and isinstance(row.get("public_task_sha256"), str)
    }
    history_binding_valid = not raw_history or history_digests == {task_sha256}
    started_binding_valid = (
        not started_binding_values
        or all(isinstance(value, str) and value == task_sha256
               for value in started_binding_values)
    )
    if gate_binding_present:
        task_binding_source = "deployment_gate_public_task_sha256"
        task_binding_valid = (isinstance(gate_digest, str)
                              and gate_digest == task_sha256
                              and started_binding_valid and history_binding_valid)
    elif started_binding_values:
        task_binding_source = "episode_started_public_task_digest"
        task_binding_valid = started_binding_valid and history_binding_valid
    else:
        task_binding_source = "legacy_consistent_history_public_task_sha256"
        task_binding_valid = bool(raw_history) and history_digests == {task_sha256}
    if not task_binding_valid:
        quality_failures.append("deployment_task_binding_invalid")
    seen: set[tuple[str, int]] = set()
    answered_required: set[tuple[str, int]] = set()
    for row in raw_history:
        if not isinstance(row, Mapping):
            quality_failures.append("malformed_history_entry")
            row = {}
        key = _assessment_key(row)
        if key is None:
            quality_failures.append("malformed_history_key")
            # Keep malformed raw rows diagnosable without allowing arbitrary
            # JSON values (for example a list ID) to break set bookkeeping.
            key = (None, len(records))
        epoch = key[1]
        duplicate = key[0] is not None and key in seen
        if key[0] is not None:
            seen.add(key)
        if duplicate:
            quality_failures.append("duplicate_history_epoch")
        required_epoch = key[0] is not None and key in required
        if key[0] is not None and not required_epoch:
            quality_failures.append("history_epoch_not_required")
        if required_epoch and not duplicate:
            answered_required.add(key)
        external = row.get("external_evaluation")
        external_valid = _valid_oracle_evaluation(external, key if key[0] is not None else None)
        if not external_valid:
            quality_failures.append("history_external_evaluation_invalid")
        task_bound = row.get("public_task_sha256") == task_sha256
        if not task_bound:
            quality_failures.append("history_task_binding_mismatch")
        cached = by_epoch.get(key) if key[0] is not None else None
        cached_matches = (external_valid and _valid_oracle_evaluation(cached, key)
                          and dict(external) == dict(cached))
        if required_epoch and not cached_matches:
            quality_failures.append("history_oracle_cache_mismatch")
        label = external.get("feasibility_label") if external_valid else None
        decision = row.get("decision")
        classification = None
        if (external_valid and task_bound and not duplicate and required_epoch
                and cached_matches):
            classification = {
                ("deployable", "deployable"): "correct_deployment",
                ("deployable", "not_deployable"): "false_refusal",
                ("not_deployable", "deployable"): "false_deployment",
                ("not_deployable", "not_deployable"): "correct_refusal",
            }.get((label, decision))
        if required_epoch and classification is None:
            quality_failures.append("history_decision_invalid")
        valid = (required_epoch and not duplicate and task_bound
                 and cached_matches and classification is not None)
        trigger = event_index.get(row.get("trigger_event_sequence"), {})
        if not isinstance(trigger, Mapping):
            trigger = {}
        start, declared = trigger.get("time_s"), row.get("declared_at_s")
        def finite(value):
            return type(value) in (float, int) and math.isfinite(value)
        latency = (declared - start if finite(start) and finite(declared) and declared >= start else None)
        before = []
        declaration_found = False
        for event in events:
            if not isinstance(event, Mapping):
                continue
            if event.get("sequence", -1) < row.get("trigger_event_sequence", 0):
                continue
            payload = event.get("payload", {})
            if not isinstance(payload, Mapping):
                continue
            if event.get("type") != "action_result" or payload.get("status") != "accepted":
                continue
            if payload.get("action_id") == row.get("decision_action_id"):
                declaration_found = True
                break
            accepted = actions.get(payload.get("action_id"))
            if accepted:
                before.append(accepted)
        counts = {kind: sum(isinstance(a, Mapping) and a.get("type") == kind for a in before) for kind in (
            "select_team", "submit_plan", "revise_plan", "dispatch_skill", "consume_message")}
        requested = termination.get("assessment_id") == key[0]
        required_meta = required_by_epoch.get(key, {})
        records.append({
            "assessment_id": key[0], "assessment_epoch": epoch,
            "phase": required_meta.get(
                "phase", "initial" if epoch == 0 else "runtime"),
            "phase_source": required_meta.get(
                "phase_source", "legacy_epoch_zero_phase_rule"),
            "classification": classification, "label_eligible": valid and classification is not None,
            "oracle_status": "verified" if external_valid else "failed",
            "platform_valid": external.get("platform_valid") if isinstance(external, Mapping) else None,
            "decision": decision, "feasibility_label": label if valid else None, "duplicate_epoch": duplicate,
            "cached_oracle_matches_history": cached_matches,
            "host_declaration_latency_s": latency,
            "physical_change_latency_s": None, "notification_delivery_latency_s": None,
            "human_assistance_requested": requested,
            "stopped_before_epoch_dispatch": (counts["dispatch_skill"] == 0
                if requested and declaration_found else None),
            "pre_declaration_accepted_action_counts": counts if declaration_found else None,
            "pre_declaration_communication_bytes": None,
            "pre_declaration_activation_cost": None,
            "pre_declaration_planning_compute_cost": None,
            "business_task_completed": False if requested else None,
        })
    unanswered = sorted(required - answered_required, key=lambda pair: pair[1])
    episode_eligible = (bool(required) and not missing_or_failed and not duplicate_oracle
                        and set(by_epoch) == required
                        and not quality_failures)
    infrastructure_failures = len(missing_or_failed) + malformed_oracle
    if duplicate_oracle:
        infrastructure_failures += 1
    if quality_failures and infrastructure_failures == 0:
        infrastructure_failures = 1

    record_by_epoch = {
        (record["assessment_id"], record["assessment_epoch"]): record
        for record in records
        if record["assessment_id"] is not None and not record["duplicate_epoch"]
    }

    def risk_counts(keys: set[tuple[str, int]]) -> dict[str, int]:
        labels = {key: by_epoch[key]["feasibility_label"] for key in keys}
        phase_answered = keys & answered_required
        counts = {
            "required_epoch_count": len(keys),
            "deployable_required_epoch_count": sum(
                label == "deployable" for label in labels.values()),
            "not_deployable_required_epoch_count": sum(
                label == "not_deployable" for label in labels.values()),
            "answered_epoch_count": len(phase_answered),
            "unanswered_epoch_count": len(keys - phase_answered),
            "unanswered_deployable_count": sum(
                labels[key] == "deployable" for key in keys - phase_answered),
            "unanswered_not_deployable_count": sum(
                labels[key] == "not_deployable" for key in keys - phase_answered),
        }
        counts.update({
            f"{name}_count": sum(
                record_by_epoch[key]["classification"] == name
                for key in phase_answered)
            for name in _CLASSIFICATIONS
        })
        return counts

    label_risks = risk_counts(required) if episode_eligible else None
    phase_risks = {
        phase: risk_counts({
            key for key, meta in required_by_epoch.items() if meta["phase"] == phase
        }) if episode_eligible else None
        for phase in ("initial", "runtime")
    }
    conditional_rates = _conditional_risk_rates(label_risks)
    rates_by_phase = {
        phase: _conditional_risk_rates(phase_risks[phase])
        for phase in ("initial", "runtime")
    }
    classification_counts = {
        name: (label_risks[f"{name}_count"] if label_risks is not None else 0)
        for name in _CLASSIFICATIONS
    }
    all_epoch_denominator = len(required) if episode_eligible else 0
    return {"status": gate.get("status"), "raw_only": True, "not_protocol_score": True,
            "records": records, "accuracy_denominator": len(required) if episode_eligible else 0,
            "accuracy_denominator_scope": "all_required_epochs_of_oracle_verified_episodes_including_missing_answers",
            "episode_oracle_eligible": episode_eligible,
            "excluded_from_deployment_metrics": not episode_eligible,
            "task_binding_source": task_binding_source,
            "task_binding_valid": task_binding_valid,
            "required_epoch_count": len(required) if required else None,
            "quality_failures": sorted(set(quality_failures)),
            "unanswered_epochs": [{"assessment_id": key[0], "assessment_epoch": key[1],
                                   "phase": required_by_epoch[key]["phase"],
                                   "feasibility_label": (by_epoch[key].get("feasibility_label")
                                                         if key in valid_oracles else None)}
                                   for key in unanswered],
            "classification_counts": classification_counts,
            "label_risk_counts": label_risks,
            "label_risk_counts_by_phase": phase_risks,
            "conditional_rates": conditional_rates,
            "conditional_rates_by_phase": rates_by_phase,
            "all_epoch_false_refusal_share": (
                classification_counts["false_refusal"] / all_epoch_denominator
                if all_epoch_denominator else None),
            "all_epoch_false_deployment_share": (
                classification_counts["false_deployment"] / all_epoch_denominator
                if all_epoch_denominator else None),
            "oracle_infrastructure_failure_count": infrastructure_failures,
            "missing_assessment_count": len(unanswered) if episode_eligible else None,
            "undeclared_current_epoch": gate.get("status") == "required",
            "clock_source": "goal_host_receipt_clock", "physical_clock_bound": False,
            "scoreable": False}


def plan_admission_observations(session_report: Mapping[str, Any]) -> dict[str, Any]:
    """Summarize participant plan admission facts without judging decomposition.

    Rejected ``submit_plan``/``revise_plan`` attempts are recorded by the
    trusted GoalSession before the host fails closed.  The denominator includes
    accepted and rejected attempts; this is a diagnostic profile for the
    participant's own plan, not a fixed-subtask score.
    """
    if not isinstance(session_report, Mapping):
        raise ValueError("session_report must be an object")
    actions = session_report.get("actions", [])
    rejections = session_report.get("action_rejections", [])
    if not isinstance(actions, list) or not isinstance(rejections, list):
        raise ValueError("session report plan admission records must be lists")
    accepted = [row for row in actions
                if isinstance(row, Mapping)
                and row.get("type") in {"submit_plan", "revise_plan"}]
    rejected = [row for row in rejections
                if isinstance(row, Mapping)
                and row.get("type") in {"submit_plan", "revise_plan"}]

    def category(row: Mapping[str, Any]) -> str:
        code = row.get("reason_code")
        stable = {
            "resource_conflict": "resource_conflict",
            "load_conflict": "load",
            "capability_or_team": "capability",
            "budget_exhausted": "budget",
        }
        if code in stable:
            return stable[code]
        reason = row.get("reason")
        text = str(reason).lower()
        if any(token in text for token in ("capability", "assigned", "team", "agent")):
            return "capability"
        if any(token in text for token in ("load", "capacity", "max_active", "busy")):
            return "load"
        if any(token in text for token in ("resource", "conflict", "reservation", "overlap")):
            return "resource_conflict"
        if any(token in text for token in ("budget", "window", "time", "duration")):
            return "budget"
        return "contract_or_other"

    counts = {name: 0 for name in
              ("capability", "load", "resource_conflict", "budget", "contract_or_other")}
    for row in rejected:
        counts[category(row)] += 1
    attempts = len(accepted) + len(rejected)
    revision_attempts = sum(row.get("type") == "revise_plan"
                            for row in accepted + rejected)
    accepted_revisions = sum(row.get("type") == "revise_plan" for row in accepted)
    ratio = lambda numerator, denominator: (numerator / denominator if denominator else None)
    return {
        "status": "observed",
        "raw_only": True,
        "not_protocol_score": True,
        "accepted_plan_attempt_count": len(accepted),
        "rejected_plan_attempt_count": len(rejected),
        "plan_attempt_count": attempts,
        "plan_rejection_rate": ratio(len(rejected), attempts),
        "rejection_counts_by_category": counts,
        "resource_conflict_rate": ratio(counts["resource_conflict"], attempts),
        "resource_conflict_rate_among_rejections": ratio(
            counts["resource_conflict"], len(rejected)),
        "revision_attempt_count": revision_attempts,
        "accepted_revision_count": accepted_revisions,
        "revision_survival_rate": ratio(accepted_revisions, revision_attempts),
        "rejections": deepcopy(rejected),
    }


def task_allocation_observations(
    deployment: Mapping[str, Any], plan_admission: Mapping[str, Any],
) -> dict[str, Any]:
    """Join deployment-gate and self-authored plan diagnostics.

    This is a raw diagnostic profile for the task-allocation dimension.  It
    keeps the deployment denominator fail-closed: an episode whose hidden
    remaining-task oracle is not eligible contributes no deployment rate, but
    its plan admission facts remain available for diagnosis.  The function
    deliberately does not calculate a protocol score or treat a rejected
    participant plan as a decomposition error.
    """
    if not isinstance(deployment, Mapping) or not isinstance(plan_admission, Mapping):
        raise ValueError("task allocation observations require two mapping inputs")
    eligible = deployment.get("episode_oracle_eligible") is True
    quality_failures = ([] if eligible else ["deployment_oracle_ineligible"])

    denominator_value = deployment.get("accuracy_denominator")
    denominator_valid = (type(denominator_value) is int and denominator_value >= 0)
    denominator = denominator_value if denominator_valid else None
    if eligible and not denominator_valid:
        quality_failures.append("deployment_accuracy_denominator_invalid")

    raw_counts = deployment.get("classification_counts")
    legacy_counts_valid = (
        isinstance(raw_counts, Mapping)
        and all(type(raw_counts.get(name)) is int and raw_counts[name] >= 0
                for name in _CLASSIFICATIONS)
    )
    legacy_counts = (
        {name: raw_counts[name] for name in _CLASSIFICATIONS}
        if legacy_counts_valid else None
    )
    if (eligible and (not legacy_counts_valid or denominator is None
                      or sum(legacy_counts.values()) > denominator)):
        legacy_counts = None
        quality_failures.append("deployment_classification_counts_invalid")

    def validated_risk_counts(value: Any, *, expected_total: int | None,
                              expected_classes: Mapping[str, int] | None,
                              ) -> dict[str, int] | None:
        if (not isinstance(value, Mapping)
                or any(type(value.get(name)) is not int or value[name] < 0
                       for name in _LABEL_RISK_COUNT_FIELDS)):
            return None
        counts = {name: value[name] for name in _LABEL_RISK_COUNT_FIELDS}
        if expected_total is not None and counts["required_epoch_count"] != expected_total:
            return None
        if (counts["deployable_required_epoch_count"]
                + counts["not_deployable_required_epoch_count"]
                != counts["required_epoch_count"]):
            return None
        if (counts["answered_epoch_count"] + counts["unanswered_epoch_count"]
                != counts["required_epoch_count"]):
            return None
        if (counts["unanswered_deployable_count"]
                + counts["unanswered_not_deployable_count"]
                != counts["unanswered_epoch_count"]):
            return None
        if sum(counts[f"{name}_count"] for name in _CLASSIFICATIONS) != counts[
                "answered_epoch_count"]:
            return None
        if (counts["correct_deployment_count"] + counts["false_refusal_count"]
                + counts["unanswered_deployable_count"]
                != counts["deployable_required_epoch_count"]):
            return None
        if (counts["correct_refusal_count"] + counts["false_deployment_count"]
                + counts["unanswered_not_deployable_count"]
                != counts["not_deployable_required_epoch_count"]):
            return None
        if expected_classes is not None and any(
                counts[f"{name}_count"] != expected_classes[name]
                for name in _CLASSIFICATIONS):
            return None
        return counts

    risk_counts = None
    phase_counts: dict[str, dict[str, int] | None] = {
        "initial": None, "runtime": None,
    }
    if eligible and denominator is not None and legacy_counts is not None:
        raw_risks = deployment.get("label_risk_counts")
        raw_phases = deployment.get("label_risk_counts_by_phase")
        if raw_risks is None:
            quality_failures.append("deployment_label_risk_counts_missing")
        else:
            risk_counts = validated_risk_counts(
                raw_risks, expected_total=denominator,
                expected_classes=legacy_counts)
            if risk_counts is None:
                quality_failures.append("deployment_label_risk_counts_invalid")
            if not isinstance(raw_phases, Mapping):
                risk_counts = None
                quality_failures.append("deployment_phase_risk_counts_missing")
            else:
                for phase in ("initial", "runtime"):
                    phase_counts[phase] = validated_risk_counts(
                        raw_phases.get(phase), expected_total=None,
                        expected_classes=None)
                if any(phase_counts[phase] is None
                       for phase in ("initial", "runtime")):
                    risk_counts = None
                    quality_failures.append("deployment_phase_risk_counts_invalid")
                elif risk_counts is not None and any(
                        phase_counts["initial"][field]
                        + phase_counts["runtime"][field] != risk_counts[field]
                        for field in _LABEL_RISK_COUNT_FIELDS):
                    risk_counts = None
                    quality_failures.append("deployment_phase_risk_counts_do_not_sum")
        if risk_counts is None:
            phase_counts = {"initial": None, "runtime": None}

    conditional_rates = _conditional_risk_rates(risk_counts)
    all_epoch_false_refusal_share = (
        legacy_counts["false_refusal"] / denominator
        if eligible and legacy_counts is not None and denominator else None
    )
    all_epoch_false_deployment_share = (
        legacy_counts["false_deployment"] / denominator
        if eligible and legacy_counts is not None and denominator else None
    )

    def exposed_count(name: str) -> int | None:
        if not eligible:
            return 0
        return legacy_counts[name] if legacy_counts is not None else None

    risk_denominators = {
        "deployable_required_epoch_count": (
            risk_counts["deployable_required_epoch_count"] if risk_counts else None),
        "not_deployable_required_epoch_count": (
            risk_counts["not_deployable_required_epoch_count"] if risk_counts else None),
    }
    unanswered_counts = {
        name: (risk_counts[name] if risk_counts else None)
        for name in (
            "unanswered_epoch_count", "unanswered_deployable_count",
            "unanswered_not_deployable_count",
        )
    }

    # The explicit names avoid importing ambiguous legacy IRR/FIR meanings
    # into the self-decomposed Goal track.  They are the raw ingredients for a
    # future protocol statistic once its native oracle and applicable claim
    # gates pass. A single-episode assessment does not require branch restore.
    deployment_metrics = {
        "oracle_eligible": eligible,
        "verified_epoch_count": (denominator if eligible and denominator is not None else
                                 0 if not eligible else None),
        "correct_deployment_count": exposed_count("correct_deployment"),
        "correct_refusal_count": exposed_count("correct_refusal"),
        "false_refusal_count": exposed_count("false_refusal"),
        "false_deployment_count": exposed_count("false_deployment"),
        "risk_set_denominators": risk_denominators,
        "unanswered_counts": unanswered_counts,
        **conditional_rates,
        "all_epoch_false_refusal_share": all_epoch_false_refusal_share,
        "all_epoch_false_deployment_share": all_epoch_false_deployment_share,
        "by_phase": {
            phase: {
                "label_risk_counts": deepcopy(phase_counts[phase]),
                "conditional_rates": _conditional_risk_rates(phase_counts[phase]),
            }
            for phase in ("initial", "runtime")
        },
        "infrastructure_failure_count": deployment.get(
            "oracle_infrastructure_failure_count", 0),
    }
    return {
        "schema": "eai.benchmark.task_allocation_observations",
        "version": 2,
        "raw_only": True,
        "not_protocol_score": True,
        "deployment_gate": deployment_metrics,
        "plan_admission": deepcopy(dict(plan_admission)),
        "quality_failures": sorted(set(quality_failures)),
        "scoreable": False,
        "execution_validated": False,
        "business_outcomes_validated": False,
    }


def _assessment_archive(root: Path, *, episode_id: str, task_sha256: str) -> dict[str, Any]:
    path = root / "goal_session_report.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("episode_id") != episode_id or not isinstance(payload.get("report"), Mapping):
            raise ValueError("assessment archive episode mismatch")
        return deployment_assessment_observations(payload["report"], task_sha256=task_sha256)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {"status": "missing_or_invalid_archive", "raw_only": True,
                "records": [], "accuracy_denominator": None}


def _plan_admission_archive(
    root: Path, *, episode_id: str, session_binding_status: str,
) -> dict[str, Any]:
    path = root / "goal_session_report.json"
    try:
        if session_binding_status != "verified":
            raise ValueError("plan admission session source is not identity-bound")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(payload, Mapping) or payload.get("episode_id") != episode_id
                or not isinstance(payload.get("report"), Mapping)):
            raise ValueError("plan admission archive is missing report")
        return {**plan_admission_observations(payload["report"]),
                "binding_status": session_binding_status}
    except (OSError, ValueError, KeyError, TypeError, AttributeError, json.JSONDecodeError):
        return {"status": "missing_or_invalid_archive", "raw_only": True,
                "not_protocol_score": True,
                "binding_status": session_binding_status}


def development_track_observations(
    markers: Iterable[Mapping[str, Any]] | None,
) -> dict[str, Any]:
    """Validate development-track markers without ever releasing evidence.

    A missing marker list keeps the sidecar fail-closed: undeclared runs are
    not formal statistics either.  Malformed markers fail closed to an
    invalid marker report instead of raising, so one bad scene cannot lose
    the remaining raw observations.
    """
    if markers is None:
        return {"status": "missing", "scene_markers": {}, "release_eligible": False,
                "execution_validated": False, "business_outcomes_validated": False,
                "scoreable": False}
    try:
        from .development_track_marker import merge_development_markers

        boundary = merge_development_markers(markers)
        return {"status": "verified", **boundary}
    except (ValueError, TypeError, AttributeError) as exc:
        return {"status": "invalid_marker", "quality_failure": str(exc),
                "scene_markers": {}, "release_eligible": False,
                "execution_validated": False, "business_outcomes_validated": False,
                "scoreable": False}


def communication_capacity_observations(output_root: str | Path) -> dict[str, Any]:
    """Attach the offline capacity-experiment report as raw evidence.

    JP/IID/BP paired effects and per-pool C90/C50 curves are development
    diagnostics until the formal episode hookup; the sidecar preserves the
    stamped development marker and never converts a bracket into a score.
    """
    path = Path(output_root) / "communication_capacity_experiments.json"
    base = {"raw_only": True, "not_protocol_score": True,
            "scoreable": False, "execution_validated": False,
            "business_outcomes_validated": False}
    if not path.is_file():
        return {**base, "status": "missing", "report": None,
                "report_sha256": None, "quality_failure": "capacity_archive_missing"}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            raise ValueError("capacity archive must be an object")
        if payload.get("schema") != "eai.benchmark.communication_capacity_experiments":
            raise ValueError("unexpected capacity experiments schema")
        if type(payload.get("version")) is not int or payload["version"] < 1:
            raise ValueError("invalid capacity experiments version")
        if payload.get("status") != "ok":
            raise ValueError("capacity experiments are not summarized")
        marker = payload.get("development_track")
        if not isinstance(marker, Mapping) or marker.get("release_eligible") is not False:
            raise ValueError("capacity experiments lack a development marker")
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return {**base, "status": "invalid", "report": None,
                "report_sha256": None,
                "quality_failure": f"capacity_archive_invalid:{type(exc).__name__}"}
    return {**base, "status": "observed", "report": deepcopy(payload),
            "report_sha256": _sha256(path)}


def checkpoint_branch_observations(output_root: str | Path) -> dict[str, Any]:
    """Attach the checkpoint branch-replay report as raw evidence.

    Strict branch eligibility and sham isolation are infrastructure facts;
    a missing or malformed branch report never becomes replay evidence and
    native restore stays explicitly unverified until its own gate passes.
    """
    path = Path(output_root) / "goal_checkpoint_branch.json"
    base = {"raw_only": True, "not_protocol_score": True,
            "scoreable": False, "execution_validated": False,
            "business_outcomes_validated": False}
    if not path.is_file():
        return {**base, "status": "missing", "report": None,
                "report_sha256": None, "absence_reason": "optional_branch_archive_missing"}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            raise ValueError("branch archive must be an object")
        if payload.get("schema") != "eai.benchmark.goal_checkpoint_branch":
            raise ValueError("unexpected checkpoint branch schema")
        if type(payload.get("version")) is not int or payload["version"] < 1:
            raise ValueError("invalid checkpoint branch version")
        if payload.get("native_restore_verified") is not False:
            raise ValueError("branch report cannot claim native restore")
        marker = payload.get("development_track")
        if not isinstance(marker, Mapping) or marker.get("release_eligible") is not False:
            raise ValueError("branch report lacks a development marker")
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return {**base, "status": "invalid", "report": None,
                "report_sha256": None,
                "quality_failure": f"branch_archive_invalid:{type(exc).__name__}"}
    return {**base, "status": "observed", "report": deepcopy(payload),
            "report_sha256": _sha256(path)}


def progress_evidence_observations(output_root: str | Path,
                                   public_task: Mapping[str, Any], *,
                                   episode_id: str) -> dict[str, Any]:
    """Independently replay raw predicate facts; do not trust a stored replay flag."""
    from .goal_progress_evidence import replay_progress_evidence

    path = Path(output_root) / "goal_progress_evidence.json"
    base = {"raw_only": True, "not_protocol_score": True,
            "scoreable": False, "execution_validated": False,
            "business_outcomes_validated": False}
    if not path.is_file():
        return {**base, "status": "missing", "report": None,
                "quality_failure": "progress_archive_missing"}
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
        replay = replay_progress_evidence(report, public_task, episode_id=episode_id)
        receipt_path = Path(output_root) / "skill_receipts.json"
        if not receipt_path.is_file():
            return {**base, "status": "missing", "report": None,
                    "quality_failure": "progress_receipt_archive_missing"}
        receipts = json.loads(receipt_path.read_text(encoding="utf-8"))
        if not isinstance(receipts, list) or any(
                not isinstance(row, Mapping) or row.get("episode_id") != episode_id
                for row in receipts):
            raise ValueError("progress receipt binding has invalid episode identities")
        receipt_times = [row["completed_at_s"] for row in receipts]
        if any(isinstance(time, bool) or not isinstance(time, (float, int))
               or not math.isfinite(time) for time in receipt_times):
            raise ValueError("progress receipt binding has invalid times")
        receipt_times.sort()
        boundary_times = sorted(row["host_time_s"] for row in
                                [*report["samples"], *report["quality_errors"]]
                                if row["boundary"] == "skill_completed")
        if receipt_times != boundary_times:
            raise ValueError("progress boundaries do not match accepted skill receipts")
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return {**base, "status": "invalid", "report": None,
                "quality_failure": f"progress_archive_invalid:{type(exc).__name__}"}
    return {**base, "status": replay["status"], "report": report,
            "replay": replay, "report_sha256": _sha256(path)}


def _progress_requested(output_root: str | Path) -> bool:
    root = Path(output_root)
    if (root / "goal_progress_evidence.json").exists():
        return True
    try:
        manifest = json.loads((root / "episode_manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(manifest, Mapping) and "progress_evidence" in manifest


def build_scoring_adapter_metadata(
    public_task: Mapping[str, Any],
    *,
    episode_id: str,
    output_root: str | Path,
    code_baseline: str | None = None,
    development_markers: Iterable[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a fail-closed, raw-only protocol mapping sidecar."""
    if not isinstance(episode_id, str) or not episode_id.strip():
        raise ValueError("episode_id must be a nonempty string")
    if not isinstance(public_task, Mapping):
        raise ValueError("public_task must be an object")
    task_id = public_task.get("task_id")
    mission = public_task.get("mission")
    scene_key = mission.get("scene_key") if isinstance(mission, Mapping) else None
    if not isinstance(task_id, str) or not task_id.strip():
        raise ValueError("public_task.task_id must be a nonempty string")
    if not isinstance(scene_key, str) or not scene_key.strip():
        raise ValueError("public_task.mission.scene_key must be a nonempty string")
    from .goal_cost_evidence import build_cost_evidence

    # Cost evidence is an auditable raw sidecar.  It intentionally preserves
    # missing clocks/ledgers as null and never promotes release flags.
    task_sha256 = canonical_sha256(public_task)
    cost_evidence = build_cost_evidence(
        output_root, episode_id=episode_id, task_sha256=task_sha256)
    deployment_observations = _assessment_archive(
        Path(output_root), episode_id=episode_id,
        task_sha256=task_sha256)
    plan_observations = _plan_admission_archive(
        Path(output_root), episode_id=episode_id,
        session_binding_status=cost_evidence["accepted_actions"]["binding_status"])
    from .goal_restaurant_business_facts import (
        restaurant_business_fact_observations, restaurant_business_facts_requested,
    )

    restaurant_extension = restaurant_business_facts_requested(output_root)
    return {
        "schema": SCORING_ADAPTER_SCHEMA,
        "version": SCORING_ADAPTER_VERSION,
        "adapter_schema": SCORING_ADAPTER_SCHEMA,
        "protocol_mapping_revision": (
            "protocol-20260925-restaurant-business-facts-v6"
            if restaurant_extension else PROTOCOL_MAPPING_REVISION),
        "episode_id": episode_id,
        "task_id": task_id,
        "scene_key": scene_key,
        "task_contract_sha256": canonical_sha256(public_task),
        "deployment_assessment": deployment_observations,
        "plan_admission": plan_observations,
        "task_allocation": task_allocation_observations(
            deployment_observations, plan_observations),
        "communication_analysis": communication_analysis_observations(output_root),
        "communication_capacity": communication_capacity_observations(output_root),
        "checkpoint_branch": checkpoint_branch_observations(output_root),
        "native_oracle_registry": native_oracle_registry_observations(output_root),
        "development_track": development_track_observations(development_markers),
        "cost_evidence": cost_evidence,
        **({"progress_evidence": progress_evidence_observations(
            output_root, public_task, episode_id=episode_id)}
           if _progress_requested(output_root) else {}),
        **({"restaurant_business_facts": restaurant_business_fact_observations(
            output_root, public_task, episode_id=episode_id)}
           if restaurant_extension else {}),
        "condition": {
            "id": public_task.get("condition_id"),
            "seed": public_task.get("seed"),
            "experiment_factor": public_task.get("experiment_factor"),
        },
        "evaluation_track": "goal_driven_team_planning_v1",
        "task_design": {
            "decomposition_owner": "participant",
            "assignment_owner": "participant",
            "schedule_owner": "participant",
            "fixed_subtask_metrics": "not_applicable_participant_decomposition",
            "allocation_evidence": "participant_plan_posthoc_audit_and_deployment_gate",
            "coverage_evidence": "external_milestone_progress",
            "evidence_status": "observation_dependent",
            "observation_refs": ["task_allocation", "progress_evidence"] + (
                ["restaurant_business_facts"] if restaurant_extension else []),
        },
        "checkpoint_requirements": {
            "status": "not_attested_by_adapter",
            "applies_to": "strict_same_snapshot_comparison",
            "observation_ref": "checkpoint_branch",
            "required_for_baseline_execution": False,
            "restore_verified": False,
            "strict_branch_comparison_eligible": False,
            "required_state_domains": [
                "environment_physics", "participant_recoverable",
                "visible_history", "rng_and_clocks",
                "host_scheduler_and_pending_communication",
                "deployment_gate_epoch_and_assessment_history",
                "preregistered_remaining_task_oracle_and_cached_truth",
            ],
            "preregistered_trigger_design": [
                "initial", "decision_pre", "treatment_pre", "replan_pre",
                "milestone_change", "episode_round",
                "initial_assessment_pre", "reassessment_pre",
            ],
            "design_ref": "SCORING_CHECKPOINT_DESIGN.zh-CN.md",
        },
        "trusted_state_contract": {
            "status": "not_attested_by_adapter",
            "observation_refs": ["native_oracle_registry", "deployment_assessment"],
            "design_ref": "TRUSTED_STATE_CONTRACT.zh-CN.md",
            "formal_label_source": "registered_remaining_feasibility_oracle",
            "native_verified": False,
        },
        "archive_role": "goal_engineering_observation",
        "code_baseline": code_baseline,
        "branch_id": None,
        "branch_status": "single_episode_no_protocol_branch",
        "raw_only": True,
        "not_protocol_score": True,
        "dimensions": dict(DIMENSION_STATUS),
        "clock_evidence": {
            "source": "goal_host_receipt_clock",
            "scope": "adapter_attestation_only",
            "observation_refs": ["cost_evidence", "progress_evidence"],
            "physical_clock_bound": False,
            "native_pose_scope": "optional_current_readback",
            "protocol_time_status": "not_attested_by_adapter",
        },
        "snapshot_evidence": {
            "symbolic_runtime_state": None,
            "physics_state": None,
            "rng_state": None,
            "branching": None,
            "status": "not_attested_by_adapter",
            "applies_to": "strict_same_snapshot_comparison",
            "required_for_baseline_execution": False,
            "observation_ref": "checkpoint_branch",
        },
        "scoreability_reasons": list(SCOREABILITY_REASONS),
        "claim_requirements": {
            "status": "requirements_only_not_eligibility",
            "qualification_source": "independent_native_release_review",
            "requirements_by_claim": deepcopy(CLAIM_REQUIREMENTS),
        },
        "execution_validated": False,
        "business_outcomes_validated": False,
        "scoreable": False,
        "archive_index": build_archive_index(output_root),
    }


__all__ = [
    "DIMENSION_STATUS", "PROTOCOL_MAPPING_REVISION", "SCORING_ADAPTER_SCHEMA",
    "SCORING_ADAPTER_VERSION", "build_archive_index", "build_scoring_adapter_metadata",
    "checkpoint_branch_observations", "communication_analysis_observations",
    "communication_capacity_observations", "deployment_assessment_observations",
    "development_track_observations", "native_oracle_registry_observations",
    "plan_admission_observations", "task_allocation_observations",
    "progress_evidence_observations",
]
