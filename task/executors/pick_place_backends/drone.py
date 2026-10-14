"""Local flight/grasp adapter using the existing owned FixedJoint bridge."""
import math
from uuid import uuid4

from algorithm.skills.aerial_transport.geometry import rotate, vector
from algorithm.skills.aerial_transport.trajectory import VerticalProfile
from algorithm.skills.pick_place import BackendFeedback

SUPPORTED_ROBOT_TYPES = frozenset({"PegasusIris", "PegasusX4"})


class DronePickPlaceAdapter:
    def __init__(self, robot_id, bridge, *, robot_body, robot_pose, object_pose,
                 write_goal, stop_motion, simulation_time,
                 resolve_object=lambda ref: ref, grasp_anchor_object_m=(0., 0., 0.),
                 robot_type="PegasusIris", clearance_m=.3, tolerance_m=.008,
                 stable_steps=5):
        """Callbacks share the host's normal goal_position command buffer.

        stop_motion returns exactly True only when this writer has relinquished
        the buffer. The host keeps its last hover goal until navigation changes
        it. FixedJoints persist independently of the local skill writer.
        """
        self.robot_id, self.bridge, self._body = robot_id, bridge, robot_body
        self._robot_pose, self._object_pose = robot_pose, object_pose
        self._write_goal, self._stop_motion, self._time = write_goal, stop_motion, simulation_time
        self._resolve, self._anchor = resolve_object, vector(grasp_anchor_object_m)
        if not math.isfinite(clearance_m) or clearance_m <= 0 or not 0 < tolerance_m <= .01:
            raise ValueError("positive clearance and <=1 cm connection tolerance required")
        if type(stable_steps) is not int or stable_steps <= 0:
            raise ValueError("stable_steps must be positive")
        self._type, self._clearance, self._tolerance = robot_type, clearance_m, tolerance_m
        self._stable_steps, self._stable = stable_steps, 0
        self._command, self._key, self._path = None, None, None
        self._phase, self._stopped, self._reason = "idle", False, ""
        self._goal, self._profile, self._phase_time = None, None, 0.

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
        # The object target is frozen in world. Use live attachment geometry
        # to compensate controller tilt, never re-interpret it in moving base.
        robot, obj = self._robot_pose(), self._object_pose(self._path)
        target = self._command.target_position_world_m
        return tuple(robot.position_m[i] + target[i] - obj.position_m[i] for i in range(3))

    def _enter(self, phase, goal, *, vertical=False):
        self._phase, self._goal, self._stable = phase, goal, 0
        self._phase_time = self._time()
        self._profile = VerticalProfile(self._robot_pose().position_m[2], goal[2]) if vertical else None

    def start(self, command):
        self._command, self._stopped, self._reason = command, False, ""
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
        self.bridge.apply(self._key, operation, self._command.request_id + ":" + operation)

    def before_step(self):
        if self._stopped or self._phase in {"holding", "idle"}:
            return
        goal = self._goal
        if self._profile is not None:
            z, _ = self._profile.sample(self._time() - self._phase_time)
            goal = goal[0], goal[1], z
        if self._phase == "descend" and self._command.operation == "place":
            live = self._root_for_object()
            # Preserve the smooth vertical profile until its end; final
            # settling uses the actual object-origin error.
            goal = (live[0], live[1], live[2] if self._time()-self._phase_time >= self._profile.duration_s else goal[2])
        self._write_goal(goal)

    def after_step(self):
        if self._stopped or self._phase in {"holding", "idle"}:
            return
        robot = self._robot_pose()
        arrived = math.dist(robot.position_m, self._goal) <= self._tolerance
        if self._profile is not None:
            arrived = arrived and self._time() - self._phase_time >= self._profile.duration_s
        if self._phase == "descend":
            if self._command.operation == "pick":
                drone, obj = self.bridge.measure(self._key)
                arrived = math.dist(drone, obj) <= self._tolerance
            else:
                arrived = math.dist(self._object_pose(self._path).position_m,
                                    self._command.target_position_world_m) <= self._tolerance
            arrived = arrived and self._time() - self._phase_time >= self._profile.duration_s
        self._stable = self._stable + 1 if arrived else 0
        if self._phase == "ascend" and not self.bridge.quiescent(self._key):
            self._stable = 0
        if self._stable < self._stable_steps:
            return
        if self._phase == "align":
            if self._command.operation == "pick":
                self._mutate("create_support")
                self._mutate("disable_collision")
            self._enter("descend", self._release_root, vertical=True)
        elif self._phase == "descend":
            if self._command.operation == "pick":
                self._mutate("attach")
                self._mutate("remove_support")
            else:
                self._mutate("release")
            self._enter("ascend", (robot.position_m[0], robot.position_m[1],
                                    robot.position_m[2] + self._clearance), vertical=True)
        elif self._phase == "ascend":
            if self._stop_motion() is not True:
                raise ValueError("host did not relinquish local flight control")
            self._stopped = True
            if self._command.operation == "pick":
                if not self.bridge.attached(self._key):
                    raise ValueError("attachment lost during lift")
                self._phase = "holding"
            else:
                if math.dist(self._object_pose(self._path).position_m,
                             self._command.target_position_world_m) > .035:
                    raise ValueError("released object moved away from the requested origin")
                self.bridge.unbind(self._key)
                self._key, self._path, self._phase = None, None, "idle"

    def feedback(self):
        attached = self._key is not None and self.bridge.attached(self._key)
        complete = self._phase in {"holding", "idle"} and not self._reason
        clean = self._key is None
        return BackendFeedback(self._command.request_id,
            "completed" if complete else "stopped" if self._stopped else "running",
            "holding" if attached else "empty", self._command.object_ref if attached else None,
            ready=complete, stopped=self._stopped, clean=clean, reason=self._reason)

    def request_stop(self, reason):
        self._reason = reason
        self._stopped = self._stop_motion() is True
        if self._key is not None and not self.bridge.attached(self._key):
            self._mutate("rollback")
            self.bridge.unbind(self._key)
            self._key, self._path = None, None
        self._phase = "holding" if self._key is not None else "idle"


__all__ = ["DronePickPlaceAdapter"]
