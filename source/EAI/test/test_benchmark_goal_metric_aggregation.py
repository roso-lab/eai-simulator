"""Tests for demo.benchmark.goal_metric_aggregation (protocol 2.6 batch 3).

Covers the cross-episode aggregation semantics required by protocol
section 2.6: the base-instance macro average counterexample (epoch pooling
would inflate the rate), Wilson bounds cross-checked against the existing
deployment-module interval, deterministic stratified bootstrap CIs,
paired differences, the episode-clustered stall aggregation counterexample
versus event pooling, occupied-time-weighted mutex aggregation, the
development/formal evidence-source gate (no approved state), the
six-dimension top-level report with bucketing and coverage, the N/A
discipline (empty set / all censored / zero denominators), and the
fail-closed input validation (duplicate instance ids, unknown metric
names).  Release flags must be false everywhere.
"""

import math

import pytest

from demo.benchmark.goal_metric_aggregation import (
    AGGREGATION_SCHEMA,
    BINARY_RATE_METRICS,
    CONTINUOUS_METRICS,
    DEFAULT_BOOTSTRAP_SEED,
    DIMENSIONS,
    EVIDENCE_BUCKETS,
    FORMAL_PROMOTION_STATUS,
    GATE_STATUSES,
    NA_ALL_MISSING,
    NA_DIMENSION_SOURCE_NOT_WIRED,
    NA_EMPTY_SET,
    NA_NO_EVENTS,
    NA_ZERO_CONFIRMED_STALLS,
    NA_ZERO_OCCUPANCY,
    PAIRED_METRICS,
    SOURCE_BINDING_FIELDS,
    UNVERIFIED_ANCHOR_SOURCE,
    aggregate_binary_rate,
    aggregate_continuous,
    aggregate_mutex,
    aggregate_paired_difference,
    aggregate_stall_metrics,
    build_protocol_metrics_report,
    evidence_source_gate,
)
from demo.benchmark.goal_protocol_deployment import (
    wilson_95_interval as deployment_wilson,
)


_SHA = "a" * 64


def _bindings(episode_id="ep0", **overrides):
    values = {
        "contract_sha256": _SHA,
        "episode_id": episode_id,
        "evaluator_ref": "evaluator-01",
        "termination_anchor_source": "host_event",
    }
    values.update(overrides)
    return values


def _stall_row(episode_id, indicator, *, stalls=(), tilde_tau=None,
               delta=None, m_stall=None, l_stall_obs=None):
    pair = None
    if tilde_tau is not None or delta is not None:
        pair = {"tilde_tau_det_s": tilde_tau, "delta_i": delta}
    row = {
        "episode_id": episode_id,
        "S_i_stall_indicator": indicator,
        "stalls": list(stalls),
        "first_stall_right_censored_pair": pair,
        "L_stall_obs_s": l_stall_obs,
    }
    if m_stall is not None:
        row["M_stall"] = m_stall
    return row


def _recovery_row(observed, l_obs=5.0):
    return {"L_obs_s": l_obs, "recovery_observed": observed}


# ---------------------------------------------------------------------------
# Binary-rate macro aggregation
# ---------------------------------------------------------------------------

def test_binary_rate_macro_average_not_epoch_pooling():
    # One instance with 3 epochs all 1 and one instance with 2 epochs all
    # 0: the base-instance macro average is 0.5, while pooling the five
    # epochs would wrongly give 5/5 = 1.0.
    rows = [
        {"base_instance_id": "i1", "epoch_rows": [1, 1, 1]},
        {"base_instance_id": "i2", "epoch_rows": [0, 0]},
    ]
    result = aggregate_binary_rate(rows,
                                   metric_name="team_state_infeasibility_detection_rate")
    assert result["status"] == "computed"
    assert result["macro_rate"] == 0.5
    assert result["instance_count"] == 2
    assert result["numerator_epochs"] == 3
    assert result["denominator_epochs"] == 5
    assert result["pooled_epoch_rate"] == 0.6
    assert result["pooled_epoch_rate"] != result["macro_rate"]


