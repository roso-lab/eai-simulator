"""Independent bilateral pick/place using Pepper's existing Jacobian IK.

The controller delegate runs at physics rate, including throughout external
navigation. It never generates a base command or invokes the old route FSM.
"""
import math

from algorithm.skills.aerial_transport.geometry import RigidPose, conjugate, rotate
from algorithm.skills.pick_place import BackendFeedback
from algorithm.skills.pick_place.contracts import multiply


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
    # Keep the native bilateral controller available for diagnostics while the
    # benchmark task result follows the idealized transport contract.
    idealized_semantics = True

    def __init__(self, robot_id, env, *, controller_dict=None, base_pose=None,
                 resolve_object=lambda ref: ref, collision_filter_validator=None,
                 lift_m=.12, tolerance_m=.04, stable_steps=8, robot_name=None, env_id=0,
                 grasp_tolerance_m=None):
        self.robot_id, self._name = robot_id, robot_name or robot_id
        state = controller_dict if controller_dict is not None else env._controllers_dict[self._name]
        # Legacy scalar controllers expose coordinates relative to clone
        # origins. Local skill inputs always use world coordinates.
        if 'environment_states' in state:
            controller_dict = state['environment_states'][env_id]
            # Reuse the row view created by the scalar controller loader. It
            # carries the same local-frame convention as the Pepper runtime;
            # constructing a second global view here makes the controller's
            # local IK targets and measured hand poses disagree whenever the
            # scene clone has a non-zero origin.
            views = state.get('environment_views', ())
            if views:
                env = views[env_id]
            else:
                from EAI.controllers.environment_view import SingleEnvironmentView
                env = SingleEnvironmentView(env, env_id, local_coordinates=True)
        self.env = env
        self._controller = controller_dict if controller_dict is not None else state
        self._runtime = self._controller["runtime"]
        if self._controller.get("local_pick_place") is not None:
            raise ValueError("Pepper controller already has a local pick/place backend")
        if getattr(self._runtime.state, "attached", False):
            raise ValueError("legacy Pepper runtime still holds an object")
        self._base = base_pose or (lambda: read_pepper_base_pose(env, self._name))
        self._resolve, self._filter = resolve_object, collision_filter_validator
        if not math.isfinite(lift_m) or lift_m <= 0 or not math.isfinite(tolerance_m) or tolerance_m <= 0:
            raise ValueError("lift and tolerance must be positive")
        if type(stable_steps) is not int or stable_steps <= 0:
            raise ValueError("stable_steps must be positive")
        self._lift, self._tol, self._stable_steps = lift_m, tolerance_m, stable_steps
        self._grasp_tol = (getattr(self._runtime.state.config, "grasp_distance_threshold_m", tolerance_m)
                           if grasp_tolerance_m is None else grasp_tolerance_m)
        if not math.isfinite(self._grasp_tol) or self._grasp_tol <= 0:
            raise ValueError("grasp tolerance must be positive")
        self._command, self._box, self._object_name = None, None, None
        self._phase, self._held, self._stable = "idle", False, 0
        self._elapsed = 0.0
        self._reason, self._stopped, self._frozen = "", False, {}
        self._last_hands = None
        self._controller["local_pick_place"] = self

    def _object(self, ref):
        name = self._resolve(ref)
        objects = self.env.scene.rigid_objects
        if name in objects:
            return name, objects[name]
        for key, obj in objects.items():
            if self._path(obj) == name:
                return key, obj
        raise ValueError("object_ref must resolve to one scene rigid object")

    @staticmethod
    def _path(obj):
        paths = getattr(getattr(obj, "root_physx_view", None), "prim_paths", ())
        path = str(paths[0]) if paths else obj.cfg.prim_path.replace("{ENV_REGEX_NS}", "/World/envs/env_0")
        if not path.startswith("/") or any(t in path for t in (".*", "[", "]")):
            raise ValueError("object path must identify one environment")
        return path

    def resource_key(self, object_ref):
        _, obj = self._object(object_ref)
        return "object:" + self._path(obj)

    def validate(self, command):
        if int(getattr(self.env, "num_envs", 1)) != 1:
            raise ValueError("Pepper local manipulation requires one environment")
        if command.target_orientation_base_wxyz is not None:
            raise ValueError("bilateral Pepper backend does not control object orientation")
        name, box = self._object(command.object_ref)
        if command.operation == "pick":
            if self._held:
                raise ValueError("Pepper already holds an object")
            if math.dist(_values(box.data.root_pos_w[0]), command.target_position_world_m) > self._tol:
                raise ValueError("pick coordinates differ from the live object origin")
            filtered = (name == self._runtime.state.config.cube_prim_name
                        and self._controller.get("collision_filter_installed", False))
            if self._filter is not None:
                filtered = self._filter(self.robot_id, name) is True
            if not filtered:
                raise ValueError("host must prepare Pepper/object collision filtering before physics initialization")
        elif not self._held or name != self._object_name:
            raise ValueError("Pepper does not hold the requested object")
        robot = self.env.scene.articulations[self._name]
        for side in ("left", "right"):
            ids, body = self._runtime._resolve(robot, side)
            if len(ids) < 4 or body is None:
                raise ValueError("Pepper bilateral arm chain is unavailable")

    def start(self, command):
        self._command, self._reason, self._stopped = command, "", False
        self._object_name, self._box = self._object(command.object_ref)
        self._stable, self._frozen, self._last_hands = 0, {}, None
        self._elapsed = 0.0
        # Keep the low-level runtime's lifecycle aligned with the adapter's
        # local operation. Its phase is used only for retained IK state and is
        # advanced by neither the host nor this adapter.
        # The production Pepper runtime exposes lifecycle hooks so its
        # retained IK state can be reset for every local operation.  Hosts
        # may also provide a small controller delegate that only implements
        # the geometry/IK methods used below; in that case there is no
        # lifecycle state to reset and the adapter must remain usable.
        reset = getattr(self._runtime, "reset", None)
        if callable(reset):
            reset()
        start = getattr(self._runtime, "start", None)
        if callable(start):
            start()
        base = self._base()
        if command.operation == "pick":
            self._phase = "approach"
            cfg = self._runtime.state.config
            span = cfg.side_offset
            q = _values(self._box.data.root_quat_w[0])
            self._targets = {side: tuple(command.target_position_world_m[i] + rotate(q, (0., sign*span, 0.))[i]
                                        for i in range(3)) for side, sign in (("left", 1), ("right", -1))}
            self._carry_origin = base.to_world(tuple(command.target_position_base_m[i] + (self._lift if i == 2 else 0.)
                                                   for i in range(3)))
        else:
            self._phase = "place"
            midpoint = tuple(command.target_position_world_m[i] - rotate(base.orientation_wxyz, self._object_offset)[i]
                             for i in range(3))
            self._targets = {side: tuple(midpoint[i] + rotate(base.orientation_wxyz, offset)[i] for i in range(3))
                             for side, offset in self._hand_offsets.items()}

    def before_step(self):
        pass  # native composite apply_action invokes physics_step exactly once

    def after_step(self):
        pass

    def _hands(self, robot):
        return {side: _values(robot.data.body_pos_w[0, self._runtime._resolve(robot, side)[1]])
                for side in ("left", "right")}

    def _sync(self, robot):
        import torch
        hands, base = self._hands(robot), self._base()
        mid = tuple((hands["left"][i] + hands["right"][i])/2 for i in range(3))
        offset = rotate(base.orientation_wxyz, self._object_offset)
        pose = self._box.data.root_pose_w.clone()
        pose[:, :3] = torch.tensor(tuple(mid[i] + offset[i] for i in range(3)), device=pose.device)
        pose[:, 3:7] = torch.tensor(multiply(base.orientation_wxyz, self._object_orientation), device=pose.device)
        self._box.write_root_pose_to_sim(pose)
        self._box.write_root_velocity_to_sim(torch.zeros_like(self._box.data.root_vel_w))

    def physics_step(self, dt_s):
        if self._command is None:
            return
        robot = self.env.scene.articulations[self._name]
        try:
            self._elapsed += float(dt_s)
            hands = self._hands(robot)
            stable = self._last_hands is not None and all(
                math.dist(hands[side], self._last_hands[side]) <= .04 * dt_s
                for side in ("left", "right"))
            self._last_hands = hands
            reached = True
            for side in ("left", "right"):
                ids, body = self._runtime._resolve(robot, side)
                if self._phase in {"holding", "stopped"}:
                    robot.set_joint_position_target(self._frozen[side], joint_ids=ids)
                elif self._phase in {"return", "idle"}:
                    if self._phase == "return":
                        home = self._runtime._home(robot, side, ids)
                        self._runtime._apply_home(robot, side, ids)
                        reached = reached and bool(((robot.data.joint_pos[:, ids]-home).abs() < .05).all())
                else:
                    self._runtime._apply_target(robot, side, ids, body, self._targets[side])
                    distance = self._runtime._measure_reach(robot, side, body, self._targets[side])
                    tolerance = self._grasp_tol if self._phase in {"approach", "grasp"} else self._tol
                    reached = reached and distance <= tolerance
                # Match the retained Pepper/G1 controller ordering: solve the
                # target from the measured articulation state first, then
                # clamp any transient imported-USD limit overshoot before the
                # next physics tick feeds it back into IK.
                self._runtime._enforce_joint_safety(robot, ids)
            if self._held:
                self._sync(robot)
            if self._phase in {"holding", "stopped", "idle"}:
                return
            # Preserve the retained controller's physical approach window:
            # Pepper's high-gain Jacobian needs time to settle before the
            # bilateral reach gate may authorize a grasp.
            if self._phase == "approach":
                if self._elapsed >= 2.0:
                    self._phase, self._stable, self._elapsed = "grasp", 0, 0.0
                return
            self._stable = self._stable + 1 if reached and stable else 0
            if self._stable < self._stable_steps:
                return
            self._stable = 0
            if self._phase == "grasp":
                hands, base = self._hands(robot), self._base()
                mid = tuple((hands["left"][i]+hands["right"][i])/2 for i in range(3))
                obj = _values(self._box.data.root_pos_w[0])
                self._object_offset = rotate(conjugate(base.orientation_wxyz), tuple(obj[i]-mid[i] for i in range(3)))
                self._object_orientation = multiply(conjugate(base.orientation_wxyz), _values(self._box.data.root_quat_w[0]))
                self._hand_offsets = {side: rotate(conjugate(base.orientation_wxyz), tuple(p[i]-mid[i] for i in range(3)))
                                      for side, p in hands.items()}
                self._held, self._phase = True, "lift"
                lift = tuple(self._carry_origin[i]-obj[i] for i in range(3))
                self._targets = {side: tuple(p[i]+lift[i] for i in range(3)) for side, p in hands.items()}
            elif self._phase == "lift":
                if math.dist(_values(self._box.data.root_pos_w[0]), self._carry_origin) > self._tol:
                    return
                self._freeze(robot)
                self._phase, self._stopped = "holding", True
            elif self._phase == "place":
                if math.dist(_values(self._box.data.root_pos_w[0]), self._command.target_position_world_m) > self._tol:
                    return
                # _sync has performed the last attached pose write. No write
                # occurs during RETURN or later external simulator steps.
                self._held, self._phase = False, "return"
            elif self._phase == "return":
                self._phase, self._stopped = "idle", True
        except Exception as exc:
            self._reason = str(exc)
            self.request_stop(self._reason)

    def _freeze(self, robot):
        self._frozen = {side: robot.data.joint_pos[:, self._runtime._resolve(robot, side)[0]].clone()
                        for side in ("left", "right")}
        for target in self._frozen.values():
            _values(target[0])

    def request_stop(self, reason):
        self._reason = reason
        robot = self.env.scene.articulations[self._name]
        self._freeze(robot)
        if self._held:
            self._sync(robot)
        self._phase, self._stopped = "stopped", True

    def feedback(self):
        if self._phase == "idle" and self._command.operation == "place":
            if math.dist(_values(self._box.data.root_pos_w[0]), self._command.target_position_world_m) > self._tol:
                self._reason = "released object moved away from the requested origin"
        complete = self._phase in {"holding", "idle"} and not self._reason
        return BackendFeedback(self._command.request_id,
            "completed" if complete else "stopped" if self._stopped else "running",
            "holding" if self._held else "empty", self._command.object_ref if self._held else None,
            ready=complete, stopped=self._stopped, clean=not self._held, reason=self._reason)

    def reset(self):
        # Called only by the host's explicit simulator reset, never on False.
        self._phase, self._held, self._command = "idle", False, None
        self._box, self._object_name, self._frozen = None, None, {}


__all__ = ["PepperPickPlaceAdapter", "read_pepper_base_pose"]
