"""R1 fake-backend concurrency evidence tests.

Concurrency evidence is defined as overlapping *actual execution* intervals on
one fake clock, each closed by a real completion event.  Planned execution
windows are context and never evidence.  The tests below cover the five R1
categories (genuine overlap, resource mutex, cancel races in both orders,
mid-flight member exit, post-termination refusal), the report schema with
explicit ``not_triggered`` sections, and integration with the real
:class:`FormalGoalEpisodeRunner` loop.  Genuine in-flight overlap is proven at
component level against the real ``GoalSession`` state machine because the
current runner loop executes ``pending[0]`` synchronously to a terminal
receipt; runner-level tests therefore prove the serial counterexample and the
termination/cancellation behaviour through the unmodified loop.
"""

from copy import deepcopy
import json
from pathlib import Path
from typing import Any

import pytest

import demo.benchmark.goal_episode_runner as runner_module
from demo.benchmark.communication import CommunicationRuntime
from demo.benchmark.goal_algorithm_adapter import GoalAlgorithmHost
from demo.benchmark.goal_communication_tool import GoalCommunicationTools
from demo.benchmark.goal_concurrency_evidence import (
    EVIDENCE_CATEGORIES,
    GOAL_CONCURRENCY_EVIDENCE_SCHEMA,
    ConcurrencyProbeRecorder,
    ConcurrentFakeBackend,
    PostTerminationExecutionRefused,
    ResourceConflictExecutionRefused,
    build_concurrency_evidence_report,
)
from demo.benchmark.goal_deployment_assessment import enable_deployment_assessment
from demo.benchmark.goal_driven_contract import (
    GOAL_PLAN_VERSION,
    build_goal_participant_api,
)
from demo.benchmark.goal_driven_tasks import build_goal_task_package
from demo.benchmark.goal_episode_runner import FormalGoalEpisodeRunner
from demo.benchmark.goal_session import GoalSession

GROUND_1 = "warehouse_ground_1"
GROUND_3 = "warehouse_ground_3"
AERIAL = "warehouse_aerial_1"


def _action(action_id, action_type, payload, *, submitted_at_s=0.0):
    return {
        "schema": "eai.benchmark.goal_action", "version": 1,
        "action_id": action_id, "submitted_at_s": submitted_at_s,
        "type": action_type, "payload": payload,
    }


def _submit(session, action_id, action_type, payload):
    now = session.report()["current_time_s"]
    return session.submit_action(
        _action(action_id, action_type, payload, submitted_at_s=now))


def _declare_assessment(session, action_id, decision, *, reason_codes=(),
                        evidence_refs=()):
    gate = session.report()["deployment_assessment"]
    return _submit(session, action_id, "deployment_assessment", {
        "assessment_id": gate["assessment_id"],
        "assessment_epoch": gate["assessment_epoch"],
        "roster_version": gate["roster_version"],
        "capability_versions": gate["capability_versions"],
        "decision": decision,
        "reason_codes": list(reason_codes),
        "evidence_refs": list(evidence_refs),
    })


def _nav_task(task_id, agent, destination, route, start_s, duration_s):
    return {
        "task_id": task_id, "skill": "ground_navigation",
        "arguments": {"region_id": destination, "route_ids": [route]},
        "required_capabilities": ["ground_navigation"],
        "assigned_agents": [agent], "prerequisites": [], "evidence_refs": [],
        "execution": {"start_s": start_s, "duration_s": duration_s},
    }


def _aerial_task(task_id, agent, destination, route, start_s, duration_s):
    return {
        "task_id": task_id, "skill": "aerial_navigation",
        "arguments": {"region_id": destination, "route_ids": [route]},
        "required_capabilities": ["aerial_navigation"],
        "assigned_agents": [agent], "prerequisites": [], "evidence_refs": [],
        "execution": {"start_s": start_s, "duration_s": duration_s},
    }


def _component_fixture(*, deployment=False, ground_3_region="consolidation_area"):
    bundle = build_goal_task_package("warehouse_nvd")
    public = bundle["public_task"]
    api = bundle["participant_api"]
    if deployment:
        public = enable_deployment_assessment(public)
        api = build_goal_participant_api(public)
    regions = {}
    for row in public["candidate_robot_pool"]:
        if row["mobility"] == "aerial":
            regions[row["id"]] = "rack_aisle_a"
        elif row["id"] == GROUND_3:
            regions[row["id"]] = ground_3_region
        else:
            regions[row["id"]] = "consolidation_area"
    objects = {row["id"]: {"region_id": row["physical_properties"]["initial_region_id"]}
               for row in public["public_world"]["objects"]}
    return public, api, {"robot_regions": regions, "object_states": objects}


def _component_setup(public, api, plan, state, *, episode_id):
    session = GoalSession(public, api, episode_id=episode_id)
    session.update_runtime_state(0.0, state["robot_regions"], state["object_states"])
    recorder = ConcurrencyProbeRecorder(episode_id=episode_id)
    backend = ConcurrentFakeBackend(
        public, episode_id=episode_id, recorder=recorder, runtime_state=state)
    recorder.note_plan(plan)
    return session, recorder, backend


def _commit(session, delivery):
    completion = session.complete_skill(
        delivery["receipt"]["task_id"], time_s=delivery["completed_at_s"],
        status=delivery["status"], receipt=delivery["receipt"])
    assert completion.get("accepted") is True
    return completion


# ----------------------------------------------------------------------
# Component level: genuine concurrency on the fake clock
# ----------------------------------------------------------------------
def test_two_skills_genuinely_in_flight_overlap_with_real_completions():
    """Different resources: both skills in flight, both really completed.

    The overlap pair must carry the receipt evidence of both completion
    events, and a pair whose second completion is still outstanding must not
    be counted yet.
    """
    public, api, state = _component_fixture()
    plan = {"schema": "eai.benchmark.goal_plan", "version": GOAL_PLAN_VERSION,
            "plan_id": "overlap-plan", "assigned_team": [GROUND_1, GROUND_3],
            "tasks": [
                _nav_task("nav_a", GROUND_1, "rack_aisle_a",
                          "consolidation_to_rack_a", 0.0, 10.0),
                _nav_task("nav_b", GROUND_3, "rack_aisle_b",
                          "consolidation_to_rack_b", 0.0, 8.0),
            ]}
    session, recorder, backend = _component_setup(
        public, api, plan, state, episode_id="overlap-episode-1")
    _submit(session, "team", "select_team", {"agent_ids": [GROUND_1, GROUND_3]})
    _submit(session, "plan", "submit_plan", {"plan": plan})
    _submit(session, "dispatch-a", "dispatch_skill",
            {"task_id": "nav_a", "evidence_refs": []})
    _submit(session, "dispatch-b", "dispatch_skill",
            {"task_id": "nav_b", "evidence_refs": []})

    first = backend.execute(plan["tasks"][0], episode_id="overlap-episode-1",
                            trusted_report=session.report())
    second = backend.execute(plan["tasks"][1], episode_id="overlap-episode-1",
                             trusted_report=session.report())
    assert first["status"] == second["status"] == "in_flight"
    assert backend.in_flight_task_ids() == ["nav_a", "nav_b"]

    delivery_b = backend.complete("nav_b", status="succeeded")
    _commit(session, delivery_b)
    partial = build_concurrency_evidence_report(recorder)
    assert partial["overlap_pairs"] == []
    assert partial["in_flight_open"] == ["nav_a"]
    assert partial["execution_counts"] == {"total": 2, "closed": 1, "in_flight_open": 1}

    delivery_a = backend.complete("nav_a", status="succeeded")
    _commit(session, delivery_a)
    report = build_concurrency_evidence_report(
        recorder, task_id=public["task_id"], scene_key=public["mission"]["scene_key"])
    assert len(report["overlap_pairs"]) == 1
    pair = report["overlap_pairs"][0]
    assert pair["tasks"] == ["nav_a", "nav_b"]
    assert (pair["overlap_start_s"], pair["overlap_end_s"]) == (0.0, 8.0)
    assert pair["statuses"] == {"nav_a": "succeeded", "nav_b": "succeeded"}
    assert set(pair["evidence_refs"]) == {
        delivery_a["receipt"]["receipt_ref"], delivery_b["receipt"]["receipt_ref"]}
    session_report = session.report()
    assert {tid: row["status"] for tid, row in session_report["tasks"].items()} == {
        "nav_a": "succeeded", "nav_b": "succeeded"}
    assert session_report["current_time_s"] == 10.0
    # The two executions used disjoint mutex keys, so both could fly together.
    assert report["resource_intervals"]["route:crate_aisle"][0]["task_id"] == "nav_a"
    assert report["resource_intervals"]["route:carton_aisle"][0]["task_id"] == "nav_b"
    assert report["release"] is False


