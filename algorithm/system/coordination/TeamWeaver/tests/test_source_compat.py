"""Independent source overlay tests; no native TeamWeaver execution."""
from pathlib import Path
import hashlib
import json

import pytest

from algorithm.system.coordination.TeamWeaver.source_compat import (
    PATCHES, SourceCompatibilityError, apply_compatibility_overlay,
)


ROOT = Path("/home/airs/.cache/heterocoord/upstream/teamweaver").resolve()


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def source_root(tmp_path):
    # Copy only the package into a test checkout root; the production source is
    # read-only input and the fixture also verifies package completeness.
    import shutil
    shutil.copytree(ROOT / "TeamWeaver", tmp_path / "TeamWeaver")
    return tmp_path


def test_real_known_sources_match_audit():
    for patch in PATCHES:
        path = ROOT / patch["relative"]
        assert sha(path) == patch["before_sha256"]
        assert path.read_text().count(patch["old"]) == 1


def test_overlay_copies_full_package_patches_only_known_lines_and_writes_receipt(tmp_path):
    receipt = apply_compatibility_overlay(ROOT, tmp_path / "overlay")
    target = tmp_path / "overlay" / "TeamWeaver"
    assert receipt["source_unchanged"] is True
    assert receipt["source_file_count"] == receipt["copied_file_count"]
    assert receipt["semantic_change"] is False
    assert len(receipt["patches"]) == 2
    assert Path(receipt["receipt_path"]).is_file()
    for patch, expected in zip(receipt["patches"], PATCHES):
        assert patch["file"] == str(expected["relative"])
        assert patch["before_sha256"] == expected["before_sha256"]
        assert patch["replacement_count"] == 1
        assert any(line.startswith("-") and "iflen(" in line for line in patch["diff"].splitlines())
        assert any(line.startswith("+") and "if len(" in line for line in patch["diff"].splitlines())
        assert sha(tmp_path / "overlay" / patch["file"]) == patch["after_sha256"]
        assert "iflen(" not in (tmp_path / "overlay" / patch["file"]).read_text()
    assert json.loads(Path(receipt["receipt_path"]).read_text())["patches"] == receipt["patches"]


def test_original_source_bytes_are_unchanged(tmp_path):
    before = {p: sha(ROOT / p) for p in (patch["relative"] for patch in PATCHES)}
    apply_compatibility_overlay(ROOT, tmp_path / "overlay")
    assert {p: sha(ROOT / p) for p in before} == before


def test_unknown_target_source_rejected_before_copy(tmp_path):
    import shutil
    checkout = tmp_path / "checkout"
    shutil.copytree(ROOT / "TeamWeaver", checkout / "TeamWeaver")
    path = checkout / PATCHES[0]["relative"]
    path.write_text(path.read_text().replace(PATCHES[0]["old"], "if len(self.execution_history['actions']) > 99", 1))
    with pytest.raises(SourceCompatibilityError, match="unknown source"):
        apply_compatibility_overlay(checkout, tmp_path / "overlay")
    assert not (tmp_path / "overlay" / "TeamWeaver").exists()


def test_unknown_extra_iflen_rejected(tmp_path):
    import shutil
    checkout = tmp_path / "checkout"
    shutil.copytree(ROOT / "TeamWeaver", checkout / "TeamWeaver")
    path = checkout / PATCHES[1]["relative"]
    path.write_text(path.read_text() + "\niflen(extra)\n")
    with pytest.raises(SourceCompatibilityError, match="unknown source"):
        apply_compatibility_overlay(checkout, tmp_path / "overlay")


def test_destination_collision_rejected_without_touching_existing_tree(tmp_path):
    target = tmp_path / "overlay" / "TeamWeaver"
    target.mkdir(parents=True)
    marker = target / "marker"
    marker.write_text("keep")
    with pytest.raises(SourceCompatibilityError, match="already exists"):
        apply_compatibility_overlay(ROOT, tmp_path / "overlay")
    assert marker.read_text() == "keep"


def test_compile_report_is_diagnostic_and_other_syntax_errors_are_not_repaired(tmp_path):
    import shutil
    checkout = tmp_path / "checkout"
    shutil.copytree(ROOT / "TeamWeaver", checkout / "TeamWeaver")
    extra = checkout / "TeamWeaver" / "unrelated_syntax_error.py"
    extra.write_text("def broken(:\n    pass\n")
    receipt = apply_compatibility_overlay(checkout, tmp_path / "overlay")
    assert receipt["compile"]["all_syntax_valid"] is False
    assert any(item["file"].endswith("unrelated_syntax_error.py") for item in receipt["compile"]["syntax_failures"])
    assert (tmp_path / "overlay" / "TeamWeaver" / "unrelated_syntax_error.py").read_text() == extra.read_text()


def test_missing_known_file_rejected(tmp_path):
    import shutil
    checkout = tmp_path / "checkout"
    shutil.copytree(ROOT / "TeamWeaver", checkout / "TeamWeaver")
    (checkout / PATCHES[1]["relative"]).unlink()
    with pytest.raises(SourceCompatibilityError, match="missing"):
        apply_compatibility_overlay(checkout, tmp_path / "overlay")


def test_disjoint_paths_required(source_root):
    for destination in (source_root, source_root / "overlay", source_root.parent):
        with pytest.raises(SourceCompatibilityError, match="disjoint"):
            apply_compatibility_overlay(source_root, destination)


def test_unrelated_destination_files_preserved(tmp_path):
    destination = tmp_path / "overlay"
    destination.mkdir()
    marker = destination / "keep.txt"
    marker.write_text("preserve")
    with pytest.raises(SourceCompatibilityError, match="not empty"):
        apply_compatibility_overlay(ROOT, destination)
    assert marker.read_text() == "preserve"


def test_symlink_rejected_without_writing_source(source_root, tmp_path):
    link = source_root / "TeamWeaver" / "outside.py"
    link.symlink_to(ROOT / PATCHES[0]["relative"])
    with pytest.raises(SourceCompatibilityError, match="symlink"):
        apply_compatibility_overlay(source_root, tmp_path.parent / (tmp_path.name + "-overlay"))


def test_known_package_full_compile_and_only_two_byte_changes(tmp_path):
    receipt = apply_compatibility_overlay(ROOT, tmp_path / "overlay")
    assert receipt["compile"]["all_syntax_valid"] is True
    assert receipt["compile"]["python_files"] > 2
    changed = {name for name, digest in receipt["source_manifest_sha256"].items()
               if sha(tmp_path / "overlay" / name) != digest}
    assert changed == {str(p["relative"]) for p in PATCHES}
    for patch in receipt["patches"]:
        expected = next(p for p in PATCHES if str(p["relative"]) == patch["file"])
        assert patch["after_sha256"] == expected["after_sha256"]
    assert json.loads(Path(receipt["receipt_path"]).read_text()) == receipt
