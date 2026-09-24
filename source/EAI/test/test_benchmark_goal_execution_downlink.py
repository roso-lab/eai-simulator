from pathlib import Path
import json

import pytest

from demo.benchmark.communication import CommunicationRuntime
from demo.benchmark.goal_driven_contract import GOAL_PLAN_VERSION
from demo.benchmark.goal_driven_tasks import build_goal_task_package
from demo.benchmark.goal_episode_runner import FormalGoalEpisodeRunner
from demo.benchmark.goal_execution_downlink import GoalExecutionDownlink
from demo.benchmark.provenance import canonical_sha256


def _task(agents=("robot_a",)):
    return {
        "task_id": "move-1",
        "skill": "ground_navigation",
        "arguments": {"region_id": "destination", "route_ids": ["route-1"]},
        "required_capabilities": ["ground_navigation"],
        "assigned_agents": list(agents),
        "prerequisites": [],
        "evidence_refs": [],
        "execution": {"start_s": 0.0, "duration_s": 20.0},
    }


def _states(task, status="pending", *, invalidated=False):
    return {task["task_id"]: {
        "status": status,
        "invalidated_by": [7] if invalidated else [],
    }}


def _runtime(policy=None, *, access=None):
    return CommunicationRuntime({
        "pools": {"backhaul_downlink": dict(policy or {})},
        **({"access": access} if access is not None else {}),
    })


def test_downlink_waits_for_every_real_recipient_delivery_and_counts_full_task():
    task = _task(("robot_a", "robot_b"))
    runtime = _runtime(access={
        "robot_a": {"capacity_bps": 1_000_000.0},
        "robot_b": {"capacity_bps": 100.0},
    })
    downlink = GoalExecutionDownlink(
        {"task_id": "mission"}, episode_id="episode", runtime=runtime, ttl_s=30.0)
    queued = downlink.queue_dispatch(
        task, dispatch_action_id="dispatch-1", time_s=0.0)
    report = runtime.report()
    message = report["messages"][queued["message_id"]]
    assert message["payload"]["estimate"]["task"] == task
    assert message["size_bytes"] == len(json.dumps(
        message["payload"], sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), allow_nan=False).encode("utf-8"))
    assert {recipient: row["access_endpoints"] for recipient, row
            in message["recipients"].items()} == {
                "robot_a": ["robot_a"], "robot_b": ["robot_b"]}

    first_arrival = min(row["scheduled_finish_at_s"]
                        for row in message["recipients"].values())
    last_arrival = max(row["scheduled_finish_at_s"]
                       for row in message["recipients"].values())
    early = downlink.advance(
        first_arrival, active_agent_ids=task["assigned_agents"],
        task_states=_states(task))
    assert early["ready_dispatches"] == []
    ready = downlink.advance(
        last_arrival, active_agent_ids=task["assigned_agents"],
        task_states=_states(task))
    assert [row["task_id"] for row in ready["ready_dispatches"]] == ["move-1"]
    delivered = runtime.report()["messages"][queued["message_id"]]["recipients"]
    assert all(row["consumption"] is not None for row in delivered.values())


@pytest.mark.parametrize("policy,ttl,wakeup", [
    ({"active_drop": True}, 5.0, 0.0),
    ({"fixed_delay_s": 5.0}, 2.0, 2.0),
])
def test_drop_and_ttl_are_terminal_transport_failures(policy, ttl, wakeup):
    task = _task()
    downlink = GoalExecutionDownlink(
        {"task_id": "mission"}, episode_id="episode",
        runtime=_runtime(policy), ttl_s=ttl)
    downlink.queue_dispatch(task, dispatch_action_id="dispatch-1", time_s=0.0)
    outcome = downlink.advance(
        wakeup, active_agent_ids=task["assigned_agents"],
        task_states=_states(task))
    assert len(outcome["failed_dispatches"]) == 1
    assert outcome["ready_dispatches"] == []
    assert not downlink.has_pending_work()


