"""Offline validation for the four-scene collaboration coverage registry."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping


SCENES = ("factory", "warehouse_nvd", "modern_house_nvd", "city")
M0_MECHANISMS = (
    "decomposition", "role_allocation", "communication_adaptation", "role_churn_recovery",
)
EXPECTED_MECHANISMS = set(M0_MECHANISMS) | {
    "timing_synchronization", "resource_contention", "recovery_replanning",
    "multi_bottleneck_calibration",
}


def load_coverage_matrix(path: str | Path | None = None) -> dict[str, Any]:
    path = Path(path) if path is not None else Path(__file__).with_name("task_coverage_matrix.json")
    with path.open(encoding="utf-8") as stream:
        value = json.load(stream)
    return validate_coverage_matrix(value)["normalized"]


def validate_coverage_matrix(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("coverage matrix must be an object")
    required = {
        "schema", "version", "status", "claim_boundary", "scene_keys", "counting_policy",
        "mechanism_taxonomy", "scene_coverage", "factor_conditions", "coverage_gates",
        "downstream_ownership",
    }
    if set(value) != required:
        raise ValueError("coverage matrix fields mismatch")
    if value["schema"] != "eai.benchmark.task_coverage_matrix" or value["version"] != 1:
        raise ValueError("unsupported coverage matrix schema")
    if value["status"] != "authoring_contract":
        raise ValueError("coverage matrix must remain an authoring contract")
    if tuple(value["scene_keys"]) != SCENES:
        raise ValueError("coverage matrix must list the four scenes in registry order")
    boundary = value["claim_boundary"]
    if not isinstance(boundary, Mapping) or "forbidden" not in boundary or "unknown_states" not in boundary:
        raise ValueError("coverage matrix claim boundary is incomplete")
    if "physical_causality" not in boundary["forbidden"] or "external_pending" not in boundary["unknown_states"]:
        raise ValueError("coverage matrix must fail closed on physical and pending claims")
    counts = value["counting_policy"]
    for key in ("current_goal_packages", "current_factor_pairs", "current_canonical_templates",
                "m0_target_canonical_templates", "m1_target_canonical_templates"):
        if not isinstance(counts.get(key), int) or counts[key] < 0:
            raise ValueError(f"coverage count {key} must be a nonnegative integer")
    taxonomy = value["mechanism_taxonomy"]
    if not isinstance(taxonomy, list) or {row.get("mechanism_id") for row in taxonomy} != EXPECTED_MECHANISMS:
        raise ValueError("coverage taxonomy must contain exactly the eight registered mechanisms")
    for row in taxonomy:
        if row.get("m0") is True and row["mechanism_id"] not in M0_MECHANISMS:
            raise ValueError("non-M0 mechanism cannot be marked as an M0 target")
        for field in ("collaboration_primitive", "measurement_target", "failure_mechanisms",
                      "eligible_metrics", "calibration_role"):
            if field not in row or not row[field]:
                raise ValueError(f"mechanism {row.get('mechanism_id')} lacks {field}")
    coverage = value["scene_coverage"]
    if set(coverage) != set(SCENES):
        raise ValueError("scene coverage must include every registered scene")
    for scene in SCENES:
        row = coverage[scene]
        if set(row.get("m0_required_mechanisms", ())) != set(M0_MECHANISMS):
            raise ValueError(f"scene {scene} does not have the complete M0 mechanism target")
        if row.get("current_status") not in {
            "legacy_measurement_unclassified",
            "m0_authoring_classified_execution_pending",
        }:
            raise ValueError(f"scene {scene} current status is not explicit")
    factors = value["factor_conditions"]
    if set(factors) != {"communication_quality", "team_continuity"}:
        raise ValueError("coverage matrix factor vocabulary mismatch")
    return {"validated": True, "normalized": json.loads(json.dumps(value, sort_keys=True))}


def validate_m0_template_registry(*, scenes: tuple[str, ...] = SCENES,
                                  mechanisms: tuple[str, ...] = M0_MECHANISMS) -> dict[str, Any]:
    """Check that the canonical authoring registry covers the declared M0 grid.

    The import is lazy so reading the static coverage matrix stays independent
    of the goal generator.  This helper is used by release/preflight checks
    that want the stronger 16-template assertion.
    """
    from .mechanism_templates import registered_mechanism_templates

    if tuple(scenes) != SCENES or tuple(mechanisms) != M0_MECHANISMS:
        raise ValueError("M0 registry validation must use the canonical scene and mechanism order")
    rows = registered_mechanism_templates(scenes=scenes, mechanisms=mechanisms)
    expected = len(scenes) * len(mechanisms)
    if len(rows) != expected or any(row.get("execution_status") != "external_pending" for row in rows):
        raise ValueError("M0 registry must contain one pending template per scene/mechanism pair")
    ids = [row.get("template_id") for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("M0 registry template ids must be unique")
    return {"validated": True, "canonical_template_count": len(rows),
            "scene_count": len(scenes), "mechanism_count": len(mechanisms),
            "execution_validated": False, "scoreable": False}


__all__ = ["load_coverage_matrix", "validate_coverage_matrix", "validate_m0_template_registry"]
