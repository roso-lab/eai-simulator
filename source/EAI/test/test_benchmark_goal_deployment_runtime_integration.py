"""Formal runner integration checks for dynamic deployment invalidation."""

from copy import deepcopy
import json

import pytest

import demo.benchmark.goal_episode_runner as runner_module
from demo.benchmark.goal_algorithm_adapter import GoalAlgorithmHost
from demo.benchmark.goal_deployment_assessment import enable_deployment_assessment
from demo.benchmark.goal_driven_contract import (
    GOAL_PLAN_VERSION,
    build_goal_participant_api,
)
from demo.benchmark.goal_driven_tasks import build_goal_task_package
from demo.benchmark.goal_episode_runner import FormalGoalEpisodeRunner


GROUND = "warehouse_ground_1"
AERIAL = "warehouse_aerial_1"
FIRST_TASK = "queued-ground-navigation"
SECOND_TASK = "queued-aerial-navigation"


def _action(action_id, action_type, payload, *, time_s):
    return {
        "schema": "eai.benchmark.goal_action",
        "version": 1,
        "action_id": action_id,
        "submitted_at_s": time_s,
        "type": action_type,
        "payload": payload,
    }


def _assessment(event, decision):
    payload = event["payload"]
    return _action(
        f"assessment-{payload['assessment_epoch']}",
        "deployment_assessment",
        {
            "assessment_id": payload["assessment_id"],
            "assessment_epoch": payload["assessment_epoch"],
            "roster_version": payload["roster_version"],
            "capability_versions": payload["capability_versions"],
            "decision": decision,
            "reason_codes": ([] if decision == "deployable"
                             else ["lost_unique_capability"]),
            "evidence_refs": ([] if decision == "deployable" else [AERIAL]),
        },
        time_s=event["time_s"],
    )


def _fixture():
    bundle = build_goal_task_package("warehouse_nvd")
    public = enable_deployment_assessment(bundle["public_task"])
    api = build_goal_participant_api(public)
    plan = {
        "schema": "eai.benchmark.goal_plan",
        "version": GOAL_PLAN_VERSION,
        "plan_id": "dynamic-deployment-plan",
        "assigned_team": [GROUND, AERIAL],
        "tasks": [
            {
                "task_id": FIRST_TASK,
                "skill": "ground_navigation",
                "arguments": {
                    "region_id": "consolidation_area",
                    "route_ids": ["rack_a_to_consolidation"],
                },
                "required_capabilities": ["ground_navigation"],
                "assigned_agents": [GROUND],
                "prerequisites": [],
                "evidence_refs": [],
                "execution": {"start_s": 0.0, "duration_s": 8.0},
            },
            {
                "task_id": SECOND_TASK,
                "skill": "aerial_navigation",
                "arguments": {
                    "region_id": "rack_overview",
                    "route_ids": ["aisle_to_overview_air"],
                },
                "required_capabilities": ["aerial_navigation"],
                "assigned_agents": [AERIAL],
                "prerequisites": [],
                "evidence_refs": [],
                "execution": {"start_s": 0.0, "duration_s": 8.0},
            },
        ],
    }
    regions = {
        robot["id"]: next(
            region["id"] for region in public["public_world"]["regions"]
            if ("aerial_navigation" if robot["mobility"] == "aerial"
                else "ground_navigation") in region["access_modes"])
        for robot in public["candidate_robot_pool"]
    }
    regions[GROUND] = regions[AERIAL] = "rack_aisle_a"
    objects = {
        row["id"]: {"region_id": row["physical_properties"]["initial_region_id"]}
        for row in public["public_world"]["objects"]
    }
    return public, api, plan, {
        "robot_regions": regions,
        "object_states": objects,
    }


