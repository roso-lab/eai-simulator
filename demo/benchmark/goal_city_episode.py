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
CITY_ROBOT_REGIONS = {"go2_1": "road_surface", "carter_1": "road_surface",
                      "iris_1": "road_surface"}
DEFAULT_CITY_EPISODE_BINDING = ROOT / (
    "tmp/city_benchmark/recovery_binding_20260920/authoring/private.json")


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
    delivered the observation.  Windows are scheduled serially on the common
    simulation clock with the public ``skill_durations_s``; the report cites
    the frozen service endpoint.
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


class CityNativeGoalBackend:
    """Execute GoalSession dispatches through the concrete City services.

    ``ctx`` is the injection context supplied by ``_run_bound_episode``; every
    native call, pose and clock reading comes from it.  Native request/contact/
    review/readback summaries accumulate in ``self.evidence`` so the business
    evaluator can assemble the acceptance envelope from trusted readbacks
    only.  Backend failures raise and let the runner archive the episode
    error; no path fabricates a success.
    """

    def __init__(self, ctx: Mapping[str, Any], plan: Mapping[str, Any]):
        self._ctx = dict(ctx)
        self._plan = deepcopy(dict(plan))
        self.evidence: list[dict[str, Any]] = []
        # No simulation step can run between the host warmup observation and
        # this construction, so the shared clock reading is the observation's
        # delivery time (the native snapshot expires one second after capture).
        self._observation_time_s = float(ctx["city"].elapsed)
        self.evidence.append({"kind": "episode_observation",
                              "observation_ref": str(ctx["guidance_observation"]),
                              "delivered_at_s": self._observation_time_s})

    # -- trusted observation renewal ------------------------------------
    def _current_observation(self) -> str:
        ctx = self._ctx
        if float(ctx["city"].elapsed) <= self._observation_time_s:
            return str(ctx["guidance_observation"])
        ref = str(ctx["runner"].observe())
        self._observation_time_s = float(ctx["city"].elapsed)
        return ref

    # -- shared helpers ---------------------------------------------------
    def _runtime_state(self) -> dict[str, Any]:
        return {"robot_regions": dict(CITY_ROBOT_REGIONS), "object_states": {}}

    def _settle_to_window(self, task: Mapping[str, Any]) -> float:
        """Advance the real simulation to the task's reserved window end."""
        ctx = self._ctx
        window_end = float(task["execution"]["start_s"]) + float(task["execution"]["duration_s"])
        while float(ctx["city"].elapsed) + float(ctx["dt"]) <= window_end:
            ctx["step"](ctx["session"], ctx["city"], ctx["actions"], 1)
        return window_end

    def _terminal(self, task: Mapping[str, Any], row: Mapping[str, Any], episode_id: str,
                  status: str, completed_at_s: float, evidence_refs: list[str],
                  receipt_ref: Any) -> dict[str, Any]:
        refs = [str(ref) for ref in dict.fromkeys(evidence_refs)]
        if not refs:
            refs = [f"{CITY_BACKEND_ID}:{task['task_id']}"]
        completed = float(completed_at_s)
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
        skill = task["skill"]
        if skill == "guide_people":
            return self._execute_guide(task, row, episode_id, float(ledger["current_time_s"]))
        if skill == "control_following_traffic":
            return self._execute_control(task, row, episode_id, float(ledger["current_time_s"]))
        if skill == "report_incident":
            return self._execute_report(task, row, episode_id, float(ledger["current_time_s"]))
        raise ValueError(f"native city backend does not implement skill: {skill}")

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
        observation = self._current_observation()
        ctx["role"](runner, "guidance_contact", ctx["session"], ctx["trace"])
        guidance = None
        for attempt in range(CITY_GUIDE_RETRY_LIMIT):
            try:
                guidance = runner.guide_people(tuple(person_ids), observation_ref=observation,
                                               assembly_region_id=region_id)
                break
            except ValueError as exc:
                if "not admitted" not in str(exc) or attempt == CITY_GUIDE_RETRY_LIMIT - 1:
                    raise
                ctx["step"](ctx["session"], city, ctx["actions"],
                            max(1, math.ceil(1.0 / float(ctx["dt"]))))
                ctx["role"](runner, "guidance_contact", ctx["session"], ctx["trace"])
                observation = self._current_observation()
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
        refs = [str(request["request_id"]),
                *(str(ref) for ref in contact.get("evidence_refs") or ())]
        if not acknowledged:
            return self._terminal(task, row, episode_id, "failed",
                                  max(float(city.elapsed), ledger_now), refs,
                                  request["request_id"])
        window_end = self._settle_to_window(task)
        if float(city.elapsed) > window_end + float(ctx["dt"]):
            return self._terminal(task, row, episode_id, "failed",
                                  max(float(city.elapsed), ledger_now), refs,
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
        ctx["role"](runner, "traffic_control_point", ctx["session"], ctx["trace"])
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
            if float(city.elapsed) <= window_end + float(ctx["dt"]):
                return self._terminal(task, row, episode_id, "succeeded", window_end, refs,
                                      request["request_id"])
        return self._terminal(task, row, episode_id, "failed",
                              max(float(city.elapsed), ledger_now), refs, request["request_id"])

    def _execute_report(self, task: Mapping[str, Any], row: Mapping[str, Any],
                        episode_id: str, ledger_now: float) -> dict[str, Any]:
        ctx = self._ctx
        runner, city = ctx["runner"], ctx["city"]
        observation = self._current_observation()
        ctx["role"](runner, "incident_observer", ctx["session"], ctx["trace"])
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
            if float(city.elapsed) <= window_end + float(ctx["dt"]):
                return self._terminal(task, row, episode_id, "succeeded", window_end, refs,
                                      request["request_id"])
        return self._terminal(task, row, episode_id, "failed",
                              max(float(city.elapsed), ledger_now), refs, request["request_id"])

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
                              ctx: Mapping[str, Any]) -> dict[str, Any]:
    """Assemble the acceptance envelope from trusted native readbacks only.

    Every boolean is derived from an admitted request, a confirmed contact, a
    recorded readback or a completed review; participant self-reports are never
    consulted.  Unmeasurable facts are omitted so ``evaluate_city_acceptance``
    fails closed on them, and this slice's missing assembly arrival is recorded
    explicitly as ``arrived_at_admitted_assembly=False``.
    """
    records = list(evidence_log)
    guides = [row for row in records if row.get("kind") == "guide"]
    controls = [row for row in records if row.get("kind") == "control"]
    reports = [row for row in records if row.get("kind") == "report"]
    observation = next((row for row in records
                        if row.get("kind") == "episode_observation"), None)
    first = guides[0] if guides else None
    delivered = (float(observation["delivered_at_s"])
                 if observation is not None else None)
    decision = (float(first["request"]["submitted_time_s"])
                if first is not None else None)
    admitted = first is not None and observation is not None

    def contact_acks(record: Mapping[str, Any]) -> dict[str, Any]:
        return {str(actor["id"]): actor for actor in
                (record.get("contact") or {}).get("actor_acks") or ()
                if isinstance(actor, Mapping) and actor.get("id") is not None}

    first_acks = contact_acks(first) if first is not None else {}
    first_ack_time = None
    if first is not None and first.get("contact", {}).get("ack_time_s") is not None:
        first_ack_time = float(first["contact"]["ack_time_s"])
    elif first_acks:
        first_ack_time = max(float(actor.get("ack_time_s")) for actor in first_acks.values()
                             if actor.get("ack_time_s") is not None)

    people: list[dict[str, Any]] = []
    arrivals = {row.get("person_id"): row for row in records
                if row.get("kind") == "arrival"}
    for person_id in sorted(ctx["roster_ids"]):
        record = next((row for row in guides if person_id in row.get("person_ids", ())), None)
        if record is None:
            people.append({"person_id": person_id})
            continue
        acks = contact_acks(record)
        arrival = arrivals.get(person_id) or {}
        row: dict[str, Any] = {
            "person_id": person_id,
            "observation_ref": record.get("observation_ref"),
            # The native admission gate accepted the evacuation route and the
            # assembly capacity when it accepted this request.
            "route_admitted": True,
            "guidance_request_time_s": float(record["request"]["submitted_time_s"]),
            "arrived_at_admitted_assembly": bool(arrival.get("arrived")),
        }
        if arrival.get("arrived"):
            arrival_time = arrival.get("arrival_time_s")
            if isinstance(arrival_time, (int, float)) and not isinstance(arrival_time, bool):
                row["arrival_time_s"] = float(arrival_time)
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
        row["guidance_acknowledged"] = bool(
            ack is not None
            and (record.get("contact") or {}).get("status") == "contact_confirmed")
        row["guidance_time_s"] = (float(ack["ack_time_s"])
                                  if ack is not None and ack.get("ack_time_s") is not None
                                  else None)
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
            "request_time_s": float(control["request"]["submitted_time_s"]),
            "ack_time_s": (float(ack_time)
                           if isinstance(ack_time, (int, float))
                           and not isinstance(ack_time, bool) else None),
        }
        if check_time is not None:
            row["trajectory_check_time_s"] = float(check_time)
        if readback:
            row["entered_exclusion_boundary"] = not bool(readback.get("no_hazard_ingress"))
        traffic.append(row)

    report = reports[0] if reports else None
    receipt = dict((report or {}).get("receipt") or {})
    received = receipt.get("received_time_s")
    access = dict((report or {}).get("access") or {})
    start = float(report["receipt_time_s"]) if report is not None else None
    end = float(report["access_end_time_s"]) if report is not None else None
    clear = access.get("measured_clear_throughout") is True
    return {
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
        "incident_report": {
            "observation_refs": [report["observation_ref"]] if report is not None else [],
            "receipt_received": bool(isinstance(received, (int, float))
                                     and not isinstance(received, bool)),
            "submit_time_s": (float(report["request"]["submitted_time_s"])
                              if report is not None else None),
            "receipt_time_s": (float(received) if isinstance(received, (int, float))
                               and not isinstance(received, bool) else None),
        },
        "response_access": {
            "continuous_access_check": bool(clear and start is not None and end is not None
                                            and end > start),
            "route_admitted": access.get("rescue_and_evacuation_routes_admitted") is True,
            "hazard_free_throughout": clear,
            "check_start_time_s": start,
            "check_end_time_s": end,
        },
    }


