"""Episode-owned resident clearance measurement against the live PhysX scene.

The candidate route is an author input, not evidence. Every segment is swept
in both directions with the public resident envelope. No robot or furniture
is excluded. Verified static cuboids below the sole plane are support contacts.
Floor sampling and a continuous static-cuboid cover are recorded separately.
"""

from __future__ import annotations

from copy import deepcopy
import math

from demo.benchmark.goal_live_occupancy import _scene_check
from demo.benchmark.provenance import canonical_sha256


class _AllObstaclesQuery:
    """Resident sweeps have no mover/self exclusion, including for instances.

    PhysX can return an undefined protoIndex for ordinary rigid shapes. The
    shared robot checker needs reliable indices for self exclusion; residents
    exclude nothing. Preserve raw indices separately and treat every hit as an
    obstacle regardless of its instance index.
    """

    def __init__(self, query, support_colliders):
        self.query = query
        self.support_colliders = support_colliders
        self.raw_hits = []

    def _call(self, method, args, callback_index):
        values = list(args)
        callback = values[callback_index]
        raw_count = 0
        kept_count = 0

        def report(hit):
            nonlocal raw_count, kept_count
            raw_count += 1
            row = (dict(hit) if isinstance(hit, dict) else {
                key: getattr(hit, key) for key in ("collision", "rigid_body", "distance", "protoIndex")
                if hasattr(hit, key)})
            self.raw_hits.append({"collision": str(row.get("collision")),
                                  "protoIndex": row.get("protoIndex"),
                                  "verified_floor_contact": row.get("collision") in self.support_colliders})
            if row.get("collision") in self.support_colliders:
                return True
            kept_count += 1
            row.pop("protoIndex", None)
            return callback(row)

        values[callback_index] = report
        returned = getattr(self.query, method)(*values)
        if method == "overlap_box":
            if type(returned) is not int or returned != raw_count:
                raise ValueError("native overlap completion mismatch")
            return kept_count
        if type(returned) is not bool or returned != bool(raw_count):
            raise ValueError("native sweep completion mismatch")
        return bool(kept_count)

    def overlap_box(self, *args):
        return self._call("overlap_box", args, 3)

    def sweep_box_all(self, *args):
        return self._call("sweep_box_all", args, 5)


def _point(value):
    if (not isinstance(value, (tuple, list)) or len(value) != 3
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in value)):
        raise ValueError("finite metric XYZ route point required")
    return [float(v) for v in value]


