"""New-host episode driver for the ported on-demand camera skill.

This module is the resurrection target of the retired ``demo/benchmark``
episode machinery, reduced to the camera-relevant slice the observation
skill actually needs (task book §三 host mapping):

| retired host piece                          | replacement here                       |
| ------------------------------------------- | -------------------------------------- |
| ``GoalSession`` event/action ledger         | :class:`ObservationSession`            |
| ``GoalAlgorithmHost`` (bind / opt-in / pump | :class:`ObservationEpisodeDriver` host |
| / member_join routing / first close)        | methods                                |
| ``FormalGoalEpisodeRunner`` (idle wake loop | :class:`ObservationEpisodeDriver.run`  |
| / guarded teardown / archives)              |                                        |
| ``goal_scoring_adapter`` metadata sidecar   | lean ``scoring_adapter_metadata.json`` |

Retired semantics that are preserved verbatim:

* the camera companion stays opt-in: a participant without an
  ``initialize_camera_tools`` hook gets zero pumped frames and cannot
  submit camera calls;
* member_join activation is authenticated against committed session
  events by index, idempotent per index, and delivers a profile delta
  via ``update_camera_tools`` or a public observation event;
* idle turns wake exactly at ``next_wakeup_s()`` (deadline / transport
  service times), never at a fixed step, and hit the simulation budget
  by finalizing pending camera work;
* teardown runs ``finalize_episode`` → ``close`` → ``report`` with every
  step guarded; the host context performs an earlier ``close`` attempt,
  and every retained failure lands in ``lifecycle_errors.json`` and the
  episode error string;
* ``execution_validated`` / ``scoreable`` are hard-coded ``False``: this
  driver never promotes a run to a formal result.
"""

from __future__ import annotations

from copy import deepcopy
import json
import math
from pathlib import Path
from typing import Any, Mapping

from algorithm.skills.observation.tools import (
    CAMERA_TOOL_SCHEMA,
    GoalCameraTools,
    canonical_sha256,
)

OBSERVATION_EPISODE_SCHEMA = "eai.benchmark.observation_episode"
OBSERVATION_EPISODE_RESULT_SCHEMA = "eai.benchmark.observation_episode_result"
OBSERVATION_SCORING_ADAPTER_SCHEMA = "eai.benchmark.observation_scoring_adapter"

_LOW_LEVEL_ACTION_TYPES = frozenset({
    "cmd_vel", "velocity_command", "raw_velocity", "trajectory", "raw_trajectory",
    "joint_command", "joint_positions", "joint_torques", "grasp_command",
    "low_level_action", "controller_action",
})


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, sort_keys=True, indent=2), encoding="utf-8")


def _require_state(value: Any, label: str) -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    robots = value.get("robot_regions")
    objects = value.get("object_states")
    if not isinstance(robots, Mapping) or not all(isinstance(k, str) for k in robots):
        raise ValueError(f"{label}.robot_regions must map robot ids to regions")
    if not isinstance(objects, Mapping):
        raise ValueError(f"{label}.object_states must map object ids to states")
    return deepcopy(dict(robots)), deepcopy(dict(objects))


