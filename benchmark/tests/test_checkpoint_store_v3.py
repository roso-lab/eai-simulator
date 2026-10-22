"""Typed, durable storage tests for versioned replay registry snapshots."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest

import benchmark.checkpoint_store_v3 as checkpoint_store
from benchmark.checkpoint_store_v3 import (
    read_registry_checkpoint,
    write_registry_checkpoint,
)
from benchmark.replay_state_v3 import ReplayStateRegistryV3


METADATA = {"run_id": "durable-test", "source_digest": "a" * 64}


def _snapshot(*, extension_value: int = 7) -> dict[str, dict[str, object]]:
    snapshot: dict[str, dict[str, object]] = {
        name: {
            "version": "participant.v1",
            "state": {
                "name": name,
                "none": None,
                "flag": True,
                "count": 3,
                "ratio": 0.25,
                "list": [1, False, "value"],
                "tuple": (1, True, None),
                "mapping": {"__codec__": "ordinary-user-data"},
                "array": np.arange(12, dtype=">i2").reshape(3, 4)[:, ::2],
            },
        }
        for name in ReplayStateRegistryV3.REQUIRED_PARTICIPANTS
    }
    snapshot["extension"] = {
        "version": "plugin.v2",
        "state": {
            "scalar_array": np.array(extension_value, dtype=np.int16),
            "empty_array": np.empty((0, 3), dtype=np.float32),
        },
    }
    return snapshot


def _minimal_snapshot() -> dict[str, dict[str, object]]:
    return {
        name: {"version": "v1", "state": {}}
        for name in ReplayStateRegistryV3.REQUIRED_PARTICIPANTS
    }


def _canonical_manifest(path: Path, mutate) -> tuple[dict[str, object], str]:
    manifest_path = path / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    mutate(manifest)
    encoded = (json.dumps(
        manifest,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ) + "\n").encode("utf-8")
    manifest_path.write_bytes(encoded)
    return manifest, sha256(encoded).hexdigest()


def test_round_trip_preserves_typed_state_and_extension_participant(tmp_path):
    artifact = tmp_path / "checkpoint"
    digest = write_registry_checkpoint(artifact, _snapshot(), metadata=METADATA)

    restored = read_registry_checkpoint(
        artifact, expected_digest=digest, expected_metadata=METADATA)

    assert set(restored) == set(ReplayStateRegistryV3.REQUIRED_PARTICIPANTS) | {"extension"}
    state = restored["world"]["state"]
    assert state["none"] is None
    assert type(state["flag"]) is bool
    assert type(state["count"]) is int
    assert type(state["ratio"]) is float
    assert type(state["list"]) is list
    assert type(state["tuple"]) is tuple
    assert state["mapping"] == {"__codec__": "ordinary-user-data"}
    assert state["array"].dtype == np.dtype(">i2")
    assert state["array"].shape == (3, 2)
    np.testing.assert_array_equal(
        state["array"], np.arange(12, dtype=">i2").reshape(3, 4)[:, ::2])
    scalar = restored["extension"]["state"]["scalar_array"]
    assert isinstance(scalar, np.ndarray) and scalar.shape == ()
    assert scalar.dtype == np.dtype(np.int16) and scalar.item() == 7
    empty = restored["extension"]["state"]["empty_array"]
    assert empty.shape == (0, 3) and empty.dtype == np.dtype(np.float32)


def test_digest_binds_extension_participants_and_external_metadata(tmp_path):
    first = write_registry_checkpoint(tmp_path / "first", _snapshot(extension_value=7), metadata=METADATA)
    second = write_registry_checkpoint(tmp_path / "second", _snapshot(extension_value=8), metadata=METADATA)
    changed_metadata = dict(METADATA, run_id="different-run")
    third = write_registry_checkpoint(tmp_path / "third", _snapshot(), metadata=changed_metadata)

    assert len({first, second, third}) == 3
    with pytest.raises(ValueError, match="metadata"):
        read_registry_checkpoint(
            tmp_path / "first", expected_digest=first,
            expected_metadata=dict(METADATA, run_id="foreign"))
    with pytest.raises(ValueError, match="digest"):
        read_registry_checkpoint(
            tmp_path / "first", expected_digest="0" * 64,
            expected_metadata=METADATA)


def test_existing_artifact_is_never_overwritten(tmp_path):
    artifact = tmp_path / "checkpoint"
    first = write_registry_checkpoint(artifact, _snapshot(), metadata=METADATA)
    original_manifest = (artifact / "manifest.json").read_bytes()

    with pytest.raises(FileExistsError):
        write_registry_checkpoint(
            artifact, _snapshot(extension_value=99), metadata=METADATA)

    assert (artifact / "manifest.json").read_bytes() == original_manifest
    assert read_registry_checkpoint(
        artifact, expected_digest=first,
        expected_metadata=METADATA)["extension"]["state"]["scalar_array"].item() == 7


def test_publish_is_one_complete_bounded_directory(tmp_path):
    artifact = tmp_path / "checkpoint"
    write_registry_checkpoint(artifact, _snapshot(), metadata=METADATA)

    assert {entry.name for entry in artifact.iterdir()} == {"manifest.json", "blobs"}
    assert all(path.is_file() for path in (artifact / "blobs").iterdir())
    assert not tuple(tmp_path.glob(".checkpoint.tmp-*"))

    existing = tmp_path / "existing"
    existing.mkdir()
    with pytest.raises(FileExistsError):
        write_registry_checkpoint(existing, _snapshot(), metadata=METADATA)
    with pytest.raises(ValueError, match="parent"):
        write_registry_checkpoint(tmp_path / "missing" / "checkpoint", _snapshot(), metadata=METADATA)


def test_blob_corruption_is_rejected(tmp_path):
    artifact = tmp_path / "checkpoint"
    digest = write_registry_checkpoint(artifact, _snapshot(), metadata=METADATA)
    blob = next((artifact / "blobs").iterdir())
    content = bytearray(blob.read_bytes())
    content[0] ^= 0xFF
    blob.write_bytes(content)

    with pytest.raises(ValueError, match="checksum"):
        read_registry_checkpoint(artifact, expected_digest=digest, expected_metadata=METADATA)


def test_truncated_manifest_is_rejected(tmp_path):
    artifact = tmp_path / "checkpoint"
    digest = write_registry_checkpoint(artifact, _snapshot(), metadata=METADATA)
    manifest = artifact / "manifest.json"
    manifest.write_bytes(manifest.read_bytes()[:31])

    with pytest.raises(ValueError, match="digest"):
        read_registry_checkpoint(artifact, expected_digest=digest, expected_metadata=METADATA)


@pytest.mark.parametrize("location", ["root", "blobs"])
def test_unexpected_artifact_entries_are_rejected(tmp_path, location):
    artifact = tmp_path / "checkpoint"
    digest = write_registry_checkpoint(artifact, _snapshot(), metadata=METADATA)
    directory = artifact if location == "root" else artifact / "blobs"
    (directory / "unexpected").write_bytes(b"x")

    with pytest.raises(ValueError, match="unexpected"):
        read_registry_checkpoint(artifact, expected_digest=digest, expected_metadata=METADATA)


def test_missing_blob_is_rejected(tmp_path):
    artifact = tmp_path / "checkpoint"
    digest = write_registry_checkpoint(artifact, _snapshot(), metadata=METADATA)
    next((artifact / "blobs").iterdir()).unlink()

    with pytest.raises(ValueError, match="missing or unexpected"):
        read_registry_checkpoint(artifact, expected_digest=digest, expected_metadata=METADATA)


def test_symlinked_artifact_entries_and_parent_are_rejected(tmp_path):
    real_parent = tmp_path / "real-parent"
    real_parent.mkdir()
    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(real_parent, target_is_directory=True)
    with pytest.raises(ValueError, match="parent"):
        write_registry_checkpoint(
            linked_parent / "checkpoint", _snapshot(), metadata=METADATA)

    artifact = tmp_path / "checkpoint"
    digest = write_registry_checkpoint(artifact, _snapshot(), metadata=METADATA)
    manifest = artifact / "manifest.json"
    real_manifest = artifact / "manifest.real"
    manifest.rename(real_manifest)
    manifest.symlink_to(real_manifest.name)
    with pytest.raises(ValueError, match="regular file"):
        read_registry_checkpoint(artifact, expected_digest=digest, expected_metadata=METADATA)


def test_rehashed_unsupported_format_is_still_rejected(tmp_path):
    artifact = tmp_path / "checkpoint"
    write_registry_checkpoint(artifact, _snapshot(), metadata=METADATA)
    _, digest = _canonical_manifest(
        artifact, lambda manifest: manifest.__setitem__("format_version", "future.v99"))

    with pytest.raises(ValueError, match="format version"):
        read_registry_checkpoint(artifact, expected_digest=digest, expected_metadata=METADATA)


def test_rehashed_participant_payload_schema_is_still_rejected(tmp_path):
    artifact = tmp_path / "checkpoint"
    write_registry_checkpoint(artifact, _snapshot(), metadata=METADATA)

    def add_payload_field(manifest):
        manifest["participants"]["world"]["unregistered"] = True

    _, digest = _canonical_manifest(artifact, add_payload_field)
    with pytest.raises(ValueError, match="schema mismatch"):
        read_registry_checkpoint(artifact, expected_digest=digest, expected_metadata=METADATA)


def test_rehashed_missing_participant_is_still_rejected(tmp_path):
    artifact = tmp_path / "checkpoint"
    write_registry_checkpoint(artifact, _snapshot(), metadata=METADATA)

    def remove_world(manifest):
        del manifest["participants"]["world"]

    _, digest = _canonical_manifest(artifact, remove_world)
    with pytest.raises(ValueError, match="missing required participants"):
        read_registry_checkpoint(artifact, expected_digest=digest, expected_metadata=METADATA)


def test_rehashed_state_that_decodes_to_non_mapping_is_rejected(tmp_path):
    artifact = tmp_path / "checkpoint"
    write_registry_checkpoint(artifact, _snapshot(), metadata=METADATA)

    def replace_state(manifest):
        manifest["participants"]["malicious"] = {
            "version": "v1",
            "state": {"__codec__": "list", "items": [1, 2, 3]},
        }

    _, digest = _canonical_manifest(artifact, replace_state)
    with pytest.raises(ValueError, match="state must be a mapping"):
        read_registry_checkpoint(artifact, expected_digest=digest, expected_metadata=METADATA)


def test_rehashed_noncanonical_blob_id_is_rejected(tmp_path):
    artifact = tmp_path / "checkpoint"
    write_registry_checkpoint(artifact, _snapshot(), metadata=METADATA)

    def replace_blob_id(manifest):
        original = next(iter(manifest["arrays"]))
        manifest["arrays"]["array-00000x"] = manifest["arrays"].pop(original)

    _, digest = _canonical_manifest(artifact, replace_blob_id)
    with pytest.raises(ValueError, match="index"):
        read_registry_checkpoint(artifact, expected_digest=digest, expected_metadata=METADATA)


@pytest.mark.parametrize(
    "payload",
    [
        {"version": "", "state": {}},
        {"version": "v1", "state": [], "extra": True},
        {"version": "v1", "state": []},
    ],
)
def test_participant_schema_is_strict_on_write(tmp_path, payload):
    snapshot = _snapshot()
    snapshot["world"] = payload
    with pytest.raises(ValueError, match="participant"):
        write_registry_checkpoint(tmp_path / "checkpoint", snapshot, metadata=METADATA)


def test_missing_required_participant_is_rejected_on_write(tmp_path):
    snapshot = _snapshot()
    del snapshot["rng"]
    with pytest.raises(ValueError, match="missing required participants"):
        write_registry_checkpoint(tmp_path / "checkpoint", snapshot, metadata=METADATA)


def _snapshot_with_value(value):
    snapshot = _snapshot()
    snapshot["world"]["state"] = {"value": value}
    return snapshot


@pytest.mark.parametrize(
    "value, error",
    [
        (np.float32(1.0), "NumPy scalar"),
        (float("inf"), "nonfinite"),
        (np.array([1.0, np.nan]), "nonfinite"),
        (np.array([object()], dtype=object), "dtype"),
        ({1: "not-a-string-key"}, "keys"),
    ],
)
def test_unsupported_or_lossy_values_fail_closed(tmp_path, value, error):
    with pytest.raises((TypeError, ValueError), match=error):
        write_registry_checkpoint(
            tmp_path / "checkpoint", _snapshot_with_value(value), metadata=METADATA)


def test_torch_values_fail_closed_without_importing_torch(tmp_path):
    fake_tensor_type = type("Tensor", (), {"__module__": "torch"})
    with pytest.raises(TypeError, match="Torch"):
        write_registry_checkpoint(
            tmp_path / "checkpoint", _snapshot_with_value(fake_tensor_type()),
            metadata=METADATA)


def test_cyclic_state_fails_closed(tmp_path):
    cycle = []
    cycle.append(cycle)
    with pytest.raises(ValueError, match="cyclic"):
        write_registry_checkpoint(
            tmp_path / "checkpoint", _snapshot_with_value(cycle), metadata=METADATA)


@pytest.mark.parametrize(
    "metadata",
    [
        {"run": 1},
        {1: "run"},
        {"": "run"},
        {"run": {"nested": "unsupported"}},
    ],
)
def test_metadata_is_string_to_string_only(tmp_path, metadata):
    with pytest.raises(TypeError, match="string"):
        write_registry_checkpoint(
            tmp_path / "checkpoint", _snapshot(), metadata=metadata)


def test_path_dimension_and_byte_limits_fail_before_publication(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="path"):
        write_registry_checkpoint(tmp_path / ("x" * 256), _snapshot(), metadata=METADATA)

    too_many_dimensions = np.zeros((1,) * 9, dtype=np.uint8)
    with pytest.raises(ValueError, match="shape"):
        write_registry_checkpoint(
            tmp_path / "dimensions", _snapshot_with_value(too_many_dimensions),
            metadata=METADATA)

    monkeypatch.setattr(checkpoint_store, "MAX_ARRAY_BYTES", 3)
    with pytest.raises(ValueError, match="bytes"):
        write_registry_checkpoint(
            tmp_path / "bytes", _snapshot_with_value(np.zeros(4, dtype=np.uint8)),
            metadata=METADATA)
    assert not (tmp_path / "dimensions").exists()
    assert not (tmp_path / "bytes").exists()


def test_read_rejects_rehashed_array_shape_beyond_limit(tmp_path):
    artifact = tmp_path / "checkpoint"
    write_registry_checkpoint(artifact, _snapshot(), metadata=METADATA)

    def mutate_shape(manifest):
        first = next(iter(manifest["arrays"].values()))
        first["shape"] = [1] * 9

    _, digest = _canonical_manifest(artifact, mutate_shape)
    with pytest.raises(ValueError, match="shape"):
        read_registry_checkpoint(artifact, expected_digest=digest, expected_metadata=METADATA)


@pytest.mark.parametrize(
    "node",
    [
        {"__codec__": "ndarray", "items": []},
        {"__codec__": "list", "id": "array-000000"},
        {"__codec__": ["list"], "items": []},
        {"__codec__": "list", "items": [], "id": "array-000000"},
    ],
)
def test_malformed_typed_nodes_raise_value_error(tmp_path, node):
    artifact = tmp_path / "checkpoint"
    write_registry_checkpoint(artifact, _minimal_snapshot(), metadata=METADATA)

    def replace_state(manifest):
        manifest["participants"]["world"]["state"] = node

    _, digest = _canonical_manifest(artifact, replace_state)
    with pytest.raises(ValueError, match="typed checkpoint node"):
        read_registry_checkpoint(artifact, expected_digest=digest, expected_metadata=METADATA)


@pytest.mark.parametrize("field", ["mapping_key", "participant_id", "version"])
def test_all_state_schema_strings_obey_the_same_byte_limit(tmp_path, monkeypatch, field):
    monkeypatch.setattr(checkpoint_store, "MAX_STRING_BYTES", 10)
    snapshot = _minimal_snapshot()
    if field == "mapping_key":
        snapshot["world"]["state"] = {"x" * 11: 1}
        match = "mapping key"
    elif field == "participant_id":
        snapshot["x" * 11] = {"version": "v1", "state": {}}
        match = "participant ID"
    else:
        snapshot["world"]["version"] = "v" * 11
        match = "version"
    with pytest.raises(ValueError, match=match):
        write_registry_checkpoint(tmp_path / "checkpoint", snapshot, metadata={"run": "v1"})


def test_decode_rechecks_mapping_key_byte_limit(tmp_path, monkeypatch):
    snapshot = _minimal_snapshot()
    snapshot["world"]["state"] = {"x" * 11: 1}
    artifact = tmp_path / "checkpoint"
    metadata = {"run": "v1"}
    digest = write_registry_checkpoint(artifact, snapshot, metadata=metadata)

    monkeypatch.setattr(checkpoint_store, "MAX_STRING_BYTES", 10)
    with pytest.raises(ValueError, match="mapping key"):
        read_registry_checkpoint(artifact, expected_digest=digest, expected_metadata=metadata)


def test_fresh_interpreter_can_restore_without_pickle_or_runtime_objects(tmp_path):
    artifact = tmp_path / "checkpoint"
    digest = write_registry_checkpoint(artifact, _snapshot(), metadata=METADATA)
    script = """
import json
import sys
from benchmark.checkpoint_store_v3 import read_registry_checkpoint

snapshot = read_registry_checkpoint(
    sys.argv[1], expected_digest=sys.argv[2],
    expected_metadata={"run_id": "durable-test", "source_digest": "a" * 64})
array = snapshot["world"]["state"]["array"]
scalar = snapshot["extension"]["state"]["scalar_array"]
print(json.dumps({
    "dtype": array.dtype.str,
    "shape": list(array.shape),
    "tuple": isinstance(snapshot["world"]["state"]["tuple"], tuple),
    "scalar_shape": list(scalar.shape),
    "scalar": int(scalar),
    "extension": "extension" in snapshot,
}))
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(artifact), digest],
        cwd=Path(__file__).parents[2],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    evidence = json.loads(result.stdout)
    assert evidence == {
        "dtype": ">i2",
        "shape": [3, 2],
        "tuple": True,
        "scalar_shape": [],
        "scalar": 7,
        "extension": True,
    }
