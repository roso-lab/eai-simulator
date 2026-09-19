"""Separated rendezvous positions for idealized two-member transport.

These are static footprint routes, not a contact or formation controller.
"""

import math
import numpy as np

from EAI.scenario_spec.oracle import InflatedGrid


FORMATION_SLACK_M = 0.35


def joint_formation_limit(pickup_slots, delivery_slots, *, slack_m=FORMATION_SLACK_M):
    """Return a measured center-separation bound for an idealized carry."""
    if not math.isfinite(slack_m) or slack_m <= 0:
        raise ValueError("formation slack must be finite and positive")
    member_ids = tuple(pickup_slots)
    if len(member_ids) != 2 or set(delivery_slots) != set(member_ids):
        raise ValueError("joint formation requires the same two pickup and delivery members")
    distances = []
    for slots in (pickup_slots, delivery_slots):
        first, second = (slots[member] for member in member_ids)
        if (len(first) != 2 or len(second) != 2
                or not all(math.isfinite(float(value)) for value in (*first, *second))):
            raise ValueError("joint formation slots must be finite planar points")
        distances.append(math.dist(first, second))
    return max(distances) + slack_m


def formation_hold_required(*, member, positions, target, max_separation_m):
    """Hold a faster member when its next target would stretch the team further."""
    if member not in positions or len(positions) != 2:
        raise ValueError("formation pacing requires positions for exactly two members")
    if not math.isfinite(max_separation_m) or max_separation_m <= 0:
        raise ValueError("formation separation bound must be finite and positive")
    other = next(identifier for identifier in positions if identifier != member)
    current = math.dist(positions[member][:2], positions[other][:2])
    projected = math.dist(target[:2], positions[other][:2])
    return current >= max_separation_m and projected > current + 1e-9


def measure_joint_formation(samples, *, members, start_step, end_step,
                            max_separation_m):
    """Audit measured member separation between ordered pickup and release."""
    if len(members) != 2 or len(set(members)) != 2:
        raise ValueError("formation audit requires two distinct members")
    if not isinstance(start_step, int) or not isinstance(end_step, int) or end_step < start_step:
        raise ValueError("formation audit requires an ordered integer step window")
    measured = []
    for sample in samples:
        step = sample.get("step")
        positions = sample.get("positions") or {}
        if (not isinstance(step, int) or step < start_step or step > end_step
                or any(member not in positions for member in members)):
            continue
        measured.append((step, math.dist(positions[members[0]][:2], positions[members[1]][:2])))
    maximum = max((distance for _, distance in measured), default=None)
    violations = [
        {"step": step, "separation_m": distance, "bound_m": max_separation_m}
        for step, distance in measured if distance > max_separation_m + 1e-6
    ]
    return {
        "validated": bool(measured) and not violations,
        "sample_count": len(measured),
        "max_measured_separation_m": maximum,
        "max_allowed_separation_m": max_separation_m,
        "violations": violations,
        "scope": "robot_center_separation_between_pickup_and_release",
    }


def joint_route_grid(occupancy, blocked, radius, *, obstacles=()):
    """Add currently solid circular footprints before robot inflation."""
    free = blocked.copy()
    columns = (np.arange(occupancy.width) + .5) * occupancy.resolution
    rows = (occupancy.height - np.arange(occupancy.height) - .5) * occupancy.resolution
    for center, obstacle_radius in obstacles:
        local_x, local_y = occupancy.local(*center[:2])
        free &= ((columns[None, :] - local_x) ** 2 + (rows[:, None] - local_y) ** 2
                 > (obstacle_radius + occupancy.resolution / math.sqrt(2)) ** 2)
    return InflatedGrid(occupancy, radius, free)


def plan_joint_rendezvous(*, occupancy, blocked, members, radii, starts,
                          pickup, delivery, object_radius, tolerance=0.15):
    """Choose opposite pickup/delivery slots, using each member's own radius.

    Reserve terminal tolerance on both sides so accepted poses cannot overlap.
    Never snap an obstructed rendezvous into the shared object-centre cell.
    """
    if len(members) != 2 or len(set(members)) != 2:
        raise ValueError("joint rendezvous requires two distinct members")
    grids = {m: InflatedGrid(occupancy, radii[m], blocked) for m in members}
    # The movable crate is absent from the static map, but still has contacts
    # until pickup. Keep approach paths outside its footprint.
    offsets = {m: radii[m] + object_radius + 0.1 + tolerance for m in members}
    heading = math.atan2(delivery[1] - pickup[1], delivery[0] - pickup[0])
    candidates = []
    for index in range(16):
        angle = heading + math.pi / 2 + index * math.pi / 8
        axis = (math.cos(angle), math.sin(angle))
        slots = [{m: (center[0] + sign * offsets[m] * axis[0],
                      center[1] + sign * offsets[m] * axis[1])
                  for m, sign in zip(members, (1, -1))}
                 for center in (pickup, delivery)]
        paths = {}
        for member in members:
            other = next(m for m in members if m != member)
            # A member that arrives first becomes a stationary obstacle. Do
            # not route its partner through the occupied rendezvous slot.
            grid = joint_route_grid(occupancy, blocked, radii[member], obstacles=[
                (pickup, object_radius), (slots[0][other], radii[other] + tolerance)])
            carry_grid = joint_route_grid(occupancy, blocked, radii[member], obstacles=[
                (slots[1][other], radii[other] + tolerance)])
            # Endpoints remain exact; only a live start displaced by contact
            # may escape from its nearest legal cell, as in normal navigation.
            start = tuple(starts[member][:2])
            if not grid.passable(*grid.cell(*start)):
                cell = grid.nearest_passable(start)
                if cell is None:
                    break
                start = occupancy.world((cell[0] + .5) * grid.resolution,
                                        (cell[1] + .5) * grid.resolution)
            approach = grid.path(start, slots[0][member])
            carry = carry_grid.path(slots[0][member], slots[1][member])
            if approach is None or carry is None:
                break
            paths[member] = (approach, carry)
        if len(paths) == len(members):
            cost = sum(len(path) for pair in paths.values() for path in pair)
            candidates.append((cost, index, slots, paths))
    if not candidates:
        raise ValueError("no separated joint pickup/delivery rendezvous is reachable")
    _, _, slots, paths = min(candidates, key=lambda item: item[:2])
    return {"pickup_slots": slots[0], "delivery_slots": slots[1],
            "paths": paths, "grids": grids, "terminal_tolerance_m": tolerance}
