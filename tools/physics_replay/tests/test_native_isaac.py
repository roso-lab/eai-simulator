"""Opt-in Factory regression for restoring after visual extinguisher pickup."""

import json
import os
from pathlib import Path
import subprocess
import sys
import traceback

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
    (tmp_path / "pickup_regression.json").write_text(json.dumps({
        "passed": True, "cases": cases, "prewrite_rejection_keeps_current_state": True,
        "trajectory_accuracy_verified": False,
    }, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", type=Path, required=True)
    _run_worker(parser.parse_args().worker)
