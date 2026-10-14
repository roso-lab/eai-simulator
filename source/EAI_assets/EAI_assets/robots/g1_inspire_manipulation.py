"""Explicit calibrated, fixed-base G1 asset binding for task-owned manipulation."""
from pathlib import Path


def calibrated_g1_cfg(asset_path, *, prim_path='{ENV_REGEX_NS}/G1', position=(0., 0., .7923)):
    """Require a caller-resolved asset; never substitute a different G1 hand USD."""
    import hashlib
    import isaaclab.sim as sim
    from isaaclab.assets import ArticulationCfg
    from isaaclab.actuators import ImplicitActuatorCfg
    asset = Path(asset_path).expanduser().resolve(strict=True)
    expected = '86047174b87b4df485e996232fb4d2ece5901a9bcd6f9a54b78f961ce664730e'
    if hashlib.sha256(asset.read_bytes()).hexdigest() != expected:
        raise ValueError('G1 asset differs from the Inspire grip calibration')
    return ArticulationCfg(prim_path=prim_path,
        spawn=sim.UsdFileCfg(usd_path=str(asset),
            rigid_props=sim.RigidBodyPropertiesCfg(disable_gravity=True),
            articulation_props=sim.ArticulationRootPropertiesCfg(fix_root_link=True,
                enabled_self_collisions=False, solver_position_iteration_count=16,
                solver_velocity_iteration_count=4)),
        init_state=ArticulationCfg.InitialStateCfg(pos=position,
            joint_pos={'.*': 0.}, joint_vel={'.*': 0.}),
        actuators={'all': ImplicitActuatorCfg(joint_names_expr=['.*'],
            effort_limit_sim=300., velocity_limit_sim=2., stiffness=3000., damping=100.)})
