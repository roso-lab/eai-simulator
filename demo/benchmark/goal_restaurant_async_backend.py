"""Native Restaurant async execution backend and minimal R1 native driver.

This module wraps the Stage53 native Restaurant fleet substrate
(:class:`demo.benchmark.goal_restaurant_fleet.RestaurantFleetController` - the
same full-eight tick owner the ``RestaurantExecutionPump`` drives) as a
``GoalExecutionBackend`` that declares the runner's opt-in async execution
capability:

* ``supports_async_execution`` is ``True``;
* ``execute`` dispatches real navigation work (``fleet.plan_route`` plus
  ``fleet.begin_navigation``) and returns an ``in_flight`` result without
  waiting for the arrival; observe/relay dispatch is accepted only when a
  native observation provider is attached, otherwise it is recorded as a
  platform-gap terminal instead of a fake success;
* ``advance_idle(target_s)`` performs the real full-fleet simulation ticks,
  classifies navigation terminals with the pump's semantics (route-chain
  continuation, measured region membership on arrival, deadline/stall
  failures), and pushes terminal receipts into ``completed_deliveries`` with
  the pump's dual-time shape - the receipt time is the immutable physical
  terminal time, and the backend archive additionally records when the runner
  drained the delivery;
* the fleet's own per-agent active-navigation mutex and the mirrored
  task-resource keys surface as :class:`ConcurrencyEvidenceRefused` subclasses
  recorded on the shared :class:`ConcurrencyProbeRecorder`.

The module also provides the minimal native driver used for the R1
native-async-concurrency validation (Stage63).  The driver runs one bounded
episode of the real :class:`FormalGoalEpisodeRunner` in ``execution_mode
="async"`` over a two-Carter parallel patrol plan, then performs a
component-level native fleet-mutex probe on the same live fleet, and archives
``concurrency_evidence`` through the repository authority
(:func:`build_concurrency_evidence_report`).

Integration honesty: the runner-level part is a full FormalGoalEpisodeRunner
integration; the fleet-mutex probe is a component-level native probe that
calls the same backend ``execute`` outside the runner loop, because the
GoalSession admission layer refuses a same-agent double dispatch before the
backend is reached and an invalid participant batch would fail the episode
closed.  Nothing here starts Isaac on import, and every archive keeps
``release``/``scoreable``/``execution_validated`` ``False``.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
import time
import traceback
from typing import Any, Mapping

from .goal_concurrency_evidence import (
    ConcurrencyEvidenceRefused,
    ConcurrencyProbeRecorder,
    PostTerminationExecutionRefused,
    ResourceConflictExecutionRefused,
    build_concurrency_evidence_report,
    task_resource_keys,
)
from .provenance import canonical_sha256


ROOT = Path(__file__).resolve().parents[2]
BACKEND_ID = "restaurant-native-async-backend"
TERMINAL_SCHEMA = "eai.restaurant.async_native_terminal"
REGION_SCHEMA = "eai.restaurant.async_backend_region_measurement"
GAP_SCHEMA = "eai.restaurant.async_backend_platform_gap"
NAVIGATION_SKILLS = frozenset({"ground_navigation", "aerial_navigation"})
_TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled"})
_DRIVER_SOURCE_FILES = (
    "demo/benchmark/goal_restaurant_async_backend.py",
    "demo/benchmark/goal_episode_runner.py",
    "demo/benchmark/goal_concurrency_evidence.py",
    "demo/benchmark/goal_restaurant_fleet.py",
    "demo/benchmark/goal_restaurant_execution_v2.py",
    "demo/benchmark/goal_restaurant_deployment_v2.py",
    "demo/benchmark/goal_restaurant_autonomous_v2.py",
    "demo/benchmark/goal_restaurant_native_state.py",
)


def _finite_s(value: Any, label: str) -> float:
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value < 0):
        raise ValueError(f"{label} must be a finite nonnegative number")
    return float(value)


def _sealed(row: Mapping[str, Any]) -> dict[str, Any]:
    result = deepcopy(dict(row))
    result["evidence_ref"] = "sha256:" + canonical_sha256(result)
    return result


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2,
                               sort_keys=True, allow_nan=False) + "\n",
                    encoding="utf-8")


class RestaurantAsyncExecutionBackend:
    """Async-capable ``GoalExecutionBackend`` over one live native fleet.

    Parameters
    ----------
    fleet:
        Duck-typed ``RestaurantFleetController`` protocol: ``time_s``,
        ``control_dt_s``, ``snapshot()``, ``step_once()``, ``plan_route``,
        ``begin_navigation``, ``cancel_navigation``.  Unit tests substitute a
        deterministic fake; the native driver passes the real controller.
    public_task:
        The derived Restaurant public task; its route table feeds both the
        fleet dispatch and the mirrored resource-key derivation.
    deployment:
        The ``metric_regions_v2`` companion; its region bindings are the only
        geometry used for measured region membership.
    episode_id / recorder:
        Episode identity and the shared concurrency evidence authority.  The
        recorder ``backend_id`` must match this backend.
    observation_provider:
        Optional native ground-readiness provider.  Without it an ``observe``
        dispatch is a recorded platform-gap failure, never a fabricated
        success.
    """

    def __init__(self, *, fleet: Any, public_task: Mapping[str, Any],
                 deployment: Mapping[str, Any], episode_id: str,
                 recorder: ConcurrencyProbeRecorder,
                 observation_provider: Any = None,
                 backend_id: str = BACKEND_ID,
                 max_idle_ticks: int = 200_000) -> None:
        if not isinstance(recorder, ConcurrencyProbeRecorder):
            raise ValueError("recorder must be a ConcurrencyProbeRecorder")
        if recorder.backend_id != backend_id:
            raise ValueError("recorder backend_id must match the async backend")
        if not isinstance(episode_id, str) or not episode_id.strip():
            raise ValueError("episode_id must be a nonempty string")
        if (isinstance(max_idle_ticks, bool) or not isinstance(max_idle_ticks, int)
                or max_idle_ticks < 1):
            raise ValueError("max_idle_ticks must be a positive integer")
        self._fleet = fleet
        self._episode_id = episode_id
        self._recorder = recorder
        self._backend_id = backend_id
        self._observation_provider = observation_provider
        self._max_idle_ticks = max_idle_ticks
        self._routes = {row["id"]: row for row in public_task["public_world"]["routes"]}
        self._region_bindings = deepcopy(
            dict(deployment["region_bindings"]))
        self._spawn_regions = deepcopy(dict(deployment["spawn_regions"]))
        self._mobility = {row["id"]: row["mobility"]
                          for row in public_task["candidate_robot_pool"]}
        self._in_flight: dict[str, dict[str, Any]] = {}
        self._dispatch_actions: dict[str, str | None] = {}
        self._execution_sequence = 0
        self._result_sequence = 0
        self._last_robot_regions: dict[str, str | None] = {}
        self._terminal_records: list[dict[str, Any]] = []
        self._platform_gaps: list[dict[str, Any]] = []
        self._region_records: list[dict[str, Any]] = []
        self._delivery_journal: list[dict[str, Any]] = []
        self._idle_ticks = 0
        self.completed_deliveries: list[dict[str, Any]] = []
        self.execute_calls: list[tuple[str, float]] = []
        self.cancel_calls: list[str] = []
        self.idle_calls: list[float] = []

    # ------------------------------------------------------------------
    # Async capability declaration
    # ------------------------------------------------------------------
    @property
    def supports_async_execution(self) -> bool:
        return True

    def in_flight_task_ids(self) -> list[str]:
        return sorted(self._in_flight)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _session_now(self) -> float:
        return float(self._fleet.time_s)

    def _session_task_row(self, trusted_report: Mapping[str, Any],
                          task_id: str) -> dict[str, Any] | None:
        session = trusted_report.get("session", trusted_report)
        row = session.get("tasks", {}).get(task_id)
        return deepcopy(row) if isinstance(row, Mapping) else None

    def _terminated(self, trusted_report: Mapping[str, Any]) -> bool:
        session = trusted_report.get("session", trusted_report)
        assessment = session.get("deployment_assessment") or {}
        return bool(assessment.get("terminated"))

    def _measure_regions(self, snapshot: Mapping[str, Any]) -> dict[str, Any]:
        """Measured membership against companion geometry, tolerance only.

        This mirrors the pump's ``maintained_geometry_and_tolerance_only``
        rule set: an agent with an active navigation operation is reported
        region-less while it moves, the latest arrived operation target wins
        when its disk/ball actually measures inside, the previous region is
        kept while it still measures inside, and otherwise a unique match is
        required.  It never treats a commanded goal as an arrival.
        """
        operations = list(snapshot.get("operations", {}).values())
        mapped: dict[str, str] = {}
        details: dict[str, Any] = {}
        for public_id, sample in snapshot["agents"].items():
            mobility = self._mobility.get(public_id)
            if mobility is None:
                continue
            matches: list[dict[str, Any]] = []
            for region_id, binding in self._region_bindings.items():
                if binding.get("geometry_status") != "defined":
                    continue
                center = binding.get("center_xyz_m", binding.get("center_xy_m"))
                if center is None:
                    continue
                modes = set(binding.get("access_modes", []))
                if mobility == "aerial" and "aerial_navigation" not in modes:
                    continue
                if (mobility == "ground"
                        and not modes.intersection({"ground_navigation", "stair_traversal"})):
                    continue
                distance = math.dist(sample["position_m"][:len(center)], center)
                if distance <= float(binding["tolerance_m"]):
                    matches.append({"region_id": region_id, "distance_m": distance})
            matched_ids = {row["region_id"] for row in matches}
            actively_moving = any(
                operation.get("public_agent_id") == public_id
                and operation.get("status") == "active"
                for operation in operations)
            arrived_targets = [
                operation.get("goal_binding", {}).get("region_id")
                for operation in operations
                if (operation.get("public_agent_id") == public_id
                    and operation.get("status") == "arrived")]
            previous = self._last_robot_regions.get(public_id)
            if actively_moving:
                selected, basis = None, "active_navigation_region_omitted"
            elif (arrived_targets and arrived_targets[-1] in matched_ids):
                selected = arrived_targets[-1]
                basis = "arrived_operation_target_measured_inside"
            elif previous in matched_ids:
                selected, basis = previous, "previous_region_still_measured_inside"
            elif len(matched_ids) == 1:
                selected, basis = matched_ids.pop(), "unique_measured_membership"
            else:
                selected, basis = None, "no_or_ambiguous_measured_membership"
            details[public_id] = {
                "matches": matches,
                "selected_region_id": selected,
                "selection_basis": basis,
            }
            if selected is not None:
                mapped[public_id] = selected
                self._last_robot_regions[public_id] = selected
            elif previous is not None:
                # Keep the last measured membership for stability while the
                # agent moves; the pump reports the omission the same way.
                self._last_robot_regions[public_id] = None
        row = _sealed({
            "schema": REGION_SCHEMA, "version": 1,
            "episode_id": self._episode_id,
            "physics_step": snapshot["physics_step"],
            "time_s": float(snapshot["time_s"]),
            "robot_regions": mapped,
            "agents": details,
            "membership_rule": "maintained_geometry_and_tolerance_only",
        })
        self._region_records.append(row)
        return row

    def _initial_robot_regions(self, snapshot: Mapping[str, Any]) -> dict[str, str]:
        """Designated post-warmup spawn membership, like the native state gate.

        Generic membership cannot disambiguate the co-located staging center
        and the ``restaurant_spawn_carter_1`` disk; the deployment companion's
        designated spawn mapping is the maintained initial authority.
        """
        mapped: dict[str, str] = {}
        for public_id, spawn_region in self._spawn_regions.items():
            sample = snapshot["agents"].get(public_id)
            binding = self._region_bindings.get(spawn_region, {})
            center = binding.get("center_xyz_m", binding.get("center_xy_m"))
            if (sample is None or center is None
                    or binding.get("geometry_status") != "defined"):
                continue
            distance = math.dist(sample["position_m"][:len(center)], center)
            if distance <= float(binding["tolerance_m"]):
                mapped[public_id] = spawn_region
                self._last_robot_regions[public_id] = spawn_region
        return mapped

    def _runtime_state(self, region_record: Mapping[str, Any]) -> dict[str, Any]:
        return {"robot_regions": deepcopy(dict(region_record["robot_regions"])),
                "object_states": {}}

    def _record_gap(self, task_id: str, now: float, reason: str,
                    **data: Any) -> dict[str, Any]:
        row = _sealed({
            "schema": GAP_SCHEMA, "version": 1,
            "episode_id": self._episode_id, "task_id": task_id,
            "time_s": now, "reason": reason, **deepcopy(data),
        })
        self._platform_gaps.append(row)
        return row

    def _agent_holder(self, task: Mapping[str, Any]) -> tuple[str, str] | None:
        agents = set(task["assigned_agents"])
        for task_id, row in self._in_flight.items():
            if agents.intersection(row["task"]["assigned_agents"]):
                return task_id, sorted(agents.intersection(
                    row["task"]["assigned_agents"]))[0]
        return None

    def _planned_end(self, task: Mapping[str, Any], *, now: float) -> float:
        execution = task["execution"]
        window_end = (_finite_s(execution["start_s"], "task start_s")
                      + _finite_s(execution["duration_s"], "task duration_s"))
        return min(now + _finite_s(execution["duration_s"], "task duration_s"),
                   window_end)

    def _begin_navigation_leg(self, task_id: str) -> dict[str, Any]:
        row = self._in_flight[task_id]
        task = row["task"]
        routes = task["arguments"].get("route_ids", [])
        if not routes:
            raise ValueError("navigation task has no public route chain")
        route_id = routes[row["route_index"]]
        declared = self._routes.get(route_id)
        if declared is None:
            raise ValueError("navigation route is absent from public topology")
        agent = task["assigned_agents"][0]
        planned = self._fleet.plan_route(agent, route_id, declared["to_region_id"])
        self._execution_sequence += 1
        operation_id = (f"{self._episode_id}:x{self._execution_sequence}:"
                        f"{task_id}:leg{row['route_index']}")
        operation = self._fleet.begin_navigation(
            operation_id, planned, deadline_s=row["window_end_s"])
        row["operation_ids"].append(operation_id)
        if row["physical_started_at_s"] is None:
            row["physical_started_at_s"] = float(operation["started_at_s"])
        return operation

    def _build_receipt(self, task_id: str, *, status: str,
                       completed_at_s: float) -> dict[str, Any]:
        return {
            "backend_id": self._backend_id,
            "receipt_ref": (f"{self._episode_id}/{task_id}/async/"
                            f"{self._result_sequence}"),
            "task_id": task_id,
            "action_id": self._dispatch_actions.get(task_id),
            "status": status,
            "completed_at_s": completed_at_s,
            "episode_id": self._episode_id,
            "evidence_refs": [],
        }

    def _stage_terminal(self, task_id: str, *, status: str, now: float,
                        snapshot: Mapping[str, Any],
                        region_record: Mapping[str, Any],
                        evidence: list[Mapping[str, Any]],
                        disposition: str) -> dict[str, Any]:
        """Seal one native terminal and stage it for the runner to commit.

        The receipt time is the immutable physical terminal time (the pump's
        ``physical_completed_at_s``); the runner commits it later at its own
        drain boundary, and the journal row keeps both instants.
        """
        if status not in _TERMINAL_STATUSES:
            raise ValueError("native terminal requires a terminal status")
        row = self._in_flight.pop(task_id)
        self._result_sequence += 1
        terminal = _sealed({
            "schema": TERMINAL_SCHEMA, "version": 1,
            "episode_id": self._episode_id,
            "task_id": task_id,
            "dispatch_action_id": self._dispatch_actions.get(task_id),
            "status": status,
            "physical_terminal_at_s": now,
            "disposition": disposition,
            "task": deepcopy(row["task"]),
            "operations": [deepcopy(dict(item)) for item in evidence],
            "fleet_snapshot_sha256": canonical_sha256(snapshot),
            "region_record_sha256": canonical_sha256(region_record),
            "execution_validated": False, "scoreable": False,
        })
        self._terminal_records.append(terminal)
        receipt = self._build_receipt(task_id, status=status, completed_at_s=now)
        receipt["evidence_refs"] = list(dict.fromkeys(
            [terminal["evidence_ref"]]
            + [item["evidence_ref"] for item in evidence
               if isinstance(item, Mapping) and item.get("evidence_ref")]))
        self._recorder.note_execution_terminal(
            task_id, end_s=now, status=status,
            receipt_ref=receipt["receipt_ref"])
        delivery = {
            "status": status,
            "completed_at_s": now,
            "receipt": receipt,
            "runtime_state": self._runtime_state(region_record),
            "physical_completed_at_s": now,
            "staged_at_s": float(self._fleet.time_s),
            "disposition": disposition,
        }
        self.completed_deliveries.append(delivery)
        self._delivery_journal.append({
            "task_id": task_id, "status": status,
            "physical_completed_at_s": now,
            "staged_at_s": delivery["staged_at_s"],
            "drained": False, "drained_at_s": None,
            "receipt_ref": receipt["receipt_ref"],
            "disposition": disposition,
        })
        return delivery

    def _fail_with_gap(self, task_id: str, *, now: float,
                       snapshot: Mapping[str, Any], reason: str,
                       disposition: str, **data: Any) -> dict[str, Any]:
        gap = self._record_gap(task_id, now, reason, **data)
        region_record = self._measure_regions(snapshot)
        return self._stage_terminal(
            task_id, status="failed", now=now, snapshot=snapshot,
            region_record=region_record, evidence=[gap],
            disposition=disposition)

    def _start_execution_row(self, task: Mapping[str, Any], *,
                             now: float) -> dict[str, Any]:
        task_id = task["task_id"]
        if task_id in self._in_flight:
            raise ValueError(f"task {task_id} is already in flight")
        resources = task_resource_keys(task, routes=self._routes)
        row = {
            "task": deepcopy(dict(task)),
            "resources": resources,
            "planned_end_s": self._planned_end(task, now=now),
            "window_end_s": (_finite_s(task["execution"]["start_s"], "task start_s")
                             + _finite_s(task["execution"]["duration_s"],
                                         "task duration_s")),
            "physical_started_at_s": None,
            "route_index": 0,
            "operation_ids": [],
        }
        self._in_flight[task_id] = row
        return row

    def _note_start(self, task: Mapping[str, Any], row: Mapping[str, Any],
                    *, now: float, mode: str) -> None:
        self._recorder.note_execution_start(
            task["task_id"], skill=task["skill"], start_s=now,
            planned_end_s=row["planned_end_s"],
            resources=list(row["resources"]), mode=mode)

    # ------------------------------------------------------------------
    # GoalExecutionBackend protocol
    # ------------------------------------------------------------------
    def execute(self, task: Mapping[str, Any], *, episode_id: str,
                trusted_report: Mapping[str, Any]) -> dict[str, Any]:
        if episode_id != self._episode_id:
            raise ValueError("async backend received a foreign episode_id")
        task_id = task["task_id"]
        now = self._session_now()
        session_row = self._session_task_row(trusted_report, task_id)
        if session_row is not None and session_row.get("dispatch_action_id"):
            self._dispatch_actions[task_id] = session_row["dispatch_action_id"]
        self.execute_calls.append((task_id, now))
        if self._terminated(trusted_report):
            self._recorder.note_post_termination_attempt(
                task_id, at_s=now, operation="execute",
                refusal="PostTerminationExecutionRefused")
            raise PostTerminationExecutionRefused(
                "deployment assessment terminated the episode; refusing execute")
        skill = task["skill"]
        if task_id in self._in_flight:
            raise ValueError(f"task {task_id} is already in flight")
        snapshot = self._fleet.snapshot()
        now = float(snapshot["time_s"])
        if skill in NAVIGATION_SKILLS:
            row = self._start_execution_row(task, now=now)
            try:
                self._begin_navigation_leg(task_id)
            except ValueError as exc:
                if "already has active navigation" in str(exc):
                    self._in_flight.pop(task_id, None)
                    holder = self._agent_holder(task)
                    held_by, agent = holder if holder else (None, None)
                    agent = agent or task["assigned_agents"][0]
                    self._recorder.note_resource_conflict(
                        task_id, resource_key=f"agent:{agent}",
                        held_by=held_by, at_s=now, layer="native_fleet_mutex",
                        detail=("native fleet refused a second active "
                                f"navigation for {agent}"))
                    raise ResourceConflictExecutionRefused(
                        f"native fleet agent {agent} already has an active "
                        f"navigation (held by {held_by})") from exc
                self._note_start(task, row, now=now, mode="native_in_flight")
                return self._fail_with_gap(
                    task_id, now=now, snapshot=snapshot,
                    reason="native_start_failure",
                    disposition="native_start_failure",
                    exception_type=type(exc).__name__,
                    exception_message=str(exc))
            except Exception as exc:  # platform gap, never a fake success
                self._note_start(task, row, now=now, mode="native_in_flight")
                return self._fail_with_gap(
                    task_id, now=now, snapshot=snapshot,
                    reason="native_start_failure",
                    disposition="native_start_failure",
                    exception_type=type(exc).__name__,
                    exception_message=str(exc))
            self._note_start(task, row, now=now, mode="native_in_flight")
            return {
                "status": "in_flight", "task_id": task_id,
                "started_at_s": now,
                "planned_end_s": row["planned_end_s"],
                "resources": list(row["resources"]),
                "backend_id": self._backend_id,
            }
        if skill == "observe":
            row = self._start_execution_row(task, now=now)
            self._note_start(task, row, now=now, mode="native_immediate")
            region = task["arguments"].get("region_id")
            mode = task["arguments"].get("observation_mode")
            if (self._observation_provider is None
                    or mode != "service_ground_readiness"):
                return self._fail_with_gap(
                    task_id, now=now, snapshot=snapshot,
                    reason=("unsupported_observation_mode" if mode !=
                            "service_ground_readiness"
                            else "native_observation_provider_unavailable"),
                    disposition="platform_gap", observation_mode=mode,
                    region_id=region)
            producer = task["assigned_agents"][0]
            try:
                scene = self._observation_provider.ground_readiness(producer, region)
            except Exception as exc:
                return self._fail_with_gap(
                    task_id, now=now, snapshot=snapshot,
                    reason="native_observation_unavailable",
                    disposition="native_observation_failure",
                    exception_type=type(exc).__name__,
                    exception_message=str(exc))
            native = _sealed({
                "kind": "native_ground_readiness_observation",
                "task_id": task_id, "producer_id": producer,
                "region_id": region,
                "scene_observation_validated": bool(
                    scene.get("scene_observation_validated")),
                "measured_at_s": now,
            })
            region_record = self._measure_regions(snapshot)
            return self._stage_terminal(
                task_id, status="succeeded", now=now, snapshot=snapshot,
                region_record=region_record, evidence=[native],
                disposition="native_ground_readiness_observation")
        if skill == "relay":
            row = self._start_execution_row(task, now=now)
            self._note_start(task, row, now=now, mode="native_immediate")
            return self._fail_with_gap(
                task_id, now=now, snapshot=snapshot,
                reason="relay_transport_not_wired_into_async_backend",
                disposition="platform_gap")
        row = self._start_execution_row(task, now=now)
        self._note_start(task, row, now=now, mode="native_immediate")
        return self._fail_with_gap(
            task_id, now=now, snapshot=snapshot,
            reason="unsupported_restaurant_skill", disposition="platform_gap",
            skill=skill)

    def cancel(self, task: Mapping[str, Any], *, episode_id: str,
               trusted_report: Mapping[str, Any]) -> dict[str, Any]:
        if episode_id != self._episode_id:
            raise ValueError("async backend received a foreign episode_id")
        task_id = task["task_id"]
        self.cancel_calls.append(task_id)
        session_row = self._session_task_row(trusted_report, task_id)
        if session_row is not None and session_row.get("dispatch_action_id"):
            self._dispatch_actions[task_id] = session_row["dispatch_action_id"]
        snapshot = self._fleet.snapshot()
        now = float(snapshot["time_s"])
        in_flight = task_id in self._in_flight
        safety_stop = bool(session_row is not None and session_row.get("safety_stop"))
        terminated = self._terminated(trusted_report)
        if terminated:
            cause, outcome = ("post_termination_safety_stop",
                              "safety_stop_after_termination")
        elif safety_stop:
            cause = "safety_stop_after_invalidation"
            outcome = ("safety_stop_while_in_flight" if in_flight
                       else "safety_stop_pre_execution")
        else:
            cause = "participant_cancel"
            outcome = ("cancel_arrived_first_while_in_flight" if in_flight
                       else "pre_execution_withdrawal")
        self._recorder.note_cancel_observation(
            task_id, at_s=now, in_flight=in_flight, outcome=outcome,
            cause=cause, extra={"terminated": terminated})
        if not in_flight:
            receipt = self._build_receipt(task_id, status="cancelled",
                                          completed_at_s=now)
            return {"status": "cancelled", "completed_at_s": now,
                    "receipt": receipt,
                    "runtime_state": self._runtime_state(
                        self._measure_regions(snapshot))}
        row = self._in_flight[task_id]
        evidence: list[Mapping[str, Any]] = []
        if row["operation_ids"]:
            evidence.append(self._fleet.cancel_navigation(row["operation_ids"][-1]))
        region_record = self._measure_regions(snapshot)
        return self._stage_terminal(
            task_id, status="cancelled", now=now, snapshot=snapshot,
            region_record=region_record, evidence=evidence,
            disposition="backend_cancel_physical_stop")

    def advance_idle(self, target_s: float, *,
                     trusted_report: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Advance the real native fleet clock toward ``target_s``.

        Each iteration is one full-fleet control tick.  The pump stops at the
        earliest new native terminal so the runner can commit that receipt at
        its own physical time before the clock advances further; it also stops
        when no execution is in flight or the bounded tick guard trips.
        """
        target = _finite_s(target_s, "idle target_s")
        before_clock = self._recorder.clock_s
        self.idle_calls.append(target)
        snapshot = self._fleet.snapshot()
        ticks = 0
        while (self._in_flight
               and float(snapshot["time_s"]) < target - 1e-9
               and ticks < self._max_idle_ticks):
            snapshot = self._fleet.step_once()
            ticks += 1
            region_record = self._measure_regions(snapshot)
            terminals = self._poll_navigation(snapshot, region_record)
            if terminals:
                break
        self._idle_ticks += ticks
        reached = float(self._fleet.snapshot()["time_s"])
        if reached < before_clock:
            raise RuntimeError("native fleet clock moved backwards")
        self._recorder.note_idle_advance(from_s=before_clock, to_s=reached)
        return {
            "time_s": reached,
            "runtime_state": self._runtime_state(
                self._measure_regions(self._fleet.snapshot())),
            "idle_ticks": ticks,
            "stopped_early": bool(self._in_flight) and reached < target - 1e-9,
        }

    def _poll_navigation(self, snapshot: Mapping[str, Any],
                         region_record: Mapping[str, Any]) -> list[str]:
        """Classify native navigation terminals with the pump's semantics."""
        terminals: list[str] = []
        for task_id in sorted(list(self._in_flight)):
            row = self._in_flight[task_id]
            if row["task"]["skill"] not in NAVIGATION_SKILLS:
                continue
            if not row["operation_ids"]:
                continue
            operation = snapshot["operations"].get(row["operation_ids"][-1])
            if operation is None or operation.get("status") == "active":
                continue
            now = float(snapshot["time_s"])
            if operation["status"] == "arrived":
                routes = row["task"]["arguments"]["route_ids"]
                if row["route_index"] + 1 < len(routes):
                    row["route_index"] += 1
                    try:
                        self._begin_navigation_leg(task_id)
                    except Exception as exc:
                        self._fail_with_gap(
                            task_id, now=now, snapshot=snapshot,
                            reason="route_chain_continuation_failure",
                            disposition="route_chain_failure",
                            exception_type=type(exc).__name__,
                            exception_message=str(exc))
                        terminals.append(task_id)
                    continue
                expected = row["task"]["arguments"]["region_id"]
                measured = region_record["robot_regions"].get(
                    row["task"]["assigned_agents"][0])
                if measured != expected:
                    self._fail_with_gap(
                        task_id, now=now, snapshot=snapshot,
                        reason="arrival_outside_region_membership",
                        disposition="measured_region_membership_failure",
                        expected_region_id=expected,
                        measured_region_id=measured)
                else:
                    self._stage_terminal(
                        task_id, status="succeeded", now=now, snapshot=snapshot,
                        region_record=region_record, evidence=[operation],
                        disposition="native_navigation_arrival")
            else:
                self._stage_terminal(
                    task_id, status=("cancelled" if operation["status"] == "cancelled"
                                     else "failed"),
                    now=now, snapshot=snapshot, region_record=region_record,
                    evidence=[operation],
                    disposition=operation.get("terminal_reason"))
            terminals.append(task_id)
        return terminals

    # ------------------------------------------------------------------
    # Archives
    # ------------------------------------------------------------------
    def mark_deliveries_drained(self, drained_at_s: float) -> None:
        """Record the runner boundary for the dual-time journal.

        The runner clears :attr:`completed_deliveries` when it commits each
        receipt, so the journal keeps the immutable physical terminal times
        and stamps the runner-side drain boundary with the runner session's
        closing clock (an upper bound for the individual commit instants).
        """
        for row in self._delivery_journal:
            if not row["drained"]:
                row["drained"] = True
                row["drained_at_s"] = float(drained_at_s)

    def report(self) -> dict[str, Any]:
        return {
            "schema": "eai.restaurant.async_backend_archive", "version": 1,
            "episode_id": self._episode_id,
            "backend_id": self._backend_id,
            "supports_async_execution": True,
            "execution_counts": {
                "in_flight_open": len(self._in_flight),
                "terminal_records": len(self._terminal_records),
                "platform_gaps": len(self._platform_gaps),
            },
            "in_flight_open": sorted(self._in_flight),
            "terminal_records": [deepcopy(row) for row in self._terminal_records],
            "platform_gaps": [deepcopy(row) for row in self._platform_gaps],
            "delivery_journal": [deepcopy(row) for row in self._delivery_journal],
            "undelivered_staged_receipts": [
                deepcopy(row["receipt"]) for row in self.completed_deliveries],
            "idle_ticks": self._idle_ticks,
            "idle_calls": list(self.idle_calls),
            "execute_calls": list(self.execute_calls),
            "cancel_calls": list(self.cancel_calls),
            "region_records": len(self._region_records),
            "region_measurement_rule": "maintained_geometry_and_tolerance_only",
            "observation_provider_attached": self._observation_provider is not None,
            "execution_validated": False,
            "business_outcomes_validated": False,
            "scoreable": False,
            "release_ready": False,
        }


