"""Isolated factory physics slots; not a vectorized Fire Rescue task runner."""

from __future__ import annotations

import copy
import asyncio
import inspect
from contextlib import contextmanager
from collections.abc import Mapping
from dataclasses import dataclass
from time import monotonic

from demo.fire_rescue.branching.parallel.shared_static import (
    COMMON_FACTORY_ROOT,
    is_common_factory_path,
    prepare_gpu_shared_static_source,
    split_factory_static_geometry,
    register_fabric_shared_static,
)


class SlotResetRandomStreams:
    def __init__(self, count):
        self.count = count
        self.generators = None

    def randint(self, index, low, high):
        import torch

        if not 0 <= index < self.count:
            raise ValueError("reset RNG slot is out of range")
        if self.generators is None:
            state = torch.random.get_rng_state()
            self.generators = [torch.Generator(device="cpu").set_state(state) for slot in range(self.count)]
        result = torch.randint(low, high, (1,), dtype=torch.int, device="cpu", generator=self.generators[index])
        if index == 0:
            torch.random.set_rng_state(self.generators[0].get_state())
        return result


def install_slot_reset_randomness(scene):
    import torch
    from isaaclab.actuators import DelayedPDActuator, IdealPDActuator

    streams = SlotResetRandomStreams(scene.num_envs)

    class SlotDelayedPDActuator(DelayedPDActuator):
        _replay_reset_streams = streams

        def reset(self, env_ids):
            IdealPDActuator.reset(self, env_ids)
            indices = self._ALL_INDICES if env_ids is None else self._ALL_INDICES[env_ids]
            time_lags = torch.cat([streams.randint(int(index), self.cfg.min_delay, self.cfg.max_delay + 1)
                                   for index in indices]) if len(indices) else torch.empty(0, dtype=torch.int)
            time_lags = time_lags.to(self._device)
            for buffer in (self.positions_delay_buffer, self.velocities_delay_buffer, self.efforts_delay_buffer):
                buffer.set_time_lag(time_lags, env_ids)
                buffer.reset(env_ids)

    for config in vars(scene).values():
        for actuator in getattr(config, "actuators", {}).values():
            if actuator.class_type is DelayedPDActuator:
                actuator.class_type = SlotDelayedPDActuator
            elif issubclass(actuator.class_type, DelayedPDActuator):
                raise ValueError("custom delayed actuator requires an explicit slot RNG adapter")


