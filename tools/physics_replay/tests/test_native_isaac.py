"""Opt-in Factory regressions for checkpoints before and after extinguisher pickup."""

import json
import os
from pathlib import Path
import subprocess
import sys
import traceback
from types import SimpleNamespace

import pytest


@pytest.mark.skipif(os.environ.get("EAI_RUN_NATIVE_REPLAY") != "1", reason="requires an explicit Isaac/GPU integration run")
def test_factory_pickup_then_restore_and_prewrite_rejection(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "tools.physics_replay.tests.test_native_isaac", "--worker", str(tmp_path)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=300,
    )
    (tmp_path / "isaac.log").write_text(result.stdout, encoding="utf-8")
    report_path = tmp_path / "pickup_regression.json"
    assert report_path.is_file(), result.stdout[-5000:]
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["passed"], report.get("error", result.stdout[-5000:])
    assert result.returncode == 0, result.stdout[-5000:]


@pytest.mark.skipif(os.environ.get("EAI_RUN_NATIVE_REPLAY") != "1", reason="requires an explicit Isaac/GPU integration run")
def test_task_runner_uses_algorithm_entry_and_saves_without_manual_commands(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "tools.physics_replay.tests.test_native_isaac", "--task-worker", str(tmp_path)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=300,
    )
    (tmp_path / "isaac.log").write_text(result.stdout, encoding="utf-8")
    report_path = tmp_path / "task_runner_regression.json"
    assert report_path.is_file(), result.stdout[-5000:]
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["passed"], report.get("error", result.stdout[-5000:])
    assert result.returncode == 0, result.stdout[-5000:]


