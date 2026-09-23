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
    "no_protocol_shared_capacity_or_iid_loss_contract",
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
    return {
        "schema": SCORING_ADAPTER_SCHEMA,
        "version": SCORING_ADAPTER_VERSION,
        "adapter_schema": SCORING_ADAPTER_SCHEMA,
        "protocol_mapping_revision": PROTOCOL_MAPPING_REVISION,
        "episode_id": episode_id,
        "task_id": task_id,
        "scene_key": scene_key,
        "task_contract_sha256": canonical_sha256(public_task),
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
            ],
            "preregistered_trigger_design": [
                "initial", "decision_pre", "treatment_pre", "replan_pre",
                "milestone_change", "episode_round",
            ],
            "design_ref": "SCORING_CHECKPOINT_DESIGN.zh-CN.md",
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
]