@dataclass(frozen=True)
class FactorySlot:
    index: int
    common_colliders: frozenset[str] = frozenset()

    def __post_init__(self):
        if isinstance(self.index, bool) or not isinstance(self.index, int) or self.index < 0:
            raise ValueError("slot index must be a nonnegative integer")
        if any(not is_common_factory_path(path) for path in self.common_colliders):
            raise ValueError("common colliders must belong to the registered Factory root")

    @property
    def root(self):
        return f"/World/envs/env_{self.index}"

    def canonical_path(self, path):
        if path in self.common_colliders:
            return "/World/Indoor" + path[len(COMMON_FACTORY_ROOT):]
        indoor = self.root + "/Indoor"
        if path == indoor or path.startswith(indoor + "/"):
            return "/World/Indoor" + path[len(indoor):]
        if path == self.root or path.startswith(self.root + "/"):
            return "/World/envs/env_0" + path[len(self.root):]
        raise ValueError(f"physical path is outside slot {self.index}: {path}")

    def logical_path(self, path):
        """Return a slot-independent path for serialized task state."""
        path = str(path)
        if path == self.root or path.startswith(self.root + "/"):
            return "/World" + path[len(self.root):]
        raise ValueError(f"logical path is outside slot {self.index}: {path}")

    def scoped_path(self, path):
        path = str(path)
        if path == self.root or path.startswith(self.root + "/"):
            return path
        world = "/World"
        if path == world:
            return self.root
        if path.startswith(world + "/"):
            return self.root + path[len(world):]
        raise ValueError(f"path is outside the USD world: {path}")

    def canonical_layout(self, layout):
        return {name: [self.canonical_path(path) for path in value] if name.endswith("_paths") else value
                for name, value in layout.items()}

    def canonical_state_paths(self, value):
        if isinstance(value, str) and (value == "/World" or value.startswith("/World/")):
            return self.canonical_path(value)
        if isinstance(value, dict):
            return {self.canonical_state_paths(key): self.canonical_state_paths(item)
                    for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return type(value)(self.canonical_state_paths(item) for item in value)
        return value


@dataclass(frozen=True)
class SlotBatch:
    """Select one environment row while preserving its batch dimension."""

    index: int
    count: int

    def __post_init__(self):
        if type(self.index) is not int or type(self.count) is not int or not 0 <= self.index < self.count:
            raise ValueError("slot batch requires an index within a positive batch")
        if self.count < 1:
            raise ValueError("slot batch requires a positive count")

    def row(self, value, name="value"):
        shape = getattr(value, "shape", None)
        if shape is None or len(shape) == 0:
            return value
        if shape[0] != self.count:
            raise ValueError(f"{name} batch axis is {shape[0]}, expected {self.count}")
        return value[self.index:self.index + 1]

    def assign(self, target, value, name="value"):
        target_shape = getattr(target, "shape", None)
        value_shape = getattr(value, "shape", None)
        if target_shape is None or len(target_shape) == 0 or target_shape[0] != self.count:
            raise ValueError(f"{name} target does not have the expected {self.count}-row batch")
        if value_shape is None or len(value_shape) == 0:
            target[self.index] = value
            return
        if value_shape[0] != 1:
            raise ValueError(f"{name} assignment must contain exactly one slot row")
        target[self.index:self.index + 1] = value


class _SlotDataView:
    def __init__(self, data, batch: SlotBatch, origin=None):
        self._data = data
        self._batch = batch
        self._origin = origin

    def __getattr__(self, name):
        value = getattr(self._data, name)
        value = self._batch.row(value, f"data.{name}")
        if self._origin is not None and name.endswith('_w') and (
                '_pos_' in name or '_state_' in name):
            value = value.clone()
            value[..., :3] -= self._origin
        return value

    def __setattr__(self, name, value):
        if name in {"_data", "_batch", "_origin"}:
            object.__setattr__(self, name, value)
            return
        target = getattr(self._data, name)
        if self._origin is not None and name.endswith('_w') and (
                '_pos_' in name or '_state_' in name):
            value = value.clone()
            value[..., :3] += self._origin
        self._batch.assign(target, value, f"data.{name}")


class _SlotEntityView:
    _ENV_SCOPED_METHODS = frozenset({
        "reset",
        "set_joint_position_target",
        "set_joint_velocity_target",
        "set_joint_effort_target",
        "write_data_to_sim",
        "write_joint_state_to_sim",
        "write_root_pose_to_sim",
        "write_root_velocity_to_sim",
        "write_root_state_to_sim",
    })

    def __init__(self, entity, batch: SlotBatch, origin=None):
        self._entity = entity
        self._batch = batch
        self._origin = origin

    @property
    def slot_index(self):
        return self._batch.index

    def __getattr__(self, name):
        value = getattr(self._entity, name)
        if name == "data":
            return _SlotDataView(value, self._batch, self._origin)
        if name in self._ENV_SCOPED_METHODS and callable(value):
            return self._scoped_method(name, value)
        return value

    def _env_ids(self, value=None):
        """Return the underlying Isaac Lab environment row for this slot."""
        import torch

        device = getattr(value, "device", None) or getattr(self._entity, "device", None)
        return torch.tensor([self._batch.index], dtype=torch.long, device=device)

    def _row_argument(self, value, name):
        if value is None:
            return None
        shape = getattr(value, "shape", None)
        if shape is None or len(shape) == 0 or shape[0] == 1:
            return value
        return self._batch.row(value, name)

    def _scoped_method(self, name, method):
        if name == "set_joint_position_target":
            def set_joint_position_target(target, joint_ids=None, env_ids=None):
                target = self._row_argument(target, "joint position target")
                return method(target, joint_ids=joint_ids, env_ids=self._env_ids(target))
            return set_joint_position_target
        if name == "set_joint_velocity_target":
            def set_joint_velocity_target(target, joint_ids=None, env_ids=None):
                target = self._row_argument(target, "joint velocity target")
                return method(target, joint_ids=joint_ids, env_ids=self._env_ids(target))
            return set_joint_velocity_target
        if name == "set_joint_effort_target":
            def set_joint_effort_target(target, joint_ids=None, env_ids=None):
                target = self._row_argument(target, "joint effort target")
                return method(target, joint_ids=joint_ids, env_ids=self._env_ids(target))
            return set_joint_effort_target
        if name == "write_data_to_sim":
            def write_data_to_sim():
                """Leave implicit arm targets for the shared pre-solve scene flush."""
                actuators = getattr(self._entity, "actuators", {})
                if not actuators or any(
                    type(actuator).__module__ != "isaaclab.actuators.actuator_pd"
                    or type(actuator).__name__ != "ImplicitActuator"
                    for actuator in actuators.values()
                ):
                    raise ValueError("slot-local flush requires stock implicit actuators")
                composers = (getattr(self._entity, name, None) for name in (
                    "_instantaneous_wrench_composer", "_permanent_wrench_composer",
                ))
                if getattr(self._entity, "has_external_wrench", False) or any(
                    getattr(composer, "active", False) for composer in composers
                ):
                    raise ValueError("slot-local flush cannot defer an active external wrench")
            return write_data_to_sim
        if name == "write_joint_state_to_sim":
            def write_joint_state_to_sim(joint_pos, joint_vel, root_state=None, env_ids=None):
                joint_pos = self._row_argument(joint_pos, "joint position")
                joint_vel = self._row_argument(joint_vel, "joint velocity")
                root_state = self._row_argument(root_state, "root state")
                if self._origin is not None and root_state is not None:
                    root_state = root_state.clone()
                    root_state[..., :3] += self._origin
                return method(joint_pos, joint_vel, root_state, self._env_ids(joint_pos))
            return write_joint_state_to_sim
        if name in {"write_root_pose_to_sim", "write_root_velocity_to_sim", "write_root_state_to_sim"}:
            def write_root_state(value, env_ids=None):
                value = self._row_argument(value, name)
                if self._origin is not None and name != 'write_root_velocity_to_sim':
                    value = value.clone()
                    value[..., :3] += self._origin
                return method(value, env_ids=self._env_ids(value))
            return write_root_state
        if name == "reset":
            def reset(env_ids=None):
                return method(env_ids=self._env_ids())
            return reset
        raise AttributeError(name)


class _SlotEntityMap(Mapping):
    def __init__(self, entities, batch: SlotBatch, origin=None):
        self._entities = entities
        self._batch = batch
        self._origin = origin

    def __getitem__(self, name):
        return _SlotEntityView(self._entities[name], self._batch, self._origin)

    def __iter__(self):
        return iter(self._entities)

    def __len__(self):
        return len(self._entities)


class SlotSceneView:
    """Read view over one scene slot; entity maps retain their original names."""

    _ENTITY_MAPS = frozenset(("articulations", "rigid_objects", "sensors", "xforms"))

    def __init__(self, scene, batch: SlotBatch):
        self._scene = scene
        self._batch = batch

    def __getattr__(self, name):
        value = getattr(self._scene, name)
        if name in self._ENTITY_MAPS and isinstance(value, Mapping):
            origin = (self._scene.env_origins[self._batch.index]
                      if getattr(self._scene, 'replay_spatial_slots', False) else None)
            return _SlotEntityMap(value, self._batch, origin)
        if name == 'env_origins' and getattr(self._scene, 'replay_spatial_slots', False):
            return value[self._batch.index:self._batch.index+1] * 0
        return value


class SlotEnvironmentView:
    """Single-slot facade for task code running over a batched environment."""

    _BATCH_MAPPINGS = frozenset((
        "actions_dict", "obs_dict", "reward_dict", "terminated_dict", "time_out_dict", "_last_computed_actions",
    ))

    def __init__(self, environment, index):
        count = getattr(environment, "num_envs", None)
        if type(count) is not int or count < 1:
            raise ValueError("batched environment must expose a positive integer num_envs")
        self._environment = environment
        self._batch = SlotBatch(index, count)
        self.scene = SlotSceneView(environment.scene, self._batch)

    @property
    def num_envs(self):
        return 1

    @property
    def slot_index(self):
        return self._batch.index

    def _env_ids(self, value=None):
        import torch

        device = getattr(value, "device", None) or getattr(self._environment, "device", None)
        return torch.tensor([self._batch.index], dtype=torch.long, device=device)

    def reset(self, *args, **kwargs):
        """Reset only this slot through the underlying direct environment."""
        if args or kwargs:
            raise ValueError("slot reset does not accept unscoped reset arguments")
        reset_idx = getattr(self._environment, "_reset_idx", None)
        if not callable(reset_idx):
            raise AttributeError("batched environment does not expose _reset_idx")
        return reset_idx(self._env_ids())

    def set_command(self, agent, command_name, value):
        shape = getattr(value, "shape", None)
        if shape is None or len(shape) == 0:
            raise ValueError("slot commands must preserve a leading batch dimension")
        if shape[0] != 1:
            value = self._batch.row(value, f"command {agent}/{command_name}")
        if command_name == 'goal_position' and getattr(self._environment.scene, 'replay_spatial_slots', False):
            value = value.clone()
            value[..., :3] += self._environment.scene.env_origins[self.slot_index]
        target = self._environment.get_command(agent, command_name)
        if target is None:
            shape = (self._batch.count, *value.shape[1:])
            if hasattr(value, "new_zeros"):
                target = value.new_zeros(shape)
            else:
                import numpy as np

                target = np.zeros(shape, dtype=value.dtype)
        else:
            target = target.clone()
        self._batch.assign(target, value, f"command {agent}/{command_name}")
        self._environment.set_command(agent, command_name, target)

    def get_command(self, agent, command_name):
        value = self._environment.get_command(agent, command_name)
        if value is None:
            return None
        value = self._batch.row(value, f"command {agent}/{command_name}")
        if command_name == 'goal_position' and getattr(self._environment.scene, 'replay_spatial_slots', False):
            value = value.clone()
            value[..., :3] -= self._environment.scene.env_origins[self.slot_index]
        return value

    def __getattr__(self, name):
        value = getattr(self._environment, name)
        if name in self._BATCH_MAPPINGS:
            presence = (getattr(self._environment, "_shared_action_presence", None)
                        if name in {"actions_dict", "_last_computed_actions"} else None)
            return {key: self._batch.row(item, f"{name}.{key}") for key, item in value.items()
                    if presence is None or self.slot_index in presence.get(key, ())}
        return self._batch.row(value, name)


def bind_slot_manager(manager, environment, index):
    """Bind a task manager to one batch row without wrapping it twice."""
    if isinstance(environment, SlotEnvironmentView):
        if environment.slot_index != index:
            raise ValueError(
                f"manager is already bound to slot {environment.slot_index}, expected {index}"
            )
        slot_view = environment
    else:
        slot_view = SlotEnvironmentView(environment, index)
    if hasattr(manager, "_env"):
        manager._env = slot_view
    return slot_view


class SlotActions(dict):
    """Batched storage plus the slots that actually submitted each command."""

    def __init__(self, values, active_slots):
        super().__init__(values)
        self.active_slots = active_slots


class SlotActionAssembler:
    """Collect one-row actions and produce one complete batched action map."""

    def __init__(self, count, *, buffers=None):
        if type(count) is not int or count < 1:
            raise ValueError("action assembler requires a positive slot count")
        self.count = count
        self._rows = {}
        self._buffers = {} if buffers is None else buffers

    def put(self, index, actions):
        SlotBatch(index, self.count)
        if not isinstance(actions, Mapping):
            raise ValueError("each slot must submit an action mapping")
        if index in self._rows:
            raise ValueError(f"slot {index} submitted actions more than once")
        normalized = {}
        for name, value in actions.items():
            shape = getattr(value, "shape", None)
            if shape is None or len(shape) == 0 or shape[0] != 1:
                raise ValueError(f"action {name!r} must contain exactly one slot row")
            normalized[name] = value
        self._rows[index] = normalized

    def complete(self):
        if len(self._rows) != self.count:
            missing = sorted(set(range(self.count)) - set(self._rows))
            raise ValueError(f"cannot step before every slot submits actions: missing {missing}")
        names = dict.fromkeys(name for index in range(self.count) for name in self._rows[index])
        result = {}
        active_slots = {}
        for name in names:
            indices = tuple(index for index in range(self.count) if name in self._rows[index])
            first = self._rows[indices[0]][name]
            shape = (self.count, *first.shape[1:])
            target = self._buffers.get(name)
            if (target is None or target.shape != shape or target.dtype != first.dtype
                    or getattr(target, "device", None) != getattr(first, "device", None)):
                if hasattr(first, "new_zeros"):
                    target = first.new_zeros(shape)
                else:
                    import numpy as np

                    target = np.zeros(shape, dtype=first.dtype)
                self._buffers[name] = target
            target[...] = 0
            for index in indices:
                row = self._rows[index][name]
                if (row.shape != first.shape or row.dtype != first.dtype
                        or getattr(row, "device", None) != getattr(first, "device", None)):
                    raise ValueError(f"action {name!r} slot rows have incompatible shape, dtype or device")
                SlotBatch(index, self.count).assign(target, row, f"action {name}")
            result[name] = target
            active_slots[name] = indices
        self._rows.clear()
        return SlotActions(result, active_slots)


class _SlotControllerConfig:
    def __init__(self, config, dispatcher):
        self._config = config
        self._dispatcher = dispatcher

    def __getattr__(self, name):
        return getattr(self._config, name)

    def compute_action_from_command(self, environment, name, command, controller):
        dispatcher = self._dispatcher
        indices = dispatcher.pending[name]
        previous = getattr(environment, "_shared_action_presence", {}).get(name, dispatcher.indices)
        if indices == dispatcher.indices and previous == dispatcher.indices:
            return self._config.compute_action_from_command(environment, name, command, controller)
        rows = {}
        for index in indices:
            with dispatcher.inference.slot(index):
                rows[index] = self._config.compute_action_from_command(
                    dispatcher.views[index], name, command[index:index + 1], controller,
                )
        first = next(iter(rows.values()))
        result = first.new_zeros((environment.num_envs, *first.shape[1:]))
        for index, row in rows.items():
            SlotBatch(index, environment.num_envs).assign(result, row, f"computed action {name}")
        return result

    def apply_action(self, environment, name, action, controller):
        dispatcher = self._dispatcher
        indices = dispatcher.pending[name]
        if indices == dispatcher.indices:
            return self._config.apply_action(environment, name, action, controller)
        for index in indices:
            self._config.apply_action(dispatcher.views[index], name, action[index:index + 1], controller)

    def compute_observations(self, environment, name):
        import torch

        dispatcher = self._dispatcher
        presence = getattr(environment, "_shared_action_presence", {}).get(name, dispatcher.indices)
        if presence == dispatcher.indices:
            return self._config.compute_observations(environment, name)
        return torch.cat([self._config.compute_observations(view, name) for view in dispatcher.views], dim=0)


class SharedSlotControllers:
    """Preserve absent-command semantics while keeping one shared physics step."""

    def __init__(self, environment, step, inference):
        self.environment = environment
        self._step = step
        self.inference = inference
        self.indices = tuple(range(environment.num_envs))
        self.views = [SlotEnvironmentView(environment, index) for index in self.indices]
        self.pending = None

    def step(self, actions):
        if not isinstance(actions, SlotActions):
            raise ValueError("shared controller dispatch requires explicit slot action presence")
        if self.pending is not None:
            raise RuntimeError("shared controller step is already active")
        if getattr(self.environment, "_replay_precomputed_actions", None) is not None:
            raise ValueError("shared task controllers cannot inject precomputed actions")
        self.pending = actions.active_slots
        try:
            return self._step(actions)
        finally:
            self.pending = None

    def __enter__(self):
        environment = self.environment
        if hasattr(environment, "_shared_action_presence"):
            raise ValueError("shared slot controllers are already installed")
        self._configs = environment._controller_configs
        self._original_pre = environment._pre_physics_step
        self._owned_pre = vars(environment).get("_pre_physics_step")

        def pre_physics_step(actions):
            if self.pending is None or set(actions) != set(self.pending):
                raise ValueError("shared physics call lost its slot action presence")
            self._original_pre(actions)
            environment._shared_action_presence = dict(self.pending)

        environment._shared_action_presence = {
            name: self.indices for name in environment.actions_dict
        }
        environment._controller_configs = {
            name: _SlotControllerConfig(config, self) for name, config in self._configs.items()
        }
        environment._pre_physics_step = pre_physics_step
        return self

    def __exit__(self, *error):
        self.environment._controller_configs = self._configs
        if self._owned_pre is None:
            del self.environment._pre_physics_step
        else:
            self.environment._pre_physics_step = self._owned_pre
        del self.environment._shared_action_presence


@dataclass
class TaskSlotState:
    """Mutable task state owned by exactly one physics slot."""

    index: int
    clock_step: int = 0
    logical: dict = None
    rng_state: object = None
    failed: bool = False

    def __post_init__(self):
        if type(self.index) is not int or self.index < 0:
            raise ValueError("task slot index must be a nonnegative integer")
        if type(self.clock_step) is not int or self.clock_step < 0:
            raise ValueError("task slot clock must be a nonnegative integer")
        if self.logical is None:
            self.logical = {}
        elif not isinstance(self.logical, dict):
            raise ValueError("task slot logical state must be a dictionary")

    def update(self, *, logical=None, rng_state=None, failed=None):
        if logical is not None:
            if not isinstance(logical, Mapping):
                raise ValueError("task slot logical state must be a mapping")
            self.logical = copy.deepcopy(dict(logical))
        if rng_state is not None:
            self.rng_state = copy.deepcopy(rng_state)
        if failed is not None:
            if type(failed) is not bool:
                raise ValueError("task slot failure state must be boolean")
            self.failed = failed

    def snapshot(self):
        return {
            "clock_step": self.clock_step,
            "logical": copy.deepcopy(self.logical),
            "rng_state": copy.deepcopy(self.rng_state),
            "failed": self.failed,
        }


class TaskSlotCoordinator:
    """Lock-step action and task-state barrier for a shared physics batch."""

    def __init__(self, count):
        if type(count) is not int or count < 1:
            raise ValueError("task slot count must be a positive integer")
        self.count = count
        self.states = {index: TaskSlotState(index) for index in range(count)}
        self._actions = None
        self._task_states = None
        self._action_buffers = {}

    def begin(self, clock_step):
        if type(clock_step) is not int or clock_step < 0:
            raise ValueError("task clock step must be a nonnegative integer")
        if self._actions is not None:
            raise RuntimeError("previous task slot tick is incomplete")
        self._actions = SlotActionAssembler(self.count, buffers=self._action_buffers)
        self._task_states = {}
        for state in self.states.values():
            if state.clock_step != clock_step:
                raise ValueError(
                    f"slot {state.index} clock is {state.clock_step}, expected {clock_step}"
                )

    def submit(self, index, actions, *, logical=None, rng_state=None, failed=None):
        if self._actions is None or self._task_states is None:
            raise RuntimeError("task slot tick has not begun")
        if index not in self.states:
            raise ValueError(f"task slot index is out of range: {index}")
        self._actions.put(index, actions)
        state = self.states[index]
        state.update(logical=logical, rng_state=rng_state, failed=failed)
        self._task_states[index] = state.snapshot()

    def complete(self):
        if self._actions is None or self._task_states is None:
            raise RuntimeError("task slot tick has not begun")
        actions = self._actions.complete()
        if set(self._task_states) != set(self.states):
            missing = sorted(set(self.states) - set(self._task_states))
            self._actions = None
            self._task_states = None
            raise ValueError(f"task state is missing for slots: {missing}")
        states = copy.deepcopy(self._task_states)
        for state in self.states.values():
            state.clock_step += 1
        self._actions = None
        self._task_states = None
        return actions, states

    def tick(self, clock_step, submissions):
        """Commit one lock-step batch from per-slot task tick submissions."""

        if not isinstance(submissions, Mapping):
            raise ValueError("task tick submissions must be a mapping")
        expected = set(range(self.count))
        if set(submissions) != expected:
            missing = sorted(expected - set(submissions))
            extra = sorted(set(submissions) - expected)
            raise ValueError(f"task tick submissions must cover every slot (missing={missing}, extra={extra})")
        self.begin(clock_step)
        try:
            for index in range(self.count):
                if index not in submissions:
                    raise ValueError(f"task tick submission is missing slot {index}")
                submission = submissions[index]
                if not isinstance(submission, Mapping) or "actions" not in submission:
                    raise ValueError(f"task tick submission for slot {index} is invalid")
                self.submit(
                    index,
                    submission["actions"],
                    logical=submission.get("logical"),
                    rng_state=submission.get("rng_state"),
                    failed=submission.get("failed"),
                )
            return self.complete()
        except Exception:
            self._actions = None
            self._task_states = None
            raise


class SharedTaskSlotScheduler:
    """Drive slot tasks through one shared physics barrier per control tick."""

    def __init__(self, environment, count, *, physics_step_fn=None, task_clock=None, progress_callback=None):
        if type(count) is not int or count < 1:
            raise ValueError("task slot count must be a positive integer")
        actual_count = getattr(environment, "num_envs", None)
        if type(actual_count) is not int or actual_count != count:
            raise ValueError(f"scheduler environment has {actual_count} slots, expected {count}")
        self.environment = environment
        self.count = count
        self.coordinator = TaskSlotCoordinator(count)
        self._physics_step = physics_step_fn or environment.step
        self.task_clock = task_clock
        self.progress_callback = progress_callback
        self._finished = set()
        self._stable_actions = {}
        self._stable_logical = {}
        self._async_submissions = {}
        self._async_waiters = {}
        self._async_finished = set()
        self._async_last_actions = {}
        self._async_last_logical = {}
        self.physics_step_count = 0
        self.peak_active_task_slots = 0
        self.active_intervals = []
        self.physics_wall_s = 0.0
        self.advanced_branch_steps = 0
        self.public_releases = {}
        self.public_active_intervals = []
        self.public_advanced_branch_steps = 0
        self.public_peak_active_slots = 0

    def release_public_branch(self, index):
        if index not in range(self.count) or index in self.public_releases:
            raise ValueError(f"invalid or repeated public branch release: {index}")
        self.public_releases[index] = {"after_physics_step": self.physics_step_count,
                                       "monotonic": monotonic()}

    def record_advance(self, clock_step, active_indices, started):
        """Count only barriers whose physical step actually returned."""
        active = sorted(active_indices)
        finished = monotonic()
        self.physics_step_count += 1
        self.physics_wall_s += max(0.0, finished - started)
        self.advanced_branch_steps += len(active)
        self.peak_active_task_slots = max(self.peak_active_task_slots, len(active))
        public_active = sorted(set(active) & self.public_releases.keys())
        if public_active:
            self.public_advanced_branch_steps += len(public_active)
            self.public_peak_active_slots = max(self.public_peak_active_slots, len(public_active))
            intervals = self.public_active_intervals
            if (intervals and intervals[-1]["active_slots"] == public_active
                    and intervals[-1]["stop_step_exclusive"] == clock_step):
                intervals[-1].update(stop_step_exclusive=clock_step + 1, finished_monotonic=finished)
            else:
                intervals.append({"start_step": clock_step, "stop_step_exclusive": clock_step + 1,
                                  "active_slots": public_active, "started_monotonic": started,
                                  "finished_monotonic": finished})
        if (self.active_intervals and self.active_intervals[-1]["active_slots"] == active
                and self.active_intervals[-1]["stop_step_exclusive"] == clock_step):
            self.active_intervals[-1].update(stop_step_exclusive=clock_step + 1, finished_monotonic=finished)
        else:
            self.active_intervals.append({
                "start_step": clock_step, "stop_step_exclusive": clock_step + 1,
                "active_slots": active, "idle_slots": sorted(set(range(self.count)) - set(active)),
                "started_monotonic": started, "finished_monotonic": finished,
            })
        if self.progress_callback is not None:
            self.progress_callback(self)

    def progress_report(self):
        return {"physics_step_count": self.physics_step_count,
                "peak_active_task_slots": self.peak_active_task_slots,
                "active_intervals": copy.deepcopy(self.active_intervals),
                "advanced_branch_steps": self.advanced_branch_steps,
                "public_releases": copy.deepcopy(self.public_releases),
                "public_active_intervals": copy.deepcopy(self.public_active_intervals),
                "public_advanced_branch_steps": self.public_advanced_branch_steps,
                "public_peak_active_slots": self.public_peak_active_slots,
                "physics_wall_s": self.physics_wall_s}

    def slot(self, index):
        return SlotEnvironmentView(self.environment, index)

    def task_environment(self, index):
        return SlotTaskEnvironmentView(self.environment, self, index)

    @staticmethod
    def _clone_actions(actions):
        return {
            name: value.clone() if hasattr(value, "clone") else copy.deepcopy(value)
            for name, value in actions.items()
        }

    def _validate_action_row(self, index, actions):
        SlotActionAssembler(self.count).put(index, actions)

    def finish(self, index, *, actions=None, logical=None, failed=False):
        """Mark a task finished; later barriers synthesize a stable zero-action row."""
        if index not in self.coordinator.states:
            raise ValueError(f"task slot index is out of range: {index}")
        if actions is not None:
            self._validate_action_row(index, actions)
            self._stable_actions[index] = self._clone_actions(actions)
        elif index not in self._stable_actions:
            raise ValueError("finished slot needs one action row before it can idle")
        self._finished.add(index)
        self._stable_logical[index] = copy.deepcopy(logical or {})
        self.coordinator.states[index].update(logical=self._stable_logical[index], failed=failed)

    def _idle_submission(self, index):
        actions = {}
        for name, value in self._stable_actions[index].items():
            if hasattr(value, "new_zeros"):
                actions[name] = value.new_zeros(value.shape)
            else:
                import numpy as np

                actions[name] = np.zeros_like(value)
        return {
            "actions": actions,
            "logical": self._stable_logical.get(index, {}),
            "failed": self.coordinator.states[index].failed,
        }

    def tick(self, clock_step, submissions):
        """Submit active slots, idle finished slots, then perform one physics step."""
        if not isinstance(submissions, Mapping):
            raise ValueError("task tick submissions must be a mapping")
        expected = set(range(self.count))
        extra = sorted(set(submissions) - expected)
        if extra:
            raise ValueError(f"task tick submissions contain out-of-range slots: {extra}")
        merged = dict(submissions)
        for index in range(self.count):
            if index not in merged:
                if index not in self._finished:
                    raise ValueError(f"active task slot {index} did not submit a tick")
                merged[index] = self._idle_submission(index)
            else:
                submission = merged[index]
                if not isinstance(submission, Mapping) or "actions" not in submission:
                    raise ValueError(f"task tick submission for slot {index} is invalid")
                self._validate_action_row(index, submission["actions"])
                if index in self._finished:
                    raise ValueError(f"finished task slot {index} submitted another tick")
                self._stable_actions[index] = self._clone_actions(submission["actions"])
                self._stable_logical[index] = copy.deepcopy(submission.get("logical", {}))
        actions, states = self.coordinator.tick(clock_step, merged)
        started = monotonic()
        physics_result = self._physics_step(actions)
        self.record_advance(clock_step, expected - self._finished, started)
        return physics_result, actions, states

    def submit_task_tick(self, index, actions, task_state):
        """Register one async task row before its slot environment steps."""
        if index not in self.coordinator.states:
            raise ValueError(f"task slot index is out of range: {index}")
        if not isinstance(task_state, Mapping):
            raise ValueError("task state submission must be a mapping")
        clock_step = task_state.get("clock_step")
        if type(clock_step) is not int or clock_step < 0:
            raise ValueError("task state submission needs a nonnegative clock_step")
        if index in self._async_submissions:
            raise RuntimeError(f"task slot {index} submitted a tick more than once")
        self._validate_action_row(index, actions)
        self._async_submissions[index] = {
            "actions": self._clone_actions(actions),
            "logical": copy.deepcopy(task_state),
        }
        self._async_last_actions[index] = self._clone_actions(actions)
        self._async_last_logical[index] = copy.deepcopy(task_state)
        return actions

    async def finish_async(self, index, *, failed=False, logical=None):
        """Remove a completed coroutine from future barriers without recycling it."""
        if index not in self.coordinator.states:
            raise ValueError(f"task slot index is out of range: {index}")
        if index in self._async_submissions or index in self._async_waiters:
            raise RuntimeError(f"task slot {index} cannot finish before its barrier returns")
        if index not in self._async_last_actions:
            raise ValueError(f"task slot {index} has no stable action row")
        self._async_finished.add(index)
        if logical is not None:
            self._async_last_logical[index] = copy.deepcopy(logical)
        self.coordinator.states[index].failed = bool(failed)
        # A sibling may already be waiting for its next step when this task
        # finishes. Re-evaluate that barrier with the smaller active set.
        await self._advance_async_barrier()

    def abort(self, error):
        """Release waiters after a task coroutine fails before the barrier."""
        self._async_submissions.clear()
        waiters = tuple(self._async_waiters.values())
        self._async_waiters.clear()
        for waiter in waiters:
            if not waiter.done():
                waiter.set_exception(error)

    async def step_slot(self, index, actions):
        """Wait for every slot, perform one batched physics step, and resume all."""
        if index not in self.coordinator.states:
            raise ValueError(f"task slot index is out of range: {index}")
        if index in self._async_finished:
            raise RuntimeError(f"finished task slot {index} submitted another tick")
        if index not in self._async_submissions:
            raise RuntimeError(f"task slot {index} must submit task state before stepping")
        if index in self._async_waiters:
            raise RuntimeError(f"task slot {index} attempted to step twice")
        self._validate_action_row(index, actions)
        waiter = asyncio.get_running_loop().create_future()
        self._async_waiters[index] = waiter
        await self._advance_async_barrier()
        return await waiter

    async def _advance_async_barrier(self):
        active_indices = set(self.coordinator.states) - self._async_finished
        if active_indices and set(self._async_waiters) == active_indices:
            submissions = self._async_submissions
            self._async_submissions = {}
            waiters = tuple(self._async_waiters.values())
            self._async_waiters.clear()
            try:
                clock_steps = {item["logical"].get("clock_step") for item in submissions.values()}
                if len(clock_steps) != 1:
                    raise ValueError(f"task slot clocks disagree at barrier: {sorted(clock_steps)}")
                clock_step = next(iter(clock_steps))
                for finished_index in self._async_finished:
                    idle_actions = {
                        name: value.new_zeros(value.shape)
                        if hasattr(value, "new_zeros") else copy.deepcopy(value) * 0
                        for name, value in self._async_last_actions[finished_index].items()
                    }
                    idle_logical = copy.deepcopy(self._async_last_logical.get(finished_index, {}))
                    idle_logical["clock_step"] = clock_step
                    submissions[finished_index] = {
                        "actions": idle_actions,
                        "logical": idle_logical,
                        "failed": self.coordinator.states[finished_index].failed,
                    }
                batched_actions, states = self.coordinator.tick(clock_step, submissions)
                started = monotonic()
                physics_result = self._physics_step(batched_actions)
                if inspect.isawaitable(physics_result):
                    physics_result = await physics_result
                if self.task_clock is not None:
                    self.task_clock.step_index += 1
                self.record_advance(clock_step, active_indices, started)
            except BaseException as error:
                self.abort(error)
                for waiter in waiters:
                    if not waiter.done():
                        waiter.set_exception(error)
                return
            for slot_waiter in waiters:
                if not slot_waiter.done():
                    slot_waiter.set_result(physics_result)

    async def run(self, task_coroutines):
        """Run slot coroutines cooperatively; fail and release all on one error."""
        tasks = [asyncio.create_task(coroutine) for coroutine in task_coroutines]
        if len(tasks) != self.count:
            for task in tasks:
                task.cancel()
            raise ValueError(f"scheduler requires {self.count} task coroutines")
        pending = set(tasks)
        try:
            while pending:
                done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_EXCEPTION)
                for task in done:
                    error = task.exception()
                    if error is not None:
                        self.abort(error)
                        for other in pending:
                            other.cancel()
                        await asyncio.gather(*pending, return_exceptions=True)
                        raise error
            return [task.result() for task in tasks]
        finally:
            self.abort(RuntimeError("task scheduler stopped"))


