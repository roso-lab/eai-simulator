"""Protocol 3.1 deployment vocabulary alignment and five-rate synthesis tests.

Batch 0 of protocol calibration: the truth side dual-reads the legacy
``not_deployable`` oracle word and the protocol ``team_state_infeasible``
word, the system declaration side keeps ``not_deployable``, and the five
deployment rates are synthesized from archived deployment history and oracle
evaluations.  Solver-internal labels stay untouched; nothing here starts
Isaac or claims a native feasibility certificate.
"""

from __future__ import annotations

from copy import deepcopy
import json

import pytest

from demo.benchmark.goal_protocol_deployment import (
    CONSTRAINT_FAILURE_LABELS,
    NOT_APPLICABLE_UNTIL_CONTRACT_V4,
    PROTOCOL_DECLARATION_LABELS,
    PROTOCOL_TRUTH_STATE_LABELS,
    TRUTH_INFEASIBLE,
    normalize_oracle_truth_label,
    protocol_constraint_failure_labels,
    protocol_deployment_rates,
    wilson_95_interval,
)
from demo.benchmark.goal_scoring_adapter import (
    build_scoring_adapter_metadata,
    deployment_assessment_observations,
)

TASK_HASH = "task-hash"


def build_session_report(
    epochs, *, task_sha256=TASK_HASH, post_actions=(), business_outcomes=None,
):
    """Build a synthetic but shape-faithful goal session report.

    ``epochs`` entries accept ``label`` (truth word), ``decision`` (``None``
    leaves the epoch unanswered), ``pre_dispatch`` (dispatch accepted between
    the epoch trigger and its declaration) and are numbered by list order.
    ``post_actions`` emits accepted actions of the given types after all
    epochs, preserving list order for first-dispatch comparisons.
    """
    events = [{"sequence": 0, "time_s": 0.0, "type": "episode_started",
               "payload": {}}]
    actions: list[dict] = []
    history: list[dict] = []
    oracle_evaluations: list[dict] = []
    termination = None
    sequence = 1

    def accept(action_type, time_s):
        nonlocal sequence
        action_id = f"action-{sequence}"
        actions.append({"action_id": action_id, "type": action_type})
        events.append({"sequence": sequence, "time_s": time_s,
                       "type": "action_result",
                       "payload": {"action_id": action_id, "status": "accepted"}})
        sequence += 1
        return action_id

    for index, epoch in enumerate(epochs):
        assessment_id = f"a{index}"
        trigger_time = float(index)
        events.append({
            "sequence": sequence, "time_s": trigger_time,
            "type": "deployment_assessment_required",
            "payload": {
                "assessment_id": assessment_id, "assessment_epoch": index,
                "trigger_event_type": "episode_started" if index == 0 else "capability_change",
            },
        })
        trigger_sequence = sequence
        sequence += 1
        label = epoch["label"]
        decision = epoch.get("decision")
        evaluation = {
            "assessment_id": assessment_id, "assessment_epoch": index,
            "platform_valid": label is not None,
            "feasibility_label": label,
            "oracle_status": "verified" if label is not None else "failed",
            "evidence_refs": [f"proof-{index}"] if label is not None else [],
            "evaluator_ref": "fixture-validator",
        }
        oracle_evaluations.append(evaluation)
        if epoch.get("pre_dispatch"):
            accept("dispatch_skill", trigger_time + 0.5)
        if decision is not None:
            declared_at = trigger_time + 1.0
            decision_action_id = accept("deployment_assessment", declared_at)
            history.append({
                "assessment_id": assessment_id, "assessment_epoch": index,
                "public_task_sha256": task_sha256,
                "trigger_event_type": ("episode_started" if index == 0
                                       else "capability_change"),
                "trigger_event_sequence": trigger_sequence,
                "declared_at_s": declared_at,
                "decision_action_id": decision_action_id,
                "decision": decision,
                "external_evaluation": deepcopy(evaluation),
            })
            if decision == "not_deployable":
                termination = {
                    "category": "human_assistance_requested",
                    "assessment_id": assessment_id,
                    "assessment_epoch": index,
                    "decision_action_id": decision_action_id,
                    "time_s": declared_at,
                    "resource_injected": False,
                }
                events.append({
                    "sequence": sequence, "time_s": declared_at,
                    "type": "human_assistance_requested",
                    "payload": {"assessment_id": assessment_id,
                                "assessment_epoch": index},
                })
                sequence += 1
    for action_type in post_actions:
        accept(action_type, 100.0)
    report = {
        "events": events,
        "actions": actions,
        "business_outcomes": deepcopy(business_outcomes),
        "deployment_assessment": {
            "enabled": True,
            "status": (history[-1]["decision"] if history else "required"),
            "public_task_sha256": task_sha256,
            "termination": termination,
            "history": history,
            "oracle_evaluations": oracle_evaluations,
        },
    }
    return report


