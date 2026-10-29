"""Action-only pick/place phases for a host-owned Pegasus controller.

The adapter owns only flight goals and completion feedback. PickPlace keeps a
logical holding state, but this backend never binds, moves, filters, or mutates
a payload rigid body.
"""
import math
from copy import deepcopy

from algorithm.skills.aerial_transport.geometry import vector
from algorithm.skills.pick_place import BackendFeedback

SUPPORTED_ROBOT_TYPES = frozenset({"PegasusIris", "PegasusX4"})


class DronePickPlaceAdapter:
    def __init__(self, robot_id, bridge=None, *, robot_body=None, robot_pose,
                 object_pose=None, write_goal, stop_motion, simulation_time,
                 resolve_object=lambda ref: ref, grasp_anchor_object_m=(0., 0., 0.),
                 robot_type="PegasusIris", clearance_m=.3, tolerance_m=.03,
                 stable_steps=5, return_target_reader=None,
                 align_xy_tolerance_m=.08, align_z_tolerance_m=.05):
        # Positional/legacy arguments remain accepted so old hosts fail only
        # on behavior changes, never on construction. They are intentionally
        # unused in action-only mode.
        del bridge, robot_body, object_pose, resolve_object, grasp_anchor_object_m
        if return_target_reader is not None and not callable(return_target_reader):
            raise ValueError("return_target_reader must be callable")
        values = (clearance_m, tolerance_m, align_xy_tolerance_m, align_z_tolerance_m)
        if (not all(math.isfinite(float(value)) and float(value) > 0 for value in values)
                or type(stable_steps) is not int or stable_steps <= 0):
            raise ValueError("drone action tolerances and stable_steps must be positive")
        self.robot_id = robot_id
        self._robot_pose = robot_pose
        self._write_goal, self._stop_motion, self._time = write_goal, stop_motion, simulation_time
        self._return_target_reader = return_target_reader
        self._type = robot_type
        self._clearance, self._tolerance = float(clearance_m), float(tolerance_m)
        self._align_xy_tolerance, self._align_z_tolerance = float(align_xy_tolerance_m), float(align_z_tolerance_m)
        self._stable_steps = stable_steps
        self._command = None
        self._phase, self._stopped, self._reason = "idle", False, ""
        self._goal, self._phase_time, self._stable = None, 0.0, 0
        self._return_target, self._return_complete = None, False
        self._phases = []

    def resource_key(self, object_ref):
        if not isinstance(object_ref, str) or not object_ref.strip():
            raise ValueError("object_ref must be nonempty text")
        return "object:" + object_ref.strip()

    def validate(self, command):
        if self._type not in SUPPORTED_ROBOT_TYPES:
            raise ValueError("drone backend requires a supported Pegasus goal_position controller")
        del command

    def _enter(self, phase, goal):
        self._phase, self._goal, self._stable = phase, vector(goal), 0
        self._phase_time = float(self._time())
        self._phases.append({"phase": phase, "simulation_time_s": self._phase_time,
                             "goal_position_m": self._goal})

    def start(self, command):
        self._command, self._stopped, self._reason = command, False, ""
        self._return_complete, self._phases = False, []
        self._return_target = (None if self._return_target_reader is None else
                               vector(self._return_target_reader(command)))
        robot = self._robot_pose()
        # PickPlace is called after navigation. The fixed action is local to
        # the robot's measured position and never consumes a payload pose.
        target = (robot.position_m[0], robot.position_m[1], robot.position_m[2] - self._clearance)
        high_z = robot.position_m[2]
        self._action_target = vector(target)
        self._return_root = (robot.position_m[0], robot.position_m[1], high_z)
        self._enter("align", (target[0], target[1], high_z))

    def _arrived(self, robot):
        if self._phase == "align":
            return (math.hypot(robot.position_m[0] - self._goal[0],
                               robot.position_m[1] - self._goal[1]) <= self._align_xy_tolerance
                    and abs(robot.position_m[2] - self._goal[2]) <= self._align_z_tolerance)
        return math.dist(robot.position_m, self._goal) <= self._tolerance

    def before_step(self):
        if not self._stopped and self._phase not in {"holding", "idle"}:
            self._write_goal(self._goal)

    def after_step(self):
        if self._stopped or self._phase in {"holding", "idle"}:
            return
        robot = self._robot_pose()
        self._stable = self._stable + 1 if self._arrived(robot) else 0
        if self._stable < self._stable_steps:
            return
        if self._phase == "align":
            self._enter("descend", self._action_target)
        elif self._phase == "descend":
            if self._return_target is None:
                self._enter("ascend", self._return_root)
            else:
                high = max(robot.position_m[2] + self._clearance, self._return_target[2])
                self._enter("return_ascend", (robot.position_m[0], robot.position_m[1], high))
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

    def _finish(self):
        if self._stop_motion() is not True:
            raise ValueError("host did not relinquish local flight control")
        self._stopped = True
        self._return_complete = self._return_target is not None
        self._phase = "holding" if self._command.operation == "pick" else "idle"

    def execution_record(self):
        position = self._robot_pose().position_m
        phase_times = {row["phase"]: row["simulation_time_s"] for row in self._phases}
        start_time = self._phases[0]["simulation_time_s"] if self._phases else None
        return deepcopy({
            "attachment_mode": "none",
            "return_target_m": self._return_target,
            "final_robot_position_m": position,
            "return_error_m": (None if self._return_target is None else
                               math.dist(position, self._return_target)),
            "goal_position_m": self._goal,
            "return_completed": self._return_complete,
            "phase": self._phase,
            "failure_reason": self._reason,
            "phases": self._phases,
            "stable_samples": self._stable,
            "operation_duration_s": (None if start_time is None else self._time() - start_time),
            "phase_times": phase_times,
        })

    def feedback(self):
        complete = self._phase in {"holding", "idle"} and self._stopped and not self._reason
        logical_state = "holding" if self._command is not None and self._command.operation == "pick" and complete else "empty"
        return BackendFeedback(self._command.request_id,
            "completed" if complete else "stopped" if self._stopped else "running",
            logical_state,
            self._command.object_ref if logical_state == "holding" else None,
            ready=complete, stopped=self._stopped, clean=True, reason=self._reason)

    def request_stop(self, reason):
        self._reason = str(reason)
        if not self._stopped:
            self._write_goal(self._robot_pose().position_m)
            self._stopped = self._stop_motion() is True
        self._phase = "holding" if self._command is not None and self._command.operation == "pick" else "idle"


__all__ = ["DronePickPlaceAdapter"]
