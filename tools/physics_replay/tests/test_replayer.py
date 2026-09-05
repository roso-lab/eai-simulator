from types import SimpleNamespace

import numpy as np
import pytest

from tools.physics_replay.replayer import PhysicsReplayer
from tools.physics_replay.store import EpisodeReader, EpisodeWriter
from tools.physics_replay.types import FrameRecord, Manifest, stable_hash


class FakeAdapter:
    def __init__(self):
        self.writes = []
        self.state = {"target_0/pose": np.zeros(7, dtype=np.float32)}

    def write(self, target, values):
        self.writes.append(target.target_id)
        self.state.update({key: value.copy() for key, value in values.items()})

    def read(self, target, channels):
        return {key: self.state[key].copy() for key in channels}


def _reader(tmp_path):
    manifest = Manifest(
        environment={"env_name": "test", "num_envs": 1},
        targets=[{"target_id": "target_0", "writable_channels": ["pose"]}],
    )
    writer = EpisodeWriter(tmp_path / "episode", manifest)
    value = np.arange(7, dtype=np.float32)
    writer.append(
        FrameRecord(
            0,
            0.0,
            {"target_0/pose": value},
            {"target_0/pose": value},
            {},
        )
    )
    writer.close()
    return EpisodeReader(tmp_path / "episode")


def test_restore_writes_state_and_returns_physical_state_only(tmp_path):
    target = SimpleNamespace(
        target_id="target_0", writable_channels=("pose",), depth=0
    )
    adapter = FakeAdapter()
    replayer = PhysicsReplayer(adapter, session=None, targets=[target])

    report = replayer.restore_from_reader(_reader(tmp_path), 0)

    assert report.ok
    assert report.status == "physical_state_only"
    assert adapter.writes == ["target_0"]
    assert report.channel_errors["target_0/pose"]["within_tolerance"]


def test_restore_rejects_corrupt_readback(tmp_path):
    target = SimpleNamespace(
        target_id="target_0", writable_channels=("pose",), depth=0
    )

    class DivergingAdapter(FakeAdapter):
        def read(self, target, channels):
            result = super().read(target, channels)
            result["target_0/pose"][0] += 1.0
            return result

    report = PhysicsReplayer(
        DivergingAdapter(), session=None, targets=[target]
    ).restore_from_reader(_reader(tmp_path), 0)
    assert report.status == "replay_diverged"
    assert not report.ok


def test_restore_rejects_target_mismatch(tmp_path):
    target = SimpleNamespace(
        target_id="other_target", writable_channels=("pose",), depth=0
    )
    with pytest.raises(ValueError, match="target"):
        PhysicsReplayer(
            FakeAdapter(), session=None, targets=[target]
        ).restore_from_reader(_reader(tmp_path), 0)


def test_restore_rejects_selection_configuration_mismatch(tmp_path):
    manifest = Manifest(
        environment={
            "env_name": "test",
            "num_envs": 1,
            "selection_sha256": stable_hash({"scene": "plane"}),
        },
        targets=[],
    )
    writer = EpisodeWriter(tmp_path / "episode", manifest)
    value = np.arange(7, dtype=np.float32)
    writer.append(FrameRecord(0, 0.0, {}, {}, {}))
    writer.close()
    reader = EpisodeReader(tmp_path / "episode")
    session = SimpleNamespace(
        env_name="test", selection_data={"scene": "warehouse"}
    )
    with pytest.raises(ValueError, match="selection"):
        PhysicsReplayer(
            FakeAdapter(), session=session, targets=[]
        ).restore_from_reader(reader, 0)


def test_restore_reports_unsupported_when_no_requested_channel_is_writable(tmp_path):
    target = SimpleNamespace(
        target_id="target_0",
        writable_channels=(),
        depth=1,
        spec=SimpleNamespace(channels=("pose",)),
    )
    report = PhysicsReplayer(
        FakeAdapter(), session=None, targets=[target]
    ).restore_from_reader(_reader(tmp_path), 0)
    assert report.status == "unsupported"
    assert not report.ok


def test_restore_keeps_read_only_diagnostic_channel_as_physical_state_only(tmp_path):
    target = SimpleNamespace(
        target_id="target_0",
        writable_channels=(),
        depth=1,
        spec=SimpleNamespace(channels=("contact_force",)),
    )
    report = PhysicsReplayer(
        FakeAdapter(), session=None, targets=[target]
    ).restore_from_reader(_reader(tmp_path), 0)
    assert report.status == "physical_state_only"
    assert report.ok
    assert "target_0/contact_force" in report.unrestored_channels
