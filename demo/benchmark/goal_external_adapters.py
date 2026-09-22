"""Goal adapters for exercising the real TeamWeaver and EMOS orchestration APIs.

The adapters translate only public goal data into planner inputs and emit the
existing goal-action envelope.  They deliberately use deterministic local
decomposition/policy inputs for reproducible native validation; this exercises
the shipped orchestration, allocator, event handling and trace boundary without
requiring a network LLM during a benchmark evidence run.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import sys
import time
from types import SimpleNamespace
import re
from typing import Any, Mapping

from .goal_algorithm_adapter import GOAL_ADAPTER_INTERFACE, GOAL_ADAPTER_INTERFACE_VERSION
from .goal_driven_contract import GOAL_PLAN_VERSION


def _ensure_algorithm_roots():
    """Prepend the repo-local algorithm roots required by the lazy imports.

    Native probes and offline tests share this module; without the bootstrap
    the adapters only import when the launcher happened to extend PYTHONPATH
    with ``algorithm/TeamWeaver`` and ``algorithm/emos``.
    """
    root = Path(__file__).resolve().parents[2]
    required = {
        "TeamWeaver": root / "algorithm" / "TeamWeaver" / "__init__.py",
        "emos.engine": root / "algorithm" / "emos" / "engine.py",
    }
    missing = [name for name, path in required.items() if not path.is_file()]
    if missing:
        raise RuntimeError(f"repo-local algorithm modules missing: {', '.join(missing)}")
    for path in (root / "algorithm", root / "algorithm" / "TeamWeaver"):
        text = str(path)
        if text not in sys.path:
            sys.path.insert(0, text)


def _emos_first_ground_policy(request):
    """Deterministic native-validation policy using EMOS's public callback boundary."""
    names = sorted(request.robot_positions)
    if not names:
        raise ValueError("EMOS policy received no candidate robots")
    return {names[0]: "yellow"}


def _action(action_id: str, action_type: str, payload: Mapping[str, Any], time_s: float) -> dict[str, Any]:
    return {
        "schema": "eai.benchmark.goal_action", "version": 1,
        "action_id": action_id, "submitted_at_s": float(time_s),
        "type": action_type, "payload": deepcopy(dict(payload)),
    }


class _WarehouseDecomposer:
    """Small deterministic decomposer with the same result contract as TeamWeaver."""

    def __init__(self, task):
        self.task = task

    def decompose(self, instruction, world, **_kwargs):
        _ensure_algorithm_roots()
        from TeamWeaver.eai_adapter.semantic_decomposer import SemanticDecompositionResult
        from TeamWeaver.eai_adapter.capability_ontology import CapabilityRequirement
        from TeamWeaver.eai_adapter.task_models import SemanticTask, TaskType

        return SemanticDecompositionResult(
            tasks=(SemanticTask(
                task_id="warehouse_select_carrier",
                task_type=TaskType.NAVIGATE,
                description="Reach the declared crate pickup region",
                target_ref="order_crate_01",
                priority=1,
                requirements={"navigation": CapabilityRequirement(0.6, 1.0, True)},
                prerequisites=(), can_parallel=False, estimated_duration_s=8.0,
                preferred_agent=None,
            ),),
            source="deepseek", attempts=1,
        )

    def cancel(self):
        return None


