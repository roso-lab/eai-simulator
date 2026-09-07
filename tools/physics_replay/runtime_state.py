"""Explicit, non-pickle snapshots of mutable Python and actuator state."""

from __future__ import annotations

from collections import deque
from dataclasses import is_dataclass
from enum import Enum
from types import SimpleNamespace
from typing import Any, Iterable, Mapping

import numpy as np


class StateCodec:
    """Keep tensor devices, collection types and tuple dictionary keys intact."""

    def __init__(self, value_types: Iterable[type] = ()):
        self.types = {f"{kind.__module__}.{kind.__qualname__}": kind for kind in value_types}

    def encode(self, value: Any) -> Any:
        if isinstance(value, Enum):
            name = f"{type(value).__module__}.{type(value).__qualname__}"
            self.types[name] = type(value)
            return {"kind": "enum", "type": name, "name": value.name}
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, np.generic):
            return value.item()
        if isinstance(value, np.ndarray):
            return {"kind": "numpy", "value": value.copy()}
        if hasattr(value, "detach") and hasattr(value, "device"):
            return {"kind": "tensor", "device": str(value.device), "value": value.detach().cpu().numpy().copy()}
        if isinstance(value, Mapping):
            return {"kind": "dict", "items": [(self.encode(key), self.encode(item)) for key, item in value.items()]}
        if isinstance(value, (list, tuple, set, frozenset, deque)):
            return {
                "kind": type(value).__name__,
                "items": [self.encode(item) for item in value],
                "maxlen": value.maxlen if isinstance(value, deque) else None,
            }
        if isinstance(value, slice):
            return {"kind": "slice", "items": [value.start, value.stop, value.step]}
        if isinstance(value, range):
            return {"kind": "range", "items": [value.start, value.stop, value.step]}
        name = f"{type(value).__module__}.{type(value).__qualname__}"
        if name == "torch.device":
            return {"kind": "device", "value": str(value)}
        if is_dataclass(value) or isinstance(value, SimpleNamespace) or name in self.types:
            self.types[name] = type(value)
            return {"kind": "object", "type": name, "attributes": self.encode(vars(value))}
        raise ValueError(f"no checkpoint adapter for {name}")

    def decode(self, value: Any) -> Any:
        if not isinstance(value, dict) or "kind" not in value:
            return value
        kind = value["kind"]
        if kind == "numpy":
            return np.asarray(value["value"]).copy()
        if kind in {"tensor", "device"}:
            import torch

            if kind == "device":
                return torch.device(value["value"])
            return torch.as_tensor(np.asarray(value["value"]).copy(), device=value["device"])
        if kind == "dict":
            return {self.decode(key): self.decode(item) for key, item in value["items"]}
        if kind == "enum":
            return self.types[value["type"]][value["name"]]
        if kind == "object":
            restored = object.__new__(self.types[value["type"]])
            for name, item in self.decode(value["attributes"]).items():
                object.__setattr__(restored, name, item)
            return restored
        items = [self.decode(item) for item in value["items"]]
        if kind == "deque":
            return deque(items, maxlen=value["maxlen"])
        factories = {"list": list, "tuple": tuple, "set": set, "frozenset": frozenset}
        if kind in factories:
            return factories[kind](items)
        if kind == "slice":
            return slice(*items)
        if kind == "range":
            return range(*items)
        raise ValueError(f"unsupported checkpoint value kind: {kind}")


class ObjectStateComponent:
    """Snapshot a declared object's fields; unsupported values fail closed."""

    version = 1

    def __init__(self, name: str, instance: Any, *, exclude: Iterable[str] = (), priority: int = 40, value_types: Iterable[type] = ()):
        self.name = name
        self.instance = instance
        self.exclude = set(exclude)
        self.priority = priority
        self.codec = StateCodec(value_types)

    def capture_state(self) -> Mapping[str, Any]:
        values = {name: value for name, value in vars(self.instance).items() if name not in self.exclude}
        for name, value in values.items():
            try:
                self.codec.encode(value)
            except ValueError as exc:
                raise ValueError(f"{self.name}.{name}: {exc}") from exc
        return {"attributes": self.codec.encode(values)}

    def validate_state(self, state: Mapping[str, Any]) -> None:
        restored = self.codec.decode(state["attributes"])
        if not isinstance(restored, dict) or set(restored) & self.exclude:
            raise ValueError(f"invalid object snapshot: {self.name}")

    def restore_state(self, state: Mapping[str, Any]) -> None:
        restored = self.codec.decode(state["attributes"])
        for name in set(vars(self.instance)) - self.exclude - set(restored):
            delattr(self.instance, name)
        for name, value in restored.items():
            setattr(self.instance, name, value)


