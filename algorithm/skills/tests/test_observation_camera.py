"""On-demand camera skill tests: contract, metering, lifecycle, leak fixes.

Skill-layer adaptation of the 2026-09-23 companion suite (926 lines at
``source/EAI/test/test_benchmark_goal_camera_tools.py``).  Direct tool tests
keep their original names and assertions; host-integration tests live in
``benchmark/tests/test_observation_host_episode.py`` because they exercise the
episode driver.  Old host piece → new location:

| ``demo.benchmark.communication``             | ``algorithm.skills.observation.communication`` |
| ``demo.benchmark.goal_camera_ondemand``      | ``algorithm.skills.observation.ondemand`` |
| ``demo.benchmark.goal_camera_telemetry``     | ``algorithm.skills.observation.telemetry`` |
| ``demo.benchmark.goal_camera_tools``         | ``algorithm.skills.observation.tools`` |
| ``build_goal_task_package("warehouse_nvd")`` | minimal in-file public task fixture |
| ``EAI.interface_catalog`` declaration        | ``SKILL_REGISTRY`` entry for ``eai.observation.camera`` |
"""

from copy import deepcopy
import json

import pytest

from algorithm.skills.observation import (
    BuilderCameraBackend,
    BuilderCameraBinding,
    CommunicationRuntime,
    FakeOnDemandCameraBackend,
    GoalCameraTelemetry,
    GoalCameraTools,
    OnDemandRenderLifecycle,
    validate_communication_report,
)
from algorithm.skills.observation.tools import CAMERA_TOOL_SCHEMA
from algorithm.skills.registry import SKILL_REGISTRY


ROBOT = "warehouse_ground_1"
SENSOR = "camera_rgb_primary"

PUBLIC_TASK = {
    "task_id": "warehouse_nvd_observation_probe",
    "mission": {"scene_key": "warehouse_nvd"},
    "candidate_robot_pool": [
        {"id": ROBOT},
        {"id": "warehouse_ground_2"},
    ],
    "budgets": {"simulation_s": 240.0},
}


@pytest.fixture
def bundle():
    return {"public_task": deepcopy(PUBLIC_TASK)}


def sensor_catalog(robot=ROBOT, sensor=SENSOR, **overrides):
    profile = {
        "member": "left",
        "modalities": ["rgb"],
        "encodings": ["png"],
        "max_width": 128,
        "max_height": 96,
        "max_bytes": 50_000,
    }
    profile.update(overrides)
    return {robot: {sensor: profile}}


def request_call(*, request_id="cam-1", now=0.0, robot=ROBOT, sensor=SENSOR,
                 width=32, height=24, max_bytes=10_000, deadline_s=5.0,
                 encoding="png", extra_args=None, quality_extra=None):
    quality = {"encoding": encoding, "max_bytes": max_bytes}
    if quality_extra:
        quality.update(quality_extra)
    arguments = {
        "robot_id": robot,
        "sensor_id": sensor,
        "modalities": ["rgb"],
        "deadline_s": deadline_s,
        "resolution": {"width": width, "height": height},
        "quality": quality,
        "freshness": "capture_at_or_after_submit",
    }
    if extra_args:
        arguments.update(extra_args)
    return {
        "schema": CAMERA_TOOL_SCHEMA,
        "version": 1,
        "request_id": request_id,
        "submitted_at_s": now,
        "tool": "request_observation",
        "arguments": arguments,
    }


def cancel_call(target, *, request_id="cam-cancel", now=0.0):
    return {
        "schema": CAMERA_TOOL_SCHEMA,
        "version": 1,
        "request_id": request_id,
        "submitted_at_s": now,
        "tool": "cancel_request",
        "arguments": {"request_id": target},
    }


def setup_tools(bundle, *, contract=None, episode_id="cam-test",
                catalog=None, backend=None, validity_s=5.0, **tool_kwargs):
    backend = backend or FakeOnDemandCameraBackend(warmup_frames=0, idle_timeout_s=1.0)
    telemetry = GoalCameraTelemetry(CommunicationRuntime(contract), message_prefix=episode_id)
    tools = GoalCameraTools(
        bundle["public_task"],
        episode_id=episode_id,
        sensor_catalog=catalog or sensor_catalog(),
        backend=backend,
        telemetry=telemetry,
        validity_s=validity_s,
        **tool_kwargs,
    )
    return tools, backend


