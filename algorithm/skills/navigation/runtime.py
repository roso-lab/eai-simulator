"""Cooperative navigation handle using caller-supplied existing tracking code."""
from __future__ import annotations

import math
from typing import Callable

from algorithm.common_coordination.contract import FailureCode
from algorithm.skills.contracts import SkillPlan
from task.contracts import DependencyOutcome
from task.executors.skills import ExecutionReceipt
from .provider import NavigationProvider


class NavigationHandle:
    """Pure action production plus measured arrival/stopping, never env.step.

    install_path(robot_id, waypoints) and track(robot_id, pose) wrap existing
    navigation sessions. pose() returns measured world x,y,yaw; stopped() must
    inspect actual velocity. make_action converts a tracker command to the
    selected controller tensor. stop_action is an explicit neutral command.
    """

    def __init__(self, plan: SkillPlan, *, install_path: Callable, track: Callable,
                 pose: Callable, stopped: Callable, make_action: Callable,
                 stop_action: Callable, arrival_radius: float, settle_ticks: int = 3) -> None:
        if plan.skill_id != NavigationProvider.skill_id or plan.status != "planned":
            raise ValueError("not an executable navigation plan")
        if len(plan.commands) != 1 or plan.commands[0].command_type != "navigate_waypoints":
            raise ValueError("invalid navigation command")
        if not isinstance(plan.metadata.get('planner_version'),str) or not plan.metadata['planner_version'].strip():
            raise ValueError('navigation plan requires planner provenance')
        if plan.metadata.get('physical_outcome') != 'external_evaluator_required':
            raise ValueError('navigation plan must defer physical verdict to evaluator')
        if isinstance(arrival_radius, bool) or not math.isfinite(arrival_radius) or arrival_radius <= 0:
            raise ValueError("arrival_radius must be finite and positive")
        if type(settle_ticks) is not int or settle_ticks < 1:
            raise ValueError("settle_ticks must be positive")
        if not all(callable(callback) for callback in (install_path, track, pose, stopped, make_action, stop_action)):
            raise TypeError('all navigation runtime bindings must be callable')
        payload = plan.commands[0].payload
        if set(payload) != {'target','waypoints'}:
            raise ValueError("unsupported navigation command fields")
        endpoint_tolerance = plan.metadata.get("endpoint_tolerance_m", 1e-6)
        if isinstance(endpoint_tolerance, bool) or not math.isfinite(endpoint_tolerance) or not 0 <= endpoint_tolerance <= arrival_radius:
            raise ValueError("planner endpoint tolerance must fit within measured arrival radius")
        self.target = tuple(float(v) for v in payload["target"])
        self.waypoints = tuple(tuple(float(v) for v in p) for p in payload["waypoints"])
        if len(self.target) != 2 or not self.waypoints or any(
            len(p) != 2 or not all(math.isfinite(v) for v in p)
            for p in (self.target, *self.waypoints)
        ) or math.dist(self.waypoints[-1], self.target) > endpoint_tolerance:
            raise ValueError("invalid navigation path/target")
        self.plan, self.track, self.pose = plan, track, pose
        self.stopped, self.make_action, self.stop_action = stopped, make_action, stop_action
        self.arrival_radius, self.settle_ticks = arrival_radius, settle_ticks
        self.cancel_reason = None
        self.stable_ticks = 0
        self._receipt = None
        install_path(plan.robot_id, self.waypoints)

    def _pose(self):
        values = tuple(float(v) for v in self.pose())
        if len(values) != 3 or not all(math.isfinite(v) for v in values):
            raise ValueError("measured navigation pose unavailable")
        return values

    def command(self):
        pose = self._pose()
        if self.cancel_reason is not None or self._receipt is not None or math.dist(pose[:2], self.target) <= self.arrival_radius:
            return self.stop_action()
        return self.make_action(self.track(self.plan.robot_id, pose))

    def cancel(self, reason: str):
        self.cancel_reason = str(reason)

    def after_step(self):
        if self._receipt is not None:
            return self._receipt
        arrived = math.dist(self._pose()[:2], self.target) <= self.arrival_radius
        ready = (self.cancel_reason is not None or arrived) and self.stopped() is True
        self.stable_ticks = self.stable_ticks + 1 if ready else 0
        if self.stable_ticks < self.settle_ticks:
            return None
        self._receipt = ExecutionReceipt(self.plan.request_id, self.plan.skill_id, self.plan.robot_id,
            DependencyOutcome.TERMINAL_FAILURE if self.cancel_reason is not None else DependencyOutcome.COMPLETED,
            True, FailureCode.SAFE_STOP if self.cancel_reason is not None else FailureCode.NONE,
            self.cancel_reason or "measured goal reached and stopped")
        return self._receipt
