"""Joint rendezvous must remain reachable and separate physical footprints."""

import math

import numpy as np
import pytest

from demo.benchmark.joint_transport import (
    formation_hold_required,
    joint_formation_limit,
    measure_joint_formation,
    plan_joint_rendezvous,
)


class OpenMap:
    resolution = .1
    width = height = 120
    free = np.ones((height, width), dtype=bool)

    def local(self, x, y):
        return x, y

    def world(self, x, y):
        return x, y


def plan(**overrides):
    occupancy = OpenMap()
    args = dict(occupancy=occupancy, blocked=occupancy.free,
                members=["small", "large"], radii={"small": .3, "large": .7},
                starts={"small": (2., 3.), "large": (2., 7.)},
                pickup=(4., 5.), delivery=(8., 5.), object_radius=.45)
    return plan_joint_rendezvous(**(args | overrides))


def test_rendezvous_separates_members_and_crate_even_at_tolerance_limits():
    result = plan()
    tolerance = result["terminal_tolerance_m"]
    for key, center in (("pickup_slots", (4, 5)), ("delivery_slots", (8, 5))):
        slots = result[key]
        assert math.dist(slots["small"], slots["large"]) - 2 * tolerance > 1.
        for member, radius in (("small", .3), ("large", .7)):
            assert math.dist(slots[member], center) - tolerance > radius + .45
    assert result["grids"]["large"].free.sum() < result["grids"]["small"].free.sum()
    for member, radius in (("small", .3), ("large", .7)):
        for x, y in result["paths"][member][0]:
            assert math.dist(((x + .5) * .1, (y + .5) * .1), (4, 5)) > radius + .45


def test_no_space_fails_instead_of_snapping_both_members_to_object():
    with pytest.raises(ValueError, match="no separated"):
        plan(blocked=np.zeros((120, 120), dtype=bool))


def test_duplicate_member_is_rejected():
    with pytest.raises(ValueError, match="two distinct"):
        plan(members=["small", "small"])


def test_rendezvous_is_deterministic():
    first, second = plan(), plan()
    assert first["pickup_slots"] == second["pickup_slots"]
    assert first["delivery_slots"] == second["delivery_slots"]
    assert first["paths"] == second["paths"]


def test_approach_does_not_cross_a_parked_teammates_slot():
    result = plan(starts={"small": (1.2, 5.), "large": (2.5, 5.)})
    for member, other in (("small", "large"), ("large", "small")):
        for x, y in result["paths"][member][0]:
            assert math.dist(((x + .5) * .1, (y + .5) * .1),
                             result["pickup_slots"][other]) > 1.


def test_formation_pacing_holds_only_the_member_stretching_an_overlong_team():
    positions = {"fast": (4.0, 0.0), "slow": (0.0, 0.0)}
    assert formation_hold_required(
        member="fast", positions=positions, target=(5.0, 0.0), max_separation_m=3.5)
    assert not formation_hold_required(
        member="slow", positions=positions, target=(1.0, 0.0), max_separation_m=3.5)
    assert not formation_hold_required(
        member="fast", positions={"fast": (3.0, 0.0), "slow": (0.0, 0.0)},
        target=(4.0, 0.0), max_separation_m=3.5)


def test_formation_pacing_allows_an_overlong_team_to_close_the_gap():
    positions = {"fast": (4.0, 0.0), "slow": (0.0, 0.0)}
    assert not formation_hold_required(
        member="fast", positions=positions, target=(3.0, 0.0), max_separation_m=3.5)
    assert not formation_hold_required(
        member="slow", positions=positions, target=(1.0, 0.0), max_separation_m=3.5)


def test_formation_limit_and_measured_window_are_auditable():
    pickup = {"small": (0.0, 1.0), "large": (0.0, -1.0)}
    delivery = {"small": (4.0, 1.0), "large": (4.0, -1.0)}
    limit = joint_formation_limit(pickup, delivery)
    samples = [
        {"step": 4, "positions": {"small": (0.0, 1.0), "large": (0.0, -1.0)}},
        {"step": 5, "positions": {"small": (1.0, 1.0), "large": (1.0, -1.0)}},
        {"step": 6, "positions": {"small": (2.0, 1.0), "large": (2.0, -1.0)}},
        {"step": 7, "positions": {"small": (3.0, 1.0), "large": (3.0, -1.0)}},
    ]
    report = measure_joint_formation(
        samples, members=["small", "large"], start_step=5, end_step=7,
        max_separation_m=limit)
    assert report["validated"] is True
    assert report["sample_count"] == 3
    samples[2]["positions"]["small"] = (5.0, 1.0)
    assert measure_joint_formation(
        samples, members=["small", "large"], start_step=5, end_step=7,
        max_separation_m=limit)["validated"] is False
