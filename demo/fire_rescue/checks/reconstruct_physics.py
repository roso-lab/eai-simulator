"""Fresh-process physical reconstruction acceptance fixture, not the rescue mission."""

from __future__ import annotations

import argparse
from functools import partial
from importlib import metadata
import json
import math
import os
from pathlib import Path
import platform
import subprocess
import sys
import traceback


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("audit", "record", "reconstruct"), default="audit")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--scenario", choices=("locomotion", "carry"), default="carry")
    parser.add_argument("--steps", type=int, default=160)
    parser.add_argument("--checkpoint-step", type=int, default=80)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--gui", action="store_true")
    parser.add_argument("--release-at-checkpoint", action="store_true")
    parser.add_argument("--live-speed", type=float, default=0.1,
                        help="fixture command speed after verified live release only (m/s)")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    return parser


def _fingerprint(session, reader, args, fixture):
    import omni.usd
    import torch
    from tools.physics_replay.resources import ReplayResource
    from tools.physics_replay.store import _sha256

    models = {}
    for name, controller in sorted(session.base_env._controllers_dict.items()):
        policy = controller.get("policy")
        if policy is not None:
            resource = ReplayResource(name, policy)
            if resource.mode != "onnx":
                raise ValueError("this bounded fixture only audits the supported ONNX controller")
            state = resource.codec.decode(resource.capture_state()["values"])
            state.pop("session_identity")
            options = policy.session.get_session_options()
            state["execution"] = {key: str(getattr(options, key)) for key in (
                "execution_mode", "graph_optimization_level", "inter_op_num_threads", "intra_op_num_threads",
            )}
            models[name] = state
    sources = {}
    for name, module in sorted(sys.modules.items()):
        if name == "simulator" or name.startswith(("EAI.", "EAI_assets.", "EAI_hmrs.", "isaaclab.",
                                                  "demo.fire_rescue.", "tools.physics_replay.")):
            path = getattr(module, "__file__", None)
            if path and Path(path).is_file() and Path(path).suffix == ".py":
                sources[name] = _sha256(Path(path))
    sources["acceptance_fixture"] = _sha256(Path(__file__))
    layers = {}
    for layer in omni.usd.get_context().get_stage().GetUsedLayers():
        if layer.realPath and Path(layer.realPath).is_file():
            layers[layer.realPath] = _sha256(Path(layer.realPath))
    versions = {}
    for package in ("isaacsim", "isaaclab", "torch", "numpy", "onnxruntime", "onnxruntime-gpu"):
        try:
            versions[package] = metadata.version(package)
        except metadata.PackageNotFoundError:
            versions[package] = None
    return {"task": "fire_rescue_bounded_physics_fixture_v1", "scenario": args.scenario,
            "seed": args.seed, "steps": args.steps, "checkpoint_step": args.checkpoint_step,
            "selection": session.selection_data, "layout": reader.describe(), "models": models,
            "loaded_sources_sha256": sources, "file_backed_usd_layers_sha256": layers,
            "versions": versions, "platform": list(platform.uname()), "device": str(session.device),
            "torch_threads": torch.get_num_threads(), "decimation": session.base_env.cfg.decimation,
            "enhanced_determinism": session.base_env.cfg.sim.physx.enable_enhanced_determinism,
            "gui": args.gui, "action_noise_std": 0.001, "fixture": fixture,
            "coverage": "declared runtime/model/source/file-backed layers; not every hidden simulator state"}


