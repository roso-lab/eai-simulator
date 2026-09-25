"""Read-only, post-solve measurements at every actual physics step."""

from __future__ import annotations

from typing import Any, Callable
from contextlib import contextmanager

import numpy as np

from .tape import capture_commands


def array_copy(value: Any) -> np.ndarray:
    if hasattr(value, "detach"):
        value = value.detach().cpu().numpy()
    elif isinstance(value, (tuple, list)):
        # A few Isaac/PhysX tensor APIs return a Python container of device
        # tensors rather than one batched tensor.  Convert each element before
        # handing the value to NumPy; np.asarray(container) would otherwise
        # try to call __array__ on a CUDA tensor directly.
        value = [array_copy(item) for item in value]
    try:
        return np.asarray(value).copy()
    except Exception as exc:
        raise TypeError(
            f"physics measurement cannot be converted to NumPy: type={type(value)!r}"
        ) from exc


class PolicyInferenceTrace:
    """Observe actual ONNXPolicy-style act calls without an extra inference/draw."""

    def __init__(self, policies: dict[str, Any], *, env_index: int | None = None,
                 batch_count: int | None = None):
        self.frames = {}
        self.counts = {}
        self.active = True
        self.env_index = env_index
        self.batch_count = batch_count
        if env_index is not None and (type(env_index) is not int or env_index < 0):
            raise ValueError("inference environment index must be a nonnegative integer")
        if batch_count is not None and (type(batch_count) is not int or batch_count < 1):
            raise ValueError("inference batch count must be positive")
        if env_index is not None and batch_count is None:
            raise ValueError("batch_count is required for slot inference tracing")
        self._hooks = []
        try:
            for name, policy in policies.items():
                original = policy.act
                owned = vars(policy).get("act")

                def act(inputs, *args, _name=name, _original=original, **kwargs):
                    if not self.active:
                        return _original(inputs, *args, **kwargs)
                    index = self.counts.get(_name, 0)
                    prefix = f"inference/{_name}/{index}"
                    observation = inputs["states"]
                    if self.env_index is not None:
                        if observation.shape[0] != self.batch_count:
                            raise ValueError("slot inference input has an unexpected batch")
                        observation = observation[self.env_index:self.env_index + 1]
                    self.frames[f"{prefix}/observation"] = array_copy(observation)
                    result = _original(inputs, *args, **kwargs)
                    action = result[0]
                    if self.env_index is not None:
                        if action.shape[0] != self.batch_count:
                            raise ValueError("slot inference output has an unexpected batch")
                        action = action[self.env_index:self.env_index + 1]
                    self.frames[f"{prefix}/raw_action"] = array_copy(action)
                    self.counts[_name] = index + 1
                    return result

                policy.act = act
                self._hooks.append((name, policy, owned, act))
        except Exception:
            self.close()
            raise

    def take_frame(self) -> dict[str, np.ndarray]:
        if not self.active:
            return {}
        result = self.frames
        for name, policy, owned, hook in self._hooks:
            if policy.act is not hook:
                raise ValueError(f"policy inference trace was replaced: {name}")
            result[f"inference/{name}/calls"] = np.asarray([self.counts.get(name, 0)], dtype=np.int64)
        self.frames = {}
        self.counts = {}
        return result

    def pause(self) -> None:
        """Stop retaining frames while an outer shared task keeps the hook alive."""
        self.active = False
        self.frames = {}
        self.counts = {}

    def close(self) -> None:
        self.pause()
        for name, policy, owned, hook in reversed(self._hooks):
            if policy.act is hook:
                if owned is None:
                    delattr(policy, "act")
                else:
                    policy.act = owned
        self._hooks.clear()


