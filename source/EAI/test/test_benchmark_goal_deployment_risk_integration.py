"""Exercise risk denominators through real session events and archived sidecars.

The injected oracle is a fixture; these tests make no native feasibility claim.
"""

from copy import deepcopy
import json

import pytest

from demo.benchmark.goal_deployment_assessment import enable_deployment_assessment
from demo.benchmark.goal_driven_contract import build_goal_participant_api
from demo.benchmark.goal_driven_tasks import build_goal_task_package
from demo.benchmark.goal_scoring_adapter import build_scoring_adapter_metadata
from demo.benchmark.goal_session import GoalSession
from demo.benchmark.goal_cost_evidence import build_cost_source_bindings
from demo.benchmark.provenance import canonical_sha256


def _archive_session(tmp_path, labels, decisions):
    public = enable_deployment_assessment(
        build_goal_task_package("warehouse_nvd")["public_task"])
    api = build_goal_participant_api(public)
    session = GoalSession(public, api)
    for index, (label, decision) in enumerate(zip(labels, decisions, strict=True)):
        if index:
            report = session.report()
            agent = report["current_agents"][0]
            session.emit("capability_change", float(index), {
                "agent_id": agent,
                "capability_declaration": deepcopy(report["current_robot_declarations"][agent]),
                "capability_version": report["capability_versions"][agent] + 1,
            })
        gate = session.report()["deployment_assessment"]
        session.record_deployment_assessment_evaluation({
            "assessment_id": gate["assessment_id"],
            "assessment_epoch": gate["assessment_epoch"],
            "feasibility_label": label,
            "oracle_status": "verified" if label is not None else "failed",
            "platform_valid": label is not None,
            "evidence_refs": [f"fixture-oracle:{index}"],
            "evaluator_ref": "test.fixture-only-no-native-certificate",
        })
        if decision is not None:
            session.submit_action({
                "schema": "eai.benchmark.goal_action", "version": 1,
                "action_id": f"answer-{index}",
                "submitted_at_s": float(index),
                "type": "deployment_assessment",
                "payload": {
                    "assessment_id": gate["assessment_id"],
                    "assessment_epoch": gate["assessment_epoch"],
                    "roster_version": gate["roster_version"],
                    "capability_versions": gate["capability_versions"],
                    "decision": decision,
                    "reason_codes": ["missing_capability"] if decision == "not_deployable" else [],
                    "evidence_refs": [],
                },
            })
    report = session.report()
    (tmp_path / "goal_session_report.json").write_text(json.dumps({
        "episode_id": "risk-integration", "report": report,
    }))
    (tmp_path / "goal_cost_source_bindings.json").write_text(json.dumps(
        build_cost_source_bindings(
            tmp_path, episode_id="risk-integration", task_sha256=canonical_sha256(public))))
    metadata = build_scoring_adapter_metadata(
        public, episode_id="risk-integration", output_root=tmp_path)
    # Hidden fixture labels must not migrate into events/actions or the public task.
    assert "feasibility_label" not in json.dumps({
        "task": public, "events": report["events"], "actions": report["actions"],
    })
    for flag in ("scoreable", "execution_validated", "business_outcomes_validated"):
        assert metadata[flag] is False
        assert metadata["task_allocation"][flag] is False
    return report, metadata


def test_unanswered_runtime_epoch_survives_later_refusal_in_archive(tmp_path):
    report, metadata = _archive_session(
        tmp_path, ["deployable", "not_deployable", "deployable"],
        ["deployable", None, "not_deployable"])
    observed = metadata["deployment_assessment"]
    assert observed["episode_oracle_eligible"] is True
    assert observed["accuracy_denominator"] == 3
    assert observed["missing_assessment_count"] == 1
    assert observed["classification_counts"]["false_refusal"] == 1
    risks = observed["label_risk_counts"]
    assert risks["deployable_required_epoch_count"] == 2
    assert risks["not_deployable_required_epoch_count"] == 1
    assert risks["unanswered_not_deployable_count"] == 1
    phases = observed["label_risk_counts_by_phase"]
    assert phases["initial"]["required_epoch_count"] == 1
    assert phases["runtime"]["required_epoch_count"] == 2
    assert observed["conditional_rates_by_phase"]["initial"]["false_refusal_rate"] == 0.0
    assert observed["conditional_rates_by_phase"]["runtime"]["false_refusal_rate"] == 1.0
    rates = observed["conditional_rates"]
    assert rates["correct_deployment_rate"] + rates["false_refusal_rate"] + (
        rates["unanswered_deployable_rate"]) == pytest.approx(1.0)
    assert rates["correct_refusal_rate"] + rates["false_deployment_rate"] + (
        rates["unanswered_not_deployable_rate"]) == pytest.approx(1.0)
    assert [row["assessment_epoch"] for row in observed["unanswered_epochs"]] == [1]
    gate = metadata["task_allocation"]["deployment_gate"]
    assert metadata["task_allocation"]["version"] == 2
    assert gate["false_refusal_rate"] == pytest.approx(1 / 2)
    assert gate["false_deployment_rate"] == 0.0
    assert gate["all_epoch_false_refusal_share"] == pytest.approx(1 / 3)
    assert report["deployment_assessment"]["terminated"] is True
    assert observed["records"][-1]["business_task_completed"] is False


def test_wrong_deploy_uses_only_infeasible_oracle_risk_set(tmp_path):
    _, metadata = _archive_session(
        tmp_path, ["deployable", "deployable", "not_deployable"],
        ["deployable", "deployable", "deployable"])
    gate = metadata["task_allocation"]["deployment_gate"]
    assert gate["verified_epoch_count"] == 3
    assert gate["false_deployment_count"] == 1
    assert gate["false_deployment_rate"] == 1.0
    assert gate["false_refusal_rate"] == 0.0


def test_all_unanswered_has_oracle_denominator_and_absent_class_stays_null(tmp_path):
    _, metadata = _archive_session(tmp_path, ["deployable", "deployable"], [None, None])
    observed = metadata["deployment_assessment"]
    gate = metadata["task_allocation"]["deployment_gate"]
    assert observed["episode_oracle_eligible"] is True
    assert observed["accuracy_denominator"] == observed["missing_assessment_count"] == 2
    assert sum(observed["classification_counts"].values()) == 0
    assert gate["false_refusal_rate"] == 0.0
    assert gate["false_deployment_rate"] is None
    assert observed["conditional_rates"]["unanswered_deployable_rate"] == 1.0


def test_failed_oracle_excludes_episode_without_erasing_plan_observation(tmp_path):
    _, metadata = _archive_session(
        tmp_path, ["deployable", None], ["deployable", None])
    observed = metadata["deployment_assessment"]
    gate = metadata["task_allocation"]["deployment_gate"]
    assert observed["episode_oracle_eligible"] is False
    assert gate["verified_epoch_count"] == 0
    assert gate["false_refusal_rate"] is gate["false_deployment_rate"] is None
    assert metadata["plan_admission"]["status"] == "observed"


def test_all_unanswered_archive_cannot_be_rebound_to_another_public_task(tmp_path):
    _archive_session(tmp_path, ["deployable"], [None])
    other_public = enable_deployment_assessment(
        build_goal_task_package("city")["public_task"])
    metadata = build_scoring_adapter_metadata(
        other_public, episode_id="risk-integration", output_root=tmp_path)
    assert metadata["deployment_assessment"]["episode_oracle_eligible"] is False
    gate = metadata["task_allocation"]["deployment_gate"]
    assert gate["verified_epoch_count"] == 0
    assert gate["false_refusal_rate"] is gate["false_deployment_rate"] is None