def bind_join_event(tools, event, *, index=0):
    class Session:
        def report(self):
            events = [{} for _ in range(index)] + [deepcopy(event)]
            return {"episode_id": tools.episode_id, "events": events}
    tools.bind_goal_session(Session())


def link_bytes(report):
    total = 0
    for message in report["communication"]["messages"].values():
        if message["channel"] != "camera":
            continue
        for delivery in message["recipients"].values():
            total += delivery.get("bytes_sent", 0)
    return total


# ----------------------------------------------------------------------
# Direct tool-level tests (import swap only, original names kept).
# ----------------------------------------------------------------------
def test_no_opt_in_zero_requests_zero_frames_zero_link_bytes(bundle):
    tools, backend = setup_tools(bundle)
    assert backend.captures == []
    report = tools.report()
    assert report["request_count"] == 0
    assert report["communication"]["messages"] == {}
    assert link_bytes(report) == 0


def test_single_request_one_frame_with_hash_clock_pose_calibration(bundle):
    tools, backend = setup_tools(bundle)
    result = tools.submit(request_call(), time_s=0)
    assert result["accepted"] is True
    events = tools.tick(0)
    frames = [e for e in events if e.get("kind") == "camera_frame"]
    assert len(frames) == 1
    data = frames[0]["data"]
    assert data["request_id"] == "cam-1"
    assert data["cache_hit"] is False
    assert data["byte_length"] > 0
    assert "payload_ref" not in data
    assert data["pose"]["source"] == "synthetic"
    assert link_bytes(tools.report()) > 0
    assert validate_communication_report(tools.report()["communication"])["validated"]
    assert len(backend.captures) == 1


def test_p1_late_frame_after_timeout_is_suppressed_but_bytes_kept(bundle):
    """deadline < link delay < TTL: timeout first, late camera_frame never exposed."""
    tools, backend = setup_tools(
        bundle, contract={"delay_s": 5}, validity_s=20.0, episode_id="late-frame")
    tools.submit(request_call(deadline_s=2), time_s=0)
    first = tools.tick(0)
    assert not any(row.get("kind") == "camera_frame" for row in first)
    assert tools.has_pending_work()
    timeout = tools.tick(2)
    assert [row["kind"] for row in timeout] == ["camera_request_timeout"]
    assert not tools.has_pending_work()
    late = tools.tick(5)
    assert not any(row.get("kind") == "camera_frame" for row in late)
    report = tools.report()
    assert report["requests"][0]["status"] == "timeout"
    assert report["requests"][0]["terminal"] is True
    assert link_bytes(report) > 0
    blob = json.dumps(timeout)
    assert "drop" not in blob and "delay_s" not in blob


def test_duplicate_request_id_rejected(bundle):
    tools, _ = setup_tools(bundle)
    tools.submit(request_call(), time_s=0)
    with pytest.raises(ValueError, match="request_id"):
        tools.submit(request_call(request_id="cam-1"), time_s=0)


def test_unknown_sensor_and_permission_errors(bundle):
    tools, backend = setup_tools(bundle)
    assert tools.submit(request_call(sensor="missing"), time_s=0)["error_code"] == "unknown_sensor"
    assert tools.submit(request_call(request_id="cam-2", robot="private"), time_s=0)[
        "error_code"] == "unknown_robot"
    assert tools.submit(request_call(request_id="cam-3"), time_s=0, current_agents=set())[
        "error_code"] == "inactive_robot"
    assert backend.captures == []


def test_resolution_budget_quality_deadline_and_unsupported_field(bundle):
    tools, backend = setup_tools(bundle)
    assert tools.submit(request_call(request_id="cam-r", width=999), time_s=0)[
        "error_code"] == "resolution_exceeded"
    assert tools.submit(request_call(request_id="cam-b", max_bytes=5_000_000), time_s=0)[
        "error_code"] == "budget_exceeded"
    assert tools.submit(request_call(request_id="cam-d", deadline_s=0.0), time_s=0)[
        "error_code"] == "invalid_deadline"
    assert tools.submit(request_call(
        request_id="cam-q", quality_extra={"quality_factor": 0.5}), time_s=0)[
        "error_code"] == "invalid_arguments"
    assert tools.submit(request_call(
        request_id="cam-u", extra_args={"requested_sim_time_s": 0.0}), time_s=0)[
        "error_code"] == "unsupported_field"
    assert backend.captures == []