def test_cancel_tombstone_suppresses_late_dispatch_and_distinguishes_stop_kind():
    task = _task()
    runtime = _runtime({"fixed_delay_s": 2.0})
    downlink = GoalExecutionDownlink(
        {"task_id": "mission"}, episode_id="episode", runtime=runtime, ttl_s=10.0)
    downlink.queue_dispatch(task, dispatch_action_id="dispatch-1", time_s=0.0)
    downlink.queue_cancel(task, cancel_action_id="cancel-1", time_s=0.0)
    outcome = downlink.advance(
        2.0, active_agent_ids=task["assigned_agents"],
        task_states=_states(task, "cancel_requested"))
    assert outcome["ready_dispatches"] == []
    assert outcome["ready_cancels"][0]["disposition"] == "local_unstarted_withdrawal"

    task2 = {**_task(), "task_id": "move-2"}
    downlink.queue_dispatch(task2, dispatch_action_id="dispatch-2", time_s=2.0)
    ready = downlink.advance(
        4.0, active_agent_ids=task2["assigned_agents"],
        task_states=_states(task2))
    assert ready["ready_dispatches"]
    downlink.mark_started("move-2", dispatch_action_id="dispatch-2", time_s=4.0)
    downlink.queue_cancel(task2, cancel_action_id="cancel-2", time_s=4.0)
    stopped = downlink.advance(
        6.0, active_agent_ids=task2["assigned_agents"],
        task_states=_states(task2, "cancel_requested"))
    assert stopped["ready_cancels"][0]["disposition"] == "physical_stop_required"


def test_removed_member_tombstones_queued_dispatch_before_late_delivery():
    task = _task(("robot_a", "robot_b"))
    downlink = GoalExecutionDownlink(
        {"task_id": "mission"}, episode_id="episode",
        runtime=_runtime({"fixed_delay_s": 3.0}), ttl_s=10.0)
    downlink.queue_dispatch(task, dispatch_action_id="dispatch-1", time_s=0.0)
    invalidated = downlink.advance(
        1.0, active_agent_ids={"robot_a"}, task_states=_states(task))
    assert invalidated["ready_dispatches"] == []
    assert invalidated["failed_dispatches"][0]["reason"] == "member_removed"
    late = downlink.advance(
        3.0, active_agent_ids=task["assigned_agents"],
        task_states=_states(task))
    assert late["ready_dispatches"] == []


def test_downlink_deadline_expires_without_extending_the_execution_window():
    task = _task()
    task["execution"] = {"start_s": 0.0, "duration_s": 1.0}
    downlink = GoalExecutionDownlink(
        {"task_id": "mission"}, episode_id="episode",
        runtime=_runtime({"fixed_delay_s": 3.0}), ttl_s=10.0)
    downlink.queue_dispatch(task, dispatch_action_id="dispatch-1", time_s=0.0)
    assert downlink.next_wakeup_s() == 1.0
    outcome = downlink.advance(
        1.0, active_agent_ids=task["assigned_agents"],
        task_states=_states(task))
    assert outcome["ready_dispatches"] == []
    assert outcome["failed_dispatches"][0]["reason"] == "execution_deadline_elapsed"


def test_ready_command_is_revalidated_after_another_serial_skill_advances_time():
    first = _task()
    second = {**_task(), "task_id": "move-2"}
    runtime = _runtime()
    downlink = GoalExecutionDownlink(
        {"task_id": "mission"}, episode_id="episode", runtime=runtime, ttl_s=2.0)
    downlink.queue_dispatch(first, dispatch_action_id="dispatch-1", time_s=0.0)
    downlink.queue_dispatch(second, dispatch_action_id="dispatch-2", time_s=0.0)
    ready = downlink.advance(
        0.0, active_agent_ids={"robot_a"},
        task_states={**_states(first), **_states(second)})
    assert {row["task_id"] for row in ready["ready_dispatches"]} == {
        "move-1", "move-2"}
    downlink.mark_started("move-1", dispatch_action_id="dispatch-1", time_s=0.0)
    downlink.mark_terminal(
        "move-1", dispatch_action_id="dispatch-1", status="succeeded", time_s=3.0)
    stale = downlink.advance(
        3.0, active_agent_ids={"robot_a"},
        task_states={
            "move-1": {"status": "succeeded", "invalidated_by": []},
            **_states(second),
        })
    assert stale["ready_dispatches"] == []
    assert stale["failed_dispatches"][0]["task_id"] == "move-2"
    with pytest.raises(ValueError, match="fresh ready dispatch"):
        downlink.mark_started(
            "move-2", dispatch_action_id="dispatch-2", time_s=3.0)
    first_report = downlink.report()["dispatches"]["move-1"]
    assert first_report["tombstoned"] is False