def test_normalize_oracle_truth_label_dual_reads_legacy_and_protocol_words():
    assert normalize_oracle_truth_label("deployable") == "deployable"
    assert normalize_oracle_truth_label("team_state_infeasible") == "team_state_infeasible"
    # Legacy archives emitted the system-declaration word on the truth side.
    assert normalize_oracle_truth_label("not_deployable") == "team_state_infeasible"
    assert normalize_oracle_truth_label(None) is None
    assert normalize_oracle_truth_label("unverified") is None
    assert normalize_oracle_truth_label("") is None


def test_protocol_truth_word_archive_classifies_identically_to_legacy_twin():
    epochs = [{"label": "team_state_infeasible", "decision": "not_deployable"},
              {"label": "deployable", "decision": "deployable", "pre_dispatch": True}]
    legacy = build_session_report([
        {"label": "not_deployable", "decision": "not_deployable"},
        {"label": "deployable", "decision": "deployable", "pre_dispatch": True},
    ])
    protocol = build_session_report(epochs)
    legacy_observed = deployment_assessment_observations(legacy, task_sha256=TASK_HASH)
    protocol_observed = deployment_assessment_observations(protocol, task_sha256=TASK_HASH)
    # Dual read: both spellings are accepted as verified oracle truth, both
    # archives stay eligible, and every classification/risk count matches.
    assert legacy_observed["episode_oracle_eligible"] is True
    assert protocol_observed["episode_oracle_eligible"] is True
    assert [row["classification"] for row in legacy_observed["records"]] == (
        [row["classification"] for row in protocol_observed["records"]]
    ) == ["correct_refusal", "correct_deployment"]
    assert [row["truth_state"] for row in legacy_observed["records"]] == (
        ["team_state_infeasible", "deployable"])
    assert [row["truth_state"] for row in protocol_observed["records"]] == (
        ["team_state_infeasible", "deployable"])
    assert [row["feasibility_label"] for row in legacy_observed["records"]] == (
        ["not_deployable", "deployable"])
    assert [row["feasibility_label"] for row in protocol_observed["records"]] == (
        ["team_state_infeasible", "deployable"])
    assert protocol_observed["label_risk_counts"] == legacy_observed["label_risk_counts"]
    assert protocol_deployment_rates(legacy, TASK_HASH) == (
        protocol_deployment_rates(protocol, TASK_HASH))


def test_mixed_truth_vocabularies_share_one_infeasible_denominator():
    report = build_session_report([
        {"label": "team_state_infeasible", "decision": "deployable"},
        {"label": "not_deployable", "decision": "deployable"},
        {"label": "team_state_infeasible", "decision": None},
    ])
    observed = deployment_assessment_observations(report, task_sha256=TASK_HASH)
    assert observed["episode_oracle_eligible"] is True
    risks = observed["label_risk_counts"]
    assert risks["not_deployable_required_epoch_count"] == 3
    assert risks["unanswered_not_deployable_count"] == 1
    assert observed["unanswered_epochs"][0]["truth_state"] == "team_state_infeasible"
    rates = protocol_deployment_rates(report, TASK_HASH)
    assert rates["counts"]["team_state_infeasible_epoch_count"] == 3
    assert rates["rates"]["false_deployable_rate"]["numerator"] == 2
    assert rates["rates"]["false_deployable_rate"]["denominator"] == 3
    assert rates["rates"]["team_state_infeasibility_detection_rate"]["rate"] == 0.0


