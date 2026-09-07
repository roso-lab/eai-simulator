"""Read-only, post-solve measurements at every actual physics step."""

from __future__ import annotations

from typing import Any, Callable

import numpy as np

from .tape import capture_commands


def array_copy(value: Any) -> np.ndarray:
    if hasattr(value, "detach"):
        value = value.detach().cpu().numpy()
    return np.asarray(value).copy()


class PolicyInferenceTrace:
    """Observe actual ONNXPolicy-style act calls without an extra inference/draw."""

    def __init__(self, policies: dict[str, Any]):
        self.frames = {}
        self.counts = {}
        self._hooks = []
        try:
            for name, policy in policies.items():
                original = policy.act
                owned = vars(policy).get("act")

                def act(inputs, *args, _name=name, _original=original, **kwargs):
                    index = self.counts.get(_name, 0)
                    prefix = f"inference/{_name}/{index}"
                    self.frames[f"{prefix}/observation"] = array_copy(inputs["states"])
                    result = _original(inputs, *args, **kwargs)
                    self.frames[f"{prefix}/raw_action"] = array_copy(result[0])
                    self.counts[_name] = index + 1
                    return result

                policy.act = act
                self._hooks.append((name, policy, owned, act))
        except Exception:
            self.close()
            raise

    def take_frame(self) -> dict[str, np.ndarray]:
        result = self.frames
        for name, policy, owned, hook in self._hooks:
            if policy.act is not hook:
                raise ValueError(f"policy inference trace was replaced: {name}")
            result[f"inference/{name}/calls"] = np.asarray([self.counts.get(name, 0)], dtype=np.int64)
        self.frames = {}
        self.counts = {}
        return result

    def close(self) -> None:
        for name, policy, owned, hook in reversed(self._hooks):
            if policy.act is hook:
                if owned is None:
                    delattr(policy, "act")
                else:
                    policy.act = owned
        self._hooks.clear()


class PhysicsStepTrace:
    """Instrument sim.step, not a pre-solve callback or cached scene buffers."""

    def __init__(self, simulation: Any, reader: Callable[[], dict[str, np.ndarray]], decimation: int):
        self.simulation = simulation
        self.reader = reader
        self.decimation = int(decimation)
        self.active = False
        self.frames = []
        self.error = None
        self._original_step = simulation.step
        self._owned_step = vars(simulation).get("step")

        def step(*args: Any, **kwargs: Any) -> Any:
            result = self._original_step(*args, **kwargs)
            if self.active and self.error is None:
                try:
                    frame = {name: array_copy(value) for name, value in self.reader().items()}
                    if not frame or any(not np.all(np.isfinite(value)) for value in frame.values()):
                        raise ValueError("empty/non-finite physics measurement")
                    self.frames.append(frame)
                    if len(self.frames) > self.decimation:
                        raise ValueError("unexpected extra physics step during verification")
                except Exception as exc:
                    self.error = str(exc)
            return result

        self._step = step
        simulation.step = step

    def begin(self) -> None:
        self.frames.clear()
        self.error = None
        self.active = True

    def take_frame(self) -> dict[str, np.ndarray]:
        if self.error is not None:
            raise ValueError(f"physics trace failed: {self.error}")
        if len(self.frames) != self.decimation:
            raise ValueError(f"physics substep count mismatch: {len(self.frames)} != {self.decimation}")
        result = {f"substep/{index}/{name}": value
                  for index, frame in enumerate(self.frames) for name, value in frame.items()}
        self.frames.clear()
        return result

    def finish(self) -> None:
        self.active = False
        self.frames.clear()

    def close(self) -> None:
        self.finish()
        if self.simulation.step is self._step:
            if self._owned_step is None:
                delattr(self.simulation, "step")
            else:
                self.simulation.step = self._owned_step
        closer = getattr(self.reader, "close", None)
        if closer is not None:
            closer()


