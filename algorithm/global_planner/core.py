# Copyright (c) 2022-2025. Migrated from EAI_assets.controller.traditional.factory_rrt.
"""2D occupancy-grid global planning (A* / RRT) and lookahead waypoint following."""

from __future__ import annotations

import heapq
import math
import os
from typing import Callable, List, Optional, Tuple

import numpy as np
import yaml
from PIL import Image

try:
    from . import _planner_cpp
    _HAS_CPP = True
except ImportError:
    _HAS_CPP = False

Vec2 = Tuple[float, float]
Waypoint = Tuple[float, float, float]  # (x, y, theta)

# 障碍物栅格膨胀（方形结构元半宽，单位：格）。地图常见 resolution=0.05m 时：
# 12 格 ≈ 0.6m、18 格 ≈ 0.9m；默认 12，较原先 18 略减走廊占用、路径更贴可行区域。
DEFAULT_INFLATION_RADIUS_CELLS = 12
# 路径简化 / 降采样时线段碰撞检测的额外邻域（格），与膨胀叠加，使折线离障碍更稳
PATH_SIMPLIFY_CLEARANCE_CELLS = 3
PATH_DOWNSAMPLE_CLEARANCE_CELLS = 2
PATH_RRT_SEGMENT_CLEARANCE_CELLS = 2

