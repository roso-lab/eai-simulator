"""Observation helpers specific to the Go2 parkour tasks."""

from __future__ import annotations

from collections.abc import Sequence
import math
import os
from pathlib import Path
from typing import TYPE_CHECKING

import torch
import torch.nn.functional as F
from isaaclab.assets import Articulation
from isaaclab.envs import mdp as isaaclab_mdp
from isaaclab.managers import ManagerTermBase, SceneEntityCfg
from isaaclab.sensors import ContactSensor, RayCasterCamera
from isaaclab.utils.math import euler_xyz_from_quat, quat_apply_yaw, quat_from_euler_xyz, wrap_to_pi

from .extreme_parkour_v1_geometry import HORIZONTAL_SCALE, TRACK_SIZE
from .extreme_parkour_v1_heightfield import build_extreme_parkour_v1_heightfields

if TYPE_CHECKING:
    from isaaclab.envs import ManagerBasedEnv


# The Teacher protocol is fixed-width.  Keep these values in one place so
# both the Teacher and visual Student observation terms use the same layout.
EXTREME_PARKOUR_HISTORY_LENGTH = 10
EXTREME_PARKOUR_PROPRIO_DIM = 53
EXTREME_PARKOUR_SCAN_DIM = 132
EXTREME_PARKOUR_PRIV_LATENT_DIM = 29
EXTREME_PARKOUR_OBSERVATION_DIM = 753

# Backward-compatible local name used by the Teacher constructor default.
build_extreme_parkour_heightfields = build_extreme_parkour_v1_heightfields


def roll_pitch(env: "ManagerBasedEnv", asset_cfg: SceneEntityCfg = SceneEntityCfg("robot")) -> torch.Tensor:
    """Return the simulated base roll and pitch in radians, shape ``(N, 2)``."""

    asset: Articulation = env.scene[asset_cfg.name]
    roll, pitch, _ = euler_xyz_from_quat(asset.data.root_quat_w)
    return torch.stack((roll, pitch), dim=-1)

# 足端接触观测改为当前帧与历史帧 OR 滤波，阈值 2 N
def feet_contact(
    env: "ManagerBasedEnv",
    sensor_cfg: SceneEntityCfg = SceneEntityCfg("contact_forces", body_names=".*_foot"),
    threshold: float = 2.0,
) -> torch.Tensor:
    """Binary contact state OR-filtered across the newest and oldest samples."""

    contact_sensor = env.scene[sensor_cfg.name]
    body_ids = sensor_cfg.body_ids
    # ``parkour_proprio`` calls this helper internally, so IsaacLab's manager
    # does not resolve the helper's default SceneEntityCfg.  Resolve its four
    # Go2 foot bodies here rather than accidentally using every body covered
    # by the contact sensor (which would turn the 4-D term into 19-D).
    if isinstance(body_ids, slice):
        body_ids, _ = contact_sensor.find_bodies([".*_foot"])
    force_history = contact_sensor.data.net_forces_w_history[:, :, body_ids]
    current_contact = torch.linalg.vector_norm(force_history[:, 0], dim=-1) > threshold
    previous_contact = torch.linalg.vector_norm(force_history[:, -1], dim=-1) > threshold
    return (current_contact | previous_contact).to(dtype=force_history.dtype)


def parkour_proprio(env: "ManagerBasedEnv", command_name: str = "base_velocity") -> torch.Tensor:
    """Phase-1 Teacher's exact 48-D current proprioceptive observation."""

    asset: Articulation = env.scene["robot"]
    command = env.command_manager.get_command(command_name)
    proprio = torch.cat(
        (
            asset.data.root_ang_vel_b * 0.2,  # 3
            roll_pitch(env),  # 2
            command,  # 3
            asset.data.joint_pos - asset.data.default_joint_pos,  # 12
            asset.data.joint_vel * 0.05,  # 12
            isaaclab_mdp.last_action(env),  # 12
            feet_contact(env),  # 4
        ),
        dim=-1,
    )
    if proprio.shape[-1] != 48:
        raise RuntimeError(f"Phase-1 Teacher proprio must be 48-D, got {proprio.shape[-1]}.")
    return proprio


def physical_privileged(env: "ManagerBasedEnv") -> torch.Tensor:
    """Return the real startup-randomized physical parameters for each environment.

    The buffer is written after the official IsaacLab startup randomization
    terms run.  A zero value is only returned while the observation manager is
    probing shapes before startup; it is never the training-time parameter
    source.
    """

    values = getattr(env, "_parkour_physical_parameters", None)
    if values is None:
        return torch.zeros(env.num_envs, 29, device=env.device)
    return values


