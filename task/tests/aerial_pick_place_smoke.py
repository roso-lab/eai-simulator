"""Explicit headless Iris integration smoke using the existing session API.

This is not a pytest test and does not provide an external evaluator verdict.
Run from the repository with env_isaaclab. Output goes to a caller-selected
runtime JSON file, never a maintained fixture.
"""
import argparse
from dataclasses import replace
import json
import math
from pathlib import Path


def run(mode, output, device):
    from simulator import SimulatorLaunchConfig, open_simulator_session

    selection = {"version": 1, "scene_key": "plane", "task_name": "aerial_transport_smoke",
        "robots": [{"type": "iris", "controller": {"cfg": "PEGASUS_IRIS_POSITION_CFG", "mode": "default"},
                    "attachments": [], "spawn_pose": {"position": [0., 0., 1.3], "rotation": [1., 0., 0., 0.]}}]}

    def scene_hook(cfg):
        from isaaclab.assets import RigidObjectCfg
        from isaaclab import sim as sim_utils
        cfg.scene.payload = RigidObjectCfg(
            prim_path="{ENV_REGEX_NS}/payload",
            spawn=sim_utils.CuboidCfg(size=(.10, .10, .25),
                rigid_props=sim_utils.RigidBodyPropertiesCfg(),
                mass_props=sim_utils.MassPropertiesCfg(mass=.005),
                collision_props=sim_utils.CollisionPropertiesCfg()),
            init_state=RigidObjectCfg.InitialStateCfg(pos=(0., 0., .675)))
        # Static narrow pedestals keep rotor/landing-gear clear of support.
        from isaaclab.assets import AssetBaseCfg
        for name, x in (("pick_stand", 0.), ("place_stand", .6)):
            setattr(cfg.scene, name, AssetBaseCfg(
                prim_path="{ENV_REGEX_NS}/"+name,
                spawn=sim_utils.CuboidCfg(size=(.40, .40, .55), collision_props=sim_utils.CollisionPropertiesCfg()),
                init_state=AssetBaseCfg.InitialStateCfg(pos=(x, 0., .275))))

    config = SimulatorLaunchConfig(env="aerial_transport_smoke", resolved_env_name="aerial_transport_smoke",
        selection_data=selection, device=device, headless=True, enable_ros_bridge_extension=False,
        disable_orsus_ros_env=True, aerial_sensor_backend="disabled", env_cfg_hook=scene_hook)
    with open_simulator_session(config) as session:
        import torch
        from pxr import Usd, UsdPhysics
        from algorithm.skills import SkillRequest
        from task.executors.aerial_pick_place import AerialPickPlaceExecutor, read_aerial_pose
        env = session.base_env
        robot = "iris_1"
        neutral = {robot: torch.zeros((1, 3), device=env.device)}
        env.set_command(robot, "goal_position", torch.tensor([[0., 0., 1.3]], device=env.device))
        for _ in range(200):
            env.step(neutral)
        topo = {"nodes": [{"id": "pick", "position_xyz": [0., 0., 1.3], "target_refs": ["source"]},
                          {"id": "place", "position_xyz": [.6, 0., 1.3], "target_refs": ["destination"]}],
                "edges": [{"id": "carry", "from": "pick", "to": "place"}]}
        executor = AerialPickPlaceExecutor(env, topology=topo, world_state_validator=lambda seq, motion: None,
            timeout_s=120., drone_anchor_local_m=(0., 0., -.30))
        path = "/World/envs/env_0/payload"
        # Explicit world anchor supplied by the host; no bounding-box inference.
        pick_anchor = env.scene["payload"].data.root_pos_w[0].cpu().tolist()
        pick_anchor[2] += .125
        request = SkillRequest("smoke/start", "eai.transport.aerial_pick_place", robot, task_id="smoke",
            parameters={"intent": "start", "world_state_seq": 0, "object_ref": path,
                        "source_ref": "source", "destination_ref": "destination", "robot_ids": [robot],
                        "carry_mode": "fixed_joint", "pick_anchor_world_m": pick_anchor,
                        "place_anchor_world_m": [.6, 0., .8], "phase_timeout_s": 60.})
        response = executor.dispatch(request)
        phases, measurements, stopped = [], [], False
        stage = executor.bridge.stage
        def physical_properties():
            return {str(attr.GetPath()): {"value": str(attr.Get()), "authored": attr.HasAuthoredValueOpinion()}
                for prim in Usd.PrimRange(stage.GetPrimAtPath(path)) for attr in prim.GetAttributes()
                if any(token in attr.GetName().lower() for token in ("mass", "inertia", "gravity", "damping", "collisionenabled"))}
        def collision_properties():
            return {str(prim.GetPath()): {"value": UsdPhysics.CollisionAPI(prim).GetCollisionEnabledAttr().Get(),
                                        "authored": UsdPhysics.CollisionAPI(prim).GetCollisionEnabledAttr().HasAuthoredValueOpinion()}
                    for prim in Usd.PrimRange(stage.GetPrimAtPath(path)) if prim.HasAPI(UsdPhysics.CollisionAPI)}
        properties_before, collisions_before = physical_properties(), collision_properties()
        mass_before = UsdPhysics.MassAPI(stage.GetPrimAtPath(path)).GetMassAttr().Get()
        collision_before = UsdPhysics.CollisionAPI(stage.GetPrimAtPath(path)).GetCollisionEnabledAttr().Get()
        for tick in range(6000):
            intent = "start" if tick == 0 else "continue"
            if mode == "retained-stop" and response.state["phase"] == "navigating_to_place":
                intent, stopped = "stop", True
            current = replace(request, request_id=f"smoke/{tick}", parameters={**request.parameters, "intent": intent, "world_state_seq": tick+1})
            response = executor.control(current)
            phase = response.state["phase"]
            if not phases or phases[-1] != phase:
                phases.append(phase)
                measurements.append({"phase": phase, "root_position_m": list(read_aerial_pose(env, robot).position_m),
                    "payload_position_m": env.scene["payload"].data.root_pos_w[0].cpu().tolist(),
                    "simulation_time_s": env.sim.current_time})
            if response.state["status"] != "pending":
                break
        result = {"mode": mode, "transport_state": response.state, "phases": phases,
                  "phase_measurements": measurements, "private_evidence": response.private_evidence,
                  "mass_before_kg": mass_before,
                  "mass_after_kg": UsdPhysics.MassAPI(stage.GetPrimAtPath(path)).GetMassAttr().Get(),
                  "collision_before": collision_before,
                  "collision_after": UsdPhysics.CollisionAPI(stage.GetPrimAtPath(path)).GetCollisionEnabledAttr().Get(),
                  "physical_properties_before": properties_before, "physical_properties_after": physical_properties(),
                  "collision_properties_before": collisions_before, "collision_properties_after": collision_properties(),
                  "physical_outcome": "unknown", "external_evaluator": None}
        if stopped:
            result["retained_joint_before_host_release"] = executor.bridge.evidence("smoke")["joint_path"]
            result["retained_resources_before_host_release"] = executor.resources.snapshot()
            before = list(read_aerial_pose(env, robot).position_m)
            for _ in range(100):
                env.step(neutral)
            after = list(read_aerial_pose(env, robot).position_m)
            result["retained_hover_measurements"] = {"before": before, "after": after}
            result["retained_velocity_m_s"] = env.scene[robot].data.root_lin_vel_w[0].cpu().tolist()
            result["payload_position_while_retained_m"] = env.scene["payload"].data.root_pos_w[0].cpu().tolist()
            # Host deliberately recovers before session teardown; this is an
            # explicit release of the suspended test payload, not a place.
            executor.host_release("smoke", quiescent=True)
            result["collision_after_host_release"] = UsdPhysics.CollisionAPI(stage.GetPrimAtPath(path)).GetCollisionEnabledAttr().Get()
            result["collision_properties_after_host_release"] = collision_properties()
            result["physical_properties_after_host_release"] = physical_properties()
            result["resources_after_host_release"] = executor.resources.snapshot()
            result["smoke_checks"] = {
                "joint_retained": bool(result["retained_joint_before_host_release"]),
                "lease_quarantined": result["retained_resources_before_host_release"]["leases"][0]["state"] == "quarantined",
                "retained_hover_drift_below_2cm": math.dist(before, after) < .02,
                "retained_velocity_below_2cm_s": math.sqrt(sum(v*v for v in result["retained_velocity_m_s"])) < .02,
                "physical_properties_restored": properties_before == result["physical_properties_after_host_release"],
                "collision_authored_state_restored": collisions_before == result["collision_properties_after_host_release"],
                "resources_released": result["resources_after_host_release"]["leases"] == [],
            }
        elif response.state["status"] == "succeeded":
            for _ in range(100):
                env.step(neutral)
            result["settled_payload_position_m"] = env.scene["payload"].data.root_pos_w[0].cpu().tolist()
            result["settled_root_position_m"] = list(read_aerial_pose(env, robot).position_m)
            result["smoke_checks"] = {
                "payload_resting_on_fixture": math.dist(result["settled_payload_position_m"], [.6, 0., .675]) < .02,
                "hover_height_restored": abs(result["settled_root_position_m"][2]-response.private_evidence["place_hover_z"]) < .02,
                "physical_properties_preserved": properties_before == result["physical_properties_after"],
                "collision_authored_state_restored": collisions_before == result["collision_properties_after"],
                "resources_released": executor.resources.snapshot()["leases"] == [],
            }
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        Path(output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"mode": mode, "state": response.state, "phases": phases, "output": str(output)}), flush=True)
        if result.get("smoke_checks") and not all(result["smoke_checks"].values()):
            raise RuntimeError("fixture observation checks failed; physical_outcome remains unknown")
        if mode == "cycle" and response.state["status"] != "succeeded":
            raise RuntimeError("pick/place command cycle did not complete; inspect private evidence")
        if mode == "retained-stop" and not stopped:
            raise RuntimeError("retained-stop smoke never reached the stop trigger")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("cycle", "retained-stop"), default="cycle")
    parser.add_argument("--output", required=True)
    parser.add_argument("--device", default="cuda:0")
    args = parser.parse_args()
    run(args.mode, args.output, args.device)
