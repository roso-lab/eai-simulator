"""USD runtime objects and rigid bodies not registered in InteractiveScene."""

from __future__ import annotations

from typing import Any, Iterable, Mapping

import numpy as np


class StageStateComponent:
    name = "physics.usd_runtime"
    version = 2
    priority = 10

    def __init__(self, session: Any, *, runtime_paths: Iterable[str] = (), tolerance: float = 1e-5):
        from pxr import UsdPhysics
        import omni.usd

        self.session = session
        self.stage = omni.usd.get_context().get_stage()
        self.runtime_paths = tuple(runtime_paths)
        self.tolerance = tolerance
        self.managed_paths = set()
        for collection in ("articulations", "rigid_objects"):
            for entity in getattr(session.base_env.scene, collection, {}).values():
                configured = str(entity.cfg.prim_path)
                configured = configured.replace("/World/{ENV_REGEX_NS}", "/World/envs/env_0")
                configured = configured.replace("{ENV_REGEX_NS}", "/World/envs/env_0")
                self.managed_paths.add(configured.replace("env_.*", "env_0").replace("env_*", "env_0"))
        self.rigid_api = UsdPhysics.RigidBodyAPI

    def _managed(self, path: str) -> bool:
        return any(path == root or path.startswith(root + "/") for root in self.managed_paths)

    def _rigid_paths(self, *, include_disabled: bool = False) -> list[str]:
        return sorted(
            str(prim.GetPath()) for prim in self.stage.Traverse()
            if prim.HasAPI(self.rigid_api) and not self._managed(str(prim.GetPath()))
            and (include_disabled or self.rigid_api(prim).GetRigidBodyEnabledAttr().Get() is not False)
        )

    def _rigid_view(self, paths: list[str]) -> Any:
        import omni.physics.tensors

        simulation_view = omni.physics.tensors.create_simulation_view("numpy")
        view = simulation_view.create_rigid_body_view(paths)
        if view is None or sorted(view.prim_paths) != paths:
            raise ValueError("non-scene rigid bodies are not accessible through PhysX: " + ", ".join(paths))
        return simulation_view, view

    def read_trajectory_state(self) -> dict[str, np.ndarray]:
        paths = self._rigid_paths()
        if not paths:
            return {}
        simulation_view, view = self._rigid_view(paths)
        transforms = view.get_transforms().copy()
        velocities = view.get_velocities().copy()
        result = {}
        for index, path in enumerate(view.prim_paths):
            result[f"usd:{path}/pose"] = transforms[index].copy()
            result[f"usd:{path}/velocity"] = velocities[index].copy()
        return result

    def capture_state(self) -> Mapping[str, Any]:
        from pxr import Sdf

        attributes = Sdf.Layer.CreateAnonymous("checkpoint_attributes.usda")
        for prim in self.stage.Traverse():
            path = str(prim.GetPath())
            if not path.startswith("/World/"):
                continue
            for attribute in prim.GetAttributes():
                name = attribute.GetName()
                physical = name.startswith(("physics:", "physx", "drive:"))
                visual = name in {"visibility", "inputs:color", "inputs:intensity"} or name.startswith("xformOp")
                if not physical and not (visual and not self._managed(path)):
                    continue
                value = attribute.Get()
                if value is None:
                    continue
                specification = Sdf.CreatePrimInLayer(attributes, path)
                copied = Sdf.AttributeSpec(specification, name, attribute.GetTypeName(), attribute.GetVariability())
                copied.default = value
        runtime = Sdf.Layer.CreateAnonymous("checkpoint_runtime.usda")
        present = [path for path in self.runtime_paths if self.stage.GetPrimAtPath(path).IsValid()]
        if present:
            flattened = self.stage.Flatten()
            for path in present:
                parent = Sdf.Path(path).GetParentPath()
                Sdf.CreatePrimInLayer(runtime, parent)
                if not Sdf.CopySpec(flattened, path, runtime, path):
                    raise ValueError(f"cannot snapshot runtime USD object {path}")
        paths = self._rigid_paths()
        rigid = {}
        if paths:
            simulation_view, view = self._rigid_view(paths)
            rigid = {
                "paths": list(view.prim_paths),
                "transforms": view.get_transforms().copy(),
                "velocities": view.get_velocities().copy(),
            }
        return {
            "attributes": attributes.ExportToString(),
            "runtime": runtime.ExportToString(),
            "present": present,
            "rigid": rigid,
            "rigid_topology": self._rigid_paths(include_disabled=True),
        }

    def validate_state(self, state: Mapping[str, Any]) -> None:
        from pxr import Sdf

        for key in ("attributes", "runtime"):
            layer = Sdf.Layer.CreateAnonymous("validate.usda")
            if not layer.ImportFromString(state[key]):
                raise ValueError(f"invalid USD checkpoint {key}")
        if not set(state["present"]).issubset(self.runtime_paths):
            raise ValueError("unexpected runtime USD object in checkpoint")
        expected = set(state["rigid_topology"])
        if not set(state["rigid"].get("paths", ())).issubset(expected):
            raise ValueError("saved active rigid bodies are missing from the topology snapshot")
        current = set(self._rigid_paths(include_disabled=True))
        changed = expected.symmetric_difference(current)
        unsupported = sorted(
            path for path in changed
            if not any(path == root or path.startswith(root + "/") for root in self.runtime_paths)
        )
        if unsupported:
            raise ValueError("unmanaged physics topology changed outside the registered runtime objects: " + ", ".join(unsupported))

    def restore_state(self, state: Mapping[str, Any]) -> None:
        from omni.physx import get_physx_simulation_interface
        from pxr import Sdf, Usd

        runtime = Sdf.Layer.CreateAnonymous("restore_runtime.usda")
        runtime.ImportFromString(state["runtime"])
        target_layer = self.stage.GetEditTarget().GetLayer()
        for path in self.runtime_paths:
            if path in state["present"]:
                if not self.stage.GetPrimAtPath(path).IsValid():
                    Sdf.CreatePrimInLayer(target_layer, Sdf.Path(path).GetParentPath())
                    Sdf.CopySpec(runtime, path, target_layer, path)
            elif self.stage.GetPrimAtPath(path).IsValid():
                self.stage.RemovePrim(path)
        attributes = Sdf.Layer.CreateAnonymous("restore_attributes.usda")
        attributes.ImportFromString(state["attributes"])
        saved = Usd.Stage.Open(attributes)
        for prim in saved.TraverseAll():
            live = self.stage.GetPrimAtPath(prim.GetPath())
            if not live.IsValid():
                raise ValueError(f"checkpoint USD object is missing: {prim.GetPath()}")
            for attribute in prim.GetAttributes():
                target = live.GetAttribute(attribute.GetName())
                value = attribute.Get()
                if not target:
                    target = live.CreateAttribute(attribute.GetName(), attribute.GetTypeName())
                if target.Get() != value:
                    target.Set(value)
        get_physx_simulation_interface().flush_changes()
        self.session.base_env.sim.forward()
        rigid = state["rigid"]
        if rigid:
            paths = list(rigid["paths"])
            simulation_view, view = self._rigid_view(sorted(paths))
            order = [paths.index(path) for path in view.prim_paths]
            transforms = np.asarray(rigid["transforms"])[order].copy()
            velocities = np.asarray(rigid["velocities"])[order].copy()
            indices = np.arange(len(paths), dtype=np.uint32)
            view.set_transforms(transforms, indices)
            view.set_velocities(velocities, indices)
            for expected, actual in ((transforms, view.get_transforms()), (velocities, view.get_velocities())):
                if not np.all(np.isfinite(actual)) or not np.allclose(expected, actual, rtol=0, atol=self.tolerance):
                    raise RuntimeError("non-scene rigid body restore exceeded tolerance")


