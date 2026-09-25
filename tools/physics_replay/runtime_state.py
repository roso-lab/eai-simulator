"""Explicit, non-pickle snapshots of mutable Python and actuator state."""

from __future__ import annotations

from collections import deque
from dataclasses import is_dataclass
from enum import Enum
from types import SimpleNamespace
from typing import Any, Iterable, Mapping

import numpy as np


def _numpy(value):
    return value.detach().cpu().numpy() if hasattr(value, "detach") else np.asarray(value)


def _stable_state_key(value: Any) -> tuple[Any, ...]:
    if isinstance(value, Mapping):
        entries = tuple(sorted(
            (_stable_state_key(key), _stable_state_key(item))
            for key, item in value.items()
        ))
        return ("mapping", entries)
    if isinstance(value, (list, tuple)):
        return (type(value).__name__, tuple(_stable_state_key(item) for item in value))
    if isinstance(value, np.ndarray):
        return ("numpy", str(value.dtype), tuple(value.shape), value.tobytes().hex())
    return (type(value).__module__, type(value).__qualname__, repr(value))


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
        if isinstance(value, (set, frozenset)):
            encoded_items = [self.encode(item) for item in value]
            encoded_items.sort(key=_stable_state_key)
            return {
                "kind": type(value).__name__,
                "items": encoded_items,
                "maxlen": None,
            }
        if isinstance(value, (list, tuple, deque)):
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
            value_type = self.types[value["type"]]
            restored = SimpleNamespace() if value_type is SimpleNamespace else object.__new__(value_type)
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

    def __init__(self, base_env: Any, *, tolerance: float = 1e-5,
                 env_index: int | None = None, batch_count: int | None = None):
        from isaaclab.utils.buffers import CircularBuffer, DelayBuffer, TimestampedBuffer

        if not np.isfinite(tolerance) or tolerance < 0:
            raise ValueError("actuator restore tolerance must be finite and non-negative")
        self.tolerance = float(tolerance)
        self.base_env = base_env
        self.env_index = env_index
        self.batch_count = batch_count
        if env_index is not None and (type(env_index) is not int or env_index < 0):
            raise ValueError("actuator slot index must be a nonnegative integer")
        if batch_count is not None and (type(batch_count) is not int or batch_count < 1):
            raise ValueError("actuator batch count must be positive")
        if (env_index is None) != (batch_count is None):
            raise ValueError("actuator slot index and batch count must be provided together")
        if env_index is not None and env_index >= batch_count:
            raise ValueError("actuator slot index is outside the batch")
        self.components = {}
        from isaaclab.actuators import DCMotor, DelayedPDActuator, IdealPDActuator, ImplicitActuator

        allowed_types = (ImplicitActuator, IdealPDActuator, DCMotor, DelayedPDActuator)
        for name, entity in getattr(base_env.scene, "articulations", {}).items():
            for actuator_name, actuator in getattr(entity, "actuators", {}).items():
                if not isinstance(actuator, allowed_types):
                    raise ValueError(
                        f"actuator {name}/{actuator_name} needs a state adapter: {type(actuator).__name__}"
                    )
                self.components[f"{name}/{actuator_name}"] = ObjectStateComponent(
                    f"{name}/{actuator_name}", actuator, exclude={"cfg"}, priority=70,
                    value_types=(CircularBuffer, DelayBuffer),
                )
        self.codec = StateCodec((TimestampedBuffer,))

    def _slotize(self, value: Any, *, section: str, field: str = "", owner_type: str = "") -> Any:
        env_index = getattr(self, "env_index", None)
        if env_index is None:
            return value
        if isinstance(value, dict):
            kind = value.get("kind")
            if kind is None:
                return {
                    key: self._slotize(item, section=section, field=str(key), owner_type=owner_type)
                    for key, item in value.items()
                }
            if kind in {"numpy", "tensor"}:
                array = np.asarray(value["value"])
                if section == "actuators" and field == "_ALL_INDICES":
                    if not np.array_equal(array, np.arange(self.batch_count)):
                        raise ValueError("actuator environment indices differ from the slot batch")
                    return {**value, "value": np.zeros(1, dtype=array.dtype)}
                if field == "_buffer" and owner_type == "isaaclab.utils.buffers.circular_buffer.CircularBuffer":
                    if array.ndim < 2 or array.shape[1] != self.batch_count:
                        raise ValueError("circular buffer environment axis differs from the slot batch")
                    return {**value, "value": array[:, env_index:env_index + 1].copy()}
                if array.ndim and array.shape[0] == self.batch_count and (
                    section != "actuators" or array.ndim > 1
                    or field in {"computed_effort", "applied_effort", "_max_len", "_num_pushes", "_time_lags"}
                ):
                    copied = dict(value)
                    copied["value"] = array[env_index:env_index + 1].copy()
                    return copied
                return value
            if kind == "dict":
                return {
                    **value,
                    "items": [
                        (self._slotize(key, section=section, field=field, owner_type=owner_type),
                         self._slotize(item, section=section,
                                       field=str(key) if isinstance(key, str) else field, owner_type=owner_type))
                        for key, item in value["items"]
                    ],
                }
            if kind == "object":
                return {
                    **value,
                    "attributes": self._slotize(value["attributes"], section=section, field=field,
                                                owner_type=value["type"]),
                }
            if "items" in value:
                return {**value, "items": [
                    self._slotize(item, section=section, field=field, owner_type=owner_type)
                    for item in value["items"]
                ]}
            return value
        if field in {"_num_envs", "_batch_size"} and isinstance(value, int):
            return 1 if value == self.batch_count else value
        return value

    def _wrench_sources(self) -> dict[str, Any]:
        from .resources import ReplayResource

        return {name: ReplayResource(f"external_wrench/{name}", source)
                for name, source in getattr(self.base_env, "replay_external_wrench_sources", {}).items()}

    def _merge_slot(self, current: Any, incoming: Any, *, field="", section="actuators", owner_type="") -> Any:
        """Merge a one-row snapshot into the live batched value.

        Actuator snapshots are captured through a slot view, so restoring the
        decoded value directly would replace every environment row.  Preserve
        sibling rows and update only ``env_index``.  CircularBuffer histories
        store environments on axis 1; ordinary tensors use axis 0.
        """
        env_index = getattr(self, "env_index", None)
        batch_count = getattr(self, "batch_count", None)
        if env_index is None:
            return incoming
        if field in {"_num_envs", "_batch_size"}:
            if incoming != 1 or current != batch_count:
                raise ValueError(f"invalid slot cardinality: {field}")
            return current
        if hasattr(current, "shape") and hasattr(incoming, "shape"):
            if field == "_ALL_INDICES":
                if incoming.shape != (1,) or int(incoming[0]) != 0:
                    raise ValueError("invalid canonical actuator indices")
                return current
            axis = None
            if field == "_buffer" and owner_type.endswith(".CircularBuffer"):
                axis = 1
            elif current.ndim and current.shape[0] == batch_count and (
                section != "actuators" or current.ndim > 1
                or field in {"computed_effort", "applied_effort", "_max_len", "_num_pushes", "_time_lags"}
            ):
                axis = 0
            if axis is not None:
                expected_shape = list(current.shape)
                expected_shape[axis] = 1
                if tuple(expected_shape) != tuple(incoming.shape):
                    raise ValueError(f"invalid slot shape: {field}: {incoming.shape}")
                merged = current.clone() if hasattr(current, "clone") else current.copy()
                selection = [slice(None)] * current.ndim
                selection[axis] = slice(env_index, env_index + 1)
                merged[tuple(selection)] = incoming
                return merged
            if not np.array_equal(_numpy(current), _numpy(incoming)):
                raise ValueError(f"shared actuator array changed: {field}")
            return current
        if isinstance(current, dict) and isinstance(incoming, dict):
            if current.keys() != incoming.keys():
                raise ValueError(f"slot state keys changed: {field}")
            return {key: self._merge_slot(current[key], value, field=str(key), section=section,
                                          owner_type=owner_type) for key, value in incoming.items()}
        if hasattr(current, "__dict__") and type(current) is type(incoming):
            type_name = f"{type(current).__module__}.{type(current).__qualname__}"
            attributes = dict(vars(incoming))
            if type_name.endswith(".CircularBuffer"):
                # The batch has one ring pointer. Rotate this row's history to
                # that pointer so later restores cannot move sibling histories.
                saved_pointer = attributes["_pointer"]
                attributes["_pointer"] = current._pointer
                if attributes.get("_buffer") is not None:
                    import torch
                    attributes["_buffer"] = torch.roll(attributes["_buffer"], current._pointer - saved_pointer, 0)
            for name, value in attributes.items():
                setattr(current, name, self._merge_slot(getattr(current, name), value, field=name,
                                                       section=section, owner_type=type_name))
            return current
        if current != incoming:
            raise ValueError(f"shared actuator metadata changed: {field}: {current!r} != {incoming!r}")
        return current

    def _restore_component_slot(self, component, state):
        decoded = component.codec.decode(state["attributes"])
        if getattr(self, "env_index", None) is None:
            component.restore_state(state)
            return
        current = vars(component.instance)
        for name in set(current) - component.exclude - set(decoded):
            delattr(component.instance, name)
        for name, value in decoded.items():
            if name not in component.exclude and name in current:
                value = self._merge_slot(current[name], value, field=name)
            setattr(component.instance, name, value)

    def capture_state(self) -> Mapping[str, Any]:
        result = {name: self._slotize(component.capture_state(), section="actuators")
                  for name, component in self.components.items()}
        env_index = getattr(self, "env_index", None)

        def is_zero(value: Any) -> bool:
            if hasattr(value, "detach"):
                value = value.detach().cpu().numpy()
            if isinstance(value, np.ndarray):
                return bool(not value.size or np.all(value == 0))
            if isinstance(value, np.generic):
                return bool(value == 0)
            return value is False or value == 0

        forces = {}
        for collection in ("articulations", "rigid_objects"):
            for name, entity in getattr(self.base_env.scene, collection, {}).items():
                values = {}
                for field in ("_external_force_b", "_external_torque_b", "_external_wrench_positions_b", "has_external_wrench", "_use_global_wrench_frame"):
                    if hasattr(entity, field):
                        value = getattr(entity, field)
                        if env_index is not None:
                            array = value.detach().cpu().numpy() if hasattr(value, "detach") else np.asarray(value)
                            if array.ndim and array.shape[0] == self.batch_count:
                                value = value[env_index:env_index + 1]
                        values[field] = value
                for field in ("_external_force_b", "_external_torque_b"):
                    value = values.get(field)
                    if value is not None:
                        array = value.detach().cpu().numpy() if hasattr(value, "detach") else np.asarray(value)
                        if np.any(array) and not getattr(self.base_env, "replay_external_wrench_sources", {}):
                            raise ValueError(f"active external wrench on {name} requires replay_external_wrench_sources adapters")
                active_wrench = values.get("has_external_wrench", False)
                if is_zero(active_wrench):
                    values = {}
                else:
                    values = {field: value for field, value in values.items() if not is_zero(value)}
                forces[f"{collection}/{name}"] = self._slotize(
                    self.codec.encode(values), section="forces"
                )
        drives = {}
        histories = {}
        for name, entity in self.base_env.scene.articulations.items():
            view = entity.root_physx_view
            drives[name] = self._slotize(self.codec.encode({
                channel: getattr(view, f"get_dof_{channel}")()
                for channel in ("position_targets", "velocity_targets", "actuation_forces")
            }), section="drives")
            histories[name] = self._slotize(self.codec.encode({
                field: getattr(entity.data, field)
                for field in ("_previous_joint_vel", "_sim_timestamp", "_joint_acc")
            }), section="histories")
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
            self._restore_component_slot(component, state["actuators"][name])
        for key, values in state["forces"].items():
            collection, name = key.split("/", 1)
            entity = getattr(self.base_env.scene, collection)[name]
            for field, value in self.codec.decode(values).items():
                current = getattr(entity, field, None)
                setattr(entity, field, self._merge_slot(current, value, field=field, section="forces"))
        for name, values in state["drives"].items():
            entity = self.base_env.scene.articulations[name]
            for channel, value in self.codec.decode(values).items():
                if getattr(self, "env_index", None) is None:
                    target = entity._ALL_INDICES
                    expected = value
                    write_value = value
                else:
                    import torch
                    target = torch.as_tensor([self.env_index], device=value.device, dtype=torch.long)
                    expected = value
                    write_value = getattr(entity.root_physx_view, f"get_dof_{channel}")().clone()
                    write_value[self.env_index:self.env_index + 1] = value
                getattr(entity.root_physx_view, f"set_dof_{channel}")(write_value, target)
                actual = getattr(entity.root_physx_view, f"get_dof_{channel}")()
                if getattr(self, "env_index", None) is not None:
                    actual = actual[self.env_index:self.env_index + 1]
                if not np.allclose(expected.cpu().numpy(), actual.cpu().numpy(), rtol=0, atol=self.tolerance):
                    raise RuntimeError(f"PhysX drive read-back failed: {name}/{channel}")
            data = entity.data
            for field, buffer in vars(data).items():
                if type(buffer).__name__ == "TimestampedBuffer" and field != "_joint_acc":
                    buffer.timestamp = -1.0
            for field, value in self.codec.decode(state["histories"][name]).items():
                setattr(data, field, self._merge_slot(getattr(data, field, None), value, field=field, section="histories"))
        for name, source in self._wrench_sources().items():
            source.restore_state(state["sources"][name])
