"""Pepper low-level base and bilateral-arm controller for local PickPlace.

The host creates ``PepperPickPlaceAdapter`` and assigns it to the
``local_pick_place`` slot after loading this controller. This module owns
articulation configuration, holonomic base commands, and the per-physics tick
callback; it has no source/destination route or automatic transport.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import torch
from isaaclab.utils import configclass

from EAI.controllers import ControllerCfg
from EAI_assets.g1_box_grasp_skill import G1BoxGraspConfig
from EAI_assets.controller.traditional.pepper_box_grasp.controller import PepperBoxGraspRuntime
from EAI_assets.controller.traditional.pepper_holonomic.pepper_holonomic import (
    PEPPER_HOLONOMIC_CFG,
    apply_pepper_holonomic_action,
    compute_pepper_holonomic_action_from_command,
)


def compute_pepper_pick_place_action(
    controller_cfg: Any,
    env: Any,
    robot_name: str,
    command: torch.Tensor,
    controller_dict: Dict[str, Any],
) -> torch.Tensor:
    """Pass caller-owned base motion through the normal holonomic controller."""
    del controller_cfg
    controller_dict["locomotion_compute_calls"] = int(
        controller_dict.get("locomotion_compute_calls", 0)
    ) + 1
    return compute_pepper_holonomic_action_from_command(
        PEPPER_HOLONOMIC_CFG, env, robot_name, command, controller_dict,
    )


def apply_pepper_pick_place_action(
    env: Any,
    robot_name: str,
    action: torch.Tensor,
    controller_dict: Dict[str, Any],
) -> None:
    """Apply base action once and advance a registered local adapter once."""
    apply_pepper_holonomic_action(env, robot_name, action, controller_dict)
    controller_dict["locomotion_apply_calls"] = int(
        controller_dict.get("locomotion_apply_calls", 0)
    ) + 1
    local = controller_dict.get("local_pick_place")
    if local is not None:
        sim_cfg = getattr(getattr(env, "cfg", None), "sim", None)
        local.physics_step(float(getattr(sim_cfg, "dt", 0.005)))


@configclass
class PepperPickPlaceControllerCfg(ControllerCfg):
    """Host-bound Pepper controller used by the local PickPlace adapter."""

    command_dim: int = 3
    skill_config: G1BoxGraspConfig = G1BoxGraspConfig(
        grasp_distance_threshold_m=0.12,
        grasp_timeout_s=12.0,
        min_side_offset_m=0.18,
    )

    def load(self, robot_name: str, task_name: str, device: str, env: Any) -> Dict[str, Any]:
        controller_dict = PEPPER_HOLONOMIC_CFG.load(robot_name, task_name, device, env)
        runtime = PepperBoxGraspRuntime(G1BoxGraspConfig.from_environment(self.skill_config))
        runtime.hold_box_before_grasp = False
        runtime.write_release_pose = False
        controller_dict.update({
            "runtime": runtime,
            "local_pick_place": None,
            "collision_filter_installed": True,
            "locomotion_controller": "PEPPER_HOLONOMIC_CFG",
            "locomotion_compute_calls": 0,
            "locomotion_apply_calls": 0,
        })
        return controller_dict

    def compute_action(
        self, env: Any, robot_name: str, observations: Optional[torch.Tensor], controller_dict: Dict[str, Any]
    ) -> torch.Tensor:
        del observations
        return PEPPER_HOLONOMIC_CFG.compute_action(env, robot_name, None, controller_dict)

    def compute_action_from_command(
        self, env: Any, robot_name: str, command: torch.Tensor, controller_dict: Dict[str, Any]
    ) -> torch.Tensor:
        return compute_pepper_pick_place_action(self, env, robot_name, command, controller_dict)

    def apply_action(
        self, env: Any, robot_name: str, action: torch.Tensor, controller_dict: Dict[str, Any]
    ) -> None:
        apply_pepper_pick_place_action(env, robot_name, action, controller_dict)

    def reset(self, env: Any, robot_name: str, controller_dict: Dict[str, Any], env_ids: Any) -> None:
        del env, robot_name, env_ids
        local = controller_dict.get("local_pick_place")
        if local is not None:
            local.reset()
        controller_dict["runtime"].reset()
        controller_dict["local_pick_place"] = None
        controller_dict["locomotion_compute_calls"] = 0
        controller_dict["locomotion_apply_calls"] = 0


PEPPER_PICK_PLACE_CFG = PepperPickPlaceControllerCfg(
    robot_type="Pepper",
    compute_action_from_command_func=compute_pepper_pick_place_action,
    apply_action_func=apply_pepper_pick_place_action,
)


__all__ = [
    "PEPPER_PICK_PLACE_CFG",
    "PepperPickPlaceControllerCfg",
    "apply_pepper_pick_place_action",
    "compute_pepper_pick_place_action",
]
