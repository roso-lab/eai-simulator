"""Exact compilation of registered seconds to the paper's 20 ms clock."""
from decimal import Decimal, InvalidOperation
from typing import Literal


def seconds_to_ticks(value: str | int | float | Decimal, *, rounding: Literal["ceil", "floor"]) -> int:
    """Treat the supplied decimal representation as the registered quantity.

    Decimal strings preserve an author's exact boundary; binary-float arithmetic
    and nearest-integer rounding must not extend a deadline or shorten a duration.
    """
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ValueError("seconds must be a finite nonnegative decimal quantity")
    if rounding not in {"ceil", "floor"}:
        raise ValueError("rounding must be ceil or floor")
    try:
        seconds = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("invalid seconds") from exc
    if not seconds.is_finite() or seconds < 0:
        raise ValueError("seconds must be finite and nonnegative")
    # Reject unbounded authoring inputs before allocating huge integers. This is
    # an explicit compiler input limit, never a feasibility/infeasibility result.
    if seconds > Decimal("1e9") or len(seconds.as_tuple().digits) > 1000 or abs(seconds.as_tuple().exponent) > 1000:
        raise ValueError("seconds exceed compiler input limits")
    numerator, denominator = seconds.as_integer_ratio()
    numerator *= 50
    return -(-numerator // denominator) if rounding == "ceil" else numerator // denominator


def compile_time_bounds(*, duration_s, release_s, deadline_s, horizon_s) -> dict[str, int]:
    """Compile fields independently; preserve even an empty timing domain.

    A positive duration that cannot fit between release and deadline is a valid
    infeasible constraint, not a reason to round or widen its registered bounds.
    """
    duration = seconds_to_ticks(duration_s, rounding="ceil")
    if duration <= 0:
        raise ValueError("duration must be positive")
    return {
        "duration_ticks": duration,
        "release_tick": seconds_to_ticks(release_s, rounding="ceil"),
        "deadline_tick": seconds_to_ticks(deadline_s, rounding="floor"),
        "horizon_ticks": seconds_to_ticks(horizon_s, rounding="floor"),
    }
