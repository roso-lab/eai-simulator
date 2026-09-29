"""Decision adapter for a second mission.

The skill graph defines tasks, arguments, prerequisites and windows. It does
not define the team. Team selection and every task assignment come from the
algorithm under test, over the public candidate pool. The measured path is
``EMOSDiscussionManager`` real LLM discussion (``use_llm_discussion=True``).
If that discussion is unavailable, the episode records ``unavailable`` and
stops. It does not substitute a deterministic policy.

``use_llm_discussion=False`` is an explicitly labeled diagnostic mode. Its
source is ``deterministic_fallback`` and it is not a measured decision.
Allocation gaps and mismatches are recorded. They do not raise. A schema-valid
mismatch is admitted and fails later as one ``constraint_violation`` receipt.
"""

from __future__ import annotations

import os
from copy import deepcopy
from pathlib import Path
import time
from typing import Any, Mapping


ALLOCATION_REAL_LLM = "real_llm"
ALLOCATION_DIAGNOSTIC = "deterministic_fallback"
ALLOCATION_UNAVAILABLE = "unavailable"

_POLICY_NAME = "second-mission-diagnostic"
_ROUND_TIMEOUT_S = 240.0
_DIAGNOSTIC_TIMEOUT_S = 5.0
# The engine writes chat history below <dir>/<episode_id>/ with a fixed
# episode id, so concurrent processes must isolate this directory via
# EAI_EMOS_DISCUSSION_DIR instead of sharing tmp/emos_discussion.
_DISCUSSION_LOG_DIR = Path(os.environ.get("EAI_EMOS_DISCUSSION_DIR")
                           or Path(__file__).resolve().parents[2] / "tmp" / "emos_discussion")
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


def _diagnostic_assignment_policy(request: Any) -> dict[str, str]:
    """Capability walk used only by the labeled non-LLM diagnostic mode.

    An incapable robot is still assigned when nobody covers the keywords, so
    a wrong assignment remains a measurement instead of an exception.
    """
    scenario = request.scenario
    order = list(request.robot_positions)
    profiles = scenario.robot_profiles
    assignments: dict[str, str] = {}
    used: set[str] = set()
    for subtask_id, spec in scenario.subtasks.items():
        required = set(spec.match_keywords)
        chosen = None
        for name in order:
            if name in used:
                continue
            profile = profiles.get(name)
            capabilities = set(profile.capabilities) if profile is not None else set()
            robot_type = profile.robot_type if profile is not None else ""
            if required and not required <= capabilities:
                continue
            if "aerial_navigation" in required and robot_type != "aerial":
                continue
            if required & {"ground_navigation", "stair_traversal"} and robot_type == "aerial":
                continue
            chosen = name
            break
        if chosen is None:
            for name in order:
                if name not in used:
                    chosen = name
                    break
        if chosen is None:
            continue
        assignments[chosen] = subtask_id
        used.add(chosen)
    if not assignments:
        raise ValueError("diagnostic policy found no robot in the public pool")
    return assignments


def _slot_count(task: Mapping[str, Any]) -> int:
    from demo.benchmark.skill_ontology import minimum_agents

    arguments = task.get("arguments") or {}
    bindings = arguments.get("carrier_bindings")
    binding_count = len(bindings) if isinstance(bindings, list) else 0
    if binding_count:
        return binding_count
    return max(minimum_agents(str(task["skill"])), 1)


def measured_llm_channel() -> dict[str, str]:
    """Credential channel for the measured EMOS discussion.

    The discussion client reads ``os.environ[key_env]`` before any default.
    A dongli gateway therefore has to be named ``DONGLI_API_KEY``; a different
    provider key stored in ``DEEPSEEK_API_KEY`` is not a substitute.
    """
    import os
    import re

    from demo.benchmark.goal_external_adapters import _city_llm_env

    env = _city_llm_env()
    # An explicit TeamWeaver override wins. Otherwise use the operator's
    # dongli endpoint settings; the helper's built-in default is only the
    # last resort.
    base_url = (
        os.environ.get("TEAMWEAVER_DEEPSEEK_BASE_URL", "").strip()
        or os.environ.get("DONGLI_BASE_URL", "").strip()
        or env["TEAMWEAVER_DEEPSEEK_BASE_URL"])
    model = (
        os.environ.get("TEAMWEAVER_DEEPSEEK_MODEL", "").strip()
        or os.environ.get("DONGLI_MODEL", "").strip()
        or env["TEAMWEAVER_DEEPSEEK_MODEL"])
    dongli = os.environ.get("DONGLI_API_KEY", "").strip()
    if not dongli:
        secrets = Path.home() / ".secrets"
        if secrets.is_file():
            match = re.search(r'DONGLI_API_KEY="([^"]+)"', secrets.read_text(encoding="utf-8"))
            dongli = match.group(1) if match else ""
    if "dongli" in base_url.lower() and dongli:
        return {"key_env": "DONGLI_API_KEY", "api_key": dongli, "base_url": base_url, "model": model}
    return {
        "key_env": "DEEPSEEK_API_KEY",
        "api_key": env["DEEPSEEK_API_KEY"],
        "base_url": base_url,
        "model": model,
    }


