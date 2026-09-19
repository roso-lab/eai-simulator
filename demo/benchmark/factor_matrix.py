"""Public factor axes and private episode-condition policy for benchmark tasks.

The matrix is deliberately separate from the semantic prose in ScenarioSpec.
It tells a participant which comparisons are meaningful while telling the
evaluator which evidence track can actually score each level.  A declared
factor is never treated as an executed intervention by itself.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping


_MATRIX = {
    "factory": (
        {
            "id": "information_asymmetry",
            "levels": ("known_location", "unresolved_location"),
            "track": "factory_dispatch",
            "evidence": "observation message provenance and pre-action consumption",
            "status": "implemented_conditional",
        },
        {
            "id": "shared_aisle_contention",
            "levels": ("open", "reserved", "blocked"),
            "track": "scheduler_geometry",
            "evidence": "resource reservation and route admission trace",
            "status": "implemented_conditional",
        },
        {
            "id": "team_continuity",
            "levels": ("stable", "member_exit", "replacement_join"),
            "track": "protocol_components",
            "evidence": "causal lifecycle and recovery trace",
            "status": "protocol_only",
        },
        {
            "id": "vertical_access",
            "levels": ("ground_only", "measured_link_required"),
            "track": "vertical_probe",
            "evidence": "support-relative XYZ/WXYZ and level-progress samples",
            "status": "probe_only",
        },
        {
            "id": "communication_quality",
            "levels": ("immediate", "delayed", "dropped", "bandwidth_limited", "stale"),
            "track": "protocol_components",
            "evidence": "private delivery, consumption and expiry ledger",
            "status": "protocol_only",
        },
    ),
    "warehouse_nvd": (
        {
            "id": "order_priority",
            "levels": ("routine", "priority"),
            "track": "domain_contract",
            "evidence": "order line, deadline and dispatch decision",
            "status": "design_axis_fixed_dag",
        },
        {
            "id": "aisle_clearance",
            "levels": ("open", "constrained", "blocked"),
            "track": "scheduler_geometry",
            "evidence": "continuous route admission and resource trace",
            "status": "implemented_conditional",
        },
        {
            "id": "payload_team_need",
            "levels": ("single_carton", "joint_oversized_crate"),
            "track": "kinematic_ownership",
            "evidence": "pickup team, ownership transitions and measured release",
            "status": "implemented_kinematic",
        },
        {
            "id": "team_continuity",
            "levels": ("stable", "member_exit", "replacement_join"),
            "track": "protocol_components",
            "evidence": "lifecycle event, release obligation and reassignment trace",
            "status": "protocol_only",
        },
        {
            "id": "communication_quality",
            "levels": ("immediate", "delayed", "dropped", "bandwidth_limited", "stale"),
            "track": "protocol_components",
            "evidence": "private delivery, consumption and expiry ledger",
            "status": "implemented_conditional",
        },
    ),
    "modern_house_nvd": (
        {
            "id": "room_access",
            "levels": ("clear", "furniture_constrained", "blocked"),
            "track": "domain_access",
            "evidence": "before/after route records and measured entity bounds",
            "status": "implemented_conditional",
        },
        {
            "id": "resident_safety",
            "levels": ("circulation_preserved", "circulation_at_risk"),
            "track": "domain_access",
            "evidence": "final access width and post-closeout occupancy record",
            "status": "implemented_conditional",
        },
        {
            "id": "household_item_identity",
            "levels": ("located", "not_located"),
            "track": "domain_identity",
            "evidence": "private object identity, detection source and pre-action consumption",
            "status": "implemented_fixture_only",
        },
        {
            "id": "team_continuity",
            "levels": ("stable", "member_exit", "capability_change"),
            "track": "protocol_components",
            "evidence": "causal lifecycle and recovery trace",
            "status": "protocol_only",
        },
        {
            "id": "communication_quality",
            "levels": ("immediate", "delayed", "dropped", "bandwidth_limited", "stale"),
            "track": "protocol_components",
            "evidence": "private delivery, consumption and expiry ledger",
            "status": "implemented_conditional",
        },
    ),
}


def _validate_rows(scene_key: str, rows: tuple[Mapping[str, Any], ...]) -> None:
    if scene_key not in _MATRIX:
        raise ValueError(f"unsupported factor-matrix scene: {scene_key}")
    identifiers = set()
    for row in rows:
        identifier = row.get("id")
        levels = row.get("levels")
        if not isinstance(identifier, str) or not identifier or identifier in identifiers:
            raise ValueError("factor ids must be unique nonempty strings")
        if not isinstance(levels, (tuple, list)) or len(levels) < 2 or any(
                not isinstance(level, str) or not level for level in levels):
            raise ValueError(f"{identifier}: factor needs at least two named levels")
        if not isinstance(row.get("track"), str) or not row["track"]:
            raise ValueError(f"{identifier}: factor needs an evidence track")
        if not isinstance(row.get("evidence"), str) or not row["evidence"]:
            raise ValueError(f"{identifier}: factor needs an evidence description")
        if row.get("status") not in {
            "implemented_conditional", "implemented_kinematic", "implemented_fixture_only",
            "protocol_only", "probe_only", "design_axis_fixed_dag",
        }:
            raise ValueError(f"{identifier}: unknown implementation status")
        identifiers.add(identifier)
    required = {"team_continuity", "communication_quality"}
    if not required <= identifiers:
        raise ValueError(f"{scene_key}: factor matrix must include lifecycle and communication")


def build_public_factor_manifest(scene_key: str) -> dict[str, Any]:
    """Return the participant-visible factor axes without hidden schedules."""
    rows = _MATRIX.get(scene_key)
    if rows is None:
        raise ValueError(f"unsupported factor-matrix scene: {scene_key}")
    _validate_rows(scene_key, rows)
    return {
        "schema": "eai.benchmark.factor_manifest",
        "version": 1,
        "scene_key": scene_key,
        "scope": "scene_factor_catalog_not_episode_conditions",
        "comparison_rule": "vary one declared factor; freeze all non-target inputs, observations, budget and scoring thresholds",
        "axes": [{**deepcopy(dict(row)), "levels": list(row["levels"])} for row in rows],
        "participant_visibility": "factor names and admissible levels are public; intervention schedule and private bindings are evaluator-only",
        "score_separation": ["business_outcome", "protocol_correctness", "recovery", "motion_baseline"],
    }


def build_hidden_factor_policy(scene_key: str) -> dict[str, Any]:
    """Return evaluator-owned condition rules, without selecting an episode."""
    public = build_public_factor_manifest(scene_key)
    return {
        "schema": "eai.benchmark.hidden_factor_policy",
        "version": 1,
        "scene_key": scene_key,
        "axes": deepcopy(public["axes"]),
        "condition_sources": {
            "communication_quality": "demo.benchmark.communication.CommunicationRuntime",
            "team_continuity": "demo.benchmark.team_lifecycle.TeamLifecycle",
            "member_exit": "preloaded ground-only replacement path where the Isaac contract permits it",
            "member_join": "preloaded reserve parking and command-gate activation through the MultiRobotDirectEnv callback/readback binding",
            "capability_change": "MultiRobotDirectEnv navigation-interface command admission with versioned readback; other physical capability changes require a scene adapter",
        },
        "private_fields": ["selected_level", "event_time_s", "reserve_identity", "message_delivery_schedule", "reference_decision"],
        "one_factor_at_a_time": True,
        "execution_validated": False,
    }


def validate_factor_manifest(manifest: Mapping[str, Any], *, scene_key: str) -> dict[str, Any]:
    if not isinstance(manifest, Mapping) or manifest.get("schema") != "eai.benchmark.factor_manifest":
        raise ValueError("unsupported factor manifest")
    if manifest.get("version") != 1 or manifest.get("scene_key") != scene_key:
        raise ValueError("factor manifest version or scene mismatch")
    axes = manifest.get("axes")
    if not isinstance(axes, list):
        raise ValueError("factor manifest axes must be a list")
    _validate_rows(scene_key, tuple(axes))
    expected = build_public_factor_manifest(scene_key)
    if manifest.get("comparison_rule") != expected["comparison_rule"]:
        raise ValueError("factor manifest comparison rule is not frozen")
    if dict(manifest) != expected:
        raise ValueError("factor manifest differs from the reviewed catalog; support cannot be promoted by metadata")
    return {"validated": True, "scene_key": scene_key, "axis_ids": [row["id"] for row in axes]}


__all__ = ["build_public_factor_manifest", "build_hidden_factor_policy", "validate_factor_manifest"]
