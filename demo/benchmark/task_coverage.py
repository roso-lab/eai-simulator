"""Offline validation for the five-scene collaboration coverage registry."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping


SCENES = ("factory", "warehouse_nvd", "modern_house_nvd", "city", "restaurant")
M0_MECHANISMS = (
    "decomposition", "role_allocation", "communication_adaptation", "role_churn_recovery",
)
M1_MECHANISMS = (
    "timing_synchronization", "resource_contention", "recovery_replanning",
    "multi_bottleneck_calibration",
)
EXPECTED_MECHANISMS = set(M0_MECHANISMS) | set(M1_MECHANISMS)


def _expected_scene_template_ids(scene: str) -> list[str]:
    return (
        [f"{scene}.m0.{mechanism}" for mechanism in M0_MECHANISMS]
        + [f"{scene}.m1.{mechanism}" for mechanism in M1_MECHANISMS]
    )


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
    if value["schema"] != "eai.benchmark.task_coverage_matrix" or value["version"] != 2:
        raise ValueError("unsupported coverage matrix schema")
    if value["status"] != "authoring_contract":
        raise ValueError("coverage matrix must remain an authoring contract")
    if tuple(value["scene_keys"]) != SCENES:
        raise ValueError("coverage matrix must list the five scenes in registry order")
    boundary = value["claim_boundary"]
    if not isinstance(boundary, Mapping) or "forbidden" not in boundary or "unknown_states" not in boundary:
        raise ValueError("coverage matrix claim boundary is incomplete")
    if "physical_causality" not in boundary["forbidden"] or "external_pending" not in boundary["unknown_states"]:
        raise ValueError("coverage matrix must fail closed on physical and pending claims")
    counts = value["counting_policy"]
    for key in (
        "current_goal_packages", "current_factor_pairs", "current_canonical_templates",
        "m0_target_canonical_templates", "m1_target_canonical_templates",
        "baseline_canonical_templates", "baseline_condition_packages",
        "conditions_per_canonical_template", "full_grid_canonical_templates",
        "full_grid_goal_packages", "full_grid_factor_pairs",
    ):
        if not isinstance(counts.get(key), int) or counts[key] < 0:
            raise ValueError(f"coverage count {key} must be a nonnegative integer")
    expected_counts = {
        "current_goal_packages": 45,
        "current_factor_pairs": 35,
        "current_canonical_templates": 40,
        "m0_target_canonical_templates": 20,
        "m1_target_canonical_templates": 20,
        "baseline_canonical_templates": 5,
        "baseline_condition_packages": 45,
        "conditions_per_canonical_template": 9,
        "full_grid_canonical_templates": 45,
        "full_grid_goal_packages": 405,
        "full_grid_factor_pairs": 315,
    }
    for key, expected in expected_counts.items():
        if counts[key] != expected:
            raise ValueError(f"coverage matrix count {key} must be {expected}")
    if counts["full_grid_goal_packages"] != (
            counts["full_grid_canonical_templates"]
            * counts["conditions_per_canonical_template"]):
        raise ValueError("coverage full-grid package count identity is inconsistent")
    taxonomy = value["mechanism_taxonomy"]
    if not isinstance(taxonomy, list) or {row.get("mechanism_id") for row in taxonomy} != EXPECTED_MECHANISMS:
        raise ValueError("coverage taxonomy must contain exactly the eight registered mechanisms")
    for row in taxonomy:
        mechanism_id = row["mechanism_id"]
        if row.get("m0") is True and mechanism_id not in M0_MECHANISMS:
            raise ValueError("non-M0 mechanism cannot be marked as an M0 target")
        if mechanism_id in M0_MECHANISMS and row.get("m0") is not True:
            raise ValueError("M0 mechanism must remain marked as an M0 target")
        if mechanism_id in M1_MECHANISMS and row.get("m0") is not False:
            raise ValueError("M1 mechanism must remain marked m0=false")
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
        expected_ids = _expected_scene_template_ids(scene)
        listed = row.get("current_canonical_templates")
        if not isinstance(listed, list) or listed != expected_ids:
            raise ValueError(f"scene {scene} must list the eight M0+M1 canonical template ids")
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
    that want the stronger 20-template assertion.
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
    if any(".m1." in template_id for template_id in ids):
        raise ValueError("M0 registry validation must not include M1 template ids")
    return {"validated": True, "canonical_template_count": len(rows),
            "scene_count": len(scenes), "mechanism_count": len(mechanisms),
            "execution_validated": False, "scoreable": False}


def validate_m1_template_registry(*, scenes: tuple[str, ...] = SCENES,
                                  mechanisms: tuple[str, ...] = M1_MECHANISMS) -> dict[str, Any]:
    """Check that the authoring registry covers the declared M1 grid (20 rows).

    This is independent of :func:`validate_m0_template_registry`, which must
    continue to assert exactly the 20 M0 templates.
    """
    from .mechanism_templates import M1_MECHANISMS as registry_m1
    from .mechanism_templates import registered_mechanism_templates

    if tuple(scenes) != SCENES or tuple(mechanisms) != M1_MECHANISMS:
        raise ValueError("M1 registry validation must use the canonical scene and mechanism order")
    if tuple(mechanisms) != registry_m1:
        raise ValueError("M1 registry mechanisms must match the template module")
    rows = registered_mechanism_templates(scenes=scenes, mechanisms=mechanisms)
    expected = len(scenes) * len(mechanisms)
    if len(rows) != expected or any(row.get("execution_status") != "external_pending" for row in rows):
        raise ValueError("M1 registry must contain one pending template per scene/mechanism pair")
    ids = [row.get("template_id") for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("M1 registry template ids must be unique")
    if any(not isinstance(template_id, str) or ".m1." not in template_id for template_id in ids):
        raise ValueError("M1 registry template ids must use the .m1. tier")
    if any(row.get("scoreable") is not False for row in rows):
        raise ValueError("M1 registry templates cannot claim scoreability")
    return {"validated": True, "canonical_template_count": len(rows),
            "scene_count": len(scenes), "mechanism_count": len(mechanisms),
            "execution_validated": False, "scoreable": False}


__all__ = [
    "M0_MECHANISMS", "M1_MECHANISMS", "load_coverage_matrix",
    "validate_coverage_matrix", "validate_m0_template_registry",
    "validate_m1_template_registry",
]