class FactoryMapPlanner:
    """Global planner on a Nav2-style 2D map (yaml + image).

    Loads occupancy grid from YAML + PNG, runs A* (optional RRT fallback),
    post-processes path, returns world-frame waypoints ``(x, y, theta)``.
    """

    def __init__(
        self,
        yaml_path: str,
        prefer_astar: bool = True,
        inflation_radius_cells: int = DEFAULT_INFLATION_RADIUS_CELLS,
    ):
        self.yaml_path = os.path.abspath(yaml_path)
        self.prefer_astar = bool(prefer_astar)
        self.inflation_radius_cells = max(0, int(inflation_radius_cells))
        with open(self.yaml_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)

        self.image_rel = cfg["image"]
        self.resolution: float = float(cfg["resolution"])
        self.origin_x: float = float(cfg["origin"][0])
        self.origin_y: float = float(cfg["origin"][1])
        self.occupied_thresh: float = float(cfg.get("occupied_thresh", 0.65))
        self.free_thresh: float = float(cfg.get("free_thresh", 0.196))
        self.negate: int = int(cfg.get("negate", 0))

        yaml_dir = os.path.dirname(self.yaml_path)
        image_path = self.image_rel if os.path.isabs(self.image_rel) else os.path.join(yaml_dir, self.image_rel)
        img = Image.open(image_path).convert("L")
        self.img = np.array(img, dtype=np.uint8)

        occ_grid = self._build_occupancy_grid(self.img)
        occ_grid = np.flipud(occ_grid)
        occ_grid = self._inflate_obstacles(occ_grid, radius_cells=self.inflation_radius_cells)

        self.occ_grid = occ_grid
        self.height, self.width = self.occ_grid.shape

        self._cpp: Optional[_planner_cpp.GridPlanner] = None
        if _HAS_CPP:
            try:
                self._cpp = _planner_cpp.GridPlanner()
                self._cpp.set_grid(
                    self.occ_grid.copy(),
                    self.resolution,
                    self.origin_x,
                    self.origin_y,
                )
            except Exception as exc:
                print(f"[FactoryMapPlanner] C++ init failed ({exc}), using Python fallback")
                self._cpp = None

    def _build_occupancy_grid(self, img: np.ndarray) -> np.ndarray:
        v = img.astype(np.float32)
        if self.negate:
            v = 255.0 - v

        occ_prob = (255.0 - v) / 255.0

        occ_grid = np.ones_like(occ_prob, dtype=np.uint8)
        occ_grid[occ_prob < self.free_thresh] = 0
        return occ_grid

    def _inflate_obstacles(self, occ_grid: np.ndarray, radius_cells: int) -> np.ndarray:
        if radius_cells <= 0:
            return occ_grid

        h, w = occ_grid.shape
        inflated = occ_grid.copy()

        for di in range(-radius_cells, radius_cells + 1):
            for dj in range(-radius_cells, radius_cells + 1):
                if di == 0 and dj == 0:
                    continue
                src_i_start = max(0, -di)
                src_i_end = min(h, h - di)
                src_j_start = max(0, -dj)
                src_j_end = min(w, w - dj)
                if src_i_start >= src_i_end or src_j_start >= src_j_end:
                    continue

                shifted = np.zeros_like(occ_grid, dtype=np.uint8)
                dst_i_start = src_i_start + di
                dst_i_end = src_i_end + di
                dst_j_start = src_j_start + dj
                dst_j_end = src_j_end + dj

                shifted[dst_i_start:dst_i_end, dst_j_start:dst_j_end] = occ_grid[
                    src_i_start:src_i_end, src_j_start:src_j_end
                ]
                inflated = np.maximum(inflated, shifted)

        return inflated

    def world_to_grid(self, x: float, y: float) -> Tuple[int, int]:
        j = int((x - self.origin_x) / self.resolution)
        i = int((y - self.origin_y) / self.resolution)
        return i, j

    def grid_to_world(self, i: int, j: int) -> Vec2:
        x = self.origin_x + (j + 0.5) * self.resolution
        y = self.origin_y + (i + 0.5) * self.resolution
        return float(x), float(y)

    def in_bounds(self, i: int, j: int) -> bool:
        return 0 <= i < self.height and 0 <= j < self.width

    def is_free(self, i: int, j: int) -> bool:
        return self.in_bounds(i, j) and self.occ_grid[i, j] == 0

    def _nearest_free(self, i: int, j: int, max_radius: int = 10) -> Tuple[int, int]:
        if self._cpp is not None:
            return self._cpp.nearest_free(i, j, max_radius)

        if self.is_free(i, j):
            return i, j

        best_ij = None
        best_dist = float("inf")
        for r in range(1, max_radius + 1):
            for di in range(-r, r + 1):
                for dj in range(-r, r + 1):
                    ni, nj = i + di, j + dj
                    if not self.is_free(ni, nj):
                        continue
                    d = abs(di) + abs(dj)
                    if d < best_dist:
                        best_dist = d
                        best_ij = (ni, nj)
            if best_ij is not None:
                return best_ij

        return i, j

    def _heuristic(self, a: Tuple[int, int], b: Tuple[int, int]) -> float:
        return math.hypot(a[0] - b[0], a[1] - b[1])

    def _neighbors(self, i: int, j: int):
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                if di == 0 and dj == 0:
                    continue
                ni, nj = i + di, j + dj
                if not self.is_free(ni, nj):
                    continue
                if di != 0 and dj != 0:
                    if (not self.is_free(i + di, j)) or (not self.is_free(i, j + dj)):
                        continue
                cost = math.hypot(di, dj)
                yield ni, nj, cost

    def _a_star(self, start_ij: Tuple[int, int], goal_ij: Tuple[int, int]) -> List[Tuple[int, int]]:
        if self._cpp is not None:
            return self._cpp.a_star(start_ij, goal_ij)

        (si, sj) = start_ij
        (gi, gj) = goal_ij

        if not self.is_free(si, sj):
            raise ValueError(f"Start cell {start_ij} is not free.")
        if not self.is_free(gi, gj):
            raise ValueError(f"Goal cell {goal_ij} is not free.")

        open_heap = []
        heapq.heappush(open_heap, (0.0, (si, sj)))
        came_from = {(si, sj): None}
        g_score = {(si, sj): 0.0}

        while open_heap:
            _, current = heapq.heappop(open_heap)
            if current == (gi, gj):
                path = [current]
                while came_from[current] is not None:
                    current = came_from[current]
                    path.append(current)
                path.reverse()
                return path

            ci, cj = current
            for ni, nj, step_cost in self._neighbors(ci, cj):
                tentative_g = g_score[current] + step_cost
                if (ni, nj) not in g_score or tentative_g < g_score[(ni, nj)]:
                    g_score[(ni, nj)] = tentative_g
                    f = tentative_g + self._heuristic((ni, nj), (gi, gj))
                    heapq.heappush(open_heap, (f, (ni, nj)))
                    came_from[(ni, nj)] = current

        raise RuntimeError("A* failed to find a path.")

    def _downsample_world_path(self, world_points: List[Vec2], step: float) -> List[Vec2]:
        if not world_points:
            return []
        if self._cpp is not None:
            return self._cpp.downsample_path(world_points, step, PATH_DOWNSAMPLE_CLEARANCE_CELLS)

        waypoints = [world_points[0]]
        i = 0
        clearance = PATH_DOWNSAMPLE_CLEARANCE_CELLS

        for j in range(1, len(world_points)):
            p_prev = waypoints[-1]
            p_curr = world_points[j]
            dist = math.hypot(p_curr[0] - p_prev[0], p_curr[1] - p_prev[1])
            if self._collision_free_segment(p_prev, p_curr, clearance_cells=clearance):
                if dist >= step:
                    waypoints.append(p_curr)
                    i = j
            else:
                for k in range(i + 1, j + 1):
                    waypoints.append(world_points[k])
                i = j

        if waypoints[-1] != world_points[-1]:
            waypoints.append(world_points[-1])

        return waypoints

    def _simplify_world_path(self, world_points: List[Vec2], clearance_cells: int = 1) -> List[Vec2]:
        if len(world_points) <= 2:
            return world_points
        if self._cpp is not None:
            return self._cpp.simplify_path(world_points, clearance_cells)

        simplified: List[Vec2] = [world_points[0]]
        i = 0
        n = len(world_points)
        while i < n - 1:
            j = n - 1
            while j > i + 1:
                if self._collision_free_segment(
                    world_points[i], world_points[j], clearance_cells=clearance_cells
                ):
                    break
                j -= 1
            simplified.append(world_points[j])
            i = j
        return simplified

    def _compute_headings(self, points_xy: List[Vec2]) -> List[Waypoint]:
        if not points_xy:
            return []

        n = len(points_xy)
        out: List[Waypoint] = []
        for k in range(n):
            x, y = points_xy[k]
            if k < n - 1:
                nx, ny = points_xy[k + 1]
                theta = math.atan2(ny - y, nx - x)
            elif n >= 2:
                px, py = points_xy[k - 1]
                theta = math.atan2(y - py, x - px)
            else:
                theta = 0.0
            out.append((x, y, theta))
        return out

    def plan(self, start_xy: Vec2, goal_xy: Vec2, waypoint_step: float = 2.5) -> List[Waypoint]:
        si, sj = self.world_to_grid(*start_xy)
        gi, gj = self.world_to_grid(*goal_xy)
        si, sj = self._nearest_free(si, sj)
        gi, gj = self._nearest_free(gi, gj)
        snap_start = self.grid_to_world(si, sj)
        snap_goal = self.grid_to_world(gi, gj)

        world_path: List[Vec2] = []
        if self.prefer_astar:
            try:
                grid_path = self._a_star((si, sj), (gi, gj))
                world_path = [self.grid_to_world(i, j) for (i, j) in grid_path]
            except Exception:
                world_path = self._plan_rrt(snap_start, snap_goal)
        else:
            world_path = self._plan_rrt(snap_start, snap_goal)

        world_path = self._simplify_world_path(
            world_path, clearance_cells=PATH_SIMPLIFY_CLEARANCE_CELLS
        )
        ds_points = self._downsample_world_path(world_path, step=waypoint_step)
        return self._compute_headings(ds_points)

    def _collision_free_segment(self, p0: Vec2, p1: Vec2, clearance_cells: int = 0) -> bool:
        if self._cpp is not None:
            return self._cpp.collision_free_segment(
                float(p0[0]), float(p0[1]), float(p1[0]), float(p1[1]), clearance_cells,
            )

        x0, y0 = p0
        x1, y1 = p1
        dist = math.hypot(x1 - x0, y1 - y0)
        if dist < 1e-6:
            i, j = self.world_to_grid(x0, y0)
            if not self.in_bounds(i, j):
                return False
            return self.occ_grid[i, j] == 0

        step = self.resolution * 0.5
        num_samples = max(2, int(math.ceil(dist / step)))
        for k in range(num_samples + 1):
            t = k / num_samples
            x = x0 + t * (x1 - x0)
            y = y0 + t * (y1 - y0)
            i, j = self.world_to_grid(x, y)
            if not self.in_bounds(i, j):
                return False
            if clearance_cells > 0:
                for di in range(-clearance_cells, clearance_cells + 1):
                    for dj in range(-clearance_cells, clearance_cells + 1):
                        ni, nj = i + di, j + dj
                        if not self.in_bounds(ni, nj) or self.occ_grid[ni, nj] != 0:
                            return False
            else:
                if self.occ_grid[i, j] != 0:
                    return False
        return True

    def _plan_rrt(
        self,
        start_xy: Vec2,
        goal_xy: Vec2,
        max_iters: int = 5000,
        step_size: float = 0.5,
        goal_sample_rate: float = 0.1,
        goal_tolerance: float = 0.5,
    ) -> List[Vec2]:
        class Node:
            __slots__ = ("x", "y", "parent")

            def __init__(self, x: float, y: float, parent: Optional[int]):
                self.x = x
                self.y = y
                self.parent = parent

        x_min = self.origin_x
        x_max = self.origin_x + self.width * self.resolution
        y_min = self.origin_y
        y_max = self.origin_y + self.height * self.resolution

        nodes: List[Node] = [Node(start_xy[0], start_xy[1], parent=None)]

        def _nearest(qx: float, qy: float) -> int:
            best_idx = 0
            best_dist = float("inf")
            for idx, n in enumerate(nodes):
                d = (n.x - qx) ** 2 + (n.y - qy) ** 2
                if d < best_dist:
                    best_dist = d
                    best_idx = idx
            return best_idx

        si, sj = self.world_to_grid(*start_xy)
        gi, gj = self.world_to_grid(*goal_xy)
        si, sj = self._nearest_free(si, sj)
        gi, gj = self._nearest_free(gi, gj)
        start_xy = self.grid_to_world(si, sj)
        goal_xy = self.grid_to_world(gi, gj)
        nodes[0].x, nodes[0].y = start_xy

        for _ in range(max_iters):
            if np.random.rand() < goal_sample_rate:
                qx, qy = goal_xy
            else:
                qx = float(np.random.uniform(x_min, x_max))
                qy = float(np.random.uniform(y_min, y_max))

            nearest_idx = _nearest(qx, qy)
            nx, ny = nodes[nearest_idx].x, nodes[nearest_idx].y

            theta = math.atan2(qy - ny, qx - nx)
            new_x = nx + step_size * math.cos(theta)
            new_y = ny + step_size * math.sin(theta)

            if not (x_min <= new_x <= x_max and y_min <= new_y <= y_max):
                continue

            if not self._collision_free_segment(
                (nx, ny), (new_x, new_y), clearance_cells=PATH_RRT_SEGMENT_CLEARANCE_CELLS
            ):
                continue

            nodes.append(Node(new_x, new_y, parent=nearest_idx))
            new_idx = len(nodes) - 1

            if math.hypot(new_x - goal_xy[0], new_y - goal_xy[1]) < goal_tolerance:
                if self._collision_free_segment(
                    (new_x, new_y), goal_xy, clearance_cells=PATH_RRT_SEGMENT_CLEARANCE_CELLS
                ):
                    nodes.append(Node(goal_xy[0], goal_xy[1], parent=new_idx))
                    goal_idx = len(nodes) - 1
                    path: List[Vec2] = []
                    cur: Optional[int] = goal_idx
                    while cur is not None:
                        n = nodes[cur]
                        path.append((n.x, n.y))
                        cur = nodes[cur].parent
                    path.reverse()
                    return path

        try:
            grid_path = self._a_star((si, sj), (gi, gj))
            return [self.grid_to_world(i, j) for (i, j) in grid_path]
        except Exception:
            if self._collision_free_segment(
                start_xy, goal_xy, clearance_cells=PATH_RRT_SEGMENT_CLEARANCE_CELLS
            ):
                return [start_xy, goal_xy]
            return [start_xy]


