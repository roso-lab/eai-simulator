"""Composite G1 controller: learned locomotion plus two-arm box transport."""

from __future__ import annotations

import math
from typing import Any, Dict, Optional

import torch
from isaaclab.utils import configclass

from EAI.controllers import ControllerCfg
from EAI_assets.box_transport_skill import (
    BoxTransportConfig,
    BoxTransportPhase,
    BoxTransportState,
    install_robot_box_collision_filter,
    transport_grasp_config,
    world_to_body_planar_command,
)
from EAI_assets.controller.rl.g1_skrl.g1_skrl import (
    G1_SKRL_CFG,
    apply_g1_action,
    compute_g1_observations,
    compute_skrl_action_from_command,
)
from EAI_assets.controller.traditional.g1_box_grasp.controller import G1BoxGraspRuntime
from EAI_assets.g1_box_grasp_skill import G1BoxGraspConfig, G1BoxGraspPhase


def _yaw_from_quaternion_wxyz(quaternion: torch.Tensor) -> float:
    w, x, y, z = (float(value) for value in quaternion[:4].detach().cpu().tolist())
    return math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))


def _g1_planar_pose(env: Any, robot_name: str) -> tuple[float, float, float]:
    robot = env.scene.articulations[robot_name]
    position = robot.data.root_pos_w[0]
    return (
        float(position[0].item()),
        float(position[1].item()),
        _yaw_from_quaternion_wxyz(robot.data.root_quat_w[0]),
    )


def _update_composite_state(env: Any, robot_name: str, controller_dict: Dict[str, Any]) -> torch.Tensor:
    runtime: G1BoxGraspRuntime = controller_dict["runtime"]
    transport: BoxTransportState = controller_dict["transport"]
    planar_pose = _g1_planar_pose(env, robot_name)
    position_command = transport.update(
        runtime.state.phase,
        runtime.state.attached,
        planar_pose,
        float(getattr(env, "step_dt", 0.02)),
    )
    robot = env.scene.articulations[robot_name]
    measured_velocity = torch.cat(
        (robot.data.root_lin_vel_w[0, :2], robot.data.root_ang_vel_w[0, 2:3])
    ).detach().cpu()
    previous_velocity = controller_dict.get("filtered_world_velocity")
    alpha = float(controller_dict.get("velocity_filter_alpha", 0.2))
    if previous_velocity is None:
        filtered_velocity = measured_velocity
    else:
        filtered_velocity = (1.0 - alpha) * previous_velocity + alpha * measured_velocity
    controller_dict["filtered_world_velocity"] = filtered_velocity
    world_command = position_command
    if transport.phase in {BoxTransportPhase.WAIT_FOR_GRASP, BoxTransportPhase.TRANSPORT}:
        linear_gain = float(controller_dict.get("linear_velocity_damping", 0.35))
        angular_gain = float(controller_dict.get("angular_velocity_damping", 0.08))
        vx = float(position_command[0]) - linear_gain * float(filtered_velocity[0])
        vy = float(position_command[1]) - linear_gain * float(filtered_velocity[1])
        speed = math.hypot(vx, vy)
        # Cruise remains slow, but braking must be stronger than cruise to
        # counter the policy's learned lateral inertia.
        speed_limit = float(transport.config.station_keep_speed_m_s)
        if speed > speed_limit > 0.0:
            scale = speed_limit / speed
            vx *= scale
            vy *= scale
        # The bundled policy introduces its own yaw stabilization.  A second
        # closed-loop yaw command fights that stabilizer while the carry arms
        # are held, so G1 transport deliberately keeps the requested yaw at 0.
        wz = 0.0
        world_command = (vx, vy, max(-0.5, min(0.5, wz)))
    command = world_to_body_planar_command(world_command, planar_pose[2])
    runtime.set_composite_motion(
        freeze_carry=transport.freeze_carry,
        world_offset=transport.grasp_world_offset(),
    )
    runtime.hold_arm_joints = (
        transport.freeze_carry
        or (transport.phase.value == "place" and runtime.state.phase is G1BoxGraspPhase.CARRY)
    )
    controller_dict["world_velocity_command"] = world_command
    controller_dict["internal_velocity_command"] = command
    controller_dict["locomotion_compute_calls"] = int(
        controller_dict.get("locomotion_compute_calls", 0)
    ) + 1
    return torch.tensor([command], device=env.device, dtype=torch.float32).expand(env.num_envs, -1)


