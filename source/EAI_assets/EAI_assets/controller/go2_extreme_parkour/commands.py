from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

import torch
import isaaclab.sim as sim_utils
from isaaclab.envs.mdp import UniformVelocityCommand, UniformVelocityCommandCfg
from isaaclab.managers import CommandTerm, CommandTermCfg
from isaaclab.markers import VisualizationMarkers, VisualizationMarkersCfg
from isaaclab.utils import configclass
from isaaclab.utils.math import euler_xyz_from_quat, quat_apply_inverse, wrap_to_pi, yaw_quat

if TYPE_CHECKING:
    from isaaclab.envs import ManagerBasedRLEnv


# Kept structurally identical to Isaaclab_Parkour's parkour_events_cfg.py.
# The two large marker types identify current/future waypoints; the two small
# marker types form the green/red direction-dot guides shown in its demo view.
# 可视化目标点
CURRENT_GOAL_MARKER_CFG = VisualizationMarkersCfg(
    markers={
        "frame": sim_utils.SphereCfg(
            radius=0.15,
            visual_material=sim_utils.PreviewSurfaceCfg(diffuse_color=(0.0, 1.0, 0.0)),
        ),
    }
)
FUTURE_GOAL_MARKER_CFG = VisualizationMarkersCfg(
    markers={
        "frame": sim_utils.SphereCfg(
            radius=0.15,
            visual_material=sim_utils.PreviewSurfaceCfg(diffuse_color=(1.0, 0.0, 0.0)),
        ),
    }
)
CURRENT_ARROW_MARKER_CFG = VisualizationMarkersCfg(
    markers={
        "frame": sim_utils.SphereCfg(
            radius=0.05,
            visual_material=sim_utils.PreviewSurfaceCfg(diffuse_color=(0.0, 1.0, 0.0)),
        ),
    }
)
FUTURE_ARROW_MARKER_CFG = VisualizationMarkersCfg(
    markers={
        "frame": sim_utils.SphereCfg(
            radius=0.05,
            visual_material=sim_utils.PreviewSurfaceCfg(diffuse_color=(1.0, 0.0, 0.0)),
        ),
    }
)


class ExtremeParkourVelocityCommand(UniformVelocityCommand):
    """生成给策略的底盘速度命令 ``[vx, vy, wz]``。

    这是 Isaac Lab 的 ``UniformVelocityCommand`` 的定制版本。
    每次重采样时，它随机给出前进/横移速度和一个世界坐标系下的
    目标朝向；每个仿真步再把“离目标朝向还差多少”转换成偏航角速度
    ``wz``。因此策略拿到的是“该怎么走、该怎么转”，不是电机动作。
    """

    cfg: "ExtremeParkourVelocityCommandCfg"

    def _resample_command(self, env_ids: Sequence[int]):
        # 对刚重置或命令到期的环境，重新抽取一组移动目标。
        random_values = torch.empty(len(env_ids), device=self.device)
        self.vel_command_b[env_ids, 0] = random_values.uniform_(*self.cfg.ranges.lin_vel_x)
        self.vel_command_b[env_ids, 1] = random_values.uniform_(*self.cfg.ranges.lin_vel_y)
        self.vel_command_b[env_ids, 2] = random_values.uniform_(*self.cfg.ranges.ang_vel_z)
        self.heading_target[env_ids] = random_values.uniform_(*self.cfg.ranges.heading)
        self.is_heading_env[env_ids] = True
        self.is_standing_env[env_ids] = False

        # 前进速度过小时，把 vx、vy 一并置零，避免产生几乎原地的横移命令。
        nonzero_linear = self.vel_command_b[env_ids, 0].abs() > self.cfg.lin_vel_clip
        self.vel_command_b[env_ids, :2] *= nonzero_linear.unsqueeze(-1)

    def _update_command(self):
        # heading_target 是“想面向哪里”；heading_w 是机器人当前朝向。
        # 二者相减得到 yaw 误差，并通过比例控制转换为 z 轴角速度 wz。
        heading_error = wrap_to_pi(self.heading_target - self.robot.data.heading_w)
        yaw_command = (self.cfg.heading_control_stiffness * heading_error).clamp(-1.0, 1.0)
        self.vel_command_b[:, 2] = yaw_command * (yaw_command.abs() > self.cfg.ang_vel_clip)
        self.vel_command_b[self.is_standing_env] = 0.0


