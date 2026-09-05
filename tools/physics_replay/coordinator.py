"""Coordinate physical, component, and execution-tape replay."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

import numpy as np

from .checkpoints import CheckpointStore
from .components import (
    CheckpointableComponent,
    ComponentRegistry,
    EnvironmentCommandComponent,
    RngStateComponent,
)
from .recorder import ReplayRecorder
from .replayer import PhysicsReplayer
from .store import EpisodeReader
from .tape import ExecutionTape, actions_to_backend, apply_commands, capture_commands
from .types import RestoreReport, TargetSpec


@dataclass
class CoordinatorReport:
    step_index: int
    physical: RestoreReport
    restored_components: tuple[str, ...] = ()
    diagnostics: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.physical.ok

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_index": int(self.step_index),
            "ok": self.ok,
            "physical": {
                "status": self.physical.status,
                "step_index": self.physical.step_index,
                "channel_errors": self.physical.channel_errors,
                "unrestored_channels": list(self.physical.unrestored_channels),
                "diagnostics": list(self.physical.diagnostics),
            },
            "restored_components": list(self.restored_components),
            "diagnostics": list(self.diagnostics),
        }


class ReplayCoordinator:
    """Own recording inputs and restore a complete in-process checkpoint."""

    def __init__(
        self,
        session: Any,
        targets: Iterable[TargetSpec],
        output_dir: str | Path,
        *,
        components: Iterable[CheckpointableComponent] = (),
        include_rng: bool = True,
        segment_frames: int = 256,
    ):
        self._test_mode = False
        self.session = session
        self.target_specs = tuple(targets)
        self.output_dir = Path(output_dir)
        self.recorder = ReplayRecorder.from_session(
            session,
            self.target_specs,
            self.output_dir,
            sample_every=1,
            segment_frames=segment_frames,
        )
        default_components: list[CheckpointableComponent] = [
            EnvironmentCommandComponent(session.base_env, device=session.device)
        ]
        if include_rng:
            default_components.append(RngStateComponent())
        default_components.extend(components)
        self.registry = ComponentRegistry(default_components)
        self.checkpoints = CheckpointStore(self.output_dir)
        self.reader: EpisodeReader | None = None
        self.tape: ExecutionTape | None = None
        self._physical_replayer = PhysicsReplayer(
            self.recorder.adapter,
            session=session,
            targets=self.recorder.targets,
        )
        self._checkpoint_steps: set[int] = set()
        self.precomputed_action_steps: list[int] = []
        self.noise_consumer: Callable[[Any], None] | None = None

    @classmethod
    def for_test(
        cls,
        *,
        physical_restore: Callable[[int], RestoreReport],
        component_restore: Callable[[int], None],
        command_apply: Callable[[Mapping[str, Any]], None],
        env_step: Callable[[Mapping[str, Any]], Any],
        tape: Any,
    ) -> "ReplayCoordinator":
        instance = object.__new__(cls)
        instance._test_mode = True
        instance._test_physical_restore = physical_restore
        instance._test_component_restore = component_restore
        instance._test_command_apply = command_apply
        instance._test_env_step = env_step
        instance.tape = tape
        instance.noise_consumer = None
        return instance

    def capture_checkpoint(self, step_index: int) -> Path:
        if self._test_mode:
            raise RuntimeError("test coordinator does not persist checkpoints")
        step_index = int(step_index)
        path = self.checkpoints.write(step_index, self.registry.capture_all())
        self._checkpoint_steps.add(step_index)
        return path

    def record_before_step(
        self,
        actions: Mapping[str, Any],
        *,
        commands: Mapping[str, Any] | None = None,
        noise: Mapping[str, Any] | None = None,
    ) -> None:
        if self._test_mode:
            raise RuntimeError("test coordinator cannot record")
        command_values = capture_commands(self.session.base_env) if commands is None else commands
        self.recorder.before_step(actions, commands=command_values, noise=noise)

    def record_after_step(
        self,
        *,
        applied_actions: Mapping[str, Any] | None = None,
        phase: str | None = None,
        events: Iterable[Mapping[str, Any]] = (),
    ) -> None:
        if self._test_mode:
            raise RuntimeError("test coordinator cannot record")
        self.recorder.after_step(
            applied_actions=applied_actions,
            phase=phase,
            events=events,
        )

    def close_recording(self) -> EpisodeReader:
        if self._test_mode:
            raise RuntimeError("test coordinator cannot close a recording")
        self.recorder.close()
        self.reader = self.recorder.reader
        self.tape = ExecutionTape(self.reader)
        return self.reader

    def restore(self, step_index: int) -> CoordinatorReport:
        step_index = int(step_index)
        if self._test_mode:
            physical = self._test_physical_restore(step_index)
            if physical.ok:
                self._test_component_restore(step_index)
            return CoordinatorReport(step_index, physical)

        if self.reader is None or self.tape is None:
            raise RuntimeError("close_recording must be called before restore")
        self.reader.validate_integrity()
        self.tape.require_range(step_index, 1)
        snapshots = self.checkpoints.read(step_index)
        self.registry.validate_all(snapshots)
        physical = self._physical_replayer.restore_from_reader(self.reader, step_index)
        if not physical.ok:
            return CoordinatorReport(
                step_index,
                physical,
                diagnostics=("physical restore failed; components were not restored",),
            )
        self.registry.restore_all(snapshots)
        return CoordinatorReport(
            step_index,
            physical,
            restored_components=tuple(component.name for component in self.registry.components),
        )

    def replay_step(self, step_index: int) -> Any:
        if self.tape is None:
            raise RuntimeError("execution tape is unavailable")
        replay_step = self.tape.step(int(step_index))
        if replay_step.noise:
            if self.noise_consumer is None:
                raise RuntimeError(
                    "replay step contains noise samples but no noise consumer is configured"
                )
            self.noise_consumer(replay_step)
        if self._test_mode:
            self._test_command_apply(replay_step.commands)
            actions = (
                replay_step.applied_actions
                if replay_step.applied_actions_recorded
                else replay_step.actions
            )
            return self._test_env_step(actions)

        apply_commands(
            self.session.base_env,
            replay_step.commands,
            device=self.session.device,
        )
        if replay_step.applied_actions_recorded:
            self.session.env._replay_precomputed_actions = actions_to_backend(
                replay_step.applied_actions,
                device=self.session.device,
            )
            target_env = getattr(self.session.env, "unwrapped", self.session.env)
            target_env._replay_precomputed_actions = self.session.env._replay_precomputed_actions
            result = self.session.env.step({})
            if bool(getattr(target_env, "_replay_precomputed_used", False)):
                self.precomputed_action_steps.append(int(step_index))
            return result
        templates = dict(getattr(self.session.base_env, "actions_dict", {}) or {})
        actions = actions_to_backend(
            replay_step.actions,
            device=self.session.device,
            templates=templates,
        )
        return self.session.env.step(actions)

    def compare_state(self, expected_step: int) -> dict[str, dict[str, float]]:
        if self._test_mode or self.reader is None:
            raise RuntimeError("recorded state is unavailable")
        expected = self.reader.read_state(int(expected_step))
        actual = self.recorder._read_state()
        comparison: dict[str, dict[str, float]] = {}
        for key, expected_value in expected.items():
            if key not in actual:
                comparison[key] = {"max_abs": float("inf"), "rmse": float("inf")}
                continue
            expected_array = np.asarray(expected_value, dtype=np.float64)
            actual_array = np.asarray(actual[key], dtype=np.float64)
            if expected_array.shape != actual_array.shape:
                comparison[key] = {"max_abs": float("inf"), "rmse": float("inf")}
                continue
            difference = expected_array - actual_array
            comparison[key] = {
                "max_abs": float(np.max(np.abs(difference))) if difference.size else 0.0,
                "rmse": float(np.sqrt(np.mean(np.square(difference)))) if difference.size else 0.0,
            }
        return comparison
