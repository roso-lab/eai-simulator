"""A frozen comparison retains failed methods and rejects input mutation."""

import json
from copy import deepcopy
from pathlib import Path

import pytest

from benchmark_evidence_support import frozen_attempt
from EAI.scenario_spec import load_scenario
from demo.factory_benchmark import run_allocation_comparison as runner


def _setup(tmp_path, monkeypatch):
    scenario_path = Path(__file__).resolve().parents[3] / "demo/factory_benchmark/scenarios/coverage_01.json"
    scenario = load_scenario(scenario_path)
    measurements = tmp_path / "records.json"
    measurements.write_text('{"records": []}')
    methods = ("fixed", "greedy", "optimized")
    comparison = {"input_sha256": "b" * 64, "compiled_scenarios": {method: deepcopy(scenario) for method in methods},
                  "methods": {method: {"status": "feasible"} for method in methods}}
    monkeypatch.setattr(runner, "compare_scenario", lambda *args, **kwargs: comparison)
    return scenario_path, measurements


def test_paired_runner_keeps_completed_nodes_with_failed_freshness(tmp_path, monkeypatch):
    scenario_path, measurements = _setup(tmp_path, monkeypatch)
    calls = []

    def execute(scenario, report_path, **kwargs):
        request = json.loads(kwargs["request_path"].read_text())
        attempt = frozen_attempt(tmp_path, scenario_sha256=request["scenario_sha256"], options=request["options"])
        report = attempt["report_body"]
        report["execution_provenance"]["request"] = request
        failed = scenario.name.startswith("optimized")
        report["execution_validation"]["validated"] = not failed
        report.update(steps_taken=100, camera_window_validation={"validated": True},
                      factory_metrics={"scheduling": {"completed_nodes": ["arrive", "watch"], "terminal_time_s": 10.0},
                                       "coverage": {"validated": not failed, "max_gap_s": 55.86 if failed else 1.26}})
        report_path.write_text(json.dumps(report))
        calls.append(request["options"])
        return {"returncode": 0, "duration_s": 1.0}, report

    monkeypatch.setattr(runner, "run_executor", execute)
    output_dir = tmp_path / "comparison"
    results = runner.run_comparison(scenario_path, measurements, output_dir)
    assert [result["status"] for result in results] == ["passed", "passed", "failed"]
    assert "coverage_freshness_failed" in results[-1]["violations"]
    assert results[-1]["completed_nodes"] == ["arrive", "watch"]
    assert calls[0] == calls[1] == calls[2]
    assert json.loads((output_dir / "results.json").read_text()) == results
    assert json.loads((output_dir / "optimized.attempt.json").read_text())["status"] == "failed"
    with pytest.raises(FileExistsError):
        runner.run_comparison(scenario_path, measurements, output_dir)


def test_paired_runner_stops_on_request_mutation_and_keeps_finished_attempt(tmp_path, monkeypatch):
    scenario_path, measurements = _setup(tmp_path, monkeypatch)
    output_dir = tmp_path / "comparison"

    def execute(scenario, report_path, **kwargs):
        path = output_dir / "greedy.request.json"
        request = json.loads(path.read_text())
        request["options"]["render_period_s"] = 1.0
        path.write_text(json.dumps(request))
        return {"returncode": "timeout", "duration_s": 1.0}, None

    monkeypatch.setattr(runner, "run_executor", execute)
    with pytest.raises(ValueError, match="request changed"):
        runner.run_comparison(scenario_path, measurements, output_dir)
    assert (output_dir / "fixed.attempt.json").exists()
    assert json.loads((output_dir / "results.json").read_text())[0]["status"] == "failed"
    assert not (output_dir / "greedy.attempt.json").exists()
