"""Pure metadata and scope boundaries for the Warehouse benchmark.

The Warehouse benchmark is kept separate from the within-Factory protocol.  This
module carries declarations only: it does not claim physical manipulation,
contact, grasp, or payload validation.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

FACTORY_SCENE_KEY = "factory"
WAREHOUSE_SCENE_KEY = "warehouse_nvd"
MODERN_HOUSE_SCENE_KEY = "modern_house_nvd"
WAREHOUSE_R1_ID = "warehouse_r1"

# ``warehouse_r1`` contains two quality variants plus two explicitly
# heterogeneous task candidates.  The quality pair is not a heterogeneous
# necessity claim; each candidate's certificate is recorded separately.
WAREHOUSE_R1_QUALITY_VARIANT_FILES = (
    "trn01r_s17_light_cardbox_d.json",
    "trn01r_s17_heavy_cardbox_a.json",
)
WAREHOUSE_R1_HETEROGENEOUS_CANDIDATE_FILES = (
    "heavy_wh_joint_s17.json",
    "combo_isr_heavy_s23.json",
)
WAREHOUSE_R1_INSTANCE_FILES = WAREHOUSE_R1_QUALITY_VARIANT_FILES + WAREHOUSE_R1_HETEROGENEOUS_CANDIDATE_FILES
WAREHOUSE_R1_METADATA: dict[str, Any] = {
    "benchmark_id": "warehouse_r1",
    "scene_key": WAREHOUSE_SCENE_KEY,
    "instance_count": 4,
    "quality_variant_count": 2,
    "heterogeneous_candidate_count": 2,
    "quality_variants_require_heterogeneous_team": False,
    "physical_manipulation_validated": False,
}

MODERN_HOUSE_R1_METADATA: dict[str, Any] = {
    "benchmark_id": "modern_house_r1",
    "scene_key": MODERN_HOUSE_SCENE_KEY,
    "instance_count": 3,
    "heterogeneous_instance_count": 1,
    "heterogeneous_required": True,
    "team_necessary": True,
    "physical_manipulation_validated": False,
}

# Scope is data so callers can expose it in manifests without importing an
# evaluator or executor.  A missing per-record scene_key remains compatible
# with legacy Factory pool records; an explicit non-Factory key is rejected.
PROTOCOL_SCOPES: dict[str, dict[str, Any]] = {
    "factory": {
        "name": "factory",
        "scene_keys": (FACTORY_SCENE_KEY,),
        "description": "within-Factory family/layout holdout",
    },
    "warehouse": {
        "name": "warehouse",
        "scene_keys": (WAREHOUSE_SCENE_KEY,),
        "description": "Warehouse benchmark metadata scope",
    },
    "modern_house": {
        "name": "modern_house",
        "scene_keys": (MODERN_HOUSE_SCENE_KEY,),
        "description": "Modern House benchmark metadata scope",
    },
}


def protocol_scope_metadata(scope: str) -> dict[str, Any]:
    """Return JSON-ready metadata for a named protocol scope."""
    try:
        value = PROTOCOL_SCOPES[scope]
    except KeyError as exc:
        raise ValueError(f"unknown benchmark protocol scope: {scope!r}") from exc
    result = deepcopy(value)
    result["scene_keys"] = list(result["scene_keys"])
    return result


def validate_protocol_scope(
    records: Iterable[Mapping[str, Any]],
    *,
    scope: str = "factory",
    pool_scene_key: str | None = None,
) -> None:
    """Reject records whose declared scene is outside the protocol scope.

    Legacy Factory manifests omitted ``scene_key`` on each record, so omitted
    values are tolerated.  A pool-level scene key, when present, is checked as
    well.  This keeps Warehouse records from silently entering Factory splits.
    """
    metadata = protocol_scope_metadata(scope)
    allowed = set(metadata["scene_keys"])
    observed: set[str] = set()
    if pool_scene_key is not None:
        observed.add(str(pool_scene_key))
    for record in records:
        scene_key = record.get("scene_key")
        if scene_key not in (None, ""):
            observed.add(str(scene_key))
    invalid = sorted(observed - allowed)
    if invalid:
        raise ValueError(
            f"benchmark protocol scope {scope!r} cannot include scene_key(s): "
            + ", ".join(invalid)
        )


def file_sha256(path: str | Path) -> str:
    """Hash the exact scenario bytes used by a manifest entry."""
    return __import__("hashlib").sha256(Path(path).read_bytes()).hexdigest()


def _certificate_prerequisites(data: Mapping[str, Any]) -> list[dict[str, Any]]:
    nodes = data.get("phase_graph", {}).get("nodes", [])
    return [
        {"id": node.get("id"), "prerequisites": list(node.get("prerequisites") or [])}
        for node in nodes
    ]


def _certificate_view(contract: Mapping[str, Any]) -> dict[str, Any]:
    minimum = contract.get("minimum_team") or {}
    counterfactual = contract.get("counterfactual") or {}
    return {
        "status": contract.get("status"),
        "eligible": contract.get("eligible"),
        "minimum_team_size": minimum.get("minimum_team_size"),
        "forced_heterogeneity": bool(minimum.get("forced_heterogeneity")),
        "single_agent_feasible": list(counterfactual.get("single_agent_feasible") or
                                        minimum.get("single_agent_feasible") or []),
    }












__all__ = [
    "FACTORY_SCENE_KEY",
    "WAREHOUSE_SCENE_KEY",
    "MODERN_HOUSE_SCENE_KEY",
    "WAREHOUSE_R1_ID",
    "WAREHOUSE_R1_METADATA",
    "MODERN_HOUSE_R1_METADATA",
    "WAREHOUSE_R1_INSTANCE_FILES",
    "WAREHOUSE_R1_QUALITY_VARIANT_FILES",
    "WAREHOUSE_R1_HETEROGENEOUS_CANDIDATE_FILES",
    "PROTOCOL_SCOPES",
    "protocol_scope_metadata",
    "validate_protocol_scope",
    "file_sha256",
]
