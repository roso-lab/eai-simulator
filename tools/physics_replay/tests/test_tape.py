from types import SimpleNamespace

import numpy as np
import pytest

from tools.physics_replay.store import EpisodeReader, EpisodeWriter
from tools.physics_replay.tape import (
    ExecutionTape,
    NoiseTape,
    apply_commands,
    capture_commands,
    flatten_commands,
)
from tools.physics_replay.types import FrameRecord, Manifest


def test_noise_tape_reuses_exact_samples():
    record = NoiseTape.recording(seed=7)
    first = record.sample("wind", 4, lambda rng: rng.normal(size=3))
    stored = record.values_for_step(4)

    replay = NoiseTape.replaying({4: stored})
    second = replay.sample("wind", 4, lambda rng: np.full(3, 99.0))

    np.testing.assert_array_equal(second, first)
    replay.assert_step_consumed(4)


def test_noise_tape_keyed_events_are_stable_across_branch_draw_order():
    left = NoiseTape.recording(seed=11)
    expected = left.sample("wind", 4, lambda rng: rng.normal(size=3), event_key="gust")
    right = NoiseTape.recording(seed=11)
    right.sample("wind", 4, lambda rng: rng.normal(size=99), event_key="unrelated")
    actual = right.sample("wind", 4, lambda rng: rng.normal(size=3), event_key="gust")
    np.testing.assert_array_equal(expected, actual)

    replay = NoiseTape.replaying({4: right.values_for_step(4)}, seed=11)
    replay.sample("wind", 4, lambda rng: np.full(3, 99.0), event_key="unrelated")
    np.testing.assert_array_equal(
        replay.sample("wind", 4, lambda rng: np.full(3, 99.0), event_key="gust"),
        actual,
    )


def test_noise_tape_rejects_missing_and_unconsumed_samples():
    replay = NoiseTape.replaying(
        {
            2: {
                "wind#0": np.array([1.0]),
                "wind#1": np.array([2.0]),
            }
        }
    )
    replay.sample("wind", 2, lambda rng: np.array([0.0]))
    with pytest.raises(ValueError, match="unconsumed"):
        replay.assert_step_consumed(2)
    with pytest.raises(KeyError, match="missing"):
        replay.sample("rain", 2, lambda rng: np.array([0.0]))


def test_command_helpers_flatten_capture_and_apply():
    source = {
        "m20_1": {"goal_position": np.array([[1.0, 2.0, 0.5]])},
        "carter_1": {"base_velocity": np.array([[0.5, 0.0, 0.1]])},
    }
    flat = flatten_commands(source)
    assert set(flat) == {
        "m20_1/goal_position",
        "carter_1/base_velocity",
    }

    writes = []
    base_env = SimpleNamespace(
        _commands_dict=source,
        get_command=lambda robot, name: source.get(robot, {}).get(name),
        set_command=lambda robot, name, value: writes.append((robot, name, value)),
    )
    captured = capture_commands(base_env)
    source["m20_1"]["goal_position"][:] = 99.0
    apply_commands(base_env, captured)

    np.testing.assert_array_equal(
        captured["m20_1/goal_position"], np.array([[1.0, 2.0, 0.5]])
    )
    assert [item[:2] for item in writes] == [
        ("carter_1", "base_velocity"),
        ("m20_1", "goal_position"),
    ]


def test_execution_tape_requires_exact_step_range(tmp_path):
    writer = EpisodeWriter(
        tmp_path / "episode",
        Manifest(environment={"env_name": "x", "num_envs": 1}, targets=[]),
    )
    for step in range(2):
        writer.append(
            FrameRecord(
                step,
                step * 0.1,
                {},
                {},
                {"robot": np.array([step], dtype=np.float32)},
                commands={"robot/goal": np.array([step], dtype=np.float32)},
        noise={"wind#0": np.array([step], dtype=np.float32)},
        applied_actions={"robot": np.array([step + 1], dtype=np.float32)},
            )
        )
    writer.close()
    tape = ExecutionTape(EpisodeReader(tmp_path / "episode"))

    assert tape.step(1).step_index == 1
    assert tape.step(1).applied_actions_recorded is True
    assert len(tape.require_range(0, 2)) == 2
    with pytest.raises(ValueError, match="step 2"):
        tape.require_range(1, 2)


def test_execution_tape_marks_omitted_applied_actions_as_absent(tmp_path):
    writer = EpisodeWriter(
        tmp_path / "episode",
        Manifest(environment={"env_name": "x", "num_envs": 1}, targets=[]),
    )
    writer.append(
        FrameRecord(
            0,
            0.0,
            {},
            {},
            {"robot": np.array([0.0], dtype=np.float32)},
        )
    )
    writer.close()
    assert ExecutionTape(EpisodeReader(tmp_path / "episode")).step(0).applied_actions_recorded is False
