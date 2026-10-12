"""Run a declared static USD/topology route with Go2 through the shared session.

This diagnostic is scene independent. Inputs and physical run outputs are
caller-owned files, normally under tmp; it creates no benchmark task/contract.
"""
from __future__ import annotations
import argparse
from contextlib import contextmanager
import hashlib
import inspect
import json
import math
from pathlib import Path

from algorithm.skills.navigation.ground_navigation import GroundNavigationProvider, GroundTrackingConfig
from algorithm.skills.navigation.landmarks import resolve_landmark_route
from task.executors.ground_navigation import GroundNavigationExecutor, read_ground_pose
from tools.topology_authoring.adapters.usd_scene import UsdSceneAdapter
from tools.topology_authoring.product import load_product, product_digest


def scene_hook(adapter, source, *, mixed_stairs=False, ray_origin_offset_m=0.25,
               topology_path=None, expected_product_digest=None, input_receipt=None):
    """Install static scene and a truthful sensor mesh before environment reset.

    The local ray origin avoids hitting a ceiling above the robot's floor. The
    policy still gets its original base-height-minus-hit observation; scanner
    offset changes ray origins only, not the measured base pose.
    """
    def configure(cfg):
        # This callback runs after AppLauncher. Importing system OpenUSD before
        # Kit loads its own USD ABI can crash the process during extension startup.
        if topology_path is not None:
            load_product(topology_path,adapter,source,expected_digest=expected_product_digest)
        surface = adapter._geometry(source)
        scanner_surface = surface
        if mixed_stairs and getattr(cfg.scene.terrain,"terrain_type",None) == "plane":
            # The shared plane selection adds a real physical ground plane.
            # Include that same plane beneath the asset, so rays outside the
            # building footprint do not miss physical ground that is present.
            import numpy as np
            from tools.topology_authoring.surfaces import SupportSurface
            low=surface.points[:,:2].min(axis=0)-100
            high=surface.points[:,:2].max(axis=0)+100
            offset=len(surface.points)
            points=np.vstack((surface.points,[[low[0],low[1],0],[high[0],low[1],0],
                                              [high[0],high[1],0],[low[0],high[1],0]]))
            triangles=np.vstack((surface.triangles,[[offset,offset+1,offset+2],[offset,offset+2,offset+3]]))
            scanner_surface=SupportSurface(points,triangles)
        from isaaclab.assets import AssetBaseCfg
        import isaaclab.sim as sim
        usd = sim.UsdFileCfg(usd_path=source.payload["usd_path"])
        original = usd.func
        def spawn(path, spawn_cfg, translation=None, orientation=None, **kwargs):
            prim = original(path, spawn_cfg, translation=translation, orientation=orientation, **kwargs)
            from pxr import Usd, UsdPhysics
            for child in Usd.PrimRange(prim):
                if child.IsA(UsdPhysics.Scene):
                    child.SetActive(False)
            if mixed_stairs:
                scanner_surface.create_raycast_proxy(prim.GetStage(), "/World/TopologyTerrain")
            return prim
        usd.func = spawn
        cfg.scene.world = AssetBaseCfg(prim_path="/World/TopologyScene", spawn=usd)
        if mixed_stairs:
            scanner = cfg.scene.go2_1_height_scanner
            scanner.mesh_prim_paths = ["/World/TopologyTerrain"]
            scanner.offset.pos = (0.0, 0.0, ray_origin_offset_m)
        if input_receipt is not None:
            from EAI.controllers.base import normalize_controller_entry
            primary,_auxiliary=normalize_controller_entry(cfg.controllers["go2_1"])
            paths=[Path(cfg.scene.go2_1.spawn.usd_path),Path(primary.resolve_model_path()),
                   Path(inspect.getfile(type(primary))),Path(__file__).resolve()]
            input_receipt.update({str(path.resolve()):hashlib.sha256(path.read_bytes()).hexdigest() for path in paths})
    return configure