class EnvironmentBuffersComponent:
    name = "eai.runtime_buffers"
    version = 2
    priority = 25
    fields = (
        "episode_length_buf", "common_step_counter", "_sim_step_counter", "reset_buf",
        "reset_terminated", "reset_time_outs", "reward_buf", "_last_computed_actions",
        "_replay_precomputed_actions", "_replay_precomputed_used",
        "obs_dict", "reward_dict", "terminated_dict", "time_out_dict", "agents", "extras",
    )

    def __init__(self, base_env: Any):
        self.base_env = base_env
        self.codec = StateCodec()

    def _resources(self) -> dict[str, Any]:
        from .resources import ReplayResource

        resources = {}
        for name, values in getattr(self.base_env, "_controllers_dict", {}).items():
            if values.get("policy") is not None:
                resources[f"policy/{name}"] = values["policy"]
        for field in ("_action_noise_model", "_observation_noise_model"):
            models = getattr(self.base_env, field, None)
            if models is not None:
                for name, model in (models.items() if isinstance(models, Mapping) else (("environment", models),)):
                    if model is not None:
                        resources[f"{field}/{name}"] = model
        for name, resource in getattr(self.base_env, "replay_state_resources", {}).items():
            resources[f"resource/{name}"] = resource
        return {name: ReplayResource(name, instance) for name, instance in resources.items()}

    def capture_state(self) -> Mapping[str, Any]:
        buffers = {name: getattr(self.base_env, name) for name in self.fields if hasattr(self.base_env, name)}
        controllers = {}
        for name, values in getattr(self.base_env, "_controllers_dict", {}).items():
            if any(key != "name" and key != "policy" and callable(value) for key, value in values.items()):
                raise ValueError(f"controller {name} needs an explicit checkpoint adapter")
            controllers[name] = {key: value for key, value in values.items() if key != "policy"}
        return {
            "buffers": self.codec.encode(buffers), "controllers": self.codec.encode(controllers),
            "resources": {name: resource.capture_state() for name, resource in self._resources().items()},
        }

    def validate_state(self, state: Mapping[str, Any]) -> None:
        controllers = self.codec.decode(state["controllers"])
        if set(controllers) != set(getattr(self.base_env, "_controllers_dict", {})):
            raise ValueError("controller set changed since checkpoint")
        self.codec.decode(state["buffers"])
        resources = self._resources()
        if set(resources) != set(state["resources"]):
            raise ValueError("policy/noise resource set changed since checkpoint")
        for name, resource in resources.items():
            resource.validate_state(state["resources"][name])

    def restore_state(self, state: Mapping[str, Any]) -> None:
        self.validate_state(state)
        for name, value in self.codec.decode(state["buffers"]).items():
            setattr(self.base_env, name, value)
        for name, values in self.codec.decode(state["controllers"]).items():
            target = self.base_env._controllers_dict[name]
            policy = target.get("policy")
            has_policy = "policy" in target
            target.clear()
            target.update(values)
            if has_policy:
                target["policy"] = policy
        for name, resource in self._resources().items():
            resource.restore_state(state["resources"][name])