class SharedPolicyInferenceTrace:
    """Trace one rowwise policy dispatcher without stacking per-slot hooks."""

    def __init__(self, policies: dict[str, Any], batch_count: int):
        import torch

        if type(batch_count) is not int or batch_count < 1:
            raise ValueError("shared inference batch count must be positive")
        self.batch_count = batch_count
        self.frames: dict[int, dict[str, np.ndarray]] = {}
        self.counts: dict[int, dict[str, int]] = {}
        self._slot_index = None
        self.active = True
        self._hooks = []
        try:
            for name, policy in policies.items():
                original = policy.act
                owned = vars(policy).get("act")

                def act(inputs, *args, _name=name, _original=original, **kwargs):
                    indices = (range(self.batch_count) if self._slot_index is None else (self._slot_index,))
                    if set(inputs) != {"states"} or inputs["states"].shape[0] != len(indices):
                        raise ValueError("shared ONNX inputs must have exactly one row per slot")
                    outputs = []
                    for row_index, index in enumerate(indices):
                        row = inputs["states"][row_index:row_index + 1]
                        result = _original({"states": row}, *args, **kwargs)
                        if len(result) != 2 or result[1] is not None or result[0].shape[0] != 1:
                            raise ValueError("shared ONNX inference returned unsupported state/batch")
                        if self.active and index in self.frames:
                            call_index = self.counts[index].get(_name, 0)
                            prefix = f"inference/{_name}/{call_index}"
                            self.frames[index][f"{prefix}/observation"] = array_copy(row)
                            self.frames[index][f"{prefix}/raw_action"] = array_copy(result[0])
                            self.counts[index][_name] = call_index + 1
                        outputs.append(result[0])
                    action = (torch.cat(outputs, dim=0) if isinstance(outputs[0], torch.Tensor)
                              else np.concatenate(outputs, axis=0))
                    return action, None

                policy.act = act
                self._hooks.append((name, policy, owned, act))
        except Exception:
            self.close()
            raise

    @contextmanager
    def slot(self, index: int):
        if type(index) is not int or not 0 <= index < self.batch_count:
            raise ValueError("shared inference slot index is out of range")
        if self._slot_index is not None:
            raise RuntimeError("shared inference slot context is already active")
        self._slot_index = index
        try:
            yield
        finally:
            self._slot_index = None

    def register(self, index: int):
        if type(index) is not int or not 0 <= index < self.batch_count:
            raise ValueError("shared inference slot index is out of range")
        if index in self.frames:
            raise ValueError(f"shared inference slot {index} is already registered")
        self.frames[index] = {}
        self.counts[index] = {}
        return _SharedPolicyInferenceSlotTrace(self, index)

    def take_frame(self, index: int) -> dict[str, np.ndarray]:
        if not self.active:
            return {}
        if index not in self.frames:
            raise ValueError(f"shared inference slot {index} is not registered")
        result = self.frames[index]
        for name, policy, owned, hook in self._hooks:
            if policy.act is not hook:
                raise ValueError(f"policy inference trace was replaced: {name}")
            result[f"inference/{name}/calls"] = np.asarray([self.counts[index].get(name, 0)], dtype=np.int64)
        self.frames[index] = {}
        self.counts[index] = {}
        return result

    def pause(self, index: int) -> None:
        if index in self.frames:
            self.frames[index] = {}
            self.counts[index] = {}

    def close(self) -> None:
        self.active = False
        self.frames.clear()
        self.counts.clear()
        for name, policy, owned, hook in reversed(self._hooks):
            if policy.act is hook:
                if owned is None:
                    del policy.act
                else:
                    policy.act = owned
        self._hooks.clear()


class _SharedPolicyInferenceSlotTrace:
    def __init__(self, owner: SharedPolicyInferenceTrace, index: int):
        self.owner = owner
        self.index = index

    def take_frame(self) -> dict[str, np.ndarray]:
        return self.owner.take_frame(self.index)

    def pause(self) -> None:
        self.owner.pause(self.index)

    def close(self) -> None:
        return None


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
                    import traceback
                    self.error = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
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


class SharedPhysicsStepTrace:
    """Capture every slot after one shared simulation step.

    The simulation owns one ``step`` method, so installing one trace per slot
    would stack wrappers and produce duplicate or reordered measurements.  This
    dispatcher patches the method once and gives each registered slot its own
    decimated frame queue.
    """

    def __init__(self, simulation: Any, decimation: int):
        self.simulation = simulation
        self.decimation = int(decimation)
        if self.decimation < 1:
            raise ValueError("shared physics trace decimation must be positive")
        self.active = False
        self.error = None
        self._readers: dict[int, Callable[[], dict[str, np.ndarray]]] = {}
        self._frames: dict[int, list[dict[str, np.ndarray]]] = {}
        self._active_slots: set[int] = set()
        self._original_step = simulation.step
        self._owned_step = vars(simulation).get("step")

        def step(*args: Any, **kwargs: Any) -> Any:
            result = self._original_step(*args, **kwargs)
            if self.active and self.error is None:
                try:
                    for index in tuple(self._active_slots):
                        reader = self._readers[index]
                        frame = {name: array_copy(value) for name, value in reader().items()}
                        if not frame or any(not np.all(np.isfinite(value)) for value in frame.values()):
                            raise ValueError(f"empty/non-finite physics measurement for slot {index}")
                        self._frames[index].append(frame)
                        if len(self._frames[index]) > self.decimation:
                            raise ValueError(f"unexpected extra physics step for slot {index}")
                except Exception as exc:
                    self.error = str(exc)
            return result

        self._step = step
        simulation.step = step

    def register(self, index: int, reader: Callable[[], dict[str, np.ndarray]]):
        if type(index) is not int or index < 0:
            raise ValueError("shared physics trace slot index must be nonnegative")
        if index in self._readers:
            raise ValueError(f"shared physics trace slot {index} is already registered")
        self._readers[index] = reader
        self._frames[index] = []
        self._active_slots.add(index)
        return _SharedPhysicsSlotTrace(self, index)

    def begin(self) -> None:
        self._frames = {index: [] for index in self._readers}
        self._active_slots = set(self._readers)
        self.error = None
        self.active = True

    def pause(self, index: int) -> None:
        if index not in self._readers:
            raise ValueError(f"shared physics trace slot {index} is not registered")
        self._active_slots.discard(index)
        self._frames[index].clear()

    def take_frame(self, index: int) -> dict[str, np.ndarray]:
        if self.error is not None:
            raise ValueError(f"shared physics trace failed: {self.error}")
        frames = self._frames.get(index)
        if frames is None:
            raise ValueError(f"shared physics trace slot {index} is not registered")
        if len(frames) != self.decimation:
            raise ValueError(f"physics substep count mismatch: {len(frames)} != {self.decimation}")
        result = {f"substep/{substep}/{name}": value
                  for substep, frame in enumerate(frames) for name, value in frame.items()}
        frames.clear()
        return result

    def close(self) -> None:
        self.active = False
        self._active_slots.clear()
        self._frames = {index: [] for index in self._readers}
        if self.simulation.step is self._step:
            if self._owned_step is None:
                delattr(self.simulation, "step")
            else:
                self.simulation.step = self._owned_step


