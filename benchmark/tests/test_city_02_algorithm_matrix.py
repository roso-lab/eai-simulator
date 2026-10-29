from __future__ import annotations

from benchmark.city_02_algorithms import EmosCity02Algorithm, TeamWeaverCity02Algorithm
from benchmark.hetero_diagnostic_runtime_v1 import DiagnosticRuntime
from benchmark.scenarios.city_02 import City02ScenarioAdapter


def test_city02_map_contract_has_spawns_and_dual_inspection9_binding():
    adapter = City02ScenarioAdapter()
    graph = adapter.semantic_graph()
    assert graph.resolve("start_1", "ground") == ("ground::node:0083",)
    assert graph.resolve("start_4", "ground") == ("ground::node:0086",)
    assert graph.resolve("build_01", "aerial") == ("aerial::node:0007",)
    assert set(graph.resolve("Inspection point_9", "ground")) == {"ground::node:0069", "ground::node:0074"}


def test_city02_both_contract_adapters_complete_all_tiers():
    adapter = City02ScenarioAdapter()
    for algorithm in (EmosCity02Algorithm, TeamWeaverCity02Algorithm):
        for tier in range(1, 5):
            runtime = DiagnosticRuntime(adapter.build_episode(tier=tier), algorithm=algorithm())
            result = runtime.run()
            assert result["success"] is True
            assert result["task_completion_rate"] == 1.0
            metrics = adapter.diagnostic_metrics(result)
            assert metrics["patrol_ground_fraction"] == 1.0
            assert metrics["patrol_aerial_fraction"] == 1.0
            assert metrics["xy_area_coverage_fraction"] == 1.0
            if tier >= 2:
                assert metrics["object_place_success"]["warning_sign"] is True
            if tier >= 3:
                assert metrics["object_place_success"]["box"] is True


def test_city02_non_capable_discoverer_holds_until_capable_responder_places_object():
    adapter = City02ScenarioAdapter()
    runtime = DiagnosticRuntime(adapter.build_episode(tier=3), algorithm=EmosCity02Algorithm())
    result = runtime.run()
    starts = [row for row in result["events"] if row["kind"] == "robot_hold_started"]
    releases = [row for row in result["events"] if row["kind"] == "robot_hold_released"]
    assert starts and releases
    assert any(row["release_task"] == "warning_place" for row in starts)
    assert any(row["release_task"] == "box_place" for row in releases)
    placements = [row for row in result["events"] if row["kind"] == "object_placed"]
    assert {row["object_id"] for row in placements} == {"warning_sign", "box"}
    assert not [row for row in result["events"] if row["kind"] == "invalid_assignment" and row["reason"] == "missing_required_capability"]


def test_city02_box_trigger_is_or_and_checkpoint_replay_is_equivalent():
    adapter = City02ScenarioAdapter()
    runtime = DiagnosticRuntime(adapter.build_episode(tier=4, communication="C3"), algorithm=TeamWeaverCity02Algorithm())
    checkpoint = runtime.checkpoint()
    result = runtime.run()
    replay = runtime.replay(checkpoint)
    box_triggers = [row for row in result["events"] if row["kind"] == "incident_triggered" and row["task_id"] == "box_event"]
    assert len(box_triggers) == 1
    assert replay["replay_equivalent"] is True
    assert result["communication_loss_bits"] >= 0
