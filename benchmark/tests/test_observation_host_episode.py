"""Observation host-integration tests: episode driver, archives, join routing.

Benchmark-layer half of the adapted 2026-09-23 companion suite.  The retired
host pieces map onto ``benchmark.observation_episode``:

| ``GoalAlgorithmHost`` / ``GoalSession``     | ``ObservationEpisodeDriver`` host API |
| ``FormalGoalEpisodeRunner``                 | ``ObservationEpisodeDriver.run``      |
| ``build_goal_task_package("warehouse_nvd")``| minimal in-file public task fixture   |
| pose tools coexistence                      | second camera toolset on one shared   |
|                                             | ``CommunicationRuntime``              |
"""

from copy import deepcopy
import json

import pytest

from benchmark.observation_episode import ObservationEpisodeDriver, ObservationSession
from algorithm.skills.observation import (
    CommunicationRuntime,
    FakeOnDemandCameraBackend,
    GoalCameraTelemetry,
    GoalCameraTools,
    validate_communication_report,
)
from algorithm.skills.observation.tools import (
    CAMERA_TOOL_SCHEMA,
    estimate_camera_transport_size,
)


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
    """Retired goal adapter surface minus the goal ceremony."""

    def __init__(self):
        self.seen = []
        self.calls = 0
        self.actions = []
        self.camera_profile = None

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


def link_bytes(report):
    total = 0
    for message in report["communication"]["messages"].values():
        if message["channel"] != "camera":
            continue
        for delivery in message["recipients"].values():
            total += delivery.get("bytes_sent", 0)
    return total


def initial_state(bundle):
    return {
        "robot_regions": {r["id"]: "rack_aisle_a"
                          for r in bundle["public_task"]["candidate_robot_pool"]},
        "object_states": {},
    }


def make_driver(bundle, adapter, backend, *, tools, tmp_path, episode_id="cam-test"):
    return ObservationEpisodeDriver(
        bundle["public_task"], adapter, backend,
        episode_id=episode_id, output_root=tmp_path / "episode",
        camera_tools=tools)


def test_no_opt_in_zero_requests_zero_frames_zero_link_bytes(bundle, tmp_path):
    tools, backend = setup_tools(bundle)
    adapter = Adapter()
    adapter.initialize_camera_tools = None  # type: ignore[method-assign]
    driver = make_driver(bundle, adapter, object(), tools=tools, tmp_path=tmp_path)
    driver.setup()
    driver.request_actions()
    driver.close()
    assert backend.captures == []
    assert tools.report()["request_count"] == 0
    assert tools.report()["communication"]["messages"] == {}
    assert link_bytes(tools.report()) == 0


def test_single_request_one_frame_with_hash_clock_pose_calibration(bundle, tmp_path):
    tools, backend = setup_tools(bundle)
    adapter = Adapter()
    adapter.actions = [request_call()]
    driver = make_driver(bundle, adapter, object(), tools=tools, tmp_path=tmp_path)
    driver.setup()
    assert driver.request_actions()[0]["accepted"] is True
    driver.request_actions()
    driver.close()
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


def test_p1_runner_idle_wakes_at_camera_deadline_not_fixed_step(bundle, tmp_path):
    tools, _ = setup_tools(
        bundle, contract={"delay_s": 5}, validity_s=20.0, episode_id="cam-test")

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
            return {"time_s": time_s, "runtime_state": deepcopy(initial_state(bundle))}

    backend = Backend()
    adapter = WaitingAdapter()
    result = make_driver(bundle, adapter, backend, tools=tools, tmp_path=tmp_path).run(
        initial_state=initial_state(bundle))
    assert result["error"] is None
    assert backend.ticks == [2]
    assert not any(
        e["payload"].get("kind") == "camera_frame" for e in adapter.seen)
    assert any(
        e["payload"].get("kind") == "camera_request_timeout" for e in adapter.seen)


