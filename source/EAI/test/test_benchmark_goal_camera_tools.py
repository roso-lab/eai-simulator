"""On-demand camera companion tools: contract, metering, lifecycle, and leak fixes."""

from copy import deepcopy
import json

import pytest

from demo.benchmark.communication import CommunicationRuntime, validate_communication_report
from demo.benchmark.goal_algorithm_adapter import GoalAlgorithmHost
from demo.benchmark.goal_camera_ondemand import (
    FakeOnDemandCameraBackend,
    OnDemandRenderLifecycle,
)
from demo.benchmark.goal_camera_telemetry import GoalCameraTelemetry
from demo.benchmark.goal_camera_tools import CAMERA_TOOL_SCHEMA, GoalCameraTools
from demo.benchmark.goal_driven_tasks import build_goal_task_package
from demo.benchmark.goal_episode_runner import FormalGoalEpisodeRunner
from demo.benchmark.goal_pose_telemetry import GoalPoseTelemetry
from demo.benchmark.goal_pose_tools import GoalPoseTools, POSE_TOOL_SCHEMA


ROBOT = "warehouse_ground_1"
SENSOR = "camera_rgb_primary"


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


def consume(message_id, now=0.0, action_id="consume-cam"):
    return {
        "schema": "eai.benchmark.goal_action",
        "version": 1,
        "action_id": action_id,
        "submitted_at_s": now,
        "type": "consume_message",
        "payload": {"message_id": message_id},
    }


class Adapter:
    def __init__(self):
        self.seen = []
        self.calls = 0
        self.actions = []
        self.camera_profile = None

    def manifest(self):
        return {
            "kind": "participant", "id": "camera-tools-test", "version": 1,
            "interface": "eai.benchmark.goal_algorithm_adapter", "interface_version": 1,
            "evaluation_track": "goal_driven",
            "input_scope": "participant_visible_events_only",
            "control_scope": "benchmark_managed_low_level_control",
        }

    def initialize(self, public_task, participant_api):
        self.public = deepcopy(public_task)
        self.api = deepcopy(participant_api)

    def initialize_camera_tools(self, profile):
        self.camera_profile = deepcopy(profile)

    def act(self, events):
        self.seen.extend(deepcopy(events))
        self.calls += 1
        result, self.actions = self.actions, []
        return result

    def close(self):
        pass


@pytest.fixture
def bundle():
    return build_goal_task_package("warehouse_nvd")


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


def frame_events(events):
    return [e for e in events if e.get("kind") == "camera_frame"
            or (isinstance(e, dict) and e.get("type") == "observation"
                and e.get("payload", {}).get("kind") == "camera_frame")]


def test_no_opt_in_zero_requests_zero_frames_zero_link_bytes(bundle):
    tools, backend = setup_tools(bundle)
    adapter = Adapter()
    adapter.initialize_camera_tools = None
    with GoalAlgorithmHost(bundle["public_task"], bundle["participant_api"], adapter,
                           camera_tools=tools) as host:
        host.request_actions()
    assert backend.captures == []
    assert tools.report()["request_count"] == 0
    assert tools.report()["communication"]["messages"] == {}
    assert link_bytes(tools.report()) == 0


def test_single_request_one_frame_with_hash_clock_pose_calibration(bundle):
    tools, backend = setup_tools(bundle)
    adapter = Adapter()
    adapter.actions = [request_call()]
    with GoalAlgorithmHost(bundle["public_task"], bundle["participant_api"], adapter,
                           camera_tools=tools) as host:
        assert host.request_actions()[0]["accepted"] is True
        host.request_actions()
    frames = [e for e in adapter.seen if e["type"] == "observation"
              and e["payload"].get("kind") == "camera_frame"]
    assert len(frames) == 1
    data = frames[0]["payload"]["data"]
    assert data["request_id"] == "cam-1"
    assert data["cache_hit"] is False
    assert data["byte_length"] > 0
    assert "payload_ref" not in data
    assert data["pose"]["source"] == "synthetic"
    assert link_bytes(tools.report()) > 0
    assert validate_communication_report(tools.report()["communication"])["validated"]


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


