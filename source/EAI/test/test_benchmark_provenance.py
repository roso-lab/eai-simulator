"""Regression tests for source, timing and report evidence misattribution."""

from copy import deepcopy

import pytest

from benchmark_evidence_support import frozen_attempt
from demo.benchmark.provenance import (
    canonical_sha256, capture_request, execution_code_provenance, finish_provenance,
    runtime_options, validate_execution_provenance,
)
from tools.scenario_generation.batch_execute import _record_attempt, run_executor, should_skip_matching_success


def test_sources_cover_observations_allocation_and_evaluators():
    sources = execution_code_provenance()
    for path in ("demo/benchmark/observations.py", "demo/benchmark/allocation.py",
                 "demo/factory_benchmark/evaluation.py", "demo/factory_benchmark/tasks.py",
                 "source/EAI_hmrs/EAI_hmrs/env_builder.py", "simulator.py"):
        assert len(sources[path]) == 64


def test_matching_frozen_attempt_has_verifiable_runtime_files(tmp_path):
    attempt = frozen_attempt(tmp_path)
    assert validate_execution_provenance(attempt, check_runtime_files=True)["validated"]


def test_loaded_voxel_product_is_bound_to_runtime_and_stale_load_fails(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from EAI_assets import asset_resolver
    from tools.assets import usd_closure
    from demo.benchmark.provenance import capture_runtime, changed_runtime_files, file_sha256

    usd_path = tmp_path / "scene.usda"
    usd_path.write_text("#usda 1.0")
    map_path = tmp_path / "map.yaml"
    map_path.write_text("image: map.png\n")
    (tmp_path / "map.png").write_bytes(b"map fixture")
    voxel_path = tmp_path / "voxels.npz"
    voxel_path.write_bytes(b"frozen product fixture")
    digest = file_sha256(voxel_path)
    monkeypatch.setattr(asset_resolver, "collect_usd_asset_paths", lambda cfg: [str(usd_path)])
    monkeypatch.setattr(asset_resolver, "collect_controller_asset_paths", lambda cfg: [])
    monkeypatch.setattr(usd_closure, "collect_closure", lambda roots: ({usd_path}, [], []))
    session = SimpleNamespace(device="cpu", base_env=SimpleNamespace(step_dt=.02),
                              env_cfg=SimpleNamespace(sim=SimpleNamespace(dt=.01, render_interval=2), decimation=2))
    occupancy = SimpleNamespace(path=map_path, sha256="a" * 64)
    snapshot = capture_runtime(session, occupancy, [], input_file_hashes={str(voxel_path): digest})
    assert snapshot["files"][str(voxel_path)] == digest
    assert snapshot["complete"]
    voxel_path.write_bytes(b"changed product")
    assert str(voxel_path) in changed_runtime_files(snapshot)
    stale = capture_runtime(session, occupancy, [], input_file_hashes={str(voxel_path): digest})
    assert not stale["complete"]
    assert any(item.get("reason") == "file_changed_between_load_and_capture"
               for item in stale["missing_dependencies"])


@pytest.mark.parametrize("mutation", ["legacy", "frame", "rendering", "request", "runtime", "sources"])
def test_corrupted_or_mismatched_evidence_is_rejected(tmp_path, mutation):
    attempt = deepcopy(frozen_attempt(tmp_path))
    report = attempt["report_body"]
    if mutation == "legacy":
        report.pop("execution_provenance")
    elif mutation == "frame":
        report["observation_store"] = {"invented": True}
    elif mutation == "rendering":
        report["rendering"]["actual_period_s"] = 0.2
        attempt["report_sha256"] = canonical_sha256(report)
    elif mutation == "request":
        attempt["config"]["request"] = {"sha256": "b" * 64}
    elif mutation == "runtime":
        report["execution_provenance"]["runtime"]["complete"] = False
    else:
        attempt["config"]["code_provenance"] = {"wrong": "c" * 64}
    with pytest.raises(ValueError, match="provenance|digest"):
        validate_execution_provenance(attempt)


def test_render_conditions_cannot_reuse_default_success(tmp_path):
    attempt = frozen_attempt(tmp_path)
    assert not should_skip_matching_success(
        attempt, scenario_sha256="a" * 64, transport_mode="none", protocol_context=None,
        requested_options=runtime_options(render_period_s=0.2))
    assert should_skip_matching_success(
        attempt, scenario_sha256="a" * 64, transport_mode="none", protocol_context=None,
        requested_options=runtime_options())


def test_changed_runtime_asset_cannot_promote_or_skip(tmp_path):
    attempt = frozen_attempt(tmp_path)
    (tmp_path / "evidence_asset.usda").write_text("changed material or collision geometry")
    with pytest.raises(ValueError, match="current installation"):
        validate_execution_provenance(attempt, check_runtime_files=True)
    assert not should_skip_matching_success(
        attempt, scenario_sha256="a" * 64, transport_mode="none", protocol_context=None)


def test_source_change_is_excluded_unless_explicitly_summarizing_its_frozen_protocol(tmp_path):
    attempt = frozen_attempt(tmp_path)
    frozen_sources = deepcopy(attempt["config"]["code_provenance"])
    frozen_sources["demo/factory_benchmark/evaluation.py"] = "b" * 64
    with pytest.raises(ValueError, match="source version"):
        validate_execution_provenance(attempt, expected_sources=frozen_sources)


def test_record_attempt_never_backfills_missing_prelaunch_provenance(tmp_path):
    attempt = _record_attempt(
        instance_id="old", scenario_sha256="a" * 64, report_path=tmp_path / "report.json",
        transport_mode="none", steps=100, device="cpu", timeout_s=30,
        outcome={"returncode": 0}, report={"execution_validation": {
            "tier": "navigation_execution_validated", "validated": True}},
    )
    assert attempt["status"] == "failed"
    assert attempt["config"]["request"] is None
    assert attempt["config"]["code_provenance"] is None


def test_request_is_captured_before_source_or_scenario_change(tmp_path):
    scenario = tmp_path / "scenario.json"
    scenario.write_text('{"seed": 1}', encoding="utf-8")
    request = capture_request(scenario, runtime_options())
    fixture = frozen_attempt(tmp_path)
    runtime = fixture["report_body"]["execution_provenance"]["runtime"]
    scenario.write_text('{"seed": 2}', encoding="utf-8")
    provenance = finish_provenance(request, runtime, scenario_path=scenario)
    assert not provenance["validated"]
    assert "scenario_changed_during_execution" in provenance["violations"]


def test_nonzero_exit_cannot_skip_even_with_valid_report(tmp_path):
    attempt = frozen_attempt(tmp_path)
    attempt["status"] = "failed"
    attempt["outcome"]["returncode"] = 1
    assert not should_skip_matching_success(
        attempt, scenario_sha256="a" * 64, transport_mode="none", protocol_context=None)


@pytest.mark.parametrize("timestamp", [None, "invalid", "2026-01-01T00:00:00", "2099-01-01T00:00:00Z"])
def test_invalid_or_postrun_snapshot_time_is_rejected(tmp_path, timestamp):
    attempt = frozen_attempt(tmp_path)
    attempt["report_body"]["execution_provenance"]["runtime"]["captured_at"] = timestamp
    attempt["report_sha256"] = canonical_sha256(attempt["report_body"])
    with pytest.raises(ValueError):
        validate_execution_provenance(attempt)


def test_batch_passes_frozen_request_and_explicit_conditions_to_child(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from tools.scenario_generation import batch_execute

    scenario = tmp_path / "scenario.json"
    request = tmp_path / "request.json"
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=1, stderr="", stdout="bounded failure")

    monkeypatch.setattr(batch_execute.subprocess, "run", run)
    outcome, report = run_executor(
        scenario, tmp_path / "report.json", steps=123, timeout_s=900, device="cpu",
        transport_mode="none", request_path=request, render_period_s=0.2, wall_timeout_s=600)
    command, settings = calls[0]
    assert command[command.index("--execution-request") + 1] == str(request)
    assert command[command.index("--render-period-s") + 1] == "0.2"
    assert command[command.index("--wall-timeout-s") + 1] == "600"
    assert settings["timeout"] == 900
    assert outcome["returncode"] == 1 and report is None


def test_batch_forwards_explicit_camera_archive_option(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from tools.scenario_generation import batch_execute

    captured = []

    def run(command, **kwargs):
        captured.append(command)
        return SimpleNamespace(returncode=1, stderr="", stdout="test")

    monkeypatch.setattr(batch_execute.subprocess, "run", run)
    run_executor(tmp_path / "scenario.json", tmp_path / "report.json", steps=100, timeout_s=30,
                 device="cpu", transport_mode="none", request_path=tmp_path / "request.json",
                 archive_camera_frames=True)
    assert "--archive-camera-frames" in captured[0]
    assert runtime_options(archive_camera_frames=True)["archive_camera_frames"] is True