def _public_argument_text(task: Mapping[str, Any], index: int = 0) -> str:
    """Public per-slot brief: which carrier point this slot is bound to.

    Slot ``task#index`` maps to ``carrier_bindings[index]``; disclosing that
    mapping keeps the adapter's positional rebind honest, so a wrong-point
    or far-robot choice is the algorithm's informed decision rather than an
    adapter translation error.
    """
    arguments = dict(task.get("arguments") or {})
    bindings = arguments.pop("carrier_bindings", None)
    lines = [f"skill={task['skill']} arguments={arguments}"]
    if isinstance(bindings, list) and bindings:
        ordered = [row.get("point_id") for row in bindings if isinstance(row, Mapping)]
        own = None
        if 0 <= index < len(bindings) and isinstance(bindings[index], Mapping):
            own = bindings[index].get("point_id")
        lines.append(
            f"carrier points in slot order={ordered}; this slot #{index} carries point {own}")
    lines.append("robot positions are not part of this contract")
    return "; ".join(lines)


class SecondMissionEmosAdapter:
    """EMOS discussion manager driving one second mission through GoalSession."""

    def __init__(self, skill_graph: Mapping[str, Any], *, use_llm_discussion: bool = True):
        self._graph = deepcopy(dict(skill_graph))
        self._use_llm = bool(use_llm_discussion)
        self._task: dict[str, Any] | None = None
        self._plan: dict[str, Any] | None = None
        self._opened = False
        self._dispatched = 0
        self._terminal: set[str] = set()
        self._stopped = False
        self.allocation_source: str | None = None
        self.submitted_plan_id: str | None = None
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
            "task_id": self._task["task_id"],
            "use_llm_discussion": self._use_llm,
            "candidate_pool": [row["id"] for row in self._task["candidate_robot_pool"]],
        })

    def act(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not self._opened:
            if not any(item["type"] == "episode_started" for item in events):
                return []
            self._opened = True
            self._allocate()
            if self._plan is None:
                self._stopped = True
                return []
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
        self.trace.append({
            "event": "dispatch_skill",
            "task_id": task_id,
            "index": index,
            "allocation_source": self.allocation_source,
        })
        return [_action(
            f"second_mission_decision_dispatch_{index}", "dispatch_skill",
            {"task_id": task_id, "evidence_refs": []}, now,
        )]

    def close(self) -> None:
        self.trace.append({"event": "close", "allocation_source": self.allocation_source})

    def _allocate(self) -> None:
        try:
            self._allocate_from_pool()
        except Exception as exc:
            self._plan = None
            if self.allocation_source is None:
                self.allocation_source = (
                    ALLOCATION_DIAGNOSTIC if not self._use_llm else ALLOCATION_UNAVAILABLE)
            self.trace.append({
                "event": "allocation_failed",
                "allocation_source": self.allocation_source,
                "error_type": type(exc).__name__,
                "error": str(exc)[:500],
            })

    def _allocate_from_pool(self) -> None:
        from demo.benchmark.goal_external_adapters import _ensure_algorithm_roots

        if self._task is None:
            raise RuntimeError("SecondMissionEmosAdapter was not initialized")
        _ensure_algorithm_roots()
        from emos import engine as emos_engine
        from emos.engine import EMOSDiscussionManager
        from emos.types import EMOSRobotAgentSpec, EMOSScenarioConfig, PositionRule, SubtaskSpec

        pool = list(self._task["candidate_robot_pool"])
        pool_ids = [row["id"] for row in pool]
        tasks = [deepcopy(dict(task)) for task in self._graph["tasks"]]
        for task in tasks:
            task["assigned_agents"] = []
        slots: list[tuple[str, int]] = []
        for task in tasks:
            for index in range(_slot_count(task)):
                slots.append((task["task_id"], index))
        assigned: dict[str, list[str | None]] = {
            task["task_id"]: [None] * _slot_count(task) for task in tasks
        }
        llm_env = None
        if not self._use_llm:
            self.allocation_source = ALLOCATION_DIAGNOSTIC
        elif not emos_engine.LLM_AVAILABLE:
            self.allocation_source = ALLOCATION_UNAVAILABLE
            self.trace.append({
                "event": "allocation_aborted",
                "allocation_source": ALLOCATION_UNAVAILABLE,
                "reason": "llm_module_unavailable",
            })
            return
        else:
            try:
                llm_env = measured_llm_channel()
            except RuntimeError:
                self.allocation_source = ALLOCATION_UNAVAILABLE
                self.trace.append({
                    "event": "allocation_aborted",
                    "allocation_source": ALLOCATION_UNAVAILABLE,
                    "reason": "llm_credentials_unavailable",
                })
                return
            self.allocation_source = ALLOCATION_REAL_LLM

        def llm_kwargs() -> dict[str, Any]:
            assert llm_env is not None
            return {
                "llm_model": llm_env["model"],
                "llm_base_url": llm_env["base_url"],
                "llm_api_key_env": llm_env["key_env"],
                "llm_api_key_default": llm_env["api_key"],
                "llm_display_label": f"{llm_env['key_env'].split('_')[0].lower()}:{llm_env['model']}",
                "llm_preserve_raw_assignments": True,
            }

        remaining = list(slots)
        saw_real_llm = False
        for round_index in range(max(len(slots), 1)):
            if not remaining:
                break
            subtasks = {}
            for task_id, index in remaining:
                slot_id = f"{task_id}#{index}"
                task = next(row for row in tasks if row["task_id"] == task_id)
                keywords = [slot_id] if self._use_llm else list(task["required_capabilities"])
                subtasks[slot_id] = SubtaskSpec(
                    id=slot_id,
                    name=task["skill"],
                    description=(
                        f"Reply must contain {slot_id}. "
                        f"{_public_argument_text(task, index)}"
                    ),
                    match_keywords=keywords,
                )
            scenario = EMOSScenarioConfig(
                scenario_id=f"second_mission_{self._task['task_id']}",
                scene_title=str(self._task["mission"]["scene_key"]),
                anchor_label=str(self._task["task_id"]),
                task_description=str(self._task["mission"]["goal"]),
                subtasks=subtasks,
                position_rules={slot_id: PositionRule("fixed", xy=(0.0, 0.0)) for slot_id in subtasks},
                robot_profiles={},
                preferred_fallback={},
                yellow_subtask_id="",
                green_subtask_id="",
            )
            specs = {
                row["id"]: EMOSRobotAgentSpec(
                    agent_name=row["id"],
                    robot_type=row["mobility"],
                    capabilities=list(row["public_capabilities"]),
                )
                for row in pool
            }
            manager = EMOSDiscussionManager.build_from_agent_specs(
                None, specs, scenario, use_env_resume=False,
                discussion_log_dir=str(_DISCUSSION_LOG_DIR),
                get_group_discussion_llm_kwargs=(llm_kwargs if llm_env is not None else None))
            if not self._use_llm:
                manager.set_assignment_policy(_diagnostic_assignment_policy, name=_POLICY_NAME)
            before = manager.discussion_count
            manager.trigger(1, (0.0, 0.0, 0.0))
            result = None
            deadline = time.monotonic() + (
                _ROUND_TIMEOUT_S if self._use_llm else _DIAGNOSTIC_TIMEOUT_S)
            try:
                while result is None and time.monotonic() < deadline:
                    result = manager.poll()
                    if result is None:
                        time.sleep(0.05)
            except RuntimeError as exc:
                self.trace.append({
                    "event": "allocation_failed",
                    "allocation_source": self.allocation_source,
                    "round": round_index,
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:500],
                })
                result = None
            if result is None:
                self.trace.append({
                    "event": "allocation_aborted",
                    "allocation_source": self.allocation_source,
                    "round": round_index,
                    "reason": "discussion_timeout" if self._use_llm else "diagnostic_no_result",
                    "open_slots": [f"{task_id}#{index}" for task_id, index in remaining],
                })
                if self._use_llm and not saw_real_llm:
                    self.allocation_source = ALLOCATION_UNAVAILABLE
                break
            if self._use_llm and manager.last_result_source != "llm":
                result_source = manager.last_result_source
                llm_responded = manager.discussion_count > before
                if result_source == "fallback" and llm_responded:
                    # The real LLM answered but its reply yielded no usable
                    # assignment: the algorithm's allocation failure, kept as
                    # a measurement. The engine's heuristic fallback result
                    # is discarded and never executed.
                    self.trace.append({
                        "event": "allocation_invalid",
                        "allocation_source": ALLOCATION_REAL_LLM,
                        "round": round_index,
                        "reason": "llm_response_unparseable",
                        "discarded": "heuristic_engine_fallback",
                    })
                    saw_real_llm = True
                    self.allocation_source = ALLOCATION_REAL_LLM
                    break
                self.trace.append({
                    "event": "allocation_aborted",
                    "allocation_source": ALLOCATION_UNAVAILABLE,
                    "round": round_index,
                    "reason": f"llm_result_source:{result_source or 'none'}",
                    "llm_responded": llm_responded,
                    "discarded": "deterministic_engine_fallback",
                })
                if not saw_real_llm:
                    self.allocation_source = ALLOCATION_UNAVAILABLE
                break
            if self._use_llm:
                saw_real_llm = True
                self.allocation_source = ALLOCATION_REAL_LLM
            round_map: dict[str, str] = {}
            for row in result.values():
                colour = str(row.subtask_colour)
                if colour not in subtasks or row.robot_name not in pool_ids:
                    continue
                task_id, index_text = colour.rsplit("#", 1)
                index = int(index_text)
                if assigned[task_id][index] is None:
                    assigned[task_id][index] = str(row.robot_name)
                    round_map[colour] = str(row.robot_name)
            self.trace.append({
                "event": "emos_assignment",
                "manager": "EMOSDiscussionManager",
                "round": round_index,
                "allocation_source": self.allocation_source,
                "candidate_pool": list(pool_ids),
                "assignment": round_map,
                "discussion_count": manager.discussion_count,
            })
            if not round_map:
                break
            remaining = [
                (task_id, index) for task_id, index in remaining
                if assigned[task_id][index] is None
            ]
        self._finish_plan(tasks, assigned, pool_ids)

    def _finish_plan(self, tasks: list[dict[str, Any]],
                     assigned: Mapping[str, list[str | None]],
                     pool_ids: list[str]) -> None:
        gaps = []
        filled: dict[str, list[str]] = {}
        for task in tasks:
            agents = assigned[task["task_id"]]
            if any(agent is None for agent in agents) or len(set(agents)) != len(agents):
                gaps.append(task["task_id"])
                continue
            filled[task["task_id"]] = [str(agent) for agent in agents]
        self.trace.append({
            "event": "allocation_recorded",
            "allocation_source": self.allocation_source,
            "unassigned_task_ids": gaps,
            "assignment": filled,
        })
        if gaps or self.allocation_source == ALLOCATION_UNAVAILABLE:
            self._plan = None
            return
        rewritten = deepcopy(self._graph)
        rewritten["plan_id"] = f"{self._graph['plan_id']}_emos"
        for task in rewritten["tasks"]:
            agents = filled[task["task_id"]]
            task["assigned_agents"] = agents
            arguments = dict(task.get("arguments") or {})
            bindings = arguments.get("carrier_bindings")
            if isinstance(bindings, list):
                if len(bindings) != len(agents):
                    self.trace.append({
                        "event": "allocation_recorded",
                        "allocation_source": self.allocation_source,
                        "unassigned_task_ids": [task["task_id"]],
                        "reason": "binding_count_mismatch",
                    })
                    self._plan = None
                    return
                rebound = []
                for binding, agent in zip(bindings, agents):
                    point_id = binding.get("point_id") if isinstance(binding, Mapping) else None
                    if not isinstance(point_id, str) or not point_id:
                        self._plan = None
                        return
                    rebound.append({"agent_id": agent, "point_id": point_id})
                arguments["carrier_bindings"] = rebound
                task["arguments"] = arguments
        team = list(dict.fromkeys(agent for agents in filled.values() for agent in agents))
        unknown = [agent for agent in team if agent not in pool_ids]
        if unknown:
            self.trace.append({
                "event": "allocation_recorded",
                "allocation_source": self.allocation_source,
                "unknown_agents": unknown,
            })
            self._plan = None
            return
        rank = {name: index for index, name in enumerate(pool_ids)}
        rewritten["assigned_team"] = sorted(team, key=lambda name: rank[name])
        self._plan = rewritten
        self.submitted_plan_id = rewritten["plan_id"]
        self.trace.append({
            "event": "team_selected",
            "allocation_source": self.allocation_source,
            "team": list(rewritten["assigned_team"]),
        })
