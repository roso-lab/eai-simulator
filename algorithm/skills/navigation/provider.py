"""SkillPlan adapter over an injected existing 2D path planner.

The planner/map/profile remain caller-owned. No replacement planning algorithm,
default map or formal task schema is introduced by this adapter.
"""
from __future__ import annotations

import math
from typing import Any, Callable, Mapping

from algorithm.skills.contracts import SkillCommand, SkillKind, SkillPlan, SkillRequest


class NavigationProvider:
    skill_id = "global_2d_navigation"
    kind = SkillKind.NAVIGATION

    def __init__(self, planner: Callable, *, planner_version: str, endpoint_tolerance_m: float = 1e-6) -> None:
        if not isinstance(planner_version, str) or not planner_version.strip():
            raise ValueError("planner_version is required")
        self.planner, self.planner_version = planner, planner_version
        if isinstance(endpoint_tolerance_m, bool) or not math.isfinite(endpoint_tolerance_m) or endpoint_tolerance_m < 0:
            raise ValueError("endpoint tolerance must be finite and non-negative")
        self.endpoint_tolerance_m = float(endpoint_tolerance_m)

    def plan(self, request: SkillRequest, *, observation: Mapping[str, Any] | None = None) -> SkillPlan:
        if request.skill_id != self.skill_id:
            raise ValueError("navigation skill identity mismatch")
        if request.target_pose is None or len(request.target_pose) not in (2, 3):
            raise ValueError("2D navigation requires an explicit world target pose (x,y[,z])")
        if len(request.target_pose) == 3 and request.target_pose[2] != 0:
            raise ValueError("2D navigation cannot execute a nonzero target elevation")
        if request.target_ref is not None or request.parameters:
            raise ValueError("resolve symbolic targets and planner settings before navigation binding")
        if observation is None:
            raise ValueError("navigation requires a current robot observation")
        # WorldSnapshot payload uses the existing RobotState representation.
        robots = [item for item in observation.get("robots", ())
                  if item.get("robot_id") == request.robot_id and item.get("active") is True]
        if len(robots) != 1 or robots[0].get("position") is None:
            raise ValueError("navigation requires one observed active robot pose")
        start = tuple(float(v) for v in robots[0]["position"][:2])
        target = tuple(float(v) for v in request.target_pose[:2])
        if not all(math.isfinite(v) for v in target):
            raise ValueError('navigation target must be finite')
        if len(start) != 2 or not all(math.isfinite(v) for v in start):
            raise ValueError("navigation start pose must be finite")
        raw = self.planner(start, target)
        waypoints = []
        for point in raw:
            # The existing grid planner returns (x,y,heading). This adapter
            # deliberately executes position goals, without terminal heading.
            values = tuple(float(v) for v in point)
            if len(values) not in (2, 3) or not all(math.isfinite(v) for v in values):
                raise ValueError("planner emitted invalid 2D waypoint")
            waypoints.append(values[:2])
        waypoints = tuple(waypoints)
        if not waypoints:
            return SkillPlan(request.request_id, request.skill_id, request.robot_id,
                             status="unreachable", metadata={"planner_version": self.planner_version})
        if any(len(p) != 2 or not all(math.isfinite(v) for v in p) for p in waypoints):
            raise ValueError("planner emitted invalid waypoints")
        if math.dist(waypoints[-1], target) > self.endpoint_tolerance_m:
            raise ValueError("planner path does not end at requested target")
        return SkillPlan(request.request_id, request.skill_id, request.robot_id,
            (SkillCommand("navigate_waypoints", {"waypoints": waypoints, "target": target}),),
            metadata={"planner_version": self.planner_version,
                      "endpoint_tolerance_m": self.endpoint_tolerance_m,
                      "path_endpoint_error_m": math.dist(waypoints[-1], target),
                      "physical_outcome": "external_evaluator_required"})
