import pytest
import queue

from demo.fire_rescue.runtime.branch_control import (
    AssignmentEdit,
    BranchController,
    BranchPhase,
    DEFAULT_ROBOT_KEYS,
    normalize_assignments,
)
from demo.fire_rescue.runtime.experiment_loop import (
    _DashStub,
    _discard_stale_emos_results,
    run_robot_factory_platform,
)
from demo.fire_rescue.main import validate_fire_rescue_cli_args


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


def test_assignment_checkpoint_can_be_saved_while_fact_run_continues():
    controller = BranchController()
    controller.save_assignment_checkpoint(
        assignments(), "assignment-0", await_edit=False
    )
    assert controller.phase is BranchPhase.FACT_RUNNING
    controller.finish({"success": True})
    assert controller.phase is BranchPhase.WAITING_ASSIGNMENT_EDIT


def test_headless_dashboard_stub_exposes_replay_branch_contract():
    dashboard = _DashStub()
    assert dashboard.get_pending_branch_assignment_edits() == []
    assert dashboard.get_pending_branch_starts() == []
    dashboard.update_replay_state("waiting_assignment_edit")


def test_interactive_branch_rejects_headless_cli():
    with pytest.raises(ValueError, match="requires a dashboard"):
        validate_fire_rescue_cli_args(
            type("Args", (), {"branch_interactive": True, "headless": True})()
        )


def test_interactive_branch_rejects_headless_programmatic_entrypoint():
    with pytest.raises(ValueError, match="requires a dashboard"):
        run_robot_factory_platform(
            simulation_app=None,
            env=None,
            base_env=None,
            env_cfg=None,
            args_cli=type("Args", (), {"branch_interactive": True, "headless": True})(),
            emos_mgr=None,
            scenario=None,
            map_yaml="",
            repo_root=None,
            device="cpu",
            num_envs=1,
            possible_agents=[],
            auto_fire_delay_s=0.0,
            interpret_factory_tasks_fn=lambda *_: [],
        )


def test_branch_restore_discards_stale_emos_results():
    manager = type("Manager", (), {})()
    manager._result_queue = queue.Queue()
    manager._result_queue.put({"stale": True})
    manager._last_result = {"stale": True}
    manager._fallback_dispatched_for_round = False
    manager._lock = __import__("threading").Lock()

    _discard_stale_emos_results(manager)

    assert manager._result_queue.empty()
    assert manager._last_result is None
    assert manager._fallback_dispatched_for_round is True
