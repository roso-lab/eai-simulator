"""Runtime composition helpers owned by the ``room_06`` scene."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


ROOM06_RAYCAST_PROXY_NAME = "EaiRaycastTerrain"
ROOM06_RAYCAST_PROXY_PRIM_PATH = f"/World/Room06/{ROOM06_RAYCAST_PROXY_NAME}"


@dataclass(frozen=True)
class Room06RaycastProxyReport:
    prim_path: str
    point_count: int
    triangle_count: int
    source_mesh_count: int


def _validated_topology(mesh: Any, path: str) -> tuple[list[Any], list[int], list[int]]:
    from pxr import UsdGeom, Vt

    points = list(mesh.GetPointsAttr().Get() or [])
    counts = [int(value) for value in (mesh.GetFaceVertexCountsAttr().Get() or [])]
    indices = [int(value) for value in (mesh.GetFaceVertexIndicesAttr().Get() or [])]
    valid, reason = UsdGeom.Mesh.ValidateTopology(
        Vt.IntArray(indices), Vt.IntArray(counts), len(points)
    )
    if not valid:
        raise ValueError(f"invalid room_06 collision mesh topology at {path}: {reason}")
    if not points or not counts or any(count < 3 for count in counts):
        raise ValueError(f"room_06 collision mesh is empty or degenerate: {path}")
    return points, counts, indices


def _collision_meshes(root: Any) -> list[Any]:
    from pxr import Usd, UsdGeom, UsdPhysics

    result = []
    for prim in Usd.PrimRange(root, Usd.TraverseInstanceProxies()):
        if not prim.IsA(UsdGeom.Mesh) or prim.GetName() == ROOM06_RAYCAST_PROXY_NAME:
            continue
        collision = UsdPhysics.CollisionAPI(prim)
        enabled = collision.GetCollisionEnabledAttr().Get()
        if prim.HasAPI(UsdPhysics.CollisionAPI) and enabled is not False:
            result.append(prim)
    return result


def build_room06_raycast_proxy(
    stage: Any, room_root_path: str
) -> Room06RaycastProxyReport:
    """Merge scene-owned collision meshes for Isaac Lab's single-mesh RayCaster."""

    from pxr import Gf, Usd, UsdGeom, Vt

    root = stage.GetPrimAtPath(room_root_path)
    if not root or not root.IsValid():
        raise ValueError(f"room_06 root does not exist: {room_root_path}")
    source_prims = _collision_meshes(root)
    if not source_prims:
        raise ValueError(f"room_06 has no collision meshes below {room_root_path}")

    proxy_path = f"{room_root_path.rstrip('/')}/{ROOM06_RAYCAST_PROXY_NAME}"
    proxy = UsdGeom.Mesh.Define(stage, proxy_path)
    cache = UsdGeom.XformCache(Usd.TimeCode.Default())
    inverse = cache.GetLocalToWorldTransform(proxy.GetPrim()).GetInverse()
    merged_points: list[Any] = []
    triangle_indices: list[int] = []

    for source_prim in source_prims:
        source_path = str(source_prim.GetPath())
        points, counts, indices = _validated_topology(
            UsdGeom.Mesh(source_prim), source_path
        )
        source_to_world = cache.GetLocalToWorldTransform(source_prim)
        offset = len(merged_points)
        for point in points:
            world = source_to_world.Transform(Gf.Vec3d(point))
            merged_points.append(Gf.Vec3f(inverse.Transform(world)))
        cursor = 0
        for count in counts:
            face = indices[cursor : cursor + count]
            cursor += count
            for index in range(1, count - 1):
                triangle_indices.extend(
                    (offset + face[0], offset + face[index], offset + face[index + 1])
                )

    triangle_counts = [3] * (len(triangle_indices) // 3)
    valid, reason = UsdGeom.Mesh.ValidateTopology(
        Vt.IntArray(triangle_indices),
        Vt.IntArray(triangle_counts),
        len(merged_points),
    )
    if not valid or not triangle_indices:
        raise ValueError(f"generated room_06 raycast proxy is invalid: {reason}")
    proxy.CreatePointsAttr().Set(merged_points)
    proxy.CreateFaceVertexCountsAttr().Set(triangle_counts)
    proxy.CreateFaceVertexIndicesAttr().Set(triangle_indices)
    proxy.CreateSubdivisionSchemeAttr().Set(UsdGeom.Tokens.none)
    proxy.CreateVisibilityAttr().Set(UsdGeom.Tokens.invisible)
    return Room06RaycastProxyReport(
        prim_path=proxy_path,
        point_count=len(merged_points),
        triangle_count=len(triangle_counts),
        source_mesh_count=len(source_prims),
    )


__all__ = [
    "ROOM06_RAYCAST_PROXY_NAME",
    "ROOM06_RAYCAST_PROXY_PRIM_PATH",
    "Room06RaycastProxyReport",
    "build_room06_raycast_proxy",
]