def test_advance_idle_completes_due_in_flight_skills_on_the_fake_clock():
    """The idle pump is the second completion driver: advancing the fake clock
    closes exactly the skills whose planned end has passed and leaves the
    others genuinely in flight."""
    public, api, state = _component_fixture()
    plan = {"schema": "eai.benchmark.goal_plan", "version": GOAL_PLAN_VERSION,
            "plan_id": "idle-pump-plan", "assigned_team": [GROUND_1, GROUND_3],
            "tasks": [
                _nav_task("nav_a", GROUND_1, "rack_aisle_a",
                          "consolidation_to_rack_a", 0.0, 10.0),
                _nav_task("nav_b", GROUND_3, "rack_aisle_b",
                          "consolidation_to_rack_b", 0.0, 8.0),
            ]}
    session, recorder, backend = _component_setup(
        public, api, plan, state, episode_id="idle-pump-1")
    _submit(session, "team", "select_team", {"agent_ids": [GROUND_1, GROUND_3]})
    _submit(session, "plan", "submit_plan", {"plan": plan})
    _submit(session, "dispatch-a", "dispatch_skill",
            {"task_id": "nav_a", "evidence_refs": []})
    _submit(session, "dispatch-b", "dispatch_skill",
            {"task_id": "nav_b", "evidence_refs": []})
    backend.execute(plan["tasks"][0], episode_id="idle-pump-1",
                    trusted_report=session.report())
    backend.execute(plan["tasks"][1], episode_id="idle-pump-1",
                    trusted_report=session.report())

    idle = backend.advance_idle(9.0)
    assert idle["time_s"] == 9.0
    assert backend.in_flight_task_ids() == ["nav_a"]
    assert [row["receipt"]["task_id"] for row in backend.completed_deliveries] == ["nav_b"]
    while backend.completed_deliveries:
        _commit(session, backend.completed_deliveries.pop(0))
    partial = build_concurrency_evidence_report(recorder)
    assert partial["overlap_pairs"] == []
    assert partial["clock"]["idle_advances"] == [{"from_s": 0.0, "to_s": 9.0}]

    backend.advance_idle(10.0, trusted_report=session.report())
    while backend.completed_deliveries:
        _commit(session, backend.completed_deliveries.pop(0))
    report = build_concurrency_evidence_report(recorder)
    assert report["execution_counts"] == {"total": 2, "closed": 2, "in_flight_open": 0}
    assert len(report["overlap_pairs"]) == 1
    assert (report["overlap_pairs"][0]["overlap_start_s"],
            report["overlap_pairs"][0]["overlap_end_s"]) == (0.0, 8.0)
    assert session.report()["current_time_s"] == 10.0


def test_backend_refuses_second_in_flight_execution_on_same_resource():
    """Backend-level mutex: the same route resource cannot be doubly held."""
    public, api, state = _component_fixture(ground_3_region="rack_aisle_a")
    plan = {"schema": "eai.benchmark.goal_plan", "version": GOAL_PLAN_VERSION,
            "plan_id": "mutex-plan", "assigned_team": [GROUND_1, GROUND_3],
            "tasks": [
                _nav_task("nav_a", GROUND_1, "rack_aisle_a",
                          "consolidation_to_rack_a", 0.0, 10.0),
                _nav_task("nav_reverse", GROUND_3, "consolidation_area",
                          "rack_a_to_consolidation", 10.0, 8.0),
            ]}
    session, recorder, backend = _component_setup(
        public, api, plan, state, episode_id="mutex-episode-1")
    _submit(session, "team", "select_team", {"agent_ids": [GROUND_1, GROUND_3]})
    _submit(session, "plan", "submit_plan", {"plan": plan})
    _submit(session, "dispatch-a", "dispatch_skill",
            {"task_id": "nav_a", "evidence_refs": []})
    backend.execute(plan["tasks"][0], episode_id="mutex-episode-1",
                    trusted_report=session.report())

    # The reverse task shares the crate_aisle route resource with the skill
    # that is currently in flight; the backend must refuse the double hold.
    with pytest.raises(ResourceConflictExecutionRefused, match="route:crate_aisle"):
        backend.execute(plan["tasks"][1], episode_id="mutex-episode-1",
                        trusted_report=session.report())
    assert backend.in_flight_task_ids() == ["nav_a"]
    report = build_concurrency_evidence_report(recorder)
    assert report["resource_conflicts"]["status"] == "triggered"
    assert report["resource_conflicts"]["count"] == 1
    conflict = report["resource_conflicts"]["details"][0]
    assert conflict == {
        "task_id": "nav_reverse", "resource_key": "route:crate_aisle",
        "held_by": "nav_a", "at_s": 0.0, "layer": "backend_mutex",
        "detail": "fake backend refused a second in-flight execution holding route:crate_aisle",
    }
    # A refused dispatch is not concurrency evidence of any kind.
    assert report["overlap_pairs"] == []


def test_session_admission_mutex_fires_while_first_execution_is_in_flight():
    """Runtime admission mutex: GoalSession refuses the second same-resource
    dispatch while the first is genuinely still in flight, then admits it
    after the first terminal receipt releases the reservation."""
    public, api, state = _component_fixture(ground_3_region="rack_aisle_a")
    plan = {"schema": "eai.benchmark.goal_plan", "version": GOAL_PLAN_VERSION,
            "plan_id": "admission-mutex-plan", "assigned_team": [GROUND_1, GROUND_3],
            "tasks": [
                _nav_task("nav_a", GROUND_1, "rack_aisle_a",
                          "consolidation_to_rack_a", 0.0, 10.0),
                _nav_task("nav_reverse", GROUND_3, "consolidation_area",
                          "rack_a_to_consolidation", 10.0, 8.0),
            ]}
    session, recorder, backend = _component_setup(
        public, api, plan, state, episode_id="admission-episode-1")
    _submit(session, "team", "select_team", {"agent_ids": [GROUND_1, GROUND_3]})
    _submit(session, "plan", "submit_plan", {"plan": plan})
    _submit(session, "dispatch-a", "dispatch_skill",
            {"task_id": "nav_a", "evidence_refs": []})
    backend.execute(plan["tasks"][0], episode_id="admission-episode-1",
                    trusted_report=session.report())
    session.update_runtime_state(10.0, state["robot_regions"], state["object_states"])

    with pytest.raises(ValueError, match="pending runtime resource conflict"):
        _submit(session, "dispatch-reverse", "dispatch_skill",
                {"task_id": "nav_reverse", "evidence_refs": []})
    recorder.note_resource_conflict(
        "nav_reverse", resource_key="route:crate_aisle", held_by="nav_a",
        at_s=10.0, layer="session_admission",
        detail="GoalSession refused the dispatch while nav_a was still in flight")

    delivery = backend.complete("nav_a", status="succeeded")
    _commit(session, delivery)
    session.update_runtime_state(10.0, state["robot_regions"], state["object_states"])
    _submit(session, "dispatch-reverse-2", "dispatch_skill",
            {"task_id": "nav_reverse", "evidence_refs": []})
    started = backend.execute(plan["tasks"][1], episode_id="admission-episode-1",
                              trusted_report=session.report())
    assert started["status"] == "in_flight"
    assert started["started_at_s"] == 10.0

    report = build_concurrency_evidence_report(recorder)
    assert report["resource_conflicts"]["count"] == 1
    assert report["resource_conflicts"]["details"][0]["layer"] == "session_admission"
    # The resource was held serially: closed interval plus one still open.
    assert report["resource_intervals"]["route:crate_aisle"] == [
        {"task_id": "nav_a", "start_s": 0.0, "end_s": 10.0, "status": "succeeded"}]
    assert report["overlap_pairs"] == []


# ----------------------------------------------------------------------
# Cancel competition: both orderings
# ----------------------------------------------------------------------
def _single_task_fixture():
    public, api, state = _component_fixture()
    plan = {"schema": "eai.benchmark.goal_plan", "version": GOAL_PLAN_VERSION,
            "plan_id": "cancel-plan", "assigned_team": [GROUND_1],
            "tasks": [_nav_task("nav_a", GROUND_1, "rack_aisle_a",
                                "consolidation_to_rack_a", 0.0, 10.0)]}
    session, recorder, backend = _component_setup(
        public, api, plan, state, episode_id="cancel-episode-1")
    _submit(session, "team", "select_team", {"agent_ids": [GROUND_1]})
    _submit(session, "plan", "submit_plan", {"plan": plan})
    _submit(session, "dispatch-a", "dispatch_skill",
            {"task_id": "nav_a", "evidence_refs": []})
    backend.execute(plan["tasks"][0], episode_id="cancel-episode-1",
                    trusted_report=session.report())
    return session, recorder, backend, plan


