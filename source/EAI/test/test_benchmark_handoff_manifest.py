import pytest
import json

from demo.benchmark.algorithm_adapter import DEFAULT_CONTROL_STACK
from demo.benchmark.handoff_manifest import (
    SCENE_KEYS,
    build_four_scene_handoff,
    validate_four_scene_handoff,
    write_four_scene_handoff,
    verify_four_scene_handoff,
)


def test_four_scene_handoff_has_uniform_authoring_shape_and_fixed_controls():
    report = build_four_scene_handoff()
    assert report["scene_keys"] == list(SCENE_KEYS)
    assert report["all_handoff_ready"] is True
    assert report["all_authoring_passed"] is True
    assert report["all_execution_pending"] is True
    assert report["scoreable"] is False
    assert all(row["package_shape"] == [
        "public_task", "participant_api", "baseline_manifest", "hidden_evaluator",
    ] for row in report["scenes"])
    assert all(row["control_stack"] == DEFAULT_CONTROL_STACK for row in report["scenes"])
    assert [row["task_id"] for row in report["scenes"]] == [
        "FACTORY-GOAL-01", "WAREHOUSE-GOAL-01", "MODERN_HOUSE-GOAL-01", "CITY-GOAL-01",
    ]


def test_handoff_rejects_control_stack_or_execution_claim_drift():
    report = build_four_scene_handoff()
    report["scenes"][0]["control_stack"]["navigation"] = "custom"
    with pytest.raises(ValueError, match="control stack"):
        validate_four_scene_handoff(report)
    report = build_four_scene_handoff()
    report["scenes"][3]["scoreable"] = True
    with pytest.raises(ValueError, match="execution or scoreability"):
        validate_four_scene_handoff(report)


@pytest.mark.parametrize("field,value", [
    ("task_id", "WRONG-GOAL-01"), ("artifact_sha256", {}),
    ("participant_api", "failed"), ("hidden_boundary", "failed"),
    ("handoff_ready", False), ("runtime_boundary", "ParticipantSession"),
    ("execution_blockers", []), ("package_shape", []),
])
def test_reaudit_rejects_forged_or_stale_report_fields(field, value):
    report = build_four_scene_handoff()
    report["scenes"][0][field] = value
    with pytest.raises(ValueError, match="re-audited"):
        validate_four_scene_handoff(report)


def test_export_reads_actual_bundle_files_and_does_not_overwrite(tmp_path):
    root = tmp_path / "handoff"
    report = write_four_scene_handoff(root)
    assert verify_four_scene_handoff(root) == report
    assert {row["runtime_boundary"] for row in report["scenes"]} == {
        "demo.benchmark.goal_session.GoalSession"}
    for scene in SCENE_KEYS:
        assert (root / scene / "release/participant_api.json").is_file()
        assert not (root / scene / "release/hidden_evaluator.json").exists()
    with pytest.raises(FileExistsError):
        write_four_scene_handoff(root)
    public_path = root / "city/release/public_task.json"
    public = json.loads(public_path.read_text())
    public["mission"]["goal"] += " tampered"
    public_path.write_text(json.dumps(public))
    with pytest.raises(ValueError):
        verify_four_scene_handoff(root)


def test_handoff_does_not_accept_report_without_complete_bundles():
    report = build_four_scene_handoff()
    with pytest.raises(ValueError, match="exactly four"):
        validate_four_scene_handoff(report, bundles={})


def test_readback_rejects_private_artifact_copied_into_public_release(tmp_path):
    import shutil
    root = tmp_path / "handoff"
    write_four_scene_handoff(root)
    shutil.copyfile(root / "city/evaluator/hidden_evaluator.json",
                    root / "city/release/hidden_evaluator.json")
    with pytest.raises(ValueError, match="public/private boundary"):
        verify_four_scene_handoff(root)


def test_handoff_rejects_symlink_parent_for_read_and_write(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    write_four_scene_handoff(real / "handoff")
    with pytest.raises(ValueError, match="symlinks"):
        verify_four_scene_handoff(alias / "handoff")
    with pytest.raises(ValueError, match="symlinks"):
        write_four_scene_handoff(alias / "new")
    assert not (real / "new").exists()