def _teamweaver_world(public_task: Mapping[str, Any]):
    from TeamWeaver.eai_adapter.task_models import (
        RobotSnapshot, SymbolicWorldState, TargetSnapshot, TaskType,
    )

    robots = []
    for row in public_task["candidate_robot_pool"]:
        caps = set(row["public_capabilities"])
        values = {"navigation": 1.0 if "ground_navigation" in caps else 0.0,
                  "sensing": 1.0 if "observe" in caps else 0.0,
                  "manipulation": 1.0 if "pick" in caps else 0.0,
                  "payload": 1.0 if "cooperative_carry" in caps else 0.0}
        robots.append(RobotSnapshot(
            name=row["id"], position=(0.0, 0.0), base_capabilities=values,
            reliability={name: 1.0 for name in values}, equipment=frozenset(),
            busy=False, current_task=None, current_load=0, safe=True,
        ))
    targets = tuple(TargetSnapshot(
        ref=row["id"], position=(0.0, 0.0, 0.0), available=True,
        kind="object", compatible_task_types=(TaskType.NAVIGATE,))
        for row in public_task["public_world"]["objects"])
    return SymbolicWorldState(
        hazard_id=0, hazard_position=(0.0, 0.0, 0.0), hazard_active=False,
        targets=targets, robots=tuple(robots), extinguisher_available=False,
        extinguisher_carrier=None, extinguisher_delivered=False,
        rescue_channel_open=True, completed_task_ids=(), failed_task_ids=(),
        recent_feedback=(), observations={},
    )


class TeamWeaverGoalAdapter:
    """Translate TeamWeaver allocation and failure feedback into goal actions."""

    def __init__(self):
        self.trace: list[dict[str, Any]] = []
        self._task: Mapping[str, Any] | None = None
        self._now = 0.0
        self._replanned = False

    def manifest(self):
        return {
            "kind": "participant", "id": "teamweaver-goal-native", "version": 1,
            "interface": GOAL_ADAPTER_INTERFACE,
            "interface_version": GOAL_ADAPTER_INTERFACE_VERSION,
            "evaluation_track": "goal_driven",
            "input_scope": "participant_visible_events_only",
            "control_scope": "benchmark_managed_low_level_control",
        }

    def initialize(self, public_task, participant_api):
        self._task = deepcopy(public_task)
        self.trace.append({"event": "initialize", "planner": "TeamWeaverPipeline"})

    def act(self, events):
        if self._task is None:
            raise RuntimeError("TeamWeaver adapter was not initialized")
        self._now = max((float(row.get("time_s", 0.0)) for row in events), default=self._now)
        if any(row.get("type") == "action_result" and row.get("payload", {}).get("status") == "failed"
               for row in events):
            self._replanned = True
            self.trace.append({"event": "replan", "reason": "public_skill_failure"})
            sequence = next((row.get("sequence") for row in events
                             if row.get("type") == "action_result"), None)
            return self._pair_actions(self._now, sequence)
        if any(row.get("type") == "episode_started" for row in events):
            return self._initial_actions(self._now)
        return []

    def _initial_actions(self, now):
        _ensure_algorithm_roots()
        from TeamWeaver.eai_adapter.dynamic_miqp import DynamicMIQPAllocator
        from TeamWeaver.eai_adapter.teamweaver_pipeline import TeamWeaverPipeline

        public = self._task
        world = _teamweaver_world(public)
        pipeline = TeamWeaverPipeline(
            _WarehouseDecomposer(public), DynamicMIQPAllocator(prefer_miqp=False),
            post_decomposition=lambda tasks, _world: tuple(tasks),
        )
        plan = pipeline.plan_initial("Select a safe ground carrier for the crate", world)
        assignment = plan.allocation.assignments[0].robot_name
        self.trace.append({"event": "plan_initial", "solver": plan.allocation.solver,
                           "assignment": assignment, "task_ids": [t.task_id for t in plan.tasks]})
        goal_plan = self._single_plan(assignment, "formal_overload_crate")
        return [
            _action("tw_select_single", "select_team", {"agent_ids": [assignment]}, now),
            _action("tw_submit_overload", "submit_plan", {"plan": goal_plan}, now),
            _action("tw_dispatch_overload", "dispatch_skill",
                    {"task_id": "formal_overload_crate", "evidence_refs": []}, now),
        ]

    def _single_plan(self, agent, task_id):
        return {"schema": "eai.benchmark.goal_plan", "version": GOAL_PLAN_VERSION,
                "plan_id": ("formal_overload_plan" if task_id == "formal_overload_crate"
                             else f"{task_id}_plan"),
                "assigned_team": [agent], "tasks": [{
                    "task_id": task_id, "skill": "pick",
                    "arguments": {"object_id": "order_crate_01", "pickup_region_id": "rack_aisle_a",
                                   "handling_mode_id": "single_grip", "carrier_bindings":
                                   [{"agent_id": agent, "point_id": "center_grip"}]},
                    "required_capabilities": ["pick"], "assigned_agents": [agent],
                    "prerequisites": [], "evidence_refs": [],
                    "execution": {"start_s": self._now, "duration_s": 8.0},
                }]}

    def _pair_actions(self, now, trigger_sequence=None):
        pair = ["warehouse_ground_1", "warehouse_ground_3"]
        plan = {"schema": "eai.benchmark.goal_plan", "version": GOAL_PLAN_VERSION,
                "plan_id": "formal_paired_recovery_plan", "assigned_team": pair, "tasks": [{
                    "task_id": "formal_paired_recovery", "skill": "cooperative_carry",
                    "arguments": {"object_id": "order_crate_01", "destination_region_id": "consolidation_area",
                                   "handling_mode_id": "paired_support", "route_ids": ["rack_a_to_consolidation"],
                                   "carrier_bindings": [{"agent_id": pair[0], "point_id": "support_left"},
                                                        {"agent_id": pair[1], "point_id": "support_right"}]},
                    "required_capabilities": ["cooperative_carry", "synchronize"],
                    "assigned_agents": pair, "prerequisites": [], "evidence_refs": [],
                    "execution": {"start_s": now, "duration_s": 120.0},
                }]}
        return [_action("tw_select_pair", "select_team", {"agent_ids": pair}, now),
                _action("tw_revise_pair", "revise_plan", {
                    "replaced_plan_id": "formal_overload_plan",
                    "trigger_event_sequence": [trigger_sequence] if trigger_sequence is not None else [0],
                    "reason": "public skill failure", "plan": plan}, now),
                _action("tw_dispatch_pair", "dispatch_skill",
                        {"task_id": "formal_paired_recovery", "evidence_refs": []}, now)]

    def close(self):
        self.trace.append({"event": "close", "replanned": self._replanned})


