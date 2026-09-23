"""Capacity curves use frozen reference points and bounded inference."""

import pytest

from demo.benchmark.communication_analysis import (
    summarize_capacity_curve, summarize_paired_distribution_effect,
)


def rows():
    result = []
    for base, offset in (("i1", 0.0), ("i2", .1), ("i3", -.1)):
        for seed in ("s1", "s2"):
            result.append({"base_instance_id": base, "channel_seed": seed,
                           "method_id": "m", "capacity_bps": None,
                           "c0_bps": 1000, "metric_value": 1.0})
            result.append({"base_instance_id": base, "channel_seed": seed,
                           "method_id": "m", "capacity_bps": 100,
                           "c0_bps": 1000, "metric_value": .9 + offset})
            result.append({"base_instance_id": base, "channel_seed": seed,
                           "method_id": "m", "capacity_bps": 10,
                           "c0_bps": 1000, "metric_value": .4 + offset})
    return result


def test_capacity_summary_aggregates_seeds_and_reports_bounded_thresholds():
    report = summarize_capacity_curve(rows(), bootstrap_samples=100, seed=3)
    assert report["status"] == "ok"
    assert report["reference"]["mean"] == pytest.approx(1.0)
    assert [point["capacity_bps"] for point in report["points"]] == [10., 100.]
    assert report["points"][1]["capacity_ratio_c0"] == pytest.approx(.1)
    assert report["thresholds"]["0.9"]["status"] == "bracketed"
    assert report["thresholds"]["0.9"]["capacity_lower_bps"] == 10.
    assert report["thresholds"]["0.9"]["capacity_upper_bps"] == 100.
    assert report["thresholds"]["0.5"]["status"] == "bracketed"
    assert report["no_extrapolation"] is True


def test_capacity_summary_reports_above_maximum_without_extrapolating():
    data = rows()
    for row in data:
        if row["capacity_bps"] == 100:
            row["metric_value"] = .2
    report = summarize_capacity_curve(data, thresholds=(.9,), bootstrap_samples=20)
    assert report["thresholds"]["0.9"]["status"] == "above_tested_maximum"
    assert report["thresholds"]["0.9"]["capacity_lower_bound_bps"] == 100.


def test_capacity_summary_fail_closes_missing_or_weak_reference():
    missing = [row for row in rows() if row["capacity_bps"] is not None]
    assert summarize_capacity_curve(missing)["status"] == "missing_reference"
    report = summarize_capacity_curve(rows(), min_reference_mean=1.1, thresholds=(.9,))
    assert report["reference"]["valid"] is False
    assert report["thresholds"]["0.9"]["status"] == "reference_below_minimum"


def test_capacity_summary_rejects_duplicates_and_mixed_methods():
    data = rows()
    with pytest.raises(ValueError, match="duplicate"):
        summarize_capacity_curve(data + [data[0]])
    mixed = rows()
    mixed[0] = {**mixed[0], "method_id": "other"}
    with pytest.raises(ValueError, match="different method_id|multiple methods"):
        summarize_capacity_curve(mixed)


def test_distribution_effect_pairs_same_level_and_reports_jp_style_difference():
    data = []
    for base, fixed, jitter in (("i1", .8, .6), ("i2", .7, .5)):
        for channel in ("s1", "s2"):
            data.extend([
                {"base_instance_id": base, "channel_seed": channel,
                 "comparison_level": "delay-1", "condition": "fixed",
                 "metric_value": fixed, "method_id": "m"},
                {"base_instance_id": base, "channel_seed": channel,
                 "comparison_level": "delay-1", "condition": "jitter",
                 "metric_value": jitter, "method_id": "m"},
            ])
    report = summarize_paired_distribution_effect(
        data, left_condition="fixed", right_condition="jitter", bootstrap_samples=50)
    effect = report["effects"][0]
    assert effect["status"] == "ok"
    assert effect["n_pairs"] == 4
    assert effect["mean_difference_right_minus_left"] == pytest.approx(-.2)
    assert report["no_unmatched_pair_inference"] is True


def test_distribution_effect_does_not_infer_from_unmatched_conditions():
    data = [{"base_instance_id": "i1", "channel_seed": "s1",
             "comparison_level": "loss-.1", "condition": "iid",
             "metric_value": .8}]
    report = summarize_paired_distribution_effect(
        data, left_condition="iid", right_condition="burst")
    assert report["effects"][0]["status"] == "missing_pair"
