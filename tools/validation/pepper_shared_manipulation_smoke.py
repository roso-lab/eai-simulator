"""Native two-clone Pepper transfer through the unified manipulation runtime."""
from pathlib import Path
import argparse
import json
import os
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
for package in ('EAI', 'EAI_assets', 'EAI_hmrs'):
    sys.path.insert(0, str(ROOT / 'source' / package))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--asset', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    os.environ['EAI_DISABLE_ORSUS_ROS_ENV'] = '1'
    from isaaclab.app import AppLauncher
    launcher = AppLauncher(headless=True, device='cpu', enable_cameras=True)
    report = {'physical_success': 'unknown', 'mode': 'bilateral ideal attachment', 'tasks': []}
    env = None
    try:
        import torch
        import isaaclab.sim as sim
        from isaaclab.assets import AssetBaseCfg, RigidObjectCfg
        from isaaclab.scene import InteractiveSceneCfg
        from EAI.hmrs_env.multi_robot_direct_env_cfg import MultiRobotDirectEnvCfg
        from EAI.hmrs_env.multi_robot_direct_env import MultiRobotDirectEnv
        from EAI_assets.robots.pepper import PEPPER_BOX_GRASP_ROBOT_CFG
        from EAI_assets.box_transport_skill import transport_grasp_config, spawn_transport_robot_with_collision_filters
        from EAI_assets.controller.traditional.pepper_box_transport.controller import PEPPER_BOX_TRANSPORT_TASK_CFG
        from algorithm.skills.contracts import SkillRequest
        from task.executors.manipulation import ManipulationRobotBinding, ManipulationSkillRuntime
        controller = PEPPER_BOX_TRANSPORT_TASK_CFG.copy()
        transport = controller.transport_config
        grasp = transport_grasp_config(controller.skill_config, transport)
        cfg = MultiRobotDirectEnvCfg(scene=InteractiveSceneCfg(num_envs=2, env_spacing=5.))
        cfg.sim.device = 'cpu'
        cfg.sim.use_fabric = False
        cfg.scene.terrain = None
        robot = PEPPER_BOX_GRASP_ROBOT_CFG.replace(prim_path='{ENV_REGEX_NS}/pepper_1')
        robot.spawn.usd_path = str(args.asset.resolve())
        robot.spawn.func = spawn_transport_robot_with_collision_filters
        cfg.scene.pepper_1 = robot
        for name, pos in ((transport.source_table_name, transport.source_table_position),
                          (transport.destination_table_name, transport.destination_table_position)):
            setattr(cfg.scene, name, AssetBaseCfg(prim_path='{ENV_REGEX_NS}/'+name,
                spawn=sim.CuboidCfg(size=transport.table_size,
                    collision_props=sim.CollisionPropertiesCfg()),
                init_state=AssetBaseCfg.InitialStateCfg(pos=pos)))
        setattr(cfg.scene, grasp.cube_prim_name, RigidObjectCfg(
            prim_path='{ENV_REGEX_NS}/'+grasp.cube_prim_name,
            spawn=sim.CuboidCfg(size=grasp.box_size,
                rigid_props=sim.RigidBodyPropertiesCfg(),
                mass_props=sim.MassPropertiesCfg(mass=8.),
                collision_props=sim.CollisionPropertiesCfg()),
            init_state=RigidObjectCfg.InitialStateCfg(pos=grasp.box_position)))
        cfg.controllers = {'pepper_1': controller}
        cfg.__post_init__()
        env = MultiRobotDirectEnv(cfg)
        env.reset()
        runtime = ManipulationSkillRuntime(env, [
            ManipulationRobotBinding('pepper', 'pepper_1', row,
                objects={grasp.cube_prim_name: '{ENV_REGEX_NS}/'+grasp.cube_prim_name})
            for row in range(2)])
        pending = []
        for row in range(2):
            identity = f'env_{row}/pepper_1'
            pick = env.scene.rigid_objects[grasp.cube_prim_name].data.root_pos_w[row].tolist()
            origin = env.scene.env_origins[row].tolist()
            place = [grasp.place_position[i]+transport.destination_offset[i]+origin[i] for i in range(3)]
            request = SkillRequest(identity+'/transfer', 'eai.manipulation.pick_place', identity,
                parameters={'object_ref': grasp.cube_prim_name, 'pick_position': pick, 'place_position': place})
            binding = runtime.binding(request)
            lease = runtime.resources.acquire(request.request_id, ('robot:'+identity, *binding.resources))
            handle = binding.start(binding.provider.plan(request), scene=env, lease=lease)
            pending.append((identity, handle, lease, row, place))
        for tick in range(4000):
            if tick % 250 == 0:
                print('tick', tick, [(identity, h.handle.controller['runtime'].state.phase.value,
                    h.handle.controller['transport'].phase.value) for identity, h, *_ in pending], flush=True)
            runtime.step(env, {identity: h.command() for identity, h, *_ in pending})
            for item in list(pending):
                identity, h, lease, row, place = item
                receipt = h.after_step()
                if receipt is not None:
                    error = float(torch.linalg.vector_norm(
                        env.scene.rigid_objects[grasp.cube_prim_name].data.root_pos_w[row]-torch.tensor(place)))
                    assert receipt.outcome.value == 'completed' and error < .08, (identity, receipt, error)
                    report['tasks'].append({'robot_id': identity, 'ticks': tick+1,
                        'place_error_m': error, 'receipt': receipt.outcome.value})
                    runtime.resources.release(lease)
                    pending.remove(item)
            if not pending:
                break
        assert not pending, 'Pepper transfers did not settle within 4000 shared ticks'
        report['completed'] = True
    except Exception:
        import traceback
        report['completed'] = False
        report['error'] = traceback.format_exc()
        print(report['error'], flush=True)
    finally:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2))
        print(json.dumps(report), flush=True)
        if env is not None:
            env.close()
        launcher.app.close(wait_for_replicator=False, skip_cleanup=True)


if __name__ == '__main__':
    main()
