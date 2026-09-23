"""Trusted bridge from a tested multi-agent algorithm to ``GoalSession``.

The tested adapter lifecycle is deliberately small::

    adapter.initialize(public_task, participant_api)
    actions = adapter.act(new_public_events)
    adapter.close()

Goal actions use the existing ``eai.benchmark.goal_action`` envelope. An
explicitly connected pose companion accepts tool calls beside those actions;
it does not alter the frozen Goal action schema or execute skills.
The trusted host retains sole ownership of runtime events, symbolic snapshots
and terminal backend receipts.  A full ``GoalSession.report()`` is therefore
never passed to the adapter; only public task/API copies and newly visible
goal events cross the boundary.

This is an architectural trust boundary, not an authentication mechanism.
Callers must not hand the host or its private session to untrusted algorithms.
All methods are synchronous and must be serialized by the episode runner.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping, Protocol

from .goal_driven_contract import validate_goal_participant_api, validate_goal_task_spec
from .goal_session import GoalSession
from .participant_runtime import validate_adapter_manifest


_LOW_LEVEL_ACTION_TYPES = frozenset({
    "cmd_vel", "velocity_command", "raw_velocity", "trajectory", "raw_trajectory",
    "joint_command", "joint_positions", "joint_torques", "grasp_command",
    "low_level_action", "controller_action",
})
GOAL_ADAPTER_INTERFACE = "eai.benchmark.goal_algorithm_adapter"
GOAL_ADAPTER_INTERFACE_VERSION = 1


class GoalAlgorithmAdapter(Protocol):
    """Duck-typed high-level algorithm interface used on all goal scenes."""

    def manifest(self) -> Mapping[str, Any]: ...

    def initialize(
        self,
        public_task: Mapping[str, Any],
        participant_api: Mapping[str, Any],
    ) -> None: ...

    def act(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]: ...

    def close(self) -> None: ...


def validate_goal_adapter_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Require an explicit goal-track identity at the adapter boundary.

    The generic participant manifest is intentionally insufficient here:
    accepting it would allow a fixed-DAG adapter to be silently run against a
    goal task with a different action and lifecycle contract.
    """
    result = validate_adapter_manifest(manifest)
    expected = {
        "interface": GOAL_ADAPTER_INTERFACE,
        "interface_version": GOAL_ADAPTER_INTERFACE_VERSION,
        "evaluation_track": "goal_driven",
        "input_scope": "participant_visible_events_only",
        "control_scope": "benchmark_managed_low_level_control",
    }
    for key, value in expected.items():
        if result.get(key) != value:
            raise ValueError(f"goal adapter manifest {key} must be {value!r}")
    return result


