"""The deployment-realism modality matrix must load, count and stay worded."""

import copy

import pytest

from demo.benchmark.modality_matrix import (
    MODALITY_STATUSES,
    WORDING_BLACKLIST,
    load_modality_matrix,
    modality_matrix_statistics,
    validate_modality_matrix,
)


EXPECTED_COUNTS = {
    "factory": {"declared_realistic": 5, "realistic_missing": 0,
                "excluded_defensible": 2, "idealized_unknown": 0},
    "warehouse_nvd": {"declared_realistic": 6, "realistic_missing": 0,
                      "excluded_defensible": 2, "idealized_unknown": 0},
    "modern_house_nvd": {"declared_realistic": 6, "realistic_missing": 1,
                         "excluded_defensible": 2, "idealized_unknown": 0},
    "city": {"declared_realistic": 4, "realistic_missing": 3,
             "excluded_defensible": 1, "idealized_unknown": 0},
    "restaurant": {"declared_realistic": 3, "realistic_missing": 0,
                   "excluded_defensible": 0, "idealized_unknown": 5},
}


def test_tracked_matrix_loads_and_validates_with_expected_statistics():
    matrix = load_modality_matrix()
    statistics = modality_matrix_statistics(matrix)
    assert statistics["by_scene"] == EXPECTED_COUNTS
    assert statistics["totals"] == {
        "declared_realistic": 24, "realistic_missing": 4,
        "excluded_defensible": 7, "idealized_unknown": 5}
    assert statistics["row_count"] == sum(sum(counts.values()) for counts in EXPECTED_COUNTS.values())
    assert validate_modality_matrix(matrix)["validated"] is True


def test_every_row_carries_evidence_and_phase_decomposition_wording():
    matrix = load_modality_matrix()
    for scene, rows in matrix["scene_modality_audit"].items():
        for row in rows:
            assert row["evidence"], (scene, row["skill"])
            assert row["rationale"].strip()
            if row["status"] == "excluded_defensible":
                assert "阶段" in row["rationale"], (scene, row["skill"])
                assert "proposal" not in row
            if row["status"] == "realistic_missing":
                assert row.get("proposal", "").strip(), (scene, row["skill"])
            if row["status"] == "idealized_unknown":
                assert "idealized/unknown" in row["rationale"], (scene, row["skill"])


def test_restaurant_audit_preserves_solo_ground_closure_without_heterogeneity_claim():
    rows = load_modality_matrix()["scene_modality_audit"]["restaurant"]
    solo = [row for row in rows if row["status"] == "declared_realistic"]
    assert {(row["skill"], row["modality"]) for row in solo} == {
        ("ground_navigation", "ground-wheeled"),
        ("observe", "ground-wheeled"),
        ("relay", "ground-wheeled"),
    }
    assert all("单 Carter" in row["rationale"] for row in solo)
    assert all("不构成异构团队必要性" in row["rationale"] for row in rows)


def _matrix():
    return load_modality_matrix()


def _first_row(matrix, status):
    for rows in matrix["scene_modality_audit"].values():
        for row in rows:
            if row["status"] == status:
                return row
    raise AssertionError(f"matrix has no {status} row")


@pytest.mark.parametrize("word", WORDING_BLACKLIST)
def test_blacklisted_wording_in_rationale_is_rejected(word):
    matrix = _matrix()
    row = _first_row(matrix, "declared_realistic")
    row["rationale"] = row["rationale"] + f"；该机型{word}承担此阶段。"
    with pytest.raises(ValueError, match="forbidden wording"):
        validate_modality_matrix(matrix)


@pytest.mark.parametrize("word", WORDING_BLACKLIST)
def test_blacklisted_wording_in_proposal_is_rejected(word):
    matrix = _matrix()
    row = _first_row(matrix, "realistic_missing")
    row["proposal"] = f"提案：其余成员{word}参与。"
    with pytest.raises(ValueError, match="forbidden wording"):
        validate_modality_matrix(matrix)


def test_realistic_missing_without_proposal_is_rejected():
    matrix = _matrix()
    row = _first_row(matrix, "realistic_missing")
    del row["proposal"]
    with pytest.raises(ValueError, match="require a nonempty proposal"):
        validate_modality_matrix(matrix)


def test_excluded_defensible_without_phase_decomposition_is_rejected():
    matrix = _matrix()
    row = _first_row(matrix, "excluded_defensible")
    row["rationale"] = "该模态与现行声明契约不一致。"
    with pytest.raises(ValueError, match="phases"):
        validate_modality_matrix(matrix)


def test_unknown_status_is_rejected():
    matrix = _matrix()
    row = _first_row(matrix, "declared_realistic")
    row["status"] = "physically_impossible"
    with pytest.raises(ValueError, match="status"):
        validate_modality_matrix(matrix)
    assert "physically_impossible" not in MODALITY_STATUSES


def test_idealized_unknown_without_explicit_boundary_is_rejected():
    matrix = _matrix()
    row = _first_row(matrix, "idealized_unknown")
    row["rationale"] = row["rationale"].replace("idealized/unknown", "pending")
    with pytest.raises(ValueError, match="idealized/unknown"):
        validate_modality_matrix(matrix)


def test_row_without_evidence_is_rejected():
    matrix = _matrix()
    row = _first_row(matrix, "declared_realistic")
    row["evidence"] = []
    with pytest.raises(ValueError, match="evidence"):
        validate_modality_matrix(matrix)


def test_evidence_with_out_of_range_line_is_rejected():
    matrix = _matrix()
    row = _first_row(matrix, "declared_realistic")
    row["evidence"] = ["demo/benchmark/goal_driven_tasks.py:999999"]
    with pytest.raises(ValueError, match="line range outside"):
        validate_modality_matrix(matrix)


def test_evidence_with_missing_file_is_rejected():
    matrix = _matrix()
    row = _first_row(matrix, "declared_realistic")
    row["evidence"] = ["demo/benchmark/not_a_source_file.py:1"]
    with pytest.raises(ValueError, match="missing file"):
        validate_modality_matrix(matrix)


def test_undeclared_skill_vocabulary_is_rejected():
    matrix = _matrix()
    row = _first_row(matrix, "declared_realistic")
    row["skill"] = "firefighting_hose"
    with pytest.raises(ValueError, match="skill ontology"):
        validate_modality_matrix(matrix)


def test_duplicate_scene_row_identity_is_rejected():
    matrix = _matrix()
    rows = matrix["scene_modality_audit"]["factory"]
    duplicate = copy.deepcopy(rows[0])
    duplicate["rationale"] = duplicate["rationale"] + "（重复行）"
    rows.append(duplicate)
    with pytest.raises(ValueError, match="duplicate skill/phase/modality"):
        validate_modality_matrix(matrix)


def test_missing_scene_section_is_rejected():
    matrix = _matrix()
    del matrix["scene_modality_audit"]["city"]
    with pytest.raises(ValueError, match="every registered scene"):
        validate_modality_matrix(matrix)
