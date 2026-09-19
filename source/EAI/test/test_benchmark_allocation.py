"""Pure tests for benchmark allocation and runtime-plan compilation."""

from copy import deepcopy
import pytest

from demo.benchmark.allocation import (
    compare_allocators, compile_assignment_plan, fixed_allocate, greedy_allocate, optimized_allocate,
    reallocation_certificate,
)
from tools.scenario_generation.capability_profile import RADAR_AXES


def _profile(domain="ground"):
    return {
        "hard_constraints": {
            "domain": domain,
            "supports_navigation": True,
            "supports_aerial_scan": domain == "aerial",
            "sensor_capabilities": {"camera": True, "lidar": True},
            "attachments": [],
            "passing_evidence_tier": "E6",
        },
        "radar": [{"axis": axis, "status": "measured", "normalized": 1.0}
                  for axis in RADAR_AXES],
    }


def _scenario():
    return {
        "robots": [{"id": "carter_1"}],
        "composition": {"entities": [], "targets": [{"id": "destination"}]},
        "phase_graph": {"nodes": [{"id": "approach", "skill_key": "navigate",
                                     "preferred_agent": None, "agents": None,
                                     "target_ref": "destination", "object_ref": None,
                                     "prerequisites": [], "timeout_s": 60.0,
                                     "duration_s": None, "resources": []}]},
    }


def test_greedy_allocate_canonicalizes_shared_resources():
    requirements = [{"id": "route", "domain": "ground", "navigation": True,
                     "shared_resources": ["midband"]}]
    result = greedy_allocate(
        requirements,
        [{"id": "carter_1", "robot_key": "carter", "profile_key": "carter"}],
        {"carter": _profile()},
    )
    assert result["status"] == "feasible"
    assert result["requirements"] == [{"id": "route", "domain": "ground",
                                        "navigation": True, "resources": ["midband"]}]


def test_compile_assignment_plan_binds_phase_graph_without_changing_task_requirements():
    scenario = _scenario()
    plan = {
        "schema": "eai.assignment_plan",
        "version": 1,
        "status": "feasible",
        "requirements": [{"id": "approach", "resources": ["midband"]}],
        "assignments": [{"requirement_id": "approach", "agents": ["carter_1"]}],
    }
    compiled = compile_assignment_plan(scenario, plan)
    node = compiled["phase_graph"]["nodes"][0]
    assert node["preferred_agent"] == "carter_1"
    assert node["agents"] is None
    assert node["resources"] == ["midband"]
    assert scenario["phase_graph"]["nodes"][0]["resources"] == []


def test_compile_assignment_plan_rejects_incomplete_and_invalid_joint_bindings():
    scenario = _scenario()
    missing = {"schema": "eai.assignment_plan", "version": 1,
               "status": "feasible", "assignments": []}
    with pytest.raises(ValueError, match="no assignment"):
        compile_assignment_plan(scenario, missing)

    joint = deepcopy(scenario)
    joint["phase_graph"]["nodes"][0]["skill_key"] = "joint_transport"
    joint["phase_graph"]["nodes"][0]["object_ref"] = None
    invalid = {
        "schema": "eai.assignment_plan",
        "version": 1,
        "status": "feasible",
        "requirements": [{"id": "approach"}],
        "assignments": [{"requirement_id": "approach", "agents": ["carter_1"]}],
    }
    with pytest.raises(ValueError, match="joint_transport"):
        compile_assignment_plan(joint, invalid)


def test_allocation_rejects_configuration_below_required_evidence_tier():
    requirements = [{"id": "route", "domain": "ground", "navigation": True,
                     "required_evidence_tier": "E4"}]
    result = greedy_allocate(
        requirements,
        [{"id": "carter_1", "robot_key": "carter", "profile_key": "carter"}],
        {"carter": {**_profile(), "hard_constraints": {
            **_profile()["hard_constraints"], "passing_evidence_tier": "E3"
        }}},
    )
    assert result["status"] == "infeasible"
    assert "evidence:E3_below_E4" in result["decisions"][0]["rejected_robots"][0]["reasons"]


