"""Build editable geometry-first semantic maps for the Living/public scene set.

The raster is collision-height surface evidence, not a universal robot
traversability certificate.  Visual labels are optional authored overlays;
everything without evidence remains explicitly unknown.
"""
from __future__ import annotations

import argparse
from collections import deque
import hashlib
import heapq
import json
import math
import os
from pathlib import Path
import tempfile

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap, to_rgb
from matplotlib.patches import Patch, Polygon as PlotPolygon
import numpy as np
from scipy import ndimage
from shapely.geometry import box, mapping
from shapely.ops import unary_union

from tools.topology_authoring.examples.render_office_navigation import rasterize, rasterize_heightfield
from tools.topology_authoring.model import TopologyDocument
from tools.topology_authoring.product import generate_product, load_product


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DESCRIPTOR = ROOT / "tools/topology_authoring/maps/living_public_scenes.json"
PALETTE = ["#3977a8", "#b56f36", "#54845c", "#8a62a4", "#b04f5f", "#857348", "#4f8a8b"]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fan_triangles(indices, counts):
    """Triangulate authored polygons without changing vertex order."""
    faces, offset = [], 0
    for count in counts:
        count = int(count)
        face = indices[offset:offset+count]
        offset += count
        for k in range(1, count-1):
            faces.append((int(face[0]), int(face[k]), int(face[k+1])))
    return np.asarray(faces, dtype=int).reshape(-1, 3)


def _cube_geometry(cube):
    """Return the canonical finite UsdGeomCube surface in local coordinates."""
    from pxr import UsdGeom
    size = cube.GetSizeAttr().Get()
    size = 2.0 if size is None else float(size)
    if not math.isfinite(size) or size <= 0:
        raise ValueError(f"invalid Cube size at {cube.GetPath()}: {size}")
    half = size / 2
    points = np.asarray([
        [-half, -half, -half], [half, -half, -half],
        [half, half, -half], [-half, half, -half],
        [-half, -half, half], [half, -half, half],
        [half, half, half], [-half, half, half],
    ], dtype=float)
    triangles = np.asarray([
        [0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7],
        [0, 1, 5], [0, 5, 4], [1, 2, 6], [1, 6, 5],
        [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7],
    ], dtype=int)
    if cube.GetOrientationAttr().Get() == UsdGeom.Tokens.leftHanded:
        triangles = triangles[:, [0, 2, 1]]
    return points, triangles


def _plane_geometry(plane):
    """Return the canonical finite UsdGeomPlane surface in local coordinates."""
    from pxr import UsdGeom
    width = plane.GetWidthAttr().Get()
    length = plane.GetLengthAttr().Get()
    width = 2.0 if width is None else float(width)
    length = 2.0 if length is None else float(length)
    if not (math.isfinite(width) and width > 0 and math.isfinite(length) and length > 0):
        raise ValueError(f"invalid Plane dimensions at {plane.GetPath()}: {width} x {length}")
    w, l = width / 2, length / 2
    axis = plane.GetAxisAttr().Get() or UsdGeom.Tokens.z
    if axis == UsdGeom.Tokens.x:
        points = [[0, -w, -l], [0, w, -l], [0, w, l], [0, -w, l]]
    elif axis == UsdGeom.Tokens.y:
        points = [[-w, 0, -l], [-w, 0, l], [w, 0, l], [w, 0, -l]]
    elif axis == UsdGeom.Tokens.z:
        points = [[-w, -l, 0], [w, -l, 0], [w, l, 0], [-w, l, 0]]
    else:
        raise ValueError(f"invalid Plane axis at {plane.GetPath()}: {axis}")
    triangles = np.asarray([[0, 1, 2], [0, 2, 3]], dtype=int)
    if plane.GetOrientationAttr().Get() == UsdGeom.Tokens.leftHanded:
        triangles = triangles[:, [0, 2, 1]]
    return np.asarray(points, dtype=float), triangles


def _world_triangles(prim, points, triangle_indices, cache, meters):
    matrix = np.asarray(cache.GetLocalToWorldTransform(prim), dtype=float)
    world = np.c_[points, np.ones(len(points))] @ matrix
    return world[:, :3][triangle_indices] * meters


def stage_triangles(usd_path: Path, *, force_static=False, geometry_source="auto",
                    roi_xy=None, z_range=None):
    """Return metre-scaled world triangles, tolerating empty collision meshes.

    Enabled collision Mesh, Cube and Plane geometry is preferred.  Other
    analytic collision types are reported but never approximated as boxes.  A
    static visible-Mesh fallback is used only when collision geometry lacks a
    plausible horizontal support area.
    """
    from pxr import Usd, UsdGeom, UsdPhysics
    stage = Usd.Stage.Open(str(usd_path))
    if stage is None:
        raise ValueError(f"cannot open USD: {usd_path}")
    if UsdGeom.GetStageUpAxis(stage) != "Z":
        raise ValueError(f"only Z-up scenes are supported: {usd_path}")
    meters = float(UsdGeom.GetStageMetersPerUnit(stage))
    cache = UsdGeom.XformCache()

    if geometry_source not in {"auto", "collision_geometry", "static_mesh_fallback"}:
        raise ValueError(f"unsupported geometry_source: {geometry_source}")
    if roi_xy is not None:
        if (len(roi_xy) != 4 or not np.isfinite(np.asarray(roi_xy, dtype=float)).all()
                or not (roi_xy[0] < roi_xy[2] and roi_xy[1] < roi_xy[3])):
            raise ValueError(f"invalid geometry ROI: {roi_xy}")
        roi_xy = tuple(map(float, roi_xy))
    if z_range is not None:
        if (len(z_range) != 2 or not np.isfinite(np.asarray(z_range, dtype=float)).all()
                or not z_range[0] < z_range[1]):
            raise ValueError(f"invalid geometry Z range: {z_range}")
        z_range = tuple(map(float, z_range))

    def collect(collision_only):
        chunks = []
        paths = {"Mesh": [], "Cube": [], "Plane": []}
        unsupported = {}
        empty = nontri = filtered = 0
        for prim in stage.Traverse():
            if collision_only:
                if (not prim.HasAPI(UsdPhysics.CollisionAPI)
                        or UsdPhysics.CollisionAPI(prim).GetCollisionEnabledAttr().Get() is False):
                    continue
            elif not prim.IsA(UsdGeom.Mesh):
                continue

            if prim.IsA(UsdGeom.Mesh):
                mesh = UsdGeom.Mesh(prim)
                points = np.asarray(mesh.GetPointsAttr().Get(), dtype=float)
                counts = np.asarray(mesh.GetFaceVertexCountsAttr().Get(), dtype=int)
                indices = np.asarray(mesh.GetFaceVertexIndicesAttr().Get(), dtype=int)
                if not len(points) or not len(counts) or not len(indices):
                    empty += 1
                    continue
                if not np.all(counts == 3):
                    tri_indices = _fan_triangles(indices, counts)
                    if not len(tri_indices):
                        nontri += 1
                        continue
                    nontri += 1
                else:
                    tri_indices = indices.reshape(-1, 3)
                kind = "Mesh"
            elif collision_only and prim.IsA(UsdGeom.Cube):
                points, tri_indices = _cube_geometry(UsdGeom.Cube(prim))
                kind = "Cube"
            elif collision_only and prim.IsA(UsdGeom.Plane):
                points, tri_indices = _plane_geometry(UsdGeom.Plane(prim))
                kind = "Plane"
            else:
                kind = prim.GetTypeName() or "untyped"
                unsupported.setdefault(kind, []).append(str(prim.GetPath()))
                continue

            world_triangles = _world_triangles(prim, points, tri_indices, cache, meters)
            keep = np.ones(len(world_triangles), dtype=bool)
            if roi_xy is not None:
                lower_xy = world_triangles[:, :, :2].min(1)
                upper_xy = world_triangles[:, :, :2].max(1)
                keep &= ((upper_xy[:, 0] >= roi_xy[0]) & (lower_xy[:, 0] <= roi_xy[2])
                         & (upper_xy[:, 1] >= roi_xy[1]) & (lower_xy[:, 1] <= roi_xy[3]))
            if z_range is not None:
                lower_z = world_triangles[:, :, 2].min(1)
                upper_z = world_triangles[:, :, 2].max(1)
                keep &= (upper_z >= z_range[0]) & (lower_z <= z_range[1])
            filtered += int(len(world_triangles) - keep.sum())
            if not keep.any():
                continue
            chunks.append(world_triangles[keep])
            paths[kind].append(str(prim.GetPath()))
        return dict(chunks=chunks, paths=paths, empty=empty, nontri=nontri,
                    unsupported=unsupported, filtered=filtered)

    collision = collect(True)
    plausible = False
    if collision["chunks"]:
        joined = np.concatenate(collision["chunks"])
        normals = np.cross(joined[:, 1]-joined[:, 0], joined[:, 2]-joined[:, 0])
        norm = np.linalg.norm(normals, axis=1)
        horizontal_area = float((norm[np.abs(normals[:, 2]) >= .985*norm] / 2).sum())
        plausible = horizontal_area >= 2.0
    if force_static or geometry_source == "static_mesh_fallback":
        selected = collect(False)
        mode = "static_mesh_fallback"
    elif geometry_source == "collision_geometry":
        selected = collision
        mode = ("collision_geometry" if collision["paths"]["Cube"] or collision["paths"]["Plane"]
                else "collision_mesh")
    else:
        if not plausible:
            selected = collect(False)
            mode = "static_mesh_fallback"
        else:
            selected = collision
            mode = ("collision_geometry" if collision["paths"]["Cube"] or collision["paths"]["Plane"]
                    else "collision_mesh")
    if not selected["chunks"]:
        raise ValueError(f"no usable mesh triangles: {usd_path}")
    triangles = np.concatenate(selected["chunks"])
    layers = {str(Path(layer.realPath).resolve()): digest(Path(layer.realPath))
              for layer in stage.GetUsedLayers() if layer.realPath}
    selected_counts = {kind: len(selected["paths"][kind]) for kind in selected["paths"]}
    collision_counts = {kind: len(collision["paths"][kind]) for kind in collision["paths"]}
    return triangles, dict(
        mode=mode, mesh_count=selected_counts["Mesh"], cube_count=selected_counts["Cube"],
        plane_count=selected_counts["Plane"], geometry_primitive_counts=selected_counts,
        collision_primitive_counts=collision_counts,
        unsupported_collision_primitive_counts={kind: len(paths) for kind, paths in
                                                sorted(collision["unsupported"].items())},
        unsupported_collision_primitive_paths={kind: paths for kind, paths in
                                               sorted(collision["unsupported"].items())},
        empty_meshes_skipped=selected["empty"],
        polygon_meshes_triangulated=selected["nontri"],
        triangles_filtered_before_batch_concat=selected["filtered"],
        geometry_roi_xy=list(roi_xy) if roi_xy is not None else None,
        geometry_z_range=list(z_range) if z_range is not None else None,
        mesh_paths=selected["paths"]["Mesh"], meters_per_unit=meters,
        source_layer_sha256=layers)


