"""Production-shaped goal episode runner.

The runner owns one GoalSession and keeps the participant adapter, execution
backend, domain evaluator, and persisted evidence on the same episode identity.
It is deliberately backend-neutral: an Isaac adapter can implement the small
``GoalExecutionBackend`` protocol without changing the participant boundary.
The runner never promotes a run to a scoreable benchmark result by itself.
"""

from __future__ import annotations

from copy import deepcopy
import json
import math
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol
import uuid

from .goal_algorithm_adapter import GoalAlgorithmAdapter, GoalAlgorithmHost
from .goal_algorithm_adapter import validate_goal_adapter_manifest
from .algorithm_adapter import run_algorithm_subprocess
from .goal_session import GoalSession
from .goal_session_audit import (
    AuditedGoalSession,
    GOAL_SESSION_AUDIT_REPLAY_SCHEMA,
    GOAL_SESSION_AUDIT_VERSION,
    replay_goal_session_audit,
)
from .provenance import canonical_sha256


class GoalExecutionBackend(Protocol):
    """Trusted host-side execution adapter.

    ``execute`` runs one skill to termination before the next skill starts. Its
    completion time must be at least the trusted report's current time. It must
    return a terminal status and a complete GoalSession receipt; it may also
    return a fresh runtime state. The participant never receives this object
    or the trusted host report.

    ``cancel`` acknowledges a previously dispatched task without starting
    ordinary execution. It must return a ``cancelled`` terminal receipt and
    may include a fresh state. Backends without cancellation support fail
    closed while the task remains cancel_requested.
    """

    def execute(self, task: Mapping[str, Any], *, episode_id: str,
                trusted_report: Mapping[str, Any]) -> Mapping[str, Any]: ...

    def cancel(self, task: Mapping[str, Any], *, episode_id: str,
               trusted_report: Mapping[str, Any]) -> Mapping[str, Any]: ...


BusinessEvaluator = Callable[[str, Mapping[str, Any]], Mapping[str, Mapping[str, Any]]]
AssessmentEvaluator = Callable[[str, Mapping[str, Any]], Mapping[str, Any]]


class DeploymentAssessmentProtocolError(RuntimeError):
    """Participant failed to declare a required deployment assessment."""


class SubprocessGoalAdapter:
    """Isolated JSON adapter for a goal algorithm process.

    The child receives only public task/API data and the current public event
    batch. It returns ``{"actions": [...]}``; trusted receipts and runtime
    state never cross this boundary.
    """

    def __init__(self, command: list[str], manifest: Mapping[str, Any], *,
                 timeout_s: float, cwd: str | None = None,
                 env: Mapping[str, str] | None = None,
                 episode_id: str | None = None):
        if not isinstance(command, list) or not command or any(
                not isinstance(item, str) or not item for item in command):
            raise ValueError("command must be a nonempty string list")
        self._command = list(command)
        self._manifest = validate_goal_adapter_manifest(manifest)
        self._timeout_s = timeout_s
        self._cwd = cwd
        self._env = dict(env) if env is not None else None
        self._episode_id = episode_id
        self._public_task: dict[str, Any] | None = None
        self._participant_api: dict[str, Any] | None = None
        self._tool_profile: dict[str, Any] | None = None

    def initialize_tools(self, profile: Mapping[str, Any]) -> None:
        """Optional companion interface; the child may ignore the profile."""
        self._tool_profile = deepcopy(dict(profile))

    def manifest(self) -> Mapping[str, Any]:
        return deepcopy(self._manifest)

    def initialize(self, public_task: Mapping[str, Any], participant_api: Mapping[str, Any]) -> None:
        self._public_task = deepcopy(dict(public_task))
        self._participant_api = deepcopy(dict(participant_api))

    def act(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if self._public_task is None or self._participant_api is None:
            raise RuntimeError("subprocess goal adapter was not initialized")
        context = {
            "episode_id": self._episode_id,
            "public_task": self._public_task,
            "participant_api": self._participant_api,
            "events": deepcopy(events),
        }
        if self._tool_profile is not None:
            context["tool_profile"] = deepcopy(self._tool_profile)
        result = run_algorithm_subprocess(
            self._command, manifest=self._manifest, context=context,
            timeout_s=self._timeout_s, cwd=self._cwd, env=self._env)
        if result["status"] != "completed":
            raise RuntimeError(f"goal algorithm subprocess failed: {result['failure_class']}: "
                               f"{result.get('stderr', '')}")
        output = result.get("output")
        if not isinstance(output, Mapping) or not isinstance(output.get("actions"), list):
            raise ValueError("goal algorithm subprocess output must contain an actions list")
        return deepcopy(list(output["actions"]))

    def close(self) -> None:
        return None


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2,
                                sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def _require_state(value: Mapping[str, Any], label: str) -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    robots = value.get("robot_regions")
    objects = value.get("object_states")
    if not isinstance(robots, Mapping) or not isinstance(objects, Mapping):
        raise ValueError(f"{label} must contain robot_regions and object_states")
    return dict(robots), {str(key): deepcopy(dict(item)) for key, item in objects.items()}


def _public_skill_observation(value: Any, task: Mapping[str, Any], status: str) -> dict[str, Any]:
    """Allow only measured, non-prescriptive facts across the participant boundary."""
    if status != "failed" or not isinstance(value, Mapping):
        raise ValueError("skill observation requires a failed backend task")
    fields = {"skill_succeeded", "object_moved", "task_advanced", "carrier_load_monitor"}
    if set(value) != fields or any(type(value[key]) is not bool for key in fields - {"carrier_load_monitor"}):
        raise ValueError("skill observation has unexpected or invalid fields")
    if value["skill_succeeded"] is not False or value["task_advanced"] is not False:
        raise ValueError("failed skill observation contradicts terminal status")
    readings = value["carrier_load_monitor"]
    if not isinstance(readings, list):
        raise ValueError("carrier load monitor must be a list")
    agents = set(task["assigned_agents"])
    seen: set[str] = set()
    for reading in readings:
        if not isinstance(reading, Mapping) or set(reading) != {
                "agent_id", "requested_load_kg", "effective_payload_kg"}:
            raise ValueError("carrier load monitor has unexpected fields")
        agent = reading["agent_id"]
        if not isinstance(agent, str) or agent not in agents or agent in seen:
            raise ValueError("carrier load monitor references an invalid member")
        seen.add(agent)
        for key in ("requested_load_kg", "effective_payload_kg"):
            number = reading[key]
            if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number) or number < 0:
                raise ValueError("carrier load monitor requires finite nonnegative readings")
    if readings and seen != agents:
        raise ValueError("carrier load monitor must cover all assigned members")
    return deepcopy(dict(value))


