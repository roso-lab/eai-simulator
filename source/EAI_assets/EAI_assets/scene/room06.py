"""Hash-bound room_06 benchmark scene for Isaac Lab."""

from isaaclab.sim.spawners.from_files import UsdFileCfg
from isaaclab.sim.spawners.from_files import from_files as _from_files
from isaaclab.utils import configclass

from EAI_assets.room06_asset import resolve_room06_scene
from EAI_assets.scene.room06_runtime import build_room06_raycast_proxy


_ROOM06_ASSET = resolve_room06_scene()


def _spawn_room06(prim_path, cfg, translation=None, orientation=None, **kwargs):
    prim = _from_files.spawn_from_usd(
        prim_path, cfg, translation, orientation, **kwargs
    )
    import omni.usd
    from isaaclab.sim.utils import find_matching_prim_paths
    from pxr import Usd

    stage = omni.usd.get_context().get_stage()
    parent_regex = prim_path.rpartition("/")[0]
    parents = find_matching_prim_paths(parent_regex)
    if not parents:
        raise RuntimeError(f"room_06 spawn matched no environment parents: {parent_regex}")
    room_name = prim_path.rpartition("/")[2]
    for parent in parents:
        room_root = f"{str(parent).rstrip('/')}/{room_name}"
        root = stage.GetPrimAtPath(room_root)
        if not root or not root.IsValid():
            raise RuntimeError(f"room_06 composed root is missing: {room_root}")
        embedded_physics = []
        for candidate in Usd.PrimRange(root, Usd.TraverseInstanceProxies()):
            if candidate.GetTypeName() == "PhysicsScene":
                candidate.SetActive(False)
                embedded_physics.append(str(candidate.GetPath()))
        report = build_room06_raycast_proxy(stage, room_root)
        print(
            f"[Room06Scene] Built raycast proxy {report.prim_path}: "
            f"{report.source_mesh_count} source meshes, "
            f"{report.point_count} points, {report.triangle_count} triangles."
        )
        if embedded_physics:
            print(
                "[Room06Scene] Deactivated embedded PhysicsScene prims: "
                + ", ".join(embedded_physics)
            )
    return prim


@configclass
class Room06Cfg(UsdFileCfg):
    usd_path: str = str(_ROOM06_ASSET.path)
    func = _spawn_room06


ROOM06_CFG = Room06Cfg()

__all__ = ["ROOM06_CFG", "Room06Cfg"]