def robot_privileged(env: "ManagerBasedEnv", asset_cfg: SceneEntityCfg = SceneEntityCfg("robot")) -> torch.Tensor:
    """Phase-1 robot privileged vector: true linear velocity plus six zeros.

    The paper fixes this interface at nine dimensions for history
    compatibility, but only the three body-frame linear-velocity entries are
    populated: ``[v_x, v_y, v_z, 0, 0, 0, 0, 0, 0]``.  The zero slots are
    intentional protocol-defined placeholders, not observation padding.
    """

    asset: Articulation = env.scene[asset_cfg.name]
    compatibility_zeros = torch.zeros(env.num_envs, 6, device=env.device, dtype=asset.data.root_lin_vel_b.dtype)
    return torch.cat((asset.data.root_lin_vel_b, compatibility_zeros), dim=-1)


class ExtremeParkourTeacherObservation(ManagerTermBase):
    """Build the original ``53 + 132 + 9 + 29 + 10*53`` Teacher vector."""

    def __init__(self, cfg, env: "ManagerBasedEnv"):
        super().__init__(cfg, env)
        self.asset_cfg: SceneEntityCfg = cfg.params["asset_cfg"]
        self.contact_sensor_cfg: SceneEntityCfg = cfg.params["contact_sensor_cfg"]
        self.velocity_command_name = str(cfg.params.get("velocity_command_name", "base_velocity"))
        self.waypoint_command_name = str(cfg.params.get("waypoint_command_name", "parkour_waypoint"))
        self.history_length = int(cfg.params.get("history_length", EXTREME_PARKOUR_HISTORY_LENGTH))
        self.yaw_update_interval = int(cfg.params.get("yaw_update_interval", 5))

        if self.history_length != EXTREME_PARKOUR_HISTORY_LENGTH:
            raise ValueError("Extreme Parkour requires exactly ten proprioceptive history frames.")
        if self.yaw_update_interval <= 0:
            raise ValueError("yaw_update_interval must be positive.")

        self.asset: Articulation = env.scene[self.asset_cfg.name]
        self.contact_sensor: ContactSensor = env.scene.sensors[self.contact_sensor_cfg.name]
        if len(self.asset_cfg.joint_ids) != 12:
            raise RuntimeError(
                "Extreme Parkour observation requires twelve joints in SDK order; "
                f"resolved {len(self.asset_cfg.joint_ids)}."
            )
        if len(self.contact_sensor_cfg.body_ids) != 4:
            raise RuntimeError(
                "Extreme Parkour observation requires four feet in FR, FL, RR, RL order; "
                f"resolved {len(self.contact_sensor_cfg.body_ids)}."
            )

        self.history = torch.zeros(
            env.num_envs,
            self.history_length,
            EXTREME_PARKOUR_PROPRIO_DIM,
            device=env.device,
        )
        self.cached_waypoint_yaw = torch.zeros(env.num_envs, 2, device=env.device)
        self.height_points = self._make_height_points(device=env.device, dtype=self.asset.data.root_pos_w.dtype)
        heightfield_builder = cfg.params.get("heightfield_builder", build_extreme_parkour_heightfields)
        self.heightfield_horizontal_scale = float(
            cfg.params.get("heightfield_horizontal_scale", HORIZONTAL_SCALE)
        )
        if self.heightfield_horizontal_scale <= 0.0:
            raise ValueError("heightfield_horizontal_scale must be positive.")
        self.height_fields = heightfield_builder(env.device, self.asset.data.root_pos_w.dtype)
        self.cached_height_scan = torch.zeros(env.num_envs, EXTREME_PARKOUR_SCAN_DIM, device=env.device)

    def reset(self, env_ids: Sequence[int] | torch.Tensor | None = None) -> None:
        if env_ids is None:
            self.history.zero_()
            self.cached_waypoint_yaw.zero_()
        else:
            self.history[env_ids] = 0.0
            self.cached_waypoint_yaw[env_ids] = 0.0

    def _feet_contact(self) -> torch.Tensor:
        forces = self.contact_sensor.data.net_forces_w_history[:, :, self.contact_sensor_cfg.body_ids]
        # Legacy ``contact_filt`` is an OR of the two newest contact samples.
        contact = torch.any(torch.linalg.vector_norm(forces, dim=-1) > 2.0, dim=1)
        return contact.to(dtype=forces.dtype) - 0.5

    def _update_waypoint_yaw(self, env: "ManagerBasedEnv") -> None:
        step = int(getattr(env, "common_step_counter", 0))
        if step % self.yaw_update_interval == 0:
            waypoint = env.command_manager.get_command(self.waypoint_command_name)
            if waypoint.shape[-1] != 2:
                raise RuntimeError(
                    f"Waypoint command must contain current/next yaw errors, got {waypoint.shape[-1]} values."
                )
            self.cached_waypoint_yaw.copy_(waypoint)

    # 当前的 proprioceptive 观测，53-D
    def _current_proprio(self, env: "ManagerBasedEnv") -> torch.Tensor:
        self._update_waypoint_yaw(env)
        roll, pitch, _ = euler_xyz_from_quat(self.asset.data.root_quat_w)
        imu = torch.stack((wrap_to_pi(roll), wrap_to_pi(pitch)), dim=-1)
        speed_command = env.command_manager.get_command(self.velocity_command_name)[:, :1]

        # The legacy two-class signal distinguishes its flat parkour track
        # (terrain class 17) from all obstacle tracks.  Local columns 0--7 are
        # the corresponding flat track.
        is_flat = env.scene.terrain.terrain_types < 8
        terrain_class = torch.stack((~is_flat, is_flat), dim=-1).to(self.asset.data.joint_pos.dtype)

        joint_ids = self.asset_cfg.joint_ids
        joint_pos = self.asset.data.joint_pos[:, joint_ids] - self.asset.data.default_joint_pos[:, joint_ids]
        joint_vel = self.asset.data.joint_vel[:, joint_ids] * 0.05
        last_action = env.action_manager.action
        if last_action.shape[-1] != 12:
            raise RuntimeError(f"Extreme Parkour requires a 12-D action, got {last_action.shape[-1]}.")

        zeros_yaw = torch.zeros_like(self.cached_waypoint_yaw[:, :1])
        zeros_command_xy = torch.zeros(env.num_envs, 2, device=env.device, dtype=speed_command.dtype)
        # 拼接
        proprio = torch.cat(
            (
                self.asset.data.root_ang_vel_b * 0.25,
                imu,
                zeros_yaw,
                self.cached_waypoint_yaw,
                zeros_command_xy,
                speed_command,
                terrain_class,
                joint_pos,
                joint_vel,
                last_action,
                self._feet_contact(),
            ),
            dim=-1,
        )
        if proprio.shape[-1] != EXTREME_PARKOUR_PROPRIO_DIM:
            raise RuntimeError(
                f"Extreme Parkour current proprio must be {EXTREME_PARKOUR_PROPRIO_DIM}-D, "
                f"got {proprio.shape[-1]}."
            )
        return proprio

    # 高度扫描点    总共132维
    @staticmethod
    def _make_height_points(device: torch.device, dtype: torch.dtype) -> torch.Tensor:
        """Create the legacy 12 x 11 yaw-aligned scan grid in base coordinates."""

        # 132 点范围 x=[-0.45,1.20]、y=[-0.75,0.75]、间隔 .15 m
        x = torch.arange(-0.45, 1.20 + 1.0e-6, 0.15, device=device, dtype=dtype)
        y = torch.arange(-0.75, 0.75 + 1.0e-6, 0.15, device=device, dtype=dtype)
        grid_x, grid_y = torch.meshgrid(x, y, indexing="ij")
        points = torch.zeros(grid_x.numel(), 3, device=device, dtype=dtype)
        points[:, 0] = grid_x.flatten()
        points[:, 1] = grid_y.flatten()
        if points.shape[0] != EXTREME_PARKOUR_SCAN_DIM:
            raise RuntimeError(f"Expected {EXTREME_PARKOUR_SCAN_DIM} height points, got {points.shape[0]}.")
        return points

    def _height_scan(self, env: "ManagerBasedEnv") -> torch.Tensor:
        """Match the legacy height-field lookup and five-policy-step refresh."""

        step = int(getattr(env, "common_step_counter", 0))
        # 每五步更新一次高度扫描，减少计算量
        if step % self.yaw_update_interval == 0:
            num_envs = env.num_envs
            num_points = self.height_points.shape[0]
            points = self.height_points.unsqueeze(0).expand(num_envs, -1, -1)
            rotated = quat_apply_yaw(
                self.asset.data.root_quat_w.repeat_interleave(num_points, dim=0), points.reshape(-1, 3)
            ).view(num_envs, num_points, 3)

            # ``terrain.env_origins`` is the safe local point (1.0, 2.0, 0.0)
            # in every deterministic 18 x 4 m tile.
            local_offset = torch.tensor((1.0, TRACK_SIZE[1] * 0.5), device=env.device, dtype=rotated.dtype)
            local_xy = (
                self.asset.data.root_pos_w[:, None, :2]
                + rotated[:, :, :2]
                - env.scene.terrain.env_origins[:, None, :2]
                + local_offset
            )
            ix = torch.floor(local_xy[..., 0] / self.heightfield_horizontal_scale).long()
            iy = torch.floor(local_xy[..., 1] / self.heightfield_horizontal_scale).long()
            ix = ix.clamp_(0, self.height_fields.shape[-2] - 2)
            iy = iy.clamp_(0, self.height_fields.shape[-1] - 2)

            terrain = env.scene.terrain
            # Index only the 132 requested cells.  Materializing one complete
            # Materializing a complete map for every environment would add a
            # large temporary GPU allocation at every five-step refresh.
            height_fields_flat = self.height_fields.flatten(2).flatten(0, 1)
            tile_ids = terrain.terrain_levels * self.height_fields.shape[1] + terrain.terrain_types
            stride_y = self.height_fields.shape[-1]
            heights_1 = height_fields_flat[tile_ids[:, None], ix * stride_y + iy]
            heights_2 = height_fields_flat[tile_ids[:, None], (ix + 1) * stride_y + iy]
            heights_3 = height_fields_flat[tile_ids[:, None], ix * stride_y + iy + 1]
            measured_heights = torch.minimum(heights_1, torch.minimum(heights_2, heights_3))
            self.cached_height_scan = (self.asset.data.root_pos_w[:, 2:3] - 0.3 - measured_heights).clamp(-1.0, 1.0)

        return self.cached_height_scan

    # 显示特征  特权显式信息9维
    def _privileged_explicit(self) -> torch.Tensor:
        zeros = torch.zeros(
            self.num_envs,
            6,
            device=self.asset.device,
            dtype=self.asset.data.root_lin_vel_b.dtype,
        )
        return torch.cat((self.asset.data.root_lin_vel_b * 2.0, zeros), dim=-1)

    # 29维物理参数在这里
    def _privileged_latent(self, env: "ManagerBasedEnv") -> torch.Tensor:
        values = getattr(env, "_extreme_parkour_physical_parameters", None)
        if values is None:
            return torch.zeros(env.num_envs, EXTREME_PARKOUR_PRIV_LATENT_DIM, device=env.device)
        return values

    def __call__(
        self,
        env: "ManagerBasedEnv",
        asset_cfg: SceneEntityCfg,
        contact_sensor_cfg: SceneEntityCfg,
        velocity_command_name: str = "base_velocity",
        waypoint_command_name: str = "parkour_waypoint",
        history_length: int = EXTREME_PARKOUR_HISTORY_LENGTH,
        yaw_update_interval: int = 5,
        heightfield_builder=None,
        heightfield_horizontal_scale: float | None = None,
    ) -> torch.Tensor:
        del asset_cfg, contact_sensor_cfg
        del velocity_command_name, waypoint_command_name, history_length, yaw_update_interval
        del heightfield_builder
        del heightfield_horizontal_scale

        # 最终 753 维在这里拼起来
        current = self._current_proprio(env)
        output = torch.cat(
            (
                current,
                self._height_scan(env),
                self._privileged_explicit(),
                self._privileged_latent(env),
                self.history.reshape(env.num_envs, -1),
            ),
            dim=-1,
        )
        if output.shape[-1] != EXTREME_PARKOUR_OBSERVATION_DIM:
            raise RuntimeError(
                f"Extreme Parkour Teacher observation must be {EXTREME_PARKOUR_OBSERVATION_DIM}-D, "
                f"got {output.shape[-1]}."
            )

        # The current yaw and next-yaw entries are deliberately hidden from
        # the adaptation history.  On the first post-reset observations the
        # legacy code fills the internal history with the current frame after
        # emitting the zeroed history in ``output``.
        history_frame = current.clone()
        history_frame[:, 6:8] = 0.0
        just_reset = env.episode_length_buf <= 1
        if torch.any(just_reset):
            self.history[just_reset] = history_frame[just_reset, None, :].expand(-1, self.history_length, -1)
        continuing = ~just_reset
        if torch.any(continuing):
            self.history[continuing, :-1] = self.history[continuing, 1:].clone()
            self.history[continuing, -1] = history_frame[continuing]
        return output