class _SharedPhysicsSlotTrace:
    def __init__(self, owner: SharedPhysicsStepTrace, index: int):
        self.owner = owner
        self.index = index

    def begin(self) -> None:
        self.owner.begin()

    def take_frame(self) -> dict[str, np.ndarray]:
        return self.owner.take_frame(self.index)

    def pause(self) -> None:
        self.owner.pause(self.index)

    def close(self) -> None:
        return None


class PhysxMeasurementReader:
    """Measure active bodies, link reactions, normal contacts and friction patches."""

    @staticmethod
    def prepare_contact_reporting(base_env: Any, *, only_missing: bool = False) -> bool:
        import omni.usd
        from omni.physx import get_physx_simulation_interface
        from pxr import PhysxSchema, UsdPhysics

        stage = omni.usd.get_context().get_stage()
        owners = set()
        spatial = getattr(getattr(base_env, 'scene', None), 'replay_spatial_slots', False)
        if spatial:
            from pxr import Usd
            prims = stage.Traverse(Usd.TraverseInstanceProxies())
        else:
            prims = stage.Traverse()
        for prim in prims:
            if not prim.HasAPI(UsdPhysics.CollisionAPI) or UsdPhysics.CollisionAPI(prim).GetCollisionEnabledAttr().Get() is False:
                continue
            owner = prim
            while owner.IsValid() and not owner.HasAPI(UsdPhysics.RigidBodyAPI):
                owner = owner.GetParent()
            if owner.IsValid() and UsdPhysics.RigidBodyAPI(owner).GetRigidBodyEnabledAttr().Get() is not False:
                owners.add(str(owner.GetPath()))
        changed = False
        for path in sorted(owners):
            prim = stage.GetPrimAtPath(path)
            if ((only_missing or spatial)
                    and prim.HasAPI(PhysxSchema.PhysxContactReportAPI)
                    and PhysxSchema.PhysxContactReportAPI(prim).GetThresholdAttr().Get() == 0.0):
                continue
            PhysxSchema.PhysxContactReportAPI.Apply(prim).CreateThresholdAttr().Set(0.0)
            changed = True
        if only_missing and not changed:
            return False
        get_physx_simulation_interface().flush_changes()
        if not only_missing:
            # Initial measurement setup follows the established single-task
            # baseline. During a live topology change, forward() recomputes
            # every articulation's link poses, including other task scenes.
            # Reporters only need USD changes flushed; the next normal solve
            # initializes their measurements without perturbing live bodies.
            base_env.sim.forward()
        return True

    def __init__(self, base_env: Any, *, root_path: str | None = None, env_index: int | None = None,
                 prepare_contact_reports: bool = True, common_static=None, canonical_path=None):
        import carb.settings
        import omni.physics.tensors
        import omni.usd
        from omni.physx import get_physx_simulation_interface
        from pxr import PhysxSchema, Usd, UsdPhysics

        self.base_env = base_env
        self.env_index = env_index
        if env_index is not None and (isinstance(env_index, bool) or not isinstance(env_index, int)
                                      or not 0 <= env_index < base_env.num_envs):
            raise ValueError("measurement environment index is out of range")
        if root_path is not None and (not root_path.startswith("/") or root_path.endswith("/")):
            raise ValueError("measurement root must be an absolute prim path without a trailing slash")
        self.dt = float(base_env.physics_dt)
        stage = omni.usd.get_context().get_stage()
        common_paths = common_static.collider_paths if common_static is not None else frozenset()
        if common_paths and (root_path is None or env_index is None or canonical_path is None):
            raise ValueError("common static measurements require a scoped reader and canonical column order")
        if common_static is not None:
            common_static.validate_immutable()
        paths = []
        contact_paths = set()
        filter_paths = set()
        if getattr(base_env.scene, 'replay_spatial_slots', False) and root_path and not common_paths:
            prims = Usd.PrimRange(stage.GetPrimAtPath(root_path), Usd.TraverseInstanceProxies())
        else:
            prims = (stage.Traverse(Usd.TraverseInstanceProxies())
                     if getattr(base_env.scene, 'replay_spatial_slots', False) else stage.Traverse())
        for prim in prims:
            path = str(prim.GetPath())
            if path in common_paths:
                if (not prim.HasAPI(UsdPhysics.CollisionAPI)
                        or prim.HasAPI(UsdPhysics.RigidBodyAPI)
                        or UsdPhysics.CollisionAPI(prim).GetCollisionEnabledAttr().Get() is False):
                    raise ValueError(f"common static collider coverage changed: {path}")
                filter_paths.add(path)
                continue
            if root_path is not None and path != root_path and not path.startswith(root_path + "/"):
                continue
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
        if not common_paths <= filter_paths:
            raise ValueError("registered common colliders are missing from the live stage")
        if prepare_contact_reports:
            for path in sorted(contact_paths):
                PhysxSchema.PhysxContactReportAPI.Apply(stage.GetPrimAtPath(path)).CreateThresholdAttr().Set(0.0)
            get_physx_simulation_interface().flush_changes()
            base_env.sim.forward()
        elif any(not stage.GetPrimAtPath(path).HasAPI(PhysxSchema.PhysxContactReportAPI)
                 or PhysxSchema.PhysxContactReportAPI(stage.GetPrimAtPath(path)).GetThresholdAttr().Get() != 0.0
                 for path in contact_paths):
            raise ValueError("shared contact reports must be prepared once before constructing readers")
        # GPU PhysX pipelines reject the Numpy frontend. Keep measurements
        # lossless by reading through Torch on CUDA and copying to NumPy only
        # at the journal boundary; CPU retains the historical Numpy path.
        sim_device = getattr(base_env, "device", None)
        if sim_device is None:
            sim_device = getattr(getattr(base_env, "cfg", None), "sim", None)
            sim_device = getattr(sim_device, "device", "cpu")
        frontend = "torch" if str(sim_device).startswith("cuda") else "numpy"
        self.simulation_view = omni.physics.tensors.create_simulation_view(frontend)
        self.bodies = self.simulation_view.create_rigid_body_view(sorted(paths, key=canonical_path))
        self.filter_paths = sorted(filter_paths, key=canonical_path)
        self.contacts = self.simulation_view.create_rigid_contact_view(
            sorted(contact_paths, key=canonical_path), filter_patterns=[self.filter_paths for path in contact_paths],
            max_contact_data_count=16384,
        )
        if self.contacts is None or sorted(self.contacts.sensor_paths) != sorted(contact_paths):
            actual = self.contacts.sensor_paths if self.contacts is not None else []
            raise ValueError(f"incomplete PhysX contact-report coverage: expected={sorted(contact_paths)}, actual={actual}")
        if self.contacts.filter_count != len(filter_paths):
            raise ValueError("incomplete PhysX collision filter coverage")
        self.paths = tuple(self.bodies.prim_paths)
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
                result[f"{name}/{channel}"] = self.environment_array(getattr(view, f"get_{channel}")())
        for name, action in getattr(self.base_env, "_last_computed_actions", {}).items():
            presence = getattr(self.base_env, "_shared_action_presence", None)
            if presence is not None and self.env_index is not None and self.env_index not in presence.get(name, ()):
                continue
            result[f"policy/{name}/action"] = self.environment_array(action)
        for name, command in capture_commands(self.base_env).items():
            result[f"command/{name}"] = self.environment_array(command)
        self._patches(result, "contacts/normal", self.contacts.get_contact_data(self.dt),
                      ("force_N", "point_m", "normal", "separation_m"))
        self._patches(result, "contacts/friction", self.contacts.get_friction_data(self.dt),
                      ("force_N", "point_m"))
        stream = getattr(self, "contact_stream", None)
        if stream is not None:
            result.update(stream.measurements(self, result))
        return result

    def environment_array(self, value: Any) -> np.ndarray:
        index = getattr(self, "env_index", None)
        if index is not None:
            if value.shape[0] != self.base_env.num_envs:
                raise ValueError("physical measurement does not have the environment batch axis")
            value = value[index:index + 1]
        return array_copy(value)
