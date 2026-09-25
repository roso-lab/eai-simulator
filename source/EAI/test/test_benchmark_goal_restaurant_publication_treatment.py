"""Real result publication joins, with fake physics kept explicitly diagnostic."""

from copy import deepcopy

import pytest

from demo.benchmark.communication import CommunicationRuntime
from demo.benchmark.goal_driven_tasks import build_goal_task_package
from demo.benchmark.goal_restaurant_deployment_v2 import build_restaurant_deployment_v2
from demo.benchmark.goal_restaurant_execution_v2 import RestaurantExecutionPump, ResultPublicationError
from demo.benchmark.goal_restaurant_publication_treatment import (
    RestaurantPublicationTreatment, verify_restaurant_publication_treatment)
from test_benchmark_goal_restaurant_execution_v2 import (
    _context, _parallel_plan, _runtime, _task, FakeObservationProvider, Participant)


def _cross_plan(contract):
    plan = _parallel_plan(contract)
    aerial = "restaurant_iris_1"
    spawn = contract["deployment"]["spawn_regions"][aerial]
    route = next(row for row in contract["public_task"]["public_world"]["routes"]
                 if row["from_region_id"] == spawn and "aerial_navigation" in row["access_modes"])
    plan["assigned_team"].append(aerial)
    plan["tasks"].append(_task(
        "participant-chosen-air-task", "aerial_navigation", aerial,
        {"region_id": route["to_region_id"], "route_ids": [route["id"]]},
        ["obs-k", "obs-d"], start=12., duration=contract["public_task"][
            "execution_contract"]["skill_durations_s"]["aerial_navigation"]))
    return plan


def _setup(tmp_path, cross=True, capacity=None):
    contract = build_restaurant_deployment_v2()
    plan = _cross_plan(contract) if cross else _parallel_plan(contract)
    participant = Participant(plan)
    context, fleet, _ = _context(tmp_path, participant)
    runtime = _runtime()
    if capacity is not None:
        communication = runtime.report()["initial_contract"]
        communication["pools"]["backhaul_uplink"]["capacity_bps"] = capacity
        runtime = CommunicationRuntime(communication)
    hidden = build_goal_task_package("restaurant")["hidden_evaluator"]
    hook = RestaurantPublicationTreatment(
        public_task=contract["public_task"], hidden_evaluator=hidden,
        communication_contract=runtime.report()["initial_contract"],
        deployment_companion=contract["deployment"])
    pump = RestaurantExecutionPump(context, runtime=runtime,
        observation_provider=FakeObservationProvider(fleet), publication_treatment=hook)
    inputs = dict(public_task=contract["public_task"], hidden_evaluator=hidden,
        participant_api=contract["participant_api"],
        communication_contract=runtime.report()["initial_contract"],
        deployment_companion=contract["deployment"])
    return pump, participant, inputs


def test_actual_observation_uplink_one_shot_and_private_binding(tmp_path):
    pump, participant, inputs = _setup(tmp_path)
    report = pump.run(max_ticks=160)
    review = verify_restaurant_publication_treatment(pump_report=report, **inputs)
    assert review["validated"] and review["application_count"] == 1
    assert review["binding_status"] == "bound"
    assert review["trigger_status"] == "triggered"
    assert review["application_status"] == "applied"
    assert review["verified_effect"] is False
    assert review["scoreable"] is False
    decisions = report["publication_treatment"]["decisions"]
    assert [d["reason"] for d in decisions] == ["selected", "first_trigger_already_selected"]
    first = decisions[0]
    assert first["prospective_consumers"] == [
        {"task_id": "participant-chosen-air-task", "agent_id": "restaurant_iris_1"}]
    message = report["communication"]["messages"][first["message_id"]]
    assert message["channel"] == "goal_execution_result"
    assert list(message["recipients"]) == ["trusted_execution_host"]
    assert message["payload"]["estimate"]["participant_observation"] is not None
    assert "publication_treatment" not in message["payload"]
    assert "binding_sha256" not in str(participant.execution_profile)
    assert len(report["physical_records"]) == 6
    assert all(row["status"] == "succeeded" for row in report["physical_records"])
    assert report["communication"]["version"] == 4


