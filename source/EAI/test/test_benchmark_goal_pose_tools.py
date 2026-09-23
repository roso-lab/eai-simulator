"""Optional pose tools through the real Goal host and formal archive boundary."""

from copy import deepcopy
import json

import pytest

from demo.benchmark.communication import CommunicationRuntime, validate_communication_report
from demo.benchmark.goal_algorithm_adapter import GoalAlgorithmHost
from demo.benchmark.goal_driven_tasks import build_goal_task_package
from demo.benchmark.goal_episode_runner import FormalGoalEpisodeRunner
from demo.benchmark.goal_pose_telemetry import GoalPoseTelemetry
from demo.benchmark.goal_pose_tools import GoalPoseTools, POSE_TOOL_SCHEMA
from demo.benchmark.goal_session import GoalSession


ROBOT = "warehouse_ground_1"


def call(name="query_pose", *, now=0.0, identifier="pose-query", robot=ROBOT):
    return {"schema": POSE_TOOL_SCHEMA, "version": 1, "call_id": identifier,
            "submitted_at_s": now, "tool": name, "arguments": {"robot_ids": [robot]}}


def consume(message_id, now=0.0):
    return {"schema": "eai.benchmark.goal_action", "version": 1,
            "action_id": "consume-pose", "submitted_at_s": now,
            "type": "consume_message", "payload": {"message_id": message_id}}


class Adapter:
    def __init__(self):
        self.seen = []
        self.calls = 0
        self.actions = []

    def manifest(self):
        return {"kind": "participant", "id": "pose-tools-test", "version": 1,
                "interface": "eai.benchmark.goal_algorithm_adapter", "interface_version": 1,
                "evaluation_track": "goal_driven", "input_scope": "participant_visible_events_only",
                "control_scope": "benchmark_managed_low_level_control"}

    def initialize(self, public_task, participant_api):
        self.public = deepcopy(public_task)
        self.api = deepcopy(participant_api)

    def initialize_tools(self, profile):
        self.profile = deepcopy(profile)

    def act(self, events):
        self.seen.extend(deepcopy(events))
        self.calls += 1
        result, self.actions = self.actions, []
        return result

    def close(self):
        pass


@pytest.fixture
def bundle():
    return build_goal_task_package("warehouse_nvd")


def setup_tools(bundle, *, contract=None, episode_id="pose-test"):
    sampled = []

    def sample(robot, now):
        sampled.append((robot, now))
        return {"sampled_at_s": now, "position_m": [now, 2.0, 0.5], "yaw_rad": 0.0,
                "frame_id": "world", "source": "synthetic", "quality": {"position_error_m": None}}

    telemetry = GoalPoseTelemetry(CommunicationRuntime(contract), message_prefix=episode_id)
    return GoalPoseTools(bundle["public_task"], episode_id=episode_id,
                         sample=sample, telemetry=telemetry), sampled


def test_no_opt_in_no_samples_and_frozen_inputs_unchanged(bundle):
    tools, sampled = setup_tools(bundle)
    adapter = Adapter()
    adapter.initialize_tools = None
    with GoalAlgorithmHost(bundle["public_task"], bundle["participant_api"], adapter,
                           pose_tools=tools) as host:
        host.request_actions()
    assert sampled == []
    assert not tools.report()["communication"]["messages"]
    assert adapter.public == bundle["public_task"]
    assert adapter.api == bundle["participant_api"]
    assert not hasattr(adapter, "profile")


@pytest.mark.parametrize("contract,visible", [({}, True), ({"drop": True}, False), ({"delay_s": 1}, False)])
def test_query_visibility_is_the_link_result_not_a_direct_return(bundle, contract, visible):
    tools, sampled = setup_tools(bundle, contract=contract)
    adapter = Adapter()
    adapter.actions = [call()]
    with GoalAlgorithmHost(bundle["public_task"], bundle["participant_api"], adapter,
                           pose_tools=tools) as host:
        results = host.request_actions()
        assert results[0]["accepted"]
        assert not any(e["type"] == "observation" for e in adapter.seen)
        host.request_actions()
        assert bool([e for e in adapter.seen if e["type"] == "observation"
                     and e["payload"]["kind"] == "pose_update"]) is visible
        assert any(e["payload"].get("kind") == "pose_tool_result" for e in adapter.seen)
    assert sampled == [(ROBOT, 0.0)]
    assert tools.report()["calls"][0]["size_bytes"] > 0
    assert validate_communication_report(tools.report()["communication"])["validated"]


def test_subscribe_unsubscribe_and_no_backfill(bundle):
    tools, sampled = setup_tools(bundle)
    assert tools.tick(0) == []
    tools.submit(call("subscribe_pose"), time_s=0)
    assert len([row for row in tools.tick(0) if row["kind"] == "pose_update"]) == 1
    assert tools.tick(0.5) == []
    assert len(tools.tick(3.25)) == 1
    assert sampled == [(ROBOT, 0.0), (ROBOT, 3.25)]
    row = tools.report()["subscription_samples"][-1]
    assert row == {"robot_id": ROBOT, "scheduled_at_s": 1.0,
                   "sampled_at_s": 3.25, "missed_periods": 2}
    tools.submit(call("unsubscribe_pose", now=3.25, identifier="stop"), time_s=3.25)
    assert not [row for row in tools.tick(4) if row["kind"] == "pose_update"]
    assert len(sampled) == 2


