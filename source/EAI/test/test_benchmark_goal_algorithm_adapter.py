"""Offline tests for the common high-level goal algorithm bridge."""

from copy import deepcopy

import pytest

from demo.benchmark.goal_algorithm_adapter import GoalAlgorithmHost
from demo.benchmark.goal_driven_tasks import build_goal_task_package
from demo.benchmark.goal_session import GoalSession
from demo.benchmark.reliable_skill_backend import ReliableSkillReferenceHost


SCENES = ("factory", "warehouse_nvd", "modern_house_nvd", "city")


def action(action_id, action_type, payload, *, time_s=0.0):
    return {
        "schema": "eai.benchmark.goal_action",
        "version": 1,
        "action_id": action_id,
        "submitted_at_s": time_s,
        "type": action_type,
        "payload": payload,
    }


class RecordingAdapter:
    def __init__(self, decide):
        self.decide = decide
        self.initialized = []
        self.batches = []
        self.close_count = 0

    def manifest(self):
        return {
            "kind": "participant", "id": "test-goal-algorithm", "version": 1,
            "interface": "eai.benchmark.goal_algorithm_adapter",
            "interface_version": 1, "evaluation_track": "goal_driven",
            "input_scope": "participant_visible_events_only",
            "control_scope": "benchmark_managed_low_level_control",
        }

    def initialize(self, public_task, participant_api):
        self.initialized.append((public_task, participant_api))

    def act(self, events):
        self.batches.append(deepcopy(events))
        return self.decide(events)

    def close(self):
        self.close_count += 1


@pytest.mark.parametrize("scene_key", SCENES)
def test_four_goal_scenes_use_one_public_adapter_lifecycle(scene_key):
    bundle = build_goal_task_package(scene_key)
    public, api = bundle["public_task"], bundle["participant_api"]
    selected = public["candidate_robot_pool"][0]["id"]
    adapter = RecordingAdapter(lambda events: [
        action(f"{scene_key}-select", "select_team", {"agent_ids": [selected]},
               time_s=events[-1]["time_s"])
    ])

    host = GoalAlgorithmHost(public, api, adapter)
    results = host.request_actions()

    assert results[0]["accepted"] and results[0]["type"] == "select_team"
    assert adapter.initialized == [(public, api)]
    assert [event["type"] for event in adapter.batches[0]] == ["episode_started"]
    assert host.trusted_report()["session"]["selected_team"] == [selected]
    host.close()
    assert adapter.close_count == 1


def test_select_plan_and_event_cursor_use_only_new_public_events():
    bundle = build_goal_task_package("warehouse_nvd")
    public, api = bundle["public_task"], bundle["participant_api"]
    plan = bundle["hidden_evaluator"]["witness_plans"][0]["plan"]

    def decide(events):
        if events[0]["type"] == "episode_started":
            return [
                action("select", "select_team", {"agent_ids": plan["assigned_team"]}),
                action("plan", "submit_plan", {"plan": plan}),
            ]
        return []

    adapter = RecordingAdapter(decide)
    host = GoalAlgorithmHost(public, api, adapter)
    results = host.request_actions()
    assert [row["type"] for row in results] == ["select_team", "submit_plan"]

    # Action-result events created during the first turn are delivered once on
    # the next turn. Calling again without new events does not invoke the model.
    assert host.request_actions() == []
    assert [event["type"] for event in adapter.batches[1]] == [
        "action_result", "action_result"]
    assert host.request_actions() == []
    assert len(adapter.batches) == 2
    assert host.trusted_report()["session"]["current_plan_id"] == plan["plan_id"]


def test_delivered_observation_can_be_consumed_without_runtime_state_leakage():
    bundle = build_goal_task_package("city")
    public, api = bundle["public_task"], bundle["participant_api"]
    seen = []

    def decide(events):
        seen.extend(deepcopy(events))
        observation = next((event for event in events if event["type"] == "observation"), None)
        return ([] if observation is None else [action(
            "consume-city-message", "consume_message",
            {"message_id": observation["payload"]["message_id"]},
            time_s=observation["time_s"],
        )])

    adapter = RecordingAdapter(decide)
    host = GoalAlgorithmHost(public, api, adapter)
    host.emit("observation", 0.0, {
        "message_id": "city-public-observation",
        "producer_id": public["candidate_robot_pool"][0]["id"],
        "kind": "incident_update",
        "data": {"status": "visible"},
        "valid_until_s": 10.0,
    })

    assert host.request_actions()[0]["type"] == "consume_message"
    assert host.trusted_report()["session"]["consumed_message_ids"] == [
        "city-public-observation"]
    assert all("runtime_state" not in event and "tasks" not in event for event in seen)
    assert set(adapter.initialized[0][0]) == set(public)
    assert "hidden_evaluator" not in adapter.initialized[0][0]


