"""
EAI-Simulator 数据接口服务器

提供 HTTP REST API 供前端 Dashboard 实时查询：
  - 主机系统状态  (CPU / 内存 / GPU / 磁盘 / 网络)
  - 机器人位置与路径信息
  - 仿真运行状态
  - 工厂地图图片
  - 接收并转发导航目标点命令

端口: 8767 (HTTP)
CORS: 允许所有来源

API 端点:
  GET  /api/status              → 主机系统指标
  GET  /api/robots              → 所有机器人状态
  GET  /api/sim                 → 仿真状态
  GET  /api/hazards             → 当前危险柱列表
  GET  /api/map                 → 工厂地图 PNG（字节流）
  GET  /api/map/meta            → 地图元信息（坐标系参数）
  POST /api/robots/{name}/goal  → 为指定机器人设置新导航终点
  GET  /api/llm_presets         → EMOS 讨论用 LLM 预设列表与当前选中 id
  POST /api/emos/llm            → 设置 EMOS 讨论 LLM 预设 {"preset": "<id>"}
  POST /api/voice/command       → 确认/取消待确认语音命令 {"action": "confirm"|"cancel", "command_id": "..."}
"""

from __future__ import annotations

import json
import logging
import os
import queue
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

from demo.fire_rescue.runtime.llm_presets import (
    DEFAULT_EMOS_LLM_PRESET,
    EMOS_LLM_PRESETS,
    list_presets_for_api,
    preset_to_group_discussion_kwargs,
)

# ── 共享状态（主循环写入，HTTP 服务器读取）────────────────────────────────────
_state: Dict[str, Any] = {
    # 仿真状态
    "sim": {
        "step": 0,
        "fps": 0.0,
        "runtime_s": 0.0,
         "virtual_time_s": 0.0,
        "running": False,
        "mode": "unknown",   # "hazard" | "robots"
        "result": "running",  # running | success | fail
        "fail_reason": "",
        # 轮次结束弹窗（前端根据 seq 递增触发 alert；与 update_sim 合并时保留）
        "trial_popup": {
            "seq": 0,
            "outcome": "",  # success | fail | end
            "sim_s": 0.0,
            "wall_s": 0.0,
            "delay_s": 0.0,
            "hazard_label": "",
        },
    },
    # 机器人状态列表
    "robots": {},
    # 危险柱状态
    "hazards": {},
    # EMOS 讨论状态
    "emos": {
        "phase": "idle",      # idle | running | done | error
        "hazard_id": 0,
        "assignments": {},    # robot_name -> {subtask, target_xy, colour}
    },
    # 聊天消息列表（EMOS 讨论过程 + 系统事件）
    "chat_messages": [],      # [{type, sender, text, ts}, ...] 最多保留 200 条
    # 障碍物救援状态
    "rescue": None,           # Dict or None
    # OpenClaw 事件流：执行层只上报事实，下一步规划仍由 OpenClaw 决定。
    "openclaw_events": [],
    "openclaw_event_seq": 0,
    # 指环语音命令状态（只读展示；执行权威状态在 voice_command_control.py）
    "voice_command": {
        "phase": "idle",
        "ring_connected": False,
        "current": None,
        "history": [],
        "updated_at": 0.0,
    },
    # 最后更新时间
    "updated_at": 0.0,
}
_state_lock = threading.Lock()

_OPENCLAW_TASK_CATALOG: Dict[str, Dict[str, Any]] = {
    "red": {
        "name": "火源侦查",
        "description": "前往火源位置附近进行侦查，适合四足机器人。",
    },
    "green": {
        "name": "打开救援通道",
        "description": "前往救援通道按钮处，使用具备机械臂/按钮操作能力的机器人打开通道。",
        "target_xy": [10.58, 1.0],
        "requires": ["mechanical_arm", "button_press"],
        "forbidden_without": ["mechanical_arm"],
    },
    "blue": {
        "name": "数据采集",
        "description": "前往现场采集图像与传感数据并回传。",
    },
    "yellow": {
        "name": "灭火器运送",
        "description": "夹取灭火器并运送到火源附近，适合 M20 系列机器人。",
        "target_xy": [1.77, -9.38],
    },
    "obstacle": {
        "name": "障碍物清除",
        "description": "突发障碍阻塞时，选择救援机器人搬运或清理障碍物。",
    },
    "fire_delivery": {
        "name": "灭火器送往火源",
        "description": "收到 yellow/extinguisher_grabbed 事件后，由 OpenClaw 决定是否将携带灭火器的机器人派往火源附近。",
        "target_source": "hazards 或 openclaw_events.detail.hazard_xy",
    },
    "post_green": {
        "name": "救援通道开启后的后续任务",
        "description": "收到 green/rescue_channel_opened 事件后，由 OpenClaw 决定该机器人下一步去向。",
        "target_source": "hazards 或 openclaw_events.detail.hazard_xy",
    },
}

_OPENCLAW_CONSTRAINTS = [
    "green 打开救援通道任务需要机械臂/按钮按压能力；不得分配给无机械臂的 carter_1。",
    "yellow 灭火器运送任务优先由 M20 系列机器人执行。",
    "OpenClaw 组中，yellow 抓取完成和 green 按钮完成后，仿真器只通过 openclaw_events 上报事实，不自动派发后续去火源/集结任务。",
    "OpenClaw 输出任务级决策，仿真器负责导航、机械臂和物理执行。",
]