def await_assembly_arrivals(ctx: Mapping[str, Any],
                            evidence_log: list[Mapping[str, Any]]) -> None:
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
    controller, regions = service.people_controller, service.assembly_regions
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
    first_arrival: dict[str, float] = {}

    def poll_arrivals() -> bool:
        state = {}
        for person_id in roster:
            arrived = bool(controller.readback(person_id, regions)["arrived"])
            state[person_id] = arrived
            if arrived and person_id not in first_arrival:
                # Native-clock measurement: the moment this person's readback
                # first reports assembly arrival during the wait.
                first_arrival[person_id] = float(city.elapsed)
        return all(state.values())

    poll_arrivals()
    while float(city.elapsed) < horizon and not poll_arrivals():
        ctx["step"](ctx["session"], city, ctx["actions"])

    for row in guides:
        request_id = row["request"]["request_id"]
        region_id = row.get("assembly_region_id")
        for person_id in row.get("person_ids", ()):
            readback = controller.readback(person_id, regions)
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
                            ctx: Mapping[str, Any]) -> Callable[[str, Mapping[str, Any]],
                                                                dict[str, dict[str, Any]]]:
    """Wrap the City acceptance predicates as a FormalGoalEpisodeRunner evaluator."""

    def evaluate(episode_id: str, trusted_report: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
        del episode_id
        await_assembly_arrivals(ctx, evidence_log)
        envelope = build_acceptance_evidence(evidence_log, ctx)
        acceptance = evaluate_city_acceptance(
            envelope, default_deadline_policy(),
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
                     episode_binding: str | Path | None = None) -> dict[str, Any]:
    """Prepare the formal City probe and run the injected GoalSession episode."""
    from demo.city_benchmark.service_probe import execute_probe, prepare_probe

    root = Path(output_root).absolute()
    binding = Path(episode_binding) if episode_binding is not None \
        else DEFAULT_CITY_EPISODE_BINDING
    prepare_probe(root, device=device, episode_binding=binding)

    def driver(ctx: Mapping[str, Any]) -> None:
        episode_root = Path(ctx["report_path"]).parent / "goal_episode"
        package = build_goal_task_package("city", city_roster_profile="bound_native_v1")
        public = package["public_task"]
        plan = build_city_episode_plan(public)
        backend = CityNativeGoalBackend(ctx, plan)
        runner = FormalGoalEpisodeRunner(
            public, package["participant_api"], CityWitnessParticipant(plan), backend,
            output_root=episode_root,
            evaluator=city_business_evaluator(backend.evidence, ctx))
        result = runner.run(initial_state={"robot_regions": dict(CITY_ROBOT_REGIONS),
                                           "object_states": {}})
        source_sha256 = file_sha256(Path(__file__))
        # Report finalization first: an archival hiccup must never mask a
        # completed episode in the probe report.
        ctx["report"]["trace"]["goal_episode"] = {
            "episode_id": result["episode_id"], "output_root": str(episode_root),
            "error": result["error"], "turns": result["turns"],
            "skill_receipt_count": result["skill_receipt_count"],
            "backend": type(backend).__name__,
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

    return execute_probe(root, device=device, driver=driver)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--device", choices=("cpu", "cuda:0"), default="cpu")
    parser.add_argument("--episode-binding", type=Path, default=None,
                        help="authoring/private.json or standalone binding document "
                             f"(default: {DEFAULT_CITY_EPISODE_BINDING})")
    args = parser.parse_args(argv)
    report = run_city_episode(args.output_root, device=args.device,
                              episode_binding=args.episode_binding)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report.get("status") == "completed" else 1


__all__ = ["CityNativeGoalBackend", "CityWitnessParticipant", "build_acceptance_evidence",
           "build_city_episode_plan", "city_business_evaluator", "main", "run_city_episode"]


if __name__ == "__main__":
    raise SystemExit(main())
