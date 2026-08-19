"""Mission state and synchronized trajectory playback for EAI db-CBS."""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
from typing import Callable, Mapping, Sequence

from .map_environment import PlanningFrame, build_planning_frame
from .planner import DbcbsPlan, PlanarAgent, run_dbcbs
from .trajectory import PlanarTarget, SynchronizedTrajectoryPlayer


@dataclass(frozen=True)
class PreparedDbcbsMission:
    """Immutable handoff from a planner worker to the simulation thread."""

    result: dict[str, bool]
    plan: DbcbsPlan | None

    @property
    def succeeded(self) -> bool:
        return bool(self.result) and all(self.result.values())


class DbcbsNavigationSession:
    def __init__(
        self,
        map_yaml: str | Path,
        *,
        plan_scale: float = 4.0,
        time_scale: float = 8.0,
        planning_timeout: float = 60.0,
        robot_radius: float = 0.60,
        robot_radii: Mapping[str, float] | None = None,
        safety_margin: float = 0.10,
        coarsen_factor: int = 4,
        arrival_tolerance: float = 0.30,
        planner: Callable[..., DbcbsPlan] = run_dbcbs,
        planning_frame: PlanningFrame | None = None,
    ) -> None:
        if time_scale <= 0.0:
            raise ValueError("db-CBS time scale must be positive")
        self.frame = planning_frame or build_planning_frame(
            map_yaml,
            scale=plan_scale,
            robot_radius=robot_radius,
            safety_margin=safety_margin,
            coarsen_factor=coarsen_factor,
        )
        self.time_scale = float(time_scale)
        self.planning_timeout = max(1.0, float(planning_timeout))
        self.robot_radius = float(robot_radius)
        self.robot_radii = {
            str(name): float(radius) for name, radius in (robot_radii or {}).items()
        }
        if self.robot_radius <= 0.0 or any(
            not math.isfinite(radius) or radius <= 0.0
            for radius in self.robot_radii.values()
        ):
            raise ValueError("db-CBS robot radii must be positive")
        self.safety_margin = float(safety_margin)
        self.arrival_tolerance = max(0.01, float(arrival_tolerance))
        self._run_planner = planner
        self.navigator = self
        self.plan: DbcbsPlan | None = None
        self.player: SynchronizedTrajectoryPlayer | None = None
        self.elapsed = 0.0
        self.active: set[str] = set()
        self.paths: dict[str, tuple[tuple[float, float, float], ...]] = {}

    def register_agent(self, *_args, **_kwargs) -> None:
        return None

    def plan_mission(
        self,
        starts: Mapping[str, Sequence[float]],
        goals: Mapping[str, Sequence[float]],
    ) -> dict[str, bool]:
        prepared = self.prepare_mission(starts, goals)
        self.stop()
        if prepared.succeeded:
            self.commit_mission(prepared)
        return dict(prepared.result)

    def prepare_mission(
        self,
        starts: Mapping[str, Sequence[float]],
        goals: Mapping[str, Sequence[float]],
    ) -> PreparedDbcbsMission:
        """Compute a mission without mutating simulation-thread playback state."""

        if not goals:
            return PreparedDbcbsMission({}, None)
        scale = self.frame.scale
        result = {name: False for name in goals}
        agents: list[PlanarAgent] = []
        holders = set(starts).difference(goals)
        for name, goal in goals.items():
            start = starts[name]
            start_plan = self.frame.snap(
                float(start[0]) / scale,
                float(start[1]) / scale,
                max_distance=1.5 / scale,
                prefer_interior=True,
            )
            goal_plan = self.frame.snap(
                float(goal[0]) / scale,
                float(goal[1]) / scale,
                max_distance=1.5 / scale,
                prefer_interior=False,
            )
            if start_plan is None or goal_plan is None:
                continue
            if math.dist(start_plan, goal_plan) * scale <= 1.0:
                result[name] = True
                holders.add(name)
                continue
            agents.append(
                PlanarAgent(
                    name,
                    start_plan,
                    goal_plan,
                    self.effective_radius(name) / scale,
                )
            )

        if not all(result[name] or any(agent.name == name for agent in agents) for name in goals):
            return PreparedDbcbsMission(result, None)
        if not agents:
            return PreparedDbcbsMission({name: True for name in goals}, None)

        obstacle_dicts = [box.as_dict() for box in self.frame.environment.boxes]
        for name in holders:
            start = starts[name]
            holder_size = 2.0 * self.effective_radius(name) / scale
            obstacle_dicts.append(
                {
                    "type": "box",
                    "center": [float(start[0]) / scale, float(start[1]) / scale],
                    "size": [holder_size, holder_size],
                }
            )

        shortest = min(math.dist(agent.start, agent.goal) for agent in agents)
        delta = min(0.5, max(0.08, shortest * 0.45))
        plan = self._run_planner(
            agents=agents,
            workspace_min=self.frame.environment.min,
            workspace_max=self.frame.environment.max,
            obstacles=obstacle_dicts,
            timeout=self.planning_timeout,
            config={"delta_0": round(delta, 4)},
        )
        result = {name: result[name] or name in plan.trajectories for name in goals}
        return PreparedDbcbsMission(result, plan)

    def commit_mission(self, prepared: PreparedDbcbsMission) -> None:
        """Install a successfully prepared mission on the simulation thread."""

        if not prepared.succeeded:
            raise ValueError("Cannot commit an incomplete db-CBS mission")
        self.stop()
        if prepared.plan is None:
            return
        plan = prepared.plan
        self.plan = plan
        self.player = SynchronizedTrajectoryPlayer(
            plan, time_scale=self.time_scale, world_scale=self.frame.scale
        )
        self.elapsed = 0.0
        self.active = set(plan.trajectories)
        self.paths = {
            name: tuple(
                (
                    sample.x * self.frame.scale,
                    sample.y * self.frame.scale,
                    0.0,
                )
                for sample in samples
            )
            for name, samples in plan.trajectories.items()
        }

    def replan_mission(
        self,
        starts: Mapping[str, Sequence[float]],
        goals: Mapping[str, Sequence[float]],
    ) -> dict[str, bool]:
        """Replace the active plan only when every requested goal replans."""

        prepared = self.prepare_mission(starts, goals)
        if prepared.succeeded:
            self.commit_mission(prepared)
        return dict(prepared.result)

    def body_radius(self, name: str) -> float:
        return self.robot_radii.get(name, self.robot_radius)

    def effective_radius(self, name: str) -> float:
        return self.body_radius(name) + self.safety_margin

    def required_separation(self, left: str, right: str) -> float:
        return self.effective_radius(left) + self.effective_radius(right)

    def unsafe_pairs(
        self, positions: Mapping[str, Sequence[float]]
    ) -> tuple[tuple[str, str, float, float], ...]:
        return self.proximity_pairs(positions)

    def proximity_pairs(
        self,
        positions: Mapping[str, Sequence[float]],
        *,
        extra_clearance: float = 0.0,
    ) -> tuple[tuple[str, str, float, float], ...]:
        if not math.isfinite(extra_clearance) or extra_clearance < 0.0:
            raise ValueError("db-CBS extra clearance must be non-negative")
        names = tuple(positions)
        unsafe = []
        for left_index, left in enumerate(names[:-1]):
            for right in names[left_index + 1 :]:
                distance = math.hypot(
                    float(positions[left][0]) - float(positions[right][0]),
                    float(positions[left][1]) - float(positions[right][1]),
                )
                required = self.required_separation(left, right) + extra_clearance
                if distance < required:
                    unsafe.append((left, right, distance, required))
        return tuple(unsafe)

    def target(self, name: str) -> PlanarTarget | None:
        if name not in self.active or self.player is None:
            return None
        return self.player.target(name, self.elapsed)

    def advance(
        self, dt: float, positions: Mapping[str, Sequence[float]]
    ) -> None:
        if self.player is None:
            return
        self.elapsed += max(0.0, float(dt))
        if not self.player.finished(self.elapsed):
            return
        for name in tuple(self.active):
            target = self.player.target(name, self.elapsed)
            position = positions.get(name)
            if position is not None and math.hypot(
                float(position[0]) - target.x, float(position[1]) - target.y
            ) <= self.arrival_tolerance:
                self.active.discard(name)

    def stop(self) -> None:
        self.active.clear()
        self.player = None
        self.plan = None
        self.elapsed = 0.0
        self.paths.clear()

    def set_active(self, name: str, active: bool) -> None:
        if active and name in self.paths:
            self.active.add(name)
        else:
            self.active.discard(name)

    def is_navigating(self, name: str) -> bool:
        return name in self.active

    def get_index(self, name: str) -> int:
        if self.plan is None or name not in self.plan.trajectories:
            return 0
        sample_count = len(self.plan.trajectories[name])
        index = int(self.elapsed / (self.time_scale * self.plan.dt))
        return max(0, min(index, sample_count - 1))

    def get_waypoints(self, name: str) -> list[tuple[float, float, float]]:
        return list(self.paths.get(name, ()))


__all__ = ["DbcbsNavigationSession", "PreparedDbcbsMission"]