def test_busy_backpressure(bundle):
    tools, backend = setup_tools(bundle, max_outstanding_per_sensor=1)
    assert tools.submit(request_call(request_id="cam-a"), time_s=0)["accepted"]
    assert tools.submit(request_call(request_id="cam-b"), time_s=0)["error_code"] == "busy"
    assert backend.captures == []


def test_cancel_before_capture(bundle):
    tools, backend = setup_tools(bundle)
    tools.submit(request_call(request_id="cam-a", deadline_s=10), time_s=0)
    tools.submit(cancel_call("cam-a", request_id="cam-c", now=0), time_s=0)
    events = tools.tick(0)
    assert backend.captures == []
    assert not any(row.get("kind") == "camera_frame" for row in events)
    assert tools.report()["requests"][0]["status"] == "cancelled"
    assert tools.report()["requests"][0]["terminal"] is True


def test_cache_hit_still_remeters_bytes(bundle):
    tools, backend = setup_tools(bundle)
    tools.submit(request_call(request_id="cam-a"), time_s=0)
    tools.tick(0)
    first_bytes = link_bytes(tools.report())
    tools.submit(request_call(request_id="cam-b"), time_s=0)
    events = tools.tick(0)
    frames = [row for row in events if row.get("kind") == "camera_frame"]
    assert frames and frames[0]["data"]["cache_hit"] is True
    assert len(backend.captures) == 1
    assert link_bytes(tools.report()) > first_bytes


def test_p1_oversized_frame_not_cached_and_fails_terminal(bundle):
    backend = FakeOnDemandCameraBackend(
        warmup_frames=0, idle_timeout_s=1.0, oversized_bytes=20_000)
    tools, _ = setup_tools(bundle, backend=backend)
    tools.submit(request_call(request_id="cam-big", max_bytes=100), time_s=0)
    events = tools.tick(0)
    assert not any(row.get("kind") == "camera_frame" for row in events)
    assert any(row.get("kind") == "camera_request_failed" for row in events)
    assert tools.report()["cache_entries"] == 0
    assert tools.report()["requests"][0]["status"] == "failed"
    # Repeat failure must not grow cache.
    tools.submit(request_call(request_id="cam-big-2", max_bytes=100), time_s=0)
    tools.tick(0)
    assert tools.report()["cache_entries"] == 0


def test_p1_inactive_robot_emits_terminal_cancel(bundle):
    tools, backend = setup_tools(bundle)
    tools.submit(request_call(request_id="cam-a", deadline_s=10), time_s=0)
    events = tools.tick(0, current_agents=set())
    assert backend.captures == []
    cancelled = [row for row in events if row.get("kind") == "camera_request_cancelled"]
    assert len(cancelled) == 1
    assert cancelled[0]["data"] == {
        "request_id": "cam-a", "reason": "inactive_robot", "time_s": 0.0,
    }
    assert tools.report()["requests"][0]["terminal"] is True
    assert tools.report()["requests"][0]["status"] == "cancelled"


def test_p1_inactive_after_publish_suppresses_late_frame(bundle):
    tools, _ = setup_tools(bundle, contract={"delay_s": 2}, validity_s=10)
    tools.submit(request_call(request_id="cam-a", deadline_s=10), time_s=0)
    tools.tick(0, current_agents={ROBOT})
    events = tools.tick(1, current_agents=set())
    assert any(row.get("kind") == "camera_request_cancelled" for row in events)
    late = tools.tick(2, current_agents=set())
    assert not any(row.get("kind") == "camera_frame" for row in late)
    assert link_bytes(tools.report()) > 0