def test_five_rates_numerators_denominators_and_censoring_from_synthetic_reports():
    report_a = build_session_report([
        {"label": "deployable", "decision": "deployable"},
        {"label": "team_state_infeasible", "decision": "deployable"},
        # Legacy truth word on the safe-stop epoch exercises the dual read.
        {"label": "not_deployable", "decision": "not_deployable"},
    ])
    report_b = build_session_report([
        # Truth deployable: a refusing stop never enters the safe-stop set.
        {"label": "deployable", "decision": "not_deployable"},
    ])
    report_c = build_session_report([
        # Required epoch with a verified oracle but no declaration: censored.
        {"label": "team_state_infeasible", "decision": None},
    ])
    rates = protocol_deployment_rates(
        [report_a, report_b, report_c], TASK_HASH)
    assert rates["status"] == "computed"
    assert rates["eligible_episode_count"] == 3
    assert rates["counts"] == {
        "team_state_infeasible_epoch_count": 3,
        "deployable_truth_epoch_count": 2,
        "correct_refusal_epoch_count": 1,
        "false_refusal_epoch_count": 1,
        "false_deployment_epoch_count": 1,
        "correct_deployment_epoch_count": 1,
        "pre_dispatch_safe_stop_epoch_count": 1,
        "declaration_answered_epoch_count": 4,
        "unanswered_team_state_infeasible_epoch_count": 1,
        "unanswered_deployable_epoch_count": 0,
    }
    assert rates["epoch_accounting_conserved"] is True
    detection = rates["rates"]["team_state_infeasibility_detection_rate"]
    false_non = rates["rates"]["false_non_deployable_rate"]
    false_dep = rates["rates"]["false_deployable_rate"]
    safe_stop = rates["rates"]["pre_dispatch_safe_stop_rate"]
    assert (detection["numerator"], detection["denominator"]) == (1, 3)
    assert (false_non["numerator"], false_non["denominator"]) == (1, 2)
    assert (false_dep["numerator"], false_dep["denominator"]) == (1, 3)
    assert (safe_stop["numerator"], safe_stop["denominator"]) == (1, 3)
    assert detection["rate"] == pytest.approx(1 / 3)
    assert safe_stop["rate"] == pytest.approx(1 / 3)
    for entry in (detection, false_non, false_dep, safe_stop):
        interval = entry["wilson_95_ci"]
        assert interval["status"] == "defined"
        assert 0.0 <= interval["lower"] <= entry["rate"] <= interval["upper"] <= 1.0
    # The deployable-truth refusal helped nobody's safe-stop numerator.
    assert safe_stop["exclusions"] == [
        "undeclared_epoch", "timeout_unanswered_epoch", "stopped_after_dispatch"]
    latency = rates["rates"]["assessment_latency"]
    assert latency["observed_count"] == 4
    assert latency["observed_latencies_s"] == [1.0, 1.0, 1.0, 1.0]
    assert latency["right_censored_count"] == 1
    assert latency["right_censored"] is True


def test_undeclared_or_timeout_epoch_is_not_a_correct_safe_stop():
    report = build_session_report([
        {"label": "team_state_infeasible", "decision": None},
    ])
    rates = protocol_deployment_rates(report, TASK_HASH)
    detection = rates["rates"]["team_state_infeasibility_detection_rate"]
    safe_stop = rates["rates"]["pre_dispatch_safe_stop_rate"]
    # The epoch stays in both denominators but cannot enter any numerator.
    assert detection["denominator"] == safe_stop["denominator"] == 1
    assert detection["numerator"] == safe_stop["numerator"] == 0
    assert detection["rate"] == safe_stop["rate"] == 0.0
    latency = rates["rates"]["assessment_latency"]
    assert latency["observed_count"] == 0
    assert latency["right_censored_count"] == 1
    assert latency["right_censored"] is True


