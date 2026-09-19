"""Compose v2 USD audits with an explicit payload-stage to base-stage transform."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.scenario_generation.combination_profile import RigidComponent, combine_components


def compose(base: dict, payload: dict, offset: tuple[float, float, float], *, rotation=None) -> dict:
    if rotation is None:
        return {"status": "unknown", "reason": "Explicit payload-stage to base-stage rotation required"}
    for report in (base, payload):
        if (report.get("schema") != "eai.usd_physics_audit" or report.get("version") != 2
                or report.get("reference_frame") != "stage_world" or not report.get("frame_id")):
            return {"status": "unknown", "reason": "Version 2 audits with explicit stage frame identities required"}
        if report.get("aggregate", {}).get("inertia_reference_point") != "center_of_mass":
            return {"status": "unknown", "reason": "Inertia must be expressed about component COM"}
    base_data, payload_data = base["aggregate"], payload["aggregate"]
    quantities = combine_components([
        RigidComponent("base", base_data.get("mass_kg"), base_data.get("center_of_mass_world_m"),
                       base_data.get("inertia_kg_m2"), evidence_status="derived"),
        RigidComponent("payload", payload_data.get("mass_kg"), payload_data.get("center_of_mass_world_m"),
                       payload_data.get("inertia_kg_m2"), translation_m=offset,
                       rotation_matrix=rotation, evidence_status="derived"),
    ])
    return {
        "schema": "eai.static_mount_composition", "version": 2,
        "status": quantities["status"], "reference_frame": base["frame_id"],
        "payload_reference_frame": payload["frame_id"],
        "total_mass_kg": quantities["total_mass_kg"], "total_center_of_mass_m": quantities["com_m"],
        "total_inertia_kg_m2": quantities["inertia_kg_m2"], "inertia_reference_point": "center_of_mass",
        "quantity_status": quantities["quantity_status"],
        "payload_stage_to_base_stage": {"translation_m": offset, "rotation_matrix": rotation},
        "limitations": ["Input joint poses and transform are caller-supplied; no mount compatibility claim",
                        "No support, contact, actuator, dynamic stability or hardware capacity validation"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base", type=Path)
    parser.add_argument("payload", type=Path)
    parser.add_argument("--offset", nargs=3, type=float, required=True, metavar=("X", "Y", "Z"))
    parser.add_argument("--rotation", nargs=9, type=float, required=True,
                        help="Row-major SO(3) matrix mapping payload stage vectors into the base stage")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rotation = tuple(tuple(args.rotation[start:start + 3]) for start in (0, 3, 6))
    result = compose(json.loads(args.base.read_text()), json.loads(args.payload.read_text()),
                     tuple(args.offset), rotation=rotation)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
