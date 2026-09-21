"""Diagnostic IDs and maintained author audits must bind actual inputs."""

import json

import pytest

from demo.benchmark.diagnostic_audit import ROOT, MAINTAINED_SCENARIOS, check_audit, write_audit
from demo.benchmark.goal_driven_tasks import build_goal_task_package
from EAI.scenario_spec.validation import ScenarioError, validate_scenario


def test_house_goal_bundle_uses_canonical_id_across_layers():
    bundle = build_goal_task_package("modern_house_nvd")
    for key in ("public_task", "participant_api", "hidden_evaluator", "baseline_manifest"):
        assert bundle[key]["task_id"] == "MODERN_HOUSE-GOAL-01"


def test_audit_rejects_changed_input_and_forged_execution(tmp_path):
    source = ROOT / MAINTAINED_SCENARIOS[0]
    path = tmp_path / source.name
    path.write_bytes(source.read_bytes())
    sidecar = write_audit(path)
    check_audit(path)
    data = json.loads(sidecar.read_text())
    data["execution_validated"] = True
    sidecar.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="stale or changed"):
        check_audit(path)
    write_audit(path)
    data = json.loads(path.read_text())
    data["seed"] += 1
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="stale or changed"):
        check_audit(path)


@pytest.mark.parametrize("scene,track", [("warehouse_nvd", "W-DAG-01"),
                                         ("modern_house_nvd", "MH-DAG-01")])
def test_dag_track_matches_scene_and_public_bundle(scene, track):
    import importlib
    package = "warehouse_benchmark" if scene == "warehouse_nvd" else "modern_house_benchmark"
    challenge = importlib.import_module(f"demo.{package}.challenge")
    data = json.loads(challenge.DEFAULT_SCENARIO.read_text())
    assert data["benchmark_task_id"] == track
    assert challenge.compile_challenge()["public_task"]["task_id"] == track
    data["scene_key"] = "factory"
    with pytest.raises(ScenarioError, match="does not match"):
        validate_scenario(data)
