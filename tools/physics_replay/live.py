"""Boundary-synchronized live checkpoints and a UI-independent command queue."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from queue import Empty, Full, Queue
from typing import Any, Callable, Iterable, Mapping

import numpy as np

from .adapters import IsaacLabStateAdapter
from .checkpoints import CheckpointStore
from .components import ComponentRegistry, ComponentSnapshot, EnvironmentCommandComponent, RngStateComponent
from .replayer import PhysicsReplayer
from .targets import TargetResolver
from .types import TargetSpec


class CheckpointBusy(RuntimeError):
    """A transient unsafe boundary that can be retried after normal steps."""


class RestoreRejected(ValueError):
    """Restore validation failed before any live simulation state was written."""


def report_errors(errors: Mapping[str, float]) -> dict[str, float | None]:
    return {key: value if np.isfinite(value) else None for key, value in errors.items()}


def scene_target_specs(base_env: Any) -> tuple[TargetSpec, ...]:
    specs = []
    scene = base_env.scene
    for collection_name in ("articulations", "rigid_objects"):
        for name, entity in sorted(getattr(scene, collection_name, {}).items()):
            path = str(entity.cfg.prim_path)
            path = path.replace("/World/{ENV_REGEX_NS}", "/World/envs/env_0")
            path = path.replace("{ENV_REGEX_NS}", "/World/envs/env_0")
            path = path.replace("env_.*", "env_0").replace("env_*", "env_0")
            channels = ["pose", "velocity"]
            if collection_name == "articulations":
                channels.extend(("joint_state", "joint_target"))
                if hasattr(entity.data, "joint_effort_target"):
                    channels.append("joint_effort_target")
            specs.append(TargetSpec(path, tuple(channels)))
    if not specs:
        raise ValueError("no checkpointable rigid bodies or articulations in the scene")
    for collection_name in ("deformable_objects", "rigid_object_collections", "surface_grippers"):
        if getattr(scene, collection_name, {}):
            raise ValueError(f"live checkpoints need an adapter for {collection_name}")
    return tuple(specs)


def state_errors(expected: Mapping[str, Any], actual: Mapping[str, Any]) -> dict[str, float]:
    if set(expected) != set(actual):
        raise ValueError("restored physical target set differs from the checkpoint")
    errors = {}
    for key, value in expected.items():
        reference = np.asarray(value, dtype=np.float64)
        restored = np.asarray(actual[key], dtype=np.float64)
        if reference.shape != restored.shape or not np.all(np.isfinite(restored)) or not np.all(np.isfinite(reference)):
            errors[key] = float("inf")
            continue
        if key.endswith("/pose"):
            errors[f"{key}/position_m"] = float(np.linalg.norm(reference[:3] - restored[:3]))
            first = reference[3:7]
            second = restored[3:7]
            norms = np.linalg.norm(first) * np.linalg.norm(second)
            cosine = abs(float(np.dot(first, second))) / norms if norms > 0 else 0.0
            if norms > 0 and (np.array_equal(first, second) or np.array_equal(first, -second)):
                cosine = 1.0
            errors[f"{key}/rotation_rad"] = float(2 * np.arccos(np.clip(cosine, 0, 1)))
        else:
            errors[key] = float(np.max(np.abs(reference - restored))) if reference.size else 0.0
            if key.endswith("/joint_state") and reference.size % 2 == 0 and reference.size:
                count = reference.size // 2
                difference = np.abs(reference - restored)
                errors[f"{key}/position"] = float(difference[:count].max())
                errors[f"{key}/velocity"] = float(difference[count:].max())
    return errors


@dataclass(frozen=True)
class LiveCheckpoint:
    identifier: int
    step_index: int
    label: str


class LiveCheckpointBackend:
    """Persist all registered scene state at a completed environment-step boundary."""

    def __init__(
        self,
        session: Any,
        output_dir: str | Path,
        *,
        components: Iterable[Any] = (),
        tolerance: float = 1e-5,
        safety_check: Callable[[], None] | None = None,
        restore_check: Callable[[], None] | None = None,
        exact: bool = False,
        physics_trace: Any = None,
    ):
        if not np.isfinite(tolerance) or tolerance < 0:
            raise ValueError("restore tolerance must be finite and non-negative")
        if exact and physics_trace is None:
            raise ValueError("exact verification requires post-solve physics measurements")
        self.exact = bool(exact)
        self.physics_trace = physics_trace
        self.session = session
        self.output_dir = Path(output_dir)
        self.tolerance = 0.0 if exact else float(tolerance)
        self.safety_check = safety_check or (lambda: None)
        self.restore_check = restore_check or (lambda: None)
        self.targets = TargetResolver(session.base_env).resolve_all(scene_target_specs(session.base_env))
        self.adapter = IsaacLabStateAdapter(session.base_env)
        self.replayer = PhysicsReplayer(
            self.adapter,
            session=session,
            targets=self.targets,
            tolerances={channel: self.tolerance for target in self.targets for channel in target.writable_channels},
        )
        self.registry = ComponentRegistry((
            EnvironmentCommandComponent(session.base_env, device=session.device),
            *components,
            RngStateComponent(streams=getattr(session.base_env, "replay_random_streams", None)),
        ))
        self.store = CheckpointStore(self.output_dir)
        self.checkpoints: list[LiveCheckpoint] = []
        self.last_report: dict[str, Any] | None = None
        self._next_identifier = max(self.store.list_steps(), default=-1) + 1

    def read_physics(self) -> dict[str, np.ndarray]:
        values = {}
        for target in self.targets:
            keys = tuple(f"{target.target_id}/{channel}" for channel in target.writable_channels)
            values.update(self.adapter.read(target, keys))
        return values

    def capture(self, step_index: int, label: str = "") -> LiveCheckpoint:
        self.safety_check()
        components = self.registry.capture_all()
        self.registry.validate_all(components)
        physics = self.read_physics()
        if any(not np.all(np.isfinite(value)) for value in physics.values()):
            raise ValueError("cannot save non-finite physical state")
        identifier = self._next_identifier
        checkpoint = LiveCheckpoint(identifier, int(step_index), label or f"Checkpoint {identifier + 1}")
        payload = dict(components)
        payload["physics"] = ComponentSnapshot("physics", 1, {
            "step_index": checkpoint.step_index,
            "label": checkpoint.label,
            "state": physics,
            "targets": [target.to_dict() for target in self.targets],
        })
        self.store.write(identifier, payload)
        self._next_identifier += 1
        self.checkpoints.append(checkpoint)
        return checkpoint

    def read_trajectory_state(self) -> dict[str, np.ndarray]:
        values = self.read_physics()
        for component in self.registry.components:
            reader = getattr(component, "read_trajectory_state", None)
            if reader is not None:
                values.update(reader())
        return values

    def read_verification_state(self) -> dict[str, np.ndarray]:
        values = self.read_trajectory_state()
        if self.physics_trace is not None:
            values.update(self.physics_trace.take_frame())
        return values

    def _write_report(self, result: dict[str, Any]) -> None:
        self.last_report = result
        self.output_dir.mkdir(parents=True, exist_ok=True)
        destination = self.output_dir / "last_restore.json"
        temporary = destination.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        temporary.replace(destination)

    def restore(self, identifier: int) -> dict[str, Any]:
        try:
            if identifier not in {checkpoint.identifier for checkpoint in self.checkpoints}:
                raise ValueError("only checkpoints saved by this live session can be restored")
            self.restore_check()
            snapshots = self.store.read(identifier)
            physical = snapshots.pop("physics")
            if physical.name != "physics" or physical.version != 1 or int(physical.state["step_index"]) < 0:
                raise ValueError("invalid physical checkpoint metadata")
            if physical.state["targets"] != [target.to_dict() for target in self.targets]:
                raise ValueError("checkpoint targets differ from the live session")
            self.registry.validate_all(snapshots)
            self.replayer.validate_snapshot(physical.state["state"])
        except CheckpointBusy:
            raise
        except (ValueError, RuntimeError, OSError, TypeError, KeyError) as exc:
            result = {"checkpoint_id": identifier, "passed": False, "phase": "validation", "state_may_have_changed": False, "error": str(exc)}
            try:
                self._write_report(result)
            except OSError:
                pass
            raise RestoreRejected(f"Restore rejected before writes: {exc}. Current state is unchanged.") from exc
        ordered = sorted(self.registry.components, key=lambda item: (item.priority, item.name))
        try:
            for component in ordered:
                if component.priority < 20:
                    component.restore_state(snapshots[component.name].state)
            report = self.replayer.restore_snapshot(physical.state["state"], physical.state["step_index"])
            if not report.ok:
                raise RuntimeError(report.summary)
            for component in ordered:
                if component.priority >= 20:
                    component.restore_state(snapshots[component.name].state)
            errors = state_errors(physical.state["state"], self.read_physics())
        except Exception as exc:
            for component in ordered:
                callback = getattr(component, "on_restore_failed", None)
                if callable(callback):
                    callback()
            result = {"checkpoint_id": identifier, "passed": False, "phase": "restore", "state_may_have_changed": True, "error": str(exc)}
            try:
                self._write_report(result)
            except OSError:
                pass
            raise
        passed = bool(errors) and all(value <= self.tolerance for value in errors.values())
        result = {
            "checkpoint_id": int(identifier),
            "step_index": int(physical.state["step_index"]),
            "passed": passed,
            "phase": "readback",
            "state_may_have_changed": True,
            "readback_errors": report_errors(errors),
            "tolerance": self.tolerance,
            "restored_components": [component.name for component in ordered],
            "fidelity": "observable_state_checkpoint",
            "solver_cache_restored": False,
            "trajectory_verified": False,
        }
        self._write_report(result)
        if not passed:
            for component in ordered:
                callback = getattr(component, "on_restore_failed", None)
                if callable(callback):
                    callback()
            raise RuntimeError("restored state exceeds tolerance; simulation remains paused")
        return result


class LiveReplayController:
    """Consume UI requests only on the simulator thread, outside env.step()."""

    def __init__(self, backend: Any):
        self.backend = backend
        self._read_trajectory = getattr(backend, "read_trajectory_state", backend.read_physics)
        self._read_verification = getattr(backend, "read_verification_state", self._read_trajectory)
        self.requests: Queue = Queue(maxsize=32)
        self.paused = False
        self.blocked = False
        self.step_index = 0
        self.selected: int | None = None
        self.pending_save = False
        self.save_error: str | None = None
        self.verification_report: dict[str, Any] | None = None
        self._verification: dict[str, Any] | None = None
        self._single_step = False
        self._fresh_checkpoint: int | None = None
        self._pending_restore: int | None = None
        self.message = "F5 Save | F9 Restore | F8 Pause | F10 Step"

    def request(self, command: str, value: Any = None) -> None:
        try:
            self.requests.put_nowait((command, value))
        except Full:
            self.message = "Replay request queue is full; wait for the next boundary"

    @property
    def can_capture(self) -> bool:
        return not self.blocked and self._verification is None and self._pending_restore is None

    def capture_checkpoint(self, label: str = "") -> LiveCheckpoint:
        if not self.can_capture:
            raise CheckpointBusy("restore/verification must finish before saving")
        checkpoint = self.backend.capture(self.step_index, label) if label else self.backend.capture(self.step_index)
        self.selected = checkpoint.identifier
        self._fresh_checkpoint = self.selected
        self.message = f"Saved {checkpoint.label} at step {checkpoint.step_index}"
        return checkpoint

    def _restore_checkpoint(self, identifier: int) -> dict[str, Any]:
        self._fresh_checkpoint = None
        try:
            report = self.backend.restore(identifier)
        except (CheckpointBusy, RestoreRejected):
            raise
        except Exception:
            self.blocked = True
            raise
        self.step_index = int(report["step_index"])
        self.blocked = False
        return report

    def poll(self) -> bool:
        if self._pending_restore is not None:
            self.request("restore", self._pending_restore)
            self._pending_restore = None
        while True:
            try:
                command, value = self.requests.get_nowait()
            except Empty:
                break
            try:
                if self._verification is not None:
                    raise ValueError("verification is running; wait for both trajectories to finish")
                if command == "save":
                    self.pending_save = True
                    self.save_error = None
                elif command == "select":
                    identifier = int(value)
                    if identifier not in {item.identifier for item in self.backend.checkpoints}:
                        raise ValueError("checkpoint is not part of this session")
                    self.selected = identifier
                elif command == "restore":
                    self.paused = True
                    self._single_step = False
                    self.pending_save = False
                    if value is not None:
                        self.selected = int(value)
                    if self.selected is None:
                        raise ValueError("save a checkpoint before restoring")
                    try:
                        report = self._restore_checkpoint(self.selected)
                    except CheckpointBusy:
                        self._pending_restore = self.selected
                        raise
                    maximum = max(report["readback_errors"].values(), default=0.0)
                    self.message = f"Restored step {self.step_index}; max error {maximum:.3g}; PAUSED"
                elif command == "pause":
                    if self._pending_restore is not None:
                        raise RuntimeError("restore is waiting for a worker; simulation remains paused")
                    if self.blocked:
                        raise RuntimeError("restore failed; restore a valid checkpoint before continuing")
                    self.paused = not self.paused
                    self._single_step = False
                elif command == "step":
                    if self._pending_restore is not None:
                        raise RuntimeError("restore is waiting for a worker; simulation remains paused")
                    if self.blocked:
                        raise RuntimeError("cannot step after a failed restore")
                    self.paused = True
                    self._single_step = True
                elif command == "verify":
                    self.paused = True
                    if self.selected is None:
                        raise ValueError("save a checkpoint before verification")
                    count = int(value or 20)
                    if count <= 0:
                        raise ValueError("verification step count must be positive")
                    original_reference = self._fresh_checkpoint == self.selected
                    if getattr(self.backend, "exact", False) and not original_reference:
                        raise ValueError("exact verification requires a fresh checkpoint and an uninterrupted original reference")
                    if not original_reference:
                        self._restore_checkpoint(self.selected)
                    self.blocked = False
                    self.verification_report = None
                    self.pending_save = False
                    self._single_step = False
                    self._verification = {
                        "count": count, "done": 0, "phase": "reference", "checkpoint": self.selected,
                        "frames": [], "errors": {},
                        "first_divergence": None,
                        "comparison": "original_vs_restored" if original_reference else "two_restored_runs",
                        "start": self._read_trajectory(),
                    }
                    trace = getattr(self.backend, "physics_trace", None)
                    if trace is not None:
                        trace.begin()
                    self.message = f"Verifying {count} steps: reference run"
                else:
                    raise ValueError(f"unknown replay command: {command}")
            except (ValueError, RuntimeError, OSError, TypeError, KeyError) as exc:
                self.message = str(exc)
        if self.pending_save and not self.blocked:
            try:
                self.capture_checkpoint()
                self.pending_save = False
            except CheckpointBusy as exc:
                self.message = f"Save pending: {exc}. Run/step until safe."
            except (ValueError, RuntimeError, OSError, TypeError, KeyError) as exc:
                self.pending_save = False
                self.save_error = str(exc)
                self.message = f"Save failed: {exc}"
        return not self.blocked and self._pending_restore is None and (not self.paused or self._single_step or self._verification is not None)

    def after_step(self) -> None:
        self.step_index += 1
        self._fresh_checkpoint = None
        self._single_step = False
        verification = self._verification
        if verification is None:
            return
        self.paused = True
        try:
            current = self._read_verification()
            if verification["phase"] == "reference":
                verification["frames"].append(current)
            else:
                errors = state_errors(verification["frames"][verification["done"]], current)
                threshold = 0.0 if getattr(self.backend, "exact", False) else 1e-3
                different = {key: value for key, value in errors.items() if value > threshold}
                if different and verification["first_divergence"] is None:
                    verification["first_divergence"] = {
                        "environment_step_offset": verification["done"] + 1,
                        "errors": report_errors(different),
                    }
                for key, value in errors.items():
                    verification["errors"][key] = max(verification["errors"].get(key, 0.0), value)
            verification["done"] += 1
            if verification["done"] < verification["count"]:
                return
            if verification["phase"] == "reference":
                verification["phase"] = "restore"
                self._restore_checkpoint(verification["checkpoint"])
                verification["phase"] = "replay"
                verification["done"] = 0
                self.message = f"Verifying {verification['count']} steps: replay run"
                return
            errors = verification["errors"]
            threshold = 0.0 if getattr(self.backend, "exact", False) else 1e-3
            passed = bool(errors) and all(value <= threshold for value in errors.values())
            self.verification_report = {
                "steps": verification["count"], "checkpoint_id": verification["checkpoint"],
                "passed": passed, "trajectory_errors": report_errors(errors), "threshold": threshold,
                "comparison": verification["comparison"], "aggregation": "per_frame_maximum",
                "reference_motion": report_errors(state_errors(
                    verification["start"], {key: verification["frames"][-1][key] for key in verification["start"]},
                )),
                "solver_cache_restored": False,
                "measurement_scope": "physics_substeps_and_environment_boundaries" if getattr(self.backend, "physics_trace", None) else "environment_boundaries",
                "zero_deviation_required": bool(getattr(self.backend, "exact", False)),
                "complete_physical_state_restored": False,
                "first_divergence": verification["first_divergence"],
            }
            reader = getattr(getattr(self.backend, "physics_trace", None), "reader", None)
            if reader is not None and hasattr(reader, "describe"):
                self.verification_report["measurement_layout"] = reader.describe()
            self.blocked = not passed
            self.message = f"Trajectory {'PASS' if passed else 'FAIL'}; max error {max(errors.values()):.3g}; PAUSED"
        except (ValueError, RuntimeError, OSError) as exc:
            self.blocked = not isinstance(exc, RestoreRejected)
            self.verification_report = {
                "passed": False, "error": str(exc), "phase": verification["phase"],
                "environment_step_offset": verification["done"] + 1,
                "solver_cache_restored": False, "complete_physical_state_restored": False,
                "zero_deviation_required": bool(getattr(self.backend, "exact", False)),
            }
            self.message = str(exc)
        self._verification = None
        trace = getattr(self.backend, "physics_trace", None)
        if trace is not None:
            trace.finish()
        destination = self.backend.output_dir / "trajectory_verification.json"
        destination.write_text(json.dumps(self.verification_report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
