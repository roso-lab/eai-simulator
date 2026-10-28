"""Validation and metadata helpers for the frozen city_01 checkpoints.

The scene manifest owns the checkpoint policy.  This module deliberately does
not infer a checkpoint from a controller report: a runtime must provide an
immutable artifact digest and an external readback receipt.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any


SCHEMA = "eai.city_01.checkpoint-policy.v1"
RECEIPT_SCHEMA = "eai.city_01.checkpoint-receipt.v1"
HEX64 = re.compile(r"^[0-9a-f]{64}$")
EXPECTED_CHECKPOINTS = (
    "cp_00_spawn_valid",
    "cp_01_patrol_ready",
    "cp_02_pre_incident",
    "cp_03_observation_complete",
    "cp_04_recover_ledger_closed",
    "cp_05_ground_rendezvous",
    "cp_06_closeout",
)
EXPECTED_MONITORS = (
    "mp_spawn",
    "mp_area_coverage",
    "mp_incident",
    "mp_aerial_observation",
    "mp_recover_resource",
    "mp_ground_arrival",
    "mp_closeout",
)


class City01CheckpointError(ValueError):
    """Raised when checkpoint policy or runtime evidence is malformed."""


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise City01CheckpointError(f"{name} must be nonempty text")
    return value


def _digest(value: Any, name: str) -> str:
    value = _text(value, name)
    if not HEX64.fullmatch(value):
        raise City01CheckpointError(f"{name} must be lowercase SHA-256")
    return value


def _ids(rows: Any, name: str) -> tuple[str, ...]:
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        raise City01CheckpointError(f"{name} must be a list")
    values = tuple(_text(item, f"{name} item") for item in rows)
    if len(values) != len(set(values)):
        raise City01CheckpointError(f"{name} contains duplicate IDs")
    return values


def _manifest_context(manifest: Mapping[str, Any]) -> tuple[str, str, str, str]:
    if not isinstance(manifest, Mapping):
        raise City01CheckpointError("task manifest must be an object")
    task_family = _text(manifest.get("task_family"), "task_family")
    scene = _text(manifest.get("scene_key"), "scene_key")
    semantic_map = manifest.get("semantic_map")
    if not isinstance(semantic_map, Mapping):
        raise City01CheckpointError("semantic_map is required")
    revision = _text(semantic_map.get("revision"), "semantic_map.revision")
    binding = _digest(semantic_map.get("semantic_binding_digest"), "semantic_binding_digest")
    return task_family, scene, revision, binding


def validate_checkpoint_policy(policy: Mapping[str, Any], manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the static checkpoint and monitoring contract."""
    if not isinstance(policy, Mapping) or policy.get("schema") != SCHEMA:
        raise City01CheckpointError("checkpoint policy schema mismatch")
    task_family, scene, revision, binding = _manifest_context(manifest)
    phase_graph = manifest.get("phase_graph")
    if not isinstance(phase_graph, list):
        raise City01CheckpointError("manifest phase_graph is required")
    phases = {_text(row.get("phase_id"), "phase_id") for row in phase_graph if isinstance(row, Mapping)}
    if len(phases) != len(phase_graph):
        raise City01CheckpointError("phase_graph contains malformed or duplicate phases")
    checkpoints = policy.get("checkpoints")
    if not isinstance(checkpoints, list):
        raise City01CheckpointError("checkpoints must be a list")
    checkpoint_ids = tuple(_text(row.get("checkpoint_id"), "checkpoint_id")
                          for row in checkpoints if isinstance(row, Mapping))
    if len(checkpoint_ids) != len(checkpoints) or len(checkpoint_ids) != len(set(checkpoint_ids)):
        raise City01CheckpointError("checkpoints contain malformed or duplicate IDs")
    if checkpoint_ids != EXPECTED_CHECKPOINTS:
        raise City01CheckpointError("city_01 checkpoint order is not the frozen order")
    branchable = []
    for row in checkpoints:
        if not isinstance(row, Mapping):
            raise City01CheckpointError("checkpoint entry must be an object")
        checkpoint_id = _text(row.get("checkpoint_id"), "checkpoint_id")
        phase_id = _text(row.get("phase_id"), "phase_id")
        if phase_id not in phases:
            raise City01CheckpointError(f"{checkpoint_id} references unknown phase {phase_id}")
        capture_fields = [key for key in ("capture_after_milestone", "capture_before_milestone") if key in row]
        if len(capture_fields) != 1:
            raise City01CheckpointError(f"{checkpoint_id} must bind exactly one capture boundary")
        if not isinstance(row.get("required_tiers"), list) or not row["required_tiers"]:
            raise City01CheckpointError(f"{checkpoint_id} requires a nonempty tier list")
        if any(type(tier) is not int or tier not in {1, 2, 3, 4} for tier in row["required_tiers"]):
            raise City01CheckpointError(f"{checkpoint_id} has invalid required tiers")
        if type(row.get("branchable")) is not bool:
            raise City01CheckpointError(f"{checkpoint_id}.branchable must be boolean")
        if row["branchable"]:
            branchable.append(checkpoint_id)
    if tuple(branchable) != EXPECTED_CHECKPOINTS[:3]:
        raise City01CheckpointError("only cp_00, cp_01 and cp_02 may be branchable")

    # The manifest keeps monitoring points beside checkpoint_policy so that
    # event streams can evolve without changing the snapshot policy schema.
    monitors = policy.get("monitoring_points") or manifest.get("monitoring_points")
    if not isinstance(monitors, Mapping) or monitors.get("schema") != "eai.city_01.monitoring-points.v1":
        raise City01CheckpointError("monitoring point schema mismatch")
    monitor_rows = monitors.get("points")
    if not isinstance(monitor_rows, list):
        raise City01CheckpointError("monitoring points must be a list")
    monitor_ids = tuple(_text(row.get("monitor_id"), "monitor_id")
                        for row in monitor_rows if isinstance(row, Mapping))
    if len(monitor_ids) != len(monitor_rows) or len(monitor_ids) != len(set(monitor_ids)):
        raise City01CheckpointError("monitoring points contain malformed or duplicate IDs")
    if monitor_ids != EXPECTED_MONITORS:
        raise City01CheckpointError("city_01 monitoring point order is not frozen")
    for row in monitor_rows:
        if not isinstance(row, Mapping):
            raise City01CheckpointError("monitoring point entry must be an object")
        if _text(row.get("phase_id"), "monitor phase_id") not in phases:
            raise City01CheckpointError("monitor references unknown phase")
        _text(row.get("predicate"), "monitor predicate")
        evidence = _ids(row.get("evidence"), "monitor evidence")

    return {
        "task_family": task_family,
        "scene_key": scene,
        "map_revision": revision,
        "semantic_binding_digest": binding,
        "checkpoint_ids": checkpoint_ids,
        "monitor_ids": monitor_ids,
    }


