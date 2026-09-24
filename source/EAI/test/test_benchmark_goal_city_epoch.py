"""Clock-epoch regression tests for the native City goal backend."""

from copy import deepcopy
import math

import pytest

from demo.benchmark import goal_city_episode as city_episode
from demo.benchmark.goal_city_episode import (
    CityNativeGoalBackend,
    build_acceptance_evidence,
    build_city_episode_plan,
    city_business_evaluator,
)
from demo.benchmark.goal_driven_tasks import build_goal_task_package
from demo.city_benchmark.task_logic import (
    default_deadline_policy,
    evaluate_city_acceptance,
)
from source.EAI.test.test_benchmark_goal_city_episode import (
    DT,
    ROSTER_IDS,
    VEHICLE_IDS,
    _make_ctx,
    _trusted_report_for,
)


def _twenty_second_guide():
    public = build_goal_task_package(
        "city", city_roster_profile="bound_native_v1")["public_task"]
    task = deepcopy(next(
        row for row in build_city_episode_plan(public)["tasks"]
        if row["skill"] == "guide_people"))
    task["execution"] = {"start_s": 0.0, "duration_s": 20.0}
    return task


@pytest.mark.parametrize("invalid", [True, -1.0, math.inf, math.nan, "0"])
def test_clock_epoch_must_be_finite_and_nonnegative(tmp_path, invalid):
    ctx, _runner, _steps, _roles = _make_ctx(tmp_path, start_s=10.0)
    with pytest.raises(ValueError, match="clock_epoch_s"):
        CityNativeGoalBackend(ctx, clock_epoch_s=invalid)
    with pytest.raises(ValueError, match="clock_epoch_s"):
        build_acceptance_evidence([], ctx, clock_epoch_s=invalid)
    with pytest.raises(ValueError, match="clock_epoch_s"):
        city_business_evaluator([], ctx, clock_epoch_s=invalid)


def test_nonzero_epoch_runs_relative_window_and_returns_relative_receipt(tmp_path):
    ctx, _runner, step_calls, _roles = _make_ctx(tmp_path, start_s=10.0)
    task = _twenty_second_guide()
    backend = CityNativeGoalBackend(ctx, clock_epoch_s=10.0)

    result = backend.execute(
        task,
        episode_id="city-epoch-success",
        trusted_report=_trusted_report_for(
            task, episode_id="city-epoch-success", current_time_s=0.0),
    )

    assert backend.clock_epoch_s == 10.0
    assert result["status"] == "succeeded"
    assert result["completed_at_s"] == 20.0
    assert result["receipt"]["completed_at_s"] == 20.0
    assert float(ctx["city"].elapsed) - 10.0 == pytest.approx(20.0, abs=DT)
    assert sum(step_calls) * DT == pytest.approx(20.0, abs=DT)
    # Native service evidence and role traces retain the absolute clock.
    assert backend.evidence[0]["delivered_at_s"] == 10.0
    assert backend.evidence[-1]["request"]["submitted_time_s"] == 10.0
    assert ctx["trace"]["member_dispatch"][0]["recorded_at_s"] == 10.0


def test_native_deadline_failure_returns_nonnegative_relative_time(tmp_path):
    ctx, _runner, step_calls, _roles = _make_ctx(tmp_path, start_s=32.0)
    task = _twenty_second_guide()
    backend = CityNativeGoalBackend(ctx, clock_epoch_s=10.0)

    result = backend.execute(
        task,
        episode_id="city-epoch-late",
        trusted_report=_trusted_report_for(
            task, episode_id="city-epoch-late", current_time_s=0.0),
    )

    assert result["status"] == "failed"
    assert result["completed_at_s"] == pytest.approx(22.0)
    assert result["receipt"]["completed_at_s"] == pytest.approx(22.0)
    assert result["completed_at_s"] >= 0.0
    assert step_calls == []
    assert backend.evidence[-1]["request"]["submitted_time_s"] == 32.0


