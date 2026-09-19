"""Pure 3-D admission checks; no Isaac, assets, GPU or networking required."""

import hashlib

import numpy as np
import pytest

from demo.benchmark.aerial_navigation import (
    AerialVoxelMap, aerial_route_points, load_aerial_voxel_map, oriented_box_aabb,
    plan_local_aerial_detour, resolve_aerial_command,
)


def volume(voxels=None, **kwargs):
    return AerialVoxelMap(np.zeros((12, 10, 10), dtype=bool) if voxels is None else voxels,
                         x0=-2., y0=10., res=.5, vox=.25, **kwargs)


def test_author_admission_checks_delivered_object_bounds_and_rejects_occupied_goal():
    from demo.benchmark.aerial_navigation import audit_aerial_plan
    cells = np.zeros((20, 30, 30), dtype=bool)
    grid = AerialVoxelMap(cells, x0=0., y0=0., res=.5, vox=.25)
    scenario = {"scene_key": "warehouse_nvd",
        "robots": [{"id": "cf2x_1", "type": "cf2x", "attachments": [],
                    "spawn_pose": {"position": [3., 3., 2.]}}],
        "composition": {"entities": [{"id": "crate", "size_m": [1., 1., 3.],
            "spawn_pose": {"position": [9., 9., 1.5], "rotation": [1, 0, 0, 0]}}],
            "targets": [{"id": "drop", "position": [6., 3., 0.]}]},
        "phase_graph": {"nodes": [
            {"id": "move", "skill_key": "joint_transport", "object_ref": "crate", "target_ref": "drop"},
            {"id": "scan", "skill_key": "aerial_scan"}]}}
    plan = [{"node": "move"}, {"node": "scan", "agent": "cf2x_1", "scan_goal": [6., 3., 2.]}]
    admitted_without_move = audit_aerial_plan(scenario, plan[1:], aerial_map=grid)
    assert admitted_without_move["validated"]
    report = audit_aerial_plan(scenario, plan, aerial_map=grid)
    assert not report["validated"]
    assert report["checks"][0]["local_detour"]["admitted"] is False
    assert not report["execution_validated"]


def check(grid, points, **kwargs):
    return grid.check_route(points, half_extents_m=(.05, .05, .05), margin_m=0., **kwargs)


def test_thin_wall_between_free_endpoints_blocks_continuous_segment():
    cells = np.zeros((12, 10, 10), dtype=bool)
    cells[4, 4, 4] = True  # x [0,.5], y [12,12.5], z [1,1.25]
    grid = volume(cells)
    assert check(grid, [(-1., 12.25, 1.1)])["admitted"]
    assert check(grid, [(2., 12.25, 1.1)])["admitted"]
    blocked = check(grid, [(-1., 12.25, 1.1), (2., 12.25, 1.1)])
    assert blocked["status"] == "blocked"
    assert blocked["occupied_cell_xyz"] == [4, 4, 4]
    assert check(grid, [(-1., 12.25, 1.5), (2., 12.25, 1.5)])["admitted"]


def test_diagonal_bounding_rectangle_does_not_replace_segment_intersection():
    cells = np.zeros((12, 10, 10), dtype=bool)
    cells[4, 7, 2] = True
    assert check(volume(cells), [(-1., 11., 1.1), (2., 14., 1.1)])["admitted"]


def test_body_envelope_and_exact_voxel_face_contact_block():
    cells = np.zeros((12, 10, 10), dtype=bool)
    cells[4, 4, 4] = True
    grid = volume(cells)
    assert check(grid, [(-1., 11.75, 1.1), (2., 11.75, 1.1)])["admitted"]
    result = grid.check_route([(-1., 11.75, 1.1), (2., 11.75, 1.1)],
                              half_extents_m=(.25, .25, .1), margin_m=0.)
    assert result["status"] == "blocked"


@pytest.mark.parametrize("point", [(-2., 12., 1.), (1., 15., 1.), (1., 12., 3.), (1., 12., 0.)])
def test_any_body_extent_outside_product_is_unknown(point):
    result = check(volume(), [point])
    assert result["status"] == "unknown"
    assert not result["admitted"]
    assert result["reason"] == "route_envelope_leaves_voxel_volume"


def test_vertical_climb_tests_intervening_ceiling_not_just_xy():
    cells = np.zeros((12, 10, 10), dtype=bool)
    cells[6, 4, 4] = True
    result = check(volume(cells), [(.25, 12.25, 1.), (.25, 12.25, 2.)])
    assert result["status"] == "blocked"


