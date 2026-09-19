"""Deployment host must preserve diagnostic scope and current-run evidence."""

import json

import pytest

from demo.benchmark.goal_navigation_probe import (
    execute_navigation_probe, load_navigation_probe, prepare_navigation_probe,
)


@pytest.fixture
def probe(tmp_path):
    root = tmp_path / "probe"
    prepare_navigation_probe(root)
    return root


def test_probe_is_separate_from_order_scene_binding(probe):
    inputs = load_navigation_probe(probe)
    public = inputs["public_task.json"]
    assert public["task_id"] == "WAREHOUSE-NAV-DEPLOYMENT-PROBE-01"
    assert public["public_world"]["objects"] == []
    assert {r["id"] for r in public["public_world"]["regions"]} == {
        "navigation_probe_start", "navigation_probe_goal"}
    assert inputs["diagnostic_binding.json"]["whole_task_scene_binding_validated"] is False
    assert inputs["diagnostic_binding.json"]["scoreable"] is False
    assert len(inputs["scenario.json"]["robots"]) == 1


@pytest.mark.parametrize("name", ["scenario.json", "author_plan.json", "diagnostic_binding.json"])
def test_swapped_input_cannot_launch_a_backend(probe, monkeypatch, name):
    from demo.benchmark import goal_navigation_probe as module
    called = []
    monkeypatch.setattr(module.subprocess, "Popen", lambda *a, **k: called.append(True))
    path = probe / name
    value = json.loads(path.read_text())
    value["foreign_input"] = "changed target or identity"
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="reviewed source binding"):
        execute_navigation_probe(probe)
    assert not called


def test_an_old_run_cannot_be_reused_as_a_new_receipt(probe, monkeypatch):
    from demo.benchmark import goal_navigation_probe as module
    (probe / "run").mkdir()
    (probe / "run/navigation_report.json").write_text('{"status": "executed"}')
    called = []
    monkeypatch.setattr(module.subprocess, "Popen", lambda *a, **k: called.append(True))
    with pytest.raises(FileExistsError):
        execute_navigation_probe(probe)
    assert not called


def test_backend_failure_keeps_dispatched_task_pending(probe, monkeypatch):
    from demo.benchmark import goal_navigation_probe as module

    class FailedProcess:
        def __init__(self, *args, **kwargs):
            pass

        def wait(self, timeout):
            return 1

    monkeypatch.setattr(module.subprocess, "Popen", FailedProcess)
    result = execute_navigation_probe(probe)
    assert result["process"]["returncode"] == 1
    assert result["completion"]["accepted"] is False
    ledger = json.loads((probe / "run/ledger_after_review.json").read_text())
    assert ledger["tasks"]["probe_navigation"]["status"] == "pending"
    assert ledger["tasks"]["probe_navigation"]["receipt"] is None
    assert result["scoreable"] is False


def test_probe_directory_is_not_overwritten(probe):
    original = (probe / "diagnostic_binding.json").read_bytes()
    with pytest.raises(FileExistsError):
        prepare_navigation_probe(probe)
    assert (probe / "diagnostic_binding.json").read_bytes() == original