# ── 目标命令队列（HTTP → 主循环）─────────────────────────────────────────────
# 每个条目: {"robot": str, "x": float, "y": float}
_goal_queue: queue.Queue = queue.Queue(maxsize=32)

# 服务器线程
_server_thread: Optional[threading.Thread] = None
_httpd: Optional[HTTPServer] = None
_PORT: int = 8767
_dashboard_html_path: str = ""

# 地图文件路径（由主脚本设置）
_map_png_path: str = ""
_map_meta: Dict[str, Any] = {}

# 启动时间（计算 uptime）
_start_time: float = time.time()

# 缓存系统状态（后台线程定期更新，避免每次 HTTP 请求都调用 psutil/pynvml）
_cached_status: Dict[str, Any] = {}
_cached_status_lock = threading.Lock()

# EMOS 任务重分配队列
_reassign_queue: queue.Queue = queue.Queue(maxsize=16)

# 障碍物救援审批队列
_rescue_approval_queue: queue.Queue = queue.Queue(maxsize=16)

# EMOS 重规划请求队列（Dashboard 重规划按钮触发）
_replan_queue: queue.Queue = queue.Queue(maxsize=4)
_REPLAN_POST_DEBOUNCE_S = 45.0
_replan_last_post_ts: float = 0.0

# 指环语音命令确认/取消队列（Dashboard → 主循环 → VoiceCommandController）
_voice_action_queue: queue.Queue = queue.Queue(maxsize=16)

# 纯人工对照组命令队列
_manual_action_queue: queue.Queue = queue.Queue(maxsize=32)
_manual_hazard_queue: queue.Queue = queue.Queue(maxsize=16)
_manual_top_camera_queue: queue.Queue = queue.Queue(maxsize=16)
_manual_task_end_queue: queue.Queue = queue.Queue(maxsize=16)

# ── 机械臂遥操作状态（由前端 HTML 设置，主循环读取）────────────────────────────
_arm_teleop_enabled: bool = False
_arm_teleop_keys_down: set[str] = set()
# 当前通过 HTML 控制的机械臂所属机器人名称（如 "scout_1"、"m20_1" 等）。
_arm_teleop_robot: str = "scout_1"
_arm_teleop_lock = threading.Lock()

# EMOS 讨论所用 LLM 预设（与 emos_llm_presets 同步）
_emos_llm_preset_lock = threading.Lock()
_emos_llm_preset_id: str = DEFAULT_EMOS_LLM_PRESET


def get_emos_llm_preset_id() -> str:
    with _emos_llm_preset_lock:
        return _emos_llm_preset_id


def set_emos_llm_preset_id(preset_id: str) -> str:
    """Return the resolved preset id (falls back to default if unknown)."""
    global _emos_llm_preset_id
    pid = preset_id if preset_id in EMOS_LLM_PRESETS else DEFAULT_EMOS_LLM_PRESET
    with _emos_llm_preset_lock:
        _emos_llm_preset_id = pid
    return pid


def get_group_discussion_llm_kwargs() -> Dict[str, Any]:
    """Supplies ``llm_*`` kwargs for :func:`algorithm.emos.brain.MultiLLM_discussion.group_discussion`."""
    with _emos_llm_preset_lock:
        pid = _emos_llm_preset_id
    return preset_to_group_discussion_kwargs(pid)


# ── 更新函数（供主循环调用）───0───────────────────────────────────────────────

def update_sim(
    step: int,
    fps: float,
    runtime_s: float,
    running: bool,
    virtual_time_s: Optional[float] = None,
    mode: str = "robots",
    result: Optional[str] = None,
    fail_reason: Optional[str] = None,
    manual_task_status: Optional[Dict[str, Any]] = None,
    active_hazard_id: Optional[int] = None,
) -> None:
    with _state_lock:
        payload = {
            "step": step, "fps": fps, "runtime_s": runtime_s,
            "running": running, "mode": mode,
        }
        if virtual_time_s is not None:
            payload["virtual_time_s"] = float(virtual_time_s)
        if result is not None:
            payload["result"] = str(result)
        if fail_reason is not None:
            payload["fail_reason"] = str(fail_reason)
        if manual_task_status is not None:
            payload["manual_task_status"] = manual_task_status
        if active_hazard_id is not None:
            payload["active_hazard_id"] = int(active_hazard_id)
        tp_keep = _state["sim"].get("trial_popup")
        _state["sim"].update(payload)
        if isinstance(tp_keep, dict):
            _state["sim"]["trial_popup"] = tp_keep
        _state["updated_at"] = time.time()


