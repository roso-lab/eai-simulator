from __future__ import annotations

from copy import deepcopy

import pytest

from demo.benchmark.development_track_marker import (
    FACTORY_FIXTURE_HASH_MISMATCH,
    build_development_marker,
    factory_hash_mismatch_marker,
    merge_development_markers,
    stamp_evidence_record,
    validate_development_marker,
)


def test_development_marker_keeps_release_isolation():
    marker = build_development_marker(
        "factory",
        asset_status="development_unverified",
        evidence_class="contract",
        infrastructure_blockers=[FACTORY_FIXTURE_HASH_MISMATCH],
    )
    assert marker["asset_status"] == "development_unverified"
    assert marker["evidence_class"] == "contract"
    assert marker["infrastructure_blockers"] == [FACTORY_FIXTURE_HASH_MISMATCH]
    assert marker["release_eligible"] is False
    assert marker["scoreable"] is False
    assert marker["execution_validated"] is False
    assert marker["business_outcomes_validated"] is False


def test_factory_marker_is_contract_evidence_under_hash_mismatch():
    marker = factory_hash_mismatch_marker()
    assert validate_development_marker(marker)["scene_key"] == "factory"
    assert FACTORY_FIXTURE_HASH_MISMATCH in marker["infrastructure_blockers"]


def test_frozen_verified_marker_still_cannot_promote_release():
    marker = build_development_marker(
        "city", asset_status="frozen_verified", evidence_class="raw")
    assert marker["release_eligible"] is False
    assert marker["scoreable"] is False


def test_unverified_assets_require_a_blocker_reason():
    with pytest.raises(ValueError, match="blocker"):
        build_development_marker(
            "city", asset_status="development_unverified", evidence_class="raw")


@pytest.mark.parametrize("field,value", [
    ("asset_status", "unverified"),
    ("evidence_class", "formal"),
    ("evidence_class", ""),
])
def test_unknown_statuses_and_classes_fail_closed(field, value):
    kwargs = {"asset_status": "frozen_verified", "evidence_class": "raw"}
    kwargs[field] = value
    with pytest.raises(ValueError):
        build_development_marker("city", **kwargs)


def test_tampered_marker_digest_and_release_flags_are_rejected():
    marker = factory_hash_mismatch_marker(evidence_class="diagnostic")
    tampered = deepcopy(marker)
    tampered["infrastructure_blockers"] = []
    with pytest.raises(ValueError):
        validate_development_marker(tampered)
    promoted = deepcopy(marker)
    promoted["release_eligible"] = True
    with pytest.raises(ValueError):
        validate_development_marker(promoted)
    scored = deepcopy(marker)
    scored["scoreable"] = True
    with pytest.raises(ValueError):
        validate_development_marker(scored)


def test_merge_boundary_is_restrictive_across_scenes():
    boundary = merge_development_markers([
        factory_hash_mismatch_marker(),
        build_development_marker("city", asset_status="frozen_verified",
                                 evidence_class="contract"),
    ])
    assert boundary["development_unverified_scenes"] == ["factory"]
    assert FACTORY_FIXTURE_HASH_MISMATCH in boundary["infrastructure_blockers"]
    assert boundary["release_eligible"] is False
    assert boundary["scoreable"] is False
    assert set(boundary["scene_markers"]) == {"factory", "city"}


def test_merge_rejects_one_bad_marker():
    bad = factory_hash_mismatch_marker()
    bad["evidence_class"] = "formal"
    with pytest.raises(ValueError):
        merge_development_markers([bad])


def test_stamped_records_carry_evidence_role_not_truth():
    marker = factory_hash_mismatch_marker(evidence_class="diagnostic")
    stamped = stamp_evidence_record({"reader_status": "verified"}, marker)
    assert stamped["evidence_role"] == "development_track_evidence"
    assert stamped["formal_statistics"] is False
    assert stamped["development_track"]["scene_key"] == "factory"
    # The original record is never mutated by stamping.
    assert stamped["reader_status"] == "verified"
    with pytest.raises(ValueError):
        stamp_evidence_record({}, factory_hash_mismatch_marker()["infrastructure_blockers"])
