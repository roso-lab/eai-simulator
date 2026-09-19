"""Offline region geometry derivation from the provider map and scene USD layer.

The Modern House double binding needs measured-feeling region intervals that a
clean checkout can rebuild without Isaac: radial free-cell intervals seeded at
the author-proposed region centers, static wall cuboids read from the authored
bench collision layer with the standalone single-layer OpenUSD reader, and
doorway openings where the measured boundary leaks through a gap whose flanks
are corroborated by those static wall cuboids. Nothing here composes the live
stage, runs physics, or certifies execution; every row keeps its limits.
"""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

from .provenance import canonical_sha256


HOUSE_LAYER_RELATIVE = "scene/modern_house_nvd/modern_house_nvd_bench.usd"
DEFAULT_MAX_RADIUS_M = 5.0
DEFAULT_RAY_COUNT = 72
DEFAULT_FLOOR_Z_M = 0.25
DEFAULT_WALL_MIN_HEIGHT_M = 0.3
DEFAULT_GAP_MIN_M = 0.5
DEFAULT_GAP_MAX_M = 1.8
DEFAULT_FAR_FACTOR_M = 1.0
DEFAULT_CORROBORATION_TOLERANCE_M = 0.12
DEFAULT_DOOR_APPROACH_M = 0.4


def house_bench_layer_path() -> Path:
    """Resolve the authored House bench layer through the read-only USD root."""
    from EAI_assets.asset_resolver import usd_root
    return usd_root() / HOUSE_LAYER_RELATIVE


def _finite(value: Any) -> bool:
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and math.isfinite(value))


def _xy(value: Any, label: str) -> list[float]:
    if (not isinstance(value, (list, tuple)) or len(value) < 2
            or any(not _finite(item) for item in value[:2])):
        raise ValueError(f"{label} requires finite XY coordinates")
    return [float(value[0]), float(value[1])]


def _file_sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def static_wall_cuboids(layer_path: Path | str, *, max_cuboids: int = 20000) -> dict[str, Any]:
    """Enumerate authored axis-aligned static collision cubes from one layer.

    Mirrors the live stage's ``static_floor_colliders`` acceptance rule—only
    enabled collision Cubes with a plain translate+scale op order—but reads the
    authored layer with the standalone OpenUSD single-layer reader, so nothing
    is composed, referenced, or simulated here.
    """
    layer_path = Path(layer_path)
    result: dict[str, Any] = {
        "path": str(layer_path.absolute()),
        "sha256": _file_sha256(layer_path) if layer_path.is_file() else None,
        "status": "enumerated",
        "cuboids": [],
        "basis": "authored_single_layer_axis_aligned_enabled_collision_cubes",
        "limits": ["authored_layer_only_no_reference_or_payload_composition",
                   "no_live_fabric_or_physics_state",
                   "transformed_or_sheared_cubes_are_skipped_not_approximated"],
    }
    if not layer_path.is_file():
        result["status"] = "missing_layer"
        return result
    try:
        from pxr import Sdf
    except ImportError:
        result["status"] = "openusd_unavailable"
        return result
    layer = Sdf.Layer.OpenAsAnonymous(str(layer_path))
    if layer is None:
        result["status"] = "unreadable_layer"
        return result
    cuboids: list[dict[str, Any]] = []

    def visit(prim_path) -> None:
        prim = layer.GetObjectAtPath(prim_path)
        if not isinstance(prim, Sdf.PrimSpec):
            return
        if prim.typeName == "Cube":
            attributes = {attribute.name: attribute.default for attribute in prim.attributes}
            if attributes.get("physics:collisionEnabled") is False:
                return
            order = list(attributes.get("xformOpOrder") or [])
            if order != ["xformOp:translate", "xformOp:scale"]:
                return
            translate, scale = attributes.get("xformOp:translate"), attributes.get("xformOp:scale")
            size = attributes.get("size")
            if translate is None or scale is None:
                return
            # Unauthored size falls back to the UsdGeomCube schema value (2.0).
            size_value = 2.0 if size is None else float(size)
            if not all(_finite(value) for value in (*translate, *scale, size_value)):
                return
            half = [size_value * float(scale[i]) / 2 for i in range(3)]
            cuboids.append({
                "prim_path": str(prim_path),
                "low_xyz_m": [round(float(translate[i]) - half[i], 6) for i in range(3)],
                "high_xyz_m": [round(float(translate[i]) + half[i], 6) for i in range(3)],
            })
            if len(cuboids) > max_cuboids:
                raise ValueError("static collision cube inventory exceeds bound")
        for child in prim.nameChildren:
            visit(prim_path.AppendChild(child.name))

    visit(Sdf.Path.absoluteRootPath)
    result["cuboids"] = cuboids
    result["cuboid_count"] = len(cuboids)
    return result