# ----------------------------------------------------------------------
# Minimal native driver (Stage63 R1 native async concurrency validation)
# ----------------------------------------------------------------------
def build_parallel_patrol_plan(*, window_s: float) -> dict[str, Any]:
    """Two-Carter parallel patrol: spawn anchors to distinct service zones."""
    tasks = [
        {
            "task_id": "nav-kitchen", "skill": "ground_navigation",
            "arguments": {"region_id": "kitchen_service", "route_ids": [
                "restaurant_spawn_carter_1_to_kitchen_service"]},
            "required_capabilities": ["ground_navigation"],
            "assigned_agents": ["restaurant_carter_1"],
            "prerequisites": [], "evidence_refs": [],
            "execution": {"start_s": 0.0, "duration_s": float(window_s)},
        },
        {
            "task_id": "nav-dining", "skill": "ground_navigation",
            "arguments": {"region_id": "dining_service", "route_ids": [
                "restaurant_spawn_carter_2_to_dining_service"]},
            "required_capabilities": ["ground_navigation"],
            "assigned_agents": ["restaurant_carter_2"],
            "prerequisites": [], "evidence_refs": [],
            "execution": {"start_s": 0.0, "duration_s": float(window_s)},
        },
    ]
    return {
        "schema": "eai.benchmark.goal_plan", "version": 3,
        "plan_id": "native-async-parallel-patrol",
        "assigned_team": ["restaurant_carter_1", "restaurant_carter_2"],
        "tasks": tasks,
    }


