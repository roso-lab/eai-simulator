import pytest

from benchmark.composite_v3 import composite_profile_range, composite_score
from benchmark.metrics_v3 import ScoreResultV3


def _results():
    return tuple(ScoreResultV3(key, "valid", 50.0, components={"p0": 0.8} if key == "D2" else {})
                 for key in ("D1", "D2", "D3", "D4", "D5", "D6"))


def test_composite_uses_delivered_d2_and_keeps_gates_closed():
    result = composite_score(_results())
    assert result["status"] == "valid"
    assert result["values"]["D2_delivered"] == 40.0
    assert result["point"] == pytest.approx((50.0 * 5 + 40.0) / 6)
    assert result["formal_gates"]["rankable"] is False


def test_composite_missing_dimension_is_fail_closed():
    results = _results()[:-1]
    result = composite_score(results)
    assert result["status"] == "incomplete"
    assert result["point"] is None


def test_composite_missing_d2_nominal_success_is_fail_closed():
    results = list(_results())
    results[1] = ScoreResultV3("D2", "valid", 50.0)
    result = composite_score(results)
    assert result["status"] == "incomplete"
    assert result["reason"] == "d2_nominal_success_missing"


def test_profile_range_has_sixteen_vertices():
    result = composite_profile_range(_results())
    assert result["status"] == "valid"
    assert len(result["vertices"]) == 16
    assert result["minimum"] <= result["maximum"]
