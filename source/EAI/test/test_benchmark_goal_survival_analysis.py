"""Tests for demo.benchmark.goal_survival_analysis (protocol 2.6/3.3/3.5).

Covers the Kaplan-Meier semantics demanded by the protocol: a single-event
curve drops from 1 to 0 exactly at the event time, a censoring time shrinks
only the risk set (never S), tied censorings stay in an event time's risk
set, RMST is the hand-computable area of the step function, right censoring
is never extrapolated behind the last observation, the RMST difference
follows the protocol learning-gain direction RMST^C - RMST^R, the recovery
probability is 1 - S(ell_star), the two-point window gap matches the event
mass, the aggregator interface pools per-episode pair sets with an
episode-cluster bootstrap CI that is deterministic under a fixed seed, the
aggregator's mapping pair shapes are accepted, all-censored / empty inputs
are explicit N/A labels rather than zero values, malformed inputs raise
fail-closed, and release flags stay false everywhere.
"""

import math

import pytest

from demo.benchmark.goal_survival_analysis import (
    DEFAULT_BOOTSTRAP_ITERATIONS,
    DEFAULT_BOOTSTRAP_SEED,
    NA_ALL_MISSING,
    NA_ELL_STAR_BEYOND_HORIZON,
    NA_EMPTY_SET,
    NA_NO_EVENTS,
    NA_WINDOW_BEYOND_HORIZON,
    PROTOCOL_SECTION,
    SURVIVAL_SCHEMA,
    aggregate_survival,
    kaplan_meier_curve,
    recovery_probability,
    restricted_mean_gap,
    rmst,
    rmst_difference,
)


# ---------------------------------------------------------------------------
# Kaplan-Meier curve semantics
# ---------------------------------------------------------------------------

def test_km_single_event_two_points():
    # One observed event at t=5: S starts at 1 and jumps to 0 exactly at 5.
    curve = kaplan_meier_curve([(5.0, 1)], horizon_s=10.0)
    assert curve["estimator"] == "kaplan_meier"
    assert curve["times"] == [0.0, 5.0, 10.0]
    assert curve["S_t"] == [1.0, 0.0, 0.0]
    assert curve["n_at_risk"] == [1, 1, 0]
    assert curve["n_events"] == [0, 1, 0]
    assert curve["n_censored"] == [0, 0, 0]
    assert curve["n_events_total"] == 1
    assert curve["n_censored_total"] == 0
    # Monotone non-increasing step function from 1 on [0, H].
    assert all(a >= b for a, b in zip(curve["S_t"], curve["S_t"][1:]))
    assert curve["times"][0] == 0.0
    assert curve["times"][-1] == 10.0


def test_km_censoring_reduces_risk_set_not_survival():
    # A censoring at t=5 does not move S; the subject leaves the risk set,
    # so the event at t=8 faces a risk set of 1, not 2.
    curve = kaplan_meier_curve([(5.0, 0), (8.0, 1)], horizon_s=10.0)
    assert curve["times"] == [0.0, 5.0, 8.0, 10.0]
    assert curve["S_t"] == [1.0, 1.0, 0.0, 0.0]
    assert curve["n_at_risk"] == [2, 2, 1, 0]
    assert curve["n_censored"] == [0, 1, 0, 0]
    assert curve["n_events"] == [0, 0, 1, 0]
    # S at the censoring time itself is unchanged: no drop without an event.
    assert curve["S_t"][curve["times"].index(5.0)] == 1.0


def test_km_tie_event_and_censoring_at_same_time():
    # Events at t precede censoring removal at t: the censored subject
    # stays in the risk set of the event tied at its own censoring time.
    curve = kaplan_meier_curve([(4.0, 1), (6.0, 1), (6.0, 0), (9.0, 1)],
                               horizon_s=12.0)
    assert curve["times"] == [0.0, 4.0, 6.0, 9.0, 12.0]
    # t=4: risk 4 (the event subject is at risk at its own event time),
    # d=1 -> 3/4; t=6: risk 3 (tied event + tied censor + t=9), d=1 ->
    # 3/4 * 2/3 = 1/2; t=9: risk 1, d=1 -> 0.
    assert curve["S_t"] == pytest.approx([1.0, 0.75, 0.5, 0.0, 0.0])
    assert curve["n_at_risk"] == [4, 4, 3, 1, 0]
    assert curve["n_events"] == [0, 1, 1, 1, 0]
    assert curve["n_censored"] == [0, 0, 1, 0, 0]


# ---------------------------------------------------------------------------
# RMST
# ---------------------------------------------------------------------------

