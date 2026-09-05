import numpy as np

from tools.physics_replay.recorder import ReplayRecorder
from tools.physics_replay.store import EpisodeReader, EpisodeWriter
from tools.physics_replay.types import FrameRecord, Manifest


def test_frame_round_trips_commands_and_noise(tmp_path):
    frame = FrameRecord(
        3,
        0.15,
        {},
        {},
        {"robot": np.array([1.0], dtype=np.float32)},
        commands={
            "robot/goal_position": np.array([[2.0, 3.0, 0.5]], dtype=np.float32)
        },
        noise={"wind#0": np.array([0.1, -0.1], dtype=np.float32)},
        applied_actions={"robot": np.array([[0.2]], dtype=np.float32)},
    )
    writer = EpisodeWriter(
        tmp_path / "episode",
        Manifest(environment={"env_name": "x", "num_envs": 1}, targets=[]),
    )
    writer.append(frame)
    writer.close()

    restored = EpisodeReader(tmp_path / "episode").read_frame(3)
    np.testing.assert_array_equal(
        restored.commands["robot/goal_position"],
        frame.commands["robot/goal_position"],
    )
    np.testing.assert_array_equal(restored.noise["wind#0"], frame.noise["wind#0"])
    np.testing.assert_array_equal(
        restored.applied_actions["robot"], frame.applied_actions["robot"]
    )


def test_recorder_copies_commands_and_noise_before_step(tmp_path):
    recorder = ReplayRecorder(adapter=object(), targets=[], output_dir=tmp_path / "episode")
    command = np.array([[1.0, 2.0, 3.0]], dtype=np.float32)
    noise = np.array([0.25], dtype=np.float32)
    recorder.before_step(
        {},
        commands={"robot/goal_position": command},
        noise={"wind#0": noise},
    )
    command[:] = 99.0
    noise[:] = 99.0
    recorder.after_step()
    recorder.close()

    frame = recorder.reader.read_frame(0)
    np.testing.assert_array_equal(
        frame.commands["robot/goal_position"],
        np.array([[1.0, 2.0, 3.0]], dtype=np.float32),
    )
    np.testing.assert_array_equal(
        frame.noise["wind#0"], np.array([0.25], dtype=np.float32)
    )
