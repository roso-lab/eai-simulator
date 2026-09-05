import json

import numpy as np
import pytest

from tools.physics_replay.checkpoints import CheckpointStore
from tools.physics_replay.components import ComponentSnapshot


def test_checkpoint_store_round_trips_nested_arrays(tmp_path):
    store = CheckpointStore(tmp_path / "episode")
    snapshots = {
        "component": ComponentSnapshot(
            "component",
            1,
            {"nested": (np.arange(3), {"enabled": True}), "value": None},
        )
    }
    path = store.write(8, snapshots)
    restored = store.read(8)

    assert path.name == "00000008.json"
    assert isinstance(restored["component"].state["nested"], tuple)
    np.testing.assert_array_equal(
        restored["component"].state["nested"][0], np.arange(3)
    )
    assert restored["component"].state["nested"][1]["enabled"] is True


def test_checkpoint_store_rejects_corrupt_array_file(tmp_path):
    store = CheckpointStore(tmp_path / "episode")
    store.write(1, {"component": ComponentSnapshot("component", 1, {"x": np.arange(2)})})
    (tmp_path / "episode" / "checkpoints" / "00000001.npz").write_bytes(b"bad")
    with pytest.raises(ValueError, match="checksum"):
        store.read(1)


def test_checkpoint_store_rejects_unsupported_state_value(tmp_path):
    store = CheckpointStore(tmp_path / "episode")
    with pytest.raises(TypeError, match="unsupported"):
        store.write(
            1,
            {"component": ComponentSnapshot("component", 1, {"handle": object()})},
        )


def test_checkpoint_store_detects_tampered_json(tmp_path):
    store = CheckpointStore(tmp_path / "episode")
    store.write(1, {"component": ComponentSnapshot("component", 1, {"x": np.arange(2)})})
    path = tmp_path / "episode" / "checkpoints" / "00000001.json"
    payload = json.loads(path.read_text())
    payload["components"]["component"]["version"] = 99
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="checksum"):
        store.read(1)
