# Copyright (c) 2022-2025. Pure-Python path tracking (no torch / Isaac).
"""Velocity generation and exponential smoothing for factory-style mobile robots."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Tuple


def normalize_angle(angle: float) -> float:
    while angle > math.pi:
        angle -= 2 * math.pi
    while angle < -math.pi:
        angle += 2 * math.pi
    return angle


class RobotNavProfile(str, Enum):
    """Kinematic style for command generation (matches legacy EMOS robot_nav)."""

    M20 = "m20"
    SCOUT = "scout"
    CARTER_DIFF = "carter_diff"
    GENERIC = "generic"


@dataclass(frozen=True)
class NavTrackConfig:
    """Tunable navigation / smoothing (defaults match former factory_nav.py)."""

    nav_lookahead: float = 0.6
    nav_arrive_radius: float = 0.3
    nav_final_radius: float = 0.25
    arrival_dist: float = 0.3
    scout_arrival_dist: float = 0.1
    carter_max_speed: float = 0.9
    carter_turn_speed: float = 0.5
    carter_align_thresh: float = 0.3
    scout_max_speed: float = 0.9
    m20_max_lin_speed: float = 0.7
    m20_max_ang_speed: float = 0.8
    m20_kp_lin: float = 1.5
    m20_kp_ang: float = 1.0
    smooth_alpha_m20: float = 0.2
    smooth_alpha_carter: float = 0.35


def infer_profile(robot_name: str) -> RobotNavProfile:
    if robot_name.startswith("m20"):
        return RobotNavProfile.M20
    if robot_name == "scout_1":
        return RobotNavProfile.SCOUT
    if robot_name.startswith("carter"):
        return RobotNavProfile.CARTER_DIFF
    return RobotNavProfile.GENERIC


def smooth_velocity(
    last: Tuple[float, float, float],
    raw: Tuple[float, float, float],
    alpha: float,
) -> Tuple[float, float, float]:
    lx, ly, lz = last
    rx, ry, rz = raw
    a = alpha
    return (
        lx * (1 - a) + rx * a,
        ly * (1 - a) + ry * a,
        lz * (1 - a) + rz * a,
    )


def compute_raw_velocity_command(
    profile: RobotNavProfile,
    curr_x: float,
    curr_y: float,
    curr_yaw: float,
    target_x: float,
    target_y: float,
    *,
    cfg: NavTrackConfig,
) -> Tuple[float, float, float]:
    """Return desired body-frame (vx, vy, wz) for RSL-style velocity command input.

    Differential / Scout stack historically uses ``vx`` and ``wz``; ``vy`` may be ignored
    by some controllers (see eai-simulator controller configs).
    """
    global_dx = target_x - curr_x
    global_dy = target_y - curr_y
    dist = math.hypot(global_dx, global_dy)
    if dist < 1e-6:
        return (0.0, 0.0, 0.0)

    target_yaw = math.atan2(global_dy, global_dx)
    yaw_err = normalize_angle(target_yaw - curr_yaw)

    if profile in (RobotNavProfile.M20, RobotNavProfile.SCOUT):
        is_m20 = profile == RobotNavProfile.M20
        max_spd = cfg.m20_max_lin_speed if is_m20 else cfg.scout_max_speed
        kp = cfg.m20_kp_lin if is_m20 else 1.5
        tgt_spd = min(max_spd, dist * kp)
        if dist < 0.35:
            tgt_spd *= dist / 0.35

        cos_y = math.cos(curr_yaw)
        sin_y = math.sin(curr_yaw)
        local_dx = global_dx * cos_y + global_dy * sin_y
        local_dy = -global_dx * sin_y + global_dy * cos_y
        ln = math.sqrt(local_dx ** 2 + local_dy ** 2)
        raw_vx, raw_vy = 0.0, 0.0
        if ln > 1e-5:
            raw_vx = (local_dx / ln) * tgt_spd
            raw_vy = (local_dy / ln) * tgt_spd
        raw_wz = 0.0
        if profile == RobotNavProfile.M20:
            raw_wz = max(-cfg.m20_max_ang_speed, min(cfg.m20_max_ang_speed, yaw_err * cfg.m20_kp_ang))
        return (raw_vx, raw_vy, raw_wz)

    if profile == RobotNavProfile.CARTER_DIFF:
        if abs(yaw_err) > cfg.carter_align_thresh:
            return (0.0, 0.0, cfg.carter_turn_speed * (1.0 if yaw_err > 0 else -1.0))
        raw_vx = max(0.1, min(cfg.carter_max_speed, dist))
        raw_wz = max(-1.0, min(1.0, 2.0 * yaw_err))
        return (raw_vx, 0.0, raw_wz)

    raw_vx = max(0.1, min(cfg.carter_max_speed, dist))
    raw_wz = max(-1.0, min(1.0, 2.0 * yaw_err))
    return (raw_vx, 0.0, raw_wz)


def smooth_alpha_for_profile(profile: RobotNavProfile, cfg: NavTrackConfig) -> float:
    if profile in (RobotNavProfile.M20, RobotNavProfile.SCOUT):
        return cfg.smooth_alpha_m20
    return cfg.smooth_alpha_carter