def test_rmst_hand_computed_single_event():
    # S=1 until t=5 then S=0: RMST(10) = 1*5 + 0*5 = 5.
    curve = kaplan_meier_curve([(5.0, 1)], horizon_s=10.0)
    assert rmst(curve, horizon_s=10.0) == pytest.approx(5.0)


def test_rmst_weighted_step_area_hand_computed():
    curve = kaplan_meier_curve([(4.0, 1), (6.0, 1), (6.0, 0), (9.0, 1)],
                               horizon_s=12.0)
    # Area = 4*1 + 2*(3/4) + 3*(1/2) + 3*0 = 7.
    assert rmst(curve, horizon_s=12.0) == pytest.approx(7.0)
    # A shorter restricted horizon inside the curve window integrates only
    # that prefix of the step function: [0, 6] -> 4 + 2*(3/4) = 5.5.
    assert rmst(curve, horizon_s=6.0) == pytest.approx(5.5)


def test_rmst_no_extrapolation_after_last_censoring():
    # All pairs censored before H: the curve is carried flat to H and S is
    # never assumed to be 0 behind a censored observation, so RMST = H and
    # the risk set behind the last observation is empty (disclosed, not
    # extrapolated).
    curve = kaplan_meier_curve([(5.0, 0), (7.0, 0)], horizon_s=10.0)
    assert curve["times"] == [0.0, 5.0, 7.0, 10.0]
    assert curve["S_t"] == [1.0, 1.0, 1.0, 1.0]
    assert curve["n_at_risk"][-1] == 0
    assert rmst(curve, horizon_s=10.0) == pytest.approx(10.0)


def test_rmst_rejects_horizon_beyond_curve():
    curve = kaplan_meier_curve([(5.0, 1)], horizon_s=10.0)
    with pytest.raises(ValueError, match="extrapolat"):
        rmst(curve, horizon_s=10.5)


def test_rmst_difference_learning_gain_direction():
    # Protocol 3.5: LG_RMST = RMST^C - RMST^R.  The learning condition R
    # completes at t=2, the no-experience baseline C at t=8: the difference
    # is positive and equals 8 - 2 = 6.
    baseline_c = kaplan_meier_curve([(8.0, 1)], horizon_s=10.0)
    learning_r = kaplan_meier_curve([(2.0, 1)], horizon_s=10.0)
    difference = rmst_difference(baseline_c, learning_r, horizon_s=10.0)
    assert difference["rmst_condition_a"] == pytest.approx(8.0)
    assert difference["rmst_condition_b"] == pytest.approx(2.0)
    assert difference["difference"] == pytest.approx(6.0)
    assert difference["difference"] > 0
    assert "LG" in difference["definition"]
    # Reversing the arms flips the sign: the API never takes absolute value.
    flipped = rmst_difference(learning_r, baseline_c, horizon_s=10.0)
    assert flipped["difference"] == pytest.approx(-6.0)


# ---------------------------------------------------------------------------
# Recovery probability and window gap
# ---------------------------------------------------------------------------

def test_recovery_probability_values():
    curve = kaplan_meier_curve([(3.0, 1), (7.0, 1)], horizon_s=10.0)
    # t=3: risk 2, d=1 -> S=1/2; t=7: risk 1, d=1 -> S=0.
    assert curve["S_t"] == pytest.approx([1.0, 0.5, 0.0, 0.0])
    within_5 = recovery_probability(curve, ell_star=5.0)
    assert within_5["status"] == "computed"
    assert within_5["S_at_ell_star"] == pytest.approx(0.5)
    assert within_5["probability"] == pytest.approx(0.5)
    assert recovery_probability(curve, ell_star=7.0)["probability"] == \
        pytest.approx(1.0)
    assert recovery_probability(curve, ell_star=2.0)["probability"] == \
        pytest.approx(0.0)
    # ell_star on the curve boundary is estimable.
    assert recovery_probability(curve, ell_star=10.0)["probability"] == \
        pytest.approx(1.0)


def test_recovery_probability_beyond_horizon_is_na():
    curve = kaplan_meier_curve([(3.0, 1)], horizon_s=10.0)
    result = recovery_probability(curve, ell_star=15.0)
    assert result["status"] == NA_ELL_STAR_BEYOND_HORIZON
    assert result["probability"] is None
    assert result["S_at_ell_star"] is None


