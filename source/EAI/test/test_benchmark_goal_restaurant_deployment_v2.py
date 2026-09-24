from __future__ import annotations

from copy import deepcopy

import pytest


def _task(task_id, skill, arguments, prerequisites, agents, start_s, duration_s):
    return {
        "task_id": task_id,
        "skill": skill,
        "arguments": arguments,
        "prerequisites": prerequisites,
        "required_capabilities": [skill],
        "assigned_agents": agents,
        "evidence_refs": [],
        "execution": {"start_s": start_s, "duration_s": duration_s},
    }


def _plan_a():
    agent = "restaurant_carter_1"
    rows = [
        _task("k-nav", "ground_navigation", {
            "region_id": "kitchen_service",
            "route_ids": ["restaurant_spawn_carter_1_to_kitchen_service"],
        }, [], [agent], 0.0, 8.0),
        _task("k-see", "observe", {
            "region_id": "kitchen_service", "subject_ref": "kitchen_service",
            "observation_mode": "service_ground_readiness",
        }, ["k-nav"], [agent], 8.0, 4.0),
        _task("d-nav", "ground_navigation", {
            "region_id": "dining_service",
            "route_ids": ["restaurant_kitchen_to_dining"],
        }, ["k-see"], [agent], 12.0, 8.0),
        _task("d-see", "observe", {
            "region_id": "dining_service", "subject_ref": "dining_service",
            "observation_mode": "service_ground_readiness",
        }, ["d-nav"], [agent], 20.0, 4.0),
        _task("return", "ground_navigation", {
            "region_id": "restaurant_staging",
            "route_ids": ["restaurant_dining_to_staging"],
        }, ["d-see"], [agent], 24.0, 8.0),
        _task("relay", "relay", {
            "message_kind": "restaurant_readiness_receipt",
            "region_id": "restaurant_staging",
        }, ["return", "k-see", "d-see"], [agent], 32.0, 1.0),
    ]
    return {"schema": "eai.benchmark.goal_plan", "version": 3,
            "plan_id": "participant-serial", "assigned_team": [agent], "tasks": rows}


def _plan_b():
    carter, m20 = "restaurant_carter_2", "restaurant_m20_1"
    rows = [
        _task("k-nav", "ground_navigation", {
            "region_id": "kitchen_service",
            "route_ids": ["restaurant_spawn_carter_2_to_kitchen_service"],
        }, [], [carter], 0.0, 8.0),
        _task("k-see", "observe", {
            "region_id": "kitchen_service", "subject_ref": "kitchen_service",
            "observation_mode": "service_ground_readiness",
        }, ["k-nav"], [carter], 8.0, 4.0),
        _task("d-nav", "ground_navigation", {
            "region_id": "dining_service",
            "route_ids": ["restaurant_spawn_m20_1_to_dining_service"],
        }, [], [m20], 0.0, 8.0),
        _task("d-see", "observe", {
            "region_id": "dining_service", "subject_ref": "dining_service",
            "observation_mode": "service_ground_readiness",
        }, ["d-nav"], [m20], 8.0, 4.0),
        _task("return", "ground_navigation", {
            "region_id": "restaurant_staging",
            "route_ids": ["restaurant_kitchen_to_staging"],
        }, ["k-see"], [carter], 12.0, 8.0),
        _task("relay", "relay", {
            "message_kind": "restaurant_readiness_receipt",
            "region_id": "restaurant_staging",
        }, ["return", "k-see", "d-see"], [carter], 20.0, 1.0),
    ]
    return {"schema": "eai.benchmark.goal_plan", "version": 3,
            "plan_id": "participant-parallel", "assigned_team": [carter, m20],
            "tasks": rows}


