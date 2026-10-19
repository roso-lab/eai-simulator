"""Mechanically validate editable semantic-map and Camera-Light batches.

This checker deliberately distinguishes geometry evidence from robot-certified
traversability.  It verifies file integrity and that draft route samples remain
on free support cells, but it does not certify footprint, clearance or dynamics.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np
from PIL import Image
from shapely.geometry import Point, shape

from tools.topology_authoring.product import load_product


def digest(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _cell(x, y, origin, resolution, shape_2d):
    i = int(math.floor((float(x) - float(origin[0])) / float(resolution)))
    j = int(math.floor((float(y) - float(origin[1])) / float(resolution)))
    if not (0 <= j < shape_2d[0] and 0 <= i < shape_2d[1]):
        return None
    return j, i


def _segment_cells(a, b, origin, resolution, shape_2d):
    distance = math.dist(a[:2], b[:2])
    count = max(2, int(math.ceil(distance / (resolution * .35))) + 1)
    cells = []
    for point in np.linspace(np.asarray(a, dtype=float), np.asarray(b, dtype=float), count):
        cell = _cell(point[0], point[1], origin, resolution, shape_2d)
        if not cells or cell != cells[-1]:
            cells.append(cell)
    return cells


def _validate_route(route, document, grids, errors):
    # The builder intentionally emits an editable placeholder edge when a
    # level has no proven free component.  It is a bookkeeping anchor, not a
    # route claim, so it must not be sampled against the free raster.
    if route.get("properties", {}).get("status", "").startswith("unresolved_"):
        return
    nodes = {row["id"]: row for row in document.nodes}
    route_nodes = [nodes[node_id] for node_id in route["node_ids"]]
    if route["kind"] in {"stairs", "ramp"}:
        if route.get("properties", {}).get("status") != "world_endpoints_authored_robot_unvalidated":
            errors.append(f"{route['id']}: vertical route lacks authored-world status")
        return
    for first, last in zip(route_nodes, route_nodes[1:]):
        level_id = first.get("level_id")
        if level_id not in grids or last.get("level_id") != level_id:
            errors.append(f"{route['id']}: flat route crosses or lacks a level")
            continue
        grid = grids[level_id]
        cells = _segment_cells(first["position_xyz"], last["position_xyz"],
                               grid["origin"], grid["resolution"], grid["free"].shape)
        previous = None
        for cell in cells:
            if cell is None or not grid["free"][cell]:
                errors.append(f"{route['id']}: sample leaves free support")
                break
            if previous is not None:
                dj, di = cell[0] - previous[0], cell[1] - previous[1]
                if abs(dj) == 1 and abs(di) == 1:
                    if not (grid["free"][previous[0], cell[1]]
                            and grid["free"][cell[0], previous[1]]):
                        errors.append(f"{route['id']}: diagonal corner cut")
                        break
                elif abs(dj) > 1 or abs(di) > 1:
                    errors.append(f"{route['id']}: route sampling skipped grid cells")
                    break
            previous = cell


def _validate_feature_binding(feature, grid, errors, label):
    geometry = shape(feature.get("geometry"))
    if geometry.is_empty or not geometry.is_valid:
        errors.append(f"{label}: invalid/empty GeoJSON geometry")
        return
    xmin, ymin, xmax, ymax = geometry.bounds
    resolution, origin = grid["resolution"], grid["origin"]
    i0 = max(0, int(math.floor((xmin-origin[0])/resolution))-1)
    i1 = min(grid["support"].shape[1]-1, int(math.floor((xmax-origin[0])/resolution))+1)
    j0 = max(0, int(math.floor((ymin-origin[1])/resolution))-1)
    j1 = min(grid["support"].shape[0]-1, int(math.floor((ymax-origin[1])/resolution))+1)
    target = grid["support"] if feature["properties"].get("feature_type") == "room_region" \
        else grid["semantic_occupied"]
    seen = 0
    for j in range(j0, j1+1):
        y = origin[1] + (j+.5)*resolution
        for i in range(i0, i1+1):
            x = origin[0] + (i+.5)*resolution
            if geometry.covers(Point(x, y)):
                seen += 1
                if not target[j, i]:
                    errors.append(f"{label}: geometry covers a non-target raster cell")
                    return
    if not seen:
        errors.append(f"{label}: geometry binds no raster cell center")


def validate_scene(scene_dir: Path):
    errors = []
    manifest_path = scene_dir / "semantic-manifest.json"
    receipt_path = scene_dir / "completion-receipt.json"
    required = [manifest_path, receipt_path, scene_dir/"topology.json",
                scene_dir/"semantic-footprints.json", scene_dir/"semantic-footprints.geojson"]
    for path in required:
        if not path.is_file():
            errors.append(f"missing {path.name}")
    if errors:
        return errors, 0
    manifest, receipt = _load_json(manifest_path), _load_json(receipt_path)
    if receipt.get("status") != "done" or receipt.get("manifest_sha256") != digest(manifest_path):
        errors.append("invalid completion receipt")
    if manifest.get("source_unchanged") is not True or manifest.get("topology_reopen") is not True:
        errors.append("source/topology manifest flags are not true")
    for source, expected in manifest.get("source_layer_sha256", {}).items():
        path = Path(source)
        if not path.is_file() or digest(path) != expected:
            errors.append(f"source layer digest mismatch: {source}")
    try:
        document = load_product(scene_dir/"topology.json")
    except Exception as exc:
        errors.append(f"topology reopen failed: {exc}")
        return errors, len(manifest.get("levels", {}))

    grids = {}
    for level_id, report in manifest.get("levels", {}).items():
        path = scene_dir / f"{level_id}-semantic-grid.npz"
        if not path.is_file():
            errors.append(f"{level_id}: missing NPZ")
            continue
        with np.load(path) as data:
            needed = {"support", "occupied", "state", "instance_id", "region_id",
                      "origin_xy", "resolution_m"}
            if not needed.issubset(data.files):
                errors.append(f"{level_id}: NPZ keys missing {sorted(needed-set(data.files))}")
                continue
            support = np.asarray(data["support"], dtype=bool)
            occupied = np.asarray(data["occupied"], dtype=bool)
            semantic_occupied = np.asarray(
                data["semantic_occupied"] if "semantic_occupied" in data.files
                else data["occupied"], dtype=bool)
            state = np.asarray(data["state"])
            if not (support.shape == occupied.shape == semantic_occupied.shape == state.shape):
                errors.append(f"{level_id}: raster shape mismatch")
                continue
            free = support & ~occupied
            counts = (int(support.sum()), int(occupied.sum()), int(free.sum()))
            expected = (int(report.get("support_cells", -1)),
                        int(report.get("occupied_cells", -1)),
                        int(report.get("free_cells", -1)))
            route_status = report.get("route_status", "")
            unresolved_grid = route_status in {
                "unresolved_no_support", "unresolved_no_free_component",
                "unresolved_no_routeable_component",
            }
            if (counts != expected
                    or (not unresolved_grid and (counts[0] < 4 or counts[2] < 2))):
                errors.append(f"{level_id}: raster counts invalid {counts} != {expected}")
            semantic_expected = report.get("semantic_occupied_cells")
            if (semantic_expected is not None
                    and int(semantic_occupied.sum()) != int(semantic_expected)):
                errors.append(
                    f"{level_id}: semantic occupied count invalid "
                    f"{int(semantic_occupied.sum())} != {semantic_expected}")
            if not (np.all(state[occupied] == 1)
                    and np.all(state[free] == 0)
                    and np.all(state[~(support|occupied)] == -1)):
                errors.append(f"{level_id}: state/support/occupied contract mismatch")
            grids[level_id] = {"support":support,"occupied":occupied,
                "semantic_occupied":semantic_occupied,"free":free,
                "origin":np.asarray(data["origin_xy"], dtype=float),
                "resolution":float(np.asarray(data["resolution_m"]).item())}
        image = scene_dir / f"{level_id}-semantic-map.png"
        if not image.is_file():
            errors.append(f"{level_id}: missing semantic map PNG")
        else:
            with Image.open(image) as opened:
                opened.load()
                if min(opened.size) <= 0 or np.asarray(opened.convert("RGB")).std() <= 1:
                    errors.append(f"{level_id}: blank/invalid semantic map PNG")

    semantics = document.semantics or {}
    for route in semantics.get("routes", []):
        _validate_route(route, document, grids, errors)
    nodes = {row["id"]: row for row in document.nodes}
    anchor_counts = {level_id: 0 for level_id in grids}
    for region in semantics.get("regions", []):
        anchor_id = region.get("properties", {}).get("topology_anchor_node_id")
        if anchor_id is None:
            continue
        node = nodes.get(anchor_id)
        level_id = region.get("level_id")
        if node is None or level_id not in grids:
            errors.append(f"{region['id']}: missing/unknown topology region anchor")
            continue
        if node.get("kind") != "region_anchor" or node.get("level_id") != level_id:
            errors.append(f"{region['id']}: invalid topology region anchor metadata")
            continue
        grid = grids[level_id]
        cell = _cell(*node["position_xyz"][:2], grid["origin"], grid["resolution"],
                     grid["free"].shape)
        if cell is None or not grid["free"][cell]:
            errors.append(f"{region['id']}: topology region anchor is not on free support")
            continue
        polygon = shape({"type":"Polygon", "coordinates":[[
            *[point[:2] for point in region["polygon_xyz"]],
            region["polygon_xyz"][0][:2]] ]})
        if not polygon.covers(Point(*node["position_xyz"][:2])):
            errors.append(f"{region['id']}: topology region anchor leaves its region")
            continue
        anchor_counts[level_id] += 1
    for level_id, count in anchor_counts.items():
        expected = manifest["levels"][level_id].get("semantic_region_anchor_count")
        if expected is not None and count != int(expected):
            errors.append(f"{level_id}: semantic region anchor count invalid {count} != {expected}")
    geojson = _load_json(scene_dir/"semantic-footprints.geojson")
    for index, feature in enumerate(geojson.get("features", [])):
        level_id = feature.get("properties", {}).get("level_id")
        if level_id in grids:
            _validate_feature_binding(feature, grids[level_id], errors,
                                      f"feature[{index}]/{level_id}")
        else:
            errors.append(f"feature[{index}]: unknown level {level_id}")
    return sorted(set(errors)), len(grids)


def validate_capture(scene_dir: Path):
    errors = []
    report_path, receipt_path = scene_dir/"capture.json", scene_dir/"capture-complete.json"
    if not report_path.is_file() or not receipt_path.is_file():
        return ["missing capture report/receipt"], 0
    report, receipt = _load_json(report_path), _load_json(receipt_path)
    if (report.get("capture_contract_version") != 2 or report.get("camera_light") is not True
            or report.get("source_unchanged") is not True or receipt.get("complete") is not True
            or receipt.get("capture_report_sha256") != digest(report_path)):
        errors.append("capture v2/report/receipt contract failed")
    rows = receipt.get("images", {})
    for view in report.get("views", []):
        level_id = view.get("level")
        images = rows.get(level_id, [])
        if len(images) != 5 or len({row.get("path") for row in images}) != 5:
            errors.append(f"{level_id}: expected five distinct capture images")
            continue
        for row in images:
            path = Path(row["path"])
            if not path.is_file() or digest(path) != row.get("sha256"):
                errors.append(f"{level_id}: image missing/hash mismatch: {path}")
                continue
            with Image.open(path) as opened:
                opened.load()
                if list(opened.size) != row.get("pixel_size") or np.asarray(opened.convert("RGB")).std() <= 1:
                    errors.append(f"{level_id}: image size/flatness mismatch: {path}")
    return sorted(set(errors)), len(report.get("views", []))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-root", type=Path, action="append", default=[])
    parser.add_argument("--capture-root", type=Path, action="append", default=[])
    parser.add_argument("--expected-scenes", type=int)
    parser.add_argument("--expected-levels", type=int)
    args = parser.parse_args()
    results, scene_count, level_count = {}, 0, 0
    for root in args.batch_root:
        batch = _load_json(root/"batch-manifest.json")
        if batch.get("complete") is not True or batch.get("failures"):
            results[str(root)] = ["batch manifest incomplete"]
        for scene_id in batch.get("scenes", {}):
            errors, levels = validate_scene(root/scene_id)
            results[f"map:{scene_id}"] = errors
            scene_count += 1; level_count += levels
    for root in args.capture_root:
        for scene_dir in sorted(path for path in root.iterdir() if path.is_dir()):
            errors, levels = validate_capture(scene_dir)
            results[f"capture:{scene_dir.name}"] = errors
    if args.expected_scenes is not None and scene_count != args.expected_scenes:
        results["totals:scenes"] = [f"{scene_count} != {args.expected_scenes}"]
    if args.expected_levels is not None and level_count != args.expected_levels:
        results["totals:levels"] = [f"{level_count} != {args.expected_levels}"]
    failed = {key:value for key,value in results.items() if value}
    print(json.dumps({"ok":not failed,"scene_count":scene_count,"level_count":level_count,
                      "failures":failed},ensure_ascii=False,indent=2))
    raise SystemExit(bool(failed))


if __name__ == "__main__":
    main()
