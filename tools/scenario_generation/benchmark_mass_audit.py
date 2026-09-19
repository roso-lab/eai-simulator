"""Compare benchmark mounted-arm PhysX masses with unscaled source USD links."""
from __future__ import annotations

import hashlib
import math
from pathlib import Path


def authored_inertials(asset):
    import numpy as np
    from pxr import Gf, Usd, UsdPhysics

    stage = Usd.Stage.Open(str(asset))
    if stage is None:
        raise ValueError(f"cannot inspect arm asset {asset}")
    bodies = {}
    for prim in stage.Traverse():
        if not prim.HasAPI(UsdPhysics.RigidBodyAPI):
            continue
        mass_api = UsdPhysics.MassAPI(prim)
        mass = mass_api.GetMassAttr().Get()
        if mass is None or not math.isfinite(float(mass)) or mass <= 0:
            continue
        if prim.GetName() in bodies:
            raise ValueError(f"ambiguous authored rigid body {prim.GetName()}")
        diagonal = mass_api.GetDiagonalInertiaAttr().Get()
        axes = mass_api.GetPrincipalAxesAttr().Get()
        inertia = None
        if diagonal is not None and axes is not None and all(math.isfinite(value) and value > 0 for value in diagonal):
            rotation = np.asarray(Gf.Matrix3d(Gf.Quatd(axes)))
            inertia = (rotation.T @ np.diag(diagonal) @ rotation).reshape(-1).tolist()
        bodies[prim.GetName()] = {"mass_kg": float(mass), "inertia_kg_m2": inertia}
    layers = [{"path": layer.realPath,
               "sha256": hashlib.sha256(Path(layer.realPath).read_bytes()).hexdigest()}
              for layer in stage.GetUsedLayers() if layer.realPath and Path(layer.realPath).is_file()]
    return bodies, layers


def audit_benchmark_masses(base_env):
    from EAI.scenario_spec.benchmark_physics import BENCHMARK_PHYSICS_CONTRACT

    arms, violations = {}, []
    for name, handle in base_env.scene.articulations.items():
        if not name.endswith("_arm"):
            continue
        asset = Path(handle.cfg.spawn.usd_path)
        authored, layers = authored_inertials(asset)
        masses = handle.root_physx_view.get_masses()[0].cpu().tolist()
        inertias = handle.root_physx_view.get_inertias()[0].cpu().tolist()
        links = {}
        for body, mass, inertia in zip(handle.body_names, masses, inertias, strict=True):
            expected_body = authored.get(body, {})
            expected = expected_body.get("mass_kg")
            if expected is None or not math.isclose(mass, expected, rel_tol=1e-4, abs_tol=1e-6):
                violations.append(f"{name}/{body}: runtime mass {mass} != authored mass {expected}")
            if not all(math.isfinite(value) for value in inertia):
                violations.append(f"{name}/{body}: nonfinite runtime inertia")
            expected_inertia = expected_body.get("inertia_kg_m2")
            if expected_inertia is None or any(not math.isclose(actual, expected, rel_tol=1e-4, abs_tol=1e-7)
                                               for actual, expected in zip(inertia, expected_inertia)):
                violations.append(f"{name}/{body}: runtime inertia differs from authored tensor or source is unknown")
            links[body] = {"authored_mass_kg": expected, "runtime_mass_kg": mass,
                           "authored_inertia_kg_m2": expected_inertia,
                           "runtime_inertia_kg_m2": inertia}
        missing = set(authored) - set(handle.body_names)
        if missing:
            violations.append(f"{name}: missing authored links {sorted(missing)}")
        arms[name] = {"source_asset": str(asset), "source_layers": layers, "links": links,
                      "runtime_total_mass_kg": sum(masses),
                      "authored_total_mass_kg": sum(body["mass_kg"] for body in authored.values())}
    return {"contract": dict(BENCHMARK_PHYSICS_CONTRACT), "validated": not violations,
            "arms": arms, "violations": violations,
            "limitations": ["Unscaled asset agreement is not hardware mass or actuator certification",
                            "Runtime inertias compared in body frame; not hardware calibration"]}
