"""Factory task-family admission tests."""

from pathlib import Path

import pytest

from EAI.scenario_spec import load_scenario
from demo.factory_benchmark.tasks import validate_factory_scenario
from demo.benchmark.scheduling import EpisodeScheduler


def test_scan_height_keeps_floor_validation_and_rejects_wrong_skill():
    from EAI.scenario_spec.validation import ScenarioError, validate_scenario

    root = Path(__file__).resolve().parents[3]
    scenario = load_scenario(root / "demo/factory_benchmark/scenarios/inspection_01.json")
    node = scenario["phase_graph"]["nodes"][0]
    node["scan_height_m"] = 2.3
    validate_scenario(scenario)
    scenario["composition"]["targets"][0]["position"][2] = 1.3
    with pytest.raises(ScenarioError, match="known floor"):
        validate_scenario(scenario)
    scenario["composition"]["targets"][0]["position"][2] = 1.57
    node["scan_height_m"] = 1.5
    with pytest.raises(ScenarioError, match="above the target"):
        validate_scenario(scenario)
    scenario["composition"]["targets"][0]["position"][2] = 0.0
    scenario["phase_graph"]["nodes"][1]["scan_height_m"] = 2.3
    with pytest.raises(ScenarioError, match="only supported for aerial_scan"):
        validate_scenario(scenario)


def test_factory_dispatch_uses_its_own_task_design_track():
    from demo.factory_benchmark.challenge import compile_challenge

    bundle = compile_challenge()
    public = bundle["public_task"]
    assert "task_design_review" not in public["business_rules"]
    review = bundle["hidden_evaluator"]["task_design_review"]
    assert review["track"] == "factory_dispatch_v1"
    assert review["task_id"] == "F-INS-01"
    assert review["node_ids"] == [
        "aerial_view", "ground_approach", "ground_view", "route_confirmation",
    ]
    assert review["execution_validated"] is False


def test_traffic_task_is_explicit_and_does_not_require_aerial_or_transport():
    root = Path(__file__).resolve().parents[3]
    scenario = load_scenario(root / "demo/factory_benchmark/scenarios/traffic_01.json")
    result = validate_factory_scenario(scenario, "F-TRAFFIC-01")
    assert result["validated"]


def test_task_admission_rejects_wrong_family_or_missing_resource():
    root = Path(__file__).resolve().parents[3]
    scenario = load_scenario(root / "demo/factory_benchmark/scenarios/traffic_01.json")
    with pytest.raises(ValueError, match="benchmark_task_id"):
        validate_factory_scenario({**scenario, "benchmark_task_id": "F-COV-01"}, "F-TRAFFIC-01")
    broken = {**scenario, "phase_graph": {"nodes": [
        {**scenario["phase_graph"]["nodes"][0], "resources": []},
        scenario["phase_graph"]["nodes"][1],
    ]}}
    with pytest.raises(ValueError, match="shared resources"):
        validate_factory_scenario(broken, "F-TRAFFIC-01")


def test_coverage_baseline_declares_single_agent_comparison_mode():
    root = Path(__file__).resolve().parents[3]
    scenario = load_scenario(root / "demo/factory_benchmark/scenarios/coverage_01.json")
    result = validate_factory_scenario(scenario, "F-COV-01")
    assert result["validated"]

    from EAI.scenario_spec.benchmark_contract import benchmark_eligibility_contract

    contract = benchmark_eligibility_contract(scenario, payload_policy={"simple_car": 10.0})
    assert contract["eligible"]
    assert contract["comparison_mode"] == "single_agent_baseline"


def test_coverage_dropout_declares_member_exit_recovery_variant():
    root = Path(__file__).resolve().parents[3]
    scenario = load_scenario(root / "demo/factory_benchmark/scenarios/coverage_01_dropout.json")
    result = validate_factory_scenario(scenario, "F-COV-01")
    assert result["validated"]
    assert scenario["fault_events"][0]["replacement_agent"] == "simple_car_4"


def test_declared_recovery_node_can_start_after_member_exit():
    scheduler = EpisodeScheduler([
        {"node": "watch", "agent": "simple_car_1", "team": None,
         "prerequisites": [], "timeout_s": 20.0, "resources": ["storage_west"]},
        {"node": "takeover", "agent": "simple_car_4", "team": None,
         "recovery_for": "watch", "prerequisites": ["watch"],
         "timeout_s": 20.0, "resources": ["storage_west"]},
    ])
    assert scheduler.advance(0.0) == ["watch"]
    scheduler.fail("watch", "member_exit:simple_car_1")
    assert scheduler.advance(1.0) == ["takeover"]
    scheduler.complete("takeover")
    assert scheduler.report()["states"]["takeover"]["status"] == "succeeded"
