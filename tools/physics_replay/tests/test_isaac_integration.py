"""Opt-in Isaac Sim integration coverage for checkpoint restore."""

from __future__ import annotations

import os

import pytest


if os.environ.get("EAI_RUN_ISAAC_REPLAY") != "1":
    pytest.skip(
        "set EAI_RUN_ISAAC_REPLAY=1 to run the Isaac integration test",
        allow_module_level=True,
    )

pytest.importorskip("isaaclab")


def test_fixed_action_checkpoint_restore_and_continue():
    from simulator import SimulatorLaunchConfig, open_simulator_session
    from tools.physics_replay import PhysicsReplayer, ReplayRecorder, TargetSpec

    env_name = os.environ.get("EAI_REPLAY_TEST_ENV", "my_env")
    prim_path = os.environ.get(
        "EAI_REPLAY_TEST_TARGET", "/World/envs/env_0/go2_1/base"
    )
    config = SimulatorLaunchConfig(
        env=env_name,
        device=os.environ.get("EAI_REPLAY_DEVICE", "cpu"),
        headless=True,
        enable_ros_bridge_extension=False,
    )
    with open_simulator_session(config) as session:
        targets = (TargetSpec(prim_path, ("pose", "velocity")),)
        recorder = ReplayRecorder.from_session(
            session,
            targets,
            os.environ.get("EAI_REPLAY_OUTPUT", "tmp/replay/isaac_smoke"),
        )
        try:
            for _ in range(50):
                recorder.before_step({})
                session.env.step({})
                recorder.after_step(phase="integration")
        finally:
            recorder.close()
        report = PhysicsReplayer.from_session(session, targets).restore(
            str(recorder.writer.root), 20
        )
        assert report.ok, report.summary
        for _ in range(30):
            session.env.step({})
