"""Formal City goal episode: participant GoalSession drives the native services.

``run_city_episode`` prepares the author-frozen City service probe, then injects
a ``driver`` into ``demo.city_benchmark.service_probe`` so the already-built
native scene (bound roster, geometry, warmup observation and
``CityNativeServiceRunner``) is driven by a ``FormalGoalEpisodeRunner`` instead
of the host-owned service sequence.  A fixed in-process participant selects the
team, submits a reduced seven-task plan and dispatches each skill after the
previous one reached a terminal state; ``CityNativeGoalBackend`` executes every
dispatch through the concrete native services and the business evaluator maps
``evaluate_city_acceptance`` onto the GoalSession business outcomes.

Deliberate deviations from the internal design note, kept honest and minimal:

* ``observation_refs`` in the reduced plan cite the frozen native observation
  source identity instead of an empty list: the public skill contract requires
  a nonempty unique string list, and the plan must pass ``validate_goal_plan``.
  The runtime observation refs actually delivered by ``runner.observe()`` are
  recorded in the backend evidence, never claimed by the plan.
* Skill receipts complete at the task's admitted execution window end
  (``start_s + duration_s``), not at the raw ``city.elapsed`` ack instant.
  GoalSession dispatch requires ``start_s <= dispatch_time`` and plan
  prerequisites require ``prerequisite.end <= dependent.start``, so for a
  serial single-agent chain the only admissible completion time is exactly the
  reserved window end.  The backend first advances the real simulation to that
  time (people keep walking toward assembly), so the ledger clock and
  ``city.elapsed`` stay one simulation quantum apart at most; a skill whose
  native service completes more than one quantum past its window end fails.
* Every service request renews the native observation when simulation steps
  have intervened.  The warmup snapshot referenced by
  ``ctx["guidance_observation"]`` is reused only while no step has advanced the
  clock, mirroring the host sequence's own retry behaviour; native observations
  expire after one second, so a request after settling steps would otherwise be
  honestly rejected as expired.
* The evaluator reports GoalSession business outcome status ``validated`` (not
  ``"succeeded"``): ``GoalSession.record_business_outcome`` only accepts
  pending/validated/failed.  ``response_dispatch`` from the acceptance review
  is not a declared required outcome of the public task and is therefore kept
  in the archived acceptance evidence instead of the session ledger.
* ``cancel`` returns the exact receipt field set GoalSession requires, so the
  honest reason string ("native city cancel not implemented; ledger-only
  cancel") travels next to the receipt, not inside it.
* The guide skill stays acknowledgement-terminal (``eai.skills.v3``), but the
  business evaluator first advances the native scene until the roster reaches
  assembly (or the public arrival budget expires) and records per-person
  arrival readbacks through the trusted runner, mirroring the host sequence.
  People who never arrive keep ``arrived_at_admitted_assembly=False`` and the
  outcome fails closed.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
import math
from pathlib import Path
from typing import Any, Callable, Mapping

from .goal_driven_contract import GOAL_PLAN_VERSION, validate_goal_plan
from .goal_driven_tasks import (
    _CITY_NATIVE_AFFECTED_PERSON_ASSEMBLY,
    _CITY_NATIVE_FOLLOWING_VEHICLE_IDS,
    build_goal_task_package,
)
from .goal_episode_runner import FormalGoalEpisodeRunner
from .goal_external_adapters import CityEmosParticipant, CityTeamWeaverParticipant
from .provenance import file_sha256
from demo.city_benchmark.task_logic import (
    default_deadline_policy,
    evaluate_city_acceptance,
)


ROOT = Path(__file__).resolve().parents[2]
CITY_EPISODE_PLAN_ID = "city_native_goal_episode_v1"
CITY_RESPONSE_SERVICE_ID = "city_dispatch"
CITY_NATIVE_OBSERVATION_REF = "city_native_runtime_observation"
CITY_BACKEND_ID = "isaac.city.runtime"
CITY_EVALUATOR_REF = "demo.city_benchmark.task_logic.evaluate_city_acceptance"
CITY_GUIDE_RETRY_LIMIT = 20


def _requested_target_profile(service: Any) -> bool:
    from demo.city_benchmark.guide_admission import REQUESTED_TARGET_PROFILES

    return getattr(service, "guidance_profile", None) in REQUESTED_TARGET_PROFILES


def _support_footprint_profile(service: Any) -> bool:
    from demo.city_benchmark.guide_admission import REQUESTED_TARGET_V2_PROFILE

    return getattr(service, "guidance_profile", None) == REQUESTED_TARGET_V2_PROFILE


def _clock_epoch(value: Any) -> float:
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value < 0):
        raise ValueError("clock_epoch_s must be finite and nonnegative")
    return float(value)


def _relative_timestamp(value: Any, clock_epoch_s: float) -> float | None:
    if value is None:
        return None
    return float(value) - clock_epoch_s


def city_robot_regions(public_task: Mapping[str, Any]) -> dict[str, str]:
    """Region snapshot for the whole candidate pool (road_surface admits
    both mobilities), so any team the algorithm selects is coverable."""
    return {row["id"]: "road_surface"
            for row in public_task["candidate_robot_pool"]}


CITY_ROBOT_REGIONS = {"go2_1": "road_surface", "carter_1": "road_surface",
                      "iris_1": "road_surface"}
DEFAULT_CITY_EPISODE_BINDING = ROOT / (
    "tmp/city_benchmark/recovery_binding_v2_20260922/authoring/private.json")


def _action(identifier: str, kind: str, payload: Mapping[str, Any],
            submitted_at_s: float) -> dict[str, Any]:
    return {"schema": "eai.benchmark.goal_action", "version": 1,
            "action_id": identifier, "submitted_at_s": float(submitted_at_s),
            "type": kind, "payload": deepcopy(dict(payload))}


def build_city_episode_plan(public_task: Mapping[str, Any]) -> dict[str, Any]:
    """Build the reduced seven-task City plan for the bound native roster.

    The plan keeps the frozen roster binding (one ``guide_people`` task per
    assembly-capacity group, ``control_following_traffic`` over the six bound
    following vehicles in ``hold`` mode, and one ``report_incident`` to the
    frozen response service) while dropping the witness's navigation and
    observation tasks: the native host has already placed the responders and
    delivered the observation.  When the public contract publishes
    ``aerial_broadcast`` (City pool v3), one broadcast covering the whole
    roster is scheduled after the ground guidance tasks and assigned to
    ``iris_1``.  Placing it first would timestamp contact before the ground
    request and start the assembly clock early.  v1/v2 plans omit it.
    Windows are scheduled serially on the common simulation clock with the
    public ``skill_durations_s``; the report cites the frozen service endpoint.
    """
    durations = public_task["execution_contract"]["skill_durations_s"]
    for skill in ("guide_people", "control_following_traffic", "report_incident"):
        if skill not in durations:
            raise ValueError(f"public task lacks the native {skill} duration")
    from .skill_ontology import required_capabilities

    groups = [(region_id, sorted(person for person, bound
                                 in _CITY_NATIVE_AFFECTED_PERSON_ASSEMBLY.items()
                                 if bound == region_id))
              for region_id in sorted(set(_CITY_NATIVE_AFFECTED_PERSON_ASSEMBLY.values()))]
    if sorted(person for _, persons in groups for person in persons) != \
            sorted(_CITY_NATIVE_AFFECTED_PERSON_ASSEMBLY):
        raise ValueError("assembly groups do not cover the frozen roster exactly")

    def row(task_id: str, skill: str, arguments: Mapping[str, Any],
            prerequisites: list[str], agents: list[str], start_s: float) -> dict[str, Any]:
        return {"task_id": task_id, "skill": skill, "arguments": deepcopy(dict(arguments)),
                "prerequisites": list(prerequisites),
                "required_capabilities": sorted(required_capabilities(skill)),
                "assigned_agents": list(agents), "evidence_refs": [],
                "execution": {"start_s": float(start_s),
                              "duration_s": float(durations[skill])}}

    tasks: list[dict[str, Any]] = []
    guide_ids: list[str] = []
    clock_s = 0.0
    for index, (region_id, person_ids) in enumerate(groups):
        task_id = f"guide_people_{region_id}"
        # The first two groups may be acknowledged in either order; the rest
        # wait for the first group so the native ACK chain stays serial.
        prerequisites = [] if index < 2 else [guide_ids[0]]
        tasks.append(row(task_id, "guide_people",
                         {"person_ids": list(person_ids),
                          "observation_refs": [CITY_NATIVE_OBSERVATION_REF],
                          "assembly_region_id": region_id},
                         prerequisites, ["go2_1"], clock_s))
        guide_ids.append(task_id)
        clock_s += float(durations["guide_people"])
    if "aerial_broadcast" in durations:
        roster = sorted(_CITY_NATIVE_AFFECTED_PERSON_ASSEMBLY)
        tasks.append(row(
            "aerial_broadcast_roster", "aerial_broadcast",
            {"person_ids": roster,
             "observation_refs": [CITY_NATIVE_OBSERVATION_REF]},
            [guide_ids[0]], ["iris_1"], clock_s))
        # The skill name is the declared capability gate. Ontology requirements
        # are aerial_navigation plus relay; iris_1 on pool v3 also publishes
        # aerial_broadcast, and a ground-only assignee must fail that check.
        tasks[-1]["required_capabilities"] = sorted(
            set(tasks[-1]["required_capabilities"]) | {"aerial_broadcast"})
        clock_s += float(durations["aerial_broadcast"])
    control_id = "control_following_traffic_hold"
    tasks.append(row(control_id, "control_following_traffic",
                     {"vehicle_ids": list(_CITY_NATIVE_FOLLOWING_VEHICLE_IDS),
                      "observation_refs": [CITY_NATIVE_OBSERVATION_REF], "mode": "hold"},
                     [guide_ids[0]], ["go2_1"], clock_s))
    clock_s += float(durations["control_following_traffic"])
    tasks.append(row("report_incident_city_dispatch", "report_incident",
                     {"observation_refs": [CITY_NATIVE_OBSERVATION_REF],
                      "recipient_service_id": CITY_RESPONSE_SERVICE_ID,
                      "time_s": clock_s},
                     [*guide_ids, control_id], ["iris_1"], clock_s))
    plan = {"schema": "eai.benchmark.goal_plan", "version": GOAL_PLAN_VERSION,
            "plan_id": CITY_EPISODE_PLAN_ID,
            "assigned_team": ["go2_1", "iris_1"], "tasks": tasks}
    validate_goal_plan(plan, task_spec=public_task)
    return plan


class CityWitnessParticipant:
    """Fixed high-level participant that walks the reduced City plan once.

    It emits ``select_team`` plus ``submit_plan`` on ``episode_started`` and
    then dispatches exactly one task per turn, only after the previous
    dispatch reached a terminal ``action_result``.  Trusted receipts, the host
    report and the runtime snapshot never cross into this object.
    """

    def __init__(self, plan: Mapping[str, Any]):
        self._plan = deepcopy(dict(plan))
        self._opened = False
        self._dispatched = 0
        self._terminal: set[str] = set()

    def manifest(self) -> Mapping[str, Any]:
        return {"kind": "participant", "id": "city-native-goal-witness", "version": 1,
                "interface": "eai.benchmark.goal_algorithm_adapter", "interface_version": 1,
                "evaluation_track": "goal_driven",
                "input_scope": "participant_visible_events_only",
                "control_scope": "benchmark_managed_low_level_control"}

    def initialize(self, public_task: Mapping[str, Any],
                   participant_api: Mapping[str, Any]) -> None:
        del public_task, participant_api

    def act(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not self._opened:
            if not any(item["type"] == "episode_started" for item in events):
                return []
            self._opened = True
            return [_action("city_witness_select", "select_team",
                            {"agent_ids": list(self._plan["assigned_team"])}, 0.0),
                    _action("city_witness_plan", "submit_plan",
                            {"plan": deepcopy(self._plan)}, 0.0)]
        for item in events:
            if item["type"] == "action_result" and item["payload"].get("status") in {
                    "succeeded", "failed", "cancelled"}:
                self._terminal.add(item["payload"]["action_id"])
        tasks = self._plan["tasks"]
        if self._dispatched >= len(tasks):
            return []
        previous = f"city_witness_dispatch_{self._dispatched - 1}"
        if self._dispatched and previous not in self._terminal:
            return []
        index = self._dispatched
        self._dispatched += 1
        now = max((item["time_s"] for item in events), default=0.0)
        return [_action(f"city_witness_dispatch_{index}", "dispatch_skill",
                        {"task_id": tasks[index]["task_id"], "evidence_refs": []}, now)]

    def close(self) -> None:
        pass


class _CapabilityExecutionFailure(RuntimeError):
    """Deterministic execution-layer failure for incapable assignments."""


class CityNativeGoalBackend:
    """Execute GoalSession dispatches through the concrete City services.

    ``ctx`` is the injection context supplied by ``_run_bound_episode``; every
    native call, pose and clock reading comes from it.  Native request/contact/
    review/readback summaries accumulate in ``self.evidence`` so the business
    evaluator can assemble the acceptance envelope from trusted readbacks
    only.  Backend failures raise and let the runner archive the episode
    error; no path fabricates a success.
    """

    def __init__(self, ctx: Mapping[str, Any], plan: Mapping[str, Any] | None = None,
                 public_task: Mapping[str, Any] | None = None, *,
                 clock_epoch_s: float = 0.0,
                 strict_dispatch_clock: bool = False):
        self._ctx = dict(ctx)
        self.clock_epoch_s = _clock_epoch(clock_epoch_s)
        if type(strict_dispatch_clock) is not bool:
            raise ValueError("strict_dispatch_clock must be a bool")
        self.strict_dispatch_clock = strict_dispatch_clock
        # ``plan`` remains an ignored compatibility argument for legacy
        # callers. Runtime execution is bound exclusively to the pending task
        # in GoalSession's trusted report, so autonomous adapters do not need
        # an author-preconstructed plan at backend construction time.
        del plan
        self._robot_regions = (city_robot_regions(public_task) if public_task is not None
                               else dict(CITY_ROBOT_REGIONS))
        self._task_pool = list((public_task or {}).get("candidate_robot_pool") or [])
        self._assembly_candidates = {
            item["id"] for item in (public_task or {}).get("public_world", {}).get("regions", ())
            if item.get("kind") == "assembly_candidate"}
        budget = ((public_task or {}).get("budgets") or {}).get("simulation_s")
        self._simulation_budget_s = None
        if budget is not None:
            if (isinstance(budget, bool) or not isinstance(budget, (int, float))
                    or not math.isfinite(budget) or budget <= 0):
                raise ValueError("City simulation budget must be finite and positive")
            self._simulation_budget_s = float(budget)
        self.evidence: list[dict[str, Any]] = []
        # No simulation step can run between the host warmup observation and
        # this construction, so the shared clock reading is the observation's
        # delivery time (the native snapshot expires one second after capture).
        self._observation_time_s = float(ctx["city"].elapsed)
        self._observation_ref = str(ctx["guidance_observation"])
        self.evidence.append({"kind": "episode_observation",
                              "observation_ref": self._observation_ref,
                              "delivered_at_s": self._observation_time_s})

    # -- trusted observation renewal ------------------------------------
    def _current_observation(self) -> str:
        ctx = self._ctx
        now = float(ctx["city"].elapsed)
        if now <= self._observation_time_s:
            return self._observation_ref
        # A trusted intake observer may already have captured this exact
        # native tick. Reuse it to avoid the runner's timestamp-derived ID
        # colliding; observations from older ticks never refresh the TTL.
        runner = ctx["runner"]
        current_ref = getattr(runner, "_observation_ref", None)
        lookup = getattr(getattr(runner, "host", None), "observation", None)
        if isinstance(current_ref, str) and current_ref and callable(lookup):
            current = lookup(current_ref)
            captured = (current or {}).get("captured_at_s") \
                if isinstance(current, Mapping) else None
            if (isinstance(captured, (int, float)) and not isinstance(captured, bool)
                    and math.isclose(float(captured), now, rel_tol=0.0, abs_tol=1e-9)):
                self._observation_time_s = now
                self._observation_ref = current_ref
                return self._observation_ref
        ref = str(runner.observe())
        self._observation_time_s = now
        self._observation_ref = ref
        return self._observation_ref

    # -- shared helpers ---------------------------------------------------
    _SERVICE_ROLE = {"guide_people": "guidance_contact",
                     "control_following_traffic": "traffic_control_point",
                     "report_incident": "incident_observer",
                     "aerial_broadcast": "aerial_broadcaster"}

    def _runtime_state(self) -> dict[str, Any]:
        return {"robot_regions": self._robot_regions, "object_states": {}}

    def _dispatch_agent(self, task: Mapping[str, Any]) -> str:
        """Member dispatch: the plan's assigned agent executes this service."""
        return str(task["assigned_agents"][0])

    def _assign_service_agent(self, task: Mapping[str, Any], *, region_id: str = "road_surface") -> str:
        """Rebind the native services to the task's assigned agent.

        Host-owned rebinding plus a measured field-role interval for the
        chosen agent (pose-sampled from the trusted articulation state), so
        the admission gates pass for whichever member the plan dispatched.
        Capability reality is enforced HERE, at the execution layer (user
        principle: bad assignments are measurable, never filtered at the
        adapter): an agent whose public capabilities do not cover the task's
        declared requirements fails this dispatch deterministically, with
        factual observables only - mirroring the warehouse overload pattern.
        """
        ctx = self._ctx
        agent_id = self._dispatch_agent(task)
        required = set(task.get("required_capabilities", ()))
        pool_caps = {row["id"]: set(row["public_capabilities"])
                     for row in self._task_pool}
        if required and agent_id in pool_caps and not required <= pool_caps[agent_id]:
            raise _CapabilityExecutionFailure(
                f"execution failure: agent {agent_id} lacks "
                f"{sorted(required - pool_caps[agent_id])} required by "
                f"{task['task_id']} (public capability contract)")
        # The native runner submits each service with its own agent_id (the
        # request adapter is a separate non-prose API path), so member
        # dispatch swaps the runner's agent identity per task.
        ctx["runner"].agent_id = agent_id
        role = self._SERVICE_ROLE[str(task["skill"])]
        pose = None
        try:
            from demo.city_benchmark.native_probe import _robot_tensor_pose
            pose = tuple(float(v) for v in _robot_tensor_pose(ctx["session"], agent_id))
        except (ImportError, AttributeError):
            # Pose sampling is trace evidence, not an admission input; offline
            # doubles without articulations still dispatch honestly.
            pass
        host = ctx["runner"].host
        now = float(ctx["runner"].adapter.now_s())
        # The real episode host rejects an occupancy end that is still in its
        # future. Window settling can move the adapter clock ahead of that host.
        # Offline doubles record the interval directly and have no host clock.
        host_now = getattr(host, "now_s", None)
        advance = getattr(host, "advance", None)
        if callable(host_now) and callable(advance):
            if now > float(host_now()):
                advance(now)
            else:
                now = float(host_now())
            start = now - float(ctx["dt"])
            if start < 0.0:
                start = 0.0
            if not start < now:
                advance(now + float(ctx["dt"]))
                now = float(host_now())
                start = now - float(ctx["dt"])
        else:
            # A strictly positive, already-elapsed interval (the host rejects
            # start == end): span the most recent physics step.
            start = max(0.0, now - float(ctx["dt"]))
        host.record_role_occupancy(
            agent_id, role, region_id, start_time_s=start, end_time_s=now)
        ctx["trace"].setdefault("member_dispatch", []).append({
            "task_id": task["task_id"], "skill": task["skill"],
            "agent_id": agent_id, "role": role, "region_id": region_id,
            "pose_xyz": (list(pose) if pose is not None else None),
            "recorded_at_s": now,
        })
        return agent_id

    def _settle_to_window(self, task: Mapping[str, Any]) -> float:
        """Advance native time to an episode-relative reserved window end."""
        ctx = self._ctx
        window_end = float(task["execution"]["start_s"]) + float(task["execution"]["duration_s"])
        native_window_end = self.clock_epoch_s + window_end
        while float(ctx["city"].elapsed) + float(ctx["dt"]) <= native_window_end:
            ctx["step"](ctx["session"], ctx["city"], ctx["actions"], 1)
        return window_end

    def _relative_now(self) -> float:
        native_now = float(self._ctx["city"].elapsed)
        if not math.isfinite(native_now):
            raise ValueError("native City clock must be finite")
        return max(0.0, native_now - self.clock_epoch_s)

    def _strict_dispatch_start(
        self,
        task: Mapping[str, Any],
        *,
        episode_id: str,
        action_id: str,
        dispatched_at_s: float,
        backend_entry_host_s: float,
    ) -> tuple[dict[str, Any], bool]:
        """Align native time before service entry and archive the real clock."""
        for value, label in (
                (dispatched_at_s, "trusted ledger dispatch time"),
                (backend_entry_host_s, "backend entry host time")):
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{label} must be finite and nonnegative")
        if dispatched_at_s > backend_entry_host_s:
            raise ValueError("trusted dispatch cannot follow backend entry host time")
        planned_start = float(task["execution"]["start_s"])
        duration = float(task["execution"]["duration_s"])
        planned_end = planned_start + duration
        if (not math.isfinite(planned_start) or planned_start < 0
                or not math.isfinite(duration) or duration <= 0
                or not math.isfinite(planned_end)):
            raise ValueError("City planned window must be finite and nonnegative")

        row = {
            "kind": "dispatch_clock",
            "episode_id": episode_id,
            "action_id": action_id,
            "task_id": task["task_id"],
            "skill": task["skill"],
            "clock_scope": "backend_entry",
            "clock_epoch_s": self.clock_epoch_s,
            "planned_start_s": planned_start,
            "planned_end_s": planned_end,
            "dispatched_at_s": dispatched_at_s,
            "ledger_dispatch_s": dispatched_at_s,
            "backend_entry_host_s": backend_entry_host_s,
            "native_actual_start_s": None,
            "actual_start_s": None,
            "native_actual_end_s": None,
            "actual_end_s": None,
            "service_request_id": None,
            "service_request_native_s": None,
            "service_request_time_s": None,
            "disposition": None,
            "status": None,
        }
        native_now = float(self._ctx["city"].elapsed)
        relative_now = self._relative_now()
        if backend_entry_host_s >= planned_end or relative_now >= planned_end:
            row.update(disposition="missed_window", status="failed")
            self.evidence.append(row)
            return row, False

        target_relative = max(planned_start, backend_entry_host_s)
        target_native = self.clock_epoch_s + target_relative
        if native_now < target_native:
            dt = float(self._ctx["dt"])
            if not math.isfinite(dt) or dt <= 0:
                raise ValueError("native City step must be finite and positive")
            step_count = max(1, math.ceil((target_native - native_now) / dt))
            self._ctx["step"](
                self._ctx["session"], self._ctx["city"],
                self._ctx["actions"], step_count)
            native_now = float(self._ctx["city"].elapsed)
            relative_now = self._relative_now()

        # The plan window is half-open. A physics quantum that crosses the end
        # cannot be treated as an on-time service start.
        if relative_now >= planned_end:
            row.update(disposition="missed_window", status="failed")
            self.evidence.append(row)
            return row, False
        if relative_now < backend_entry_host_s:
            raise RuntimeError("native City clock did not reach backend entry host time")

        row.update(
            native_actual_start_s=native_now,
            actual_start_s=relative_now,
            disposition="executing",
        )
        self.evidence.append(row)
        return row, True

    def _finish_dispatch_clock(
        self,
        row: dict[str, Any],
        result: Mapping[str, Any] | None,
    ) -> None:
        native_end = float(self._ctx["city"].elapsed)
        row["native_actual_end_s"] = native_end
        row["actual_end_s"] = self._relative_now()
        row["disposition"] = "executed"
        row["status"] = result.get("status") if result is not None else "error"
        for evidence in reversed(self.evidence):
            if evidence is row or evidence.get("task_id") != row["task_id"]:
                continue
            request = evidence.get("request")
            if not isinstance(request, Mapping):
                continue
            request_id = request.get("request_id")
            submitted = request.get("submitted_time_s")
            if (isinstance(request_id, str) and request_id
                    and isinstance(submitted, (int, float))
                    and not isinstance(submitted, bool)
                    and math.isfinite(submitted)):
                row["service_request_id"] = request_id
                row["service_request_native_s"] = float(submitted)
                row["service_request_time_s"] = _relative_timestamp(
                    submitted, self.clock_epoch_s)
            break

    def _strict_window_expired(self, task: Mapping[str, Any]) -> bool:
        if not self.strict_dispatch_clock:
            return False
        planned_end = (float(task["execution"]["start_s"])
                       + float(task["execution"]["duration_s"]))
        return self._relative_now() >= planned_end

    def advance_idle(self, target_time_s: float) -> Mapping[str, Any]:
        """Advance only the real native clock toward a relative wakeup."""
        if (isinstance(target_time_s, bool)
                or not isinstance(target_time_s, (int, float))
                or not math.isfinite(target_time_s)):
            raise ValueError("City idle target must be finite")
        target = float(target_time_s)
        now = self._relative_now()
        if target <= now:
            raise ValueError("City idle target must advance the episode clock")
        if self._simulation_budget_s is None:
            raise RuntimeError("City idle advance requires a public simulation budget")
        budget = self._simulation_budget_s
        if now >= budget:
            raise RuntimeError("City idle cannot advance beyond the simulation budget")
        dt = float(self._ctx["dt"])
        if not math.isfinite(dt) or dt <= 0:
            raise ValueError("native City step must be finite and positive")

        limit = min(target, budget)
        steps = max(1, math.ceil((limit - now) / dt - 1e-9))
        if now + steps * dt > budget + 1e-9:
            steps = math.floor((budget - now) / dt + 1e-9)
        if steps <= 0:
            raise RuntimeError("City idle has no reachable physics step within budget")

        native_start = float(self._ctx["city"].elapsed)
        self._ctx["step"](
            self._ctx["session"], self._ctx["city"], self._ctx["actions"], steps)
        native_end = float(self._ctx["city"].elapsed)
        actual = self._relative_now()
        if not native_end > native_start or not actual > now:
            raise RuntimeError("City idle physics clock did not advance")
        if actual > budget + 1e-9:
            raise RuntimeError("City idle physics clock exceeded the simulation budget")
        self.evidence.append({
            "kind": "idle_clock",
            "clock_epoch_s": self.clock_epoch_s,
            "requested_time_s": target,
            "start_time_s": now,
            "completed_at_s": actual,
            "native_start_s": native_start,
            "native_end_s": native_end,
            "control_steps": steps,
        })
        return {"time_s": actual, "runtime_state": self._runtime_state()}

    def _terminal(self, task: Mapping[str, Any], row: Mapping[str, Any], episode_id: str,
                  status: str, completed_at_s: float, evidence_refs: list[str],
                  receipt_ref: Any) -> dict[str, Any]:
        refs = [str(ref) for ref in dict.fromkeys(evidence_refs)]
        if not refs:
            refs = [f"{CITY_BACKEND_ID}:{task['task_id']}"]
        completed = float(completed_at_s)
        if not math.isfinite(completed) or completed < 0:
            raise ValueError("City receipt time must be finite and nonnegative")
        return {"status": status, "completed_at_s": completed,
                "receipt": {"backend_id": CITY_BACKEND_ID, "receipt_ref": str(receipt_ref),
                            "task_id": task["task_id"],
                            "action_id": row["dispatch_action_id"],
                            "status": status, "completed_at_s": completed,
                            "episode_id": episode_id, "evidence_refs": refs},
                "runtime_state": self._runtime_state()}

    # -- GoalExecutionBackend protocol ------------------------------------
    def execute(self, task: Mapping[str, Any], *, episode_id: str,
                trusted_report: Mapping[str, Any]) -> Mapping[str, Any]:
        ledger = trusted_report["session"]
        task_id = task["task_id"]
        row = ledger["tasks"][task_id]
        if (ledger["episode_id"] != episode_id or row["status"] != "pending"
                or row["task"] != task or row["plan_id"] != ledger["current_plan_id"]):
            raise ValueError("native city task does not match the pending GoalSession dispatch")
        ledger_now = float(ledger["current_time_s"])
        dispatch_clock = None
        if self.strict_dispatch_clock:
            dispatched = row.get("dispatched_at_s", ledger_now)
            if (isinstance(dispatched, bool)
                    or not isinstance(dispatched, (int, float))):
                raise ValueError("trusted ledger dispatched_at_s must be a number")
            dispatch_clock, can_execute = self._strict_dispatch_start(
                task,
                episode_id=episode_id,
                action_id=str(row["dispatch_action_id"]),
                dispatched_at_s=float(dispatched),
                backend_entry_host_s=ledger_now,
            )
            if not can_execute:
                completed = max(self._relative_now(), ledger_now)
                result = self._terminal(
                    task, row, episode_id, "failed", completed,
                    [f"dispatch_clock:{task_id}:missed_window"],
                    f"dispatch_clock:{episode_id}:{task_id}")
                result["reason"] = "native city service window expired before execution"
                return result
        skill = task["skill"]
        from demo.city_benchmark.guide_admission import CityGuideAdmissionError

        result: Mapping[str, Any] | None = None
        try:
            if skill == "guide_people":
                self._check_requested_guide_entities(task)
                self._check_public_guide_evidence(task, row, ledger)
                result = self._execute_guide(task, row, episode_id, ledger_now)
            elif skill == "control_following_traffic":
                result = self._execute_control(task, row, episode_id, ledger_now)
            elif skill == "report_incident":
                result = self._execute_report(task, row, episode_id, ledger_now)
            elif skill == "aerial_broadcast":
                result = self._execute_broadcast(task, row, episode_id, ledger_now)
            else:
                raise ValueError(f"native city backend does not implement skill: {skill}")
        except CityGuideAdmissionError as failure:
            # Legacy candidate heights were qualified for author-selected
            # people, not every public choice. Preserve that geometry but do
            # not charge its qualification gap to the participant.
            if "target_body_outside_region" in failure.reason_codes:
                failure = CityGuideAdmissionError(
                    failure.reason_codes, infrastructure_failure=True,
                    report={"platform_qualification_failure": True,
                            "admission": failure.report})
            evidence_ref = f"guide_admission:{episode_id}:{row['dispatch_action_id']}"
            self.evidence.append({
                "kind": "guide_admission_failure", "task_id": task_id,
                "action_id": row["dispatch_action_id"], "evidence_ref": evidence_ref,
                "classification": ("infrastructure_evidence_failure" if failure.infrastructure_failure
                                   else "execution_condition_failure"),
                "reason_codes": list(failure.reason_codes), "report": deepcopy(failure.report),
                "native_time_s": float(self._ctx["city"].elapsed)})
            if failure.infrastructure_failure:
                raise failure
            result = self._terminal(task, row, episode_id, "failed",
                                    max(self._relative_now(), ledger_now), [evidence_ref], evidence_ref)
            result["city_admission_outcome"] = {
                "task_id": task_id, "action_id": row["dispatch_action_id"], "status": "failed",
                "reason_codes": list(failure.reason_codes), "evidence_ref": evidence_ref}
        except _CapabilityExecutionFailure as failure:
            # Task-level failure with factual observables only: the episode
            # continues and the business outcomes judge the algorithm. Like a
            # success, the failed attempt consumes its reserved window (the
            # world settles to the window end) so the plan cadence holds.
            window_end = self._settle_to_window(task)
            now = window_end
            self.evidence.append({"kind": "capability_execution_failure",
                                  "task_id": task["task_id"],
                                  "agent_id": self._dispatch_agent(task),
                                  "reason": str(failure)})
            result = {"status": "failed", "completed_at_s": now,
                      "runtime_state": self._runtime_state(),
                      "receipt": {"backend_id": CITY_BACKEND_ID,
                                  "receipt_ref": f"capability:{episode_id}:{task['task_id']}",
                                  "task_id": task["task_id"],
                                  "action_id": row["dispatch_action_id"],
                                  "status": "failed", "completed_at_s": now,
                                  "episode_id": episode_id,
                                  "evidence_refs": [f"capability:{task['task_id']}"]}}
        finally:
            if dispatch_clock is not None:
                self._finish_dispatch_clock(dispatch_clock, result)
        return result

    def _check_requested_guide_entities(self, task) -> None:
        """Reject invalid public identities without requiring tool consumption."""
        from demo.city_benchmark.guide_admission import CityGuideAdmissionError

        service = getattr(self._ctx["city"], "_episode_service", None)
        if not _requested_target_profile(service):
            return
        target = task["arguments"]["assembly_region_id"]
        if target not in self._assembly_candidates:
            raise CityGuideAdmissionError(
                ["target_region_unknown"], infrastructure_failure=False,
                report={"invalid_public_target": target})
        lookup = getattr(self._ctx["runner"].host, "observation", None)
        if callable(lookup):
            observation = lookup(self._current_observation())
            if not isinstance(observation, Mapping):
                raise ValueError("current trusted City observation is missing")
            persons = {row["id"] for row in observation.get("entities", ())
                       if row.get("kind") == "person"}
            if not set(task["arguments"]["person_ids"]) <= persons:
                raise CityGuideAdmissionError(
                    ["person_not_observed"], infrastructure_failure=False,
                    report={"requested_person_ids": task["arguments"]["person_ids"]})

    def _check_public_guide_evidence(self, task, dispatch_row, ledger) -> None:
        """Bind optional cited measurements without making tool use mandatory."""
        from demo.city_benchmark.guide_admission import CityGuideAdmissionError

        self._guide_evidence_checked = None
        service = getattr(self._ctx["city"], "_episode_service", None)
        if service is None or not _requested_target_profile(service):
            return
        action = next((action for action in ledger.get("actions", ())
                       if action.get("action_id") == dispatch_row["dispatch_action_id"]), {})
        refs = set(task.get("evidence_refs", ())) | set(action.get("payload", {}).get("evidence_refs", ()))
        cited = []
        consumed = set(ledger.get("consumed_message_ids", ()))
        for event in ledger.get("events", ()):
            payload = event.get("payload", {})
            if event.get("type") != "observation" or payload.get("message_id") not in refs:
                continue
            estimate = payload.get("data", {}).get("estimate", {})
            if "assembly_affordance" in estimate:
                if payload["message_id"] not in consumed:
                    raise CityGuideAdmissionError(
                        reason_codes=["route_proposal_missing"], infrastructure_failure=True,
                        report={"unconsumed_public_evidence": payload["message_id"]})
                cited.append((payload, estimate["assembly_affordance"]))
        if not cited:
            return  # Blind target choice is a legitimate participant action.
        target = task["arguments"]["assembly_region_id"]
        people = set(task["arguments"]["person_ids"])
        signatures = {tuple(sorted((r["person_id"], r["state_sha256"])
                                  for r in snap["routes"]
                                  if r["region_id"] == target and r["person_id"] in people))
                      for _, snap in cited}
        if len(signatures) != 1:
            raise CityGuideAdmissionError(
                reason_codes=["affordance_changed"], infrastructure_failure=False,
                report={"conflicting_message_ids": [p["message_id"] for p, _ in cited]})
        payload, snapshot = max(cited, key=lambda item: item[1]["captured_at_s"])
        now = self._relative_now()
        if now >= min(float(payload["valid_until_s"]), float(snapshot["valid_until_s"])):
            raise CityGuideAdmissionError(
                reason_codes=["affordance_expired"], infrastructure_failure=False,
                report={"message_id": payload["message_id"], "now_s": now})
        current = service.assembly_affordances()
        for person in task["arguments"]["person_ids"]:
            old = next((r for r in snapshot["routes"]
                        if (r["person_id"], r["region_id"]) == (person, target)), None)
            new = next((r for r in current["routes"]
                        if (r["person_id"], r["region_id"]) == (person, target)), None)
            if old is None or new is None:
                raise CityGuideAdmissionError(
                    reason_codes=["route_proposal_missing"], infrastructure_failure=True,
                    report={"message_id": payload["message_id"], "person_id": person, "region_id": target})
            if new.get("status") == "unavailable" or "target_body_outside_region" in new.get("reason_codes", ()):
                raise CityGuideAdmissionError(
                    reason_codes=new.get("reason_codes") or ["route_measurement_unknown"],
                    infrastructure_failure=True,
                    report={"message_id": payload["message_id"], "previous": old, "current": new})
            if old["state_sha256"] != new["state_sha256"]:
                raise CityGuideAdmissionError(
                    reason_codes=["affordance_changed"], infrastructure_failure=False,
                    report={"message_id": payload["message_id"], "previous": old, "current": new})
        candidate = next((candidate for candidate in current.get("candidates", ())
                          if candidate["region_id"] == target), None)
        batch_fits = False
        if candidate is not None:
            occupants = candidate.get("occupant_ids", [])
            reservations = candidate.get("reservation_ids", [])
            # These identity sets remain trusted-side. Counts alone cannot
            # detect overlap with the requested people or between claims.
            claims_known = (len(occupants) == candidate.get("occupancy_count", 0)
                            and len(reservations) == candidate.get("reservation_count", 0))
            batch_fits = claims_known and len(set(occupants) | set(reservations) | people) <= candidate["capacity"]
        self._guide_evidence_checked = {
            "task_id": task["task_id"], "message_id": payload["message_id"],
            "batch_fits": batch_fits,
            "routes": [r for r in current["routes"]
                       if r["region_id"] == target and r["person_id"] in task["arguments"]["person_ids"]]}

    def cancel(self, task: Mapping[str, Any], *, episode_id: str,
               trusted_report: Mapping[str, Any]) -> Mapping[str, Any]:
        ledger = trusted_report["session"]
        task_id = task["task_id"]
        row = ledger["tasks"][task_id]
        if (ledger["episode_id"] != episode_id or row["status"] != "cancel_requested"
                or row["task"] != task):
            raise ValueError("native city cancel does not match the requested task")
        # The City runtime has no native cancel; this acknowledges the ledger
        # without touching the scene, and the honest reason travels beside the
        # receipt because GoalSession enforces an exact receipt field set.
        return {"status": "cancelled", "completed_at_s": float(ledger["current_time_s"]),
                "receipt": {"backend_id": CITY_BACKEND_ID,
                            "receipt_ref": f"cancel:{episode_id}:{task_id}",
                            "task_id": task_id,
                            "action_id": row["dispatch_action_id"],
                            "status": "cancelled",
                            "completed_at_s": float(ledger["current_time_s"]),
                            "episode_id": episode_id,
                            "evidence_refs": [f"cancel:{episode_id}:{task_id}"]},
                "runtime_state": self._runtime_state(),
                "reason": "native city cancel not implemented; ledger-only cancel"}

    # -- skill recipes ------------------------------------------------------
    def _execute_guide(self, task: Mapping[str, Any], row: Mapping[str, Any],
                       episode_id: str, ledger_now: float) -> dict[str, Any]:
        ctx = self._ctx
        runner, city = ctx["runner"], ctx["city"]
        person_ids = list(task["arguments"]["person_ids"])
        region_id = task["arguments"]["assembly_region_id"]
        guidance = None
        observation = None
        for attempt in range(CITY_GUIDE_RETRY_LIMIT):
            if self._strict_window_expired(task):
                result = self._terminal(
                    task, row, episode_id, "failed",
                    max(self._relative_now(), ledger_now),
                    [f"dispatch_clock:{task['task_id']}:retry_window_expired"],
                    f"retry_window:{episode_id}:{task['task_id']}")
                result["reason"] = "native city guide retry window expired"
                return result
            observation = self._current_observation()
            self._assign_service_agent(task)
            try:
                guidance = runner.guide_people(tuple(person_ids), observation_ref=observation,
                                               assembly_region_id=region_id)
                break
            except ValueError as exc:
                from demo.city_benchmark.guide_admission import CityGuideAdmissionError

                if isinstance(exc, CityGuideAdmissionError):
                    checked = getattr(self, "_guide_evidence_checked", None)
                    if (checked is not None and checked["task_id"] == task["task_id"]
                            and checked["batch_fits"]
                            and all(r["status"] == "admissible" for r in checked["routes"])):
                        raise CityGuideAdmissionError(
                            reason_codes=list(exc.reason_codes), infrastructure_failure=True,
                            report={"same_state_admission_contradiction": True,
                                    "public_evidence": checked, "admission": exc.report}) from exc
                    raise  # A typed rejection is not a transient legacy retry.
                if "not admitted" not in str(exc) or attempt == CITY_GUIDE_RETRY_LIMIT - 1:
                    raise
                ctx["step"](ctx["session"], city, ctx["actions"],
                            max(1, math.ceil(1.0 / float(ctx["dt"]))))
        request = dict(guidance["request"])
        for person_id in person_ids:
            city.people.resume(person_id)
        contact = dict(runner.guidance_contact(request["request_id"]))
        acknowledged_ids = {str(actor["id"]) for actor in contact.get("actor_acks") or ()}
        acknowledged = (contact.get("status") == "contact_confirmed"
                        and set(person_ids) <= acknowledged_ids)
        self.evidence.append({
            "kind": "guide", "task_id": task["task_id"], "skill": "guide_people",
            "person_ids": person_ids, "assembly_region_id": region_id,
            "observation_ref": observation, "request": request, "contact": contact,
            "acknowledged": acknowledged})
        service = getattr(city, "_episode_service", None)
        admission = getattr(service, "_last_guidance_admission_report", None)
        if admission is not None:
            self.evidence[-1]["requested_target_admission"] = deepcopy(admission)
        refs = [str(request["request_id"]),
                *(str(ref) for ref in contact.get("evidence_refs") or ())]
        if not acknowledged:
            return self._terminal(task, row, episode_id, "failed",
                                  max(self._relative_now(), ledger_now), refs,
                                  request["request_id"])
        window_end = self._settle_to_window(task)
        if float(city.elapsed) > self.clock_epoch_s + window_end + float(ctx["dt"]):
            return self._terminal(task, row, episode_id, "failed",
                                  max(self._relative_now(), ledger_now), refs,
                                  request["request_id"])
        return self._terminal(task, row, episode_id, "succeeded", window_end, refs,
                              request["request_id"])

    def _execute_control(self, task: Mapping[str, Any], row: Mapping[str, Any],
                         episode_id: str, ledger_now: float) -> dict[str, Any]:
        ctx = self._ctx
        runner, city = ctx["runner"], ctx["city"]
        vehicle_ids = list(task["arguments"]["vehicle_ids"])
        mode = task["arguments"]["mode"]
        observation = self._current_observation()
        self._assign_service_agent(task)
        before = {vehicle_id: tuple(float(value) for value in city.following_vehicle_pose(vehicle_id))
                  for vehicle_id in vehicle_ids}
        held = runner.control_following_traffic(tuple(vehicle_ids), mode,
                                                observation_ref=observation)
        request = dict(held["request"])
        reaction_s = float(ctx["window"]["trajectory_reaction_window_s"]) + 0.1
        ctx["step"](ctx["session"], city, ctx["actions"],
                    max(1, math.ceil(reaction_s / float(ctx["dt"]))))
        readbacks = {vehicle_id: dict(runner.record_vehicle_readback(
            request["request_id"], vehicle_id, previous_position=before[vehicle_id],
            status_before=mode)) for vehicle_id in vehicle_ids}
        review = dict(runner.review(request["request_id"]))
        self.evidence.append({
            "kind": "control", "task_id": task["task_id"],
            "skill": "control_following_traffic", "vehicle_ids": vehicle_ids,
            "mode": mode, "observation_ref": observation, "request": request,
            "readbacks": readbacks, "review": review,
            "positions_before": {key: list(value) for key, value in before.items()}})
        refs = [str(request["request_id"]),
                *(str(self._readback_ref(readbacks[vehicle_id])) for vehicle_id in vehicle_ids)]
        status = "succeeded" if review.get("status") == "evidence_complete" else "failed"
        if status == "succeeded":
            window_end = self._settle_to_window(task)
            if float(city.elapsed) <= self.clock_epoch_s + window_end + float(ctx["dt"]):
                return self._terminal(task, row, episode_id, "succeeded", window_end, refs,
                                      request["request_id"])
        return self._terminal(task, row, episode_id, "failed",
                              max(self._relative_now(), ledger_now), refs, request["request_id"])

    def _execute_report(self, task: Mapping[str, Any], row: Mapping[str, Any],
                        episode_id: str, ledger_now: float) -> dict[str, Any]:
        ctx = self._ctx
        runner, city = ctx["runner"], ctx["city"]
        observation = self._current_observation()
        self._assign_service_agent(task)
        result = runner.report_incident(observation_ref=observation)
        request = dict(result["request"])
        receipt = dict(result.get("receipt") or {})
        receipt_time = receipt.get("received_time_s")
        if not isinstance(receipt_time, (int, float)) or isinstance(receipt_time, bool):
            receipt_time = float(city.elapsed)
        access_steps = max(1, math.ceil(
            float(ctx["endpoint"]["continuous_access_min_span_s"]) / float(ctx["dt"]))) + 1
        ctx["step"](ctx["session"], city, ctx["actions"], access_steps)
        access = dict(runner.record_continuous_access(
            request, start_time_s=float(receipt_time), end_time_s=float(city.elapsed)))
        access_end_s = float(city.elapsed)
        review = dict(runner.review(request["request_id"]))
        self.evidence.append({
            "kind": "report", "task_id": task["task_id"], "skill": "report_incident",
            "observation_ref": observation, "request": request, "receipt": receipt,
            "receipt_time_s": float(receipt_time), "access": access,
            "access_end_time_s": access_end_s, "review": review})
        refs = [str(request["request_id"])]
        receipt_ref = receipt.get("receipt_evidence_ref")
        if receipt_ref:
            refs.append(str(receipt_ref))
        access_ref = self._readback_ref(access)
        if access_ref:
            refs.append(str(access_ref))
        status = "succeeded" if review.get("status") == "evidence_complete" else "failed"
        if status == "succeeded":
            window_end = self._settle_to_window(task)
            if float(city.elapsed) <= self.clock_epoch_s + window_end + float(ctx["dt"]):
                return self._terminal(task, row, episode_id, "succeeded", window_end, refs,
                                      request["request_id"])
        return self._terminal(task, row, episode_id, "failed",
                              max(self._relative_now(), ledger_now), refs, request["request_id"])

    def _execute_broadcast(self, task: Mapping[str, Any], row: Mapping[str, Any],
                           episode_id: str, ledger_now: float) -> dict[str, Any]:
        """Aerial advance-arrival broadcast (City P0 modality, ``eai.skills.v4``).

        The member dispatch gate runs first exactly like the ground services,
        so an assignment whose declared capabilities do not cover the task
        fails deterministically through the shared ``_CapabilityExecutionFailure``
        path (factual receipt, window still consumed).  A capable dispatch
        records one broadcast ACK per person - acknowledgement time from the
        native clock, evidence bound to the current trusted observation - plus
        the native clock stamp taken when the broadcast window opened.  The
        business evaluator decides what the ACK is worth (B parallel path);
        person arrival itself always stays with the arrival readbacks.
        """
        ctx = self._ctx
        city = ctx["city"]
        person_ids = list(task["arguments"]["person_ids"])
        self._assign_service_agent(task)
        observation = self._current_observation()
        arrival_stamp = float(city.elapsed)
        actor_acks = [{"id": person_id,
                       "ack_time_s": float(city.elapsed),
                       "ack_evidence_ref": observation}
                      for person_id in person_ids]
        self.evidence.append({
            "kind": "broadcast", "task_id": task["task_id"], "skill": "aerial_broadcast",
            "person_ids": person_ids, "actor_acks": actor_acks,
            "arrival_stamp": arrival_stamp, "observation_ref": observation})
        refs = [f"broadcast:{task['task_id']}"]
        receipt_ref = f"broadcast:{episode_id}:{task['task_id']}"
        window_end = self._settle_to_window(task)
        if float(city.elapsed) > self.clock_epoch_s + window_end + float(ctx["dt"]):
            return self._terminal(task, row, episode_id, "failed",
                                  max(self._relative_now(), ledger_now), refs, receipt_ref)
        return self._terminal(task, row, episode_id, "succeeded", window_end, refs, receipt_ref)

    @staticmethod
    def _readback_ref(readback: Mapping[str, Any]) -> str | None:
        for key in ("evidence_ref", "trajectory_evidence_ref", "hazard_evidence_ref"):
            value = readback.get(key)
            if isinstance(value, str) and value:
                return value
        # Native rows carry LiveEvidence objects rather than plain references.
        value = readback.get("trajectory_evidence")
        if isinstance(value, Mapping):
            value = value.get("evidence_ref")
        if not isinstance(value, str) or not value:
            evidence = readback.get("evidence")
            if isinstance(evidence, Mapping):
                evidence = evidence.get("evidence_ref")
            value = evidence if isinstance(evidence, str) else None
        if isinstance(value, str) and value:
            return value
        return getattr(readback.get("trajectory_evidence"), "evidence_ref", None)


