"""Bind benchmark lifecycle transactions to ``MultiRobotDirectEnv``.

The adapter uses only already-loaded articulations and controllers. It does
not spawn entities or infer capabilities from asset files. Inactive reserves
remain in their authored parking poses under the environment's command gate;
joining releases that gate. Exiting an agent closes it; capability changes
update the same guard consulted by the controller hot path. Physical
attachment, sensor and payload changes require a separate scene adapter.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from .isaac_lifecycle_callbacks import (
    ArticulationBinding,
    ControllerBinding,
    IsaacLifecycleCallbacks,
)


_REQUIRED_ENV_METHODS = (
    "configure_benchmark_lifecycle",
    "set_benchmark_agent_active",
    "reconfigure_benchmark_controller",
    "read_benchmark_agent_state",
    "read_benchmark_controller_state",
)


def scenario_capability_declarations(
    scenario: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    """Return the same public capability declarations used by task packets."""
    from demo.benchmark.scene_challenge import _public_robot

    robots = scenario.get("robots")
    if not isinstance(robots, list) or not robots:
        raise ValueError("lifecycle scenario requires a nonempty robot list")
    declarations = {}
    for robot in robots:
        public = _public_robot(robot)
        declarations[public["id"]] = public["capability_declaration"]
    if len(declarations) != len(robots):
        raise ValueError("lifecycle scenario robot ids must be unique")
    return declarations


def build_env_lifecycle_callbacks(
    base_env: Any,
    scenario: Mapping[str, Any],
) -> IsaacLifecycleCallbacks:
    """Create strict lifecycle callbacks over one live EAI environment."""
    missing = [name for name in _REQUIRED_ENV_METHODS
               if not callable(getattr(base_env, name, None))]
    if missing:
        raise ValueError(
            "environment does not implement benchmark lifecycle methods: "
            + ", ".join(missing))

    declarations = scenario_capability_declarations(scenario)
    expected = set(declarations)
    lifecycle = scenario.get("runtime_lifecycle") or {}
    initial = set(lifecycle.get("initial_agent_ids") or expected)
    reserves = set(lifecycle.get("preloaded_reserve_agent_ids") or ())
    if initial & reserves:
        raise ValueError("initial and reserve lifecycle inventories must be disjoint")
    if initial | reserves != expected:
        raise ValueError(
            "initial and reserve lifecycle inventories must partition scenario robots")

    base_env.configure_benchmark_lifecycle(
        active_agent_ids=initial,
        capability_declarations=deepcopy(declarations),
        capability_versions={agent_id: 1 for agent_id in expected},
    )

    articulations = {}
    controllers = {}
    for agent_id in sorted(expected):
        entity = base_env.read_benchmark_agent_state(agent_id)
        prim_path = entity.get("prim_path") if isinstance(entity, Mapping) else None
        if not isinstance(prim_path, str) or not prim_path.startswith("/"):
            raise ValueError(f"{agent_id}: lifecycle articulation has no absolute prim path")
        controller = base_env.read_benchmark_controller_state(agent_id)
        controller_id = (
            controller.get("controller_id") if isinstance(controller, Mapping) else None)
        if not isinstance(controller_id, str) or not controller_id:
            raise ValueError(f"{agent_id}: lifecycle controller has no stable id")

        def set_active(handle: Any, active: bool, *, _agent_id: str = agent_id) -> None:
            handle.set_benchmark_agent_active(_agent_id, active)

        def read_entity(handle: Any, *, _agent_id: str = agent_id) -> Mapping[str, Any]:
            return handle.read_benchmark_agent_state(_agent_id)

        def reconfigure(
            handle: Any,
            declaration: Mapping[str, Any],
            version: int,
            *,
            _agent_id: str = agent_id,
        ) -> None:
            handle.reconfigure_benchmark_controller(
                _agent_id, deepcopy(dict(declaration)), version)

        def read_controller(
            handle: Any, *, _agent_id: str = agent_id,
        ) -> Mapping[str, Any]:
            return handle.read_benchmark_controller_state(_agent_id)

        articulations[agent_id] = ArticulationBinding(
            handle=base_env,
            prim_path=prim_path,
            set_active=set_active,
            read_state=read_entity,
        )
        controllers[agent_id] = ControllerBinding(
            handle=base_env,
            controller_id=controller_id,
            reconfigure=reconfigure,
            read_state=read_controller,
        )
    return IsaacLifecycleCallbacks(
        articulations=articulations,
        controllers=controllers,
    )


__all__ = ["build_env_lifecycle_callbacks", "scenario_capability_declarations"]
