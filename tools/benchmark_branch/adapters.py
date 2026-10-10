"""Scene/task plugin contracts and a deterministic protocol adapter.

The core executor only calls this interface.  A real scene package can
implement the methods with Isaac Sim or another engine without importing that
engine from :mod:`tools.benchmark_branch`.
"""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any, ClassVar, Mapping, Protocol, Sequence

from .models import BranchJob, BranchPlan, CheckpointArtifact, canonical_digest


class SceneAdapter(Protocol):
    """Boundary implemented by an upstream task package."""

    def validate_contract(self, plan: BranchPlan) -> Mapping[str, Any] | bool: ...
    def create_scene(self, job: BranchJob) -> Any: ...
    def prepare_base_instance(self, job: BranchJob, checkpoint: CheckpointArtifact) -> Any: ...
    def capture_checkpoint(self, runtime: Any, job: BranchJob) -> Mapping[str, Any] | CheckpointArtifact: ...
    def restore_checkpoint(self, runtime: Any, checkpoint: CheckpointArtifact) -> Mapping[str, Any] | bool: ...
    def validate_restore(self, runtime: Any, checkpoint: CheckpointArtifact, restored: Any) -> Mapping[str, Any] | bool: ...
    def apply_treatment(self, runtime: Any, job: BranchJob) -> None: ...
    def step(self, runtime: Any, job: BranchJob) -> Mapping[str, Any] | bool: ...
    def observe_external_state(self, runtime: Any, job: BranchJob) -> Mapping[str, Any]: ...
    def collect_events(self, runtime: Any, job: BranchJob) -> Sequence[Mapping[str, Any]]: ...
    def collect_progress(self, runtime: Any, job: BranchJob) -> Mapping[str, Any]: ...
    def collect_network(self, runtime: Any, job: BranchJob) -> Mapping[str, Any]: ...
    def collect_cost(self, runtime: Any, job: BranchJob) -> Mapping[str, Any]: ...
    def finish_episode(self, runtime: Any, job: BranchJob) -> Mapping[str, Any]: ...
    def close(self, runtime: Any) -> None: ...

    # Measured execution is opt-in.  A plugin that has not explicitly
    # certified its simulator/evaluator boundary is synthetic-only.
    supported_execution_scopes: ClassVar[frozenset[str]]


class SharedSessionAdapter(SceneAdapter, Protocol):
    """Optional vectorized backend for one scene-owned simulator session.

    A shared-session adapter owns exactly one simulator/Kit process and maps
    each ``BranchJob`` to an independent environment slot.  The callback is
    deliberately row based so the generic collector can keep the same
    append-only ledgers as the fresh-process backend.  Static scene assets may
    be shared by the adapter; mutable controller, task, queue, RNG,
    checkpoint and evaluator state must be keyed by ``job.slot``/branch id.

    ``num_envs`` is a capacity request, not a promise.  The adapter must
    report the observed resident/active/completed/queued counts and any
    cross-slot isolation violations in its returned mapping.
    """

    def run_shared_session(self, jobs: Sequence[BranchJob], checkpoint: CheckpointArtifact,
                           emit: Any, *, num_envs: int,
                           timeout_s: float | None = None) -> Mapping[str, Any]: ...


