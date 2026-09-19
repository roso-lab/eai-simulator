"""Offline Modern House region double-binding tests; no Isaac/ROS launch.

These tests rebuild the measured region intervals from the real provider
occupancy map and the authored bench collision layer with the standalone
OpenUSD reader, exactly like the deployment overlay does. They assert
fail-closed behavior of the geometry derivation and its overlay integration,
never a live scene measurement.
"""

import math
from pathlib import Path

import pytest

from demo.benchmark.goal_driven_tasks import build_goal_task_spec
from demo.benchmark.goal_indoor_deployment import build_indoor_deployment_overlay
from demo.benchmark import goal_indoor_deployment as deployment
from demo.benchmark import goal_region_geometry as geometry
from EAI.scenario_spec.geometry import provider_map


WALL_LAYER = """#usda 1.0
(
    metersPerUnit = 1
    upAxis = "Z"
)

def Cube "WallA" (
    prepend apiSchemas = ["PhysicsCollisionAPI"]
)
{
    double3 xformOp:translate = (2, 0, 0.4)
    double3 xformOp:scale = (0.1, 2, 0.8)
    uniform token[] xformOpOrder = ["xformOp:translate", "xformOp:scale"]
    bool physics:collisionEnabled = True
}

def Cube "AuthoredSize" (
    prepend apiSchemas = ["PhysicsCollisionAPI"]
)
{
    double size = 1
    double3 xformOp:translate = (4, 0, 0.4)
    double3 xformOp:scale = (0.1, 2, 0.8)
    uniform token[] xformOpOrder = ["xformOp:translate", "xformOp:scale"]
}

def Cube "Rotated" (
    prepend apiSchemas = ["PhysicsCollisionAPI"]
)
{
    double3 xformOp:translate = (6, 0, 0.4)
    double3 xformOp:scale = (0.1, 2, 0.8)
    uniform token[] xformOpOrder = ["xformOp:translate", "xformOp:rotateZYX", "xformOp:scale"]
}

def Cube "Disabled" (
    prepend apiSchemas = ["PhysicsCollisionAPI"]
)
{
    double3 xformOp:translate = (8, 0, 0.4)
    double3 xformOp:scale = (0.1, 2, 0.8)
    uniform token[] xformOpOrder = ["xformOp:translate", "xformOp:scale"]
    bool physics:collisionEnabled = False
}

def Mesh "NotACube"
{
}
"""


@pytest.fixture(scope="module")
def occupancy():
    return provider_map(scene_key="modern_house_nvd")


@pytest.fixture(scope="module")
def layer_path():
    return geometry.house_bench_layer_path()


def test_static_wall_cuboids_enumerate_only_plain_enabled_cubes(tmp_path):
    path = tmp_path / "walls.usda"
    path.write_text(WALL_LAYER)
    result = geometry.static_wall_cuboids(path)
    assert result["status"] == "enumerated"
    rows = {row["prim_path"]: row for row in result["cuboids"]}
    assert set(rows) == {"/WallA", "/AuthoredSize"}
    assert rows["/WallA"]["low_xyz_m"] == pytest.approx([1.9, -2.0, -0.4])
    assert rows["/WallA"]["high_xyz_m"] == pytest.approx([2.1, 2.0, 1.2])
    assert rows["/AuthoredSize"]["low_xyz_m"] == pytest.approx([3.95, -1.0, 0.0])
    assert result["sha256"] == __import__("hashlib").sha256(path.read_bytes()).hexdigest()


def test_static_wall_cuboids_fail_closed_without_layer(tmp_path):
    result = geometry.static_wall_cuboids(tmp_path / "absent.usda")
    assert result["status"] == "missing_layer"
    assert result["cuboids"] == []
    assert result["sha256"] is None


def test_radial_interval_measures_walls_and_open_directions(occupancy):
    interval = geometry.radial_free_interval(occupancy, [4.0, 3.5])
    assert interval["status"] == "measured"
    assert interval["ray_count"] == geometry.DEFAULT_RAY_COUNT
    seed_x, seed_y = interval["seed_xy_m"]
    low_x, low_y, high_x, high_y = interval["interval_aabb_xy_m"]
    radius = interval["max_radius_m"]
    assert seed_x - radius - 1e-6 <= low_x and high_x <= seed_x + radius + 1e-6
    assert seed_y - radius - 1e-6 <= low_y and high_y <= seed_y + radius + 1e-6
    assert low_x < 1.0  # the west wall closes the entry hall
    assert interval["open_ray_count"] > 0  # the hall leaks through doorways
    wall_point = next(ray["boundary_xy_m"] for ray in interval["rays"] if ray["hit"])
    assert geometry.radial_free_interval(occupancy, wall_point)["status"] == "seed_cell_not_free"


