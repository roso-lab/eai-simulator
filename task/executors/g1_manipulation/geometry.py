"""Configured static boxes and actual visual-mesh samples for clearance checks."""
from dataclasses import dataclass
import math
import numpy as np
from pxr import Usd, UsdGeom


def xyz(value, name):
    if not isinstance(value, (tuple, list, np.ndarray)) or np.shape(value) != (3,):
        raise ValueError(f'{name} must contain three finite world coordinates')
    if any(isinstance(v, (bool, np.bool_)) or not isinstance(v, (int, float, np.number)) for v in value):
        raise ValueError(f'{name} must be numeric, not bool/string')
    result = np.asarray(value, dtype=float)
    if not np.isfinite(result).all():
        raise ValueError(f'{name} must be finite')
    return result


@dataclass(frozen=True)
class ObstacleBox:
    """Static world box; yaw rotates its XY axes. Includes the solid interior."""
    center: tuple[float, float, float]
    half_extents: tuple[float, float, float]
    yaw: float = 0.
    name: str = 'obstacle'

    def __post_init__(self):
        object.__setattr__(self, 'center', tuple(xyz(self.center, 'box center')))
        half = xyz(self.half_extents, 'box half_extents')
        if np.any(half <= 0) or isinstance(self.yaw, bool) or not math.isfinite(self.yaw):
            raise ValueError('box dimensions must be positive and yaw finite')
        object.__setattr__(self, 'half_extents', tuple(half))

    def clearance(self, points):
        c, s = math.cos(self.yaw), math.sin(self.yaw)
        rotation = np.array([[c, -s, 0.], [s, c, 0.], [0., 0., 1.]])
        local = (points - np.asarray(self.center)) @ rotation
        delta = np.abs(local) - self.half_extents
        return np.linalg.norm(np.maximum(delta, 0.), axis=1) + np.minimum(np.max(delta, axis=1), 0.)


def world_matrix(prim):
    return np.asarray(UsdGeom.XformCache().GetLocalToWorldTransform(prim)).T.copy()


def set_world_matrix(prim, matrix):
    from pxr import Gf
    parent = prim.GetParent()
    local = np.linalg.inv(world_matrix(parent)) @ matrix if parent and not parent.IsPseudoRoot() else matrix
    xf = UsdGeom.Xformable(prim)
    op = next((o for o in xf.GetOrderedXformOps() if o.GetOpName() == 'xformOp:transform:g1Skill'), None)
    if op is None:
        op = xf.AddTransformOp(UsdGeom.XformOp.PrecisionDouble, 'g1Skill')
    xf.SetXformOpOrder([op])
    op.Set(Gf.Matrix4d(local.T.tolist()))


class ArmClearance:
    """Checks mesh vertices against configured boxes, not a physics engine."""
    def __init__(self, robot, obstacles):
        self.robot, self.obstacles, self.parts = robot, tuple(obstacles), []
        cache = UsdGeom.XformCache()
        for name, path in robot.body_paths.items():
            if not name.startswith(('right_', 'left_', 'R_', 'L_')):
                continue
            if any(s in name for s in ('hip', 'knee', 'ankle')):
                continue
            inv = np.linalg.inv(np.asarray(cache.GetLocalToWorldTransform(robot.stage.GetPrimAtPath(path))).T)
            points = []
            for prim in Usd.PrimRange(robot.stage.GetPrimAtPath(path)):
                if not prim.IsA(UsdGeom.Mesh) or 'collision' in str(prim.GetPath()).lower():
                    continue
                mesh = UsdGeom.Mesh(prim)
                vertices = np.asarray(mesh.GetPointsAttr().Get(), dtype=float)
                if not len(vertices):
                    continue
                transform = inv @ np.asarray(cache.GetLocalToWorldTransform(prim)).T
                points.append(vertices @ transform[:3, :3].T + transform[:3, 3])
            if points:
                self.parts.append((path, np.unique(np.round(np.vstack(points), 5), axis=0)))

    def measure(self, angles=None):
        if not self.obstacles:
            return math.inf, None
        world = self.robot.forward(angles)
        minimum, blocker = math.inf, None
        for path, points in self.parts:
            transform = world[path]
            vertices = points @ transform[:3, :3].T + transform[:3, 3]
            for box in self.obstacles:
                margin = float(box.clearance(vertices).min())
                if margin < minimum:
                    minimum, blocker = margin, (box.name, path.rsplit('/', 1)[-1])
        return minimum, blocker


def object_local_size(prim):
    """Bound visible geometry in its own frame, without world-AABB inflation."""
    import itertools
    cache = UsdGeom.XformCache()
    inverse = np.linalg.inv(world_matrix(prim))
    points = []
    for child in Usd.PrimRange(prim, Usd.TraverseInstanceProxies()):
        if not child.IsA(UsdGeom.Gprim) or 'collision' in str(child.GetPath()).lower():
            continue
        if UsdGeom.Imageable(child).ComputeVisibility() == 'invisible':
            continue
        if child.IsA(UsdGeom.Mesh):
            vertices = np.asarray(UsdGeom.Mesh(child).GetPointsAttr().Get(), dtype=float)
        else:
            extent = UsdGeom.Boundable.ComputeExtentFromPlugins(UsdGeom.Boundable(child), Usd.TimeCode.Default())
            if extent is None or len(extent) != 2:continue
            vertices = np.array(list(itertools.product(*zip(*extent))))
        if not len(vertices):continue
        matrix = inverse @ np.asarray(cache.GetLocalToWorldTransform(child)).T
        points.append(vertices @ matrix[:3,:3].T + matrix[:3,3])
    if not points:return None
    vertices = np.vstack(points)
    return vertices.max(0) - vertices.min(0)
