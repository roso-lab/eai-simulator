"""Render seeded procedural trials through the simulator's public session API."""
from pathlib import Path
import argparse
import hashlib
import json
import sys
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=ROOT / 'artifacts/g1_random_pick_place')
    parser.add_argument('--fps', type=int, default=24)
    parser.add_argument('--seed', type=int, default=20261014)
    parser.add_argument('--preview', action='store_true')
    args = parser.parse_args()
    if args.fps < 10: parser.error('fps must be at least 10')
    args.output.mkdir(parents=True, exist_ok=True)
    from simulator import SimulatorLaunchConfig, open_simulator_session

    def hook(cfg):
        import isaaclab.sim as sim
        from isaaclab.assets import AssetBaseCfg
        cfg.scene.terrain = None
        cfg.scene.demo_ground = AssetBaseCfg(prim_path='/World/DemoGround',
            spawn=sim.CuboidCfg(size=(80.,80.,.1)),
            init_state=AssetBaseCfg.InitialStateCfg(pos=(0.,0.,-.07)))
        cfg.sim.use_fabric = False

    config = SimulatorLaunchConfig(env='g1_random_pick_place', resolved_env_name='g1_random_pick_place',
        selection_data={'scene_key':'plane','robots':[]}, headless=True, device='cpu',
        enable_ros_bridge_extension=False, disable_orsus_ros_env=True,
        env_cfg_hook=hook, app_launcher_args={'enable_cameras':True})
    with open_simulator_session(config) as session:
        import omni.usd
        import omni.timeline
        import omni.replicator.core as rep
        import carb
        import imageio.v2 as imageio
        from PIL import Image, ImageDraw, ImageFont
        from pxr import Gf, UsdGeom, UsdLux
        from tools.g1_random_pick_place.scene import RandomTrial
        from task.controllers.g1_pick_place.geometry import set_world_matrix

        omni.timeline.get_timeline_interface().pause()
        stage = omni.usd.get_context().get_stage()
        for light in list(stage.Traverse()):
            if light.HasAPI(UsdLux.LightAPI): light.SetActive(False)
        dome = UsdLux.DomeLight.Define(stage, '/World/RandomDemo/Ambient')
        dome.CreateIntensityAttr(1800); dome.CreateColorAttr(Gf.Vec3f(.82,.88,1.))
        key = UsdLux.DistantLight.Define(stage, '/World/RandomDemo/Key')
        key.CreateIntensityAttr(2200); key.CreateAngleAttr(12)
        key.CreateColorAttr(Gf.Vec3f(1.,.85,.68))
        UsdGeom.Xformable(key).AddRotateXYZOp().Set(Gf.Vec3f(25,-35,-35))
        fill = UsdLux.DistantLight.Define(stage, '/World/RandomDemo/Fill')
        fill.CreateIntensityAttr(950); fill.CreateAngleAttr(25)
        fill.CreateColorAttr(Gf.Vec3f(.9,.94,1.))
        settings = carb.settings.get_settings()
        for name, value in (('op',1),('filmIso',100.),('cameraShutter',100.),('fNumber',8.)):
            settings.set('/rtx/post/tonemap/' + name, value)
        settings.set('/rtx/post/histogram/enabled',False)
        font = ImageFont.truetype('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc', 27)
        small = ImageFont.truetype('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc', 23)
        camera = rep.create.camera(position=(3,-3,2.7), look_at=(1,-1,1), focal_length=28)
        product = rep.create.render_product(camera, (1920,1080))
        rgb = rep.AnnotatorRegistry.get_annotator('rgb'); rgb.attach(product)
        cam = next(p for p in stage.Traverse() if p.IsA(UsdGeom.Camera) and str(p.GetPath()).startswith('/Replicator'))
        captions = {'prepare':'准备动作', 'approach':'抬手接近', 'grasp':'对准并闭合手指',
            'lift':'向上动作', 'transfer':'移动到动作点', 'lower':'下降动作',
            'release':'张开手指', 'withdraw':'向上退离',
            'return_ready':'回到准备位', 'retract':'收回手臂', 'done':'抓放完成'}
        manifest = args.output / 'audit.json'
        specifications = json.loads(manifest.read_text()) if manifest.exists() else None
        if specifications and specifications['seed'] != args.seed:
            raise ValueError('audit seed differs; use a new output directory or rerun the audit')
        rng = np.random.default_rng(args.seed)
        video = imageio.get_writer(str(args.output / 'demo.mp4'), fps=args.fps,
                                   codec='libx264', quality=8, macro_block_size=1)
        report = {'seed':args.seed, 'fps':args.fps, 'resolution':[1920,1080], 'trials':[],
            'images':[], 'mode':'kinematic fixed action with no payload attachment',
            'physical_grasp_verified':False, 'completed':False, 'source_sha256':{}}
        for source in (Path(__file__), Path(__file__).with_name('scene.py'), ROOT / 'task/controllers/g1_pick_place/runtime.py',
                       *(ROOT / 'task/controllers/g1_pick_place').glob('*.py')):
            report['source_sha256'][str(source.relative_to(ROOT))] = hashlib.sha256(source.read_bytes()).hexdigest()
        total_frames = 0
        try:
            for index in range(3):
                if index: stage.GetPrimAtPath(f'/World/RandomDemo/Trial{index-1}').SetActive(False)
                trial = RandomTrial(stage, index, rng,
                    specification=specifications['trials'][index] if specifications else None)
                # Camera follows the NEW fixed root pose between independent scene cuts.
                eye = (trial.root_matrix @ np.array([2.2,-2.0,1.40,1]))[:3]
                target = (trial.root_matrix @ np.array([.20,-.23,.24,1]))[:3]
                matrix = Gf.Matrix4d().SetLookAt(Gf.Vec3d(*eye), Gf.Vec3d(*target), Gf.Vec3d(0,0,1)).GetInverse()
                set_world_matrix(cam, np.asarray(matrix).T)
                UsdGeom.Camera(cam).GetFocalLengthAttr().Set(34)
                for _ in range(10): session.simulation_app.update()
                last_phase = None
                for frame in range(args.fps * 90):
                    trial.step(1 / args.fps)
                    phase = trial.skill.phase
                    changed = phase != last_phase
                    for _ in range(7 if changed else 2): session.simulation_app.update()
                    data = rgb.get_data()
                    if data.size == 0: raise RuntimeError('RGB annotator returned no image')
                    pic = Image.fromarray(data[:,:,:3]); draw = ImageDraw.Draw(pic)
                    draw.rectangle((0,0,1920,112), fill=(20,25,32))
                    draw.text((28,7), f'G1 随机位置抓放  |  {index+1}/3  {trial.label}  |  {captions.get(phase,phase)}', font=font, fill='white')
                    pick, place = trial.specification['pick_position'], trial.specification['place_position']
                    draw.text((28,49), f'世界坐标  抓取 ({pick[0]:.3f}, {pick[1]:.3f}, {pick[2]:.3f}) → 放置 ({place[0]:.3f}, {place[1]:.3f}, {place[2]:.3f}) m', font=small, fill=(180,215,235))
                    draw.text((28,81), f'固定站位 · seed {args.seed} · 仅执行机器人动作 / 物体保持原位', font=small, fill=(180,190,200))
                    video.append_data(np.asarray(pic)); total_frames += 1
                    if changed:
                        print(json.dumps({'trial':index+1,'seconds':frame/args.fps,'phase':phase},ensure_ascii=False), flush=True)
                        dest = args.output / f'{index+1}_{phase}.png'
                        pic.save(dest); report['images'].append(str(dest))
                    last_phase = phase
                    if args.preview: break
                    if trial.skill.status == 'completed':
                        for _ in range(args.fps): video.append_data(np.asarray(pic)); total_frames += 1
                        break
                else: raise RuntimeError('trial exceeded 90 seconds')
                report['trials'].append(trial.report())
                if args.preview: break
            report['completed'] = len(report['trials']) == 3 and all(t['status']=='completed' for t in report['trials'])
            report['video_duration_s'] = total_frames / args.fps
            stage.GetRootLayer().Export(str(args.output / 'scene_final.usda'))
        except Exception as error:
            report['error'] = str(error)
            raise
        finally:
            video.close(); rgb.detach(product); product.destroy()
            (args.output / 'render_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2) + '\n')


if __name__ == '__main__': main()
