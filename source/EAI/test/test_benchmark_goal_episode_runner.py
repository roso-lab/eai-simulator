from pathlib import Path
import json
import sys

import pytest

from demo.benchmark.goal_driven_contract import GOAL_PLAN_VERSION
from demo.benchmark.goal_driven_tasks import build_goal_task_package
from demo.benchmark.goal_episode_runner import (
    FormalGoalEpisodeRunner, SubprocessGoalAdapter, _public_skill_observation,
)


def _action(action_id, action_type, payload, *, submitted_at_s=0.0):
    return {
        "schema": "eai.benchmark.goal_action", "version": 1,
        "action_id": action_id, "submitted_at_s": submitted_at_s,
        "type": action_type, "payload": payload,
    }


class _Adapter:
    def __init__(self, plan, robot):
        self.plan = plan
        self.robot = robot
        self.sent = False

    def manifest(self):
        return {
            "kind": "participant", "id": "formal-runner-test", "version": 1,
            "interface": "eai.benchmark.goal_algorithm_adapter",
            "interface_version": 1, "evaluation_track": "goal_driven",
            "input_scope": "participant_visible_events_only",
            "control_scope": "benchmark_managed_low_level_control",
        }

    def initialize(self, public_task, participant_api):
        del public_task, participant_api

    def act(self, events):
        if self.sent or not any(event["type"] == "episode_started" for event in events):
            return []
        self.sent = True
        return [
            _action("select", "select_team", {"agent_ids": [self.robot]}),
            _action("plan", "submit_plan", {"plan": self.plan}),
            _action("dispatch", "dispatch_skill", {"task_id": "formal_nav", "evidence_refs": []}),
        ]

    def close(self):
        pass


class _Backend:
    def __init__(self, public, robot, regions, objects):
        self.public = public
        self.robot = robot
        self.regions = regions
        self.objects = objects

    def execute(self, task, *, episode_id, trusted_report):
        assert episode_id
        assert trusted_report["session"]["tasks"][task["task_id"]]["status"] == "pending"
        return {
            "status": "succeeded", "completed_at_s": 16.0,
            "receipt": {
                "backend_id": "formal-runner-test-backend",
                "receipt_ref": "formal-runner-test/formal_nav",
                "task_id": task["task_id"], "action_id": "dispatch",
                "status": "succeeded", "completed_at_s": 16.0,
                "episode_id": episode_id,
                "evidence_refs": [f"native:{episode_id}:formal_nav"],
            },
            "runtime_state": {"robot_regions": self.regions, "object_states": self.objects},
        }


def test_formal_runner_keeps_one_episode_identity_and_pending_release(tmp_path: Path):
    bundle = build_goal_task_package("warehouse_nvd")
    public, api = bundle["public_task"], bundle["participant_api"]
    robot = "warehouse_ground_1"
    plan = {
        "schema": "eai.benchmark.goal_plan", "version": GOAL_PLAN_VERSION,
        "plan_id": "formal-runner-plan", "assigned_team": [robot],
        "tasks": [{
            "task_id": "formal_nav", "skill": "ground_navigation",
            "arguments": {"region_id": "consolidation_area",
                           "route_ids": ["rack_a_to_consolidation"]},
            "required_capabilities": ["ground_navigation"], "assigned_agents": [robot],
            "prerequisites": [], "evidence_refs": [],
            "execution": {"start_s": 0.0, "duration_s": 16.0},
        }],
    }
    regions = {row["id"]: "rack_aisle_a" for row in public["candidate_robot_pool"]}
    objects = {row["id"]: {"region_id": row["physical_properties"]["initial_region_id"]}
               for row in public["public_world"]["objects"]}
    episode_id = "formal-runner-warehouse-001"
    root = tmp_path / "episode"
    runner = FormalGoalEpisodeRunner(
        public, api, _Adapter(plan, robot), _Backend(public, robot, regions, objects),
        output_root=root, episode_id=episode_id)
    result = runner.run(initial_state={"robot_regions": regions, "object_states": objects})

    assert result["episode_id"] == episode_id
    assert result["error"] is None
    assert result["skill_receipt_count"] == 1
    assert result["execution_validated"] is False
    assert result["scoreable"] is False
    assert '"episode_id": "formal-runner-warehouse-001"' in (root / "skill_receipts.json").read_text()
    assert '"scoreable": false' in (root / "business_evidence.json").read_text()
    assert '"episode_id": "formal-runner-warehouse-001"' in (root / "participant_trace.json").read_text()
    assert '"episode_id": "formal-runner-warehouse-001"' in (root / "hidden_evaluator_trace.json").read_text()