def test_p1_backend_close_destroys_owned_product(bundle):
    backend = FakeOnDemandCameraBackend(warmup_frames=0, idle_timeout_s=10.0)
    tools, _ = setup_tools(bundle, backend=backend)
    tools.submit(request_call(), time_s=0)
    tools.tick(0)
    assert backend.lifecycle.active_sensor_keys()
    tools.close()
    assert backend.lifecycle.active_sensor_keys() == []
    assert backend.lifecycle.telemetry()["destroy_count"] >= 1
    tools.close()  # idempotent


def test_p1_external_owner_blocks_destroy(bundle):
    backend = FakeOnDemandCameraBackend(warmup_frames=0, idle_timeout_s=0.0)
    key = f"{ROBOT}/{SENSOR}"
    backend.lifecycle.register_external_owner(key, "ros_continuous")
    tools, _ = setup_tools(bundle, backend=backend)
    tools.submit(request_call(), time_s=0)
    tools.tick(0)
    assert key in backend.lifecycle.active_sensor_keys()
    tools.close()
    assert key in backend.lifecycle.active_sensor_keys()


def test_p1_idle_timeout_releases_on_later_tick(bundle):
    backend = FakeOnDemandCameraBackend(warmup_frames=0, idle_timeout_s=1.0)
    tools, _ = setup_tools(bundle, backend=backend)
    tools.submit(request_call(), time_s=0)
    tools.tick(0)
    assert backend.lifecycle.active_sensor_keys()
    tools.tick(0.5)
    assert backend.lifecycle.active_sensor_keys()
    tools.tick(1.0)
    assert backend.lifecycle.active_sensor_keys() == []
    assert backend.lifecycle.telemetry()["destroy_count"] == 1


def test_p1_capture_failure_cleans_owned_product(bundle):
    backend = FakeOnDemandCameraBackend(
        warmup_frames=0, idle_timeout_s=1.0,
        fail_sensors={f"{ROBOT}/{SENSOR}"})
    tools, _ = setup_tools(bundle, backend=backend)
    tools.submit(request_call(), time_s=0)
    events = tools.tick(0)
    assert any(row.get("kind") == "camera_request_failed" for row in events)
    assert backend.lifecycle.active_sensor_keys() == []


def test_member_join_requires_predeclared_catalog(bundle):
    catalog = sensor_catalog()
    catalog["joined"] = sensor_catalog(robot="joined")["joined"]
    tools, _ = setup_tools(bundle, catalog=catalog)
    with pytest.raises(RuntimeError, match="direct register_public_robot is disabled"):
        tools.register_public_robot("unknown_join")
    assert "joined" not in tools.public_profile()["sensors"]
    assert "joined" not in tools.public_profile()["robot_ids"]
    event = {"type": "member_join", "payload": {"agent_id": "joined"}}
    bind_join_event(tools, event)
    delta = tools.activate_joined_member(event, session_event_index=0, time_s=0.0)
    assert delta["joined_robot_id"] == "joined"
    assert set(delta["sensors"]) == {"joined"}
    assert "joined" in tools.public_profile()["sensors"]
    assert tools.submit(request_call(request_id="cam-j", robot="joined"), time_s=0)["accepted"]
    # Idempotent for the same session event index.
    again = tools.activate_joined_member(event, session_event_index=0, time_s=0.0)
    assert again["already_active"] is True


def test_p1_hidden_join_identity_not_in_public_profile(bundle):
    hidden = "warehouse_nvd_hidden_joiner_1"
    catalog = sensor_catalog()
    catalog[hidden] = sensor_catalog(robot=hidden)[hidden]
    tools, _ = setup_tools(bundle, catalog=catalog)
    profile = tools.public_profile()
    blob = json.dumps(profile, sort_keys=True)
    assert hidden not in blob
    assert hidden not in profile["sensors"]
    assert hidden not in profile["robot_ids"]
    assert tools.submit(request_call(request_id="cam-h", robot=hidden), time_s=0)[
        "error_code"] == "unknown_robot"
    with pytest.raises(RuntimeError, match="direct register_public_robot is disabled"):
        tools.register_public_robot(hidden)
    event = {"type": "member_join", "payload": {"agent_id": hidden}}
    bind_join_event(tools, event)
    tools.activate_joined_member(event, session_event_index=0, time_s=0.0)
    profile2 = tools.public_profile()
    assert set(profile2["sensors"]) - set(profile["sensors"]) == {hidden}
    assert hidden in profile2["robot_ids"]
    assert tools.submit(request_call(request_id="cam-h2", robot=hidden), time_s=0)["accepted"]


