"""Static collision-triangle support queries shared by authoring and deployment.

OpenUSD and NumPy are lazy dependencies. Queries do not use visual visibility,
assume a robot body clearance, or replace collision/contact simulation.
"""
from __future__ import annotations
import math


class SupportSurface:
    def __init__(self, points, triangles, *, source_paths=()):
        import numpy as np
        self.points = np.asarray(points, dtype=float).reshape(-1, 3)
        self.triangles = np.asarray(triangles, dtype=int).reshape(-1, 3)
        self.source_paths = tuple(source_paths)
        if (not len(self.triangles) or not np.isfinite(self.points).all()
                or self.triangles.min() < 0 or self.triangles.max() >= len(self.points)):
            raise ValueError("invalid or empty support triangles")
        tri = self.points[self.triangles]
        normal = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
        # Reject near-vertical walls. Keep walkable inclines and both
        # orientations of horizontal surfaces, as collision meshes can be double sided.
        norm = np.linalg.norm(normal,axis=1)
        keep = (norm > 1e-12) & (abs(normal[:,2]) >= math.cos(math.radians(70))*norm)
        self._tri = tri[keep]
        self._xy_min = self._tri[:, :, :2].min(axis=1)
        self._xy_max = self._tri[:, :, :2].max(axis=1)
        self._bins = {}

    def intersections(self, x, y):
        import numpy as np
        if not math.isfinite(x) or not math.isfinite(y):
            raise ValueError("support XY must be finite")
        p = np.array([x, y])
        key = (math.floor(x / .5), math.floor(y / .5))
        if key not in self._bins:
            low = np.array(key) * .5
            mask = ((self._xy_min <= low + .5 + 1e-8)
                    & (self._xy_max >= low - 1e-8)).all(axis=1)
            self._bins[key] = (self._tri[mask], self._xy_min[mask], self._xy_max[mask])
        triangles, lower, upper = self._bins[key]
        mask = ((lower <= p + 1e-8) & (upper >= p - 1e-8)).all(axis=1)
        tri = triangles[mask]
        if not len(tri):
            return []
        a, b, c = tri[:, 0], tri[:, 1], tri[:, 2]
        v, w, q = b - a, c - a, p - a[:, :2]
        det = v[:, 0] * w[:, 1] - v[:, 1] * w[:, 0]
        u = (q[:, 0] * w[:, 1] - q[:, 1] * w[:, 0]) / det
        t = (v[:, 0] * q[:, 1] - v[:, 1] * q[:, 0]) / det
        inside = (u >= -1e-7) & (t >= -1e-7) & (u + t <= 1 + 1e-7)
        heights = a[:, 2] + u * v[:, 2] + t * w[:, 2]
        return sorted(set(float(z) for z in heights[inside]))

    def below(self, position, *, max_drop_m=2.0):
        if len(position) != 3 or not all(math.isfinite(v) for v in position):
            raise ValueError("support query requires finite XYZ")
        if not math.isfinite(max_drop_m) or max_drop_m <= 0:
            raise ValueError("max support drop must be positive and finite")
        hits = [z for z in self.intersections(*position[:2])
                if position[2] - max_drop_m <= z <= position[2] + 1e-5]
        if not hits:
            raise ValueError("no collision support below measured robot pose")
        return max(hits)

    def nearest(self, position, *, max_distance_m=0.25):
        if len(position) != 3 or not all(math.isfinite(v) for v in position):
            raise ValueError("support projection requires finite XYZ")
        if not math.isfinite(max_distance_m) or max_distance_m <= 0:
            raise ValueError("support distance must be positive and finite")
        heights = self.intersections(*position[:2])
        if not heights:
            raise ValueError("no collision support at requested XY")
        z = min(heights, key=lambda height: abs(height - position[2]))
        if abs(z - position[2]) > max_distance_m:
            raise ValueError("no support surface near requested level/height")
        return float(position[0]), float(position[1]), z

    @classmethod
    def from_stage(cls, stage, *, mesh_paths=()):
        import numpy as np
        from pxr import Usd, UsdGeom, UsdPhysics
        if UsdGeom.GetStageUpAxis(stage) != "Z" or UsdGeom.GetStageMetersPerUnit(stage) != 1:
            raise ValueError("support source must use metres and Z-up")
        cache = UsdGeom.XformCache()
        points, triangles, paths, offset = [], [], [], 0
        requested = set(mesh_paths)
        for prim in stage.Traverse():
            if not prim.IsA(UsdGeom.Mesh):
                continue
            path = str(prim.GetPath())
            if requested and path not in requested:
                continue
            collision = UsdPhysics.CollisionAPI(prim)
            if not collision or collision.GetCollisionEnabledAttr().Get() is False:
                continue
            ancestor = prim
            while ancestor and not ancestor.IsPseudoRoot():
                body = UsdPhysics.RigidBodyAPI(ancestor)
                if body and body.GetRigidBodyEnabledAttr().Get() is not False:
                    raise ValueError(f"support source must be static, found rigid body: {ancestor.GetPath()}")
                xform = UsdGeom.Xformable(ancestor)
                if xform and xform.GetTimeSamples():
                    raise ValueError(f"animated support transform is not supported: {ancestor.GetPath()}")
                ancestor = ancestor.GetParent()
            mesh = UsdGeom.Mesh(prim)
            if any(attr.ValueMightBeTimeVarying() for attr in
                   (mesh.GetPointsAttr(),mesh.GetFaceVertexCountsAttr(),mesh.GetFaceVertexIndicesAttr(),
                    collision.GetCollisionEnabledAttr())):
                raise ValueError(f"animated support mesh is not supported: {path}")
            p = np.asarray(mesh.GetPointsAttr().Get(), dtype=float)
            counts = np.asarray(mesh.GetFaceVertexCountsAttr().Get(), dtype=int)
            indices = np.asarray(mesh.GetFaceVertexIndicesAttr().Get(), dtype=int)
            if not len(p) or not len(counts) or not np.all(counts == 3):
                raise ValueError(f"support source must be a nonempty triangle mesh: {path}")
            valid, reason = UsdGeom.Mesh.ValidateTopology(indices.tolist(), counts.tolist(), len(p))
            if not valid:
                raise ValueError(f"invalid support mesh {path}: {reason}")
            matrix = np.asarray(cache.GetLocalToWorldTransform(prim))
            world = np.column_stack((p, np.ones(len(p)))) @ matrix
            points.append(world[:, :3])
            triangles.append(indices.reshape(-1, 3) + offset)
            offset += len(p)
            paths.append(path)
        if requested - set(paths):
            raise ValueError("missing/disabled collision support meshes: " + str(sorted(requested - set(paths))))
        if not points:
            raise ValueError("source contains no supported collision triangle meshes")
        return cls(np.concatenate(points), np.concatenate(triangles), source_paths=paths)

    def create_raycast_proxy(self, stage, path):
        from pxr import UsdGeom, Vt
        mesh = UsdGeom.Mesh.Define(stage, path)
        mesh.CreatePointsAttr().Set(Vt.Vec3fArray.FromNumpy(self.points.astype("float32")))
        mesh.CreateFaceVertexCountsAttr().Set([3] * len(self.triangles))
        mesh.CreateFaceVertexIndicesAttr().Set(Vt.IntArray.FromNumpy(self.triangles.astype("int32").reshape(-1)))
        mesh.CreateSubdivisionSchemeAttr().Set(UsdGeom.Tokens.none)
        mesh.CreateVisibilityAttr().Set(UsdGeom.Tokens.invisible)
        # Intentionally no collision API: this proxy is only the sensor's view
        # of the same static triangles, not duplicate physical geometry.
        return mesh