def test_binary_rate_wilson_bounds_match_deployment_module():
    rows = [
        {"base_instance_id": f"i{index}", "epoch_rows": [1, 1, 0, 0, 1]}
        for index in range(8)
    ]
    result = aggregate_binary_rate(rows, metric_name="MutexAny")
    interval = result["wilson_95_ci"]
    assert interval["status"] == "defined"
    assert result["macro_rate"] == 0.6
    assert 0.0 <= interval["lower"] <= result["macro_rate"]
    assert result["macro_rate"] <= interval["upper"] <= 1.0
    # The Wilson interval is evaluated at the base-instance level (8 units,
    # fractional effective numerator 4.8), so it is WIDER than the pooled
    # epoch interval on 12/40: the instance-level rule must not borrow
    # precision from non-independent epochs.
    pooled = deployment_wilson(12, 8 * 5)
    assert interval["lower"] > pooled["lower"]
    assert interval["upper"] > pooled["upper"]
    # When the effective numerator is integral the interval must reproduce
    # the deployment-module Wilson formula exactly.
    integral = aggregate_binary_rate(
        [{"base_instance_id": f"i{index}", "epoch_rows": [1, 1]}
         for index in range(5)],
        metric_name="MutexAny")
    assert integral["wilson_95_ci"] == deployment_wilson(5, 5)
    # All-success keeps the upper bound at 1.0 and zero-success the lower
    # bound at 0.0 (no overshoot past the parameter space).
    all_success = aggregate_binary_rate(
        [{"base_instance_id": "a", "epoch_rows": [1, 1]},
         {"base_instance_id": "b", "epoch_rows": [1]}],
        metric_name="MutexAny")
    assert all_success["wilson_95_ci"]["upper"] == 1.0
    no_success = aggregate_binary_rate(
        [{"base_instance_id": "a", "epoch_rows": [0, 0]}],
        metric_name="MutexAny")
    assert no_success["wilson_95_ci"]["lower"] == 0.0


def test_binary_rate_accepts_instance_mean_and_count_forms():
    via_mean = aggregate_binary_rate(
        [{"base_instance_id": "i1", "instance_mean": 1.0},
         {"base_instance_id": "i2", "instance_mean": 0.25}],
        metric_name="false_deployable_rate")
    via_counts = aggregate_binary_rate(
        [{"base_instance_id": "i1", "numerator": 2, "denominator": 2},
         {"base_instance_id": "i2", "numerator": 1, "denominator": 4}],
        metric_name="false_deployable_rate")
    assert via_mean["macro_rate"] == via_counts["macro_rate"] == 0.625
    assert via_counts["numerator_epochs"] == 3
    assert via_counts["denominator_epochs"] == 6
    assert via_mean["unit"] == "base_instance"
    assert "epoch" in via_counts["within_instance_rule"]


def test_binary_rate_na_empty_set_and_all_missing():
    empty = aggregate_binary_rate([], metric_name="StallRate")
    assert empty["status"] == NA_EMPTY_SET
    assert empty["macro_rate"] is None
    assert empty["wilson_95_ci"]["lower"] is None
    all_missing = aggregate_binary_rate(
        [{"base_instance_id": "i1", "epoch_rows": []},
         {"base_instance_id": "i2", "epoch_rows": []}],
        metric_name="StallRate")
    assert all_missing["status"] == NA_ALL_MISSING
    assert all_missing["macro_rate"] is None
    assert all_missing["missing_instance_count"] == 2
    assert all_missing["missing_instance_ids"] == ["i1", "i2"]


# ---------------------------------------------------------------------------
# Continuous aggregation
# ---------------------------------------------------------------------------

def test_continuous_macro_mean_of_means():
    rows = [
        {"base_instance_id": "i1", "epoch_rows": [1.0, 2.0]},  # mean 1.5
        {"base_instance_id": "i2", "epoch_rows": [3.0]},       # mean 3.0
    ]
    result = aggregate_continuous(rows, metric_name="AUP")
    assert result["status"] == "computed"
    # Macro mean of instance means (2.25), not the pooled epoch mean (2.0).
    assert result["macro_mean"] == 2.25
    assert result["instance_mean_min"] == 1.5
    assert result["instance_mean_max"] == 3.0
    assert [entry["n_repeats"] for entry in result["instances"]] == [2, 1]


