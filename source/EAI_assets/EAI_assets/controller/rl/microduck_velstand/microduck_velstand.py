# Copyright (c) 2022-2025, The Isaac Lab Project Developers.
# All rights reserved.
#
# SPDX-License-Identifier: BSD-3-Clause
"""MicroDuck VelStand ONNX Controller Configuration.

Runs the official pollen ``velstand.onnx`` walking/standing/recovery policy
(HF ``pollen-robotics/microduck-policies``, Apache-2.0; task
``Mjlab-VelStand-Rough-Backlash-MicroDuck``, run ``0045i2si@2749``, policy
manifest v8) on the PhysX-side MicroDuck articulation through onnxruntime.

Observation layout (61-D, in order — verified against the ONNX graph metadata
``observation_names`` and microduck_rl ``scripts/infer_policy.py``
``get_observations``; term order also matches the mjlab training env):

==== ============================= ====== ==========================================
idx  term                          dim    source / convention
==== ============================= ====== ==========================================
0    base_ang_vel                  3      trunk-frame gyro (rad/s)
3    projected_gravity             3      gravity dir in trunk frame, upright = [0,0,-1]
6    joint_pos                     14     q - DEFAULT_POSE (rad, policy joint order)
20   joint_vel                     14     raw joint velocities (rad/s)
34   actions                       14     previous control step's raw action
48   command (twist)               3      [vx, vy, yaw_rate] (m/s, m/s, rad/s)
51   head_command                  4      head pose deltas (kept at zero)
55   body_command                  6      [x,y,z,roll,pitch,yaw] deltas (kept at zero)
==== ============================= ====== ==========================================

The ONNX graph has the rsl_rl EmpiricalNormalization baked in
(``actor(normalizer(obs))`` — microduck_rl ``export.py``), so this controller
feeds RAW observations and performs no normalization of its own.

Actions (14-D) are joint position offsets: ``target = DEFAULT_POSE + action
* 1.0`` (ONNX metadata ``action_scale=1.0``; same as mjlab
JointPositionActionCfg scale 1.0 with default offset).

Control rate
------------
The policy was trained at 50 Hz (MuJoCo dt 0.005 x decimation 4 = 0.02 s,
the daemon loop rate).  The hmrs environment already runs controllers at
exactly that rate (sim.dt 0.005, env decimation 4): ``_pre_physics_step``
computes one action per 20 ms control tick and ``_apply_action`` re-applies
the SAME target on every 5 ms physics substep in between — the "infer every
4 physics steps, hold the last target in between" contract.  If the env
decimation ever changes, keep ``MICRODUCK_VELSTAND_DECIMATION`` in sync so
``policy rate == sim rate / decimation`` stays 50 Hz.

MuJoCo -> PhysX conventions (risks kept visible for the smoke stage)
--------------------------------------------------------------------
- Gyro frame: mjlab reads the freejoint qvel[3:6], which MuJoCo expresses in
  the body (link) frame.  IsaacLab's ``root_ang_vel_b`` is the COM-frame
  angular velocity instead, so this controller uses ``root_link_ang_vel_b``.
  For this robot the link and COM frames nearly coincide; if the smoke test
  shows a persistent wobble/rotation bias, this is the first knob
  (MICRODUCK_VELSTAND_USE_LINK_FRAME_GYRO = False switches to the COM frame).
- Projected gravity: identical construction on both sides (world [0,0,-1]
  rotated by the inverse trunk quaternion); no conversion needed.
- Joint order: policy joints are gathered BY NAME from the articulation, so a
  USD-side joint reorder cannot silently scramble the observation/action
  vectors.
- Joint velocities/positions: rad and rad/s on both sides; mjlab trained with
  a 1-step joint_vel lag and +-0.25 rad/s obs noise (plus IMU mounting
  +-6 deg and encoder +-0.86 deg DR), so small PhysX/MuJoCo sensor
  differences are inside the trained envelope.
- Actuator: the policy was trained against the BAM M6 voltage model
  (torque-limited PD with 3-6 step delay).  The PhysX DCMotor approximation
  in ``EAI_assets.robots.microduck`` is harder and delay-free; if the gait
  buzzes or the knees collapse in the smoke test, tune stiffness there first.
- Command envelope: trained twist ranges are vx +-0.4, vy +-0.3, yaw +-1.0
  rad/s.  Commands are clipped into that envelope (training-range clipping,
  not a control law); ``MICRODUCK_VELSTAND_CMD_LIMITS`` exposes it for
  smoke-stage tuning.  v8 tracks yaw down to ~0.5 rad/s in place; the v7
  yaw remap is intentionally NOT applied (v8 takes the true command).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import torch

from isaaclab.utils import configclass

from EAI.controllers import RSLControllerCfg
from EAI_assets.robots.microduck import (
    MICRODUCK_DEFAULT_JOINT_POS,
    MICRODUCK_JOINT_NAMES,
)


_RL_DIR = Path(__file__).parent

MICRODUCK_VELSTAND_OBS_DIM = 61
MICRODUCK_VELSTAND_ACTION_DIM = 14

# Observation segments (see module docstring table).  The slice bounds are the
# single source of truth for tests and for any future debugger UI.
MICRODUCK_VELSTAND_OBS_SLICES = {
    "base_ang_vel": (0, 3),
    "projected_gravity": (3, 6),
    "joint_pos": (6, 20),
    "joint_vel": (20, 34),
    "actions": (34, 48),
    "command": (48, 51),
    "head_command": (51, 55),
    "body_command": (55, 61),
}
assert sum(end - start for start, end in MICRODUCK_VELSTAND_OBS_SLICES.values()) == MICRODUCK_VELSTAND_OBS_DIM

# ONNX metadata action_scale = 1.0 (mjlab JointPositionActionCfg scale 1.0).
MICRODUCK_VELSTAND_ACTION_SCALE = 1.0

# Policy control rate contract: sim 200 Hz / decimation 4 = 50 Hz policy.
MICRODUCK_VELSTAND_DECIMATION = 4

# Trained twist command envelope (mjlab velocity recipe fixed ranges).
MICRODUCK_VELSTAND_CMD_LIMITS = ((-0.4, 0.4), (-0.3, 0.3), (-1.0, 1.0))

# Gyro frame switch for the MuJoCo->PhysX smoke calibration (see docstring).
MICRODUCK_VELSTAND_USE_LINK_FRAME_GYRO = True

# Default joint offsets in POLICY joint order (raw action semantics).
MICRODUCK_VELSTAND_DEFAULT_POSE = torch.tensor(
    [MICRODUCK_DEFAULT_JOINT_POS[name] for name in MICRODUCK_JOINT_NAMES],
    dtype=torch.float32,
)


def _policy_joint_gather_indices(robot: Any) -> list[int]:
    """Articulation indices of the policy joints, in policy order.

    Gathering by name keeps the observation/action columns correct even if the
    USD articulation ever lists its joints in a different order than the MJCF.
    """
    articulation_names = list(robot.joint_names)
    try:
        return [articulation_names.index(name) for name in MICRODUCK_JOINT_NAMES]
    except ValueError as exc:
        missing = sorted(set(MICRODUCK_JOINT_NAMES) - set(articulation_names))
        raise ValueError(
            f"MicroDuck velstand policy expects joints {MICRODUCK_JOINT_NAMES}; "
            f"articulation provides {articulation_names} (missing: {missing})."
        ) from exc


def _policy_joint_index_tensor(robot: Any, device: torch.device) -> torch.Tensor:
    return torch.as_tensor(_policy_joint_gather_indices(robot), device=device, dtype=torch.long)


def _clip_twist_command(command: torch.Tensor) -> torch.Tensor:
    """Clip the [vx, vy, yaw_rate] command into the trained envelope."""
    lower = torch.tensor([lo for lo, _hi in MICRODUCK_VELSTAND_CMD_LIMITS], device=command.device)
    upper = torch.tensor([hi for _lo, hi in MICRODUCK_VELSTAND_CMD_LIMITS], device=command.device)
    return torch.clamp(command, min=lower, max=upper)


def _get_twist_command(env: Any, robot_name: str, reference: torch.Tensor) -> torch.Tensor:
    """Fetch the [vx, vy, yaw_rate] command with the go2 fallback ladder."""
    command = None
    if hasattr(env, "get_command") and callable(env.get_command):
        command = env.get_command(robot_name, "base_velocity")
    if command is None:
        for attr_name in (
            f"_base_velocity_{robot_name}",
            f"_velocity_command_{robot_name}",
            "_base_velocity",
            "_velocity_command",
            "base_velocity",
            "velocity_command",
        ):
            if hasattr(env, attr_name):
                command = getattr(env, attr_name)
                if command is not None:
                    break
    if command is None:
        return torch.zeros_like(reference[:, :3])
    if command.shape[-1] == 2:
        # Differential-drive style [vx, yaw]: vy = 0 (mirrors go2 handling).
        command = torch.cat(
            [command[:, 0:1], torch.zeros_like(command[:, 0:1]), command[:, 1:2]], dim=-1
        )
    elif command.shape[-1] > 3:
        command = command[:, :3]
    return _clip_twist_command(command.float())


def _get_last_action(env: Any, robot_name: str, reference: torch.Tensor, num_joints: int) -> torch.Tensor:
    """Previous control step's raw policy action (mjlab 'actions' obs term)."""
    last_action = None
    if hasattr(env, "actions_dict") and robot_name in env.actions_dict:
        last_action = env.actions_dict[robot_name]
    if last_action is None or last_action.shape[-1] != num_joints:
        return torch.zeros(reference.shape[0], num_joints, device=reference.device, dtype=torch.float32)
    return last_action.float()