def test_p1_destroy_failure_keeps_delivered_frame(bundle):
    backend = FakeOnDemandCameraBackend(
        warmup_frames=0, idle_timeout_s=0.0, raise_on_destroy=True)
    tools, _ = setup_tools(bundle, backend=backend)
    tools.submit(request_call(), time_s=0)
    events = tools.tick(0)
    frames = [row for row in events if row.get("kind") == "camera_frame"]
    assert len(frames) == 1
    assert tools.report()["requests"][0]["status"] == "delivered"
    assert any(row.get("kind") == "camera_lifecycle_error" for row in events)
    assert tools.report()["lifecycle_errors"]
    # Failures recorded; destroy_count must not silently claim success for that sensor.
    assert tools.report()["render_lifecycle"]["failures"]


def test_p1_episode_budget_rejects_over_budget_deadline(bundle):
    tools, _ = setup_tools(bundle)
    budget = tools.public_profile()["simulation_budget_s"]
    assert tools.submit(request_call(deadline_s=budget + 1), time_s=0)[
        "error_code"] == "invalid_deadline"


def test_p1_close_finalizes_pending_beyond_budget(bundle):
    tools, _ = setup_tools(
        bundle, contract={"delay_s": 300}, validity_s=400.0, episode_id="budget-end")
    budget = tools.public_profile()["simulation_budget_s"]
    assert budget == 240.0
    assert tools.submit(
        request_call(request_id="cam-late", deadline_s=budget), time_s=0)["accepted"]
    tools.tick(0)
    assert tools.has_pending_work()
    events = tools.finalize_episode(budget)
    assert any(row.get("kind") == "camera_request_cancelled"
               and row["data"]["reason"] == "episode_ended" for row in events)
    assert tools.report()["requests"][0]["status"] == "episode_ended"
    assert tools.report()["requests"][0]["terminal"] is True
    assert tools.report()["episode_finalized"] is True
    tools.close()
    tools.close()  # idempotent
    assert tools.report()["requests"][0]["status"] == "episode_ended"
    assert link_bytes(tools.report()) > 0


def test_p2_publish_failure_does_not_leave_cache(bundle):
    tools, backend = setup_tools(bundle)

    def boom(*_args, **_kwargs):
        raise ValueError("duplicate message_id forced")

    tools.telemetry.publish = boom  # type: ignore[method-assign]
    tools.submit(request_call(), time_s=0)
    events = tools.tick(0)
    assert tools.report()["cache_entries"] == 0
    assert any(row.get("kind") == "camera_request_failed" for row in events)
    # Repeat must not grow memory.
    tools.submit(request_call(request_id="cam-2"), time_s=0)
    tools.tick(0)
    assert tools.report()["cache_entries"] == 0


def test_p2_payload_ref_rejected_by_telemetry(bundle):
    telemetry = GoalCameraTelemetry(CommunicationRuntime(), message_prefix="ref")
    with pytest.raises(ValueError, match="payload_ref"):
        telemetry.publish(ROBOT, {
            "request_id": "r",
            "frame_id": "f",
            "capture_sim_time_s": 0.0,
            "physics_step": 0,
            "pose": {"source": "synthetic"},
            "calibration": {},
            "encoding": "png",
            "payload_b64": "QQ==",
            "payload_sha256": "a" * 64,
            "byte_length": 1,
            "cache_hit": False,
            "status": "fresh",
            "payload_ref": "file:///tmp/secret.png",
        }, valid_until_s=1.0)