def measure_resident_path(query, *, points, context, width_m=.9, height_m=2.,
                          support_spacing_m=.1, floor_tolerance_m=.02, support_colliders=None):
    """Read-only, bounded query. Caller freezes physics through this call.

    Points are sole-plane centres in metric USD_world. A 1 mm vertical boundary
    tolerance avoids counting floor/ceiling contact as penetration; it is
    explicit in the receipt, and does not shift the envelope above its ceiling.
    Scene-query collision participation is a caller-owned prerequisite.
    """
    result = {"schema": "eai.benchmark.house_live_resident_path.v1",
              "status": "unknown", "segments": [], "support_samples": [],
              "blockers": [], "scoreable": False,
              "physical_task_execution_validated": False,
              "business_outcomes_validated": False,
              "continuous_support_validated": False}
    try:
        if (not isinstance(context, dict) or not context.get("episode_id")
                or not context.get("stage_identifier")
                or context.get("frame_id") != "USD_world"
                or context.get("meters_per_unit") != 1.
                or type(context.get("physics_step")) is not int
                or context["physics_step"] < 0
                or context.get("readback_frozen") is not True
                or context.get("kinematics_current") is not True
                or context.get("scene_queries_enabled") is not True):
            raise ValueError("frozen current metric episode context required")
        if not isinstance(points, (list, tuple)) or not 2 <= len(points) <= 64:
            raise ValueError("bounded continuous route required")
        route = [_point(p) for p in points]
        for value in (width_m, height_m, support_spacing_m, floor_tolerance_m):
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError("finite resident envelope/tolerances required")
        if (width_m < .9 or height_m < 2. or not .02 <= support_spacing_m <= .1
                or not .001 <= floor_tolerance_m <= .02):
            raise ValueError("cannot weaken public resident clearance or support sampling")
        lengths = [math.dist(a, b) for a, b in zip(route, route[1:])]
        if any(length <= 1e-6 for length in lengths) or sum(lengths) > 50:
            raise ValueError("degenerate or over-budget resident route")
        if any(abs(p[2]-route[0][2]) > 1e-6 for p in route):
            raise ValueError("level route only; stairs need a separate support contract")
        support_colliders = deepcopy(support_colliders or {})
        for path, bounds in support_colliders.items():
            if not isinstance(path, str) or not path.startswith("/") or len(bounds) != 2:
                raise ValueError("invalid trusted floor geometry")
            low, high = map(_point, bounds)
            if any(a >= b for a, b in zip(low, high)) or high[2] > route[0][2]+1e-6:
                raise ValueError("floor exclusion intrudes into resident volume")
        sole_gap = .001
        result.update(context=deepcopy(context), points=route,
                      width_m=width_m, height_m=height_m, sole_gap_m=sole_gap,
                      vertical_boundary_contact_tolerance_m=sole_gap,
                      support_spacing_m=support_spacing_m,
                      floor_tolerance_m=floor_tolerance_m,
                      verified_static_support_colliders=support_colliders,
                      excluded_collision_paths=[])
        half = width_m / 2
        obstacle_query = _AllObstaclesQuery(query, support_colliders)
        for index, (a, b, length) in enumerate(zip(route, route[1:], lengths)):
            segment = {"index": index, "start": a, "end": b, "checks": []}
            for start, end in ((a, b), (b, a)):
                bounds = [[start[0]-half, start[1]-half, start[2]+sole_gap],
                          [start[0]+half, start[1]+half, start[2]+height_m-sole_gap]]
                segment["checks"].extend(_scene_check(
                    obstacle_query, bounds, [v-u for u, v in zip(start, end)], {}))
            result["segments"].append(segment)
            if any(c["status"] == "unknown" for c in segment["checks"]):
                result["blockers"].append(f"segment_{index}:query_incomplete")
            elif any(c["status"] == "blocked" for c in segment["checks"]):
                result["blockers"].append(f"segment_{index}:resident_volume_obstructed")
            count = math.ceil(length / support_spacing_m)
            for i in range(count+1):
                center = [u+(v-u)*i/count for u, v in zip(a, b)]
                # Corners plus centre detect common slab-edge and threshold gaps.
                for dx, dy in ((0., 0.), (-half, -half), (-half, half),
                               (half, -half), (half, half)):
                    origin = [center[0]+dx, center[1]+dy, center[2]+.05]
                    hit = query.raycast_closest(tuple(origin), (0., 0., -1.), .1)
                    row = {"origin_m": origin, "supported": False}
                    if isinstance(hit, dict) and hit.get("hit") is True:
                        # Kit returns carb.Float3 rather than a Python list.
                        position = _point(list(hit.get("position")))
                        normal = _point(list(hit.get("normal")))
                        collision = hit.get("collision")
                        if not isinstance(collision, str) or not collision.startswith("/"):
                            raise ValueError("support hit lacks collision identity")
                        row.update(collision=collision, position_m=position, normal=normal,
                                   raw_proto_index=hit.get("protoIndex"),
                                   supported=(abs(position[2]-center[2]) <= floor_tolerance_m
                                              and normal[2] >= .99))
                    result["support_samples"].append(row)
        if any(not s["supported"] for s in result["support_samples"]):
            result["blockers"].append("support_samples_missing_or_uneven")
        result["raw_obstacle_hit_metadata"] = obstacle_query.raw_hits
        result["continuous_volume_clear"] = not any(
            c["status"] != "no_external_hit" for s in result["segments"] for c in s["checks"])
        result["status"] = "blocked" if result["blockers"] else "clear_volume_support_sampled"
        # CPU scene queries read stale actor poses with Direct GPU enabled.
        # Retain diagnostics, but do not certify a current dynamic scene.
        if context.get("dynamic_query_pose_current") is not True:
            result["blockers"].append("dynamic_scene_query_pose_not_synchronized")
            result["continuous_volume_clear"] = False
            result["status"] = "unknown"
    except Exception as exc:
        result["blockers"].append(f"measurement_failed:{type(exc).__name__}:{exc}")
        result["status"] = "unknown"
    result["sha256"] = canonical_sha256(result)
    return result


