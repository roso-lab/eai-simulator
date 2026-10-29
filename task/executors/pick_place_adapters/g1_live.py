"""Calibrated G1 trajectories driven through live articulation targets.

Planning and USD pose playback happen only on an anonymous shadow stage.
The shared simulator owns every physics step. The handle writes robot targets
and never reads or synchronizes a payload.
"""
from __future__ import annotations

from dataclasses import replace
import hashlib
import math
from pathlib import Path
import numpy as np
from pxr import Gf, Usd, UsdPhysics

from task.controllers.g1_pick_place.runtime import G1PickPlaceSkill, GraspProfile, ObjectBinding
from task.controllers.g1_pick_place.geometry import world_matrix, set_world_matrix
from .skills import ExecutionReceipt
from task.contracts import DependencyOutcome
from algorithm.common_coordination.contract import FailureCode


def pose_matrix(position, quaternion):
    p = np.asarray(position.detach().cpu() if hasattr(position, 'detach') else position, dtype=float)
    q = np.asarray(quaternion.detach().cpu() if hasattr(quaternion, 'detach') else quaternion, dtype=float)
    if not np.isfinite(p).all() or not np.isfinite(q).all() or np.linalg.norm(q) < 1e-9:
        raise RuntimeError('nonfinite live pose')
    q = q / np.linalg.norm(q)
    matrix = Gf.Matrix4d(1.)
    matrix.SetRotate(Gf.Quatd(float(q[0]), Gf.Vec3d(*map(float, q[1:]))))
    matrix.SetTranslateOnly(Gf.Vec3d(*map(float, p)))
    return np.asarray(matrix).T


def matrix_pose(matrix, device):
    import torch
    q = Gf.Matrix4d(matrix.T.tolist()).ExtractRotationQuat()
    return torch.tensor([[*matrix[:3, 3], q.GetReal(), *q.GetImaginary()]], device=device, dtype=torch.float32)


