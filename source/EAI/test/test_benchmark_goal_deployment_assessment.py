from copy import deepcopy
import json

import pytest

from demo.benchmark.goal_algorithm_adapter import GoalAlgorithmHost
from demo.benchmark.goal_deployment_assessment import (
    enable_deployment_assessment,
    validate_assessment_evaluation,
)
from demo.benchmark.goal_driven_contract import (
    GOAL_PLAN_VERSION,
    build_goal_participant_api,
    validate_goal_task_spec,
)
from demo.benchmark.goal_driven_tasks import build_goal_task_package
from demo.benchmark.goal_episode_runner import FormalGoalEpisodeRunner
from demo.benchmark.goal_session import GoalSession
from demo.benchmark.provenance import canonical_sha256


def _v3(scene="warehouse_nvd"):
    bundle = build_goal_task_package(scene)
    public = enable_deployment_assessment(bundle["public_task"])
    return bundle, public, build_goal_participant_api(public)


def _action(session, action_id, action_type, payload):
    return {
        "schema": "eai.benchmark.goal_action",
        "version": 1,
        "action_id": action_id,
        "submitted_at_s": session.report()["current_time_s"],
        "type": action_type,
        "payload": payload,
    }


def _assessment_action(session, decision, *, action_id="assessment", reasons=None):
    current = session.report()["deployment_assessment"]
    return _action(session, action_id, "deployment_assessment", {
        "assessment_id": current["assessment_id"],
        "assessment_epoch": current["assessment_epoch"],
        "roster_version": current["roster_version"],
        "capability_versions": current["capability_versions"],
        "decision": decision,
        "reason_codes": list(reasons or []),
        "evidence_refs": [],
    })


def _submit(session, action_id, action_type, payload):
    return session.submit_action(_action(session, action_id, action_type, payload))


def _initial_state(public):
    regions = {
        robot["id"]: next(
            region["id"] for region in public["public_world"]["regions"]
            if ("aerial_navigation" if robot["mobility"] == "aerial"
                else "ground_navigation") in region["access_modes"])
        for robot in public["candidate_robot_pool"]
    }
    objects = {
        row["id"]: {"region_id": row["physical_properties"]["initial_region_id"]}
        for row in public["public_world"]["objects"]
    }
    return {"robot_regions": regions, "object_states": objects}


def test_v3_is_explicit_and_v2_task_api_hashes_and_behavior_stay_frozen():
    bundle = build_goal_task_package("factory")
    public_v2 = deepcopy(bundle["public_task"])
    api_v2 = deepcopy(bundle["participant_api"])
    public_hash = canonical_sha256(public_v2)
    api_hash = canonical_sha256(api_v2)

    public_v3 = enable_deployment_assessment(public_v2)
    api_v3 = build_goal_participant_api(public_v3)

    assert bundle["public_task"] == public_v2
    assert canonical_sha256(bundle["public_task"]) == public_hash
    assert build_goal_participant_api(public_v2) == api_v2
    assert canonical_sha256(api_v2) == api_hash
    assert public_v3["version"] == api_v3["version"] == 3
    assert "deployment_assessment" in api_v3["action_types"]
    assert "deployment_assessment_required" in api_v3["event_types"]
    assert "deployment_assessment" not in api_v2["action_types"]
    v2_session = GoalSession(public_v2, api_v2)
    assert "deployment_assessment" not in v2_session.report()
    assert "resource_versions" not in v2_session.report()
    v2_host = GoalAlgorithmHost(public_v2, api_v2, _IdleAdapter())
    assert "optional_tools" not in v2_host.trusted_report()
    v2_host.close()


