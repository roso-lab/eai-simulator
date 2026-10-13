"""Adapter from ``tools.physics_replay`` reports to benchmark branch semantics.

The physics replay package owns Isaac state capture/restore.  This adapter is
the only benchmark-facing seam: it restores the same checkpoint for every
branch, applies an optional branch treatment after restore, and returns the
typed provenance fields consumed by :class:`PairedBranchCoordinator`.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping

from .protocol import BranchSpec, CheckpointRef


TreatmentApplier = Callable[[BranchSpec], None]
BranchRunner = Callable[[BranchSpec], Mapping[str, Any] | None]
FidelityProvider = Callable[[Any, BranchSpec], str]


class PhysicsReplayBackend:
    """Use a replay coordinator while keeping causal claims explicit.

    ``ReplayCoordinator.restore`` normally proves only an observable physical
    state restore.  A caller must inject a fidelity provider backed by its
    action/noise/controller-state audit before this adapter can return
    ``causal_replay``.  The default therefore remains fail-closed.
    """

    def __init__(
        self,
        coordinator: Any,
        *,
        checkpoint: CheckpointRef | None = None,
        treatment_applier: TreatmentApplier | None = None,
        branch_runner: BranchRunner | None = None,
        fidelity_provider: FidelityProvider | None = None,
    ) -> None:
        if not callable(getattr(coordinator, "restore", None)):
            raise TypeError("coordinator.restore is required")
        self.coordinator = coordinator
        self.checkpoint = checkpoint
        self.treatment_applier = treatment_applier
        self.branch_runner = branch_runner
        self.fidelity_provider = fidelity_provider

    def fork(self, checkpoint: CheckpointRef, branch: BranchSpec) -> dict[str, Any]:
        self._validate_checkpoint(checkpoint, branch)
        diagnostics: list[str] = []
        try:
            report = self.coordinator.restore(checkpoint.step)
        except Exception as exc:
            return {
                "status": "unsupported",
                "replay_fidelity": "",
                "step_index": checkpoint.step,
                "unrestored_channels": (),
                "diagnostics": (f"coordinator_restore_failed:{exc}",),
            }

        physical = self._field(report, "physical", report)
        status = str(self._field(physical, "status", ""))
        # ``LiveCheckpointBackend`` is deliberately independent of this
        # protocol adapter and reports ``passed``/``fidelity`` instead of a
        # benchmark ``status``.  A passed readback proves an observable state
        # restore, but never upgrades it to causal replay.
        if not status:
            passed = self._field(physical, "passed", None)
            status = "physical_state_only" if passed is True else "unsupported"
        step_index = int(self._field(physical, "step_index", self._field(report, "step_index", checkpoint.step)))
        unrestored = tuple(str(item) for item in self._field(physical, "unrestored_channels", ()))
        diagnostics.extend(str(item) for item in self._field(physical, "diagnostics", ()))
        diagnostics.extend(str(item) for item in self._field(report, "diagnostics", ()))
        if step_index != checkpoint.step:
            diagnostics.append(f"restore_step_mismatch:{step_index}!={checkpoint.step}")

        fidelity = "physical_state_only"
        if self.fidelity_provider is not None:
            try:
                candidate = str(self.fidelity_provider(report, branch))
            except Exception as exc:
                candidate = "physical_state_only"
                diagnostics.append(f"fidelity_provider_failed:{exc}")
            if candidate == "causal_replay":
                if status == "restored" and step_index == checkpoint.step and not unrestored:
                    fidelity = candidate
                else:
                    diagnostics.append("causal_replay_rejected_by_restore_report")
            elif candidate in {"physical_state_only", "observable_state_checkpoint", ""}:
                fidelity = "physical_state_only"
            else:
                diagnostics.append(f"unknown_fidelity:{candidate}")

        if status not in {"restored", "physical_state_only"} or step_index != checkpoint.step:
            return {
                "status": status,
                "replay_fidelity": fidelity,
                "step_index": step_index,
                "unrestored_channels": unrestored,
                "diagnostics": tuple(diagnostics),
            }

        try:
            if self.treatment_applier is not None:
                self.treatment_applier(branch)
            artifact = self.branch_runner(branch) if self.branch_runner is not None else None
        except Exception as exc:
            diagnostics.append(f"branch_setup_failed:{exc}")
            return {
                "status": "unsupported",
                "replay_fidelity": fidelity,
                "step_index": step_index,
                "unrestored_channels": unrestored,
                "diagnostics": tuple(diagnostics),
            }

        result: dict[str, Any] = {
            "status": status,
            "replay_fidelity": fidelity,
            "step_index": step_index,
            "unrestored_channels": unrestored,
            "diagnostics": tuple(diagnostics),
        }
        if artifact is not None:
            result["artifact"] = dict(artifact)
        return result

    def _validate_checkpoint(self, checkpoint: CheckpointRef, branch: BranchSpec) -> None:
        if branch.checkpoint.digest != checkpoint.digest:
            raise ValueError("branch checkpoint digest does not match requested checkpoint")
        if self.checkpoint is not None:
            if checkpoint.digest != self.checkpoint.digest or checkpoint.step != self.checkpoint.step:
                raise ValueError("requested checkpoint does not match backend checkpoint")

    @staticmethod
    def _field(value: Any, name: str, default: Any) -> Any:
        if isinstance(value, Mapping):
            return value.get(name, default)
        return getattr(value, name, default)


__all__ = ["BranchRunner", "FidelityProvider", "PhysicsReplayBackend", "TreatmentApplier"]
