"""Author-only deployment overlays for the three indoor goal tasks.

The overlay keeps frozen public parameters separate from scene-native assets
and from candidate planning geometry.  A map-checked candidate is still not a
physical scene binding or execution certificate.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping

from EAI.scenario_spec.geometry import provider_map
from EAI.scenario_spec.oracle import InflatedGrid

from .goal_driven_contract import validate_goal_task_spec
from .goal_scene_binding import build_goal_scene_binding_inventory
from .provenance import canonical_sha256


REPO_ROOT = Path(__file__).resolve().parents[2]
_SCENARIOS = {
    "factory": REPO_ROOT / "demo/factory_benchmark/scenarios/inspection_dispatch_01.json",
    "warehouse_nvd": REPO_ROOT / "demo/warehouse_benchmark/scenarios/business_logistics_01.json",
    "modern_house_nvd": REPO_ROOT / "demo/modern_house_benchmark/scenarios/home_recovery_01.json",
}
_VERTICAL_SCENARIO = REPO_ROOT / "demo/factory_benchmark/scenarios/vertical_stair_01.json"
_PROPS = REPO_ROOT / "source/EAI_assets/EAI_assets/scenario_props.json"
_ADAPTER_SOURCE_FILES = (
    REPO_ROOT / "demo/benchmark/goal_indoor_deployment.py",
    REPO_ROOT / "demo/benchmark/goal_scene_binding.py",
    REPO_ROOT / "demo/benchmark/goal_region_geometry.py",
    REPO_ROOT / "source/EAI/EAI/scenario_spec/geometry.py",
    REPO_ROOT / "source/EAI/EAI/scenario_spec/oracle.py",
)

# Positions are author proposals derived from the named legacy target, spawn,
# zone, or a documented offset between those declarations.  They are not USD
# measurements and never establish public/legacy identity equivalence.
_REGION_PROPOSALS = {
    "factory": {
        "factory_overview": ([-7.95, -6.35, 2.5], "inspection_site_xy_with_author_altitude"),
        "assembly_cell": ([5.55, 3.4, 0.0], "assembly_site"),
        "stair_approach": ([1.5, -0.35, 0.0], "vertical_stair_go2_spawn_xy"),
        "mezzanine_workcell": ([1.5, -7.5, 1.57], "mezzanine_stair_exit"),
        "response_staging": ([-1.95, 3.4, 0.0], "dock_site_author_repurpose_proposal"),
    },
    "warehouse_nvd": {
        "rack_overview": ([-12.5, -11.75, 2.5], "rack_scan_zone_xy_with_author_altitude"),
        "rack_aisle_a": ([-10.6, -11.75, 0.0], "aisle_entry_zone"),
        "rack_aisle_b": ([-7.0, -10.05, 0.0], "priority_carton_proxy_xy"),
        "consolidation_area": ([-5.0, -11.75, 0.0], "consolidation_zone"),
        "outbound_dock": ([-5.0, -10.75, 0.0], "outbound_dock_zone"),
        "receiving_buffer": ([-4.0, -9.75, 0.0], "author_offset_from_outbound_dock_zone"),
        "warehouse_standby_a": ([-3.575, -11.75, 0.0], "carter_1_spawn_xy"),
        "warehouse_standby_b": ([-2.5, -11.75, 0.0], "author_offset_from_carter_1_spawn"),
    },
    "modern_house_nvd": {
        "room_overview": ([5.0, 13.5, 1.6], "indoor_living_rug_airspace_candidate"),
        "entry_hall": ([4.0, 3.5, 0.0], "indoor_authored_camera_entry_axis_candidate"),
        "furniture_side": ([5.0, 13.5, 0.0], "indoor_rug_between_native_armchair_and_sofa"),
        "living_area": ([4.5, 12.3, 0.0], "indoor_living_rug_south_candidate"),
        "furniture_parking": ([5.0, 15.0, 0.0], "indoor_living_rug_north_candidate"),
        "keepsake_staging": ([4.0, 13.0, 0.0], "indoor_shelf_side_staging_candidate"),
        "resident_path": ([3.5, 9.5, 0.0], "indoor_west_circulation_anchor_not_complete_path"),
        "house_standby_a": ([8.4, 6.0, 0.0], "indoor_east_bypass_south_candidate"),
        "house_standby_b": ([8.4, 10.0, 0.0], "indoor_east_bypass_north_candidate"),
    },
}

_OBJECT_PROPOSALS = {
    "factory": {
        "production_anomaly_marker": ([5.55, 3.4], "factory_goal_anomaly_marker_v2", None),
        "maintenance_service_cart": ([-1.95, 3.4], "factory_goal_service_cart_v2", None),
    },
    "warehouse_nvd": {
        "order_crate_01": ([-10.6, -11.75], "wooden_crate_a", "oversized_crate_proxy"),
        "order_carton_01": ([-7.0, -10.05], "cardbox_c", "priority_carton_proxy"),
    },
    "modern_house_nvd": {
        # Task-owned native prop instances; source artwork does not alias a
        # pre-existing decorative chair/bookstack's runtime identity.
        "living_armchair_01": ([5.0, 13.5], "mh_armchair", "armchair_proxy"),
        "resident_bookstack_01": ([4.5, 12.3], "mh_bookstack", "keepsake_box_proxy"),
    },
}

_SPAWN_PROPOSALS = {
    "factory": {
        "factory_aerial_1": ([-7.95, -5.25, 1.0], [0.0, 0.0, 0.0, 1.0], "iris_1"),
        "factory_ground_1": ([-6.95, -3.85, 0.52], [1.0, 0.0, 0.0, 0.0], None),
        "factory_ground_2": ([-1.95, -8.6, 0.2], [1.0, 0.0, 0.0, 0.0], "carter_1"),
        "factory_ground_3": ([-6.95, -2.8, 0.52], [1.0, 0.0, 0.0, 0.0], None),
    },
    "warehouse_nvd": {
        "warehouse_aerial_1": ([-13.5, -11.75, 1.0], [1.0, 0.0, 0.0, 0.0], "iris_1"),
        "warehouse_ground_1": ([-9.0, -11.75, 0.52], [1.0, 0.0, 0.0, 0.0], "m20_1"),
        "warehouse_ground_2": ([-3.575, -11.75, 0.2], [1.0, 0.0, 0.0, 0.0], "carter_1"),
        "warehouse_ground_3": ([-8.0, -11.75, 0.52], [1.0, 0.0, 0.0, 0.0], None),
    },
    "modern_house_nvd": {
        "house_aerial_1": ([4.5, 12.5, 1.6], [1.0, 0.0, 0.0, 0.0], None),
        # Live floor rays and native collision cubes agree on z=.25 m here.
        # Apply the nominal ground-relative root offsets above that floor;
        # bounded root drift alone cannot detect initial slab penetration.
        "house_ground_1": ([4.0, 3.5, 0.77], [1.0, 0.0, 0.0, 0.0], None),
        "house_ground_2": ([6.0, 4.1, 0.45], [1.0, 0.0, 0.0, 0.0], None),
        "house_ground_3": ([8.0, 3.7, 0.77], [1.0, 0.0, 0.0, 0.0], None),
    },
}

# Declared initial region per Modern House ground spawn. The overlay verifies
# each spawn footprint against that region's measured interval; a spawn outside
# the interval is a rejected overlay row, never silently re-labeled.
_HOUSE_SPAWN_REGIONS = {
    "house_ground_1": "entry_hall",
    "house_ground_2": "entry_hall",
    "house_ground_3": "entry_hall",
}


def _read_json(path: Path) -> Any:
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f"duplicate JSON key in {path}: {key}")
            value[key] = item
        return value

    return json.loads(path.read_bytes(), object_pairs_hook=unique,
                      parse_constant=lambda value: (_ for _ in ()).throw(
                          ValueError(f"non-finite JSON in {path}: {value}")))


def _sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _source_files(inventory: Mapping[str, Any]) -> dict[str, str]:
    files: dict[str, str] = {}
    for row in inventory.get("sources", ()):
        if row.get("status") != "present":
            continue
        path = Path(row["path"]).resolve()
        if not path.is_file():
            raise ValueError(f"deployment source is missing: {path}")
        digest = _sha256(path)
        if digest != row.get("sha256"):
            raise ValueError(f"deployment source changed while current binding inventory was read: {path}")
        files[str(path)] = digest
    # Keep this dependency list finite and reviewable. Do not recursively parse
    # imports: the current binding inventory plus these adapter/planner sources is the
    # rebuild boundary used by the main deployment verifier.
    for path in _ADAPTER_SOURCE_FILES:
        resolved = path.resolve()
        if not resolved.is_file():
            raise ValueError(f"deployment adapter source is missing: {resolved}")
        files[str(resolved)] = _sha256(resolved)
    if inventory["scene_key"] == "factory":
        from demo.factory_benchmark.goal_props import FACTORY_GOAL_PROPS
        sources = [REPO_ROOT / "demo/factory_benchmark/goal_props.py",
                   *(row["path"] for row in FACTORY_GOAL_PROPS.values())]
        for path in sources:
            resolved = path.resolve()
            if not resolved.is_file():
                raise ValueError(f"Factory task-owned source is missing: {resolved}")
            files[str(resolved)] = _sha256(resolved)
    return dict(sorted(files.items()))


def _confirm_source_files(files: Mapping[str, str]) -> None:
    for name, expected in files.items():
        path = Path(name)
        if not path.is_file() or _sha256(path) != expected:
            raise ValueError(f"deployment source changed during overlay construction: {path}")


def _scenario_entities(scene_key: str) -> dict[str, dict[str, Any]]:
    scenario = _read_json(_SCENARIOS[scene_key])
    return {row["id"]: row for row in scenario["composition"]["entities"]}


def _native_props() -> dict[str, dict[str, Any]]:
    return {row["key"]: row for row in _read_json(_PROPS)["props"]}


def _inventory_rows(inventory: Mapping[str, Any], kind: str) -> dict[str, dict[str, Any]]:
    return {row["public_id"]: row for row in inventory["inventory"][kind]}


def _map_check_rectangle(occupancy: Any, position: list[float], size_xy: tuple[float, float]) -> dict[str, Any]:
    admitted = occupancy.rectangle_free(position[0], position[1], size_xy[0] / 2, size_xy[1] / 2)
    return {
        "check_kind": "footprint",
        "status": "map_checked" if admitted else "blocked",
        "admitted": admitted,
        "footprint_size_m": [size_xy[0], size_xy[1]],
        "map_sha256": occupancy.sha256,
        "limits": [
            "2d_occupancy_footprint_only",
            "map_to_current_composed_usd_revision_not_established",
            "no_inter_object_or_dynamic_clearance_check",
        ],
    }


def _map_check_point(occupancy: Any, position: list[float]) -> dict[str, Any]:
    admitted = occupancy.rectangle_free(position[0], position[1], 0.0, 0.0)
    return {
        "check_kind": "point",
        "status": "map_checked" if admitted else "blocked",
        "admitted": admitted,
        "world_point_m": deepcopy(position),
        "map_sha256": occupancy.sha256,
        "limits": [
            "2d_occupancy_center_cell_only",
            "no_region_boundary_capacity_or_robot_footprint_check",
            "map_to_current_composed_usd_revision_not_established",
        ],
    }


def _region_aabb(scene_key: str, region_id: str) -> list[float]:
    center = _REGION_PROPOSALS[scene_key][region_id][0]
    return [center[0] - 0.5, center[1] - 0.5, center[0] + 0.5, center[1] + 0.5]


def _measured_region_geometry(scene_key: str, occupancy: Any,
                              region_ids: Any) -> dict[str, dict[str, Any]]:
    """Offline double binding for ground regions where the scene supports it.

    Only Modern House currently ships a derivable bench collision layer. The
    derivation is rebuilt from the provider occupancy map plus the authored
    layer on every overlay build; it is never a cached or hand-entered
    measurement.
    """
    if scene_key != "modern_house_nvd":
        return {}
    from .goal_region_geometry import derive_region_geometry, house_bench_layer_path
    from .goal_region_geometry import static_wall_cuboids
    layer = house_bench_layer_path()
    walls = static_wall_cuboids(layer)
    result: dict[str, dict[str, Any]] = {}
    for identifier in region_ids:
        position = _REGION_PROPOSALS[scene_key][identifier][0]
        if position[2] > 0.5:
            continue
        result[identifier] = derive_region_geometry(occupancy, layer, position[:2], walls=walls)
    return result


def _region_interval_bounds(scene_key: str, region_id: str,
                            measured: Mapping[str, Any] | None) -> tuple[list[float], str, str]:
    row = (measured or {}).get(region_id)
    if row is not None and row.get("status") == "measured":
        return (list(row["interval_aabb_xy_m"]),
                "occupancy_interval_plus_usd_static_wall_cuboids",
                "measured_interval")
    return (_region_aabb(scene_key, region_id),
            "author_proposal_geometry_not_physical_scene_measurement",
            "authored_center_half_meter")


def _initial_region_check(scene_key: str, physical: Mapping[str, Any],
                          position: list[float],
                          measured: Mapping[str, Any] | None = None) -> dict[str, Any]:
    region_id = physical["initial_region_id"]
    bounds, basis, source = _region_interval_bounds(scene_key, region_id, measured)
    low_x, low_y, high_x, high_y = bounds
    half_x, half_y = float(physical["size_m"][0]) / 2, float(physical["size_m"][1]) / 2
    object_bounds = [position[0] - half_x, position[1] - half_y,
                     position[0] + half_x, position[1] + half_y]
    admitted = (object_bounds[0] >= low_x and object_bounds[1] >= low_y
                and object_bounds[2] <= high_x and object_bounds[3] <= high_y)
    return {
        "check_kind": "footprint_within_region",
        "status": "candidate_checked" if admitted else "blocked",
        "admitted": admitted,
        "region_id": region_id,
        "region_candidate_aabb_xy_m": [low_x, low_y, high_x, high_y],
        "object_candidate_aabb_xy_m": object_bounds,
        "bounds_source": source,
        "basis": basis,
    }


def _object_bindings(public: Mapping[str, Any], scene_key: str, inventory: Mapping[str, Any],
                     occupancy: Any, measured: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    native_props = _native_props()
    old_entities = _scenario_entities(scene_key)
    old_rows = _inventory_rows(inventory, "objects")
    rows = []
    for item in public["public_world"]["objects"]:
        identifier = item["id"]
        physical = deepcopy(item["physical_properties"])
        xy, asset_key, old_id = _OBJECT_PROPOSALS[scene_key][identifier]
        if (public.get("task_id") == "WAREHOUSE-INBOUND-01"
                and physical["initial_region_id"] == "receiving_buffer"):
            # GOAL-01 leaves these goods on the rack. This task's own copy
            # starts inside the measured receiving buffer; the rack coordinates
            # stay put for the frozen outbound task.
            bounds, _, _ = _region_interval_bounds(scene_key, "receiving_buffer", measured)
            half_x = float(physical["size_m"][0]) / 2
            half_y = float(physical["size_m"][1]) / 2
            cx = (bounds[0] + bounds[2]) / 2
            cy = (bounds[1] + half_y if identifier == "order_crate_01"
                  else bounds[3] - half_y)
            xy = [cx, cy]
        # House's live floor is z=.25 (Stage17 native support rays/cuboids).
        # Centring a 25cm bookstack at z=.125 buries it wholly in that slab.
        # The armchair may depenetrate by itself; a thin bookstack need not.
        support_z = .25 if scene_key == "modern_house_nvd" else 0.
        position = [*xy, support_z + physical["size_m"][2] / 2]
        legacy = deepcopy(old_entities.get(old_id)) if old_id else None
        asset: dict[str, Any]
        differences = []
        if scene_key == "factory":
            from demo.factory_benchmark.goal_props import FACTORY_GOAL_PROPS
            authored = FACTORY_GOAL_PROPS[identifier]
            if (asset_key != authored["asset_key"] or physical["size_m"] != authored["size_m"]
                    or physical["mass_kg"] != authored["mass_kg"]):
                raise ValueError("Factory task-owned asset differs from frozen public parameters")
            asset = {
                "kind": "task_owned_usd_asset_with_public_parameter_overrides",
                "asset_key": asset_key,
                "usd_rel": authored["path"].relative_to(REPO_ROOT / "usd").as_posix(),
                "native_size_m": deepcopy(authored["size_m"]),
                "native_suggested_mass_kg": authored["mass_kg"],
                "geometry_scale_xyz": [1.0, 1.0, 1.0],
                "geometry_policy": "reviewed_task_owned_mesh_exact_public_size",
            }
        elif asset_key is None:
            asset = {
                "kind": "procedural_box_author_proposal",
                "asset_key": None,
                "native_size_m": None,
                "geometry_scale_xyz": [1.0, 1.0, 1.0],
                "geometry_source_status": "missing_native_asset",
            }
        else:
            native = native_props[asset_key]
            native_size = native["size_m"]
            scale = [physical["size_m"][i] / native_size[i] for i in range(3)]
            asset = {
                "kind": "native_usd_asset_with_public_parameter_overrides",
                "asset_key": asset_key,
                "usd_rel": native["usd_rel"],
                "native_size_m": deepcopy(native_size),
                "native_suggested_mass_kg": native["suggested_mass_kg"],
                "geometry_scale_xyz": scale,
                "geometry_policy": "scale_native_geometry_to_frozen_public_size",
            }
            differences.extend([
                {"field": "size_m", "public_value": deepcopy(physical["size_m"]),
                 "native_value": deepcopy(native_size)},
                {"field": "mass_kg", "public_value": physical["mass_kg"],
                 "native_value": native["suggested_mass_kg"]},
            ])
        if legacy is not None:
            differences.extend([
                {"field": "legacy_instance_id", "public_value": identifier, "legacy_value": old_id},
                {"field": "legacy_size_m", "public_value": deepcopy(physical["size_m"]),
                 "legacy_value": deepcopy(legacy["size_m"])},
                {"field": "legacy_mass_kg", "public_value": physical["mass_kg"],
                 "legacy_value": legacy["physics"]["mass_kg"]},
            ])
        map_check = _map_check_rectangle(
            occupancy, position, (float(physical["size_m"][0]), float(physical["size_m"][1])))
        region_check = _initial_region_check(scene_key, physical, position, measured)
        gaps = ["physical_spawn_and_collision_not_verified"]
        if region_check["bounds_source"] != "measured_interval":
            gaps.append("initial_region_membership_not_physically_verified")
        if asset_key is None:
            gaps.append("native_asset_geometry_missing")
        admitted = map_check["admitted"] and region_check["admitted"]
        rows.append({
            "id": identifier,
            "status": "proposed" if admitted else "blocked",
            "evidence_status": "author_proposal_map_checked_not_physical",
            "public_object": deepcopy(item),
            "deployment_directive": {
                "instance_id": f"goal_object__{identifier}",
                "position_m": position,
                "rotation_wxyz": [1.0, 0.0, 0.0, 0.0],
                "mass_kg": physical["mass_kg"],
                "size_m": deepcopy(physical["size_m"]),
                "initial_region_id": physical["initial_region_id"],
                "grasp_points": deepcopy(physical["grasp_points"]),
                "handling_modes": deepcopy(physical["handling_modes"]),
                "asset": asset,
            },
            "legacy_candidate": legacy,
            "legacy_identity_equivalent": False,
            "native_and_legacy_differences": differences,
            "inventory_leads": [candidate["candidate_id"] for candidate in old_rows[identifier]["candidates"]],
            "map_check": map_check,
            "initial_region_check": region_check,
            "physical_verified": False,
            "gaps": gaps,
        })
    return rows


def _region_bindings(public: Mapping[str, Any], scene_key: str, inventory: Mapping[str, Any],
                     occupancy: Any, measured: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    old_rows = _inventory_rows(inventory, "regions")
    result = []
    for item in public["public_world"]["regions"]:
        identifier = item["id"]
        position, basis = _REGION_PROPOSALS[scene_key][identifier]
        ground = "ground_navigation" in item["access_modes"] and position[2] <= 0.5
        check = (_map_check_point(occupancy, position) if ground else {
            "check_kind": "point",
            "status": "not_applicable",
            "admitted": False,
            "map_sha256": occupancy.sha256,
            "reason": "ground_map_cannot_admit_aerial_or_elevated_region_geometry",
        })
        status = "map_checked" if check["admitted"] else "proposed"
        geometry_row = (measured or {}).get(identifier)
        if ground and geometry_row is not None and geometry_row.get("status") == "measured":
            # The interval and its doorways are rebuilt from the provider map
            # plus the authored bench collision layer on every overlay build.
            candidate_geometry = {
                "kind": "measured_occupancy_interval_with_usd_wall_corroboration",
                "center_m": deepcopy(position),
                "aabb_xy_m": list(geometry_row["interval_aabb_xy_m"]),
                "basis": geometry_row["basis"],
                "boundary_status": geometry_row["boundary_status"],
                "measured_binding": deepcopy(geometry_row),
            }
            evidence_status = "offline_measured_interval_from_provider_map_and_authored_bench_layer"
            gaps = ["capacity_and_visibility_not_physically_verified",
                    "region_interval_is_offline_map_and_layer_derivation_not_live_measurement"]
        else:
            candidate_geometry = {
                "kind": "author_proposed_aabb",
                "center_m": deepcopy(position),
                "aabb_xy_m": _region_aabb(scene_key, identifier),
                "basis": basis,
                "boundary_status": "pending",
            }
            evidence_status = "author_candidate_from_existing_declarations"
            gaps = ["reviewed_region_boundary_missing", "capacity_and_visibility_not_physically_verified"]
        result.append({
            "id": identifier,
            "status": status,
            "evidence_status": evidence_status,
            "public_region": deepcopy(item),
            "candidate_geometry": candidate_geometry,
            "inventory_leads": [candidate["candidate_id"] for candidate in old_rows[identifier]["candidates"]],
            "map_check": check,
            "physical_verified": False,
            "gaps": gaps,
        })
    return result


def _world_path(occupancy: Any, cells: list[tuple[int, int]]) -> list[list[float]]:
    points = []
    for ix, iy in cells:
        x, y = occupancy.world((ix + 0.5) * occupancy.resolution,
                               (iy + 0.5) * occupancy.resolution)
        points.append([round(x, 4), round(y, 4), 0.0])
    # Retain endpoints and direction changes; every segment follows the A* cell path.
    if len(points) <= 2:
        return points
    kept = [points[0]]
    previous = None
    for index in range(1, len(cells)):
        direction = (cells[index][0] - cells[index - 1][0],
                     cells[index][1] - cells[index - 1][1])
        if previous is not None and direction != previous:
            kept.append(points[index - 1])
        previous = direction
    kept.append(points[-1])
    return kept


def _route_bindings(public: Mapping[str, Any], scene_key: str, occupancy: Any) -> list[dict[str, Any]]:
    centers = {key: value[0] for key, value in _REGION_PROPOSALS[scene_key].items()}
    result = []
    for item in public["public_world"]["routes"]:
        start = centers[item["from_region_id"]]
        goal = centers[item["to_region_id"]]
        row = {
            "id": item["id"],
            "public_route": deepcopy(item),
            "candidate_endpoints_m": [deepcopy(start), deepcopy(goal)],
            "physical_verified": False,
        }
        if item["access_modes"] != ["ground_navigation"] or start[2] > 0.5 or goal[2] > 0.5:
            row.update({
                "status": "blocked",
                "evidence_status": "missing_3d_or_vertical_route_evidence",
                "planning_candidate": None,
                "map_check": {"check_kind": "segment", "status": "blocked", "admitted": False,
                              "reason": "2d_ground_map_cannot_admit_this_route"},
                "gaps": ["continuous_3d_or_vertical_path_missing", "clearance_not_verified"],
            })
            result.append(row)
            continue
        radius = float(item["clearance_m"][0]) / 2
        grid = InflatedGrid(occupancy, radius)
        cells = grid.path(start[:2], goal[:2])
        if cells is None:
            start_passable = bool(grid.passable(*grid.cell(*start[:2])))
            goal_passable = bool(grid.passable(*grid.cell(*goal[:2])))
            row.update({
                "status": "blocked",
                "evidence_status": "occupancy_route_rejected",
                "planning_candidate": None,
                "map_check": {"check_kind": "segment", "status": "blocked", "admitted": False,
                              "inflation_radius_m": radius, "map_sha256": occupancy.sha256,
                              "reason": "no_path_at_declared_half_width_inflation",
                              "start_cell_passable": start_passable,
                              "goal_cell_passable": goal_passable,
                              "failure_class": (
                                  "endpoint_blocked" if not (start_passable and goal_passable)
                                  else "disconnected_passable_endpoints")},
                "gaps": ["candidate_route_not_map_admitted", "physical_clearance_not_verified"],
            })
        else:
            polyline = _world_path(occupancy, cells)
            length = sum(math.dist(a[:2], b[:2]) for a, b in zip(polyline, polyline[1:]))
            row.update({
                "status": "map_checked",
                "evidence_status": "candidate_2d_occupancy_path",
                "planning_candidate": {
                    "polyline_m": polyline,
                    "planned_length_m": round(length, 4),
                    "planning_radius_m": radius,
                    "basis": "deterministic_inflated_grid_astar",
                },
                "map_check": {"check_kind": "segment", "status": "map_checked", "admitted": True,
                              "inflation_radius_m": radius, "map_sha256": occupancy.sha256,
                              "limits": ["2d_static_occupancy_only",
                                         "public_declared_length_is_not_replaced_by_planned_length",
                                         "map_to_composed_usd_revision_not_established"]},
                "gaps": ["loaded_envelope_and_dynamic_clearance_not_physically_verified"],
            })
        result.append(row)
    return result


def _robot_spawns(public: Mapping[str, Any], scene_key: str, occupancy: Any,
                  measured: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    published = dict(public.get("public_world", {}).get("robot_spawn_positions") or {})
    rows = []
    for item in public["candidate_robot_pool"]:
        proposal = _SPAWN_PROPOSALS[scene_key].get(item["id"])
        if proposal is not None:
            position, rotation, old_id = proposal
        else:
            # Opt-in pools publish frozen anchors. Legacy ids stay on the
            # proposal table so the default overlay does not move.
            raw = published.get(item["id"])
            if not isinstance(raw, (list, tuple)) or len(raw) != 3:
                raise KeyError(item["id"])
            position = [float(raw[0]), float(raw[1]), float(raw[2])]
            rotation = [1.0, 0.0, 0.0, 0.0]
            old_id = None
        profile = item["physical_profile"]
        if item["mobility"] == "ground":
            check = _map_check_rectangle(
                occupancy, position, (float(profile["body_size_m"][0]),
                                      float(profile["body_size_m"][1])))
            status = "map_checked" if check["admitted"] else "blocked"
        else:
            check = {"check_kind": "footprint", "status": "not_applicable", "admitted": False,
                     "map_sha256": occupancy.sha256,
                     "reason": "ground_map_cannot_admit_aerial_spawn_volume"}
            status = "proposed"
        row = {
            "id": item["id"],
            "status": status,
            "evidence_status": "distinct_author_instance_proposal",
            "public_robot": deepcopy(item),
            "deployment_directive": {
                "instance_id": f"goal_robot__{item['id']}",
                "type": item["type"],
                "position_m": deepcopy(position),
                "rotation_wxyz": deepcopy(rotation),
                "required_configuration_id": profile["configuration_id"],
                "required_interfaces": deepcopy(item["interfaces"]),
            },
            "legacy_candidate_id": old_id,
            "legacy_identity_equivalent": False,
            "map_check": check,
            "physical_verified": False,
            "gaps": ["controller_sensor_tool_assembly_not_physically_verified",
                     "body_envelope_and_payload_configuration_not_measured"],
        }
        region_id = _HOUSE_SPAWN_REGIONS.get(item["id"]) if scene_key == "modern_house_nvd" else None
        if item["mobility"] == "ground" and region_id is not None:
            bounds, basis, source = _region_interval_bounds(scene_key, region_id, measured)
            half_x, half_y = float(profile["body_size_m"][0]) / 2, float(profile["body_size_m"][1]) / 2
            spawn_bounds = [position[0] - half_x, position[1] - half_y,
                            position[0] + half_x, position[1] + half_y]
            admitted = (source == "measured_interval"
                        and spawn_bounds[0] >= bounds[0] and spawn_bounds[1] >= bounds[1]
                        and spawn_bounds[2] <= bounds[2] and spawn_bounds[3] <= bounds[3])
            row["initial_spawn_region_check"] = {
                "check_kind": "footprint_within_region",
                "status": "candidate_checked" if admitted else "blocked",
                "admitted": admitted,
                "region_id": region_id,
                "region_candidate_aabb_xy_m": bounds,
                "spawn_candidate_aabb_xy_m": spawn_bounds,
                "bounds_source": source,
                "basis": basis,
            }
            if not admitted:
                row["status"] = "blocked"
                row["gaps"].append("initial_spawn_region_membership_rejected")
        rows.append(row)
    return rows


def _vertical_links(public: Mapping[str, Any], inventory: Mapping[str, Any]) -> list[dict[str, Any]]:
    old_rows = _inventory_rows(inventory, "vertical_links")
    return [{
        "id": item["id"],
        "status": "open_pending_physical_binding",
        "public_open": True,
        "public_vertical_link": deepcopy(item),
        "inventory_leads": [candidate["candidate_id"] for candidate in old_rows[item["id"]]["candidates"]],
        "physical_binding_status": "pending",
        "physical_verified": False,
        "gaps": ["measured_stair_support_width_tread_and_connectivity_missing",
                 "robot_specific_stair_execution_missing"],
    } for item in public["public_world"]["vertical_links"]]


def build_indoor_deployment_overlay(
        public_task: Mapping[str, Any], *, verify_sources: bool = True,
        allow_extended_roster: bool = False) -> dict[str, Any]:
    """Build a source-bound author overlay without starting Isaac or ROS."""
    if not isinstance(public_task, Mapping):
        raise ValueError("public_task must be a mapping")
    if type(verify_sources) is not bool or type(allow_extended_roster) is not bool:
        raise ValueError("verify_sources and allow_extended_roster must be boolean")
    public = deepcopy(dict(public_task))
    validate_goal_task_spec(public)
    scene_key = public["mission"]["scene_key"]
    if scene_key not in _SCENARIOS:
        raise ValueError(f"unsupported indoor deployment scene: {scene_key}")
    public_hash = canonical_sha256(public)
    inventory = build_goal_scene_binding_inventory(
        scene_key, verify_sources=verify_sources)
    if inventory.get("scene_key") != scene_key:
        raise ValueError("current scene-binding inventory names a different scene")
    if inventory.get("public_task_sha256") != public_hash and not allow_extended_roster:
        raise ValueError("public task does not match the current frozen public task")
    sources = _source_files(inventory)
    occupancy = provider_map(scene_key=scene_key)
    measured = _measured_region_geometry(
        scene_key, occupancy,
        [item["id"] for item in public["public_world"]["regions"]
         if "ground_navigation" in item["access_modes"]])
    objects = _object_bindings(public, scene_key, inventory, occupancy, measured)
    regions = _region_bindings(public, scene_key, inventory, occupancy, measured)
    routes = _route_bindings(public, scene_key, occupancy)
    spawns = _robot_spawns(public, scene_key, occupancy, measured)
    vertical = _vertical_links(public, inventory)
    blockers = [
        "physical_scene_binding_and_execution_not_verified",
        "map_to_composed_usd_revision_alignment_not_verified",
        "reliable_skill_backend_conformance_owned_by_integration_layer",
    ]
    blockers.extend(f"route:{row['id']}:{row['map_check']['reason']}"
                    for row in routes if row["status"] == "blocked")
    blockers.extend(f"spawn:{row['id']}:initial_region_membership_rejected"
                    for row in spawns if row.get("initial_spawn_region_check", {}).get("admitted") is False)
    blockers.extend(f"vertical_link:{row['id']}:physical_binding_pending" for row in vertical)
    pending_actions = [
        {"id": "apply_and_read_back_object_robot_configuration", "status": "pending",
         "required_evidence": "distinct runtime entities with frozen parameter readback"},
        {"id": "align_occupancy_map_to_composed_scene_revision", "status": "pending",
         "required_evidence": "content-addressed composed USD and occupancy alignment"},
        {"id": "validate_aerial_and_vertical_route_geometry", "status": "pending",
         "required_evidence": "continuous 3d clearance and robot-specific stair execution"},
        {"id": "connect_reliable_skill_backend_to_physical_adapters", "status": "pending",
         "required_evidence": "physical effects and business receipt provenance"},
    ]
    result = {
        "schema": "eai.benchmark.indoor_deployment_overlay",
        "version": 1,
        "visibility": "author_only",
        "scene_key": scene_key,
        "public_task_sha256": public_hash,
        "source_verification_requested": verify_sources,
        **({"allow_extended_roster": True} if allow_extended_roster else {}),
        "object_bindings": objects,
        "region_bindings": regions,
        "route_bindings": routes,
        "robot_spawns": spawns,
        "vertical_link_bindings": vertical,
        "source_files": sources,
        "blockers": blockers,
        "pending_actions": pending_actions,
        "physical_verified": False,
        "scoreable": False,
    }
    _confirm_source_files(sources)
    return result


__all__ = ["build_indoor_deployment_overlay"]