@pytest.mark.parametrize("illegal_type", ["cmd_vel", "joint_torques", "complete_skill"])
def test_algorithm_cannot_issue_low_level_control_or_trusted_receipts(illegal_type):
    bundle = build_goal_task_package("factory")
    adapter = RecordingAdapter(lambda events: [action("illegal", illegal_type, {})])
    host = GoalAlgorithmHost(bundle["public_task"], bundle["participant_api"], adapter)

    with pytest.raises(ValueError, match="low-level control|action type is not declared"):
        host.request_actions()

    report = host.trusted_report()
    assert report["failed"] and report["closed"]
    assert report["session"]["actions"] == []
    assert adapter.close_count == 1
    with pytest.raises(RuntimeError, match="failed"):
        host.request_actions()


def test_adapter_failure_closes_once_and_does_not_replay_seen_events():
    bundle = build_goal_task_package("modern_house_nvd")

    def fail(events):
        raise RuntimeError("planner failed")

    adapter = RecordingAdapter(fail)
    host = GoalAlgorithmHost(bundle["public_task"], bundle["participant_api"], adapter)
    with pytest.raises(RuntimeError, match="planner failed"):
        host.request_actions()
    report = host.trusted_report()
    assert report["event_cursor"] == 1
    assert report["failed"] and report["closed"]
    host.close()
    assert adapter.close_count == 1


def test_initialization_failure_still_runs_cleanup():
    bundle = build_goal_task_package("factory")

    class InitFailure(RecordingAdapter):
        def initialize(self, public_task, participant_api):
            raise RuntimeError("initialization failed")

    adapter = InitFailure(lambda events: [])
    with pytest.raises(RuntimeError, match="initialization failed"):
        GoalAlgorithmHost(bundle["public_task"], bundle["participant_api"], adapter)
    assert adapter.close_count == 1


def test_goal_host_rejects_generic_fixed_dag_manifest():
    bundle = build_goal_task_package("factory")
    class FixedDagManifest(RecordingAdapter):
        def manifest(self):
            return {"kind": "participant", "id": "fixed-dag", "version": 1,
                    "interface": "eai.benchmark.participant_adapter",
                    "interface_version": 1, "evaluation_track": "fixed_dag_diagnostic",
                    "input_scope": "participant_visible_events_only",
                    "control_scope": "benchmark_managed_low_level_control"}
    with pytest.raises(ValueError, match="goal adapter manifest interface"):
        GoalAlgorithmHost(bundle["public_task"], bundle["participant_api"], FixedDagManifest(lambda _: []))


def test_partial_invalid_batch_retains_accepted_action_and_cannot_replay():
    bundle = build_goal_task_package("factory")
    robot = bundle["public_task"]["candidate_robot_pool"][0]["id"]
    adapter = RecordingAdapter(lambda events: [
        action("select", "select_team", {"agent_ids": [robot]}),
        action("bad", "complete_skill", {}),
    ])
    host = GoalAlgorithmHost(bundle["public_task"], bundle["participant_api"], adapter)
    with pytest.raises(ValueError, match="action type is not declared"):
        host.request_actions()
    report = host.trusted_report()
    assert report["session"]["selected_team"] == [robot]
    assert [a["action_id"] for a in report["session"]["actions"]] == ["select"]
    assert report["failed"] and report["closed"]
    with pytest.raises(RuntimeError, match="failed"):
        host.request_actions()
    assert len(adapter.batches) == 1


def test_context_cleanup_does_not_mask_trusted_host_error():
    bundle = build_goal_task_package("factory")
    class BadClose(RecordingAdapter):
        def close(self):
            super().close()
            raise ValueError("cleanup failed")
    adapter = BadClose(lambda events: [])
    with pytest.raises(RuntimeError, match="backend failed"):
        with GoalAlgorithmHost(bundle["public_task"], bundle["participant_api"], adapter):
            raise RuntimeError("backend failed")
    assert adapter.close_count == 1