class ObservationSession:
    """Trusted event/action ledger for one observation-only episode.

    The participant never receives this object; it only sees event copies.
    Committed events (by index) authenticate ``member_join`` activations on
    the camera tools, which is the fail-closed replacement for the retired
    ``GoalSession`` binding.
    """

    def __init__(self, episode_id: str, initial_agents: set[str] | None = None) -> None:
        self.episode_id = episode_id
        self._events: list[dict[str, Any]] = []
        self._actions: list[dict[str, Any]] = []
        self._message_ids: set[str] = set()
        self._joined_agents: set[str] = set()
        self._time_s = 0.0
        # The retired GoalSession activated the full candidate pool at
        # construction; manual host driving relies on that initial roster.
        self._agents: set[str] = set(initial_agents or ())
        # Initial public declarations carry capability version zero; every
        # join must strictly increase the agent's last committed version.
        self._capability_versions: dict[str, int] = {
            agent: 0 for agent in self._agents}
        # The retired GoalSession committed episode_started at construction;
        # the participant's first act() turn depends on it existing.
        self._events.append({
            "type": "episode_started",
            "time_s": 0.0,
            "payload": {"episode_id": episode_id},
        })

    def emit(self, type: str, time_s: float, payload: Mapping[str, Any]) -> dict[str, Any]:
        now = float(time_s)
        if now < self._time_s:
            raise ValueError("trusted clock cannot move backwards")
        self._time_s = now
        # The retired GoalSession activated a joined member in its roster at
        # emit time and never dropped it on a later runtime-state update;
        # camera inactivity checks read that roster.  Joins must strictly
        # increase the agent's capability version.
        if type == "member_join" and isinstance(payload, Mapping):
            agent_id = payload.get("agent_id")
            if not (isinstance(agent_id, str) and agent_id):
                raise ValueError("member_join payload must include agent_id")
            version = payload.get("capability_version", 1)
            if not isinstance(version, int) or isinstance(version, bool) or version <= 0:
                raise ValueError("capability_version must be a positive integer")
            if version <= self._capability_versions.get(agent_id, 0):
                raise ValueError(
                    "capability_version must strictly increase the last committed version")
            self._capability_versions[agent_id] = version
            self._agents.add(agent_id)
            self._joined_agents.add(agent_id)
        if type == "observation" and isinstance(payload, Mapping):
            message_ids = []
            if isinstance(payload.get("message_id"), str):
                message_ids.append(payload["message_id"])
            # Camera events keep their transport receipt under ``data``;
            # deduplicate that nested message id as well as host-authored
            # observation payloads.  Track both namespaces when a malformed
            # event supplies both, so an alias cannot bypass the guard.
            if isinstance(payload.get("data"), Mapping):
                nested_id = payload["data"].get("message_id")
                if isinstance(nested_id, str):
                    message_ids.append(nested_id)
            if any(message_id in self._message_ids for message_id in message_ids):
                raise ValueError("observation message id has already been delivered")
            self._message_ids.update(message_ids)
        event = {"type": type, "time_s": now, "payload": deepcopy(dict(payload))}
        self._events.append(event)
        return deepcopy(event)

    def update_runtime_state(
        self,
        time_s: float,
        robot_regions: Mapping[str, str],
        object_states: Mapping[str, Mapping[str, Any]],
    ) -> dict[str, Any]:
        now = float(time_s)
        if not math.isfinite(now) or now < self._time_s:
            raise ValueError("session time must advance monotonically")
        if not isinstance(robot_regions, Mapping) or not all(
                isinstance(agent_id, str) for agent_id in robot_regions):
            raise ValueError("robot_regions must map string robot ids to regions")
        if not isinstance(object_states, Mapping):
            raise ValueError("object_states must be a mapping")
        self._time_s = now
        # Runtime snapshots describe physical state only.  They must not
        # mutate the trusted membership roster: the retired GoalSession kept
        # ``_current`` unchanged here, so an incomplete backend snapshot
        # cannot silently turn an active camera member into ``inactive_robot``.
        return self.report()

    def record_action(self, result: Mapping[str, Any]) -> None:
        self._actions.append(deepcopy(dict(result)))

    def report(self) -> dict[str, Any]:
        return {
            "episode_id": self.episode_id,
            "current_time_s": self._time_s,
            "current_agents": sorted(self._agents),
            "events": deepcopy(self._events),
            "actions": deepcopy(self._actions),
        }