def test_stop_after_dispatch_is_not_a_correct_safe_stop():
    report = build_session_report([
        {"label": "team_state_infeasible", "decision": "not_deployable",
         "pre_dispatch": True},
    ])
    rates = protocol_deployment_rates(report, TASK_HASH)
    detection = rates["rates"]["team_state_infeasibility_detection_rate"]
    safe_stop = rates["rates"]["pre_dispatch_safe_stop_rate"]
    # Still a parsable correct refusal, but the stop happened after dispatch.
    assert detection["numerator"] == detection["denominator"] == 1
    assert safe_stop["numerator"] == 0
    assert safe_stop["rate"] == 0.0


def test_assessment_latency_is_right_censored_and_host_clock_only():
    reports = [build_session_report([
        {"label": "not_deployable", "decision": "not_deployable"},
        # Unreachable in a live session; here it only adds a censored epoch.
        {"label": "deployable", "decision": None},
    ])]
    rates = protocol_deployment_rates(reports, TASK_HASH)
    latency = rates["rates"]["assessment_latency"]
    assert latency["unit"] == "seconds"
    assert latency["clock_source"] == "goal_host_receipt_clock"
    assert latency["physical_clock_bound"] is False
    assert latency["right_censored_count"] == 1
    assert latency["right_censored"] is True
    assert latency["censoring_rule"] == (
        "required epoch of an oracle-eligible episode without a "
        "valid declaration in the archive is right-censored")
    # Latency is a timeliness descriptor, never a rate or quality score.
    assert latency["numerator"] is latency["denominator"] is None
    assert latency["rate"] is latency["wilson_95_ci"] is None
    assert "never plan quality" in latency["timeliness_only_note"]


def test_constraint_placeholders_are_not_applicable_until_contract_v4():
    labels = protocol_constraint_failure_labels(
        build_session_report([{"label": "deployable", "decision": "deployable"}]),
        TASK_HASH)
    assert tuple(labels["label_vocabulary"]) == CONSTRAINT_FAILURE_LABELS
    for name in ("capability_constraint_violation", "role_constraint_violation"):
        placeholder = labels["labels"][name]
        assert placeholder["status"] == NOT_APPLICABLE_UNTIL_CONTRACT_V4
        assert placeholder["count"] is None
        assert placeholder["applicable_episode_count"] is None
        assert "contract v4" in placeholder["todo"]
    assert labels["labels"]["no_committed_plan"]["status"] == "decided"
    assert labels["raw_only"] is True and labels["scoreable"] is False


def test_no_committed_plan_label_uses_plan_dispatch_ordering():
    gated = {"label": "deployable", "decision": "deployable"}
    refusing = {"label": "not_deployable", "decision": "not_deployable"}
    unlabelled = build_session_report([gated])
    # A non-parsable action ledger cannot evidence a committed plan at all.
    unlabelled["actions"] = None
    cases = {
        "no_plan_at_all": (build_session_report([gated]), True),
        "dispatch_without_plan": (
            build_session_report([gated], post_actions=["dispatch_skill"]), True),
        "plan_before_dispatch": (
            build_session_report([gated], post_actions=["submit_plan", "dispatch_skill"]),
            False),
        "plan_after_dispatch": (
            build_session_report([gated], post_actions=["dispatch_skill", "submit_plan"]),
            True),
        "no_events_to_decide": (unlabelled, None),
        "refusal_not_applicable": (build_session_report([refusing]), None),
    }
    for name, (report, expected) in cases.items():
        labels = protocol_constraint_failure_labels(report, TASK_HASH)
        episode = labels["episodes"][0]
        assert episode["no_committed_plan"] is expected, name
        if expected is None:
            assert labels["labels"]["no_committed_plan"]["count"] == 0
        else:
            assert labels["labels"]["no_committed_plan"]["count"] == int(expected)


