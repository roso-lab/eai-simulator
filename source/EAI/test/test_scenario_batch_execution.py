"""Unit tests for durable execution evidence, without Isaac subprocesses."""

import json
from pathlib import Path
from benchmark_evidence_support import frozen_attempt

from tools.scenario_generation.batch_execute import (
    append_execution_result,
    execution_code_provenance,
    _record_attempt,
    load_protocol_selection,
    preflight_executor,
    resolve_transport_mode,
    should_skip_matching_success,
    validated_tier,
)


def _result(tier, valid=True):
    return {
        "status": "completed" if valid else "failed",
        "report_body": {
            "execution_validation": {"tier": tier, "validated": valid},
        },
    }


def test_validated_tier_only_accepts_explicit_execution_evidence() -> None:
    assert validated_tier(_result("navigation_execution_validated")["report_body"]) == "navigation_execution_validated"
    assert validated_tier(_result("kinematic_execution_validated")["report_body"]) == "kinematic_execution_validated"
    assert validated_tier(_result("oracle_validated")["report_body"]) is None
    assert validated_tier(_result("navigation_execution_validated", valid=False)["report_body"]) is None


def test_execution_provenance_covers_shared_demo_runtime():
    provenance = execution_code_provenance()
    assert all(len(provenance[key]) == 64 for key in (
        "executor_sha256", "batch_executor_sha256", "demo_runtime_sha256",
        "demo_control_sha256", "demo_scheduling_sha256"))


def test_execution_history_preserves_failures_and_only_labels_validated_attempts(tmp_path) -> None:
    record = {"id": "episode_1", "labels": ["spawn_validated"]}
    assert append_execution_result(record, _result("navigation_execution_validated", valid=False)) is False
    assert record["labels"] == ["spawn_validated"]
    assert append_execution_result(record, frozen_attempt(tmp_path)) is True
    assert record["labels"] == ["spawn_validated", "navigation_execution_validated"]
    assert len(record["execution_history"]) == 2
    assert record["execution"] == record["execution_history"][-1]


def test_recorded_attempt_carries_dossier_and_policy_provenance(tmp_path: Path) -> None:
    attempt = _record_attempt(
        instance_id="episode_1", scenario_sha256="a" * 64,
        robot_dossier_snapshot_sha256="b" * 64, payload_policy_hash="c" * 64,
        report_path=tmp_path / "report.json", transport_mode="none", steps=10,
        device="cpu", timeout_s=30.0,
        outcome={"returncode": 0},
        report={"execution_validation": {
            "tier": "navigation_execution_validated", "validated": True}},
    )
    assert attempt["robot_dossier_snapshot_sha256"] == "b" * 64
    assert attempt["payload_policy_hash"] == "c" * 64


def test_protocol_selection_is_split_scoped_and_preserves_frozen_hashes(tmp_path: Path) -> None:
    protocol = {
        "schema": 2,
        "frozen_at_utc": "2026-09-11T10:00:00+00:00",
        "split": {
            "development_groups": [{"id": "dev"}],
            "test_groups": [{"id": "test"}],
        },
        "instances": [
            {"id": "dev_episode", "group_id": "dev", "scenario_sha256": "a" * 64},
            {"id": "test_episode", "group_id": "test", "scenario_sha256": "b" * 64},
        ],
    }
    path = tmp_path / "protocol.json"
    path.write_text(json.dumps(protocol), encoding="utf-8")

    selection, context = load_protocol_selection(path, "development")
    assert selection == {"dev_episode": "a" * 64}
    assert context["split"] == "development"
    assert context["frozen_at_utc"] == "2026-09-11T10:00:00+00:00"


def test_preflight_marks_unsupported_dag_as_harness_limit_not_runtime_failure() -> None:
    contract, reason = preflight_executor(
        {"phase_graph": {"nodes": [{"id": "handoff", "skill_key": "handoff_object", "preferred_agent": "a"}]}},
        transport_mode="kinematic",
    )
    assert contract is None
    assert "does not support phase skills" in reason


def test_test_split_does_not_skip_success_that_predates_protocol_freeze(tmp_path) -> None:
    attempt = frozen_attempt(tmp_path)
    attempt["attempted_at"] = "2026-09-10T10:00:00+00:00"
    attempt["config"]["request"]["captured_at"] = attempt["attempted_at"]
    attempt["report_body"]["execution_provenance"]["runtime"]["captured_at"] = attempt["attempted_at"]
    from demo.benchmark.provenance import canonical_sha256
    attempt["report_sha256"] = canonical_sha256(attempt["report_body"])
    context = {"split": "test", "frozen_at_utc": "2026-09-11T10:00:00+00:00",
               "execution_sources": attempt["config"]["code_provenance"]}
    assert not should_skip_matching_success(
        attempt, scenario_sha256="a" * 64, transport_mode="none", protocol_context=context
    )
    assert should_skip_matching_success(
        attempt, scenario_sha256="a" * 64, transport_mode="none",
        protocol_context={**context, "split": "development"},
    )


def test_auto_transport_mode_is_explicitly_kinematic_only_when_transport_is_declared() -> None:
    assert resolve_transport_mode({"phase_graph": {"nodes": [{"skill_key": "navigate"}]}}, "auto") == "none"
    assert resolve_transport_mode(
        {"phase_graph": {"nodes": [{"skill_key": "navigate"}, {"skill_key": "transport_object"}]}}, "auto"
    ) == "kinematic"
    assert resolve_transport_mode(
        {"phase_graph": {"nodes": [{"skill_key": "joint_transport"}]}}, "auto"
    ) == "kinematic"


def test_execution_code_provenance_has_content_hashes() -> None:
    provenance = execution_code_provenance()
    assert set(provenance) >= {
        "executor_sha256", "batch_executor_sha256", "demo_runtime_sha256",
        "demo_control_sha256", "demo_scheduling_sha256",
    }
    assert all(len(value) == 64 for value in provenance.values())


def test_executor_retains_full_logs_for_a_missing_report(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from tools.scenario_generation import batch_execute

    stdout = "initial diagnostic\n" + "later output\n" * 100
    monkeypatch.setattr(batch_execute.subprocess, "run", lambda *args, **kwargs:
                        SimpleNamespace(returncode=0, stdout=stdout, stderr="original error\n"))
    outcome, report = batch_execute.run_executor(
        tmp_path / "scenario.json", tmp_path / "report.json", steps=100, timeout_s=30,
        device="cpu", transport_mode="none", request_path=tmp_path / "request.json")
    assert report is None
    assert Path(outcome["logs"]["stdout"]["path"]).read_text() == stdout
    assert Path(outcome["logs"]["stderr"]["path"]).read_text() == "original error\n"
    assert len(outcome["logs"]["stdout"]["sha256"]) == 64
