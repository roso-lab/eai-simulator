from __future__ import annotations

import math

import pytest

from tools.experiment_analysis.primitives import (
    competing_risks,
    crossing_brackets,
    holm_adjust,
    noninferiority,
    percentile,
    simultaneous_band,
)
from tools.experiment_analysis.schema import AnalysisError


def test_percentile_uses_linear_interpolation_and_rejects_nonfinite() -> None:
    assert percentile([0.0, 10.0], 0.25) == 2.5
    assert percentile([9.0, 1.0, 5.0], 0.5) == 5.0
    with pytest.raises(AnalysisError):
        percentile([], 0.5)
    with pytest.raises(AnalysisError):
        percentile([1.0, math.inf], 0.5)


def test_competing_risks_ties_apply_events_before_censor_removal() -> None:
    # At t=1 the risk set is four: two causes and one censor are tied.
    # CIF_a=CIF_b=1/4 and S=1/2.  One subject remains and fails from a at t=2,
    # adding 1/2 to CIF_a and reducing S to zero. RMST=1*1 + .5*1 = 1.5.
    result = competing_risks(
        [
            {"time": 1.0, "cause": "a"},
            {"time": 1.0, "cause": "b"},
            {"time": 1.0, "cause": "censor"},
            {"time": 2.0, "cause": "a"},
        ],
        horizon=3.0,
        causes=("a", "b"),
    )
    assert result["status"] == "computed"
    assert result["cif"] == {"a": 0.75, "b": 0.25}
    assert result["rmst"] == 1.5
    assert result["n_events"] == 3
    assert result["n_censored"] == 1
    assert result["curve"][0] == {
        "time": 1.0,
        "risk": 4,
        "events": {"a": 1, "b": 1},
        "censored": 1,
        "administrative_censored": 0,
        "survival": 0.5,
        "cif": {"a": 0.25, "b": 0.25},
    }
    assert result["curve"][-1]["time"] == 3.0
    assert result["curve"][-1]["survival"] == 0.0


def test_competing_risks_does_not_treat_competing_event_as_censor() -> None:
    # With two subjects at t=1, cause b consumes half the risk rather than
    # censoring it. Cause a at t=2 therefore adds the remaining S=.5.
    result = competing_risks(
        [{"time": 1, "cause": "b"}, {"time": 2, "cause": "a"}],
        horizon=2,
        causes=("a", "b"),
    )
    assert result["cif"] == {"a": 0.5, "b": 0.5}
    assert result["n_events"] == 2
    assert result["n_censored"] == 0


def test_competing_risks_administratively_censors_after_horizon() -> None:
    result = competing_risks(
        [{"time": 1, "cause": "a"}, {"time": 5, "cause": "b"}],
        horizon=2,
        causes=("a", "b"),
    )
    # S drops to .5 at t=1 and is observed through horizon: RMST=1+.5=1.5.
    assert result["status"] == "computed"
    assert result["rmst"] == 1.5
    assert result["cif"] == {"a": 0.5, "b": 0.0}
    assert result["administrative_censored"] == 1
    assert result["n_censored"] == 1
    assert result["at_risk_at_horizon"] == 1
    assert result["curve"][-1]["administrative_censored"] == 1


def test_competing_risks_support_and_empty_boundaries() -> None:
    empty = competing_risks([], horizon=2, causes=("failure",))
    assert empty["status"] == "N/A"
    assert empty["reason"] == "empty_sample"

    # Censoring at the horizon provides follow-up through the requested point.
    at_horizon = competing_risks(
        [{"time": 2, "cause": "censor"}, {"time": 2, "cause": "censor"}],
        horizon=2,
        causes=("failure",),
    )
    assert at_horizon["status"] == "computed"
    assert at_horizon["cif"] == {"failure": 0.0}
    assert at_horizon["rmst"] == 2.0

    early = competing_risks(
        [{"time": 1, "cause": "censor"}], horizon=2, causes=("failure",)
    )
    assert early["status"] == "N/A"
    assert early["reason"] == "horizon_beyond_observed_support"
    assert early["cif"] == {"failure": None}
    assert early["rmst"] is None


@pytest.mark.parametrize(
    "samples,causes,horizon",
    [
        ([{"time": -1, "cause": "a"}], ("a",), 1),
        ([{"time": 1, "cause": "unknown"}], ("a",), 1),
        ([{"time": 1, "cause": "a", "extra": 0}], ("a",), 1),
        ([{"time": 1, "cause": "a"}], ("a", "a"), 1),
        ([{"time": 1, "cause": "a"}], ("a",), math.nan),
    ],
)
def test_competing_risks_rejects_invalid_inputs(samples, causes, horizon) -> None:
    with pytest.raises(AnalysisError):
        competing_risks(samples, horizon=horizon, causes=causes)