def test_cancel_arriving_while_in_flight_wins_the_race():
    """Order 1: cancel reaches the in-flight skill before completion."""
    session, recorder, backend, plan = _single_task_fixture()
    session.update_runtime_state(2.0, session.report()["runtime_state"]["robot_regions"],
                                 session.report()["runtime_state"]["object_states"])
    _submit(session, "cancel-a", "cancel_skill",
            {"task_id": "nav_a", "reason": "participant withdrew the order"})
    assert session.report()["tasks"]["nav_a"]["status"] == "cancel_requested"

    result = backend.cancel(plan["tasks"][0], episode_id="cancel-episode-1",
                            trusted_report=session.report())
    assert result["status"] == "cancelled"
    _commit(session, result)
    assert session.report()["tasks"]["nav_a"]["status"] == "cancelled"

    report = build_concurrency_evidence_report(recorder)
    assert report["cancel_competition"]["status"] == "triggered"
    assert report["cancel_competition"]["count"] == 1
    race = report["cancel_competition"]["details"][0]
    assert race["task_id"] == "nav_a"
    assert race["in_flight"] is True
    assert race["outcome"] == "cancel_arrived_first_while_in_flight"
    assert race["cause"] == "participant_cancel"
    execution = next(row for row in report["executions"] if row["task_id"] == "nav_a")
    assert execution["status"] == "cancelled" and execution["end_s"] == 2.0
    with pytest.raises(RuntimeError, match="not in flight"):
        backend.complete("nav_a")


def test_completion_arriving_before_cancel_resolves_the_race():
    """Order 2: the real completion lands first and the late cancel loses."""
    session, recorder, backend, plan = _single_task_fixture()
    session.update_runtime_state(4.0, session.report()["runtime_state"]["robot_regions"],
                                 session.report()["runtime_state"]["object_states"])
    delivery = backend.complete("nav_a", status="succeeded")
    _commit(session, delivery)
    assert session.report()["tasks"]["nav_a"]["status"] == "succeeded"

    with pytest.raises(ValueError, match="only cancel an active task"):
        _submit(session, "cancel-late", "cancel_skill",
                {"task_id": "nav_a", "reason": "too late"})
    recorder.note_cancel_observation(
        "nav_a", at_s=session.report()["current_time_s"], in_flight=False,
        outcome="completion_arrived_first", cause="participant_cancel",
        extra={"completion_at_s": delivery["completed_at_s"]})

    report = build_concurrency_evidence_report(recorder)
    assert report["cancel_competition"]["count"] == 1
    race = report["cancel_competition"]["details"][0]
    assert race["outcome"] == "completion_arrived_first"
    assert race["in_flight"] is False
    assert race["completion_at_s"] == 10.0
    # Exactly one receipt exists; the losing cancel produced no cancellation.
    assert report["executions"][0]["status"] == "succeeded"
    assert report["cancellations_other"] == []


# ----------------------------------------------------------------------
# Mid-flight member exit (session state machine)
# ----------------------------------------------------------------------
def test_member_exit_while_in_flight_forces_cancellation_not_success():
    public, api, state = _component_fixture(deployment=True)
    plan = {"schema": "eai.benchmark.goal_plan", "version": GOAL_PLAN_VERSION,
            "plan_id": "exit-plan", "assigned_team": [GROUND_1],
            "tasks": [_nav_task("nav_a", GROUND_1, "rack_aisle_a",
                                "consolidation_to_rack_a", 0.0, 10.0)]}
    session, recorder, backend = _component_setup(
        public, api, plan, state, episode_id="exit-episode-1")
    _declare_assessment(session, "assess-0", "deployable")
    _submit(session, "team", "select_team", {"agent_ids": [GROUND_1]})
    _submit(session, "plan", "submit_plan", {"plan": plan})
    _submit(session, "dispatch-a", "dispatch_skill",
            {"task_id": "nav_a", "evidence_refs": []})
    backend.execute(plan["tasks"][0], episode_id="exit-episode-1",
                    trusted_report=session.report())
    session.update_runtime_state(4.0, state["robot_regions"], state["object_states"])

    session.emit("member_exit", 4.0, {"agent_id": GROUND_1})
    recorder.note_midflight_exit(
        "nav_a", agent_id=GROUND_1, at_s=4.0, in_flight=True,
        session_status_before="pending", session_status_after="cancel_requested")
    row = session.report()["tasks"]["nav_a"]
    assert row["status"] == "cancel_requested"
    assert row["safety_stop"]["requested"] is True

    # The state machine forbids a silent success after the invalidation.
    silent = backend.build_receipt("nav_a", status="succeeded", completed_at_s=4.0)
    with pytest.raises(ValueError, match="cannot silently succeed"):
        session.complete_skill("nav_a", time_s=4.0, status="succeeded", receipt=silent)

    result = backend.cancel(plan["tasks"][0], episode_id="exit-episode-1",
                            trusted_report=session.report())
    _commit(session, result)
    assert session.report()["tasks"]["nav_a"]["safety_stop"]["acknowledged"] is True

    report = build_concurrency_evidence_report(recorder)
    assert report["midflight_member_exits"]["status"] == "triggered"
    exit_detail = report["midflight_member_exits"]["details"][0]
    assert exit_detail["agent_id"] == GROUND_1
    assert exit_detail["in_flight"] is True
    assert exit_detail["task_outcome"] == {"status": "cancelled", "end_s": 4.0}
    # The safety stop is not a participant cancel race; it stays separate.
    assert report["cancel_competition"]["status"] == "not_triggered"
    assert report["cancellations_other"][0]["cause"] == "safety_stop_after_invalidation"


# ----------------------------------------------------------------------
# Post-termination refusal
# ----------------------------------------------------------------------
def test_not_deployable_termination_refuses_any_further_execution():
    public, api, state = _component_fixture(deployment=True)
    plan = {"schema": "eai.benchmark.goal_plan", "version": GOAL_PLAN_VERSION,
            "plan_id": "terminate-plan", "assigned_team": [GROUND_1],
            "tasks": [_nav_task("nav_a", GROUND_1, "rack_aisle_a",
                                "consolidation_to_rack_a", 0.0, 10.0)]}
    session, recorder, backend = _component_setup(
        public, api, plan, state, episode_id="terminate-episode-1")
    _declare_assessment(session, "assess-0", "deployable")
    _submit(session, "team", "select_team", {"agent_ids": [GROUND_1]})
    _submit(session, "plan", "submit_plan", {"plan": plan})
    _submit(session, "dispatch-a", "dispatch_skill",
            {"task_id": "nav_a", "evidence_refs": []})
    backend.execute(plan["tasks"][0], episode_id="terminate-episode-1",
                    trusted_report=session.report())
    session.update_runtime_state(2.0, state["robot_regions"], state["object_states"])

    declaration = deepcopy(
        session.report()["current_robot_declarations"][AERIAL])
    declaration["public_capabilities"] = [
        capability for capability in declaration["public_capabilities"]
        if capability != "aerial_navigation"]
    session.emit("capability_change", 2.0, {
        "agent_id": AERIAL, "capability_declaration": declaration,
        "capability_version": session.report()["capability_versions"][AERIAL] + 1,
    })
    assert session.report()["tasks"]["nav_a"]["status"] == "cancel_requested"
    _declare_assessment(session, "assess-1", "not_deployable",
                        reason_codes=["lost_unique_capability"], evidence_refs=[AERIAL])
    recorder.note_termination(2.0)
    assert session.report()["deployment_assessment"]["terminated"] is True

    # Safety cancellation still works after termination...
    result = backend.cancel(plan["tasks"][0], episode_id="terminate-episode-1",
                            trusted_report=session.report())
    _commit(session, result)
    assert session.report()["tasks"]["nav_a"]["status"] == "cancelled"
    # ...but the participant ledger and the backend both refuse new execution.
    with pytest.raises(ValueError, match="terminated the episode"):
        _submit(session, "late-team", "select_team", {"agent_ids": [GROUND_1]})
    with pytest.raises(PostTerminationExecutionRefused):
        backend.execute(plan["tasks"][0], episode_id="terminate-episode-1",
                        trusted_report=session.report())

    report = build_concurrency_evidence_report(recorder)
    assert report["post_termination_execution_attempts"]["status"] == "triggered"
    attempt = report["post_termination_execution_attempts"]["details"][0]
    assert attempt == {"task_id": "nav_a", "at_s": 2.0,
                       "operation": "execute",
                       "refusal": "PostTerminationExecutionRefused"}
    assert report["clock"]["terminated_at_s"] == 2.0
    assert report["cancellations_other"][0]["cause"] == "post_termination_safety_stop"
    assert report["cancellations_other"][0]["terminated"] is True


