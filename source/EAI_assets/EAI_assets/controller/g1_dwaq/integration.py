"""Opt-in HeteroCoord controller configuration; does not change global defaults."""
from isaaclab.utils import configclass
from EAI.controllers import ControllerCfg
from .deployment import G1DwaqController

def compute_from_command(cfg,env,name,command,state):
    return state['deployment'].compute(command)

def apply_action(env,name,action,state):
    state['deployment'].apply(action)

@configclass
class G1DwaqControllerCfg(ControllerCfg):
    robot_type: str='G1'
    command_dim: int=3
    compute_action_from_command_func: object=compute_from_command
    apply_action_func: object=apply_action

    def load(self,robot_name,task_name,device,env):
        return {'name':robot_name,'deployment':G1DwaqController(env.scene.articulations[robot_name])}

    def reset(self,env,robot_name,controller_dict,env_ids):
        controller_dict['deployment'].reset(env_ids)

G1_DWAQ_CFG=G1DwaqControllerCfg()