def test_acceptance_timestamps_are_relative_without_mutating_native_evidence(
        tmp_path, monkeypatch):
    ctx, _runner, _steps, _roles = _make_ctx(tmp_path, start_s=10.0)
    person_id = ROSTER_IDS[0]
    vehicle_id = VEHICLE_IDS[0]
    log = [
        {"kind": "episode_observation", "observation_ref": "obs-absolute",
         "delivered_at_s": 10.0},
        {"kind": "guide", "person_ids": [person_id],
         "assembly_region_id": "assembly_0", "observation_ref": "obs-absolute",
         "request": {"request_id": "guide-1", "agent_id": "go2_1",
                     "submitted_time_s": 12.0},
         "contact": {"status": "contact_confirmed", "ack_time_s": 14.0,
                     "actor_acks": [{"id": person_id, "ack_time_s": 14.0}]}},
        {"kind": "broadcast", "person_ids": [person_id],
         "observation_ref": "obs-absolute",
         "actor_acks": [{"id": person_id, "ack_time_s": 13.0}]},
        {"kind": "arrival", "person_id": person_id, "arrived": True,
         "arrival_time_s": 20.0, "safe_route_and_assembly": True,
         "review": {"evidence": {"actors": [
             {"id": person_id, "entered_hazard": False}]}}},
        {"kind": "control", "request": {"submitted_time_s": 30.0},
         "readbacks": {bound_vehicle_id: {
             "measured_trajectory": True, "no_hazard_ingress": True,
             "trajectory_check_time_s": 35.1}
             for bound_vehicle_id in VEHICLE_IDS},
         "review": {"status": "evidence_complete", "evidence": {
             "actors": [{"id": bound_vehicle_id, "ack_time_s": 30.0}
                        for bound_vehicle_id in VEHICLE_IDS]}}},
        {"kind": "report", "observation_ref": "obs-report",
         "request": {"submitted_time_s": 40.0},
         "receipt": {"received_time_s": 41.0}, "receipt_time_s": 41.0,
         "access": {"measured_clear_throughout": True,
                    "rescue_and_evacuation_routes_admitted": True},
         "access_end_time_s": 42.0},
    ]
    original = deepcopy(log)

    envelope = build_acceptance_evidence(log, ctx, clock_epoch_s=10.0)

    assert log == original
    assert envelope["incident_assessed"]["observation_delivery_time_s"] == 0.0
    assert envelope["incident_assessed"]["decision_time_s"] == 2.0
    assert envelope["responder_dispatch"]["dispatch_time_s"] == 2.0
    assert envelope["responder_dispatch"]["arrival_time_s"] == 4.0
    person = next(row for row in envelope["people"] if row["person_id"] == person_id)
    assert person["guidance_request_time_s"] == 2.0
    assert person["guidance_time_s"] == 3.0
    assert person["arrival_time_s"] == 10.0
    vehicle = next(row for row in envelope["traffic"] if row["vehicle_id"] == vehicle_id)
    assert vehicle["request_time_s"] == 20.0
    assert vehicle["ack_time_s"] == 20.0
    assert vehicle["trajectory_check_time_s"] == pytest.approx(25.1)
    assert envelope["incident_report"]["submit_time_s"] == 30.0
    assert envelope["incident_report"]["receipt_time_s"] == 31.0
    assert envelope["response_access"]["check_start_time_s"] == 31.0
    assert envelope["response_access"]["check_end_time_s"] == 32.0
    review = evaluate_city_acceptance(
        envelope,
        default_deadline_policy(),
        expected_affected_person_ids=set(ROSTER_IDS),
        expected_following_vehicle_ids=set(VEHICLE_IDS),
    )
    assert review["outcomes"]["incident_assessed"]["success"] is True
    assert review["outcomes"]["response_access_preserved"]["success"] is True
    assert build_acceptance_evidence(log, ctx) == build_acceptance_evidence(
        log, ctx, clock_epoch_s=0.0)

    captured = {}

    def fake_acceptance(value, *_args, **_kwargs):
        captured.update(deepcopy(value))
        return {"outcomes": {"incident_assessed": {"success": True}}}

    monkeypatch.setattr(city_episode, "evaluate_city_acceptance", fake_acceptance)
    evaluator_log = [deepcopy(log[0])]
    outcome = city_business_evaluator(
        evaluator_log, ctx, clock_epoch_s=10.0)(
            "city-epoch-evaluator",
            {"session": {"business_outcomes": [{"id": "incident_assessed"}]}},
        )
    assert captured["incident_assessed"]["observation_delivery_time_s"] == 0.0
    assert outcome["incident_assessed"]["status"] == "validated"