def build_acceptance_evidence(evidence_log: list[Mapping[str, Any]],
                              ctx: Mapping[str, Any], *,
                              clock_epoch_s: float = 0.0) -> dict[str, Any]:
    """Assemble the acceptance envelope from trusted native readbacks only.

    Every boolean is derived from an admitted request, a confirmed contact, a
    recorded readback or a completed review; participant self-reports are never
    consulted.  Unmeasurable facts are omitted so ``evaluate_city_acceptance``
    fails closed on them, and this slice's missing assembly arrival is recorded
    explicitly as ``arrived_at_admitted_assembly=False``.

    B parallel path (City P0 aerial modality, frozen decision): an aerial
    ``aerial_broadcast`` ACK and a ground ``guide_people`` contact confirmation
    are two legal sources of the per-person contact-confirmation component.
    For each person, ``guidance_acknowledged`` is the union of both sources and
    ``guidance_time_s`` takes the earlier available acknowledgement; the ground
    observation provenance wins when both exist.  Person arrival at an
    admitted assembly always stays with the arrival readbacks, and the four
    business outcome predicates in ``evaluate_city_acceptance`` are unchanged.
    Evidence logs without broadcast rows keep exactly the historical behavior.
    """
    epoch = _clock_epoch(clock_epoch_s)
    records = list(evidence_log)
    guides = [row for row in records if row.get("kind") == "guide"]
    broadcasts = [row for row in records if row.get("kind") == "broadcast"]
    controls = [row for row in records if row.get("kind") == "control"]
    reports = [row for row in records if row.get("kind") == "report"]
    observation = next((row for row in records
                        if row.get("kind") == "episode_observation"), None)
    first = guides[0] if guides else None
    delivered = (_relative_timestamp(observation["delivered_at_s"], epoch)
                 if observation is not None else None)
    decision = (_relative_timestamp(first["request"]["submitted_time_s"], epoch)
                if first is not None else None)
    admitted = first is not None and observation is not None

    def contact_acks(record: Mapping[str, Any]) -> dict[str, Any]:
        return {str(actor["id"]): actor for actor in
                (record.get("contact") or {}).get("actor_acks") or ()
                if isinstance(actor, Mapping) and actor.get("id") is not None}

    first_acks = contact_acks(first) if first is not None else {}
    first_ack_time = None
    if first is not None and first.get("contact", {}).get("ack_time_s") is not None:
        first_ack_time = _relative_timestamp(first["contact"]["ack_time_s"], epoch)
    elif first_acks:
        first_ack_time = max(
            _relative_timestamp(actor.get("ack_time_s"), epoch)
            for actor in first_acks.values() if actor.get("ack_time_s") is not None)

    def broadcast_ack_time(record: Mapping[str, Any] | None,
                           person_id: str) -> float | None:
        if record is None:
            return None
        for actor in record.get("actor_acks") or ():
            if (isinstance(actor, Mapping) and actor.get("id") == person_id
                    and isinstance(actor.get("ack_time_s"), (int, float))
                    and not isinstance(actor.get("ack_time_s"), bool)):
                return _relative_timestamp(actor["ack_time_s"], epoch)
        return None

    people: list[dict[str, Any]] = []
    arrivals = {row.get("person_id"): row for row in records
                if row.get("kind") == "arrival"}
    for person_id in sorted(ctx["roster_ids"]):
        record = next((row for row in guides if person_id in row.get("person_ids", ())), None)
        broadcast = next((row for row in broadcasts
                          if person_id in row.get("person_ids", ())), None)
        broadcast_ack_s = broadcast_ack_time(broadcast, person_id)
        if record is None and broadcast is None:
            people.append({"person_id": person_id})
            continue
        acks = contact_acks(record) if record is not None else {}
        arrival = arrivals.get(person_id) or {}
        row: dict[str, Any] = {
            "person_id": person_id,
            # Ground provenance wins when both sources exist (B path rule).
            "observation_ref": (record.get("observation_ref") if record is not None
                                else broadcast.get("observation_ref")),
            "arrived_at_admitted_assembly": bool(arrival.get("arrived")),
        }
        if record is not None:
            # The native admission gate accepted the evacuation route and the
            # assembly capacity when it accepted this request.  A broadcast
            # alone carries no route admission; such a row keeps failing the
            # evaluator's remaining components honestly.
            row["route_admitted"] = True
            row["guidance_request_time_s"] = _relative_timestamp(
                record["request"]["submitted_time_s"], epoch)
        if arrival.get("arrived"):
            arrival_time = arrival.get("arrival_time_s")
            if isinstance(arrival_time, (int, float)) and not isinstance(arrival_time, bool):
                row["arrival_time_s"] = _relative_timestamp(arrival_time, epoch)
            review_actors = {str(actor.get("id")): actor for actor in
                             (dict(arrival.get("review") or {}).get("evidence")
                              or {}).get("actors") or ()
                             if isinstance(actor, Mapping)}
            entered_hazard = review_actors.get(person_id, {}).get("entered_hazard")
            if not isinstance(entered_hazard, bool):
                # Route safety preference: the readback row returned by the
                # arrival recording itself, then the review actor's flag.
                safe_route = arrival.get("safe_route_and_assembly")
                if not isinstance(safe_route, bool):
                    safe_route = review_actors.get(person_id, {}).get("safe_route_and_assembly")
                if isinstance(safe_route, bool):
                    entered_hazard = not safe_route
            if isinstance(entered_hazard, bool):
                row["entered_hazard"] = entered_hazard
        ack = acks.get(person_id)
        ground_acknowledged = bool(
            ack is not None
            and (record.get("contact") or {}).get("status") == "contact_confirmed")
        ack_times: list[float] = []
        if record is not None and ack is not None and ack.get("ack_time_s") is not None:
            ack_times.append(_relative_timestamp(ack["ack_time_s"], epoch))
        if broadcast_ack_s is not None:
            ack_times.append(broadcast_ack_s)
        # B path union: either legal contact-confirmation source acknowledges
        # the person; the earlier available acknowledgement timestamps it.
        row["guidance_acknowledged"] = bool(ground_acknowledged or broadcast_ack_s is not None)
        row["guidance_time_s"] = min(ack_times) if ack_times else None
        people.append(row)

    traffic: list[dict[str, Any]] = []
    control = controls[0] if controls else None
    review_evidence = dict((control or {}).get("review") or {}).get("evidence") or {}
    actors = {str(actor.get("id")): actor for actor in review_evidence.get("actors") or ()
              if isinstance(actor, Mapping) and actor.get("id") is not None}
    for vehicle_id in sorted(ctx["vehicle_ids"]):
        if control is None:
            traffic.append({"vehicle_id": vehicle_id})
            continue
        readback = dict((control.get("readbacks") or {}).get(vehicle_id) or {})
        actor = actors.get(vehicle_id) or {}
        ack_time = actor.get("ack_time_s")
        check_time = readback.get("trajectory_check_time_s")
        row: dict[str, Any] = {
            "vehicle_id": vehicle_id,
            # Only bound following traffic is controllable through this service.
            "following_incident": True,
            "control_acknowledged": bool(
                ack_time is not None
                and (control.get("review") or {}).get("status") == "evidence_complete"),
            "trajectory_sample_after_ack": bool(
                readback.get("measured_trajectory") is True and ack_time is not None
                and isinstance(check_time, (int, float))
                and not isinstance(check_time, bool)
                and float(check_time) >= float(ack_time)),
            "request_time_s": _relative_timestamp(
                control["request"]["submitted_time_s"], epoch),
            "ack_time_s": (_relative_timestamp(ack_time, epoch)
                           if isinstance(ack_time, (int, float))
                           and not isinstance(ack_time, bool) else None),
        }
        if check_time is not None:
            row["trajectory_check_time_s"] = _relative_timestamp(check_time, epoch)
        if readback:
            row["entered_exclusion_boundary"] = not bool(readback.get("no_hazard_ingress"))
        traffic.append(row)

    report = reports[0] if reports else None
    receipt = dict((report or {}).get("receipt") or {})
    received = receipt.get("received_time_s")
    access = dict((report or {}).get("access") or {})
    start = (_relative_timestamp(report["receipt_time_s"], epoch)
             if report is not None else None)
    end = (_relative_timestamp(report["access_end_time_s"], epoch)
           if report is not None else None)
    clear = access.get("measured_clear_throughout") is True
    envelope = {
        "incident_assessed": {
            "observation_ref": (observation or {}).get("observation_ref"),
            "fresh_at_decision": bool(admitted and delivered is not None
                                      and decision is not None and delivered <= decision),
            "source_visible_to_participant": admitted,
            "observation_delivery_time_s": delivered,
            "decision_time_s": decision,
        },
        "responder_dispatch": {
            "selected_agent_id": (first["request"].get("agent_id") if first is not None else None),
            "agent_active_at_dispatch": admitted,
            "capability_admitted": admitted,
            "route_admitted": admitted,
            "dispatch_time_s": decision,
            "arrival_time_s": first_ack_time,
            # Single admitted responder: vacuously minimal (diagnostic only).
            "eta_was_minimum_among_admitted_options": admitted,
        },
        "people": people,
        "traffic": traffic,
    }
    # Sections without measured evidence are omitted entirely: a skipped or
    # never-attempted service must fail via the evaluator's missing-section
    # failure codes, never via null timestamps that crash time parsing.
    if report is not None:
        envelope["incident_report"] = {
            "observation_refs": [report["observation_ref"]],
            "receipt_received": bool(isinstance(received, (int, float))
                                     and not isinstance(received, bool)),
            "submit_time_s": _relative_timestamp(
                report["request"]["submitted_time_s"], epoch),
            "receipt_time_s": (_relative_timestamp(received, epoch)
                               if isinstance(received, (int, float))
                               and not isinstance(received, bool) else None),
        }
    if report is not None and start is not None and end is not None:
        envelope["response_access"] = {
            "continuous_access_check": bool(clear and end > start),
            "route_admitted": access.get("rescue_and_evacuation_routes_admitted") is True,
            "hazard_free_throughout": clear,
            "check_start_time_s": start,
            "check_end_time_s": end,
        }
    return envelope