def test_trusted_skill_receipt_reaches_algorithm_as_public_event():
    bundle = build_goal_task_package("warehouse_nvd")
    public, api = bundle["public_task"], bundle["participant_api"]
    robot = "warehouse_ground_1"
    plan = {"schema": "eai.benchmark.goal_plan", "version": 3,
            "plan_id": "navigation-smoke", "assigned_team": [robot], "tasks": [{
                "task_id": "nav", "skill": "ground_navigation",
                "arguments": {"region_id": "rack_aisle_a", "route_ids": ["consolidation_to_rack_a"]},
                "required_capabilities": ["ground_navigation"], "assigned_agents": [robot],
                "prerequisites": [], "evidence_refs": [],
                "execution": {"start_s": 0., "duration_s": 8.}}]}
    def decide(events):
        if events[0]["type"] != "episode_started":
            return []
        return [action("select", "select_team", {"agent_ids": [robot]}),
                action("plan", "submit_plan", {"plan": plan}),
                action("dispatch", "dispatch_skill", {"task_id": "nav", "evidence_refs": []})]
    adapter = RecordingAdapter(decide)
    with GoalAlgorithmHost(public, api, adapter) as host:
        objects = {o["id"]: {"region_id": o["physical_properties"]["initial_region_id"]}
                   for o in public["public_world"]["objects"]}
        host.update_runtime_state(0., {robot: "consolidation_area"}, objects)
        assert all(row["accepted"] for row in host.request_actions())
        receipt = {"backend_id": "unit-test-only", "receipt_ref": "test/nav",
                   "task_id": "nav", "action_id": "dispatch", "status": "succeeded",
                   "completed_at_s": 5.08, "evidence_refs": ["test/arrival"]}
        assert host.complete_skill("nav", time_s=5.08, status="succeeded", receipt=receipt)["status"] == "succeeded"
        host.request_actions()
        assert host.trusted_report()["session"]["tasks"]["nav"]["status"] == "succeeded"
        assert host.trusted_report()["session"]["scoreable"] is False
        assert any(event["type"] == "action_result" and event["payload"].get("action_id") == "dispatch"
                   and event["payload"]["status"] == "succeeded" for event in adapter.batches[-1])


def test_algorithm_and_skill_hosts_share_one_episode_ledger():
    bundle = build_goal_task_package("warehouse_nvd")
    public, api = bundle["public_task"], bundle["participant_api"]
    robot = "warehouse_ground_1"
    plan = {"schema": "eai.benchmark.goal_plan", "version": 3,
            "plan_id": "shared-ledger", "assigned_team": [robot], "tasks": [{
                "task_id": "shared-nav", "skill": "ground_navigation",
                "arguments": {"region_id": "consolidation_area",
                               "route_ids": ["rack_b_to_consolidation"]},
                "required_capabilities": ["ground_navigation"], "assigned_agents": [robot],
                "prerequisites": [], "evidence_refs": [],
                "execution": {"start_s": 0., "duration_s": 16.}}]}

    def decide(events):
        if events[0]["type"] != "episode_started":
            return []
        return [action("select", "select_team", {"agent_ids": [robot]}),
                action("plan", "submit_plan", {"plan": plan}),
                action("dispatch", "dispatch_skill", {"task_id": "shared-nav", "evidence_refs": []})]

    session = GoalSession(public, api)
    adapter = RecordingAdapter(decide)
    host = GoalAlgorithmHost(public, api, adapter, session=session)
    regions = {
        row["id"]: next(region["id"] for region in public["public_world"]["regions"]
                         if ("aerial_navigation" if row["mobility"] == "aerial"
                             else "ground_navigation") in region["access_modes"])
        for row in public["candidate_robot_pool"]
    }
    regions[robot] = "rack_aisle_b"
    backend = ReliableSkillReferenceHost(public, robot_regions=regions, session=session)

    assert all(row["accepted"] for row in host.request_actions())
    backend.set_occupancy(time_s=0., blocked_routes={}, blocked_regions={},
                          evidence_ref="shared-ledger:clear")
    result = backend.execute_pending("shared-nav", completed_at_s=16.)
    assert result["accepted"] and result["status"] == "succeeded"
    assert host.trusted_report()["session"]["tasks"]["shared-nav"]["status"] == "succeeded"
    assert backend.report()["ledger"]["tasks"]["shared-nav"]["status"] == "succeeded"
    host.close()


def test_shared_host_rejects_a_session_bound_to_another_task():
    warehouse = build_goal_task_package("warehouse_nvd")
    factory = build_goal_task_package("factory")
    adapter = RecordingAdapter(lambda events: [])
    with pytest.raises(ValueError, match="different task"):
        GoalAlgorithmHost(
            warehouse["public_task"], warehouse["participant_api"], adapter,
            session=GoalSession(factory["public_task"], factory["participant_api"]))
