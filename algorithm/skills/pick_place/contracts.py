"""Robot-independent local manipulation contracts (metres, xyzw on the wire)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..aerial_transport.geometry import RigidPose, vector


def multiply(left, right):
    w, x, y, z = left
    a, b, c, d = right
    return (w*a-x*b-y*c-z*d, w*b+x*a+y*d-z*c,
            w*c-x*d+y*a+z*b, w*d+x*c-y*b+z*a)


def pose_xyzw(position, orientation):
    x, y, z, w = vector(orientation, 4)
    return RigidPose(vector(position), (w, x, y, z))


@dataclass(frozen=True)
class PickPlaceCommand:
    request_id: str
    robot_id: str
    object_ref: str
    operation: str
    base_pose_world: RigidPose
    target_position_base_m: tuple[float, float, float]
    target_orientation_base_wxyz: tuple[float, float, float, float] | None
    world_state_seq: int
    simulation_time_s: float
    timeout_s: float
    # Optional vector from the object origin to the configured grasp point,
    # expressed in the object's local frame. The executor carries this
    # through unchanged; an adapter owns the robot/tool installation
    # transform that turns it into a controller target.
    grasp_offset_object_m: tuple[float, float, float] | None = None
    approach_mode: str = "vertical_legacy"
    grasp_pose_object_position_m: tuple[float, float, float] | None = None
    grasp_pose_object_orientation_wxyz: tuple[float, float, float, float] | None = None
    approach_direction_world_m: tuple[float, float, float] | None = None
    pregrasp_distance_m: float | None = None
    retreat_distance_m: float | None = None
    approach_speed_m_s: float | None = None

    @property
    def target_position_world_m(self):
        """Reconstruct the latched world target using the sampled base frame."""
        return self.base_pose_world.to_world(self.target_position_base_m)

    @property
    def target_orientation_world_wxyz(self):
        if self.target_orientation_base_wxyz is None:
            return None
        return multiply(self.base_pose_world.orientation_wxyz, self.target_orientation_base_wxyz)


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

    The adapter owns approach, grasp, lift, release and recovery.  It is
    created by the host together with its controller and is never responsible
    for advancing the simulator.  ``PickPlaceExecutor`` owns the public
    state machine, leases, timeout/cancel policy and boolean result.
    """
    robot_id: str

    def resource_key(self, object_ref: str) -> str:
        """Return the canonical object resource, including alias resolution."""

    def start(self, command: PickPlaceCommand) -> None:
        """Invoke the underlying motion controller for a physical diagnostic.

        Production benchmark execution does not call this method for adapters
        declaring ``idealized_semantics = True``.  Such adapters may retain a
        native controller here for opt-in visual/physical diagnostics, but
        geometry, IK, tool-axis and controller lifecycle failures must never
        become the semantic task result.
        """
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
           "PickPlaceStateMachine", "multiply", "pose_xyzw"]
