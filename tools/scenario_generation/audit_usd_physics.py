"""Audit USD declarations in SI units at time zero, with inertia about COM."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.scenario_generation.combination_profile import RigidComponent, combine_components


def _finite_values(value):
    if value is None:
        return None
    values = [float(item) for item in value]
    return values if all(math.isfinite(item) for item in values) else None


def audit(path: Path) -> dict:
    import numpy as np
    from pxr import Gf, Usd, UsdGeom, UsdPhysics

    stage = Usd.Stage.Open(str(path))
    if stage is None:
        raise ValueError(f"cannot open USD: {path}")
    meters = UsdGeom.GetStageMetersPerUnit(stage)
    kilograms = UsdPhysics.GetStageKilogramsPerUnit(stage)
    if not all(math.isfinite(value) and value > 0 for value in (meters, kilograms)):
        raise ValueError("Stage units must be positive and finite")
    bodies, components = [], []
    for prim in Usd.PrimRange.Stage(stage, Usd.TraverseInstanceProxies()):
        if not prim.HasAPI(UsdPhysics.RigidBodyAPI):
            continue
        mass_api = UsdPhysics.MassAPI(prim)
        attributes = {
            "mass": mass_api.GetMassAttr(), "com": mass_api.GetCenterOfMassAttr(),
            "inertia": mass_api.GetDiagonalInertiaAttr(), "axes": mass_api.GetPrincipalAxesAttr(),
        }
        authored = {key: attr.HasAuthoredValueOpinion() for key, attr in attributes.items()}
        reasons = []
        mass = attributes["mass"].Get() if authored["mass"] else None
        if mass is None or not math.isfinite(mass) or mass <= 0:
            mass = None
            reasons.append("Mass absent, nonpositive or nonfinite; no density inference")
        else:
            mass = float(mass) * kilograms
        com = _finite_values(attributes["com"].Get()) if authored["com"] else None
        diagonal = _finite_values(attributes["inertia"].Get()) if authored["inertia"] else None
        axes = attributes["axes"].Get()
        axes_values = None if axes is None else _finite_values((axes.GetReal(), *axes.GetImaginary()))
        matrix = np.asarray(UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(0))
        rotation = matrix[:3, :3].T
        rigid_transform = (np.isfinite(matrix).all()
                           and np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-6, rtol=0)
                           and math.isclose(float(np.linalg.det(rotation)), 1.0, abs_tol=1e-6))
        world_com = None
        if com is not None and rigid_transform:
            world_com = (rotation @ np.asarray(com) + matrix[3, :3]) * meters
        else:
            reasons.append("COM absent/nonfinite or transform contains scale, shear or reflection")
        tensor = None
        if diagonal is not None and all(value > 0 for value in diagonal) and axes_values and rigid_transform:
            if math.isclose(sum(value * value for value in axes_values), 1.0, abs_tol=1e-5):
                local_rotation = np.asarray(Gf.Matrix3d(Gf.Quatd(axes))).T
                principal_rotation = rotation @ local_rotation
                tensor = principal_rotation @ np.diag(diagonal) @ principal_rotation.T * kilograms * meters ** 2
        if tensor is None:
            reasons.append("Inertia/axes absent or invalid; no shape inference")
        component = RigidComponent(str(prim.GetPath()), mass,
                                   None if world_com is None else tuple(world_com),
                                   None if tensor is None else tuple(map(tuple, tensor)),
                                   evidence_status="declared")
        try:
            combine_components([component])
        except ValueError as error:
            reasons.append(str(error))
            tensor = None
            component = RigidComponent(component.name, mass, component.com_m, None, evidence_status="declared")
        components.append(component)
        bodies.append({
            "path": str(prim.GetPath()), "mass_kg": mass,
            "center_of_mass_local_m": None if com is None else [value * meters for value in com],
            "center_of_mass_world_m": None if world_com is None else world_com.tolist(),
            "inertia_about_com_world_kg_m2": None if tensor is None else tensor.tolist(),
            "principal_axes_wxyz": axes_values, "authored_fields": authored,
            "principal_axes_provenance": "authored" if authored["axes"] else "usd_schema_default",
            "authored_complete": not reasons, "issues": reasons,
        })
    quantities = combine_components(components) if components else {
        "total_mass_kg": None, "com_m": None, "inertia_kg_m2": None,
    }
    layers = sorted([
        {"path": layer.realPath, "sha256": hashlib.sha256(Path(layer.realPath).read_bytes()).hexdigest()}
        for layer in stage.GetUsedLayers() if layer.realPath and Path(layer.realPath).is_file()
    ], key=lambda item: item["path"])
    return {
        "schema": "eai.usd_physics_audit", "version": 2, "asset": str(path.resolve()),
        "reference_frame": "stage_world", "time_code": 0,
        "stage_units": {"meters_per_unit": meters, "kilograms_per_unit": kilograms},
        "source_layers": layers,
        "frame_id": hashlib.sha256(json.dumps(layers, sort_keys=True).encode()).hexdigest(),
        "rigid_bodies": bodies,
        "aggregate": {
            "mass_kg": quantities["total_mass_kg"],
            "center_of_mass_world_m": quantities["com_m"],
            "inertia_kg_m2": quantities["inertia_kg_m2"], "inertia_reference_point": "center_of_mass",
            "mass_status": "derived" if quantities["total_mass_kg"] is not None else "unknown",
            "com_status": "derived" if quantities["com_m"] is not None else "unknown",
            "inertia_status": "derived" if quantities["inertia_kg_m2"] is not None else "unknown",
        },
        "status": "declared_complete" if bodies and all(body["authored_complete"] for body in bodies) else "unknown",
        "limitations": ["USD time-zero pose, not the mounted runtime joint pose",
                        "Authored values and derived aggregates are not measured hardware calibration or payload certification",
                        "Scaled rigid-body transforms are rejected; USD unit conversion is recorded explicitly"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("asset", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = audit(args.asset)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
