"""Cartesian PickPlace adapter for the host's UR5/Z1 manipulation runtime."""
import math

from algorithm.skills.aerial_transport.geometry import rotate
from algorithm.skills.pick_place import BackendFeedback


# The source object is isolated (collision, gravity and motion are disabled)
# before the UR5 enters GRASP.  A 5 mm arrival gate therefore lets the Robotiq
# fingers close at the requested grasp pose instead of accepting a 50 mm early
# arrival from the generic idealized runtime.
UR5_PICK_APPROACH_TOLERANCE_M = 0.005

# cartesian_pose mode aligns the end-effector tool axis (local +Z) with the
# approach direction.  A grasp frame whose axis disagrees with the declared
# approach direction is rejected instead of silently re-oriented.
CARTESIAN_TOOL_AXIS_TOLERANCE_RAD = 0.35
DEFAULT_MOTION_SPEED_SCALE = {"ur5": 6.0, "z1": 5.0}


class ArmPickPlaceAdapter:
    def __init__(self, robot_id, runtime, *, resolve_object=lambda ref: ref,
                 position_tolerance_m=.035, robot_name=None, env_id=0,
                 reachable_cartesian=None, motion_speed_scale=None):
        self.robot_id, self._name = robot_id, robot_name or robot_id
        if getattr(runtime, '_dispatcher', False):
            runtime = runtime._runtime_for_env(env_id)
        self.runtime, self._resolve = runtime, resolve_object
        if not math.isfinite(position_tolerance_m) or position_tolerance_m <= 0:
            raise ValueError("position_tolerance_m must be positive")
        self._tolerance = position_tolerance_m
        binding = runtime._binding(self._name, disable_collisions=False)
        self._model = str(getattr(binding.model_spec, "model", "")).strip().lower()
        if motion_speed_scale is None:
            motion_speed_scale = DEFAULT_MOTION_SPEED_SCALE.get(self._model, 1.0)
        try:
            motion_speed_scale = float(motion_speed_scale)
        except (TypeError, ValueError):
            raise ValueError("motion_speed_scale must be finite and positive") from None
        if not math.isfinite(motion_speed_scale) or motion_speed_scale <= 0.0:
            raise ValueError("motion_speed_scale must be finite and positive")
        self.motion_speed_scale = motion_speed_scale
        self._reachable_cartesian = reachable_cartesian
        self._command, self._path, self._stopped = None, None, False
        self._release_verified = False
        self._grasp_tool = None
        runtime.preserve_holding_on_failure(self._name)

    def _runtime_tolerance(self, operation):
        """Return the phase arrival gate for the selected arm model."""
        if operation != "pick":
            return self._tolerance
        if self._model in {"ur5", "z1"}:
            return min(self._tolerance, UR5_PICK_APPROACH_TOLERANCE_M)
        return self._tolerance

    def resource_key(self, object_ref):
        path = self._resolve(object_ref)
        if not isinstance(path, str) or not path.startswith("/"):
            raise ValueError("object must resolve to an absolute rigid-body path")
        return "object:" + path

    def _cartesian_targets(self, command):
        """World-frame contact/pregrasp/retreat waypoints for cartesian_pose."""
        from EAI.manipulation_geometry import (
            _normalize_quaternion, _quat_multiply, _quat_rotate, cartesian_place_contact_pose,
        )
        approach = command.approach_direction_world_m
        if approach is None:
            raise ValueError("cartesian_pose requires approach_direction_world")
        approach = tuple(float(value) for value in approach)
        norm = math.sqrt(sum(value * value for value in approach))
        if norm <= 1e-9:
            raise ValueError("approach_direction_world must be nonzero")
        approach = tuple(value / norm for value in approach)
        if command.grasp_offset_object_m is not None:
            raise ValueError(
                "grasp_offset_object_m belongs to vertical_legacy; use grasp_pose_object"
            )
        if command.grasp_pose_object_position_m is None or command.grasp_pose_object_orientation_wxyz is None:
            raise ValueError("cartesian_pose requires grasp_pose_object")
        path = self._resolve(command.object_ref)
        if command.operation == "pick":
            position, orientation = self.runtime._rigid_body_pose(path)
            if math.dist(position, command.target_position_world_m) > self._tolerance:
                raise ValueError("pick coordinates do not match the live object origin")
            offset = rotate(orientation, command.grasp_pose_object_position_m)
            contact = tuple(position[i] + offset[i] for i in range(3))
            contact_orientation = _normalize_quaternion(_quat_multiply(
                orientation, command.grasp_pose_object_orientation_wxyz,
            ))
            tcp_orientation = contact_orientation
            self._grasp_tool = self.runtime.grasp_tool_for_object(self._name, path, tcp_orientation)
            contact, contact_orientation = self._grasp_tool.wrist_pose(contact, tcp_orientation)
        else:
            held = self.runtime.held_object(self._name)
            if held is None or held.object_body_path != path:
                raise ValueError("place requires the backend's held body")
            binding = self.runtime._binding(self._name, disable_collisions=False)
            _, ee_orientation = self.runtime._end_effector_pose(binding)
            tool = self.runtime._states[self._name].grasp_tool
            contact, contact_orientation = cartesian_place_contact_pose(
                held.relative_object_position_ee, ee_orientation, approach,
                command.target_position_world_m,
                tool_axis_ee=((0., 0., 1.) if tool is None else
                              _quat_rotate(tool.orientation_ee_wxyz, (0., 0., 1.))),
            )
            tcp_orientation = contact_orientation if tool is None else _quat_multiply(
                contact_orientation, tool.orientation_ee_wxyz)
        # Tool axis convention: finger-centre TCP local +Z points away from the
        # object, i.e. along -approach at the contact pose.
        ez = self._rotate_z(tcp_orientation)
        alignment = sum(ez[i] * approach[i] for i in range(3))
        if alignment > -math.cos(CARTESIAN_TOOL_AXIS_TOLERANCE_RAD):
            raise ValueError(
                "grasp_pose_object tool axis must oppose approach_direction_world "
                "(finger-centre TCP +Z points away from the object)"
            )
        pregrasp = tuple(contact[i] - command.pregrasp_distance_m * approach[i] for i in range(3))
        retreat = tuple(contact[i] - command.retreat_distance_m * approach[i] for i in range(3))
        return contact, contact_orientation, pregrasp, retreat

    @staticmethod
    def _rotate_z(quaternion_wxyz):
        x, y, z, w = quaternion_wxyz[1], quaternion_wxyz[2], quaternion_wxyz[3], quaternion_wxyz[0]
        return (
            2.0 * (x * z + w * y),
            2.0 * (y * z - w * x),
            1.0 - 2.0 * (x * x + y * y),
        )

    def _check_cartesian_reachable(self, waypoints):
        if self._reachable_cartesian is None:
            return
        for position, orientation in waypoints:
            if self._reachable_cartesian(position, orientation) is not True:
                raise ValueError("cartesian waypoint is unreachable")

    def _validate_cartesian(self, command):
        if command.target_orientation_base_wxyz is not None:
            raise ValueError("arm cartesian_pose mode does not command object orientation")
        for name in ("pregrasp_distance_m", "retreat_distance_m", "approach_speed_m_s"):
            value = getattr(command, name)
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be positive")
        contact, orientation, pregrasp, retreat = self._cartesian_targets(command)
        self._check_cartesian_reachable((
            (pregrasp, orientation), (contact, orientation), (retreat, orientation),
        ))
        del contact, orientation, pregrasp, retreat

    def validate(self, command):
        if not self.runtime.enabled:
            raise ValueError("arm runtime is unavailable")
        if getattr(command, "approach_mode", None) != "cartesian_pose":
            raise ValueError("UR5/Z1 arm backend only supports cartesian_pose")
        if command.grasp_offset_object_m is not None:
            raise ValueError("grasp_offset_object_m is not supported by the Cartesian arm backend")
        self._validate_cartesian(command)

    def start(self, command):
        if getattr(command, "approach_mode", None) != "cartesian_pose":
            raise ValueError("UR5/Z1 arm backend only supports cartesian_pose")
        if command.grasp_offset_object_m is not None:
            raise ValueError("grasp_offset_object_m is not supported by the Cartesian arm backend")
        self._command, self._path = command, self._resolve(command.object_ref)
        self._stopped, self._release_verified = False, False
        phase_steps = max(300, math.ceil(command.timeout_s / self.runtime._physics_step_duration_s()))
        contact, orientation, _, _ = self._cartesian_targets(command)
        tolerance = self._runtime_tolerance(command.operation)
        if command.operation == "pick":
            accepted = self.runtime.start_pick_cartesian(
                self._name, self._path,
                contact_position_w=contact,
                contact_orientation_wxyz=orientation,
                approach_direction_w=command.approach_direction_world_m,
                pregrasp_distance_m=command.pregrasp_distance_m,
                retreat_distance_m=command.retreat_distance_m,
                approach_speed_m_s=command.approach_speed_m_s,
                motion_speed_scale=self.motion_speed_scale,
                tolerance_m=tolerance,
                phase_timeout_steps=phase_steps,
            )
        else:
            accepted = self.runtime.start_place_cartesian(
                self._name,
                target_object_position_w=command.target_position_world_m,
                approach_direction_w=command.approach_direction_world_m,
                pregrasp_distance_m=command.pregrasp_distance_m,
                retreat_distance_m=command.retreat_distance_m,
                approach_speed_m_s=command.approach_speed_m_s,
                motion_speed_scale=self.motion_speed_scale,
                tolerance_m=self._tolerance,
                phase_timeout_steps=phase_steps,
            )
        if not accepted:
            raise ValueError(self.runtime.last_error(self._name) or "arm rejected cartesian command")
        if command.operation == "pick":
            self.runtime.configure_grasp_tool(self._name, self._grasp_tool)

    def before_step(self):
        pass  # env.run_post_apply owns the native physics-rate controller

    def after_step(self):
        if self._command.operation == "place" and not self._release_verified:
            if self.runtime.held_object(self._name) is None:
                p, _ = self.runtime._rigid_body_pose(self._path)
                if math.dist(p, self._command.target_position_world_m) > self._tolerance:
                    raise ValueError("released object missed the requested origin")
                self._release_verified = True

    def feedback(self):
        c = self._command
        state = self.runtime._states.get(self._name)
        held = self.runtime.held_object(self._name)
        if c is None:
            return BackendFeedback("", "stopped", "holding" if held is not None else "empty",
                stopped=held is None, clean=held is None and (state is None or state.pending_isolation is None))
        unknown = state is not None and state.release_started
        grasp = "unknown" if unknown else "holding" if held is not None else "empty"
        error = self.runtime.last_error(self._name)
        phase = self.runtime.state(self._name).value
        complete = (c.operation == "pick" and phase == "attached_hold" and held is not None
                    or c.operation == "place" and phase == "idle" and self._release_verified)
        if complete and c.operation == "place":
            position, _ = self.runtime._rigid_body_pose(self._path)
            if math.dist(position, c.target_position_world_m) > self._tolerance:
                error = "object moved away from the requested origin during recovery"
        status = "stopped" if self._stopped else "failed" if error else "completed" if complete else "running"
        clean = held is None and state is not None and state.pending_isolation is None and not unknown
        return BackendFeedback(c.request_id, status, grasp,
            c.object_ref if held is not None else None, ready=complete and not error,
            stopped=self._stopped or (complete and not error), clean=clean, reason=error or "")

    def request_stop(self, reason):
        self._stopped = self.runtime.stop_retaining_object(self._name, reason)


__all__ = ["ArmPickPlaceAdapter", "CARTESIAN_TOOL_AXIS_TOLERANCE_RAD", "UR5_PICK_APPROACH_TOLERANCE_M"]
