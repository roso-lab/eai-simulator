import numpy as np
import pytest

from tools.physics_replay.types import FrameRecord, Manifest, TargetSpec


def test_target_spec_normalizes_path_and_rejects_unknown_channel():
    assert (
        TargetSpec(" /World/envs/env_0/cube ", ("pose",)).prim_path
        == "/World/envs/env_0/cube"
    )
    with pytest.raises(ValueError, match="unknown channel"):
        TargetSpec("/World/cube", ("not_a_channel",))


def test_target_spec_rejects_relative_path_and_duplicate_channels():
    with pytest.raises(ValueError, match="absolute"):
        TargetSpec("World/cube", ("pose",))
    with pytest.raises(ValueError, match="duplicate"):
        TargetSpec("/World/cube", ("pose", "pose"))


def test_frame_record_copies_array_inputs():
    source = np.array([1.0, 2.0], dtype=np.float32)
    frame = FrameRecord(
        step_index=3,
        sim_time=0.15,
        state_before={"target_0/pose": source},
        state_after={"target_0/pose": source + 1},
        actions={"robot_0": source},
    )
    source[0] = 99.0
    assert frame.state_before["target_0/pose"][0] == 1.0
    assert frame.actions["robot_0"][0] == 1.0


def test_manifest_round_trips_json_compatible_values():
    manifest = Manifest(
        environment={"env_name": "test", "num_envs": 1},
        targets=[{"target_id": "target_0", "channels": ["pose"]}],
        fields={"target_0/pose": {"shape": [7], "dtype": "float32"}},
    )
    restored = Manifest.from_dict(manifest.to_dict())
    assert restored.format_version == 1
    assert restored.environment["env_name"] == "test"
    assert restored.fields["target_0/pose"]["shape"] == [7]
