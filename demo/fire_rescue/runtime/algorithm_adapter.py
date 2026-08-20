# Copyright (c) 2022-2025. EAI-Factory EMOS: thin sim adapter for algorithm.global_planner.session.
"""Pose I/O + torch tensors only; planning, lookahead, velocity law live in ``algorithm.global_planner``."""

from __future__ import annotations

from typing import Any, Dict, List, Set, Tuple

import torch

from EAI.controllers.base import normalize_controller_entry

from algorithm.global_planner.session import GlobalNavSession, StepCommand


def _get_yaw_from_quat(quat_tensor: torch.Tensor) -> float:
    import math

    w, x, y, z = quat_tensor[0], quat_tensor[1], quat_tensor[2], quat_tensor[3]
    siny_cosp = 2.0 * (w * z + x * y)
    cosy_cosp = 1.0 - 2.0 * (y * y + z * z)
    return math.atan2(siny_cosp, cosy_cosp)


def _get_robot_pose_tensors(base_env: Any, name: str) -> Tuple[torch.Tensor, torch.Tensor]:
    robot = base_env.scene.articulations[name]
    if name == "scout_1":
        try:
            for ln in ("base_link", "chassis"):
                bid, _ = robot.find_bodies(ln, preserve_order=True)
                if bid:
                    return robot.data.body_pos_w[0, bid[0]], robot.data.body_quat_w[0, bid[0]]
        except Exception:
            pass
    return robot.data.root_pos_w[0], robot.data.root_quat_w[0]


def _classify_agents(env_cfg: Any, possible_agents: List[str]) -> Tuple[Set[str], Set[str]]:
    """Returns (goal_position_agents, velocity_command_agents)."""
    goal_pos: Set[str] = set()
    velocity: Set[str] = set()
    controllers = getattr(env_cfg, "controllers", None) or {}
    for agent_name in possible_agents:
        entry = controllers.get(agent_name)
        if entry is None:
            velocity.add(agent_name)
            continue
        controller_cfg, _ = normalize_controller_entry(entry)
        cn = getattr(controller_cfg, "command_name", None)
        rt = getattr(controller_cfg, "robot_type", None)
        if cn == "goal_position" or rt in ("Quadcopter", "M20Nav"):
            goal_pos.add(agent_name)
        else:
            velocity.add(agent_name)
    return goal_pos, velocity


