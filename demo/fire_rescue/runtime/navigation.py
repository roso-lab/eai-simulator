"""工厂 EMOS 仿真 — 机器人导航控制器

集中管理所有机器人的运动控制：
  - M20 四足机器人：RL goal_position 控制
  - Carter 无人车：速度（vx, vy, wz）控制
  - Key5 预设目标点序列
  - Navigator（RRT 全局规划）路径跟踪
  - 卡住检测与自动重规划
"""

import math
import os
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from queue import Queue as _TQueue, Empty as _TEmpty
from typing import Any, Callable, Dict, List, Optional, Tuple

import torch

from .settings import (
    ARRIVAL_DIST, SCOUT1_ARRIVAL_DIST,
    SCOUT1_MAX_SPEED, CARTER_MAX_SPEED, CARTER_TURN_SPEED, SCOUT1_TURN_SPEED,
    CARTER_ALIGN_THRESH, SCOUT1_ALIGN_THRESH, CARTER_MAX_OMEGA, SCOUT1_MAX_OMEGA,
    M20_MAX_LIN_SPEED, M20_MAX_ANG_SPEED, M20_KP_LIN, M20_KP_ANG,
    SMOOTH_ALPHA_M20, SMOOTH_ALPHA_CARTER, SMOOTH_ALPHA_SCOUT1,
    CARTER_DELAY_SECONDS,
    KEY5_TARGET_POSITIONS, DEFAULT_ROBOT_WAYPOINTS,
    STUCK_THRESHOLD_DIST, STUCK_TIMEOUT_S, STUCK_REPLAN_CD_S,
    NAV_LOOKAHEAD, NAV_ARRIVE_RADIUS, NAV_FINAL_RADIUS, M20_EXTINGUISHER_PICKUP_FINAL_RADIUS,
    NAV_WAYPOINT_STEP,
    PATROL_FALLBACK_OFFSET,
    CARTER_STEP_ZONES,
    HAZARD_POSITIONS, FIRE_FIXED_PROXIMITY_TARGETS, FIRE_FIXED_PROXIMITY_TARGETS_BY_ROBOT,
)
from .sim_helpers import get_yaw_from_quat, normalize_angle, get_robot_pos, get_robot_pose_tensors

# 规划失败时的“脱困锚点”参数（仅内部路径点，不进入任务序列/任务栏）
ESCAPE_SEARCH_RADIUS_M = 6.0
ESCAPE_MIN_PROGRESS_M = 0.6
ESCAPE_MIN_CLEARANCE_CELLS = 2
ESCAPE_MAX_CANDIDATES = 24
# 性能保护：避免在卡死检测主循环里进行超大规模锚点搜索导致仿真主线程卡顿。
ESCAPE_SEARCH_STRIDE_CELLS = 2
ESCAPE_MAX_EVAL_CELLS = 1400
ESCAPE_PHASE_MAX_TIME_S = 0.35
FIRE_PROXIMITY_ACCEPT_RADIUS_M = 3.0
TASK_NEARBY_FALLBACK_RADIUS_M = 3.0
# 火源 3m 邻域：需尝试多候选 + 吸附到可通行格；原 0.45s 仅够 1 次慢规划，易误判为无解。
FALLBACK_SEARCH_TIME_BUDGET_S = 18.0
FALLBACK_SEARCH_MAX_CANDIDATES = 56
# 关闭多机器人 conflict-aware 规划，统一改为每机器人独立规划以降低负担。
USE_CONFLICT_AWARE_PLANNING = False


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


# 默认对规划路径做占据栅格逐段校验，避免 RRT/平滑后路点共线但穿墙；性能调试可设 EAI_DISABLE_PATH_VALIDATION=1。
DISABLE_PATH_VALIDATION = _env_truthy("EAI_DISABLE_PATH_VALIDATION")


# EAI_FIRE_PATH_DEBUG=1：对所有机器人打印火源邻域规划调试；否则仅对 m20_2 自动打印（便于对照实验）。
FIRE_PATH_DEBUG_ALL = _env_truthy("EAI_FIRE_PATH_DEBUG")


# ── 任务数据结构 ────────────────────────────────────────────────────────────────

@dataclass
class RobotTask:
    """单个机器人任务描述。"""
    task_id: str                                    # 唯一标识，如 "emos_yellow" / "rescue" / "patrol"
    task_type: str                                  # "emos" | "rescue" | "patrol" | "retrieve_ext"
    subtask_name: str                               # 显示名称，如 "灭火器运送"
    subtask_colour: str                             # EMOS 颜色标记，如 "yellow"
    target_xy: Tuple[float, float]                  # 导航目标坐标
    waypoints: list = field(default_factory=list)   # 已规划路径点列表
    priority: int = 0                               # 0=普通, 1=高优先级（救援）
    arrival_target_yaw: Optional[float] = None      # 到达后对齐朝向


class TaskQueue:
    """单机器人有序任务队列，基于 deque 实现 O(1) 头尾操作。"""

    def __init__(self):
        self._queue: deque = deque()

    def push_back(self, task: RobotTask) -> None:
        """正常追加到队尾（EMOS 分配、巡逻恢复）。"""
        self._queue.append(task)

    def push_front(self, task: RobotTask) -> None:
        """插入队头（救援任务优先执行）。"""
        self._queue.appendleft(task)

    def peek(self) -> Optional[RobotTask]:
        """查看当前任务，不弹出。"""
        return self._queue[0] if self._queue else None

    def pop(self) -> Optional[RobotTask]:
        """完成当前任务，弹出并返回。"""
        return self._queue.popleft() if self._queue else None

    def is_empty(self) -> bool:
        return len(self._queue) == 0

    def clear(self) -> None:
        """清空队列中所有任务。"""
        self._queue.clear()

    def all_tasks(self) -> list:
        """返回全部任务列表（供 Dashboard 显示）。"""
        return list(self._queue)

    def __len__(self) -> int:
        return len(self._queue)


# ═══════════════════════════════════════════════════════════════════════════════
#  子进程路径规划 —— 独立 GIL，不阻塞仿真主循环
# ═══════════════════════════════════════════════════════════════════════════════