def compute_microduck_velstand_observations(env: Any, robot_name: str) -> torch.Tensor:
    """Assemble the 61-D velstand observation from articulation data.

    Returns:
        Observation tensor of shape (num_envs, 61).
    """
    robot = env.scene.articulations[robot_name]
    indices = _policy_joint_index_tensor(robot, robot.data.root_ang_vel_w.device)

    # MuJoCo freejoint qvel[3:6] is link-frame; use root_link_ang_vel_b (see
    # module docstring gyro-frame note).
    if MICRODUCK_VELSTAND_USE_LINK_FRAME_GYRO:
        base_ang_vel_b = robot.data.root_link_ang_vel_b
    else:
        base_ang_vel_b = robot.data.root_ang_vel_b
    projected_gravity_b = robot.data.projected_gravity_b

    joint_pos = robot.data.joint_pos.index_select(dim=-1, index=indices)
    joint_vel = robot.data.joint_vel.index_select(dim=-1, index=indices)
    # default_joint_pos rows follow the articulation order; gather identically.
    joint_pos_rel = joint_pos - robot.data.default_joint_pos.index_select(dim=-1, index=indices)

    twist_command = _get_twist_command(env, robot_name, base_ang_vel_b)
    last_action = _get_last_action(env, robot_name, base_ang_vel_b, MICRODUCK_VELSTAND_ACTION_DIM)

    obs = torch.cat(
        [
            base_ang_vel_b.float(),          # 0:3
            projected_gravity_b.float(),     # 3:6
            joint_pos_rel.float(),           # 6:20
            joint_vel.float(),               # 20:34
            last_action,                     # 34:48
            twist_command,                   # 48:51
            torch.zeros(base_ang_vel_b.shape[0], 4, device=base_ang_vel_b.device),  # 51:55 head
            torch.zeros(base_ang_vel_b.shape[0], 6, device=base_ang_vel_b.device),  # 55:61 body
        ],
        dim=-1,
    )
    return obs


