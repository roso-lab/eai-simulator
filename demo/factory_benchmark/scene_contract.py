"""Factory semantic contract for task generation and admission.

The contract deliberately separates the migrated base USD from scenario-owned
task props.  Zone geometry is usable for navigation only; an observation site
does not claim that a camera, detector, or inventory recognizer is active.
"""

from __future__ import annotations

from typing import Any

from EAI_assets.scenario_assets import ASSETS
from EAI.scenario_spec.templates import SceneTemplate, zone_anchors


FACTORY_ZONE_ROLES = {
    "storage_west": "storage_approach",
    "yard_south": "staging_and_waiting",
    "midband": "shared_cross_aisle",
    "assembly_east": "assembly_approach",
    "dock_north": "delivery_and_dock",
}

FACTORY_TASK_CONTRACTS = {
    "F-INS-01": {
        "family": "inspection",
        "required_zones": ("storage_west", "midband", "assembly_east"),
        "required_assets": ("primitive_box", "safety_beam"),
        "business_result": "a marked object is observed from two allowed viewpoints and the second result is consumed",
        "observation_claim": "geometric_visibility_only_until_sensor_pipeline is connected",
    },
    "F-TRAFFIC-01": {
        "family": "traffic",
        "required_zones": ("midband", "yard_south", "dock_north"),
        "required_assets": (),
        "business_result": "robots reserve a shared aisle and dock without conflicting occupancy",
        "observation_claim": "live robot pose and reservation state",
    },
    "F-COV-01": {
        "family": "coverage",
        "required_zones": ("storage_west", "midband", "assembly_east", "dock_north"),
        "required_assets": (),
        "business_result": "inspection sites meet an observation freshness bound and recover after member loss",
        "observation_claim": "site visitation and configured sensor stream status",
    },
    "F-VERT-01": {
        "family": "vertical_access",
        "required_zones": ("yard_south", "mezzanine_south"),
        "required_assets": ("safety_beam",),
        "business_result": "an explicit legged probe assesses access to an elevated production inspection area",
        "observation_claim": "support-relative pose and contact evidence; geometry and physics remain pending until calibrated",
    },
}


def build_factory_scene_contract(template: SceneTemplate, prim_audit: dict[str, Any] | None = None) -> dict[str, Any]:
    if template.scene_key != "factory":
        raise ValueError("Factory scene contract requires the factory template")
    zones = []
    for zone in template.zones:
        anchors = zone_anchors(template, zone.id)
        zones.append({
            "id": zone.id,
            "role": FACTORY_ZONE_ROLES.get(zone.id, "unassigned_scene_zone"),
            "kind": zone.kind,
            "aabb_m": list(zone.aabb),
            "level": zone.level,
            "anchor_count": len(anchors),
            "navigation_status": "map_anchored" if anchors else "unavailable",
        })
    if prim_audit is not None and not prim_audit.get("validated"):
        raise ValueError("Factory prim binding audit is not valid")
    inspection_targets = [
        {
            "id": item["id"],
            "prim_path": item["prim_path"],
            "world_bounds_m": item.get("world_bounds_m"),
            "semantic_status": "visual_reference_only",
            "ground_truth_status": "evaluator_only_pending",
        }
        for item in (prim_audit.get("bindings", []) if prim_audit else [])
        if item.get("role") == "inspection_target_visual_reference"
    ]
    return {
        "schema": "eai.factory_scene_contract",
        "version": 1,
        "scene_key": "factory",
        "units": "meters",
        "template_key": template.key,
        "map_sha256": template.map_sha256,
        "base_world": {
            "source": "usd/scene/factory/factory.usd",
            "usd_sha256": prim_audit.get("usd_sha256") if prim_audit else None,
            "semantic_binding_status": (
                "geometry_prims_verified;_business_semantics_pending"
                if prim_audit else
                "zone_and_occupancy_verified;_prim_business_semantics_pending"
            ),
        },
        "prim_bindings": (prim_audit.get("bindings", []) if prim_audit else []),
        "inspection_targets": inspection_targets,
        "zones": zones,
        "task_assets": {
            key: {
                "available": key in ASSETS,
                "modes": list(asset.modes),
                "size_m": list(asset.size_m) if asset.size_m is not None else None,
                "semantic_status": "scenario_prop_registry_only",
            }
            for key, asset in ASSETS.items()
        },
        "observation_sites": [
            {"id": f"obs_{zone.id}_{index + 1}", "zone_id": zone.id,
             "position_m": [anchor.x, anchor.y, zone.level],
             "quality_status": "geometric_site_only"}
            for zone in template.zones
            for index, anchor in enumerate(zone_anchors(template, zone.id)[:2])
        ],
        "task_contracts": FACTORY_TASK_CONTRACTS,
        "limitations": [
            "base USD geometry is not an inventory or equipment API",
            "task props require independent asset closure and interaction validation",
            "observation sites do not produce sensor observations by themselves",
            "unverified elevated surfaces are not admitted as executable routes",
        ],
    }


def validate_factory_task_contract(contract: dict[str, Any], task_id: str) -> dict[str, Any]:
    if contract.get("schema") != "eai.factory_scene_contract":
        raise ValueError("unsupported Factory scene contract")
    task = contract.get("task_contracts", {}).get(task_id)
    if not isinstance(task, dict):
        raise ValueError(f"unknown Factory task contract {task_id!r}")
    known_zones = {zone["id"] for zone in contract.get("zones", [])
                   if zone.get("navigation_status") == "map_anchored"}
    missing_zones = sorted(set(task["required_zones"]) - known_zones)
    missing_assets = sorted(key for key in task["required_assets"]
                            if not contract.get("task_assets", {}).get(key, {}).get("available"))
    missing_target_bindings = []
    if task_id == "F-INS-01" and not contract.get("inspection_targets"):
        missing_target_bindings.append("inspection_target_visual_reference")
    return {
        "task_id": task_id,
        "eligible": not missing_zones and not missing_assets and not missing_target_bindings,
        "missing_zones": missing_zones,
        "missing_assets": missing_assets,
        "missing_target_bindings": missing_target_bindings,
        "semantic_binding_status": contract["base_world"]["semantic_binding_status"],
    }


__all__ = ["FACTORY_TASK_CONTRACTS", "FACTORY_ZONE_ROLES",
           "build_factory_scene_contract", "validate_factory_task_contract"]
