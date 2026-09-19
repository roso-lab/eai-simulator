"""Compare Gen3 URDF tensors, USD declarations and optional PhysX observations."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "source" / "EAI_assets"))

from EAI_assets.robots.kinova_inertials import check_link_frames, read_source


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def runtime_tensors(report):
    if report.get("schema") == "eai.mobile_arm_mount_trial.v2":
        return {name: np.asarray(tensor).reshape(3, 3).T
                for name, tensor in zip(report["arm_body_names"], report["arm_body_inertias_kg_m2"], strict=True)}
    if report.get("schema") == "eai.kinova_gen3_mount_smoke.v2":
        return {name: np.asarray(tensor).reshape(3, 3).T
                for name, tensor in zip(report["modes"][-1]["runtime_body_names"],
                                       report["modes"][-1]["runtime_inertias_kg_m2"], strict=True)}
    raise ValueError("Unsupported runtime report schema")


def tensor_difference(observed, expected):
    observed, expected = np.asarray(observed), np.asarray(expected)
    if observed.shape != (3, 3) or not np.isfinite(observed).all():
        raise ValueError("Runtime tensor must be a finite 3x3 matrix")
    maximum_error = float(np.abs(observed - expected).max())
    tolerance = 1e-7 + 1e-4 * float(np.abs(expected).max())
    return {"max_abs_error_kg_m2": maximum_error,
            "frobenius_relative_error": float(np.linalg.norm(observed - expected) / np.linalg.norm(expected)),
            "max_abs_tolerance_kg_m2": tolerance, "match": maximum_error <= tolerance}


def audit(asset, urdf, runtime_path=None):
    from pxr import Gf, Usd, UsdGeom, UsdPhysics

    source = read_source(urdf)
    stage = Usd.Stage.Open(str(asset))
    arm_path = str(stage.GetDefaultPrim().GetPath())
    frame_errors = check_link_frames(stage, arm_path, source)
    factor = UsdPhysics.GetStageKilogramsPerUnit(stage) * UsdGeom.GetStageMetersPerUnit(stage) ** 2
    runtime = None
    if runtime_path is not None:
        runtime_report = json.loads(Path(runtime_path).read_text())
        layers = {item["path"]: item["sha256"] for item in runtime_report["source_layers"]}
        for layer in stage.GetUsedLayers():
            if layer.realPath and layers.get(layer.realPath) != digest(layer.realPath):
                raise ValueError(f"Runtime/source asset layer mismatch: {layer.realPath}")
        runtime = runtime_tensors(runtime_report)
        if set(runtime) - set(source["links"]):
            raise ValueError("Unexpected runtime arm bodies")
    rows = []
    for name, frame in frame_errors.items():
        expected = source["links"][name]["inertia_at_com_link_kg_m2"]
        properties = UsdPhysics.MassAPI(stage.GetPrimAtPath(f"{arm_path}/{name}"))
        axes = properties.GetPrincipalAxesAttr().Get()
        axes_values = np.array([axes.GetReal(), *axes.GetImaginary()])
        diagonal = np.array(properties.GetDiagonalInertiaAttr().Get())
        valid_axes = np.isfinite(axes_values).all() and abs(float(axes_values @ axes_values) - 1) <= 1e-5
        valid_diagonal = (properties.GetDiagonalInertiaAttr().HasAuthoredValueOpinion()
                          and np.isfinite(diagonal).all() and np.all(diagonal > 0))
        declared_tensor = None
        if valid_axes and valid_diagonal:
            rotation = np.asarray(Gf.Matrix3d(Gf.Quatd(axes))).T
            declared_tensor = rotation @ np.diag(diagonal) @ rotation.T * factor
        observed = None if runtime is None else runtime.get(name)
        rows.append({
            "link": name, "frame_check": frame, "urdf_inertia_at_com_link_kg_m2": expected.tolist(),
            "usd_principal_axes_wxyz": axes_values.tolist(), "usd_axes_valid": bool(valid_axes),
            "usd_principal_axes_authored": properties.GetPrincipalAxesAttr().HasAuthoredValueOpinion(),
            "usd_tensor_status": "declared" if declared_tensor is not None else "unknown",
            "usd_vs_urdf": None if declared_tensor is None else tensor_difference(declared_tensor, expected),
            "runtime_inertia_at_com_link_kg_m2": None if observed is None else observed.tolist(),
            "runtime_vs_urdf": None if observed is None else tensor_difference(observed, expected),
        })
    return {"schema": "eai.kinova_inertia_audit.v2", "asset": str(Path(asset).resolve()),
            "asset_sha256": digest(asset), "source_urdf": source["path"], "source_urdf_sha256": source["sha256"],
            "runtime_report": None if runtime_path is None else str(Path(runtime_path).resolve()),
            "runtime_report_sha256": None if runtime_path is None else digest(runtime_path),
            "runner_sha256": digest(__file__), "source_parser_sha256": digest(ROOT / "source/EAI_assets/EAI_assets/robots/kinova_inertials.py"),
            "reference": "inertia about each link COM, expressed in matched USD/URDF link axes; SI units",
            "frame_check": "zero-pose base-relative link frames matched before comparison",
            "rows": rows, "unknown_usd_tensor_links": [row["link"] for row in rows if row["usd_tensor_status"] == "unknown"],
            "runtime_mismatch_links": [row["link"] for row in rows if row["runtime_vs_urdf"] is not None and not row["runtime_vs_urdf"]["match"]],
            "runtime_missing_links": [row["link"] for row in rows if row["runtime_vs_urdf"] is None],
            "limitations": ["No zero-quaternion to identity fallback; unknown USD principal axes stay unknown",
                            "Inertial RPY is applied; no parallel-axis shift because tensors are about their own COM",
                            "Matching source frames at zero pose is not a joint-axis, collision or motor calibration audit",
                            "A URDF match is source consistency, not a real payload or hardware fidelity certificate"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--asset", type=Path, required=True)
    parser.add_argument("--urdf", type=Path, required=True)
    parser.add_argument("--runtime-report", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.report.exists():
        parser.error("Choose a new report path")
    report = audit(args.asset, args.urdf, args.runtime_report)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps({key: report[key] for key in ("unknown_usd_tensor_links", "runtime_mismatch_links", "runtime_missing_links")}))


if __name__ == "__main__":
    main()
