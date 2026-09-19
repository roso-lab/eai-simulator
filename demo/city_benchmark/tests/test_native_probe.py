"""Pure checks for the native City incident microdiagnostic."""
from contextlib import contextmanager, nullcontext
import json
from types import SimpleNamespace

import pytest

from demo.city_benchmark.native_probe import (
    DIAGNOSTIC_REGION_RADIUS_M,
    DIAGNOSTIC_REGION_CENTER_XY,
    _robot_tensor_pose,
    main,
    prepare_probe,
    record_diagnostic_incident,
    SERIAL_LOCK,
    _serial_lock,
    _report_guard,
    _source_hashes,
)


def test_prepare_is_executable_contract_without_isaac(tmp_path):
    result = prepare_probe(tmp_path, device="cpu", seed=17)
    assert result["status"] == "prepared"
    assert result["execution_status"] == "pending"
    assert result["scoreable"] is False
    assert result["scene_key"] == "city"
    assert result["serial_lock"] == str(SERIAL_LOCK)
    assert result["source_hashes"]
    assert {
        "demo/city_benchmark/native_probe.py",
        "demo/city_benchmark/live_witness.py",
        "demo/city_benchmark/episode_adapter.py",
        "demo/city/runtime.py", "demo/city/main.py", "simulator.py",
    } <= {path.removeprefix("/home/pupil/eai-simulator/") for path in result["source_hashes"]}
    assert result["people"] == 1 and result["use_flow"] is False
    saved = json.loads((tmp_path / "native_probe_prepare.json").read_text())
    assert saved["runtime_boundary"].endswith("CityEaiRuntime")
    assert "CityIncidentReceiver.receive" in saved["receiver_boundary"]


def test_native_tensor_pose_is_read_without_string_or_static_fallback():
    class Tensor:
        def detach(self):
            return self

        def cpu(self):
            return self

        def tolist(self):
            return ["-69.0", 49.0, 0.62]

    articulation = SimpleNamespace(data=SimpleNamespace(root_pos_w=[Tensor()]))
    session = SimpleNamespace(base_env=SimpleNamespace(
        scene=SimpleNamespace(articulations={"go2_1": articulation})))
    assert _robot_tensor_pose(session) == (-69.0, 49.0, 0.62)


def test_native_pose_request_receiver_stores_independent_receipt_and_stays_pending():
    pose = (*DIAGNOSTIC_REGION_CENTER_XY, 0.62)
    trace = record_diagnostic_incident(
        episode_id="city-native-test-01", pose=pose, now_s=0.1)
    assert trace["observation"]["entities"][0]["position_xyz"] == list(pose)
    assert trace["admission_trace"]["admitted"] is True
    assert trace["request"]["observation_refs"] == [trace["observation"]["id"]]
    assert trace["receipt"]["status"] == "received"
    assert trace["stored_receipt"] == trace["receipt"]
    assert trace["service_review"]["status"] == "pending"
    assert trace["service_review"]["scoreable"] is False
    assert trace["full_city_service"] == "pending"


def test_native_admission_uses_defined_region_and_fails_closed_outside():
    pose = (DIAGNOSTIC_REGION_CENTER_XY[0] + DIAGNOSTIC_REGION_RADIUS_M + 0.01,
            DIAGNOSTIC_REGION_CENTER_XY[1], 0.62)
    with pytest.raises(RuntimeError, match="outside diagnostic region"):
        record_diagnostic_incident(episode_id="city-native-test-02", pose=pose, now_s=0.1)


def test_cli_prepare_does_not_open_simulator(tmp_path, monkeypatch):
    def fail_if_called(*args, **kwargs):
        raise AssertionError("prepare must not launch open_simulator_session")

    monkeypatch.setattr("simulator.open_simulator_session", fail_if_called)
    assert main(["--prepare", "--output-root", str(tmp_path), "--device", "cpu"]) == 0
    assert (tmp_path / "native_probe_prepare.json").is_file()


def test_prepare_does_not_silently_replace_frozen_inputs(tmp_path):
    prepare_probe(tmp_path, device="cpu", seed=17)
    with pytest.raises(FileExistsError, match="overwrite"):
        prepare_probe(tmp_path, device="cuda:0", seed=17)


def test_source_hashes_include_probe_and_runtime_contract():
    hashes = _source_hashes()
    assert all(isinstance(value, str) and len(value) == 64 for value in hashes.values())
    assert any(path.endswith("demo/city_benchmark/native_probe.py") for path in hashes)
    assert any(path.endswith("simulator.py") for path in hashes)


def test_serial_lock_fails_closed_without_waiting(monkeypatch):
    import demo.city_benchmark.native_probe as probe

    def busy(*args):
        raise BlockingIOError

    monkeypatch.setattr(probe.fcntl, "flock", busy)
    with pytest.raises(RuntimeError, match="canonical City native probe lock is busy"):
        with _serial_lock():
            pass


def test_report_guard_persists_exception_before_outer_context_teardown(tmp_path):
    path = tmp_path / "native_probe_report.json"
    report = {"status": "starting"}
    report_seen_during_teardown = []

    @contextmanager
    def outer_session():
        try:
            yield
        finally:
            report_seen_during_teardown.append(json.loads(path.read_text()))

    with pytest.raises(RuntimeError, match="native failure"):
        with outer_session(), _report_guard(path, report):
            raise RuntimeError("native failure")
    assert report_seen_during_teardown == [{
        "status": "blocked",
        "blocker": "RuntimeError: native failure",
        "full_city_service": "pending",
    }]


def test_execute_refuses_reuse_of_prior_start_without_report(tmp_path, monkeypatch):
    prepare_probe(tmp_path, device="cpu", seed=17)
    (tmp_path / "native_probe_start.json").write_text("{}")
    import demo.city_benchmark.native_probe as probe

    monkeypatch.setattr(probe, "_serial_lock", nullcontext)

    with pytest.raises(FileExistsError, match="launch already exists"):
        probe.execute_probe(tmp_path, device="cpu", seed=17)
