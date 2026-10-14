"""UR5/Z1 adapter for the host's existing idealized manipulation runtime."""
from copy import copy
import math

from algorithm.skills.aerial_transport.geometry import RigidPose, rotate, vector
from algorithm.skills.pick_place import BackendFeedback


class ArmPickPlaceAdapter:
    def __init__(self, robot_id, runtime, *, resolve_object=lambda ref: ref,
                 grasp_offset_object_m=(0., 0., 0.), lift_m=.15,
                 reachable=None, position_tolerance_m=.035, robot_name=None, env_id=0):
        self.robot_id, self._name = robot_id, robot_name or robot_id
        if getattr(runtime, '_dispatcher', False):
            runtime = runtime._runtime_for_env(env_id)
        self.runtime, self._resolve = runtime, resolve_object
        self._offset = vector(grasp_offset_object_m)
        if not math.isfinite(lift_m) or lift_m <= 0:
            raise ValueError("lift_m must be positive")
        if not math.isfinite(position_tolerance_m) or position_tolerance_m <= 0:
            raise ValueError("position_tolerance_m must be positive")
        self._lift, self._reachable, self._tolerance = lift_m, reachable, position_tolerance_m
        self._command, self._path, self._stopped = None, None, False
        self._release_verified = False
        runtime.preserve_holding_on_failure(self._name)

    def resource_key(self, object_ref):
        path = self._resolve(object_ref)
        if not isinstance(path, str) or not path.startswith("/"):
            raise ValueError("object must resolve to an absolute rigid-body path")
        return "object:" + path

    def _target(self, command):
        # The live arm root includes installation translation AND rotation.
        # Never assume it coincides with the caller's chassis root.
        from EAI.manipulation_geometry import CylindricalPose
        binding = self.runtime._binding(self._name, disable_collisions=False)
        arm_pose = RigidPose(*self.runtime._base_pose(binding))
        target = command.target_position_world_m
        path = self._resolve(command.object_ref)
        if command.operation == "pick":
            obj = RigidPose(*self.runtime._rigid_body_pose(path))
            if math.dist(obj.position_m, target) > self._tolerance:
                raise ValueError("pick coordinates do not match the live object origin")
            delta = rotate(obj.orientation_wxyz, self._offset)
            ee = tuple(target[i] + delta[i] for i in range(3))
        else:
            held = self.runtime.held_object(self._name)
            if held is None or held.object_body_path != path:
                raise ValueError("place requires the backend's held body")
            ee = target
            # Target is the object origin, not the wrist. Solve the radial-yaw
            # dependent tool rotation together with the measured grasp offset.
            predicted = copy(self.runtime._states[self._name])
            for _ in range(80):
                pose = self.runtime._cylindrical_from_world(ee, arm_pose.position_m,
                    arm_pose.orientation_wxyz, plane_offset_m=binding.planar_spec.plane_offset_m)
                predicted.command_pose = pose
                _, q = self.runtime._target_world_pose(binding, predicted)
                delta = rotate(q, held.relative_object_position_ee)
                revised = tuple(target[i] - delta[i] for i in range(3))
                if math.dist(ee, revised) < 1e-6:
                    ee = revised
                    break
                ee = tuple((ee[i] + revised[i]) / 2 for i in range(3))
            else:
                raise ValueError("object-origin placement has no converged tool target")
        pose = self.runtime._cylindrical_from_world(ee, arm_pose.position_m,
            arm_pose.orientation_wxyz, plane_offset_m=binding.planar_spec.plane_offset_m)
        current = self.runtime._states.get(self._name)
        current = None if current is None else current.command_pose
        radius = pose.x_m if current is None else min(pose.x_m, current.x_m)
        height = pose.z_m + self._lift if current is None else max(pose.z_m + self._lift, current.z_m)
        return pose, CylindricalPose(radius, height, pose.yaw_rad)

    def validate(self, command):
        if not self.runtime.enabled:
            raise ValueError("arm runtime is unavailable")
        if command.target_orientation_base_wxyz is not None:
            raise ValueError("cylindrical arm backend does not support arbitrary object orientation")
        pose, recovery = self._target(command)
        if self._reachable is not None and not all(self._reachable(p) is True for p in (pose, recovery)):
            raise ValueError("arm target or recovery is unreachable")

    def start(self, command):
        self._command, self._path = command, self._resolve(command.object_ref)
        self._stopped, self._release_verified = False, False
        pose, recovery = self._target(command)
        phase_steps = max(300, math.ceil(command.timeout_s / self.runtime._physics_step_duration_s()))
        if command.operation == "pick":
            accepted = self.runtime.start_pick(self._name, self._path, pose, recovery_pose=recovery,
                                               phase_timeout_steps=phase_steps)
        else:
            accepted = self.runtime.start_place(self._name, pose, recovery_pose=recovery,
                                                phase_timeout_steps=phase_steps)
        if not accepted:
            raise ValueError(self.runtime.last_error(self._name) or "arm rejected command")

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


__all__ = ["ArmPickPlaceAdapter"]