def test_v3_can_publish_a_nonempty_pool_missing_aerial_capability():
    _bundle, public, _api = _v3("warehouse_nvd")
    ground = next(row for row in public["candidate_robot_pool"]
                  if row["mobility"] == "ground")
    public["candidate_robot_pool"] = [ground]
    public["budgets"]["max_active_agents"] = 1
    if "robot_spawn_positions" in public["public_world"]:
        public["public_world"]["robot_spawn_positions"] = {
            ground["id"]: public["public_world"]["robot_spawn_positions"][ground["id"]]}
    assert validate_goal_task_spec(public)["validated"] is True
    api = build_goal_participant_api(public)
    assert api["version"] == 3

    empty = deepcopy(public)
    empty["candidate_robot_pool"] = []
    empty["budgets"]["max_active_agents"] = 0
    if "robot_spawn_positions" in empty["public_world"]:
        empty["public_world"]["robot_spawn_positions"] = {}
    with pytest.raises(ValueError, match="nonempty candidate robot pool|nonempty"):
        validate_goal_task_spec(empty)


def test_trusted_oracle_result_is_binary_only_when_verified():
    verified = {
        "assessment_id": "deployment-assessment-0",
        "assessment_epoch": 0,
        "feasibility_label": "deployable",
        "oracle_status": "verified",
        "platform_valid": True,
        "evidence_refs": ["oracle:registered-case"],
        "evaluator_ref": "test.oracle",
    }
    assert validate_assessment_evaluation(
        verified, assessment_id=verified["assessment_id"], assessment_epoch=0,
    ) == verified

    failed = {**verified, "feasibility_label": None, "oracle_status": "failed",
              "platform_valid": False, "evidence_refs": []}
    assert validate_assessment_evaluation(
        failed, assessment_id=failed["assessment_id"], assessment_epoch=0,
    ) == failed

    for invalid in (
        {**verified, "feasibility_label": "unverified"},
        {**verified, "feasibility_label": None},
        {**verified, "platform_valid": False},
        {**verified, "evidence_refs": []},
        {**verified, "assessment_epoch": False},
        {**verified, "evidence_refs": [" "]},
        {**verified, "evaluator_ref": " "},
        {**failed, "feasibility_label": "not_deployable"},
    ):
        with pytest.raises(ValueError):
            validate_assessment_evaluation(
                invalid, assessment_id=invalid["assessment_id"], assessment_epoch=0)


def test_oracle_ledger_keeps_superseded_unanswered_epochs():
    _bundle, public, api = _v3()
    session = GoalSession(public, api)
    gate = session.report()["deployment_assessment"]
    first = {
        "assessment_id": gate["assessment_id"],
        "assessment_epoch": gate["assessment_epoch"],
        "feasibility_label": "deployable",
        "oracle_status": "verified",
        "platform_valid": True,
        "evidence_refs": ["oracle:epoch-0"],
        "evaluator_ref": "test.oracle",
    }
    session.record_deployment_assessment_evaluation(first)
    agent = gate["current_agent_ids"][0]
    declaration = deepcopy(session.report()["current_robot_declarations"][agent])
    session.emit("capability_change", 0.0, {
        "agent_id": agent,
        "capability_declaration": declaration,
        "capability_version": 1,
    })

    current = session.report()["deployment_assessment"]
    assert current["assessment_epoch"] == 1
    assert current["external_evaluation"] is None
    assert current["history"] == []
    assert current["oracle_evaluations"] == [first]
    second = {
        "assessment_id": current["assessment_id"],
        "assessment_epoch": current["assessment_epoch"],
        "feasibility_label": None,
        "oracle_status": "failed",
        "platform_valid": False,
        "evidence_refs": ["oracle:platform-invalid"],
        "evaluator_ref": "test.oracle",
    }
    session.record_deployment_assessment_evaluation(second)
    ledger = session.report()["deployment_assessment"]["oracle_evaluations"]
    assert ledger == [first, second]
    assert all(set(row) == {
        "assessment_id", "assessment_epoch", "feasibility_label",
        "oracle_status", "platform_valid", "evidence_refs", "evaluator_ref",
    } for row in ledger)