def stamp_arrival_times(ctx: Mapping[str, Any], stamps: dict[str, float]) -> None:
    """Record each person's first native arrival readback time.

    Called after every simulation advance so people who reach assembly while
    earlier skills execute are stamped at their actual arrival, not at the
    evaluator's first observation.
    """
    city = ctx["city"]
    service = city._episode_service
    for person_id in ctx["roster_ids"]:
        if person_id in stamps:
            continue
        try:
            if _support_footprint_profile(service):
                motion = service.people_controller.motion_readback(person_id)
                if motion.get("path_finished") is not True:
                    continue
                arrived = service.arrival_readback(person_id)["arrived"]
            else:
                arrived = service.people_controller.readback(
                    person_id, service.assembly_regions)["arrived"]
        except ValueError as exc:
            # Assembly assignments exist only after a person's guide request
            # is admitted; people still waiting for guidance are skipped.
            if "no assembly assignment" not in str(exc):
                raise
            continue
        if arrived:
            stamps[person_id] = float(city.elapsed)


def await_assembly_arrivals(ctx: Mapping[str, Any],
                            evidence_log: list[Mapping[str, Any]],
                            stamps: dict[str, float] | None = None) -> None:
    """Advance the native scene until the guided roster reaches assembly.

    Mirrors the host sequence's arrival wait: step the simulation until every
    bound person's ``people_controller`` readback reports ``arrived``, bounded
    per person by the public ``people_assembly_arrival_s`` budget counted from
    that person's acknowledgement (the acceptance predicate measures
    ``arrival_time_s - guidance_time_s``).  Arrivals are then recorded through
    the trusted runner and the review is taken after recording so the per-person
    ``arrival_time_s`` readback is present.
    """
    city = ctx["city"]
    roster = list(ctx["roster_ids"])
    guides = [row for row in evidence_log if row.get("kind") == "guide"]
    service = city._episode_service
    budget = float(default_deadline_policy()["people_assembly_arrival_s"])

    def ack_time(record: Mapping[str, Any], person_id: str) -> float | None:
        for actor in (record.get("contact") or {}).get("actor_acks") or ():
            if isinstance(actor, Mapping) and actor.get("id") == person_id \
                    and isinstance(actor.get("ack_time_s"), (int, float)) \
                    and not isinstance(actor.get("ack_time_s"), bool):
                return float(actor["ack_time_s"])
        return None

    def person_deadline(record: Mapping[str, Any], person_id: str) -> float:
        anchored = ack_time(record, person_id)
        if anchored is None:
            anchored = float(record["request"]["submitted_time_s"])
        return anchored + budget

    deadlines = [person_deadline(row, person_id)
                 for row in guides for person_id in row.get("person_ids", ())]
    horizon = max(deadlines, default=float(city.elapsed))
    first_arrival: dict[str, float] = stamps if stamps is not None else {}

    def poll_arrivals() -> bool:
        stamp_arrival_times(ctx, first_arrival)
        return all(person_id in first_arrival for person_id in roster)

    poll_arrivals()
    while float(city.elapsed) < horizon and not poll_arrivals():
        ctx["step"](ctx["session"], city, ctx["actions"])

    for row in guides:
        request_id = row["request"]["request_id"]
        region_id = row.get("assembly_region_id")
        for person_id in row.get("person_ids", ()):
            readback = (service.person_readback(person_id)
                        if _support_footprint_profile(service)
                        else service.people_controller.readback(
                            person_id, service.assembly_regions))
            recorded = ctx["runner"].record_person_arrival(
                request_id, person_id,
                previous_position=ctx["person_start"][person_id],
                assembly_region_id=region_id, arrived=bool(readback["arrived"]))
            review = ctx["runner"].review(request_id)
            actor = next((item for item in
                          (dict(review or {}).get("evidence") or {}).get("actors") or ()
                          if isinstance(item, Mapping) and item.get("id") == person_id), {})
            actor_time = actor.get("arrival_time_s")
            measured = first_arrival.get(person_id)
            arrival_time = (float(measured) if measured is not None
                            else (float(actor_time)
                                  if isinstance(actor_time, (int, float))
                                  and not isinstance(actor_time, bool) else None))
            # Route safety: prefer the trusted readback row returned by the
            # recording call itself; the review actor is only a fallback.
            safe_route = (recorded or {}).get("safe_route_and_assembly") \
                if isinstance(recorded, Mapping) else None
            if not isinstance(safe_route, bool):
                safe_route = actor.get("safe_route_and_assembly")
            evidence_log.append({
                "kind": "arrival", "person_id": person_id,
                "request_id": request_id, "assembly_region_id": region_id,
                "arrived": bool(readback["arrived"]),
                "arrival_time_s": arrival_time,
                "safe_route_and_assembly": safe_route,
                "review": review,
            })