class PhysxMeasurementReader:
    """Measure active bodies, link reactions, normal contacts and friction patches."""

    def __init__(self, base_env: Any):
        import carb.settings
        import omni.physics.tensors
        import omni.usd
        from omni.physx import get_physx_simulation_interface
        from pxr import PhysxSchema, UsdPhysics

        self.base_env = base_env
        self.dt = float(base_env.physics_dt)
        stage = omni.usd.get_context().get_stage()
        paths = []
        contact_paths = set()
        filter_paths = set()
        for prim in stage.Traverse():
            if prim.HasAPI(UsdPhysics.RigidBodyAPI) and UsdPhysics.RigidBodyAPI(prim).GetRigidBodyEnabledAttr().Get() is not False:
                paths.append(str(prim.GetPath()))
            if prim.HasAPI(UsdPhysics.CollisionAPI) and UsdPhysics.CollisionAPI(prim).GetCollisionEnabledAttr().Get() is not False:
                owner = prim
                while owner.IsValid() and not owner.HasAPI(UsdPhysics.RigidBodyAPI):
                    owner = owner.GetParent()
                if owner.IsValid() and UsdPhysics.RigidBodyAPI(owner).GetRigidBodyEnabledAttr().Get() is not False:
                    contact_paths.add(str(owner.GetPath()))
                    filter_paths.add(str(owner.GetPath()))
                elif not owner.IsValid():
                    filter_paths.add(str(prim.GetPath()))
        if not paths:
            raise ValueError("no physical bodies available for force verification")
        for path in sorted(contact_paths):
            PhysxSchema.PhysxContactReportAPI.Apply(stage.GetPrimAtPath(path)).CreateThresholdAttr().Set(0.0)
        get_physx_simulation_interface().flush_changes()
        base_env.sim.forward()
        self.simulation_view = omni.physics.tensors.create_simulation_view("numpy")
        self.bodies = self.simulation_view.create_rigid_body_view(sorted(paths))
        self.contacts = self.simulation_view.create_rigid_contact_view(
            sorted(contact_paths), filter_patterns=[sorted(filter_paths) for path in contact_paths],
            max_contact_data_count=16384,
        )
        if self.contacts is None or sorted(self.contacts.sensor_paths) != sorted(contact_paths):
            actual = self.contacts.sensor_paths if self.contacts is not None else []
            raise ValueError(f"incomplete PhysX contact-report coverage: expected={sorted(contact_paths)}, actual={actual}")
        if self.contacts.filter_count != len(filter_paths):
            raise ValueError("incomplete PhysX collision filter coverage")
        self.paths = tuple(self.bodies.prim_paths)
        self.filter_paths = sorted(filter_paths)
        self.settings = carb.settings.get_settings()
        self._contact_processing_disabled = self.settings.get_as_bool("/physics/disableContactProcessing")
        self.settings.set_bool("/physics/disableContactProcessing", False)

    def close(self) -> None:
        self.settings.set_bool("/physics/disableContactProcessing", self._contact_processing_disabled)

    def describe(self) -> dict[str, Any]:
        return {"physics_dt": self.dt, "body_paths": list(self.paths),
                "contact_sensor_paths": list(self.contacts.sensor_paths), "contact_filter_paths": self.filter_paths}

    def _patches(self, result: dict[str, np.ndarray], prefix: str, data: tuple, fields: tuple[str, ...]) -> None:
        buffers = [array_copy(value) for value in data]
        counts, starts = buffers[-2:]
        if int(counts.sum()) >= self.contacts.max_contact_data_count:
            raise ValueError("contact buffer capacity reached; refusing truncated force measurements")
        result[f"{prefix}/counts"] = counts
        collected = {field: [] for field in fields}
        pair_indices = []
        for sensor_index in range(self.contacts.sensor_count):
            for filter_index in range(self.contacts.filter_count):
                count = int(counts[sensor_index, filter_index])
                if not count:
                    continue
                start = int(starts[sensor_index, filter_index])
                if start < 0 or start + count > self.contacts.max_contact_data_count:
                    raise ValueError("invalid contact buffer range")
                pieces = [buffer[start:start + count].reshape(count, -1) for buffer in buffers[:-2]]
                points = pieces[1]
                order = np.lexsort(tuple(points[:, index] for index in reversed(range(points.shape[1]))))
                pieces = [piece[order] for piece in pieces]
                pair_indices.extend([(sensor_index, filter_index)] * count)
                for field, values in zip(fields, pieces):
                    collected[field].append(values)
        result[f"{prefix}/pair_indices"] = np.asarray(pair_indices, dtype=np.int64).reshape(-1, 2)
        for field, buffer in zip(fields, buffers[:-2]):
            result[f"{prefix}/{field}"] = np.concatenate(collected[field]) if collected[field] else np.empty(
                (0, int(np.prod(buffer.shape[1:]))), dtype=buffer.dtype,
            )

    def __call__(self) -> dict[str, np.ndarray]:
        result = {
            "bodies/transforms_xyzw": array_copy(self.bodies.get_transforms()),
            "bodies/velocities": array_copy(self.bodies.get_velocities()),
            "contacts/net_force_N": array_copy(self.contacts.get_net_contact_forces(self.dt)),
        }
        result["contacts/net_impulse_Ns"] = result["contacts/net_force_N"] * self.dt
        for name, entity in sorted(self.base_env.scene.articulations.items()):
            view = entity.root_physx_view
            for channel in ("dof_positions", "dof_velocities", "dof_actuation_forces", "link_incoming_joint_force"):
                result[f"{name}/{channel}"] = array_copy(getattr(view, f"get_{channel}")())
        for name, action in getattr(self.base_env, "_last_computed_actions", {}).items():
            result[f"policy/{name}/action"] = array_copy(action)
        for name, command in capture_commands(self.base_env).items():
            result[f"command/{name}"] = command
        self._patches(result, "contacts/normal", self.contacts.get_contact_data(self.dt),
                      ("force_N", "point_m", "normal", "separation_m"))
        self._patches(result, "contacts/friction", self.contacts.get_friction_data(self.dt),
                      ("force_N", "point_m"))
        return result
