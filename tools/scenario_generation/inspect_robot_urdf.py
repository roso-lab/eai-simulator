"""Read declared URDF inertial/joint data without loading meshes or simulator code.

All aggregates cover authored inertial links at a specified joint configuration.
They are not runtime masses, rated payloads, or geometry clearance certificates.
The file SHA-256 and configuration are part of every report.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import xml.etree.ElementTree as ET

import numpy as np

from tools.scenario_generation.combination_profile import RigidComponent, combine_components


def _number(raw, name):
    if isinstance(raw, bool):
        raise ValueError(f"{name}: boolean is not a quantity")
    try:
        value = float(raw)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{name}: expected a number") from exc
    if not math.isfinite(value):
        raise ValueError(f"{name}: expected a finite number")
    return value


def _xyz(raw, default):
    values = default if raw is None else raw.split()
    if len(values) != 3:
        raise ValueError("xyz/rpy/axis requires three values")
    return np.array([_number(v, "xyz/rpy/axis") for v in values])


def _rotation(axis, angle):
    norm = np.linalg.norm(axis)
    if norm <= 0:
        raise ValueError("joint axis must be nonzero")
    x, y, z = axis / norm
    cross = np.array([[0, -z, y], [z, 0, -x], [-y, x, 0]])
    return np.eye(3) + math.sin(angle) * cross + (1 - math.cos(angle)) * (cross @ cross)


def _origin(element):
    transform = np.eye(4)
    if element is None:
        return transform
    r, p, y = _xyz(element.get("rpy"), (0, 0, 0))
    transform[:3, :3] = (_rotation(np.array([0, 0, 1]), y)
                          @ _rotation(np.array([0, 1, 0]), p)
                          @ _rotation(np.array([1, 0, 0]), r))
    transform[:3, 3] = _xyz(element.get("xyz"), (0, 0, 0))
    return transform


def inspect_urdf(path: str | Path, *, joint_positions: dict[str, float] | None = None) -> dict:
    """Extract a tree and its authored inertial-link aggregate at q (default zero).

    Meshes, transmissions and plugins are never executed or resolved. Nonfixed
    planar/floating/mimic joints are rejected because their FK isn't implemented.
    Missing inertial data remain listed; their mass is not silently taken as zero.
    """
    path = Path(path)
    content = path.read_bytes()
    if len(content) > 10_000_000:
        raise ValueError("URDF exceeds 10 MB")
    if b"<!DOCTYPE" in content.upper() or b"<!ENTITY" in content.upper():
        raise ValueError("DTD/entity URDFs are not supported")
    root = ET.fromstring(content)
    if root.tag != "robot":
        raise ValueError("expected flattened URDF <robot>")
    links = {}
    for link in root.findall("link"):
        name = link.get("name")
        if not name or name in links:
            raise ValueError("link names must be nonempty and unique")
        links[name] = link
    if not links:
        raise ValueError("URDF contains no links")
    incoming, joints = {}, {}
    q = dict(joint_positions or {})
    for element in root.findall("joint"):
        name, kind = element.get("name"), element.get("type")
        if not name or name in joints:
            raise ValueError("joint names must be nonempty and unique")
        if kind not in {"fixed", "revolute", "continuous", "prismatic"} or element.find("mimic") is not None:
            raise ValueError(f"{name}: unsupported joint type/mimic")
        parent_node, child_node = element.find("parent"), element.find("child")
        parent = None if parent_node is None else parent_node.get("link")
        child = None if child_node is None else child_node.get("link")
        if parent not in links or child not in links or parent == child or child in incoming:
            raise ValueError(f"{name}: invalid or multiply-parented link")
        position = _number(q.get(name, 0.0), name)
        if kind == "fixed" and position != 0:
            raise ValueError(f"{name}: fixed joint cannot move")
        limit_node = element.find("limit")
        limits = {} if limit_node is None else {
            key: _number(value, f"{name}.{key}") for key, value in limit_node.attrib.items()
        }
        if "lower" in limits and "upper" in limits and limits["lower"] > limits["upper"]:
            raise ValueError(f"{name}: reversed limits")
        if kind not in {"fixed", "continuous"} and (
            position < limits.get("lower", -math.inf) - 1e-9
            or position > limits.get("upper", math.inf) + 1e-9
        ):
            raise ValueError(f"{name}: configuration outside joint limits")
        for key in ("effort", "velocity"):
            if key in limits and limits[key] < 0:
                raise ValueError(f"{name}: negative {key} limit")
        axis_node = element.find("axis")
        axis = _xyz(None if axis_node is None else axis_node.get("xyz"), (1, 0, 0))
        motion = np.eye(4)
        if kind in {"revolute", "continuous"}:
            motion[:3, :3] = _rotation(axis, position)
        elif kind == "prismatic":
            if np.linalg.norm(axis) <= 0:
                raise ValueError(f"{name}: zero axis")
            motion[:3, 3] = axis / np.linalg.norm(axis) * position
        origin = _origin(element.find("origin"))
        joints[name] = {"name": name, "type": kind, "parent": parent, "child": child,
                        "position": position, "position_unit": "m" if kind == "prismatic" else "rad",
                        "axis": axis.tolist(), "limits": limits,
                        "origin_matrix": origin.tolist()}
        incoming[child] = (parent, origin @ motion)
    if set(q) - set(joints):
        raise ValueError(f"unknown joint positions: {sorted(set(q) - set(joints))}")
    roots = set(links) - set(incoming)
    if len(roots) != 1:
        raise ValueError("URDF must be one rooted tree")
    base = next(iter(roots))
    transforms = {base: np.eye(4)}
    def link_transform(name, visiting):
        if name in transforms:
            return transforms[name]
        if name in visiting:
            raise ValueError("URDF joint cycle")
        parent, local = incoming[name]
        transforms[name] = link_transform(parent, visiting | {name}) @ local
        return transforms[name]
    for name in links:
        link_transform(name, set())
    components, link_records, missing = [], [], []
    for name, link in links.items():
        inertial = link.find("inertial")
        record = {"name": name, "visual_elements": len(link.findall("visual")),
                  "collision_elements": len(link.findall("collision")),
                  "mass_kg": None, "com_in_link_m": None, "inertia_in_link_kg_m2": None}
        if inertial is None:
            missing.append(name)
        else:
            mass_node, inertia_node = inertial.find("mass"), inertial.find("inertia")
            mass = None if mass_node is None else _number(mass_node.get("value"), f"{name}.mass")
            if mass is not None and mass <= 0:
                raise ValueError(f"{name}: positive authored mass required")
            local = _origin(inertial.find("origin"))
            inertia = None
            if inertia_node is not None:
                a = {key: _number(inertia_node.get(key), f"{name}.{key}")
                     for key in ("ixx", "ixy", "ixz", "iyy", "iyz", "izz")}
                matrix = np.array([[a["ixx"], a["ixy"], a["ixz"]],
                                   [a["ixy"], a["iyy"], a["iyz"]],
                                   [a["ixz"], a["iyz"], a["izz"]]])
                inertia = local[:3, :3] @ matrix @ local[:3, :3].T
            transform = transforms[name]
            components.append(RigidComponent(
                name=name, mass_kg=mass, com_m=tuple(local[:3, 3]),
                inertia_kg_m2=None if inertia is None else tuple(map(tuple, inertia)),
                translation_m=tuple(transform[:3, 3]),
                rotation_matrix=tuple(map(tuple, transform[:3, :3])), evidence_status="urdf_declared",
            ))
            record.update(mass_kg=mass, com_in_link_m=local[:3, 3].tolist(),
                          inertia_in_link_kg_m2=None if inertia is None else inertia.tolist())
        link_records.append(record)
    aggregate = combine_components(components) if components else None
    frame_only = [item["name"] for item in link_records
                  if item["mass_kg"] is None and item["visual_elements"] == 0
                  and item["collision_elements"] == 0]
    incomplete = [item["name"] for item in link_records
                  if item["mass_kg"] is None and item["name"] not in frame_only]
    subtotal = sum(c.mass_kg for c in components if c.mass_kg is not None)
    report = {
        "schema_id": "eai.urdf_inspection", "schema_version": "1.0",
        "source": {"locator": str(path.resolve()), "sha256": hashlib.sha256(content).hexdigest(),
                   "size_bytes": len(content), "kind": "URDF"},
        "robot_name": root.get("name"), "root_frame": base,
        "method": "URDF FK and inertial origins; SI units; authored inertial links only",
        "joint_positions": {name: item["position"] for name, item in joints.items()},
        "joints": list(joints.values()), "links": link_records,
        "authored_mass_sum_kg": subtotal, "missing_inertial_links": incomplete,
        "frame_only_links": frame_only,
        "total_model_mass_kg": None if incomplete else subtotal,
        "aggregate_of_authored_inertial_links": aggregate,
        "limitations": ["No USD/runtime equivalence established", "No mesh dimensions or collision clearance computed",
                        "Unspecified link masses are omitted from subtotal, never certified massless",
                        "URDF effort limits are distinct from EAI actuator overrides; no payload rating inferred"],
        "runtime_validated": False,
    }
    # Normalize tuples/numpy scalar containers into JSON, rejecting nonfinite math.
    return json.loads(json.dumps(report, allow_nan=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("urdf", type=Path)
    parser.add_argument("--joint-positions", default="{}", help="JSON name -> radians/meters")
    args = parser.parse_args()
    print(json.dumps(inspect_urdf(args.urdf, joint_positions=json.loads(args.joint_positions)),
                     indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
