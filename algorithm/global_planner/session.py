# Copyright (c) 2022-2025. Planning + tracking session (no torch / Isaac).
"""Binds :class:`FactoryWaypointNavigator` with velocity law and smoothing."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Literal, Optional, Tuple

from .core import (
    DEFAULT_INFLATION_RADIUS_CELLS,
    FactoryMapPlanner,
    FactoryWaypointNavigator,
    ReservationTable,
    SpaceTimeAStar,
    Vec2,
    Waypoint,
)
from .tracking import (
    NavTrackConfig,
    RobotNavProfile,
    compute_raw_velocity_command,
    infer_profile,
    smooth_alpha_for_profile,
    smooth_velocity,
)


@dataclass
class StepCommand:
    """One simulation step worth of high-level command for a single robot."""

    kind: Literal["track_goal", "hold_goal", "velocity", "idle"]
    goal_xyz: Optional[Tuple[float, float, float]] = None
    vx: float = 0.0
    vy: float = 0.0
    wz: float = 0.0
    path_complete: bool = False


class GlobalNavSession:
    """Owns planner + navigator + per-robot velocity memory; pure float API."""

    def __init__(
        self,
        map_yaml: str,
        *,
        waypoint_step: float = 1.0,
        prefer_astar: bool = True,
        inflation_radius_cells: int = DEFAULT_INFLATION_RADIUS_CELLS,
        track_config: Optional[NavTrackConfig] = None,
    ) -> None:
        self.planner = FactoryMapPlanner(
            map_yaml,
            prefer_astar=prefer_astar,
            inflation_radius_cells=inflation_radius_cells,
        )
        self.navigator = FactoryWaypointNavigator(self.planner, default_waypoint_step=waypoint_step)
        self.cfg = track_config or NavTrackConfig()

        self._modes: Dict[str, Literal["goal", "velocity"]] = {}
        self._profiles: Dict[str, RobotNavProfile] = {}
        self._smooth_alpha: Dict[str, float] = {}
        self._last_vel: Dict[str, Tuple[float, float, float]] = {}

        self.reservation = ReservationTable()
        self._st_astar = SpaceTimeAStar(self.planner, self.reservation)

    def register_agent(self, robot_name: str, *, use_goal_position: bool) -> None:
        self._modes[robot_name] = "goal" if use_goal_position else "velocity"
        profile = infer_profile(robot_name)
        self._profiles[robot_name] = profile
        self._smooth_alpha[robot_name] = smooth_alpha_for_profile(profile, self.cfg)
        self._last_vel.setdefault(robot_name, (0.0, 0.0, 0.0))

    def plan_to_goal(self, robot_name: str, start_xy: Vec2, goal_xy: Vec2) -> bool:
        wps = self.navigator.set_path(robot_name, start_xy, goal_xy, waypoint_step=None)
        return bool(wps)

    # ── Multi-robot batch planning with space-time conflict avoidance ─────────

    def plan_batch(
        self,
        requests: List[Tuple[str, Vec2, Vec2]],
        *,
        waypoint_step: Optional[float] = None,
        priorities: Optional[Dict[str, int]] = None,
    ) -> Dict[str, List[Waypoint]]:
        """Plan paths for multiple robots while avoiding space-time conflicts.

        *requests*: ``[(robot_name, start_xy, goal_xy), ...]``
        *priorities*: optional ``{robot_name: int}``; lower value = higher priority
            (planned first, others avoid it).  Robots not in the dict get priority 999.

        Returns ``{robot_name: waypoints}`` for each successfully planned robot.
        Robots whose ST-A* fails get a standard (non-conflict-aware) fallback path.
        """
        step = waypoint_step if waypoint_step is not None else self.navigator._default_step
        prio = priorities or {}

        sorted_requests = sorted(
            requests,
            key=lambda r: prio.get(r[0], 999),
        )

        # Fast path: plan in 2D first, only escalate to ST-A* when conflicts are detected.
        naive_results: Dict[str, List[Waypoint]] = {}
        naive_grid_paths: Dict[str, List[Tuple[int, int]]] = {}
        all_naive_ok = True
        for robot_name, start_xy, goal_xy in sorted_requests:
            wps = self.navigator.set_path(robot_name, start_xy, goal_xy, waypoint_step=step)
            if not wps:
                all_naive_ok = False
                break
            if not self._validate_waypoint_segments(wps, clearance_cells=4):
                all_naive_ok = False
                break
            naive_results[robot_name] = wps
            naive_grid_paths[robot_name] = [
                self.planner.world_to_grid(float(w[0]), float(w[1])) for w in wps
            ]

        if all_naive_ok and not self._has_spacetime_conflicts(naive_grid_paths, safety_radius_m=0.9):
            self.reservation.clear()
            for robot_name, _, _ in sorted_requests:
                self.reservation.reserve_path(naive_grid_paths[robot_name], robot_name)
                self.navigator.active[robot_name] = True
            print(f"[GlobalNavSession] [batch] fast-path: no conflict, robots={len(naive_results)}")
            return naive_results

        if all_naive_ok:
            print("[GlobalNavSession] [batch] conflict detected, escalate to ST-A*")
        else:
            print("[GlobalNavSession] [batch] fast-path failed, fallback to ST-A* pipeline")

        self.reservation.clear()
        results: Dict[str, List[Waypoint]] = {}

        for robot_name, start_xy, goal_xy in sorted_requests:
            print(f"[GlobalNavSession] [batch] planning {robot_name}: {start_xy} -> {goal_xy}")
            self.reservation.clear_robot(robot_name)

            si, sj = self.planner.world_to_grid(*start_xy)
            gi, gj = self.planner.world_to_grid(*goal_xy)
            si, sj = self.planner._nearest_free(si, sj)
            gi, gj = self.planner._nearest_free(gi, gj)

            timed_grid_path = self._st_astar.plan((si, sj), (gi, gj), robot_name)

            if timed_grid_path:
                self.reservation.reserve_path(timed_grid_path, robot_name)
                unique_ij: list[Tuple[int, int]] = []
                for cell in timed_grid_path:
                    if not unique_ij or unique_ij[-1] != cell:
                        unique_ij.append(cell)
                world_path = [self.planner.grid_to_world(i, j) for (i, j) in unique_ij]
                world_path = self.planner._simplify_world_path(world_path, clearance_cells=1)
                ds = self.planner._downsample_world_path(world_path, step=step)
                wps = self.planner._compute_headings(ds)

                self.navigator.paths[robot_name] = wps
                self.navigator.indices[robot_name] = 0
                self.navigator.active[robot_name] = True
                results[robot_name] = wps
                print(f"[GlobalNavSession] [batch] done {robot_name}, waypoints={len(wps)} (ST-A*)")
            else:
                print(f"[GlobalNavSession] [batch] ST-A* fallback for {robot_name}, use 2D A*")
                wps = self.navigator.set_path(
                    robot_name, start_xy, goal_xy, waypoint_step=step
                )
                if wps:
                    if not self._validate_waypoint_segments(wps, clearance_cells=4):
                        cons = self._build_conservative_astar_waypoints(start_xy, goal_xy, step)
                        if cons:
                            wps = cons
                    results[robot_name] = wps
                    grid_path_fb = [
                        self.planner.world_to_grid(float(w[0]), float(w[1])) for w in wps
                    ]
                    self.reservation.reserve_path(grid_path_fb, robot_name)
                    print(f"[GlobalNavSession] [batch] done {robot_name}, waypoints={len(wps)} (fallback)")
                else:
                    print(f"[GlobalNavSession] [batch] failed {robot_name}: no path")

        return results

    def _has_spacetime_conflicts(
        self,
        paths: Dict[str, List[Tuple[int, int]]],
        hold_tail: int = 20,
        safety_radius_m: float = 0.0,
    ) -> bool:
        """Detect vertex and edge-swap conflicts on discretized timed paths."""
        if len(paths) <= 1:
            return False

        names = list(paths.keys())
        for i in range(len(names)):
            n1 = names[i]
            p1 = paths[n1]
            if not p1:
                continue
            for j in range(i + 1, len(names)):
                n2 = names[j]
                p2 = paths[n2]
                if not p2:
                    continue
                t_max = max(len(p1), len(p2)) + hold_tail
                for t in range(t_max):
                    a_now = p1[min(t, len(p1) - 1)]
                    b_now = p2[min(t, len(p2) - 1)]
                    if a_now == b_now:
                        return True
                    if safety_radius_m > 0.0:
                        aw = self.planner.grid_to_world(a_now[0], a_now[1])
                        bw = self.planner.grid_to_world(b_now[0], b_now[1])
                        if math.hypot(aw[0] - bw[0], aw[1] - bw[1]) < safety_radius_m:
                            return True
                    if t == 0:
                        continue
                    a_prev = p1[min(t - 1, len(p1) - 1)]
                    b_prev = p2[min(t - 1, len(p2) - 1)]
                    if a_prev == b_now and b_prev == a_now:
                        return True
                    if safety_radius_m > 0.0:
                        a0 = self.planner.grid_to_world(a_prev[0], a_prev[1])
                        a1 = self.planner.grid_to_world(a_now[0], a_now[1])
                        b0 = self.planner.grid_to_world(b_prev[0], b_prev[1])
                        b1 = self.planner.grid_to_world(b_now[0], b_now[1])
                        if self._segment_segment_min_dist(a0, a1, b0, b1) < safety_radius_m:
                            return True
        return False

    def _validate_waypoint_segments(self, wps: List[Waypoint], clearance_cells: int = 3) -> bool:
        if len(wps) <= 1:
            return True
        for i in range(len(wps) - 1):
            p0 = (float(wps[i][0]), float(wps[i][1]))
            p1 = (float(wps[i + 1][0]), float(wps[i + 1][1]))
            if not self.planner._collision_free_segment(p0, p1, clearance_cells=clearance_cells):
                return False
        return True

    def _build_conservative_astar_waypoints(
        self, start_xy: Vec2, goal_xy: Vec2, step: float
    ) -> List[Waypoint]:
        try:
            si, sj = self.planner.world_to_grid(*start_xy)
            gi, gj = self.planner.world_to_grid(*goal_xy)
            si, sj = self.planner._nearest_free(si, sj)
            gi, gj = self.planner._nearest_free(gi, gj)
            grid_path = self.planner._a_star((si, sj), (gi, gj))
            world_path = [self.planner.grid_to_world(i, j) for (i, j) in grid_path]
            ds = self.planner._downsample_world_path(world_path, step=step)
            return self.planner._compute_headings(ds)
        except Exception:
            return []

    @staticmethod
    def _point_segment_dist(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
        abx = bx - ax
        aby = by - ay
        apx = px - ax
        apy = py - ay
        den = abx * abx + aby * aby
        if den <= 1e-9:
            return math.hypot(px - ax, py - ay)
        t = max(0.0, min(1.0, (apx * abx + apy * aby) / den))
        cx = ax + t * abx
        cy = ay + t * aby
        return math.hypot(px - cx, py - cy)

    @classmethod
    def _segment_segment_min_dist(cls, a0: Vec2, a1: Vec2, b0: Vec2, b1: Vec2) -> float:
        return min(
            cls._point_segment_dist(a0[0], a0[1], b0[0], b0[1], b1[0], b1[1]),
            cls._point_segment_dist(a1[0], a1[1], b0[0], b0[1], b1[0], b1[1]),
            cls._point_segment_dist(b0[0], b0[1], a0[0], a0[1], a1[0], a1[1]),
            cls._point_segment_dist(b1[0], b1[1], a0[0], a0[1], a1[0], a1[1]),
        )

    def replan_single(
        self,
        robot_name: str,
        start_xy: Vec2,
        goal_xy: Vec2,
        *,
        waypoint_step: Optional[float] = None,
    ) -> List[Waypoint]:
        """Replan one robot respecting existing reservations of other robots."""
        step = waypoint_step if waypoint_step is not None else self.navigator._default_step
        self.reservation.clear_robot(robot_name)

        si, sj = self.planner.world_to_grid(*start_xy)
        gi, gj = self.planner.world_to_grid(*goal_xy)
        si, sj = self.planner._nearest_free(si, sj)
        gi, gj = self.planner._nearest_free(gi, gj)

        timed_grid_path = self._st_astar.plan((si, sj), (gi, gj), robot_name)

        if timed_grid_path:
            self.reservation.reserve_path(timed_grid_path, robot_name)
            unique_ij: list[Tuple[int, int]] = []
            for cell in timed_grid_path:
                if not unique_ij or unique_ij[-1] != cell:
                    unique_ij.append(cell)
            world_path = [self.planner.grid_to_world(i, j) for (i, j) in unique_ij]
            world_path = self.planner._simplify_world_path(world_path, clearance_cells=1)
            ds = self.planner._downsample_world_path(world_path, step=step)
            wps = self.planner._compute_headings(ds)

            self.navigator.paths[robot_name] = wps
            self.navigator.indices[robot_name] = 0
            self.navigator.active[robot_name] = True
            print(f"[GlobalNavSession] [replan] {robot_name} ST-A* waypoints={len(wps)}")
            return wps

        print(f"[GlobalNavSession] [replan] {robot_name} fallback to 2D A*")
        wps = self.navigator.set_path(robot_name, start_xy, goal_xy, waypoint_step=step)
        if wps:
            if not self._validate_waypoint_segments(wps, clearance_cells=4):
                cons = self._build_conservative_astar_waypoints(start_xy, goal_xy, step)
                if cons:
                    wps = cons
            grid_path_fb = [
                self.planner.world_to_grid(float(w[0]), float(w[1])) for w in wps
            ]
            self.reservation.reserve_path(grid_path_fb, robot_name)
        return wps or []

    def is_navigating(self, robot_name: str) -> bool:
        return self.navigator.is_active(robot_name)

    def step(
        self,
        robot_name: str,
        x: float,
        y: float,
        z: float,
        yaw: float,
    ) -> StepCommand:
        mode = self._modes.get(robot_name, "velocity")
        if mode == "goal":
            return self._step_goal(robot_name, x, y, z)
        return self._step_velocity(robot_name, x, y, z, yaw)

    def _arrival_dist(self, robot_name: str) -> float:
        return self.cfg.scout_arrival_dist if robot_name == "scout_1" else self.cfg.arrival_dist

    def _step_goal(self, robot_name: str, x: float, y: float, z: float) -> StepCommand:
        if not self.navigator.is_active(robot_name):
            return StepCommand(kind="hold_goal", goal_xyz=(x, y, z))

        cxy = (float(x), float(y))
        nav_goal, done = self.navigator.compute_goal(
            robot_name,
            cxy,
            z,
            arrive_radius=max(self.cfg.nav_arrive_radius, self.cfg.arrival_dist),
            lookahead=self.cfg.nav_lookahead,
            final_radius=self.cfg.nav_final_radius,
        )
        if done and nav_goal is not None:
            return StepCommand(
                kind="track_goal",
                goal_xyz=(float(nav_goal[0]), float(nav_goal[1]), float(nav_goal[2])),
                path_complete=True,
            )

        if nav_goal is not None:
            return StepCommand(
                kind="track_goal",
                goal_xyz=(float(nav_goal[0]), float(nav_goal[1]), float(nav_goal[2])),
            )
        return StepCommand(kind="hold_goal", goal_xyz=(x, y, z))

    def _step_velocity(self, robot_name: str, x: float, y: float, z: float, yaw: float) -> StepCommand:
        if not self.navigator.is_active(robot_name):
            self._last_vel[robot_name] = (0.0, 0.0, 0.0)
            return StepCommand(kind="idle", vx=0.0, vy=0.0, wz=0.0)

        profile = self._profiles.get(robot_name, RobotNavProfile.GENERIC)
        cxy = (float(x), float(y))
        nav_goal, nav_done = self.navigator.compute_goal(
            robot_name,
            cxy,
            z,
            arrive_radius=max(self._arrival_dist(robot_name), self.cfg.nav_arrive_radius),
            lookahead=self.cfg.nav_lookahead,
            final_radius=self.cfg.nav_final_radius,
        )

        if nav_done or nav_goal is None:
            self.navigator.set_active(robot_name, False)
            self._last_vel[robot_name] = (0.0, 0.0, 0.0)
            return StepCommand(kind="idle", vx=0.0, vy=0.0, wz=0.0, path_complete=True)

        tx, ty = float(nav_goal[0]), float(nav_goal[1])
        dist = math.hypot(tx - x, ty - y)

        raw = compute_raw_velocity_command(
            profile,
            x,
            y,
            yaw,
            tx,
            ty,
            cfg=self.cfg,
        )
        if dist < 1e-4:
            raw = (0.0, 0.0, 0.0)

        alpha = self._smooth_alpha.get(robot_name, self.cfg.smooth_alpha_carter)
        last = self._last_vel.get(robot_name, (0.0, 0.0, 0.0))
        sm = smooth_velocity(last, raw, alpha)
        self._last_vel[robot_name] = sm
        return StepCommand(kind="velocity", vx=sm[0], vy=sm[1], wz=sm[2])