class SlotTaskEnvironmentView:
    """Environment facade whose ``step`` enters a shared async barrier."""

    def __init__(self, environment, scheduler: SharedTaskSlotScheduler, index):
        self._environment = environment
        self._scheduler = scheduler
        self._index = index

    async def step(self, actions):
        return await self._scheduler.step_slot(self._index, actions)

    def __getattr__(self, name):
        return getattr(self._environment, name)


def capture_fire_rescue_task_state(
    *, slot_index, clock_step, nav, emos, ur5, obstacle, assignments,
    hazard_id, hazard_pos, result, failure=None, logical_state=None,
):
    """Return the JSON-safe logical state committed with one task tick."""

    from demo.fire_rescue.task_checkpoints import checkpoint_signals

    queues = {}
    for name, queue in getattr(nav, "task_queues", {}).items():
        tasks = []
        for task in queue.all_tasks():
            tasks.append({
                "task_id": str(getattr(task, "task_id", "")),
                "task_type": str(getattr(task, "task_type", "")),
                "subtask_name": str(getattr(task, "subtask_name", "")),
                "subtask_colour": str(getattr(task, "subtask_colour", "")),
                "target_xy": [float(value) for value in getattr(task, "target_xy", ())],
                "priority": int(getattr(task, "priority", 0)),
            })
        queues[str(name)] = tasks
    rescue_state = getattr(obstacle, "state", None)
    logical = copy.deepcopy(logical_state or {})
    flags = logical.get("flags", {}) if isinstance(logical, dict) else {}
    signals = checkpoint_signals(
        assignment_applied=bool(flags.get("emos_auto_dispatched")),
        ur5=ur5,
        rescue_channel_open=bool(flags.get("rescue_channel_arm_done")),
        obstacle=obstacle,
        result=result,
    )
    return {
        "slot_index": int(slot_index),
        "clock_step": int(clock_step),
        "hazard": {
            "id": int(hazard_id or 0),
            "position": None if hazard_pos is None else [float(value) for value in hazard_pos],
        },
        "assignment": copy.deepcopy(assignments or {}),
        "emos": {
            "phase": str(getattr(emos, "_phase", "")),
            "hazard_id": int(getattr(emos, "_hazard_id", 0) or 0),
        },
        "navigation": {
            "queues": queues,
            "waiting_for_task": {str(name): bool(value) for name, value in getattr(nav, "waiting_for_task", {}).items()},
            "is_patrolling": {str(name): bool(value) for name, value in getattr(nav, "is_patrolling", {}).items()},
            "events": sorted(str(value) for value in getattr(nav, "events", ())),
        },
        "rescue": {
            "phase": str(getattr(getattr(rescue_state, "phase", None), "name", "")),
            "robot": getattr(rescue_state, "rescue_robot", None),
            "obstacle_spawned": bool(getattr(rescue_state, "obstacle_spawned", False)),
            "obstacle_removed": bool(getattr(rescue_state, "obstacle_removed", False)),
        },
        "checkpoints": signals,
        "ur5": {
            "extinguisher_robot": getattr(ur5, "_ext_robot_name", None),
            "extinguisher_active": bool(getattr(ur5, "is_extinguisher_active", False)),
            "extinguisher_grabbed": bool(getattr(ur5, "_ext_grabbed", False)),
            "button_active": bool(getattr(getattr(ur5, "scout_1_pick", None), "active", False)),
        },
        "result": str(result),
        "failure": copy.deepcopy(failure),
        "logical": logical,
    }


