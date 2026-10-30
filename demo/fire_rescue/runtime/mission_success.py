# Copyright (c) 2022-2025. Unified factory mission success (triple baseline).
"""三组对照共用的任务成功判定。"""
from __future__ import annotations

import math
from typing import Any, List, Optional, Tuple


def _dist_xy_to_fire(
    base_env: Any,
    robot_name: str,
    fire_xy: Tuple[float, float],
) -> Optional[float]:
    from .sim_helpers import get_robot_pos

    try:
        px, py, _ = get_robot_pos(base_env, robot_name)
        return float(math.hypot(float(px) - float(fire_xy[0]), float(py) - float(fire_xy[1])))
    except Exception:
        return None


def unified_mission_success(
    *,
    base_env: Any,
    emos_agents: List[str],
    fire_xy: Optional[Tuple[float, float]],
    extinguisher_robot: Optional[str],
    extinguisher_task1_complete: bool,
    rescue_channel_complete: bool,
    emergency_task_required: bool = False,
    emergency_task_complete: bool = True,
    fire_radius_m: float = 3.0,
) -> bool:
    """拿到灭火器 + 救援通道按钮 + 必要突发任务 + ≥3 台/灭火器车在 3m。"""
    if fire_xy is None:
        return False
    if not rescue_channel_complete or not extinguisher_task1_complete:
        return False
    if emergency_task_required and not emergency_task_complete:
        return False
    if not extinguisher_robot or extinguisher_robot not in emos_agents:
        return False
    ext_d = _dist_xy_to_fire(base_env, extinguisher_robot, (fire_xy[0], fire_xy[1]))
    if ext_d is None or ext_d > fire_radius_m:
        return False
    in_cnt = 0
    for rn in emos_agents:
        d = _dist_xy_to_fire(base_env, rn, (fire_xy[0], fire_xy[1]))
        if d is not None and d <= fire_radius_m:
            in_cnt += 1
    return in_cnt >= 3
