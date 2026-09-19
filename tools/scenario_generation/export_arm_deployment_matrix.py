"""Export the Env DIY manipulator deployment evidence matrix.

The matrix is an evidence view of the shared catalog gate. It does not infer
payload capacity from mass or turn a conditional stationary result into a
walking or hardware claim.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = ROOT / "source" / "EAI"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from EAI.hmrs_env.env_diy.catalog import ROBOT_KEYS, ROBOT_LABELS  # noqa: E402


def build_report() -> dict:
    from tools.scenario_generation.arm_deployment_evidence import (
        CANDIDATE_ONLY_MANIPULATORS,
        ENV_DIY_MANIPULATOR_VARIANTS,
        deployment_evidence_for_configuration,
    )
    from tools.scenario_generation.robot_dossier import all_dossiers_snapshot_hash, robot_dossier

    rows = []
    dossier_snapshot_sha256 = all_dossiers_snapshot_hash()
    for robot_key in ROBOT_KEYS:
        for manipulator in ENV_DIY_MANIPULATOR_VARIANTS:
            evidence = deployment_evidence_for_configuration(robot_key, manipulator)
            try:
                dossier = robot_dossier(robot_key, attachments=[manipulator], physics_context="benchmark")
                physics = dossier["physics"]
                field_statuses = [
                    physics["mass_kg"]["status"],
                    physics["center_of_mass_m"]["status"],
                    physics["inertia_kg_m2"]["status"],
                    physics["support_geometry_status"],
                    physics["aggregate_com_inertia_status"],
                    physics["installation_geometry_status"],
                ]
                physics_status = {
                    "overall": "validated" if all(status in {"measured", "derived", "validated"}
                                                   for status in field_statuses) else "unknown",
                    "mass": physics["mass_kg"]["status"],
                    "center_of_mass": physics["center_of_mass_m"]["status"],
                    "inertia": physics["inertia_kg_m2"]["status"],
                    "support_geometry": physics["support_geometry_status"],
                    "aggregate_com_inertia": physics["aggregate_com_inertia_status"],
                    "installation_geometry": physics["installation_geometry_status"],
                }
            except ValueError as exc:
                physics_status = {"overall": "unsupported", "status": "unsupported", "reason": str(exc)}
            rows.append({
                "robot_key": robot_key,
                "robot_dossier_snapshot_sha256": dossier_snapshot_sha256,
                "robot_label": ROBOT_LABELS.get(robot_key, robot_key),
                "manipulator": manipulator,
                "tool": "Robotiq 2F-85" if manipulator == "ur5" else None,
                "status": evidence["mount_evidence"]["status"],
                "reason": evidence["mount_evidence"]["reason"],
                "physics_status": physics_status,
                "mass_policy": "unscaled_asset_mass",
                "evidence_scope": "stationary_simulation_only",
                "deployment_evidence": evidence,
            })
    candidate_only_rows = []
    for robot_key in ROBOT_KEYS:
        for manipulator in CANDIDATE_ONLY_MANIPULATORS:
            evidence = deployment_evidence_for_configuration(robot_key, manipulator)
            candidate_only_rows.append({
                "robot_key": robot_key,
                "robot_label": ROBOT_LABELS.get(robot_key, robot_key),
                "manipulator": manipulator,
                "status": evidence["mount_evidence"]["status"],
                "catalog_state": evidence["catalog_state"],
                "selectable_in_env_diy": evidence["selectable_in_env_diy"],
                "deployment_evidence": evidence,
            })
    status_counts = {
        status: sum(row["status"] == status for row in rows)
        for status in ("allowed", "conditional", "rejected", "unknown")
    }
    return {
        "schema": "eai.arm_deployment_matrix",
        "version": 2,
        "robot_dossier_snapshot_sha256": dossier_snapshot_sha256,
        "rows": rows,
        "status_counts": status_counts,
        "candidate_only_rows": candidate_only_rows,
        "policy": {
            "unknown_or_rejected": "blocked by Env DIY validation",
            "conditional": "visible only with its evidence limitation",
            "mass": "never scale benchmark manipulator mass or inertia",
            "hardware_claims": "not established by this matrix",
            "candidate_only": "retained for audit; never exposed as an Env DIY selection",
        },
        "limitations": [
            "No walking manipulation or hardware validation",
            "No measured payload, grasp force, wind resistance, collision margin, or load dynamics",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(build_report(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