def partition_factory_physics_scenes(scene, *, shared_solver=False):
    """Assign task bodies to solver scenes, optionally reusing one GPU solver."""
    from pxr import Sdf, UsdPhysics
    from tools.physics_replay.validation.contact_reports import slot_owner

    stage = scene.stage
    primary = scene.physics_scene_path
    layer = stage.GetRootLayer()
    paths = [primary] * scene.num_envs if shared_solver else [primary]
    for index in range(1, scene.num_envs if not shared_solver else 1):
        path = f"{primary}_slot_{index}"
        if stage.GetPrimAtPath(path).IsValid():
            raise ValueError(f"physics scene already exists: {path}")
        if not Sdf.CopySpec(layer, primary, layer, path):
            raise RuntimeError("cannot copy baseline physics scene settings")
        paths.append(path)
    counts = [0] * scene.num_envs
    for prim in stage.Traverse():
        schemas = [schema for schema in (UsdPhysics.RigidBodyAPI, UsdPhysics.CollisionAPI)
                   if prim.HasAPI(schema)]
        if not schemas:
            continue
        owner = slot_owner(prim.GetPath())
        if owner is None or owner >= scene.num_envs:
            raise ValueError(f"unscoped physical prim: {prim.GetPath()}")
        for schema in schemas:
            schema(prim).GetSimulationOwnerRel().SetTargets([Sdf.Path(paths[owner])])
            counts[owner] += 1
    result = {"physics_scene_paths": paths, "owned_body_collision_prims": counts,
              "mode": "single-shared" if shared_solver else "per-slot",
              "settings_copied_from": primary}
    scene.replay_physics_partition = result
    return result


