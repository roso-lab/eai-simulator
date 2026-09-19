"""Read-only, bounded live support/clearance samples; never route admission.

The caller owns the reset stage and scene query. A query object must implement
raycast_all(origin, direction, distance, callback); a callable with the same
signature is also accepted. No simulator, asset resolver or ROS is started.
"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import math
from pathlib import Path

from tools.validation import ground_health_probe as _support
from demo.benchmark import provenance as _provenance
from demo.benchmark.provenance import canonical_sha256


MAX_QUERIES = 4096
MAX_POINTS = 16
MAX_BINDINGS = 256
MAX_HITS = 256
MAX_RAY_M = 10.0


def _vector(value, size=3):
    if not isinstance(value, (list, tuple)) or len(value) != size:
        raise ValueError(f"expected {size} coordinates")
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
           for v in value):
        raise ValueError("coordinates must be finite numbers")
    return [float(v) for v in value]


def _point(value):
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return [*_vector(value, 2), 0.0], "2d_search_window_only_not_declared_surface_height"
    return _vector(value), "deployment_world_coordinates"


def _sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _finite_json(value):
    # The shared raycaster retains malformed raw distances as error evidence.
    # Preserve the unknown status while making those payloads strict JSON.
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: _finite_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_finite_json(item) for item in value]
    return value


def _pick(points):
    if len(points) <= MAX_POINTS:
        return list(points)
    return [points[round(i * (len(points) - 1) / (MAX_POINTS - 1))] for i in range(MAX_POINTS)]


class _Query:
    def __init__(self, query):
        self.query = query
        self.count = 0

    def raycast_all(self, origin, direction, distance, callback):
        if self.query is None:
            raise RuntimeError("scene_query_unavailable")
        if self.count >= MAX_QUERIES:
            raise RuntimeError("scene_query_budget_exhausted")
        self.count += 1
        operation = getattr(self.query, "raycast_all", self.query)
        return operation(origin, direction, distance, callback)


def _classifier(stage, roots):
    from pxr import UsdPhysics

    def classify(hit):
        path = hit["collision"]
        if any(_support.under(path, root) for root in roots):
            return "robot"
        prim = stage.GetPrimAtPath(path)
        if not prim.IsValid() or not prim.IsActive():
            return "missing_or_inactive_prim"
        if not prim.HasAPI(UsdPhysics.CollisionAPI):
            return "missing_collision_api"
        if not UsdPhysics.CollisionAPI(prim).GetCollisionEnabledAttr().Get():
            return "disabled_collision"
        while prim.IsValid() and not prim.IsPseudoRoot():
            if prim.HasAPI(UsdPhysics.ArticulationRootAPI):
                return "dynamic_or_kinematic"
            if prim.HasAPI(UsdPhysics.RigidBodyAPI):
                enabled = UsdPhysics.RigidBodyAPI(prim).GetRigidBodyEnabledAttr().Get()
                if enabled is not False:
                    return "dynamic_or_kinematic"
            prim = prim.GetParent()
        return "static"

    return classify


def _clearance(query, classify, roots, origin, direction, distance):
    result = {"origin_xyz_m": origin, "direction": direction, "length_m": distance,
              "status": "unknown", "hits": [], "excluded_hits": []}
    hits, errors = [], []

    def report(hit):
        if len(hits) >= MAX_HITS:
            errors.append("hit_limit")
            return False
        try:
            get = hit.get if isinstance(hit, Mapping) else lambda key: getattr(hit, key, None)
            row = {"collision": str(get("collision") or ""),
                   "rigid_body": str(get("rigid_body") or ""),
                   "position": _vector(list(get("position"))),
                   "normal": _vector(list(get("normal"))), "distance": float(get("distance"))}
            if not math.isfinite(row["distance"]):
                raise ValueError("nonfinite distance")
            hits.append(row)
        except (ValueError, TypeError, OverflowError):
            errors.append("malformed_hit")
        return not errors

    try:
        if not 0 < distance <= MAX_RAY_M:
            raise ValueError("clearance_length_outside_bounded_query")
        query.raycast_all(origin, direction, distance, report)
        if errors:
            raise ValueError(errors[0])
        for hit in hits:
            paths = [hit[k] for k in ("collision", "rigid_body") if hit[k]]
            if not hit["collision"].startswith("/") or any(not p.startswith("/") for p in paths):
                raise ValueError("unidentified_hit")
            expected = [origin[i] + direction[i] * hit["distance"] for i in range(3)]
            if not 0 <= hit["distance"] <= distance or math.dist(hit["position"], expected) > .005:
                raise ValueError("inconsistent_hit")
            hit["kind"] = classify(hit)
            if any(_support.under(p, root) for p in paths for root in roots):
                result["excluded_hits"].append(hit)
            else:
                result["hits"].append(hit)
        result["hits"].sort(key=lambda h: h["distance"])
        result["status"] = "blocked" if result["hits"] else "sampled_no_hit"
        if result["hits"]:
            result["nearest_hit"] = result["hits"][0]
    except Exception as exc:
        result["reason"] = f"{type(exc).__name__}:{exc}"
    return result


def _geometry_points(binding):
    """Return authored centers/corners/boundary vertices, never invent ingress."""
    candidate = binding.get("candidate_geometry") or {}
    if "center_m" in candidate:
        center = _vector(candidate["center_m"])
        points = [center]
        if "aabb_xy_m" in candidate:
            x0, y0, x1, y1 = _vector(candidate["aabb_xy_m"], 4)
            if x0 > x1 or y0 > y1:
                raise ValueError("invalid region bounds")
            points += [[x, y, center[2]] for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]
        return points, "candidate_center_and_aabb_corners"
    geometry = binding.get("geometry") or {}
    kind, coords = geometry.get("type"), geometry.get("coordinates")
    if kind == "Point":
        return [coords], "declared_point"
    if kind == "Polygon":
        return [p for ring in coords for p in ring], "polygon_boundary_vertices_only"
    if kind == "MultiPolygon":
        return [p for polygon in coords for ring in polygon for p in ring], "polygon_boundary_vertices_only"
    return [], "missing_supported_geometry"


def _route_points(binding):
    planning = binding.get("planning_candidate") or {}
    if planning.get("polyline_m"):
        return [(binding["id"], planning["polyline_m"])]
    geometry = binding.get("geometry") or {}
    if geometry.get("type") == "DirectedPolylineSet":
        return [(r["id"], r["points_xy_m"]) for r in geometry["routes"]]
    return []


def collect_live_spatial(stage, deployment, *, query=None, robot_roots=()):
    """Collect JSON evidence at deployment proposals in the supplied live stage.

    Metric Z-up stages only; no implicit scene/env transform is applied. Queries
    are capped at 4096, bindings at 256/category and sample points at 16/path.
    Query injection proves adapter behavior only, not physical authenticity.
    """
    from pxr import UsdGeom

    overlay = deployment.get("scene_overlay", {})
    public = deployment.get("public_task", {})
    roots = list(robot_roots) or [r["prim_path"] for r in deployment.get("robot_instances", [])]
    result = {
        "schema": "eai.benchmark.goal_live_spatial", "version": 1, "visibility": "author_only",
        "scene_key": deployment.get("scene_key"), "deployment_sha256": deployment.get("sha256"),
        "deployment_content_sha256": canonical_sha256(deployment),
        "source_files": {str(Path(p).resolve()): _sha(p)
                         for p in (__file__, _support.__file__, _provenance.__file__)},
        "frame": {"coordinate_frame": "USD_world", "deployment_transform": "identity_required",
                  "meters_per_unit": None, "up_axis": None},
        "sampling_policy": {"max_queries": MAX_QUERIES, "max_points_per_path": MAX_POINTS,
                            "max_bindings_per_category": MAX_BINDINGS,
                            "support_ray_length_m": _support.RAY_LENGTH,
                            "support_origin_offset_above_reference_m": 1.0,
                            "scope": "discrete_rays_not_continuous_support_or_swept_volume"},
        "robot_exclusion_roots": roots, "regions": [], "routes": [], "spawns": [],
        "vertical_links": [], "scene_layers": [], "blockers": [],
        "traversal_validated": False, "physical_execution_validated": False,
        "measurement_authenticity_validated": False, "scoreable": False,
    }
    failure = None
    try:
        result["frame"].update(meters_per_unit=UsdGeom.GetStageMetersPerUnit(stage),
                               up_axis=str(UsdGeom.GetStageUpAxis(stage)))
        if result["frame"]["meters_per_unit"] != 1.0 or result["frame"]["up_axis"] != "Z":
            failure = "unsupported_stage_units_or_up_axis"
        if not roots or any(not isinstance(p, str) or p == "/" or not p.startswith("/")
                            or not stage.GetPrimAtPath(p).IsValid() for p in roots):
            failure = "robot_exclusion_roots_unavailable_or_invalid"
        for layer in stage.GetUsedLayers():
            path = layer.realPath
            result["scene_layers"].append({"identifier": layer.identifier,
                "sha256": _sha(path) if path and Path(path).is_file() else None,
                "dirty": bool(layer.dirty), "scope": "backing_file_not_live_composed_state"})
    except Exception as exc:
        failure = f"stage_readback_error:{type(exc).__name__}:{exc}"
    if query is None:
        failure = failure or "scene_query_unavailable"
    if failure:
        result["blockers"].append(failure)
    queries = _Query(query)
    classify = _classifier(stage, roots)
    support = _support.SupportRaycaster(queries, roots, classify)

    def evidence(identifier, points, *, basis, envelope=None, aerial=False, patch_radius=None):
        row = {"id": identifier, "status": "blocked", "sampling_basis": basis,
               "samples": [], "blockers": [], "traversal_validated": False}
        if aerial:
            row["blockers"].append("unsupported_aerial_volume_evidence")
            return row
        if not points:
            row["blockers"].append("missing_geometry")
            return row
        if failure:
            row["blockers"].append(failure)
            return row
        row["available_point_count"] = len(points)
        row["sampling_truncated"] = len(points) > MAX_POINTS
        try:
            if envelope is not None:
                width, height = _vector(envelope, 2)
                if not 0 < width <= 2 * MAX_RAY_M or not .02 < height <= MAX_RAY_M:
                    raise ValueError("unsupported_clearance_envelope")
                row["declared_envelope_width_height_m"] = [width, height]
            for point in _pick(points):
                xyz, coordinate_basis = _point(point)
                origin = [xyz[0], xyz[1], xyz[2] + 1.0]
                measured = support.cast(origin)
                sample = {"reference_xyz_m": xyz, "coordinate_basis": coordinate_basis,
                          "support": measured, "clearance_rays": []}
                row["samples"].append(sample)
                if patch_radius is not None:
                    patch = support.patch(origin, patch_radius)
                    sample["support_patch"] = patch
                    sample["support_patch_radius_m"] = patch_radius
                    if patch["status"] != "measured":
                        row["blockers"].append("support_patch_unknown_or_nonflat")
                if measured["status"] != "measured":
                    row["blockers"].append("support_sample_unknown")
                    continue
                sample["support_minus_reference_z_m"] = measured["support_z_m"] - xyz[2]
                if envelope is None:
                    continue
                z = measured["support_z_m"]
                # Sparse centerline vertical and horizontal spokes, never a
                # collision volume or a profile/footprint certificate.
                rays = [([xyz[0], xyz[1], z + .01], [0., 0., 1.], height - .01)]
                for dz in (.05, height / 2):
                    for direction in ([1., 0., 0.], [-1., 0., 0.], [0., 1., 0.], [0., -1., 0.]):
                        rays.append(([xyz[0], xyz[1], z + dz], direction, width / 2))
                sample["clearance_rays"] = [_clearance(queries, classify, roots, *ray) for ray in rays]
                if any(r["status"] != "sampled_no_hit" for r in sample["clearance_rays"]):
                    row["blockers"].append("clearance_sample_blocked_or_unknown")
            if not row["blockers"]:
                row["status"] = "sampled"
        except (ValueError, TypeError, KeyError) as exc:
            row["blockers"].append(f"invalid_geometry:{exc}")
        row["blockers"] = sorted(set(row["blockers"]))
        return row

    def bounded(rows, category):
        if len(rows) > MAX_BINDINGS:
            result["blockers"].append(f"{category}:binding_limit")
        return rows[:MAX_BINDINGS]

    regions = deployment.get("region_bindings", overlay.get("region_bindings", []))
    region_by_id = {r["id"]: r for r in regions}
    for binding in bounded(regions, "regions"):
        declaration = binding.get("public_region", {})
        try:
            points, basis = _geometry_points(binding)
        except (ValueError, TypeError, KeyError) as exc:
            points, basis = [], f"invalid_region_geometry:{exc}"
        result["regions"].append(evidence(binding["id"], points, basis=basis,
            aerial=declaration.get("access_modes") == ["aerial_navigation"]))
    for binding in bounded(deployment.get("route_bindings", overlay.get("route_bindings", [])), "routes"):
        declaration = binding.get("public_route", {})
        paths = _route_points(binding)
        row = {"id": binding["id"], "paths": [], "status": "blocked", "blockers": [],
               "traversal_validated": False}
        if "aerial_navigation" in declaration.get("access_modes", []):
            row["blockers"].append("unsupported_aerial_volume_evidence")
        elif not paths:
            row["blockers"].append("missing_route_polyline_no_endpoint_interpolation")
        else:
            for path_id, vertices in bounded(paths, "route_paths"):
                # Include midpoints to expose endpoint-only blind spots. Cap
                # samples explicitly; unsampled spans remain unverified.
                points = []
                for a, b in zip(vertices, vertices[1:]):
                    pa, _ = _point(a)
                    pb, _ = _point(b)
                    midpoint = [(x + y) / 2 for x, y in zip(pa, pb)]
                    if len(a) == len(b) == 2:
                        midpoint = midpoint[:2]
                    points.extend([a, midpoint])
                points.extend(vertices[-1:])
                row["paths"].append(evidence(path_id, points, basis="polyline_vertices_and_segment_midpoints",
                    envelope=declaration.get("clearance_m")))
            if all(p["status"] == "sampled" for p in row["paths"]):
                row["status"] = "sampled"
            else:
                row["blockers"].append("route_samples_incomplete_or_blocked")
        result["routes"].append(row)
    for robot in bounded(deployment.get("robot_instances", []), "spawns"):
        spawn = robot.get("spawn_binding", {})
        pos = (spawn.get("deployment_directive") or {}).get("position_m", spawn.get("position_xyz_m"))
        declaration = robot.get("public_declaration", spawn.get("public_robot", {}))
        body = robot.get("physical_profile", {}).get("body_size_m")
        envelope = None
        if body:
            envelope = [math.hypot(*body[:2]), body[2]]
        row = evidence(robot["public_id"], [pos] if pos is not None else [],
                       basis="deployment_spawn_proposal_not_measured_robot_pose", envelope=envelope,
                       aerial=declaration.get("mobility") == "aerial",
                       patch_radius=envelope[0] / 2 if envelope else None)
        row["runtime_prim_path"] = robot.get("prim_path")
        row["robot_pose_measured"] = False
        if body is None:
            row["status"] = "blocked"
            row["blockers"].append("missing_public_body_envelope")
        result["spawns"].append(row)
    links = overlay.get("vertical_link_bindings", [])
    if not links:
        links = [{"id": r["id"], "public_vertical_link": r}
                 for r in public.get("public_world", {}).get("vertical_links", [])]
    for binding in bounded(links, "vertical_links"):
        declaration = binding.get("public_vertical_link", {})
        endpoints = []
        for key in ("from_region_id", "to_region_id"):
            region = region_by_id.get(declaration.get(key), {})
            points, basis = _geometry_points(region)
            endpoints.append(evidence(declaration.get(key), points[:1], basis="vertical_endpoint:" + basis))
        result["vertical_links"].append({"id": binding["id"], "status": "blocked",
            "endpoint_checks": endpoints, "traversal_validated": False,
            "blockers": ["missing_bound_stair_tread_geometry", "continuous_stair_support_and_traversal_unverified"]})
    result["query_count"] = queries.count
    if queries.count >= MAX_QUERIES:
        result["blockers"].append("scene_query_budget_reached")
    result["blockers"].extend(["discrete_samples_do_not_establish_traversability",
                               "robot_footprint_volume_and_dynamic_occupancy_unverified",
                               "deployment_to_live_world_alignment_not_independently_verified"])
    result["status"] = "diagnostic_only"
    return _finite_json(result)


__all__ = ["collect_live_spatial"]
