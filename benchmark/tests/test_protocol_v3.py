from dataclasses import replace
import hashlib
import pytest

from benchmark.protocol_v3 import (CommonStartV3, AdaptationPairV3, CalibrationBatchV3,
                                   ExternalContentionRegistrationV3, FixedSystemDisruptionV3,
                                   PairedBlockRerunLedgerV3, PairedBlockRerunV3,
                                   ProtectiveStopRegistrationV3, obligation_components)
from benchmark.records_v3 import IdentityV3, ObligationOutcomeV3, DynamicItemOutcomeV3
from benchmark.dynamic_v3 import score_d4_outcomes_v3


def identity():
    return IdentityV3("v3", "score", "catalog", "design", "task", "anchor", "production", ("P2", "P3"), "easy", "nominal", "system", "i", "r", "fact", "checkpoint")


def digest(label):
    return hashlib.sha256(label.encode()).hexdigest()


def test_d4_common_start_rejects_own_trajectory_and_late_history():
    start = CommonStartV3("i", "checkpoint", "history", "cal", "replay", "closure", "feasible", 1, 3, 5, 10, 20)
    start.validate_branch(checkpoint_digest="checkpoint", history_digest="history", initialization_digest="routine", coordinator_id="cal", system_id="s")
    with pytest.raises(ValueError, match="common checkpoint"):
        start.validate_branch(checkpoint_digest="own-first-eligible", history_digest="history", initialization_digest="routine", coordinator_id="cal", system_id="s")
    with pytest.raises(ValueError, match="precede"):
        replace(start, checkpoint_tick=3)


def test_same_task_can_contain_both_p3_subtypes_unreached_kept():
    rows = [ObligationOutcomeV3(identity(), obligation_id="j", relation="P3", participants=("r1",), p3_subtype="J", opportunity=True, completed=True), ObligationOutcomeV3(identity(), obligation_id="s", relation="P3", participants=("r1",), p3_subtype="S", opportunity=True)]
    assert obligation_components(rows, unknown=0) == {"P3J": 1, "P3S": 0}
    assert rows[0].disposition == "completed"
    assert rows[1].disposition == "unreached"
    with pytest.raises(ValueError, match="subtype"):
        replace(rows[0], p3_subtype=None)
    with pytest.raises(ValueError, match="unique"):
        obligation_components(rows + rows, unknown=0)


def test_cumulative_p3_service_preserves_partial_accepted_progress():
    row = ObligationOutcomeV3(identity(), obligation_id="s", relation="P3", participants=("r1",), p3_subtype="S", opportunity=True, accepted_fraction=.4)
    assert obligation_components([row], unknown=0) == {"P3S": .4}
    assert row.disposition == "completed"
    violated = replace(row, accepted_fraction=None, violated=True, evidence_digests=("external-evidence",), disposition=None)
    assert violated.disposition == "violated"
    assert obligation_components([violated], unknown=0) == {"P3S": 0}


def test_unknown_obligation_is_not_serialized_as_unreached_and_round_trips():
    from benchmark.records_v3 import RecordStatus
    row = ObligationOutcomeV3(
        identity(), status=RecordStatus.UNKNOWN, invalid_reason="lost evaluator record",
        obligation_id="handoff", relation="P2", participants=("giver", "receiver"), opportunity=True)
    assert row.disposition == "unknown"
    restored = ObligationOutcomeV3.from_dict(row.to_dict())
    assert restored == row and restored.disposition == "unknown"
    with pytest.raises(ValueError, match="validated IdentityV3"):
        ObligationOutcomeV3(**row.to_dict())
    with pytest.raises(ValueError, match="end_tick"):
        replace(restored, status="valid", invalid_reason=None, start_tick=4, end_tick=3)


