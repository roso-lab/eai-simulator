"""Deterministic fake-backend concurrency evidence for the R1 roadmap item.

The R1 roadmap requires proving, before any native validation, that the
execution layer can actually run independent skills simultaneously, refuse
same-resource double dispatch, resolve cancellation races, handle a member
exit while a skill is in flight, and stop executing after a human-assistance
termination.  This module provides the offline, Isaac-free instruments for
that proof:

* :class:`ConcurrencyProbeRecorder` collects execution facts on one fake
  clock.  Concurrency evidence is defined strictly as *overlapping actual
  execution intervals plus real completion events on that clock*.  Overlapping
  timestamps in a submitted plan are never counted as concurrency.
* :class:`ConcurrentFakeBackend` is a deterministic ``GoalExecutionBackend``.
  In ``in_flight`` mode ``execute`` records a start without completing the
  skill, and completion is driven explicitly through ``complete``/``cancel``
  or by ``advance_idle`` reaching the planned end, so several skills can be
  simultaneously in flight.  In ``serial`` mode every ``execute`` terminates
  its skill immediately at the admitted window end; this is the mode the
  current synchronous :class:`~demo.benchmark.goal_episode_runner.FormalGoalEpisodeRunner`
  loop can drive.
* :func:`build_concurrency_evidence_report` freezes the collected facts into
  the ``eai.benchmark.goal_concurrency_evidence`` archive.  Every one of the
  five evidence categories is either ``triggered`` with details or explicitly
  ``not_triggered``; release-style flags are permanently ``False``.

Known integration boundary (documented, not hidden): the *default* runner
loop executes ``pending[0]`` synchronously to a terminal receipt before the
next skill starts, so genuine in-flight overlap cannot be produced through
that unmodified serial loop. ``FormalGoalEpisodeRunner`` now offers an
opt-in ``execution_mode="async"`` that requires this backend's explicit
``supports_async_execution`` capability (in-flight mode only); under that
mode the runner starts every pending dispatch without blocking, advances
the backend clock through ``advance_idle``, commits staged
``completed_deliveries`` terminals through the real ``GoalSession``, and
archives this report as ``concurrency_evidence.json`` when a recorder is
configured. Runner-level serial tests still prove the serial counterexample
(planned overlap that is correctly not credited), admission windows,
cancellation, and post-termination refusals; component-level tests keep
covering the same races directly against the state machine. This module
never starts Isaac and never promotes any run to a scoreable benchmark
result.
"""

from __future__ import annotations

from copy import deepcopy
import math
from typing import Any, Mapping

GOAL_CONCURRENCY_EVIDENCE_SCHEMA = "eai.benchmark.goal_concurrency_evidence"
GOAL_CONCURRENCY_EVIDENCE_VERSION = 1

_NAVIGATION = frozenset({"ground_navigation", "aerial_navigation", "stair_traversal"})
_DESTINATION_KEYS = {
    "pick": "pickup_region_id",
    "place": "placement_region_id",
    "handoff": "receiving_region_id",
    "cooperative_carry": "destination_region_id",
    "stair_traversal": "exit_region_id",
}

EVIDENCE_CATEGORIES = (
    "resource_conflicts",
    "cancel_competition",
    "midflight_member_exits",
    "post_termination_execution_attempts",
)

_EVIDENCE_DEFINITION = (
    "Concurrency evidence requires overlapping actual execution intervals on "
    "one fake clock, each closed by a real completion event. Overlapping "
    "planned execution windows are never counted as concurrency evidence."
)

_INTEGRATION_GAP = {
    "runner_loop": (
        "The default FormalGoalEpisodeRunner loop executes pending[0] "
        "synchronously to a terminal receipt before the next skill starts, so "
        "no two skills can be simultaneously in flight through that "
        "unmodified serial loop. The opt-in execution_mode='async' removes "
        "this structural limit for backends that explicitly declare "
        "supports_async_execution."
    ),
    "genuine_overlap_scope": (
        "Genuine in-flight overlap, the backend-level resource mutex, "
        "mid-flight member exits, and cancel-versus-completion races are "
        "exercised at component level against the real GoalSession state "
        "machine and, in the runner's opt-in async execution mode, through "
        "the real FormalGoalEpisodeRunner loop itself."
    ),
    "native_validation": (
        "All facts in this archive come from a deterministic fake backend; "
        "native/physical validation and scoreability remain unclaimed."
    ),
}


