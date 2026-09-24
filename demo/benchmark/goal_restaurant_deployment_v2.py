"""Explicit Goal v3 deployment contract for the Restaurant full8 roster.

This pure builder derives a new public task and a companion metric-geometry
contract from maintained repository authorities.  It does not read a native
session, participant content, witness plan, or runtime observation.
"""

from __future__ import annotations

from copy import deepcopy
import math
from typing import Any, Mapping

from .goal_deployment_assessment import enable_deployment_assessment
from .goal_driven_contract import (
    build_goal_participant_api,
    validate_goal_participant_api,
    validate_goal_task_spec,
)
from .goal_driven_tasks import build_goal_task_package
from .provenance import canonical_sha256
from demo.restaurant_benchmark import config, navigation_probe, route_admission
from EAI_assets.restaurant_benchmark_products import (
    EXPECTED_BUNDLE_MANIFEST_SHA256,
    EXPECTED_METADATA_SHA256,
    EXPECTED_PRODUCT_SHA256,
)


DEPLOYMENT_PROFILE = "metric_regions_v2"
DEPLOYMENT_SCHEMA = "eai.restaurant.goal_deployment"
DEPLOYMENT_VERSION = 2
ORIGINAL_TASK_ID = "RESTAURANT-GOAL-01"
TASK_ID = "RESTAURANT-GOAL-NATIVE-02"
ORIGINAL_PUBLIC_TASK_SHA256 = (
    "e34fe98668acece2234fda06140f867840f58c272e11216430151ce86eac9084"
)
GROUND_SPAWN_RADIUS_M = 0.50
AERIAL_SPAWN_RADIUS_M = route_admission.AERIAL_FINAL_TOLERANCE_M
SERVICE_REGION_RADIUS_M = navigation_probe.FINAL_TOLERANCE_M
AERIAL_GOAL_Z_M = 3.0
CLOSED_VALIDATION_FLAGS = {
    "execution_validated": False,
    "business_outcomes_validated": False,
    "feasibility_oracle_verified": False,
    "release_ready": False,
    "scoreable": False,
}


def _baseline_public_task() -> dict[str, Any]:
    public = build_goal_task_package("restaurant")["public_task"]
    if (public.get("task_id") != ORIGINAL_TASK_ID
            or canonical_sha256(public) != ORIGINAL_PUBLIC_TASK_SHA256):
        raise RuntimeError("frozen Restaurant public task identity drifted")
    return public


def _spawn_regions(public: Mapping[str, Any]) -> dict[str, str]:
    public_ids = {row["id"] for row in public["candidate_robot_pool"]}
    bindings = {
        f"restaurant_{native_id}": f"restaurant_spawn_{native_id}"
        for native_id in config.BENCHMARK_ROBOT_SPAWN_XYZ_M
    }
    if set(bindings) != public_ids:
        raise RuntimeError("Restaurant full8 roster drifted from maintained spawn anchors")
    return bindings


def _spawn_region_rows(public: Mapping[str, Any]) -> list[dict[str, Any]]:
    robots = {row["id"]: row for row in public["candidate_robot_pool"]}
    rows = []
    for public_id, region_id in _spawn_regions(public).items():
        mobility = robots[public_id]["mobility"]
        if mobility == "ground":
            mode, geometry = "ground_navigation", "XY disk radius 0.50 m"
        elif mobility == "aerial":
            mode, geometry = "aerial_navigation", "3D ball radius 0.30 m"
        else:
            raise RuntimeError(f"unsupported Restaurant mobility: {mobility}")
        rows.append({
            "id": region_id,
            "kind": "deployment_spawn_region",
            "public_description": (
                f"公开部署初始位置语义区（{geometry}，精确几何见 {DEPLOYMENT_PROFILE} "
                f"companion）；名称不为 {public_id} 保留，任何具备 {mode} 的兼容成员均可访问。"
            ),
            "access_modes": [mode],
            "resource_capacity": 8,
        })
    return rows