def test_factory_coverage_reuses_three_robots_for_six_sequential_phases():
    from pathlib import Path
    from EAI.scenario_spec import load_scenario
    from demo.factory_benchmark.tasks import factory_task_requirements, validate_factory_scenario

    root = Path(__file__).resolve().parents[3]
    scenario = load_scenario(root / "demo/factory_benchmark/scenarios/coverage_01.json")
    requirements = factory_task_requirements("F-COV-01")
    fleet = [{"id": robot["id"], "robot_key": "simple_car"} for robot in scenario["robots"]]
    profiles = {"simple_car": _profile()}
    from tools.scenario_generation.record_configuration_microtests import task_scope
    nodes = {node["id"]: node for node in scenario["phase_graph"]["nodes"]}
    profiles["simple_car"]["identity"] = {"configuration_id": "fixture-config"}
    profiles["simple_car"]["protocol_checks"] = []
    for requirement in requirements:
        for protocol in requirement["required_protocols"]:
            protocol["scope"] = task_scope(scenario, nodes[requirement["id"]])
            profiles["simple_car"]["protocol_checks"].append({
                **protocol, "status": "passed", "evidence_tier": "E6", "configuration_id": "fixture-config"})
    fixed = {node["id"]: [node["preferred_agent"]] for node in scenario["phase_graph"]["nodes"]}
    comparison = compare_allocators(requirements, fleet, profiles, fixed)
    for method in comparison["methods"].values():
        assert method["status"] == "feasible"
        assert len(method["assignments"]) == 6
        assert method["objective"]["distinct_robot_count"] == 3
        compiled = compile_assignment_plan(scenario, method)
        validate_factory_scenario(compiled, "F-COV-01")
        assignments = {item["requirement_id"]: item["agents"] for item in method["assignments"]}
        for site in ("storage", "assembly", "dock"):
            assert assignments[f"{site}_arrive"] == assignments[f"{site}_watch"]
    assert comparison["methods"]["optimized"]["optimality_proven"]
    assert not comparison["methods"]["greedy"]["optimality_proven"]
    certificate = reallocation_certificate(requirements, fleet, profiles)
    assert certificate["status"] == "feasible"
    assert certificate["minimum_distinct_robot_count"] == 3
    assert not certificate["single_agent_feasible"]
    assert certificate["homogeneous_alternative_feasible"]
    assert not certificate["heterogeneity_necessary"]


def test_reallocation_certificate_allows_sequential_team_reuse():
    requirements = [{"id": "arrive", "domain": "ground"},
                    {"id": "watch", "domain": "ground", "prerequisites": ["arrive"],
                     "same_team_as": "arrive"}]
    fleet = [{"id": "one", "robot_key": "simple_car"}, {"id": "two", "robot_key": "carter"}]
    certificate = reallocation_certificate(requirements, fleet, {"simple_car": _profile(), "carter": _profile()})
    assert certificate["minimum_distinct_robot_count"] == 1
    assert certificate["single_agent_feasible"]
    assert not certificate["heterogeneity_necessary"]


def test_reallocation_certificate_does_not_call_budget_exhaustion_infeasible():
    requirements = [{"id": f"route_{index}", "domain": "ground"} for index in range(3)]
    fleet = [{"id": "one", "robot_key": "simple_car"}, {"id": "two", "robot_key": "carter"}]
    certificate = reallocation_certificate(requirements, fleet, {"simple_car": _profile(), "carter": _profile()},
                                            max_candidate_evaluations=1)
    assert certificate["status"] == "unknown"
    assert certificate["budget_exhausted"]
    assert not certificate["infeasibility_proven"]


