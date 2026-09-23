"""Stateful admission ledger for goal-driven v2, with no execution or scoring.

``GoalSession(task_spec, api=None)`` accepts the existing public contracts.
Participant access is limited to ``submit_action(action)``. The hosting runtime
alone calls ``emit(type, time_s, payload)``, ``update_runtime_state(time_s,
robot_regions, object_states)`` and ``complete_skill(task_id, *, time_s, status,
receipt)``. These are trust boundaries, not authentication mechanisms; do not
expose the session object or trusted methods directly to an untrusted agent.
All calls must be serialized by the host on the simulation loop.

Actions use the existing goal_action envelope. Accepted dispatches are pending
requests for the external backend, never successful executions. Cancellation
also stays pending and retains reservations until a backend terminal receipt.
The receipt is an independently supplied mapping with nonempty ``backend_id``,
``receipt_ref``, ``evidence_refs`` (a list), and exact ``task_id``, dispatch
``action_id``, ``status`` and ``completed_at_s``. Missing evidence leaves the
request pending; mismatched/reused receipts raise ValueError. Reference contents
and backend authenticity must be verified by the trusted host, not this ledger.

Plan revisions contain a self-contained DAG of remaining, undispatched work.
Dispatched task IDs cannot be reused, including after failure/cancellation.
Pending work from previous plans remains reserved and separately cancellable;
cross-plan prerequisites are not supported by the unchanged v2 contract.
Selected teams must match submitted plans. Terminal tasks do not infer world
state: the runtime must supply a fresh snapshot before subsequent spatial work.
After time zero a snapshot at the current simulation time is mandatory.

``report()`` returns a defensive diagnostic snapshot for the host (including
backend receipt references and the optional host-owned ``episode_id``). Only
its ``events`` are participant event frames.
It never certifies physical execution or infers business outcomes from skills.
Validation errors are transactional: they do not consume accepted-action IDs,
advance clocks, or alter plans. Accepted action IDs are unique for the session.
"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
import math
from typing import Any, Mapping

from .goal_driven_contract import (
    GOAL_DEPLOYMENT_CONTRACT_VERSION,
    GOAL_EVENT_SCHEMA,
    GOAL_EVENT_VERSION,
    GOAL_PLAN_SCHEMA,
    GOAL_PLAN_VERSION,
    build_goal_participant_api,
    validate_goal_action,
    validate_goal_event,
    validate_goal_participant_api,
    validate_goal_plan,
    validate_goal_task_spec,
)
from .goal_deployment_assessment import (
    DEPLOYMENT_REASSESSMENT_EVENTS,
    validate_assessment_evaluation,
)
from .provenance import canonical_sha256


_NAVIGATION = {"ground_navigation", "aerial_navigation", "stair_traversal"}
_TERMINAL = {"succeeded", "failed", "cancelled"}
_PENDING = {"pending", "cancel_requested"}
_DESTINATION = {
    "pick": "pickup_region_id", "place": "placement_region_id",
    "handoff": "receiving_region_id", "cooperative_carry": "destination_region_id",
    "stair_traversal": "exit_region_id",
}


def _number(value: Any, name: str) -> float:
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value < 0):
        raise ValueError(f"{name} must be a finite nonnegative number")
    return float(value)


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")
    return value


class GoalSession:
    """Single-episode, synchronous plan and action ledger; see module API notes."""

    def __init__(self, task_spec: Mapping[str, Any], api: Mapping[str, Any] | None = None,
                 *, episode_id: str | None = None):
        validate_goal_task_spec(task_spec)
        if episode_id is not None and (not isinstance(episode_id, str) or not episode_id.strip()):
            raise ValueError("episode_id must be a nonempty string when provided")
        self._spec = deepcopy(dict(task_spec))
        self._api = deepcopy(dict(api)) if api is not None else build_goal_participant_api(self._spec)
        validate_goal_participant_api(self._api, task_spec=self._spec)
        self._episode_id = episode_id
        self._robots = {r["id"]: deepcopy(r) for r in self._spec["candidate_robot_pool"]}
        self._pool_ids = set(self._robots)
        self._current = set(self._robots)
        self._capability_versions = {agent: 0 for agent in self._robots}
        self._team: list[str] = []
        self._time = 0.0
        self._version = 0
        self._roster_version = 0
        self._events: list[dict[str, Any]] = []
        self._message_ids: set[str] = set()
        self._consumed: set[str] = set()
        self._actions: dict[str, dict[str, Any]] = {}
        self._action_rejections: list[dict[str, Any]] = []
        self._plans: dict[str, dict[str, Any]] = {}
        self._plan_id: str | None = None
        self._plan_version = 0
        self._tasks: dict[str, dict[str, Any]] = {}
        self._dispatched: set[str] = set()
        self._receipt_ids: set[tuple[str, str]] = set()
        self._snapshot: dict[str, Any] | None = None
        self._snapshot_required = False
        self._business_outcomes = {
            row["id"]: {"id": row["id"], "status": "unassessed",
                        "evidence_refs": [], "evaluator_ref": None}
            for row in self._spec["required_outcomes"]
        }
        self._deployment_enabled = (
            self._spec["version"] == GOAL_DEPLOYMENT_CONTRACT_VERSION)
        self._deployment = {
            "enabled": self._deployment_enabled,
            "status": "not_required",
            "assessment_epoch": None,
            "assessment_id": None,
            "trigger_event_sequence": None,
            "trigger_event_type": None,
            "roster_version": self._roster_version,
            "capability_versions": {},
            "current_agent_ids": [],
            "current_robot_pool": {},
            "resource_versions": {},
            "deployment_change_versions": {},
            "runtime_state": None,
            "runtime_state_sha256": None,
            "public_task_sha256": canonical_sha256(self._spec),
            "completed_outcome_ids": [],
            "external_evaluation": None,
            "oracle_evaluations": [],
            "history": [],
            "terminated": False,
            "termination": None,
        }
        self._resource_versions: dict[str, int] = {}
        self._deployment_change_versions: dict[str, int] = {}
        started = self._append_event(
            "episode_started", {"public_task_digest": canonical_sha256(self._spec)})
        if self._deployment_enabled:
            self._invalidate_deployment_assessment(started)

    def _clock(self, time_s: Any) -> float:
        now = _number(time_s, "time_s")
        if now < self._time:
            raise ValueError("trusted clock cannot move backwards")
        return now

    def _event(self, type: str, now: float, payload: Mapping[str, Any]) -> dict[str, Any]:
        return validate_goal_event({
            "schema": GOAL_EVENT_SCHEMA, "version": GOAL_EVENT_VERSION,
            "sequence": len(self._events), "time_s": now, "type": type,
            "payload": deepcopy(payload),
        }, api=self._api, task_spec=self._spec, current_time_s=now)

    def _append_event(self, type: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        event = self._event(type, self._time, payload)
        self._events.append(event)
        return event

    def _pending(self) -> list[dict[str, Any]]:
        return [r for r in self._tasks.values() if r["status"] in _PENDING]

    def _context(self) -> dict[str, Any]:
        context = {
            "current_time_s": self._time, "current_agents": sorted(self._current),
            "allow_joined_agents": True,
            "joined_agents": [r for a, r in self._robots.items() if a not in self._pool_ids],
            "capability_updates": [r for a, r in self._robots.items() if a in self._pool_ids],
            "visible_events": self._events, "known_plan_ids": list(self._plans),
            "known_task_ids": [t["task_id"] for t in self._plans[self._plan_id]["plan"]["tasks"]]
                if self._plan_id else [],
            "completed_task_ids": [tid for tid, r in self._tasks.items() if r["status"] == "succeeded"],
            "active_task_ids": [r["task"]["task_id"] for r in self._pending()],
            "consumed_message_ids": sorted(self._consumed),
        }
        if self._deployment_enabled:
            context["deployment_assessment"] = deepcopy(self._deployment)
        if self._snapshot is not None:
            context.update({k: self._snapshot[k] for k in ("robot_regions", "object_states")})
        return context

    def _assessment_snapshot(self) -> dict[str, Any]:
        agents = sorted(self._current)
        runtime_state = deepcopy(self._snapshot)
        return {
            "roster_version": self._roster_version,
            "capability_versions": {
                agent: self._capability_versions[agent] for agent in agents},
            "current_agent_ids": agents,
            "current_robot_pool": {
                agent: deepcopy(self._robots[agent]) for agent in agents},
            "resource_versions": deepcopy(self._resource_versions),
            "deployment_change_versions": deepcopy(
                self._deployment_change_versions),
            "runtime_state": runtime_state,
            "runtime_state_sha256": (
                canonical_sha256(runtime_state) if runtime_state is not None else None),
            "public_task_sha256": canonical_sha256(self._spec),
            "completed_outcome_ids": sorted(
                outcome_id for outcome_id, row in self._business_outcomes.items()
                if row["status"] == "validated"),
        }

    def _invalidate_deployment_assessment(self, trigger: Mapping[str, Any]) -> None:
        if not self._deployment_enabled or self._deployment["terminated"]:
            return
        previous = self._deployment["assessment_epoch"]
        epoch = 0 if previous is None else previous + 1
        snapshot = self._assessment_snapshot()
        assessment_id = f"deployment-assessment-{epoch}"
        self._deployment.update({
            "status": "required",
            "assessment_epoch": epoch,
            "assessment_id": assessment_id,
            "trigger_event_sequence": trigger["sequence"],
            "trigger_event_type": trigger["type"],
            "triggered_at_s": trigger["time_s"],
            "external_evaluation": None,
            **snapshot,
        })
        for row in self._pending():
            if trigger["sequence"] not in row["invalidated_by"]:
                row["invalidated_by"].append(trigger["sequence"])
            if row["status"] == "pending":
                row["status"] = "cancel_requested"
                row["safety_stop"] = {
                    "requested": True,
                    "trigger_event_sequence": trigger["sequence"],
                    "acknowledged": False,
                }
        self._append_event("deployment_assessment_required", {
            "assessment_id": assessment_id,
            "assessment_epoch": epoch,
            "trigger_event_sequence": trigger["sequence"],
            "trigger_event_type": trigger["type"],
            "roster_version": snapshot["roster_version"],
            "capability_versions": snapshot["capability_versions"],
            "current_agent_ids": snapshot["current_agent_ids"],
        })

    def _require_deployable(self, action_type: str) -> None:
        if not self._deployment_enabled:
            return
        if self._deployment["terminated"]:
            raise ValueError("deployment assessment terminated the episode")
        if self._deployment["status"] != "deployable":
            raise ValueError(
                f"deployment_assessment is required before {action_type}")

    def _require_snapshot(self, agents: list[str]) -> None:
        if self._time == 0 and not self._snapshot_required and self._snapshot is None:
            return  # The v2 contract permits initial location inference at time zero only.
        if (self._snapshot is None or self._snapshot_required
                or self._snapshot["time_s"] != self._time
                or not set(agents) <= set(self._snapshot["robot_regions"])):
            raise ValueError("a fresh trusted runtime snapshot with selected-robot positions is required")

    def emit(self, type: str, time_s: float, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Deliver a current event and update roster/capability versions atomically.

        Initial public declarations have capability version zero. Joins and
        updates must strictly increase the last version; a rejoin preserves
        identity/mobility. An action_result event alone cannot complete a skill.
        """
        now = self._clock(time_s)
        event = self._event(type, now, payload)
        data = event["payload"]
        if type == "episode_started":
            raise ValueError("episode_started is emitted once by the constructor")
        if type == "observation" and data["message_id"] in self._message_ids:
            raise ValueError("observation message id has already been delivered")
        if type == "action_result" and data["action_id"] not in self._actions:
            raise ValueError("action_result must reference an accepted action")
        if type == "resource_status":
            if data["resource_version"] <= self._resource_versions.get(data["resource_id"], 0):
                raise ValueError("resource_version must strictly increase")
        if type == "deployment_change":
            if data["change_version"] <= self._deployment_change_versions.get(data["change_id"], 0):
                raise ValueError("deployment change_version must strictly increase")
        if type in {"member_join", "member_exit", "capability_change"}:
            agent = data["agent_id"]
            if type == "member_join":
                if agent in self._current:
                    raise ValueError("member_join cannot repeat an active member")
            elif agent not in self._current:
                raise ValueError("lifecycle event requires a current member")
            if type != "member_exit":
                declaration = data["capability_declaration"]
                if data["capability_version"] <= self._capability_versions.get(agent, 0):
                    raise ValueError("capability_version must strictly increase")
                if agent in self._robots and any(
                        declaration[key] != self._robots[agent][key] for key in ("type", "mobility")):
                    raise ValueError("lifecycle declaration cannot change robot identity or mobility")

        self._time = now
        if type in {"member_join", "member_exit", "capability_change"}:
            agent = data["agent_id"]
            if type == "member_exit":
                self._current.remove(agent)
                self._team = [a for a in self._team if a != agent]
            else:
                self._robots[agent] = deepcopy(data["capability_declaration"])
                self._capability_versions[agent] = data["capability_version"]
                self._current.add(agent)
            self._roster_version += 1
            self._snapshot_required = True
            for row in self._pending():
                if agent in row["task"]["assigned_agents"]:
                    row["invalidated_by"].append(event["sequence"])
        elif type == "resource_status":
            self._resource_versions[data["resource_id"]] = data["resource_version"]
        elif type == "deployment_change":
            self._deployment_change_versions[data["change_id"]] = data["change_version"]
        if type == "observation":
            self._message_ids.add(data["message_id"])
        self._events.append(event)
        if type in DEPLOYMENT_REASSESSMENT_EVENTS:
            self._invalidate_deployment_assessment(event)
        self._version += 1
        return deepcopy(event)

    def update_runtime_state(self, time_s: float, robot_regions: Mapping[str, str],
                             object_states: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
        """Replace the trusted symbolic snapshot, without claiming task completion.

        Objects must be complete. Robot positions may cover a subset, but every
        selected/dispatching member needs a current position when admitted.
        Physically unsafe states (e.g. an exited carrier still holding a load)
        may be reported; pure plan validation will reject using them as safe.
        """
        snapshot = self.validate_runtime_state(time_s, robot_regions, object_states)
        self._snapshot = snapshot
        self._time = snapshot["time_s"]
        self._snapshot_required = False
        self._version += 1
        return deepcopy(snapshot)

    def validate_runtime_state(self, time_s: float, robot_regions: Mapping[str, str],
                               object_states: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
        """Preflight a trusted snapshot without changing the episode ledger."""
        now = self._clock(time_s)
        if not isinstance(robot_regions, Mapping) or not isinstance(object_states, Mapping):
            raise ValueError("runtime positions and object states must be mappings")
        robots, objects = deepcopy(dict(robot_regions)), deepcopy(dict(object_states))
        regions = {r["id"]: r for r in self._spec["public_world"]["regions"]}
        expected = {o["id"]: o for o in self._spec["public_world"]["objects"]}
        if set(objects) != set(expected):
            raise ValueError("runtime object_states must be a complete current snapshot")
        for agent, region in robots.items():
            _text(agent, "robot id")
            _text(region, "robot region")
            if agent not in self._robots or region not in regions:
                raise ValueError("runtime robot_regions references an unknown robot or region")
            modes = set(regions[region]["access_modes"])
            if (self._robots[agent]["mobility"] == "aerial" and "aerial_navigation" not in modes
                    or self._robots[agent]["mobility"] == "ground"
                    and not modes.intersection({"ground_navigation", "stair_traversal"})):
                raise ValueError("runtime robot region does not allow this mobility")
        carriers: set[str] = set()
        for oid, state in objects.items():
            if not isinstance(state, Mapping):
                raise ValueError("runtime object state must be a mapping")
            holding = "carrier_bindings" in state or "handling_mode_id" in state
            if set(state) != ({"region_id", "handling_mode_id", "carrier_bindings"} if holding else {"region_id"}):
                raise ValueError("runtime object state has unexpected or missing fields")
            if _text(state["region_id"], "object region") not in regions:
                raise ValueError("runtime object state references an unknown region")
            if holding:
                props = expected[oid]["physical_properties"]
                mode = next((m for m in props["handling_modes"] if m["id"] == state["handling_mode_id"]), None)
                bindings = state["carrier_bindings"]
                if mode is None or not isinstance(bindings, list) or not bindings:
                    raise ValueError("runtime attachment requires a known mode and carrier bindings")
                points = set()
                for binding in bindings:
                    if not isinstance(binding, Mapping) or set(binding) != {"agent_id", "point_id"}:
                        raise ValueError("runtime carrier binding requires agent_id and point_id")
                    agent = _text(binding["agent_id"], "carrier agent")
                    point = _text(binding["point_id"], "carrier point")
                    if agent not in self._robots or agent in carriers or point in points:
                        raise ValueError("runtime attachment has unknown or repeated carriers/points")
                    if agent in robots and robots[agent] != state["region_id"]:
                        raise ValueError("runtime carrier and held object positions disagree")
                    carriers.add(agent)
                    points.add(point)
                if points != {slot["point_id"] for slot in mode["slots"]}:
                    raise ValueError("runtime attachment points must match the handling mode")
        return {"time_s": now, "robot_regions": robots, "object_states": objects}

    def assert_compatible(self, task_spec: Mapping[str, Any], api: Mapping[str, Any]) -> None:
        """Check that a trusted host is attaching to this exact episode ledger."""
        validate_goal_task_spec(task_spec)
        validate_goal_participant_api(api, task_spec=task_spec)
        if (canonical_sha256(self._spec) != canonical_sha256(task_spec)
                or canonical_sha256(self._api) != canonical_sha256(api)):
            raise ValueError("GoalSession is bound to a different task or participant API")

    def _resources(self, task: Mapping[str, Any]) -> dict[tuple[str, str], int]:
        args, skill = task["arguments"], task["skill"]
        resources = {("agent", a): 1 for a in task["assigned_agents"]}
        world = self._spec["public_world"]
        routes = {r["id"]: r for r in world["routes"]}
        for route_id in args.get("route_ids", []):
            route = routes[route_id]
            resources[("route", route["resource_id"])] = route["capacity"]
        regions = set()
        destination = args.get(_DESTINATION.get(skill, "region_id"))
        if destination is not None and skill not in _NAVIGATION:
            regions.add(destination)
        if "object_id" in args:
            oid = args["object_id"]
            resources[("object", oid)] = 1
            if skill in {"pick", "cooperative_carry"}:
                if self._snapshot is not None:
                    regions.add(self._snapshot["object_states"][oid]["region_id"])
                else:
                    obj = next(o for o in world["objects"] if o["id"] == oid)
                    regions.add(obj["physical_properties"]["initial_region_id"])
        if skill in _NAVIGATION and self._snapshot is not None:
            for oid, state in self._snapshot["object_states"].items():
                if any(b["agent_id"] in task["assigned_agents"] for b in state.get("carrier_bindings", [])):
                    resources[("object", oid)] = 1
        capacities = {r["id"]: r["resource_capacity"] for r in world["regions"]}
        for region in regions:
            resources[("region", region)] = capacities[region]
        return resources

    def _admit_dispatch(self, action: Mapping[str, Any]) -> dict[tuple[str, str], int]:
        payload = action["payload"]
        tid = payload["task_id"]
        row = self._tasks[tid]
        task = row["task"]
        if tid in self._dispatched or row["status"] != "scheduled":
            raise ValueError("task has already been dispatched or superseded")
        if row["plan_id"] != self._plan_id:
            raise ValueError("dispatch requires the current plan version")
        if not set(task["assigned_agents"]) <= set(self._team):
            raise ValueError("task agents are outside the currently selected team")
        if any(self._tasks[d]["status"] != "succeeded" for d in task["prerequisites"]):
            raise ValueError("dispatch prerequisite has not completed successfully")
        start = task["execution"]["start_s"]
        end = start + task["execution"]["duration_s"]
        if not start <= self._time < end:
            raise ValueError("dispatch is outside its planned half-open time window")
        self._require_snapshot(task["assigned_agents"])
        refs = set(task["evidence_refs"]) | set(payload["evidence_refs"])
        if not refs <= self._consumed:
            raise ValueError("dispatch evidence must first be consumed by the participant")

        # Validate only this ready call against CURRENT state. Predicted effects
        # from an admitted plan must never become trusted positions/ownership.
        projected = deepcopy(task)
        projected["prerequisites"] = []
        projected["evidence_refs"] = sorted(refs)
        projected["execution"] = {"start_s": self._time, "duration_s": end - self._time}
        validate_goal_plan({
            "schema": GOAL_PLAN_SCHEMA, "version": GOAL_PLAN_VERSION,
            "plan_id": self._plan_id, "assigned_team": task["assigned_agents"], "tasks": [projected],
        }, task_spec=self._spec, context=self._context(),
            allow_physical_infeasibility=True)
        resources = self._resources(task)
        occupied = Counter(key for pending in self._pending() for key in pending["reservations"])
        for key, capacity in resources.items():
            if occupied[key] >= capacity:
                raise ValueError(f"pending runtime resource conflict: {key[0]} {key[1]}")
        return resources

    def submit_action(self, action: Mapping[str, Any]) -> dict[str, Any]:
        """Validate and commit one version-declared action; failures raise ValueError."""
        if self._deployment_enabled and self._deployment["terminated"]:
            raise ValueError("deployment assessment terminated the episode")
        accepted = validate_goal_action(
            action, api=self._api, task_spec=self._spec, context=self._context(),
            allow_physical_infeasibility=True)
        action_id, type, payload = accepted["action_id"], accepted["type"], accepted["payload"]
        if action_id in self._actions:
            raise ValueError("action_id has already been accepted")
        if type in {"select_team", "submit_plan", "revise_plan", "dispatch_skill"}:
            self._require_deployable(type)
        if type not in {"cancel_skill", "consume_message"} and self._time >= self._spec["budgets"]["simulation_s"]:
            raise ValueError("episode simulation budget has ended")
        if type == "select_team":
            busy = {a for r in self._pending() for a in r["task"]["assigned_agents"]} & self._current
            if not busy <= set(payload["agent_ids"]):
                raise ValueError("cannot deselect a pending runtime member before backend termination")
        elif type in {"submit_plan", "revise_plan"}:
            plan = payload["plan"]
            if not self._team or set(plan["assigned_team"]) != set(self._team):
                raise ValueError("select_team must match the plan assigned_team")
            self._require_snapshot(plan["assigned_team"])
            if plan["plan_id"] in self._plans:
                raise ValueError("plan_id must be new for each submission/revision")
            if type == "submit_plan" and self._plan_id is not None:
                raise ValueError("an existing plan must be changed through revise_plan")
            if type == "revise_plan" and payload["replaced_plan_id"] != self._plan_id:
                raise ValueError("revision must replace the current plan version")
            if any(t["task_id"] in self._dispatched for t in plan["tasks"]):
                raise ValueError("remaining-work plans cannot reuse a dispatched task_id")
        elif type == "dispatch_skill":
            reservations = self._admit_dispatch(accepted)
        elif type == "cancel_skill" and self._tasks[payload["task_id"]]["status"] == "cancel_requested":
            raise ValueError("cancellation has already been requested")
        elif type == "deployment_assessment":
            current = self._deployment
            if current["status"] != "required":
                raise ValueError("deployment assessment is not currently required")
            expected = {
                "assessment_id": current["assessment_id"],
                "assessment_epoch": current["assessment_epoch"],
                "roster_version": current["roster_version"],
                "capability_versions": current["capability_versions"],
            }
            if any(payload[name] != value for name, value in expected.items()):
                raise ValueError("deployment assessment is bound to a stale roster or epoch")

        self._actions[action_id] = accepted
        if type == "select_team":
            self._team = list(payload["agent_ids"])
        elif type in {"submit_plan", "revise_plan"}:
            for row in self._tasks.values():
                if row["status"] == "scheduled":
                    row["status"] = "superseded"
            self._plan_version += 1
            self._plan_id = plan["plan_id"]
            self._plans[self._plan_id] = {
                "plan": deepcopy(plan), "version": self._plan_version, "action_id": action_id,
                "submitted_at_s": self._time, "roster_version": self._roster_version,
                "visible_event_sequence": len(self._events) - 1,
            }
            for task in plan["tasks"]:
                self._tasks[task["task_id"]] = {
                    "task": deepcopy(task), "plan_id": self._plan_id, "plan_version": self._plan_version,
                    "status": "scheduled", "dispatch_action_id": None, "cancel_action_id": None,
                    "invalidated_by": [], "reservations": [], "receipt": None,
                }
        elif type == "dispatch_skill":
            tid = payload["task_id"]
            self._dispatched.add(tid)
            self._tasks[tid].update({
                "status": "pending", "dispatch_action_id": action_id, "dispatched_at_s": self._time,
                "reservations": sorted(reservations),
                "capability_versions": {a: self._capability_versions[a]
                                        for a in self._tasks[tid]["task"]["assigned_agents"]},
            })
        elif type == "cancel_skill":
            self._tasks[payload["task_id"]].update({"status": "cancel_requested", "cancel_action_id": action_id})
        elif type == "consume_message":
            self._consumed.add(payload["message_id"])
        elif type == "deployment_assessment":
            snapshot = {
                name: deepcopy(self._deployment[name]) for name in (
                    "assessment_id", "assessment_epoch", "trigger_event_sequence",
                    "trigger_event_type", "triggered_at_s", "roster_version",
                    "capability_versions",
                    "current_agent_ids", "current_robot_pool", "public_task_sha256",
                    "resource_versions", "deployment_change_versions")
            }
            declaration = self._assessment_snapshot()
            row = {
                **snapshot,
                "trigger_completed_outcome_ids": deepcopy(
                    self._deployment["completed_outcome_ids"]),
                "trigger_runtime_state": deepcopy(self._deployment["runtime_state"]),
                "trigger_runtime_state_sha256": self._deployment["runtime_state_sha256"],
                "completed_outcome_ids": declaration["completed_outcome_ids"],
                "declared_runtime_state": declaration["runtime_state"],
                "declared_runtime_state_sha256": declaration["runtime_state_sha256"],
                "decision_action_id": action_id,
                "decision": payload["decision"],
                "reason_codes": list(payload["reason_codes"]),
                "evidence_refs": list(payload["evidence_refs"]),
                "declared_at_s": self._time,
                "external_evaluation": deepcopy(
                    self._deployment["external_evaluation"]),
            }
            self._deployment["history"].append(row)
            self._deployment["status"] = payload["decision"]
            if payload["decision"] == "not_deployable":
                self._deployment["terminated"] = True
                self._deployment["termination"] = {
                    "category": "human_assistance_requested",
                    "assessment_id": payload["assessment_id"],
                    "assessment_epoch": payload["assessment_epoch"],
                    "decision_action_id": action_id,
                    "time_s": self._time,
                    "resource_injected": False,
                }
        self._version += 1
        event = self._append_event("action_result", {
            "action_id": action_id, "status": "accepted", "reason": "ledger admission; no execution claim",
        })
        result = {"accepted": True, "action_id": action_id, "type": type,
                  "state_version": self._version, "plan_version": self._plan_version,
                  "event": deepcopy(event), "execution_validated": False}
        if type in {"dispatch_skill", "cancel_skill"}:
            result["pending_task"] = deepcopy(self._tasks[payload["task_id"]])
        if type == "deployment_assessment":
            result["deployment_assessment"] = deepcopy(self._deployment)
            if payload["decision"] == "not_deployable":
                assistance = self._append_event("human_assistance_requested", {
                    "assessment_id": payload["assessment_id"],
                    "assessment_epoch": payload["assessment_epoch"],
                    "decision_action_id": action_id,
                    "reason_codes": list(payload["reason_codes"]),
                    "evidence_refs": list(payload["evidence_refs"]),
                })
                result["termination_event"] = deepcopy(assistance)
        return result

    def record_action_rejection(
        self, action: Mapping[str, Any], *, reason: str,
    ) -> dict[str, Any]:
        """Record a rejected plan admission for raw scoring diagnostics.

        The caller still fails closed after recording. This does not create a
        plan, imply progress, or add a fixed-subtask scoring track.
        """
        if not isinstance(action, Mapping):
            raise ValueError("rejected action must be an object")
        action_type = action.get("type")
        if action_type not in {"submit_plan", "revise_plan"}:
            raise ValueError("only plan admission rejections are recorded")
        action_id = action.get("action_id")
        if not isinstance(action_id, str) or not action_id:
            raise ValueError("rejected plan action_id must be nonempty")
        if any(row["action_id"] == action_id for row in self._action_rejections):
            raise ValueError("rejected plan action_id has already been recorded")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("rejection reason must be nonempty")
        payload = action.get("payload")
        plan = payload.get("plan") if isinstance(payload, Mapping) else None
        row = {
            "action_id": action_id,
            "type": action_type,
            "submitted_at_s": action.get("submitted_at_s"),
            "reason": reason,
            "state_version": self._version,
            "plan_id": plan.get("plan_id") if isinstance(plan, Mapping) else None,
        }
        self._action_rejections.append(row)
        return deepcopy(row)

    def record_deployment_assessment_evaluation(
        self, evaluation: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Attach one trusted hidden label to an accepted declaration epoch."""
        current = self._deployment
        if not self._deployment_enabled or current["status"] != "required":
            raise ValueError("no required deployment assessment epoch is available")
        if current["external_evaluation"] is not None:
            raise ValueError("deployment assessment epoch is already evaluated")
        validated = validate_assessment_evaluation(
            evaluation, assessment_id=current["assessment_id"],
            assessment_epoch=current["assessment_epoch"])
        current["external_evaluation"] = validated
        current["oracle_evaluations"].append(deepcopy(validated))
        self._version += 1
        return {"accepted": True, "assessment_id": current["assessment_id"],
                "assessment_epoch": current["assessment_epoch"],
                "state_version": self._version}

    def complete_skill(self, task_id: str, *, time_s: float, status: str,
                       receipt: Mapping[str, Any] | None) -> dict[str, Any]:
        """Record a trusted backend terminal result; never execute/infer effects."""
        now = self._clock(time_s)
        _text(task_id, "task_id")
        if task_id not in self._tasks or self._tasks[task_id]["status"] not in _PENDING:
            raise ValueError("completion requires a pending dispatched task")
        if status not in _TERMINAL:
            raise ValueError("backend status must be succeeded, failed or cancelled")
        row = self._tasks[task_id]
        if receipt is not None and not isinstance(receipt, Mapping):
            raise ValueError("backend receipt must be a mapping")
        data = dict(receipt or {})
        if any(k in data and data[k] != v for k, v in {
                "task_id": task_id, "action_id": row["dispatch_action_id"], "status": status,
                "completed_at_s": now}.items()):
            raise ValueError("backend receipt does not match pending task/action/status/time")
        required = {"backend_id", "receipt_ref", "task_id", "action_id", "status", "completed_at_s", "evidence_refs"}
        if self._episode_id is not None:
            required.add("episode_id")
        if not required <= set(data) or not data.get("backend_id") or not data.get("receipt_ref") or not data.get("evidence_refs"):
            return {"accepted": False, "task_id": task_id, "status": row["status"],
                    "reason": "missing backend receipt/evidence; request remains pending"}
        if self._episode_id is not None and data.get("episode_id") != self._episode_id:
            raise ValueError("backend receipt episode_id does not match GoalSession")
        if set(data) != required:
            raise ValueError("backend receipt has unexpected fields")
        key = (_text(data["backend_id"], "backend_id"), _text(data["receipt_ref"], "receipt_ref"))
        _number(data["completed_at_s"], "completed_at_s")
        evidence = data["evidence_refs"]
        if not isinstance(evidence, list) or any(not isinstance(e, str) or not e.strip() for e in evidence):
            raise ValueError("backend evidence_refs must be nonempty reference strings")
        if len(evidence) != len(set(evidence)):
            raise ValueError("backend evidence references must be distinct")
        if key in self._receipt_ids:
            raise ValueError("backend receipt reference has already been used")
        if status == "succeeded":
            if row["invalidated_by"] or row["status"] == "cancel_requested":
                raise ValueError("invalidated/cancel-requested task cannot silently succeed")
            task = row["task"]
            end = task["execution"]["start_s"] + task["execution"]["duration_s"]
            # Duration is the admitted budget/reservation, not a minimum
            # backend latency. A real arrival may precede the nominal duration.
            if not row["dispatched_at_s"] <= now <= end:
                raise ValueError("successful backend completion is outside the execution time window")
        self._time = now
        self._receipt_ids.add(key)
        row.update({"status": status, "receipt": deepcopy(data), "completed_at_s": now, "reservations": []})
        if "safety_stop" in row:
            row["safety_stop"]["acknowledged"] = status == "cancelled"
        self._snapshot_required = True
        self._version += 1
        event = self._append_event("action_result", {
            "action_id": row["dispatch_action_id"], "status": status,
            "reason": "trusted backend terminal receipt recorded; business outcomes unassessed",
        })
        return {"accepted": True, "task_id": task_id, "status": status,
                "state_version": self._version, "event": deepcopy(event),
                "business_outcomes_validated": self._business_outcomes_validated(),
                "business_task_success": self._business_task_success()}

    def record_business_outcome(
        self,
        outcome_id: str,
        *,
        status: str,
        evidence_refs: list[str],
        evaluator_ref: str,
    ) -> dict[str, Any]:
        """Record an explicit domain-evaluator result in the same episode ledger.

        Skill receipts only describe the trusted skill backend.  They never
        mutate this business layer.  A domain evaluator must therefore submit
        an outcome id, its own evidence references and an evaluator reference
        before the business result can change from ``unassessed``.  This
        method is trusted-host only and intentionally emits no participant
        event, so private business evidence cannot leak to the algorithm.
        """
        outcome_id = _text(outcome_id, "outcome_id")
        if outcome_id not in self._business_outcomes:
            raise ValueError("unknown business outcome id")
        if status not in {"pending", "validated", "failed"}:
            raise ValueError("business outcome status must be pending, validated or failed")
        if (not isinstance(evidence_refs, list) or not evidence_refs
                or any(not isinstance(ref, str) or not ref.strip() for ref in evidence_refs)):
            raise ValueError("business outcome evidence_refs must be a nonempty string list")
        if len(evidence_refs) != len(set(evidence_refs)):
            raise ValueError("business outcome evidence_refs must be distinct")
        evaluator_ref = _text(evaluator_ref, "evaluator_ref")
        row = self._business_outcomes[outcome_id]
        if (row["status"], row["evidence_refs"], row["evaluator_ref"]) == (
                status, evidence_refs, evaluator_ref):
            return {"accepted": True, "outcome_id": outcome_id, "status": status,
                    "state_version": self._version,
                    "business_outcomes_validated": self._business_outcomes_validated(),
                    "business_task_success": self._business_task_success(),
                    "idempotent": True}
        if row["status"] in {"validated", "failed"}:
            raise ValueError("terminal business outcome cannot be rewritten")
        row.update({"status": status, "evidence_refs": list(evidence_refs),
                    "evaluator_ref": evaluator_ref})
        self._version += 1
        return {"accepted": True, "outcome_id": outcome_id, "status": status,
                "state_version": self._version,
                "business_outcomes_validated": self._business_outcomes_validated(),
                "business_task_success": self._business_task_success()}

    def _business_outcomes_validated(self) -> bool:
        return bool(self._business_outcomes) and all(
            row["status"] == "validated" for row in self._business_outcomes.values())

    def _business_task_success(self) -> bool | None:
        statuses = {row["status"] for row in self._business_outcomes.values()}
        if "failed" in statuses:
            return False
        if statuses and statuses == {"validated"}:
            return True
        return None

    def report(self) -> dict[str, Any]:
        """Return a host diagnostic copy, not a public release or evaluator result."""
        skill_statuses = {task_id: row["status"] for task_id, row in self._tasks.items()}
        skill_succeeded = sorted(task_id for task_id, status in skill_statuses.items()
                                 if status == "succeeded")
        skill_terminal = sorted(task_id for task_id, status in skill_statuses.items()
                                if status in _TERMINAL)
        business_outcomes_validated = self._business_outcomes_validated()
        business_task_success = self._business_task_success()
        report = {
            "task_id": self._spec["task_id"], "episode_id": self._episode_id,
            "current_time_s": self._time,
            "state_version": self._version, "roster_version": self._roster_version,
            "plan_version": self._plan_version, "current_plan_id": self._plan_id,
            "current_agents": sorted(self._current), "selected_team": self._team,
            "capability_versions": self._capability_versions,
            "current_robot_declarations": self._robots, "events": self._events,
            "consumed_message_ids": sorted(self._consumed), "actions": list(self._actions.values()),
            "action_rejections": list(self._action_rejections),
            "plans": list(self._plans.values()), "tasks": self._tasks,
            "runtime_state": self._snapshot, "runtime_snapshot_refresh_required": self._snapshot_required,
            # Explicitly separate trusted backend completion from domain
            # evaluation.  A succeeded skill is not a successful business
            # outcome, and a business evaluator never rewrites skill status.
            "skill_execution": {
                "task_statuses": skill_statuses,
                "terminal_task_ids": skill_terminal,
                "succeeded_task_ids": skill_succeeded,
                "validated": False,
            },
            "business_outcomes": list(self._business_outcomes.values()),
            "business_task_success": business_task_success,
            "execution_validated": False,
            "business_outcomes_validated": (
                business_outcomes_validated and not self._deployment_enabled),
            "scoreable": False,
            "limitations": ["trusted_host_must_authenticate_backend_and_resolve_evidence",
                            "symbolic_resources_only_no_contact_or_idle_occupancy_model",
                            "no_executor_no_business_evaluator", "remaining_work_revisions_only"],
        }
        if self._deployment_enabled:
            report.update({
                "resource_versions": self._resource_versions,
                "deployment_change_versions": self._deployment_change_versions,
                "deployment_assessment": self._deployment,
            })
        return deepcopy(report)


__all__ = ["GoalSession"]
