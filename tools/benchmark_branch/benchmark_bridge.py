"""Compatibility bridge from the paper benchmark identities to the collector.

``benchmark/`` owns ``ExperimentBatch``, ``RunKey`` and treatment semantics.
This module only serialises those identities into collector jobs; it never
recomputes a base instance id or decides whether a pair is scientifically
valid.  The bridge accepts both live benchmark dataclasses and detached JSON
manifests so a resumed run can be reconstructed without importing a simulator.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, is_dataclass
import hashlib
from typing import Any

from .models import BRANCH_ROLES, BranchJob, BranchPlan, CheckpointArtifact


class BenchmarkBridgeError(ValueError):
    """Raised when a collector job would lose benchmark identity."""


def _value(value: Any, name: str, default: Any = "") -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _text(value: Any, name: str, default: str = "") -> str:
    result = str(value if value is not None else default).strip()
    if not result:
        raise BenchmarkBridgeError(f"benchmark field {name} is required")
    return result


def _mapping(value: Any) -> dict[str, Any]:
    """Detach a benchmark dataclass, mapping, or simple protocol object."""

    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        result = to_dict()
        if isinstance(result, Mapping):
            return dict(result)
    if is_dataclass(value):
        return dict(asdict(value))
    values = getattr(value, "__dict__", None)
    if isinstance(values, Mapping):
        return dict(values)
    raise BenchmarkBridgeError(f"benchmark object is not mapping-compatible: {type(value).__name__}")


def _role(value: Any) -> str:
    raw = _value(value, "branch_role", value)
    return _text(getattr(raw, "value", raw), "branch_role")


def _run_key(branch: Any) -> Any:
    key = _value(branch, "run_key", None)
    if key is None:
        raise BenchmarkBridgeError("branch is missing run_key")
    return key


def _checkpoint(batch: Any, branch: Any) -> Any:
    checkpoint = _value(branch, "checkpoint", None) or _value(batch, "checkpoint", None)
    if checkpoint is None:
        raise BenchmarkBridgeError("benchmark branch is missing checkpoint")
    return checkpoint


def _checkpoint_artifact(checkpoint: Any, metadata: Mapping[str, Any]) -> CheckpointArtifact:
    digest = _text(_value(checkpoint, "digest", ""), "checkpoint.digest")
    checkpoint_id = _text(_value(checkpoint, "checkpoint_id", "benchmark"), "checkpoint.checkpoint_id")
    state = _value(checkpoint, "state", {"artifact_uri": _value(checkpoint, "artifact_uri", "")})
    schema = _text(_value(checkpoint, "schema", "benchmark.checkpoint.v1"), "checkpoint.schema")
    return CheckpointArtifact(
        checkpoint_id=checkpoint_id, digest=digest, state=state, schema=schema,
        metadata=dict(metadata),
    )


def _batch_metadata(batch: Any) -> dict[str, Any]:
    manifest_digest = _text(_value(batch, "manifest_digest", ""), "manifest_digest")
    plan_digest = _text(_value(batch, "plan_digest", ""), "plan_digest")
    cell = _value(batch, "cell", {})
    anchor = _value(batch, "anchor", {})
    checkpoint = _value(batch, "checkpoint", {})
    cell_data = _mapping(cell)
    anchor_data = _mapping(anchor)
    manifest = batch.manifest() if callable(getattr(batch, "manifest", None)) else {}
    anchor_digest = str(_value(batch, "anchor_digest", manifest.get("anchor_digest", "")))
    return {
        "benchmark_authority": "benchmark",
        "benchmark_manifest_digest": manifest_digest,
        "benchmark_plan_digest": plan_digest,
        "anchor_id": _text(_value(batch, "anchor_id", cell_data.get("anchor_id", _value(anchor, "anchor_id", ""))), "anchor_id"),
        "anchor_digest": anchor_digest,
        "checkpoint_round": _text(cell_data.get("checkpoint_round", ""), "checkpoint_round"),
        "strict_pair_required": bool(_value(batch, "strict_pair_required", cell_data.get("strict_pair_required", False))),
        "treatment_authority": "benchmark.protocol",
        "checkpoint_ref": {
            "checkpoint_id": _value(checkpoint, "checkpoint_id", ""),
            "kind": _value(checkpoint, "kind", ""),
            "step": _value(checkpoint, "step", 0),
            "digest": _value(checkpoint, "digest", ""),
            "artifact_uri": _value(checkpoint, "artifact_uri", ""),
            "source_run": _value(checkpoint, "source_run", ""),
        },
        # Keep detached semantic metadata available to the task adapter, but
        # do not use it as a replacement for the benchmark manifest digest.
        "anchor_semantics": anchor_data,
    }


def branch_job_from_spec(
    branch: Any,
    *,
    batch: Any,
    slot: int = 0,
    execution_scope: str = "pending",
    run_id: str = "benchmark",
) -> BranchJob:
    """Project one authoritative ``BranchSpec`` into a collector job."""

    if execution_scope not in {"measured", "protocol_synthetic", "pending", "failed"}:
        raise BenchmarkBridgeError(f"unknown execution_scope: {execution_scope!r}")
    key = _run_key(branch)
    role = _role(key)
    if role not in BRANCH_ROLES:
        raise BenchmarkBridgeError(f"unknown benchmark branch role: {role!r}")
    batch_meta = _batch_metadata(batch)
    checkpoint = _checkpoint(batch, branch)
    run_key_values = {
        "base_instance_id": _text(_value(key, "base_instance_id", ""), "base_instance_id"),
        "system_id": _text(_value(key, "system_id", ""), "system_id"),
        "condition_id": _text(_value(key, "condition_id", ""), "condition_id"),
        "repeat_id": _text(_value(key, "repeat_id", ""), "repeat_id"),
        "checkpoint_round": _text(_value(key, "checkpoint_round", ""), "checkpoint_round"),
        "untreated_history_id": str(_value(key, "untreated_history_id", "")),
    }
    pair_key = tuple(run_key_values[name] for name in (
        "base_instance_id", "system_id", "condition_id", "repeat_id", "checkpoint_round"
    ))
    pair_group_id = "benchmark/pair/" + hashlib.sha256("\x1f".join(pair_key).encode()).hexdigest()[:24]
    branch_key = "/".join((*pair_key, role))
    branch_id = f"{run_id}/{branch_key}#attempt-0"
    branch_meta = {
        **batch_meta,
        "benchmark_run_key": {**run_key_values, "branch_role": role},
        "treatment_id": _text(_value(branch, "treatment_id", "none"), "treatment_id"),
        "authority": _text(_value(branch, "authority", "protocol"), "authority"),
        "non_target_budget": dict(_value(branch, "non_target_budget", {}) or {}),
        "untreated_history_id": run_key_values["untreated_history_id"],
    }
    metadata = {**dict(_value(batch, "metadata", {}) or {}), **branch_meta}
    checkpoint_meta = {
        **metadata,
        "checkpoint_digest": _value(checkpoint, "digest", ""),
        "task_contract_digest": batch_meta["anchor_digest"],
    }
    anchor = _value(batch, "anchor", {})
    scene_key = _text(_value(anchor, "scene_key", _value(batch, "scene_key", "benchmark")), "scene_key")
    task_id = _text(_value(anchor, "anchor_id", batch_meta["anchor_id"]), "task_id")
    task_version = _text(_value(anchor, "anchor_id", "task.v1"), "task_version")
    artifact = _checkpoint_artifact(checkpoint, checkpoint_meta)
    digest_seed = int(hashlib.sha256("\x1f".join(pair_key).encode()).hexdigest()[:8], 16)
    return BranchJob(
        run_id=_text(run_id, "run_id"), system_id=run_key_values["system_id"],
        scene_key=scene_key, task_id=task_id,
        condition_id=run_key_values["condition_id"], slot=int(slot),
        branch_type=role, branch_id=branch_id, pair_group_id=pair_group_id,
        repeat_id=run_key_values["repeat_id"], pair_seed=digest_seed,
        base_instance_id=run_key_values["base_instance_id"],
        checkpoint_digest=artifact.digest, restore_checkpoint_digest=artifact.digest,
        task_version=task_version, system_version=run_key_values["system_id"],
        source_revision=batch_meta["benchmark_manifest_digest"],
        intervention_point=branch_meta["treatment_id"], execution_scope=execution_scope,
        task_contract_digest=str(batch_meta["anchor_digest"]), metadata=metadata,
    )


def branch_jobs_from_batch(
    batch: Any,
    *,
    execution_scope: str = "pending",
    run_id: str = "benchmark",
) -> tuple[BranchJob, ...]:
    """Convert every branch in a benchmark batch without changing identity."""

    branches = _value(batch, "branches", None)
    if branches is None and isinstance(batch, Mapping):
        branches = batch.get("branches", ())
    branches = tuple(branches or ())
    if not branches:
        raise BenchmarkBridgeError("benchmark batch contains no branches")
    pair_slots: dict[tuple[str, ...], int] = {}
    jobs: list[BranchJob] = []
    pair_fields = ("base_instance_id", "system_id", "condition_id", "repeat_id", "checkpoint_round")
    for item in branches:
        key = _run_key(item)
        pair_key = tuple(_text(_value(key, field, ""), field) for field in pair_fields)
        slot = pair_slots.setdefault(pair_key, len(pair_slots))
        jobs.append(branch_job_from_spec(
            item, batch=batch, slot=slot, execution_scope=execution_scope,
            run_id=run_id,
        ))
    return tuple(jobs)


def validate_jobs_against_batch(jobs: Sequence[BranchJob], batch: Any) -> dict[str, Any]:
    """Check that a projected job list still names the benchmark batch."""

    expected = tuple(_value(batch, "branches", ()) or ())
    def _job_key(job: BranchJob) -> tuple[str, ...]:
        values = job.metadata.get("benchmark_run_key", {})
        return tuple(str(values.get(field, "")) for field in (
            "base_instance_id", "system_id", "condition_id", "repeat_id",
            "checkpoint_round", "branch_role",
        ))

    actual_keys = {_job_key(job) for job in jobs}
    expected_keys = {
        tuple(_text(_value(_run_key(branch), field, ""), field)
              for field in ("base_instance_id", "system_id", "condition_id",
                            "repeat_id", "checkpoint_round")) + (_role(_run_key(branch)),)
        for branch in expected
    }
    manifest_digest = _text(_value(batch, "manifest_digest", ""), "manifest_digest")
    observed = {str(job.metadata.get("benchmark_manifest_digest", "")) for job in jobs}
    if actual_keys != expected_keys:
        raise BenchmarkBridgeError("projected jobs changed benchmark branch identity")
    if observed != {manifest_digest}:
        raise BenchmarkBridgeError("projected jobs lost benchmark_manifest_digest")
    return {
        "passed": True,
        "branch_count": len(jobs),
        "manifest_digest": manifest_digest,
        "strict_pair_required": bool(_value(batch, "strict_pair_required", False)),
        "strict_pair_claim_status": "unsupported",
    }


def branch_plan_from_batch(
    batch: Any,
    *,
    execution_scope: str = "pending",
    run_id: str = "benchmark",
) -> BranchPlan:
    """Build an explicit-job plan suitable for :class:`BranchExecutor`."""

    jobs = branch_jobs_from_batch(batch, execution_scope=execution_scope, run_id=run_id)
    validate_jobs_against_batch(jobs, batch)
    first = jobs[0]
    metadata = dict(first.metadata)
    return BranchPlan(
        scene_key=first.scene_key, task_id=first.task_id, system_id=first.system_id,
        condition_id=first.condition_id, task_version=first.task_version,
        system_version=first.system_version, source_revision=first.source_revision,
        slots=max(1, len({job.pair_group_id for job in jobs})),
        arms=tuple(dict.fromkeys(job.branch_type for job in jobs)), repeats=1,
        pair_seed=first.pair_seed, run_id=first.run_id,
        execution_scope=execution_scope, metadata=metadata, explicit_jobs=jobs,
    )


__all__ = [
    "BenchmarkBridgeError", "branch_job_from_spec", "branch_jobs_from_batch",
    "branch_plan_from_batch", "validate_jobs_against_batch",
]
