"""Robot-independent fixed-action PickPlace contracts."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

@dataclass(frozen=True)
class PickPlaceCommand:
    request_id: str
    robot_id: str
    object_ref: str
    operation: str
    timeout_s: float


@dataclass(frozen=True)
class BackendFeedback:
    """Private execution feedback; never returned as the public skill result.

    stopped confirms relinquishing operation control, not just receiving a
    stop request. Holding must include the object identity. clean confirms
    that temporary state can be released after an empty failure.
    """
    request_id: str
    status: str  # running | completed | failed | stopped
    grasp_state: str  # empty | holding | unknown
    object_ref: str | None = None
    ready: bool = False  # pick: carry ready; place: released and recovered
    stopped: bool = False
    clean: bool = False
    reason: str = ""


class PickPlaceAdapter(Protocol):
    """Robot-specific local pick/place adapter.

    The adapter owns approach, down/up action and recovery.  It is
    created by the host together with its controller and is never responsible
    for advancing the simulator.  ``PickPlaceExecutor`` owns the public
    state machine, leases, timeout/cancel policy and boolean result.
    """
    robot_id: str

    def resource_key(self, object_ref: str) -> str:
        """Return the canonical object resource, including alias resolution."""

    def start(self, command: PickPlaceCommand) -> None:
        """Start the configured fixed action for this robot."""
        ...
    def before_step(self) -> None: ...
    def after_step(self) -> None: ...
    def feedback(self) -> BackendFeedback: ...
    def request_stop(self, reason: str) -> None: ...


@dataclass
class PickPlaceStateMachine:
    """Only local operation state. Navigation is absent from this machine."""
    phase: str = "idle"
    object_ref: str | None = None

    def begin(self, command: PickPlaceCommand) -> None:
        if command.operation == "pick":
            if self.phase != "idle" or self.object_ref is not None:
                raise ValueError("robot is busy or already holding an object")
            self.phase, self.object_ref = "picking", command.object_ref
        elif command.operation == "place":
            if self.phase != "holding" or self.object_ref != command.object_ref:
                raise ValueError("place requires this robot's held object")
            self.phase = "placing"
        else:
            raise ValueError("operation must be pick or place")

    def finish(self, feedback: BackendFeedback, *, succeeded: bool) -> None:
        if feedback.grasp_state == "holding" and feedback.object_ref == self.object_ref:
            self.phase = "holding" if succeeded or feedback.stopped else "fault"
        elif feedback.grasp_state == "empty" and feedback.clean and feedback.stopped:
            self.phase, self.object_ref = "idle", None
        else:
            self.phase = "fault"


__all__ = ["BackendFeedback", "PickPlaceAdapter", "PickPlaceCommand",
           "PickPlaceStateMachine"]
