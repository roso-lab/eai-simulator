"""Deterministic action, command, and external-noise tape helpers."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Any, Callable, Mapping, Sequence

import numpy as np

from .store import EpisodeReader


def _to_numpy(value: Any) -> np.ndarray:
    detached = value.detach() if hasattr(value, "detach") else value
    moved = detached.cpu() if hasattr(detached, "cpu") else detached
    array = np.asarray(moved)
    if array.dtype == object:
        raise ValueError("tape values cannot have object dtype")
    return array.copy()


def _to_backend(value: Any, *, reference: Any = None, device: str | None = None) -> Any:
    array = np.asarray(value)
    reference_device = getattr(reference, "device", None)
    target_device = reference_device or device
    if target_device is None:
        return array.copy()
    try:
        import torch
    except ModuleNotFoundError:
        return array.copy()
    kwargs: dict[str, Any] = {"device": target_device}
    if reference is not None and getattr(reference, "dtype", None) is not None:
        kwargs["dtype"] = reference.dtype
    return torch.as_tensor(array, **kwargs)


@dataclass(frozen=True)
class ReplayStep:
    step_index: int
    actions: dict[str, np.ndarray]
    commands: dict[str, np.ndarray]
    noise: dict[str, np.ndarray]
    events: tuple[dict[str, Any], ...]
    applied_actions: dict[str, np.ndarray] | None = None
    applied_actions_recorded: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "step_index", int(self.step_index))
        for attribute in ("actions", "commands", "noise"):
            values = getattr(self, attribute)
            object.__setattr__(
                self,
                attribute,
                {str(key): _to_numpy(value) for key, value in values.items()},
            )
        object.__setattr__(
            self,
            "applied_actions",
            {
                str(key): _to_numpy(value)
                for key, value in (self.applied_actions or {}).items()
            },
        )
        object.__setattr__(self, "events", tuple(dict(event) for event in self.events))


class ExecutionTape:
    """Read exact execution inputs from a closed replay episode."""

    def __init__(self, reader: EpisodeReader):
        self.reader = reader

    def step(self, step_index: int) -> ReplayStep:
        step_index = int(step_index)
        last_step = self.reader.manifest.metadata.get("last_step")
        if last_step is None or step_index > int(last_step):
            raise ValueError(f"execution tape has no recorded step {step_index}")
        try:
            frame = self.reader.read_frame(step_index)
        except ValueError as exc:
            raise ValueError(f"execution tape has no recorded step {step_index}") from exc
        return ReplayStep(
            step_index=frame.step_index,
            actions=frame.actions,
            commands=frame.commands,
            noise=frame.noise,
            events=tuple(frame.events),
            applied_actions=frame.applied_actions,
            applied_actions_recorded=bool(
                self.reader.manifest.metadata.get(
                    "has_applied_action_tape",
                    bool(self.reader.manifest.metadata.get("applied_action_keys")),
                )
            ),
        )

    def require_range(self, start_step: int, count: int) -> tuple[ReplayStep, ...]:
        start_step = int(start_step)
        count = int(count)
        if start_step < 0 or count < 0:
            raise ValueError("start_step and count must be non-negative")
        return tuple(self.step(step) for step in range(start_step, start_step + count))


class NoiseTape:
    """Record or replay explicit time-indexed exogenous samples."""

    def __init__(
        self,
        *,
        mode: str,
        seed: int = 0,
        values: Mapping[int, Mapping[str, Any]] | None = None,
    ):
        if mode not in {"record", "replay"}:
            raise ValueError("noise tape mode must be 'record' or 'replay'")
        self.mode = mode
        self.seed = int(seed)
        self._values = {
            int(step): {str(key): _to_numpy(value) for key, value in step_values.items()}
            for step, step_values in (values or {}).items()
        }
        self._counters: dict[tuple[int, str], int] = {}
        self._consumed: dict[int, set[str]] = {}

    @classmethod
    def recording(cls, seed: int = 0) -> "NoiseTape":
        return cls(mode="record", seed=seed)

    @classmethod
    def replaying(
        cls, values: Mapping[int, Mapping[str, Any]], *, seed: int = 0
    ) -> "NoiseTape":
        return cls(mode="replay", seed=seed, values=values)

    def sample(
        self,
        channel: str,
        step_index: int,
        sampler: Callable[[np.random.Generator], Any],
        *,
        event_key: Any | None = None,
    ) -> np.ndarray:
        channel = str(channel).strip()
        if not channel:
            raise ValueError("noise channel must not be empty")
        step_index = int(step_index)
        if event_key is None:
            # Legacy recordings remain readable. New environment and
            # communication sources should always provide an event key.
            counter_key = (step_index, channel)
            sample_index = self._counters.get(counter_key, 0)
            self._counters[counter_key] = sample_index + 1
            key = f"{channel}#{sample_index}"
            material = f"{self.seed}|legacy|{channel}|{step_index}|{sample_index}"
        else:
            try:
                canonical_event = json.dumps(
                    event_key, sort_keys=True, separators=(",", ":"),
                    ensure_ascii=False, allow_nan=False,
                )
            except (TypeError, ValueError) as exc:
                raise ValueError("event_key must be finite JSON data") from exc
            key = f"{channel}@{canonical_event}"
            material = f"{self.seed}|physics_replay_noise|{channel}|{step_index}|{canonical_event}"
        if self.mode == "replay":
            try:
                value = self._values[step_index][key]
            except KeyError as exc:
                raise KeyError(
                    f"missing noise sample {key!r} at step {step_index}"
                ) from exc
            self._consumed.setdefault(step_index, set()).add(key)
            return value.copy()

        derived_seed = int.from_bytes(sha256(material.encode("utf-8")).digest()[:16], "big")
        value = _to_numpy(sampler(np.random.default_rng(derived_seed)))
        self._values.setdefault(step_index, {})[key] = value
        return value.copy()

    def values_for_step(self, step_index: int) -> dict[str, np.ndarray]:
        return {
            key: value.copy()
            for key, value in self._values.get(int(step_index), {}).items()
        }

    def assert_step_consumed(self, step_index: int) -> None:
        if self.mode != "replay":
            return
        step_index = int(step_index)
        expected = set(self._values.get(step_index, {}))
        consumed = self._consumed.get(step_index, set())
        missing = sorted(expected - consumed)
        if missing:
            raise ValueError(
                f"unconsumed noise samples at step {step_index}: {', '.join(missing)}"
            )


def flatten_commands(commands: Mapping[str, Mapping[str, Any]]) -> dict[str, np.ndarray]:
    flattened: dict[str, np.ndarray] = {}
    for robot_name, robot_commands in commands.items():
        if not isinstance(robot_commands, Mapping):
            raise TypeError(f"commands for {robot_name!r} must be a mapping")
        for command_name, value in robot_commands.items():
            flattened[f"{robot_name}/{command_name}"] = _to_numpy(value)
    return flattened


def unflatten_commands(commands: Mapping[str, Any]) -> dict[str, dict[str, np.ndarray]]:
    nested: dict[str, dict[str, np.ndarray]] = {}
    for key, value in commands.items():
        if "/" not in key:
            raise ValueError(f"command key must use robot/name form: {key!r}")
        robot_name, command_name = key.split("/", 1)
        nested.setdefault(robot_name, {})[command_name] = _to_numpy(value)
    return nested


def capture_commands(base_env: Any) -> dict[str, np.ndarray]:
    return flatten_commands(dict(getattr(base_env, "_commands_dict", {}) or {}))


def apply_commands(
    base_env: Any,
    commands: Mapping[str, Any],
    *,
    device: str | None = None,
) -> None:
    setter = getattr(base_env, "set_command", None)
    if not callable(setter):
        raise ValueError("base environment does not expose set_command")
    getter = getattr(base_env, "get_command", None)
    for key in sorted(commands):
        if "/" not in key:
            raise ValueError(f"command key must use robot/name form: {key!r}")
        robot_name, command_name = key.split("/", 1)
        reference = getter(robot_name, command_name) if callable(getter) else None
        setter(
            robot_name,
            command_name,
            _to_backend(commands[key], reference=reference, device=device),
        )


def actions_to_backend(
    actions: Mapping[str, Any],
    *,
    device: str | None = None,
    templates: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    templates = templates or {}
    return {
        str(name): _to_backend(value, reference=templates.get(name), device=device)
        for name, value in actions.items()
    }