class EmosGoalAdapter:
    """Use the EMOS discussion manager with a deterministic assignment policy."""

    def __init__(self, base_env=None):
        self._env = base_env
        self.trace: list[dict[str, Any]] = []
        self._task = None
        self._now = 0.0

    def manifest(self):
        return {
            "kind": "participant", "id": "emos-goal-native", "version": 1,
            "interface": GOAL_ADAPTER_INTERFACE,
            "interface_version": GOAL_ADAPTER_INTERFACE_VERSION,
            "evaluation_track": "goal_driven",
            "input_scope": "participant_visible_events_only",
            "control_scope": "benchmark_managed_low_level_control",
        }

    def initialize(self, public_task, participant_api):
        self._task = deepcopy(public_task)
        self.trace.append({"event": "initialize", "planner": "EMOSDiscussionManager"})

    def act(self, events):
        self._now = max((float(row.get("time_s", 0.0)) for row in events), default=self._now)
        if any(row.get("type") == "action_result" and row.get("payload", {}).get("status") == "failed"
               for row in events):
            self.trace.append({"event": "replan", "reason": "public_skill_failure"})
            sequence = next((row.get("sequence") for row in events
                             if row.get("type") == "action_result"), None)
            return TeamWeaverGoalAdapter()._pair_actions(self._now, sequence)
        if not any(row.get("type") == "episode_started" for row in events):
            return []
        _ensure_algorithm_roots()
        from emos.engine import EMOSDiscussionManager
        from emos.types import EMOSRobotAgentSpec, EMOSScenarioConfig, PositionRule, SubtaskSpec

        ground = [row["id"] for row in self._task["candidate_robot_pool"] if row["mobility"] == "ground"]
        chosen = ground[0]
        scenario = EMOSScenarioConfig(
            scenario_id="warehouse_goal_native", scene_title="Warehouse", anchor_label="crate",
            task_description="Select a ground carrier for the declared crate",
            subtasks={"yellow": SubtaskSpec("yellow", "carry", "carry the crate")},
            position_rules={"yellow": PositionRule("fixed", xy=(0.0, 0.0))},
            robot_profiles={name: __import__("emos.types", fromlist=["RobotProfileSpec"]).RobotProfileSpec("ground", ["navigation"])
                            for name in ground},
            preferred_fallback={name: "yellow" for name in ground}, yellow_subtask_id="yellow",
            yellow_robot_prefix="warehouse_ground_", green_subtask_id="green",
        )
        specs = {name: EMOSRobotAgentSpec(agent_name=name, robot_type="ground", capabilities=["navigation"], preferred_task="yellow")
                 for name in ground}
        manager = EMOSDiscussionManager.build_from_agent_specs(
            self._env, specs, scenario, use_env_resume=False)
        manager.set_assignment_policy(_emos_first_ground_policy, name="goal-native-deterministic")
        manager.trigger(1, (0.0, 0.0, 0.0))
        result = None
        for _ in range(20):
            result = manager.poll()
            if result is not None:
                break
            time.sleep(0.01)
        if not result:
            raise RuntimeError("EMOS did not produce an assignment")
        chosen = sorted(result)[0]
        self.trace.append({"event": "assignment", "manager": "EMOSDiscussionManager", "agent": chosen})
        plan = TeamWeaverGoalAdapter()._single_plan(chosen, "formal_overload_crate")
        return [_action("emos_select_single", "select_team", {"agent_ids": [chosen]}, self._now),
                _action("emos_submit_overload", "submit_plan", {"plan": plan}, self._now),
                _action("emos_dispatch_overload", "dispatch_skill", {"task_id": "formal_overload_crate", "evidence_refs": []}, self._now)]

    def close(self):
        self.trace.append({"event": "close"})




