"""Pepper collision and spawn helpers used by the local PickPlace adapter.

There is deliberately no route, destination, or transport state machine in
this module.  The helpers only author collision filtering before Isaac Lab
creates tensor views; arm motion and object ownership belong to the Pepper
adapter and controller.
"""

from __future__ import annotations

from typing import Any


def spawn_transport_robot_with_collision_filters(
    prim_path: str,
    cfg: Any,
    translation: tuple[float, float, float] | None = None,
    orientation: tuple[float, float, float, float] | None = None,
    **kwargs: Any,
) -> Any:
    """Spawn a robot and author robot-to-payload filters before tensor views."""
    import isaaclab.sim as sim_utils
    from pxr import Sdf, Usd, UsdPhysics

    spawned = sim_utils.spawn_from_usd(
        prim_path, cfg, translation=translation, orientation=orientation, **kwargs,
    )
    stage = spawned.GetStage()
    robot_paths = sim_utils.find_matching_prim_paths(prim_path, stage=stage) or [str(spawned.GetPath())]
    for robot_path in robot_paths:
        robot_root = stage.GetPrimAtPath(robot_path)
        env_root = robot_path.rsplit("/", 1)[0]
        # The host may use either the default Pepper box name or an explicit
        # payload.  Local pick/place scenes may also provide a physical
        # support under the object.  Filter only robot↔payload/support pairs;
        # payload↔support contact remains physical after release.
        targets = [Sdf.Path(f"{env_root}/{name}") for name in (
            "g1_grasp_box", "payload", "pick_place_support",
            "box_source_table", "box_destination_table",
        )]
        for body in Usd.PrimRange(robot_root):
            if not body.HasAPI(UsdPhysics.RigidBodyAPI):
                continue
            relation = UsdPhysics.FilteredPairsAPI.Apply(body).CreateFilteredPairsRel()
            for target in targets:
                relation.AddTarget(target)
    return spawned


def install_robot_box_collision_filter(
    env: Any,
    robot_name: str,
    cube_prim_name: str,
    support_prim_names: tuple[str, ...] = (),
) -> None:
    """Filter robot-to-held-object/support contacts while preserving payload support."""
    from pxr import Sdf, UsdGeom, UsdPhysics
    import omni.usd

    robot = env.scene.articulations[robot_name]
    box = env.scene.rigid_objects[cube_prim_name]
    robot_physics_path = str(robot.root_physx_view.prim_paths[0])
    marker = f"/{robot_name}"
    marker_index = robot_physics_path.find(marker)
    robot_root_path = robot_physics_path[: marker_index + len(marker)] if marker_index >= 0 else robot_physics_path
    box_root_path = str(box.root_physx_view.prim_paths[0])
    stage = omni.usd.get_context().get_stage()
    scope_path = "/World/pick_place_collision_filters"
    if not stage.GetPrimAtPath(scope_path).IsValid():
        UsdGeom.Scope.Define(stage, scope_path)
    safe_name = "".join(character if character.isalnum() else "_" for character in robot_name)
    robot_group_path = f"{scope_path}/{safe_name}_robot"
    payload_group_path = f"{scope_path}/{safe_name}_payload_and_supports"
    robot_group = UsdPhysics.CollisionGroup.Define(stage, robot_group_path)
    payload_group = UsdPhysics.CollisionGroup.Define(stage, payload_group_path)
    env_root_path = box_root_path.rsplit("/", 1)[0]
    opposite_paths = [box_root_path]
    opposite_paths.extend(f"{env_root_path}/{name}" for name in support_prim_names)
    for group, included_paths in ((robot_group, [robot_root_path]), (payload_group, opposite_paths)):
        collection = Usd.CollectionAPI.Apply(group.GetPrim(), "colliders")
        collection.CreateExpansionRuleAttr().Set("expandPrims")
        collection.CreateIncludesRel().SetTargets([Sdf.Path(path) for path in included_paths])
    robot_group.GetFilteredGroupsRel().SetTargets([Sdf.Path(payload_group_path)])
    payload_group.GetFilteredGroupsRel().SetTargets([Sdf.Path(robot_group_path)])


__all__ = ["spawn_transport_robot_with_collision_filters", "install_robot_box_collision_filter"]
