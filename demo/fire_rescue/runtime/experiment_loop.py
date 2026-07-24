# Copyright (c) 2022-2025. Robot-only baseline main loop.
"""纯机器人对照组主循环：EMOS 自动讨论 → 自动派发，Dashboard 只读观察。

与 emos_factory_run.py 的区别：
  - auto_confirm_emos 始终为 True
  - 不处理 dashboard 改派/重规划/目标/救援审批（全部自动）
  - 救援自动批准
  - dashboard 为只读观察模式（使用 dashboard/index.html）
"""

from __future__ import annotations

import copy
import math
import random
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from algorithm.emos.types import compute_subtask_positions


class _DashStub:
    def push_chat(self, *a, **k): pass
    def update_emos(self, *a, **k): pass
    def update_hazard(self, *a, **k): pass
    def clear_hazards(self): pass
    def update_sim(self, *a, **k): pass
    def update_robot(self, *a, **k): pass
    def update_robot_tasks(self, *a, **k): pass
    def update_rescue(self, *a, **k): pass
    def get_pending_goals(self): return []
    def get_pending_reassignments(self): return []
    def get_pending_rescue_approvals(self): return []
    def get_pending_replan_requests(self): return False
    def get_pending_manual_task_end_commands(self): return []


def run_robot_factory_platform(
    *,
    simulation_app: Any,
    env: Any,
    base_env: Any,
    env_cfg: Any,
    args_cli: Any,
    emos_mgr: Any,
    scenario: Any,
    map_yaml: str,
    repo_root: Any,
    device: str,
    num_envs: int,
    possible_agents: List[str],
    auto_fire_delay_s: float,
    interpret_factory_tasks_fn: Callable[..., List[Any]],
    fixed_auto_hazard_id: Optional[int] = None,
    trial_index: int = 1,
    total_trials: int = 1,
    skip_http_init: bool = False,
) -> int:
    from pathlib import Path
    import omni.kit.app
    import omni.timeline
    import omni.usd
    from . import settings as cfg
    from .algorithm_adapter import EmosFactoryNavBridge
    from .fire import create_hazard_column
    from .input import InputHandler
    from .mission_success import unified_mission_success
    from .navigation import RobotNavController, RobotTask
    from .obstacle_rescue import ObstacleRescueManager, RescuePhase
    from .sim_helpers import LightFlashController, get_robot_pos, get_robot_pose_tensors
    from .ur5 import UR5Manager
    import queue

    # Dashboard：只读观察模式
    dashboard = not getattr(args_cli, "headless", False)
    _dsrv: Any
    _stream: Any = None
    if dashboard:
        from ..dashboard import server as _dsrv
        from ..dashboard import stream as _stream

        if not skip_http_init:
            def _kill_port_holders(*ports: int) -> None:
                import os, signal, subprocess
                my_pid = os.getpid()
                for port in ports:
                    try:
                        out = subprocess.check_output(["ss", "-ltnp"], text=True, stderr=subprocess.DEVNULL)
                        for line in out.splitlines():
                            if f":{port} " not in line:
                                continue
                            for token in line.split(","):
                                if token.strip().startswith("pid="):
                                    pid = int(token.strip().split("=")[1])
                                    if pid != my_pid:
                                        os.kill(pid, signal.SIGKILL)
                    except Exception:
                        pass

            _kill_port_holders(cfg.STREAM_PORT, cfg.DATA_PORT)
            time.sleep(0.5)
            _stream.start(port=cfg.STREAM_PORT, fps=cfg.STREAM_FPS, jpeg_quality=cfg.STREAM_JPEG_QUALITY)
            import yaml
            with open(map_yaml) as _f:
                _map_cfg = yaml.safe_load(_f)
            png_path = cfg.resolve_factory_map_png_path(map_yaml, _map_cfg)
            _robot_html = str(Path(__file__).resolve().parents[1] / "dashboard" / "index.html")
            _dsrv.configure(map_png=str(png_path), map_yaml=_map_cfg, html_path=_robot_html)
            _dsrv.start(port=cfg.DATA_PORT)
            print("[Robot Dashboard] 请在浏览器打开: http://127.0.0.1:8767/")
    else:
        _dsrv = _DashStub()

    emos_mgr._push_chat = _dsrv.push_chat

    if dashboard:
        _extensions = ("omni.anim.navigation.core", "omni.anim.graph.core")
        _ext_mgr = omni.kit.app.get_app().get_extension_manager()
        for _ext in _extensions:
            try:
                _ext_mgr.set_extension_enabled_immediate(_ext, True)
            except Exception:
                pass
        for _ in range(10):
            simulation_app.update()

    nav_bridge = EmosFactoryNavBridge(
        base_env, possible_agents, env_cfg, device, num_envs, map_yaml,
        waypoint_step=float(getattr(args_cli, "waypoint_step", 1.0)),
        prefer_astar=bool(getattr(args_cli, "nav_prefer_astar", True)),
        inflation_radius_cells=int(getattr(cfg, "NAV_INFLATION_RADIUS_CELLS", 14)),
    )
    navigator = nav_bridge.session.navigator

    nav = RobotNavController(base_env, possible_agents, device, num_envs, navigator, env_cfg,
                             nav_session=nav_bridge.session)
    ur5 = UR5Manager(
        base_env,
        enable_extinguisher_visual_follow=not bool(getattr(args_cli, "headless", False)),
        enable_extinguisher_stage_attach=not bool(getattr(args_cli, "headless", False)),
    )
    remote_key_queue: queue.Queue = queue.Queue()
    inp = InputHandler(remote_key_queue)

    if hasattr(env_cfg, "people"):
        env_cfg.people = None

    stage = omni.usd.get_context().get_stage()
    light = LightFlashController(stage)

    emos_agents = [
        a for a in possible_agents
        if a in base_env.scene.articulations and not str(a).startswith("person")
    ]

    extinguisher_robot: Optional[str] = None
    rescue_channel_robot: Optional[str] = None
    _rc_press_started = False
    _rc_channel_arm_done = False
    _rc_final_approach = False
    _rc_final_approach_start = 0.0
    _extinguisher_fire_nav_dispatched = False
    _extinguisher_task1_completed = False
    _last_rescue_robot: Optional[str] = None
    _pending_emos_for_rescue: Dict[str, RobotTask] = {}
    _rally_dispatched = False

    def _rescue_llm_kwargs():
        from .llm_presets import DEFAULT_EMOS_LLM_PRESET, preset_to_group_discussion_kwargs
        return preset_to_group_discussion_kwargs(
            getattr(args_cli, "emos_llm_preset", DEFAULT_EMOS_LLM_PRESET)
        )

    obstacle_mgr = ObstacleRescueManager(
        base_env=base_env, nav_controller=nav, push_chat_fn=_dsrv.push_chat,
        update_rescue_fn=_dsrv.update_rescue, stage=stage, simulation_app=simulation_app,
        navigator=navigator, extinguisher_robot=extinguisher_robot, ur5=ur5,
        planner_mode="robots",
        rescue_llm_kwargs_getter=_rescue_llm_kwargs,
        obstacle_ur5_reach_enabled=not bool(getattr(args_cli, "headless", False)),
        allow_runtime_app_update=bool(dashboard),
    )
    obstacle_mgr.start_monitoring()

    if dashboard:
        timeline = omni.timeline.get_timeline_interface()
        if not timeline.is_playing():
            timeline.play()
        for _ in range(5):
            simulation_app.update()

    nav.setup_initial_navigation()
    nav.set_initial_goal_positions()

    if dashboard and _stream is not None:
        _robot_top_camera_path = "/World/RobotTopCamera"
        _robot_top_camera_height = 102.0

        def _ensure_robot_top_camera() -> str:
            """为只读网页视频流创建 102m 俯视相机。"""
            try:
                from pxr import UsdGeom, Gf
                cam_path = _robot_top_camera_path
                cam = UsdGeom.Camera.Get(stage, cam_path)
                if not cam:
                    cam = UsdGeom.Camera.Define(stage, cam_path)
                cam_prim = cam.GetPrim()
                xform = UsdGeom.Xformable(cam_prim)
                xform.ClearXformOpOrder()
                # 俯视工厂中心区域：默认朝 -Z（Z-up 场景即向下看）
                xform.AddTranslateOp().Set(Gf.Vec3d(-1.5, -0.8, _robot_top_camera_height))
                cam.CreateClippingRangeAttr().Set(Gf.Vec2f(0.1, 2000.0))
                simulation_app.update()
                return cam_path
            except Exception as e:
                print(f"[Robot Baseline] 创建俯视相机失败，回退默认视角: {e}")
                return "/OmniverseKit_Persp"

        _camera_path = _ensure_robot_top_camera()
        _stream.setup_capture(resolution=cfg.STREAM_RESOLUTION, camera_path=_camera_path)
        print(f"\n[Robot Baseline] 观察页: http://127.0.0.1:{cfg.DATA_PORT}/\n")

    _dsrv.update_sim(
        step=0,
        fps=0,
        runtime_s=0,
        running=True,
        virtual_time_s=0.0,
        mode="robots",
        result="running",
        fail_reason="",
        manual_task_status={
            "task1": {"state": "pending"},
            "task2": {"state": "pending"},
            "emergency_task": {"state": "pending"},
            "formation": {
                "near_fire_count": 0,
                "near_fire_required": 3,
                "radius_m": 3.0,
                "extinguisher_in_radius": False,
                "delivery_complete": False,
                "fire_active": False,
            },
        },
        active_hazard_id=0,
    )
    emos_mgr.register_rescue_manager(obstacle_mgr)
    ur5.set_extinguisher_drop_callback(obstacle_mgr.on_extinguisher_dropped)

    def _start_patrol():
        for agent in emos_agents:
            nav.is_patrolling[agent] = False
            nav._start_patrol(agent)

    _start_patrol()

    t0 = time.time()
    patrol_started_at = t0
    step_counter = 0
    fps_counter = 0
    fps_last_t = time.time()
    # 运行时长改为"健康帧 dt 累加"：单帧 wall-clock 间隔超过阈值视为卡顿/挂起，
    # 不计入 runtime_s。这样 dashboard 上仿真步停止时，运行时长也不会"补涨"。
    # 正常 IsaacSim ~17 FPS（帧 dt ~ 60ms），1.0s 阈值留有 17x 余量。
    _active_runtime_s = 0.0
    _prev_frame_real_ts: Optional[float] = None
    _RUNTIME_STALL_THRESHOLD_S = 1.0

    active_hazards: Dict[str, Tuple[float, float, float]] = {}
    _fire_cleared = False
    auto_fire_done = False
    _record_hazard_id: int = 0
    emos_auto_dispatched = False
    _emos_triggered = False
    _replan_defer_ts: Optional[float] = None
    _suppress_patrol_pending = False
    _pending_emos_assignments: Optional[Dict[str, Any]] = None
    _pending_emos_dispatch_at_sim_s: Optional[float] = None
    _pending_rescue_approval: Optional[Dict[str, Any]] = None

    max_steps = max(0, int(getattr(args_cli, "max_steps", 0)))
    step_dt = float(base_env.step_dt)
    do_real_time = bool(getattr(args_cli, "real_time", False))
    sim_timeout_s = 1000.0
    # LLM 首轮讨论超时熔断：超过该秒数仍未拿到 LLM 结果，立即用本地规则引擎派遣，
    # LLM 结果迟到后会被 EMOSDiscussionManager._worker 自行丢弃，避免覆盖派遣。
    LLM_FALLBACK_TIMEOUT_S = 160.0

    # ── 实验统计状态（纯机器人）───────────────────────────────────────────────
    _fire_start_ts: Optional[float] = None
    _fire_pos: Optional[Tuple[float, float, float]] = None
    # 时间统计统一使用真实墙钟时间（time.time()）。变量名沿用 _sim_time_since_fire_s 以
    # 保持下游消费方（结果 JSON、HTML、xlsx）的字段口径不变。
    _sim_time_since_fire_s: float = 0.0
    _prev_loop_real_ts: Optional[float] = None
    _robot_moving_time_s: Dict[str, float] = {a: 0.0 for a in emos_agents}
    _robot_standby_time_s: Dict[str, float] = {a: 0.0 for a in emos_agents}
    _robot_end_ts: Dict[str, float] = {}
    _robot_goal_done: Dict[str, bool] = {a: False for a in emos_agents}
    _robot_last_task_done: Dict[str, bool] = {a: False for a in emos_agents}
    _robot_in_fire_range: Dict[str, bool] = {a: False for a in emos_agents}
    _sim_result: str = "running"  # running | success | fail
    _sim_fail_reason: Optional[str] = None
    _should_stop_sim: bool = False
    _obstacle_spawn_sim_s: Optional[float] = None
    _move_obstacle_exec_sim_s: Optional[float] = None
    _press_button_exec_sim_s: Optional[float] = None
    _carry_ext_exec_sim_s: Optional[float] = None

    def _current_virtual_time_s() -> float:
        # 使用步数 * step_dt 作为统一仿真时间轴，确保延迟按仿真秒生效。
        return float(step_counter) * float(step_dt)

    def _is_robot_arm_busy(rn: str) -> bool:
        if getattr(ur5, "is_extinguisher_active", False) and getattr(ur5, "_ext_robot_name", "") == rn:
            return True
        if rn == "scout_1" and getattr(getattr(ur5, "scout_1_pick", None), "active", False):
            return True
        if getattr(ur5, "is_obstacle_rescue_reach_active", False) and getattr(ur5, "_obstacle_rescue_reach_robot", "") == rn:
            return True
        return False

    def _is_waiting_idle(rn: str) -> bool:
        tq = nav.task_queues.get(rn)
        queue_empty = (tq is None) or tq.is_empty()
        return (
            queue_empty
            and nav.waiting_for_task.get(rn, False)
            and not nav.navigator.is_active(rn)
            and not nav.is_patrolling.get(rn, False)
        )

    def _distance_to_fire_xy(rn: str) -> Optional[float]:
        if _fire_pos is None:
            return None
        try:
            px, py, _ = get_robot_pos(base_env, rn)
            return float(math.hypot(float(px) - float(_fire_pos[0]), float(py) - float(_fire_pos[1])))
        except Exception:
            return None

    def _mark_fire_if_needed(pos: Tuple[float, float, float]) -> None:
        nonlocal _fire_start_ts, _fire_pos
        if _fire_start_ts is None:
            _fire_start_ts = time.time()
            _fire_pos = (float(pos[0]), float(pos[1]), float(pos[2]))
            try:
                nav.set_known_fire_xy((float(pos[0]), float(pos[1])))
            except Exception:
                pass

    def _clear_active_fire_hazards() -> None:
        """任务成功后清理场景火源（危险柱）并同步 Dashboard。"""
        nonlocal _fire_cleared
        if _fire_cleared:
            return
        try:
            import omni.kit.commands as okc
            for op in list(active_hazards.keys()):
                try:
                    okc.execute("DeletePrims", paths=[op])
                except Exception:
                    pass
                active_hazards.pop(op, None)
            _dsrv.clear_hazards()
            # 通知前端火源事件已结束（不再展示分配）
            try:
                _dsrv.update_emos("idle", hazard_id=0, assignments={})
            except Exception:
                pass
            for _ in range(2):
                simulation_app.update()
            _fire_cleared = True
            print("[Robot Baseline] ✅ 任务完成，火源已清除。")
        except Exception as e:
            print(f"[Robot Baseline] 清理火源失败: {e}")

    def _refresh_robot_goal_state(now_ts: float) -> None:
        if _fire_start_ts is None:
            return
        for rn in emos_agents:
            if rn in _robot_end_ts:
                continue
            last_task_done = _is_waiting_idle(rn) and (not _is_robot_arm_busy(rn))
            dist = _distance_to_fire_xy(rn)
            in_fire_range = (dist is not None) and (dist <= 3.0)
            _robot_last_task_done[rn] = bool(last_task_done)
            _robot_in_fire_range[rn] = bool(in_fire_range)
            if last_task_done and in_fire_range:
                _robot_goal_done[rn] = True
                _robot_end_ts[rn] = now_ts
                _dsrv.push_chat("emos", "EMOS 统计", f"📌 {rn} 已完成末任务并抵达火源3m范围，停止计入利用率。")

    def _task_status_payload() -> Dict[str, Any]:
        def _state(done: bool, in_progress: bool) -> Dict[str, Any]:
            if done:
                return {"state": "done"}
            if in_progress:
                return {"state": "in_progress"}
            return {"state": "pending"}

        near_cnt = sum(1 for rn in emos_agents if _distance_to_fire_xy(rn) is not None and _distance_to_fire_xy(rn) <= 3.0)
        ext_in_radius = False
        if extinguisher_robot and extinguisher_robot in emos_agents:
            _dist = _distance_to_fire_xy(extinguisher_robot)
            ext_in_radius = (_dist is not None) and (_dist <= 3.0)

        rescue_state = obstacle_mgr.state if obstacle_mgr is not None else None
        rescue_phase = rescue_state.phase if rescue_state is not None else None
        _obstacle_actually_spawned = bool(getattr(rescue_state, "obstacle_spawned", False))
        emergency_done = rescue_phase == RescuePhase.COMPLETED
        emergency_in_progress = rescue_phase in (
            RescuePhase.OBSTACLE_DROPPED,
            RescuePhase.CARTER_BLOCKED,
            RescuePhase.DISCUSSING,
            RescuePhase.AWAITING_APPROVAL,
            RescuePhase.RESCUE_DISPATCHED,
            RescuePhase.REMOVING_OBSTACLE,
            RescuePhase.DRAGGING,
            RescuePhase.CARTER_RESUMING,
            RescuePhase.RESCUE_RETURNING,
        )

        task1_done = bool(_extinguisher_task1_completed)
        task1_in_progress = bool(
            extinguisher_robot
            and (
                _carry_ext_exec_sim_s is not None
                or ur5.is_extinguisher_grabbed
                or getattr(ur5, "_ext_grabbed", False)
            )
            and not task1_done
        )
        task2_done = bool(_rc_channel_arm_done)
        task2_in_progress = bool(
            rescue_channel_robot and (_press_button_exec_sim_s is not None or _rc_press_started or _rc_final_approach) and not task2_done
        )

        return {
            "task1": _state(task1_done, task1_in_progress),
            "task2": _state(task2_done, task2_in_progress),
            "emergency_task": (
                _state(emergency_done, emergency_in_progress and not emergency_done)
                if _obstacle_actually_spawned
                else {"state": "skipped"}
            ),
            "formation": {
                "near_fire_count": int(near_cnt),
                "near_fire_required": 3,
                "radius_m": 3.0,
                "extinguisher_in_radius": bool(ext_in_radius),
                "delivery_complete": bool(_extinguisher_task1_completed),
                "fire_active": _fire_pos is not None,
            },
        }

    def _assignments_dict_from_fixed(fixed):
        out = {}
        for rn, rt in fixed.items():
            out[rn] = {"subtask": rt.subtask_name, "colour": rt.subtask_colour, "target_xy": list(rt.target_xy)}
        return out

    def _reconcile_rescue_channel_assignee(new_green):
        nonlocal rescue_channel_robot, _rc_press_started, _rc_channel_arm_done, _rc_final_approach
        old = rescue_channel_robot
        if old == new_green:
            return
        if old:
            nav.hold_position.discard(old)
        _rc_press_started = False
        _rc_channel_arm_done = False
        _rc_final_approach = False
        rescue_channel_robot = new_green

    def _dispatch_assignments(assignments_raw, merge_ui=True):
        nonlocal _suppress_patrol_pending
        from algorithm.emos.types import RobotTask as AlgoRobotTask
        filtered = emos_mgr.filter_dashboard_reassignments(assignments_raw)
        if not filtered:
            return 0
        pos = compute_subtask_positions(scenario, emos_mgr._hazard_pos)
        algo_tasks = {}
        for rn, info in filtered.items():
            if rn not in base_env.scene.articulations:
                continue
            xy = info.get("target_xy")
            if not xy or len(xy) < 2:
                continue
            gx, gy = float(xy[0]), float(xy[1])
            algo_tasks[rn] = AlgoRobotTask(
                robot_name=rn, subtask_colour=str(info.get("colour", "")),
                subtask_name=str(info.get("subtask", "")), subtask_desc="",
                target_xy=(gx, gy),
            )
        _preserve_raw_llm = False
        try:
            _preserve_raw_llm = bool(emos_mgr.should_preserve_raw_llm_assignments())
        except Exception:
            _preserve_raw_llm = False
        if algo_tasks and not _preserve_raw_llm:
            fixed_algo = emos_mgr._validate_and_fix_green_assignment(copy.deepcopy(algo_tasks), pos)
            for rn, rt in fixed_algo.items():
                if rn in filtered:
                    filtered[rn] = {"subtask": rt.subtask_name, "colour": rt.subtask_colour, "target_xy": list(rt.target_xy)}
            new_green = next((rn for rn, rt in fixed_algo.items() if rt.subtask_colour == "green"), None)
            _reconcile_rescue_channel_assignee(new_green)
        else:
            new_green = next((rn for rn, info in filtered.items() if str(info.get("colour", "")) == "green"), None)
            _reconcile_rescue_channel_assignee(new_green)

        rescue_robot_now = obstacle_mgr.state.rescue_robot if obstacle_mgr.is_active else None
        _batch_tasks: Dict[str, RobotTask] = {}
        _skip_batch: set = set()
        for rn, info in filtered.items():
            if rn not in base_env.scene.articulations:
                continue
            xy = info.get("target_xy")
            if not xy or len(xy) < 2:
                continue
            gx, gy = float(xy[0]), float(xy[1])
            task = RobotTask(
                task_id=f"emos_{rn}_{int(time.time() * 1000)}", task_type="emos",
                subtask_name=str(info.get("subtask", "")), subtask_colour=str(info.get("colour", "")),
                target_xy=(gx, gy), waypoints=[], priority=0,
            )
            if rn == rescue_robot_now:
                _pending_emos_for_rescue[rn] = task
                _skip_batch.add(rn)
                continue
            _batch_tasks[rn] = task
        queued_cnt = len(_batch_tasks)
        cnt = nav.batch_dispatch_tasks(_batch_tasks, skip_agents=_skip_batch)
        _dsrv.update_emos("done", hazard_id=emos_mgr._hazard_id, assignments=dict(filtered))
        # 不在派发瞬间切换到“最终待命”模式，避免 no-path 机器人被误判为完成后待命。
        # 等待异步规划结果确认至少一台成功入队后，再启用 suppress_patrol_after_emos。
        _suppress_patrol_pending = queued_cnt > 0
        if not _suppress_patrol_pending:
            nav.suppress_patrol_after_emos = False
        return cnt if cnt > 0 else queued_cnt

    # ═══════════════════════ 主循环 ═══════════════════════════════════════════
    while simulation_app.is_running():
        if max_steps > 0 and step_counter >= max_steps:
            _sim_result = "fail"
            _sim_fail_reason = f"达到 max_steps={max_steps} 仍未完成"
            break
        step_counter += 1
        loop_t0 = time.perf_counter()
        keys = inp.poll()

        # ── 处理网页“手动结束任务”（按失败记）──
        for cmd in _dsrv.get_pending_manual_task_end_commands():
            end_type = str(cmd.get("type", "")).strip()
            if end_type == "simulation_end":
                _sim_result = "fail"
                _sim_fail_reason = "操作员在网页端手动结束任务"
                _dsrv.push_chat("emos", "EMOS 统计", "🛑 操作员手动结束本轮任务，按失败记录。")
                _should_stop_sim = True
                break

        if _should_stop_sim:
            break

        # ── 自动火源 ──
        if not auto_fire_done:
            if time.time() - patrol_started_at >= auto_fire_delay_s:
                auto_fire_done = True
                hid = int(fixed_auto_hazard_id) if fixed_auto_hazard_id is not None else random.randint(1, 5)
                _record_hazard_id = hid
                pos = cfg.HAZARD_POSITIONS[hid]
                prim_path = f"/World/Factory_Hazard_{hid}"
                try:
                    import omni.kit.commands as okc
                    for op in list(active_hazards.keys()):
                        okc.execute("DeletePrims", paths=[op])
                        _dsrv.clear_hazards()
                        del active_hazards[op]
                    if dashboard:
                        simulation_app.update()
                    create_hazard_column(stage, prim_path, pos, simulation_app.update if dashboard else (lambda: None))
                    active_hazards[prim_path] = pos
                    if dashboard:
                        for _ in range(3):
                            simulation_app.update()
                    print(f"[自动火源] #{hid} ({pos[0]:.1f}, {pos[1]:.1f})")
                    _mark_fire_if_needed(pos)
                    emos_auto_dispatched = False
                    _dsrv.update_emos("running", hazard_id=hid)
                    _dsrv.update_hazard(hid, pos, active=True)
                    if not _emos_triggered:
                        emos_mgr.trigger(hazard_id=hid, hazard_pos=pos)
                        _emos_triggered = True
                except Exception as e:
                    print(f"[自动火源] 失败: {e}")

        # ── LLM 首轮讨论 RTT 长尾时熔断：直接用本地规则引擎出方案，迟到的 LLM 结果会被丢弃 ──
        if (
            (not emos_auto_dispatched)
            and emos_mgr.is_running
            and emos_mgr.seconds_since_trigger() >= LLM_FALLBACK_TIMEOUT_S
        ):
            try:
                emos_mgr.trigger_local_fallback(reason=f"LLM 超过 {LLM_FALLBACK_TIMEOUT_S:.0f}s 未返回")
            except Exception as _fb_e:
                print(f"[EMOS] 熔断本地规则失败: {_fb_e}")

        # ── EMOS 轮询 → 自动派发 ──
        if not emos_auto_dispatched:
            emos_result = emos_mgr.poll()
            if emos_result is not None:
                hz = emos_mgr._hazard_pos
                positions = compute_subtask_positions(scenario, hz)
                _preserve_raw_llm = False
                try:
                    _preserve_raw_llm = bool(emos_mgr.should_preserve_raw_llm_assignments())
                except Exception:
                    _preserve_raw_llm = False
                if _preserve_raw_llm:
                    emos_result_fixed = emos_result
                    ext_robot = next((rn for rn, rt in emos_result_fixed.items() if rt.subtask_colour == "yellow"), None)
                else:
                    emos_result_fixed, ext_robot = emos_mgr._validate_and_fix_yellow_assignment(emos_result, positions)
                    emos_result_fixed = emos_mgr._validate_and_fix_green_assignment(emos_result_fixed, positions)
                if ext_robot:
                    extinguisher_robot = ext_robot
                    obstacle_mgr.extinguisher_robot = ext_robot
                    _extinguisher_fire_nav_dispatched = False
                    _extinguisher_task1_completed = False
                new_green = next((rn for rn, rt in emos_result_fixed.items() if rt.subtask_colour == "green"), None)
                _reconcile_rescue_channel_assignee(new_green)
                ass = _assignments_dict_from_fixed(emos_result_fixed)
                _dsrv.update_emos("done", hazard_id=emos_mgr._hazard_id, assignments=ass)
                _dsrv.push_chat("emos", "EMOS 系统", "📋 讨论完成，纯机器人模式自动执行。")
                try:
                    for it in interpret_factory_tasks_fn(emos_result_fixed):
                        print(f"  [解读] {it.robot_name} → {it.event_kind.value}")
                except Exception:
                    pass
                _pending_emos_assignments = ass
                _pending_emos_dispatch_at_sim_s = _current_virtual_time_s() + 3.0

        if (not emos_auto_dispatched) and (_pending_emos_assignments is not None):
            if _pending_emos_dispatch_at_sim_s is not None and _current_virtual_time_s() >= _pending_emos_dispatch_at_sim_s:
                cnt = _dispatch_assignments(_pending_emos_assignments, merge_ui=True)
                emos_auto_dispatched = True
                _pending_emos_assignments = None
                _pending_emos_dispatch_at_sim_s = None
                _dsrv.push_chat("emos", "EMOS 导航派遣", f"🎯 自动派遣 {cnt} 个机器人。")

        # ── 救援自动处理 ──
        obstacle_mgr.update()
        _phase_name = str(getattr(getattr(obstacle_mgr, "state", None), "phase", "")).lower()
        if getattr(obstacle_mgr.state, "obstacle_spawned", False) and _fire_start_ts is not None and _obstacle_spawn_sim_s is None:
            _obstacle_spawn_sim_s = float(_sim_time_since_fire_s)
        if "removing_obstacle" in _phase_name and _move_obstacle_exec_sim_s is None and _fire_start_ts is not None:
            _move_obstacle_exec_sim_s = float(_sim_time_since_fire_s)
        # 自动批准救援（纯机器人模式：不等待人类审批）
        if obstacle_mgr.is_active and hasattr(obstacle_mgr, 'state'):
            if getattr(obstacle_mgr.state, 'phase', None) is not None:
                if obstacle_mgr.state.phase == RescuePhase.AWAITING_APPROVAL:
                    rec = getattr(obstacle_mgr.state, "rescue_robot", None)
                    if not rec:
                        try:
                            rec = obstacle_mgr._recommended_candidate_name(obstacle_mgr.rescue_candidates)
                        except Exception:
                            rec = None
                    if rec:
                        if _pending_rescue_approval is None:
                            _pending_rescue_approval = {
                                "robot": rec,
                                "approve_at_sim_s": _current_virtual_time_s() + 3.0,
                            }
                        else:
                            _pending_rescue_approval["robot"] = rec
                else:
                    _pending_rescue_approval = None
            else:
                _pending_rescue_approval = None
        else:
            _pending_rescue_approval = None

        if obstacle_mgr.is_active and _pending_rescue_approval is not None:
            approve_at_sim_s = float(_pending_rescue_approval.get("approve_at_sim_s", 0.0))
            if _current_virtual_time_s() >= approve_at_sim_s:
                _rec_robot = str(_pending_rescue_approval.get("robot", "")).strip()
                if _rec_robot:
                    obstacle_mgr.handle_rescue_approval({"action": "approve", "robot": _rec_robot})
                    _dsrv.push_chat("emos", "EMOS 系统", f"🤖 自动批准 {_rec_robot} 执行救援（纯机器人模式）。")
                _pending_rescue_approval = None

        _cur_rescue_robot = obstacle_mgr.state.rescue_robot if obstacle_mgr.is_active else None
        if _cur_rescue_robot != _last_rescue_robot:
            _completed_rescue = _last_rescue_robot
            _last_rescue_robot = _cur_rescue_robot
            if _cur_rescue_robot:
                _dsrv.update_robot_tasks(name=_cur_rescue_robot, task_queue=[{
                    "task_id": "rescue_obstacle", "task_type": "rescue",
                    "subtask_name": "障碍物清除", "subtask_colour": "orange",
                    "target_xy": list(obstacle_mgr.state.obstacle_position[:2]),
                }], current_task_index=0, is_patrolling=False)
            elif _completed_rescue:
                emos_mgr.register_rescue_completed(_completed_rescue)
                obstacle_mgr.mark_rescue_completed(_completed_rescue)
                if _completed_rescue in _pending_emos_for_rescue:
                    _deferred = _pending_emos_for_rescue.pop(_completed_rescue)
                    _tq = nav.task_queues.get(_completed_rescue)
                    _cur = _tq.peek() if _tq and not _tq.is_empty() else None
                    _already = (
                        _cur is not None
                        and getattr(_cur, "task_type", "") == "emos"
                        and getattr(_cur, "target_xy", None) == _deferred.target_xy
                    )
                    if not _already:
                        nav.interrupt_patrol_with_task(_completed_rescue, _deferred, priority=False)
                    if _deferred.subtask_colour == "yellow":
                        extinguisher_robot = _completed_rescue
                        obstacle_mgr.extinguisher_robot = _completed_rescue
                emos_mgr.reset_replan_flag()

        # ── 动态重规划（纯机器人自动触发）──
        if emos_auto_dispatched and _replan_defer_ts is None:
            if emos_mgr.check_and_trigger_replan(obstacle_mgr):
                _replan_defer_ts = time.time() + 25.0
                _dsrv.push_chat("emos", "EMOS 系统", "⚡ 动态重规划已触发…")

        if _replan_defer_ts is not None and time.time() >= _replan_defer_ts:
            emos_auto_dispatched = False
            _replan_defer_ts = None

        # ── 按键 ──
        _, j5 = keys[5]
        if j5:
            nav.key5_active = True
            nav.key5_segment_indices.clear()
        _, j6 = keys[6]
        if j6:
            light.toggle()
        _, jk = keys["K"]
        if jk:
            nav.all_stopped = not nav.all_stopped
        light.update()

        # ── 集结逻辑 ──
        if not _rally_dispatched and not emos_mgr.is_running and emos_auto_dispatched:
            _hp = emos_mgr._hazard_pos
            _hx, _hy = float(_hp[0]), float(_hp[1])
            if abs(_hx) + abs(_hy) > 0.05:
                _busy_arm = ur5.is_extinguisher_active or ur5.is_obstacle_rescue_reach_active or ur5.scout_1_pick.active
                _queues_empty = all(
                    nav.task_queues.get(_ra) is None or nav.task_queues.get(_ra).is_empty()
                    for _ra in emos_agents
                )
                if _queues_empty and not obstacle_mgr.is_active and not _busy_arm:
                    _rally_dispatched = True
                    _rally_off = {"m20_1": (1.5, 1.2), "m20_2": (-1.5, 1.2), "carter_1": (-2.0, -1.0), "scout_1": (2.0, -1.0)}
                    _dsrv.push_chat("emos", "EMOS 系统", f"📍 子任务已完成，各机前往火源附近集结。")
                    for _rn in emos_agents:
                        nav.hold_position.discard(_rn)
                        _ox, _oy = _rally_off.get(_rn, (0.8, 0.8))
                        _rt = RobotTask(
                            task_id=f"emos_rally_{_rn}", task_type="emos", subtask_name="火源集结",
                            subtask_colour="grey", target_xy=(_hx + _ox, _hy + _oy), waypoints=[], priority=0,
                        )
                        try:
                            nav.interrupt_patrol_with_task(_rn, _rt, priority=False)
                        except Exception:
                            pass

        nav.consume_pending_plans()
        _plan_report = nav.get_and_clear_batch_plan_report()
        if _plan_report:
            _status = str(_plan_report.get("status", ""))
            _ok_agents = list(_plan_report.get("dispatched_agents", []) or [])
            _fail_agents = list(_plan_report.get("failed_agents", []) or [])
            if _status == "error":
                print(f"[EMOS 派发] ⚠️ 批量规划异常: {_plan_report.get('error', '')}")
                _dsrv.push_chat("emos", "EMOS 导航派遣", "⚠️ 批量路径规划异常，已保持巡逻并等待重新分配。")
                _suppress_patrol_pending = False
                nav.suppress_patrol_after_emos = False
            else:
                if _ok_agents:
                    if not nav.suppress_patrol_after_emos:
                        print(f"[EMOS 派发] ✅ 已成功派发 {len(_ok_agents)} 台，启用完成后待命模式。")
                    nav.suppress_patrol_after_emos = True
                    _suppress_patrol_pending = False
                elif _suppress_patrol_pending and _fail_agents:
                    nav.suppress_patrol_after_emos = False
                    _suppress_patrol_pending = False
                    print(
                        "[EMOS 派发] ⚠️ 本轮无机器人成功派发，保持巡逻模式，"
                        f"失败机器人: {','.join(_fail_agents)}"
                    )
                if _fail_agents:
                    _dsrv.push_chat(
                        "emos",
                        "EMOS 导航派遣",
                        f"⚠️ 路径不可达: {', '.join(_fail_agents)}。已转入巡逻重试，避免误待命。",
                    )
        # 保护 extinguisher_robot：yellow 任务到达目标后等待 UR5 抓取，禁止被火源集结覆盖
        if (
            extinguisher_robot
            and not ur5.is_extinguisher_grabbed
            and not getattr(ur5, "_ext_grabbed", False)
            and not _extinguisher_task1_completed
        ):
            _ext_tq = nav.task_queues.get(extinguisher_robot)
            _ext_top = _ext_tq.peek() if _ext_tq else None
            if (
                _ext_top is not None
                and getattr(_ext_top, "subtask_colour", "") == "yellow"
                and nav.waiting_for_task.get(extinguisher_robot, False)
            ):
                nav.hold_position.add(extinguisher_robot)
                nav.suppress_patrol_agents.add(extinguisher_robot)
        # 保护 rescue_channel_robot：green 任务到达按钮附近后禁止被火源集结抢走
        # 仅当队头任务是 green（按钮）任务时才保护，避免阻塞 manual 任务的正常弹出/恢复
        if rescue_channel_robot and not _rc_press_started and not _rc_channel_arm_done:
            _rc_tq = nav.task_queues.get(rescue_channel_robot)
            _rc_top = _rc_tq.peek() if _rc_tq else None
            _rc_is_green = (
                _rc_top is not None
                and getattr(_rc_top, "subtask_colour", "") == "green"
            )
            if _rc_is_green and nav.waiting_for_task.get(rescue_channel_robot, False):
                try:
                    _rcx, _rcy, _ = get_robot_pos(base_env, rescue_channel_robot)
                    _rbx, _rby = cfg.RESCUE_CHANNEL_BUTTON_POS[0], cfg.RESCUE_CHANNEL_BUTTON_POS[1]
                    if math.hypot(_rcx - _rbx, _rcy - _rby) <= 4.0:
                        nav.hold_position.add(rescue_channel_robot)
                        nav.suppress_patrol_agents.add(rescue_channel_robot)
                except Exception:
                    pass
        actions = nav.compute_actions()

        # ── 灭火器逻辑 ──
        if (
            extinguisher_robot
            and (ur5.is_extinguisher_grabbed or getattr(ur5, "_ext_grabbed", False))
            and not _extinguisher_task1_completed
        ):
            emos_mgr.register_extinguisher_completed(extinguisher_robot)
            _extinguisher_task1_completed = True

        for _agent in emos_agents:
            _tq = nav.task_queues.get(_agent)
            if _tq is None:
                continue
            _cur = _tq.peek()
            if _cur and _cur.task_type == "retrieve_ext":
                if f"{_agent}_nav_arrived" in nav.events:
                    ur5.restart_extinguisher_pickup(_agent)
                    _tq.pop()

        if extinguisher_robot and not ur5.is_extinguisher_active:
            if f"{extinguisher_robot}_nav_arrived" in nav.events:
                current_task = nav.task_queues.get(extinguisher_robot, None)
                current_task = current_task.peek() if current_task else None
                if current_task is None or current_task.subtask_colour == "yellow":
                    ur5.start_extinguisher_pickup(extinguisher_robot)
                    if _carry_ext_exec_sim_s is None and _fire_start_ts is not None:
                        _carry_ext_exec_sim_s = float(_sim_time_since_fire_s)
                    nav.hold_position.add(extinguisher_robot)

        if extinguisher_robot and extinguisher_robot in nav.hold_position:
            _ext_tq = nav.task_queues.get(extinguisher_robot, None)
            _ext_task = _ext_tq.peek() if _ext_tq else None
            _is_yellow_pick_phase = bool(
                _ext_task is not None
                and (
                    getattr(_ext_task, "subtask_colour", "") == "yellow"
                    or getattr(_ext_task, "task_type", "") == "retrieve_ext"
                )
            )
            # 仅在黄色抓取阶段自动解 hold，避免误清 fire_plan_failed 等后续任务 hold。
            if _is_yellow_pick_phase:
                if ur5.is_extinguisher_grabbed:
                    nav.hold_position.discard(extinguisher_robot)
                elif not ur5.is_extinguisher_active:
                    nav.hold_position.discard(extinguisher_robot)

        # 子任务1改为“拿到灭火器即完成”，但仍需执行最终任务：前往火源区（3m内）。
        if (
            extinguisher_robot
            and ur5.is_extinguisher_grabbed
            and not _extinguisher_fire_nav_dispatched
            and emos_mgr._hazard_id
        ):
            hx = float(emos_mgr._hazard_pos[0])
            hy = float(emos_mgr._hazard_pos[1])
            ox, oy = cfg.FIRE_EXTINGUISHER_DELIVERY_OFFSET_XY
            tx, ty = hx + ox, hy + oy
            _extinguisher_fire_nav_dispatched = True
            _final_fire = RobotTask(
                task_id="emos_extinguisher_final_fire_zone",
                task_type="emos",
                subtask_name="最终任务：前往火源区",
                subtask_colour="grey",
                target_xy=(tx, ty),
                waypoints=[],
                priority=0,
            )
            nav.interrupt_patrol_with_task(extinguisher_robot, _final_fire, priority=False)
            _dsrv.push_chat("emos", "EMOS 系统", f"🔥 {extinguisher_robot} 前往火源区 ({tx:.1f}, {ty:.1f})")

        # ── 救援通道按钮 ──
        _rc_arm_plane_dist_m = 4.0
        _rc_final_approach_trigger_dist = 1.2
        _rc_final_approach_timeout_s = 12.0
        _rc_final_approach_speed = 0.15
        if rescue_channel_robot and not _rc_press_started and not _rc_channel_arm_done:
            current_task = nav.task_queues.get(rescue_channel_robot, None)
            current_task = current_task.peek() if current_task else None
            if current_task is None or current_task.subtask_colour == "green":
                try:
                    bx, by, _ = get_robot_pos(base_env, rescue_channel_robot)
                    bpx, bpy = cfg.RESCUE_CHANNEL_BUTTON_POS[0], cfg.RESCUE_CHANNEL_BUTTON_POS[1]
                    d_plane = math.hypot(bx - bpx, by - bpy)
                except Exception:
                    d_plane = 999.0
                _near_button = d_plane <= _rc_arm_plane_dist_m
                _nav_done = nav.waiting_for_task.get(rescue_channel_robot, False) and not nav.navigator.is_active(rescue_channel_robot)
                if _near_button and _nav_done:
                    if d_plane <= _rc_final_approach_trigger_dist:
                        ur5.scout_1_pick.start_pick(stage=None, target_pos=cfg.RESCUE_CHANNEL_BUTTON_POS, button_press_mode=True)
                        if _press_button_exec_sim_s is None and _fire_start_ts is not None:
                            _press_button_exec_sim_s = float(_sim_time_since_fire_s)
                        nav.hold_position.add(rescue_channel_robot)
                        _rc_press_started = True
                        _rc_final_approach = False
                    elif not _rc_final_approach:
                        _rc_final_approach = True
                        _rc_final_approach_start = time.time()
                        nav.hold_position.add(rescue_channel_robot)
                    else:
                        if time.time() - _rc_final_approach_start > _rc_final_approach_timeout_s:
                            ur5.scout_1_pick.start_pick(stage=None, target_pos=cfg.RESCUE_CHANNEL_BUTTON_POS, button_press_mode=True)
                            if _press_button_exec_sim_s is None and _fire_start_ts is not None:
                                _press_button_exec_sim_s = float(_sim_time_since_fire_s)
                            _rc_press_started = True
                            _rc_final_approach = False

        if _rc_final_approach and rescue_channel_robot:
            try:
                from .sim_helpers import get_yaw_from_quat, normalize_angle
                _fa_bx, _fa_by, _ = get_robot_pos(base_env, rescue_channel_robot)
                _fa_bpx, _fa_bpy = cfg.RESCUE_CHANNEL_BUTTON_POS[0], cfg.RESCUE_CHANNEL_BUTTON_POS[1]
                _fa_target_yaw = math.atan2(_fa_bpy - _fa_by, _fa_bpx - _fa_bx)
                _, _fa_quat = get_robot_pose_tensors(base_env, rescue_channel_robot)
                _fa_curr_yaw = get_yaw_from_quat(_fa_quat)
                _fa_yaw_err = normalize_angle(_fa_target_yaw - _fa_curr_yaw)
                _fa_wz = max(-0.8, min(0.8, 2.0 * _fa_yaw_err))
                _fa_cmd = nav.robot_commands[rescue_channel_robot]
                _fa_cmd[:] = 0
                _fa_cmd[:, 0] = _rc_final_approach_speed
                _fa_cmd[:, 2] = _fa_wz
                actions[rescue_channel_robot] = _fa_cmd
            except Exception:
                pass

        if _rc_press_started and rescue_channel_robot in nav.hold_position:
            if not ur5.scout_1_pick.active:
                nav.hold_position.discard(rescue_channel_robot)
                _rc_press_started = False
                _rc_channel_arm_done = True
                light.trigger_rescue_channel()
                _dsrv.push_chat("emos", "EMOS 系统", f"✅ {rescue_channel_robot} 已敲击救援通道按钮。")
                if emos_mgr._hazard_pos and (abs(emos_mgr._hazard_pos[0]) + abs(emos_mgr._hazard_pos[1])) > 0.05:
                    _hx_rc = float(emos_mgr._hazard_pos[0])
                    _hy_rc = float(emos_mgr._hazard_pos[1])
                    _retreat_x, _retreat_y = 8.0, 1.0
                    _rc_rally_x, _rc_rally_y = _hx_rc + 2.0, _hy_rc - 1.0
                    _retreat_th = math.atan2(_rc_rally_y - _retreat_y, _rc_rally_x - _retreat_x)
                    _rc_retreat_task = RobotTask(
                        task_id=f"emos_green_retreat_{rescue_channel_robot}", task_type="emos",
                        subtask_name="按钮后退→转向", subtask_colour="grey",
                        target_xy=(_retreat_x, _retreat_y), waypoints=[(_retreat_x, _retreat_y, _retreat_th)], priority=0,
                    )
                    _rc_rally_task = RobotTask(
                        task_id=f"emos_green_done_rally_{rescue_channel_robot}", task_type="emos",
                        subtask_name="绿色完成→火源集结", subtask_colour="grey",
                        target_xy=(_rc_rally_x, _rc_rally_y), waypoints=[], priority=0,
                    )
                    nav.interrupt_patrol_with_task(rescue_channel_robot, _rc_retreat_task, priority=False)
                    tq_rc = nav.task_queues.get(rescue_channel_robot)
                    if tq_rc:
                        tq_rc.push_back(_rc_rally_task)

        _skip_c2_key5_for_green = False
        if rescue_channel_robot == "scout_1" and not _rc_channel_arm_done:
            _tq_c2 = nav.task_queues.get("scout_1")
            _cur_c2 = _tq_c2.peek() if _tq_c2 and not _tq_c2.is_empty() else None
            if _cur_c2 is not None and getattr(_cur_c2, "subtask_colour", "") == "green":
                _skip_c2_key5_for_green = True
        if not _rc_press_started and not _skip_c2_key5_for_green:
            ur5.update_scout1_arm_sequence(light)
        if "scout1_key5_arrived" in nav.events:
            ur5.start_scout1_key5_arm()
        try:
            ur5.update_obstacle_rescue_reach(stage)
        except Exception:
            pass
        try:
            ur5.update_auto_pick(stage)
        except Exception:
            pass
        try:
            ur5.update_extinguisher_grab(stage)
        except Exception:
            pass

        _robot_standby_this_frame: Dict[str, bool] = {}
        if _fire_start_ts is not None:
            for rn in emos_agents:
                if rn in _robot_end_ts:
                    continue
                _robot_standby_this_frame[rn] = _is_waiting_idle(rn) and (not _is_robot_arm_busy(rn))

        if actions:
            env.step(actions)
        else:
            env.step({})

        if getattr(ur5, "_ext_grabbed", False):
            try:
                ur5.update_extinguisher_grab(stage)
            except Exception:
                pass

        if _fire_start_ts is not None:
            _now_real_ts = time.time()
            _frame_real_dt = 0.0 if _prev_loop_real_ts is None else max(0.0, _now_real_ts - _prev_loop_real_ts)
            _prev_loop_real_ts = _now_real_ts
            _sim_time_since_fire_s = max(0.0, _now_real_ts - _fire_start_ts)
            for _rn, _stby in _robot_standby_this_frame.items():
                if _rn in _robot_end_ts:
                    continue
                if _stby:
                    _robot_standby_time_s[_rn] = float(_robot_standby_time_s.get(_rn, 0.0) + _frame_real_dt)
                else:
                    _robot_moving_time_s[_rn] = float(_robot_moving_time_s.get(_rn, 0.0) + _frame_real_dt)

        if dashboard and _stream is not None:
            _stream.broadcast_if_ready()

        fps_counter += 1
        now_t = time.time()
        if _prev_frame_real_ts is not None:
            _frame_dt = now_t - _prev_frame_real_ts
            if 0.0 < _frame_dt < _RUNTIME_STALL_THRESHOLD_S:
                _active_runtime_s += _frame_dt
        _prev_frame_real_ts = now_t
        _refresh_robot_goal_state(now_t)
        if now_t - fps_last_t >= 1.0:
            cur_fps = fps_counter / (now_t - fps_last_t)
            fps_counter = 0
            fps_last_t = now_t
            _dsrv.update_sim(
                step=step_counter,
                fps=round(cur_fps, 1),
                runtime_s=_sim_time_since_fire_s,
                running=True,
                virtual_time_s=float(_sim_time_since_fire_s),
                mode="robots",
                result="running",
                fail_reason="",
                manual_task_status=_task_status_payload(),
                active_hazard_id=int(_record_hazard_id or 0),
            )

        if step_counter % cfg.DATA_SERVER_UPDATE_INTERVAL == 0:
            try:
                for rn, rd in nav.get_robot_data_for_server().items():
                    _dsrv.update_robot(name=rn, pos_x=rd["pos"][0], pos_y=rd["pos"][1], pos_z=rd["pos"][2],
                                       waypoints=rd["waypoints"], current_idx=rd["current_idx"],
                                       rrt_active=rd["rrt_active"], status=rd["status"],
                                       nav_reason=rd.get("nav_reason", ""))
                    tq = nav.task_queues.get(rn)
                    if tq is not None:
                        tasks_list = [{"task_id": t.task_id, "task_type": t.task_type,
                                       "subtask_name": t.subtask_name, "subtask_colour": t.subtask_colour,
                                       "target_xy": list(t.target_xy)} for t in tq.all_tasks()]
                        _dsrv.update_robot_tasks(name=rn, task_queue=tasks_list, current_task_index=0,
                                                 is_patrolling=nav.is_patrolling.get(rn, False))
            except Exception:
                pass

        if step_counter % cfg.STUCK_CHECK_INTERVAL == 0:
            nav.check_stuck()

        # ── 纯机器人实验终止条件（与实验组统一） ──
        if _fire_start_ts is not None and not _should_stop_sim:
            _ext_done = bool(extinguisher_robot and _extinguisher_task1_completed)
            _fire_xy = (
                (float(_fire_pos[0]), float(_fire_pos[1]))
                if _fire_pos is not None
                else None
            )
            _rescue_state = obstacle_mgr.state if obstacle_mgr is not None else None
            _emergency_required = bool(
                _obstacle_spawn_sim_s is not None
                or getattr(_rescue_state, "obstacle_spawned", False)
            )
            _emergency_complete = bool(getattr(_rescue_state, "obstacle_removed", False))
            _ok = unified_mission_success(
                base_env=base_env,
                emos_agents=emos_agents,
                fire_xy=_fire_xy,
                extinguisher_robot=extinguisher_robot,
                extinguisher_task1_complete=_ext_done,
                rescue_channel_complete=bool(_rc_channel_arm_done),
                emergency_task_required=_emergency_required,
                emergency_task_complete=_emergency_complete,
            )
            if _ok:
                _sim_result = "success"
                _sim_fail_reason = None
                _clear_active_fire_hazards()
                _should_stop_sim = True
                _dsrv.push_chat(
                    "emos",
                    "EMOS 统计",
                    "✅ 灭火器已拿取、救援通道已敲击、必要突发任务已完成，且满足 3 台/灭火器车在火源 3m 内，任务成功。",
                )
            elif _sim_time_since_fire_s >= sim_timeout_s:
                _sim_result = "fail"
                _sim_fail_reason = f"任务时长超过{sim_timeout_s:.0f}s仍未达成目标"
                _should_stop_sim = True
                _dsrv.push_chat("emos", "EMOS 统计", f"❌ 任务超时：{sim_timeout_s:.0f}s内未达成目标。")

        if _should_stop_sim:
            break

        if do_real_time:
            sleep_time = step_dt - (time.perf_counter() - loop_t0)
            if sleep_time > 0:
                time.sleep(sleep_time)

    if _sim_result == "running":
        _sim_result = "fail"
        if _sim_fail_reason is None:
            _sim_fail_reason = "任务提前结束（未命中成功条件）"

    _end_wall = time.time()
    try:
        from ..dashboard import server as _dsp
        _wt = float(_end_wall - _fire_start_ts) if _fire_start_ts is not None else 0.0
        _hz = f"火源地{_record_hazard_id}" if _record_hazard_id else ""
        _oc = "success" if _sim_result == "success" else "fail"
        _dsp.set_trial_end_popup(_oc, float(_sim_time_since_fire_s), _wt, _hz)
    except Exception as _tp_e:
        print(f"[Robot Baseline] trial 弹窗状态更新失败: {_tp_e}")
    try:
        _dsrv.update_sim(
            step=step_counter,
            fps=0.0,
            runtime_s=_sim_time_since_fire_s,
            running=False,
            virtual_time_s=float(_sim_time_since_fire_s),
            mode="robots",
            result=_sim_result,
            fail_reason=(_sim_fail_reason or ""),
            manual_task_status=_task_status_payload(),
            active_hazard_id=int(_record_hazard_id or 0),
        )
    except Exception:
        pass

    return step_counter
