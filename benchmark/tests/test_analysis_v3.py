from dataclasses import replace
import pytest
from benchmark.analysis_v3 import ScheduledRunV3, analyze_dimension, d5_run_weights
from benchmark.registration_v3 import ExperimentRegistrationV3, coverage_status, formal_readiness
from benchmark.scorebook_v3 import PAPER_COMMIT
from benchmark.statistics_v3 import hoeffding_sample_size, d2_precision_plan, ClusterDesignV3, InstanceV3


def runs(values):
    return [ScheduledRunV3(f"{i}/{s}", str(i), "cell", str(s), "checkpoint", "evidence", tuple(v), "infrastructure_unknown" if any(x is None for x in v) else "observed") for i,v in enumerate(values) for s in range(3)]


def test_partial_unknown_repeat_keeps_fixed_weight_identification_range():
    rows = runs([(1,), (0,)])
    rows[1] = replace(rows[1], values=(None,), status="infrastructure_unknown")
    report = analyze_dimension("D4", rows, stratum_weights={"cell": 1}, resamples=100)
    assert report["status"] == "range"
    assert report["reason"] == "residual_infrastructure_unknown_identification_range"
    assert report["identification"] == pytest.approx([0., 50.])
    assert report["scheduled_runs"] == 6 and report["infrastructure_unknown"] == 1
    assert report["physical_qualified"] is False
    with pytest.raises(ValueError, match="three scheduled"):
        analyze_dimension("D4", rows[:-1], stratum_weights={"cell": 1}, resamples=100)


def test_d2_unknown_retains_bounded_identification_range():
    rows = runs([(1, 1, 0, 0), (1, 0, 0, 0)])
    rows[0] = replace(rows[0], values=(None, 1, 0, 0), status="infrastructure_unknown")
    report = analyze_dimension("D2", rows, stratum_weights={"cell": 1}, resamples=100)
    assert report["status"] == "range"
    assert report["identification"][0] <= report["identification"][1]
    assert report["infrastructure_unknown"] == 1


def test_method_failure_remains_observed_and_d2_pipeline():
    rows = runs([(1, 1, 1, 0), (0, 0, 0, 0)])
    rows[-1] = replace(rows[-1], status="method_failure")
    report = analyze_dimension("D2", rows, stratum_weights={"cell": 1}, resamples=100)
    assert report["identification"] == pytest.approx([200/3, 200/3])
    assert report["method"] == "d2_finite_sample_retention_set"
    assert report["method_failures"] == 1


def test_hierarchical_d5_weights_do_not_reward_larger_families():
    weights = d5_run_weights({"a": {"c": ["u"]}, "b": {"x": ["u", "v"], "y": ["u"]}})
    assert sum(weights.values()) == pytest.approx(1)
    assert sum(w for k,w in weights.items() if k[0] == "a") == pytest.approx(.5)
    assert sum(w for k,w in weights.items() if k[:2] == ("b", "x")) == pytest.approx(.25)


def test_formal_registration_does_not_invent_missing_certification():
    reg = ExperimentRegistrationV3(PAPER_COMMIT, *([None]*11))
    result = formal_readiness(reg, case_report={"status": "na"}, baseline_report={"status": "na"}, calibration_report=None)
    assert not result["ready"] and "capacities_bps" in result["blockers"]
    assert coverage_status(planned=40, infrastructure_valid=30)["reportable"] is False
    assert coverage_status(planned=30, infrastructure_valid=30)["reportable"] is True
    with pytest.raises(ValueError, match="C0"):
        replace(reg, capacities_bps=(100, 10, 20, 1))


def test_precision_plan_cap_is_reported_not_silently_extended():
    r = hoeffding_sample_size(target_half_width=1)
    assert r["count"] == 200 and not r["precision_sufficient"]
    pilot = ClusterDesignV3([InstanceV3(str(i), "cell", (1, 1, 1, 1)) for i in range(30)], {"cell": 1})
    r = d2_precision_plan({"independent": pilot}, counts=(30,), simulations=1, resamples=100, target_half_width=1)
    assert not r["precision_sufficient"] and r["count_per_stratum"] == 30


def test_calibration_median_uncertainty_is_not_zero_for_tiny_batch():
    from benchmark.registration_v3 import median_interval
    assert median_interval([10, 20, 30]) == (0, float("inf"))
    lo, hi = median_interval(list(range(1, 101)))
    assert lo < 50.5 < hi


