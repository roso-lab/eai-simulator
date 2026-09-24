from copy import deepcopy
import json
from pathlib import Path

import pytest

from demo.benchmark.goal_deployment_assessment import enable_deployment_assessment
from demo.benchmark.goal_driven_contract import (
    GOAL_PLAN_VERSION,
    build_goal_participant_api,
)
from demo.benchmark.goal_driven_tasks import build_goal_task_package
from demo.benchmark.goal_episode_runner import FormalGoalEpisodeRunner
from demo.benchmark.goal_session_audit import (
    AuditedGoalSession,
    replay_goal_session_audit,
    validate_goal_session_audit,
)
from demo.benchmark.provenance import canonical_sha256


def _action(session, action_id, action_type, payload):
    return {
        "schema": "eai.benchmark.goal_action",
        "version": 1,
        "action_id": action_id,
        "submitted_at_s": session.report()["current_time_s"],
        "type": action_type,
        "payload": payload,
    }


def _initial_state(public):
    regions = {
        robot["id"]: next(
            region["id"] for region in public["public_world"]["regions"]
            if ("aerial_navigation" if robot["mobility"] == "aerial"
                else "ground_navigation") in region["access_modes"])
        for robot in public["candidate_robot_pool"]
    }
    objects = {
        row["id"]: {
            "region_id": row["physical_properties"]["initial_region_id"]}
        for row in public["public_world"]["objects"]
    }
    return {"robot_regions": regions, "object_states": objects}


def _navigation_plan(plan_id, task_id, robot):
    return {
        "schema": "eai.benchmark.goal_plan",
        "version": GOAL_PLAN_VERSION,
        "plan_id": plan_id,
        "assigned_team": [robot],
        "tasks": [{
            "task_id": task_id,
            "skill": "ground_navigation",
            "arguments": {
                "region_id": "consolidation_area",
                "route_ids": ["rack_a_to_consolidation"],
            },
            "required_capabilities": ["ground_navigation"],
            "assigned_agents": [robot],
            "prerequisites": [],
            "evidence_refs": [],
            "execution": {"start_s": 0.0, "duration_s": 16.0},
        }],
    }


def _revision_payload(session, plan, replaced_plan_id):
    return {
        "plan": plan,
        "replaced_plan_id": replaced_plan_id,
        "trigger_event_sequence": [session.report()["events"][-1]["sequence"]],
        "reason": "participant selected an alternative remaining-work plan",
    }


def _build_audited_episode():
    bundle = build_goal_task_package("warehouse_nvd")
    public, api = bundle["public_task"], bundle["participant_api"]
    robot = "warehouse_ground_1"
    state = _initial_state(public)
    session = AuditedGoalSession(public, api, episode_id="audit-episode")
    session.validate_runtime_state(
        0.0, state["robot_regions"], state["object_states"])
    session.update_runtime_state(
        0.0, state["robot_regions"], state["object_states"])
    session.submit_action(_action(
        session, "select", "select_team", {"agent_ids": [robot]}))
    first = _navigation_plan("plan-first", "nav-first", robot)
    session.submit_action(_action(
        session, "plan-first-action", "submit_plan", {"plan": first}))

    rejected = _action(
        session, "bad-revision", "revise_plan",
        _revision_payload(
            session, _navigation_plan("plan-bad", "nav-bad", robot),
            "not-the-current-plan"))
    with pytest.raises(ValueError) as caught:
        session.submit_action(rejected)
    session.record_action_rejection(rejected, reason=str(caught.value))

    second = _navigation_plan("plan-second", "nav-second", robot)
    session.submit_action(_action(
        session, "good-revision", "revise_plan",
        _revision_payload(session, second, "plan-first")))
    session.submit_action(_action(
        session, "dispatch-second", "dispatch_skill",
        {"task_id": "nav-second", "evidence_refs": []}))
    receipt = {
        "backend_id": "audit-test-backend",
        "receipt_ref": "audit-test/nav-second",
        "task_id": "nav-second",
        "action_id": "dispatch-second",
        "status": "failed",
        "completed_at_s": 1.0,
        "episode_id": "audit-episode",
        "evidence_refs": ["native:audit-test:nav-second"],
    }
    session.complete_skill(
        "nav-second", time_s=1.0, status="failed", receipt=receipt)
    session.emit("tick", 1.0, {"state_digest": "post-failure-state"})
    session.record_business_outcome(
        public["required_outcomes"][0]["id"], status="failed",
        evidence_refs=["business:audit-test:failed"],
        evaluator_ref="audit-test-evaluator")
    return public, api, session.audit_journal()


def _rehash(journal):
    previous = None
    for call in journal["calls"]:
        call["previous_call_sha256"] = previous
        call["call_sha256"] = canonical_sha256({
            key: value for key, value in call.items()
            if key != "call_sha256"})
        previous = call["call_sha256"]
    journal["final_report_sha256"] = canonical_sha256(journal["final_report"])
    journal["journal_sha256"] = canonical_sha256({
        key: value for key, value in journal.items()
        if key != "journal_sha256"})
    return journal


