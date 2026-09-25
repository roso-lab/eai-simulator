"""Fire Rescue projection into the shared branch-data ledgers.

The Fire task runner remains responsible for scene creation, the shared
physics barrier, task state, and public checkpoint capture/restore.  This
adapter observes those boundaries and writes the scene-neutral ledgers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from demo.benchmark.branch_data import (
    BranchDataBatch,
    BranchIdentity,
    digest_state,
    digest_value,
    open_batch,
    source_revision,
    validate_run,
)


def _json_value(value: Any) -> Any:
    if hasattr(value, "detach"):
        value = value.detach()
    if hasattr(value, "cpu"):
        value = value.cpu()
    if hasattr(value, "tolist"):
        value = value.tolist()
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if value is None or type(value) in (str, bool, int, float):
        return value
    return str(value)


def _row(value: Any) -> Any:
    result = _json_value(value)
    if isinstance(result, list) and len(result) == 1 and isinstance(result[0], list):
        return result[0]
    return result


@dataclass
class FireBranchDataAdapter:
    """Observe a live Fire shared batch without owning its task control."""

    sim: Any
    batch: BranchDataBatch
    checkpoint_id: str
    assignments: Sequence[Path]
    fingerprint: Mapping[str, Any]
    started_monotonic: float
    scene_key: str = "factory"
    task_id: str = "fire_rescue"
    snapshot_schema: str = "fire_rescue.public_checkpoint.v2"
    _branch_id_by_slot: dict[int, str] = field(default_factory=dict)
    _restore_passed: dict[int, bool] = field(default_factory=dict)
    _seen_checkpoints: dict[int, set[str]] = field(default_factory=dict)
    _last_progress_step: dict[int, int] = field(default_factory=dict)
    _closed: bool = False

    @classmethod
    def open(
        cls,
        *,
        sim: Any,
        output: Path,
        checkpoint_id: str,
        assignments: Sequence[Path],
        fingerprint: Mapping[str, Any],
        started_monotonic: float,
    ) -> "FireBranchDataAdapter":
        revision = source_revision(Path(__file__).resolve().parents[2])
        batch = open_batch(
            output,
            scene_key="factory",
            task_id="fire_rescue",
            requested=len(assignments),
            execution_scope="measured",
            task_version="fire_rescue.task.v1",
            source_revision_value=revision,
            formal=len(assignments) == 32,
            metadata={
                "runner": (
                    "demo.fire_rescue.branching.experiments."
                    "shared_checkpoint_branches"
                ),
                "checkpoint": checkpoint_id,
                "checkpoint_scope": (
                    "public task/physics/runtime/RNG state; private PhysX "
                    "solver/contact/warm-start caches excluded"
                ),
            },
            autosave_every=max(128, len(assignments) * 8),
        )
        return cls(
            sim=sim,
            batch=batch,
            checkpoint_id=checkpoint_id,
            assignments=tuple(assignments),
            fingerprint=dict(fingerprint),
            started_monotonic=float(started_monotonic),
        )

    @property
    def output(self) -> Path:
        return self.batch.output

    @property
    def closed(self) -> bool:
        return self._closed

    def create_scene(self, *, slots: int, headless: bool) -> Any:
        raise RuntimeError("Fire scene creation remains owned by the shared task runner")

    def read_all_branch_states(self) -> list[dict[str, Any]]:
        environment = self.sim.base_env
        origins = environment.scene.env_origins
        count = self.batch.requested
        articulations: dict[str, list[Any]] = {}
        for name, entity in environment.scene.articulations.items():
            values = entity.data.root_state_w.clone()
            values[:, :3] -= origins
            articulations[name] = _json_value(values)
        rigid_objects: dict[str, list[Any]] = {}
        for name, entity in environment.scene.rigid_objects.items():
            values = entity.data.root_state_w.clone()
            values[:, :3] -= origins
            rigid_objects[name] = _json_value(values)
        return [
            {
                "articulations": {
                    name: rows[slot] for name, rows in articulations.items()
                },
                "rigid_objects": {
                    name: rows[slot] for name, rows in rigid_objects.items()
                },
            }
            for slot in range(count)
        ]

    def read_branch_state(self, runtime: Any, slot: int) -> Mapping[str, Any]:
        return self.read_all_branch_states()[slot]

    def apply_participant_actions(
        self, runtime: Any, actions: Mapping[str, Any]
    ) -> None:
        raise RuntimeError("Fire action application remains owned by SharedTaskSlotScheduler")

    def step_simulation(self, runtime: Any) -> int:
        raise RuntimeError("Fire stepping remains owned by SharedTaskSlotScheduler")

    @staticmethod
    def capture_checkpoint(runtime: Any) -> Mapping[str, Any]:
        return runtime.capture_live_state()

    @staticmethod
    def restore_checkpoint(
        runtime: Any, snapshot: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        runtime.restore_public_checkpoint(snapshot)
        return dict(runtime.checkpoint_restore_validation or {})

    @staticmethod
    def task_status(runtime: Any, slot: int) -> Mapping[str, Any]:
        return dict(runtime._async_last_logical.get(slot, {}))

    @staticmethod
    def scene_payload(runtime: Any, slot: int) -> Mapping[str, Any]:
        logical = runtime._async_last_logical.get(slot, {})
        return {
            "hazard": _json_value(logical.get("hazard")),
            "rescue": _json_value(logical.get("rescue")),
            "ur5": _json_value(logical.get("ur5")),
        }

    def _identity(self, slot: int, checkpoint_digest: str) -> BranchIdentity:
        assignment = self.assignments[slot]
        assignment_digest = digest_value(
            __import__("json").loads(assignment.read_text(encoding="utf-8"))
        )
        seed = int(self.fingerprint.get("seed", 0))
        hazard = int(self.fingerprint.get("hazard", 0))
        base_instance_id = f"fire-rescue:h{hazard}:s{seed}:{checkpoint_digest[:16]}"
        pair_group_id = f"{base_instance_id}:{self.checkpoint_id}"
        return BranchIdentity(
            scene_key=self.scene_key,
            task_id=self.task_id,
            base_instance_id=base_instance_id,
            condition_id=f"assignment:{assignment_digest[:16]}",
            pair_seed=seed,
            pair_group_id=pair_group_id,
            branch_id=f"{pair_group_id}:fact:slot-{slot:03d}",
            branch_type="fact",
            slot=slot,
            checkpoint_digest=checkpoint_digest,
            task_version=self.batch.manifest["task_version"],
            source_revision=self.batch.manifest["source_revision"],
            execution_scope="measured",
        )

    @staticmethod
    def _restore_measurement(validation: Mapping[str, Any]) -> tuple[float | None, str]:
        arrays = validation.get("arrays")
        if not isinstance(arrays, Mapping) or not arrays:
            return None, "unavailable"
        values = [
            float(item["max_abs_error"])
            for item in arrays.values()
            if isinstance(item, Mapping) and item.get("max_abs_error") is not None
        ]
        if not values:
            return None, "unavailable"
        return max(values), "measured"

    def register_checkpoint(
        self,
        audits: Mapping[int, Any],
        checkpoint_state: Mapping[str, Any],
        scheduler: Any,
    ) -> None:
        checkpoint_digest = digest_state(checkpoint_state)
        simulator_step = int(checkpoint_state["step"])
        sim_time_s = simulator_step * float(self.sim.base_env.step_dt)
        for slot, audit in sorted(audits.items()):
            identity = self.batch.register_branch(
                self._identity(slot, checkpoint_digest))
            self._branch_id_by_slot[slot] = identity.branch_id
            validation = dict(audit.checkpoint_restore_validation or {})
            restore_error, restore_status = self._restore_measurement(validation)
            restore_passed = (
                bool(validation.get("passed")) if restore_error is not None else None
            )
            self._restore_passed[slot] = bool(restore_passed)
            self._seen_checkpoints[slot] = {
                name for name, active in scheduler._async_last_logical.get(
                    slot, {}
                ).get("checkpoints", {}).items() if active
            }
            self.batch.record_checkpoint(
                identity.branch_id,
                checkpoint_id=self.checkpoint_id,
                simulator_step=simulator_step,
                snapshot_digest=checkpoint_digest,
                snapshot_schema=self.snapshot_schema,
                restore_error=restore_error,
                restore_passed=restore_passed,
                restore_status=restore_status,
                restore_error_source=(
                    "max_abs_compare_state_array_error"
                    if restore_error is not None
                    else "compare_state_returned_no_array_measurements"
                ),
                payload={
                    "restore_attempts": len(audit.restore_history),
                    "readback_digest": validation.get("readback_digest"),
                    "solver_cache_serialized": False,
                },
            )
            self.batch.record_event(
                identity.branch_id,
                event_type="checkpoint_restored",
                simulator_step=simulator_step,
                sim_time_s=sim_time_s,
                payload={
                    "checkpoint_id": self.checkpoint_id,
                    "restore_error": restore_error,
                    "restore_status": restore_status,
                },
            )
            self.batch.record_progress(
                identity.branch_id,
                simulator_step=simulator_step,
                sim_time_s=sim_time_s,
                progress={
                    "phase": "checkpoint_restored",
                    "branch_completed": False,
                    "active_slots": 0,
                },
            )
            self.batch.record_network(
                identity.branch_id,
                simulator_step=simulator_step,
                sim_time_s=sim_time_s,
                network={
                    "messages_offered": 0,
                    "messages_delivered": 0,
                    "offered_bits": 0,
                    "delivered_bits": 0,
                    "scope": "shared Fire runner exposes no byte-accounted participant transport",
                },
            )

    def record_barrier(self, scheduler: Any) -> None:
        if not self._branch_id_by_slot or not scheduler.public_releases:
            return
        simulator_step = int(scheduler.physics_step_count)
        sim_time_s = simulator_step * float(self.sim.base_env.step_dt)
        states = self.read_all_branch_states()
        active_slots = set(
            scheduler.public_active_intervals[-1]["active_slots"]
            if scheduler.public_active_intervals else ()
        )
        step_records = []
        progress_slots: set[int] = set()
        for slot, branch_id in sorted(self._branch_id_by_slot.items()):
            if slot not in scheduler.public_releases:
                continue
            logical = _json_value(scheduler._async_last_logical.get(slot, {}))
            checkpoints = {
                name for name, active in logical.get("checkpoints", {}).items() if active
            }
            new_checkpoints = checkpoints - self._seen_checkpoints.setdefault(slot, set())
            completed = bool(
                logical.get("result") == "success"
                or logical.get("checkpoints", {}).get("mission_success")
            )
            active = slot in active_slots and not completed
            commands = {
                name: _row(value)
                for name, value in scheduler._async_last_actions.get(slot, {}).items()
            }
            step_records.append((branch_id, {
                "simulator_step": simulator_step,
                "sim_time_s": sim_time_s,
                "commands": commands,
                "post_step_state": states[slot],
                "phase_state": {
                    "emos_phase": logical.get("emos", {}).get("phase"),
                    "rescue_phase": logical.get("rescue", {}).get("phase"),
                    "result": logical.get("result"),
                },
                "task_state": logical,
                "active": active,
                "completed": completed,
                "scene_payload": {
                    "hazard": logical.get("hazard"),
                    "rescue": logical.get("rescue"),
                    "ur5": logical.get("ur5"),
                    "checkpoint_signals": logical.get("checkpoints", {}),
                },
            }))
            for checkpoint in sorted(new_checkpoints):
                self.batch.record_event(
                    branch_id,
                    event_type="task_checkpoint_reached",
                    simulator_step=simulator_step,
                    sim_time_s=sim_time_s,
                    payload={"checkpoint_id": checkpoint},
                )
            if new_checkpoints or simulator_step - self._last_progress_step.get(slot, -25) >= 25:
                progress_slots.add(slot)
            self._seen_checkpoints[slot].update(checkpoints)
        self.batch.record_step_batch(step_records)
        for slot in sorted(progress_slots):
            logical = scheduler._async_last_logical.get(slot, {})
            self.batch.record_progress(
                self._branch_id_by_slot[slot],
                simulator_step=simulator_step,
                sim_time_s=sim_time_s,
                progress={
                    "phase": "post_checkpoint_task",
                    "checkpoint_signals": _json_value(logical.get("checkpoints", {})),
                    "result": str(logical.get("result", "running")),
                    "active_slots": len(active_slots),
                    "completed_slots": len(scheduler.public_releases) - len(active_slots),
                },
            )
            self._last_progress_step[slot] = simulator_step

    def finish(self, report: Mapping[str, Any], *, wall_s: float) -> dict[str, Any]:
        simulator_step = int(report.get("physics_step_count", 0))
        sim_time_s = simulator_step * float(self.sim.base_env.step_dt)
        slots = {str(key): value for key, value in report.get("slots", {}).items()}
        full_task = report.get("full_task_completed_by_slot", {})
        isolation = bool(report.get("slot_isolation_verified"))
        for slot, branch_id in sorted(self._branch_id_by_slot.items()):
            slot_report = dict(slots.get(str(slot), slots.get(slot, {})) or {})
            completed = bool(full_task.get(str(slot), slot_report.get("result") == "success"))
            verified = bool(completed and isolation and self._restore_passed.get(slot))
            branch_report = (report.get("branch_reports", {}).get(slot)
                             or report.get("branch_reports", {}).get(str(slot)) or {})
            self.batch.record_network(
                branch_id,
                simulator_step=simulator_step,
                sim_time_s=sim_time_s,
                network={
                    "messages_offered": 0,
                    "messages_delivered": 0,
                    "offered_bits": 0,
                    "delivered_bits": 0,
                    "scope": "shared Fire runner exposes no byte-accounted participant transport",
                    "final": True,
                },
            )
            self.batch.record_cost(
                branch_id,
                simulator_step=simulator_step,
                sim_time_s=sim_time_s,
                cost={
                    "control_steps": int(branch_report.get("branch_steps", 0)),
                    "shared_batch_wall_s": float(wall_s),
                    "participant_messages": 0,
                    "effective_execution": verified,
                },
            )
            self.batch.record_event(
                branch_id,
                event_type="task_finished",
                simulator_step=simulator_step,
                sim_time_s=sim_time_s,
                payload={
                    "result": slot_report.get("result"),
                    "mission_success": completed,
                    "independently_verified": verified,
                },
            )
            self.batch.finish_branch(
                branch_id,
                completed=completed,
                independently_verified=verified,
                simulator_step=simulator_step,
                sim_time_s=sim_time_s,
                outcome={
                    "result": slot_report.get("result"),
                    "fail_reason": slot_report.get("fail_reason"),
                    "mission_success": completed,
                    "slot_isolation_verified": isolation,
                },
            )
        self.batch.close_batch(
            status="completed",
            resident=int(report.get("batched_physics_slots", len(self._branch_id_by_slot))),
            peak_active=int(report.get("public_peak_active_slots", 0)),
            queued=max(0, self.batch.requested - len(self._branch_id_by_slot)),
            details={
                "actual_device": str(self.sim.device),
                "solver_cache_serialized": False,
            },
        )
        self._closed = True
        return validate_run(self.output)

    def fail(self, error: BaseException) -> dict[str, Any]:
        if not self._closed:
            self.batch.close_batch(
                status="interrupted" if isinstance(error, KeyboardInterrupt) else "failed",
                resident=len(self._branch_id_by_slot),
                peak_active=int(self.batch.run_state.get("peak_active", 0)),
                queued=max(0, self.batch.requested - len(self._branch_id_by_slot)),
                details={"error": f"{type(error).__name__}: {error}"},
            )
            self._closed = True
        return validate_run(self.output)


__all__ = ["FireBranchDataAdapter"]
