from copy import deepcopy
import hashlib
from pathlib import Path

import pytest

from demo.benchmark.goal_driven_tasks import build_goal_task_spec
from demo.benchmark.goal_indoor_deployment import build_indoor_deployment_overlay


SCENES = ("factory", "warehouse_nvd", "modern_house_nvd")


@pytest.mark.parametrize("scene_key", SCENES)
def test_overlay_has_exact_public_coverage_and_frozen_parameters(scene_key):
    public = build_goal_task_spec(scene_key)
    overlay = build_indoor_deployment_overlay(public)
    world = public["public_world"]
    assert overlay["scene_key"] == scene_key
    assert overlay["source_verification_requested"] is True
    assert len(overlay["pending_actions"]) == 4
    assert all(row["status"] == "pending" for row in overlay["pending_actions"])
    assert {row["id"] for row in overlay["object_bindings"]} == {
        row["id"] for row in world["objects"]}
    assert {row["id"] for row in overlay["region_bindings"]} == {
        row["id"] for row in world["regions"]}
    assert {row["id"] for row in overlay["route_bindings"]} == {
        row["id"] for row in world["routes"]}
    assert {row["id"] for row in overlay["robot_spawns"]} == {
        row["id"] for row in public["candidate_robot_pool"]}
    assert {row["id"] for row in overlay["vertical_link_bindings"]} == {
        row["id"] for row in world["vertical_links"]}
    expected_objects = {row["id"]: row for row in world["objects"]}
    for row in overlay["object_bindings"]:
        expected = expected_objects[row["id"]]["physical_properties"]
        directive = row["deployment_directive"]
        assert directive["mass_kg"] == expected["mass_kg"]
        assert directive["size_m"] == expected["size_m"]
        assert directive["grasp_points"] == expected["grasp_points"]
        assert directive["handling_modes"] == expected["handling_modes"]
        assert row["initial_region_check"]["admitted"] is True
        region_bounds = row["initial_region_check"]["region_candidate_aabb_xy_m"]
        object_bounds = row["initial_region_check"]["object_candidate_aabb_xy_m"]
        assert object_bounds[0] >= region_bounds[0] and object_bounds[1] >= region_bounds[1]
        assert object_bounds[2] <= region_bounds[2] and object_bounds[3] <= region_bounds[3]
        assert row["physical_verified"] is False


@pytest.mark.parametrize("scene_key", SCENES)
def test_ground_spawns_are_distinct_and_map_admitted(scene_key):
    overlay = build_indoor_deployment_overlay(build_goal_task_spec(scene_key))
    instance_ids = [row["deployment_directive"]["instance_id"]
                    for row in overlay["robot_spawns"]]
    assert len(instance_ids) == len(set(instance_ids))
    by_type = {}
    for row in overlay["robot_spawns"]:
        directive = row["deployment_directive"]
        by_type.setdefault(directive["type"], []).append(tuple(directive["position_m"]))
        if row["public_robot"]["mobility"] == "ground":
            assert row["status"] == "map_checked"
            assert row["map_check"]["admitted"] is True
            assert row["map_check"]["check_kind"] == "footprint"
    for positions in by_type.values():
        assert len(positions) == len(set(positions))


def test_native_assets_keep_geometry_and_legacy_differences_explicit():
    for scene_key in ("warehouse_nvd", "modern_house_nvd"):
        overlay = build_indoor_deployment_overlay(build_goal_task_spec(scene_key))
        for row in overlay["object_bindings"]:
            asset = row["deployment_directive"]["asset"]
            assert asset["kind"] == "native_usd_asset_with_public_parameter_overrides"
            assert asset["native_size_m"] != row["deployment_directive"]["size_m"]
            assert asset["geometry_scale_xyz"] != [1.0, 1.0, 1.0]
            assert row["legacy_candidate"]["id"] != row["id"]
            assert row["legacy_identity_equivalent"] is False
            assert {difference["field"] for difference in row["native_and_legacy_differences"]} >= {
                "size_m", "mass_kg", "legacy_instance_id", "legacy_size_m", "legacy_mass_kg"}


@pytest.mark.parametrize("scene_key,floor_z", [("modern_house_nvd", .25), ("warehouse_nvd", 0.)])
def test_native_objects_start_above_scene_support_plane(scene_key, floor_z):
    overlay = build_indoor_deployment_overlay(build_goal_task_spec(scene_key))
    for row in overlay["object_bindings"]:
        directive = row["deployment_directive"]
        bottom = directive["position_m"][2] - directive["size_m"][2] / 2
        assert bottom == pytest.approx(floor_z)


