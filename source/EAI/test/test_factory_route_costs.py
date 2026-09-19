"""Route estimates must use the map and stay within their supported origin model."""

from copy import deepcopy
from types import SimpleNamespace

import numpy as np
import pytest

from demo.benchmark.allocation import compare_allocators
from demo.factory_benchmark.route_costs import estimate_route_costs


def _map():
    return SimpleNamespace(
        free=np.ones((24, 24), dtype=bool), width=24, height=24, resolution=0.5,
        local=lambda x_m, y_m: (x_m, y_m), world=lambda x_m, y_m: (x_m, y_m), sha256="a" * 64,
    )


def _problem():
    data = {
        "scene_key": "factory", "robots": [
            {"id": name, "type": "simple_car", "attachments": [],
             "spawn_pose": {"position": position}}
            for name, position in (("west_robot", [1.25, 1.25, 0.34]), ("east_robot", [9.25, 1.25, 0.34]))
        ],
        "composition": {"entities": [], "targets": [
            {"id": name, "position": position, "radius_m": 0.5}
            for name, position in (("west", [1.25, 2.25, 0.0]), ("east", [9.25, 2.25, 0.0]))
        ]},
        "phase_graph": {"nodes": [
            {"id": f"{site}_arrive", "skill_key": "navigate", "target_ref": site,
             "agents": None, "prerequisites": []} for site in ("west", "east")
        ]},
    }
    requirements = [{"id": node["id"], "domain": "ground"} for node in data["phase_graph"]["nodes"]]
    return data, requirements


def test_map_distance_changes_unit_cost_assignment_and_is_shared_between_methods():
    data, requirements = _problem()
    before = deepcopy(data)
    costs = estimate_route_costs(data, requirements, occupancy=_map())
    assert data == before
    fleet = [{"id": robot["id"], "robot_key": robot["type"]} for robot in data["robots"]]
    profiles = {"simple_car": {"hard_constraints": {"domain": "ground"}}}
    fixed = {"west_arrive": ["east_robot"], "east_arrive": ["west_robot"]}
    comparison = compare_allocators(requirements, fleet, profiles, fixed,
                                    estimated_costs=costs["estimated_costs"],
                                    unavailable_assignments=costs["unavailable_assignments"])
    for method in ("greedy", "optimized"):
        result = comparison["methods"][method]
        assert {item["requirement_id"]: item["agents"] for item in result["assignments"]} == {
            "west_arrive": ["west_robot"], "east_arrive": ["east_robot"]}
        assert result["objective"]["cost"] == pytest.approx(2.0)
    assert comparison["methods"]["fixed"]["objective"]["cost"] > 16.0
    assert comparison["methods"]["optimized"]["optimality_proven"]


def test_static_wall_changes_distance_and_blocked_endpoints_are_not_snapped():
    data, requirements = _problem()
    occupancy = _map()
    occupancy.free[8:, 10] = False
    result = estimate_route_costs(data, requirements, occupancy=occupancy)
    assert result["estimated_costs"]["east_arrive"]["west_robot"] > 12.0
    occupancy.free[:, 10] = False
    result = estimate_route_costs(data, requirements, occupancy=occupancy)
    assert "west_robot" not in result["estimated_costs"]["east_arrive"]
    assert "route:no_passable" in result["unavailable_assignments"]["east_arrive"]["west_robot"]
    fleet = [{"id": robot["id"], "robot_key": robot["type"]} for robot in data["robots"]]
    comparison = compare_allocators(
        requirements, fleet, {"simple_car": {"hard_constraints": {"domain": "ground"}}},
        {"west_arrive": ["east_robot"], "east_arrive": ["west_robot"]},
        estimated_costs=result["estimated_costs"], unavailable_assignments=result["unavailable_assignments"])
    assert comparison["methods"]["fixed"]["status"] == "infeasible"
    assert comparison["methods"]["optimized"]["status"] == "feasible"
    data["composition"]["targets"][1]["position"][:2] = [5.25, 2.25]
    result = estimate_route_costs(data, requirements, occupancy=occupancy)
    assert len(result["unavailable_assignments"]["east_arrive"]) == 2


def test_wait_adds_no_distance_and_ambiguous_intermediate_origins_are_rejected():
    data, requirements = _problem()
    data["phase_graph"]["nodes"].append({"id": "watch", "skill_key": "wait", "target_ref": "west",
                                         "agents": None, "prerequisites": ["west_arrive"]})
    requirements.append({"id": "watch", "same_team_as": "west_arrive", "prerequisites": ["west_arrive"]})
    costs = estimate_route_costs(data, requirements, occupancy=_map())
    assert costs["estimated_costs"]["watch"] == {"west_robot": 0.0, "east_robot": 0.0}
    data["phase_graph"]["nodes"][-1]["target_ref"] = "east"
    with pytest.raises(ValueError, match="same target"):
        estimate_route_costs(data, requirements, occupancy=_map())
    data["phase_graph"]["nodes"][-1]["target_ref"] = "west"
    data["phase_graph"]["nodes"][1]["prerequisites"] = ["west_arrive"]
    with pytest.raises(ValueError, match="assignment-dependent"):
        estimate_route_costs(data, requirements, occupancy=_map())


def test_real_factory_routes_preserve_close_station_assignments():
    from pathlib import Path
    from EAI.scenario_spec import load_scenario
    from demo.factory_benchmark.tasks import factory_task_requirements

    data = load_scenario(Path(__file__).resolve().parents[3] / "demo/factory_benchmark/scenarios/coverage_01.json")
    result = estimate_route_costs(data, factory_task_requirements("F-COV-01"))
    for node in data["phase_graph"]["nodes"]:
        if node["skill_key"] == "navigate":
            costs = result["estimated_costs"][node["id"]]
            assert min(costs, key=costs.get) == node["preferred_agent"]
    assert all(not rejected for rejected in result["unavailable_assignments"].values())
