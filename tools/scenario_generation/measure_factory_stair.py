"""Measure static Factory stair geometry from an OpenUSD stage.

This module is deliberately independent of Isaac Sim.  It reads authored USD
geometry only; it does not edit a stage, infer a support height from a bounding
box, or claim that a robot can traverse the result.  The report is suitable as
an input to a later physics probe and remains ``unknown`` when the mesh does
not contain enough evidence for a measurement.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable, Sequence


SCHEMA = "eai.factory_stair_static_measurement"
VERSION = 1
DEFAULT_PRIM_PATH = "/World/SM_StairsSmall"


def _finite(values: Iterable[float], size: int, label: str) -> tuple[float, ...]:
    result = tuple(float(value) for value in values)
    if len(result) != size or not all(math.isfinite(value) for value in result):
        raise ValueError(f"{label} must contain {size} finite values")
    return result


def _unit_xy(vector: Sequence[float]) -> tuple[float, float]:
    x, y = _finite(vector, 2, "horizontal vector")
    length = math.hypot(x, y)
    if length <= 1e-12:
        raise ValueError("horizontal vector has zero length")
    return x / length, y / length


def _dot_xy(point: Sequence[float], axis: Sequence[float]) -> float:
    return float(point[0]) * float(axis[0]) + float(point[1]) * float(axis[1])


def _cross_xy(a: Sequence[float], b: Sequence[float]) -> float:
    return float(a[0]) * float(b[1]) - float(a[1]) * float(b[0])


def _canonical_axis(axis: Sequence[float]) -> tuple[float, float]:
    x, y = _unit_xy(axis)
    if x < -1e-12 or (abs(x) <= 1e-12 and y < 0):
        return -x, -y
    return x, y


def _bounds(points: Sequence[Sequence[float]]) -> dict[str, list[float]]:
    if not points:
        raise ValueError("cannot bound an empty point set")
    columns = list(zip(*points))
    if len(columns) != 3:
        raise ValueError("world points must be three-dimensional")
    minimum = [min(float(value) for value in column) for column in columns]
    maximum = [max(float(value) for value in column) for column in columns]
    _finite(minimum, 3, "minimum bounds")
    _finite(maximum, 3, "maximum bounds")
    return {"min": minimum, "max": maximum}


def _polygon_normal(points: Sequence[Sequence[float]]) -> tuple[float, float, float]:
    """Return the Newell normal, which also handles non-triangular faces."""
    normal = [0.0, 0.0, 0.0]
    for first, second in zip(points, (*points[1:], points[0])):
        normal[0] += (first[1] - second[1]) * (first[2] + second[2])
        normal[1] += (first[2] - second[2]) * (first[0] + second[0])
        normal[2] += (first[0] - second[0]) * (first[1] + second[1])
    return _finite(normal, 3, "polygon normal")


def _projected_spans(points: Sequence[Sequence[float]], width_axis: Sequence[float],
                     run_axis: Sequence[float]) -> tuple[float, float, tuple[float, float, float]]:
    width_values = [_dot_xy(point, width_axis) for point in points]
    run_values = [_dot_xy(point, run_axis) for point in points]
    width = max(width_values) - min(width_values)
    depth = max(run_values) - min(run_values)
    center = (
        sum(float(point[0]) for point in points) / len(points),
        sum(float(point[1]) for point in points) / len(points),
        sum(float(point[2]) for point in points) / len(points),
    )
    return width, depth, center


def _face_area_and_axes(points: Sequence[Sequence[float]]) -> tuple[float, tuple[float, float]]:
    """Get projected area and a stable principal axis from a horizontal face."""
    normal = _polygon_normal(points)
    area = abs(normal[2]) * 0.5
    return area, _principal_horizontal_axis(points)


def _principal_horizontal_axis(points: Sequence[Sequence[float]]) -> tuple[float, float]:
    """Return the major 2-D covariance axis of a horizontal point set."""
    if len(points) < 3:
        raise ValueError("horizontal face needs at least three points")
    mean_x = sum(float(point[0]) for point in points) / len(points)
    mean_y = sum(float(point[1]) for point in points) / len(points)
    covariance_xx = sum((float(point[0]) - mean_x) ** 2 for point in points)
    covariance_xy = sum((float(point[0]) - mean_x) * (float(point[1]) - mean_y)
                        for point in points)
    covariance_yy = sum((float(point[1]) - mean_y) ** 2 for point in points)
    if max(covariance_xx, covariance_yy) <= 1e-14:
        raise ValueError("horizontal face has no measurable planar extent")
    angle = 0.5 * math.atan2(2.0 * covariance_xy,
                             covariance_xx - covariance_yy)
    return _canonical_axis((math.cos(angle), math.sin(angle)))


def _face_region(face: dict[str, Any], width_axis: Sequence[float],
                 run_axis: Sequence[float]) -> dict[str, Any]:
    points = face["points_world_m"]
    width, depth, center = _projected_spans(points, width_axis, run_axis)
    return {
        "source": {
            "prim_path": face["prim_path"],
            "face_index": face["face_index"],
            "face_indices": list(face.get("face_indices", [face["face_index"]])),
        },
        "center_world_m": [float(value) for value in center],
        "bounds_world_m": _bounds(points),
        "width_m": float(width),
        "depth_m": float(depth),
        "area_m2": float(face["area_m2"]),
        "support_z_m": float(center[2]),
        "width_interval_m": [
            min(_dot_xy(point, width_axis) for point in points),
            max(_dot_xy(point, width_axis) for point in points),
        ],
        "run_interval_m": [
            min(_dot_xy(point, run_axis) for point in points),
            max(_dot_xy(point, run_axis) for point in points),
        ],
    }


def _merge_coplanar_faces(faces: Sequence[dict[str, Any]], *,
                           tolerance_m: float = 1e-4) -> list[dict[str, Any]]:
    """Merge same-height triangles sharing an authored mesh edge.

    Production stair treads are commonly triangulated quads. Treating each
    triangle as a separate tread creates duplicate centers and false height
    reversals, so connected coplanar faces are combined before flight
    detection. Faces at another height or in another prim never merge.
    """
    if tolerance_m <= 0 or not math.isfinite(tolerance_m):
        raise ValueError("face merge tolerance must be finite and positive")

    def same_point(first, second):
        return math.dist(first, second) <= tolerance_m

    def adjacent(first, second):
        if first["prim_path"] != second["prim_path"]:
            return False
        first_z = sum(point[2] for point in first["points_world_m"]) / len(first["points_world_m"])
        second_z = sum(point[2] for point in second["points_world_m"]) / len(second["points_world_m"])
        if abs(first_z - second_z) > tolerance_m:
            return False
        shared = sum(any(same_point(point, other) for other in second["points_world_m"])
                     for point in first["points_world_m"])
        return shared >= 2

    remaining = set(range(len(faces)))
    merged = []
    while remaining:
        component = {remaining.pop()}
        frontier = list(component)
        while frontier:
            current = frontier.pop()
            neighbors = {index for index in remaining
                         if adjacent(faces[current], faces[index])}
            remaining -= neighbors
            component |= neighbors
            frontier.extend(neighbors)
        rows = [faces[index] for index in sorted(component)]
        points = []
        for row in rows:
            for point in row["points_world_m"]:
                if not any(same_point(point, existing) for existing in points):
                    points.append(list(point))
        face_indices = sorted(row["face_index"] for row in rows)
        merged.append({
            "prim_path": rows[0]["prim_path"],
            "face_index": face_indices[0],
            "face_indices": face_indices,
            "points_world_m": points,
            "area_m2": sum(float(row["area_m2"]) for row in rows),
            "long_edge_axis_world_xy": list(_principal_horizontal_axis(points)),
        })
    return merged


def _deduplicate_faces(faces: Sequence[dict[str, Any]], *, tolerance_m: float = 0.01) -> list[dict[str, Any]]:
    unique: list[dict[str, Any]] = []
    for face in sorted(faces, key=lambda item: (item["center_world_m"][2],
                                                 item["center_world_m"][0],
                                                 item["center_world_m"][1])):
        duplicate = False
        for existing in unique:
            if (abs(face["support_z_m"] - existing["support_z_m"]) <= tolerance_m
                    and math.dist(face["center_world_m"], existing["center_world_m"]) <= tolerance_m
                    and abs(face["width_m"] - existing["width_m"]) <= 2 * tolerance_m
                    and abs(face["depth_m"] - existing["depth_m"]) <= 2 * tolerance_m):
                duplicate = True
                break
        if not duplicate:
            unique.append(face)
    return unique


def _infer_width_axis(faces: Sequence[dict[str, Any]]) -> tuple[float, float]:
    if not faces:
        raise ValueError("cannot infer stair direction without horizontal faces")
    # The long edge of a tread is its transverse edge.  Circular averaging
    # doubled angles makes the result independent of edge winding.
    weighted_x = weighted_y = total = 0.0
    for face in faces:
        axis = face["long_edge_axis_world_xy"]
        weight = max(float(face["area_m2"]), 1e-6)
        weighted_x += weight * math.cos(2 * math.atan2(axis[1], axis[0]))
        weighted_y += weight * math.sin(2 * math.atan2(axis[1], axis[0]))
        total += weight
    if math.hypot(weighted_x, weighted_y) <= 1e-10:
        return faces[0]["long_edge_axis_world_xy"]
    angle = 0.5 * math.atan2(weighted_y, weighted_x)
    return _canonical_axis((math.cos(angle), math.sin(angle)))


def detect_tread_candidates(horizontal_faces: Sequence[dict[str, Any]], *,
                            minimum_area_m2: float = 0.15,
                            minimum_width_m: float = 0.5,
                            minimum_aspect_ratio: float = 2.0,
                            maximum_depth_m: float = 0.65) -> dict[str, Any]:
    """Identify visible tread-like faces without using an AABB top height.

    ``horizontal_faces`` is intentionally a plain-Python record list so this
    function can be tested without importing OpenUSD.  Faces are admitted only
    when their authored polygon is horizontal, upward-facing and elongated.
    Large floor slabs and vertical/unknown geometry therefore do not become
    artificial steps.
    """
    if not horizontal_faces:
        return {"status": "unknown", "reason": "no_visible_horizontal_faces",
                "treads": [], "width_axis_world_xy": None, "run_axis_world_xy": None}
    preliminary = []
    for face in _merge_coplanar_faces(horizontal_faces):
        face_axis = _canonical_axis(face["long_edge_axis_world_xy"])
        perpendicular = (-face_axis[1], face_axis[0])
        long_span, short_span, _ = _projected_spans(
            face["points_world_m"], face_axis, perpendicular)
        width, depth = sorted((long_span, short_span), reverse=True)
        if (face["area_m2"] >= minimum_area_m2 and width >= minimum_width_m
                and depth > 1e-6 and width / depth >= minimum_aspect_ratio
                and depth <= maximum_depth_m):
            preliminary.append(face)
    if not preliminary:
        return {"status": "unknown", "reason": "no_tread_like_horizontal_faces",
                "treads": [], "width_axis_world_xy": None,
                "run_axis_world_xy": None}
    width_axis = _infer_width_axis(preliminary)
    run_axis = (-width_axis[1], width_axis[0])
    candidates = []
    for face in preliminary:
        region = _face_region(face, width_axis, run_axis)
        width, depth = sorted((region["width_m"], region["depth_m"]), reverse=True)
        if (region["area_m2"] < minimum_area_m2 or width < minimum_width_m
                or depth <= 1e-6 or width / depth < minimum_aspect_ratio
                or depth > maximum_depth_m):
            continue
        region["width_m"] = width
        region["depth_m"] = depth
        region["long_edge_axis_world_xy"] = face["long_edge_axis_world_xy"]
        candidates.append(region)
    candidates = _deduplicate_faces(candidates)
    if not candidates:
        return {"status": "unknown", "reason": "no_tread_like_horizontal_faces",
                "treads": [], "width_axis_world_xy": list(width_axis),
                "run_axis_world_xy": list(run_axis)}

    # The cross-stair coordinate separates parallel flights.  A large gap is
    # evidence of a distinct flight; small gaps retain one continuous flight.
    median_width = sorted(item["width_m"] for item in candidates)[len(candidates) // 2]
    split_gap = max(0.75 * median_width, 1.0)
    ordered = sorted(candidates, key=lambda item: _dot_xy(item["center_world_m"], width_axis))
    flights: list[list[dict[str, Any]]] = [[]]
    previous = None
    for candidate in ordered:
        coordinate = _dot_xy(candidate["center_world_m"], width_axis)
        if previous is not None and coordinate - previous > split_gap:
            flights.append([])
        flights[-1].append(candidate)
        previous = coordinate

    flight_reports = []
    for index, flight in enumerate(flights, start=1):
        flight_center = sum(_dot_xy(item["center_world_m"], width_axis) for item in flight) / len(flight)
        # A flight is valid only when the measured face centers form a
        # monotonic support-height sequence with nonzero run spacing.
        run_values = [_dot_xy(item["center_world_m"], run_axis) for item in flight]
        z_values = [item["support_z_m"] for item in flight]
        correlation = 0.0
        if len(flight) > 1:
            mean_run = sum(run_values) / len(run_values)
            mean_z = sum(z_values) / len(z_values)
            correlation = sum((r - mean_run) * (z - mean_z)
                              for r, z in zip(run_values, z_values))
        travel_sign = 1.0 if correlation >= 0 else -1.0
        travel_axis = (run_axis[0] * travel_sign, run_axis[1] * travel_sign)
        sequence = sorted(flight, key=lambda item: _dot_xy(item["center_world_m"], travel_axis))
        steps = []
        monotonic = len(sequence) >= 2
        previous_item = None
        for ordinal, item in enumerate(sequence, start=1):
            row = dict(item)
            row.pop("long_edge_axis_world_xy", None)
            row["ordinal"] = ordinal
            if previous_item is None:
                row["riser_height_m"] = None
                row["tread_depth_m"] = None
            else:
                row["riser_height_m"] = item["support_z_m"] - previous_item["support_z_m"]
                row["tread_depth_m"] = abs(
                    _dot_xy(item["center_world_m"], travel_axis)
                    - _dot_xy(previous_item["center_world_m"], travel_axis))
                monotonic = (monotonic and row["riser_height_m"] > 1e-4
                             and row["tread_depth_m"] > 1e-4)
            steps.append(row)
            previous_item = item
        status = "measured" if monotonic and len(steps) >= 2 else "unknown"
        flight_reports.append({
            "flight_id": f"flight_{index:02d}",
            "status": status,
            "candidate_count": len(steps),
            "center_width_coordinate_m": float(flight_center),
            "travel_direction_world_xy": [float(value) for value in travel_axis],
            "treads": steps,
            "reason": None if status == "measured" else "insufficient_monotonic_tread_sequence",
        })
    return {
        "status": "measured" if any(item["status"] == "measured" for item in flight_reports) else "unknown",
        "reason": None if any(item["status"] == "measured" for item in flight_reports)
        else "insufficient_monotonic_tread_sequence",
        "treads": candidates,
        "width_axis_world_xy": list(width_axis),
        "run_axis_world_xy": list(run_axis),
        "flights": flight_reports,
    }


def _unknown_support(reason: str) -> dict[str, Any]:
    return {"status": "unknown", "reason": reason, "region": None}


def infer_support_regions(tread_report: dict[str, Any], support_faces: Sequence[dict[str, Any]], *,
                          level_tolerance_m: float = 0.30,
                          endpoint_tolerance_m: float = 0.85) -> dict[str, Any]:
    """Match start/end regions to authored horizontal support faces.

    A support match requires a real polygon, compatible height, transverse
    overlap and endpoint adjacency.  No match is manufactured from the stair
    bounding box or from the first/last point in a candidate sequence.
    """
    if not tread_report.get("flights"):
        return {"status": "unknown", "reason": "no_measured_flights", "start": _unknown_support("no_measured_flights"),
                "end": _unknown_support("no_measured_flights"), "by_flight": []}
    results = []
    for flight in tread_report["flights"]:
        if flight["status"] != "measured":
            results.append({"flight_id": flight["flight_id"], "status": "unknown",
                            "start": _unknown_support("flight_geometry_unknown"),
                            "end": _unknown_support("flight_geometry_unknown")})
            continue
        treads = flight["treads"]
        first, last = treads[0], treads[-1]
        travel = flight["travel_direction_world_xy"]
        width_axis = tread_report["width_axis_world_xy"]
        run_axis = tread_report["run_axis_world_xy"]
        travel_sign = 1.0 if _dot_xy(travel, run_axis) >= 0 else -1.0
        tread_sources = {
            (item["source"]["prim_path"], face_index)
            for item in treads
            for face_index in item["source"].get(
                "face_indices", [item["source"]["face_index"]])
        }

        def intervals(item):
            run_interval = item["run_interval_m"]
            travel_interval = ([run_interval[0], run_interval[1]]
                               if travel_sign > 0
                               else [-run_interval[1], -run_interval[0]])
            return item["width_interval_m"], travel_interval

        def match(endpoint, *, start: bool):
            endpoint_width, endpoint_run = intervals(endpoint)
            endpoint_center = _dot_xy(endpoint["center_world_m"], travel)
            matches = []
            for face in support_faces:
                source = face.get("source") or {}
                source_indices = source.get("face_indices", [source.get("face_index")])
                if any((source.get("prim_path"), face_index) in tread_sources
                       for face_index in source_indices):
                    continue
                width_interval, run_interval = intervals(face)
                overlap = max(0.0, min(endpoint_width[1], width_interval[1])
                              - max(endpoint_width[0], width_interval[0]))
                endpoint_width_size = endpoint_width[1] - endpoint_width[0]
                if endpoint_width_size <= 0 or overlap / endpoint_width_size < 0.5:
                    continue
                face_center = _dot_xy(face["center_world_m"], travel)
                if start:
                    if run_interval[0] > endpoint_run[1]:
                        continue
                    gap = max(0.0, endpoint_run[0] - run_interval[1])
                    compatible_level = (endpoint["support_z_m"] - level_tolerance_m
                                        <= face["support_z_m"] <= endpoint["support_z_m"] + 0.05)
                else:
                    if run_interval[1] < endpoint_run[0]:
                        continue
                    gap = max(0.0, run_interval[0] - endpoint_run[1])
                    compatible_level = (endpoint["support_z_m"] - 0.05
                                        <= face["support_z_m"] <= endpoint["support_z_m"] + level_tolerance_m)
                if not compatible_level or gap > endpoint_tolerance_m:
                    continue
                # Prefer the closest endpoint-adjacent region, then larger
                # area.  The center coordinate is only a tie-breaker.
                matches.append((gap, -face["area_m2"], abs(face_center - endpoint_center), face))
            if not matches:
                return _unknown_support("no_adjacent_authored_support_face")
            selected = min(matches, key=lambda value: value[:3])[3]
            return {"status": "measured", "reason": None, "region": selected}

        start = match(first, start=True)
        end = match(last, start=False)
        results.append({"flight_id": flight["flight_id"],
                        "status": "measured" if start["status"] == end["status"] == "measured" else "partial",
                        "start": start, "end": end})

    measured = [item for item in results if item["status"] == "measured"]
    if len(results) == 1:
        top_start, top_end = results[0]["start"], results[0]["end"]
        status = "measured" if top_start["status"] == top_end["status"] == "measured" else "partial"
        reason = None if status == "measured" else "one_or_more_endpoint_supports_unknown"
    else:
        top_start = _unknown_support("multiple_flights_require_route_selection")
        top_end = _unknown_support("multiple_flights_require_route_selection")
        status = "unknown"
        reason = "multiple_flights_require_route_selection"
    return {"status": status, "reason": reason, "start": top_start, "end": top_end,
            "by_flight": results, "measured_flight_count": len(measured)}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _layer_record(layer: Any, *, root: bool = False) -> dict[str, Any]:
    identifier = str(layer.identifier)
    real_path = str(getattr(layer, "realPath", "") or "")
    path = Path(real_path).resolve() if real_path else None
    record = {"identifier": identifier, "path": str(path) if path else None,
              "kind": "root" if root else "dependency", "status": "unknown", "sha256": None}
    if path is None:
        record["reason"] = "anonymous_or_unresolved_layer"
    elif not path.is_file():
        record["reason"] = "layer_file_missing"
    else:
        record["status"] = "measured"
        record["sha256"] = _sha256(path)
    return record


def _provenance(stage: Any) -> dict[str, Any]:
    root_layer = stage.GetRootLayer()
    root_record = _layer_record(root_layer, root=True)
    dependencies = []
    ignored_layers = []
    seen = {root_record["path"]}
    for layer in sorted(stage.GetUsedLayers(), key=lambda item: str(item.identifier)):
        record = _layer_record(layer)
        # Stage.Open commonly creates an anonymous session layer.  It is not a
        # persisted scene dependency and cannot be content-hashed, so retain
        # its identity as diagnostic context without failing file provenance.
        if record["path"] is None:
            ignored_layers.append(record)
            continue
        if record["path"] in seen:
            continue
        seen.add(record["path"])
        dependencies.append(record)
    complete = root_record["status"] == "measured" and all(
        item["status"] == "measured" for item in dependencies)
    return {
        "root_layer": root_record,
        "dependency_layers": dependencies,
        "ignored_nonfile_layers": ignored_layers,
        "status": "measured" if complete else "unknown",
        "reason": None if complete else "one_or_more_stage_layers_unhashable",
    }


def _extract_faces(stage: Any, prim: Any, scale_m_per_unit: float, *,
                   world_crop_m: dict[str, Sequence[float]] | None = None
                   ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    from pxr import Usd, UsdGeom

    cache = UsdGeom.XformCache(Usd.TimeCode.Default())
    bbox_cache = UsdGeom.BBoxCache(
        Usd.TimeCode.Default(), [UsdGeom.Tokens.default_, UsdGeom.Tokens.render,
                                 UsdGeom.Tokens.proxy])
    crop_min = crop_max = None
    if world_crop_m is not None:
        if not isinstance(world_crop_m, dict) or set(world_crop_m) != {"min", "max"}:
            raise ValueError("world crop must contain min and max")
        crop_min = _finite(world_crop_m["min"], 3, "world crop minimum")
        crop_max = _finite(world_crop_m["max"], 3, "world crop maximum")
        if any(low > high for low, high in zip(crop_min, crop_max)):
            raise ValueError("world crop minimum exceeds maximum")
    horizontal = []
    support = []
    for candidate_prim in Usd.PrimRange(prim):
        if not candidate_prim.IsA(UsdGeom.Mesh) or not candidate_prim.IsActive():
            continue
        imageable = UsdGeom.Imageable(candidate_prim)
        if imageable.ComputeVisibility(Usd.TimeCode.Default()) == UsdGeom.Tokens.invisible:
            continue
        purpose = imageable.GetPurposeAttr().Get()
        if purpose == UsdGeom.Tokens.guide:
            continue
        if crop_min is not None:
            aligned = bbox_cache.ComputeWorldBound(candidate_prim).ComputeAlignedBox()
            mesh_min = tuple(float(value) * scale_m_per_unit for value in aligned.GetMin())
            mesh_max = tuple(float(value) * scale_m_per_unit for value in aligned.GetMax())
            if any(mesh_max[axis] < crop_min[axis]
                   or mesh_min[axis] > crop_max[axis] for axis in range(3)):
                continue
        mesh = UsdGeom.Mesh(candidate_prim)
        points = mesh.GetPointsAttr().Get()
        counts = mesh.GetFaceVertexCountsAttr().Get()
        indices = mesh.GetFaceVertexIndicesAttr().Get()
        if points is None or counts is None or indices is None:
            continue
        transform = cache.GetLocalToWorldTransform(candidate_prim)
        cursor = 0
        for face_index, count in enumerate(counts):
            if not isinstance(count, int) or count < 3 or cursor + count > len(indices):
                cursor += max(int(count), 0) if isinstance(count, int) else 0
                continue
            world_points = []
            valid = True
            for point_index in indices[cursor:cursor + count]:
                if point_index < 0 or point_index >= len(points):
                    valid = False
                    break
                value = transform.Transform(points[point_index])
                world_points.append([float(value[axis]) * scale_m_per_unit for axis in range(3)])
            cursor += count
            if not valid or len(world_points) < 3:
                continue
            try:
                normal = _polygon_normal(world_points)
                normal_length = math.sqrt(sum(value * value for value in normal))
                z_span = max(point[2] for point in world_points) - min(point[2] for point in world_points)
                if normal_length <= 1e-12 or normal[2] / normal_length < math.cos(math.radians(8)) or z_span > 1e-4:
                    continue
                area, long_axis = _face_area_and_axes(world_points)
            except (TypeError, ValueError, IndexError, OverflowError):
                continue
            face = {"prim_path": str(candidate_prim.GetPath()), "face_index": face_index,
                    "points_world_m": world_points, "area_m2": area,
                    "long_edge_axis_world_xy": list(long_axis)}
            horizontal.append(face)
            # Support candidates are deliberately broader than tread candidates;
            # the matcher applies adjacency and level constraints later.
            if area >= 0.5:
                support.append(face)
    return horizontal, support


def _unknown_report(stage_path: str | Path, prim_path: str, reason: str, *, provenance=None) -> dict[str, Any]:
    return {"schema": SCHEMA, "version": VERSION, "status": "unknown", "reason": reason,
            "stage_path": str(Path(stage_path).resolve()), "prim_path": prim_path,
            "world_aabb_m": None, "direction": None, "treads": None,
            "net_width_m": None, "support_regions": None, "provenance": provenance}


def measure_factory_stair(stage_path: str | Path, prim_path: str = DEFAULT_PRIM_PATH) -> dict[str, Any]:
    """Return a JSON-safe static measurement report for one Factory stair prim."""
    stage_path = Path(stage_path).resolve()
    if not stage_path.is_file():
        return _unknown_report(stage_path, prim_path, "stage_file_missing")
    try:
        from pxr import Usd, UsdGeom
    except ImportError:
        return _unknown_report(stage_path, prim_path, "openusd_unavailable")
    stage = Usd.Stage.Open(str(stage_path))
    if stage is None:
        return _unknown_report(stage_path, prim_path, "stage_open_failed")
    provenance = _provenance(stage)
    prim = stage.GetPrimAtPath(prim_path)
    if not prim or not prim.IsValid():
        return _unknown_report(stage_path, prim_path, "prim_missing", provenance=provenance)
    try:
        scale = float(UsdGeom.GetStageMetersPerUnit(stage))
        if not math.isfinite(scale) or scale <= 0:
            raise ValueError("invalid_stage_meters_per_unit")
        bbox = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_,
                                                           UsdGeom.Tokens.render,
                                                           UsdGeom.Tokens.proxy]).ComputeWorldBound(prim).ComputeAlignedBox()
        aabb = {"min": [float(value) * scale for value in bbox.GetMin()],
                "max": [float(value) * scale for value in bbox.GetMax()]}
        _finite(aabb["min"], 3, "world AABB minimum")
        _finite(aabb["max"], 3, "world AABB maximum")
        horizontal, _ = _extract_faces(stage, prim, scale)
        tread_faces = []
        for face in horizontal:
            area, long_axis = _face_area_and_axes(face["points_world_m"])
            tread_faces.append({**face, "area_m2": area, "long_edge_axis_world_xy": list(long_axis)})
        tread_report = detect_tread_candidates(tread_faces)
        direction = None
        support_regions = None
        support_search_world_aabb = None
        net_width = None
        if tread_report["status"] == "measured":
            widths = [item["width_m"] for item in tread_report["treads"]]
            net_width = {"status": "measured_from_visible_tread_faces",
                         "value_m": float(min(widths)),
                         "basis": "minimum transverse span of authored horizontal tread polygons",
                         "limitation": "external handrail/wall clearance and robot-specific clearance are not measured"}
            # Endpoint supports may be authored as neighboring floor/platform
            # prims rather than descendants of the stair asset. Search the
            # stage for support polygons while keeping tread detection scoped
            # strictly to the declared stair prim.
            # Reject meshes before decoding their topology when their world
            # bounds cannot touch either stair endpoint.  The previous full
            # stage face walk took about 90 seconds on the production asset.
            # A one-metre planar margin exceeds the endpoint matcher tolerance;
            # the vertical margin covers its allowed support-level offsets.
            support_search_world_aabb = {
                "min": [aabb["min"][0] - 1.0, aabb["min"][1] - 1.0,
                        aabb["min"][2] - 0.35],
                "max": [aabb["max"][0] + 1.0, aabb["max"][1] + 1.0,
                        aabb["max"][2] + 0.35],
            }
            _, support_faces = _extract_faces(
                stage, stage.GetPseudoRoot(), scale,
                world_crop_m=support_search_world_aabb)
            support_records = []
            width_axis = tread_report["width_axis_world_xy"]
            run_axis = tread_report["run_axis_world_xy"]
            for face in _merge_coplanar_faces(support_faces):
                support_records.append(_face_region(face, width_axis, run_axis))
            support_regions = infer_support_regions(tread_report, support_records)
            direction = {
                "status": "measured",
                "run_axis_world_xy": tread_report["run_axis_world_xy"],
                "width_axis_world_xy": tread_report["width_axis_world_xy"],
                "flight_directions_world_xy": [item["travel_direction_world_xy"]
                                                for item in tread_report["flights"]],
                "method": "repeated_visible_upward_horizontal_mesh_faces",
            }
        else:
            tread_report = {key: value for key, value in tread_report.items()
                            if key != "treads"} | {"treads": None}
        status = "measured" if tread_report["status"] == "measured" and provenance["status"] == "measured" else "unknown"
        reason = None if status == "measured" else (
            "layer_hash_incomplete" if provenance["status"] != "measured" else tread_report.get("reason"))
        return {
            "schema": SCHEMA, "version": VERSION, "status": status, "reason": reason,
            "stage_path": str(stage_path), "prim_path": prim_path,
            "stage_metadata": {"meters_per_unit": scale, "up_axis": stage.GetMetadata("upAxis")},
            "world_aabb_m": aabb,
            "direction": direction,
            "treads": tread_report,
            "net_width_m": net_width,
            "support_regions": support_regions,
            "support_search_world_aabb_m": support_search_world_aabb,
            "provenance": provenance,
            "limitations": [
                "static authored geometry is not contact or collision evidence",
                "tread candidates are visible polygon evidence, not a robot-specific walkable route",
                "support regions are unknown unless an adjacent authored horizontal face matches",
                "the stair AABB maximum is never used as a support height",
            ],
        }
    except (TypeError, ValueError, RuntimeError, IndexError, OverflowError) as exc:
        return _unknown_report(stage_path, prim_path, f"geometry_measurement_failed:{exc}", provenance=provenance)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", required=True, type=Path, help="OpenUSD stage to inspect")
    parser.add_argument("--prim-path", default=DEFAULT_PRIM_PATH, help="stair prim path")
    args = parser.parse_args(argv)
    print(json.dumps(measure_factory_stair(args.stage, args.prim_path), ensure_ascii=False,
                     sort_keys=True, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["DEFAULT_PRIM_PATH", "SCHEMA", "detect_tread_candidates",
           "infer_support_regions", "main", "measure_factory_stair"]
