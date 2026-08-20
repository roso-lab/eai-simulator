"""工厂 EMOS 仿真 — UR5 机械臂统一管理

管理 M20_1 / Scout_1 上的 UR5 臂：
  - 内部任务关节控制
  - 自动抓取控制器（UR5AutoPickController）
  - Scout_1 Key5 两段关节序列
  - 灭火器粘连抓取（M20 到达目标后 UR5 伸出 → 碰触 → FixedJoint 粘连）
"""

import os
import re
import time
from typing import Callable, Dict, Optional, Tuple

import torch

from .settings import (
    M20_UR5_JOINT_NAMES,
    SCOUT1_KEY5_ARM_POSE_1,
    SCOUT1_KEY5_ARM_POSE_2,
    SCOUT1_KEY5_ARM_STAGE_DELAY,
    FIRE_EXTINGUISHER_ARM_TARGET,
    FIRE_EXTINGUISHER_GRAB_DISTANCE,
    FIRE_EXTINGUISHER_SEARCH_RADIUS,
    FIRE_EXTINGUISHER_PREALIGN_DIST,
    FIRE_EXTINGUISHER_GRAB_MASS,
    FIRE_EXTINGUISHER_ATTACH_MODE,
    GENERIC_GRAB_DISTANCE,
)

# ═══════════════════════════════════════════════════════════════════════════════
# UR5AutoPickController（原 ur5_auto_pick_controller.py，与 UR5Manager 同模块）
# ═══════════════════════════════════════════════════════════════════════════════

# Copyright (c) 2022-2025, The Isaac Lab Project Developers.
# SPDX-License-Identifier: BSD-3-Clause
"""
UR5 机械臂自动抓取固定点方块控制器。

通过关节空间关键帧序列：HOME -> 方块上方 -> 下压 -> 贴近时创建 FixedJoint 抓取 -> 抬起。
关节名与 control window 一致：shoulder_pan_joint, shoulder_lift_joint, elbow_joint,
wrist_1_joint, wrist_2_joint, wrist_3_joint。末端 link：wrist_3_link。
"""

import math
from typing import Any, List, Optional, Tuple

import torch

from .sim_helpers import get_yaw_from_quat
from .ur5_paths import matches_robot_ee_path

# 与 ur5_control_window / goal_factory 一致：M20 上的 UR5
UR5_JOINT_NAMES_M20 = [
    "shoulder_pan_joint",
    "shoulder_lift_joint",
    "elbow_joint",
    "wrist_1_joint",
    "wrist_2_joint",
    "wrist_3_joint",
]

def _get_ur5_joint_names(robot_name: str):
    """UR5 关节名列表（与 M20 / Scout_1 合并关节一致）。

    Factory 中 Scout_1 使用 ``scout.py`` 的 SCOUT_UR5_CFG：UR5 经 FixedJoint 并入底盘，
    关节名为 ``shoulder_pan_joint`` 等（见 ``SCOUT_UR5_JOINT_NAMES``），与 M20 相同。
    """
    return UR5_JOINT_NAMES_M20
EE_LINK_NAME = "wrist_3_link"
CUBE_PRIM_PATH = "/World/Cube"
GRASP_DISTANCE_THRESHOLD = 0.08  # 末端与方块中心距离小于此值则执行抓取（FixedJoint）


def resolve_ur5_articulation(articulations, robot_name: str):
    """Resolve the mounted arm, with compatibility for legacy merged assets."""
    if not articulations:
        return None
    arm_name = f"{robot_name}_arm"
    if arm_name in articulations:
        return articulations[arm_name]
    if robot_name in articulations:
        return articulations[robot_name]
    return None


# 关键帧关节角（弧度），针对固定方块位置 (0.6, 0, 0.45) 调参；无 IK 时用 j0=朝向目标
POSE_HOME = [0.0, -1.57, 1.57, -1.57, -1.57, 0.0]
POSE_ABOVE_CUBE = [0.5, -0.7, 1.2, -2.0, -1.57, 0.0]
POSE_AT_CUBE = [0.55, -0.35, 0.7, -2.2, -1.57, 0.0]
# M20 的 UR5 安装方向下，资产默认姿态会让末端降到接近地面。
# 肩部竖起、肘部收拢后，末端位于底盘上方且水平包络较小；j0 仍保留抓取朝向。
POSE_CARRY = [0.0, -1.57, 0.0, -1.57, -1.57, 0.0]
J0_LIMIT = 3.14  # shoulder_pan 限位约 ±π，无 IK 时用 j0 朝向方块

# 墙面按钮按压专用关键帧：手臂水平伸展（加长伸展距离以适配停靠较远时的触及需求）
POSE_BUTTON_APPROACH = [0.0, -1.25, 0.30, -0.65, -1.57, 0.0]   # 手臂接近水平伸向按钮
POSE_BUTTON_PRESS   = [0.0, -1.15, 0.05, -0.50, -1.57, 0.0]    # 最大水平伸展按压
POSE_BUTTON_RETRACT = [0.0, -1.45, 0.80, -0.95, -1.57, 0.0]    # 回收手臂

# UR5 关节限位 (rad)：与 URDF 一致，肘关节 j2 为 ±π，避免轨迹超限卡住
UR5_JOINT_LIMITS = [
    (-2 * math.pi, 2 * math.pi),   # j0 shoulder_pan
    (-2 * math.pi, 2 * math.pi),   # j1 shoulder_lift
    (-math.pi, math.pi),            # j2 elbow
    (-2 * math.pi, 2 * math.pi),   # j3 wrist_1
    (-2 * math.pi, 2 * math.pi),   # j4 wrist_2
    (-2 * math.pi, 2 * math.pi),   # j5 wrist_3
]

# REACH_ABOVE -> LOWER -> GRASP -> LIFT（无 IK 时从 POSE_HOME 到目标上方再下压）
PHASE_STEPS = [25, 20, 15, 25]
# 墙面按钮「按压」：略缩短各段步数，便于更快看到伸臂与到位
BUTTON_PRESS_PHASE_STEPS = [14, 10, 8, 14]
PHASE_NAMES = ["REACH_ABOVE", "LOWER", "GRASP", "LIFT"]


def _log_joint_target(phase: int, step: int, steps_total: int, pose: List[float], phase_name: str = None) -> None:
    """打印相位首尾的 6 个关节目标，避免每帧日志拖慢 Isaac 主循环。"""
    if step not in (0, steps_total):
        return
    name = phase_name or (PHASE_NAMES[phase] if phase < len(PHASE_NAMES) else "?")
    j = pose[:6]
    print(f"[UR5AutoPick] {name} phase={phase} step={step}/{steps_total} | "
          f"j0={j[0]:.3f} j1={j[1]:.3f} j2={j[2]:.3f} j3={j[3]:.3f} j4={j[4]:.3f} j5={j[5]:.3f}")


def _interp(a: float, p0: List[float], p1: List[float]) -> List[float]:
    return [(1 - a) * x + a * y for x, y in zip(p0, p1)]


def _clamp_ur5_pose(pose: List[float]) -> List[float]:
    """将 6 关节角裁剪到 UR5 限位内，避免超限卡住。"""
    out = []
    for i, q in enumerate(pose[:6]):
        lo, hi = UR5_JOINT_LIMITS[i]
        out.append(max(lo, min(hi, float(q))))
    return out


def get_cube_pos(stage) -> Optional[Tuple[float, float, float]]:
    """从 USD 读取 /World/Cube 的世界坐标。"""
    try:
        from pxr import UsdGeom
        prim = stage.GetPrimAtPath(CUBE_PRIM_PATH)
        if not prim.IsValid():
            return None
        xform = UsdGeom.Xformable(prim)
        world_xf = xform.ComputeLocalToWorldTransform(0)
        pos = world_xf.ExtractTranslation()
        return (float(pos[0]), float(pos[1]), float(pos[2]))
    except Exception:
        return None


def get_ee_pos(robot) -> Optional[Tuple[float, float, float]]:
    """获取 wrist_3_link 的世界坐标，兼容不同资产层级下的名称前缀。"""
    try:
        bid, _ = robot.find_bodies(EE_LINK_NAME, preserve_order=True)
        if not bid:
            bid, _ = robot.find_bodies(".*wrist_3_link$", preserve_order=True)
        if not bid:
            return None
        pos = robot.data.body_pos_w[0, bid[0]]
        return (pos[0].item(), pos[1].item(), pos[2].item())
    except Exception:
        return None


def get_ee_prim_path(stage, robot_name: str = "m20_1") -> Optional[str]:
    """获取 wrist_3_link 的 USD prim 路径（用于创建 FixedJoint）。"""
    try:
        import isaacsim.core.utils.prims as prim_utils

        arm_hint = f"{robot_name.casefold()}_arm"

        # 1) 直接路径（当前 Builder 使用小写实例名，如 m20_2_arm）。
        direct = prim_utils.find_matching_prim_paths(f".*/{arm_hint}/wrist_3_link$")
        for path in direct:
            if matches_robot_ee_path(path, robot_name):
                return path

        # 2) 中间带层级。
        nested = prim_utils.find_matching_prim_paths(f".*/{arm_hint}/.*/wrist_3_link$")
        for path in nested:
            if matches_robot_ee_path(path, robot_name):
                return path

        # 3) 广搜 wrist_3_link
        paths = prim_utils.find_matching_prim_paths(".*wrist_3_link$")
        for path in paths:
            if matches_robot_ee_path(path, robot_name):
                return path

        # 4) 兜底：遍历 /World 下所有 prim，手动匹配
        if stage is not None:
            root = stage.GetPrimAtPath("/World")

            def _walk(prim):
                if not prim.IsValid():
                    return None
                p = str(prim.GetPath())
                if matches_robot_ee_path(p, robot_name):
                    return p
                for c in prim.GetChildren():
                    found = _walk(c)
                    if found:
                        return found
                return None

            if root.IsValid():
                found = _walk(root)
                if found:
                    return found
    except Exception:
        pass
    return None


