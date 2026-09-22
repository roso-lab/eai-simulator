"""Tests for demo.benchmark.scene_deployment indoor spawn deployment gates.

Synthetic-map cases build a tiny ROS map_server YAML+PNG pair under an
isolated ``EAI_USD_ROOT`` so the shared loader (``FactoryMap``) runs unmodified;
real-map cases re-validate the frozen ``tmp/scene_v2_anchors`` anchor sets
against the actual scene occupancy maps and skip with an explicit reason when
those runtime map artifacts are absent.
"""

from pathlib import Path

import numpy as np
import pytest
import yaml
from PIL import Image

from demo.benchmark.scene_deployment import (
    DEFAULT_CLEARANCE_M,
    DEFAULT_SPAWN_Z_BY_TYPE,
    ROUTE_CORRIDOR_MIN_CLEARANCE_M,
    SCENE_DEPLOYMENT_KEYS,
    SPAWN_Z_BY_SCENE_AND_TYPE,
    build_robot_spawn_positions,
    frozen_anchor_path,
    load_scene_map,
    provider_scene_key,
    route_corridor_clearance,
    spawn_anchor_rotation,
    validate_scene_spawn_points,
)
from demo.benchmark.goal_driven_tasks import build_goal_task_spec


REPO_ROOT = Path(__file__).resolve().parents[3]
# Provider occupancy-map yaml paths for the three indoor scenes.
REAL_MAP_YAML = {
    "warehouse": REPO_ROOT / "usd/scene/warehouse_nvd/ground_map_0.6m.yaml",
    "house": REPO_ROOT / "usd/scene/modern_house_nvd/ground_map_0.6m.yaml",
    "factory": REPO_ROOT / "usd/scene/factory/factory_map.yaml",
}
# The legacy blocking corridor called out by the warehouse anchor freeze.
WAREHOUSE_CRATE_AISLE_BLOCKERS = [(-8.0, -11.75), (-9.0, -11.75)]

_GRID_WIDTH, _GRID_HEIGHT, _RESOLUTION = 40, 30, 0.1
_ORIGIN = (-1.0, -2.0, 0.0)


def _synthetic_grid() -> np.ndarray:
    """Image-row grid: white free floor, black wall, gray unknown patch."""
    grid = np.full((_GRID_HEIGHT, _GRID_WIDTH), 255, dtype=np.uint8)
    grid[:, 19:21] = 0        # wall at local x in [1.9, 2.1) -> world x in [0.9, 1.1)
    grid[0:4, 30:34] = 128    # unknown at local x [3.0, 3.4), local y [2.6, 3.0)
    return grid


@pytest.fixture
def synthetic_map_root(tmp_path, monkeypatch):
    """Isolated EAI_USD_ROOT carrying one synthetic map per indoor scene."""
    grid = _synthetic_grid()
    for alias, provider in SCENE_DEPLOYMENT_KEYS.items():
        name = "factory_map" if provider == "factory" else "ground_map_0.6m"
        scene_dir = tmp_path / "scene" / provider
        scene_dir.mkdir(parents=True)
        Image.fromarray(grid).save(scene_dir / f"{name}.png")
        (scene_dir / f"{name}.yaml").write_text(yaml.safe_dump({
            "image": f"{name}.png",
            "resolution": _RESOLUTION,
            "origin": list(_ORIGIN),
            "negate": 0,
            "occupied_thresh": 0.65,
            "free_thresh": 0.196,
        }), encoding="utf-8")
    monkeypatch.setenv("EAI_USD_ROOT", str(tmp_path))
    return tmp_path


# ---------------------------------------------------------------- load/validate

@pytest.mark.parametrize("alias", sorted(SCENE_DEPLOYMENT_KEYS))
def test_load_scene_map_reuses_shared_loader(synthetic_map_root, alias):
    occupancy = load_scene_map(alias)
    assert occupancy.resolution == _RESOLUTION
    assert tuple(occupancy.origin) == _ORIGIN
    assert occupancy.free.shape == (_GRID_HEIGHT, _GRID_WIDTH)
    # Provider keys resolve to the same artifact as the deployment alias.
    assert load_scene_map(provider_scene_key(alias)).sha256 == occupancy.sha256