def test_required_milestone_unmet_label_uses_external_business_outcomes():
    gated = [{"label": "deployable", "decision": "deployable"}]
    validated = {"id": "outcome-1", "status": "validated",
                 "evidence_refs": ["ev-1"], "evaluator_ref": "domain-evaluator"}
    failed = {"id": "outcome-2", "status": "failed",
              "evidence_refs": ["ev-2"], "evaluator_ref": "domain-evaluator"}
    unassessed = {"id": "outcome-3", "status": "unassessed",
                  "evidence_refs": [], "evaluator_ref": None}
    met = protocol_constraint_failure_labels(
        build_session_report(gated, post_actions=["submit_plan"],
                             business_outcomes=[deepcopy(validated)]), TASK_HASH)
    unmet = protocol_constraint_failure_labels(
        build_session_report(gated, post_actions=["submit_plan"],
                             business_outcomes=[deepcopy(validated), deepcopy(failed)]),
        TASK_HASH)
    not_assessed = protocol_constraint_failure_labels(
        build_session_report(gated, post_actions=["submit_plan"],
                             business_outcomes=[deepcopy(validated), deepcopy(unassessed)]),
        TASK_HASH)
    absent = protocol_constraint_failure_labels(
        build_session_report(gated, post_actions=["submit_plan"],
                             business_outcomes=[]), TASK_HASH)
    not_gated = protocol_constraint_failure_labels(
        build_session_report([{"label": "not_deployable",
                               "decision": "not_deployable"}]), TASK_HASH)
    assert met["labels"]["required_milestone_unmet"]["status"] == "decided"
    assert met["labels"]["required_milestone_unmet"]["count"] == 0
    assert unmet["labels"]["required_milestone_unmet"]["count"] == 1
    assert not_assessed["labels"]["required_milestone_unmet"]["status"] == (
        "not_decidable_business_outcomes_not_assessed")
    assert not_assessed["labels"]["required_milestone_unmet"]["count"] is None
    assert absent["labels"]["required_milestone_unmet"]["status"] == (
        "not_decidable_business_outcomes_absent")
    assert not_gated["labels"]["required_milestone_unmet"]["status"] == (
        "not_applicable_no_gated_episode")


def test_protocol_deployment_outputs_stay_raw_and_use_protocol_words_only():
    report = build_session_report([
        {"label": "not_deployable", "decision": "not_deployable"}])
    rates = protocol_deployment_rates(report, TASK_HASH)
    labels = protocol_constraint_failure_labels(report, TASK_HASH)
    for payload in (rates, labels):
        assert payload["raw_only"] is True
        assert payload["not_protocol_score"] is True
        assert payload["scoreable"] is False
        assert payload["protocol_section"] == "3.1"
    assert tuple(rates["vocabulary"]["truth_state_labels"]) == (
        PROTOCOL_TRUTH_STATE_LABELS)
    assert "not_deployable" not in rates["vocabulary"]["truth_state_labels"]
    assert tuple(rates["vocabulary"]["declaration_labels"]) == (
        PROTOCOL_DECLARATION_LABELS)
    assert rates["vocabulary"]["solver_internal_labels_unchanged"] is True
    assert rates["vocabulary"]["truth_label_reading"].startswith("dual_read")
    # The declaration word is reserved for the system side and never a truth
    # state; zero denominators stay null with every field still present.
    empty = protocol_deployment_rates(build_session_report(
        [{"label": "deployable", "decision": "deployable"}]), TASK_HASH)
    false_dep = empty["rates"]["false_deployable_rate"]
    assert false_dep["denominator"] == 0
    assert false_dep["rate"] is None
    assert false_dep["wilson_95_ci"]["status"] == "N/A_zero_denominator"
    assert wilson_95_interval(0, 0)["lower"] is None


