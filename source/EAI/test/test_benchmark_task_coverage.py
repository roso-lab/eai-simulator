from demo.benchmark.task_coverage import load_coverage_matrix, validate_coverage_matrix


def test_coverage_registry_has_four_scene_m0_parity():
    matrix = load_coverage_matrix()
    assert matrix["counting_policy"]["current_goal_packages"] == 36
    assert matrix["counting_policy"]["current_factor_pairs"] == 28
    assert set(matrix["scene_coverage"]) == {"factory", "warehouse_nvd", "modern_house_nvd", "city"}
    assert validate_coverage_matrix(matrix)["validated"] is True