class UR5AutoPickController:
    """
    状态机：IDLE -> REACH_ABOVE -> LOWER -> GRASP -> LIFT -> DONE。
    每帧调用 step() 返回 (joint_target_6, should_grasp)。
    若提供 ik_function，则根据当前方块位置（每轮开始时从 USD 读取）用 IK 计算「上方/贴近」关节角，否则用固定关键帧。
    """

    def __init__(self, robot_name: str = "m20_1", ik_function=None):
        self.robot_name = robot_name
        self.ik_function = ik_function  # 可选：(target_pos, target_quat, current_joint_pos, robot_base_pos, robot_base_quat) -> joint_angles
        self.active = False
        self.phase = 0
        self._step = 0
        self.grabbed = False
        self._arm_joint_ids = None
        self._ee_body_id = None
        self._device = None
        self._target_cube_pos = None  # 本轮抓取目标（世界坐标）：窗口指定或从 stage /World/Cube 读取）
        self._ik_pose_above = None  # IK 解：目标上方
        self._ik_pose_at = None     # IK 解：贴近目标
        self._phase_steps = list(PHASE_STEPS)
        self._button_press_mode = False
        self.carry_pose: Optional[List[float]] = None

    def start_pick(
        self,
        stage=None,
        target_pos: Optional[tuple[float, float, float]] = None,
        *,
        button_press_mode: bool = False,
    ) -> None:
        if not self.active:
            self.active = True
            self.phase = 0
            self._step = 0
            self.grabbed = False
            self._ik_pose_above = None
            self._ik_pose_at = None
            self.carry_pose = None
            self._button_press_mode = bool(button_press_mode)
            self._phase_steps = (
                list(BUTTON_PRESS_PHASE_STEPS) if button_press_mode else list(PHASE_STEPS)
            )
            if target_pos is not None:
                # 允许外部直接指定世界坐标目标点（例如固定抓取点）
                tx, ty, tz = target_pos
                self._target_cube_pos = (float(tx), float(ty), float(tz))
            elif stage is not None:
                self._target_cube_pos = get_cube_pos(stage)
            else:
                self._target_cube_pos = None
            print("[UR5AutoPick] ▶ 开始自动抓取序列")
            if button_press_mode:
                print("[UR5AutoPick] 墙面按钮按压模式（缩短相位步数）")
            if self._target_cube_pos is not None:
                cx, cy, cz = self._target_cube_pos
                print(f"[UR5AutoPick] 目标位置 (世界坐标): x={cx:.4f}, y={cy:.4f}, z={cz:.4f}（无 IK 时仅 j0 朝向目标，末端可能有偏差）")
            else:
                print("[UR5AutoPick] 未指定目标位置，将使用固定关键帧")

    def step(
        self,
        env: Any,
        stage: Any,
    ) -> Tuple[Optional[torch.Tensor], bool]:
        """
        返回 (joint_target, should_grasp)。
        joint_target: (1, 6) 或 None（不更新关节时）;
        should_grasp: 本帧是否应创建 FixedJoint。
        """
        should_grasp = False
        scene = getattr(env, "scene", None) or (getattr(env, "unwrapped", None) and getattr(env.unwrapped, "scene", None))
        articulations = getattr(scene, "articulations", None) if scene else None
        robot = resolve_ur5_articulation(articulations, self.robot_name)
        if robot is None:
            return None, False
        # 至少需要找到 6 个 UR5 关节（兼容不同机器人的合并关节命名）
        if getattr(robot, "num_joints", 0) < 6:
            return None, False
        if self._device is None:
            self._device = robot.device
        if self._arm_joint_ids is None:
            joint_names = _get_ur5_joint_names(self.robot_name)
            try:
                arm_ids, _ = robot.find_joints(joint_names, preserve_order=True)
            except Exception:
                return None, False
            if arm_ids is None or len(arm_ids) < 6:
                return None, False
            self._arm_joint_ids = arm_ids[:6]

        if not self.active:
            return None, False

        # 每轮开始时更新抓取目标（若未指定则从 stage 读 /World/Cube）并计算 IK/朝向
        if self.phase == 0 and self._step == 0:
            if self._target_cube_pos is None and stage is not None:
                self._target_cube_pos = get_cube_pos(stage)
            if self._target_cube_pos is not None:
                cx, cy, cz = self._target_cube_pos
                if self.ik_function is not None:
                    try:
                        import numpy as np
                        base_pos = robot.data.root_pos_w[0].cpu().numpy().copy()
                        base_quat = robot.data.root_quat_w[0].cpu().numpy().copy()  # w,x,y,z
                        base_pos[2] += 0.08
                        cur = robot.data.joint_pos[0, self._arm_joint_ids].cpu().numpy()
                        quat_down = np.array([0.0, 0.707, 0.0, 0.707])
                        pose_above = self.ik_function(
                            np.array([cx, cy, cz + 0.08]),
                            quat_down, cur, base_pos, base_quat
                        )
                        pose_at = self.ik_function(
                            np.array([cx, cy, cz]),
                            quat_down, cur, base_pos, base_quat
                        )
                        if pose_above is not None and pose_at is not None and len(pose_above) >= 6 and len(pose_at) >= 6:
                            self._ik_pose_above = _clamp_ur5_pose(list(pose_above[:6]))
                            self._ik_pose_at = _clamp_ur5_pose(list(pose_at[:6]))
                        else:
                            self._ik_pose_above = None
                            self._ik_pose_at = None
                    except Exception:
                        self._ik_pose_above = None
                        self._ik_pose_at = None
                else:
                    # 无 IK：用 shoulder_pan (j0) 朝向目标在「底盘水平面」内的方向，其余关节用固定关键帧。
                    # 必须用世界位移按底盘 yaw 旋到基座坐标再 atan2；否则底盘 yaw≠0 时 j0 会指错（救援按钮常见）。
                    base = robot.data.root_pos_w[0]
                    bx, by = base[0].item(), base[1].item()
                    bz = base[2].item()
                    wx, wy = cx - bx, cy - by
                    quat = robot.data.root_quat_w[0]
                    yaw = get_yaw_from_quat(quat)
                    local_dx = math.cos(yaw) * wx + math.sin(yaw) * wy
                    local_dy = -math.sin(yaw) * wx + math.cos(yaw) * wy
                    j0 = math.atan2(local_dy, local_dx)
                    j0 = max(-J0_LIMIT, min(J0_LIMIT, j0))
                    if self._button_press_mode:
                        self._ik_pose_above = [j0] + list(POSE_BUTTON_APPROACH[1:])
                        self._ik_pose_at = [j0] + list(POSE_BUTTON_PRESS[1:])
                        print(
                            f"[UR5AutoPick] 🔘 按钮按压模式（水平伸展关键帧），j0={j0:.3f} "
                            f"(base=({bx:.2f},{by:.2f},{bz:.2f}) yaw={yaw:.3f} "
                            f"target=({cx:.2f},{cy:.2f},{cz:.2f}) "
                            f"local_dx={local_dx:.3f} local_dy={local_dy:.3f})"
                        )
                    else:
                        self._ik_pose_above = [j0] + list(POSE_ABOVE_CUBE[1:])
                        self._ik_pose_at = [j0] + list(POSE_AT_CUBE[1:])
                        print(
                            f"[UR5AutoPick] 无 IK，使用 j0={j0:.3f} 朝向目标 "
                            f"(base_yaw={yaw:.3f} local_dx={local_dx:.3f} local_dy={local_dy:.3f})"
                        )

        if self.phase >= len(self._phase_steps):
            self.active = False
            if self._button_press_mode:
                ee_pos = self._get_ee_pos(robot)
                ee_str = f"ee=({ee_pos[0]:.3f},{ee_pos[1]:.3f},{ee_pos[2]:.3f})" if ee_pos else "ee=None"
                tgt = self._target_cube_pos
                tgt_str = f"target=({tgt[0]:.3f},{tgt[1]:.3f},{tgt[2]:.3f})" if tgt else "target=None"
                print(f"[UR5AutoPick] ✅ 按钮按压序列完成 {ee_str} {tgt_str}")
            else:
                print("[UR5AutoPick] ✅ 抓取序列完成")
            return None, False

        steps_total = self._phase_steps[self.phase]
        alpha = self._step / max(1, steps_total)
        p0_above = self._ik_pose_above if self._ik_pose_above is not None else POSE_ABOVE_CUBE
        p1_at = self._ik_pose_at if self._ik_pose_at is not None else POSE_AT_CUBE
        if self.phase == 0:
            p0, p1 = POSE_HOME, p0_above
        elif self.phase == 1:
            p0, p1 = p0_above, p1_at
        elif self.phase == 2:
            # 保持「贴近目标」姿态；与目标点距离小于阈值时触发抓取（目标点=窗口指定或 /World/Cube）
            pose = _clamp_ur5_pose(p1_at)
            ee_pos = self._get_ee_pos(robot)
            target_pos = self._target_cube_pos if self._target_cube_pos is not None else get_cube_pos(stage)
            if ee_pos and target_pos:
                dx = ee_pos[0] - target_pos[0]
                dy = ee_pos[1] - target_pos[1]
                dz = ee_pos[2] - target_pos[2]
                dist = (dx * dx + dy * dy + dz * dz) ** 0.5
                _thresh = (
                    max(GRASP_DISTANCE_THRESHOLD, 0.22)
                    if getattr(self, "_button_press_mode", False)
                    else GRASP_DISTANCE_THRESHOLD
                )
                if dist < _thresh and not self.grabbed:
                    should_grasp = True
                    self.grabbed = True
                    print(f"[UR5AutoPick] 贴近目标 (dist={dist:.3f})，创建 FixedJoint")
            joint_target = torch.tensor([pose], device=self._device, dtype=torch.float32)
            _log_joint_target(self.phase, self._step, steps_total, pose, phase_name="GRASP")
            self._step += 1
            if self._step > steps_total:
                self.phase += 1
                self._step = 0
            return joint_target, should_grasp
        else:
            # phase == 3: LIFT / RETRACT
            if self._button_press_mode:
                p1_lift = [p1_at[0]] + list(POSE_BUTTON_RETRACT[1:]) if len(p1_at) >= 1 else POSE_BUTTON_RETRACT
            else:
                p1_lift = [p1_at[0]] + list(POSE_CARRY[1:]) if len(p1_at) >= 1 else POSE_CARRY
                self.carry_pose = _clamp_ur5_pose(p1_lift)
            p0, p1 = p1_at, p1_lift

        pose = _interp(min(1.0, alpha), p0, p1)
        pose = _clamp_ur5_pose(pose)
        joint_target = torch.tensor([pose], device=self._device, dtype=torch.float32)
        phase_name = PHASE_NAMES[self.phase] if self.phase < len(PHASE_NAMES) else "?"
        _log_joint_target(self.phase, self._step, steps_total, pose, phase_name=phase_name)
        self._step += 1
        if self._step > steps_total:
            self.phase += 1
            self._step = 0
        return joint_target, should_grasp

    def _get_ee_pos(self, robot) -> Optional[Tuple[float, float, float]]:
        try:
            if self._ee_body_id is None:
                bid, _ = robot.find_bodies(EE_LINK_NAME, preserve_order=True)
                if not bid:
                    bid, _ = robot.find_bodies(".*wrist_3_link$", preserve_order=True)
                if not bid:
                    return None
                self._ee_body_id = int(bid[0])
            pos = robot.data.body_pos_w[0, self._ee_body_id]
            return (pos[0].item(), pos[1].item(), pos[2].item())
        except Exception:
            self._ee_body_id = None
            return None