def _wall_band_cuboids(walls: Mapping[str, Any], *, floor_z: float,
                       wall_min_height_m: float) -> list[dict[str, Any]]:
    """Cubes that stand on the floor plane and are tall enough to be walls."""
    rows = []
    for cuboid in walls.get("cuboids", []):
        low, high = cuboid["low_xyz_m"], cuboid["high_xyz_m"]
        if low[2] <= floor_z + 1e-6 and high[2] >= floor_z + wall_min_height_m:
            rows.append(cuboid)
    return rows


def radial_free_interval(occupancy: Any, seed_xy: Sequence[float], *,
                         max_radius_m: float = DEFAULT_MAX_RADIUS_M,
                         ray_count: int = DEFAULT_RAY_COUNT) -> dict[str, Any]:
    """March fixed rays from the seed until the first non-free map cell.

    A ray that never meets an occupied/unknown cell or the map border within
    ``max_radius_m`` is open; its capped endpoint still bounds the reported
    interval, and the open direction is recorded instead of being hidden.
    """
    seed = _xy(seed_xy, "seed")
    if not _finite(max_radius_m) or not 1.0 <= max_radius_m <= 12.0:
        raise ValueError("bounded max radius required")
    if type(ray_count) is not int or not 8 <= ray_count <= 360:
        raise ValueError("bounded ray count required")
    resolution = float(occupancy.resolution)
    seed_lx, seed_ly = occupancy.local(*seed)
    seed_ix, seed_iy = math.floor(seed_lx / resolution), math.floor(seed_ly / resolution)
    if not (0 <= seed_ix < occupancy.width and 0 <= seed_iy < occupancy.height
            and bool(occupancy.free[occupancy.height - 1 - seed_iy, seed_ix])):
        return {"status": "seed_cell_not_free", "seed_xy_m": seed, "rays": [],
                "map_sha256": occupancy.sha256}
    rays, points = [], []
    for index in range(ray_count):
        angle = 2 * math.pi * index / ray_count
        direction = (math.cos(angle), math.sin(angle))
        hit, distance, boundary = False, max_radius_m, None
        march = resolution
        while march <= max_radius_m + 1e-9:
            lx, ly = occupancy.local(seed[0] + direction[0] * march, seed[1] + direction[1] * march)
            ix, iy = math.floor(lx / resolution), math.floor(ly / resolution)
            if not 0 <= ix < occupancy.width or not 0 <= iy < occupancy.height \
                    or not bool(occupancy.free[occupancy.height - 1 - iy, ix]):
                hit, distance = True, march
                boundary = list(occupancy.world((ix + .5) * resolution, (iy + .5) * resolution))
                break
            march += resolution
        if boundary is None:
            boundary = [seed[0] + direction[0] * max_radius_m, seed[1] + direction[1] * max_radius_m]
        boundary = [round(boundary[0], 4), round(boundary[1], 4)]
        points.append(boundary)
        rays.append({"angle_rad": round(angle, 6), "hit": hit,
                     "distance_m": round(distance, 4), "boundary_xy_m": boundary})
    closed = [ray["distance_m"] for ray in rays if ray["hit"]]
    median = sorted(closed)[len(closed) // 2] if closed else None
    return {
        "status": "measured",
        "seed_xy_m": seed,
        "map_sha256": occupancy.sha256,
        "resolution_m": resolution,
        "ray_count": ray_count,
        "max_radius_m": float(max_radius_m),
        "rays": rays,
        "interval_aabb_xy_m": [round(min(p[0] for p in points), 4), round(min(p[1] for p in points), 4),
                               round(max(p[0] for p in points), 4), round(max(p[1] for p in points), 4)],
        "open_ray_count": sum(1 for ray in rays if not ray["hit"]),
        "closed_median_distance_m": None if median is None else round(median, 4),
    }


def _sector_far(ray: Mapping[str, Any], median: float | None, far_factor_m: float) -> bool:
    if not ray["hit"]:
        return True
    return median is not None and ray["distance_m"] > median + far_factor_m


def _corroborated(point: Sequence[float], band: Sequence[Mapping[str, Any]],
                  tolerance_m: float) -> bool:
    for cuboid in band:
        low, high = cuboid["low_xyz_m"], cuboid["high_xyz_m"]
        if (low[0] - tolerance_m <= point[0] <= high[0] + tolerance_m
                and low[1] - tolerance_m <= point[1] <= high[1] + tolerance_m):
            return True
    return False


def doorway_openings(interval: Mapping[str, Any], walls: Mapping[str, Any], *,
                     floor_z: float = DEFAULT_FLOOR_Z_M,
                     wall_min_height_m: float = DEFAULT_WALL_MIN_HEIGHT_M,
                     gap_min_m: float = DEFAULT_GAP_MIN_M,
                     gap_max_m: float = DEFAULT_GAP_MAX_M,
                     far_factor_m: float = DEFAULT_FAR_FACTOR_M,
                     tolerance_m: float = DEFAULT_CORROBORATION_TOLERANCE_M,
                     approach_m: float = DEFAULT_DOOR_APPROACH_M) -> list[dict[str, Any]]:
    """Door candidates where the measured boundary leaks through a wall gap.

    A maximal run of far-or-open rays flanked by closed rays is an opening; it
    becomes a doorway only when both flank points sit on standing static wall
    cuboids and the flank-to-flank width is a plausible door width. Everything
    else stays an honest uncorroborated opening, never an invented threshold.
    """
    rays = list(interval.get("rays", []))
    if interval.get("status") != "measured" or len(rays) < 3:
        return []
    band = _wall_band_cuboids(walls, floor_z=floor_z, wall_min_height_m=wall_min_height_m)
    median = interval.get("closed_median_distance_m")
    markers = [_sector_far(ray, median, far_factor_m) for ray in rays]
    openings: list[dict[str, Any]] = []
    count = len(rays)
    index = 0
    while index < count:
        if not markers[index]:
            index += 1
            continue
        start = index
        while index < count and markers[index]:
            index += 1
        end = index - 1  # inclusive far-run bounds
        before, after = (start - 1) % count, index % count
        if not markers[before] and not markers[after]:
            flank_a = rays[before]["boundary_xy_m"]
            flank_b = rays[after]["boundary_xy_m"]
            width = math.dist(flank_a, flank_b)
            far_points = [rays[i]["boundary_xy_m"] for i in range(start, end + 1)]
            farthest = max(far_points, key=lambda p: math.dist(p, interval["seed_xy_m"]))
            threshold = [(flank_a[0] + flank_b[0]) / 2, (flank_a[1] + flank_b[1]) / 2]
            toward_seed = [interval["seed_xy_m"][0] - threshold[0], interval["seed_xy_m"][1] - threshold[1]]
            seed_norm = math.hypot(*toward_seed) or 1.0
            outward = [farthest[0] - threshold[0], farthest[1] - threshold[1]]
            outward_norm = math.hypot(*outward) or 1.0
            corroborated = (_corroborated(flank_a, band, tolerance_m)
                            and _corroborated(flank_b, band, tolerance_m))
            openings.append({
                "id": f"opening_at_angle_{rays[start]['angle_rad']}",
                "status": ("wall_corroborated_doorway" if corroborated and gap_min_m <= width <= gap_max_m
                           else "uncorroborated_opening"),
                "flank_a_xy_m": flank_a,
                "flank_b_xy_m": flank_b,
                "threshold_xy_m": [round(threshold[0], 4), round(threshold[1], 4)],
                "width_m": round(width, 4),
                "inside_xy_m": [round(threshold[0] + toward_seed[0] / seed_norm * approach_m, 4),
                                round(threshold[1] + toward_seed[1] / seed_norm * approach_m, 4)],
                "outside_xy_m": [round(threshold[0] + outward[0] / outward_norm * approach_m, 4),
                                 round(threshold[1] + outward[1] / outward_norm * approach_m, 4)],
                "far_boundary_xy_m": farthest,
                "basis": ("occupancy_far_sector_flanked_by_static_wall_cuboids"
                          if corroborated else "occupancy_far_sector_without_corroborated_wall_flanks"),
            })
    return openings


def derive_region_geometry(occupancy: Any, layer_path: Path | str, seed_xy: Sequence[float], *,
                           walls: Mapping[str, Any] | None = None, **options: Any) -> dict[str, Any]:
    """One region's offline double binding: map interval plus USD wall evidence.

    Pass a shared ``walls`` inventory when deriving many regions against one
    layer; it is enumerated from ``layer_path`` when omitted.
    """
    interval = radial_free_interval(occupancy, seed_xy,
                                    max_radius_m=options.pop("max_radius_m", DEFAULT_MAX_RADIUS_M),
                                    ray_count=options.pop("ray_count", DEFAULT_RAY_COUNT))
    walls = static_wall_cuboids(layer_path) if walls is None else walls
    doors = doorway_openings(interval, walls, **options) if interval["status"] == "measured" else []
    band_count = len(_wall_band_cuboids(walls, floor_z=options.get("floor_z", DEFAULT_FLOOR_Z_M),
                                        wall_min_height_m=options.get("wall_min_height_m", DEFAULT_WALL_MIN_HEIGHT_M)))
    result = {
        "kind": "occupancy_interval_plus_usd_static_wall_cuboids",
        "basis": "derived_offline_from_provider_occupancy_rays_and_authored_bench_collision_layer",
        "status": interval["status"],
        "map_sha256": occupancy.sha256,
        "layer": {"path": walls["path"], "sha256": walls["sha256"], "status": walls["status"],
                  "cuboid_count": walls.get("cuboid_count", 0), "wall_band_cuboid_count": band_count},
        "seed_xy_m": interval.get("seed_xy_m"),
        "interval_aabb_xy_m": interval.get("interval_aabb_xy_m"),
        "boundary_status": ("wall_closed" if interval.get("open_ray_count") == 0 else "partially_open"),
        "open_ray_count": interval.get("open_ray_count"),
        "closed_median_distance_m": interval.get("closed_median_distance_m"),
        "ray_count": interval.get("ray_count"),
        "max_radius_m": interval.get("max_radius_m"),
        "doorways": [door for door in doors if door["status"] == "wall_corroborated_doorway"],
        "uncorroborated_openings": [door for door in doors if door["status"] != "wall_corroborated_doorway"],
        "limits": ["static_map_and_authored_layer_only_no_dynamic_or_furniture_state",
                   "open_directions_capped_at_max_radius_not_physical_walls",
                   "radial_interval_is_star_shaped_from_the_seed_not_a_room_polygon"],
    }
    result["sha256"] = canonical_sha256(result)
    return result


__all__ = [
    "DEFAULT_DOOR_APPROACH_M",
    "HOUSE_LAYER_RELATIVE",
    "derive_region_geometry",
    "doorway_openings",
    "house_bench_layer_path",
    "radial_free_interval",
    "static_wall_cuboids",
]
