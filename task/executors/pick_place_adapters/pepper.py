"""Action-only bilateral arm motion for Pepper.

The composite controller remains the owner of IK and physics-rate updates. The
PickPlace adapter only supplies hand targets and reports logical completion; it
never reads or writes a payload object.
"""
import math

from algorithm.skills.aerial_transport.geometry import RigidPose, rotate
from algorithm.skills.pick_place import BackendFeedback


def _values(tensor):
    values = tuple(float(x) for x in tensor.detach().cpu().tolist())
    if not all(math.isfinite(x) for x in values):
        raise ValueError("Pepper live feedback contains a nonfinite value")
    return values


def read_pepper_base_pose(env, robot_id):
    robot = env.scene.articulations[robot_id]
    for name in ("base_link", "base", "torso"):
        ids, _ = robot.find_bodies(name, preserve_order=True)
        if ids:
            body = ids[0]
            return RigidPose(_values(robot.data.body_pos_w[0, body]), _values(robot.data.body_quat_w[0, body]))
    raise ValueError("Pepper requires the moving chassis body, not the fixed articulation root")


class PepperPickPlaceAdapter:
    action_only = True

    def __init__(self, robot_id, env, *, controller_dict=None, base_pose=None,
                 resolve_object=lambda ref: ref, collision_filter_validator=None,
                 lift_m=.12, tolerance_m=.04, stable_steps=8, robot_name=None, env_id=0,
                 grasp_tolerance_m=None):
        del resolve_object, collision_filter_validator, grasp_tolerance_m
        self.robot_id, self._name = robot_id, robot_name or robot_id
        state = controller_dict if controller_dict is not None else env._controllers_dict[self._name]
        if 'environment_states' in state:
            controller_dict = state['environment_states'][env_id]
            views = state.get('environment_views', ())
            if views:
                env = views[env_id]
        self.env = env
        self._controller = controller_dict if controller_dict is not None else state
        self._runtime = self._controller["runtime"]
        self._base = base_pose or (lambda: read_pepper_base_pose(env, self._name))
        if not math.isfinite(lift_m) or lift_m <= 0 or not math.isfinite(tolerance_m) or tolerance_m <= 0:
            raise ValueError("lift and tolerance must be positive")
        if type(stable_steps) is not int or stable_steps <= 0:
            raise ValueError("stable_steps must be positive")
        self._lift, self._tol, self._stable_steps = float(lift_m), float(tolerance_m), stable_steps
        self._command = None
        self._phase, self._stable, self._elapsed = "idle", 0, 0.0
        self._reason, self._stopped = "", False
        self._last_hands, self._frozen, self._targets = None, {}, {}
        self._controller["local_pick_place"] = self

    def resource_key(self, object_ref):
        if not isinstance(object_ref, str) or not object_ref.strip():
            raise ValueError("object_ref must be nonempty text")
        return "object:" + object_ref.strip()

    def validate(self, command):
        if int(getattr(self.env, "num_envs", 1)) != 1:
            raise ValueError("Pepper local manipulation requires one environment")
        robot = self.env.scene.articulations[self._name]
        for side in ("left", "right"):
            ids, body = self._runtime._resolve(robot, side)
            if len(ids) < 4 or body is None:
                raise ValueError("Pepper bilateral arm chain is unavailable")

    def start(self, command):
        self._command, self._reason, self._stopped = command, "", False
        self._stable, self._elapsed, self._last_hands, self._frozen = 0, 0.0, None, {}
        reset = getattr(self._runtime, "reset", None)
        if callable(reset):
            reset()
        start = getattr(self._runtime, "start", None)
        if callable(start):
            start()
        robot = self.env.scene.articulations[self._name]
        base = self._base()
        span = self._runtime.state.config.side_offset
        offsets = {"left": (0., span, 0.), "right": (0., -span, 0.)}
        # Use the measured bilateral hand midpoint as the fixed action point.
        # Navigation is responsible for bringing Pepper to the work area.
        hands = self._hands(robot)
        center = tuple(sum(hands[side][i] for side in ("left", "right")) / 2.0
                       for i in range(3))
        center = (center[0], center[1], center[2] - self._lift)
        self._targets = {side: tuple(center[i] + rotate(base.orientation_wxyz, offset)[i]
                                     for i in range(3)) for side, offset in offsets.items()}
        self._carry_targets = {side: tuple(point[i] + (self._lift if i == 2 else 0.)
                                           for i in range(3)) for side, point in self._targets.items()}
        self._phase = "approach"

    def before_step(self):
        pass

    def after_step(self):
        pass

    def _hands(self, robot):
        return {side: _values(robot.data.body_pos_w[0, self._runtime._resolve(robot, side)[1]])
                for side in ("left", "right")}

    def _freeze(self, robot):
        self._frozen = {side: robot.data.joint_pos[:, self._runtime._resolve(robot, side)[0]].clone()
                        for side in ("left", "right")}

    def physics_step(self, dt_s):
        if self._command is None or self._stopped:
            return
        robot = self.env.scene.articulations[self._name]
        try:
            self._elapsed += float(dt_s)
            hands = self._hands(robot)
            stable = self._last_hands is not None and all(
                math.dist(hands[side], self._last_hands[side]) <= .04 * max(float(dt_s), 1e-6)
                for side in ("left", "right"))
            self._last_hands = hands
            reached = True
            targets = self._carry_targets if self._phase == "lift" else self._targets
            for side in ("left", "right"):
                ids, body = self._runtime._resolve(robot, side)
                if self._phase == "holding":
                    robot.set_joint_position_target(self._frozen[side], joint_ids=ids)
                elif self._phase == "return":
                    self._runtime._apply_home(robot, side, ids)
                    home = self._runtime._home(robot, side, ids)
                    reached = reached and bool(((robot.data.joint_pos[:, ids] - home).abs() < .05).all())
                else:
                    self._runtime._apply_target(robot, side, ids, body, targets[side])
                    reached = reached and self._runtime._measure_reach(robot, side, body, targets[side]) <= self._tol
                self._runtime._enforce_joint_safety(robot, ids)
            if self._phase == "approach":
                if self._elapsed >= 0.5:
                    self._phase, self._stable, self._elapsed = "grasp", 0, 0.0
                return
            self._stable = self._stable + 1 if reached and stable else 0
            if self._stable < self._stable_steps:
                return
            self._stable = 0
            if self._phase == "grasp":
                self._phase = "lift"
            elif self._phase == "lift":
                self._freeze(robot)
                if self._command.operation == "pick":
                    self._phase, self._stopped = "holding", True
                else:
                    self._phase = "return"
            elif self._phase == "return":
                self._phase, self._stopped = "idle", True
        except Exception as exc:
            self.request_stop(str(exc))

    def request_stop(self, reason):
        self._reason = str(reason)
        robot = self.env.scene.articulations[self._name]
        self._freeze(robot)
        self._phase, self._stopped = "holding" if self._command and self._command.operation == "pick" else "idle", True

    def feedback(self):
        complete = self._phase in {"holding", "idle"} and self._stopped and not self._reason
        holding = complete and self._command is not None and self._command.operation == "pick"
        return BackendFeedback(self._command.request_id,
            "completed" if complete else "stopped" if self._stopped else "running",
            "holding" if holding else "empty",
            self._command.object_ref if holding else None,
            ready=complete, stopped=self._stopped, clean=True, reason=self._reason)


__all__ = ["PepperPickPlaceAdapter", "read_pepper_base_pose"]
