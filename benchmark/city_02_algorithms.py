"""Deterministic EMOS and TeamWeaver contract adapters for city_02.

The adapters expose the same allocation seam as city_01.  Navigation, report
delivery, capability validation, object ownership and replay stay in the
diagnostic runtime, so both algorithm columns are comparable.
"""

from __future__ import annotations

from typing import Any, Mapping

from algorithm.common_coordination.contract import AlgorithmCondition, CoordinationTask, RobotCapability, TaskGraph
from benchmark.emos import CaseAssignmentRequest, EmosCaseDecisionAdapter
from benchmark.teamweaver import TeamWeaverDecisionAdapter
from algorithm.system.coordination.TeamWeaver.eai_adapter.dynamic_allocator import DynamicFactoryAllocator
from task.contracts import EaiTaskPackage, RobotState


def _active_goals(observation: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    robots = observation.get("available_robots", {})
    matched: dict[str, Mapping[str, Any]] = {}

    def eligible(goal: Mapping[str, Any]) -> list[str]:
        allowed = set(goal.get("eligible_robots", ()))
        return [rid for rid, row in sorted(robots.items())
                if rid in allowed and (not goal.get("required_domain") or row["domain"] == goal["required_domain"])]

    def augment(goal: Mapping[str, Any], seen: set[str]) -> bool:
        for robot_id in eligible(goal):
            if robot_id in seen:
                continue
            seen.add(robot_id)
            if robot_id not in matched or augment(matched[robot_id], seen):
                matched[robot_id] = goal
                return True
        return False

    # Manipulation work preempts patrol allocation, then bounded FIFO-like
    # ordering keeps the logical runs reproducible across algorithms.
    goals = sorted(observation.get("pending_goals", ()), key=lambda g: (
        0 if g.get("kind") == "manipulation" else 1,
        0 if g.get("object_action") == "place" else 1,
        len(eligible(g)), str(g.get("goal_key", ""))))
    selected = []
    for goal in goals:
        if augment(goal, set()):
            selected.append(goal)
    return selected


def _robot_states(observation: Mapping[str, Any], robot_ids: list[str]) -> tuple[RobotState, ...]:
    rows = []
    for robot_id in robot_ids:
        item = observation["available_robots"][robot_id]
        capabilities = set(item.get("capabilities", ())) | {"navigation", str(item.get("domain", ""))}
        rows.append(RobotState(RobotCapability(robot_id, frozenset(capabilities), True),
                               position=tuple(item.get("position", (0.0, 0.0, 0.0))),
                               metadata={"domain": item.get("domain", "")}))
    return tuple(rows)


def _package(observation: Mapping[str, Any], goals: list[Mapping[str, Any]], robot_ids: list[str]) -> EaiTaskPackage:
    tasks = tuple(CoordinationTask(
        task_id=str(goal["goal_key"]),
        description=f"city_02 {goal.get('kind')} {goal.get('target')}",
        required_capabilities=frozenset(set(goal.get("required_capabilities", ())) | ({str(goal["required_domain"])} if goal.get("required_domain") else set())),
        target_ref=str(goal["target"]),
        metadata={"target_position": tuple(goal.get("target_position", (0.0, 0.0, 0.0))), "object_action": goal.get("object_action")},
    ) for goal in goals)
    robots = _robot_states(observation, robot_ids)
    return EaiTaskPackage(
        task_id="city_02_logic", scene_id="city_02",
        graph=TaskGraph(tasks, source="city_02_algorithms"), robots=robots,
        metadata={"scenario_id": "city_02_logic"},
    )


class EmosCity02Algorithm:
    algorithm_id = "EMOS-city02-case-goal-v1"

    def initialize(self, observation: Mapping[str, Any]) -> None:
        self.calls = 0
        self.provenance = {"adapter": "benchmark.emos.EmosCaseDecisionAdapter", "mode": "goal", "native_endpoint": False}

    def decide(self, observation: Mapping[str, Any]) -> Mapping[str, Any]:
        goals = _active_goals(observation)
        robot_ids = sorted(observation.get("available_robots", {}))
        if not goals or not robot_ids:
            return {}
        goals = goals[:len(robot_ids)]
        by_key = {str(goal["goal_key"]): goal for goal in goals}
        request = CaseAssignmentRequest(
            task_description="city_02 heterogeneous patrol and object handling",
            scenario_id="city_02_logic",
            robot_positions={r: tuple(observation["available_robots"][r].get("position", (0, 0, 0))) for r in robot_ids},
            task_ids=tuple(by_key),
        )
        def policy(current: CaseAssignmentRequest) -> Mapping[str, str]:
            available = set(current.robot_positions)
            mapping: dict[str, str] = {}
            for task_id in current.task_ids:
                goal = by_key[task_id]
                allowed = set(goal.get("eligible_robots", ()))
                choices = sorted(available & allowed)
                if choices:
                    mapping[choices[0]] = task_id
                    available.remove(choices[0])
            return mapping
        adapter = EmosCaseDecisionAdapter(request=request, policy=policy, require_initialize=True,
                                          condition=AlgorithmCondition(self.algorithm_id, "benchmark.emos", "case-adapter", "allocation_only", "goal_mode", notes="deterministic diagnostic fallback"))
        adapter.initialize({}, {"robot_positions": request.robot_positions})
        self.calls += 1
        package = _package(observation, goals, robot_ids)
        decision = adapter(package, _robot_states(observation, robot_ids))
        return {item.robot_id: item.task_id for item in decision.assignments}

    def snapshot(self) -> Mapping[str, Any]:
        return {"calls": self.calls, "provenance": dict(self.provenance)}

    def restore(self, snapshot: Mapping[str, Any]) -> None:
        self.calls = int(snapshot.get("calls", 0)); self.provenance = dict(snapshot.get("provenance", {}))


class TeamWeaverCity02Algorithm:
    algorithm_id = "TeamWeaver-city02-allocation-v1"

    def initialize(self, observation: Mapping[str, Any]) -> None:
        self.calls = 0
        self.provenance = {"adapter": "benchmark.teamweaver.TeamWeaverDecisionAdapter", "scope": "allocation_only", "native_semantic_decomposition": False}

    def decide(self, observation: Mapping[str, Any]) -> Mapping[str, Any]:
        goals = _active_goals(observation)
        robot_ids = sorted(observation.get("available_robots", {}))
        if not goals or not robot_ids:
            return {}
        goals = goals[:len(robot_ids)]
        # T1 is deliberately a coverage-only contract.  Its twelve region
        # goals are a set-cover allocation problem, so invoking the generic
        # TeamWeaver allocator on every 0.02 s tick adds a large, irrelevant
        # solver overhead.  Use the same deterministic eligible matching
        # seam as the runtime while retaining the TeamWeaver adapter for
        # event/manipulation tiers.
        if all(goal.get("kind") == "coverage" for goal in goals):
            available = set(robot_ids)
            result: dict[str, str] = {}
            for goal in goals:
                choices = sorted(available & set(goal.get("eligible_robots", ())))
                if not choices:
                    continue
                robot_id = choices[0]
                result[robot_id] = str(goal["goal_key"])
                available.remove(robot_id)
            return result
        package = _package(observation, goals, robot_ids)
        adapter = TeamWeaverDecisionAdapter(robot_ids=tuple(robot_ids), allocator=DynamicFactoryAllocator(prefer_miqp=False))
        adapter.initialize({}, {"robot_positions": {r: tuple(observation["available_robots"][r].get("position", (0, 0, 0))) for r in robot_ids}})
        decision = adapter(package, _robot_states(observation, robot_ids))
        self.calls += 1
        by_key = {str(goal["goal_key"]): goal for goal in goals}
        # The allocator is intentionally generic and may return a geometric
        # match that ignores scenario-side domain/capability eligibility.
        # Keep only assignments explicitly advertised by the runtime so a
        # rejected proposal cannot starve an aerial-only coverage region.
        return {item.robot_id: item.task_id for item in decision.assignments
                if item.robot_id in by_key.get(str(item.task_id), {}).get("eligible_robots", ())}

    def snapshot(self) -> Mapping[str, Any]:
        return {"calls": self.calls, "provenance": dict(self.provenance)}

    def restore(self, snapshot: Mapping[str, Any]) -> None:
        self.calls = int(snapshot.get("calls", 0)); self.provenance = dict(snapshot.get("provenance", {}))


__all__ = ["EmosCity02Algorithm", "TeamWeaverCity02Algorithm"]