def test_p1_bandwidth_limited_queue_jumps_windows_within_turn_budget(bundle, tmp_path):
    """~666 B transport / 45 B per window should deliver near t=14, not burn 256 turns."""
    from base64 import b64encode
    import hashlib

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
            return {"time_s": time_s, "runtime_state": deepcopy(initial_state(bundle))}

    backend_idle = Backend()
    adapter = WaitingAdapter()
    result = make_driver(bundle, adapter, backend_idle, tools=tools, tmp_path=tmp_path,
                         episode_id="bw-test").run(initial_state=initial_state(bundle))
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


def test_p1_host_member_join_delivers_profile_delta(bundle, tmp_path):
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
    driver = make_driver(bundle, adapter, object(), tools=tools, tmp_path=tmp_path,
                         episode_id="join-delta")
    driver.setup()
    driver.emit_member_join(hidden, 1.0, {
        "capability_declaration": declaration,
        "capability_version": 1,
    })
    driver.request_actions()
    driver.request_actions()
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
    driver.close()


def test_p1_host_member_join_observation_fallback_without_hook(bundle, tmp_path):
    """Participants without update_camera_tools still get a public profile delta."""
    hidden = "joined_camera_obs"
    catalog = sensor_catalog()
    catalog[hidden] = sensor_catalog(robot=hidden)[hidden]
    tools, _ = setup_tools(bundle, catalog=catalog, episode_id="join-obs")

    class ObsOnlyAdapter(Adapter):
        def act(self, events):
            self.seen.extend(deepcopy(events))
            return []

    adapter = ObsOnlyAdapter()
    assert not hasattr(adapter, "update_camera_tools")
    driver = make_driver(bundle, adapter, object(), tools=tools, tmp_path=tmp_path,
                         episode_id="join-obs")
    driver.setup()
    driver.emit_member_join(hidden, 1.0)
    driver.request_actions()
    driver.close()
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


def test_joined_member_survives_runtime_state_update(bundle, tmp_path):
    """A joined member must not be silently deactivated by a later snapshot.

    The retired GoalSession never removed an activated member from its
    roster on update_runtime_state; the lean session must not either.
    """
    hidden = "joined_camera_persist"
    catalog = sensor_catalog()
    catalog[hidden] = sensor_catalog(robot=hidden)[hidden]
    # Link delay keeps the request in flight across the roster update.
    tools, _ = setup_tools(bundle, catalog=catalog, episode_id="join-persist",
                           contract={"delay_s": 5}, validity_s=10.0)

    class SlowAdapter(Adapter):
        def act(self, events):
            self.seen.extend(deepcopy(events))
            self.calls += 1
            if any(e["type"] == "member_join" for e in events):
                return [request_call(request_id="cam-slow", robot=hidden, deadline_s=10)]
            return []

    driver = make_driver(bundle, SlowAdapter(), object(), tools=tools, tmp_path=tmp_path,
                         episode_id="join-persist")
    driver.setup()
    driver.emit_member_join(hidden, 0.0)
    driver.request_actions()
    assert tools.report()["requests"][0]["robot_id"] == hidden
    # Backend snapshot without the joined member arrives while the request
    # is still in flight.
    driver.session.update_runtime_state(1.0, {ROBOT: "rack_aisle_a"}, {})
    events = driver.request_actions()
    assert not any(e["payload"].get("kind") == "camera_request_cancelled"
                   for e in events)
    assert tools.report()["requests"][0]["terminal"] is False
    driver.close()


def test_runtime_snapshot_does_not_drop_initial_member():
    session = ObservationSession("roster", initial_agents={ROBOT, "warehouse_ground_2"})
    session.update_runtime_state(1.0, {ROBOT: "rack_aisle_a"}, {})
    assert session.report()["current_agents"] == [ROBOT, "warehouse_ground_2"]


def test_session_rejects_duplicate_observation_message_id(bundle):
    session = ObservationSession("dup", initial_agents={"r1"})
    payload = {"message_id": "m-1", "kind": "camera_frame"}
    session.emit("observation", 0.0, payload)
    with pytest.raises(ValueError, match="already been delivered"):
        session.emit("observation", 1.0, payload)