def test_continuous_bootstrap_deterministic_same_seed():
    # Continuous, irregular repeat values keep bootstrap replicates off any
    # coarse grid so cross-seed equality cannot happen by coincidence.
    rows = [
        {"base_instance_id": f"i{index}",
         "epoch_rows": [index * 0.123456 + 0.1011,
                        index * 0.123456 + 0.3579,
                        index * 0.123456 + 0.6423]}
        for index in range(5)
    ]
    first = aggregate_continuous(rows, metric_name="Q_H", seed=7)
    second = aggregate_continuous(rows, metric_name="Q_H", seed=7)
    other = aggregate_continuous(rows, metric_name="Q_H", seed=8)
    assert first["bootstrap"]["seed"] == 7 == second["bootstrap"]["seed"]
    assert first["bootstrap"] == second["bootstrap"]
    assert (other["bootstrap"]["ci_lower"], other["bootstrap"]["ci_upper"]) != (
        first["bootstrap"]["ci_lower"], first["bootstrap"]["ci_upper"])
    assert first["bootstrap"]["ci_lower"] <= first["macro_mean"]
    assert first["macro_mean"] <= first["bootstrap"]["ci_upper"]
    # Default seed is a fixed constant, so default runs are reproducible.
    assert aggregate_continuous(rows, metric_name="Q_H")[
        "bootstrap"]["seed"] == DEFAULT_BOOTSTRAP_SEED


def test_continuous_stratified_bootstrap_declares_resampling_order():
    rows = [{"base_instance_id": "i1", "epoch_rows": [0.0, 1.0]},
            {"base_instance_id": "i2", "instance_mean": 0.5}]
    result = aggregate_continuous(rows, metric_name="nPGI_exec")
    assert result["bootstrap"]["resampling"].startswith("stratified")
    assert "base instances first" in result["bootstrap"]["resampling"]
    assert "within-instance repeats" in result["bootstrap"]["resampling"]
    assert result["bootstrap"]["iterations"] == 2000


def test_continuous_na_labels():
    assert aggregate_continuous([], metric_name="AUP")["status"] == NA_EMPTY_SET
    missing = aggregate_continuous(
        [{"base_instance_id": "i1", "epoch_rows": []}],
        metric_name="AUP")
    assert missing["status"] == NA_ALL_MISSING
    assert missing["bootstrap"]["ci_lower"] is None


# ---------------------------------------------------------------------------
# Paired differences
# ---------------------------------------------------------------------------

def test_paired_difference_mean_and_bootstrap():
    pairs = [
        {"base_instance_id": "i1", "treated": [0.8, 0.6], "reference": 0.5},
        {"base_instance_id": "i2", "treated": 0.4, "reference": [0.5, 0.3]},
        {"base_instance_id": "i3", "treated": 0.7, "reference": 0.7},
    ]
    result = aggregate_paired_difference(pairs, metric_name="AUP", seed=3)
    assert result["status"] == "computed"
    # d_1 = 0.7 - 0.5 = 0.2, d_2 = 0.4 - 0.4 = 0.0, d_3 = 0.0.
    assert math.isclose(result["mean_difference"], 0.2 / 3)
    assert [entry["d"] for entry in result["paired_differences"]] == pytest.approx(
        [0.2, 0.0, 0.0])
    bootstrap = result["bootstrap"]
    assert bootstrap["ci_lower"] <= result["mean_difference"] <= bootstrap["ci_upper"]
    assert bootstrap["seed"] == 3
    assert "reference-validity" in result["reference_validity_rule"]


def test_paired_difference_empty_and_malformed():
    empty = aggregate_paired_difference([], metric_name="Q_H")
    assert empty["status"] == NA_EMPTY_SET
    assert empty["mean_difference"] is None
    try:
        aggregate_paired_difference(
            [{"base_instance_id": "i1", "treated": 1.0}], metric_name="Q_H")
    except ValueError as error:
        assert "reference" in str(error)
    else:
        raise AssertionError("missing reference arm must raise")


# ---------------------------------------------------------------------------
# Stall aggregation (episode unit, episode-clustered CI)
# ---------------------------------------------------------------------------

