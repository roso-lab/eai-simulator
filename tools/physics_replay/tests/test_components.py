import random
from types import SimpleNamespace

import numpy as np
import pytest

from tools.physics_replay.components import (
    CallbackComponent,
    ComponentRegistry,
    EnvironmentCommandComponent,
    RngStateComponent,
)


def test_registry_validates_every_component_before_restoring():
    events = []
    good = CallbackComponent(
        "good", 1, lambda: {"x": 1}, lambda state: events.append("good")
    )

    def reject(_state):
        raise ValueError("bad state")

    bad = CallbackComponent(
        "bad",
        1,
        lambda: {"x": 2},
        lambda state: events.append("bad"),
        validator=reject,
    )
    registry = ComponentRegistry([good, bad])
    with pytest.raises(ValueError, match="bad state"):
        registry.restore_all(registry.capture_all())
    assert events == []


def test_registry_rejects_missing_and_wrong_version_snapshots():
    component = CallbackComponent("one", 2, lambda: {}, lambda state: None)
    registry = ComponentRegistry([component])
    snapshot = registry.capture_all()
    with pytest.raises(ValueError, match="missing"):
        registry.validate_all({})
    snapshot["one"].version = 1
    with pytest.raises(ValueError, match="version"):
        registry.validate_all(snapshot)


def test_rng_component_restores_python_and_numpy_sequences():
    random.seed(12)
    np.random.seed(34)
    component = RngStateComponent(include_torch=False)
    state = component.capture_state()
    expected_python = random.random()
    expected_numpy = float(np.random.random())
    random.random()
    np.random.random()

    component.restore_state(state)

    assert random.random() == expected_python
    assert float(np.random.random()) == expected_numpy


def test_environment_component_round_trips_commands_and_actions():
    commands = {"robot": {"goal": np.array([[1.0, 2.0]], dtype=np.float32)}}
    writes = []
    base_env = SimpleNamespace(
        _commands_dict=commands,
        actions_dict={"robot": np.array([[0.5]], dtype=np.float32)},
        get_command=lambda robot, name: None,
        set_command=lambda robot, name, value: writes.append((robot, name, value)),
    )
    component = EnvironmentCommandComponent(base_env, device=None)
    state = component.capture_state()
    base_env.actions_dict = {}
    component.restore_state(state)

    assert [item[:2] for item in writes] == [("robot", "goal")]
    np.testing.assert_array_equal(base_env.actions_dict["robot"], [[0.5]])