def test_holm_keeps_missing_hypothesis_in_fixed_family_size() -> None:
    result = holm_adjust(
        {"h1": 0.01, "h2": None},
        declared_family=("h1", "h2", "h3"),
        alpha=0.05,
    )
    assert result["status"] == "N/A"
    assert result["family_size"] == 3
    assert result["observed_tests"] == 1
    assert result["tests"]["h1"]["adjusted_p"] == 0.03
    assert result["tests"]["h1"]["reject"] is True
    assert result["tests"]["h2"]["status"] == "N/A"
    assert result["tests"]["h3"]["status"] == "N/A"


def test_holm_step_down_adjustment_is_hand_calculable() -> None:
    result = holm_adjust(
        {"a": 0.01, "b": 0.03, "c": 0.04},
        declared_family=("a", "b", "c"),
        alpha=0.05,
    )
    assert result["status"] == "computed"
    assert {name: row["adjusted_p"] for name, row in result["tests"].items()} == {
        "a": 0.03,
        "b": 0.06,
        "c": 0.06,
    }
    assert {name: row["reject"] for name, row in result["tests"].items()} == {
        "a": True,
        "b": False,
        "c": False,
    }


def test_noninferiority_uses_lower_bound_against_negative_margin() -> None:
    assert noninferiority(-0.10, 0.10)["status"] == "PASS"
    assert noninferiority(-0.10001, 0.10)["status"] == "FAIL"
    assert noninferiority(None, 0.10)["status"] == "N/A"
    with pytest.raises(AnalysisError):
        noninferiority(0.0, -0.1)


def test_simultaneous_band_uses_joint_supremum_deviation() -> None:
    result = simultaneous_band(
        {"x1": 1.0, "x2": 3.0},
        [
            {"x1": 1.1, "x2": 2.8},  # sup=.2
            {"x1": 0.6, "x2": 3.1},  # sup=.4
            {"x1": 1.3, "x2": 2.7},  # sup=.3
        ],
        confidence=0.5,
    )
    assert math.isclose(result["critical"], 0.3)
    assert result["lower"] == {"x1": 0.7, "x2": 2.7}
    assert result["upper"] == {"x1": 1.3, "x2": 3.3}
    assert result["method"] == "sup_absolute_deviation"
    with pytest.raises(AnalysisError):
        simultaneous_band({"x1": 1.0, "x2": 2.0}, [{"x1": 1.1}])


def test_crossing_brackets_are_adjacent_descriptive_and_not_interpolated() -> None:
    increasing = crossing_brackets(
        [0.0, 0.5, 1.0], [0.1, 0.4, 0.8], threshold=0.5, direction="increasing"
    )
    assert increasing["brackets"] == [
        {
            "lower": 0.5,
            "upper": 1.0,
            "lower_value": 0.4,
            "upper_value": 0.8,
            "qualification": "descriptive",
        }
    ]
    assert increasing["interpolated"] is False
    assert increasing["band_qualified"] is False
    assert "simultaneous_band_required" in increasing["qualification"]

    decreasing = crossing_brackets(
        [1, 2, 3], [0.9, 0.5, 0.2], threshold=0.5, direction="decreasing"
    )
    # Equality at the left endpoint remains at risk; the registered stress
    # crossing is the first strictly-below observation.
    assert [(row["lower"], row["upper"]) for row in decreasing["brackets"]] == [(2.0, 3.0)]
    assert decreasing["grid_censor"] is None


def test_crossing_brackets_report_grid_censoring_without_invention() -> None:
    left_decreasing = crossing_brackets(
        [0, 1, 2], [0.49, 0.3, 0.2], threshold=0.5, direction="decreasing"
    )
    assert left_decreasing["brackets"] == []
    assert left_decreasing["grid_censor"] == "left"
    assert left_decreasing["reason"] == "left_grid_censored"

    right_decreasing = crossing_brackets(
        [0, 1, 2], [0.8, 0.6, 0.5], threshold=0.5, direction="decreasing"
    )
    assert right_decreasing["brackets"] == []
    assert right_decreasing["grid_censor"] == "right"
    assert right_decreasing["reason"] == "right_grid_censored"

    left_increasing = crossing_brackets(
        [0, 1], [0.5, 0.8], threshold=0.5, direction="increasing"
    )
    assert left_increasing["grid_censor"] == "left"

    right_increasing = crossing_brackets(
        [0, 1], [0.2, 0.49], threshold=0.5, direction="increasing"
    )
    assert right_increasing["grid_censor"] == "right"
    assert all(result["interpolated"] is False for result in (
        left_decreasing, right_decreasing, left_increasing, right_increasing
    ))
