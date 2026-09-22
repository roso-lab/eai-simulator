from pathlib import Path


def test_restaurant_is_registered_as_a_selectable_runtime_scene():
    from EAI.hmrs_env.env_diy.catalog import scene_choices
    from EAI_assets.asset_requirements import resolve_selection

    assert ("restaurant", "Restaurant authoring scene") in scene_choices()
    graph = resolve_selection({"scene_key": "restaurant", "robots": []})
    requirement = next(item for item in graph.requirements if item.id == "scene:restaurant")
    assert requirement.relative_paths == ("scene/restaurant/Restaurant_Only.usda",)


def test_restaurant_scene_only_preset_has_no_unvalidated_robot_spawn():
    import json

    root = Path(__file__).resolve().parents[3]
    preset = json.loads(
        (root / "source/EAI_hmrs/EAI_hmrs/envs/restaurant_scene.json").read_text(
            encoding="utf-8"
        )
    )
    assert preset == {
        "robots": [],
        "scene_key": "restaurant",
        "task_name": "restaurant_scene",
        "version": 1,
    }


def test_restaurant_release_gates_are_fail_closed():
    from demo.restaurant_benchmark import config

    assert config.SOURCE_METERS_PER_UNIT == 0.01
    assert config.RUNTIME_SCALE == (0.01, 0.01, 0.01)
    assert config.RELEASE_STATUS == "authoring_draft"
    assert config.EXECUTION_VALIDATED is False
    assert config.SCOREABLE is False


def test_restaurant_native_probe_uses_the_scene_only_selection():
    from demo.restaurant_benchmark.native_probe import build_selection

    assert build_selection() == {
        "version": 1,
        "task_name": "restaurant_scene",
        "scene_key": "restaurant",
        "robots": [],
    }