class EmosFactoryNavBridge:
    """Uses :class:`~algorithm.global_planner.session.GlobalNavSession` for all navigation math."""

    def __init__(
        self,
        base_env: Any,
        possible_agents: List[str],
        env_cfg: Any,
        device: str,
        num_envs: int,
        map_yaml: str,
        *,
        waypoint_step: float = 1.0,
        prefer_astar: bool = True,
        inflation_radius_cells: int | None = None,
    ) -> None:
        self.base_env = base_env
        self.possible_agents = list(possible_agents)
        self.env_cfg = env_cfg
        self.device = device
        self.num_envs = num_envs

        self.session = GlobalNavSession(
            map_yaml,
            waypoint_step=waypoint_step,
            prefer_astar=prefer_astar,
            inflation_radius_cells=inflation_radius_cells if inflation_radius_cells is not None else 12,
        )

        self.goal_agents, self.velocity_agents = _classify_agents(env_cfg, self.possible_agents)
        for a in self.possible_agents:
            self.session.register_agent(a, use_goal_position=(a in self.goal_agents))

        self.robot_commands = {
            a: torch.zeros((num_envs, 3), device=device) for a in self.possible_agents
        }

    def dispatch_target(self, robot_name: str, target_xy: Tuple[float, float]) -> bool:
        if robot_name not in self.base_env.scene.articulations:
            return False
        pos, _ = _get_robot_pose_tensors(self.base_env, robot_name)
        start_xy = (float(pos[0].item()), float(pos[1].item()))
        ok = self.session.plan_to_goal(robot_name, start_xy, target_xy)
        if not ok:
            print(f"[factory_nav] ⚠️  {robot_name}: no path to ({target_xy[0]:.2f}, {target_xy[1]:.2f})")
            return False
        print(f"[factory_nav] {robot_name}: path to ({target_xy[0]:.2f}, {target_xy[1]:.2f})")
        return True

    def dispatch_from_emos_result(self, result: Dict[str, Any]) -> int:
        n = 0
        for robot_name, task in result.items():
            if not hasattr(task, "target_xy"):
                continue
            xy = task.target_xy
            if self.dispatch_target(robot_name, (float(xy[0]), float(xy[1]))):
                n += 1
        return n

    def dispatch_from_xy_dict(self, targets: Dict[str, Tuple[float, float]]) -> int:
        n = 0
        for robot_name, xy in targets.items():
            if self.dispatch_target(robot_name, (float(xy[0]), float(xy[1]))):
                n += 1
        return n

    # ── Multi-robot conflict-aware batch planning ────────────────────────────

    def dispatch_batch(
        self,
        targets: Dict[str, Tuple[float, float]],
        *,
        priorities: Dict[str, int] | None = None,
    ) -> Dict[str, bool]:
        """Plan for multiple robots simultaneously, avoiding inter-robot conflicts.

        Returns ``{robot_name: success_bool}``.
        """
        requests: List[Tuple[str, Tuple[float, float], Tuple[float, float]]] = []
        for robot_name, goal_xy in targets.items():
            if robot_name not in self.base_env.scene.articulations:
                continue
            pos, _ = _get_robot_pose_tensors(self.base_env, robot_name)
            start_xy = (float(pos[0].item()), float(pos[1].item()))
            requests.append((robot_name, start_xy, goal_xy))

        if not requests:
            return {}

        planned = self.session.plan_batch(requests, priorities=priorities)

        result: Dict[str, bool] = {}
        for robot_name, _, goal_xy in requests:
            wps = planned.get(robot_name)
            ok = wps is not None and len(wps) > 0
            result[robot_name] = ok
            if ok:
                print(f"[factory_nav] {robot_name}: batch path to "
                      f"({goal_xy[0]:.2f}, {goal_xy[1]:.2f}) [{len(wps)} wps]")
            else:
                print(f"[factory_nav] ⚠️  {robot_name}: batch plan failed → "
                      f"({goal_xy[0]:.2f}, {goal_xy[1]:.2f})")
        return result

    def replan_single_conflict_aware(
        self,
        robot_name: str,
        goal_xy: Tuple[float, float],
    ) -> bool:
        """Replan one robot while respecting other robots' existing reservations."""
        if robot_name not in self.base_env.scene.articulations:
            return False
        pos, _ = _get_robot_pose_tensors(self.base_env, robot_name)
        start_xy = (float(pos[0].item()), float(pos[1].item()))
        wps = self.session.replan_single(robot_name, start_xy, goal_xy)
        if wps:
            print(f"[factory_nav] {robot_name}: conflict-aware replan → "
                  f"({goal_xy[0]:.2f}, {goal_xy[1]:.2f}) [{len(wps)} wps]")
            return True
        print(f"[factory_nav] ⚠️  {robot_name}: conflict-aware replan failed")
        return False

    def is_navigating(self, robot_name: str) -> bool:
        return self.session.is_navigating(robot_name)

    def compute_actions(self) -> Dict[str, torch.Tensor]:
        out: Dict[str, torch.Tensor] = {}

        for agent in self.possible_agents:
            if agent not in self.base_env.scene.articulations:
                continue

            curr_pos, curr_quat = _get_robot_pose_tensors(self.base_env, agent)
            curr_yaw = _get_yaw_from_quat(curr_quat)
            x = float(curr_pos[0].item())
            y = float(curr_pos[1].item())
            z = float(curr_pos[2].item())

            cmd = self.session.step(agent, x, y, z, curr_yaw)

            if agent in self.goal_agents:
                out[agent] = self._apply_goal_command(agent, curr_pos, cmd)
            else:
                out[agent] = self._apply_velocity_command(agent, cmd)

        return out

    def _apply_goal_command(self, agent: str, curr_pos: torch.Tensor, cmd: StepCommand) -> torch.Tensor:
        zero = torch.zeros((self.num_envs, 3), device=self.device)
        if not hasattr(self.base_env, "set_command"):
            return zero

        if cmd.kind == "hold_goal" and cmd.goal_xyz is not None:
            g = torch.tensor([cmd.goal_xyz], device=self.device)
            self.base_env.set_command(agent, "goal_position", g)
            return zero

        if cmd.kind == "track_goal" and cmd.goal_xyz is not None:
            nt = torch.tensor([cmd.goal_xyz], device=self.device)
            self.base_env.set_command(agent, "goal_position", nt)
            return zero

        self.base_env.set_command(agent, "goal_position", curr_pos.unsqueeze(0))
        return zero

    def _apply_velocity_command(self, agent: str, cmd: StepCommand) -> torch.Tensor:
        c = self.robot_commands[agent]
        if cmd.kind == "velocity":
            c[:, 0] = cmd.vx
            c[:, 1] = cmd.vy
            c[:, 2] = cmd.wz
            return c
        c[:] = 0
        return c
