"""Trusted bridge from a tested multi-agent algorithm to ``GoalSession``.

The tested adapter lifecycle is deliberately small::

    adapter.initialize(public_task, participant_api)
    actions = adapter.act(new_public_events)
    adapter.close()

``actions`` must use the existing ``eai.benchmark.goal_action`` envelope.
This module does not define another action schema and does not execute skills.
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
    ) -> None:
        validate_goal_task_spec(public_task)
        self._public_task = deepcopy(dict(public_task))
        validate_goal_participant_api(participant_api, task_spec=self._public_task)
        self._participant_api = deepcopy(dict(participant_api))
        self._adapter = adapter
        self._adapter_manifest = validate_goal_adapter_manifest(adapter.manifest())
        self._session = GoalSession(self._public_task, self._participant_api)
        self._event_cursor = 0
        self._closed = False
        self._failed = False
        try:
            adapter.initialize(deepcopy(self._public_task), deepcopy(self._participant_api))
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
        host_report = self._session.report()
        event_end = len(host_report["events"])
        if self._event_cursor == event_end:
            return []
        events = deepcopy(host_report["events"][self._event_cursor:event_end])
        try:
            raw_actions = self._adapter.act(deepcopy(events))
            if not isinstance(raw_actions, list):
                raise ValueError("goal algorithm adapter must return a list of actions")
            actions = []
            for raw in raw_actions:
                if not isinstance(raw, Mapping):
                    raise ValueError("goal algorithm action must be an object")
                action = deepcopy(dict(raw))
                if action.get("type") in _LOW_LEVEL_ACTION_TYPES:
                    raise ValueError(
                        "goal algorithm may emit high-level goal actions only, not low-level control")
                actions.append(self._session.submit_action(action))
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