def test_load_scene_map_rejects_city_and_unknown(synthetic_map_root):
    with pytest.raises(ValueError, match="validate_city_spawn_points"):
        load_scene_map("city")
    with pytest.raises(ValueError, match="unsupported scene deployment key"):
        load_scene_map("street")


def test_validate_accepts_free_point_with_clearance(synthetic_map_root):
    # (-0.15, 0.05) is the exact center of cell (8, 20); the wall face center
    # (1.95, 2.05) is 1.10 m away on the same row.
    result = validate_scene_spawn_points("warehouse", {"free_far": [-0.15, 0.05]})
    assert result["validated"] is True
    assert result["accepted"] == ["free_far"]
    assert result["clearance_m"] == DEFAULT_CLEARANCE_M
    assert result["resolution_m"] == _RESOLUTION
    point = result["points"][0]
    assert point["in_free_cell"] is True
    # Conservative free distance: center distance minus the half cell diagonal.
    assert point["free_distance_m"] == pytest.approx(1.10 - _RESOLUTION * 2 ** 0.5 / 2, abs=1e-3)
    assert point["cell"] == [8, 20]
    assert point["nearest_nonfree_cell"] == [19, 20]


def test_validate_rejects_occupied_unknown_and_offgrid_points(synthetic_map_root):
    with pytest.raises(ValueError, match="inside an occupied/unknown cell.*cell col=20"):
        validate_scene_spawn_points("warehouse", [[1.0, 0.0]])
    with pytest.raises(ValueError, match="inside an occupied/unknown cell.*cell col=32"):
        validate_scene_spawn_points("warehouse", [[2.2, 0.8]])  # gray unknown patch
    with pytest.raises(ValueError, match="outside the occupancy map grid"):
        validate_scene_spawn_points("warehouse", [[5.0, 0.0]])
    with pytest.raises(ValueError, match="outside the occupancy map grid"):
        validate_scene_spawn_points("warehouse", [[0.0, 5.0]])


def test_validate_rejects_insufficient_clearance_and_honors_override(synthetic_map_root):
    # Free cell whose center is 0.20 m from the wall cell-center column:
    # blocked at the 0.6 m default clearance.
    with pytest.raises(ValueError, match="free distance 0.129 m is below the required 0.6"):
        validate_scene_spawn_points("warehouse", {"too_close": [0.75, 0.05]})
    # The same point passes a clearance requirement it actually satisfies.
    relaxed = validate_scene_spawn_points("warehouse", {"too_close": [0.75, 0.05]},
                                          clearance_m=0.12)
    assert relaxed["points"][0]["free_distance_m"] == pytest.approx(
        0.20 - _RESOLUTION * 2 ** 0.5 / 2, abs=1e-3)


def test_validate_reports_every_failing_point_and_requires_input(synthetic_map_root):
    with pytest.raises(ValueError) as excinfo:
        validate_scene_spawn_points("factory", [[1.0, 0.0], [0.7, 0.0]])
    message = str(excinfo.value)
    assert "point[0]" in message and "point[1]" in message
    with pytest.raises(ValueError, match="at least one point"):
        validate_scene_spawn_points("factory", [])
    with pytest.raises(ValueError, match="not a two-coordinate point|2D/3D"):
        validate_scene_spawn_points("factory", {"bad": [0.0]})
    with pytest.raises(ValueError, match="finite"):
        validate_scene_spawn_points("factory", {"nan": [float("nan"), 0.0]})


# ------------------------------------------------------------------- rotation

_ANCHORS = [(0.0, 0.0), (2.0, 0.0), (4.0, 0.0)]


def test_spawn_anchor_rotation_is_deterministic_and_wraps():
    assert spawn_anchor_rotation(_ANCHORS, 0) == (0.0, 0.0)
    assert spawn_anchor_rotation(_ANCHORS, 1) == (2.0, 0.0)
    assert spawn_anchor_rotation(_ANCHORS, 3) == (0.0, 0.0)   # wraps
    assert spawn_anchor_rotation(_ANCHORS, 7) == (2.0, 0.0)   # 7 mod 3 = 1
    # Cross-instance exchange: distinct frozen instances get distinct anchors.
    assigned = [spawn_anchor_rotation(_ANCHORS, index) for index in range(3)]
    assert len(set(assigned)) == 3
    for index in (1, 4, 9):
        assert spawn_anchor_rotation(_ANCHORS, index) == spawn_anchor_rotation(_ANCHORS, index)