# ----------------------------------------------------------------------
# Report semantics: counterexample, schema, explicit untriggered
# ----------------------------------------------------------------------
def test_planned_overlap_with_serial_execution_is_not_concurrency_evidence():
    """Counterexample: overlapping planned windows, serial actual intervals."""
    recorder = ConcurrencyProbeRecorder(episode_id="serial-counter-1")
    recorder.note_plan({"tasks": [
        {"task_id": "a", "execution": {"start_s": 0.0, "duration_s": 10.0}},
        {"task_id": "b", "execution": {"start_s": 0.0, "duration_s": 20.0}},
    ]})
    recorder.note_execution_start(
        "a", skill="ground_navigation", start_s=0.0, planned_end_s=10.0,
        resources=["route:x"], mode="serial")
    recorder.note_execution_start(
        "b", skill="ground_navigation", start_s=10.0, planned_end_s=30.0,
        resources=["route:y"], mode="serial")
    recorder.note_execution_terminal(
        "a", end_s=10.0, status="succeeded", receipt_ref="serial:a")
    recorder.note_execution_terminal(
        "b", end_s=20.0, status="succeeded", receipt_ref="serial:b")

    report = build_concurrency_evidence_report(recorder)
    assert report["overlap_pairs"] == []
    assert len(report["planned_overlap_not_counted"]) == 1
    entry = report["planned_overlap_not_counted"][0]
    assert entry["tasks"] == ["a", "b"]
    assert entry["planned_overlap_s"] == [0.0, 10.0]
    assert entry["actual_intervals"] == {"a": [0.0, 10.0], "b": [10.0, 20.0]}
    assert entry["reason"] == "actual_execution_intervals_do_not_overlap"
    assert report["release"] is False
    assert report["scoreable"] is False
    assert report["execution_validated"] is False


def test_report_schema_flags_and_json_roundtrip():
    recorder = ConcurrencyProbeRecorder(
        episode_id="schema-1", backend_id="concurrent-fake-backend")
    recorder.note_execution_start(
        "only", skill="observe", start_s=0.0, planned_end_s=4.0,
        resources=["agent:r1"], mode="in_flight")
    recorder.note_execution_terminal(
        "only", end_s=4.0, status="failed", receipt_ref="schema:only")
    report = build_concurrency_evidence_report(
        recorder, task_id="T-1", scene_key="warehouse_nvd")

    assert report["schema"] == GOAL_CONCURRENCY_EVIDENCE_SCHEMA == (
        "eai.benchmark.goal_concurrency_evidence")
    assert report["version"] == 1
    assert report["episode_id"] == "schema-1"
    assert report["backend_id"] == "concurrent-fake-backend"
    assert "never counted" in report["evidence_definition"]
    assert report["release"] is False and report["scoreable"] is False
    assert report["execution_validated"] is False
    assert report["runner_integration"] is None
    assert report["execution_counts"] == {"total": 1, "closed": 1, "in_flight_open": 0}
    assert report["executions"][0]["receipt_ref"] == "schema:only"
    for key in ("integration_gap", "limitations"):
        assert isinstance(report[key], dict if key == "integration_gap" else list)
    encoded = json.dumps(report, ensure_ascii=False, allow_nan=False, sort_keys=True)
    assert json.loads(encoded) == build_concurrency_evidence_report(
        recorder, task_id="T-1", scene_key="warehouse_nvd")


def test_untriggered_categories_are_explicitly_marked():
    recorder = ConcurrencyProbeRecorder(episode_id="quiet-1")
    recorder.note_execution_start(
        "only", skill="observe", start_s=0.0, planned_end_s=4.0,
        resources=["agent:r1"], mode="in_flight")
    recorder.note_execution_terminal(
        "only", end_s=4.0, status="succeeded", receipt_ref="quiet:only")
    report = build_concurrency_evidence_report(recorder)
    for name in EVIDENCE_CATEGORIES:
        assert report[name] == {"status": "not_triggered", "count": 0, "details": []}, name
    assert report["planned_overlap_not_counted"] == []
    assert report["cancellations_other"] == []
    assert report["clock"]["terminated_at_s"] is None


# ----------------------------------------------------------------------
# Runner-level integration through the real FormalGoalEpisodeRunner loop
# ----------------------------------------------------------------------
class _RunnerAdapter:
    """Minimal participant: team/plan/dispatch, optional delayed publish."""

    def __init__(self, plan, dispatch_plan):
        self.plan = plan
        self.dispatch_plan = dispatch_plan  # task_id -> trigger action id
        self.submitted = False
        self.dispatched: set[str] = set()
        self.published = False
        self.peer = None
        self.comm_profile = None

    def manifest(self):
        return {"kind": "participant", "id": "concurrency-evidence-test",
                "version": 1, "interface": "eai.benchmark.goal_algorithm_adapter",
                "interface_version": 1, "evaluation_track": "goal_driven",
                "input_scope": "participant_visible_events_only",
                "control_scope": "benchmark_managed_low_level_control"}

    def initialize(self, public_task, participant_api):
        pass

    def initialize_communication_tools(self, profile):
        self.comm_profile = profile

    def act(self, events):
        now = max((row["time_s"] for row in events), default=0.0)
        if not self.submitted:
            self.submitted = True
            return [_action("team", "select_team",
                            {"agent_ids": self.plan["assigned_team"]},
                            submitted_at_s=now),
                    _action("plan", "submit_plan", {"plan": self.plan},
                            submitted_at_s=now)]
        for task_id, trigger in self.dispatch_plan.items():
            if task_id in self.dispatched:
                continue
            if trigger is None or any(
                    row["type"] == "action_result"
                    and row["payload"]["action_id"] == trigger
                    and row["payload"]["status"] == "succeeded"
                    for row in events):
                self.dispatched.add(task_id)
                return [_action(f"dispatch-{task_id}", "dispatch_skill",
                                {"task_id": task_id, "evidence_refs": []},
                                submitted_at_s=now)]
        if (self.peer is not None and not self.published
                and all(task_id in self.dispatched for task_id in self.dispatch_plan)
                and any(row["type"] == "action_result"
                        and row["payload"]["action_id"] == (
                            f"dispatch-{max(self.dispatch_plan)}")
                        and row["payload"]["status"] == "succeeded"
                        for row in events)):
            self.published = True
            return [{
                "schema": "eai.benchmark.goal_communication_tool_call", "version": 1,
                "call_id": "publish-1", "submitted_at_s": now, "operation": "publish",
                "arguments": {"producer_id": f"robot:{self.plan['assigned_team'][0]}",
                              "recipients": [f"robot:{self.peer}"],
                              "channel": "relay", "estimate": {"note": "idle clock probe"},
                              "valid_until_s": now + 8.0},
            }]
        return []

    def close(self):
        pass


def test_runner_different_resource_plan_executes_serially_and_advances_idle(tmp_path: Path):
    """Real runner loop: different-resource two-skill plan, planned overlap,
    strictly serial actual intervals, and the fake backend's ``advance_idle``
    feeding the runner's idle clock through a delayed communication message."""
    public, api, state = _component_fixture()
    plan = {"schema": "eai.benchmark.goal_plan", "version": GOAL_PLAN_VERSION,
            "plan_id": "runner-overlap-plan", "assigned_team": [GROUND_1, GROUND_3],
            "tasks": [
                _nav_task("nav_a", GROUND_1, "rack_aisle_a",
                          "consolidation_to_rack_a", 0.0, 10.0),
                _nav_task("nav_b", GROUND_3, "rack_aisle_b",
                          "consolidation_to_rack_b", 0.0, 20.0),
            ]}
    episode_id = "runner-overlap-1"
    recorder = ConcurrencyProbeRecorder(episode_id=episode_id)
    backend = ConcurrentFakeBackend(
        public, episode_id=episode_id, recorder=recorder, runtime_state=state,
        serial=True)
    recorder.note_plan(plan)
    adapter = _RunnerAdapter(plan, {"nav_a": None, "nav_b": None})
    pool = [row["id"] for row in public["candidate_robot_pool"]]
    adapter.peer = pool[2]
    tools = GoalCommunicationTools(
        public, episode_id=episode_id,
        endpoints={"edges": {"edge_1": [pool[1]]}, "robots": pool[:3]},
        runtime=CommunicationRuntime(
            {"pools": {"p2p": {"bytes_per_second": 100000, "fixed_delay_s": 0.04}}}))
    root = tmp_path / "episode"
    result = FormalGoalEpisodeRunner(
        public, api, adapter, backend, output_root=root, episode_id=episode_id,
        communication_tools=tools).run(initial_state=state)

    assert result["error"] is None
    assert result["skill_receipt_count"] == 2
    assert backend.execute_calls == [("nav_a", 0.0), ("nav_b", 10.0)]
    # The delayed message forced the runner to drive the backend idle clock.
    assert backend.idle_calls and backend.idle_calls[0] > 20.0
    receipts = json.loads((root / "skill_receipts.json").read_text())
    assert [row["completed_at_s"] for row in receipts] == [10.0, 20.0]
    session_report = json.loads(
        (root / "goal_session_report.json").read_text())["report"]
    assert {tid: row["status"] for tid, row in session_report["tasks"].items()} == {
        "nav_a": "succeeded", "nav_b": "succeeded"}

    report = build_concurrency_evidence_report(
        recorder, task_id=public["task_id"],
        scene_key=public["mission"]["scene_key"], runner_result=result)
    assert report["overlap_pairs"] == []
    assert len(report["planned_overlap_not_counted"]) == 1
    entry = report["planned_overlap_not_counted"][0]
    assert entry["tasks"] == ["nav_a", "nav_b"]
    assert entry["planned_overlap_s"] == [0.0, 10.0]
    assert entry["actual_intervals"] == {"nav_a": [0.0, 10.0], "nav_b": [10.0, 20.0]}
    assert report["runner_integration"] == {
        "integrated": True, "turns": result["turns"], "error": None,
        "skill_receipt_count": 2, "termination": None}
    assert report["clock"]["idle_advances"]
    assert report["release"] is False and report["scoreable"] is False
    replay = json.loads((root / "communication_tool_replay.json").read_text())
    assert replay["validated"] is True
    assert not (root / "lifecycle_errors.json").exists()


