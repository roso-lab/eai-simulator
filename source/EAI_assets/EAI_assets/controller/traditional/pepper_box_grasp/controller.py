"""Isaac Lab adapter for Pepper's idealized two-arm box grasp skill."""

from __future__ import annotations

from typing import Any, Dict, Optional

import torch
from isaaclab.utils import configclass

from EAI.controllers import ControllerCfg
from EAI_assets.g1_box_grasp_skill import G1BoxGraspConfig, G1BoxGraspPhase
from EAI_assets.controller.traditional.g1_box_grasp.controller import (
    G1BoxGraspRuntime,
    apply_g1_box_grasp_action,
    compute_g1_box_grasp_action,
    _find_first,
)


class PepperBoxGraspRuntime(G1BoxGraspRuntime):
    """Reuse the box state/IK runtime with Pepper's arm naming vocabulary."""

    _SIDE_PREFIX = {"left": "L", "right": "R"}
    _ARM_JOINT_SUFFIXES = (
        "ShoulderPitch",
        "ShoulderRoll",
        "ElbowYaw",
        "ElbowRoll",
        "WristYaw",
    )
    _HAND_BODY_SUFFIXES = ("_gripper", "_wrist")

    def _resolve(self, robot: Any, side: str) -> tuple[list[int], int | None]:
        key = (id(robot), side)
        if key not in self._joint_ids:
            prefix = self._SIDE_PREFIX[side]
            ids: list[int] = []
            seen: set[int] = set()
            for suffix in self._ARM_JOINT_SUFFIXES:
                joint_id = _find_first(robot, (f"{prefix}{suffix}",))
                if joint_id is not None and joint_id not in seen:
                    ids.append(joint_id)
                    seen.add(joint_id)
            self._joint_ids[key] = ids
        if key not in self._ee_ids:
            prefix = self._SIDE_PREFIX[side].lower()
            self._ee_ids[key] = _find_first(
                robot,
                tuple(f"{prefix}{suffix}" for suffix in self._HAND_BODY_SUFFIXES),
                body=True,
            )
        return self._joint_ids[key], self._ee_ids[key]


def compute_pepper_box_grasp_action(
    controller_cfg: Any,
    env: Any,
    robot_name: str,
    command: torch.Tensor,
    controller_dict: Dict[str, Any],
) -> torch.Tensor:
    return compute_g1_box_grasp_action(controller_cfg, env, robot_name, command, controller_dict)


def apply_pepper_box_grasp_action(
    env: Any,
    robot_name: str,
    action: torch.Tensor,
    controller_dict: Dict[str, Any],
) -> None:
    apply_g1_box_grasp_action(env, robot_name, action, controller_dict)


def start_pepper_box_grasp(env: Any, robot_name: str) -> None:
    env._controllers_dict[robot_name]["runtime"].start()


def reset_pepper_box_grasp(env: Any, robot_name: str) -> None:
    env._controllers_dict[robot_name]["runtime"].reset()


@configclass
class PepperBoxGraspControllerCfg(ControllerCfg):
    """Primary bottom-level controller for a fixed-base Pepper box transfer."""

    command_dim: int = 1

    skill_config: G1BoxGraspConfig = G1BoxGraspConfig()
    auto_start: bool = True

    def load(self, robot_name: str, task_name: str, device: str, env: Any) -> Dict[str, Any]:
        del task_name, device, env
        return {
            "name": robot_name,
            "runtime": PepperBoxGraspRuntime(G1BoxGraspConfig.from_environment(self.skill_config)),
            "auto_start": bool(self.auto_start),
        }

    def compute_action(
        self, env: Any, robot_name: str, observations: Optional[torch.Tensor], controller_dict: Dict[str, Any]
    ) -> torch.Tensor:
        del robot_name, observations, controller_dict
        return torch.zeros((env.num_envs, 1), device=env.device, dtype=torch.float32)

    def compute_action_from_command(
        self, env: Any, robot_name: str, command: torch.Tensor, controller_dict: Dict[str, Any]
    ) -> torch.Tensor:
        return compute_pepper_box_grasp_action(self, env, robot_name, command, controller_dict)

    def reset(
        self, env: Any, robot_name: str, controller_dict: Dict[str, Any], env_ids: Any
    ) -> None:
        del env, robot_name, env_ids
        runtime = controller_dict.get("runtime")
        if runtime is not None:
            runtime.reset()

    def apply_action(
        self, env: Any, robot_name: str, action: torch.Tensor, controller_dict: Dict[str, Any]
    ) -> None:
        apply_pepper_box_grasp_action(env, robot_name, action, controller_dict)


PEPPER_BOX_GRASP_CFG = PepperBoxGraspControllerCfg(
    robot_type="Pepper",
    compute_action_from_command_func=compute_pepper_box_grasp_action,
    apply_action_func=apply_pepper_box_grasp_action,
    # Pepper's longer upper-body settling time is still physically gated, but
    # needs a slightly wider M20-style reach tolerance and timeout than G1.
    skill_config=G1BoxGraspConfig(
        grasp_distance_threshold_m=0.12,
        grasp_timeout_s=12.0,
        # Pepper's bundled arm chain becomes singular below roughly 18 cm of
        # palm-to-palm lateral span.  Keep a comfortable outer side target for
        # narrow cartons; the runtime still gates attachment by measured palm
        # distance to that target.
        min_side_offset_m=0.18,
    ),
)


__all__ = [
    "PEPPER_BOX_GRASP_CFG",
    "PepperBoxGraspControllerCfg",
    "PepperBoxGraspRuntime",
    "reset_pepper_box_grasp",
    "start_pepper_box_grasp",
]