def compute_g1_box_transport_action(
    controller_cfg: Any,
    env: Any,
    robot_name: str,
    command: torch.Tensor,
    controller_dict: Dict[str, Any],
) -> torch.Tensor:
    # The external tensor only triggers the standard EAI command/action path.
    # Route generation belongs to this bottom-level composite skill.
    del command
    internal_command = _update_composite_state(env, robot_name, controller_dict)
    return compute_skrl_action_from_command(
        G1_SKRL_CFG,
        env,
        robot_name,
        internal_command,
        controller_dict,
    )


def apply_g1_box_transport_action(
    env: Any,
    robot_name: str,
    action: torch.Tensor,
    controller_dict: Dict[str, Any],
) -> None:
    # Preserve the real G1 SKRL action path.  Arm IK is applied afterwards so
    # the policy continues controlling balance/legs while the grasp skill owns
    # only the resolved arm joints.
    if not controller_dict.get("collision_filter_installed", False):
        install_robot_box_collision_filter(
            env,
            robot_name,
            controller_dict["runtime"].state.config.cube_prim_name,
            (
                controller_dict["transport"].config.source_table_name,
                controller_dict["transport"].config.destination_table_name,
            ),
        )
        controller_dict["collision_filter_installed"] = True
    apply_g1_action(env, robot_name, action, controller_dict)
    runtime: G1BoxGraspRuntime = controller_dict["runtime"]
    controller_dict["locomotion_apply_calls"] = int(
        controller_dict.get("locomotion_apply_calls", 0)
    ) + 1
    sim_cfg = getattr(getattr(env, "cfg", None), "sim", None)
    physics_dt = float(getattr(sim_cfg, "dt", 0.005))
    if runtime.state.phase is G1BoxGraspPhase.IDLE and controller_dict.get("auto_start", True):
        controller_dict["source_settle_elapsed_s"] += physics_dt
        if controller_dict["source_settle_elapsed_s"] >= controller_dict["transport"].config.source_settle_s:
            runtime.start()
    runtime.step(env, robot_name, physics_dt)
    env.g1_box_grasp_state = runtime.state.snapshot()
    env.box_transport_state = controller_dict["transport"].snapshot()
    if runtime.last_error:
        env.g1_box_grasp_error = runtime.last_error