class NativeAsyncPairAdapter:
    """Public diagnostic participant: deploy, then dispatch the pair at t=0."""

    def __init__(self, plan: Mapping[str, Any]):
        self.plan = deepcopy(dict(plan))
        self.task_ids = [row["task_id"] for row in self.plan["tasks"]]
        self.events: list[dict[str, Any]] = []
        self.actions: list[dict[str, Any]] = []
        self.assessment_sent = False
        self.setup_sent = False
        self.closed = False

    def manifest(self) -> dict[str, Any]:
        return {"kind": "participant", "id": "native-async-pair-diagnostic",
                "version": 1, "interface": "eai.benchmark.goal_algorithm_adapter",
                "interface_version": 1, "evaluation_track": "goal_driven",
                "input_scope": "participant_visible_events_only",
                "control_scope": "benchmark_managed_low_level_control"}

    def initialize(self, public_task: Mapping[str, Any],
                   participant_api: Mapping[str, Any]) -> None:
        return None

    def _action(self, action_id: str, action_type: str,
                payload: dict[str, Any], now: float) -> dict[str, Any]:
        action = {"schema": "eai.benchmark.goal_action", "version": 1,
                  "action_id": action_id, "submitted_at_s": now,
                  "type": action_type, "payload": payload}
        self.actions.append(deepcopy(action))
        return action

    def act(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        self.events.extend(deepcopy(events))
        now = max((row["time_s"] for row in events), default=0.0)
        actions: list[dict[str, Any]] = []
        for event in events:
            payload = event["payload"]
            if event["type"] == "deployment_assessment_required" and not self.assessment_sent:
                self.assessment_sent = True
                actions.append(self._action(
                    "assessment-0", "deployment_assessment",
                    {**{key: payload[key] for key in (
                        "assessment_id", "assessment_epoch", "roster_version",
                        "capability_versions")},
                     "decision": "deployable", "reason_codes": [],
                     "evidence_refs": []}, now))
        if self.assessment_sent and not self.setup_sent:
            self.setup_sent = True
            actions.append(self._action(
                "team", "select_team",
                {"agent_ids": self.plan["assigned_team"]}, now))
            actions.append(self._action(
                "plan", "submit_plan", {"plan": self.plan}, now))
            for index, task_id in enumerate(self.task_ids):
                actions.append(self._action(
                    f"dispatch-{task_id}-{index}", "dispatch_skill",
                    {"task_id": task_id, "evidence_refs": []}, now))
        return actions

    def close(self) -> None:
        self.closed = True


def _driver_request(args: argparse.Namespace, plan: Mapping[str, Any],
                    selection: Mapping[str, Any]) -> dict[str, Any]:
    sources = {}
    for name in _DRIVER_SOURCE_FILES:
        path = ROOT / name
        sources[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "schema": "eai.restaurant.native_async_concurrency_run", "version": 1,
        "scope": "r1_native_async_concurrency_diagnostic_not_release",
        "episode_id": args.episode_id,
        "device": args.device, "seed": args.seed,
        "warmup_ticks": args.warmup_ticks,
        "navigation_window_s": float(args.nav_window_s),
        "probe_window_s": float(args.probe_window_s),
        "products_root": str(args.products_root.resolve()),
        "source_sha256": sources,
        "plan": deepcopy(dict(plan)),
        "selection": deepcopy(dict(selection)),
        "native_mutex_probe": bool(args.probe),
        "execution_validated": False, "scoreable": False,
        "release_ready": False,
    }


def _probe_mutex_on_live_fleet(backend: RestaurantAsyncExecutionBackend,
                               *, window_s: float,
                               trusted_report: Mapping[str, Any]) -> dict[str, Any]:
    """Component-level native fleet-mutex probe on the live fleet.

    The holder leg is a real native navigation; the refused leg would reuse
    the same agent and is refused by the fleet's active-navigation mutex.  The
    refusal is recorded on the shared recorder with layer
    ``native_fleet_mutex``; the holder leg is then driven to a real arrival so
    its interval closes with a terminal receipt.
    """
    now = float(backend._fleet.time_s)
    holder = {
        "task_id": "probe-mutex-holder", "skill": "ground_navigation",
        "arguments": {"region_id": "restaurant_staging", "route_ids": [
            "restaurant_kitchen_to_staging"]},
        "required_capabilities": ["ground_navigation"],
        "assigned_agents": ["restaurant_carter_1"],
        "prerequisites": [], "evidence_refs": [],
        "execution": {"start_s": now, "duration_s": float(window_s)},
    }
    refused = {
        "task_id": "probe-mutex-refused", "skill": "ground_navigation",
        "arguments": {"region_id": "restaurant_spawn_carter_1", "route_ids": [
            "restaurant_kitchen_service_to_spawn_carter_1"]},
        "required_capabilities": ["ground_navigation"],
        "assigned_agents": ["restaurant_carter_1"],
        "prerequisites": [], "evidence_refs": [],
        "execution": {"start_s": now, "duration_s": float(window_s)},
    }
    started = backend.execute(
        holder, episode_id=backend._episode_id, trusted_report=trusted_report)
    refusal: dict[str, Any] | None = None
    try:
        backend.execute(refused, episode_id=backend._episode_id,
                        trusted_report=trusted_report)
        refusal = {"refused": False,
                   "note": "native fleet accepted the conflicting dispatch; "
                           "the mutex probe did not trigger"}
    except ConcurrencyEvidenceRefused as exc:
        refusal = {"refused": True, "type": type(exc).__name__, "error": str(exc)}
    terminal = None
    ticks = 0
    while backend.in_flight_task_ids() and ticks < 20_000:
        result = backend.advance_idle(
            float(backend._fleet.time_s) + float(window_s))
        ticks += result["idle_ticks"]
        if result["idle_ticks"] == 0:
            break
    if not backend.in_flight_task_ids():
        delivery = next((row for row in backend.completed_deliveries
                         if row["receipt"]["task_id"] == "probe-mutex-holder"),
                        None)
        terminal = (None if delivery is None else {
            "status": delivery["status"],
            "physical_completed_at_s": delivery["physical_completed_at_s"],
            "receipt_ref": delivery["receipt"]["receipt_ref"],
            "disposition": delivery["disposition"]})
    return {
        "schema": "eai.restaurant.native_mutex_probe", "version": 1,
        "episode_id": backend._episode_id,
        "integration_level": "component_native_probe_on_live_fleet",
        "started_at_s": now,
        "holder": {"task_id": holder["task_id"],
                   "started": started.get("status") == "in_flight",
                   "planned_end_s": started.get("planned_end_s")},
        "refusal": refusal,
        "holder_terminal": terminal,
        "idle_ticks": ticks,
        "reason": ("GoalSession admission refuses a same-agent double dispatch "
                   "before the backend, so this native constraint is probed at "
                   "component level on the same live fleet and recorder"),
        "execution_validated": False, "scoreable": False, "release_ready": False,
    }


def run_native(args: argparse.Namespace, request: Mapping[str, Any]) -> dict[str, Any]:
    from .goal_episode_runner import FormalGoalEpisodeRunner
    from .goal_restaurant_deployment_v2 import DEPLOYMENT_PROFILE
    from .goal_restaurant_fleet import RestaurantFleetController

    os.environ["EAI_RESTAURANT_BENCHMARK_ROOT"] = request["products_root"]
    from simulator import SimulatorLaunchConfig, open_simulator_session

    def configure(env_cfg: Any) -> None:
        env_cfg.episode_length_s = 3600.0
        env_cfg.sim.enable_scene_query_support = True

    selection = request["selection"]
    config = SimulatorLaunchConfig(
        env=selection["task_name"], resolved_env_name="EAI-Interactive-v0",
        selection_data=selection, num_envs=1, device=args.device, seed=args.seed,
        headless=True, enable_ros_bridge_extension=False,
        disable_orsus_ros_env=True, env_cfg_hook=configure,
        # The full8 selection is attachment-free, so the designed ``disabled``
        # backend keeps aerial physics without the native aerial sensor ROS
        # manager.  This diagnostic consumes no aerial sensor stream, and the
        # baseline's native path currently passes a ``publish_ros`` keyword
        # the suite constructor does not accept (recorded as a known gap).
        aerial_sensor_backend="disabled")
    wall_start = time.monotonic()
    with open_simulator_session(config) as simulator:
        fleet = RestaurantFleetController(
            simulator, products_root=request["products_root"],
            deployment_profile=DEPLOYMENT_PROFILE)
        metadata = fleet.begin_episode(args.episode_id,
                                       warmup_ticks=args.warmup_ticks)
        _write_json(args.output / "episode-metadata.json", metadata)
        recorder = ConcurrencyProbeRecorder(
            episode_id=args.episode_id, backend_id=BACKEND_ID)
        backend = RestaurantAsyncExecutionBackend(
            fleet=fleet, public_task=request["plan_public_task"],
            deployment=request["deployment"], episode_id=args.episode_id,
            recorder=recorder)
        initial_snapshot = fleet.snapshot()
        initial_regions = backend._initial_robot_regions(initial_snapshot)
        initial_state = {"robot_regions": initial_regions, "object_states": {}}
        adapter = NativeAsyncPairAdapter(request["plan"])
        runner = FormalGoalEpisodeRunner(
            request["plan_public_task"], request["participant_api"], adapter,
            backend, output_root=args.output / "episode",
            episode_id=args.episode_id, max_turns=64,
            execution_mode="async", concurrency_recorder=recorder)
        result = runner.run(initial_state=initial_state)
        final_session = json.loads(
            (args.output / "episode" / "goal_session_report.json").read_text(
                encoding="utf-8"))["report"]
        backend.mark_deliveries_drained(
            float(final_session["current_time_s"]))
        _write_json(args.output / "async-backend-archive.json", backend.report())
        probe = None
        if request["native_mutex_probe"]:
            probe = _probe_mutex_on_live_fleet(
                backend, window_s=request["probe_window_s"],
                trusted_report={"session": final_session})
            _write_json(args.output / "native-mutex-probe.json", probe)
            _write_json(args.output / "async-backend-archive.json",
                        backend.report())
        final_evidence = build_concurrency_evidence_report(
            recorder, task_id=request["plan_public_task"]["task_id"],
            scene_key=request["plan_public_task"]["mission"]["scene_key"],
            runner_result=result)
        _write_json(args.output / "concurrency_evidence_native.json",
                    final_evidence)
        receipts = json.loads(
            (args.output / "episode" / "skill_receipts.json").read_text(
                encoding="utf-8"))
        overlap = final_evidence["overlap_pairs"]
        report = {
            "schema": "eai.restaurant.native_async_concurrency_report",
            "version": 1, "episode_id": args.episode_id,
            "status": "native_run_archived",
            "device": args.device, "seed": args.seed,
            "runner_error": result["error"],
            "runner_turns": result["turns"],
            "skill_receipt_count": result["skill_receipt_count"],
            "receipts": [{"task_id": row["task_id"], "status": row["status"],
                          "completed_at_s": row["completed_at_s"],
                          "receipt_ref": row["receipt_ref"]} for row in receipts],
            "overlap_pairs": [{"tasks": row["tasks"],
                               "overlap_start_s": row["overlap_start_s"],
                               "overlap_end_s": row["overlap_end_s"],
                               "overlap_duration_s": row["overlap_duration_s"],
                               "statuses": row["statuses"],
                               "evidence_refs": row["evidence_refs"]}
                              for row in overlap],
            "native_mutex_probe": None if probe is None else {
                "refused": (probe["refusal"] or {}).get("refused"),
                "holder_terminal": probe["holder_terminal"]},
            "resource_conflicts_status": final_evidence["resource_conflicts"]["status"],
            "post_termination_status":
                final_evidence["post_termination_execution_attempts"]["status"],
            "cancel_competition_status": final_evidence["cancel_competition"]["status"],
            "midflight_exit_status": final_evidence["midflight_member_exits"]["status"],
            "fleet_final_time_s": float(fleet.time_s),
            "fleet_physics_step": int(fleet.snapshot()["physics_step"]),
            "wall_time_s": round(time.monotonic() - wall_start, 3),
            "source_drift": [name for name, digest in request["source_sha256"].items()
                             if not (ROOT / name).is_file()
                             or hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != digest],
            "execution_validated": False, "business_outcomes_validated": False,
            "scoreable": False, "release_ready": False,
        }
        _write_json(args.output / "report.json", report)
        return report


def prepare_request(args: argparse.Namespace) -> dict[str, Any]:
    """Static validation before any simulator startup or directory writes."""
    from .goal_restaurant_deployment_v2 import build_restaurant_deployment_v2
    from .goal_restaurant_fleet import build_restaurant_full_fleet_selection

    if not args.episode_id.strip():
        raise ValueError("episode id must be nonempty")
    contract = build_restaurant_deployment_v2()
    plan = build_parallel_patrol_plan(window_s=args.nav_window_s)
    request = _driver_request(args, plan, build_restaurant_full_fleet_selection())
    request["plan_public_task"] = contract["public_task"]
    request["participant_api"] = contract["participant_api"]
    request["deployment"] = contract["deployment"]
    return request


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--products-root", type=Path, required=True)
    result.add_argument("--output", type=Path, required=True,
                        help="new directory; never overwritten")
    result.add_argument("--episode-id", required=True)
    result.add_argument("--device", choices=("cpu", "cuda:0"), default="cuda:0")
    result.add_argument("--seed", type=int, default=17)
    result.add_argument("--warmup-ticks", type=int, default=240)
    result.add_argument("--nav-window-s", type=float, default=45.0)
    result.add_argument("--probe-window-s", type=float, default=45.0)
    result.add_argument("--probe", action=argparse.BooleanOptionalAction,
                        default=True,
                        help="run the component-level native fleet-mutex probe")
    result.add_argument("--preflight-only", action="store_true")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.output.exists():
        raise FileExistsError(f"refusing to overwrite existing archive: {args.output}")
    if args.warmup_ticks < 1:
        raise ValueError("warmup ticks must be positive")
    if args.nav_window_s <= 0 or args.probe_window_s <= 0:
        raise ValueError("windows must be positive")
    request = prepare_request(args)
    args.output.mkdir(parents=True, exist_ok=False)
    _write_json(args.output / "request.json", request)
    try:
        from tools.assets.prepare_restaurant_benchmark_assets import inspect_candidate

        assets = inspect_candidate(args.products_root.resolve())
        _write_json(args.output / "asset-preflight.json", assets)
        if assets["status"] != "ready":
            raise RuntimeError("frozen Restaurant asset closure is not ready")
        if args.preflight_only:
            result = {"status": "preflight_ready", "native_started": False,
                      "execution_validated": False, "scoreable": False,
                      "release_ready": False}
            _write_json(args.output / "preflight-result.json", result)
        else:
            result = run_native(args, request)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as exc:
        _write_json(args.output / "failure.json", {
            "status": "native_async_run_error", "type": type(exc).__name__,
            "error": str(exc), "traceback": traceback.format_exc(),
            "execution_validated": False, "scoreable": False,
            "release_ready": False})
        raise


__all__ = [
    "BACKEND_ID",
    "NativeAsyncPairAdapter",
    "RestaurantAsyncExecutionBackend",
    "build_parallel_patrol_plan",
    "main",
    "parser",
    "prepare_request",
    "run_native",
]


if __name__ == "__main__":
    raise SystemExit(main())
