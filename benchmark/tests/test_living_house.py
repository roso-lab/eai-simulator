from dataclasses import replace

import pytest

from benchmark.hetero_diagnostic_runtime_v1 import DiagnosticRuntime
from benchmark.living_house_algorithms import (
    EmosLivingHouseAlgorithm,
    TeamWeaverLivingHouseAlgorithm,
)
from benchmark.scenarios.living_house import LivingHouseScenarioAdapter


def test_living_house_map_digest_and_surface_pose_bindings():
    adapter = LivingHouseScenarioAdapter()
    graph = adapter.semantic_graph()
    assert graph.resolve("start_1", "aerial") == ("aerial::node:0003",)
    assert graph.resolve("Pot", "aerial") == ("aerial::node:0006",)
    # The original high semantic nodes remain present; the logical pose is an
    # additional explicit interaction binding used by Pepper's arm protocol.
    assert "ground::node:0033" in graph.resolve("fruit", "ground")
    assert "ground::node:0033" in graph.resolve("air_trans", "ground")
    assert adapter.contract(tier=4)["paper_score_eligible"] is False


def test_living_house_c3_availability_loss_is_after_aerial_obligations():
    adapter = LivingHouseScenarioAdapter()
    result = DiagnosticRuntime(
        adapter.build_episode(tier=4, condition_id="C3", seed=1),
        algorithm=EmosLivingHouseAlgorithm(),
    ).run()
    assert result["success"] is True
    assert result["lost_robots"] == ["Pegasus"]
    loss = next(event for event in result["events"] if event["kind"] == "availability_loss")
    completed_before_loss = {
        event["task_id"] for event in result["events"]
        if event["kind"] == "task_completed" and event["tick"] <= loss["tick"]
    }
    assert {"pot_place", "bowl_air_place"}.issubset(completed_before_loss)


def test_living_house_contract_freezes_all_condition_deadlines():
    adapter = LivingHouseScenarioAdapter()
    timing = adapter.manifest()["timing_policy"]["logical_candidate_deadline_ticks"]
    assert len(timing) == 16
    assert all(f"t{tier}:C{condition}" in timing
               for tier in range(1, 5) for condition in range(4))


def test_living_house_spawn_contract_matches_normal_robot_roster():
    manifest = LivingHouseScenarioAdapter().manifest()
    roster_ids = {row["robot_id"] for row in manifest["robot_roster"]}
    spawn_ids = set(manifest["spawn_contract"])
    assert roster_ids == spawn_ids
    assert "Carter_D1" not in spawn_ids


def test_all_living_house_conditions_complete_with_goal_baselines():
    adapter = LivingHouseScenarioAdapter()
    for algorithm_type in (EmosLivingHouseAlgorithm, TeamWeaverLivingHouseAlgorithm):
        for tier in range(1, 5):
            for condition in ("C0", "C1", "C2", "C3"):
                result = DiagnosticRuntime(
                    adapter.build_episode(tier=tier, condition_id=condition, seed=1),
                    algorithm=algorithm_type(),
                ).run()
                assert result["success"] is True
                assert result["deadline_met"] is True
                if condition == "C3":
                    assert result["lost_robots"] == ["Pegasus"]


def test_chair_route_contains_stairs_and_scout_consumes_one_shot_descent():
    adapter = LivingHouseScenarioAdapter()
    graph = adapter.semantic_graph()
    distance, route = graph.shortest("ground::node:0020", "ground::node:0007", ("stair_down_once", "chair_carry"))
    assert distance > 0
    assert ("ground::node:0019" in route and "ground::node:0009" in route)
    result = DiagnosticRuntime(adapter.build_episode(tier=1), algorithm=EmosLivingHouseAlgorithm()).run()
    losses = [e for e in result["events"] if e["kind"] == "capability_lost" and e["robot_id"] == "Scout_D1"]
    assert losses and losses[0]["capability"] == "stair_down_once"


