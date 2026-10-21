"""Regression tests for the intentionally unavailable scenario backend."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Establish the project-local source boundary before importing the EAI contract.
# The shared IsaacLab environment may expose an editable legacy EAI package;
# importing it during pytest collection would poison the process and make the
# later fail-closed runtime checks indistinguishable from a real project mix.
from tools.project_runtime import activate_project_runtime

activate_project_runtime(enforce_cwd=False)

from EAI.scenario_spec.benchmark_physics import BENCHMARK_PHYSICS_CONTRACT
from tools.scenario_generation import (
    batch_execute,
    benchmark_protocol,
    execute_episode,
    project_provenance,
    protocol_scope,
    runtime_evidence,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _current_mass_audit() -> dict:
    return {
        "validated": True,
        "contract": dict(BENCHMARK_PHYSICS_CONTRACT),
        "violations": [],
        "arms": {"/World/robot/arm": {}},
    }


def _self_declared_attempt() -> dict:
    return {
        "attempted_at": "2026-10-20T00:00:00+00:00",
        "status": "completed",
        "scenario_sha256": "a" * 64,
        "outcome": {"returncode": 0},
        "report_body": {
            "benchmark_mass_audit": _current_mass_audit(),
            "execution_validation": {
                "validated": True,
                "tier": "navigation_execution_validated",
            },
            "runtime": {"complete": True},
            "files": {},
        },
        "request": {"sha256": "b" * 64},
        "runtime": {"sha256": "c" * 64, "complete": True},
        "files": {},
    }


def test_batch_helpers_import_without_legacy_demo_and_preflight_fails_closed():
    for module in (batch_execute, benchmark_protocol, execute_episode, project_provenance, protocol_scope):
        assert Path(module.__file__).resolve().is_relative_to(PROJECT_ROOT)
    assert "demo" not in sys.modules

    contract, reason = batch_execute.preflight_executor(
        {"phase_graph": {"nodes": []}}, transport_mode="none"
    )
    assert contract is None
    assert reason == execute_episode.BACKEND_ERROR


def test_execution_contract_rejects_before_any_scenario_launch():
    with pytest.raises(ValueError, match="scenario execution backend is unavailable"):
        execute_episode.execution_contract({}, transport_mode="none")


def test_direct_run_executor_preserves_existing_report_and_never_starts_subprocess(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    report_path = tmp_path / "existing.report.json"
    report_path.write_text('{"preserve": true}\n', encoding="utf-8")
    subprocess_called = False

    def forbidden_subprocess(*args, **kwargs):
        nonlocal subprocess_called
        subprocess_called = True
        raise AssertionError("subprocess must not start without a scenario backend")

    monkeypatch.setattr(batch_execute.subprocess, "run", forbidden_subprocess)
    with pytest.raises(ValueError, match="scenario execution backend is unavailable"):
        batch_execute.run_executor(
            tmp_path / "scenario.json",
            report_path,
            steps=1,
            timeout_s=1.0,
            device="cpu",
            transport_mode="none",
            request_path=tmp_path / "request.json",
        )

    assert subprocess_called is False
    assert report_path.read_text(encoding="utf-8") == '{"preserve": true}\n'


@pytest.mark.parametrize(
    "attempt",
    [
        {},
        {"schema": "benchmark_execution_v1", "sha256": "a" * 64},
        _self_declared_attempt(),
    ],
)
def test_self_declared_or_legacy_provenance_can_never_authenticate(attempt: dict):
    with pytest.raises(ValueError, match="provenance is unavailable"):
        project_provenance.validate_execution_provenance(
            attempt, check_runtime_files=True
        )


def test_self_declared_completed_attempt_never_earns_or_reuses_a_tier():
    attempt = _self_declared_attempt()
    assert batch_execute.validated_attempt_tier(attempt) is None
    assert not batch_execute.should_skip_matching_success(
        attempt,
        scenario_sha256="a" * 64,
        transport_mode="none",
        protocol_context=None,
    )


def test_runtime_evidence_importer_cannot_promote_self_declared_attempt():
    attempt = {
        **_self_declared_attempt(),
        "robot_dossier_snapshot_sha256": "d" * 64,
        "report_sha256": "e" * 64,
    }
    with pytest.raises(ValueError, match="provenance is unavailable"):
        runtime_evidence.import_execution_attempt(
            attempt,
            scenario_sha256="a" * 64,
            robot_dossier_snapshot_sha256="d" * 64,
        )


def test_execution_code_provenance_is_entirely_project_local():
    provenance = project_provenance.execution_code_provenance()
    assert provenance
    for relative, digest in provenance.items():
        assert not Path(relative).is_absolute()
        assert "demo/" not in relative
        assert "eai-simulator" not in relative
        assert (PROJECT_ROOT / relative).is_file()
        assert len(digest) == 64
        assert set(digest) <= set("0123456789abcdef")


def test_capture_request_rejects_scenario_outside_project(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    isolated_project = tmp_path / "isolated-project"
    isolated_project.mkdir()
    scenario = tmp_path / "external-scenario.json"
    scenario.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(project_provenance, "REPO_ROOT", isolated_project)
    with pytest.raises(ValueError, match="inside the current project"):
        project_provenance.capture_request(
            scenario, project_provenance.runtime_options()
        )