def set_trial_end_popup(
    outcome: str,
    sim_s: float,
    wall_s: float,
    hazard_label: str = "",
) -> None:
    """通知 Dashboard 弹出本轮任务时长（真实墙钟秒）。前端轮询 /api/all 根据 trial_popup.seq 递增显示一次。

    sim_s / wall_s / delay_s 三个字段保留是为了和旧客户端兼容；当前所有调用方传入的
    sim_s 已是 _sim_time_since_fire_s（即 time.time() - _fire_start_ts），与 wall_s
    几乎相同，前端只展示 sim_s。
    """
    delay_s = float(wall_s) - float(sim_s)
    with _state_lock:
        tp = dict(_state["sim"].get("trial_popup") or {})
        seq = int(tp.get("seq", 0)) + 1
        _state["sim"]["trial_popup"] = {
            "seq": seq,
            "outcome": str(outcome),
            "sim_s": float(sim_s),
            "wall_s": float(wall_s),
            "delay_s": float(delay_s),
            "hazard_label": str(hazard_label or ""),
        }
        _state["updated_at"] = time.time()


def update_robot(
    name: str,
    pos_x: float, pos_y: float, pos_z: float,
    waypoints: list,
    current_idx: int,
    rrt_active: bool,
    status: str = "ok",
    nav_reason: str = "",
) -> None:
    with _state_lock:
        _state["robots"][name] = {
            "name": name,
            "pos_x": pos_x,
            "pos_y": pos_y,
            "pos_z": pos_z,
            "waypoints": [[float(wp[0]), float(wp[1])] for wp in waypoints],
            "current_idx": current_idx,
            "rrt_active": rrt_active,
            "status": status,
            "nav_reason": str(nav_reason or ""),
            "updated_at": time.time(),
            # 任务队列字段（由 update_robot_tasks 补充更新，此处提供默认值）
            "task_queue": _state["robots"].get(name, {}).get("task_queue", []),
            "current_task_index": _state["robots"].get(name, {}).get("current_task_index", 0),
            "is_patrolling": _state["robots"].get(name, {}).get("is_patrolling", False),
        }


def update_hazard(hid: int, pos: tuple, active: bool = True) -> None:
    with _state_lock:
        if active:
            _state["hazards"][str(hid)] = {
                "id": hid,
                "pos": {"x": float(pos[0]), "y": float(pos[1]), "z": float(pos[2])},
            }
        else:
            _state["hazards"].pop(str(hid), None)


def clear_hazards() -> None:
    with _state_lock:
        _state["hazards"].clear()


def update_emos(phase: str, hazard_id: int = 0, assignments: Optional[Dict[str, Any]] = None,
                merge: bool = False) -> None:
    """Update EMOS discussion state for the dashboard.

    Args:
        phase:       当前阶段 ('running' | 'done')
        hazard_id:   危险柱编号
        assignments: 机器人任务分配字典
        merge:       为 True 时将 assignments 合并到现有分配中（用于追加救援任务），
                     为 False 时完全替换（用于新一轮 EMOS 讨论结果）
    """
    with _state_lock:
        if merge and assignments:
            # 合并模式：保留已有分配，追加/更新新的分配
            existing = _state.get("emos", {}).get("assignments", {})
            merged = dict(existing)
            merged.update(assignments)
            _state["emos"] = {
                "phase": phase,
                "hazard_id": hazard_id,
                "assignments": merged,
            }
        else:
            _state["emos"] = {
                "phase": phase,
                "hazard_id": hazard_id,
                "assignments": assignments or {},
            }


def update_voice_command(payload: Dict[str, Any]) -> None:
    """Update read-only voice command state for the dashboard."""
    with _state_lock:
        current = dict(_state.get("voice_command") or {})
        current.update(payload or {})
        current["updated_at"] = time.time()
        _state["voice_command"] = current
        _state["updated_at"] = time.time()


_CHAT_MAX = 200
_OPENCLAW_EVENT_MAX = 100

def push_chat(msg_type: str, sender: str, text: str) -> None:
    """Push a chat message visible in the dashboard dialogue panel.

    msg_type: 'sys' | 'bot' | 'emos'
    sender:   display name, e.g. 'EMOS 系统', 'm20_1'
    text:     message body (may contain newlines, will be rendered as <br>)
    """
    with _state_lock:
        msgs = _state["chat_messages"]
        msgs.append({
            "type": msg_type,
            "sender": sender,
            "text": text,
            "ts": time.time(),
        })
        if len(msgs) > _CHAT_MAX:
            _state["chat_messages"] = msgs[-_CHAT_MAX:]


def push_openclaw_event(event_type: str, robot: str, task: str, detail: Optional[Dict[str, Any]] = None) -> None:
    """Append a structured execution event for OpenClaw to consume from /api/openclaw/context."""
    with _state_lock:
        events = _state["openclaw_events"]
        seq = int(_state.get("openclaw_event_seq", 0) or 0) + 1
        _state["openclaw_event_seq"] = seq
        events.append({
            "seq": seq,
            "type": str(event_type),
            "robot": str(robot),
            "task": str(task),
            "detail": dict(detail or {}),
            "ts": time.time(),
        })
        if len(events) > _OPENCLAW_EVENT_MAX:
            _state["openclaw_events"] = events[-_OPENCLAW_EVENT_MAX:]
        _state["updated_at"] = time.time()


def get_pending_goals() -> list:
    """从队列中取出所有待处理的目标命令并返回（主循环每帧调用）。"""
    cmds = []
    while True:
        try:
            cmds.append(_goal_queue.get_nowait())
        except queue.Empty:
            break
    return cmds


