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
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

from .provenance import canonical_sha256


SCORING_ADAPTER_SCHEMA = "eai.benchmark.scoring_adapter_metadata"
SCORING_ADAPTER_VERSION = 1
PROTOCOL_MAPPING_REVISION = "protocol-20260923-goal-milestones-v2"

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
    "fixed_subtask_metrics_require_revision_for_goal_decomposition",
    "no_native_communication_episode_provenance",
    "no_physics_checkpoint_or_fact_reference_sham_branch",
    "formal_runner_is_serial_and_has_no_protocol_concurrency_evidence",
    "execution_and_business_validation_are_release_gated",
]


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
        "cost_evidence.json", "communication_analysis.json",
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


def deployment_assessment_observations(
    session_report: Mapping[str, Any], *, task_sha256: str,
) -> dict[str, Any]:
    """Archive assessment classification facts without inventing score inputs.

    Timing is the host receipt clock. Physical change/notification transport
    latency and per-epoch byte costs remain unknown until their real sources
    are joined. Unknown or platform-invalid labels never enter accuracy counts.
    """
    gate = session_report.get("deployment_assessment")
    if not isinstance(gate, Mapping) or not gate.get("enabled"):
        return {"status": "not_enabled", "raw_only": True, "records": [],
                "accuracy_denominator": None}
    events = session_report.get("events", [])
    actions = {row["action_id"]: row for row in session_report.get("actions", [])}
    event_index = {row["sequence"]: row for row in events}
    termination = gate.get("termination") or {}
    records = []
    seen = set()
    for row in gate.get("history", []):
        epoch = row.get("assessment_epoch")
        key = (row.get("assessment_id"), epoch)
        duplicate = key in seen
        seen.add(key)
        external = row.get("external_evaluation") or {}
        valid = (not duplicate and row.get("public_task_sha256") == task_sha256
                 and external.get("assessment_id") == key[0]
                 and external.get("assessment_epoch") == epoch
                 and external.get("oracle_status") == "verified"
                 and external.get("platform_valid") is True
                 and bool(external.get("evidence_refs"))
                 and bool(external.get("evaluator_ref"))
                 and external.get("feasibility_label") in {"deployable", "not_deployable"})
        label, decision = external.get("feasibility_label"), row.get("decision")
        classification = None
        if valid:
            classification = {
                ("deployable", "deployable"): "correct_deployment",
                ("deployable", "not_deployable"): "false_refusal",
                ("not_deployable", "deployable"): "false_deployment",
                ("not_deployable", "not_deployable"): "correct_refusal",
            }.get((label, decision))
        trigger = event_index.get(row.get("trigger_event_sequence"), {})
        start, declared = trigger.get("time_s"), row.get("declared_at_s")
        def finite(value):
            return type(value) in (float, int) and math.isfinite(value)
        latency = (declared - start if finite(start) and finite(declared) and declared >= start else None)
        before = []
        declaration_found = False
        for event in events:
            if event.get("sequence", -1) < row.get("trigger_event_sequence", 0):
                continue
            payload = event.get("payload", {})
            if event.get("type") != "action_result" or payload.get("status") != "accepted":
                continue
            if payload.get("action_id") == row.get("decision_action_id"):
                declaration_found = True
                break
            accepted = actions.get(payload.get("action_id"))
            if accepted:
                before.append(accepted)
        counts = {kind: sum(a["type"] == kind for a in before) for kind in (
            "select_team", "submit_plan", "revise_plan", "dispatch_skill", "consume_message")}
        requested = termination.get("assessment_id") == key[0]
        records.append({
            "assessment_id": key[0], "assessment_epoch": epoch,
            "phase": "initial" if row.get("trigger_event_type") == "episode_started" else "runtime",
            "classification": classification, "label_eligible": valid and classification is not None,
            "oracle_status": "verified" if valid else "failed",
            "platform_valid": external.get("platform_valid"),
            "decision": decision, "feasibility_label": label if valid else None, "duplicate_epoch": duplicate,
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
    required = {(event.get("payload", {}).get("assessment_id"),
                 event.get("payload", {}).get("assessment_epoch"))
                for event in events if event.get("type") == "deployment_assessment_required"}
    unanswered = sorted(required - seen, key=lambda pair: pair[1])
    oracles = gate.get("oracle_evaluations", [])
    by_epoch = {}
    duplicate_oracle = False
    for oracle in oracles:
        key = (oracle.get("assessment_id"), oracle.get("assessment_epoch"))
        duplicate_oracle |= key in by_epoch
        by_epoch[key] = oracle
    valid_oracles = {key for key, oracle in by_epoch.items()
                     if oracle.get("oracle_status") == "verified"
                     and oracle.get("feasibility_label") in {"deployable", "not_deployable"}
                     and oracle.get("platform_valid") is True
                     and bool(oracle.get("evidence_refs")) and bool(oracle.get("evaluator_ref"))}
    missing_or_failed = required - valid_oracles
    episode_eligible = (bool(required) and not missing_or_failed and not duplicate_oracle
                        and set(by_epoch) == required
                        and all(record["label_eligible"] for record in records))
    return {"status": gate.get("status"), "raw_only": True, "not_protocol_score": True,
            "records": records, "accuracy_denominator": len(required) if episode_eligible else 0,
            "accuracy_denominator_scope": "all_required_epochs_of_oracle_verified_episodes_including_missing_answers",
            "episode_oracle_eligible": episode_eligible,
            "excluded_from_deployment_metrics": not episode_eligible,
            "required_epoch_count": len(required) if required else None,
            "unanswered_epochs": [{"assessment_id": key[0], "assessment_epoch": key[1]}
                                   for key in unanswered],
            "classification_counts": {name: (sum(r["classification"] == name for r in records) if episode_eligible else 0)
                for name in ("correct_deployment", "false_refusal", "false_deployment", "correct_refusal")},
            "oracle_infrastructure_failure_count": len(missing_or_failed),
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


def _plan_admission_archive(root: Path) -> dict[str, Any]:
    path = root / "goal_session_report.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping) or not isinstance(payload.get("report"), Mapping):
            raise ValueError("plan admission archive is missing report")
        return plan_admission_observations(payload["report"])
    except (OSError, ValueError, KeyError, TypeError, AttributeError, json.JSONDecodeError):
        return {"status": "missing_or_invalid_archive", "raw_only": True,
                "not_protocol_score": True}


def build_scoring_adapter_metadata(
    public_task: Mapping[str, Any],
    *,
    episode_id: str,
    output_root: str | Path,
    code_baseline: str | None = None,
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
    cost_evidence = build_cost_evidence(output_root, episode_id=episode_id)
    return {
        "schema": SCORING_ADAPTER_SCHEMA,
        "version": SCORING_ADAPTER_VERSION,
        "adapter_schema": SCORING_ADAPTER_SCHEMA,
        "protocol_mapping_revision": PROTOCOL_MAPPING_REVISION,
        "episode_id": episode_id,
        "task_id": task_id,
        "scene_key": scene_key,
        "task_contract_sha256": canonical_sha256(public_task),
        "deployment_assessment": _assessment_archive(
            Path(output_root), episode_id=episode_id, task_sha256=canonical_sha256(public_task)),
        "plan_admission": _plan_admission_archive(Path(output_root)),
        "communication_analysis": communication_analysis_observations(output_root),
        "cost_evidence": cost_evidence,
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
            "fixed_subtask_metrics": "not_applicable_protocol_mismatch",
        },
        "checkpoint_requirements": {
            "status": "planned_not_captured",
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
            "status": "contract_only",
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
            "physical_clock_bound": False,
            "native_pose_scope": "optional_current_readback",
            "protocol_time_status": "unverified_clock",
        },
        "snapshot_evidence": {
            "symbolic_runtime_state": True,
            "physics_state": False,
            "rng_state": False,
            "branching": False,
            "status": "unverified_for_protocol_replay",
        },
        "scoreability_reasons": list(SCOREABILITY_REASONS),
        "execution_validated": False,
        "business_outcomes_validated": False,
        "scoreable": False,
        "archive_index": build_archive_index(output_root),
    }


__all__ = [
    "DIMENSION_STATUS", "PROTOCOL_MAPPING_REVISION", "SCORING_ADAPTER_SCHEMA",
    "SCORING_ADAPTER_VERSION", "build_archive_index", "build_scoring_adapter_metadata",
    "communication_analysis_observations", "deployment_assessment_observations",
    "plan_admission_observations",
]