def test_p1_finalize_rejects_submit_cancel_and_activation(bundle):
    catalog = sensor_catalog()
    catalog["joined"] = sensor_catalog(robot="joined")["joined"]
    tools, _ = setup_tools(bundle, catalog=catalog)
    tools.submit(request_call(request_id="cam-a", deadline_s=10), time_s=0)
    tools.finalize_episode(0.0)
    with pytest.raises(RuntimeError, match="finalized"):
        tools.submit(request_call(request_id="cam-b"), time_s=0)
    with pytest.raises(RuntimeError, match="finalized"):
        tools.submit(cancel_call("cam-a", request_id="cam-c"), time_s=0)
    with pytest.raises(RuntimeError, match="finalized"):
        tools.activate_joined_member(
            {"type": "member_join", "payload": {"agent_id": "joined"}},
            session_event_index=1, time_s=0.0)
    # No non-terminal accepted request after finalize.
    assert all(row.get("terminal") for row in tools.report()["requests"])


def test_p1_close_terminalizes_orphaned_nonterminal_rows(bundle):
    tools, _ = setup_tools(bundle)
    tools.submit(request_call(request_id="cam-orphan"), time_s=0)
    # Simulate a ledger row that left pending/awaiting maps without a terminal.
    orphan = tools._pending.pop("cam-orphan")
    tools._outstanding.clear()
    tools._requests["cam-orphan"]["terminal"] = False
    tools._requests["cam-orphan"]["status"] = "accepted"
    tools.close()
    tools.close()
    row = next(r for r in tools.report()["requests"] if r["request_id"] == "cam-orphan")
    assert row["terminal"] is True
    assert row["status"] == "episode_ended"
    assert sum(1 for r in tools.report()["requests"] if r["request_id"] == "cam-orphan") == 1
    del orphan  # silence unused


def test_p2_cleanup_retry_after_destroy_failure(bundle):
    backend = FakeOnDemandCameraBackend(
        warmup_frames=0, idle_timeout_s=10.0, raise_on_destroy=True)
    tools, _ = setup_tools(bundle, backend=backend)
    tools.submit(request_call(), time_s=0)
    events = tools.tick(0)
    assert any(row.get("kind") == "camera_frame" for row in events)
    tools.close()
    report = tools.report()["render_lifecycle"]
    assert report["cleanup_pending"] is True
    assert report["cleanup_complete"] is False
    assert report["destroy_count"] == 0
    assert report["failures"]
    first_failures = len(report["failures"])
    # Retry succeeds after destroy is healed.
    backend.set_raise_on_destroy(False)
    tools.close()
    report2 = tools.report()["render_lifecycle"]
    assert report2["cleanup_complete"] is True
    assert report2["cleanup_pending"] is False
    assert report2["destroy_count"] == 1
    assert len(report2["failures"]) >= first_failures
    # Delivered frame evidence remains.
    assert tools.report()["requests"][0]["status"] == "delivered"


def test_p2_cleanup_stays_pending_while_destroy_keeps_failing(bundle):
    backend = FakeOnDemandCameraBackend(
        warmup_frames=0, idle_timeout_s=10.0, raise_on_destroy=True)
    tools, _ = setup_tools(bundle, backend=backend)
    tools.submit(request_call(), time_s=0)
    tools.tick(0)
    tools.close()
    tools.close()
    report = tools.report()["render_lifecycle"]
    assert report["cleanup_pending"] is True
    assert report["cleanup_complete"] is False
    assert report["destroy_count"] == 0
    assert len(report["failures"]) >= 2


def test_episode_teardown_clears_cache(bundle):
    tools, _ = setup_tools(bundle)
    tools.submit(request_call(), time_s=0)
    tools.tick(0)
    assert tools.report()["cache_entries"] == 1
    tools.close()
    assert tools.report()["cache_entries"] == 0


def test_idle_destroy_rebuild_warmup_and_shared_owner():
    created = []
    destroyed = []

    def create(key):
        created.append(key)
        return {"key": key, "alive": True}

    def destroy(product):
        assert product["alive"]
        product["alive"] = False
        destroyed.append(product["key"])

    life = OnDemandRenderLifecycle(
        create_product=create, destroy_product=destroy,
        idle_timeout_s=1.0, warmup_frames=2)
    life.ensure("r/s", now_s=0.0)
    assert life.warmup_tick("r/s", now_s=0.0) is False
    assert life.warmup_tick("r/s", now_s=0.1) is False
    assert life.warmup_tick("r/s", now_s=0.2) is True
    life.mark_queue_activity("r/s", queue_depth=0, now_s=0.5)
    assert life.release_if_idle("r/s", now_s=1.0) is False
    assert life.release_if_idle("r/s", now_s=1.6) is True
    life.register_external_owner("r/s", "ros_continuous")
    life.ensure("r/s", now_s=2.0)
    life.mark_queue_activity("r/s", queue_depth=0, now_s=2.0)
    assert life.force_release("r/s", now_s=10.0) is False
    life.release_external_owner("r/s", "ros_continuous")
    assert life.force_release("r/s", now_s=10.0) is True