def collect_goal_resident_path(stage, session, deployment, *, episode_id):
    """Native goal-scene hook, with all dynamic/idle entities left in queries."""
    import omni.physx
    from pxr import UsdGeom

    if deployment["scene_key"] != "modern_house_nvd":
        raise ValueError("House deployment required")
    session.base_env.sim.physics_sim_view.update_articulations_kinematic()
    context = {"episode_id": episode_id, "stage_identifier": stage.GetRootLayer().identifier,
               "frame_id": "USD_world", "meters_per_unit": float(UsdGeom.GetStageMetersPerUnit(stage)),
               "physics_step": int(session.base_env.sim.current_time_step_index),
               "time_s": float(session.base_env.sim.current_time),
               "readback_frozen": True, "kinematics_current": True,
               "scene_queries_enabled": session.env_cfg.sim.enable_scene_query_support,
               "dynamic_query_pose_current": not omni.physx.get_physx_interface().is_readback_suppressed(),
               "deployment_sha256": deployment["sha256"]}
    # Explicit author candidate, not silently a planner interpolation or a pass.
    # The straight diagonal crosses kitchen wall proxies. This explicit
    # west-circulation candidate goes around them; live queries still decide.
    points = [[4., 3.5, .25], [3.5, 3.5, .25], [3.5, 9.5, .25],
              [3.5, 12.3, .25], [4.5, 12.3, .25]]
    floors = static_floor_colliders(stage, points, width_m=.9)
    result = measure_resident_path(omni.physx.get_physx_scene_query_interface(),
                                  points=points, context=context, support_colliders=floors)
    result["support_cover"] = static_support_cover(stage, result)
    result["continuous_support_validated"] = result["support_cover"]["status"] == "covered"
    if result["status"] == "clear_volume_support_sampled" and result["continuous_support_validated"]:
        result["status"] = "current_resident_corridor_clear"
    result["sha256"] = canonical_sha256({k: v for k, v in result.items() if k != "sha256"})
    return result


def static_floor_colliders(stage, points, *, width_m):
    """Known enabled static cuboids below the sole plane; no mesh AABB fallback."""
    from pxr import UsdGeom, UsdPhysics
    cache = UsdGeom.XformCache()
    half = width_m / 2
    domain = [[min(p[i] for p in points)-half for i in range(2)],
              [max(p[i] for p in points)+half for i in range(2)]]
    result = {}
    for prim in stage.Traverse():
        if (not prim.IsA(UsdGeom.Cube) or not prim.HasAPI(UsdPhysics.CollisionAPI)
                or UsdPhysics.CollisionAPI(prim).GetCollisionEnabledAttr().Get() is not True):
            continue
        try:
            low, high = _static_cube_bounds(prim, cache)
        except ValueError:
            continue
        if abs(high[2]-points[0][2]) > 1e-6:
            continue
        if any(high[i] < domain[0][i] or low[i] > domain[1][i] for i in range(2)):
            continue
        result[str(prim.GetPath())] = [low, high]
    return result


def _static_cube_bounds(prim, cache):
    from pxr import Gf, UsdGeom, UsdPhysics
    parent = prim
    while parent and not parent.IsPseudoRoot():
        if parent.HasAPI(UsdPhysics.RigidBodyAPI):
            raise ValueError("dynamic support is not a static floor")
        parent = parent.GetParent()
    transform = cache.GetLocalToWorldTransform(prim)
    axes = [list(transform.TransformDir(Gf.Vec3d(*(int(i == j) for j in range(3)))))
            for i in range(3)]
    if (any(sum(abs(v) > 1e-8 for v in axis) != 1 for axis in axes)
            or len({max(range(3), key=lambda i: abs(axis[i])) for axis in axes}) != 3):
        raise ValueError("rotated/sheared support cuboid unsupported")
    size = float(UsdGeom.Cube(prim).GetSizeAttr().Get()) / 2
    corners = [transform.Transform(Gf.Vec3d(x, y, z))
               for x in (-size, size) for y in (-size, size) for z in (-size, size)]
    return [[min(p[i] for p in corners) for i in range(3)],
            [max(p[i] for p in corners) for i in range(3)]]


