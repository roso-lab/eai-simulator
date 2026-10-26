from __future__ import annotations

import json
from pathlib import Path

import pytest

from generate_runtime_roster import build
from skill_fingerprint_impact import affected_entries, parse_changes


HERE = Path(__file__).resolve().parent


def test_v31_selection_and_exclusions():
    doc, audit, selected = build()
    assert len(selected) == len(doc["robots"]) == len(audit) == 27
    assert doc["membership_counts"] == {"total": 27, "pool": 25, "independent_arm_immediate": 2}
    ids = {row["source_resume_id"] for row in audit}
    assert "carter.CARTER_DIFF_CFG.v0002" not in ids
    assert "m20.M20_ROUGH_RSL_CFG.v0002" not in ids
    assert not any("deprecated" in row["source_resume_id"] for row in audit)


def test_three_digests_and_gates_are_fail_closed():
    doc, _, _ = build()
    assert all(len(r["asset_binding"]["asset_digest"]) == 64 for r in doc["robots"])
    assert all(len(r["capability_binding"]["capability_digest"]) == 64 for r in doc["robots"])
    assert all(len(r["initial_state_binding"]["initial_state_digest"]) == 64 for r in doc["robots"])
    assert all(r["runtime_qualified"] is False for r in doc["robots"])
    assert all(v is False for v in doc["formal_gates"].values())


def test_restriction_markers_exact_counts():
    doc, _, _ = build()
    counts = {key: 0 for key in doc["restriction_codes"]}
    for robot in doc["robots"]:
        for code in robot["restrictions"]:
            counts[code] += 1
    assert counts == {
        "GO2_OFFROAD_MOTION_UNSTABLE": 2,
        "G1_WALK_GRASP_MODE_EXCLUSIVE_AND_STAIRS_UNSTABLE": 1,
        "CF2X_NO_PICK_PLACE_ADAPTER": 1,
        "LITE3_STAIRS_UNSUPPORTED": 2,
    }


def test_skill_change_impacts_only_bound_entries_and_forces_revalidation():
    doc, _, _ = build()
    changes = parse_changes(["eai.manipulation.pick_place=9.9.9"])
    impacted = affected_entries(doc, changes)
    expected = {r["instance_id"] for r in doc["robots"] if any(s["skill_id"] == "eai.manipulation.pick_place" for s in r["capability_binding"]["skill_bindings"])}
    assert {row["instance_id"] for row in impacted} == expected
    assert impacted and all("recompute_capability_digest" in row["actions"] for row in impacted)
    assert all(row["runtime_qualified_after_change"] is False for row in impacted)


def test_skill_change_same_version_is_noop_and_malformed_rejected():
    doc, _, _ = build()
    current = next(r for r in doc["robots"] if r["capability_binding"]["skill_bindings"])["capability_binding"]["skill_bindings"][0]
    assert affected_entries(doc, {current["skill_id"]: current["provider_version"]}) == []
    with pytest.raises(ValueError):
        parse_changes(["not-a-version"])