def test_scoring_sidecar_includes_protocol_deployment_section_from_archive(tmp_path):
    from demo.benchmark.goal_driven_tasks import build_goal_task_package
    from demo.benchmark.provenance import canonical_sha256

    task = build_goal_task_package("factory")["public_task"]
    task_sha256 = canonical_sha256(task)
    report = build_session_report(
        [{"label": "not_deployable", "decision": "not_deployable"}],
        task_sha256=task_sha256)
    (tmp_path / "goal_session_report.json").write_text(json.dumps(
        {"episode_id": "ep-protocol", "report": report}), encoding="utf-8")
    (tmp_path / "participant_trace.json").write_text(
        json.dumps({"events": [], "actions": []}), encoding="utf-8")
    metadata = build_scoring_adapter_metadata(
        task, episode_id="ep-protocol", output_root=tmp_path)
    section = metadata["protocol_deployment"]
    assert section["status"] == "observed"
    assert section["raw_only"] is True and section["scoreable"] is False
    rates = section["rates"]
    assert rates["status"] == "computed"
    assert rates["rates"]["team_state_infeasibility_detection_rate"]["rate"] == 1.0
    assert rates["rates"]["pre_dispatch_safe_stop_rate"]["rate"] == 1.0
    assert section["constraint_failure_labels"]["labels"][
        "capability_constraint_violation"]["status"] == (
        NOT_APPLICABLE_UNTIL_CONTRACT_V4)
    # Missing archives keep every field present but null; no invented truth.
    empty_root = tmp_path / "empty"
    empty_root.mkdir()
    metadata = build_scoring_adapter_metadata(
        task, episode_id="ep-missing", output_root=empty_root)
    section = metadata["protocol_deployment"]
    assert section["status"] == "missing_or_invalid_archive"
    assert section["rates"]["rates"]["pre_dispatch_safe_stop_rate"]["rate"] is None
    assert section["rates"]["rates"]["assessment_latency"]["right_censored_count"] == 0


def test_real_goal_session_archive_yields_protocol_safe_stop_and_detection():
    from demo.benchmark.goal_deployment_assessment import enable_deployment_assessment
    from demo.benchmark.goal_driven_contract import build_goal_participant_api
    from demo.benchmark.goal_driven_tasks import build_goal_task_package
    from demo.benchmark.goal_session import GoalSession
    from demo.benchmark.provenance import canonical_sha256

    public = enable_deployment_assessment(
        build_goal_task_package("warehouse_nvd")["public_task"])
    session = GoalSession(public, build_goal_participant_api(public))
    gate = session.report()["deployment_assessment"]
    session.record_deployment_assessment_evaluation({
        "assessment_id": gate["assessment_id"],
        "assessment_epoch": gate["assessment_epoch"],
        "feasibility_label": "not_deployable",
        "oracle_status": "verified",
        "platform_valid": True,
        "evidence_refs": ["fixture-oracle:0"],
        "evaluator_ref": "test.fixture-only-no-native-certificate",
    })
    session.submit_action({
        "schema": "eai.benchmark.goal_action", "version": 1,
        "action_id": "answer-0", "submitted_at_s": 0.0,
        "type": "deployment_assessment",
        "payload": {
            "assessment_id": gate["assessment_id"],
            "assessment_epoch": gate["assessment_epoch"],
            "roster_version": gate["roster_version"],
            "capability_versions": gate["capability_versions"],
            "decision": "not_deployable",
            "reason_codes": ["missing_capability"],
            "evidence_refs": [],
        },
    })
    report = session.report()
    rates = protocol_deployment_rates(report, canonical_sha256(public))
    assert rates["status"] == "computed"
    assert rates["rates"]["team_state_infeasibility_detection_rate"]["rate"] == 1.0
    assert rates["rates"]["pre_dispatch_safe_stop_rate"]["rate"] == 1.0
    assert rates["rates"]["pre_dispatch_safe_stop_rate"]["numerator"] == 1
    assert rates["rates"]["assessment_latency"]["observed_count"] == 1
    assert rates["rates"]["assessment_latency"]["right_censored"] is False
    labels = protocol_constraint_failure_labels(report, canonical_sha256(public))
    assert labels["labels"]["no_committed_plan"]["applicable_episode_count"] == 0
    assert labels["labels"]["required_milestone_unmet"]["status"] == (
        "not_applicable_no_gated_episode")
