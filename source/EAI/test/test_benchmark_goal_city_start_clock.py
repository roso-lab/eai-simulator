"""Strict native start-clock and idle-advance tests for City Goal execution."""

from copy import deepcopy

import pytest

from demo.benchmark.goal_city_episode import (
    CityNativeGoalBackend,
    build_city_episode_plan,
)
from demo.benchmark.goal_driven_tasks import build_goal_task_package
from source.EAI.test.test_benchmark_goal_city_episode import (
    DT,
    _make_ctx,
    _trusted_report_for,
)


def _public_task():
    return build_goal_task_package(
        "city", city_roster_profile="bound_native_v1")["public_task"]


def _twenty_second_guide(public_task):
    task = deepcopy(next(
        row for row in build_city_episode_plan(public_task)["tasks"]
        if row["skill"] == "guide_people"))
    task["execution"] = {"start_s": 0.0, "duration_s": 20.0}
    return task


def _dispatch_clock(backend):
    return next(row for row in backend.evidence if row.get("kind") == "dispatch_clock")


@pytest.mark.parametrize("invalid", [None, 0, 1, "true"])
def test_strict_dispatch_clock_requires_a_bool(tmp_path, invalid):
    ctx, _runner, _steps, _roles = _make_ctx(tmp_path, start_s=10.0)
    with pytest.raises(ValueError, match="strict_dispatch_clock"):
        CityNativeGoalBackend(ctx, strict_dispatch_clock=invalid)


def test_host_dispatch_jump_advances_native_before_service_start(tmp_path):
    public = _public_task()
    task = _twenty_second_guide(public)
    ctx, _runner, step_calls, _roles = _make_ctx(tmp_path, start_s=10.0)
    backend = CityNativeGoalBackend(
        ctx, public_task=public, clock_epoch_s=10.0,
        strict_dispatch_clock=True)

    trusted = _trusted_report_for(
        task, episode_id="city-start-host-jump", current_time_s=15.0)
    trusted["session"]["tasks"][task["task_id"]]["dispatched_at_s"] = 2.0
    result = backend.execute(
        task, episode_id="city-start-host-jump", trusted_report=trusted)

    assert result["status"] == "succeeded"
    assert result["completed_at_s"] == 20.0
    guide = next(row for row in backend.evidence if row.get("kind") == "guide")
    assert guide["request"]["submitted_time_s"] >= 25.0
    assert sum(step_calls) * DT == pytest.approx(20.0, abs=DT)
    clock = _dispatch_clock(backend)
    assert clock["episode_id"] == "city-start-host-jump"
    assert clock["action_id"] == "dispatch-0"
    assert clock["clock_scope"] == "backend_entry"
    assert clock["clock_epoch_s"] == 10.0
    assert clock["planned_start_s"] == 0.0
    assert clock["planned_end_s"] == 20.0
    assert clock["dispatched_at_s"] == 2.0
    assert clock["ledger_dispatch_s"] == 2.0
    assert clock["backend_entry_host_s"] == 15.0
    assert clock["native_actual_start_s"] == pytest.approx(25.0, abs=DT)
    assert clock["actual_start_s"] == pytest.approx(15.0, abs=DT)
    assert clock["native_actual_end_s"] == pytest.approx(30.0, abs=DT)
    assert clock["actual_end_s"] == pytest.approx(20.0, abs=DT)
    assert clock["service_request_id"] == guide["request"]["request_id"]
    assert clock["service_request_native_s"] == pytest.approx(25.0, abs=DT)
    assert clock["service_request_time_s"] == pytest.approx(15.0, abs=DT)
    assert clock["status"] == "succeeded"


def test_dispatch_at_window_end_fails_without_native_side_effects(tmp_path):
    public = _public_task()
    task = _twenty_second_guide(public)
    ctx, runner, step_calls, _roles = _make_ctx(tmp_path, start_s=10.0)
    backend = CityNativeGoalBackend(
        ctx, public_task=public, clock_epoch_s=10.0,
        strict_dispatch_clock=True)

    result = backend.execute(
        task,
        episode_id="city-start-expired",
        trusted_report=_trusted_report_for(
            task, episode_id="city-start-expired", current_time_s=20.0),
    )

    assert result["status"] == "failed"
    assert result["completed_at_s"] == 20.0
    assert result["reason"] == "native city service window expired before execution"
    assert runner.calls == []
    assert step_calls == []
    assert ctx["city"].people.resumed == []
    assert ctx["trace"].get("member_dispatch") is None
    clock = _dispatch_clock(backend)
    assert clock["disposition"] == "missed_window"
    assert clock["native_actual_start_s"] is None
    assert clock["actual_start_s"] is None
    assert clock["native_actual_end_s"] is None
    assert clock["actual_end_s"] is None


def test_dispatch_tick_crossing_window_end_fails_before_service(tmp_path):
    public = _public_task()
    task = _twenty_second_guide(public)
    ctx, runner, step_calls, _roles = _make_ctx(tmp_path, start_s=29.98)
    backend = CityNativeGoalBackend(
        ctx, public_task=public, clock_epoch_s=10.0,
        strict_dispatch_clock=True)

    result = backend.execute(
        task,
        episode_id="city-start-quantized-expiry",
        trusted_report=_trusted_report_for(
            task, episode_id="city-start-quantized-expiry",
            current_time_s=19.99),
    )

    assert result["status"] == "failed"
    assert result["completed_at_s"] > 20.0
    assert runner.calls == []
    assert step_calls == [1]
    assert _dispatch_clock(backend)["disposition"] == "missed_window"