def test_builds_distinct_goal3_identity_and_preserves_frozen_restaurant_package():
    from demo.benchmark.goal_driven_contract import (
        validate_goal_participant_api,
        validate_goal_task_spec,
    )
    from demo.benchmark.goal_driven_tasks import build_goal_task_package
    from demo.benchmark.goal_restaurant_deployment_v2 import (
        DEPLOYMENT_PROFILE,
        ORIGINAL_PUBLIC_TASK_SHA256,
        build_restaurant_deployment_v2,
        validate_restaurant_deployment_v2,
    )
    from demo.benchmark.provenance import canonical_sha256

    original = build_goal_task_package("restaurant")["public_task"]
    preserved = deepcopy(original)
    package = build_restaurant_deployment_v2()
    public, api, deployment = (package["public_task"], package["participant_api"],
                               package["deployment"])

    assert original == preserved
    assert original["task_id"] == "RESTAURANT-GOAL-01"
    assert canonical_sha256(original) == ORIGINAL_PUBLIC_TASK_SHA256
    assert public["task_id"] == "RESTAURANT-GOAL-NATIVE-02"
    assert public["version"] == 3
    assert public["skill_catalog_version"] == original["skill_catalog_version"]
    assert deployment["profile"] == DEPLOYMENT_PROFILE == "metric_regions_v2"
    assert deployment["original_task_id"] == original["task_id"]
    assert deployment["original_public_task_sha256"] == canonical_sha256(original)
    assert deployment["task_id"] == public["task_id"]
    assert deployment["public_task_sha256"] == canonical_sha256(public)
    for field in ("required_outcomes", "candidate_robot_pool", "budgets",
                  "execution_contract", "experiment_factor"):
        assert public[field] == original[field]
    changed_constraints = [
        index for index, (before, after) in enumerate(zip(
            original["business_constraints"], public["business_constraints"]))
        if before != after
    ]
    assert changed_constraints == [3]
    assert "全八台 runtime 绑定" in public["business_constraints"][3]
    assert "不是相机输出" in public["business_constraints"][3]
    assert "完整公开参数域" in public["business_constraints"][3]
    assert "release/score 门仍未验证" in public["business_constraints"][3]
    assert validate_goal_task_spec(public)["validated"]
    assert validate_goal_participant_api(api, task_spec=public)["validated"]
    assert validate_restaurant_deployment_v2(package) == package
    assert "deployment_assessment" in api["action_types"]


def test_full8_spawn_regions_are_distinct_metric_slots_not_agent_reservations():
    from demo.benchmark.goal_restaurant_deployment_v2 import (
        build_restaurant_deployment_v2,
    )
    from demo.restaurant_benchmark.config import BENCHMARK_ROBOT_SPAWN_XYZ_M

    package = build_restaurant_deployment_v2()
    public, deployment = package["public_task"], package["deployment"]
    spawns, bindings = deployment["spawn_regions"], deployment["region_bindings"]
    assert len(spawns) == len(set(spawns.values())) == 8
    assert set(spawns) == {row["id"] for row in public["candidate_robot_pool"]}
    public_regions = {row["id"]: row for row in public["public_world"]["regions"]}
    robots = {row["id"]: row for row in public["candidate_robot_pool"]}
    for public_id, region_id in spawns.items():
        native_id = public_id.removeprefix("restaurant_")
        xyz = list(BENCHMARK_ROBOT_SPAWN_XYZ_M[native_id])
        assert public_regions[region_id]["kind"] == "deployment_spawn_region"
        assert "保留" in public_regions[region_id]["public_description"]
        assert bindings[region_id]["geometry_status"] == "defined"
        if robots[public_id]["mobility"] == "ground":
            assert bindings[region_id] == {
                "geometry_status": "defined", "center_xy_m": xyz[:2],
                "tolerance_m": 0.50, "access_modes": ["ground_navigation"]}
        else:
            assert bindings[region_id] == {
                "geometry_status": "defined", "center_xyz_m": xyz,
                "tolerance_m": 0.30, "access_modes": ["aerial_navigation"]}

    # Region names do not reserve a slot for the similarly named robot: any
    # compatible ground visitor may request Carter1's region; aerial mobility
    # is excluded by the region's public access mode.
    carter1_region = spawns["restaurant_carter_1"]
    assert robots["restaurant_carter_2"]["mobility"] == "ground"
    assert "ground_navigation" in bindings[carter1_region]["access_modes"]
    assert robots["restaurant_iris_1"]["mobility"] == "aerial"
    assert "aerial_navigation" not in bindings[carter1_region]["access_modes"]
    routes = {row["id"]: row for row in public["public_world"]["routes"]}
    assert routes["restaurant_staging_to_spawn_carter_1"]["to_region_id"] == carter1_region


