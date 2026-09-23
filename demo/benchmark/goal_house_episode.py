"""Formal House goal episode entry, copied from the City driver shape.

The witness plan is the episode plan. Skills that do not yet have a House
physical adapter fail with a factual receipt after the session has stepped;
they are not reported as business success. ``scoreable`` stays false.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys
from typing import Any, Mapping

from .goal_driven_contract import validate_goal_plan
from .goal_driven_tasks import build_goal_task_package
from .goal_episode_runner import FormalGoalEpisodeRunner


HOUSE_EPISODE_PLAN_ID = "house_native_goal_episode_v1"
HOUSE_BACKEND_ID = "house_native_goal_backend"
_PENDING_REASON = "house_native_skill_adapter_pending"
INDOOR_EPISODE_SCENES = ("factory", "warehouse_nvd", "modern_house_nvd")
_APPROACH_GATE_M = 1.25


def _yaw(state: list[float]) -> float:
    import math
    w, qx, qy, qz = state[3:7]
    return math.atan2(2 * (w * qz + qx * qy), 1 - 2 * (qy * qy + qz * qz))


def _root_state(session: Any, runtime_id: str) -> list[float]:
    return session.base_env.scene.articulations[runtime_id].data.root_state_w[0].detach().cpu().tolist()


_GRID_CACHE: dict[tuple[str, float], tuple[Any, Any]] = {}
_GROUND_SPEED_MPS = 0.55


def _seek_command(state: list[float], target_xy: tuple[float, float],
                  speed_limit: float, stop_radius: float, yaw_limit: float = 1.0,
                  *, creep: bool = False) -> list[float]:
    """Body-frame seek used by the measured M20 probe.

    Lateral velocity is ignored. A large heading error is yaw-only until the
    body faces the waypoint; a target directly behind is reverse, which the
    wheel gait does track. ``creep`` adds a small forward command when yaw
    alone has not changed the heading.
    """
    import math
    dx, dy = float(target_xy[0]) - state[0], float(target_xy[1]) - state[1]
    distance = math.hypot(dx, dy)
    if distance <= stop_radius:
        return [0.0, 0.0, 0.0]
    heading = math.atan2(dy, dx)
    error = math.atan2(math.sin(heading - _yaw(state)), math.cos(heading - _yaw(state)))
    if abs(error) > 2.2:
        return [-speed_limit, 0.0, max(-0.1, min(0.1, error))]
    # Keep turning until the body is within about 15 degrees. A simultaneous
    # forward command of similar size stalls the policy against the wall.
    if abs(error) > 0.25 and not creep:
        return [0.0, 0.0, math.copysign(yaw_limit, error)]
    return [speed_limit, 0.0, max(-0.1, min(0.1, error))]


def occupancy_waypoints(scene_key: str, start_xy: tuple[float, float],
                        goal_xy: tuple[float, float], radius: float = 0.25
                        ) -> list[tuple[float, float]] | None:
    """A* on the scene occupancy map. Straight lines cross indoor walls."""
    key = (scene_key, radius)
    if key not in _GRID_CACHE:
        from EAI.scenario_spec.geometry import provider_map
        from EAI.scenario_spec.oracle import InflatedGrid
        occupancy = provider_map(scene_key=scene_key)
        _GRID_CACHE[key] = (occupancy, InflatedGrid(occupancy, radius))
    occupancy, grid = _GRID_CACHE[key]
    from demo.benchmark.goal_indoor_deployment import _world_path
    start_cell = grid.nearest_passable(start_xy)
    goal_cell = grid.nearest_passable(goal_xy)
    if start_cell is None or goal_cell is None:
        return None

    def _world(cell: tuple[int, int]) -> tuple[float, float]:
        ix, iy = cell
        point = occupancy.world((ix + 0.5) * occupancy.resolution,
                                (iy + 0.5) * occupancy.resolution)
        return float(point[0]), float(point[1])

    cells = grid.path(_world(start_cell), _world(goal_cell))
    if not cells:
        return None
    return [(float(point[0]), float(point[1])) for point in _world_path(occupancy, cells)]


def _region_geometry(deployment: Mapping[str, Any] | None,
                     region_id: str) -> tuple[tuple[float, float] | None, list[float] | None]:
    if deployment is None:
        return None, None
    for row in deployment.get("region_bindings") or []:
        if row.get("id") != region_id:
            continue
        geometry = row.get("candidate_geometry") or {}
        center = geometry.get("center_m")
        point = None
        if isinstance(center, (list, tuple)) and len(center) >= 2:
            point = (float(center[0]), float(center[1]))
        raw_bounds = geometry.get("aabb_xy_m")
        bounds = None
        if isinstance(raw_bounds, (list, tuple)) and len(raw_bounds) == 4:
            bounds = [float(value) for value in raw_bounds]
        return point, bounds
    return None, None


def _inside_region(xy: tuple[float, float], center: tuple[float, float],
                   bounds: list[float] | None, skill: str) -> bool:
    import math
    if bounds is not None:
        # A carried prop often fills a 1 m cell. The root can be in the region
        # while remaining a body-radius outside the published box.
        margin = 0.4
        return (bounds[0] - margin <= xy[0] <= bounds[2] + margin
                and bounds[1] - margin <= xy[1] <= bounds[3] + margin)
    limit = 2.0 if skill == "aerial_navigation" else 1.2
    return math.hypot(xy[0] - center[0], xy[1] - center[1]) <= limit


def _action(identifier: str, kind: str, payload: Mapping[str, Any],
            submitted_at_s: float) -> dict[str, Any]:
    return {"schema": "eai.benchmark.goal_action", "version": 1,
            "action_id": identifier, "submitted_at_s": float(submitted_at_s),
            "type": kind, "payload": deepcopy(dict(payload))}


def build_house_episode_plan(package: Mapping[str, Any]) -> dict[str, Any]:
    """Use the first authored witness as the serial native plan."""
    witnesses = package["hidden_evaluator"]["witness_plans"]
    if not witnesses:
        raise ValueError("house package has no witness plan")
    plan = deepcopy(witnesses[0]["plan"])
    plan["plan_id"] = HOUSE_EPISODE_PLAN_ID
    validate_goal_plan(plan, task_spec=package["public_task"])
    return plan


def house_runtime_state(public_task: Mapping[str, Any],
                        plan: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """A legal initial snapshot. Each robot starts where its first route begins."""
    regions = public_task["public_world"]["regions"]
    aerial = next(row["id"] for row in regions if "aerial_navigation" in row["access_modes"])
    ground = next(row["id"] for row in regions
                  if "ground_navigation" in row["access_modes"] or "stair_traversal" in row["access_modes"])
    robots = {robot["id"]: aerial if robot["mobility"] == "aerial" else ground
              for robot in public_task["candidate_robot_pool"]}
    if plan is not None:
        routes = {row["id"]: row for row in public_task["public_world"].get("routes") or []}
        seen: set[str] = set()
        for task in plan.get("tasks") or []:
            arguments = task.get("arguments") or {}
            route_ids = arguments.get("route_ids") or []
            origin = None
            if route_ids and route_ids[0] in routes:
                origin = routes[route_ids[0]].get("from_region_id")
            elif isinstance(arguments.get("region_id"), str):
                origin = arguments["region_id"]
            if not isinstance(origin, str):
                continue
            for agent in task.get("assigned_agents") or []:
                if agent in robots and agent not in seen:
                    robots[agent] = origin
                    seen.add(agent)
    objects = {}
    for item in public_task["public_world"]["objects"]:
        initial = (item.get("physical_properties") or {}).get("initial_region_id")
        objects[item["id"]] = {"region_id": initial if isinstance(initial, str) else ground}
    return {"robot_regions": robots, "object_states": objects}


class HouseWitnessParticipant:
    """Dispatch the House witness plan one skill at a time."""

    def __init__(self, plan: Mapping[str, Any]):
        self._plan = deepcopy(dict(plan))
        self._opened = False
        self._dispatched = 0
        self._terminal: set[str] = set()

    def manifest(self) -> Mapping[str, Any]:
        return {"kind": "participant", "id": "house-native-goal-witness", "version": 1,
                "interface": "eai.benchmark.goal_algorithm_adapter", "interface_version": 1,
                "evaluation_track": "goal_driven",
                "input_scope": "participant_visible_events_only",
                "control_scope": "benchmark_managed_low_level_control"}

    def initialize(self, public_task: Mapping[str, Any],
                   participant_api: Mapping[str, Any]) -> None:
        del public_task, participant_api

    def act(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not self._opened:
            if not any(item["type"] == "episode_started" for item in events):
                return []
            self._opened = True
            return [_action("house_witness_select", "select_team",
                            {"agent_ids": list(self._plan["assigned_team"])}, 0.0),
                    _action("house_witness_plan", "submit_plan",
                            {"plan": deepcopy(self._plan)}, 0.0)]
        for item in events:
            if item["type"] == "action_result" and item["payload"].get("status") in {
                    "succeeded", "failed", "cancelled"}:
                self._terminal.add(item["payload"]["action_id"])
        tasks = self._plan["tasks"]
        if self._dispatched >= len(tasks):
            return []
        previous = f"house_witness_dispatch_{self._dispatched - 1}"
        if self._dispatched and previous not in self._terminal:
            return []
        index = self._dispatched
        self._dispatched += 1
        now = max((item["time_s"] for item in events), default=0.0)
        return [_action(f"house_witness_dispatch_{index}", "dispatch_skill",
                        {"task_id": tasks[index]["task_id"], "evidence_refs": []}, now)]

    def close(self) -> None:
        pass


class HouseNativeGoalBackend:
    """Step an open House session and record an honest skill receipt.

    ``session is None`` is the offline contract test: time advances by the
    declared duration and every skill fails closed as adapter-pending.
    A live session steps physics, reads goal objects for ``observe``, and
    still fails skills whose motion adapter is not bound.
    """

    def __init__(self, public_task: Mapping[str, Any], *, session: Any = None,
                 deployment: Mapping[str, Any] | None = None,
                 plan: Mapping[str, Any] | None = None):
        self.public_task = public_task
        self.session = session
        self.deployment = deployment
        self.plan = plan
        self.time_s = 0.0
        self.evidence: list[dict[str, Any]] = []
        # Per-task host diagnostics are execution evidence only. They never
        # alter the witness plan, release gates, or scoreability.
        self._last_execution_diagnostics: dict[str, Any] = {}
        self._last_navigation_diagnostics: dict[str, Any] = {}
        initial = house_runtime_state(public_task, plan)
        self.regions = initial["robot_regions"]
        self.objects = initial["object_states"]
        self.holds: dict[str, dict[str, Any]] = {}

    def execute(self, task: Mapping[str, Any], *, episode_id: str,
                trusted_report: Mapping[str, Any]) -> Mapping[str, Any]:
        ledger = trusted_report["session"]
        row = ledger["tasks"][task["task_id"]]
        if (ledger["episode_id"] != episode_id or row["status"] != "pending"
                or row["task"] != task or row["plan_id"] != ledger["current_plan_id"]):
            raise ValueError("native house task does not match the pending GoalSession dispatch")
        duration = float(task["execution"]["duration_s"])
        window_end = float(task["execution"]["start_s"]) + duration
        observed = None
        if self.session is None:
            self.time_s = max(self.time_s, window_end)
        else:
            import torch
            dt = float(self.session.base_env.step_dt)
            steps = max(1, min(30, int(duration / dt) if dt else 1))
            agents = list(self.session.possible_agents)
            for _ in range(steps):
                actions = {agent: torch.zeros((1, 3), device=self.session.device) for agent in agents}
                self.session.env.step(actions)
            # The ledger stays on the plan window. Driving can take longer than
            # the nominal duration; folding that sim time into the receipt puts
            # the next skill outside its own half-open window.
            self.time_s = max(self.time_s, window_end)
            if self.deployment is not None and task["skill"] == "observe":
                from demo.benchmark.goal_native_objects import read_goal_objects
                import omni.usd
                stage = omni.usd.get_context().get_stage()
                observed = read_goal_objects(stage, self.session.base_env.scene, self.deployment)
        self._last_execution_diagnostics = {}
        self._last_navigation_diagnostics = {}
        status, reason = self._skill_result(task, observed)
        self.evidence.append({
            "task_id": task["task_id"], "skill": task["skill"], "status": status,
            "reason": reason, "time_s": self.time_s,
            "execution_diagnostics": deepcopy(self._last_execution_diagnostics),
        })
        print(f"house_skill {task['task_id']} {status} {reason or ''}", flush=True)
        receipt = {
            "backend_id": HOUSE_BACKEND_ID,
            "receipt_ref": f"{HOUSE_BACKEND_ID}:{episode_id}:{task['task_id']}",
            "task_id": task["task_id"],
            "action_id": row["dispatch_action_id"],
            "status": status,
            "completed_at_s": self.time_s,
            "episode_id": episode_id,
            "evidence_refs": [f"house:{task['task_id']}:{status}"],
        }
        if status == "succeeded":
            self._advance_symbolic_state(task)
        return {"status": status, "completed_at_s": self.time_s, "receipt": receipt,
                "runtime_state": {"robot_regions": dict(self.regions),
                                  "object_states": deepcopy(self.objects)}}

    def _skill_result(self, task: Mapping[str, Any], observed: Any) -> tuple[str, str | None]:
        skill = task["skill"]
        if self.session is None:
            if skill == "observe":
                return "succeeded", None
            return "failed", _PENDING_REASON
        if skill == "observe":
            return ("succeeded", None) if observed else ("failed", "observe_objects_unreadable")
        if skill in {"ground_navigation", "aerial_navigation", "stair_traversal"}:
            return self._drive_to_region(task)
        if skill in {"pick", "place", "handoff", "cooperative_carry"}:
            return self._manipulate(task)
        return "failed", _PENDING_REASON

    def _advance_symbolic_state(self, task: Mapping[str, Any]) -> None:
        """Move the ledger to the region this successful skill actually ends in."""
        skill = task["skill"]
        arguments = task["arguments"]
        destination_field = {
            "ground_navigation": "region_id",
            "aerial_navigation": "region_id",
            "stair_traversal": "exit_region_id",
            "place": "placement_region_id",
            "handoff": "receiving_region_id",
            "cooperative_carry": "destination_region_id",
        }.get(skill)
        destination = arguments.get(destination_field) if destination_field else None
        if isinstance(destination, str):
            for agent in task["assigned_agents"]:
                if agent in self.regions:
                    self.regions[agent] = destination
        object_id = arguments.get("object_id")
        if not isinstance(object_id, str) or object_id not in self.objects:
            return
        if skill == "pick":
            pickup = arguments.get("pickup_region_id")
            if isinstance(pickup, str):
                for agent in task["assigned_agents"]:
                    if agent in self.regions:
                        self.regions[agent] = pickup
                self.objects[object_id] = {
                    "region_id": pickup,
                    "handling_mode_id": arguments["handling_mode_id"],
                    "carrier_bindings": [dict(row) for row in arguments["carrier_bindings"]],
                }
        elif skill in {"place", "handoff", "cooperative_carry"} and isinstance(destination, str):
            self.objects[object_id] = {"region_id": destination}

    def _drive_to_region(self, task: Mapping[str, Any]) -> tuple[str, str | None]:
        arguments = task["arguments"]
        region_id = arguments.get("exit_region_id") or arguments.get("region_id")
        if not isinstance(region_id, str):
            return "failed", "navigation_region_missing"
        target, bounds = _region_geometry(self.deployment, region_id)
        if target is None:
            return "failed", f"navigation_region_unbound:{region_id}"
        public_ids = list(task["assigned_agents"])
        runtime_ids = [self._runtime_id(agent_id) for agent_id in public_ids]
        if any(item is None for item in runtime_ids):
            return "failed", "navigation_runtime_id_missing"
        if task["skill"] == "aerial_navigation":
            return self._drive_aerial(runtime_ids, target, task["task_id"])
        # A 1 m region is smaller than the old 1.2 m center radius, so arrival
        # is the published box. Waypoint tolerance only advances the path.
        def arrived(states: Mapping[str, list[float]]) -> bool:
            return all(_inside_region((state[0], state[1]), target, bounds, "ground_navigation")
                       for state in states.values())
        return self._follow_ground(
            runtime_ids, {runtime_id: target for runtime_id in runtime_ids},
            arrived=arrived, label=task["task_id"])

    def _manipulate(self, task: Mapping[str, Any]) -> tuple[str, str | None]:
        import omni.usd
        from demo.benchmark.goal_attachment_probe import LiveBodies
        from demo.benchmark.goal_native_attachment import (
            commit_attachment, prepare_attachment)
        arguments = task["arguments"]
        bindings = list(arguments.get("carrier_bindings") or [])
        carriers = {row["agent_id"]: row["point_id"] for row in bindings
                    if isinstance(row, Mapping)}
        object_id = arguments.get("object_id")
        if not isinstance(object_id, str):
            return "failed", "manipulation_arguments_incomplete"
        self._last_execution_diagnostics = {
            "object_id": object_id,
            "failure_stage": None,
            "approach_gate_m": _APPROACH_GATE_M,
            "approach_gate_passed": False,
            "reach_check_entered": False,
        }
        stage = omni.usd.get_context().get_stage()
        skill = task["skill"]
        if skill in {"place", "handoff"}:
            receipt = self.holds.get(object_id)
            if receipt is None:
                reason = ("place_requires_an_existing_hold" if skill == "place"
                          else "handoff_requires_an_existing_hold")
                return "failed", reason
            destination = (arguments.get("placement_region_id")
                           or arguments.get("receiving_region_id"))
            moved = self._release_hold(stage, object_id, receipt, task, destination)
            return moved
        if not carriers:
            return "failed", "manipulation_arguments_incomplete"
        phase = "body_binding"
        reach_checks: dict[str, list[dict[str, Any]]] = {"prepare_attachment": [], "commit_attachment": []}
        self._last_execution_diagnostics["reach_checks"] = reach_checks
        try:
            bodies = LiveBodies(self.session.base_env.scene, self.deployment)
            phase = "approach_object"
            approached = self._approach_object(object_id, list(carriers), bodies)
            if approached[0] != "succeeded":
                self._last_execution_diagnostics["failure_stage"] = "approach_object"
                return approached
            phase = "prepare_attachment"
            prepared = prepare_attachment(
                stage, self.public_task, object_id=object_id,
                mode_id=arguments.get("handling_mode_id", "single_grip"),
                carriers=carriers, active_member_ids=list(carriers),
                body_bindings=bodies.bindings, pose_provider=bodies,
                reach_diagnostics=reach_checks[phase])
            phase = "commit_attachment"
            receipt = commit_attachment(
                stage, prepared, active_member_ids=list(carriers), pose_provider=bodies,
                reach_diagnostics=reach_checks[phase])
        except Exception as exc:
            detail = str(exc).splitlines()[0][:160]
            self._last_execution_diagnostics["failure_reason_raw"] = str(exc)
            self._last_execution_diagnostics["failure_stage"] = phase
            return "failed", f"manipulation_rejected:{type(exc).__name__}:{detail}"
        finally:
            self._last_execution_diagnostics["reach_check_entered"] = any(reach_checks.values())
            failed_reach_members = [
                check["agent_id"] for checks in reach_checks.values()
                for check in checks if not check["within_reach"]]
            if failed_reach_members:
                self._last_execution_diagnostics["failure_agent_ids"] = list(dict.fromkeys(failed_reach_members))
            for row in self._last_execution_diagnostics.get("carriers", []):
                row["reach_check_entered"] = any(
                    check["agent_id"] == row["agent_id"]
                    for checks in reach_checks.values() for check in checks)
        self._last_execution_diagnostics["failure_stage"] = None
        self._last_execution_diagnostics["attachment_status"] = "committed"
        if skill == "pick":
            self.holds[object_id] = receipt
            return "succeeded", None
        destination = arguments.get("destination_region_id")
        result = self._release_hold(stage, object_id, receipt, task, destination)
        if result[0] != "succeeded":
            self._last_execution_diagnostics["failure_stage"] = "transport_or_release"
        return result

    def _approach_object(self, object_id: str, public_ids: list[str],
                         bodies: Any) -> tuple[str, str | None]:
        """Move each carrier next to the live object before the reach check."""
        import math
        binding = bodies.bindings["objects"].get(object_id)
        if binding is None:
            return "failed", "manipulation_object_unbound"
        pose = bodies(binding["body_path"])
        center = (float(pose["position_m"][0]), float(pose["position_m"][1]))
        diagnostics: dict[str, Any] = {
            "stage": "approach_object",
            "object_id": object_id,
            "object_center_m": [center[0], center[1]],
            "object_center_sample": "approach_start_physx_xy",
            "approach_gate_m": _APPROACH_GATE_M,
            "allow_reverse": False,
            "carriers": [],
            "failure_agent_ids": [],
            "failure_stage": None,
            "reach_check_entered": False,
        }
        # Stop beside the object, on the robot's own side. A goal inside the
        # prop makes the follower reverse away, which is not a collaboration result.
        targets = {}
        carrier_rows: dict[str, dict[str, Any]] = {}
        pool = {row["id"]: row for row in self.public_task.get("candidate_robot_pool", [])}
        for public_id in public_ids:
            runtime_id = self._runtime_id(public_id)
            if runtime_id is None:
                return "failed", "navigation_runtime_id_missing"
            state = _root_state(self.session, runtime_id)
            dx, dy = center[0] - state[0], center[1] - state[1]
            distance = math.hypot(dx, dy) or 1.0
            hold = min(0.75, max(0.0, distance - 0.75))
            targets[runtime_id] = (center[0] - dx / distance * hold,
                                   center[1] - dy / distance * hold)
            carrier_rows[runtime_id] = {
                "agent_id": public_id,
                "runtime_id": runtime_id,
                "start_root_pose_m": list(state[:3]),
                "reach_m": pool.get(public_id, {}).get("physical_profile", {}).get("reach_m"),
                "reach_check_entered": False,
                "approach_target_m": [targets[runtime_id][0], targets[runtime_id][1]],
            }
        diagnostics["carriers"] = [carrier_rows[runtime_id] for runtime_id in targets]
        def arrived(states: Mapping[str, list[float]]) -> bool:
            # 1.25 m is the hand-off to the reach check (declared reach is 0.8 m).
            # Closer than that is contact physics, not a collaboration decision.
            return all(math.hypot(state[0] - center[0], state[1] - center[1]) <= _APPROACH_GATE_M
                       for state in states.values())
        # Preserve the object-approach policy: disable the stall sign flip.
        # _seek_command can still request reverse for a waypoint behind the
        # robot; diagnostics distinguish that from the stall recovery flip.
        followed = self._follow_ground(
            list(targets), targets, arrived=arrived, label=object_id, allow_reverse=False)
        navigation = self._last_navigation_diagnostics
        # The follower uses the initial center. A dynamic prop can move during
        # approach; report its current center separately without changing that
        # existing arrival predicate or substituting it for a reach check.
        final_object_pose = bodies(binding["body_path"])
        final_center = list(map(float, final_object_pose["position_m"][:2]))
        diagnostics["final_object_center_m"] = final_center
        diagnostics["object_center_displacement_xy_m"] = math.dist(center, final_center)
        failed_agents: list[str] = []
        for row in diagnostics["carriers"]:
            runtime_id = row["runtime_id"]
            final = (navigation.get("agents", {}).get(runtime_id, {}).get("final_root_pose_m")
                     or row["start_root_pose_m"])
            row["final_root_pose_m"] = list(final)
            row["distance_to_approach_target_m"] = math.dist(
                final[:2], row["approach_target_m"])
            row["distance_to_gate_center_m"] = math.dist(final[:2], center)
            row["distance_to_object_center_m"] = math.dist(final[:2], final_center)
            row["within_approach_gate"] = row["distance_to_gate_center_m"] <= _APPROACH_GATE_M
            row["within_live_center_radius"] = row["distance_to_object_center_m"] <= _APPROACH_GATE_M
            row["reverse_applied"] = bool(navigation.get("agents", {}).get(runtime_id, {}).get(
                "reverse_applied", False))
            row["stall_sign_flip_applied"] = bool(navigation.get("agents", {}).get(runtime_id, {}).get(
                "stall_sign_flip_applied", False))
            if not row["within_approach_gate"]:
                failed_agents.append(row["agent_id"])
        diagnostics["failure_agent_ids"] = failed_agents
        diagnostics["all_carriers_within_gate_at_final_sample"] = not failed_agents
        diagnostics["approach_gate_passed"] = followed[0] == "succeeded"
        diagnostics["navigation"] = deepcopy(navigation)
        if followed[0] != "succeeded":
            diagnostics["failure_stage"] = "approach_object"
        self._last_execution_diagnostics.update(diagnostics)
        if followed[0] != "succeeded":
            reason = followed[1] or ""
            if reason.startswith("navigation_target_not_reached"):
                distances = [row["distance_to_approach_target_m"]
                             for row in diagnostics["carriers"]]
                distance = max(distances, default=0.0)
                return "failed", f"approach_object_not_reached:{object_id}:{distance:.2f}m"
        return followed

    def _drive_aerial(self, runtime_ids: list[str], target: tuple[float, float],
                      label: str) -> tuple[str, str | None]:
        import math
        import torch
        session = self.session
        last_distance = None
        for _ in range(4000):
            states = {runtime_id: _root_state(session, runtime_id) for runtime_id in runtime_ids}
            last_distance = max(math.hypot(state[0] - target[0], state[1] - target[1])
                                for state in states.values())
            if last_distance <= 2.0:
                return "succeeded", None
            actions = {agent: torch.zeros((1, 3), device=session.device)
                       for agent in session.possible_agents}
            for runtime_id, state in states.items():
                goal = torch.tensor([[target[0], target[1], state[2]]],
                                    device=session.device, dtype=torch.float32)
                session.base_env.set_command(runtime_id, "goal_position", goal)
                actions[runtime_id] = goal
            session.env.step(actions)
        return "failed", f"navigation_target_not_reached:{label}:{last_distance:.2f}m"

    def _follow_ground(self, runtime_ids: list[str],
                       goals: Mapping[str, tuple[float, float]], *,
                       arrived: Any, label: str, allow_reverse: bool = True) -> tuple[str, str | None]:
        """Follow an occupancy-map path, then stop on ``arrived``."""
        import math
        import torch
        session = self.session
        scene_key = self.deployment["scene_key"]
        states = {runtime_id: _root_state(session, runtime_id) for runtime_id in runtime_ids}
        paths: dict[str, list[tuple[float, float]]] = {}
        indexes = {runtime_id: 0 for runtime_id in runtime_ids}
        yaw_stall = {runtime_id: 0 for runtime_id in runtime_ids}
        # Existing corridor stall recovery. A stationary forward command does
        # not by itself identify a controller fault versus blocked geometry.
        vx_sign = {runtime_id: 1.0 for runtime_id in runtime_ids}
        stall_anchor = {runtime_id: None for runtime_id in runtime_ids}
        stall_steps = {runtime_id: 0 for runtime_id in runtime_ids}
        self._last_navigation_diagnostics = {
            "label": label,
            "allow_reverse": bool(allow_reverse),
            "agents": {
                runtime_id: {
                    "start_root_pose_m": list(states[runtime_id][:3]),
                    "goal_m": [goals[runtime_id][0], goals[runtime_id][1]],
                    "final_root_pose_m": list(states[runtime_id][:3]),
                    "reverse_applied": False,
                    "stall_sign_flip_applied": False,
                    "negative_vx_command_steps": 0,
                    "stall_steps": 0,
                }
                for runtime_id in runtime_ids
            },
        }
        for runtime_id, state in states.items():
            path = occupancy_waypoints(scene_key, (state[0], state[1]), goals[runtime_id])
            if not path:
                self._last_navigation_diagnostics["failure"] = "path_blocked"
                return "failed", f"navigation_path_blocked:{label}"
            paths[runtime_id] = path
        first = paths[runtime_ids[0]]
        length = sum(math.dist(a, b) for a, b in zip(first, first[1:]))
        print(f"house_path {label} {runtime_ids[0]} waypoints={len(first)} "
              f"length={length:.2f}", flush=True)
        last_distance = None
        for _ in range(7000):
            states = {runtime_id: _root_state(session, runtime_id) for runtime_id in runtime_ids}
            for runtime_id, state in states.items():
                self._last_navigation_diagnostics["agents"][runtime_id]["final_root_pose_m"] = list(state[:3])
                self._last_navigation_diagnostics["agents"][runtime_id]["distance_to_goal_m"] = math.dist(
                    state[:2], goals[runtime_id])
                self._last_navigation_diagnostics["agents"][runtime_id]["final_yaw_rad"] = _yaw(state)
            last_distance = max(math.hypot(state[0] - goals[runtime_id][0],
                                            state[1] - goals[runtime_id][1])
                                for runtime_id, state in states.items())
            if arrived(states):
                self._last_navigation_diagnostics["status"] = "succeeded"
                return "succeeded", None
            actions = {agent: torch.zeros((1, 3), device=session.device)
                       for agent in session.possible_agents}
            for runtime_id, state in states.items():
                path = paths[runtime_id]
                index = indexes[runtime_id]
                while index < len(path) - 1 and math.hypot(
                        state[0] - path[index][0], state[1] - path[index][1]) <= 0.45:
                    index += 1
                indexes[runtime_id] = index
                aim = path[index]
                stop = 0.35 if index == len(path) - 1 else 0.4
                values = _seek_command(
                    state, aim, _GROUND_SPEED_MPS, stop, creep=yaw_stall[runtime_id] >= 2000)
                if values[0] == 0.0 and abs(values[2]) > 0.0:
                    yaw_stall[runtime_id] += 1
                else:
                    yaw_stall[runtime_id] = 0
                if abs(values[0]) > 0.4:
                    anchor = stall_anchor[runtime_id]
                    if anchor is None or math.hypot(state[0] - anchor[0], state[1] - anchor[1]) > 0.08:
                        stall_anchor[runtime_id] = (state[0], state[1])
                        stall_steps[runtime_id] = 0
                    else:
                        stall_steps[runtime_id] += 1
                        if allow_reverse and stall_steps[runtime_id] >= 250:
                            # Existing corridor recovery may flip again after
                            # another stall. Object approach disables this flip.
                            vx_sign[runtime_id] *= -1.0
                            self._last_navigation_diagnostics["agents"][runtime_id]["stall_sign_flip_applied"] = True
                            stall_steps[runtime_id] = 0
                            stall_anchor[runtime_id] = (state[0], state[1])
                            print(f"house_vx_sign {label} {runtime_id} {vx_sign[runtime_id]}",
                                  flush=True)
                self._last_navigation_diagnostics["agents"][runtime_id]["stall_steps"] = stall_steps[runtime_id]
                values = [values[0] * vx_sign[runtime_id], values[1], values[2]]
                agent_diagnostics = self._last_navigation_diagnostics["agents"][runtime_id]
                agent_diagnostics["last_command_base_velocity"] = list(values)
                if values[0] < 0.0:
                    agent_diagnostics["negative_vx_command_steps"] += 1
                    agent_diagnostics["reverse_applied"] = True
                command = torch.tensor(values, dtype=torch.float32, device=session.device)
                session.base_env.set_command(runtime_id, "base_velocity", command.unsqueeze(0))
                actions[runtime_id][0] = command
                if _ % 1000 == 0:
                    print(f"house_pose {label} {runtime_id} "
                          f"xy=({state[0]:.2f},{state[1]:.2f}) yaw={_yaw(state):.2f} "
                          f"aim=({aim[0]:.2f},{aim[1]:.2f}) cmd={values}", flush=True)
            session.env.step(actions)
        self._last_navigation_diagnostics["status"] = "failed"
        self._last_navigation_diagnostics["failure"] = "target_not_reached"
        # The loop's last state precedes its final env.step. Record fresh final
        # poses without changing the legacy timeout decision/reason.
        for runtime_id in runtime_ids:
            state = _root_state(session, runtime_id)
            agent_diagnostics = self._last_navigation_diagnostics["agents"][runtime_id]
            agent_diagnostics["final_root_pose_m"] = list(state[:3])
            agent_diagnostics["final_yaw_rad"] = _yaw(state)
            agent_diagnostics["distance_to_goal_m"] = math.dist(state[:2], goals[runtime_id])
        return "failed", f"navigation_target_not_reached:{label}:{last_distance:.2f}m"

    def _release_hold(self, stage: Any, object_id: str, receipt: Mapping[str, Any],
                      task: Mapping[str, Any], destination: str | None) -> tuple[str, str | None]:
        from demo.benchmark.goal_native_attachment import detach_attachment
        moved: tuple[str, str | None] = ("succeeded", None)
        if isinstance(destination, str):
            moved = self._drive_to_region({
                **task, "skill": "ground_navigation",
                "arguments": {"region_id": destination},
            })
        if moved[0] != "succeeded":
            return moved
        try:
            detach_attachment(stage, receipt)
        except Exception as exc:
            return "failed", f"manipulation_detach_rejected:{type(exc).__name__}"
        self.holds.pop(object_id, None)
        return moved

    def _runtime_id(self, public_id: str) -> str | None:
        if self.deployment is None:
            return None
        for row in self.deployment["robot_instances"]:
            if row["public_id"] == public_id:
                return row["runtime_id"]
        return None


def run_house_episode_offline(output_root: str | Path, *,
                              roster_profile: str = "bound_native_v2",
                              scene_key: str = "modern_house_nvd") -> dict[str, Any]:
    """Run an indoor witness through GoalSession without Isaac."""
    package = build_goal_task_package(
        scene_key, indoor_roster_profile=roster_profile)
    plan = build_house_episode_plan(package)
    public = package["public_task"]
    root = Path(output_root)
    backend = HouseNativeGoalBackend(public, plan=plan)
    runner = FormalGoalEpisodeRunner(
        public, package["participant_api"], HouseWitnessParticipant(plan), backend,
        output_root=root / "goal_episode")
    result = runner.run(initial_state=house_runtime_state(public, plan))
    summary = {
        "episode_id": result["episode_id"],
        "error": result["error"],
        "turns": result["turns"],
        "skill_receipt_count": result["skill_receipt_count"],
        "task_id": public["task_id"],
        "scene_key": scene_key,
        "roster_profile": roster_profile,
        "pending_skills": [row["skill"] for row in backend.evidence if row["status"] != "succeeded"],
        "skill_results": backend.evidence,
        "scoreable": False,
    }
    root.mkdir(parents=True, exist_ok=True)
    (root / "house_episode_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary


def build_house_deployment(roster_profile: str = "bound_native_v2",
                           scene_key: str = "modern_house_nvd"):
    """Compile an indoor deployment, allowing the opt-in seven-robot roster."""
    from .goal_deployment import compile_goal_deployment
    from .goal_indoor_deployment import build_indoor_deployment_overlay
    if scene_key not in INDOOR_EPISODE_SCENES:
        raise ValueError(f"unsupported indoor episode scene: {scene_key}")
    package = build_goal_task_package(
        scene_key, indoor_roster_profile=roster_profile)
    extended = roster_profile != "legacy_v1"
    overlay = build_indoor_deployment_overlay(
        package["public_task"], verify_sources=False,
        allow_extended_roster=extended)
    return package, compile_goal_deployment(package["public_task"], overlay)


def house_selection(deployment: Mapping[str, Any]) -> dict[str, Any]:
    from EAI.hmrs_env.env_diy.catalog import default_controller_cfg
    from EAI.hmrs_env.env_diy.flow import interactive_selection_from_dict
    robots = []
    for row in deployment["robot_instances"]:
        directive = row["spawn_binding"]["deployment_directive"]
        robots.append({
            "type": row["type"],
            "controller": {"mode": "default", "cfg": default_controller_cfg(row["type"])},
            "spawn_pose": {"position": list(directive["position_m"]),
                           "rotation": list(directive["rotation_wxyz"])},
            "attachments": [],
        })
    selection = {"version": 1, "scene_key": deployment["scene_key"],
                 "task_name": f"goal_episode_{deployment['scene_key']}", "robots": robots}
    interactive_selection_from_dict(selection)
    return selection


def prepare_house_episode(output_root: str | Path, *, device: str = "cpu",
                          roster_profile: str = "bound_native_v2",
                          scene_key: str = "modern_house_nvd") -> dict[str, Any]:
    """Write the launch inputs. Isaac starts only in the worker process."""
    package, deployment = build_house_deployment(roster_profile, scene_key=scene_key)
    selection = house_selection(deployment)
    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=False)
    request = {"schema": "eai.benchmark.goal_house_episode_request", "version": 1,
               "device": device, "roster_profile": roster_profile,
               "task_id": package["public_task"]["task_id"]}
    for name, value in (("request.json", request), ("deployment.json", deployment),
                        ("selection.json", selection), ("package.json", package)):
        (root / name).write_text(json.dumps(value, ensure_ascii=False) + "\n", encoding="utf-8")
    return request


def _run_house_worker(root: Path) -> dict[str, Any]:
    """Open Kit in this process. Callers must not have imported pxr already."""
    import torch
    if any(name == "pxr" or name.startswith("pxr.") for name in sys.modules):
        raise RuntimeError("OpenUSD must be loaded by the simulator application, not worker preflight")
    from simulator import SimulatorLaunchConfig, open_simulator_session
    request = json.loads((root / "request.json").read_text(encoding="utf-8"))
    deployment = json.loads((root / "deployment.json").read_text(encoding="utf-8"))
    selection = json.loads((root / "selection.json").read_text(encoding="utf-8"))
    package = json.loads((root / "package.json").read_text(encoding="utf-8"))
    public = package["public_task"]
    plan = build_house_episode_plan(package)

    def configure(env_cfg):
        from demo.benchmark.goal_native_objects import configure_goal_objects
        env_cfg.sim.render_interval = max(1, round(0.1 / env_cfg.sim.dt))
        env_cfg.sim.enable_scene_query_support = True
        env_cfg.episode_length_s = 3600.0
        configure_goal_objects(env_cfg, deployment)

    config = SimulatorLaunchConfig(
        env=selection["task_name"], resolved_env_name="EAI-Interactive-v0",
        selection_data=selection, num_envs=1, device=request["device"], seed=7,
        headless=True, enable_ros_bridge_extension=False, disable_orsus_ros_env=True,
        env_cfg_hook=configure)
    with open_simulator_session(config) as session:
        agents = list(session.possible_agents)
        for _ in range(10):
            actions = {agent: torch.zeros((1, 3), device=session.device) for agent in agents}
            session.env.step(actions)
        backend = HouseNativeGoalBackend(
            public, session=session, deployment=deployment, plan=plan)
        runner = FormalGoalEpisodeRunner(
            public, package["participant_api"], HouseWitnessParticipant(plan), backend,
            output_root=root / "goal_episode")
        result = runner.run(initial_state=house_runtime_state(public, plan))
        summary = {
            "episode_id": result["episode_id"],
            "error": result["error"],
            "turns": result["turns"],
            "skill_receipt_count": result["skill_receipt_count"],
            "task_id": public["task_id"],
            "roster_profile": request["roster_profile"],
            "device": request["device"],
            "possible_agents": agents,
            "pending_skills": [row["skill"] for row in backend.evidence
                               if row["status"] != "succeeded"],
            "skill_results": backend.evidence,
            "scoreable": False,
        }
        (root / "house_episode_summary.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary


def run_house_episode(output_root: str | Path, *, device: str = "cpu",
                      roster_profile: str = "bound_native_v2",
                      scene_key: str = "modern_house_nvd") -> dict[str, Any]:
    """Prepare inputs and run the indoor session in a clean worker process."""
    import subprocess
    root = Path(output_root)
    prepare_house_episode(root, device=device, roster_profile=roster_profile,
                          scene_key=scene_key)
    command = [sys.executable, "-m", "demo.benchmark.goal_house_episode",
               "--worker", "--output-root", str(root)]
    completed = subprocess.run(command, cwd=Path(__file__).resolve().parents[2], check=False)
    summary_path = root / "house_episode_summary.json"
    if completed.returncode != 0 or not summary_path.is_file():
        raise RuntimeError(f"house episode worker failed with code {completed.returncode}")
    return json.loads(summary_path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--scene", choices=INDOOR_EPISODE_SCENES, default="modern_house_nvd")
    parser.add_argument("--roster", choices=("legacy_v1", "bound_native_v2"), default="bound_native_v2")
    parser.add_argument("--device", choices=("cpu", "cuda:0"), default="cpu")
    parser.add_argument("--offline", action="store_true",
                        help="run the GoalSession contract without Isaac")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.worker:
        _run_house_worker(args.output_root)
        return 0
    if args.offline:
        summary = run_house_episode_offline(
            args.output_root, roster_profile=args.roster, scene_key=args.scene)
    else:
        summary = run_house_episode(
            args.output_root, device=args.device, roster_profile=args.roster,
            scene_key=args.scene)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    stopped_on_unbound_skill = "prerequisite" in (summary["error"] or "")
    ran = summary["skill_receipt_count"] >= 1 and (
        summary["error"] is None or stopped_on_unbound_skill)
    return 0 if ran else 1


if __name__ == "__main__":
    raise SystemExit(main())