def test_guide_retry_crossing_window_does_not_submit_another_request(tmp_path):
    public = _public_task()
    task = _twenty_second_guide(public)
    task["execution"]["duration_s"] = 0.5
    ctx, runner, step_calls, _roles = _make_ctx(
        tmp_path, start_s=10.0, guide_not_admitted_first=3)
    backend = CityNativeGoalBackend(
        ctx, public_task=public, clock_epoch_s=10.0,
        strict_dispatch_clock=True)

    result = backend.execute(
        task,
        episode_id="city-start-retry-expired",
        trusted_report=_trusted_report_for(
            task, episode_id="city-start-retry-expired", current_time_s=0.0),
    )

    assert result["status"] == "failed"
    assert result["reason"] == "native city guide retry window expired"
    assert result["completed_at_s"] == pytest.approx(1.0)
    assert [row[0] for row in runner.calls].count("guide_people") == 1
    assert [row[0] for row in runner.calls].count("record_role") == 1
    assert step_calls == [int(1.0 / DT)]
    assert not any(row.get("kind") == "guide" for row in backend.evidence)
    clock = _dispatch_clock(backend)
    assert clock["status"] == "failed"
    assert clock["actual_end_s"] == pytest.approx(1.0)
    assert clock["service_request_id"] is None
    assert clock["service_request_native_s"] is None
    assert clock["service_request_time_s"] is None


def test_normal_nonzero_epoch_archives_real_start_and_end(tmp_path):
    public = _public_task()
    task = _twenty_second_guide(public)
    ctx, _runner, _steps, _roles = _make_ctx(tmp_path, start_s=10.0)
    backend = CityNativeGoalBackend(
        ctx, public_task=public, clock_epoch_s=10.0,
        strict_dispatch_clock=True)

    result = backend.execute(
        task,
        episode_id="city-start-normal",
        trusted_report=_trusted_report_for(
            task, episode_id="city-start-normal", current_time_s=0.0),
    )

    assert result["status"] == "succeeded"
    assert result["receipt"]["completed_at_s"] == 20.0
    clock = _dispatch_clock(backend)
    assert clock["native_actual_start_s"] == 10.0
    assert clock["actual_start_s"] == 0.0
    assert clock["native_actual_end_s"] == pytest.approx(30.0, abs=DT)
    assert clock["actual_end_s"] == pytest.approx(20.0, abs=DT)


def test_idle_advance_uses_native_clock_without_service_calls(tmp_path):
    public = _public_task()
    ctx, runner, step_calls, _roles = _make_ctx(tmp_path, start_s=10.0)
    backend = CityNativeGoalBackend(
        ctx, public_task=public, clock_epoch_s=10.0,
        strict_dispatch_clock=True)

    result = backend.advance_idle(0.2)

    assert result["time_s"] == pytest.approx(0.2, abs=1e-9)
    assert float(ctx["city"].elapsed) == pytest.approx(10.2, abs=1e-9)
    assert result["runtime_state"] == {
        "robot_regions": backend._runtime_state()["robot_regions"],
        "object_states": {},
    }
    assert sum(step_calls) == 4
    assert runner.calls == []
    assert ctx["trace"].get("member_dispatch") is None
    idle = backend.evidence[-1]
    assert idle["kind"] == "idle_clock"
    assert idle["requested_time_s"] == 0.2
    assert idle["native_start_s"] == 10.0
    assert idle["native_end_s"] == pytest.approx(10.2, abs=1e-9)


def test_idle_advance_floors_at_budget_and_fails_when_no_step_reachable(tmp_path):
    public = deepcopy(_public_task())
    public["budgets"]["simulation_s"] = 0.22
    ctx, runner, step_calls, _roles = _make_ctx(tmp_path, start_s=10.1)
    backend = CityNativeGoalBackend(
        ctx, public_task=public, clock_epoch_s=10.0,
        strict_dispatch_clock=True)

    first = backend.advance_idle(0.22)
    assert first["time_s"] == pytest.approx(0.2, abs=1e-9)
    assert step_calls == [2]
    assert runner.calls == []
    with pytest.raises(RuntimeError, match="no reachable physics step"):
        backend.advance_idle(0.22)
    assert step_calls == [2]


def test_current_observation_reuses_only_same_native_tick(tmp_path):
    ctx, runner, _steps, _roles = _make_ctx(tmp_path, start_s=10.0)
    backend = CityNativeGoalBackend(ctx)
    ctx["city"].elapsed = 11.0
    runner._observation_ref = "intake-observation"
    runner.host.observation = lambda ref: {
        "id": ref, "captured_at_s": 11.0, "delivered_at_s": 11.0}
    runner.observe = lambda: pytest.fail("same-tick observation must be reused")

    assert backend._current_observation() == "intake-observation"
    assert backend._current_observation() == "intake-observation"

    ctx["city"].elapsed = 12.0
    runner.host.observation = lambda ref: {
        "id": ref, "captured_at_s": 11.0, "delivered_at_s": 11.0}
    runner.observe = lambda: "renewed-observation"
    assert backend._current_observation() == "renewed-observation"
