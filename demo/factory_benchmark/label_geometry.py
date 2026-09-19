"""Fit a metric paper square entirely inside an actual box surface triangle."""

import numpy as np


SURFACE_OFFSET_M = 0.001
EDGE_MARGIN_M = 0.001


def surface_triangles(parent):
    from pxr import Gf, Usd, UsdGeom

    cache = UsdGeom.XformCache()
    parent_transform = cache.GetLocalToWorldTransform(parent)
    basis = np.array(parent_transform)[:3, :3]
    if not np.allclose(basis @ basis.T, np.eye(3), atol=1e-7) or np.linalg.det(basis) < 0:
        raise ValueError("metric inventory labels require a rigid, unscaled parent transform")
    triangles = []
    for prim in Usd.PrimRange(parent):
        if not prim.IsA(UsdGeom.Mesh):
            continue
        mesh = UsdGeom.Mesh(prim)
        if mesh.GetSubdivisionSchemeAttr().Get() != "none":
            raise ValueError("inventory support requires explicit unsubdivided triangles")
        counts = mesh.GetFaceVertexCountsAttr().Get()
        if any(count != 3 for count in counts):
            raise ValueError("inventory support requires a triangulated box mesh")
        transform = cache.GetLocalToWorldTransform(prim) * parent_transform.GetInverse()
        points = np.array([transform.Transform(Gf.Vec3d(*point)) for point in mesh.GetPointsAttr().Get()])
        indices = np.array(mesh.GetFaceVertexIndicesAttr().Get()).reshape(-1, 3)
        holes = set(mesh.GetHoleIndicesAttr().Get() or [])
        for face_index, vertices in enumerate(points[indices]):
            if face_index not in holes:
                triangles.append({"prim_path": str(prim.GetPath()), "face_index": face_index,
                                  "vertices_local_m": vertices.tolist()})
    if not triangles:
        raise ValueError("inventory label has no triangle surface support")
    return triangles


def _clip(polygon, normal, threshold):
    result = []
    for start, end in zip(polygon, polygon[1:] + polygon[:1]):
        start_distance, end_distance = np.dot(normal, start) - threshold, np.dot(normal, end) - threshold
        if start_distance >= 0:
            result.append(start)
        if (start_distance >= 0) != (end_distance >= 0):
            result.append(start + start_distance / (start_distance - end_distance) * (end - start))
    return result


def fit_surface_label(triangles, outer_side_m):
    if not np.isfinite(outer_side_m) or outer_side_m <= 0:
        raise ValueError("paper side must be positive and finite")
    front = max(point[0] for triangle in triangles for point in triangle["vertices_local_m"])
    candidates = []
    for triangle in triangles:
        vertices = np.array(triangle["vertices_local_m"])
        normal = np.cross(vertices[1] - vertices[0], vertices[2] - vertices[0])
        double_area = np.linalg.norm(normal)
        if double_area < 1e-10:
            continue
        normal /= double_area
        if normal[0] < 0:
            normal *= -1
        if normal[0] < 0.98 or np.min(vertices[:, 0]) < front - 0.012:
            continue
        horizontal = np.array([0., 1., 0.]) - normal[1] * normal
        horizontal /= np.linalg.norm(horizontal)
        vertical = np.cross(normal, horizontal)
        projected = np.column_stack(((vertices - vertices[0]) @ horizontal,
                                     (vertices - vertices[0]) @ vertical))
        polygon = list(projected)
        for start, end in zip(projected, np.roll(projected, -1, axis=0)):
            edge = end - start
            inward = np.array([-edge[1], edge[0]])
            inward /= np.linalg.norm(inward)
            if np.dot(inward, projected.mean(axis=0) - start) < 0:
                inward *= -1
            threshold = np.dot(inward, start) + outer_side_m / 2 * np.abs(inward).sum() + EDGE_MARGIN_M
            polygon = _clip(polygon, inward, threshold)
            if not polygon:
                break
        if not polygon:
            continue
        location = np.mean(polygon, axis=0)
        center = vertices[0] + location[0] * horizontal + location[1] * vertical + SURFACE_OFFSET_M * normal
        candidates.append((double_area, {**triangle, "normal": normal.tolist(),
                           "horizontal": horizontal.tolist(), "vertical": vertical.tolist(),
                           "local_center_m": center.tolist(), "outer_side_m": outer_side_m,
                           "surface_offset_m": SURFACE_OFFSET_M, "edge_margin_m": EDGE_MARGIN_M,
                           "method": "whole_square_inside_single_source_triangle"}))
    if not candidates:
        raise ValueError("inventory paper has no complete support on a front surface triangle")
    return max(candidates, key=lambda candidate: candidate[0])[1]


def validate_label_support(base_stage, overlay_stage, variant):
    from pxr import Gf, UsdGeom

    parent = base_stage.GetPrimAtPath(variant["parent_prim_path"])
    support = fit_surface_label(surface_triangles(parent), variant["marker_side_m"] * 8 / 6)
    if support != variant["surface_support"]:
        raise ValueError("inventory support metadata differs from original mesh geometry")
    if variant["state"] == "absent":
        if overlay_stage.GetPrimAtPath(variant["label_prim_path"]):
            raise ValueError("absent label contains geometry")
        return {"validated": True, "label_present": False, "support": support}
    vertices = np.array(support["vertices_local_m"])
    normal = np.array(support["normal"])
    horizontal, vertical = np.array(support["horizontal"]), np.array(support["vertical"])
    center = np.array(support["local_center_m"])
    inverse = UsdGeom.XformCache().GetLocalToWorldTransform(parent).GetInverse()
    extent_points = []
    for name in ("Ink", "Paper"):
        mesh = UsdGeom.Mesh(overlay_stage.GetPrimAtPath(variant["label_prim_path"] + "/" + name))
        if not mesh:
            raise ValueError("inventory label is missing its paper or ink mesh")
        transform = UsdGeom.XformCache().GetLocalToWorldTransform(mesh.GetPrim()) * inverse
        points = np.array([transform.Transform(Gf.Vec3d(*point)) for point in mesh.GetPointsAttr().Get()])
        distances = (points - vertices[0]) @ normal
        if not np.allclose(distances, SURFACE_OFFSET_M, atol=1e-6):
            raise ValueError("inventory label is detached from the supporting plane")
        projected = np.column_stack(((points - center) @ horizontal, (points - center) @ vertical))
        expected_side = variant["marker_side_m"] if name == "Ink" else support["outer_side_m"]
        if (not np.allclose(projected.min(axis=0), -expected_side / 2, atol=1e-6)
                or not np.allclose(projected.max(axis=0), expected_side / 2, atol=1e-6)):
            raise ValueError("inventory metric dimensions differ from rendered geometry")
        for start, end in zip(vertices, np.roll(vertices, -1, axis=0)):
            inward = np.cross(normal, end - start)
            inward /= np.linalg.norm(inward)
            if np.dot(inward, vertices.mean(axis=0) - start) < 0:
                inward *= -1
            if np.min((points - start) @ inward) < EDGE_MARGIN_M - 1e-6:
                raise ValueError("inventory label extends beyond its supporting triangle")
        extent_points.extend(points.tolist())
    return {"validated": True, "label_present": True, "support": support,
            "checked_vertices": len(extent_points), "scope": "all paper and ink faces, convex triangle support"}
