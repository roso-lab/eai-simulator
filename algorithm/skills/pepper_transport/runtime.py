"""Cooperative handle for the existing Pepper transport controller.

This module neither builds a scene nor steps it. Targets are validated against
the caller's configured controller before admission; arbitrary targets are not
silently substituted. Cancellation finishes an in-progress safe placement,
then reports failure, rather than dropping an attached box.
"""

from __future__ import annotations

import math
from typing import Any, Callable

from algorithm.common_coordination.contract import FailureCode
from algorithm.skills.contracts import SkillRequest
from task.contracts import DependencyOutcome
from task.executors.skills import ExecutionReceipt
from .executor import _default_neutral_action, _validated_payload, pepper_controller_state
from .provider import validate_pepper_transport_request
from .evaluator import _single_vector


def base_env(scene: Any) -> Any:
    return getattr(scene, "base_env", scene)


def validate_transport_binding(*, scene: Any, request: Any) -> None:
    validate_pepper_transport_request(request)
    env = base_env(scene)
    controller = pepper_controller_state(env, request.robot_id)
    grasp = controller["runtime"].state.config
    transport = controller["transport"].config
    expected = {"object_ref": grasp.cube_prim_name,
                "source_ref": transport.source_table_name,
                "destination_ref": transport.destination_table_name}
    if any(request.parameters[key] != value for key, value in expected.items()):
        raise ValueError("request targets do not match the live Pepper controller binding")
    if expected["object_ref"] not in env.scene.rigid_objects:
        raise ValueError("requested box is absent from the live scene")
    box = env.scene.rigid_objects[expected['object_ref']]
    position = _single_vector(box.data.root_pos_w, 3)
    if position is None or math.dist(position, grasp.box_position) > .03:
        raise ValueError('live box is outside the configured pickup position')
    if tuple(box.cfg.spawn.size) != tuple(grasp.box_size):
        raise ValueError('live box size differs from the configured grasp geometry')
    for name in (expected["source_ref"], expected["destination_ref"]):
        if getattr(env.cfg.scene, name, None) is None or name not in env.scene.extras:
            raise ValueError(f"requested support is absent: {name}")
        support_cfg = getattr(env.cfg.scene, name)
        position, rotation = env.scene.extras[name].get_world_poses()
        position, rotation = _single_vector(position, 3), _single_vector(rotation, 4)
        target = transport.source_table_position if name == expected['source_ref'] else transport.destination_table_position
        if position is None or math.dist(position,target) > .01 or rotation is None or abs(rotation[0]) < .9999:
            raise ValueError(f'live support pose differs from configured route: {name}')
        if tuple(support_cfg.spawn.size) != tuple(transport.table_size):
            raise ValueError(f'live support dimensions differ from configured route: {name}')
    if not controller.get("auto_start", False):
        raise ValueError("cooperative Pepper controller requires its existing auto_start state machine")
    if "execution_enabled" not in controller:
        raise ValueError("controller lacks task-owned execution gating")


def measured_base_stopped(env: Any, robot_id: str) -> bool:
    robot = env.scene.articulations[robot_id]
    ids, _ = robot.find_joints(["XDisp", "YDisp", "ZRot"], preserve_order=True)
    if len(ids) != 3:
        return False
    values = robot.data.joint_vel[0, ids]
    if hasattr(values, "detach"):
        values = values.detach().cpu().tolist()
    values = [float(v) for v in values]
    return all(math.isfinite(v) for v in values) and math.hypot(*values[:2]) < 0.05 and abs(values[2]) < 0.1


def measured_transport_stopped(env: Any, robot_id: str) -> bool:
    if not measured_base_stopped(env, robot_id):
        return False
    robot = env.scene.articulations[robot_id]
    runtime = pepper_controller_state(env, robot_id)["runtime"]
    for side in ("left", "right"):
        ids, _ = runtime._resolve(robot, side)
        if not ids:
            return False
        values = robot.data.joint_vel[0, ids].detach().cpu().tolist()
        if not all(math.isfinite(float(v)) and abs(float(v)) < 0.1 for v in values):
            return False
    box = env.scene.rigid_objects[runtime.state.config.cube_prim_name]
    velocity = box.data.root_vel_w[0].detach().cpu().tolist()
    if len(velocity) != 6 or not all(math.isfinite(v) for v in velocity):
        return False
    if math.sqrt(sum(v*v for v in velocity[:3])) >= .05 or math.sqrt(sum(v*v for v in velocity[3:])) >= .1:
        return False
    return True


class PepperTransportHandle:
    """Start once; command/after_step participate in a shared owner loop."""

    def __init__(self, plan: Any, *, scene: Any,
                 neutral_action: Callable = _default_neutral_action,
                 stopped_probe: Callable = measured_transport_stopped,
                 settle_ticks: int = 10) -> None:
        if type(settle_ticks) is not int or settle_ticks < 1:
            raise ValueError("settle_ticks must be a positive integer")
        self.payload = _validated_payload(plan)
        validate_transport_binding(scene=scene, request=SkillRequest(
            plan.request_id, plan.skill_id, plan.robot_id, parameters=self.payload))
        self.plan, self.env = plan, base_env(scene)
        self.controller = pepper_controller_state(self.env, plan.robot_id)
        self.neutral_action, self.stopped_probe = neutral_action, stopped_probe
        self.settle_ticks, self.stable_ticks, self.ticks = settle_ticks, 0, 0
        self.cancel_reason: str | None = None
        self._receipt: ExecutionReceipt | None = None
        grasp = self.controller["runtime"].state
        transport = self.controller["transport"]
        if self.controller.get("execution_enabled") is not False:
            raise ValueError("Pepper must be gated idle before a task starts")
        if grasp.phase.value != "idle" or transport.phase.value != "wait_for_grasp" or grasp.attached:
            raise ValueError("Pepper is not at an idle, unattached transport start; restore/reset explicitly")
        # All validation above is pure; this is the first controlled mutation.
        self.controller["execution_enabled"] = True

    def command(self) -> Any:
        return self.neutral_action(self.env, self.plan.robot_id)

    def cancel(self, reason: str) -> None:
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("cancel reason must be nonempty")
        self.cancel_reason = reason
        # Do not detach or disable a controller while it owns a carried object.
        # The shared loop continues until ordinary placement completes safely.

    def after_step(self) -> ExecutionReceipt | None:
        if self._receipt is not None:
            return self._receipt
        self.ticks += 1
        runtime, transport = self.controller["runtime"], self.controller["transport"]
        if runtime.last_error:
            raise RuntimeError(runtime.last_error)
        if self.ticks >= self.payload["horizon_ticks"] and self.cancel_reason is None:
            self.cancel("skill horizon exceeded")
        done = (transport.phase.value == "done" and runtime.state.phase.value == "done"
                and runtime.state.attached is False)
        stopped = done and self.stopped_probe(self.env, self.plan.robot_id) is True
        self.stable_ticks = self.stable_ticks + 1 if stopped else 0
        if self.stable_ticks < self.settle_ticks:
            return None
        self.controller["execution_enabled"] = False
        self._receipt = ExecutionReceipt(
            self.plan.request_id, self.plan.skill_id, self.plan.robot_id,
            DependencyOutcome.TERMINAL_FAILURE if self.cancel_reason is not None else DependencyOutcome.COMPLETED,
            True, FailureCode.SAFE_STOP if self.cancel_reason is not None else FailureCode.NONE,
            self.cancel_reason or "controller done, released box, base and arms stopped",
        )
        return self._receipt