def _scarce_sensor_problem():
    requirements = [{"id": "flexible", "domain": "ground"},
                    {"id": "inspection", "domain": "ground", "sensors": ["camera"]}]
    fleet = [{"id": "a_specialist", "robot_key": "specialist"},
             {"id": "b_general", "robot_key": "general"}]
    profiles = {"specialist": _profile(), "general": _profile()}
    profiles["general"]["hard_constraints"]["sensor_capabilities"] = {}
    return requirements, fleet, profiles


def test_optimizer_reserves_scarce_sensor_that_greedy_uses_too_early():
    requirements, fleet, profiles = _scarce_sensor_problem()
    comparison = compare_allocators(
        requirements, fleet, profiles,
        {"flexible": ["b_general"], "inspection": ["a_specialist"]}, max_candidate_evaluations=30,
    )
    methods = comparison["methods"]
    assert methods["fixed"]["status"] == methods["optimized"]["status"] == "feasible"
    assert methods["greedy"]["status"] == "infeasible"
    assert not methods["greedy"]["infeasibility_proven"]
    assert methods["optimized"]["optimality_proven"]
    assert all(item["candidate_evaluation_budget"] == 30 for item in methods.values())
    assert len(comparison["input_sha256"]) == 64


def test_configuration_smoke_cannot_admit_an_unmeasured_site_or_other_configuration():
    from demo.benchmark.allocation import eligible_fleet

    profile = {**_profile(), "identity": {"configuration_id": "camera-config"},
               "protocol_checks": [{"protocol_id": "factory_navigation_visit_v2", "status": "passed",
                                    "configuration_id": "camera-config", "evidence_tier": "E6",
                                    "scope": {"scene_key": "factory", "target": "nearby"}}]}
    requirement = {"required_protocols": [{"protocol_id": "factory_navigation_visit_v2",
                                           "scope": {"scene_key": "factory", "target": "dock"}}]}
    fleet = [{"id": "one", "robot_key": "simple_car"}]
    eligible, rejected = eligible_fleet(fleet, {"simple_car": profile}, requirement)
    assert not eligible and "scope_unverified" in rejected[0]["reasons"][0]
    requirement["required_protocols"][0]["scope"]["target"] = "nearby"
    assert eligible_fleet(fleet, {"simple_car": profile}, requirement)[0] == fleet
    profile["protocol_checks"][0]["configuration_id"] = "lidar-config"
    assert not eligible_fleet(fleet, {"simple_car": profile}, requirement)[0]


