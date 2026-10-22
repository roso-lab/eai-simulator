from __future__ import annotations

from benchmark.metrics_v3 import d1_score, d2_score, d3_score, d4_score, d5_score, d6_score, missing_weight_range, weighted_hoeffding_half_width


def test_d1_golden_and_component_bound():
    assert d1_score(validity=.6, organization=.85, infeasible=.9).point == 73.5
    assert d1_score(validity=0, organization=1, infeasible=1, complete_plan=False).point == 50


def test_d2_retention_and_zero_nominal():
    assert d2_score(nominal=.8, restricted=(.8, .6, .4)).point == 75
    assert d2_score(nominal=0, restricted=(.2, .2, .2)).status == "na"


def test_d3_weights_are_not_double_counted():
    assert round(d3_score(p2=.9, p3j=.8, p3s=.6, p4=.75).point, 6) == round(100/3*(.9 + (.8+.6)/2 + .75), 6)


def test_d4_item_types_and_fixed_directory():
    result = d4_score(({"kind": "deadline", "e": 1}, {"kind": "time_valued", "e": 1, "phi": .4}, {"kind": "continuous_supply", "e": 0, "phi": .8}))
    assert result.point == 70
    assert d4_score(({"kind": "deadline", "e": 1},), expected_count=2).status == "incomplete"


def test_d5_joint_unit_and_source_independence():
    families = {"f": {f"case-{i}": {"u": (1, 0, 1)} for i in range(30)}}
    assert d5_score(families).point == 66.66666666666666


def test_d5_excludes_and_discloses_below_floor_family():
    eligible = {f"case-{i}": {"u": (1, 1, 1)} for i in range(30)}
    result = d5_score({"eligible": eligible, "short": {"case": {"u": (0, 0, 0)}}})
    assert result.point == 100
    assert result.excluded_families == ("short",)
    assert result.components["excluded_family_count"] == 1
    all_short = d5_score({"short": {"case": {"u": (1, 1, 1)}}})
    assert all_short.status == "na" and all_short.excluded_families == ("short",)


def test_d6_success_gate_and_efficiency():
    result = d6_score(({"success": False, "bits": 1, "time": 1}, {"success": True, "bits": 200, "time": 2}), reference_bits=100, reference_time=1)
    assert result.point == 25
    assert d6_score(({"success": True, "bits": 200, "time": 2},), reference_bits=100, reference_time=1).point == 50


def test_fixed_directory_missing_weight_and_hoeffding():
    result = missing_weight_range(known_score=40, missing_weight=.25)
    assert result.status == "range" and result.lower == 40 and result.upper == 65
    assert weighted_hoeffding_half_width((.25, .25, .25, .25)) > 0