def test_ground_only_plan_preserves_default_transport_and_is_not_forced_to_trigger(tmp_path):
    pump, _, inputs = _setup(tmp_path, cross=False)
    report = pump.run(max_ticks=160)
    review = verify_restaurant_publication_treatment(pump_report=report, **inputs)
    assert review["trigger_status"] == "not_triggered"
    assert review["application_status"] == "not_applied"
    assert report["communication"]["version"] == 2
    assert all(d["reason"] == "no_prospective_cross_mobility_consumer"
               for d in report["publication_treatment"]["decisions"])


def test_partial_serialization_does_not_consume_or_complete_observation(tmp_path):
    pump, participant, inputs = _setup(tmp_path, capacity=1000.)
    for _ in range(83):
        pump.step_once()
    report = pump.report()
    decision = report["publication_treatment"]["decisions"][0]
    message = report["communication"]["messages"][decision["message_id"]]
    assert message["recipients"]["trusted_execution_host"]["status"] == "serializing"
    assert decision["message_id"] not in participant.consumed
    assert report["context"]["session_audit"]["final_report"]["tasks"]["obs-k"]["status"] == "pending"
    assert next(row for row in report["physical_records"]
                if row["task_id"] == "obs-k")["status"] == "succeeded"
    review = verify_restaurant_publication_treatment(pump_report=report, **inputs)
    assert review["application_status"] == "applied" and review["verified_effect"] is False


@pytest.mark.parametrize("mutation", ["contract", "binding", "prefix", "coverage", "source"])
def test_review_rejects_changed_authorities_or_causal_evidence(tmp_path, mutation):
    pump, _, inputs = _setup(tmp_path)
    report = pump.run(max_ticks=160)
    if mutation == "contract":
        inputs["communication_contract"]["pools"]["backhaul_uplink"]["fixed_delay_s"] = 42.
    elif mutation == "binding":
        report["publication_treatment"]["conditions"]["binding_sha256"] = "0" * 64
    elif mutation == "prefix":
        report["publication_treatment"]["decisions"][0]["audit_prefix"] = deepcopy(
            report["context"]["session_audit"])
    elif mutation == "coverage":
        report["publication_treatment"]["decisions"].pop()
    else:
        report["physical_records"][2]["status"] = "failed"
    with pytest.raises((ValueError, KeyError)):
        verify_restaurant_publication_treatment(pump_report=report, **inputs)


def test_failed_publish_does_not_consume_hook(tmp_path, monkeypatch):
    pump, _, _ = _setup(tmp_path)
    publish = pump.runtime.publish
    def fail_observation(**kwargs):
        if kwargs.get("publication_treatment") is not None:
            raise ValueError("synthetic pre-publication rejection")
        return publish(**kwargs)
    monkeypatch.setattr(pump.runtime, "publish", fail_observation)
    with pytest.raises(ResultPublicationError, match="pre-publication rejection"):
        pump.run(max_ticks=160)
    report = pump.report()
    assert report["platform_gaps"][-1]["reason"] == "result_publication_failure"
    assert report["platform_gaps"][-1]["participant_failure_inferred"] is False
    assert report["physical_records"][-1]["status"] == "succeeded"
    assert all(row["status"] == "succeeded" for row in report["physical_records"])
    assert pump._publication_treatment.report()["selected_count"] == 0
    assert pump._publication_treatment.report()["decisions"] == []


def test_selection_uses_current_plan_and_stops_at_service_commit(tmp_path):
    pump, _, inputs = _setup(tmp_path)
    report = pump.run(max_ticks=160)
    decision = report["publication_treatment"]["decisions"][0]
    source = next(row for row in report["physical_records"]
                  if row["evidence_ref"] == decision["source_record_ref"])
    def select(journal, receipt=None):
        hook = RestaurantPublicationTreatment(**{
            k: v for k, v in inputs.items() if k != "participant_api"})
        result = hook.select(
            message_id=decision["message_id"], producer_id=decision["producer_id"],
            task_id=decision["task_id"], published_at_s=decision["published_at_s"],
            physical_record=source, audit_journal=journal, readiness_receipt=receipt)
        return result, hook.report()
    journal = decision["audit_prefix"]
    result, archive = select(journal, {
        "episode_id": decision["episode_id"], "time_s": decision["published_at_s"]})
    assert result is None
    assert archive["decisions"][0]["reason"] == "business_commit_already_exists"
    for mutation in ("stale", "invalidated", "old_plan", "missing_mobility"):
        changed = deepcopy(journal)
        state = changed["final_report"]
        if mutation == "stale":
            state["current_time_s"] -= .1
        elif mutation == "invalidated":
            state["tasks"][decision["task_id"]]["invalidated_by"] = ["later_replan"]
        elif mutation == "old_plan":
            state["tasks"][decision["task_id"]]["plan_version"] -= 1
        else:
            state["plans"][-1]["plan"]["tasks"][-1]["assigned_agents"] = ["unknown_robot"]
        with pytest.raises(ValueError):
            select(changed)