class _DynamicAdapter:
    def __init__(self, plan, reassessment):
        self.plan = deepcopy(plan)
        self.reassessment = reassessment
        self.declared_epochs = set()
        self.plan_sent = False

    def manifest(self):
        return {
            "kind": "participant",
            "id": "dynamic-deployment-test",
            "version": 1,
            "interface": "eai.benchmark.goal_algorithm_adapter",
            "interface_version": 1,
            "evaluation_track": "goal_driven",
            "input_scope": "participant_visible_events_only",
            "control_scope": "benchmark_managed_low_level_control",
        }

    def initialize(self, public_task, participant_api):
        self.public_task = deepcopy(public_task)
        self.participant_api = deepcopy(participant_api)

    def act(self, events):
        required = next(
            (row for row in reversed(events)
             if row["type"] == "deployment_assessment_required"
             and row["payload"]["assessment_epoch"] not in self.declared_epochs),
            None,
        )
        if required is not None:
            epoch = required["payload"]["assessment_epoch"]
            self.declared_epochs.add(epoch)
            if epoch == 0:
                return [_assessment(required, "deployable")]
            if self.reassessment == "raise":
                raise RuntimeError("participant failed during reassessment")
            if self.reassessment == "silent":
                return []
            return [_assessment(required, "not_deployable")]

        if self.plan_sent or not any(
                row["type"] == "action_result"
                and row["payload"]["action_id"] == "assessment-0"
                for row in events):
            return []
        self.plan_sent = True
        now = max(row["time_s"] for row in events)
        return [
            _action("select", "select_team", {"agent_ids": [GROUND, AERIAL]},
                    time_s=now),
            _action("plan", "submit_plan", {"plan": self.plan}, time_s=now),
            _action("dispatch-ground", "dispatch_skill",
                    {"task_id": FIRST_TASK, "evidence_refs": []}, time_s=now),
            _action("dispatch-aerial", "dispatch_skill",
                    {"task_id": SECOND_TASK, "evidence_refs": []}, time_s=now),
        ]

    def close(self):
        pass


class _EpochOracle:
    def __init__(self):
        self.calls = []

    def __call__(self, episode_id, trusted_report):
        snapshot = deepcopy(trusted_report)
        self.calls.append((episode_id, snapshot))
        gate = snapshot["session"]["deployment_assessment"]
        epoch = gate["assessment_epoch"]
        return {
            "assessment_id": gate["assessment_id"],
            "assessment_epoch": epoch,
            "feasibility_label": ("deployable" if epoch == 0 else "not_deployable"),
            "oracle_status": "verified",
            "platform_valid": True,
            "evidence_refs": [f"oracle:epoch-{epoch}"],
            "evaluator_ref": "test.registered.dynamic-oracle",
        }


class _DynamicBackend:
    def __init__(self, host_box, event_type):
        self.host_box = host_box
        self.event_type = event_type
        self.execute_calls = []
        self.cancel_calls = []

    @staticmethod
    def _receipt(task, *, episode_id, trusted_report, status, completed_at_s):
        task_id = task["task_id"]
        action_id = trusted_report["session"]["tasks"][task_id][
            "dispatch_action_id"]
        return {
            "backend_id": "dynamic-deployment-backend",
            "receipt_ref": f"dynamic/{task_id}/{status}",
            "task_id": task_id,
            "action_id": action_id,
            "status": status,
            "completed_at_s": completed_at_s,
            "episode_id": episode_id,
            "evidence_refs": [f"backend:{task_id}:{status}"],
        }

    def execute(self, task, *, episode_id, trusted_report):
        self.execute_calls.append(task["task_id"])
        assert task["task_id"] == FIRST_TASK
        self.host_box["event_request"] = {
            "type": self.event_type,
            "agent_id": AERIAL,
        }
        completed_at_s = 4.0
        return {
            "status": "succeeded",
            "completed_at_s": completed_at_s,
            "receipt": self._receipt(
                task, episode_id=episode_id, trusted_report=trusted_report,
                status="succeeded", completed_at_s=completed_at_s),
        }

    def cancel(self, task, *, episode_id, trusted_report):
        self.cancel_calls.append(task["task_id"])
        completed_at_s = trusted_report["session"]["current_time_s"]
        return {
            "status": "cancelled",
            "completed_at_s": completed_at_s,
            "receipt": self._receipt(
                task, episode_id=episode_id, trusted_report=trusted_report,
                status="cancelled", completed_at_s=completed_at_s),
        }


def _install_trusted_event_host(monkeypatch, host_box):
    class _TrustedEventHost(GoalAlgorithmHost):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            host_box["host"] = self

        def complete_skill(self, task_id, *, time_s, status, receipt):
            result = super().complete_skill(
                task_id, time_s=time_s, status=status, receipt=receipt)
            request = host_box.pop("event_request", None)
            if request is None:
                return result
            if request["type"] == "member_exit":
                self.emit("member_exit", time_s, {"agent_id": request["agent_id"]})
            else:
                report = self.trusted_report()["session"]
                declaration = deepcopy(
                    report["current_robot_declarations"][request["agent_id"]])
                declaration["public_capabilities"] = [
                    capability for capability in declaration["public_capabilities"]
                    if capability != "aerial_navigation"
                ]
                self.emit("capability_change", time_s, {
                    "agent_id": request["agent_id"],
                    "capability_declaration": declaration,
                    "capability_version": (
                        report["capability_versions"][request["agent_id"]] + 1),
                })
            return result

    monkeypatch.setattr(runner_module, "GoalAlgorithmHost", _TrustedEventHost)