def _prepare_carried_payload(environment):
    import numpy as np
    import omni.physics.tensors
    import omni.usd
    from omni.physx import get_physx_simulation_interface
    from pxr import UsdPhysics
    from demo.fire_rescue.runtime.ur5 import UR5Manager, get_ee_prim_path

    stage = omni.usd.get_context().get_stage()
    payload_path = "/World/Indoor/SM_FireExtinguisher_02"
    payload = stage.GetPrimAtPath(payload_path)
    body = UsdPhysics.RigidBodyAPI(payload)
    if not payload.IsValid() or body.GetRigidBodyEnabledAttr().Get() is False or body.GetKinematicEnabledAttr().Get():
        raise ValueError("fixture requires the original dynamic extinguisher")
    ee_path = get_ee_prim_path(stage, "m20_1")
    if not ee_path:
        raise ValueError("missing M20 UR5 end effector")
    before = {attribute.GetName(): attribute.Get() for attribute in payload.GetAttributes()
              if attribute.GetName().startswith(("physics:", "physx"))}
    view = omni.physics.tensors.create_simulation_view("numpy")
    payload_view = view.create_rigid_body_view(payload_path)
    ee_view = view.create_rigid_body_view(ee_path)
    transform = payload_view.get_transforms().copy()
    ee_position = ee_view.get_transforms()[0, :3].copy()
    transform[0, :3] = ee_position + np.asarray([0.0, 0.0, -0.25], dtype=np.float32)
    if not UR5Manager._set_prim_world_translate(stage, payload_path, transform[0, :3]):
        raise ValueError("failed to author fixture initial pose")
    get_physx_simulation_interface().flush_changes()
    payload_view.set_transforms(transform, np.asarray([0], dtype=np.uint32))
    payload_view.set_velocities(np.zeros((1, 6), dtype=np.float32), np.asarray([0], dtype=np.uint32))
    environment.sim.forward()
    joint_path = UR5Manager._create_grab_joint(
        stage, ee_path, payload_path, ee_position, transform[0, :3], preserve_physics=True,
    )
    if not joint_path:
        raise ValueError("failed to construct physical weld fixture")
    get_physx_simulation_interface().flush_changes()
    environment.sim.forward()
    if {attribute.GetName(): attribute.Get() for attribute in payload.GetAttributes()
        if attribute.GetName().startswith(("physics:", "physx"))} != before:
        raise ValueError("fixture unexpectedly changed payload mass/damping/physical attributes")
    mass = float(payload_view.get_masses()[0, 0])
    if mass <= 0:
        raise ValueError("fixture payload must have positive real mass")
    joint = UsdPhysics.FixedJoint(stage.GetPrimAtPath(joint_path))
    if np.linalg.norm(joint.GetLocalPos0Attr().Get()) > 1e-8:
        raise ValueError("physical weld did not use the actual end-effector pose")
    return {"payload_path": payload_path, "ee_path": ee_path, "joint_path": joint_path,
            "payload_mass_kg": mass, "payload_physics_unchanged": True,
            "joint_excluded_from_articulation": joint.GetExcludeFromArticulationAttr().Get(),
            "local_anchor_ee": list(joint.GetLocalPos0Attr().Get()),
            "local_anchor_payload": list(joint.GetLocalPos1Attr().Get()),
            "scope": "controlled initial physical weld, not autonomous reach/grasp or the full rescue mission"}


def _rng_frame():
    import numpy as np
    import random
    import torch

    python_state = random.getstate()
    numpy_state = np.random.get_state()
    result = {"rng/python/state": np.asarray(python_state[1], dtype=np.uint64),
              "rng/python/gauss": np.asarray([python_state[2] is not None, python_state[2] or 0.0]),
              "rng/numpy/state": numpy_state[1].copy(),
              "rng/numpy/cache": np.asarray(numpy_state[2:]),
              "rng/torch/cpu": torch.get_rng_state().cpu().numpy().copy()}
    if torch.cuda.is_initialized():
        result.update({f"rng/torch/cuda/{index}": state.cpu().numpy().copy()
                       for index, state in enumerate(torch.cuda.get_rng_state_all())})
    return result


def _carry_measurement(reader, fixture):
    import numpy as np
    from pxr import Gf

    transforms = reader.bodies.get_transforms()
    anchors = []
    for path, anchor in ((fixture["ee_path"], fixture["local_anchor_ee"]),
                         (fixture["payload_path"], fixture["local_anchor_payload"])):
        pose = transforms[reader.paths.index(path)]
        rotation = Gf.Quatd(float(pose[6]), Gf.Vec3d(*map(float, pose[3:6])))
        anchors.append(pose[:3] + np.asarray(rotation.Transform(Gf.Vec3d(*anchor))))
    payload_pose = transforms[reader.paths.index(fixture["payload_path"])].copy()
    return {"carry/anchor_error_m": np.asarray([np.linalg.norm(anchors[0] - anchors[1])]),
            "carry/payload_pose": payload_pose}