def test_restricted_mean_gap_window_mass_and_recovery_equivalence():
    curve = kaplan_meier_curve([(3.0, 1), (7.0, 1)], horizon_s=10.0)
    # S(lo) - S(hi) is the event mass inside (lo, hi].
    window = restricted_mean_gap(curve, lo=3.0, hi=7.0)
    assert window["status"] == "computed"
    assert window["S_lo"] == pytest.approx(0.5)
    assert window["S_hi"] == pytest.approx(0.0)
    assert window["gap"] == pytest.approx(0.5)
    assert restricted_mean_gap(curve, lo=0.0, hi=10.0)["gap"] == \
        pytest.approx(1.0)
    # With lo=0 the gap is exactly the preregistered recovery probability.
    for ell_star in (2.0, 5.0, 7.0, 10.0):
        gap = restricted_mean_gap(curve, lo=0.0, hi=ell_star)["gap"]
        probability = recovery_probability(curve, ell_star=ell_star)["probability"]
        assert gap == pytest.approx(probability)
    # A window beyond the horizon is an explicit N/A, not an extrapolation.
    beyond = restricted_mean_gap(curve, lo=0.0, hi=15.0)
    assert beyond["status"] == NA_WINDOW_BEYOND_HORIZON
    assert beyond["gap"] is None


# ---------------------------------------------------------------------------
# Aggregator interface
# ---------------------------------------------------------------------------

def test_aggregate_survival_end_to_end():
    episodes = [
        [(4.0, 1), (6.0, 0)],
        [(2.0, 1), (9.0, 1)],
        [(6.0, 0), (10.0, 0)],
    ]
    result = aggregate_survival(episodes, horizon_s=10.0, ell_star=5.0,
                                bootstrap_iterations=400, seed=0)
    assert result["schema"] == SURVIVAL_SCHEMA
    assert result["status"] == "computed"
    assert result["unit"] == "episode_pair_set"
    assert result["episode_count"] == 3
    assert result["episodes_with_pairs"] == 3
    assert result["n_events"] == 3
    assert result["n_censored"] == 3
    # Pooled KM over the merged pair set (protocol-allowed direct merge):
    # t=2: 6 at risk, d=1 -> 5/6; t=4: 5 at risk, d=1 -> 2/3;
    # t=6: censoring only; t=9: 2 at risk, d=1 -> 1/3.
    curve = result["pooled_curve"]
    assert curve["times"] == [0.0, 2.0, 4.0, 6.0, 9.0, 10.0]
    assert curve["S_t"] == pytest.approx(
        [1.0, 5 / 6, 2 / 3, 2 / 3, 1 / 3, 1 / 3])
    # RMST = 2*1 + 2*(5/6) + 5*(2/3) + 1*(1/3) = 22/3.
    assert result["rmst_H"] == pytest.approx(22 / 3)
    recovery = result["recovery_prob_at_ell_star"]
    assert recovery["probability"] == pytest.approx(1 / 3)
    # Episode-cluster bootstrap CI brackets the point estimate and stays
    # inside the feasible [0, H] range.
    bootstrap = result["bootstrap"]
    assert bootstrap["seed"] == 0
    assert bootstrap["iterations"] == 400
    assert 0.0 <= bootstrap["ci_lower"] <= result["rmst_H"] <= \
        bootstrap["ci_upper"] <= 10.0
    assert "cluster" in bootstrap["resampling"]
    assert bootstrap["recovery_ci"]["ci_lower"] <= 1 / 3 <= \
        bootstrap["recovery_ci"]["ci_upper"]
    assert result["release"] is False


def test_aggregate_all_censored_is_na_no_events():
    # Zero events: the censored time distribution is not estimable; RMST is
    # an explicit N/A, not 0 and not the horizon.
    result = aggregate_survival([[(5.0, 0), (8.0, 0)]], horizon_s=10.0,
                                ell_star=4.0)
    assert result["status"] == NA_NO_EVENTS
    assert result["n_events"] == 0
    assert result["n_censored"] == 2
    assert result["rmst_H"] is None
    assert result["pooled_curve"] is None
    assert result["recovery_prob_at_ell_star"] is None
    assert result["bootstrap"]["ci_lower"] is None
    assert result["bootstrap"]["status"] == NA_NO_EVENTS


def test_aggregate_empty_set_and_all_empty_episodes_na():
    empty = aggregate_survival([], horizon_s=10.0)
    assert empty["status"] == NA_EMPTY_SET
    assert empty["n_events"] is None
    assert empty["n_censored"] is None
    assert empty["rmst_H"] is None
    all_empty = aggregate_survival([[], []], horizon_s=10.0)
    assert all_empty["status"] == NA_ALL_MISSING
    assert all_empty["empty_episode_count"] == 2
    assert all_empty["episodes_with_pairs"] == 0
    assert all_empty["rmst_H"] is None