@configclass
class G1BoxTransportControllerCfg(ControllerCfg):
    """Autonomous table transport while retaining G1's learned locomotion."""

    skill_config: G1BoxGraspConfig = G1BoxGraspConfig(
        place_position=(0.20, 0.0, 0.62),
        carry_duration_s=0.8,
        grasp_timeout_s=30.0,
    )
    # The bundled G1 policy amplifies lateral commands substantially while its
    # arms are held around the payload.  Use a robot-specific low-speed
    # envelope and start braking well before the target; Pepper retains the
    # generic transport tuning.
    transport_config: BoxTransportConfig = BoxTransportConfig(
        destination_offset=(0.0, 0.75, 0.0),
        cruise_speed_m_s=0.06,
        position_gain=0.25,
        lateral_correction_gain=0.25,
        yaw_correction_gain=0.0,
        arrival_tolerance_m=0.06,
        arrival_yaw_tolerance_rad=3.141592653589793,
        arrival_hold_s=0.8,
    )
    linear_velocity_damping: float = 0.6
    angular_velocity_damping: float = 0.0
    velocity_filter_alpha: float = 0.2
    auto_start: bool = True

    def load(self, robot_name: str, task_name: str, device: str, env: Any) -> Dict[str, Any]:
        controller_dict = G1_SKRL_CFG.load(robot_name, task_name, device, env)
        runtime = G1BoxGraspRuntime(
            transport_grasp_config(self.skill_config, self.transport_config)
        )
        # Idealized pre-grasp staging prevents early palm contacts from
        # launching the dynamic carton before the bilateral reach gate.  The
        # object remains an 8 kg dynamic rigid body and is fully physical once
        # released on the destination table.
        # Let the 8 kg dynamic carton settle on the physical source table.
        # Robot/carton contacts are filtered until the bilateral reach gate
        # enables the ideal attachment.
        runtime.hold_box_before_grasp = False
        runtime.write_release_pose = False
        controller_dict.update(
            {
                "runtime": runtime,
                "transport": BoxTransportState(self.transport_config),
                "auto_start": bool(self.auto_start),
                "locomotion_controller": "G1_SKRL_CFG",
                "source_settle_elapsed_s": 0.0,
                # FilteredPairsAPI is authored by the transport robot spawner
                # before Isaac Lab creates articulation tensor views.
                "collision_filter_installed": True,
                "locomotion_compute_calls": 0,
                "locomotion_apply_calls": 0,
                "linear_velocity_damping": float(self.linear_velocity_damping),
                "angular_velocity_damping": float(self.angular_velocity_damping),
                "velocity_filter_alpha": float(self.velocity_filter_alpha),
            }
        )
        return controller_dict

    def compute_action(
        self,
        env: Any,
        robot_name: str,
        observations: Optional[torch.Tensor],
        controller_dict: Dict[str, Any],
    ) -> torch.Tensor:
        return G1_SKRL_CFG.compute_action(env, robot_name, observations, controller_dict)

    def compute_action_from_command(
        self,
        env: Any,
        robot_name: str,
        command: torch.Tensor,
        controller_dict: Dict[str, Any],
    ) -> torch.Tensor:
        return compute_g1_box_transport_action(self, env, robot_name, command, controller_dict)

    def apply_action(
        self,
        env: Any,
        robot_name: str,
        action: torch.Tensor,
        controller_dict: Dict[str, Any],
    ) -> None:
        apply_g1_box_transport_action(env, robot_name, action, controller_dict)

    def reset(self, env: Any, robot_name: str, controller_dict: Dict[str, Any], env_ids: Any) -> None:
        del env, robot_name, env_ids
        controller_dict["runtime"].reset()
        controller_dict["transport"].reset()
        controller_dict.pop("internal_velocity_command", None)
        controller_dict.pop("world_velocity_command", None)
        controller_dict.pop("filtered_world_velocity", None)
        controller_dict["source_settle_elapsed_s"] = 0.0
        controller_dict["collision_filter_installed"] = True
        controller_dict["locomotion_compute_calls"] = 0
        controller_dict["locomotion_apply_calls"] = 0


def reset_g1_box_transport(env: Any, robot_name: str) -> None:
    controller_dict = env._controllers_dict[robot_name]
    controller_dict["runtime"].reset()
    controller_dict["transport"].reset()
    controller_dict.pop("filtered_world_velocity", None)
    controller_dict["source_settle_elapsed_s"] = 0.0
    controller_dict["collision_filter_installed"] = True


G1_BOX_TRANSPORT_CFG = G1BoxTransportControllerCfg(
    robot_type="G1",
    observation_func=compute_g1_observations,
    compute_action_from_command_func=compute_g1_box_transport_action,
    apply_action_func=apply_g1_box_transport_action,
)


__all__ = [
    "G1_BOX_TRANSPORT_CFG",
    "G1BoxTransportControllerCfg",
    "apply_g1_box_transport_action",
    "compute_g1_box_transport_action",
    "reset_g1_box_transport",
]
