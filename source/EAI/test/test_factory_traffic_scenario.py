"""Offline validation for the first maintained Factory traffic task."""

from pathlib import Path

from EAI.scenario_spec import load_scenario
from EAI.scenario_spec.geometry import provider_map, validate_connectivity, validate_geometry
from demo.benchmark.runtime import build_plan, planning_template


def test_factory_traffic_scenario_uses_two_control_modes_and_shared_aisle():
    root = Path(__file__).resolve().parents[3]
    scenario = load_scenario(root / "demo/factory_benchmark/scenarios/traffic_01.json")
    occupancy = provider_map(scene_key="factory")
    validate_geometry(scenario, occupancy)
    validate_connectivity(scenario, occupancy)
    plan = build_plan(scenario, occupancy, planning_template("factory", occupancy))
    assert {item["agent"] for item in plan} == {"carter_1", "simple_car_1"}
    assert {resource for item in plan for resource in item["resources"]} >= {"midband", "dock_north", "yard_south"}
    assert {robot["type"] for robot in scenario["robots"]} == {"carter", "simple_car"}
