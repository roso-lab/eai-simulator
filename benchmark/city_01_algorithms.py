"""EMOS and TeamWeaver adapters for the city_01 logical runtime.

Both classes preserve the algorithm-specific benchmark adapter boundary.  They
produce allocation decisions only; movement, resources, communication and
success remain owned by :class:`DiagnosticRuntime`.
"""

from __future__ import annotations

from typing import Any, Mapping

from algorithm.common_coordination.contract import (
    AlgorithmCondition, CoordinationTask, RobotCapability,
    TaskGraph,
)
from benchmark.emos import CaseAssignmentRequest, EmosCaseDecisionAdapter
from benchmark.teamweaver import TeamWeaverDecisionAdapter
from algorithm.system.coordination.TeamWeaver.eai_adapter.dynamic_allocator import DynamicFactoryAllocator
from task.contracts import EaiTaskPackage, RobotState


def _active_goals(observation: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """Select a feasible batch; busy or held robots cannot take new work.

    Augmenting-path matching prevents Hall-condition failures when several
    tasks share a restricted eligible roster. This is adapter batch formation,
    not a replacement for either algorithm's allocation decision.
    """
    robots = observation.get("available_robots", {})
    matched: dict[str, Mapping[str, Any]] = {}

    def eligible(goal):
        return [r for r, state in sorted(robots.items())
                if (not goal.get("required_domain") or state["domain"] == goal["required_domain"])
                and r in goal.get("eligible_robots", robots)]

    def augment(goal, seen):
        for robot in eligible(goal):
            if robot in seen:
                continue
            seen.add(robot)
            if robot not in matched or augment(matched[robot], seen):
                matched[robot] = goal
                return True
        return False

    selected = []
    for goal in sorted(observation.get("pending_goals", ()),
                       key=lambda g: (0 if g.get("kind") == "recover" else 1,
                                      0 if g.get("kind") == "response" else 1,
                                      len(eligible(g)), g["goal_key"])):
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
                               metadata={"domain": item.get("domain", "") }))
    return tuple(rows)


def _package(observation: Mapping[str, Any], goals: list[Mapping[str, Any]], robot_ids: list[str]) -> EaiTaskPackage:
    tasks = tuple(CoordinationTask(
        task_id=str(goal["goal_key"]),
        description=f"city_01 reach {goal['target']}",
        required_capabilities=frozenset({str(goal["required_domain"])} if goal.get("required_domain") else
                                        ({"aerial"} if str(goal["target"]).startswith("accident_aerial") else set())),
        target_ref=str(goal["target"]),
        metadata={"target_position": tuple(goal.get("target_position", (0.0, 0.0, 0.0)))},
    ) for goal in goals)
    robots = _robot_states(observation, robot_ids)
    positions = {row.capability.robot_id: row.position for row in robots}
    targets = {task.task_id: task.metadata["target_position"] for task in tasks}
    return EaiTaskPackage(
        task_id="city_01_logic",
        scene_id="city_01",
        graph=TaskGraph(tasks, source="city_01_algorithms"),
        robots=robots,
        metadata={"scenario_id": "city_01_logic", "target_positions": targets,
                  "robot_positions": positions},
    )


