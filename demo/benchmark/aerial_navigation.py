"""Offline, continuous 3-D route admission against a declared voxel product.

This is a geometric gate, not a flight planner or proof of RL tracking. The
NVD product is bool[z, y, x], with y increasing in world coordinates, XY cell
size ``res``, Z cell size ``vox``, and Z origin zero. Out-of-volume space and
unavailable products are unknown, never free. No Isaac or USD import is needed.
"""

import hashlib
import io
import json
import math
from pathlib import Path
from zipfile import BadZipFile

import numpy as np


# Decimal map origins/resolutions do not have exact binary representations.
# Expand collision tests by a nanometre so rounded face contact cannot pass.
CONTACT_TOLERANCE_M = 1e-9


def _vector3(value, name, *, positive=False):
    result = np.asarray(value, dtype=float)
    if (result.shape != (3,) or not np.isfinite(result).all()
            or (positive and not (result > 0).all())):
        raise ValueError(f"{name} must contain three finite" + (" positive values" if positive else " values"))
    return result


def _segment_hits_boxes(start, end, lower, upper):
    """Closed segment/AABB slab test, including face/corner contact."""
    enter = np.zeros(len(lower))
    leave = np.ones(len(lower))
    delta = end - start
    for axis in range(3):
        if delta[axis] == 0:
            outside = (start[axis] < lower[:, axis]) | (start[axis] > upper[:, axis])
            leave[outside] = -1
        else:
            a = (lower[:, axis] - start[axis]) / delta[axis]
            b = (upper[:, axis] - start[axis]) / delta[axis]
            enter = np.maximum(enter, np.minimum(a, b))
            leave = np.minimum(leave, np.maximum(a, b))
    return enter <= leave