def test_nvd_coarse_proxy_expansion_is_accounted_for_and_y_is_not_flipped():
    cells = np.zeros((12, 10, 10), dtype=bool)
    cells[4, 1, 1] = True
    point = (-.25, 11.75, 1.1)  # clear raw cell in same coarse 5x5 block
    assert check(volume(cells), [point])["admitted"]
    grid = volume(cells, proxy_xy_block_size=5)
    assert check(grid, [point])["status"] == "blocked"
    assert check(grid, [(-.25, 13.75, 1.1)])["admitted"]


def test_supplied_task_object_is_not_ignored_by_static_voxel_gate():
    result = check(volume(), [(-1., 12., 1.), (2., 12., 1.)],
                   obstacle_aabbs=[(0., 11.5, .5, .1, 12.5, 1.5)])
    assert result["reason"] == "route_intersects_supplied_obstacle"


def test_local_aerial_detour_is_bounded_and_reuses_route_gate():
    result = plan_local_aerial_detour(
        volume(), (-1., 12.25, 1.1), (2., 12.25, 1.1),
        half_extents_m=(.05, .05, .05), margin_m=0.,
        obstacle_aabbs=[(0., 12., .5, .1, 12.5, 1.5)], step_m=1.0)
    assert result["admitted"] and result["detour"]
    assert len(result["points"]) == 3


def test_local_aerial_detour_fails_closed_when_no_candidate_is_inside_product():
    result = plan_local_aerial_detour(
        volume(), (-1., 12.25, 1.1), (2., 12.25, 1.1),
        half_extents_m=(.05, .05, .05), margin_m=0.,
        obstacle_aabbs=[(-2., 10., 0., 2., 15., 3.)], step_m=1.0)
    assert not result["admitted"]


def test_runtime_command_inserts_detour_without_losing_pending_waypoints():
    pending = [(1., 12.25, 1.1), (2., 12.25, 1.1)]
    decision = resolve_aerial_command(
        volume(), (-1., 12.25, 1.1), (3., 12.25, 1.1),
        queued_waypoints=pending, half_extents_m=(.05, .05, .05), margin_m=0.,
        obstacle_aabbs=[(0., 12., .5, .1, 12.5, 1.5)])
    assert decision["admitted"]
    assert decision["route"]["local_detour"]
    assert decision["queued_waypoints"][-2:] == [list(point) for point in pending]
    assert decision["command_goal"] == decision["queued_waypoints"][0]


def test_runtime_command_consumes_reached_detour_and_fails_closed_if_trapped():
    clear = resolve_aerial_command(
        volume(), (0., 12.25, 1.1), (2., 12.25, 1.1),
        queued_waypoints=[(0.05, 12.25, 1.1), (1., 12.25, 1.1)],
        half_extents_m=(.05, .05, .05), margin_m=0., waypoint_tolerance_m=.1)
    assert clear["queued_waypoints"] == [[1., 12.25, 1.1]]
    assert clear["command_goal"] == [1., 12.25, 1.1]

    trapped = resolve_aerial_command(
        volume(), (-1., 12.25, 1.1), (2., 12.25, 1.1),
        queued_waypoints=[(1., 12.25, 1.1)],
        half_extents_m=(.05, .05, .05), margin_m=0.,
        obstacle_aabbs=[(-2., 10., 0., 2., 15., 3.)])
    assert not trapped["admitted"]
    assert trapped["route"]["status"] in {"blocked", "unknown"}
    assert trapped["queued_waypoints"] == [[1., 12.25, 1.1]]


