"""City demo: compose before PhysX reset, then update existing prims only."""
from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from functools import partial
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from simulator import SimulatorLaunchConfig, open_simulator_session
from demo.city_benchmark.scene.flow import FIRE_USD, SMOKE_USD, FLOW_KIT_PRELOAD_ARGS
from demo.city_benchmark.scene.runtime import CityEaiRuntime, CityRuntimeConfig
from demo.city_benchmark.scene.usd_scene import CITY_STATIC_USD, compose_city


def _spawn_city_usd(prim_path, cfg, translation=None, orientation=None, *, seed=7, use_flow=True, target_path=None, people=4, **kwargs):
    from isaaclab.sim.spawners.from_files.from_files import spawn_from_usd
    from isaaclab.sim.utils import get_current_stage
    from demo.city_benchmark.scene.prebuilt import publish
    from demo.city_benchmark.scene.usd_scene import CitySceneAdapter
    from demo.city_benchmark.scene.flow import FlowPairAdapter

    print(f"[city-demo] prebuild callback invoked at {prim_path}", flush=True)
    try:
        prim = spawn_from_usd(prim_path, cfg, translation, orientation, **kwargs)
        stage = get_current_stage()
        compose_city(stage, prim_path)
        scene = CitySceneAdapter(stage, city_root_path=prim_path, config=CityRuntimeConfig(
            seed=seed, target_path=target_path, use_flow=use_flow,
            validate_obstacles=False,
        )).prepare(target_path)
        scene.spawn_traffic()
        from demo.city_benchmark.scene.people import spawn_background_people
        from demo.city_benchmark.scene.usd_scene import CITY_SCENARIO_JSON
        routes = json.loads(CITY_SCENARIO_JSON.read_text(encoding="utf-8"))["pedestrian_routes"]
        scene.people_runtime, scene.people = spawn_background_people(stage, scene, routes, people)
        flow = FlowPairAdapter(stage) if use_flow else None
        if flow is not None:
            flow.compose()
        publish((stage, scene, flow))
        CityEaiRuntime(stage).reset()
        print(f"[city-demo] prebuilt target={scene.target.path}, vehicles={len(scene.traffic) + 1}", flush=True)
        return prim
    except BaseException:
        traceback.print_exc()
        sys.stderr.flush()
        raise


def build_selection(seed: int) -> dict:
    return {
        "version": 1, "scene_key": "modern_house_nvd", "task_name": "city_eai_demo",
        "robots": [
            {"type": "go2", "controller": {"mode": "default", "cfg": "GO2_VELOCITY_RSL_CFG"}, "spawn_pose": {"position": [-69.0, 49.0, 0.62], "rotation": [1, 0, 0, 0]}, "attachments": []},
            {"type": "carter", "controller": {"mode": "default", "cfg": "CARTER_DIFF_CFG"}, "spawn_pose": {"position": [-65.0, 49.0, 0.22], "rotation": [1, 0, 0, 0]}, "attachments": []},
            {"type": "iris", "controller": {"mode": "default", "cfg": "PEGASUS_IRIS_POSITION_CFG"}, "spawn_pose": {"position": [-61.0, 49.0, 3.0], "rotation": [1, 0, 0, 0]}, "attachments": []},
        ], "scenario_seed": int(seed),
    }


def configure_city_env_cfg(env_cfg, *, city_path: Path = CITY_STATIC_USD, seed=7, use_flow=True, target_path=None, people=4):
    import isaaclab.sim as sim_utils
    # Fabric keeps GPU PhysX robot poses synchronized with the renderer.
    env_cfg.sim.use_fabric = True
    if use_flow:
        # SimulationContext applies the default "balanced" rendering preset
        # after command-line settings, forcibly disabling translucency,
        # reflections and indirect diffuse. Flow's ray-marched volumes
        # composite through those RTX features, so without restoring them the
        # accident still collides but fire and smoke never reach the viewport.
        # RenderCfg values are applied after the preset, so they win.
        env_cfg.sim.render.enable_translucency = True
        env_cfg.sim.render.enable_reflections = True
        env_cfg.sim.render.enable_global_illumination = True
    world = env_cfg.scene.world
    world.prim_path = "/World/City"
    world.spawn = sim_utils.UsdFileCfg(
        usd_path=str(city_path.resolve()),
        func=partial(_spawn_city_usd, seed=seed, use_flow=use_flow, target_path=target_path, people=people),
    )
    world.init_state.pos = (0.0, 0.0, 0.0)


def _zero_actions(session):
    import torch
    actions = {}
    for agent in session.possible_agents:
        cfg = session.env_cfg.controllers.get(agent)
        command_name = getattr(cfg, "command_name", None)
        if command_name == "goal_position":
            articulation = session.base_env.scene.articulations.get(agent)
            if articulation is not None:
                goal = articulation.data.root_pos_w[0].detach().clone().unsqueeze(0)
                session.base_env.set_command(agent, "goal_position", goal)
        actions[agent] = torch.zeros((session.num_envs, 3), device=session.device)
    return actions