def test_subprocess_goal_adapter_exposes_only_public_json_boundary(tmp_path: Path):
    script = tmp_path / "planner.py"
    script.write_text(
        "import json, sys\n"
        "payload = json.load(sys.stdin)\n"
        "assert 'public_task' in payload['context'] and 'events' in payload['context']\n"
        "print(json.dumps({'actions': []}))\n",
        encoding="utf-8")
    adapter = SubprocessGoalAdapter(
        [sys.executable, str(script)],
        {
            "kind": "participant", "id": "subprocess-goal-test", "version": 1,
            "interface": "eai.benchmark.goal_algorithm_adapter",
            "interface_version": 1, "evaluation_track": "goal_driven",
            "input_scope": "participant_visible_events_only",
            "control_scope": "benchmark_managed_low_level_control",
        },
        timeout_s=5.0, episode_id="episode-subprocess")
    bundle = build_goal_task_package("factory")
    adapter.initialize(bundle["public_task"], bundle["participant_api"])
    assert adapter.act([{"type": "episode_started", "time_s": 0.0}]) == []


def test_failed_skill_publishes_only_observable_facts_and_requires_fresh_state(tmp_path: Path):
    bundle = build_goal_task_package("warehouse_nvd")
    public, api = bundle["public_task"], bundle["participant_api"]
    robot = "warehouse_ground_1"
    plan = {
        "schema": "eai.benchmark.goal_plan", "version": GOAL_PLAN_VERSION,
        "plan_id": "overload-plan", "assigned_team": [robot], "tasks": [{
            "task_id": "formal_nav", "skill": "ground_navigation",
            "arguments": {"region_id": "consolidation_area", "route_ids": ["rack_a_to_consolidation"]},
            "required_capabilities": ["ground_navigation"], "assigned_agents": [robot],
            "prerequisites": [], "evidence_refs": [],
            "execution": {"start_s": 0.0, "duration_s": 16.0},
        }],
    }
    regions = {row["id"]: "rack_aisle_a" for row in public["candidate_robot_pool"]}
    objects = {row["id"]: {"region_id": row["physical_properties"]["initial_region_id"]}
               for row in public["public_world"]["objects"]}

    class Participant(_Adapter):
        def __init__(self):
            super().__init__(plan, robot)
            self.observed = []

        def act(self, events):
            self.observed.extend(events)
            return super().act(events)

    class FailedBackend(_Backend):
        def execute(self, task, *, episode_id, trusted_report):
            result = super().execute(task, episode_id=episode_id, trusted_report=trusted_report)
            result["status"] = result["receipt"]["status"] = "failed"
            result["public_observation"] = {
                "skill_succeeded": False, "object_moved": False,
                "task_advanced": False, "carrier_load_monitor": [],
            }
            del result["runtime_state"]
            return result

    participant = Participant()
    root = tmp_path / "failed"
    result = FormalGoalEpisodeRunner(
        public, api, participant, FailedBackend(public, robot, regions, objects),
        output_root=root).run(initial_state={"robot_regions": regions, "object_states": objects})
    assert result["error"] is None
    report = json.loads((root / "goal_session_report.json").read_text())["report"]
    assert report["runtime_snapshot_refresh_required"] is True
    observations = [row for row in participant.observed if row["type"] == "observation"]
    assert len(observations) == 1
    assert observations[0]["payload"]["data"] == {
        "action_id": "dispatch", "skill_succeeded": False,
        "object_moved": False, "task_advanced": False, "carrier_load_monitor": [],
    }
    assert "receipt_ref" not in json.dumps(participant.observed)
    assert json.loads((root / "episode_manifest.json").read_text())["version"] == 3


