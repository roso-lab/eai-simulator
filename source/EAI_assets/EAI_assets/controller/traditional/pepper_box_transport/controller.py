"""Composite Pepper controller: holonomic base plus two-arm box transport."""

from __future__ import annotations

from typing import Any, Dict, Optional

import torch
from isaaclab.utils import configclass

from EAI.controllers import ControllerCfg
from EAI_assets.box_transport_skill import (
    BoxTransportConfig,
    BoxTransportState,
    install_robot_box_collision_filter,
    transport_grasp_config,
)
from EAI_assets.controller.traditional.pepper_box_grasp.controller import PepperBoxGraspRuntime
from EAI_assets.controller.traditional.pepper_holonomic.pepper_holonomic import (
    PEPPER_HOLONOMIC_CFG,
    _get_pepper_base_ids,
    apply_pepper_holonomic_action,
    compute_pepper_holonomic_action_from_command,
)
from EAI_assets.g1_box_grasp_skill import G1BoxGraspConfig, G1BoxGraspPhase


def _pepper_planar_pose(env: Any, robot_name: str) -> tuple[float, float, float]:
    robot = env.scene.articulations[robot_name]
    ids = _get_pepper_base_ids(robot)
    values = robot.data.joint_pos[0, ids].detach().cpu().tolist()
    return tuple(float(item) for item in values[:3])


def compute_pepper_box_transport_action(
    controller_cfg: Any,
    env: Any,
    robot_name: str,
    command: torch.Tensor,
    controller_dict: Dict[str, Any],
) -> torch.Tensor:
    runtime: PepperBoxGraspRuntime = controller_dict["runtime"]
    transport: BoxTransportState = controller_dict["transport"]
    if not controller_dict.get("execution_enabled", True):
        controller_dict["internal_velocity_command"] = (0.0, 0.0, 0.0)
        controller_dict["locomotion_compute_calls"] = int(controller_dict.get("locomotion_compute_calls", 0)) + 1
        return compute_pepper_holonomic_action_from_command(
            PEPPER_HOLONOMIC_CFG, env, robot_name, torch.zeros_like(command), controller_dict,
        )
    generated = transport.update(
        runtime.state.phase,
        runtime.state.attached,
        _pepper_planar_pose(env, robot_name),
        float(getattr(env, "step_dt", 0.02)),
    )
    runtime.set_composite_motion(
        freeze_carry=transport.freeze_carry,
        world_offset=transport.grasp_world_offset(),
    )
    runtime.hold_arm_joints = (
        transport.freeze_carry
        or (transport.phase.value == "place" and runtime.state.phase is G1BoxGraspPhase.CARRY)
    )
    controller_dict["internal_velocity_command"] = generated
    controller_dict["locomotion_compute_calls"] = int(
        controller_dict.get("locomotion_compute_calls", 0)
    ) + 1
    internal = torch.tensor([generated], device=env.device, dtype=torch.float32).expand(env.num_envs, -1)
    return compute_pepper_holonomic_action_from_command(
        PEPPER_HOLONOMIC_CFG,
        env,
        robot_name,
        internal,
        controller_dict,
    )


def apply_pepper_box_transport_action(
    env: Any,
    robot_name: str,
    action: torch.Tensor,
    controller_dict: Dict[str, Any],
) -> None:
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
    apply_pepper_holonomic_action(env, robot_name, action, controller_dict)
    controller_dict["locomotion_apply_calls"] = int(
        controller_dict.get("locomotion_apply_calls", 0)
    ) + 1
    runtime: PepperBoxGraspRuntime = controller_dict["runtime"]
    if not controller_dict.get("execution_enabled", True):
        env.pepper_box_grasp_state = runtime.state.snapshot()
        env.box_transport_state = controller_dict["transport"].snapshot()
        return
    sim_cfg = getattr(getattr(env, "cfg", None), "sim", None)
    physics_dt = float(getattr(sim_cfg, "dt", 0.005))
    if runtime.state.phase is G1BoxGraspPhase.IDLE and controller_dict.get("auto_start", True):
        controller_dict["source_settle_elapsed_s"] += physics_dt
        if controller_dict["source_settle_elapsed_s"] >= controller_dict["transport"].config.source_settle_s:
            runtime.start()
    runtime.step(env, robot_name, physics_dt)
    # Keep the public Pepper transport evidence namespace robot-neutral.  The
    # packaged Pepper evaluator consumes this key; it deliberately does not
    # expose the historical G1 state name.
    grasp_snapshot = runtime.state.snapshot()
    env.pepper_box_grasp_state = grasp_snapshot
    env.box_transport_state = controller_dict["transport"].snapshot()
    if runtime.last_error:
        env.g1_box_grasp_error = runtime.last_error


