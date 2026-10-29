"""Deterministic goal-mode baselines for the living-house logical track."""

from __future__ import annotations

import math
from typing import Any, Mapping


class LivingHouseGoalAlgorithm:
    """Nearest eligible goal with continuation and capability awareness.

    The evaluator still receives every proposal and records invalid
    assignments.  ``eligible_robots`` is used only as the controller's public
    observation of protocol constraints; it is not an adapter-side filter.
    """

    algorithm_id = "living_house_goal_greedy_v1"
    uses_distance = True

    def __init__(self) -> None:
        self.assignments: dict[str, str] = {}

    def initialize(self, observation: Mapping[str, Any]) -> None:
        self.assignments = {}

    def decide(self, observation: Mapping[str, Any]) -> Mapping[str, Any]:
        available = dict(observation.get("available_robots", {}))
        goals = list(observation.get("pending_goals", ()))
        result: dict[str, str] = {}
        used: set[str] = set()
        for goal in sorted(goals, key=lambda item: (
            len(item.get("reachable_robots", ())) if item.get("reachable_robots") else 999,
            0 if item.get("continuation_group") else 1,
            item.get("task_id", ""), item.get("target", ""))):
            eligible = set(goal.get("eligible_robots", ()))
            reachable = set(goal.get("reachable_robots", ()))
            candidates = [row for rid, row in available.items()
                          if rid not in used and (not eligible or rid in eligible)
                          and (not reachable or rid in reachable)
                          and (not goal.get("required_domain") or row.get("domain") == goal["required_domain"])]
            if not candidates:
                continue
            reachable = [row for row in candidates if math.isfinite(float(row.get("distance", math.inf)))]
            if not reachable:
                continue
            candidates = reachable
            selected = min(candidates, key=lambda row: (float(row.get("distance", math.inf)), row["robot_id"]))
            result[selected["robot_id"]] = str(goal["goal_key"])
            used.add(selected["robot_id"])
        self.assignments.update(result)
        return result

    def snapshot(self) -> Mapping[str, Any]:
        return {"assignments": dict(sorted(self.assignments.items()))}

    def restore(self, snapshot: Mapping[str, Any]) -> None:
        self.assignments = dict(snapshot.get("assignments", {}))


class EmosLivingHouseAlgorithm(LivingHouseGoalAlgorithm):
    algorithm_id = "emos_living_house_goal_v1"


class TeamWeaverLivingHouseAlgorithm(LivingHouseGoalAlgorithm):
    algorithm_id = "teamweaver_living_house_goal_v1"


__all__ = ["LivingHouseGoalAlgorithm", "EmosLivingHouseAlgorithm", "TeamWeaverLivingHouseAlgorithm"]