@pytest.mark.parametrize("mutation", [
    {"recommendation": "add carrier"},
    {"object_moved": "false"},
    {"carrier_load_monitor": [{"agent_id": "warehouse_ground_1",
                               "requested_load_kg": float("nan"), "effective_payload_kg": 8.0}]},
])
def test_skill_observation_rejects_untrusted_or_prescriptive_fields(mutation):
    observation = {"skill_succeeded": False, "object_moved": False,
                   "task_advanced": False, "carrier_load_monitor": []}
    observation.update(mutation)
    with pytest.raises(ValueError, match="observation|monitor"):
        _public_skill_observation(observation, {"assigned_agents": ["warehouse_ground_1"]}, "failed")


def test_invalid_backend_observation_does_not_commit_terminal_receipt(tmp_path: Path):
    bundle = build_goal_task_package("warehouse_nvd")
    public, api = bundle["public_task"], bundle["participant_api"]
    robot = "warehouse_ground_1"
    plan = {"schema": "eai.benchmark.goal_plan", "version": GOAL_PLAN_VERSION,
            "plan_id": "invalid-feedback-plan", "assigned_team": [robot], "tasks": [{
                "task_id": "formal_nav", "skill": "ground_navigation",
                "arguments": {"region_id": "consolidation_area",
                              "route_ids": ["rack_a_to_consolidation"]},
                "required_capabilities": ["ground_navigation"], "assigned_agents": [robot],
                "prerequisites": [], "evidence_refs": [],
                "execution": {"start_s": 0.0, "duration_s": 16.0},
            }]}
    regions = {row["id"]: "rack_aisle_a" for row in public["candidate_robot_pool"]}
    objects = {row["id"]: {"region_id": row["physical_properties"]["initial_region_id"]}
               for row in public["public_world"]["objects"]}

    class InvalidBackend(_Backend):
        def execute(self, task, *, episode_id, trusted_report):
            result = super().execute(task, episode_id=episode_id, trusted_report=trusted_report)
            result["status"] = result["receipt"]["status"] = "failed"
            result["public_observation"] = {"recommendation": "add a carrier"}
            return result

    root = tmp_path / "invalid"
    result = FormalGoalEpisodeRunner(
        public, api, _Adapter(plan, robot), InvalidBackend(public, robot, regions, objects),
        output_root=root).run(initial_state={"robot_regions": regions, "object_states": objects})
    assert "skill observation" in result["error"]
    assert result["skill_receipt_count"] == 0
    report = json.loads((root / "goal_session_report.json").read_text())["report"]
    assert report["tasks"]["formal_nav"]["status"] == "pending"
    assert not any(row["type"] == "observation" for row in report["events"])


def test_invalid_backend_snapshot_does_not_commit_terminal_receipt(tmp_path: Path):
    bundle = build_goal_task_package("warehouse_nvd")
    public, api = bundle["public_task"], bundle["participant_api"]
    robot = "warehouse_ground_1"
    plan = {"schema": "eai.benchmark.goal_plan", "version": GOAL_PLAN_VERSION,
            "plan_id": "invalid-state-plan", "assigned_team": [robot], "tasks": [{
                "task_id": "formal_nav", "skill": "ground_navigation",
                "arguments": {"region_id": "consolidation_area",
                              "route_ids": ["rack_a_to_consolidation"]},
                "required_capabilities": ["ground_navigation"], "assigned_agents": [robot],
                "prerequisites": [], "evidence_refs": [],
                "execution": {"start_s": 0.0, "duration_s": 16.0},
            }]}
    regions = {row["id"]: "rack_aisle_a" for row in public["candidate_robot_pool"]}
    objects = {row["id"]: {"region_id": row["physical_properties"]["initial_region_id"]}
               for row in public["public_world"]["objects"]}

    class InvalidStateBackend(_Backend):
        def execute(self, task, *, episode_id, trusted_report):
            result = super().execute(task, episode_id=episode_id, trusted_report=trusted_report)
            result["runtime_state"]["object_states"]["order_crate_01"]["region_id"] = "unbound_region"
            return result

    root = tmp_path / "invalid-state"
    result = FormalGoalEpisodeRunner(
        public, api, _Adapter(plan, robot), InvalidStateBackend(public, robot, regions, objects),
        output_root=root).run(initial_state={"robot_regions": regions, "object_states": objects})
    assert "unknown region" in result["error"]
    assert result["skill_receipt_count"] == 0
    report = json.loads((root / "goal_session_report.json").read_text())["report"]
    assert report["tasks"]["formal_nav"]["status"] == "pending"
    assert report["current_time_s"] == 0.0
    assert not any(row["type"] == "observation" for row in report["events"])


