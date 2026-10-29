from __future__ import annotations

from dataclasses import replace

from benchmark.city_01_algorithms import EmosCity01Algorithm, TeamWeaverCity01Algorithm
from benchmark.hetero_diagnostic_runtime_v1 import DiagnosticRuntime
from benchmark.scenarios.city_01 import City01ScenarioAdapter


def test_city01_adapters_complete_all_frozen_tiers():
    adapter = City01ScenarioAdapter()
    for algorithm in (EmosCity01Algorithm, TeamWeaverCity01Algorithm):
        for tier in range(1, 5):
            runtime = DiagnosticRuntime(
                adapter.build_episode(tier=tier), algorithm=algorithm()
            )
            result = runtime.run()
            assert result["success"] is True
            assert result["coverage_fraction"] == 1.0
            assert result["task_completion_rate"] == 1.0
            if tier >= 3:
                assert result["recover_services"] == 4
                assert result["resource_contention_requests"] >= 1
            if tier == 4:
                members = runtime.tasks.group_members["ground_response"]
                assert len(members) == 4


def test_city01_adapter_checkpoint_replay_is_deterministic():
    adapter = City01ScenarioAdapter()
    runtime = DiagnosticRuntime(
        adapter.build_episode(tier=4, condition_id="battery_heterogeneous", communication="C3"),
        algorithm=TeamWeaverCity01Algorithm(),
    )
    checkpoint = runtime.checkpoint()
    result = runtime.replay(checkpoint)
    assert result["replay_equivalent"] is True


def test_city01_incident_triggers_after_three_areas_and_remaining_areas_close():
    adapter = City01ScenarioAdapter()
    runtime = DiagnosticRuntime(adapter.build_episode(tier=2), algorithm=EmosCity01Algorithm())
    result = runtime.run()

    trigger = next(item for item in result["events"] if item["kind"] == "incident_triggered")
    assert trigger["completed_activation_count"] == 3
    completed = runtime.tasks.completed_tick
    coverage = [task_id for task_id, spec in runtime.tasks.specs.items() if spec.kind == "coverage"]
    assert len(coverage) == 6
    assert sum(completed[task_id] <= trigger["tick"] for task_id in coverage) == 3
    assert all(task_id in completed for task_id in coverage)
    assert any(completed[task_id] > trigger["tick"] for task_id in coverage)


def test_city01_reports_block_replanning_and_pending_is_live_state():
    adapter = City01ScenarioAdapter()
    episode = replace(adapter.build_episode(tier=4, communication="C3"), seed=4)
    runtime = DiagnosticRuntime(episode, algorithm=EmosCity01Algorithm())
    result = runtime.run()

    assert result["success"] is True
    assert result["notification_pending"] == 0
    assert result["notification_submitted"] >= result["notification_delivered"]
    assert result["report_wait_ticks"] > 0
    assert result["communication_loss_bits"] >= 0
    waits = [event for event in result["events"] if event["kind"] == "notification_submitted" and event["attempt"] > 1]
    assert waits, "a C0--C3 stress run must expose report retry/replanning blocking"


def test_city01_availability_loss_removes_observation_robot_after_delivery():
    adapter = City01ScenarioAdapter()
    runtime = DiagnosticRuntime(
        adapter.build_episode(tier=4, condition_id="availability_loss", communication="C0"),
        algorithm=EmosCity01Algorithm(),
    )
    result = runtime.run()

    loss = next(item for item in result["events"] if item["kind"] == "availability_loss")
    delivered = next(item for item in result["events"]
                     if item["kind"] == "notification_delivered"
                     and item["task_id"] == "incident_observation_2")
    assert loss["robot_id"] == delivered["robot_id"]
    assert loss["tick"] >= delivered["tick"]
    assert loss["robot_id"] in result["lost_robots"]
    assert runtime.robot_states[loss["robot_id"]].status == "lost"