def capture_frame(app, path, *, simulation=None):
    """Capture without advancing physics behind the controller's back.

    A raw Kit update while the timeline plays can advance physics with stale
    joint targets. Use SimulationContext.render for active environments; the
    authoring-only fallback explicitly disables simulation around UI updates.
    """
    import carb.settings
    import omni.kit.renderer_capture
    settings = carb.settings.get_settings()
    playing = settings.get("/app/player/playSimulations")
    before = simulation.current_time if simulation is not None else None
    render = simulation.render if simulation is not None else app.update
    try:
        settings.set_bool("/app/player/playSimulations", False)
        for _ in range(6):
            render()
        capture = omni.kit.renderer_capture.acquire_renderer_capture_interface()
        capture.capture_next_frame_swapchain(str(path))
        for _ in range(6):
            render()
    finally:
        settings.set_bool("/app/player/playSimulations", playing)
    if simulation is not None and simulation.current_time != before:
        raise RuntimeError("screenshot advanced physics outside the control loop")


def upright_cosine(quaternion):
    """Body-up alignment with world-up, independent of quaternion magnitude."""
    if len(quaternion) != 4 or not all(math.isfinite(v) for v in quaternion):
        raise ValueError("posture requires a finite wxyz quaternion")
    norm_squared = sum(v*v for v in quaternion)
    if norm_squared <= 1e-12:
        raise ValueError("posture quaternion must be nonzero")
    return 1-2*(quaternion[1]**2+quaternion[2]**2)/norm_squared


@contextmanager
def diagnostic_failure_guard(env, output, report, *, zero_action=None):
    """Stop and retain failure evidence for errors outside the skill executor."""
    try:
        yield
    except BaseException as exc:
        report.update(physical_pass=False, diagnostic_error={
            "type":type(exc).__name__, "message":str(exc)},
            navigation_state={"status":"failed", "reason_code":"diagnostic_error",
                              "safe_action":"stop"}, zero_command_applied=False)
        try:
            if zero_action is None:
                import torch
                zero_action=torch.zeros((1,3),device=env.device)
            env.step({"go2_1":zero_action})
            report["zero_command_applied"]=True
        except Exception as stop_error:
            report["stop_error"]={"type":type(stop_error).__name__,"message":str(stop_error)}
        (output/"result.json").write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding="utf-8")
        print("TOPOLOGY_DIAGNOSTIC_FAILED",str(exc),flush=True)
        if not isinstance(exc, Exception):
            raise