def test_registry_declares_ondemand_capability_without_activation():
    """Old interface-catalog declaration, remapped to the skill registry."""
    row = next(item for item in SKILL_REGISTRY
               if item["skill_id"] == "eai.observation.camera")
    assert row["kind"] == "inspection"
    assert row["subscription"] is False
    assert row["execution_validated"] is False
    assert row["scoreable"] is False
    # Declaration data only: no runtime is activated by the registry itself.
    assert isinstance(row["module"], str) and isinstance(row["status"], str)


# ----------------------------------------------------------------------
# Builder backend protocol (gate C prerequisite, static CPU only).
# ----------------------------------------------------------------------
BUILDER_PUBLIC = {
    "task_id": "warehouse_nvd_observation_builder",
    "mission": {"scene_key": "warehouse_nvd"},
    "candidate_robot_pool": [{"id": "r1"}],
    "budgets": {"simulation_s": 240.0},
}
BUILDER_CATALOG = {"r1": {"cam": {
    "member": "primary", "modalities": ["rgb"], "encodings": ["png"],
    "max_width": 128, "max_height": 96, "max_bytes": 100_000,
}}}


def builder_request(request_id="cam-1", *, now=0.0, **overrides):
    arguments = {
        "robot_id": "r1", "sensor_id": "cam", "modalities": ["rgb"],
        "deadline_s": 5.0,
        "resolution": {"width": 32, "height": 24},
        "quality": {"encoding": "png", "max_bytes": 50_000},
        "freshness": "capture_at_or_after_submit",
    }
    arguments.update(overrides)
    return {"schema": CAMERA_TOOL_SCHEMA, "version": 1,
            "request_id": request_id, "submitted_at_s": now,
            "tool": "request_observation", "arguments": arguments}


def make_builder_backend(*, payload=b"builder-frame", fail_read=False):
    state = {"created": [], "destroyed": []}
    binding = BuilderCameraBinding(
        robot_id="r1", sensor_id="cam", camera_prim="/World/robots/r1/cam",
        publication={"topic": "/r1/cam/color", "encoding": "png"})

    def create(binding_, width, height, encoding):
        product = {"prim": binding_.camera_prim, "width": width, "height": height,
                   "encoding": encoding}
        state["created"].append(product)
        return product

    def destroy(product):
        state["destroyed"].append(product)

    def read(product, binding_, request, time_s):
        if fail_read:
            raise RuntimeError("annotator read failed")
        return {"payload": payload, "capture_sim_time_s": float(time_s),
                "frame_id": "builder-frame-1"}

    backend = BuilderCameraBackend(
        {("r1", "cam"): binding},
        create_render_product=create, destroy_render_product=destroy,
        read_annotator=read)
    return backend, state


def make_builder_tools(backend, **kwargs):
    return GoalCameraTools(
        BUILDER_PUBLIC, episode_id="obs-builder", sensor_catalog=BUILDER_CATALOG,
        backend=backend,
        telemetry=GoalCameraTelemetry(CommunicationRuntime(), message_prefix="obs-builder"),
        validity_s=10.0, **kwargs)


def test_builder_backend_capture_lifecycle_via_tools():
    backend, state = make_builder_backend()
    tools = make_builder_tools(backend)
    assert tools.submit(builder_request(), time_s=0)["accepted"] is True
    events = tools.tick(0)
    frames = [e for e in events if e.get("kind") == "camera_frame"]
    assert len(frames) == 1 and len(state["created"]) == 1
    data = frames[0]["data"]
    assert data["byte_length"] == len(b"builder-frame")
    assert data["calibration"] == {}  # binding default, no invented intrinsics
    assert data["pose"]["source"] == "physx" and data["pose"]["camera_prim"] == "/World/robots/r1/cam"
    assert "payload_ref" not in data
    # Same sensor + dimensions reuse the render product.
    tools.submit(builder_request(request_id="cam-2"), time_s=0)
    tools.tick(0)
    assert len(state["created"]) == 1
    tools.close()
    assert len(state["destroyed"]) == 1
    report = tools.report()["render_lifecycle"]
    assert report["create_count"] == 1 and report["destroy_count"] == 1
    assert report["cleanup_pending"] is False