def test_stall_rate_episode_unit_with_right_censored_pairs():
    episodes = [
        _stall_row("e1", 1, stalls=[_recovery_row(False)],
                   tilde_tau=30.0, delta=1, m_stall=1, l_stall_obs=42.0),
        _stall_row("e2", 0, stalls=[], tilde_tau=100.0, delta=0),
        _stall_row("e3", 0, stalls=[], tilde_tau=80.0, delta=0),
    ]
    result = aggregate_stall_metrics(episodes)
    assert result["status"] == "computed"
    assert result["unit"] == "episode"
    # StallRate keeps stall-free episodes in the denominator.
    assert result["StallRate"]["numerator"] == 1
    assert result["StallRate"]["denominator"] == 3
    assert result["StallRate"]["rate"] == 1 / 3
    # First-stall pair set: one event, two censored.
    assert result["first_stall_event_count"] == 1
    assert result["first_stall_censored_count"] == 2
    assert len(result["first_stall_right_censored_pairs"]) == 3
    # All-censored time distribution side: with events present the
    # distribution is computable; P(T_det<=H) equals the StallRate view.
    assert result["P_T_det_le_H"] == 1 / 3


def test_stall_all_censored_time_distribution_is_na():
    episodes = [
        _stall_row("e1", 0, stalls=[], tilde_tau=100.0, delta=0),
        _stall_row("e2", 0, stalls=[], tilde_tau=90.0, delta=0),
    ]
    result = aggregate_stall_metrics(episodes)
    # Zero events: the censored time distribution is not estimable, but the
    # within-H probability stays defined at 0 (episodes not deleted).
    assert result["first_stall_time_distribution"]["status"] == NA_NO_EVENTS
    assert result["first_stall_event_count"] == 0
    assert result["P_T_det_le_H"] == 0.0
    assert result["StallRate"]["denominator"] == 2


def test_stall_cluster_bootstrap_counterexample_vs_event_pooling():
    # ep1 contributes 10 unrecovered stalls, ep2 one recovered stall.  The
    # point estimate is the pooled formula 1/11, but the CI must reflect
    # the two episode clusters: bootstrap replicates that draw only ep2
    # give rate 1.0, so the cluster CI upper bound reaches 1.0 while the
    # event-pooled Wilson interval on 1/11 stays far below.
    episodes = [
        _stall_row("heavy", 1,
                   stalls=[_recovery_row(False)] * 10,
                   tilde_tau=20.0, delta=1, m_stall=10),
        _stall_row("light", 1,
                   stalls=[_recovery_row(True, l_obs=4.0)],
                   tilde_tau=10.0, delta=1, m_stall=1),
    ]
    result = aggregate_stall_metrics(episodes)
    recovery = result["StallRecoveryObserved"]
    assert recovery["numerator"] == 1
    assert recovery["denominator"] == 11
    assert recovery["rate"] == 1 / 11
    ci = recovery["bootstrap_ci"]
    assert ci["ci_upper"] == 1.0
    assert ci["ci_lower"] == 0.0
    pooled_wilson = deployment_wilson(1, 11)
    assert ci["ci_upper"] > pooled_wilson["upper"]
    assert recovery["ci_scope"] == "episode_cluster_bootstrap"


def test_stall_na_zero_confirmed_stalls_and_empty_set():
    no_stalls = aggregate_stall_metrics(
        [_stall_row("e1", 0, stalls=[], tilde_tau=50.0, delta=0)])
    recovery = no_stalls["StallRecoveryObserved"]
    assert recovery["status"] == NA_ZERO_CONFIRMED_STALLS
    assert recovery["rate"] is None
    assert recovery["denominator"] == 0
    empty = aggregate_stall_metrics([])
    assert empty["status"] == NA_EMPTY_SET
    assert empty["StallRate"]["rate"] is None


def test_stall_incomplete_right_censored_pairs_counted():
    episodes = [
        _stall_row("e1", 1, stalls=[_recovery_row(True)],
                   tilde_tau=25.0, delta=1, m_stall=1),
        # Pair present but tilde_tau missing: incomplete, excluded, counted.
        _stall_row("e2", 0, stalls=[],
                   tilde_tau=None, delta=0),
        # No pair block at all.
        {"episode_id": "e3", "S_i_stall_indicator": 0, "stalls": []},
    ]
    result = aggregate_stall_metrics(episodes)
    assert result["incomplete_right_censored_pair_count"] == 2
    pairs = result["first_stall_right_censored_pairs"]
    assert [pair["episode_id"] for pair in pairs] == ["e1"]
    assert result["first_stall_event_count"] == 1
    # StallRate still counts all three episodes.
    assert result["StallRate"]["denominator"] == 3


# ---------------------------------------------------------------------------
# Mutex aggregation
# ---------------------------------------------------------------------------