def get_pending_reassignments() -> list:
    """从队列中取出所有待处理的 EMOS 任务重分配。"""
    cmds = []
    while True:
        try:
            cmds.append(_reassign_queue.get_nowait())
        except queue.Empty:
            break
    return cmds


def get_pending_rescue_approvals() -> list:
    """从队列中取出所有待处理的障碍物救援审批。"""
    cmds = []
    while True:
        try:
            cmds.append(_rescue_approval_queue.get_nowait())
        except queue.Empty:
            break
    return cmds


def get_pending_replan_requests() -> bool:
    """检查是否有来自 Dashboard 的重规划请求，有则返回 True 并清空队列。"""
    found = False
    while True:
        try:
            _replan_queue.get_nowait()
            found = True
        except queue.Empty:
            break
    return found


def get_pending_voice_actions() -> list:
    """从队列中取出所有待处理的语音命令确认/取消动作。"""
    cmds = []
    while True:
        try:
            cmds.append(_voice_action_queue.get_nowait())
        except queue.Empty:
            break
    return cmds


def get_pending_manual_actions() -> list:
    """从队列中取出所有待处理的纯人工动作命令。"""
    cmds = []
    while True:
        try:
            cmds.append(_manual_action_queue.get_nowait())
        except queue.Empty:
            break
    return cmds


def get_pending_manual_hazards() -> list:
    """从队列中取出所有待处理的纯人工危险事件命令。"""
    cmds = []
    while True:
        try:
            cmds.append(_manual_hazard_queue.get_nowait())
        except queue.Empty:
            break
    return cmds


def get_pending_manual_top_camera_commands() -> list:
    """从队列中取出所有待处理的纯人工俯视相机调节命令。"""
    cmds = []
    while True:
        try:
            cmds.append(_manual_top_camera_queue.get_nowait())
        except queue.Empty:
            break
    return cmds


def get_pending_manual_task_end_commands() -> list:
    """从队列中取出所有待处理的纯人工任务结束命令。"""
    cmds = []
    while True:
        try:
            cmds.append(_manual_task_end_queue.get_nowait())
        except queue.Empty:
            break
    return cmds


def set_arm_teleop_enabled(enabled: bool) -> None:
    """由前端 HTML 控制是否启用 UR5 遥操作模式。"""
    global _arm_teleop_enabled
    with _arm_teleop_lock:
        _arm_teleop_enabled = bool(enabled)


def update_arm_teleop_keys(keys_down: list[str]) -> None:
    """由前端 HTML 上报当前按下的遥操作按键集合（'W','A','S','D','H','J','P'）。"""
    global _arm_teleop_keys_down
    norm = {str(k).upper() for k in keys_down if isinstance(k, str)}
    with _arm_teleop_lock:
        _arm_teleop_keys_down = norm


def get_arm_teleop_enabled() -> bool:
    """主循环查询当前是否启用 UR5 遥操作模式。"""
    with _arm_teleop_lock:
        return _arm_teleop_enabled


def get_arm_teleop_keys_down() -> set[str]:
    """主循环查询当前按下的遥操作按键集合（大写字母）。"""
    with _arm_teleop_lock:
        return set(_arm_teleop_keys_down)


def set_arm_teleop_robot(robot_name: str) -> None:
    """设置当前由 HTML 遥操作控制的机器人名称。"""
    global _arm_teleop_robot
    name = str(robot_name or "").strip()
    if not name:
        return
    with _arm_teleop_lock:
        _arm_teleop_robot = name


def get_arm_teleop_robot() -> str:
    """主循环查询当前遥操作控制的机器人名称。"""
    with _arm_teleop_lock:
        return _arm_teleop_robot


def update_rescue(rescue_data: Optional[Dict[str, Any]]) -> None:
    """Update obstacle rescue state for dashboard polling."""
    with _state_lock:
        _state["rescue"] = rescue_data


def update_robot_tasks(
    name: str,
    task_queue: list,
    current_task_index: int = 0,
    is_patrolling: bool = False,
) -> None:
    """补充更新机器人的任务队列状态（供 Dashboard 任务链显示）。

    Args:
        name:               机器人名称
        task_queue:         任务列表，每项为含 task_id/task_type/subtask_name/subtask_colour/target_xy 的字典
        current_task_index: 当前正在执行的任务索引
        is_patrolling:      是否处于巡逻状态
    """
    with _state_lock:
        if name not in _state["robots"]:
            return
        _state["robots"][name]["task_queue"] = task_queue
        _state["robots"][name]["current_task_index"] = current_task_index
        _state["robots"][name]["is_patrolling"] = is_patrolling


def _openclaw_robot_profiles() -> Dict[str, Any]:
    try:
        from demo.fire_rescue.scenario import build_factory_emos_agent_specs
    except Exception as exc:
        return {"error": str(exc), "profiles": {}}

    profiles: Dict[str, Any] = {}
    for name, spec in build_factory_emos_agent_specs().items():
        caps = [str(capability) for capability in spec.capabilities]
        has_ur5 = any(("UR5" in cap or "机械臂" in cap) for cap in caps)
        resume = {
            "robot_type": spec.robot_type,
            "capabilities": caps,
        }
        profiles[str(name)] = {
            "robot_type": spec.robot_type,
            "capabilities": caps,
            "has_mechanical_arm": bool(has_ur5),
            "can_press_button": bool(has_ur5),
            "can_execute_green": bool(has_ur5),
            "resume": resume,
            "preferred_task": spec.preferred_task,
        }
    return profiles