class ObservationEpisodeDriver:
    """Run one observation-only episode against a trusted camera companion.

    The participant protocol mirrors the retired goal adapter minus the goal
    ceremony: ``initialize(public_task, participant_api)``, ``act(events)``
    returning camera tool calls and ``consume_message`` actions, and
    ``close()``.  ``initialize_camera_tools(profile)`` is the opt-in hook;
    ``update_camera_tools(delta)`` is the optional join-delta hook.

    ``setup`` / ``request_actions`` / ``emit_member_join`` / ``close`` expose
    the retired host surface for manually driven tests; ``run`` composes them
    into the retired episode-runner loop with guarded teardown and archives.
    """

    def __init__(
        self,
        public_task: Mapping[str, Any],
        participant: Any,
        backend: Any,
        *,
        episode_id: str,
        output_root: str | Path,
        camera_tools: GoalCameraTools,
        max_turns: int = 256,
    ) -> None:
        if not isinstance(max_turns, int) or isinstance(max_turns, bool) or max_turns < 1:
            raise ValueError("max_turns must be a positive integer")
        self.public_task = deepcopy(dict(public_task))
        self.participant = participant
        self.backend = backend
        self.output_root = Path(output_root).absolute()
        self.episode_id = episode_id
        if not isinstance(self.episode_id, str) or not self.episode_id.strip():
            raise ValueError("episode_id must be a nonempty string")
        self.camera_tools = camera_tools
        self.max_turns = max_turns
        if camera_tools is not None and (
                camera_tools.episode_id != self.episode_id
                or camera_tools.public_task_sha256 != canonical_sha256(self.public_task)):
            raise ValueError("camera tools must be bound to this episode and public task")
        self.session = ObservationSession(
            self.episode_id,
            initial_agents={row["id"] for row in self.public_task["candidate_robot_pool"]},
        )
        self._event_cursor = 0
        self._camera_visible_ids: set[str] = set()
        self._camera_connected = False
        self._setup_done = False
        self._closed = False
        self.host_cleanup_errors: list[dict[str, str]] = []
        self._archive_files: list[str] = []

    # ------------------------------------------------------------------
    # Host-side operations (never handed to the participant).
    # ------------------------------------------------------------------
    def _require_open(self) -> None:
        if self._closed:
            raise RuntimeError("observation episode host is closed")

    def setup(self) -> None:
        """Initialize the participant and connect the camera companion.

        Connection is opt-in exactly like the retired host: only a
        participant exposing ``initialize_camera_tools`` gets the camera
        profile, and only then are frames pumped and calls admitted.
        """
        self._require_open()
        if self._setup_done:
            return
        task_sha = canonical_sha256(self.public_task)
        self.participant.initialize(
            deepcopy(self.public_task),
            deepcopy({"public_task_sha256": task_sha,
                      "episode_id": self.episode_id,
                      "input_scope": "participant_visible_events_only"}),
        )
        self.camera_tools.bind_goal_session(self.session)
        initialize_camera = getattr(self.participant, "initialize_camera_tools", None)
        if callable(initialize_camera):
            profile = self.camera_tools.public_profile()
            if profile["public_task_sha256"] != task_sha:
                raise ValueError("camera tool profile does not match public task")
            initialize_camera(deepcopy(profile))
            self._camera_connected = True
        self._setup_done = True

    def emit_member_join(self, agent_id: str, time_s: float,
                         payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Commit a member_join session event.

        Activation happens when the event is delivered by
        :meth:`request_actions`, authenticated by its committed index.
        """
        self._require_open()
        body = {"agent_id": agent_id, **deepcopy(dict(payload or {}))}
        return self.session.emit("member_join", time_s, body)

    def _pump_camera_tools(self) -> None:
        if not self._camera_connected:
            return
        report = self.session.report()
        now = report["current_time_s"]
        for event in self.camera_tools.tick(now, current_agents=set(report["current_agents"])):
            self.session.emit("observation", now, event)

    def request_actions(self) -> list[dict[str, Any]]:
        """Pump due frames, deliver new events, and route returned actions."""
        self._require_open()
        if not self._setup_done:
            self.setup()
        self._pump_camera_tools()
        report = self.session.report()
        event_end = len(report["events"])
        if self._event_cursor == event_end:
            return []
        events = deepcopy(report["events"][self._event_cursor:event_end])
        try:
            delivered = list(events)
            for offset, event in enumerate(events):
                if event["type"] != "member_join" or not self._camera_connected:
                    continue
                session_index = self._event_cursor + offset
                now = report["current_time_s"]
                delta = self.camera_tools.activate_joined_member(
                    event, session_event_index=session_index, time_s=now)
                update_camera = getattr(self.participant, "update_camera_tools", None)
                if callable(update_camera):
                    update_camera(deepcopy(delta))
                else:
                    # Participants without the companion hook still receive a
                    # public observation carrying the same join-scoped delta.
                    delivered.append({
                        "type": "observation",
                        "time_s": now,
                        "payload": {
                            "kind": "camera_tools_profile_delta",
                            "message_id": (
                                f"{self.camera_tools.episode_id}:"
                                f"camera-profile-delta:{session_index}"),
                            "data": deepcopy(delta),
                            "valid_until_s": now + self.camera_tools.validity_s,
                        },
                    })
            events = delivered
            self._camera_visible_ids.update(
                event["payload"]["message_id"] for event in events
                if event["type"] == "observation"
                and event["payload"].get("kind") in {"camera_frame", "camera_tools_profile_delta"})
            raw_actions = self.participant.act(deepcopy(events))
            if not isinstance(raw_actions, list):
                raise ValueError("participant act must return a list of actions")
            actions: list[dict[str, Any]] = []
            for raw in raw_actions:
                if not isinstance(raw, Mapping):
                    raise ValueError("participant action must be an object")
                action = deepcopy(dict(raw))
                if action.get("schema") == CAMERA_TOOL_SCHEMA:
                    if not self._camera_connected:
                        raise ValueError("camera tool interface was not connected")
                    now = self.session.report()["current_time_s"]
                    actions.append(self.camera_tools.submit(
                        action, time_s=now,
                        current_agents=set(self.session.report()["current_agents"])))
                    self._pump_camera_tools()
                    continue
                if action.get("type") in _LOW_LEVEL_ACTION_TYPES:
                    raise ValueError(
                        "participant may emit observation tool calls and "
                        "consume_message actions only, not low-level control")
                if action.get("type") == "consume_message":
                    payload = action.get("payload")
                    message_id = payload.get("message_id") if isinstance(payload, Mapping) else None
                    telemetry = self.camera_tools.telemetry
                    if not telemetry.owns_message(message_id):
                        raise ValueError("consume_message targets an unknown camera message")
                    if message_id not in self._camera_visible_ids:
                        raise ValueError("camera message has not been delivered to the participant")
                    now = self.session.report()["current_time_s"]
                    if not telemetry.can_consume(message_id, time_s=now):
                        raise ValueError("camera message is not currently delivered and fresh")
                    result = {
                        "action_id": action.get("action_id", ""),
                        "accepted": True,
                        "message_id": message_id,
                    }
                    telemetry.consume(message_id, action_id=action.get("action_id", ""),
                                      time_s=self.session.report()["current_time_s"])
                    actions.append(result)
                    self.session.record_action(result)
                    continue
                raise ValueError(
                    "observation episode accepts camera tool calls and "
                    "consume_message actions only")
        except BaseException:
            # The participant has observed this batch; a partial batch cannot
            # be replayed transactionally, so fail closed at the episode level.
            self._event_cursor = event_end
            raise
        self._event_cursor = event_end
        return deepcopy(actions)

    def close(self) -> None:
        """First close attempt, mirroring the retired host context exit.

        Failures are retained in ``host_cleanup_errors`` instead of masking
        an in-flight episode error.
        """
        if self._closed:
            return
        self._closed = True
        try:
            self.camera_tools.close()
        except Exception as exc:
            self.host_cleanup_errors.append({
                "component": "camera_tools",
                "operation": "host_close",
                "error": f"{type(exc).__name__}: {exc}",
            })
        participant_close = getattr(self.participant, "close", None)
        if callable(participant_close):
            try:
                participant_close()
            except Exception as exc:
                self.host_cleanup_errors.append({
                    "component": "participant",
                    "operation": "close",
                    "error": f"{type(exc).__name__}: {exc}",
                })

    # ------------------------------------------------------------------
    # Episode loop and guarded teardown.
    # ------------------------------------------------------------------
    def run(self, *, initial_state: Mapping[str, Any]) -> dict[str, Any]:
        if self.output_root.exists():
            raise FileExistsError(f"episode output already exists: {self.output_root}")
        self.output_root.mkdir(parents=True)

        def archive(name: str, value: Any) -> None:
            _write_json(self.output_root / name, value)
            self._archive_files.append(name)

        robots, objects = _require_state(initial_state, "initial_state")
        task_sha = canonical_sha256(self.public_task)
        manifest = {
            "schema": OBSERVATION_EPISODE_SCHEMA,
            "version": 1,
            "episode_id": self.episode_id,
            "task_id": self.public_task["task_id"],
            "scene_key": self.public_task["mission"]["scene_key"],
            "public_task_sha256": task_sha,
            "backend": type(self.backend).__name__,
            "camera_tools": {
                "profile_sha256": canonical_sha256(self.camera_tools.public_profile()),
                "capture_scope": "owner_thread_on_request; idle_destroy render lifecycle",
            },
            "execution_validated": False,
            "scoreable": False,
        }
        archive("episode_manifest.json", manifest)
        archive("camera_tool_profile.json", self.camera_tools.public_profile())
        archive("public_task.json", self.public_task)

        snapshots: list[dict[str, Any]] = []
        lifecycle_errors: list[dict[str, str]] = []
        error: str | None = None
        turns = 0
        budget = float(self.public_task["budgets"]["simulation_s"])

        try:
            # setup lives inside the guarded region: a participant whose
            # initialize() raises still gets teardown archives, not a bare
            # exception with a leaked render lifecycle.  The failure is also
            # retained as lifecycle evidence so scoring metadata consumers
            # see it without parsing the error string.
            try:
                self.setup()
            except Exception as exc:
                lifecycle_errors.append({
                    "component": "participant",
                    "operation": "setup",
                    "error": f"{type(exc).__name__}: {exc}",
                })
                raise
            self.session.update_runtime_state(0.0, robots, objects)
            snapshots.append({"episode_id": self.episode_id, "time_s": 0.0,
                              "robot_regions": deepcopy(robots),
                              "object_states": deepcopy(objects)})
            for turns in range(1, self.max_turns + 1):
                self.request_actions()
                # Observation-only episodes have no goal skill tasks; the
                # idle branch mirrors the retired runner exactly.
                if turns > 1 and not self.request_actions():
                    idle = getattr(self.backend, "advance_idle", None)
                    camera_pending = self.camera_tools.has_pending_work()
                    if camera_pending and callable(idle):
                        now = self.session.report()["current_time_s"]
                        wake = self.camera_tools.next_wakeup_s()
                        wakeups = [float(wake)] if wake is not None else [now + 1e-6]
                        next_time = min(min(wakeups), budget)
                        if next_time <= now:
                            # Deadlines in the past still need a forward tick.
                            next_time = min(now + 1e-6, budget)
                        if next_time > now:
                            idle_result = idle(next_time)
                            actual_time = (
                                idle_result.get("time_s")
                                if isinstance(idle_result, Mapping) else None)
                            if (isinstance(actual_time, bool)
                                    or not isinstance(actual_time, (int, float))
                                    or not math.isfinite(actual_time)
                                    or not now < actual_time <= budget):
                                raise ValueError(
                                    "idle backend must report its actual advancing episode time")
                            idle_robots, idle_objects = _require_state(
                                idle_result.get("runtime_state"), "idle runtime state")
                            self.session.update_runtime_state(actual_time, idle_robots, idle_objects)
                            self.session.emit("tick", actual_time, {"state_digest": "camera_tool_idle"})
                            snapshots.append({"episode_id": self.episode_id,
                                              "time_s": actual_time,
                                              "robot_regions": deepcopy(idle_robots),
                                              "object_states": deepcopy(idle_objects)})
                            continue
                        # Hit simulation budget while tools still pending.
                        if camera_pending:
                            self.camera_tools.finalize_episode(budget)
                        break
                    if camera_pending and not callable(idle):
                        raise RuntimeError(
                            "camera tools are waiting but the backend cannot "
                            "advance its idle clock")
                    break
            else:
                raise RuntimeError("observation episode exceeded max_turns")
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        finally:
            self.close()
        lifecycle_errors.extend(deepcopy(self.host_cleanup_errors))

        archive("runtime_snapshots.json", snapshots)
        tool_report: Mapping[str, Any] | None = None
        finalizer = getattr(self.camera_tools, "finalize_episode", None)
        if callable(finalizer):
            try:
                finalizer()
            except Exception as exc:
                lifecycle_errors.append({
                    "component": "camera_tools",
                    "operation": "finalize_episode",
                    "error": f"{type(exc).__name__}: {exc}",
                })
        closer = getattr(self.camera_tools, "close", None)
        if callable(closer):
            try:
                closer()
            except Exception as exc:
                lifecycle_errors.append({
                    "component": "camera_tools",
                    "operation": "close",
                    "error": f"{type(exc).__name__}: {exc}",
                })
        try:
            from algorithm.skills.observation.communication import validate_communication_report
            tool_report = self.camera_tools.report()
            archive("camera_tools_report.json", tool_report)
            archive("camera_communication_replay.json",
                    validate_communication_report(tool_report["communication"]))
        except Exception as exc:
            lifecycle_errors.append({
                "component": "camera_tools",
                "operation": "report",
                "error": f"{type(exc).__name__}: {exc}",
            })
        if isinstance(tool_report, Mapping) and tool_report.get("lifecycle_errors"):
            archive("camera_lifecycle_errors.json", tool_report["lifecycle_errors"])
        if isinstance(tool_report, Mapping):
            render_report = tool_report.get("render_lifecycle") or {}
            if render_report.get("cleanup_pending"):
                lifecycle_errors.append({
                    "component": "camera_tools",
                    "operation": "render_cleanup",
                    "error": "RuntimeError: owned render products still pending cleanup",
                })
            if render_report.get("failures"):
                archive("camera_render_failures.json", render_report["failures"])
        if lifecycle_errors:
            archive("lifecycle_errors.json", lifecycle_errors)
            if error is None:
                error = "lifecycle_error: " + "; ".join(
                    row["error"] for row in lifecycle_errors)
        archive("participant_trace.json", {
            "episode_id": self.episode_id,
            "events": deepcopy(self.session.report()["events"]),
            "actions": deepcopy(self.session.report()["actions"]),
        })
        result = {
            "schema": OBSERVATION_EPISODE_RESULT_SCHEMA,
            "version": 1,
            "episode_id": self.episode_id,
            "task_id": self.public_task["task_id"],
            "scene_key": self.public_task["mission"]["scene_key"],
            "turns": turns,
            "error": error,
            "execution_validated": False,
            "scoreable": False,
            "output_root": str(self.output_root),
        }
        archive("result.json", result)
        archive("scoring_adapter_metadata.json", {
            "schema": OBSERVATION_SCORING_ADAPTER_SCHEMA,
            "version": 1,
            "episode_id": self.episode_id,
            "public_task_sha256": task_sha,
            "archive_index": {"files": list(self._archive_files)},
            "execution_validated": False,
            "scoreable": False,
            "episode_result": {
                "error": result["error"],
                "turns": result["turns"],
                "lifecycle_error_count": len(lifecycle_errors),
                "execution_validated": False,
                "scoreable": False,
            },
        })
        return deepcopy(result)


__all__ = [
    "OBSERVATION_EPISODE_RESULT_SCHEMA",
    "OBSERVATION_EPISODE_SCHEMA",
    "OBSERVATION_SCORING_ADAPTER_SCHEMA",
    "ObservationEpisodeDriver",
    "ObservationSession",
]