class ConcurrencyEvidenceRefused(RuntimeError):
    """Base class for deterministic fake-backend execution refusals."""


class ResourceConflictExecutionRefused(ConcurrencyEvidenceRefused):
    """A resource demanded by the task is already held by an in-flight skill."""


class PostTerminationExecutionRefused(ConcurrencyEvidenceRefused):
    """Execution was attempted after a human-assistance termination."""


def _finite_s(value: Any, label: str) -> float:
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value < 0):
        raise ValueError(f"{label} must be a finite nonnegative number")
    return float(value)


def task_resource_keys(task: Mapping[str, Any], *, routes: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """Derive the deterministic mutex keys one task demands.

    This mirrors the symbolic resource dimensions ``GoalSession`` reserves
    (agents, route resources, objects, and destination regions for non
    navigation skills) at capacity one.  It is a fake-backend mirror for
    evidence collection, not a second implementation of the session contract.
    """
    arguments = task["arguments"]
    skill = task["skill"]
    keys = {f"agent:{agent}" for agent in task["assigned_agents"]}
    for route_id in arguments.get("route_ids", []):
        route = routes.get(route_id)
        if route is None:
            raise ValueError(f"unknown route_id for concurrency evidence: {route_id}")
        keys.add(f"route:{route['resource_id']}")
    if "object_id" in arguments:
        keys.add(f"object:{arguments['object_id']}")
    destination = arguments.get(_DESTINATION_KEYS.get(skill, "region_id"))
    if destination is not None and skill not in _NAVIGATION:
        keys.add(f"region:{destination}")
    return sorted(keys)


class ConcurrencyProbeRecorder:
    """Collect concurrency evidence facts on one monotonic fake clock.

    The recorder is the evidence authority.  It never inspects planned
    windows to manufacture concurrency: overlap pairs are computed only from
    execution intervals that were closed by a real completion event.
    """

    def __init__(self, *, episode_id: str, backend_id: str = "concurrent-fake-backend"):
        if not isinstance(episode_id, str) or not episode_id.strip():
            raise ValueError("recorder episode_id must be a nonempty string")
        if not isinstance(backend_id, str) or not backend_id.strip():
            raise ValueError("recorder backend_id must be a nonempty string")
        self.episode_id = episode_id
        self.backend_id = backend_id
        self._clock_s = 0.0
        self._executions: dict[str, dict[str, Any]] = {}
        self._execution_order: list[str] = []
        self._plan_windows: dict[str, dict[str, float]] = {}
        self._resource_conflicts: list[dict[str, Any]] = []
        self._cancels: list[dict[str, Any]] = []
        self._midflight_exits: list[dict[str, Any]] = []
        self._post_termination: list[dict[str, Any]] = []
        self._idle_advances: list[dict[str, Any]] = []
        self._terminated_at_s: float | None = None

    # ------------------------------------------------------------------
    # Clock
    # ------------------------------------------------------------------
    @property
    def clock_s(self) -> float:
        return self._clock_s

    def advance_clock(self, target_s: Any, *, label: str = "explicit") -> float:
        target = _finite_s(target_s, "target_s")
        if target < self._clock_s:
            raise ValueError("fake evidence clock cannot move backwards")
        self._clock_s = target
        del label
        return self._clock_s

    def _witness(self, at_s: Any) -> float:
        """Record an event time that may precede the current clock position.

        A pump jump (``advance_idle`` past several due ends) closes intervals
        whose terminal times lie in the past relative to the clock; that is a
        legitimate witness, never a backwards clock move.  Only forward times
        advance the clock.
        """
        at = _finite_s(at_s, "at_s")
        if at > self._clock_s:
            self._clock_s = at
        return at

    # ------------------------------------------------------------------
    # Plan windows (context only; never concurrency evidence)
    # ------------------------------------------------------------------
    def note_plan(self, plan: Mapping[str, Any]) -> None:
        for task in plan.get("tasks", []):
            execution = task["execution"]
            start = _finite_s(execution["start_s"], "plan start_s")
            duration = _finite_s(execution["duration_s"], "plan duration_s")
            self._plan_windows[task["task_id"]] = {
                "start_s": start, "end_s": start + duration}

    @property
    def planned_windows(self) -> dict[str, dict[str, float]]:
        return {tid: dict(row) for tid, row in self._plan_windows.items()}

    # ------------------------------------------------------------------
    # Executions
    # ------------------------------------------------------------------
    def note_execution_start(self, task_id: str, *, skill: str, start_s: Any,
                             planned_end_s: Any, resources: list[str],
                             mode: str) -> None:
        if task_id in self._executions:
            raise ValueError(f"task {task_id} already has an execution record")
        start = self.advance_clock(start_s)
        planned_end = _finite_s(planned_end_s, "planned_end_s")
        if planned_end < start:
            raise ValueError("planned execution end precedes its start")
        self._executions[task_id] = {
            "task_id": task_id, "skill": skill, "mode": mode,
            "start_s": start, "planned_end_s": planned_end,
            "end_s": None, "status": "in_flight", "receipt_ref": None,
            "resources": list(resources),
        }
        self._execution_order.append(task_id)

    def note_execution_terminal(self, task_id: str, *, end_s: Any, status: str,
                                receipt_ref: str) -> None:
        if task_id not in self._executions:
            raise ValueError(f"unknown execution for task {task_id}")
        row = self._executions[task_id]
        if row["status"] != "in_flight":
            raise ValueError(f"execution for task {task_id} is already closed")
        if status not in {"succeeded", "failed", "cancelled"}:
            raise ValueError("execution terminal status must be terminal")
        end = self._witness(end_s)
        if end < row["start_s"]:
            raise ValueError("execution completion precedes its start")
        row.update({"end_s": end, "status": status, "receipt_ref": receipt_ref})

    def note_resource_conflict(self, task_id: str, *, resource_key: str,
                               held_by: str | None, at_s: Any, layer: str,
                               detail: str) -> None:
        self._resource_conflicts.append({
            "task_id": task_id, "resource_key": resource_key,
            "held_by": held_by, "at_s": self._witness(at_s),
            "layer": layer, "detail": detail,
        })

    def note_cancel_observation(self, task_id: str, *, at_s: Any, in_flight: bool,
                                outcome: str, cause: str,
                                extra: Mapping[str, Any] | None = None) -> None:
        row: dict[str, Any] = {
            "task_id": task_id, "at_s": self._witness(at_s),
            "in_flight": in_flight, "outcome": outcome, "cause": cause,
        }
        if extra:
            row.update(deepcopy(dict(extra)))
        self._cancels.append(row)

    def note_midflight_exit(self, task_id: str, *, agent_id: str, at_s: Any,
                            in_flight: bool, session_status_before: str,
                            session_status_after: str) -> None:
        self._midflight_exits.append({
            "task_id": task_id, "agent_id": agent_id,
            "at_s": self._witness(at_s), "in_flight": in_flight,
            "session_status_before": session_status_before,
            "session_status_after": session_status_after,
        })

    def note_post_termination_attempt(self, task_id: str, *, at_s: Any,
                                      operation: str, refusal: str) -> None:
        self._post_termination.append({
            "task_id": task_id, "at_s": self._witness(at_s),
            "operation": operation, "refusal": refusal,
        })

    def note_idle_advance(self, *, from_s: Any, to_s: Any) -> None:
        start = _finite_s(from_s, "from_s")
        end = self.advance_clock(to_s)
        self._idle_advances.append({"from_s": start, "to_s": end})

    def note_termination(self, at_s: Any) -> None:
        if self._terminated_at_s is None:
            self._terminated_at_s = self._witness(at_s)

    # ------------------------------------------------------------------
    # Read access for report building
    # ------------------------------------------------------------------
    @property
    def executions(self) -> dict[str, dict[str, Any]]:
        return {tid: deepcopy(row) for tid, row in self._executions.items()}

    def in_flight_task_ids(self) -> list[str]:
        return [tid for tid in self._execution_order
                if self._executions[tid]["status"] == "in_flight"]

    def cancellation_observations(self) -> list[dict[str, Any]]:
        return deepcopy(self._cancels)

    def _category_rows(self, name: str) -> list[dict[str, Any]]:
        return {"resource_conflicts": self._resource_conflicts,
                "cancel_competition": [
                    row for row in self._cancels
                    if row["outcome"] in {"cancel_arrived_first_while_in_flight",
                                          "completion_arrived_first"}],
                "midflight_member_exits": self._midflight_exits,
                "post_termination_execution_attempts": self._post_termination,
                }[name]


def _overlap(start_a: float, end_a: float, start_b: float, end_b: float) -> tuple[float, float] | None:
    start = max(start_a, start_b)
    end = min(end_a, end_b)
    if start < end:
        return start, end
    return None


def _closed_executions(recorder: ConcurrencyProbeRecorder) -> list[dict[str, Any]]:
    return [row for row in recorder.executions.values() if row["status"] != "in_flight"]


def _execution_pairs(recorder: ConcurrencyProbeRecorder) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    rows = sorted(_closed_executions(recorder), key=lambda row: (row["start_s"], row["task_id"]))
    return [(rows[i], rows[j]) for i in range(len(rows)) for j in range(i + 1, len(rows))]


def _pair_label(a: Mapping[str, Any], b: Mapping[str, Any]) -> str:
    return "+".join(sorted((a["task_id"], b["task_id"])))


def build_concurrency_evidence_report(
    recorder: ConcurrencyProbeRecorder,
    *,
    task_id: str | None = None,
    scene_key: str | None = None,
    admission_conflicts: list[Mapping[str, Any]] | None = None,
    runner_result: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Freeze recorder facts into the versioned concurrency evidence archive.

    ``admission_conflicts`` carries caller-observed session-admission
    refusals (the runtime resource mutex in ``GoalSession``) as structured
    rows; they are reported alongside backend-level refusals and never
    conflated with them.  The release flag is permanently ``False``: this is
    fake-backend evidence only and never a scoreable benchmark artifact.
    """
    executions = recorder.executions
    closed = _closed_executions(recorder)
    open_rows = [row for row in executions.values() if row["status"] == "in_flight"]
    planned = recorder.planned_windows

    overlap_pairs: list[dict[str, Any]] = []
    planned_not_counted: list[dict[str, Any]] = []
    for a, b in _execution_pairs(recorder):
        actual = _overlap(a["start_s"], a["end_s"], b["start_s"], b["end_s"])
        window_a = planned.get(a["task_id"])
        window_b = planned.get(b["task_id"])
        planned_overlap = None
        if window_a is not None and window_b is not None:
            planned_overlap = _overlap(
                window_a["start_s"], window_a["end_s"],
                window_b["start_s"], window_b["end_s"])
        if actual is not None:
            overlap_pairs.append({
                "task_pair": _pair_label(a, b),
                "tasks": sorted((a["task_id"], b["task_id"])),
                "actual_intervals": {
                    a["task_id"]: [a["start_s"], a["end_s"]],
                    b["task_id"]: [b["start_s"], b["end_s"]],
                },
                "overlap_start_s": actual[0],
                "overlap_end_s": actual[1],
                "overlap_duration_s": round(actual[1] - actual[0], 6),
                "statuses": {a["task_id"]: a["status"], b["task_id"]: b["status"]},
                "evidence_refs": sorted(
                    ref for ref in (a["receipt_ref"], b["receipt_ref"]) if ref),
                "planned_overlap_s": (
                    [planned_overlap[0], planned_overlap[1]]
                    if planned_overlap is not None else None),
            })
        elif planned_overlap is not None:
            planned_not_counted.append({
                "task_pair": _pair_label(a, b),
                "tasks": sorted((a["task_id"], b["task_id"])),
                "planned_overlap_s": [planned_overlap[0], planned_overlap[1]],
                "actual_intervals": {
                    a["task_id"]: [a["start_s"], a["end_s"]],
                    b["task_id"]: [b["start_s"], b["end_s"]],
                },
                "reason": "actual_execution_intervals_do_not_overlap",
            })

    recorded_pairs = {row["task_pair"] for row in planned_not_counted}
    for a, b in sorted((x, y) for x in planned for y in planned if x < y):
        pair_label = "+".join(sorted((a, b)))
        if pair_label in recorded_pairs:
            continue
        planned_overlap = _overlap(
            planned[a]["start_s"], planned[a]["end_s"],
            planned[b]["start_s"], planned[b]["end_s"])
        if planned_overlap is None:
            continue
        actual_a = executions.get(a)
        actual_b = executions.get(b)
        if (actual_a is not None and actual_b is not None
                and actual_a["status"] != "in_flight"
                and actual_b["status"] != "in_flight"):
            continue  # both closed; the closed-pairs loop already classified it
        planned_not_counted.append({
            "task_pair": pair_label,
            "tasks": sorted((a, b)),
            "planned_overlap_s": [planned_overlap[0], planned_overlap[1]],
            "actual_intervals": {
                tid: ([executions[tid]["start_s"], executions[tid]["end_s"]]
                      if tid in executions else None)
                for tid in sorted((a, b))},
            "reason": "execution_missing_or_still_open",
        })

    resource_conflicts = [deepcopy(row) for row in recorder._category_rows("resource_conflicts")]
    for row in (admission_conflicts or []):
        resource_conflicts.append({
            "task_id": row["task_id"], "resource_key": row["resource_key"],
            "held_by": row.get("held_by"), "at_s": _finite_s(row.get("at_s", 0.0), "at_s"),
            "layer": "session_admission", "detail": row.get(
                "detail", "GoalSession refused the dispatch while the resource was pending"),
        })

    categories: dict[str, dict[str, Any]] = {}
    for name in EVIDENCE_CATEGORIES:
        rows = (resource_conflicts if name == "resource_conflicts"
                else recorder._category_rows(name))
        details = deepcopy(rows) if rows else []
        if name == "midflight_member_exits":
            for detail in details:
                terminal = executions.get(detail["task_id"])
                detail["task_outcome"] = (
                    {"status": terminal["status"], "end_s": terminal["end_s"]}
                    if terminal is not None and terminal["status"] != "in_flight" else
                    "in_flight_or_never_terminated")
        categories[name] = {
            "status": "triggered" if details else "not_triggered",
            "count": len(details),
            "details": details,
        }

    cancels = recorder.cancellation_observations()
    resource_intervals: dict[str, list[dict[str, Any]]] = {}
    for row in sorted(closed, key=lambda item: (item["start_s"], item["task_id"])):
        for key in row["resources"]:
            resource_intervals.setdefault(key, []).append({
                "task_id": row["task_id"], "start_s": row["start_s"],
                "end_s": row["end_s"], "status": row["status"]})

    report: dict[str, Any] = {
        "schema": GOAL_CONCURRENCY_EVIDENCE_SCHEMA,
        "version": GOAL_CONCURRENCY_EVIDENCE_VERSION,
        "episode_id": recorder.episode_id,
        "backend_id": recorder.backend_id,
        "task_id": task_id,
        "scene_key": scene_key,
        "evidence_definition": _EVIDENCE_DEFINITION,
        "clock": {
            "final_s": recorder.clock_s,
            "idle_advances": deepcopy(recorder._idle_advances),
            "terminated_at_s": recorder._terminated_at_s,
        },
        "execution_counts": {
            "total": len(executions),
            "closed": len(closed),
            "in_flight_open": len(open_rows),
        },
        "executions": [
            executions[tid] for tid in sorted(executions)
        ],
        "in_flight_open": [row["task_id"] for row in open_rows],
        "planned_windows": {tid: planned[tid] for tid in sorted(planned)},
        "overlap_pairs": overlap_pairs,
        "planned_overlap_not_counted": planned_not_counted,
        "resource_intervals": {
            key: resource_intervals[key] for key in sorted(resource_intervals)},
        "cancellations_other": [
            row for row in cancels
            if row["outcome"] not in {"cancel_arrived_first_while_in_flight",
                                      "completion_arrived_first"}],
        "runner_integration": (
            None if runner_result is None else {
                "integrated": True,
                "turns": runner_result.get("turns"),
                "error": runner_result.get("error"),
                "skill_receipt_count": runner_result.get("skill_receipt_count"),
                "termination": deepcopy(dict(runner_result["termination"]))
                if runner_result.get("termination") else None,
            }),
        "integration_gap": _INTEGRATION_GAP,
        "release": False,
        "scoreable": False,
        "execution_validated": False,
        "limitations": [
            "fake_backend_no_physical_execution",
            "single_fake_clock_shared_by_all_recorded_events",
            "planned_windows_are_context_not_evidence",
            "no_isaac_no_ros_no_native_validation",
        ],
    }
    report.update(categories)
    return report


class ConcurrentFakeBackend:
    """Deterministic ``GoalExecutionBackend`` double with in-flight semantics.

    Parameters
    ----------
    public_task:
        The public task spec; only its route table feeds the mirrored
        resource-key derivation.
    episode_id:
        Episode identity stamped into every receipt.
    recorder:
        The evidence authority.
    runtime_state:
        Complete ``{"robot_regions", "object_states"}`` template returned with
        terminal results and idle advances.
    serial:
        ``False`` (default) keeps executed skills in flight until
        ``complete``/``cancel``/``advance_idle`` terminates them.
        ``True`` terminates every ``execute`` synchronously at the admitted
        window end, which is the only mode the current synchronous runner
        loop can drive.
    """

    def __init__(self, public_task: Mapping[str, Any], *, episode_id: str,
                 recorder: ConcurrencyProbeRecorder,
                 runtime_state: Mapping[str, Any], serial: bool = False,
                 backend_id: str = "concurrent-fake-backend") -> None:
        if not isinstance(recorder, ConcurrencyProbeRecorder):
            raise ValueError("recorder must be a ConcurrencyProbeRecorder")
        self._routes = {row["id"]: row for row in public_task["public_world"]["routes"]}
        self._episode_id = episode_id
        self._recorder = recorder
        self._runtime_state = deepcopy(dict(runtime_state))
        self._serial = bool(serial)
        self._backend_id = backend_id
        if recorder.backend_id != backend_id:
            raise ValueError("recorder backend_id must match the fake backend")
        self._in_flight: dict[str, dict[str, Any]] = {}
        self._dispatch_actions: dict[str, str | None] = {}
        self._terminal_status = "succeeded"
        self.completed_deliveries: list[dict[str, Any]] = []
        self.idle_calls: list[float] = []
        self.execute_calls: list[tuple[str, float]] = []
        self.cancel_calls: list[str] = []

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------
    @property
    def serial(self) -> bool:
        return self._serial

    @property
    def supports_async_execution(self) -> bool:
        """Declare the runner's opt-in async execution capability.

        Only the in-flight mode can keep several skills simultaneously
        executing under the runner's two-phase dispatch/pump loop; the serial
        mode exists precisely for the default synchronous runner loop.
        """
        return not self._serial

    def in_flight_task_ids(self) -> list[str]:
        return sorted(self._in_flight)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _session_now(self, trusted_report: Mapping[str, Any]) -> float:
        session = trusted_report.get("session", trusted_report)
        now = _finite_s(session["current_time_s"], "trusted current_time_s")
        return max(self._recorder.clock_s, now)

    def _terminated(self, trusted_report: Mapping[str, Any]) -> bool:
        session = trusted_report.get("session", trusted_report)
        assessment = session.get("deployment_assessment") or {}
        return bool(assessment.get("terminated"))

    def _session_task_row(self, trusted_report: Mapping[str, Any], task_id: str) -> dict[str, Any] | None:
        session = trusted_report.get("session", trusted_report)
        return session.get("tasks", {}).get(task_id)

    def _start_execution(self, task: Mapping[str, Any], *, start_s: float) -> dict[str, Any]:
        task_id = task["task_id"]
        resources = task_resource_keys(task, routes=self._routes)
        for key in resources:
            holder = next((tid for tid, row in self._in_flight.items()
                           if key in row["resources"]), None)
            if holder is not None:
                self._recorder.note_resource_conflict(
                    task_id, resource_key=key, held_by=holder, at_s=start_s,
                    layer="backend_mutex",
                    detail=("fake backend refused a second in-flight execution "
                            f"holding {key}"))
                raise ResourceConflictExecutionRefused(
                    f"resource {key} is already held by in-flight task {holder}")
        execution = task["execution"]
        duration = _finite_s(execution["duration_s"], "task duration_s")
        planned_start = _finite_s(execution["start_s"], "task start_s")
        # Complete at the admitted window end when the dispatch started late,
        # exactly like serial mode: GoalSession refuses a successful completion
        # beyond the planned window, so an in-flight skill restarted after a
        # backend mutex refusal must not plan past that boundary.
        planned_end = min(start_s + duration, planned_start + duration)
        row = {
            "task": deepcopy(dict(task)), "resources": resources,
            "planned_end_s": planned_end,
        }
        self._in_flight[task_id] = row
        self._recorder.note_execution_start(
            task_id, skill=task["skill"], start_s=start_s,
            planned_end_s=row["planned_end_s"], resources=resources,
            mode="serial" if self._serial else "in_flight")
        return row

    def build_receipt(self, task_id: str, *, status: str, completed_at_s: float,
                      action_id: str | None = None) -> dict[str, Any]:
        """Build the exact GoalSession receipt shape for one terminal event."""
        return {
            "backend_id": self._backend_id,
            "receipt_ref": (f"{self._episode_id}:{task_id}:{status}:"
                            f"{completed_at_s!r}"),
            "task_id": task_id,
            "action_id": action_id if action_id is not None
            else self._dispatch_actions.get(task_id),
            "status": status,
            "completed_at_s": completed_at_s,
            "episode_id": self._episode_id,
            "evidence_refs": [
                f"fake_execution:{self._episode_id}:{task_id}:{status}"],
        }

    def _terminal_result(self, task_id: str, *, status: str,
                         completed_at_s: float) -> dict[str, Any]:
        action_id = self._dispatch_actions.get(task_id)
        if action_id is None:
            raise ValueError(
                f"task {task_id} has no cached dispatch action; supply a trusted report")
        self._in_flight.pop(task_id, None)
        receipt = self.build_receipt(task_id, status=status,
                                     completed_at_s=completed_at_s,
                                     action_id=action_id)
        self._recorder.note_execution_terminal(
            task_id, end_s=completed_at_s, status=status,
            receipt_ref=receipt["receipt_ref"])
        return {"status": status, "completed_at_s": completed_at_s,
                "receipt": receipt, "runtime_state": deepcopy(self._runtime_state)}

    # ------------------------------------------------------------------
    # GoalExecutionBackend protocol
    # ------------------------------------------------------------------
    def execute(self, task: Mapping[str, Any], *, episode_id: str,
                trusted_report: Mapping[str, Any]) -> dict[str, Any]:
        if episode_id != self._episode_id:
            raise ValueError("fake backend received a foreign episode_id")
        now = self._session_now(trusted_report)
        task_id = task["task_id"]
        row = self._session_task_row(trusted_report, task_id)
        if row is not None:
            self._dispatch_actions[task_id] = row.get("dispatch_action_id")
        self.execute_calls.append((task_id, now))
        if self._terminated(trusted_report):
            self._recorder.note_post_termination_attempt(
                task_id, at_s=now, operation="execute",
                refusal="PostTerminationExecutionRefused")
            raise PostTerminationExecutionRefused(
                "deployment assessment terminated the episode; refusing execute")
        started = self._start_execution(task, start_s=now)
        if not self._serial:
            return {
                "status": "in_flight", "task_id": task_id,
                "started_at_s": now,
                "planned_end_s": started["planned_end_s"],
                "resources": list(started["resources"]),
                "backend_id": self._backend_id,
            }
        execution = task["execution"]
        window_end = (_finite_s(execution["start_s"], "window start_s")
                      + _finite_s(execution["duration_s"], "window duration_s"))
        completed_at = max(window_end, now)
        return self._terminal_result(task_id, status=self._terminal_status,
                                     completed_at_s=completed_at)

    def cancel(self, task: Mapping[str, Any], *, episode_id: str,
               trusted_report: Mapping[str, Any]) -> dict[str, Any]:
        if episode_id != self._episode_id:
            raise ValueError("fake backend received a foreign episode_id")
        now = self._session_now(trusted_report)
        task_id = task["task_id"]
        self.cancel_calls.append(task_id)
        row = self._session_task_row(trusted_report, task_id)
        if row is not None and row.get("dispatch_action_id"):
            self._dispatch_actions[task_id] = row["dispatch_action_id"]
        in_flight = task_id in self._in_flight
        safety_stop = bool(row is not None and row.get("safety_stop"))
        terminated = self._terminated(trusted_report)
        if terminated:
            cause = "post_termination_safety_stop"
            outcome = "safety_stop_after_termination"
        elif safety_stop:
            cause = "safety_stop_after_invalidation"
            outcome = "safety_stop_while_in_flight" if in_flight else "safety_stop_pre_execution"
        else:
            cause = "participant_cancel"
            outcome = ("cancel_arrived_first_while_in_flight" if in_flight
                       else "pre_execution_withdrawal")
        self._recorder.note_cancel_observation(
            task_id, at_s=now, in_flight=in_flight, outcome=outcome,
            cause=cause, extra={"terminated": terminated})
        if in_flight:
            self._in_flight.pop(task_id)
            return self._terminal_result(task_id, status="cancelled",
                                         completed_at_s=now)
        # A withdrawal that arrives before the backend ever started the skill
        # (the runner's immediate-cancellation path) closes no interval; the
        # cancellation observation above is the whole evidence record.
        action_id = self._dispatch_actions.get(task_id)
        if action_id is None:
            raise ValueError(
                f"task {task_id} has no cached dispatch action; supply a trusted report")
        receipt = self.build_receipt(task_id, status="cancelled",
                                     completed_at_s=now, action_id=action_id)
        return {"status": "cancelled", "completed_at_s": now, "receipt": receipt,
                "runtime_state": deepcopy(self._runtime_state)}

    def advance_idle(self, target_s: float, *, trusted_report: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Advance the fake clock, completing in-flight skills that come due.

        The runner calls this positionally with its computed wakeup time.
        Component-level harnesses may pass ``trusted_report`` to refresh the
        cached dispatch action ids.  Completed receipts are staged in
        :attr:`completed_deliveries` for the harness to commit through
        ``GoalSession.complete_skill``; the fake backend never writes the
        session ledger itself.
        """
        target = _finite_s(target_s, "idle target_s")
        before = self._recorder.clock_s
        if trusted_report is not None:
            now = self._session_now(trusted_report)
            if now > target:
                target = now
        self._recorder.note_idle_advance(from_s=before, to_s=target)
        self.idle_calls.append(target)
        for task_id in sorted(self._in_flight):
            row = self._in_flight[task_id]
            if row["planned_end_s"] <= target:
                self._in_flight.pop(task_id)
                self.completed_deliveries.append(self._terminal_result(
                    task_id, status=self._terminal_status,
                    completed_at_s=row["planned_end_s"]))
        return {"time_s": self._recorder.clock_s,
                "runtime_state": deepcopy(self._runtime_state)}

    # ------------------------------------------------------------------
    # Component-level explicit completion
    # ------------------------------------------------------------------
    def complete(self, task_id: str, *, status: str = "succeeded",
                 at_s: float | None = None,
                 trusted_report: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Explicitly terminate one in-flight skill at a deterministic time."""
        if task_id not in self._in_flight:
            raise RuntimeError(f"task {task_id} is not in flight")
        if status not in {"succeeded", "failed", "cancelled"}:
            raise ValueError("explicit completion needs a terminal status")
        if trusted_report is not None:
            row = self._session_task_row(trusted_report, task_id)
            if row is not None and row.get("dispatch_action_id"):
                self._dispatch_actions[task_id] = row["dispatch_action_id"]
        end = (self._in_flight[task_id]["planned_end_s"] if at_s is None
               else _finite_s(at_s, "completion at_s"))
        self._in_flight.pop(task_id)
        return self._terminal_result(task_id, status=status, completed_at_s=end)


__all__ = [
    "GOAL_CONCURRENCY_EVIDENCE_SCHEMA",
    "GOAL_CONCURRENCY_EVIDENCE_VERSION",
    "ConcurrencyEvidenceRefused",
    "ConcurrencyProbeRecorder",
    "ConcurrentFakeBackend",
    "PostTerminationExecutionRefused",
    "ResourceConflictExecutionRefused",
    "build_concurrency_evidence_report",
    "task_resource_keys",
]