class ActuatorStateComponent:
    name = "physics.actuators"
    version = 2
    priority = 70

    def __init__(self, base_env: Any, *, tolerance: float = 1e-5):
        from isaaclab.utils.buffers import CircularBuffer, DelayBuffer, TimestampedBuffer

        if not np.isfinite(tolerance) or tolerance < 0:
            raise ValueError("actuator restore tolerance must be finite and non-negative")
        self.tolerance = float(tolerance)
        self.base_env = base_env
        self.components = {}
        for name, entity in getattr(base_env.scene, "articulations", {}).items():
            for actuator_name, actuator in getattr(entity, "actuators", {}).items():
                allowed = {"ImplicitActuator", "IdealPDActuator", "DCMotor", "DelayedPDActuator"}
                if type(actuator).__name__ not in allowed:
                    raise ValueError(f"actuator {name}/{actuator_name} needs a state adapter: {type(actuator).__name__}")
                self.components[f"{name}/{actuator_name}"] = ObjectStateComponent(
                    f"{name}/{actuator_name}", actuator, exclude={"cfg"}, priority=70,
                    value_types=(CircularBuffer, DelayBuffer),
                )
        self.codec = StateCodec((TimestampedBuffer,))

    def _wrench_sources(self) -> dict[str, Any]:
        from .resources import ReplayResource

        return {name: ReplayResource(f"external_wrench/{name}", source)
                for name, source in getattr(self.base_env, "replay_external_wrench_sources", {}).items()}

    def capture_state(self) -> Mapping[str, Any]:
        result = {name: component.capture_state() for name, component in self.components.items()}
        forces = {}
        for collection in ("articulations", "rigid_objects"):
            for name, entity in getattr(self.base_env.scene, collection, {}).items():
                values = {}
                for field in ("_external_force_b", "_external_torque_b", "_external_wrench_positions_b", "has_external_wrench", "_use_global_wrench_frame"):
                    if hasattr(entity, field):
                        values[field] = getattr(entity, field)
                for field in ("_external_force_b", "_external_torque_b"):
                    value = values.get(field)
                    if value is not None:
                        array = value.detach().cpu().numpy() if hasattr(value, "detach") else np.asarray(value)
                        if np.any(array) and not getattr(self.base_env, "replay_external_wrench_sources", {}):
                            raise ValueError(f"active external wrench on {name} requires replay_external_wrench_sources adapters")
                forces[f"{collection}/{name}"] = self.codec.encode(values)
        drives = {}
        histories = {}
        for name, entity in self.base_env.scene.articulations.items():
            view = entity.root_physx_view
            drives[name] = self.codec.encode({
                channel: getattr(view, f"get_dof_{channel}")()
                for channel in ("position_targets", "velocity_targets", "actuation_forces")
            })
            histories[name] = self.codec.encode({
                field: getattr(entity.data, field)
                for field in ("_previous_joint_vel", "_sim_timestamp", "_joint_acc")
            })
        return {"actuators": result, "forces": forces, "drives": drives, "histories": histories,
                "sources": {name: source.capture_state() for name, source in self._wrench_sources().items()}}

    def validate_state(self, state: Mapping[str, Any]) -> None:
        if set(state["actuators"]) != set(self.components):
            raise ValueError("actuator set changed since checkpoint")
        for name, component in self.components.items():
            component.validate_state(state["actuators"][name])
        if set(state["drives"]) != set(self.base_env.scene.articulations):
            raise ValueError("articulation drive set changed since checkpoint")
        sources = self._wrench_sources()
        if set(sources) != set(state["sources"]):
            raise ValueError("external wrench source set changed since checkpoint")
        for name, source in sources.items():
            source.validate_state(state["sources"][name])

    def restore_state(self, state: Mapping[str, Any]) -> None:
        self.validate_state(state)
        for name, component in self.components.items():
            component.restore_state(state["actuators"][name])
        for key, values in state["forces"].items():
            collection, name = key.split("/", 1)
            entity = getattr(self.base_env.scene, collection)[name]
            for field, value in self.codec.decode(values).items():
                setattr(entity, field, value)
        for name, values in state["drives"].items():
            entity = self.base_env.scene.articulations[name]
            for channel, value in self.codec.decode(values).items():
                getattr(entity.root_physx_view, f"set_dof_{channel}")(value, entity._ALL_INDICES)
                actual = getattr(entity.root_physx_view, f"get_dof_{channel}")()
                if not np.allclose(value.cpu().numpy(), actual.cpu().numpy(), rtol=0, atol=self.tolerance):
                    raise RuntimeError(f"PhysX drive read-back failed: {name}/{channel}")
            data = entity.data
            for buffer in vars(data).values():
                if type(buffer).__name__ == "TimestampedBuffer":
                    buffer.timestamp = -1.0
            for field, value in self.codec.decode(state["histories"][name]).items():
                setattr(data, field, value)
        for name, source in self._wrench_sources().items():
            source.restore_state(state["sources"][name])
