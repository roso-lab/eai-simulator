"""Native shared-tick G1 grasp/place: two clones, two robots in each clone.

Uses real articulation joint drives and measured palms with ideal attachment.
Contact-force success is deliberately not asserted. Run with Isaac Lab Python.
"""
from pathlib import Path
import argparse
import json
import sys
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--asset', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    import os
    os.environ['EAI_DISABLE_ORSUS_ROS_ENV'] = '1'
    for package in ('EAI', 'EAI_assets', 'EAI_hmrs'):
        sys.path.insert(0, str(ROOT / 'source' / package))
    def hook(cfg):
        import isaaclab.sim as sim
        from isaaclab.assets import AssetBaseCfg, RigidObjectCfg
        from EAI_assets.robots.g1_inspire_manipulation import calibrated_g1_cfg
        from EAI.controllers.fixed_manipulation import FixedManipulationControllerCfg
        cfg.scene.terrain = None
        cfg.scene.env_spacing = 5.
        cfg.controllers = {}
        for i in range(2):
            name, x = f'g1_{i+1}', i * 2.
            setattr(cfg.scene, name, calibrated_g1_cfg(args.asset,
                prim_path='{ENV_REGEX_NS}/'+name, position=(x, 0., .7923)))
            setattr(cfg.scene, f'rod_{i+1}', RigidObjectCfg(prim_path='{ENV_REGEX_NS}/'+f'rod_{i+1}',
                spawn=sim.CylinderCfg(radius=.012, height=.24,
                    rigid_props=sim.RigidBodyPropertiesCfg(disable_gravity=True),
                    mass_props=sim.MassPropertiesCfg(mass=.05),
                    collision_props=sim.CollisionPropertiesCfg(collision_enabled=False)),
                init_state=RigidObjectCfg.InitialStateCfg(pos=(x+.28, -.46, 1.2423))))
            cfg.controllers[name] = FixedManipulationControllerCfg()
        cfg.__post_init__()
        cfg.sim.use_fabric = False
    from isaaclab.app import AppLauncher
    launcher = AppLauncher(headless=True, device='cpu', enable_cameras=True)
    from contextlib import contextmanager
    from types import SimpleNamespace
    @contextmanager
    def native_session():
        from isaaclab.scene import InteractiveSceneCfg
        from EAI.hmrs_env.multi_robot_direct_env_cfg import MultiRobotDirectEnvCfg
        from EAI.hmrs_env.multi_robot_direct_env import MultiRobotDirectEnv
        cfg = MultiRobotDirectEnvCfg(scene=InteractiveSceneCfg(num_envs=2, env_spacing=5.))
        cfg.sim.device = 'cpu'
        hook(cfg)
        env = MultiRobotDirectEnv(cfg)
        try:
            env.reset()
            yield SimpleNamespace(base_env=env)
        finally:
            env.close()
    report = {'physical_success':'unknown', 'mode':'live joint drives / ideal attachment', 'tasks':[]}
    try:
        with native_session() as session:
            import torch
            import omni.usd
            from algorithm.skills.contracts import SkillRequest
            from task.executors.g1_pick_place import ObjectBinding, GraspProfile
            from task.executors.manipulation import ManipulationRobotBinding, ManipulationSkillRuntime
            env = session.base_env
            print('shared environment ready', flush=True)
            bindings=[]
            profile = GraspProfile.builtin('bottle_neck')
            profile = GraspProfile('rod', profile.hand_point, (0.,0.,0.), profile.closed_joints,
                profile.robot_asset_sha256, (.024,.024,.24))
            for row in range(2):
                for i in range(2):
                    bindings.append(ManipulationRobotBinding('g1', f'g1_{i+1}', row,
                        objects={f'rod_{i+1}':ObjectBinding('{ENV_REGEX_NS}/'+f'rod_{i+1}',profile)},
                        robot_path='{ENV_REGEX_NS}/'+f'g1_{i+1}'))
            runtime = ManipulationSkillRuntime(env, bindings, stage=omni.usd.get_context().get_stage())
            handles=[]
            for b in bindings:
                ref = next(iter(b.objects))
                pick = env.scene.rigid_objects[ref].data.root_pos_w[b.env_id].cpu().tolist()
                place = [pick[0]+.02, pick[1]+.40, pick[2]]
                request=SkillRequest(f'{b.task_robot_id}/transfer','eai.manipulation.pick_place', b.task_robot_id,
                    parameters={'object_ref':ref,'pick_position':pick,'place_position':place})
                print('planning '+b.task_robot_id, flush=True)
                binding=runtime.binding(request)
                lease=runtime.resources.acquire(request.request_id, ('robot:'+request.robot_id, *binding.resources))
                handle=binding.start(binding.provider.plan(request),scene=env,lease=lease)
                handles.append((b,handle,lease,place))
                print('started '+b.task_robot_id, flush=True)
            pending=list(handles)
            for tick in range(6000):
                if tick % 250 == 0:
                    print('tick', tick, [(b.task_robot_id,h.handle.skill.transfer.phase) for b,h,_,_ in pending],flush=True)
                runtime.step(env,{b.task_robot_id:h.command() for b,h,_,_ in pending})
                for item in list(pending):
                    b,h,lease,place=item
                    receipt=h.after_step()
                    if receipt:
                        measured=env.scene.rigid_objects[next(iter(b.objects))].data.root_pos_w[b.env_id]
                        error=float(torch.linalg.vector_norm(measured-torch.tensor(place)))
                        assert error < .02, (b.task_robot_id,error)
                        report['tasks'].append({'robot_id':b.task_robot_id,'ticks':tick+1,'place_error_m':error,
                            'receipt':receipt.outcome.value})
                        runtime.resources.release(lease)
                        pending.remove(item)
                if not pending:break
            assert not pending, 'shared manipulation did not settle within 6000 ticks'
            report['completed']=True
    except Exception:
        import traceback
        report['completed']=False
        report['error']=traceback.format_exc()
        print(report['error'], flush=True)
    finally:
        args.report.parent.mkdir(parents=True,exist_ok=True)
        args.report.write_text(json.dumps(report,indent=2))
        # Kit close may exit the process; keep the evidence on disk first.
        print(json.dumps(report), flush=True)
        launcher.app.close(wait_for_replicator=False, skip_cleanup=True)

if __name__=='__main__':main()