@pytest.mark.parametrize("stale_second", [False, True])
def test_pending_skills_execute_serially_with_monotonic_receipts(tmp_path: Path, stale_second):
    bundle = build_goal_task_package("warehouse_nvd")
    public, api = bundle["public_task"], bundle["participant_api"]
    first, second = "warehouse_ground_1", "warehouse_ground_3"
    tasks = [
        {"task_id": tid, "skill": "ground_navigation",
         "arguments": {"region_id": destination, "route_ids": [route]},
         "required_capabilities": ["ground_navigation"], "assigned_agents": [agent],
         "prerequisites": [], "evidence_refs": [],
         "execution": {"start_s": 0.0, "duration_s": 30.0}}
        for tid, agent, destination, route in (
            ("first", first, "rack_aisle_a", "consolidation_to_rack_a"),
            ("second", second, "rack_aisle_b", "consolidation_to_rack_b"))]
    plan = {"schema": "eai.benchmark.goal_plan", "version": GOAL_PLAN_VERSION,
            "plan_id": "serial-skills", "assigned_team": [first, second], "tasks": tasks}
    regions = {row["id"]: ("rack_overview" if row["mobility"] == "aerial"
                           else "consolidation_area") for row in public["candidate_robot_pool"]}
    objects = {row["id"]: {"region_id": row["physical_properties"]["initial_region_id"]}
               for row in public["public_world"]["objects"]}

    class Participant(_Adapter):
        def act(self, events):
            if self.sent:
                return []
            self.sent = True
            return [_action("team", "select_team", {"agent_ids": [first, second]}),
                    _action("plan", "submit_plan", {"plan": plan}),
                    _action("dispatch-first", "dispatch_skill", {
                        "task_id": "first", "evidence_refs": []}),
                    _action("dispatch-second", "dispatch_skill", {
                        "task_id": "second", "evidence_refs": []})]

    class SerialBackend:
        def __init__(self):
            self.calls = []

        def execute(self, task, *, episode_id, trusted_report):
            clock = trusted_report["session"]["current_time_s"]
            self.calls.append((task["task_id"], clock))
            completed = (12.0 if task["task_id"] == "first" else
                         2.0 if stale_second else clock + 2.0)
            return {"status": "succeeded", "completed_at_s": completed,
                    "receipt": {"backend_id": "serial-test", "receipt_ref": task["task_id"],
                                "task_id": task["task_id"],
                                "action_id": f"dispatch-{task['task_id']}",
                                "status": "succeeded", "completed_at_s": completed,
                                "episode_id": episode_id,
                                "evidence_refs": [f"measured:{task['task_id']}"]},
                    "runtime_state": {"robot_regions": regions, "object_states": objects}}

    backend = SerialBackend()
    root = tmp_path / "serial"
    result = FormalGoalEpisodeRunner(
        public, api, Participant(plan, first), backend, output_root=root,
    ).run(initial_state={"robot_regions": regions, "object_states": objects})
    assert backend.calls == [("first", 0.0), ("second", 12.0)]
    report = json.loads((root / "goal_session_report.json").read_text())["report"]
    if stale_second:
        assert "nonmonotonic completion time" in result["error"]
        assert result["skill_receipt_count"] == 1
        assert report["tasks"]["second"]["status"] == "pending"
    else:
        assert result["error"] is None
        assert result["skill_receipt_count"] == 2
        assert [row["completed_at_s"] for row in json.loads(
            (root / "skill_receipts.json").read_text())] == [12.0, 14.0]
        assert report["tasks"]["second"]["status"] == "succeeded"