def test_d4_item_weights_do_not_replace_paper_equal_item_mean():
    rows = [DynamicItemOutcomeV3(identity(), item_id="a", item_type="deadline", work_weight=99, legal_completion_fraction=1), DynamicItemOutcomeV3(identity(), item_id="b", item_type="deadline", work_weight=1, legal_completion_fraction=0)]
    assert score_d4_outcomes_v3(rows).point == 50


def test_d4_unknown_item_preserves_denominator_and_known_service():
    rows = [DynamicItemOutcomeV3(identity(), item_id="a", item_type="deadline", legal_completion_fraction=1), DynamicItemOutcomeV3(identity(), status="unknown", item_id="b", item_type="time_valued", legal_completion_fraction=None, service_fraction=.4)]
    result = score_d4_outcomes_v3(rows)
    assert result.status == "incomplete"
    assert result.support_weight == .5
    assert result.reason == "residual_infrastructure_unknown_requires_three_decidable_repeats"


def test_d5_reference_gain_and_forbidden_target_training():
    pair = AdaptationPairV3("source", "checkpoint", "certificate", "init", (1, 1, 0), (0, 1, 0), ("1", "2", "3"))
    assert pair.reference_gain == pytest.approx(100/3)
    with pytest.raises(ValueError, match="forbids"):
        replace(pair, parameter_updates=True)


def test_calibration_successful_medians_and_identity():
    batch = CalibrationBatchV3("b", "cal", "src", "tasks", ("1", "2", "3"), (True, False, True), (10, 1000, 30), (2, 1000, 4))
    assert batch.references(candidate_ids=("a",)) == (20, 3)
    with pytest.raises(ValueError, match="independent"):
        batch.references(candidate_ids=("cal",))


def test_catalog_and_runtime_transport_preserve_mixed_p3_obligations():
    from benchmark.task_catalog import load_default_catalog, TaskAnchor
    anchor = next(a for a in load_default_catalog() if "P3" in a.relation_signature)
    payload = anchor.to_dict()
    payload["p3_subtype"] = None
    payload["obligations"] = ["joint-work", "continuous-support"]
    payload["obligation_subtypes"] = {"joint-work": "P3-J", "continuous-support": "P3-S"}
    mixed = TaskAnchor.from_mapping(payload)
    assert mixed.to_protocol_contract().obligation_subtypes == payload["obligation_subtypes"]


def test_d6_uses_wire_bits_capped_physical_time_and_retains_unknown():
    from benchmark.protocol_v3 import d6_observed_value
    from benchmark.records_v3 import ResourceTraceV3
    trace = ResourceTraceV3(identity(), submitted_bits=99999, transmitted_bits=200, sim_time_s=200)
    args = dict(success=True, horizon_seconds=20, reference_bits=100, reference_time=10)
    assert d6_observed_value(trace, **args) == .5
    assert d6_observed_value(trace, **{**args, "success": False}) == 0
    assert d6_observed_value(replace(trace, status="unknown"), **args) is None