class G1LiveHandle:
    # Class defaults keep __new__-based gate fixtures working without __init__.
    base_mode = 'fixed'
    base_drift_tolerance_m = .015

    def __init__(self, plan, *, view, stage, robot_name, robot_path, objects,
                 config=None, obstacles=(), settle_ticks=10, defer_start=True,
                 base_mode='fixed', base_drift_tolerance_m=.015):
        from EAI.controllers.environment_view import environment_path
        self.base_mode = base_mode
        self.base_drift_tolerance_m = float(base_drift_tolerance_m)
        if self.base_mode not in ('fixed', 'stationary_floating'):
            raise ValueError("base_mode must be 'fixed' or 'stationary_floating'")
        if not (math.isfinite(self.base_drift_tolerance_m) and 0 < self.base_drift_tolerance_m):
            raise ValueError('base drift tolerance must be positive and finite')
        self.plan, self.view, self.robot_name = plan, view, robot_name
        self.robot = view.scene.articulations[robot_name]
        if self.base_mode == 'fixed':
            if not self.robot.is_fixed_base:
                raise ValueError('G1 live manipulation requires a fixed articulation base')
            if self.base_drift_tolerance_m > .015:
                raise ValueError('fixed-base grip calibration allows at most 1.5 cm base drift')
        elif self.robot.is_fixed_base:
            raise ValueError(
                "stationary_floating base_mode expects the walking (floating-base) "
                "G1 instance; the calibrated fixed asset must use base_mode='fixed'")
        if self.base_mode == 'stationary_floating' and self.base_drift_tolerance_m > .25:
            raise ValueError('stationary grasp envelope must stay below 25 cm')
        self.cancel_reason, self.error, self.receipt = None, None, None
        self.stable_ticks, self.settle_ticks, self.elapsed = 0, settle_ticks, 0.
        self._target = None
        robot_path = environment_path(robot_path, view.env_id)
        bindings = {}
        for ref, binding in objects.items():
            path = environment_path(binding.prim_path, view.env_id)
            profile = GraspProfile.builtin(binding.profile) if isinstance(binding.profile, str) else binding.profile
            if profile.robot_asset_sha256:
                layers = {spec.layer.realPath for spec in stage.GetPrimAtPath(robot_path).GetPrimStack() if spec.layer.realPath}
                if not any(hashlib.sha256(Path(p).read_bytes()).hexdigest() == profile.robot_asset_sha256 for p in layers):
                    raise ValueError('live G1 asset differs from the grip calibration')
            bindings[ref] = ObjectBinding(path, replace(profile, robot_asset_sha256=None))
        shadow = Usd.Stage.Open(stage.Flatten())
        for prim in shadow.Traverse():
            if prim.HasAPI(UsdPhysics.RigidBodyAPI):
                UsdPhysics.RigidBodyAPI(prim).GetRigidBodyEnabledAttr().Set(False)
            if prim.IsA(UsdPhysics.Joint):
                UsdPhysics.Joint(prim).GetJointEnabledAttr().Set(False)
        self.skill = G1PickPlaceSkill(shadow, robot_path, bindings, config=config,
                                     obstacles=obstacles, robot_id=plan.robot_id)
        kinematic = self.skill.robot
        base_path = next(iter(kinematic.base_paths))
        if len(kinematic.base_paths) != 1:
            raise ValueError('live G1 requires one articulation base')
        self.base_id = self._body_id(Path(base_path).name)
        self.hand_id = self._body_id(kinematic.hand_body('right').rsplit('/', 1)[-1])
        root = self._body_pose(self.base_id) @ np.linalg.inv(kinematic.base_local[base_path])
        self.root = root.copy()
        set_world_matrix(kinematic.root, root)
        self.joint_ids = {}
        measured = {}
        for name, joint in kinematic.by_name.items():
            if not joint.movable:
                continue
            ids, _ = self.robot.find_joints([name], preserve_order=True)
            if len(ids) != 1:
                raise ValueError(f'live G1 joint missing or ambiguous: {name}')
            self.joint_ids[name] = int(ids[0])
            measured[name] = float(self.robot.data.joint_pos[0, ids[0]])
        kinematic.set_joint_positions(measured)
        # Action-only PickPlace never reads or synchronizes payload poses. The
        # shadow stage is used solely for robot kinematics and calibration.
        if not defer_start:
            raise ValueError("G1LiveHandle is started by G1PickPlaceAdapter")

    def _body_id(self, name):
        ids, _ = self.robot.find_bodies([name], preserve_order=True)
        if len(ids) != 1:
            raise ValueError(f'live G1 body missing or ambiguous: {name}')
        return int(ids[0])

    def _body_pose(self, index):
        return pose_matrix(self.robot.data.body_pos_w[0, index], self.robot.data.body_quat_w[0, index])

    def command(self):
        import torch
        # Base controllers receive neutral commands; this handle owns arm and
        # finger targets at the final post-apply hook, without stepping.
        cfg = self.view.cfg.action_spaces[self.robot_name]
        if type(cfg) is not int:
            raise ValueError('G1 requires a flat numeric action space')
        return torch.zeros((1, cfg), device=self.view.device)

    def cancel(self, reason):
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError('cancel reason must be nonempty')
        self.cancel_reason = reason
        # Continue a started transfer to its registered placement. Do not
        # release a grasp merely because the task was cancelled.

    def _write_target(self):
        import torch
        names = list(self._target)
        self.robot.set_joint_position_target(
            torch.tensor([[self._target[n] for n in names]], device=self.view.device),
            joint_ids=[self.joint_ids[n] for n in names])

    def advance(self, dt):
        if self.receipt is not None:
            return
        try:
            guard = getattr(self, '_ownership_guard', None)
            if guard is not None:
                guard()
        except Exception as exc:
            # A revoked owner cannot keep writing even when already faulted.
            self.error = str(exc)
            return
        if self.error is not None:
            if self._target is not None:
                self._write_target()
            return
        try:
            self.elapsed += dt
            if self.elapsed > self.skill.config.timeout_s:
                raise RuntimeError('live G1 trajectory timeout; ownership retained')
            root = self._body_pose(self.base_id) @ np.linalg.inv(self.skill.robot.base_local[next(iter(self.skill.robot.base_paths))])
            drift = float(np.linalg.norm(root[:3, 3] - self.root[:3, 3]))
            if drift > self.base_drift_tolerance_m:
                raise RuntimeError(
                    f'G1 base moved {drift:.3f} m beyond the stationary grasp '
                    f'envelope ({self.base_drift_tolerance_m} m); grasp and '
                    'locomotion are exclusive modes; ownership retained')
            if self._target is not None:
                error = max(abs(float(self.robot.data.joint_pos[0, self.joint_ids[n]]) - v) for n, v in self._target.items())
                if not math.isfinite(error):
                    raise RuntimeError('nonfinite G1 joint observation')
                if error > .06:
                    self._write_target()
                    return
            result = self.skill.transfer.step(dt, self.skill.pick, self.skill.place)
            self._target = {n: result['joint_angles_rad'][n] for n in self.skill.transfer.names + list(self.skill.transfer.closed)}
            self._write_target()
        except Exception as exc:
            self.error = str(exc)

    def after_step(self):
        if self.error is not None:
            raise RuntimeError(self.error)
        if self.receipt is not None:
            return self.receipt
        import torch
        stopped = (self.skill.transfer.phase == 'done'
                   and bool(torch.isfinite(self.robot.data.joint_vel).all())
                   and bool((self.robot.data.joint_vel.abs() < .1).all()))
        self.stable_ticks = self.stable_ticks + 1 if stopped else 0
        if self.stable_ticks >= self.settle_ticks:
            cancelled = self.cancel_reason is not None
            self.receipt = ExecutionReceipt(self.plan.request_id, self.plan.skill_id, self.plan.robot_id,
                DependencyOutcome.TERMINAL_FAILURE if cancelled else DependencyOutcome.COMPLETED,
                True, FailureCode.SAFE_STOP if cancelled else FailureCode.NONE,
                self.cancel_reason or 'live targets complete, ideal attachment released, measured motion settled')
        return self.receipt


