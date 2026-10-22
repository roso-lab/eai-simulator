from __future__ import annotations

from benchmark import composition_v3
from benchmark.records_v3 import Branch, IdentityV3, MetricResultV3, RecordStatus
from benchmark.scorebook_v3 import SCOREBOOK_VERSION, default_scorebook_v3


def _identity(**overrides):
    values = dict(
        protocol_version="protocol.v3",
        scorebook_digest="scorebook",
        catalog_digest="catalog",
        design_digest="design",
        task_id="task-1",
        anchor_id="anchor-1",
        domain="production",
        relation_signature=("P2",),
        difficulty="L1",
        condition_id="nominal",
        system_id="system-1",
        base_instance_id="instance-1",
        repeat_id="r1",
        branch=Branch.FACT,
        checkpoint_id="cp-start",
    )
    values.update(overrides)
    return IdentityV3(**values)


def test_scorebook_has_six_versioned_composites_and_legacy_map():
    scorebook = default_scorebook_v3()
    assert scorebook.version == SCOREBOOK_VERSION
    assert tuple(item.score_id for item in scorebook.specs) == ("D1", "D2", "D3", "D4", "D5", "D6")
    assert scorebook.digest() == scorebook.digest()
    assert "D2.success_retention" in scorebook.legacy_endpoint_map["D2"]
    assert "C0-C3 numeric rates and queue limits" in scorebook.unresolved_parameters


def test_identity_keeps_d5_dimensions_out_of_metadata():
    identity = _identity(family_id="fam-1", case_id="case-1", adaptation_unit_id="unit-1", target_checkpoint_id="target-cp")
    payload = identity.to_dict()
    assert payload["family_id"] == "fam-1"
    assert payload["adaptation_unit_id"] == "unit-1"
    assert "family_id" not in payload.get("metadata", {})


def test_metric_result_supports_explicit_range_and_missing_states():
    result = MetricResultV3(identity=_identity(), score_id="D5", status=RecordStatus.VALID, lower=40.0, upper=70.0, support_weight=0.25, estimator_family="weighted_hoeffding")
    assert result.lower == 40.0 and result.upper == 70.0


def test_composition_records_asset_roots_and_commit_without_importing_simulator(monkeypatch):
    expected_commit = "0123456789abcdef0123456789abcdef01234567"
    monkeypatch.setattr(composition_v3, "current_git_commit", lambda cwd: expected_commit)
    manifest = composition_v3.build_composition_manifest(cwd="/home/pupil/HeteroCoord", protocol_version="protocol.v3", scorebook_digest="abc", entry_mode="cpu-contract-test", step_owner="test")
    assert manifest.project_commit == expected_commit
    assert "EAI_NVD_ASSET_ROOT" in manifest.asset_roots
    assert manifest.digest() == manifest.digest()