def test_shared_staging_and_service_geometry_keep_separate_semantics():
    from demo.benchmark.goal_restaurant_deployment_v2 import (
        build_restaurant_deployment_v2,
    )
    from demo.restaurant_benchmark import navigation_probe, route_admission

    package = build_restaurant_deployment_v2()
    deployment = package["deployment"]
    bindings = deployment["region_bindings"]
    staging = bindings["restaurant_staging"]
    carter_spawn = bindings["restaurant_spawn_carter_1"]
    assert staging == {
        "geometry_status": "defined",
        "center_xy_m": list(navigation_probe.ROUTES["A-E"]["start_xy_m"]),
        "aerial_goal_z_m": 3.0,
        "tolerance_m": navigation_probe.FINAL_TOLERANCE_M,
        "access_modes": ["ground_navigation", "aerial_navigation"],
    }
    assert staging["center_xy_m"] == carter_spawn["center_xy_m"]
    assert staging["tolerance_m"] == 0.25
    assert carter_spawn["tolerance_m"] == 0.50
    assert bindings["kitchen_service"]["tolerance_m"] == 0.25
    assert bindings["dining_service"]["tolerance_m"] == 0.25
    assert bindings["restaurant_overview"] == {
        "geometry_status": "defined",
        "center_xyz_m": list(route_admission.OVERVIEW_ANCHOR_XYZ_M),
        "tolerance_m": 0.30,
        "access_modes": ["aerial_navigation"],
    }
    for region_id in ("restaurant_standby_a", "restaurant_standby_b"):
        assert bindings[region_id] == {
            "geometry_status": "unavailable",
            "access_modes": ["ground_navigation"],
        }
    assert deployment["region_membership_semantics"]["initial_mapping"] == (
        "designated_spawn_region_only_after_measured_membership")
    public = package["public_task"]

    def strings(value):
        if isinstance(value, dict):
            return [item for child in value.values() for item in strings(child)]
        if isinstance(value, list):
            return [item for child in value for item in strings(child)]
        return [value] if isinstance(value, str) else []

    assert all("staging 是所有地面成员的起始参考" not in value
               for value in strings(public))
    assert public["public_world"]["public_notes"].count(
        "restaurant_staging 是 Carter A-E 锚点定义的唯一共享 XY 区域") == 1


def test_topology_is_general_bidirectional_and_preserves_resource_pairing():
    from demo.benchmark.goal_restaurant_deployment_v2 import (
        build_restaurant_deployment_v2,
    )

    package = build_restaurant_deployment_v2()
    public, deployment = package["public_task"], package["deployment"]
    routes = {row["id"]: row for row in public["public_world"]["routes"]}
    robots = {row["id"]: row for row in public["candidate_robot_pool"]}
    resources = set()
    for public_id, spawn_region in deployment["spawn_regions"].items():
        mobility = robots[public_id]["mobility"]
        targets = (["restaurant_staging", "kitchen_service", "dining_service"]
                   if mobility == "ground"
                   else ["restaurant_staging", "restaurant_overview"])
        spawn_short = spawn_region.removeprefix("restaurant_")
        for target in targets:
            target_short = target.removeprefix("restaurant_")
            outward = routes[f"restaurant_{spawn_short}_to_{target_short}"]
            reverse = routes[f"restaurant_{target_short}_to_{spawn_short}"]
            assert outward["from_region_id"] == reverse["to_region_id"] == spawn_region
            assert outward["to_region_id"] == reverse["from_region_id"] == target
            assert outward["resource_id"] == reverse["resource_id"]
            assert outward["capacity"] == reverse["capacity"] == 1
            resources.add(outward["resource_id"])
            for route in (outward, reverse):
                review = deployment["route_bindings"][route["id"]]
                assert review["length_semantics"] in {
                    "euclidean_lower_bound_geometry",
                    "co_located_semantic_transition_schema_positive_epsilon",
                }
                assert review["native_reachability_verified"] is False
                assert "not_physically_verified" in review["clearance_status"]
    # 6 ground * 3 targets plus 2 aerial * 2 targets; each independent link
    # has its own resource, while only its reverse direction shares it.
    assert len(resources) == 22


def test_two_participant_decompositions_are_legal_without_public_witness_leak():
    from demo.benchmark.goal_driven_contract import validate_goal_plan
    from demo.benchmark.goal_restaurant_deployment_v2 import (
        build_restaurant_deployment_v2,
    )

    public = build_restaurant_deployment_v2()["public_task"]
    assert validate_goal_plan(_plan_a(), task_spec=public)["validated"]
    assert validate_goal_plan(_plan_b(), task_spec=public)["validated"]

    forbidden = {"witness_plans", "reference_plan", "reference_assignment",
                 "assigned_agents", "prerequisites", "execution_steps"}

    def keys(value):
        if isinstance(value, dict):
            return set(value) | {item for child in value.values() for item in keys(child)}
        if isinstance(value, list):
            return {item for child in value for item in keys(child)}
        return set()

    assert not forbidden & keys(public)