class G1LiveRuntime:
    def __init__(self, env):
        self.env, self.handles = env, {}
        if getattr(env, '_g1_pick_place_runtime', None) is not None:
            raise ValueError('one G1 runtime may own a shared environment')
        env._g1_pick_place_runtime = self

    def add(self, env_id, robot_name, handle):
        key = (env_id, robot_name)
        previous = self.handles.get(key)
        if previous is not None and previous.receipt is None:
            raise ValueError('G1 row already has an active or unresolved operation')
        self.handles[key] = handle
        return handle

    def run_post_apply(self, env):
        if env is not self.env:
            raise ValueError('G1 runtime belongs to another scene')
        dt = float(env.cfg.sim.dt)
        for handle in self.handles.values():
            handle.advance(dt)

    def reset(self, env_ids=None):
        ids = set(range(self.env.num_envs) if env_ids is None else map(int, env_ids))
        for (env_id, name), handle in list(self.handles.items()):
            if env_id in ids and handle.receipt is None:
                handle.error = 'environment reset invalidated the G1 task; reconcile task leases'
                # The environment reset owns object state restoration. Do not
                handle._target = None
            if env_id in ids:
                # Old handles remain invalid for callers, but a restored row
                # can accept a new operation after its leases are reconciled.
                del self.handles[(env_id, name)]

    def close(self):
        self.reset()
        if getattr(self.env, '_g1_pick_place_runtime', None) is self:
            self.env._g1_pick_place_runtime = None
