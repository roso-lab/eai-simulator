from decimal import Decimal, localcontext

import pytest

from benchmark.oracle_clock_v3 import compile_time_bounds, seconds_to_ticks
from benchmark.task_catalog import load_default_catalog
from dataclasses import replace


def test_decimal_tick_boundary_is_exact_even_with_low_decimal_precision():
    with localcontext() as context:
        context.prec = 2
        assert seconds_to_ticks("0.14000000000000000001", rounding="ceil") == 8
        assert seconds_to_ticks("0.13999999999999999999", rounding="floor") == 6
        assert seconds_to_ticks(0.14, rounding="ceil") == 7
        assert seconds_to_ticks(Decimal("0.14"), rounding="floor") == 7


def test_compilation_preserves_minimums_and_maximums_without_widening_empty_domain():
    assert compile_time_bounds(duration_s="0.021", release_s="0.001",
                               deadline_s="0.059", horizon_s="0.079") == {
        "duration_ticks": 2, "release_tick": 1,
        "deadline_tick": 2, "horizon_ticks": 3,
    }


@pytest.mark.parametrize("value", [True, None, -1, "NaN", "Infinity", "oops", "1e100000", "1e-100000"])
def test_invalid_or_unbounded_seconds_are_rejected(value):
    with pytest.raises(ValueError):
        seconds_to_ticks(value, rounding="floor")


def test_runtime_catalog_projection_cannot_round_horizon_up():
    anchor = replace(load_default_catalog()[0], horizon_s=0.039)
    assert anchor.to_protocol_contract().horizon_ticks == 1