def test_mutex_any_episode_rate_and_overlap_time_weighting():
    rows = [
        {"episode_id": "e1", "MutexAny_episode": True,
         "overlap_time_s": 10.0, "occupied_time_s": 20.0},
        {"episode_id": "e2", "MutexAny_episode": False,
         "overlap_time_s": 0.0, "occupied_time_s": 80.0},
    ]
    result = aggregate_mutex(rows)
    assert result["MutexAny"]["numerator"] == 1
    assert result["MutexAny"]["denominator"] == 2
    assert result["MutexAny"]["rate"] == 0.5
    assert result["MutexAny"]["wilson_95_ci"] == deployment_wilson(1, 2)
    # Occupied-time weighting: 10/100 = 0.1, not the 0.25 mean of the two
    # per-episode ratios (0.5 and 0.0).
    overlap = result["MutexOverlap"]
    assert overlap["numerator_time_s"] == 10.0
    assert overlap["denominator_time_s"] == 100.0
    assert overlap["rate"] == 0.1
    assert overlap["rate"] != 0.25
    assert overlap["bootstrap_ci"]["ci_lower"] <= 0.1 <= overlap["bootstrap_ci"]["ci_upper"]


def test_mutex_na_zero_occupancy():
    rows = [
        {"episode_id": "e1", "MutexAny_episode": False,
         "overlap_time_s": 0.0, "occupied_time_s": 0.0},
        {"episode_id": "e2", "MutexAny_episode": False,
         "overlap_time_s": 0.0, "occupied_time_s": 0.0},
    ]
    result = aggregate_mutex(rows)
    overlap = result["MutexOverlap"]
    assert overlap["status"] == NA_ZERO_OCCUPANCY
    assert overlap["rate"] is None
    assert overlap["episodes_without_occupancy_count"] == 2
    # The any-violation rate stays defined.
    assert result["MutexAny"]["rate"] == 0.0
    assert aggregate_mutex([])["status"] == NA_EMPTY_SET


# ---------------------------------------------------------------------------
# Evidence-source gate
# ---------------------------------------------------------------------------

def test_gate_missing_binding_routes_to_development_bucket():
    reports = [
        {"source_bindings": _bindings("ep1")},
        {"source_bindings": _bindings("ep2", evaluator_ref="")},
        {"source_bindings": _bindings("ep3", contract_sha256="not-a-hash")},
    ]
    gate = evidence_source_gate(reports, required_bindings=SOURCE_BINDING_FIELDS)
    assert gate["formal_eligible_count"] == 1
    assert gate["development_evidence_count"] == 2
    assert gate["gate_status"] == "development_input_present"
    development = gate["development_evidence"]
    assert development[0]["missing_bindings"] == ["evaluator_ref_missing"]
    assert development[1]["missing_bindings"] == [
        "contract_sha256_missing_or_invalid"]
    assert gate["formal_eligible"][0]["episode_id"] == "ep1"


def test_gate_all_bindings_formal_eligible():
    reports = [{"source_bindings": _bindings("ep%d" % index)}
               for index in range(3)]
    gate = evidence_source_gate(reports, required_bindings=SOURCE_BINDING_FIELDS)
    assert gate["formal_eligible_count"] == 3
    assert gate["development_evidence_count"] == 0
    assert gate["gate_status"] == "formal_eligible"
    assert gate["development_evidence"] == []
    # Top-level binding fields are accepted as an alternative location.
    top_level = evidence_source_gate(
        [_bindings("ep9")], required_bindings=SOURCE_BINDING_FIELDS)
    assert top_level["formal_eligible_count"] == 1


def test_gate_no_approved_state_and_release_action_marker():
    gate = evidence_source_gate([{"source_bindings": _bindings()}],
                                required_bindings=SOURCE_BINDING_FIELDS)
    assert "approved" not in gate["statuses"]
    assert set(gate["statuses"]) == set(GATE_STATUSES)
    assert gate["no_approved_state"] is True
    assert gate["formal_promotion"] == FORMAL_PROMOTION_STATUS
    assert list(EVIDENCE_BUCKETS) == ["development_evidence",
                                      "formal_eligible"]
    assert gate["release"] is False


def test_gate_unverified_anchor_stays_development():
    gate = evidence_source_gate(
        [{"source_bindings": _bindings(termination_anchor_source=
                                       UNVERIFIED_ANCHOR_SOURCE)}],
        required_bindings=SOURCE_BINDING_FIELDS)
    assert gate["development_evidence_count"] == 1
    assert gate["development_evidence"][0]["missing_bindings"] == [
        "termination_anchor_source_unverified"]


