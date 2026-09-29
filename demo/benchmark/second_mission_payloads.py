"""Cuboid bodies for the house parcel and the factory extinguisher.

The formal warehouse compiler does not cover these scenes. Each measured
object is a rigid cuboid whose mass and support socket match the public
properties. The button and the sign-off marker stay kinematic. The parcel
and the extinguisher are dynamic so a fixed joint can carry them.
"""

from __future__ import annotations

from typing import Any


def spawn_second_mission_cuboid(prim_path, cfg, translation=None, orientation=None, **kwargs):
    """Spawn one cuboid, then author the public support socket when it has one."""
    from pxr import Gf, UsdGeom, UsdPhysics

    from isaaclab.sim.spawners.shapes import shapes

    prim = shapes.spawn_cuboid(prim_path, cfg, translation, orientation, **kwargs)
    root = prim if hasattr(prim, "IsValid") else None
    if root is None or not root.IsValid():
        raise RuntimeError(f"payload prim was not created: {prim_path}")
    from demo.benchmark.goal_driven_fixtures import object_physical_properties

    public_id = root.GetName()
    props = object_physical_properties(public_id)
    UsdPhysics.MassAPI.Apply(root).CreateMassAttr().Set(float(props["mass_kg"]))
    for point in props["grasp_points"]:
        grasp = UsdGeom.Xform.Define(root.GetStage(), f"{root.GetPath()}/GraspPoints/{point['id']}")
        grasp.AddTranslateOp().Set(Gf.Vec3d(*point["position_m"]))
        grasp.GetPrim().SetCustomDataByKey("goal_interface", point["interface"])
    return prim


def _payload_cfg(name: str, position_m: tuple[float, float, float], *,
                 size_m: tuple[float, float, float], mass_kg: float, kinematic: bool):
    import isaaclab.sim as sim_utils
    from isaaclab.assets import RigidObjectCfg
    from isaaclab.sim.utils import clone

    color = (0.72, 0.55, 0.28) if not kinematic else (0.25, 0.45, 0.75)
    return RigidObjectCfg(
        prim_path=f"{{ENV_REGEX_NS}}/{name}",
        spawn=sim_utils.CuboidCfg(
            size=size_m,
            func=clone(spawn_second_mission_cuboid),
            rigid_props=sim_utils.RigidBodyPropertiesCfg(
                kinematic_enabled=kinematic,
                disable_gravity=kinematic,
            ),
            mass_props=sim_utils.MassPropertiesCfg(mass=mass_kg),
            collision_props=sim_utils.CollisionPropertiesCfg(collision_enabled=True),
            visual_material=sim_utils.PreviewSurfaceCfg(diffuse_color=color),
        ),
        init_state=RigidObjectCfg.InitialStateCfg(pos=position_m),
    )


def attach_second_mission_payloads(env_cfg, public_task) -> None:
    """Add the measured cuboids for a house delivery or factory fire episode."""
    from demo.benchmark.goal_driven_fixtures import object_physical_properties
    from demo.benchmark.second_mission_deployment import (
        _KINEMATIC_PAYLOADS, _MEASURED_PAYLOADS, payload_spawn_positions,
    )

    names = _MEASURED_PAYLOADS.get(public_task.get("task_id"))
    if not names:
        return
    positions = payload_spawn_positions(public_task)
    for name in names:
        if hasattr(env_cfg.scene, name):
            raise RuntimeError(f"scene cfg already has {name}")
        props = object_physical_properties(name)
        size = tuple(float(value) for value in props["size_m"])
        setattr(env_cfg.scene, name, _payload_cfg(
            name, positions[name], size_m=size, mass_kg=float(props["mass_kg"]),
            kinematic=name in _KINEMATIC_PAYLOADS))


def bind_second_mission_payloads(deployment: dict[str, Any]) -> None:
    """Name the live prims LiveBodies and the object reader will sample."""
    from copy import deepcopy

    from demo.benchmark.goal_driven_fixtures import object_physical_properties
    from demo.benchmark.second_mission_deployment import _MEASURED_PAYLOADS

    names = _MEASURED_PAYLOADS.get(deployment.get("task_id"))
    if not names:
        return
    for row in deployment["robot_instances"]:
        row["prim_path"] = f"/World/envs/env_0/{row['runtime_id']}"
    deployment["object_instances"] = [
        {
            "public_id": public_id,
            "runtime_id": public_id,
            "prim_path": f"/World/envs/env_0/{public_id}",
            "physical_properties": deepcopy(object_physical_properties(public_id)),
        }
        for public_id in names
    ]