class RecordExtremeParkourPhysicalParameters(ManagerTermBase):
    """Record the exact 29-D privileged randomization vector after startup."""

    def __init__(self, cfg, env: "ManagerBasedEnv"):
        super().__init__(cfg, env)
        self.asset_cfg: SceneEntityCfg = cfg.params["asset_cfg"]
        self.asset: Articulation = env.scene[self.asset_cfg.name]
        if len(self.asset_cfg.body_ids) != 1:
            raise ValueError("Extreme Parkour physical recorder requires exactly one base body.")
        if len(self.asset_cfg.joint_ids) != 12:
            raise ValueError("Extreme Parkour physical recorder requires twelve SDK-ordered joints.")
        self.base_body_id = self.asset_cfg.body_ids[0]
        self.default_com = self.asset.root_physx_view.get_coms().clone()

    def __call__(self, env: "ManagerBasedEnv", env_ids: torch.Tensor | None, asset_cfg: SceneEntityCfg) -> None:
        del asset_cfg
        if env_ids is None:
            env_ids = torch.arange(env.num_envs, device=env.device)
        env_ids_cpu = env_ids.to(device="cpu", dtype=torch.long)

        masses = self.asset.root_physx_view.get_masses()
        mass = masses[env_ids_cpu, self.base_body_id].to(env.device)
        # Rigid-body defaults are CPU tensors in Isaac Lab because PhysX mass
        # properties are authored through the CPU view.
        default_mass = self.asset.data.default_mass[env_ids_cpu, self.base_body_id].to(env.device)
        mass_offset = (mass - default_mass).unsqueeze(-1)

        coms = self.asset.root_physx_view.get_coms()
        com_offset = (
            coms[env_ids_cpu, self.base_body_id, :3] - self.default_com[env_ids_cpu, self.base_body_id, :3]
        ).to(env.device)
        materials = self.asset.root_physx_view.get_material_properties()
        friction = materials[env_ids_cpu, :, 0].mean(dim=1, keepdim=True).to(env.device)

        joint_ids = self.asset_cfg.joint_ids
        stiffness = self.asset.data.joint_stiffness[env_ids][:, joint_ids]
        damping = self.asset.data.joint_damping[env_ids][:, joint_ids]
        default_stiffness = self.asset.data.default_joint_stiffness[env_ids][:, joint_ids]
        default_damping = self.asset.data.default_joint_damping[env_ids][:, joint_ids]
        kp_offset = stiffness / default_stiffness.clamp_min(1.0e-6) - 1.0
        kd_offset = damping / default_damping.clamp_min(1.0e-6) - 1.0

        values = torch.cat((mass_offset, com_offset, friction, kp_offset, kd_offset), dim=-1)
        if values.shape[-1] != EXTREME_PARKOUR_PRIV_LATENT_DIM:
            raise RuntimeError(f"Expected 29 physical values, got {values.shape[-1]}.")
        if not hasattr(env, "_extreme_parkour_physical_parameters"):
            env._extreme_parkour_physical_parameters = torch.zeros(
                env.num_envs,
                EXTREME_PARKOUR_PRIV_LATENT_DIM,
                device=env.device,
            )
        env._extreme_parkour_physical_parameters[env_ids] = values