def _save_report(path, report):
    if path:
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2), encoding="utf-8")


def run(args):
    from demo.city_benchmark.scene.prebuilt import clear
    clear()
    frame_limit = args.frames if args.frames is not None else (1000 if args.headless else 0)
    if args.headless and frame_limit <= 0:
        raise ValueError("Headless runs require --frames greater than zero")
    selection = build_selection(args.seed)
    device = args.device
    launcher_args = {"kit_args": FLOW_KIT_PRELOAD_ARGS} if args.flow else {}
    if args.experience:
        launcher_args["experience"] = args.experience
    config = SimulatorLaunchConfig(
        env="city_eai_demo", resolved_env_name="EAI-Interactive-v0", selection_data=selection,
        num_envs=1, device=device, seed=args.seed, headless=args.headless,
        enable_ros_bridge_extension=False, disable_orsus_ros_env=True,
        app_launcher_args=launcher_args,
        env_cfg_hook=partial(configure_city_env_cfg, seed=args.seed, use_flow=args.flow, target_path=args.target_path, people=args.people),
    )
    import hashlib
    from demo.city_benchmark.scene.usd_scene import VEHICLE_ROOT, SUPPORTED_VEHICLES
    sources = [CITY_STATIC_USD, *(VEHICLE_ROOT / name for name, _ in SUPPORTED_VEHICLES)]
    if args.flow:
        sources += [FIRE_USD, SMOKE_USD]
    hashes_before = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in sources}
    report = {"selection": selection, "frames": 0, "seed": args.seed, "status": "starting"}
    _save_report(args.report, report)
    with open_simulator_session(config) as session:
        city = None
        controls = None
        instance_ids = None
        try:
            print("[city-demo] EAI session ready; binding runtime", flush=True)
            city = CityEaiRuntime.from_current_stage(config=CityRuntimeConfig(seed=args.seed, use_flow=args.flow))
            print("[city-demo] runtime bound; building commands", flush=True)
            if getattr(city, "flow", None) is not None:
                from demo.city_benchmark.scene.flow import verify_live_flow
                verify_live_flow(city.stage, city.flow.paths)
            actions = _zero_actions(session)
            dt = float(session.base_env.step_dt)
            city.reset()
            viewport = None
            capture_dir = getattr(args, "capture_dir", None)
            if not args.headless:
                from demo.city_benchmark.scene.presentation import configure_view, capture_viewport
                viewport = configure_view(city.stage, city.scene.plan)
                if capture_dir:
                    from demo.city_benchmark.scene.presentation import attach_instance_ids
                    instance_ids = attach_instance_ids(viewport)
                for _ in range(getattr(args, "warmup_frames", 90)):
                    session.env.step(actions)
                city.reset()
                viewport = configure_view(city.stage, city.scene.plan)
                from demo.city_benchmark.scene.presentation import CityDemoControls
                controls = CityDemoControls(city)
                controls.paused = getattr(args, "start_paused", False)
                print("[city-demo] GUI ready. Replay / Pause / Focus accident controls are available. Close the window to exit.", flush=True)
            physics_audit = {"steps": 0}
            physics_subscription = None
            if capture_dir and viewport is not None:
                import omni.physx
                def count_physics_step(step_dt):
                    physics_audit["steps"] += 1
                physics_subscription = omni.physx.get_physx_interface().subscribe_physics_step_events(count_physics_step)
            flow_clock = None
            if getattr(city, "flow", None) is not None:
                from demo.city_benchmark.scene.flow import FlowTimelineClock
                flow_clock = FlowTimelineClock(dt)
            captures = []
            capture_times = [1.0, 3.0, city.scene.plan.impact_time + .4, 14.0] if viewport is not None and capture_dir else []
            frame = 0
            while frame_limit <= 0 or frame < frame_limit:
                tick_start = time.perf_counter()
                if not session.simulation_app.is_running():
                    if frame_limit > 0:
                        raise RuntimeError(f"Application closed before frame {frame}")
                    break
                if controls is not None:
                    if controls.replay_requested or (getattr(args, "loop", False) and city.elapsed >= 20.0):
                        city.reset()
                        controls.replay_requested = False
                    controls.update()
                    if controls.paused:
                        session.base_env.sim.render()
                        time.sleep(.01)
                        continue
                if flow_clock is not None:
                    city.flow.set_ticking(True)
                    flow_clock.advance(dt)
                try:
                    session.env.step(actions)
                finally:
                    if flow_clock is not None:
                        city.flow.set_ticking(False)
                city.step(dt=dt)
                frame += 1
                report["frames"] = frame
                if viewport is not None and capture_dir and capture_times and city.elapsed >= capture_times[0]:
                    capture_times.pop(0)
                    path = Path(capture_dir) / f"{len(captures):02d}_{city.phase}.png"
                    viewport = configure_view(city.stage, city.scene.plan)
                    for _ in range(8):
                        session.base_env.sim.render()
                    from demo.city_benchmark.scene.presentation import viewport_witness, visible_vehicle_pixels
                    witness = viewport_witness(city.stage, viewport)
                    witness["phase"] = city.phase
                    witness["elapsed"] = city.elapsed
                    witness["visible_vehicle_pixels"] = visible_vehicle_pixels(instance_ids)
                    report.setdefault("viewport_witnesses", []).append(witness)
                    witness["sim_time"] = session.base_env.sim.current_time
                    witness["physics_steps"] = physics_audit["steps"]
                    witness["expected_physics_steps"] = frame * int(session.base_env.cfg.decimation)
                    captures.append(capture_viewport(session.simulation_app, viewport, path, render=session.base_env.sim.render))
                    witness["sim_time_after_capture"] = session.base_env.sim.current_time
                if controls is not None:
                    time.sleep(max(0.0, dt - (time.perf_counter() - tick_start)))
            if captures and session.simulation_app.is_running():
                from demo.city_benchmark.scene.presentation import capture_vehicle_views
                report["vehicle_views"] = capture_vehicle_views(city.stage, viewport, session.simulation_app,
                    session.base_env.sim.render, instance_ids, capture_dir)
                configure_view(city.stage, city.scene.plan)
            report.update({
                "captures": captures,
                "status": "completed", "dt": dt, "target_building": city.scene.target.path,
                "lane_id": city.scene.lane_id, "impact_time": city.scene.plan.impact_time,
                "events": city.events, "traffic_count": len(city.traffic) + 1,
                "background_people": getattr(city.scene, "people", []),
                "people_count": len(getattr(city.scene, "people", [])),
                "people_final_positions": {actor_id: city.people.position(actor_id) for actor_id in city.people.actor_ids} if getattr(city, "people", None) else {},
                "physics_device": device,
                "flow_effect_count": len(city.effects),
                "flow": city.flow.readback_report() if city.flow else None,
                "final_state": vars(city.scene.plan.sample(city.elapsed)),
                "building_candidates": len(city.scene.buildings),
                "disabled_city_rigid_bodies": city.scene.disabled_rigid_bodies,
                "obstacle_validation": city.scene.config.validate_obstacles,
                "source_hashes": hashes_before,
                "sources_unchanged": all(hashlib.sha256(path.read_bytes()).hexdigest() == hashes_before[str(path)] for path in sources),
                "robots": {name: robot.data.root_pos_w.detach().cpu().tolist()
                           for name, robot in session.base_env.scene.articulations.items()},
            })
            for witness in report.get("viewport_witnesses", []):
                if witness["physics_steps"] != witness["expected_physics_steps"]:
                    raise RuntimeError("GUI rendering advanced PhysX outside EAI steps")
            if city.flow and city.elapsed >= 3.0 and len(captures) >= 2:
                if not any(w["flow_active_blocks"] > 0 for w in report.get("viewport_witnesses", [])):
                    raise RuntimeError("Flow nodes loaded but no live GPU volume was generated")
            _save_report(args.report, report)
            print(json.dumps(report, indent=2), flush=True)
        except BaseException as exc:
            report.update(status="failed", error=repr(exc), traceback=traceback.format_exc())
            _save_report(args.report, report)
            traceback.print_exc()
            sys.stderr.flush()
            raise
        finally:
            if instance_ids is not None:
                instance_ids.detach()
            if controls is not None:
                controls.close()
            if city is not None:
                city.close()
            clear()
    return report


def build_parser():
    parser = argparse.ArgumentParser(description="EAI city accident with Go2, Carter and Iris")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--frames", type=int, default=None, help="0 keeps GUI open; defaults to 0 in GUI, 1000 headless")
    parser.add_argument("--loop", action="store_true", help="Replay the accident every 20 simulated seconds")
    parser.add_argument("--start-paused", action="store_true", help="Wait for Pause / Resume in the GUI before starting")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--target-path", default=None)
    parser.add_argument("--people", type=int, choices=range(9), default=4,
                        help="Walking background people (0 disables, default 4; up to available safe paths)")
    parser.add_argument("--no-flow", dest="flow", action="store_false")
    parser.add_argument("--capture-dir", default=None, help="Save four accident-phase GUI viewport images")
    parser.add_argument("--warmup-frames", type=int, default=90, help="GUI render warmup before the accident starts")
    parser.add_argument("--report", default="tmp/city_eai_demo/scenario_report.json")
    parser.add_argument("--experience", default="",
                        help="Override the Isaac Lab experience file, e.g. isaaclab.python.kit")
    parser.set_defaults(flow=True)
    return parser


if __name__ == "__main__":
    run(build_parser().parse_args())