class RobotNavController:
    """管理全部机器人的导航与速度控制。"""

    def __init__(self, base_env, possible_agents, device, num_envs,
                 navigator, env_cfg, nav_session=None):
        self.base_env = base_env
        self.possible_agents = possible_agents
        self.device = device
        self.num_envs = num_envs
        self.navigator = navigator
        self._nav_session = nav_session

        # 运动指令缓冲
        self.robot_commands = {
            a: torch.zeros((num_envs, 3), device=device) for a in possible_agents
        }
        self.last_cmd_vel = {
            a: torch.zeros(3, device=device) for a in possible_agents
        }

        # 路径点与索引
        self.robot_waypoints: Dict[str, list] = dict(DEFAULT_ROBOT_WAYPOINTS)
        self.robot_waypoint_indices: Dict[str, int] = {a: 0 for a in possible_agents}
        self.arrived_flags: Dict[str, bool] = {a: False for a in possible_agents}
        # 到达任务目标后保持等待，直到收到新任务
        self.waiting_for_task: Dict[str, bool] = {a: True for a in possible_agents}
        # 到达后车体朝向对齐（先旋转对齐，再静止等待）
        self.arrival_target_yaw: Dict[str, Optional[float]] = {a: None for a in possible_agents}
        self.align_on_arrival_pending: Dict[str, bool] = {a: False for a in possible_agents}

        # Goal 控制机器人（M20 用 RL policy + goal_position 指令）
        self.goal_controlled_robots = set()
        for agent in possible_agents:
            if agent.startswith("m20"):
                continue
            if hasattr(env_cfg, "controllers") and agent in env_cfg.controllers:
                cc = env_cfg.controllers[agent]
                if getattr(cc, "command_name", None) == "goal_position":
                    self.goal_controlled_robots.add(agent)
        self.robot_goal_positions = {
            a: torch.zeros((num_envs, 3), device=device)
            for a in self.goal_controlled_robots
        }

        # Key5
        self.key5_active = False
        self.key5_segment_indices: Dict[str, int] = {}

        # 全局停止
        self.all_stopped = False

        # Carter 延迟启动
        self._carter_start_time = None
        self._carter_delay_printed: Dict[str, bool] = {}  # 每个 carter 独立的延迟打印标志

        # 卡住检测
        self._stuck_last_pos: Dict[str, Tuple[float, float]] = {}
        self._stuck_last_time: Dict[str, float] = {}
        self._stuck_replan_cd: Dict[str, float] = {}
        self._stuck_consec_count: Dict[str, int] = {}
        self._stuck_consec_pos: Dict[str, Tuple[float, float]] = {}
        # 振荡检测：追踪目标距离进度，即使机器人在移动但未朝目标前进也判定卡死
        self._stuck_goal_dist: Dict[str, float] = {}
        self._stuck_goal_dist_time: Dict[str, float] = {}
        # 救援任务路径失败后的限频重试
        self._rescue_retry_last_ts: Dict[str, float] = {}
        self._rescue_retry_count: Dict[str, int] = {}

        # 事件通知（供主循环读取后清空）
        self.events: List[str] = []

        # ── TaskQueue 多任务队列 ────────────────────────────────────────────
        self.task_queues: Dict[str, TaskQueue] = {
            a: TaskQueue() for a in possible_agents
        }
        # 巡逻路径（从 DEFAULT_ROBOT_WAYPOINTS 加载）
        self.patrol_waypoints: Dict[str, list] = dict(DEFAULT_ROBOT_WAYPOINTS)
        # 巡逻状态标记
        self.is_patrolling: Dict[str, bool] = {a: False for a in possible_agents}
        # 巡逻循环索引（到达终点后回绕）
        self.patrol_loop_index: Dict[str, int] = {a: 0 for a in possible_agents}
        # 灭火器抓取等操作需要机器人原地等待，不弹出任务
        self.hold_position: set = set()
        # EMOS 灭火任务派遣后：队列为空时不再恢复巡逻，原地停留
        self.suppress_patrol_after_emos: bool = False
        # 指定机器人在任务队列清空后不再恢复巡逻（用于纯人工接管后待命）
        self.suppress_patrol_agents: set = set()
        # 人工接管中的机器人：系统任务不覆盖，先暂存等待人工任务完成后恢复。
        self.manual_override_agents: set = set()
        # 按机器人保存人工接管期间被打断/新到达的系统任务（FIFO）。
        self.deferred_system_tasks: Dict[str, deque] = {
            a: deque() for a in possible_agents
        }
        # 仍允许恢复巡逻的机器人（例如障碍清除后需继续 EMOS/巡逻的 carter_1）
        self.suppress_patrol_exempt: set = set()
        # 走廊障碍物生成后立即暂停 Carter1 导航（在 compute_actions 顶部处理）
        self.carter1_obstacle_pause: bool = False

        # ── 后台异步规划 ──────────────────────────────────────────────────
        self._pending_plan: Optional[dict] = None
        self._plan_thread: Optional[threading.Thread] = None
        self._next_plan_batch_id: int = 1
        self._last_batch_plan_report: Optional[Dict[str, Any]] = None
        self._last_plan_failed_agents: set = set()
        self._last_known_fire_xy: Optional[Tuple[float, float]] = None
        self._recent_fire_paths: Dict[str, Dict[str, Any]] = {}
        # 火源集结连续补发计数：防止“派发可达半径==到达判定半径”在边界上互相打架导致活锁。
        self._fire_rally_redispatch_count: Dict[str, int] = {}
        # 异步重规划请求队列：check_stuck 仅投递请求，不在主循环同步重规划。
        self._async_plan_backlog: Dict[str, RobotTask] = {}
        self._async_plan_last_req_ts: Dict[str, float] = {}

        self._plan_result_queue: _TQueue = _TQueue(maxsize=4)

    def _set_hold_position(self, agent: str, reason: str) -> None:
        was_held = agent in self.hold_position
        self.hold_position.add(agent)
        if not was_held:
            print(f"[Hold] {agent} -> ON ({reason})")

    def _clear_hold_position(self, agent: str, reason: str) -> None:
        if agent in self.hold_position:
            self.hold_position.discard(agent)
            print(f"[Hold] {agent} -> OFF ({reason})")

    def set_known_fire_xy(self, xy: Optional[Tuple[float, float]]) -> None:
        """供工厂主循环在火源出现/重置时主动通知 nav 真实火源坐标。

        通过 _infer_fire_target_xy_from_tasks 推断火源坐标依赖 red 任务进入批量规划，
        但 red 机器人若同时承担障碍救援则会被批量规划跳过（rescue_robot_now），
        此时 _last_known_fire_xy 永远为 None，blue/red 失败任务也无法走 fire_proximity_fallback。
        提供显式 setter 后，工厂在 _mark_fire_if_needed 里直接同步坐标，避免该死锁。
        """
        if xy is not None and len(xy) >= 2:
            self._last_known_fire_xy = (float(xy[0]), float(xy[1]))
        else:
            self._last_known_fire_xy = None

    def _set_batch_plan_report(self, report: Dict[str, Any]) -> None:
        self._last_batch_plan_report = report

    def get_and_clear_batch_plan_report(self) -> Optional[Dict[str, Any]]:
        report = self._last_batch_plan_report
        self._last_batch_plan_report = None
        return report

    @staticmethod
    def _same_target_xy(a: Optional[Tuple[float, float]], b: Optional[Tuple[float, float]]) -> bool:
        if a is None or b is None:
            return False
        try:
            return (abs(float(a[0]) - float(b[0])) < 1e-3) and (abs(float(a[1]) - float(b[1])) < 1e-3)
        except Exception:
            return False

    @staticmethod
    def _is_manual_task(task: Optional["RobotTask"]) -> bool:
        return task is not None and str(getattr(task, "task_type", "")) == "manual"

    def _enqueue_deferred_system_task(self, agent: str, task: Optional["RobotTask"], *, reason: str) -> bool:
        if task is None:
            return False
        if agent not in self.deferred_system_tasks:
            return False
        if self._is_manual_task(task):
            return False
        dq = self.deferred_system_tasks[agent]
        if dq and self._same_target_xy(getattr(dq[-1], "target_xy", None), getattr(task, "target_xy", None)):
            return False
        dq.append(task)
        print(
            f"[手动接管] 暂存系统任务 {agent} "
            f"[{getattr(task, 'task_type', '?')}:{getattr(task, 'subtask_name', '?')}] ({reason})"
        )
        return True

    def _capture_current_system_tasks_for_manual(self, agent: str) -> None:
        q = self.task_queues.get(agent)
        if q is None or q.is_empty():
            return
        cur_tasks = q.all_tasks()
        if not cur_tasks:
            return
        dq = self.deferred_system_tasks.get(agent)
        if dq is None:
            return
        for task in cur_tasks:
            if self._is_manual_task(task):
                continue
            if dq and self._same_target_xy(getattr(dq[-1], "target_xy", None), getattr(task, "target_xy", None)):
                continue
            dq.append(task)

    def _resume_deferred_system_task(self, agent: str) -> bool:
        dq = self.deferred_system_tasks.get(agent)
        if not dq:
            return False
        while dq:
            task = dq.popleft()
            if task is None:
                continue
            print(
                f"[手动接管] 恢复系统任务 {agent} "
                f"[{getattr(task, 'task_type', '?')}:{getattr(task, 'subtask_name', '?')}]"
            )
            self.manual_override_agents.discard(agent)
            self.suppress_patrol_agents.discard(agent)
            self._clear_hold_position(agent, "resume_deferred_system_task")
            self.interrupt_patrol_with_task(agent, task, priority=False)
            return True
        return False

    def _queue_async_plan_request(
        self,
        agent: str,
        task: Optional["RobotTask"],
        *,
        reason: str,
        min_interval_s: float = 1.0,
    ) -> bool:
        """Queue an async plan request for one robot with dedupe/throttle."""
        if task is None or agent not in self.base_env.scene.articulations:
            return False
        if agent in self.manual_override_agents and not self._is_manual_task(task):
            return self._enqueue_deferred_system_task(agent, task, reason=f"async_replan:{reason}")
        now = time.time()
        last_ts = self._async_plan_last_req_ts.get(agent, 0.0)
        if now - last_ts < max(0.0, float(min_interval_s)):
            return False

        pending_map = (self._pending_plan or {}).get("task_map", {}) if self._pending_plan else {}
        pending_task = pending_map.get(agent)
        if pending_task is not None and self._same_target_xy(
            getattr(pending_task, "target_xy", None), getattr(task, "target_xy", None)
        ):
            return False

        backlog_task = self._async_plan_backlog.get(agent)
        if backlog_task is not None and self._same_target_xy(
            getattr(backlog_task, "target_xy", None), getattr(task, "target_xy", None)
        ):
            return False

        self._async_plan_backlog[agent] = task
        self._async_plan_last_req_ts[agent] = now
        print(
            f"[异步重规划] 已入队 {agent} [{getattr(task, 'task_type', '?')}:{getattr(task, 'subtask_name', '?')}]"
            f" -> ({float(task.target_xy[0]):.2f},{float(task.target_xy[1]):.2f}) ({reason})"
        )
        return True

    def _flush_async_plan_backlog(self) -> bool:
        """Submit queued async requests when no plan is currently pending."""
        if self._pending_plan is not None:
            return False
        if not self._async_plan_backlog:
            return False
        task_map: Dict[str, RobotTask] = {}
        for rn, task in self._async_plan_backlog.items():
            if rn in self.manual_override_agents and not self._is_manual_task(task):
                self._enqueue_deferred_system_task(rn, task, reason="flush_async_backlog")
                continue
            task_map[rn] = task
        self._async_plan_backlog.clear()
        if not task_map:
            return False
        print(f"[异步重规划] 提交积压请求，机器人数={len(task_map)}")
        self.batch_dispatch_tasks(task_map)
        return True

    # ── 初始化 ─────────────────────────────────────────────────────────────────

    def setup_initial_navigation(self):
        """初始化 M20 navigator 为当前位置（inactive），Carter 加载默认路径。"""
        for mn in ("m20_1", "m20_2"):
            if mn in self.base_env.scene.articulations:
                mp = self.base_env.scene.articulations[mn].data.root_pos_w[0].cpu().numpy()
                self.robot_waypoints[mn] = [(float(mp[0]), float(mp[1]), float(mp[2]))]
                self.robot_waypoint_indices[mn] = 0
                self.navigator.set_waypoints(mn, self.robot_waypoints[mn], active=False)
        for cn in ("carter_1", "scout_1"):
            if cn in self.robot_waypoints:
                self.navigator.set_waypoints(cn, self.robot_waypoints[cn], active=False)

    def set_initial_goal_positions(self):
        """将所有 goal 控制机器人的初始目标设为当前位置（不乱跑）。"""
        for agent in self.possible_agents:
            if agent not in self.base_env.scene.articulations:
                continue
            robot = self.base_env.scene.articulations[agent]
            curr = robot.data.root_pos_w[0]
            if agent in self.goal_controlled_robots and hasattr(self.base_env, "set_command"):
                self.base_env.set_command(agent, "goal_position", curr.unsqueeze(0))

    # ── 巡逻机制 ────────────────────────────────────────────────────────────────

    def _start_patrol(self, agent: str) -> None:
        """为机器人分配巡逻路径并开始移动。队列为空时调用，恢复巡逻状态。"""
        if agent in self.suppress_patrol_agents:
            self.waiting_for_task[agent] = True
            self.is_patrolling[agent] = False
            self._set_hold_position(agent, "suppress_patrol_agents")
            return
        if self.suppress_patrol_after_emos and agent not in self.suppress_patrol_exempt:
            # EMOS 后期：scout_1 队列空时不直接待命，优先补发火源集结任务（3m 邻域策略）。
            if (
                agent == "scout_1"
                and self._last_known_fire_xy is not None
                and self.task_queues.get(agent) is not None
                and self.task_queues[agent].is_empty()
            ):
                fire_xy = self._last_known_fire_xy
                rally_task = RobotTask(
                    task_id=f"emos_fire_rally_{agent}_{int(time.time() * 1000)}",
                    task_type="emos",
                    subtask_name="火源集结（scout1补发）",
                    subtask_colour="grey",
                    target_xy=(float(fire_xy[0]), float(fire_xy[1])),
                )
                self._clear_hold_position(agent, "scout1_fire_rally_resume")
                self.task_queues[agent].clear()
                self.task_queues[agent].push_back(rally_task)
                print(
                    f"[任务] {agent} 后期队列为空，自动补发火源集结任务"
                    f" ({fire_xy[0]:.2f}, {fire_xy[1]:.2f})"
                )
                self._start_task(agent, rally_task)
                return
            self.waiting_for_task[agent] = True
            self.is_patrolling[agent] = False
            self._set_hold_position(agent, "suppress_patrol_after_emos")
            return
        wps = self.patrol_waypoints.get(agent)
        if not wps:
            # 无预设路径：以当前位置为起点生成简单往返路径
            try:
                pos = get_robot_pos(self.base_env, agent)
                offset = (pos[0] + PATROL_FALLBACK_OFFSET, pos[1], pos[2])
                wps = [(pos[0], pos[1], pos[2]), offset, (pos[0], pos[1], pos[2])]
                self.patrol_waypoints[agent] = wps
            except Exception:
                return

        idx = self.patrol_loop_index.get(agent, 0) % len(wps)

        # 若当前路径点与机器人实际位置重合（距离 < 0.5m），跳到下一个点，
        # 避免立刻触发到达判定导致机器人停在原地
        try:
            pos = get_robot_pos(self.base_env, agent)
            for _ in range(len(wps)):
                wp = wps[idx]
                if math.hypot(pos[0] - wp[0], pos[1] - wp[1]) > 0.5:
                    break
                idx = (idx + 1) % len(wps)
        except Exception:
            pass

        self.robot_waypoints[agent] = wps
        self.robot_waypoint_indices[agent] = idx
        self.arrived_flags[agent] = False
        self.waiting_for_task[agent] = False
        self.is_patrolling[agent] = True
        self.navigator.set_waypoints(agent, wps, active=False)
        print(f"[巡逻] {agent} 开始巡逻，路径点 {len(wps)} 个，起始索引 {idx}")

    @staticmethod
    def _carter_step_detour(start_xy, goal_xy, agent):
        """Carter 轮式机器人台阶绕行：如果路径穿越台阶禁行区，生成绕行路径点。"""
        if agent.startswith("m20") or not CARTER_STEP_ZONES:
            return None
        sx, sy = start_xy
        gx, gy = goal_xy
        for (xmin, xmax, yc, yhw) in CARTER_STEP_ZONES:
            y_lo, y_hi = yc - yhw, yc + yhw
            crosses_y = (sy > y_hi and gy < y_lo) or (sy < y_lo and gy > y_hi)
            if not crosses_y:
                continue
            in_x_range = not (sx > xmax + 0.5 and gx > xmax + 0.5) and \
                         not (sx < xmin - 0.5 and gx < xmin - 0.5)
            if not in_x_range:
                continue
            dist_left = abs(sx - (xmin - 2.5)) + abs(gx - (xmin - 2.5))
            dist_right = abs(sx - (xmax + 2.5)) + abs(gx - (xmax + 2.5))
            if dist_left <= dist_right:
                bypass_x = xmin - 2.5
            else:
                bypass_x = xmax + 2.5
            heading = math.atan2(gy - sy, gx - sx)
            detour_wps = [
                (bypass_x, sy, math.atan2(0, bypass_x - sx)),
                (bypass_x, gy, math.atan2(gy - sy, 0)),
                (gx, gy, heading),
            ]
            print(f"  🚧 [台阶绕行] {agent} 路径穿越台阶区 x=[{xmin},{xmax}] y={yc}，"
                  f"绕行 x={bypass_x:.1f}")
            return detour_wps
        return None

    def _validate_detour_path(self, detour_wps, agent):
        """Validate every segment of a manually constructed detour path against the occupancy grid.
        Returns True only if all segments are collision-free."""
        if DISABLE_PATH_VALIDATION:
            return True
        planner = getattr(self.navigator, '_planner', None)
        if planner is None:
            return True
        for k in range(len(detour_wps) - 1):
            p0 = (float(detour_wps[k][0]), float(detour_wps[k][1]))
            p1 = (float(detour_wps[k + 1][0]), float(detour_wps[k + 1][1]))
            if not planner._collision_free_segment(p0, p1, clearance_cells=1):
                print(f"  ⚠️ [路径校验] {agent} 台阶绕行第 {k} 段碰撞检测失败 "
                      f"({p0[0]:.2f},{p0[1]:.2f})→({p1[0]:.2f},{p1[1]:.2f})，回退到标准规划")
                return False
        return True

    def _validate_planned_path(self, wps, agent: str, clearance_cells: int = 2) -> bool:
        """Validate generic planned path to avoid wall-crossing segments.

        Some planners may output sparsified/smoothed waypoints; this check enforces that
        every segment remains collision-free on the occupancy map.
        """
        if DISABLE_PATH_VALIDATION:
            return True
        if not wps or len(wps) < 2:
            return bool(wps)
        planner = getattr(self.navigator, "_planner", None)
        if planner is None:
            return True
        try:
            for k in range(len(wps) - 1):
                p0 = (float(wps[k][0]), float(wps[k][1]))
                p1 = (float(wps[k + 1][0]), float(wps[k + 1][1]))
                if not planner._collision_free_segment(p0, p1, clearance_cells=clearance_cells):
                    print(
                        f"  ⚠️ [路径校验] {agent} 规划路径第 {k} 段疑似穿墙 "
                        f"({p0[0]:.2f},{p0[1]:.2f})→({p1[0]:.2f},{p1[1]:.2f})"
                    )
                    return False
            return True
        except Exception as e:
            print(f"  ⚠️ [路径校验] {agent} 发生异常，保守判定为无效: {e}")
            return False

    def _validate_manual_recovery_path(
        self,
        wps,
        agent: str,
        *,
        label: str = "手工恢复路径",
        clearance_cells: int = 0,
    ) -> bool:
        """Always validate manually constructed stuck-recovery paths.

        Unlike generic path validation, this check intentionally ignores the
        global ``DISABLE_PATH_VALIDATION`` switch because these short fallback
        paths are synthesized locally and must never override a known-good
        planner path if they cut through obstacles.
        """
        if not wps or len(wps) < 2:
            return bool(wps)
        planner = getattr(self.navigator, "_planner", None)
        if planner is None:
            print(f"  ⚠️ [路径校验] {agent} {label} 缺少 planner，拒绝采用")
            return False
        try:
            for k in range(len(wps) - 1):
                p0 = (float(wps[k][0]), float(wps[k][1]))
                p1 = (float(wps[k + 1][0]), float(wps[k + 1][1]))
                if not planner._collision_free_segment(p0, p1, clearance_cells=clearance_cells):
                    print(
                        f"  ⚠️ [路径校验] {agent} {label} 第 {k} 段碰撞检测失败 "
                        f"({p0[0]:.2f},{p0[1]:.2f})→({p1[0]:.2f},{p1[1]:.2f})"
                    )
                    return False
            return True
        except Exception as e:
            print(f"  ⚠️ [路径校验] {agent} {label} 校验异常，拒绝采用: {e}")
            return False

    def _estimate_cell_clearance(self, planner, ci: int, cj: int, max_probe: int = 10) -> int:
        """Estimate local free-space radius around one free cell (in grid cells)."""
        if not planner.is_free(ci, cj):
            return 0
        for r in range(1, max_probe + 1):
            i0, i1 = ci - r, ci + r
            j0, j1 = cj - r, cj + r
            for jj in range(j0, j1 + 1):
                if (not planner.in_bounds(i0, jj)) or (not planner.is_free(i0, jj)):
                    return r - 1
                if (not planner.in_bounds(i1, jj)) or (not planner.is_free(i1, jj)):
                    return r - 1
            for ii in range(i0 + 1, i1):
                if (not planner.in_bounds(ii, j0)) or (not planner.is_free(ii, j0)):
                    return r - 1
                if (not planner.in_bounds(ii, j1)) or (not planner.is_free(ii, j1)):
                    return r - 1
        return max_probe

    def _find_escape_anchor(self, start_xy: tuple, goal_xy: tuple):
        """Find a nearby open-area anchor used only when direct planning failed."""
        planner = getattr(self.navigator, "_planner", None)
        if planner is None:
            return None
        try:
            t0 = time.perf_counter()
            si, sj = planner.world_to_grid(float(start_xy[0]), float(start_xy[1]))
            si, sj = planner._nearest_free(si, sj)
            search_cells = max(3, int(ESCAPE_SEARCH_RADIUS_M / max(planner.resolution, 1e-6)))
            stride = max(1, int(ESCAPE_SEARCH_STRIDE_CELLS))
            sx, sy = float(start_xy[0]), float(start_xy[1])
            gx, gy = float(goal_xy[0]), float(goal_xy[1])
            g_norm = math.hypot(gx - sx, gy - sy)
            best = []
            eval_cells = 0
            budget_exhausted = False
            for di in range(-search_cells, search_cells + 1, stride):
                for dj in range(-search_cells, search_cells + 1, stride):
                    eval_cells += 1
                    if eval_cells > ESCAPE_MAX_EVAL_CELLS:
                        budget_exhausted = True
                        break
                    if (time.perf_counter() - t0) > ESCAPE_PHASE_MAX_TIME_S:
                        budget_exhausted = True
                        break
                    if di == 0 and dj == 0:
                        continue
                    ci, cj = si + di, sj + dj
                    if not planner.in_bounds(ci, cj) or (not planner.is_free(ci, cj)):
                        continue
                    wx, wy = planner.grid_to_world(ci, cj)
                    d_start = math.hypot(wx - sx, wy - sy)
                    if d_start < 1.0:
                        continue
                    d_goal_drop = math.hypot(sx - gx, sy - gy) - math.hypot(wx - gx, wy - gy)
                    if d_goal_drop < -ESCAPE_MIN_PROGRESS_M:
                        # 不要求必须前进，但避免明显“倒着绕很远”
                        continue
                    if not planner._collision_free_segment((sx, sy), (wx, wy), clearance_cells=0):
                        continue
                    clearance = self._estimate_cell_clearance(planner, ci, cj, max_probe=10)
                    if clearance < ESCAPE_MIN_CLEARANCE_CELLS:
                        continue
                    # 评分：优先净空更大，其次稍偏向朝向目标
                    dir_bonus = 0.0
                    if g_norm > 1e-6:
                        dir_bonus = ((wx - sx) * (gx - sx) + (wy - sy) * (gy - sy)) / (g_norm * max(d_start, 1e-6))
                    score = float(clearance) * 10.0 + float(dir_bonus) * 2.0 - float(d_start) * 0.2
                    best.append((score, (float(wx), float(wy))))
                if budget_exhausted:
                    break
            if not best:
                return None
            best.sort(key=lambda x: x[0], reverse=True)
            if budget_exhausted:
                print(
                    f"  ℹ️ [EscapeReplan] 锚点搜索触发预算保护："
                    f"eval={eval_cells}, stride={stride}, dt={time.perf_counter() - t0:.3f}s"
                )
            return [xy for _, xy in best[:ESCAPE_MAX_CANDIDATES]]
        except Exception:
            return None

    def _plan_path_without_escape(self, agent: str, start_xy: tuple, goal_xy: tuple, waypoint_step: float):
        """Plan path with existing strategy, but never force unsafe fallback."""
        planner = getattr(self.navigator, "_planner", None)

        def _try_plan(step_val: float, *, force_astar: bool) -> list:
            old_prefer = None
            switched = False
            if force_astar and planner is not None and hasattr(planner, "prefer_astar"):
                old_prefer = bool(planner.prefer_astar)
                if not old_prefer:
                    planner.prefer_astar = True
                    switched = True
            try:
                if USE_CONFLICT_AWARE_PLANNING and self._nav_session is not None:
                    w = self._nav_session.replan_single(
                        agent, start_xy, goal_xy, waypoint_step=step_val
                    )
                    if w:
                        return w
                return self.navigator.set_path(
                    agent, start_xy, goal_xy, waypoint_step=step_val
                ) or []
            finally:
                if switched and old_prefer is not None:
                    planner.prefer_astar = old_prefer

        for st in (max(0.2, float(waypoint_step)),):
            # 1) 当前策略（常见为 RRT 优先）
            wps = _try_plan(st, force_astar=False)
            if wps and self._validate_planned_path(wps, agent, clearance_cells=0):
                return wps

            # 2) 强制 A* 回退
            wps_astar = _try_plan(st, force_astar=True)
            if wps_astar and self._validate_planned_path(wps_astar, agent, clearance_cells=0):
                print(f"  ℹ️ [路径回退] {agent} 使用 A* 回退成功（step={st:.2f}）")
                return wps_astar

            # 3) 保守 A*（不做简化，避免少数情况下平滑后线段穿障）
            if planner is not None:
                try:
                    si, sj = planner.world_to_grid(float(start_xy[0]), float(start_xy[1]))
                    gi, gj = planner.world_to_grid(float(goal_xy[0]), float(goal_xy[1]))
                    si, sj = planner._nearest_free(si, sj)
                    gi, gj = planner._nearest_free(gi, gj)
                    grid_path = planner._a_star((si, sj), (gi, gj))
                    world_path = [planner.grid_to_world(i, j) for (i, j) in grid_path]
                    ds_points = planner._downsample_world_path(world_path, step=st)
                    wps_cons = planner._compute_headings(ds_points)
                    if wps_cons and self._validate_planned_path(wps_cons, agent, clearance_cells=0):
                        print(f"  ℹ️ [路径回退] {agent} 使用保守 A* 路径成功（step={st:.2f}）")
                        return wps_cons
                except Exception:
                    pass
        return None

    @staticmethod
    def _is_fire_proximity_task(task: Optional["RobotTask"]) -> bool:
        if task is None:
            return False
        subtask_name = str(getattr(task, "subtask_name", "") or "")
        subtask_colour = str(getattr(task, "subtask_colour", "") or "")
        # 灰色集结/最终火源区任务，以及名称显式包含“火源”的任务都允许 3m 宽松目标。
        return subtask_colour == "grey" or ("火源" in subtask_name)

    @staticmethod
    def _is_nearby_fallback_task(task: Optional["RobotTask"]) -> bool:
        if task is None:
            return False
        # 仅对非精密任务放宽目标点：blue/red 常是“到附近采集/侦查”。
        # green(按按钮) / yellow(取灭火器) 仍保持精确目标，避免动作触发失败。
        subtask_colour = str(getattr(task, "subtask_colour", "") or "")
        if subtask_colour in ("blue", "red"):
            return True
        return False

    def _fire_batch_path_usable(
        self,
        agent: str,
        wps: Optional[list],
        nominal_goal_xy: Tuple[float, float],
    ) -> Optional[Tuple[float, float]]:
        """若后台 batch 已给出抵达火源邻域的路径且通过校验，返回路径末端可达点。"""
        if not wps or len(wps) < 2:
            return None
        if not self._validate_planned_path(wps, agent, clearance_cells=0):
            return None
        lx, ly = float(wps[-1][0]), float(wps[-1][1])
        if not self._within_fire_radius_m(lx, ly, nominal_goal_xy, FIRE_PROXIMITY_ACCEPT_RADIUS_M):
            return None
        return (lx, ly)

    def _fire_path_debug_verbose(self, agent: str) -> bool:
        """火源邻域规划详细日志：环境变量全开，或对 m20_2 默认开启。"""
        return bool(FIRE_PATH_DEBUG_ALL or agent == "m20_2")

    def _snap_world_xy_to_nearest_free(
        self, wx: float, wy: float
    ) -> Optional[Tuple[float, float]]:
        """将世界坐标吸附到占据栅格最近可通行格中心（火源柱心常非 free，环上点需先吸附再规划）。"""
        planner = getattr(self.navigator, "_planner", None)
        if planner is None:
            return (float(wx), float(wy))
        try:
            ci, cj = planner.world_to_grid(float(wx), float(wy))
            fi, fj = planner._nearest_free(ci, cj)
            twx, twy = planner.grid_to_world(fi, fj)
            return (float(twx), float(twy))
        except Exception:
            return None

    def _within_fire_radius_m(
        self,
        wx: float,
        wy: float,
        nominal_goal_xy: Tuple[float, float],
        radius_m: float,
    ) -> bool:
        """判定吸附后的目标是否仍在火源邻域：优先相对真实火源坐标，否则相对任务名义目标。"""
        if self._last_known_fire_xy is not None:
            fx, fy = float(self._last_known_fire_xy[0]), float(self._last_known_fire_xy[1])
            d = math.hypot(wx - fx, wy - fy)
        else:
            gx, gy = float(nominal_goal_xy[0]), float(nominal_goal_xy[1])
            d = math.hypot(wx - gx, wy - gy)
        # Allow a small planning margin: the navigator may snap to the nearest
        # free grid cell, while the mission-success check still uses strict 3m
        # robot position. If the robot truly stops outside 3m, the queue-empty
        # guard will issue another fire-rally task.
        return d <= float(radius_m) + 0.25

    def _fixed_fire_proximity_target(
        self,
        nominal_goal_xy: Tuple[float, float],
        agent: Optional[str] = None,
    ) -> Optional[Tuple[float, float]]:
        """Return a hard-coded safe fire-nearby target when configured.

        优先级：FIRE_FIXED_PROXIMITY_TARGETS_BY_ROBOT[hid][agent]
              → FIRE_FIXED_PROXIMITY_TARGETS[hid]（共享单点，向后兼容）。
        """
        try:
            gx, gy = float(nominal_goal_xy[0]), float(nominal_goal_xy[1])
        except Exception:
            return None

        def _hazard_matches(hid_int: int) -> bool:
            fire_pos = HAZARD_POSITIONS.get(int(hid_int))
            if not fire_pos or len(fire_pos) < 2:
                return False
            fx, fy = float(fire_pos[0]), float(fire_pos[1])
            near_known_fire = (
                self._last_known_fire_xy is not None
                and math.hypot(float(self._last_known_fire_xy[0]) - fx, float(self._last_known_fire_xy[1]) - fy) <= 0.75
            )
            near_nominal_goal = math.hypot(gx - fx, gy - fy) <= 3.25
            return bool(near_known_fire or near_nominal_goal)

        if agent:
            for hid, by_robot in FIRE_FIXED_PROXIMITY_TARGETS_BY_ROBOT.items():
                if not _hazard_matches(hid):
                    continue
                xy = by_robot.get(str(agent))
                if xy and len(xy) >= 2:
                    return (float(xy[0]), float(xy[1]))

        for hid, fixed_xy in FIRE_FIXED_PROXIMITY_TARGETS.items():
            if _hazard_matches(hid):
                return (float(fixed_xy[0]), float(fixed_xy[1]))
        return None

    @staticmethod
    def _nominal_fire_goal_matches(path_goal_xy: Tuple[float, float], goal_xy: Tuple[float, float]) -> bool:
        try:
            return math.hypot(
                float(path_goal_xy[0]) - float(goal_xy[0]),
                float(path_goal_xy[1]) - float(goal_xy[1]),
            ) <= 0.75
        except Exception:
            return False

    def _remember_recent_fire_path(
        self,
        agent: str,
        nominal_goal_xy: Tuple[float, float],
        actual_goal_xy: Tuple[float, float],
        wps: list,
    ) -> None:
        if not wps:
            return
        self._recent_fire_paths[agent] = {
            "nominal_goal_xy": (float(nominal_goal_xy[0]), float(nominal_goal_xy[1])),
            "actual_goal_xy": (float(actual_goal_xy[0]), float(actual_goal_xy[1])),
            "waypoints": list(wps),
            "ts": time.time(),
        }

    def _reuse_recent_fire_path(
        self,
        agent: str,
        start_xy: Tuple[float, float],
        goal_xy: Tuple[float, float],
        *,
        snap_dist_m: float = 1.25,
    ) -> Tuple[Optional[list], Optional[Tuple[float, float]]]:
        """Reuse a previously planned fire path suffix near the robot's current pose.

        This avoids repeatedly running expensive fire-neighborhood planning on the
        main loop when the robot is merely oscillating around the same global route.
        """
        candidates: List[Dict[str, Any]] = []
        current_wps = self.robot_waypoints.get(agent) or []
        if current_wps:
            candidates.append({
                "nominal_goal_xy": tuple(goal_xy),
                "actual_goal_xy": (float(current_wps[-1][0]), float(current_wps[-1][1])),
                "waypoints": list(current_wps),
            })
        cached = self._recent_fire_paths.get(agent)
        if cached:
            candidates.append(cached)

        planner = getattr(self.navigator, "_planner", None)
        for cand in candidates:
            nominal_xy = cand.get("nominal_goal_xy")
            actual_xy = cand.get("actual_goal_xy")
            waypoints = cand.get("waypoints") or []
            if not waypoints or len(waypoints) < 2:
                continue
            if nominal_xy is not None and (not self._nominal_fire_goal_matches(tuple(nominal_xy), goal_xy)):
                continue
            if actual_xy is None:
                actual_xy = (float(waypoints[-1][0]), float(waypoints[-1][1]))
            try:
                if math.hypot(float(actual_xy[0]) - float(goal_xy[0]), float(actual_xy[1]) - float(goal_xy[1])) > (
                    FIRE_PROXIMITY_ACCEPT_RADIUS_M + 0.5
                ):
                    continue
            except Exception:
                continue

            best_idx = None
            best_dist = float("inf")
            for idx, wp in enumerate(waypoints):
                if idx >= len(waypoints) - 1:
                    break
                d = math.hypot(float(wp[0]) - float(start_xy[0]), float(wp[1]) - float(start_xy[1]))
                if d > snap_dist_m:
                    continue
                if planner is not None and not planner._collision_free_segment(
                    (float(start_xy[0]), float(start_xy[1])),
                    (float(wp[0]), float(wp[1])),
                    clearance_cells=0,
                ):
                    continue
                if d < best_dist:
                    best_dist = d
                    best_idx = idx

            if best_idx is None:
                continue

            suffix = list(waypoints[best_idx:])
            if not suffix:
                continue
            if math.hypot(float(suffix[0][0]) - float(start_xy[0]), float(suffix[0][1]) - float(start_xy[1])) < 0.05:
                stitched = suffix
            else:
                heading = math.atan2(float(suffix[0][1]) - float(start_xy[1]), float(suffix[0][0]) - float(start_xy[0]))
                stitched = [(float(start_xy[0]), float(start_xy[1]), heading)] + suffix

            if not self._validate_manual_recovery_path(
                stitched,
                agent,
                label="火源路径后缀复用",
                clearance_cells=0,
            ):
                continue
            return stitched, (float(actual_xy[0]), float(actual_xy[1]))
        return None, None

    def _debug_fire_grid_snapshot(self, agent: str, start_xy: tuple, goal_xy: tuple) -> None:
        """打印占据栅格上起点/终点与粗 A* 是否连通，用于区分「采样不够」与「图上不可达」。"""
        planner = getattr(self.navigator, "_planner", None)
        if planner is None:
            print(f"[火源规划调试] {agent} navigator._planner 不可用，无法做栅格快照")
            return
        try:
            sx, sy = float(start_xy[0]), float(start_xy[1])
            gx, gy = float(goal_xy[0]), float(goal_xy[1])
            si, sj = planner.world_to_grid(sx, sy)
            gi, gj = planner.world_to_grid(gx, gy)
            start_free = bool(planner.is_free(si, sj))
            goal_free = bool(planner.is_free(gi, gj))
            sfi, sfj = planner._nearest_free(si, sj)
            gfi, gfj = planner._nearest_free(gi, gj)
            swx, swy = planner.grid_to_world(sfi, sfj)
            gwx, gwy = planner.grid_to_world(gfi, gfj)
            d_start_snap = math.hypot(sx - float(swx), sy - float(swy))
            d_goal_snap = math.hypot(gx - float(gwx), gy - float(gwy))
            res = getattr(planner, "resolution", None)
            astar_nodes = 0
            astar_err: Optional[str] = None
            try:
                gp = planner._a_star((sfi, sfj), (gfi, gfj))
                astar_nodes = len(gp) if gp else 0
            except Exception as ex:
                astar_err = str(ex)
                astar_nodes = -1
            print(
                f"[火源规划调试] {agent} 起点 world=({sx:.2f},{sy:.2f}) grid=({si},{sj}) "
                f"is_free={start_free} nearest_free=({sfi},{sfj}) world_snap=({float(swx):.2f},{float(swy):.2f}) "
                f"d_snap={d_start_snap:.2f}m"
            )
            print(
                f"[火源规划调试] {agent} 火源目标 world=({gx:.2f},{gy:.2f}) grid=({gi},{gj}) "
                f"is_free={goal_free} nearest_free=({gfi},{gfj}) world_snap=({float(gwx):.2f},{float(gwy):.2f}) "
                f"d_snap={d_goal_snap:.2f}m  resolution={res}"
            )
            if astar_err:
                print(f"[火源规划调试] {agent} 粗A*(snap→snap) 异常: {astar_err}")
            else:
                print(
                    f"[火源规划调试] {agent} 粗A*(snap→snap) 节点数={astar_nodes} "
                    f"({'栅格上无通路' if astar_nodes == 0 else '栅格存在通路，后续失败多为 set_path/RRT 或候选未覆盖'})"
                )
        except Exception as e:
            print(f"[火源规划调试] {agent} 栅格快照异常: {e}")

    def _plan_fire_proximity_fallback(
        self,
        agent: str,
        start_xy: tuple,
        goal_xy: tuple,
        waypoint_step: float,
        radius_m: float = FIRE_PROXIMITY_ACCEPT_RADIUS_M,
    ) -> Tuple[Optional[list], Optional[Tuple[float, float]]]:
        """Try planning to reachable alternative targets within fire proximity radius."""
        # 由近到远多环、多角度；火源中心格常为障碍，候选先吸附到最近 free 再规划。
        ring_radii = [0.5, 0.9, 1.3, 1.7, 2.1, 2.5, min(2.95, float(radius_m) - 0.05)]
        ring_steps = [16, 16, 16, 16, 16, 16, 20]
        gx, gy = float(goal_xy[0]), float(goal_xy[1])
        best_wps: Optional[list] = None
        best_xy: Optional[Tuple[float, float]] = None
        best_goal_dist = float("inf")
        t0 = time.perf_counter()
        tried = 0
        n_empty = 0
        n_validate_fail = 0
        dbg = self._fire_path_debug_verbose(agent)
        fail_samples: List[Tuple[float, float, float, int]] = []
        budget_hit = False

        fixed_xy = self._fixed_fire_proximity_target(goal_xy, agent=agent)
        if fixed_xy is not None:
            wps_fixed = self._plan_path_without_escape(agent, start_xy, fixed_xy, waypoint_step)
            if wps_fixed and self._validate_planned_path(wps_fixed, agent, clearance_cells=0):
                print(
                    f"[火源邻域规划] FIXED {agent} picked=({fixed_xy[0]:.2f},{fixed_xy[1]:.2f}) "
                    f"(hardcoded-safe-point)"
                )
                return wps_fixed, fixed_xy

        if dbg:
            print(
                f"[火源邻域规划] begin {agent} start=({start_xy[0]:.2f},{start_xy[1]:.2f}) "
                f"fire_goal=({gx:.2f},{gy:.2f}) radius_m={radius_m:.2f} "
                f"max_cand={FALLBACK_SEARCH_MAX_CANDIDATES} t_budget_s={FALLBACK_SEARCH_TIME_BUDGET_S:.2f} "
                f"step={waypoint_step:.2f}"
            )
            self._debug_fire_grid_snapshot(agent, start_xy, (gx, gy))

        for ridx, r in enumerate(ring_radii):
            if r > radius_m + 1e-6:
                continue
            steps = ring_steps[min(ridx, len(ring_steps) - 1)]
            for k in range(steps):
                if tried >= FALLBACK_SEARCH_MAX_CANDIDATES:
                    budget_hit = True
                    break
                if (time.perf_counter() - t0) > FALLBACK_SEARCH_TIME_BUDGET_S:
                    budget_hit = True
                    break
                tried += 1
                ang = (2.0 * math.pi * float(k)) / float(steps)
                tx = gx + r * math.cos(ang)
                ty = gy + r * math.sin(ang)
                snapped = self._snap_world_xy_to_nearest_free(tx, ty)
                if snapped is None:
                    n_empty += 1
                    if dbg and len(fail_samples) < 6:
                        fail_samples.append((float(tx), float(ty), float(r), int(k)))
                    continue
                sx, sy = snapped[0], snapped[1]
                if not self._within_fire_radius_m(sx, sy, (gx, gy), radius_m):
                    continue
                wps = self._plan_path_without_escape(agent, start_xy, (sx, sy), waypoint_step)
                if not wps:
                    n_empty += 1
                    if dbg and len(fail_samples) < 6:
                        fail_samples.append((float(sx), float(sy), float(r), int(k)))
                    continue
                if not self._validate_planned_path(wps, agent, clearance_cells=0):
                    n_validate_fail += 1
                    continue
                goal_dist = math.hypot(sx - gx, sy - gy)
                if goal_dist < best_goal_dist:
                    best_goal_dist = goal_dist
                    best_wps = wps
                    best_xy = (float(sx), float(sy))
            if best_wps is not None:
                break
            if tried >= FALLBACK_SEARCH_MAX_CANDIDATES:
                budget_hit = True
                break
            if (time.perf_counter() - t0) > FALLBACK_SEARCH_TIME_BUDGET_S:
                budget_hit = True
                break

        dt_ms = (time.perf_counter() - t0) * 1000.0
        if best_wps is None:
            print(
                f"[火源邻域规划] FAIL {agent} tried={tried} dt={dt_ms:.0f}ms empty_path={n_empty} "
                f"validate_fail={n_validate_fail} budget_hit={budget_hit} "
                f"(规划器对全部候选返回空路径或校验失败)"
            )
            if dbg and fail_samples:
                fs = " | ".join(
                    f"({a:.2f},{b:.2f}) r={c:.1f} k={d}" for (a, b, c, d) in fail_samples
                )
                print(f"[火源邻域规划] {agent} 前若干失败候选 world: {fs}")
        elif dbg:
            print(
                f"[火源邻域规划] OK {agent} picked=({best_xy[0]:.2f},{best_xy[1]:.2f}) "
                f"tried={tried} dt={dt_ms:.0f}ms n_wp={len(best_wps)}"
            )

        return best_wps, best_xy

    def _plan_nearby_target_fallback(
        self,
        agent: str,
        start_xy: tuple,
        goal_xy: tuple,
        waypoint_step: float,
        radius_m: float = TASK_NEARBY_FALLBACK_RADIUS_M,
    ) -> Tuple[Optional[list], Optional[Tuple[float, float]]]:
        """Try nearby reachable alternatives around non-precise task goals."""
        ring_radii = [0.45, 0.9, 1.4, 2.1, max(2.8, float(radius_m) - 0.05)]
        ring_steps = [8, 10, 12, 14, 16]
        gx, gy = float(goal_xy[0]), float(goal_xy[1])
        best_wps: Optional[list] = None
        best_xy: Optional[Tuple[float, float]] = None
        best_goal_dist = float("inf")
        t0 = time.perf_counter()
        tried = 0

        for ridx, r in enumerate(ring_radii):
            if r > radius_m + 1e-6:
                continue
            steps = ring_steps[min(ridx, len(ring_steps) - 1)]
            for k in range(steps):
                if tried >= FALLBACK_SEARCH_MAX_CANDIDATES:
                    break
                if (time.perf_counter() - t0) > FALLBACK_SEARCH_TIME_BUDGET_S:
                    break
                tried += 1
                ang = (2.0 * math.pi * float(k)) / float(steps)
                tx = gx + r * math.cos(ang)
                ty = gy + r * math.sin(ang)
                wps = self._plan_path_without_escape(agent, start_xy, (tx, ty), waypoint_step)
                if not wps:
                    continue
                if not self._validate_planned_path(wps, agent, clearance_cells=0):
                    continue
                goal_dist = math.hypot(tx - gx, ty - gy)
                if goal_dist < best_goal_dist:
                    best_goal_dist = goal_dist
                    best_wps = wps
                    best_xy = (float(tx), float(ty))
            if best_wps is not None:
                break
            if tried >= FALLBACK_SEARCH_MAX_CANDIDATES:
                break
            if (time.perf_counter() - t0) > FALLBACK_SEARCH_TIME_BUDGET_S:
                break
        return best_wps, best_xy

    def _infer_fire_target_xy_from_tasks(
        self, task_map: Dict[str, "RobotTask"]
    ) -> Optional[Tuple[float, float]]:
        """Infer fire-area target from current team tasks (prefer red)."""
        for _task in task_map.values():
            _colour = str(getattr(_task, "subtask_colour", "") or "")
            if _colour == "red":
                _xy = getattr(_task, "target_xy", None)
                if _xy and len(_xy) >= 2:
                    return (float(_xy[0]), float(_xy[1]))
        return None

    def _is_agent_near_fire(self, agent: str, radius_m: float = FIRE_PROXIMITY_ACCEPT_RADIUS_M) -> bool:
        """Return True if robot is within fire proximity radius.

        到达判定半径必须 **大于** 派发可达半径（``FIRE_PROXIMITY_ACCEPT_RADIUS_M``）：
        ``_plan_fire_proximity_fallback`` 会把可达点放在“恰好 3.0m”处，若判定也用 3.0m，
        机器人停在该点时距火源略 ≥3.0m 会被判“未到邻域”，从而无限补发集结任务（活锁）。
        这里默认留 0.8m 余量覆盖 ``final_radius`` 与浮点边界。
        """
        if self._last_known_fire_xy is None:
            return False
        if agent not in self.base_env.scene.articulations:
            return False
        try:
            pos = get_robot_pos(self.base_env, agent)
            d = math.hypot(
                float(pos[0]) - float(self._last_known_fire_xy[0]),
                float(pos[1]) - float(self._last_known_fire_xy[1]),
            )
            return d <= float(radius_m)
        except Exception:
            return False

    def _make_fire_rally_task(self, agent: str, note: str = "补发") -> Optional["RobotTask"]:
        """Create a fire-rally task when fire position is known."""
        if self._last_known_fire_xy is None:
            return None
        fx, fy = self._last_known_fire_xy
        return RobotTask(
            task_id=f"emos_fire_rally_{agent}_{int(time.time() * 1000)}",
            task_type="emos",
            subtask_name=f"火源集结（{note}）",
            subtask_colour="grey",
            target_xy=(float(fx), float(fy)),
            waypoints=[],
            priority=0,
        )

    def _safe_replan_single(self, agent: str, start_xy: tuple, goal_xy: tuple, waypoint_step: float):
        """Replan with RRT-first / A*-fallback, validated on the already-inflated grid.

        The occupancy grid is pre-inflated (typically 14 cells ≈ 0.7 m), so
        post-validation only needs ``clearance_cells=0`` to confirm the path
        stays within free (inflated) cells.  Using a larger clearance would
        double-count the safety margin and reject legitimate A* paths near
        inflated boundaries — which is exactly the bug we saw in practice.
        """
        wps_direct = self._plan_path_without_escape(agent, start_xy, goal_xy, waypoint_step)
        if wps_direct:
            return wps_direct

        # 仅在规划失败时触发“脱困锚点”二段规划（内部路径点，不计入任务序列/任务栏）
        candidates = self._find_escape_anchor(start_xy, goal_xy)
        if candidates:
            t_escape = time.perf_counter()
            for anchor_xy in candidates:
                if (time.perf_counter() - t_escape) > ESCAPE_PHASE_MAX_TIME_S:
                    print(
                        f"  ℹ️ [EscapeReplan] {agent} 二段规划超时保护触发，"
                        f"已尝试部分锚点后提前结束（dt={time.perf_counter() - t_escape:.3f}s）"
                    )
                    break
                wps_escape = self._plan_path_without_escape(agent, start_xy, anchor_xy, waypoint_step)
                if not wps_escape:
                    continue
                wps_main = self._plan_path_without_escape(agent, anchor_xy, goal_xy, waypoint_step)
                if not wps_main:
                    continue
                merged = list(wps_escape)
                if merged and wps_main and (
                    abs(float(merged[-1][0]) - float(wps_main[0][0])) < 1e-6
                    and abs(float(merged[-1][1]) - float(wps_main[0][1])) < 1e-6
                ):
                    merged.extend(wps_main[1:])
                else:
                    merged.extend(wps_main)
                if merged and self._validate_planned_path(merged, agent, clearance_cells=0):
                    print(
                        f"  ℹ️ [EscapeReplan] {agent} 直达失败，先脱困到"
                        f" ({anchor_xy[0]:.2f},{anchor_xy[1]:.2f}) 再前往目标（{len(merged)} 路点）"
                    )
                    return merged

        print(f"  ⚠️ [路径规划] {agent} 规划器未返回可用路径，返回空路径等待后续处理")
        return None

    def _start_task(self, agent: str, task: 'RobotTask') -> None:
        """启动队列中的下一个任务，规划路径并开始导航。

        Rescue tasks are never popped on planning failure — they stay in the
        queue and the robot holds position until the next retry.
        """
        if task is None:
            self._start_patrol(agent)
            return
        is_rescue = getattr(task, "task_type", "") == "rescue"
        self._clear_hold_position(agent, f"start_task:{getattr(task, 'task_type', 'unknown')}")
        try:
            pos = get_robot_pos(self.base_env, agent)
            start_xy = (pos[0], pos[1])
            goal_xy = task.target_xy
            straight_dist = math.hypot(goal_xy[0] - start_xy[0], goal_xy[1] - start_xy[1])
            # 火源相关任务直接按 3m 邻域选可达点，不再优先尝试精确火源点。
            if self._is_fire_proximity_task(task):
                wps_relaxed, relaxed_xy = self._reuse_recent_fire_path(agent, start_xy, goal_xy)
                if wps_relaxed:
                    print(
                        f"[任务] {agent} 复用既有火源路径后缀"
                        f" ({relaxed_xy[0]:.2f}, {relaxed_xy[1]:.2f})"
                    )
                else:
                    wps_relaxed, relaxed_xy = self._plan_fire_proximity_fallback(
                        agent,
                        start_xy,
                        goal_xy,
                        waypoint_step=NAV_WAYPOINT_STEP,
                        radius_m=FIRE_PROXIMITY_ACCEPT_RADIUS_M,
                    )
                if wps_relaxed:
                    wps = wps_relaxed
                    if relaxed_xy is not None:
                        task.target_xy = relaxed_xy
                        self._remember_recent_fire_path(agent, goal_xy, relaxed_xy, wps)
                    print(
                        f"[任务] {agent} 火源任务直接采用 3m 范围可达目标"
                        f" ({task.target_xy[0]:.2f}, {task.target_xy[1]:.2f})"
                    )
                else:
                    detour_wps = self._carter_step_detour(start_xy, goal_xy, agent)
                    if detour_wps and self._validate_detour_path(detour_wps, agent):
                        wps = detour_wps
                        self.navigator.paths[agent] = wps
                        self.navigator.indices[agent] = 0
                        self.navigator.active[agent] = True
                    else:
                        wps = self._safe_replan_single(
                            agent, start_xy, goal_xy, waypoint_step=NAV_WAYPOINT_STEP
                        )
            else:
                detour_wps = self._carter_step_detour(start_xy, goal_xy, agent)
                if detour_wps and self._validate_detour_path(detour_wps, agent):
                    wps = detour_wps
                    self.navigator.paths[agent] = wps
                    self.navigator.indices[agent] = 0
                    self.navigator.active[agent] = True
                else:
                    wps = self._safe_replan_single(
                        agent, start_xy, goal_xy, waypoint_step=NAV_WAYPOINT_STEP
                    )
            if (not wps) and self._is_nearby_fallback_task(task):
                wps_relaxed, relaxed_xy = self._plan_nearby_target_fallback(
                    agent,
                    start_xy,
                    goal_xy,
                    waypoint_step=NAV_WAYPOINT_STEP,
                    radius_m=TASK_NEARBY_FALLBACK_RADIUS_M,
                )
                if wps_relaxed:
                    wps = wps_relaxed
                    if relaxed_xy is not None:
                        task.target_xy = relaxed_xy
                    print(
                        f"[任务] {agent} 目标点不可达，改为邻近可达点"
                        f" ({task.target_xy[0]:.2f}, {task.target_xy[1]:.2f})"
                    )
            if (not wps) and self._is_nearby_fallback_task(task) and self._last_known_fire_xy is not None:
                fire_xy = self._last_known_fire_xy
                wps_relaxed, relaxed_xy = self._plan_fire_proximity_fallback(
                    agent,
                    start_xy,
                    fire_xy,
                    waypoint_step=NAV_WAYPOINT_STEP,
                    radius_m=FIRE_PROXIMITY_ACCEPT_RADIUS_M,
                )
                if wps_relaxed:
                    wps = wps_relaxed
                    if relaxed_xy is not None:
                        task.target_xy = relaxed_xy
                    task.subtask_name = "火源集结（blue/red降级）"
                    task.subtask_colour = "grey"
                    print(
                        f"[任务] {agent} 原任务不可达，降级切换为火源集结点"
                        f" ({task.target_xy[0]:.2f}, {task.target_xy[1]:.2f})"
                    )
            is_fire_task = self._is_fire_proximity_task(task)
            if wps:
                self.robot_waypoints[agent] = wps
                self.robot_waypoint_indices[agent] = 0
                self.arrived_flags[agent] = False
                self.waiting_for_task[agent] = False
                self.arrival_target_yaw[agent] = task.arrival_target_yaw or self._compute_final_approach_yaw(wps)
                self.align_on_arrival_pending[agent] = False
                self.is_patrolling[agent] = False
                path_len = sum(
                    math.hypot(wps[i+1][0] - wps[i][0], wps[i+1][1] - wps[i][1])
                    for i in range(len(wps) - 1)
                )
                detour_ratio = path_len / max(straight_dist, 0.01)
                print(f"[任务] {agent} 开始执行任务 '{task.subtask_name}'，路径点 {len(wps)} 个"
                      f"（直线距离 {straight_dist:.1f}m，路径长 {path_len:.1f}m，绕路比 {detour_ratio:.1f}x）")
                if detour_ratio > 5.0 and len(wps) > 50:
                    print(f"  ⚠️ [路径诊断] {agent} 绕路比过高({detour_ratio:.1f}x)！"
                          f"起点=({start_xy[0]:.2f},{start_xy[1]:.2f}) 终点=({goal_xy[0]:.2f},{goal_xy[1]:.2f})")
                    print(f"  ⚠️ 前5路点: {wps[:5]}")
                    print(f"  ⚠️ 后5路点: {wps[-5:]}")
                if is_rescue:
                    self._rescue_retry_count.pop(agent, None)
                    self._rescue_retry_last_ts.pop(agent, None)
                if is_fire_task:
                    self._rescue_retry_count.pop(agent, None)
                    self._rescue_retry_last_ts.pop(agent, None)
            else:
                if is_rescue:
                    print(f"[任务] {agent} 救援任务路径规划暂时失败，保持任务等待重试"
                          f"（起点=({start_xy[0]:.2f},{start_xy[1]:.2f}) 终点=({goal_xy[0]:.2f},{goal_xy[1]:.2f})）")
                    self.waiting_for_task[agent] = True
                    self._set_hold_position(agent, "rescue_plan_failed")
                    self._rescue_retry_count[agent] = self._rescue_retry_count.get(agent, 0) + 1
                    self._rescue_retry_last_ts.setdefault(agent, time.time())
                elif is_fire_task:
                    print(
                        f"[任务] {agent} 火源任务路径规划暂时失败，保持任务等待重试"
                        f"（起点=({start_xy[0]:.2f},{start_xy[1]:.2f}) 终点=({goal_xy[0]:.2f},{goal_xy[1]:.2f})）"
                    )
                    self.waiting_for_task[agent] = True
                    self._set_hold_position(agent, "fire_plan_failed")
                    self._rescue_retry_count[agent] = self._rescue_retry_count.get(agent, 0) + 1
                    self._rescue_retry_last_ts.setdefault(agent, time.time())
                else:
                    print(f"[任务] {agent} 路径规划失败，跳过任务 '{task.subtask_name}'"
                          f"（起点=({start_xy[0]:.2f},{start_xy[1]:.2f}) 终点=({goal_xy[0]:.2f},{goal_xy[1]:.2f})）")
                    self.task_queues[agent].pop()
                    next_task = self.task_queues[agent].peek()
                    if next_task:
                        self._start_task(agent, next_task)
                    else:
                        self._start_patrol(agent)
        except Exception as e:
            print(f"[任务] {agent} _start_task 异常: {e}")
            import traceback; traceback.print_exc()
            if not is_rescue:
                self._start_patrol(agent)

    def interrupt_patrol_with_task(self, agent: str, task: 'RobotTask',
                                   priority: bool = False) -> None:
        """中断巡逻，将新任务插入队列并立即执行。
        priority=True 时插入队头（救援优先），否则追加队尾（EMOS 分配）。
        若机器人当前已在执行相同目标的任务（非巡逻），则跳过，避免重复打断。
        """
        if (
            (not priority)
            and agent in self.manual_override_agents
            and not self._is_manual_task(task)
        ):
            self._enqueue_deferred_system_task(agent, task, reason="manual_override_active")
            return

        # 若当前正在执行非巡逻任务且目标相同，仅当导航仍在进行或已到点等待时跳过；
        # 若同目标但路径未激活且未在等待（首轮派遣后被误跳过），必须重新 set_path。
        if not self.is_patrolling.get(agent, True) and not priority:
            cur = self.task_queues[agent].peek() if self.task_queues.get(agent) else None
            if cur is not None and cur.target_xy == task.target_xy:
                if self.navigator.is_active(agent):
                    print(f"[任务] {agent} 同目标任务且导航已激活，跳过重复派发")
                    return
                if self.waiting_for_task.get(agent, False):
                    try:
                        cpos = get_robot_pos(self.base_env, agent)
                        d_same = math.hypot(
                            float(cpos[0]) - float(task.target_xy[0]),
                            float(cpos[1]) - float(task.target_xy[1]),
                        )
                    except Exception:
                        d_same = 999.0
                    if self.arrived_flags.get(agent, False) and d_same < 0.6:
                        print(f"[任务] {agent} 同目标任务且已到达目标附近（{d_same:.2f}m），跳过重复派发")
                        return
                    print(f"[任务] {agent} 同目标任务但导航未激活/状态异常，强制重启路径")

        self.is_patrolling[agent] = False
        self._clear_hold_position(agent, "dispatch_new_task")
        if priority:
            self.task_queues[agent].push_front(task)
        else:
            if self._is_manual_task(task):
                self.manual_override_agents.add(agent)
                self._capture_current_system_tasks_for_manual(agent)
            elif agent in self.manual_override_agents:
                self._enqueue_deferred_system_task(agent, task, reason="manual_override_nonpriority")
                return
            # 清空旧队列再插入，避免堆积过期任务
            self.task_queues[agent].clear()
            self.task_queues[agent].push_back(task)
        self._start_task(agent, self.task_queues[agent].peek())

    def batch_dispatch_tasks(
        self,
        task_map: Dict[str, 'RobotTask'],
        *,
        priorities: Optional[Dict[str, int]] = None,
        skip_agents: Optional[set] = None,
    ) -> int:
        """Plan paths for multiple robots using conflict-aware batch planning.

        Planning runs in a background thread so the main simulation loop is not
        blocked.  Call :meth:`consume_pending_plans` each frame to pick up
        completed results and actually dispatch robots.

        Returns 0 immediately; the real dispatch count comes from
        ``consume_pending_plans``.
        """
        if self._pending_plan is not None:
            # 当前已有规划批次在跑：先入队，等待空闲时统一异步提交，避免覆盖 pending 结果。
            for rn, task in task_map.items():
                if rn in (skip_agents or set()) or task is None:
                    continue
                if rn in self.manual_override_agents and not self._is_manual_task(task):
                    self._enqueue_deferred_system_task(rn, task, reason="pending_plan_backlog")
                    continue
                self._async_plan_backlog[rn] = task
            if task_map:
                print(
                    f"[批量规划] 当前已有 pending 规划，新增 {len(task_map)} 个请求已转入异步队列等待"
                )
            return 0

        skip = skip_agents or set()
        if self._nav_session is None:
            cnt = 0
            dispatched_agents: List[str] = []
            deferred_agents: List[str] = []
            for rn, task in task_map.items():
                if rn in skip:
                    continue
                if rn in self.manual_override_agents and not self._is_manual_task(task):
                    self._enqueue_deferred_system_task(rn, task, reason="sync_batch_dispatch")
                    deferred_agents.append(rn)
                    continue
                self.hold_position.discard(rn)
                self.interrupt_patrol_with_task(rn, task, priority=False)
                cnt += 1
                dispatched_agents.append(rn)
            self._set_batch_plan_report({
                "status": "ok",
                "batch_id": -1,
                "requested_agents": sorted([rn for rn in task_map.keys() if rn not in skip]),
                "dispatched_agents": sorted(dispatched_agents),
                "failed_agents": [],
                "deferred_agents": sorted(deferred_agents),
                "dispatched_count": int(cnt),
                "failed_count": 0,
            })
            return cnt

        targets: Dict[str, Tuple[float, float]] = {}
        valid_tasks: Dict[str, 'RobotTask'] = {}
        for rn, task in task_map.items():
            if rn in skip or rn not in self.base_env.scene.articulations:
                continue
            if rn in self.manual_override_agents and not self._is_manual_task(task):
                self._enqueue_deferred_system_task(rn, task, reason="async_batch_dispatch")
                continue
            targets[rn] = task.target_xy
            valid_tasks[rn] = task

        if not targets:
            self._set_batch_plan_report({
                "status": "ok",
                "batch_id": -1,
                "requested_agents": sorted([rn for rn in task_map.keys() if rn not in skip]),
                "dispatched_agents": [],
                "failed_agents": [],
                "deferred_agents": sorted(
                    [rn for rn in task_map.keys() if rn in self.manual_override_agents and rn not in skip]
                ),
                "dispatched_count": 0,
                "failed_count": 0,
            })
            return 0

        requests = []
        start_by_robot: Dict[str, Tuple[float, float]] = {}
        for rn, goal_xy in targets.items():
            pos = get_robot_pos(self.base_env, rn)
            start_xy = (pos[0], pos[1])
            requests.append((rn, start_xy, goal_xy))
            start_by_robot[rn] = start_xy

        prio = priorities or {}
        batch_id = self._next_plan_batch_id
        self._next_plan_batch_id += 1
        if USE_CONFLICT_AWARE_PLANNING and self._nav_session is not None:
            print(f"[批量规划] 提交异步 conflict-aware 规划，机器人数={len(requests)}")
        else:
            print(f"[批量规划] 提交异步独立规划（关闭 conflict-aware），机器人数={len(requests)}")

        self._pending_plan = {
            "batch_id": batch_id,
            "task_map": valid_tasks,
            "start_by_robot": start_by_robot,
        }
        self._plan_thread = threading.Thread(
            target=self._plan_worker,
            args=(requests, prio, batch_id),
            daemon=True,
            name="NavPlanBatch",
        )
        self._plan_thread.start()
        return 0

    # ── 后台规划线程 & 结果消费 ───────────────────────────────────────────────

    def _plan_worker(self, requests, priorities, batch_id: int):
        """Background thread: call plan_batch and enqueue result."""
        try:
            if USE_CONFLICT_AWARE_PLANNING and self._nav_session is not None:
                planned = self._nav_session.plan_batch(requests, priorities=priorities)
            else:
                planned = {}
                for rn, start_xy, goal_xy in requests:
                    planned[rn] = self._safe_replan_single(
                        rn, start_xy, goal_xy, waypoint_step=NAV_WAYPOINT_STEP
                    )
            self._plan_result_queue.put((batch_id, "ok", planned))
        except Exception as e:
            self._plan_result_queue.put((batch_id, "error", str(e)))

    def consume_pending_plans(self) -> int:
        """Consume completed background planning results and dispatch robots.

        Should be called once per frame in the main loop (before ``compute_actions``).
        Returns the number of robots successfully dispatched this frame, or 0 if
        no results are ready yet.
        """
        if self._pending_plan is None:
            # 主循环空闲时，自动拉起 check_stuck 等环节投递的异步重规划请求。
            self._flush_async_plan_backlog()
            return 0
        pending = self._pending_plan
        pending_batch_id = int(pending.get("batch_id", -1))

        matched = None
        while True:
            item = None
            try:
                item = self._plan_result_queue.get_nowait()
            except (_TEmpty, Exception):
                pass
            if item is None:
                return 0
            if len(item) == 3:
                batch_id, status, payload = item
            else:
                batch_id, status, payload = pending_batch_id, item[0], item[1]
            if int(batch_id) != pending_batch_id:
                print(
                    f"[批量规划] ℹ️ 丢弃过期结果 batch={batch_id}，"
                    f"当前待消费 batch={pending_batch_id}"
                )
                continue
            matched = (status, payload)
            break

        self._pending_plan = None
        status, payload = matched

        if status != "ok":
            print(f"[批量规划] ⚠️ 后台规划出错: {payload}")
            self._set_batch_plan_report({
                "status": "error",
                "batch_id": pending_batch_id,
                "requested_agents": sorted(list(pending["task_map"].keys())),
                "dispatched_agents": [],
                "failed_agents": sorted(list(pending["task_map"].keys())),
                "error": str(payload),
            })
            self._flush_async_plan_backlog()
            return 0

        dispatched = self._apply_plan_results(payload, pending, pending_batch_id)
        self._flush_async_plan_backlog()
        return dispatched

    def _apply_plan_results(self, planned: dict, pending: dict, batch_id: int = -1) -> int:
        """Apply waypoints from background planner; always dispatches if a path exists."""
        valid_tasks = pending["task_map"]
        start_by_robot = pending["start_by_robot"]
        inferred_fire_xy = self._infer_fire_target_xy_from_tasks(valid_tasks)
        if inferred_fire_xy is not None:
            self._last_known_fire_xy = inferred_fire_xy

        cnt = 0
        dispatched_agents: List[str] = []
        failed_agents: List[str] = []
        deferred_agents: List[str] = []
        for rn, task in valid_tasks.items():
            if rn in self.manual_override_agents and not self._is_manual_task(task):
                self._enqueue_deferred_system_task(rn, task, reason="apply_plan_results")
                deferred_agents.append(rn)
                continue
            if self._is_fire_proximity_task(task):
                # 火源类任务历史上在消费批量结果时一律走 _plan_fire_proximity_fallback（主线程），
                # 与后台已算好的 batch 路径重复且极慢（多候选 × RRT/A*，预算可达数秒级×机器人数）。
                # 优先复用后台 planned 路径：终点落在火源邻域且校验通过则不再做邻域搜索。
                wps = None
                wps_relaxed: Optional[list] = None
                relaxed_xy: Optional[Tuple[float, float]] = None
                nominal_fire_goal = task.target_xy
                _batch = planned.get(rn)
                _batch_end = (
                    self._fire_batch_path_usable(rn, _batch, nominal_fire_goal) if _batch else None
                )
                if _batch_end is not None and _batch is not None:
                    wps_relaxed = _batch
                    relaxed_xy = _batch_end
                    print(
                        f"[批量规划] {rn} 火源任务复用后台路径（{len(_batch)} 点）→ "
                        f"邻域目标 ({relaxed_xy[0]:.2f}, {relaxed_xy[1]:.2f}），跳过主线程邻域搜索"
                    )
                if wps_relaxed is None:
                    wps_relaxed, relaxed_xy = self._reuse_recent_fire_path(
                        rn, start_by_robot[rn], task.target_xy
                    )
                    if wps_relaxed:
                        print(
                            f"[批量规划] {rn} 复用既有火源路径后缀"
                            f" ({relaxed_xy[0]:.2f}, {relaxed_xy[1]:.2f})"
                        )
                if wps_relaxed is None:
                    wps_relaxed, relaxed_xy = self._plan_fire_proximity_fallback(
                        rn,
                        start_by_robot[rn],
                        task.target_xy,
                        waypoint_step=NAV_WAYPOINT_STEP,
                        radius_m=FIRE_PROXIMITY_ACCEPT_RADIUS_M,
                    )
                if wps_relaxed:
                    wps = wps_relaxed
                    if relaxed_xy is not None:
                        nominal_goal_xy = task.target_xy
                        task.target_xy = relaxed_xy
                        self._remember_recent_fire_path(rn, nominal_goal_xy, relaxed_xy, wps)
                    if _batch_end is None:
                        print(
                            f"[批量规划] {rn} 火源任务直接采用 3m 范围可达目标"
                            f" ({task.target_xy[0]:.2f}, {task.target_xy[1]:.2f})"
                        )
            else:
                wps = planned.get(rn)
            if not wps:
                if not self._is_nearby_fallback_task(task):
                    wps = self._safe_replan_single(
                        rn, start_by_robot[rn], task.target_xy,
                        waypoint_step=NAV_WAYPOINT_STEP,
                    )
            if not wps:
                if (not wps) and self._is_nearby_fallback_task(task):
                    wps_relaxed, relaxed_xy = self._plan_nearby_target_fallback(
                        rn,
                        start_by_robot[rn],
                        task.target_xy,
                        waypoint_step=NAV_WAYPOINT_STEP,
                        radius_m=TASK_NEARBY_FALLBACK_RADIUS_M,
                    )
                    if wps_relaxed:
                        wps = wps_relaxed
                        if relaxed_xy is not None:
                            task.target_xy = relaxed_xy
                        print(
                            f"[批量规划] {rn} 目标点不可达，改为邻近可达点"
                            f" ({task.target_xy[0]:.2f}, {task.target_xy[1]:.2f})"
                        )
                if (
                    (not wps)
                    and self._is_nearby_fallback_task(task)
                    and self._last_known_fire_xy is not None
                ):
                    fire_xy = self._last_known_fire_xy
                    wps_relaxed, relaxed_xy = self._plan_fire_proximity_fallback(
                        rn,
                        start_by_robot[rn],
                        fire_xy,
                        waypoint_step=NAV_WAYPOINT_STEP,
                        radius_m=FIRE_PROXIMITY_ACCEPT_RADIUS_M,
                    )
                    if wps_relaxed:
                        wps = wps_relaxed
                        if relaxed_xy is not None:
                            task.target_xy = relaxed_xy
                        task.subtask_name = "火源集结（blue/red降级）"
                        task.subtask_colour = "grey"
                        print(
                            f"[批量规划] {rn} 原任务不可达，降级切换为火源集结点"
                            f" ({task.target_xy[0]:.2f}, {task.target_xy[1]:.2f})"
                        )
                if not wps:
                    failed_agents.append(rn)
                    self._last_plan_failed_agents.add(rn)
                    self.waiting_for_task[rn] = True
                    self.is_patrolling[rn] = False
                    if self.suppress_patrol_after_emos:
                        # EMOS 阶段后禁止回巡逻：失败机器人原地待命，等待后续重分配/集结。
                        self.suppress_patrol_exempt.discard(rn)
                        self._set_hold_position(rn, "batch_plan_failed_after_emos")
                        print(f"[批量规划] ⚠️ {rn} 无法获得路径，保持待命等待后续派发")
                    else:
                        # 仅允许在 EMOS 启动前回到巡逻。
                        self.suppress_patrol_exempt.add(rn)
                        self._clear_hold_position(rn, "batch_plan_failed_pre_emos")
                        self._start_patrol(rn)
                        print(
                            f"[批量规划] ⚠️ {rn} 无法获得路径，切回巡逻待重分配"
                            f"（suppress_exempt={rn in self.suppress_patrol_exempt}）"
                        )
                    continue

            if not self._validate_planned_path(wps, rn, clearance_cells=0):
                print(f"[批量规划] ⚠️ {rn} 路径在膨胀格 clearance=0 下未通过，尝试 A* 重规划")
                wps_retry = self._safe_replan_single(
                    rn,
                    start_by_robot[rn],
                    task.target_xy,
                    waypoint_step=NAV_WAYPOINT_STEP,
                )
                if wps_retry:
                    wps = wps_retry

            self.hold_position.discard(rn)
            self.is_patrolling[rn] = False
            self.task_queues[rn].clear()
            self.task_queues[rn].push_back(task)

            self.robot_waypoints[rn] = wps
            self.robot_waypoint_indices[rn] = 0
            self.arrived_flags[rn] = False
            self.waiting_for_task[rn] = False
            self._last_plan_failed_agents.discard(rn)
            self.suppress_patrol_exempt.discard(rn)
            self.arrival_target_yaw[rn] = (
                task.arrival_target_yaw or self._compute_final_approach_yaw(wps)
            )
            self.align_on_arrival_pending[rn] = False

            straight_dist = math.hypot(
                task.target_xy[0] - start_by_robot[rn][0],
                task.target_xy[1] - start_by_robot[rn][1],
            )
            path_len = sum(
                math.hypot(wps[i + 1][0] - wps[i][0], wps[i + 1][1] - wps[i][1])
                for i in range(len(wps) - 1)
            ) if len(wps) > 1 else 0.0
            print(
                f"[批量规划] {rn} → '{task.subtask_name}' {len(wps)} 路径点"
                f"（直线 {straight_dist:.1f}m / 路径 {path_len:.1f}m）"
            )
            cnt += 1
            dispatched_agents.append(rn)
        self._set_batch_plan_report({
            "status": "ok",
            "batch_id": batch_id,
            "requested_agents": sorted(list(valid_tasks.keys())),
            "dispatched_agents": sorted(dispatched_agents),
            "failed_agents": sorted(failed_agents),
            "deferred_agents": sorted(deferred_agents),
            "dispatched_count": int(cnt),
            "failed_count": int(len(failed_agents)),
        })
        return cnt

    # ── 每帧主控制 ──────────────────────────────────────────────────────────────

    def compute_actions(self) -> Dict[str, torch.Tensor]:
        """计算全部机器人本帧动作，返回 {agent_name: action_tensor}。"""
        self.events.clear()
        actions = {}

        if self._carter_start_time is None:
            self._carter_start_time = time.time()

        if self.all_stopped:
            return self._stopped_actions()

        for agent in self.possible_agents:
            if agent not in self.robot_waypoints:
                continue
            if agent not in self.base_env.scene.articulations:
                continue

            if agent == "carter_1" and self.carter1_obstacle_pause:
                if agent in self.goal_controlled_robots and hasattr(self.base_env, "set_command"):
                    robot = self.base_env.scene.articulations[agent]
                    self.base_env.set_command(agent, "goal_position", robot.data.root_pos_w[0].unsqueeze(0))
                    actions[agent] = torch.zeros((self.num_envs, 3), device=self.device)
                else:
                    self.last_cmd_vel[agent][:] = 0
                    self.robot_commands[agent][:] = 0
                    actions[agent] = self.robot_commands[agent]
                continue

            curr_pos, curr_quat = get_robot_pose_tensors(self.base_env, agent)
            curr_yaw = get_yaw_from_quat(curr_quat)

            # 到达后：检查 TaskQueue，有任务则执行下一个，否则恢复巡逻
            if (
                self.waiting_for_task.get(agent, False)
                and not self.key5_active
                and not self.navigator.is_active(agent)
            ):
                if agent in self.hold_position:
                    if agent in self.goal_controlled_robots and hasattr(self.base_env, "set_command"):
                        robot = self.base_env.scene.articulations[agent]
                        self.base_env.set_command(agent, "goal_position", robot.data.root_pos_w[0].unsqueeze(0))
                        actions[agent] = torch.zeros((self.num_envs, 3), device=self.device)
                    else:
                        self.last_cmd_vel[agent][:] = 0
                        self.robot_commands[agent][:] = 0
                        actions[agent] = self.robot_commands[agent]
                    continue
                # 弹出已完成任务，检查队列
                queue = self.task_queues.get(agent)
                if queue and not queue.is_empty():
                    # 弹出刚完成的任务
                    done_task = queue.pop()
                    if getattr(done_task, "task_id", None) == "emos_yellow_fire_delivery":
                        self.events.append(f"{agent}_fire_delivery_complete")
                    next_task = queue.peek()
                    if next_task is not None:
                        # 有下一个任务，立即执行
                        self._start_task(agent, next_task)
                        continue
                    else:
                        # EMOS 任务完成后默认待命，不回巡逻；等待后续重分配/集结任务。
                        if getattr(done_task, "task_type", "") == "emos":
                            # 仅当已到火源邻域才允许进入待命；否则必须继续前往火源附近。
                            # 判定半径放宽 0.8m，避免与派发可达半径在边界相等而活锁。
                            near_fire = self._is_agent_near_fire(
                                agent, radius_m=FIRE_PROXIMITY_ACCEPT_RADIUS_M + 0.8
                            )
                            # 止损：即便仍判“未到”，连续补发超过上限也强制待命，杜绝残留边界活锁。
                            rally_cnt = self._fire_rally_redispatch_count.get(agent, 0)
                            if near_fire or rally_cnt >= 3:
                                if not near_fire:
                                    print(
                                        f"[EMOS] {agent} 连续补发 {rally_cnt} 次仍判未到火源邻域，"
                                        f"强制进入待命（避免活锁）"
                                    )
                                self._fire_rally_redispatch_count.pop(agent, None)
                                self.suppress_patrol_agents.add(agent)
                                self.hold_position.add(agent)
                                self.waiting_for_task[agent] = True
                                self.is_patrolling[agent] = False
                                print(f"[EMOS] {agent} 已到火源邻域，任务队列清空后进入待命")
                            else:
                                rally_task = self._make_fire_rally_task(agent, note="队列清空补发")
                                if rally_task is not None:
                                    self._fire_rally_redispatch_count[agent] = rally_cnt + 1
                                    self._clear_hold_position(agent, "emos_queue_empty_resume_to_fire")
                                    self.task_queues[agent].clear()
                                    self.task_queues[agent].push_back(rally_task)
                                    self.waiting_for_task[agent] = False
                                    self.is_patrolling[agent] = False
                                    print(
                                        f"[EMOS] {agent} 未到火源邻域，禁止待命，补发火源集结任务 "
                                        f"({rally_task.target_xy[0]:.2f}, {rally_task.target_xy[1]:.2f}) "
                                        f"[第 {rally_cnt + 1} 次]"
                                    )
                                    self._start_task(agent, rally_task)
                                else:
                                    # 未知火源位置时不进入待命，维持可恢复状态等待后续派发。
                                    self.waiting_for_task[agent] = True
                                    self.is_patrolling[agent] = False
                                    self._clear_hold_position(agent, "emos_queue_empty_wait_reassign")
                                    print(f"[EMOS] {agent} 未知火源位置，暂不待命，等待后续派发")
                            continue
                        # 语音插入的黄色/绿色任务到点后还要等待主循环执行机械臂阶段，
                        # 暂不恢复被打断的系统任务，避免抓取/按按钮流程被旧任务抢回。
                        if (
                            getattr(done_task, "task_type", "") == "manual"
                            and str(getattr(done_task, "task_id", "")).startswith("voice_")
                            and str(getattr(done_task, "subtask_colour", "")) in ("yellow", "green")
                        ):
                            self.suppress_patrol_agents.add(agent)
                            self.hold_position.add(agent)
                            self.waiting_for_task[agent] = True
                            self.is_patrolling[agent] = False
                            print(f"[语音接管] {agent} 已到达 {done_task.subtask_name}，等待动作阶段完成")
                            continue
                        # 人机手动任务结束后默认进入待命，避免自动跳回巡逻。
                        if getattr(done_task, "task_type", "") == "manual":
                            self.manual_override_agents.discard(agent)
                            if self._resume_deferred_system_task(agent):
                                print(f"[手动接管] {agent} 手动任务完成，已恢复系统任务队列")
                                continue
                            self.suppress_patrol_agents.add(agent)
                            self.hold_position.add(agent)
                            self.waiting_for_task[agent] = True
                            self.is_patrolling[agent] = False
                            print(f"[手动接管] {agent} 手动任务完成，无待恢复任务，进入待命")
                            continue
                        # 队列已空，恢复巡逻
                        self._start_patrol(agent)
                        continue
                elif queue and queue.is_empty() and not self.is_patrolling.get(agent, False):
                    # 队列为空且不在巡逻，恢复巡逻
                    self._start_patrol(agent)
                    continue

                if agent in self.goal_controlled_robots and hasattr(self.base_env, "set_command"):
                    robot = self.base_env.scene.articulations[agent]
                    self.base_env.set_command(agent, "goal_position", robot.data.root_pos_w[0].unsqueeze(0))
                    actions[agent] = torch.zeros((self.num_envs, 3), device=self.device)
                else:
                    # 如果有到点朝向对齐任务，则仅旋转不平移
                    target_yaw = self.arrival_target_yaw.get(agent)
                    if self.align_on_arrival_pending.get(agent, False) and target_yaw is not None:
                        yaw_err = normalize_angle(target_yaw - curr_yaw)
                        if abs(yaw_err) > 0.08:
                            k_w = 3.2 if agent == "scout_1" else 1.8
                            max_w = 2.2 if agent == "scout_1" else (0.6 if agent.startswith("carter") else M20_MAX_ANG_SPEED)
                            wz = max(-max_w, min(max_w, k_w * yaw_err))
                            self.last_cmd_vel[agent][0] = 0.0
                            self.last_cmd_vel[agent][1] = 0.0
                            self.last_cmd_vel[agent][2] = wz
                            self.robot_commands[agent][:] = 0
                            self.robot_commands[agent][:, 2] = wz
                            actions[agent] = self.robot_commands[agent]
                            continue
                        self.align_on_arrival_pending[agent] = False
                        print(f"✅ {agent} 已完成到点朝向对齐，进入等待状态")

                    self.last_cmd_vel[agent][:] = 0
                    self.robot_commands[agent][:] = 0
                    actions[agent] = self.robot_commands[agent]
                continue

            target, arrival_dist, has_key5_next, key5_seg_idx = self._resolve_target(agent, curr_pos)
            tx, ty = float(target[0]), float(target[1])
            tz = float(curr_pos[2].item()) if hasattr(curr_pos[2], "item") else float(curr_pos[2])
            target_tensor = torch.tensor([[tx, ty, tz]], device=self.device)
            dist = torch.norm(curr_pos[:2] - target_tensor[0, :2]).item()

            if agent in self.goal_controlled_robots:
                actions[agent] = self._goal_action(agent, target_tensor, dist, arrival_dist,
                                                    curr_pos, has_key5_next, key5_seg_idx)
            else:
                act = self._velocity_action(agent, target_tensor, dist, arrival_dist,
                                            curr_pos, curr_yaw, has_key5_next, key5_seg_idx)
                if act is not None:
                    actions[agent] = act

        return actions

    # ── Goal 控制（M20）─────────────────────────────────────────────────────────

    def _goal_action(self, agent, target_tensor, dist, arrival_dist,
                     curr_pos, has_key5_next, key5_seg_idx):
        zero = torch.zeros((self.num_envs, 3), device=self.device)
        key5_val = KEY5_TARGET_POSITIONS.get(agent) if self.key5_active else None

        if self.key5_active and key5_val is not None:
            self.robot_goal_positions[agent] = target_tensor
            if hasattr(self.base_env, "set_command"):
                self.base_env.set_command(agent, "goal_position", target_tensor)
            if dist < arrival_dist:
                if has_key5_next:
                    self.key5_segment_indices[agent] = key5_seg_idx + 1
                    if not self.arrived_flags[agent]:
                        print(f"✅ {agent} Key5 第 {key5_seg_idx+1} 段完成，前往下一段")
                    self.arrived_flags[agent] = False
                else:
                    if not self.arrived_flags[agent]:
                        print(f"✅ {agent} 到达 Key5 终点")
                    self.arrived_flags[agent] = True
                    self.waiting_for_task[agent] = True
            else:
                self.arrived_flags[agent] = False
            return zero

        if self.navigator.is_active(agent):
            cxy = (float(curr_pos[0].item()), float(curr_pos[1].item()))
            cz = float(curr_pos[2].item())
            _tq = self.task_queues.get(agent)
            _cur = _tq.peek() if _tq and not _tq.is_empty() else None
            if (
                _cur is not None
                and getattr(_cur, "subtask_colour", "") == "yellow"
                and getattr(_cur, "task_id", "") != "emos_yellow_fire_delivery"
            ):
                _goal_final_r = max(NAV_FINAL_RADIUS, M20_EXTINGUISHER_PICKUP_FINAL_RADIUS)
            else:
                _goal_final_r = NAV_FINAL_RADIUS
            goal_xyz, done = self.navigator.compute_goal(
                agent, cxy, cz, arrive_radius=NAV_ARRIVE_RADIUS,
                lookahead=NAV_LOOKAHEAD, final_radius=_goal_final_r,
            )
            if goal_xyz is not None:
                nt = torch.tensor([goal_xyz], device=self.device)
                self.robot_goal_positions[agent] = nt
                if hasattr(self.base_env, "set_command"):
                    self.base_env.set_command(agent, "goal_position", nt)
            if done:
                if not self.arrived_flags.get(agent, True):
                    print(f"✅ {agent} 到达 RRT/Web 终点")
                    self.events.append(f"{agent}_nav_arrived")
                self.arrived_flags[agent] = True
                self.waiting_for_task[agent] = True
            else:
                self.arrived_flags[agent] = False
            return zero

        # 无 RRT/Web 导航激活时：巡逻沿路径点直连 goal（Carter 差速车等 goal_position 控制）
        # 否则会把目标锁在 curr_pos，导致自动巡航不动；Dashboard set_path 会激活 navigator 故能走。
        if self.is_patrolling.get(agent, False) and not self.key5_active:
            self.robot_goal_positions[agent] = target_tensor
            if hasattr(self.base_env, "set_command"):
                self.base_env.set_command(agent, "goal_position", target_tensor)
            self._check_arrival(agent, dist, arrival_dist, has_key5_next, key5_seg_idx,
                                curr_pos, target_tensor)
            if dist >= arrival_dist:
                self.arrived_flags[agent] = False
            return zero

        # 无激活导航且非巡逻：保持当前位置
        if hasattr(self.base_env, "set_command"):
            self.base_env.set_command(agent, "goal_position", curr_pos.unsqueeze(0))
        self.arrived_flags[agent] = True
        return zero

    # ── 速度控制（Carter）──────────────────────────────────────────────────────

    def _velocity_action(self, agent, target_tensor, dist, arrival_dist,
                         curr_pos, curr_yaw, has_key5_next, key5_seg_idx):
        is_carter = agent.startswith("carter")
        is_m20 = agent.startswith("m20")
        # scout_1 uses Scout skid-steer (SCOUT_DIFF): command uses vx and wz only; vy is ignored.
        is_scout_scout1 = agent == "scout_1"

        # Carter 延迟启动
        if is_carter and self._carter_start_time is not None:
            elapsed = time.time() - self._carter_start_time
            if elapsed < CARTER_DELAY_SECONDS:
                return torch.zeros((self.num_envs, 3), device=self.device)
            elif not self._carter_delay_printed.get(agent, False):
                print(f"⏱️ {agent} 延迟结束，开始移动")
                self._carter_delay_printed[agent] = True

        # Navigator 覆盖
        if not self.key5_active and self.navigator.is_active(agent):
            cxy = (float(curr_pos[0].item()), float(curr_pos[1].item()))
            cz = float(curr_pos[2].item()) if hasattr(curr_pos[2], "item") else float(curr_pos[2])
            _tq = self.task_queues.get(agent)
            _cur = _tq.peek() if _tq and not _tq.is_empty() else None
            if (
                is_m20
                and _cur is not None
                and getattr(_cur, "subtask_colour", "") == "yellow"
                and getattr(_cur, "task_id", "") != "emos_yellow_fire_delivery"
            ):
                _nav_final_r = max(NAV_FINAL_RADIUS, M20_EXTINGUISHER_PICKUP_FINAL_RADIUS)
            else:
                _nav_final_r = NAV_FINAL_RADIUS
            nav_goal, nav_done = self.navigator.compute_goal(
                agent, cxy, cz,
                arrive_radius=max(arrival_dist, NAV_ARRIVE_RADIUS),
                lookahead=NAV_LOOKAHEAD, final_radius=_nav_final_r,
            )
            # 与 Navigator 的路径点进度同步，避免 robot_waypoint_indices 落后导致下一帧目标倒退
            wps = self.robot_waypoints.get(agent, [])
            if wps:
                nav_idx = self.navigator.get_index(agent)
                self.robot_waypoint_indices[agent] = min(nav_idx, len(wps) - 1)
            if nav_done:
                self.navigator.set_active(agent, False)
                if not self.arrived_flags.get(agent, True):
                    print(f"✅ {agent} 到达 Web 导航终点")
                    self.events.append(f"{agent}_nav_arrived")
                self.arrived_flags[agent] = True
                self.waiting_for_task[agent] = True
            elif nav_goal is not None:
                tx, ty = nav_goal[0], nav_goal[1]
                tz = cz
                target_tensor = torch.tensor([[tx, ty, tz]], device=self.device)
                dist = torch.norm(curr_pos[:2] - target_tensor[0, :2]).item()
                self.arrived_flags[agent] = False

        # 到达判定 + 路径点推进
        self._check_arrival(agent, dist, arrival_dist, has_key5_next, key5_seg_idx,
                            curr_pos, target_tensor)

        # 速度计算
        raw_vx, raw_vy, raw_wz = 0.0, 0.0, 0.0
        if self.arrived_flags[agent] and dist < arrival_dist:
            pass
        else:
            self.arrived_flags[agent] = False
            global_dx = target_tensor[0, 0].item() - curr_pos[0].item()
            global_dy = target_tensor[0, 1].item() - curr_pos[1].item()
            target_yaw = math.atan2(global_dy, global_dx)
            yaw_err = normalize_angle(target_yaw - curr_yaw)

            if is_m20:
                max_spd = M20_MAX_LIN_SPEED
                kp = M20_KP_LIN
                tgt_spd = min(max_spd, dist * kp)
                if dist < 0.35:
                    tgt_spd *= dist / 0.35
                cos_y = math.cos(curr_yaw)
                sin_y = math.sin(curr_yaw)
                local_dx = global_dx * cos_y + global_dy * sin_y
                local_dy = -global_dx * sin_y + global_dy * cos_y
                ln = math.sqrt(local_dx ** 2 + local_dy ** 2)
                if ln > 1e-5:
                    raw_vx = (local_dx / ln) * tgt_spd
                    raw_vy = (local_dy / ln) * tgt_spd
                raw_wz = max(-M20_MAX_ANG_SPEED, min(M20_MAX_ANG_SPEED, yaw_err * M20_KP_ANG))
            elif is_scout_scout1:
                abs_yaw_err = abs(yaw_err)
                if abs_yaw_err > SCOUT1_ALIGN_THRESH:
                    raw_vx = 0.03 * max(0.0, 1.0 - abs_yaw_err / math.pi)
                    raw_wz = SCOUT1_TURN_SPEED * (1.0 if yaw_err > 0 else -1.0)
                else:
                    speed_scale = 1.0 - 0.6 * (abs_yaw_err / SCOUT1_ALIGN_THRESH)
                    raw_vx = max(0.15, min(SCOUT1_MAX_SPEED * speed_scale, dist))
                    raw_wz = max(-SCOUT1_MAX_OMEGA, min(SCOUT1_MAX_OMEGA, 3.8 * yaw_err))
            else:
                if abs(yaw_err) > CARTER_ALIGN_THRESH:
                    raw_vx = 0.0
                    raw_wz = CARTER_TURN_SPEED * (1.0 if yaw_err > 0 else -1.0)
                else:
                    raw_vx = max(0.1, min(CARTER_MAX_SPEED, dist))
                    raw_wz = max(-CARTER_MAX_OMEGA, min(CARTER_MAX_OMEGA, 2.0 * yaw_err))

        # 平滑
        alpha = SMOOTH_ALPHA_M20 if is_m20 else (SMOOTH_ALPHA_SCOUT1 if is_scout_scout1 else SMOOTH_ALPHA_CARTER)
        lv = self.last_cmd_vel[agent]
        svx = lv[0].item() * (1 - alpha) + raw_vx * alpha
        svy = lv[1].item() * (1 - alpha) + raw_vy * alpha
        swz = lv[2].item() * (1 - alpha) + raw_wz * alpha
        lv[0], lv[1], lv[2] = svx, svy, swz
        cmd = self.robot_commands[agent]
        cmd[:, 0], cmd[:, 1], cmd[:, 2] = svx, svy, swz
        return cmd

    # ── 到达判定 ──────────────────────────────────────────────────────────────

    def _check_arrival(self, agent, dist, arrival_dist, has_key5_next, key5_seg_idx,
                       curr_pos, target_tensor):
        if dist >= arrival_dist:
            return

        # Navigator 激活时 dist 多为「前瞻点 / 胡萝卜」距离，不是离散路点距；若仍用 _check_arrival
        # 推进索引，会每帧误判「到达路径点」并刷屏，且与 compute_goal 内部索引重复。
        if not self.key5_active and self.navigator.is_active(agent):
            return

        if not self.key5_active:
            wps = self.robot_waypoints.get(agent, [])
            idx = self.robot_waypoint_indices.get(agent, 0)
            is_last = idx >= len(wps) - 1
            is_nav_one_shot = self.navigator.is_active(agent)
            if is_last and is_nav_one_shot:
                if not self.arrived_flags[agent]:
                    print(f"✅ {agent} 到达 RRT/Web 终点")
                    self.events.append(f"{agent}_nav_arrived")
                self.arrived_flags[agent] = True
                self.waiting_for_task[agent] = True
                self.align_on_arrival_pending[agent] = self.arrival_target_yaw.get(agent) is not None
                self.navigator.set_active(agent, False)
                # 与 Navigator 进度一致，避免下一帧 _resolve_target 指向已过的路点导致倒退
                if wps:
                    self.robot_waypoint_indices[agent] = min(
                        self.navigator.get_index(agent), len(wps) - 1
                    )
            elif is_last:
                if self.is_patrolling.get(agent, False):
                    # 巡逻模式：到达终点后自动回绕，不进入等待状态
                    self.patrol_loop_index[agent] = (self.patrol_loop_index.get(agent, 0) + 1) % len(wps)
                    self.robot_waypoint_indices[agent] = 0
                    self.arrived_flags[agent] = False
                    print(f"[巡逻] {agent} 完成一轮巡逻，回绕至起点")
                else:
                    if not self.arrived_flags[agent]:
                        print(f"✅ {agent} 到达任务目标点，等待新指令")
                        self.events.append(f"{agent}_nav_arrived")
                    self.arrived_flags[agent] = True
                    self.waiting_for_task[agent] = True
                    self.align_on_arrival_pending[agent] = self.arrival_target_yaw.get(agent) is not None
            elif not self.arrived_flags[agent]:
                print(f"✅ {agent} 到达路径点")
                self.arrived_flags[agent] = True
                self.robot_waypoint_indices[agent] = idx + 1
                self.arrived_flags[agent] = False
        else:
            key5_val = KEY5_TARGET_POSITIONS.get(agent)
            if key5_val is not None and has_key5_next:
                self.key5_segment_indices[agent] = key5_seg_idx + 1
                if not self.arrived_flags[agent]:
                    print(f"✅ {agent} 到达 Key5 第 {key5_seg_idx+1} 段，前往下一段")
                self.arrived_flags[agent] = False
            else:
                if not self.arrived_flags[agent]:
                    print(f"✅ {agent} 到达 Key5 目标点")
                    if agent == "scout_1":
                        self.events.append("scout1_key5_arrived")
                self.arrived_flags[agent] = True
                self.waiting_for_task[agent] = True
                self.align_on_arrival_pending[agent] = self.arrival_target_yaw.get(agent) is not None

    @staticmethod
    def _compute_final_approach_yaw(waypoints) -> Optional[float]:
        """根据最终一段路径方向计算到点后应对齐的朝向。"""
        if not waypoints or len(waypoints) < 2:
            return None
        x0, y0 = waypoints[-2][0], waypoints[-2][1]
        x1, y1 = waypoints[-1][0], waypoints[-1][1]
        dx, dy = float(x1) - float(x0), float(y1) - float(y0)
        if math.hypot(dx, dy) < 1e-4:
            return None
        return math.atan2(dy, dx)

    # ── 目标解析 ──────────────────────────────────────────────────────────────

    def _resolve_target(self, agent, curr_pos):
        """返回 (target_xyz, arrival_dist, has_key5_next, key5_seg_idx)。"""
        idx = self.robot_waypoint_indices[agent]
        wps = self.robot_waypoints[agent]
        arrival = SCOUT1_ARRIVAL_DIST if agent == "scout_1" else ARRIVAL_DIST
        has_next = False
        seg_idx = 0

        if self.key5_active:
            k5 = KEY5_TARGET_POSITIONS.get(agent)
            if k5 is not None:
                if isinstance(k5, list) and len(k5) > 0:
                    seg_idx = min(self.key5_segment_indices.get(agent, 0), len(k5) - 1)
                    self.key5_segment_indices[agent] = seg_idx
                    has_next = seg_idx < len(k5) - 1
                    return k5[seg_idx], arrival, has_next, seg_idx
                elif isinstance(k5, tuple) and len(k5) == 3:
                    return k5, arrival, False, 0
            return wps[idx], arrival, False, 0

        return wps[idx], arrival, False, 0

    # ── 停止模式 ──────────────────────────────────────────────────────────────

    def _stopped_actions(self):
        actions = {}
        for agent in self.possible_agents:
            if agent not in self.base_env.scene.articulations:
                continue
            self.last_cmd_vel[agent][:] = 0
            self.robot_commands[agent][:] = 0
            actions[agent] = self.robot_commands[agent]
            if agent in self.goal_controlled_robots and hasattr(self.base_env, "set_command"):
                robot = self.base_env.scene.articulations[agent]
                self.base_env.set_command(agent, "goal_position", robot.data.root_pos_w[0].unsqueeze(0))
        return actions

    # ── Dashboard 目标命令 ────────────────────────────────────────────────────

    def handle_dashboard_goals(
        self,
        goals,
        active_rescue_robot: Optional[str] = None,
        *,
        straight_line: bool = False,
    ):
        """人机界面目标最高优先级：覆盖当前任务。

        ``straight_line=True`` 时绕过 RRT/A* 全局规划，直接用「当前位置 → 目标」两点作为
        waypoints；适用于人机协作组（EMOS / OpenClaw）测试员通过鼠标或指环手动下发的导航点，
        反映人工接管场景下机器人按操作员意图直线移动的行为。
        ``straight_line=False`` 时走原有的 ``interrupt_patrol_with_task`` 路径（含规划避障）。
        """
        for cmd in goals:
            name = cmd.get("robot", "")
            gx, gy = cmd.get("x", 0.0), cmd.get("y", 0.0)
            if name not in self.possible_agents:
                print(f"[Dashboard→目标] 未知机器人 '{name}'，忽略。")
                continue
            self.hold_position.discard(name)
            try:
                gx_f, gy_f = float(gx), float(gy)
                if active_rescue_robot and name == active_rescue_robot:
                    print(f"[Dashboard→目标] {name} 正在执行救援，允许人工目标覆盖导航。")
                # 仅保留 1 个人机界面目标：新目标覆盖旧目标（含当前正在执行的 manual）。
                q = self.task_queues.get(name)
                cur = q.peek() if q and not q.is_empty() else None
                if (
                    cur is not None
                    and getattr(cur, "task_type", "") == "manual"
                    and abs(float(cur.target_xy[0]) - gx_f) < 1e-3
                    and abs(float(cur.target_xy[1]) - gy_f) < 1e-3
                ):
                    print(f"[Dashboard→目标] {name} 目标未变化，忽略重复下发。")
                    continue
                # 人机接管后，该机器人默认不再自动恢复巡逻，直到显式接收下一条任务。
                self.suppress_patrol_agents.add(name)
                self.manual_override_agents.add(name)
                # 若 carter_1 处于障碍阻塞暂停，人工目标应强制抢占该暂停状态。
                if name == "carter_1":
                    self.carter1_obstacle_pause = False

                if straight_line:
                    self._dispatch_dashboard_goal_straight_line(name, gx_f, gy_f)
                    print(
                        f"[Dashboard→目标] {name} → ({gx_f:.2f}, {gy_f:.2f})（人机协作直线，跳过规划）"
                    )
                    continue

                manual = RobotTask(
                    task_id=f"dashboard_{name}_{int(time.time() * 1000)}",
                    task_type="manual",
                    subtask_name="人机界面目标",
                    subtask_colour="",
                    target_xy=(gx_f, gy_f),
                    waypoints=[],
                    priority=1,
                )
                # 使用既有任务派发逻辑（含 RRT/A* 规划与避障），而不是直线覆盖路径。
                self.interrupt_patrol_with_task(name, manual, priority=False)
                print(f"[Dashboard→目标] {name} → ({gx_f:.2f}, {gy_f:.2f})（最高优先级，规划避障）")
            except Exception as e:
                print(f"[Dashboard→目标] 处理异常：{e}")

    def _dispatch_dashboard_goal_straight_line(
        self, robot_name: str, gx: float, gy: float
    ) -> None:
        """直线导航：直接以「当前位置 → 目标」两点作为路径，不调全局规划。

        与 manual_baseline._dispatch_straight_line 的写状态方式一致：navigator.paths/indices
        与 nav.robot_waypoints/indices 同步写入两点，task_queues 推入 ``task_type="manual"``
        的 RobotTask；上层 compute_actions 会按 waypoints 直接驱动机器人 P 控制到目标。
        """
        pos = get_robot_pos(self.base_env, robot_name)
        sx, sy = float(pos[0]), float(pos[1])
        heading = math.atan2(gy - sy, gx - sx)
        wps = [(sx, sy, heading), (gx, gy, heading)]

        self.navigator.paths[robot_name] = wps
        self.navigator.indices[robot_name] = 0
        self.navigator.active[robot_name] = False
        self.robot_waypoints[robot_name] = wps
        self.robot_waypoint_indices[robot_name] = 0
        self.arrived_flags[robot_name] = False
        self.waiting_for_task[robot_name] = False
        self.is_patrolling[robot_name] = False
        self.arrival_target_yaw[robot_name] = heading
        self.align_on_arrival_pending[robot_name] = False

        task = RobotTask(
            task_id=f"dashboard_straight_{robot_name}_{int(time.time() * 1000)}",
            task_type="manual",
            subtask_name="人机界面目标（直线）",
            subtask_colour="",
            target_xy=(gx, gy),
            waypoints=list(wps),
            priority=1,
        )
        tq = self.task_queues.get(robot_name)
        if tq is not None:
            tq.clear()
            tq.push_back(task)

    # ── EMOS 讨论结果派遣 ─────────────────────────────────────────────────────

    def dispatch_emos_result(self, result):
        """将 EMOS 讨论结果转化为各机器人导航路径。返回派遣计数。"""
        count = 0
        for rname, rtask in result.items():
            if rname not in self.base_env.scene.articulations:
                print(f"  ⚠️  {rname}: 不在仿真场景中，跳过")
                continue
            rp = get_robot_pos(self.base_env, rname)
            start_xy = (rp[0], rp[1])
            goal_xy = rtask.target_xy
            print(f"\n  🤖 {rname}")
            print(f"     任务: {rtask.subtask_name}")
            print(f"     描述: {rtask.subtask_desc}")
            print(f"     当前位置: ({start_xy[0]:.1f}, {start_xy[1]:.1f})")
            print(f"     目标位置: ({goal_xy[0]:.1f}, {goal_xy[1]:.1f})")
            wps = self.navigator.set_path(rname, start_xy, goal_xy, waypoint_step=NAV_WAYPOINT_STEP)
            if wps:
                self.robot_waypoints[rname] = wps
                self.robot_waypoint_indices[rname] = 0
                self.arrived_flags[rname] = False
                self.waiting_for_task[rname] = False
                self.arrival_target_yaw[rname] = self._compute_final_approach_yaw(wps)
                self.align_on_arrival_pending[rname] = False
                print(f"     导航: ✅ 路径已规划（{len(wps)} 个路点）")
                count += 1
            else:
                print(f"     导航: ❌ 路径规划失败")
        return count

    # ── EMOS 重分配 ───────────────────────────────────────────────────────────

    def handle_reassignments(self, reassign_cmds):
        """HTML 确认的任务分配：写入任务队列并规划路径（含 Carter 差速 goal / 速度模式）。"""
        for cmd in reassign_cmds:
            assignments = cmd.get("assignments", {})
            if not assignments:
                continue
            print(f"[EMOS 重分配] 收到新的任务分配方案：")
            for rn, rt in assignments.items():
                xy = rt.get("target_xy")
                if rn not in self.possible_agents or not xy or len(xy) < 2:
                    continue
                gx, gy = float(xy[0]), float(xy[1])
                print(f"  {rn} → {rt.get('subtask', '?')} at ({gx:.1f}, {gy:.1f})")
                try:
                    self.hold_position.discard(rn)
                    task = RobotTask(
                        task_id=f"emos_reassign_{rn}_{int(time.time() * 1000)}",
                        task_type="emos",
                        subtask_name=str(rt.get("subtask", "EMOS重分配")),
                        subtask_colour=str(rt.get("colour", "")),
                        target_xy=(gx, gy),
                        waypoints=[],
                        priority=0,
                    )
                    self.interrupt_patrol_with_task(rn, task, priority=False)
                except Exception as e:
                    print(f"  [EMOS 重分配] {rn} 派遣失败: {e}")

    # ── 卡住检测（节流调用）─────────────────────────────────────────────────────

    def check_stuck(self):
        now = time.time()
        for sn in self.possible_agents:
            _tq_stuck = self.task_queues.get(sn)
            _top_stuck = _tq_stuck.peek() if _tq_stuck and not _tq_stuck.is_empty() else None
            _is_rescue_task = (_top_stuck is not None and getattr(_top_stuck, "task_type", "") == "rescue")
            _is_fire_task = self._is_fire_proximity_task(_top_stuck)

            if sn in self.hold_position:
                self._stuck_last_pos.pop(sn, None)
                self._stuck_last_time.pop(sn, None)
                self._stuck_goal_dist.pop(sn, None)
                self._stuck_goal_dist_time.pop(sn, None)
                continue

            if not self.navigator.is_active(sn):
                self._stuck_last_pos.pop(sn, None)
                self._stuck_last_time.pop(sn, None)
                self._stuck_goal_dist.pop(sn, None)
                self._stuck_goal_dist_time.pop(sn, None)
                continue

            if sn == "carter_1" and self.carter1_obstacle_pause:
                self._stuck_last_pos.pop(sn, None)
                self._stuck_last_time.pop(sn, None)
                continue
            if self.waiting_for_task.get(sn, False):
                self._stuck_last_pos.pop(sn, None)
                self._stuck_last_time.pop(sn, None)
                self._stuck_goal_dist.pop(sn, None)
                self._stuck_goal_dist_time.pop(sn, None)
                continue
            if self.arrived_flags.get(sn, False):
                self._stuck_last_pos.pop(sn, None)
                self._stuck_last_time.pop(sn, None)
                continue
            try:
                pos = get_robot_pos(self.base_env, sn)
                sxy = (pos[0], pos[1])
            except Exception:
                continue

            # 获取目标位置（用于后续振荡检测和重规划）
            _tq = self.task_queues.get(sn)
            _cur = _tq.peek() if _tq and not _tq.is_empty() else None
            if _cur:
                sfxy = _cur.target_xy
            else:
                swps = self.navigator.paths.get(sn, [])
                if not swps:
                    continue
                sfinal = swps[-1]
                sfxy = (float(sfinal[0]), float(sfinal[1]))
            _dist_to_goal = math.hypot(sxy[0] - sfxy[0], sxy[1] - sfxy[1])
            if _dist_to_goal < 0.5:
                self._stuck_goal_dist.pop(sn, None)
                self._stuck_goal_dist_time.pop(sn, None)
                continue

            # ── 振荡检测：追踪目标距离是否持续无进展 ──
            _is_oscillation_stuck = False
            if sn not in self._stuck_goal_dist:
                self._stuck_goal_dist[sn] = _dist_to_goal
                self._stuck_goal_dist_time[sn] = now
            elif _dist_to_goal < self._stuck_goal_dist[sn] - 0.5:
                self._stuck_goal_dist[sn] = _dist_to_goal
                self._stuck_goal_dist_time[sn] = now
            else:
                # 放宽振荡卡死判定窗口，避免长路径/窄通道中被过早判定为“无进展”。
                _osc_timeout = 45.0 if _is_rescue_task else 35.0
                _osc_elapsed = now - self._stuck_goal_dist_time.get(sn, now)
                if _osc_elapsed > _osc_timeout:
                    _is_oscillation_stuck = True

            # ── 位置卡死检测 ──
            if sn not in self._stuck_last_pos:
                self._stuck_last_pos[sn] = sxy
                self._stuck_last_time[sn] = now
                if not _is_oscillation_stuck:
                    continue

            sdist = math.hypot(sxy[0] - self._stuck_last_pos[sn][0],
                               sxy[1] - self._stuck_last_pos[sn][1])

            _position_stuck = False
            if sdist > STUCK_THRESHOLD_DIST:
                self._stuck_last_pos[sn] = sxy
                self._stuck_last_time[sn] = now
                if not _is_oscillation_stuck:
                    continue
            else:
                _effective_stuck_timeout = STUCK_TIMEOUT_S * 2.0 if _is_rescue_task else STUCK_TIMEOUT_S
                _effective_replan_cd = STUCK_REPLAN_CD_S * 1.5 if _is_rescue_task else STUCK_REPLAN_CD_S
                if now - self._stuck_last_time[sn] < _effective_stuck_timeout:
                    if not _is_oscillation_stuck:
                        continue
                _position_stuck = True

            # 重规划冷却
            _effective_replan_cd = STUCK_REPLAN_CD_S * 1.5 if _is_rescue_task else STUCK_REPLAN_CD_S
            if now - self._stuck_replan_cd.get(sn, 0) < _effective_replan_cd:
                continue

            step_sz = NAV_WAYPOINT_STEP
            _task_tag = f"[{_top_stuck.task_type}:{_top_stuck.subtask_name}]" if _top_stuck else ""
            _straight_dist = math.hypot(sxy[0] - sfxy[0], sxy[1] - sfxy[1])

            _prev_consec_pos = self._stuck_consec_pos.get(sn)
            if _prev_consec_pos and math.hypot(sxy[0] - _prev_consec_pos[0], sxy[1] - _prev_consec_pos[1]) < 2.0:
                self._stuck_consec_count[sn] = self._stuck_consec_count.get(sn, 0) + 1
            else:
                self._stuck_consec_count[sn] = 1
                self._stuck_consec_pos[sn] = sxy
            _consec = self._stuck_consec_count[sn]

            _stuck_reason = "振荡卡死（移动但无进展）" if (_is_oscillation_stuck and not _position_stuck) else "位置卡死"
            _effective_stuck_timeout_display = STUCK_TIMEOUT_S * 2.0 if _is_rescue_task else STUCK_TIMEOUT_S
            print(f"🔄 [StuckDetector] {sn} {_stuck_reason} {_task_tag}（连续第{_consec}次），"
                  f"当前位置 ({sxy[0]:.2f}, {sxy[1]:.2f})，d_goal={_dist_to_goal:.1f}m，"
                  f"触发重规划 → ({sfxy[0]:.1f}, {sfxy[1]:.1f})（直线距离 {_straight_dist:.1f}m）")

            new_wps = None

            # M20 连续卡死：分级处理
            if sn.startswith("m20") and _consec >= 3:
                if _consec >= 6:
                    # 6+ 次：垂直于目标方向的大幅绕路
                    _direct_angle = math.atan2(sfxy[1] - sxy[1], sfxy[0] - sxy[0])
                    _perp_offset = 3.0
                    _perp_angle = _direct_angle + math.pi / 2
                    _mid_x = (sxy[0] + sfxy[0]) / 2 + _perp_offset * math.cos(_perp_angle)
                    _mid_y = (sxy[1] + sfxy[1]) / 2 + _perp_offset * math.sin(_perp_angle)
                    _fwd_th = math.atan2(sfxy[1] - _mid_y, sfxy[0] - _mid_x)
                    new_wps = [
                        (_mid_x, _mid_y, _fwd_th),
                        (sfxy[0], sfxy[1], _fwd_th),
                    ]
                    if self._validate_manual_recovery_path(
                        new_wps,
                        sn,
                        label="卡死垂直绕路",
                        clearance_cells=0,
                    ):
                        self.navigator.paths[sn] = new_wps
                        self.navigator.indices[sn] = 0
                        self.navigator.active[sn] = True
                        self.robot_waypoints[sn] = new_wps
                        self.robot_waypoint_indices[sn] = 0
                        self.arrived_flags[sn] = False
                        self.waiting_for_task[sn] = False
                        self._stuck_consec_count[sn] = max(0, _consec - 3)
                        print(f"   [StuckDetector] ↪ {sn} 连续卡死{_consec}次，尝试垂直绕路 "
                              f"经 ({_mid_x:.1f},{_mid_y:.1f}) → ({sfxy[0]:.1f},{sfxy[1]:.1f})")
                    else:
                        print(f"   [StuckDetector] ⚠️ {sn} 垂直绕路候选穿障，回退到异步重规划")
                        new_wps = None
                else:
                    # 3-5 次：后退 0.8m 再前进
                    _back_dir = math.atan2(sxy[1] - sfxy[1], sxy[0] - sfxy[0])
                    _back_dist = 0.8
                    _back_x = sxy[0] + _back_dist * math.cos(_back_dir)
                    _back_y = sxy[1] + _back_dist * math.sin(_back_dir)
                    _fwd_th = math.atan2(sfxy[1] - sxy[1], sfxy[0] - sxy[0])
                    new_wps = [
                        (_back_x, _back_y, _back_dir),
                        (sxy[0], sxy[1], _fwd_th),
                        (sfxy[0], sfxy[1], _fwd_th),
                    ]
                    if self._validate_manual_recovery_path(
                        new_wps,
                        sn,
                        label="卡死后退重试",
                        clearance_cells=0,
                    ):
                        self.navigator.paths[sn] = new_wps
                        self.navigator.indices[sn] = 0
                        self.navigator.active[sn] = True
                        self.robot_waypoints[sn] = new_wps
                        self.robot_waypoint_indices[sn] = 0
                        self.arrived_flags[sn] = False
                        self.waiting_for_task[sn] = False
                        print(f"   [StuckDetector] ⏪ {sn} 连续卡死{_consec}次，先后退 {_back_dist}m 再重试")
                    else:
                        print(f"   [StuckDetector] ⚠️ {sn} 后退重试候选穿障，回退到异步重规划")
                        new_wps = None
                if new_wps:
                    # 振荡检测也需重置
                    self._stuck_goal_dist[sn] = _dist_to_goal
                    self._stuck_goal_dist_time[sn] = now
                    self._stuck_replan_cd[sn] = now
                    self._stuck_last_pos[sn] = sxy
                    self._stuck_last_time[sn] = now
                    continue

            # 振荡卡死时也重置进度追踪
            if _is_oscillation_stuck:
                self._stuck_goal_dist[sn] = _dist_to_goal
                self._stuck_goal_dist_time[sn] = now

            _detour_wps = self._carter_step_detour(sxy, sfxy, sn)
            if _detour_wps and self._validate_detour_path(_detour_wps, sn):
                new_wps = _detour_wps
                self.navigator.paths[sn] = new_wps
                self.navigator.indices[sn] = 0
                self.navigator.active[sn] = True
            else:
                # 不在 stuck 检测里同步重规划，改为仅投递异步请求。
                _queued_task = _top_stuck
                if _queued_task is None:
                    _queued_task = RobotTask(
                        task_id=f"stuck_replan_{sn}_{int(now * 1000)}",
                        task_type="patrol",
                        subtask_name="卡死重规划",
                        subtask_colour="",
                        target_xy=(float(sfxy[0]), float(sfxy[1])),
                        waypoints=[],
                        priority=0,
                    )
                self.waiting_for_task[sn] = True
                if _is_rescue_task or _is_fire_task:
                    self._set_hold_position(sn, "stuck_async_pending")
                _queued = self._queue_async_plan_request(
                    sn, _queued_task, reason="stuck_detector", min_interval_s=_effective_replan_cd
                )
                if _queued:
                    print(f"   [StuckDetector] {sn} 已提交异步重规划请求")
                else:
                    print(f"   [StuckDetector] {sn} 异步重规划请求已在队列/冷却中，跳过重复提交")
                new_wps = None
            if new_wps:
                _path_len = sum(
                    math.hypot(new_wps[i+1][0] - new_wps[i][0], new_wps[i+1][1] - new_wps[i][1])
                    for i in range(len(new_wps) - 1)
                ) if len(new_wps) > 1 else 0.0
                _detour = _path_len / max(_straight_dist, 0.01)
                print(f"   [StuckDetector] {sn} 新路径 {len(new_wps)} 个路径点"
                      f"（路径长 {_path_len:.1f}m，绕路比 {_detour:.1f}x）")
                self.robot_waypoints[sn] = new_wps
                self.robot_waypoint_indices[sn] = 0
                self.arrived_flags[sn] = False
                self.waiting_for_task[sn] = False
            self._stuck_replan_cd[sn] = now
            self._stuck_last_pos[sn] = sxy
            self._stuck_last_time[sn] = now

    # ── 环境重置 ──────────────────────────────────────────────────────────────

    def reset(self):
        for agent in self.possible_agents:
            if agent in self.robot_waypoints:
                self.robot_waypoint_indices[agent] = 0
        self._carter_start_time = None
        self._carter_delay_printed = {}
        self.key5_active = False
        self.key5_segment_indices.clear()
        self.all_stopped = False
        self.events.clear()
        self.hold_position.clear()
        self.suppress_patrol_after_emos = False
        self.suppress_patrol_agents.clear()
        self.manual_override_agents.clear()
        for dq in self.deferred_system_tasks.values():
            dq.clear()
        self.suppress_patrol_exempt.clear()
        self.carter1_obstacle_pause = False
        self.waiting_for_task = {a: True for a in self.possible_agents}
        self.arrival_target_yaw = {a: None for a in self.possible_agents}
        self.align_on_arrival_pending = {a: False for a in self.possible_agents}
        self._pending_plan = None
        self._rescue_retry_last_ts.clear()
        self._rescue_retry_count.clear()
        self._last_plan_failed_agents.clear()
        self._last_batch_plan_report = None
        self._last_known_fire_xy = None
        self._recent_fire_paths.clear()

        for mn in ("m20_1", "m20_2"):
            try:
                if mn in self.base_env.scene.articulations:
                    p = self.base_env.scene.articulations[mn].data.root_pos_w[0].cpu().numpy()
                    self.robot_waypoints[mn] = [(float(p[0]), float(p[1]), float(p[2]))]
                    self.robot_waypoint_indices[mn] = 0
            except Exception:
                pass

    # ── 数据服务器数据获取 ────────────────────────────────────────────────────

    def get_robot_data_for_server(self):
        """返回每个机器人的数据字典，供 data_server.update_robot 使用。"""
        data = {}
        for name in self.possible_agents:
            if name not in self.base_env.scene.articulations:
                continue
            pos = get_robot_pos(self.base_env, name)
            idx = (self.navigator.get_index(name)
                   if self.navigator.is_active(name)
                   else self.robot_waypoint_indices.get(name, 0))
            data[name] = {
                "pos": pos,
                "waypoints": self.robot_waypoints.get(name, []),
                "current_idx": idx,
                "rrt_active": self.navigator.is_active(name),
                "status": "stopped" if self.all_stopped else "active",
                "nav_reason": self._robot_nav_reason(name),
            }
        return data

    def _robot_nav_reason(self, name: str) -> str:
        if self.all_stopped:
            return "all_stopped"
        if name == "carter_1" and self.carter1_obstacle_pause:
            return "obstacle_pause"
        if name in self.hold_position:
            return "hold"
        if name in self._last_plan_failed_agents:
            return "plan_failed_retrying"
        if self.is_patrolling.get(name, False):
            return "patrolling"
        if self.navigator.is_active(name):
            return "navigating"
        if self.waiting_for_task.get(name, False):
            return "waiting_task"
        return "unknown"