class EmosCity01Algorithm:
    """EMOS case adapter in deterministic goal mode.

    The fallback policy is explicitly recorded as a diagnostic EMOS adapter;
    setting ``model_endpoint`` requires a caller-provided native endpoint and
    produces separate provenance.
    """

    algorithm_id = "EMOS-city01-case-goal-v1"

    def initialize(self, observation: Mapping[str, Any]) -> None:
        self.calls = 0
        self.provenance = {"adapter": "benchmark.emos.EmosCaseDecisionAdapter",
                           "mode": "goal", "native_endpoint": False}

    def decide(self, observation: Mapping[str, Any]) -> Mapping[str, Any]:
        goals = _active_goals(observation)
        robots = sorted(observation.get("available_robots", {}))
        if not goals or not robots:
            return {}
        # One round is limited to currently idle robots. Remaining goals stay
        # in the runtime ledger and are offered in the next allocation round.
        # Incident observation is an aerial-only obligation. Flexible area
        # coverage remains available to either modality.
        aerial = [goal for goal in goals if str(goal["target"]).startswith("accident_aerial")]
        flexible = [goal for goal in goals if goal not in aerial]
        goals = (aerial + flexible)[:len(robots)]
        request = CaseAssignmentRequest(
            task_description="city_01 heterogeneous patrol and incident goals",
            scenario_id="city_01_logic",
            robot_positions={r: tuple(observation["available_robots"][r].get("position", (0, 0, 0))) for r in robots},
            task_ids=tuple(str(g["goal_key"]) for g in goals),
        )
        goal_by_key = {str(g["goal_key"]): g for g in goals}
        def policy(current: CaseAssignmentRequest) -> Mapping[str, str]:
            available = set(current.robot_positions)
            mapping: dict[str, str] = {}
            for task_id in current.task_ids:
                goal = goal_by_key[task_id]
                required = goal.get("required_domain") or ("aerial" if str(goal["target"]).startswith("accident_aerial") else None)
                choices = [robot for robot in sorted(available)
                           if (required is None or observation["available_robots"][robot].get("domain") == required)
                           and robot in goal.get("eligible_robots", available)]
                if not choices:
                    continue
                robot = choices[0]
                mapping[robot] = task_id
                available.remove(robot)
            return mapping
        adapter = EmosCaseDecisionAdapter(request=request, policy=policy, require_initialize=True,
                                          condition=AlgorithmCondition(
                                              self.algorithm_id, "benchmark.emos", "case-adapter",
                                              "allocation_only", "goal_mode", notes="deterministic diagnostic fallback"))
        adapter.initialize({}, {"robot_positions": request.robot_positions, "phase": "city_01"})
        package = _package(observation, goals, robots)
        decision = adapter(package, _robot_states(observation, robots))
        self.calls += 1
        assignments = {item.robot_id: item.task_id for item in decision.assignments}
        # The in-tree allocator has no native robot-identity constraint for a
        # distinct response group. Preserve the allocator's task choices, but
        # repair only this representational gap before the runtime validates
        # the hard group invariant.
        pending_by_key = {str(goal["goal_key"]): goal for goal in goals}
        used = set(assignments)
        repaired = {}
        for robot_id, task_id in assignments.items():
            goal = pending_by_key.get(task_id)
            allowed = set(goal.get("eligible_robots", ())) if goal else set()
            if allowed and robot_id not in allowed:
                alternatives = sorted(allowed - used - set(repaired))
                if alternatives:
                    repaired[alternatives[0]] = task_id
                    continue
            repaired[robot_id] = task_id
        return repaired

    def snapshot(self) -> Mapping[str, Any]:
        return {"calls": self.calls, "provenance": dict(self.provenance)}

    def restore(self, snapshot: Mapping[str, Any]) -> None:
        self.calls = int(snapshot.get("calls", 0)); self.provenance = dict(snapshot.get("provenance", {}))


class TeamWeaverCity01Algorithm:
    """TeamWeaver contract allocator for city_01 goal batches."""

    algorithm_id = "TeamWeaver-city01-allocation-v1"

    def initialize(self, observation: Mapping[str, Any]) -> None:
        self.calls = 0
        self.provenance = {"adapter": "benchmark.teamweaver.TeamWeaverDecisionAdapter",
                           "scope": "allocation_only", "native_semantic_decomposition": False}

    def decide(self, observation: Mapping[str, Any]) -> Mapping[str, Any]:
        goals = _active_goals(observation)
        robots = sorted(observation.get("available_robots", {}))
        if not goals or not robots:
            return {}
        aerial = [goal for goal in goals if str(goal["target"]).startswith("accident_aerial")]
        flexible = [goal for goal in goals if goal not in aerial]
        goals = (aerial + flexible)[:len(robots)]
        package = _package(observation, goals, robots)
        # The logical diagnostic path must remain fast and reproducible.  The
        # native TeamWeaver MIQP remains a separate provenance column; using
        # its deterministic Hungarian fallback here avoids solving the same
        # tiny assignment at every 20 ms control tick.
        adapter = TeamWeaverDecisionAdapter(robot_ids=tuple(robots),
                                            allocator=DynamicFactoryAllocator(prefer_miqp=False))
        adapter.initialize({}, {"robot_positions": {r: tuple(observation["available_robots"][r].get("position", (0, 0, 0))) for r in robots}})
        decision = adapter(package, _robot_states(observation, robots))
        self.calls += 1
        assignments = {item.robot_id: item.task_id for item in decision.assignments}
        pending_by_key = {str(goal["goal_key"]): goal for goal in goals}
        used = set(assignments)
        repaired = {}
        for robot_id, task_id in assignments.items():
            goal = pending_by_key.get(task_id)
            allowed = set(goal.get("eligible_robots", ())) if goal else set()
            if allowed and robot_id not in allowed:
                alternatives = sorted(allowed - used - set(repaired))
                if alternatives:
                    repaired[alternatives[0]] = task_id
                    continue
            repaired[robot_id] = task_id
        return repaired

    def snapshot(self) -> Mapping[str, Any]:
        return {"calls": self.calls, "provenance": dict(self.provenance)}

    def restore(self, snapshot: Mapping[str, Any]) -> None:
        self.calls = int(snapshot.get("calls", 0)); self.provenance = dict(snapshot.get("provenance", {}))


__all__ = ["EmosCity01Algorithm", "TeamWeaverCity01Algorithm"]