def checkpoint_metadata(manifest: Mapping[str, Any], *, task_id: str, episode_id: str,
                        repeat_id: str, condition_id: str, phase_id: str,
                        checkpoint_id: str, controller_digest: str) -> dict[str, str]:
    """Build the string metadata required by ``checkpoint_store_v3``."""
    context = validate_checkpoint_policy(manifest["checkpoint_policy"], manifest)
    if checkpoint_id not in context["checkpoint_ids"]:
        raise City01CheckpointError(f"unknown checkpoint_id: {checkpoint_id}")
    _text(task_id, "task_id")
    _text(episode_id, "episode_id")
    _text(repeat_id, "repeat_id")
    _text(condition_id, "condition_id")
    _text(phase_id, "phase_id")
    _digest(controller_digest, "controller_digest")
    return {
        "task_family": context["task_family"],
        "scene_key": context["scene_key"],
        "map_revision": context["map_revision"],
        "semantic_binding_digest": context["semantic_binding_digest"],
        "task_id": task_id,
        "episode_id": episode_id,
        "repeat_id": repeat_id,
        "condition_id": condition_id,
        "phase_id": phase_id,
        "checkpoint_id": checkpoint_id,
        "controller_digest": controller_digest,
    }


def validate_checkpoint_receipt(receipt: Mapping[str, Any], manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Validate an external receipt proving an artifact was written and read back."""
    if not isinstance(receipt, Mapping) or receipt.get("schema") != RECEIPT_SCHEMA:
        raise City01CheckpointError("checkpoint receipt schema mismatch")
    context = validate_checkpoint_policy(manifest["checkpoint_policy"], manifest)
    for key in ("task_id", "episode_id", "repeat_id", "condition_id", "phase_id", "checkpoint_id", "artifact_path"):
        _text(receipt.get(key), key)
    if receipt["checkpoint_id"] not in context["checkpoint_ids"]:
        raise City01CheckpointError("receipt references unknown checkpoint")
    if receipt["task_id"] not in {row["task_id"] for row in manifest.get("difficulty_tiers", [])}:
        raise City01CheckpointError("receipt task_id is not registered in difficulty_tiers")
    _digest(receipt.get("artifact_digest"), "artifact_digest")
    _digest(receipt.get("readback_digest"), "readback_digest")
    if type(receipt.get("readback_verified")) is not bool or not receipt["readback_verified"]:
        raise City01CheckpointError("receipt must explicitly confirm readback")
    if type(receipt.get("sim_time_s")) not in (int, float) or receipt["sim_time_s"] < 0:
        raise City01CheckpointError("sim_time_s must be nonnegative")
    _digest(receipt.get("map_revision_digest"), "map_revision_digest")
    _digest(receipt.get("semantic_binding_digest"), "receipt semantic_binding_digest")
    if receipt["semantic_binding_digest"] != context["semantic_binding_digest"]:
        raise City01CheckpointError("receipt semantic binding differs from task manifest")
    return dict(receipt)


def validate_paired_receipts(receipts: Sequence[Mapping[str, Any]], *, checkpoint_id: str) -> dict[str, Any]:
    """Require paired branches to share the exact pre-factor artifact digest."""
    if not isinstance(receipts, Sequence) or isinstance(receipts, (str, bytes)) or not receipts:
        raise City01CheckpointError("paired receipts must be a nonempty list")
    rows = [dict(row) for row in receipts]
    if any(row.get("checkpoint_id") != checkpoint_id for row in rows):
        raise City01CheckpointError("paired receipts use different checkpoints")
    digests = {row.get("artifact_digest") for row in rows}
    if len(digests) != 1 or None in digests:
        raise City01CheckpointError("paired receipts do not share one artifact digest")
    pair_ids = {row.get("pair_id") for row in rows}
    if len(pair_ids) != 1 or None in pair_ids:
        raise City01CheckpointError("paired receipts do not share one pair_id")
    return {"checkpoint_id": checkpoint_id, "pair_id": next(iter(pair_ids)),
            "artifact_digest": next(iter(digests)), "branch_count": len(rows)}


def write_city01_checkpoint(path: str, snapshot: Mapping[str, Any], manifest: Mapping[str, Any], **identity: str) -> str:
    """Write a city_01 checkpoint through the durable registry store."""
    from .checkpoint_store_v3 import write_registry_checkpoint

    metadata = checkpoint_metadata(manifest, **identity)
    return write_registry_checkpoint(path, snapshot, metadata=metadata)


def read_city01_checkpoint(path: str, *, expected_digest: str,
                           expected_metadata: Mapping[str, str]) -> dict[str, Any]:
    """Read and authenticate a city_01 checkpoint artifact."""
    from .checkpoint_store_v3 import read_registry_checkpoint

    return read_registry_checkpoint(path, expected_digest=expected_digest,
                                    expected_metadata=expected_metadata)


__all__ = [
    "City01CheckpointError", "SCHEMA", "RECEIPT_SCHEMA",
    "validate_checkpoint_policy", "checkpoint_metadata",
    "validate_checkpoint_receipt", "validate_paired_receipts",
    "write_city01_checkpoint", "read_city01_checkpoint",
]