def run_worker(args) -> None:
    import numpy as np
    import torch
    from simulator import SimulatorLaunchConfig, open_simulator_session
    from demo.fire_rescue.experiment import configure_fire_rescue_env_cfg

    torch.use_deterministic_algorithms(True)
    torch.backends.cudnn.benchmark = False
    torch.backends.cudnn.deterministic = True
    torch.backends.cuda.matmul.allow_tf32 = False
    torch.backends.cudnn.allow_tf32 = False
    config = SimulatorLaunchConfig(
        env="EAI-Factory-v0", seed=args.seed, device="cpu", headless=not args.gui,
        enable_ros_bridge_extension=False, disable_orsus_ros_env=True,
        env_cfg_hook=partial(configure_fire_rescue_env_cfg, exact=True),
    )
    with open_simulator_session(config) as session:
        from contextlib import ExitStack
        import omni.kit.app
        from isaaclab.utils.noise import GaussianNoiseCfg, NoiseModelCfg
        from isaaclab.utils.noise.noise_model import NoiseModel
        from tools.physics_replay.physics_trace import PhysicsStepTrace, PhysxMeasurementReader, PolicyInferenceTrace
        from tools.physics_replay.reconstruction import ReconstructionRecorder, ReconstructionVerifier

        recorder = None
        verifier = None
        report = {"completed": False, "mode": args.mode, "pid": os.getpid()}
        try:
            with ExitStack() as cleanup:
                environment = session.base_env
                fixture = _prepare_carried_payload(environment) if args.scenario == "carry" else {}
                noise_config = NoiseModelCfg(noise_cfg=GaussianNoiseCfg(mean=0.0, std=0.001))
                environment.cfg.action_noise_model = {"m20_1": noise_config}
                environment._action_noise_model = {"m20_1": NoiseModel(noise_config, 1, "cpu")}
                reader = PhysxMeasurementReader(environment)
                cleanup.callback(reader.close)

                def read_physics():
                    values = reader()
                    if fixture:
                        values.update(_carry_measurement(reader, fixture))
                    return values

                trace = PhysicsStepTrace(environment.sim, read_physics, environment.cfg.decimation)
                cleanup.callback(trace.close)
                fingerprint = _fingerprint(session, reader, args, fixture)
                checkpoint_name = "extinguisher_carried" if fixture else "locomotion_ready"
                if args.mode == "record":
                    recorder = ReconstructionRecorder(args.output / "journal", fingerprint)
                else:
                    verifier = ReconstructionVerifier(args.reference, fingerprint, checkpoint=checkpoint_name)
                    cleanup.callback(verifier.close)
                inference = PolicyInferenceTrace({name: controller["policy"]
                                                  for name, controller in environment._controllers_dict.items()
                                                  if controller.get("policy") is not None})
                cleanup.callback(inference.close)
                peak_contact = 0.0
                peak_anchor_error = 0.0
                checkpoint_position = None
                payload_motion = 0.0
                trace.begin()
                for step_index in range(args.steps):
                    speed = args.live_speed if verifier is not None and verifier.released else 0.05
                    command = torch.tensor([[speed, 0.0, 0.0]], device="cpu")
                    frame = {"input/m20_1": command.cpu().numpy().copy(),
                             **{f"before/{name}": value for name, value in _rng_frame().items()}}
                    session.env.step({"m20_1": command})
                    frame.update(trace.take_frame())
                    frame.update(inference.take_frame())
                    frame.update({f"after/{name}": value for name, value in _rng_frame().items()})
                    if fixture:
                        frame.update(_carry_measurement(reader, fixture))
                        peak_anchor_error = max(peak_anchor_error, *(float(value[0]) for name, value in frame.items()
                                                                    if name.endswith("carry/anchor_error_m")))
                        if step_index + 1 == args.checkpoint_step:
                            checkpoint_position = frame["carry/payload_pose"][:3].copy()
                        elif checkpoint_position is not None:
                            payload_motion = max(payload_motion, float(np.linalg.norm(
                                frame["carry/payload_pose"][:3] - checkpoint_position,
                            )))
                    peak_contact = max(peak_contact, *(float(np.abs(value).max(initial=0)) for name, value
                                                       in frame.items() if name.endswith("contacts/net_force_N")))
                    checkpoint = checkpoint_name if step_index + 1 == args.checkpoint_step else None
                    task_state = {"step": step_index + 1, "phase": "carrying" if fixture else "locomotion"}
                    if recorder is not None:
                        recorder.append(frame, task_state=task_state, checkpoint=checkpoint)
                    elif not verifier.released:
                        verifier.compare(frame, task_state=task_state, checkpoint=checkpoint)
                        if args.release_at_checkpoint and verifier.prefix_verified:
                            verifier.release()
                            print(f"[Reconstruction] verified {checkpoint_name}; live continuation released", flush=True)
                report.update(steps=args.steps, checkpoint_step=args.checkpoint_step,
                              fixture=fixture, peak_contact_force_N=peak_contact,
                              peak_weld_anchor_error_m=peak_anchor_error, payload_motion_after_checkpoint_m=payload_motion,
                              physics_dt=reader.dt, decimation=environment.cfg.decimation,
                              live_steps_after_release=args.steps - args.checkpoint_step
                              if verifier is not None and verifier.released else 0,
                              live_command_speed=args.live_speed if verifier is not None and verifier.released else None,
                              scope="bounded physical fixture, not the full Fire Rescue task/main")
                if peak_contact <= 0 or (fixture and (payload_motion <= 1e-3 or peak_anchor_error > 0.01)):
                    raise ValueError("fixture lacks actual contact/motion or its physical weld is not holding")
                if recorder is not None:
                    recorder.close()
                report["completed"] = True
        except Exception:
            report["error"] = traceback.format_exc()
            print(report["error"], file=sys.stderr, flush=True)
            if recorder is not None:
                recorder.close(complete=False)
            omni.kit.app.get_app().post_quit(1)
        finally:
            if verifier is not None:
                report["verification"] = verifier.report()
            (args.output / "run.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def run_check(args) -> dict:
    args.output.mkdir(parents=True, exist_ok=False)
    reports = []
    modes = ("record", "reconstruct") if args.mode == "audit" else (args.mode,)
    for mode in modes:
        directory = args.output / mode
        directory.mkdir()
        command = [sys.executable, "-m", "demo.fire_rescue.checks.reconstruct_physics", "--worker",
                   "--mode", mode, "--output", str(directory), "--scenario", args.scenario,
                   "--steps", str(args.steps), "--checkpoint-step", str(args.checkpoint_step), "--seed", str(args.seed)]
        if args.gui:
            command.append("--gui")
        if mode == "reconstruct":
            reference = args.output / "record" / "journal" if args.mode == "audit" else args.reference
            command.extend(["--reference", str(reference)])
            if args.release_at_checkpoint:
                command.extend(["--release-at-checkpoint", "--live-speed", str(args.live_speed)])
        print(f"[Reconstruction] starting fresh {mode} process; log: {directory / 'isaac.log'}", flush=True)
        with (directory / "isaac.log").open("w", encoding="utf-8") as log:
            result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=600,
                                    env={**os.environ, "PYTHONHASHSEED": str(args.seed),
                                         "CUBLAS_WORKSPACE_CONFIG": ":4096:8"})
        path = directory / "run.json"
        report = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {"completed": False,
                                                                                  "error": "worker report missing"}
        report["exit_code"] = result.returncode
        reports.append(report)
        if result.returncode != 0 or not report["completed"]:
            break
    passed = len(reports) == len(modes) and all(report["completed"] and report["exit_code"] == 0 for report in reports)
    if passed and modes[-1] == "reconstruct":
        verification = reports[-1]["verification"]
        passed = verification["released_for_live_continuation"] if args.release_at_checkpoint else verification[
            "original_continuation_verified"]
    summary = {"passed": passed, "mode": args.mode, "runs": reports,
               "claim": "finite-horizon measured equality only; not universal physical zero deviation"}
    (args.output / "comparison.json").write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(f"[Reconstruction] {'PASS' if passed else 'FAIL'}: {args.output / 'comparison.json'}", flush=True)
    return summary


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if not 0 < args.checkpoint_step < args.steps:
        parser.error("require 0 < --checkpoint-step < --steps")
    if not 0 <= args.seed < 2 ** 32 or not math.isfinite(args.live_speed):
        parser.error("require a uint32 seed and a finite live speed")
    if args.mode == "reconstruct" and args.reference is None:
        parser.error("--mode reconstruct requires --reference pointing to a journal directory")
    if args.release_at_checkpoint and args.mode != "reconstruct":
        parser.error("--release-at-checkpoint requires --mode reconstruct")
    if args.worker and args.mode == "audit":
        parser.error("audit must use the fresh-process supervisor")
    if args.worker:
        run_worker(args)
        return 0
    return 0 if run_check(args)["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
