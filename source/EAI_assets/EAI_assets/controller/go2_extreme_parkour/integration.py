"""Opt-in HeteroCoord RSL configuration using the dynamic-batch ONNX."""
from pathlib import Path
from isaaclab.utils import configclass
from EAI.controllers import RSLControllerCfg
from . import deployment as deploy

@configclass
class Go2ExtremeParkourControllerCfg(RSLControllerCfg):
    def reset(self,env,robot_name,controller_dict,env_ids):
        deploy.reset_controller(env,robot_name,env_ids)

GO2_EXTREME_PARKOUR_TEACHER_CFG=Go2ExtremeParkourControllerCfg(
    model_path=str(Path(__file__).parent/'exported/policy_dynamic.onnx'),robot_type='Go2',
    observation_func=deploy.compute_go2_extreme_parkour_observations,
    apply_action_func=deploy.apply_go2_extreme_parkour_action,
    compute_action_from_command_func=deploy.compute_rsl_action_from_command)

def sensor_configs(robot_prim_path,collision_mesh_paths):
    """Caller must supply the actual scene collision surface, not a Factory proxy."""
    from isaaclab.sensors import RayCasterCfg,ContactSensorCfg,patterns
    return dict(height_scanner=RayCasterCfg(prim_path=robot_prim_path+'/base',
        offset=RayCasterCfg.OffsetCfg(pos=(.375,0.,20.)),ray_alignment='yaw',
        pattern_cfg=patterns.GridPatternCfg(resolution=.15,size=(1.65,1.5),ordering='yx'),
        mesh_prim_paths=list(collision_mesh_paths)),
        contact_forces=ContactSensorCfg(prim_path=robot_prim_path+'/.*',history_length=2,update_period=.02))
