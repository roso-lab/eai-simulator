import pytest

from demo.fire_rescue.runtime.branch_control import (
    AssignmentEdit,
    BranchController,
    BranchPhase,
    DEFAULT_ROBOT_KEYS,
    normalize_assignments,
)


def assignments():
    return {
        robot: {
            "subtask": f"task-{robot}",
            "colour": "blue",
            "target_xy": [1.0, 2.0],
        }
        for robot in DEFAULT_ROBOT_KEYS
    }


def test_normalize_preserves_robot_keys_and_rejects_unknown_robot():
    value = assignments()
    normalized = normalize_assignments(value)
    assert tuple(normalized) == DEFAULT_ROBOT_KEYS
    assert normalized["m20_1"]["target_xy"] == [1.0, 2.0]

    value["unknown_1"] = value.pop("carter_1")
    with pytest.raises(ValueError, match="unknown robot"):
        normalize_assignments(value)


def test_normalize_rejects_duplicate_robot_and_missing_fields():
    pairs = list(assignments().items())
    pairs.append(("m20_1", assignments()["m20_1"]))
    with pytest.raises(ValueError, match="duplicate robot"):
        normalize_assignments(pairs)

    value = assignments()
    del value["m20_2"]["colour"]
    with pytest.raises(ValueError, match="missing field.*colour"):
        normalize_assignments(value)


def test_fact_finish_waits_for_edit_and_branch_requires_edit():
    controller = BranchController()
    controller.enter_fact_running()
    controller.save_assignment_checkpoint(assignments(), "assignment-0")
    assert controller.phase is BranchPhase.WAITING_ASSIGNMENT_EDIT

    with pytest.raises(RuntimeError, match="assignment edit"):
        controller.start_branch()

    edit = controller.submit_assignment_edit(assignments())
    assert isinstance(edit, AssignmentEdit)
    assert controller.phase is BranchPhase.BRANCH_READY
    controller.start_branch()
    assert controller.phase is BranchPhase.BRANCH_RUNNING

    controller.finish({"success": True})
    assert controller.phase is BranchPhase.FINISHED
    assert controller.snapshot()["branch_result"] == {"success": True}


def test_snapshot_restore_round_trip_is_json_safe():
    controller = BranchController()
    controller.enter_fact_running()
    controller.save_assignment_checkpoint(assignments(), "assignment-0")
    controller.submit_assignment_edit(assignments())
    snapshot = controller.snapshot()
    restored = BranchController.restore(snapshot)
    assert restored.snapshot() == snapshot
    assert restored.phase is BranchPhase.BRANCH_READY

