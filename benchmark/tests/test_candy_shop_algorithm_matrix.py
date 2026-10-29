from __future__ import annotations

from benchmark.candy_shop_algorithms import EmosCandyShopAlgorithm, TeamWeaverCandyShopAlgorithm
from benchmark.hetero_diagnostic_runtime_v1 import DiagnosticRuntime
from benchmark.scenarios.candy_shop import CandyShopScenarioAdapter


def test_candy_shop_semantic_contract_and_floor2_routes():
    adapter = CandyShopScenarioAdapter()
    graph = adapter.semantic_graph()
    assert graph.resolve("floor2-shelf-north", "ground") == ("ground::node:0084",)
    assert graph.resolve("floor2-shelf-north", "aerial") == ("aerial::node:0003",)
    assert graph.resolve("floor1-gate-external", "ground") == ("ground::node:0008",)
    assert graph.resolve("shelf_1", "ground") == ("ground::node:0084",)
    assert graph.shortest("ground::node:0008", "ground::node:0084")[0] > 0


def test_candy_shop_both_algorithms_complete_all_tiers():
    adapter = CandyShopScenarioAdapter()
    for algorithm_type in (EmosCandyShopAlgorithm, TeamWeaverCandyShopAlgorithm):
        for tier in range(1, 5):
            runtime = DiagnosticRuntime(adapter.build_episode(tier=tier), algorithm=algorithm_type())
            result = runtime.run()
            assert result["success"] is True, (algorithm_type.__name__, tier, result["failure_reason"])
            assert result["task_completion_rate"] == 1.0
            assert result["deadline_met"] is True
            placed = adapter.diagnostic_metrics(result)["object_place_success"]
            assert len(placed) == {1: 1, 2: 2, 3: 2, 4: 3}[tier]
            if tier >= 3:
                assert result["resource_contention_requests"] >= 1
                assert result["resource_wait_ticks"] > 0


def test_candy_shop_handoff_enforces_distinct_capability_roles():
    adapter = CandyShopScenarioAdapter()
    runtime = DiagnosticRuntime(adapter.build_episode(tier=2), algorithm=EmosCandyShopAlgorithm())
    result = runtime.run()
    assert result["success"] is True
    picks = {row["object_id"]: row["robot_id"] for row in result["events"] if row["kind"] == "object_picked"}
    receives = {row["object_id"]: row["robot_id"] for row in result["events"] if row["kind"] == "handoff_accept"}
    assert picks and receives
    assert set(picks) == set(receives)
    assert all(picks[key] != receives[key] for key in picks)
    assert not [row for row in result["events"] if row["kind"] == "invalid_assignment"]


def test_candy_shop_checkpoint_replay_and_availability_loss():
    adapter = CandyShopScenarioAdapter()
    runtime = DiagnosticRuntime(adapter.build_episode(tier=4, condition_id="availability_loss", seed=7), algorithm=TeamWeaverCandyShopAlgorithm())
    checkpoint = runtime.checkpoint()
    result = runtime.run()
    replay = runtime.replay(checkpoint)
    assert result["success"] is True
    assert result["lost_robots"] == ["pegasus_1"]
    assert replay["replay_equivalent"] is True


def test_candy_shop_run_generic_reports_phase_checkpoints():
    result = CandyShopScenarioAdapter().run_generic(tier=4, condition_id="communication_stress")
    assert [row["label"] for row in result["checkpoints"]] == [
        "cp_00_spawn_valid", "cp_01_gate_open_verified",
        "cp_02_inventory_observed", "cp_03_handoff_complete",
        "cp_04_order_closeout",
    ]
    assert all(row["applicable"] for row in result["checkpoints"])


def test_candy_shop_c3_keeps_report_transport_in_the_protocol():
    adapter = CandyShopScenarioAdapter()
    runtime = DiagnosticRuntime(adapter.build_episode(tier=4, condition_id="communication_stress", seed=3), algorithm=EmosCandyShopAlgorithm())
    result = runtime.run()
    assert result["success"] is True
    assert result["notification_submitted"] >= result["notification_delivered"]
    assert result["report_wait_ticks"] > 0
    assert result["communication_loss_bits"] >= 0


def test_candy_shop_public_goals_do_not_expose_floor_qualified_labels():
    runtime = DiagnosticRuntime(CandyShopScenarioAdapter().build_episode(tier=3), algorithm=EmosCandyShopAlgorithm())
    public = runtime._observation()["scene_description"]
    assert "floor2-" not in public
    assert "gate_external" in public