def _openclaw_context() -> Dict[str, Any]:
    with _cached_status_lock:
        cached_status = dict(_cached_status)
    with _state_lock:
        robots = {name: dict(data) for name, data in _state["robots"].items()}
        hazards = list(_state["hazards"].values())
        sim = dict(_state["sim"])
        emos = dict(_state["emos"])
        rescue = dict(_state["rescue"]) if isinstance(_state.get("rescue"), dict) else _state.get("rescue")
        openclaw_events = list(_state.get("openclaw_events") or [])
    return {
        "ok": True,
        "status": cached_status,
        "sim": sim,
        "robots": robots,
        "robot_profiles": _openclaw_robot_profiles(),
        "hazards": hazards,
        "planner_state": emos,
        "openclaw_events": openclaw_events,
        "rescue": rescue,
        "tasks": _OPENCLAW_TASK_CATALOG,
        "constraints": list(_OPENCLAW_CONSTRAINTS),
        "map": {
            "meta": dict(_map_meta) if _map_meta else {},
            "png_url": "/api/map",
            "meta_url": "/api/map/meta",
        },
        "submit": {
            "assignments_url": "/api/openclaw/assignments",
            "rescue_url": "/api/openclaw/rescue",
            "legacy_assignments_url": "/api/emos/reassign",
            "legacy_rescue_url": "/api/rescue/approve",
        },
    }


def _openclaw_api_enabled() -> bool:
    with _state_lock:
        return str((_state.get("sim") or {}).get("mode", "")) == "openclaw"


# ── HTTP 请求处理器 ────────────────────────────────────────────────────────────

class DashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # 静默日志，减少控制台噪音

    def do_OPTIONS(self):
        self.send_response(200)
        self._send_cors()
        self.end_headers()

    def _send_cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json(self, data: Any, status: int = 200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self._send_cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _png(self, path: str):
        try:
            with open(path, "rb") as f:
                body = f.read()
            self.send_response(200)
            self._send_cors()
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(body)
        except FileNotFoundError:
            self._json({"error": "map not found"}, 404)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "/api/status":
            with _cached_status_lock:
                self._json(dict(_cached_status))
        elif path == "/api/robots":
            with _state_lock:
                data = dict(_state["robots"])
            self._json(data)
        elif path == "/api/sim":
            with _state_lock:
                data = dict(_state["sim"])
            self._json(data)
        elif path == "/api/hazards":
            with _state_lock:
                data = list(_state["hazards"].values())
            self._json({"hazards": data})
        elif path == "/api/emos":
            with _state_lock:
                self._json(dict(_state["emos"]))
        elif path == "/api/map":
            if _map_png_path and os.path.isfile(_map_png_path):
                self._png(_map_png_path)
            else:
                self._json({"error": "map not configured"}, 404)
        elif path == "/api/map/meta":
            self._json(_map_meta if _map_meta else {"error": "meta not configured"})
        elif path == "/api/chat":
            with _state_lock:
                self._json(list(_state["chat_messages"]))
        elif path == "/api/llm_presets":
            self._json({
                "current": get_emos_llm_preset_id(),
                "presets": list_presets_for_api(),
            })
        elif path == "/api/arm_teleop":
            # 返回当前遥操作配置，便于前端调试/显示。
            self._json({
                "enabled": get_arm_teleop_enabled(),
                "keys_down": sorted(list(get_arm_teleop_keys_down())),
                "robot": get_arm_teleop_robot(),
            })
        elif path == "/api/openclaw/context":
            if not _openclaw_api_enabled():
                self._json({"ok": False, "error": "openclaw api is only available in OpenClaw mode"}, 409)
                return
            self._json(_openclaw_context())
        elif path == "/api/all":
            with _cached_status_lock:
                cached_st = dict(_cached_status)
            with _state_lock:
                robots = dict(_state["robots"])
                hazards = list(_state["hazards"].values())
                sim = dict(_state["sim"])
                emos = dict(_state["emos"])
                chat = list(_state["chat_messages"])
                rescue = _state.get("rescue")
                voice_command = dict(_state.get("voice_command") or {})
            result = {
                "status": cached_st,
                "robots": robots,
                "hazards": hazards,
                "sim": sim,
                "emos": emos,
                "chat": chat,
                "voice_command": voice_command,
            }
            if rescue is not None:
                result["rescue"] = rescue
            result["llm_presets"] = {
                "current": get_emos_llm_preset_id(),
                "presets": list_presets_for_api(),
            }
            self._json(result)
        elif path == "/design-tokens.css":
            self._serve_design_tokens_css()
        elif path in ("", "/dashboard", "/index.html", "/index_v5.html"):
            self._serve_html()
        else:
            self._json({"error": "not found"}, 404)

    def _serve_design_tokens_css(self):
        """Serve the dashboard design-token stylesheet."""
        css_path = os.path.join(os.path.dirname(__file__), "design-tokens.css")
        css_path = os.path.normpath(css_path)
        try:
            with open(css_path, "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/css; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except FileNotFoundError:
            self._json({"error": "design tokens css not found", "path": css_path}, 404)

    def _serve_html(self):
        """Serve the dashboard HTML file (enables same-origin access, avoids CORS)."""
        html_path = _dashboard_html_path
        if not html_path or not os.path.isfile(html_path):
            # Fallback: look relative to this file
            html_path = os.path.join(os.path.dirname(__file__), "index.html")
            html_path = os.path.normpath(html_path)
        try:
            with open(html_path, "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except FileNotFoundError:
            self._json({"error": "dashboard html not found", "path": html_path}, 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        parts = parsed.path.strip("/").split("/")

        # POST /api/robots/{name}/goal
        if len(parts) == 4 and parts[0] == "api" and parts[1] == "robots" and parts[3] == "goal":
            robot_name = parts[2]
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                payload = json.loads(body)
                x = float(payload["x"])
                y = float(payload["y"])
                _goal_queue.put_nowait({"robot": robot_name, "x": x, "y": y})
                self._json({"ok": True, "robot": robot_name, "x": x, "y": y})
            except Exception as e:
                self._json({"error": str(e)}, 400)
        elif len(parts) == 3 and parts[0] == "api" and parts[1] == "emos" and parts[2] == "reassign":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                payload = json.loads(body)
                _reassign_queue.put_nowait(payload)
                with _state_lock:
                    _state["emos"]["assignments"] = payload.get("assignments", {})
                self._json({"ok": True})
            except Exception as e:
                self._json({"error": str(e)}, 400)
        elif len(parts) == 3 and parts[0] == "api" and parts[1] == "openclaw" and parts[2] == "assignments":
            if not _openclaw_api_enabled():
                self._json({"ok": False, "error": "openclaw api is only available in OpenClaw mode"}, 409)
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                payload = json.loads(body or "{}")
                assignments = payload.get("assignments")
                if not isinstance(assignments, dict) or not assignments:
                    raise ValueError("missing assignments")
                _reassign_queue.put_nowait(payload)
                with _state_lock:
                    _state["emos"]["assignments"] = assignments
                self._json({"ok": True, "accepted": len(assignments)})
            except Exception as e:
                self._json({"error": str(e)}, 400)
        elif len(parts) == 3 and parts[0] == "api" and parts[1] == "rescue" and parts[2] == "approve":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                payload = json.loads(body)
                _rescue_approval_queue.put_nowait(payload)
                self._json({"ok": True, "action": payload.get("action"), "robot": payload.get("robot")})
            except Exception as e:
                self._json({"error": str(e)}, 400)
        elif len(parts) == 3 and parts[0] == "api" and parts[1] == "openclaw" and parts[2] == "rescue":
            if not _openclaw_api_enabled():
                self._json({"ok": False, "error": "openclaw api is only available in OpenClaw mode"}, 409)
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                payload = json.loads(body or "{}")
                _rescue_approval_queue.put_nowait(payload)
                self._json({"ok": True, "action": payload.get("action"), "robot": payload.get("robot")})
            except Exception as e:
                self._json({"error": str(e)}, 400)
        elif len(parts) == 3 and parts[0] == "api" and parts[1] == "voice" and parts[2] == "command":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                payload = json.loads(body or "{}")
                action = str(payload.get("action", "")).strip()
                command_id = str(payload.get("command_id", "")).strip()
                if action not in ("confirm", "cancel"):
                    raise ValueError("invalid voice action")
                _voice_action_queue.put_nowait({
                    "action": action,
                    "command_id": command_id,
                    "source": "dashboard",
                    "ts": time.time(),
                })
                self._json({"ok": True, "action": action, "command_id": command_id})
            except Exception as e:
                self._json({"error": str(e)}, 400)
        elif len(parts) == 3 and parts[0] == "api" and parts[1] == "emos" and parts[2] == "llm":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                payload = json.loads(body or "{}")
                preset = payload.get("preset", "")
                if not isinstance(preset, str) or not preset:
                    self._json({"ok": False, "error": "missing preset"}, 400)
                    return
                pid = preset if preset in EMOS_LLM_PRESETS else DEFAULT_EMOS_LLM_PRESET
                meta = EMOS_LLM_PRESETS[pid]
                key_env = meta["api_key_env"]
                if not os.environ.get(key_env) and not meta.get("api_key_default"):
                    self._json(
                        {
                            "ok": False,
                            "error": (
                                f"仿真进程未设置环境变量 {key_env}，无法使用预设「{meta['label']}」。"
                                "请在启动 Isaac / emos.py 的同一终端先 export 该变量，"
                                "或将 export 写入 ~/.profile（勿依赖仅交互式 .bashrc 后半段）。"
                            ),
                            "required_env": key_env,
                        },
                        400,
                    )
                    return
                cur = set_emos_llm_preset_id(preset)
                self._json({"ok": True, "current": cur})
            except Exception as e:
                self._json({"ok": False, "error": str(e)}, 400)
        elif len(parts) == 3 and parts[0] == "api" and parts[1] == "emos" and parts[2] == "replan":
            # Dashboard 重规划：入队供主循环 trigger；短时间去重避免重复 POST 刷 LLM
            global _replan_last_post_ts
            try:
                now = time.time()
                if now - _replan_last_post_ts < _REPLAN_POST_DEBOUNCE_S:
                    self._json({
                        "ok": True,
                        "ignored": True,
                        "reason": f"debounce_{_REPLAN_POST_DEBOUNCE_S:.0f}s",
                    })
                else:
                    _replan_last_post_ts = now
                    _replan_queue.put_nowait({"ts": now})
                    self._json({"ok": True})
            except Exception as e:
                self._json({"error": str(e)}, 400)
        elif len(parts) == 3 and parts[0] == "api" and parts[1] == "manual" and parts[2] == "action":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                payload = json.loads(body or "{}")
                robot = str(payload.get("robot", "")).strip()
                action = str(payload.get("action", "")).strip()
                if not robot:
                    raise ValueError("missing robot")
                if not action:
                    raise ValueError("missing action")
                _manual_action_queue.put_nowait({"robot": robot, "action": action})
                self._json({"ok": True, "robot": robot, "action": action})
            except Exception as e:
                self._json({"error": str(e)}, 400)
        elif len(parts) == 3 and parts[0] == "api" and parts[1] == "manual" and parts[2] == "hazard":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                payload = json.loads(body or "{}")
                hazard_id = int(payload.get("hazard_id", 0))
                if hazard_id <= 0:
                    raise ValueError("invalid hazard_id")
                _manual_hazard_queue.put_nowait({"hazard_id": hazard_id})
                self._json({"ok": True, "hazard_id": hazard_id})
            except Exception as e:
                self._json({"error": str(e)}, 400)
        elif len(parts) == 4 and parts[0] == "api" and parts[1] == "manual" and parts[2] == "top_camera" and parts[3] == "height":
            # POST /api/manual/top_camera/height {delta?: number, height?: number}
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                payload = json.loads(body or "{}")
                cmd: Dict[str, Any] = {}
                if "height" in payload:
                    cmd["height"] = float(payload["height"])
                if "delta" in payload:
                    cmd["delta"] = float(payload["delta"])
                if not cmd:
                    raise ValueError("missing height or delta")
                _manual_top_camera_queue.put_nowait(cmd)
                self._json({"ok": True, **cmd})
            except Exception as e:
                self._json({"error": str(e)}, 400)
        elif len(parts) == 3 and parts[0] == "api" and parts[1] == "manual" and parts[2] == "task_end":
            # POST /api/manual/task_end {type: "simulation_end" | "robot_end", robot?: "..."}
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                payload = json.loads(body or "{}")
                end_type = str(payload.get("type", "")).strip()
                robot = str(payload.get("robot", "")).strip()
                if end_type not in ("simulation_end", "robot_end"):
                    raise ValueError("invalid type")
                if end_type == "robot_end" and not robot:
                    raise ValueError("missing robot for robot_end")
                cmd = {"type": end_type}
                if robot:
                    cmd["robot"] = robot
                _manual_task_end_queue.put_nowait(cmd)
                self._json({"ok": True, **cmd})
            except Exception as e:
                self._json({"error": str(e)}, 400)
        elif len(parts) == 2 and parts[0] == "api" and parts[1] == "arm_teleop":
            # POST /api/arm_teleop  {enabled: bool, robot?: str}
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                payload = json.loads(body or "{}")
                enabled = bool(payload.get("enabled", False))
                set_arm_teleop_enabled(enabled)
                robot = payload.get("robot")
                if isinstance(robot, str) and robot:
                    set_arm_teleop_robot(robot)
                self._json({
                    "ok": True,
                    "enabled": enabled,
                    "robot": get_arm_teleop_robot(),
                })
            except Exception as e:
                self._json({"error": str(e)}, 400)
        elif len(parts) == 3 and parts[0] == "api" and parts[1] == "arm_teleop" and parts[2] == "keys":
            # POST /api/arm_teleop/keys  {keys_down: ["W","A",...]}
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                payload = json.loads(body or "{}")
                keys_down = payload.get("keys_down", [])
                if not isinstance(keys_down, list):
                    raise ValueError("keys_down must be a list of strings")
                update_arm_teleop_keys(keys_down)
                self._json({"ok": True, "keys_down": sorted(list(get_arm_teleop_keys_down()))})
            except Exception as e:
                self._json({"error": str(e)}, 400)
        else:
            self._json({"error": "not found"}, 404)

    @staticmethod
    def _get_system_status() -> dict:
        """收集主机系统指标（依赖 psutil + nvidia-ml-py）。"""
        result: dict = {}

        # ── CPU / Memory ──────────────────────────────────────────────────────
        try:
            import psutil
            result["cpu_percent"] = psutil.cpu_percent(interval=None)
            vm = psutil.virtual_memory()
            result["mem_total_gb"] = round(vm.total / 1e9, 1)
            result["mem_used_gb"] = round(vm.used / 1e9, 1)
            result["mem_percent"] = vm.percent

            # Disk I/O (MB/s) - delta
            disk = psutil.disk_io_counters()
            result["disk_read_mbps"] = round(disk.read_bytes / 1e6, 1) if disk else 0
            result["disk_write_mbps"] = round(disk.write_bytes / 1e6, 1) if disk else 0

            # Network (MB/s)
            net = psutil.net_io_counters()
            result["net_sent_mbps"] = round(net.bytes_sent / 1e6, 1) if net else 0
            result["net_recv_mbps"] = round(net.bytes_recv / 1e6, 1) if net else 0

            # Uptime
            result["uptime_s"] = int(time.time() - _start_time)
        except Exception as e:
            result["cpu_error"] = str(e)

        # ── GPU (nvidia-smi via pynvml) ────────────────────────────────────────
        try:
            import pynvml
            pynvml.nvmlInit()
            handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            util = pynvml.nvmlDeviceGetUtilizationRates(handle)
            mem_info = pynvml.nvmlDeviceGetMemoryInfo(handle)
            power = pynvml.nvmlDeviceGetPowerUsage(handle) / 1000.0  # mW → W
            temp = pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU)
            result["gpu_util"] = util.gpu
            result["gpu_mem_util"] = util.memory
            result["gpu_mem_used_gb"] = round(mem_info.used / 1e9, 1)
            result["gpu_mem_total_gb"] = round(mem_info.total / 1e9, 1)
            result["gpu_mem_percent"] = round(mem_info.used / mem_info.total * 100, 1)
            result["gpu_power_w"] = round(power, 1)
            result["gpu_temp_c"] = temp
            pynvml.nvmlShutdown()
        except Exception:
            # Fallback: try nvidia-smi
            try:
                import subprocess
                out = subprocess.check_output(
                    ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total,power.draw,temperature.gpu",
                     "--format=csv,noheader,nounits"],
                    timeout=2, text=True
                ).strip().split(",")
                result["gpu_util"] = float(out[0].strip())
                result["gpu_mem_used_gb"] = round(float(out[1].strip()) / 1024, 1)
                result["gpu_mem_total_gb"] = round(float(out[2].strip()) / 1024, 1)
                result["gpu_mem_percent"] = round(float(out[1].strip()) / float(out[2].strip()) * 100, 1)
                result["gpu_power_w"] = round(float(out[3].strip()), 1)
                result["gpu_temp_c"] = int(out[4].strip())
            except Exception:
                result["gpu_util"] = 0
                result["gpu_mem_percent"] = 0
                result["gpu_power_w"] = 0

        return result


# ── 服务器启停 ─────────────────────────────────────────────────────────────────

def configure(map_png: str = "", map_yaml: dict = None, html_path: str = "") -> None:
    """
    配置地图文件路径和元信息（须在 start() 之前调用）。

    Args:
        map_png:   工厂地图 PNG 文件绝对路径
        map_yaml:  工厂地图 YAML 配置字典，需包含 resolution / origin 字段
        html_path: Dashboard HTML 绝对路径（配置后可通过 http://127.0.0.1:8767/ 访问）
    """
    global _map_png_path, _map_meta, _dashboard_html_path
    if html_path:
        _dashboard_html_path = html_path
    _map_png_path = map_png
    if map_yaml:
        res = float(map_yaml.get("resolution", 0.05))
        ox = float(map_yaml["origin"][0])
        oy = float(map_yaml["origin"][1])
        try:
            from PIL import Image
            img = Image.open(map_png)
            w, h = img.size
            img.close()
        except Exception:
            w, h = 439, 405
        _map_meta = {
            "resolution": res,
            "origin_x": ox,
            "origin_y": oy,
            "width_px": w,
            "height_px": h,
            "map_width_px": w,
            "map_height_px": h,
            "world_x_min": ox,
            "world_x_max": ox + w * res,
            "world_y_min": oy,
            "world_y_max": oy + h * res,
        }


def start(port: int = 8767) -> None:
    """在后台线程中启动 HTTP 数据服务器。"""
    global _server_thread, _httpd, _PORT, _start_time
    _PORT = port
    _start_time = time.time()

    if _server_thread is not None and _server_thread.is_alive():
        logger.warning("[DataServer] 已在运行，跳过重复启动。")
        return

    def _run():
        global _httpd
        try:
            _httpd = HTTPServer(("0.0.0.0", port), DashboardHandler)
            _httpd.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            print(f"[DataServer] ✅ 数据接口服务器已启动：http://0.0.0.0:{port}")
            print(f"[DataServer] 📊 Dashboard 访问地址: http://127.0.0.1:{port}/")
            _httpd.serve_forever()
        except OSError as e:
            print(f"[DataServer] ❌ 启动失败（端口占用或权限问题）: {e}")

    _server_thread = threading.Thread(target=_run, daemon=True, name="DataServer")
    _server_thread.start()

    def _status_loop():
        """后台线程：每 2 秒更新一次缓存的系统状态。"""
        try:
            import psutil
            psutil.cpu_percent(interval=None)
        except Exception:
            pass
        time.sleep(1)
        while True:
            try:
                st = DashboardHandler._get_system_status()
                with _cached_status_lock:
                    _cached_status.update(st)
            except Exception as _e:
                print(f"[StatusPoller] 采集异常: {_e}")
            time.sleep(2)

    _status_thread = threading.Thread(target=_status_loop, daemon=True, name="StatusPoller")
    _status_thread.start()


def stop() -> None:
    global _httpd
    if _httpd:
        _httpd.shutdown()
        _httpd = None
