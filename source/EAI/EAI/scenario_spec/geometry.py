"""Conservative ground-floor placement checks against the provider occupancy map.

Checks initial placement only, not route feasibility or full 3-D collision.
"""

import hashlib
import math
from pathlib import Path

from .schema import ROBOT_PROFILES
from .validation import ScenarioError


class FactoryMap:
    def __init__(self, yaml_path):
        import numpy as np
        import yaml
        from PIL import Image

        self.path = Path(yaml_path)
        raw = self.path.read_bytes()
        config = yaml.safe_load(raw)
        self.resolution = float(config["resolution"])
        self.origin = tuple(float(item) for item in config["origin"])
        if (not math.isfinite(self.resolution) or self.resolution <= 0 or
                len(self.origin) != 3 or not all(math.isfinite(x) for x in self.origin)):
            raise ScenarioError("invalid occupancy map resolution/origin")
        image_path = self.path.parent / config["image"]
        with Image.open(image_path) as image:
            pixels = np.asarray(image.convert("L"), dtype=float) / 255
        occupancy = pixels if config.get("negate", 0) else 1 - pixels
        threshold = float(config["free_thresh"])
        if not 0 < threshold < 1:
            raise ScenarioError("invalid occupancy map free_thresh")
        self.free = occupancy < threshold  # unknown cells are blocked
        self.height, self.width = self.free.shape
        self.sha256 = hashlib.sha256(raw + image_path.read_bytes()).hexdigest()

    def local(self, x, y):
        c, s = math.cos(self.origin[2]), math.sin(self.origin[2])
        dx, dy = x - self.origin[0], y - self.origin[1]
        return c * dx + s * dy, -s * dx + c * dy

    def world(self, x, y):
        c, s = math.cos(self.origin[2]), math.sin(self.origin[2])
        return self.origin[0] + c*x - s*y, self.origin[1] + s*x + c*y

    def rectangle_free(self, x, y, hx, hy):
        # Enclose the world AABB in map coordinates; conservative for rotated maps.
        points = [self.local(x + sx*hx, y + sy*hy) for sx in (-1, 1) for sy in (-1, 1)]
        ix0 = math.floor(min(p[0] for p in points) / self.resolution)
        ix1 = math.floor(max(p[0] for p in points) / self.resolution)
        iy0 = math.floor(min(p[1] for p in points) / self.resolution)
        iy1 = math.floor(max(p[1] for p in points) / self.resolution)
        if ix0 < 0 or iy0 < 0 or ix1 >= self.width or iy1 >= self.height:
            return False
        return bool(self.free[self.height-1-iy1:self.height-iy0, ix0:ix1+1].all())

    def placement_hints(self, radius=0.9, limit=48):
        result = []
        stride = max(1, math.ceil(1.5 / self.resolution))
        for row in range(stride, self.height, stride):
            for col in range(stride, self.width, stride):
                x, y = self.world((col + 0.5)*self.resolution, (row + 0.5)*self.resolution)
                if self.rectangle_free(x, y, radius, radius):
                    result.append([round(x, 4), round(y, 4)])
        # Spread hints over the map, not just one corner.
        if limit > 1 and len(result) > limit:
            result = [result[round(i*(len(result)-1)/(limit-1))] for i in range(limit)]
        return result


def provider_map(*, ensure=False, scene_key="factory"):
    from EAI_assets.asset_resolver import usd_root
    from EAI_assets.scene_resources import OCCUPANCY_MAP, ensure_scene_resource, scene_resource_relative_paths

    _NVD_MAPS = {
        "warehouse_nvd": usd_root() / "scene" / "warehouse_nvd" / "ground_map_0.6m.yaml",
        "modern_house_nvd": usd_root() / "scene" / "modern_house_nvd" / "ground_map_0.6m.yaml",
    }
    if scene_key in _NVD_MAPS:
        path = _NVD_MAPS[scene_key]
        if not path.exists():
            raise ScenarioError(f"{scene_key} ground map missing: install the demo USD resource bundle")
        return FactoryMap(path)
    if ensure:
        path = ensure_scene_resource("factory", OCCUPANCY_MAP)[0]
    else:
        path = usd_root() / scene_resource_relative_paths("factory", OCCUPANCY_MAP)[0]
    return FactoryMap(path)