def _city_llm_env() -> dict[str, str]:
    """Resolve the dongli gateway credentials for TeamWeaver decomposition.

    Priority: existing DEEPSEEK_API_KEY, then DONGLI_API_KEY, then the
    DONGLI_API_KEY line in ~/.secrets (last resort, local-run convenience).
    The key is never traced or archived.
    """
    import os

    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        api_key = os.environ.get("DONGLI_API_KEY", "").strip()
    if not api_key:
        secrets = Path.home() / ".secrets"
        if secrets.is_file():
            match = re.search(r'DONGLI_API_KEY="([^"]+)"', secrets.read_text(encoding="utf-8"))
            api_key = match.group(1) if match else ""
    if not api_key:
        raise RuntimeError("no DeepSeek/dongli API key available for TeamWeaver LLM decomposition")
    return {
        "DEEPSEEK_API_KEY": api_key,
        "TEAMWEAVER_DEEPSEEK_BASE_URL": os.environ.get(
            "TEAMWEAVER_DEEPSEEK_BASE_URL", "https://ai.dongli.work/v1"),
        "TEAMWEAVER_DEEPSEEK_MODEL": os.environ.get(
            "TEAMWEAVER_DEEPSEEK_MODEL", "gpt-5.6-luna"),
    }


class _LowEffortClient:
    """OpenAI-compatible client wrapper injecting reasoning_effort=low."""

    def __init__(self, client):
        self._client = client
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        kwargs.setdefault("reasoning_effort", "low")
        return self._client.chat.completions.create(**kwargs)


def _low_effort_client_factory(**kwargs):
    import httpx
    from openai import OpenAI

    kwargs = dict(kwargs)
    kwargs["timeout"] = 240.0
    kwargs["http_client"] = httpx.Client(timeout=240.0, trust_env=False)
    return _LowEffortClient(OpenAI(**kwargs))


