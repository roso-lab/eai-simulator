from demo.benchmark.goal_progress_curve import (
    EQUAL_WEIGHT_POLICY,
    build_progress_curve,
)


def _obs(milestone, at, stability=0.0):
    return {"milestone_id": milestone, "satisfied_at_s": at,
            "stability_duration_s": stability}


def test_equal_weight_policy_is_frozen_default():
    curve = build_progress_curve(["a", "b", "c"], [_obs("a", 10.0)],
                                 horizon_s=100.0)
    assert curve["status"] == "computed"
    assert curve["weight_policy"] == EQUAL_WEIGHT_POLICY
    assert curve["active_milestone_count"] == 3
    assert curve["Q_H"] == 1 / 3
    assert curve["MCR"] == curve["Q_H"]


def test_step_integral_aup_is_exact():
    # Milestones complete at t=10 and t=50 over H=100 with equal weights:
    # Q = 0 on [0,10), 0.5 on [10,50), 1 on [50,100] -> AUP = 0.7.
    curve = build_progress_curve(["a", "b"],
                                 [_obs("a", 10.0), _obs("b", 50.0)],
                                 horizon_s=100.0)
    assert abs(curve["AUP"] - 0.70) < 1e-12
    assert curve["Q_H"] == 1.0


def test_stability_duration_delays_completion():
    curve = build_progress_curve(["a"], [_obs("a", 10.0, stability=5.0)],
                                 horizon_s=100.0)
    assert curve["Q_H"] == 1.0
    # Completion lands at satisfied_at + stability; Q_samples only carries
    # change points, so the first nonzero sample is at t=15.
    nonzero = [row for row in curve["Q_samples"] if row["Q"] > 0]
    assert nonzero and nonzero[0]["t_s"] == 15.0


def test_missing_milestone_stays_unsatisfied():
    curve = build_progress_curve(["a", "b"], [_obs("a", 5.0)], horizon_s=50.0)
    assert curve["Q_H"] == 0.5
    assert curve["completed_milestones"] == ["a"]


def test_hold_last_observation_to_horizon():
    # Milestone completed exactly at H counts; nothing beyond H leaks in.
    curve = build_progress_curve(["a"], [_obs("a", 100.0)], horizon_s=100.0)
    assert curve["Q_H"] == 1.0
    late = build_progress_curve(["a"], [_obs("a", 120.0)], horizon_s=100.0)
    assert late["Q_H"] == 0.0


def test_duplicate_observations_never_double_count():
    curve = build_progress_curve(["a", "b"],
                                 [_obs("a", 5.0), _obs("a", 7.0), _obs("a", 3.0)],
                                 horizon_s=50.0)
    # First observation (t=3) wins; duplicates ignored; b unsatisfied.
    assert curve["Q_H"] == 0.5
    changes = [row["t_s"] for row in curve["Q_samples"] if row["Q"] > 0]
    assert changes and changes[0] == 3.0


def test_pgi_npgi_over_execution_window():
    curve = build_progress_curve(["a"], [_obs("a", 40.0)], horizon_s=100.0,
                                 execution_end_s=40.0)
    assert curve["execution_window"]["t_exec_s"] == 40.0
    assert abs(curve["PGI_exec"] - 40.0) < 1e-12
    assert abs(curve["nPGI_exec"] - 1.0) < 1e-12
    assert curve["S_progress"] == 0.0


def test_zero_length_execution_window_is_na():
    curve = build_progress_curve(["a"], [], horizon_s=100.0,
                                 execution_end_s=0.0)
    assert curve["PGI_exec"] == 0.0
    assert curve["nPGI_exec"] is None
    assert curve["nPGI_note"] == "zero_length_execution_window_na"


def test_na_discipline_for_empty_set_and_zero_weight():
    empty = build_progress_curve([], [], horizon_s=100.0)
    assert empty["status"] == "na_empty_active_set"
    zero = build_progress_curve(["a"], [], horizon_s=100.0,
                                weights={"a": 0.0})
    assert zero["status"] == "na_zero_total_weight"
    assert zero["scoreable"] is False


def test_explicit_weights_are_frozen_and_validated():
    curve = build_progress_curve(["a", "b"], [_obs("a", 5.0)], horizon_s=10.0,
                                 weights={"a": 3.0, "b": 1.0})
    assert curve["weight_policy"] == "explicit_frozen"
    assert abs(curve["Q_H"] - 0.75) < 1e-12
    try:
        build_progress_curve(["a"], [], horizon_s=10.0, weights={"a": -1})
    except ValueError:
        pass
    else:
        raise AssertionError("negative weight accepted")


def test_zero_progress_unit_metrics_flagged():
    curve = build_progress_curve(["a"], [], horizon_s=10.0)
    assert curve["Q_H"] == 0.0
    assert curve["per_progress_note"] == "undefined_zero_progress"


def test_unknown_milestone_observation_rejected():
    try:
        build_progress_curve(["a"], [_obs("ghost", 1.0)], horizon_s=10.0)
    except ValueError:
        pass
    else:
        raise AssertionError("inactive milestone accepted")
