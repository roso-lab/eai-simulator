"""Integration checks for the author-only measurement contract attachment."""

import json

from demo.benchmark.goal_driven_contract import validate_goal_bundle
from demo.benchmark.goal_driven_tasks import (
    build_goal_task_package,
    generate_goal_task_packages,
)
from demo.benchmark.goal_scene_registry import registered_goal_scenes
from demo.benchmark.measurement_contract import AUTHORING_ONLY_SCENE_KEYS, SCENE_KEYS


SCENES = tuple(row["scene_key"] for row in registered_goal_scenes()
               if row["package_kind"] == "goal_bundle")


def test_all_registered_goal_scenes_carry_unclassified_measurement_contract():
    for scene in SCENES:
        bundle = build_goal_task_package(scene)
        assert validate_goal_bundle(bundle)["validated"] is True
        hidden = bundle["hidden_evaluator"]
        assert hidden["measurement_status"] == "unclassified"
        assert hidden["measurement_contract"]["measurement_target"]["scene_key"] == scene
        assert "measurement_contract" not in bundle["public_task"]
        assert "witness_plans" not in bundle["public_task"]


def test_default_generation_is_five_scene_complete():
    packages = generate_goal_task_packages()
    assert set(packages) == set(SCENES)


def test_registry_includes_restaurant_without_promoting_release_gate():
    registrations = {row["scene_key"]: row for row in registered_goal_scenes()}
    assert tuple(SCENES) == SCENE_KEYS
    assert AUTHORING_ONLY_SCENE_KEYS == ()
    assert registrations["restaurant"]["package_kind"] == "goal_bundle"
    assert registrations["restaurant"]["execution_status"] == "external_pending"
    bundle = build_goal_task_package("restaurant")
    assert bundle["hidden_evaluator"]["measurement_contract"]["measurement_target"]["scene_key"] == "restaurant"
    assert bundle["hidden_evaluator"]["release_gate"]["scoreable"] is False


def test_goal_task_cli_uses_registered_city_scene(capsys):
    from demo.benchmark.goal_driven_tasks import main

    assert main(["--scene", "city"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert set(payload) == {"city"}
