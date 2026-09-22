"""Deterministic reference skill host; never a PhysX or business-result backend.

The host owns complete committed robot/object state and current occupancy
facts. GoalSession owns plan admission, dependencies, resource windows, current
team declarations and cancellation. Effects are independently applied below,
not copied from an author witness or predicted constraint-validator result.

Navigation is an atomic reference transition, and fixed-point manipulation has
no stochastic failure. Collision facts come from a trusted occupancy producer;
this module does not derive collision-free geometry from region names. Unknown
or stale occupancy holds execution. Observations, business handoff, relay and
city services require separate backends and are never manufactured here.
"""

from __future__ import annotations

from copy import deepcopy
import math

from .goal_constraints import assess_transport_binding
from .goal_driven_contract import GOAL_PLAN_VERSION, build_goal_participant_api, validate_goal_plan
from .goal_session import GoalSession
from .provenance import canonical_sha256

SUPPORTED_SKILLS = frozenset({"ground_navigation", "aerial_navigation", "stair_traversal",
                              "pick", "place", "cooperative_carry", "synchronize"})
_DESTINATION = {"pick": "pickup_region_id", "place": "placement_region_id",
                "cooperative_carry": "destination_region_id", "stair_traversal": "exit_region_id"}


def _time(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError("time must be finite and nonnegative")
    return float(value)


class ReliableSkillReferenceHost:
    """Single-threaded host with explicit reference state and occupancy authority.

    Only submit_action is a participant operation. All other methods belong to
    the author/runtime host. Every current agent, including idle members, needs
    a region. Occupancy facts must be refreshed after any state/team change and
    at each execution time. Action capacity is not reinterpreted as body capacity.
    This is a reference integration boundary, not a participant security server.
    """

    def __init__(self, public_task, *, robot_regions, session: GoalSession | None = None):
        self._spec = deepcopy(public_task)
        self._api = build_goal_participant_api(public_task)
        self._shared_session = session is not None
        self._session = session or GoalSession(public_task, self._api)
        if session is not None:
            self._session.assert_compatible(public_task, self._api)
        expected = {row["id"] for row in public_task["candidate_robot_pool"]}
        if not isinstance(robot_regions, dict) or set(robot_regions) != expected:
            raise ValueError("reference backend requires a region for every candidate robot")
        self._robots = deepcopy(robot_regions)
        self._objects = {obj["id"]: {"region_id": obj["physical_properties"]["initial_region_id"]}
                         for obj in public_task["public_world"]["objects"]}
        self._session.update_runtime_state(0., self._robots, self._objects)
        self._occupancy = None
        self._evidence = []
        self._state_version = 0

    def state(self):
        return deepcopy({"robot_regions": self._robots, "object_states": self._objects,
                         "state_version": self._state_version})

    def _require_current_regions(self):
        if not set(self._session.report()["current_agents"]) <= self._robots.keys():
            raise ValueError("every current member requires a fresh region binding")

    def set_occupancy(self, *, time_s, blocked_routes, blocked_regions, evidence_ref):
        """Trusted current collision findings, not participant assertions.

        Maps use route/region ids -> nonempty lists of physical entity ids
        explaining the block (including idle robots/objects/dynamic actors).
        Empty maps explicitly assert no known block for the reference state;
        they do not certify a real scene or continuous collision avoidance.
        """
        now = _time(time_s)
        self._require_current_regions()
        if now != self._session.report()["current_time_s"]:
            raise ValueError("occupancy must describe the current host time")
        if not isinstance(evidence_ref, str) or not evidence_ref.strip():
            raise ValueError("occupancy requires a trusted evidence reference")
        world = self._spec["public_world"]
        for label, value, rows in (("routes", blocked_routes, world["routes"]),
                                   ("regions", blocked_regions, world["regions"])):
            if not isinstance(value, dict) or not set(value) <= {r["id"] for r in rows}:
                raise ValueError(f"occupancy references unknown {label}")
            for blockers in value.values():
                if (not isinstance(blockers, list) or not blockers
                        or any(not isinstance(x, str) or not x for x in blockers)
                        or len(set(blockers)) != len(blockers)):
                    raise ValueError("occupancy blockers need distinct nonempty entity identities")
        self._occupancy = deepcopy({"time_s": now, "state_version": self._state_version,
                                    "blocked_routes": blocked_routes, "blocked_regions": blocked_regions,
                                    "evidence_ref": evidence_ref})

    def advance(self, time_s):
        """Advance trusted reference time, without executing a queued task."""
        now = _time(time_s)
        self._require_current_regions()
        self._session.update_runtime_state(now, self._robots, self._objects)
        self._occupancy = None

    def emit(self, type, time_s, payload):
        """Publish a current trusted event; joining members need set_member_region."""
        result = self._session.emit(type, time_s, payload)
        if type == "member_exit":
            # Historical object ownership is retained for safe recovery checks,
            # but a rejoining identity must never inherit its old position.
            self._robots.pop(payload["agent_id"], None)
        self._occupancy = None
        self._state_version += 1
        return result

    def set_member_region(self, agent_id, region_id, *, time_s):
        """Bind a newly joined member; existing member motion uses skills."""
        report = self._session.report()
        if agent_id not in report["current_agents"] or agent_id in self._robots:
            raise ValueError("region binding is only for a newly joined member")
        candidate = {**self._robots, agent_id: region_id}
        self._session.update_runtime_state(time_s, candidate, self._objects)
        self._robots = candidate
        self._state_version += 1
        self._occupancy = None

    def submit_action(self, action):
        if action.get("type") in {"select_team", "submit_plan", "revise_plan", "dispatch_skill"}:
            self._require_current_regions()
        return self._session.submit_action(action)

    def execute_pending(self, task_id, *, completed_at_s):
        """Execute one admitted reference action or return a held/pending reason.

        Host explicitly chooses execution; this is not a planner. No intermediate
        geometry is simulated. Resource reservations remain until ledger accepts
        the terminal receipt. Failed checks do not mutate committed state.
        """
        report = self._session.report()
        row = report["tasks"].get(task_id)
        if row is None or row["status"] not in {"pending", "cancel_requested"}:
            raise ValueError("reference execution requires a pending dispatch")
        end = _time(completed_at_s)
        now = report["current_time_s"]
        if end < now:
            raise ValueError("reference completion cannot precede current time")
        task = row["task"]
        skill, args = task["skill"], task["arguments"]
        if skill not in SUPPORTED_SKILLS:
            return {"accepted": False, "status": "pending", "reason": "external_skill_backend_required"}
        if row["status"] == "cancel_requested" or row["invalidated_by"]:
            status = "cancelled" if row["status"] == "cancel_requested" else "failed"
            return self._commit(row, end, status, self._robots, self._objects,
                                reason="cancelled_or_member_configuration_invalidated")
        if not set(report["current_agents"]) <= self._robots.keys():
            return {"accepted": False, "status": "pending", "reason": "missing_current_member_region"}
        occupancy = self._occupancy
        if (occupancy is None or occupancy["time_s"] != now
                or occupancy["state_version"] != self._state_version):
            return {"accepted": False, "status": "pending", "reason": "current_occupancy_evidence_required"}
        destination = args.get(_DESTINATION.get(skill, "region_id"))
        operation_regions = {destination} if destination is not None else set()
        operation_regions.update(self._robots[agent] for agent in task["assigned_agents"])
        routes = {route["id"]: route for route in self._spec["public_world"]["routes"]}
        for identifier in args.get("route_ids", []):
            operation_regions.update((routes[identifier]["from_region_id"], routes[identifier]["to_region_id"]))
        if "object_id" in args:
            operation_regions.add(self._objects[args["object_id"]]["region_id"])
        if (set(args.get("route_ids", [])) & occupancy["blocked_routes"].keys()
                or operation_regions & occupancy["blocked_regions"].keys()):
            return {"accepted": False, "status": "held", "reason": "current_occupancy_blocked"}
        minimum = self._spec["execution_contract"]["skill_durations_s"][skill]
        if end - now < minimum or end > task["execution"]["start_s"] + task["execution"]["duration_s"]:
            raise ValueError("reference operation needs its full minimum duration within the admitted window")
        # Recheck against committed state/current declarations. Prior DAG
        # dependencies are already enforced by GoalSession at dispatch. The
        # participant path intentionally permits a physically infeasible
        # transport choice through this protocol boundary; the reference host
        # turns that choice into a deterministic observed skill failure below.
        current_task = deepcopy(task)
        current_task["prerequisites"] = []
        current_task["execution"] = {"start_s": now, "duration_s": end - now}
        dispatch = next(action for action in report["actions"] if action["action_id"] == row["dispatch_action_id"])
        current_task["evidence_refs"] = sorted(set(task["evidence_refs"]) | set(dispatch["payload"]["evidence_refs"]))
        plan = {"schema": "eai.benchmark.goal_plan", "version": GOAL_PLAN_VERSION,
                "plan_id": "reference_current_step", "assigned_team": task["assigned_agents"],
                "tasks": [current_task]}
        pool = {robot["id"] for robot in self._spec["candidate_robot_pool"]}
        context = {"current_time_s": now, "current_agents": report["current_agents"],
                   "robot_regions": self._robots, "object_states": self._objects,
                   "visible_events": report["events"], "allow_joined_agents": True,
                   "joined_agents": [r for a, r in report["current_robot_declarations"].items() if a not in pool],
                   "capability_updates": [r for a, r in report["current_robot_declarations"].items() if a in pool]}
        # Evidence may have expired while dispatch was held. Recheck the full
        # call at actual reference start, including current capability versions.
        validate_goal_plan(
            plan, task_spec=self._spec, context=context,
            allow_physical_infeasibility=True)
        if skill in {"pick", "cooperative_carry"}:
            assigned = task["assigned_agents"]
            declarations = report["current_robot_declarations"]
            assessment = assess_transport_binding(
                self._spec, args["object_id"], args, assigned,
                robot_rows=declarations)
            if not assessment["feasible"]:
                observation = {
                    "skill_succeeded": False,
                    "object_moved": False,
                    "task_advanced": False,
                    "carrier_load_monitor": [
                        {
                            "agent_id": row["agent_id"],
                            "requested_load_kg": row["load_kg"],
                            "effective_payload_kg": assessment["physical_profiles"][
                                row["agent_id"]]["effective_payload_kg"],
                        }
                        for row in assessment["member_loads_kg"]
                    ],
                }
                return self._commit(
                    row, end, "failed", self._robots, self._objects,
                    reason="physical_skill_failure", observation=observation)
        robots, objects = deepcopy(self._robots), deepcopy(self._objects)
        assigned = task["assigned_agents"]
        if skill == "pick":
            objects[args["object_id"]] = {"region_id": args["pickup_region_id"],
                                          "handling_mode_id": args["handling_mode_id"],
                                          "carrier_bindings": deepcopy(args["carrier_bindings"])}
        elif skill in {"place", "cooperative_carry"}:
            objects[args["object_id"]] = {"region_id": destination}
        elif skill in {"ground_navigation", "aerial_navigation", "stair_traversal"}:
            for state in objects.values():
                if any(b["agent_id"] in assigned for b in state.get("carrier_bindings", [])):
                    state["region_id"] = destination
        if destination is not None:
            for agent in assigned:
                robots[agent] = destination
        return self._commit(row, end, "succeeded", robots, objects, reason="conditional_reference_effect")

    def _commit(self, row, time_s, status, robots, objects, *, reason, observation=None):
        evidence = {"schema": "eai.benchmark.reference_skill_evidence", "version": 1,
                    "origin": "deterministic_reference_not_simulator",
                    "task_id": row["task"]["task_id"], "action_id": row["dispatch_action_id"],
                    "public_task_sha256": canonical_sha256(self._spec),
                    "started_at_s": self._session.report()["current_time_s"], "completed_at_s": time_s,
                    "status": status, "reason": reason, "before": self.state(),
                    "after": {"robot_regions": deepcopy(robots), "object_states": deepcopy(objects)},
                    "occupancy": deepcopy(self._occupancy),
                    "physical_execution_validated": False, "business_outcomes_validated": False,
                    "scoreable": False}
        if observation is not None:
            evidence["observation"] = deepcopy(observation)
        ref = "sha256:" + canonical_sha256(evidence)
        receipt = {"backend_id": "reliable_skill_reference_v1", "receipt_ref": ref,
                   "task_id": row["task"]["task_id"], "action_id": row["dispatch_action_id"],
                   "status": status, "completed_at_s": time_s, "evidence_refs": [ref]}
        episode_id = self._session.report().get("episode_id")
        if episode_id is not None:
            receipt["episode_id"] = episode_id
        # Keep the shared episode ledger identity when an external runner
        # injects one. The standalone backend retains its transactional copy.
        if self._shared_session:
            snapshot = deepcopy(self._session.__dict__)
            try:
                result = self._session.complete_skill(
                    row["task"]["task_id"], time_s=time_s, status=status, receipt=receipt)
                if not result["accepted"]:
                    return result
                self._session.update_runtime_state(time_s, robots, objects)
            except BaseException:
                self._session.__dict__ = snapshot
                raise
        else:
            session = deepcopy(self._session)
            result = session.complete_skill(
                row["task"]["task_id"], time_s=time_s, status=status, receipt=receipt)
            if not result["accepted"]:
                return result
            session.update_runtime_state(time_s, robots, objects)
            self._session = session
        self._robots, self._objects = deepcopy(robots), deepcopy(objects)
        self._state_version += 1
        self._occupancy = None
        self._evidence.append({**evidence, "sha256": ref.removeprefix("sha256:")})
        response = {**result, "evidence_ref": ref,
                    "execution_origin": "deterministic_reference_not_simulator"}
        if observation is not None:
            response["observation"] = deepcopy(observation)
        return response

    def report(self):
        return {"state": self.state(), "ledger": self._session.report(),
                "evidence": deepcopy(self._evidence), "supported_skills": sorted(SUPPORTED_SKILLS),
                "physical_execution_validated": False, "business_outcomes_validated": False, "scoreable": False,
                "limitations": ["atomic_reference_motion_no_trajectory", "trusted_external_occupancy_required",
                                "no_observation_or_business_service_synthesis", "no_native_scene_or_actuator_application"]}