def _run_dynamic(tmp_path, monkeypatch, *, event_type, reassessment):
    public, api, plan, initial_state = _fixture()
    host_box = {}
    _install_trusted_event_host(monkeypatch, host_box)
    backend = _DynamicBackend(host_box, event_type)
    oracle = _EpochOracle()
    root = tmp_path / f"{event_type}-{reassessment}"
    result = FormalGoalEpisodeRunner(
        public,
        api,
        _DynamicAdapter(plan, reassessment),
        backend,
        output_root=root,
        assessment_evaluator=oracle,
    ).run(initial_state=initial_state)
    report = json.loads((root / "goal_session_report.json").read_text())["report"]
    hidden = json.loads((root / "hidden_evaluator_trace.json").read_text())
    receipts = json.loads((root / "skill_receipts.json").read_text())
    return result, report, hidden, receipts, backend, oracle, root


@pytest.mark.parametrize("event_type", ["capability_change", "member_exit"])
def test_dynamic_loss_reassesses_then_cancels_queued_skill(
        tmp_path, monkeypatch, event_type):
    result, report, hidden, receipts, backend, oracle, _root = _run_dynamic(
        tmp_path,
        monkeypatch,
        event_type=event_type,
        reassessment="not_deployable",
    )

    assert result["error"] is None
    assert backend.execute_calls == [FIRST_TASK]
    assert backend.cancel_calls == [SECOND_TASK]
    assert [row["status"] for row in receipts] == ["succeeded", "cancelled"]
    assert report["tasks"][FIRST_TASK]["status"] == "succeeded"
    stopped = report["tasks"][SECOND_TASK]
    assert stopped["status"] == "cancelled"
    assert stopped["safety_stop"] == {
        "requested": True,
        "trigger_event_sequence": stopped["invalidated_by"][-1],
        "acknowledged": True,
    }
    gate = hidden["deployment_assessment"]
    assert [row["assessment_epoch"] for row in gate["oracle_evaluations"]] == [0, 1]
    assert [row["feasibility_label"] for row in gate["oracle_evaluations"]] == [
        "deployable", "not_deployable"]
    assert [row["decision"] for row in gate["history"]] == [
        "deployable", "not_deployable"]
    assert all(call[1]["session"]["deployment_assessment"]["status"] == "required"
               for call in oracle.calls)
    assert oracle.calls[0][1]["session"]["deployment_assessment"]["history"] == []
    second_pre_act_history = oracle.calls[1][1]["session"][
        "deployment_assessment"]["history"]
    assert [row["assessment_epoch"] for row in second_pre_act_history] == [0]
    assert len([row for row in report["events"]
                if row["type"] == "human_assistance_requested"]) == 1
    assert result["termination"]["category"] == "human_assistance_requested"
    assert "feasibility_label" not in json.dumps({
        "events": report["events"], "actions": report["actions"]})


def test_silent_reassessment_still_acknowledges_queued_cancellation(
        tmp_path, monkeypatch):
    result, report, hidden, _receipts, backend, _oracle, root = _run_dynamic(
        tmp_path,
        monkeypatch,
        event_type="member_exit",
        reassessment="silent",
    )

    assert "missing_deployment_assessment" in result["error"]
    assert backend.execute_calls == [FIRST_TASK]
    assert backend.cancel_calls == [SECOND_TASK]
    assert report["tasks"][SECOND_TASK]["status"] == "cancelled"
    assert report["tasks"][SECOND_TASK]["safety_stop"]["acknowledged"] is True
    assert len(hidden["deployment_assessment"]["oracle_evaluations"]) == 2
    assert [row["decision"] for row in hidden["deployment_assessment"]["history"]] == [
        "deployable"]
    protocol = json.loads((root / "deployment_protocol_error.json").read_text())
    assert protocol["code"] == "missing_assessment"


@pytest.mark.xfail(
    strict=True,
    reason=("FormalGoalEpisodeRunner exits on participant reassessment exception "
            "before draining cancel_requested work"),
)
def test_reassessment_exception_must_still_acknowledge_queued_cancellation(
        tmp_path, monkeypatch):
    result, report, _hidden, _receipts, backend, _oracle, _root = _run_dynamic(
        tmp_path,
        monkeypatch,
        event_type="member_exit",
        reassessment="raise",
    )

    assert "participant failed during reassessment" in result["error"]
    assert backend.execute_calls == [FIRST_TASK]
    assert backend.cancel_calls == [SECOND_TASK]
    assert report["tasks"][SECOND_TASK]["status"] == "cancelled"
    assert report["tasks"][SECOND_TASK]["safety_stop"]["acknowledged"] is True
