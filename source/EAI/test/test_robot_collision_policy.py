from __future__ import annotations

from types import SimpleNamespace

import pytest

from EAI.physics.robot_collision import robot_collision_filter_pairs, robot_entity_roots


def test_default_policy_pairs_robots_only_within_each_environment():
    roots = {
        "go2_1": ("/World/envs/env_0/go2_1", "/World/envs/env_1/go2_1"),
        "b2_1": ("/World/envs/env_0/b2_1", "/World/envs/env_1/b2_1"),
        "g1_1": ("/World/envs/env_0/g1_1", "/World/envs/env_1/g1_1"),
    }
    pairs = robot_collision_filter_pairs(roots)
    assert len(pairs) == 6
    assert all(a.split("/envs/")[1].split("/", 1)[0] == b.split("/envs/")[1].split("/", 1)[0]
               for a, b in pairs)


def test_collision_policy_rejects_mismatched_environment_counts():
    with pytest.raises(ValueError, match="same environment count"):
        robot_collision_filter_pairs({"a": ("/World/envs/env_0/a",),
                                      "b": ("/World/envs/env_0/b", "/World/envs/env_1/b")})


def test_robot_entity_groups_mounted_articulations_and_uses_longest_prefix():
    def articulation(*paths):
        return SimpleNamespace(root_physx_view=SimpleNamespace(prim_paths=paths))

    articulations = {
        "robot_1": articulation("/World/envs/env_0/robot_1", "/World/envs/env_1/robot_1"),
        "robot_1_arm": articulation("/World/envs/env_0/robot_1_arm", "/World/envs/env_1/robot_1_arm"),
        "robot_10": articulation("/World/envs/env_0/robot_10", "/World/envs/env_1/robot_10"),
        "robot_10_sensor": articulation("/World/envs/env_0/robot_10_sensor", "/World/envs/env_1/robot_10_sensor"),
    }

    entities = robot_entity_roots(articulations, {"robot_1": object(), "robot_10": object()})

    assert entities["robot_1"] == (
        "/World/envs/env_0/robot_1",
        "/World/envs/env_0/robot_1_arm",
        "/World/envs/env_1/robot_1",
        "/World/envs/env_1/robot_1_arm",
    )
    assert entities["robot_10"] == (
        "/World/envs/env_0/robot_10",
        "/World/envs/env_0/robot_10_sensor",
        "/World/envs/env_1/robot_10",
        "/World/envs/env_1/robot_10_sensor",
    )


def test_unregistered_articulation_remains_an_independent_entity():
    articulation = lambda path: SimpleNamespace(
        root_physx_view=SimpleNamespace(prim_paths=(path,))
    )
    entities = robot_entity_roots(
        {"robot": articulation("/World/envs/env_0/robot"),
         "loose": articulation("/World/envs/env_0/loose")},
        {"robot": object()},
    )
    assert entities == {
        "loose": ("/World/envs/env_0/loose",),
        "robot": ("/World/envs/env_0/robot",),
    }
