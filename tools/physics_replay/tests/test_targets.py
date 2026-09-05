from types import SimpleNamespace

import pytest

from tools.physics_replay.targets import TargetResolver
from tools.physics_replay.types import TargetSpec


def _fake_env():
    robot = SimpleNamespace(
        cfg=SimpleNamespace(prim_path="/World/envs/env_.* /go2_1".replace(" ", "")),
        root_physx_view=SimpleNamespace(
            prim_paths=["/World/envs/env_0/go2_1/base"]
        ),
        data=SimpleNamespace(
            root_pos_w=True,
            root_quat_w=True,
            root_lin_vel_w=True,
            root_ang_vel_w=True,
            joint_pos=True,
            joint_vel=True,
            net_contact_forces_w=True,
            applied_torque=True,
            body_pos_w=True,
            body_quat_w=True,
            body_lin_vel_w=True,
            body_ang_vel_w=True,
        ),
    )
    robot.find_bodies = (
        lambda name, preserve_order=True: ([0], [name])
        if name == "base"
        else ([], [])
    )
    robot.write_root_pose_to_sim = lambda *args: None
    robot.write_root_velocity_to_sim = lambda *args: None
    robot.write_joint_state_to_sim = lambda *args: None
    return SimpleNamespace(
        scene=SimpleNamespace(articulations={"go2_1": robot}, rigid_objects={})
    )


def test_resolver_maps_root_and_link_targets():
    resolver = TargetResolver(_fake_env())
    root = resolver.resolve(
        TargetSpec("/World/envs/env_0/go2_1", ("pose", "joint_state"))
    )
    link = resolver.resolve(
        TargetSpec("/World/envs/env_0/go2_1/base", ("pose", "contact_force"))
    )
    assert root.kind == "articulation"
    assert root.writable_channels == ("pose", "joint_state")
    assert link.kind == "link"
    assert link.body_index == 0
    assert link.writable_channels == ()


def test_resolver_rejects_missing_paths_and_unavailable_channels():
    resolver = TargetResolver(_fake_env())
    with pytest.raises(ValueError, match="does not match"):
        resolver.resolve(TargetSpec("/World/envs/env_0/missing", ("pose",)))
    del resolver._articulations["go2_1"].data.net_contact_forces_w
    with pytest.raises(ValueError, match="contact_force"):
        resolver.resolve(
            TargetSpec("/World/envs/env_0/go2_1/base", ("contact_force",))
        )


def test_resolver_maps_rigid_object_root():
    cube = SimpleNamespace(
        cfg=SimpleNamespace(prim_path="/World/envs/env_0/cube"),
        data=SimpleNamespace(
            root_pos_w=True,
            root_quat_w=True,
            root_lin_vel_w=True,
            root_ang_vel_w=True,
        ),
    )
    env = SimpleNamespace(
        scene=SimpleNamespace(articulations={}, rigid_objects={"cube": cube})
    )
    resolved = TargetResolver(env).resolve(
        TargetSpec("/World/envs/env_0/cube", ("pose", "velocity"))
    )
    assert resolved.kind == "rigid_object"
    assert resolved.collection_name == "cube"


def test_resolver_normalizes_embedded_env_namespace():
    robot = SimpleNamespace(
        cfg=SimpleNamespace(prim_path="/World/{ENV_REGEX_NS}/go2_1"),
        root_physx_view=SimpleNamespace(
            prim_paths=["/World/envs/env_0/go2_1/base"]
        ),
        data=SimpleNamespace(root_pos_w=True, root_quat_w=True),
    )
    env = SimpleNamespace(
        scene=SimpleNamespace(articulations={"go2_1": robot}, rigid_objects={})
    )
    resolved = TargetResolver(env).resolve(
        TargetSpec("/World/envs/env_0/go2_1", ("pose",))
    )
    assert resolved.kind == "articulation"


def test_resolver_does_not_mark_read_only_object_as_writable():
    robot = SimpleNamespace(
        cfg=SimpleNamespace(prim_path="/World/envs/env_0/robot"),
        data=SimpleNamespace(
            root_pos_w=True,
            root_quat_w=True,
            root_lin_vel_w=True,
            root_ang_vel_w=True,
        ),
    )
    env = SimpleNamespace(
        scene=SimpleNamespace(articulations={"robot": robot}, rigid_objects={})
    )
    resolved = TargetResolver(env).resolve(
        TargetSpec("/World/envs/env_0/robot", ("pose", "velocity"))
    )
    assert resolved.writable_channels == ()


def test_resolver_rejects_multi_environment_and_invalid_env_id():
    base = _fake_env()
    base.num_envs = 2
    with pytest.raises(ValueError, match="num_envs=1"):
        TargetResolver(base)

    base = _fake_env()
    base.num_envs = 1
    with pytest.raises(ValueError, match="env_id"):
        TargetResolver(base).resolve(
            TargetSpec("/World/envs/env_0/go2_1", ("pose",), env_id=1)
        )


def test_resolver_rejects_link_path_that_is_not_a_usd_prim():
    class FakeStage:
        def GetPrimAtPath(self, path):
            return SimpleNamespace(
                IsValid=lambda: path == "/World/envs/env_0/go2_1"
            )

    base = _fake_env()
    base.stage = FakeStage()
    with pytest.raises(ValueError, match="does not exist"):
        TargetResolver(base).resolve(
            TargetSpec("/World/envs/env_0/go2_1/not_parent/base", ("pose",))
        )


def test_resolver_uses_physx_link_paths_when_stage_is_unavailable():
    base = _fake_env()
    base.scene.articulations["go2_1"].root_physx_view.link_paths = [
        ["/World/envs/env_0/go2_1/base"]
    ]
    with pytest.raises(ValueError, match="does not match"):
        TargetResolver(base).resolve(
            TargetSpec("/World/envs/env_0/go2_1/not_parent/base", ("pose",))
        )