def test_gate_rejects_unknown_or_empty_bindings():
    for bad in (["not_a_binding"], []):
        try:
            evidence_source_gate([], required_bindings=bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"required_bindings={bad} must raise")


# ---------------------------------------------------------------------------
# Top-level six-dimension report
# ---------------------------------------------------------------------------

def _episode_report(episode_id, base_instance_id, *, formal=True,
                    q_h=1.0, detection=(1, 2)):
    report = {
        "episode_id": episode_id,
        "base_instance_id": base_instance_id,
        "deployment_rates": {"rates": {
            "team_state_infeasibility_detection_rate": {
                "numerator": detection[0], "denominator": detection[1]},
            "false_non_deployable_rate": {"numerator": 0,
                                          "denominator": 0},
            "false_deployable_rate": {"numerator": 0, "denominator": 0},
            "pre_dispatch_safe_stop_rate": {"numerator": 0,
                                            "denominator": 0},
            "assessment_latency": {"observed_latencies_s": [4.0, 6.0]},
        }},
        "cs_vector": {"vector": {"cap": 0.5, "role": 1.0, "mile": 0.25}},
        "temporal": {
            "mutex": {"MutexAny_episode": False, "overlap_time_s": 1.0,
                      "occupied_time_s": 10.0},
            "stall": dict(_stall_row(episode_id, 0, stalls=[],
                                     tilde_tau=50.0, delta=0)),
            "precedence": {"PrecE2E": 0.5},
            "synchronization": {"SyncE2E": 1.0},
            "handoff": {"continuity_rate": 1.0, "G_h_mean_s": 2.0},
        },
        "progress_curve": {"Q_H": q_h, "MCR": q_h, "AUP": 0.6 * q_h,
                           "nPGI_exec": 0.5 * q_h, "PGI_exec": 40.0},
    }
    if formal:
        report["source_bindings"] = _bindings(episode_id)
    return report


def test_top_level_report_structure_six_dimensions():
    report = build_protocol_metrics_report([
        _episode_report("ep1", "i1"),
        _episode_report("ep2", "i2", q_h=0.5),
    ])
    assert report["schema"] == AGGREGATION_SCHEMA
    assert report["release"] is False
    assert tuple(report["dimension_names"]) == DIMENSIONS
    assert set(report["dimensions"]) == set(DIMENSIONS)
    # Wired dimensions aggregated with the documented unit.
    assert report["dimensions"]["task_allocation"]["status"] == "computed"
    assert report["dimensions"]["temporal_coordination"]["status"] == "computed"
    assert report["dimensions"]["coordination_cost_efficiency"]["status"] == "computed"
    # Unwired dimensions are explicit N/A, never fabricated numbers.
    for dimension in ("communication_adaptation",
                      "dynamic_reconfiguration", "cross_episode_learning"):
        block = report["dimensions"][dimension]
        assert block["status"] == NA_DIMENSION_SOURCE_NOT_WIRED
        assert block["metrics"] == {}
    # Evidence gate embedded with per-component bucketing and coverage.
    gate = report["evidence_gate"]
    assert gate["formal_eligible_count"] == 2
    task = report["dimensions"]["task_allocation"]["metrics"]
    detection = task["team_state_infeasibility_detection_rate"]
    assert detection["aggregation"]["status"] == "computed"
    assert detection["aggregation"]["instances_used"] == 2
    assert detection["coverage"]["coverage_ratio"] == 1.0
    temporal = report["dimensions"]["temporal_coordination"]["metrics"]
    assert temporal["mutex"]["aggregation"]["MutexAny"]["rate"] == 0.0
    assert temporal["stall"]["aggregation"]["StallRate"]["rate"] == 0.0
    cost = report["dimensions"]["coordination_cost_efficiency"]["metrics"]
    assert cost["AUP"]["aggregation"]["macro_mean"] == pytest.approx(0.45)
    assert report["base_instance_count"] == 2
    assert report["censoring_rule"].startswith(
        "response/recovery/detection times")


