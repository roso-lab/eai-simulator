"""Pick/place phase targets for the host's Pegasus position controller."""
import math
from copy import deepcopy
from uuid import uuid4

from algorithm.skills.aerial_transport.geometry import rotate, vector
from algorithm.skills.pick_place import BackendFeedback
from task.executors.aerial_pick_place import ATTACHMENT_POSITION_TOLERANCE_M

SUPPORTED_ROBOT_TYPES = frozenset({"PegasusIris", "PegasusX4"})


class DronePickPlaceAdapter:
    def __init__(self, robot_id, bridge, *, robot_body, robot_pose, object_pose,
                 write_goal, stop_motion, simulation_time,
                 resolve_object=lambda ref: ref, grasp_anchor_object_m=(0., 0., 0.),
                 robot_type="PegasusIris", clearance_m=.3,
                 tolerance_m=ATTACHMENT_POSITION_TOLERANCE_M,
                 stable_steps=5, return_target_reader=None):
        """Callbacks share the host's normal goal_position command buffer.

        stop_motion returns exactly True only when this writer has relinquished
        the buffer without replacing the last goal. Cancellation first writes
        the measured position as a hover target. FixedJoints persist
        independently of the local skill writer. Each phase submits a fixed
        world XYZ goal; Pegasus owns all flight dynamics and control.
        Optional return_target_reader(command) supplies world XYZ only. Its
        value is frozen before binding bodies; no navigation/map logic runs
        here. Recovery and return share the operation's timeout and lease.
        """
        self.robot_id, self.bridge, self._body = robot_id, bridge, robot_body
        self._robot_pose, self._object_pose = robot_pose, object_pose
        self._write_goal, self._stop_motion, self._time = write_goal, stop_motion, simulation_time
        self._resolve, self._anchor = resolve_object, vector(grasp_anchor_object_m)
        if return_target_reader is not None and not callable(return_target_reader):
            raise ValueError("return_target_reader must be callable")
        self._return_target_reader = return_target_reader
        if not math.isfinite(clearance_m) or clearance_m <= 0 or not 0 < tolerance_m <= ATTACHMENT_POSITION_TOLERANCE_M:
            raise ValueError("positive clearance and <=3 cm connection tolerance required")
        if type(stable_steps) is not int or stable_steps <= 0:
            raise ValueError("stable_steps must be positive")
        self._type, self._clearance, self._tolerance = robot_type, clearance_m, tolerance_m
        self._stable_steps, self._stable = stable_steps, 0
        self._command, self._key, self._path = None, None, None
        self._phase, self._stopped, self._reason = "idle", False, ""
        self._goal, self._phase_time = None, 0.
        self._return_target, self._return_complete = None, False
        self._released, self._failure_phase = False, None
        self._phases, self._physical_events = [], []
        self._pending_measurements = []

    def resource_key(self, object_ref):
        path = self._resolve(object_ref)
        if not isinstance(path, str) or not path.startswith("/"):
            raise ValueError("object must resolve to an absolute rigid body path")
        return "object:" + path

    def validate(self, command):
        if self._type not in SUPPORTED_ROBOT_TYPES:
            raise ValueError("drone backend requires a supported Pegasus goal_position controller")
        if command.target_orientation_base_wxyz is not None:
            raise ValueError("FixedJoint drone backend does not command object orientation")
        path = self._resolve(command.object_ref)
        obj = self._object_pose(path)
        if command.operation == "pick":
            if self._key is not None:
                raise ValueError("drone already owns an object binding")
            if math.dist(obj.position_m, command.target_position_world_m) > .035:
                raise ValueError("pick target differs from the live object origin")
        elif path != self._path or self._key is None or not self.bridge.attached(self._key):
            raise ValueError("place requires the drone's attached body")

    def _root_for_anchor(self, anchor):
        q = self._robot_pose().orientation_wxyz
        offset = rotate(q, self.bridge.drone_anchor_local_m)
        return tuple(anchor[i] - offset[i] for i in range(3))

    def _root_for_object(self):
        # Convert the object-origin target to a root target once at phase
        # entry. Flight feedback belongs to the Pegasus controller.
        robot, obj = self._robot_pose(), self._object_pose(self._path)
        target = self._command.target_position_world_m
        return tuple(robot.position_m[i] + target[i] - obj.position_m[i] for i in range(3))

    def _enter(self, phase, goal):
        self._phase, self._goal, self._stable = phase, vector(goal), 0
        self._phase_time = self._time()
        self._phases.append({"phase": phase, "simulation_time_s": self._phase_time,
                             "goal_position_m": self._goal})

    def start(self, command):
        self._command, self._stopped, self._reason = command, False, ""
        self._return_target, self._return_complete = None, False
        self._released, self._failure_phase = False, None
        self._phases, self._physical_events, self._pending_measurements = [], [], []
        # Read once, validate, and copy before the first physical mutation.
        if self._return_target_reader is not None:
            self._return_target = vector(self._return_target_reader(command))
        self._path = self._resolve(command.object_ref)
        if command.operation == "pick":
            obj = self._object_pose(self._path)
            offset = rotate(obj.orientation_wxyz, self._anchor)
            origin = command.target_position_world_m
            self._contact = tuple(origin[i] + offset[i] for i in range(3))
            self._key = "local_pick_place:" + uuid4().hex
            self.bridge.bind(self._key, self._body, self._path, self._contact)
            goal = self._root_for_anchor(self._contact)
        else:
            goal = self._root_for_object()
        self._release_root = goal
        self._enter("align", (goal[0], goal[1], max(self._robot_pose().position_m[2], goal[2] + self._clearance)))

    def _mutate(self, operation):
        event = {"operation": operation, "simulation_time_s": self._time(),
                 "robot_before_m": self._robot_pose().position_m,
                 "object_before_m": self._object_pose(self._path).position_m}
        self.bridge.apply(self._key, operation, self._command.request_id + ":" + operation)
        self._physical_events.append(event)
        self._pending_measurements.append(event)

    def _finish(self):
        if self._command.operation == "pick":
            if not self.bridge.attached(self._key):
                raise ValueError("attachment lost during lift or return")
        elif math.dist(self._object_pose(self._path).position_m,
                       self._command.target_position_world_m) > self._tolerance:
            raise ValueError("released object moved away from the requested origin")
        if self._stop_motion() is not True:
            raise ValueError("host did not relinquish local flight control")
        self._stopped = True
        self._return_complete = self._return_target is not None
        if self._command.operation == "pick":
            self._phase = "holding"
        else:
            self.bridge.unbind(self._key)
            self._key, self._path, self._phase = None, None, "idle"

    def execution_record(self):
        """Drone-only diagnostics; the common executor still returns bool."""
        position = self._robot_pose().position_m
        return deepcopy({
            "return_target_m": self._return_target,
            "final_robot_position_m": position,
            "return_error_m": (None if self._return_target is None else
                               math.dist(position, self._return_target)),
            "goal_position_m": self._goal,
            "return_completed": self._return_complete,
            "released": self._released,
            "attached": self._key is not None and self.bridge.attached(self._key),
            "phase": self._phase, "failure_phase": self._failure_phase,
            "failure_reason": self._reason, "phases": self._phases,
            "physical_events": self._physical_events,
            "stable_samples": self._stable,
        })

    def before_step(self):
        if self._stopped or self._phase in {"holding", "idle"}:
            return
        self._write_goal(self._goal)

    def after_step(self):
        if self._stopped or self._phase in {"holding", "idle"}:
            return
        robot = self._robot_pose()
        for event in self._pending_measurements:
            event.update({"robot_after_tick_m": robot.position_m,
                          "object_after_tick_m": self._object_pose(self._path).position_m})
        self._pending_measurements.clear()
        # During the vertical retreat the XY error is intentionally deferred
        # to the following translation phase. Requiring the full XYZ gate here
        # lets horizontal controller drift prevent the ascent from completing.
        arrived = (abs(robot.position_m[2] - self._goal[2]) <= self._tolerance
                    if self._phase == "return_ascend"
                    else math.dist(robot.position_m, self._goal) <= self._tolerance)
        if self._phase == "descend":
            if self._command.operation == "pick":
                drone, obj = self.bridge.measure(self._key)
                arrived = math.dist(drone, obj) <= self._tolerance
            else:
                arrived = math.dist(self._object_pose(self._path).position_m,
                                    self._command.target_position_world_m) <= self._tolerance
        self._stable = self._stable + 1 if arrived else 0
        final_return = (self._phase == "return_descend" or
                        (self._phase == "return_translate" and self._goal == self._return_target))
        if (self._phase == "ascend" or final_return) and not self.bridge.quiescent(self._key):
            self._stable = 0
        if self._stable < self._stable_steps:
            return
        if self._phase == "align":
            if self._command.operation == "pick":
                self._mutate("create_support")
                self._mutate("disable_collision")
            goal = (self._release_root if self._command.operation == "pick"
                    else self._root_for_object())
            self._enter("descend", goal)
        elif self._phase == "descend":
            if self._command.operation == "pick":
                self._mutate("attach")
                self._mutate("remove_support")
            else:
                self._mutate("release")
                self._released = True
            retreat_z = robot.position_m[2] + self._clearance
            if self._return_target is not None:
                retreat_z = max(retreat_z, self._return_target[2])
            self._enter("ascend" if self._return_target is None else "return_ascend",
                        (robot.position_m[0], robot.position_m[1], retreat_z))
        elif self._phase == "ascend":
            self._finish()
        elif self._phase == "return_ascend":
            self._enter("return_translate", (*self._return_target[:2], self._goal[2]))
        elif self._phase == "return_translate":
            if self._goal[2] > self._return_target[2]:
                self._enter("return_descend", self._return_target)
            else:
                self._finish()
        elif self._phase == "return_descend":
            self._finish()

    def feedback(self):
        attached = self._key is not None and self.bridge.attached(self._key)
        complete = self._phase in {"holding", "idle"} and not self._reason
        clean = self._key is None
        return BackendFeedback(self._command.request_id,
            "completed" if complete else "stopped" if self._stopped else "running",
            "holding" if attached else "empty", self._command.object_ref if attached else None,
            ready=complete, stopped=self._stopped, clean=clean, reason=self._reason)

    def request_stop(self, reason):
        if not self._reason:
            self._failure_phase = self._phase
        self._reason = reason
        if not self._stopped:
            self._write_goal(self._robot_pose().position_m)
        self._stopped = self._stop_motion() is True
        if self._key is not None and not self.bridge.attached(self._key):
            self._mutate("rollback")
            self.bridge.unbind(self._key)
            self._key, self._path = None, None
        self._phase = "holding" if self._key is not None else "idle"


__all__ = ["DronePickPlaceAdapter"]
