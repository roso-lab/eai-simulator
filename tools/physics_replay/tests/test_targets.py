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