def test_opposite_directions_cannot_bypass_the_shared_link_capacity():
    from demo.benchmark.goal_driven_contract import validate_goal_plan
    from demo.benchmark.goal_restaurant_deployment_v2 import (
        build_restaurant_deployment_v2,
    )

    carter1, carter2 = "restaurant_carter_1", "restaurant_carter_2"
    plan = {
        "schema": "eai.benchmark.goal_plan", "version": 3,
        "plan_id": "opposite-direction-conflict",
        "assigned_team": [carter1, carter2],
        "tasks": [
            _task("outward", "ground_navigation", {
                "region_id": "kitchen_service",
                "route_ids": ["restaurant_spawn_carter_1_to_kitchen_service"],
            }, [], [carter1], 0.0, 8.0),
            _task("reverse", "ground_navigation", {
                "region_id": "restaurant_spawn_carter_1",
                "route_ids": ["restaurant_kitchen_service_to_spawn_carter_1"],
            }, [], [carter2], 0.0, 8.0),
        ],
    }
    public = build_restaurant_deployment_v2()["public_task"]
    with pytest.raises(ValueError, match="capacity exceeded"):
        validate_goal_plan(plan, task_spec=public)


def test_observation_support_and_closed_gates_do_not_claim_camera_or_success():
    from demo.benchmark.goal_restaurant_deployment_v2 import (
        build_restaurant_deployment_v2,
    )

    deployment = build_restaurant_deployment_v2()["deployment"]
    support = deployment["observation_support"]
    assert support["service_ground_readiness"]["region_ids"] == [
        "kitchen_service", "dining_service"]
    assert support["aerial_overview"]["camera_or_visibility_certified"] is False
    assert support["local_surface_probe"]["scope"] == (
        "robot_local_diagnostic_not_region_business_outcome")
    assert all(row["profile_native_success_certified"] is False
               for row in support.values())
    for field in ("execution_validated", "business_outcomes_validated",
                  "feasibility_oracle_verified", "release_ready", "scoreable"):
        assert deployment[field] is False


def test_source_revision_is_the_frozen_local_candidate_not_a_provider_release():
    from demo.benchmark.goal_restaurant_deployment_v2 import (
        build_restaurant_deployment_v2,
    )
    from EAI_assets.restaurant_benchmark_products import (
        EXPECTED_BUNDLE_MANIFEST_SHA256,
        EXPECTED_METADATA_SHA256,
        EXPECTED_PRODUCT_SHA256,
    )

    source = build_restaurant_deployment_v2()["deployment"]["source_revision"]
    assert source["publication_status"] == "local_candidate"
    assert source["provider_revision"] is None
    assert source["product_sha256"] == EXPECTED_PRODUCT_SHA256
    assert source["bundle_manifest_sha256"] == EXPECTED_BUNDLE_MANIFEST_SHA256
    assert source["metadata_sha256"] == EXPECTED_METADATA_SHA256
    assert source["occupancy_map"] == {
        "resource_id": "occupancy_map",
        "runtime_path_name": "restaurant_benchmark_map.yaml",
        "yaml_sha256": EXPECTED_PRODUCT_SHA256["restaurant_benchmark_map.yaml"],
        "png_sha256": EXPECTED_PRODUCT_SHA256["restaurant_benchmark_map.png"],
        "status": "frozen_local_candidate_hashes_runtime_resolution_required",
    }
    assert source["runtime_products_required"] is True


@pytest.mark.parametrize("mutation", ["task_id", "task_hash", "profile", "binding", "api"])
def test_validation_rejects_foreign_or_malformed_versions(mutation):
    from demo.benchmark.goal_restaurant_deployment_v2 import (
        build_restaurant_deployment_v2,
        validate_restaurant_deployment_v2,
    )

    package = build_restaurant_deployment_v2()
    if mutation == "task_id":
        package["public_task"]["task_id"] = "RESTAURANT-GOAL-NATIVE-OTHER"
    elif mutation == "task_hash":
        package["deployment"]["public_task_sha256"] = "0" * 64
    elif mutation == "profile":
        package["deployment"]["profile"] = "foreign"
    elif mutation == "binding":
        package["deployment"]["region_bindings"]["restaurant_staging"][
            "center_xy_m"][0] += 1.0
    else:
        package["participant_api"]["task_id"] = "RESTAURANT-GOAL-NATIVE-OTHER"
    with pytest.raises(ValueError, match="foreign or malformed"):
        validate_restaurant_deployment_v2(package)