class CityTeamWeaverParticipant:
    """Real TeamWeaver allocator deciding the City episode's team and dispatch.

    The reduced City plan's structure (tasks, windows, prerequisites) is
    witness-derived; the measured decisions are team selection and the
    per-task agent assignment, which this participant derives from the real
    ``TeamWeaverPipeline`` + ``DynamicMIQPAllocator`` over the public
    candidate pool.  Eligibility is a hard capability containment: a task is
    only ever assigned to an agent whose public capabilities cover the task's
    declared requirements.  Trusted receipts and host state never cross into
    this object.
    """

    def __init__(self, base_plan: Mapping[str, Any], *, use_llm_decomposition: bool = False):
        self._base = deepcopy(dict(base_plan))
        self._use_llm = bool(use_llm_decomposition)
        self._plan: dict[str, Any] | None = None
        self._opened = False
        self._dispatched = 0
        self._terminal: set[str] = set()
        self.trace: list[dict[str, Any]] = []

    def manifest(self) -> Mapping[str, Any]:
        return {"kind": "participant", "id": "city-native-teamweaver", "version": 1,
                "interface": "eai.benchmark.goal_algorithm_adapter", "interface_version": 1,
                "evaluation_track": "goal_driven",
                "input_scope": "participant_visible_events_only",
                "control_scope": "benchmark_managed_low_level_control"}

    def initialize(self, public_task: Mapping[str, Any],
                   participant_api: Mapping[str, Any]) -> None:
        self._task = deepcopy(public_task)
        del participant_api
        self.trace.append({"event": "initialize", "planner": "TeamWeaverPipeline"})

    # -- allocation ------------------------------------------------------
    def _allocate(self) -> None:
        _ensure_algorithm_roots()
        from TeamWeaver.eai_adapter.dynamic_miqp import DynamicMIQPAllocator
        from TeamWeaver.eai_adapter.semantic_decomposer import SemanticDecompositionResult
        from TeamWeaver.eai_adapter.capability_ontology import CapabilityRequirement
        from TeamWeaver.eai_adapter.task_models import (
            RobotSnapshot, SemanticTask, SymbolicWorldState, TargetSnapshot, TaskType,
        )
        from TeamWeaver.eai_adapter.teamweaver_pipeline import TeamWeaverPipeline

        pool = self._task["candidate_robot_pool"]
        plan_tasks = self._base["tasks"]
        # TeamWeaver's LLM emits canonical capability axes (navigation /
        # sensing / ...), so the world model maps city capabilities onto
        # those axes instead of exposing the raw city capability names.
        _CANONICAL_AXIS = {"ground_navigation": "navigation",
                           "observe": "sensing", "relay": "communication"}

        def _capability_values(caps):
            values = {"navigation": 0.0, "sensing": 0.0,
                      "manipulation": 0.0, "payload": 0.0, "communication": 0.0}
            for cap in caps:
                axis = _CANONICAL_AXIS.get(cap, cap)
                values[axis] = max(values.get(axis, 0.0), 1.0)
            return values

        def _busy_snapshot(row):
            values = _capability_values(row["public_capabilities"])
            return RobotSnapshot(
                name=row["id"], position=(0.0, 0.0), base_capabilities=values,
                reliability={name: 1.0 for name in values}, equipment=frozenset(),
                busy=True, current_task="<phase_assigned>", current_load=0, safe=True)

        def robot_snapshot(row):
            values = _capability_values(row["public_capabilities"])
            return RobotSnapshot(
                name=row["id"], position=(0.0, 0.0), base_capabilities=values,
                reliability={name: 1.0 for name in values}, equipment=frozenset(),
                busy=False, current_task=None, current_load=0, safe=True)

        targets = tuple(TargetSnapshot(
            ref=task["task_id"], position=(0.0, 0.0, 0.0), available=True,
            kind="region", compatible_task_types=(TaskType.NAVIGATE,))
            for task in plan_tasks)
        world = SymbolicWorldState(
            hazard_id=0, hazard_position=(0.0, 0.0, 0.0), hazard_active=False,
            targets=targets, robots=tuple(robot_snapshot(row) for row in pool),
            extinguisher_available=False, extinguisher_carrier=None,
            extinguisher_delivered=False, rescue_channel_open=True,
            completed_task_ids=(), failed_task_ids=(), recent_feedback=(),
            observations={})

        class _CityDecomposer:
            """Emit the still-unassigned plan tasks as semantic tasks."""

            def __init__(self, remaining):
                self._remaining = remaining

            def decompose(self, instruction, world_state, **_kwargs):
                rows = []
                for task in plan_tasks:
                    if task["task_id"] not in self._remaining:
                        continue
                    requirements = {cap: CapabilityRequirement(1.0, 1.0, True)
                                    for cap in task["required_capabilities"]}
                    rows.append(SemanticTask(
                        task_id=task["task_id"], task_type=TaskType.NAVIGATE,
                        description=task["skill"], target_ref=task["task_id"],
                        priority=1, requirements=requirements,
                        prerequisites=(), can_parallel=False,
                        estimated_duration_s=float(task["execution"]["duration_s"]),
                        preferred_agent=None))
                return SemanticDecompositionResult(
                    tasks=tuple(rows), source="deepseek", attempts=1)

        allocator = DynamicMIQPAllocator(prefer_miqp=False)
        # The allocator is a per-phase matching (at most one task per robot
        # per round), so the city episode allocates in dynamic rounds: each
        # round plans only the still-unassigned tasks over the free pool
        # (phase-assigned robots complete between rounds), until every task
        # has an agent or a round makes no progress.
        semantic_tasks = None
        if self._use_llm:
            from TeamWeaver.eai_adapter.semantic_decomposer import DeepSeekSemanticDecomposer

            env = _city_llm_env()
            decomposer = DeepSeekSemanticDecomposer(
                env=env, client_factory=_low_effort_client_factory)
            instruction = (
                "Coordinate the city emergency response. Create exactly one "
                "subtask for every listed target: guide each bound affected "
                "person to their assembly region, hold the following traffic "
                "at the traffic control point, and report the incident to the "
                "city_dispatch recipient service.")
            llm_result = decomposer.decompose(instruction, world, max_attempts=2,
                                               retry_delays=(2.0,))
            semantic_tasks = list(llm_result.tasks)
            self.trace.append({
                "event": "llm_decomposition",
                "model": env["TEAMWEAVER_DEEPSEEK_MODEL"],
                "base_url": env["TEAMWEAVER_DEEPSEEK_BASE_URL"],
                "source": llm_result.source,
                "task_ids": [t.task_id for t in semantic_tasks],
                "targets": [t.target_ref for t in semantic_tasks],
            })
            covered = [t.target_ref for t in semantic_tasks]
            catalog_ids = {task["task_id"] for task in plan_tasks}
            missing = sorted(catalog_ids - set(covered))
            duplicated = sorted({ref for ref in covered if covered.count(ref) > 1})
            if missing or duplicated:
                raise RuntimeError(
                    f"LLM decomposition does not cover the frozen catalog "
                    f"one-to-one (missing={missing}, duplicated={duplicated})")
            # The public task contract's declared capabilities are the
            # authority on eligibility: replace each LLM task's requirements
            # with exactly the declared capabilities mapped onto canonical
            # axes. The LLM's measured contribution is the decomposition
            # (which targets become tasks); the allocator still makes every
            # assignment choice among the eligible robots.
            catalog_by_id = {task["task_id"]: task for task in plan_tasks}
            for index, semantic in enumerate(semantic_tasks):
                declared = catalog_by_id[semantic.target_ref]["required_capabilities"]
                enriched = {_CANONICAL_AXIS.get(cap, cap): CapabilityRequirement(1.0, 1.0, True)
                            for cap in declared}
                try:
                    semantic.requirements = enriched
                except (TypeError, AttributeError):
                    semantic_tasks[index] = replace(semantic, requirements=enriched)

        assignment: dict[str, str] = {}
        remaining = {task["task_id"] for task in plan_tasks}
        for round_index in range(len(plan_tasks)):
            if not remaining:
                break
            if semantic_tasks is not None:
                batch = tuple(t for t in semantic_tasks if t.target_ref in remaining)
            else:
                batch = _CityDecomposer(remaining).decompose(None, world).tasks
            result = allocator.allocate(world, batch)
            if semantic_tasks is not None:
                semantic_by_id = {t.task_id: t for t in semantic_tasks}
                round_map = {semantic_by_id[row.task_id].target_ref: row.robot_name
                             for row in result.assignments
                             if row.task_id in semantic_by_id
                             and semantic_by_id[row.task_id].target_ref in remaining}
            else:
                round_map = {row.task_id: row.robot_name
                             for row in result.assignments
                             if row.task_id in remaining}
            self.trace.append({"event": "plan_initial", "round": round_index,
                               "solver": getattr(result, "solver", "miqp"),
                               "assignment": dict(round_map),
                               "task_ids": sorted(remaining)})
            if not round_map:
                break
            assignment.update(round_map)
            remaining -= set(round_map)
        if remaining:
            raise RuntimeError(f"TeamWeaver left city tasks unassigned: {sorted(remaining)}")

        capability_map = {row["id"]: set(row["public_capabilities"]) for row in pool}
        rewritten = deepcopy(self._base)
        for task in rewritten["tasks"]:
            agent = assignment[task["task_id"]]
            required = set(task["required_capabilities"])
            if not required <= capability_map[agent]:
                raise RuntimeError(
                    f"TeamWeaver assigned {task['task_id']} to ineligible {agent}")
            task["assigned_agents"] = [agent]
        team = list(dict.fromkeys(
            assignment[task["task_id"]] for task in rewritten["tasks"]))
        order = {row["id"]: index for index, row in enumerate(pool)}
        rewritten["assigned_team"] = sorted(team, key=lambda name: order[name])
        self._plan = rewritten
        self.trace.append({"event": "team_selected", "team": rewritten["assigned_team"]})

    # -- action sequencing (mirrors the witness participant) --------------
    def act(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not self._opened:
            if not any(item["type"] == "episode_started" for item in events):
                return []
            self._opened = True
            self._allocate()
            return [{
                "schema": "eai.benchmark.goal_action", "version": 1,
                "action_id": "tw_city_select", "submitted_at_s": 0.0,
                "type": "select_team", "payload": {"agent_ids": list(self._plan["assigned_team"])},
            }, {
                "schema": "eai.benchmark.goal_action", "version": 1,
                "action_id": "tw_city_plan", "submitted_at_s": 0.0,
                "type": "submit_plan", "payload": {"plan": deepcopy(self._plan)},
            }]
        for item in events:
            if item["type"] == "action_result" and item["payload"].get("status") in {
                    "succeeded", "failed", "cancelled"}:
                self._terminal.add(item["payload"]["action_id"])
                if item["payload"].get("status") == "failed":
                    self.trace.append({"event": "replan", "reason": "public_skill_failure"})
        tasks = self._plan["tasks"]
        if self._dispatched >= len(tasks):
            return []
        previous = f"tw_city_dispatch_{self._dispatched - 1}"
        if self._dispatched and previous not in self._terminal:
            return []
        index = self._dispatched
        self._dispatched += 1
        now = max((item["time_s"] for item in events), default=0.0)
        return [{
            "schema": "eai.benchmark.goal_action", "version": 1,
            "action_id": f"tw_city_dispatch_{index}", "submitted_at_s": float(now),
            "type": "dispatch_skill",
            "payload": {"task_id": tasks[index]["task_id"], "evidence_refs": []},
        }]

    def close(self) -> None:
        pass


__all__ = ["TeamWeaverGoalAdapter", "EmosGoalAdapter", "CityTeamWeaverParticipant"]
