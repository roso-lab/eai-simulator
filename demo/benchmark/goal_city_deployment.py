"""Build an author-only deployment overlay for the maintained city draft.

The overlay binds public identities to existing static map evidence.  It does
not activate city services, disclose private incident state, validate physics,
or promote the preparation draft to a scoreable goal bundle.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import hashlib
import math
from pathlib import Path
from typing import Any

from . import goal_city_intake as _goal_city_intake_module
from . import provenance as _provenance_module
from .goal_city_intake import load_city_goal_candidate
from .provenance import canonical_sha256


_PENDING_CITY_SERVICES = frozenset({"guide_people", "control_traffic", "report_incident"})
_ROLE_INTERVAL_SOURCE = {
    "method": "CityServiceSource.role_interval",
    "adapter_parameter": "CityRuntimeReadbackAdapter.field_role_interval",
    "signature": "role_interval(agent_id, role, region_id, as_of_s)",
    "trust_boundary": "episode_owned_historical_role_readback_not_participant_supplied",
}
_PENDING_SERVICE_METHODS = {
    "guide_people": {
        "method": "CityServiceRequestAdapter.guide_people",
        "signature": "guide_people(*, person_ids, observation_refs, assembly_region_id, now_s=None)",
        "response_reference_method": "CityResponseServices.guide_people",
        "parameters": ["person_ids", "observation_refs", "assembly_region_id", "now_s"],
        "request_ack_scope": "trusted_adapter_request_ack_only",
        "required_trusted_source_callback": deepcopy(_ROLE_INTERVAL_SOURCE),
        "role_continuity_requirement": "submit_through_each_actor_ack",
        "post_ack_trajectory_role_requirement": "original_member_may_exit_after_its_ack",
        "assignment_transfer": "unsupported_new_request_required",
        "external_effect_evidence": ["measured_person_trajectory", "assembly_arrival"],
    },
    "control_traffic": {
        "method": "CityServiceRequestAdapter.control_following_traffic",
        "signature": "control_following_traffic(vehicle_ids, mode, *, observation_refs)",
        "response_reference_method": "CityResponseServices.control_following_traffic",
        "parameters": ["vehicle_ids", "mode", "observation_refs"],
        "request_ack_scope": "trusted_adapter_request_ack_only",
        "required_trusted_source_callback": deepcopy(_ROLE_INTERVAL_SOURCE),
        "role_continuity_requirement": "submit_through_each_actor_ack",
        "post_ack_trajectory_role_requirement": "original_member_may_exit_after_its_ack",
        "assignment_transfer": "unsupported_new_request_required",
        "external_effect_evidence": ["measured_vehicle_trajectory", "continuous_response_access"],
    },
    "report_incident": {
        "method": "CityServiceRequestAdapter.report_incident",
        "signature": "report_incident(observation_refs, recipient_service_id, *, time_s=None)",
        "response_reference_method": "CityResponseServices.report_incident",
        "parameters": ["observation_refs", "recipient_service_id", "time_s"],
        "request_ack_scope": "trusted_adapter_request_ack_only",
        "required_trusted_source_callback": deepcopy(_ROLE_INTERVAL_SOURCE),
        "role_continuity_requirement": "submit_only",
        "post_ack_trajectory_role_requirement": "not_applicable",
        "assignment_transfer": "unsupported_new_request_required",
        "external_effect_evidence": ["recipient_service_receipt", "observation_provenance"],
    },
}


def load_city_deployment_public(
    city_package_dir: str | Path, *, verify_sources: bool = True,
) -> dict[str, Any]:
    """Return only the validated public task from a city preparation package."""
    candidate = load_city_goal_candidate(city_package_dir, verify_sources=verify_sources)
    return deepcopy(candidate["public_task"])


def _sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _source_files(candidate: Mapping[str, Any]) -> dict[str, str]:
    root = Path(candidate["package_dir"])
    manifest = candidate["manifest"]
    files = {
        str((root / relative).resolve()): digest
        for relative, digest in manifest["artifact_sha256"].items()
    }
    manifest_path = (root / "integration_manifest.json").resolve()
    files[str(manifest_path)] = candidate["intake_review"]["manifest_file_sha256"]

    source = candidate["source_binding"]
    source_root = Path(source["source_root"])
    for relative, digest in source["source_sha256"].items():
        files[str((source_root / relative).resolve())] = digest
    for absolute, digest in source["composed_usd_layers_sha256"].items():
        files[str(Path(absolute).resolve())] = digest
    for path in (
        Path(__file__).resolve(),
        Path(_goal_city_intake_module.__file__).resolve(),
        Path(_provenance_module.__file__).resolve(),
        Path(_provenance_module.REPO_ROOT, "demo/city_benchmark/service_backend.py").resolve(),
        Path(_provenance_module.REPO_ROOT, "demo/city_benchmark/episode_adapter.py").resolve(),
        Path(_provenance_module.REPO_ROOT, "demo/city_benchmark/response.py").resolve(),
    ):
        if not path.is_file():
            raise ValueError(f"city deployment interface source missing: {path}")
        files[str(path)] = _sha256(path)
    return dict(sorted(files.items()))


def _occupancy_reader(candidate: Mapping[str, Any]):
    from PIL import Image

    root = Path(candidate["package_dir"])
    path = root / "release/maps/occupancy.png"
    expected = candidate["manifest"]["artifact_sha256"]["release/maps/occupancy.png"]
    if _sha256(path) != expected:
        raise ValueError("city occupancy map changed after intake validation")
    metadata = candidate["public_maps"]["metadata"]
    if metadata["image_row_zero"] != "north":
        raise ValueError("unsupported city occupancy row convention")
    with Image.open(path) as source_image:
        image = source_image.convert("L")
        image.load()

    labels = {number: label for label, number in metadata["occupancy_values"].items()}

    def read_cell(column: int, row: int) -> dict[str, Any]:
        if not (0 <= column < metadata["width"] and 0 <= row < metadata["height"]):
            return {"pixel_column_row": [column, row], "value": None, "classification": "outside"}
        value = image.getpixel((column, row))
        return {
            "pixel_column_row": [column, row],
            "value": value,
            "classification": labels.get(value, "undeclared_value"),
        }

    def read_point(point_xy: list[float]) -> dict[str, Any]:
        resolution = metadata["resolution_m"]
        origin = metadata["origin_xyz_m"]
        column = math.floor((point_xy[0] - origin[0]) / resolution)
        south_row = math.floor((point_xy[1] - origin[1]) / resolution)
        row = metadata["height"] - 1 - south_row
        return read_cell(column, row)

    return read_point, read_cell


def _region_bindings(public: Mapping[str, Any], semantics: Mapping[str, Any]) -> list[dict[str, Any]]:
    public_regions = public["public_world"]["regions"]
    map_regions = semantics["regions"]
    map_bindings = semantics["bindings"]
    if map_regions != public_regions:
        raise ValueError("city semantic-map regions must exactly equal public regions")
    by_id = {row["id"]: row for row in map_bindings}
    public_ids = [row["id"] for row in public_regions]
    if len(by_id) != len(map_bindings) or set(by_id) != set(public_ids):
        raise ValueError("city semantic geometry must cover each public region exactly once")
    return [
        {
            "id": row["id"],
            "status": "map_checked",
            "evidence": "exact_public_and_semantic_map_identity",
            "coordinate_frame": semantics["coordinate_frame"],
            "public_region": deepcopy(row),
            "geometry": deepcopy(by_id[row["id"]]["geometry"]),
            "map_attributes": {
                key: deepcopy(value)
                for key, value in by_id[row["id"]].items()
                if key not in {"id", "geometry"}
            },
        }
        for row in public_regions
    ]


def _object_bindings(public: Mapping[str, Any], semantics: Mapping[str, Any]) -> list[dict[str, Any]]:
    obstacles = {row["id"]: row for row in semantics["static_obstacles"]}
    bindings = []
    for row in public["public_world"]["objects"]:
        obstacle = obstacles.get(row["id"])
        bindings.append({
            "id": row["id"],
            "status": "map_checked" if obstacle is not None else "declared_unmapped",
            "evidence": "exact_static_obstacle_identity" if obstacle is not None else "public_task_only",
            "public_object": deepcopy(row),
            "physical_properties": deepcopy(row.get("physical_properties")),
            "map_bounds_m": deepcopy(obstacle["bounds_m"]) if obstacle is not None else None,
            "mass_policy": "frozen_from_public_task_never_inferred",
        })
    return bindings


def _occupancy_summary(points: list[list[float]], read_occupancy) -> dict[str, Any]:
    counts = {"free": 0, "occupied": 0, "unknown": 0, "outside": 0, "undeclared_value": 0}
    samples = []
    for point in points:
        sample = read_occupancy(point)
        counts[sample["classification"]] += 1
        samples.append(sample)
    return {
        "point_count": len(points),
        "classification_counts": counts,
        "all_sampled_points_free": counts["free"] == len(points),
        "samples": samples,
        "scope": "polyline_vertices_only_not_segment_sweep_or_robot_footprint",
    }


def _segment_supercover_cells(
    start_xy: list[float], finish_xy: list[float], metadata: Mapping[str, Any],
) -> list[tuple[int, int]]:
    """Return every raster cell touched by a closed world-space segment."""
    resolution = metadata["resolution_m"]
    origin = metadata["origin_xyz_m"]
    height = metadata["height"]
    x0 = (start_xy[0] - origin[0]) / resolution
    y0 = (start_xy[1] - origin[1]) / resolution
    x1 = (finish_xy[0] - origin[0]) / resolution
    y1 = (finish_xy[1] - origin[1]) / resolution
    if any(not math.isfinite(value) for value in (x0, y0, x1, y1)):
        raise ValueError("route segment coordinates must be finite")

    cell_x, cell_y = math.floor(x0), math.floor(y0)
    target_x, target_y = math.floor(x1), math.floor(y1)
    delta_x, delta_y = x1 - x0, y1 - y0
    step_x = 1 if delta_x > 0 else -1 if delta_x < 0 else 0
    step_y = 1 if delta_y > 0 else -1 if delta_y < 0 else 0
    t_delta_x = abs(1.0 / delta_x) if step_x else math.inf
    t_delta_y = abs(1.0 / delta_y) if step_y else math.inf
    next_x = cell_x + 1 if step_x > 0 else cell_x
    next_y = cell_y + 1 if step_y > 0 else cell_y
    t_max_x = (next_x - x0) / delta_x if step_x else math.inf
    t_max_y = (next_y - y0) / delta_y if step_y else math.inf
    south_cells = {(cell_x, cell_y)}
    remaining_steps = abs(target_x - cell_x) + abs(target_y - cell_y) + 1
    while (cell_x, cell_y) != (target_x, target_y):
        if remaining_steps <= 0:
            raise ValueError("route segment raster traversal exceeded its bounded grid distance")
        remaining_steps -= 1
        next_t_x = t_max_x if cell_x != target_x else math.inf
        next_t_y = t_max_y if cell_y != target_y else math.inf
        if math.isclose(next_t_x, next_t_y, rel_tol=0.0, abs_tol=1e-12):
            south_cells.add((cell_x + step_x, cell_y))
            south_cells.add((cell_x, cell_y + step_y))
            cell_x += step_x
            cell_y += step_y
            t_max_x += t_delta_x
            t_max_y += t_delta_y
        elif next_t_x < next_t_y:
            cell_x += step_x
            t_max_x += t_delta_x
        else:
            cell_y += step_y
            t_max_y += t_delta_y
        south_cells.add((cell_x, cell_y))

    # A segment exactly on a grid boundary touches cells on both sides. Closed
    # endpoint cells are also included conservatively when an endpoint is on a
    # grid line; the authored map already accounts for cell extent.
    if step_x == 0 and math.isclose(x0, round(x0), rel_tol=0.0, abs_tol=1e-12):
        south_cells.update((x - 1, y) for x, y in tuple(south_cells))
    if step_y == 0 and math.isclose(y0, round(y0), rel_tol=0.0, abs_tol=1e-12):
        south_cells.update((x, y - 1) for x, y in tuple(south_cells))
    for endpoint_x, endpoint_y in ((x0, y0), (x1, y1)):
        xs = [math.floor(endpoint_x)]
        ys = [math.floor(endpoint_y)]
        if math.isclose(endpoint_x, round(endpoint_x), rel_tol=0.0, abs_tol=1e-12):
            xs.append(math.floor(endpoint_x) - 1)
        if math.isclose(endpoint_y, round(endpoint_y), rel_tol=0.0, abs_tol=1e-12):
            ys.append(math.floor(endpoint_y) - 1)
        south_cells.update((x, y) for x in xs for y in ys)
    return sorted((column, height - 1 - south_row) for column, south_row in south_cells)


def _continuous_raster_check(points: list[list[float]], metadata: Mapping[str, Any], read_cell):
    all_cells: set[tuple[int, int]] = set()
    blocked_segments = []
    for index, (start, finish) in enumerate(zip(points, points[1:])):
        cells = _segment_supercover_cells(start, finish, metadata)
        samples = [read_cell(column, row) for column, row in cells]
        if any(sample["classification"] != "free" for sample in samples):
            blocked_segments.append(index)
        all_cells.update(cells)
    samples = [read_cell(column, row) for column, row in sorted(all_cells)]
    counts = {"free": 0, "occupied": 0, "unknown": 0, "outside": 0, "undeclared_value": 0}
    for sample in samples:
        counts[sample["classification"]] += 1
    return {
        "segment_count": max(0, len(points) - 1),
        "supercover_cell_count": len(samples),
        "classification_counts": counts,
        "all_supercover_cells_free": counts["free"] == len(samples),
        "blocked_segment_indices": blocked_segments,
        "nonfree_cells": [sample for sample in samples if sample["classification"] != "free"],
        "scope": "closed_segment_cell_supercover_on_packaged_preinflated_raster",
    }


def _ground_profile_review(
    robot: Mapping[str, Any], public_route: Mapping[str, Any], raster_check: Mapping[str, Any],
    map_inflation_m: float,
) -> dict[str, Any]:
    body = robot["physical_profile"]["body_size_m"]
    radius = math.hypot(body[0], body[1]) / 2.0
    diameter = 2.0 * radius
    width_clearance = public_route["clearance_m"][0]
    height_clearance = public_route["clearance_m"][1]
    covered = radius <= map_inflation_m
    blockers = []
    if not raster_check["all_supercover_cells_free"]:
        blockers.append("continuous_supercover_contains_nonfree_cells")
    if not covered:
        blockers.append("conservative_envelope_radius_exceeds_existing_map_inflation")
    if width_clearance < diameter or height_clearance < body[2]:
        blockers.append("public_clearance_smaller_than_conservative_profile_envelope")
    blockers.append("dynamic_occupancy_and_physical_execution_not_validated")
    return {
        "id": robot["id"],
        "robot_type": robot["type"],
        "body_size_xyz_m": deepcopy(body),
        "conservative_xy_envelope": "circumscribed_circle_from_frozen_public_body_size",
        "conservative_radius_m": radius,
        "conservative_diameter_m": diameter,
        "packaged_map_inflation_m": map_inflation_m,
        "existing_inflation_covers_radius": covered,
        "additional_inflation_required_m": max(0.0, radius - map_inflation_m),
        "inflation_policy": "consume_packaged_inflation_once_no_second_raster_dilation",
        "declared_clearance_width_m": width_clearance,
        "declared_clearance_height_m": height_clearance,
        "declared_width_margin_m": width_clearance - diameter,
        "declared_lateral_margin_each_side_m": (width_clearance - diameter) / 2.0,
        "declared_height_margin_m": height_clearance - body[2],
        "static_raster_status": "candidate" if not blockers[:-1] else "blocked",
        "admission_status": "blocked",
        "blockers": blockers,
    }


def _route_bindings(
    public: Mapping[str, Any], semantics: Mapping[str, Any], region_bindings: list[dict[str, Any]],
    metadata: Mapping[str, Any], read_occupancy, read_cell,
) -> list[dict[str, Any]]:
    regions = {row["id"]: row for row in region_bindings}
    public_routes = {row["id"]: row for row in public["public_world"]["routes"]}
    ground_robots = [row for row in public["candidate_robot_pool"] if row["mobility"] == "ground"]
    vehicle_routes = []
    for route in semantics["vehicle_routes"]:
        raster_check = _continuous_raster_check(route["points"], metadata, read_cell)
        vehicle_routes.append({
            "id": route["id"],
            "point_order": "declared_travel_direction",
            "points_xy_m": deepcopy(route["points"]),
            "occupancy_vertex_check": _occupancy_summary(route["points"], read_occupancy),
            "continuous_raster_check": raster_check,
            "ground_profile_reviews": [
                _ground_profile_review(
                    robot, public_routes["city_ground_road_network"], raster_check,
                    metadata["inflation_m"],
                )
                for robot in ground_robots
            ],
        })

    bindings = []
    for route in public["public_world"]["routes"]:
        row = {
            "id": route["id"],
            "from_region_id": route["from_region_id"],
            "to_region_id": route["to_region_id"],
            "direction": "preserve_public_from_to",
            "public_route": deepcopy(route),
            "coordinate_frame": semantics["coordinate_frame"],
            "physical_route_validated": False,
        }
        if route["id"] == "city_ground_road_network":
            conflicts = sum(
                item["continuous_raster_check"]["classification_counts"]["occupied"]
                + item["continuous_raster_check"]["classification_counts"]["unknown"]
                + item["continuous_raster_check"]["classification_counts"]["outside"]
                for item in vehicle_routes
            )
            row.update({
                "status": "map_checked_with_static_conflicts" if conflicts else "map_checked",
                "evidence": "semantic_vehicle_route_point_order_and_continuous_raster_supercover",
                "geometry": {"type": "DirectedPolylineSet", "routes": deepcopy(vehicle_routes)},
                "blockers": (["some_declared_route_segments_cross_nonfree_preinflated_cells"]
                             if conflicts else []) + [
                                 "ground_profiles_exceed_existing_map_inflation",
                                 "dynamic_occupancy_and_physical_execution_not_validated",
                             ],
            })
        elif route["id"] == "city_aerial_road_network":
            row.update({
                "status": "declared_envelope_unvalidated",
                "evidence": "road_surface_horizontal_geometry_only",
                "geometry": deepcopy(regions["road_surface"]["geometry"]),
                "blockers": ["no_3d_aerial_corridor_or_runtime_admission"],
            })
        else:
            target = regions[route["to_region_id"]]
            row.update({
                "status": "declared_endpoints_unrouted",
                "evidence": "exact_public_direction_and_target_region_geometry",
                "geometry": deepcopy(target["geometry"]),
                "blockers": ["no_map_derived_ingress_polyline_or_clearance_validation"],
            })
        bindings.append(row)
    return bindings


def _robot_spawns(
    public: Mapping[str, Any], candidate: Mapping[str, Any], read_occupancy,
) -> list[dict[str, Any]]:
    reviews = candidate["navigation_handoff"]["start_reviews"]
    by_id = {row["agent_id"]: row for row in reviews}
    robots = public["candidate_robot_pool"]
    if len(by_id) != len(reviews) or set(by_id) != {row["id"] for row in robots}:
        raise ValueError("city navigation handoff must cover each public robot exactly once")
    obstacles = candidate["public_maps"]["semantic_map"]["static_obstacles"]
    result = []
    for robot in robots:
        review = by_id[robot["id"]]
        position = deepcopy(review["candidate_spawn_xyz_m"])
        occupancy = read_occupancy(position[:2])
        overlaps = [
            obstacle["id"] for obstacle in obstacles
            if all(obstacle["bounds_m"][0][axis] <= position[axis]
                   <= obstacle["bounds_m"][1][axis] for axis in range(3))
        ]
        static_clear = occupancy["classification"] == "free" and not overlaps
        result.append({
            "id": robot["id"],
            "public_robot": deepcopy(robot),
            "instance_id": f"city::{robot['id']}",
            "robot_type": robot["type"],
            "mobility": robot["mobility"],
            "position_xyz_m": position,
            "orientation_wxyz": None,
            "coordinate_frame": "city_world",
            "status": "map_checked_static_candidate" if static_clear else "static_map_rejected",
            "evidence": "author_navigation_candidate_plus_occupancy_pixel_and_public_aabb_point_check",
            "occupancy_check": occupancy,
            "static_obstacle_point_overlaps": overlaps,
            "physical_profile": deepcopy(robot["physical_profile"]),
            "physical_profile_policy": "frozen_from_public_task",
            "physical_spawn_validated": False,
            "blockers": [
                "spawn_orientation_not_authored",
                "support_contact_and_full_body_clearance_not_validated",
                "dynamic_layers_not_admitted",
            ] + (["aerial_3d_path_not_validated"] if robot["mobility"] == "aerial" else []),
        })
    if len({row["instance_id"] for row in result}) != len(result):
        raise ValueError("city runtime instance IDs must be unique")
    positions = [tuple(row["position_xyz_m"]) for row in result]
    if len(set(positions)) != len(positions):
        raise ValueError("city candidate spawns must be distinct")
    return result


def _pending_services(candidate: Mapping[str, Any]) -> list[dict[str, Any]]:
    required = candidate["private_authoring"]["required_services"]
    if {row["id"] for row in required} != _PENDING_CITY_SERVICES:
        raise ValueError("city service handoff must retain exactly the three pending requirements")
    return [
        {
            **deepcopy(row),
            "integration_contract": deepcopy(_PENDING_SERVICE_METHODS[row["id"]]),
            "activation_status": "pending_external_service_integration",
            "activated": False,
            "receipt_requirements": deepcopy(row["evidence_types"]),
            "reference_only": True,
            "request_ack_is_business_outcome": False,
            "outcome_statuses": ["pending", "timed_out", "reference_complete", "evidence_complete"],
            "trusted_readback_physically_authenticated": False,
        }
        for row in required
    ]


def build_city_deployment_overlay(
    public_task: Mapping[str, Any], *, city_package_dir: str | Path, verify_sources: bool = True,
) -> dict[str, Any]:
    """Bind a frozen city public task to static deployment candidates.

    The function is read-only and launches neither Isaac nor ROS.  Any public
    task change, stale package artifact, or stale listed local source fails.
    """
    if not isinstance(public_task, Mapping):
        raise ValueError("public_task must be a mapping")
    candidate = load_city_goal_candidate(city_package_dir, verify_sources=verify_sources)
    public = candidate["public_task"]
    if dict(public_task) != public or canonical_sha256(dict(public_task)) != canonical_sha256(public):
        raise ValueError("city public task differs from the validated preparation package")

    resolved_package_dir = str(Path(candidate["package_dir"]).resolve(strict=True))
    semantics = candidate["public_maps"]["semantic_map"]
    metadata = candidate["public_maps"]["metadata"]
    read_occupancy, read_cell = _occupancy_reader(candidate)
    regions = _region_bindings(public, semantics)
    objects = _object_bindings(public, semantics)
    routes = _route_bindings(public, semantics, regions, metadata, read_occupancy, read_cell)
    spawns = _robot_spawns(public, candidate, read_occupancy)
    static_obstacles = [
        {
            "id": row["id"], "kind": row["kind"], "bounds_m": deepcopy(row["bounds_m"]),
            "status": "map_checked", "evidence": "public_semantic_map_aabb",
            "task_object": False, "carryable": False,
            "mass_policy": "not_declared_do_not_infer",
        }
        for row in semantics["static_obstacles"]
    ]
    blockers = sorted(set(candidate["intake_review"]["blockers"]) | {
        "city_whole_business_scoring_blocked",
        "dynamic_occupancy_admission_pending",
        "physical_spawn_and_route_validation_pending",
        *(f"pending_city_service:{name}" for name in sorted(_PENDING_CITY_SERVICES)),
    })
    return {
        "schema": "eai.benchmark.city_deployment_overlay",
        "version": 1,
        "visibility": "author_only",
        "scene_key": "city",
        "task_id": public["task_id"],
        "public_task_sha256": canonical_sha256(public),
        "source_context": {
            "city_package_dir": resolved_package_dir,
            "source_verification_requested": verify_sources,
        },
        "source_verification_requested": verify_sources,
        "object_bindings": objects,
        "region_bindings": regions,
        "route_bindings": routes,
        "robot_spawns": spawns,
        "source_files": _source_files(candidate),
        "blockers": blockers,
        "static_obstacle_bindings": static_obstacles,
        "dynamic_occupancy_policy": {
            "policy": "blocked_until_current_layers_are_admitted",
            "unknown_space_policy": candidate["navigation_handoff"]["unknown_space_policy"],
            "required_layers": deepcopy(candidate["navigation_handoff"]["dynamic_layers_required"]),
            "static_map_excluded_layers": deepcopy(
                candidate["public_maps"]["metadata"]["excluded_dynamic_layers"]
            ),
        },
        "pending_service_requirements": _pending_services(candidate),
        "source_verification": {
            "requested": verify_sources,
            "local_source_status": candidate["source_binding"]["status"],
            "checked_local_file_count": candidate["source_binding"]["checked_file_count"],
            "package_artifact_integrity_validated": True,
        },
        "execution_validated": False,
        "scoreable": False,
        "scoring_status": "blocked_pending_services_and_physical_validation",
    }


__all__ = ["build_city_deployment_overlay", "load_city_deployment_public"]