class FactoryWaypointNavigator:
    """Per-robot path follower with smooth lookahead (carrot) along planned polyline."""

    def __init__(self, planner: FactoryMapPlanner, default_waypoint_step: float = 2.5):
        self._planner = planner
        self._default_step = default_waypoint_step
        self.paths: dict[str, List[Waypoint]] = {}
        self.indices: dict[str, int] = {}
        self.active: dict[str, bool] = {}

    def set_path(
        self,
        robot_name: str,
        start_xy: Vec2,
        goal_xy: Vec2,
        waypoint_step: Optional[float] = None,
    ) -> List[Waypoint]:
        step = waypoint_step if waypoint_step is not None else self._default_step
        wps = self._planner.plan(start_xy, goal_xy, waypoint_step=step)
        if not wps:
            return []
        # 规划失败时 plan 可能退化为单点 [start]。此前这里会无条件补一条 start→goal 直线，
        # 在个别场景下会绕过占据栅格审查导致穿障碍。改为：先尝试保守 A*，再做线段碰撞校验。
        if len(wps) == 1:
            w0 = wps[0]
            fx, fy = float(w0[0]), float(w0[1])
            gx, gy = float(goal_xy[0]), float(goal_xy[1])
            if math.hypot(gx - fx, gy - fy) > 0.05:
                try:
                    si, sj = self._planner.world_to_grid(fx, fy)
                    gi, gj = self._planner.world_to_grid(gx, gy)
                    si, sj = self._planner._nearest_free(si, sj)
                    gi, gj = self._planner._nearest_free(gi, gj)
                    grid_path = self._planner._a_star((si, sj), (gi, gj))
                    world_path = [self._planner.grid_to_world(i, j) for (i, j) in grid_path]
                    ds = self._planner._downsample_world_path(world_path, step=step)
                    wps = self._planner._compute_headings(ds)
                except Exception:
                    if self._planner._collision_free_segment(
                        (fx, fy),
                        (gx, gy),
                        clearance_cells=PATH_RRT_SEGMENT_CLEARANCE_CELLS,
                    ):
                        th = math.atan2(gy - fy, gx - fx)
                        wps = [(fx, fy, th), (gx, gy, th)]
                    else:
                        return []
        self.paths[robot_name] = wps
        self.indices[robot_name] = 0
        self.active[robot_name] = True
        return wps

    def set_waypoints(
        self,
        robot_name: str,
        waypoints: List[Waypoint],
        active: bool = False,
    ) -> None:
        self.paths[robot_name] = list(waypoints)
        self.indices[robot_name] = 0
        self.active[robot_name] = active

    def set_active(self, robot_name: str, active: bool) -> None:
        self.active[robot_name] = active

    def is_active(self, robot_name: str) -> bool:
        return self.active.get(robot_name, False)

    def get_waypoints(self, robot_name: str) -> List[Waypoint]:
        return self.paths.get(robot_name, [])

    def get_index(self, robot_name: str) -> int:
        return self.indices.get(robot_name, 0)

    def compute_goal(
        self,
        robot_name: str,
        curr_pos_xy: Vec2,
        curr_z: float,
        arrive_radius: float = 0.4,
        lookahead: float = 1.5,
        final_radius: float = 0.3,
    ) -> Tuple[Optional[Waypoint], bool]:
        if not self.active.get(robot_name, False):
            return None, True

        wps = self.paths.get(robot_name, [])
        idx = self.indices.get(robot_name, 0)

        if not wps:
            return None, True

        final_xy = (float(wps[-1][0]), float(wps[-1][1]))
        dist_to_final = math.hypot(curr_pos_xy[0] - final_xy[0], curr_pos_xy[1] - final_xy[1])
        if dist_to_final <= final_radius:
            self.indices[robot_name] = len(wps)
            self.active[robot_name] = False
            return (final_xy[0], final_xy[1], curr_z), True

        while idx < max(0, len(wps) - 1):
            wx, wy = float(wps[idx][0]), float(wps[idx][1])
            if math.hypot(curr_pos_xy[0] - wx, curr_pos_xy[1] - wy) < arrive_radius:
                idx += 1
            else:
                break
        self.indices[robot_name] = idx

        if idx >= len(wps):
            self.active[robot_name] = False
            return (final_xy[0], final_xy[1], curr_z), True

        if idx >= len(wps) - 1:
            self.indices[robot_name] = len(wps) - 1
            return (final_xy[0], final_xy[1], curr_z), False

        start_x = float(wps[idx][0])
        start_y = float(wps[idx][1])
        seg_start_idx = idx
        if idx < len(wps) - 1:
            ax, ay = float(wps[idx][0]), float(wps[idx][1])
            bx, by = float(wps[idx + 1][0]), float(wps[idx + 1][1])
            vx, vy = bx - ax, by - ay
            seg_len_sq = vx * vx + vy * vy
            if seg_len_sq > 1e-9:
                t = ((curr_pos_xy[0] - ax) * vx + (curr_pos_xy[1] - ay) * vy) / seg_len_sq
                t = max(0.0, min(1.0, t))
                start_x = ax + t * vx
                start_y = ay + t * vy

        la_x = start_x
        la_y = start_y
        remaining = lookahead

        for k in range(seg_start_idx, len(wps) - 1):
            p0x = la_x if k == seg_start_idx else float(wps[k][0])
            p0y = la_y if k == seg_start_idx else float(wps[k][1])
            p1x = float(wps[k + 1][0])
            p1y = float(wps[k + 1][1])
            seg_dx = p1x - p0x
            seg_dy = p1y - p0y
            seg_len = math.hypot(seg_dx, seg_dy)
            if seg_len < 1e-6:
                continue
            if remaining <= seg_len:
                t = remaining / seg_len
                la_x = p0x + t * seg_dx
                la_y = p0y + t * seg_dy
                break
            remaining -= seg_len
            la_x = p1x
            la_y = p1y

        return (la_x, la_y, curr_z), False