def _rect_cell_bounds(occupancy, x, y, hx, hy):
    points = [occupancy.local(x + sx * hx, y + sy * hy) for sx in (-1, 1) for sy in (-1, 1)]
    ix0 = math.floor(min(p[0] for p in points) / occupancy.resolution)
    ix1 = math.floor(max(p[0] for p in points) / occupancy.resolution)
    iy0 = math.floor(min(p[1] for p in points) / occupancy.resolution)
    iy1 = math.floor(max(p[1] for p in points) / occupancy.resolution)
    if ix0 < 0 or iy0 < 0 or ix1 >= occupancy.width or iy1 >= occupancy.height:
        raise ScenarioError(f"rectangle at ({x:.2f}, {y:.2f}) leaves the map")
    return ix0, ix1, iy0, iy1


def ground_surface_exclusions(data):
    """Exclude declared vertical links from unvalidated Factory ground tasks."""
    if data.get("scene_key") != "factory" or data.get("benchmark_task_id") not in {
            "F-INS-01", "F-TRAFFIC-01", "F-COV-01"}:
        return []
    # Explicit vertical objectives opt into a separate surface-following
    # protocol.  This opens the link at the scenario boundary while keeping
    # ordinary ground objectives conservative.
    nodes = data.get("phase_graph", {}).get("nodes", ())
    # Opening a declared surface link is scoped to a vertical-only episode.
    # A mixed DAG must keep ordinary ground objectives away from the same
    # unvalidated envelope; the dedicated vertical executor owns the surface
    # segment after its ground approach has completed.
    if any(node.get("vertical") for node in nodes):
        robots = {robot["id"]: robot for robot in data.get("robots", ())}
        ground_nodes = [node for node in nodes
                        if not node.get("vertical")
                        and (robots.get(node.get("preferred_agent"), {}).get("type")
                             not in {"cf2x", "iris", "pegasus"})]
        if not ground_nodes:
            return []
    from .templates import FACTORY_STAIRS

    padding = 0.02
    return [{"id": surface.id, "kind": surface.kind,
             "aabb_xy_m": [surface.aabb[0] - padding, surface.aabb[1] - padding,
                           surface.aabb[2] + padding, surface.aabb[3] + padding],
             "bounds_rounding_allowance_m": padding,
             "reason": "vertical_surface_not_validated_for_this_ground_navigation_protocol"}
            for surface in FACTORY_STAIRS]


def _point_segment_distance(point, start, end):
    delta_x, delta_y = end[0] - start[0], end[1] - start[1]
    length_squared = delta_x * delta_x + delta_y * delta_y
    if length_squared == 0:
        return math.dist(point, start)
    projection = ((point[0] - start[0]) * delta_x
                  + (point[1] - start[1]) * delta_y) / length_squared
    projection = max(0.0, min(1.0, projection))
    closest = (start[0] + projection * delta_x, start[1] + projection * delta_y)
    return math.dist(point, closest)


def _orientation(first, second, third):
    return ((second[0] - first[0]) * (third[1] - first[1])
            - (second[1] - first[1]) * (third[0] - first[0]))


def _segments_intersect(first_start, first_end, second_start, second_end):
    orientations = (
        _orientation(first_start, first_end, second_start),
        _orientation(first_start, first_end, second_end),
        _orientation(second_start, second_end, first_start),
        _orientation(second_start, second_end, first_end),
    )
    if orientations[0] * orientations[1] < 0 and orientations[2] * orientations[3] < 0:
        return True
    epsilon = 1e-12
    return any(
        abs(orientation) <= epsilon
        and min(start[0], end[0]) - epsilon <= point[0] <= max(start[0], end[0]) + epsilon
        and min(start[1], end[1]) - epsilon <= point[1] <= max(start[1], end[1]) + epsilon
        for orientation, point, start, end in (
            (orientations[0], second_start, first_start, first_end),
            (orientations[1], second_end, first_start, first_end),
            (orientations[2], first_start, second_start, second_end),
            (orientations[3], first_end, second_start, second_end),
        )
    )


def _point_rectangle_distance(point, bounds):
    low_x, low_y, high_x, high_y = bounds
    return math.hypot(max(low_x - point[0], 0, point[0] - high_x),
                      max(low_y - point[1], 0, point[1] - high_y))


def _segment_rectangle_distance(start, end, bounds):
    low_x, low_y, high_x, high_y = bounds
    corners = ((low_x, low_y), (high_x, low_y), (high_x, high_y), (low_x, high_y))
    edges = tuple(zip(corners, (*corners[1:], corners[0])))
    if any(_segments_intersect(start, end, edge_start, edge_end)
           for edge_start, edge_end in edges):
        return 0.0
    return min(
        _point_rectangle_distance(start, bounds),
        _point_rectangle_distance(end, bounds),
        *(_point_segment_distance(corner, start, end) for corner in corners),
    )


