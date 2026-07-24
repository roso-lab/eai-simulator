# Copyright (c) 2022-2025, The Isaac Lab Project Developers.
# SPDX-License-Identifier: BSD-3-Clause
"""工厂仿真通用辅助：位姿/角度工具 + 穹顶灯闪烁（原 ``utils.py`` + ``light_manager.py`` 合并）。"""

from __future__ import annotations

import math
import time

from pxr import Gf

# ── 位姿与角度（原 utils.py）───────────────────────────────────────────────────


def get_yaw_from_quat(quat_tensor):
    """从四元数 [w,x,y,z] 提取 yaw 角（弧度）。"""
    w, x, y, z = quat_tensor[0], quat_tensor[1], quat_tensor[2], quat_tensor[3]
    siny_cosp = 2.0 * (w * z + x * y)
    cosy_cosp = 1.0 - 2.0 * (y * y + z * z)
    return math.atan2(siny_cosp, cosy_cosp)


def normalize_angle(angle):
    """将角度规范到 [-π, π]。"""
    while angle > math.pi:
        angle -= 2 * math.pi
    while angle < -math.pi:
        angle += 2 * math.pi
    return angle


def get_robot_pos(base_env, name):
    """获取机器人当前世界坐标 (x,y,z)，scout_1 特殊处理取 base_link。"""
    if name not in base_env.scene.articulations:
        return (0.0, 0.0, 0.0)
    robot = base_env.scene.articulations[name]
    if name == "scout_1":
        try:
            for ln in ("base_link", "chassis"):
                bid, _ = robot.find_bodies(ln, preserve_order=True)
                if bid:
                    p = robot.data.body_pos_w[0, bid[0]].cpu().numpy()
                    return (float(p[0]), float(p[1]), float(p[2]))
        except Exception:
            pass
    p = robot.data.root_pos_w[0].cpu().numpy()
    return (float(p[0]), float(p[1]), float(p[2]))


def get_robot_pose_tensors(base_env, name):
    """返回 (curr_pos_tensor, curr_quat_tensor)，scout_1 取 base_link body。"""
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


# ── 穹顶灯（原 light_manager.py）───────────────────────────────────────────────


class LightFlashController:
    """控制穹顶灯红白交替闪烁（危险警示效果）。"""

    RESCUE_CHANNEL_RED_PULSES = 5

    def __init__(self, stage, dome_light_path="/World/DomeLight"):
        self.is_flashing = False
        self._color_index = 0
        self._last_switch = 0.0
        self._interval = 0.5
        self._arm_triggers = 0
        self._max_arm_triggers = 2
        self._rescue_channel_triggers = 0
        self._max_rescue_channel_triggers = 8
        self._rescue_channel_wants_flash = False
        self._rescue_red_pulses_done = 0
        self._rescue_solid_red = False

        self._color_attr = None
        prim = stage.GetPrimAtPath(dome_light_path)
        if not prim.IsValid():
            print(
                f"[LightFlash] ⚠️ 未找到穹顶灯 prim {dome_light_path}，"
                "红白闪烁不可用（请检查工厂场景是否包含 DomeLight）。"
            )
        else:
            self._color_attr = prim.GetAttribute("inputs:color")
            if not self._color_attr or not self._color_attr.IsValid():
                print(
                    f"[LightFlash] ⚠️ {dome_light_path} 缺少 inputs:color，"
                    "无法驱动红白闪烁；Isaac 版本若使用其它属性名需同步修改 runtime/sim_helpers.py。"
                )
                self._color_attr = None

    def toggle(self):
        """手动切换闪烁 / 停止。"""
        self.is_flashing = not self.is_flashing
        if self.is_flashing:
            self._rescue_channel_wants_flash = False
            self._rescue_solid_red = False
            self._last_switch = time.time()
            self._color_index = 0
            print("[Key 6] 🚨 启动灯光闪烁模式（红白交替）")
        else:
            self._rescue_solid_red = False
            if self._color_attr:
                self._color_attr.Set(Gf.Vec3f(0.75, 0.75, 0.75))
            print("[Key 6] ✅ 停止闪烁，恢复正常灯光")

    def trigger_once(self):
        """机械臂事件触发一次闪烁（限次）。"""
        if self._arm_triggers >= self._max_arm_triggers:
            print(f"[机械臂] ⚠️ 已达到最大触发次数（{self._max_arm_triggers}次），不再触发")
            return
        self._arm_triggers += 1
        if not self.is_flashing:
            self._rescue_channel_wants_flash = False
            self._rescue_solid_red = False
            self.is_flashing = True
            self._last_switch = time.time()
            self._color_index = 0
        print(f"[机械臂] 🚨 触发灯光闪烁 - 第 {self._arm_triggers}/{self._max_arm_triggers} 次")

    def trigger_rescue_channel(self):
        """救援通道按钮成功后的警示闪（与 Key5/机械臂测试限次独立）。"""
        if self._rescue_channel_triggers >= self._max_rescue_channel_triggers:
            print(
                f"[救援通道] ⚠️ 灯光触发已达上限（{self._max_rescue_channel_triggers} 次），不再触发"
            )
            return
        self._rescue_channel_triggers += 1
        if not self._color_attr:
            print(
                "[救援通道] 灯光属性未绑定，跳过红白闪烁（见初始化时的 [LightFlash] 警告）。"
            )
            return
        self._rescue_channel_wants_flash = True
        self._rescue_red_pulses_done = 0
        self._rescue_solid_red = False
        if not self.is_flashing:
            self.is_flashing = True
            self._last_switch = time.time()
            self._color_index = 0
        print(
            f"[救援通道] 🚨 触发穹顶红白警示闪（闪 {self.RESCUE_CHANNEL_RED_PULSES} 次后常红）- 第 "
            f"{self._rescue_channel_triggers}/{self._max_rescue_channel_triggers} 次"
        )

    def update(self):
        """每帧调用：执行颜色切换（时间驱动，开销极低）。"""
        if self._rescue_solid_red and self._color_attr:
            self._color_attr.Set(Gf.Vec3f(1.0, 0.0, 0.0))
            return
        if not self.is_flashing or not self._color_attr:
            return
        now = time.time()
        if now - self._last_switch < self._interval:
            return
        if self._rescue_channel_wants_flash:
            if self._color_index == 0:
                self._color_attr.Set(Gf.Vec3f(1.0, 1.0, 1.0))
                self._color_index = 1
            else:
                self._color_attr.Set(Gf.Vec3f(1.0, 0.0, 0.0))
                self._rescue_red_pulses_done += 1
                self._color_index = 0
                if self._rescue_red_pulses_done >= self.RESCUE_CHANNEL_RED_PULSES:
                    self._rescue_solid_red = True
                    self.is_flashing = False
                    self._rescue_channel_wants_flash = False
            self._last_switch = now
            return
        if self._color_index == 0:
            self._color_attr.Set(Gf.Vec3f(1.0, 1.0, 1.0))
            self._color_index = 1
        else:
            self._color_attr.Set(Gf.Vec3f(1.0, 0.0, 0.0))
            self._color_index = 0
        self._last_switch = now

    def reset(self):
        """环境重置时恢复初始状态。"""
        self._arm_triggers = 0
        self._rescue_channel_triggers = 0
        self._rescue_channel_wants_flash = False
        self._rescue_red_pulses_done = 0
        self._rescue_solid_red = False
        self.is_flashing = False
        if self._color_attr:
            self._color_attr.Set(Gf.Vec3f(0.75, 0.75, 0.75))