def validate_partition_ownership(scene):
    """Reject new physical prims that would silently enter the default scene."""
    from pxr import UsdPhysics
    from tools.physics_replay.validation.contact_reports import slot_owner

    partition = scene.replay_physics_partition
    paths = partition["physics_scene_paths"]
    gpu_shared_solver = partition.get("mode") == "single-shared" and len(set(paths)) == 1
    common = getattr(scene, "replay_static_factory", None)
    if common is not None:
        common.validate_immutable()
    for prim in scene.stage.Traverse():
        shared = common is not None and common.contains_collider(prim.GetPath())
        if shared and (prim.HasAPI(UsdPhysics.RigidBodyAPI)
                       or prim.HasAPI(UsdPhysics.ArticulationRootAPI) or prim.IsA(UsdPhysics.Joint)):
            raise ValueError("registered common geometry acquired dynamic ownership")
        for schema in (UsdPhysics.RigidBodyAPI, UsdPhysics.CollisionAPI):
            if not prim.HasAPI(schema):
                continue
            owner = slot_owner(prim.GetPath())
            # GPU env-id replication owns private collision shapes through the
            # replicated scene metadata; some composed clone specs do not
            # expose a USD simulationOwner relationship. Keep the topology
            # check strict for common geometry and CPU layouts, but do not
            # reject a valid GPU private shape for that absent relationship.
            if gpu_shared_solver and not shared:
                continue
            expected = ([paths[0]] if len(set(paths)) == 1 else paths) if shared else ([paths[owner]] if owner is not None and owner < len(paths) else [])
            if not expected or list(map(str, schema(prim).GetSimulationOwnerRel().GetTargets())) != expected:
                raise ValueError(f"physical prim lacks its task's solver scene: {prim.GetPath()}")
        if prim.IsA(UsdPhysics.Joint):
            joint = UsdPhysics.Joint(prim)
            owner = slot_owner(prim.GetPath())
            if owner is None or owner >= len(paths):
                raise ValueError(f"unscoped task joint: {prim.GetPath()}")
            for relation in (joint.GetBody0Rel(), joint.GetBody1Rel()):
                if any(slot_owner(target) != owner for target in relation.GetTargets()):
                    raise ValueError(f"joint crosses task solver scenes: {prim.GetPath()}")


