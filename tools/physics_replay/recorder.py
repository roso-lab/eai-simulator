"""Step-aligned recording for an existing EAI simulator session."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

from .adapters import IsaacLabStateAdapter
from .store import EpisodeReader, EpisodeWriter
from .targets import TargetResolver
from .types import FrameRecord, Manifest, TargetSpec, stable_hash


def _array_copy(value: Any) -> np.ndarray:
    detached = value.detach() if hasattr(value, "detach") else value
    moved = detached.cpu() if hasattr(detached, "cpu") else detached
    array = np.asarray(moved)
    if array.dtype == object:
        raise ValueError("actions and states cannot have object dtype")
    return array.copy()


def _copy_actions(action: Mapping[str, Any] | None) -> dict[str, np.ndarray]:
    if action is None:
        return {}
    if not isinstance(action, Mapping):
        raise TypeError("action must be a mapping from agent name to array")
    return {str(name): _array_copy(value) for name, value in action.items()}


def _target_dict(target: Any) -> dict[str, Any]:
    if hasattr(target, "to_dict"):
        return target.to_dict()
    spec = getattr(target, "spec", None)
    return {
        "target_id": str(getattr(target, "target_id", "")),
        "prim_path": str(getattr(spec, "prim_path", "")),
        "channels": list(getattr(spec, "channels", ())),
        "env_id": int(getattr(spec, "env_id", 0)),
        "kind": str(getattr(target, "kind", "unknown")),
        "collection_name": str(getattr(target, "collection_name", "")),
        "root_path": str(getattr(target, "root_path", "")),
        "body_index": getattr(target, "body_index", None),
        "writable_channels": list(getattr(target, "writable_channels", ())),
    }


@dataclass
class _PendingFrame:
    step_index: int
    sim_time: float
    captured: bool
    state_before: dict[str, np.ndarray]
    actions: dict[str, np.ndarray]


class ReplayRecorder:
    """Capture state around ``env.step`` without owning the simulator loop."""

    def __init__(
        self,
        *,
        adapter: Any,
        targets: Sequence[Any],
        output_dir: str | Path,
        sample_every: int = 1,
        segment_frames: int = 256,
        session: Any | None = None,
    ):
        if int(sample_every) <= 0:
            raise ValueError("sample_every must be positive")
        self.adapter = adapter
        self.targets = tuple(targets)
        self.session = session
        self.sample_every = int(sample_every)
        self.step_index = 0
        self._pending: _PendingFrame | None = None
        self._closed = False
        self._reader: EpisodeReader | None = None
        base_env = getattr(session, "base_env", None)
        step_dt = float(getattr(base_env, "step_dt", 0.0))
        physics_dt = float(getattr(base_env, "physics_dt", step_dt))
        environment = {
            "env_name": str(getattr(session, "env_name", "unknown")),
            "num_envs": int(getattr(session, "num_envs", 1)),
            "env_id": 0,
            "physics_dt": physics_dt,
            "step_dt": step_dt,
            "device": str(getattr(session, "device", "unknown")),
        }
        if session is not None:
            selection_data = getattr(session, "selection_data", None)
            if selection_data is not None:
                environment["selection_sha256"] = stable_hash(selection_data)
            seed = getattr(getattr(session, "env_cfg", None), "seed", None)
            if seed is not None:
                try:
                    environment["seed"] = int(seed)
                except (TypeError, ValueError):
                    environment["seed"] = str(seed)
        manifest = Manifest(
            environment=environment,
            targets=[_target_dict(target) for target in self.targets],
            sample_every=self.sample_every,
            segment_frames=int(segment_frames),
            replay_fidelity="physical_state_only",
        )
        self.writer = EpisodeWriter(output_dir, manifest)

    @classmethod
    def from_session(
        cls,
        session: Any,
        targets: Iterable[TargetSpec],
        output_dir: str | Path,
        *,
        sample_every: int = 1,
        segment_frames: int = 256,
    ) -> "ReplayRecorder":
        resolved = TargetResolver(session.base_env).resolve_all(targets)
        return cls(
            adapter=IsaacLabStateAdapter(session.base_env),
            targets=resolved,
            output_dir=output_dir,
            sample_every=sample_every,
            segment_frames=segment_frames,
            session=session,
        )

    def _read_state(self) -> dict[str, np.ndarray]:
        state: dict[str, np.ndarray] = {}
        for target in self.targets:
            channels = tuple(
                getattr(getattr(target, "spec", None), "channels", ())
            )
            if not channels:
                continue
            values = self.adapter.read(
                target,
                tuple(f"{target.target_id}/{channel}" for channel in channels),
            )
            state.update({str(key): _array_copy(value) for key, value in values.items()})
        return state

    def before_step(self, action: Mapping[str, Any] | None) -> None:
        if self._closed:
            raise RuntimeError("recorder is closed")
        if self._pending is not None:
            raise RuntimeError("a step is already pending; call after_step first")
        if self.step_index % self.sample_every != 0:
            self._pending = _PendingFrame(
                self.step_index,
                self._sim_time(),
                False,
                {},
                {},
            )
            return
        self._pending = _PendingFrame(
            self.step_index,
            self._sim_time(),
            True,
            self._read_state(),
            _copy_actions(action),
        )

    def after_step(
        self,
        action: Mapping[str, Any] | None = None,
        *,
        info: Mapping[str, Any] | None = None,
        phase: str | None = None,
        events: Iterable[Mapping[str, Any]] = (),
    ) -> None:
        if self._pending is None:
            raise RuntimeError("after_step called without before_step")
        pending = self._pending
        if pending.captured:
            state_after = self._read_state()
            event_values = list(events)
            if info:
                event_values.append({"info": dict(info)})
            self.writer.append(
                FrameRecord(
                    pending.step_index,
                    pending.sim_time,
                    pending.state_before,
                    state_after,
                    pending.actions if action is None else _copy_actions(action),
                    phase=phase,
                    events=tuple(event_values),
                )
            )
        self._pending = None
        self.step_index += 1

    def _sim_time(self) -> float:
        step_dt = float(getattr(getattr(self.session, "base_env", None), "step_dt", 0.0))
        return self.step_index * step_dt

    @property
    def reader(self) -> EpisodeReader:
        if self._reader is None:
            raise RuntimeError("reader is available after recorder.close()")
        return self._reader

    def close(self) -> None:
        if self._closed:
            return
        if self._pending is not None:
            raise RuntimeError("cannot close recorder with a pending step")
        self.writer.close()
        self._reader = EpisodeReader(self.writer.root)
        self._closed = True

    def __enter__(self) -> "ReplayRecorder":
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        self.close()
