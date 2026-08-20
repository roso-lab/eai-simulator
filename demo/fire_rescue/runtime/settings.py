# Copyright (c) 2022-2025. Factory EMOS: constants aligned with factory_env + goal_factory_nav_emos.
"""Fire Rescue patrol, hazard, UR5, and dashboard settings."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

# Default map assets bundled with this demo.
_DEFAULT_MAP_DIR = Path(__file__).resolve().parents[1] / "assets"

# ── 危险柱预设位置 (X, Y, Z) ────────────────────────────────────────────────────
HAZARD_POSITIONS = {
    1: (-6.0, -4.0, 0.0),
    2: (-5.0, 2.5, 0.0),
    3: (0.25, 9.5, 0.0),
    4: (3.5, -2.5, 0.0),
    5: (-8.11, 8.66, 0.0),
}

# 火源邻域任务的硬编码安全点：用于已知某些火源附近地图边界/占据栅格不稳定，
# 但存在明确可达且仍位于 3m 范围内的集结/送达位置。
# 对于所有机器人共用一个安全点的火源，写在这里（hazard 3 已迁移至 BY_ROBOT 表）。
FIRE_FIXED_PROXIMITY_TARGETS = {
}

# 按机器人区分专属集结点，避免多机争抢同一点或
# 同向 anchor_offset 集体落入墙体内。命中此表时优先于 FIRE_FIXED_PROXIMITY_TARGETS。
FIRE_FIXED_PROXIMITY_TARGETS_BY_ROBOT = {
    1: {
        "m20_1": (-3.5, -5.0),
        "m20_2": (-7.15, -3.5),
        "carter_1": (-8.0, -5.2),
        # Keep Scout south of the east-west aisle used by m20_2 while carrying
        # the extinguisher.  Parking farther north makes the collision guard
        # hard-stop m20_2 before it can pass the stationary Scout.
        "scout_1": (-4.55, -2.95),
    },
    3: {
        "m20_1": (-1.84, 9.0),
        "m20_2": (0.93, 7.84),
        "carter_1": (-0.14, 8.28),
        "scout_1": (1.57, 8.1),
    },
    5: {
        "m20_1": (-7.63, 8.25),
        "m20_2": (-7.24, 9.02),
        "carter_1": (-7.63, 7.53),
        "scout_1": (-8.02, 7.85),
    },
}

# ── 按键 5 预设目标位置 ─────────────────────────────────────────────────────────
KEY5_TARGET_POSITIONS = {
    "m20_1": (10.3, 0.9, 0.52),
    "m20_2": [(2.0, -3.5, 0.52), (2.0, -10.0, 2)],
    "carter_1": (-10.437, 0.173, 0.080),
    "scout_1": (-3.5, 4.0, 0.0),
}

# ── Scout_1 机械臂两段关节姿态 ─────────────────────────────────────────────────
SCOUT1_KEY5_ARM_POSE_1 = [-1.61, -1.68, 0.26, -1.57, -1.57, 0.0]
SCOUT1_KEY5_ARM_POSE_2 = [2.64, -1.27, 0.72, -1.57, -1.57, 0.0]
SCOUT1_KEY5_ARM_STAGE_DELAY = 2.0

ARM_TELEOP_ENABLED_DEFAULT = False
ARM_TELEOP_JOINT_DELTA = 0.02
GENERIC_GRAB_DISTANCE = 0.08

M20_UR5_JOINT_NAMES = [
    "shoulder_pan_joint",
    "shoulder_lift_joint",
    "elbow_joint",
    "wrist_1_joint",
    "wrist_2_joint",
    "wrist_3_joint",
]

# ── 默认巡逻路径点（首点与 factory_env spawn 一致）──────────────────────────────
DEFAULT_ROBOT_WAYPOINTS = {
    "m20_1": [
        (-3.0, 5.0, 0.52),
        (-5.5, 4.2, 0.52),
        (-4.2, 7.8, 0.52),
        (-2.7, 8.0, 0.52),
        (-1.5, 7.6, 0.52),
        (2.0, 7.5, 0.52),
        (-1.5, 7.6, 0.52),
        (-2.7, 8.0, 0.52),
        (-3.0, 5.0, 0.52),
    ],
    "m20_2": [
        (3.0, 1.0, 0.52),
        (2.7, 4.9, 0.52),
        (0.8, 3.0, 0.52),
        (-2.0, 3.0, 0.52),
        (-2.6, -0.9, 0.52),
        (1.4, -0.9, 0.52),
        (3.0, 1.0, 0.52),
    ],
    "carter_1": [
        (-7.6, -8.0, 0.0),
        (-7.6, 0.3, 0.0),
        (-4.0, 0.28, 0.0),
        (-7.6, 0.3, 0.0),
    ],
    "scout_1": [
        (8.0, 3.0, 0.0),
        (6.7, 0.1, 0.0),
        (7.5, -5.0, 0.0),
        (6.7, 0.1, 0.0),
        (8.0, 3.0, 0.0),
        (6.0, 5.5, 0.0),
        (2.5, 5.5, 0.0),
        (6.0, 5.5, 0.0),
        (8.0, 3.0, 0.0),
    ],
}

# ── 人机协作组（group 3 / EMOS）专用巡逻路径覆盖 ────────────────────────────────
# 纯机器人 demo 使用 DEFAULT_ROBOT_WAYPOINTS；保留该变量供共享导航代码读取。
EMOS_GROUP_ROBOT_WAYPOINT_OVERRIDES = {
    "m20_1": [
        (-3.0, 5.0, 0.52),
        (-5.5, 4.2, 0.52),
        (-4.2, 7.8, 0.52),
        (-2.7, 8.0, 0.52),
        (-1.5, 7.6, 0.52),
        (2.0, 7.5, 0.52),
        (-1.5, 7.6, 0.52),
        (-2.7, 8.0, 0.52),
        (-3.0, 5.0, 0.52),
    ],
}

# 导航停靠点与墙面按钮世界坐标；若场景中按钮位置不同，请同步改此两处
RESCUE_CHANNEL_NAV_TARGET = (10.78, 1.0, 0.0)
RESCUE_CHANNEL_BUTTON_POS = (10.8, 1.0, 0.74)

ARRIVAL_DIST = 0.3
SCOUT1_ARRIVAL_DIST = 0.2
SCOUT1_MAX_SPEED = 1.1
CARTER_MAX_SPEED = 1.0
CARTER_TURN_SPEED = 0.9
SCOUT1_TURN_SPEED = 2.4
CARTER_ALIGN_THRESH = 0.3
SCOUT1_ALIGN_THRESH = 0.4
CARTER_MAX_OMEGA = 1.6
SCOUT1_MAX_OMEGA = 3.0
M20_MAX_LIN_SPEED = 0.7
M20_MAX_ANG_SPEED = 0.8
M20_KP_LIN = 1.5
M20_KP_ANG = 1.0
SMOOTH_ALPHA_M20 = 0.2
SMOOTH_ALPHA_CARTER = 0.35
SMOOTH_ALPHA_SCOUT1 = 0.70
CARTER_DELAY_SECONDS = 0.0

NAV_LOOKAHEAD = 0.6
NAV_ARRIVE_RADIUS = 0.3
NAV_FINAL_RADIUS = 0.25
# M20 黄色任务第一段（去灭火器取货）专用：终点容差略大于 NAV_FINAL_RADIUS，避免贴墙时无法判到达。
# 第二段「运送至火源」(task_id=emos_yellow_fire_delivery) 仍用 NAV_FINAL_RADIUS。
M20_EXTINGUISHER_PICKUP_FINAL_RADIUS = 0.38
NAV_WAYPOINT_STEP = 1.0

# 台阶禁行区：Carter 轮式机器人无法通过的台阶位置
# 格式: (x_min, x_max, y_center, y_half_width) — 当 carter 路径在 x∈[x_min,x_max] 范围穿越 y_center±y_half_width 时需绕行
CARTER_STEP_ZONES = [
    (0.56, 2.56, -1.0, 0.8),
]

STREAM_FPS = 15
STREAM_RESOLUTION = (1280, 720)
STREAM_JPEG_QUALITY = 60

DATA_SERVER_UPDATE_INTERVAL = 5
STUCK_CHECK_INTERVAL = 30
DEBUG_PRINT_INTERVAL = 200

# 卡死触发阈值（放宽）：更小位移也算“有进展”，并延长判定/重规划冷却窗口，
# 减少在狭窄区域、贴障行驶时的误触发。
STUCK_THRESHOLD_DIST = 0.08
STUCK_TIMEOUT_S = 12.0
STUCK_REPLAN_CD_S = 20.0

# Effective ground-plane footprints include the mounted UR5 geometry.  They are
# intentionally larger than the bare chassis radii used by the generic plugin.
INTER_ROBOT_RADII = {
    "carter": 0.45,
    "m20": 0.80,
    "scout": 0.85,
}
INTER_ROBOT_SAFETY_MARGIN = 0.35
INTER_ROBOT_HARD_STOP_MARGIN = 0.12
INTER_ROBOT_LOOKAHEAD_S = 2.0
INTER_ROBOT_RELEASE_HYSTERESIS = 0.25
INTER_ROBOT_REPLAN_COOLDOWN_S = 5.0

FIRE_EXTINGUISHER_NAV_TARGET = (1.77, -8.98, 2.0)
FIRE_EXTINGUISHER_ARM_TARGET = (1.85, -9.7, 0.5)
# 抓取点位于狭窄货架区域。携带灭火器后先沿中间通道直线向北退出，
# 再交给全局规划器前往火源，避免大体积 M20+UR5 斜切货架边缘。
FIRE_EXTINGUISHER_EGRESS_TARGET = (1.80, -7.50, 2.0)

DEBUG_EXTINGUISHER_GRAB = False
DEBUG_EXTINGUISHER_GRAB_ROBOT = "m20_1"
FIRE_EXTINGUISHER_GRAB_DISTANCE = 1.5
FIRE_EXTINGUISHER_SEARCH_RADIUS = 3.0
FIRE_EXTINGUISHER_PREALIGN_DIST = 0.30
FIRE_EXTINGUISHER_GRAB_MASS = 0.35
FIRE_EXTINGUISHER_ATTACH_MODE = "follow"
FIRE_EXTINGUISHER_DELIVERY_OFFSET_XY = (-1.2, 0.5)

OBSTACLE_CORRIDOR_Y_TRIGGER = -3.0
OBSTACLE_POSITION = (-7.765, -1.125, 0.0)
OBSTACLE_LENGTH = 2.3
OBSTACLE_WIDTH = 0.3
OBSTACLE_HEIGHT = 0.5
OBSTACLE_PRIM_PATH = "/World/Corridor_Obstacle"
# 救援导航：停靠点相对障碍物中心沿 -Y 方向偏移（米），略大于半长+车宽，避免 m20 贴箱碰撞翻倒
RESCUE_NAV_TARGET_OFFSET_Y = 2.15
# 判定「已到达停靠点」：与导航目标 (x,y) 的距离（米），不再用「距障碍物中心」以免贴箱。
# 收紧自 0.55 → 0.30：与 NAV_FINAL_RADIUS=0.25 一致，避免机器人在距停靠点 0.55m 远就误判到达
# 而出现「机械臂还离障碍物较远就开始隔空 attach」的演示问题。
RESCUE_NAV_ARRIVAL_EPS = 0.30
# 兜底：车体已顶到/紧贴钢梁时，往往到不了理想停靠点；若水平距障碍物中心 ≤ 此值则仍进入伸臂阶段
# 须小于「理想停靠」到中心的距离 (RESCUE_NAV_TARGET_OFFSET_Y)，否则会误触发
RESCUE_CONTACT_ARRIVAL_DIST = 1.38

# 派遣超时兜底（机器人长时间到不了停靠点时，按距障碍物中心的距离强行进入伸臂）：
# 旧值 90s + 2.76m（=1.38×2）会让兜底在距障碍物 2.7m 远就触发，演示时机械臂"隔空抓"。
# 新值 60s + 1.38m（=1.0×CONTACT）：提前介入但要求更接近障碍物，与新到达半径 0.30 配套，
# 不再出现"距障 2.75m 隔空抓"。若 60s 内仍 >1.38m，继续等到机器人靠近或实验自然超时。
RESCUE_TIMEOUT_FALLBACK_S = 60.0
RESCUE_TIMEOUT_FALLBACK_DIST = 1.38
# 救援机器人进入该世界坐标附近（水平半径内）即可开始搬运障碍物（与规划停靠、接触兜底并列）。
# 试过把 X 由 -8.82 → -8.65 拉近障碍物，但实测（5.6-14.07/2-a 日志）该点会被规划器吸附到
# (-8.65, -0.15)（位于障碍物长边西北角）；m20_1 需绕到 X≈-1 走廊再向南穿越 X∈[-3,-5] 障碍区，
# 系统性卡死。回退到 (-8.82, -0.36) 这个历史已验证可稳定到达的位置（距障碍物 X 表面 0.91m）。
# 视觉上仍距 UR5 reach=0.85m 稍远 0.06m，但靠收紧的 0.30m 半径保证机器人精准停靠，
# 比旧 0.75m 配合下"最远停车 1.66m" 改善 ~2.3 倍。
RESCUE_ARM_START_XY = (-8.55, -0.49)
# 半径由 0.75 → 0.30：必须更精准停靠才触发抓取，避免在距障碍物 1.6m+ 远处误触发「隔空 attach」。
RESCUE_ARM_START_RADIUS = 0.30
# True 时救援导航目标直接使用 RESCUE_ARM_START_XY（严格先到该点再搬运）
RESCUE_NAV_USE_ARM_START_POINT = True
# True 时允许“贴近障碍中心”提前进入搬运；为满足“必须先到指定点”需求，默认关闭
RESCUE_ALLOW_CONTACT_ARRIVAL_FALLBACK = False
# 兼容旧逻辑：保留变量名，新逻辑见 obstacle_rescue._check_rescue_arrival
RESCUE_ARRIVAL_DIST = 1.5
RESCUE_ARM_OPERATION_TIME = 5.0
RESCUE_AUTO_APPROVE_TIMEOUT = 60.0
CARTER1_PATROL_START_DELAY = 0.0
RESCUE_ARM_REACH_X = -1.2
RESCUE_ARM_REACH_Z = 0.3

SCOUT1_NAV_DEBUG = False
SCOUT1_NAV_DEBUG_INTERVAL = 15
SCOUT1_TEST_GOAL_AT_STEP = 0
SCOUT1_TEST_GOAL_XY = (2.0, 2.0)

STREAM_PORT = 8766
DATA_PORT = 8767

# 全局规划障碍膨胀半径（格）；resolution=0.05m 时 14格≈0.70m
NAV_INFLATION_RADIUS_CELLS = 10

REPLAN_TRIGGER_DELAY_S = 5.0
PATROL_FALLBACK_OFFSET = 2.0
TASK_HIGHLIGHT_DURATION_MS = 3000

# Auto fire: seconds after patrol start before random hazard (if none manual)
AUTO_FIRE_DELAY_S = 5.0


def resolve_factory_map_png_path(
    map_yaml_file: str | Path,
    map_yaml_dict: Optional[Dict[str, Any]],
) -> Path:
    """Resolve the dashboard map beside YAML, falling back to the demo asset."""
    if map_yaml_dict:
        img = map_yaml_dict.get("image")
        if img:
            return Path(map_yaml_file).resolve().parent / str(img)
    return _DEFAULT_MAP_DIR / "factory_map.png"