def _region_bindings(public: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    rows = {row["id"]: row for row in public["public_world"]["regions"]}
    bindings: dict[str, dict[str, Any]] = {}
    staging = list(navigation_probe.ROUTES["A-E"]["start_xy_m"])
    kitchen = list(route_admission.KITCHEN_SERVICE_XY_M)
    dining = list(route_admission.DINING_SERVICE_XY_M)
    overview = list(route_admission.OVERVIEW_ANCHOR_XYZ_M)
    maintained = {
        "restaurant_staging": {
            "geometry_status": "defined", "center_xy_m": staging,
            "aerial_goal_z_m": AERIAL_GOAL_Z_M,
            "tolerance_m": SERVICE_REGION_RADIUS_M,
            "access_modes": ["ground_navigation", "aerial_navigation"],
        },
        "kitchen_service": {
            "geometry_status": "defined", "center_xy_m": kitchen,
            "tolerance_m": SERVICE_REGION_RADIUS_M,
            "access_modes": ["ground_navigation"],
        },
        "dining_service": {
            "geometry_status": "defined", "center_xy_m": dining,
            "tolerance_m": SERVICE_REGION_RADIUS_M,
            "access_modes": ["ground_navigation"],
        },
        "restaurant_overview": {
            "geometry_status": "defined", "center_xyz_m": overview,
            "tolerance_m": AERIAL_SPAWN_RADIUS_M,
            "access_modes": ["aerial_navigation"],
        },
        "restaurant_standby_a": {
            "geometry_status": "unavailable", "access_modes": ["ground_navigation"],
        },
        "restaurant_standby_b": {
            "geometry_status": "unavailable", "access_modes": ["ground_navigation"],
        },
    }
    for public_id, region_id in _spawn_regions(public).items():
        native_id = public_id.removeprefix("restaurant_")
        xyz = list(config.BENCHMARK_ROBOT_SPAWN_XYZ_M[native_id])
        mobility = next(row["mobility"] for row in public["candidate_robot_pool"]
                        if row["id"] == public_id)
        maintained[region_id] = (
            {"geometry_status": "defined", "center_xy_m": xyz[:2],
             "tolerance_m": GROUND_SPAWN_RADIUS_M,
             "access_modes": ["ground_navigation"]}
            if mobility == "ground"
            else {"geometry_status": "defined", "center_xyz_m": xyz,
                  "tolerance_m": AERIAL_SPAWN_RADIUS_M,
                  "access_modes": ["aerial_navigation"]}
        )
    if set(maintained) != set(rows):
        raise RuntimeError("Restaurant companion geometry does not cover public regions")
    for region_id, binding in maintained.items():
        if binding["access_modes"] != rows[region_id]["access_modes"]:
            raise RuntimeError(f"Restaurant region access drifted for {region_id}")
        bindings[region_id] = deepcopy(binding)
    return bindings


def _route(identifier: str, source: str, target: str, *, mode: str,
           length_m: float, resource_id: str) -> dict[str, Any]:
    aerial = mode == "aerial_navigation"
    return {
        "id": identifier,
        "from_region_id": source,
        "to_region_id": target,
        "access_modes": [mode],
        "length_m": float(length_m),
        "clearance_m": [1.0, 1.0] if aerial else [1.2, 2.2],
        "max_step_height_m": 0.0,
        "resource_id": resource_id,
        "capacity": 1,
    }


def _center_distance(source: Mapping[str, Any], target: Mapping[str, Any]) -> tuple[float, str]:
    source_point = source.get("center_xyz_m", source.get("center_xy_m"))
    target_point = target.get("center_xyz_m", target.get("center_xy_m"))
    if source_point is None or target_point is None:
        raise RuntimeError("cannot author a route without both endpoint centers")
    dimensions = min(len(source_point), len(target_point))
    distance = math.dist(source_point[:dimensions], target_point[:dimensions])
    if distance == 0.0:
        return 1e-9, "co_located_semantic_transition_schema_positive_epsilon"
    return distance, "euclidean_lower_bound_geometry"


def _deployment_routes(public: Mapping[str, Any]) -> tuple[list[dict[str, Any]], set[str]]:
    bindings = _region_bindings(public)
    routes = deepcopy(public["public_world"]["routes"])
    generated_ids: set[str] = set()
    robot_by_id = {row["id"]: row for row in public["candidate_robot_pool"]}
    for public_id, spawn_region in _spawn_regions(public).items():
        mobility = robot_by_id[public_id]["mobility"]
        mode = "ground_navigation" if mobility == "ground" else "aerial_navigation"
        targets = (["restaurant_staging", "kitchen_service", "dining_service"]
                   if mobility == "ground"
                   else ["restaurant_staging", "restaurant_overview"])
        spawn_short = spawn_region.removeprefix("restaurant_")
        for target in targets:
            target_short = target.removeprefix("restaurant_")
            distance, _semantics = _center_distance(
                bindings[spawn_region], bindings[target])
            resource = f"restaurant_deployment_link_{spawn_short}_{target_short}"
            outward_id = f"restaurant_{spawn_short}_to_{target_short}"
            return_id = f"restaurant_{target_short}_to_{spawn_short}"
            routes.append(_route(outward_id, spawn_region, target, mode=mode,
                                 length_m=distance, resource_id=resource))
            routes.append(_route(return_id, target, spawn_region, mode=mode,
                                 length_m=distance, resource_id=resource))
            generated_ids.update((outward_id, return_id))
    route_ids = [row["id"] for row in routes]
    if len(route_ids) != len(set(route_ids)):
        raise RuntimeError("Restaurant deployment route ids are not unique")
    return routes, generated_ids


def _derive_public_task() -> dict[str, Any]:
    original = _baseline_public_task()
    task = deepcopy(original)
    task["task_id"] = TASK_ID
    world = deepcopy(task["public_world"])
    world["regions"] = [*deepcopy(world["regions"]), *_spawn_region_rows(task)]
    task["public_world"] = world
    routes, _generated = _deployment_routes(task)
    task["public_world"]["routes"] = routes
    task["public_world"]["public_notes"] = (
        f"本派生任务使用显式 {DEPLOYMENT_PROFILE} companion。八个候选各有独立出生语义区；"
        "出生区名称不构成机器人保留权，兼容成员可访问。restaurant_staging 是 Carter A-E 锚点"
        "定义的唯一共享 XY 区域，不等同于八个出生区。初始位置只在原生 root 满足指定出生区几何时"
        "确定。新增路线表示参与者可提出的合法拓扑；长度为作者几何名义值，通行宽度、清障与原生可达性"
        "未经验证，平台规划或执行可以失败。厨房和用餐观察仅由地面支撑查询实现；空中 overview 与本地"
        "surface probe 只返回原始诊断，不证明相机可见性、业务完成、稳定性或 release readiness。"
    )
    original_evidence_status = next(
        index for index, text in enumerate(task["business_constraints"])
        if text.startswith("部署配置 bound_native_v2"))
    task["business_constraints"][original_evidence_status] = (
        f"部署配置 {config.BENCHMARK_DEPLOYMENT_PROFILE} 的全八台 runtime 绑定、post-warmup "
        "root 状态和当前原生 surface-query 接口已经接通；这些是组件与接口测量，不是相机输出。"
        "完整公开参数域、导航执行、业务闭环、feasibility oracle、稳定性和 release/score 门仍未验证，"
        "任何缺失的路线或查询支持均作为平台缺口处理。"
    )
    task = enable_deployment_assessment(task)
    validate_goal_task_spec(task)
    for field in ("required_outcomes", "candidate_robot_pool", "budgets",
                  "execution_contract", "experiment_factor"):
        if task[field] != original[field]:
            raise RuntimeError(f"Restaurant deployment changed frozen {field}")
    changed_constraints = [
        index for index, (before, after) in enumerate(zip(
            original["business_constraints"], task["business_constraints"]))
        if before != after
    ]
    if (len(task["business_constraints"]) != len(original["business_constraints"])
            or changed_constraints != [original_evidence_status]):
        raise RuntimeError("Restaurant deployment changed a business rule")
    return task


def _observation_support() -> dict[str, Any]:
    return {
        "service_ground_readiness": {
            "region_ids": ["kitchen_service", "dining_service"],
            "implementation": "native_ground_surface_probe",
            "interface_implemented": True,
            "profile_native_success_certified": False,
            "business_outcome_validated": False,
        },
        "aerial_overview": {
            "region_ids": ["restaurant_overview"],
            "implementation": "native_downward_surface_probe_raw_payload",
            "interface_implemented": True,
            "camera_or_visibility_certified": False,
            "profile_native_success_certified": False,
            "business_outcome_validated": False,
        },
        "local_surface_probe": {
            "region_ids": [],
            "scope": "robot_local_diagnostic_not_region_business_outcome",
            "implementation": "native_downward_surface_probe_raw_payload",
            "interface_implemented": True,
            "profile_native_success_certified": False,
            "business_outcome_validated": False,
        },
    }


def _route_bindings(public: Mapping[str, Any], generated_ids: set[str]) -> dict[str, Any]:
    rows = {}
    bindings = _region_bindings(public)
    for route in public["public_world"]["routes"]:
        generated = route["id"] in generated_ids
        if generated:
            _distance, length_semantics = _center_distance(
                bindings[route["from_region_id"]], bindings[route["to_region_id"]])
        else:
            length_semantics = "frozen_author_nominal_not_measured_planned_length"
        rows[route["id"]] = {
            "geometry_status": "author_defined_nominal",
            "length_semantics": length_semantics,
            "clearance_status": "author_defined_envelope_not_physically_verified",
            "native_reachability_verified": False,
            "resource_id": route["resource_id"],
            "capacity": route["capacity"],
        }
    return rows


def _deployment(public: Mapping[str, Any]) -> dict[str, Any]:
    generated_ids = {
        row["id"] for row in public["public_world"]["routes"]
        if (row["from_region_id"].startswith("restaurant_spawn_")
            or row["to_region_id"].startswith("restaurant_spawn_"))
    }
    return {
        "schema": DEPLOYMENT_SCHEMA,
        "version": DEPLOYMENT_VERSION,
        "profile": DEPLOYMENT_PROFILE,
        "original_task_id": ORIGINAL_TASK_ID,
        "original_public_task_sha256": ORIGINAL_PUBLIC_TASK_SHA256,
        "task_id": public["task_id"],
        "public_task_sha256": canonical_sha256(public),
        "region_membership_semantics": {
            "center_xy_m": "native_root_xy_inside_closed_disk",
            "center_xyz_m": "native_root_xyz_inside_closed_ball",
            "initial_mapping": "designated_spawn_region_only_after_measured_membership",
            "spawn_region_reservation": "none_compatible_visitors_allowed",
            "staging_identity": "one_shared_xy_region_distinct_from_spawn_regions",
            "post_warmup_stability_certified": False,
        },
        "region_bindings": _region_bindings(public),
        "spawn_regions": _spawn_regions(public),
        "route_bindings": _route_bindings(public, generated_ids),
        "route_request_semantics": {
            "public_topology": "general_compatible_spawn_to_operational_regions_bidirectional",
            "generated_length": "euclidean_lower_bound_geometry_not_measured_planned_length",
            "resource_pairing": "opposite_directions_share_one_link_resource_and_capacity",
            "cross_link_mutex": "none_unless_an_existing_route_declares_the_same_resource",
            "execution_failure": "missing_route_or_source_support_is_platform_gap",
            "native_reachability_verified": False,
        },
        "observation_support": _observation_support(),
        "source_revision": {
            "scene_key": config.BENCHMARK_SCENE_KEY,
            "usd_relative_path": config.USD_RELATIVE_PATH,
            "source_sha256": config.SOURCE_SHA256,
            "unresolved_manifest_sha256": config.UNRESOLVED_MANIFEST_SHA256,
            "used_layer_manifest_sha256": config.USED_LAYER_MANIFEST_SHA256,
            "publication_status": "local_candidate",
            "provider_revision": None,
            "product_sha256": deepcopy(EXPECTED_PRODUCT_SHA256),
            "bundle_manifest_sha256": EXPECTED_BUNDLE_MANIFEST_SHA256,
            "metadata_sha256": EXPECTED_METADATA_SHA256,
            "occupancy_map": {
                "resource_id": "occupancy_map",
                "runtime_path_name": "restaurant_benchmark_map.yaml",
                "yaml_sha256": EXPECTED_PRODUCT_SHA256[
                    "restaurant_benchmark_map.yaml"],
                "png_sha256": EXPECTED_PRODUCT_SHA256[
                    "restaurant_benchmark_map.png"],
                "status": "frozen_local_candidate_hashes_runtime_resolution_required",
            },
            "runtime_products_required": True,
        },
        **CLOSED_VALIDATION_FLAGS,
    }


def validate_restaurant_deployment_v2(value: Mapping[str, Any]) -> dict[str, Any]:
    """Fail closed unless a package exactly matches repository authorities."""
    if not isinstance(value, Mapping) or set(value) != {
            "public_task", "participant_api", "deployment"}:
        raise ValueError("Restaurant deployment package fields mismatch")
    expected_public = _derive_public_task()
    if value["public_task"] != expected_public:
        raise ValueError("Restaurant deployment public task is foreign or malformed")
    validate_goal_task_spec(value["public_task"])
    expected_api = build_goal_participant_api(expected_public)
    if value["participant_api"] != expected_api:
        raise ValueError("Restaurant deployment participant API is foreign or malformed")
    validate_goal_participant_api(value["participant_api"], task_spec=expected_public)
    expected_deployment = _deployment(expected_public)
    if value["deployment"] != expected_deployment:
        raise ValueError("Restaurant companion deployment is foreign or malformed")
    return deepcopy(dict(value))


def build_restaurant_deployment_v2() -> dict[str, Any]:
    """Return the canonical derived Goal v3 task, API, and metric companion."""
    public = _derive_public_task()
    package = {
        "public_task": public,
        "participant_api": build_goal_participant_api(public),
        "deployment": _deployment(public),
    }
    return validate_restaurant_deployment_v2(package)


__all__ = [
    "DEPLOYMENT_PROFILE",
    "build_restaurant_deployment_v2",
    "validate_restaurant_deployment_v2",
]
