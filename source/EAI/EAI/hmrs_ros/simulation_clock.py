# Copyright (c) 2026, EAI Simulator contributors.
# SPDX-License-Identifier: BSD-3-Clause
"""Session-owned Isaac Sim ROS 2 simulation clock graph."""

from __future__ import annotations

from typing import Any, Callable


SIMULATION_CLOCK_GRAPH_PATH = "/World/ROS2_EAI/simulation_clock_graph"
_MANAGER_ATTRIBUTE = "_simulation_clock_manager"


class _IsaacSimulationClockRuntime:
    """Own the single OmniGraph that publishes simulation time."""

    def __init__(
        self,
        *,
        controller: Any | None = None,
        stage_provider: Callable[[], Any] | None = None,
        update_callback: Callable[[], None] | None = None,
        graph_path: str = SIMULATION_CLOCK_GRAPH_PATH,
    ) -> None:
        if controller is None:
            import omni.graph.core as og

            controller = og.Controller
        self._controller = controller
        self._stage_provider = stage_provider or self._get_stage
        self._update_callback = update_callback or self._update_kit
        self._graph_path = str(graph_path)
        self._graph: Any | None = None
        self._stage: Any | None = None
        self._owned_graph_path: str | None = None

    @staticmethod
    def _get_stage() -> Any:
        import omni.usd

        return omni.usd.get_context().get_stage()

    @staticmethod
    def _update_kit() -> None:
        import omni.kit.app

        omni.kit.app.get_app().update()

    @staticmethod
    def _prim_is_valid(prim: Any) -> bool:
        return prim is not None and bool(prim.IsValid())

    @property
    def active(self) -> bool:
        return self._owned_graph_path is not None

    def create(self) -> None:
        if self.active:
            return
        stage = self._stage_provider()
        if stage is None:
            raise RuntimeError("Isaac simulation clock requires an active USD stage")
        if self._prim_is_valid(stage.GetPrimAtPath(self._graph_path)):
            raise RuntimeError(
                f"Isaac simulation clock graph already exists: {self._graph_path}"
            )

        # Record ownership before edit(): a failed edit can leave a partial graph.
        self._stage = stage
        self._owned_graph_path = self._graph_path
        keys = self._controller.Keys
        try:
            result = self._controller.edit(
                {"graph_path": self._graph_path, "evaluator_name": "execution"},
                {
                    keys.CREATE_NODES: [
                        ("on_playback_tick", "omni.graph.action.OnPlaybackTick"),
                        (
                            "read_simulation_time",
                            "isaacsim.core.nodes.IsaacReadSimulationTime",
                        ),
                        ("ros2_context", "isaacsim.ros2.bridge.ROS2Context"),
                        ("clock_qos", "isaacsim.ros2.bridge.ROS2QoSProfile"),
                        ("publish_clock", "isaacsim.ros2.bridge.ROS2PublishClock"),
                    ],
                    keys.CONNECT: [
                        (
                            "on_playback_tick.outputs:tick",
                            "publish_clock.inputs:execIn",
                        ),
                        (
                            "read_simulation_time.outputs:simulationTime",
                            "publish_clock.inputs:timeStamp",
                        ),
                        (
                            "ros2_context.outputs:context",
                            "publish_clock.inputs:context",
                        ),
                        (
                            "clock_qos.outputs:qosProfile",
                            "publish_clock.inputs:qosProfile",
                        ),
                    ],
                    keys.SET_VALUES: [
                        ("read_simulation_time.inputs:resetOnStop", False),
                        ("ros2_context.inputs:useDomainIDEnvVar", True),
                        ("ros2_context.inputs:domain_id", 0),
                        ("clock_qos.inputs:createProfile", "Custom"),
                        ("clock_qos.inputs:history", "keepLast"),
                        ("clock_qos.inputs:depth", 1),
                        ("clock_qos.inputs:reliability", "bestEffort"),
                        ("clock_qos.inputs:durability", "volatile"),
                        ("clock_qos.inputs:liveliness", "systemDefault"),
                        ("clock_qos.inputs:deadline", 0.0),
                        ("clock_qos.inputs:lifespan", 0.0),
                        ("clock_qos.inputs:leaseDuration", 0.0),
                        ("publish_clock.inputs:topicName", "/clock"),
                        ("publish_clock.inputs:nodeNamespace", ""),
                        ("publish_clock.inputs:queueSize", 1),
                    ],
                },
            )
            self._graph = result[0] if isinstance(result, tuple) else result
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        stage = self._stage
        graph_path = self._owned_graph_path
        self._graph = None
        self._stage = None
        self._owned_graph_path = None
        if stage is None or graph_path is None:
            return

        try:
            prim = stage.GetPrimAtPath(graph_path)
            if self._prim_is_valid(prim):
                stage.RemovePrim(graph_path)
        except Exception as exc:
            print(f"[SimulationClock] Warning: failed to remove clock graph: {exc}")
        try:
            self._update_callback()
        except Exception as exc:
            print(f"[SimulationClock] Warning: failed to finalize cleanup: {exc}")


class SimulationClockManager:
    """Attach one simulation clock runtime to an EAI environment."""

    def __init__(self, env: Any, *, runtime: Any | None = None) -> None:
        self._env: Any | None = env
        self._runtime: Any | None = runtime or _IsaacSimulationClockRuntime()
        self._runtime.create()

    @property
    def active(self) -> bool:
        return self._runtime is not None

    def close(self) -> None:
        runtime = self._runtime
        if runtime is None:
            return
        env = self._env
        self._runtime = None
        self._env = None
        if env is not None and getattr(env, _MANAGER_ATTRIBUTE, None) is self:
            delattr(env, _MANAGER_ATTRIBUTE)
        runtime.close()


def attach_simulation_clock_manager(env: Any, manager: SimulationClockManager) -> None:
    existing = get_simulation_clock_manager(env)
    if existing is not None and existing is not manager and existing.active:
        raise RuntimeError("An active simulation clock manager is already attached")
    setattr(env, _MANAGER_ATTRIBUTE, manager)


def get_simulation_clock_manager(env: Any) -> SimulationClockManager | None:
    manager = getattr(env, _MANAGER_ATTRIBUTE, None)
    return manager if isinstance(manager, SimulationClockManager) else None


def ensure_simulation_clock_manager(
    env: Any,
    *,
    runtime: Any | None = None,
) -> SimulationClockManager:
    manager = get_simulation_clock_manager(env)
    if manager is not None and manager.active:
        return manager
    manager = SimulationClockManager(env, runtime=runtime)
    attach_simulation_clock_manager(env, manager)
    return manager


__all__ = [
    "SIMULATION_CLOCK_GRAPH_PATH",
    "SimulationClockManager",
    "attach_simulation_clock_manager",
    "ensure_simulation_clock_manager",
    "get_simulation_clock_manager",
]
