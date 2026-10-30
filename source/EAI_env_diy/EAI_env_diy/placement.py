"""Pose placement helpers, with Isaac PhysX imports kept behind call sites."""

from __future__ import annotations

from typing import Any
import math


def payload_drop_robot_id(target_prim_path, robot_prim_paths) -> str | None:
    """Resolve a viewport drop target only when it belongs to a robot preview."""
    target = str(target_prim_path or "")
    for robot_id, root_path in robot_prim_paths.items():
        root = str(root_path)
        if target == root or target.startswith(f"{root}/"):
            return str(robot_id)
    return None


def canonical_robot_selection(selected_prim_paths, robot_prim_paths):
    """Map the first selected robot descendant to its editable assembly root."""
    for selected_path in selected_prim_paths:
        robot_id = payload_drop_robot_id(selected_path, robot_prim_paths)
        if robot_id is not None:
            return robot_id, str(robot_prim_paths[robot_id])
    return None


def robot_drop_position(world_hit, *, default_root_height: float) -> tuple[float, float, float]:
    hit = tuple(float(item) for item in world_hit)
    if len(hit) != 3:
        raise ValueError("Viewport drop position must contain exactly 3 values.")
    return hit[0], hit[1], round(hit[2] + float(default_root_height), 12)


def collision_aware_robot_drop_position(
    world_hit,
    *,
    collision_hit,
    default_root_height: float,
    surface_tolerance: float = 0.1,
) -> tuple[float, float, float]:
    """Use a viewport hit only when PhysX confirms the same support surface."""
    visual = tuple(float(item) for item in world_hit)
    if len(visual) != 3:
        raise ValueError("Viewport drop position must contain exactly 3 values.")
    if collision_hit is None:
        raise ValueError("No collision surface exists below the viewport drop point.")
    collision = tuple(float(item) for item in collision_hit)
    if len(collision) != 3:
        raise ValueError("Collision surface position must contain exactly 3 values.")
    if abs(visual[2] - collision[2]) > float(surface_tolerance):
        raise ValueError("The selected visual surface has no matching collision surface.")
    return robot_drop_position(collision, default_root_height=default_root_height)


def placement_diagnostics(
    model,
    *,
    missing_robot_ids: tuple[str, ...] | list[str] = (),
    overlap_distance: float = 0.2,
    world_limit: float = 10000.0,
) -> list[str]:
    diagnostics = [f"{robot_id}: preview asset is missing" for robot_id in missing_robot_ids]
    for robot in model.robots:
        if any(abs(value) > world_limit for value in robot.position):
            diagnostics.append(f"{robot.id}: pose is outside authoring bounds")
    for index, left in enumerate(model.robots):
        for right in model.robots[index + 1:]:
            distance = math.sqrt(
                sum((a - b) ** 2 for a, b in zip(left.position, right.position))
            )
            if distance < overlap_distance:
                diagnostics.append(f"{left.id} and {right.id}: possible collision overlap")
    return diagnostics


def surface_snap_position(
    current_position,
    *,
    hit_position,
    clearance: float = 0.0,
    keep_current_z: bool = False,
) -> tuple[float, float, float]:
    current = tuple(float(item) for item in current_position)
    hit = tuple(float(item) for item in hit_position)
    if len(current) != 3 or len(hit) != 3:
        raise ValueError("Surface snapping requires 3D positions.")
    if keep_current_z:
        return current
    return (current[0], current[1], round(hit[2] + float(clearance), 12))


def select_surface_hit_below(
    hit_positions,
    *,
    current_z: float,
    epsilon: float = 1.0e-6,
) -> tuple[float, float, float] | None:
    """Return the highest hit that is not meaningfully above the current pose."""
    ceiling = float(current_z) + float(epsilon)
    candidates: list[tuple[float, float, float]] = []
    for hit_position in hit_positions:
        hit = tuple(float(item) for item in hit_position)
        if len(hit) != 3:
            raise ValueError("Raycast hit positions must contain exactly 3 values.")
        if hit[2] <= ceiling:
            candidates.append(hit)
    return max(candidates, key=lambda item: item[2]) if candidates else None


def raycast_surface_below(
    position,
    *,
    excluded_prim_prefix: str | None = None,
    ray_height: float = 1000.0,
    ray_distance: float = 2000.0,
) -> tuple[float, float, float] | None:
    """Return the highest non-excluded PhysX hit below the given XY point."""
    from omni.physx import get_physx_interface, get_physx_scene_query_interface

    x, y, z = (float(item) for item in position)
    height = float(ray_height)
    if height <= 0.0:
        raise ValueError("Ray height must be positive.")
    origin = (x, y, z + height)
    hits: list[tuple[float, float, float]] = []

    def report_hit(hit: Any) -> bool:
        if isinstance(hit, dict):
            body = str(hit.get("rigidBody", hit.get("rigid_body", "")))
            hit_position = hit.get("position")
        else:
            body = str(getattr(hit, "rigid_body", ""))
            hit_position = getattr(hit, "position", None)
        if excluded_prim_prefix and body.startswith(excluded_prim_prefix):
            return True
        if hit_position is not None:
            hits.append(tuple(float(item) for item in hit_position))
        return True

    get_physx_interface().force_load_physics_from_usd()
    get_physx_scene_query_interface().raycast_all(
        origin,
        (0.0, 0.0, -1.0),
        float(ray_distance),
        report_hit,
    )
    return select_surface_hit_below(hits, current_z=z)
