from pathlib import Path


def test_restaurant_is_registered_as_a_selectable_runtime_scene():
    from EAI.hmrs_env.env_diy.catalog import scene_choices
    from EAI_assets.asset_requirements import resolve_selection

    assert ("restaurant", "Restaurant authoring scene") in scene_choices()
    graph = resolve_selection({"scene_key": "restaurant", "robots": []})
    requirement = next(item for item in graph.requirements if item.id == "scene:restaurant")
    assert requirement.relative_paths == (
        "scene/restaurant/Restaurant_Only.usda",
        "scene/restaurant/Assemblies/assembly_Restaurant.usd",
        "scene/restaurant/World_RestaurantDemopack.usd",
    )


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
    assert config.SOURCE_SHA256 == (
        "618470672c40d18b93adbbcdecf69aea98931ebc4d5e1f8d13e7a77ee67e653b"
    )
    assert len(config.UNRESOLVED_MANIFEST_SHA256) == 64
    assert len(config.USED_LAYER_MANIFEST_SHA256) == 64
    assert config.RUNTIME_SCALE == (0.01, 0.01, 0.01)
    assert config.RELEASE_STATUS == "authoring_draft"
    assert config.EXECUTION_VALIDATED is False
    assert config.SCOREABLE is False


def test_restaurant_effective_closure_categories_are_explicit():
    from demo.restaurant_benchmark.scene_audit import _unresolved_category

    assert _unresolved_category("/tmp/Assemblies/assembly_City.usd") == (
        "uncomposed_structural"
    )
    assert _unresolved_category("/tmp/Materials/CitySky1.exr") == (
        "uncomposed_environment"
    )
    assert _unresolved_category("OmniPBR.mdl") == "kit_core_mdl_module"
    assert _unresolved_category("/tmp/Assets/Vegetation/leaf.png") == (
        "uncomposed_custom_data_texture"
    )
    assert _unresolved_category("Materials/SteelWire/wire.png") == (
        "uncomposed_custom_data_texture"
    )
    assert _unresolved_category("/tmp/Materials/Base/Wood/Oak/Oak_N.png") == (
        "stale_custom_data_with_valid_default"
    )
    assert _unresolved_category("/tmp/unknown.usd") == "effective_unresolved"


def test_restaurant_effective_closure_fails_closed_on_source_change():
    from demo.restaurant_benchmark import config, scene_audit

    failures = scene_audit._evaluate_closure_policy(
        source_sha256="different-source",
        normalized_unresolved=[],
        used_layer_manifest_sha256=config.USED_LAYER_MANIFEST_SHA256,
        used_layer_failures=[],
        composition_errors=[],
        missing_core_modules=[],
    )

    assert "policy:source_sha256_mismatch" in failures


def test_restaurant_effective_closure_rejects_new_png_and_layer_changes():
    from demo.restaurant_benchmark import config, scene_audit

    failures = scene_audit._evaluate_closure_policy(
        source_sha256=config.SOURCE_SHA256,
        normalized_unresolved=["new/dependency/materials/Oak_N.png"],
        used_layer_manifest_sha256="changed-layer-manifest",
        used_layer_failures=[],
        composition_errors=[],
        missing_core_modules=[],
    )

    assert "policy:unresolved_manifest_mismatch" in failures
    assert "policy:used_layer_manifest_mismatch" in failures


def test_restaurant_native_probe_uses_the_scene_only_selection():
    from demo.restaurant_benchmark.native_probe import build_selection

    assert build_selection() == {
        "version": 1,
        "task_name": "restaurant_scene",
        "scene_key": "restaurant",
        "robots": [],
    }


def test_restaurant_native_probe_builds_a_bounded_carter_diagnostic_selection():
    from demo.restaurant_benchmark.native_probe import build_selection

    assert build_selection("carter") == {
        "version": 1,
        "task_name": "restaurant_scene",
        "scene_key": "restaurant",
        "robots": [
            {
                "type": "carter",
                "controller": {"mode": "default", "cfg": "CARTER_DIFF_CFG"},
                "attachments": [],
            }
        ],
    }
