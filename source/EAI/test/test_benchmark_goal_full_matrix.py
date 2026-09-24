"""Full-matrix generation and fail-closed readback tests."""

import json
import os
import shutil

import pytest

from demo.benchmark.goal_full_matrix import (
    EXPECTED_CANONICAL_TEMPLATES,
    EXPECTED_FACTOR_PAIRS,
    EXPECTED_PACKAGES,
    LEGACY_SUBSET_FACTOR_PAIRS,
    LEGACY_SUBSET_PACKAGES,
    verify_goal_full_matrix,
    write_goal_full_matrix,
)
from demo.benchmark.goal_suite import main as goal_suite_main
from demo.benchmark.provenance import file_sha256


def _save(path, value):
    _save_text(
        path,
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n",
    )


def _save_text(path, value):
    temporary = path.with_name(path.name + ".replacement")
    temporary.write_text(value, encoding="utf-8")
    path.unlink(missing_ok=True)
    temporary.replace(path)


@pytest.fixture(scope="module")
def full_matrix(tmp_path_factory):
    root = tmp_path_factory.mktemp("goal-full-matrix") / "matrix"
    result = write_goal_full_matrix(root)
    assert result["package_count"] == EXPECTED_PACKAGES
    assert result["factor_pair_count"] == EXPECTED_FACTOR_PAIRS
    return root


@pytest.fixture
def matrix_copy(full_matrix, tmp_path):
    root = tmp_path / "matrix"
    shutil.copytree(full_matrix, root, copy_function=os.link)
    return root


def test_full_matrix_physically_materializes_exact_grid_and_pairs(full_matrix, capsys):
    assert goal_suite_main([
        "full-verify", "--output-root", str(full_matrix), "--verify-sources",
    ]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result == {
        "validated": True,
        "canonical_template_count": EXPECTED_CANONICAL_TEMPLATES,
        "package_count": EXPECTED_PACKAGES,
        "factor_pair_count": EXPECTED_FACTOR_PAIRS,
        "legacy_subset_package_count": LEGACY_SUBSET_PACKAGES,
        "legacy_subset_factor_pair_count": LEGACY_SUBSET_FACTOR_PAIRS,
        "execution_validated": False,
        "business_outcomes_validated": False,
        "scoreable": False,
    }
    report = json.loads((full_matrix / "audit_report.json").read_text())
    package_dirs = {row["path"] for row in report["packages"]}
    assert len(package_dirs) == EXPECTED_PACKAGES
    assert all((full_matrix / path / "release/public_task.json").is_file()
               for path in package_dirs)
    assert len(json.loads((full_matrix / report["factor_pairs"]).read_text())) == EXPECTED_FACTOR_PAIRS


def test_full_matrix_keeps_legacy_subset_and_measurement_claim_boundary(full_matrix):
    report = json.loads((full_matrix / "audit_report.json").read_text())
    subset = report["legacy_condition_subset"]
    assert subset["identity"] == "baseline_templates_x_nine_conditions"
    assert subset["package_count"] == LEGACY_SUBSET_PACKAGES
    assert subset["factor_pair_count"] == LEGACY_SUBSET_FACTOR_PAIRS
    assert len(subset["package_ids"]) == LEGACY_SUBSET_PACKAGES
    assert all(".baseline/" in package_id for package_id in subset["package_ids"])
    assert report["variant_semantics"] == {
        "kind": "measurement_metadata_over_shared_scene_business_goals",
        "independent_business_goals_claimed": False,
        "package_count_is_not_an_independent_business_goal_count": True,
    }
    freeze = json.loads((full_matrix / report["freeze_manifest"]).read_text())
    source_files = next(iter(freeze["source_bindings"].values()))["files"]
    assert any(path.endswith("/goal_full_matrix.py") for path in source_files)
    assert len(freeze["packages"]) == EXPECTED_PACKAGES


@pytest.mark.parametrize("mutation", ["missing", "duplicate_record"])
def test_full_matrix_rejects_missing_or_duplicate_packages(matrix_copy, mutation):
    report_path = matrix_copy / "audit_report.json"
    report = json.loads(report_path.read_text())
    if mutation == "missing":
        target = matrix_copy / report["packages"][0]["path"] / "release/public_task.json"
        target.unlink()
    else:
        report["packages"].append(dict(report["packages"][0]))
        report["package_count"] += 1
        _save(report_path, report)
    with pytest.raises(ValueError, match="inventory|duplicate"):
        verify_goal_full_matrix(matrix_copy)


def test_full_matrix_rejects_tampered_bundle_bytes(matrix_copy):
    target = next(matrix_copy.glob("packages/*/*/*/*/evaluator/hidden_evaluator.json"))
    hidden = json.loads(target.read_text())
    hidden["factor_effect"]["effect_status"] = "forged"
    _save(target, hidden)
    with pytest.raises(ValueError, match="hash mismatch"):
        verify_goal_full_matrix(matrix_copy)


def test_full_matrix_rejects_private_file_in_public_release_even_if_inventoried(matrix_copy):
    release = next(matrix_copy.glob("packages/*/*/*/*/release"))
    target = release / "hidden_evaluator.json"
    _save(target, {"private": True})
    report_path = matrix_copy / "audit_report.json"
    report = json.loads(report_path.read_text())
    relative = target.relative_to(matrix_copy).as_posix()
    report["artifact_sha256"][relative] = file_sha256(target)
    _save(report_path, report)
    with pytest.raises(ValueError, match="protected public/author boundary"):
        verify_goal_full_matrix(matrix_copy)


def test_full_matrix_rejects_duplicate_json_keys(matrix_copy):
    report_path = matrix_copy / "audit_report.json"
    text = report_path.read_text()
    _save_text(report_path, text.replace(
        '"schema": "eai.benchmark.goal_full_matrix_audit",',
        '"schema": "eai.benchmark.goal_full_matrix_audit",\n  "schema": "forged",',
        1,
    ))
    with pytest.raises(ValueError, match="duplicate JSON key"):
        verify_goal_full_matrix(matrix_copy)
