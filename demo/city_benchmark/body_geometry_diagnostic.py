# Copyright (c) 2022-2026, The Isaac Lab Project Developers.
# SPDX-License-Identifier: BSD-3-Clause

"""Evaluator-only current-body geometry diagnostics for the City benchmark.

The collector deliberately does not participate in arrival, safety, capacity,
or release decisions.  It records the current visible mesh points needed to
recompute a disk-footprint diagnosis offline.  Skinned meshes are accepted
only when OpenUSD can evaluate their current pose; bind-pose or extent
fallbacks would turn missing evidence into a false measurement.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any


_SCHEMA = "eai.city.body_geometry_diagnostic"
_VERSION = 1
_VISIBLE_PURPOSES = {"default", "render"}
_LIMITATIONS = [
    "current_visible_mesh_points_only",
    "does_not_certify_collision_geometry",
    "does_not_change_arrival_or_safety",
    "does_not_validate_oracle_or_release",
]


class _MeasurementUnknown(RuntimeError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = str(code)
        self.detail = str(detail)


def _finite(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _MeasurementUnknown("invalid_input", f"{label} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise _MeasurementUnknown("invalid_input", f"{label} must be finite")
    return result


def _nonempty(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _MeasurementUnknown("invalid_input", f"{label} must be a non-empty string")
    return value


def _json_safe(value: Any, label: str = "value") -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise _MeasurementUnknown("invalid_input", f"{label} contains a non-finite float")
        return value
    if isinstance(value, Mapping):
        result = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise _MeasurementUnknown("invalid_input", f"{label} contains a non-string key")
            result[key] = _json_safe(item, f"{label}.{key}")
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_json_safe(item, f"{label}[]") for item in value]
    raise _MeasurementUnknown(
        "invalid_input", f"{label} contains unsupported type {type(value).__name__}"
    )


def _footprint(value: Any) -> tuple[dict[str, Any], tuple[float, float], float]:
    if not isinstance(value, Mapping):
        raise _MeasurementUnknown("invalid_footprint", "footprint must be a mapping")
    document = _json_safe(deepcopy(dict(value)), "footprint")
    center = document.get("center_xy_m")
    radius = document.get("radius_m")
    digest = document.get("geometry_sha256")
    if (document.get("footprint") != "disk"
            or not isinstance(document.get("region_id"), str)
            or not document["region_id"].strip()
            or not isinstance(center, list) or len(center) != 2
            or not isinstance(digest, str) or len(digest) != 64):
        raise _MeasurementUnknown(
            "invalid_footprint", "footprint must identify a fixed disk and geometry digest"
        )
    center_xy = (
        _finite(center[0], "footprint.center_xy_m[0]"),
        _finite(center[1], "footprint.center_xy_m[1]"),
    )
    radius_m = _finite(radius, "footprint.radius_m")
    if radius_m <= 0.0:
        raise _MeasurementUnknown("invalid_footprint", "footprint radius must be positive")
    return document, center_xy, radius_m


def _matrix_rows(matrix: Any) -> list[list[float]]:
    rows = [[float(matrix[row][column]) for column in range(4)] for row in range(4)]
    if not all(math.isfinite(value) for row in rows for value in row):
        raise _MeasurementUnknown("invalid_transform", "transform contains non-finite values")
    return rows


def _checked_matrix(matrix: Any, label: str) -> list[list[float]]:
    rows = _matrix_rows(matrix)
    determinant = float(matrix.GetDeterminant())
    if not math.isfinite(determinant) or abs(determinant) <= 1.0e-12:
        raise _MeasurementUnknown("invalid_transform", f"{label} transform is singular")
    return rows


def _point(value: Any, *, label: str) -> list[float]:
    result = [float(value[index]) for index in range(3)]
    if not all(math.isfinite(component) for component in result):
        raise _MeasurementUnknown("invalid_geometry_point", f"{label} is non-finite")
    return result


def _range_document(value: Any) -> dict[str, list[float]]:
    if value.IsEmpty():
        raise _MeasurementUnknown("empty_geometry", "geometry range is empty")
    low = _point(value.GetMin(), label="range minimum")
    high = _point(value.GetMax(), label="range maximum")
    if any(high[index] < low[index] for index in range(3)):
        raise _MeasurementUnknown("invalid_geometry_range", "geometry range is inverted")
    return {"min": low, "max": high}


def _corners(bounds: Mapping[str, Sequence[float]]) -> list[tuple[float, float, float]]:
    low, high = bounds["min"], bounds["max"]
    return [
        (float(x), float(y), float(z))
        for x in (low[0], high[0])
        for y in (low[1], high[1])
        for z in (low[2], high[2])
    ]


def _max_xy_distance(points: Sequence[Sequence[float]], center: tuple[float, float]) -> float:
    if not points:
        raise _MeasurementUnknown("empty_geometry", "geometry contains no points")
    return max(math.hypot(float(point[0]) - center[0], float(point[1]) - center[1])
               for point in points)


def _base(
    *,
    prim_path: Any,
    person_id: Any,
    episode_id: Any,
    source_id: Any,
    captured_native_s: Any,
    footprint: Any,
) -> dict[str, Any]:
    safe_time = (
        float(captured_native_s)
        if isinstance(captured_native_s, (int, float))
        and not isinstance(captured_native_s, bool)
        and math.isfinite(float(captured_native_s))
        else None
    )
    return {
        "schema": _SCHEMA,
        "version": _VERSION,
        "status": "unknown",
        "reason_codes": [],
        "reason_details": [],
        "person_id": person_id if isinstance(person_id, str) else str(person_id),
        "episode_id": episode_id if isinstance(episode_id, str) else str(episode_id),
        "source_id": source_id if isinstance(source_id, str) else str(source_id),
        "captured_native_s": safe_time,
        "prim_path": prim_path if isinstance(prim_path, str) else str(prim_path),
        # Only a validated JSON-safe footprint is copied into the result.
        "footprint": None,
        "world_aabb": None,
        "transforms": None,
        "measurement": {
            "time_code": "default",
            "scope": "current_visible_mesh_points",
            "point_semantics": "current_default_timecode_skinned_or_authored_mesh_points",
            "mesh_count": None,
            "point_count": None,
            "meshes": [],
            "actor_local_range": None,
            "oriented_world_corners": [],
            "max_oriented_corner_xy_distance_m": None,
            "max_visible_mesh_point_xy_distance_m": None,
            "oriented_bound_inside_footprint": None,
            "visible_mesh_points_inside_footprint": None,
        },
        "limitations": list(_LIMITATIONS),
        "platform_valid": False,
        "execution_validated": False,
        "business_outcomes_validated": False,
        "scoreable": False,
    }


def _unknown(result: dict[str, Any], error: _MeasurementUnknown) -> dict[str, Any]:
    result["status"] = "unknown"
    result["reason_codes"] = [error.code]
    result["reason_details"] = [error.detail]
    # Partial point sets must never look like a completed measurement.
    result["measurement"].update({
        "mesh_count": None,
        "point_count": None,
        "meshes": [],
        "actor_local_range": None,
        "oriented_world_corners": [],
        "max_oriented_corner_xy_distance_m": None,
        "max_visible_mesh_point_xy_distance_m": None,
        "oriented_bound_inside_footprint": None,
        "visible_mesh_points_inside_footprint": None,
    })
    return result


def _has_incomplete_skinning_authorship(prim: Any) -> bool:
    return any(
        str(attribute.GetName()).startswith(("primvars:skel:", "skel:"))
        for attribute in prim.GetAttributes()
    )


def _require_current_animation_pose(anim_query: Any, skel_query: Any, time: Any) -> None:
    """Reject UsdSkel's silent rest-pose fallback for incomplete animation."""
    animation_order = tuple(str(value) for value in anim_query.GetJointOrder())
    skeleton_order = tuple(str(value) for value in skel_query.GetJointOrder())
    if not animation_order or animation_order != skeleton_order:
        raise _MeasurementUnknown(
            "current_animation_unavailable",
            "animation joint order is empty or does not cover the skeleton exactly",
        )
    try:
        components = anim_query.ComputeJointLocalTransformComponents(time)
        local_transforms = anim_query.ComputeJointLocalTransforms(time)
    except Exception as exc:
        raise _MeasurementUnknown(
            "current_animation_unavailable",
            f"current animation components failed: {type(exc).__name__}: {exc}",
        ) from exc
    if (not isinstance(components, tuple) or len(components) != 3
            or any(len(values) != len(animation_order) for values in components)
            or len(local_transforms) != len(animation_order)):
        raise _MeasurementUnknown(
            "current_animation_unavailable",
            "current animation translations, rotations, and scales must cover every joint",
        )
    translations, rotations, scales = components
    for index, value in enumerate(translations):
        _point(value, label=f"animation translation {index}")
    for index, value in enumerate(scales):
        _point(value, label=f"animation scale {index}")
    for index, value in enumerate(rotations):
        quaternion = [float(value.GetReal()), *map(float, value.GetImaginary())]
        if (not all(math.isfinite(component) for component in quaternion)
                or sum(component * component for component in quaternion) <= 1.0e-12):
            raise _MeasurementUnknown(
                "current_animation_unavailable",
                f"animation rotation {index} is non-finite or degenerate",
            )
    for index, value in enumerate(local_transforms):
        try:
            _checked_matrix(value, f"animation joint {index}")
        except _MeasurementUnknown as exc:
            raise _MeasurementUnknown(
                "current_animation_unavailable", exc.detail
            ) from exc


