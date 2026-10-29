"""Deterministic EMOS/TeamWeaver allocation adapters for candy_shop."""

from __future__ import annotations

from typing import Any, Mapping


def _goals(observation: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    goals = list(observation.get("pending_goals", ()))
    return sorted(goals, key=lambda g: (0 if g.get("kind") in {"handoff", "resource_access", "gate"} else 1,
                                        len(g.get("eligible_robots", ())), str(g.get("goal_key", ""))))


class _CandyBase:
    uses_distance = True

    def initialize(self, observation: Mapping[str, Any]) -> None:
        self.calls = 0
        self.provenance = {"mode": "goal", "scope": "allocation_only", "native_endpoint": False}

    def decide(self, observation: Mapping[str, Any]) -> Mapping[str, Any]:
        available = observation.get("available_robots", {})
        used: set[str] = set()
        result: dict[str, str] = {}
        for goal in _goals(observation):
            choices = [rid for rid in goal.get("eligible_robots", ())
                       if rid in available and rid not in used]
            if not choices:
                continue
            robot = min(choices, key=lambda rid: (float(available[rid].get("distance", 0.0)), rid))
            result[robot] = str(goal["goal_key"])
            used.add(robot)
        self.calls += 1
        return result

    def snapshot(self) -> Mapping[str, Any]:
        return {"calls": self.calls, "provenance": dict(self.provenance)}

    def restore(self, snapshot: Mapping[str, Any]) -> None:
        self.calls = int(snapshot.get("calls", 0))
        self.provenance = dict(snapshot.get("provenance", {}))


class EmosCandyShopAlgorithm(_CandyBase):
    algorithm_id = "EMOS-candy-shop-case-goal-v1"


class TeamWeaverCandyShopAlgorithm(_CandyBase):
    algorithm_id = "TeamWeaver-candy-shop-allocation-v1"


__all__ = ["EmosCandyShopAlgorithm", "TeamWeaverCandyShopAlgorithm"]
