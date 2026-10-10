"""Compatibility bridge from the paper benchmark identities to the collector.

``benchmark/`` owns ``ExperimentBatch``, ``RunKey`` and treatment semantics.
This module only serialises those identities into collector jobs; it never
recomputes a base instance id or decides whether a pair is scientifically
valid.  The bridge accepts both live benchmark dataclasses and detached JSON
manifests so a resumed run can be reconstructed without importing a simulator.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, is_dataclass, replace
from copy import deepcopy
import hashlib
from typing import Any

from .models import BRANCH_ROLES, BranchJob, BranchPlan, CheckpointArtifact


class BenchmarkBridgeError(ValueError):
    """Raised when a collector job would lose benchmark identity."""


_MISSING = object()


def _resolved(value: Any, name: str, default: Any = "") -> Any:
    """Read a field and evaluate zero-argument identity accessors."""

    result = _value(value, name, default)
    return result() if callable(result) else result


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
        return deepcopy(dict(value))
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        result = to_dict()
        if isinstance(result, Mapping):
            return deepcopy(dict(result))
    if is_dataclass(value):
        return dict(asdict(value))
    values = getattr(value, "__dict__", None)
    if isinstance(values, Mapping):
        return dict(values)
    raise BenchmarkBridgeError(f"benchmark object is not mapping-compatible: {type(value).__name__}")


def _role(value: Any) -> str:
    raw = _resolved(value, "branch_role", value)
    return _text(getattr(raw, "value", raw), "branch_role")


def _run_key(branch: Any) -> Any:
    key = _resolved(branch, "run_key", None)
    if key is None:
        raise BenchmarkBridgeError("branch is missing run_key")
    return key


def _checkpoint(batch: Any, branch: Any) -> Any:
    checkpoint = _resolved(branch, "checkpoint", None) or _resolved(batch, "checkpoint", None)
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
    anchor_digest = _text(
        _resolved(batch, "anchor_digest", manifest.get("anchor_digest", "")),
        "anchor_digest",
    )
    strict_pair = _resolved(batch, "strict_pair_required", _MISSING)
    if strict_pair is _MISSING:
        strict_pair = _resolved(cell, "strict_pair_required", _MISSING)
    if type(strict_pair) is not bool:
        raise BenchmarkBridgeError("benchmark field strict_pair_required must be a boolean")
    return {
        "benchmark_authority": "benchmark",
        "benchmark_manifest_digest": manifest_digest,
        "benchmark_plan_digest": plan_digest,
        "anchor_id": _text(_value(batch, "anchor_id", cell_data.get("anchor_id", _value(anchor, "anchor_id", ""))), "anchor_id"),
        "anchor_digest": anchor_digest,
        "checkpoint_round": _text(cell_data.get("checkpoint_round", ""), "checkpoint_round"),
        "strict_pair_required": strict_pair,
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
        "base_instance_id": _text(_resolved(key, "base_instance_id", ""), "base_instance_id"),
        "system_id": _text(_resolved(key, "system_id", ""), "system_id"),
        "condition_id": _text(_resolved(key, "condition_id", ""), "condition_id"),
        "repeat_id": _text(_resolved(key, "repeat_id", ""), "repeat_id"),
        "checkpoint_round": _text(_resolved(key, "checkpoint_round", ""), "checkpoint_round"),
        "untreated_history_id": _text(_resolved(key, "untreated_history_id", ""), "untreated_history_id"),
    }
    pair_key = tuple(run_key_values[name] for name in (
        "base_instance_id", "system_id", "condition_id", "repeat_id", "checkpoint_round"
    ))
    pair_group_value = _resolved(branch, "pair_group_id", None)
    if pair_group_value is None:
        pair_group_value = _resolved(key, "pair_group_id", None)
    pair_group_id = (_text(pair_group_value, "pair_group_id") if pair_group_value is not None
                     else "benchmark/pair/" + hashlib.sha256("\x1f".join(pair_key).encode()).hexdigest()[:24])
    branch_key = "/".join((*pair_key, role))
    source_branch_id = _resolved(branch, "branch_id", None)
    if source_branch_id is None:
        source_branch_id = _resolved(key, "branch_id", None)
    if source_branch_id is None:
        branch_id = f"{run_id}/{branch_key}#attempt-0"
        source_attempt = 0
    else:
        source_branch_id = _text(source_branch_id, "branch_id")
        branch_id = source_branch_id
        source_attempt = 0
        if "#attempt-" in source_branch_id:
            try:
                source_attempt = int(source_branch_id.rsplit("#attempt-", 1)[1])
            except (TypeError, ValueError) as exc:
                raise BenchmarkBridgeError("benchmark branch_id has an invalid attempt suffix") from exc
    branch_meta = {
        **batch_meta,
        "benchmark_run_key": {**run_key_values, "branch_role": role},
        "treatment_id": _text(_resolved(branch, "treatment_id", ""), "treatment_id"),
        "authority": _text(_resolved(branch, "authority", ""), "authority"),
        "non_target_budget": _mapping(_resolved(branch, "non_target_budget", {})),
        "checkpoint_ref": _mapping(checkpoint),
        "untreated_history_id": run_key_values["untreated_history_id"],
        "benchmark_branch_id": branch_id.split("#attempt-", 1)[0],
    }
    for field in ("treatment_id", "authority", "non_target_budget"):
        if _value(branch, field, None) is None:
            raise BenchmarkBridgeError(f"benchmark field {field} is required")
    pair_identity = _resolved(key, "pair_key", None)
    if pair_identity is not None:
        branch_meta["benchmark_pair_key"] = deepcopy(pair_identity)
    metadata = {**_mapping(_value(batch, "metadata", {}) or {}), **branch_meta}
    checkpoint_meta = {
        **metadata,
        "checkpoint_digest": _value(checkpoint, "digest", ""),
        "task_contract_digest": batch_meta["anchor_digest"],
    }
    anchor = _resolved(batch, "anchor", {})
    scene_key = _text(_resolved(anchor, "scene_key", _resolved(batch, "scene_key", "benchmark")), "scene_key")
    task_id = _text(_resolved(anchor, "anchor_id", batch_meta["anchor_id"]), "task_id")
    task_version = _text(_resolved(anchor, "task_version", "task.v1"), "task_version")
    system_version = _text(
        _resolved(branch, "system_version", _resolved(batch, "system_version", run_key_values["system_id"])),
        "system_version",
    )
    pair_seed_value = _resolved(key, "pair_seed", _resolved(branch, "pair_seed", None))
    pair_seed = int(pair_seed_value) if pair_seed_value is not None else int(hashlib.sha256("\x1f".join(pair_key).encode()).hexdigest()[:8], 16)
    rng_namespace = str(_resolved(branch, "rng_namespace", "") or "")
    communication_rng_namespace = str(_resolved(branch, "communication_rng_namespace", "") or "")
    artifact = _checkpoint_artifact(checkpoint, checkpoint_meta)
    return BranchJob(
        run_id=_text(run_id, "run_id"), system_id=run_key_values["system_id"],
        scene_key=scene_key, task_id=task_id,
        condition_id=run_key_values["condition_id"], slot=int(slot),
        branch_type=role, branch_id=branch_id, pair_group_id=pair_group_id,
        repeat_id=run_key_values["repeat_id"], pair_seed=pair_seed, attempt=source_attempt,
        base_instance_id=run_key_values["base_instance_id"],
        checkpoint_digest=artifact.digest, restore_checkpoint_digest=artifact.digest,
        task_version=task_version, system_version=system_version,
        source_revision=batch_meta["benchmark_manifest_digest"],
        intervention_point=branch_meta["treatment_id"], execution_scope=execution_scope,
        rng_namespace=rng_namespace,
        communication_rng_namespace=communication_rng_namespace,
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

    if not jobs:
        raise BenchmarkBridgeError("projected jobs are empty")
    projected = branch_jobs_from_batch(batch, execution_scope=jobs[0].execution_scope,
                                       run_id=jobs[0].run_id)
    expected = {job.logical_branch_id: job for job in projected}
    if len(jobs) != len(projected) or len({job.logical_branch_id for job in jobs}) != len(jobs):
        raise BenchmarkBridgeError("projected jobs changed benchmark branch count or duplicated identity")
    for job in jobs:
        original = expected.get(job.logical_branch_id)
        if original is None:
            raise BenchmarkBridgeError("projected jobs changed benchmark branch identity")
        # Retry changes execution attempt only; compare all other authoritative
        # fields, including checkpoint, treatment, authority and budget.
        # An upstream manifest may use a branch id without the collector's
        # ``#attempt-0`` suffix.  Preserve that exact id while allowing the
        # collector's retry suffix to be added later.
        expected_job = replace(original, branch_id=job.branch_id, attempt=job.attempt)
        if job != expected_job:
            raise BenchmarkBridgeError("projected jobs changed benchmark identity or evidence metadata")
    manifest_digest = _text(_value(batch, "manifest_digest", ""), "manifest_digest")
    return {
        "passed": True,
        "branch_count": len(jobs),
        "manifest_digest": manifest_digest,
        "strict_pair_required": bool(_value(batch, "strict_pair_required", False)),
        "strict_pair_claim_status": "unsupported",
    }


def benchmark_job_errors(job: BranchJob) -> list[str]:
    """Check the required identity envelope without implementing paper semantics."""
    meta = job.metadata
    errors = []
    for name in ("benchmark_manifest_digest", "benchmark_plan_digest", "treatment_id", "authority",
                 "untreated_history_id"):
        if not isinstance(meta.get(name), str) or not meta[name].strip():
            errors.append(f"missing benchmark metadata: {name}")
    if not isinstance(meta.get("non_target_budget"), Mapping):
        errors.append("missing benchmark metadata: non_target_budget")
    if type(meta.get("strict_pair_required")) is not bool:
        errors.append("missing benchmark metadata: strict_pair_required")
    key = meta.get("benchmark_run_key", {})
    if not isinstance(key, Mapping):
        key = {}
    for name in ("base_instance_id", "system_id", "condition_id", "repeat_id", "checkpoint_round", "branch_role"):
        expected = job.branch_type if name == "branch_role" else getattr(job, name, meta.get(name))
        if not key.get(name) or str(key[name]) != str(expected):
            errors.append(f"benchmark run key mismatch or missing: {name}")
    if key.get("untreated_history_id") != meta.get("untreated_history_id"):
        errors.append("benchmark untreated_history_id mismatch")
    ref = meta.get("checkpoint_ref", {})
    if not isinstance(ref, Mapping) or not ref.get("checkpoint_id") or not ref.get("digest"):
        errors.append("missing benchmark metadata: checkpoint_ref")
    elif ref["digest"] != job.checkpoint_digest:
        errors.append("benchmark checkpoint digest mismatch")
    if not job.checkpoint_digest or job.restore_checkpoint_digest != job.checkpoint_digest:
        errors.append("missing or mismatched checkpoint digest")
    return errors


def branch_plan_from_batch(
    batch: Any,
    *,
    execution_scope: str = "pending",
    run_id: str = "benchmark",
) -> BranchPlan:
    """Build an explicit-job plan suitable for :class:`BranchExecutor`."""

    jobs = branch_jobs_from_batch(batch, execution_scope=execution_scope, run_id=run_id)
    validate_jobs_against_batch(jobs, batch)
    identity_errors = {
        error for job in jobs for error in benchmark_job_errors(job)
    }
    if identity_errors:
        raise BenchmarkBridgeError("benchmark branch identity is incomplete: "
                                   + "; ".join(sorted(identity_errors)))
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
    "branch_plan_from_batch", "validate_jobs_against_batch", "benchmark_job_errors",
]
