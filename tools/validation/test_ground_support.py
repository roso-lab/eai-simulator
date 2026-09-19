import math

import pytest

from mobile_mount_checks import ground_support_summary


def frames(vertical_force, count=100):
    return [[[0.0, 0.0, vertical_force / 4] for _ in range(4)] for _ in range(count)]


def test_ground_reaction_balancing_weight_passes():
    summary = ground_support_summary(frames(200.0), 200.0, True)
    assert summary["persistent_support_pass"]
    assert summary["supported_sample_fraction"] == 1
    assert summary["mean_vertical_force_weight_ratio"] == 1


def test_one_contact_cannot_certify_sustained_support():
    samples = frames(0.0)
    samples[0] = frames(200.0, 1)[0]
    summary = ground_support_summary(samples, 200.0, True)
    assert summary["contact_observed"]
    assert not summary["persistent_support_pass"]


def test_contact_reporting_disabled_cannot_pass_with_stale_buffers():
    summary = ground_support_summary(frames(200.0), 200.0, False)
    assert not summary["contact_observed"]
    assert not summary["persistent_support_pass"]


def test_force_magnitude_without_upward_support_cannot_pass():
    samples = [[[200.0, 0.0, 0.0] for _ in range(4)] for _ in range(100)]
    assert not ground_support_summary(samples, 200.0, True)["persistent_support_pass"]


def test_large_reaction_without_weight_balance_cannot_pass():
    assert not ground_support_summary(frames(400.0), 200.0, True)["persistent_support_pass"]


@pytest.mark.parametrize("weight", [0.0, -1.0, math.nan, math.inf])
def test_invalid_weight_is_rejected(weight):
    with pytest.raises(ValueError):
        ground_support_summary(frames(200.0), weight, True)


@pytest.mark.parametrize("samples", [[], [[]], [[[0, 0, math.nan]]], [[[0, 0]]],
                                    [[[0, 0, 100]], [[0, 0, 50], [0, 0, 50]]]])
def test_missing_or_invalid_force_coverage_is_rejected(samples):
    with pytest.raises(ValueError):
        ground_support_summary(samples, 200.0, True)