def test_top_level_report_development_bucket_never_enters_formal_denominator():
    report = build_protocol_metrics_report([
        _episode_report("ep1", "i1", formal=True, q_h=1.0),
        _episode_report("ep2", "i2", formal=False, q_h=0.0),  # no bindings
    ])
    gate = report["evidence_gate"]
    assert gate["development_evidence_count"] == 1
    q_h = report["dimensions"]["coordination_cost_efficiency"]["metrics"]["Q_H"]
    aggregation = q_h["aggregation"]
    # Only the formal report enters the denominator: macro stays 1.0 and
    # the excluded development provider is counted, never averaged in.
    assert aggregation["instances_used"] == 1
    assert aggregation["macro_mean"] == 1.0
    assert q_h["coverage"]["development_excluded_episodes"] == 1
    assert q_h["coverage"]["episodes_providing_metric"] == 1
    # The same discipline holds for binary deployment-rate components.
    detection = report["dimensions"]["task_allocation"]["metrics"][
        "team_state_infeasibility_detection_rate"]
    assert detection["aggregation"]["instances_used"] == 1
    assert detection["coverage"]["development_excluded_episodes"] == 1


def test_top_level_report_within_instance_grouping():
    # Two formal episodes of the SAME base instance are repeats: the
    # instance contributes its within-instance mean once.
    report = build_protocol_metrics_report([
        _episode_report("ep1", "i1", q_h=1.0),
        _episode_report("ep2", "i1", q_h=0.4),
        _episode_report("ep3", "i2", q_h=0.7),
    ])
    q_h = report["dimensions"]["coordination_cost_efficiency"]["metrics"]["Q_H"]
    aggregation = q_h["aggregation"]
    assert aggregation["instance_count"] == 2
    # (mean(1.0, 0.4) + 0.7) / 2 = 0.7 -- not the pooled 0.7 nor 2.1/3.
    assert aggregation["macro_mean"] == (0.7 + 0.7) / 2
    report_repeat = build_protocol_metrics_report([
        _episode_report("ep1", "i1", q_h=1.0),
        _episode_report("ep2", "i1", q_h=0.4),
        _episode_report("ep3", "i2", q_h=0.7),
    ])
    assert (report_repeat["dimensions"]["coordination_cost_efficiency"]
            ["metrics"]["Q_H"]["aggregation"]["bootstrap"]
        == aggregation["bootstrap"])


def test_top_level_report_na_when_no_reports():
    report = build_protocol_metrics_report([])
    assert report["episode_report_count"] == 0
    assert report["evidence_gate"]["gate_status"] == "no_reports"
    assert report["dimensions"]["task_allocation"]["metrics"][
        "assessment_latency"]["aggregation"]["status"] == NA_EMPTY_SET


def test_top_level_report_rejects_duplicate_episode_ids():
    try:
        build_protocol_metrics_report([
            _episode_report("ep1", "i1"),
            _episode_report("ep1", "i2"),
        ])
    except ValueError as error:
        assert "duplicate_episode_id" in str(error)
    else:
        raise AssertionError("duplicate episode id must raise")


# ---------------------------------------------------------------------------
# Fail-closed validation and release discipline
# ---------------------------------------------------------------------------

def test_duplicate_instance_ids_rejected():
    duplicate_rows = [
        {"base_instance_id": "i1", "epoch_rows": [1]},
        {"base_instance_id": "i1", "epoch_rows": [0]},
    ]
    for aggregator, kwargs in (
        (aggregate_binary_rate, {"metric_name": "StallRate"}),
        (aggregate_continuous, {"metric_name": "AUP"}),
    ):
        try:
            aggregator(duplicate_rows, **kwargs)
        except ValueError as error:
            assert "duplicate_base_instance_id" in str(error)
        else:
            raise AssertionError(f"{aggregator.__name__} must reject dupes")
    try:
        aggregate_paired_difference(
            [{"base_instance_id": "i1", "treated": 1.0, "reference": 0.0},
             {"base_instance_id": "i1", "treated": 0.0, "reference": 1.0}],
            metric_name="AUP")
    except ValueError as error:
        assert "duplicate_base_instance_id" in str(error)
    else:
        raise AssertionError("paired aggregator must reject dupes")
    try:
        aggregate_stall_metrics([_stall_row("e1", 0), _stall_row("e1", 1)])
    except ValueError as error:
        assert "duplicate_episode_id" in str(error)
    else:
        raise AssertionError("stall aggregator must reject dupes")