def configure_shared_factory(env_cfg, *, shared=True, physics_only=False, dispatcher="physx",
                             physics_scenes="single", static_audit=None, device="cpu",
                             gpu_clone_mode="native", render_slots=None, spatial_spacing=0.0,
                             geometry_audit=None, instance_carter_meshes=False):
    from demo.fire_rescue.experiment import configure_fire_rescue_env_cfg

    if dispatcher not in ("physx", "carb"):
        raise ValueError("unknown PhysX dispatcher profile")
    device = str(device).strip().lower()
    if device == "cuda":
        device = "cuda:0"
    if device not in ("cpu", "cuda:0"):
        raise ValueError("device must be cpu or cuda:0")
    if gpu_clone_mode not in ("native", "copy", "fabric"):
        raise ValueError("gpu_clone_mode must be native, copy or fabric")
    if device == "cpu" and gpu_clone_mode != "native":
        raise ValueError("gpu_clone_mode copy/fabric is only valid for CUDA diagnostics")
    if instance_carter_meshes and not (device.startswith("cuda") and gpu_clone_mode == "copy"):
        raise ValueError("Carter geometry instancing requires CUDA copy cloning")
    if gpu_clone_mode == "fabric" and (not device.startswith("cuda") or static_audit is None):
        raise ValueError("gpu_clone_mode=fabric requires CUDA shared-static audit")
    if physics_scenes not in ("single", "per-slot") or (physics_scenes == "per-slot" and not shared):
        raise ValueError("per-slot physics scenes require the shared factory layout")
    if static_audit is not None and physics_scenes != "per-slot" and not (device.startswith("cuda") and physics_scenes == "single"):
        raise ValueError("common Factory statics require per-slot scenes, or the GPU single-shared solver")
    configure_fire_rescue_env_cfg(env_cfg, exact=True)
    if shared and dispatcher == "carb":
        from isaaclab.sim import SimulationContext

        original = SimulationContext._apply_physics_settings

        def apply_settings(simulation):
            try:
                original(simulation)
                simulation.carb_settings.set_bool("/physics/physxDispatcher", False)
            finally:
                SimulationContext._apply_physics_settings = original

        SimulationContext._apply_physics_settings = apply_settings
    scene = env_cfg.scene
    if spatial_spacing < 0 or (spatial_spacing and (static_audit is not None or render_slots is not None)):
        raise ValueError("spatial slots require private Factory copies and all-slot visibility")
    if render_slots is not None:
        render_slots = tuple(sorted(set(render_slots)))
        if not render_slots or any(type(index) is not int or not 0 <= index < scene.num_envs
                                   for index in render_slots):
            raise ValueError("render slots must select existing physics slots")
    if scene.world.prim_path != "/World/Indoor":
        raise ValueError("shared factory requires the unmodified Factory world asset")
    if not shared and scene.num_envs != 1:
        raise ValueError("legacy factory layout is valid only for one slot")
    if shared:
        scene.world.prim_path = "{ENV_REGEX_NS}/Indoor"
        scene.env_spacing = float(spatial_spacing)
        # GPU uses Isaac Lab's native batched physics replication. The Factory
        # USD remains unchanged; Isaac Sim may report unsupported nested
        # PhysicsScene payloads from the decorative light assemblies while
        # cloning, which is recorded by the probe and must not be hidden.
        scene.replicate_physics = device.startswith("cuda")
        # Fabric cloning is intentionally disabled: Factory contains nested
        # PhysicsScene payloads and the Fabric path does not expose a complete
        # USD ownership graph for the strict topology audit.
        scene.clone_in_fabric = device.startswith("cuda") and gpu_clone_mode == "fabric"
        if device.startswith("cuda") and (gpu_clone_mode == "copy" or
                                          (static_audit is not None and gpu_clone_mode == "native")):
            # Isaac Lab normally creates GPU replicas as USD inherit arcs. A
            # diagnostic copy mode asks the same Cloner API for independent
            # specs, preserving the complete Factory subtree while removing
            # mutable env_1 -> env_0 inheritance. The one-shot patch is
            # restored immediately after InteractiveScene.__init__ calls it.
            from isaaclab.scene import InteractiveScene

            original_clone_environments = InteractiveScene.clone_environments

            def clone_with_independent_specs(environment, copy_from_source=False):
                try:
                    if render_slots is not None or spatial_spacing or instance_carter_meshes:
                        from demo.fire_rescue.branching.parallel.shared_static import instance_carter_geometry

                        instance_carter_geometry(environment)
                    if geometry_audit is not None:
                        from demo.fire_rescue.branching.parallel.shared_static import instance_spatial_factory_geometry
                        instance_spatial_factory_geometry(environment, geometry_audit)
                    if static_audit is not None:
                        prepare_gpu_shared_static_source(environment, static_audit)
                    return original_clone_environments(environment, copy_from_source=True)
                finally:
                    if vars(InteractiveScene).get("clone_environments") is clone_with_independent_specs:
                        InteractiveScene.clone_environments = original_clone_environments

            InteractiveScene.clone_environments = clone_with_independent_specs
        scene.filter_collisions = True
        # The custom per-slot delay RNG is needed for the CPU isolated solver
        # path. Isaac Lab's native batched actuator reset handles CUDA tensor
        # shapes correctly; applying the CPU adapter there corrupts the batch.
        if device == "cpu":
            install_slot_reset_randomness(scene)
        if physics_scenes == "per-slot" or device.startswith("cuda") or render_slots is not None:
            from EAI.hmrs_env.multi_robot_direct_env import MultiRobotDirectEnv

            original_setup = MultiRobotDirectEnv._setup_scene
            owned_setup = vars(MultiRobotDirectEnv).get("_setup_scene")

            def setup(environment):
                try:
                    print(f"[SharedStatic][GPU] setup hook active static_audit={static_audit}", flush=True)
                    original_setup(environment)
                    # InteractiveSceneCfg's extra fields are interpreted as
                    # assets. Runtime metadata belongs on the constructed
                    # scene, otherwise Isaac Lab rejects it during spawning.
                    environment.scene._eai_fabric_clone = gpu_clone_mode == "fabric"
                    environment.scene.replay_spatial_slots = bool(spatial_spacing)
                    if spatial_spacing:
                        # Contact reporters must exist before GPU replication
                        # initializes tensor views; adding them to articulated
                        # instance geometry later invalidates the solver views.
                        from pxr import PhysxSchema, UsdPhysics
                        for prim in environment.scene.stage.Traverse():
                            if prim.HasAPI(UsdPhysics.RigidBodyAPI):
                                PhysxSchema.PhysxContactReportAPI.Apply(prim).CreateThresholdAttr().Set(0.0)
                    if render_slots is not None:
                        configure_slot_render_visibility(environment.scene, render_slots)
                    if physics_scenes == "per-slot" or static_audit is not None:
                        partition_factory_physics_scenes(
                            environment.scene,
                            shared_solver=device.startswith("cuda") and physics_scenes == "single",
                        )
                    if static_audit is not None:
                        if gpu_clone_mode == "fabric":
                            register_fabric_shared_static(environment.scene, static_audit)
                        else:
                            split_factory_static_geometry(
                                environment.scene, static_audit,
                                gpu_shared=device.startswith("cuda") and physics_scenes == "single",
                            )
                    if device.startswith("cuda"):
                        # GPU tensor indices must exist before the first solve.
                        # Keep task obstacles hidden and noncolliding until the
                        # normal Carter trigger activates their existing bodies.
                        from demo.fire_rescue.runtime.obstacle_rescue import reserve_slot_obstacles

                        reserve_slot_obstacles(environment.scene)
                finally:
                    if owned_setup is None:
                        del MultiRobotDirectEnv._setup_scene
                    else:
                        MultiRobotDirectEnv._setup_scene = owned_setup

            # One construction hook, restored before initialization/reset. The
            # reusable launcher remains the owner of both environment and app.
            MultiRobotDirectEnv._setup_scene = setup
        if physics_only:
            for config in vars(scene).values():
                spawn = getattr(config, "spawn", None)
                if spawn is not None and hasattr(spawn, "visible"):
                    spawn.visible = False