def test_loader_records_exact_product_hash_and_validates_schema(tmp_path):
    path = tmp_path / "volume.npz"
    metadata = dict(x0=-2., y0=10., res=.5, vox=.25)
    np.savez(path, voxels=np.zeros((12, 10, 10), dtype=bool), **metadata)
    grid = load_aerial_voxel_map(path)
    report = check(grid, [(0., 12., 1.)])
    assert report["admitted"]
    assert report["product_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert report["product_path"] == str(path.resolve())
    np.savez(path, voxels=np.zeros((12, 10, 10), dtype=float), **metadata)
    assert check(load_aerial_voxel_map(path), [(0., 12., 1.)])["reason"] == "voxel_product_invalid"
    np.savez(path, voxels=np.zeros((12, 10, 10), dtype=bool), **{**metadata, "res": float("nan")})
    assert check(load_aerial_voxel_map(path), [(0., 12., 1.)])["status"] == "unknown"
    np.savez(path, voxels=np.zeros((12, 10, 10), dtype=bool), **{**metadata, "x0": [-2.]})
    assert check(load_aerial_voxel_map(path), [(0., 12., 1.)])["status"] == "unknown"


def test_factory_voxel_sidecar_binds_product_and_source_hash(tmp_path):
    import json

    path = tmp_path / "factory.npz"
    np.savez(path, voxels=np.zeros((4, 8, 8), dtype=bool),
             x0=-2., y0=10., res=.5, vox=.25)
    source = tmp_path / "factory.usd"
    source.write_bytes(b"source-usd")
    sidecar = tmp_path / "factory.meta.json"
    sidecar.write_text(json.dumps({
        "schema": "eai.benchmark.aerial_voxel_product", "version": 1,
        "product_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "source": {"usd_sha256": hashlib.sha256(source.read_bytes()).hexdigest()},
    }))
    grid = load_aerial_voxel_map(
        path, metadata_path=sidecar,
        expected_source_sha256=hashlib.sha256(source.read_bytes()).hexdigest())
    assert isinstance(grid, AerialVoxelMap)
    assert str(sidecar.resolve()) in grid.input_file_hashes
    tampered = json.loads(sidecar.read_text())
    tampered["source"]["usd_sha256"] = "0" * 64
    sidecar.write_text(json.dumps(tampered))
    assert check(load_aerial_voxel_map(path, metadata_path=sidecar),
                 [(0., 12., 1.)])["reason"] == "voxel_product_invalid"


def test_missing_undeclared_and_corrupt_products_never_pass(tmp_path):
    path = tmp_path / "missing.npz"
    assert check(load_aerial_voxel_map(None), [(0., 12., 1.)])["reason"] == "voxel_product_not_declared"
    assert check(load_aerial_voxel_map(path), [(0., 12., 1.)])["reason"] == "voxel_product_unavailable"
    path.write_bytes(b"PK\x03\x04broken zip")
    assert not check(load_aerial_voxel_map(path), [(0., 12., 1.)])["admitted"]


def test_actual_pose_waypoints_and_final_hover_are_all_checked():
    entry = {"scan_goal": [2., 12.25, 1.1], "waypoints": [(-1., 12.25)]}
    route = aerial_route_points(entry, (-1., 12.25, .5))
    assert route == [[-1., 12.25, .5], [-1., 12.25, 1.1], [2., 12.25, 1.1]]
    cells = np.zeros((12, 10, 10), dtype=bool)
    cells[4, 4, 4] = True
    result = check(volume(cells), route)
    assert result["status"] == "blocked"
    assert result["segment_index"] == 1


@pytest.mark.parametrize("points", [[], [(0., 1.)], [(0., 12., float("nan"))]])
def test_invalid_routes_are_rejected(points):
    with pytest.raises(ValueError, match="XYZ point"):
        check(volume(), points)


def test_invalid_envelopes_and_margins_are_rejected():
    for extent in ((0., .1, .1), (-1., .1, .1), (.1, .1, float("nan"))):
        with pytest.raises(ValueError, match="half_extents"):
            volume().check_route([(0., 12., 1.)], half_extents_m=extent)
    with pytest.raises(ValueError, match="margin"):
        volume().check_route([(0., 12., 1.)], half_extents_m=(.1, .1, .1), margin_m=-.1)


def test_ground_collision_wall_extrusion_and_file_identity(tmp_path):
    import yaml
    from PIL import Image

    path = tmp_path / "ground.yaml"
    config = {"image": "ground.png", "origin": [-2., 10., 0.], "resolution": .5,
              "free_thresh": .2, "negate": 0}
    path.write_text(yaml.safe_dump(config))
    pixels = np.full((10, 10), 254, dtype=np.uint8)
    # World-Y cell 2 lives in PNG row height-1-2, not row 2.
    pixels[7, 2] = 0
    Image.fromarray(pixels).save(tmp_path / "ground.png")
    grid = volume().include_ground_collision_proxy(path, height_m=.8)
    # The physical proxy is taller than the original ground-band obstacle.
    assert check(grid, [(-.75, 11.25, .7)])["status"] == "blocked"
    assert check(grid, [(-.75, 11.25, 1.1)])["admitted"]
    assert check(grid, [(-.75, 13.75, .7)])["admitted"]
    assert len(grid.input_file_hashes) == 2
    assert grid.input_file_hashes[str(path)] == hashlib.sha256(path.read_bytes()).hexdigest()
    config["origin"][1] = 9.
    path.write_text(yaml.safe_dump(config))
    with pytest.raises(ValueError, match="frames"):
        volume().include_ground_collision_proxy(path, height_m=.8)


def test_oriented_task_object_bounds_use_current_three_dimensional_pose():
    root_half = np.sqrt(.5)
    box = oriented_box_aabb((1., 2., 3.), (root_half, 0., 0., root_half), (2., 4., 6.))
    assert box == pytest.approx([-1., 1., 0., 3., 3., 6.])


@pytest.mark.parametrize("origin", [-18.95, -4.95, -10., 17.])
@pytest.mark.parametrize("axis", [0, 1])
def test_decimal_grid_contact_never_rounds_to_free(origin, axis):
    # Exercise many faces: cancellation can affect candidate-cell selection
    # and the slab intersection independently at different grid indices.
    for index in range(1, 90):
        cells = np.zeros((10, 100, 100), dtype=bool)
        xyz = [4, 4, 4]
        xyz[axis] = index
        cells[xyz[2], xyz[1], xyz[0]] = True
        grid = AerialVoxelMap(cells, x0=origin, y0=origin, res=.05, vox=.25)
        point = grid.origin + (np.array(xyz) + .5) * grid.cell_size
        point[axis] = origin + (index + 1) * .05 + .01
        result = grid.check_route([point], half_extents_m=(.01, .01, .01), margin_m=0)
        assert result["status"] == "blocked", (origin, axis, index)
        point[axis] += 1e-7  # real clearance must still pass
        assert grid.check_route([point], half_extents_m=(.01, .01, .01), margin_m=0)["admitted"]


@pytest.mark.parametrize("negate", ["0", "1", -1, 2, None, True])
def test_invalid_ground_negate_fails_closed(tmp_path, negate):
    import yaml
    from PIL import Image

    voxels = tmp_path / "volume.npz"
    np.savez(voxels, voxels=np.zeros((12, 10, 10), dtype=bool),
             x0=-2., y0=10., res=.5, vox=.25)
    path = tmp_path / "ground.yaml"
    path.write_text(yaml.safe_dump({"image": "ground.png", "origin": [-2., 10., 0.],
                                   "resolution": .5, "free_thresh": .2, "negate": negate}))
    Image.fromarray(np.zeros((10, 10), dtype=np.uint8)).save(tmp_path / "ground.png")
    result = check(load_aerial_voxel_map(voxels, ground_map_path=path), [(0., 12., .5)])
    assert result["status"] == "unknown"
    assert result["reason"] == "voxel_product_invalid"
    assert not result["admitted"]


@pytest.mark.parametrize("negate", [0, 1])
def test_transparent_and_unknown_ground_pixels_block(tmp_path, negate):
    import yaml
    from PIL import Image

    path = tmp_path / "ground.yaml"
    path.write_text(yaml.safe_dump({"image": "ground.png", "origin": [-2., 10., 0.],
                                   "resolution": .5, "free_thresh": .2, "negate": negate}))
    pixels = np.full((10, 10, 4), 255, dtype=np.uint8)
    pixels[:, :, :3] = 0 if negate else 255
    pixels[7, 2, 3] = 0
    pixels[7, 3, :3] = 128
    Image.fromarray(pixels).save(tmp_path / "ground.png")
    grid = volume().include_ground_collision_proxy(path, height_m=.8)
    assert check(grid, [(-.75, 11.25, .7)])["status"] == "blocked"
    assert check(grid, [(-.25, 11.25, .7)])["status"] == "blocked"
    assert check(grid, [(-.75, 13.75, .7)])["admitted"]


@pytest.mark.parametrize("altitude", [.7, 2.25])
def test_navigation_without_scan_uses_current_world_altitude(altitude):
    route = aerial_route_points({"waypoints": [(1., 12.)]}, (0., 12., altitude))
    assert route == [[0., 12., altitude], [1., 12., max(1.5, altitude)]]


def test_actual_shortcut_before_climb_finishes_must_be_checked_again():
    cells = np.zeros((12, 10, 10), dtype=bool)
    cells[4, 4, 4] = True
    grid = volume(cells)
    planned = [(-1., 12.25, .6), (-1., 12.25, 2.), (2., 12.25, 2.)]
    assert check(grid, planned)["admitted"]
    assert check(grid, [planned[0], planned[-1]])["status"] == "blocked"


@pytest.mark.parametrize("boxes", [[(0, 0, 0, -1, 1, 1)], [(0, 0, 0, 1, 1, float('nan'))], [(0, 1)]])
def test_malformed_additional_obstacles_are_never_ignored(boxes):
    with pytest.raises(ValueError, match="obstacle_aabbs"):
        check(volume(), [(0., 12., 1.)], obstacle_aabbs=boxes)