def test_p1_runner_idle_wakes_at_camera_deadline_not_fixed_step(bundle, tmp_path):
    tools, _ = setup_tools(
        bundle, contract={"delay_s": 5}, validity_s=20.0, episode_id="cam-test")
    state = {
        "robot_regions": {r["id"]: "rack_aisle_a"
                          for r in bundle["public_task"]["candidate_robot_pool"]},
        "object_states": {
            o["id"]: {"region_id": o["physical_properties"]["initial_region_id"]}
            for o in bundle["public_task"]["public_world"]["objects"]
        },
    }

    class WaitingAdapter(Adapter):
        def act(self, events):
            self.seen.extend(deepcopy(events))
            if self.calls == 0:
                self.calls += 1
                return [request_call(deadline_s=2)]
            return []

    class Backend:
        def __init__(self):
            self.ticks = []

        def advance_idle(self, time_s):
            self.ticks.append(time_s)
            return {"time_s": time_s, "runtime_state": deepcopy(state)}

    backend = Backend()
    adapter = WaitingAdapter()
    result = FormalGoalEpisodeRunner(
        bundle["public_task"], bundle["participant_api"], adapter, backend,
        episode_id="cam-test", output_root=tmp_path / "episode",
        camera_tools=tools).run(initial_state=state)
    assert result["error"] is None
    assert backend.ticks == [2]
    assert not any(
        e["payload"].get("kind") == "camera_frame" for e in adapter.seen)
    assert any(
        e["payload"].get("kind") == "camera_request_timeout" for e in adapter.seen)


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


