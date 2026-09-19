"""Physically validate an externally supplied, fixed-base Kinova Gen3 USD."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path


def authored_physics_status(inertials: dict[str, dict], missing_mass_bodies: list[str]) -> tuple[str, list[str]]:
    """Report unknown for missing COM, moments, or valid principal axes."""
    reasons = [f"missing authored mass: {path}" for path in missing_mass_bodies]
    for path, fields in inertials.items():
        for field in ("centerOfMass", "diagonalInertia"):
            if fields.get(field) is None:
                reasons.append(f"missing authored {field}: {path}")
        axes = fields.get("principalAxes")
        if (axes is None or len(axes) != 4 or not all(math.isfinite(value) for value in axes)
                or not math.isclose(sum(value * value for value in axes), 1, abs_tol=1e-5)):
            reasons.append(f"missing or invalid principalAxes: {path}")
    if reasons:
        return "unknown", reasons
    return "declared", []


def mode_config(mode: str) -> dict[str, object]:
    if mode == "zero-drive":
        return {"name": mode, "effort_limit_sim": 0.0, "stiffness": 0.0, "damping": 0.0,
                "target": "none", "description": "zero drive; no commanded motion"}
    if mode == "constrained-targets":
        return {"name": mode, "effort_limit_sim": 39.0, "stiffness": 40.0, "damping": 1.0,
                "target": "authored initial joint position", "description": "limited joint targets"}
    raise ValueError(f"unsupported mode: {mode}")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def apply_explicit_zero_mass_link(stage, path: str) -> dict[str, object]:
    """Validate a source-verified zero-mass link without corrupting PhysX mass."""
    from pxr import UsdPhysics

    prim = stage.GetPrimAtPath(path)
    if not prim.IsValid():
        raise ValueError(f"Kinova source link does not exist: {path}")
    return {"path": path, "applied": False, "source_mass_kg": 0.0,
            "reason": "PhysX rigid bodies cannot use a zero mass; model this URDF interface link as non-rigid or remove it from the articulation.",
            "has_rigid_body_api": prim.HasAPI(UsdPhysics.RigidBodyAPI)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--asset", type=Path, required=True, help="external Kinova Gen3 USD input")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--mode", choices=("zero-drive", "constrained-targets", "both"), default="both")
    parser.add_argument("--steps", type=int, default=120)
    parser.add_argument("--gui", action="store_true")
    parser.add_argument("--apply-urdf-zero-mass", action="store_true",
                        help="Apply the externally supplied URDF zero-mass end-effector source value")
    parser.add_argument("--urdf-zero-mass-link", default="/gen3/end_effector_link")
    parser.add_argument("--model-urdf-massless-link", action="store_true",
                        help="Represent the URDF zero-mass interface link without a PhysX rigid body")
    parser.add_argument("--urdf", type=Path, help="Source URDF required for massless interface conversion")
    parser.add_argument("--apply-urdf-inertias", action="store_true",
                        help="Restore complete URDF tensors after validating source link frames, mass and COM")
    args = parser.parse_args()
    if not args.asset.is_file():
        parser.error(f"external USD does not exist: {args.asset}")
    if args.report.exists():
        parser.error("choose a new report path")
    if args.steps < 1:
        parser.error("steps must be positive")
    if (args.model_urdf_massless_link or args.apply_urdf_inertias) and (args.urdf is None or not args.urdf.is_file()):
        parser.error("URDF interface/inertia conversion requires an existing --urdf source")

    from isaaclab.app import AppLauncher
    launcher = AppLauncher(headless=not args.gui, device="cpu")
    failed = False
    try:
        import torch
        import isaaclab.sim as sim_utils
        from isaaclab.actuators import ImplicitActuatorCfg
        from isaaclab.assets import Articulation, ArticulationCfg
        from pxr import Usd, UsdPhysics

        stage = Usd.Stage.Open(str(args.asset))
        inertials, missing_mass, bodies, joints, collisions = {}, [], [], 0, 0
        authored_mass_total = 0.0
        for prim in Usd.PrimRange.Stage(stage, Usd.TraverseInstanceProxies()):
            if prim.HasAPI(UsdPhysics.RigidBodyAPI):
                path = str(prim.GetPath())
                mass_attr = prim.GetAttribute("physics:mass")
                mass = mass_attr.Get() if mass_attr and mass_attr.HasAuthoredValue() else None
                if mass is None or not math.isfinite(float(mass)) or float(mass) <= 0:
                    missing_mass.append(path)
                else:
                    authored_mass_total += float(mass)
                fields = {"mass_kg": mass}
                for name in ("centerOfMass", "diagonalInertia"):
                    attr = prim.GetAttribute(f"physics:{name}")
                    value = attr.Get() if attr and attr.HasAuthoredValue() else None
                    fields[name] = None if value is None else list(map(float, value))
                axes = prim.GetAttribute("physics:principalAxes").Get()
                fields["principalAxes"] = None if axes is None else [float(axes.GetReal()), *map(float, axes.GetImaginary())]
                inertials[path] = fields
                bodies.append(path)
            if prim.IsA(UsdPhysics.Joint):
                joints += 1
            if prim.HasAPI(UsdPhysics.CollisionAPI):
                collisions += 1

        modes = ("zero-drive", "constrained-targets") if args.mode == "both" else (args.mode,)
        mode_reports = []
        simulation = sim_utils.SimulationContext(sim_utils.SimulationCfg(dt=1 / 240, device="cpu"))
        settings = mode_config("constrained-targets")
        cfg = ArticulationCfg(
                prim_path="/World/KinovaGen3",
                spawn=sim_utils.UsdFileCfg(
                    usd_path=str(args.asset),
                    articulation_props=sim_utils.ArticulationRootPropertiesCfg(
                        fix_root_link=True,
                        enabled_self_collisions=True,
                    ),
                ),
                init_state=ArticulationCfg.InitialStateCfg(joint_vel={".*": 0.0}),
                actuators={"gen3": ImplicitActuatorCfg(joint_names_expr=[".*"], **{
                    key: settings[key] for key in ("effort_limit_sim", "stiffness", "damping")})},
        )
        arm = Articulation(cfg)
        source_overrides = []
        source_inertia_metadata = None
        if args.apply_urdf_inertias:
            from EAI_assets.robots.kinova_inertials import apply_source_inertias
            source_inertia_metadata = apply_source_inertias(simulation.stage, cfg.prim_path, args.urdf)
        if args.model_urdf_massless_link:
            from EAI_assets.robots.kinova_physics import model_massless_interface
            source_overrides.append(model_massless_interface(simulation.stage, cfg.prim_path, str(args.urdf)))
        elif args.apply_urdf_zero_mass:
            source_overrides.append(apply_explicit_zero_mass_link(stage, args.urdf_zero_mass_link))
        simulation.reset()
        if source_overrides and source_overrides[0].get("applied"):
            if "end_effector_link" in arm.body_names or len(arm.body_names) != 8 or arm.num_joints != 7:
                raise RuntimeError("Massless conversion must produce eight massive links and seven DOFs")
        dt = simulation.get_physics_dt()
        target = arm.data.default_joint_pos.clone()
        for mode in modes:
            settings = mode_config(mode)
            arm.write_joint_state_to_sim(target, torch.zeros_like(target))
            arm.reset()
            limits = torch.tensor([[0.0 if mode == "zero-drive" else
                                    39.0 if name in {"joint_1", "joint_2", "joint_3", "joint_4"} else 9.0
                                    for name in arm.joint_names]], device=arm.device)
            arm.write_joint_stiffness_to_sim(settings["stiffness"])
            arm.write_joint_damping_to_sim(settings["damping"])
            arm.write_joint_effort_limit_to_sim(limits)
            runtime_drives = {
                "stiffness": arm.root_physx_view.get_dof_stiffnesses()[0].tolist(),
                "damping": arm.root_physx_view.get_dof_dampings()[0].tolist(),
                "effort_limits_Nm": arm.root_physx_view.get_dof_max_forces()[0].tolist(),
            }
            if mode == "zero-drive" and any(value != 0.0 for values in runtime_drives.values() for value in values):
                raise RuntimeError("zero-drive parameters did not reach PhysX")
            finite = True
            completed = 0
            for _ in range(args.steps):
                arm.set_joint_position_target(target)
                arm.set_joint_velocity_target(torch.zeros_like(arm.data.joint_vel))
                arm.write_data_to_sim()
                simulation.step(render=args.gui)
                arm.update(dt)
                completed += 1
                if not all(bool(torch.isfinite(values).all()) for values in (arm.data.joint_pos, arm.data.joint_vel, arm.data.root_state_w)):
                    finite = False
                    break
            mode_reports.append({"mode": mode_config(mode), "configured_actuation": settings,
                "runtime_drives": runtime_drives,
                "max_joint_displacement_rad": float((arm.data.joint_pos - target).abs().max()),
                "commanded_motion": "none" if mode == "zero-drive" else "authored initial joint position",
                "fixed_base": True, "mass_scale": 1.0,
                "runtime_body_names": arm.body_names, "runtime_masses_kg": arm.root_physx_view.get_masses()[0].cpu().tolist(),
                "runtime_inertias_kg_m2": arm.root_physx_view.get_inertias()[0].cpu().tolist(),
                "runtime_joint_names": arm.joint_names, "runtime_dof_count": arm.num_joints,
                "physics_steps": completed, "finite_state_rollout_pass": finite and completed == args.steps, "dt_s": dt})
        status, reasons = authored_physics_status(inertials, missing_mass)
        effective_status = status
        effective_reasons = reasons
        if any(item.get("applied") for item in source_overrides):
            effective_reasons = [reason for reason in reasons
                                 if args.urdf_zero_mass_link not in reason]
            effective_status = "unknown" if effective_reasons else "authored_with_source_resolved_interface"
        report = {"schema": "eai.kinova_gen3_mount_smoke.v2", "asset": str(args.asset.resolve()),
            "external_usd_input": True, "source_hash_sha256": sha256(args.asset),
            "source_layers": [{"path": layer.realPath, "sha256": sha256(Path(layer.realPath))}
                              for layer in stage.GetUsedLayers() if layer.realPath and Path(layer.realPath).is_file()],
            "runner_sha256": sha256(Path(__file__)), "fixed_base": True, "mass_scale": 1.0,
            "rigid_body_count": len(bodies), "joint_count": joints, "collision_api_count": collisions,
            "authored_mass_total_kg": None if missing_mass else authored_mass_total,
            "authored_inertials": inertials, "missing_mass_bodies": missing_mass,
            "physical_fidelity_status": status, "physical_fidelity_reasons": reasons,
            "effective_physical_fidelity_status": effective_status,
            "effective_physical_fidelity_reasons": effective_reasons,
            "source_overrides": source_overrides,
            "source_inertia_metadata": source_inertia_metadata,
            "hardware_calibration": "unknown",
            "modes": mode_reports,
            "limitations": ["No asset download, hardware calibration, grasp, or payload test",
                            "Massless interface resolution does not validate the remaining inertia tensors or real motor limits",
                            "Missing authored COM/diagonal inertia or invalid principal axes is reported unknown; source overrides are explicit"]}
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        print(f"REPORT_COMPLETE {args.report}", flush=True)
    except Exception:
        import traceback
        failed = True
        failure = traceback.format_exc()
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.with_suffix(".error.txt").write_text(failure)
        print(failure, flush=True)
    finally:
        launcher.app.close(wait_for_replicator=False, skip_cleanup=True)
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
