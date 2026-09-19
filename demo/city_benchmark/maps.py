"""Conservative static map products from the city layout and composed USD.

No AppLauncher, downloads, asset writes, accident selection or dynamic truth.
Image row zero is north; ROS map origin is the bottom-left cell corner.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

from demo.city_benchmark.scene.usd_scene import compose_city, discover_buildings, discover_obstacles


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_sources(asset_root: Path):
    paths = {name: asset_root / name for name in ("city_layout.json", "city_scenario.json", "city_static.usd")}
    for path in paths.values():
        if not path.is_file():
            raise FileNotFoundError(f"Install the local city asset first: {path}")
    layout = json.loads(paths["city_layout.json"].read_text(encoding="utf-8"))
    scenario = json.loads(paths["city_scenario.json"].read_text(encoding="utf-8"))
    if layout.get("coordinate_system") != {"meters_per_unit": 1.0, "up_axis": "Z"}:
        raise ValueError("city layout must use metres and Z-up")
    if layout.get("template_id") != scenario.get("template_id"):
        raise ValueError("layout and traffic template ids differ")
    if not scenario.get("vehicle_routes") or not scenario.get("pedestrian_routes"):
        raise ValueError("city requires authored traffic and pedestrian routes")
    # layout_digest has no declared canonicalization; bind actual bytes instead
    # of pretending that its equality to an invented hash algorithm was checked.
    provenance = {
        "source_sha256": {name: file_hash(path) for name, path in paths.items()},
        "declared_layout_digest": scenario.get("layout_digest"),
        "layout_digest_status": "unverified_upstream_algorithm",
    }
    return layout, scenario, provenance


def inspect_geometry(asset_root: Path):
    from pxr import Usd, UsdGeom

    asset = asset_root / "city_static.usd"
    original = Usd.Stage.Open(str(asset.resolve()))
    if original is None:
        raise ValueError("could not open city USD")
    if (UsdGeom.GetStageUpAxis(original) != "Z"
            or abs(UsdGeom.GetStageMetersPerUnit(original) - 1.0) > 1e-9):
        raise ValueError("city USD must use metres and Z-up")
    # Isaac references defaultPrim /World at /World/City. Opening the asset and
    # querying /World/City directly would silently miss all manual buildings.
    stage = Usd.Stage.CreateInMemory()
    compose_city(stage, asset=asset)
    if stage.GetCompositionErrors():
        raise ValueError(f"city USD has composition errors: {stage.GetCompositionErrors()}")
    buildings = discover_buildings(stage)
    if not buildings:
        raise ValueError("no visible buildings in composed city; refusing an empty obstacle map")
    obstacles = discover_obstacles(stage)
    rows = []
    for i, building in enumerate(buildings):
        lo, hi = building.bounds
        # The demo seats every building. XY is unchanged; reflect its Z shift
        # arithmetically without authoring collision or transform overrides.
        delta = 0.22 - lo[2]
        rows.append({"id": f"building_{i + 1:03d}", "kind": "building",
                     "bounds_m": [[lo[0], lo[1], lo[2] + delta], [hi[0], hi[1], hi[2] + delta]],
                     "runtime_prim_path": building.path})
    for i, (bounds, meta) in enumerate(obstacles):
        rows.append({"id": f"roadside_{i + 1:03d}", "kind": "roadside_obstacle",
                     "bounds_m": [list(bounds[0]), list(bounds[1])], "runtime_prim_path": meta["path"]})
    # Bus shelters are not included by the existing trees/lamps helper.
    root = stage.GetPrimAtPath("/World/City")
    cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), ["default", "render", "proxy"])
    for prim in root.GetChildren():
        if prim.GetName().lower().startswith("busstation"):
            box = cache.ComputeRelativeBound(prim, root).ComputeAlignedRange()
            rows.append({"id": f"shelter_{len(rows):03d}", "kind": "bus_shelter",
                         "bounds_m": [list(box.GetMin()), list(box.GetMax())],
                         "runtime_prim_path": str(prim.GetPath())})
    layer_hashes = {layer.realPath: file_hash(Path(layer.realPath))
                    for layer in stage.GetUsedLayers() if layer.realPath}
    return rows, layer_hashes


def rasterize(layout: dict, obstacles: list[dict], *, resolution_m: float, inflation_m: float):
    """Return a conservative ROS occupancy image and unpadded semantic geometry."""
    import numpy as np
    from shapely.geometry import box, shape
    from shapely.ops import unary_union
    from shapely import contains_xy, intersects_xy

    if not math.isfinite(resolution_m) or resolution_m < 0.05:
        raise ValueError("resolution_m must be finite and at least 0.05")
    if not math.isfinite(inflation_m) or inflation_m < 0:
        raise ValueError("inflation_m must be finite and nonnegative")
    geometry = layout["geometry"]
    boundary, road, sidewalk = (shape(geometry[key]) for key in ("boundary", "road_surface", "sidewalk_surface"))
    if any(g.is_empty or not g.is_valid for g in (boundary, road, sidewalk)):
        raise ValueError("city surfaces must be nonempty valid polygon geometry")
    x0, y0, x1, y1 = boundary.bounds
    width, height = math.ceil((x1 - x0) / resolution_m), math.ceil((y1 - y0) / resolution_m)
    if width * height > 20_000_000:
        raise ValueError("map exceeds the authoring export cell limit")
    # Reject cells whose square touches the occupied set, not only centers.
    half_diagonal = resolution_m / math.sqrt(2)
    obstacle_shapes = []
    for row in obstacles:
        lo, hi = row["bounds_m"]
        if not all(math.isfinite(v) for v in (*lo, *hi)) or any(hi[i] <= lo[i] for i in range(3)):
            raise ValueError("invalid obstacle bounds")
        obstacle_shapes.append(box(lo[0], lo[1], hi[0], hi[1]))
    occupied = unary_union(obstacle_shapes)
    # Road/sidewalk curb boundaries are blocked until morphology-specific
    # curb traversal has evidence; no stairs or curb ability is inferred.
    curb = sidewalk.boundary.buffer(float(layout.get("materials", {}).get("curbs", {}).get("width", 0.35)) / 2)
    blocked = unary_union([occupied, curb]).buffer(inflation_m + half_diagonal)
    support = road.union(sidewalk).intersection(boundary).buffer(-(inflation_m + half_diagonal))
    xs = x0 + (np.arange(width) + 0.5) * resolution_m
    ys = y0 + (height - np.arange(height) - 0.5) * resolution_m
    xx, yy = np.meshgrid(xs, ys)
    grid = np.full((height, width), 205, dtype=np.uint8)
    grid[contains_xy(support, xx, yy)] = 254
    grid[intersects_xy(blocked, xx, yy)] = 0
    metadata = {
        "schema": "eai.city.static_map", "version": 1,
        "coordinate_frame": "city_world", "meters_per_unit": 1.0, "up_axis": "Z",
        "resolution_m": resolution_m, "origin_xyz_m": [x0, y0, 0.0],
        "width": width, "height": height, "inflation_m": inflation_m,
        "image_row_zero": "north", "occupancy_values": {"occupied": 0, "unknown": 205, "free": 254},
        "cells": {"free": int((grid == 254).sum()), "occupied": int((grid == 0).sum()),
                  "unknown": int((grid == 205).sum())},
        "status": "static_geometry_candidate", "navigation_validated": False,
        "aerial_use": "use 3D bounds and runtime sensors; this 2D image is not an aerial route certificate",
        "excluded_dynamic_layers": ["vehicles", "people", "fire", "smoke", "robot_footprints"],
        "limitations": ["render_bounds_not_PhysX_measurements", "unmodelled_static_geometry_may_exist",
                        "unknown_cells_are_not_free", "curb_crossing_requires_separate_validation",
                        "no_fire_or_smoke_safety_certificate"],
    }
    return grid, metadata


def semantic_map(layout: dict, scenario: dict, obstacles: list[dict], metadata: dict, grid):
    """Static public regions; future accident targets are never selected here."""
    import numpy as np
    from shapely.geometry import Point, shape

    regions, bindings = [], []
    def add(identifier, kind, description, access, geometry, **extra):
        regions.append({"id": identifier, "kind": kind, "public_description": description,
                        "access_modes": access, "resource_capacity": 1})
        bindings.append({"id": identifier, "geometry": geometry, **extra})

    for key, kind, description in (("road_surface", "road_network", "城市道路与应急车辆通行网络"),
                                    ("sidewalk_surface", "pedestrian_network", "城市人行道与街边通行网络")):
        add(key, kind, description, ["ground_navigation", "aerial_navigation"], layout["geometry"][key])
    for row in obstacles:
        if row["kind"] != "building":
            continue
        lo, hi = row["bounds_m"]
        # The region denotes an exterior observation target.  Ground/aerial
        # access means an approach to its admitted exterior viewpoint; the
        # occupancy map still blocks traversal through the building footprint.
        add(row["id"], "building_exterior", "建筑外围观察对象；内部通行未建模",
            ["ground_navigation", "aerial_navigation"],
            {"type": "Polygon", "coordinates": [[[lo[0], lo[1]], [hi[0], lo[1]], [hi[0], hi[1]],
                                                     [lo[0], hi[1]], [lo[0], lo[1]]]]})
    for i, crosswalk in enumerate(scenario["crosswalks"]):
        add(f"crossing_{i + 1:03d}", "crosswalk", "需要依据实时交通状态确认的过街区域",
            ["ground_navigation"], crosswalk["polygon"], crossing_id=crosswalk["crosswalk_id"],
            lane_stop_distances=crosswalk["lane_stop_distances"])

    # Static candidate collection locations, independent of which building burns.
    # A sample is retained only if its 0.75 m disk lies on authored sidewalk
    # and touches exclusively free map cells. This is not dynamic safety.
    sidewalks = shape(layout["geometry"]["sidewalk_surface"])
    res = metadata["resolution_m"]
    x0, y0, _ = metadata["origin_xyz_m"]
    h, w = grid.shape
    for route in scenario["pedestrian_routes"]:
        chosen = None
        for j, (a, b) in enumerate(zip(route["points"], route["points"][1:])):
            if route["segment_crosswalk_ids"][j] is not None or math.dist(a, b) < 6:
                continue
            length = math.dist(a, b)
            normal = (-(b[1] - a[1]) / length, (b[0] - a[0]) / length)
            # Walking centerlines often hug a curb. Search bounded lateral
            # offsets while retaining the same disk and occupancy criteria.
            for fraction in (.5, .25, .75):
                for offset in (0.0, 1.25, -1.25, 2.5, -2.5):
                    x, y = (a[k] + fraction * (b[k] - a[k]) + offset * normal[k] for k in range(2))
                    disk = Point(x, y).buffer(0.75)
                    if not sidewalks.covers(disk):
                        continue
                    left, right = math.floor((x - .75 - x0) / res), math.floor((x + .75 - x0) / res)
                    top, bottom = h - 1 - math.floor((y + .75 - y0) / res), h - 1 - math.floor((y - .75 - y0) / res)
                    if not (0 <= left <= right < w and 0 <= top <= bottom < h):
                        continue
                    if np.all(grid[top:bottom + 1, left:right + 1] == 254):
                        chosen = (x, y)
                        break
                if chosen:
                    break
            if chosen:
                break
        if chosen:
            add(f"assembly_candidate_{len([r for r in regions if r['kind'] == 'assembly_candidate']) + 1:03d}",
                "assembly_candidate", "候选集合位置；需检查实时危险、容量和可达性后才能采用",
                ["ground_navigation"], {"type": "Point", "coordinates": list(chosen)},
                candidate_radius_m=.75, capacity_people=None, safety_validated=False)
    return {
        "schema": "eai.city.semantic_map", "version": 1, "coordinate_frame": "city_world",
        "meters_per_unit": 1.0, "up_axis": "Z", "regions": regions, "bindings": bindings,
        "static_obstacles": [{k: row[k] for k in ("id", "kind", "bounds_m")} for row in obstacles],
        "vehicle_routes": [{"id": r["route_id"], "points": r["points"]} for r in scenario["vehicle_routes"]],
        "pedestrian_routes": [{"id": r["route_id"], "points": r["points"]} for r in scenario["pedestrian_routes"]],
        "surface_heights_m": layout["surface_heights"],
        "scope": "static_prior_only; routes_do_not_describe_current_actor_positions_or_incident_trajectory",
    }


def write_map_products(output: Path, grid, metadata: dict, semantics: dict):
    from PIL import Image, ImageDraw
    import numpy as np
    import yaml

    output.mkdir(parents=True, exist_ok=False)
    Image.fromarray(grid).save(output / "occupancy.png")
    nav = {"image": "occupancy.png", "resolution": metadata["resolution_m"],
           "origin": metadata["origin_xyz_m"], "negate": 0, "occupied_thresh": .65, "free_thresh": .196}
    (output / "occupancy.yaml").write_text(yaml.safe_dump(nav, sort_keys=False), encoding="utf-8")
    for name, data in (("map_metadata.json", metadata), ("semantic_map.json", semantics)):
        (output / name).write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    rgb = np.full((*grid.shape, 3), (94, 105, 119), dtype=np.uint8)
    rgb[grid == 254] = (225, 238, 231)
    rgb[grid == 0] = (26, 36, 48)
    canvas = Image.fromarray(rgb)
    draw = ImageDraw.Draw(canvas)
    x0, y0, _ = metadata["origin_xyz_m"]
    res, height = metadata["resolution_m"], metadata["height"]
    def pixel(p): return ((p[0] - x0) / res, height - (p[1] - y0) / res)
    for route in semantics["vehicle_routes"]:
        draw.line([pixel(p) for p in route["points"]], fill=(215, 146, 38), width=2)
    for route in semantics["pedestrian_routes"]:
        draw.line([pixel(p) for p in route["points"]], fill=(29, 142, 194), width=2)
    for row in semantics["bindings"]:
        if row["id"].startswith("assembly_candidate"):
            x, y = pixel(row["geometry"]["coordinates"])
            draw.ellipse((x - 4, y - 4, x + 4, y + 4), fill=(207, 74, 107))
    draw.rectangle((5, 5, min(650, canvas.width - 5), 48), fill="white")
    draw.text((12, 10), "CITY STATIC CANDIDATE | north up | not collision/evacuation validated", fill="black")
    draw.text((12, 27), "orange: traffic routes | blue: walking routes | pink: assembly candidates", fill="black")
    canvas.save(output / "map_preview.png")
