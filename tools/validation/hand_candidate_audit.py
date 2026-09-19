"""Audit a hand URDF without treating it as an Isaac-ready asset."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import numpy as np


def audit(path: Path) -> dict:
    path = path.expanduser().resolve()
    data = path.read_bytes()
    root = ET.fromstring(data)
    links = []
    missing = []
    invalid_inertia = []
    for link in root.findall("link"):
        name = link.attrib["name"]
        inertial = link.find("inertial")
        mass = None if inertial is None or inertial.find("mass") is None else float(inertial.find("mass").attrib["value"])
        inertia = None if inertial is None or inertial.find("inertia") is None else inertial.find("inertia").attrib
        required = ("ixx", "ixy", "ixz", "iyy", "iyz", "izz")
        complete = mass is not None and inertia is not None and all(key in inertia for key in required)
        if not complete:
            missing.append(name)
        else:
            tensor = np.array([[float(inertia["ixx"]), float(inertia["ixy"]), float(inertia["ixz"])],
                               [float(inertia["ixy"]), float(inertia["iyy"]), float(inertia["iyz"])],
                               [float(inertia["ixz"]), float(inertia["iyz"]), float(inertia["izz"])]]); values = np.linalg.eigvalsh(tensor)
            if mass <= 0 or values[0] <= 0 or values[-1] > values[0] + values[1] + 1e-12:
                invalid_inertia.append(name)
        links.append({"name": name, "mass_kg": mass, "complete_inertial": complete})
    joints = root.findall("joint")
    movable = [joint for joint in joints if joint.attrib.get("type") != "fixed"]
    invalid_joints = [joint.attrib.get("name") for joint in movable
                      if joint.find("parent") is None or joint.find("child") is None]
    if invalid_joints:
        raise ValueError(f"movable joints missing parent or child: {invalid_joints}")
    return {
        "schema": "eai.hand_candidate_urdf_audit.v1", "asset": str(path),
        "source_sha256": hashlib.sha256(data).hexdigest(), "robot_name": root.attrib.get("name"),
        "link_count": len(links), "joint_count": len(joints), "movable_joint_count": len(movable),
        "mass_total_kg": sum(item["mass_kg"] for item in links if item["mass_kg"] is not None),
        "missing_or_incomplete_inertial_links": missing, "invalid_inertia_links": invalid_inertia, "invalid_movable_joints": invalid_joints,
        "links": links,
        "status": "source_complete_usd_missing" if not missing and not invalid_inertia else "invalid_source_physics" if invalid_inertia else "unknown",
        "usd_asset": None,
        "limitations": ["URDF source has no immutable upstream commit recorded", "No Isaac USD, mount geometry, controller, contact or hardware calibration", "URDF declarations are not measured hardware values"],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--urdf", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.report.exists():
        parser.error("Choose a new report path")
    result = audit(args.urdf)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("status", "link_count", "movable_joint_count", "mass_total_kg", "missing_or_incomplete_inertial_links", "invalid_inertia_links")}))


if __name__ == "__main__":
    main()