def row_runs(mask):
    runs = []
    for j, row in enumerate(mask):
        edges = np.diff(np.r_[False, row, False].astype(np.int8))
        runs.extend([int(j), int(start), int(end)] for start, end in
                    zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)))
    return runs


def mask_geometry(mask, origin, resolution):
    pieces = [box(origin[0]+start*resolution, origin[1]+j*resolution,
                  origin[0]+end*resolution, origin[1]+(j+1)*resolution)
              for j, start, end in row_runs(mask)]
    if not pieces:
        return None
    return mapping(unary_union(pieces))


def normalize_bounds(bounds, extent):
    """Map top-view image bounds into world XY.

    The Camera-Light evidence uses image coordinates with ``u`` increasing
    right and ``v`` increasing down.  The orthographic camera is aligned with
    world X right and world Y up, so the v axis must be inverted here.
    """
    u0, v0, u1, v1 = bounds
    xmin, xmax, ymin, ymax = extent
    if not (0 <= u0 < u1 <= 1 and 0 <= v0 < v1 <= 1):
        raise ValueError(f"normalized bounds outside [0,1]: {bounds}")
    return [xmin+u0*(xmax-xmin), ymax-v1*(ymax-ymin),
            xmin+u1*(xmax-xmin), ymax-v0*(ymax-ymin)]


def selector_mask(bounds, x, y, base):
    x0, y0, x1, y1 = bounds
    return base & (y[:, None] >= y0) & (y[:, None] <= y1) & \
        (x[None, :] >= x0) & (x[None, :] <= x1)


def scope_visual_semantics(scene_id, level_id, regions, objects):
    """Give image-authored local IDs a deterministic scene/level namespace."""
    region_ids = {
        row["id"]: f"region:{scene_id}:{level_id}:{row['id']}" for row in regions}
    scoped_regions = [
        {**row, "id":region_ids[row["id"]], "source_semantic_id":row["id"]}
        for row in regions]
    scoped_objects = []
    for row in objects:
        source_region = row.get("region_id")
        scoped_objects.append({
            **row,
            "id":f"entity:{scene_id}:{level_id}:{row['id']}",
            "source_semantic_id":row["id"],
            **({"region_id":region_ids[source_region]}
               if source_region in region_ids else {}),
        })
    return scoped_regions, scoped_objects


def nearest_free(seed, free):
    j, i = seed
    if 0 <= j < free.shape[0] and 0 <= i < free.shape[1] and free[j, i]:
        return j, i
    points = np.argwhere(free)
    if not len(points):
        raise ValueError("level has no free support cells")
    return tuple(points[np.argmin(((points-np.asarray(seed))**2).sum(axis=1))])


def farthest_cell(start, component):
    q = deque([start]); distance = {start: 0}; far = start
    while q:
        cell = q.popleft()
        if distance[cell] > distance[far]:
            far = cell
        j, i = cell
        for dj, di in ((-1,0),(1,0),(0,-1),(0,1)):
            nxt = (j+dj, i+di)
            if (0 <= nxt[0] < component.shape[0] and 0 <= nxt[1] < component.shape[1]
                    and component[nxt] and nxt not in distance):
                distance[nxt] = distance[cell] + 1
                q.append(nxt)
    return far


def astar(start, goal, free):
    frontier = [(0.0, start)]; came = {start: None}; cost = {start: 0.0}
    while frontier:
        _, current = heapq.heappop(frontier)
        if current == goal:
            break
        j, i = current
        for dj, di, step in ((-1,0,1),(1,0,1),(0,-1,1),(0,1,1),
                             (-1,-1,math.sqrt(2)),(-1,1,math.sqrt(2)),
                             (1,-1,math.sqrt(2)),(1,1,math.sqrt(2))):
            nxt = (j+dj, i+di)
            if not (0 <= nxt[0] < free.shape[0] and 0 <= nxt[1] < free.shape[1] and free[nxt]):
                continue
            # A diagonal move may not squeeze between two blocked cardinal
            # neighbours.  Checking only the destination lets a zero-radius
            # centerline cut through an occupied cell corner.
            if dj and di and not (free[j, i+di] and free[j+dj, i]):
                continue
            new = cost[current] + step
            if new < cost.get(nxt, float("inf")):
                cost[nxt] = new; came[nxt] = current
                heuristic = math.hypot(goal[0]-nxt[0], goal[1]-nxt[1])
                heapq.heappush(frontier, (new+heuristic, nxt))
    if goal not in came:
        raise ValueError("no free-cell route")
    path, current = [], goal
    while current is not None:
        path.append(current); current = came[current]
    return path[::-1]