def test_runner_same_resource_plan_holds_the_resource_serially(tmp_path: Path):
    """Real runner loop, same-resource control: the plan must declare disjoint
    windows, the runner executes them one after the other, and the evidence
    report shows the resource held serially with no concurrency credited."""
    public, api, state = _component_fixture(ground_3_region="rack_aisle_a")
    plan = {"schema": "eai.benchmark.goal_plan", "version": GOAL_PLAN_VERSION,
            "plan_id": "runner-same-resource-plan",
            "assigned_team": [GROUND_1, GROUND_3],
            "tasks": [
                _nav_task("nav_a", GROUND_1, "rack_aisle_a",
                          "consolidation_to_rack_a", 0.0, 10.0),
                _nav_task("nav_reverse", GROUND_3, "consolidation_area",
                          "rack_a_to_consolidation", 10.0, 8.0),
            ]}
    episode_id = "runner-same-resource-1"
    recorder = ConcurrencyProbeRecorder(episode_id=episode_id)
    backend = ConcurrentFakeBackend(
        public, episode_id=episode_id, recorder=recorder, runtime_state=state,
        serial=True)
    recorder.note_plan(plan)
    adapter = _RunnerAdapter(plan, {"nav_a": None, "nav_reverse": "dispatch-nav_a"})
    root = tmp_path / "episode"
    result = FormalGoalEpisodeRunner(
        public, api, adapter, backend, output_root=root,
        episode_id=episode_id).run(initial_state=state)

    assert result["error"] is None
    assert backend.execute_calls == [("nav_a", 0.0), ("nav_reverse", 10.0)]
    receipts = json.loads((root / "skill_receipts.json").read_text())
    assert [row["completed_at_s"] for row in receipts] == [10.0, 18.0]

    report = build_concurrency_evidence_report(recorder, runner_result=result)
    assert report["overlap_pairs"] == []
    # Planned windows touch but do not overlap; nothing was promised.
    assert report["planned_overlap_not_counted"] == []
    holdings = report["resource_intervals"]["route:crate_aisle"]
    assert [(row["task_id"], row["start_s"], row["end_s"]) for row in holdings] == [
        ("nav_a", 0.0, 10.0), ("nav_reverse", 10.0, 18.0)]
    assert report["resource_conflicts"]["status"] == "not_triggered"


def test_runner_termination_stops_execution_and_backend_refuses_afterwards(
        tmp_path: Path, monkeypatch):
    """Real runner loop, v3 deployment: a capability change after the first
    skill triggers the required reassessment; the participant requests human
    assistance, the queued skill is safety-cancelled, and the fake backend
    refuses any execution attempted after termination."""
    public, api, state = _component_fixture(deployment=True)
    first = _nav_task("nav_ground", GROUND_1, "rack_aisle_a",
                      "consolidation_to_rack_a", 0.0, 10.0)
    second = _aerial_task("nav_air", AERIAL, "rack_overview",
                          "aisle_to_overview_air", 0.0, 12.0)
    plan = {"schema": "eai.benchmark.goal_plan", "version": GOAL_PLAN_VERSION,
            "plan_id": "runner-termination-plan", "assigned_team": [GROUND_1, AERIAL],
            "tasks": [first, second]}
    episode_id = "runner-termination-1"
    host_box: dict[str, Any] = {}

    class _EventEmittingHost(GoalAlgorithmHost):
        def complete_skill(self, task_id, *, time_s, status, receipt):
            result = super().complete_skill(
                task_id, time_s=time_s, status=status, receipt=receipt)
            request = host_box.pop("event", None)
            if request is not None:
                self.emit("capability_change", time_s, request)
            return result

    monkeypatch.setattr(runner_module, "GoalAlgorithmHost", _EventEmittingHost)

    recorder = ConcurrencyProbeRecorder(episode_id=episode_id)
    recorder.note_plan(plan)

    class _TerminationAdapter(_RunnerAdapter):
        """Declares each assessment epoch and dispatches both skills together
        once the initial deployable declaration has been accepted."""

        def act(self, events):
            required = next(
                (row for row in reversed(events)
                 if row["type"] == "deployment_assessment_required"), None)
            if required is not None:
                payload = required["payload"]
                return [_action(
                    f"assess-{payload['assessment_epoch']}", "deployment_assessment",
                    {"assessment_id": payload["assessment_id"],
                     "assessment_epoch": payload["assessment_epoch"],
                     "roster_version": payload["roster_version"],
                     "capability_versions": payload["capability_versions"],
                     "decision": ("deployable" if payload["assessment_epoch"] == 0
                                  else "not_deployable"),
                     "reason_codes": ([] if payload["assessment_epoch"] == 0
                                      else ["lost_unique_capability"]),
                     "evidence_refs": ([] if payload["assessment_epoch"] == 0
                                       else [AERIAL])},
                    submitted_at_s=required["time_s"])]
            if (not self.submitted and any(
                    row["type"] == "action_result"
                    and row["payload"]["action_id"] == "assess-0"
                    for row in events)):
                self.submitted = True
                now = max(row["time_s"] for row in events)
                return [_action("team", "select_team",
                                {"agent_ids": self.plan["assigned_team"]},
                                submitted_at_s=now),
                        _action("plan", "submit_plan", {"plan": self.plan},
                                submitted_at_s=now),
                        _action("dispatch-nav_ground", "dispatch_skill",
                                {"task_id": "nav_ground", "evidence_refs": []},
                                submitted_at_s=now),
                        _action("dispatch-nav_air", "dispatch_skill",
                                {"task_id": "nav_air", "evidence_refs": []},
                                submitted_at_s=now)]
            return []

    class _EventBackend(ConcurrentFakeBackend):
        def execute(self, task, *, episode_id, trusted_report):
            result = super().execute(task, episode_id=episode_id,
                                     trusted_report=trusted_report)
            if task["task_id"] == "nav_ground":
                session = trusted_report["session"]
                declaration = deepcopy(
                    session["current_robot_declarations"][AERIAL])
                declaration["public_capabilities"] = [
                    capability for capability in declaration["public_capabilities"]
                    if capability != "aerial_navigation"]
                host_box["event"] = {
                    "agent_id": AERIAL, "capability_declaration": declaration,
                    "capability_version": (
                        session["capability_versions"][AERIAL] + 1),
                }
            return result

    event_backend = _EventBackend(
        public, episode_id=episode_id, recorder=recorder, runtime_state=state,
        serial=True)
    adapter = _TerminationAdapter(plan, {"nav_ground": None, "nav_air": None})
    root = tmp_path / "episode"
    result = FormalGoalEpisodeRunner(
        public, api, adapter, event_backend, output_root=root,
        episode_id=episode_id).run(initial_state=state)

    assert result["error"] is None
    assert result["termination"]["category"] == "human_assistance_requested"
    assert event_backend.execute_calls == [("nav_ground", 0.0)]
    assert event_backend.cancel_calls == ["nav_air"]
    session_report = json.loads(
        (root / "goal_session_report.json").read_text())["report"]
    assert session_report["tasks"]["nav_ground"]["status"] == "succeeded"
    stopped = session_report["tasks"]["nav_air"]
    assert stopped["status"] == "cancelled"
    assert stopped["safety_stop"]["acknowledged"] is True

    # After the episode, a stray host-side execute is refused by the backend.
    archived = {"session": session_report}
    with pytest.raises(PostTerminationExecutionRefused):
        event_backend.execute(second, episode_id=episode_id, trusted_report=archived)

    report = build_concurrency_evidence_report(
        recorder, runner_result=result, task_id=public["task_id"])
    assert report["runner_integration"]["termination"]["category"] == (
        "human_assistance_requested")
    assert report["post_termination_execution_attempts"]["count"] == 1
    assert report["post_termination_execution_attempts"]["details"][0][
        "operation"] == "execute"
    assert report["overlap_pairs"] == []
    # The runner drains the safety stop before the participant declares the
    # termination, so the queued skill was cancelled after invalidation while
    # the episode was not yet terminated; no execution followed either way.
    cancels = report["cancellations_other"]
    assert cancels and cancels[0]["cause"] == "safety_stop_after_invalidation"
    assert cancels[0]["in_flight"] is False


