import json
from types import SimpleNamespace

import pytest

from demo.fire_rescue.task_checkpoints import FIRE_RESCUE_CHECKPOINTS, TaskCheckpointScheduler, checkpoint_signals
from tools.physics_replay.live import CheckpointBusy, LiveCheckpoint, LiveReplayController


class Backend:
    def __init__(self, directory, scheduler):
        self.output_dir = directory
        self.scheduler = scheduler
        self.checkpoints = []
        self.states = {}
        self.busy = False
        self.fail = False

    def capture(self, step_index, label="Manual"):
        if self.busy:
            raise CheckpointBusy("worker running")
        if self.fail:
            raise ValueError("unsupported physics state")
        checkpoint = LiveCheckpoint(len(self.checkpoints), step_index, label)
        self.states[checkpoint.identifier] = self.scheduler.capture_state()
        self.checkpoints.append(checkpoint)
        return checkpoint

    def restore(self, identifier):
        self.scheduler.restore_state(self.states[identifier])
        return {"step_index": self.checkpoints[identifier].step_index, "readback_errors": {}}

    def read_physics(self):
        return {}


def setup_scheduler(tmp_path):
    scheduler = TaskCheckpointScheduler(tmp_path)
    controller = LiveReplayController(Backend(tmp_path, scheduler))
    return scheduler, controller


def test_task_owns_named_milestones_and_signals():
    signals = checkpoint_signals(
        assignment_applied=True,
        ur5=SimpleNamespace(is_extinguisher_pick_complete=False, is_carrying_extinguisher=True),
        rescue_channel_open=True, obstacle=SimpleNamespace(state=SimpleNamespace(obstacle_removed=True)),
        result="success",
    )
    assert set(signals) == {rule.key for rule in FIRE_RESCUE_CHECKPOINTS}
    assert not signals["extinguisher_carried"]
    assert all(value for key, value in signals.items() if key != "extinguisher_carried")


def test_automatic_save_runs_once_without_pausing_and_manual_save_remains(tmp_path):
    scheduler, controller = setup_scheduler(tmp_path)
    scheduler.tick(controller, {"assignment_applied": True})
    assert len(controller.backend.checkpoints) == 1
    assert controller.backend.checkpoints[0].label == "Tasks assigned"
    assert controller.backend.states[0]["completed"] == ["assignment_applied"]
    assert not controller.paused
    scheduler.tick(controller, {"assignment_applied": True})
    assert len(controller.backend.checkpoints) == 1
    controller.request("save")
    assert controller.poll()
    assert len(controller.backend.checkpoints) == 2


def test_busy_capture_keeps_trigger_step_and_retries_at_safe_boundary(tmp_path):
    scheduler, controller = setup_scheduler(tmp_path)
    controller.backend.busy = True
    controller.step_index = 5
    scheduler.tick(controller, {"extinguisher_carried": True})
    assert scheduler.pending == {"extinguisher_carried": 5}
    assert not scheduler.completed
    assert not controller.paused
    before = (tmp_path / "task_checkpoints.json").stat().st_mtime_ns
    scheduler.tick(controller, {"extinguisher_carried": True})
    assert (tmp_path / "task_checkpoints.json").stat().st_mtime_ns == before
    controller.step_index = 9
    controller.backend.busy = False
    scheduler.tick(controller, {"extinguisher_carried": True})
    event = scheduler.history[-1]
    assert event["trigger_step"] == 5
    assert event["saved_step"] == 9
    assert not scheduler.pending


def test_expired_carry_condition_is_reported_not_saved_as_a_later_idle_state(tmp_path):
    scheduler, controller = setup_scheduler(tmp_path)
    controller.backend.busy = True
    scheduler.tick(controller, {"extinguisher_carried": True})
    controller.backend.busy = False
    scheduler.tick(controller, {"extinguisher_carried": False})
    assert not controller.backend.checkpoints
    assert "extinguisher_carried" in scheduler.missed
    assert scheduler.history[-1]["status"] == "missed"


def test_restore_rearms_future_milestones_without_duplicating_current_one(tmp_path):
    scheduler, controller = setup_scheduler(tmp_path)
    scheduler.tick(controller, {"assignment_applied": True})
    controller.after_step()
    scheduler.tick(controller, {"assignment_applied": True, "extinguisher_carried": True})
    controller.request("restore", 0)
    assert not controller.poll()
    scheduler.tick(controller, {"assignment_applied": True})
    assert len(controller.backend.checkpoints) == 2
    assert "extinguisher_carried" not in scheduler.completed
    scheduler.tick(controller, {"assignment_applied": True, "extinguisher_carried": True})
    assert len(controller.backend.checkpoints) == 3
    controller.request("restore", 2)
    assert not controller.poll()
    scheduler.tick(controller, {"assignment_applied": True, "extinguisher_carried": True})
    assert len(controller.backend.checkpoints) == 3
    assert len(scheduler.history) == 3


def test_simultaneous_milestones_share_one_physical_snapshot(tmp_path):
    scheduler, controller = setup_scheduler(tmp_path)
    scheduler.tick(controller, {"assignment_applied": True, "rescue_channel_open": True})
    assert len(controller.backend.checkpoints) == 1
    assert {event["checkpoint_id"] for event in scheduler.history} == {0}
    assert len(scheduler.completed) == 2


@pytest.mark.parametrize("guard", ["blocked", "verification", "restore"])
def test_no_auto_capture_during_unsafe_restore_or_verification(tmp_path, guard):
    scheduler, controller = setup_scheduler(tmp_path)
    if guard == "blocked":
        controller.blocked = True
    elif guard == "verification":
        controller._verification = {}
    else:
        controller._pending_restore = 0
    scheduler.tick(controller, {"assignment_applied": True})
    assert not scheduler.completed and not scheduler.pending
    assert not controller.backend.checkpoints


def test_permanent_capture_failure_is_explicit_and_does_not_complete_milestone(tmp_path):
    scheduler, controller = setup_scheduler(tmp_path)
    controller.backend.fail = True
    with pytest.raises(RuntimeError, match="automatic task checkpoint failed"):
        scheduler.tick(controller, {"assignment_applied": True})
    assert not scheduler.completed
    assert scheduler.pending == {"assignment_applied": 0}
    assert scheduler.history[-1]["status"] == "error"


def test_final_index_reports_not_reached_and_unsafe_pending_milestones(tmp_path):
    scheduler, controller = setup_scheduler(tmp_path)
    controller.backend.busy = True
    scheduler.tick(controller, {"assignment_applied": True})
    scheduler.finish("fail")
    report = json.loads((tmp_path / "task_checkpoints.json").read_text())
    assert report["result"] == "fail"
    assert report["state"]["pending"] == {"assignment_applied": 0}
    assert "mission_success" in report["not_reached"]
    assert report["wait_reason"] == "worker running"


def test_invalid_scheduler_state_is_rejected_before_restore(tmp_path):
    scheduler = TaskCheckpointScheduler(tmp_path)
    with pytest.raises(ValueError, match="conflicting"):
        scheduler.validate_state({"completed": ["assignment_applied"], "pending": {"assignment_applied": 0}, "missed": {}})