def simplify_grid_path(path):
    if len(path) <= 2:
        return path
    result = [path[0]]; direction = None
    for a, b in zip(path, path[1:]):
        current = (int(np.sign(b[0]-a[0])), int(np.sign(b[1]-a[1])))
        if direction is not None and current != direction:
            result.append(a)
        direction = current
    result.append(path[-1])
    return result


def routes_for_components(free, x, y, z, *, minimum_cells=4, maximum_routes=64, z_grid=None):
    # Four-connected labelling matches the no-corner-cutting motion contract.
    # Two cells that only touch at a corner are not route-connected.
    labels, count = ndimage.label(
        free, structure=np.asarray([[0, 1, 0], [1, 1, 1], [0, 1, 0]], dtype=int))
    if not count:
        raise ValueError("level has no connected free component")
    sizes = np.bincount(labels.ravel())
    selected = [int(index) for index in np.argsort(sizes[1:])[::-1] + 1
                if int(sizes[index]) >= int(minimum_cells)][:int(maximum_routes)]
    if not selected:
        raise ValueError("level has no routeable free component")
    routes = []
    for label_index in selected:
        component = labels == label_index
        indices = np.argwhere(component)
        seed = tuple(indices[len(indices)//2])
        first = farthest_cell(seed, component); second = farthest_cell(first, component)
        path = simplify_grid_path(astar(first, second, component))
        routes.append({"component_label":label_index,"cell_count":int(sizes[label_index]),
                       "mask":component,
                       "xyz":[[float(x[i]),float(y[j]),
                                float(z_grid[j, i] if z_grid is not None else z)] for j,i in path]})
    return routes


def choose_region_anchor(region_mask, free, used_cells=()):
    """Choose a deterministic free cell inside one semantic region."""
    region_cells = np.argwhere(region_mask)
    candidates = np.argwhere(region_mask & free)
    if not len(region_cells) or not len(candidates):
        return None
    center = region_cells.mean(axis=0)
    used = {tuple(cell) for cell in used_cells}
    ordered = sorted(
        (tuple(map(int, cell)) for cell in candidates),
        key=lambda cell: ((cell[0]-center[0])**2 + (cell[1]-center[1])**2,
                          cell[0], cell[1]))
    return next((cell for cell in ordered if cell not in used), None)


def region_route_tree(anchors, free, x, y, z):
    """Connect semantic-region anchors per free component with a deterministic tree."""
    labels, _ = ndimage.label(
        free, structure=np.asarray([[0, 1, 0], [1, 1, 1], [0, 1, 0]], dtype=int))
    groups = {}
    for anchor in anchors:
        cell = tuple(anchor["cell"])
        component = int(labels[cell])
        if component:
            groups.setdefault(component, []).append(anchor)
    links = []
    for component, rows in sorted(groups.items()):
        rows = sorted(rows, key=lambda row: row["region_id"])
        connected = [rows[0]]
        for current in rows[1:]:
            target = min(
                connected,
                key=lambda row: (
                    (row["cell"][0]-current["cell"][0])**2
                    + (row["cell"][1]-current["cell"][1])**2,
                    row["region_id"]))
            component_mask = labels == component
            path = simplify_grid_path(astar(tuple(target["cell"]), tuple(current["cell"]),
                                            component_mask))
            links.append({
                "from": target,
                "to": current,
                "xyz": [[float(x[i]), float(y[j]), float(z)] for j, i in path],
            })
            connected.append(current)
    return links


def _capture_evidence_view(image, level_id, *, require_top):
    """Validate one image against its capture-v2 report and atomic receipt."""
    image_path = Path(image).resolve()
    if not image_path.is_file():
        raise ValueError(f"visual evidence image is missing: {image_path}")
    capture_path = image_path.parent / "capture.json"
    receipt_path = image_path.parent / "capture-complete.json"
    if not capture_path.is_file():
        raise ValueError(f"visual evidence lacks capture report: {capture_path}")
    if not receipt_path.is_file():
        raise ValueError(f"visual evidence lacks completion receipt: {receipt_path}")
    capture = json.loads(capture_path.read_text())
    receipt = json.loads(receipt_path.read_text())
    if (capture.get("capture_contract_version") != 2
            or capture.get("source_unchanged") is not True
            or capture.get("camera_light") is not True
            or receipt.get("complete") is not True
            or receipt.get("capture_report_sha256") != digest(capture_path)):
        raise ValueError(f"invalid capture evidence: {capture_path}")
    views = [view for view in capture.get("views", []) if view.get("level") == level_id]
    if len(views) != 1:
        raise ValueError(f"capture level mismatch: {capture_path} {level_id}")
    view = views[0]
    top_path = Path(view.get("image", "")).resolve()
    oblique_paths = [Path(path).resolve() for path in view.get("oblique_images", [])]
    if require_top and top_path != image_path:
        raise ValueError(f"normalized selector must cite the level top image: {image_path}")
    if not require_top and image_path not in [top_path, *oblique_paths]:
        raise ValueError(f"connector image is not part of the declared capture view: {image_path}")
    expected_sha = (view.get("image_sha256") if image_path == top_path else
                    view.get("oblique_image_sha256", {}).get(str(image_path)))
    if expected_sha != digest(image_path):
        raise ValueError(f"visual evidence image digest mismatch: {image_path}")
    matches = [record for record in receipt.get("images", {}).get(level_id, [])
               if Path(record.get("path", "")).resolve() == image_path]
    if len(matches) != 1 or matches[0].get("sha256") != expected_sha:
        raise ValueError(f"visual evidence is not bound by capture receipt: {image_path}")
    return view


def visual_extent(configured, level_id, fallback):
    """Resolve normalized Flash boxes in the coordinate frame of their image."""
    candidates = []
    for row in list(configured.get("regions", [])) + list(configured.get("objects", [])):
        if "bounds_norm" not in row:
            continue
        evidence = row.get("evidence")
        image = evidence.get("image") if isinstance(evidence, dict) else None
        if not image:
            raise ValueError(f"normalized visual selector lacks image evidence: {row.get('id')}")
        view = _capture_evidence_view(image, level_id, require_top=True)
        extent = view.get("extent_xy")
        if (not isinstance(extent, list) or len(extent) != 4
                or not np.isfinite(np.asarray(extent, dtype=float)).all()
                or not (extent[0] < extent[1] and extent[2] < extent[3])):
            raise ValueError(f"invalid capture extent: {capture_path} {level_id}")
        candidates.append(tuple(map(float, extent)))
    unique = sorted(set(candidates))
    if len(unique) > 1:
        raise ValueError(f"conflicting capture extents for {level_id}: {unique}")
    return list(unique[0]) if unique else list(fallback)


def _write_json_atomic(path, payload):
    """Write and fsync a JSON file before publishing its containing directory."""
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    _fsync_directory(path.parent)


def _fsync_directory(path):
    """Persist directory entries when the local filesystem supports fsync."""
    descriptor = os.open(Path(path), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def publish_scene_directory(staged_scene, final_scene, manifest):
    """Commit one complete scene with its receipt in a single directory rename."""
    staged_scene, final_scene = Path(staged_scene), Path(final_scene)
    if final_scene.exists():
        raise FileExistsError(f"scene output already exists: {final_scene}")
    receipt = {"scene_id":manifest["scene_id"], "status":"done",
               "source_unchanged":manifest.get("source_unchanged") is True,
               "manifest_sha256":digest(staged_scene / "semantic-manifest.json")}
    if not receipt["source_unchanged"]:
        raise RuntimeError(f"source USD changed for {manifest['scene_id']}")
    _write_json_atomic(staged_scene / "completion-receipt.json", receipt)
    _fsync_directory(staged_scene)
    os.replace(staged_scene, final_scene)
    _fsync_directory(final_scene.parent)


def publish_batch_manifest(output_root, payload):
    """Atomically publish a batch manifest after all scene directories exist."""
    output_root = Path(output_root)
    if not payload.get("complete"):
        raise ValueError("refusing to publish complete=false as a final batch manifest")
    expected = set(payload.get("expected_scene_keys", []))
    actual = set(payload.get("scenes", {}))
    if expected and actual != expected:
        raise ValueError(f"batch scene keys mismatch: expected={sorted(expected)} actual={sorted(actual)}")
    _write_json_atomic(output_root / "batch-manifest.json", payload)


def load_visual(path):
    if path is None or not path.is_file():
        return {"scenes": {}}
    data = json.loads(path.read_text())
    if not isinstance(data.get("scenes", {}), dict):
        raise ValueError("visual observations require a scenes object")
    return data


def validate_vertical_connector(connector, level_lookup, level_grids, extent):
    """Return a normalized connector only when its world endpoints are evidenced."""
    from_level = connector.get("from_level_id")
    to_level = connector.get("to_level_id")
    polyline = connector.get("polyline_xyz")
    traversal_mode = connector.get("traversal_mode", "stairs")
    if (from_level not in level_lookup or to_level not in level_lookup
            or from_level == to_level or traversal_mode not in {"stairs", "ramp"}
            or not isinstance(polyline, list) or len(polyline) < 2):
        raise ValueError(f"invalid vertical connector structure: {connector}")
    points = np.asarray(polyline, dtype=float)
    if points.ndim != 2 or points.shape[1] != 3 or not np.isfinite(points).all():
        raise ValueError(f"invalid vertical connector coordinates: {connector}")
    evidence = connector.get("evidence")
    if (not isinstance(evidence, dict) or evidence.get("world_registration") is not True
            or not any(isinstance(evidence.get(key), str) and evidence[key].strip()
                       for key in ("source_ref", "image"))):
        raise ValueError(f"vertical connector lacks world-registration evidence: {connector}")
    image = evidence.get("image")
    if image is not None:
        evidence_level = evidence.get("level_id")
        if (not isinstance(image, str)
                or evidence_level not in {from_level, to_level}):
            raise ValueError(f"vertical connector image evidence lacks endpoint level: {connector}")
        _capture_evidence_view(image, evidence_level, require_top=False)
    expected_z = [float(level_lookup[from_level]["support_z_m"]),
                  float(level_lookup[to_level]["support_z_m"])]
    if abs(points[0, 2]-expected_z[0]) > .20 or abs(points[-1, 2]-expected_z[1]) > .20:
        raise ValueError(f"vertical connector endpoints do not match level heights: {connector}")
    zlow, zhigh = min(expected_z)-.20, max(expected_z)+.20
    if np.any(points[:, 2] < zlow) or np.any(points[:, 2] > zhigh):
        raise ValueError(f"vertical connector leaves its level-height interval: {connector}")
    xmin, xmax, ymin, ymax = extent
    if (np.any(points[:, 0] < xmin) or np.any(points[:, 0] > xmax)
            or np.any(points[:, 1] < ymin) or np.any(points[:, 1] > ymax)):
        raise ValueError(f"vertical connector leaves navigation ROI: {connector}")
    for endpoint, level_id in ((points[0], from_level), (points[-1], to_level)):
        grid = level_grids[level_id]
        i = int(math.floor((endpoint[0]-grid["origin"][0])/grid["resolution"]))
        j = int(math.floor((endpoint[1]-grid["origin"][1])/grid["resolution"]))
        if (not (0 <= j < grid["free"].shape[0] and 0 <= i < grid["free"].shape[1])
                or not grid["free"][j, i]):
            raise ValueError(
                f"vertical connector endpoint is not on free support: {level_id} {endpoint.tolist()}")
    normalized = dict(connector)
    normalized["polyline_xyz"] = points.tolist()
    normalized["traversal_mode"] = traversal_mode
    return normalized


def prepare_scene(scene, defaults, scene_root, output_root, visual):
    scene_id = scene["id"]
    scene_out = output_root / scene_id
    scene_out.mkdir(parents=True, exist_ok=False)
    usd_path = (scene_root / scene["usd"]).resolve()
    resolution = float(scene.get("resolution_m", defaults["resolution_m"]))
    nav_low, nav_high = map(float, scene.get(
        "collision_height_above_floor_m", defaults["collision_height_above_floor_m"]))
    low, high = map(float, scene.get("semantic_obstacle_height_above_floor_m",
                                    defaults["semantic_obstacle_height_above_floor_m"]))
    source_before = digest(usd_path)
    navigation_bounds = scene.get("navigation_bounds_xy")
    if navigation_bounds is not None:
        if (not isinstance(navigation_bounds, list) or len(navigation_bounds) != 4
                or not np.isfinite(np.asarray(navigation_bounds, dtype=float)).all()
                or not (navigation_bounds[0] < navigation_bounds[2]
                        and navigation_bounds[1] < navigation_bounds[3])):
            raise ValueError(
                f"invalid navigation_bounds_xy [xmin,ymin,xmax,ymax] for {scene_id}: "
                f"{navigation_bounds}")
    level_heights = [float(level["support_z_m"]) for level in scene["levels"]]
    support_ranges = [level.get("support_z_range_m") for level in scene["levels"]
                      if level.get("support_z_range_m") is not None]
    geometry_z_range = ([min(float(row[0]) for row in support_ranges)-.1,
                         max(float(row[1]) for row in support_ranges)+high+.1]
                        if support_ranges else [min(level_heights)-.1, max(level_heights)+high+.1])
    geometry_source = scene.get("geometry_source", "auto")
    triangles, geometry_meta = stage_triangles(
        usd_path, force_static=geometry_source == "static_mesh_fallback",
        geometry_source=geometry_source, roi_xy=navigation_bounds,
        z_range=geometry_z_range)
    if navigation_bounds is None:
        lower = np.floor(triangles[:, :, :2].min((0,1)) / resolution) * resolution - resolution
        upper = np.ceil(triangles[:, :, :2].max((0,1)) / resolution) * resolution + resolution
    else:
        lower = np.floor(np.asarray([navigation_bounds[0], navigation_bounds[1]])
                         / resolution) * resolution
        upper = np.ceil(np.asarray([navigation_bounds[2], navigation_bounds[3]])
                        / resolution) * resolution
    nx, ny = np.ceil((upper-lower)/resolution).astype(int)
    # Keep extremely large scenes bounded without silently changing scale.
    if nx*ny > 1_500_000:
        raise ValueError(f"grid too large for {scene_id}: {nx}x{ny}; raise descriptor resolution")
    xmax, ymax = lower + np.array([nx, ny])*resolution
    x = lower[0] + (np.arange(nx)+.5)*resolution
    y = lower[1] + (np.arange(ny)+.5)*resolution
    extent = [float(lower[0]), float(xmax), float(lower[1]), float(ymax)]
    layers = geometry_meta["source_layer_sha256"]
    source_inventory = {f"usd_layer_{i:04d}": value for i, (_, value) in enumerate(sorted(layers.items()))}
    doc = TopologyDocument(scene_id, source=source_inventory)
    doc.set_levels(scene["levels"])
    geo_features, exported_objects, panels, level_reports, level_grids = [], [], [], {}, {}
    unresolved_connectors = []

    # The v2 batch descriptor uses globally unique ids (for example
    # ``living_home_02``), while the Flash handoff deliberately keeps the
    # human-facing scene names (``home_02``) and legacy Living ids.  Resolve
    # these aliases here so visual evidence is actually bound to the same
    # scene that produced the geometry.  Exact ids remain highest priority;
    # all fallbacks are deterministic and scoped to the current descriptor
    # record.
    visual_scenes = visual.get("scenes", {})
    visual_scene = visual_scenes.get(scene_id, {})
    if not visual_scene:
        aliases = [scene.get("scene_key"), scene.get("display_name")]
        usd_name = str(scene.get("usd", "")).split("/", 1)[0]
        if usd_name:
            aliases.append(usd_name)
        for alias in aliases:
            if alias in visual_scenes:
                visual_scene = visual_scenes[alias]
                break
    visual_levels = visual_scene.get("levels", {})
    for level_index, level in enumerate(scene["levels"]):
        lid, z = level["id"], float(level["support_z_m"])
        tri_min = triangles[:, :, 2].min(1); tri_max = triangles[:, :, 2].max(1)
        normals = np.cross(triangles[:,1]-triangles[:,0], triangles[:,2]-triangles[:,0])
        norm = np.linalg.norm(normals, axis=1)
        support_model = level.get("support_model", scene.get("support_model", "flat"))
        if support_model == "heightfield":
            support_range = level.get("support_z_range_m")
            if (not isinstance(support_range, list) or len(support_range) != 2
                    or not support_range[0] < support_range[1]):
                raise ValueError(f"heightfield level lacks support_z_range_m: {scene_id}/{lid}")
            relevant = ((tri_max >= float(support_range[0])-.02)
                        & (tri_min <= float(support_range[1])+high+.02))
            support, semantic_occupied, ground_z = rasterize_heightfield(
                triangles[relevant], float(lower[0]), float(lower[1]), int(nx), int(ny),
                resolution, float(support_range[0]), float(support_range[1]), z,
                float(level.get("minimum_support_cosine", .70)),
                bool(level.get("select_lowest_support", True)), low, high)
            navigation_support, occupied, navigation_ground_z = rasterize_heightfield(
                triangles[relevant], float(lower[0]), float(lower[1]), int(nx), int(ny),
                resolution, float(support_range[0]), float(support_range[1]), z,
                float(level.get("minimum_support_cosine", .70)),
                bool(level.get("select_lowest_support", True)), nav_low, nav_high)
            if (not np.array_equal(support, navigation_support)
                    or not np.allclose(ground_z, navigation_ground_z, equal_nan=True)):
                raise RuntimeError(f"heightfield support changed across obstacle bands: {scene_id}/{lid}")
        else:
            near = ((np.abs(normals[:,2]) >= .985*norm) & (tri_max >= z-.02) & (tri_min <= z+.02))
            relevant = near | ((tri_max >= z+low) & (tri_min <= z+high))
            navigation_relevant = near | ((tri_max >= z+nav_low) & (tri_min <= z+nav_high))
            support, semantic_occupied = rasterize(
                triangles[relevant], float(lower[0]), float(lower[1]),
                int(nx), int(ny), resolution, z, z+low, z+high)
            navigation_support, occupied = rasterize(
                triangles[navigation_relevant], float(lower[0]), float(lower[1]),
                int(nx), int(ny), resolution, z, z+nav_low, z+nav_high)
            if not np.array_equal(support, navigation_support):
                raise RuntimeError(f"flat support changed across obstacle bands: {scene_id}/{lid}")
            ground_z = np.full(support.shape, z, dtype=float)
        free = support & ~occupied
        support_status = "support_confirmed" if int(support.sum()) >= 4 else "support_unresolved"
        # A descriptor level can be a roof/platform candidate whose support
        # or free space is not yet evidenced.  Preserve that level as an
        # explicit unknown layer so the batch remains editable and fail-closed
        # about routing instead of aborting the whole scene.
        route_status = "routeable"
        if int(support.sum()) < 4:
            route_status = "unresolved_no_support"
            free = np.zeros_like(support, dtype=bool)
        elif not int(free.sum()):
            route_status = "unresolved_no_free_component"
        state = np.full(support.shape, -1, np.int8); state[support] = 0; state[occupied] = 1
        level_grids[lid] = {"support":support,"occupied":occupied,"free":free,
                            "origin":lower,"resolution":resolution}
        routes = []
        if route_status == "routeable":
            try:
                routes = routes_for_components(
                    free, x, y, z,
                    minimum_cells=int(scene.get("minimum_route_component_cells", 4)),
                    maximum_routes=int(scene.get("maximum_routes_per_level", 64)),
                    # The shared topology contract currently binds ordinary
                    # walk nodes to one declared level height.  Keep the
                    # terrain profile in NPZ/level metadata and emit this
                    # route as a plan-view draft until a heightfield-aware
                    # route contract is added.
                    z_grid=None)
            except ValueError:
                route_status = "unresolved_no_routeable_component"
        routed_free = (np.logical_or.reduce([route["mask"] for route in routes])
                       if routes else np.zeros_like(free, dtype=bool))

        instance = np.full(state.shape, -1, np.int32); instance[semantic_occupied] = 0
        region_id = np.zeros(state.shape, np.int32)
        configured = visual_levels.get(lid, {})
        capture_extent = visual_extent(configured, lid, extent)
        configured_regions = list(configured.get("regions", []))
        configured_objects = list(configured.get("objects", []))
        regions, objects = scope_visual_semantics(
            scene_id, lid, configured_regions, configured_objects)
        if not regions and routed_free.any():
            indices = np.argwhere(routed_free)
            xmin, xmax = float(x[indices[:,1]].min()), float(x[indices[:,1]].max())
            ymin, ymax = float(y[indices[:,0]].min()), float(y[indices[:,0]].max())
            if xmax <= xmin:
                xmin, xmax = xmin - resolution / 2.0, xmax + resolution / 2.0
            if ymax <= ymin:
                ymin, ymax = ymin - resolution / 2.0, ymax + resolution / 2.0
            regions = [{"id":f"region:{scene_id}:{lid}:unknown", "label":"用途待确认区域",
                        "kind":"unclassified_space", "confidence":"unknown_function",
                        "bounds_xy":[xmin, ymin, xmax, ymax]}]
        elif not regions:
            regions = []
        materialized_region_ids = set()
        region_anchors = []
        used_anchor_cells = set()
        for ridx, row in enumerate(regions, 1):
            bounds = (normalize_bounds(row["bounds_norm"], capture_extent)
                      if "bounds_norm" in row else row["bounds_xy"])
            mask = selector_mask(bounds, x, y, support)
            if not mask.any():
                continue
            region_id[mask] = ridx
            polygon = [[bounds[0],bounds[1]],[bounds[2],bounds[1]],
                       [bounds[2],bounds[3]],[bounds[0],bounds[3]]]
            doc.upsert_semantic("regions", {
                "id":row["id"], "kind":row.get("kind","unclassified_space"),
                "label":row["label"], "aliases":[], "level_id":lid,
                "polygon_xyz":[[px,py,z] for px,py in polygon], "height_m":float(high),
                "entrance_node_ids":[], "properties":{
                    "confidence":row.get("confidence","unknown_function"),
                    "evidence":row.get("evidence"), "boundary_status":"editable_visual_selector",
                    "source_semantic_id":row.get("source_semantic_id"),
                    "raster_region_id":ridx}}
            )
            semantic_region = next(
                item for item in doc.semantics["regions"] if item["id"] == row["id"])
            anchor_cell = choose_region_anchor(mask, free, used_anchor_cells)
            if anchor_cell is None:
                semantic_region["properties"].update({
                    "topology_status":"unresolved_no_free_region_anchor"})
            else:
                used_anchor_cells.add(anchor_cell)
                j, i = anchor_cell
                anchor_node_id = f"node:{scene_id}:{lid}:region:{row['id']}"
                doc.add_node([float(x[i]), float(y[j]), z], node_id=anchor_node_id,
                             level_id=lid, kind="region_anchor", label=row["label"])
                semantic_region["properties"].update({
                    "topology_anchor_node_id":anchor_node_id,
                    "topology_status":"grid_free_anchor_draft",
                    "topology_warning":"Anchor is free-grid evidence, not a visually confirmed doorway"})
                region_anchors.append({"region_id":row["id"], "node_id":anchor_node_id,
                                       "label":row["label"], "cell":anchor_cell})
            materialized_region_ids.add(row["id"])
            region_geom = mask_geometry(mask, lower, resolution)
            geo_features.append({"type":"Feature", "geometry":region_geom,
                                 "properties":{**row,"level_id":lid,"feature_type":"room_region",
                                               "raster_id":ridx,"bounds_xy":bounds,
                                               "support_cell_count":int(mask.sum()),
                                               "boundary_status":"support_mask_clipped_visual_selector"}})

        if not objects:
            labels, object_count = ndimage.label(
                semantic_occupied, structure=np.ones((3,3), dtype=int))
            sizes = np.bincount(labels.ravel())
            keep = sorted((int(sizes[i]), i) for i in range(1, object_count+1) if sizes[i] >= 4)[-40:]
            objects = []
            for ordinal, (size, label_index) in enumerate(reversed(keep), 1):
                points = np.argwhere(labels == label_index)
                objects.append({"id":f"object:{scene_id}:{lid}:unknown:{ordinal:03d}",
                    "label":"未分类障碍", "kind":"unknown_obstacle", "confidence":"unknown_function",
                    "component_label":label_index})
        component_labels = None
        if any("component_label" in row for row in objects):
            component_labels = ndimage.label(
                semantic_occupied, structure=np.ones((3,3), dtype=int))[0]
        for oidx, row in enumerate(objects, 1):
            if "component_label" in row:
                mask = component_labels == int(row["component_label"])
            else:
                bounds = (normalize_bounds(row["bounds_norm"], capture_extent)
                          if "bounds_norm" in row else row["bounds_xy"])
                mask = selector_mask(bounds, x, y, semantic_occupied)
            if not mask.any():
                continue
            if np.any(instance[mask] > 0):
                # Visual boxes can overlap; keep first evidence and leave overlap with that label.
                mask &= instance <= 0
            if not mask.any():
                continue
            instance[mask] = oidx
            points = np.argwhere(mask); cx=float(x[points[:,1]].mean()); cy=float(y[points[:,0]].mean())
            geom = mask_geometry(mask, lower, resolution)
            record = {**row, "level_id":lid, "instance_id":oidx,
                      "occupied_cell_count":int(mask.sum()), "mask_rle_row_start_end":row_runs(mask),
                      "footprint_geojson":geom, "raster_file":f"{lid}-semantic-grid.npz"}
            exported_objects.append(record)
            requested_region_id = row.get("region_id")
            resolved_region_id = (requested_region_id
                                  if requested_region_id in materialized_region_ids else None)
            doc.upsert_semantic("entities", {
                "id":row["id"], "kind":row.get("kind","unknown_obstacle"), "label":row["label"],
                "aliases":[], "level_id":lid, "region_id":resolved_region_id,
                "position_xyz":[cx,cy,z], "source_ref":None,
                "properties":{"confidence":row.get("confidence","unknown_function"),
                    "evidence":row.get("evidence"),
                    "source_semantic_id":row.get("source_semantic_id"),
                    "requested_region_id":requested_region_id,
                    "region_assignment_status":("resolved" if resolved_region_id else
                                                "unresolved_region_not_materialized" if requested_region_id else
                                                "not_requested"),
                    "geometry_binding":{"raster_file":record["raster_file"],
                                        "instance_id":oidx,"footprint_geojson":geom},
                    "position_source":"centroid_of_bound_occupied_cells",
                    "pose_status":"task_interactions_deferred"}}
            )
            geo_features.append({"type":"Feature","geometry":geom,
                "properties":{k:v for k,v in record.items() if k not in {"footprint_geojson","mask_rle_row_start_end"}}})

        semantic_route_xyzs = []
        for link_index, link in enumerate(
                region_route_tree(region_anchors, free, x, y, z), 1):
            route_id = f"route:{scene_id}:{lid}:region-link:{link_index:03d}"
            doc.add_waypoint_route(
                route_id=route_id,
                label=f"区域连接草稿：{link['from']['label']} → {link['to']['label']}",
                start_node_id=link["from"]["node_id"],
                end_node_id=link["to"]["node_id"],
                intermediate_xyz=link["xyz"][1:-1], traversal_mode="walk")
            semantic_route = next(r for r in doc.semantics["routes"] if r["id"] == route_id)
            semantic_route["properties"].update({
                "status":"grid_region_connection_draft",
                "robot_validated":False,
                "doorway_validated":False,
                "resolution_m":resolution,
                "warning":"Free-grid connection does not establish a visually confirmed doorway"})
            semantic_route_xyzs.append(link["xyz"])

        route_xyzs = []
        for route_index, route_info in enumerate(routes, 1):
            route_xyz = route_info["xyz"]
            suffix = f"component-{route_index:03d}"
            start_id=f"node:{scene_id}:{lid}:{suffix}:route-start"
            end_id=f"node:{scene_id}:{lid}:{suffix}:route-end"
            doc.add_node(route_xyz[0], node_id=start_id, level_id=lid,
                         kind="custom", label="几何路线起点")
            doc.add_node(route_xyz[-1], node_id=end_id, level_id=lid,
                         kind="custom", label="几何路线终点")
            route_id=f"route:{scene_id}:{lid}:{suffix}:free-centerline"
            doc.add_waypoint_route(route_id=route_id, label="自由空间避障中心线草稿",
                                   start_node_id=start_id, end_node_id=end_id,
                                   intermediate_xyz=route_xyz[1:-1], traversal_mode="walk")
            semantic_route = next(r for r in doc.semantics["routes"] if r["id"] == route_id)
            semantic_route["properties"].update({
                "status":"grid_collision_free_centerline_draft",
                "robot_validated":False,"resolution_m":resolution,
                "free_component_cells":route_info["cell_count"],
                "heightfield_profile_in_npz": support_model == "heightfield",
                "warning":"No robot footprint, clearance or dynamics certification"})
            route_xyzs.append(route_xyz)

        np.savez_compressed(scene_out/f"{lid}-semantic-grid.npz", x=x, y=y, support=support,
            occupied=occupied, semantic_occupied=semantic_occupied,
            state=state, instance_id=instance, region_id=region_id,
            origin_xy=lower, resolution_m=resolution, height_band_z=[z+low,z+high],
            navigation_height_band_z=[z+nav_low,z+nav_high], ground_z=ground_z,
            support_model=support_model)
        panels.append((lid,z,state,instance,region_id,regions,objects,route_xyzs,
                       semantic_route_xyzs,region_anchors))
        level_reports[lid] = {"floor_z_m":z,"triangles_considered":int(relevant.sum()),
            "support_cells":int(support.sum()),"occupied_cells":int(occupied.sum()),
            "semantic_occupied_cells":int(semantic_occupied.sum()),
            "free_cells":int(free.sum()),"unknown_cells":int((state==-1).sum()),
            "labeled_occupied_cells":int((instance>0).sum()),
            "unknown_occupied_cells":int((instance==0).sum()),
            "route_count":len(route_xyzs),
            "route_points":sum(len(route_xyz) for route_xyz in route_xyzs),
            "semantic_route_count":len(semantic_route_xyzs),
            "semantic_region_anchor_count":len(region_anchors),
            "capture_extent_xy":capture_extent,
            "support_model":support_model,
            "support_status":support_status,
            "route_status":route_status}
        if support_model == "heightfield":
            finite_ground = ground_z[np.isfinite(ground_z)]
            level_reports[lid]["ground_z_range_m"] = [float(finite_ground.min()), float(finite_ground.max())]

    level_lookup = {row["id"]: row for row in scene["levels"]}
    declared_connectors = [
        validate_vertical_connector(connector, level_lookup, level_grids, extent)
        for connector in scene.get("vertical_connectors", [])]
    resolved_connector_summaries = []
    for connector_index, connector in enumerate(declared_connectors, 1):
        from_level = connector.get("from_level_id")
        to_level = connector.get("to_level_id")
        polyline = connector.get("polyline_xyz")
        traversal_mode = connector.get("traversal_mode", "stairs")
        connector_id = connector.get(
            "id", f"route:{scene_id}:{from_level}-to-{to_level}:vertical-{connector_index:03d}")
        start_id = f"node:{connector_id}:start"
        end_id = f"node:{connector_id}:end"
        doc.add_node(polyline[0], node_id=start_id, level_id=from_level,
                     kind="transition", label="跨层连接入口")
        doc.add_node(polyline[-1], node_id=end_id, level_id=to_level,
                     kind="transition", label="跨层连接出口")
        doc.add_waypoint_route(
            route_id=connector_id, label=connector.get("label", "跨层连接（世界坐标已登记）"),
            start_node_id=start_id, end_node_id=end_id,
            intermediate_xyz=polyline[1:-1], traversal_mode=traversal_mode,
            required_controller_capabilities=connector.get(
                "required_controller_capabilities",
                ["stair_capable"] if traversal_mode == "stairs" else []))
        semantic_route = next(r for r in doc.semantics["routes"] if r["id"] == connector_id)
        semantic_route["properties"].update({
            "status":"world_endpoints_authored_robot_unvalidated",
            "robot_validated":False,
            "evidence":connector.get("evidence"),
            "warning":"Connector geometry does not certify robot clearance or dynamics"})
        resolved_connector_summaries.append({
            "id":connector_id,"from_level_id":from_level,"to_level_id":to_level,
            "status":"world_endpoints_authored_robot_unvalidated",
            "robot_validated":False})

    if len(scene["levels"]) > 1 and not declared_connectors:
        ordered_levels = [row["id"] for row in scene["levels"]]
        unresolved_connectors = [{
            "id":f"connector:{scene_id}:{first}-to-{second}:unresolved",
            "from_level_id":first,"to_level_id":second,
            "status":"multi_level_connector_unresolved",
            "robot_validated":False,
            "required_evidence":"world-registered endpoints/polyline and 3-D clearance sweep"
        } for first, second in zip(ordered_levels, ordered_levels[1:])]
    if doc.semantics is not None:
        doc.semantics.setdefault("extensions", {})["vertical_connectors"] = (
            resolved_connector_summaries + unresolved_connectors)

    plt.rcParams["font.family"] = ["Noto Sans CJK JP", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    for (lid,z,state,instance,region_id,regions,objects,route_xyzs,
         semantic_route_xyzs,region_anchors) in panels:
        fig, ax = plt.subplots(figsize=(11,9))
        rgb=np.full((*state.shape,3),to_rgb("#cbd3dc")); rgb[state==0]=to_rgb("#fafafa"); rgb[state==1]=to_rgb("#3c444d")
        for i,row in enumerate(regions,1):
            rgb[(region_id==i)&(state==0)] = .88+.12*np.asarray(to_rgb(PALETTE[(i-1)%len(PALETTE)]))
        for i,row in enumerate(objects,1):
            rgb[instance==i]=to_rgb(PALETTE[(i-1)%len(PALETTE)])
        ax.imshow(rgb,origin="lower",extent=extent,interpolation="nearest")
        for route_index, route_xyz in enumerate(route_xyzs):
            route=np.asarray(route_xyz)
            ax.plot(route[:,0],route[:,1],color="#16824b",lw=1.8,
                    label="避障中心线草稿" if route_index == 0 else None)
        for route_index, route_xyz in enumerate(semantic_route_xyzs):
            route=np.asarray(route_xyz)
            ax.plot(route[:,0],route[:,1],color="#315da8",lw=2.2,ls="--",
                    label="语义区域拓扑边（草稿）" if route_index == 0 else None)
        if region_anchors:
            points=np.asarray([[x[int(row["cell"][1])],y[int(row["cell"][0])]]
                               for row in region_anchors])
            ax.scatter(points[:,0],points[:,1],s=48,c="#7a2ca6",marker="o",
                       ec="white",lw=.8,zorder=6,label="语义区域拓扑节点")
        for i,row in enumerate(regions,1):
            cells=np.argwhere(region_id==i)
            if len(cells):
                ax.text(float(x[cells[:,1]].mean()),float(y[cells[:,0]].mean()),row["label"],ha="center",va="center",
                    fontsize=9,weight="bold",bbox=dict(fc="white",ec=PALETTE[(i-1)%len(PALETTE)],alpha=.88))
        ax.set(title=f"{scene_id} {lid} · 二维障碍、区域语义与可行路线草稿",
               xlabel="世界坐标 X (m)",ylabel="世界坐标 Y (m)",aspect="equal")
        ax.legend(handles=[Patch(color="#3c444d",label="高度带内几何障碍"),Patch(color="#fafafa",ec="#999",label="有支撑且未命中障碍"),
                           Patch(color="#cbd3dc",label="未知/无本层支撑")],loc="upper right")
        fig.text(.5,.02,f"地面 z={z:.3f} m；路线按栅格中心线避障，但尚未加入机器人外形、净空、动力学或楼梯能力。",ha="center",fontsize=9)
        fig.tight_layout(rect=[0,.04,1,.96]); fig.savefig(scene_out/f"{lid}-semantic-map.png",dpi=170); plt.close(fig)

    count=len(panels); cols=min(3,count); rows=int(math.ceil(count/cols))
    fig,axes=plt.subplots(rows,cols,figsize=(7*cols,6.2*rows),squeeze=False)
    for ax,panel in zip(axes.ravel(),panels):
        (lid,z,state,instance,region_id,regions,objects,route_xyzs,
         semantic_route_xyzs,region_anchors)=panel
        rgb=np.full((*state.shape,3),to_rgb("#cbd3dc"));rgb[state==0]=to_rgb("#fafafa");rgb[state==1]=to_rgb("#3c444d")
        for i in range(1,len(regions)+1):rgb[(region_id==i)&(state==0)]=.88+.12*np.asarray(to_rgb(PALETTE[(i-1)%len(PALETTE)]))
        for i in range(1,len(objects)+1):rgb[instance==i]=to_rgb(PALETTE[(i-1)%len(PALETTE)])
        ax.imshow(rgb,origin="lower",extent=extent,interpolation="nearest")
        for route_xyz in route_xyzs:
            route=np.asarray(route_xyz);ax.plot(route[:,0],route[:,1],"-",c="#16824b",lw=1.4)
        for route_xyz in semantic_route_xyzs:
            route=np.asarray(route_xyz);ax.plot(route[:,0],route[:,1],"--",c="#315da8",lw=1.8)
        if region_anchors:
            points=np.asarray([[x[int(row["cell"][1])],y[int(row["cell"][0])]]
                               for row in region_anchors])
            ax.scatter(points[:,0],points[:,1],s=28,c="#7a2ca6",ec="white",lw=.5,zorder=6)
        ax.set(title=f"{lid}  z={z:.2f} m",aspect="equal",xlabel="X (m)",ylabel="Y (m)")
    for ax in axes.ravel()[count:]:ax.axis("off")
    fig.suptitle(f"{scene_id} · 可编辑语义拓扑地图",fontsize=18)
    fig.text(.5,.02,"深灰=导航障碍；浅色=区域；绿线=自由格中心线；紫点/蓝虚线=语义区域拓扑草稿；未分类障碍保留 unknown。",ha="center")
    fig.tight_layout(rect=[0,.04,1,.95]);fig.savefig(scene_out/f"{scene_id}-semantic-overview.png",dpi=160);plt.close(fig)

    # Keep an editable topology product even when geometry evidence yielded
    # no free component.  The two anchors and their edge are explicitly
    # unresolved; downstream consumers must not interpret this as a certified
    # route.  This avoids dropping an otherwise useful obstacle/semantic map
    # merely because the route draft is still pending review.
    if len(doc.nodes) < 2 or not doc.edges:
        anchor_level = scene["levels"][0]["id"]
        anchor_z = float(scene["levels"][0]["support_z_m"])
        cx = (float(extent[0]) + float(extent[1])) / 2.0
        cy = (float(extent[2]) + float(extent[3])) / 2.0
        if len(doc.nodes) < 1:
            doc.add_node([cx - resolution, cy, anchor_z],
                         node_id=f"node:{scene_id}:unresolved:start",
                         kind="unresolved_anchor", level_id=anchor_level,
                         label="路线起点待确认")
        if len(doc.nodes) < 2:
            doc.add_node([cx + resolution, cy, anchor_z],
                         node_id=f"node:{scene_id}:unresolved:end",
                         kind="unresolved_anchor", level_id=anchor_level,
                         label="路线终点待确认")
        if not doc.edges:
            doc.add_waypoint_route(
                route_id=f"route:{scene_id}:unresolved-draft",
                label="路线草稿待几何/视觉证据确认",
                start_node_id=doc.nodes[0]["id"], end_node_id=doc.nodes[1]["id"],
                traversal_mode="walk")
            unresolved_route = next(r for r in doc.semantics["routes"]
                                    if r["id"] == f"route:{scene_id}:unresolved-draft")
            unresolved_route["properties"].update({
                "status": "unresolved_no_routeable_component",
                "robot_validated": False,
                "warning": "Placeholder anchors are not a traversability claim"})
    generate_product(scene_out/"topology.json",doc);load_product(scene_out/"topology.json")
    semantic = {"schema":"eai.semantic_footprints","version":1,"scene_id":scene_id,
        "coordinate_frame":"USD world metres, X right / Y up in plan","source_usd":str(usd_path),
        "visual_review":visual_scene,"objects":exported_objects,
        "vertical_connectors":resolved_connector_summaries + unresolved_connectors,
        "notes":["Unclassified occupied cells remain unknown_obstacle.",
                 "Region selectors are editable and visual semantics carry explicit confidence.",
                 "Routes are geometry-only drafts, not universal robot traversability certificates."]}
    (scene_out/"semantic-footprints.json").write_text(json.dumps(semantic,ensure_ascii=False,indent=2)+"\n")
    (scene_out/"semantic-footprints.geojson").write_text(json.dumps({"type":"FeatureCollection","features":geo_features},ensure_ascii=False,indent=2)+"\n")
    manifest={"schema":"eai.semantic_map.manifest","version":1,"scene_id":scene_id,
        "source_usd":str(usd_path),"source_entry_sha256":source_before,
        "source_unchanged":digest(usd_path)==source_before,"source_layer_sha256":layers,
        "geometry":{k:v for k,v in geometry_meta.items() if k not in {"mesh_paths","source_layer_sha256"}},
        "mesh_paths_count":len(geometry_meta["mesh_paths"]),"resolution_m":resolution,
        "semantic_obstacle_height_above_floor_m":[low,high],
        "navigation_reference_height_above_floor_m":[nav_low,nav_high],
        "origin_xy":lower.tolist(),"extent_xy":extent,
        "navigation_bounds_xy":navigation_bounds,"levels":level_reports,
        "generator_sha256":digest(Path(__file__)),"topology_reopen":True,
        "limitations":["surface evidence rather than watertight solid occupancy",
                       "no robot footprint or swept-clearance certificate",
                       "unresolved vertical transitions require visual/world registration"]}
    (scene_out/"semantic-manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+"\n")
    if not manifest["source_unchanged"]:
        raise RuntimeError(f"source USD changed: {usd_path}")
    return manifest


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--descriptor",type=Path,default=DEFAULT_DESCRIPTOR)
    parser.add_argument("--visual-observations",type=Path)
    parser.add_argument("--output-root",type=Path,required=True)
    parser.add_argument("--scene",action="append",help="limit to one or more scene IDs")
    parser.add_argument("--resume",action="store_true",
                        help="resume a batch root, preserving complete scene directories")
    args=parser.parse_args()
    args.output_root.mkdir(parents=True,exist_ok=args.resume)
    descriptor=json.loads(args.descriptor.read_text());visual=load_visual(args.visual_observations)
    # v2 is the category-wide descriptor used by the 30-scene batch.  Keep
    # the legacy Living descriptor contract intact while resolving each v2
    # scene against its own category root.
    is_v2 = descriptor.get("schema") == "eai.semantic_map.scene_batch" and descriptor.get("version") == 2
    if is_v2:
        scene_roots = {category: Path(root) for category, root in descriptor["scene_roots"].items()}
        defaults = descriptor["defaults"]
    else:
        scene_roots = {None: Path(descriptor["scene_root"])}
        defaults = descriptor["defaults"]
    selected=set(args.scene or [])
    reports={}; failures={}
    if args.resume:
        prior = None
        for name in ("batch-manifest.json", "batch-failed.json"):
            candidate = args.output_root / name
            if candidate.is_file():
                prior = json.loads(candidate.read_text())
                break
        if prior:
            reports.update(prior.get("scenes", {}))
            failures.update(prior.get("failures", {}))
    for scene in descriptor["scenes"]:
        if selected and scene["id"] not in selected:continue
        scene_id = scene["id"]
        final_scene = args.output_root / scene_id
        if args.resume and final_scene.is_dir() and (final_scene / "completion-receipt.json").is_file():
            continue
        try:
            with tempfile.TemporaryDirectory(prefix=f".{scene_id}-", dir=args.output_root) as working:
                working_root = Path(working)
                root = (scene_roots.get(scene.get("category")) if is_v2
                        else scene_roots[None])
                report = prepare_scene(scene, defaults, root, working_root, visual)
                final_scene = args.output_root / scene_id
                publish_scene_directory(working_root / scene_id, final_scene, report)
            reports[scene_id]=report
            failures.pop(scene_id, None)
            print(scene_id,json.dumps(report["levels"],ensure_ascii=False),flush=True)
        except Exception as exc:
            failures[scene_id]={"type":type(exc).__name__,"message":str(exc)}
            print(scene_id,json.dumps({"status":"failed",**failures[scene_id]},ensure_ascii=False),flush=True)
    known_scene_ids = {scene["id"] for scene in descriptor["scenes"]}
    unknown_selected = selected-known_scene_ids
    if unknown_selected:raise ValueError("unknown scene IDs: "+", ".join(sorted(unknown_selected)))
    expected_scene_keys = ((selected or known_scene_ids) if is_v2 else
                           (selected or set(reports)))
    missing = expected_scene_keys-set(reports)-set(failures)
    for scene_id in sorted(missing):
        failures[scene_id] = {"type":"MissingScene","message":"scene was not produced"}
    batch={"schema":"eai.semantic_map.batch","version":1,
        "descriptor":str(args.descriptor.resolve()),"descriptor_sha256":digest(args.descriptor),
        "complete":not failures and set(reports) == expected_scene_keys,
        "scenes":reports,"failures":failures}
    batch["expected_scene_keys"] = sorted(expected_scene_keys)
    if not batch["complete"]:
        # Keep an explicit diagnostic file, but never expose a false final
        # completion manifest that downstream validators could mistake for a
        # publishable batch.
        _write_json_atomic(args.output_root / "batch-failed.json", batch)
    else:
        publish_batch_manifest(args.output_root, batch)
        # A successful resume supersedes the exact diagnostic from the prior
        # failed attempt; leaving both files makes the batch state ambiguous.
        (args.output_root / "batch-failed.json").unlink(missing_ok=True)
    if failures:
        raise RuntimeError("semantic map generation failed for: "+", ".join(sorted(failures)))


if __name__=="__main__":main()
