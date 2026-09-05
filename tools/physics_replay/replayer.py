"""Checkpoint validation and in-process state restoration."""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

import numpy as np

from .adapters import IsaacLabStateAdapter
from .store import EpisodeReader
from .targets import TargetResolver
from .types import RestoreReport, TargetSpec, stable_hash


_WRITABLE_CHANNELS = frozenset({"pose", "velocity", "joint_state"})


def _error(expected: Any, actual: Any, tolerance: float) -> dict[str, float | bool]:
    expected_array = np.asarray(expected, dtype=np.float64)
    actual_array = np.asarray(actual, dtype=np.float64)
    if expected_array.shape != actual_array.shape:
        return {
            "max_abs": float("inf"),
            "rmse": float("inf"),
            "tolerance": float(tolerance),
            "within_tolerance": False,
        }
    difference = np.abs(expected_array - actual_array)
    max_abs = float(difference.max()) if difference.size else 0.0
    rmse = float(np.sqrt(np.mean(np.square(difference)))) if difference.size else 0.0
    return {
        "max_abs": max_abs,
        "rmse": rmse,
        "tolerance": float(tolerance),
        "within_tolerance": bool(max_abs <= tolerance),
    }


class PhysicsReplayer:
    """Restore checkpoints using a session-specific state adapter."""

    def __init__(
        self,
        adapter: Any,
        *,
        session: Any | None,
        targets: Sequence[Any],
        tolerances: Mapping[str, float] | None = None,
    ):
        self.adapter = adapter
        self.session = session
        self.targets = tuple(targets)
        self.tolerances = {
            "pose": 1e-4,
            "velocity": 1e-4,
            "joint_state": 1e-4,
            "joint_effort": 1e-4,
            **dict(tolerances or {}),
        }

    @classmethod
    def from_session(
        cls,
        session: Any,
        targets: Iterable[TargetSpec],
        *,
        tolerances: Mapping[str, float] | None = None,
    ) -> "PhysicsReplayer":
        resolved = TargetResolver(session.base_env).resolve_all(targets)
        return cls(
            IsaacLabStateAdapter(session.base_env),
            session=session,
            targets=resolved,
            tolerances=tolerances,
        )

    def restore(self, episode_dir: str, step_index: int) -> RestoreReport:
        return self.restore_from_reader(EpisodeReader(episode_dir), step_index)

    def _validate_reader(self, reader: EpisodeReader) -> None:
        expected_ids = tuple(str(item.target_id) for item in self.targets)
        manifest_ids = tuple(str(item.get("target_id", "")) for item in reader.manifest.targets)
        if manifest_ids and manifest_ids != expected_ids:
            raise ValueError(
                f"replay target mismatch: artifact={manifest_ids!r}, session={expected_ids!r}"
            )
        if self.session is None:
            return
        environment = reader.manifest.environment
        session_name = str(getattr(self.session, "env_name", ""))
        artifact_name = str(environment.get("env_name", ""))
        if artifact_name and session_name and artifact_name != session_name:
            raise ValueError(
                f"replay environment mismatch: artifact={artifact_name!r}, session={session_name!r}"
            )
        artifact_selection = environment.get("selection_sha256")
        current_selection = getattr(self.session, "selection_data", None)
        if artifact_selection and current_selection is not None:
            if str(artifact_selection) != stable_hash(current_selection):
                raise ValueError("replay selection configuration mismatch")
        expected_targets = {str(item.get("target_id")): item for item in reader.manifest.targets}
        for target in self.targets:
            artifact = expected_targets.get(str(target.target_id))
            if artifact is None:
                continue
            prim_path = getattr(getattr(target, "spec", None), "prim_path", None)
            if prim_path and artifact.get("prim_path") and prim_path != artifact["prim_path"]:
                raise ValueError(
                    f"replay target {target.target_id!r} Prim mismatch: "
                    f"artifact={artifact['prim_path']!r}, session={prim_path!r}"
                )

    def _restore_environment_buffers(self, step_index: int) -> None:
        if self.session is None:
            return
        base_env = getattr(self.session, "base_env", None)
        if base_env is None:
            return
        if hasattr(base_env, "actions_dict"):
            base_env.actions_dict = {}
        counter = getattr(base_env, "episode_length_buf", None)
        if counter is not None and len(self.targets):
            env_id = int(getattr(getattr(self.targets[0], "spec", None), "env_id", 0))
            try:
                counter[env_id] = step_index
            except (IndexError, TypeError, RuntimeError, ValueError):
                pass

    def restore_from_reader(self, reader: EpisodeReader, step_index: int) -> RestoreReport:
        self._validate_reader(reader)
        frame = reader.read_frame(step_index)
        channel_errors: dict[str, dict[str, float | bool]] = {}
        unrestored: list[str] = []
        unsupported: list[str] = []
        diagnostics: list[str] = []
        writable_targets = sorted(self.targets, key=lambda target: int(getattr(target, "depth", 0)))
        for target in writable_targets:
            target_id = str(target.target_id)
            writable_channels = tuple(getattr(target, "writable_channels", ()))
            values: dict[str, np.ndarray] = {}
            for channel in writable_channels:
                key = f"{target_id}/{channel}"
                if key in frame.state_before:
                    values[key] = frame.state_before[key]
                else:
                    unrestored.append(key)
            requested = tuple(getattr(getattr(target, "spec", None), "channels", writable_channels))
            missing_writable = set(requested) & _WRITABLE_CHANNELS - set(writable_channels)
            if missing_writable:
                unsupported.extend(
                    f"{target_id}/{channel}" for channel in sorted(missing_writable)
                )
            unrestored.extend(
                f"{target_id}/{channel}"
                for channel in requested
                if channel not in writable_channels
            )
            if not values:
                continue
            self.adapter.write(target, values)
            actual = self.adapter.read(target, tuple(values))
            for key, expected in values.items():
                channel = key.rsplit("/", 1)[-1]
                if key not in actual:
                    diagnostics.append(f"adapter did not return {key}")
                    channel_errors[key] = _error(expected, np.asarray([]), self.tolerances.get(channel, 1e-4))
                    continue
                channel_errors[key] = _error(
                    expected,
                    actual[key],
                    self.tolerances.get(channel, 1e-4),
                )
        self._restore_environment_buffers(int(step_index))
        failed = [key for key, result in channel_errors.items() if not result["within_tolerance"]]
        if failed:
            diagnostics.append("read-back state exceeded configured tolerance")
            status = "replay_diverged"
        elif unsupported:
            diagnostics.append("requested target channels have no writable state API")
            status = "unsupported"
        elif not self.session or reader.manifest.replay_fidelity != "causal_replay":
            status = "physical_state_only"
            diagnostics.append("controller, ROS, and noise state were not restored")
        else:
            status = "restored"
        return RestoreReport(
            status=status,
            step_index=int(step_index),
            channel_errors=channel_errors,
            unrestored_channels=tuple(sorted(set(unrestored + unsupported))),
            diagnostics=tuple(diagnostics),
        )

    def validate_session(self, reader: EpisodeReader) -> dict[str, Any]:
        self._validate_reader(reader)
        return {
            "valid": True,
            "environment": reader.manifest.environment,
            "target_count": len(self.targets),
            "replay_fidelity": reader.manifest.replay_fidelity,
        }
