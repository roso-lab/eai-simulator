from __future__ import annotations

from dataclasses import replace

from benchmark.factory_algorithms import EmosFactoryAlgorithm, TeamWeaverFactoryAlgorithm
from benchmark.hetero_diagnostic_runtime_v1 import DiagnosticRuntime
from benchmark.scenarios.factory import FactoryScenarioAdapter


def test_factory_map_bindings_and_hard_deadline_are_registered():
    adapter = FactoryScenarioAdapter()
    graph = adapter.semantic_graph()
    assert graph.resolve("start_1", "ground") == ("ground::node:0019",)
    assert graph.resolve("fire", "ground") == ("ground::node:0014",)
    episode = adapter.build_episode(tier=4)
    assert episode.hard_deadline_tick == 10278
    contract = adapter.contract(tier=4)
    assert contract["hard_deadline_tick"] == episode.hard_deadline_tick
    assert contract["hard_deadline_s"] == 205.56


def test_factory_algorithms_complete_all_registered_tiers_before_deadline():
    adapter = FactoryScenarioAdapter()
    for algorithm_type in (EmosFactoryAlgorithm, TeamWeaverFactoryAlgorithm):
        for tier in range(1, 5):
            result = DiagnosticRuntime(adapter.build_episode(tier=tier), algorithm=algorithm_type()).run()
            assert result["success"] is True
            assert result["deadline_met"] is True
            assert result["hard_deadline_tick"] == adapter.build_episode(tier=tier).hard_deadline_tick


def test_factory_hard_deadline_fails_closed_and_is_not_a_soft_horizon():
    adapter = FactoryScenarioAdapter()
    episode = adapter.build_episode(tier=1)
    runtime = DiagnosticRuntime(replace(episode, hard_deadline_tick=1, horizon_ticks=1000), algorithm=EmosFactoryAlgorithm())
    result = runtime.run()
    assert result["success"] is False
    assert result["failure_reason"] == "hard_deadline_exceeded"
    assert result["deadline_met"] is False
