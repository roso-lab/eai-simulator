from copy import deepcopy
import json
from pathlib import Path

import pytest

from demo.benchmark.lifecycle_plan import (
    build_maintained_lifecycle_plan, validate_lifecycle_plan,
)
from EAI.scenario_spec.geometry import provider_map


def _scene(key):
    path = {
        "warehouse_nvd": "demo/warehouse_benchmark/scenarios/business_logistics_01.json",
        "modern_house_nvd": "demo/modern_house_benchmark/scenarios/home_recovery_01.json",
    }[key]
    return json.loads(Path(path).read_text())


def _with_reserve(data, robot_type="carter"):
    result = deepcopy(data)
    reserve = deepcopy(next(robot for robot in result["robots"] if robot["type"] == robot_type))
    reserve["id"] = f"{robot_type}_reserve_1"
    result["robots"].append(reserve)
    result["runtime_lifecycle"] = {
        "initial_agent_ids": [robot["id"] for robot in data["robots"]],
        "preloaded_reserve_agent_ids": [reserve["id"]],
    }
    return result, reserve["id"]


@pytest.mark.parametrize("scene,node,parking", [
    ("warehouse_nvd", "rack_overview_scan", (-8.425, -9.975, 0.2)),
    ("modern_house_nvd", "room_entry_scan", (19.075, 5.025, 0.2)),
])
def test_nvd_private_lifecycle_plan_has_map_bound_join_route(scene, node, parking):
    data, reserve_id = _with_reserve(_scene(scene))
    original = deepcopy(data)
    result = validate_lifecycle_plan(
        data, provider_map(scene_key=scene), reserve_agent_id=reserve_id,
        parking_pose=parking, join_node_id=node,
        exit_agent_id="carter_1", exit_time_s=80.,
        replacement_agent_id=reserve_id,
        capability_change_agent_id="m20_1", capability_change_time_s=120.,
    )
    assert result["route"]["validated"]
    assert result["physical_execution_validated"] is False
    assert result["visibility"] == "author_evaluator_only"
    assert data == original


def test_factory_plan_requires_a_preloaded_reserve_and_preserves_public_input():
    source = json.loads(Path("demo/factory_benchmark/scenarios/coverage_01.json").read_text())
    reserve = deepcopy(source["robots"][0])
    reserve["id"] = "simple_car_reserve_1"
    source["robots"].append(reserve)
    source["runtime_lifecycle"] = {
        "initial_agent_ids": [robot["id"] for robot in source["robots"][:-1]],
        "preloaded_reserve_agent_ids": [reserve["id"]],
    }
    original = deepcopy(source)
    result = validate_lifecycle_plan(
        source, provider_map(scene_key="factory"),
        reserve_agent_id=reserve["id"], parking_pose=(-3.45, -8.6, 0.34),
        join_node_id="dock_arrive",
    )
    assert result["route"]["validated"]
    assert source == original


def test_lifecycle_plan_rejects_unsafe_event_order_and_unknown_reserve():
    data, reserve_id = _with_reserve(_scene("warehouse_nvd"))
    occupancy = provider_map(scene_key="warehouse_nvd")
    with pytest.raises(ValueError, match="preloaded_reserve"):
        validate_lifecycle_plan(
            data, occupancy, reserve_agent_id="m20_1",
            parking_pose=(-8.425, -9.975, 0.2), join_node_id="rack_overview_scan")
    with pytest.raises(ValueError, match="after member_join"):
        validate_lifecycle_plan(
            data, occupancy, reserve_agent_id=reserve_id,
            parking_pose=(-8.425, -9.975, 0.2), join_node_id="rack_overview_scan",
            capability_change_agent_id="m20_1", capability_change_time_s=10.)


@pytest.mark.parametrize("scene", ["factory", "warehouse_nvd", "modern_house_nvd"])
def test_maintained_private_plan_compiles_from_checked_in_scene(scene):
    result = build_maintained_lifecycle_plan(scene)
    assert result["scene_key"] == scene
    assert result["route"]["validated"] is True
    assert result["physical_execution_validated"] is False
    assert result["map_sha256"]
