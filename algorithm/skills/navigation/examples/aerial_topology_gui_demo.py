"""Show authored XYZ node navigation with an Iris and native Isaac Sim physics.

Run from the repository root in env_isaaclab. Use --help without Isaac Sim;
importing this module never opens an application or creates output files.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
import json
import math
from pathlib import Path
import sys
import time

REPO_ROOT = Path(__file__).resolve().parents[4]
NODE_POSITIONS = ((-1., 0., 1.), (-1., 0., 2.5), (2., 0., 2.5),
                  (2., 2., 3.5), (-1., 2., 3.5), (-1., 2., 1.5))
TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}


def build_topology():
    """A fixed, manually authored route; geometry is never inspected."""
    nodes = []
    for index, position in enumerate(NODE_POSITIONS):
        refs = ["place:start"] if index == 0 else ["place:finish"] if index == 5 else []
        nodes.append({"id": f"n{index}", "position_xyz": list(position), "target_refs": refs})
    return {"schema": "eai.navigation.topology", "version": 2, "domain": "aerial", "levels": [], "scene_key": "plane",
            "coordinate_frame": "scene_world", "meters_per_unit": 1., "up_axis": "Z",
            "nodes": nodes,
            "edges": [{"id": f"e{index}", "from": f"n{index}", "to": f"n{index + 1}",
                       "directed": False, "polyline_xyz": [list(p) for p in NODE_POSITIONS[index:index + 2]]}
                      for index in range(5)]}


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", default="cuda:0", help="Isaac Lab physics device")
    parser.add_argument("--output-dir", type=Path, default=REPO_ROOT / "tmp/aerial-topology-gui")
    parser.add_argument("--frames", type=int, default=0, help="0 keeps the GUI open; otherwise exit after this many control frames")
    parser.add_argument("--no-repeat", action="store_true", help="Hold after one route instead of alternating directions")
    return parser


def _configure(env_cfg):
    env_cfg.sim.use_fabric = True
    env_cfg.episode_length_s = 1_000_000


def _write_json(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def run(args):
    # Prefer this checkout over an editable install belonging to another
    # worktree. Native imports follow the single shared launcher lifecycle.
    for path in (REPO_ROOT, REPO_ROOT / "source/EAI", REPO_ROOT / "source/EAI_assets",
                 REPO_ROOT / "source/EAI_hmrs"):
        sys.path.insert(0, str(path))
    from simulator import SimulatorLaunchConfig, open_simulator_session
    from algorithm.skills.contracts import SkillRequest
    from algorithm.skills.navigation import AerialNavigationProvider, AerialPose

    output = args.output_dir.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    topology = build_topology()
    _write_json(output / "topology.json", topology)
    selection = json.loads((REPO_ROOT / "source/EAI_hmrs/EAI_hmrs/envs/pegasus_drones.json").read_text())
    selection["robots"][0]["attachments"] = []
    config = SimulatorLaunchConfig(
        env="pegasus_drones", resolved_env_name="pegasus_drones", selection_data=selection,
        headless=False, device=args.device, enable_ros_bridge_extension=False,
        disable_orsus_ros_env=True, aerial_sensor_backend="disabled", env_cfg_hook=_configure,
        app_launcher_args={"kit_args": "--/app/window/title=AerialTopologyNavigation"},
    )
    with open_simulator_session(config) as session:
        import torch
        import omni.ui as ui
        import omni.usd
        from omni.kit.viewport.utility import get_active_viewport, capture_viewport_to_file
        from pxr import Gf, Usd, UsdGeom
        from EAI.physics.aerial_pose_reader import read_aerial_pose

        agent = session.base_env.possible_agents[0]
        actions = {agent: torch.zeros((1, 3), dtype=torch.float32, device=session.device)}
        last_goal = None

        def measured_pose():
            return AerialPose(read_aerial_pose(session.base_env, agent).position_m)

        def send_goal(goal):
            nonlocal last_goal
            last_goal = list(goal)
            tensor = torch.tensor([last_goal], dtype=torch.float32, device=session.device)
            session.base_env.set_command(agent, "goal_position", tensor)

        def send_response(response):
            for command in response.plan.commands:
                send_goal(command.payload["target_pose"])

        def step():
            before = time.perf_counter()
            session.env.step(actions)
            time.sleep(max(0., session.base_env.step_dt - (time.perf_counter() - before)))

        send_goal(measured_pose().position_m)
        for _ in range(100):
            if not session.simulation_app.is_running():
                return
            step()
        stage = omni.usd.get_context().get_stage()
        markers = []
        with Usd.EditContext(stage, stage.GetSessionLayer()):
            for index, point in enumerate(NODE_POSITIONS):
                marker = UsdGeom.Sphere.Define(stage, f"/World/AerialNodeDemo/Nodes/n{index}")
                marker.CreateRadiusAttr(.10)
                marker.AddTranslateOp().Set(Gf.Vec3d(*point))
                marker.CreateDisplayColorAttr([Gf.Vec3f(.05, .65, 1.)])
                markers.append(marker)
            edges = UsdGeom.BasisCurves.Define(stage, "/World/AerialNodeDemo/Connections")
            edges.CreateTypeAttr(UsdGeom.Tokens.linear)
            edges.CreateCurveVertexCountsAttr([2] * 5)
            edges.CreatePointsAttr([Gf.Vec3f(*p) for i in range(5) for p in NODE_POSITIONS[i:i + 2]])
            edges.CreateWidthsAttr([.025])
            edges.CreateDisplayColorAttr([Gf.Vec3f(.05, .65, 1.)])
            trace = UsdGeom.BasisCurves.Define(stage, "/World/AerialNodeDemo/ActualFlightTrace")
            trace.CreateTypeAttr(UsdGeom.Tokens.linear)
            trace.CreateWidthsAttr([.018])
            trace.CreateDisplayColorAttr([Gf.Vec3f(1., .72, .08)])
            camera = UsdGeom.Camera.Define(stage, "/World/AerialNodeDemoCamera")
            camera.CreateFocalLengthAttr(24.)
            camera.CreateClippingRangeAttr(Gf.Vec2f(.05, 1000.))
            camera.AddTransformOp().Set(Gf.Matrix4d().SetLookAt(
                Gf.Vec3d(9., -11., 8.), Gf.Vec3d(.5, 1., 2.), Gf.Vec3d(0., 0., 1.)).GetInverse())
        viewport = get_active_viewport()
        if viewport is None:
            raise RuntimeError("GUI viewport is unavailable")
        viewport.set_active_camera(str(camera.GetPath()))
        window = ui.Window("Aerial node navigation", width=360, height=410)
        pending = ["start"]
        with window.frame:
            with ui.VStack(spacing=8):
                ui.Label("Iris | native physics | XYZ nodes", height=24)
                ui.Label("Blue: topology / Yellow: actual flight\nOrange: next node / Green: visited", height=40)
                repeat = ui.SimpleBoolModel(not args.no_repeat)
                with ui.HStack(height=24):
                    ui.CheckBox(repeat, width=24)
                    ui.Label("Repeat forward / backward")
                for pair in (("Start / Replay", "Hold"), ("Continue", "Replan")):
                    with ui.HStack(height=30, spacing=8):
                        for label in pair:
                            intent = {"Start / Replay": "start", "Hold": "hold", "Continue": "continue", "Replan": "replan"}[label]
                            ui.Button(label, clicked_fn=lambda value=intent: pending.append(value))
                ui.Button("Stop", height=28, clicked_fn=lambda: pending.append("stop"))
                status_label = ui.Label("Starting...", word_wrap=True, height=40)
                pose_label = ui.Label("", height=24)
                route_label = ui.Label("", word_wrap=True, height=40)

        provider = AerialNavigationProvider()
        request = response = None
        target_ref, control_intent = "place:finish", "continue"
        task_count = frame = 0
        elapsed = control_elapsed = 0.
        next_auto = last_node_index = None
        recorded_tasks = set()
        last_saved = -1.
        samples, outcomes, capture_tasks = [], [], []

        def terminal():
            return response is not None and response.state["status"] in TERMINAL_STATUSES

        def observation():
            return {"valid": True, "fresh": True,
                    "world_state_seq": int(session.base_env.sim.current_time_step_index),
                    "simulation_time_s": float(session.base_env.sim.current_time),
                    "pose": measured_pose(), "topology": topology}

        def save_evidence():
            _write_json(output / "evidence.json", {
                "profile": "diagnostic_gui", "source": "isaac_sim_native", "agent": agent,
                "nodes": NODE_POSITIONS, "frames": frame, "sim_elapsed_s": elapsed,
                "navigation_state": dict(response.state) if response else None,
                "private_evidence": response.private_evidence if response else {},
                "samples": samples, "completed_runs": outcomes,
            })

        print("AERIAL_GUI_READY " + json.dumps({"agent": agent, "output": str(output)}), flush=True)
        try:
            while session.simulation_app.is_running() and (not args.frames or frame < args.frames):
                if next_auto is not None and elapsed >= next_auto and repeat.as_bool:
                    target_ref = "place:start" if target_ref == "place:finish" else "place:finish"
                    pending.append("start")
                    next_auto = None
                if pending or control_elapsed >= .1:
                    intent = pending.pop(0) if pending else control_intent
                    dt = max(control_elapsed, session.base_env.step_dt)
                    control_elapsed = 0.
                    obs = observation()
                    params = {"intent": intent, "world_state_seq": obs["world_state_seq"]}
                    if intent in {"hold", "stop"}:
                        next_auto = None
                        if intent == "stop":
                            repeat.set_value(False)
                    if intent == "start":
                        if request is not None and not terminal():
                            send_response(provider.control(replace(request, parameters={**params, "intent": "stop"}), observation=obs))
                        task_count += 1
                        request = SkillRequest(f"gui-{task_count}", provider.skill_id, agent,
                                               task_id=f"gui-{task_count}", target_ref=target_ref,
                                               parameters=params, timing={"duration_s": 120.})
                        send_response(provider.dispatch(request, observation=obs))
                        response = provider.control(request, observation=obs, dt_s=dt)
                        control_intent, next_auto = "continue", None
                        samples, last_node_index = [], None
                        trace.GetCurveVertexCountsAttr().Set([])
                        trace.GetPointsAttr().Set([])
                    elif request is not None and not terminal():
                        request = replace(request, parameters=params)
                        response = provider.control(request, observation=obs, dt_s=dt)
                        control_intent = "hold" if intent == "hold" else "continue"
                    if response is not None and not terminal():
                        send_response(response)
                    elif response is not None and task_count not in recorded_tasks:
                        send_response(response)
                    native = {"physics_step": obs["world_state_seq"], "simulation_time_s": obs["simulation_time_s"],
                              "position_m": list(obs["pose"].position_m), "command": last_goal}
                    if response is not None:
                        node_index = response.private_evidence["node_index"]
                        route = response.private_evidence.get("route_selection", {}).get("topology_node_ids", [])
                        native["node_index"] = node_index
                        samples.append(native)
                        samples = samples[-1200:]
                        if len(samples) >= 2:
                            trace.GetCurveVertexCountsAttr().Set([len(samples)])
                            trace.GetPointsAttr().Set([Gf.Vec3f(*row["position_m"]) for row in samples])
                        # Match the layer holding the initial colors; root-layer
                        # edits would be hidden by those session-layer opinions.
                        with Usd.EditContext(stage, stage.GetSessionLayer()):
                            for index, marker in enumerate(markers):
                                node_id = f"n{index}"
                                color = (.1, .9, .25) if node_id in route[:node_index] else (.05, .65, 1.)
                                if node_index < len(route) and node_id == route[node_index]:
                                    color = (1., .25, .05)
                                marker.GetDisplayColorAttr().Set([Gf.Vec3f(*color)])
                        status_label.text = f"{response.state['execution_state']} | {response.state['reason_code']}\nTask {task_count} | node {node_index}/{len(route)}"
                        pose_label.text = "XYZ: " + ", ".join(f"{value:.2f}" for value in native["position_m"])
                        route_label.text = "Route: " + " > ".join(route)
                        if node_index != last_node_index:
                            print("AERIAL_GUI_NODE " + json.dumps(native), flush=True)
                            last_node_index = node_index
                        if terminal() and task_count not in recorded_tasks:
                            recorded_tasks.add(task_count)
                            outcome = {"task": task_count, "status": response.state["status"], "route": route,
                                       "node_index": node_index, "position_m": native["position_m"], "hold_command": last_goal,
                                       "distance_to_target_m": math.dist(native["position_m"], NODE_POSITIONS[0 if target_ref == "place:start" else -1]),
                                       "terminal_hold_error_m": math.dist(last_goal, native["position_m"]),
                                       "samples": list(samples)}
                            outcomes.append(outcome)
                            outcomes = outcomes[-10:]
                            print("AERIAL_GUI_TERMINAL " + json.dumps({key: value for key, value in outcome.items() if key != "samples"}), flush=True)
                            if response.state["status"] == "succeeded":
                                next_auto = elapsed + 4.
                                if len(outcomes) == 1:
                                    capture_tasks.append(capture_viewport_to_file(viewport, str(output / "viewport.png")))
                        if elapsed - last_saved >= 1.:
                            save_evidence()
                            last_saved = elapsed
                step()
                frame += 1
                elapsed += session.base_env.step_dt
                control_elapsed += session.base_env.step_dt
        except Exception as exc:
            if request is not None and not terminal():
                response = provider.fail(request.task_id, exc, hover_position=measured_pose().position_m)
                send_response(response)
            raise
        finally:
            if request is not None and not terminal():
                obs = observation()
                response = provider.control(replace(request, parameters={"intent": "stop", "world_state_seq": obs["world_state_seq"]}), observation=obs)
                send_response(response)
            save_evidence()
            window.destroy()
    return 0


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.frames < 0:
        parser.error("--frames must be nonnegative")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
