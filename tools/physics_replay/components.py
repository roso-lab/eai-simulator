"""Versioned checkpoint components for non-physical runtime state."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import random
from typing import Any, Callable, Iterable, Mapping, Protocol, runtime_checkable

import numpy as np

from .tape import actions_to_backend, apply_commands, capture_commands


@dataclass
class ComponentSnapshot:
    name: str
    version: int
    state: Mapping[str, Any]

    def __post_init__(self) -> None:
        self.name = str(self.name)
        self.version = int(self.version)
        self.state = deepcopy(dict(self.state))


@runtime_checkable
class CheckpointableComponent(Protocol):
    name: str
    version: int
    priority: int

    def capture_state(self) -> Mapping[str, Any]: ...

    def validate_state(self, state: Mapping[str, Any]) -> None: ...

    def restore_state(self, state: Mapping[str, Any]) -> None: ...


class CallbackComponent:
    """Adapt capture/restore callbacks to the component protocol."""

    def __init__(
        self,
        name: str,
        version: int,
        capture: Callable[[], Mapping[str, Any]],
        restore: Callable[[Mapping[str, Any]], None],
        *,
        validator: Callable[[Mapping[str, Any]], None] | None = None,
        priority: int = 100,
    ):
        if not str(name).strip():
            raise ValueError("component name must not be empty")
        self.name = str(name)
        self.version = int(version)
        self.priority = int(priority)
        self._capture = capture
        self._restore = restore
        self._validator = validator

    def capture_state(self) -> Mapping[str, Any]:
        return deepcopy(dict(self._capture()))

    def validate_state(self, state: Mapping[str, Any]) -> None:
        if not isinstance(state, Mapping):
            raise ValueError(f"component {self.name!r} state must be a mapping")
        if self._validator is not None:
            self._validator(state)

    def restore_state(self, state: Mapping[str, Any]) -> None:
        self._restore(deepcopy(dict(state)))


class ComponentRegistry:
    """Capture and restore a fixed set of uniquely named components."""

    def __init__(self, components: Iterable[CheckpointableComponent] = ()):
        self._components: dict[str, CheckpointableComponent] = {}
        for component in components:
            self.register(component)

    @property
    def components(self) -> tuple[CheckpointableComponent, ...]:
        return tuple(self._components.values())

    def register(self, component: CheckpointableComponent) -> None:
        name = str(component.name)
        if name in self._components:
            raise ValueError(f"duplicate checkpoint component name: {name}")
        self._components[name] = component

    def capture_all(self) -> dict[str, ComponentSnapshot]:
        return {
            name: ComponentSnapshot(name, component.version, component.capture_state())
            for name, component in self._components.items()
        }

    def validate_all(self, snapshots: Mapping[str, ComponentSnapshot]) -> None:
        registered = set(self._components)
        provided = set(snapshots)
        missing = sorted(registered - provided)
        extra = sorted(provided - registered)
        if missing:
            raise ValueError(f"missing component snapshots: {', '.join(missing)}")
        if extra:
            raise ValueError(f"unknown component snapshots: {', '.join(extra)}")
        for name, component in self._components.items():
            snapshot = snapshots[name]
            if snapshot.name != name:
                raise ValueError(f"component snapshot name mismatch for {name}")
            if int(snapshot.version) != int(component.version):
                raise ValueError(
                    f"component {name!r} version mismatch: "
                    f"snapshot={snapshot.version}, runtime={component.version}"
                )
            component.validate_state(snapshot.state)

    def restore_all(self, snapshots: Mapping[str, ComponentSnapshot]) -> None:
        self.validate_all(snapshots)
        ordered = sorted(self._components.values(), key=lambda item: (item.priority, item.name))
        for component in ordered:
            component.restore_state(snapshots[component.name].state)


def _array_copy(value: Any) -> np.ndarray:
    detached = value.detach() if hasattr(value, "detach") else value
    moved = detached.cpu() if hasattr(detached, "cpu") else detached
    return np.asarray(moved).copy()


class EnvironmentCommandComponent:
    """Checkpoint EAI command buffers and previous controller actions."""

    name = "eai.commands"
    version = 1
    priority = 20

    def __init__(self, base_env: Any, *, device: str | None):
        self.base_env = base_env
        self.device = device

    def capture_state(self) -> Mapping[str, Any]:
        actions = {
            str(name): _array_copy(value)
            for name, value in dict(getattr(self.base_env, "actions_dict", {}) or {}).items()
        }
        return {"commands": capture_commands(self.base_env), "actions": actions}

    def validate_state(self, state: Mapping[str, Any]) -> None:
        if not isinstance(state, Mapping):
            raise ValueError("environment command state must be a mapping")
        if not isinstance(state.get("commands", {}), Mapping):
            raise ValueError("environment command snapshot commands must be a mapping")
        if not isinstance(state.get("actions", {}), Mapping):
            raise ValueError("environment command snapshot actions must be a mapping")

    def restore_state(self, state: Mapping[str, Any]) -> None:
        self.validate_state(state)
        apply_commands(self.base_env, state.get("commands", {}), device=self.device)
        templates = dict(getattr(self.base_env, "actions_dict", {}) or {})
        self.base_env.actions_dict = actions_to_backend(
            state.get("actions", {}), device=self.device, templates=templates
        )


class RngStateComponent:
    """Checkpoint Python, NumPy, and optional Torch RNG state."""

    name = "runtime.rng"
    version = 2
    priority = 90

    def __init__(self, *, include_torch: bool = True, streams: Mapping[str, Any] | None = None):
        self.include_torch = bool(include_torch)
        self.streams = streams if streams is not None else {}

    def _stream_state(self, stream: Any) -> dict[str, Any]:
        if isinstance(stream, np.random.Generator):
            return {"kind": "numpy", "algorithm": type(stream.bit_generator).__name__,
                    "state": deepcopy(stream.bit_generator.state)}
        if isinstance(stream, random.Random):
            return {"kind": "python", "state": stream.getstate()}
        try:
            import torch
        except ModuleNotFoundError:
            torch = None
        if torch is not None and isinstance(stream, torch.Generator):
            return {"kind": "torch", "device": str(stream.device), "state": _array_copy(stream.get_state())}
        raise ValueError(f"unsupported named random stream: {type(stream).__name__}")

    def _set_stream_state(self, stream: Any, snapshot: Mapping[str, Any]) -> None:
        if snapshot["kind"] == "numpy":
            stream.bit_generator.state = deepcopy(snapshot["state"])
        elif snapshot["kind"] == "python":
            stream.setstate(tuple(snapshot["state"]))
        else:
            import torch

            stream.set_state(torch.as_tensor(snapshot["state"], dtype=torch.uint8, device="cpu"))

    def capture_state(self) -> Mapping[str, Any]:
        state: dict[str, Any] = {
            "python": random.getstate(),
            "numpy": np.random.get_state(),
            "torch": None,
            "torch_cuda": None,
            "streams": {name: {**self._stream_state(stream), "identity": id(stream)}
                        for name, stream in self.streams.items()},
        }
        if not self.include_torch:
            return state
        try:
            import torch
        except ModuleNotFoundError:
            return state
        state["torch"] = _array_copy(torch.get_rng_state())
        if torch.cuda.is_available():
            state["torch_cuda"] = [
                _array_copy(value) for value in torch.cuda.get_rng_state_all()
            ]
        return state

    def validate_state(self, state: Mapping[str, Any]) -> None:
        required = {"python", "numpy", "torch", "torch_cuda", "streams"}
        missing = sorted(required - set(state))
        if missing:
            raise ValueError(f"RNG snapshot is missing: {', '.join(missing)}")
        random.Random().setstate(tuple(state["python"]))
        np.random.RandomState().set_state(tuple(state["numpy"]))
        if set(state["streams"]) != set(self.streams):
            raise ValueError("named random stream set changed since checkpoint")
        for name, stream in self.streams.items():
            snapshot = state["streams"][name]
            current = self._stream_state(stream)
            if snapshot["identity"] != id(stream) or any(
                snapshot.get(key) != value for key, value in current.items() if key != "state"
            ):
                raise ValueError(f"named random stream changed: {name}")
            self._set_stream_state(deepcopy(stream), snapshot)
        if state["torch"] is not None:
            import torch

            torch.Generator(device="cpu").set_state(torch.as_tensor(state["torch"], dtype=torch.uint8))
            if state["torch_cuda"] is not None:
                if not torch.cuda.is_available() or len(state["torch_cuda"]) != torch.cuda.device_count():
                    raise ValueError("CUDA RNG device set changed since checkpoint")
                for index, value in enumerate(state["torch_cuda"]):
                    torch.Generator(device=f"cuda:{index}").set_state(torch.as_tensor(value, dtype=torch.uint8))

    def restore_state(self, state: Mapping[str, Any]) -> None:
        self.validate_state(state)
        random.setstate(tuple(state["python"]))
        np.random.set_state(tuple(state["numpy"]))
        for name, stream in self.streams.items():
            self._set_stream_state(stream, state["streams"][name])
        if state.get("torch") is None:
            return
        try:
            import torch
        except ModuleNotFoundError as exc:
            raise ValueError("RNG snapshot contains Torch state but Torch is unavailable") from exc
        torch.set_rng_state(torch.as_tensor(state["torch"], dtype=torch.uint8, device="cpu"))
        cuda_values = state.get("torch_cuda")
        if cuda_values is not None:
            if not torch.cuda.is_available():
                raise ValueError("RNG snapshot contains CUDA state but CUDA is unavailable")
            torch.cuda.set_rng_state_all(
                [torch.as_tensor(value, dtype=torch.uint8, device="cpu") for value in cuda_values]
            )
