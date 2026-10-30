"""Synchronized interpolation for planar db-CBS trajectories."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Mapping, Sequence

from .planner import DbcbsPlan, PlanSample


@dataclass(frozen=True)
class PlanarTarget:
    x: float
    y: float
    vx: float
    vy: float
    yaw: float


class SynchronizedTrajectoryPlayer:
    """Interpolate all trajectories on one time-dilated playback clock."""

    def __init__(
        self,
        plan: DbcbsPlan,
        *,
        time_scale: float = 1.0,
        world_scale: float = 1.0,
        world_offset: tuple[float, float] = (0.0, 0.0),
    ) -> None:
        if time_scale <= 0.0:
            raise ValueError("time_scale must be positive.")
        if world_scale <= 0.0:
            raise ValueError("world_scale must be positive.")
        self.plan = plan
        self.time_scale = float(time_scale)
        self.world_scale = float(world_scale)
        self.world_offset = (float(world_offset[0]), float(world_offset[1]))

    @property
    def playback_duration(self) -> float:
        return self.plan.duration * self.time_scale

    def target(self, agent_name: str, elapsed: float) -> PlanarTarget:
        samples = self.plan.trajectories[agent_name]
        planner_time = min(
            max(float(elapsed) / self.time_scale, 0.0), samples[-1].t
        )
        sample, next_sample, fraction = _sample_interval(samples, planner_time)
        x = sample.x + (next_sample.x - sample.x) * fraction
        y = sample.y + (next_sample.y - sample.y) * fraction
        vx = sample.vx + (next_sample.vx - sample.vx) * fraction
        vy = sample.vy + (next_sample.vy - sample.vy) * fraction
        yaw = _target_yaw(samples, planner_time, vx, vy)
        velocity_scale = self.world_scale / self.time_scale
        return PlanarTarget(
            x=x * self.world_scale + self.world_offset[0],
            y=y * self.world_scale + self.world_offset[1],
            vx=vx * velocity_scale,
            vy=vy * velocity_scale,
            yaw=yaw,
        )

    def finished(self, elapsed: float) -> bool:
        return float(elapsed) >= self.playback_duration


def _sample_interval(
    samples: tuple[PlanSample, ...], planner_time: float
) -> tuple[PlanSample, PlanSample, float]:
    if len(samples) == 1 or planner_time <= samples[0].t:
        return samples[0], samples[min(1, len(samples) - 1)], 0.0
    for index in range(1, len(samples)):
        if planner_time <= samples[index].t:
            previous = samples[index - 1]
            following = samples[index]
            duration = following.t - previous.t
            fraction = (
                0.0 if duration <= 0.0 else (planner_time - previous.t) / duration
            )
            return previous, following, fraction
    return samples[-1], samples[-1], 0.0


def _target_yaw(
    samples: tuple[PlanSample, ...], planner_time: float, vx: float, vy: float
) -> float:
    if math.hypot(vx, vy) > 1.0e-5:
        return math.atan2(vy, vx)

    best_distance = math.inf
    best_yaw = 0.0
    for left, right in zip(samples, samples[1:]):
        dx = right.x - left.x
        dy = right.y - left.y
        if math.hypot(dx, dy) <= 1.0e-8:
            continue
        distance = abs(0.5 * (left.t + right.t) - planner_time)
        if distance < best_distance:
            best_distance = distance
            best_yaw = math.atan2(dy, dx)
    return best_yaw


def minimum_pairwise_distance(
    positions: Mapping[str, Sequence[float]],
) -> float:
    names = tuple(positions)
    if len(names) < 2:
        return math.inf
    result = math.inf
    for left_index, left_name in enumerate(names[:-1]):
        for right_name in names[left_index + 1 :]:
            result = min(
                result,
                math.dist(positions[left_name], positions[right_name]),
            )
    return result
