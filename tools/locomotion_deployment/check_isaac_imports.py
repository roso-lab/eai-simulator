"""Verify opt-in configs and full training-source modules after Isaac startup."""
from pathlib import Path
import sys,os,json
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'source/EAI'),str(ROOT/'source/EAI_assets/EAI_assets/controller')]
from isaaclab.app import AppLauncher
app=AppLauncher(headless=True,device='cpu').app
from g1_dwaq.integration import G1_DWAQ_CFG
from go2_extreme_parkour.integration import GO2_EXTREME_PARKOUR_TEACHER_CFG
from go2_extreme_parkour import extreme_parkour_observations,extreme_parkour_v1_geometry,extreme_parkour_v1_heightfield,commands
assert callable(G1_DWAQ_CFG.compute_action_from_command_func)
assert callable(G1_DWAQ_CFG.apply_action_func)
assert callable(GO2_EXTREME_PARKOUR_TEACHER_CFG.observation_func)
assert hasattr(commands,'ExtremeParkourVelocityCommand') and hasattr(commands,'ParkourWaypointCommand')
import torch
from types import SimpleNamespace as S
from check_adapters import fixture
from go2_extreme_parkour import deployment as d
env,cmd=fixture(4)
env.num_envs=4;env.device=torch.device('cpu')
env.episode_length_buf=torch.zeros(4,dtype=torch.long)
env.action_manager=S(action=torch.zeros(4,12))
env.command_manager=S(get_command=lambda name:cmd['parkour_waypoint_yaw'] if name=='parkour_waypoint' else cmd[name])
env.scene.terrain=S(terrain_types=torch.full((4,),8))
robot=env.scene.articulations['go2'];robot.device=env.device
robot.data.default_joint_pos=d._TRAINING_DEFAULT_POS.flip(0)[None].repeat(4,1)
ref=object.__new__(extreme_parkour_observations.ExtremeParkourTeacherObservation)
ref._env=env;ref.asset=robot;ref.asset_cfg=S(joint_ids=list(reversed(range(12))))
ref.contact_sensor=env.scene.sensors['go2_contact_forces'];ref.contact_sensor_cfg=S(body_ids=list(range(4)))
ref.velocity_command_name='base_velocity';ref.waypoint_command_name='parkour_waypoint'
ref.history_length=10;ref.yaw_update_interval=5
ref.history=torch.zeros(4,10,53);ref.cached_waypoint_yaw=torch.zeros(4,2)
# Compare sensor-independent ordering/history to the actual training class.
# Flat collision scan is zero for both; terrain-heightfield generation is separate.
ref._height_scan=lambda env:torch.zeros(4,132)
error=0.
for step in range(8):
    env.common_step_counter=step
    if step==3:
        ids=torch.tensor([0,2]);env.episode_length_buf[ids]=0
        ref.reset(ids);d.reset_controller(env,'go2',ids)
    robot.data.root_ang_vel_b.add_(.01)
    a=d.compute_go2_extreme_parkour_observations(env,'go2')
    b=ref(env,ref.asset_cfg,ref.contact_sensor_cfg)
    torch.testing.assert_close(a,b,rtol=0.,atol=0.)
    error=max(error,float((a-b).abs().max()))
    env.episode_length_buf+=1
print('Isaac configs and all requested source module imports passed',flush=True)
Path(__file__).with_name('isaac_import_verification.json').write_text(json.dumps({'passed':True,'configs':['G1_DWAQ_CFG','GO2_EXTREME_PARKOUR_TEACHER_CFG'],'source_modules':4,'go2_training_observation_parity':{'steps':8,'batch':4,'partial_reset':True,'max_abs_error':error,'height_scan_fixture':'flat zero scan; actual heightfield sampling not compared'}},indent=2))
os._exit(0)