def test_doorway_openings_need_wall_corroborated_flanks(occupancy):
    walls = {"cuboids": []}
    rays = []
    for index in range(8):
        angle = 2 * math.pi * index / 8
        direction = (math.cos(angle), math.sin(angle))
        far = index == 2
        distance = 3.0 if far else 1.0
        rays.append({"angle_rad": round(angle, 6), "hit": not far, "distance_m": distance,
                     "boundary_xy_m": [direction[0] * distance, direction[1] * distance]})
        if index in (1, 3):
            walls["cuboids"].append({
                "prim_path": f"/Wall{index}",
                "low_xyz_m": [direction[0] * 1.0 - .1, direction[1] * 1.0 - .1, 0.0],
                "high_xyz_m": [direction[0] * 1.0 + .1, direction[1] * 1.0 + .1, 0.8]})
    interval = {"status": "measured", "rays": rays, "seed_xy_m": [0.0, 0.0],
                "closed_median_distance_m": 1.0}
    doors = geometry.doorway_openings(interval, walls)
    assert len(doors) == 1
    door = doors[0]
    assert door["status"] == "wall_corroborated_doorway"
    assert door["width_m"] == pytest.approx(math.sqrt(2), abs=1e-3)
    assert door["threshold_xy_m"] == pytest.approx([0.0, math.sqrt(2) / 2], abs=1e-3)
    assert door["inside_xy_m"][1] < door["threshold_xy_m"][1] < door["outside_xy_m"][1]
    # A flank without any static wall cuboid never becomes a doorway.
    walls["cuboids"] = walls["cuboids"][:1]
    assert geometry.doorway_openings(interval, walls)[0]["status"] == "uncorroborated_opening"
    # An implausibly wide gap is not a door either.
    for ray in rays:
        if ray["hit"]:
            ray["boundary_xy_m"] = [ray["boundary_xy_m"][0] * 3, ray["boundary_xy_m"][1] * 3]
    assert geometry.doorway_openings(interval, walls)[0]["status"] == "uncorroborated_opening"


def test_derive_region_geometry_binds_map_and_layer(occupancy, layer_path):
    result = geometry.derive_region_geometry(occupancy, layer_path, [4.0, 3.5])
    assert result["status"] == "measured"
    assert result["kind"] == "occupancy_interval_plus_usd_static_wall_cuboids"
    assert result["map_sha256"] == occupancy.sha256
    assert result["layer"]["path"] == str(layer_path)
    assert result["layer"]["cuboid_count"] > 1000
    assert result["doorways"], "entry hall must expose at least one corroborated doorway"
    for door in result["doorways"]:
        assert {"threshold_xy_m", "inside_xy_m", "outside_xy_m", "width_m"} <= set(door)
        assert geometry.DEFAULT_GAP_MIN_M <= door["width_m"] <= geometry.DEFAULT_GAP_MAX_M
    assert result["limits"]


def test_house_overlay_double_binds_ground_regions_and_spawn_membership():
    overlay = build_indoor_deployment_overlay(build_goal_task_spec("modern_house_nvd"))
    regions = {row["id"]: row for row in overlay["region_bindings"]}
    assert regions["room_overview"]["candidate_geometry"]["kind"] == "author_proposed_aabb"
    assert regions["room_overview"]["candidate_geometry"]["boundary_status"] == "pending"
    for identifier, row in regions.items():
        if identifier == "room_overview":
            continue
        geometry_row = row["candidate_geometry"]
        assert geometry_row["kind"] == "measured_occupancy_interval_with_usd_wall_corroboration"
        assert geometry_row["boundary_status"] in {"wall_closed", "partially_open"}
        assert "reviewed_region_boundary_missing" not in row["gaps"]
        binding = geometry_row["measured_binding"]
        assert binding["map_sha256"] and binding["layer"]["sha256"]
        assert binding["interval_aabb_xy_m"] == geometry_row["aabb_xy_m"]
    assert regions["entry_hall"]["candidate_geometry"]["measured_binding"]["doorways"]
    for row in overlay["robot_spawns"]:
        check = row.get("initial_spawn_region_check")
        if row["public_robot"]["mobility"] == "ground":
            assert check["status"] == "candidate_checked"
            assert check["region_id"] == "entry_hall"
            assert check["bounds_source"] == "measured_interval"
        else:
            assert check is None
    for row in overlay["object_bindings"]:
        assert row["initial_region_check"]["bounds_source"] == "measured_interval"
        assert "initial_region_membership_not_physically_verified" not in row["gaps"]
    assert "spawn:house_ground_3:initial_region_membership_rejected" not in overlay["blockers"]


def test_spawn_outside_measured_interval_is_rejected(monkeypatch):
    proposals = deployment._SPAWN_PROPOSALS
    original = proposals["modern_house_nvd"]["house_ground_3"]
    proposals["modern_house_nvd"]["house_ground_3"] = ([11.0, 3.7, 0.77], original[1], original[2])
    try:
        overlay = build_indoor_deployment_overlay(build_goal_task_spec("modern_house_nvd"))
    finally:
        proposals["modern_house_nvd"]["house_ground_3"] = original
    row = next(row for row in overlay["robot_spawns"] if row["id"] == "house_ground_3")
    check = row["initial_spawn_region_check"]
    assert check["status"] == "blocked"
    assert check["admitted"] is False
    assert row["status"] == "blocked"
    assert "initial_spawn_region_membership_rejected" in row["gaps"]
    assert "spawn:house_ground_3:initial_region_membership_rejected" in overlay["blockers"]
    for other in overlay["robot_spawns"]:
        if other["id"] != "house_ground_3":
            assert other["status"] != "blocked"


def test_scenes_without_derivation_keep_authored_proposals():
    overlay = build_indoor_deployment_overlay(build_goal_task_spec("factory"))
    for row in overlay["region_bindings"]:
        assert row["candidate_geometry"]["kind"] == "author_proposed_aabb"
        assert row["candidate_geometry"]["boundary_status"] == "pending"
        assert "reviewed_region_boundary_missing" in row["gaps"]
    for row in overlay["robot_spawns"]:
        assert "initial_spawn_region_check" not in row
    for row in overlay["object_bindings"]:
        assert row["initial_region_check"]["bounds_source"] == "authored_center_half_meter"
        assert "initial_region_membership_not_physically_verified" in row["gaps"]


def test_region_geometry_source_is_pinned_by_overlay_sources():
    overlay = build_indoor_deployment_overlay(build_goal_task_spec("modern_house_nvd"))
    names = set(overlay["source_files"])
    assert str(Path("demo/benchmark/goal_region_geometry.py").resolve()) in names
