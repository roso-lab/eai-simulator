"""
EMOS 障碍物救援模块

管理 Carter1 通道被障碍物阻挡后的多机器人协同救援流程：
  1. 监测 Carter1 位置，在接近目标通道时生成障碍物
  2. 检测 Carter1 被阻挡，通过 EMOS 对话报告
  3. 系统发起多机器人协同讨论，选择具有搬运能力的救援机器人
  4. 将救援审批通知推送至 Dashboard 侧边面板，等待人类批准
  5. 救援机器人前往障碍物精确位置，使用 UR5 创建视觉代理拖拽障碍物
  6. 拖拽一段距离后障碍物消失，Carter1 恢复通行
  7. 救援机器人返回之前的任务
"""

from __future__ import annotations

import math
import os
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from queue import Queue, Empty
from typing import Any, Callable, Dict, List, Optional, Tuple

from .settings import (
    OBSTACLE_POSITION, OBSTACLE_LENGTH, OBSTACLE_WIDTH, OBSTACLE_HEIGHT,
    OBSTACLE_PRIM_PATH, OBSTACLE_CORRIDOR_Y_TRIGGER,
    RESCUE_NAV_TARGET_OFFSET_Y, RESCUE_NAV_ARRIVAL_EPS, RESCUE_CONTACT_ARRIVAL_DIST,
    RESCUE_ARM_START_XY, RESCUE_ARM_START_RADIUS,
    RESCUE_NAV_USE_ARM_START_POINT, RESCUE_ALLOW_CONTACT_ARRIVAL_FALLBACK,
    RESCUE_TIMEOUT_FALLBACK_S, RESCUE_TIMEOUT_FALLBACK_DIST,
    RESCUE_ARM_OPERATION_TIME,
    RESCUE_AUTO_APPROVE_TIMEOUT, CARTER1_PATROL_START_DELAY,
    NAV_WAYPOINT_STEP,
    RESCUE_ARM_REACH_X, RESCUE_ARM_REACH_Z,
)
from .sim_helpers import get_robot_pos

# 救援臂伸展序列超时（秒），超时后仍创建代理以免卡死
RESCUE_ARM_REACH_TIMEOUT_S = 45.0

# 清障机器人选择是否走 LLM。False 时直接用本地推荐（首推机器人，通常为 m20_1），
# 避免主线程同步阻塞在 LLM 网络调用上导致仿真卡死。可用环境变量 RESCUE_USE_LLM=1 临时打开。
RESCUE_USE_LLM = os.environ.get("RESCUE_USE_LLM", "0").strip().lower() in ("1", "true", "yes")

# Dashboard 救援审批侧栏：列出场景中全部 EMOS 机器人，由人类选择；advised 仅表示系统是否首推
RESCUE_PANEL_ROSTER: Dict[str, Dict[str, Any]] = {
    "m20_1": {
        "type": "四足机器人 M20",
        "arm": "UR5",
        "terrain": "可翻越复杂地形",
        "has_arm": True,
    },
    "m20_2": {
        "type": "四足机器人 M20",
        "arm": "UR5",
        "terrain": "可翻越复杂地形",
        "has_arm": True,
    },
    "scout_1": {
        "type": "Scout 差速无人车",
        "arm": "UR5",
        "terrain": "仅限平坦地面",
        "has_arm": True,
    },
    "carter_1": {
        "type": "差速无人车 Carter1（Orsus）",
        "arm": "无",
        "terrain": "平坦通道",
        "has_arm": False,
    },
}


class _C:
    RST = "\033[0m"
    BOLD = "\033[1m"
    YELLOW = "\033[93m"


def _log(msg: str):
    print(f"{_C.YELLOW}{_C.BOLD}[ObstacleRescue]{_C.RST} {msg}")


class RescuePhase(Enum):
    IDLE = auto()
    WAITING_PATROL = auto()
    MONITORING = auto()
    OBSTACLE_DROPPED = auto()
    CARTER_BLOCKED = auto()
    DISCUSSING = auto()
    AWAITING_APPROVAL = auto()
    RESCUE_DISPATCHED = auto()
    REMOVING_OBSTACLE = auto()
    DRAGGING = auto()
    CARTER_RESUMING = auto()
    RESCUE_DWELL = auto()        # 仅人机协作组：搬运完成后救援机器人原地停留 N 秒
    RESCUE_RETURNING = auto()
    COMPLETED = auto()


@dataclass
class RescueState:
    phase: RescuePhase = RescuePhase.IDLE
    obstacle_spawned: bool = False
    rescue_robot: Optional[str] = None
    rescue_robot_saved_waypoints: Optional[list] = None
    rescue_robot_saved_index: int = 0
    rescue_robot_saved_waiting: bool = True
    obstacle_position: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    block_detected_time: float = 0.0
    approval_wait_start: float = 0.0
    removal_start_time: float = 0.0
    drag_start_time: float = 0.0
    drag_proxy_path: str = ""
    carter1_resumed: bool = False
    obstacle_removed: bool = False
    patrol_started: bool = False
    start_time: float = 0.0