def test_factory_inspection_admits_only_exact_scoped_configuration_checks():
    from pathlib import Path

    from EAI.scenario_spec import load_scenario
    from demo.benchmark.allocation import eligible_fleet
    from demo.factory_benchmark.tasks import factory_task_requirements
    from tools.scenario_generation.capability_profile import profile_for_dossier
    from tools.scenario_generation.record_configuration_microtests import task_scope
    from tools.scenario_generation.robot_dossier import robot_dossier

    root = Path(__file__).resolve().parents[3]
    scenario = load_scenario(root / "demo/factory_benchmark/scenarios/inspection_dispatch_01.json")
    requirements = factory_task_requirements("F-INS-01")
    nodes = {node["id"]: node for node in scenario["phase_graph"]["nodes"]}
    for requirement in requirements:
        for protocol in requirement.get("required_protocols", ()):
            protocol["scope"] = task_scope(scenario, nodes[requirement["id"]])

    checks_by_robot = {
        "iris": [("aerial_view", "factory_inventory_detection_v2")],
        "simple_car": [("ground_approach", "factory_navigation_visit_v2"),
                       ("ground_view", "factory_inventory_detection_v2")],
        "carter": [("route_confirmation", "factory_navigation_visit_v2")],
    }
    profiles = {}
    fleet = []
    for robot in scenario["robots"]:
        dossier = robot_dossier(
            robot["type"], attachments=[attachment["type"] for attachment in robot["attachments"]])
        declared = profile_for_dossier(dossier)
        configuration_id = declared["identity"]["configuration_id"]
        checks = [{
            "kind": "protocol_check", "status": "passed", "configuration_id": configuration_id,
            "protocol_id": protocol_id, "sample_count": 1, "evidence_id": f"{node_id}-evidence",
            "evidence_tier": "E6", "scope": task_scope(scenario, nodes[node_id]),
        } for node_id, protocol_id in checks_by_robot[robot["type"]]]
        profiles[configuration_id] = profile_for_dossier(dossier, checks)
        fleet.append({"id": robot["id"], "robot_key": robot["type"],
                      "profile_key": configuration_id})

    fixed = {node["id"]: node.get("agents") or [node["preferred_agent"]]
             for node in scenario["phase_graph"]["nodes"]}
    comparison = compare_allocators(requirements, fleet, profiles, fixed)
    assert all(result["status"] == "feasible" for result in comparison["methods"].values())
    assert all(axis["status"] == "unknown" for profile in profiles.values() for axis in profile["radar"])

    eligible_by_node = {
        requirement["id"]: [robot["id"] for robot in eligible_fleet(fleet, profiles, requirement)[0]]
        for requirement in requirements
    }
    assert eligible_by_node == {
        "aerial_view": ["iris_1"],
        "ground_approach": ["simple_car_1"],
        "ground_view": ["simple_car_1"],
        "route_confirmation": ["carter_1"],
    }

    ground_view = next(item for item in requirements if item["id"] == "ground_view")
    simple_profile_key = next(robot["profile_key"] for robot in fleet if robot["id"] == "simple_car_1")
    altered_profiles = deepcopy(profiles)
    detection = next(check for check in altered_profiles[simple_profile_key]["protocol_checks"]
                     if check["protocol_id"] == "factory_inventory_detection_v2")
    detection["scope"] = {**detection["scope"], "duration_s": 3.0}
    eligible, rejected = eligible_fleet(fleet, altered_profiles, ground_view)
    assert "simple_car_1" not in {robot["id"] for robot in eligible}
    assert "protocol:factory_inventory_detection_v2_scope_unverified" in next(
        item["reasons"] for item in rejected if item["robot_id"] == "simple_car_1")


def test_optimizer_budget_exhaustion_is_not_a_proof_of_infeasibility():
    requirements, fleet, profiles = _scarce_sensor_problem()
    result = optimized_allocate(requirements, fleet, profiles, max_candidate_evaluations=1)
    assert result["status"] == "unknown"
    assert result["budget_exhausted"]
    assert not result["optimality_proven"]
    assert not result["infeasibility_proven"]


def test_fixed_baseline_obeys_sensor_and_concurrency_constraints():
    requirements, fleet, profiles = _scarce_sensor_problem()
    result = fixed_allocate(requirements, fleet, profiles,
                            {"flexible": ["a_specialist"], "inspection": ["a_specialist"]})
    assert result["status"] == "infeasible"


def test_sequential_cost_optimization_and_compiler_preserve_same_team_binding():
    requirements = [{"id": "arrive", "domain": "ground"},
                    {"id": "watch", "domain": "ground", "prerequisites": ["arrive"],
                     "same_team_as": "arrive"}]
    fleet = [{"id": "one", "robot_key": "general"}, {"id": "two", "robot_key": "general"}]
    result = optimized_allocate(requirements, fleet, {"general": _profile()}, estimated_costs={
        "arrive": {"one": 10, "two": 1}, "watch": {"one": 1, "two": 2},
    })
    assert result["objective"] == {"cost": 3, "distinct_robot_count": 1}
    assert [item["agents"] for item in result["assignments"]] == [["two"], ["two"]]


@pytest.mark.parametrize("requirements", [
    [{"id": "same"}, {"id": "same"}],
    [{"id": "first", "prerequisites": ["missing"]}],
    [{"id": "first", "prerequisites": ["second"]}, {"id": "second", "prerequisites": ["first"]}],
    [{"id": "first", "same_team_as": "second"}, {"id": "second"}],
])
def test_invalid_requirement_graphs_fail_closed(requirements):
    with pytest.raises(ValueError):
        greedy_allocate(requirements, [], {})
