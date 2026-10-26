"""Paper-0b006d4 composite coordination score.

The composite is an auxiliary release value built only after D1--D6 have
their own result/status.  It never opens a qualification gate and preserves
unknown/NA states instead of renormalizing around missing dimensions.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Mapping, Sequence

from .metrics_v3 import ScoreResultV3

PAPER_COMMIT = "0b006d4efcd25d5160d4b0a6e75cddd9e5265e44"
DIMENSIONS = ("D1", "D2", "D3", "D4", "D5", "D6")


class CompositeV3Error(ValueError):
    pass


def _profile_masses(profile: Mapping[str, float] | None) -> tuple[float, ...]:
    values = {"communication": 1.0, "loss": 1.0, "transfer": 1.0, "cost": 1.0}
    if profile is not None:
        for key in values:
            if key in profile:
                value = float(profile[key])
                if not 0.0 <= value <= 1.0:
                    raise CompositeV3Error(f"profile {key} must be in [0,1]")
                values[key] = value
    return (1.0, values["communication"], 1.0, values["loss"],
            values["transfer"], values["cost"])


def composite_score(results: Sequence[ScoreResultV3], *, profile: Mapping[str, float] | None = None) -> dict:
    """Compute the paper composite while retaining a closed release status.

    D2 enters only here as delivered success ``p0 * S_D2``.  A missing or
    non-valid dimension returns ``status='incomplete'``; it is never silently
    dropped or used to reweight the remaining dimensions.
    """
    by_id = {item.score_id: item for item in results}
    if len(by_id) != len(tuple(results)) or any(key not in by_id for key in DIMENSIONS):
        return {"schema": "heterocoord.composite.v1", "paper_commit": PAPER_COMMIT,
                "status": "incomplete", "point": None,
                "reason": "dimension_set_incomplete", "formal_gates": {"rankable": False}}
    if any(by_id[key].status != "valid" for key in DIMENSIONS):
        return {"schema": "heterocoord.composite.v1", "paper_commit": PAPER_COMMIT,
                "status": "incomplete", "point": None,
                "reason": "dimension_not_valid", "formal_gates": {"rankable": False}}
    d2 = by_id["D2"]
    if "p0" not in d2.components:
        return {"schema": "heterocoord.composite.v1", "paper_commit": PAPER_COMMIT,
                "status": "incomplete", "point": None,
                "reason": "d2_nominal_success_missing", "formal_gates": {"rankable": False}}
    p0 = float(d2.components["p0"])
    if not 0.0 <= p0 <= 1.0:
        raise CompositeV3Error("D2 result must carry bounded p0")
    values = {key: float(by_id[key].point) for key in DIMENSIONS}
    values["D2_delivered"] = p0 * values["D2"]
    masses = _profile_masses(profile)
    denominator = sum(masses)
    weights = {key: masses[index] / denominator for index, key in enumerate(DIMENSIONS)}
    point = sum(weights[key] * (values["D2_delivered"] if key == "D2" else values[key]) for key in DIMENSIONS)
    return {"schema": "heterocoord.composite.v1", "paper_commit": PAPER_COMMIT,
            "status": "valid", "point": point, "values": values,
            "masses": dict(zip(DIMENSIONS, masses)), "weights": weights,
            "contributions": {key: weights[key] * (values["D2_delivered"] if key == "D2" else values[key]) for key in DIMENSIONS},
            "profile": dict(profile or {}), "formal_gates": {"rankable": False}}


def composite_profile_range(results: Sequence[ScoreResultV3]) -> dict:
    """Evaluate the 16 profile vertices required by the latest paper."""
    vertices = []
    for communication in (0.0, 1.0):
        for loss in (0.0, 1.0):
            for transfer in (0.0, 1.0):
                for cost in (0.0, 1.0):
                    profile = {"communication": communication, "loss": loss,
                               "transfer": transfer, "cost": cost}
                    value = composite_score(results, profile=profile)
                    vertices.append({"profile": profile, "point": value.get("point"), "status": value["status"]})
    points = [row["point"] for row in vertices if row["status"] == "valid"]
    dimension_points = [float(item.point) for item in results
                        if item.status == "valid" and item.point is not None]
    return {"schema": "heterocoord.composite-profile-range.v1", "paper_commit": PAPER_COMMIT,
            "status": "valid" if len(points) == len(vertices) else "incomplete",
            "minimum": min(points) if points else None, "maximum": max(points) if points else None,
            "weakest_dimension": min(dimension_points, default=None),
            "vertices": vertices, "formal_gates": {"rankable": False}}


__all__ = ["PAPER_COMMIT", "CompositeV3Error", "composite_score", "composite_profile_range"]