def test_fresh_consumption_is_recorded_in_both_ledgers(bundle):
    tools, _ = setup_tools(bundle)
    adapter = Adapter()
    adapter.actions = [call()]
    with GoalAlgorithmHost(bundle["public_task"], bundle["participant_api"], adapter,
                           pose_tools=tools) as host:
        host.request_actions()
        host.request_actions()
        message_id = next(e["payload"]["message_id"] for e in adapter.seen
                          if e["payload"].get("kind") == "pose_update")
        adapter.actions = [consume(message_id)]
        host.emit("tick", 0, {"state_digest": "tick-after-query"})
        host.request_actions()
        assert message_id in host.trusted_report()["session"]["consumed_message_ids"]
    report = tools.report()["communication"]
    assert [row["consumer_id"] for row in report["ledger"] if row["event"] == "consumed"] == ["consume-pose"]
    assert validate_communication_report(report)["validated"]


def test_expired_consumption_does_not_contaminate_goal_ledger(bundle):
    tools, _ = setup_tools(bundle)
    adapter = Adapter()
    adapter.actions = [call()]
    with GoalAlgorithmHost(bundle["public_task"], bundle["participant_api"], adapter,
                           pose_tools=tools) as host:
        host.request_actions()
        host.request_actions()
        message_id = next(e["payload"]["message_id"] for e in adapter.seen
                          if e["payload"].get("kind") == "pose_update")
        adapter.actions = [consume(message_id, now=5)]
        host.emit("tick", 5, {"state_digest": "expired"})
        with pytest.raises(ValueError, match="fresh"):
            host.request_actions()
        assert host.trusted_report()["session"]["consumed_message_ids"] == []
    assert not any(row["event"] == "consumed" for row in tools.report()["communication"]["ledger"])


def test_unauthorized_or_forged_query_cannot_sample(bundle):
    tools, sampled = setup_tools(bundle)
    for invalid in (call(robot="private_object"), {**call(), "submitted_at_s": True},
                    {**call(), "arguments": {"robot_ids": [ROBOT], "position_m": [1, 2, 3]}}):
        with pytest.raises(ValueError):
            tools.submit(invalid, time_s=0)
    assert sampled == []


def test_formal_runner_archives_delayed_tool_delivery_on_real_idle_ticks(bundle, tmp_path):
    tools, _ = setup_tools(bundle, contract={"delay_s": 1})
    state = {"robot_regions": {r["id"]: "rack_aisle_a" for r in bundle["public_task"]["candidate_robot_pool"]},
             "object_states": {o["id"]: {"region_id": o["physical_properties"]["initial_region_id"]}
                               for o in bundle["public_task"]["public_world"]["objects"]}}

    class WaitingAdapter(Adapter):
        def act(self, events):
            self.seen.extend(deepcopy(events))
            if self.calls == 0:
                self.calls += 1
                return [call()]
            observations = [e for e in events if e["payload"].get("kind") == "pose_update"]
            if observations:
                return [consume(observations[0]["payload"]["message_id"], now=1)]
            return []

    class Backend:
        def __init__(self):
            self.ticks = []

        def advance_idle(self, time_s):
            self.ticks.append(time_s)
            return {"time_s": time_s, "runtime_state": deepcopy(state)}

    adapter, backend = WaitingAdapter(), Backend()
    result = FormalGoalEpisodeRunner(
        bundle["public_task"], bundle["participant_api"], adapter, backend,
        episode_id="pose-test", output_root=tmp_path / "episode", pose_tools=tools).run(initial_state=state)
    assert result["error"] is None
    assert result["scoreable"] is False
    assert backend.ticks == [1]
    archive = json.loads((tmp_path / "episode/pose_tools_report.json").read_text())
    assert archive["episode_id"] == "pose-test"
    assert any(row["event"] == "consumed" for row in archive["communication"]["ledger"])
    assert json.loads((tmp_path / "episode/pose_communication_replay.json").read_text())["validated"]


def test_query_then_guess_consume_in_same_batch_cannot_claim_received_pose(bundle):
    tools, _ = setup_tools(bundle)
    adapter = Adapter()
    adapter.actions = [call(), consume("pose-test:pose:0")]
    with GoalAlgorithmHost(bundle["public_task"], bundle["participant_api"], adapter,
                           pose_tools=tools) as host:
        with pytest.raises(ValueError, match="delivered to the algorithm"):
            host.request_actions()
        assert not host.trusted_report()["session"]["consumed_message_ids"]
    assert not any(row["event"] == "consumed" for row in tools.report()["communication"]["ledger"])


