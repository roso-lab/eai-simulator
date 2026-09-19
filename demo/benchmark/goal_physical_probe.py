"""Serial author-side Isaac deployment diagnostics, never a business episode.

The parent freezes a new deployment and starts one bounded child using the
repository session API. All native observations are produced in that child;
declarations and expected capabilities remain separate from measurements.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import fcntl
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
import traceback
import uuid

from .goal_deployment import SCENES, build_scene_deployment, validate_goal_deployment
from .provenance import canonical_sha256, file_sha256

ROOT = Path(__file__).resolve().parents[2]
_LOCAL_SOURCES = ("goal_physical_probe.py", "goal_deployment.py", "goal_native_objects.py", "goal_live_spatial.py",
                  "goal_live_occupancy.py", "goal_native_occupancy.py")


def _probe_sources(deployment):
    sources = {str(Path(__file__).with_name(name)): file_sha256(Path(__file__).with_name(name))
               for name in _LOCAL_SOURCES}
    sources[str(ROOT / "simulator.py")] = file_sha256(ROOT / "simulator.py")
    sources.update(deployment["scene_overlay"]["source_files"])
    if deployment["scene_key"] == "modern_house_nvd":
        path = ROOT / "demo/modern_house_benchmark/live_path.py"
        sources[str(path)] = file_sha256(path)
    return sources


def write_json(path, value):
    path = Path(path)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def build_physical_selection(deployment):
    """Native identities/spawns; skill promises are not inferred from a base model."""
    scene = deployment["scene_key"]
    if scene == "city":
        from demo.city_benchmark.selection import build_selected_selection
        rows = deployment["robot_instances"]
        return build_selected_selection([r["public_id"] for r in rows], spawn_positions={
            r["public_id"]: r["spawn_binding"]["position_xyz_m"] for r in rows})
    from EAI.hmrs_env.env_diy.catalog import default_controller_cfg
    robots = []
    for row in deployment["robot_instances"]:
        directive = row["spawn_binding"]["deployment_directive"]
        robots.append({"type": row["type"], "controller": {"mode": "default", "cfg": default_controller_cfg(row["type"])},
                       "spawn_pose": {"position": deepcopy(directive["position_m"]),
                                      "rotation": deepcopy(directive["rotation_wxyz"])},
                       "attachments": []})
    return {"version": 1, "scene_key": scene, "task_name": f"goal_physical_{scene}", "robots": robots}


def prepare_probe(output_root, *, scene, city_package=None, steps=240, gui=False, device="cuda:0",
                  house_withdrawal=False):
    if type(steps) is not int or not 1 <= steps <= 3000:
        raise ValueError("steps must be an integer in [1,3000]")
    if device not in {"cpu", "cuda:0"}:
        raise ValueError("unsupported physics device")
    if type(house_withdrawal) is not bool or (house_withdrawal and (scene != "modern_house_nvd" or steps <= 60)):
        raise ValueError("house withdrawal requires modern_house_nvd and more than 60 steps")
    deployment = build_scene_deployment(scene, city_package_dir=city_package)
    selection = build_physical_selection(deployment)
    from EAI.hmrs_env.env_diy.flow import interactive_selection_from_dict
    interactive_selection_from_dict(selection)
    sources = _probe_sources(deployment)
    from simulator import _runtime_device_for_env
    effective_device = _runtime_device_for_env(selection["task_name"], selection, device)
    request = {"schema": "eai.benchmark.goal_physical_request", "version": 1,
               "run_id": uuid.uuid4().hex, "scene_key": scene, "deployment_sha256": deployment["sha256"],
               "selection_sha256": canonical_sha256(selection), "source_files": sources,
               "steps": steps, "gui": bool(gui), "requested_device": device, "effective_device": effective_device,
               "device_policy": "documented_factory_fallback" if effective_device != device else "requested_device",
               "mode": "native_house_resident_withdrawal" if house_withdrawal else "native_configuration_and_zero_command_settle",
               "scoreable": False}
    root = Path(output_root).absolute()
    root.mkdir(parents=True, exist_ok=False)
    for name, value in (("deployment.json", deployment), ("selection.json", selection), ("request.json", request)):
        write_json(root / name, value)
    return request


def load_probe(root, *, rebuild=True):
    root = Path(root)
    request, deployment, selection = (json.loads((root / name).read_text()) for name in (
        "request.json", "deployment.json", "selection.json"))
    if (set(request) != {"schema", "version", "run_id", "scene_key", "deployment_sha256", "selection_sha256",
                         "source_files", "steps", "gui", "requested_device", "effective_device", "device_policy", "mode", "scoreable"}
            or request.get("schema") != "eai.benchmark.goal_physical_request" or type(request.get("version")) is not int
            or request["version"] != 1 or not isinstance(request.get("run_id"), str)
            or not re.fullmatch(r"[a-f0-9]{32}", request["run_id"])
            or request["scene_key"] not in SCENES or request["scene_key"] != deployment["scene_key"]
            or type(request["steps"]) is not int or not 1 <= request["steps"] <= 3000
            or type(request["gui"]) is not bool or request["requested_device"] not in {"cpu", "cuda:0"}
            or request["mode"] not in {"native_configuration_and_zero_command_settle", "native_house_resident_withdrawal"}
            or (request["mode"] == "native_house_resident_withdrawal" and
                (request["scene_key"] != "modern_house_nvd" or request["steps"] <= 60))
            or request["scoreable"] is not False):
        raise ValueError("invalid physical probe request")
    from simulator import _runtime_device_for_env
    effective_device = _runtime_device_for_env(selection["task_name"], selection, request["requested_device"])
    policy = "documented_factory_fallback" if effective_device != request["requested_device"] else "requested_device"
    if request["effective_device"] != effective_device or request["device_policy"] != policy:
        raise ValueError("physical device resolution differs from frozen launch")
    if canonical_sha256(request["source_files"]) != canonical_sha256(_probe_sources(deployment)):
        raise ValueError("physical probe source inventory missing or changed")
    if request["deployment_sha256"] != deployment["sha256"] or request["selection_sha256"] != canonical_sha256(selection):
        raise ValueError("physical probe input hash mismatch")
    for path, digest in request["source_files"].items():
        if file_sha256(Path(path)) != digest:
            raise ValueError(f"physical probe source changed: {path}")
    if rebuild:
        validate_goal_deployment(deployment)
    if canonical_sha256(selection) != canonical_sha256(build_physical_selection(deployment)):
        raise ValueError("physical selection differs from independent deployment identities/spawns")
    return request, deployment, selection


def _finite(value):
    if isinstance(value, (list, tuple)):
        return all(_finite(v) for v in value)
    return type(value) in (float, int) and math.isfinite(value)


def runtime_sources(stage):
    paths = {Path(layer.realPath).resolve() for layer in stage.GetUsedLayers()
             if layer.realPath and Path(layer.realPath).is_file()}
    for module in tuple(sys.modules.values()):
        name = getattr(module, "__file__", None)
        if name:
            path = Path(name).resolve()
            if path.is_relative_to(ROOT) and path.is_file() and path.suffix == ".py":
                paths.add(path)
    return {str(path): file_sha256(path) for path in sorted(paths)}


def read_robot_measurements(session, deployment, stage):
    """Tensor values and composed authored geometry, explicitly distinct scopes."""
    from pxr import Usd, UsdGeom
    cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_, UsdGeom.Tokens.render])
    result = []
    for row in deployment["robot_instances"]:
        robot = session.base_env.scene.articulations.get(row["runtime_id"])
        if robot is None:
            result.append({"public_id": row["public_id"], "runtime_id": row["runtime_id"], "status": "missing"})
            continue
        paths = list(robot.root_physx_view.prim_paths)
        root_state = robot.data.root_state_w[0].detach().cpu().tolist()
        root_prim = stage.GetPrimAtPath(row["prim_path"])
        bound = cache.ComputeWorldBound(root_prim).ComputeAlignedRange()
        cameras = [str(p.GetPath()) for p in Usd.PrimRange(root_prim) if p.IsA(UsdGeom.Camera)]
        controller = session.base_env._controller_configs[row["runtime_id"]]
        result.append({"public_id": row["public_id"], "runtime_id": row["runtime_id"],
            "status": "native_articulation_read_back", "actual_articulation_paths": paths,
            "root_state_w": root_state, "finite_root_state": _finite(root_state),
            "body_names": list(robot.body_names),
            "body_masses_kg": robot.root_physx_view.get_masses()[0].detach().cpu().tolist(),
            "body_state_w": robot.data.body_state_w[0].detach().cpu().tolist(),
            "authored_bounds_m": [list(bound.GetMin()), list(bound.GetMax())],
            "bounds_scope": "composed_authored_USD_not_live_fabric_collision_envelope",
            "camera_prims": cameras, "controller_class": type(controller).__name__,
            "command_name": getattr(controller, "command_name", None),
            "controller_state_identity": str(id(session.base_env._controllers_dict[row["runtime_id"]])),
            "declared_profile": deepcopy(row["physical_profile"]),
            "profile_conformance_validated": False,
            "pending_interfaces": sorted(set(row["public_declaration"]["interfaces"]) - ({"camera"} if cameras else set())),
            "capability_execution_validated": False})
    return result


def _camera_capture(session, root, positions, name, *, scene_key=None, eye=None):
    from omni.kit.viewport.utility import get_active_viewport
    from demo.city_benchmark.scene.presentation import capture_viewport
    viewport = get_active_viewport()
    if viewport is None:
        raise RuntimeError("GUI requested but no active viewport")
    xs, ys, zs = zip(*positions)
    center = ((min(xs) + max(xs))/2, (min(ys) + max(ys))/2, (min(zs) + max(zs))/2)
    distance = max(1.6 if name.startswith("close_") else 4., max(xs)-min(xs), max(ys)-min(ys))
    if eye is None:
        if scene_key == "factory" and name in {"initial_team", "settled_team"}:
            # The generic elevated camera is outside the factory shell. Keep
            # this review camera inside; individual captures cover occlusions.
            eye = (-9., -6., 2.2)
        elif scene_key == "factory" and name.startswith("close_"):
            # Static mesh center-ray checks select different sides per robot;
            # racks prevent a single overview from showing the whole team.
            center = (center[0], center[1], center[2]+.1)
            offset = ((1.5, 0., 1.) if "iris" in name.lower() else
                      (0., 1.5, 1.) if "carter" in name.lower() else (-1.5, 0., 1.))
            eye = tuple(c+o for c, o in zip(center, offset))
        elif scene_key == "modern_house_nvd":
            if name == "task_objects":
                eye = (5.1, 11.6, 1.8)
            elif name.startswith("close_"):
                eye = (center[0]-.8, center[1]-1.2, min(1.9,center[2]+.7))
            else:
                eye = (8., 1.2, 2.)
        else:
            eye = (center[0]+distance*.7, center[1]-distance, center[2]+distance*.6)
    session.base_env.sim.set_camera_view(eye=eye, target=center)
    for _ in range(24):
        session.base_env.sim.render()
    result = capture_viewport(session.simulation_app, viewport, root / f"{name}.png", render=session.base_env.sim.render)
    result["sha256"] = file_sha256(Path(result["path"]))
    result["camera_eye_world_m"] = list(eye)
    result["camera_target_world_m"] = list(center)
    result["scope"] = "author_review_camera_not_agent_observation"
    return result


def run_worker(root):
    inherited_lock = os.environ.get("EAI_GOAL_PHYSICAL_WORKER_LOCK_FD")
    if inherited_lock is None:
        raise RuntimeError("worker requires the parent-owned serial lock")
    descriptor = int(inherited_lock)
    actual_lock = os.fstat(descriptor)
    expected_lock = (ROOT / "tmp/goal_physical_probe.lock").stat()
    if (actual_lock.st_dev, actual_lock.st_ino) != (expected_lock.st_dev, expected_lock.st_ino):
        raise RuntimeError("worker descriptor is not the canonical serial lock")
    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    root = Path(root).absolute()
    # The parent already rebuilds the source-derived overlay. Importing the
    # standalone pxr here would mix its ABI with Kit's bundled USD libraries.
    request, deployment, selection = load_probe(root, rebuild=False)
    if any(name == "pxr" or name.startswith("pxr.") for name in sys.modules):
        raise RuntimeError("OpenUSD must be loaded by the simulator application, not worker preflight")
    launch = json.loads((root / "launch.json").read_text())
    if (launch.get("request_sha256") != canonical_sha256(request)
            or launch.get("run_id") != request["run_id"]
            or launch.get("command") != [sys.executable, "-m", "demo.benchmark.goal_physical_probe", "--worker", "--output-root", str(root)]):
        raise ValueError("worker launch does not match parent request")
    from simulator import SimulatorLaunchConfig, open_simulator_session
    report = {"schema": "eai.benchmark.goal_physical_probe", "version": 1, "run_id": request["run_id"],
              "scene_key": request["scene_key"], "request_sha256": canonical_sha256(request),
              "deployment_sha256": deployment["sha256"], "status": "starting", "samples": [], "captures": [],
              "business_outcomes_validated": False, "scoreable": False}
    report["mode"] = request["mode"]

    def save():
        write_json(root / "physical_report.json", report)

    def configure(env_cfg):
        from demo.benchmark.goal_native_objects import configure_goal_objects
        try:
            if request["scene_key"] == "city":
                from demo.city_benchmark.scene.main import configure_city_env_cfg
                configure_city_env_cfg(env_cfg, use_flow=False, people=0)
            env_cfg.sim.render_interval = max(1, round(.1 / env_cfg.sim.dt))
            # Isaac Lab only enables queries automatically with a GUI. Empty
            # headless query results otherwise look like collision-free space.
            env_cfg.sim.enable_scene_query_support = True
            env_cfg.episode_length_s = 3600.
            report["object_application"] = configure_goal_objects(env_cfg, deployment)
            save()
        except BaseException as exc:
            # Kit teardown may exit the process before an outer handler runs.
            report.update(status="failed", error=repr(exc), traceback=traceback.format_exc())
            save()
            raise

    config = SimulatorLaunchConfig(env=selection["task_name"], resolved_env_name="EAI-Interactive-v0",
        selection_data=selection, num_envs=1, device=request["requested_device"], seed=7,
        headless=not request["gui"], enable_ros_bridge_extension=False, disable_orsus_ros_env=True,
        env_cfg_hook=configure)
    save()
    try:
        with open_simulator_session(config) as session:
            import torch
            import omni.usd
            from demo.benchmark.goal_native_objects import read_goal_objects
            from demo.benchmark.goal_live_spatial import collect_live_spatial
            from demo.benchmark.goal_native_occupancy import collect_native_occupancy
            from demo.benchmark.goal_live_occupancy import check_live_segment
            stage = omni.usd.get_context().get_stage()
            report["actual_device"] = session.device
            report["possible_agents"] = list(session.possible_agents)
            report["initial_robots"] = read_robot_measurements(session, deployment, stage)
            report["initial_objects"] = read_goal_objects(stage, session.base_env.scene, deployment)
            # Position-controlled aerial members hold their initial native pose.
            aerial_goals = {r["runtime_id"]: session.base_env.scene.articulations[r["runtime_id"]].data.root_pos_w.clone()
                            for r in deployment["robot_instances"] if r["public_declaration"]["mobility"] == "aerial"}
            if request["gui"]:
                report["captures"].append(_camera_capture(session, root,
                    [r["root_state_w"][:3] for r in report["initial_robots"] if "root_state_w" in r], "initial_team", scene_key=request["scene_key"]))
            report["runtime_source_files"] = runtime_sources(stage)
            from pxr import UsdGeom
            manifest = {"schema": "eai.benchmark.native_runtime_manifest", "version": 1,
                "run_id": request["run_id"], "request_sha256": canonical_sha256(request),
                "source_files": report["runtime_source_files"],
                "stage": {"root_layer_identifier": stage.GetRootLayer().identifier,
                          "meters_per_unit": float(UsdGeom.GetStageMetersPerUnit(stage)),
                          "up_axis": str(UsdGeom.GetStageUpAxis(stage)),
                          "used_file_layers": sorted(str(Path(layer.realPath).resolve()) for layer in stage.GetUsedLayers()
                                                     if layer.realPath and Path(layer.realPath).is_file())},
                "scope": "loaded_repository_python_and_composed_USD_layers_not_textures_or_unloaded_dependencies"}
            write_json(root / "runtime_manifest.json", manifest)
            report["runtime_manifest_sha256"] = file_sha256(root / "runtime_manifest.json")
            report["timing"] = {"initial_simulation_time_s": float(session.base_env.sim.current_time),
                "physics_dt_s": float(session.base_env.sim.get_physics_dt()),
                "control_decimation": int(session.env_cfg.decimation), "control_step_dt_s": float(session.base_env.step_dt)}
            for step in range(request["steps"]):
                if not session.simulation_app.is_running():
                    raise RuntimeError("Isaac application closed before the requested steps")
                actions = {agent: torch.zeros((1, 3), device=session.device) for agent in session.possible_agents}
                if request["mode"] == "native_house_resident_withdrawal" and step >= 60:
                    from demo.modern_house_benchmark.live_path import withdrawal_commands, collect_goal_resident_path
                    if step == 60:
                        report["resident_path_before_withdrawal"] = collect_goal_resident_path(
                            stage, session, deployment, episode_id=request["run_id"])
                    command, iris_goal = withdrawal_commands(session, aerial_goals["iris_1"])
                    actions["m20_1"][0] = torch.tensor(command, device=session.device)
                    aerial_goals["iris_1"] = iris_goal
                for agent, goal in aerial_goals.items():
                    session.base_env.set_command(agent, "goal_position", goal)
                    actions[agent] = goal.clone()
                session.env.step(actions)
                states = {agent: session.base_env.scene.articulations[agent].data.root_state_w[0].detach().cpu().tolist()
                          for agent in session.possible_agents}
                report["samples"].append({"step": step, "time_s": float(session.base_env.sim.current_time),
                    "commands": {a: v[0].detach().cpu().tolist() for a,v in actions.items()}, "robot_root_state_w": states})
                if not all(_finite(value) for value in states.values()):
                    raise ValueError("nonfinite native root state")
                if (step+1) % 60 == 0:
                    save()
            report["final_robots"] = read_robot_measurements(session, deployment, stage)
            report["final_objects"] = read_goal_objects(stage, session.base_env.scene, deployment)
            if request["scene_key"] == "modern_house_nvd":
                from demo.modern_house_benchmark.live_path import collect_goal_resident_path
                report["resident_path"] = collect_goal_resident_path(
                    stage, session, deployment, episode_id=request["run_id"])
            import omni.physx
            report["spatial"] = collect_live_spatial(stage, deployment,
                query=omni.physx.get_physx_scene_query_interface(),
                robot_roots=tuple(r["prim_path"] for r in deployment["robot_instances"]))
            # Synchronous author readback: no physics advances between refresh,
            # tensor poses, current colliders and the bounded safety query.
            session.base_env.sim.physics_sim_view.update_articulations_kinematic()
            now = float(session.base_env.sim.current_time)
            context = {"scene_id": request["run_id"], "snapshot_id": f"final_{request['steps']}",
                "source_id": "native_probe_host", "observed_at_s": now,
                "physics_step": int(session.base_env.sim.current_time_step_index),
                "frame_id": "USD_world", "meters_per_unit": 1., "stage_identifier": stage.GetRootLayer().identifier,
                "physx_view_frame": "USD_world", "readback_frozen": True, "kinematics_current": True}
            native_occupancy = collect_native_occupancy(stage, session.base_env.scene,
                robot_bindings=[{"id": row["public_id"], "scene_key": row["runtime_id"], "instance_index": 0,
                                 "root_path": row["prim_path"], "activity": "idle"} for row in deployment["robot_instances"]],
                readback_context=context, now_s=now)
            report["current_occupancy"] = native_occupancy
            if native_occupancy["snapshot"] is not None:
                report["aerial_translation_safety_query"] = [check_live_segment(
                    native_occupancy["snapshot"], expected_inventory=native_occupancy["expected_inventory"],
                    moving_robot_id=row["public_id"], translation_xyz_m=[.2,0.,0.], now_s=now,
                    query=omni.physx.get_physx_scene_query_interface(),
                    query_context={**context,"kind":"physx_scene_query","static_colliders_included":True})
                    for row in deployment["robot_instances"] if row["public_declaration"]["mobility"]=="aerial"]
            if request["gui"]:
                report["captures"].append(_camera_capture(session, root,
                    [r["root_state_w"][:3] for r in report["final_robots"] if "root_state_w" in r], "settled_team", scene_key=request["scene_key"]))
                for robot in report["final_robots"]:
                    if robot["runtime_id"] in aerial_goals or request["scene_key"] in {"factory", "modern_house_nvd"}:
                        report["captures"].append(_camera_capture(session, root, [robot["root_state_w"][:3]],
                            "close_"+robot["runtime_id"], scene_key=request["scene_key"]))
                object_positions = [r["scene_binding"]["deployment_directive"]["position_m"] for r in deployment["object_instances"]]
                if object_positions:
                    report["captures"].append(_camera_capture(session, root, object_positions, "task_objects", scene_key=request["scene_key"]))
                if request["scene_key"] == "factory":
                    report["captures"].append(_camera_capture(session, root, [(1.6814,-3.3279,.21045)],
                        "stair_entry", eye=(1.68,-1.30,1.80)))
                    report["captures"].append(_camera_capture(session, root, [(1.7168,-8.3102,1.56557)],
                        "stair_landing", eye=(1.70,-6.30,2.70)))
            report["status"] = "diagnostic_completed"
            report["native_step_count"] = len(report["samples"])
            report["physical_task_execution_validated"] = False
            if any(file_sha256(Path(path)) != digest for path, digest in report["runtime_source_files"].items()):
                raise ValueError("loaded runtime source or USD layer changed during native execution")
            save()
    except BaseException as exc:
        report.update(status="failed", error=repr(exc), traceback=traceback.format_exc())
        save()
        raise
    return report


def execute_probe(root, *, timeout_s=600.):
    root = Path(root).absolute()
    request, _, _ = load_probe(root)
    if (root / "launch.json").exists():
        raise ValueError("this probe already has a launch; create a fresh output directory")
    if not math.isfinite(timeout_s) or timeout_s <= 0:
        raise ValueError("timeout must be positive")
    lock_path = ROOT / "tmp/goal_physical_probe.lock"
    lock_path.parent.mkdir(exist_ok=True)
    with lock_path.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("another goal physical scene is running; scenes must execute serially") from exc
        command = [sys.executable, "-m", "demo.benchmark.goal_physical_probe", "--worker", "--output-root", str(root)]
        write_json(root / "launch.json", {"command": command, "request_sha256": canonical_sha256(request),
                                          "run_id": request["run_id"], "timeout_s": timeout_s})
        started = time.monotonic()
        with (root / "isaac.log").open("x") as log:
            environment = {**os.environ, "EAI_GOAL_PHYSICAL_WORKER_LOCK_FD": str(lock.fileno())}
            process = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
                                       pass_fds=(lock.fileno(),), env=environment)
            timed_out = False
            interrupted = None
            previous_term = signal.getsignal(signal.SIGTERM)

            def terminate_parent(signum, frame):
                raise KeyboardInterrupt("physical probe parent terminated")

            def stop_worker():
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGTERM)
                try:
                    return process.wait(timeout=10.)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    return process.wait(timeout=10.)

            signal.signal(signal.SIGTERM, terminate_parent)
            try:
                code = process.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                timed_out = True
                code = stop_worker()
            except BaseException as exc:
                interrupted = exc
                code = stop_worker()
            finally:
                signal.signal(signal.SIGTERM, previous_term)
        result = {"returncode": code, "timed_out": timed_out, "wall_time_s": time.monotonic()-started,
                  "request_sha256": canonical_sha256(request), "run_id": request["run_id"]}
        result["physical_report_sha256"] = file_sha256(root / "physical_report.json") if (root / "physical_report.json").is_file() else None
        result["runtime_manifest_sha256"] = file_sha256(root / "runtime_manifest.json") if (root / "runtime_manifest.json").is_file() else None
        result["launch_sha256"] = file_sha256(root / "launch.json")
        try:
            native_report = json.loads((root / "physical_report.json").read_text())
            result["diagnostic_completed"] = (native_report.get("status") == "diagnostic_completed"
                and native_report.get("run_id") == request["run_id"]
                and native_report.get("native_step_count") == request["steps"])
        except (OSError, ValueError):
            result["diagnostic_completed"] = False
        write_json(root / "process_result.json", result)
        if interrupted is not None:
            raise interrupted
        load_probe(root, rebuild=False)
        return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene", choices=SCENES)
    parser.add_argument("--city-package", type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--steps", type=int, default=240)
    parser.add_argument("--gui", action="store_true")
    parser.add_argument("--device", default="cuda:0", choices=("cuda:0", "cpu"))
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--house-withdrawal", action="store_true",
                        help="author diagnostic: command M20_1 and Iris out of the resident corridor after 60 settle steps")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.worker:
        run_worker(args.output_root)
    elif args.execute:
        result = execute_probe(args.output_root)
        print(json.dumps(result))
        return 0 if result["returncode"] == 0 and not result["timed_out"] and result["diagnostic_completed"] else 1
    else:
        if args.scene is None:
            parser.error("preparation requires --scene")
        print(json.dumps(prepare_probe(args.output_root, scene=args.scene, city_package=args.city_package,
            steps=args.steps, gui=args.gui, device=args.device, house_withdrawal=args.house_withdrawal)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
