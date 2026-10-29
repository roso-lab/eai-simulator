"""Exact logical object participant for the existing causal replay registry."""
from collections.abc import Callable

from task.runtime.logical_objects import LogicalObjectRuntime
from .replay_state_v3 import SnapshotParticipantV3


def logical_object_participant(runtime: LogicalObjectRuntime, *, registered_digest: Callable[[], str]):
    """The external checkpoint authority supplies the expected state digest.

    World/robot physics, clocks, queues, locks and RNG remain separate existing
    registry participants. No world task object is authored or reconstructed.
    """
    if not isinstance(runtime, LogicalObjectRuntime) or not callable(registered_digest):
        raise TypeError("logical runtime and external checkpoint digest reader are required")
    return SnapshotParticipantV3(
        participant_id="logical_objects", version="1",
        capture=runtime.capture_state,
        restore=lambda state: runtime.restore_state(state, expected_digest=registered_digest()),
        readback=runtime.capture_state,
    )


def register_logical_object_replay(registry, runtime, *, registered_digest):
    registry.register(logical_object_participant(runtime, registered_digest=registered_digest))