@pytest.mark.parametrize("level", ["immediate", "delayed", "dropped", "bandwidth_limited", "stale"])
def test_all_frozen_factor_levels_reach_the_same_real_transport(level):
    from demo.benchmark.communication import validate_communication_report
    from demo.benchmark.goal_driven_contract import GOAL_PLAN_VERSION
    from demo.benchmark.goal_session_audit import AuditedGoalSession, replay_goal_session_audit
    from demo.benchmark.provenance import canonical_sha256

    package = build_goal_task_package("restaurant", factor_id="communication_quality", factor_level=level)
    public, api = package["public_task"], package["participant_api"]
    producer, consumer = "restaurant_carter_1", "restaurant_iris_1"
    session = AuditedGoalSession(public, api, episode_id="factor-fixture")
    action = Participant._action
    session.submit_action(action("select", "select_team", {"agent_ids": [producer, consumer]}, 0.))
    durations = public["execution_contract"]["skill_durations_s"]
    observe = _task("my-observation", "observe", producer, {
        "region_id": "restaurant_staging", "subject_ref": "restaurant_staging"},
        duration=durations["observe"])
    downstream = _task("my-consumer", "observe", consumer, {
        "region_id": "restaurant_overview", "subject_ref": "restaurant_overview"},
        ["my-observation"], start=durations["observe"], duration=durations["observe"])
    plan = {"schema": "eai.benchmark.goal_plan", "version": GOAL_PLAN_VERSION,
            "plan_id": "self-authored-factor-check", "assigned_team": [producer, consumer],
            "tasks": [observe, downstream]}
    session.submit_action(action("plan", "submit_plan", {"plan": plan}, 0.))
    session.submit_action(action("dispatch", "dispatch_skill", {"task_id": "my-observation", "evidence_refs": []}, 0.))
    now = float(durations["observe"])
    session.emit("tick", now, {"state_digest": "factor-fixture-state"})
    journal = session.audit_journal()
    assert replay_goal_session_audit(journal, public, api)["validated"]
    source = {"episode_id": "factor-fixture", "task_id": "my-observation",
              "physical_terminal_at_s": now, "status": "succeeded",
              "disposition": "native_ground_readiness_observation", "task": observe,
              "dispatch_action_id": "dispatch"}
    source["evidence_ref"] = "sha256:" + canonical_sha256(source)
    runtime = _runtime()
    hook = RestaurantPublicationTreatment(public_task=public,
        hidden_evaluator=package["hidden_evaluator"],
        communication_contract=runtime.report()["initial_contract"])
    selected = hook.select(message_id="result", producer_id=producer,
        task_id="my-observation", published_at_s=now, physical_record=source,
        audit_journal=journal, readiness_receipt=None)
    frozen = package["hidden_evaluator"]["hidden_event_schedule"]["communication_events"][0]
    assert selected is not None
    assert all(selected[k] == frozen[k] for k in (
        "operation", "delay_s", "drop", "bytes_per_window", "window_s", "publication_validity_s"))
    runtime.publish(message_id="result", producer_id=producer, recipients=["trusted_execution_host"],
        channel="goal_execution_result", estimate={"source_record_ref": source["evidence_ref"],
        "data": "x" * 2000}, published_at_s=now, valid_until_s=now + 30.,
        pool="backhaul_uplink", direction="uplink", publication_treatment=selected)
    runtime.advance(now + min(29., selected["publication_validity_s"]))
    report = runtime.report()
    assert report["version"] == 4
    assert report["messages"]["result"]["publication_treatment"] == selected
    assert validate_communication_report(report)["validated"]