def test_wrong_team_table_downstairs_is_contract_level_d1_infeasible():
    adapter = LivingHouseScenarioAdapter()
    certificate = adapter.d1_infeasible_certificate()
    assert certificate["case_id"] == "D1_infeasible_wrong_team_table"
    assert set(certificate["robot_pool"]) == {"M20_D1", "Carter_D1"}
    assert certificate["eligible_robot_ids"] == ["M20_D1"]
    assert certificate["eligible_distinct_holders"] == 1
    assert certificate["required_distinct_holders"] == 2
    assert certificate["infeasibility_reason"] == "carter_no_stair_capability"
    assert certificate["status"] == "team-state-infeasible"
    assert certificate["eligible_distinct_holders"] < certificate["required_distinct_holders"]
    assert certificate["expected_action"] == "not-deployable_safe-stop"

    episode = adapter.build_d1_infeasible_episode()
    assert {robot.robot_id for robot in episode.robots} == {"M20_D1", "Carter_D1"}
    assert all(task.task_id.startswith("table_carry_") for task in episode.tasks)
    assert {task.cooperative_group for task in episode.tasks} == {"table_downstairs"}
    capabilities = {robot.robot_id: set(robot.capabilities) for robot in episode.robots}
    assert "stair_down" in capabilities["M20_D1"]
    assert "stair_down" not in capabilities["Carter_D1"]
    graph = adapter.semantic_graph()
    destination = graph.resolve("chair_new", "ground")[0]
    m20_start = graph.resolve("start_2", "ground")[0]
    carter_start = graph.resolve("start_3", "ground")[0]
    assert graph.shortest(m20_start, destination, ("table_carry", "stair_down"))[0] > 0
    with pytest.raises(ValueError, match="no route"):
        graph.shortest(carter_start, destination, ("table_carry",))


def test_all_living_house_tiers_complete_with_registered_goal_baselines():
    adapter = LivingHouseScenarioAdapter()
    for algorithm_type in (EmosLivingHouseAlgorithm, TeamWeaverLivingHouseAlgorithm):
        for tier in range(1, 5):
            result = DiagnosticRuntime(adapter.build_episode(tier=tier), algorithm=algorithm_type()).run()
            assert result["success"] is True
            assert result["deadline_met"] is True


def test_door_protocol_requires_open_and_closes_after_joint_transit():
    adapter = LivingHouseScenarioAdapter()
    runtime = DiagnosticRuntime(adapter.build_episode(tier=1), algorithm=EmosLivingHouseAlgorithm())
    result = runtime.run()
    assert result["success"]
    opened = [e for e in result["events"] if e["kind"] == "gate_opened"]
    closed = [e for e in result["events"] if e["kind"] == "gate_closed"]
    assert opened and closed
    assert opened[0]["tick"] < closed[0]["tick"]
    transit = [e for e in result["events"] if e["kind"] == "cooperative_service_completed" and e.get("group") == "chair_transit"]
    assert transit and len(transit[0]["robot_ids"]) == 2


def test_checkpoint_replay_preserves_object_and_gate_state():
    adapter = LivingHouseScenarioAdapter()
    runtime = DiagnosticRuntime(adapter.build_episode(tier=4), algorithm=EmosLivingHouseAlgorithm())
    for _ in range(1000):
        if runtime.terminal:
            break
        runtime.step()
    checkpoint = runtime.checkpoint()
    replay = runtime.replay(checkpoint)
    assert replay["replay_equivalent"] is True
    branch = runtime.branch(checkpoint)
    assert branch.snapshot()["skill"]["state"]["gate_state"] == runtime.snapshot()["skill"]["state"]["gate_state"]


def test_wrong_capability_proposal_is_retained_as_invalid_assignment_evidence():
    adapter = LivingHouseScenarioAdapter()

    class WrongPepper(EmosLivingHouseAlgorithm):
        algorithm_id = "wrong_pepper_for_chair"

        def __init__(self):
            super().__init__()
            self.injected = False

        def decide(self, observation):
            if not self.injected:
                for goal in observation.get("pending_goals", ()):
                    if goal["task_id"] == "chair_grasp_left":
                        self.injected = True
                        return {"Pepper": goal["goal_key"]}
            return super().decide(observation)

        def snapshot(self):
            state = dict(super().snapshot())
            state["injected"] = self.injected
            return state

        def restore(self, snapshot):
            super().restore(snapshot)
            self.injected = bool(snapshot.get("injected", False))

    result = DiagnosticRuntime(adapter.build_episode(tier=1), algorithm=WrongPepper()).run()
    assert result["success"]
    invalid = [e for e in result["events"] if e["kind"] == "invalid_assignment"]
    assert any(e["robot_id"] == "Pepper" and e["reason"] == "missing_required_capability" for e in invalid)