def ground_surface_swept_disk_clearance(data, start_xy, end_xy, radius_m):
    """Minimum exact 2-D swept-disk clearance to declared ground exclusions."""
    values = [
        {"surface_id": exclusion["id"],
         "clearance_m": _segment_rectangle_distance(
             start_xy, end_xy, exclusion["aabb_xy_m"]) - radius_m}
        for exclusion in ground_surface_exclusions(data)
    ]
    return min(values, key=lambda item: item["clearance_m"]) if values else None


def validate_ground_surface_access(data):
    exclusions = ground_surface_exclusions(data)
    if not exclusions:
        return
    robots = {robot["id"]: robot for robot in data["robots"]}
    targets = {target["id"]: target for target in data["composition"]["targets"]}
    checks = [(robot["id"], robot["spawn_pose"]["position"], ROBOT_PROFILES[robot["type"]]["radius_m"])
              for robot in robots.values() if ROBOT_PROFILES[robot["type"]]["mobility"] != "aerial"]
    for node in data["phase_graph"]["nodes"]:
        robot = robots.get(node.get("preferred_agent"))
        target = targets.get(node.get("target_ref"))
        if robot and target and ROBOT_PROFILES[robot["type"]]["mobility"] != "aerial":
            checks.append((node["id"], target["position"],
                           max(target["radius_m"], ROBOT_PROFILES[robot["type"]]["radius_m"])))
    for identifier, position, radius in checks:
        for exclusion in exclusions:
            low_x, low_y, high_x, high_y = exclusion["aabb_xy_m"]
            if (position[0] + radius >= low_x and position[0] - radius <= high_x
                    and position[1] + radius >= low_y and position[1] - radius <= high_y):
                raise ScenarioError(f"{identifier}: ground navigation overlaps unverified {exclusion['id']}")


def static_blocked_grid(occupancy, data, *, ground_tracking_reserve_m=0.0):
    """Free grid with static/kinematic entities rasterized as blocked cells."""
    import numpy as np

    if (isinstance(ground_tracking_reserve_m, bool)
            or not isinstance(ground_tracking_reserve_m, (int, float))
            or not math.isfinite(ground_tracking_reserve_m)
            or ground_tracking_reserve_m < 0):
        raise ScenarioError("ground tracking reserve must be finite and nonnegative")

    free = np.array(occupancy.free, copy=True)
    for exclusion in ground_surface_exclusions(data):
        low_x, low_y, high_x, high_y = exclusion["aabb_xy_m"]
        low_x -= ground_tracking_reserve_m
        low_y -= ground_tracking_reserve_m
        high_x += ground_tracking_reserve_m
        high_y += ground_tracking_reserve_m
        corners = [occupancy.local(point_x, point_y)
                   for point_x in (low_x, high_x) for point_y in (low_y, high_y)]
        left = max(0, math.floor(min(point[0] for point in corners) / occupancy.resolution))
        right = min(occupancy.width, math.floor(max(point[0] for point in corners) / occupancy.resolution) + 1)
        bottom = max(0, math.floor(min(point[1] for point in corners) / occupancy.resolution))
        top = min(occupancy.height, math.floor(max(point[1] for point in corners) / occupancy.resolution) + 1)
        if left < right and bottom < top:
            free[occupancy.height - top:occupancy.height - bottom, left:right] = False
    for entity in data["composition"]["entities"]:
        if entity["physics"]["mode"] not in {"static", "kinematic"}:
            continue
        x, y, _ = entity["spawn_pose"]["position"]
        w, _, _, z = entity["spawn_pose"]["rotation"]
        yaw = 2 * math.atan2(z, w)
        c, s = abs(math.cos(yaw)), abs(math.sin(yaw))
        dx, dy, _ = entity["size_m"]
        ix0, ix1, iy0, iy1 = _rect_cell_bounds(occupancy, x, y, (c * dx + s * dy) / 2 + 0.05,
                                                (s * dx + c * dy) / 2 + 0.05)
        free[occupancy.height - 1 - iy1:occupancy.height - iy0, ix0:ix1 + 1] = False
    return free