def team_usage_summary(trusted_report: Mapping[str, Any],
                       public_task: Mapping[str, Any]) -> dict[str, Any]:
    """Derive the standard per-episode team-usage record from trusted data.

    Score owners decide how (or whether) to score "using fewer robots"; this
    record only guarantees every episode archives the raw facts such a score
    needs. Everything is derived from the session ledger and the public task
    (never participant self-reports): the selected team, the agents the
    submitted plans actually assign, the agents that reached a dispatch, and
    a per-agent activation cost proxy summed over the active set (type table
    from the candidate-pool design when the scene provides one, 1.0
    otherwise - the source is declared either way).
    """
    nested = trusted_report.get("session")
    session = nested if isinstance(nested, Mapping) else trusted_report
    actions = session.get("actions") or []
    selected: list[str] = []
    for row in actions:
        if row.get("type") == "select_team":
            selected = [str(a) for a in (row.get("payload") or {}).get("agent_ids", [])]
            break
    plan_agents: set[str] = set()
    dispatched_agents: set[str] = set()
    plans = session.get("plans") or []
    plan_rows = list(plans.values()) if isinstance(plans, Mapping) else list(plans)
    for plan in plan_rows:
        for task in (plan.get("plan") or {}).get("tasks", []):
            plan_agents.update(str(a) for a in task.get("assigned_agents", []))
    for row in actions:
        if row.get("type") != "dispatch_skill":
            continue
        task_id = (row.get("payload") or {}).get("task_id")
        for plan in plan_rows:
            for task in (plan.get("plan") or {}).get("tasks", []):
                if task.get("task_id") == task_id:
                    dispatched_agents.update(str(a) for a in task.get("assigned_agents", []))
    pool_rows = public_task.get("candidate_robot_pool") or []
    cost_table: dict[str, float] = {}
    cost_source = "unit_default"
    try:
        from .candidate_pool_design import activation_cost as _pool_cost
        cost_table = {row["id"]: float(_pool_cost(row)) for row in pool_rows}
        cost_source = "candidate_pool_design.activation_cost"
    except Exception:
        cost_table = {}
    active = sorted(dispatched_agents)
    total_cost = round(sum(cost_table.get(a, 1.0) for a in active), 4)
    return {
        "schema": "eai.benchmark.goal_team_usage", "version": 1,
        "selected_team": selected,
        "selected_team_size": len(selected),
        "plan_assigned_agents": sorted(plan_agents),
        "active_agents": active,
        "active_team_size": len(active),
        "activation_cost_proxy": total_cost,
        "activation_cost_source": cost_source,
        "action_count": len(actions),
        "scoreable": False,
    }


