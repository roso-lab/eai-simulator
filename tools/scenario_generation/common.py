from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
for package in ("EAI", "EAI_assets", "EAI_hmrs"):
    path = str(REPO_ROOT / "source" / package)
    if path not in sys.path:
        sys.path.insert(0, path)


def write_json(path, payload):
    """Publish without replacing an existing scenario, including concurrent writes.

    Writes to a sibling temp file and renames, so a crash mid-write never
    leaves a truncated scenario behind.
    """
    import json
    import os
    import uuid

    text = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp = destination.with_name(f".{destination.name}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        with tmp.open("x", encoding="utf-8") as stream:
            stream.write(text)
        os.replace(tmp, destination)
    finally:
        tmp.unlink(missing_ok=True)


def _arm_host_summary():
    from EAI.hmrs_env.env_diy import catalog

    hosts = {}
    for arm in ("ur5", "z1", "d1"):
        entry = catalog.attachment_entry(arm)
        hosts[arm] = sorted(entry.supported_robots)
    return hosts


def manifest(occupancy=None, *, scene_key="factory"):
    from demo.benchmark.authoring import build_authoring_contract
    from demo.benchmark.runtime import executor_capabilities
    from EAI.hmrs_env.env_diy import catalog
    from EAI.scenario_spec.templates import template_for
    from EAI.scenario_spec.schema import ROBOT_PROFILES, SKILLS
    from EAI_assets.scenario_assets import ASSETS, asset_manifest
    from tools.scenario_generation.benchmark_experiment import (
        ROBOT_COMBINATION_CELLS, SCENE_ASSET_KEYS, SCENE_EXPERIMENT_NOTES,
        available_scene_assets,
    )
    from tools.scenario_generation.robot_dossier import (
        all_dossiers_snapshot_hash, sanitized_robot_dossiers,
    )

    arm_hosts = _arm_host_summary()
    template = template_for(scene_key, occupancy)
    return {
        "scene_key": scene_key, "units": "meters, kilograms, seconds; quaternion wxyz; Z up",
        "template": {"key": template.key, "map_sha256": template.map_sha256,
                     "zones": [zone.id for zone in template.zones]},
        "layout_mode": ("existing_factory_ground_floor_overlay" if scene_key == "factory"
                        else f"{scene_key}_overlay"),
        "robots": [{"type": key, **profile, "controller": catalog.default_controller_cfg(key),
                    "attachments": [{"type": a.name, "controller": a.controller_cfg}
                                    for a in (catalog.attachment_catalog() | catalog.tool_catalog()).values()
                                    if a.supports(key) and not a.visual_only]}
                   for key, profile in ROBOT_PROFILES.items()],
        "robot_dossiers": sanitized_robot_dossiers(),
        "robot_dossier_snapshot_sha256": all_dossiers_snapshot_hash(),
        "attachment_mount_conflicts": catalog.PAYLOAD_MOUNT_CONFLICTS,
        "manipulator_hosts": arm_hosts,
        "assets": [item for item in asset_manifest()
                   if item["key"] in available_scene_assets(scene_key, ASSETS)],
        "asset_allowlist": list(SCENE_ASSET_KEYS[scene_key]),
        "asset_availability": {key: key in ASSETS for key in SCENE_ASSET_KEYS[scene_key]},
        "experiment_design": {
            "robot_combinations": ROBOT_COMBINATION_CELLS,
            "scene_notes": SCENE_EXPERIMENT_NOTES[scene_key],
        },
        "business_task_authoring": (build_authoring_contract(scene_key)
                                    if scene_key in {"factory", "warehouse_nvd", "modern_house_nvd"} else None),
        "skills": {key: {**value, "execution_status": executor_capabilities().get(key, "unsupported")}
                   for key, value in SKILLS.items()},
        "placement_hints_xy": [] if occupancy is None else occupancy.placement_hints(),
        "map_sha256": None if occupancy is None else occupancy.sha256,
        "constraints": [
            "IDs of robots must equal type_occurrence, e.g. m20_1, m20_2",
            "Keep all physical entities on the existing ground floor; yaw rotations only",
            "A primitive_box is dynamic; safety_beam static, kinematic or dynamic; goal_marker visual only",
            "mass_kg must be null for static/visual, positive for dynamic/kinematic",
            "Object pose denotes its center: z = size_m[2]/2",
            "Targets are free ground positions with z=0; keep them clear of objects",
            "Placement hints do not certify an arbitrary footprint; avoid initial overlaps",
            "Payload capacities are unknown, not inferred from robot family or capability scores",
            "A mounted manipulator is necessary but does not prove a working grasp or transport skill",
            "Use prerequisite IDs to describe a DAG; do not assert task or physical success",
            f"Manipulator hosts are strict: ur5 only on {arm_hosts['ur5']}; z1 only on {arm_hosts['z1']}; "
            f"d1 only on {arm_hosts['d1']}. "
            "Check each robot's own attachments list; carter for example takes z1 or d1, never ur5",
        ],
    }
