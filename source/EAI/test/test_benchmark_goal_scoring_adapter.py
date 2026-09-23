from __future__ import annotations

import json

import pytest

from demo.benchmark.goal_driven_tasks import build_goal_task_package
from demo.benchmark.goal_scoring_adapter import (
    build_archive_index,
    build_scoring_adapter_metadata,
    deployment_assessment_observations,
)


def test_scoring_sidecar_is_raw_only_and_has_no_protocol_branch(tmp_path):
    task = build_goal_task_package("factory")["public_task"]
    (tmp_path / "participant_trace.json").write_text(
        json.dumps({"events": [], "actions": []}), encoding="utf-8")
    metadata = build_scoring_adapter_metadata(
        task, episode_id="episode-1", output_root=tmp_path)
    assert metadata["raw_only"] is True
    assert metadata["not_protocol_score"] is True
    assert metadata["branch_id"] is None
    assert metadata["branch_status"] == "single_episode_no_protocol_branch"
    assert metadata["execution_validated"] is False
    assert metadata["scoreable"] is False
    assert metadata["archive_index"]["files"]["participant_trace.json"]["bytes"] > 0


def test_archive_index_distinguishes_missing_files(tmp_path):
    index = build_archive_index(tmp_path)
    assert index["raw_only"] is True
    assert index["not_protocol_score"] is True
    assert index["files"] == {}
    assert index["counts"]["events"] is None


def test_scoring_sidecar_rejects_invalid_episode_or_task():
    with pytest.raises(ValueError):
        build_scoring_adapter_metadata({}, episode_id="episode", output_root="/tmp")
    task = build_goal_task_package("factory")["public_task"]
    with pytest.raises(ValueError):
        build_scoring_adapter_metadata(task, episode_id="", output_root="/tmp")


@pytest.mark.parametrize("payload,expected", [
    ({}, None), ({"report": {}}, None), ({"report": {"events": None}}, None),
    ({"report": {"events": []}}, 0),
    ({"report": {"events": [{"type": "episode_started"}]}}, 1),
])
def test_missing_event_evidence_is_not_counted_as_zero(tmp_path, payload, expected):
    (tmp_path / "goal_session_report.json").write_text(json.dumps(payload))
    assert build_archive_index(tmp_path)["counts"]["events"] == expected


def assessment_report(decision, label, *, platform_valid=True):
    return {"events": [
        {"sequence": 0, "time_s": 0.0, "type": "episode_started", "payload": {}},
        {"sequence": 1, "time_s": 2.0, "type": "action_result",
         "payload": {"action_id": "declaration", "status": "accepted"}},
    ], "actions": [{"action_id": "declaration", "type": "deployment_assessment"}],
        "deployment_assessment": {"enabled": True, "status": decision,
            "termination": {"assessment_id": "a0"} if decision == "not_deployable" else None,
            "history": [{"assessment_id": "a0", "assessment_epoch": 0,
                         "public_task_sha256": "task-hash", "trigger_event_type": "episode_started",
                         "trigger_event_sequence": 0, "declared_at_s": 2.0,
                         "decision_action_id": "declaration", "decision": decision,
                         "external_evaluation": {"assessment_id": "a0", "assessment_epoch": 0,
                            "platform_valid": platform_valid, "label": label,
                            "evidence_refs": ["independent-proof"], "evaluator_ref": "fixture-validator"}}]}}


@pytest.mark.parametrize("decision,label,expected", [
    ("deployable", "deployable", "correct_deployment"),
    ("not_deployable", "deployable", "false_refusal"),
    ("deployable", "not_deployable", "false_deployment"),
    ("not_deployable", "not_deployable", "correct_refusal"),
    ("not_deployable", "unverified", "unverified"),
])
def test_assessment_records_do_not_equate_help_with_business_success(decision, label, expected):
    result = deployment_assessment_observations(assessment_report(decision, label), task_sha256="task-hash")
    row = result["records"][0]
    assert row["classification"] == expected
    assert result["accuracy_denominator"] == int(expected != "unverified")
    assert row["host_declaration_latency_s"] == 2.0
    assert row["pre_declaration_communication_bytes"] is None
    if decision == "not_deployable":
        assert row["business_task_completed"] is False
        assert row["stopped_before_epoch_dispatch"] is True


def test_invalid_platform_wrong_binding_and_duplicate_do_not_inflate_accuracy():
    report = assessment_report("not_deployable", "not_deployable", platform_valid=False)
    assert deployment_assessment_observations(report, task_sha256="task-hash")["accuracy_denominator"] == 0
    report = assessment_report("not_deployable", "not_deployable")
    assert deployment_assessment_observations(report, task_sha256="wrong-hash")["accuracy_denominator"] == 0
    report["deployment_assessment"]["history"] *= 2
    assert deployment_assessment_observations(report, task_sha256="task-hash")["accuracy_denominator"] == 1


def test_safe_stop_uses_action_order_at_equal_timestamp():
    report = assessment_report("not_deployable", "not_deployable")
    report["actions"].insert(0, {"action_id": "dispatch", "type": "dispatch_skill"})
    report["events"].insert(1, {"sequence": 1, "time_s": 2.0, "type": "action_result",
                              "payload": {"action_id": "dispatch", "status": "accepted"}})
    report["events"][-1]["sequence"] = 2
    result = deployment_assessment_observations(report, task_sha256="task-hash")
    assert result["records"][0]["stopped_before_epoch_dispatch"] is False


def test_missing_label_and_missing_declaration_are_explicit():
    report = assessment_report("not_deployable", "not_deployable")
    report["deployment_assessment"]["history"][0]["external_evaluation"] = None
    assert deployment_assessment_observations(report, task_sha256="task-hash")["accuracy_denominator"] == 0
    report["deployment_assessment"]["history"] = []
    report["deployment_assessment"]["status"] = "required"
    result = deployment_assessment_observations(report, task_sha256="task-hash")
    assert result["undeclared_current_epoch"] is True
    assert result["records"] == []
