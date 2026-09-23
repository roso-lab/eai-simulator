from __future__ import annotations

import json

import pytest

from demo.benchmark.goal_driven_tasks import build_goal_task_package
from demo.benchmark.goal_scoring_adapter import (
    build_archive_index,
    build_scoring_adapter_metadata,
    communication_analysis_observations,
    deployment_assessment_observations,
    plan_admission_observations,
)
from demo.benchmark.goal_session import classify_plan_rejection


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
    assert metadata["cost_evidence"]["raw_only"] is True
    assert metadata["cost_evidence"]["validity"]["scoreable"] is False
    assert metadata["cost_evidence"]["communication"]["total_bytes"] is None


def test_archive_index_distinguishes_missing_files(tmp_path):
    index = build_archive_index(tmp_path)
    assert index["raw_only"] is True
    assert index["not_protocol_score"] is True
    assert index["files"] == {}
    assert index["counts"]["events"] is None


def test_communication_analysis_is_explicitly_missing_until_archived(tmp_path):
    observed = communication_analysis_observations(tmp_path)
    assert observed["status"] == "missing"
    assert observed["report"] is None
    assert observed["report_sha256"] is None
    metadata = build_scoring_adapter_metadata(
        build_goal_task_package("factory")["public_task"],
        episode_id="episode-comm-missing", output_root=tmp_path)
    assert metadata["communication_analysis"]["status"] == "missing"
    assert "communication_analysis.json" not in metadata["archive_index"]["files"]


def test_communication_analysis_archive_is_bound_without_becoming_a_score(tmp_path):
    report = {
        "schema": "eai.benchmark.communication_analysis", "version": 1,
        "status": "ok", "reference": {"capacity_bps": None, "mean": 1.0},
        "points": [], "thresholds": {}, "no_extrapolation": True,
    }
    path = tmp_path / "communication_analysis.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    observed = communication_analysis_observations(tmp_path)
    assert observed["status"] == "observed"
    assert observed["report"]["no_extrapolation"] is True
    assert len(observed["report_sha256"]) == 64
    metadata = build_scoring_adapter_metadata(
        build_goal_task_package("factory")["public_task"],
        episode_id="episode-comm-observed", output_root=tmp_path)
    assert metadata["communication_analysis"]["status"] == "observed"
    assert metadata["scoreable"] is False
    assert metadata["execution_validated"] is False


def test_communication_analysis_archive_fails_closed_on_wrong_schema(tmp_path):
    (tmp_path / "communication_analysis.json").write_text(
        json.dumps({"schema": "other", "version": 1, "status": "ok"}),
        encoding="utf-8")
    observed = communication_analysis_observations(tmp_path)
    assert observed["status"] == "invalid"
    assert observed["report"] is None
    assert observed["scoreable"] is False


def test_scoring_sidecar_rejects_invalid_episode_or_task():
    with pytest.raises(ValueError):
        build_scoring_adapter_metadata({}, episode_id="episode", output_root="/tmp")
    task = build_goal_task_package("factory")["public_task"]
    with pytest.raises(ValueError):
        build_scoring_adapter_metadata(task, episode_id="", output_root="/tmp")


def test_plan_admission_observations_aggregate_rejections_without_fixed_subtasks():
    report = {
        "actions": [
            {"action_id": "p0", "type": "submit_plan"},
            {"action_id": "r1", "type": "revise_plan"},
        ],
        "action_rejections": [
            {"action_id": "r2", "type": "revise_plan",
             "reason": "pending runtime resource conflict: arm grasp"},
            {"action_id": "r3", "type": "submit_plan",
             "reason": "task capability is unavailable"},
            {"action_id": "r4", "type": "revise_plan",
             "reason": "episode simulation budget has ended"},
        ],
    }
    observed = plan_admission_observations(report)
    assert observed["plan_attempt_count"] == 5
    assert observed["rejected_plan_attempt_count"] == 3
    assert observed["plan_rejection_rate"] == pytest.approx(3 / 5)
    assert observed["rejection_counts_by_category"] == {
        "capability": 1, "load": 0, "resource_conflict": 1,
        "budget": 1, "contract_or_other": 0,
    }
    assert observed["resource_conflict_rate"] == pytest.approx(1 / 5)
    assert observed["revision_survival_rate"] == pytest.approx(1 / 3)
    assert observed["not_protocol_score"] is True


@pytest.mark.parametrize("reason,code", [
    ("pending runtime resource conflict: arm grasp", "resource_conflict"),
    ("episode simulation budget has ended", "budget_exhausted"),
    ("task capability is unavailable", "capability_or_team"),
    ("cannot deselect a busy agent", "load_conflict"),
    ("plan snapshot is stale", "missing_evidence"),
])
def test_plan_rejection_codes_are_stable_across_reason_wording(reason, code):
    assert classify_plan_rejection(reason) == code


def test_plan_admission_prefers_stable_rejection_code_over_localized_text():
    observed = plan_admission_observations({
        "actions": [{"action_id": "p0", "type": "submit_plan"}],
        "action_rejections": [{"action_id": "r0", "type": "submit_plan",
                                "reason": "任意本地化文本", "reason_code": "resource_conflict"}],
    })
    assert observed["rejection_counts_by_category"]["resource_conflict"] == 1


