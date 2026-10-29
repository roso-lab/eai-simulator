"""Deterministic EMOS/TeamWeaver allocation adapters for room_06 logic runs."""

from __future__ import annotations

import math
from typing import Any, Mapping


class _Room06Allocation:
    algorithm_id = "room06-allocation-v1"
    uses_distance = True

    def initialize(self, observation: Mapping[str, Any]) -> None:
        self.calls = 0

    def decide(self, observation: Mapping[str, Any]) -> Mapping[str, Any]:
        robots = {rid: row for rid, row in observation.get("available_robots", {}).items()}
        goals = list(observation.get("pending_goals", ()))
        matched: dict[str, Mapping[str, Any]] = {}

        def eligible(goal: Mapping[str, Any]) -> list[str]:
            required = set(goal.get("required_capabilities", ()))
            return [rid for rid, state in sorted(robots.items())
                    if (not goal.get("required_domain") or state.get("domain") == goal["required_domain"])
                    and required.issubset(set(state.get("capabilities", ())))
                    and rid in goal.get("eligible_robots", tuple(robots))]

        def augment(goal: Mapping[str, Any], seen: set[str]) -> bool:
            for rid in eligible(goal):
                if rid in seen:
                    continue
                seen.add(rid)
                if rid not in matched or augment(matched[rid], seen):
                    matched[rid] = goal
                    return True
            return False

        # Resource and response goals get first chance; then the most
        # constrained relay stage. This is a standard allocation decision and
        # leaves route, transport and completion adjudication to the runtime.
        goals.sort(key=lambda g: (0 if g.get("kind") == "response" else 1,
                                  len(eligible(g)), str(g.get("goal_key", ""))))
        for goal in goals:
            augment(goal, set())
        self.calls += 1
        return {rid: str(goal["goal_key"]) for rid, goal in sorted(matched.items())}

    def snapshot(self) -> Mapping[str, Any]:
        return {"calls": self.calls}

    def restore(self, snapshot: Mapping[str, Any]) -> None:
        self.calls = int(snapshot.get("calls", 0))


class EmosRoom06Algorithm(_Room06Allocation):
    algorithm_id = "EMOS-room06-case-goal-v1"


class TeamWeaverRoom06Algorithm(_Room06Allocation):
    algorithm_id = "TeamWeaver-room06-allocation-v1"


__all__ = ["EmosRoom06Algorithm", "TeamWeaverRoom06Algorithm"]
