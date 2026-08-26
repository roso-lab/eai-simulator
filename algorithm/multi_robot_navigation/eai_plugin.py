"""Reusable EAI multi-robot navigation adapter.

The plugin is called from an EAI simulation loop. It does not create an Isaac
application, ROS process, or launch process. Its default planner is the native
db-CBS implementation included in this package.
"""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
import math
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from algorithm.global_planner.session import GlobalNavSession, StepCommand


AERIAL_TYPE_TOKENS = frozenset({
    "cf2",
    "cf2x",
    "crazyflie",
    "iris",
    "pegasus",
    "pegasusiris",
    "pegasusx4",
    "quadcopter",
})

BUILTIN_ROBOT_RADII = {
    "carter": 0.35,
    "pepper": 0.35,
    "go2": 0.30,
    "b2": 0.50,
    "lite3": 0.38,
    "scout": 0.60,
    "m20": 0.50,
    "g1": 0.40,
    "mushrnanov2": 0.30,
    "cocoairs": 0.50,
}


def _type_token(value: object) -> str:
    return "".join(character for character in str(value).casefold() if character.isalnum())


def navigation_radius_for_type(robot_type: str, fallback: float = 0.60) -> float:
    """Resolve EAI's configured planar bounding radius for a robot type."""

    return BUILTIN_ROBOT_RADII.get(_type_token(robot_type), float(fallback))


def _normalize_angle(value: float) -> float:
    return math.atan2(math.sin(float(value)), math.cos(float(value)))


def _default_controller_normalizer(entry: Any) -> tuple[Any, tuple[Any, ...]]:
    from EAI.controllers.base import normalize_controller_entry

    return normalize_controller_entry(entry)


def _controller_metadata(
    env_cfg: Any,
    agent_name: str,
    normalizer: Callable[[Any], tuple[Any, Sequence[Any]]],
) -> tuple[str, str]:
    controllers = getattr(env_cfg, "controllers", None) or {}
    entry = controllers.get(agent_name)
    if entry is None:
        return agent_name.rsplit("_", 1)[0], ""
    controller_cfg, _ = normalizer(entry)
    robot_type = str(getattr(controller_cfg, "robot_type", "") or "")
    command_name = str(getattr(controller_cfg, "command_name", "") or "")
    return robot_type, command_name


def is_aerial_robot(robot_type: str, agent_name: str = "") -> bool:
    """Return whether an EAI controller/instance identifies an aerial robot."""

    type_token = _type_token(robot_type)
    name_token = _type_token(agent_name.rsplit("_", 1)[0])
    return type_token in AERIAL_TYPE_TOKENS or name_token in AERIAL_TYPE_TOKENS


def get_yaw_from_quaternion(quaternion: Any) -> float:
    """Read a scalar-first quaternion tensor/sequence as planar yaw."""

    w, x, y, z = (float(quaternion[index]) for index in range(4))
    siny_cosp = 2.0 * (w * z + x * y)
    cosy_cosp = 1.0 - 2.0 * (y * y + z * z)
    return math.atan2(siny_cosp, cosy_cosp)


def get_robot_pose_tensors(base_env: Any, name: str) -> tuple[Any, Any]:
    """Return the navigation body pose, with an articulation-root fallback."""

    robot = base_env.scene.articulations[name]
    for body_name in ("base_link", "base_footprint", "chassis", "trunk", "pelvis"):
        try:
            body_ids, _ = robot.find_bodies(body_name, preserve_order=True)
            if body_ids:
                body_id = body_ids[0]
                return robot.data.body_pos_w[0, body_id], robot.data.body_quat_w[0, body_id]
        except (AttributeError, IndexError, RuntimeError, ValueError):
            continue
    return robot.data.root_pos_w[0], robot.data.root_quat_w[0]


def builtin_scene_map(
    scene_key: str,
    *,
    asset_resolver: Any | None = None,
) -> Path:
    """Resolve and ensure an EAI scene's external occupancy-map pair."""

    from EAI_assets.scene_resources import OCCUPANCY_MAP, ensure_scene_resource

    yaml_path, _png_path = ensure_scene_resource(
        scene_key,
        OCCUPANCY_MAP,
        asset_resolver=asset_resolver,
    )
    return yaml_path


