"""Rebuild the saved articulation parameters using only Isaac Lab and bundled JSON."""
from pathlib import Path
import hashlib
from .deployment import CONTRACT


def make_robot_cfg(usd_path):
    import isaaclab.sim as sim_utils
    from isaaclab.assets import ArticulationCfg
    from isaaclab.actuators import ImplicitActuatorCfg
    usd_path=Path(usd_path)
    if hashlib.sha256(usd_path.read_bytes()).hexdigest()!=CONTRACT['contract']['asset_sha256']:
        raise ValueError('G1 USD hash differs from model_287 contract; revalidate the asset before deployment')
    saved=CONTRACT['robot']
    actuators={}
    for name,value in saved['actuators'].items():
        params={k:v for k,v in value.items() if k!='class_type' and v is not None}
        actuators[name]=ImplicitActuatorCfg(**params)
    rigid=saved['spawn']['rigid_props']
    articulation=saved['spawn']['articulation_props']
    return ArticulationCfg(prim_path='/World/G1',spawn=sim_utils.UsdFileCfg(
        usd_path=str(Path(usd_path).resolve()),activate_contact_sensors=True,
        rigid_props=sim_utils.RigidBodyPropertiesCfg(**{k:v for k,v in rigid.items() if v is not None}),
        articulation_props=sim_utils.ArticulationRootPropertiesCfg(**{k:(int(v) if 'iteration_count' in k else v) for k,v in articulation.items() if v is not None})),
        init_state=ArticulationCfg.InitialStateCfg(pos=(0.,0.,.8),
            joint_pos=saved['init_state']['joint_pos'],joint_vel=saved['init_state']['joint_vel']),
        actuators=actuators,soft_joint_pos_limit_factor=saved['soft_joint_pos_limit_factor'])
