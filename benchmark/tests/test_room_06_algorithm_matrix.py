from benchmark.hetero_diagnostic_runtime_v1 import DiagnosticRuntime
from benchmark.room06_algorithms import EmosRoom06Algorithm, TeamWeaverRoom06Algorithm
from benchmark.scenarios.room_06 import Room06ScenarioAdapter


def test_room06_algorithms_complete_all_tiers_and_conditions():
    adapter = Room06ScenarioAdapter()
    for algorithm in (EmosRoom06Algorithm, TeamWeaverRoom06Algorithm):
        for tier in range(1, 5):
            for condition in ("C0", "C1", "C2", "C3"):
                runtime = DiagnosticRuntime(
                    adapter.build_episode(tier=tier, condition_id=condition, seed=1), algorithm=algorithm()
                )
                result = runtime.run()
                assert result["success"] is True
                assert result["task_completion_rate"] == 1.0
                assert result["deadline_met"] is True
                if tier >= 3:
                    starts = [row for row in result["events"] if row["kind"] == "cooperative_service_started"]
                    completes = [row for row in result["events"] if row["kind"] == "cooperative_service_completed"]
                    arrivals = [row for row in result["events"] if row["kind"] == "goal_reached"
                                and row.get("robot_id") in starts[0]["robot_ids"]] if starts else []
                    assert len(starts) == len(completes) == 1
                    assert len(starts[0]["robot_ids"]) == 2
                    assert starts[0]["robot_ids"] == completes[0]["robot_ids"]
                    assert arrivals and starts[0]["tick"] >= max(row["tick"] for row in arrivals)
                    requests = [row for row in result["events"] if row["kind"] == "cooperative_resource_request"]
                    assert len(requests) == 1 and requests[0]["accepted"] is True
                if tier == 4 and condition == "C3":
                    assert result["lost_robots"]
                    assert result["communication_loss_bits"] > 0


def test_room06_checkpoint_replay_and_branch_are_deterministic():
    adapter = Room06ScenarioAdapter()
    runtime = DiagnosticRuntime(
        adapter.build_episode(tier=4, condition_id="C3", seed=4), algorithm=EmosRoom06Algorithm()
    )
    checkpoint = runtime.checkpoint(metadata={"scenario": "room_06", "tier": "4"})
    replay = runtime.replay(checkpoint)
    branch = runtime.branch(checkpoint)
    assert replay["replay_equivalent"] is True
    assert branch.replay_digest(checkpoint) == replay["replay_digest"]


def test_room06_run_generic_reports_phase_checkpoints():
    result = Room06ScenarioAdapter().run_generic(tier=3, condition_id="C2")
    labels = [row["label"] for row in result["checkpoints"]]
    assert labels[:3] == ["cp_00_spawn_valid", "cp_01_books_assigned", "cp_02_relay_started"]
    assert "cp_04_books_complete" in labels
    assert labels[-1] == "cp_07_closeout"


def test_room06_public_observation_exposes_resources_without_hidden_future_ids():
    adapter = Room06ScenarioAdapter()
    runtime = DiagnosticRuntime(
        adapter.build_episode(tier=4, condition_id="C3"), algorithm=TeamWeaverRoom06Algorithm()
    )
    observation = runtime._observation()
    assert {row["resource_id"] for row in observation["resources"]} == {
        "transition_capacity", "joint_carry_capacity"
    }
    assert "heavy_carry_left" not in observation["scene_description"]
    assert "heavy_carry_right" not in observation["scene_description"]


def test_room06_contract_binds_latest_semantic_map_revision():
    contract = Room06ScenarioAdapter().contract(tier=4, condition_id="C3")
    assert contract["map_revision"] == "60878828dc38412496b1b322f687a829"
    assert contract["semantic_binding_digest"] == "0a382650f572aa67b815254ad2b4487e8b582a352822f2fda1f32ee1550f35b7"
    assert contract["cross_floor_route_status"] == "logical_proxy_pending_physical_witness"
