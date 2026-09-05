import numpy as np
import pytest
from types import SimpleNamespace

from tools.physics_replay.recorder import ReplayRecorder


class IncrementingFakeAdapter:
    def __init__(self):
        self.value = 0

    def read(self, targets):
        current = np.array([self.value], dtype=np.float32)
        self.value += 1
        return {"target_0/pose": current} if targets else {}


def test_before_after_step_keep_action_aligned_with_pre_step_state(tmp_path):
    adapter = IncrementingFakeAdapter()
    recorder = ReplayRecorder(
        adapter=adapter, targets=[], output_dir=tmp_path / "episode"
    )
    recorder.before_step({"robot_0": np.array([2.0], dtype=np.float32)})
    recorder.after_step(phase="phase_a")
    recorder.close()
    frame = recorder.reader.read_frame(0)
    assert frame.actions["robot_0"].tolist() == [2.0]
    assert frame.phase == "phase_a"


def test_recorder_rejects_double_before_step_and_unfinished_frame(tmp_path):
    recorder = ReplayRecorder(
        adapter=IncrementingFakeAdapter(), targets=[], output_dir=tmp_path / "episode"
    )
    recorder.before_step({})
    with pytest.raises(RuntimeError, match="pending"):
        recorder.before_step({})
    with pytest.raises(RuntimeError, match="pending"):
        recorder.close()


def test_sample_every_records_only_selected_steps(tmp_path):
    recorder = ReplayRecorder(
        adapter=IncrementingFakeAdapter(),
        targets=[],
        output_dir=tmp_path / "episode",
        sample_every=2,
    )
    for step in range(4):
        recorder.before_step({"robot_0": np.array([step], dtype=np.float32)})
        recorder.after_step()
    recorder.close()
    assert recorder.reader.manifest.metadata["frame_count"] == 2
    assert recorder.reader.read_frame(2).actions["robot_0"].tolist() == [2.0]


def test_manifest_carries_selection_hash_and_seed(tmp_path):
    session = SimpleNamespace(
        env_name="test",
        num_envs=1,
        device="cpu",
        selection_data={"scene": "plane", "robots": []},
        env_cfg=SimpleNamespace(seed=17),
        base_env=SimpleNamespace(step_dt=0.1, physics_dt=0.005),
    )
    recorder = ReplayRecorder(
        adapter=IncrementingFakeAdapter(),
        targets=[],
        output_dir=tmp_path / "episode",
        session=session,
    )
    recorder.close()
    environment = recorder.reader.manifest.environment
    assert environment["seed"] == 17
    assert len(environment["selection_sha256"]) == 64
    assert environment["physics_dt"] == 0.005
    assert environment["step_dt"] == 0.1


def test_info_events_convert_numpy_values_to_json_safe_data(tmp_path):
    recorder = ReplayRecorder(
        adapter=IncrementingFakeAdapter(),
        targets=[],
        output_dir=tmp_path / "episode",
    )
    recorder.before_step({})
    recorder.after_step(info={"metric": np.array([1.0, 2.0], dtype=np.float32)})
    recorder.close()
    assert recorder.reader.read_frame(0).events == (
        {"info": {"metric": [1.0, 2.0]}},
    )
