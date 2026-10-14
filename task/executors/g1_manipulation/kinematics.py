"""USD joint-frame FK and constrained kinematic grasp playback.

Angles are radians and positions are world metres. This module authors visual
link poses only; the caller owns object attachment, physics and simulation time.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
from pxr import Gf, Usd, UsdGeom, UsdPhysics


def _matrix(position, rotation):
    matrix = Gf.Matrix4d(1.)
    matrix.SetRotate(Gf.Quatd(rotation))
    matrix.SetTranslateOnly(Gf.Vec3d(position))
    return np.asarray(matrix).T.copy()


def _motion(axis, angle, linear=False):
    result = np.eye(4)
    if linear:
        result[axis, 3] = angle
    else:
        i, j = (axis + 1) % 3, (axis + 2) % 3
        result[i, i] = result[j, j] = math.cos(angle)
        result[j, i], result[i, j] = math.sin(angle), -math.sin(angle)
    return result


def _rotation_error(current, target):
    """Return world-frame error vector and exact angular separation."""
    vector = .5 * sum((np.cross(current[:, i], target[:, i]) for i in range(3)), np.zeros(3))
    cosine = np.clip((np.trace(current.T @ target) - 1.) / 2., -1., 1.)
    return vector, float(math.acos(float(cosine)))


def _axis_error(current, target):
    """Return vector difference and angular separation for two unit axes."""
    current = np.asarray(current, dtype=float)
    target = np.asarray(target, dtype=float)
    return target - current, float(math.acos(float(np.clip(np.dot(current, target), -1., 1.))))


def _validated_rotation(value):
    rotation = np.asarray(value, dtype=float)
    if rotation.shape != (3, 3) or not np.isfinite(rotation).all():
        raise ValueError('target_rotation must be a finite 3x3 matrix')
    if not np.allclose(rotation.T @ rotation, np.eye(3), atol=2e-3) or not math.isclose(
            float(np.linalg.det(rotation)), 1., abs_tol=2e-3):
        raise ValueError('target_rotation must be an orthonormal right-handed matrix')
    return rotation


@dataclass
class Joint:
    name: str
    parent: str
    child: str
    frame0: np.ndarray
    frame1_inverse: np.ndarray
    axis: int
    lower: float
    upper: float
    movable: bool
    linear: bool


class KinematicRobot:
    """Read actual USD Physics joint frames and author robot link Xforms."""

    def __init__(self, stage: Any, path: str):
        self.stage, self.path = stage, path
        self.root = stage.GetPrimAtPath(path)
        if not self.root:
            raise ValueError('missing robot: ' + path)
        cache = UsdGeom.XformCache()
        initial, joints = {}, []
        for prim in Usd.PrimRange(self.root):
            if not prim.IsA(UsdPhysics.Joint):
                continue
            joint = UsdPhysics.Joint(prim)
            p0, p1 = joint.GetBody0Rel().GetTargets(), joint.GetBody1Rel().GetTargets()
            if not p0 or not p1:
                continue
            parent, child = str(p0[0]), str(p1[0])
            if not parent.startswith(path + '/') or not child.startswith(path + '/'):
                continue
            for body in (parent, child):
                initial[body] = np.asarray(cache.GetLocalToWorldTransform(stage.GetPrimAtPath(body))).T.copy()
            revolute, linear = prim.IsA(UsdPhysics.RevoluteJoint), prim.IsA(UsdPhysics.PrismaticJoint)
            movable = revolute or linear
            api = UsdPhysics.RevoluteJoint(prim) if revolute else UsdPhysics.PrismaticJoint(prim) if linear else None
            axis = 'XYZ'.index(str(api.GetAxisAttr().Get())) if movable else 0
            lower, upper = (float(api.GetLowerLimitAttr().Get()), float(api.GetUpperLimitAttr().Get())) if movable else (0., 0.)
            if not movable:
                lower = upper = 0.
            elif revolute:
                lower, upper = math.radians(lower), math.radians(upper)
            frame0 = _matrix(joint.GetLocalPos0Attr().Get(), joint.GetLocalRot0Attr().Get())
            frame1 = _matrix(joint.GetLocalPos1Attr().Get(), joint.GetLocalRot1Attr().Get())
            joints.append(Joint(prim.GetName(), parent, child, frame0, np.linalg.inv(frame1), axis,
                                lower, upper, movable, linear))
        if not joints:
            raise ValueError('robot has no connected USD Physics joints')
        children = {joint.child for joint in joints}
        self.base_paths = set(initial) - children
        if not self.base_paths:
            raise ValueError('cyclic joint graph')
        root_world = np.asarray(cache.GetLocalToWorldTransform(self.root)).T.copy()
        self.base_local = {path_: np.linalg.inv(root_world) @ initial[path_] for path_ in self.base_paths}
        self.joints, pending, known = [], list(joints), set(self.base_paths)
        while pending:
            ready = [joint for joint in pending if joint.parent in known]
            if not ready:
                raise ValueError('unresolved/cyclic joint graph')
            for joint in ready:
                self.joints.append(joint)
                known.add(joint.child)
                pending = [item for item in pending if item is not joint]
        self.by_name = {joint.name: joint for joint in self.joints}
        self.body_paths = {path_.rsplit('/', 1)[-1]: path_ for path_ in initial}
        self.angles = {}
        for joint in self.joints:
            relative = (np.linalg.inv(initial[joint.parent] @ joint.frame0) @ initial[joint.child]
                        @ np.linalg.inv(joint.frame1_inverse))
            other, last = (joint.axis + 1) % 3, (joint.axis + 2) % 3
            value = relative[joint.axis, 3] if joint.linear else math.atan2(
                relative[last, other], relative[other, other])
            self.angles[joint.name] = float(np.clip(value, joint.lower, joint.upper)) if joint.movable else 0.
        self.home = dict(self.angles)
        self._ops = {}
        self.last_receipt = None

    def forward(self, angles=None, *, root_world=None):
        q = self.angles if angles is None else angles
        if root_world is None:
            root_world = np.asarray(UsdGeom.XformCache().GetLocalToWorldTransform(self.root)).T.copy()
        world = {path_: root_world @ matrix for path_, matrix in self.base_local.items()}
        for joint in self.joints:
            world[joint.child] = (world[joint.parent] @ joint.frame0
                                  @ _motion(joint.axis, q.get(joint.name, 0), joint.linear)
                                  @ joint.frame1_inverse)
        return world

    def set_joint_positions(self, angles: dict[str, float], *, write=True):
        for name, value in angles.items():
            joint = self.by_name[name]
            if not math.isfinite(value) or value < joint.lower - 1e-8 or value > joint.upper + 1e-8:
                raise ValueError('invalid/out-of-limit angle: ' + name)
        self.angles.update({name: float(value) for name, value in angles.items()})
        if write:
            self.write_pose()

    def write_pose(self):
        world = self.forward()
        cache = UsdGeom.XformCache()
        for body, matrix in world.items():
            if body in self.base_paths:
                continue
            prim = self.stage.GetPrimAtPath(body)
            parent = str(prim.GetParent().GetPath())
            parent_world = world.get(parent)
            if parent_world is None:
                parent_world = np.asarray(cache.GetLocalToWorldTransform(prim.GetParent())).T.copy()
            local = np.linalg.inv(parent_world) @ matrix
            if body not in self._ops:
                xform = UsdGeom.Xformable(prim)
                op = next((op for op in xform.GetOrderedXformOps()
                           if op.GetOpName() == 'xformOp:transform:g1SkillPose'), None)
                if op is None:
                    op = xform.AddTransformOp(UsdGeom.XformOp.PrecisionDouble, 'g1SkillPose')
                xform.SetXformOpOrder([op])
                self._ops[body] = op
            self._ops[body].Set(Gf.Matrix4d(local.T.tolist()))
            cache.Clear()

    def body_world(self, name: str):
        return self.forward()[self.body_paths.get(name, name)]

    def hand_body(self, side='left'):
        for suffix in ('hand_palm_link', 'palm_link', 'rubber_hand', 'hand_base_link', 'wrist_yaw_link'):
            name = side + '_' + suffix
            if name in self.body_paths:
                return name
        raise ValueError('no ' + side + ' palm/wrist body')

    def arm_joints(self, side='left', *, waist=True):
        names = [joint.name for joint in self.joints if joint.movable and joint.name.startswith(side + '_') and
                 any(part in joint.name for part in ('shoulder_', 'elbow_', 'wrist_'))]
        if waist:
            names = [joint.name for joint in self.joints
                     if joint.movable and joint.name.startswith('waist_')] + names
        if len(names) < 3:
            raise ValueError('insufficient arm joints')
        return names

    def solve_ik(self, side, target_world, *, iterations=100, tolerance=.008, waist=True,
                 multistart=False, max_delta=None, write=True, body=None, offset=(0, 0, 0), names=None,
                 target_rotation=None, orientation_weight=.15, orientation_tolerance_rad=.12,
                 joint_bounds=None, target_axis=None, body_axis=1):
        """Solve position and, when supplied, body orientation with bounded DLS."""
        target = np.asarray(target_world, dtype=float)
        if target.shape != (3,) or not np.isfinite(target).all():
            raise ValueError('target must be finite world XYZ')
        if not math.isfinite(tolerance) or tolerance <= 0:
            raise ValueError('tolerance must be finite and positive')
        rotation_target = None if target_rotation is None else _validated_rotation(target_rotation)
        if target_axis is not None:
            if rotation_target is not None:
                raise ValueError('target_rotation and target_axis are mutually exclusive')
            axis_target = np.asarray(target_axis, dtype=float)
            if axis_target.shape != (3,) or not np.isfinite(axis_target).all() or np.linalg.norm(axis_target) < 1e-8:
                raise ValueError('target_axis must be a finite nonzero XYZ vector')
            axis_target /= np.linalg.norm(axis_target)
            if body_axis not in (0, 1, 2):
                raise ValueError('body_axis must be 0, 1, or 2')
        else:
            axis_target = None
        if (rotation_target is not None or axis_target is not None) and (
                not math.isfinite(orientation_weight) or orientation_weight <= 0):
            raise ValueError('orientation_weight must be finite and positive')
        if (rotation_target is not None or axis_target is not None) and (
                not math.isfinite(orientation_tolerance_rad) or orientation_tolerance_rad <= 0):
            raise ValueError('orientation_tolerance_rad must be finite and positive')
        names, body = names or self.arm_joints(side, waist=waist), body or self.hand_body(side)
        path = self.body_paths.get(body, body)
        original = dict(self.angles)
        lows = np.array([self.by_name[name].lower for name in names])
        highs = np.array([self.by_name[name].upper for name in names])
        if joint_bounds:
            for index, name in enumerate(names):
                if name in joint_bounds:
                    low, high = joint_bounds[name]
                    lows[index], highs[index] = max(lows[index], low), min(highs[index], high)
                    if lows[index] > highs[index]:
                        raise ValueError('empty IK bounds for joint: ' + name)
        if max_delta is not None:
            start = np.array([original[name] for name in names])
            lows, highs = np.maximum(lows, start - max_delta), np.minimum(highs, start + max_delta)
        seeds = [np.array([original[name] for name in names])]
        if multistart:
            rng = np.random.default_rng(7)
            seeds += [np.array([self.home[name] for name in names])]
            seed_low, seed_high = np.maximum(lows, -1.3), np.minimum(highs, 1.3)
            outside = seed_low > seed_high
            seed_low[outside] = seed_high[outside] = np.clip(seeds[0], lows, highs)[outside]
            seeds += [rng.uniform(seed_low, seed_high) for _ in range(18)]
        offset4 = np.array([*offset, 1.])
        root_world = np.asarray(UsdGeom.XformCache().GetLocalToWorldTransform(self.root)).T.copy()

        incoming = {joint.child: joint for joint in self.joints}
        ancestors, cursor = set(), path
        while cursor in incoming:
            joint = incoming[cursor]
            ancestors.add(joint.name)
            cursor = joint.parent

        best_metric = float('inf')
        best = (float('inf'), None, seeds[0])
        for seed in seeds:
            values = np.clip(seed, lows, highs)
            for _ in range(iterations):
                q = dict(original)
                q.update(zip(names, values))
                world = self.forward(q, root_world=root_world)
                body_world = world[path]
                point = (body_world @ offset4)[:3]
                position_error = target - point
                position_residual = float(np.linalg.norm(position_error))
                orientation_error = None
                orientation_residual = None
                if rotation_target is not None:
                    orientation_error, orientation_residual = _rotation_error(body_world[:3, :3], rotation_target)
                elif axis_target is not None:
                    orientation_error, orientation_residual = _axis_error(
                        body_world[:3, body_axis], axis_target)
                if orientation_residual is not None:
                    metric = ((position_residual / tolerance) ** 2
                              + (orientation_residual / orientation_tolerance_rad) ** 2)
                else:
                    metric = position_residual
                if metric < best_metric:
                    best_metric = metric
                    best = (position_residual, orientation_residual, values.copy())
                reached = position_residual <= tolerance and (
                    orientation_residual is None or orientation_residual <= orientation_tolerance_rad)
                if reached:
                    break
                position_columns, rotation_columns = [], []
                for name in names:
                    joint = self.by_name[name]
                    frame = world[joint.parent] @ joint.frame0
                    axis = frame[:3, joint.axis]
                    active = name in ancestors
                    position_columns.append(np.zeros(3) if not active else axis if joint.linear
                                            else np.cross(axis, point - frame[:3, 3]))
                    if not active or joint.linear:
                        rotation_columns.append(np.zeros(3))
                    elif axis_target is not None:
                        rotation_columns.append(np.cross(axis, body_world[:3, body_axis]))
                    else:
                        rotation_columns.append(axis)
                jacobian = np.stack(position_columns, axis=1)
                error = position_error
                if rotation_target is not None or axis_target is not None:
                    jacobian = np.vstack((jacobian, orientation_weight * np.stack(rotation_columns, axis=1)))
                    error = np.concatenate((position_error, orientation_weight * orientation_error))
                damping = .025 if rotation_target is not None or axis_target is not None else .015
                dq = jacobian.T @ np.linalg.solve(
                    jacobian @ jacobian.T + damping ** 2 * np.eye(len(error)), error)
                values = np.clip(values + np.clip(dq, -.14, .14), lows, highs)
            if best[0] <= tolerance and (best[1] is None or best[1] <= orientation_tolerance_rad):
                break
        self.angles.update(zip(names, best[2]))
        if write:
            self.write_pose()
        body_world = self.forward(root_world=root_world)[path]
        hand = (body_world @ offset4)[:3]
        orientation_residual = None
        if rotation_target is not None:
            _, orientation_residual = _rotation_error(body_world[:3, :3], rotation_target)
        elif axis_target is not None:
            _, orientation_residual = _axis_error(body_world[:3, body_axis], axis_target)
        reached = float(np.linalg.norm(hand - target)) <= tolerance and (
            orientation_residual is None or orientation_residual <= orientation_tolerance_rad)
        receipt = {
            'residual_m': float(np.linalg.norm(hand - target)),
            'orientation_residual_rad': orientation_residual,
            'reached': reached,
            'hand_world': hand.tolist(),
            'hand_rotation_world': body_world[:3, :3].tolist(),
            'target_world': target.tolist(),
            'target_rotation_world': None if rotation_target is None else rotation_target.tolist(),
            'target_axis_world': None if axis_target is None else axis_target.tolist(),
            'body_axis': None if axis_target is None else body_axis,
            'hand_body': body,
            'joint_angles_rad': dict(self.angles),
            'kinematic_only': True,
        }
        self.last_receipt = receipt
        return receipt