def _action(action_id, action_type, payload):
    return {
        "schema": "eai.benchmark.goal_action", "version": 1,
        "action_id": action_id, "submitted_at_s": 0.0,
        "type": action_type, "payload": payload,
    }


class _Participant:
    def __init__(self, plan, robot, *, cancel=False):
        self.plan = plan
        self.robot = robot
        self.cancel = cancel
        self.sent = False

    def manifest(self):
        return {
            "kind": "participant", "id": "downlink-runner-test", "version": 1,
            "interface": "eai.benchmark.goal_algorithm_adapter",
            "interface_version": 1, "evaluation_track": "goal_driven",
            "input_scope": "participant_visible_events_only",
            "control_scope": "benchmark_managed_low_level_control",
        }

    def initialize(self, public_task, participant_api):
        del public_task, participant_api

    def act(self, events):
        if self.sent or not any(row["type"] == "episode_started" for row in events):
            return []
        self.sent = True
        actions = [
            _action("select", "select_team", {"agent_ids": [self.robot]}),
            _action("plan", "submit_plan", {"plan": self.plan}),
            _action("dispatch", "dispatch_skill", {
                "task_id": "formal_nav", "evidence_refs": []}),
        ]
        if self.cancel:
            actions.append(_action(
                "cancel", "cancel_skill", {
                    "task_id": "formal_nav", "reason": "request withdrawn"}))
        return actions

    def close(self):
        return None


class _IdleBackend:
    def __init__(self, regions, objects):
        self.regions = regions
        self.objects = objects
        self.idle_calls = []
        self.execute_calls = []
        self.cancel_calls = []

    def advance_idle(self, time_s):
        self.idle_calls.append(time_s)
        return {"time_s": time_s,
                "runtime_state": {"robot_regions": self.regions,
                                  "object_states": self.objects}}

    def execute(self, task, *, episode_id, trusted_report):
        started = trusted_report["session"]["current_time_s"]
        self.execute_calls.append((task["task_id"], started))
        completed = started + 1.0
        return {
            "status": "succeeded", "completed_at_s": completed,
            "receipt": {
                "backend_id": "downlink-test-native",
                "receipt_ref": f"native:{task['task_id']}",
                "task_id": task["task_id"], "action_id": "dispatch",
                "status": "succeeded", "completed_at_s": completed,
                "episode_id": episode_id,
                "evidence_refs": [f"native:{episode_id}:{task['task_id']}"],
            },
            "runtime_state": {"robot_regions": self.regions,
                              "object_states": self.objects},
        }

    def cancel(self, task, *, episode_id, trusted_report):
        self.cancel_calls.append(task["task_id"])
        pytest.fail("an unstarted dispatch must be withdrawn locally")


