"""Consume one verified native FMC schedule without solving again.

Feedback belongs to the HOST plan consumer. It is NOT sent to FMC, does not
update native market state, and is not evidence of native dynamic replanning.
``replan`` is only the TaskRunner callback name: it exposes the remaining head
of each original robot queue. The original condition and assignments survive
unchanged. Deferred queue entries are not labelled native-unassigned tasks.

Use NativeFMCScheduleSession(build_native_adapter("fmc_ata", ...)) as provider,
NativeFMCFeedbackExecutor(session, executor) as orchestration_executor, and
session.replan as the replan callback. Inject an independent evaluator.

Executors must return ExecutionUpdate records with terminal ``success``/NONE
or ``failure``/non-NONE feedback. Retrying, guessing success from an arbitrary
string, and converting not_executed into success are deliberately unsupported.
The host must retain the original package/roster throughout this static plan.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, replace
import json
from threading import RLock

from algorithm.common_coordination.contract import Assignment, FailureCode
from task.contracts import AlgorithmDecision, DependencyOutcome, ExecutionUpdate


class NativeFMCScheduleError(RuntimeError):
    """Rejected plan/dispatch/feedback; no alternative allocation is invented."""


def _signature(package, robots):
    def convert(value):
        if isinstance(value, (set, frozenset)):
            return sorted(value)
        raise TypeError(f"non-JSON contract value: {type(value).__name__}")
    return json.dumps({"package": asdict(package), "robots": [asdict(r) for r in robots]},
                      sort_keys=True, allow_nan=False, default=convert)


class NativeFMCScheduleSession:
    """Single-use verified solve, immutable original queues, atomic consumption.

    ``begin_batch`` registers actual dispatch before feedback is accepted. Only
    one batch may be in flight. Validation failure, executor exception, or a
    valid terminal failure closes the entire session; there is no implicit
    retry. Failed batches never advance any queue. Their real receipts remain
    available in ``snapshot()``. A protocol rejection retains the pre-batch
    cursors and previously accepted feedback.
    """

    def __init__(self, adapter):
        if not callable(adapter):
            raise TypeError("callable native adapter required; source verification is caller-owned")
        self.adapter = adapter
        self._lock = RLock()
        self._attempted = False
        self._decision = None
        self._signature = None
        self._nodes = {}
        self._queues = {}
        self._cursors = {}
        self._inflight = None
        self._accepted = []
        self._completed = set()
        self._stopped = False
        self._closed = False
        self._reason = None

    def close(self):
        """Idempotently close this host consumer; no native RPC or solver call.

        This is not a physical emergency stop. The injected executor retains
        responsibility for stopping/closing its own physical resources.
        """
        with self._lock:
            self._closed = True

    def abort(self, reason):
        """Close host consumption, without reporting native/physical success."""
        with self._lock:
            self._stopped = True
            self._reason = self._reason or str(reason)

    def snapshot(self):
        with self._lock:
            return deepcopy({
                "scope": "host_consumption_of_one_native_schedule; feedback_not_sent_to_FMC",
                "native_replanning": False,
                "solve_attempts": int(self._attempted),
                "closed": self._closed,
                "status": "failed" if self._stopped else "new" if self._decision is None
                    else "completed" if len(self._completed) == len(self._nodes) else "active",
                "stop_reason": self._reason,
                "original_queues": {rid: [asdict(a) for a in queue] for rid, queue in self._queues.items()},
                "cursors": dict(self._cursors),
                "completed_task_ids": sorted(self._completed),
                "inflight": None if self._inflight is None else [asdict(a) for a in self._inflight],
                "accepted_feedback_batches": self._accepted,
            })

    def _ensure_active(self):
        if self._closed:
            raise NativeFMCScheduleError("host schedule session is closed")
        if self._decision is None:
            raise NativeFMCScheduleError("native schedule has not been admitted")
        if self._stopped:
            raise NativeFMCScheduleError("host mission stopped: " + str(self._reason))

    def _heads(self):
        return tuple(queue[self._cursors[rid]] for rid, queue in self._queues.items()
                     if self._cursors[rid] < len(queue))

    def _publish(self):
        decision = deepcopy(self._decision)
        return replace(decision, assignments=self._heads(), unassigned_tasks=(), provenance={
            **decision.provenance, "host_schedule_consumption": self.snapshot()})

    def __call__(self, package, robots):
        with self._lock:
            if self._closed:
                raise NativeFMCScheduleError("host schedule session is closed")
            if self._attempted:
                raise NativeFMCScheduleError("session already used; replan only exposes existing queue heads")
            robots = tuple(robots)
            self._signature = _signature(package, robots)
            self._attempted = True  # even a failed native call may never be retried in this session
            try:
                decision = self.adapter(package, robots)
                self._admit(decision, package, robots)
            except Exception as exc:
                self.abort("initial native plan rejected: " + str(exc))
                raise
            return self._publish()

    def _admit(self, decision, package, robots):
        if not isinstance(decision, AlgorithmDecision):
            raise NativeFMCScheduleError("verified adapter must return AlgorithmDecision")
        if decision.unassigned_tasks or any(t.prerequisites for t in package.graph.tasks):
            raise NativeFMCScheduleError("only a complete independent-task native schedule can be consumed")
        provenance = decision.provenance
        nodes = {t.task_id: t for t in package.graph.tasks}
        roster = {r.capability.robot_id: r for r in robots if r.capability.active}
        assigned = [a.task_id for a in decision.assignments]
        if len(set(assigned)) != len(assigned) or set(assigned) != set(nodes):
            raise NativeFMCScheduleError("native plan must cover each task exactly once")
        queues = {}
        for assignment in decision.assignments:
            robot = roster.get(assignment.robot_id)
            if robot is None or not nodes[assignment.task_id].required_capabilities <= robot.capability.capabilities:
                raise NativeFMCScheduleError("native assignment has an unknown/ineligible robot")
            queues.setdefault(assignment.robot_id, []).append(assignment)
        # Retain exactly the upstream per-player schedule, not the package order.
        try:
            native = provenance["native_input"]
            tasks = {t["id"]: t["task_id"] for t in native["tasks"]}
            players = {p["id"]: p["robot_id"] for p in native["players"]}
            native_pairs = [(tasks[e["task_id"]], players[p["id"]])
                            for p in provenance["native_output"]["players"] for e in p["schedule"]]
        except (KeyError, TypeError) as exc:
            raise NativeFMCScheduleError("missing native schedule identity evidence") from exc
        if native_pairs != [(a.task_id, a.robot_id) for a in decision.assignments]:
            raise NativeFMCScheduleError("assignments differ from original native schedule order")
        self._decision = deepcopy(decision)
        self._nodes = deepcopy(nodes)
        self._queues = {rid: tuple(queue) for rid, queue in queues.items()}
        self._cursors = {rid: 0 for rid in queues}

    def replan(self, package, robots, state=None):
        """Return remaining original queue heads; NEVER call the native solver."""
        with self._lock:
            if self._stopped or self._closed:
                return None
            self._ensure_active()
            if _signature(package, robots) != self._signature:
                self.abort("static package or robot roster changed")
                raise NativeFMCScheduleError(self._reason)
            if self._inflight is not None:
                raise NativeFMCScheduleError("feedback is required before publishing another batch")
            if state is not None and (set(state.completed_task_ids) != self._completed
                    or set(state.pending_task_ids) != set(self._nodes) - self._completed
                    or state.failed_task_ids or state.blocked_task_ids):
                self.abort("runner state contradicts accepted host feedback")
                raise NativeFMCScheduleError(self._reason)
            return self._publish()

    def begin_batch(self, assignments, *, tasks=None):
        """Register only original queue heads, before invoking a real executor."""
        with self._lock:
            self._ensure_active()
            if self._inflight is not None:
                raise NativeFMCScheduleError("a dispatch batch is already in flight")
            assignments = tuple(assignments)
            if not assignments or any(not isinstance(a, Assignment) for a in assignments):
                raise NativeFMCScheduleError("nonempty Assignment batch required")
            if len({a.task_id for a in assignments}) != len(assignments) or len({a.robot_id for a in assignments}) != len(assignments):
                raise NativeFMCScheduleError("duplicate task or robot in dispatch")
            heads = {(a.task_id, a.robot_id): a for a in self._heads()}
            for a in assignments:
                if heads.get((a.task_id, a.robot_id)) != a:
                    raise NativeFMCScheduleError("dispatch is not an unchanged original queue head")
                if tasks is not None and tasks.get(a.task_id) != self._nodes[a.task_id]:
                    raise NativeFMCScheduleError("executor task differs from frozen native input")
            self._inflight = assignments

    def feedback_batch(self, updates):
        """Validate the ENTIRE terminal batch before recording or advancing it.

        ExecutionUpdate is a trusted executor boundary, not a cryptographic
        physical attestation. This method rejects unsolicited, duplicate, stale,
        wrong-identity and contradictory records; an evaluator still owns truth.
        """
        with self._lock:
            self._ensure_active()
            try:
                if self._inflight is None:
                    raise NativeFMCScheduleError("feedback without registered dispatch")
                updates = tuple(updates)
                if not updates or any(not isinstance(u, ExecutionUpdate) for u in updates):
                    raise NativeFMCScheduleError("nonempty ExecutionUpdate terminal batch required")
                expected = [(a.task_id, a.robot_id) for a in self._inflight]
                received = [(u.feedback.task_id, u.feedback.robot_id) for u in updates]
                if len(set(received)) != len(received) or set(received) != set(expected):
                    raise NativeFMCScheduleError("duplicate, missing, stale or wrong-identity feedback")
                for update in updates:
                    feedback = update.feedback
                    if update.dependency_outcome is DependencyOutcome.COMPLETED:
                        valid = feedback.outcome == "success" and feedback.failure_code is FailureCode.NONE
                    elif update.dependency_outcome is DependencyOutcome.TERMINAL_FAILURE:
                        valid = feedback.outcome == "failure" and feedback.failure_code is not FailureCode.NONE
                    else:
                        valid = False
                    if not valid:
                        raise NativeFMCScheduleError("terminal outcome and success/failure code contradict")
                by_id = dict(zip(received, updates))
                ordered = tuple(by_id[key] for key in expected)
                records = [asdict(u) for u in ordered]
                # Freeze external mutable feedback payloads before the commit.
                records = deepcopy(records)
            except Exception as exc:
                self.abort("feedback rejected: " + str(exc))
                raise
            self._accepted.append(records)
            self._inflight = None
            if any(u.dependency_outcome is DependencyOutcome.TERMINAL_FAILURE for u in ordered):
                self.abort("executor terminal failure; original mission stopped without queue advancement")
            else:
                for update in ordered:
                    self._cursors[update.feedback.robot_id] += 1
                    self._completed.add(update.feedback.task_id)
            return ordered


class NativeFMCFeedbackExecutor:
    """Forward actual execution updates to the HOST schedule session, not FMC.

    A batch executor is called once; its reordered results are validated and
    returned in dispatch order. An execute-only executor supports singleton
    batches; a multi-robot batch requires execute_batch so partial serial failure
    cannot dispatch additional work or invent receipts for unexecuted tasks.
    Neither path fabricates updates.
    """

    def __init__(self, session, executor):
        if not isinstance(session, NativeFMCScheduleSession):
            raise TypeError("NativeFMCScheduleSession required")
        self.session, self.executor = session, executor

    def bind_run(self, request):
        bind = getattr(self.executor, "bind_run", None)
        if callable(bind):
            bind(request)

    def execute(self, assignment, task, *, scene):
        self.session.begin_batch((assignment,), tasks={task.task_id: task})
        try:
            update = self.executor.execute(assignment, task, scene=scene)
            return self.session.feedback_batch((update,))[0]
        except Exception as exc:
            self.session.abort("executor/feedback error: " + str(exc))
            raise

    def execute_batch(self, assignments, tasks, *, scene):
        assignments = tuple(assignments)
        batch = getattr(self.executor, "execute_batch", None)
        if not callable(batch) and len(assignments) > 1:
            self.session.abort("multi-robot execution requires executor.execute_batch")
            raise NativeFMCScheduleError("multi-robot execution requires executor.execute_batch")
        self.session.begin_batch(assignments, tasks=tasks)
        try:
            if callable(batch):
                updates = tuple(batch(assignments, tasks, scene=scene))
            else:
                updates = tuple(self.executor.execute(a, tasks[a.task_id], scene=scene) for a in assignments)
            return self.session.feedback_batch(updates)
        except Exception as exc:
            self.session.abort("executor/feedback error: " + str(exc))
            raise