# ---------------------------------------------------------------------------
# ObstacleRescueManager
# ---------------------------------------------------------------------------
class ObstacleRescueManager:
    """管理障碍物救援的完整生命周期。"""

    DRAG_DURATION = 4.0
    DRAG_DISTANCE = 2.5

    def __init__(
        self,
        base_env,
        nav_controller,
        push_chat_fn: Callable,
        update_rescue_fn: Callable,
        stage,
        simulation_app,
        navigator,
        extinguisher_robot: Optional[str] = None,
        ur5=None,
        planner_mode: str = "emos",
        dwell_after_drag_s: float = 0.0,
        rescue_llm_kwargs_getter: Optional[Callable[[], Dict[str, Any]]] = None,
        obstacle_ur5_reach_enabled: bool = True,
        allow_runtime_app_update: bool = True,
    ):
        self.base_env = base_env
        self.nav = nav_controller
        self.ur5 = ur5
        self.push_chat = push_chat_fn
        self.update_rescue_state = update_rescue_fn
        self.stage = stage
        self.sim_app = simulation_app
        self.navigator = navigator
        self.extinguisher_robot = extinguisher_robot
        self.planner_mode = str(planner_mode or "emos").strip().lower()
        self._rescue_llm_kwargs_getter = rescue_llm_kwargs_getter
        self.obstacle_ur5_reach_enabled = bool(obstacle_ur5_reach_enabled)
        self.allow_runtime_app_update = bool(allow_runtime_app_update)
        # 搬运完后让救援机器人原地停留 N 秒再规划下一个任务（默认 0 秒 = 维持旧行为）。
        # 人机协作组传 5.0；其它组保持默认。
        self.dwell_after_drag_s = max(0.0, float(dwell_after_drag_s or 0.0))
        self._dwell_start_time: float = 0.0

        self.state = RescueState()
        self._approval_queue: Queue = Queue(maxsize=8)
        self.rescue_candidates: List[dict] = []
        self._drag_start_pos: Optional[Tuple[float, float, float]] = None
        # 与 _dispatch_rescue 中 target_xy 一致，用于「到达」判定（距障碍物中心过近会顶翻）
        self._rescue_nav_goal_xy: Optional[Tuple[float, float]] = None
        # True：救援结束后用 _start_task 续跑队列中的 EMOS，_check_rescue_return 勿恢复旧路点
        self._rescue_return_emos_resume: bool = False

        # ── 灭火器放下追踪 ──────────────────────────────────────────────────
        self._extinguisher_dropped_robots: set = set()
        # ── 中断派遣等待状态 ────────────────────────────────────────────────
        self._pending_rescue_robot: Optional[str] = None
        self._pending_rescue_nav = None
        # 本会话内已完成过一次障碍物救援的机器人，不再作为救援候选
        self._rescue_completed_robots: set = set()
        # 记录 phase 变化，便于排障
        self._last_phase_logged: Optional[RescuePhase] = None

    def _is_openclaw(self) -> bool:
        return self.planner_mode == "openclaw"

    def _is_robot_baseline(self) -> bool:
        return self.planner_mode == "robots"

    def _system_sender(self) -> str:
        return "OpenClaw 控制端" if self._is_openclaw() else "EMOS 系统"

    def _planner_label(self) -> str:
        return "OpenClaw" if self._is_openclaw() else "EMOS"

    def _operator_sender(self) -> str:
        return "OpenClaw 控制端" if self._is_openclaw() else "人类操作员"

    def _runtime_app_update(self, count: int = 1) -> None:
        if not self.allow_runtime_app_update:
            return
        for _ in range(max(0, int(count))):
            self.sim_app.update()

    # ── 辅助方法 ────────────────────────────────────────────────────────────────

    def mark_rescue_completed(self, robot_name: str) -> None:
        """该机器人完成障碍物清除后调用，后续不再被选为救援机器人。"""
        if robot_name:
            self._rescue_completed_robots.add(robot_name)

    def clear_rescue_session_flags(self) -> None:
        """环境 reset 时清空。"""
        self._rescue_completed_robots.clear()

    def _build_rescue_panel_candidates(self, obs_pos: Tuple[float, float, float]) -> List[dict]:
        """生成侧栏展示用候选：包含全部在场景中的机器人；非首推项仍展示供人类决策。"""
        candidates: List[dict] = []
        for rname, rinfo in RESCUE_PANEL_ROSTER.items():
            if rname not in self.base_env.scene.articulations:
                continue
            try:
                pos = get_robot_pos(self.base_env, rname)
                dist = math.hypot(pos[0] - obs_pos[0], pos[1] - obs_pos[1])
                has_task = not self.nav.task_queues[rname].is_empty()
            except Exception:
                continue

            has_arm = bool(rinfo["has_arm"])
            advised = True
            advise_note = ""

            if not has_arm:
                advised = False
                advise_note = (
                    "未搭载机械臂，标准 UR5 清障流程无法执行；OpenClaw 仍可选择"
                    if self._is_openclaw()
                    else "未搭载机械臂，标准 UR5 清障流程无法执行；操作员仍可选择"
                )
            elif rname in self._rescue_completed_robots:
                advised = False
                advise_note = (
                    "本会话内已执行过一次障碍物清除，OpenClaw 不应重复选择"
                    if self._is_openclaw()
                    else "本会话内已执行过一次障碍物清除，非系统首选；操作员可强制改派"
                )
            elif (
                rname == self.extinguisher_robot
                and rname not in self._extinguisher_dropped_robots
            ):
                advised = False
                advise_note = "正在执行灭火器运送；派遣时将先触发就地放下再继续救援"

            candidates.append({
                "name": rname,
                "type": rinfo["type"],
                "arm": rinfo["arm"],
                "terrain": rinfo["terrain"],
                "has_arm": has_arm,
                "pos": [round(pos[0], 1), round(pos[1], 1)],
                "distance": round(dist, 1),
                "has_task": has_task,
                "advised": advised,
                "advise_note": advise_note,
            })

        # 侧栏顺序：首推（advised）在上 → 有臂非首推 → 无机械臂（如 carter_1）固定最下；各档内按距障碍物远近
        def _panel_order_key(c: dict) -> Tuple[int, float]:
            if not c.get("has_arm"):
                tier = 2
            elif c.get("advised"):
                tier = 0
            else:
                tier = 1
            return (tier, float(c["distance"]))

        candidates.sort(key=_panel_order_key)
        return candidates

    def _recommended_candidate_name(self, candidates: List[dict]) -> Optional[str]:
        def _dispatchable(c: dict) -> bool:
            name = str(c.get("name", ""))
            return bool(c.get("has_arm")) and (name not in self._rescue_completed_robots)

        for c in candidates:
            if c.get("advised") and _dispatchable(c):
                return c["name"]
        for c in candidates:
            if _dispatchable(c):
                return c["name"]
        return candidates[0]["name"] if candidates else None

    def _choose_rescue_robot_with_llm(self, candidates: List[dict], obs_pos: Tuple[float, float, float]) -> Tuple[Optional[str], str, bool]:
        if not RESCUE_USE_LLM or self._is_openclaw() or self._rescue_llm_kwargs_getter is None:
            # 直接派遣本地推荐（首推机器人，通常为 m20_1），不走 LLM，避免主线程阻塞。
            return self._recommended_candidate_name(candidates), "", True
        try:
            from .rescue_llm import choose_rescue_robot_with_llm
            kwargs = self._rescue_llm_kwargs_getter() or {}
            return choose_rescue_robot_with_llm(
                obstacle_pos=obs_pos,
                candidates=candidates,
                llm_kwargs=kwargs,
                fallback_fn=self._recommended_candidate_name,
                blocked_robot="carter_1",
            )
        except Exception as exc:
            return self._recommended_candidate_name(candidates), f"[LLM_ERROR] {exc}", True

    def _resolve_dispatch_robot(self, preferred: Optional[str]) -> Optional[str]:
        """选择可派遣救援机器人：已执行过清障的机器人不再参与派遣。"""
        pref = str(preferred or "").strip()
        if self._is_openclaw():
            return pref if pref and pref not in self._rescue_completed_robots else None
        if pref and pref not in self._rescue_completed_robots:
            return pref
        rec = self._recommended_candidate_name(self.rescue_candidates or [])
        if rec and rec not in self._rescue_completed_robots:
            return rec
        return None

    def _dispatch_rescue_after_approval(self) -> None:
        """人类批准或超时自动批准后的派遣入口；若选中机器人仍扛灭火器则先中断放下。"""
        robot = self._resolve_dispatch_robot(self.state.rescue_robot)
        if not robot:
            self.push_chat(
                "sys",
                self._system_sender(),
                "⚠️ 当前无可派遣救援机器人（候选可能已执行过清障或不具备机械臂）。",
            )
            return
        if robot != self.state.rescue_robot:
            self.push_chat(
                "sys",
                self._system_sender(),
                f"ℹ️ 原选择机器人不可重复执行清障，已自动切换为 {robot}。",
            )
        self.state.rescue_robot = robot
        if self.ur5 and robot == self.extinguisher_robot:
            self.dispatch_rescue_robot_with_interrupt(robot, self.nav, self.ur5)
        else:
            self._dispatch_rescue()

    def _snap_nav_goal_to_free(self, target_xy: Tuple[float, float]) -> Tuple[Tuple[float, float], str]:
        """将目标点吸附到最近可导航自由栅格，避免把任务目标落在膨胀障碍/墙内。"""
        planner = getattr(self.navigator, "_planner", None)
        if planner is None:
            return target_xy, ""
        try:
            ti, tj = planner.world_to_grid(float(target_xy[0]), float(target_xy[1]))
            ni, nj = planner._nearest_free(ti, tj, max_radius=30)
            if not planner.is_free(ni, nj):
                return target_xy, "（警告：附近未找到可导航自由栅格，保留原目标）"
            wx, wy = planner.grid_to_world(ni, nj)
            snapped = (float(wx), float(wy))
            drift = math.hypot(snapped[0] - target_xy[0], snapped[1] - target_xy[1])
            if drift > 0.05:
                return snapped, f"（已吸附到最近可达点，偏移 {drift:.2f}m）"
            return snapped, ""
        except Exception:
            return target_xy, ""

    def on_extinguisher_dropped(self, robot_name: str) -> None:
        """UR5Manager 回调：灭火器已放下，将机器人加入候选。"""
        if robot_name == self.extinguisher_robot:
            self.extinguisher_robot = None
            self._extinguisher_dropped_robots.add(robot_name)
            _log(f"✅ {robot_name} 灭火器已放下，现可参与救援候选")
            self.push_chat(
                "sys", self._system_sender(),
                f"✅ {robot_name} 已完成灭火器放下，现已加入救援候选列表。"
            )
            if self.state.phase == RescuePhase.AWAITING_APPROVAL:
                self._refresh_candidates()

    def _refresh_candidates(self) -> None:
        """动态刷新候选列表（灭火器放下后 advised 状态变化等）。"""
        self.rescue_candidates = self._build_rescue_panel_candidates(
            self.state.obstacle_position
        )
        self._broadcast()

    def dispatch_rescue_robot_with_interrupt(self, robot_name: str, nav, ur5) -> None:
        """派遣救援机器人，若其正在搬运灭火器则先中断放下再派遣。"""
        interrupted = ur5.interrupt_for_rescue(robot_name)
        if interrupted:
            self._pending_rescue_robot = robot_name
            self._pending_rescue_nav = nav
            self.push_chat(
                "sys", self._system_sender(),
                f"⚡ {robot_name} 正在搬运灭火器，已触发就地放下流程。\n"
                f"放下完成后将立即前往执行救援任务。"
            )
        else:
            self._do_dispatch_rescue(robot_name, nav)

    def on_extinguisher_dropped_for_rescue(
        self, robot_name: str, drop_pos: Tuple[float, float, float]
    ) -> None:
        """UR5Manager 回调：就地放下完成，现在可以派遣救援。"""
        if self._pending_rescue_robot == robot_name:
            pending_nav = self._pending_rescue_nav
            self._pending_rescue_robot = None
            self._pending_rescue_nav = None
            self._do_dispatch_rescue(robot_name, pending_nav)

    def _do_dispatch_rescue(self, robot_name: str, nav) -> None:
        """实际执行救援派遣（内部方法）。"""
        self.state.rescue_robot = robot_name
        self._dispatch_rescue()

    def on_rescue_complete_check_ext_return(self, robot_name: str, nav, ur5) -> None:
        """救援完成后检查是否需要追加「取回灭火器」任务。"""
        from .navigation import RobotTask as NavRobotTask
        drop_pos = ur5.get_ext_drop_position(robot_name)
        if drop_pos is None:
            return
        retrieve_task = NavRobotTask(
            task_id=f"retrieve_ext_{robot_name}",
            task_type="retrieve_ext",
            subtask_name="取回灭火器",
            subtask_colour="yellow",
            target_xy=(drop_pos[0], drop_pos[1]),
            waypoints=[],
            priority=0,
        )
        nav.task_queues[robot_name].push_back(retrieve_task)
        self.push_chat(
            "sys", self._system_sender(),
            f"✅ {robot_name} 救援完成，已追加「取回灭火器」任务，"
            f"将前往坐标 ({drop_pos[0]:.1f}, {drop_pos[1]:.1f}) 取回灭火器。"
        )
        _log(f"🔄 {robot_name} 救援完成，追加取回灭火器任务")

    def start_monitoring(self):
        self.state.phase = RescuePhase.WAITING_PATROL
        self.state.start_time = time.time()
        _log("障碍物救援模块已启动，等待 Carter1 巡逻开始...")

    def update(self):
        if self.state.phase != self._last_phase_logged:
            # 静默隐藏 RESCUE_DWELL：进入时跳过日志；离开时把来源相位伪装回
            # CARTER_RESUMING——观察者看到的日志轨迹与无 dwell 完全相同。
            _src = self._last_phase_logged
            _dst = self.state.phase
            if _dst == RescuePhase.RESCUE_DWELL:
                pass  # 进入 dwell：完全不打日志
            else:
                if _src == RescuePhase.RESCUE_DWELL:
                    _src = RescuePhase.CARTER_RESUMING
                _log(
                    f"阶段切换: {_src.name if _src else 'None'}"
                    f" -> {_dst.name} (rescue_robot={self.state.rescue_robot})"
                )
            self._last_phase_logged = _dst
        self._consume_approval_commands()
        phase = self.state.phase
        if phase == RescuePhase.WAITING_PATROL:
            self._wait_patrol_start()
        elif phase == RescuePhase.MONITORING:
            self._check_carter1_position()
        elif phase == RescuePhase.OBSTACLE_DROPPED:
            self._check_carter_blocked()
        elif phase == RescuePhase.CARTER_BLOCKED:
            self._start_rescue_discussion()
        elif phase == RescuePhase.AWAITING_APPROVAL:
            self._check_approval()
        elif phase == RescuePhase.RESCUE_DISPATCHED:
            self._check_rescue_arrival()
        elif phase == RescuePhase.REMOVING_OBSTACLE:
            self._arm_extend()
        elif phase == RescuePhase.DRAGGING:
            self._drag_obstacle()
        elif phase == RescuePhase.CARTER_RESUMING:
            self._resume_carter1()
        elif phase == RescuePhase.RESCUE_DWELL:
            self._wait_after_drag()
        elif phase == RescuePhase.RESCUE_RETURNING:
            self._check_rescue_return()

    def handle_rescue_approval(self, data: dict):
        self._approval_queue.put(data)

    def _consume_approval_commands(self):
        """Consume approval/switch commands from dashboard side panel."""
        while True:
            try:
                data = self._approval_queue.get_nowait()
            except Empty:
                break
            self._handle_approval_command(data)

    def _handle_approval_command(self, data: dict):
        action = str(data.get("action", "approve"))
        robot = data.get("robot", self.state.rescue_robot)
        if not robot:
            return
        robot = str(robot)

        if self.state.phase == RescuePhase.AWAITING_APPROVAL:
            if action in ("approve", "switch", "reassign"):
                if robot in self._rescue_completed_robots:
                    self.push_chat(
                        "sys",
                        self._system_sender(),
                        f"⚠️ {robot} 本会话已执行过一次清障，不再重复分配。请改派其他机器人。",
                    )
                    return
                self.state.rescue_robot = robot
                _log(f"✅ {self._operator_sender()} 批准救援: {robot}")
                self.push_chat("sys", self._operator_sender(), f"✅ 批准派遣 {robot} 前往清除障碍物。")
                self._dispatch_rescue_after_approval()
            return

        # Allow dynamic reassignment while robot is still on the way.
        if self.state.phase == RescuePhase.RESCUE_DISPATCHED:
            if robot == self.state.rescue_robot:
                return
            if robot in self._rescue_completed_robots:
                self.push_chat(
                    "sys",
                    self._system_sender(),
                    f"⚠️ {robot} 本会话已执行过一次清障，不再重复分配。请改派其他机器人。",
                )
                return
            old_robot = self.state.rescue_robot
            self._cancel_rescue_dispatch(old_robot)
            self.state.rescue_robot = robot
            self.push_chat(
                "sys",
                self._operator_sender(),
                f"🔁 改派救援机器人：{old_robot} → {robot}",
            )
            _log(f"🔁 改派救援机器人: {old_robot} -> {robot}")
            self._dispatch_rescue_after_approval()
            return

    def _cancel_rescue_dispatch(self, robot: Optional[str]) -> None:
        """Best-effort cancel current dispatched rescue task for a robot."""
        if not robot:
            return
        try:
            self.nav.hold_position.discard(robot)
        except Exception:
            pass

        tq = self.nav.task_queues.get(robot)
        if tq and not tq.is_empty():
            top = tq.peek()
            if top is not None and getattr(top, "task_type", "") == "rescue":
                tq.pop()
            nxt = tq.peek()
            if nxt is not None:
                try:
                    self.nav._start_task(robot, nxt)
                except Exception:
                    pass
            else:
                try:
                    self.nav._start_patrol(robot)
                except Exception:
                    pass

    @property
    def is_active(self) -> bool:
        return self.state.phase not in (RescuePhase.IDLE, RescuePhase.COMPLETED)

    def _broadcast(self):
        """Push current rescue state to the dashboard via data_server."""
        try:
            carter_pos = get_robot_pos(self.base_env, "carter_1")
            cp = [round(carter_pos[0], 1), round(carter_pos[1], 1)]
        except Exception:
            cp = [0, 0]

        obs = self.state.obstacle_position
        heuristic_rec = None if self._is_openclaw() else self._recommended_candidate_name(self.rescue_candidates)
        rec_name = None if self._is_openclaw() else (self.state.rescue_robot or heuristic_rec)
        # 对 dashboard 隐藏 RESCUE_DWELL：用 CARTER_RESUMING 顶替，外观与旧流程一致
        _public_phase = (
            RescuePhase.CARTER_RESUMING if self.state.phase == RescuePhase.RESCUE_DWELL
            else self.state.phase
        )
        data = {
            "phase": _public_phase.name.lower(),
            "planner_mode": self.planner_mode,
            "decision_endpoint": "/api/openclaw/rescue" if self._is_openclaw() else "/api/rescue/approve",
            "obstacle_pos": [round(obs[0], 1), round(obs[1], 1)],
            "obstacle_size": f"{OBSTACLE_LENGTH:.1f}m x {OBSTACLE_WIDTH:.1f}m x {OBSTACLE_HEIGHT:.1f}m",
            "carter1_pos": cp,
            "rescue_robot": self.state.rescue_robot,
            "candidates": self.rescue_candidates,
            "recommended": rec_name,
            "heuristic_recommended": heuristic_rec,
        }
        self.update_rescue_state(data)

    # -- WAITING_PATROL -------------------------------------------------------

    def _wait_patrol_start(self):
        elapsed = time.time() - self.state.start_time
        if elapsed < CARTER1_PATROL_START_DELAY or self.state.patrol_started:
            return
        if "carter_1" not in self.base_env.scene.articulations:
            self.state.phase = RescuePhase.COMPLETED
            return
        self.nav.waiting_for_task["carter_1"] = False
        self.state.patrol_started = True
        self.state.phase = RescuePhase.MONITORING
        _log(f"Carter1 巡逻已启动（延迟 {elapsed:.1f}s）")
        self.push_chat("sys", self._system_sender(), "📡 Carter1 已启动巡逻任务，沿默认路线执行数据采集。")

    # -- MONITORING -----------------------------------------------------------

    def _check_carter1_position(self):
        try:
            pos = get_robot_pos(self.base_env, "carter_1")
            if pos[1] > OBSTACLE_CORRIDOR_Y_TRIGGER and not self.state.obstacle_spawned:
                _log(f"Carter1 到达触发区 y={pos[1]:.2f} > {OBSTACLE_CORRIDOR_Y_TRIGGER}")
                self._spawn_obstacle()
        except Exception:
            pass

    def _spawn_obstacle(self):
        create_obstacle_barrier(
            self.stage, OBSTACLE_PRIM_PATH, OBSTACLE_POSITION,
            length=OBSTACLE_LENGTH, width=OBSTACLE_WIDTH, height=OBSTACLE_HEIGHT,
            update_fn=None if self._is_robot_baseline() else self.sim_app.update,
        )
        self.state.obstacle_spawned = True
        self.state.obstacle_position = OBSTACLE_POSITION
        self.state.phase = RescuePhase.OBSTACLE_DROPPED
        self.push_chat(
            "sys", "场景事件",
            f"⚠️ 突发事件！一个长条障碍物突然落在通道上！\n"
            f"位置: ({OBSTACLE_POSITION[0]:.1f}, {OBSTACLE_POSITION[1]:.1f})\n"
            f"尺寸: {OBSTACLE_LENGTH:.1f}m × {OBSTACLE_WIDTH:.1f}m × {OBSTACLE_HEIGHT:.1f}m\n"
            f"通道被完全封堵！",
        )
        print(f"\n{'='*60}")
        _log(f"⚠️  障碍物已生成！位置 ({OBSTACLE_POSITION[0]:.2f}, {OBSTACLE_POSITION[1]:.2f})")
        print(f"{'='*60}\n")

        self.nav.carter1_obstacle_pause = True
        self.navigator.set_active("carter_1", False)
        # 障碍物是运行时动态生成，不在静态导航地图里。这里清空 Carter1 旧路径，
        # 避免 Dashboard 继续显示「直线穿障」的历史规划线。
        try:
            c1_pos = get_robot_pos(self.base_env, "carter_1")
            hold_wp = (float(c1_pos[0]), float(c1_pos[1]), 0.0)
            self.nav.robot_waypoints["carter_1"] = [hold_wp]
            self.nav.robot_waypoint_indices["carter_1"] = 0
            if hasattr(self.navigator, "paths"):
                self.navigator.paths["carter_1"] = [hold_wp]
            if hasattr(self.navigator, "indices"):
                self.navigator.indices["carter_1"] = 0
        except Exception:
            pass
        self.nav._stuck_last_pos.pop("carter_1", None)
        self.nav._stuck_last_time.pop("carter_1", None)
        self.nav._stuck_replan_cd.pop("carter_1", None)

        # 障碍物已落地且 Carter1 已暂停，无法再「靠近」到旧判定的 1.5m 内；直接视为被阻挡并进入救援流程。
        try:
            pos = get_robot_pos(self.base_env, "carter_1")
            self._on_carter_blocked(pos)
        except Exception:
            pass

    # -- OBSTACLE_DROPPED -----------------------------------------------------

    def _check_carter_blocked(self):
        """兼容：若 spawn 时未进入 CARTER_BLOCKED，仍用接近判定兜底。"""
        if self.state.phase != RescuePhase.OBSTACLE_DROPPED:
            return
        try:
            pos = get_robot_pos(self.base_env, "carter_1")
            dist_to_obs = abs(pos[1] - OBSTACLE_POSITION[1])
            if dist_to_obs < 1.5:
                if self.state.block_detected_time == 0.0:
                    self.state.block_detected_time = time.time()
                    _log(f"Carter1 接近障碍物 (距离 {dist_to_obs:.2f}m)")
                if time.time() - self.state.block_detected_time > 3.0:
                    self._on_carter_blocked(pos)
        except Exception:
            pass

    def _on_carter_blocked(self, carter_pos):
        self.state.phase = RescuePhase.CARTER_BLOCKED
        self.nav.waiting_for_task["carter_1"] = True
        self.nav.arrived_flags["carter_1"] = True
        self.push_chat(
            "bot", "carter_1",
            f"🚫 我是 Carter1（差速无人车，搭载 Orsus 传感器套件）。\n"
            f"前方通道被一个长条障碍物阻挡，我无法通过！\n"
            f"我的当前位置: ({carter_pos[0]:.1f}, {carter_pos[1]:.1f})\n"
            f"障碍物位置: ({OBSTACLE_POSITION[0]:.1f}, {OBSTACLE_POSITION[1]:.1f})\n"
            f"我没有机械臂，无法自行搬运障碍物。\n"
            f"通过 Orsus 摄像头已拍摄障碍物图像，正在上传至系统。\n"
            f"请求具有搬运能力的机器人前来支援！",
        )
        print(f"\n{'='*60}")
        _log(f"🚫 Carter1 被障碍物阻挡！位置 ({carter_pos[0]:.1f}, {carter_pos[1]:.1f})")
        print(f"{'='*60}\n")

    # -- CARTER_BLOCKED → DISCUSSING ------------------------------------------

    def _start_rescue_discussion(self):
        self.state.phase = RescuePhase.DISCUSSING
        if self._is_openclaw():
            self.push_chat(
                "emos",
                self._system_sender(),
                "📡 接收到 Carter1 的求助信号及障碍物图像！\n"
                "正在汇总场景中全部机器人状态；请读取 /api/openclaw/context 中的 rescue 字段，"
                "并向 /api/openclaw/rescue 提交清障机器人选择。",
            )
        else:
            self.push_chat(
                "emos",
                self._system_sender(),
                "📡 接收到 Carter1 的求助信号及障碍物图像！\n"
                "正在汇总场景中全部机器人状态；侧栏将列出所有节点供人类操作员决策。",
            )

        obs_pos = OBSTACLE_POSITION
        candidates = self._build_rescue_panel_candidates(obs_pos)
        self.rescue_candidates = candidates

        _log("─" * 50)
        _log(f"🤖 {self._planner_label()} 障碍物清除决策请求")
        _log("─" * 50)

        situation_lines = [
            "🔍 障碍物清除任务 — 候选机器人状态（等待 OpenClaw 派发）："
            if self._is_openclaw()
            else "🔍 障碍物清除任务 — 场景内机器人状态（含非首选）："
        ]
        for c in candidates:
            if self._is_openclaw():
                tag = "【可清障】" if c.get("has_arm") else "【不建议】"
            else:
                tag = "【首推】" if c.get("advised") else "【非首选】"
            line = (
                f"  🤖 {tag} {c['name']} ({c['type']}): 位置 ({c['pos'][0]}, {c['pos'][1]})，"
                f"距障碍物 {c['distance']}m，机械臂: {c['arm']}，{c['terrain']}"
            )
            if c.get("advise_note"):
                line += f" — {c['advise_note']}"
            situation_lines.append(line)
            _log(line)
        self.push_chat("emos", f"{self._planner_label()} 态势分析", "\n".join(situation_lines))

        for c in candidates:
            if c.get("advised"):
                speech = (
                    f"收到 Carter1 求助信号。我是 {c['name']}（{c['type']}），"
                    f"当前位置 ({c['pos'][0]}, {c['pos'][1]})，距障碍物 {c['distance']}m。"
                    f"我搭载 {c['arm']} 机械臂，{c['terrain']}，可以前往搬运障碍物。"
                )
            else:
                speech = (
                    f"收到 Carter1 求助信号。我是 {c['name']}（{c['type']}），"
                    f"当前位置 ({c['pos'][0]}, {c['pos'][1]})，距障碍物 {c['distance']}m。"
                    f"{c.get('advise_note') or ('等待 OpenClaw 判断是否选择我执行派遣。' if self._is_openclaw() else '当前非系统首推，仍可由人类操作员在侧栏选择我执行派遣。')}"
                )
            self.push_chat("bot", c["name"], speech)

        if not candidates:
            self.push_chat("emos", self._system_sender(), "❌ 场景中未找到可展示的机器人节点！")
            self.state.phase = RescuePhase.COMPLETED
            return

        if self._is_openclaw():
            self.state.rescue_robot = None
            self.push_chat(
                "emos",
                "OpenClaw 决策请求",
                "📋 已列出全部候选机器人和约束；仿真器不会生成默认派发。\n"
                "请 OpenClaw 通过 /api/openclaw/rescue 返回最终清障机器人。",
            )
            _log("⏳ OpenClaw 模式：等待 /api/openclaw/rescue 派发救援机器人")
        else:
            rec_name, llm_raw, used_fallback = self._choose_rescue_robot_with_llm(candidates, obs_pos)
            self.state.rescue_robot = rec_name
            rec = next((x for x in candidates if x["name"] == rec_name), candidates[0])
            if llm_raw:
                self.push_chat(
                    "emos",
                    "EMOS 突发救援 LLM",
                    (
                        f"LLM 选择清障机器人: {rec_name or '(无有效选择)'}"
                        f"{'（已回退本地推荐）' if used_fallback else ''}\n"
                        f"原始输出: {llm_raw[:500]}"
                    ),
                )
                _log(
                    f"🧠 救援 LLM 选择: {rec_name or '(none)'} "
                    f"fallback={used_fallback} raw={llm_raw[:160]!r}"
                )
            if rec.get("advised"):
                self.push_chat(
                    "emos",
                    f"{self._planner_label()} 决策请求",
                    f"🎯 LLM 建议清障机器人：{rec['name']}（{rec['type']}）。\n"
                    f"候选信息：距障碍物 {rec['distance']}m，机械臂: {rec['arm']}。\n\n"
                    "📋 侧栏已列出全部机器人；人类操作员可改派任意节点。",
                )
            else:
                self.push_chat(
                    "emos",
                    f"{self._planner_label()} 决策请求",
                    f"🎯 LLM 当前选中 {rec['name']}（{rec['type']}，{rec['distance']}m）。\n"
                    "📋 侧栏已列出全部机器人，请以人类决策为准确认或改派。",
                )
            _log(f"🎯 LLM/默认救援机器人: {rec_name} (fallback={used_fallback}, 首推={'是' if rec.get('advised') else '否'})")

        self.state.phase = RescuePhase.AWAITING_APPROVAL
        self.state.approval_wait_start = time.time()
        self._broadcast()

    # -- AWAITING_APPROVAL ----------------------------------------------------

    def _check_approval(self):
        if self._is_openclaw():
            return
        if time.time() - self.state.approval_wait_start > RESCUE_AUTO_APPROVE_TIMEOUT:
            _log(f"⏰ 超时 {RESCUE_AUTO_APPROVE_TIMEOUT}s，自动批准 {self.state.rescue_robot}")
            self.push_chat("sys", self._system_sender(),
                           f"⏰ 人类操作员未响应，自动批准派遣 {self.state.rescue_robot}。")
            self._dispatch_rescue_after_approval()

    # -- 派遣 -----------------------------------------------------------------

    def _dispatch_rescue(self):
        robot = self.state.rescue_robot
        if not robot:
            return

        has_arm = any(
            c["name"] == robot and c.get("has_arm")
            for c in self.rescue_candidates
        )
        if not has_arm:
            self.push_chat(
                "sys",
                self._system_sender(),
                f"⚠️ 已按{self._operator_sender()}指令派遣 {robot}；该节点未配置 UR5 清障流程，"
                f"伸臂与拖拽阶段可能异常，请知悉。",
            )

        # 保存救援前状态（用于救援完成后恢复）
        self.state.rescue_robot_saved_waypoints = list(self.nav.robot_waypoints.get(robot, []))
        self.state.rescue_robot_saved_index = self.nav.robot_waypoint_indices.get(robot, 0)
        self.state.rescue_robot_saved_waiting = self.nav.waiting_for_task.get(robot, True)

        obs_pos = self.state.obstacle_position
        # 新逻辑：优先使用显式作业点（RESCUE_ARM_START_XY），保证“先到点再搬运”。
        if RESCUE_NAV_USE_ARM_START_POINT:
            requested_target_xy = (float(RESCUE_ARM_START_XY[0]), float(RESCUE_ARM_START_XY[1]))
        else:
            # 兼容旧逻辑：障碍物外侧沿 -Y 偏移停靠
            requested_target_xy = (float(obs_pos[0]), float(obs_pos[1]) - RESCUE_NAV_TARGET_OFFSET_Y)
        target_xy, snap_note = self._snap_nav_goal_to_free(requested_target_xy)
        self._rescue_nav_goal_xy = target_xy

        try:
            from .navigation import RobotTask
            rescue_task = RobotTask(
                task_id=f"rescue_{robot}",
                task_type="rescue",
                subtask_name="障碍物清除",
                subtask_colour="orange",
                target_xy=target_xy,
                waypoints=[],
                priority=1,
            )
            # 通过 task_queues 系统注入，priority=True 插入队头
            self.nav.interrupt_patrol_with_task(robot, rescue_task, priority=True)

            self.state.phase = RescuePhase.RESCUE_DISPATCHED
            # 输出救援路径诊断信息
            try:
                pos = get_robot_pos(self.base_env, robot)
                _rescue_straight = math.hypot(pos[0] - target_xy[0], pos[1] - target_xy[1])
                _log(
                    f"🚀 {robot} 已通过 task_queues 派遣 → ({target_xy[0]:.1f}, {target_xy[1]:.1f})"
                    f"（请求点 ({requested_target_xy[0]:.1f}, {requested_target_xy[1]:.1f}){snap_note}；"
                    f"当前位置 ({pos[0]:.2f}, {pos[1]:.2f})，直线距离 {_rescue_straight:.1f}m）"
                )
            except Exception:
                _log(
                    f"🚀 {robot} 已通过 task_queues 派遣 → ({target_xy[0]:.1f}, {target_xy[1]:.1f})"
                    f"（请求点 ({requested_target_xy[0]:.1f}, {requested_target_xy[1]:.1f}){snap_note}）"
                )
            self.push_chat("emos", f"{self._planner_label()} 调度",
                           f"🚀 {robot} 已出发！目标: 障碍物位置 ({obs_pos[0]:.1f}, {obs_pos[1]:.1f})\n"
                           f"导航目标设为 ({target_xy[0]:.1f}, {target_xy[1]:.1f})"
                           f"{snap_note if snap_note else ''}")
            self.push_chat("bot", robot,
                           f"收到指令！正在前往障碍物位置 ({obs_pos[0]:.1f}, {obs_pos[1]:.1f})，"
                           f"我将使用 UR5 机械臂搬运障碍物。")
            self._broadcast()
        except Exception as e:
            _log(f"❌ 派遣异常: {e}")

    # -- RESCUE_DISPATCHED: 等待到达 ------------------------------------------

    def _check_rescue_arrival(self):
        robot = self.state.rescue_robot
        if not robot:
            return

        # ── Task-queue consistency guard ──
        # If the rescue task was unexpectedly popped (e.g. by a planning failure
        # before the guard-mode fix), re-inject it so the robot keeps heading to
        # the obstacle.
        tq = self.nav.task_queues.get(robot)
        cur_task = tq.peek() if tq and not tq.is_empty() else None
        if cur_task is None or getattr(cur_task, "task_type", "") != "rescue":
            manual_override = (
                getattr(cur_task, "task_type", "") == "manual"
                and robot in getattr(self.nav, "manual_override_agents", set())
            )
            if manual_override:
                if not hasattr(self, "_rescue_manual_override_log_t") or time.time() - self._rescue_manual_override_log_t > 5.0:
                    _log(f"🧭 {robot} 救援中由人工目标接管导航，暂不重注入 rescue 任务")
                    self._rescue_manual_override_log_t = time.time()
            else:
                goal = self._rescue_nav_goal_xy
                if goal is not None:
                    _log(f"⚠️ {robot} 救援任务在队列中丢失（队头={getattr(cur_task, 'task_type', None)}），重新注入")
                    from .navigation import RobotTask as _NRT
                    _reinject = _NRT(
                        task_id=f"rescue_{robot}_reinject",
                        task_type="rescue",
                        subtask_name="障碍物清除",
                        subtask_colour="orange",
                        target_xy=goal,
                        waypoints=[],
                        priority=1,
                    )
                    self.nav.interrupt_patrol_with_task(robot, _reinject, priority=True)
                    return

        try:
            pos = get_robot_pos(self.base_env, robot)
            goal = self._rescue_nav_goal_xy
            if goal is None:
                obs_pos = self.state.obstacle_position
                if RESCUE_NAV_USE_ARM_START_POINT:
                    goal = (float(RESCUE_ARM_START_XY[0]), float(RESCUE_ARM_START_XY[1]))
                else:
                    goal = (float(obs_pos[0]), float(obs_pos[1]) - RESCUE_NAV_TARGET_OFFSET_Y)
            dist_to_goal = math.hypot(pos[0] - goal[0], pos[1] - goal[1])
            ox = float(self.state.obstacle_position[0])
            oy = float(self.state.obstacle_position[1])
            dist_to_obs = math.hypot(pos[0] - ox, pos[1] - oy)

            arrived_at_standoff = dist_to_goal < RESCUE_NAV_ARRIVAL_EPS
            arrived_by_contact = (
                RESCUE_ALLOW_CONTACT_ARRIVAL_FALLBACK
                and (dist_to_obs <= RESCUE_CONTACT_ARRIVAL_DIST)
            )
            ax, ay = float(RESCUE_ARM_START_XY[0]), float(RESCUE_ARM_START_XY[1])
            dist_to_arm_start = math.hypot(pos[0] - ax, pos[1] - ay)
            arrived_at_arm_start = dist_to_arm_start <= RESCUE_ARM_START_RADIUS

            # Timeout-based contact fallback: if robot has been dispatched for a long time
            # and is within RESCUE_TIMEOUT_FALLBACK_DIST of the obstacle, accept it.
            # 旧值 90s + 2.76m 会让兜底在距障 2.7m 远就触发，演示出现"隔空 attach"问题。
            # 新值 60s + 1.38m（见 runtime/settings）：提前介入 + 更接近障碍才触发。
            _dispatch_elapsed = time.time() - self.state.approval_wait_start
            arrived_by_timeout = (
                _dispatch_elapsed > RESCUE_TIMEOUT_FALLBACK_S
                and dist_to_obs <= RESCUE_TIMEOUT_FALLBACK_DIST
            )

            if RESCUE_NAV_USE_ARM_START_POINT:
                arrived = arrived_at_arm_start or arrived_at_standoff or arrived_by_timeout
            else:
                arrived = arrived_at_standoff or arrived_by_contact or arrived_at_arm_start or arrived_by_timeout

            if not arrived and not hasattr(self, '_rescue_arrival_log_t'):
                self._rescue_arrival_log_t = time.time()
            if not arrived and hasattr(self, '_rescue_arrival_log_t'):
                if time.time() - self._rescue_arrival_log_t > 5.0:
                    self._rescue_arrival_log_t = time.time()
                    _log(f"📍 {robot} 救援中: pos=({pos[0]:.2f},{pos[1]:.2f}) → goal={goal} "
                         f"d_goal={dist_to_goal:.2f}m d_obs={dist_to_obs:.2f}m d_arm={dist_to_arm_start:.2f}m"
                         f"（阈值: standoff={RESCUE_NAV_ARRIVAL_EPS} contact={RESCUE_CONTACT_ARRIVAL_DIST} "
                         f"arm_r={RESCUE_ARM_START_RADIUS}）")
            if arrived:
                if arrived_at_standoff:
                    _log(f"✅ {robot} 到达救援停靠点！距导航目标 {dist_to_goal:.2f}m")
                    self.push_chat(
                        "bot",
                        robot,
                        f"✅ 已到达障碍物外侧停靠点（距目标 {dist_to_goal:.1f}m）。\n"
                        f"正在展开 UR5 机械臂，准备搬运障碍物...",
                    )
                elif arrived_at_arm_start:
                    _log(
                        f"✅ {robot} 已到达伸臂作业区（{ax:.2f},{ay:.2f}）附近 "
                        f"（{dist_to_arm_start:.2f}m ≤ {RESCUE_ARM_START_RADIUS}m）→ 开始搬运",
                    )
                    self.push_chat(
                        "bot",
                        robot,
                        f"✅ 已到达障碍物作业区（距作业点 {dist_to_arm_start:.1f}m）。\n"
                        f"正在展开 UR5 机械臂，准备搬运障碍物...",
                    )
                elif arrived_by_timeout:
                    _log(
                        f"✅ {robot} 派遣超时兜底到达（{_dispatch_elapsed:.0f}s, "
                        f"d_obs={dist_to_obs:.2f}m）→ 进入伸臂",
                    )
                    self.push_chat(
                        "bot",
                        robot,
                        f"✅ 已接近障碍物（距障碍约 {dist_to_obs:.1f}m，超时兜底）。\n"
                        f"开始 UR5 搬运。",
                    )
                else:
                    _log(
                        f"✅ {robot} 已贴近障碍物（距障碍中心 {dist_to_obs:.2f}m，"
                        f"未到理想停靠点 {dist_to_goal:.2f}m）→ 仍进入伸臂",
                    )
                    self.push_chat(
                        "bot",
                        robot,
                        f"✅ 已贴近障碍物（距障碍约 {dist_to_obs:.1f}m）。\n"
                        f"未完全到达规划停靠点，按接触判定开始 UR5 搬运。",
                    )
                self.state.phase = RescuePhase.REMOVING_OBSTACLE
                self.state.removal_start_time = 0.0
                self.nav.waiting_for_task[robot] = True
                self.nav.arrived_flags[robot] = True
                self.nav.hold_position.add(robot)
                self._broadcast()
        except Exception as e:
            if not hasattr(self, "_rescue_arrival_err_log_t") or time.time() - self._rescue_arrival_err_log_t > 10.0:
                _log(f"⚠️ _check_rescue_arrival 异常: {e}")
                self._rescue_arrival_err_log_t = time.time()

    # -- REMOVING_OBSTACLE: UR5 arm extend + create visual proxy --------------

    def _arm_extend(self):
        robot = self.state.rescue_robot
        if not robot:
            return

        if self.state.removal_start_time == 0.0:
            self.state.removal_start_time = time.time()
            ox, oy, oz = self.state.obstacle_position
            tz = float(oz) + OBSTACLE_HEIGHT * 0.5 + 0.2
            target = (float(ox), float(oy), tz)
            if self.ur5 and self.obstacle_ur5_reach_enabled:
                self.ur5.start_obstacle_rescue_reach(robot, target)
                self.push_chat(
                    "bot",
                    robot,
                    "🔧 UR5 机械臂正在展开，对准障碍物...到达目标后粘连搬运。",
                )
                _log(f"🔧 {robot} 开始 UR5 机械臂伸向障碍物（目标 {target}）...")
                return
            self.push_chat(
                "bot",
                robot,
                "🔧 清障物理伸臂在当前运行模式下关闭，改用视觉代理执行搬运。",
            )
            _log(f"🔧 {robot} 跳过清障物理伸臂，直接进入视觉代理搬运")

        elapsed = time.time() - self.state.removal_start_time
        if self.ur5:
            if self.ur5.is_obstacle_rescue_reach_active and elapsed < RESCUE_ARM_REACH_TIMEOUT_S:
                return
            self.ur5.end_obstacle_rescue_reach()
        else:
            if elapsed < RESCUE_ARM_OPERATION_TIME:
                return

        proxy_path = self._create_obstacle_visual_proxy()
        if proxy_path:
            self.state.drag_proxy_path = proxy_path
            _log(f"🪄 障碍物视觉代理创建成功: {proxy_path}")
        else:
            _log("⚠️ 视觉代理创建失败，直接删除障碍物")

        self.state.phase = RescuePhase.DRAGGING
        self.state.drag_start_time = time.time()
        robot_pos = get_robot_pos(self.base_env, self.state.rescue_robot)
        self._drag_start_pos = (robot_pos[0], robot_pos[1], robot_pos[2])

        self.push_chat("bot", self.state.rescue_robot,
                       "🔧 UR5 机械臂已抓住障碍物，正在拖拽搬离通道...")
        _log(f"🔧 开始拖拽障碍物...")
        self._broadcast()

    # -- DRAGGING: move the visual proxy, then delete -------------------------

    def _drag_obstacle(self):
        elapsed = time.time() - self.state.drag_start_time
        t = min(elapsed / self.DRAG_DURATION, 1.0)

        if self._drag_start_pos and self.state.drag_proxy_path:
            robot_pos = get_robot_pos(self.base_env, self.state.rescue_robot)
            new_x = robot_pos[0] + RESCUE_ARM_REACH_X
            new_y = robot_pos[1]
            new_z = robot_pos[2] + RESCUE_ARM_REACH_Z
            self._set_prim_translate(self.state.drag_proxy_path, (new_x, new_y, new_z))

        if t >= 1.0:
            import omni.kit.commands
            try:
                omni.kit.commands.execute("DeletePrims", paths=[OBSTACLE_PRIM_PATH])
            except Exception:
                pass
            if self.state.drag_proxy_path:
                try:
                    omni.kit.commands.execute("DeletePrims", paths=[self.state.drag_proxy_path])
                except Exception:
                    pass
            self._runtime_app_update(count=5)

            self.state.obstacle_removed = True
            self.state.phase = RescuePhase.CARTER_RESUMING
            self.push_chat("bot", self.state.rescue_robot, "✅ 障碍物已被 UR5 机械臂成功搬离通道！")
            self.push_chat("emos", self._system_sender(),
                           f"🎉 {self.state.rescue_robot} 成功清除了通道障碍物！\n通知 Carter1 恢复导航。")
            _log("✅ 障碍物拖拽完成并已移除！")
            self._broadcast()

    # -- CARTER_RESUMING ------------------------------------------------------

    def _resume_carter1(self):
        """障碍物清除后恢复 Carter1 导航。

        优先恢复 **EMOS 已派发且仍在 task_queues 中的任务**（与 m20 救援完成后恢复逻辑一致）；
        若队列为空或尚无 EMOS 任务（例如仍在纯巡逻），则沿用原默认「驶出通道」目标 (-7.6, 0.3)。
        生成障碍物时 navigator 曾对 carter_1 set_active(False)，此处须重新激活。
        """
        try:
            self.nav.carter1_obstacle_pause = False
            self.nav.hold_position.discard("carter_1")
            pos = get_robot_pos(self.base_env, "carter_1")
            self.navigator.set_active("carter_1", True)

            tq = self.nav.task_queues.get("carter_1")
            cur = tq.peek() if tq and not tq.is_empty() else None
            use_emos = (
                cur is not None
                and getattr(cur, "task_type", "") == "emos"
                and cur.target_xy is not None
            )
            if use_emos:
                target = (float(cur.target_xy[0]), float(cur.target_xy[1]))
                desc = f"EMOS「{cur.subtask_name}」({target[0]:.1f}, {target[1]:.1f})"
            else:
                target = (-7.6, 0.3)
                desc = f"默认通道目标 ({target[0]:.1f}, {target[1]:.1f})"

            wps = self.navigator.set_path(
                "carter_1", (pos[0], pos[1]), target, waypoint_step=NAV_WAYPOINT_STEP
            )
            if wps:
                # EMOS 全局 suppress 时仍允许 carter_1 在任务结束后恢复巡逻（见 robot_nav._start_patrol）
                self.nav.suppress_patrol_exempt.add("carter_1")
                self.nav.robot_waypoints["carter_1"] = wps
                self.nav.robot_waypoint_indices["carter_1"] = 0
                self.nav.arrived_flags["carter_1"] = False
                self.nav.waiting_for_task["carter_1"] = False
                self.state.carter1_resumed = True
                self.push_chat(
                    "bot",
                    "carter_1",
                    f"✅ 通道已畅通！感谢 {self.state.rescue_robot} 的支援！\n"
                    f"我正在恢复导航，继续前往 {desc}。",
                )
                _log(f"Carter1 恢复导航 → {target} ({'EMOS' if use_emos else '默认'})")
            elif use_emos and cur is not None:
                _log("Carter1 直接 set_path 失败，改用 _start_task 重规划 EMOS 任务")
                self.nav.suppress_patrol_exempt.add("carter_1")
                self.nav._start_task("carter_1", cur)
                self.state.carter1_resumed = True
                self.push_chat(
                    "bot",
                    "carter_1",
                    f"✅ 通道已畅通！感谢 {self.state.rescue_robot} 的支援！\n"
                    f"正在重新规划前往 EMOS 目标「{cur.subtask_name}」。",
                )
            else:
                self.nav.suppress_patrol_exempt.add("carter_1")
                try:
                    self.nav._start_patrol("carter_1")
                except Exception:
                    pass
                self.state.carter1_resumed = True
                self.push_chat(
                    "bot",
                    "carter_1",
                    f"✅ 通道已畅通！感谢 {self.state.rescue_robot} 的支援！\n"
                    f"正在恢复巡逻 / 默认导航。",
                )
                _log(f"Carter1 恢复：无有效 EMOS 路径，尝试巡逻")
        except Exception as e:
            _log(f"Carter1 恢复导航失败: {e}")
        self._begin_post_drag_phase()
        self._broadcast()

    # -- DWELL：搬运完成后原地停留 N 秒（仅人机协作组）---------------------

    def _begin_post_drag_phase(self) -> None:
        """Carter1 已恢复后，根据 dwell 配置决定立即返回还是先原地停留。

        非人机协作组（dwell=0）保留旧行为：直接 _send_rescue_robot_back。
        人机协作组（dwell>0）静默切换到 RESCUE_DWELL，让 update() 等满 N 秒再返回。
        rescue robot 在 REMOVING_OBSTACLE 起就被加入 nav.hold_position，所以 dwell
        期间机器人天然停在原地，无需额外干预。

        说明：本分支故意不打日志、不推聊天、不在广播中区分相位——对外观测与
        旧版 CARTER_RESUMING 阶段保持完全一致（见 _broadcast / update 的相应
        屏蔽逻辑），避免在终端日志或 dashboard 上暴露多出的停留窗口。
        """
        if self.dwell_after_drag_s <= 0.0:
            self._send_rescue_robot_back()
            return
        self._dwell_start_time = time.time()
        self.state.phase = RescuePhase.RESCUE_DWELL

    def _wait_after_drag(self) -> None:
        if self._dwell_start_time <= 0.0:
            self._dwell_start_time = time.time()
        if time.time() - self._dwell_start_time < self.dwell_after_drag_s:
            return
        self._dwell_start_time = 0.0
        self._send_rescue_robot_back()
        self._broadcast()

    # -- 返回 -----------------------------------------------------------------

    def _send_rescue_robot_back(self):
        robot = self.state.rescue_robot
        if not robot:
            self.state.phase = RescuePhase.COMPLETED
            return
        try:
            self.nav.hold_position.discard(robot)
            tq = self.nav.task_queues.get(robot)
            if tq and not tq.is_empty():
                top = tq.peek()
                if top is not None and getattr(top, "task_type", None) == "rescue":
                    tq.pop()
            next_task = tq.peek() if tq and not tq.is_empty() else None
            # 队列中仍有 EMOS 任务（例如 scout_1 绿色「打开救援通道」）：从当前位置重新规划，勿用旧路点
            if next_task is not None and getattr(next_task, "task_type", None) == "emos":
                self._rescue_return_emos_resume = True
                try:
                    self.nav._start_task(robot, next_task)
                    self.state.phase = RescuePhase.RESCUE_RETURNING
                    self.push_chat(
                        "bot",
                        robot,
                        f"✅ 救援任务完成！正在继续 {self._planner_label()} 任务「{next_task.subtask_name}」"
                        f"（{next_task.target_xy[0]:.1f}, {next_task.target_xy[1]:.1f}）。",
                    )
                    self.push_chat(
                        "emos",
                        self._system_sender(),
                        f"🔄 {robot} 已恢复 {self._planner_label()} 分配任务。",
                    )
                    _log(f"🔄 {robot} 恢复 EMOS: {next_task.subtask_name}")
                    self._broadcast()
                    return
                except Exception as e:
                    self._rescue_return_emos_resume = False
                    _log(f"恢复 EMOS 任务失败，回退到路点返回: {e}")
        except Exception:
            pass
        saved_wps = self.state.rescue_robot_saved_waypoints
        if not saved_wps:
            self._on_completed()
            return
        idx = min(self.state.rescue_robot_saved_index, len(saved_wps) - 1)
        target = saved_wps[idx]
        target_xy = (target[0], target[1])
        try:
            pos = get_robot_pos(self.base_env, robot)
            wps = self.navigator.set_path(robot, (pos[0], pos[1]), target_xy, waypoint_step=NAV_WAYPOINT_STEP)
            if wps:
                self.nav.robot_waypoints[robot] = wps
                self.nav.robot_waypoint_indices[robot] = 0
                self.nav.arrived_flags[robot] = False
                self.nav.waiting_for_task[robot] = False
            self.state.phase = RescuePhase.RESCUE_RETURNING
            self.push_chat("bot", robot,
                           f"✅ 救援任务完成！正在返回之前的工作位置 ({target_xy[0]:.1f}, {target_xy[1]:.1f})。")
            self.push_chat("emos", self._system_sender(), f"🔄 {robot} 正在返回之前的任务位置。救援流程即将完成。")
            _log(f"🔄 {robot} 返回 → ({target_xy[0]:.1f}, {target_xy[1]:.1f})")
        except Exception as e:
            _log(f"返回路径规划失败: {e}")
            self._on_completed()

    def _check_rescue_return(self):
        robot = self.state.rescue_robot
        if not robot:
            self._on_completed()
            return
        if self.nav.waiting_for_task.get(robot, False) and self.nav.arrived_flags.get(robot, False):
            if self._rescue_return_emos_resume:
                self._rescue_return_emos_resume = False
                self._on_completed()
                return
            saved_wps = self.state.rescue_robot_saved_waypoints
            if saved_wps:
                self.nav.robot_waypoints[robot] = saved_wps
                self.nav.robot_waypoint_indices[robot] = self.state.rescue_robot_saved_index
                self.nav.waiting_for_task[robot] = self.state.rescue_robot_saved_waiting
            self._on_completed()

    # -- 完成 -----------------------------------------------------------------

    def _on_completed(self):
        self.state.phase = RescuePhase.COMPLETED
        if self.state.rescue_robot:
            try:
                self.nav.hold_position.discard(self.state.rescue_robot)
            except Exception:
                pass
        robot = self.state.rescue_robot or "(unknown)"
        self.push_chat("emos", self._system_sender(),
                       f"🎉 障碍物救援流程已全部完成！\n"
                       f"  ✅ Carter1 已成功通过被阻通道\n"
                       f"  ✅ {robot} 已返回之前的任务\n所有机器人恢复正常工作。")
        print(f"\n{'='*60}")
        _log(f"🎉 障碍物救援流程全部完成！")
        _log(f"   Carter1 已通过通道, {robot} 已返回")
        print(f"{'='*60}\n")
        self._broadcast()

    # -- Visual proxy for obstacle drag (follow extinguisher pattern) ---------

    def _create_obstacle_visual_proxy(self) -> str:
        """Create a moveable visual proxy of the obstacle (no physics)."""
        try:
            from pxr import UsdGeom, UsdPhysics, Gf, Sdf, UsdShade

            proxy_path = "/World/ObstacleProxy"
            obs = self.state.obstacle_position
            x, y, z = float(obs[0]), float(obs[1]), float(obs[2])

            proxy_prim = self.stage.DefinePrim(proxy_path, "Xform")

            body_path = f"{proxy_path}/Body"
            cube = UsdGeom.Cube.Define(self.stage, body_path)
            cube.AddTranslateOp().Set(Gf.Vec3d(0, 0, OBSTACLE_HEIGHT / 2))
            cube.AddScaleOp().Set(Gf.Vec3f(OBSTACLE_LENGTH / 2, OBSTACLE_WIDTH / 2, OBSTACLE_HEIGHT / 2))

            mtl_path = f"{proxy_path}/Mat"
            mtl = UsdShade.Material.Define(self.stage, mtl_path)
            sh = UsdShade.Shader.Define(self.stage, f"{mtl_path}/Shader")
            sh.CreateIdAttr("UsdPreviewSurface")
            sh.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(0.85, 0.40, 0.08))
            sh.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(0.7)
            sh.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(0.3)
            mtl.CreateSurfaceOutput().ConnectToSource(sh.ConnectableAPI(), "surface")
            UsdShade.MaterialBindingAPI(cube.GetPrim()).Bind(mtl)

            for api_cls in [UsdPhysics.RigidBodyAPI, UsdPhysics.CollisionAPI, UsdPhysics.MassAPI]:
                try:
                    if proxy_prim.HasAPI(api_cls):
                        proxy_prim.RemoveAPI(api_cls)
                except Exception:
                    pass

            xformable = UsdGeom.Xformable(proxy_prim)
            xformable.ClearXformOpOrder()
            t_op = xformable.AddTranslateOp(UsdGeom.XformOp.PrecisionDouble)
            t_op.Set(Gf.Vec3d(x, y, z))

            # Hide the original obstacle's beam
            original = self.stage.GetPrimAtPath(f"{OBSTACLE_PRIM_PATH}/beam")
            if original.IsValid():
                UsdGeom.Imageable(original).MakeInvisible()

            self._runtime_app_update(count=3)

            return proxy_path
        except Exception as e:
            _log(f"❌ 视觉代理创建失败: {e}")
            return ""

    def _set_prim_translate(self, prim_path: str, pos: tuple):
        try:
            from pxr import UsdGeom, Gf
            prim = self.stage.GetPrimAtPath(prim_path)
            if not prim.IsValid():
                return
            xformable = UsdGeom.Xformable(prim)
            for op in xformable.GetOrderedXformOps():
                if op.GetOpType() == UsdGeom.XformOp.TypeTranslate:
                    op.Set(Gf.Vec3d(float(pos[0]), float(pos[1]), float(pos[2])))
                    return
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════════════════
# 障碍物创建函数
# ═══════════════════════════════════════════════════════════════════════════════