class _Observer:
    def __init__(self, public, episode_id, runtime, *, pending_until=None):
        self.episode_id = episode_id
        self.public_task_sha256 = canonical_sha256(public)
        self.runtime = runtime
        self.calls = []
        self.pending_until = pending_until

    def profile(self):
        return {"schema": "test.observer.profile", "version": 1}

    def advance(self, *, host):
        now = host.trusted_report()["session"]["current_time_s"]
        self.calls.append(("advance", now))
        if self.pending_until is not None and now >= self.pending_until:
            self.pending_until = None

    def after_skill(self, task, result, *, host):
        report = host.trusted_report()["session"]
        assert report["tasks"][task["task_id"]]["status"] == result["status"]
        assert report["runtime_state"]["time_s"] == result["completed_at_s"]
        self.calls.append(("after_skill", result["completed_at_s"]))

    def has_pending_work(self):
        return self.pending_until is not None

    def next_wakeup_s(self):
        return self.pending_until

    def report(self):
        return {"schema": "test.observer", "version": 1,
                "calls": list(self.calls), "scoreable": False}


def _runner_fixture():
    bundle = build_goal_task_package("warehouse_nvd")
    public, api = bundle["public_task"], bundle["participant_api"]
    robot = "warehouse_ground_1"
    task = {
        "task_id": "formal_nav", "skill": "ground_navigation",
        "arguments": {"region_id": "consolidation_area",
                      "route_ids": ["rack_a_to_consolidation"]},
        "required_capabilities": ["ground_navigation"],
        "assigned_agents": [robot], "prerequisites": [], "evidence_refs": [],
        "execution": {"start_s": 0.0, "duration_s": 16.0},
    }
    plan = {"schema": "eai.benchmark.goal_plan", "version": GOAL_PLAN_VERSION,
            "plan_id": "downlink-plan", "assigned_team": [robot], "tasks": [task]}
    regions = {row["id"]: "rack_aisle_a" for row in public["candidate_robot_pool"]}
    objects = {row["id"]: {"region_id": row["physical_properties"]["initial_region_id"]}
               for row in public["public_world"]["objects"]}
    return public, api, robot, plan, regions, objects


def test_runner_waits_on_real_idle_clock_archives_once_and_orders_observer(tmp_path: Path):
    public, api, robot, plan, regions, objects = _runner_fixture()
    episode_id = "downlink-runner-1"
    runtime = _runtime({"fixed_delay_s": 2.0})
    downlink = GoalExecutionDownlink(
        public, episode_id=episode_id, runtime=runtime, ttl_s=8.0)
    observer = _Observer(public, episode_id, runtime, pending_until=0.5)
    backend = _IdleBackend(regions, objects)
    root = tmp_path / "episode"
    result = FormalGoalEpisodeRunner(
        public, api, _Participant(plan, robot), backend,
        output_root=root, episode_id=episode_id,
        shared_communication_runtime=runtime,
        downlink=downlink, execution_observer=observer,
    ).run(initial_state={"robot_regions": regions, "object_states": objects})

    assert result["error"] is None
    assert backend.idle_calls == [0.5, 2.0]
    assert backend.execute_calls == [("formal_nav", 2.0)]
    assert result["business_outcomes_validated"] is False
    report = json.loads((root / "execution_downlink_report.json").read_text())
    assert report["dispatches"]["formal_nav"]["physical_started_at_s"] == 2.0
    assert report["dispatches"]["formal_nav"]["terminal"]["status"] == "succeeded"
    assert report["shared_transport_embedded"] is False
    assert (root / "execution_observer_profile.json").is_file()
    assert (root / "execution_observer_report.json").is_file()
    assert (root / "communication_transport_report.json").is_file()
    analysis = json.loads((root / "communication_analysis.json").read_text())
    assert analysis["aggregation"] == "shared_transport_counted_once"
    assert analysis["event_totals"]["published"] == 1
    assert [row[0] for row in observer.calls].count("after_skill") == 1


