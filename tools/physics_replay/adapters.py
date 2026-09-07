"""Isaac Lab state access isolated behind a small duck-typed adapter."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

import numpy as np


def _to_numpy(value: Any) -> np.ndarray:
    detached = value.detach() if hasattr(value, "detach") else value
    moved = detached.cpu() if hasattr(detached, "cpu") else detached
    return np.asarray(moved).copy()


def _backend_array(value: Any, reference: Any, *, batch: bool = True) -> Any:
    array = np.asarray(value)
    if batch:
        array = array[None, ...]
    reference_device = getattr(reference, "device", None)
    if reference_device is None:
        return array
    try:
        import torch
    except ModuleNotFoundError:
        return array
    kwargs: dict[str, Any] = {}
    if getattr(reference, "dtype", None) is not None:
        kwargs["dtype"] = reference.dtype
    kwargs["device"] = reference_device
    return torch.as_tensor(array, **kwargs)


def _env_ids(env_id: int, reference: Any) -> Any:
    reference_device = getattr(reference, "device", None)
    if reference_device is None:
        return np.asarray([env_id], dtype=np.int64)
    try:
        import torch
    except ModuleNotFoundError:
        return np.asarray([env_id], dtype=np.int64)
    return torch.as_tensor([env_id], dtype=torch.long, device=reference_device)


def _channel_name(key: str) -> str:
    return str(key).rsplit("/", 1)[-1]


class IsaacLabStateAdapter:
    """Read and write EAI scene object state without importing Isaac eagerly."""

    def __init__(self, base_env: Any):
        scene = getattr(base_env, "scene", None)
        if scene is None:
            raise ValueError("base_env has no scene")
        self.base_env = base_env
        self.articulations = dict(getattr(scene, "articulations", {}) or {})
        self.rigid_objects = dict(getattr(scene, "rigid_objects", {}) or {})

    def _object(self, target: Any) -> Any:
        collections = (
            self.articulations
            if target.kind in {"articulation", "link"}
            else self.rigid_objects
        )
        try:
            return collections[target.collection_name]
        except KeyError as exc:
            raise ValueError(
                f"scene object {target.collection_name!r} is unavailable for {target.target_id}"
            ) from exc

    def _data_value(self, obj: Any, name: str) -> Any:
        value = getattr(getattr(obj, "data", None), name, None)
        if value is None:
            raise ValueError(f"scene object has no data field {name!r}")
        return value

    def _root_pose(self, obj: Any, target: Any) -> np.ndarray:
        if target.kind == "link":
            index = int(target.body_index)
            pos = self._data_value(obj, "body_pos_w")[target.spec.env_id, index]
            quat = self._data_value(obj, "body_quat_w")[target.spec.env_id, index]
        else:
            pos = self._data_value(obj, "root_pos_w")[target.spec.env_id]
            quat = self._data_value(obj, "root_quat_w")[target.spec.env_id]
        return np.concatenate((_to_numpy(pos).reshape(-1), _to_numpy(quat).reshape(-1)))

    def _velocity(self, obj: Any, target: Any) -> np.ndarray:
        if target.kind == "link":
            index = int(target.body_index)
            linear = self._data_value(obj, "body_lin_vel_w")[target.spec.env_id, index]
            angular = self._data_value(obj, "body_ang_vel_w")[target.spec.env_id, index]
        else:
            linear = self._data_value(obj, "root_lin_vel_w")[target.spec.env_id]
            angular = self._data_value(obj, "root_ang_vel_w")[target.spec.env_id]
        return np.concatenate((_to_numpy(linear).reshape(-1), _to_numpy(angular).reshape(-1)))

    def _joint_values(self, obj: Any, target: Any) -> np.ndarray:
        position = _to_numpy(self._data_value(obj, "joint_pos")[target.spec.env_id]).reshape(-1)
        velocity = _to_numpy(self._data_value(obj, "joint_vel")[target.spec.env_id]).reshape(-1)
        if target.joint_indices:
            indices = list(target.joint_indices)
            position = position[indices]
            velocity = velocity[indices]
        return np.concatenate((position, velocity))

    def _joint_targets(self, obj: Any, target: Any) -> np.ndarray:
        position = _to_numpy(self._data_value(obj, "joint_pos_target")[target.spec.env_id]).reshape(-1)
        velocity = _to_numpy(self._data_value(obj, "joint_vel_target")[target.spec.env_id]).reshape(-1)
        if target.joint_indices:
            indices = list(target.joint_indices)
            position = position[indices]
            velocity = velocity[indices]
        return np.concatenate((position, velocity))

    def _contact_force(self, obj: Any, target: Any) -> np.ndarray:
        force = self._data_value(obj, "net_contact_forces_w")
        if getattr(force, "ndim", 0) == 2:
            value = force[target.spec.env_id]
        else:
            body_index = 0 if target.body_index is None else int(target.body_index)
            value = force[target.spec.env_id, body_index]
        return _to_numpy(value).reshape(-1)

    def _joint_effort(self, obj: Any, target: Any) -> np.ndarray:
        for name in ("applied_torque", "joint_effort", "computed_torque"):
            value = getattr(getattr(obj, "data", None), name, None)
            if value is not None:
                effort = _to_numpy(value[target.spec.env_id]).reshape(-1)
                if target.joint_indices:
                    effort = effort[list(target.joint_indices)]
                return effort
        raise ValueError(f"scene object has no joint effort field for {target.target_id}")

    def read(self, target: Any, channels: Sequence[str]) -> dict[str, np.ndarray]:
        obj = self._object(target)
        values: dict[str, np.ndarray] = {}
        for key in channels:
            channel = _channel_name(str(key))
            if channel == "joint_effort_target":
                value = _to_numpy(self._data_value(obj, "joint_effort_target")[target.spec.env_id])
                if target.joint_indices:
                    value = value[list(target.joint_indices)]
            elif channel == "pose":
                value = self._root_pose(obj, target)
            elif channel == "velocity":
                value = self._velocity(obj, target)
            elif channel == "joint_state":
                value = self._joint_values(obj, target)
            elif channel == "joint_target":
                value = self._joint_targets(obj, target)
            elif channel == "contact_force":
                value = self._contact_force(obj, target)
            elif channel == "joint_effort":
                value = self._joint_effort(obj, target)
            else:
                raise ValueError(f"unsupported replay channel: {channel}")
            logical_key = str(key) if "/" in str(key) else f"{target.target_id}/{channel}"
            values[logical_key] = value
        return values

    def _write_root(self, obj: Any, target: Any, values: Mapping[str, np.ndarray]) -> None:
        if target.kind == "link":
            raise ValueError(f"link target {target.target_id} has no writable root API")
        env_id = int(target.spec.env_id)
        if f"{target.target_id}/pose" in values:
            pose = np.asarray(values[f"{target.target_id}/pose"])
            reference = self._data_value(obj, "root_pos_w")
            obj.write_root_pose_to_sim(
                _backend_array(pose, reference), _env_ids(env_id, reference)
            )
        if f"{target.target_id}/velocity" in values:
            velocity = np.asarray(values[f"{target.target_id}/velocity"])
            reference = self._data_value(obj, "root_lin_vel_w")
            obj.write_root_velocity_to_sim(
                _backend_array(velocity, reference), _env_ids(env_id, reference)
            )

    def _write_joints(self, obj: Any, target: Any, values: Mapping[str, np.ndarray]) -> None:
        key = f"{target.target_id}/joint_state"
        if key not in values:
            return
        if target.kind != "articulation":
            raise ValueError(f"target {target.target_id} is not an articulation root")
        data = getattr(obj, "data", None)
        position_ref = self._data_value(obj, "joint_pos")
        velocity_ref = self._data_value(obj, "joint_vel")
        position = _to_numpy(position_ref[target.spec.env_id]).reshape(-1)
        velocity = _to_numpy(velocity_ref[target.spec.env_id]).reshape(-1)
        incoming = np.asarray(values[key]).reshape(-1)
        count = len(target.joint_indices) or position.size
        if incoming.size != count * 2:
            raise ValueError(
                f"joint_state for {target.target_id} has {incoming.size} values; expected {count * 2}"
            )
        indices = list(target.joint_indices) if target.joint_indices else list(range(position.size))
        position[indices] = incoming[:count]
        velocity[indices] = incoming[count:]
        env_ids = _env_ids(int(target.spec.env_id), position_ref)
        obj.write_joint_state_to_sim(
            _backend_array(position, position_ref),
            _backend_array(velocity, velocity_ref),
            None,
            env_ids,
        )

    def _write_joint_targets(self, obj: Any, target: Any, values: Mapping[str, np.ndarray]) -> None:
        key = f"{target.target_id}/joint_target"
        if key not in values:
            return
        if target.kind != "articulation":
            raise ValueError(f"target {target.target_id} is not an articulation root")
        position_ref = self._data_value(obj, "joint_pos_target")
        velocity_ref = self._data_value(obj, "joint_vel_target")
        position = _to_numpy(position_ref[target.spec.env_id]).reshape(-1)
        velocity = _to_numpy(velocity_ref[target.spec.env_id]).reshape(-1)
        incoming = np.asarray(values[key]).reshape(-1)
        count = len(target.joint_indices) or position.size
        if incoming.size != count * 2:
            raise ValueError(
                f"joint_target for {target.target_id} has {incoming.size} values; expected {count * 2}"
            )
        indices = list(target.joint_indices) if target.joint_indices else list(range(position.size))
        position[indices] = incoming[:count]
        velocity[indices] = incoming[count:]
        env_ids = _env_ids(int(target.spec.env_id), position_ref)
        obj.set_joint_position_target(
            _backend_array(position, position_ref), joint_ids=None, env_ids=env_ids
        )
        obj.set_joint_velocity_target(
            _backend_array(velocity, velocity_ref), joint_ids=None, env_ids=env_ids
        )

    def write(self, target: Any, values: Mapping[str, np.ndarray]) -> None:
        obj = self._object(target)
        self._write_root(obj, target, values)
        self._write_joints(obj, target, values)
        self._write_joint_targets(obj, target, values)
        effort_key = f"{target.target_id}/joint_effort_target"
        if effort_key in values:
            reference = self._data_value(obj, "joint_effort_target")
            effort = _to_numpy(reference[target.spec.env_id])
            indices = list(target.joint_indices) if target.joint_indices else slice(None)
            effort[indices] = values[effort_key]
            obj.set_joint_effort_target(
                _backend_array(effort, reference),
                env_ids=_env_ids(target.spec.env_id, reference),
            )

    def refresh(self) -> None:
        """Refresh Isaac Lab entity caches after direct PhysX tensor writes."""

        scene = getattr(self.base_env, "scene", None)
        updater = getattr(scene, "update", None)
        if not callable(updater):
            return
        dt = float(getattr(self.base_env, "physics_dt", 0.0))
        updater(dt=dt)