def test_bootstrap_ci_deterministic_same_seed():
    episodes = [
        [(4.0, 1), (6.0, 0)],
        [(2.0, 1), (9.0, 1)],
        [(6.0, 0), (10.0, 0)],
    ]
    first = aggregate_survival(episodes, horizon_s=10.0,
                               bootstrap_iterations=300, seed=7)
    second = aggregate_survival(episodes, horizon_s=10.0,
                                bootstrap_iterations=300, seed=7)
    other = aggregate_survival(episodes, horizon_s=10.0,
                               bootstrap_iterations=300, seed=8)
    assert (first["bootstrap"]["ci_lower"]
            == second["bootstrap"]["ci_lower"])
    assert (first["bootstrap"]["ci_upper"]
            == second["bootstrap"]["ci_upper"])
    # The defaults mirror the aggregator's bootstrap constants.
    default_call = aggregate_survival(episodes, horizon_s=10.0)
    assert default_call["bootstrap"]["iterations"] == \
        DEFAULT_BOOTSTRAP_ITERATIONS
    assert default_call["bootstrap"]["seed"] == DEFAULT_BOOTSTRAP_SEED
    assert default_call["bootstrap"]["method"] == "percentile_2.5_97.5"
    # Different seeds explore different resample paths.
    assert (other["bootstrap"]["ci_lower"], other["bootstrap"]["ci_upper"]) \
        != (first["bootstrap"]["ci_lower"], first["bootstrap"]["ci_upper"])


def test_aggregate_accepts_aggregator_mapping_pairs():
    # The aggregator emits first-stall pairs as
    # {"tilde_tau_det_s", "delta_i"} mappings and stall-episode recovery
    # rows as {"L_obs_s", "recovery_observed"}; both shapes must aggregate
    # identically to the equivalent tuples.
    mapping_form = [
        [{"tilde_tau_det_s": 30.0, "delta_i": 1, "episode_id": "e1"}],
        [{"L_obs_s": 4.0, "recovery_observed": True},
         {"L_obs_s": 6.0, "recovery_observed": False}],
    ]
    tuple_form = [[(30.0, 1)], [(4.0, 1), (6.0, 0)]]
    mapping_result = aggregate_survival(mapping_form, horizon_s=60.0,
                                        ell_star=5.0,
                                        bootstrap_iterations=200, seed=3)
    tuple_result = aggregate_survival(tuple_form, horizon_s=60.0,
                                      ell_star=5.0,
                                      bootstrap_iterations=200, seed=3)
    assert mapping_result["status"] == "computed"
    assert mapping_result["rmst_H"] == tuple_result["rmst_H"]
    assert (mapping_result["pooled_curve"]["S_t"]
            == tuple_result["pooled_curve"]["S_t"])
    assert (mapping_result["bootstrap"]["ci_lower"]
            == tuple_result["bootstrap"]["ci_lower"])
    # Hand check: t=4: risk 3, d=1 -> 2/3; t=6: censoring only, S stays 2/3;
    # t=30: risk 1, d=1 -> 0.  RMST = 4*1 + 26*(2/3) + 30*0.
    assert mapping_result["rmst_H"] == pytest.approx(4.0 + 26.0 * 2.0 / 3.0)
    assert mapping_result["recovery_prob_at_ell_star"]["probability"] == \
        pytest.approx(1.0 / 3.0)
    # A mapping pair carrying only one side is excluded and counted, never
    # fabricated (the aggregator's incomplete-pair discipline).
    incomplete = aggregate_survival(
        [[(5.0, 1)], [{"tilde_tau_det_s": 7.0}]], horizon_s=10.0)
    assert incomplete["status"] == "computed"
    assert incomplete["incomplete_pair_count"] == 1
    assert incomplete["empty_episode_count"] == 1
    assert incomplete["n_events"] == 1


def test_aggregate_beyond_horizon_pairs_treated_censored_at_h():
    # An event observed beyond H carries no information inside [0, H]: it
    # is right-censored at H, disclosed, and never treated as an event at a
    # fabricated time.
    result = aggregate_survival([[(12.0, 1), (6.0, 1)]], horizon_s=10.0)
    assert result["beyond_horizon_pair_count"] == 1
    assert result["n_events"] == 1
    assert result["n_censored"] == 1
    # KM: t=6: risk 2, d=1 -> 1/2, carried flat to 10 (no extrapolated drop).
    assert result["pooled_curve"]["S_t"] == pytest.approx([1.0, 0.5, 0.5])
    assert result["rmst_H"] == pytest.approx(8.0)