def create_obstacle_barrier(
    stage, prim_path: str, position: tuple,
    length: float = 2.2, width: float = 0.3, height: float = 0.6,
    update_fn=None,
):
    """在指定位置创建一个长条障碍物（钢梁/横杆）。"""
    from pxr import UsdGeom, UsdPhysics, Gf, Sdf, UsdShade

    x, y, z = float(position[0]), float(position[1]), float(position[2])
    UsdGeom.Xform.Define(stage, prim_path)

    beam_path = f"{prim_path}/beam"
    cube = UsdGeom.Cube.Define(stage, beam_path)
    cube.AddTranslateOp().Set(Gf.Vec3d(x, y, z + height / 2))
    cube.AddScaleOp().Set(Gf.Vec3f(length / 2, width / 2, height / 2))

    prim = cube.GetPrim()
    UsdPhysics.CollisionAPI.Apply(prim)
    UsdPhysics.RigidBodyAPI.Apply(prim)
    rb_api = UsdPhysics.RigidBodyAPI(prim)
    rb_api.CreateKinematicEnabledAttr(True)

    mtl_path = f"{prim_path}/BeamMaterial"
    mtl = UsdShade.Material.Define(stage, mtl_path)
    shader = UsdShade.Shader.Define(stage, f"{mtl_path}/Shader")
    shader.CreateIdAttr("UsdPreviewSurface")
    shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(0.85, 0.40, 0.08))
    shader.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(0.7)
    shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(0.3)
    mtl.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), "surface")
    UsdShade.MaterialBindingAPI(prim).Bind(mtl)

    for i, x_off in enumerate([-length / 4, 0, length / 4]):
        stripe_path = f"{prim_path}/stripe_{i}"
        stripe = UsdGeom.Cube.Define(stage, stripe_path)
        stripe.AddTranslateOp().Set(Gf.Vec3d(x + x_off, y, z + height + 0.015))
        stripe.AddScaleOp().Set(Gf.Vec3f(length / 8, width / 2 + 0.01, 0.015))

        s_mtl_path = f"{prim_path}/StripeMtl_{i}"
        s_mtl = UsdShade.Material.Define(stage, s_mtl_path)
        s_sh = UsdShade.Shader.Define(stage, f"{s_mtl_path}/Shader")
        s_sh.CreateIdAttr("UsdPreviewSurface")
        s_sh.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(1.0, 0.85, 0.0))
        s_sh.CreateInput("emissiveColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(0.3, 0.25, 0.0))
        s_mtl.CreateSurfaceOutput().ConnectToSource(s_sh.ConnectableAPI(), "surface")
        UsdShade.MaterialBindingAPI(stripe.GetPrim()).Bind(s_mtl)

    if update_fn:
        for _ in range(5):
            update_fn()

    _log(f"障碍物已创建: {prim_path}  ({x:.2f}, {y:.2f}, {z:.2f})  "
         f"{length:.1f}m x {width:.1f}m x {height:.1f}m")