@pytest.mark.parametrize("supports_cancel", [False, True])
def test_cancellation_never_calls_ordinary_skill_execution(tmp_path: Path, supports_cancel):
    bundle = build_goal_task_package("warehouse_nvd")
    public, api = bundle["public_task"], bundle["participant_api"]
    first, second = "warehouse_ground_1", "warehouse_ground_3"
    tasks = [{"task_id": tid, "skill": "ground_navigation",
              "arguments": {"region_id": destination, "route_ids": [route]},
              "required_capabilities": ["ground_navigation"], "assigned_agents": [agent],
              "prerequisites": [], "evidence_refs": [],
              "execution": {"start_s": 0.0, "duration_s": 30.0}}
             for tid, agent, destination, route in (
                 ("first", first, "rack_aisle_a", "consolidation_to_rack_a"),
                 ("second", second, "rack_aisle_b", "consolidation_to_rack_b"))]
    plan = {"schema": "eai.benchmark.goal_plan", "version": GOAL_PLAN_VERSION,
            "plan_id": "cancel-plan", "assigned_team": [first, second], "tasks": tasks}
    regions = {row["id"]: ("rack_overview" if row["mobility"] == "aerial"
                           else "consolidation_area") for row in public["candidate_robot_pool"]}
    objects = {row["id"]: {"region_id": row["physical_properties"]["initial_region_id"]}
               for row in public["public_world"]["objects"]}

    class Participant(_Adapter):
        def __init__(self):
            super().__init__(plan, first)
            self.cancelled = False

        def act(self, events):
            if not self.sent:
                self.sent = True
                return [_action("team", "select_team", {"agent_ids": [first, second]}),
                        _action("plan", "submit_plan", {"plan": plan}),
                        _action("dispatch-first", "dispatch_skill", {
                            "task_id": "first", "evidence_refs": []}),
                        _action("dispatch-second", "dispatch_skill", {
                            "task_id": "second", "evidence_refs": []})]
            if not self.cancelled and any(row["type"] == "action_result"
                    and row["payload"]["action_id"] == "dispatch-first"
                    and row["payload"]["status"] == "succeeded" for row in events):
                self.cancelled = True
                return [_action("cancel-second", "cancel_skill", {
                    "task_id": "second", "reason": "first result changed the plan"},
                    submitted_at_s=12.0)]
            return []

    def terminal(task, episode_id, status, now):
        return {"status": status, "completed_at_s": now,
                "receipt": {"backend_id": "cancel-test", "receipt_ref": task["task_id"],
                            "task_id": task["task_id"],
                            "action_id": f"dispatch-{task['task_id']}",
                            "status": status, "completed_at_s": now,
                            "episode_id": episode_id,
                            "evidence_refs": [f"measured:{task['task_id']}"]},
                "runtime_state": {"robot_regions": regions, "object_states": objects}}

    class Backend:
        def __init__(self):
            self.executed = []
            self.cancelled = []

        def execute(self, task, *, episode_id, trusted_report):
            self.executed.append(task["task_id"])
            assert trusted_report["session"]["current_time_s"] == 0.0
            return terminal(task, episode_id, "succeeded", 12.0)

    class CancellingBackend(Backend):
        def cancel(self, task, *, episode_id, trusted_report):
            self.cancelled.append(task["task_id"])
            assert trusted_report["session"]["tasks"][task["task_id"]]["status"] == "cancel_requested"
            return terminal(task, episode_id, "cancelled", 12.0)

    backend = CancellingBackend() if supports_cancel else Backend()
    root = tmp_path / "cancellation"
    result = FormalGoalEpisodeRunner(
        public, api, Participant(), backend, output_root=root,
    ).run(initial_state={"robot_regions": regions, "object_states": objects})
    report = json.loads((root / "goal_session_report.json").read_text())["report"]
    assert backend.executed == ["first"]
    if supports_cancel:
        assert result["error"] is None
        assert backend.cancelled == ["second"]
        assert result["skill_receipt_count"] == 2
        assert report["tasks"]["second"]["status"] == "cancelled"
    else:
        assert "cannot acknowledge cancellation" in result["error"]
        assert result["skill_receipt_count"] == 1
        assert report["tasks"]["second"]["status"] == "cancel_requested"