class FactoryMapVisualizer:
    """Optional matplotlib map + path viewer (requires matplotlib)."""

    def __init__(
        self,
        planner: FactoryMapPlanner,
        goal_callback: Optional[Callable[[float, float], None]] = None,
    ):
        self._planner = planner
        self._goal_callback = goal_callback
        self._enabled = False

        try:
            import matplotlib.pyplot as plt  # noqa: WPS433
        except Exception:
            print("[MAP_VIZ] matplotlib not available; map visualization disabled.")
            self._plt = None
            return

        self._plt = plt
        plt.ion()

        self._fig, self._ax = plt.subplots()
        self._ax.imshow(
            planner.occ_grid,
            origin="lower",
            cmap="gray_r",
            interpolation="nearest",
        )
        (self._path_line,) = self._ax.plot([], [], "r-", linewidth=2)
        (self._wp_scatter,) = self._ax.plot([], [], "bo", markersize=4)
        (self._robot_dot,) = self._ax.plot([], [], "go", markersize=6)

        self._ax.set_title("Factory Map & Global Path")
        self._ax.set_xlabel("grid-j")
        self._ax.set_ylabel("grid-i")
        self._ax.set_aspect("equal")

        self._fig.canvas.mpl_connect("button_press_event", self._on_click)

        self._enabled = True
        self._refresh()

    def _refresh(self) -> None:
        if not self._enabled or self._plt is None:
            return
        self._fig.canvas.draw_idle()
        self._fig.canvas.flush_events()

    def update_path(self, waypoints: List[Waypoint]) -> None:
        if not self._enabled:
            return

        if not waypoints:
            self._path_line.set_data([], [])
            self._wp_scatter.set_data([], [])
        else:
            js: List[int] = []
            is_: List[int] = []
            for x, y, _theta in waypoints:
                i, j = self._planner.world_to_grid(x, y)
                is_.append(i)
                js.append(j)
            self._path_line.set_data(js, is_)
            self._wp_scatter.set_data(js, is_)

        self._refresh()

    def update_robot(self, x: float, y: float) -> None:
        if not self._enabled:
            return
        i, j = self._planner.world_to_grid(x, y)
        self._robot_dot.set_data([j], [i])
        self._refresh()

    def _on_click(self, event) -> None:
        if not self._enabled or self._goal_callback is None:
            return
        if event.inaxes != self._ax:
            return
        if event.button != 1:
            return
        if event.xdata is None or event.ydata is None:
            return

        j = int(round(event.xdata))
        i = int(round(event.ydata))
        if not (0 <= i < self._planner.height and 0 <= j < self._planner.width):
            return

        x, y = self._planner.grid_to_world(i, j)
        print(f"[MAP_VIZ] clicked goal at grid=({i}, {j}) -> world=({x:.2f}, {y:.2f})")
        try:
            self._goal_callback(x, y)
        except Exception as exc:
            print(f"[MAP_VIZ] goal callback failed: {exc}")