def test_session_rejects_duplicate_nested_camera_message_id():
    session = ObservationSession("nested-dup", initial_agents={"r1"})
    payload = {"kind": "camera_frame", "data": {"message_id": "camera-1"}}
    session.emit("observation", 0.0, payload)
    with pytest.raises(ValueError, match="already been delivered"):
        session.emit("observation", 1.0, payload)


def test_session_tracks_both_camera_message_id_namespaces():
    session = ObservationSession("namespace-dup", initial_agents={"r1"})
    session.emit("observation", 0.0, {
        "message_id": "outer", "data": {"message_id": "inner"}})
    with pytest.raises(ValueError, match="already been delivered"):
        session.emit("observation", 1.0, {"data": {"message_id": "inner"}})
    with pytest.raises(ValueError, match="already been delivered"):
        session.emit("observation", 2.0, {"message_id": "outer"})


def test_session_member_join_requires_increasing_capability_version():
    """Retired GoalSession roster rule: joins strictly increase the version."""
    session = ObservationSession("ver", initial_agents={"r1"})
    session.emit("member_join", 0.0, {"agent_id": "joined", "capability_version": 2})
    with pytest.raises(ValueError, match="capability_version"):
        session.emit("member_join", 1.0, {"agent_id": "joined", "capability_version": 2})
    session.emit("member_join", 2.0, {"agent_id": "joined", "capability_version": 3})


def test_participant_setup_failure_is_archived_not_bare(bundle, tmp_path):
    """initialize() raising must still produce teardown archives and a
    fail-closed result, never a bare exception with a leaked lifecycle."""
    tools, _ = setup_tools(bundle, episode_id="setup-boom")

    class BrokenInit(Adapter):
        def initialize(self, public_task, participant_api):
            raise RuntimeError("participant refused to initialize")

    root = tmp_path / "episode"
    result = ObservationEpisodeDriver(
        bundle["public_task"], BrokenInit(), object(),
        episode_id="setup-boom", output_root=root, camera_tools=tools,
    ).run(initial_state=initial_state(bundle))
    assert "participant refused to initialize" in result["error"]
    assert result["scoreable"] is False
    metadata = json.loads((root / "scoring_adapter_metadata.json").read_text())
    assert metadata["episode_result"]["lifecycle_error_count"] >= 1
    assert "camera_tools_report.json" in metadata["archive_index"]["files"]
    report = json.loads((root / "camera_tools_report.json").read_text())
    assert report["closed"] is True


def test_camera_tools_coexist_on_shared_communication_runtime(bundle, tmp_path):
    """Retired pose-coexistence test, remapped: a second camera toolset sharing
    one CommunicationRuntime meters and delivers independently."""
    shared = CommunicationRuntime()
    tools_a, backend_a = setup_tools(bundle, episode_id="cam-test")
    catalog_b = sensor_catalog(robot="warehouse_ground_2", sensor="camera_rgb_secondary")
    tools_b, backend_b = setup_tools(
        bundle, catalog=catalog_b, episode_id="cam-test-b",
        backend=FakeOnDemandCameraBackend(warmup_frames=0, idle_timeout_s=1.0))
    tools_a.telemetry = GoalCameraTelemetry(shared, message_prefix="cam-test")
    tools_b.telemetry = GoalCameraTelemetry(shared, message_prefix="cam-test-b")

    adapter = Adapter()
    adapter.actions = [request_call(request_id="cam-1")]
    driver = make_driver(bundle, adapter, object(), tools=tools_a, tmp_path=tmp_path)
    driver.setup()
    driver.request_actions()
    driver.request_actions()  # frames land on the next delivery turn
    # The second toolset is driven host-side in the same episode timeline.
    assert tools_b.submit(
        request_call(request_id="cam-b", robot="warehouse_ground_2",
                     sensor="camera_rgb_secondary"),
        time_s=0)["accepted"]
    events_b = tools_b.tick(0)
    frames_b = [row for row in events_b if row.get("kind") == "camera_frame"]
    assert frames_b
    kinds = {e["payload"].get("kind") for e in adapter.seen if e["type"] == "observation"}
    assert "camera_frame" in kinds
    assert backend_a.captures and backend_b.captures
    replay_a = validate_communication_report(tools_a.report()["communication"])
    replay_b = validate_communication_report(tools_b.report()["communication"])
    assert replay_a["validated"] and replay_b["validated"]
    driver.close()
    tools_b.close()


