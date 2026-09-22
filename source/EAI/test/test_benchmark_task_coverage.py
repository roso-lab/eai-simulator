from demo.benchmark.task_coverage import (
    load_coverage_matrix,
    validate_coverage_matrix,
    validate_m0_template_registry,
    validate_m1_template_registry,
)


def test_coverage_registry_has_four_scene_m0_parity():
    matrix = load_coverage_matrix()
    assert matrix["counting_policy"]["current_goal_packages"] == 36
    assert matrix["counting_policy"]["current_factor_pairs"] == 28
    assert matrix["counting_policy"]["current_canonical_templates"] == 32
    assert matrix["counting_policy"]["m0_target_canonical_templates"] == 16
    assert matrix["counting_policy"]["m1_target_canonical_templates"] == 32
    assert set(matrix["scene_coverage"]) == {"factory", "warehouse_nvd", "modern_house_nvd", "city"}
    assert validate_coverage_matrix(matrix)["validated"] is True


def test_m0_and_m1_template_registries_remain_separate():
    m0 = validate_m0_template_registry()
    m1 = validate_m1_template_registry()
    assert m0["canonical_template_count"] == 16
    assert m1["canonical_template_count"] == 16
    assert m0["scoreable"] is False
    assert m1["scoreable"] is False
    assert m0["execution_validated"] is False
    assert m1["execution_validated"] is False
