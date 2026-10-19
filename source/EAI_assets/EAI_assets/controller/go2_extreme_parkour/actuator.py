"""Unitree Go2 torque-speed actuator used by the Extreme Parkour policy."""

from __future__ import annotations

from dataclasses import MISSING

import torch
from isaaclab.actuators import DelayedPDActuator, DelayedPDActuatorCfg
from isaaclab.utils import configclass
from isaaclab.utils.types import ArticulationActions


class Go2ParkourActuator(DelayedPDActuator):
    """PD actuator with the Unitree torque-speed curve and motor friction."""

    cfg: "Go2ParkourActuatorCfg"

    def __init__(self, cfg: "Go2ParkourActuatorCfg", *args, **kwargs):
        super().__init__(cfg, *args, **kwargs)
        self._joint_vel = torch.zeros_like(self.computed_effort)
        self._effort_y1 = self._parse_joint_parameter(cfg.Y1, 1.0e9)
        self._effort_y2 = self._parse_joint_parameter(cfg.Y2, cfg.Y1)
        self._velocity_x1 = self._parse_joint_parameter(cfg.X1, 1.0e9)
        self._velocity_x2 = self._parse_joint_parameter(cfg.X2, 1.0e9)
        self._friction_static = self._parse_joint_parameter(cfg.Fs, 0.0)
        self._friction_dynamic = self._parse_joint_parameter(cfg.Fd, 0.0)
        self._activation_vel = self._parse_joint_parameter(cfg.Va, 0.01)

    def compute(
        self,
        control_action: ArticulationActions,
        joint_pos: torch.Tensor,
        joint_vel: torch.Tensor,
    ) -> ArticulationActions:
        self._joint_vel[:] = joint_vel
        control_action = super().compute(control_action, joint_pos, joint_vel)
        self.applied_effort -= (
            self._friction_static * torch.tanh(joint_vel / self._activation_vel)
            + self._friction_dynamic * joint_vel
        )
        control_action.joint_positions = None
        control_action.joint_velocities = None
        control_action.joint_efforts = self.applied_effort
        return control_action

    def _clip_effort(self, effort: torch.Tensor) -> torch.Tensor:
        same_direction = (self._joint_vel * effort) > 0
        max_effort = torch.where(same_direction, self._effort_y1, self._effort_y2)
        max_effort = torch.where(
            self._joint_vel.abs() < self._velocity_x1,
            max_effort,
            self._compute_effort_limit(max_effort),
        )
        return torch.clip(effort, -max_effort, max_effort)

    def _compute_effort_limit(self, max_effort: torch.Tensor) -> torch.Tensor:
        slope = -max_effort / (self._velocity_x2 - self._velocity_x1)
        limit = slope * (self._joint_vel.abs() - self._velocity_x1) + max_effort
        return limit.clamp_min(0.0)


@configclass
class Go2ParkourActuatorCfg(DelayedPDActuatorCfg):
    """Configuration for a Unitree-style torque-speed actuator."""

    class_type: type = Go2ParkourActuator
    X1: float = 13.5
    X2: float = 30.0
    Y1: dict[str, float] | float = MISSING
    Y2: dict[str, float] | float | None = None
    Fs: float = 0.0
    Fd: float = 0.0
    Va: float = 0.01


GO2_EXTREME_PARKOUR_ACTUATOR_CFG = Go2ParkourActuatorCfg(
    joint_names_expr=[".*_hip_joint", ".*_thigh_joint", ".*_calf_joint"],
    stiffness=40.0,
    damping=1.0,
    friction=0.01,
    Y1={
        ".*_hip_joint": 35.0,
        ".*_thigh_joint": 40.0,
        ".*_calf_joint": 40.0,
    },
    Y2={
        ".*_hip_joint": 35.0,
        ".*_thigh_joint": 45.0,
        ".*_calf_joint": 45.0,
    },
)