def capture_body_geometry(
    stage: Any,
    *,
    prim_path: str,
    person_id: str,
    episode_id: str,
    source_id: str,
    captured_native_s: float,
    footprint: Mapping[str, Any],
) -> dict[str, Any]:
    """Capture one actor's current visible-mesh geometry at Default time.

    The result is JSON-safe and fail-closed.  Any skinned mesh whose current
    posed points cannot be established makes the entire diagnostic unknown.
    """
    result = _base(
        prim_path=prim_path,
        person_id=person_id,
        episode_id=episode_id,
        source_id=source_id,
        captured_native_s=captured_native_s,
        footprint=footprint,
    )
    try:
        path_text = _nonempty(prim_path, "prim_path")
        result["person_id"] = _nonempty(person_id, "person_id")
        result["episode_id"] = _nonempty(episode_id, "episode_id")
        result["source_id"] = _nonempty(source_id, "source_id")
        result["captured_native_s"] = _finite(captured_native_s, "captured_native_s")
        footprint_document, center_xy, radius_m = _footprint(footprint)
        result["footprint"] = footprint_document
        if stage is None or not hasattr(stage, "GetPrimAtPath"):
            raise _MeasurementUnknown(
                "stage_unavailable", "current OpenUSD stage is unavailable"
            )

        try:
            from pxr import Gf, Sdf, Usd, UsdGeom, UsdSkel, Vt
        except Exception as exc:  # pragma: no cover - exercised outside Isaac/OpenUSD envs
            raise _MeasurementUnknown(
                "openusd_unavailable", f"OpenUSD imports failed: {type(exc).__name__}: {exc}"
            ) from exc

        path = Sdf.Path(path_text)
        if not path.IsAbsolutePath() or not path.IsPrimPath():
            raise _MeasurementUnknown("invalid_prim_path", f"invalid absolute prim path: {path_text}")
        root = stage.GetPrimAtPath(path)
        if not root or not root.IsValid():
            raise _MeasurementUnknown("actor_prim_missing", f"actor prim is missing: {path_text}")
        asset_path = path.AppendChild("Asset")
        asset_prim = stage.GetPrimAtPath(asset_path)
        if not asset_prim or not asset_prim.IsValid():
            raise _MeasurementUnknown("asset_prim_missing", f"actor Asset prim is missing: {asset_path}")

        time = Usd.TimeCode.Default()
        xform_cache = UsdGeom.XformCache(time)
        root_to_world = xform_cache.GetLocalToWorldTransform(root)
        asset_to_world = xform_cache.GetLocalToWorldTransform(asset_prim)
        root_rows = _checked_matrix(root_to_world, "actor root")
        asset_rows = _checked_matrix(asset_to_world, "actor Asset")
        world_to_root = root_to_world.GetInverse()
        root_translation = _point(root_to_world.ExtractTranslation(), label="root translation")
        root_yaw = math.atan2(root_rows[0][1], root_rows[0][0])
        result["transforms"] = {
            "root": {
                "prim_path": path_text,
                "local_to_world_matrix": root_rows,
                "world_translation_m": root_translation,
                "world_yaw_rad": root_yaw,
            },
            "asset": {
                "prim_path": str(asset_path),
                "local_to_world_matrix": asset_rows,
            },
        }

        bbox_cache = UsdGeom.BBoxCache(
            time, [UsdGeom.Tokens.default_, UsdGeom.Tokens.render]
        )
        result["world_aabb"] = _range_document(
            bbox_cache.ComputeWorldBound(root).ComputeAlignedRange()
        )

        descendants = list(Usd.PrimRange(root))
        skel_cache = UsdSkel.Cache()
        for descendant in descendants:
            if descendant.IsA(UsdSkel.Root):
                skel_cache.Populate(UsdSkel.Root(descendant), Usd.PrimDefaultPredicate)

        mesh_rows: list[dict[str, Any]] = []
        all_world_points: list[list[float]] = []
        actor_local_range = Gf.Range3d()
        for descendant in descendants:
            if not descendant.IsA(UsdGeom.Mesh):
                continue
            imageable = UsdGeom.Imageable(descendant)
            if imageable.ComputeVisibility() == UsdGeom.Tokens.invisible:
                continue
            purpose = imageable.ComputePurpose() or UsdGeom.Tokens.default_
            if str(purpose) not in _VISIBLE_PURPOSES:
                continue

            mesh = UsdGeom.Mesh(descendant)
            binding = UsdSkel.BindingAPI(descendant)
            blend_shapes = binding.GetBlendShapesAttr().Get(time) or []
            blend_targets = binding.GetBlendShapeTargetsRel().GetTargets()
            if blend_shapes or blend_targets:
                raise _MeasurementUnknown(
                    "unsupported_blend_shapes",
                    f"current blend-shape points are unsupported: {descendant.GetPath()}",
                )

            points_value = mesh.GetPointsAttr().Get(time)
            if not points_value:
                raise _MeasurementUnknown(
                    "mesh_points_missing", f"mesh points are missing: {descendant.GetPath()}"
                )
            points = Vt.Vec3fArray(points_value)
            skinning_query = skel_cache.GetSkinningQuery(descendant)
            skeleton = binding.GetInheritedSkeleton()
            if bool(skinning_query) != bool(skeleton):
                raise _MeasurementUnknown(
                    "incomplete_skinning_binding",
                    f"skinning query and inherited skeleton disagree: {descendant.GetPath()}",
                )

            if skinning_query and skeleton:
                skel_query = skel_cache.GetSkelQuery(skeleton)
                if not skel_query:
                    raise _MeasurementUnknown(
                        "skinning_query_unavailable",
                        f"skeleton query is unavailable: {descendant.GetPath()}",
                    )
                anim_query = skel_query.GetAnimQuery()
                if not anim_query:
                    raise _MeasurementUnknown(
                        "current_animation_unavailable",
                        f"current animation source is unavailable: {descendant.GetPath()}",
                    )
                _require_current_animation_pose(anim_query, skel_query, time)
                try:
                    _checked_matrix(
                        skinning_query.GetGeomBindTransform(),
                        f"{descendant.GetPath()} geometry bind",
                    )
                    skinning_transforms = skel_query.ComputeSkinningTransforms(time)
                    skinned = skinning_query.ComputeSkinnedPoints(skinning_transforms, points)
                except Exception as exc:
                    raise _MeasurementUnknown(
                        "skinning_evaluation_failed",
                        f"current skinning failed for {descendant.GetPath()}: "
                        f"{type(exc).__name__}: {exc}",
                    ) from exc
                if not skinned:
                    raise _MeasurementUnknown(
                        "skinning_evaluation_failed",
                        f"current skinning returned false: {descendant.GetPath()}",
                    )
                point_source = "current_skinned_points"
                point_space = "skeleton_local"
                point_space_to_world = xform_cache.GetLocalToWorldTransform(
                    skeleton.GetPrim()
                )
            else:
                if _has_incomplete_skinning_authorship(descendant):
                    raise _MeasurementUnknown(
                        "incomplete_skinning_binding",
                        f"skinning attributes lack a current skeleton query: {descendant.GetPath()}",
                    )
                point_source = "current_authored_points"
                point_space = "mesh_local"
                point_space_to_world = xform_cache.GetLocalToWorldTransform(descendant)

            point_space_rows = _checked_matrix(
                point_space_to_world, f"{descendant.GetPath()} point-space"
            )
            world_points = []
            for index, point_value in enumerate(points):
                world_point = _point(
                    point_space_to_world.Transform(Gf.Vec3d(point_value)),
                    label=f"{descendant.GetPath()} point {index}",
                )
                local_point = world_to_root.Transform(Gf.Vec3d(*world_point))
                actor_local_range.UnionWith(local_point)
                world_points.append(world_point)
                all_world_points.append(world_point)
            if not world_points:
                raise _MeasurementUnknown(
                    "mesh_points_missing", f"mesh contains no points: {descendant.GetPath()}"
                )
            mesh_rows.append({
                "prim_path": str(descendant.GetPath()),
                "point_source": point_source,
                "point_space": point_space,
                "point_space_to_world_matrix": point_space_rows,
                "point_count": len(world_points),
                "world_points_m": world_points,
            })

        if not mesh_rows:
            raise _MeasurementUnknown("visible_mesh_missing", "actor has no visible render mesh")
        actor_bounds = _range_document(actor_local_range)
        oriented_world_corners = [
            _point(root_to_world.Transform(Gf.Vec3d(*corner)), label="oriented world corner")
            for corner in _corners(actor_bounds)
        ]
        oriented_distance = _max_xy_distance(oriented_world_corners, center_xy)
        point_distance = _max_xy_distance(all_world_points, center_xy)
        result["measurement"] = {
            "time_code": "default",
            "scope": "current_visible_mesh_points",
            "point_semantics": "current_default_timecode_skinned_or_authored_mesh_points",
            "mesh_count": len(mesh_rows),
            "point_count": len(all_world_points),
            "meshes": mesh_rows,
            "actor_local_range": actor_bounds,
            "oriented_world_corners": oriented_world_corners,
            "max_oriented_corner_xy_distance_m": oriented_distance,
            "max_visible_mesh_point_xy_distance_m": point_distance,
            "oriented_bound_inside_footprint": oriented_distance <= radius_m,
            "visible_mesh_points_inside_footprint": point_distance <= radius_m,
        }
        result["status"] = "measured"
        result["reason_codes"] = []
        result["reason_details"] = []
        return result
    except _MeasurementUnknown as exc:
        return _unknown(result, exc)
    except Exception as exc:  # Preserve diagnostic evidence without breaking the episode.
        return _unknown(
            result,
            _MeasurementUnknown(
                "geometry_capture_failed", f"{type(exc).__name__}: {exc}"
            ),
        )


__all__ = ["capture_body_geometry"]
