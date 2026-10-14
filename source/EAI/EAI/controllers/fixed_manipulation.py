"""Neutral primary controller for a fixed G1 whose targets are task-owned."""
from isaaclab.utils import configclass
from .base import ControllerCfg


@configclass
class FixedManipulationControllerCfg(ControllerCfg):
    robot_type: str = 'G1'
    command_dim: int = 1

    def load(self, robot_name, task_name, device, env):
        robot = env.scene.articulations[robot_name]
        if not robot.is_fixed_base:
            raise ValueError('fixed manipulation requires a fixed articulation base')
        return {'name': robot_name, 'task': task_name}

    def apply_action(self, env, robot_name, action, controller_dict):
        # Drives retain their targets until the G1 post-apply runtime updates them.
        pass

    def reset(self, env, robot_name, controller_dict, env_ids):
        pass