def test_immediate_cancellation_is_acknowledged_without_execution(tmp_path: Path):
    bundle = build_goal_task_package("warehouse_nvd")
    public, api = bundle["public_task"], bundle["participant_api"]
    robot = "warehouse_ground_1"
    task = {"task_id": "formal_nav", "skill": "ground_navigation",
            "arguments": {"region_id": "consolidation_area",
                          "route_ids": ["rack_a_to_consolidation"]},
            "required_capabilities": ["ground_navigation"], "assigned_agents": [robot],
            "prerequisites": [], "evidence_refs": [],
            "execution": {"start_s": 0.0, "duration_s": 16.0}}
    plan = {"schema": "eai.benchmark.goal_plan", "version": GOAL_PLAN_VERSION,
            "plan_id": "immediate-cancel", "assigned_team": [robot], "tasks": [task]}
    regions = {row["id"]: "rack_aisle_a" for row in public["candidate_robot_pool"]}
    objects = {row["id"]: {"region_id": row["physical_properties"]["initial_region_id"]}
               for row in public["public_world"]["objects"]}

    class Participant(_Adapter):
        def act(self, events):
            if self.sent:
                return []
            self.sent = True
            return [_action("team", "select_team", {"agent_ids": [robot]}),
                    _action("plan", "submit_plan", {"plan": plan}),
                    _action("dispatch", "dispatch_skill", {
                        "task_id": task["task_id"], "evidence_refs": []}),
                    _action("cancel", "cancel_skill", {
                        "task_id": task["task_id"], "reason": "request withdrawn"})]

    class Backend:
        def execute(self, task, *, episode_id, trusted_report):
            pytest.fail("cancelled skill reached ordinary execution")

        def cancel(self, task, *, episode_id, trusted_report):
            assert trusted_report["session"]["tasks"][task["task_id"]]["status"] == "cancel_requested"
            return {"status": "cancelled", "completed_at_s": 0.0,
                    "receipt": {"backend_id": "cancel-test", "receipt_ref": "immediate",
                                "task_id": task["task_id"], "action_id": "dispatch",
                                "status": "cancelled", "completed_at_s": 0.0,
                                "episode_id": episode_id,
                                "evidence_refs": ["measured:no-execution"]}}

    root = tmp_path / "immediate-cancel"
    result = FormalGoalEpisodeRunner(
        public, api, Participant(plan, robot), Backend(), output_root=root,
    ).run(initial_state={"robot_regions": regions, "object_states": objects})
    assert result["error"] is None and result["skill_receipt_count"] == 1
    report = json.loads((root / "goal_session_report.json").read_text())["report"]
    assert report["tasks"]["formal_nav"]["status"] == "cancelled"