def test_spawn_anchor_rotation_accepts_frozen_anchor_rows():
    rows = [{"id": "a", "xy_m": [1.0, 2.0]}, {"id": "b", "xy_m": [3.0, 4.0]}]
    assert spawn_anchor_rotation(rows, 1) == (3.0, 4.0)


def test_spawn_anchor_rotation_rejects_bad_inputs():
    with pytest.raises(ValueError, match="at least one anchor"):
        spawn_anchor_rotation([], 0)
    with pytest.raises(ValueError, match="non-negative int"):
        spawn_anchor_rotation(_ANCHORS, -1)
    with pytest.raises(ValueError, match="non-negative int"):
        spawn_anchor_rotation(_ANCHORS, True)
    with pytest.raises(ValueError, match="non-negative int"):
        spawn_anchor_rotation(_ANCHORS, 1.0)
    with pytest.raises(ValueError, match="2D/3D"):
        spawn_anchor_rotation([[1.0]], 0)
    with pytest.raises(ValueError, match="finite"):
        spawn_anchor_rotation([(float("inf"), 0.0)], 0)


# ------------------------------------------------------- spawn position format

_POOL = [
    {"id": "robot_aerial_1", "type": "iris", "mobility": "aerial"},
    {"id": "robot_ground_1", "type": "m20", "mobility": "ground"},
    {"id": "robot_ground_2", "type": "carter", "mobility": "ground"},
    {"id": "robot_ground_3", "type": "m20", "mobility": "ground"},
]


def test_build_robot_spawn_positions_format_and_rotation():
    anchors = [[float(index), -1.0] for index in range(8)]
    positions = build_robot_spawn_positions(_POOL, anchors)
    assert list(positions) == [row["id"] for row in _POOL]
    for agent_id, position in positions.items():
        assert isinstance(agent_id, str) and agent_id
        assert len(position) == 3
        assert all(isinstance(value, float) for value in position)
        assert all(value == value and abs(value) != float("inf") for value in position)
    # Frozen pool order drives the rotation: instance i takes anchors[i].
    assert positions["robot_ground_1"] == [1.0, -1.0, DEFAULT_SPAWN_Z_BY_TYPE["m20"]]
    assert positions["robot_ground_2"] == [2.0, -1.0, DEFAULT_SPAWN_Z_BY_TYPE["carter"]]
    assert positions["robot_aerial_1"] == [0.0, -1.0, DEFAULT_SPAWN_Z_BY_TYPE["iris"]]
    # The mapping is injective in XY across the whole pool.
    xy = [tuple(position[:2]) for position in positions.values()]
    assert len(set(xy)) == len(xy)


def test_build_robot_spawn_positions_scene_z_table_and_override():
    anchors = [[0.0, 0.0]] * 8
    house = build_robot_spawn_positions(_POOL, anchors, scene_key="house")
    assert house["robot_ground_1"][2] == SPAWN_Z_BY_SCENE_AND_TYPE["house"]["m20"]
    assert house["robot_ground_2"][2] == SPAWN_Z_BY_SCENE_AND_TYPE["house"]["carter"]
    assert house["robot_aerial_1"][2] == SPAWN_Z_BY_SCENE_AND_TYPE["house"]["iris"]
    override = build_robot_spawn_positions(_POOL, anchors,
                                           spawn_z_by_type={"iris": 2.0, "m20": 0.4, "carter": 0.1})
    assert override["robot_ground_3"][2] == 0.4
    with pytest.raises(ValueError, match="without a reference spawn z"):
        build_robot_spawn_positions([{"id": "x", "type": "go2"}], anchors)


def test_build_robot_spawn_positions_rejects_oversized_pool_and_bad_rows():
    short_anchors = [[0.0, 0.0]] * 3
    with pytest.raises(ValueError, match="exceeds the 3 anchors"):
        build_robot_spawn_positions(_POOL, short_anchors)
    with pytest.raises(ValueError, match="duplicate pool agent id"):
        build_robot_spawn_positions([{"id": "a", "type": "iris"},
                                     {"id": "a", "type": "m20"}], [[0.0, 0.0]] * 2)
    with pytest.raises(ValueError, match="'id' and 'type'"):
        build_robot_spawn_positions([{"id": "a"}], [[0.0, 0.0]])
    with pytest.raises(ValueError, match="at least one robot"):
        build_robot_spawn_positions([], [[0.0, 0.0]])


