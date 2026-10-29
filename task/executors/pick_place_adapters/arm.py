"""Cartesian PickPlace adapter for the host's UR5/Z1 manipulation runtime."""
import math

from algorithm.skills.pick_place import BackendFeedback


# The close arrival gate keeps the fixed action deterministic on UR5/Z1.
UR5_PICK_APPROACH_TOLERANCE_M = 0.005

# cartesian_pose mode aligns the end-effector tool axis (local +Z) with the
# approach direction.  A grasp frame whose axis disagrees with the declared
# approach direction is rejected instead of silently re-oriented.
CARTESIAN_TOOL_AXIS_TOLERANCE_RAD = 0.35
DEFAULT_MOTION_SPEED_SCALE = {"ur5": 6.0, "z1": 5.0}


class ArmPickPlaceAdapter:
    # The adapter only drives a fixed down/up trajectory. Logical holding is
    # recorded by PickPlaceExecutor and never mirrored into the scene.
    action_only = True

    def __init__(self, robot_id, runtime, *, resolve_object=lambda ref: ref,
                 position_tolerance_m=.035, robot_name=None, env_id=0,
                 reachable_cartesian=None, motion_speed_scale=None,
                 grasp_offset_object_m=None):
        del grasp_offset_object_m
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
        self._action_complete = False

    def _runtime_tolerance(self, operation):
        """Return the phase arrival gate for the selected arm model."""
        if operation != "pick":
            return self._tolerance
        if self._model in {"ur5", "z1"}:
            return min(self._tolerance, UR5_PICK_APPROACH_TOLERANCE_M)
        return self._tolerance

    def resource_key(self, object_ref):
        if not isinstance(object_ref, str) or not object_ref.strip():
            raise ValueError("object_ref must be nonempty text")
        return "object:" + object_ref.strip()

    def _cartesian_targets(self, command):
        """Build the robot's fixed down/up action around its measured TCP."""
        del command
        binding = self.runtime._binding(self._name, disable_collisions=False)
        position, orientation = self.runtime._end_effector_pose(binding)
        approach = (0.0, 0.0, -1.0)
        contact = tuple(position[i] + 0.06 * approach[i] for i in range(3))
        pregrasp = tuple(contact[i] - 0.08 * approach[i] for i in range(3))
        retreat = tuple(contact[i] - 0.12 * approach[i] for i in range(3))
        return contact, orientation, pregrasp, retreat, approach

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
        contact, orientation, pregrasp, retreat, approach = self._cartesian_targets(command)
        self._check_cartesian_reachable((
            (pregrasp, orientation), (contact, orientation), (retreat, orientation),
        ))
        del contact, orientation, pregrasp, retreat

    def validate(self, command):
        if not self.runtime.enabled:
            raise ValueError("arm runtime is unavailable")
        self._validate_cartesian(command)

    def start(self, command):
        self._command, self._path = command, None
        self._stopped, self._release_verified = False, False
        phase_steps = max(300, math.ceil(command.timeout_s / self.runtime._physics_step_duration_s()))
        contact, orientation, _, _, approach = self._cartesian_targets(command)
        self._action_complete = False
        accepted = self.runtime.start_action_cartesian(
            self._name, operation=command.operation,
            contact_position_w=contact,
            contact_orientation_wxyz=orientation,
            approach_direction_w=approach,
            pregrasp_distance_m=.08,
            retreat_distance_m=.12,
            approach_speed_m_s=.05,
            motion_speed_scale=self.motion_speed_scale,
            tolerance_m=self._runtime_tolerance(command.operation),
            phase_timeout_steps=phase_steps,
        )
        if not accepted:
            raise ValueError(self.runtime.last_error(self._name) or "arm rejected action command")

    def before_step(self):
        pass  # env.run_post_apply owns the native physics-rate controller

    def after_step(self):
        state = self.runtime._states.get(self._name)
        self._action_complete = bool(state is not None and state.phase.value == "idle")

    def feedback(self):
        c = self._command
        state = self.runtime._states.get(self._name)
        if c is None:
            return BackendFeedback("", "stopped", "empty", stopped=True, clean=True)
        complete = self._action_complete and not (state is not None and state.last_error)
        grasp = "holding" if c.operation == "pick" and complete else "empty"
        error = self.runtime.last_error(self._name)
        status = "stopped" if self._stopped else "failed" if error else "completed" if complete else "running"
        clean = True
        return BackendFeedback(c.request_id, status, grasp,
            c.object_ref if grasp == "holding" else None, ready=complete and not error,
            stopped=self._stopped or (complete and not error), clean=clean, reason=error or "")

    def request_stop(self, reason):
        self._stopped = self.runtime.stop_retaining_object(self._name, reason)


__all__ = ["ArmPickPlaceAdapter", "CARTESIAN_TOOL_AXIS_TOLERANCE_RAD", "UR5_PICK_APPROACH_TOLERANCE_M"]
