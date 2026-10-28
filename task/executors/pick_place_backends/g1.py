"""Local G1 adapter with calibrated fingers and measured palms.

Default binding is the fixed-base calibrated asset. base_mode='stationary_floating'
binds the walking G1 instance: the DWAQ policy holds a standing balance with a
zero velocity command while this adapter drives the calibrated right-arm/finger
trajectory; a drift envelope enforces the exclusive walk/grasp mode contract.

Only the planning shadow receives USD joint transforms. The host post-apply
hook drives live articulation targets and maintains the ideal attachment.
"""
import math
from types import SimpleNamespace

import numpy as np

from algorithm.skills.pick_place import BackendFeedback
from task.executors.g1_live import G1LiveHandle, G1LiveRuntime, pose_matrix


class G1PickPlaceAdapter(G1LiveHandle):
    # Geometry/IK and stationary-base checks are diagnostic for the benchmark
    # contract; logical pick/place is admitted by semantic arrival + payload.
    idealized_semantics = True

    _hold_active = False

    def __init__(self, robot_id, env, *, stage, robot_name, robot_path, objects,
                 config=None, obstacles=(), env_id=0, settle_ticks=10,
                 position_tolerance_m=.015, base_mode='fixed',
                 base_drift_tolerance_m=.015):
        from EAI.controllers.environment_view import SingleEnvironmentView
        if not math.isfinite(position_tolerance_m) or not 0 < position_tolerance_m <= .02:
            raise ValueError('G1 position tolerance must be positive and <= 2 cm')
        self.robot_id, self.env_id, self._env = robot_id, env_id, env
        view = SingleEnvironmentView(env, env_id)
        super().__init__(SimpleNamespace(robot_id=robot_id), view=view, stage=stage,
            robot_name=robot_name, robot_path=robot_path, objects=objects,
            config=config, obstacles=obstacles, settle_ticks=settle_ticks,
            defer_start=True, base_mode=base_mode,
            base_drift_tolerance_m=base_drift_tolerance_m)
        self._command, self._phase, self._stopped = None, 'idle', True
        self._reason, self._tol, self._planner = '', position_tolerance_m, None
        self._stable, self._home = 0, None
        runtime = getattr(env, '_g1_manipulation_runtime', None)
        self._host_runtime = runtime if runtime is not None else G1LiveRuntime(env)
        self._host_runtime.add(env_id, robot_name, self)

    def resource_key(self, object_ref):
        if object_ref not in self.skill.objects:
            raise ValueError('G1 requires a registered calibrated object')
        return 'object:' + self.skill.objects[object_ref].prim_path

    def _sync_shadow(self):
        from task.executors.g1_manipulation.geometry import set_world_matrix
        kinematic = self.skill.robot
        root = self._body_pose(self.base_id) @ np.linalg.inv(kinematic.base_local[next(iter(kinematic.base_paths))])
        drift = float(np.linalg.norm(root[:3, 3] - self.root[:3, 3]))
        if drift > self.base_drift_tolerance_m:
            raise ValueError(
                f'G1 base moved {drift:.3f} m beyond the stationary grasp envelope '
                f'({self.base_drift_tolerance_m} m); grasp and locomotion are exclusive modes')
        set_world_matrix(kinematic.root, root)
        kinematic.set_joint_positions({n: float(self.robot.data.joint_pos[0, i]) for n, i in self.joint_ids.items()})
        for ref, binding in self.skill.objects.items():
            obj = self.view.scene.rigid_objects[ref]
            set_world_matrix(self.skill.stage.GetPrimAtPath(binding.prim_path),
                             pose_matrix(obj.data.root_pos_w[0], obj.data.root_quat_w[0]))

    def validate(self, command):
        self.resource_key(command.object_ref)
        if self._host_runtime.handles.get((self.env_id, self.robot_name)) is not self:
            raise ValueError('G1 host reset or replaced the backend; reconcile ownership')
        if command.target_orientation_base_wxyz is not None:
            raise ValueError('calibrated G1 backend only handles upright axial objects')
        obj = self.view.scene.rigid_objects[command.object_ref]
        position = np.asarray(obj.data.root_pos_w[0].detach().cpu(), dtype=float)
        if not np.isfinite(position).all():
            raise ValueError('nonfinite G1 object observation')
        if command.operation == 'pick':
            if self.attached:
                raise ValueError('G1 already holds an object')
            if np.linalg.norm(position-command.target_position_world_m) > self.skill.config.object_match_tolerance_m:
                raise ValueError('pick target differs from live G1 object origin')
        elif not self.attached or command.object_ref != self.skill.object_ref:
            raise ValueError('G1 place requires its held object')

    def _begin_stationary_hold(self, driven_joint_names):
        """Pin the floating base and brace all non-driven joints.

        This emulates the calibrated fixed-base binding on the walking G1:
        with the locomotion controller suspended, the root stays at its
        captured standing pose and non-driven joints hold their measured
        targets while the calibrated right-arm trajectory executes.
        """
        if self.base_mode != 'stationary_floating' or self._hold_active:
            return
        self._hold_active = True
        self._hold_root_pose = self.robot.data.root_state_w[:, :7].clone()
        driven = set(driven_joint_names)
        self._hold_ids = [i for i, n in enumerate(self.robot.joint_names) if n not in driven]
        self._hold_targets = (self.robot.data.joint_pos[:, self._hold_ids].clone()
                              if self._hold_ids else None)

    def _apply_stationary_hold(self):
        if not self._hold_active:
            return
        import torch
        self.robot.write_root_pose_to_sim(self._hold_root_pose)
        self.robot.write_root_velocity_to_sim(
            torch.zeros_like(self.robot.data.root_vel_w))
        if self._hold_ids:
            self.robot.set_joint_position_target(self._hold_targets, joint_ids=self._hold_ids)

    def release_stationary_hold(self):
        """Relinquish the grasp-mode brace; the caller resumes locomotion."""
        self._hold_active = False

    def start(self, command):
        from task.executors.g1_manipulation.planner import TransferPlanner
        self._command, self._reason, self.error = command, '', None
        self._stopped, self._stable, self.elapsed = False, 0, 0.
        self._sync_shadow()
        if command.operation == 'pick':
            self._home = dict(self.skill.robot.angles)
            self.skill.status, self.skill.attached = 'idle', False
            self.skill.start_pick(command.target_position_world_m, object_ref=command.object_ref)
            self.object = self.view.scene.rigid_objects[command.object_ref]
            self._planner = self.skill.transfer
        else:
            profile = self.skill.transfer.profile
            planner = TransferPlanner(self.skill.robot, profile, self.skill.config, self.skill.obstacles)
            # Use the actual grasp offset. The commanded target is the object
            # origin, never a nominal palm/grip location copied from pickup.
            palm = self._body_pose(self.hand_id)
            target = np.asarray(command.target_position_world_m)
            grip = target + palm[:3, :3] @ (planner.pocket-self.relative[:3, 3])
            planner.prepare_place(grip, home=self._home)
            self._planner = self.skill.transfer = planner
            self.skill.place = target
        self._begin_stationary_hold(
            set(self._planner.names) | set(self._planner.closed))
        self._target = None
        self._phase = self._planner.phase

    def before_step(self):
        pass  # the host invokes G1LiveRuntime once per physics step

    def advance(self, dt):
        self._apply_stationary_hold()
        if self._command is None:
            return
        try:
            if self._phase == 'stopped' and self._stopped:
                # Keep the stop-time measured target; following later joint
                # drift would progressively relax the carrying arm.
                self._write_target()
                self._follow()
                return
            if self.error is not None:
                raise RuntimeError(self.error)
            root = self._body_pose(self.base_id) @ np.linalg.inv(self.skill.robot.base_local[next(iter(self.skill.robot.base_paths))])
            drift = float(np.linalg.norm(root[:3, 3] - self.root[:3, 3]))
            if drift > self.base_drift_tolerance_m:
                raise RuntimeError(
                    f'G1 base moved {drift:.3f} m beyond the stationary grasp envelope; '
                    'ownership retained')
            if self._phase in {'holding', 'stopped', 'idle'}:
                if self._phase != 'idle' and self._target is not None:
                    self._write_target()
                self._follow()
                return
            self.elapsed += dt
            if self._target is not None:
                errors = [abs(float(self.robot.data.joint_pos[0, self.joint_ids[n]])-v)
                          for n, v in self._target.items()]
                if not all(math.isfinite(e) for e in errors):
                    raise ValueError('nonfinite G1 joint observation')
                if max(errors) > .06:
                    self._write_target()
                    self._follow()
                    return
            result = self._planner.step(dt, self.skill.pick, self.skill.place)
            names = self._planner.names + list(self._planner.closed)
            self._target = {n: result['joint_angles_rad'][n] for n in names}
            self._write_target()
            palm = self._body_pose(self.hand_id)
            if result['request_attach']:
                matrix = pose_matrix(self.object.data.root_pos_w[0], self.object.data.root_quat_w[0])
                grip = (palm @ np.r_[self._planner.pocket, 1])[:3]
                object_grip = (matrix @ np.r_[self._planner.object_point, 1])[:3]
                fingers = all(abs(float(self.robot.data.joint_pos[0, self.joint_ids[n]])-v) < .06
                              for n, v in self._planner.closed.items())
                if np.linalg.norm(grip-object_grip) < .008 and palm[2, 2] < -.995 and fingers:
                    self.relative, self.attached = np.linalg.inv(palm) @ matrix, True
                    self._planner.acknowledge_attachment(True)
            self._follow()
            if result['request_release'] and self.attached:
                position = (palm @ self.relative)[:3, 3]
                if np.linalg.norm(position-self._command.target_position_world_m) < self._tol:
                    self.attached = False
                    self._planner.acknowledge_attachment(False)
            self._phase = self._planner.phase
        except Exception as exc:
            self.request_stop(str(exc))

    def after_step(self):
        if self._command is None:
            return
        self._follow()
        terminal = ((self._command.operation == 'pick' and self._phase == 'holding' and self.attached)
                    or (self._command.operation == 'place' and self._phase == 'done' and not self.attached))
        if not terminal or self._reason:
            return
        import torch
        finite = bool(torch.isfinite(self.robot.data.joint_vel).all())
        settled = finite and bool((self.robot.data.joint_vel.abs() < .1).all())
        position = np.asarray(self.object.data.root_pos_w[0].detach().cpu(), dtype=float)
        if self._command.operation == 'pick':
            # Confirm actual lift, rather than a completed planned trajectory.
            settled = settled and position[2] >= self._command.target_position_world_m[2] + self.skill.config.lift_height_m-.015
        else:
            settled = settled and bool(torch.isfinite(self.object.data.root_vel_w).all())
            settled = settled and bool((self.object.data.root_vel_w[:, :3].norm(dim=-1) < .05).all())
            settled = settled and bool((self.object.data.root_vel_w[:, 3:].norm(dim=-1) < .1).all())
            if np.linalg.norm(position-self._command.target_position_world_m) > self._tol:
                self.request_stop('released G1 object moved away from the requested origin')
                return
        self._stable = self._stable + 1 if settled else 0
        if self._stable >= self.settle_ticks:
            self._stopped = True
            self._phase = 'holding' if self.attached else 'idle'

    def request_stop(self, reason):
        self._reason = self.error = reason
        self._stopped = False
        names = self._planner.names + list(self._planner.closed) if self._planner else list(self.joint_ids)
        self._target = {n: float(self.robot.data.joint_pos[0, self.joint_ids[n]]) for n in names}
        if not all(math.isfinite(v) for v in self._target.values()):
            raise ValueError('cannot confirm G1 stop with nonfinite joint observations')
        self._write_target()
        self._follow()
        self._phase, self._stopped = 'stopped', True

    def feedback(self):
        complete = self._phase in {'holding', 'idle'} and self._stopped and not self._reason
        return BackendFeedback(self._command.request_id,
            'completed' if complete else 'stopped' if self._stopped else 'running',
            'holding' if self.attached else 'empty', self._command.object_ref if self.attached else None,
            ready=complete, stopped=self._stopped, clean=not self.attached, reason=self._reason)


__all__ = ['G1PickPlaceAdapter']