def test_p1_bandwidth_limited_queue_jumps_windows_within_turn_budget(bundle, tmp_path):
    """~666 B transport / 45 B per window should deliver near t=14, not burn 256 turns."""
    from base64 import b64encode
    import hashlib

    from demo.benchmark.goal_camera_tools import estimate_camera_transport_size

    base = {
        "request_id": "sized",
        "frame_id": "f",
        "capture_sim_time_s": 0.0,
        "physics_step": 0,
        "pose": {"position_m": [0, 0, 0], "orientation_xyzw": [0, 0, 0, 1],
                 "source": "synthetic"},
        "calibration": {"model": "pinhole"},
        "encoding": "png",
        "cache_hit": False,
        "status": "fresh",
    }
    # Fixed raw payload near the Sol repro (~666 B probe transport). Live
    # published size may differ; assert delivery from the archived size_bytes.
    payload = b"A" * 145
    probe = {
        **base,
        "payload_b64": b64encode(payload).decode("ascii"),
        "payload_sha256": hashlib.sha256(payload).hexdigest(),
        "byte_length": len(payload),
    }
    assert 660 <= estimate_camera_transport_size(probe) <= 670

    backend = FakeOnDemandCameraBackend(
        warmup_frames=0, idle_timeout_s=10.0, fixed_payload=payload)
    tools, _ = setup_tools(
        bundle, backend=backend, episode_id="bw-test",
        contract={"bytes_per_window": 45, "window_s": 1.0, "delay_s": 0.0},
        validity_s=30.0,
        catalog=sensor_catalog(max_bytes=50_000),
    )
    state = {
        "robot_regions": {r["id"]: "rack_aisle_a"
                          for r in bundle["public_task"]["candidate_robot_pool"]},
        "object_states": {
            o["id"]: {"region_id": o["physical_properties"]["initial_region_id"]}
            for o in bundle["public_task"]["public_world"]["objects"]
        },
    }

    class WaitingAdapter(Adapter):
        def act(self, events):
            self.seen.extend(deepcopy(events))
            if self.calls == 0:
                self.calls += 1
                return [request_call(deadline_s=20, max_bytes=50_000)]
            return []

    class Backend:
        def __init__(self):
            self.ticks = []

        def advance_idle(self, time_s):
            self.ticks.append(time_s)
            return {"time_s": time_s, "runtime_state": deepcopy(state)}

    backend_idle = Backend()
    adapter = WaitingAdapter()
    result = FormalGoalEpisodeRunner(
        bundle["public_task"], bundle["participant_api"], adapter, backend_idle,
        episode_id="bw-test", output_root=tmp_path / "episode",
        camera_tools=tools, max_turns=256).run(initial_state=state)
    assert result["error"] is None
    assert result["turns"] < 256
    assert any(
        e.get("type") == "observation"
        and e.get("payload", {}).get("kind") == "camera_frame"
        for e in adapter.seen)
    req = tools.report()["requests"][0]
    assert req["status"] == "delivered"
    assert req["terminal"] is True
    message = next(iter(tools.report()["communication"]["messages"].values()))
    size = message["size_bytes"]
    assert 600 <= size <= 900
    expected_delivery = float((size - 1) // 45)  # window_s=1, delay=0
    delivered_at = next(
        delivery["delivered_at_s"]
        for delivery in message["recipients"].values()
        if delivery.get("status") == "delivered")
    assert delivered_at == pytest.approx(expected_delivery, abs=1e-6)
    assert 12.0 <= delivered_at <= 20.0
    assert len(backend_idle.ticks) <= (size // 45) + 3
    assert backend_idle.ticks[-1] == pytest.approx(delivered_at, abs=1e-6)
    gaps = [b - a for a, b in zip(backend_idle.ticks, backend_idle.ticks[1:])]
    assert gaps and min(gaps) >= 0.5


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


def test_p1_host_member_join_delivers_profile_delta(bundle):
    """Session member_join → host activate → participant update_camera_tools."""
    hidden = "joined_camera_reserve"
    catalog = sensor_catalog()
    catalog[hidden] = sensor_catalog(robot=hidden)[hidden]
    tools, _ = setup_tools(bundle, catalog=catalog, episode_id="join-delta")
    declaration = deepcopy(bundle["public_task"]["candidate_robot_pool"][1])
    declaration["id"] = hidden

    class JoinAdapter(Adapter):
        def __init__(self):
            super().__init__()
            self.deltas = []
            self.profile_hashes = []

        def update_camera_tools(self, delta):
            self.deltas.append(deepcopy(delta))
            self.camera_profile = {
                **(self.camera_profile or {}),
                "sensors": {
                    **((self.camera_profile or {}).get("sensors") or {}),
                    **delta["sensors"],
                },
            }
            self.profile_hashes.append(delta["public_profile_sha256"])

        def act(self, events):
            self.seen.extend(deepcopy(events))
            self.calls += 1
            if any(e["type"] == "member_join" for e in events):
                return [request_call(request_id="cam-joined", robot=hidden, now=1.0)]
            return []

    adapter = JoinAdapter()
    assert hidden not in json.dumps(tools.public_profile())
    with GoalAlgorithmHost(bundle["public_task"], bundle["participant_api"], adapter,
                           camera_tools=tools) as host:
        host.emit("member_join", 1.0, {
            "agent_id": hidden,
            "capability_declaration": declaration,
            "capability_version": 1,
        })
        host.request_actions()
        host.request_actions()
        assert len(adapter.deltas) == 1
        assert adapter.deltas[0]["joined_robot_id"] == hidden
        assert set(adapter.deltas[0]["sensors"]) == {hidden}
        assert hidden in tools.public_profile()["sensors"]
        assert tools.report()["requests"]
        assert tools.report()["requests"][0]["robot_id"] == hidden
        # Direct spoof still fails while the tools remain open.
        with pytest.raises(RuntimeError, match="direct register_public_robot is disabled"):
            tools.register_public_robot(hidden)
        with pytest.raises(ValueError, match="not committed in GoalSession"):
            tools.activate_joined_member(
                {"type": "member_join", "payload": {"agent_id": "totally_unknown"}},
                session_event_index=99, time_s=1.0)


def test_p1_host_member_join_observation_fallback_without_hook(bundle):
    """Participants without update_camera_tools still get a public profile delta."""
    hidden = "joined_camera_obs"
    catalog = sensor_catalog()
    catalog[hidden] = sensor_catalog(robot=hidden)[hidden]
    tools, _ = setup_tools(bundle, catalog=catalog, episode_id="join-obs")
    declaration = deepcopy(bundle["public_task"]["candidate_robot_pool"][1])
    declaration["id"] = hidden

    class ObsOnlyAdapter(Adapter):
        def act(self, events):
            self.seen.extend(deepcopy(events))
            return []

    adapter = ObsOnlyAdapter()
    assert not hasattr(adapter, "update_camera_tools")
    with GoalAlgorithmHost(bundle["public_task"], bundle["participant_api"], adapter,
                           camera_tools=tools) as host:
        host.emit("member_join", 1.0, {
            "agent_id": hidden,
            "capability_declaration": declaration,
            "capability_version": 1,
        })
        host.request_actions()
    deltas = [
        e for e in adapter.seen
        if e["type"] == "observation"
        and e["payload"].get("kind") == "camera_tools_profile_delta"
    ]
    assert len(deltas) == 1
    data = deltas[0]["payload"]["data"]
    assert data["joined_robot_id"] == hidden
    assert set(data["sensors"]) == {hidden}
    assert hidden in tools.public_profile()["sensors"]


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


def test_pose_tools_coexist(bundle):
    cam_tools, cam_backend = setup_tools(bundle)

    def sample(robot, now):
        return {
            "sampled_at_s": now, "position_m": [now, 0.0, 0.5], "yaw_rad": 0.0,
            "frame_id": "world", "source": "synthetic",
            "quality": {"position_error_m": None},
        }

    pose = GoalPoseTools(
        bundle["public_task"], episode_id="cam-test", sample=sample,
        telemetry=GoalPoseTelemetry(CommunicationRuntime(), message_prefix="cam-test"))
    adapter = Adapter()

    def initialize_tools(profile):
        adapter.pose_profile = deepcopy(profile)

    adapter.initialize_tools = initialize_tools
    adapter.actions = [
        {"schema": POSE_TOOL_SCHEMA, "version": 1, "call_id": "pose-1",
         "submitted_at_s": 0.0, "tool": "query_pose",
         "arguments": {"robot_ids": [ROBOT]}},
        request_call(request_id="cam-1"),
    ]
    with GoalAlgorithmHost(bundle["public_task"], bundle["participant_api"], adapter,
                           pose_tools=pose, camera_tools=cam_tools) as host:
        host.request_actions()
        host.request_actions()
    kinds = {e["payload"].get("kind") for e in adapter.seen if e["type"] == "observation"}
    assert "pose_update" in kinds and "camera_frame" in kinds
    assert cam_backend.captures


def test_episode_teardown_clears_cache(bundle):
    tools, _ = setup_tools(bundle)
    tools.submit(request_call(), time_s=0)
    tools.tick(0)
    assert tools.report()["cache_entries"] == 1
    tools.close()
    assert tools.report()["cache_entries"] == 0


def test_formal_runner_archives_camera_report(bundle, tmp_path):
    tools, _ = setup_tools(bundle, contract={"delay_s": 1}, episode_id="cam-test")
    state = {
        "robot_regions": {r["id"]: "rack_aisle_a"
                          for r in bundle["public_task"]["candidate_robot_pool"]},
        "object_states": {
            o["id"]: {"region_id": o["physical_properties"]["initial_region_id"]}
            for o in bundle["public_task"]["public_world"]["objects"]
        },
    }

    class WaitingAdapter(Adapter):
        def act(self, events):
            self.seen.extend(deepcopy(events))
            if self.calls == 0:
                self.calls += 1
                return [request_call()]
            frames = [e for e in events if e["payload"].get("kind") == "camera_frame"]
            if frames:
                return [consume(frames[0]["payload"]["message_id"], now=1)]
            return []

    class Backend:
        def advance_idle(self, time_s):
            return {"time_s": time_s, "runtime_state": deepcopy(state)}

    result = FormalGoalEpisodeRunner(
        bundle["public_task"], bundle["participant_api"], WaitingAdapter(), Backend(),
        episode_id="cam-test", output_root=tmp_path / "episode",
        camera_tools=tools).run(initial_state=state)
    assert result["error"] is None
    assert result["scoreable"] is False
    archive = json.loads((tmp_path / "episode/camera_tools_report.json").read_text())
    assert archive["execution_validated"] is False
    assert json.loads((tmp_path / "episode/camera_communication_replay.json").read_text())[
        "validated"]


@pytest.mark.parametrize("operation", ["report", "finalize_episode", "close"])
def test_tool_teardown_error_preserves_scoring_archive(bundle, tmp_path, monkeypatch, operation):
    tools, _ = setup_tools(bundle, episode_id="cam-test")
    state = {
        "robot_regions": {r["id"]: "rack_aisle_a"
                          for r in bundle["public_task"]["candidate_robot_pool"]},
        "object_states": {
            o["id"]: {"region_id": o["physical_properties"]["initial_region_id"]}
            for o in bundle["public_task"]["public_world"]["objects"]
        },
    }

    def fail():
        raise RuntimeError(f"{operation} failed during teardown")

    closed = []
    original_close = tools.close

    def close():
        closed.append(True)
        if operation == "close":
            fail()
        original_close()

    monkeypatch.setattr(tools, "close", close)
    if operation != "close":
        monkeypatch.setattr(tools, operation, fail)
    root = tmp_path / "episode"
    result = FormalGoalEpisodeRunner(
        bundle["public_task"], bundle["participant_api"], Adapter(), object(),
        episode_id="cam-test", output_root=root, camera_tools=tools,
    ).run(initial_state=state)
    assert closed
    assert operation in result["error"]
    assert result["scoreable"] is False
    metadata = json.loads((root / "scoring_adapter_metadata.json").read_text())
    assert metadata["episode_result"]["lifecycle_error_count"] == 1
    assert metadata["episode_result"]["error"] == result["error"]
    assert metadata["scoreable"] is False
    assert "lifecycle_errors.json" in metadata["archive_index"]["files"]
    assert "result.json" in metadata["archive_index"]["files"]
    if operation != "report":
        archive = json.loads((root / "camera_tools_report.json").read_text())
        assert archive["closed"] is (operation != "close")


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


def test_catalog_declares_ondemand_capability_without_activation():
    from EAI.interface_catalog.loader import load_catalog

    catalog = load_catalog()
    interface = catalog.interface("benchmark.orsus.ondemand_rgb")
    assert interface.protocol == "benchmark"
    assert interface.kind == "capability"
    device = catalog.device_for_interface("benchmark.orsus.ondemand_rgb")
    assert device.metadata["benchmark_ondemand_rgb"]["activation"] == (
        "declaration_only_not_runtime")
