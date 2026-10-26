"""Pure CPU estimators for the revised composite D1--D6 scores.

These functions consume typed/normalized facts and intentionally do not read
legacy ``MetricObservation`` rows.  They provide deterministic golden-sample
semantics; inferential intervals and release ranking remain separate stages.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any, Iterable, Mapping, Sequence


class MetricV3Error(ValueError):
    """Raised for malformed facts or an undefined score."""


@dataclass(frozen=True)
class ScoreResultV3:
    score_id: str
    status: str
    point: float | None
    lower: float | None = None
    upper: float | None = None
    components: Mapping[str, float] = field(default_factory=dict)
    support_weight: float = 1.0
    reason: str | None = None
    # Fixed-catalog dimensions may exclude a preregistered family that is
    # below its case floor.  Keep that disclosure attached to the result
    # instead of silently renormalizing or hiding the family.
    excluded_families: tuple[str, ...] = ()
    excluded_family_case_counts: Mapping[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "excluded_families", tuple(self.excluded_families))
        object.__setattr__(self, "excluded_family_case_counts", dict(self.excluded_family_case_counts))
        if self.status not in {"valid", "na", "incomplete", "invalid", "range", "unstable"}:
            raise MetricV3Error(f"invalid result status: {self.status}")
        if self.status == "valid" and self.point is None:
            raise MetricV3Error("valid result needs point")
        if self.point is not None:
            point = _finite(self.point, "point")
            if not 0.0 <= point <= 100.0:
                raise MetricV3Error("point must lie in [0,100]")
            object.__setattr__(self, "point", point)
        for name in ("lower", "upper"):
            bound = getattr(self, name)
            if bound is not None:
                bound = _finite(bound, name)
                if not 0.0 <= bound <= 100.0:
                    raise MetricV3Error(f"{name} must lie in [0,100]")
                object.__setattr__(self, name, bound)
        if self.status == "range" and (self.lower is None or self.upper is None or self.lower > self.upper):
            raise MetricV3Error("range result needs ordered bounds")


def _finite(value: float, name: str) -> float:
    value = float(value)
    if not math.isfinite(value):
        raise MetricV3Error(f"{name} must be finite")
    return value


def _bounded(value: float, name: str) -> float:
    value = _finite(value, name)
    if not 0 <= value <= 1:
        raise MetricV3Error(f"{name} must be in [0,1]")
    return value


def d1_score(*, validity: float, organization: float, infeasible: float, complete_plan: bool = True) -> ScoreResultV3:
    v, q, i = (_bounded(validity, "V"), _bounded(organization, "Qorg"), _bounded(infeasible, "I"))
    score = 100 * (0.50 * v + 0.30 * q + 0.20 * i)
    if not complete_plan:
        score = min(score, 50.0)
    return ScoreResultV3("D1", "valid", score, components={"V": v, "Qorg": q, "I": i})


def d2_score(*, nominal: float, restricted: Sequence[float], expected_levels: int = 3) -> ScoreResultV3:
    p0 = _bounded(nominal, "p0")
    if len(restricted) != expected_levels:
        raise MetricV3Error(f"D2 expects {expected_levels} restricted capacities")
    if p0 == 0:
        return ScoreResultV3("D2", "na", None, reason="nominal_success_zero")
    rates = tuple(_bounded(item, f"p{index + 1}") for index, item in enumerate(restricted))
    retention = tuple(min(1.0, item / p0) for item in rates)
    return ScoreResultV3("D2", "valid", 100 * sum(retention) / len(retention), components={"p0": p0, **{f"p{index + 1}": item for index, item in enumerate(rates)}})


def d3_score(*, p2: float, p3j: float, p3s: float, p4: float, primary_condition: str = "nominal") -> ScoreResultV3:
    values = {"mu2": _bounded(p2, "mu2"), "muJ": _bounded(p3j, "muJ"), "muS": _bounded(p3s, "muS"), "mu4": _bounded(p4, "mu4")}
    if primary_condition != "nominal":
        raise MetricV3Error("D3 primary score must use nominal condition")
    score = 100 / 3 * (values["mu2"] + (values["muJ"] + values["muS"]) / 2 + values["mu4"])
    return ScoreResultV3("D3", "valid", score, components=values)


def d4_score(items: Iterable[Mapping[str, Any]], *, expected_count: int | None = None) -> ScoreResultV3:
    rows = tuple(items)
    if expected_count is not None and len(rows) != expected_count:
        return ScoreResultV3("D4", "incomplete", None, reason="fixed_directory_missing_item")
    if not rows:
        return ScoreResultV3("D4", "na", None, reason="empty_affected_work_closure")
    values: list[float] = []
    for row in rows:
        kind = str(row.get("kind", "deadline"))
        e = _bounded(row.get("e", 0.0), "e")
        if kind == "deadline":
            q = e
        elif kind in {"time_valued", "continuous_supply"}:
            q = (e + _bounded(row.get("phi", 0.0), "phi")) / 2
        else:
            raise MetricV3Error(f"unknown D4 item kind: {kind}")
        values.append(q)
    return ScoreResultV3("D4", "valid", 100 * sum(values) / len(values), components={"item_count": float(len(values))})


def d5_score(families: Mapping[str, Mapping[str, Mapping[str, Sequence[float]]]], *, min_cases_per_family: int = 30) -> ScoreResultV3:
    if not families:
        return ScoreResultV3("D5", "na", None, reason="empty_certified_family_directory")
    family_scores: list[float] = []
    excluded: list[str] = []
    excluded_counts: dict[str, int] = {}
    for family_id, cases in families.items():
        if len(cases) < min_cases_per_family:
            excluded.append(str(family_id))
            excluded_counts[str(family_id)] = len(cases)
            continue
        case_scores: list[float] = []
        for case_id, units in cases.items():
            unit_scores: list[float] = []
            for unit_id, repeats in units.items():
                if len(repeats) != 3:
                    return ScoreResultV3("D5", "incomplete", None, reason=f"unit_repeat_count:{family_id}/{case_id}/{unit_id}")
                unit_scores.append(sum(_bounded(item, "Y") for item in repeats) / 3)
            if not unit_scores:
                return ScoreResultV3("D5", "incomplete", None, reason=f"empty_adaptation_case:{family_id}/{case_id}")
            case_scores.append(sum(unit_scores) / len(unit_scores))
        family_scores.append(sum(case_scores) / len(case_scores))
    if not family_scores:
        return ScoreResultV3("D5", "na", None,
                             reason="no_family_meets_case_floor",
                             excluded_families=tuple(sorted(excluded)),
                             excluded_family_case_counts=excluded_counts)
    return ScoreResultV3(
        "D5", "valid", 100 * sum(family_scores) / len(family_scores),
        components={"family_count": float(len(family_scores)),
                    "excluded_family_count": float(len(excluded))},
        reason=("excluded_families_below_case_floor" if excluded else None),
        excluded_families=tuple(sorted(excluded)),
        excluded_family_case_counts=excluded_counts,
    )


def d6_score(runs: Sequence[Mapping[str, Any]], *, reference_bits: float, reference_time: float, expected_count: int | None = None) -> ScoreResultV3:
    if expected_count is not None and len(runs) != expected_count:
        return ScoreResultV3("D6", "incomplete", None, reason="fixed_directory_missing_run")
    b0, t0 = _finite(reference_bits, "B0"), _finite(reference_time, "T0")
    if b0 < 0 or t0 < 0:
        raise MetricV3Error("calibration references must be nonnegative")
    if b0 == 0 or t0 == 0:
        return ScoreResultV3("D6", "na", None, reason="zero_calibration_reference")
    if not runs:
        return ScoreResultV3("D6", "na", None, reason="empty_directory")
    values: list[float] = []
    for run in runs:
        if type(run.get("success")) is not bool:
            raise MetricV3Error("unknown success requires endpoint completion, not failure recoding")
        if run.get("success") is not True:
            values.append(0.0)
            continue
        b, t = _finite(run.get("bits"), "B"), _finite(run.get("time"), "T")
        if b < 0 or t < 0:
            raise MetricV3Error("resource use must be nonnegative")
        if b0 == 0 or t0 == 0:
            return ScoreResultV3("D6", "na", None, reason="zero_calibration_reference")
        values.append(100 * math.sqrt((b0 / max(b0, b)) * (t0 / max(t0, t))))
    return ScoreResultV3("D6", "valid", sum(values) / len(values), components={"success_rate": sum(bool(item.get("success")) for item in runs) / len(runs)})


def weighted_hoeffding_half_width(weights: Sequence[float], *, alpha: float = 0.05) -> float:
    """Return a 0--100 score half-width for fixed-directory weights."""

    if not 0 < alpha < 1:
        raise MetricV3Error("alpha must be in (0,1)")
    values = tuple(_finite(item, "weight") for item in weights)
    if not values or any(item < 0 for item in values):
        raise MetricV3Error("weights must be nonnegative and nonempty")
    if not math.isclose(sum(values), 1.0, rel_tol=1e-9, abs_tol=1e-9):
        raise MetricV3Error("weights must sum to one")
    return 100 * math.sqrt(math.log(2 / alpha) / 2 * sum(item * item for item in values))


def missing_weight_range(*, known_score: float, missing_weight: float) -> ScoreResultV3:
    """Keep fixed-directory missing weight as a best/worst score interval."""

    score = _finite(known_score, "known_score")
    weight = _finite(missing_weight, "missing_weight")
    if not 0 <= score <= 100 or not 0 <= weight <= 1:
        raise MetricV3Error("known score or missing weight out of range")
    return ScoreResultV3("fixed_directory", "range", None, lower=score, upper=min(100.0, score + 100 * weight), support_weight=1 - weight, reason="fixed_directory_missing_weight")


__all__ = ["MetricV3Error", "ScoreResultV3", "d1_score", "d2_score", "d3_score", "d4_score", "d5_score", "d6_score", "weighted_hoeffding_half_width", "missing_weight_range"]