def validate_connectivity(data, occupancy):
    """Every spawn, target, and dynamic entity must share one free region.

    Static and kinematic entities are rasterized onto the occupancy grid before
    the flood fill, so a barrier that seals a route is rejected here even when
    every individual placement passed the footprint gate.
    """
    from collections import deque

    import numpy as np

    free = static_blocked_grid(occupancy, data)

    def cell(x, y):
        lx, ly = occupancy.local(x, y)
        return math.floor(lx / occupancy.resolution), math.floor(ly / occupancy.resolution)

    points = [("robot:" + robot["id"], *cell(*robot["spawn_pose"]["position"][:2])) for robot in data["robots"]]
    points += [("target:" + target["id"], *cell(*target["position"][:2]))
               for target in data["composition"]["targets"]]
    # Only dynamic bodies must be reachable: a kinematic entity is an
    # externally driven barrier (rasterized above), so its own centre cell is
    # occupied by design and must not be treated as a checkpoint.
    points += [("entity:" + entity["id"], *cell(*entity["spawn_pose"]["position"][:2]))
               for entity in data["composition"]["entities"]
               if entity["physics"]["mode"] == "dynamic"]

    def blocked(ix, iy):
        return not free[occupancy.height - 1 - iy, ix]

    name0, x0, y0 = points[0]
    if blocked(x0, y0):
        raise ScenarioError(f"{name0}: center cell is occupied")
    visited = np.zeros_like(free)
    visited[occupancy.height - 1 - y0, x0] = True
    queue = deque([(x0, y0)])
    while queue:
        ix, iy = queue.popleft()
        for nx, ny in ((ix + 1, iy), (ix - 1, iy), (ix, iy + 1), (ix, iy - 1)):
            if not 0 <= nx < occupancy.width or not 0 <= ny < occupancy.height:
                continue
            row = occupancy.height - 1 - ny
            if visited[row, nx]:
                continue
            visited[row, nx] = True
            if not free[row, nx]:
                continue
            queue.append((nx, ny))
    for name, ix, iy in points[1:]:
        if blocked(ix, iy) or not visited[occupancy.height - 1 - iy, ix]:
            raise ScenarioError(f"{name}: not reachable from {name0} after static barriers")
    return {"status": "connectivity_validated"}


GROUND_LEVEL_Z_BAND = 0.5  # points above this are treated as elevated


def _is_ground_level(z):
    return z < GROUND_LEVEL_Z_BAND


def validate_geometry(data, occupancy):
    validate_ground_surface_access(data)
    boxes = []
    for robot in data["robots"]:
        radius = ROBOT_PROFILES[robot["type"]]["radius_m"]
        if any(a["type"] in {"ur5", "z1"} for a in robot["attachments"]):
            radius = max(radius, 0.9)
        x, y, z = robot["spawn_pose"]["position"]
        boxes.append((robot["id"], x, y, radius, radius, z))
    for entity in data["composition"]["entities"]:
        if entity["physics"]["mode"] == "visual":
            continue
        x, y, ez = entity["spawn_pose"]["position"]
        w, _, _, z = entity["spawn_pose"]["rotation"]
        yaw = 2 * math.atan2(z, w)
        c, s = abs(math.cos(yaw)), abs(math.sin(yaw))
        dx, dy, _ = entity["size_m"]
        boxes.append((entity["id"], x, y, (c*dx+s*dy)/2+0.05, (s*dx+c*dy)/2+0.05, ez))
    for box in boxes:
        name, x, y, hx, hy = box[:5]
        z = box[5] if len(box) > 5 else 0.0
        if not _is_ground_level(z):
            continue  # elevated points have no occupancy map; physics settles
        if not occupancy.rectangle_free(x, y, hx, hy):
            raise ScenarioError(f"{name}: footprint intersects occupied/unknown cells or map boundary")
    for index, box in enumerate(boxes):
        name, x, y, hx, hy = box[:5]
        for other_box in boxes[index+1:]:
            other, ox, oy, ohx, ohy = other_box[:5]
            if abs(x-ox) < hx+ohx and abs(y-oy) < hy+ohy:
                raise ScenarioError(f"initial footprints overlap: {name}, {other}")
    for target in data["composition"]["targets"]:
        x, y, _ = target["position"]
        r = target["radius_m"]
        if _is_ground_level(target["position"][2]) and not occupancy.rectangle_free(x, y, r, r):
            raise ScenarioError(f"{target['id']}: target overlaps occupied/unknown cells or map boundary")
        for other_box in boxes:
            name, bx, by, bhx, bhy = other_box[:5]
            if abs(x-bx) < r+bhx and abs(y-by) < r+bhy:
                raise ScenarioError(f"{target['id']}: target overlaps {name}")

    return {"status": "placement_validated", "map_sha256": occupancy.sha256,
            "oracle_validated": False, "phase_execution": "not_implemented",
            "limitations": ["2-D initial placement only; no path or 3-D clearance proof",
                            "Payload capacities and manipulation execution are not calibrated"]}