def configure_slot_render_visibility(scene, render_slots):
    """Cull unobserved overlapping visuals without changing physical activity."""
    from pxr import UsdGeom

    selected = set(render_slots)
    hidden = []
    for index in range(scene.num_envs):
        if index not in selected:
            path = f"/World/envs/env_{index}"
            prim = scene.stage.GetPrimAtPath(path)
            if not prim.IsValid() or not UsdGeom.Imageable(prim):
                raise ValueError(f"missing render slot: {path}")
            UsdGeom.Imageable(prim).CreateVisibilityAttr().Set(UsdGeom.Tokens.invisible)
            hidden.append(index)
    scene.replay_render_visibility = {
        "rendered_slots": sorted(selected), "hidden_visual_slots": hidden,
        "physics_slots": scene.num_envs, "common_static_visible": True,
        "scope": "USD visibility only; all private physics bodies and colliders remain active",
    }
    return scene.replay_render_visibility


def validate_slot_topology(
    stage,
    count,
    *,
    common_static=None,
    gpu_env_ids=False,
    gpu_fabric=False,
    allow_slot_inheritance=False,
):
    from pxr import Usd, UsdPhysics

    common_paths = common_static.collider_paths if common_static is not None else frozenset()
    if common_static is not None:
        common_static.validate_immutable()
    slots = [FactorySlot(index, common_paths) for index in range(count)]
    physical = [[] for slot in slots]
    for prim in (stage.Traverse(Usd.TraverseInstanceProxies()) if gpu_env_ids else stage.Traverse()):
        for target in prim.GetInherits().GetAllDirectInherits():
            if any(str(target) == slot.root or str(target).startswith(slot.root + "/") for slot in slots):
                owner = next((slot for slot in slots
                              if (str(prim.GetPath()) == slot.root
                                  or str(prim.GetPath()).startswith(slot.root + "/"))), None)
                valid_gpu_clone = (
                    allow_slot_inheritance
                    and gpu_env_ids
                    and owner is not None
                    and owner.index > 0
                    and (str(target) == slots[0].root
                         or str(target).startswith(slots[0].root + "/"))
                )
                if not valid_gpu_clone:
                    raise ValueError(f"mutable slot inheritance is forbidden: {prim.GetPath()} -> {target}")
        if not (prim.HasAPI(UsdPhysics.RigidBodyAPI) or prim.HasAPI(UsdPhysics.CollisionAPI)
                or prim.IsA(UsdPhysics.Joint)):
            continue
        path = str(prim.GetPath())
        if path in common_paths:
            if (prim.HasAPI(UsdPhysics.RigidBodyAPI) or prim.HasAPI(UsdPhysics.ArticulationRootAPI)
                    or prim.IsA(UsdPhysics.Joint)):
                raise ValueError(f"shared geometry cannot have dynamic ownership: {path}")
            for slot in slots:
                physical[slot.index].append(slot.canonical_path(path))
            continue
        owners = [slot for slot in slots if path.startswith(slot.root + "/")]
        if len(owners) != 1:
            raise ValueError(f"unscoped/shared physical prim: {path}")
        slot = owners[0]
        physical[slot.index].append(slot.canonical_path(path))
        if prim.IsA(UsdPhysics.Joint):
            joint = UsdPhysics.Joint(prim)
            for relation in (joint.GetBody0Rel(), joint.GetBody1Rel()):
                for target in relation.GetTargets():
                    slot.canonical_path(str(target))
    if gpu_fabric:
        if not gpu_env_ids or common_static is None or not physical[0]:
            raise ValueError("Fabric topology requires GPU env-id filtering and a static guard")
        return {"physical_prims_per_slot": len(physical[0]), "slot_count": count,
                "joint_endpoints_isolated": True,
                "shared_static_colliders": len(common_paths),
                "collision_filtering": "gpu_env_ids_fabric",
                "scope": "USD source topology plus Isaac Fabric clone count"}
    if not physical[0] or any(sorted(paths) != sorted(physical[0]) for paths in physical[1:]):
        raise ValueError("factory slots do not have identical isolated physical topology")
    collision_scope = "usd_collision_groups"
    if count > 1 and not stage.GetPrimAtPath("/World/collisions").IsValid():
        if not gpu_env_ids:
            raise ValueError("CPU collision filtering was not created")
        # GPU Cloner replication uses PhysX env-id bounds instead of the USD
        # collision-group scope used by the CPU path. Require the marker that
        # Isaac Sim writes during replication; do not infer isolation from
        # vector width alone.
        env_id_scenes = []
        for prim in stage.Traverse():
            attr = prim.GetAttribute("physxScene:envIdInBoundsBitCount")
            if attr.IsValid() and attr.Get() is not None:
                env_id_scenes.append(str(prim.GetPath()))
        if not env_id_scenes:
            raise ValueError("GPU env-id collision filtering marker was not created")
        collision_scope = "gpu_env_ids"
    return {"physical_prims_per_slot": len(physical[0]), "slot_count": count,
            "joint_endpoints_isolated": True, "shared_static_colliders": len(common_paths),
            "collision_filtering": collision_scope,
            "usd_slot_composition": ("source_inheritance" if allow_slot_inheritance
                                     else "independent_specs")}


