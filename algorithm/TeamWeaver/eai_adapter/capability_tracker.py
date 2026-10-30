from __future__ import annotations

from dataclasses import dataclass, replace
import math

from TeamWeaver.eai_adapter.task_models import (
    ExecutionFeedback,
    FeedbackOutcome,
    SymbolicWorldState,
)


SUCCESS_DELTA = 0.02
FAILURE_DELTA = -0.15
TIMEOUT_DELTA = -0.10
MIN_RELIABILITY = 0.50
MAX_RELIABILITY = 1.00


@dataclass(frozen=True)
class CapabilityUpdate:
    robot_name: str
    capability: str
    before: float
    after: float
    delta: float


class CapabilityTracker:
    def __init__(self, reliability: dict[str, dict[str, float]]) -> None:
        self._reliability = {
            str(robot_name): {
                str(capability): _clamp(float(value))
                for capability, value in values.items()
            }
            for robot_name, values in reliability.items()
        }

    @classmethod
    def from_world(cls, world: SymbolicWorldState) -> "CapabilityTracker":
        return cls(
            {
                robot.name: {
                    capability: float(robot.reliability.get(capability, 1.0))
                    for capability in robot.base_capabilities
                }
                for robot in world.robots
            }
        )

    def reliability(self, robot_name: str, capability: str) -> float:
        try:
            robot = self._reliability[robot_name]
        except KeyError as exc:
            raise KeyError(f"unknown robot: {robot_name}") from exc
        try:
            return robot[capability]
        except KeyError as exc:
            raise KeyError(
                f"unknown capability for {robot_name}: {capability}"
            ) from exc

    def apply(self, feedback: ExecutionFeedback) -> tuple[CapabilityUpdate, ...]:
        if feedback.robot_name not in self._reliability:
            raise KeyError(f"unknown robot: {feedback.robot_name}")
        if feedback.outcome is FeedbackOutcome.CANCELLED:
            return ()
        delta = _delta_for(feedback.outcome)
        updates: list[CapabilityUpdate] = []
        seen: set[str] = set()
        for capability in feedback.relevant_capabilities:
            if capability in seen:
                continue
            seen.add(capability)
            before = self.reliability(feedback.robot_name, capability)
            after = _clamp(before + delta)
            self._reliability[feedback.robot_name][capability] = after
            updates.append(
                CapabilityUpdate(
                    robot_name=feedback.robot_name,
                    capability=capability,
                    before=before,
                    after=after,
                    delta=round(after - before, 12),
                )
            )
        return tuple(updates)

    def overlay(self, world: SymbolicWorldState) -> SymbolicWorldState:
        robots = []
        for robot in world.robots:
            reliability = self._reliability.get(robot.name)
            robots.append(
                robot
                if reliability is None
                else replace(robot, reliability=dict(reliability))
            )
        return replace(world, robots=tuple(robots))


def _delta_for(outcome: FeedbackOutcome) -> float:
    if outcome is FeedbackOutcome.SUCCEEDED:
        return SUCCESS_DELTA
    if outcome is FeedbackOutcome.TIMED_OUT:
        return TIMEOUT_DELTA
    if outcome is FeedbackOutcome.FAILED:
        return FAILURE_DELTA
    return 0.0


def _clamp(value: float) -> float:
    if not math.isfinite(value):
        raise ValueError("reliability must be finite")
    return min(MAX_RELIABILITY, max(MIN_RELIABILITY, value))