def static_support_cover(stage, measurement):
    """Exact planar cover by enabled static USD cubes plus live support samples.

    Only axis-aligned cuboids with the measured sole height qualify. Mesh
    bounding boxes and dynamic bodies are never substituted for floor surfaces.
    The union must cover the entire swept square, including corners, not only
    the ray samples. Unsupported geometry remains unknown.
    """
    result = {"status": "unknown", "colliders": [], "boundary_tolerance_m": 1e-6}
    try:
        from pxr import Gf, UsdGeom, UsdPhysics
        from shapely.geometry import MultiPoint, box
        from shapely.ops import unary_union

        samples = measurement.get("support_samples", [])
        if not samples or not all(s["supported"] for s in samples):
            raise ValueError("not all support rays reached the sole plane")
        if measurement["context"].get("dynamic_query_pose_current") is not True:
            raise ValueError("support ray hits may use stale dynamic actor poses")
        paths = sorted({s["collision"] for s in samples}
                       | set(measurement.get("verified_static_support_colliders", {})))
        if len(paths) > 512:
            raise ValueError("support collider inventory exceeds bound")
        cache = UsdGeom.XformCache()
        rectangles = []
        floor_z = measurement["points"][0][2]
        for path in paths:
            prim = stage.GetPrimAtPath(path)
            if (not prim or not prim.IsActive() or not prim.IsLoaded()
                    or not prim.IsA(UsdGeom.Cube)
                    or UsdPhysics.CollisionAPI(prim).GetCollisionEnabledAttr().Get() is not True):
                raise ValueError(f"unsupported support collider:{path}")
            parent = prim
            while parent and not parent.IsPseudoRoot():
                if parent.HasAPI(UsdPhysics.RigidBodyAPI):
                    raise ValueError(f"dynamic support is not a static floor:{path}")
                parent = parent.GetParent()
            transform = cache.GetLocalToWorldTransform(prim)
            axes = [list(transform.TransformDir(Gf.Vec3d(*(int(i == j) for j in range(3)))))
                    for i in range(3)]
            if (any(sum(abs(v) > 1e-8 for v in axis) != 1 for axis in axes)
                    or len({max(range(3), key=lambda i: abs(axis[i])) for axis in axes}) != 3):
                raise ValueError(f"rotated/sheared support cuboid unsupported:{path}")
            size = float(UsdGeom.Cube(prim).GetSizeAttr().Get()) / 2
            corners = [transform.Transform(Gf.Vec3d(x, y, z))
                       for x in (-size, size) for y in (-size, size) for z in (-size, size)]
            low = [min(p[i] for p in corners) for i in range(3)]
            high = [max(p[i] for p in corners) for i in range(3)]
            if abs(high[2]-floor_z) > 1e-6:
                raise ValueError(f"support top does not match sole plane:{path}")
            rectangles.append(box(low[0], low[1], high[0], high[1]))
            result["colliders"].append({"path": path, "bounds_m": [low, high]})
        half = measurement["width_m"] / 2
        corridor = unary_union([MultiPoint([
            (p[0]+dx, p[1]+dy) for p in (a, b)
            for dx in (-half, half) for dy in (-half, half)]).convex_hull
            for a, b in zip(measurement["points"], measurement["points"][1:])])
        missing = corridor.difference(unary_union(rectangles).buffer(1e-6))
        result.update(status="covered" if missing.is_empty else "gap",
                      uncovered_area_m2=float(missing.area),
                      basis="continuous_enabled_static_cuboid_union_plus_live_support_samples")
    except Exception as exc:
        result["reason"] = f"{type(exc).__name__}:{exc}"
    return result


def withdrawal_commands(session, previous_iris_goal):
    """Bounded author control diagnostic, not a participant navigation solver.

    Uses native controllers, never teleports roots or removes colliders. The
    final resident query (including every robot) independently judges clearance.
    Targets are diagnostic parking poses, not certified task-region bindings.
    """
    import torch

    robot = session.base_env.scene.articulations["m20_1"]
    state = robot.data.root_state_w[0].detach().cpu().tolist()
    x, y = state[:2]
    w, qx, qy, qz = state[3:7]
    yaw = math.atan2(2*(w*qz+qx*qy), 1-2*(qy*qy+qz*qz))
    dx, dy = 2.6-x, 3.5-y
    distance = math.hypot(dx, dy)
    speed = min(.12, distance) if distance > .08 else 0.
    vx, vy = (speed*dx/max(distance, 1e-9), speed*dy/max(distance, 1e-9))
    command = [math.cos(yaw)*vx+math.sin(yaw)*vy,
               -math.sin(yaw)*vx+math.cos(yaw)*vy, max(-.2, min(.2, -yaw))]
    goal = previous_iris_goal.clone()
    target = torch.tensor([[5., 13.5, 1.6]], device=session.device)
    delta = target-goal
    norm = float(torch.linalg.vector_norm(delta))
    if norm > 0:
        goal += delta*min(1., .2*float(session.base_env.step_dt)/norm)
    return command, goal