def validate_slot_edits(stage, count):
    from pxr import Sdf, Usd

    probe = Usd.Stage.Open(stage.GetRootLayer())
    with Usd.EditContext(probe, probe.GetSessionLayer()):
        for index in range(count):
            slot = FactorySlot(index)
            name = f"EaiIsolationProbe_{index}"
            path = slot.root + "/" + name
            probe.DefinePrim(path, "Scope")
            for other in range(count):
                if other != index and probe.GetPrimAtPath(FactorySlot(other).root + "/" + name).IsValid():
                    raise ValueError("new slot descendants propagated to another branch")
            for asset in ("Indoor", "m20_1", "m20_2", "carter_1", "scout_1"):
                attribute = f"eai:{name}"
                probe.GetPrimAtPath(slot.root + "/" + asset).CreateAttribute(
                    attribute, Sdf.ValueTypeNames.Int, custom=True,
                ).Set(index)
                for other in range(count):
                    other_attribute = probe.GetPrimAtPath(FactorySlot(other).root + "/" + asset).GetAttribute(attribute)
                    if other != index and other_attribute.IsValid() and other_attribute.HasAuthoredValueOpinion():
                        raise ValueError("authored slot properties propagated to another branch")
            if stage.GetPrimAtPath(path).IsValid():
                raise ValueError("composition probe modified the live simulation stage")
    return {"new_descendants_isolated": True, "authored_asset_properties_isolated": True,
            "live_stage_untouched": True, "scope": "separate USD composition view, not live joint creation"}


@contextmanager
def rowwise_policy_inference(environment):
    """Share sessions while preserving the independent batch-one inference shape."""
    from tools.physics_replay.resources import ReplayResource
    from tools.physics_replay.physics_trace import SharedPolicyInferenceTrace

    policies = {}
    for name, controller in sorted(environment._controllers_dict.items()):
        policy = controller.get("policy")
        if policy is None:
            continue
        resource = ReplayResource(name, policy)
        if resource.mode != "onnx":
            raise ValueError("shared slots require verified stateless ONNX policies")
        resource.capture_state()
        policies[name] = policy
    trace = SharedPolicyInferenceTrace(policies, environment.num_envs)
    try:
        yield trace
    finally:
        trace.close()