@pytest.mark.parametrize("payload,expected", [
    ({}, None), ({"report": {}}, None), ({"report": {"events": None}}, None),
    ({"report": {"events": []}}, 0),
    ({"report": {"events": [{"type": "episode_started"}]}}, 1),
])
def test_missing_event_evidence_is_not_counted_as_zero(tmp_path, payload, expected):
    (tmp_path / "goal_session_report.json").write_text(json.dumps(payload))
    assert build_archive_index(tmp_path)["counts"]["events"] == expected


def assessment_report(decision, label, *, platform_valid=True):
    report = {"events": [
        {"sequence": 0, "time_s": 0.0, "type": "episode_started", "payload": {}},
        {"sequence": 1, "time_s": 0.0, "type": "deployment_assessment_required",
         "payload": {"assessment_id": "a0", "assessment_epoch": 0}},
        {"sequence": 2, "time_s": 2.0, "type": "action_result",
         "payload": {"action_id": "declaration", "status": "accepted"}},
    ], "actions": [{"action_id": "declaration", "type": "deployment_assessment"}],
        "deployment_assessment": {"enabled": True, "status": decision,
            "termination": {"assessment_id": "a0"} if decision == "not_deployable" else None,
            "history": [{"assessment_id": "a0", "assessment_epoch": 0,
                         "public_task_sha256": "task-hash", "trigger_event_type": "episode_started",
                         "trigger_event_sequence": 0, "declared_at_s": 2.0,
                         "decision_action_id": "declaration", "decision": decision,
                         "external_evaluation": {"assessment_id": "a0", "assessment_epoch": 0,
                            "platform_valid": platform_valid, "feasibility_label": label,
                            "oracle_status": "verified" if label in {"deployable", "not_deployable"} else "failed",
                            "evidence_refs": ["independent-proof"], "evaluator_ref": "fixture-validator"}}]}}
    report["deployment_assessment"]["oracle_evaluations"] = [
        dict(report["deployment_assessment"]["history"][0]["external_evaluation"])]
    return report


@pytest.mark.parametrize("decision,label,expected", [
    ("deployable", "deployable", "correct_deployment"),
    ("not_deployable", "deployable", "false_refusal"),
    ("deployable", "not_deployable", "false_deployment"),
    ("not_deployable", "not_deployable", "correct_refusal"),
    ("not_deployable", None, None),
])
def test_assessment_records_do_not_equate_help_with_business_success(decision, label, expected):
    result = deployment_assessment_observations(assessment_report(decision, label), task_sha256="task-hash")
    row = result["records"][0]
    assert row["classification"] == expected
    assert result["accuracy_denominator"] == int(expected is not None)
    assert result["oracle_infrastructure_failure_count"] == int(expected is None)
    assert "unverified" not in result["classification_counts"]
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
    assert deployment_assessment_observations(report, task_sha256="task-hash")["accuracy_denominator"] == 0


def test_safe_stop_uses_action_order_at_equal_timestamp():
    report = assessment_report("not_deployable", "not_deployable")
    report["actions"].insert(0, {"action_id": "dispatch", "type": "dispatch_skill"})
    report["events"].insert(2, {"sequence": 2, "time_s": 2.0, "type": "action_result",
                              "payload": {"action_id": "dispatch", "status": "accepted"}})
    report["events"][-1]["sequence"] = 3
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


def test_superseded_unanswered_epoch_is_not_lost():
    report = assessment_report("deployable", "deployable")
    for epoch in (0, 1, 2):
        report["events"].append({"sequence": epoch + 10, "time_s": epoch + 3,
            "type": "deployment_assessment_required",
            "payload": {"assessment_id": f"a{epoch}", "assessment_epoch": epoch}})
    report["deployment_assessment"]["status"] = "required"
    result = deployment_assessment_observations(report, task_sha256="task-hash")
    assert result["required_epoch_count"] == 3
    assert [row["assessment_epoch"] for row in result["unanswered_epochs"]] == [1, 2]
    assert result["accuracy_denominator"] == 0
    assert result["oracle_infrastructure_failure_count"] == 2
    # Missing participant answers do not erase valid, preregistered risk sets.
    for epoch in (1, 2):
        report["deployment_assessment"]["oracle_evaluations"].append({
            **report["deployment_assessment"]["oracle_evaluations"][0],
            "assessment_id": f"a{epoch}", "assessment_epoch": epoch})
    result = deployment_assessment_observations(report, task_sha256="task-hash")
    assert result["accuracy_denominator"] == 3
    assert result["missing_assessment_count"] == 2


def test_one_failed_oracle_excludes_whole_episode_not_just_bad_point():
    report = assessment_report("deployable", "deployable")
    report["events"].append({"sequence": 3, "time_s": 4, "type": "deployment_assessment_required",
                             "payload": {"assessment_id": "a1", "assessment_epoch": 1}})
    report["deployment_assessment"]["oracle_evaluations"].append({
        "assessment_id": "a1", "assessment_epoch": 1, "oracle_status": "failed",
        "feasibility_label": None, "platform_valid": False, "evidence_refs": [], "evaluator_ref": "failed-oracle"})
    result = deployment_assessment_observations(report, task_sha256="task-hash")
    assert result["excluded_from_deployment_metrics"] is True
    assert result["accuracy_denominator"] == 0
    assert sum(result["classification_counts"].values()) == 0
    assert result["oracle_infrastructure_failure_count"] == 1
    assert result["records"][0]["classification"] == "correct_deployment"  # raw diagnostic only
