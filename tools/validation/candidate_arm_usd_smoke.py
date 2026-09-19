"""Load a candidate manipulator USD and audit authored physical properties."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path


def physical_fidelity_status(inertials: dict[str, dict], missing_mass_bodies: list[str]) -> tuple[str, list[str]]:
    """Classify authored USD physics without filling missing values."""
    reasons = [f"missing authored mass: {path}" for path in missing_mass_bodies]
    for path, fields in inertials.items():
        for field in ("centerOfMass", "diagonalInertia"):
            if fields.get(field) is None:
                reasons.append(f"missing authored {field}: {path}")
    if reasons:
        return "unknown", reasons
    missing_axes = [path for path, fields in inertials.items()
                    if fields.get("principalAxes") is None or len(fields["principalAxes"]) != 4
                    or not all(math.isfinite(value) for value in fields["principalAxes"])
                    or not math.isclose(sum(value * value for value in fields["principalAxes"]), 1.0, abs_tol=1e-5)]
    if missing_axes:
        return "declared_with_default_principal_axes", [
            f"principalAxes is missing or invalid; runtime schema default is used: {path}"
            for path in missing_axes
        ]
    return "declared_complete", []


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--asset", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--gui", action="store_true")
    parser.add_argument("--steps", type=int, default=120)
    args = parser.parse_args()
    if not args.asset.is_file():
        parser.error(f"Asset does not exist: {args.asset}")
    if args.report.exists():
        parser.error("Choose a new report path")
    if args.steps < 1:
        parser.error("steps must be positive")

    from isaaclab.app import AppLauncher
    launcher = AppLauncher(headless=not args.gui, device="cpu")
    import isaaclab.sim as sim_utils
    from isaaclab.actuators import ImplicitActuatorCfg
    from isaaclab.assets import Articulation, ArticulationCfg
    from pxr import Usd, UsdPhysics
    import torch

    try:
        stage = Usd.Stage.Open(str(args.asset))
        bodies = []
        missing = []
        joints = 0
        collisions = 0
        total_mass = 0.0
        inertials = {}
        for prim in Usd.PrimRange.Stage(stage, Usd.TraverseInstanceProxies()):
            if prim.HasAPI(UsdPhysics.RigidBodyAPI):
                mass_attr = prim.GetAttribute("physics:mass")
                mass = mass_attr.Get() if mass_attr and mass_attr.HasAuthoredValue() else None
                if mass is None or not math.isfinite(float(mass)) or mass <= 0:
                    missing.append(str(prim.GetPath()))
                else:
                    total_mass += float(mass)
                bodies.append(str(prim.GetPath()))
                fields = {}
                for name in ("centerOfMass", "diagonalInertia", "principalAxes"):
                    attr = prim.GetAttribute(f"physics:{name}")
                    value = attr.Get() if attr and attr.HasAuthoredValue() else None
                    if value is None:
                        fields[name] = None
                    elif name == "principalAxes":
                        fields[name] = [float(value.GetReal()), *map(float, value.GetImaginary())]
                    else:
                        fields[name] = list(map(float, value))
                inertials[str(prim.GetPath())] = {"mass_kg": mass, **fields}
            if prim.IsA(UsdPhysics.Joint):
                joints += 1
            if prim.HasAPI(UsdPhysics.CollisionAPI):
                collisions += 1

        simulation = sim_utils.SimulationContext(sim_utils.SimulationCfg(dt=1 / 240, device="cpu"))
        cfg = ArticulationCfg(
            prim_path="/World/Candidate",
            spawn=sim_utils.UsdFileCfg(usd_path=str(args.asset)),
            init_state=ArticulationCfg.InitialStateCfg(joint_vel={".*": 0.0}),
            actuators={
                "unconfigured_candidate_joints": ImplicitActuatorCfg(
                    joint_names_expr=[".*"], effort_limit_sim=0.0,
                    stiffness=0.0, damping=0.0,
                ),
            },
        )
        candidate = Articulation(cfg)
        simulation.reset()
        timestep = simulation.get_physics_dt()
        candidate.update(timestep)
        initial_joint_position = candidate.data.joint_pos[0].tolist()
        initial_root_state = candidate.data.root_state_w[0].tolist()
        finite = True
        completed_steps = 0
        for _ in range(args.steps):
            candidate.write_data_to_sim()
            simulation.step(render=args.gui)
            candidate.update(timestep)
            completed_steps += 1
            if not all(bool(torch.isfinite(values).all()) for values in (
                candidate.data.joint_pos, candidate.data.joint_vel, candidate.data.root_state_w
            )):
                finite = False
                break
        runtime_masses = candidate.root_physx_view.get_masses()[0].cpu().tolist()
        runtime_inertias = candidate.root_physx_view.get_inertias()[0].cpu().tolist()
        source_layers = []
        for layer in stage.GetUsedLayers():
            path = Path(layer.realPath)
            if path.is_file():
                source_layers.append({"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        report = {
            "schema": "eai.candidate_arm_usd_smoke.v2",
            "asset": str(args.asset.resolve()),
            "source_layers": source_layers,
            "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "stage_opened": bool(stage),
            "articulation_loaded": True,
            "rigid_body_count": len(bodies),
            "joint_count": joints,
            "collision_api_count": collisions,
            "authored_mass_total_kg": None if missing else total_mass,
            "authored_mass_subtotal_kg": total_mass,
            "missing_mass_bodies": missing,
            "authored_inertials": inertials,
            "physical_fidelity_status": physical_fidelity_status(inertials, missing),
            "runtime_body_names": candidate.body_names,
            "runtime_masses_kg": runtime_masses,
            "runtime_mass_total_kg": sum(runtime_masses),
            "runtime_inertias_kg_m2": runtime_inertias,
            "runtime_joint_names": candidate.joint_names,
            "runtime_dof_count": candidate.num_joints,
            "initial_joint_position_rad": initial_joint_position,
            "final_joint_position_rad": candidate.data.joint_pos[0].tolist(),
            "initial_root_state": initial_root_state,
            "final_root_state": candidate.data.root_state_w[0].tolist(),
            "physics_execution_tested": completed_steps > 0,
            "physics_steps": completed_steps,
            "dt_s": timestep,
            "finite_state_rollout_pass": finite and completed_steps == args.steps,
            "actuation": "zero effort, stiffness and damping; no commanded motion",
            "hardware_validated": False,
            "limitations": [
                "No mount geometry, base stability, controller, grasp, or hardware calibration test",
                "Authored USD parameters are declared values, not measured hardware values",
            ],
        }
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
        print(f"REPORT_COMPLETE {args.report}", flush=True)
    finally:
        launcher.app.close(wait_for_replicator=False, skip_cleanup=True)


if __name__ == "__main__":
    main()