def apply_microduck_velstand_action(
    env: Any,
    robot_name: str,
    action: torch.Tensor,
    controller_dict: Dict[str, Any],
) -> None:
    """Apply a velstand action as joint position targets (all 14 servos).

    target = DEFAULT_POSE + action * 1.0, re-scattered from policy order into
    articulation order.  Called once per physics substep with the action held
    from the last 50 Hz inference (the env's decimation contract).
    """
    robot = env.scene.articulations[robot_name]
    num_joints = robot.num_joints
    action_dim = action.shape[-1] if len(action.shape) > 1 else action.shape[0]
    if action_dim != MICRODUCK_VELSTAND_ACTION_DIM:
        raise ValueError(
            f"MicroDuck velstand action dimension {action_dim} != {MICRODUCK_VELSTAND_ACTION_DIM}."
        )

    indices = _policy_joint_index_tensor(robot, action.device)
    default_pose = MICRODUCK_VELSTAND_DEFAULT_POSE.to(action.device).expand(action.shape[0], -1)
    policy_targets = action.float() * MICRODUCK_VELSTAND_ACTION_SCALE + default_pose

    if len(indices) == num_joints and indices.tolist() == list(range(num_joints)):
        robot.set_joint_position_target(policy_targets, joint_ids=slice(None))
        return
    # Policy order differs from articulation order: scatter into place.
    targets = robot.data.default_joint_pos.clone()
    targets.index_copy_(dim=-1, index=indices, source=policy_targets)
    robot.set_joint_position_target(targets, joint_ids=slice(None))


def compute_microduck_velstand_action_from_command(
    controller_cfg: Any,
    env: Any,
    robot_name: str,
    command: torch.Tensor,
    controller_dict: Dict[str, Any],
) -> torch.Tensor:
    """Compute an action from a [vx, vy, yaw_rate] command (cmd_vel semantics).

    Stores the command on the env (the observation reads it back), assembles
    the 61-D observation and runs the ONNX actor.  Head/body command slots
    stay at zero — the deployment default of the velstand policy.
    """
    if hasattr(env, "set_command"):
        env.set_command(robot_name, controller_cfg.command_name, command)
    obs = controller_cfg.observation_func(env, robot_name)
    return controller_cfg.compute_action(env, robot_name, obs, controller_dict)


@configclass
class MicroduckVelStandControllerCfg(RSLControllerCfg):
    """MicroDuck velstand ONNX controller configuration."""

    # Twist command dimension advertised to the env's action-space inference.
    command_dim: int = 3


# MicroDuck VelStand RSL Controller Configuration
MICRODUCK_VELSTAND_CFG = MicroduckVelStandControllerCfg(
    model_path=str(_RL_DIR / "model" / "velstand.onnx"),
    robot_type="MicroDuck",
    command_name="base_velocity",
    observation_func=compute_microduck_velstand_observations,
    apply_action_func=apply_microduck_velstand_action,
    compute_action_from_command_func=compute_microduck_velstand_action_from_command,
)
