"""Execution adapter for the existing Pepper composite transport controller.

The adapter advances an already-created EAI environment.  It never starts a
simulator and its feedback deliberately contains no physical-success field.
"""

from __future__ import annotations

import importlib
import json
from collections.abc import Callable, Mapping
from typing import Any

from algorithm.skills.contracts import SkillPlan, SkillRequest

from .provider import (
    CARRY_MODE,
    COMMAND_ROUTE,
    COMMAND_TYPE,
    CONTROLLER_CFG,
    PROVIDER_ID,
    PROVIDER_VERSION,
    SKILL_ID,
    is_pepper_robot_id,
    validate_pepper_transport_request,
)


class PepperTransportExecutionError(RuntimeError):
    """Raised when a plan cannot be safely routed to the Pepper controller."""


def _callback_name(value: object) -> str | None:
    return getattr(value, "__name__", None) if callable(value) else None


def pepper_controller_state(env: Any, robot_id: str) -> Mapping[str, Any]:
    """Return the loaded controller mapping after strict identity checks."""

    configs = getattr(env, "_controller_configs", None)
    loaded = getattr(env, "_controllers_dict", None)
    if not isinstance(configs, Mapping) or robot_id not in configs:
        raise PepperTransportExecutionError(
            f"{robot_id!r} has no configured EAI controller"
        )
    if not isinstance(loaded, Mapping) or robot_id not in loaded:
        raise PepperTransportExecutionError(
            f"{robot_id!r} has no loaded EAI controller state"
        )
    cfg = configs[robot_id]
    state = loaded[robot_id]
    if type(cfg).__name__ != "PepperBoxTransportControllerCfg":
        raise PepperTransportExecutionError(
            f"{robot_id!r} is not routed to {CONTROLLER_CFG}"
        )
    if str(getattr(cfg, "robot_type", "")).casefold() != "pepper":
        raise PepperTransportExecutionError("controller robot_type is not Pepper")
    if _callback_name(getattr(cfg, "compute_action_from_command_func", None)) != (
        "compute_pepper_box_transport_action"
    ):
        raise PepperTransportExecutionError("Pepper transport compute hook is not configured")
    if _callback_name(getattr(cfg, "apply_action_func", None)) != (
        "apply_pepper_box_transport_action"
    ):
        raise PepperTransportExecutionError("Pepper transport apply hook is not configured")
    if not isinstance(state, Mapping):
        raise PepperTransportExecutionError("loaded Pepper controller state must be a mapping")
    if state.get("robot_type") != "Pepper":
        raise PepperTransportExecutionError("loaded controller state is not Pepper")
    if state.get("locomotion_controller") != "PEPPER_HOLONOMIC_CFG":
        raise PepperTransportExecutionError("loaded Pepper locomotion controller is unexpected")
    if state.get("runtime") is None or state.get("transport") is None:
        raise PepperTransportExecutionError("loaded Pepper transport runtime is incomplete")
    return state


def _validated_payload(plan: SkillPlan) -> dict[str, Any]:
    if plan.status != "planned":
        raise PepperTransportExecutionError("Pepper transport plan must have status 'planned'")
    if plan.skill_id != SKILL_ID or not is_pepper_robot_id(plan.robot_id):
        raise PepperTransportExecutionError("plan is not a Pepper transport plan")
    if len(plan.commands) != 1 or plan.commands[0].command_type != COMMAND_TYPE:
        raise PepperTransportExecutionError(
            f"Pepper transport plan must contain one {COMMAND_TYPE!r} command"
        )
    metadata = dict(plan.metadata)
    expected_metadata = {
        "command_route": COMMAND_ROUTE,
        "controller_cfg": CONTROLLER_CFG,
        "provider_id": PROVIDER_ID,
        "provider_version": PROVIDER_VERSION,
        "physical_outcome": "external_evaluator_required",
        "schema_version": "simulator.skill.v1",
    }
    for name, expected in expected_metadata.items():
        if metadata.get(name) != expected:
            raise PepperTransportExecutionError(
                f"Pepper transport plan has invalid {name} metadata"
            )
    payload = dict(plan.commands[0].payload)
    request = SkillRequest(
        request_id=plan.request_id,
        skill_id=plan.skill_id,
        robot_id=plan.robot_id,
        parameters=payload,
    )
    try:
        validate_pepper_transport_request(request)
    except ValueError as exc:
        raise PepperTransportExecutionError(str(exc)) from exc
    if payload["carry_mode"] != CARRY_MODE:
        raise PepperTransportExecutionError("unsupported Pepper carry mode")
    return payload


def _default_neutral_action(env: Any, robot_id: str) -> Any:
    factory = getattr(env, "make_neutral_action", None)
    if callable(factory):
        return factory(robot_id)
    try:
        torch = importlib.import_module("torch")
        return torch.zeros(
            (int(getattr(env, "num_envs", 1)), 3),
            device=getattr(env, "device", "cpu"),
            dtype=torch.float32,
        )
    except Exception as exc:  # pragma: no cover - exercised only without Torch
        raise PepperTransportExecutionError(
            "a neutral_action_factory is required when Torch is unavailable"
        ) from exc


class PepperTransportExecutorAdapter:
    """Advance the configured Pepper controller with neutral external commands."""

    def execute(
        self,
        plan: SkillPlan,
        env: Any,
        *,
        advance_hook: Callable[[Mapping[str, Any]], Any] | None = None,
        neutral_action_factory: Callable[[Any, str], Any] | None = None,
    ) -> dict[str, Any]:
        payload = _validated_payload(plan)
        controller = pepper_controller_state(env, plan.robot_id)
        advance = advance_hook if advance_hook is not None else getattr(env, "step", None)
        if not callable(advance):
            raise PepperTransportExecutionError("environment has no callable advance hook")
        make_neutral = neutral_action_factory or _default_neutral_action
        before_compute = int(controller.get("locomotion_compute_calls", 0))
        before_apply = int(controller.get("locomotion_apply_calls", 0))
        ticks = int(payload["horizon_ticks"])
        for _ in range(ticks):
            neutral = make_neutral(env, plan.robot_id)
            advance({plan.robot_id: neutral})
        after_compute = int(controller.get("locomotion_compute_calls", 0))
        after_apply = int(controller.get("locomotion_apply_calls", 0))
        if after_compute - before_compute < ticks or after_apply - before_apply < ticks:
            raise PepperTransportExecutionError(
                "advance hook did not apply the configured Pepper transport controller"
            )
        state = getattr(env, "box_transport_state", None)
        feedback_state = None
        if isinstance(state, Mapping):
            try:
                feedback_state = json.loads(
                    json.dumps(dict(state), allow_nan=False, sort_keys=True)
                )
            except (TypeError, ValueError):
                feedback_state = None
        return {
            "command_applied": True,
            "feedback": {
                "command_route": COMMAND_ROUTE,
                "controller_cfg": CONTROLLER_CFG,
                "locomotion_apply_calls": after_apply,
                "locomotion_compute_calls": after_compute,
                "transport_state": feedback_state,
                "ticks_advanced": ticks,
            },
            "request_id": plan.request_id,
            "robot_id": plan.robot_id,
            "skill_id": plan.skill_id,
            "status": "command_applied",
        }


__all__ = [
    "PepperTransportExecutionError",
    "PepperTransportExecutorAdapter",
    "pepper_controller_state",
]