def city_business_evaluator(evidence_log: list[Mapping[str, Any]],
                            ctx: Mapping[str, Any],
                            arrival_stamps: dict[str, float] | None = None,
                            *, clock_epoch_s: float = 0.0,
                            ) -> Callable[[str, Mapping[str, Any]], dict[str, dict[str, Any]]]:
    """Wrap the City acceptance predicates as a FormalGoalEpisodeRunner evaluator."""
    epoch = _clock_epoch(clock_epoch_s)

    def evaluate(episode_id: str, trusted_report: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
        del episode_id
        await_assembly_arrivals(ctx, evidence_log, stamps=arrival_stamps)
        envelope = build_acceptance_evidence(
            evidence_log, ctx, clock_epoch_s=epoch)
        # Raw boundary evidence retains None for a measurement that never
        # happened. The acceptance contract expresses that as a missing field,
        # not a timestamp to parse and not an invented zero. This allows a
        # rejected-before-start attempt to finish with honest failed outcomes.
        def measured_times(value):
            if isinstance(value, Mapping):
                return {key: measured_times(item) for key, item in value.items()
                        if not (key.endswith("_time_s") and item is None)}
            if isinstance(value, list):
                return [measured_times(item) for item in value]
            return value

        acceptance = evaluate_city_acceptance(
            measured_times(envelope), default_deadline_policy(),
            expected_affected_person_ids=set(ctx["roster_ids"]),
            expected_following_vehicle_ids=set(ctx["vehicle_ids"]))
        required = {row["id"] for row in trusted_report["session"]["business_outcomes"]}
        outcomes: dict[str, dict[str, Any]] = {}
        for outcome_id in sorted(required):
            reviewed = acceptance["outcomes"].get(outcome_id)
            outcomes[outcome_id] = {
                "status": "validated" if reviewed is not None and reviewed["success"] else "failed",
                "evidence_refs": [f"city_acceptance:{outcome_id}"],
                "evaluator_ref": CITY_EVALUATOR_REF,
            }
        return outcomes

    return evaluate


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2,
                               sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def _plain_evidence(value: Any) -> Any:
    """Recursively reduce native evidence to JSON-safe values.

    Live-source markers (e.g. ``LiveEvidence``) are not serializable and carry
    no participant-visible facts; they are reduced to a type tag so the
    evidence log archives without masking the episode result.
    """
    if isinstance(value, Mapping):
        return {str(key): _plain_evidence(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain_evidence(item) for item in value]
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    return f"<{type(value).__name__}>"


def run_city_episode(output_root: str | Path, *, device: str = "cpu",
                     episode_binding: str | Path | None = None,
                     algorithm: str = "witness",
                     pool: str = "v1") -> dict[str, Any]:
    """Prepare the formal City probe and run the injected GoalSession episode."""
    from demo.city_benchmark.service_probe import execute_probe, prepare_probe

    root = Path(output_root).absolute()
    binding = Path(episode_binding) if episode_binding is not None \
        else DEFAULT_CITY_EPISODE_BINDING
    # The physical probe stage stays frozen at the v1/v2 rosters.  Pool v3 is
    # the bound_native_v3 goal-side roster (same seven robots and spawn
    # anchors as v2 plus the aerial_broadcast capability declaration), so it
    # reuses the v2 physical stage unchanged.
    probe_pool = "v2" if pool == "v3" else pool
    prepare_probe(root, device=device, episode_binding=binding, pool=probe_pool)

    def driver(ctx: Mapping[str, Any]) -> None:
        episode_root = Path(ctx["report_path"]).parent / "goal_episode"
        roster_profile = {"v1": "bound_native_v1", "v2": "bound_native_v2",
                          "v3": "bound_native_v3"}[pool]
        package = build_goal_task_package("city", city_roster_profile=roster_profile)
        public = package["public_task"]
        plan = build_city_episode_plan(public)
        arrival_stamps: dict[str, float] = {}
        native_step = ctx["step"]

        def stamped_step(session, city, actions, count=1):
            native_step(session, city, actions, count)
            stamp_arrival_times(ctx, arrival_stamps)

        ctx["step"] = stamped_step
        backend = CityNativeGoalBackend(ctx, plan, public_task=public)
        participant = (CityTeamWeaverParticipant(plan) if algorithm == "teamweaver"
                       else CityTeamWeaverParticipant(plan, use_llm_decomposition=True)
                       if algorithm == "teamweaver_llm"
                       else CityEmosParticipant(plan) if algorithm == "emos"
                       else CityEmosParticipant(plan, use_llm_discussion=True)
                       if algorithm == "emos_llm"
                       else CityWitnessParticipant(plan))
        runner = FormalGoalEpisodeRunner(
            public, package["participant_api"], participant, backend,
            output_root=episode_root,
            evaluator=city_business_evaluator(backend.evidence, ctx,
                                              arrival_stamps=arrival_stamps))
        result = runner.run(initial_state={"robot_regions": city_robot_regions(public),
                                           "object_states": {}})
        source_sha256 = file_sha256(Path(__file__))
        # Report finalization first: an archival hiccup must never mask a
        # completed episode in the probe report.
        ctx["report"]["trace"]["goal_episode"] = {
            "episode_id": result["episode_id"], "output_root": str(episode_root),
            "error": result["error"], "turns": result["turns"],
            "skill_receipt_count": result["skill_receipt_count"],
            "backend": type(backend).__name__,
            "participant": type(participant).__name__,
            "participant_trace": deepcopy(getattr(participant, "trace", []) or []),
            "participant_plan_id": CITY_EPISODE_PLAN_ID,
            "source_sha256": source_sha256,
        }
        if result["error"] is None:
            ctx["report"].update(status="completed",
                                 device_actual=str(ctx["session"].device),
                                 episode_time_s=float(ctx["city"].elapsed))
        else:
            ctx["report"].update(status="blocked",
                                 blocker=f"goal episode error: {result['error']}")
        _write_json(episode_root / "goal_episode_source.json", {
            "schema": "eai.benchmark.goal_city_episode_source", "version": 1,
            "module": __name__, "source_path": str(Path(__file__)),
            "source_sha256": source_sha256, "episode_id": result["episode_id"],
        })
        _write_json(episode_root / "city_acceptance_evidence.json",
                    build_acceptance_evidence(backend.evidence, ctx))
        _write_json(episode_root / "native_evidence_log.json", {
            "schema": "eai.benchmark.goal_city_episode_native_evidence", "version": 1,
            "episode_id": result["episode_id"],
            "records": _plain_evidence(deepcopy(backend.evidence)),
        })

    return execute_probe(root, device=device, driver=driver, pool=probe_pool)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--device", choices=("cpu", "cuda:0"), default="cpu")
    parser.add_argument("--algorithm",
                        choices=("witness", "teamweaver", "teamweaver_llm", "emos", "emos_llm"),
                        default="witness",
                        help="participant driving the episode (witness = fixed plan; "
                             "teamweaver_llm = real dongli LLM decomposition; "
                             "emos = EMOS discussion manager allocation; "
                             "emos_llm = EMOS allocation through the dongli LLM discussion)")
    parser.add_argument("--pool", choices=("v1", "v2", "v3"), default="v1",
                        help="candidate pool profile (v2 = expanded 7-robot bound_native_v2; "
                             "v3 = bound_native_v3 aerial goal roster (skill catalog v4, "
                             "iris gains aerial_broadcast) on the same v2 physical stage)")
    parser.add_argument("--episode-binding", type=Path, default=None,
                        help="authoring/private.json or standalone binding document "
                             f"(default: {DEFAULT_CITY_EPISODE_BINDING})")
    args = parser.parse_args(argv)
    report = run_city_episode(args.output_root, device=args.device,
                              episode_binding=args.episode_binding,
                              algorithm=args.algorithm, pool=args.pool)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report.get("status") == "completed" else 1


__all__ = ["CityNativeGoalBackend", "CityWitnessParticipant", "build_acceptance_evidence",
           "build_city_episode_plan", "city_business_evaluator", "main", "run_city_episode"]


if __name__ == "__main__":
    raise SystemExit(main())
