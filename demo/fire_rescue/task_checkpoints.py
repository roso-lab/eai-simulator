"""Task-owned checkpoint milestones, independent of the assignment algorithm and UI."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Mapping

from tools.physics_replay.live import CheckpointBusy


@dataclass(frozen=True)
class CheckpointRule:
    key: str
    label: str


FIRE_RESCUE_CHECKPOINTS = (
    CheckpointRule("assignment_applied", "Tasks assigned"),
    CheckpointRule("extinguisher_carried", "Extinguisher carried"),
    CheckpointRule("rescue_channel_open", "Rescue channel open"),
    CheckpointRule("obstacle_removed", "Obstacle removed"),
    CheckpointRule("mission_success", "Mission success"),
)


def checkpoint_signals(
    *, assignment_applied: bool, ur5: Any, rescue_channel_open: bool,
    obstacle: Any, result: str,
) -> dict[str, bool]:
    return {
        "assignment_applied": bool(assignment_applied),
        "extinguisher_carried": bool(
            getattr(ur5, "is_extinguisher_pick_complete", False)
            and getattr(ur5, "is_carrying_extinguisher", False)
        ),
        "rescue_channel_open": bool(rescue_channel_open),
        "obstacle_removed": bool(getattr(getattr(obstacle, "state", None), "obstacle_removed", False)),
        "mission_success": result == "success",
    }


class TaskCheckpointScheduler:
    name = "fire_rescue.task_checkpoints"
    version = 1
    priority = 28

    def __init__(self, output_dir: str | Path, *, algorithm: str = "emos"):
        self.output_dir = Path(output_dir)
        self.algorithm = algorithm
        self.completed: set[str] = set()
        self.pending: dict[str, int] = {}
        self.missed: dict[str, str] = {}
        self.history: list[dict[str, Any]] = []
        self.result: str | None = None
        self.wait_reason = ""
        self._dirty = True

    def capture_state(self) -> dict[str, Any]:
        return {"completed": sorted(self.completed), "pending": dict(self.pending), "missed": dict(self.missed)}

    def validate_state(self, state: Mapping[str, Any]) -> None:
        keys = {rule.key for rule in FIRE_RESCUE_CHECKPOINTS}
        completed, pending, missed = set(state["completed"]), state["pending"], state["missed"]
        if not isinstance(pending, dict) or not isinstance(missed, dict):
            raise ValueError("invalid task checkpoint state")
        if (completed | set(pending) | set(missed)) - keys:
            raise ValueError("unknown task checkpoint milestone")
        if completed & set(pending) or completed & set(missed) or set(pending) & set(missed):
            raise ValueError("conflicting task checkpoint status")
        if any(not isinstance(step, int) or step < 0 for step in pending.values()):
            raise ValueError("invalid task checkpoint trigger step")

    def restore_state(self, state: Mapping[str, Any]) -> None:
        self.validate_state(state)
        self.completed = set(state["completed"])
        self.pending = dict(state["pending"])
        self.missed = dict(state["missed"])
        self.result = None
        self.wait_reason = ""
        self._dirty = True

    def tick(self, controller: Any, signals: Mapping[str, bool]) -> None:
        if not controller.can_capture:
            return
        for rule in FIRE_RESCUE_CHECKPOINTS:
            if rule.key in self.completed or rule.key in self.missed:
                continue
            active = bool(signals.get(rule.key, False))
            if active and rule.key not in self.pending:
                self.pending[rule.key] = controller.step_index
                self._dirty = True
            elif not active and rule.key in self.pending:
                triggered = self.pending.pop(rule.key)
                self.missed[rule.key] = "condition ended before a safe capture boundary"
                self.history.append({
                    "milestone": rule.key, "status": "missed", "trigger_step": triggered,
                    "observed_step": controller.step_index, "reason": self.missed[rule.key],
                })
                self._dirty = True
        ready = [rule for rule in FIRE_RESCUE_CHECKPOINTS if rule.key in self.pending]
        if ready:
            previous = self.capture_state()
            was_dirty = self._dirty
            previous_reason = self.wait_reason
            for rule in ready:
                self.completed.add(rule.key)
                del self.pending[rule.key]
            try:
                checkpoint = controller.capture_checkpoint(" / ".join(rule.label for rule in ready))
            except CheckpointBusy as exc:
                self.restore_state(previous)
                self.wait_reason = str(exc)
                self._dirty = was_dirty or self.wait_reason != previous_reason
                controller.message = f"Task checkpoint pending: {exc}"
            except Exception as exc:
                self.restore_state(previous)
                self.history.append({"status": "error", "step": controller.step_index, "reason": str(exc)})
                self._write_index()
                raise RuntimeError(f"automatic task checkpoint failed: {exc}") from exc
            else:
                self.wait_reason = ""
                for rule in ready:
                    self.history.append({
                        "milestone": rule.key, "status": "saved", "label": rule.label,
                        "trigger_step": previous["pending"][rule.key], "saved_step": checkpoint.step_index,
                        "checkpoint_id": checkpoint.identifier,
                    })
                self._dirty = True
        if self._dirty:
            self._write_index()

    def finish(self, result: str) -> None:
        self.result = result
        self._write_index()

    def _write_index(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1, "task": "fire_rescue", "algorithm": self.algorithm,
            "rules": [rule.key for rule in FIRE_RESCUE_CHECKPOINTS],
            "state": self.capture_state(), "history": deepcopy(self.history),
            "result": self.result, "wait_reason": self.wait_reason,
            "not_reached": [
                rule.key for rule in FIRE_RESCUE_CHECKPOINTS
                if rule.key not in self.completed | set(self.pending) | set(self.missed)
            ],
        }
        destination = self.output_dir / "task_checkpoints.json"
        temporary = destination.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        temporary.replace(destination)
        self._dirty = False