class AerialVoxelMap:
    """Validated, read-only volume; callers must supply a body envelope.

    ``proxy_xy_block_size=5`` additionally accounts for the 5x5 XY coarse
    collision boxes produced by ``rebuild_nvd_products`` from layer 2 upward.
    It conservatively blocks the entire coarse cell containing an occupied
    voxel. Use 1 only for products whose collision geometry is voxel-exact.
    """

    def __init__(self, voxels, *, x0, y0, res, vox, source=None,
                 sha256=None, proxy_xy_block_size=1):
        if (not isinstance(voxels, np.ndarray) or voxels.dtype != np.bool_
                or voxels.ndim != 3 or min(voxels.shape) < 1):
            raise ValueError("voxels must be a nonempty bool[z,y,x] array")
        metadata = np.asarray([x0, y0, res, vox], dtype=float)
        if metadata.shape != (4,) or not np.isfinite(metadata).all() or res <= 0 or vox <= 0:
            raise ValueError("voxel origin and resolutions must be finite, with positive resolutions")
        if (isinstance(proxy_xy_block_size, bool) or not isinstance(proxy_xy_block_size, int)
                or proxy_xy_block_size < 1):
            raise ValueError("proxy_xy_block_size must be a positive integer")
        self.occupied = voxels.copy()
        block = proxy_xy_block_size
        if block > 1:
            if not math.isclose(float(vox), 0.25, abs_tol=1e-12):
                raise ValueError("NVD coarse collision proxy requires 0.25 m Z layers")
            layers, height, width = self.occupied.shape
            for k in range(2, layers):
                padded = np.pad(self.occupied[k], ((0, (-height) % block), (0, (-width) % block)))
                coarse = padded.reshape(padded.shape[0] // block, block,
                                        padded.shape[1] // block, block).any(axis=(1, 3))
                self.occupied[k] = coarse.repeat(block, axis=0).repeat(block, axis=1)[:height, :width]
        self.occupied.flags.writeable = False
        self.origin = np.asarray([x0, y0, 0.0], dtype=float)
        self.cell_size = np.asarray([res, res, vox], dtype=float)
        self.shape_xyz = np.asarray(self.occupied.shape[::-1])
        self.upper = self.origin + self.cell_size * self.shape_xyz
        self.source = source
        self.sha256 = sha256
        self.proxy_xy_block_size = block
        self.input_file_hashes = {source: sha256} if source and sha256 else {}
        self.ground_proxy_height_m = None

    def include_ground_collision_proxy(self, map_path, *, height_m):
        """Include NVD's 0–0.8 m walls from its map_server occupancy image.

        PNG rows increase downward; voxel Y indices increase in world Y, so
        flip only the image rows. A partial Z layer is filled conservatively.
        Unknown occupancy and nonopaque pixels block; invalid negate values
        are rejected rather than being interpreted by Python truthiness.
        """
        import yaml
        from PIL import Image

        if not math.isfinite(height_m) or height_m <= 0:
            raise ValueError("ground proxy height must be finite and positive")
        map_path = Path(map_path).resolve()
        raw = map_path.read_bytes()
        try:
            config = yaml.safe_load(raw)
        except yaml.YAMLError as error:
            raise ValueError(f"invalid ground proxy map YAML: {error}") from error
        if not isinstance(config, dict):
            raise ValueError("ground proxy map must be a YAML mapping")
        negate = config.get("negate", 0)
        if type(negate) is not int or negate not in (0, 1):
            raise ValueError("ground proxy map negate must be integer 0 or 1")
        origin = np.asarray(config["origin"], dtype=float)
        if (origin.shape != (3,) or not np.allclose(origin, self.origin, atol=1e-10, rtol=0)
                or not math.isclose(float(config["resolution"]), self.cell_size[0], abs_tol=1e-10)):
            raise ValueError("ground proxy map and voxel frames must match")
        image_path = (map_path.parent / config["image"]).resolve()
        image_raw = image_path.read_bytes()
        with Image.open(io.BytesIO(image_raw)) as image:
            pixels = np.asarray(image.convert("L"), dtype=float) / 255
            nonopaque = np.asarray(image.convert("RGBA"))[:, :, 3] != 255
        if pixels.shape != self.occupied.shape[1:]:
            raise ValueError("ground proxy map and voxel dimensions must match")
        threshold = float(config["free_thresh"])
        if not math.isfinite(threshold) or not 0 < threshold < 1:
            raise ValueError("ground proxy map free threshold must be within (0,1)")
        occupancy = pixels if negate else 1 - pixels
        blocked = np.flipud((occupancy >= threshold) | nonopaque)
        self.occupied = self.occupied.copy()
        self.occupied[:math.ceil(height_m / self.cell_size[2])] |= blocked
        self.occupied.flags.writeable = False
        self.ground_proxy_height_m = float(height_m)
        self.input_file_hashes.update({str(map_path): hashlib.sha256(raw).hexdigest(),
                                      str(image_path): hashlib.sha256(image_raw).hexdigest()})
        return self

    @classmethod
    def load(cls, path, *, proxy_xy_block_size=1):
        path = Path(path)
        raw = path.read_bytes()
        with np.load(io.BytesIO(raw), allow_pickle=False) as data:
            for name in ("x0", "y0", "res", "vox"):
                if data[name].shape != () or data[name].dtype.kind not in "fiu":
                    raise ValueError(f"voxel metadata {name} must be a numeric scalar")
            return cls(data["voxels"], **{name: float(data[name]) for name in ("x0", "y0", "res", "vox")},
                       source=str(path.resolve()), sha256=hashlib.sha256(raw).hexdigest(),
                       proxy_xy_block_size=proxy_xy_block_size)

    def check_route(self, points, *, half_extents_m, margin_m=0.1, obstacle_aabbs=()):
        """Check every segment's swept axis-aligned body envelope continuously.

        Additional world AABBs are ``(xmin,ymin,zmin,xmax,ymax,zmax)``. Supply
        current task objects/robots separately; the scene product does not
        contain them. No point sampling can skip a thin intervening obstacle.
        A 1e-9 m collision tolerance conservatively includes rounded contact.
        """
        points = np.asarray(points, dtype=float)
        if points.ndim != 2 or points.shape[1] != 3 or len(points) < 1 or not np.isfinite(points).all():
            raise ValueError("route must contain at least one finite XYZ point")
        half_extents = _vector3(half_extents_m, "half_extents_m", positive=True)
        if isinstance(margin_m, bool) or not math.isfinite(margin_m) or margin_m < 0:
            raise ValueError("margin_m must be finite and nonnegative")
        envelope = half_extents + margin_m
        collision_envelope = envelope + CONTACT_TOLERANCE_M
        boxes = np.asarray(obstacle_aabbs, dtype=float)
        if boxes.size == 0:
            boxes = np.empty((0, 6))
        if (boxes.ndim != 2 or boxes.shape[1] != 6 or not np.isfinite(boxes).all()
                or np.any(boxes[:, 3:] < boxes[:, :3])):
            raise ValueError("obstacle_aabbs must contain finite ordered world boxes")
        report = {"status": "clear", "admitted": True,
                  "model": "continuous_swept_aabb_against_voxel_product",
                  "product_path": self.source, "product_sha256": self.sha256,
                  "proxy_xy_block_size": self.proxy_xy_block_size,
                  "ground_proxy_height_m": self.ground_proxy_height_m,
                  "half_extents_m": half_extents.tolist(), "margin_m": float(margin_m),
                  "contact_tolerance_m": CONTACT_TOLERANCE_M,
                  "checked_segments": 0, "additional_obstacle_count": len(boxes),
                  "scope": "declared_static_product_and_supplied_obstacles_only"}
        segments = zip(points[:-1], points[1:]) if len(points) > 1 else [(points[0], points[0])]
        for index, (start, end) in enumerate(segments):
            report["checked_segments"] += 1
            low = np.minimum(start, end) - envelope
            high = np.maximum(start, end) + envelope
            if np.any(low < self.origin) or np.any(high > self.upper):
                return {**report, "status": "unknown", "admitted": False,
                        "reason": "route_envelope_leaves_voxel_volume", "segment_index": index}
            if len(boxes) and _segment_hits_boxes(start, end, boxes[:, :3] - collision_envelope,
                                                 boxes[:, 3:] + collision_envelope).any():
                return {**report, "status": "blocked", "admitted": False,
                        "reason": "route_intersects_supplied_obstacle", "segment_index": index}
            # Include both adjacent cells at an exact grid boundary: contact blocks.
            cell_low = np.maximum(0, np.ceil((low - CONTACT_TOLERANCE_M - self.origin) / self.cell_size).astype(int) - 1)
            cell_high = np.minimum(self.shape_xyz - 1, np.floor((high + CONTACT_TOLERANCE_M - self.origin) / self.cell_size).astype(int))
            x0, y0, z0 = cell_low
            x1, y1, z1 = cell_high
            occupied = np.argwhere(self.occupied[z0:z1 + 1, y0:y1 + 1, x0:x1 + 1])[:, ::-1] + cell_low
            if len(occupied):
                lower = self.origin + occupied * self.cell_size
                hit = _segment_hits_boxes(start, end, lower - collision_envelope,
                                         lower + self.cell_size + collision_envelope)
                if hit.any():
                    cell = occupied[np.flatnonzero(hit)[0]]
                    return {**report, "status": "blocked", "admitted": False,
                            "reason": "route_intersects_occupied_voxel", "segment_index": index,
                            "occupied_cell_xyz": cell.tolist()}
        return report


class UnavailableAerialMap:
    """Preserve missing/invalid evidence as a fail-closed report."""

    def __init__(self, reason, path=None, detail=None):
        self.reason, self.path, self.detail = reason, path, detail

    def check_route(self, points, *, half_extents_m, margin_m=0.1, obstacle_aabbs=()):
        return {"status": "unknown", "admitted": False, "reason": self.reason,
                "product_path": self.path, "detail": self.detail, "checked_segments": 0}


def load_aerial_voxel_map(path, *, proxy_xy_block_size=1,
                          ground_map_path=None, ground_proxy_height_m=0.8,
                          metadata_path=None, expected_source_sha256=None):
    """Read an explicitly selected local product without downloads or fallback.

    When ``metadata_path`` is supplied, the sidecar is a provenance gate: it
    must bind the exact product bytes and, when requested, the exact source
    USD bytes.  A product with an absent or stale sidecar is unknown rather
    than silently accepted as a valid map.
    """
    if path is None:
        return UnavailableAerialMap("voxel_product_not_declared")
    try:
        path = Path(path)
        product_raw = path.read_bytes()
        metadata = None
        if metadata_path is not None:
            metadata_path = Path(metadata_path)
            metadata = json.loads(metadata_path.read_text())
            if (not isinstance(metadata, dict)
                    or metadata.get("schema") != "eai.benchmark.aerial_voxel_product"
                    or metadata.get("version") != 1):
                raise ValueError("voxel product metadata schema is unsupported")
            if metadata.get("product_sha256") != hashlib.sha256(product_raw).hexdigest():
                raise ValueError("voxel product metadata does not bind product bytes")
            source = metadata.get("source")
            if not isinstance(source, dict) or not source.get("usd_sha256"):
                raise ValueError("voxel product metadata lacks source USD hash")
            if expected_source_sha256 is not None:
                if source["usd_sha256"] != expected_source_sha256:
                    raise ValueError("voxel product source USD hash differs from selected scene")
            else:
                authored_source = source.get("usd")
                if not isinstance(authored_source, str) or not authored_source:
                    raise ValueError("voxel product metadata lacks a verifiable source USD path")
                source_path = Path(authored_source)
                if not source_path.is_absolute():
                    source_path = metadata_path.parent / source_path
                source_raw = source_path.read_bytes()
                if source["usd_sha256"] != hashlib.sha256(source_raw).hexdigest():
                    raise ValueError("voxel product source USD hash differs from sidecar source")
        result = AerialVoxelMap.load(path, proxy_xy_block_size=proxy_xy_block_size)
        if metadata_path is not None:
            result.input_file_hashes[str(metadata_path.resolve())] = hashlib.sha256(
                metadata_path.read_bytes()).hexdigest()
            if expected_source_sha256 is None:
                result.input_file_hashes[str(source_path.resolve())] = hashlib.sha256(
                    source_path.read_bytes()).hexdigest()
            result.provenance_metadata = metadata
        if ground_map_path is not None:
            result.include_ground_collision_proxy(ground_map_path, height_m=ground_proxy_height_m)
        return result
    except OSError as error:
        return UnavailableAerialMap("voxel_product_unavailable", str(path), str(error))
    except (ValueError, KeyError, EOFError, TypeError, BadZipFile, json.JSONDecodeError) as error:
        return UnavailableAerialMap("voxel_product_invalid", str(path), str(error))


def aerial_route_points(entry, position):
    """Materialize the executor's XY waypoints at commanded world altitude.

    Include the final hover goal even when A* stops at a nearby passable cell.
    Call again after allocation/replanning, using the measured current pose.
    """
    start = _vector3(position, "position")
    goal = entry.get("scan_goal")
    altitude = float(goal[2]) if goal is not None else max(1.5, float(start[2]))
    points = [start.tolist()]
    for waypoint in entry.get("waypoints") or ():
        points.append([float(waypoint[0]), float(waypoint[1]), altitude])
    if goal is not None:
        points.append(_vector3(goal, "scan_goal").tolist())
    return points


def plan_local_aerial_detour(aerial_map, start, goal, *, half_extents_m,
                             margin_m=0.1, obstacle_aabbs=(), step_m=1.0,
                             max_candidates=24):
    """Try a bounded set of axis-aligned local detours.

    This is deliberately a baseline: it checks the direct segment first and
    then tries one intermediate point in each horizontal/vertical direction
    and a small set of two-axis combinations.  It never treats an unknown or
    blocked candidate as free and it does not claim global planning.
    """
    start = _vector3(start, "start")
    goal = _vector3(goal, "goal")
    step_m = float(step_m)
    if not math.isfinite(step_m) or step_m <= 0:
        raise ValueError("step_m must be finite and positive")
    direct = aerial_map.check_route([start, goal], half_extents_m=half_extents_m,
                                    margin_m=margin_m, obstacle_aabbs=obstacle_aabbs)
    if direct.get("admitted"):
        return {"admitted": True, "status": "clear", "points": [start.tolist(), goal.tolist()],
                "detour": False, "candidates_checked": 1, "route": direct}
    directions = np.asarray([
        [1, 0, 0], [-1, 0, 0], [0, 1, 0], [0, -1, 0],
        [0, 0, 1], [0, 0, -1],
        [1, 1, 0], [1, -1, 0], [-1, 1, 0], [-1, -1, 0],
    ], dtype=float)
    candidates = []
    for direction in directions:
        candidates.append(start + direction * step_m)
    for direction in directions[:4]:
        candidates.append(start + direction * step_m + np.array([0.0, 0.0, step_m]))
        candidates.append(start + direction * step_m + np.array([0.0, 0.0, -step_m]))
    checked = 1
    for waypoint in candidates[:max_candidates]:
        checked += 1
        route = aerial_map.check_route([start, waypoint, goal], half_extents_m=half_extents_m,
                                       margin_m=margin_m, obstacle_aabbs=obstacle_aabbs)
        if route.get("admitted"):
            return {"admitted": True, "status": "detour", "points": [start.tolist(), waypoint.tolist(), goal.tolist()],
                    "detour": True, "candidates_checked": checked, "route": route}
    return {"admitted": False, "status": direct.get("status", "unknown"),
            "reason": "no_local_detour", "detour": False,
            "candidates_checked": checked, "direct_route": direct}


def resolve_aerial_command(aerial_map, position, goal, *, queued_waypoints=(),
                           half_extents_m, margin_m=0.1, obstacle_aabbs=(),
                           waypoint_tolerance_m=0.2):
    """Resolve one checked command while preserving pending local detours.

    The runtime calls this before every aerial position command in both
    controller and kinematic modes. If the next pending waypoint becomes
    blocked, a newly admitted intermediate is inserted before it; the old
    waypoint and the remainder of the queue are retained. Unknown and blocked
    segments fail closed.
    """
    position = _vector3(position, "position")
    goal = _vector3(goal, "goal")
    tolerance = float(waypoint_tolerance_m)
    if not math.isfinite(tolerance) or tolerance <= 0:
        raise ValueError("waypoint_tolerance_m must be finite and positive")
    pending = [_vector3(point, "queued waypoint").tolist()
               for point in queued_waypoints]
    while pending and math.dist(position, pending[0]) <= tolerance:
        pending.pop(0)
    target = pending[0] if pending else goal.tolist()
    route = aerial_map.check_route(
        [position, target], half_extents_m=half_extents_m,
        margin_m=margin_m, obstacle_aabbs=obstacle_aabbs)
    detour = None
    if not route.get("admitted"):
        detour = plan_local_aerial_detour(
            aerial_map, position, target, half_extents_m=half_extents_m,
            margin_m=margin_m, obstacle_aabbs=obstacle_aabbs)
        if detour.get("admitted"):
            intermediates = [list(point) for point in detour["points"][1:-1]]
            pending = intermediates + pending
            target = pending[0] if pending else goal.tolist()
            route = {**detour["route"], "local_detour": True,
                     "detour_points": detour["points"]}
    return {
        "admitted": bool(route.get("admitted")),
        "command_goal": list(target),
        "queued_waypoints": pending,
        "route": route,
        "detour": detour,
    }


def scene_aerial_voxel_map(scene_key):
    """Read demo-owned product declarations; never infer a provider resource."""
    from importlib import import_module
    from demo.benchmark.registry import SCENARIOS
    from EAI_assets.asset_resolver import usd_root

    scenario = SCENARIOS.get(scene_key)
    if scenario is None:
        return load_aerial_voxel_map(None)
    config = import_module(f"{scenario.__package__}.config")
    relative = getattr(config, "AERIAL_VOXEL_RELATIVE_PATH", None)
    product_path = usd_root() / relative if relative else None
    metadata_relative = getattr(config, "AERIAL_VOXEL_METADATA_RELATIVE_PATH", None)
    metadata_path = usd_root() / metadata_relative if metadata_relative else None
    expected_source_sha256 = None
    usd_relative = getattr(config, "USD_RELATIVE_PATH", None)
    if metadata_path is not None and usd_relative:
        usd_path = usd_root() / usd_relative
        if usd_path.is_file():
            expected_source_sha256 = hashlib.sha256(usd_path.read_bytes()).hexdigest()
    return load_aerial_voxel_map(
        product_path,
        proxy_xy_block_size=getattr(config, "AERIAL_PROXY_XY_BLOCK_SIZE", 1),
        ground_map_path=usd_root() / config.MAP_RELATIVE_PATH if relative else None,
        ground_proxy_height_m=getattr(config, "AERIAL_GROUND_PROXY_HEIGHT_M", 0.8),
        metadata_path=metadata_path,
        expected_source_sha256=expected_source_sha256)


def audit_aerial_plan(scenario, plan, *, aerial_map=None):
    """Check a sequential author witness using the executor's flight baseline.

    Object bounds follow declared completed deliveries. This proves geometric
    admission for that witness only; measured robot motion, sensor visibility
    and simultaneous traffic still require runtime evidence.
    """
    from demo.benchmark.runtime import planning_radius

    robots = {r["id"]: r for r in scenario["robots"]}
    positions = {key: list(r["spawn_pose"]["position"]) for key, r in robots.items()}
    entities = {e["id"]: e for e in scenario["composition"]["entities"]}
    object_positions = {key: list(e["spawn_pose"]["position"]) for key, e in entities.items()}
    targets = {t["id"]: t for t in scenario["composition"]["targets"]}
    nodes = {n["id"]: n for n in scenario["phase_graph"]["nodes"]}
    checks = []
    for entry in plan:
        node = nodes[entry["node"]]
        if node["skill_key"] == "aerial_scan":
            if aerial_map is None:
                aerial_map = scene_aerial_voxel_map(scenario["scene_key"])
            agent = entry["agent"]
            points = aerial_route_points(entry, positions[agent])
            boxes = [oriented_box_aabb(object_positions[key], e["spawn_pose"]["rotation"], e["size_m"])
                     for key, e in entities.items()]
            radius = planning_radius(robots[agent])
            route = aerial_map.check_route(points, half_extents_m=(radius,) * 3,
                                           margin_m=0.1, obstacle_aabbs=boxes)
            detour = None
            if not route["admitted"]:
                detour = plan_local_aerial_detour(aerial_map, points[0], points[-1],
                    half_extents_m=(radius,) * 3, margin_m=0.1, obstacle_aabbs=boxes)
            admitted = route["admitted"] or bool(detour and detour["admitted"])
            checks.append({"node_id": entry["node"], "admitted": admitted,
                           "route": route, "local_detour": detour})
            positions[agent] = points[-1]
        if node["skill_key"] in {"joint_transport", "transport_object"}:
            position = object_positions[node["object_ref"]]
            position[:2] = targets[node["target_ref"]]["position"][:2]
    return {"validated": all(row["admitted"] for row in checks), "checks": checks,
            "input_file_hashes": dict(getattr(aerial_map, "input_file_hashes", {})),
            "scope": "sequential_declared_object_positions_and_static_voxel_geometry",
            "execution_validated": False,
            "limitations": ["not sensor visibility", "not concurrent robot clearance", "not measured flight tracking"]}


def oriented_box_aabb(position, rotation_wxyz, size_m):
    """Conservative world envelope of a task box at its measured pose."""
    position = _vector3(position, "position")
    half = _vector3(size_m, "size_m", positive=True) / 2
    quaternion = np.asarray(rotation_wxyz, dtype=float)
    if (quaternion.shape != (4,) or not np.isfinite(quaternion).all()
            or np.linalg.norm(quaternion) < 1e-12):
        raise ValueError("rotation must be a finite nonzero WXYZ quaternion")
    w, x, y, z = quaternion / np.linalg.norm(quaternion)
    rotation = np.array([[1 - 2*(y*y + z*z), 2*(x*y - z*w), 2*(x*z + y*w)],
                         [2*(x*y + z*w), 1 - 2*(x*x + z*z), 2*(y*z - x*w)],
                         [2*(x*z - y*w), 2*(y*z + x*w), 1 - 2*(x*x + y*y)]])
    world_half = np.abs(rotation) @ half
    return np.concatenate((position - world_half, position + world_half)).tolist()
