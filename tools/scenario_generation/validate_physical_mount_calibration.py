"""Validate a physical mount calibration record without simulator imports."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any


REQUIRED_COMPONENT_FIELDS = ("mass_kg", "center_of_mass_m", "inertia_kg_m2_about_com")
REQUIRED_IDENTITY_FIELDS = ("host_robot_model", "manipulator_model")


def validate(record: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not isinstance(record, dict):
        return ["record must be an object"]
    if record.get("status") not in {"incomplete", "complete"}:
        errors.append("status must be incomplete or complete")
    if record.get("schema") != "eai.physical_mount_calibration":
        errors.append("invalid schema")
    identity = record.get("identity", {})
    for field in REQUIRED_IDENTITY_FIELDS:
        if not identity.get(field):
            errors.append(f"identity.{field} is required")
    components = record.get("components", [])
    if not isinstance(components, list) or not components:
        errors.append("components must be non-empty")
    else:
        for index, component in enumerate(components):
            if not isinstance(component, dict):
                errors.append(f"components[{index}] must be an object")
                continue
            for field in REQUIRED_COMPONENT_FIELDS:
                if component.get(field) is None:
                    errors.append(f"components[{index}].{field} is unknown")
            if not component.get("measurement_method"):
                errors.append(f"components[{index}].measurement_method is required")
            if not isinstance(component.get("source_refs"), list) or not component.get("source_refs"):
                errors.append(f"components[{index}].source_refs must be non-empty")
            mass = component.get("mass_kg")
            if mass is not None and (isinstance(mass, bool) or not isinstance(mass, (int, float))
                                     or not math.isfinite(mass) or mass <= 0):
                errors.append(f"components[{index}].mass_kg must be positive and finite")
            inertia = component.get("inertia_kg_m2_about_com")
            if inertia is not None and (not isinstance(inertia, list) or len(inertia) != 3
                                         or any(not isinstance(row, list) or len(row) != 3
                                                or any(not isinstance(value, (int, float)) or isinstance(value, bool)
                                                       or not math.isfinite(value) for value in row)
                                                for row in inertia)):
                errors.append(f"components[{index}].inertia_kg_m2_about_com must be a finite 3x3 matrix")
            if isinstance(inertia, list) and len(inertia) == 3 and all(
                isinstance(row, list) and len(row) == 3 for row in inertia
            ):
                if any(inertia[row][column] != inertia[column][row]
                       for row in range(3) for column in range(row)):
                    errors.append(f"components[{index}].inertia_kg_m2_about_com must be symmetric")
                diagonals = [inertia[axis][axis] for axis in range(3)]
                if any(value <= 0 for value in diagonals):
                    errors.append(f"components[{index}].inertia_kg_m2_about_com must be positive definite")
                if all(isinstance(value, (int, float)) and not isinstance(value, bool)
                       and math.isfinite(value) for row in inertia for value in row):
                    moments = sorted(diagonals)
                    if moments[2] > moments[0] + moments[1]:
                        errors.append(f"components[{index}].inertia_kg_m2_about_com violates triangle inequality")
    installation = record.get("installation", {})
    for field in ("parent_link", "mount_origin_m_in_host_frame", "mount_rotation_matrix"):
        if installation.get(field) is None:
            errors.append(f"installation.{field} is unknown")
    if record.get("status") == "complete" and errors:
        errors.append("complete status requires all required fields")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("record", type=Path)
    args = parser.parse_args()
    errors = validate(json.loads(args.record.read_text(encoding="utf-8")))
    if errors:
        print(json.dumps({"status": "invalid", "errors": errors}, ensure_ascii=False))
        return 1
    print(json.dumps({"status": "valid"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
