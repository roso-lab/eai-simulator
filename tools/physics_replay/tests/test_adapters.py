from types import SimpleNamespace

import numpy as np

from tools.physics_replay.adapters import IsaacLabStateAdapter
from tools.physics_replay.types import ResolvedTarget, TargetSpec


class FakeArticulation:
    def __init__(self):
        self.data = SimpleNamespace(
            root_pos_w=np.array([[1.0, 2.0, 3.0]], dtype=np.float32),
            root_quat_w=np.array([[1.0, 0.0, 0.0, 0.0]], dtype=np.float32),
            root_lin_vel_w=np.array([[0.1, 0.2, 0.3]], dtype=np.float32),
            root_ang_vel_w=np.array([[0.4, 0.5, 0.6]], dtype=np.float32),
            joint_pos=np.array([[0.7, 0.8]], dtype=np.float32),
            joint_vel=np.array([[0.9, 1.0]], dtype=np.float32),
            net_contact_forces_w=np.array([[[1.0, 2.0, 3.0]]], dtype=np.float32),
            applied_torque=np.array([[1.1, 1.2]], dtype=np.float32),
        )
        self.pose_writes = []
        self.velocity_writes = []
        self.joint_writes = []

    def write_root_pose_to_sim(self, value, env_ids):
        self.pose_writes.append((value, env_ids))

    def write_root_velocity_to_sim(self, value, env_ids):
        self.velocity_writes.append((value, env_ids))

    def write_joint_state_to_sim(self, position, velocity, effort, env_ids):
        self.joint_writes.append((position, velocity, effort, env_ids))


def _target():
    spec = TargetSpec(
        "/World/envs/env_0/robot",
        ("pose", "velocity", "joint_state", "contact_force", "joint_effort"),
    )
    return ResolvedTarget(
        target_id="target_0",
        spec=spec,
        kind="articulation",
        collection_name="robot",
        root_path=spec.prim_path,
        supported_channels=spec.channels,
        writable_channels=("pose", "velocity", "joint_state"),
    )


def test_adapter_reads_root_joint_contact_and_effort():
    robot = FakeArticulation()
    adapter = IsaacLabStateAdapter(
        SimpleNamespace(scene=SimpleNamespace(articulations={"robot": robot}, rigid_objects={}))
    )

    values = adapter.read(
        _target(),
        (
            "target_0/pose",
            "target_0/velocity",
            "target_0/joint_state",
            "target_0/contact_force",
            "target_0/joint_effort",
        ),
    )

    np.testing.assert_allclose(values["target_0/pose"], [1, 2, 3, 1, 0, 0, 0])
    np.testing.assert_allclose(values["target_0/velocity"], [0.1, 0.2, 0.3, 0.4, 0.5, 0.6])
    np.testing.assert_allclose(values["target_0/joint_state"], [0.7, 0.8, 0.9, 1.0])
    np.testing.assert_allclose(values["target_0/contact_force"], [1, 2, 3])
    np.testing.assert_allclose(values["target_0/joint_effort"], [1.1, 1.2])


def test_adapter_writes_root_pose_velocity_and_joint_state():
    robot = FakeArticulation()
    adapter = IsaacLabStateAdapter(
        SimpleNamespace(scene=SimpleNamespace(articulations={"robot": robot}, rigid_objects={}))
    )
    target = _target()
    adapter.write(
        target,
        {
            "target_0/pose": np.arange(7, dtype=np.float32),
            "target_0/velocity": np.arange(6, dtype=np.float32),
            "target_0/joint_state": np.arange(4, dtype=np.float32),
        },
    )

    assert len(robot.pose_writes) == 1
    assert len(robot.velocity_writes) == 1
    assert len(robot.joint_writes) == 1
    np.testing.assert_array_equal(robot.pose_writes[0][0], np.arange(7, dtype=np.float32)[None, :])
    np.testing.assert_array_equal(robot.joint_writes[0][0], np.arange(2, dtype=np.float32)[None, :])
    np.testing.assert_array_equal(robot.joint_writes[0][1], np.arange(2, 4, dtype=np.float32)[None, :])
