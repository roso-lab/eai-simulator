# Copyright (c) 2022-2026, The Isaac Lab Project Developers.
# SPDX-License-Identifier: BSD-3-Clause

"""Generic arm / kinematics pipeline hooks for DirectMARLEnv `post_apply_action`.

This module stays **robot-agnostic**: no UR5/M20/Carter joint names or factory agent ids.

Naming note (review): "ik_controller" denotes the **pipeline extension point** for applying
arm joint targets after per-robot controllers run. Concrete tasks may use file-based joint
commands, analytic IK, or numerical IK in subclasses under `EAI_assets.controller.*`.

See also: `EAI_assets.controller.traditional.ur5_ik` for the factory UR5 implementation.
"""

from __future__ import annotations

import math
import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, List, Optional, Sequence, Tuple


def eai_simulator_repo_root() -> Path:
    """`eai-simulator` repo root when this file lives under `source/EAI/EAI/controllers/`."""
    return Path(__file__).resolve().parents[4]


def ensure_dir(path: Path | str) -> None:
    os.makedirs(str(path), exist_ok=True)


def read_first_line_floats(path: str, count: int) -> Optional[List[float]]:
    """Read first non-empty line from *path* and parse *count* floats (comma or space separated)."""
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            line = f.read().strip()
        if not line or line.startswith("#"):
            return None
        parts = [x.strip() for x in line.replace(",", " ").split()]
        vals = [float(x) for x in parts[:count]]
        if len(vals) < count:
            return None
        return vals
    except (OSError, ValueError):
        return None