@dataclass(frozen=True)
class NavigationPluginState:
    """Serializable UI-facing state for the navigation component."""

    managed_robots: tuple[str, ...]
    excluded_robots: tuple[str, ...]
    selected_robot: str | None
    pending_goals: dict[str, tuple[float, float]]
    navigating_robots: tuple[str, ...]
    safety_stop: tuple[str, str, float, float] | None
    planning: bool
    planning_error: str | None
    replanning: bool
    replan_event: tuple[str, str, float, float] | None
    replan_attempts: int
    replan_error: str | None


class EaiMultiRobotNavigationPlugin:
    """Conflict-aware navigation component for an existing EAI environment.

    The component owns planning and mission state only. The caller remains in
    control of the simulation loop and calls :meth:`compute_actions` before
    ``env.step(actions)``.
    """

    def __init__(
        self,
        base_env: Any,
        possible_agents: Sequence[str],
        env_cfg: Any,
        device: str,
        num_envs: int,
        map_yaml: str | Path,
        *,
        waypoint_step: float = 1.0,
        prefer_astar: bool = True,
        inflation_radius_cells: int | None = None,
        planner_backend: str = "dbcbs",
        dbcbs_time_scale: float = 8.0,
        dbcbs_plan_scale: float = 4.0,
        dbcbs_planning_timeout: float = 60.0,
        dbcbs_robot_radius: float = 0.60,
        dbcbs_robot_radii: Mapping[str, float] | None = None,
        dbcbs_safety_margin: float = 0.10,
        dbcbs_coarsen_factor: int = 4,
        dbcbs_replan_clearance: float = 0.25,
        dbcbs_replan_retry_interval: float = 0.50,
        exclude_aerial: bool = True,
        controller_normalizer: Callable[[Any], tuple[Any, Sequence[Any]]] | None = None,
        torch_module: Any | None = None,
    ) -> None:
        if int(num_envs) != 1:
            raise ValueError("EAI multi-robot navigation currently requires num_envs=1")
        if (
            not math.isfinite(dbcbs_replan_clearance)
            or dbcbs_replan_clearance < 0.0
            or not math.isfinite(dbcbs_replan_retry_interval)
            or dbcbs_replan_retry_interval < 0.0
        ):
            raise ValueError("db-CBS replanning distances and intervals must be non-negative")

        self.base_env = base_env
        self.env_cfg = env_cfg
        self.device = str(device)
        self.num_envs = int(num_envs)
        self.all_agents = list(possible_agents)
        self._normalize_controller = controller_normalizer or _default_controller_normalizer
        self._torch = torch_module

        self.robot_types: dict[str, str] = {}
        self.command_names: dict[str, str] = {}
        managed: list[str] = []
        excluded: list[str] = []
        for agent_name in self.all_agents:
            robot_type, command_name = _controller_metadata(
                env_cfg, agent_name, self._normalize_controller
            )
            self.robot_types[agent_name] = robot_type
            self.command_names[agent_name] = command_name
            if exclude_aerial and is_aerial_robot(robot_type, agent_name):
                excluded.append(agent_name)
            elif agent_name in getattr(base_env.scene, "articulations", {}):
                managed.append(agent_name)

        if not managed:
            raise ValueError("The EAI scene contains no navigable ground robots")
        self.possible_agents = managed
        self.excluded_agents = excluded

        self.goal_agents = {
            name
            for name in managed
            if self.command_names[name] == "goal_position"
            or _type_token(self.robot_types[name]) == "m20nav"
        }
        self.velocity_agents = set(managed).difference(self.goal_agents)
        radius_overrides = dict(dbcbs_robot_radii or {})
        self.robot_radii = {}
        for name in managed:
            robot_type = self.robot_types[name]
            radius = radius_overrides.get(
                name,
                radius_overrides.get(
                    robot_type,
                    navigation_radius_for_type(robot_type, dbcbs_robot_radius),
                ),
            )
            radius = float(radius)
            if not math.isfinite(radius) or radius <= 0.0:
                raise ValueError(f"Invalid navigation radius for {name}: {radius!r}")
            self.robot_radii[name] = radius
        backend = str(planner_backend).strip().casefold()
        if backend == "dbcbs":
            from .session import DbcbsNavigationSession

            self.session = DbcbsNavigationSession(
                map_yaml,
                time_scale=dbcbs_time_scale,
                plan_scale=dbcbs_plan_scale,
                planning_timeout=dbcbs_planning_timeout,
                robot_radius=max(self.robot_radii.values()),
                robot_radii=self.robot_radii,
                safety_margin=dbcbs_safety_margin,
                coarsen_factor=dbcbs_coarsen_factor,
            )
        elif backend == "global":
            self.session = GlobalNavSession(
                str(Path(map_yaml).expanduser().resolve()),
                waypoint_step=waypoint_step,
                prefer_astar=prefer_astar,
                inflation_radius_cells=(
                    int(inflation_radius_cells)
                    if inflation_radius_cells is not None
                    else 12
                ),
            )
        else:
            raise ValueError(
                f"Unknown navigation planner backend {planner_backend!r}; "
                "expected 'dbcbs' or 'global'"
            )
        self.planner_backend = backend
        self._planner_executor = (
            ThreadPoolExecutor(max_workers=1, thread_name_prefix="eai-dbcbs")
            if backend == "dbcbs"
            else None
        )
        self._planning_future: Future[Any] | None = None
        self._planning_mode: str | None = None
        self._planning_goals: dict[str, tuple[float, float]] = {}
        self._planning_pending = False
        self._planning_error: str | None = None
        self._closed = False
        for agent_name in managed:
            self.session.register_agent(
                agent_name, use_goal_position=agent_name in self.goal_agents
            )

        torch = self._torch_module()
        self.robot_commands = {
            name: torch.zeros((self.num_envs, 3), device=self.device)
            for name in managed
        }
        self.selected_robot: str | None = None
        self.pending_goals: dict[str, tuple[float, float]] = {}
        self._mission_agents: set[str] = set()
        self._mission_goals: dict[str, tuple[float, float]] = {}
        self._last_dbcbs_velocity = {
            name: (0.0, 0.0, 0.0) for name in self.velocity_agents
        }
        self._replan_clearance = float(dbcbs_replan_clearance)
        self._replan_retry_interval = float(dbcbs_replan_retry_interval)
        self._control_elapsed = 0.0
        self._replan_not_before = 0.0
        self._replan_armed = True
        self._replan_pending = False
        self._last_replan_event: tuple[str, str, float, float] | None = None
        self._replan_attempts = 0
        self._replan_error: str | None = None
        self.last_safety_stop: tuple[str, str, float, float] | None = None

    @classmethod
    def from_session(
        cls,
        simulator_session: Any,
        map_yaml: str | Path | None = None,
        **kwargs: Any,
    ) -> "EaiMultiRobotNavigationPlugin":
        """Construct the component from ``simulator.open_simulator_session``."""

        resolved_map = map_yaml
        if resolved_map is None:
            selection = simulator_session.selection_data or {}
            scene_key = str(selection.get("scene_key") or "").strip()
            if not scene_key:
                raise ValueError("Cannot auto-select a map: selection_data has no scene_key")
            resolved_map = builtin_scene_map(scene_key)
        return cls(
            simulator_session.base_env,
            simulator_session.possible_agents,
            simulator_session.env_cfg,
            simulator_session.device,
            simulator_session.num_envs,
            resolved_map,
            **kwargs,
        )

    def _torch_module(self) -> Any:
        if self._torch is None:
            import torch

            self._torch = torch
        return self._torch

    def _pose_xy(self, robot_name: str) -> tuple[float, float]:
        position, _ = get_robot_pose_tensors(self.base_env, robot_name)
        return float(position[0].item()), float(position[1].item())

    def select_robot(self, robot_name: str) -> None:
        if robot_name not in self.possible_agents:
            raise ValueError(
                f"Robot {robot_name!r} is not managed; available: {self.possible_agents}"
            )
        self.selected_robot = robot_name

    def set_goal(self, robot_name: str, target_xy: Sequence[float]) -> None:
        self.select_robot(robot_name)
        if len(target_xy) < 2:
            raise ValueError("A navigation goal requires x and y")
        x, y = float(target_xy[0]), float(target_xy[1])
        if not math.isfinite(x) or not math.isfinite(y):
            raise ValueError("Navigation goal coordinates must be finite")
        self.pending_goals[robot_name] = (x, y)

    def set_selected_goal(self, target_xy: Sequence[float]) -> None:
        if self.selected_robot is None:
            raise RuntimeError("Select a robot before assigning a navigation goal")
        self.set_goal(self.selected_robot, target_xy)

    def clear_goal(self, robot_name: str) -> None:
        self.pending_goals.pop(robot_name, None)

    def clear_pending_goals(self) -> None:
        self.pending_goals.clear()

    def _reset_replanning(self) -> None:
        self._replan_pending = False
        self._last_replan_event = None
        self._replan_attempts = 0
        self._replan_error = None
        self._replan_not_before = self._control_elapsed
        self._replan_armed = True

    def _reset_dbcbs_velocities(self) -> None:
        for name in self._last_dbcbs_velocity:
            self._last_dbcbs_velocity[name] = (0.0, 0.0, 0.0)

    def _submit_dbcbs_planning(
        self,
        starts: Mapping[str, Sequence[float]],
        goals: Mapping[str, Sequence[float]],
        *,
        mode: str,
    ) -> None:
        if self._closed:
            raise RuntimeError("The EAI multi-robot navigation component is closed")
        if self._planner_executor is None:
            raise RuntimeError("Asynchronous planning requires the db-CBS backend")
        if self._planning_future is not None:
            raise RuntimeError("A db-CBS planning request is already running")
        start_snapshot = {
            name: (float(position[0]), float(position[1]))
            for name, position in starts.items()
        }
        goal_snapshot = {
            name: (float(position[0]), float(position[1]))
            for name, position in goals.items()
        }
        self._planning_mode = mode
        self._planning_goals = goal_snapshot
        self._planning_future = self._planner_executor.submit(
            self.session.prepare_mission,
            start_snapshot,
            goal_snapshot,
        )

    def _begin_dbcbs_mission(
        self,
        starts: Mapping[str, Sequence[float]],
        goals: Mapping[str, Sequence[float]],
    ) -> dict[str, bool]:
        if self._closed:
            raise RuntimeError("The EAI multi-robot navigation component is closed")
        if self._planning_future is not None:
            raise RuntimeError("A db-CBS planning request is already running")
        self.session.stop()
        self._reset_replanning()
        self._planning_error = None
        self._planning_pending = True
        self._mission_agents = set(goals)
        self._mission_goals = {
            name: (float(goal[0]), float(goal[1])) for name, goal in goals.items()
        }
        self._reset_dbcbs_velocities()
        self._submit_dbcbs_planning(starts, goals, mode="initial")
        return {name: True for name in goals}

    @staticmethod
    def _planning_exception_message(exc: BaseException) -> str:
        detail = str(exc).splitlines()[0].strip()
        return f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__

    def _poll_dbcbs_planning(self) -> bool:
        future = self._planning_future
        if future is None or not future.done():
            return False

        mode = self._planning_mode
        goals = dict(self._planning_goals)
        self._planning_future = None
        self._planning_mode = None
        self._planning_goals.clear()
        try:
            prepared = future.result()
        except Exception as exc:
            error = self._planning_exception_message(exc)
            if mode == "replan":
                self._replan_error = error
                self._replan_not_before = (
                    self._control_elapsed + self._replan_retry_interval
                )
            else:
                self._planning_pending = False
                self._planning_error = error
                self.pending_goals.update(goals)
                self._mission_agents.clear()
                self._mission_goals.clear()
            return True

        if not prepared.succeeded:
            failed = [name for name in goals if not prepared.result.get(name, False)]
            error = "Planning failed: " + ", ".join(failed or goals)
            if mode == "replan":
                self._replan_error = error
                self._replan_not_before = (
                    self._control_elapsed + self._replan_retry_interval
                )
            else:
                self._planning_pending = False
                self._planning_error = error
                self.pending_goals.update(goals)
                self._mission_agents.clear()
                self._mission_goals.clear()
            return True

        self.session.commit_mission(prepared)
        if mode == "replan":
            self._replan_pending = False
            self._replan_error = None
            self._replan_armed = False
        else:
            self._planning_pending = False
            self._planning_error = None
        self.last_safety_stop = None
        self._reset_dbcbs_velocities()
        return True

    def _cancel_dbcbs_planning(self) -> None:
        future = self._planning_future
        if future is not None:
            future.cancel()
        self._planning_future = None
        self._planning_mode = None
        self._planning_goals.clear()
        self._planning_pending = False

    def start_navigation(
        self, *, priorities: Mapping[str, int] | None = None
    ) -> dict[str, bool]:
        """Start pending goals while reserving non-participants.

        For db-CBS, the returned values acknowledge submission to the planner
        worker. Final success or failure is exposed through :meth:`state`.
        """

        if not self.pending_goals:
            raise RuntimeError("Set at least one robot goal before starting navigation")

        requested = dict(self.pending_goals)
        self.last_safety_stop = None
        if self.planner_backend == "dbcbs":
            starts = {name: self._pose_xy(name) for name in self.possible_agents}
            result = self._begin_dbcbs_mission(starts, requested)
            self.pending_goals.clear()
            self.selected_robot = None
            return result
        else:
            self._reset_replanning()
            targets = {
                name: requested.get(name, self._pose_xy(name))
                for name in self.possible_agents
            }
            planned = self.dispatch_batch(targets, priorities=dict(priorities or {}))
            mission_names = set(targets)
        requested_result = {name: bool(planned.get(name)) for name in requested}
        if not requested_result or not all(requested_result.values()):
            for robot_name in mission_names:
                self.session.navigator.set_active(robot_name, False)
            self._mission_agents.clear()
            self._mission_goals.clear()
            return requested_result

        self._mission_agents = set(requested)
        self._mission_goals = dict(requested)
        self._reset_dbcbs_velocities()
        for holder in set(self.possible_agents).difference(self._mission_agents):
            self.session.navigator.set_active(holder, False)
        self.pending_goals.clear()
        self.selected_robot = None
        return requested_result

    def stop_navigation(self, *, clear_pending: bool = False) -> None:
        self._cancel_dbcbs_planning()
        self._planning_error = None
        if self.planner_backend == "dbcbs":
            self.session.stop()
        else:
            for robot_name in self.possible_agents:
                self.session.navigator.set_active(robot_name, False)
        self._mission_agents.clear()
        self._mission_goals.clear()
        self._reset_dbcbs_velocities()
        self._reset_replanning()
        self.last_safety_stop = None
        self.selected_robot = None
        if clear_pending:
            self.pending_goals.clear()

    def close(self) -> None:
        """Release the planner worker without blocking the simulation thread."""

        if self._closed:
            return
        self.stop_navigation()
        self._closed = True
        if self._planner_executor is not None:
            self._planner_executor.shutdown(wait=False, cancel_futures=True)

    def dispatch_target(self, robot_name: str, target_xy: Sequence[float]) -> bool:
        if robot_name not in self.possible_agents:
            return False
        if self.planner_backend == "dbcbs":
            return bool(self.dispatch_batch({robot_name: target_xy}).get(robot_name))
        start_xy = self._pose_xy(robot_name)
        goal_xy = (float(target_xy[0]), float(target_xy[1]))
        planned = self.session.plan_to_goal(robot_name, start_xy, goal_xy)
        if planned:
            self._mission_agents.add(robot_name)
        return bool(planned)

    def dispatch_from_xy_dict(
        self, targets: Mapping[str, Sequence[float]]
    ) -> int:
        return sum(
            self.dispatch_target(name, target)
            for name, target in targets.items()
        )

    def dispatch_from_emos_result(self, result: Mapping[str, Any]) -> int:
        targets = {
            name: task.target_xy
            for name, task in result.items()
            if hasattr(task, "target_xy")
        }
        return self.dispatch_from_xy_dict(targets)

    def dispatch_batch(
        self,
        targets: Mapping[str, Sequence[float]],
        *,
        priorities: Mapping[str, int] | None = None,
    ) -> dict[str, bool]:
        requests = []
        for robot_name, target_xy in targets.items():
            if robot_name not in self.possible_agents:
                continue
            requests.append((
                robot_name,
                self._pose_xy(robot_name),
                (float(target_xy[0]), float(target_xy[1])),
            ))
        if not requests:
            return {}

        if self.planner_backend == "dbcbs":
            starts = {name: self._pose_xy(name) for name in self.possible_agents}
            goals = {
                robot_name: (float(target_xy[0]), float(target_xy[1]))
                for robot_name, target_xy in targets.items()
                if robot_name in self.possible_agents
            }
            return self._begin_dbcbs_mission(starts, goals)

        planned = self.session.plan_batch(requests, priorities=dict(priorities or {}))
        result = {
            robot_name: bool(planned.get(robot_name))
            for robot_name, _, _ in requests
        }
        self._mission_agents.update(
            name for name, succeeded in result.items() if succeeded
        )
        return result

    def replan_single_conflict_aware(
        self, robot_name: str, goal_xy: Sequence[float]
    ) -> bool:
        if robot_name not in self.possible_agents:
            return False
        if self.planner_backend == "dbcbs":
            return bool(self.dispatch_batch({robot_name: goal_xy}).get(robot_name))
        waypoints = self.session.replan_single(
            robot_name,
            self._pose_xy(robot_name),
            (float(goal_xy[0]), float(goal_xy[1])),
        )
        if waypoints:
            self._mission_agents.add(robot_name)
        return bool(waypoints)

    def is_navigating(self, robot_name: str) -> bool:
        return robot_name in self._mission_agents and (
            self._planning_pending
            or self._replan_pending
            or self.session.is_navigating(robot_name)
        )

    def state(self) -> NavigationPluginState:
        return NavigationPluginState(
            managed_robots=tuple(self.possible_agents),
            excluded_robots=tuple(self.excluded_agents),
            selected_robot=self.selected_robot,
            pending_goals=dict(self.pending_goals),
            navigating_robots=tuple(
                name for name in self.possible_agents if self.is_navigating(name)
            ),
            safety_stop=self.last_safety_stop,
            planning=self._planning_pending,
            planning_error=self._planning_error,
            replanning=self._replan_pending,
            replan_event=self._last_replan_event,
            replan_attempts=self._replan_attempts,
            replan_error=self._replan_error,
        )

    def robot_position(self, robot_name: str) -> tuple[float, float, float]:
        """Return a managed robot's current world position for visualization."""

        if robot_name not in self.possible_agents:
            raise ValueError(f"Robot {robot_name!r} is not managed")
        position, _ = get_robot_pose_tensors(self.base_env, robot_name)
        return tuple(float(position[index].item()) for index in range(3))

    def planned_paths(
        self, *, remaining_only: bool = True
    ) -> dict[str, tuple[tuple[float, float], ...]]:
        """Return planned world-frame polylines for an EAI visualization."""

        result: dict[str, tuple[tuple[float, float], ...]] = {}
        navigator = self.session.navigator
        for robot_name in self.possible_agents:
            waypoints = navigator.get_waypoints(robot_name)
            if remaining_only:
                waypoints = waypoints[navigator.get_index(robot_name) :]
            if waypoints:
                result[robot_name] = tuple(
                    (float(waypoint[0]), float(waypoint[1]))
                    for waypoint in waypoints
                )
        return result

    def _hold_dbcbs_actions(self, poses: Mapping[str, tuple[Any, Any]]) -> dict[str, Any]:
        """Hold every ground robot while a replacement trajectory is computed."""

        actions: dict[str, Any] = {}
        self._reset_dbcbs_velocities()
        for agent_name, (position, _quaternion) in poses.items():
            if agent_name in self.goal_agents:
                actions[agent_name] = self._apply_goal_command(
                    agent_name, position, StepCommand(kind="idle")
                )
            else:
                self.robot_commands[agent_name].zero_()
                actions[agent_name] = self.robot_commands[agent_name]
        return actions

    def _attempt_dbcbs_replan(
        self,
        positions: Mapping[str, tuple[float, float]],
    ) -> None:
        self._replan_attempts += 1
        self._replan_not_before = self._control_elapsed + self._replan_retry_interval
        self._submit_dbcbs_planning(
            positions,
            self._mission_goals,
            mode="replan",
        )

    def _finish_dbcbs_mission(self) -> None:
        self._mission_agents.clear()
        self._mission_goals.clear()
        self._reset_replanning()
        self.last_safety_stop = None

    def compute_actions(self) -> dict[str, Any]:
        """Return command tensors for managed ground robots only."""

        if self.planner_backend == "dbcbs":
            return self._compute_dbcbs_actions()

        actions: dict[str, Any] = {}
        for agent_name in self.possible_agents:
            position, quaternion = get_robot_pose_tensors(self.base_env, agent_name)
            if agent_name in self._mission_agents:
                command = self.session.step(
                    agent_name,
                    float(position[0].item()),
                    float(position[1].item()),
                    float(position[2].item()),
                    get_yaw_from_quaternion(quaternion),
                )
            else:
                command = StepCommand(kind="idle")

            if agent_name in self.goal_agents:
                actions[agent_name] = self._apply_goal_command(
                    agent_name, position, command
                )
            else:
                actions[agent_name] = self._apply_velocity_command(
                    agent_name, command
                )
        return actions

    def _compute_dbcbs_actions(self) -> dict[str, Any]:
        step_dt = float(getattr(self.base_env, "step_dt", 0.02))
        self._control_elapsed += max(0.0, step_dt)
        actions: dict[str, Any] = {}
        poses = {
            name: get_robot_pose_tensors(self.base_env, name)
            for name in self.possible_agents
        }
        positions = {
            name: (float(pose[0][0].item()), float(pose[0][1].item()))
            for name, pose in poses.items()
        }
        planning_finished = self._poll_dbcbs_planning()
        if planning_finished or self._planning_pending:
            return self._hold_dbcbs_actions(poses)

        if self._mission_agents and self._mission_goals:
            unsafe = tuple(
                pair
                for pair in self.session.unsafe_pairs(positions)
                if pair[0] in self._mission_agents or pair[1] in self._mission_agents
            )
            nearby = tuple(
                pair
                for pair in self.session.proximity_pairs(
                    positions, extra_clearance=self._replan_clearance
                )
                if pair[0] in self._mission_agents or pair[1] in self._mission_agents
            )
            if not nearby and not self._replan_pending:
                self._replan_armed = True
                self._last_replan_event = None
                self._replan_error = None

            conflict = (
                unsafe[0]
                if unsafe
                else (nearby[0] if nearby and self._replan_armed else None)
            )
            if conflict is not None:
                self._last_replan_event = conflict
                self._replan_pending = True
            if self._replan_pending:
                if (
                    self._planning_future is None
                    and self._control_elapsed >= self._replan_not_before
                ):
                    self._attempt_dbcbs_replan(positions)
                return self._hold_dbcbs_actions(poses)

        for agent_name in self.possible_agents:
            position, quaternion = poses[agent_name]
            x = float(position[0].item())
            y = float(position[1].item())
            z = float(position[2].item())
            target = self.session.target(agent_name)
            if target is None:
                command = StepCommand(kind="idle")
            elif agent_name in self.goal_agents:
                command = StepCommand(
                    kind="track_goal", goal_xyz=(target.x, target.y, z)
                )
            else:
                vx, vy, wz = self._dbcbs_velocity_command(
                    agent_name,
                    x,
                    y,
                    get_yaw_from_quaternion(quaternion),
                    target,
                )
                command = StepCommand(kind="velocity", vx=vx, vy=vy, wz=wz)

            if agent_name in self.goal_agents:
                actions[agent_name] = self._apply_goal_command(
                    agent_name, position, command
                )
            else:
                actions[agent_name] = self._apply_velocity_command(
                    agent_name, command
                )
        self.session.advance(step_dt, positions)
        if self._mission_agents and not any(
            self.session.is_navigating(name) for name in self._mission_agents
        ):
            self._finish_dbcbs_mission()
        return actions

    def _dbcbs_velocity_command(
        self,
        agent_name: str,
        x: float,
        y: float,
        yaw: float,
        target: Any,
    ) -> tuple[float, float, float]:
        dx = float(target.x) - x
        dy = float(target.y) - y
        distance = math.hypot(dx, dy)
        type_token = _type_token(self.robot_types.get(agent_name, ""))
        holonomic = any(
            token in type_token for token in ("go2", "lite3", "quadruped")
        )
        if holonomic:
            vx_world = float(target.vx) + dx
            vy_world = float(target.vy) + dy
            speed = math.hypot(vx_world, vy_world)
            if speed > 0.45:
                vx_world *= 0.45 / speed
                vy_world *= 0.45 / speed
            cosine = math.cos(yaw)
            sine = math.sin(yaw)
            raw = (
                cosine * vx_world + sine * vy_world,
                -sine * vx_world + cosine * vy_world,
                max(-0.6, min(0.6, _normalize_angle(float(target.yaw) - yaw))),
            )
            alpha = 0.35
        else:
            desired_yaw = (
                math.atan2(dy, dx) if distance > 0.08 else float(target.yaw)
            )
            yaw_error = _normalize_angle(desired_yaw - yaw)
            if abs(yaw_error) > 0.32:
                raw = (0.0, 0.0, max(-0.8, min(0.8, 2.0 * yaw_error)))
            else:
                feedforward = max(
                    0.0,
                    float(target.vx) * math.cos(desired_yaw)
                    + float(target.vy) * math.sin(desired_yaw),
                )
                speed = min(0.45, feedforward + distance)
                if distance < 0.12 and math.hypot(target.vx, target.vy) < 0.03:
                    speed *= distance / 0.12
                raw = (speed, 0.0, max(-0.8, min(0.8, 2.0 * yaw_error)))
            alpha = 0.45 if "scout" in type_token else 0.35
        previous = self._last_dbcbs_velocity[agent_name]
        smoothed = tuple(
            old * (1.0 - alpha) + new * alpha
            for old, new in zip(previous, raw, strict=True)
        )
        self._last_dbcbs_velocity[agent_name] = smoothed
        return smoothed

    def _apply_goal_command(
        self, agent_name: str, current_position: Any, command: StepCommand
    ) -> Any:
        torch = self._torch_module()
        zero = torch.zeros((self.num_envs, 3), device=self.device)
        if not hasattr(self.base_env, "set_command"):
            return zero
        if command.kind in {"hold_goal", "track_goal"} and command.goal_xyz is not None:
            goal = torch.tensor([command.goal_xyz], device=self.device)
            self.base_env.set_command(agent_name, "goal_position", goal)
        else:
            self.base_env.set_command(
                agent_name, "goal_position", current_position.unsqueeze(0)
            )
        return zero

    def _apply_velocity_command(
        self, agent_name: str, command: StepCommand
    ) -> Any:
        output = self.robot_commands[agent_name]
        if command.kind == "velocity":
            output[:, 0] = command.vx
            output[:, 1] = command.vy
            output[:, 2] = command.wz
        else:
            output[:] = 0
        return output


__all__ = [
    "AERIAL_TYPE_TOKENS",
    "EaiMultiRobotNavigationPlugin",
    "NavigationPluginState",
    "builtin_scene_map",
    "get_robot_pose_tensors",
    "get_yaw_from_quaternion",
    "is_aerial_robot",
]