# ═══════════════════════════════════════════════════════════════════════════════
#  Multi-robot space-time planning (Cooperative A* with reservation table)
# ═══════════════════════════════════════════════════════════════════════════════

_STNode = Tuple[int, int, int]  # (grid_i, grid_j, time_step)


class ReservationTable:
    """Discrete 3-D occupancy table ``(i, j, t)`` shared by all robots.

    Tracks two constraint types:
    * **vertex** – cell ``(i, j)`` at time ``t`` is occupied.
    * **edge**   – transition ``(i1,j1,t) -> (i2,j2,t+1)`` is reserved, so the
      reverse ``(i2,j2,t) -> (i1,j1,t+1)`` is blocked (swap / head-on conflict).
    """

    def __init__(self) -> None:
        self._vertex: dict[_STNode, str] = {}
        self._edge: dict[Tuple[_STNode, _STNode], str] = {}
        self._cpp_st = None  # set by SpaceTimeAStar when C++ is available

    def clear(self) -> None:
        self._vertex.clear()
        self._edge.clear()
        if self._cpp_st is not None:
            self._cpp_st.clear()

    def clear_robot(self, robot_name: str) -> None:
        """Remove all reservations belonging to *robot_name*."""
        self._vertex = {k: v for k, v in self._vertex.items() if v != robot_name}
        self._edge = {k: v for k, v in self._edge.items() if v != robot_name}
        if self._cpp_st is not None:
            self._cpp_st.clear_robot(robot_name)

    def reserve_vertex(self, i: int, j: int, t: int, robot_name: str) -> None:
        self._vertex[(i, j, t)] = robot_name

    def reserve_edge(
        self, i1: int, j1: int, i2: int, j2: int, t: int, robot_name: str
    ) -> None:
        self._edge[((i1, j1, t), (i2, j2, t + 1))] = robot_name

    def is_vertex_free(self, i: int, j: int, t: int, robot_name: str) -> bool:
        owner = self._vertex.get((i, j, t))
        return owner is None or owner == robot_name

    def is_edge_free(
        self, i1: int, j1: int, i2: int, j2: int, t: int, robot_name: str
    ) -> bool:
        """Check swap conflict: robot at (i2,j2,t)->(i1,j1,t+1) already reserved."""
        owner = self._edge.get(((i2, j2, t), (i1, j1, t + 1)))
        return owner is None or owner == robot_name

    def reserve_path(
        self, grid_path: List[Tuple[int, int]], robot_name: str
    ) -> None:
        for t, (gi, gj) in enumerate(grid_path):
            self.reserve_vertex(gi, gj, t, robot_name)
            if t > 0:
                pi, pj = grid_path[t - 1]
                self.reserve_edge(pi, pj, gi, gj, t - 1, robot_name)
        if grid_path:
            last_i, last_j = grid_path[-1]
            last_t = len(grid_path) - 1
            for extra_t in range(last_t + 1, last_t + 30):
                self.reserve_vertex(last_i, last_j, extra_t, robot_name)
        if self._cpp_st is not None:
            self._cpp_st.reserve_path(grid_path, robot_name)

    @property
    def max_reserved_t(self) -> int:
        if not self._vertex:
            return 0
        return max(t for (_, _, t) in self._vertex)