@dataclass
class DummyAdapter:
    """Deterministic adapter for scheduler, ledger and recovery tests.

    ``execution_scope`` is always ``protocol_synthetic`` unless explicitly
    overridden by the caller.  Its outcomes are protocol evidence only; they
    are never Isaac Sim capacity or physical-task evidence.
    """

    steps: int = 3
    step_delay_s: float = 0.03
    fail_slots: tuple[int, ...] = ()
    fail_branch_types: tuple[str, ...] = ()
    restore_mismatch_slots: tuple[int, ...] = ()
    sham_invalid_slots: tuple[int, ...] = ()
    scene_build_digest: str = "dummy-scene-build-v1"
    asset_resolution_digest: str = "dummy-assets-v1"
    scene_build_order_digest: str = "dummy-order-v1"
    skill_adapter_version: str = "dummy-skill-v1"
    evaluator_version: str = "dummy-evaluator-v1"
    supported_execution_scopes: ClassVar[frozenset[str]] = frozenset({"protocol_synthetic"})

    def __post_init__(self) -> None:
        self.steps = max(1, int(self.steps))
        self.step_delay_s = max(0.0, float(self.step_delay_s))
        self.fail_slots = tuple(int(item) for item in self.fail_slots)
        self.fail_branch_types = tuple(str(item) for item in self.fail_branch_types)
        self.restore_mismatch_slots = tuple(int(item) for item in self.restore_mismatch_slots)
        self.sham_invalid_slots = tuple(int(item) for item in self.sham_invalid_slots)

    def validate_contract(self, plan: BranchPlan) -> Mapping[str, Any]:
        return {
            "passed": True,
            "task_contract_digest": canonical_digest(plan.row()),
            "task_version": plan.task_version,
            "milestones": [{"id": "dummy_terminal", "weight": 1.0, "stable_window": 1}],
            "feasibility_oracle": {"status": "synthetic", "witness_hash": canonical_digest("dummy")},
        }

    def create_scene(self, job: BranchJob) -> dict[str, Any]:
        return {"scene_key": job.scene_key, "task_id": job.task_id, "slot": job.slot,
                "step": 0, "checkpoint_digest": job.checkpoint_digest}

    def create_checkpoint(self, plan: BranchPlan) -> CheckpointArtifact:
        contract = self.validate_contract(plan)
        state = {
            "schema": "dummy.base.v1",
            "scene_key": plan.scene_key,
            "task_id": plan.task_id,
            "system_id": plan.system_id,
            "conditions": list(plan.conditions or (plan.condition_id,)),
            "simulator_step": 0,
            "sim_time_s": 0.0,
            "public_events": [],
        }
        return CheckpointArtifact.from_state(
            state,
            metadata={
                "task_contract_digest": str(contract["task_contract_digest"]),
                "task_version": plan.task_version,
                "system_version": plan.system_version,
                "scene_build_digest": self.scene_build_digest,
                "asset_resolution_digest": self.asset_resolution_digest,
                "scene_build_order_digest": self.scene_build_order_digest,
                "skill_adapter_version": self.skill_adapter_version,
                "pair_seed": plan.pair_seed,
                "rng_namespace": "dummy/public",
                "communication_rng_namespace": "dummy/communication",
                "non_target_budget": dict(plan.metadata.get("non_target_budget", {})),
                "information_permissions": dict(plan.metadata.get("information_permissions", {})),
            },
        )

    def prepare_base_instance(self, job: BranchJob, checkpoint: CheckpointArtifact) -> dict[str, Any]:
        return {"step": 0, "role": job.branch_type, "checkpoint_digest": checkpoint.digest,
                "created_at_ns": time.time_ns(), "events": []}

    def capture_checkpoint(self, runtime: Any, job: BranchJob) -> Mapping[str, Any]:
        return {"step": int(runtime.get("step", 0)), "checkpoint_digest": runtime.get("checkpoint_digest", "")}

    def restore_checkpoint(self, runtime: Any, checkpoint: CheckpointArtifact) -> Mapping[str, Any]:
        runtime["restored_digest"] = checkpoint.digest
        return {"passed": True, "readback_digest": checkpoint.digest, "max_state_error": 0.0, "error_threshold": 0.0}

    def validate_restore(self, runtime: Any, checkpoint: CheckpointArtifact, restored: Any) -> Mapping[str, Any]:
        if runtime.get("slot", -1) in self.restore_mismatch_slots:
            return {"passed": False, "readback_digest": "mismatch", "max_state_error": 1.0,
                    "error_threshold": 1e-6, "error": "configured restore mismatch",
                    "error_source": "DummyAdapter"}
        passed = bool(restored and restored.get("readback_digest") == checkpoint.digest)
        return {"passed": passed, "readback_digest": restored.get("readback_digest") if isinstance(restored, Mapping) else "",
                "max_state_error": 0.0 if passed else 1.0, "error_threshold": 1e-6,
                "error_source": "DummyAdapter" if not passed else ""}

    def apply_treatment(self, runtime: Any, job: BranchJob) -> None:
        runtime["role"] = job.branch_type
        runtime["slot"] = job.slot
        runtime["treatment"] = job.intervention_point or job.condition_id

    def step(self, runtime: Any, job: BranchJob) -> Mapping[str, Any]:
        if job.slot in self.fail_slots or job.branch_type in self.fail_branch_types:
            raise RuntimeError("configured DummyAdapter worker failure")
        if self.step_delay_s:
            time.sleep(self.step_delay_s)
        runtime["step"] += 1
        done = runtime["step"] >= self.steps
        return {"done": done, "completed": done, "role": job.branch_type,
                "task_state": "terminal" if done else "running", "active": not done,
                "phase_state": "execution", "safety": "safe"}

    def observe_external_state(self, runtime: Any, job: BranchJob) -> Mapping[str, Any]:
        return {"evaluator_observation": "synthetic", "step": runtime["step"], "slot": job.slot}

    def collect_events(self, runtime: Any, job: BranchJob) -> Sequence[Mapping[str, Any]]:
        step = int(runtime["step"])
        if step == 1:
            message = {"message_id": f"{job.branch_id}/m1", "sender": "dummy", "receiver": "dummy"}
            return ({"type": "task_started"},
                    {"type": "message_sent", **message},
                    {"type": "message_arrived", **message},
                    {"type": "message_consumed", **message},
                    {"type": "decision_made", "consumed_message_ids": [message["message_id"]]},)
        if step >= self.steps:
            return ({"type": "task_completed"}, {"type": "termination"})
        return ()

    def collect_progress(self, runtime: Any, job: BranchJob) -> Mapping[str, Any]:
        return {"progress": min(1.0, runtime["step"] / self.steps), "milestones": {"dummy_terminal": runtime["step"] >= self.steps}}

    def collect_network(self, runtime: Any, job: BranchJob) -> Mapping[str, Any]:
        count = 1 if runtime["step"] == 1 else 0
        return {"message_count": count, "bytes": 32 if count else 0,
                "message_sent": count, "message_arrived": count, "message_consumed": count,
                "decision_made": count, "information_age_s": 0.0}

    def collect_cost(self, runtime: Any, job: BranchJob) -> Mapping[str, Any]:
        return {"commands": 1, "interactions": 1, "replans": 0, "reassignments": 0}

    def finish_episode(self, runtime: Any, job: BranchJob) -> Mapping[str, Any]:
        sham_valid = job.slot not in self.sham_invalid_slots
        status = "valid_terminal" if sham_valid else "sham_invalid"
        return {
            "status": status, "valid_terminal": sham_valid, "sham_valid": sham_valid,
            "milestones": {"dummy_terminal": sham_valid}, "first_satisfied_time_s": float(runtime["step"]),
            "stable_window": 1, "terminal_predicate": "dummy_terminal",
            "safety_predicate": True, "feasibility_predicate": True,
            "evidence_refs": [f"dummy://{job.branch_id}/episode"],
            "evaluator_version": self.evaluator_version,
            "evidence_hash": canonical_digest({"branch_id": job.branch_id, "step": runtime["step"]}),
            "score": 1.0 if sham_valid else None,
        }

    def close(self, runtime: Any) -> None:
        runtime["closed"] = True

    def run_shared_session(self, jobs: Sequence[BranchJob], checkpoint: CheckpointArtifact,
                           emit: Any, *, num_envs: int,
                           timeout_s: float | None = None) -> Mapping[str, Any]:
        """Exercise the one-session/vectorized contract without Isaac.

        The implementation intentionally keeps one mutable dictionary per
        branch slot and advances a wave of slots together.  It is useful for
        testing the collector and isolation invariants; its
        ``protocol_synthetic`` scope must never be interpreted as GPU physics
        evidence.
        """
        import os
        import time as _time
        import uuid as _uuid

        env_count = max(1, min(int(num_envs), len(jobs)))
        session_id = f"dummy-shared-{_uuid.uuid4()}"
        started = _time.monotonic()
        completed = failed = 0
        cross_slot_contacts = 0
        max_active = 0
        environment_slots: dict[str, int] = {}

        def row(kind: str, job: BranchJob, payload: Mapping[str, Any], *, event: bool = False) -> None:
            emit(kind, {
                **job.identity(),
                "shared_session_id": session_id,
                "worker_instance_id": f"env-slot-{environment_slots[job.branch_id]}",
                "worker_session_id": f"{session_id}/slot-{environment_slots[job.branch_id]}",
                "environment_slot": environment_slots[job.branch_id],
                "worker_pid": os.getpid(),
                "execution_scope": "protocol_synthetic",
                **dict(payload),
            }, event=event, job=job)

        # A physical environment slot is distinct from the experimental pair
        # slot. Each branch in a wave gets a unique environment slot.
        for offset in range(0, len(jobs), env_count):
            wave = list(jobs[offset:offset + env_count])
            states: dict[str, dict[str, Any]] = {}
            environment_slots = {job.branch_id: index for index, job in enumerate(wave)}
            for job in wave:
                runtime = self.prepare_base_instance(job, checkpoint)
                runtime["slot"] = job.slot
                runtime["branch_id"] = job.branch_id
                runtime["rng_namespace"] = job.rng_namespace
                restored = self.restore_checkpoint(runtime, checkpoint)
                valid = self.validate_restore(runtime, checkpoint, restored)
                restore_ok = bool(valid.get("passed", valid) if isinstance(valid, Mapping) else valid)
                row("checkpoints", job, {
                    "checkpoint_id": checkpoint.checkpoint_id,
                    "snapshot_digest": checkpoint.digest,
                    "restore_checkpoint_digest": checkpoint.digest,
                    "restore_passed": restore_ok,
                    "restore_status": "passed" if restore_ok else "failed",
                    "restore": dict(valid) if isinstance(valid, Mapping) else {"passed": restore_ok},
                })
                if not restore_ok:
                    row("episodes", job, {"status": "failed", "completed": False,
                                           "error": "restore mismatch"})
                    failed += 1
                    continue
                self.apply_treatment(runtime, job)
                states[job.branch_id] = runtime
                row("metrics", job, {"kind": "branch_slot_admitted", "slot": job.slot})

            active = set(states)
            max_active = max(max_active, len(active))
            while active:
                if timeout_s is not None and _time.monotonic() - started > timeout_s:
                    for branch_id in sorted(active):
                        job = next(item for item in wave if item.branch_id == branch_id)
                        row("episodes", job, {"status": "method_timeout", "completed": False,
                                               "error": "shared session timeout"})
                        failed += 1
                    break
                for branch_id in list(sorted(active)):
                    job = next(item for item in wave if item.branch_id == branch_id)
                    runtime = states[branch_id]
                    state = dict(self.step(runtime, job))
                    step = int(runtime.get("step", runtime.get("simulator_step", 0)))
                    row("steps", job, {"simulator_step": step, "sim_time_s": float(step),
                                       "post_step_state": state, "active": not bool(state.get("done")),
                                       "completed": bool(state.get("done")), "terminated": bool(state.get("done"))})
                    row("progress", job, {"simulator_step": step,
                                           "progress": self.collect_progress(runtime, job)})
                    for event_item in self.collect_events(runtime, job) or ():
                        row("events", job, {"simulator_step": step, **dict(event_item)}, event=True)
                    if bool(state.get("done", state.get("completed", False))):
                        outcome = dict(self.finish_episode(runtime, job))
                        row("episodes", job, {"status": "completed", "completed": True,
                                              "outcome": outcome, "simulator_step": step})
                        completed += 1
                        self.close(runtime)
                        active.remove(branch_id)
                if not active:
                    break
            # The synthetic adapter has no contacts at all; keeping this
            # explicit makes the cross-slot isolation field mandatory for the
            # measured adapter too.
            for runtime in states.values():
                if runtime.get("slot") is None:
                    cross_slot_contacts += 1
        return {
            "session_id": session_id,
            "observed_peak_branch_workers": max_active,
            "resident": env_count,
            "active": 0,
            "completed": completed,
            "failed": failed,
            "queued": 0,
            "cross_slot_contact_count": cross_slot_contacts,
            "slot_isolation_validated": cross_slot_contacts == 0,
            "execution_scope": "protocol_synthetic",
            "static_asset_policy": "shared_scene_assets",
            "dynamic_state_policy": "per_slot_state",
        }


class ModernHouseExampleAdapter(DummyAdapter):
    """Documentation adapter showing where a Modern House plugin is injected."""

    scene_build_digest = "modern-house-plugin-unbound"


class WarehouseExampleAdapter(DummyAdapter):
    """Documentation adapter showing where a Warehouse plugin is injected."""

    scene_build_digest = "warehouse-plugin-unbound"


__all__ = ["SceneAdapter", "SharedSessionAdapter", "DummyAdapter", "ModernHouseExampleAdapter", "WarehouseExampleAdapter"]
