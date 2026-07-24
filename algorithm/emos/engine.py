"""EMOS multi-LLM discussion engine (threaded); scenario-driven."""

from __future__ import annotations

import copy
import html
import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from queue import Empty, Queue
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from .assignment import (
    fallback_assignment,
    filter_subtask_positions_for_completed,
    parse_discussion_results,
    sanitize_llm_assignments,
    validate_and_fix_green_assignment,
    validate_and_fix_yellow_assignment,
)
from .resume_provider import build_robot_resumes
from .scene_builder import build_scene_description, rescue_manager_constraint_text
from .types import (
    EMOSPhase,
    EMOSRobotAgentSpec,
    EMOSScenarioConfig,
    RobotProfileSpec,
    RobotTask,
    compute_subtask_positions,
)

LLM_AVAILABLE = False
group_discussion: Any = None
generate_robot_resume_fn: Any = None
_LLM_IMPORT_ERROR: Optional[BaseException] = None

try:
    from .brain.MultiLLM_discussion import group_discussion
    from .brain.robot_resume import generate_robot_resume as generate_robot_resume_fn

    LLM_AVAILABLE = True
except Exception as _e:
    _LLM_IMPORT_ERROR = _e


_ROBOT_COLORS = [
    "\033[92m",  # 绿
    "\033[93m",  # 黄
    "\033[94m",  # 蓝
    "\033[95m",  # 紫
    "\033[96m",  # 青
    "\033[91m",  # 红
    "\033[97m",  # 白
]


class _C:
    RST = "\033[0m"
    BOLD = "\033[1m"
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    PURPLE = "\033[95m"
    BLUE = "\033[94m"
    RED = "\033[91m"
    WHITE = "\033[97m"

    @classmethod
    def r(cls, name: str) -> str:
        # 动态颜色分配：基于名字哈希选择颜色，支持任意数量机器人
        idx = hash(name) % len(_ROBOT_COLORS)
        return _ROBOT_COLORS[idx]


def _sys(msg: str) -> None:
    print(f"{_C.CYAN}{_C.BOLD}[EMOS]{_C.RST} {msg}")


def _leader(msg: str) -> None:
    print(f"{_C.CYAN}{_C.BOLD}[EMOS-Leader]{_C.RST}{_C.CYAN} {msg}{_C.RST}")


def _get_robot_pos(env: Any, robot_name: str) -> Tuple[float, float, float]:
    """Extract (x, y, z) world position of a robot from the Isaac Lab scene.

    Prefer :attr:`ArticulationData.root_pos_w` (Isaac Lab / PhysX). The legacy
    ``gym.get_actor_root_state_tensor`` path often yields zeros or garbage on
    current Isaac Sim builds because actor indices no longer match that tensor layout.
    """
    if not hasattr(env, "scene"):
        return (0.0, 0.0, 0.0)
    scene = env.scene
    asset = None
    if hasattr(scene, "articulations") and robot_name in scene.articulations:
        asset = scene.articulations[robot_name]
    elif hasattr(scene, "rigid_objects") and robot_name in scene.rigid_objects:
        asset = scene.rigid_objects[robot_name]

    if asset is not None:
        try:
            data = getattr(asset, "data", None)
            if data is not None:
                rp = getattr(data, "root_pos_w", None)
                if rp is not None and rp.numel() >= 3:
                    p = rp[0]
                    return (float(p[0].item()), float(p[1].item()), float(p[2].item()))
        except Exception:
            pass

    if hasattr(env, "gym") and hasattr(env, "sim") and robot_name in getattr(
        scene, "articulations", {}
    ):
        try:
            body_handle = scene.articulations[robot_name]._root_handle
            t = env.gym.get_actor_root_state_tensor(env.sim)
            pos = t[body_handle, :3].cpu().numpy()
            return (float(pos[0]), float(pos[1]), float(pos[2]))
        except Exception:
            pass
    return (0.0, 0.0, 0.0)


