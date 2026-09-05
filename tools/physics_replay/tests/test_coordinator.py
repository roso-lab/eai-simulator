from types import SimpleNamespace

import numpy as np

from tools.physics_replay.coordinator import ReplayCoordinator
from tools.physics_replay.tape import ReplayStep
from tools.physics_replay.types import RestoreReport


def test_coordinator_restores_physics_before_components_and_applies_commands_before_step():
    events = []
    fake_tape = SimpleNamespace(
        step=lambda index: ReplayStep(
            index,
            {"robot": np.array([1.0])},
            {"robot/goal": np.array([2.0])},
            {},
            (),
            {"robot": np.array([1.5])},
        )
    )
    coordinator = ReplayCoordinator.for_test(
        physical_restore=lambda step: events.append(("physics", step))
        or RestoreReport("physical_state_only", step),
        component_restore=lambda step: events.append(("components", step)),
        command_apply=lambda commands: events.append(("commands", commands)),
        env_step=lambda actions: events.append(("step", actions)) or "observation",
        tape=fake_tape,
    )

    report = coordinator.restore(5)
    result = coordinator.replay_step(5)

    assert report.ok
    assert result == "observation"
    assert [event[0] for event in events] == [
        "physics",
        "components",
        "commands",
        "step",
    ]


def test_coordinator_does_not_restore_components_after_physical_failure():
    events = []
    coordinator = ReplayCoordinator.for_test(
        physical_restore=lambda step: RestoreReport("replay_diverged", step),
        component_restore=lambda step: events.append(step),
        command_apply=lambda commands: None,
        env_step=lambda actions: None,
        tape=SimpleNamespace(step=lambda index: None),
    )

    report = coordinator.restore(2)

    assert not report.ok
    assert events == []
