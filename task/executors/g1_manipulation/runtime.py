"""Caller-stepped, fixed-base G1 Inspire right-hand ideal pick/place skill.

This adapter owns USD kinematic writes. Bind it only to a paused/kinematic
scene; it does not drive a live PhysX articulation or advance simulation time.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping
import json, math, hashlib
import numpy as np
from pxr import Usd, UsdGeom, UsdPhysics
from .geometry import ObstacleBox, xyz, world_matrix, set_world_matrix, object_local_size
from .kinematics import KinematicRobot
from .planner import TransferPlanner


class G1SkillError(RuntimeError):
    def __init__(self, code, reason):
        self.code = code
        super().__init__(f'{code}: {reason}')


@dataclass(frozen=True)
class GraspProfile:
    """Calibrated local grasp frames and finger closure for one asset geometry."""
    name: str
    hand_point: tuple[float, float, float]
    object_point: tuple[float, float, float]
    closed_joints: Mapping[str, float]
    robot_asset_sha256: str | None = None
    object_size_m: tuple[float, float, float] | None = None

    def __post_init__(self):
        if self.object_size_m is not None:
            size = xyz(self.object_size_m, 'object_size_m')
            if np.any(size <= 0):raise ValueError('object_size_m must be positive')
            object.__setattr__(self, 'object_size_m', tuple(size))
        object.__setattr__(self, 'hand_point', tuple(xyz(self.hand_point, 'hand_point')))
        object.__setattr__(self, 'object_point', tuple(xyz(self.object_point, 'object_point')))
        if np.linalg.norm(self.object_point[:2]) > 1e-9:
            raise ValueError('upright axial grasp requires object_point on local Z axis')
        joints = dict(self.closed_joints)
        if not joints or any(not isinstance(n, str) or not n.startswith('R_') or isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for n, v in joints.items()):
            raise ValueError('closed_joints must contain finite right-hand finger joint angles')
        object.__setattr__(self, 'closed_joints', joints)

    @classmethod
    def builtin(cls, name):
        data = json.loads(Path(__file__).with_name('inspire_grips.json').read_text())
        if data.get('schema_version') != 1 or data.get('coordinate_frame') != 'right_hand_base_link' or data.get('cylinder_axis_hand_base') != [0., 0., 1.]:
            raise ValueError('unsupported built-in grasp calibration schema/frame/axis')
        if name not in data['objects']:
            raise ValueError(f'unsupported grasp profile: {name}')
        obj = data['objects'][name]
        return cls(name, tuple(obj['cylinder_axis_point_hand_base_m']),
                   tuple(obj['object_grip_point_local_m']), obj['closed_finger_joint_positions_rad'],
                   data['source_asset_sha256'], tuple(obj['object_full_size_m']))


@dataclass(frozen=True)
class ObjectBinding:
    prim_path: str
    profile: GraspProfile | str = 'bottle_neck'


@dataclass(frozen=True)
class G1PickPlaceConfig:
    # Robot-local ready point; transformed by the actual fixed root pose.
    ready_position_base: tuple[float, float, float] = (.30, -.12, .45)
    lift_height_m: float = .08
    waypoint_spacing_m: float = .012
    joint_speed_rad_s: float = .75
    position_tolerance_m: float = .0015
    clearance_m: float = .006
    object_match_tolerance_m: float = .02
    max_step_s: float = .10
    timeout_s: float = 90.
    # Minimum world-z component of the robot root for a grasp start. The
    # calibrated fixed asset stands exactly upright (.999); a stationary
    # walking-G1 binding may widen this to its measured standing lean while
    # measured attach/release gates still own grasp correctness.
    root_upright_min_z: float = .999
    ik_seed: Mapping[str, float] = field(default_factory=lambda: {
        'waist_yaw_joint': .023, 'right_shoulder_pitch_joint': -1.212,
        'right_shoulder_roll_joint': -.990, 'right_shoulder_yaw_joint': .066,
        'right_elbow_joint': .843, 'right_wrist_roll_joint': .476,
        'right_wrist_pitch_joint': .348, 'right_wrist_yaw_joint': .654,
    })

    def __post_init__(self):
        object.__setattr__(self, 'ready_position_base', tuple(xyz(self.ready_position_base, 'ready_position_base')))
        for name in ('lift_height_m','waypoint_spacing_m','joint_speed_rad_s','position_tolerance_m',
                     'clearance_m','object_match_tolerance_m','max_step_s','timeout_s'):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f'{name} must be positive and finite')
        if self.max_step_s > .1 or self.position_tolerance_m > .003 or self.waypoint_spacing_m > .025:
            raise ValueError('step/tolerance/spacing exceeds supported planning limits')
        if self.waypoint_spacing_m < .002:
            raise ValueError('waypoint_spacing_m must be at least .002')
        if any(not isinstance(n, str) or isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for n,v in self.ik_seed.items()):
            raise ValueError('ik_seed must contain finite numeric joint angles')
        if not isinstance(self.root_upright_min_z, (int, float)) or isinstance(self.root_upright_min_z, bool) \
                or not math.isfinite(self.root_upright_min_z) or not .9 <= self.root_upright_min_z <= 1.:
            raise ValueError('root_upright_min_z must lie in [0.9, 1.0]')
        object.__setattr__(self, 'root_upright_min_z', float(self.root_upright_min_z))
        object.__setattr__(self, 'ik_seed', dict(self.ik_seed))


class G1PickPlaceSkill:
    """Bind once, then start(pick_position, place_position) and step(dt).

    Coordinates are world metres of the registered object's Xform origin,
    normally its centered mesh origin, not a surface or wrist coordinate.
    Object registrations supply geometry-dependent grip profiles. A single
    unambiguous registered object must be near the requested pickup position.
    """
    def __init__(self, stage, robot_path, objects: Mapping[str, ObjectBinding], *,
                 obstacles=(), config=None, robot_id='g1_1'):
        self.stage, self.robot_path, self.robot_id = stage, robot_path, robot_id
        self.config = config or G1PickPlaceConfig()
        self.obstacles = tuple(obstacles)
        if any(not isinstance(o, ObstacleBox) for o in self.obstacles):
            raise ValueError('obstacles must contain ObstacleBox records')
        self.objects = dict(objects)
        if not self.objects or any(not isinstance(v, ObjectBinding) for v in self.objects.values()):
            raise ValueError('register at least one ObjectBinding')
        if len({v.prim_path for v in self.objects.values()}) != len(self.objects):
            raise ValueError('object paths must be unique')
        if UsdGeom.GetStageUpAxis(stage) != 'Z' or not math.isclose(UsdGeom.GetStageMetersPerUnit(stage), 1.):
            raise G1SkillError('unsupported_capability', 'stage must use Z-up and metres')
        self.robot = KinematicRobot(stage, robot_path)
        self.status, self.phase = 'idle', 'idle'
        self.transfer = None
        self.attached = False
        self.events = []
        self.error = None
        self._elapsed = 0.
        self._require_kinematic()

    def _require_kinematic(self):
        # Do not disable a user's physics or compete with the runtime controller.
        for path in [self.robot_path] + [b.prim_path for b in self.objects.values()]:
            prim = self.stage.GetPrimAtPath(path)
            if not prim or not prim.IsActive() or not prim.IsA(UsdGeom.Xformable):
                raise G1SkillError('invalid_binding', f'missing/inactive Xformable {path}')
            for child in Usd.PrimRange(prim):
                if child.HasAPI(UsdPhysics.RigidBodyAPI):
                    api = UsdPhysics.RigidBodyAPI(child)
                    if api.GetRigidBodyEnabledAttr().Get() and not api.GetKinematicEnabledAttr().Get():
                        raise G1SkillError('unsupported_capability', f'dynamic rigid body {child.GetPath()}; use a kinematic scene')
                if child.IsA(UsdPhysics.Joint) and UsdPhysics.Joint(child).GetJointEnabledAttr().Get():
                    raise G1SkillError('unsupported_capability', 'active physics joint; use a kinematic scene')

    def start_pick(self, pick_position, *, object_ref=None):
        """Prepare the local pickup trajectory without planning a transfer."""
        return self.start(pick_position, pick_position, object_ref=object_ref, _local_pick=True)

    def start(self, pick_position, place_position, *, object_ref=None, _local_pick=False):
        if self.status in ('running','holding') or self.attached:
            raise G1SkillError('busy', 'finish, resume, or cancel the current operation first')
        pick, place = xyz(pick_position, 'pick_position'), xyz(place_position, 'place_position')
        self._require_kinematic()
        candidates = []
        for key, binding in self.objects.items():
            actual = world_matrix(self.stage.GetPrimAtPath(binding.prim_path))
            if np.linalg.norm(actual[:3,3] - pick) <= self.config.object_match_tolerance_m:
                candidates.append(key)
        if object_ref is not None:
            if object_ref not in self.objects or object_ref not in candidates:
                raise G1SkillError('invalid_request', 'object_ref missing or not at pickup position')
            candidates = [object_ref]
        if len(candidates) != 1:
            raise G1SkillError('invalid_request', 'pickup must match exactly one registered object; provide object_ref for ambiguity')
        key = candidates[0];binding = self.objects[key]
        obj = world_matrix(self.stage.GetPrimAtPath(binding.prim_path))
        if not np.allclose(obj[:3,:3].T @ obj[:3,:3], np.eye(3), atol=1e-4) or obj[2,2] < .999:
            raise G1SkillError('unsupported_capability', 'object must be upright with scale baked into its mesh')
        root = world_matrix(self.robot.root)
        if any(np.linalg.norm(p - root[:3,3]) > 1.5 for p in (pick, place)):
            raise G1SkillError('infeasible', 'target exceeds the fixed-base workspace')
        if not np.allclose(root[:3,:3].T @ root[:3,:3], np.eye(3), atol=1e-4) or root[2,2] < self.config.root_upright_min_z:
            raise G1SkillError('unsupported_capability', 'G1 root must be upright and unscaled')
        profile = GraspProfile.builtin(binding.profile) if isinstance(binding.profile, str) else binding.profile
        if not isinstance(profile, GraspProfile):
            raise G1SkillError('invalid_binding', 'profile must be a calibrated GraspProfile')
        object_prim = self.stage.GetPrimAtPath(binding.prim_path)
        size = object_local_size(object_prim)
        if size is None:
            raise G1SkillError('invalid_binding', 'registered object has no visible geometry bounds')
        if profile.object_size_m is not None and not np.allclose(size, profile.object_size_m, rtol=.02, atol=.002):
            raise G1SkillError('invalid_binding', 'object size differs from grip calibration; calibrate its profile')
        if self.robot.hand_body('right') != 'right_hand_base_link':
            raise G1SkillError('unsupported_capability', 'calibrated right_hand_base_link is required')
        if profile.robot_asset_sha256:
            layers = {spec.layer.realPath for spec in self.robot.root.GetPrimStack() if spec.layer.realPath}
            if not any(hashlib.sha256(Path(p).read_bytes()).hexdigest() == profile.robot_asset_sha256 for p in layers):
                raise G1SkillError('invalid_binding', 'robot asset does not match grasp calibration; bind a newly calibrated profile')
        for name, value in profile.closed_joints.items():
            joint = self.robot.by_name.get(name)
            if joint is None or not joint.lower <= value <= joint.upper:
                raise G1SkillError('unsupported_capability', f'finger joint unavailable/out of range: {name}')
        for name, value in self.config.ik_seed.items():
            joint = self.robot.by_name.get(name)
            if joint is None or not joint.lower <= value <= joint.upper:
                raise G1SkillError('invalid_binding', f'IK seed joint unavailable/out of range: {name}')
        transfer = TransferPlanner(self.robot, profile, self.config, self.obstacles)
        # Use the measured origin at pickup; no pre-grasp object teleport.
        try:
            if _local_pick:
                transfer.prepare_pick(obj[:3,3])
            else:
                transfer.prepare(obj[:3,3], place)
        except (RuntimeError, ValueError) as error:
            raise G1SkillError('infeasible', str(error)) from error
        self.transfer, self.object_ref, self.object_path = transfer, key, binding.prim_path
        self.pick, self.place, self._root = obj[:3,3].copy(), place.copy(), root
        self._expected_object = obj.copy()
        self._elapsed = 0.;self.events = [];self.error = None
        self.status, self.phase, self.attached = 'running', 'prepare', False
        return self.receipt()

    def _check_state(self):
        if not np.allclose(world_matrix(self.robot.root), self._root, atol=1e-7, rtol=0):
            raise G1SkillError('state_changed', 'fixed robot root moved; stop and replan')
        actual = world_matrix(self.stage.GetPrimAtPath(self.object_path))
        if not np.allclose(actual, self._expected_object, atol=1e-6, rtol=0):
            raise G1SkillError('state_changed', 'bound object moved externally')
        cache = UsdGeom.XformCache()
        expected = self.robot.forward()
        for path, matrix in expected.items():
            measured = np.asarray(cache.GetLocalToWorldTransform(self.stage.GetPrimAtPath(path))).T
            if not np.allclose(measured, matrix, atol=2e-5, rtol=0):
                raise G1SkillError('state_changed', 'robot links changed externally')

    def step(self, dt):
        if isinstance(dt, bool) or not isinstance(dt, (float,int)) or not math.isfinite(dt) or not 0 < dt <= self.config.max_step_s:
            raise ValueError(f'dt must be in (0, {self.config.max_step_s}] seconds')
        if self.status != 'running':
            return self.receipt()
        try:
            self._check_state()
            if self._elapsed + dt > self.config.timeout_s:
                raise G1SkillError('execution_failure', 'operation timed out; attachment preserved')
            self._elapsed += dt
            r = self.transfer.step(dt, self.pick, self.place)
            self.phase = r['phase'];frame = np.asarray(r['grip_world_transform'])
            prim = self.stage.GetPrimAtPath(self.object_path)
            if r['request_attach']:
                point = (self._expected_object @ np.r_[self.transfer.object_point,1])[:3]
                if np.linalg.norm(point-frame[:3,3]) > .006:
                    raise G1SkillError('execution_failure', 'fingers did not reach the registered object grip')
                self._relative = np.linalg.inv(frame) @ self._expected_object
                self.transfer.acknowledge_attachment(True);self.attached = True
                self.events.append({'event':'attached','seconds':self._elapsed,'object_ref':self.object_ref})
            if self.attached:
                self._expected_object = frame @ self._relative
                set_world_matrix(prim, self._expected_object)
            if r['request_release']:
                if np.linalg.norm(self._expected_object[:3,3]-self.place) > .008:
                    raise G1SkillError('execution_failure', 'object not at place position; attachment preserved')
                self.transfer.acknowledge_attachment(False);self.attached = False
                self.events.append({'event':'released','seconds':self._elapsed,'object_ref':self.object_ref})
            if self.phase == 'done':
                self.status = 'completed'
        except Exception as error:
            self.status = 'holding'
            self.error = str(error)
            raise
        return self.receipt()

    def stop(self):
        """Freeze the operation; any held object stays attached. No detach/reset."""
        if self.status == 'running':self.status = 'holding'
        return self.receipt()

    def resume(self):
        if self.status != 'holding' or self.error is not None:
            raise G1SkillError('invalid_state', 'only a deliberately paused operation can resume')
        self._check_state();self.status = 'running'
        return self.receipt()

    def cancel(self):
        if self.attached:
            raise G1SkillError('holding_object', 'cannot cancel/drop a held object; resume to place it')
        self.status = 'cancelled';return self.receipt()

    def receipt(self):
        return {'status':self.status,'phase':self.phase,'elapsed_s':self._elapsed,
                'attached':self.attached,'object_ref':getattr(self,'object_ref',None),
                'events':list(self.events),'error':self.error,'physical_success':None,
                'mode':'fixed_base_kinematic_ideal_attachment',
                'obstacle_check':('configured boxes / robot arm visual vertices only' if self.obstacles else 'not configured; endpoint-only raised path')}


__all__ = ['G1PickPlaceSkill','G1PickPlaceConfig','GraspProfile','ObjectBinding','ObstacleBox','G1SkillError']
