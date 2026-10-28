"""Failure-path host tests; subprocess doubles are not algorithm/physics evidence."""
import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

from tools.algorithm_adapters import isaac_pick_place as host


def setup_host(tmp_path, monkeypatch):
    task = tmp_path / "task.json"
    task.write_text(json.dumps(host.case_document()))
    config = tmp_path / "config.json"
    config.write_text("{}")
    output = tmp_path / "evidence"
    monkeypatch.setattr(sys, "argv", ["isaac_pick_place", "--algorithm", "fmc_ata",
        "--algorithm-python", sys.executable, "--task", str(task), "--config", str(config),
        "--output", str(output)])
    return task, config, output


def receipt(assignments=None):
    return {"status": "native_decision_returned", "provenance": {"raw": "retained"},
        "result": {"condition": {"algorithm_id": "test", "source_reference": "test",
            "implementation": "protocol_fixture", "execution_scope": "test", "planner": "test"},
            "assignments": assignments or [], "unassigned_tasks": ["move_carton"] if not assignments else []}}


@pytest.mark.parametrize("mode", ["error", "idle", "timeout", "wrong_robot", "changed_input"])
def test_rejected_decision_never_launches_physics_and_preserves_evidence(tmp_path, monkeypatch, mode):
    task_path, _, output = setup_host(tmp_path, monkeypatch)
    if mode == "error":
        native = {"status": "not_passed", "error": "original solver failed",
                  "native_source_provenance": {"commit": "test-source"}}
    elif mode == "wrong_robot":
        native = receipt([{"task_id": "move_carton", "robot_id": "unknown", "source": "native"}])
    elif mode == "changed_input":
        native = receipt([{"task_id": "move_carton", "robot_id": "ur5_1", "source": "native"}])
    else:
        native = receipt()

    def run(command, **kwargs):
        assert kwargs["timeout"] == 120.0
        if mode == "timeout":
            raise subprocess.TimeoutExpired(command, 120, output=b"partial native output\n")
        if mode == "changed_input":
            task_path.write_text(task_path.read_text() + "\n")
        (output / "native_decision.json").write_text(json.dumps(native))
        return SimpleNamespace(returncode=int(mode == "error"), stdout="native log", stderr="")

    monkeypatch.setattr(host.subprocess, "run", run)
    with pytest.raises(SystemExit) as caught:
        host.main()
    assert caught.value.code == 1
    result = json.loads((output / "integration.json").read_text())
    assert result["passed"] is False
    assert result["physical_execution_performed"] is False
    assert result["physical_execution_completed"] is False
    assert result["physical_receipt_sha256"] is None
    assert result["input_files_unchanged"] is (mode != "changed_input")
    assert result["task_sha256"] and result["adapter_config_sha256"]
    assert result["native_status"] == ("native_timeout" if mode == "timeout" else native["status"])
    if mode == "error":
        assert result["native_provenance"] == native["native_source_provenance"]
    if mode == "timeout":
        assert (output / "native_process.log").read_text() == "partial native output\n"
    else:
        assert json.loads((output / "native_decision.json").read_text()) == native


def test_existing_evidence_directory_is_never_overwritten(tmp_path, monkeypatch):
    _, _, output = setup_host(tmp_path, monkeypatch)
    output.mkdir()
    old = output / "integration.json"
    old.write_text('{"previous": true}')
    with pytest.raises(SystemExit) as caught:
        host.main()
    assert caught.value.code == 2
    assert old.read_text() == '{"previous": true}'