class FormalGoalEpisodeRunner:
    """Run one trusted goal episode and archive its complete ledger."""

    def __init__(self, public_task: Mapping[str, Any], participant_api: Mapping[str, Any],
                 adapter: GoalAlgorithmAdapter, backend: GoalExecutionBackend, *,
                 output_root: str | Path, episode_id: str | None = None,
                 evaluator: BusinessEvaluator | None = None, max_turns: int = 256,
                 pose_tools: Any = None, camera_tools: Any = None,
                 communication_tools: Any = None,
                 assessment_evaluator: AssessmentEvaluator | None = None,
                 shared_communication_runtime: Any = None,
                 downlink: Any = None, execution_observer: Any = None,
                 session_audit: bool = False, progress_reader: Any = None):
        if not isinstance(max_turns, int) or isinstance(max_turns, bool) or max_turns < 1:
            raise ValueError("max_turns must be a positive integer")
        self.public_task = deepcopy(dict(public_task))
        self.participant_api = deepcopy(dict(participant_api))
        self.adapter = adapter
        self.backend = backend
        self.output_root = Path(output_root).absolute()
        self.episode_id = episode_id or uuid.uuid4().hex
        if not isinstance(self.episode_id, str) or not self.episode_id.strip():
            raise ValueError("episode_id must be a nonempty string")
        self.evaluator = evaluator
        self.assessment_evaluator = assessment_evaluator
        self.max_turns = max_turns
        self.pose_tools = pose_tools
        self.camera_tools = camera_tools
        self.communication_tools = communication_tools
        self.downlink = downlink
        self.execution_observer = execution_observer
        if type(session_audit) is not bool:
            raise ValueError("session_audit must be a bool")
        self.session_audit = session_audit
        if progress_reader is not None and not callable(progress_reader):
            raise ValueError("progress_reader must be a trusted host callable")
        self.progress_reader = progress_reader
        if pose_tools is not None and (
                pose_tools.episode_id != self.episode_id
                or pose_tools.public_task_sha256 != canonical_sha256(self.public_task)):
            raise ValueError("pose tools must be bound to this episode and public task")
        if communication_tools is not None and (
                communication_tools.episode_id != self.episode_id
                or communication_tools.public_task_sha256 != canonical_sha256(self.public_task)):
            raise ValueError(
                "communication tools must be bound to this episode and public task")
        if camera_tools is not None and (
                camera_tools.episode_id != self.episode_id
                or camera_tools.public_task_sha256 != canonical_sha256(self.public_task)):
            raise ValueError("camera tools must be bound to this episode and public task")
        # Shared-transport mode (route decision 2026-09-24): the runner holds
        # the one restricted CommunicationRuntime that the caller used to
        # build the pose/camera/communication tools.  The runner never
        # rebuilds tools; it only seals, validates, and counts that transport
        # archive once.  Refuse obviously mismatched wiring for the known tool
        # shapes so a private runtime cannot silently drop out of the totals.
        self.shared_communication_runtime = None
        if shared_communication_runtime is not None:
            from .communication import CommunicationRuntime
            if not isinstance(shared_communication_runtime, CommunicationRuntime):
                raise ValueError(
                    "shared_communication_runtime must be a CommunicationRuntime")
            self.shared_communication_runtime = shared_communication_runtime
            for label, tools, attributes in (
                    ("pose tools", self.pose_tools, ("telemetry", "runtime")),
                    ("camera tools", self.camera_tools, ("telemetry", "runtime")),
                    ("communication tools", self.communication_tools, ("runtime",))):
                holder: Any = tools
                for attribute in attributes:
                    holder = getattr(holder, attribute, None)
                if holder is not None and holder is not shared_communication_runtime:
                    raise ValueError(
                        f"{label} must reuse the shared communication runtime")
        expected_task = canonical_sha256(self.public_task)
        for label, component, methods in (
                ("execution downlink", self.downlink,
                 ("profile", "queue_dispatch", "queue_cancel", "advance",
                  "has_pending_work", "next_wakeup_s", "cancellation_state",
                  "report")),
                ("execution observer", self.execution_observer,
                 ("profile", "after_skill", "advance", "has_pending_work",
                  "next_wakeup_s", "report"))):
            if component is None:
                continue
            if self.shared_communication_runtime is None:
                raise ValueError(f"{label} requires a shared communication runtime")
            if (getattr(component, "episode_id", None) != self.episode_id
                    or getattr(component, "public_task_sha256", None) != expected_task):
                raise ValueError(
                    f"{label} must be bound to this episode and public task")
            if getattr(component, "runtime", None) is not self.shared_communication_runtime:
                raise ValueError(f"{label} must reuse the shared communication runtime")
            if any(not callable(getattr(component, name, None)) for name in methods):
                raise ValueError(f"{label} does not implement its required interface")

    def _evaluate_required_assessment(self, host: GoalAlgorithmHost) -> None:
        report = host.trusted_report()["session"]
        assessment = report.get("deployment_assessment")
        if (assessment is not None
                and assessment["status"] == "required"
                and assessment["external_evaluation"] is None):
            if self.assessment_evaluator is None:
                evaluation = {
                    "assessment_id": assessment["assessment_id"],
                    "assessment_epoch": assessment["assessment_epoch"],
                    "feasibility_label": None,
                    "oracle_status": "failed",
                    "platform_valid": False,
                    "evidence_refs": [],
                    "evaluator_ref": "oracle_not_configured",
                }
            else:
                try:
                    evaluation = self.assessment_evaluator(
                        self.episode_id, host.trusted_report())
                    host.record_deployment_assessment_evaluation(evaluation)
                except Exception:
                    # A broken hook is an infrastructure fault at this frozen
                    # opportunity, never a missing participant answer or a
                    # third feasibility class. Retain the original exception.
                    host.record_deployment_assessment_evaluation({
                        "assessment_id": assessment["assessment_id"],
                        "assessment_epoch": assessment["assessment_epoch"],
                        "feasibility_label": None,
                        "oracle_status": "failed",
                        "platform_valid": False,
                        "evidence_refs": [],
                        "evaluator_ref": "oracle_evaluation_failed",
                    })
                    raise
                return
            host.record_deployment_assessment_evaluation(evaluation)

    def run(self, *, initial_state: Mapping[str, Any]) -> dict[str, Any]:
        if self.output_root.exists():
            raise FileExistsError(f"episode output already exists: {self.output_root}")
        self.output_root.mkdir(parents=True)
        robots, objects = _require_state(initial_state, "initial_state")
        session = (
            AuditedGoalSession(
                self.public_task, self.participant_api, episode_id=self.episode_id)
            if self.session_audit else
            GoalSession(self.public_task, self.participant_api, episode_id=self.episode_id)
        )
        manifest = {
            "schema": "eai.benchmark.formal_goal_episode",
            "version": 3,
            "episode_id": self.episode_id,
            "task_id": self.public_task["task_id"],
            "scene_key": self.public_task["mission"]["scene_key"],
            "public_task_sha256": canonical_sha256(self.public_task),
            "participant_api_sha256": canonical_sha256(self.participant_api),
            "backend": type(self.backend).__name__,
            "execution_validated": False,
            "scoreable": False,
        }
        if self.session_audit:
            manifest["session_audit"] = {
                "enabled": True,
                "journal": "goal_session_audit.json",
                "replay": "goal_session_audit_replay.json",
                "execution_validated": False,
                "business_outcomes_validated": False,
                "scoreable": False,
            }
        if self.progress_reader is not None:
            manifest["progress_evidence"] = {
                "version": 1, "archive": "goal_progress_evidence.json",
                "sampling_scope": "runner_boundaries", "visibility": "evaluator_only",
                "physical_source_authenticated": False,
            }
        if self.pose_tools is not None:
            profile = self.pose_tools.public_profile()
            manifest["pose_tools"] = {
                "profile_sha256": canonical_sha256(profile),
                "sampling_scope": "runner_boundaries; no intra-skill fixed-rate claim",
            }
            _write_json(self.output_root / "pose_tool_profile.json", profile)
        if self.camera_tools is not None:
            profile = self.camera_tools.public_profile()
            manifest["camera_tools"] = {
                "profile_sha256": canonical_sha256(profile),
                "capture_scope": "owner_thread_on_request; idle_destroy render lifecycle",
            }
            _write_json(self.output_root / "camera_tool_profile.json", profile)
        if self.downlink is not None:
            profile = self.downlink.profile()
            if not isinstance(profile, Mapping):
                raise ValueError("execution downlink profile must be an object")
            manifest["execution_downlink"] = {
                "profile_sha256": canonical_sha256(profile),
                "visibility": "evaluator_only",
            }
            _write_json(self.output_root / "execution_downlink_profile.json", profile)
        if self.execution_observer is not None:
            profile = self.execution_observer.profile()
            if not isinstance(profile, Mapping):
                raise ValueError("execution observer profile must be an object")
            manifest["execution_observer"] = {
                "profile_sha256": canonical_sha256(profile),
                "visibility": "evaluator_only",
            }
            _write_json(self.output_root / "execution_observer_profile.json", profile)
        _write_json(self.output_root / "episode_manifest.json", manifest)
        _write_json(self.output_root / "public_task.json", self.public_task)
        _write_json(self.output_root / "participant_api.json", self.participant_api)
        snapshots = [{"episode_id": self.episode_id, "time_s": 0.0,
                      "robot_regions": deepcopy(robots), "object_states": deepcopy(objects)}]
        receipts: list[dict[str, Any]] = []
        business: dict[str, Any] = {}
        lifecycle_errors: list[dict[str, str]] = []
        error: str | None = None
        protocol_error: dict[str, Any] | None = None
        turns = 0
        host = None
        cancellation_attempts: set[tuple[str, str]] = set()
        downlink_ready: dict[str, dict[str, Any]] = {}
        session_audit_replay: dict[str, Any] | None = None
        progress = None
        if self.progress_reader is not None:
            from .goal_progress_evidence import GoalProgressRecorder
            progress = GoalProgressRecorder(self.public_task, self.episode_id)

        def sample_progress(boundary: str) -> None:
            if progress is None:
                return
            trusted = {"session": session.report()}
            host_time = trusted["session"]["current_time_s"]
            try:
                payload = self.progress_reader(
                    episode_id=self.episode_id, boundary=boundary,
                    trusted_report=deepcopy(trusted))
                progress.append(payload, boundary=boundary, host_time_s=host_time)
            except Exception as exc:
                # Instrumentation failure is not a participant action result.
                message = f"{type(exc).__name__}: {exc}"
                progress.record_error(boundary=boundary, host_time_s=host_time,
                                      error=message)
                lifecycle_errors.append({"component": "progress_reader",
                                         "operation": boundary, "error": message})

        def notify_after_skill(task: Mapping[str, Any], result: Mapping[str, Any]) -> None:
            try:
                if self.execution_observer is not None and host is not None:
                    self.execution_observer.after_skill(
                        deepcopy(dict(task)), deepcopy(dict(result)), host=host)
            finally:
                sample_progress("skill_completed")

        def complete_local_transport(outcome: Mapping[str, Any], *,
                                     status: str, disposition: str) -> None:
            """Record a real transport terminal without claiming native work."""
            nonlocal robots, objects
            task_id = outcome["task_id"]
            current = session.report()["tasks"].get(task_id)
            if current is None or current["status"] not in {"pending", "cancel_requested"}:
                return
            completed_at_s = session.report()["current_time_s"]
            message_id = outcome["message_id"]
            receipt = {
                "backend_id": "goal_execution_downlink_local",
                "receipt_ref": f"{message_id}:{disposition}",
                "task_id": task_id,
                "action_id": current["dispatch_action_id"],
                "status": status,
                "completed_at_s": completed_at_s,
                "episode_id": self.episode_id,
                "evidence_refs": [f"transport:{message_id}:{disposition}"],
            }
            completion = session.complete_skill(
                task_id, time_s=completed_at_s, status=status, receipt=receipt)
            if not completion.get("accepted"):
                raise ValueError(
                    f"transport terminal receipt was not accepted: {completion}")
            receipt_row = deepcopy(receipt)
            receipts.append(receipt_row)
            self.downlink.mark_terminal(
                task_id, dispatch_action_id=current["dispatch_action_id"],
                status=status, time_s=completed_at_s, disposition=disposition)
            notify_after_skill(current["task"], {
                "status": status, "completed_at_s": completed_at_s,
                "receipt": deepcopy(receipt),
                "transport_outcome": deepcopy(dict(outcome)),
            })

        def acknowledge_backend_cancel(row: Mapping[str, Any], *,
                                       transport_outcome: Mapping[str, Any] | None = None) -> None:
            nonlocal robots, objects
            task_id = row["task"]["task_id"]
            cancel = getattr(self.backend, "cancel", None)
            if not callable(cancel):
                raise ValueError("execution backend cannot acknowledge cancellation")
            trusted = host.trusted_report() if host is not None else {"session": session.report()}
            result = dict(cancel(row["task"], episode_id=self.episode_id,
                                 trusted_report=trusted))
            if result.get("status") != "cancelled":
                raise ValueError("cancellation backend must return cancelled status")
            completed_at_s = result.get("completed_at_s")
            if (isinstance(completed_at_s, bool)
                    or not isinstance(completed_at_s, (int, float))
                    or not math.isfinite(completed_at_s)
                    or completed_at_s < session.report()["current_time_s"]):
                raise ValueError(
                    "cancellation backend returned a nonmonotonic completion time")
            state = result.get("runtime_state")
            if state is not None:
                next_robots, next_objects = _require_state(state, "runtime_state")
                session.validate_runtime_state(completed_at_s, next_robots, next_objects)
            receipt = result.get("receipt")
            completion = session.complete_skill(
                task_id, time_s=completed_at_s, status="cancelled", receipt=receipt)
            if not completion.get("accepted"):
                raise ValueError(
                    f"backend cancellation receipt was not accepted: {completion}")
            receipts.append({**deepcopy(dict(receipt)), "episode_id": self.episode_id})
            if state is not None:
                robots, objects = next_robots, next_objects
                session.update_runtime_state(completed_at_s, robots, objects)
                snapshots.append({"episode_id": self.episode_id,
                                  "time_s": completed_at_s,
                                  "robot_regions": deepcopy(robots),
                                  "object_states": deepcopy(objects)})
            if self.downlink is not None:
                self.downlink.mark_terminal(
                    task_id, dispatch_action_id=row["dispatch_action_id"],
                    status="cancelled", time_s=completed_at_s,
                    disposition="physical_stop")
            observer_result = deepcopy(result)
            if transport_outcome is not None:
                observer_result["transport_outcome"] = deepcopy(
                    dict(transport_outcome))
            notify_after_skill(row["task"], observer_result)

        def advance_execution_downlink() -> None:
            if self.downlink is None:
                return
            report = session.report()
            now = report["current_time_s"]
            for row in report["tasks"].values():
                if row["status"] not in {"pending", "cancel_requested"}:
                    continue
                self.downlink.queue_dispatch(
                    row["task"], dispatch_action_id=row["dispatch_action_id"],
                    time_s=now)
                if row["status"] == "cancel_requested":
                    self.downlink.queue_cancel(
                        row["task"], cancel_action_id=row.get("cancel_action_id"),
                        time_s=now)
                    downlink_ready.pop(row["task"]["task_id"], None)
            assessment = report.get("deployment_assessment") or {}
            outcomes = self.downlink.advance(
                now, active_agent_ids=report["current_agents"],
                task_states=report["tasks"],
                terminated=bool(assessment.get("terminated")))
            for outcome in outcomes["failed_dispatches"]:
                downlink_ready.pop(outcome["task_id"], None)
                current = session.report()["tasks"].get(outcome["task_id"])
                if current is not None and current["status"] == "pending":
                    complete_local_transport(
                        outcome, status="failed", disposition="transport_delivery_failed")
            for outcome in outcomes["ready_cancels"]:
                current = session.report()["tasks"].get(outcome["task_id"])
                if current is None or current["status"] != "cancel_requested":
                    continue
                try:
                    if outcome["disposition"] == "local_unstarted_withdrawal":
                        complete_local_transport(
                            outcome, status="cancelled",
                            disposition="local_unstarted_withdrawal")
                    else:
                        acknowledge_backend_cancel(
                            current, transport_outcome=outcome)
                except Exception as exc:
                    lifecycle_errors.append({
                        "component": "execution_backend",
                        "operation": "cancel",
                        "task_id": outcome["task_id"],
                        "error": f"{type(exc).__name__}: {exc}",
                    })
            for outcome in outcomes["failed_cancels"]:
                lifecycle_errors.append({
                    "component": "execution_downlink",
                    "operation": "cancel_delivery",
                    "task_id": outcome["task_id"],
                    "error": ("RuntimeError: cancellation was not delivered: "
                              f"{outcome['reason']}"),
                })
            current_report = session.report()
            for outcome in outcomes["ready_dispatches"]:
                current = current_report["tasks"].get(outcome["task_id"])
                if current is not None and current["status"] == "pending":
                    downlink_ready[outcome["task_id"]] = outcome

        def drain_safety_stops() -> None:
            # Safety acknowledgement belongs to the trusted runner, even after
            # an adapter failure has closed the participant-facing host.
            nonlocal robots, objects
            if host is None:
                return
            requested = [row for row in session.report()["tasks"].values()
                         if row["status"] == "cancel_requested"]
            for row in requested:
                task_id = row["task"]["task_id"]
                attempt_key = (task_id, row["dispatch_action_id"])
                if attempt_key in cancellation_attempts:
                    continue
                if self.downlink is not None:
                    self.downlink.queue_dispatch(
                        row["task"], dispatch_action_id=row["dispatch_action_id"],
                        time_s=session.report()["current_time_s"])
                    self.downlink.queue_cancel(
                        row["task"], cancel_action_id=row.get("cancel_action_id"),
                        time_s=session.report()["current_time_s"])
                    downlink_ready.pop(task_id, None)
                    continue
                cancellation_attempts.add(attempt_key)
                try:
                    acknowledge_backend_cancel(row)
                except Exception as exc:
                    # Do not lose the original participant error, fabricate an
                    # acknowledgement, or skip the remaining robots' stops.
                    lifecycle_errors.append({
                        "component": "execution_backend", "operation": "cancel",
                        "task_id": task_id, "error": f"{type(exc).__name__}: {exc}",
                    })

        def require_acknowledged_stops() -> None:
            requested = [row for row in session.report()["tasks"].values()
                         if row["status"] == "cancel_requested"]
            if not requested:
                return
            if self.downlink is not None:
                states = {
                    row["task"]["task_id"]: self.downlink.cancellation_state(
                        row["task"]["task_id"],
                        dispatch_action_id=row["dispatch_action_id"])
                    for row in requested
                }
                if all(state["status"] == "pending" for state in states.values()):
                    return
                failed = {task_id: state for task_id, state in states.items()
                          if state["status"] != "pending"}
                transport_details = "; ".join(
                    f"{task_id}:{state['status']}:"
                    f"{state.get('failure_reason') or 'not_acknowledged'}"
                    for task_id, state in sorted(failed.items()))
            else:
                transport_details = ""
            details = "; ".join(row["error"] for row in lifecycle_errors
                                if row["operation"] in {
                                    "cancel", "cancel_delivery"})
            details = "; ".join(part for part in (transport_details, details) if part)
            raise RuntimeError(f"safety cancellation was not acknowledged: {details}")

        def request_actions() -> list[dict[str, Any]]:
            if self.execution_observer is not None:
                self.execution_observer.advance(host=host)
            try:
                # Freeze the measurement before cancellation or participant
                # behavior can change the current assessment's evidence.
                self._evaluate_required_assessment(host)
            finally:
                drain_safety_stops()
                advance_execution_downlink()
            require_acknowledged_stops()
            try:
                actions = host.request_actions()
            finally:
                drain_safety_stops()
                advance_execution_downlink()
            require_acknowledged_stops()
            return actions

        def advance_idle_work() -> bool:
            """Advance only through the backend's reported physical clock."""
            nonlocal robots, objects
            idle = getattr(self.backend, "advance_idle", None)
            pose_pending = (self.pose_tools is not None
                            and self.pose_tools.has_pending_work())
            camera_pending = (self.camera_tools is not None
                              and self.camera_tools.has_pending_work())
            comm_pending = (self.communication_tools is not None
                            and self.communication_tools.has_pending_work())
            downlink_pending = (self.downlink is not None
                                and self.downlink.has_pending_work())
            observer_pending = (self.execution_observer is not None
                                and self.execution_observer.has_pending_work())
            waiting = (pose_pending or camera_pending or comm_pending
                       or downlink_pending or observer_pending)
            if not waiting:
                return False
            labels = []
            for active, label in (
                    (pose_pending, "pose tools"),
                    (camera_pending, "camera tools"),
                    (comm_pending, "communication tools"),
                    (downlink_pending, "execution downlink"),
                    (observer_pending, "execution observer")):
                if active:
                    labels.append(label)
            if not callable(idle):
                raise RuntimeError(
                    f"{' and '.join(labels)} are waiting but the backend cannot advance its idle clock")
            now = session.report()["current_time_s"]
            wakeups: list[float] = []
            if pose_pending:
                wake = self.pose_tools.next_wakeup_s()
                if wake is None:
                    wake = now + self.pose_tools.period_s
                wakeups.append(float(wake))
            for active, component in (
                    (camera_pending, self.camera_tools),
                    (comm_pending, self.communication_tools),
                    (downlink_pending, self.downlink),
                    (observer_pending, self.execution_observer)):
                if active:
                    wake = component.next_wakeup_s()
                    if wake is None:
                        wake = now + 1e-6
                    wakeups.append(float(wake))
            budget = float(self.public_task["budgets"]["simulation_s"])
            next_time = min(min(wakeups), budget)
            if next_time <= now:
                next_time = min(now + 1e-6, budget)
            if next_time <= now:
                if camera_pending:
                    self.camera_tools.finalize_episode(budget)
                return False
            idle_result = idle(next_time)
            actual_time = (idle_result.get("time_s")
                           if isinstance(idle_result, Mapping) else None)
            if (isinstance(actual_time, bool)
                    or not isinstance(actual_time, (int, float))
                    or not math.isfinite(actual_time)
                    or not now < actual_time <= budget):
                raise ValueError(
                    "idle backend must report its actual advancing episode time")
            idle_state = idle_result.get("runtime_state")
            robots, objects = _require_state(idle_state, "idle runtime state")
            host.update_runtime_state(actual_time, robots, objects)
            digest_labels = []
            for active, label in (
                    (pose_pending, "pose_tool_idle"),
                    (camera_pending, "camera_tool_idle"),
                    (comm_pending, "communication_idle"),
                    (downlink_pending, "execution_downlink_idle"),
                    (observer_pending, "execution_observer_idle")):
                if active:
                    digest_labels.append(label)
            host.emit("tick", actual_time, {
                "state_digest": "+".join(digest_labels)})
            snapshots.append({"episode_id": self.episode_id, "time_s": actual_time,
                              "robot_regions": deepcopy(robots),
                              "object_states": deepcopy(objects)})
            sample_progress("idle")
            if self.execution_observer is not None:
                self.execution_observer.advance(host=host)
            drain_safety_stops()
            advance_execution_downlink()
            return True

        try:
            session.update_runtime_state(0.0, robots, objects)
            sample_progress("initial")
            with GoalAlgorithmHost(self.public_task, self.participant_api, self.adapter,
                                    session=session, pose_tools=self.pose_tools,
                                    camera_tools=self.camera_tools,
                                    communication_tools=self.communication_tools,
                                    observation_consumer=(
                                        self.execution_observer if all(
                                            callable(getattr(self.execution_observer, name, None))
                                            for name in ("owns_message", "can_consume", "consume"))
                                        else None)) as host:
                for turns in range(1, self.max_turns + 1):
                    request_actions()
                    advance_execution_downlink()
                    report = host.trusted_report()["session"]
                    assessment = report.get("deployment_assessment")
                    pending = [row for row in report["tasks"].values()
                               if row["status"] in {"pending", "cancel_requested"}]
                    if not pending:
                        if assessment is not None and assessment["terminated"]:
                            break
                        # A final adapter turn may still consume terminal receipts.
                        if turns > 1:
                            followup_actions = request_actions()
                            report = host.trusted_report()["session"]
                            assessment = report.get("deployment_assessment")
                            pending = [row for row in report["tasks"].values()
                                       if row["status"] in {"pending", "cancel_requested"}]
                            if assessment is not None and assessment["terminated"]:
                                break
                            if pending:
                                pass
                            elif followup_actions:
                                continue
                            # Optional backends can keep a real simulation clock
                            # moving while a model waits for a delayed query.
                            else:
                                if advance_idle_work():
                                    continue
                                if (assessment is not None
                                        and assessment["status"] == "required"):
                                    raise DeploymentAssessmentProtocolError(
                                        "missing_deployment_assessment: participant became idle "
                                        f"during required epoch {assessment['assessment_epoch']}")
                                break
                        continue
                    # A synchronous backend advances the same physical scene;
                    # never infer overlapping completion times from plan order.
                    if self.downlink is not None:
                        row = next((candidate for candidate in pending
                                    if candidate["status"] == "pending"
                                    and candidate["task"]["task_id"] in downlink_ready),
                                   None)
                        if row is None:
                            if advance_idle_work():
                                continue
                            require_acknowledged_stops()
                            raise RuntimeError(
                                "pending execution has no delivered downlink command")
                        downlink_outcome = downlink_ready.pop(
                            row["task"]["task_id"])
                    else:
                        row = pending[0]
                        downlink_outcome = None
                    trusted = host.trusted_report()
                    require_acknowledged_stops()
                    if self.downlink is not None:
                        self.downlink.mark_started(
                            row["task"]["task_id"],
                            dispatch_action_id=row["dispatch_action_id"],
                            time_s=trusted["session"]["current_time_s"])
                    result = dict(self.backend.execute(
                        row["task"], episode_id=self.episode_id,
                        trusted_report=trusted))
                    status = result.get("status")
                    completed_at_s = result.get("completed_at_s")
                    receipt = result.get("receipt")
                    if status not in {"succeeded", "failed", "cancelled"}:
                        raise ValueError("execution backend must return a terminal status")
                    if (isinstance(completed_at_s, bool)
                            or not isinstance(completed_at_s, (int, float))
                            or not math.isfinite(completed_at_s)
                            or completed_at_s < trusted["session"]["current_time_s"]):
                        raise ValueError("serial execution backend returned a nonmonotonic completion time")
                    observation = (_public_skill_observation(
                        result["public_observation"], row["task"], status)
                        if "public_observation" in result else None)
                    state = result.get("runtime_state")
                    if state is not None:
                        next_robots, next_objects = _require_state(state, "runtime_state")
                        session.validate_runtime_state(completed_at_s, next_robots, next_objects)
                    completion = host.complete_skill(row["task"]["task_id"],
                                                     time_s=completed_at_s,
                                                     status=status, receipt=receipt)
                    if not completion.get("accepted"):
                        raise ValueError(f"backend receipt was not accepted: {completion}")
                    receipt_row = deepcopy(dict(receipt))
                    receipt_row["episode_id"] = self.episode_id
                    receipts.append(receipt_row)
                    if state is not None:
                        robots, objects = next_robots, next_objects
                        host.update_runtime_state(completed_at_s, robots, objects)
                        snapshots.append({"episode_id": self.episode_id,
                                          "time_s": completed_at_s,
                                          "robot_regions": deepcopy(robots),
                                          "object_states": deepcopy(objects)})
                    if self.downlink is not None:
                        self.downlink.mark_terminal(
                            row["task"]["task_id"],
                            dispatch_action_id=row["dispatch_action_id"],
                            status=status, time_s=completed_at_s)
                        result["transport_outcome"] = deepcopy(downlink_outcome)
                    notify_after_skill(row["task"], result)
                    if observation is not None:
                        host.emit("observation", completed_at_s, {
                            "message_id": f"skill:{row['dispatch_action_id']}",
                            "producer_id": "trusted_skill_backend",
                            "kind": "skill_execution",
                            "data": {"action_id": row["dispatch_action_id"], **observation},
                            "valid_until_s": max(completed_at_s,
                                self.public_task["budgets"]["simulation_s"]),
                        })
                else:
                    final_assessment = host.trusted_report()["session"].get(
                        "deployment_assessment")
                    if (final_assessment is not None
                            and final_assessment["status"] == "required"):
                        raise DeploymentAssessmentProtocolError(
                            "missing_deployment_assessment: max_turns reached "
                            f"during required epoch {final_assessment['assessment_epoch']}")
                    raise RuntimeError("formal goal episode exceeded max_turns")
                final_assessment = host.trusted_report()["session"].get(
                    "deployment_assessment")
                sample_progress("execution_stopped")
                if (self.evaluator is not None
                        and not (final_assessment or {}).get("terminated", False)):
                    evaluations = self.evaluator(self.episode_id, host.trusted_report())
                    if not isinstance(evaluations, Mapping):
                        raise ValueError("business evaluator must return an object")
                    required_outcomes = {
                        row["id"] for row in self.public_task["required_outcomes"]
                    }
                    if set(evaluations) != required_outcomes:
                        raise ValueError(
                            "business evaluator must return every required outcome exactly"
                        )
                    for outcome_id, row in evaluations.items():
                        data = dict(row)
                        result = host.record_business_outcome(
                            outcome_id, status=data["status"],
                            evidence_refs=list(data["evidence_refs"]),
                            evaluator_ref=data["evaluator_ref"])
                        if not result.get("accepted"):
                            raise ValueError(f"business outcome was not accepted: {result}")
                        business[outcome_id] = data
                    sample_progress("business_evaluated")
                final_report = host.trusted_report()["session"]
        except DeploymentAssessmentProtocolError as exc:
            error = f"{type(exc).__name__}: {exc}"
            current = session.report()["deployment_assessment"]
            protocol_error = {
                "category": "protocol_error",
                "code": "missing_assessment",
                "assessment_id": current["assessment_id"],
                "assessment_epoch": current["assessment_epoch"],
                "message": str(exc),
            }
            final_report = session.report()
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            final_report = session.report()

        drain_safety_stops()
        final_report = session.report()
        sample_progress("terminal")
        if host is not None:
            lifecycle_errors.extend(host.cleanup_errors)

        if protocol_error is not None:
            _write_json(self.output_root / "deployment_protocol_error.json", protocol_error)
        assessment_report = getattr(self.assessment_evaluator, "report", None)
        if callable(assessment_report):
            try:
                evidence = assessment_report()
                if not isinstance(evidence, Mapping):
                    raise ValueError("deployment assessment evaluator report must be an object")
                _write_json(self.output_root / "deployment_feasibility_evidence.json", evidence)
            except Exception as exc:
                lifecycle_errors.append({
                    "component": "deployment_assessment_evaluator",
                    "operation": "report",
                    "error": f"{type(exc).__name__}: {exc}",
                })
        if self.downlink is not None:
            try:
                report = self.downlink.report()
                if not isinstance(report, Mapping):
                    raise ValueError("execution downlink report must be an object")
                _write_json(self.output_root / "execution_downlink_report.json", report)
            except Exception as exc:
                lifecycle_errors.append({
                    "component": "execution_downlink", "operation": "report",
                    "error": f"{type(exc).__name__}: {exc}",
                })
        if self.execution_observer is not None:
            try:
                report = self.execution_observer.report()
                if not isinstance(report, Mapping):
                    raise ValueError("execution observer report must be an object")
                _write_json(self.output_root / "execution_observer_report.json", report)
            except Exception as exc:
                lifecycle_errors.append({
                    "component": "execution_observer", "operation": "report",
                    "error": f"{type(exc).__name__}: {exc}",
                })

        _write_json(self.output_root / "runtime_snapshots.json", snapshots)
        _write_json(self.output_root / "skill_receipts.json", receipts)
        _write_json(self.output_root / "business_evidence.json", {
            "episode_id": self.episode_id, "outcomes": business,
            "execution_validated": False, "scoreable": False})
        _write_json(self.output_root / "team_usage.json",
                    team_usage_summary(final_report, self.public_task))

        def record_lifecycle_error(component: str, operation: str, exc: Exception) -> None:
            lifecycle_errors.append({
                "component": component,
                "operation": operation,
                "error": f"{type(exc).__name__}: {exc}",
            })

        communication_reports: dict[str, Any] = {}
        if self.pose_tools is not None:
            from .communication import validate_communication_report
            try:
                tool_report = self.pose_tools.report()
                _write_json(self.output_root / "pose_tools_report.json", tool_report)
                _write_json(self.output_root / "pose_communication_replay.json",
                            validate_communication_report(tool_report["communication"]))
                communication_reports["pose_tools"] = tool_report["communication"]
            except Exception as exc:
                record_lifecycle_error("pose_tools", "report", exc)
        if self.camera_tools is not None:
            from .communication import validate_communication_report
            finalizer = getattr(self.camera_tools, "finalize_episode", None)
            if callable(finalizer):
                try:
                    finalizer()
                except Exception as exc:
                    record_lifecycle_error("camera_tools", "finalize_episode", exc)
            closer = getattr(self.camera_tools, "close", None)
            if callable(closer):
                try:
                    closer()
                except Exception as exc:
                    record_lifecycle_error("camera_tools", "close", exc)
            tool_report = None
            try:
                tool_report = self.camera_tools.report()
                _write_json(self.output_root / "camera_tools_report.json", tool_report)
                _write_json(self.output_root / "camera_communication_replay.json",
                            validate_communication_report(tool_report["communication"]))
                if isinstance(tool_report, Mapping) and isinstance(
                        tool_report.get("communication"), Mapping):
                    communication_reports["camera_tools"] = tool_report["communication"]
            except Exception as exc:
                record_lifecycle_error("camera_tools", "report", exc)
            if isinstance(tool_report, Mapping) and tool_report.get("lifecycle_errors"):
                _write_json(self.output_root / "camera_lifecycle_errors.json",
                            tool_report["lifecycle_errors"])
            if isinstance(tool_report, Mapping):
                render_report = tool_report.get("render_lifecycle") or {}
                if render_report.get("cleanup_pending"):
                    record_lifecycle_error("camera_tools", "render_cleanup",
                                           RuntimeError("owned render products still pending cleanup"))
                if render_report.get("failures"):
                    _write_json(self.output_root / "camera_render_failures.json",
                                render_report["failures"])
        if self.communication_tools is not None:
            from .communication import validate_communication_report
            try:
                _write_json(self.output_root / "communication_tool_report.json",
                            self.communication_tools.report())
                replay = validate_communication_report(
                    self.communication_tools.communication_report())
                _write_json(self.output_root / "communication_tool_replay.json", replay)
                # A replay mismatch is an infrastructure failure, never a
                # silently accepted ledger: it stays out of the aggregation.
                if replay.get("validated") is not True:
                    record_lifecycle_error(
                        "communication_tools", "replay",
                        RuntimeError(
                            "communication replay failed: "
                            + "; ".join(replay.get("mismatches") or ["unknown"])))
                else:
                    communication_reports["communication_tools"] = (
                        self.communication_tools.communication_report())
            except Exception as exc:
                record_lifecycle_error("communication_tools", "report", exc)
        transport_report: dict[str, Any] | None = None
        if self.shared_communication_runtime is not None:
            from .communication import validate_communication_report
            try:
                candidate = self.shared_communication_runtime.report()
                _write_json(self.output_root / "communication_transport_report.json",
                            candidate)
                replay = validate_communication_report(candidate)
                _write_json(self.output_root / "communication_transport_replay.json",
                            replay)
                # Same gate as communication_tool_replay: the sealed shared
                # transport archive enters the aggregation only when its
                # deterministic replay validates.
                if replay.get("validated") is not True:
                    record_lifecycle_error(
                        "communication_transport", "replay",
                        RuntimeError(
                            "communication transport replay failed: "
                            + "; ".join(replay.get("mismatches") or ["unknown"])))
                else:
                    transport_report = candidate
            except Exception as exc:
                record_lifecycle_error("communication_transport", "report", exc)
        if self.shared_communication_runtime is not None:
            # Shared-transport mode: the one sealed transport ledger is the
            # only authority for the episode totals.  Tool ledgers are
            # duplicate views of the same runtime and are never summed as a
            # fallback, so a refused transport archive means no analysis
            # rather than a multiple-counted one.
            if transport_report is not None:
                try:
                    from .goal_communication_archive import build_communication_analysis

                    _write_json(self.output_root / "communication_analysis.json",
                                build_communication_analysis(
                                    communication_reports, episode_id=self.episode_id,
                                    scene_key=self.public_task["mission"]["scene_key"],
                                    evidence_class="contract",
                                    transport_report=transport_report))
                except Exception as exc:
                    record_lifecycle_error("communication_archive", "analyze", exc)
        elif communication_reports:
            # P1-COMM formal episode hookup: fold the replay-validated tool
            # ledgers into one sidecar-visible analysis archive.
            try:
                from .goal_communication_archive import build_communication_analysis

                _write_json(self.output_root / "communication_analysis.json",
                            build_communication_analysis(
                                communication_reports, episode_id=self.episode_id,
                                scene_key=self.public_task["mission"]["scene_key"],
                                evidence_class="contract"))
            except Exception as exc:
                record_lifecycle_error("communication_archive", "analyze", exc)
        if self.session_audit:
            audit_journal = None
            try:
                final_report = session.report()
                audit_journal = session.audit_journal()
                _write_json(
                    self.output_root / "goal_session_audit.json", audit_journal)
                session_audit_replay = replay_goal_session_audit(
                    audit_journal, self.public_task, self.participant_api)
                _write_json(
                    self.output_root / "goal_session_audit_replay.json",
                    session_audit_replay)
            except Exception as exc:
                session_audit_replay = {
                    "schema": GOAL_SESSION_AUDIT_REPLAY_SCHEMA,
                    "version": GOAL_SESSION_AUDIT_VERSION,
                    "episode_id": self.episode_id,
                    "journal_sha256": (
                        audit_journal.get("journal_sha256")
                        if isinstance(audit_journal, Mapping) else None),
                    "calls_replayed": 0,
                    "returned_calls_replayed": 0,
                    "raised_calls_replayed": 0,
                    "final_report_sha256": canonical_sha256(session.report()),
                    "validated": False,
                    "error": f"{type(exc).__name__}: {exc}",
                    "execution_validated": False,
                    "business_outcomes_validated": False,
                    "scoreable": False,
                    "integrity_scope": (
                        "internal_consistency_not_physical_authentication"),
                }
                record_lifecycle_error(
                    "goal_session_audit", "build_write_replay", exc)
                try:
                    _write_json(
                        self.output_root / "goal_session_audit_replay.json",
                        session_audit_replay)
                except Exception as archive_exc:
                    record_lifecycle_error(
                        "goal_session_audit", "write_failure_receipt", archive_exc)
        if progress is not None:
            from .goal_progress_evidence import replay_progress_evidence
            assessment = final_report.get("deployment_assessment") or {}
            reason = ("human_assistance_requested" if assessment.get("terminated")
                      else "execution_error" if error is not None
                      else "participant_stopped")
            progress_report = progress.report(
                termination_reason=reason,
                host_cutoff_s=final_report["current_time_s"])
            _write_json(self.output_root / "goal_progress_evidence.json", progress_report)
            _write_json(self.output_root / "goal_progress_replay.json",
                        replay_progress_evidence(progress_report, self.public_task,
                                                 episode_id=self.episode_id))
        if lifecycle_errors:
            _write_json(self.output_root / "lifecycle_errors.json", lifecycle_errors)
            if error is None:
                error = "lifecycle_error: " + "; ".join(
                    row["error"] for row in lifecycle_errors)
        _write_json(self.output_root / "participant_trace.json", {
            "episode_id": self.episode_id,
            "events": deepcopy(final_report.get("events", [])),
            "actions": deepcopy(final_report.get("actions", [])),
        })
        hidden_trace = {
            "episode_id": self.episode_id,
            "plans": deepcopy(final_report.get("plans", [])),
            "tasks": deepcopy(final_report.get("tasks", {})),
            "business_outcomes": deepcopy(final_report.get("business_outcomes", [])),
            "runtime_state": deepcopy(final_report.get("runtime_state")),
        }
        if "deployment_assessment" in final_report:
            hidden_trace["deployment_assessment"] = deepcopy(
                final_report["deployment_assessment"])
        _write_json(self.output_root / "hidden_evaluator_trace.json", hidden_trace)
        _write_json(self.output_root / "goal_session_report.json", {
            "episode_id": self.episode_id, "report": final_report})
        # Preserve a separate raw-only cost archive for downstream protocol
        # review.  The helper reads only trusted host records and leaves
        # unavailable physical/compute sources as null.
        from .goal_cost_evidence import build_cost_evidence, build_cost_source_bindings
        task_sha256 = canonical_sha256(self.public_task)
        _write_json(
            self.output_root / "goal_cost_source_bindings.json",
            build_cost_source_bindings(
                self.output_root, episode_id=self.episode_id,
                task_sha256=task_sha256),
        )
        _write_json(self.output_root / "cost_evidence.json",
                    build_cost_evidence(
                        self.output_root, episode_id=self.episode_id,
                        task_sha256=task_sha256))
        # R2 declaration-boundary cost snapshot: freeze pre-declaration facts
        # at each deployment decision with actual call-order semantics.
        from .goal_declaration_cost_snapshot import (
            build_declaration_cost_snapshots,
        )
        _write_json(self.output_root / "declaration_cost_snapshots.json",
                    build_declaration_cost_snapshots(
                        {"episode_id": self.episode_id, "report": final_report},
                        communication_report=(
                            self.communication_tools.communication_report()
                            if self.communication_tools is not None else None)))
        deployment_assessment = final_report.get("deployment_assessment")
        result = {
            "schema": "eai.benchmark.formal_goal_episode_result", "version": 3,
            "episode_id": self.episode_id, "task_id": self.public_task["task_id"],
            "scene_key": self.public_task["mission"]["scene_key"],
            "turns": turns, "error": error,
            "skill_receipt_count": len(receipts),
            "business_outcomes_validated": bool(business) and all(
                row.get("status") == "validated" for row in business.values())
                and not (deployment_assessment or {}).get("enabled", False)
                and not self.session_audit
                and self.progress_reader is None
                and self.downlink is None and self.execution_observer is None,
            "execution_validated": False, "scoreable": False,
            "output_root": str(self.output_root),
        }
        if deployment_assessment is not None:
            result["termination"] = deepcopy(deployment_assessment.get("termination"))
        if self.session_audit:
            result["session_audit_validated"] = (
                session_audit_replay is not None
                and session_audit_replay.get("validated") is True)
        _write_json(self.output_root / "result.json", result)
        from .goal_scoring_adapter import build_scoring_adapter_metadata
        scoring_metadata = build_scoring_adapter_metadata(
            self.public_task,
            episode_id=self.episode_id,
            output_root=self.output_root,
        )
        scoring_metadata["episode_result"] = {
            "error": result["error"],
            "turns": result["turns"],
            "skill_receipt_count": result["skill_receipt_count"],
            "business_outcomes_validated": result["business_outcomes_validated"],
            "lifecycle_error_count": len(lifecycle_errors),
        }
        _write_json(self.output_root / "scoring_adapter_metadata.json", scoring_metadata)
        return result


__all__ = [
    "DeploymentAssessmentProtocolError", "FormalGoalEpisodeRunner",
    "GoalExecutionBackend", "SubprocessGoalAdapter",
]