@pytest.mark.parametrize("scene_key", SCENES)
def test_routes_fail_closed_or_carry_an_astar_polyline(scene_key):
    overlay = build_indoor_deployment_overlay(build_goal_task_spec(scene_key))
    for row in overlay["route_bindings"]:
        if row["status"] == "map_checked":
            assert row["map_check"]["admitted"] is True
            assert row["map_check"]["check_kind"] == "segment"
            assert len(row["planning_candidate"]["polyline_m"]) >= 2
            assert row["planning_candidate"]["basis"] == "deterministic_inflated_grid_astar"
        else:
            assert row["status"] == "blocked"
            assert row["map_check"]["admitted"] is False
            assert row["planning_candidate"] is None
    if scene_key == "warehouse_nvd":
        blocked = {row["id"] for row in overlay["route_bindings"] if row["status"] == "blocked"}
        assert {"consolidation_to_rack_a", "rack_a_to_consolidation"} <= blocked
    if scene_key == "factory":
        vertical = {row["id"]: row for row in overlay["vertical_link_bindings"]}
        assert vertical["main_stair"]["public_open"] is True
        assert vertical["main_stair"]["physical_binding_status"] == "pending"
        routes = {row["id"]: row for row in overlay["route_bindings"]}
        assert routes["main_stair_up"]["status"] == "blocked"
        assert routes["main_stair_down"]["status"] == "blocked"


def test_warehouse_crate_route_distinguishes_blocked_author_center_from_disconnection():
    overlay = build_indoor_deployment_overlay(build_goal_task_spec("warehouse_nvd"))
    routes = {row["id"]: row for row in overlay["route_bindings"]}
    outbound = routes["rack_a_to_consolidation"]
    inbound = routes["consolidation_to_rack_a"]
    for row, start, goal in ((outbound, False, True), (inbound, True, False)):
        assert row["status"] == "blocked"
        assert row["physical_verified"] is False
        assert row["planning_candidate"] is None
        assert row["map_check"]["failure_class"] == "endpoint_blocked"
        assert row["map_check"]["start_cell_passable"] is start
        assert row["map_check"]["goal_cell_passable"] is goal


def test_source_files_are_absolute_current_hashes():
    overlay = build_indoor_deployment_overlay(build_goal_task_spec("warehouse_nvd"))
    assert overlay["source_files"]
    for name, expected in overlay["source_files"].items():
        path = Path(name)
        assert path.is_absolute() and path.is_file()
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected
    names = set(overlay["source_files"])
    assert str((Path("demo/benchmark/goal_indoor_deployment.py").resolve())) in names
    assert str((Path("demo/benchmark/goal_scene_binding.py").resolve())) in names
    assert str((Path("source/EAI/EAI/scenario_spec/geometry.py").resolve())) in names
    assert str((Path("source/EAI/EAI/scenario_spec/oracle.py").resolve())) in names
    assert any(name.endswith("ground_map_0.6m.yaml") for name in names)
    assert any(name.endswith("ground_map_0.6m.png") for name in names)


@pytest.mark.parametrize("scene_key", SCENES)
def test_map_checks_state_point_segment_and_footprint_scope(scene_key):
    overlay = build_indoor_deployment_overlay(build_goal_task_spec(scene_key))
    assert {row["map_check"]["check_kind"] for row in overlay["region_bindings"]} == {"point"}
    assert {row["map_check"]["check_kind"] for row in overlay["route_bindings"]} == {"segment"}
    assert {row["map_check"]["check_kind"] for row in overlay["object_bindings"]} == {"footprint"}
    assert {row["map_check"]["check_kind"] for row in overlay["robot_spawns"]} == {"footprint"}


def test_modified_public_parameters_do_not_reuse_frozen_scene_binding():
    public = deepcopy(build_goal_task_spec("warehouse_nvd"))
    public["public_world"]["objects"][0]["physical_properties"]["mass_kg"] = 60.0
    with pytest.raises(ValueError, match="current frozen public task"):
        build_indoor_deployment_overlay(public)


def test_current_inventory_source_hash_is_confirmed(monkeypatch):
    import demo.benchmark.goal_indoor_deployment as deployment

    original = deployment.build_goal_scene_binding_inventory
    report = original("factory", verify_sources=False)
    present = next(row for row in report["sources"] if row["status"] == "present")
    present["sha256"] = "0" * 64
    monkeypatch.setattr(deployment, "build_goal_scene_binding_inventory",
                        lambda scene_key, verify_sources: deepcopy(report))
    public = build_goal_task_spec("factory")
    with pytest.raises(ValueError, match="current binding inventory"):
        deployment.build_indoor_deployment_overlay(public)

    monkeypatch.setattr(deployment, "build_goal_scene_binding_inventory", original)
    overlay = deployment.build_indoor_deployment_overlay(public, verify_sources=False)
    assert overlay["scene_key"] == "factory"
    assert overlay["source_verification_requested"] is False