def test_audit_replays_alternative_revision_rejection_and_failed_completion():
    public, api, journal = _build_audited_episode()
    replay = replay_goal_session_audit(journal, public, api)
    methods = [row["method"] for row in journal["calls"]]
    assert methods.count("submit_action") == 5
    assert "record_action_rejection" in methods
    assert "complete_skill" in methods
    assert "record_business_outcome" in methods
    assert journal["final_report"]["current_plan_id"] == "plan-second"
    assert journal["final_report"]["tasks"]["nav-second"]["status"] == "failed"
    assert replay["validated"] is True
    assert replay["raised_calls_replayed"] == 1
    assert replay["calls_replayed"] == len(journal["calls"])
    assert replay["integrity_scope"] == (
        "internal_consistency_not_physical_authentication")
    assert replay["execution_validated"] is False
    assert replay["business_outcomes_validated"] is False
    assert replay["scoreable"] is False


def test_audit_replays_trusted_deployment_oracle_evaluation():
    bundle = build_goal_task_package("factory")
    public = enable_deployment_assessment(bundle["public_task"])
    api = build_goal_participant_api(public)
    session = AuditedGoalSession(public, api, episode_id="oracle-audit")
    gate = session.report()["deployment_assessment"]
    session.record_deployment_assessment_evaluation({
        "assessment_id": gate["assessment_id"],
        "assessment_epoch": gate["assessment_epoch"],
        "feasibility_label": None,
        "oracle_status": "failed",
        "platform_valid": False,
        "evidence_refs": [],
        "evaluator_ref": "audit-test-oracle-unavailable",
    })
    journal = session.audit_journal()
    assert journal["calls"][0]["method"] == (
        "record_deployment_assessment_evaluation")
    assert replay_goal_session_audit(journal, public, api)["validated"] is True


def test_audit_preserves_nonfinite_rejected_call_as_strict_json():
    bundle = build_goal_task_package("factory")
    public, api = bundle["public_task"], bundle["participant_api"]
    session = AuditedGoalSession(public, api, episode_id="nonfinite-audit")
    action = _action(
        session, "nonfinite", "select_team",
        {"agent_ids": [public["candidate_robot_pool"][0]["id"]]})
    action["submitted_at_s"] = float("nan")
    with pytest.raises(ValueError, match="finite"):
        session.submit_action(action)
    journal = session.audit_journal()
    json.dumps(journal, allow_nan=False)
    assert journal["calls"][0]["special_floats"] == [{
        "path": ["action", "submitted_at_s"], "value": "nan"}]
    replay = replay_goal_session_audit(journal, public, api)
    assert replay["validated"] is True
    assert replay["raised_calls_replayed"] == 1


