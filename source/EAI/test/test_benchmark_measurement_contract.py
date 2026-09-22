"""Integration checks for the author-only measurement contract attachment."""

import json

from demo.benchmark.goal_driven_contract import validate_goal_bundle
from demo.benchmark.goal_driven_tasks import (
    build_goal_task_package,
    generate_goal_task_packages,
)
from demo.benchmark.goal_scene_registry import registered_goal_scenes


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


def test_default_generation_is_four_scene_complete():
    packages = generate_goal_task_packages()
    assert set(packages) == set(SCENES)


def test_goal_task_cli_uses_registered_city_scene(capsys):
    from demo.benchmark.goal_driven_tasks import main

    assert main(["--scene", "city"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert set(payload) == {"city"}