@configclass
class PepperBoxTransportControllerCfg(ControllerCfg):
    # External base velocity command width differs from articulation DOF.
    command_dim: int = 3
    skill_config: G1BoxGraspConfig = G1BoxGraspConfig(
        place_position=(0.20, 0.0, 0.62),
        min_side_offset_m=0.18,
        grasp_distance_threshold_m=0.12,
        grasp_timeout_s=12.0,
        carry_duration_s=0.8,
    )
    transport_config: BoxTransportConfig = BoxTransportConfig()
    auto_start: bool = True
    # Legacy demonstrations retain auto execution. Task-owned runtimes set this
    # false so unrelated shared simulator steps cannot start a transport skill.
    execution_enabled: bool = True

    def load(self, robot_name: str, task_name: str, device: str, env: Any) -> Dict[str, Any]:
        controller_dict = PEPPER_HOLONOMIC_CFG.load(robot_name, task_name, device, env)
        runtime = PepperBoxGraspRuntime(
            transport_grasp_config(self.skill_config, self.transport_config)
        )
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
                "execution_enabled": bool(self.execution_enabled),
                "locomotion_controller": "PEPPER_HOLONOMIC_CFG",
                "source_settle_elapsed_s": 0.0,
                # FilteredPairsAPI is authored by the transport robot spawner
                # before Isaac Lab creates articulation tensor views.
                "collision_filter_installed": True,
                "locomotion_compute_calls": 0,
                "locomotion_apply_calls": 0,
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
        return PEPPER_HOLONOMIC_CFG.compute_action(env, robot_name, observations, controller_dict)

    def compute_action_from_command(
        self,
        env: Any,
        robot_name: str,
        command: torch.Tensor,
        controller_dict: Dict[str, Any],
    ) -> torch.Tensor:
        return compute_pepper_box_transport_action(self, env, robot_name, command, controller_dict)

    def apply_action(
        self,
        env: Any,
        robot_name: str,
        action: torch.Tensor,
        controller_dict: Dict[str, Any],
    ) -> None:
        apply_pepper_box_transport_action(env, robot_name, action, controller_dict)

    def reset(self, env: Any, robot_name: str, controller_dict: Dict[str, Any], env_ids: Any) -> None:
        del env, robot_name, env_ids
        controller_dict["runtime"].reset()
        controller_dict["transport"].reset()
        controller_dict.pop("internal_velocity_command", None)
        controller_dict.pop("pepper_position_target", None)
        controller_dict["source_settle_elapsed_s"] = 0.0
        controller_dict["collision_filter_installed"] = True
        controller_dict["locomotion_compute_calls"] = 0
        controller_dict["locomotion_apply_calls"] = 0
        controller_dict["execution_enabled"] = bool(self.execution_enabled)


PEPPER_BOX_TRANSPORT_CFG = PepperBoxTransportControllerCfg(
    robot_type="Pepper",
    compute_action_from_command_func=compute_pepper_box_transport_action,
    apply_action_func=apply_pepper_box_transport_action,
)

# Same controller contract with explicit task ownership. The caller supplies
# the box/support scene binding; ordinary shared steps cannot start this cfg.
PEPPER_BOX_TRANSPORT_TASK_CFG = PEPPER_BOX_TRANSPORT_CFG.copy()
PEPPER_BOX_TRANSPORT_TASK_CFG.execution_enabled = False


__all__ = [
    "PEPPER_BOX_TRANSPORT_CFG",
    "PEPPER_BOX_TRANSPORT_TASK_CFG",
    "PepperBoxTransportControllerCfg",
    "apply_pepper_box_transport_action",
    "compute_pepper_box_transport_action",
]
