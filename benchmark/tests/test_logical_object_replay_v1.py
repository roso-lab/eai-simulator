from copy import deepcopy

import pytest

from benchmark.logical_object_replay_v1 import register_logical_object_replay
from benchmark.replay_state_v3 import ReplayStateRegistryV3, SnapshotParticipantV3
from task.tests.test_logical_objects import make_runtime, execute


def test_existing_replay_registry_restores_logical_objects_without_physical_reconstruction():
    runtime, host = make_runtime()
    execute(runtime)
    pin = runtime.capture_state()["state_digest"]
    states = {name: {"tick": 0} for name in ReplayStateRegistryV3.REQUIRED_PARTICIPANTS}
    registry = ReplayStateRegistryV3()
    for name in states:
        def restore(value, key=name):
            states[key] = deepcopy(value)
            if key == "clock": host.tick = value["tick"]
        registry.register(SnapshotParticipantV3(name, "1", lambda key=name: deepcopy(states[key]), restore, lambda key=name: deepcopy(states[key])))
    register_logical_object_replay(registry, runtime, registered_digest=lambda: pin)
    snapshot = registry.capture()
    host.tick = 1
    execute(runtime, "deliver", "deliver", point="target")
    registry.restore(snapshot)
    assert registry.readback_snapshot() == snapshot
    assert runtime.object_state("object_01")["holders"] == ["r1"]


def test_logical_participant_cannot_restore_self_authenticated_state():
    from benchmark.logical_object_replay_v1 import logical_object_participant
    runtime, _ = make_runtime()
    participant = logical_object_participant(runtime, registered_digest=lambda: "f" * 64)
    with pytest.raises(ValueError, match="external registration"):
        participant.restore(participant.capture())