def test_paired_unknown_statuses_reach_statistics_without_becoming_method_failures():
    from benchmark.protocol_v3 import PairedBlockAttemptReceiptV3, PairedBlockRerunV3, PairedBlockRerunLedgerV3
    import hashlib
    digest = lambda value: hashlib.sha256(value.encode()).hexdigest()
    registration = PairedBlockRerunV3(
        "block", ("fact", "reference"), "fact", "skill_postcondition",
        digest("checkpoint"), digest("rng"), digest("old"), digest("new"), 1, ("r0", "r1"))
    ledger = PairedBlockRerunLedgerV3(registration)
    results = {"fact": digest("fact-result"), "reference": digest("reference-result")}
    issued = ledger.append(
        attempt_id="attempt-1", executed_branch_ids=("fact", "reference"),
        branch_result_digests=results, completion="incomplete_pair")
    base = dict(run_id="pair/0", instance_id="pair", stratum="cell", repeat_id="0",
                checkpoint_digest=digest("checkpoint"), evidence_digest=results["fact"])
    receipt = PairedBlockAttemptReceiptV3.from_dict(issued.to_dict())
    assert receipt == issued
    row = ScheduledRunV3.from_paired_attempt(issued, ledger=ledger, branch_id="fact", values=(None,), **base)
    rows = [row]
    for repeat in (1, 2):
        rows.append(ScheduledRunV3(f"pair/{repeat}", "pair", "cell", str(repeat),
                                   "checkpoint", "evidence", (1.,)))
    report = analyze_dimension("D4", rows, stratum_weights={"cell": 1}, resamples=100)
    assert report["pair_invalid"] == 1
    assert report["method_failures"] == 0
    assert report["reason"] == "paired_block_invalid_identification_range"

    with pytest.raises(ValueError, match="registration digest"):
        ScheduledRunV3.from_paired_attempt(
            replace(issued, registration_digest=digest("forged")), ledger=ledger,
            branch_id="fact", values=(None,), **base)


def test_valid_pair_retains_paired_provenance_in_scheduled_run():
    from benchmark.protocol_v3 import PairedBlockRerunV3, PairedBlockRerunLedgerV3
    import hashlib
    digest = lambda value: hashlib.sha256(value.encode()).hexdigest()
    registration = PairedBlockRerunV3(
        "block", ("fact", "reference"), "fact", "skill_postcondition",
        digest("checkpoint"), digest("rng"), digest("old"), digest("new"), 1, ("r0", "r1"))
    ledger = PairedBlockRerunLedgerV3(registration)
    results = {"fact": digest("fact-result"), "reference": digest("reference-result")}
    receipt = ledger.append(
        attempt_id="attempt-1", executed_branch_ids=("fact", "reference"),
        branch_result_digests=results, completion="complete")
    row = ScheduledRunV3.from_paired_attempt(
        receipt, ledger=ledger, branch_id="fact", values=(1,), run_id="pair/0",
        instance_id="pair", stratum="cell", repeat_id="0",
        checkpoint_digest=digest("checkpoint"), evidence_digest=results["fact"])
    assert row.status == "observed"
    assert (row.paired_block_id, row.paired_attempt_id) == ("block", "attempt-1")


def test_d2_unknown_completion_preserves_known_repeats_before_instance_mean():
    rows = [
        ScheduledRunV3("i/0", "i", "cell", "0", "cp", "e", (None, 1, 1, 1), "unknown-record",
                       paired_block_id="block", paired_attempt_id="attempt"),
        ScheduledRunV3("i/1", "i", "cell", "1", "cp", "e", (1, 1, 1, 1)),
        ScheduledRunV3("i/2", "i", "cell", "2", "cp", "e", (1, 1, 1, 1)),
    ]
    report = analyze_dimension("D2", rows, stratum_weights={"cell": 1}, resamples=100)
    assert report["nominal_extremes"] == pytest.approx((1., 2/3))
    assert report["undefined_completion_possible"] is False
    assert [endpoint["endpoint_role"] for endpoint in report["endpoints"]] == ["low_completion", "high_completion"]
    assert all(endpoint["alpha"] == .05 and endpoint["family_size"] == 3 for endpoint in report["endpoints"])
    assert all(len(endpoint["retention_bands"]) == 3 for endpoint in report["endpoints"])


def test_d2_high_completion_selects_smallest_weight_unknown_reference():
    rows = []
    for instance, stratum in (("heavy", "heavy"), ("light", "light")):
        for repeat in range(3):
            reference = None if repeat == 0 else 0
            status = "unknown-record" if repeat == 0 else "observed"
            rows.append(ScheduledRunV3(
                f"{instance}/{repeat}", instance, stratum, str(repeat), "cp", "e",
                (reference, 1, 1, 1), status,
                paired_block_id="block" if repeat == 0 else "",
                paired_attempt_id=f"{instance}-attempt" if repeat == 0 else ""))
    report = analyze_dimension("D2", rows, stratum_weights={"heavy": .9, "light": .1}, resamples=100)
    # Low completion gives both unknown reference runs credit: (.9+.1)/3.
    # High completion keeps only the light-stratum run: .1/3.
    assert report["nominal_extremes"] == pytest.approx((1/3, .1/3))
    assert report["undefined_completion_possible"] is True