class EMOSDiscussionManager:
    """Manages EMOS multi-LLM discussion in a background thread."""

    def __init__(
        self,
        base_env: Any,
        possible_agents: list[str],
        get_robot_pos_fn: Callable[[str], Tuple[float, float, float]],
        scenario: EMOSScenarioConfig,
        *,
        push_chat_fn: Optional[Callable[[str, str, str], None]] = None,
        log_dir: str = "logs/emos_reports",
        discussion_log_dir: str = "logs/multi_llm_demo",
        use_env_resume: bool = True,
        rescue_constraint_fn: Optional[Callable[[], Optional[str]]] = None,
        write_html_report: bool = False,
        get_group_discussion_llm_kwargs: Optional[Callable[[], Dict[str, Any]]] = None,
    ):
        self._env = base_env
        self._agents = list(possible_agents)
        self._get_pos = get_robot_pos_fn
        self._push_chat = push_chat_fn or (lambda *_: None)
        self._log_dir = log_dir
        self._disc_log_dir = discussion_log_dir
        self._scenario = scenario
        self._use_env_resume = use_env_resume
        self._rescue_constraint_fn = rescue_constraint_fn
        self._write_html_report = write_html_report
        self._get_group_discussion_llm_kwargs = get_group_discussion_llm_kwargs

        self._phase = EMOSPhase.IDLE
        self._lock = threading.Lock()
        self._result_queue: Queue = Queue(maxsize=4)
        self._thread: Optional[threading.Thread] = None
        self._last_result: Optional[Dict[str, RobotTask]] = None
        self._hazard_id: int = 0
        self._hazard_pos: Tuple[float, float, float] = (0, 0, 0)

        self._active_rescue_manager: Any = None
        self._replan_triggered: bool = False
        self._replan_cooldown: float = 0.0
        self._robots_completed_extinguisher: Set[str] = set()
        self._robots_completed_rescue: Set[str] = set()
        self.last_discussion_elapsed_s: float = 0.0
        self.cumulative_discussion_elapsed_s: float = 0.0
        self.discussion_count: int = 0
        # 当主循环检测到 LLM 超时（或上层显式要求走本地规则）时，会通过
        # ``trigger_local_fallback`` 把 fallback 结果直接 put 进 queue，并把当前
        # 轮次标记为已熔断；后续 LLM worker 真返回时检查此标记，避免覆盖派遣。
        self._fallback_dispatched_for_round: bool = False
        # 最近一次 trigger 的墙钟时间戳（供上层做超时熔断判断）
        self._last_trigger_started_at: float = 0.0
        self.fallback_dispatch_count: int = 0

    @classmethod
    def build_from_agent_specs(
        cls,
        base_env: Any,
        agent_specs: Dict[str, EMOSRobotAgentSpec],
        scenario: EMOSScenarioConfig,
        push_chat_fn: Optional[Callable[[str, str, str], None]] = None,
        log_dir: str = "logs/emos_reports",
        discussion_log_dir: str = "logs/multi_llm_demo",
        use_env_resume: bool = True,
        rescue_constraint_fn: Optional[Callable[[], Optional[str]]] = None,
        write_html_report: bool = False,
        get_group_discussion_llm_kwargs: Optional[Callable[[], Dict[str, Any]]] = None,
    ) -> "EMOSDiscussionManager":
        """Build a manager from per-robot specs and a **caller-supplied** scenario.

        This library does not ship domain-specific defaults (e.g. factory layout).
        The simulation or application layer must construct
        :class:`~.types.EMOSScenarioConfig` (e.g. via :func:`~.types.scenario_from_dict`
        or :func:`~.scenario_loader.load_scenario` for a YAML file you own).

        Args:
            base_env: The Isaac Lab (or compatible) environment instance.
            agent_specs: Dict mapping robot asset names → ``EMOSRobotAgentSpec``.
            scenario: Full task background and subtask/position rules.
            push_chat_fn: Optional chat-broadcast callback.
            log_dir: Directory for HTML reports.
            discussion_log_dir: Directory for LLM discussion logs.
            use_env_resume: Pass through to manager (use env-based resumes).
            rescue_constraint_fn: Optional callable returning rescue constraints.
            get_group_discussion_llm_kwargs: Optional callable returning ``llm_model`` /
                ``llm_base_url`` / ``llm_api_key_env`` for :func:`~.brain.MultiLLM_discussion.group_discussion`.
        """
        scenario_cfg = copy.deepcopy(scenario)

        agents = list(agent_specs.keys())

        # Merge static profiles from specs into the scenario (caller copy already deep-copied if scenario= was passed).
        for name, spec in agent_specs.items():
            # Auto-fill agent_name from dict key so users don't have to repeat it.
            if not spec.agent_name:
                spec.agent_name = name
            # Only override scenario.robot_profiles when the spec carries content;
            # otherwise keep existing entries from ``scenario``.
            if spec.robot_type or spec.capabilities:
                scenario_cfg.robot_profiles[name] = RobotProfileSpec(
                    robot_type=spec.robot_type,
                    capabilities=list(spec.capabilities),
                )
            if spec.preferred_task and name not in scenario_cfg.preferred_fallback:
                scenario_cfg.preferred_fallback[name] = spec.preferred_task

        manager = cls(
            base_env=base_env,
            possible_agents=agents,
            get_robot_pos_fn=lambda n, _env=base_env: _get_robot_pos(_env, n),
            push_chat_fn=push_chat_fn,
            log_dir=log_dir,
            discussion_log_dir=discussion_log_dir,
            scenario=scenario_cfg,
            use_env_resume=use_env_resume,
            rescue_constraint_fn=rescue_constraint_fn,
            write_html_report=write_html_report,
            get_group_discussion_llm_kwargs=get_group_discussion_llm_kwargs,
        )
        return manager

    def set_group_discussion_llm_kwargs_getter(
        self, fn: Optional[Callable[[], Dict[str, Any]]]
    ) -> None:
        """Replace the callable that supplies ``llm_*`` kwargs for :func:`group_discussion`."""
        self._get_group_discussion_llm_kwargs = fn

    def _llm_display_name(self) -> str:
        """Human-readable provider name for chat/console (from preset ``llm_display_label``)."""
        if self._get_group_discussion_llm_kwargs is None:
            return "LLM"
        try:
            raw = self._get_group_discussion_llm_kwargs()
            if isinstance(raw, dict):
                return str(raw.get("llm_display_label") or raw.get("llm_model") or "LLM")
        except Exception:
            pass
        return "LLM"

    def should_preserve_raw_llm_assignments(self) -> bool:
        """Whether current LLM preset should execute the parsed LLM assignment as-is."""
        if self._get_group_discussion_llm_kwargs is None:
            return False
        try:
            raw = self._get_group_discussion_llm_kwargs()
            return isinstance(raw, dict) and bool(raw.get("llm_preserve_raw_assignments", False))
        except Exception:
            return False

    @property
    def phase(self) -> EMOSPhase:
        with self._lock:
            return self._phase

    @property
    def is_running(self) -> bool:
        return self.phase == EMOSPhase.RUNNING

    def trigger(
        self,
        hazard_id: int,
        hazard_pos: Tuple[float, float, float],
        task_description: str = "",
        subtask_positions: Optional[Dict[str, Tuple[float, float]]] = None,
    ) -> None:
        if self.is_running:
            _sys("EMOS 讨论正在进行中，忽略重复触发。")
            return

        with self._lock:
            self._phase = EMOSPhase.RUNNING
            self._fallback_dispatched_for_round = False
        self._hazard_id = hazard_id
        self._hazard_pos = hazard_pos
        self._last_result = None
        self._last_trigger_started_at = time.time()

        _task = task_description or self._scenario.task_description
        _positions = compute_subtask_positions(
            self._scenario,
            hazard_pos,
            base_overrides=subtask_positions,
        )

        self._thread = threading.Thread(
            target=self._worker,
            args=(_task, _positions),
            daemon=True,
            name="EMOS-Discussion",
        )
        self._thread.start()
        _sys(f"EMOS 多机器人协同讨论已启动（{self._scenario.anchor_label} 位置 {hazard_id}）...")
        _ln = self._llm_display_name()
        self._push_chat(
            "emos",
            "EMOS 系统",
            f"⚠️ 检测到危险事件！位置 #{hazard_id} ({hazard_pos[0]:.1f}, {hazard_pos[1]:.1f}) 发现{self._scenario.anchor_label}。\n"
            f"正在启动 {len(self._agents)} 个机器人的协同讨论（{_ln}）...",
        )

    def poll(self) -> Optional[Dict[str, RobotTask]]:
        try:
            result = self._result_queue.get_nowait()
            self._last_result = result
            return result
        except Empty:
            return None

    @property
    def last_result(self) -> Optional[Dict[str, RobotTask]]:
        return self._last_result

    def seconds_since_trigger(self) -> float:
        """已触发 LLM 讨论后经过的墙钟秒。

        仅当 phase 仍为 RUNNING 时返回非零，专用于主循环判断"首轮 LLM 是否
        已经卡得足够久要熔断"。救援动态重规划路径直接走 trigger_local_fallback，
        不会进入 RUNNING，此处也会返回 0（符合预期：本地规则路径不需要再次熔断）。
        """
        if not self.is_running or self._last_trigger_started_at <= 0.0:
            return 0.0
        return max(0.0, time.time() - self._last_trigger_started_at)

    def trigger_local_fallback(self, reason: str = "") -> bool:
        """跳过 LLM，直接用本地规则引擎（fallback_assignment）出方案并入队。

        触发场景：
        1. 救援动态重规划：剩余机器人 + 颜色子任务的分配是确定性约束（green↔scout_1、
           yellow↔M20*、其它按 ``preferred_fallback``），不必再吃一次 LLM RTT。
        2. 主循环检测 LLM 首轮 RTT 超过阈值时的熔断：本地规则保底，避免长尾把
           任务时长拖长。

        ``_fallback_dispatched_for_round`` 标记同时被 :meth:`_worker` 成功路径与本方法
        在锁内置位，谁先占位谁负责 put；后到的一路看到标志直接放弃，避免向 queue 双投。
        返回值：是否真的派发了 fallback 方案（False 表示当前已有结果在路上或入队完成）。
        """
        with self._lock:
            if self._fallback_dispatched_for_round:
                return False
            self._fallback_dispatched_for_round = True

        positions = compute_subtask_positions(self._scenario, self._hazard_pos)
        try:
            assignments = self._fallback_wrapped(positions)
        except Exception as exc:
            _sys(f"{_C.RED}本地规则分配异常: {exc}{_C.RST}")
            with self._lock:
                # 异常时让出占位，允许其它路径（例如 LLM worker 完成）继续投递。
                self._fallback_dispatched_for_round = False
            return False

        self._result_queue.put(assignments)
        with self._lock:
            self._phase = EMOSPhase.DONE
        self.fallback_dispatch_count += 1

        _reason_text = reason.strip() or "本地规则"
        _sys(f"{_C.YELLOW}⚡ 本地规则引擎直接出分配方案（{_reason_text}），跳过 LLM。{_C.RST}")
        self._push_chat(
            "emos",
            "EMOS 系统",
            f"⚡ 已使用本地规则引擎完成任务分配（{_reason_text}），跳过 LLM 调用以提速。",
        )
        return True

    def register_rescue_manager(self, rescue_mgr: Any) -> None:
        self._active_rescue_manager = rescue_mgr

    def register_extinguisher_completed(self, robot_name: str) -> None:
        if robot_name:
            self._robots_completed_extinguisher.add(robot_name)

    def register_rescue_completed(self, robot_name: str) -> None:
        if robot_name:
            self._robots_completed_rescue.add(robot_name)

    def clear_session_task_history(self) -> None:
        self._robots_completed_extinguisher.clear()
        self._robots_completed_rescue.clear()

    def filter_dashboard_reassignments(self, assignments: Dict[str, Any]) -> Dict[str, Any]:
        if not assignments:
            return assignments
        yid = self._scenario.yellow_subtask_id
        blocked = self._robots_completed_extinguisher | self._robots_completed_rescue
        out: Dict[str, Any] = {}
        for rn, info in assignments.items():
            col = str(info.get("colour", "") or "").lower()
            if col == yid and rn in blocked:
                self._push_chat(
                    "sys",
                    "EMOS 系统",
                    f"⚠️ 已跳过 {rn} 的灭火器（黄）任务：该机器人本会话已完成灭火器或救援，不再重复分配。",
                )
                continue
            out[rn] = info
        return out

    def _sanitize_llm_assignments(
        self,
        assignments: Dict[str, RobotTask],
        positions: Dict[str, Tuple[float, float]],
    ) -> Dict[str, RobotTask]:
        return sanitize_llm_assignments(
            self._scenario,
            assignments,
            positions,
            self._agents,
            self._robots_completed_extinguisher,
            self._robots_completed_rescue,
            self._fallback_for_sanitize,
        )

    def _fallback_for_sanitize(self, pos: Dict[str, Tuple[float, float]]) -> Dict[str, RobotTask]:
        ass = fallback_assignment(
            self._scenario,
            pos,
            self._agents,
            robots_completed_extinguisher=self._robots_completed_extinguisher,
            robots_completed_rescue=self._robots_completed_rescue,
            sys_log=_sys,
            push_chat=self._push_chat,
            generate_report=self._generate_report,
            color_for_agent=lambda rn: f"{_C.r(rn)}{rn}{_C.RST}",
        )
        return self._sanitize_llm_assignments(ass, pos)

    def _worker(self, task_desc: str, positions: Dict[str, Tuple[float, float]]) -> None:
        positions = filter_subtask_positions_for_completed(
            dict(positions),
            self._scenario,
            self._agents,
            self._robots_completed_extinguisher,
            self._robots_completed_rescue,
        )
        try:
            assignments = self._run_discussion(task_desc, positions)
            with self._lock:
                if self._fallback_dispatched_for_round:
                    _sys(
                        f"{_C.YELLOW}⚠️ 本轮已被本地规则熔断派遣，"
                        f"丢弃迟到的 LLM 结果（耗时 {self.last_discussion_elapsed_s:.1f}s）。{_C.RST}"
                    )
                    return
                # 占位本轮派遣，避免 trigger_local_fallback 在 put 之间见缝插针又投一次。
                self._fallback_dispatched_for_round = True
                self._phase = EMOSPhase.DONE
            self._result_queue.put(assignments)
        except Exception as exc:
            _sys(f"{_C.RED}EMOS 讨论异常: {exc}{_C.RST}")
            import traceback

            traceback.print_exc()
            self._push_chat(
                "sys",
                "EMOS 系统",
                f"⚠️ LLM 讨论出现异常：{str(exc)[:200]}\n已切换为启发式任务分配。",
            )
            with self._lock:
                if self._fallback_dispatched_for_round:
                    return
                self._fallback_dispatched_for_round = True
            assignments = self._fallback_wrapped(positions)
            self._result_queue.put(assignments)
            with self._lock:
                # 与 trigger_local_fallback 终态对齐：本轮已成功入队 → DONE，
                # 让消费方只看队列是否有结果即可，不再额外区分 ERROR。
                self._phase = EMOSPhase.DONE

    def _fallback_wrapped(self, positions: Dict[str, Tuple[float, float]]) -> Dict[str, RobotTask]:
        ass = fallback_assignment(
            self._scenario,
            positions,
            self._agents,
            robots_completed_extinguisher=self._robots_completed_extinguisher,
            robots_completed_rescue=self._robots_completed_rescue,
            sys_log=_sys,
            push_chat=self._push_chat,
            generate_report=self._generate_report,
            color_for_agent=lambda rn: f"{_C.r(rn)}{rn}{_C.RST}",
        )
        return self._sanitize_llm_assignments(ass, positions)

    def _run_discussion(
        self, task_desc: str, positions: Dict[str, Tuple[float, float]]
    ) -> Dict[str, RobotTask]:
        robot_positions: Dict[str, Tuple[float, float]] = {}
        for name in self._agents:
            try:
                p = self._get_pos(name)
                robot_positions[name] = (float(p[0]), float(p[1]))
            except Exception:
                robot_positions[name] = (0.0, 0.0)

        rescue_extra = None
        if self._rescue_constraint_fn:
            rescue_extra = self._rescue_constraint_fn()
        elif self._active_rescue_manager is not None:
            rescue_extra = rescue_manager_constraint_text(self._active_rescue_manager)

        scene = build_scene_description(
            self._scenario,
            hazard_id=self._hazard_id,
            anchor_pos=self._hazard_pos,
            agents=self._agents,
            robot_positions=robot_positions,
            subtask_positions=positions,
            active_rescue_extra=rescue_extra,
        )

        gen = generate_robot_resume_fn if LLM_AVAILABLE else None
        robot_resumes = build_robot_resumes(
            self._scenario,
            self._agents,
            robot_positions,
            env=self._env,
            use_env_resume=self._use_env_resume and LLM_AVAILABLE,
            generate_robot_resume_fn=gen,
        )

        _sys(f"{'─'*50}")
        _sys(
            f"📍 {self._scenario.anchor_label}位置（标记 #{self._hazard_id}）: "
            f"({self._hazard_pos[0]:.1f}, {self._hazard_pos[1]:.1f})"
        )
        _sys(f"🤖 参与讨论的机器人: {', '.join(self._agents)}")
        _sys(f"{'─'*50}")

        pos_lines = []
        for rname in self._agents:
            rpos = robot_positions.get(rname, (0, 0))
            profile = self._scenario.robot_profiles.get(rname)
            rtype = profile.robot_type if profile else rname
            _sys(f"  {rname} ({rtype}): 当前位置 ({rpos[0]:.1f}, {rpos[1]:.1f})")
            pos_lines.append(f"  {rname} ({rtype}): ({rpos[0]:.1f}, {rpos[1]:.1f})")
        _sys(f"{'─'*50}")

        self._push_chat(
            "emos",
            "EMOS 态势感知",
            "📡 机器人状态采集完成：\n" + "\n".join(pos_lines),
        )

        if LLM_AVAILABLE:
            _ln = self._llm_display_name()
            _sys(f"🧠 调用 {_ln} 进行多机器人协同讨论中...")
            _sys("   （多轮对话讨论 + 任务反思分配，请稍候...）")
            self._push_chat(
                "emos",
                "EMOS 系统",
                f"🧠 正在调用 {_ln} 进行多轮协同讨论...\n"
                "机器人将基于各自能力和位置进行任务分析与分配。",
            )
            return self._run_llm_discussion(task_desc, scene, robot_resumes, positions)

        _sys("⚠️  LLM 模块不可用，使用默认启发式任务分配。")
        if _LLM_IMPORT_ERROR is not None:
            _sys(f"   （原因: {_LLM_IMPORT_ERROR!r}）")
            _req = Path(__file__).resolve().parent / "requirements.txt"
            _sys(f"   安装依赖: pip install -r {_req}")
        self._push_chat(
            "sys",
            "EMOS 系统",
            "⚠️ LLM 模块不可用，使用启发式规则进行任务分配。",
        )
        return self._fallback_wrapped(positions)

    def _run_llm_discussion(
        self,
        task_desc: str,
        scene: str,
        robot_resumes: Dict[str, Any],
        positions: Dict[str, Tuple[float, float]],
    ) -> Dict[str, RobotTask]:
        proxy_vars = [
            "http_proxy",
            "https_proxy",
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "all_proxy",
            "ALL_PROXY",
        ]
        saved = {v: os.environ.pop(v) for v in proxy_vars if v in os.environ}

        _llm_extra: Dict[str, Any] = {}
        _preserve_raw_assignments = False
        if self._get_group_discussion_llm_kwargs is not None:
            try:
                raw = self._get_group_discussion_llm_kwargs()
                if isinstance(raw, dict):
                    _preserve_raw_assignments = bool(raw.get("llm_preserve_raw_assignments", False))
                    for _k in (
                        "llm_model",
                        "llm_base_url",
                        "llm_api_key_env",
                        "llm_api_key_default",
                        "llm_stream",
                        "llm_temperature",
                        "llm_top_p",
                        "llm_temperature_param",
                        "llm_output_profile",
                        "llm_max_tokens",
                        "llm_should_agent_reflection",
                        "llm_leader_self_reflection",
                    ):
                        if raw.get(_k) is not None:
                            _llm_extra[_k] = raw[_k]
            except Exception as _e:
                _sys(f"⚠️  获取 LLM 讨论配置失败，使用默认: {_e}")

        _t0 = time.time()
        try:
            results = group_discussion(
                robot_resume=json.dumps(robot_resumes, ensure_ascii=False, default=str),
                scene_description=scene,
                task_description=task_desc,
                save_chat_history=True,
                save_chat_history_dir=self._disc_log_dir,
                episode_id=0,
                should_group_discussion=True,
                should_agent_reflection=bool(_llm_extra.pop("llm_should_agent_reflection", True)),
                should_robot_resume=True,
                should_numerical=False,
                max_discussion_rounds=1,
                parallel_reflection=True,
                **_llm_extra,
            )
        finally:
            for v, val in saved.items():
                os.environ[v] = val

        _elapsed = time.time() - _t0
        self.last_discussion_elapsed_s = _elapsed
        self.cumulative_discussion_elapsed_s += _elapsed
        self.discussion_count += 1
        _sys(f"🧠 LLM 讨论完成！耗时 {_elapsed:.1f} 秒")
        _sys(f"{'─'*50}")
        _sys("📋 讨论结果解析中...")

        self._push_chat(
            "emos",
            "EMOS 系统",
            f"✅ LLM 多轮讨论完成（耗时 {_elapsed:.1f} 秒）\n正在解析各机器人的任务分配...",
        )

        for rname, agent_args in results.items():
            _raw = getattr(agent_args, "subtask_description", "N/A")
            _sys(f"  {rname} LLM回复: {_raw[:200]}")
            self._push_chat("bot", rname, _raw[:500] if _raw else "(无回复内容)")

        def _leader_line(robot_name: str, tn: str, matched: str, pos: Tuple[float, float]) -> None:
            rc = _C.r(robot_name)
            _leader(
                f"  {rc}{robot_name}{_C.RST} → {tn}（{matched}）"
                f" 目标 ({pos[0]:.1f}, {pos[1]:.1f})"
            )

        return parse_discussion_results(
            self._scenario,
            results,
            positions,
            self._agents,
            leader_log=_leader_line,
            push_chat=self._push_chat,
            generate_report=self._generate_report,
            sanitize=self._sanitize_llm_assignments,
            subtask_tuple_map=self._scenario.subtask_tuple_map(),
            keyword_map=self._scenario.keyword_map_for_parse(),
            agent_iteration_order=self._agents,
            preserve_raw_assignments=_preserve_raw_assignments,
        )

    def _validate_and_fix_yellow_assignment(
        self,
        assignments: Dict[str, RobotTask],
        positions: Dict[str, Tuple[float, float]],
    ) -> Tuple[Dict[str, RobotTask], Optional[str]]:
        return validate_and_fix_yellow_assignment(
            self._scenario,
            assignments,
            positions,
            self._agents,
            self._get_pos,
            self._robots_completed_extinguisher,
            self._robots_completed_rescue,
            self._push_chat,
            self._sanitize_llm_assignments,
        )

    def _validate_and_fix_green_assignment(
        self,
        assignments: Dict[str, RobotTask],
        positions: Dict[str, Tuple[float, float]],
    ) -> Dict[str, RobotTask]:
        return validate_and_fix_green_assignment(
            self._scenario,
            assignments,
            positions,
            self._agents,
            self._robots_completed_extinguisher,
            self._robots_completed_rescue,
            self._push_chat,
            self._sanitize_llm_assignments,
        )

    def check_and_trigger_replan(self, rescue_mgr: Any) -> bool:
        with self._lock:
            current_phase = self._phase
        if current_phase != EMOSPhase.DONE:
            return False
        if not rescue_mgr.is_active:
            return False
        # 仅在已选定救援机且其正在执行救援时重规划；MONITORING 等阶段 is_active 亦为 True 但 rescue_robot 为空，
        # 若此时触发会立刻开启第二轮 LLM，导致重复派遣与同目标跳过，表现为多数机器人不动。
        rr = getattr(getattr(rescue_mgr, "state", None), "rescue_robot", None)
        if not rr:
            return False
        if self._replan_triggered:
            return False
        now = time.time()
        if now - self._replan_cooldown < 5.0:
            return False

        self._replan_triggered = True
        self._replan_cooldown = now
        # 救援场景下剩余机器人 + 颜色子任务的分配是确定性约束，直接用本地规则引擎，
        # 跳过 LLM RTT；fallback_assignment 仍会给 rescue_robot 派一条颜色，
        # 工厂派遣层（emos_factory_run / robot_factory_run）会按 rescue_robot_now 跳过其
        # 立即派发，并把任务存入 _pending_emos_for_rescue，待救援结束后再延后执行。
        with self._lock:
            # 复位本轮熔断标记，让 trigger_local_fallback 可以正常进入；phase 此刻为 DONE，
            # trigger_local_fallback 会直接把结果入队并保持 DONE。
            self._fallback_dispatched_for_round = False
        self._last_trigger_started_at = now

        self._push_chat(
            "emos",
            "EMOS 动态重规划",
            f"⚡ 检测到新的救援事件！救援机器人 {rescue_mgr.state.rescue_robot} 已被标记为不可用，"
            f"使用本地规则引擎重新分配受影响的子任务（跳过 LLM 以提速）。",
        )
        self.trigger_local_fallback(reason="救援动态重规划")
        return True

    def reset_replan_flag(self) -> None:
        self._replan_triggered = False

    def _generate_report(self, assignments: Dict[str, RobotTask]) -> None:
        if not self._write_html_report:
            return
        try:
            out_dir = Path(self._log_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = out_dir / f"emos_rescue_report_{ts}.html"

            def esc(s: str) -> str:
                return html.escape(str(s or ""), quote=True)

            rows = []
            for rt in assignments.values():
                profile = self._scenario.robot_profiles.get(rt.robot_name)
                caps = "、".join(profile.capabilities) if profile else ""
                rows.append(
                    f"<tr><td>{esc(rt.robot_name)}</td>"
                    f"<td>{esc(rt.subtask_name)}</td>"
                    f"<td>{esc(rt.subtask_desc[:150])}</td>"
                    f"<td>({rt.target_xy[0]:.1f}, {rt.target_xy[1]:.1f})</td>"
                    f"<td style='font-size:11px;color:#9ca3af;'>{esc(caps)}</td></tr>"
                )

            body = "\n".join(rows)
            content = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8"><title>EMOS 救援报告</title>
<style>
body{{font-family:sans-serif;background:#0b1120;color:#e5e7eb;padding:24px;}}
.card{{background:rgba(15,23,42,.96);border:1px solid rgba(148,163,184,.25);border-radius:16px;padding:20px;margin-bottom:16px;}}
h1{{color:#f9fafb;font-size:22px;}}
table{{width:100%;border-collapse:collapse;font-size:13px;margin-top:10px;}}
th,td{{border-bottom:1px solid #1f2937;padding:8px 6px;text-align:left;}}
th{{color:#9ca3af;font-size:12px;}}
.meta{{font-size:12px;color:#9ca3af;margin-bottom:8px;}}
</style></head><body>
<div class="card">
<div class="meta">EMOS 救援任务报告 · {esc(datetime.now().strftime('%Y-%m-%d %H:%M:%S'))} · {esc(self._scenario.anchor_label)} #{self._hazard_id} ({self._hazard_pos[0]:.1f}, {self._hazard_pos[1]:.1f})</div>
<h1>多机器人救援任务分配</h1>
<table><thead><tr><th>机器人</th><th>子任务</th><th>描述</th><th>目标坐标</th><th>能力</th></tr></thead>
<tbody>{body}</tbody></table>
</div></body></html>"""

            path.write_text(content, encoding="utf-8")
            _sys(f"EMOS 报告已生成: {path}")
        except Exception as e:
            _sys(f"生成报告失败: {e}")