def test_drop_and_late_delivery_have_same_public_timeout_without_link_diagnosis(bundle):
    public_results = []
    for contract in ({"drop": True}, {"delay_s": 20}):
        tools, sampled = setup_tools(bundle, contract=contract)
        tools.submit(call(), time_s=0)
        acknowledgement = tools.tick(0)
        assert tools.has_pending_work()
        assert tools.tick(4) == []
        timeout = tools.tick(5)
        assert not tools.has_pending_work()
        assert sampled == [(ROBOT, 0.0)]
        public_results.append([acknowledgement, timeout])
        assert timeout[0]["kind"] == "pose_query_timeout"
        assert timeout[0]["data"] == {"call_id": "pose-query", "robot_ids": [ROBOT],
                                      "response_deadline_s": 5}
    assert public_results[0] == public_results[1]


def test_invalid_consume_action_does_not_change_communication_ledger(bundle):
    tools, _ = setup_tools(bundle)
    adapter = Adapter()
    adapter.actions = [call()]
    with GoalAlgorithmHost(bundle["public_task"], bundle["participant_api"], adapter,
                           pose_tools=tools) as host:
        host.request_actions()
        host.request_actions()
        before = tools.report()["communication"]
        adapter.actions = [{**consume("pose-test:pose:0"), "version": 999}]
        host.emit("tick", 0, {"state_digest": "invalid-action"})
        with pytest.raises(ValueError):
            host.request_actions()
        assert tools.report()["communication"] == before
        assert not host.trusted_report()["session"]["consumed_message_ids"]


def test_new_member_queries_require_public_declaration_and_stop_on_exit(bundle):
    tools, sampled = setup_tools(bundle)
    profile = tools.public_profile()
    with pytest.raises(ValueError, match="public robot"):
        tools.submit(call(robot="joined"), time_s=0, current_agents={"joined"})
    adapter = Adapter()
    with GoalAlgorithmHost(bundle["public_task"], bundle["participant_api"], adapter,
                           pose_tools=tools) as host:
        declaration = deepcopy(bundle["public_task"]["candidate_robot_pool"][1])
        declaration["id"] = "joined"
        host.emit("member_join", 1, {"agent_id": "joined", "capability_declaration": declaration,
                                    "capability_version": 1})
        adapter.actions = [call("subscribe_pose", robot="joined", now=1)]
        host.request_actions()
        assert sampled == [("joined", 1)]
        host.emit("member_exit", 2, {"agent_id": "joined"})
        host.request_actions()
        assert sampled == [("joined", 1)]
    assert tools.public_profile() == profile


@pytest.mark.parametrize("idle_supported", [True, False])
def test_dropped_query_waits_for_timeout_or_reports_missing_clock(bundle, tmp_path, idle_supported):
    tools, _ = setup_tools(bundle, contract={"drop": True})
    state = {"robot_regions": {r["id"]: "rack_aisle_a" for r in bundle["public_task"]["candidate_robot_pool"]},
             "object_states": {o["id"]: {"region_id": o["physical_properties"]["initial_region_id"]}
                               for o in bundle["public_task"]["public_world"]["objects"]}}
    adapter = Adapter()
    adapter.actions = [call()]

    class Backend:
        def advance_idle(self, requested_time):
            # Report real progression; the runner must not stamp the requested
            # time on a pose when the backend actually advances farther.
            return {"time_s": requested_time + 0.25, "runtime_state": deepcopy(state)}

    backend = Backend()
    if not idle_supported:
        backend.advance_idle = None
    result = FormalGoalEpisodeRunner(
        bundle["public_task"], bundle["participant_api"], adapter, backend,
        episode_id="pose-test", output_root=tmp_path / "episode", pose_tools=tools).run(initial_state=state)
    if idle_supported:
        assert result["error"] is None
        timeouts = [e for e in adapter.seen if e["payload"].get("kind") == "pose_query_timeout"]
        assert len(timeouts) == 1
        snapshots = json.loads((tmp_path / "episode/runtime_snapshots.json").read_text())
        assert [row["time_s"] for row in snapshots] == [0, 1.25, 2.5, 3.75, 5]
    else:
        assert "cannot advance its idle clock" in result["error"]
    assert result["scoreable"] is False


def test_sampler_failure_at_later_tick_closes_host(bundle):
    tools, _ = setup_tools(bundle)
    adapter = Adapter()
    adapter.actions = [call("subscribe_pose")]
    with GoalAlgorithmHost(bundle["public_task"], bundle["participant_api"], adapter,
                           pose_tools=tools) as host:
        host.request_actions()
        def failed_sampler(robot, now):
            raise RuntimeError("native clock unavailable")
        tools._sample = failed_sampler
        host.emit("tick", 1, {"state_digest": "later"})
        with pytest.raises(RuntimeError, match="native clock unavailable"):
            host.request_actions()
        assert host.trusted_report()["failed"]
        assert host.trusted_report()["closed"]
