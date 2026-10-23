"""Measured-route evidence cannot be replaced by endpoint or method claims."""
from copy import deepcopy
import json
import pytest
from tools.validation.project_local_node_route import CONTRACT, evaluate, persist_failure


def trajectory():
    return [{"position_m": list(point), "speed_m_s": 0., "step": i,
             "simulation_time_s": i * .02}
            for i, point in enumerate(CONTRACT["points_m"])]


def test_endpoint_without_required_corner_is_not_task_success():
    samples = trajectory()
    assert evaluate(samples)["passed"]
    assert not evaluate([samples[0], samples[-1]])["passed"]


def test_completed_method_claim_does_not_override_measured_motion():
    samples = trajectory()
    samples[-1].update(speed_m_s=1., method_status="completed")
    assert not evaluate(samples)["passed"]


def test_repeated_physics_timestamp_invalidates_trace():
    samples = deepcopy(trajectory())
    samples[-1]["simulation_time_s"] = samples[-2]["simulation_time_s"]
    result = evaluate(samples)
    assert not result["passed"]
    assert not result["clock_strictly_increasing"]


def test_failure_receipt_exists_before_native_shutdown(tmp_path):
    class NativeSession:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            receipt = json.loads((tmp_path / "result.json").read_text())
            assert receipt["status"] == "failed"
            assert receipt["error"] == "controller rejected"
            assert not receipt["passed"]

    with pytest.raises(ValueError, match="controller rejected"):
        with NativeSession(), persist_failure(tmp_path, {}):
            raise ValueError("controller rejected")


@pytest.mark.parametrize("status,passed,closed,exit_code,expected", [
    ("failed", False, False, 0, False), ("starting", False, False, 0, False),
    ("completed", True, True, 1, False), ("completed", True, False, 0, False),
    ("completed", True, True, 0, True), ("completed", "true", True, 0, False),
])
def test_native_supervisor_requires_receipt_and_clean_session_close(tmp_path, status, passed, closed, exit_code, expected):
    from tools.validation.run_project_probe import assess_receipt
    path = tmp_path / "result.json"
    path.write_text(json.dumps({"status": status, "passed": passed, "session_close_observed": closed}))
    assert assess_receipt(path, exit_code)["passed"] is expected


def test_native_supervisor_rejects_completed_receipt_without_session_close(tmp_path):
    from tools.validation.run_project_probe import assess_receipt
    path = tmp_path / "result.json"
    path.write_text(json.dumps({"status": "completed", "passed": True}))
    result = assess_receipt(path, 0)
    assert result["passed"] is False
    assert result["reason"] == "session_close_not_observed"


def test_native_supervisor_accepts_project_local_close_marker(tmp_path):
    from tools.validation.run_project_probe import assess_receipt
    result_path = tmp_path / "result.json"
    marker_path = tmp_path / "session_close.json"
    result_path.write_text(json.dumps({"status": "completed", "passed": True}))
    marker_path.write_text(json.dumps({
        "schema": "heterocoord.session_close.v1",
        "status": "app_close_requested",
        "cleanup_callbacks_complete": True,
        "app_close_requested": True,
        "app_close_returned": False,
    }))
    result = assess_receipt(result_path, 0, marker_path)
    assert result["passed"] is True
    assert result["session_close_observed"] is True


def test_native_supervisor_rejects_absent_receipt(tmp_path):
    from tools.validation.run_project_probe import assess_receipt
    assert not assess_receipt(tmp_path / "missing.json", 0)["passed"]