def read_control_target_token(
    path: str,
    *,
    valid_tokens: frozenset[str],
    default: str,
) -> str:
    """Read a single token from *path*; return *default* if missing or invalid."""
    if not os.path.isfile(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            t = f.read().strip().lower()
        if t in valid_tokens:
            return t
    except OSError:
        pass
    return default


def read_pick_xyz_after_seq(
    trigger_path: str,
    target_path: str,
    last_seq: int,
) -> Tuple[int, Optional[Tuple[float, float, float]]]:
    """If trigger file seq increased, read XYZ from *target_path*."""
    if not (os.path.isfile(trigger_path) and os.path.isfile(target_path)):
        return last_seq, None
    try:
        with open(trigger_path, "r", encoding="utf-8") as f:
            seq = int(f.read().strip() or "0")
    except (OSError, ValueError):
        return last_seq, None
    if seq <= last_seq:
        return last_seq, None
    try:
        with open(target_path, "r", encoding="utf-8") as f:
            line = f.read().strip()
        if not line:
            return last_seq, None
        parts = [float(x) for x in line.replace(",", " ").split()[:3]]
        if len(parts) != 3:
            return last_seq, None
        return seq, (parts[0], parts[1], parts[2])
    except (OSError, ValueError):
        return last_seq, None


class ArmPostApplyPipeline(ABC):
    """Abstract hook invoked at the end of `MultiRobotDirectEnv._apply_action`."""

    @abstractmethod
    def run_post_apply(self, env: Any) -> None:
        """Task-specific arm / kinematics follow-up (file bridge, IK, grasp, etc.)."""


def noop_post_apply(_env: Any) -> None:
    """Placeholder `post_apply_action` that does nothing."""


def resolve_end_effector_body_id(arm: Any, body_names: Sequence[str]) -> int | None:
    """Return the first matching end-effector body index.

    Isaac assets use slightly different names for the tool body (for example
    ``wrist_3_link`` and ``link06``).  Keeping the alias lookup here lets the
    idealized manipulation runtime use the same model-spec vocabulary as the
    existing ROS manipulator controllers without importing a robot-specific
    implementation.
    """

    for body_name in body_names:
        try:
            body_ids, _ = arm.find_bodies([str(body_name)], preserve_order=True)
        except (AttributeError, TypeError, ValueError):
            return None
        try:
            if len(body_ids):
                return int(body_ids[0])
        except (TypeError, IndexError, ValueError):
            continue
    return None


def resolve_jacobian_row(arm: Any, ee_body_id: int, body_names: Sequence[str], *, row_count: int | None = None) -> int | None:
    """Return the Jacobian row that PhysX assigns to the end-effector body.

    ``link_paths`` includes the root link. Fixed-base Jacobians omit its six
    rows; floating-base Jacobians include them. Resolve the live link first,
    then account for that omission rather than treating link indices as rows.
    """

    view = getattr(arm, "root_physx_view", None)
    link_paths = getattr(view, "link_paths", None)
    if not link_paths:
        return None
    first_instance = link_paths[0] if len(link_paths) else ()
    if isinstance(first_instance, (str, bytes)):
        first_instance = link_paths
    names = [str(path).rsplit("/", 1)[-1] for path in first_instance]
    offset = 1 if bool(getattr(arm, "is_fixed_base", False)) else 0
    if row_count is not None:
        offset = len(names) - row_count
        if offset not in (0, 1):
            return None
    for body_name in body_names:
        if body_name in names:
            row = names.index(body_name) - offset
            return row if row >= 0 else None
    return None


def _physics_step_dt(env: Any) -> float | None:
    """Return the simulator physics step without assuming a concrete env class."""

    sim = getattr(env, "sim", None)
    candidates = (
        getattr(getattr(sim, "cfg", None), "dt", None),
        getattr(getattr(getattr(env, "cfg", None), "sim", None), "dt", None),
    )
    getter = getattr(sim, "get_physics_dt", None)
    if callable(getter):
        try:
            candidates = (getter(), *candidates)
        except (RuntimeError, TypeError, ValueError):
            pass
    for value in candidates:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            continue
        if 0.0 < parsed < float("inf"):
            return parsed
    return None


def _slew_limit_joint_position_target(
    env: Any,
    arm: Any,
    controller: Any,
    joint_ids: Sequence[int],
    joint_pos: Any,
    desired: Any,
    limits: Any,
    max_delta_rad: float,
    max_acceleration_rad_s2: float | None = None,
    target_state_key: str | None = None,
    limit_tracking_error: bool = True,
) -> Any:
    """Keep successive IK position targets continuous across command changes.

    Differential IK bounds its solution relative to the measured joint pose.
    That alone still permits a nearly ``2 * max_delta_rad`` target jump when a
    Cartesian waypoint reverses the requested joint direction.  Position-drive
    articulations interpret that jump as an impulse-like error reversal.  This
    second limiter persists the previous target and slews it at the live soft
    joint velocity limit times the physics step.  An optional acceleration
    bound makes direction reversals continuous for carried-object waypoints.
    ``limit_tracking_error=False`` lets the planar target integrator retain
    load compensation while still enforcing target speed and soft limits.
    """

    import torch

    max_delta = float(max_delta_rad)
    acceleration = None
    if max_acceleration_rad_s2 is not None:
        try:
            acceleration = float(max_acceleration_rad_s2)
        except (TypeError, ValueError):
            raise ValueError("max_acceleration_rad_s2 must be finite") from None
        if not math.isfinite(acceleration):
            raise ValueError("max_acceleration_rad_s2 must be finite")
        if acceleration <= 0.0:
            raise ValueError("max_acceleration_rad_s2 must be positive")

    # Acceleration limiting is an optional second stage.  Keep its state
    # separate from the position target so callers can turn it on for a
    # carried-object waypoint stream without changing the pick/place profile.
    # A caller may provide a state key when two control owners share the same
    # DifferentialIKController (for example, a ROS auxiliary controller and
    # the idealized runtime).  The default keeps the historical attributes and
    # therefore preserves existing external-controller behavior.
    state_key = None if target_state_key is None else str(target_state_key)

    def _read_state(name: str, default: Any = None) -> Any:
        if state_key is None:
            return getattr(controller, name, default)
        states = getattr(controller, "_eai_target_state_cache", None)
        if not isinstance(states, dict):
            return default
        state = states.get(state_key)
        if not isinstance(state, dict):
            return default
        return state.get(name, default)

    def _write_state(name: str, value: Any) -> None:
        if state_key is None:
            setattr(controller, name, value)
            return
        states = getattr(controller, "_eai_target_state_cache", None)
        if not isinstance(states, dict):
            states = {}
            controller._eai_target_state_cache = states
        state = states.setdefault(state_key, {})
        state[name] = value

    # A profile change starts from zero target velocity instead of inheriting
    # a direction from the preceding controller mode.
    previous_profile = _read_state("_eai_target_acceleration_profile")
    profile_changed = previous_profile != acceleration
    previous = _read_state("_eai_joint_position_target")
    stale = None
    if (
        previous is None
        or getattr(previous, "shape", None) != joint_pos.shape
        or previous.device != joint_pos.device
        or previous.dtype != joint_pos.dtype
        or not bool(torch.all(torch.isfinite(previous)))
    ):
        previous = joint_pos.clone()
    else:
        previous = previous.clone()
        # An idle controller or an external writer may have moved the arm
        # since this helper last ran.  A carried trajectory is allowed to lead
        # the measured pose by more than one DLS step while it builds speed;
        # use a wider stale threshold for that profile so the trajectory cache
        # is not mistaken for external motion.
        stale_threshold = max_delta if acceleration is None else max_delta * 4.0
        stale = torch.any(
            torch.abs(previous - joint_pos) > stale_threshold,
            dim=-1, keepdim=True,
        ) if limit_tracking_error else torch.zeros_like(joint_pos[:, :1], dtype=torch.bool)
        previous = torch.where(stale, joint_pos, previous)

    # A profile change resets only the target-velocity estimate.  Keep the
    # last position target so switching from carried motion back to pick/place
    # cannot create a discontinuity in the articulation drive command.  The
    # stale-row check above still resets both caches when an external writer
    # has moved the arm away from the cached target.

    previous_step = _read_state("_eai_joint_target_step")
    valid_previous_step = (
        previous_step is not None
        and getattr(previous_step, "shape", None) == joint_pos.shape
        and previous_step.device == joint_pos.device
        and previous_step.dtype == joint_pos.dtype
        and bool(torch.all(torch.isfinite(previous_step)))
    )
    if profile_changed or not valid_previous_step:
        previous_step = torch.zeros_like(joint_pos)
    elif stale is not None:
        previous_step = torch.where(
            stale,
            torch.zeros_like(previous_step),
            previous_step,
        )

    max_step = torch.full_like(joint_pos, max_delta)
    dt = _physics_step_dt(env)
    velocity_limits = getattr(
        getattr(arm, "data", None), "soft_joint_vel_limits", None
    )
    if velocity_limits is None:
        velocity_limits = getattr(
            getattr(arm, "data", None), "joint_vel_limits", None
        )
    if dt is not None and velocity_limits is not None:
        try:
            selected_limits = torch.abs(velocity_limits[:, list(joint_ids)]).to(
                device=joint_pos.device,
                dtype=joint_pos.dtype,
            )
            usable = torch.isfinite(selected_limits) & (selected_limits > 0.0)
            velocity_step = selected_limits * dt
            max_step = torch.where(
                usable,
                torch.minimum(max_step, velocity_step),
                max_step,
            )
        except (IndexError, RuntimeError, TypeError):
            pass

    desired_step = torch.clamp(desired - previous, -max_step, max_step)
    if acceleration is not None and dt is not None:
        # ``desired_step`` is radians per physics frame.  Limiting its change
        # by a rad/s^2 bound times dt^2 limits target acceleration while
        # preserving the existing per-joint velocity ceiling in ``max_step``.
        max_step_change = acceleration * dt * dt
        desired_step = previous_step + torch.clamp(
            desired_step - previous_step,
            -max_step_change,
            max_step_change,
        )
        desired_step = torch.clamp(desired_step, -max_step, max_step)

    # Compute the absolute target interval before applying its relative step
    # constraints.  Ordinary IK keeps the historical measured-pose safety
    # band.  The carried trajectory already has a velocity/acceleration
    # envelope; applying the measured-pose band a second time would stop a
    # valid waypoint early and make the arm choose a different IK branch.
    if acceleration is None and limit_tracking_error:
        target_low = torch.maximum(joint_pos - max_delta, limits[..., 0])
        target_high = torch.minimum(joint_pos + max_delta, limits[..., 1])
    else:
        target_low = limits[..., 0]
        target_high = limits[..., 1]
    safety_step_low = target_low - previous
    safety_step_high = target_high - previous

    if acceleration is not None and dt is not None:
        # Brake before a carried target reaches a joint soft limit.  The
        # discrete stopping distance is the sum of the remaining steps after
        # reducing target speed by ``max_step_change`` each frame.  The usual
        # carried waypoints are far from a joint limit, so this does not alter
        # their cruise profile.
        speed = torch.maximum(torch.abs(previous_step), torch.abs(desired_step))
        positive_step_count = torch.ceil(speed / max_step_change)
        positive_step_count = torch.where(
            speed > 1.0e-12,
            torch.maximum(positive_step_count, torch.ones_like(speed)),
            torch.zeros_like(speed),
        )
        stopping_distance = (
            positive_step_count * speed
            - 0.5
            * max_step_change
            * positive_step_count
            * (positive_step_count - 1.0)
        )
        upper_distance = target_high - previous
        lower_distance = previous - target_low
        direction_tolerance = torch.finfo(joint_pos.dtype).eps * 8.0
        positive_outward = (previous_step >= -direction_tolerance) & (
            desired_step + direction_tolerance >= previous_step
        )
        negative_outward = (previous_step <= direction_tolerance) & (
            desired_step - direction_tolerance <= previous_step
        )
        brake_positive = positive_outward & (
            upper_distance <= stopping_distance + 1.0e-12
        )
        brake_negative = negative_outward & (
            lower_distance <= stopping_distance + 1.0e-12
        )
        brake_step = previous_step - torch.sign(previous_step) * torch.minimum(
            torch.abs(previous_step),
            torch.full_like(previous_step, max_step_change),
        )
        # A zero previous step cannot be reduced further; holding zero is the
        # safe choice when the boundary is already inside the requested stop
        # distance.
        brake_step = torch.where(
            torch.abs(previous_step) <= 1.0e-12,
            torch.zeros_like(brake_step),
            brake_step,
        )
        desired_step = torch.where(
            brake_positive | brake_negative,
            brake_step,
            desired_step,
        )

    # Intersect the admissible step interval here rather than clamping the
    # final target after the acceleration calculation.  Under a valid state
    # the braking look-ahead makes this a no-op; it remains a last-resort
    # safety guard for a target changed by an external controller.
    desired_step = torch.maximum(
        torch.minimum(desired_step, safety_step_high),
        safety_step_low,
    )
    desired = previous + desired_step
    _write_state("_eai_joint_target_step", (desired - previous).detach().clone())
    _write_state("_eai_target_acceleration_profile", acceleration)
    _write_state("_eai_joint_position_target", desired.detach().clone())
    return desired


def _bounded_dls_delta(jacobian, error, lower, upper, damping):
    """Box-constrained DLS using a small batched active-set solve.

    Clipping an unconstrained IK solution leaves the other joints solving for
    motion the limited joint cannot make. Re-solve those joints against the
    remaining task error, releasing bounds when their KKT sign is incorrect.
    Bounds are joint displacements relative to the measured configuration.
    """
    import torch

    jt = jacobian.transpose(-1, -2)
    count = jacobian.shape[-1]
    hessian = jt @ jacobian + float(damping) ** 2 * torch.eye(
        count, device=jacobian.device, dtype=jacobian.dtype)
    rhs = (jt @ error.unsqueeze(-1)).squeeze(-1)
    delta = torch.linalg.solve(hessian, rhs.unsqueeze(-1)).squeeze(-1)
    active = torch.where(delta < lower, -1, torch.where(delta > upper, 1, 0))
    delta = delta.clamp(min=lower, max=upper)
    done = torch.zeros(delta.shape[0], device=delta.device, dtype=torch.bool)
    tolerance = 1.e-6 if delta.dtype == torch.float32 else 1.e-10
    for _ in range(8 * count + 8):
        free = active == 0
        fixed = torch.where(free, torch.zeros_like(delta), delta)
        matrix = hessian * free.unsqueeze(-1) * free.unsqueeze(-2)
        matrix = matrix + torch.diag_embed((~free).to(delta.dtype))
        residual = rhs - (hessian @ fixed.unsqueeze(-1)).squeeze(-1)
        candidate = fixed + torch.linalg.solve(
            matrix, (residual * free).unsqueeze(-1)).squeeze(-1)
        direction = candidate - delta
        crosses_lower = free & (candidate < lower - tolerance)
        crosses_upper = free & (candidate > upper + tolerance)
        crosses = crosses_lower | crosses_upper
        safe_direction = torch.where(direction.abs() > tolerance, direction, torch.ones_like(direction))
        fraction = torch.where(crosses_lower, (lower - delta) / safe_direction,
                               torch.where(crosses_upper, (upper - delta) / safe_direction,
                                           torch.ones_like(delta)))
        alpha, hit = fraction.clamp(0., 1.).min(dim=-1)
        next_delta = (delta + alpha.unsqueeze(-1) * direction).clamp(min=lower, max=upper)
        delta = torch.where(done.unsqueeze(-1), delta, next_delta)
        blocked = crosses.any(dim=-1) & ~done
        hit_mask = torch.nn.functional.one_hot(hit, count).bool() & blocked.unsqueeze(-1)
        active = torch.where(hit_mask, torch.where(direction < 0, -1, 1), active)

        gradient = (hessian @ delta.unsqueeze(-1)).squeeze(-1) - rhs
        violation = torch.where(active < 0, -gradient, torch.where(active > 0, gradient, 0.))
        worst, release = violation.max(dim=-1)
        release_mask = (torch.nn.functional.one_hot(release, count).bool()
                        & (~blocked & ~done & (worst > tolerance)).unsqueeze(-1))
        active = torch.where(release_mask, 0, active)
        done = done | (~blocked & (worst <= tolerance))
        if bool(done.all()):
            break
    return delta


def apply_end_effector_pose(
    env: Any,
    arm: Any,
    robot_name: str,
    model_spec: Any,
    target_position_w: Sequence[float],
    target_orientation_wxyz: Sequence[float] | None = None,
    *,
    max_delta_rad: float = 0.10,
    dls_lambda: float = 0.02,
    max_acceleration_rad_s2: float | None = None,
    target_state_key: str | None = None,
) -> bool:
    """Apply one world-frame end-effector pose through damped differential IK.

    The imports are intentionally lazy because this module is also used by
    lightweight controller and configuration checks that do not load Isaac
    Sim.  ``model_spec`` is structural rather than tied to a concrete robot
    class: it must expose ``model``, ``joint_names`` and ``ee_body_names``.

    The helper writes joint *position targets*; it does not teleport the arm
    or run a second physics loop.  This makes it safe to call from
    ``MultiRobotDirectEnv._apply_action`` after other controllers have run.
    ``target_state_key`` optionally namespaces the cross-frame target-slew
    state for a control owner that must coexist with another IK writer.
    """

    import torch
    from isaaclab.controllers import DifferentialIKController, DifferentialIKControllerCfg
    from isaaclab.utils.math import (
        compute_pose_error,
        matrix_from_quat,
        quat_inv,
        subtract_frame_transforms,
    )

    required = ("model", "joint_names", "ee_body_names")
    if model_spec is None or any(not hasattr(model_spec, field) for field in required):
        return False

    joint_ids, _ = arm.find_joints(
        list(model_spec.joint_names), preserve_order=True
    )
    try:
        joint_ids = [int(value) for value in joint_ids]
    except (TypeError, ValueError):
        return False
    if len(joint_ids) != len(model_spec.joint_names):
        return False

    ee_id = resolve_end_effector_body_id(arm, model_spec.ee_body_names)
    if ee_id is None:
        return False

    num_instances = int(getattr(arm, "num_instances", 1))
    device = getattr(arm, "device", None)
    if device is None:
        device = getattr(getattr(arm, "data", None), "joint_pos", torch.zeros(1)).device

    target_pos = torch.as_tensor(
        tuple(float(value) for value in target_position_w),
        device=device,
        dtype=torch.float32,
    ).reshape(1, 3).expand(num_instances, -1)

    current_world_quat = arm.data.body_quat_w[:, ee_id]
    if target_orientation_wxyz is None:
        target_quat = current_world_quat.clone()
    else:
        target_quat = torch.as_tensor(
            tuple(float(value) for value in target_orientation_wxyz),
            device=device,
            dtype=torch.float32,
        ).reshape(1, 4)
        norm = torch.linalg.vector_norm(target_quat, dim=-1, keepdim=True)
        if bool(torch.any(norm <= 1.0e-9)):
            return False
        target_quat = (target_quat / norm).expand(num_instances, -1)

    root_pos = arm.data.root_pos_w
    root_quat = arm.data.root_quat_w
    ee_pos_b, ee_quat_b = subtract_frame_transforms(
        root_pos,
        root_quat,
        arm.data.body_pos_w[:, ee_id],
        arm.data.body_quat_w[:, ee_id],
    )
    target_pos_b, target_quat_b = subtract_frame_transforms(
        root_pos,
        root_quat,
        target_pos,
        target_quat,
    )

    jacobians = arm.root_physx_view.get_jacobians()
    if len(jacobians.shape) != 4:
        return False

    # PhysX may prepend six virtual floating-base DOFs.  Derive that offset
    # from the live Jacobian width and articulation joint count instead of
    # assuming a particular robot's ``is_fixed_base`` flag is populated.
    jacobian_dof_count = int(jacobians.shape[-1])
    articulation_joint_count = int(
        getattr(arm, "num_joints", max(jacobian_dof_count, max(joint_ids, default=-1) + 1))
    )
    jacobian_offset = jacobian_dof_count - articulation_joint_count
    if jacobian_offset not in (0, 6):
        fixed_base = bool(getattr(arm, "is_fixed_base", jacobian_offset == 0))
        jacobian_offset = 0 if fixed_base else 6
    columns = [joint_id + jacobian_offset for joint_id in joint_ids]
    if any(column < 0 or column >= jacobian_dof_count for column in columns):
        fallback_columns = list(joint_ids)
        if any(column < 0 or column >= jacobian_dof_count for column in fallback_columns):
            return False
        columns = fallback_columns

    row = resolve_jacobian_row(arm, ee_id, model_spec.ee_body_names, row_count=int(jacobians.shape[1]))
    if row is None:
        jacobian_link_count = int(jacobians.shape[1])
        body_count = int(getattr(arm, "num_bodies", 0) or 0)
        if body_count > 0 and jacobian_link_count == body_count:
            row = ee_id
        elif body_count > 0 and jacobian_link_count == body_count - 1:
            row = ee_id - 1
        else:
            row = ee_id if not bool(getattr(arm, "is_fixed_base", True)) else ee_id - 1
        if row < 0 or row >= jacobian_link_count:
            alternate_row = ee_id if row == ee_id - 1 else ee_id - 1
            if alternate_row < 0 or alternate_row >= jacobian_link_count:
                return False
            row = alternate_row
    jacobian = jacobians[:, row, :, columns].clone()

    # DifferentialIK consumes the Jacobian in the same root-aligned coordinates
    # as ``ee_pos_b``/``target_pos_b``.  Match the established provider
    # controller by rotating both spatial blocks with the inverse root pose.
    base_rot = matrix_from_quat(quat_inv(root_quat))
    jacobian[:, :3, :] = torch.bmm(base_rot, jacobian[:, :3, :])
    jacobian[:, 3:, :] = torch.bmm(base_rot, jacobian[:, 3:, :])

    cache = getattr(env, "_eai_differential_ik_controllers", None)
    if cache is None:
        cache = {}
        env._eai_differential_ik_controllers = cache
    cache_key = (
        str(robot_name),
        str(getattr(model_spec, "model", "manipulator")),
        str(device),
        num_instances,
        len(joint_ids),
        float(dls_lambda),
    )
    controller = cache.get(cache_key)
    if controller is None:
        controller = DifferentialIKController(
            DifferentialIKControllerCfg(
                command_type="pose",
                use_relative_mode=False,
                ik_method="dls",
                ik_params={"lambda_val": float(dls_lambda)},
            ),
            num_envs=num_instances,
            device=device,
        )
        cache[cache_key] = controller

    controller.set_command(torch.cat((target_pos_b, target_quat_b), dim=-1))
    joint_pos = arm.data.joint_pos[:, joint_ids]
    desired = controller.compute(ee_pos_b, ee_quat_b, jacobian, joint_pos)
    limits = arm.data.soft_joint_pos_limits[:, joint_ids]
    limited = ((desired < limits[..., 0]) | (desired > limits[..., 1])).any(dim=-1)
    if bool(limited.any()):
        position_error, rotation_error = compute_pose_error(
            ee_pos_b[limited], ee_quat_b[limited], target_pos_b[limited], target_quat_b[limited],
            rot_error_type="axis_angle")
        correction = _bounded_dls_delta(
            jacobian[limited], torch.cat((position_error, rotation_error), dim=-1),
            limits[limited, :, 0] - joint_pos[limited],
            limits[limited, :, 1] - joint_pos[limited], dls_lambda)
        desired[limited] = joint_pos[limited] + correction
    desired = joint_pos + torch.clamp(
        desired - joint_pos,
        -float(max_delta_rad),
        float(max_delta_rad),
    )
    desired = torch.maximum(torch.minimum(desired, limits[..., 1]), limits[..., 0])
    desired = _slew_limit_joint_position_target(
        env,
        arm,
        controller,
        joint_ids,
        joint_pos,
        desired,
        limits,
        max_delta_rad,
        max_acceleration_rad_s2=max_acceleration_rad_s2,
        target_state_key=target_state_key,
    )
    arm.set_joint_position_target(desired, joint_ids=joint_ids)
    return True


def apply_manipulator_pose_command(
    env: Any,
    arm: Any,
    robot_name: str,
    model_spec: Any,
    command: Any,
    *,
    max_delta_rad: float = 0.10,
    dls_lambda: float = 0.02,
    max_acceleration_rad_s2: float | None = None,
) -> bool:
    """Convert a ROS manipulator pose command to the shared world-frame IK call.

    Provider controller modules are resolver-managed runtime assets, so the
    tracked loader installs a small adapter that calls this function instead of
    maintaining a second numerical IK implementation in the downloaded file.
    This conversion preserves the established ``world``/``base_link`` command
    frames and captures an omitted orientation once for each distinct target.
    """

    import torch
    from isaaclab.utils.math import combine_frame_transforms

    if command is None or not hasattr(command, "position"):
        return False

    num_instances = int(getattr(arm, "num_instances", 1))
    device = getattr(arm, "device", None)
    if device is None:
        device = getattr(getattr(arm, "data", None), "joint_pos", torch.zeros(1)).device

    try:
        target_pos = torch.as_tensor(
            tuple(float(value) for value in command.position),
            device=device,
            dtype=torch.float32,
        ).reshape(1, 3).expand(num_instances, -1)
    except (TypeError, ValueError, RuntimeError):
        return False

    orientation_xyzw = getattr(command, "orientation_xyzw", None)
    target_quat = None
    if orientation_xyzw is not None:
        try:
            x, y, z, w = (float(value) for value in orientation_xyzw)
            target_quat = torch.tensor(
                [[w, x, y, z]], device=device, dtype=torch.float32
            ).expand(num_instances, -1)
        except (TypeError, ValueError, RuntimeError):
            return False

    frame_id = str(getattr(command, "frame_id", "world"))
    if target_quat is None:
        ee_id = resolve_end_effector_body_id(arm, model_spec.ee_body_names)
        if ee_id is None:
            return False
        cache = getattr(env, "_manipulator_hold_orientations", None)
        if cache is None:
            cache = {}
            env._manipulator_hold_orientations = cache
        hold_key = (str(robot_name), str(model_spec.model))
        hold_signature = (frame_id, tuple(float(value) for value in command.position))
        cached = cache.get(hold_key)
        if cached is None or cached[0] != hold_signature:
            cached = (hold_signature, arm.data.body_quat_w[:, ee_id].clone())
            cache[hold_key] = cached
        target_quat = cached[1]

    if frame_id == "base_link":
        host = getattr(getattr(env, "scene", None), "articulations", {}).get(robot_name)
        if host is None:
            return False
        identity_quat = torch.zeros(
            (num_instances, 4), device=device, dtype=torch.float32
        )
        identity_quat[:, 0] = 1.0
        target_pos, transformed_quat = combine_frame_transforms(
            host.data.root_pos_w,
            host.data.root_quat_w,
            target_pos,
            identity_quat if orientation_xyzw is None else target_quat,
        )
        if orientation_xyzw is not None:
            target_quat = transformed_quat

    return apply_end_effector_pose(
        env,
        arm,
        robot_name,
        model_spec,
        target_pos[0],
        target_quat[0],
        max_delta_rad=max_delta_rad,
        dls_lambda=dls_lambda,
        max_acceleration_rad_s2=max_acceleration_rad_s2,
    )


def apply_planar_end_effector_pose(
    env: Any,
    arm: Any,
    robot_name: str,
    model_spec: Any,
    target_position_w: Sequence[float],
    target_orientation_wxyz: Sequence[float],
    *,
    plane_yaw_rad: float,
    yaw_joint_id: int,
    yaw_joint_target_rad: float,
    ik_joint_ids: Sequence[int],
    fixed_joint_ids: Sequence[int] = (),
    rotate_only: bool = False,
    max_delta_rad: float = 0.10,
    dls_lambda: float = 0.02,
    integration_gain_s: float = 6.0,
) -> bool:
    """Drive yaw directly, or solve x/z and tool orientation in its plane.

    The yaw and fixed joints never enter the DLS system. During ROTATE all
    other position targets are held exactly, so the tool rotates with the
    shoulder rather than counter-rotating through wrist IK. The cache belongs
    exclusively to idealized manipulation and is removed by runtime.reset().
    """
    if isinstance(integration_gain_s, bool) or not math.isfinite(integration_gain_s) or integration_gain_s <= 0:
        return False
    from types import SimpleNamespace

    import torch
    from isaaclab.utils.math import (
        compute_pose_error, matrix_from_quat, quat_inv, quat_mul,
    )

    ee_id = resolve_end_effector_body_id(arm, model_spec.ee_body_names)
    arm_ids, _ = arm.find_joints(list(model_spec.joint_names), preserve_order=True)
    arm_ids = [int(i) for i in arm_ids]
    ik_ids = [int(i) for i in ik_joint_ids]
    fixed_ids = [int(i) for i in fixed_joint_ids]
    if ee_id is None or not ik_ids or len(arm_ids) != len(model_spec.joint_names):
        return False
    if yaw_joint_id in ik_ids or set(fixed_ids).intersection(ik_ids):
        return False
    data = arm.data
    cache = getattr(env, "_eai_planar_ik_states", None)
    if cache is None:
        cache = {}
        env._eai_planar_ik_states = cache
    key = (str(robot_name), str(model_spec.model))
    owner = cache.get(key)
    if owner is None or owner.arm is not arm:
        # ROTATE must preserve the targets present when the skill takes
        # ownership; measured joint angles can differ under gravity/load.
        initial_targets = getattr(data, "joint_pos_target", data.joint_pos)[:, arm_ids]
        owner = SimpleNamespace(arm=arm, targets=initial_targets.clone())
        cache[key] = owner
    targets = owner.targets.clone()
    index = {joint_id: k for k, joint_id in enumerate(arm_ids)}
    if yaw_joint_id not in index or any(i not in index for i in ik_ids + fixed_ids):
        return False
    targets[:, index[yaw_joint_id]] = float(yaw_joint_target_rad)
    dt = _physics_step_dt(env) or 1.0 / 60.0
    for joint_id in fixed_ids:
        # Start from the last target, allowing an externally moved wrist to
        # return to the fixed angle through ordinary position drives.
        k = index[joint_id]
        speed = data.soft_joint_vel_limits[:, joint_id].abs()
        step = torch.minimum(speed * dt, torch.full_like(speed, max_delta_rad))
        targets[:, k] += torch.clamp(-targets[:, k], -step, step)

    if not rotate_only:
        jacobians = arm.root_physx_view.get_jacobians()
        dof_offset = int(jacobians.shape[-1]) - int(arm.num_joints)
        if dof_offset not in (0, 6):
            return False
        # PhysX merges fixed-joint links, so match the live link paths first;
        # the historical count heuristics only serve views without link_paths.
        row = resolve_jacobian_row(arm, ee_id, model_spec.ee_body_names, row_count=int(jacobians.shape[1]))
        if row is None:
            row = ee_id if jacobians.shape[1] == arm.num_bodies else ee_id - 1
        if row < 0 or row >= jacobians.shape[1]:
            return False
        jacobian_w = jacobians[:, row, :, [i + dof_offset for i in ik_ids]]
        current_pos = data.body_pos_w[:, ee_id]
        current_quat = data.body_quat_w[:, ee_id]
        target_pos = current_pos.new_tensor(target_position_w).reshape(1, 3)
        target_quat = current_quat.new_tensor(target_orientation_wxyz).reshape(1, 4)
        target_quat = target_quat / torch.linalg.vector_norm(target_quat, dim=-1, keepdim=True)
        pos_error_w, rot_error_w = compute_pose_error(
            current_pos, current_quat, target_pos, target_quat, rot_error_type="axis_angle",
        )
        half = 0.5 * float(plane_yaw_rad)
        yaw_quat = current_quat.new_tensor((math.cos(half), 0.0, 0.0, math.sin(half))).reshape(1, 4)
        plane_quat = quat_mul(data.root_quat_w, yaw_quat)
        world_to_plane = matrix_from_quat(quat_inv(plane_quat))
        pos_error = torch.bmm(world_to_plane, pos_error_w.unsqueeze(-1)).squeeze(-1)
        rot_error = torch.bmm(world_to_plane, rot_error_w.unsqueeze(-1)).squeeze(-1)
        linear = torch.bmm(world_to_plane, jacobian_w[:, :3])
        angular = torch.bmm(world_to_plane, jacobian_w[:, 3:])
        jacobian = torch.cat((linear[:, [0, 2]], angular), dim=1)
        error = torch.cat((pos_error[:, [0, 2]], rot_error), dim=-1)
        identity = torch.eye(5, device=error.device, dtype=error.dtype).unsqueeze(0)
        correction = torch.bmm(
            jacobian.transpose(1, 2),
            torch.linalg.solve(
                jacobian @ jacobian.transpose(1, 2) + float(dls_lambda) ** 2 * identity,
                error.unsqueeze(-1),
            ),
        ).squeeze(-1)
        measured = data.joint_pos[:, ik_ids]
        # Integrate from the preceding drive target. Replacing it with
        # measured+correction each tick leaves a load-dependent steady error
        # (notably Z1 under gravity). The existing slew/limit guard bounds the
        # accumulated correction's speed and actual soft limits. A unit gain
        # per physics frame would make this outer loop oscillate on Z1.
        desired = targets[:, [index[i] for i in ik_ids]] + torch.clamp(
            correction * min(1.0, integration_gain_s * dt), -max_delta_rad, max_delta_rad,
        )
        limits = data.soft_joint_pos_limits[:, ik_ids]
        desired = _slew_limit_joint_position_target(
            env, arm, owner, ik_ids, measured, desired, limits, max_delta_rad,
            limit_tracking_error=False,
        )
        if not bool(torch.isfinite(desired).all()):
            return False
        targets[:, [index[i] for i in ik_ids]] = desired
    limits = data.soft_joint_pos_limits[:, arm_ids]
    if bool(torch.any(targets < limits[..., 0] - 1.0e-6)) or bool(torch.any(targets > limits[..., 1] + 1.0e-6)):
        return False
    owner.targets = targets.detach().clone()
    # Write the full-width target buffer: IsaacLab's subset advanced-indexing
    # write has been observed to drop position targets on some CPU PhysX
    # builds, leaving the affected joints tracking a stale stream.
    full_targets = data.joint_pos_target.clone()
    full_targets[:, arm_ids] = targets
    arm.set_joint_position_target(full_targets)
    return True


def hold_gripper_position(
    arm: Any, model_spec: Any, *, position_rad: float | None = None,
) -> bool:
    """Hold an optional gripper at its measured or explicitly requested angle."""

    import torch

    gripper_name = getattr(model_spec, "gripper_joint_name", None)
    if not gripper_name:
        return True
    gripper_ids, _ = arm.find_joints([str(gripper_name)], preserve_order=True)
    try:
        gripper_ids = [int(value) for value in gripper_ids]
    except (TypeError, ValueError):
        return False
    if not gripper_ids:
        return False
    gripper_id = gripper_ids[0]
    current = arm.data.joint_pos[:, [gripper_id]]
    if position_rad is not None:
        try:
            position = float(position_rad)
        except (TypeError, ValueError):
            return False
        if not math.isfinite(position):
            return False
        current = torch.full_like(current, position)
    limits = arm.data.soft_joint_pos_limits[:, [gripper_id]]
    current = torch.maximum(torch.minimum(current, limits[..., 1]), limits[..., 0])
    arm.set_joint_position_target(current, joint_ids=[gripper_id])
    return True