def run(args):
    adapter = UsdSceneAdapter(scene_key=args.scene_key)
    source = adapter.load_source(args.asset_root)
    # Pure preflight here; asset geometry/source validation runs in the post-app
    # configuration hook, still before creating/resetting the physical scene.
    document = load_product(args.topology)
    mixed = args.controller == "GO2_MIXED_STAIRS_CFG"
    capabilities = ("walk", "stairs", "ramp") if mixed else ("walk",)
    route = resolve_landmark_route(document, origin_ref=args.origin, destination_ref=args.destination,
                                    controller_capabilities=capabilities)
    if not route["admitted"]:
        raise ValueError(f"route not admitted: {route}")
    if not route.get("height_aware"):
        raise ValueError("USD deployment requires an explicitly levelled v2 topology")
    output = args.output_root
    output.mkdir(parents=True, exist_ok=False)
    start = route["route_points_m"][0]
    next_point = route["route_points_m"][min(1, len(route["route_points_m"])-1)]
    yaw = math.atan2(next_point[1] - start[1], next_point[0] - start[0])
    selection = {"version": 1, "task_name": "topology_diagnostic", "scene_key": "plane", "robots": [{
        "type": "go2", "controller": {"cfg": args.controller, "mode": "default"}, "attachments": [],
        "spawn_pose": {"position": [start[0], start[1], start[2] + args.spawn_clearance],
                       "rotation": [math.cos(yaw/2),0,0,math.sin(yaw/2)]}}]}
    from simulator import SimulatorLaunchConfig, open_simulator_session
    runtime_inputs={}
    config = SimulatorLaunchConfig(env="topology_diagnostic", resolved_env_name="topology_diagnostic",
        selection_data=selection, device=args.device, headless=args.headless,
        enable_ros_bridge_extension=False, env_cfg_hook=scene_hook(adapter,source,mixed_stairs=mixed,
            ray_origin_offset_m=args.ray_origin_offset,topology_path=args.topology,
            expected_product_digest=product_digest(document),input_receipt=runtime_inputs),
        app_launcher_args={"headless": args.headless, "device": args.device, "fast_shutdown": False})
    report = {"schema":"eai.topology.native_diagnostic", "version":1, "route":route,
              "product_digest":product_digest(document), "source_digests":dict(document.source),
              "controller":args.controller, "physics_device":args.device, "headless":args.headless,
              "runtime_inputs":runtime_inputs,
              "physical_pass":False, "samples":[]}
    (output/"inputs.json").write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding="utf-8")
    with open_simulator_session(config) as session, diagnostic_failure_guard(session.base_env, output, report):
        import torch
        env, app = session.base_env, session.simulation_app
        (output/"inputs.json").write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding="utf-8")
        surface = adapter._geometry(source)
        zero = torch.zeros((1,3),device=env.device)
        if not args.headless:
            from isaacsim.core.utils.viewports import set_camera_view
            camera = source.payload.get("camera", {})
            set_camera_view(eye=camera.get("eye",[start[0]-3,start[1]-4,start[2]+3]),
                            target=camera.get("target",[start[0],start[1],start[2]+.3]))
            # Optional cutaway is visibility-only; physics and scan meshes remain.
            from pxr import UsdGeom
            for path in source.payload.get("runtime_view_hide_paths", []):
                import omni.usd
                stage = omni.usd.get_context().get_stage()
                # Descriptor paths refer to the USD default prim hierarchy.
                default_path = str(adapter._stage.GetDefaultPrim().GetPath())
                if not path.startswith(default_path+"/"):
                    raise ValueError(f"cutaway path is outside USD default prim: {path}")
                runtime_path = "/World/TopologyScene" + path[len(default_path):]
                prim = stage.GetPrimAtPath(runtime_path)
                if not prim:
                    raise ValueError(f"unknown cutaway path: {path}")
                UsdGeom.Imageable(prim).CreateVisibilityAttr().Set(UsdGeom.Tokens.invisible)
        for _ in range(80):
            env.step({"go2_1":zero})
        if not args.headless:
            capture_frame(app, output/"start.png", simulation=env.sim)
        def support(robot):
            return surface.below(read_ground_pose(env, robot).position_m)
        provider = GroundNavigationProvider(tracking_config=GroundTrackingConfig(
            node_radius_m=args.node_radius, cruise_speed_m_s=args.speed,
            max_yaw_rate_rad_s=.6 if mixed else 1.0),
            controller_capabilities={"go2_1":capabilities})
        def validate(sequence, motion):
            if sequence != env.common_step_counter:
                raise ValueError("stale native frame")
            if motion:
                q=env.scene["go2_1"].data.root_quat_w[0].tolist()
                upright=upright_cosine(q)
                if not math.isfinite(upright) or upright < .5:
                    report["motion_failure"]={"reason":"robot_fallen","tick":sequence,
                                              "quaternion_wxyz":q,"upright_cosine":upright}
                    raise ValueError("robot has fallen; route execution stopped")
        executor = GroundNavigationExecutor(env,topology=document,provider=provider,
            world_state_validator=validate,support_height_reader=support,timeout_s=args.timeout)
        def request(intent):
            return {"request_id":f"route/{env.common_step_counter}", "task_id":"route",
                "skill_id":"eai.navigation.ground_navigation", "skill_version":"simulator.skill.v1",
                "robot_id":"go2_1", "target_ref":route["target_ref"],
                "parameters":{**route["skill_parameters"],"intent":intent,"world_state_seq":env.common_step_counter}}
        executor.dispatch(request("start"))
        previous_index = None
        response = None
        for step in range(args.max_steps):
            response=executor.control(request("start" if step == 0 else "continue"))
            pose=read_ground_pose(env,"go2_1")
            sample={"tick":env.common_step_counter,"position_m":list(pose.position_m),"yaw_rad":pose.yaw_rad,
                    "support_z_m":support("go2_1"),"node_index":response.private_evidence.get("node_index"),
                    "velocity":list(response.plan.commands[0].payload["velocity"])}
            q=env.scene["go2_1"].data.root_quat_w[0].tolist()
            sample.update(quaternion_wxyz=q,upright_cosine=upright_cosine(q),
                          simulation_time_s=float(env.sim.current_time))
            if mixed:
                hits=env.scene.sensors["go2_1_height_scanner"].data.ray_hits_w
                sample["finite_scan_rays"]=int(torch.isfinite(hits).all(dim=-1).sum().item())
                sample["scan_hit_z_min_max"]=[float(hits[...,2].min().item()),float(hits[...,2].max().item())]
            report["samples"].append(sample)
            if sample["node_index"] != previous_index:
                print("ROUTE_PROGRESS",json.dumps(sample),flush=True)
                previous_index=sample["node_index"]
                if not args.headless:
                    capture_frame(app,output/f"node-{step:05d}.png", simulation=env.sim)
            if response.state["status"] != "pending":
                break
        if response is not None and response.state["status"] == "pending":
            response=executor.control(request("stop"))
        report["navigation_state"]=dict(response.state)
        report["execution_evidence"]=dict(response.private_evidence)
        try:
            for _ in range(80):
                env.step({"go2_1":zero})
        except Exception as exc:
            report["settle_error"]={"type":type(exc).__name__,"message":str(exc)}
        pose=read_ground_pose(env,"go2_1")
        robot=env.scene["go2_1"]
        q=robot.data.root_quat_w[0].tolist()
        upright=upright_cosine(q)
        end=route["route_points_m"][-1]
        distance=math.dist(pose.position_m[:2],end[:2])
        support_error=abs(support("go2_1")-end[2])
        speed=float(torch.linalg.vector_norm(robot.data.root_lin_vel_w[0,:2]).item())
        report.update(navigation_state=dict(response.state),final_position_m=list(pose.position_m),
            final_xy_error_m=distance,final_support_height_error_m=support_error,final_speed_m_s=speed,
            upright_cosine=upright,zero_command_applied=response.private_evidence.get("zero_command_applied"),
            physical_pass=(response.state["status"]=="succeeded" and distance<=args.node_radius+.05
                           and support_error<=.20 and speed<.12 and upright>.8
                           and "settle_error" not in report and "motion_failure" not in report))
        report["inputs_unchanged"]=(adapter.source_digests(source)==document.source and
            all(hashlib.sha256(Path(path).read_bytes()).hexdigest()==digest for path,digest in runtime_inputs.items()))
        report["physical_pass"]=report["physical_pass"] and report["inputs_unchanged"]
        if not args.headless:
            capture_frame(app,output/"final.png", simulation=env.sim)
            set_camera_view(eye=[pose.position_m[0]-1.8,pose.position_m[1]-2.2,pose.position_m[2]+1.3],
                            target=list(pose.position_m))
            capture_frame(app,output/"final-detail.png", simulation=env.sim)
        (output/"result.json").write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding="utf-8")
        print("TOPOLOGY_NATIVE_RESULT",json.dumps({k:v for k,v in report.items() if k not in {"samples","route","execution_evidence"}}),flush=True)
        if args.hold_gui and not args.headless:
            while app.is_running():
                env.step({"go2_1":zero})
    return 0 if report["physical_pass"] else 1


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene-key",required=True)
    parser.add_argument("--asset-root",type=Path,required=True,help="Directory containing scene.json")
    parser.add_argument("--topology",type=Path,required=True)
    parser.add_argument("--origin",required=True)
    parser.add_argument("--destination",required=True)
    parser.add_argument("--output-root",type=Path,required=True,help="New directory for trace/images")
    parser.add_argument("--controller",choices=("GO2_VELOCITY_RSL_CFG","GO2_MIXED_STAIRS_CFG"),default="GO2_VELOCITY_RSL_CFG")
    parser.add_argument("--device",default="cuda:0")
    parser.add_argument("--headless",action="store_true")
    parser.add_argument("--hold-gui",action="store_true")
    parser.add_argument("--max-steps",type=int,default=5000)
    parser.add_argument("--timeout",type=float,default=90.0)
    parser.add_argument("--speed",type=float,default=.5)
    parser.add_argument("--node-radius",type=float,default=.25)
    parser.add_argument("--spawn-clearance",type=float,default=.5)
    parser.add_argument("--ray-origin-offset",type=float,default=.25)
    args=parser.parse_args(argv)
    for name in ("max_steps","timeout","speed","node_radius","spawn_clearance"):
        if not math.isfinite(getattr(args,name)) or getattr(args,name)<=0:
            parser.error(f"{name} must be positive and finite")
    if not math.isfinite(args.ray_origin_offset) or args.ray_origin_offset < 0:
        parser.error("ray origin offset must be finite and nonnegative")
    return run(args)


if __name__=="__main__":
    raise SystemExit(main())
