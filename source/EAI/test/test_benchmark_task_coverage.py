from demo.benchmark.task_coverage import (
    load_coverage_matrix,
    validate_coverage_matrix,
    validate_m0_template_registry,
    validate_m1_template_registry,
)


def test_coverage_registry_has_five_scene_m0_m1_and_full_grid_counts():
    matrix = load_coverage_matrix()
    counts = matrix["counting_policy"]
    assert counts["current_goal_packages"] == 45
    assert counts["current_factor_pairs"] == 35
    assert counts["current_canonical_templates"] == 40
    assert counts["m0_target_canonical_templates"] == 20
    assert counts["m1_target_canonical_templates"] == 20
    assert counts["baseline_canonical_templates"] == 5
    assert counts["baseline_condition_packages"] == 45
    assert counts["conditions_per_canonical_template"] == 9
    assert counts["full_grid_canonical_templates"] == 45
    assert counts["full_grid_goal_packages"] == 405
    assert counts["full_grid_factor_pairs"] == 315
    assert set(matrix["scene_coverage"]) == {
        "factory", "warehouse_nvd", "modern_house_nvd", "city", "restaurant",
    }
    assert validate_coverage_matrix(matrix)["validated"] is True


def test_m0_and_m1_template_registries_remain_separate():
    m0 = validate_m0_template_registry()
    m1 = validate_m1_template_registry()
    assert m0["canonical_template_count"] == 20
    assert m1["canonical_template_count"] == 20
    assert m0["scoreable"] is False
    assert m1["scoreable"] is False
    assert m0["execution_validated"] is False
    assert m1["execution_validated"] is False