def test_ell_star_beyond_horizon_in_aggregate_is_na():
    result = aggregate_survival([[(3.0, 1)]], horizon_s=10.0, ell_star=12.0)
    assert result["status"] == "computed"
    assert result["rmst_H"] == pytest.approx(3.0)
    recovery = result["recovery_prob_at_ell_star"]
    assert recovery["status"] == NA_ELL_STAR_BEYOND_HORIZON
    assert recovery["probability"] is None
    assert "recovery_ci" not in result["bootstrap"]


# ---------------------------------------------------------------------------
# Validation and release discipline
# ---------------------------------------------------------------------------

def test_validation_errors_fail_closed():
    with pytest.raises(ValueError):
        kaplan_meier_curve([], horizon_s=10.0)
    with pytest.raises(ValueError):
        kaplan_meier_curve([(5.0, 1)], horizon_s=0.0)
    with pytest.raises(ValueError):
        kaplan_meier_curve([(5.0, 1)], horizon_s=-1.0)
    with pytest.raises(ValueError):
        kaplan_meier_curve([(5.0, 2)], horizon_s=10.0)
    with pytest.raises(ValueError):
        kaplan_meier_curve([(5.0, 0.5)], horizon_s=10.0)
    with pytest.raises(ValueError):
        kaplan_meier_curve([(-1.0, 1)], horizon_s=10.0)
    with pytest.raises(ValueError):
        kaplan_meier_curve([(1.0, 1, 1)], horizon_s=10.0)
    with pytest.raises(ValueError):
        kaplan_meier_curve("not-a-sequence", horizon_s=10.0)
    curve = kaplan_meier_curve([(5.0, 1)], horizon_s=10.0)
    with pytest.raises(ValueError):
        rmst("not-a-curve", horizon_s=10.0)
    with pytest.raises(ValueError):
        recovery_probability(curve, ell_star=-0.5)
    with pytest.raises(ValueError):
        restricted_mean_gap(curve, lo=3.0, hi=1.0)
    with pytest.raises(ValueError):
        aggregate_survival({"not": "a sequence"}, horizon_s=10.0)
    with pytest.raises(ValueError):
        aggregate_survival([{"tilde_tau_det_s": 1.0}], horizon_s=10.0)
    with pytest.raises(ValueError):
        aggregate_survival([[(1.0, 1)]], horizon_s=0.0)
    with pytest.raises(ValueError):
        aggregate_survival([[(1.0, 1)]], horizon_s=10.0,
                           bootstrap_iterations=0)
    with pytest.raises(ValueError):
        aggregate_survival([[(1.0, 1)]], horizon_s=10.0,
                           ell_star=-1.0)


def test_release_flags_false_everywhere():
    def release_values(node):
        if isinstance(node, dict):
            for key in ("release", "scoreable", "execution_validated",
                        "business_outcomes_validated"):
                if key in node:
                    yield node[key]
            for value in node.values():
                yield from release_values(value)
        elif isinstance(node, (list, tuple)):
            for value in node:
                yield from release_values(value)

    curve = kaplan_meier_curve([(5.0, 1)], horizon_s=10.0)
    outputs = [
        curve,
        rmst_difference(curve, kaplan_meier_curve([(2.0, 1)], horizon_s=10.0),
                        horizon_s=10.0),
        recovery_probability(curve, ell_star=5.0),
        restricted_mean_gap(curve, lo=0.0, hi=5.0),
        aggregate_survival([[(4.0, 1), (6.0, 0)], [(2.0, 1), (9.0, 1)]],
                           horizon_s=10.0, ell_star=5.0,
                           bootstrap_iterations=100),
        aggregate_survival([], horizon_s=10.0),
        aggregate_survival([[(5.0, 0)]], horizon_s=10.0),
    ]
    for output in outputs:
        values = list(release_values(output))
        assert values, "every output must carry the release flags"
        assert all(value is False for value in values)
    # The aggregation tier additionally stamps the survival schema.
    assert outputs[4]["schema"] == SURVIVAL_SCHEMA


def test_protocol_section_and_no_extrapolation_disclosure():
    result = aggregate_survival([[(4.0, 1), (6.0, 0)]], horizon_s=10.0)
    assert PROTOCOL_SECTION in result["protocol_sections"]
    assert "3.3" in result["protocol_sections"]
    assert "3.5" in result["protocol_sections"]
    # The censoring rule of protocol 2.6 is disclosed on every output tier.
    assert "never extrapolated" in result["no_extrapolation_rule"]
    assert "never extrapolated" in result["pooled_curve"]["no_extrapolation_rule"]
    curve = kaplan_meier_curve([(6.0, 1)], horizon_s=10.0)
    assert math.isfinite(rmst(curve, horizon_s=10.0))