# ----------------------------------------------------------------------
# Runner-level async execution mode (opt-in, capability-negotiated)
# ----------------------------------------------------------------------
class _AsyncBatchAdapter:
    """Participant that submits team, plan, and every dispatch in one batch."""

    def __init__(self, plan, task_ids, *, peer=None):
        self.plan = plan
        self.task_ids = list(task_ids)
        self.peer = peer
        self.submitted = False
        self.published = False
        self.seen_events: list[dict[str, Any]] = []

    def manifest(self):
        return {"kind": "participant", "id": "concurrency-evidence-async-test",
                "version": 1, "interface": "eai.benchmark.goal_algorithm_adapter",
                "interface_version": 1, "evaluation_track": "goal_driven",
                "input_scope": "participant_visible_events_only",
                "control_scope": "benchmark_managed_low_level_control"}

    def initialize(self, public_task, participant_api):
        pass

    def initialize_communication_tools(self, profile):
        pass

    def act(self, events):
        self.seen_events.extend(events)
        events = self.seen_events
        now = max((row["time_s"] for row in events), default=0.0)
        if (not self.submitted
                and any(row["type"] == "episode_started" for row in events)):
            self.submitted = True
            actions = [
                _action("team", "select_team",
                        {"agent_ids": self.plan["assigned_team"]}, submitted_at_s=now),
                _action("plan", "submit_plan", {"plan": self.plan},
                        submitted_at_s=now),
            ]
            actions.extend(
                _action(f"dispatch-{task_id}", "dispatch_skill",
                        {"task_id": task_id, "evidence_refs": []},
                        submitted_at_s=now)
                for task_id in self.task_ids)
            return actions
        if (self.peer is not None and not self.published
                and all(any(row["type"] == "action_result"
                            and row["payload"]["action_id"] == f"dispatch-{task_id}"
                            and row["payload"]["status"] == "succeeded"
                            for row in events)
                        for task_id in self.task_ids)):
            self.published = True
            return [{
                "schema": "eai.benchmark.goal_communication_tool_call", "version": 1,
                "call_id": "async-publish-1", "submitted_at_s": now,
                "operation": "publish",
                "arguments": {"producer_id": f"robot:{self.plan['assigned_team'][0]}",
                              "recipients": [f"robot:{self.peer}"],
                              "channel": "relay",
                              "estimate": {"note": "async idle clock probe"},
                              "valid_until_s": now + 8.0},
            }]
        return []

    def close(self):
        pass


def _pick_task(task_id, agent, object_id, pickup_region, start_s, duration_s):
    return {
        "task_id": task_id, "skill": "pick",
        "arguments": {"object_id": object_id, "pickup_region_id": pickup_region,
                      "handling_mode_id": "single_grip",
                      "carrier_bindings": [
                          {"agent_id": agent, "point_id": "center_grip"}]},
        "required_capabilities": ["pick"], "assigned_agents": [agent],
        "prerequisites": [], "evidence_refs": [],
        "execution": {"start_s": start_s, "duration_s": duration_s},
    }


def _async_recorder_backend(public, state, *, episode_id):
    recorder = ConcurrencyProbeRecorder(episode_id=episode_id)
    backend = ConcurrentFakeBackend(
        public, episode_id=episode_id, recorder=recorder, runtime_state=state)
    return recorder, backend


def test_async_mode_fails_closed_without_declared_backend_capability(tmp_path):
    public, api, state = _component_fixture()
    plan = {"schema": "eai.benchmark.goal_plan", "version": GOAL_PLAN_VERSION,
            "plan_id": "fail-closed-plan", "assigned_team": [GROUND_1],
            "tasks": [_nav_task("nav_a", GROUND_1, "rack_aisle_a",
                                "consolidation_to_rack_a", 0.0, 10.0)]}
    recorder, backend = _async_recorder_backend(public, state, episode_id="fail-closed-1")
    serial_backend = ConcurrentFakeBackend(
        public, episode_id="fail-closed-1", recorder=recorder,
        runtime_state=state, serial=True)
    adapter = _AsyncBatchAdapter(plan, ["nav_a"])

    def _runner(backend, **kwargs):
        return FormalGoalEpisodeRunner(
            public, api, adapter, backend, output_root=tmp_path / "fail-closed",
            episode_id="fail-closed-1", **kwargs)

    # The in-flight fake backend declares the capability; serial mode does not.
    assert backend.supports_async_execution is True
    assert serial_backend.supports_async_execution is False
    with pytest.raises(ValueError, match="supports_async_execution"):
        _runner(serial_backend, execution_mode="async")
    with pytest.raises(ValueError, match="execution_mode must be 'serial' or 'async'"):
        _runner(serial_backend, execution_mode="parallel")
    with pytest.raises(ValueError, match="concurrency_recorder requires"):
        _runner(serial_backend, concurrency_recorder=recorder)

    class _NoPumpBackend:
        supports_async_execution = True

        def execute(self, task, *, episode_id, trusted_report):
            return {"status": "in_flight"}

    class _NoDeliveriesBackend(_NoPumpBackend):
        def advance_idle(self, target_s):
            return {"time_s": target_s}

    with pytest.raises(ValueError, match="advance_idle"):
        _runner(_NoPumpBackend(), execution_mode="async")
    with pytest.raises(ValueError, match="completed_deliveries"):
        _runner(_NoDeliveriesBackend(), execution_mode="async")

    from demo.benchmark.communication import CommunicationRuntime
    from demo.benchmark.provenance import canonical_sha256

    class _StubDownlink:
        def __init__(self, episode_id, task_sha256, runtime):
            self.episode_id = episode_id
            self.public_task_sha256 = task_sha256
            self.runtime = runtime

        def profile(self):
            return {}

        def queue_dispatch(self, *args, **kwargs):
            return None

        def queue_cancel(self, *args, **kwargs):
            return None

        def advance(self, now, **kwargs):
            return {"failed_dispatches": [], "ready_cancels": [],
                    "failed_cancels": [], "ready_dispatches": []}

        def has_pending_work(self):
            return False

        def next_wakeup_s(self):
            return None

        def cancellation_state(self, task_id, **kwargs):
            return {"status": "pending"}

        def report(self):
            return {}

    runtime = CommunicationRuntime()
    downlink = _StubDownlink(
        "fail-closed-1", canonical_sha256(public), runtime)
    with pytest.raises(ValueError, match="does not support the execution downlink"):
        _runner(backend, execution_mode="async",
                shared_communication_runtime=runtime, downlink=downlink)


def test_runner_async_mode_runs_two_skills_simultaneously(tmp_path: Path):
    """Real runner loop in async mode: both dispatches start at t=0, stay
    simultaneously in flight, complete on the backend clock with real
    receipts, and the archived evidence credits the genuine overlap.  A
    delayed communication message afterwards exercises the idle branch on
    the same backend clock."""
    public, api, state = _component_fixture()
    plan = {"schema": "eai.benchmark.goal_plan", "version": GOAL_PLAN_VERSION,
            "plan_id": "runner-async-overlap-plan", "assigned_team": [GROUND_1, GROUND_3],
            "tasks": [
                _nav_task("nav_a", GROUND_1, "rack_aisle_a",
                          "consolidation_to_rack_a", 0.0, 10.0),
                _nav_task("nav_b", GROUND_3, "rack_aisle_b",
                          "consolidation_to_rack_b", 0.0, 8.0),
            ]}
    episode_id = "runner-async-overlap-1"
    recorder, backend = _async_recorder_backend(public, state, episode_id=episode_id)
    pool = [row["id"] for row in public["candidate_robot_pool"]]
    tools = GoalCommunicationTools(
        public, episode_id=episode_id,
        endpoints={"edges": {"edge_1": [pool[1]]}, "robots": pool[:3]},
        runtime=CommunicationRuntime(
            {"pools": {"p2p": {"bytes_per_second": 100000, "fixed_delay_s": 0.04}}}))
    root = tmp_path / "episode"
    result = FormalGoalEpisodeRunner(
        public, api, _AsyncBatchAdapter(plan, ["nav_a", "nav_b"], peer=pool[2]),
        backend, output_root=root, episode_id=episode_id,
        communication_tools=tools,
        execution_mode="async", concurrency_recorder=recorder).run(initial_state=state)

    assert result["error"] is None
    assert result["skill_receipt_count"] == 2
    assert result["execution_mode"] == "async"
    # Both skills were dispatched at t=0 and closed with real receipts.
    assert backend.execute_calls == [("nav_a", 0.0), ("nav_b", 0.0)]
    assert backend.in_flight_task_ids() == []
    receipts = json.loads((root / "skill_receipts.json").read_text())
    assert sorted(row["completed_at_s"] for row in receipts) == [8.0, 10.0]
    session_report = json.loads(
        (root / "goal_session_report.json").read_text())["report"]
    assert {tid: row["status"] for tid, row in session_report["tasks"].items()} == {
        "nav_a": "succeeded", "nav_b": "succeeded"}
    # The delayed message kept the backend clock moving past both completions.
    assert session_report["current_time_s"] > 10.0

    evidence = json.loads((root / "concurrency_evidence.json").read_text())
    assert evidence["schema"] == GOAL_CONCURRENCY_EVIDENCE_SCHEMA
    assert evidence["episode_id"] == episode_id
    assert evidence["execution_counts"] == {"total": 2, "closed": 2, "in_flight_open": 0}
    assert evidence["runner_integration"] == {
        "integrated": True, "turns": result["turns"], "error": None,
        "skill_receipt_count": 2, "termination": None}
    assert len(evidence["overlap_pairs"]) == 1
    pair = evidence["overlap_pairs"][0]
    assert pair["tasks"] == ["nav_a", "nav_b"]
    assert (pair["overlap_start_s"], pair["overlap_end_s"]) == (0.0, 8.0)
    assert pair["statuses"] == {"nav_a": "succeeded", "nav_b": "succeeded"}
    assert set(pair["evidence_refs"]) == {row["receipt_ref"] for row in receipts}
    assert evidence["planned_overlap_not_counted"] == []
    assert evidence["release"] is False and evidence["scoreable"] is False
    # The pump drove the clock to each planned end; the idle branch then
    # carried the delayed communication delivery past both completions.
    assert backend.idle_calls[:2] == [8.0, 10.0]
    assert backend.idle_calls[-1] > 10.0
    manifest = json.loads((root / "episode_manifest.json").read_text())
    assert manifest["execution_mode"] == "async"
    assert manifest["concurrency_evidence"]["archive"] == "concurrency_evidence.json"
    replay = json.loads((root / "communication_tool_replay.json").read_text())
    assert replay["validated"] is True
    assert not (root / "lifecycle_errors.json").exists()


