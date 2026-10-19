"""Deploy the 753-D Go2 Extreme Parkour Teacher policy in EAI.

The observation and action contracts mirror the local Extreme Parkour
training task.  Stateful history and five-step sensor caches live in the
per-robot controller dictionary so multiple Go2 instances remain isolated.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import torch
from .math_utils import euler_xyz_from_quat, wrap_to_pi



EXTREME_PARKOUR_HISTORY_LENGTH = 10
EXTREME_PARKOUR_PROPRIO_DIM = 53
EXTREME_PARKOUR_SCAN_DIM = 132
EXTREME_PARKOUR_EXPLICIT_DIM = 9
EXTREME_PARKOUR_PRIVILEGED_DIM = 29
EXTREME_PARKOUR_OBSERVATION_DIM = 753
EXTREME_PARKOUR_UPDATE_INTERVAL = 5

EXTREME_PARKOUR_JOINT_ORDER = (
    "FR_hip_joint", "FR_thigh_joint", "FR_calf_joint",
    "FL_hip_joint", "FL_thigh_joint", "FL_calf_joint",
    "RR_hip_joint", "RR_thigh_joint", "RR_calf_joint",
    "RL_hip_joint", "RL_thigh_joint", "RL_calf_joint",
)
EXTREME_PARKOUR_FOOT_ORDER = ("FR_foot", "FL_foot", "RR_foot", "RL_foot")

_TRAINING_DEFAULT_POS = torch.tensor(
    [
        -0.1, 0.8, -1.5,
        0.1, 0.8, -1.5,
        -0.1, 1.0, -1.5,
        0.1, 1.0, -1.5,
    ],
    dtype=torch.float32,
)
_ACTION_SCALE = 0.25
_ACTION_CLIP = 4.8
_STATE_KEY = "extreme_parkour_state"


def _normalize_command(command: torch.Tensor, like: torch.Tensor) -> torch.Tensor:
    command = torch.as_tensor(command, dtype=like.dtype, device=like.device)
    if command.dim() == 1:
        command = command.unsqueeze(0)
    if command.shape[-1] == 1:
        command = torch.cat((command, torch.zeros_like(command).expand(-1, 2)), dim=-1)
    elif command.shape[-1] == 2:
        command = torch.stack((command[:, 0], torch.zeros_like(command[:, 0]), command[:, 1]), dim=-1)
    elif command.shape[-1] > 3:
        command = command[:, :3]
    if command.shape[-1] != 3:
        raise ValueError(f"Go2 Extreme Parkour command must have 1, 2, or 3 values, got {command.shape[-1]}")
    if command.shape[0] == 1 and like.shape[0] > 1:
        command = command.expand(like.shape[0], -1)
    if command.shape[0] != like.shape[0]:
        raise ValueError(f"command batch {command.shape[0]} != robot batch {like.shape[0]}")
    return command


def _new_state(env: Any, like: torch.Tensor) -> dict[str, Any]:
    num_envs = like.shape[0]
    return {
        "history": torch.zeros(
            num_envs,
            EXTREME_PARKOUR_HISTORY_LENGTH,
            EXTREME_PARKOUR_PROPRIO_DIM,
            dtype=like.dtype,
            device=like.device,
        ),
        "history_ready": torch.zeros(num_envs, dtype=torch.bool, device=like.device),
        "episode_steps": torch.zeros(num_envs, dtype=torch.long, device=like.device),
        "cached_height_scan": torch.zeros(
            num_envs, EXTREME_PARKOUR_SCAN_DIM, dtype=like.dtype, device=like.device
        ),
        "height_ready": torch.zeros(num_envs, dtype=torch.bool, device=like.device),
        "cached_waypoint_yaw": torch.zeros(num_envs, 2, dtype=like.dtype, device=like.device),
        "yaw_ready": torch.zeros(num_envs, dtype=torch.bool, device=like.device),
        "last_observation_step": None,
        "cached_observation": None,
        "joint_order": None,
        "foot_ids": None,
    }


def _state_for(env: Any, robot_name: str, like: torch.Tensor) -> dict[str, Any]:
    controller_dict = getattr(env, "_controllers_dict", {}).get(robot_name)
    if controller_dict is None:
        fallback = getattr(env, "_go2_extreme_parkour_states", None)
        if fallback is None:
            fallback = {}
            env._go2_extreme_parkour_states = fallback
        controller_dict = fallback.setdefault(robot_name, {})
    state = controller_dict.get(_STATE_KEY)
    if (
        state is None
        or state["history"].shape[0] != like.shape[0]
        or state["history"].device != like.device
        or state["history"].dtype != like.dtype
    ):
        state = _new_state(env, like)
        controller_dict[_STATE_KEY] = state
    return state


def _joint_order(robot: Any, state: dict[str, Any]) -> list[int]:
    if state["joint_order"] is None:
        joint_names = list(robot.joint_names)
        if len(joint_names) != 12 or set(joint_names) != set(EXTREME_PARKOUR_JOINT_ORDER):
            raise ValueError(
                f"Go2 joint set mismatch: expected {list(EXTREME_PARKOUR_JOINT_ORDER)}, got {joint_names}"
            )
        state["joint_order"] = [joint_names.index(name) for name in EXTREME_PARKOUR_JOINT_ORDER]
    return state["joint_order"]


def _foot_ids(contact_sensor: Any, state: dict[str, Any]) -> list[int]:
    if state["foot_ids"] is None:
        ids, names = contact_sensor.find_bodies(EXTREME_PARKOUR_FOOT_ORDER, preserve_order=True)
        if tuple(names) != EXTREME_PARKOUR_FOOT_ORDER:
            raise ValueError(
                f"Go2 foot order mismatch: expected {list(EXTREME_PARKOUR_FOOT_ORDER)}, got {names}"
            )
        state["foot_ids"] = list(ids)
    return state["foot_ids"]


def _command(env: Any, robot_name: str, like: torch.Tensor) -> torch.Tensor:
    command = env.get_command(robot_name, "base_velocity") if hasattr(env, "get_command") else None
    if command is None:
        return torch.zeros(like.shape[0], 3, dtype=like.dtype, device=like.device)
    return _normalize_command(command, like)


def _height_scan(env: Any, robot_name: str, robot: Any, state: dict[str, Any]) -> torch.Tensor:
    step = int(getattr(env, "common_step_counter", 0))
    refresh = not bool(state["height_ready"].all()) or step % EXTREME_PARKOUR_UPDATE_INTERVAL == 0
    if refresh:
        sensor_name = f"{robot_name}_height_scanner"
        sensors = getattr(env.scene, "sensors", {})
        if sensor_name not in sensors:
            raise KeyError(f"Extreme Parkour height scanner '{sensor_name}' is missing")
        hits = sensors[sensor_name].data.ray_hits_w
        if hits.shape[-2] != EXTREME_PARKOUR_SCAN_DIM:
            raise ValueError(f"height scan has {hits.shape[-2]} rays, expected {EXTREME_PARKOUR_SCAN_DIM}")
        hit_z = hits[..., 2]
        # Missing scene geometry must not silently become flat ground.
        if not torch.isfinite(hit_z).all():
            raise ValueError('Go2 height scan missed collision geometry; supply 132 finite hits')
        scan = robot.data.root_pos_w[:, 2:3] - 0.3 - hit_z
        state["cached_height_scan"].copy_(scan.clamp(-1.0, 1.0))
        state["height_ready"].fill_(True)
    return state["cached_height_scan"]


def _waypoint_yaw(env: Any, robot_name: str, like: torch.Tensor, state: dict[str, Any]) -> torch.Tensor:
    step = int(getattr(env, "common_step_counter", 0))
    # Training resets yaw to zero and refreshes only on the global five-step tick.
    refresh = step % EXTREME_PARKOUR_UPDATE_INTERVAL == 0
    if refresh:
        # The Teacher's 6:8 fields are the current/next errors from the
        # separate ``parkour_waypoint`` command.  A route follower can publish
        # the two errors through EAI's command store; a straight route with no
        # route follower defaults to zero.  Never substitute angular velocity,
        # which is a different training field.
        waypoint = (
            env.get_command(robot_name, "parkour_waypoint_yaw")
            if hasattr(env, "get_command")
            else None
        )
        if waypoint is None:
            state["cached_waypoint_yaw"].zero_()
        else:
            waypoint = torch.as_tensor(waypoint, dtype=like.dtype, device=like.device)
            if waypoint.dim() == 1:
                waypoint = waypoint.unsqueeze(0)
            if waypoint.shape == (1, 2) and like.shape[0] > 1:
                waypoint = waypoint.expand(like.shape[0], -1)
            if waypoint.shape != (like.shape[0], 2):
                raise ValueError(
                    f"parkour waypoint yaw shape {tuple(waypoint.shape)} != {(like.shape[0], 2)}"
                )
            state["cached_waypoint_yaw"].copy_(waypoint)
        state["yaw_ready"].fill_(True)
    return state["cached_waypoint_yaw"]


def _feet_contact(env: Any, robot_name: str, like: torch.Tensor, state: dict[str, Any]) -> torch.Tensor:
    sensor_name = f"{robot_name}_contact_forces"
    sensors = getattr(env.scene, "sensors", {})
    if sensor_name not in sensors:
        raise KeyError(f"Extreme Parkour contact sensor '{sensor_name}' is missing")
    sensor = sensors[sensor_name]
    force_history = sensor.data.net_forces_w_history[:, :, _foot_ids(sensor, state)]
    contact = torch.any(torch.linalg.vector_norm(force_history, dim=-1) > 2.0, dim=1)
    return contact.to(dtype=like.dtype) - 0.5


def _current_proprio(env: Any, robot_name: str, robot: Any, state: dict[str, Any]) -> torch.Tensor:
    order = _joint_order(robot, state)
    root_ang_vel = robot.data.root_ang_vel_b * 0.25
    roll, pitch, _ = euler_xyz_from_quat(robot.data.root_quat_w)
    imu = torch.stack((wrap_to_pi(roll), wrap_to_pi(pitch)), dim=-1)
    command = _command(env, robot_name, root_ang_vel)
    zeros_one = torch.zeros_like(command[:, :1])
    zeros_two = torch.zeros_like(command[:, :2])
    terrain_class = torch.tensor(
        (1.0, 0.0), dtype=root_ang_vel.dtype, device=root_ang_vel.device
    ).expand(root_ang_vel.shape[0], -1)
    terrain_override = env.get_command(robot_name, 'parkour_terrain_class') if hasattr(env,'get_command') else None
    if terrain_override is not None:
        terrain_class = torch.as_tensor(terrain_override,dtype=root_ang_vel.dtype,device=root_ang_vel.device)
        if terrain_class.shape != (root_ang_vel.shape[0],2):
            raise ValueError('parkour_terrain_class must be [N,2]: obstacle,flat')
    joint_pos = robot.data.joint_pos[:, order]
    joint_vel = robot.data.joint_vel[:, order]
    defaults = _TRAINING_DEFAULT_POS.to(device=joint_pos.device, dtype=joint_pos.dtype).expand_as(joint_pos)
    last_action = getattr(env, "actions_dict", {}).get(robot_name)
    if last_action is None:
        last_action = torch.zeros_like(joint_pos)
    elif last_action.dim() == 1:
        last_action = last_action.unsqueeze(0)
    if last_action.shape != joint_pos.shape:
        raise ValueError(f"last action shape {tuple(last_action.shape)} != {tuple(joint_pos.shape)}")
    proprio = torch.cat(
        (
            root_ang_vel,
            imu,
            zeros_one,
            _waypoint_yaw(env, robot_name, root_ang_vel, state),
            zeros_two,
            command[:, :1],
            terrain_class,
            joint_pos - defaults,
            joint_vel * 0.05,
            last_action,
            _feet_contact(env, robot_name, root_ang_vel, state),
        ),
        dim=-1,
    )
    if proprio.shape[-1] != EXTREME_PARKOUR_PROPRIO_DIM:
        raise RuntimeError(f"Extreme Parkour proprio dim {proprio.shape[-1]} != 53")
    return proprio


def compute_go2_extreme_parkour_observations(env: Any, robot_name: str) -> torch.Tensor:
    """Build the Teacher's 53+132+9+29+10x53 observation vector."""
    robot = env.scene.articulations[robot_name]
    state = _state_for(env, robot_name, robot.data.root_ang_vel_b)
    step = int(getattr(env, "common_step_counter", 0))
    if state["last_observation_step"] == step and state["cached_observation"] is not None:
        return state["cached_observation"]

    current = _current_proprio(env, robot_name, robot, state)
    explicit = torch.cat(
        (
            robot.data.root_lin_vel_b * 2.0,
            torch.zeros(
                robot.data.root_lin_vel_b.shape[0],
                6,
                dtype=robot.data.root_lin_vel_b.dtype,
                device=robot.data.root_lin_vel_b.device,
            ),
        ),
        dim=-1,
    )
    privileged = torch.zeros(
        current.shape[0], EXTREME_PARKOUR_PRIVILEGED_DIM, dtype=current.dtype, device=current.device
    )
    physical = env.get_command(robot_name,'parkour_privileged') if hasattr(env,'get_command') else None
    if physical is not None:
        privileged = torch.as_tensor(physical,dtype=current.dtype,device=current.device)
        if privileged.shape != (current.shape[0],29):
            raise ValueError('parkour_privileged must be the training-encoded [N,29] vector')
    observation = torch.cat(
        (
            current,
            _height_scan(env, robot_name, robot, state),
            explicit,
            privileged,
            state["history"].reshape(current.shape[0], -1),
        ),
        dim=-1,
    )
    if observation.shape[-1] != EXTREME_PARKOUR_OBSERVATION_DIM or not torch.isfinite(observation).all():
        raise ValueError(
            f"invalid Go2 Extreme Parkour observation: shape={tuple(observation.shape)}, "
            f"finite={bool(torch.isfinite(observation).all())}"
        )

    history_frame = current.clone()
    history_frame[:, 6:8] = 0.0
    ready = state["episode_steps"] > 1
    if torch.any(~ready):
        state["history"][~ready] = history_frame[~ready, None, :].expand(
            -1, EXTREME_PARKOUR_HISTORY_LENGTH, -1
        )
    if torch.any(ready):
        state["history"][ready, :-1] = state["history"][ready, 1:].clone()
        state["history"][ready, -1] = history_frame[ready]
    state['history_ready'].fill_(True)
    state['episode_steps'] += 1
    state["last_observation_step"] = step
    state["cached_observation"] = observation
    return observation