def test_initial_assessment_blocks_operational_actions_and_deployable_opens_gate():
    bundle, public, api = _v3()
    session = GoalSession(public, api)
    plan = bundle["hidden_evaluator"]["witness_plans"][0]["plan"]
    current = session.report()["deployment_assessment"]
    assert current["status"] == "required"
    assert current["assessment_epoch"] == 0
    assert set(current["current_robot_pool"]) == set(current["current_agent_ids"])
    assert current["public_task_sha256"] == canonical_sha256(public)
    with pytest.raises(ValueError, match="deployment_assessment is required"):
        _submit(session, "select-too-early", "select_team",
                {"agent_ids": plan["assigned_team"]})

    session.submit_action(_assessment_action(session, "deployable"))
    assert _submit(session, "select", "select_team",
                   {"agent_ids": plan["assigned_team"]})["accepted"]
    assert _submit(session, "plan", "submit_plan", {"plan": plan})["accepted"]


def test_not_deployable_emits_trusted_assistance_and_rejects_terminal_actions():
    bundle, public, api = _v3()
    session = GoalSession(public, api)
    result = session.submit_action(_assessment_action(
        session, "not_deployable", reasons=["missing_capability"]))
    report = session.report()
    assert result["termination_event"]["type"] == "human_assistance_requested"
    assert report["deployment_assessment"]["terminated"] is True
    assert report["deployment_assessment"]["termination"]["resource_injected"] is False
    assert report["selected_team"] == [] and report["tasks"] == {}
    plan = bundle["hidden_evaluator"]["witness_plans"][0]["plan"]
    for action_type, payload in (
        ("deployment_assessment", _assessment_action(
            session, "deployable", action_id="replay")["payload"]),
        ("select_team", {"agent_ids": plan["assigned_team"]}),
        ("submit_plan", {"plan": plan}),
        ("dispatch_skill", {"task_id": plan["tasks"][0]["task_id"],
                            "evidence_refs": []}),
    ):
        with pytest.raises(ValueError, match="terminated"):
            _submit(session, f"terminal-{action_type}", action_type, payload)


def test_loss_declaration_can_reference_a_publicly_exited_member():
    _bundle, public, api = _v3()
    session = GoalSession(public, api)
    session.submit_action(_assessment_action(session, "deployable"))
    exited = session.report()["current_agents"][0]
    session.emit("member_exit", 0.0, {"agent_id": exited})

    lost = _assessment_action(
        session, "not_deployable", action_id="loss",
        reasons=["lost_unique_capability"])
    lost["payload"]["evidence_refs"] = [exited]
    assert session.submit_action(lost)["accepted"] is True

    other = GoalSession(public, api)
    invalid = _assessment_action(
        other, "not_deployable", action_id="future-loss",
        reasons=["lost_unique_capability"])
    invalid["payload"]["evidence_refs"] = ["future-unpublished-member"]
    with pytest.raises(ValueError, match="public entities or messages"):
        other.submit_action(invalid)


def test_lifecycle_change_invalidates_epoch_and_requests_safe_stop():
    bundle, public, api = _v3()
    session = GoalSession(public, api)
    old = _assessment_action(session, "deployable")
    session.submit_action(old)
    plan = bundle["hidden_evaluator"]["witness_plans"][0]["plan"]
    _submit(session, "select", "select_team", {"agent_ids": plan["assigned_team"]})
    _submit(session, "plan", "submit_plan", {"plan": plan})
    ready = next(task for task in plan["tasks"] if not task["prerequisites"])
    _submit(session, "dispatch", "dispatch_skill",
            {"task_id": ready["task_id"], "evidence_refs": []})

    agent = ready["assigned_agents"][0]
    declaration = deepcopy(session.report()["current_robot_declarations"][agent])
    declaration["public_capabilities"] = []
    session.emit("capability_change", 0.0, {
        "agent_id": agent,
        "capability_declaration": declaration,
        "capability_version": 1,
    })
    report = session.report()
    gate = report["deployment_assessment"]
    assert gate["status"] == "required" and gate["assessment_epoch"] == 1
    assert report["tasks"][ready["task_id"]]["status"] == "cancel_requested"
    assert report["tasks"][ready["task_id"]]["safety_stop"]["acknowledged"] is False
    with pytest.raises(ValueError, match="stale roster or epoch"):
        session.submit_action({**old, "action_id": "stale", "submitted_at_s": 0.0})

    session.submit_action(_assessment_action(
        session, "not_deployable", action_id="reassess",
        reasons=["lost_unique_capability"]))
    receipt = {
        "backend_id": "safe-stop", "receipt_ref": "cancelled-task",
        "task_id": ready["task_id"], "action_id": "dispatch",
        "status": "cancelled", "completed_at_s": 0.0,
        "evidence_refs": ["safe-stop:ack"],
    }
    session.complete_skill(ready["task_id"], time_s=0.0,
                           status="cancelled", receipt=receipt)
    stopped = session.report()["tasks"][ready["task_id"]]
    assert stopped["status"] == "cancelled"
    assert stopped["safety_stop"]["acknowledged"] is True


