import math
import pytest
from benchmark.statistics_v3 import (InstanceV3, ClusterDesignV3, d2_projection, d2_headline_interval, identification_interval, hoeffding, paired_interval, holm_six, operating_boundary, competing_risks, cluster_interval, invert_interval, resource_saving_gate)


def design(rows, streams=None):
    return ClusterDesignV3([InstanceV3(str(i), "cell", tuple(v), str(streams[i]) if streams else "") for i,v in enumerate(rows)], {"cell": 1.})


def test_d2_ratio_of_means_cap_per_level_and_constant_bound():
    d = design([(1, 1, .75, .5)]*40)
    r = d2_projection(d, seed=7, resamples=100)
    assert r["point"] == 75
    assert r["lower"] < 75 < r["upper"]
    assert r["method"] == "simultaneous_retention_projection"
    assert r["upper"] <= 100


def test_d2_zero_resamples_kept_and_zero_observed_na():
    r = d2_projection(design([(0, 1, 1, 1), (1, 0, 0, 0)]), resamples=100)
    assert r["zero_reference_resample_fraction"] > 0
    assert r["upper"] == 100
    assert r["status"] == "unstable"
    assert d2_projection(design([(0, 0, 0, 0)]), resamples=100)["status"] == "na"


def test_d2_headline_uses_finite_sample_retention_set_and_keeps_zero_reference_interval():
    degenerate = d2_headline_interval(design([(1, 1, .5, 0)] * 30), resamples=100)
    assert degenerate["method"] == "d2_finite_sample_retention_set"
    assert degenerate["status"] == "valid"
    assert 0 <= degenerate["lower"] <= degenerate["point"] <= degenerate["upper"] <= 100
    zero = d2_headline_interval(design([(0, 1, 1, 1), (0, 0, 0, 0)]), resamples=100)
    assert zero["status"] == "na" and zero["point"] is None
    assert 0 <= zero["lower"] <= zero["upper"] <= 100


def test_d2_finite_sample_bound_does_not_depend_on_bootstrap_seed_or_count():
    source = design([(1., float(i % 2), .5, .25) for i in range(30)])
    first = d2_headline_interval(source, alpha=.05, seed=1, resamples=100)
    second = d2_headline_interval(source, alpha=.05, seed=999, resamples=5000)
    assert first == second


def test_generator_identification_helper_retains_residual_unknowns_at_endpoints():
    r = identification_interval(design([(None, 1, .5, 0), (1, 0, 0, 0)]), None, d2=True, resamples=100)
    assert r["status"] == "range"
    scalar = identification_interval(design([(1,), (None,), (0,), (0,)]), lambda m: 100*m[0], resamples=100)
    assert scalar["status"] == "range"


def test_unknown_zero_reference_special_case():
    r = identification_interval(design([(None, 1, 0, 0)]), None, d2=True, resamples=100)
    assert r["status"] == "valid" and r["undefined_completion_possible"]
    r = identification_interval(design([(None, 0, 0, 0)]), None, d2=True, resamples=100)
    assert r["status"] == "valid" and r["undefined_completion_possible"]


def test_shared_stream_never_resampled_as_independent_instances():
    d = design([(0,), (1,), (0,), (1,)], streams=["a", "a", "b", "b"])
    assert all(m == (.5,) for m in d.draws(seed=3, resamples=100))
    assert d.independent_weights() == (.5, .5)
    r = cluster_interval(d, lambda m: 100*m[0], resamples=100)
    assert r.upper > r.lower


def test_fixed_catalog_bound_uses_independent_runs_and_paired_range():
    r = hoeffding([1]*100, [.01]*100)
    p = hoeffding([0]*100, [.01]*100, bounds=(-1, 1))
    assert 0 < r.lower < 1 and r.upper == pytest.approx(1)
    assert p.upper-p.lower > 2*(1-r.lower)
    with pytest.raises(ValueError):
        hoeffding([1], [0.5])


def test_paired_draw_preserves_identity_and_covariance():
    d = design([(0,), (1,), (.5,)]*10)
    r = paired_interval(d, d, lambda m: 100*m[0], resamples=100)
    assert r.point == 0 and r.lower < 0 < r.upper
    other = ClusterDesignV3([InstanceV3("foreign", "cell", (1,))], {"cell": 1})
    with pytest.raises(ValueError, match="exact scheduled"):
        paired_interval(d, other, lambda m: 100*m[0], resamples=100)


def test_d2_paired_difference_uses_six_retention_finite_sample_family():
    d = design([(1, 1, .5, 0)] * 30)
    result = paired_interval(d, d, None, d2=True, resamples=100)
    assert result.point == 0
    assert result.method == "d2_finite_sample_difference"
    assert result.lower < 0 < result.upper


def test_holm_requires_six_and_monotone_adjusted_p():
    result = holm_six({f"D{i}": p for i,p in enumerate((.001, .01, .02, .03, .3, .5), 1)})
    assert result["D1"]["adjusted_p"] == .006
    assert result["D2"]["adjusted_p"] == .05
    assert not result["D3"]["reject"]
    with pytest.raises(ValueError):
        holm_six({"D1": .01})


def test_boundary_does_not_join_disconnected_passing_levels():
    r = operating_boundary([100, 30, 10, 3], [(.7, 1), (.95, 1), (.1, .8)])
    assert r["passing_capacity"] == 100 and r["failing_capacity"] == 3
    assert r["nonmonotone_recovery_levels"] == [2]


def test_aj_ties_censor_and_rmst_hand_calculation():
    r = competing_risks([(1, "work"), (1, None), (2, "failure"), (3, None)], horizon=3)
    assert r["cif"] == {"failure": .375, "work": .25}
    assert r["rmst"] == 2.125
    assert r["censored"] == 2
    assert competing_risks([(1, None)], horizon=3)["rmst"] is None


def test_noninferiority_requires_both_endpoints():
    good = hoeffding([.5]*1000, [.001]*1000, bounds=(-1,1))
    bad = hoeffding([-.5]*1000, [.001]*1000, bounds=(-1,1))
    assert resource_saving_gate(good, bad, success_margin=0, progress_margin=0) == "fail"
    assert resource_saving_gate(good, good, success_margin=0, progress_margin=0) == "pass"
    assert resource_saving_gate(None, good, success_margin=0, progress_margin=0) == "na"


def test_interval_inversion_matches_hoeffding_closed_form():
    p = invert_interval(lambda alpha: hoeffding([.4]*100, [.01]*100, bounds=(-1, 1), alpha=alpha))
    assert p == pytest.approx(2*math.exp(-100*.4**2/2), abs=1e-9)