def test_skill_failure_rerun_is_registered_for_the_whole_paired_block():
    rerun = PairedBlockRerunV3(
        "block", ("fact", "reference"), "fact", "skill_postcondition",
        digest("checkpoint"), digest("rng"), digest("old-controller-substream"), digest("new-controller-substream"), 1, ("r0", "r1"))
    assert rerun.rerun_branch_ids == ("fact", "reference")
    ledger = PairedBlockRerunLedgerV3(rerun)
    results = {"fact": digest("fact-result"), "reference": digest("reference-result")}
    receipt = ledger.append(attempt_id="retry-1", executed_branch_ids=("fact", "reference"),
                            branch_result_digests=results, completion="incomplete_pair")
    assert receipt.status == "pair-invalid"
    assert receipt.registration_digest == rerun.registration_digest
    ledger.validate_receipt(receipt)
    with pytest.raises(ValueError, match="registration digest"):
        ledger.validate_receipt(replace(receipt, registration_digest=digest("forged")))
    with pytest.raises(ValueError, match="immutable payload"):
        ledger.validate_receipt(replace(receipt, status="valid-pair"))
    with pytest.raises(ValueError, match="already consumed"):
        ledger.append(attempt_id="retry-1", executed_branch_ids=("fact", "reference"), branch_result_digests=results, completion="complete")
    with pytest.raises(ValueError, match="every branch"):
        PairedBlockRerunLedgerV3(rerun).append(attempt_id="partial", executed_branch_ids=("fact",), branch_result_digests={"fact": digest("fact")}, completion="complete")
    with pytest.raises(ValueError, match="retry cap"):
        ledger.append(attempt_id="retry-2", executed_branch_ids=("fact", "reference"), branch_result_digests=results, completion="complete")
    with pytest.raises(ValueError, match="not retryable"):
        PairedBlockRerunV3(
            "block", ("fact", "reference"), "fact", "method",
            digest("checkpoint"), digest("rng"), digest("old"), digest("new"), 1, ("r0", "r1"))
    with pytest.raises(ValueError, match="one original record"):
        PairedBlockRerunV3(
            "block", ("fact", "reference"), "fact", "skill_postcondition",
            digest("checkpoint"), digest("rng"), digest("old"), digest("new"), 1, ("r0",))
    with pytest.raises(ValueError, match="new controller"):
        PairedBlockRerunV3(
            "block", ("fact", "reference"), "fact", "skill_postcondition",
            digest("checkpoint"), digest("rng"), digest("same"), digest("same"), 1, ("r0", "r1"))


def test_contention_and_protective_stop_require_registered_protocol_inputs():
    contention = ExternalContentionRegistrationV3(
        "condition", "external-holder", "station", 20, "shared_robot_linearization", 5, digest("typed-lock"))
    assert contention.grant_time_is_outcome is True
    assert contention.queue_request("request-1", 20) == ("request-1", "external-holder", "station", 20)
    with pytest.raises(ValueError, match="frozen tick"):
        contention.queue_request("request-2", 21)
    with pytest.raises(ValueError, match="same registered linearization"):
        ExternalContentionRegistrationV3(
            "condition", "holder", "station", 20, "fifo_linearized", 5, digest("typed-lock"))
    stop = ProtectiveStopRegistrationV3(
        "condition", ("robot-1",), digest("controller"), digest("stop"), digest("verified-stop"),
        digest("load"), 20, 30, digest("reset"))
    assert stop.release_tick == 30
    with pytest.raises(ValueError, match="load_state_checkpoint_digest"):
        ProtectiveStopRegistrationV3(
            "condition", ("robot-1",), digest("controller"), digest("stop"), digest("verified-stop"),
            "", 20, 30, digest("reset"))
    with pytest.raises(ValueError, match="lowercase SHA-256"):
        ExternalContentionRegistrationV3(
            "condition", "holder", "station", 20, "shared_robot_linearization", 5, "claimed")


def test_disruption_comparison_keeps_system_task_family_and_starting_team_fixed():
    registered = FixedSystemDisruptionV3(
        "link-loss", digest("system"), digest("system"), digest("tasks"), digest("tasks"),
        digest("team"), digest("team"), digest("nominal"), digest("loss"))
    assert registered.retraining_applied is False
    with pytest.raises(ValueError, match="forbids retraining"):
        replace(registered, retraining_applied=True)
    with pytest.raises(ValueError, match="system fixed"):
        replace(registered, treatment_system_digest=digest("retrained-system"))
    with pytest.raises(ValueError, match="task family fixed"):
        replace(registered, treatment_task_family_digest=digest("larger-tasks"))
    with pytest.raises(ValueError, match="starting team fixed"):
        replace(registered, treatment_starting_team_digest=digest("larger-team"))
    with pytest.raises(ValueError, match="must change"):
        replace(registered, treatment_condition_digest=digest("nominal"))
    with pytest.raises(ValueError, match="not runtime disruption"):
        replace(registered, condition_kind="observation_setting")