def test_runner_async_mode_backend_mutex_refuses_then_resumes_serially(tmp_path: Path):
    """Runner-level backend mutex: the session admits both picks (the shared
    pickup region has capacity two) while the fake backend refuses the second
    in-flight hold; the refused dispatch stays pending, is retried after the
    first terminal releases the region, and completes inside its admitted
    window.  No overlap is credited."""
    public, api, state = _component_fixture()
    state["robot_regions"][GROUND_1] = "rack_aisle_a"
    state["robot_regions"][GROUND_3] = "rack_aisle_a"
    state["object_states"]["order_carton_01"]["region_id"] = "rack_aisle_a"
    plan = {"schema": "eai.benchmark.goal_plan", "version": GOAL_PLAN_VERSION,
            "plan_id": "runner-async-mutex-plan", "assigned_team": [GROUND_1, GROUND_3],
            "tasks": [
                _pick_task("pick_a", GROUND_1, "order_crate_01",
                           "rack_aisle_a", 0.0, 10.0),
                _pick_task("pick_b", GROUND_3, "order_carton_01",
                           "rack_aisle_a", 0.0, 12.0),
            ]}
    episode_id = "runner-async-mutex-1"
    recorder, backend = _async_recorder_backend(public, state, episode_id=episode_id)
    root = tmp_path / "episode"
    result = FormalGoalEpisodeRunner(
        public, api, _AsyncBatchAdapter(plan, ["pick_a", "pick_b"]),
        backend, output_root=root, episode_id=episode_id,
        execution_mode="async", concurrency_recorder=recorder).run(initial_state=state)

    assert result["error"] is None
    assert result["skill_receipt_count"] == 2
    session_report = json.loads(
        (root / "goal_session_report.json").read_text())["report"]
    assert {tid: row["status"] for tid, row in session_report["tasks"].items()} == {
        "pick_a": "succeeded", "pick_b": "succeeded"}

    evidence = json.loads((root / "concurrency_evidence.json").read_text())
    conflicts = evidence["resource_conflicts"]
    assert conflicts["status"] == "triggered"
    (detail,) = conflicts["details"]
    assert detail == {
        "task_id": "pick_b", "resource_key": "region:rack_aisle_a",
        "held_by": "pick_a", "at_s": 0.0, "layer": "backend_mutex",
        "detail": ("fake backend refused a second in-flight execution holding "
                   "region:rack_aisle_a"),
    }
    # The mutex kept the region serially held; the planned overlap of both
    # windows is explicitly not counted as concurrency evidence.
    assert evidence["overlap_pairs"] == []
    holdings = evidence["resource_intervals"]["region:rack_aisle_a"]
    assert [(row["task_id"], row["start_s"], row["end_s"], row["status"])
            for row in holdings] == [
        ("pick_a", 0.0, 10.0, "succeeded"),
        ("pick_b", 10.0, 12.0, "succeeded")]
    assert len(evidence["planned_overlap_not_counted"]) == 1
    assert evidence["planned_overlap_not_counted"][0]["tasks"] == ["pick_a", "pick_b"]
    assert evidence["planned_overlap_not_counted"][0][
        "reason"] == "actual_execution_intervals_do_not_overlap"


def test_runner_async_mode_termination_never_starts_the_queued_dispatch(
        tmp_path: Path, monkeypatch):
    """Real runner loop, async mode, v3 deployment: the queued pick is refused
    by the backend mutex, a capability change then invalidates it, the
    participant requests human assistance, and the queued dispatch is
    safety-cancelled without ever starting; no execution happens after the
    termination."""
    public, api, state = _component_fixture(deployment=True)
    state["robot_regions"][GROUND_1] = "rack_aisle_a"
    state["robot_regions"][GROUND_3] = "rack_aisle_a"
    state["object_states"]["order_carton_01"]["region_id"] = "rack_aisle_a"
    plan = {"schema": "eai.benchmark.goal_plan", "version": GOAL_PLAN_VERSION,
            "plan_id": "runner-async-termination-plan",
            "assigned_team": [GROUND_1, GROUND_3],
            "tasks": [
                _pick_task("pick_a", GROUND_1, "order_crate_01",
                           "rack_aisle_a", 0.0, 10.0),
                _pick_task("pick_b", GROUND_3, "order_carton_01",
                           "rack_aisle_a", 0.0, 12.0),
            ]}
    episode_id = "runner-async-termination-1"
    host_box: dict[str, Any] = {}

    class _EventEmittingHost(GoalAlgorithmHost):
        def complete_skill(self, task_id, *, time_s, status, receipt):
            result = super().complete_skill(
                task_id, time_s=time_s, status=status, receipt=receipt)
            request = host_box.pop("event", None)
            if request is not None:
                self.emit("capability_change", time_s, request)
            return result

    monkeypatch.setattr(runner_module, "GoalAlgorithmHost", _EventEmittingHost)

    class _AssessmentAdapter:
        """Declares every assessment epoch and batch-dispatches both picks."""

        def __init__(self, plan):
            self.plan = plan
            self.submitted = False

        def manifest(self):
            return {"kind": "participant", "id": "concurrency-evidence-async-term",
                    "version": 1, "interface": "eai.benchmark.goal_algorithm_adapter",
                    "interface_version": 1, "evaluation_track": "goal_driven",
                    "input_scope": "participant_visible_events_only",
                    "control_scope": "benchmark_managed_low_level_control"}

        def initialize(self, public_task, participant_api):
            pass

        def act(self, events):
            required = next(
                (row for row in reversed(events)
                 if row["type"] == "deployment_assessment_required"), None)
            if required is not None:
                payload = required["payload"]
                return [_action(
                    f"assess-{payload['assessment_epoch']}", "deployment_assessment",
                    {"assessment_id": payload["assessment_id"],
                     "assessment_epoch": payload["assessment_epoch"],
                     "roster_version": payload["roster_version"],
                     "capability_versions": payload["capability_versions"],
                     "decision": ("deployable" if payload["assessment_epoch"] == 0
                                  else "not_deployable"),
                     "reason_codes": ([] if payload["assessment_epoch"] == 0
                                      else ["lost_unique_capability"]),
                     "evidence_refs": ([] if payload["assessment_epoch"] == 0
                                       else [AERIAL])},
                    submitted_at_s=required["time_s"])]
            if (not self.submitted and any(
                    row["type"] == "action_result"
                    and row["payload"]["action_id"] == "assess-0"
                    for row in events)):
                self.submitted = True
                now = max(row["time_s"] for row in events)
                actions = [
                    _action("team", "select_team",
                            {"agent_ids": self.plan["assigned_team"]},
                            submitted_at_s=now),
                    _action("plan", "submit_plan", {"plan": self.plan},
                            submitted_at_s=now),
                ]
                actions.extend(
                    _action(f"dispatch-{task_id}", "dispatch_skill",
                            {"task_id": task_id, "evidence_refs": []},
                            submitted_at_s=now)
                    for task_id in ("pick_a", "pick_b"))
                return actions
            return []

        def close(self):
            pass

    class _EventBackend(ConcurrentFakeBackend):
        def execute(self, task, *, episode_id, trusted_report):
            result = super().execute(task, episode_id=episode_id,
                                     trusted_report=trusted_report)
            if task["task_id"] == "pick_a":
                session = trusted_report["session"]
                declaration = deepcopy(
                    session["current_robot_declarations"][AERIAL])
                declaration["public_capabilities"] = [
                    capability for capability in declaration["public_capabilities"]
                    if capability != "aerial_navigation"]
                host_box["event"] = {
                    "agent_id": AERIAL, "capability_declaration": declaration,
                    "capability_version": (
                        session["capability_versions"][AERIAL] + 1),
                }
            return result

    recorder, _ = _async_recorder_backend(public, state, episode_id=episode_id)
    backend = _EventBackend(
        public, episode_id=episode_id, recorder=recorder, runtime_state=state)
    root = tmp_path / "episode"
    result = FormalGoalEpisodeRunner(
        public, api, _AssessmentAdapter(plan), backend, output_root=root,
        episode_id=episode_id,
        execution_mode="async", concurrency_recorder=recorder).run(initial_state=state)

    assert result["error"] is None
    assert result["termination"]["category"] == "human_assistance_requested"
    # pick_b was attempted once (refused by the backend mutex) and never
    # started; no execution was attempted after the termination.
    assert backend.execute_calls == [("pick_a", 0.0), ("pick_b", 0.0)]
    assert backend.cancel_calls == ["pick_b"]
    session_report = json.loads(
        (root / "goal_session_report.json").read_text())["report"]
    assert session_report["tasks"]["pick_a"]["status"] == "succeeded"
    stopped = session_report["tasks"]["pick_b"]
    assert stopped["status"] == "cancelled"
    assert stopped["safety_stop"]["acknowledged"] is True

    evidence = json.loads((root / "concurrency_evidence.json").read_text())
    assert evidence["execution_counts"] == {"total": 1, "closed": 1, "in_flight_open": 0}
    assert evidence["executions"][0]["task_id"] == "pick_a"
    assert evidence["resource_conflicts"]["status"] == "triggered"
    assert evidence["post_termination_execution_attempts"] == {
        "status": "not_triggered", "count": 0, "details": []}
    assert evidence["clock"]["terminated_at_s"] == 10.0
    assert evidence["runner_integration"]["termination"]["category"] == (
        "human_assistance_requested")
    assert evidence["overlap_pairs"] == []