def apply_go2_extreme_parkour_action(
    env: Any,
    robot_name: str,
    action: torch.Tensor,
    controller_dict: Dict[str, Any],
) -> None:
    """Apply clipped Teacher output as default + 0.25 * action targets."""
    del controller_dict
    robot = env.scene.articulations[robot_name]
    if action.dim() == 1:
        action = action.unsqueeze(0)
    if action.shape[-1] != 12 or not torch.isfinite(action).all():
        raise ValueError(f"invalid Go2 Extreme Parkour action shape/value: {tuple(action.shape)}")
    action = action.float().clamp(-_ACTION_CLIP, _ACTION_CLIP)
    default = _TRAINING_DEFAULT_POS.to(device=action.device, dtype=action.dtype).expand_as(action)
    targets_training_order = default + _ACTION_SCALE * action
    state = _state_for(env, robot_name, robot.data.root_ang_vel_b)
    order = _joint_order(robot, state)
    inverse = [0] * len(order)
    for training_index, articulation_index in enumerate(order):
        inverse[articulation_index] = training_index
    robot.set_joint_position_target(targets_training_order[:, inverse], joint_ids=slice(None))


def compute_rsl_action_from_command(
    controller_cfg: Any,
    env: Any,
    robot_name: str,
    command: torch.Tensor,
    controller_dict: Dict[str, Any],
) -> torch.Tensor:
    robot = env.scene.articulations[robot_name]
    command = _normalize_command(command, robot.data.root_ang_vel_b)
    if hasattr(env, "set_command"):
        env.set_command(robot_name, controller_cfg.command_name, command)
    observation = controller_cfg.observation_func(env, robot_name)
    action = controller_cfg.compute_action(env, robot_name, observation, controller_dict)
    if action.dim() == 1:
        action = action.unsqueeze(0)
    action = action.float().clamp(-_ACTION_CLIP, _ACTION_CLIP)
    if action.shape[-1] != 12 or not torch.isfinite(action).all():
        raise ValueError(f"invalid Teacher output for {robot_name}: shape={tuple(action.shape)}")
    return action


def reset_controller(env, robot_name, env_ids=None):
    """Reset selected environments; other robot instances/histories are preserved."""
    robot=env.scene.articulations[robot_name]
    state=_state_for(env,robot_name,robot.data.root_ang_vel_b)
    ids=slice(None) if env_ids is None else env_ids
    for key in ('history','cached_height_scan','cached_waypoint_yaw'):
        state[key][ids]=0.
    state['episode_steps'][ids]=0
    for key in ('history_ready','height_ready','yaw_ready'):
        state[key][ids]=False
    state['last_observation_step']=None
    state['cached_observation']=None
