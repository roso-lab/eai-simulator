"""Minimal Isaac Lab playback using only the submitted deployment bundle and USDs.

Example: python tools/locomotion_deployment/play.py --robot g1 --headless --num-envs 2
"""
import argparse,json,sys,os,traceback
from pathlib import Path
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[2]
BASE=ROOT/'source/EAI_assets/EAI_assets/controller'
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--robot',choices=['g1','go2'],required=True)
parser.add_argument('--asset-root',type=Path,default=ROOT/'usd')
parser.add_argument('--num-envs',type=int,default=1)
parser.add_argument('--steps',type=int,default=250)
parser.add_argument('--speed',type=float,default=.3)
parser.add_argument('--output',type=Path,default=ROOT/'logs/deployment_play.json')
from isaaclab.app import AppLauncher
AppLauncher.add_app_launcher_args(parser)
args=parser.parse_args()
if args.num_envs<1 or args.steps<1:parser.error('num-envs and steps must be positive')
app=AppLauncher(args).app
sys.path.insert(0,str(BASE))

def main():
    import torch
    import isaaclab.sim as su
    from isaaclab.assets import Articulation
    from isaaclab.sensors import ContactSensor,ContactSensorCfg,RayCaster,RayCasterCfg,patterns
    from pxr import UsdGeom
    torch.set_num_threads(2)
    is_g1=args.robot=='g1'
    dt=.001 if is_g1 else .005
    sim=su.SimulationContext(su.SimulationCfg(dt=dt,device=args.device,render_interval=round(.02/dt)))
    plane=su.GroundPlaneCfg();plane.func('/World/Ground',plane)
    light=su.DomeLightCfg(intensity=1500.);light.func('/World/Light',light)
    for i in range(args.num_envs):
        prim=UsdGeom.Xform.Define(sim.stage,f'/World/envs/env_{i}')
        prim.AddTranslateOp().Set((0.,i*4.,0.))
    if is_g1:
        from g1_dwaq.robot import make_robot_cfg
        from g1_dwaq.deployment import G1DwaqController,critic_height_scan
        asset=args.asset_root/'robot/g1/g1_29dof_with_inspire_rev_1_0.usd'
    else:
        from go2_extreme_parkour.robot import make_robot_cfg
        from go2_extreme_parkour import deployment as deploy
        asset=args.asset_root/'robot/go2/go2.usd'
    cfg=make_robot_cfg(asset);cfg.prim_path='/World/envs/env_.*/Robot'
    robot=Articulation(cfg)
    contacts=ContactSensor(ContactSensorCfg(prim_path=cfg.prim_path+'/.*',history_length=2,update_period=.005))
    scanner=RayCaster(RayCasterCfg(prim_path=cfg.prim_path+('/torso_link' if is_g1 else '/base'),
        offset=RayCasterCfg.OffsetCfg(pos=(0. if is_g1 else .375,0.,20.)),ray_alignment='yaw',
        pattern_cfg=patterns.GridPatternCfg(resolution=.1 if is_g1 else .15,
            size=(1.6,1.) if is_g1 else (1.65,1.5),ordering='yx'),mesh_prim_paths=['/World/Ground']))
    sim.reset()
    pose=robot.data.default_root_state[:,:7].clone();pose[:,1]=torch.arange(args.num_envs,device=robot.device)*4.
    robot.write_root_pose_to_sim(pose)
    robot.write_root_velocity_to_sim(torch.zeros((args.num_envs,6),device=robot.device))
    robot.write_joint_state_to_sim(robot.data.default_joint_pos.clone(),torch.zeros_like(robot.data.joint_vel))
    robot.update(dt);contacts.update(dt,force_recompute=True);scanner.update(dt,force_recompute=True)
    command=torch.tensor([[args.speed,0.,0.]],device=robot.device).repeat(args.num_envs,1)
    if is_g1:controller=G1DwaqController(robot)
    else:
        policy=torch.jit.load(str(BASE/'go2_extreme_parkour/exported/policy.pt'),map_location=robot.device).eval()
        env=SimpleNamespace(scene=SimpleNamespace(articulations={'robot':robot},sensors={'robot_contact_forces':contacts,'robot_height_scanner':scanner}),
            _controllers_dict={'robot':{}},actions_dict={},common_step_counter=0)
        commands={'base_velocity':command,'parkour_waypoint_yaw':command.new_zeros((args.num_envs,2))}
        env.get_command=lambda name,key:commands.get(key)
    start=robot.data.root_pos_w.clone();worst=torch.full((args.num_envs,),-1.,device=robot.device)
    sensor_misses=0;action_shape=None
    for k in range(args.steps):
        with torch.no_grad():
            if is_g1:
                action=controller.compute(command);controller.apply(action)
                critic_height_scan(scanner.data.pos_w,scanner.data.ray_hits_w)
            else:
                obs=deploy.compute_go2_extreme_parkour_observations(env,'robot')
                action=policy(obs).clamp(-4.8,4.8);deploy.apply_go2_extreme_parkour_action(env,'robot',action,{})
                env.actions_dict['robot']=action
            action_shape=list(action.shape)
            for _ in range(round(.02/dt)):
                robot.write_data_to_sim();sim.step(render=False);robot.update(dt);contacts.update(dt)
            scanner.update(.02,force_recompute=True)
            if not is_g1:env.common_step_counter+=1
            worst=torch.maximum(worst,robot.data.projected_gravity_b[:,2])
            assert torch.isfinite(robot.data.root_state_w).all()
            assert torch.isfinite(action).all()
            sensor_misses+=int((~torch.isfinite(scanner.data.ray_hits_w)).sum())
            if not args.headless:
                p=robot.data.root_pos_w[0].tolist();sim.set_camera_view((p[0]+3.,p[1]+3.,p[2]+1.5),p);sim.render()
    report=dict(robot=args.robot,steps=args.steps,num_envs=args.num_envs,physics_dt=dt,policy_dt=.02,
        robot_usd=str(asset.resolve()),joint_names=robot.joint_names,action_shape=action_shape,
        body_joint_names=list(__import__('g1_dwaq.deployment',fromlist=['JOINT_NAMES']).JOINT_NAMES) if is_g1 else list(deploy.EXTREME_PARKOUR_JOINT_ORDER),
        start_xyz=start.tolist(),end_xyz=robot.data.root_pos_w.tolist(),displacement_xyz=(robot.data.root_pos_w-start).tolist(),
        worst_projected_gravity_z=worst.tolist(),height_rays=int(scanner.data.ray_hits_w.shape[1]),ray_missing_values=sensor_misses,
        finite=True,scope='Batched policy/USD integration smoke; not stairs or rough-terrain acceptance')
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(report,indent=2));print(json.dumps(report),flush=True)
    # This standalone headless worker exits after flushing its report. Kit
    # close can hang with CPU multi-articulation scenes on the tested 5.1 build.
    if args.headless:os._exit(0)
    sim.stop();app.close();os._exit(0)

try:main()
except Exception:
    traceback.print_exc();sys.stderr.flush();os._exit(1)