def test_unknown_metric_names_rejected():
    for aggregator, vocabulary in (
        (aggregate_binary_rate, BINARY_RATE_METRICS),
        (aggregate_continuous, CONTINUOUS_METRICS),
        (aggregate_paired_difference, PAIRED_METRICS),
    ):
        try:
            aggregator([{"base_instance_id": "i1", "epoch_rows": [1]}],
                       metric_name="definitely_not_a_metric")
        except ValueError as error:
            assert "unknown" in str(error)
        else:
            raise AssertionError(f"{aggregator.__name__} must reject names")
        assert "definitely_not_a_metric" not in vocabulary
        # Every vocabulary name must be accepted structurally (parse-only
        # smoke via one representative call each is covered elsewhere).


def test_release_flags_false_everywhere():
    outputs = [
        aggregate_binary_rate([{"base_instance_id": "i1",
                                "epoch_rows": [1]}],
                              metric_name="StallRate"),
        aggregate_continuous([{"base_instance_id": "i1", "epoch_rows": [1.0]}],
                             metric_name="AUP"),
        aggregate_paired_difference(
            [{"base_instance_id": "i1", "treated": 1.0, "reference": 0.0}],
            metric_name="AUP"),
        aggregate_stall_metrics([_stall_row("e1", 0, stalls=[],
                                           tilde_tau=1.0, delta=0)]),
        aggregate_mutex([{"episode_id": "e1", "MutexAny_episode": False,
                          "overlap_time_s": 0.0, "occupied_time_s": 1.0}]),
        evidence_source_gate([{"source_bindings": _bindings()}],
                             required_bindings=SOURCE_BINDING_FIELDS),
        build_protocol_metrics_report([_episode_report("ep1", "i1")]),
    ]
    for output in outputs:
        assert output["release"] is False
        assert output["scoreable"] is False
        assert output["execution_validated"] is False
        assert output["business_outcomes_validated"] is False


def _bound_reports():
    return [{"source_bindings": _bindings("ep%d" % index)}
            for index in range(2)]


def test_gate_cannot_shrink_mandatory_bindings():
    # Viewer stage66 P0-1: dropping any mandatory field from the gate raises.
    from demo.benchmark.goal_metric_aggregation import evidence_source_gate

    for reduced in (("contract_sha256", "episode_id", "evaluator_ref"),
                    ("contract_sha256",)):
        with pytest.raises(ValueError, match="mandatory"):
            evidence_source_gate(_bound_reports(), required_bindings=reduced)


def test_gate_synthetic_episode_id_stays_development():
    # Viewer stage66 P0-2: an episode id derived from the row index is not
    # source evidence; the report lands in development_evidence.
    from demo.benchmark.goal_metric_aggregation import evidence_source_gate

    reports = _bound_reports()
    for report in reports:
        report.pop("episode_id", None)
        report.get("source_bindings", {}).pop("episode_id", None)
    gate = evidence_source_gate(reports, required_bindings=SOURCE_BINDING_FIELDS)
    assert gate["development_evidence_count"] == len(reports)
    assert gate["formal_eligible_count"] == 0


def test_gate_source_conflict_drops_to_development():
    # Viewer stage66 P0-3: source_bindings disagreeing with the top-level
    # field is a conflict — the report cannot enter the formal bucket.
    from demo.benchmark.goal_metric_aggregation import evidence_source_gate

    reports = _bound_reports()
    reports[0]["episode_id"] = "conflicting-id"
    gate = evidence_source_gate(reports, required_bindings=SOURCE_BINDING_FIELDS)
    row = next(item for item in gate["development_evidence"]
               if item["report_index"] == 0)
    assert row is not None
    assert "episode_id_missing" in row["missing_bindings"]


def test_gate_untrusted_anchor_source_stays_development():
    # Viewer stage66 P0-4: only host_event / native_receipt anchor sources
    # are formal-eligible; any other label is development-state.
    from demo.benchmark.goal_metric_aggregation import evidence_source_gate

    reports = _bound_reports()
    reports[0]["source_bindings"]["termination_anchor_source"] = "self_report"
    gate = evidence_source_gate(reports, required_bindings=SOURCE_BINDING_FIELDS)
    row = next(item for item in gate["development_evidence"]
               if item["report_index"] == 0)
    assert "termination_anchor_source_not_trusted" in row["missing_bindings"]