def test_runner_cancel_delivery_withdraws_locally_without_backend_stop(tmp_path: Path):
    public, api, robot, plan, regions, objects = _runner_fixture()
    episode_id = "downlink-cancel-1"
    runtime = _runtime({"fixed_delay_s": 1.0})
    downlink = GoalExecutionDownlink(
        public, episode_id=episode_id, runtime=runtime, ttl_s=8.0)
    backend = _IdleBackend(regions, objects)
    root = tmp_path / "episode"
    result = FormalGoalEpisodeRunner(
        public, api, _Participant(plan, robot, cancel=True), backend,
        output_root=root, episode_id=episode_id,
        shared_communication_runtime=runtime, downlink=downlink,
    ).run(initial_state={"robot_regions": regions, "object_states": objects})
    assert result["error"] is None
    assert backend.execute_calls == [] and backend.cancel_calls == []
    session = json.loads((root / "goal_session_report.json").read_text())["report"]
    assert session["tasks"]["formal_nav"]["status"] == "cancelled"
    receipt = json.loads((root / "skill_receipts.json").read_text())[0]
    assert receipt["backend_id"] == "goal_execution_downlink_local"
    assert "local_unstarted_withdrawal" in receipt["receipt_ref"]


def test_runner_never_acknowledges_an_undelivered_cancel(tmp_path: Path):
    public, api, robot, plan, regions, objects = _runner_fixture()
    episode_id = "downlink-cancel-drop-1"
    runtime = _runtime({"active_drop": True})
    downlink = GoalExecutionDownlink(
        public, episode_id=episode_id, runtime=runtime, ttl_s=8.0)
    backend = _IdleBackend(regions, objects)
    root = tmp_path / "episode"
    result = FormalGoalEpisodeRunner(
        public, api, _Participant(plan, robot, cancel=True), backend,
        output_root=root, episode_id=episode_id,
        shared_communication_runtime=runtime, downlink=downlink,
    ).run(initial_state={"robot_regions": regions, "object_states": objects})
    assert "safety cancellation was not acknowledged" in result["error"]
    assert backend.execute_calls == [] and backend.cancel_calls == []
    session = json.loads((root / "goal_session_report.json").read_text())["report"]
    assert session["tasks"]["formal_nav"]["status"] == "cancel_requested"
    assert json.loads((root / "skill_receipts.json").read_text()) == []


@pytest.mark.parametrize("policy,ttl", [
    ({"active_drop": True}, 8.0),
    ({"fixed_delay_s": 5.0}, 1.0),
])
def test_runner_never_executes_dropped_or_expired_dispatch(
        tmp_path: Path, policy, ttl):
    public, api, robot, plan, regions, objects = _runner_fixture()
    episode_id = f"downlink-failure-{ttl}"
    runtime = _runtime(policy)
    downlink = GoalExecutionDownlink(
        public, episode_id=episode_id, runtime=runtime, ttl_s=ttl)
    backend = _IdleBackend(regions, objects)
    root = tmp_path / "episode"
    result = FormalGoalEpisodeRunner(
        public, api, _Participant(plan, robot), backend,
        output_root=root, episode_id=episode_id,
        shared_communication_runtime=runtime, downlink=downlink,
    ).run(initial_state={"robot_regions": regions, "object_states": objects})
    assert result["error"] is None
    assert backend.execute_calls == []
    session = json.loads((root / "goal_session_report.json").read_text())["report"]
    assert session["tasks"]["formal_nav"]["status"] == "failed"
    receipt = json.loads((root / "skill_receipts.json").read_text())[0]
    assert receipt["backend_id"] == "goal_execution_downlink_local"
    assert "transport_delivery_failed" in receipt["receipt_ref"]


def test_runner_constructor_requires_exact_shared_bindings(tmp_path: Path):
    public, api, robot, plan, regions, objects = _runner_fixture()
    runtime = _runtime()
    downlink = GoalExecutionDownlink(
        public, episode_id="bound", runtime=runtime, ttl_s=2.0)
    with pytest.raises(ValueError, match="requires a shared communication runtime"):
        FormalGoalEpisodeRunner(
            public, api, _Participant(plan, robot), _IdleBackend(regions, objects),
            output_root=tmp_path / "missing", episode_id="bound", downlink=downlink)
    with pytest.raises(ValueError, match="bound to this episode"):
        FormalGoalEpisodeRunner(
            public, api, _Participant(plan, robot), _IdleBackend(regions, objects),
            output_root=tmp_path / "wrong", episode_id="different",
            shared_communication_runtime=runtime, downlink=downlink)


