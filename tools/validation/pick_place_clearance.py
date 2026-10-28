"""Conservative per-physics-step robot mesh bounds versus a solid table.

Each visual/collision mesh is enclosed by a body-local box. Separating-axis
tests include box face normals and all edge cross products (15 axes). A
positive gap certifies separation of the enclosed mesh; overlap is rejected
even if the actual mesh might fit. No camera visibility or rendered depth is
used for acceptance.

If a box overlaps, refine against the mesh's convex hull. This still encloses
the full mesh volume, but does not reject empty corners of a coarse box.
"""
from __future__ import annotations

import numpy as np


class MeshHull:
    """SAT against a convex superset of a mesh, including all edge axes."""
    def __init__(self, vertices):
        from scipy.spatial import ConvexHull
        hull = ConvexHull(vertices)
        self.vertices = vertices[hull.vertices]
        self.normals = self._unique_axes(hull.equations[:, :3])
        faces = hull.simplices
        edges = np.concatenate([vertices[faces[:, i]] - vertices[faces[:, j]]
                                for i, j in ((0, 1), (1, 2), (2, 0))])
        self.edges = self._unique_axes(edges)
        ranges = [self._project(axes) for axes in np.array_split(
            self.normals, max(1, (len(self.normals) + 63) // 64))]
        self.low = np.concatenate([r[0] for r in ranges])
        self.high = np.concatenate([r[1] for r in ranges])

    def _project(self, axes):
        projection = np.einsum('vi,ai->va', self.vertices, axes, optimize=False)
        return projection.min(0), projection.max(0)

    @staticmethod
    def _unique_axes(axes):
        norm = np.linalg.norm(axes, axis=1)
        axes = axes[norm > 1e-10] / norm[norm > 1e-10, None]
        # Sign is immaterial to a separating axis. Deduplicate only identical
        # rounded keys, retaining the original full-precision direction.
        leading = np.argmax(np.abs(axes), axis=1)
        axes *= np.where(axes[np.arange(len(axes)), leading] < 0, -1, 1)[:, None]
        _, ids = np.unique(np.round(axes, 10), axis=0, return_index=True)
        return axes[ids]

    def gap(self, body_position, rotation, table_centre, table_half):
        centre = rotation.T @ (table_centre - body_position)
        table_mid = np.einsum('ai,i->a', self.normals, centre)
        radius = np.abs(np.einsum('ai,ji->aj', self.normals, rotation)) @ table_half
        face_gap = float(np.max(np.maximum(self.low - table_mid - radius,
                                           table_mid - radius - self.high)))
        if face_gap > 0:
            return face_gap
        # Face normals of the table and edge cross products in body space.
        dynamic = np.concatenate((rotation, np.cross(
            self.edges[:, None, :], rotation[None, :, :]).reshape(-1, 3)))
        norm = np.linalg.norm(dynamic, axis=1)
        dynamic = dynamic[norm > 1e-10] / norm[norm > 1e-10, None]
        # Never allocate vertices x all edge axes: high-resolution gripper
        # meshes otherwise consume gigabytes for each physics sample.
        for axes in np.array_split(dynamic, max(1, (len(dynamic) + 63) // 64)):
            low, high = self._project(axes)
            table_mid = axes @ centre
            radius = np.abs(axes @ rotation.T) @ table_half
            face_gap = max(face_gap, float(np.max(np.maximum(
                low - table_mid - radius, table_mid - radius - high))))
            if face_gap > 0:
                return face_gap
        return face_gap


def box_table_gaps(centres, rotations, half_sizes, table_centre, table_half):
    """Signed separating gap for each oriented box against one axis box."""
    n = len(centres)
    identity = np.broadcast_to(np.eye(3), (n, 3, 3))
    local_axes = rotations.transpose(0, 2, 1)
    cross = np.cross(local_axes[:, :, None, :], identity[:, None, :, :]).reshape(n, 9, 3)
    axes = np.concatenate((identity, local_axes, cross), axis=1)
    norms = np.linalg.norm(axes, axis=2)
    axes = axes / np.maximum(norms[:, :, None], 1e-12)
    distance = np.abs(np.einsum('nij,nj->ni', axes, centres - table_centre))
    box_radius = np.einsum('nij,nj->ni',
        np.abs(np.einsum('nki,nij->nkj', axes, rotations)), half_sizes)
    table_radius = np.abs(axes) @ np.asarray(table_half)
    gaps = distance - box_radius - table_radius
    gaps[norms < 1e-8] = -np.inf
    return np.max(gaps, axis=1)


class ArmTableClearance:
    def __init__(self, stage, arm, table_centre, table_size):
        from pxr import Usd, UsdGeom, Gf
        self.arm = arm
        self.table_centre = np.asarray(table_centre)
        self.table_half = np.asarray(table_size) / 2
        self.names, self.body_ids, centres, sizes = [], [], [], []
        self.vertices, self.hulls = [], {}
        cache = UsdGeom.XformCache()
        for body_id, path in enumerate(arm.root_physx_view.link_paths[0]):
            body = stage.GetPrimAtPath(path)
            inverse = cache.GetLocalToWorldTransform(body).GetInverse()
            for prim in Usd.PrimRange(body, Usd.TraverseInstanceProxies()):
                if not prim.IsA(UsdGeom.Mesh):
                    continue
                points = UsdGeom.Mesh(prim).GetPointsAttr().Get()
                if not points:
                    continue
                transform = cache.GetLocalToWorldTransform(prim) * inverse
                vertices = np.asarray([tuple(transform.Transform(Gf.Vec3d(*p))) for p in points])
                low, high = vertices.min(0), vertices.max(0)
                self.names.append(str(prim.GetPath()))
                self.body_ids.append(body_id)
                centres.append((low + high) / 2)
                sizes.append((high - low) / 2)
                self.vertices.append(vertices)
        if not self.names:
            raise RuntimeError('No robot meshes available for clearance audit')
        self.centres, self.half_sizes = np.asarray(centres), np.asarray(sizes)
        self.samples = 0
        self.minimum = float('inf')
        self.worst = None
        self.overlap_steps = 0
        self.timeline = []
        self.refined_samples = 0

    def sample(self, simulation_time):
        p = self.arm.data.body_pos_w[0].detach().cpu().numpy()[self.body_ids]
        q = self.arm.data.body_quat_w[0].detach().cpu().numpy()[self.body_ids]
        q = q / np.linalg.norm(q, axis=1)[:, None]
        w, x, y, z = q.T
        rotations = np.stack((1-2*(y*y+z*z), 2*(x*y-w*z), 2*(x*z+w*y),
                              2*(x*y+w*z), 1-2*(x*x+z*z), 2*(y*z-w*x),
                              2*(x*z-w*y), 2*(y*z+w*x), 1-2*(x*x+y*y)), axis=1).reshape(-1,3,3)
        centres = p + np.einsum('nij,nj->ni', rotations, self.centres)
        gaps = box_table_gaps(centres, rotations, self.half_sizes, self.table_centre, self.table_half)
        for candidate in np.flatnonzero(gaps < 0):
            i = int(candidate)
            if i not in self.hulls:
                from scipy.spatial import QhullError
                try:
                    self.hulls[i] = MeshHull(self.vertices[i])
                except QhullError:
                    self.hulls[i] = None  # Degenerate mesh: retain conservative box.
            if self.hulls[i] is not None:
                gaps[i] = max(gaps[i], self.hulls[i].gap(
                    p[i], rotations[i], self.table_centre, self.table_half))
                self.refined_samples += 1
        if not np.all(np.isfinite(gaps)):
            raise RuntimeError('Non-finite robot/table clearance; trajectory cannot be accepted')
        i = int(np.argmin(gaps))
        gap = float(gaps[i])
        sample = {'simulation_time_s': simulation_time, 'gap_m': gap, 'mesh': self.names[i]}
        self.samples += 1
        if gap < self.minimum:
            self.minimum, self.worst = gap, sample
        if gap < -1e-4:
            self.overlap_steps += 1
        if self.samples % 10 == 0:
            self.timeline.append(sample)

    def report(self):
        return {'passed': self.samples > 0 and self.overlap_steps == 0,
                'method': 'mesh-enclosing oriented boxes plus convex hull SAT refinement, every physics step',
                'convex_hull_refinements': self.refined_samples,
                'mesh_count': len(self.names), 'physics_samples': self.samples,
                'minimum_separating_gap_m': self.minimum, 'overlap_steps': self.overlap_steps,
                'numerical_tolerance_m': 1e-4, 'worst_sample': self.worst,
                'timeline': self.timeline}
