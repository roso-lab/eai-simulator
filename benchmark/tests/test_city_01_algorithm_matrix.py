from __future__ import annotations

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