def test_runner_async_member_exit_safety_cancels_in_flight_skill(
        tmp_path: Path, monkeypatch):
    """Real runner loop, async mode: while both skills are genuinely in
    flight, one member exits; the runner safety-cancels that member's in-
    flight skill through the session state machine (no silent success), the
    other skill keeps its real success, and the overlap with the cancelled
    interval is still credited as concurrency evidence."""
    public, api, state = _component_fixture()
    plan = {"schema": "eai.benchmark.goal_plan", "version": GOAL_PLAN_VERSION,
            "plan_id": "runner-async-exit-plan", "assigned_team": [GROUND_1, GROUND_3],
            "tasks": [
                _nav_task("nav_a", GROUND_1, "rack_aisle_a",
                          "consolidation_to_rack_a", 0.0, 10.0),
                _nav_task("nav_b", GROUND_3, "rack_aisle_b",
                          "consolidation_to_rack_b", 0.0, 8.0),
            ]}
    episode_id = "runner-async-exit-1"
    host_box: dict[str, Any] = {}

    class _ExitEmittingHost(GoalAlgorithmHost):
        def complete_skill(self, task_id, *, time_s, status, receipt):
            result = super().complete_skill(
                task_id, time_s=time_s, status=status, receipt=receipt)
            request = host_box.pop("exit", None)
            if request is not None:
                self.emit("member_exit", time_s, request)
            return result

    monkeypatch.setattr(runner_module, "GoalAlgorithmHost", _ExitEmittingHost)

    class _ExitBackend(ConcurrentFakeBackend):
        def execute(self, task, *, episode_id, trusted_report):
            result = super().execute(task, episode_id=episode_id,
                                     trusted_report=trusted_report)
            if task["task_id"] == "nav_b":
                host_box["exit"] = {"agent_id": GROUND_1}
            return result

    recorder, _ = _async_recorder_backend(public, state, episode_id=episode_id)
    backend = _ExitBackend(
        public, episode_id=episode_id, recorder=recorder, runtime_state=state)
    root = tmp_path / "episode"
    result = FormalGoalEpisodeRunner(
        public, api, _AsyncBatchAdapter(plan, ["nav_a", "nav_b"]),
        backend, output_root=root, episode_id=episode_id,
        execution_mode="async", concurrency_recorder=recorder).run(initial_state=state)

    assert result["error"] is None
    assert result["skill_receipt_count"] == 2
    session_report = json.loads(
        (root / "goal_session_report.json").read_text())["report"]
    assert session_report["tasks"]["nav_a"]["status"] == "cancelled"
    assert session_report["tasks"]["nav_b"]["status"] == "succeeded"

    evidence = json.loads((root / "concurrency_evidence.json").read_text())
    exits = evidence["midflight_member_exits"]
    assert exits["status"] == "triggered"
    (exit_detail,) = exits["details"]
    assert exit_detail == {
        "task_id": "nav_a", "agent_id": GROUND_1, "at_s": 8.0,
        "in_flight": True, "session_status_before": "pending",
        "session_status_after": "cancelled",
        "task_outcome": {"status": "cancelled", "end_s": 8.0},
    }
    executions = {row["task_id"]: row for row in evidence["executions"]}
    assert executions["nav_a"]["status"] == "cancelled"
    assert (executions["nav_a"]["start_s"], executions["nav_a"]["end_s"]) == (0.0, 8.0)
    assert executions["nav_b"]["status"] == "succeeded"
    # The cancelled and succeeded intervals genuinely overlapped.
    assert len(evidence["overlap_pairs"]) == 1
    pair = evidence["overlap_pairs"][0]
    assert pair["tasks"] == ["nav_a", "nav_b"]
    assert (pair["overlap_start_s"], pair["overlap_end_s"]) == (0.0, 8.0)
    assert pair["statuses"] == {"nav_a": "cancelled", "nav_b": "succeeded"}


def test_runner_default_serial_mode_is_unchanged_and_evidence_free(tmp_path: Path):
    """Regression: omitting ``execution_mode`` and passing it explicitly as
    ``"serial"`` produce byte-identical archives, and the default episode
    writes no async markers or concurrency evidence at all."""
    public, api, state = _component_fixture()
    plan = {"schema": "eai.benchmark.goal_plan", "version": GOAL_PLAN_VERSION,
            "plan_id": "serial-regression-plan", "assigned_team": [GROUND_1],
            "tasks": [_nav_task("nav_a", GROUND_1, "rack_aisle_a",
                                "consolidation_to_rack_a", 0.0, 10.0)]}

    def _run(root: Path, **kwargs):
        recorder = ConcurrencyProbeRecorder(episode_id="serial-regression-1")
        backend = ConcurrentFakeBackend(
            public, episode_id="serial-regression-1", recorder=recorder,
            runtime_state=state, serial=True)
        result = FormalGoalEpisodeRunner(
            public, api, _AsyncBatchAdapter(plan, ["nav_a"]), backend,
            output_root=root, episode_id="serial-regression-1",
            **kwargs).run(initial_state=state)
        assert result["error"] is None
        assert result["skill_receipt_count"] == 1
        return result

    default_root = tmp_path / "default"
    explicit_root = tmp_path / "explicit-serial"
    first = _run(default_root)
    second = _run(explicit_root, execution_mode="serial")
    assert {key: value for key, value in first.items() if key != "output_root"} == {
        key: value for key, value in second.items() if key != "output_root"}
    assert first["output_root"] == str(default_root)
    assert "execution_mode" not in first

    default_files = sorted(path.name for path in default_root.iterdir())
    explicit_files = sorted(path.name for path in explicit_root.iterdir())
    assert default_files == explicit_files
    assert "concurrency_evidence.json" not in default_files
    for name in default_files:
        default_data = json.loads((default_root / name).read_text(encoding="utf-8"))
        explicit_data = json.loads((explicit_root / name).read_text(encoding="utf-8"))
        if name == "result.json":
            # Only the episode directory itself differs between the two runs.
            default_data.pop("output_root")
            explicit_data.pop("output_root")
        if name == "scoring_adapter_metadata.json":
            # The raw archive index hashes result.json's original bytes, which
            # embed that episode directory name.
            for row in (default_data, explicit_data):
                row["archive_index"]["files"].pop("result.json")
        assert default_data == explicit_data, name
    manifest = json.loads((default_root / "episode_manifest.json").read_text())
    assert "execution_mode" not in manifest
    assert "concurrency_evidence" not in manifest
    result_json = json.loads((default_root / "result.json").read_text())
    assert "execution_mode" not in result_json
