"""Deterministic EMOS/TeamWeaver-compatible allocation seams for factory."""
from __future__ import annotations

from typing import Any, Mapping


def _allocate(observation: Mapping[str, Any]) -> dict[str, str]:
    available = observation.get("available_robots", {})
    rejected = {(str(item.get("robot_id")), str(item.get("task_id")))
                for item in observation.get("rejected_assignments", ())}
    goals = list(observation.get("pending_goals", ()))
    # Incident and manipulation actions have priority.  Deliberately ignore
    # ``eligible_robots`` here: capability mistakes are part of the measured
    # algorithm behaviour.  The runtime rejects the proposal and records it.
    goals.sort(key=lambda g: (0 if g.get("kind") in {"incident_navigation", "rescue", "fire_sample", "button_press", "manipulation", "handoff"} else 1, str(g.get("goal_key", ""))))
    result: dict[str, str] = {}
    used: set[str] = set()
    for goal in goals:
        candidates = [rid for rid, row in sorted(available.items())
                      if rid not in used and (rid, str(goal.get("task_id"))) not in rejected
                      and (not goal.get("required_domain") or row.get("domain") == goal["required_domain"])]
        if not candidates:
            continue
        rid = min(candidates, key=lambda x: (float(available[x].get("distance", float("inf"))), x))
        result[rid] = str(goal["goal_key"])
        used.add(rid)
    return result


class EmosFactoryAlgorithm:
    algorithm_id = "EMOS-factory-case-goal-v1"
    uses_distance = True

    def initialize(self, observation: Mapping[str, Any]) -> None:
        self.calls = 0

    def decide(self, observation: Mapping[str, Any]) -> Mapping[str, Any]:
        self.calls += 1
        return _allocate(observation)

    def snapshot(self) -> Mapping[str, Any]:
        return {"calls": self.calls}

    def restore(self, snapshot: Mapping[str, Any]) -> None:
        self.calls = int(snapshot.get("calls", 0))


class TeamWeaverFactoryAlgorithm(EmosFactoryAlgorithm):
    algorithm_id = "TeamWeaver-factory-allocation-v1"


__all__ = ["EmosFactoryAlgorithm", "TeamWeaverFactoryAlgorithm"]
