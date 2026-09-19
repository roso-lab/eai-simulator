"""Matrix publication checks retain ownership, split and information boundaries."""

import json

import pytest

from demo.benchmark.goal_scene_registry import (
    compile_registered_goal_scene, goal_scene_registration, registered_goal_scenes,
)
from demo.benchmark.goal_suite import main, verify_goal_suite, write_goal_suite
from demo.benchmark.provenance import file_sha256


def _save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False), encoding="utf-8")


@pytest.fixture(scope="module")
def one_scene_suite(tmp_path_factory):
    root = tmp_path_factory.mktemp("goal-suite") / "matrix"
    summary = write_goal_suite(root, scene_keys=["factory"])
    assert summary["package_count"] == 9
    assert summary["factor_pair_count"] == 7
    return root


@pytest.fixture
def suite_copy(one_scene_suite, tmp_path):
    import shutil
    root = tmp_path / "suite"
    shutil.copytree(one_scene_suite, root)
    return root


def test_city_registration_does_not_claim_a_full_goal_bundle():
    scenes = {row["scene_key"]: row for row in registered_goal_scenes()}
    assert set(scenes) == {"factory", "warehouse_nvd", "modern_house_nvd", "city"}
    assert scenes["city"]["package_kind"] == "preparation_draft_not_goal_bundle"
    scenes["factory"]["package_kind"] = "forged"
    assert goal_scene_registration("factory")["package_kind"] == "goal_bundle"
    with pytest.raises(ValueError, match="prepared package"):
        compile_registered_goal_scene("city")
    with pytest.raises(ValueError, match="hidden treatments"):
        compile_registered_goal_scene("city", city_package_dir="unused", factor_level="delayed")


def test_registered_goal_scene_uses_existing_full_contract():
    item = compile_registered_goal_scene("factory")
    assert item["bundle"]["public_task"]["mission"]["scene_key"] == "factory"
    assert "candidate" not in item


def test_suite_roundtrip_keeps_existing_templates_reserved(one_scene_suite):
    assert verify_goal_suite(one_scene_suite, verify_sources=True) == {
        "validated": True, "package_count": 9, "candidate_count": 0,
        "factor_pair_count": 7, "scoreable": False, "held_out_validated": False,
    }
    report = json.loads((one_scene_suite / "audit_report.json").read_text())
    assert report["freeze_manifest"].startswith("authoring/")
    assert report["visibility"] == "author_only"
    for record in report["packages"]:
        release = one_scene_suite / record["path"] / "release"
        assert {p.name for p in release.iterdir()} == {
            "public_task.json", "participant_api.json", "baseline_manifest.json"}


def test_suite_never_overwrites_existing_output(one_scene_suite):
    report = one_scene_suite / "audit_report.json"
    digest = file_sha256(report)
    with pytest.raises(FileExistsError, match="overwrite"):
        write_goal_suite(one_scene_suite, scene_keys=["factory"])
    assert file_sha256(report) == digest


def test_changed_hidden_schedule_cannot_reuse_an_existing_audit(suite_copy):
    target = next(suite_copy.glob("packages/*/*/factory/evaluator/hidden_evaluator.json"))
    hidden = json.loads(target.read_text())
    hidden["hidden_event_schedule"]["communication_events"][0]["delay_s"] += 2
    _save(target, hidden)
    with pytest.raises(ValueError, match="hash mismatch"):
        verify_goal_suite(suite_copy)


def test_private_extra_file_in_release_is_rejected_even_with_updated_inventory(suite_copy):
    target = next(suite_copy.glob("packages/*/*/factory/release")) / "witness.json"
    _save(target, {"answer": "private assignment"})
    path = suite_copy / "audit_report.json"
    report = json.loads(path.read_text())
    report["artifact_sha256"][target.relative_to(suite_copy).as_posix()] = file_sha256(target)
    _save(path, report)
    with pytest.raises(ValueError, match="outside the reviewed public/author boundary"):
        verify_goal_suite(suite_copy)


def test_saved_success_flags_cannot_promote_candidate_to_scoring(suite_copy):
    path = suite_copy / "audit_report.json"
    report = json.loads(path.read_text())
    report["scoreable"] = True
    _save(path, report)
    with pytest.raises(ValueError, match="cannot claim"):
        verify_goal_suite(suite_copy)


def test_removing_a_factor_comparison_is_not_a_complete_matrix(suite_copy):
    path = suite_copy / "audit_report.json"
    report = json.loads(path.read_text())
    report["factor_pairs"].pop()
    report["factor_pair_count"] -= 1
    _save(path, report)
    with pytest.raises(ValueError, match="factor pair report"):
        verify_goal_suite(suite_copy)


def test_symlink_cannot_escape_an_audited_package(suite_copy, tmp_path):
    target = next(suite_copy.glob("packages/*/*/factory/release/public_task.json"))
    outside = tmp_path / "outside.json"
    outside.write_bytes(target.read_bytes())
    target.unlink()
    target.symlink_to(outside)
    with pytest.raises(ValueError, match="symlink"):
        verify_goal_suite(suite_copy)


@pytest.mark.parametrize("destination", ["private", "external", "missing"])
def test_release_directory_links_cannot_publish_unlisted_material(suite_copy, tmp_path, destination):
    release = next(suite_copy.glob("packages/*/*/factory/release"))
    if destination == "private":
        target = release.parent / "evaluator"
    else:
        target = tmp_path / destination
        if destination == "external":
            target.mkdir()
            (target / "answer.json").write_text('{"private_answer": true}')
    (release / "unlisted").symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        verify_goal_suite(suite_copy, verify_sources=True)


def test_audit_report_cannot_be_replaced_by_an_external_link(suite_copy, tmp_path):
    report = suite_copy / "audit_report.json"
    outside = tmp_path / "external-report.json"
    outside.write_bytes(report.read_bytes())
    report.unlink()
    report.symlink_to(outside)
    with pytest.raises(ValueError, match="symlink"):
        verify_goal_suite(suite_copy, verify_sources=True)


def test_suite_root_cannot_be_an_alias(one_scene_suite, tmp_path):
    alias = tmp_path / "suite-alias"
    alias.symlink_to(one_scene_suite, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        verify_goal_suite(alias)


def test_failed_generation_cleans_only_owned_staging(tmp_path, monkeypatch):
    import demo.benchmark.goal_suite as suite
    preserved = tmp_path / "other-developer.txt"
    preserved.write_text("preserve")

    def unavailable(*args, **kwargs):
        raise ValueError("invalid author input")

    monkeypatch.setattr(suite, "build_goal_task_package", unavailable)
    with pytest.raises(ValueError, match="invalid author input"):
        write_goal_suite(tmp_path / "failed", scene_keys=["factory"])
    assert list(tmp_path.iterdir()) == [preserved]


def test_cli_lists_all_task_sources_without_assets(capsys):
    assert main(["list"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["scope"] == "task_generation_not_simulator_activation"
    assert "city" in {row["scene_key"] for row in output["scenes"]}
