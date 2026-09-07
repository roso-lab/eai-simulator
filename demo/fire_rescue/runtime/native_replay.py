"""Native replay lifecycle and deterministic simulation-time task clocks."""

from __future__ import annotations

import importlib
import time as wall_time
from typing import Any

from tools.physics_replay.components import CallbackComponent
from tools.physics_replay.live import CheckpointBusy, LiveCheckpointBackend, LiveReplayController
from tools.physics_replay.runtime_state import ActuatorStateComponent, EnvironmentBuffersComponent, ObjectStateComponent


class SimulationClock:
    name = "fire_rescue.clock"
    version = 1
    priority = 21

    def __init__(self, step_dt: float):
        self.step_dt = float(step_dt)
        self.step_index = 0
        self.epoch = wall_time.time()

    def time(self) -> float:
        return self.epoch + self.step_index * self.step_dt

    def __getattr__(self, name: str) -> Any:
        return getattr(wall_time, name)

    def capture_state(self) -> dict[str, Any]:
        return {"step_index": self.step_index, "epoch": self.epoch}

    def validate_state(self, state: Any) -> None:
        if int(state["step_index"]) < 0:
            raise ValueError("invalid replay clock step")

    def restore_state(self, state: Any) -> None:
        self.step_index = int(state["step_index"])
        self.epoch = float(state["epoch"])


class NativeReplayScope:
    """Keep GUI callbacks and temporary module clocks owned by one trial."""

    def __init__(self, step_dt: float):
        self.clock = SimulationClock(step_dt)
        self.window = None
        self._original_clocks = []

    def __enter__(self) -> "NativeReplayScope":
        for name in ("experiment_loop", "navigation", "obstacle_rescue", "ur5", "sim_helpers"):
            module = importlib.import_module(f"demo.fire_rescue.runtime.{name}")
            if hasattr(module, "time"):
                self._original_clocks.append((module, module.time))
                module.time = self.clock
        engine = importlib.import_module("algorithm.emos.engine")
        self._original_clocks.append((engine, engine.time))
        engine.time = self.clock
        return self

    def __exit__(self, *args: Any) -> None:
        try:
            if self.window is not None:
                self.window.close()
        finally:
            for module, original in self._original_clocks:
                module.time = original


def build_native_replay(
    session: Any, output_dir: Any, *, scope: NativeReplayScope, nav: Any,
    obstacle: Any, ur5: Any, emos: Any, light: Any, runtime_component: Any,
    tolerance: float = 1e-5, create_window: bool = True,
) -> LiveReplayController:
    from .replay import NavigationReplayComponent, validate_fire_rescue_checkpoint
    from .navigation import RobotTask
    from .settings import OBSTACLE_PRIM_PATH
    from .ur5 import UR5AutoPickController
    from tools.physics_replay.stage_state import SleepStateComponent, StageStateComponent

    def workers_idle() -> None:
        for label, owner in (("navigation", nav), ("EMOS", emos)):
            thread = getattr(owner, "_plan_thread", None) if owner is nav else getattr(owner, "_thread", None)
            if thread is not None and thread.is_alive():
                raise CheckpointBusy(f"{label} worker is still running")

    def no_workers() -> None:
        workers_idle()
        if getattr(nav, "_pending_plan", None) is not None:
            raise CheckpointBusy("navigation plan must be consumed before checkpointing")
        for owner, field in ((nav, "_plan_result_queue"), (emos, "_result_queue"), (obstacle, "_approval_queue")):
            pending = getattr(owner, field, None)
            if pending is not None and not pending.empty():
                raise CheckpointBusy(f"pending {field} must be consumed first")

    def discard_future_results(state: Any) -> None:
        from queue import Empty

        for owner, field in ((nav, "_plan_result_queue"), (emos, "_result_queue"), (obstacle, "_approval_queue")):
            pending = getattr(owner, field, None)
            if pending is not None:
                while True:
                    try:
                        pending.get_nowait()
                    except Empty:
                        break
        nav._plan_thread = None
        emos._thread = None
        emos._active_rescue_manager = None

    def safe_checkpoint() -> None:
        no_workers()
        try:
            validate_fire_rescue_checkpoint(nav, obstacle, ur5)
        except ValueError as exc:
            raise CheckpointBusy(str(exc)) from exc

    runtime_paths = (
        OBSTACLE_PRIM_PATH, "/World/ObstacleProxy", "/World/SM_FireExtinguisherProxy",
        "/World/envs/env_0/extinguisher_grab_joint",
        *(f"/World/Factory_Hazard_{index}" for index in range(1, 6)),
    )
    stage_component = StageStateComponent(session, runtime_paths=runtime_paths, tolerance=tolerance)
    components = [
        stage_component,
        SleepStateComponent(stage_component),
        scope.clock,
        CallbackComponent("fire_rescue.workers", 1, lambda: {}, discard_future_results, priority=22),
        EnvironmentBuffersComponent(session.base_env),
        runtime_component,
        NavigationReplayComponent(nav, task_factory=RobotTask),
        ObjectStateComponent("fire_rescue.navigation_full", nav, exclude={
            "base_env", "navigator", "_nav_session", "task_queues", "_plan_thread", "_plan_result_queue",
        }, priority=41),
        ObjectStateComponent("fire_rescue.navigator", nav.navigator, exclude={"planner", "_planner"}, priority=42),
        ObjectStateComponent("fire_rescue.obstacle_full", obstacle, exclude={
            "base_env", "nav", "navigator", "push_chat", "update_rescue_state", "stage", "sim_app", "ur5",
            "_rescue_llm_kwargs_getter", "_approval_queue",
        }, priority=50),
        ObjectStateComponent("fire_rescue.ur5_full", ur5, exclude={
            "_env", "_ext_drop_callback", "_interrupt_rescue_callback",
        }, priority=60, value_types=(UR5AutoPickController,)),
        ObjectStateComponent("fire_rescue.emos", emos, exclude={
            "_env", "_get_pos", "_push_chat", "_scenario", "_get_group_discussion_llm_kwargs", "_rescue_constraint_fn",
            "_active_rescue_manager", "_thread", "_lock", "_result_queue", "_log_dir", "_disc_log_dir",
        }, priority=65),
        ObjectStateComponent("fire_rescue.light", light, exclude={"_color_attr"}, priority=66),
        ActuatorStateComponent(session.base_env),
    ]
    if nav._nav_session is not None:
        components.extend((
            ObjectStateComponent("fire_rescue.planner_tracking", nav._nav_session, exclude={
                "planner", "navigator", "cfg", "reservation", "_st_astar",
            }, priority=43),
            ObjectStateComponent("fire_rescue.reservations", nav._nav_session.reservation, priority=44),
        ))
    backend = LiveCheckpointBackend(
        session, output_dir, components=components, tolerance=tolerance,
        safety_check=safe_checkpoint, restore_check=workers_idle,
    )
    controller = LiveReplayController(backend)
    if create_window:
        from tools.physics_replay.native_ui import NativeReplayWindow

        scope.window = NativeReplayWindow(controller)
    return controller
