"""Explicit snapshot participants for causal replay qualification."""

from __future__ import annotations

from dataclasses import dataclass
from copy import deepcopy
import math
from typing import Any, Callable, Mapping


@dataclass(frozen=True)
class SnapshotParticipantV3:
    participant_id: str
    version: str
    capture: Callable[[], Mapping[str, Any]]
    restore: Callable[[Mapping[str, Any]], None]
    readback: Callable[[], Mapping[str, Any]]
    # Physics adapters may supply a preregistered tolerance comparison. Without
    # one, state equality is exact, including clock, queues and random counters.
    compare: Callable[[Mapping[str, Any], Mapping[str, Any]], bool] | None = None


def _state_equal(expected: Any, observed: Any) -> bool:
    if isinstance(expected, Mapping):
        return isinstance(observed, Mapping) and set(expected) == set(observed) and all(
            _state_equal(value, observed[key]) for key, value in expected.items())
    if isinstance(expected, (list, tuple)):
        return type(expected) is type(observed) and len(expected) == len(observed) and all(
            _state_equal(a, b) for a, b in zip(expected, observed))
    if hasattr(expected, "shape") or hasattr(observed, "shape"):
        import numpy as np
        def array(value):
            return value.detach().cpu().numpy() if hasattr(value, "detach") else np.asarray(value)
        try:
            first, second = array(expected), array(observed)
            return (first.dtype == second.dtype and first.shape == second.shape
                    and bool(np.isfinite(first).all()) and bool(np.isfinite(second).all())
                    and bool(np.array_equal(first, second)))
        except (TypeError, ValueError):
            return False
    if isinstance(expected, float) and not math.isfinite(expected):
        return False
    return type(expected) is type(observed) and expected == observed


class ReplayStateRegistryV3:
    # Native readback can restore poses while leaving simulated time advanced.
    # Time is an explicit participant; it cannot be inferred from world pose.
    REQUIRED_PARTICIPANTS = ("world", "clock", "controller", "skill", "task", "algorithm", "transport", "locks", "events", "rng")

    def __init__(self, participants: tuple[SnapshotParticipantV3, ...] = ()):
        self._participants: dict[str, SnapshotParticipantV3] = {}
        self._restore_failed = False
        for participant in participants:
            self.register(participant)

    def register(self, participant: SnapshotParticipantV3) -> None:
        if not participant.participant_id.strip() or not participant.version.strip():
            raise ValueError("participant id/version required")
        if participant.participant_id in self._participants:
            raise ValueError(f"duplicate participant: {participant.participant_id}")
        self._participants[participant.participant_id] = participant

    def missing_required(self) -> tuple[str, ...]:
        return tuple(item for item in self.REQUIRED_PARTICIPANTS if item not in self._participants)

    @property
    def restore_order(self) -> tuple[str, ...]:
        """Stable dependency order, independent of plugin registration order."""
        return tuple(name for name in self.REQUIRED_PARTICIPANTS if name in self._participants) + tuple(
            sorted(set(self._participants).difference(self.REQUIRED_PARTICIPANTS)))

    @staticmethod
    def states_equal(expected: Any, observed: Any) -> bool:
        return _state_equal(expected, observed)

    def capture(self) -> dict[str, Any]:
        if self._restore_failed:
            raise RuntimeError("partial restore quarantined this registry; rebuild and reconcile runtime")
        missing = self.missing_required()
        if missing:
            raise RuntimeError(f"causal snapshot missing participants: {', '.join(missing)}")
        return {key: {"version": item.version, "state": deepcopy(dict(item.capture()))} for key, item in sorted(self._participants.items())}

    def restore(self, snapshot: Mapping[str, Any]) -> None:
        if self._restore_failed:
            raise RuntimeError("partial restore quarantined this registry; rebuild and reconcile runtime")
        missing = self.missing_required()
        if missing:
            raise RuntimeError(f"causal restore missing participants: {', '.join(missing)}")
        if set(snapshot) != set(self._participants):
            raise ValueError("snapshot participant set differs from runtime")
        # Validate every participant before any mutating restore call. A late
        # mismatch must not leave half of the runtime on a different branch.
        for key, participant in self._participants.items():
            payload = snapshot.get(key)
            if not isinstance(payload, Mapping) or payload.get("version") != participant.version or not isinstance(payload.get("state"), Mapping):
                raise ValueError(f"snapshot version mismatch for {key}")
        try:
            for key in self.restore_order:
                participant = self._participants[key]
                participant.restore(deepcopy(snapshot[key]["state"]))
            # Check only after every write: a late participant may perturb the
            # world or clock restored earlier. A no-op restore is not success.
            for key, participant in self._participants.items():
                observed = deepcopy(participant.readback())
                expected = deepcopy(snapshot[key]["state"])
                compare = participant.compare or _state_equal
                if not isinstance(observed, Mapping) or compare(expected, observed) is not True:
                    raise RuntimeError(f"snapshot readback mismatch for {key}")
        except BaseException:
            self._restore_failed = True
            raise

    def readback(self) -> dict[str, Any]:
        return {key: dict(item.readback()) for key, item in sorted(self._participants.items())}

    def readback_snapshot(self) -> dict[str, Any]:
        """Read current versioned state for a recovery receipt, never recapture."""
        if self._restore_failed:
            raise RuntimeError("partial restore quarantined this registry; rebuild and reconcile runtime")
        missing = self.missing_required()
        if missing:
            raise RuntimeError(f"causal readback missing participants: {', '.join(missing)}")
        return {key: {"version": item.version, "state": deepcopy(dict(item.readback()))}
                for key, item in sorted(self._participants.items())}

    def quarantine(self) -> None:
        """Prevent reuse after an external recovery integrity check fails."""
        self._restore_failed = True


__all__ = ["SnapshotParticipantV3", "ReplayStateRegistryV3"]