def _run_task_worker(tmp_path):
    from simulator import SimulatorLaunchConfig, open_simulator_session
    from demo.fire_rescue.config import FireRescueConfig
    from demo.fire_rescue.experiment import configure_fire_rescue_env_cfg, run_robot_baseline_experiment

    config = SimulatorLaunchConfig(
        env="EAI-Factory-v0", device="cpu", headless=True, enable_ros_bridge_extension=False,
        disable_orsus_ros_env=True, env_cfg_hook=configure_fire_rescue_env_cfg,
    )
    with open_simulator_session(config) as session:
        try:
            from demo.fire_rescue.runtime.experiment_loop import run_robot_factory_platform

            def bounded_platform(**kwargs):
                kwargs["args_cli"].max_steps = 220
                return run_robot_factory_platform(**kwargs)

            task_config = FireRescueConfig(
                trials=1, trial_hazard_ids="1", auto_fire_delay=0.0, headless=True,
                task_checkpoints=True, assignment_policy="algorithm.emos.example_policy:assign",
                branch_checkpoint_dir=tmp_path / "task",
            )
            steps = run_robot_baseline_experiment(session, task_config, run_platform=bounded_platform)
            index_path = tmp_path / "task" / "trial_01" / "task_checkpoints.json"
            index = json.loads(index_path.read_text(encoding="utf-8"))
            assert steps == 220, steps
            assert index["algorithm"] == task_config.assignment_policy
            assert "assignment_applied" in index["state"]["completed"], index
            assert index["result"] == "fail", index
            report = {"passed": True, "steps": steps, "task_index": index, "bounded_run_not_full_mission": True}
        except Exception:
            report = {"passed": False, "error": traceback.format_exc()}
            print(report["error"], file=sys.stderr, flush=True)
        (tmp_path / "task_runner_regression.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


@pytest.mark.skipif(os.environ.get("EAI_RUN_NATIVE_REPLAY") != "1", reason="requires an explicit Isaac/GPU integration run")
def test_post_solve_contact_and_noisy_policy_measurements(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "tools.physics_replay.tests.test_native_isaac", "--force-worker", str(tmp_path)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=300,
    )
    (tmp_path / "isaac.log").write_text(result.stdout, encoding="utf-8")
    destination = tmp_path / "force_regression.json"
    assert destination.is_file(), result.stdout[-5000:]
    report = json.loads(destination.read_text(encoding="utf-8"))
    assert report["measurement_pipeline_passed"], report.get("error", result.stdout[-5000:])
    assert result.returncode == 0, result.stdout[-5000:]


def _run_force_worker(tmp_path):
    from functools import partial
    from simulator import SimulatorLaunchConfig, open_simulator_session
    from demo.fire_rescue.experiment import configure_fire_rescue_env_cfg

    config = SimulatorLaunchConfig(
        env="EAI-Factory-v0", device="cpu", headless=True, enable_ros_bridge_extension=False,
        disable_orsus_ros_env=True, env_cfg_hook=partial(configure_fire_rescue_env_cfg, exact=True),
    )
    with open_simulator_session(config) as session:
        trace = None
        try:
            import numpy as np
            import torch
            from isaaclab.utils.noise import GaussianNoiseCfg, NoiseModelCfg
            from isaaclab.utils.noise.noise_model import NoiseModel
            from tools.physics_replay.live import LiveCheckpointBackend, LiveReplayController
            from tools.physics_replay.physics_trace import PhysicsStepTrace, PhysxMeasurementReader
            from tools.physics_replay.runtime_state import ActuatorStateComponent, EnvironmentBuffersComponent
            from tools.physics_replay.stage_state import SleepStateComponent, StageStateComponent

            environment = session.base_env
            weld_fixture = _check_physical_weld_authoring()
            noise_config = NoiseModelCfg(noise_cfg=GaussianNoiseCfg(mean=0.0, std=0.001))
            environment.cfg.action_noise_model = {"m20_1": noise_config}
            environment._action_noise_model = {"m20_1": NoiseModel(noise_config, 1, "cpu")}
            commands = {"m20_1": torch.tensor([[0.05, 0.0, 0.0]], device="cpu")}
            for unused in range(40):
                session.env.step(commands)
            reader = PhysxMeasurementReader(environment)
            trace = PhysicsStepTrace(environment.sim, reader, environment.cfg.decimation)
            stage = StageStateComponent(session)
            backend = LiveCheckpointBackend(session, tmp_path / "checkpoints", physics_trace=trace, components=(
                stage, SleepStateComponent(stage), EnvironmentBuffersComponent(environment), ActuatorStateComponent(environment),
            ))
            controller = LiveReplayController(backend)
            controller.capture_checkpoint()
            controller.request("verify", 4)
            controller.poll()
            peak_contact = 0.0
            peak_friction = 0.0
            for unused in range(8):
                if not controller.poll():
                    break
                session.env.step(commands)
                peak_contact = max(peak_contact, float(np.abs(reader.contacts.get_net_contact_forces(reader.dt)).max()))
                forces, points, counts, starts = reader.contacts.get_friction_data(reader.dt)
                for count, start in zip(counts.ravel(), starts.ravel()):
                    if count:
                        peak_friction = max(peak_friction, float(np.abs(forces[int(start):int(start + count)]).max()))
                controller.after_step()
            verification = controller.verification_report
            assert verification is not None, controller.message
            assert peak_contact > 0.0, "force measurement must not be an empty/zero probe"
            errors = verification.get("trajectory_errors", {})
            command_errors = [value for name, value in errors.items() if "/command/m20_1/" in name]
            assert command_errors and all(value == 0.0 for value in command_errors), command_errors
            report = {
                "measurement_pipeline_passed": "error" not in verification,
                "zero_deviation_achieved": bool(errors) and all(value == 0.0 for value in errors.values())
                and all(value == 0.0 for value in backend.last_report["readback_errors"].values()),
                "peak_contact_force_N": peak_contact, "peak_friction_force_N": peak_friction,
                "real_action_noise_std": 0.001, "physics_dt": reader.dt,
                "post_noise_command_max_error": max(command_errors),
                "verification": verification,
                "physical_weld_authoring": weld_fixture,
            }
        except Exception:
            report = {"measurement_pipeline_passed": False, "error": traceback.format_exc()}
            print(report["error"], file=sys.stderr, flush=True)
        finally:
            if trace is not None:
                trace.close()
        (tmp_path / "force_regression.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def _check_physical_weld_authoring():
    from pxr import Gf, PhysxSchema, Usd, UsdGeom, UsdPhysics
    from demo.fire_rescue.runtime.ur5 import UR5Manager

    stage = Usd.Stage.CreateInMemory()
    for path, offset, angle in (("/World/arm", (0.0, 0.0, 1.0), 30.0),
                                ("/World/payload", (0.1, 0.0, 1.0), 70.0)):
        cube = UsdGeom.Cube.Define(stage, path)
        cube.AddTranslateOp().Set(Gf.Vec3d(*offset))
        cube.AddRotateZOp().Set(angle)
        UsdPhysics.RigidBodyAPI.Apply(cube.GetPrim()).CreateRigidBodyEnabledAttr().Set(True)
    payload = stage.GetPrimAtPath("/World/payload")
    UsdPhysics.MassAPI.Apply(payload).CreateMassAttr().Set(2.5)
    PhysxSchema.PhysxRigidBodyAPI.Apply(payload).CreateLinearDampingAttr().Set(0.2)
    before = {attribute.GetName(): attribute.Get() for attribute in payload.GetAttributes()}
    joint_path = UR5Manager._create_grab_joint(
        stage, "/World/arm", "/World/payload", (0.0, 0.0, 1.0), (0.1, 0.0, 1.0), preserve_physics=True,
    )
    assert joint_path
    assert {attribute.GetName(): attribute.Get() for attribute in payload.GetAttributes()} == before
    joint = UsdPhysics.FixedJoint(stage.GetPrimAtPath(joint_path))
    assert joint.GetExcludeFromArticulationAttr().Get()
    for body_path, local_rotation in (("/World/arm", joint.GetLocalRot0Attr().Get()),
                                      ("/World/payload", joint.GetLocalRot1Attr().Get())):
        world_rotation = UsdGeom.Xformable(stage.GetPrimAtPath(body_path)).ComputeLocalToWorldTransform(0).ExtractRotationQuat()
        assert abs((world_rotation * Gf.Quatd(local_rotation)).GetReal() - 1.0) < 1e-6
    return {"passed": True, "payload_mass_kg": 2.5, "payload_attributes_unchanged": True,
            "scope": "USD weld authoring, not a dynamic grasp or carry benchmark"}


def _run_worker(tmp_path):
    from simulator import SimulatorLaunchConfig, open_simulator_session

    config = SimulatorLaunchConfig(
        env="EAI-Factory-v0", device="cpu", headless=True,
        enable_ros_bridge_extension=False,
    )
    with open_simulator_session(config) as session:
        try:
            _check_pickup_restore(session, tmp_path)
        except Exception:
            error = traceback.format_exc()
            print(error, file=sys.stderr, flush=True)
            (tmp_path / "pickup_regression.json").write_text(json.dumps({
                "passed": False, "error": error,
            }, indent=2) + "\n", encoding="utf-8")


def _check_pickup_restore(session, tmp_path):
    import omni.usd
    from pxr import UsdGeom, UsdPhysics
    from demo.fire_rescue.runtime.ur5 import UR5Manager
    from tools.physics_replay.live import LiveCheckpointBackend, LiveReplayController
    from tools.physics_replay.runtime_state import ActuatorStateComponent, EnvironmentBuffersComponent
    from tools.physics_replay.stage_state import SleepStateComponent, StageStateComponent

    for unused in range(40):
        session.env.step({})
    stage = omni.usd.get_context().get_stage()
    prop_path = "/World/Indoor/SM_FireExtinguisher_02"
    proxy_path = "/World/SM_FireExtinguisherProxy"
    prop = stage.GetPrimAtPath(prop_path)
    assert prop.IsValid()
    stage_component = StageStateComponent(session, runtime_paths=(proxy_path,))
    backend = LiveCheckpointBackend(session, tmp_path, components=(
        stage_component, SleepStateComponent(stage_component),
        EnvironmentBuffersComponent(session.base_env), ActuatorStateComponent(session.base_env),
    ))
    controller = LiveReplayController(backend)
    cases = []
    for cycle in range(2):
        visibility = UsdGeom.Imageable(prop).GetVisibilityAttr().Get()
        controller.request("save")
        controller.poll()
        assert not controller.save_error, controller.message
        created = UR5Manager._activate_follow_attach_legacy(stage, prop_path, (0.0, 0.0, 1.0))
        assert created == proxy_path
        assert UsdPhysics.RigidBodyAPI(prop).GetRigidBodyEnabledAttr().Get() is False
        assert stage.GetPrimAtPath(proxy_path).IsValid()
        session.base_env.sim.forward()
        physics_time = session.base_env.sim.current_time
        controller.request("restore")
        assert not controller.poll()
        assert not controller.blocked, controller.message
        assert backend.last_report["passed"]
        assert session.base_env.sim.current_time == physics_time
        assert UsdPhysics.RigidBodyAPI(prop).GetRigidBodyEnabledAttr().Get() is True
        assert UsdGeom.Imageable(prop).GetVisibilityAttr().Get() == visibility
        assert not stage.GetPrimAtPath(proxy_path).IsValid()
        cases.append({"cycle": cycle, "restore": backend.last_report, "extra_physics_time": 0.0})
        controller.request("step")
        assert controller.poll()
        session.env.step({})
        controller.after_step()
        assert not controller.poll()

    unexpected_path = "/World/CheckpointUnexpectedBody"
    unexpected = UsdGeom.Cube.Define(stage, unexpected_path).GetPrim()
    UsdPhysics.RigidBodyAPI.Apply(unexpected).CreateRigidBodyEnabledAttr().Set(False)
    previous_step = controller.step_index
    controller.request("restore")
    assert not controller.poll()
    assert not controller.blocked, controller.message
    assert unexpected_path in controller.message
    assert controller.step_index == previous_step
    assert backend.last_report["phase"] == "validation"
    assert not backend.last_report["state_may_have_changed"]
    stage.RemovePrim(unexpected_path)
    controller.request("restore")
    assert not controller.poll()
    assert not controller.blocked, controller.message
    carrying = _check_carrying_checkpoints(session, tmp_path / "carrying")
    (tmp_path / "pickup_regression.json").write_text(json.dumps({
        "passed": True, "cases": cases, "prewrite_rejection_keeps_current_state": True,
        "carrying": carrying,
        "trajectory_accuracy_verified": False,
    }, indent=2) + "\n", encoding="utf-8")


def _check_carrying_checkpoints(session, output_dir):
    import numpy as np
    import omni.usd
    from pxr import UsdGeom, UsdPhysics
    from demo.fire_rescue.runtime.native_replay import (
        build_ur5_replay_component, validate_native_fire_rescue_checkpoint,
    )
    from demo.fire_rescue.runtime.ur5 import UR5Manager, resolve_ur5_articulation
    from demo.fire_rescue.task_checkpoints import TaskCheckpointScheduler, checkpoint_signals
    from tools.physics_replay.live import LiveCheckpointBackend, LiveReplayController
    from tools.physics_replay.runtime_state import ActuatorStateComponent, EnvironmentBuffersComponent
    from tools.physics_replay.stage_state import SleepStateComponent, StageStateComponent

    stage = omni.usd.get_context().get_stage()
    prop_path = "/World/Indoor/SM_FireExtinguisher_02"
    proxy_path = "/World/SM_FireExtinguisherProxy"
    joint_path = "/World/envs/env_0/extinguisher_grab_joint"
    prop = stage.GetPrimAtPath(prop_path)
    original_visibility = UsdGeom.Imageable(prop).GetVisibilityAttr().Get()
    ur5 = UR5Manager(session.base_env)
    task_checkpoints = TaskCheckpointScheduler(output_dir)
    ur5_component = build_ur5_replay_component(ur5)
    stage_component = StageStateComponent(session, runtime_paths=(proxy_path, joint_path))
    backend = LiveCheckpointBackend(
        session, output_dir, components=(
            stage_component, SleepStateComponent(stage_component), ur5_component, task_checkpoints,
            EnvironmentBuffersComponent(session.base_env), ActuatorStateComponent(session.base_env),
        ),
        safety_check=lambda: validate_native_fire_rescue_checkpoint(SimpleNamespace(), SimpleNamespace(), ur5),
    )
    controller = LiveReplayController(backend)

    def task_boundary():
        task_checkpoints.tick(controller, checkpoint_signals(
            assignment_applied=True, ur5=ur5, rescue_channel_open=False,
            obstacle=SimpleNamespace(), result="running",
        ))

    task_boundary()
    assert not controller.save_error, controller.message
    assert len(backend.checkpoints) == 1
    before_pickup = backend.checkpoints[-1].identifier
    carrier_name = "m20_1"
    carrier = session.base_env.scene.articulations[carrier_name]
    arm = resolve_ur5_articulation(session.base_env.scene.articulations, carrier_name)
    ur5.start_extinguisher_pickup(carrier_name)
    observed_lift_without_checkpoint = False
    for unused in range(140):
        if ur5._ext_pick.phase == 2 and not ur5.is_extinguisher_grabbed:
            ee_position = ur5._get_ee_pos_cached(arm, carrier_name)
            assert ee_position is not None
            assert ur5._set_prim_world_translate(stage, prop_path, ee_position)
        ur5.update_extinguisher_grab(stage)
        session.env.step({})
        ur5.update_extinguisher_visual_follow(stage)
        controller.after_step()
        task_boundary()
        if ur5.is_extinguisher_grabbed and ur5.is_extinguisher_pick_running:
            assert len(backend.checkpoints) == 1
            observed_lift_without_checkpoint = True
        assert controller.poll()
        assert not controller.save_error, controller.message
        if len(backend.checkpoints) == 2:
            break
    assert observed_lift_without_checkpoint
    assert len(backend.checkpoints) == 2, controller.message
    assert ur5.is_extinguisher_pick_complete
    assert ur5.is_carrying_extinguisher and ur5._ext_active
    assert ur5._ext_follow_mode and ur5._ext_target_prim == proxy_path
    carrying_checkpoint = backend.checkpoints[-1].identifier
    saved_proxy_position = ur5._get_prim_pos(stage, proxy_path)
    saved_ur5_state = ur5_component.capture_state()
    assert ur5._jid_cache and ur5._ee_body_cache
    assert ur5._ext_pick._arm_joint_ids is not None
    saved_carry_pose = list(ur5._ext_pick.carry_pose)
    cases = []

    def restore(checkpoint_id, label):
        physics_time = session.base_env.sim.current_time
        controller.request("restore", checkpoint_id)
        assert not controller.poll()
        assert not controller.blocked, controller.message
        assert backend.last_report["passed"], backend.last_report
        assert session.base_env.sim.current_time == physics_time
        cases.append({"case": label, "restore": backend.last_report, "extra_physics_time": 0.0})

    def check_carrying():
        task_boundary()
        assert len(backend.checkpoints) == 2
        assert "extinguisher_carried" in task_checkpoints.completed
        assert ur5._env is session.base_env
        assert ur5.is_carrying_extinguisher and ur5.is_extinguisher_pick_complete
        assert ur5._ext_active and ur5._ext_follow_mode
        assert ur5._ext_robot_name == carrier_name
        assert ur5._ext_target_prim == proxy_path
        assert ur5._ext_pick.carry_pose == saved_carry_pose
        assert ur5_component.capture_state() == saved_ur5_state
        assert stage.GetPrimAtPath(proxy_path).IsValid()
        assert UsdPhysics.RigidBodyAPI(prop).GetRigidBodyEnabledAttr().Get() is False
        assert UsdGeom.Imageable(prop).GetVisibilityAttr().Get() == UsdGeom.Tokens.invisible
        np.testing.assert_allclose(ur5._get_prim_pos(stage, proxy_path), saved_proxy_position, atol=1e-5, rtol=0)

    def advance_carrier():
        start_position = np.array(ur5._get_prim_pos(stage, proxy_path))
        root_velocity = carrier.data.root_vel_w.clone()
        root_velocity[:, 0] = 0.5
        carrier.write_root_velocity_to_sim(root_velocity)
        for unused in range(5):
            controller.request("step")
            assert controller.poll()
            ur5.update_extinguisher_grab(stage)
            session.env.step({})
            ur5.update_extinguisher_visual_follow(stage)
            controller.after_step()
            assert not controller.poll()
            np.testing.assert_allclose(
                ur5._get_prim_pos(stage, proxy_path), ur5._get_ee_pos_cached(arm, carrier_name),
                atol=1e-5, rtol=0,
            )
        distance = float(np.linalg.norm(np.array(ur5._get_prim_pos(stage, proxy_path)) - start_position))
        assert distance > 1e-4, distance
        return distance

    initial_motion = advance_carrier()
    restore(carrying_checkpoint, "carrying_after_motion")
    check_carrying()
    restored_motion = advance_carrier()
    restore(before_pickup, "before_pickup_from_carrying")
    assert not ur5.is_carrying_extinguisher and not ur5._ext_active
    assert ur5._ext_pick is None
    assert not stage.GetPrimAtPath(proxy_path).IsValid()
    assert UsdPhysics.RigidBodyAPI(prop).GetRigidBodyEnabledAttr().Get() is True
    assert UsdGeom.Imageable(prop).GetVisibilityAttr().Get() == original_visibility
    task_boundary()
    assert len(backend.checkpoints) == 2
    assert "extinguisher_carried" not in task_checkpoints.completed
    restore(carrying_checkpoint, "carrying_proxy_recreated")
    check_carrying()
    assert ur5.release_extinguisher_grab(stage)
    assert not ur5.is_carrying_extinguisher
    assert ur5._set_prim_world_translate(stage, proxy_path, (0.0, 0.0, 1.0))
    restore(carrying_checkpoint, "carrying_after_release")
    check_carrying()
    release_restore_motion = advance_carrier()
    return {
        "cases": cases, "automatic_save_after_lift": observed_lift_without_checkpoint,
        "task_checkpoint_history": task_checkpoints.history,
        "proxy_motion_m": [initial_motion, restored_motion, release_restore_motion],
        "follow_checked_each_step": True,
        "pickup_fixture": "original extinguisher positioned at the end effector during GRASP",
        "trajectory_accuracy_verified": False,
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    worker = parser.add_mutually_exclusive_group(required=True)
    worker.add_argument("--worker", type=Path)
    worker.add_argument("--task-worker", type=Path)
    worker.add_argument("--force-worker", type=Path)
    args = parser.parse_args()
    if args.force_worker:
        _run_force_worker(args.force_worker)
    elif args.task_worker:
        _run_task_worker(args.task_worker)
    else:
        _run_worker(args.worker)