EXTREME_PARKOUR_V1_DEPTH_HEIGHT = 58
EXTREME_PARKOUR_V1_DEPTH_WIDTH = 87
EXTREME_PARKOUR_V1_DEPTH_BUFFER_LENGTH = 2


class ExtremeParkourV1StudentObservation(ManagerTermBase):
    """Return masked 53-D proprioception followed by ten masked history frames.

    The layout is deliberately identical to the Teacher's proprioception.  The
    current/next waypoint yaw entries (indices 6:8) are hidden because the
    visual encoder predicts them from depth.  History also keeps these entries
    at zero, matching the original Extreme Parkour visual Student protocol.
    """

    def __init__(self, cfg, env: "ManagerBasedEnv"):
        super().__init__(cfg, env)
        self.asset_cfg: SceneEntityCfg = cfg.params["asset_cfg"]
        self.contact_sensor_cfg: SceneEntityCfg = cfg.params["contact_sensor_cfg"]
        self.velocity_command_name = str(cfg.params.get("velocity_command_name", "base_velocity"))
        self.history_length = int(cfg.params.get("history_length", EXTREME_PARKOUR_HISTORY_LENGTH))
        if self.history_length != EXTREME_PARKOUR_HISTORY_LENGTH:
            raise ValueError("Extreme Parkour v1 Student requires exactly ten history frames.")

        self.asset: Articulation = env.scene[self.asset_cfg.name]
        self.contact_sensor: ContactSensor = env.scene.sensors[self.contact_sensor_cfg.name]
        if len(self.asset_cfg.joint_ids) != 12:
            raise RuntimeError("Extreme Parkour v1 Student requires twelve SDK-ordered joints.")
        if len(self.contact_sensor_cfg.body_ids) != 4:
            raise RuntimeError("Extreme Parkour v1 Student requires four SDK-ordered feet.")

        self.history = torch.zeros(
            env.num_envs,
            self.history_length,
            EXTREME_PARKOUR_PROPRIO_DIM,
            device=env.device,
        )

    def reset(self, env_ids: Sequence[int] | torch.Tensor | None = None) -> None:
        if env_ids is None:
            self.history.zero_()
        else:
            self.history[env_ids] = 0.0

    def _feet_contact(self) -> torch.Tensor:
        forces = self.contact_sensor.data.net_forces_w_history[:, :, self.contact_sensor_cfg.body_ids]
        contact = torch.any(torch.linalg.vector_norm(forces, dim=-1) > 2.0, dim=1)
        return contact.to(dtype=forces.dtype) - 0.5

    def _current_proprio(self, env: "ManagerBasedEnv") -> torch.Tensor:
        roll, pitch, _ = euler_xyz_from_quat(self.asset.data.root_quat_w)
        imu = torch.stack((wrap_to_pi(roll), wrap_to_pi(pitch)), dim=-1)
        speed_command = env.command_manager.get_command(self.velocity_command_name)[:, :1]

        is_flat = env.scene.terrain.terrain_types < 8
        terrain_class = torch.stack((~is_flat, is_flat), dim=-1).to(self.asset.data.joint_pos.dtype)
        joint_ids = self.asset_cfg.joint_ids
        joint_pos = self.asset.data.joint_pos[:, joint_ids] - self.asset.data.default_joint_pos[:, joint_ids]
        joint_vel = self.asset.data.joint_vel[:, joint_ids] * 0.05
        last_action = env.action_manager.action
        if last_action.shape[-1] != 12:
            raise RuntimeError(f"Extreme Parkour v1 Student requires a 12-D action, got {last_action.shape[-1]}.")

        zeros_yaw = torch.zeros(env.num_envs, 3, device=env.device, dtype=speed_command.dtype)
        zeros_command_xy = torch.zeros(env.num_envs, 2, device=env.device, dtype=speed_command.dtype)
        proprio = torch.cat(
            (
                self.asset.data.root_ang_vel_b * 0.25,
                imu,
                # index 5 is the legacy yaw placeholder; indices 6:8 are the
                # hidden current/next waypoint yaw values predicted by vision.
                zeros_yaw,
                zeros_command_xy,
                speed_command,
                terrain_class,
                joint_pos,
                joint_vel,
                last_action,
                self._feet_contact(),
            ),
            dim=-1,
        )
        if proprio.shape[-1] != EXTREME_PARKOUR_PROPRIO_DIM:
            raise RuntimeError(
                f"Extreme Parkour v1 Student proprio must be {EXTREME_PARKOUR_PROPRIO_DIM}-D, "
                f"got {proprio.shape[-1]}."
            )
        return proprio

    def __call__(
        self,
        env: "ManagerBasedEnv",
        asset_cfg: SceneEntityCfg,
        contact_sensor_cfg: SceneEntityCfg,
        velocity_command_name: str = "base_velocity",
        history_length: int = EXTREME_PARKOUR_HISTORY_LENGTH,
    ) -> torch.Tensor:
        del asset_cfg, contact_sensor_cfg, velocity_command_name, history_length
        current = self._current_proprio(env)
        output = torch.cat((current, self.history.reshape(env.num_envs, -1)), dim=-1)

        just_reset = env.episode_length_buf <= 1
        if torch.any(just_reset):
            self.history[just_reset] = current[just_reset, None, :].expand(-1, self.history_length, -1)
        continuing = ~just_reset
        if torch.any(continuing):
            self.history[continuing, :-1] = self.history[continuing, 1:].clone()
            self.history[continuing, -1] = current[continuing]
        return output


