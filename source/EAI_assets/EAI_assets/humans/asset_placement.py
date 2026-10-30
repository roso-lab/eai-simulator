# Copyright (c) 2022-2026, The Isaac Lab Project Developers.
# SPDX-License-Identifier: BSD-3-Clause

"""Manifest-driven placement for human USD assets."""

from __future__ import annotations

import math
from typing import Any


# Skinned skeleton bounds can legitimately extend slightly below authored mesh
# bounds. A larger gap indicates stale/helper extents that would visibly float.
_MAX_AUXILIARY_GROUNDING_GAP = 0.25


def asset_orientation(asset: Any, Gf: Any) -> Any:
    """Return the asset-local rotation that converts its content to Z-up."""
    half_yaw = 0.5 * float(asset.yaw_offset)
    yaw = Gf.Quatd(
        math.cos(half_yaw),
        Gf.Vec3d(0.0, 0.0, math.sin(half_yaw)),
    )
    if asset.content_up_axis == "Z":
        return yaw
    half_x = math.sqrt(0.5)
    y_to_z = Gf.Quatd(half_x, Gf.Vec3d(half_x, 0.0, 0.0))
    return yaw * y_to_z


def grounding_world_range(prim: Any) -> Any:
    """Return bounds suitable for grounding and framing visible human geometry."""
    from pxr import Gf, Usd, UsdGeom, UsdSkel, Vt

    time = Usd.TimeCode.Default()
    cache = UsdGeom.BBoxCache(
        time,
        [
            UsdGeom.Tokens.default_,
            UsdGeom.Tokens.render,
            UsdGeom.Tokens.proxy,
        ],
        useExtentsHint=False,
    )
    composed_range = cache.ComputeWorldBound(prim).ComputeAlignedRange()

    descendants = list(Usd.PrimRange(prim))
    skel_cache = UsdSkel.Cache()
    for descendant in descendants:
        if descendant.IsA(UsdSkel.Root):
            skel_cache.Populate(UsdSkel.Root(descendant), Usd.PrimDefaultPredicate)

    visible_range = Gf.Range3d()
    xform_cache = UsdGeom.XformCache(time)
    for descendant in descendants:
        if not descendant.IsA(UsdGeom.Mesh):
            continue
        imageable = UsdGeom.Imageable(descendant)
        if imageable.ComputeVisibility() == UsdGeom.Tokens.invisible:
            continue
        purpose = imageable.GetPurposeAttr().Get() or UsdGeom.Tokens.default_
        if purpose not in {UsdGeom.Tokens.default_, UsdGeom.Tokens.render}:
            continue

        mesh = UsdGeom.Mesh(descendant)
        skinning_query = skel_cache.GetSkinningQuery(descendant)
        skeleton = UsdSkel.BindingAPI(descendant).GetInheritedSkeleton()
        if skinning_query and skeleton:
            skel_query = skel_cache.GetSkelQuery(skeleton)
            points_value = mesh.GetPointsAttr().Get(time)
            if skel_query and points_value:
                points = Vt.Vec3fArray(points_value)
                skinning_transforms = skel_query.ComputeSkinningTransforms(time)
                if skinning_query.ComputeSkinnedPoints(skinning_transforms, points):
                    mesh_to_world = xform_cache.GetLocalToWorldTransform(descendant)
                    for point in points:
                        visible_range.UnionWith(
                            mesh_to_world.Transform(Gf.Vec3d(point))
                        )
                    continue

        mesh_range = cache.ComputeWorldBound(descendant).ComputeAlignedRange()
        if not mesh_range.IsEmpty():
            visible_range.UnionWith(mesh_range)

    if visible_range.IsEmpty():
        return composed_range
    if composed_range.IsEmpty():
        return visible_range
    auxiliary_gap = float(visible_range.GetMin()[2]) - float(
        composed_range.GetMin()[2]
    )
    if auxiliary_gap > _MAX_AUXILIARY_GROUNDING_GAP:
        return visible_range
    return composed_range


def apply_asset_placement(asset_xform: Any, asset: Any, parent_z: float) -> None:
    """Apply manifest orientation/scale and ground the resulting visible bounds."""
    from pxr import Gf, UsdGeom

    asset_xform.ClearXformOpOrder()
    translate_op = asset_xform.AddTranslateOp(
        precision=UsdGeom.XformOp.PrecisionDouble,
        opSuffix="eaiAsset",
    )
    orient_op = asset_xform.AddOrientOp(
        precision=UsdGeom.XformOp.PrecisionDouble,
        opSuffix="eaiAsset",
    )
    scale_op = asset_xform.AddScaleOp(
        precision=UsdGeom.XformOp.PrecisionDouble,
        opSuffix="eaiAsset",
    )
    translate_op.Set(Gf.Vec3d(0.0, 0.0, 0.0))
    orient_op.Set(asset_orientation(asset, Gf))
    scale_op.Set(Gf.Vec3d(*asset.scale))

    world_range = grounding_world_range(asset_xform.GetPrim())
    correction = float(asset.ground_offset)
    if not world_range.IsEmpty():
        correction += float(parent_z) - float(world_range.GetMin()[2])
    translate_op.Set(Gf.Vec3d(0.0, 0.0, correction))


__all__ = ["apply_asset_placement", "asset_orientation", "grounding_world_range"]
