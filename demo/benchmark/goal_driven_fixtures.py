"""Frozen experimental parameters for reliable-skill task authoring.

These are benchmark definitions, not measured USD geometry or manufacturer
ratings. ``configuration_id`` identifies the experimental body/tool assembly;
in particular Carter's support connector is not an articulated grasping arm.
Scene binding and backend conformance remain separate release gates.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


SKILL_DURATIONS_S = {
    "ground_navigation": 8.0,
    "aerial_navigation": 8.0,
    "stair_traversal": 12.0,
    "observe": 4.0,
    "pick": 4.0,
    "place": 8.0,
    "handoff": 4.0,
    "cooperative_carry": 16.0,
    "relay": 1.0,
    "guide_people": 10.0,
    "control_following_traffic": 6.0,
    "report_incident": 2.0,
    "synchronize": 2.0,
}


def execution_contract() -> dict[str, Any]:
    return {
        "schema": "eai.benchmark.reliable_skills",
        "version": 1,
        "parameter_basis": "benchmark_defined",
        "scene_binding_status": "pending",
        "guarantee": "conditional_deterministic",
        "collision_policy": "hold_and_report",
        "observation_policy": "exact_within_declared_visibility",
        "clearance_margin_m": 0.05,
        "skill_durations_s": dict(SKILL_DURATIONS_S),
    }


_ROBOT_PROFILES = {
    "iris": {
        "configuration_id": "benchmark_iris_camera_v1",
        "body_size_m": [0.6, 0.6, 0.3],
        "effective_payload_kg": 0.0,
        "reach_m": 0.0,
        "grasp_interfaces": [],
        "max_step_height_m": 0.0,
    },
    "m20": {
        "configuration_id": "benchmark_m20_fixed_grasp_and_support_v1",
        "body_size_m": [0.9, 0.55, 0.85],
        "effective_payload_kg": 8.0,
        "reach_m": 0.8,
        "grasp_interfaces": ["fixed_gripper_v1", "support_socket_v1"],
        "max_step_height_m": 0.22,
    },
    "carter": {
        "configuration_id": "benchmark_carter_support_connector_no_arm_v1",
        "body_size_m": [0.8, 0.65, 1.2],
        "effective_payload_kg": 10.0,
        "reach_m": 0.6,
        "grasp_interfaces": ["support_socket_v1"],
        "max_step_height_m": 0.03,
    },
}


def robot_physical_profile(robot_type: str) -> dict[str, Any]:
    return deepcopy(_ROBOT_PROFILES[robot_type])


def _point(identifier: str, position: list[float], interface: str) -> dict[str, Any]:
    return {"id": identifier, "position_m": position, "interface": interface}


def _mode(identifier: str, fractions: list[tuple[str, float]], envelope: list[float]) -> dict[str, Any]:
    return {"id": identifier,
            "slots": [{"point_id": point, "load_fraction": fraction} for point, fraction in fractions],
            "envelope_m": envelope}


_OBJECT_PROPERTIES = {
    "production_anomaly_marker": {
        "mass_kg": 0.1, "size_m": [0.15, 0.05, 0.15],
        "initial_region_id": "assembly_cell", "grasp_points": [], "handling_modes": [],
    },
    "maintenance_service_cart": {
        "mass_kg": 18.0, "size_m": [0.8, 0.5, 0.8],
        "initial_region_id": "response_staging", "grasp_points": [], "handling_modes": [],
    },
    "order_crate_01": {
        "mass_kg": 12.0, "size_m": [0.9, 0.65, 0.6],
        "initial_region_id": "rack_aisle_a",
        "grasp_points": [
            _point("center_grip", [0.0, 0.0, 0.3], "fixed_gripper_v1"),
            _point("support_left", [0.0, -0.3, 0.25], "support_socket_v1"),
            _point("support_right", [0.0, 0.3, 0.25], "support_socket_v1"),
        ],
        "handling_modes": [
            _mode("single_grip", [("center_grip", 1.0)], [1.2, 0.8, 1.2]),
            _mode("paired_support", [("support_left", 0.5), ("support_right", 0.5)], [1.3, 1.65, 1.2]),
        ],
    },
    "order_carton_01": {
        "mass_kg": 3.0, "size_m": [0.4, 0.3, 0.3],
        "initial_region_id": "rack_aisle_b",
        "grasp_points": [_point("center_grip", [0.0, 0.0, 0.15], "fixed_gripper_v1")],
        "handling_modes": [_mode("single_grip", [("center_grip", 1.0)], [1.1, 0.65, 1.2])],
    },
    "living_armchair_01": {
        "mass_kg": 14.0, "size_m": [0.9, 0.85, 1.0],
        "initial_region_id": "furniture_side",
        "grasp_points": [
            _point("center_grip", [0.0, 0.0, 0.5], "fixed_gripper_v1"),
            _point("support_left", [0.0, -0.4, 0.25], "support_socket_v1"),
            _point("support_right", [0.0, 0.4, 0.25], "support_socket_v1"),
        ],
        "handling_modes": [
            _mode("single_grip", [("center_grip", 1.0)], [1.3, 1.0, 1.3]),
            _mode("paired_support", [("support_left", 0.5), ("support_right", 0.5)], [1.4, 1.9, 1.3]),
        ],
    },
    "resident_bookstack_01": {
        "mass_kg": 2.0, "size_m": [0.3, 0.25, 0.25],
        "initial_region_id": "living_area",
        "grasp_points": [_point("center_grip", [0.0, 0.0, 0.125], "fixed_gripper_v1")],
        "handling_modes": [_mode("single_grip", [("center_grip", 1.0)], [1.05, 0.65, 1.1])],
    },
}


def object_physical_properties(object_id: str) -> dict[str, Any]:
    return deepcopy(_OBJECT_PROPERTIES[object_id])


def _route(identifier: str, source: str, target: str, length: float, width: float,
           *, mode: str = "ground_navigation", height: float = 2.2,
           step: float = 0.0, resource: str | None = None) -> dict[str, Any]:
    return {
        "id": identifier, "from_region_id": source, "to_region_id": target,
        "access_modes": [mode], "length_m": length, "clearance_m": [width, height],
        "max_step_height_m": step, "resource_id": resource or identifier, "capacity": 1,
    }


_ROUTES = {
    "factory": [
        _route("staging_to_lower", "response_staging", "assembly_cell", 6.0, 1.4, resource="lower_service_lane"),
        _route("lower_to_staging", "assembly_cell", "response_staging", 6.0, 1.4, resource="lower_service_lane"),
        _route("lower_to_stair", "assembly_cell", "stair_approach", 4.0, 0.95, resource="stair_approach_lane"),
        _route("stair_to_lower", "stair_approach", "assembly_cell", 4.0, 0.95, resource="stair_approach_lane"),
        _route("staging_to_stair", "response_staging", "stair_approach", 5.0, 1.0, resource="maintenance_corridor"),
        _route("stair_to_staging", "stair_approach", "response_staging", 5.0, 1.0, resource="maintenance_corridor"),
        _route("main_stair_up", "stair_approach", "mezzanine_workcell", 5.0, 0.85,
               mode="stair_traversal", step=0.18, resource="main_stair"),
        _route("main_stair_down", "mezzanine_workcell", "stair_approach", 5.0, 0.85,
               mode="stair_traversal", step=0.18, resource="main_stair"),
    ],
    "warehouse_nvd": [
        _route("aisle_to_overview_air", "rack_aisle_a", "rack_overview", 4.0, 1.0,
               mode="aerial_navigation", height=1.0),
        _route("consolidation_to_rack_a", "consolidation_area", "rack_aisle_a", 7.0, 1.85, resource="crate_aisle"),
        _route("rack_a_to_consolidation", "rack_aisle_a", "consolidation_area", 7.0, 1.85, resource="crate_aisle"),
        _route("rack_a_shortcut", "rack_aisle_a", "consolidation_area", 3.0, 0.95),
        _route("consolidation_to_rack_b", "consolidation_area", "rack_aisle_b", 5.0, 0.95, resource="carton_aisle"),
        _route("rack_b_to_consolidation", "rack_aisle_b", "consolidation_area", 5.0, 0.95, resource="carton_aisle"),
        _route("consolidation_to_dock", "consolidation_area", "outbound_dock", 8.0, 1.85, resource="dock_lane"),
        _route("dock_to_consolidation", "outbound_dock", "consolidation_area", 8.0, 1.85, resource="dock_lane"),
        _route("dock_to_buffer", "outbound_dock", "receiving_buffer", 3.0, 1.85, resource="receiving_lane"),
        _route("dock_to_standby_a", "outbound_dock", "warehouse_standby_a", 3.0, 0.95),
        _route("dock_to_standby_b", "outbound_dock", "warehouse_standby_b", 3.0, 0.95),
        _route("overview_to_dock_air", "rack_overview", "outbound_dock", 8.0, 1.0,
               mode="aerial_navigation", height=1.0),
    ],
    "modern_house_nvd": [
        _route("entry_to_chair", "entry_hall", "furniture_side", 4.0, 1.0),
        _route("chair_to_parking_wide", "furniture_side", "furniture_parking", 6.0, 2.1,
               resource="furniture_transport_lane"),
        _route("chair_to_parking_shortcut", "furniture_side", "furniture_parking", 3.0, 1.0),
        _route("parking_to_living", "furniture_parking", "living_area", 3.0, 0.95, resource="living_access"),
        _route("living_to_parking", "living_area", "furniture_parking", 3.0, 0.95, resource="living_access"),
        _route("entry_to_living", "entry_hall", "living_area", 5.0, 1.0),
        _route("living_to_staging", "living_area", "keepsake_staging", 3.0, 0.85, resource="book_access"),
        _route("staging_to_living", "keepsake_staging", "living_area", 3.0, 0.85, resource="book_access"),
        _route("staging_to_chair", "keepsake_staging", "furniture_side", 5.0, 0.95),
        _route("staging_to_standby_a", "keepsake_staging", "house_standby_a", 4.0, 0.9),
        _route("parking_to_standby_a", "furniture_parking", "house_standby_a", 4.0, 0.9),
        _route("parking_to_standby_b", "furniture_parking", "house_standby_b", 4.0, 0.9),
        _route("resident_entry_segment", "entry_hall", "resident_path", 2.0, 1.0),
        _route("resident_living_segment", "resident_path", "living_area", 3.0, 1.0),
    ],
    "city": [
        _route("city_air_staging_to_overview", "response_staging", "city_incident_overview", 6.0, 1.0,
               mode="aerial_navigation", height=1.0),
        _route("city_road_to_incident", "road_surface", "incident_vicinity", 6.0, 1.2,
               resource="incident_access_lane"),
        _route("city_incident_to_road", "incident_vicinity", "road_surface", 6.0, 1.2,
               resource="incident_access_lane"),
        _route("city_road_to_control", "road_surface", "traffic_control_point", 5.0, 1.2,
               resource="traffic_control_lane"),
        _route("city_control_to_road", "traffic_control_point", "road_surface", 5.0, 1.2,
               resource="traffic_control_lane"),
        _route("city_incident_to_control", "incident_vicinity", "traffic_control_point", 4.0, 1.2),
        _route("city_control_to_incident", "traffic_control_point", "incident_vicinity", 4.0, 1.2),
    ],
}


def scene_routes(scene_key: str) -> list[dict[str, Any]]:
    return deepcopy(_ROUTES[scene_key])


def region_resource_capacity(region_id: str) -> int:
    """Capacity counts concurrent region operations, as in the constraint model."""
    if region_id in {
        "rack_aisle_a", "consolidation_area", "outbound_dock", "receiving_buffer",
        "furniture_side", "furniture_parking", "response_staging", "assembly_cell",
    }:
        return 2
    return 1


def quantitative_authoring_boundary(public_task: dict[str, Any]) -> dict[str, Any]:
    """Parameters an authoring LLM may use, never quietly retune to fit a plan."""
    return {
        "parameter_basis": "benchmark_defined",
        "scene_binding_status": "pending",
        "policy": "Frozen per scene instance; changing a value requires a new instance and quantitative review.",
        "execution_contract": deepcopy(public_task["execution_contract"]),
        "budgets": deepcopy(public_task["budgets"]),
        "business_constraints": list(public_task["business_constraints"]),
        "public_scene_notes": public_task["public_world"]["public_notes"],
        "robot_profiles": {
            row["id"]: deepcopy(row["physical_profile"])
            for row in public_task["candidate_robot_pool"]
        },
        "object_properties": {
            row["id"]: deepcopy(row["physical_properties"])
            for row in public_task["public_world"]["objects"]
        },
        "routes": deepcopy(public_task["public_world"]["routes"]),
        "regions": deepcopy(public_task["public_world"]["regions"]),
        "vertical_links": deepcopy(public_task["public_world"]["vertical_links"]),
    }
