import json
from pathlib import Path

import pytest

from benchmark.city_01_checkpoints_v1 import (
    City01CheckpointError,
    checkpoint_metadata,
    validate_checkpoint_policy,
    validate_checkpoint_receipt,
    validate_paired_receipts,
    write_city01_checkpoint,
    read_city01_checkpoint,
)
from benchmark.replay_state_v3 import ReplayStateRegistryV3


ROOT = Path(__file__).parents[1]
MANIFEST_PATH = ROOT / "tasks" / "city_01" / "city_01_task_manifest.v1.json"


def _manifest():
    return json.loads(MANIFEST_PATH.read_text())


def test_city_01_policy_is_bound_to_manifest():
    manifest = _manifest()
    result = validate_checkpoint_policy(manifest["checkpoint_policy"], manifest)
    assert result["checkpoint_ids"] == (
        "cp_00_spawn_valid", "cp_01_patrol_ready", "cp_02_pre_incident",
        "cp_03_observation_complete", "cp_04_recover_ledger_closed",
        "cp_05_ground_rendezvous", "cp_06_closeout",
    )
    assert result["monitor_ids"][-1] == "mp_closeout"


def test_checkpoint_metadata_carries_map_identity():
    manifest = _manifest()
    metadata = checkpoint_metadata(
        manifest,
        task_id="city_01_patrol_emergency_t4",
        episode_id="episode-001",
        repeat_id="repeat-01",
        condition_id="battery_heterogeneous",
        phase_id="incident_onset",
        checkpoint_id="cp_02_pre_incident",
        controller_digest="0" * 64,
    )
    assert metadata["map_revision"] == manifest["semantic_map"]["revision"]
    assert metadata["semantic_binding_digest"] == manifest["semantic_map"]["semantic_binding_digest"]
    assert metadata["checkpoint_id"] == "cp_02_pre_incident"


def test_receipt_requires_external_readback():
    manifest = _manifest()
    receipt = {
        "schema": "eai.city_01.checkpoint-receipt.v1",
        "task_id": "city_01_patrol_emergency_t4",
        "episode_id": "episode-001",
        "repeat_id": "repeat-01",
        "condition_id": "battery_heterogeneous",
        "phase_id": "incident_onset",
        "checkpoint_id": "cp_02_pre_incident",
        "artifact_path": "/tmp/cp-02",
        "artifact_digest": "1" * 64,
        "readback_digest": "2" * 64,
        "readback_verified": True,
        "sim_time_s": 12.5,
        "map_revision_digest": "3" * 64,
        "semantic_binding_digest": manifest["semantic_map"]["semantic_binding_digest"],
    }
    assert validate_checkpoint_receipt(receipt, manifest)["readback_verified"] is True
    receipt["readback_verified"] = False
    with pytest.raises(City01CheckpointError, match="readback"):
        validate_checkpoint_receipt(receipt, manifest)


def test_paired_receipts_must_share_pre_factor_digest():
    rows = [
        {"checkpoint_id": "cp_02_pre_incident", "pair_id": "pair-1", "artifact_digest": "a" * 64},
        {"checkpoint_id": "cp_02_pre_incident", "pair_id": "pair-1", "artifact_digest": "a" * 64},
    ]
    assert validate_paired_receipts(rows, checkpoint_id="cp_02_pre_incident")["branch_count"] == 2
    rows[1]["artifact_digest"] = "b" * 64
    with pytest.raises(City01CheckpointError, match="one artifact digest"):
        validate_paired_receipts(rows, checkpoint_id="cp_02_pre_incident")


def test_durable_checkpoint_round_trip(tmp_path):
    manifest = _manifest()
    snapshot = {
        participant: {"version": "city01-test-v1", "state": {"tick": 10, "phase": "incident_onset"}}
        for participant in ReplayStateRegistryV3.REQUIRED_PARTICIPANTS
    }
    identity = dict(
        task_id="city_01_patrol_emergency_t4",
        episode_id="episode-001",
        repeat_id="repeat-01",
        condition_id="battery_heterogeneous",
        phase_id="incident_onset",
        checkpoint_id="cp_02_pre_incident",
        controller_digest="0" * 64,
    )
    metadata = checkpoint_metadata(manifest, **identity)
    artifact = tmp_path / "cp_02"
    digest = write_city01_checkpoint(artifact, snapshot, manifest, **identity)
    restored = read_city01_checkpoint(artifact, expected_digest=digest, expected_metadata=metadata)
    assert restored == snapshot