def test_failed_cancel_is_not_masked_by_other_pending_downlink_work(tmp_path: Path):
    bundle = build_goal_task_package("warehouse_nvd")
    public, api = bundle["public_task"], bundle["participant_api"]
    robots = ["warehouse_ground_1", "warehouse_ground_2", "warehouse_ground_3"]
    destinations = ["rack_aisle_a", "rack_aisle_b", "outbound_dock"]
    routes = ["consolidation_to_rack_a", "consolidation_to_rack_b",
              "consolidation_to_dock"]
    tasks = [{
        "task_id": f"task-{index}", "skill": "ground_navigation",
        "arguments": {"region_id": destination, "route_ids": [route]},
        "required_capabilities": ["ground_navigation"],
        "assigned_agents": [robot], "prerequisites": [], "evidence_refs": [],
        "execution": {"start_s": 0.0, "duration_s": 30.0},
    } for index, (robot, destination, route) in enumerate(
        zip(robots, destinations, routes), start=1)]
    plan = {"schema": "eai.benchmark.goal_plan", "version": GOAL_PLAN_VERSION,
            "plan_id": "three-downlinks", "assigned_team": robots, "tasks": tasks}
    regions = {row["id"]: ("rack_overview" if row["mobility"] == "aerial"
                           else "consolidation_area")
               for row in public["candidate_robot_pool"]}
    objects = {row["id"]: {"region_id": row["physical_properties"]["initial_region_id"]}
               for row in public["public_world"]["objects"]}

    class Participant(_Participant):
        def act(self, events):
            if self.sent:
                return []
            self.sent = True
            return [
                _action("select", "select_team", {"agent_ids": robots}),
                _action("plan", "submit_plan", {"plan": plan}),
                *[_action(f"dispatch-{index}", "dispatch_skill", {
                    "task_id": f"task-{index}", "evidence_refs": []})
                  for index in range(1, 4)],
                _action("cancel-1", "cancel_skill", {
                    "task_id": "task-1", "reason": "withdraw first"}),
            ]

    class SelectiveRuntime(CommunicationRuntime):
        def publish(self, **kwargs):
            if kwargs.get("channel") != "goal_execution_cancel":
                return super().publish(**kwargs)
            now = kwargs["published_at_s"]
            self.set_conditions(
                {"active_drop": True}, pool="backhaul_downlink", time_s=now)
            try:
                return super().publish(**kwargs)
            finally:
                self.set_conditions(
                    {"active_drop": False}, pool="backhaul_downlink", time_s=now)

    runtime = SelectiveRuntime({
        "pools": {"backhaul_downlink": {}},
        "access": {robots[2]: {"capacity_bps": 1.0}},
    })
    episode_id = "masked-cancel-regression"
    downlink = GoalExecutionDownlink(
        public, episode_id=episode_id, runtime=runtime, ttl_s=20.0)

    class Backend(_IdleBackend):
        def execute(self, task, *, episode_id, trusted_report):
            self.execute_calls.append(task["task_id"])
            pytest.fail("ready work executed while another cancel was unacknowledged")

    backend = Backend(regions, objects)
    root = tmp_path / "episode"
    result = FormalGoalEpisodeRunner(
        public, api, Participant(plan, robots[0]), backend,
        output_root=root, episode_id=episode_id,
        shared_communication_runtime=runtime, downlink=downlink,
    ).run(initial_state={"robot_regions": regions, "object_states": objects})
    assert "safety cancellation was not acknowledged" in result["error"]
    assert backend.execute_calls == []
    report = json.loads((root / "execution_downlink_report.json").read_text())
    assert report["dispatches"]["task-1"]["cancel"]["status"] == "failed"
    assert report["dispatches"]["task-2"]["dispatch"]["status"] == "ready"
    assert report["dispatches"]["task-3"]["dispatch"]["status"] == "pending"