class _NotDeployableAdapter:
    def manifest(self):
        return {
            "kind": "participant", "id": "deployment-aware-test", "version": 1,
            "interface": "eai.benchmark.goal_algorithm_adapter",
            "interface_version": 1, "evaluation_track": "goal_driven",
            "input_scope": "participant_visible_events_only",
            "control_scope": "benchmark_managed_low_level_control",
        }

    def initialize(self, public_task, participant_api):
        self.public_task = public_task
        self.participant_api = participant_api

    def act(self, events):
        required = next((row for row in events
                         if row["type"] == "deployment_assessment_required"), None)
        if required is None:
            return []
        payload = required["payload"]
        return [{
            "schema": "eai.benchmark.goal_action", "version": 1,
            "action_id": f"assessment-{payload['assessment_epoch']}",
            "submitted_at_s": required["time_s"],
            "type": "deployment_assessment",
            "payload": {
                "assessment_id": payload["assessment_id"],
                "assessment_epoch": payload["assessment_epoch"],
                "roster_version": payload["roster_version"],
                "capability_versions": payload["capability_versions"],
                "decision": "not_deployable",
                "reason_codes": ["missing_capability"],
                "evidence_refs": [],
            },
        }]

    def close(self):
        pass


class _NeverBackend:
    def __init__(self):
        self.calls = 0

    def execute(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError("terminated deployment must not execute")


class _AssessmentEvaluator:
    def __init__(self, label):
        self.label = label
        self.calls = []

    def __call__(self, episode_id, trusted):
        self.calls.append((episode_id, deepcopy(trusted)))
        row = trusted["session"]["deployment_assessment"]
        return {
            "assessment_id": row["assessment_id"],
            "assessment_epoch": row["assessment_epoch"],
            "feasibility_label": self.label,
            "oracle_status": "verified",
            "platform_valid": True,
            "evidence_refs": ["hidden:necessary-condition"],
            "evaluator_ref": "test.hidden.deployment",
        }

    def report(self):
        return {
            "schema": "test.deployment_feasibility_evidence",
            "version": 1,
            "evaluations": len(self.calls),
        }


def test_runner_archives_hidden_assessment_without_backend_or_business_success(tmp_path):
    _bundle, public, api = _v3()
    backend = _NeverBackend()
    business_calls = []
    assessment_evaluator = _AssessmentEvaluator("not_deployable")

    def business_evaluator(*args):
        business_calls.append(args)
        raise AssertionError("terminated deployment must not evaluate business success")

    result = FormalGoalEpisodeRunner(
        public, api, _NotDeployableAdapter(), backend,
        output_root=tmp_path / "not-deployable", evaluator=business_evaluator,
        assessment_evaluator=assessment_evaluator,
    ).run(initial_state=_initial_state(public))

    assert result["error"] is None
    assert result["skill_receipt_count"] == 0
    assert result["business_outcomes_validated"] is False
    assert result["termination"]["category"] == "human_assistance_requested"
    assert backend.calls == 0 and business_calls == []
    assert len(assessment_evaluator.calls) == 1
    hidden = json.loads((tmp_path / "not-deployable/hidden_evaluator_trace.json").read_text())
    history = hidden["deployment_assessment"]["history"]
    assert history[0]["external_evaluation"]["feasibility_label"] == "not_deployable"
    assert assessment_evaluator.calls[0][1]["session"][
        "deployment_assessment"]["history"] == []
    assert history[0]["declared_runtime_state_sha256"]
    public_events = json.loads(
        (tmp_path / "not-deployable/participant_trace.json").read_text())["events"]
    assert "external_evaluation" not in json.dumps(public_events)
    evidence = json.loads(
        (tmp_path / "not-deployable/deployment_feasibility_evidence.json").read_text())
    assert evidence["evaluations"] == 1


class _TerminalBatchAdapter(_NotDeployableAdapter):
    def act(self, events):
        actions = super().act(events)
        if not actions:
            return []
        return actions + [
            {"schema": "eai.benchmark.goal_pose_tool_call", "call_id": "late-pose"},
            {"schema": "eai.benchmark.goal_camera_tool_call", "request_id": "late-camera"},
        ]


def test_terminal_assessment_stops_same_batch_and_future_tool_pumps():
    _bundle, public, api = _v3()
    host = GoalAlgorithmHost(public, api, _TerminalBatchAdapter())
    results = host.request_actions()
    assert [row["type"] for row in results] == ["deployment_assessment"]
    assert host.trusted_report()["session"]["deployment_assessment"]["terminated"]

    host._tools_connected = True
    host._camera_tools_connected = True
    host.pump_pose_tools = lambda: pytest.fail("terminated host pumped pose tools")
    host.pump_camera_tools = lambda: pytest.fail("terminated host pumped camera tools")
    assert host.request_actions() == []
    host.close()


class _IdleAdapter(_NotDeployableAdapter):
    def act(self, events):
        return []


def test_runner_archives_missing_assessment_as_protocol_error(tmp_path):
    _bundle, public, api = _v3()
    root = tmp_path / "missing-assessment"
    result = FormalGoalEpisodeRunner(
        public, api, _IdleAdapter(), _NeverBackend(), output_root=root,
    ).run(initial_state=_initial_state(public))

    assert "missing_deployment_assessment" in result["error"]
    archived = json.loads((root / "deployment_protocol_error.json").read_text())
    assert archived["category"] == "protocol_error"
    assert archived["code"] == "missing_assessment"
    assert archived["assessment_epoch"] == 0
    hidden = json.loads((root / "hidden_evaluator_trace.json").read_text())
    oracle = hidden["deployment_assessment"]["oracle_evaluations"]
    assert oracle == [{
        "assessment_id": "deployment-assessment-0",
        "assessment_epoch": 0,
        "feasibility_label": None,
        "oracle_status": "failed",
        "platform_valid": False,
        "evidence_refs": [],
        "evaluator_ref": "oracle_not_configured",
    }]


class _DeployableOnlyAdapter(_NotDeployableAdapter):
    def act(self, events):
        actions = super().act(events)
        if actions:
            actions[0]["payload"]["decision"] = "deployable"
            actions[0]["payload"]["reason_codes"] = []
        return actions


def test_max_turn_boundary_still_consumes_hidden_assessment(tmp_path):
    _bundle, public, api = _v3()
    evaluator = _AssessmentEvaluator("deployable")
    root = tmp_path / "max-turn-assessment"
    result = FormalGoalEpisodeRunner(
        public, api, _DeployableOnlyAdapter(), _NeverBackend(), output_root=root,
        assessment_evaluator=evaluator, max_turns=1,
    ).run(initial_state=_initial_state(public))

    assert "exceeded max_turns" in result["error"]
    assert len(evaluator.calls) == 1
    hidden = json.loads((root / "hidden_evaluator_trace.json").read_text())
    assert hidden["deployment_assessment"]["history"][0][
        "external_evaluation"]["feasibility_label"] == "deployable"


class _DeployablePlanAdapter(_DeployableOnlyAdapter):
    def __init__(self, plan):
        self.plan = plan
        self.assessed = False
        self.dispatched = False

    def act(self, events):
        if not self.assessed:
            actions = super().act(events)
            if actions:
                self.assessed = True
            return actions
        if self.dispatched:
            return []
        if not any(row["type"] == "action_result" for row in events):
            return []
        self.dispatched = True
        robot = self.plan["assigned_team"][0]
        return [
            {"schema": "eai.benchmark.goal_action", "version": 1,
             "action_id": "select", "submitted_at_s": 0.0, "type": "select_team",
             "payload": {"agent_ids": [robot]}},
            {"schema": "eai.benchmark.goal_action", "version": 1,
             "action_id": "plan", "submitted_at_s": 0.0, "type": "submit_plan",
             "payload": {"plan": self.plan}},
            {"schema": "eai.benchmark.goal_action", "version": 1,
             "action_id": "dispatch", "submitted_at_s": 0.0, "type": "dispatch_skill",
             "payload": {"task_id": self.plan["tasks"][0]["task_id"],
                         "evidence_refs": []}},
        ]


class _SuccessBackend:
    def __init__(self, state):
        self.state = deepcopy(state)

    def execute(self, task, *, episode_id, trusted_report):
        state = deepcopy(self.state)
        state["robot_regions"][task["assigned_agents"][0]] = "consolidation_area"
        return {
            "status": "succeeded", "completed_at_s": 8.0,
            "receipt": {
                "backend_id": "deployment-test", "receipt_ref": "deployment/nav",
                "task_id": task["task_id"], "action_id": "dispatch",
                "status": "succeeded", "completed_at_s": 8.0,
                "episode_id": episode_id, "evidence_refs": ["native:test-nav"],
            },
            "runtime_state": state,
        }


def test_v3_business_facts_do_not_promote_release_flags(tmp_path):
    _bundle, public, api = _v3()
    state = _initial_state(public)
    robot = next(row["id"] for row in public["candidate_robot_pool"]
                 if row["mobility"] == "ground")
    state["robot_regions"][robot] = "rack_aisle_a"
    plan = {
        "schema": "eai.benchmark.goal_plan", "version": GOAL_PLAN_VERSION,
        "plan_id": "deployment-release-gate", "assigned_team": [robot],
        "tasks": [{
            "task_id": "deployment-nav", "skill": "ground_navigation",
            "arguments": {"region_id": "consolidation_area",
                          "route_ids": ["rack_a_to_consolidation"]},
            "required_capabilities": ["ground_navigation"],
            "assigned_agents": [robot], "prerequisites": [], "evidence_refs": [],
            "execution": {"start_s": 0.0, "duration_s": 16.0},
        }],
    }

    def business_evaluator(_episode_id, _trusted):
        return {
            row["id"]: {
                "status": "validated", "evidence_refs": [f"business:{row['id']}"],
                "evaluator_ref": "test.business",
            }
            for row in public["required_outcomes"]
        }

    root = tmp_path / "release-flags"
    result = FormalGoalEpisodeRunner(
        public, api, _DeployablePlanAdapter(plan), _SuccessBackend(state),
        output_root=root, evaluator=business_evaluator,
        assessment_evaluator=_AssessmentEvaluator("deployable"),
    ).run(initial_state=state)

    assert result["error"] is None
    assert result["execution_validated"] is False
    assert result["business_outcomes_validated"] is False
    assert result["scoreable"] is False
    report = json.loads((root / "goal_session_report.json").read_text())["report"]
    assert report["business_task_success"] is True
    assert report["business_outcomes_validated"] is False
    assert report["execution_validated"] is False and report["scoreable"] is False