@configclass
class ExtremeParkourVelocityCommandCfg(UniformVelocityCommandCfg):
    """``ExtremeParkourVelocityCommand`` 的参数表，不直接产生命令。

    环境的 Command Manager 会先读取本类的 ``class_type``，据此创建上面的
    ``ExtremeParkourVelocityCommand``；其他字段随后成为该命令类的配置。
    """

    class_type: type = ExtremeParkourVelocityCommand  # 指定实际执行命令逻辑的类。
    heading_command: bool = True  # 使用目标朝向来控制转向。
    heading_control_stiffness: float = 0.8  # yaw 误差 -> wz 的比例系数。
    rel_heading_envs: float = 1.0  # 100% 环境使用 heading 模式（本实现也强制如此）。
    rel_standing_envs: float = 0.0  # 0% 环境采样站立命令。
    lin_vel_clip: float = 0.2  # |vx| 小于该值时，停止 vx、vy 命令。
    ang_vel_clip: float = 0.4  # |wz| 小于该值时，停止转向，减少来回微调。


class ParkourWaypointCommand(CommandTerm):
    """根据跑酷路径点，输出“当前点、下一点”的偏航误差。

    输出是 ``[当前目标的 yaw 误差, 下一目标的 yaw 误差]``，而不是目标点的
    绝对坐标。Teacher 观测使用这两个数预先知道该朝哪里转，既能对准当前
    障碍，也能为下一个障碍提前准备。

    The terrain module owns the immutable local waypoint table.  This command
    converts its selected ``(terrain_level, terrain_column)`` entry to world
    coordinates from the live terrain origin, then returns the two wrapped yaw
    errors: ``[current_goal_yaw_error, next_goal_yaw_error]``.  The lazy
    import avoids a configuration-module import cycle.
    """

    cfg: "ParkourWaypointCommandCfg"

    def __init__(self, cfg: "ParkourWaypointCommandCfg", env: "ManagerBasedRLEnv"):
        super().__init__(cfg, env)
        # 一张路径点表含有每种 terrain level / terrain type 对应的路线。
        if cfg.track_goals_provider is None:
            from .extreme_parkour_v1_geometry import build_track_goal_table

            track_goal_values = build_track_goal_table()
        else:
            track_goal_values = cfg.track_goals_provider()
        track_goals = torch.as_tensor(track_goal_values, device=self.device, dtype=torch.float)
        self._num_goals = track_goals.shape[2]
        # Legacy Extreme Parkour repeats the final goal twice.  It then safely
        # advances cur_goal_idx from 7 to 8, and termination sees index >= 8.
        padded_last_goal = track_goals[:, :, -1:, :].repeat(1, 1, cfg.num_future_goal_obs, 1)
        self._track_goals = torch.cat((track_goals, padded_last_goal), dim=2)
        # Command Manager 对外读取的 2 维命令：[当前目标 yaw 误差，下一目标 yaw 误差]。
        self._command = torch.zeros(self.num_envs, 2, device=self.device)
        self._goal_index = torch.zeros(self.num_envs, dtype=torch.long, device=self.device)
        # Match Extreme Parkour's goal-switch debounce.  This is deliberately
        # a step counter (rather than elapsed seconds), because the reference
        # compares it with ``reach_goal_delay / step_dt``.
        self._reach_goal_timer = torch.zeros(self.num_envs, dtype=torch.float, device=self.device)
        self._current_goal_w = torch.zeros(self.num_envs, 3, device=self.device)
        self._next_goal_w = torch.zeros(self.num_envs, 3, device=self.device)
        self.metrics["goal_index"] = torch.zeros(self.num_envs, device=self.device)
        self.metrics["track_complete"] = torch.zeros(self.num_envs, device=self.device)

    @property
    def command(self) -> torch.Tensor:
        return self._command

    @property
    def goal_index(self) -> torch.Tensor:
        """Current legacy waypoint index; ``num_goals`` means completion."""

        return self._goal_index

    @property
    def track_complete(self) -> torch.Tensor:
        """Whether the legacy index advanced past the final waypoint."""

        return self._goal_index >= self._num_goals

    @property
    def current_goal_w(self) -> torch.Tensor:
        """Current selected goal positions in world coordinates, shape ``(N, 3)``."""

        return self._current_goal_w

    @property
    def next_goal_w(self) -> torch.Tensor:
        """Next selected goal positions in world coordinates, shape ``(N, 3)``."""

        return self._next_goal_w

    def _resample_command(self, env_ids: Sequence[int]):
        # 新 episode 从路线第 0 个路径点重新开始。
        self._goal_index[env_ids] = 0
        self._reach_goal_timer[env_ids] = 0.0
        self._command[env_ids] = 0.0

    def _directions_from_goals(
        self, asset, terrain
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]:
        max_level = self._track_goals.shape[0] - 1
        max_column = self._track_goals.shape[1] - 1
        levels = terrain.terrain_levels.clamp(0, max_level).long()
        columns = terrain.terrain_types.clamp(0, max_column).long()
        # 路径点表是每块地形内部的局部坐标；加上 origin 才是世界坐标。
        goals_w = self._track_goals[levels, columns] + terrain.env_origins.unsqueeze(1)

        current_goals_w = goals_w[torch.arange(self.num_envs, device=self.device), self._goal_index]
        next_indices = (self._goal_index + 1).clamp_max(self._track_goals.shape[2] - 1)
        next_goals_w = goals_w[torch.arange(self.num_envs, device=self.device), next_indices]
        current_delta_w = current_goals_w[:, :2] - asset.data.root_pos_w[:, :2]
        next_delta_w = next_goals_w[:, :2] - asset.data.root_pos_w[:, :2]
        return current_delta_w, next_delta_w, current_goals_w, next_goals_w

    def _update_command(self):
        # 每个环境可能被分配到不同的地形块，故先读取它自己的机器人和地形状态。
        asset = self._env.scene[self.cfg.asset_name]
        terrain = self._env.scene.terrain
        # Same ordering as the legacy _update_goals(): consume the previous
        # delay first, then count this step's hit on the previous current goal.
        # The timer intentionally is not reset when the robot exits the radius.
        current_delta_w, next_delta_w, current_goals_w, next_goals_w = self._directions_from_goals(asset, terrain)
        # 进入阈值半径后先计时；持续足够久才切换下一个目标，避免边界抖动。
        reached = torch.linalg.vector_norm(current_delta_w, dim=1) < self.cfg.goal_reach_threshold
        next_flag = self._reach_goal_timer > (self.cfg.goal_reach_delay / self._env.step_dt)
        self._goal_index[next_flag] += 1
        self._reach_goal_timer[next_flag] = 0.0
        self._reach_goal_timer[reached] += 1.0

        # The reference computes target yaw from the current/next goal buffers
        # obtained before the index increment.  The newly selected goal takes
        # effect on the following policy step.
        self._current_goal_w[:] = current_goals_w
        self._next_goal_w[:] = next_goals_w
        # atan2 给出“机器人 -> 目标点”的方向；再减当前朝向就是需转的角度。
        _, _, robot_yaw = euler_xyz_from_quat(asset.data.root_quat_w)
        current_goal_yaw = torch.atan2(current_delta_w[:, 1], current_delta_w[:, 0])
        next_goal_yaw = torch.atan2(next_delta_w[:, 1], next_delta_w[:, 0])
        current_error = wrap_to_pi(current_goal_yaw - robot_yaw)
        next_error = wrap_to_pi(next_goal_yaw - robot_yaw)
        self._command = torch.stack((current_error, next_error), dim=-1)

    def _update_metrics(self):
        # 这些仅用于日志/监控，不是输给策略的 command。
        self.metrics["goal_index"] = self._goal_index.float()
        self.metrics["track_complete"] = self.track_complete.float()

    def _set_debug_vis_impl(self, debug_vis: bool):
        """开关调试可视化：绿球=当前点，红球=后续点，小球=方向指示。"""
        if debug_vis:
            if not hasattr(self, "current_goal_pose_visualizer"):
                self.current_goal_pose_visualizer = VisualizationMarkers(self.cfg.current_goal_pose_visualizer_cfg)
            self.current_goal_pose_visualizer.set_visibility(True)
            if not hasattr(self, "future_goal_poses_visualizer"):
                self.future_goal_poses_visualizer = VisualizationMarkers(self.cfg.future_goal_poses_visualizer_cfg)
            self.future_goal_poses_visualizer.set_visibility(True)
            if not hasattr(self, "current_arrow_visualizer"):
                self.current_arrow_visualizer = VisualizationMarkers(self.cfg.current_arrow_visualizer_cfg)
            self.current_arrow_visualizer.set_visibility(True)
            if not hasattr(self, "future_arrow_visualizer"):
                self.future_arrow_visualizer = VisualizationMarkers(self.cfg.future_arrow_visualizer_cfg)
            self.future_arrow_visualizer.set_visibility(True)
        else:
            for visualizer_name in (
                "current_goal_pose_visualizer",
                "future_goal_poses_visualizer",
                "current_arrow_visualizer",
                "future_arrow_visualizer",
            ):
                if hasattr(self, visualizer_name):
                    getattr(self, visualizer_name).set_visibility(False)

    def _debug_vis_callback(self, event):
        # 此函数只负责画辅助球，不影响 reward、观测或控制。
        # Commands can be constructed before the articulation is initialized.
        robot = self._env.scene[self.cfg.asset_name]
        if not robot.is_initialized:
            return

        terrain = self._env.scene.terrain
        max_level = self._track_goals.shape[0] - 1
        max_column = self._track_goals.shape[1] - 1
        levels = terrain.terrain_levels.clamp(0, max_level).long()
        columns = terrain.terrain_types.clamp(0, max_column).long()
        # The final two entries are only terminal-index padding for the
        # command update.  They are not physical waypoint markers.
        goals_w = self._track_goals[levels, columns, : self._num_goals] + terrain.env_origins.unsqueeze(1)

        env_ids = torch.arange(self.num_envs, device=self.device)
        num_goals = self._num_goals
        final_goal_index = num_goals - 1
        waypoint_ids = torch.arange(num_goals, device=self.device).unsqueeze(0)
        display_goal_index = self._goal_index.clamp_max(final_goal_index)

        # The current waypoint is green and every later waypoint (including
        # the terminal one) is red.  The small red/green spheres below are
        # direction guides, matching the reference ParkourEvent callback.
        is_future_waypoint = waypoint_ids > display_goal_index.unsqueeze(1)
        current_goals_w = goals_w[env_ids, display_goal_index].clone()
        future_goals_w = goals_w[is_future_waypoint].clone()

        # Match the reference's dotted direction guides.  They originate at
        # the robot base and point respectively at the current and next goal.
        root_pos_w = robot.data.root_pos_w
        current_target_w = goals_w[env_ids, display_goal_index]
        next_goal_indices = (display_goal_index + 1).clamp_max(final_goal_index)
        next_target_w = goals_w[env_ids, next_goal_indices]
        current_direction = torch.nn.functional.normalize(current_target_w[:, :2] - root_pos_w[:, :2], dim=1, eps=1.0e-6)
        next_direction = torch.nn.functional.normalize(next_target_w[:, :2] - root_pos_w[:, :2], dim=1, eps=1.0e-6)
        dot_indices = torch.arange(
            self.cfg.direction_dot_start,
            self.cfg.direction_dot_start + self.cfg.arrow_num,
            device=self.device,
            dtype=root_pos_w.dtype,
        )
        current_dot_positions = root_pos_w[:, None, :].repeat(1, self.cfg.arrow_num, 1)
        future_dot_positions = root_pos_w[:, None, :].repeat(1, self.cfg.arrow_num, 1)
        current_dot_positions[:, :, :2] += 0.1 * dot_indices[None, :, None] * current_direction[:, None, :]
        future_dot_positions[:, :, :2] += 0.2 * dot_indices[None, :, None] * next_direction[:, None, :]

        self.current_goal_pose_visualizer.visualize(translations=current_goals_w)
        if future_goals_w.numel() > 0:
            self.future_goal_poses_visualizer.visualize(translations=future_goals_w)
        self.current_arrow_visualizer.visualize(translations=current_dot_positions.reshape(-1, 3))
        self.future_arrow_visualizer.visualize(translations=future_dot_positions.reshape(-1, 3))