class ExtremeParkourV1DepthObservation(ManagerTermBase):
    """Reference 60x106 depth processing with a delayed 58x87 output frame."""

    def __init__(self, cfg, env: "ManagerBasedEnv"):
        super().__init__(cfg, env)
        self.sensor_cfg: SceneEntityCfg = cfg.params["sensor_cfg"]
        self.max_distance = float(cfg.params.get("max_distance", 2.0))
        self.update_interval = int(cfg.params.get("update_interval", 5))
        self.debug_vis = bool(cfg.params.get("debug_vis", False))
        self.debug_num_envs = int(cfg.params.get("debug_num_envs", 1))
        self.debug_grid_cols = int(cfg.params.get("debug_grid_cols", 1))
        self.debug_scale = int(cfg.params.get("debug_scale", 6))
        self.debug_output_path = Path(
            str(cfg.params.get("debug_output_path", "/tmp/go2_extreme_parkour_v1_depth.png"))
        ).expanduser()
        self.debug_start_env = int(cfg.params.get("debug_env_id", 0)) % env.num_envs
        self.debug_window_name = "Extreme Parkour v1 depth (processed 58x87)"
        if self.update_interval <= 0:
            raise ValueError("Depth update_interval must be positive.")
        if self.debug_num_envs <= 0:
            raise ValueError("debug_num_envs must be positive.")
        if self.debug_grid_cols <= 0:
            raise ValueError("debug_grid_cols must be positive.")
        if self.debug_scale <= 0:
            raise ValueError("debug_scale must be positive.")
        self._cv2 = None
        self._debug_frame_announced = False
        self._debug_uses_window = False
        if self.debug_vis:
            try:
                import cv2
            except ImportError as error:
                raise ImportError(
                    "Depth debug_vis=True requires opencv-python. Disable debug_vis for headless training."
                ) from error
            self._cv2 = cv2
            ui_backend = cv2.currentUIFramework() if hasattr(cv2, "currentUIFramework") else ""
            has_display = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
            self._debug_uses_window = has_display and ui_backend.lower() not in {"headless", "unknown", "none"}
            if self._debug_uses_window:
                try:
                    cv2.namedWindow(self.debug_window_name, cv2.WINDOW_NORMAL)
                    cv2.resizeWindow(
                        self.debug_window_name,
                        EXTREME_PARKOUR_V1_DEPTH_WIDTH * self.debug_scale,
                        EXTREME_PARKOUR_V1_DEPTH_HEIGHT * self.debug_scale,
                    )
                    cv2.moveWindow(self.debug_window_name, 40, 40)
                except cv2.error:
                    self._debug_uses_window = False
            if self._debug_uses_window:
                print(
                    "[INFO] Depth inspector enabled: OpenCV window "
                    f"'{self.debug_window_name}' (env {self.debug_start_env}, "
                    f"{self.debug_num_envs} image(s)/page)."
                )
            else:
                self.debug_output_path.parent.mkdir(parents=True, exist_ok=True)
                print(
                    "[INFO] Headless OpenCV detected: depth inspector will write PNG snapshots to "
                    f"{self.debug_output_path}. Open it in another terminal with: eog {self.debug_output_path}"
                )
        self.camera: RayCasterCamera = env.scene.sensors[self.sensor_cfg.name]
        self.buffer = torch.zeros(
            env.num_envs,
            EXTREME_PARKOUR_V1_DEPTH_BUFFER_LENGTH,
            EXTREME_PARKOUR_V1_DEPTH_HEIGHT,
            EXTREME_PARKOUR_V1_DEPTH_WIDTH,
            device=env.device,
        )

    def _process(self, depth: torch.Tensor) -> torch.Tensor:
        depth = torch.nan_to_num(depth, nan=self.max_distance, posinf=self.max_distance, neginf=0.0)
        depth = depth.clamp(0.0, self.max_distance)
        # Match Isaaclab_Parkour: remove the bottom two rows and four columns
        # from each side, then resize 58x98 to the 58x87 network input.
        depth = depth[:, :-2, 4:-4]
        depth = F.interpolate(
            depth.unsqueeze(1),
            size=(EXTREME_PARKOUR_V1_DEPTH_HEIGHT, EXTREME_PARKOUR_V1_DEPTH_WIDTH),
            mode="bicubic",
            align_corners=False,
        ).squeeze(1)
        return depth / self.max_distance - 0.5

    def _read(self, env_ids: torch.Tensor | None = None) -> torch.Tensor:
        depth = self.camera.data.output["distance_to_camera"].squeeze(-1)
        return self._process(depth if env_ids is None else depth[env_ids])

    def _show_debug_image(self) -> None:
        """Show the delayed, normalized image that the policy actually receives.

        The window accepts ``[`` and ``]`` to move backward/forward by one
        page of environments.  A page contains ``debug_num_envs`` images.
        """
        if not self.debug_vis or self._cv2 is None:
            return

        cv2 = self._cv2
        count = min(self.debug_num_envs, self.buffer.shape[0])
        env_ids = (torch.arange(count, device=self.buffer.device) + self.debug_start_env) % self.buffer.shape[0]
        frames = self.buffer[env_ids, 0]
        images = []
        for env_id, frame in zip(env_ids.tolist(), frames):
            # The network input is normalized to [-0.5, 0.5].  Convert only
            # for visualization; the tensor passed to the policy is unchanged.
            image = ((frame + 0.5).clamp(0.0, 1.0) * 255.0).to(torch.uint8).cpu().numpy()
            image = cv2.resize(
                image,
                (EXTREME_PARKOUR_V1_DEPTH_WIDTH * self.debug_scale, EXTREME_PARKOUR_V1_DEPTH_HEIGHT * self.debug_scale),
                interpolation=cv2.INTER_NEAREST,
            )
            image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
            cv2.putText(image, f"env {env_id}", (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 0), 1)
            images.append(image)

        rows = []
        for start in range(0, len(images), self.debug_grid_cols):
            row = images[start : start + self.debug_grid_cols]
            if len(row) < self.debug_grid_cols:
                blank = images[0] * 0
                row.extend([blank] * (self.debug_grid_cols - len(row)))
            rows.append(cv2.hconcat(row))
        mosaic = rows[0] if len(rows) == 1 else cv2.vconcat(rows)
        if self._debug_uses_window:
            cv2.imshow(self.debug_window_name, mosaic)
        else:
            if not cv2.imwrite(str(self.debug_output_path), mosaic):
                raise RuntimeError(f"Could not write depth debug image: {self.debug_output_path}")
        if not self._debug_frame_announced:
            if self._debug_uses_window:
                print(
                    "[INFO] Depth inspector is rendering the delayed normalized 58x87 policy input. "
                    "Focus the OpenCV window and press [ or ] to change environment page."
                )
            else:
                print("[INFO] First processed depth snapshot written successfully.")
            self._debug_frame_announced = True
        key = (cv2.waitKey(1) & 0xFF) if self._debug_uses_window else -1
        if key == ord("]"):
            self.debug_start_env = (self.debug_start_env + count) % self.buffer.shape[0]
        elif key == ord("["):
            self.debug_start_env = (self.debug_start_env - count) % self.buffer.shape[0]

    def reset(self, env_ids: Sequence[int] | torch.Tensor | None = None) -> None:
        if env_ids is None:
            ids = torch.arange(self.buffer.shape[0], device=self.buffer.device)
        else:
            ids = torch.as_tensor(env_ids, device=self.buffer.device, dtype=torch.long)
        processed = self._read(ids)
        self.buffer[ids] = processed[:, None].expand(-1, EXTREME_PARKOUR_V1_DEPTH_BUFFER_LENGTH, -1, -1)

    def __call__(
        self,
        env: "ManagerBasedEnv",
        sensor_cfg: SceneEntityCfg,
        max_distance: float = 2.0,
        update_interval: int = 5,
        debug_vis: bool = False,
        debug_env_id: int = 0,
        debug_num_envs: int = 1,
        debug_grid_cols: int = 1,
        debug_scale: int = 6,
        debug_output_path: str = "/tmp/go2_extreme_parkour_v1_depth.png",
    ) -> torch.Tensor:
        # Parameters are consumed during construction from ``cfg.params``.
        # They are repeated here because Isaac Lab validates every configured
        # ObservationTerm parameter against this callable's signature.
        del sensor_cfg, max_distance, update_interval, debug_vis, debug_env_id
        del debug_num_envs, debug_grid_cols, debug_scale, debug_output_path
        camera_updated = int(getattr(env, "common_step_counter", 0)) % self.update_interval == 0
        if camera_updated:
            processed = self._read()
            self.buffer[:, 0] = self.buffer[:, 1].clone()
            self.buffer[:, 1] = processed
        # Return the older frame to preserve the reference visual delay.
        if camera_updated:
            self._show_debug_image()
        return self.buffer[:, 0].flatten(start_dim=1)


def randomize_extreme_parkour_v1_camera_pitch(
    env: "ManagerBasedEnv",
    env_ids: torch.Tensor | None,
    sensor_cfg: SceneEntityCfg,
    pitch_range_deg: tuple[float, float] = (-5.0, 5.0),
    convention: str = "ros",
) -> None:
    """Apply the reference per-environment startup pitch perturbation."""

    camera: RayCasterCamera = env.scene.sensors[sensor_cfg.name]
    if env_ids is None:
        env_ids = torch.arange(env.num_envs, device=env.device, dtype=torch.long)
    base_quat = torch.tensor(camera.cfg.offset.rot, device=env.device).repeat(len(env_ids), 1)
    roll, pitch, yaw = euler_xyz_from_quat(base_quat)
    pitch_noise = torch.empty(len(env_ids), device=env.device).uniform_(
        math.radians(pitch_range_deg[0]),
        math.radians(pitch_range_deg[1]),
    )
    orientations = quat_from_euler_xyz(roll, pitch + pitch_noise, yaw)
    camera.set_world_poses(
        positions=None,
        orientations=orientations,
        convention=convention,
        env_ids=env_ids,
    )