def test_failed_observation_can_trigger_same_episode_replanning_with_fresh_state(tmp_path: Path):
    bundle = build_goal_task_package("warehouse_nvd")
    public, api = bundle["public_task"], bundle["participant_api"]
    first, second = "warehouse_ground_1", "warehouse_ground_3"
    regions = {row["id"]: "rack_aisle_a" for row in public["candidate_robot_pool"]}
    objects = {row["id"]: {"region_id": row["physical_properties"]["initial_region_id"]}
               for row in public["public_world"]["objects"]}

    def plan(identifier, task_id, team, skill, arguments, start):
        return {"schema": "eai.benchmark.goal_plan", "version": GOAL_PLAN_VERSION,
                "plan_id": identifier, "assigned_team": team, "tasks": [{
                    "task_id": task_id, "skill": skill, "arguments": arguments,
                    "required_capabilities": (["pick"] if skill == "pick"
                                              else ["cooperative_carry"]),
                    "assigned_agents": team, "prerequisites": [], "evidence_refs": [],
                    "execution": {"start_s": start, "duration_s": 20.0},
                }]}

    class Replanner(_Adapter):
        def __init__(self):
            super().__init__(None, first)
            self.replanned = False

        def act(self, events):
            if not self.sent:
                self.sent = True
                initial = plan("first-plan", "first-pick", [first], "pick", {
                    "object_id": "order_crate_01", "pickup_region_id": "rack_aisle_a",
                    "handling_mode_id": "single_grip",
                    "carrier_bindings": [{"agent_id": first, "point_id": "center_grip"}],
                }, 0.0)
                return [_action("select-first", "select_team", {"agent_ids": [first]}),
                        _action("submit-first", "submit_plan", {"plan": initial}),
                        _action("dispatch-first", "dispatch_skill", {
                            "task_id": "first-pick", "evidence_refs": []})]
            observed = [event for event in events if event["type"] == "observation"
                        and event["payload"]["kind"] == "skill_execution"]
            if not observed or self.replanned:
                return []
            self.replanned = True
            failure = observed[0]
            assert failure["payload"]["data"]["object_moved"] is False
            revised = plan("recovery-plan", "paired-carry", [first, second],
                           "cooperative_carry", {
                               "object_id": "order_crate_01",
                               "destination_region_id": "consolidation_area",
                               "handling_mode_id": "paired_support",
                               "route_ids": ["rack_a_to_consolidation"],
                               "carrier_bindings": [
                                   {"agent_id": first, "point_id": "support_left"},
                                   {"agent_id": second, "point_id": "support_right"}],
                           }, failure["time_s"])
            now = failure["time_s"]
            return [_action("select-pair", "select_team", {"agent_ids": [first, second]},
                            submitted_at_s=now),
                    _action("revise-pair", "revise_plan", {
                        "trigger_event_sequence": [failure["sequence"]],
                        "replaced_plan_id": "first-plan", "reason": "observed failed attempt",
                        "plan": revised}, submitted_at_s=now),
                    _action("dispatch-pair", "dispatch_skill", {
                        "task_id": "paired-carry", "evidence_refs": []}, submitted_at_s=now)]

    class FailedBackend(_Backend):
        def execute(self, task, *, episode_id, trusted_report):
            result = super().execute(task, episode_id=episode_id, trusted_report=trusted_report)
            result["status"] = result["receipt"]["status"] = "failed"
            result["receipt"]["action_id"] = trusted_report["session"]["tasks"][
                task["task_id"]]["dispatch_action_id"]
            result["receipt"]["receipt_ref"] = f"{episode_id}/{task['task_id']}"
            result["receipt"]["completed_at_s"] = result["completed_at_s"] = (
                4.0 if task["task_id"] == "first-pick" else 8.0)
            result["public_observation"] = {
                "skill_succeeded": False, "object_moved": False,
                "task_advanced": False, "carrier_load_monitor": [],
            }
            return result

    participant = Replanner()
    root = tmp_path / "replanned"
    result = FormalGoalEpisodeRunner(
        public, api, participant, FailedBackend(public, first, regions, objects),
        output_root=root).run(initial_state={"robot_regions": regions, "object_states": objects})
    assert result["error"] is None
    assert result["skill_receipt_count"] == 2
    assert participant.replanned is True
    report = json.loads((root / "goal_session_report.json").read_text())["report"]
    assert {row["task"]["task_id"]: row["status"] for row in report["tasks"].values()} == {
        "first-pick": "failed", "paired-carry": "failed"}
    assert report["runtime_snapshot_refresh_required"] is False
    assert result["execution_validated"] is False and result["scoreable"] is False