class GoalAlgorithmHost:
    """Own one ``GoalSession`` while exposing only public inputs to an adapter.

    Construction validates the public contracts and calls the exact adapter
    initialization signature ``initialize(public_task, participant_api)``.
    ``request_actions`` delivers each public event once. Adapter or action
    failure is terminal for this bridge: ``close`` is invoked, already accepted
    ledger entries remain truthful, and no retry can duplicate a partial turn.
    """

    def __init__(
        self,
        public_task: Mapping[str, Any],
        participant_api: Mapping[str, Any],
        adapter: GoalAlgorithmAdapter,
        *,
        session: GoalSession | None = None,
        pose_tools: Any = None,
        camera_tools: Any = None,
    ) -> None:
        validate_goal_task_spec(public_task)
        self._public_task = deepcopy(dict(public_task))
        validate_goal_participant_api(participant_api, task_spec=self._public_task)
        self._participant_api = deepcopy(dict(participant_api))
        self._adapter = adapter
        self._adapter_manifest = validate_goal_adapter_manifest(adapter.manifest())
        self._session = session or GoalSession(self._public_task, self._participant_api)
        if session is not None:
            self._session.assert_compatible(self._public_task, self._participant_api)
        self._event_cursor = 0
        self._pose_visible_ids: set[str] = set()
        self._camera_visible_ids: set[str] = set()
        self._pose_tools = pose_tools
        self._camera_tools = camera_tools
        self._tools_connected = False
        self._camera_tools_connected = False
        self._camera_profile: dict[str, Any] | None = None
        self._closed = False
        self._failed = False
        try:
            adapter.initialize(deepcopy(self._public_task), deepcopy(self._participant_api))
            initialize_tools = getattr(adapter, "initialize_tools", None)
            if pose_tools is not None and callable(initialize_tools):
                profile = pose_tools.public_profile()
                if profile["public_task_sha256"] != self._participant_api["public_task_sha256"]:
                    raise ValueError("pose tool profile does not match public task")
                initialize_tools(deepcopy(profile))
                self._tools_connected = True
            initialize_camera = getattr(adapter, "initialize_camera_tools", None)
            if camera_tools is not None and callable(initialize_camera):
                profile = camera_tools.public_profile()
                if profile["public_task_sha256"] != self._participant_api["public_task_sha256"]:
                    raise ValueError("camera tool profile does not match public task")
                initialize_camera(deepcopy(profile))
                self._camera_tools_connected = True
                self._camera_profile = deepcopy(profile)
        except BaseException:
            self._fail_closed()
            raise

    def _require_open(self) -> None:
        if self._closed:
            state = "failed" if self._failed else "closed"
            raise RuntimeError(f"goal algorithm host is {state}")

    def _close_adapter(self) -> None:
        if not self._closed:
            self._closed = True
            if self._camera_tools is not None:
                closer = getattr(self._camera_tools, "close", None)
                if callable(closer):
                    try:
                        closer()
                    except BaseException:
                        pass
            self._adapter.close()

    def _fail_closed(self) -> None:
        """Close without replacing the adapter/action failure being raised."""
        self._failed = True
        try:
            self._close_adapter()
        except BaseException:
            pass

    def close(self) -> None:
        """Close the tested adapter once; trusted ledger evidence is retained."""
        self._close_adapter()

    def __enter__(self) -> "GoalAlgorithmHost":
        self._require_open()
        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:
        if exc_type is not None:
            self._fail_closed()
        else:
            self.close()
        return False

    def request_actions(self) -> list[dict[str, Any]]:
        """Deliver new public events and submit returned v2 actions in order.

        Events created by accepted actions are deliberately left for the next
        call. If a turn fails, the bridge closes and cannot replay the batch.
        """
        self._require_open()
        try:
            if self._tools_connected:
                self.pump_pose_tools()
            if self._camera_tools_connected:
                self.pump_camera_tools()
        except BaseException:
            self._fail_closed()
            raise
        host_report = self._session.report()
        event_end = len(host_report["events"])
        if self._event_cursor == event_end:
            return []
        events = deepcopy(host_report["events"][self._event_cursor:event_end])
        try:
            delivered_events = list(events)
            for offset, event in enumerate(events):
                if event["type"] != "member_join":
                    continue
                agent_id = event["payload"]["agent_id"]
                if self._tools_connected:
                    self._pose_tools.register_public_robot(agent_id)
                if self._camera_tools_connected:
                    session_index = self._event_cursor + offset
                    now = host_report["current_time_s"]
                    delta = self._camera_tools.activate_joined_member(
                        event, session_event_index=session_index, time_s=now)
                    self._camera_profile = deepcopy(self._camera_tools.public_profile())
                    update_camera = getattr(self._adapter, "update_camera_tools", None)
                    if callable(update_camera):
                        update_camera(deepcopy(delta))
                    else:
                        # Participants without the companion hook still receive a
                        # public observation carrying the same join-scoped delta.
                        delivered_events.append({
                            "type": "observation",
                            "time_s": now,
                            "payload": {
                                "kind": "camera_tools_profile_delta",
                                "message_id": (
                                    f"{self._camera_tools.episode_id}:"
                                    f"camera-profile-delta:{session_index}"),
                                "data": deepcopy(delta),
                                "valid_until_s": now + self._camera_tools.validity_s,
                            },
                        })
            events = delivered_events
            self._pose_visible_ids.update(
                event["payload"]["message_id"] for event in events
                if event["type"] == "observation" and event["payload"]["kind"] == "pose_update")
            self._camera_visible_ids.update(
                event["payload"]["message_id"] for event in events
                if event["type"] == "observation"
                and event["payload"]["kind"] in {"camera_frame", "camera_tools_profile_delta"})
            raw_actions = self._adapter.act(deepcopy(events))
            if not isinstance(raw_actions, list):
                raise ValueError("goal algorithm adapter must return a list of actions")
            actions = []
            for raw in raw_actions:
                if not isinstance(raw, Mapping):
                    raise ValueError("goal algorithm action must be an object")
                action = deepcopy(dict(raw))
                if action.get("schema") == "eai.benchmark.goal_pose_tool_call":
                    if not self._tools_connected:
                        raise ValueError("pose tool interface was not connected")
                    report = self._session.report()
                    if (isinstance(action.get("call_id"), str)
                            and action["call_id"] in {row["action_id"] for row in report["actions"]}):
                        raise ValueError("tool call ID collides with a goal action")
                    if (self._camera_tools_connected
                            and isinstance(action.get("call_id"), str)
                            and self._camera_tools.owns_call(action["call_id"])):
                        raise ValueError("pose tool call ID collides with a camera request")
                    actions.append(self._pose_tools.submit(
                        action, time_s=report["current_time_s"],
                        current_agents=set(report["current_agents"])))
                    # Results are event-delivered next turn, never passed back
                    # as hidden publication/queue records in this action batch.
                    self.pump_pose_tools()
                    continue
                if action.get("schema") == "eai.benchmark.goal_camera_tool_call":
                    if not self._camera_tools_connected:
                        raise ValueError("camera tool interface was not connected")
                    report = self._session.report()
                    request_id = action.get("request_id")
                    if (isinstance(request_id, str)
                            and request_id in {row["action_id"] for row in report["actions"]}):
                        raise ValueError("camera request ID collides with a goal action")
                    if (self._tools_connected and isinstance(request_id, str)
                            and self._pose_tools.owns_call(request_id)):
                        raise ValueError("camera request ID collides with a pose tool call")
                    actions.append(self._camera_tools.submit(
                        action, time_s=report["current_time_s"],
                        current_agents=set(report["current_agents"])))
                    self.pump_camera_tools()
                    continue
                if self._tools_connected and self._pose_tools.owns_call(action.get("action_id")):
                    raise ValueError("goal action ID collides with a tool call")
                if (self._camera_tools_connected
                        and self._camera_tools.owns_call(action.get("action_id"))):
                    raise ValueError("goal action ID collides with a camera request")
                if action.get("type") in _LOW_LEVEL_ACTION_TYPES:
                    raise ValueError(
                        "goal algorithm may emit high-level goal actions only, not low-level control")
                pose_telemetry = self._pose_tools.telemetry if self._tools_connected else None
                camera_telemetry = (
                    self._camera_tools.telemetry if self._camera_tools_connected else None)
                payload = action.get("payload")
                message_id = payload.get("message_id") if isinstance(payload, Mapping) else None
                consumes_pose = (action.get("type") == "consume_message"
                                 and pose_telemetry is not None
                                 and pose_telemetry.owns_message(message_id))
                consumes_camera = (action.get("type") == "consume_message"
                                   and camera_telemetry is not None
                                   and camera_telemetry.owns_message(message_id))
                if consumes_pose and message_id not in self._pose_visible_ids:
                    raise ValueError("pose message has not been delivered to the algorithm")
                if consumes_camera and message_id not in self._camera_visible_ids:
                    raise ValueError("camera message has not been delivered to the algorithm")
                now = self._session.report()["current_time_s"]
                if consumes_pose and not pose_telemetry.can_consume(message_id, time_s=now):
                    raise ValueError("pose message is not currently delivered and fresh")
                if consumes_camera and not camera_telemetry.can_consume(message_id, time_s=now):
                    raise ValueError("camera message is not currently delivered and fresh")
                result = self._session.submit_action(action)
                if consumes_pose:
                    pose_telemetry.consume(message_id, action_id=action["action_id"],
                                           time_s=self._session.report()["current_time_s"])
                if consumes_camera:
                    camera_telemetry.consume(message_id, action_id=action["action_id"],
                                             time_s=self._session.report()["current_time_s"])
                actions.append(result)
        except BaseException:
            # The adapter has observed this batch. A partial action batch cannot
            # be replayed transactionally through GoalSession, so fail closed.
            self._event_cursor = event_end
            self._fail_closed()
            raise
        self._event_cursor = event_end
        return deepcopy(actions)

    # The following methods are trusted-host operations. They are intentionally
    # absent from GoalAlgorithmAdapter and the adapter never receives ``self``.
    def pump_pose_tools(self) -> None:
        """Publish due samples and deliver only fresh link responses at this tick."""
        self._require_open()
        if not self._tools_connected:
            return
        report = self._session.report()
        now = report["current_time_s"]
        for payload in self._pose_tools.tick(now, current_agents=set(report["current_agents"])):
            self._session.emit("observation", now, payload)

    def pump_camera_tools(self) -> None:
        """Capture accepted requests on the owner thread and deliver fresh frames."""
        self._require_open()
        if not self._camera_tools_connected:
            return
        report = self._session.report()
        now = report["current_time_s"]
        for payload in self._camera_tools.tick(now, current_agents=set(report["current_agents"])):
            self._session.emit("observation", now, payload)

    def emit(self, type: str, time_s: float, payload: Mapping[str, Any]) -> dict[str, Any]:
        self._require_open()
        return self._session.emit(type, time_s, payload)

    def update_runtime_state(
        self,
        time_s: float,
        robot_regions: Mapping[str, str],
        object_states: Mapping[str, Mapping[str, Any]],
    ) -> dict[str, Any]:
        self._require_open()
        return self._session.update_runtime_state(time_s, robot_regions, object_states)

    def complete_skill(
        self,
        task_id: str,
        *,
        time_s: float,
        status: str,
        receipt: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        self._require_open()
        return self._session.complete_skill(
            task_id, time_s=time_s, status=status, receipt=receipt)

    def record_business_outcome(
        self,
        outcome_id: str,
        *,
        status: str,
        evidence_refs: list[str],
        evaluator_ref: str,
    ) -> dict[str, Any]:
        """Record trusted domain evidence without exposing it to the adapter."""
        self._require_open()
        return self._session.record_business_outcome(
            outcome_id, status=status, evidence_refs=evidence_refs,
            evaluator_ref=evaluator_ref)

    def trusted_report(self) -> dict[str, Any]:
        """Return host-only diagnostics; never pass this value to the adapter."""
        return {
            "adapter": deepcopy(self._adapter_manifest),
            "event_cursor": self._event_cursor,
            "closed": self._closed,
            "failed": self._failed,
            "session": self._session.report(),
        }


__all__ = ["GOAL_ADAPTER_INTERFACE", "GOAL_ADAPTER_INTERFACE_VERSION",
           "GoalAlgorithmAdapter", "GoalAlgorithmHost", "validate_goal_adapter_manifest"]