class SleepStateComponent:
    """Restore sleep flags after pose, velocity and actuator writes have finished."""

    name = "physics.sleep"
    version = 1
    priority = 80

    def __init__(self, stage_component: StageStateComponent):
        from omni.physx import get_physx_simulation_interface
        from pxr import PhysicsSchemaTools, Sdf, UsdUtils

        self.stage_component = stage_component
        self.interface = get_physx_simulation_interface()
        self.stage_id = UsdUtils.StageCache.Get().GetId(stage_component.stage).ToLongInt()
        self.path_id = lambda path: PhysicsSchemaTools.sdfPathToInt(Sdf.Path(path))

    def capture_state(self) -> Mapping[str, Any]:
        paths = set(self.stage_component._rigid_paths())
        for collection in ("articulations", "rigid_objects"):
            for entity in getattr(self.stage_component.session.base_env.scene, collection, {}).values():
                paths.update(entity.root_physx_view.prim_paths)
        return {path: bool(self.interface.is_sleeping(self.stage_id, self.path_id(path))) for path in sorted(paths)}

    def validate_state(self, state: Mapping[str, Any]) -> None:
        for path, sleeping in state.items():
            if not isinstance(path, str) or not path.startswith("/World/") or not isinstance(sleeping, bool):
                raise ValueError("invalid physics sleep snapshot")

    def restore_state(self, state: Mapping[str, Any]) -> None:
        for path, sleeping in state.items():
            identifier = self.path_id(path)
            current = self.interface.is_sleeping(self.stage_id, identifier)
            if bool(current) != sleeping:
                setter = self.interface.put_to_sleep if sleeping else self.interface.wake_up
                setter(self.stage_id, identifier)
            if bool(self.interface.is_sleeping(self.stage_id, identifier)) != sleeping:
                raise RuntimeError(f"physics sleep read-back failed: {path}")