FIRE_EXTINGUISHER_PRIM_KEYWORDS = (
    "SM_FireExtinguisher_02",
    "FireExtinguisher",
    "Extinguisher",
)


class UR5Manager:
    def __init__(
        self,
        base_env,
        *,
        enable_extinguisher_visual_follow: bool = True,
        enable_extinguisher_stage_attach: bool = True,
    ):
        self._env = base_env
        self.enable_extinguisher_visual_follow = bool(enable_extinguisher_visual_follow)
        self.enable_extinguisher_stage_attach = bool(enable_extinguisher_stage_attach)
        # scout_1 key5 两段关节序列状态
        self.scout1_arm_stage = 0
        self.scout1_arm_arrival_time = None

        # 自动抓取控制器
        self.m20_1_pick = UR5AutoPickController(robot_name="m20_1", ik_function=None)
        self.scout_1_pick = UR5AutoPickController(robot_name="scout_1", ik_function=None)

        # 关节 ID 缓存（避免每帧 find_joints）
        self._jid_cache: dict = {}
        self._ee_body_cache: dict = {}

        # ── 灭火器粘连抓取状态 ─────────────────────────────────────────────
        self._ext_robot_name: str = ""
        self._ext_active: bool = False
        self._ext_grabbed: bool = False
        self._ext_joint_path: Optional[str] = None
        self._ext_pick = None  # UR5AutoPickController
        self._ext_target_prim: Optional[str] = None
        self._ext_target_pos: Optional[Tuple[float, float, float]] = None
        self._ext_prealign_pending: bool = False
        self._ext_follow_mode: bool = False
        self._ext_debug_counter: int = 0
        self._ext_carry_hold_logged: bool = False

        # ── 灭火器搬运中断与恢复状态 ────────────────────────────────────────
        self.is_carrying_extinguisher: bool = False          # 已抓取且正在搬运
        self._ext_drop_positions: Dict[str, Tuple[float, float, float]] = {}  # 就地放下坐标
        self._ext_interrupt_mode: bool = False               # 正在执行就地放下
        self._ext_interrupt_drop_pos: Optional[Tuple] = None
        self._ext_interrupt_start_time: float = 0.0
        self._ext_drop_callback = None                       # 灭火器放下事件回调
        self._interrupt_rescue_callback = None               # 中断超时降级回调

        # ── 通用「靠近自动粘连」状态（键盘遥操作使用）──────────────────────────
        self._generic_grab_active: bool = False
        self._generic_grab_joint_path: Optional[str] = None
        self._generic_grab_target_prim: Optional[str] = None
        self._generic_grab_follow_mode: bool = False

        # 障碍物救援：机械臂伸向障碍物（先于视觉代理粘连）
        self._obstacle_rescue_reach_pick = None
        self._obstacle_rescue_reach_robot: str = ""

        # 救援通道按钮按压：由任务分配机器人动态执行，不再固定 scout_1。
        self._button_press_pick = None
        self._button_press_robot: str = ""

    # ── 障碍物救援：臂伸向障碍物 ───────────────────────────────────────────────

    def start_obstacle_rescue_reach(self, robot_name: str, obstacle_world_pos: Tuple[float, float, float]) -> None:

        self.end_obstacle_rescue_reach()
        self._obstacle_rescue_reach_robot = robot_name
        self._obstacle_rescue_reach_pick = UR5AutoPickController(robot_name=robot_name, ik_function=None)
        self._obstacle_rescue_reach_pick.start_pick(stage=None, target_pos=tuple(obstacle_world_pos))
        print(
            f"[ObstacleRescue] UR5 伸向障碍物: {robot_name} -> "
            f"({obstacle_world_pos[0]:.2f}, {obstacle_world_pos[1]:.2f}, {obstacle_world_pos[2]:.2f})",
            flush=True,
        )

    def update_obstacle_rescue_reach(self, stage) -> None:
        if not self._obstacle_rescue_reach_pick or not self._obstacle_rescue_reach_pick.active:
            return
        robot_name = self._obstacle_rescue_reach_robot
        # 绿色「打开救援通道」与清障共用同一机械臂时，优先推进救援通道序列。
        if self.is_button_press_active(robot_name):
            return
        articulations = getattr(self._env.scene, "articulations", None)
        robot = resolve_ur5_articulation(articulations, robot_name)
        if robot is None:
            return
        jt, _ = self._obstacle_rescue_reach_pick.step(self._env, stage)
        if jt is None:
            return
        arm_ids = self._get_arm_ids(robot, robot_name)
        if arm_ids:
            try:
                robot.set_joint_position_target(jt, joint_ids=arm_ids)
                robot.write_data_to_sim()
            except Exception:
                pass

    def end_obstacle_rescue_reach(self) -> None:
        self._obstacle_rescue_reach_pick = None
        self._obstacle_rescue_reach_robot = ""

    # ── 救援通道按钮按压 ───────────────────────────────────────────────────────

    def start_button_press(self, robot_name: str, button_world_pos: Tuple[float, float, float]) -> None:
        self.end_button_press()
        self._button_press_robot = str(robot_name)
        self._button_press_pick = UR5AutoPickController(robot_name=self._button_press_robot, ik_function=None)
        self._button_press_pick.start_pick(
            stage=None,
            target_pos=tuple(button_world_pos),
            button_press_mode=True,
        )
        print(
            f"[ButtonPress] {self._button_press_robot} 开始救援通道按钮按压 -> "
            f"({button_world_pos[0]:.2f}, {button_world_pos[1]:.2f}, {button_world_pos[2]:.2f})",
            flush=True,
        )

    def update_button_press(self, stage) -> None:
        if not self._button_press_pick or not self._button_press_pick.active:
            return
        robot_name = self._button_press_robot
        articulations = getattr(self._env.scene, "articulations", None)
        robot = resolve_ur5_articulation(articulations, robot_name)
        if robot is None:
            return
        jt, _ = self._button_press_pick.step(self._env, stage)
        if jt is None:
            return
        arm_ids = self._get_arm_ids(robot, robot_name)
        if arm_ids:
            try:
                robot.set_joint_position_target(jt, joint_ids=arm_ids)
                robot.write_data_to_sim()
            except Exception:
                pass
        if robot_name == "scout_1":
            setattr(self._env, "_scout1_ur5_override", True)

    def end_button_press(self) -> None:
        self._button_press_pick = None
        self._button_press_robot = ""

    def is_button_press_active(self, robot_name: str | None = None) -> bool:
        active = self._button_press_pick is not None and self._button_press_pick.active
        if not active:
            return False
        if robot_name is None:
            return True
        return str(robot_name) == self._button_press_robot

    @property
    def is_obstacle_rescue_reach_active(self) -> bool:
        return (
            self._obstacle_rescue_reach_pick is not None
            and self._obstacle_rescue_reach_pick.active
        )

    # ── 关节 ID 缓存 ─────────────────────────────────────────────────────────

    def _get_arm_ids(self, robot, name):
        if name in self._jid_cache:
            return self._jid_cache[name]
        try:
            ids, _ = robot.find_joints(M20_UR5_JOINT_NAMES, preserve_order=True)
            if ids and len(ids) >= 6:
                self._jid_cache[name] = ids[:6]
                return ids[:6]
        except Exception:
            pass
        return None

    def _get_ee_pos_cached(self, robot, robot_name: str) -> Optional[Tuple[float, float, float]]:
        try:
            body_id = self._ee_body_cache.get(robot_name)
            if body_id is None:
                bid, _ = robot.find_bodies(EE_LINK_NAME, preserve_order=True)
                if not bid:
                    bid, _ = robot.find_bodies(".*wrist_3_link$", preserve_order=True)
                if not bid:
                    return None
                body_id = int(bid[0])
                self._ee_body_cache[robot_name] = body_id
            pos = robot.data.body_pos_w[0, body_id]
            return (pos[0].item(), pos[1].item(), pos[2].item())
        except Exception:
            self._ee_body_cache.pop(robot_name, None)
            return None

    # ── 内部任务直接控制臂关节 ────────────────────────────────────────────────

    def apply_joint_pose(self, robot_name: str, pose) -> bool:
        articulations = getattr(self._env.scene, "articulations", None)
        if len(pose) < 6:
            return False
        robot = resolve_ur5_articulation(articulations, robot_name)
        if robot is None:
            return False
        arm_ids = self._get_arm_ids(robot, robot_name)
        if not arm_ids:
            return False
        arm_target = torch.tensor([list(pose[:6])], device=robot.device, dtype=torch.float32)
        robot.set_joint_position_target(arm_target, joint_ids=arm_ids)
        robot.write_data_to_sim()
        return True

    # ── Scout_1 Key5 机械臂两段序列 ──────────────────────────────────────────

    def start_scout1_key5_arm(self):
        if self.apply_joint_pose("scout_1", SCOUT1_KEY5_ARM_POSE_1):
            print(f"   [scout_1] UR5 机械臂 pose1: {SCOUT1_KEY5_ARM_POSE_1}")
            self.scout1_arm_stage = 1
            self.scout1_arm_arrival_time = time.time()

    def update_scout1_arm_sequence(self, light_manager):
        if self.scout1_arm_stage != 1 or self.scout1_arm_arrival_time is None:
            return
        if time.time() - self.scout1_arm_arrival_time < SCOUT1_KEY5_ARM_STAGE_DELAY:
            return
        if self.apply_joint_pose("scout_1", SCOUT1_KEY5_ARM_POSE_2):
            print(f"   [scout_1] UR5 机械臂 pose2: {SCOUT1_KEY5_ARM_POSE_2}")
            light_manager.trigger_once()
        self.scout1_arm_stage = 2

    # ── 灭火器粘连抓取 ─────────────────────────────────────────────────────────

    def start_extinguisher_pickup(self, robot_name: str, arm_target=None):
        """M20 到达灭火器导航目标后调用，启动 UR5 机械臂抓取序列。"""

        target = arm_target
        if target is None:
            stage = self._try_get_current_stage()
            if stage is not None:
                prim_by_name = self._find_extinguisher_prim_by_name(
                    stage, FIRE_EXTINGUISHER_ARM_TARGET, FIRE_EXTINGUISHER_PRIM_KEYWORDS
                )
                if prim_by_name is not None:
                    resolved_pos = self._get_prim_pos(stage, prim_by_name)
                    if resolved_pos is not None:
                        target = resolved_pos
        if target is None:
            target = FIRE_EXTINGUISHER_ARM_TARGET

        self._ext_robot_name = robot_name
        self._ext_active = True
        self._ext_grabbed = False
        self._ext_joint_path = None
        self._ext_target_prim = None
        self._ext_target_pos = (float(target[0]), float(target[1]), float(target[2]))
        self._ext_prealign_pending = False
        self._ext_carry_hold_logged = False
        self._ext_pick = UR5AutoPickController(robot_name=robot_name, ik_function=None)
        self._ext_pick.start_pick(stage=None, target_pos=self._ext_target_pos)
        print(f"[灭火器] 🔥 {robot_name} UR5 机械臂开始抓取灭火器，目标: {self._ext_target_pos}")

    def update_extinguisher_grab(self, stage):
        """主循环每帧调用：推进机械臂动作 + 碰触检测 + 粘连 + 中断超时检测。"""
        if not self._ext_active:
            # 检测就地放下中断超时（即使 _ext_active 已关闭也要检查）
            if self._ext_interrupt_mode:
                elapsed = time.time() - self._ext_interrupt_start_time
                EXT_INTERRUPT_TIMEOUT = 8.0
                if elapsed > EXT_INTERRUPT_TIMEOUT:
                    robot_name = self._ext_robot_name or ""
                    self._ext_interrupt_mode = False
                    self.is_carrying_extinguisher = False
                    print(f"[UR5] ⚠️ {robot_name} 就地放下超时，强制中断")
                    if self._interrupt_rescue_callback and robot_name:
                        self._interrupt_rescue_callback(robot_name)
            return

        robot_name = self._ext_robot_name
        articulations = getattr(self._env.scene, "articulations", None)
        robot = resolve_ur5_articulation(articulations, robot_name)
        if robot is None:
            return

        if self._ext_pick and self._ext_pick.active:
            jt, _ = self._ext_pick.step(self._env, stage)
            if jt is not None:
                arm_ids = self._get_arm_ids(robot, robot_name)
                if arm_ids:
                    robot.set_joint_position_target(jt, joint_ids=arm_ids)
                    robot.write_data_to_sim()
        elif self._ext_grabbed and self._ext_pick and self._ext_pick.carry_pose:
            arm_ids = self._get_arm_ids(robot, robot_name)
            if arm_ids:
                carry_target = torch.tensor(
                    [self._ext_pick.carry_pose],
                    device=robot.device,
                    dtype=torch.float32,
                )
                robot.set_joint_position_target(carry_target, joint_ids=arm_ids)
                robot.write_data_to_sim()
                if not self._ext_carry_hold_logged:
                    self._ext_carry_hold_logged = True
                    print(
                        f"[灭火器] ⬆️ {robot_name} 已进入高位携带姿态，运输期间持续保持",
                        flush=True,
                    )

        self._ext_debug_counter = getattr(self, "_ext_debug_counter", 0) + 1
        if not self._ext_grabbed:
            self._check_extinguisher_contact(robot, robot_name, stage)

    def update_extinguisher_visual_follow(self, stage) -> None:
        """在物理步后同步视觉代理，不推进机械臂抓取状态机。"""

        if (
            not self._ext_grabbed
            or not self.enable_extinguisher_visual_follow
            or not self._ext_follow_mode
            or not self._ext_target_prim
        ):
            return
        robot_name = self._ext_robot_name
        articulations = getattr(self._env.scene, "articulations", None)
        robot = resolve_ur5_articulation(articulations, robot_name)
        if robot is None:
            return
        ee_pos = self._get_ee_pos_cached(robot, robot_name)
        if ee_pos is None:
            return
        ok_xform = self._set_prim_world_translate(stage, self._ext_target_prim, ee_pos)
        if self._ext_debug_counter % 300 == 0:
            actual = self._get_prim_pos(stage, self._ext_target_prim)
            msg = (
                f"[灭火器代理跟随] "
                f"ee=({ee_pos[0]:.3f},{ee_pos[1]:.3f},{ee_pos[2]:.3f}) "
                f"proxy=({actual[0]:.3f},{actual[1]:.3f},{actual[2]:.3f}) "
                f"ok={ok_xform} cnt={self._ext_debug_counter}"
            ) if actual else (
                f"[灭火器代理跟随] ee=({ee_pos[0]:.3f},{ee_pos[1]:.3f},{ee_pos[2]:.3f}) "
                f"proxy=None ok={ok_xform}"
            )
            print(msg, flush=True)

    def _check_extinguisher_contact(self, robot, robot_name, stage):
        """检测 UR5 末端与灭火器碰触，创建 FixedJoint 粘连。"""

        # REACH_ABOVE/LOWER 阶段末端可能仍离目标一米以上。此时吸附会让
        # 灭火器瞬移并跳过完整伸臂动作；进入 GRASP 后才允许建立粘连。
        if self._ext_pick is None or not self._ext_pick.active or self._ext_pick.phase < 2:
            return

        ee_pos = self._get_ee_pos_cached(robot, robot_name)
        if ee_pos is None:
            if self._ext_debug_counter % 60 == 0:
                print(f"[灭火器] ⚠️ 无法获取 {robot_name} UR5 末端执行器位置")
            return

        if self._ext_target_prim is None:
            search_target = self._ext_target_pos or FIRE_EXTINGUISHER_ARM_TARGET
            # 先按灭火器名称锁定 prim，再补刚体；失败时再回退到“附近刚体”策略。
            prim_by_name = self._find_extinguisher_prim_by_name(
                stage, search_target, FIRE_EXTINGUISHER_PRIM_KEYWORDS
            )
            if prim_by_name is not None:
                if self.enable_extinguisher_stage_attach:
                    ensured = self._ensure_rigid_target(stage, prim_by_name)
                    if ensured is not None:
                        self._ext_target_prim = ensured
                else:
                    self._ext_target_prim = prim_by_name
            if self._ext_target_prim is None:
                self._ext_target_prim = self._find_nearest_rigid_body(
                    stage, search_target,
                    max_search_dist=FIRE_EXTINGUISHER_SEARCH_RADIUS,
                )
            if self._ext_target_prim is None:
                if self._ext_debug_counter % 120 == 0:
                    print(f"[灭火器] ⚠️ 仍未找到灭火器 prim，搜索半径={FIRE_EXTINGUISHER_SEARCH_RADIUS}m")
                return

        ext_pos = self._get_prim_pos(stage, self._ext_target_prim)
        if ext_pos is None:
            return

        dx = ee_pos[0] - ext_pos[0]
        dy = ee_pos[1] - ext_pos[1]
        dz = ee_pos[2] - ext_pos[2]
        dist = (dx * dx + dy * dy + dz * dz) ** 0.5

        if self._ext_debug_counter % 30 == 0:
            phase_info = f"phase={self._ext_pick.phase}" if self._ext_pick else "no_pick"
            print(f"[灭火器] 🔍 ee=({ee_pos[0]:.3f},{ee_pos[1]:.3f},{ee_pos[2]:.3f}) "
                  f"ext=({ext_pos[0]:.3f},{ext_pos[1]:.3f},{ext_pos[2]:.3f}) "
                  f"dist={dist:.3f}m thresh={FIRE_EXTINGUISHER_GRAB_DISTANCE}m {phase_info}")

        if dist < FIRE_EXTINGUISHER_GRAB_DISTANCE:
            if not self.enable_extinguisher_stage_attach:
                self._complete_logical_extinguisher_grab(robot_name, dist)
                return

            # 两阶段吸附：先预对齐，等待下一帧物理更新后再建关节，避免同帧 snap 冲击。
            if not self._ext_prealign_pending and dist > FIRE_EXTINGUISHER_PREALIGN_DIST:
                if self._set_prim_world_translate(stage, self._ext_target_prim, ee_pos):
                    self._ext_prealign_pending = True
                    print(
                        f"[灭火器] 📌 已预对齐灭火器到末端，下一帧再创建关节 "
                        f"(dist={dist:.3f}m)"
                    )
                    return
            elif self._ext_prealign_pending:
                self._ext_prealign_pending = False

            ee_prim = get_ee_prim_path(stage, robot_name)
            if ee_prim:
                ee_rigid = self._find_rigid_body_ancestor(stage, ee_prim)
                target_rigid = self._find_rigid_body_ancestor(stage, self._ext_target_prim)
                if ee_rigid is None:
                    print(f"[灭火器] ⚠️ 末端 prim 非刚体或无刚体父节点: {ee_prim}")
                    return
                if target_rigid is None:
                    print(f"[灭火器] ⚠️ 灭火器 prim 非刚体或无刚体父节点: {self._ext_target_prim}")
                    return
                print(f"[灭火器] 🧷 准备粘连: ee_rigid={ee_rigid} target_rigid={target_rigid}")
                attach_mode = str(FIRE_EXTINGUISHER_ATTACH_MODE).lower().strip()
                if attach_mode == "follow":
                    new_path = self._activate_follow_attach(stage, target_rigid, ee_rigid)
                    if new_path:
                        self._ext_grabbed = True
                        self.is_carrying_extinguisher = True
                        self._ext_follow_mode = False
                        self._ext_target_prim = new_path
                        self._ext_joint_path = None
                        if self._ext_pick:
                            self._ext_pick.grabbed = True
                        print(f"[灭火器] ✅ 挂载粘连成功！{robot_name} → {new_path} (距离={dist:.3f}m)", flush=True)
                    else:
                        proxy_path = self._activate_follow_attach_legacy(
                            stage, target_rigid, ee_pos
                        )
                        if proxy_path:
                            self._ext_grabbed = True
                            self.is_carrying_extinguisher = True
                            self._ext_follow_mode = True
                            self._ext_target_prim = proxy_path   # 跟随代理，而非原始 prim
                            self._ext_joint_path = None
                            if self._ext_pick:
                                self._ext_pick.grabbed = True
                            print(f"[灭火器] ✅ 视觉代理粘连成功！{robot_name} → {proxy_path} (距离={dist:.3f}m)", flush=True)
                else:
                    joint_path = self._create_grab_joint(
                        stage, ee_rigid, target_rigid, ee_pos, ext_pos
                    )
                    if joint_path:
                        self._ext_grabbed = True
                        self.is_carrying_extinguisher = True
                        self._ext_follow_mode = False
                        self._ext_joint_path = joint_path
                        if self._ext_pick:
                            self._ext_pick.grabbed = True
                        print(f"[灭火器] ✅ 粘连成功！{robot_name} UR5 末端与灭火器已粘连 (距离={dist:.3f}m)")
            else:
                print(f"[灭火器] ⚠️ 距离足够 ({dist:.3f}m) 但未找到 UR5 末端 prim path")

    def _complete_logical_extinguisher_grab(self, robot_name: str, dist: float) -> None:
        self._ext_grabbed = True
        self.is_carrying_extinguisher = True
        self._ext_follow_mode = False
        self._ext_joint_path = None
        if self._ext_pick:
            self._ext_pick.grabbed = True
        print(
            f"[灭火器] ✅ 逻辑抓取完成（headless 无 stage 粘连）！"
            f"{robot_name} 已取得灭火器 (距离={dist:.3f}m)",
            flush=True,
        )

    def release_extinguisher_grab(self, stage) -> bool:
        """释放灭火器粘连（删除 FixedJoint），并触发放下回调。"""
        if not self._ext_grabbed:
            print("[灭火器] ⚠️ 当前没有活跃的灭火器粘连")
            return False
        try:
            if self._ext_joint_path:
                prim = stage.GetPrimAtPath(self._ext_joint_path)
                if prim.IsValid():
                    stage.RemovePrim(self._ext_joint_path)
            robot_name = self._ext_robot_name
            print(f"[灭火器] ✅ 灭火器已释放 ({robot_name})")
            self._ext_active = False
            self._ext_grabbed = False
            self._ext_joint_path = None
            self._ext_follow_mode = False
            self._ext_pick = None
            self._ext_target_prim = None
            self._ext_target_pos = None
            self._ext_robot_name = ""
            self.is_carrying_extinguisher = False
            # 触发放下回调（通知 ObstacleRescueManager）
            if self._ext_drop_callback and robot_name:
                self._ext_drop_callback(robot_name)
            return True
        except Exception as e:
            print(f"[灭火器] ❌ 释放失败: {e}")
        return False

    # ── 通用粘连（键盘遥操作用）：靠近即粘，按 P 放下 ───────────────────────────

    def update_generic_grab(self, stage, keyboard_state: dict) -> None:
        """键盘遥操作通用粘连逻辑：

        - 当未抓取时：若 UR5 末端与附近任意刚体距离 < GENERIC_GRAB_DISTANCE，则自动创建 FixedJoint 粘连。
        - 当已抓取时：仅在 keyboard_state['P_just_pressed'] 为 True 时解除粘连，模拟“放下”动作。
        """
        try:
            # 只有在 HTML 端已开启遥操作模式时才启用通用粘连逻辑，避免打扰自动抓取等流程。
            from data_server import get_arm_teleop_enabled, get_arm_teleop_robot
            if not get_arm_teleop_enabled():
                return
            target_robot = get_arm_teleop_robot() or "scout_1"

            articulations = getattr(self._env.scene, "articulations", None)
            robot = resolve_ur5_articulation(articulations, target_robot)
            if robot is None:
                return
            ee_pos = self._get_ee_pos_cached(robot, target_robot)
            if ee_pos is None:
                return

            # 已抓取状态：仅处理按 P 放下
            p_just = bool(keyboard_state.get("P_just_pressed", False))
            if self._generic_grab_active:
                if p_just:
                    # 解除 FixedJoint 或停止视觉代理跟随
                    try:
                        if self._generic_grab_joint_path:
                            joint_prim = stage.GetPrimAtPath(self._generic_grab_joint_path)
                            if joint_prim.IsValid():
                                stage.RemovePrim(self._generic_grab_joint_path)
                        print("[GenericGrab] ✅ 已根据键盘 P 解除通用粘连")
                    except Exception as e:
                        print(f"[GenericGrab] ❌ 解除通用粘连失败: {e}")
                    self._generic_grab_active = False
                    self._generic_grab_joint_path = None
                    self._generic_grab_target_prim = None
                    self._generic_grab_follow_mode = False
                else:
                    # 已抓取且未按 P，保持现状（FixedJoint/代理本身已让目标跟随末端）
                    pass
                return

            # 未抓取状态：尝试在末端附近找到最近刚体，并在距离足够小时建立粘连
            from pxr import UsdGeom
            world = stage.GetPrimAtPath("/World")
            if not world.IsValid():
                return

            best_path = None
            best_dist = float("inf")

            def _traverse(prim):
                nonlocal best_path, best_dist
                from pxr import UsdPhysics
                if not prim.IsValid():
                    return
                # 跳过机器人自身、危险柱等（复用灭火器里的排除模式会更彻底，这里只做简单过滤）
                p = str(prim.GetPath())
                if any(s in p for s in ("/m20_1", "/m20_2", "/carter_1", "/scout_1",
                                        "Factory_Hazard_", "/Characters", "GroundPlane")):
                    for c in prim.GetChildren():
                        _traverse(c)
                    return
                if prim.HasAPI(UsdPhysics.RigidBodyAPI):
                    try:
                        xform = UsdGeom.Xformable(prim)
                        wxf = xform.ComputeLocalToWorldTransform(0)
                        pos = wxf.ExtractTranslation()
                        dx = float(pos[0]) - float(ee_pos[0])
                        dy = float(pos[1]) - float(ee_pos[1])
                        dz = float(pos[2]) - float(ee_pos[2])
                        dist = (dx * dx + dy * dy + dz * dz) ** 0.5
                        if dist < best_dist:
                            best_dist = dist
                            best_path = str(prim.GetPath())
                    except Exception:
                        pass
                for c in prim.GetChildren():
                    _traverse(c)

            _traverse(world)

            if best_path is None or best_dist > float(GENERIC_GRAB_DISTANCE):
                return

            # 找到最近刚体且足够近：创建与 UR5 末端之间的 FixedJoint
            ee_prim_path = get_ee_prim_path(stage, target_robot)
            if not ee_prim_path:
                print("[GenericGrab] ⚠️ 未找到 UR5 末端 prim path，无法创建通用粘连")
                return
            ee_rigid = self._find_rigid_body_ancestor(stage, ee_prim_path)
            target_rigid = self._find_rigid_body_ancestor(stage, best_path)
            if not ee_rigid or not target_rigid:
                print(f"[GenericGrab] ⚠️ 末端或目标刚体无效: ee={ee_rigid} target={target_rigid}")
                return

            joint_path = self._create_grab_joint(
                stage, ee_rigid, target_rigid, ee_pos, self._get_prim_pos(stage, best_path)
            )
            if joint_path:
                self._generic_grab_active = True
                self._generic_grab_joint_path = joint_path
                self._generic_grab_target_prim = best_path
                self._generic_grab_follow_mode = False
                print(f"[GenericGrab] ✅ 已创建通用 FixedJoint 粘连: {target_robot} {ee_rigid} ↔ {target_rigid} (dist={best_dist:.3f}m)")
        except Exception as e:
            print(f"[GenericGrab] ❌ update_generic_grab 异常: {e}")

    def set_extinguisher_drop_callback(self, callback) -> None:
        """注册灭火器放下事件回调，供 ObstacleRescueManager 订阅。"""
        self._ext_drop_callback = callback

    def set_interrupt_rescue_callback(self, callback) -> None:
        """注册中断超时降级回调。"""
        self._interrupt_rescue_callback = callback

    def interrupt_for_rescue(self, robot_name: str) -> bool:
        """M20 搬运灭火器途中被派遣救援时，触发就地放下流程。
        返回 True 表示放下流程已启动，False 表示当前未在搬运（无需中断）。
        """
        if not self.is_carrying_extinguisher or self._ext_robot_name != robot_name:
            return False

        # 记录当前机器人位置作为放下点
        try:
            from .sim_helpers import get_robot_pos
            pos = get_robot_pos(self._env, robot_name)
            drop_pos = (pos[0] + 0.3, pos[1], pos[2])  # 偏移 0.3m 避免碰撞
            self._ext_drop_positions[robot_name] = drop_pos
        except Exception:
            drop_pos = None

        self._ext_interrupt_mode = True
        self._ext_interrupt_drop_pos = drop_pos
        self._ext_interrupt_start_time = time.time()
        print(f"[UR5] ⚡ {robot_name} 灭火器搬运被中断，开始就地放下...")
        return True

    def get_ext_drop_position(self, robot_name: str) -> Optional[Tuple[float, float, float]]:
        """获取机器人就地放下灭火器的坐标。"""
        return self._ext_drop_positions.get(robot_name)

    def restart_extinguisher_pickup(self, robot_name: str) -> None:
        """机器人救援完成并返回放下点后，重新启动灭火器抓取。"""
        drop_pos = self._ext_drop_positions.pop(robot_name, None)
        if drop_pos is None:
            print(f"[UR5] ⚠️ {robot_name} 无就地放下记录，无法重新抓取")
            return
        print(f"[UR5] 🔄 {robot_name} 开始重新抓取灭火器（位置: {drop_pos}）")
        self.start_extinguisher_pickup(robot_name, arm_target=drop_pos)

    @property
    def is_extinguisher_grabbed(self) -> bool:
        return self._ext_grabbed

    @property
    def is_extinguisher_active(self) -> bool:
        return self._ext_active

    @property
    def is_extinguisher_pick_running(self) -> bool:
        return self._ext_pick is not None and self._ext_pick.active

    @property
    def is_extinguisher_pick_complete(self) -> bool:
        """灭火器已粘连，且机械臂已完成抬升动作。"""

        return self._ext_grabbed and not self.is_extinguisher_pick_running

    # ── 灭火器辅助函数 ───────────────────────────────────────────────────────

    @staticmethod
    def _get_prim_pos(stage, prim_path: str) -> Optional[Tuple[float, float, float]]:
        """获取 prim 的世界坐标。"""
        try:
            from pxr import UsdGeom
            prim = stage.GetPrimAtPath(prim_path)
            if not prim.IsValid():
                return None
            xform = UsdGeom.Xformable(prim)
            world_xf = xform.ComputeLocalToWorldTransform(0)
            pos = world_xf.ExtractTranslation()
            return (float(pos[0]), float(pos[1]), float(pos[2]))
        except Exception:
            return None

    @staticmethod
    def _find_extinguisher_prim_by_name(stage, target_pos, keywords) -> Optional[str]:
        """按名称关键字搜索灭火器 prim，并按距目标点最近原则选中。"""
        from pxr import UsdGeom

        kws = [k.lower() for k in keywords]
        best_path = None
        best_dist = float("inf")

        def _traverse(prim):
            nonlocal best_path, best_dist
            if not prim.IsValid():
                return
            path = str(prim.GetPath())
            path_lower = path.lower()
            if any(k in path_lower for k in kws):
                try:
                    pos = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(0).ExtractTranslation()
                    dx = float(pos[0]) - float(target_pos[0])
                    dy = float(pos[1]) - float(target_pos[1])
                    dz = float(pos[2]) - float(target_pos[2])
                    dist = (dx * dx + dy * dy + dz * dz) ** 0.5
                    if dist < best_dist:
                        best_dist = dist
                        best_path = path
                except Exception:
                    pass
            for c in prim.GetChildren():
                _traverse(c)

        root = stage.GetPrimAtPath("/World")
        if root.IsValid():
            _traverse(root)

        if best_path is not None:
            print(f"[灭火器] 🎯 名称匹配到目标 prim: {best_path} (距目标={best_dist:.3f}m)")
        else:
            print("[灭火器] ⚠️ 未通过名称关键字匹配到灭火器 prim")
        return best_path

    @staticmethod
    def _ensure_rigid_target(stage, prim_path: str) -> Optional[str]:
        """
        确保目标 prim 可用于 FixedJoint：
        - 若已有刚体祖先，直接返回；
        - 否则在最近 Xform 上补 RigidBody，并在子 Mesh 上补 Collision。
        """
        from pxr import UsdPhysics, UsdGeom, PhysxSchema

        prim = stage.GetPrimAtPath(prim_path)
        if not prim.IsValid():
            return None

        # 先找已有刚体祖先
        cur = prim
        while cur and cur.IsValid():
            if cur.HasAPI(UsdPhysics.RigidBodyAPI):
                return str(cur.GetPath())
            cur = cur.GetParent()

        # 选择一个可挂刚体的 Xform 节点（优先当前节点，再向上找）
        rigid_host = prim
        while rigid_host and rigid_host.IsValid():
            if rigid_host.IsA(UsdGeom.Xform):
                break
            rigid_host = rigid_host.GetParent()
        if not rigid_host or not rigid_host.IsValid():
            return None

        # 给 host 补 RigidBody
        try:
            UsdPhysics.RigidBodyAPI.Apply(rigid_host)
        except Exception:
            pass
        try:
            PhysxSchema.PhysxRigidBodyAPI.Apply(rigid_host)
        except Exception:
            pass

        # 给 host 下面至少一个 Mesh 补碰撞
        def _apply_collision_on_mesh(node) -> bool:
            if not node.IsValid():
                return False
            done = False
            if node.IsA(UsdGeom.Mesh):
                try:
                    UsdPhysics.CollisionAPI.Apply(node)
                except Exception:
                    pass
                try:
                    UsdPhysics.MeshCollisionAPI.Apply(node)
                except Exception:
                    pass
                done = True
            for c in node.GetChildren():
                done = _apply_collision_on_mesh(c) or done
            return done

        has_mesh_collision = _apply_collision_on_mesh(rigid_host)
        rigid_path = str(rigid_host.GetPath())
        print(
            f"[灭火器] 🔩 已确保刚体目标: {rigid_path} "
            f"(mesh_collision={'yes' if has_mesh_collision else 'no'})"
        )
        return rigid_path

    @staticmethod
    def _find_nearest_rigid_body(
        stage, target_pos, max_search_dist: float = 3.0
    ) -> Optional[str]:
        """在灭火器目标位置附近搜索最近的 rigid body prim。"""
        from pxr import UsdPhysics, UsdGeom

        exclude_re = re.compile(
            r"(/m20_\d|/carter_\d|/M20|/Carter|DomeLight|Factory_Hazard_|/Robot|/Characters|GroundPlane|PhysicsScene)",
            re.IGNORECASE,
        )
        best_path = None
        best_dist = float("inf")
        candidates = []

        def _traverse(prim):
            nonlocal best_path, best_dist
            if not prim.IsValid():
                return
            path = str(prim.GetPath())
            if exclude_re.search(path):
                return
            if prim.HasAPI(UsdPhysics.RigidBodyAPI):
                try:
                    xform = UsdGeom.Xformable(prim)
                    world_xf = xform.ComputeLocalToWorldTransform(0)
                    pos = world_xf.ExtractTranslation()
                    dx = pos[0] - target_pos[0]
                    dy = pos[1] - target_pos[1]
                    dz = pos[2] - target_pos[2]
                    dist = (dx * dx + dy * dy + dz * dz) ** 0.5
                    candidates.append((path, dist, (float(pos[0]), float(pos[1]), float(pos[2]))))
                    if dist < best_dist:
                        best_dist = dist
                        best_path = path
                except Exception:
                    pass
            for c in prim.GetChildren():
                _traverse(c)

        world_prim = stage.GetPrimAtPath("/World")
        if world_prim.IsValid():
            _traverse(world_prim)

        candidates.sort(key=lambda x: x[1])
        top_n = candidates[:5]
        print(f"[灭火器] 🔍 搜索目标=({target_pos[0]:.2f},{target_pos[1]:.2f},{target_pos[2]:.2f}) "
              f"半径={max_search_dist}m，找到 {len(candidates)} 个 rigid body")
        for p, d, pos in top_n:
            print(f"  候选: {p} dist={d:.3f}m pos=({pos[0]:.2f},{pos[1]:.2f},{pos[2]:.2f})")

        if best_path and best_dist < max_search_dist:
            print(f"[灭火器] ✅ 选定 prim: {best_path} (距离={best_dist:.3f}m)")
            return best_path
        else:
            print(f"[灭火器] ⚠️ 未找到灭火器附近的 rigid body (最近距离={best_dist:.3f}m, 阈值={max_search_dist}m)")
            return None

    @staticmethod
    def _find_rigid_body_ancestor(stage, prim_path: str) -> Optional[str]:
        """向上查找最近的 RigidBody prim，返回其路径。"""
        from pxr import UsdPhysics

        prim = stage.GetPrimAtPath(prim_path)
        while prim and prim.IsValid():
            if prim.HasAPI(UsdPhysics.RigidBodyAPI):
                return str(prim.GetPath())
            prim = prim.GetParent()
        return None

    @staticmethod
    def _create_grab_joint(stage, ee_path, target_path, ee_pos, target_pos):
        """在 UR5 末端执行器与灭火器之间创建 FixedJoint 粘连。"""
        from pxr import UsdPhysics, Sdf, PhysxSchema, Gf, UsdGeom

        joint_path = "/World/envs/env_0/extinguisher_grab_joint"
        try:
            old = stage.GetPrimAtPath(joint_path)
            if old.IsValid():
                stage.RemovePrim(joint_path)

            fj = UsdPhysics.FixedJoint.Define(stage, joint_path)
            fj.CreateBody0Rel().SetTargets([Sdf.Path(ee_path)])
            fj.CreateBody1Rel().SetTargets([Sdf.Path(target_path)])
            fj.CreateCollisionEnabledAttr().Set(False)

            # 使用末端当前位置作为连接点，换算到两个刚体局部坐标，提升粘连稳定性。
            ee_prim = stage.GetPrimAtPath(ee_path)
            target_prim = stage.GetPrimAtPath(target_path)
            if ee_prim.IsValid() and target_prim.IsValid():
                ee_xf = UsdGeom.Xformable(ee_prim).ComputeLocalToWorldTransform(0)
                target_xf = UsdGeom.Xformable(target_prim).ComputeLocalToWorldTransform(0)
                p_world = Gf.Vec3d(float(ee_pos[0]), float(ee_pos[1]), float(ee_pos[2]))
                p_local_ee = ee_xf.GetInverse().Transform(p_world)
                p_local_target = target_xf.GetInverse().Transform(p_world)
                fj.CreateLocalPos0Attr().Set(Gf.Vec3f(float(p_local_ee[0]), float(p_local_ee[1]), float(p_local_ee[2])))
                fj.CreateLocalPos1Attr().Set(
                    Gf.Vec3f(float(p_local_target[0]), float(p_local_target[1]), float(p_local_target[2]))
                )

            target_prim = stage.GetPrimAtPath(target_path)
            if target_prim.IsValid():
                # 这些参数用于提升粘连稳定性；若当前 Isaac/USD API 不支持，降级为 warning，不中断关节创建。
                try:
                    if target_prim.HasAPI(PhysxSchema.PhysxRigidBodyAPI):
                        rigid = PhysxSchema.PhysxRigidBodyAPI(target_prim)
                    else:
                        rigid = PhysxSchema.PhysxRigidBodyAPI.Apply(target_prim)
                    rigid.CreateMaxDepenetrationVelocityAttr().Set(0.01)
                    rigid.CreateMaxContactImpulseAttr().Set(10.0)
                    # 质量降低可以减小吸附瞬间对机械臂施加的冲量。
                    mass_api = UsdPhysics.MassAPI.Apply(target_prim)
                    create_mass = getattr(mass_api, "CreateMassAttr", None)
                    if callable(create_mass):
                        create_mass().Set(float(FIRE_EXTINGUISHER_GRAB_MASS))
                    create_lin = getattr(rigid, "CreateLinearDampingAttr", None)
                    if callable(create_lin):
                        create_lin().Set(50.0)
                    create_ang = getattr(rigid, "CreateAngularDampingAttr", None)
                    if callable(create_ang):
                        create_ang().Set(50.0)

                    if target_prim.HasAPI(UsdPhysics.RigidBodyAPI):
                        rb = UsdPhysics.RigidBodyAPI(target_prim)
                        create_linear = getattr(rb, "CreateLinearDampingAttr", None)
                        if callable(create_linear):
                            create_linear().Set(10.0)
                        create_angular = getattr(rb, "CreateAngularDampingAttr", None)
                        if callable(create_angular):
                            create_angular().Set(10.0)
                except Exception as tuning_err:
                    print(f"[灭火器] ⚠️ 刚体稳定参数设置失败，跳过（不影响粘连创建）: {tuning_err}")

            # 创建后校验，避免出现“打印成功但关节无效”。
            joint_prim = stage.GetPrimAtPath(joint_path)
            if not joint_prim.IsValid():
                print("[灭火器] ❌ FixedJoint prim 创建后无效")
                return None
            b0 = fj.GetBody0Rel().GetTargets()
            b1 = fj.GetBody1Rel().GetTargets()
            if not b0 or not b1:
                print("[灭火器] ❌ FixedJoint 缺少 body 绑定")
                return None

            print(f"[灭火器] 🔧 FixedJoint 已创建: {ee_path} ↔ {target_path}")
            return joint_path
        except Exception as e:
            print(f"[灭火器] ❌ 创建 FixedJoint 失败: {e}")
            import traceback
            traceback.print_exc()
            return None

    @staticmethod
    def _strip_physics_recursive(prim):
        """递归移除 prim 及子节点上的所有物理 API。"""
        from pxr import UsdPhysics, PhysxSchema
        removed = []
        def _strip(p):
            path = str(p.GetPath())
            for api_cls, name in [
                (UsdPhysics.RigidBodyAPI, "RigidBodyAPI"),
                (PhysxSchema.PhysxRigidBodyAPI, "PhysxRigidBodyAPI"),
                (UsdPhysics.CollisionAPI, "CollisionAPI"),
                (UsdPhysics.MeshCollisionAPI, "MeshCollisionAPI"),
                (UsdPhysics.MassAPI, "MassAPI"),
            ]:
                try:
                    if p.HasAPI(api_cls):
                        p.RemoveAPI(api_cls)
                        removed.append(f"{path}:{name}")
                except Exception:
                    pass
            for c in p.GetChildren():
                _strip(c)
        _strip(prim)
        return removed

    @staticmethod
    def _activate_follow_attach(stage, target_path: str, ee_path: str) -> str:
        """Factory 引用 prim 无法移动，直接返回空，交给视觉代理方案处理。"""
        return ""

    @staticmethod
    def _activate_follow_attach_legacy(stage, target_path: str, ee_pos=None) -> str:
        """创建视觉代理 prim（在 session 层，无物理），隐藏原始 prim。
        代理完全由 xform 控制，不受 PhysX 时间戳写回影响。
        返回代理路径（成功）或空字符串（失败）。"""
        try:
            from pxr import UsdPhysics, UsdGeom
            # 先禁用原始 prim 碰撞，防止它与机械臂互相弹飞
            original_prim = stage.GetPrimAtPath(target_path)
            if original_prim.IsValid():
                UR5Manager._set_collision_enabled_recursive(original_prim, False)
                # 禁用原始刚体，PhysX 停止向其写回（避免干扰）
                rb = UsdPhysics.RigidBodyAPI.Apply(original_prim)
                rb.CreateRigidBodyEnabledAttr().Set(False)

            proxy_path = UR5Manager._create_visual_proxy(
                stage, target_path,
                ee_pos or UR5Manager._get_prim_pos(stage, target_path) or (0, 0, 0)
            )
            return proxy_path
        except Exception as e:
            print(f"[灭火器] ❌ legacy attach 失败: {e}", flush=True)
            return ""

    @staticmethod
    def _create_visual_proxy(stage, original_path: str, initial_world_pos) -> str:
        """
        创建可自由移动的视觉代理：
          1. 尝试从引用堆找到 USD 资产文件并外部引用
          2. 若找不到，使用红色圆柱体几何代理（近似灭火器外形）
          3. 代理无物理，xform 写入直接控制视觉
          4. 隐藏原始 prim 避免双重渲染
        返回代理路径，失败返回空字符串。
        """
        try:
            from pxr import UsdGeom, UsdPhysics, Gf, Sdf

            proxy_path = "/World/SM_FireExtinguisherProxy"
            original_prim = stage.GetPrimAtPath(original_path)
            if not original_prim.IsValid():
                print(f"[灭火器] ❌ 原始 prim 无效: {original_path}", flush=True)
                return ""

            # ① 列出所有 sublayer（帮助定位资产文件）
            all_layers = stage.GetLayerStack()
            print(f"[灭火器] 📚 stage 共 {len(all_layers)} 个 layer:", flush=True)
            for lyr in all_layers:
                rp = lyr.realPath or lyr.identifier
                print(f"  • {rp[:120]}", flush=True)

            # ② 从 prim 引用栈查找资产文件路径
            asset_path = None
            asset_prim_path = Sdf.Path("/")
            for prim_spec in original_prim.GetPrimStack():
                # 检查 references
                for ref in prim_spec.referenceList.GetAddedOrExplicitItems():
                    if ref.assetPath:
                        layer = prim_spec.layer
                        asset_path = layer.ComputeAbsolutePath(ref.assetPath)
                        if ref.primPath and ref.primPath != Sdf.Path.emptyPath:
                            asset_prim_path = ref.primPath
                        break
                # 检查 payloads
                if not asset_path:
                    for pl in prim_spec.payloadList.GetAddedOrExplicitItems():
                        if pl.assetPath:
                            layer = prim_spec.layer
                            asset_path = layer.ComputeAbsolutePath(pl.assetPath)
                            if pl.primPath and pl.primPath != Sdf.Path.emptyPath:
                                asset_prim_path = pl.primPath
                            break
                if asset_path:
                    break
            print(f"[灭火器] 📄 资产路径: {asset_path}  primPath: {asset_prim_path}", flush=True)

            # ③ 创建代理 Xform prim（在 root/session 层，非引用层，可自由移动）
            proxy_prim = stage.DefinePrim(proxy_path, "Xform")

            has_real_mesh = False
            if asset_path:
                try:
                    # primPath=emptyPath 让 USD 使用文件默认 prim（避免 "/" 路径错误）
                    proxy_prim.GetReferences().AddReference(
                        assetPath=asset_path, primPath=Sdf.Path.emptyPath
                    )
                    has_real_mesh = True
                    print(f"[灭火器] 📦 成功引用资产文件", flush=True)
                except Exception as e:
                    print(f"[灭火器] ⚠️ 引用资产失败: {e}", flush=True)
                    # 回退：尝试用找到的资产内 prim 路径
                    try:
                        proxy_prim.GetReferences().ClearReferences()
                        from pxr import Usd as _Usd
                        _s = _Usd.Stage.Open(asset_path)
                        _default = _s.GetDefaultPrim()
                        _prim_path = _default.GetPath() if _default else Sdf.Path("/Root")
                        proxy_prim.GetReferences().AddReference(
                            assetPath=asset_path, primPath=_prim_path
                        )
                        has_real_mesh = True
                        print(f"[灭火器] 📦 引用成功（prim={_prim_path}）", flush=True)
                    except Exception as e2:
                        print(f"[灭火器] ⚠️ 第二次引用也失败: {e2}", flush=True)

            if not has_real_mesh:
                # 几何代理：红色圆柱体近似灭火器（r=8cm, h=45cm）
                print(f"[灭火器] 🔴 使用几何代理（红色圆柱）", flush=True)
                try:
                    from pxr import UsdShade
                    body_prim_path = f"{proxy_path}/Body"
                    cyl = UsdGeom.Cylinder.Define(stage, body_prim_path)
                    cyl.CreateRadiusAttr(0.08)
                    cyl.CreateHeightAttr(0.45)
                    cyl.CreateAxisAttr("Z")
                    # 材质（红色）
                    mat = UsdShade.Material.Define(stage, f"{proxy_path}/Mat")
                    sh = UsdShade.Shader.Define(stage, f"{proxy_path}/Mat/Shader")
                    sh.CreateIdAttr("UsdPreviewSurface")
                    sh.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set((0.85, 0.1, 0.1))
                    sh.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(0.4)
                    mat.CreateSurfaceOutput().ConnectToSource(sh.ConnectableAPI(), "surface")
                    UsdShade.MaterialBindingAPI(cyl.GetPrim()).Bind(mat)
                except Exception as e:
                    print(f"[灭火器] ⚠️ 几何代理创建失败: {e}", flush=True)

            # ④ 引用资产的物理 API 通常位于子 prim。必须递归移除，否则这个
            # 由 xform 驱动的“视觉代理”仍会参与碰撞并把携带它的底盘卡在货架旁。
            UR5Manager._strip_physics_recursive(proxy_prim)
            try:
                rb = UsdPhysics.RigidBodyAPI.Apply(proxy_prim)
                rb.CreateRigidBodyEnabledAttr().Set(False)
            except Exception:
                pass

            # ⑤ 设置初始位置（代理父为 /World，local == world）
            xformable = UsdGeom.Xformable(proxy_prim)
            xformable.ClearXformOpOrder()
            t_op = xformable.AddTranslateOp(UsdGeom.XformOp.PrecisionDouble)
            t_op.Set(Gf.Vec3d(float(initial_world_pos[0]),
                               float(initial_world_pos[1]),
                               float(initial_world_pos[2])))
            print(f"[灭火器] 📍 代理位置: {initial_world_pos}", flush=True)

            # ⑥ 隐藏原始 prim（代理已有视觉内容，避免双重渲染）
            UsdGeom.Imageable(original_prim).MakeInvisible()
            print(f"[灭火器] 👻 原始 prim 已隐藏", flush=True)
            print(f"[灭火器] 🪄 视觉代理创建完成: {proxy_path}", flush=True)
            return proxy_path

        except Exception as e:
            print(f"[灭火器] ❌ 视觉代理创建失败: {e}", flush=True)
            import traceback; traceback.print_exc()
            return ""

    @staticmethod
    def _set_collision_enabled_recursive(prim, enabled: bool):
        try:
            from pxr import UsdPhysics
            api = UsdPhysics.CollisionAPI(prim) if prim.HasAPI(UsdPhysics.CollisionAPI) else UsdPhysics.CollisionAPI.Apply(prim)
            create_enabled = getattr(api, "CreateCollisionEnabledAttr", None)
            if callable(create_enabled):
                create_enabled().Set(bool(enabled))
            for c in prim.GetChildren():
                UR5Manager._set_collision_enabled_recursive(c, enabled)
        except Exception:
            return

    @staticmethod
    def _set_prim_world_translate(stage, prim_path: str, world_pos: Tuple[float, float, float]) -> bool:
        """将 prim 的 translate xform op 设置到给定世界坐标。
        对于代理 prim（父节点为 /World = 单位矩阵），local == world，直接写入。"""
        try:
            from pxr import UsdGeom, Gf

            prim = stage.GetPrimAtPath(prim_path)
            if not prim.IsValid():
                return False

            xform = UsdGeom.Xformable(prim)
            parent = prim.GetParent()
            parent_xf = Gf.Matrix4d(1.0)
            if parent and parent.IsValid():
                parent_xf = UsdGeom.Xformable(parent).ComputeLocalToWorldTransform(0)

            world = Gf.Vec3d(float(world_pos[0]), float(world_pos[1]), float(world_pos[2]))
            local = parent_xf.GetInverse().Transform(world)
            local_vec = Gf.Vec3d(float(local[0]), float(local[1]), float(local[2]))

            ops = xform.GetOrderedXformOps()
            translate_op = None
            for op in ops:
                if op.GetOpType() == UsdGeom.XformOp.TypeTranslate:
                    translate_op = op
                    break
            if translate_op is None:
                translate_op = xform.AddTranslateOp(UsdGeom.XformOp.PrecisionDouble)

            # 代理 prim 无物理，直接写 Default 即可
            translate_op.Set(local_vec)
            return True
        except Exception:
            return False

    # ── 自动抓取控制器更新（每帧调用）─────────────────────────────────────────

    def update_auto_pick(self, stage):
        import omni.usd
        articulations = getattr(self._env.scene, "articulations", None)
        if not articulations:
            return

        rr_robot = self._obstacle_rescue_reach_robot
        rr_busy = (
            self._obstacle_rescue_reach_pick is not None
            and self._obstacle_rescue_reach_pick.active
            and rr_robot
        )
        button_robot = self._button_press_robot
        button_busy = self.is_button_press_active()
        self.update_button_press(stage)

        # M20_1
        if "m20_1" in articulations:
            if button_busy and button_robot == "m20_1":
                pass
            elif rr_busy and rr_robot == "m20_1":
                pass
            elif self._ext_active and self._ext_robot_name == "m20_1":
                pass
            elif self.m20_1_pick.active:
                jt, _ = self.m20_1_pick.step(self._env, stage)
                if jt is not None:
                    robot = resolve_ur5_articulation(articulations, "m20_1")
                    if robot is not None:
                        arm_ids = self._get_arm_ids(robot, "m20_1")
                        if arm_ids:
                            robot.set_joint_position_target(jt, joint_ids=arm_ids)
                            robot.write_data_to_sim()
        # Scout_1（Scout + UR5）
        # 障碍物救援 reach 与 scout_1_pick 共用一车时：仅当未在跑绿色救援通道序列时才让 rr 独占
        if "scout_1" in articulations:
            block_scout1_pick = (
                rr_busy
                and rr_robot == "scout_1"
                and not self.scout_1_pick.active
            )
            if button_busy and button_robot == "scout_1":
                setattr(self._env, "_scout1_ur5_override", True)
            elif block_scout1_pick:
                setattr(self._env, "_scout1_ur5_override", True)
            else:
                jt, _ = self.scout_1_pick.step(self._env, stage)
                setattr(self._env, "_scout1_ur5_override", self.scout_1_pick.active)
                if jt is not None:
                    robot = resolve_ur5_articulation(articulations, "scout_1")
                    if robot is not None:
                        arm_ids = self._get_arm_ids(robot, "scout_1")
                        if arm_ids:
                            try:
                                robot.set_joint_position_target(jt, joint_ids=arm_ids)
                                robot.write_data_to_sim()
                            except Exception:
                                pass

    # ── 重置 ──────────────────────────────────────────────────────────────────

    def reset(self):
        self.end_obstacle_rescue_reach()
        self.end_button_press()
        self.scout1_arm_stage = 0
        self.scout1_arm_arrival_time = None
        self._ext_robot_name = ""
        self._ext_active = False
        self._ext_grabbed = False
        self._ext_joint_path = None
        self._ext_pick = None
        self._ext_target_prim = None
        self._ext_target_pos = None
        self._ext_prealign_pending = False
        self._ext_follow_mode = False
        self._ext_carry_hold_logged = False

    @staticmethod
    def _try_get_current_stage():
        try:
            import omni.usd
            return omni.usd.get_context().get_stage()
        except Exception:
            return None
