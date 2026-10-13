"""Bounded native aerial diagnostics on a temporary plane fixture.

No formal task, evaluator verdict, saved environment or scene asset is created.
Run in env_isaaclab; GUI is the default. Artifacts belong under tmp/.
"""
from __future__ import annotations
import argparse
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
# Iris body mesh's lower surface at local x=y=0 (ray/triangle measured).
# This profile is a physical underside point, not a virtual hanging offset.
IRIS_CONTACT_ANCHOR = (0., 0., -.04090352992632196)


def run(args):
    for path in (ROOT, ROOT / "source/EAI", ROOT / "source/EAI_assets", ROOT / "source/EAI_hmrs"):
        sys.path.insert(0, str(path))
    from simulator import SimulatorLaunchConfig, open_simulator_session
    from algorithm.skills.contracts import SkillRequest
    from algorithm.skills.navigation.aerial_topology import FrozenAerialTopology
    from algorithm.skills.navigation.landmarks import resolve_landmark_route
    from algorithm.common_coordination.contract import Assignment, CoordinationTask, TaskGraph, RobotCapability
    from task.contracts import EaiTaskPackage, RobotState, TaskRunRequest
    from task.resources import ResourceManager
    from task.executors.aerial_runtime import AerialSkillRuntime
    from task.executors.cooperative import CooperativeBatchExecutor
    from benchmark.aerial_adapter import AerialTaskExecutor, AerialLifecycleSkillAdapter
    from tools.topology_authoring.model import TopologyDocument
    from tools.topology_authoring.product import generate_product, load_product

    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    selection = {"version": 1, "scene_key": "plane", "task_name": "aerial_skill_diagnostic", "robots": []}
    count = 2 if args.mode == "navigation" else 1
    for index in range(count):
        selection["robots"].append({"type": "iris", "controller": {"cfg": "PEGASUS_IRIS_POSITION_CFG", "mode": "default"},
            "attachments": [], "spawn_pose": {"position": [0., index * 1.6, 1.3], "rotation": [1., 0., 0., 0.]}})

    def configure(cfg):
        from isaaclab.assets import AssetBaseCfg, RigidObjectCfg
        from isaaclab import sim as sim_utils
        cfg.sim.use_fabric = True
        cfg.episode_length_s = 100000
        cfg.scene.payload = RigidObjectCfg(prim_path="{ENV_REGEX_NS}/payload",
            spawn=sim_utils.CuboidCfg(size=(.10, .10, .25), rigid_props=sim_utils.RigidBodyPropertiesCfg(),
                mass_props=sim_utils.MassPropertiesCfg(mass=.005), collision_props=sim_utils.CollisionPropertiesCfg(),
                visual_material=sim_utils.PreviewSurfaceCfg(diffuse_color=(.65, .35, .12))),
            init_state=RigidObjectCfg.InitialStateCfg(pos=(0., 0., .675)))
        for name, x in (("pick_stand", 0.), ("place_stand", .6)):
            setattr(cfg.scene, name, AssetBaseCfg(prim_path="{ENV_REGEX_NS}/" + name,
                spawn=sim_utils.CuboidCfg(size=(.40, .40, .55), collision_props=sim_utils.CollisionPropertiesCfg()),
                init_state=AssetBaseCfg.InitialStateCfg(pos=(x, 0., .275))))

    config = SimulatorLaunchConfig(env="aerial_skill_diagnostic", resolved_env_name="aerial_skill_diagnostic",
        selection_data=selection, device=args.device, headless=args.headless,
        enable_ros_bridge_extension=False, disable_orsus_ros_env=True, aerial_sensor_backend="disabled",
        app_launcher_args={"headless": args.headless, "device": args.device, "fast_shutdown": False},
        env_cfg_hook=configure)
    result = {"mode": args.mode, "profile": "measured_diagnostic", "physical_outcome": "unknown", "external_evaluator": None}
    with open_simulator_session(config) as session:
        import torch
        import omni.usd
        from pxr import Gf, UsdGeom, UsdPhysics, Usd
        from task.executors.aerial_pick_place import read_aerial_pose
        from tools.topology_authoring.deploy import capture_frame
        env = session.base_env
        robots = tuple(env.possible_agents)
        neutral = {r: torch.zeros((1, 3), device=session.device) for r in robots}
        for i, robot in enumerate(robots):
            env.set_command(robot, "goal_position", torch.tensor([[0., i * 1.6, 1.3]], device=session.device))
        for _ in range(200):
            session.env.step(neutral)

        doc = TopologyDocument("plane", domain="aerial")
        for index in range(count):
            y = index * 1.6
            points = [(0., y, 1.3), (0., y, 1.8), (.6, y, 1.3)] if args.mode == "navigation" else [(0., y, 1.3), (.6, y, 1.3)]
            ids = []
            for k, position in enumerate(points):
                node = f"r{index + 1}_n{k}"
                doc.add_node(position, node_id=node, label=f"loading_{index + 1}" if k == 0 else f"destination_{index + 1}" if k == len(points)-1 else "")
                ids.append(node)
            for a, b in zip(ids, ids[1:]):
                doc.add_edge(a, b, [doc.node(a)["position_xyz"], doc.node(b)["position_xyz"]], directed=True)
        path = generate_product(output / "topology.json", doc)
        topology = FrozenAerialTopology(load_product(path).to_dict(), scene_key="plane", source_digests={})
        resources = ResourceManager()
        runtime = AerialSkillRuntime(env, topology=topology, source_reader=lambda: ("plane", {}), resources=resources,
            transport_options={"drone_anchor_local_m": IRIS_CONTACT_ANCHOR,
                "connection_tolerance_m": .002, "timeout_s": 120.})
        stage = omni.usd.get_context().get_stage()
        viewport = None
        editor = None
        if not args.headless:
            import omni.ui as ui
            for window in ui.Workspace.get_windows():
                if window.title == "Simulation Settings":
                    window.visible = False
            from omni.kit.viewport.utility import get_active_viewport
            viewport = get_active_viewport()
            camera = UsdGeom.Camera.Define(stage, "/World/AerialDiagnosticCamera")
            camera.CreateClippingRangeAttr(Gf.Vec2f(.01, 1000))
            camera.CreateFocalLengthAttr(32.)
            camera.AddTransformOp().Set(Gf.Matrix4d().SetLookAt(Gf.Vec3d(3.8, -5., 3.0), Gf.Vec3d(.3, .45, .95), Gf.Vec3d(0, 0, 1)).GetInverse())
            viewport.set_active_camera(str(camera.GetPath()))
            for row in doc.nodes:
                marker = UsdGeom.Sphere.Define(stage, "/World/AerialDiagnosticNodes/" + row["id"])
                marker.CreateRadiusAttr(.04)
                marker.AddTranslateOp().Set(Gf.Vec3d(*row["position_xyz"]))
                marker.CreateDisplayColorAttr([Gf.Vec3f(.1, .7, 1.)])
            if args.editor:
                from tools.topology_authoring.ui import TopologyAuthoringWindow
                from tools.topology_authoring.adapters.empty import EmptyTopologyAdapter
                from tools.topology_authoring.adapter import SceneSource
                adapter = EmptyTopologyAdapter("plane")
                editor = TopologyAuthoringWindow(doc, adapter, SceneSource("plane", output, {"navigation_domain": "aerial"}), output)
                editor._route_origin_model.set_value("loading_1")
                editor._route_destination_model.set_value("destination_1")
                editor.preview_route()
                assert editor._route_preview["admitted"]
                editor.save_draft()
                editor.load_draft()
                editor.preview_route()
                assert editor.export_route_preview() is not None
                editor.generate()
                capture_frame(session.simulation_app, output / "editor.png", simulation=env.sim)
                editor.window.visible = False

        samples, phases = [], []
        executor = None
        def record(phase):
            samples.append({"time_s": float(env.sim.current_time), "phase": phase,
                "robots": {r: list(read_aerial_pose(env, r).position_m) for r in robots},
                "payload": env.scene["payload"].data.root_pos_w[0].cpu().tolist()})
            if phase not in phases:
                phases.append(phase)
                print("AERIAL_PHASE " + json.dumps(samples[-1]), flush=True)
                if viewport:
                    capture_frame(session.simulation_app, output / f"{len(phases):02d}-{phase}.png", simulation=env.sim)
                    if phase == "attaching":
                        transform = camera.GetOrderedXformOps()[0]
                        previous = transform.Get()
                        transform.Set(Gf.Matrix4d().SetLookAt(Gf.Vec3d(1.1, -1.4, 1.0),
                            Gf.Vec3d(0, 0, .80), Gf.Vec3d(0, 0, 1)).GetInverse())
                        capture_frame(session.simulation_app, output / "contact-closeup.png", simulation=env.sim)
                        transform.Set(previous)
        record("initial")
        initial_tick = env.sim.current_time_step_index
        def step(actions):
            session.env.step({**neutral, **actions})
            handle = executor.last_handle if executor is not None else None
            phase = handle.prepared.response.state["phase"] if handle and handle.prepared is not None else "navigation"
            record(phase)

        try:
            if args.mode == "navigation":
                tasks = [CoordinationTask(f"fly-{i}", "Temporary native diagnostic") for i in range(count)]
                assignments = [Assignment(t.task_id, robots[i], "diagnostic") for i, t in enumerate(tasks)]
                requests = {}
                for i, assignment in enumerate(assignments):
                    route = resolve_landmark_route(doc, origin_ref=f"loading_{i+1}", destination_ref=f"destination_{i+1}")
                    requests[assignment.task_id] = SkillRequest("native/" + assignment.task_id, "eai.navigation.aerial_navigation", assignment.robot_id,
                        task_id=assignment.task_id, target_ref=route["target_ref"],
                        parameters={**route["skill_parameters"], "world_state_seq": initial_tick}, budget={"duration_s": 60.})
                batch = CooperativeBatchExecutor(lambda a, t, scene: runtime.binding(requests[a.task_id]),
                    step=lambda scene, actions: step(actions), neutral_actions=lambda scene: neutral,
                    resources=resources, max_ticks=args.max_ticks, max_cancel_ticks=300, expected_scene_id="plane")
                package = EaiTaskPackage("temporary-native-aerial", "plane", TaskGraph(tuple(tasks), "inline"),
                    tuple(RobotState(RobotCapability(r, frozenset())) for r in robots))
                batch.bind_run(TaskRunRequest(package))
                updates = batch.execute_batch(assignments, {t.task_id: t for t in tasks}, scene=session)
                result["updates"] = [u.dependency_outcome.value for u in updates]
                result["checks"] = {"control_cycles_completed": all(v == "completed" for v in result["updates"]),
                    "shared_resources_released": not resources.snapshot()["leases"],
                    "target_distances": all(math.dist(read_aerial_pose(env, r).position_m, (.6, i*1.6, 1.3)) < .31 for i, r in enumerate(robots))}
            else:
                robot = robots[0]
                payload_path = "/World/envs/env_0/payload"
                root = stage.GetPrimAtPath(payload_path)
                mass = UsdPhysics.MassAPI(root).GetMassAttr().Get()
                def collisions():
                    return {str(p.GetPath()): (UsdPhysics.CollisionAPI(p).GetCollisionEnabledAttr().Get(),
                        UsdPhysics.CollisionAPI(p).GetCollisionEnabledAttr().HasAuthoredValueOpinion()) for p in Usd.PrimRange(root) if p.HasAPI(UsdPhysics.CollisionAPI)}
                original_collisions = collisions()
                pick = env.scene["payload"].data.root_pos_w[0].cpu().tolist()
                pick[2] += .125
                req = SkillRequest("native/carry", "eai.transport.aerial_pick_place", robot, task_id="carry",
                    parameters={"world_state_seq": initial_tick, "object_ref": payload_path, "source_ref": "r1_n0", "destination_ref": "r1_n1",
                        "robot_ids": [robot], "carry_mode": "fixed_joint", "pick_anchor_world_m": pick, "place_anchor_world_m": [.6, 0., .8]})
                if args.mode == "cycle":
                    executor = AerialTaskExecutor(runtime, max_ticks=args.max_ticks)
                    feedback = executor.execute(Assignment("carry", robot, "diagnostic"), CoordinationTask("carry", "temporary fixture"),
                        scene=session, plan=AerialLifecycleSkillAdapter(runtime).plan(req), step=step)
                    result["feedback"] = {"outcome": feedback.outcome, "message": feedback.message}
                    result["transport_state"] = dict(executor.last_handle.response.state)
                    result["evidence"] = dict(executor.last_handle.response.private_evidence)
                    attachment_samples = [s for s in result["evidence"]["execution_samples"] if s["phase"] == "attaching"]
                    contact_gap = max((math.dist(s["drone_anchor_world_m"], s["object_anchor_world_m"]) for s in attachment_samples), default=None)
                    result["contact_gap_m"] = contact_gap
                    result["drone_anchor_local_m"] = IRIS_CONTACT_ANCHOR
                    for _ in range(100): session.env.step(neutral)
                    position = env.scene["payload"].data.root_pos_w[0].cpu().tolist()
                    max_payload_step = max(math.dist(a["payload"], b["payload"]) for a, b in zip(samples, samples[1:]))
                    result["max_payload_step_m"] = max_payload_step
                    result["checks"] = {"control_cycle_completed": feedback.outcome == "command_applied",
                        "payload_on_destination_stand": math.dist(position, [.6, 0., .675]) < .025,
                        "no_payload_jump": max_payload_step < .05,
                        "underside_contact_within_3mm": contact_gap is not None and contact_gap < .003,
                        "resources_released": not resources.snapshot()["leases"], "collision_restored": collisions() == original_collisions,
                        "mass_preserved": UsdPhysics.MassAPI(root).GetMassAttr().Get() == mass}
                else:
                    binding = runtime.binding(req)
                    lease = resources.acquire(req.request_id, ("robot:" + robot, *binding.resources))
                    handle = binding.start(runtime.plan(req), scene=session, lease=lease)
                    stopped = False
                    for _ in range(args.max_ticks):
                        if handle.response is not None and handle.response.state["phase"] == "navigating_to_place":
                            handle.cancel("native retained stop")
                            stopped = True
                        action = handle.command()
                        step({robot: action})
                        handle.after_step()
                        if handle.response.state["status"] != "pending": break
                    result["transport_state"] = dict(handle.response.state)
                    result["retained_resources"] = resources.snapshot()
                    record("retained_stop")
                    # Explicit host recovery owns these otherwise prohibited ticks.
                    # No new task may run while the retained scene is isolated.
                    before = read_aerial_pose(env, robot).position_m
                    for _ in range(150): session.env.step(neutral)
                    drift = math.dist(before, read_aerial_pose(env, robot).position_m)
                    result["hover_drift_m"] = drift
                    # Recovery returns the suspended payload to its source
                    # stand before detach. Quiescence alone is not support.
                    anchor = handle.executor.bridge.measure("carry")[1]
                    pose = read_aerial_pose(env, robot).position_m
                    goal = [pose[i] + pick[i] - anchor[i] for i in range(3)]
                    env.set_command(robot, "goal_position", torch.tensor([goal], device=session.device))
                    for _ in range(1500):
                        session.env.step(neutral)
                        if (math.dist(handle.executor.bridge.measure("carry")[1], pick) < .005
                                and handle.executor.bridge.quiescent("carry")):
                            break
                    else:
                        raise RuntimeError("isolated host recovery did not reach source support")
                    handle.executor.host_release("carry")
                    resources.release(lease)
                    for _ in range(100): session.env.step(neutral)
                    result["checks"] = {"stop_triggered": stopped,
                        "attachment_retained_before_recovery": handle.response.state["attached"] is True,
                        "lease_was_quarantined": result["retained_resources"]["leases"][0]["state"] == "quarantined",
                        "hover_drift_below_2cm": drift < .02, "resources_released": not resources.snapshot()["leases"],
                        "recovered_on_source_stand": math.dist(env.scene["payload"].data.root_pos_w[0].cpu().tolist(), [0., 0., .675]) < .025,
                        "collision_restored": collisions() == original_collisions}
                result["mass_kg"] = mass
            record("final")
            result.update(phases=phases, samples=samples, resources=resources.snapshot(),
                          topology_product_digest=topology.product_digest)
            (output / "evidence.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
            print(json.dumps({"output": str(output), "checks": result["checks"]}), flush=True)
            if not all(result["checks"].values()):
                raise RuntimeError("native diagnostic checks failed; inspect evidence.json")
            while args.hold_gui and not args.headless and session.simulation_app.is_running():
                session.env.step(neutral)
        finally:
            if sys.exc_info()[1] is not None:
                result["diagnostic_error"] = str(sys.exc_info()[1])
            result.update(phases=phases, samples=samples, resources=resources.snapshot())
            (output / "evidence.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
            if editor is not None: editor.destroy()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("navigation", "cycle", "retained-stop"), default="cycle")
    parser.add_argument("--output", type=Path, required=True, help="New output directory, normally below tmp/")
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--hold-gui", action="store_true")
    parser.add_argument("--editor", action="store_true")
    parser.add_argument("--max-ticks", type=int, default=6000)
    args = parser.parse_args()
    if args.max_ticks < 1: parser.error("max-ticks must be positive")
    run(args)


if __name__ == "__main__":
    main()
