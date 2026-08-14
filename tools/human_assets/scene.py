"""USD scene decoration for the interactive human-assets demo."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class SelectionRing:
    translate_attr: Any
    visibility_attr: Any


def grid_origins(
    count: int,
    *,
    columns: int = 8,
    column_spacing: float = 6.0,
    row_spacing: float = 6.0,
) -> tuple[tuple[float, float, float], ...]:
    count = int(count)
    columns = int(columns)
    column_spacing = float(column_spacing)
    row_spacing = float(row_spacing)
    if count < 0:
        raise ValueError("grid actor count must be non-negative")
    if columns <= 0:
        raise ValueError("grid columns must be positive")
    if (
        not math.isfinite(column_spacing)
        or not math.isfinite(row_spacing)
        or column_spacing <= 0.0
        or row_spacing <= 0.0
    ):
        raise ValueError("grid spacing must be positive and finite")
    return tuple(
        (
            float(index % columns) * column_spacing,
            float(index // columns) * row_spacing,
            0.0,
        )
        for index in range(count)
    )


def create_demo_environment(
    stage: Any,
    origins: Sequence[Sequence[float]] = (),
) -> None:
    from pxr import Gf, UsdGeom, UsdLux, Vt

    if origins:
        x_values = [float(origin[0]) for origin in origins]
        y_values = [float(origin[1]) for origin in origins]
        minimum_x, maximum_x = min(x_values) - 4.0, max(x_values) + 4.0
        minimum_y, maximum_y = min(y_values) - 4.0, max(y_values) + 4.0
        ground_center = (
            0.5 * (minimum_x + maximum_x),
            0.5 * (minimum_y + maximum_y),
        )
        ground_size = (maximum_x - minimum_x, maximum_y - minimum_y)
    else:
        ground_center = (6.0, 3.0)
        ground_size = (14.0, 9.0)
    ground = UsdGeom.Cube.Define(stage, "/World/Demo/Ground")
    ground.CreateSizeAttr(1.0)
    ground.AddTranslateOp().Set(Gf.Vec3d(*ground_center, -0.05))
    ground.AddScaleOp().Set(Gf.Vec3d(*ground_size, 0.1))
    ground.CreateDisplayColorAttr().Set(
        Vt.Vec3fArray([Gf.Vec3f(0.32, 0.34, 0.35)])
    )

    dome = UsdLux.DomeLight.Define(stage, "/World/Demo/DomeLight")
    dome.CreateIntensityAttr(900.0)
    sun = UsdLux.DistantLight.Define(stage, "/World/Demo/Sun")
    sun.CreateIntensityAttr(2500.0)
    sun.CreateAngleAttr(0.6)


def create_route_curve(
    stage: Any,
    path: str,
    waypoints: Sequence[Sequence[float]],
    color: Sequence[float],
) -> None:
    from pxr import Gf, UsdGeom, Vt

    if len(waypoints) < 2:
        raise ValueError("route curve requires at least two waypoints")
    points = [
        Gf.Vec3f(float(x), float(y), float(z) + 0.015)
        for x, y, z in waypoints
    ]
    if points[0] != points[-1]:
        points.append(points[0])
    curve = UsdGeom.BasisCurves.Define(stage, path)
    curve.CreateTypeAttr(UsdGeom.Tokens.linear)
    curve.CreateWrapAttr(UsdGeom.Tokens.nonperiodic)
    curve.CreateCurveVertexCountsAttr().Set(Vt.IntArray([len(points)]))
    curve.CreatePointsAttr().Set(Vt.Vec3fArray(points))
    curve.CreateWidthsAttr().Set(Vt.FloatArray([0.035]))
    curve.SetWidthsInterpolation(UsdGeom.Tokens.constant)
    curve.CreateDisplayColorAttr().Set(Vt.Vec3fArray([Gf.Vec3f(*color)]))


def create_selection_rings(
    stage: Any, actor_ids: Sequence[str]
) -> dict[str, SelectionRing]:
    from pxr import Gf, Tf, UsdGeom, Vt

    local_points = [
        Gf.Vec3f(
            0.48 * math.cos(index * math.tau / 32),
            0.48 * math.sin(index * math.tau / 32),
            0.025,
        )
        for index in range(33)
    ]
    rings: dict[str, SelectionRing] = {}
    for actor_id in actor_ids:
        prim_name = Tf.MakeValidIdentifier(str(actor_id))
        root = UsdGeom.Xform.Define(
            stage, f"/World/Demo/Selection/{prim_name}"
        )
        translate = root.AddTranslateOp().GetAttr()
        visibility = UsdGeom.Imageable(root.GetPrim()).CreateVisibilityAttr()
        curve = UsdGeom.BasisCurves.Define(stage, f"{root.GetPath()}/Ring")
        curve.CreateTypeAttr(UsdGeom.Tokens.linear)
        curve.CreateWrapAttr(UsdGeom.Tokens.nonperiodic)
        curve.CreateCurveVertexCountsAttr().Set(Vt.IntArray([len(local_points)]))
        curve.CreatePointsAttr().Set(Vt.Vec3fArray(local_points))
        curve.CreateWidthsAttr().Set(Vt.FloatArray([0.06]))
        curve.SetWidthsInterpolation(UsdGeom.Tokens.constant)
        curve.CreateDisplayColorAttr().Set(
            Vt.Vec3fArray([Gf.Vec3f(1.0, 0.75, 0.05)])
        )
        rings[str(actor_id)] = SelectionRing(translate, visibility)
    return rings


def update_selection_rings(
    rings: Mapping[str, SelectionRing],
    selected_actor_ids: Sequence[str],
    positions: Mapping[str, Sequence[float]],
) -> None:
    from pxr import Gf, UsdGeom

    selected = set(selected_actor_ids)
    for actor_id, ring in rings.items():
        ring.visibility_attr.Set(
            UsdGeom.Tokens.inherited
            if actor_id in selected
            else UsdGeom.Tokens.invisible
        )
        ring.translate_attr.Set(Gf.Vec3d(*positions[actor_id]))


def validate_actor_bounds(
    *,
    minimum: Sequence[float],
    size: Sequence[float],
    asset_id: str,
    require_grounded: bool = True,
    require_upright: bool = True,
) -> None:
    if len(minimum) != 3 or len(size) != 3:
        raise AssertionError(f"actor bounds must be three-dimensional: {asset_id}")
    minimum_values = tuple(float(value) for value in minimum)
    size_values = tuple(float(value) for value in size)
    if not all(math.isfinite(value) for value in (*minimum_values, *size_values)):
        raise AssertionError(f"actor bounds are non-finite: {asset_id}")
    if any(value <= 0.0 for value in size_values):
        raise AssertionError(f"actor bounds are empty: {asset_id}")
    if require_grounded and abs(minimum_values[2]) > 1.0e-3:
        raise AssertionError(
            f"actor is not grounded: {asset_id}; min_z={minimum_values[2]}"
        )
    horizontal_span = max(size_values[0], size_values[1])
    if require_upright and size_values[2] < 0.75 * horizontal_span:
        raise AssertionError(
            f"actor is not upright: {asset_id}; size={size_values}"
        )


def world_bounds(
    stage: Any,
    prim_path: Any,
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    from EAI_assets.humans.asset_placement import grounding_world_range

    bounds = grounding_world_range(stage.GetPrimAtPath(prim_path))
    if bounds.IsEmpty():
        return (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)
    return (
        tuple(float(value) for value in bounds.GetMin()),
        tuple(float(value) for value in bounds.GetSize()),
    )


def camera_view_for_bounds(
    *,
    minimum: Sequence[float],
    size: Sequence[float],
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    minimum_values = tuple(float(value) for value in minimum)
    size_values = tuple(float(value) for value in size)
    if len(minimum_values) != 3 or len(size_values) != 3:
        raise ValueError("camera bounds must be three-dimensional")
    if not all(math.isfinite(value) for value in (*minimum_values, *size_values)):
        raise ValueError("camera bounds must be finite")
    if any(value <= 0.0 for value in size_values):
        target = (0.0, 0.0, 1.0)
        radius = 2.0
    else:
        target = tuple(
            minimum_values[index] + 0.5 * size_values[index]
            for index in range(3)
        )
        radius = min(max(max(size_values), 1.5), 8.0)
    eye = (
        target[0] + 1.8 * radius,
        target[1] - 2.4 * radius,
        target[2] + 1.2 * radius,
    )
    return eye, target  # type: ignore[return-value]


def focus_camera_on_prim(stage: Any, prim_path: Any) -> None:
    from isaacsim.core.utils.viewports import set_camera_view

    minimum, size = world_bounds(stage, prim_path)
    eye, target = camera_view_for_bounds(minimum=minimum, size=size)
    set_camera_view(eye=eye, target=target)


def set_demo_camera() -> None:
    from isaacsim.core.utils.viewports import set_camera_view

    set_camera_view(eye=(7.0, -8.0, 5.5), target=(1.25, 1.5, 0.9))


__all__ = [
    "SelectionRing",
    "camera_view_for_bounds",
    "create_demo_environment",
    "create_route_curve",
    "create_selection_rings",
    "focus_camera_on_prim",
    "grid_origins",
    "set_demo_camera",
    "update_selection_rings",
    "validate_actor_bounds",
    "world_bounds",
]