def test_driver_archives_camera_report(bundle, tmp_path):
    """Retired formal-runner archive test, remapped to the episode driver."""
    tools, _ = setup_tools(bundle, contract={"delay_s": 1}, episode_id="cam-test")

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
            return {"time_s": time_s, "runtime_state": deepcopy(initial_state(bundle))}

    result = make_driver(bundle, WaitingAdapter(), Backend(), tools=tools,
                         tmp_path=tmp_path).run(initial_state=initial_state(bundle))
    assert result["error"] is None
    assert result["scoreable"] is False
    archive = json.loads((tmp_path / "episode/camera_tools_report.json").read_text())
    assert archive["execution_validated"] is False
    assert json.loads((tmp_path / "episode/camera_communication_replay.json").read_text())[
        "validated"]


@pytest.mark.parametrize("operation", ["report", "finalize_episode", "close", "close_once"])
def test_tool_teardown_error_preserves_scoring_archive(bundle, tmp_path, monkeypatch, operation):
    tools, _ = setup_tools(bundle, episode_id="cam-test")

    def fail():
        raise RuntimeError(f"{operation} failed during teardown")

    closed = []
    original_close = tools.close

    def close():
        closed.append(True)
        if operation == "close" or (operation == "close_once" and len(closed) == 1):
            fail()
        original_close()

    monkeypatch.setattr(tools, "close", close)
    if operation not in {"close", "close_once"}:
        monkeypatch.setattr(tools, operation, fail)
    root = tmp_path / "episode"
    result = ObservationEpisodeDriver(
        bundle["public_task"], Adapter(), object(),
        episode_id="cam-test", output_root=root, camera_tools=tools,
    ).run(initial_state=initial_state(bundle))
    assert closed
    assert operation in result["error"]
    assert result["scoreable"] is False
    metadata = json.loads((root / "scoring_adapter_metadata.json").read_text())
    assert metadata["episode_result"]["lifecycle_error_count"] >= 1
    assert metadata["episode_result"]["error"] == result["error"]
    assert metadata["scoreable"] is False
    assert "lifecycle_errors.json" in metadata["archive_index"]["files"]
    assert "result.json" in metadata["archive_index"]["files"]
    if operation != "report":
        archive = json.loads((root / "camera_tools_report.json").read_text())
        assert archive["closed"] is (operation != "close")


def test_pending_render_cleanup_is_archived_even_without_raised_close(bundle, tmp_path, monkeypatch):
    tools, backend = setup_tools(bundle, episode_id="cam-test")
    tools.submit(request_call(), time_s=0)
    tools.tick(0)
    assert backend.lifecycle.active_sensor_keys()

    def fail_destroy(_product):
        raise RuntimeError("render product destroy failed")

    monkeypatch.setattr(backend.lifecycle, "_destroy_product", fail_destroy)
    root = tmp_path / "episode"
    result = ObservationEpisodeDriver(
        bundle["public_task"], Adapter(), object(),
        episode_id="cam-test", output_root=root, camera_tools=tools,
    ).run(initial_state=initial_state(bundle))
    assert "pending cleanup" in result["error"]
    failures = json.loads((root / "camera_render_failures.json").read_text())
    assert any("render product destroy failed" in row["error"] for row in failures)
    metadata = json.loads((root / "scoring_adapter_metadata.json").read_text())
    assert metadata["episode_result"]["lifecycle_error_count"] >= 1
    assert "camera_render_failures.json" in metadata["archive_index"]["files"]
    assert metadata["scoreable"] is False
