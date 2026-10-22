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


def _episode(tmp_path, frames=12):
    root = tmp_path / "episode"
    with EpisodeWriter(root, Manifest(environment={}, targets=[], segment_frames=2)) as writer:
        for step in range(frames):
            writer.append(_frame(step))
    return root


def test_long_episode_cache_is_bounded_and_revisits_preserve_state(tmp_path):
    reader = EpisodeReader(_episode(tmp_path), max_cached_segments=2)
    reader.validate_integrity()
    assert reader.cache_info["segments"] == 2
    assert reader.cache_info["evictions"] == 4
    for step in (0, 7, 2, 11, 12, 0):
        np.testing.assert_array_equal(reader.read_state(step)["target_0/pose"], [step, step + 1])
        assert reader.cache_info["segments"] <= 2
        assert reader.cache_info["bytes"] <= reader.max_cache_bytes
    reader.clear_cache()
    assert reader.cache_info["bytes"] == reader.cache_info["segments"] == 0


def test_cache_hit_avoids_redecode_and_returned_arrays_are_independent(tmp_path):
    reader = EpisodeReader(_episode(tmp_path))
    first = reader.read_frame(0)
    first.state_before["target_0/pose"][:] = 99
    np.testing.assert_array_equal(reader.read_state(0)["target_0/pose"], [0, 1])
    assert reader.cache_info["hits"] == 1
    assert reader.cache_info["misses"] == 1


def test_large_segment_and_disabled_cache_are_not_retained(tmp_path):
    root = _episode(tmp_path)
    for options in ({"max_cache_bytes": 1}, {"max_cached_segments": 0}):
        reader = EpisodeReader(root, **options)
        np.testing.assert_array_equal(reader.read_state(3)["target_0/pose"], [3, 4])
        reader.validate_integrity()
        assert reader.cache_info["bytes"] == reader.cache_info["segments"] == 0


def test_cached_segment_cannot_hide_corruption_or_deletion(tmp_path):
    import pytest
    root = _episode(tmp_path)
    reader = EpisodeReader(root)
    reader.read_frame(0)
    segment = root / "segments" / "000000.npz"
    original = segment.read_bytes()
    segment.write_bytes(original + b"corrupt")
    with pytest.raises(ValueError, match="checksum"):
        reader.validate_integrity()
    segment.write_bytes(original)
    reader.read_frame(0)
    segment.unlink()
    with pytest.raises(ValueError, match="missing"):
        reader.read_frame(0)


def test_invalid_cache_budget_is_rejected(tmp_path):
    import pytest
    root = _episode(tmp_path)
    for key in ("max_cache_bytes", "max_cached_segments"):
        for value in (-1, True, 1.5, None):
            with pytest.raises(ValueError, match=key):
                EpisodeReader(root, **{key: value})