def test_builder_backend_rejects_changed_resolution_on_live_product():
    """A reused render product must never silently serve wrong dimensions."""
    backend, state = make_builder_backend()
    tools = make_builder_tools(backend)
    assert tools.submit(builder_request(), time_s=0)["accepted"] is True
    tools.tick(0)
    assert tools.submit(
        builder_request(request_id="cam-wide", now=0,
                        resolution={"width": 64, "height": 24}), time_s=0)["accepted"] is True
    events = tools.tick(0)
    assert any(e.get("kind") == "camera_request_failed" for e in events)
    assert tools.report()["requests"][1]["status"] == "failed"
    assert len(state["created"]) == 1  # no silent recreation either
    tools.close()


def test_builder_backend_fail_closed_paths():
    backend, _ = make_builder_backend(fail_read=True)
    tools = make_builder_tools(backend)
    assert tools.submit(builder_request(), time_s=0)["accepted"] is True
    events = tools.tick(0)
    assert any(e.get("kind") == "camera_request_failed" for e in events)
    # Unknown robot binding is rejected before any product is created.
    assert tools.submit(builder_request(request_id="cam-x", robot_id="ghost"), time_s=0)[
        "error_code"] == "unknown_robot"

    # Binding-level invariants.
    with pytest.raises(ValueError, match="builder_registered_default"):
        BuilderCameraBinding(robot_id="r1", sensor_id="cam", camera_prim="/p",
                             publication={"t": 1}, builder_registered_default=False)
    with pytest.raises(ValueError, match="publication"):
        BuilderCameraBinding(robot_id="r1", sensor_id="cam", camera_prim="/p", publication={})


def test_builder_backend_retries_failed_render_product_cleanup():
    backend, state = make_builder_backend()
    should_fail = {"value": True}
    original_destroy = backend._destroy

    def flaky_destroy(product):
        if should_fail["value"]:
            raise RuntimeError("destroy failed once")
        original_destroy(product)

    backend._destroy = flaky_destroy
    assert backend.capture({"robot_id": "r1", "sensor_id": "cam", "width": 32,
                            "height": 24, "encoding": "png"}, 0.0)["payload"]
    backend.close(now_s=0.0)
    first = backend.lifecycle_report()
    assert first["cleanup_pending"] is True and first["cleanup_complete"] is False
    should_fail["value"] = False
    backend.close(now_s=1.0)
    second = backend.lifecycle_report()
    assert second["cleanup_pending"] is False and second["cleanup_complete"] is True
    assert second["destroy_count"] == 1 and len(state["destroyed"]) == 1


def test_capture_delay_s_defers_frame_until_ready():
    backend = FakeOnDemandCameraBackend(warmup_frames=0)
    tools = GoalCameraTools(
        BUILDER_PUBLIC, episode_id="obs-delay", sensor_catalog=BUILDER_CATALOG,
        backend=backend,
        telemetry=GoalCameraTelemetry(CommunicationRuntime(), message_prefix="obs-delay"),
        validity_s=10.0, capture_delay_s=2.0)
    assert tools.public_profile()["capture_delay_s"] == 2.0
    assert tools.submit(builder_request(now=0.0), time_s=0)["accepted"] is True
    early = tools.tick(1.0)
    assert not any(e.get("kind") == "camera_frame" for e in early)
    ready = tools.tick(2.0)
    assert any(e.get("kind") == "camera_frame" for e in ready)
    # The wakeup schedule must expose the deferred capture time.
    tools.submit(builder_request(request_id="cam-2", now=2.0), time_s=2.0)
    assert tools.next_wakeup_s() == pytest.approx(4.0)
