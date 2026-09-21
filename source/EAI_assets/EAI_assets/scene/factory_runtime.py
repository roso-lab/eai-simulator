"""Runtime repairs for the provider-backed Factory USD stage."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


FACTORY_RAYCAST_PROXY_NAME = "EaiRaycastTerrain"
FACTORY_RAYCAST_PROXY_PRIM_PATH = f"/World/Indoor/{FACTORY_RAYCAST_PROXY_NAME}"

_LEGACY_RAYCAST_PROXY_NAME = "RaycastMergeMesh"
_RAYCAST_SOURCE_MESHES = (
    "GroundPlane/CollisionMesh",
    "SM_StairsSmall/SM_StairsSmall/Section0",
    "SM_StairsSmall/SM_StairsSmall/Section1",
    # Opaque provider name for the upper stair landing collision mesh.
    "wall/wall_06",
)
_HIGH_FRICTION_MATERIAL = "Pysical_Material/TrainHighFriction_PM"


@dataclass(frozen=True)
class FactoryRaycastProxyReport:
    prim_path: str
    point_count: int
    triangle_count: int
    source_meshes: tuple[str, ...]


def _child_path(root_path: str, relative_path: str) -> str:
    return f"{root_path.rstrip('/')}/{relative_path}"


def _validated_mesh_topology(
    mesh: Any, path: str
) -> tuple[list[Any], list[int], list[int]]:
    from pxr import UsdGeom, Vt

    points = list(mesh.GetPointsAttr().Get() or [])
    counts = [int(value) for value in (mesh.GetFaceVertexCountsAttr().Get() or [])]
    indices = [int(value) for value in (mesh.GetFaceVertexIndicesAttr().Get() or [])]
    valid, reason = UsdGeom.Mesh.ValidateTopology(
        Vt.IntArray(indices), Vt.IntArray(counts), len(points)
    )
    if not valid:
        raise ValueError(f"invalid Factory source mesh topology at {path}: {reason}")
    if any(count < 3 for count in counts):
        raise ValueError(f"Factory source mesh has a face with fewer than 3 vertices: {path}")
    return points, counts, indices


def build_factory_raycast_proxy(
    stage: Any, factory_root_path: str
) -> FactoryRaycastProxyReport:
    """Build one validated, triangle-only mesh for Isaac Lab's single-mesh RayCaster.

    Points are transformed from each source mesh into the proxy's local space. This
    preserves the provider asset's nested transforms while still producing the flat
    triangle index buffer expected by Isaac Lab's Warp conversion.
    """
    from pxr import Gf, Usd, UsdGeom, Vt

    root = stage.GetPrimAtPath(factory_root_path)
    if not root or not root.IsValid():
        raise ValueError(f"Factory root does not exist: {factory_root_path}")

    legacy = stage.GetPrimAtPath(_child_path(factory_root_path, _LEGACY_RAYCAST_PROXY_NAME))
    if legacy and legacy.IsValid():
        legacy.SetActive(False)

    proxy_path = _child_path(factory_root_path, FACTORY_RAYCAST_PROXY_NAME)
    proxy = UsdGeom.Mesh.Define(stage, proxy_path)
    xform_cache = UsdGeom.XformCache(Usd.TimeCode.Default())
    proxy_world_inverse = xform_cache.GetLocalToWorldTransform(proxy.GetPrim()).GetInverse()

    merged_points: list[Any] = []
    triangle_indices: list[int] = []
    source_paths: list[str] = []
    for relative_path in _RAYCAST_SOURCE_MESHES:
        source_path = _child_path(factory_root_path, relative_path)
        source_prim = stage.GetPrimAtPath(source_path)
        if not source_prim or not source_prim.IsA(UsdGeom.Mesh):
            raise ValueError(f"Factory raycast source mesh does not exist: {source_path}")
        source_mesh = UsdGeom.Mesh(source_prim)
        points, counts, indices = _validated_mesh_topology(source_mesh, source_path)
        source_to_world = xform_cache.GetLocalToWorldTransform(source_prim)
        point_offset = len(merged_points)
        for point in points:
            world_point = source_to_world.Transform(Gf.Vec3d(point))
            merged_points.append(Gf.Vec3f(proxy_world_inverse.Transform(world_point)))

        cursor = 0
        for count in counts:
            face = indices[cursor : cursor + count]
            cursor += count
            for index in range(1, count - 1):
                triangle_indices.extend(
                    (
                        point_offset + face[0],
                        point_offset + face[index],
                        point_offset + face[index + 1],
                    )
                )
        source_paths.append(source_path)

    triangle_counts = [3] * (len(triangle_indices) // 3)
    valid, reason = UsdGeom.Mesh.ValidateTopology(
        Vt.IntArray(triangle_indices), Vt.IntArray(triangle_counts), len(merged_points)
    )
    if not valid:
        raise ValueError(f"generated Factory raycast proxy is invalid: {reason}")
    if not triangle_indices or max(triangle_indices) >= len(merged_points):
        raise ValueError("generated Factory raycast proxy has out-of-range indices")

    proxy.CreatePointsAttr().Set(merged_points)
    proxy.CreateFaceVertexCountsAttr().Set(triangle_counts)
    proxy.CreateFaceVertexIndicesAttr().Set(triangle_indices)
    proxy.CreateSubdivisionSchemeAttr().Set(UsdGeom.Tokens.none)
    proxy.CreateVisibilityAttr().Set(UsdGeom.Tokens.invisible)

    return FactoryRaycastProxyReport(
        prim_path=proxy_path,
        point_count=len(merged_points),
        triangle_count=len(triangle_counts),
        source_meshes=tuple(source_paths),
    )


def configure_factory_high_friction(stage: Any, factory_root_path: str) -> str:
    """Apply and bind the Factory's authored 1.0/1.0 physics material."""
    from pxr import UsdPhysics, UsdShade

    material_path = _child_path(factory_root_path, _HIGH_FRICTION_MATERIAL)
    material_prim = stage.GetPrimAtPath(material_path)
    if not material_prim or not material_prim.IsA(UsdShade.Material):
        raise ValueError(f"Factory high-friction material does not exist: {material_path}")
    material = UsdShade.Material(material_prim)
    material_api = UsdPhysics.MaterialAPI.Apply(material_prim)
    material_api.CreateStaticFrictionAttr().Set(1.0)
    material_api.CreateDynamicFrictionAttr().Set(1.0)

    for relative_path in _RAYCAST_SOURCE_MESHES:
        mesh_path = _child_path(factory_root_path, relative_path)
        mesh_prim = stage.GetPrimAtPath(mesh_path)
        if not mesh_prim or not mesh_prim.IsValid():
            raise ValueError(f"Factory collision mesh does not exist: {mesh_path}")
        UsdShade.MaterialBindingAPI.Apply(mesh_prim).Bind(
            material,
            bindingStrength=UsdShade.Tokens.strongerThanDescendants,
            materialPurpose="physics",
        )
    return material_path