def test_audit_rejects_hash_result_and_final_report_tampering():
    public, api, journal = _build_audited_episode()
    bad_hash = deepcopy(journal)
    bad_hash["calls"][0]["call_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="digest|chain"):
        validate_goal_session_audit(bad_hash)

    bad_result = deepcopy(journal)
    bad_result["calls"][0]["result"]["time_s"] = 9.0
    _rehash(bad_result)
    with pytest.raises(ValueError, match="result mismatch"):
        replay_goal_session_audit(bad_result, public, api)

    bad_report = deepcopy(journal)
    bad_report["final_report"]["state_version"] += 1
    _rehash(bad_report)
    with pytest.raises(ValueError, match="final report mismatch"):
        replay_goal_session_audit(bad_report, public, api)

    empty_message = deepcopy(journal)
    raised = next(row for row in empty_message["calls"]
                  if row["outcome"] == "raised")
    raised["exception"]["message"] = ""
    _rehash(empty_message)
    assert validate_goal_session_audit(empty_message) == empty_message


def test_audit_rejects_unknown_schema_fields_methods_and_input_identity():
    public, api, journal = _build_audited_episode()
    unknown = deepcopy(journal)
    unknown["unexpected"] = True
    with pytest.raises(ValueError, match="incomplete or unknown"):
        validate_goal_session_audit(unknown)

    unknown_method = deepcopy(journal)
    unknown_method["calls"][0]["method"] = "replace_author_mission"
    _rehash(unknown_method)
    with pytest.raises(ValueError, match="method is invalid"):
        validate_goal_session_audit(unknown_method)

    changed_task = deepcopy(public)
    changed_task["task_id"] = "different-task"
    with pytest.raises(ValueError, match="public task digest"):
        replay_goal_session_audit(journal, changed_task, api)
    changed_api = deepcopy(api)
    changed_api["public_task_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="participant API digest"):
        replay_goal_session_audit(journal, public, changed_api)


class _IdleAdapter:
    def manifest(self):
        return {
            "kind": "participant",
            "id": "session-audit-idle",
            "version": 1,
            "interface": "eai.benchmark.goal_algorithm_adapter",
            "interface_version": 1,
            "evaluation_track": "goal_driven",
            "input_scope": "participant_visible_events_only",
            "control_scope": "benchmark_managed_low_level_control",
        }

    def initialize(self, public_task, participant_api):
        del public_task, participant_api

    def act(self, events):
        del events
        return []

    def close(self):
        return None


class _UnusedBackend:
    def execute(self, task, *, episode_id, trusted_report):
        raise AssertionError((task, episode_id, trusted_report))

    def cancel(self, task, *, episode_id, trusted_report):
        raise AssertionError((task, episode_id, trusted_report))


def test_runner_session_audit_is_explicit_opt_in(tmp_path: Path):
    bundle = build_goal_task_package("factory")
    public, api = bundle["public_task"], bundle["participant_api"]
    state = _initial_state(public)
    default_root = tmp_path / "default"
    default = FormalGoalEpisodeRunner(
        public, api, _IdleAdapter(), _UnusedBackend(),
        output_root=default_root, episode_id="audit-default").run(
            initial_state=state)
    assert default["error"] is None
    assert "session_audit_validated" not in default
    assert not (default_root / "goal_session_audit.json").exists()
    assert "session_audit" not in json.loads(
        (default_root / "episode_manifest.json").read_text())

    def validate_all_business_outcomes(_episode_id, _trusted_report):
        return {
            row["id"]: {
                "status": "validated",
                "evidence_refs": [f"audit-test:{row['id']}"],
                "evaluator_ref": "audit-test-business-evaluator",
            }
            for row in public["required_outcomes"]
        }

    audit_root = tmp_path / "enabled"
    enabled = FormalGoalEpisodeRunner(
        public, api, _IdleAdapter(), _UnusedBackend(),
        output_root=audit_root, episode_id="audit-enabled",
        session_audit=True,
        evaluator=validate_all_business_outcomes).run(initial_state=state)
    journal = json.loads(
        (audit_root / "goal_session_audit.json").read_text())
    replay = json.loads(
        (audit_root / "goal_session_audit_replay.json").read_text())
    metadata = json.loads(
        (audit_root / "scoring_adapter_metadata.json").read_text())
    assert enabled["error"] is None
    assert enabled["session_audit_validated"] is True
    assert enabled["business_outcomes_validated"] is False
    assert journal["final_report"]["business_outcomes_validated"] is True
    assert journal["business_outcomes_validated"] is False
    assert replay["validated"] is True
    assert replay["journal_sha256"] == journal["journal_sha256"]
    assert replay_goal_session_audit(journal, public, api)["validated"] is True
    assert replay["execution_validated"] is False
    assert replay["business_outcomes_validated"] is False
    assert replay["scoreable"] is False
    manifest = json.loads((audit_root / "episode_manifest.json").read_text())
    assert manifest["session_audit"]["enabled"] is True
    assert {"goal_session_audit.json", "goal_session_audit_replay.json"} <= set(
        metadata["archive_index"]["files"])


def test_runner_archives_audit_failure_and_still_writes_result_and_scoring(
    tmp_path: Path, monkeypatch,
):
    bundle = build_goal_task_package("factory")
    public, api = bundle["public_task"], bundle["participant_api"]

    def fail_audit(_self):
        raise ValueError()

    monkeypatch.setattr(AuditedGoalSession, "audit_journal", fail_audit)
    root = tmp_path / "audit-failure"
    result = FormalGoalEpisodeRunner(
        public, api, _IdleAdapter(), _UnusedBackend(), output_root=root,
        episode_id="audit-failure", session_audit=True).run(
            initial_state=_initial_state(public))
    replay = json.loads(
        (root / "goal_session_audit_replay.json").read_text())
    metadata = json.loads(
        (root / "scoring_adapter_metadata.json").read_text())
    lifecycle = json.loads((root / "lifecycle_errors.json").read_text())
    assert result["session_audit_validated"] is False
    assert result["error"] == "lifecycle_error: ValueError: "
    assert lifecycle[0]["component"] == "goal_session_audit"
    assert replay["validated"] is False
    assert replay["error"] == "ValueError: "
    assert replay["journal_sha256"] is None
    assert (root / "result.json").exists()
    assert metadata["episode_result"]["error"] == result["error"]


@pytest.mark.parametrize("value", [None, 0, 1, "yes"])
def test_runner_session_audit_requires_strict_bool(tmp_path: Path, value):
    bundle = build_goal_task_package("factory")
    with pytest.raises(ValueError, match="session_audit must be a bool"):
        FormalGoalEpisodeRunner(
            bundle["public_task"], bundle["participant_api"],
            _IdleAdapter(), _UnusedBackend(), output_root=tmp_path / "bad",
            session_audit=value)