@configclass
class ParkourWaypointCommandCfg(CommandTermCfg):
    """``ParkourWaypointCommand`` 的路径跟随配置。

    它定义“用哪台机器人、离路径点多近算到达、停留多久才切换”，以及仅供
    人查看的调试标记样式；实际计算仍由上面的 ``ParkourWaypointCommand`` 完成。
    """

    class_type: type[CommandTerm] = ParkourWaypointCommand  # 指定实际计算路径点命令的类。
    resampling_time_range: tuple[float, float] = (1.0e6, 1.0e6)  # 路线不定时重采样，只在 reset 时重置。
    asset_name: str = "robot"  # 要读取位置和朝向的机器人名称。
    goal_reach_threshold: float = 0.20  # 与当前目标相距小于 0.20 m，视为到达。
    goal_reach_delay: float = 0.10  # 到达后至少保持 0.10 秒，再切换下一目标。
    num_future_goal_obs: int = 2  # 末尾重复最后目标的次数，防止读取“下一点”越界。
    """Repeated final-goal count used for the legacy safe current/next gather."""
    track_goals_provider: object | None = None
    """Optional callable returning a ``(level, column, goal, xyz)`` table."""
    arrow_num: int = 8
    """Number of small directional guide spheres, matching Isaaclab_Parkour."""

    direction_dot_start: int = 3
    """First direction-dot multiplier, matching Isaaclab_Parkour's (i + 3)."""

    future_goal_poses_visualizer_cfg: VisualizationMarkersCfg = FUTURE_GOAL_MARKER_CFG.replace(
        prim_path="/Visuals/Command/future_goal_poses"
    )
    current_goal_pose_visualizer_cfg: VisualizationMarkersCfg = CURRENT_GOAL_MARKER_CFG.replace(
        prim_path="/Visuals/Command/current_goal_pose"
    )
    future_arrow_visualizer_cfg: VisualizationMarkersCfg = FUTURE_ARROW_MARKER_CFG.replace(
        prim_path="/Visuals/Command/future_arrow"
    )
    current_arrow_visualizer_cfg: VisualizationMarkersCfg = CURRENT_ARROW_MARKER_CFG.replace(
        prim_path="/Visuals/Command/current_arrow"
    )

    # Preserve the scale adjustments present in Isaaclab_Parkour.
    future_goal_poses_visualizer_cfg.markers["frame"].scale = (1.1, 1.1, 1.1)
    current_goal_pose_visualizer_cfg.markers["frame"].scale = (1.1, 1.1, 1.1)
    future_arrow_visualizer_cfg.markers["frame"].scale = (0.1, 0.1, 0.1)
    current_arrow_visualizer_cfg.markers["frame"].scale = (0.1, 0.1, 0.1)