# ------------------------------------------------------------ corridor clearance

def test_route_corridor_clearance_flags_blocking_and_clean_points():
    blocking = route_corridor_clearance("warehouse", [-8.0, -11.75])
    assert blocking["outside_all_corridors"] is False
    assert blocking["min_corridor_clearance_m"] < 0.0
    assert "rack_a_to_consolidation" in blocking["nearest_corridor_id"]
    clean = route_corridor_clearance("warehouse", [-1.5, -11.75])
    assert clean["outside_all_corridors"] is True
    assert clean["min_corridor_clearance_m"] > ROUTE_CORRIDOR_MIN_CLEARANCE_M
    # Factory endpoint homes stay usable: the interior shrinks away from ends.
    assert route_corridor_clearance("factory", [-1.95, 3.4])["outside_all_corridors"] is True
    with pytest.raises(ValueError, match="validate_city_spawn_points"):
        route_corridor_clearance("city", [0.0, 0.0])


# ------------------------------------------------- real maps + frozen anchors

@pytest.fixture
def real_map_env(monkeypatch):
    """Keep the real usd root resolution, ignoring any local EAI_USD_ROOT."""
    monkeypatch.delenv("EAI_USD_ROOT", raising=False)


@pytest.mark.parametrize("scene", sorted(REAL_MAP_YAML))
def test_frozen_anchors_pass_real_scene_map(scene, real_map_env):
    map_yaml = REAL_MAP_YAML[scene]
    if not map_yaml.is_file():
        pytest.skip(f"real scene occupancy map not present locally: {map_yaml}")
    freeze_path = frozen_anchor_path(scene)
    if not freeze_path.is_file():
        pytest.skip(f"frozen anchor artifact not present (regenerate via scene_deployment): {freeze_path}")
    import json

    payload = json.loads(freeze_path.read_text(encoding="utf-8"))
    anchors = payload["anchors"]
    assert 7 <= len(anchors) <= 8
    assert len({row["id"] for row in anchors}) == len(anchors)

    result = validate_scene_spawn_points(scene, {row["id"]: row["xy_m"] for row in anchors})
    assert result["point_count"] == len(anchors)
    # The freeze must be bound to the exact current map artifact.
    assert result["map_sha256"] == payload["map_source"]["map_sha256"]
    # Every anchor also clears every declared ground-route corridor band.
    for row in anchors:
        corridor = route_corridor_clearance(scene, row["xy_m"])
        assert corridor["min_corridor_clearance_m"] > ROUTE_CORRIDOR_MIN_CLEARANCE_M, row["id"]

    if scene == "warehouse":
        frozen_xy = {tuple(row["xy_m"]) for row in anchors}
        for blocker in WAREHOUSE_CRATE_AISLE_BLOCKERS:
            assert blocker not in frozen_xy
            assert route_corridor_clearance(scene, list(blocker))["outside_all_corridors"] is False


@pytest.mark.parametrize("scene", sorted(REAL_MAP_YAML))
def test_frozen_pool_spawns_valid_on_real_map(scene, real_map_env):
    map_yaml = REAL_MAP_YAML[scene]
    if not map_yaml.is_file():
        pytest.skip(f"real scene occupancy map not present locally: {map_yaml}")
    freeze_path = frozen_anchor_path(scene)
    if not freeze_path.is_file():
        pytest.skip(f"frozen anchor artifact not present (regenerate via scene_deployment): {freeze_path}")
    import json

    anchors = json.loads(freeze_path.read_text(encoding="utf-8"))["anchors"]
    public = build_goal_task_spec(provider_scene_key(scene))
    positions = build_robot_spawn_positions(public["candidate_robot_pool"], anchors,
                                            scene_key=scene)
    assert set(positions) == {row["id"] for row in public["candidate_robot_pool"]}
    # Full pass: every frozen pool member spawns on a free cell with clearance.
    result = validate_scene_spawn_points(scene, positions)
    assert result["point_count"] == len(positions)
