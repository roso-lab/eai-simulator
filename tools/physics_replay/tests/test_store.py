import json

import numpy as np

from tools.physics_replay.store import EpisodeReader, EpisodeWriter
from tools.physics_replay.types import FrameRecord, Manifest


def _frame(step):
    value = np.array([step, step + 1], dtype=np.float32)
    return FrameRecord(
        step,
        step * 0.1,
        {"target_0/pose": value},
        {"target_0/pose": value + 1},
        {"robot_0": value},
    )


def test_writer_round_trips_frames_and_final_state(tmp_path):
    manifest = Manifest(
        environment={"env_name": "test", "num_envs": 1},
        targets=[],
        segment_frames=2,
    )
    writer = EpisodeWriter(tmp_path / "episode", manifest)
    writer.append(_frame(0))
    writer.append(_frame(1))
    writer.append(_frame(2))
    writer.close()

    reader = EpisodeReader(tmp_path / "episode")
    assert reader.read_frame(1).step_index == 1
    np.testing.assert_array_equal(
        reader.read_state(3)["target_0/pose"],
        np.array([3, 4], dtype=np.float32),
    )
    assert len(reader.index["segments"]) == 2


def test_reader_rejects_missing_segment(tmp_path):
    manifest = Manifest(
        environment={"env_name": "test", "num_envs": 1}, targets=[]
    )
    writer = EpisodeWriter(tmp_path / "episode", manifest)
    writer.append(_frame(0))
    writer.close()
    (tmp_path / "episode" / "segments" / "000000.npz").unlink()
    reader = EpisodeReader(tmp_path / "episode")
    try:
        reader.read_frame(0)
    except ValueError as exc:
        assert "segment" in str(exc)
    else:
        raise AssertionError("missing segment was not rejected")


def test_manifest_contains_field_schema_and_checksum(tmp_path):
    manifest = Manifest(
        environment={"env_name": "test", "num_envs": 1}, targets=[]
    )
    writer = EpisodeWriter(tmp_path / "episode", manifest)
    writer.append(_frame(0))
    writer.close()
    payload = json.loads((tmp_path / "episode" / "manifest.json").read_text())
    assert payload["fields"]["before/target_0/pose"]["shape"] == [2]
    assert payload["status"] == "closed"
    assert payload["metadata"]["frame_count"] == 1
    assert len(payload["metadata"]["segment_checksums"]) == 1
