"""Decision adapter for a second mission.

The skill graph stays the mission definition: tasks, arguments, prerequisites,
windows, and the agents that already satisfy region continuity. The algorithm
under test confirms that team through ``EMOSDiscussionManager`` and then emits
``select_team``, ``submit_plan``, and one ``dispatch_skill`` per skill. The
adapter uses only the existing six goal actions. It does not add an
algorithm-specific action, event, or backend, and it does not replace the
graph with a capability-only team. That replacement is rejected by plan
admission before any skill reaches the house backend.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import time
from typing import Any, Mapping


_POLICY_NAME = "second-mission-goal-deterministic"
_ROUND_TIMEOUT_S = 30.0
_DISCUSSION_LOG_DIR = Path(__file__).resolve().parents[2] / "tmp" / "emos_discussion"
_ACTION_SCHEMA = "eai.benchmark.goal_action"
_GOAL_ACTIONS = frozenset({
    "select_team", "submit_plan", "revise_plan",
    "dispatch_skill", "cancel_skill", "consume_message",
})


def _action(identifier: str, kind: str, payload: Mapping[str, Any], time_s: float) -> dict[str, Any]:
    return {
        "schema": _ACTION_SCHEMA,
        "version": 1,
        "action_id": identifier,
        "submitted_at_s": float(time_s),
        "type": kind,
        "payload": deepcopy(dict(payload)),
    }


def _assignment_policy(request: Any) -> dict[str, str]:
    """Assign one discussion robot to each open slot, in roster order."""
    scenario = request.scenario
    order = list(request.robot_positions)
    profiles = scenario.robot_profiles
    assignments: dict[str, str] = {}
    for subtask_id, spec in scenario.subtasks.items():
        required = set(spec.match_keywords)
        for name in order:
            if name in assignments:
                continue
            profile = profiles.get(name)
            capabilities = set(profile.capabilities) if profile is not None else set()
            if not required <= capabilities:
                continue
            robot_type = profile.robot_type if profile is not None else ""
            if "aerial_navigation" in required and robot_type != "aerial":
                continue
            if required & {"ground_navigation", "stair_traversal"} and robot_type == "aerial":
                continue
            assignments[name] = subtask_id
            break
    if len(assignments) != len(scenario.subtasks):
        open_ids = sorted(set(scenario.subtasks) - set(assignments.values()))
        raise ValueError(f"EMOS left second-mission slots unassigned: {open_ids}")
    return assignments


class SecondMissionEmosAdapter:
    """EMOS discussion manager driving one second mission through GoalSession.

    Allocation uses ``EMOSDiscussionManager.set_assignment_policy``, the same
    public callback the other goal scenes use for a non-LLM episode. The
    returned plan is submitted with ``select_team`` and ``submit_plan``;
    later turns emit one ``dispatch_skill`` after each terminal receipt.
    """

    def __init__(self, skill_graph: Mapping[str, Any]):
        self._graph = deepcopy(dict(skill_graph))
        self._task: dict[str, Any] | None = None
        self._plan: dict[str, Any] | None = None
        self._opened = False
        self._dispatched = 0
        self._terminal: set[str] = set()
        self._stopped = False
        self.trace: list[dict[str, Any]] = []

    def manifest(self) -> Mapping[str, Any]:
        return {
            "kind": "participant",
            "id": "second-mission-emos",
            "version": 1,
            "interface": "eai.benchmark.goal_algorithm_adapter",
            "interface_version": 1,
            "evaluation_track": "goal_driven",
            "input_scope": "participant_visible_events_only",
            "control_scope": "benchmark_managed_low_level_control",
        }

    def initialize(self, public_task: Mapping[str, Any], participant_api: Mapping[str, Any]) -> None:
        self._task = deepcopy(dict(public_task))
        action_types = set((participant_api or {}).get("action_types") or {})
        if action_types - _GOAL_ACTIONS:
            raise ValueError("second-mission adapter refuses an extended action vocabulary")
        self.trace.append({
            "event": "initialize",
            "planner": "EMOSDiscussionManager",
            "policy": _POLICY_NAME,
            "task_id": self._task["task_id"],
        })

    def act(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not self._opened:
            if not any(item["type"] == "episode_started" for item in events):
                return []
            self._opened = True
            self._allocate()
            assert self._plan is not None
            return [
                _action("second_mission_decision_select", "select_team",
                        {"agent_ids": list(self._plan["assigned_team"])}, 0.0),
                _action("second_mission_decision_plan", "submit_plan",
                        {"plan": deepcopy(self._plan)}, 0.0),
            ]
        for item in events:
            if item["type"] != "action_result":
                continue
            status = item["payload"].get("status")
            if status == "succeeded":
                self._terminal.add(item["payload"]["action_id"])
            elif status in {"failed", "cancelled"}:
                self._stopped = True
                self.trace.append({
                    "event": "stop",
                    "reason": status,
                    "action_id": item["payload"].get("action_id"),
                })
        if self._stopped or self._plan is None:
            return []
        tasks = self._plan["tasks"]
        if self._dispatched >= len(tasks):
            return []
        previous = f"second_mission_decision_dispatch_{self._dispatched - 1}"
        if self._dispatched and previous not in self._terminal:
            return []
        index = self._dispatched
        self._dispatched += 1
        now = max((float(item["time_s"]) for item in events), default=0.0)
        task_id = tasks[index]["task_id"]
        self.trace.append({"event": "dispatch_skill", "task_id": task_id, "index": index})
        return [_action(
            f"second_mission_decision_dispatch_{index}", "dispatch_skill",
            {"task_id": task_id, "evidence_refs": []}, now,
        )]

    def close(self) -> None:
        self.trace.append({"event": "close"})

    def _allocate(self) -> None:
        from demo.benchmark.goal_driven_contract import validate_goal_plan
        from demo.benchmark.goal_external_adapters import _ensure_algorithm_roots

        if self._task is None:
            raise RuntimeError("SecondMissionEmosAdapter was not initialized")
        _ensure_algorithm_roots()
        from emos.engine import EMOSDiscussionManager
        from emos.types import EMOSRobotAgentSpec, EMOSScenarioConfig, PositionRule, SubtaskSpec

        pool = {row["id"]: row for row in self._task["candidate_robot_pool"]}
        rewritten = deepcopy(self._graph)
        rewritten["plan_id"] = f"{self._graph['plan_id']}_emos"
        for task in rewritten["tasks"]:
            agents = [str(agent) for agent in task["assigned_agents"]]
            missing = [agent for agent in agents if agent not in pool]
            if missing:
                raise RuntimeError(f"{task['task_id']} names robots outside the pool: {missing}")
            slot_ids = [f"{task['task_id']}#{slot}" for slot in range(len(agents))]
            keywords = list(task["required_capabilities"])
            subtasks = {
                slot_id: SubtaskSpec(
                    id=slot_id,
                    name=task["skill"],
                    description=f"{task['skill']}:{slot_id}",
                    match_keywords=keywords,
                )
                for slot_id in slot_ids
            }
            scenario = EMOSScenarioConfig(
                scenario_id=f"second_mission_{self._task['task_id']}",
                scene_title=str(self._task["mission"]["scene_key"]),
                anchor_label=str(self._task["task_id"]),
                task_description=str(self._task["mission"]["goal"]),
                subtasks=subtasks,
                position_rules={slot_id: PositionRule("fixed", xy=(0.0, 0.0)) for slot_id in slot_ids},
                robot_profiles={},
                preferred_fallback={},
                yellow_subtask_id="",
                green_subtask_id="",
            )
            specs = {
                agent: EMOSRobotAgentSpec(
                    agent_name=agent,
                    robot_type=pool[agent]["mobility"],
                    capabilities=list(pool[agent]["public_capabilities"]),
                )
                for agent in agents
            }
            manager = EMOSDiscussionManager.build_from_agent_specs(
                None, specs, scenario, use_env_resume=False,
                discussion_log_dir=str(_DISCUSSION_LOG_DIR))
            manager.set_assignment_policy(_assignment_policy, name=_POLICY_NAME)
            manager.trigger(1, (0.0, 0.0, 0.0))
            result = None
            deadline = time.monotonic() + _ROUND_TIMEOUT_S
            while result is None and time.monotonic() < deadline:
                result = manager.poll()
                if result is None:
                    time.sleep(0.01)
            if result is None:
                raise RuntimeError(
                    f"EMOS did not assign {task['task_id']} within {_ROUND_TIMEOUT_S:.0f}s")
            chosen: list[str | None] = [None] * len(agents)
            for row in result.values():
                colour = row.subtask_colour
                if colour not in subtasks:
                    continue
                slot = int(str(colour).rsplit("#", 1)[1])
                chosen[slot] = row.robot_name
            if any(agent is None for agent in chosen) or chosen != agents:
                raise RuntimeError(
                    f"EMOS assignment for {task['task_id']} diverged from the skill graph: {chosen}")
            self.trace.append({
                "event": "emos_assignment",
                "manager": "EMOSDiscussionManager",
                "task_id": task["task_id"],
                "agents": agents,
                "discussion_count": manager.discussion_count,
            })
        validate_goal_plan(rewritten, task_spec=self._task)
        self._plan = rewritten
        self.trace.append({"event": "team_selected", "team": list(rewritten["assigned_team"])})