class SpaceTimeAStar:
    """A* on a 3-D ``(i, j, t)`` graph that respects a :class:`ReservationTable`.

    *wait-in-place* is a valid action (cost 1).  Diagonal moves are allowed when
    both adjacent axis-aligned cells are free (same rule as :class:`FactoryMapPlanner`).
    """

    def __init__(
        self,
        planner: FactoryMapPlanner,
        reservation: ReservationTable,
        max_time_horizon: int = 256,
        max_expansions: int = 200000,
    ) -> None:
        self._planner = planner
        self._res = reservation
        self._max_t = max_time_horizon
        self._max_expansions = max_expansions

        self._cpp_st: Optional[_planner_cpp.SpaceTimeAStar] = None  # type: ignore[name-defined]
        if _HAS_CPP and planner._cpp is not None:
            try:
                self._cpp_st = _planner_cpp.SpaceTimeAStar()
                self._cpp_st.set_planner(planner._cpp)
                self._cpp_st.max_time_horizon = max_time_horizon
                self._cpp_st.max_expansions = max_expansions
                reservation._cpp_st = self._cpp_st
            except Exception:
                self._cpp_st = None

    def plan(
        self,
        start_ij: Tuple[int, int],
        goal_ij: Tuple[int, int],
        robot_name: str,
    ) -> Optional[List[Tuple[int, int]]]:
        """Return a timed grid path ``[(i,j), ...]`` (index = time step) or ``None``."""
        if self._cpp_st is not None:
            result = self._cpp_st.plan(start_ij, goal_ij, robot_name)
            return result

        si, sj = start_ij
        gi, gj = goal_ij
        p = self._planner

        if not p.is_free(si, sj) or not p.is_free(gi, gj):
            return None

        # Dynamic horizon: still bounded by hard cap, but reserve extra wait budget
        # for conflict resolution around already-reserved trajectories.
        base_h = math.hypot(si - gi, sj - gj)
        dynamic_t_cap = min(
            self._max_t,
            max(
                96,
                int(base_h * 4.0) + 40,
                self._res.max_reserved_t + 40,
            ),
        )

        open_heap: list = []
        h0 = math.hypot(si - gi, sj - gj)
        heapq.heappush(open_heap, (h0, 0.0, 0, si, sj))
        came_from: dict[_STNode, Optional[_STNode]] = {(si, sj, 0): None}
        g_score: dict[_STNode, float] = {(si, sj, 0): 0.0}

        _counter = 0
        expansions = 0

        while open_heap:
            _, _, t, ci, cj = heapq.heappop(open_heap)
            expansions += 1
            if expansions > self._max_expansions:
                return None
            if ci == gi and cj == gj:
                path: List[Tuple[int, int]] = []
                cur: Optional[_STNode] = (ci, cj, t)
                while cur is not None:
                    path.append((cur[0], cur[1]))
                    cur = came_from[cur]
                path.reverse()
                return path

            if t >= dynamic_t_cap:
                continue

            nt = t + 1
            neighbors: List[Tuple[int, int, float]] = [(ci, cj, 1.0)]  # wait
            for di in (-1, 0, 1):
                for dj in (-1, 0, 1):
                    if di == 0 and dj == 0:
                        continue
                    ni, nj = ci + di, cj + dj
                    if not p.is_free(ni, nj):
                        continue
                    if di != 0 and dj != 0:
                        if not p.is_free(ci + di, cj) or not p.is_free(ci, cj + dj):
                            continue
                    cost = math.hypot(di, dj)
                    neighbors.append((ni, nj, cost))

            for ni, nj, step_cost in neighbors:
                if not self._res.is_vertex_free(ni, nj, nt, robot_name):
                    continue
                if not self._res.is_edge_free(ci, cj, ni, nj, t, robot_name):
                    continue

                tentative_g = g_score[(ci, cj, t)] + step_cost
                key = (ni, nj, nt)
                if key not in g_score or tentative_g < g_score[key]:
                    g_score[key] = tentative_g
                    h = math.hypot(ni - gi, nj - gj)
                    f = tentative_g + h
                    _counter += 1
                    heapq.heappush(open_heap, (f, tentative_g, nt, ni, nj))
                    came_from[key] = (ci, cj, t)

        return None
