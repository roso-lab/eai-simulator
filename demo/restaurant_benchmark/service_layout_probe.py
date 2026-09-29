"""Spawn the restaurant dishes and static customers in Isaac Sim.

The placements come from ``service_layout``. The scene is the hash-checked
restaurant benchmark candidate, because the kitchen and dining coordinates
were admitted on that ground collider. A successful run records poses and
support raycasts. It does not enable execution, business, or score gates.
"""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import traceback
import uuid

from .config import BENCHMARK_SCENE_KEY
from .service_layout import DISH_USD, service_layout


DISH_MASS_KG = 0.45
DISH_SPAWN_CLEARANCE_M = 0.03
SCHEMA = "eai.restaurant.service_layout_probe"
_SPAWN_NOTES: list[dict] = []


def _write(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _vec(value) -> list[float]:
    return [float(item) for item in value]


def _hit_record(hit) -> dict:
    if not isinstance(hit, dict):
        return {"hit": False, "raw_type": type(hit).__name__}
    row = {"hit": hit.get("hit") is True}
    for key in ("position", "normal"):
        item = hit.get(key)
        if item is not None:
            output_key = "position_m" if key == "position" else key
            row[output_key] = _vec(item)
    if hit.get("distance") is not None:
        row["distance_m"] = float(hit["distance"])
    collision = hit.get("collision")
    row["collision"] = collision if isinstance(collision, str) else str(collision)
    return row


def spawn_service_dish(prim_path, cfg, translation=None, orientation=None, **kwargs):
    """Reference the metre dish and give the plate and apple one dynamic body."""
    from pxr import Usd, UsdGeom, UsdPhysics

    from isaaclab.sim.utils import create_prim

    prim = create_prim(
        prim_path,
        usd_path=cfg.usd_path,
        translation=translation,
        orientation=orientation,
    )
    root = prim if hasattr(prim, "IsValid") else None
    if root is None or not root.IsValid():
        raise RuntimeError(f"dish prim was not created: {prim_path}")
    body = UsdPhysics.RigidBodyAPI.Apply(root)
    body.CreateRigidBodyEnabledAttr().Set(True)
    body.CreateKinematicEnabledAttr().Set(False)
    UsdPhysics.MassAPI.Apply(root).CreateMassAttr().Set(DISH_MASS_KG)
    mesh_count = 0
    for child in Usd.PrimRange(root):
        if child.IsA(UsdGeom.Mesh):
            UsdPhysics.CollisionAPI.Apply(child).CreateCollisionEnabledAttr().Set(True)
            UsdPhysics.MeshCollisionAPI.Apply(child).CreateApproximationAttr().Set(
                "convexHull"
            )
            mesh_count += 1
    if mesh_count < 2:
        raise RuntimeError(
            f"dish {prim_path} expected plate and apple meshes, found {mesh_count}"
        )
    _author_dish_support_sockets(root)
    _SPAWN_NOTES.append({"prim_path": str(prim_path), "mesh_count": mesh_count})
    return prim


def _author_dish_support_sockets(root) -> None:
    """Put the public support socket on the dish the attachment check will read."""
    from pxr import Gf, UsdGeom

    from demo.benchmark.goal_driven_fixtures import object_physical_properties

    public_id = root.GetName()
    for point in object_physical_properties(public_id)["grasp_points"]:
        grasp = UsdGeom.Xform.Define(root.GetStage(), f"{root.GetPath()}/GraspPoints/{point['id']}")
        grasp.AddTranslateOp().Set(Gf.Vec3d(*point["position_m"]))
        grasp.GetPrim().SetCustomDataByKey("goal_interface", point["interface"])


def _dish_cfg(name: str, position_m: list[float]):
    import isaaclab.sim as sim_utils
    from isaaclab.assets import RigidObjectCfg
    from isaaclab.sim.utils import clone

    return RigidObjectCfg(
        prim_path=f"{{ENV_REGEX_NS}}/{name}",
        spawn=sim_utils.UsdFileCfg(
            usd_path=str(DISH_USD),
            func=clone(spawn_service_dish),
            rigid_props=sim_utils.RigidBodyPropertiesCfg(
                kinematic_enabled=False,
                disable_gravity=False,
            ),
            mass_props=sim_utils.MassPropertiesCfg(mass=DISH_MASS_KG),
        ),
        init_state=RigidObjectCfg.InitialStateCfg(pos=tuple(position_m)),
    )


def _shelf_cfg(name: str, position_m: list[float], size_m: list[float]):
    import isaaclab.sim as sim_utils
    from isaaclab.assets import RigidObjectCfg

    return RigidObjectCfg(
        prim_path=f"{{ENV_REGEX_NS}}/{name}",
        spawn=sim_utils.CuboidCfg(
            size=tuple(size_m),
            rigid_props=sim_utils.RigidBodyPropertiesCfg(
                kinematic_enabled=True,
                disable_gravity=True,
            ),
            mass_props=sim_utils.MassPropertiesCfg(mass=1.0),
            collision_props=sim_utils.CollisionPropertiesCfg(collision_enabled=True),
            visual_material=sim_utils.PreviewSurfaceCfg(diffuse_color=(0.45, 0.32, 0.18)),
        ),
        init_state=RigidObjectCfg.InitialStateCfg(pos=tuple(position_m)),
    )


def attach_service_dishes(env_cfg) -> None:
    """Add kitchen pass shelves, dining drop shelves, and both dishes."""
    env_cfg.sim.enable_scene_query_support = True
    env_cfg.episode_length_s = 3600.0
    layout = service_layout()
    shelves = {**layout["pass_shelves"], **layout["drop_shelves"]}
    for name, row in shelves.items():
        if hasattr(env_cfg.scene, name):
            raise RuntimeError(f"scene cfg already has {name}")
        size_m = row.get("size_m", layout["pass_shelf_size_m"])
        setattr(env_cfg.scene, name, _shelf_cfg(name, row["position_m"], size_m))
    for name, row in layout["dishes"].items():
        if hasattr(env_cfg.scene, name):
            raise RuntimeError(f"scene cfg already has {name}")
        spawned = [row["position_m"][0], row["position_m"][1], row["position_m"][2] + DISH_SPAWN_CLEARANCE_M]
        setattr(env_cfg.scene, name, _dish_cfg(name, spawned))


def _world_bounds(prim) -> dict:
    from EAI_assets.humans.asset_placement import grounding_world_range

    bounds = grounding_world_range(prim)
    low, high = bounds.GetMin(), bounds.GetMax()
    return {
        "min_m": _vec(low),
        "max_m": _vec(high),
        "size_m": [float(high[i] - low[i]) for i in range(3)],
    }


def _spawn_customers(stage, layout: dict) -> tuple[object, dict]:
    from pxr import Usd, UsdPhysics
    from EAI_assets.humans.registry import HumanAssetRegistry
    from EAI_assets.humans.stage_runtime import HumanActorConfig, UsdHumanStageRuntime

    registry = HumanAssetRegistry.load_default()
    runtime = UsdHumanStageRuntime(
        stage,
        registry,
        cache_root=registry.human_root / "motions/cache",
        verify_hashes=True,
    )
    rows = {}
    with Usd.EditContext(stage, stage.GetSessionLayer()):
        for name, customer in layout["customers"].items():
            position = customer["position_m"]
            prim_path = f"/World/RestaurantServiceCustomers/{name}"
            runtime.spawn(
                HumanActorConfig(
                    actor_id=name,
                    asset_id=customer["asset_id"],
                    prim_path=prim_path,
                    initial_pose=(
                        float(position[0]),
                        float(position[1]),
                        float(position[2]),
                        float(customer["spawn_yaw_rad"]),
                    ),
                    waypoints=(),
                    speed=0.0,
                    loop=False,
                    locomotion_motion_id=customer["pose_motion_id"],
                )
            )
            prim = stage.GetPrimAtPath(prim_path)
            for child in Usd.PrimRange(prim):
                if child.HasAPI(UsdPhysics.RigidBodyAPI):
                    UsdPhysics.RigidBodyAPI(child).CreateRigidBodyEnabledAttr(False)
                if child.HasAPI(UsdPhysics.CollisionAPI):
                    UsdPhysics.CollisionAPI(child).CreateCollisionEnabledAttr(False)
            bounds = _world_bounds(prim)
            rows[name] = {
                "asset_id": customer["asset_id"],
                "prim_path": prim_path,
                "authored_position_m": _vec(position),
                "authored_yaw_rad": float(customer["yaw_rad"]),
                "runtime_position_m": _vec(runtime.position(name)),
                "world_bounds": bounds,
                "locomotion_motion_id": customer["pose_motion_id"],
                "spawn_yaw_rad": float(customer["spawn_yaw_rad"]),
                "waypoints": [],
                "animate_while_idle": False,
            }
    return runtime, rows


def _concrete_prim(stage, prim_path: str):
    prim = stage.GetPrimAtPath(prim_path)
    if prim.IsValid():
        return prim
    concrete = prim_path.replace("/World/envs/env_.*", "/World/envs/env_0")
    return stage.GetPrimAtPath(concrete)


def _dish_sample(scene, stage) -> dict:
    rows = {}
    for name, body in scene.rigid_objects.items():
        root = _vec(body.data.root_pos_w[0].detach().cpu())
        velocity = _vec(body.data.root_lin_vel_w[0].detach().cpu())
        prim = _concrete_prim(stage, str(body.cfg.prim_path))
        rows[name] = {
            "root_position_m": root,
            "linear_velocity_m_s": velocity,
            "speed_m_s": math.dist(velocity, (0.0, 0.0, 0.0)),
            "world_bounds": _world_bounds(prim) if prim.IsValid() else None,
        }
    return rows


def run_probe(
    output_root: str | Path,
    *,
    products_root: str | Path,
    device: str,
    settle_steps: int,
    observe_steps: int,
) -> dict:
    if device not in {"cpu", "cuda:0"}:
        raise ValueError("device must be cpu or cuda:0")
    if not 1 <= settle_steps <= 1200 or not 1 <= observe_steps <= 1200:
        raise ValueError("settle and observe steps must be in [1, 1200]")
    products = Path(products_root).expanduser().resolve()
    output = Path(output_root).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=False)
    os.environ["EAI_RESTAURANT_BENCHMARK_ROOT"] = str(products)
    layout = service_layout()
    report = {
        "schema": SCHEMA,
        "version": 1,
        "run_id": uuid.uuid4().hex,
        "scene_key": BENCHMARK_SCENE_KEY,
        "products_root": str(products),
        "dish_usd": str(DISH_USD),
        "status": "starting",
        "requested_device": device,
        "settle_steps": settle_steps,
        "observe_steps": observe_steps,
        "authored_layout": layout,
        "scope": "dish_rigid_spawn_and_static_customer_pose_only",
        "execution_validated": False,
        "business_outcomes_validated": False,
        "benchmark_ready": False,
        "scoreable": False,
        "support_attach_validated": False,
        "surface_contact_validated": False,
    }
    report_path = output / "service_layout_probe_report.json"
    _write(report_path, report)

    from simulator import SimulatorLaunchConfig, open_simulator_session

    launch = SimulatorLaunchConfig(
        env="restaurant_service_layout",
        resolved_env_name="EAI-Interactive-v0",
        selection_data={
            "version": 1,
            "task_name": "restaurant_service_layout",
            "scene_key": BENCHMARK_SCENE_KEY,
            "robots": [
                {
                    "type": "carter",
                    "controller": {"mode": "default", "cfg": "CARTER_DIFF_CFG"},
                    "attachments": [],
                }
            ],
        },
        num_envs=1,
        device=device,
        seed=17,
        headless=True,
        enable_ros_bridge_extension=False,
        disable_orsus_ros_env=True,
        env_cfg_hook=attach_service_dishes,
    )
    try:
        with open_simulator_session(launch) as session:
            import omni.physx
            import omni.usd
            import torch

            stage = omni.usd.get_context().get_stage()
            scene_prim = stage.GetPrimAtPath("/World/RestaurantBenchmark")
            if not scene_prim or not scene_prim.IsValid():
                raise RuntimeError("restaurant benchmark scene prim is absent")
            initial = _dish_sample(session.base_env.scene, stage)
            from pxr import Usd

            visual = stage.GetPrimAtPath("/World/RestaurantBenchmark/Visual")
            visual_prim_count = len(list(Usd.PrimRange(visual))) if visual.IsValid() else 0
            query = omni.physx.get_physx_scene_query_interface()
            support = {}
            for name, row in layout["dishes"].items():
                # Offset past the dish so the ray measures the scene support, not the apple.
                origin = (float(row["position_m"][0]) + 1.5, float(row["position_m"][1]), 2.0)
                support[name] = _hit_record(query.raycast_closest(origin, (0.0, 0.0, -1.0), 3.0))
                support[name]["ray_origin_m"] = list(origin)
            for name, row in layout["customers"].items():
                origin = (float(row["position_m"][0]), float(row["position_m"][1]), 2.0)
                support[name] = _hit_record(query.raycast_closest(origin, (0.0, 0.0, -1.0), 3.0))
                support[name]["ray_origin_m"] = list(origin)
            customer_error = None
            customer_runtime = None
            try:
                customer_runtime, customers = _spawn_customers(stage, layout)
            except Exception as exc:
                customers = {}
                customer_error = f"{type(exc).__name__}: {exc}"
            agent = session.possible_agents[0]

            def step_zero(count: int) -> None:
                for _ in range(count):
                    session.env.step(
                        {agent: torch.zeros((session.num_envs, 3), device=session.device)}
                    )
                    if customer_runtime is not None:
                        customer_runtime.update(
                            session.base_env.step_dt,
                            animate_while_idle=False,
                        )

            step_zero(settle_steps)
            settled = _dish_sample(session.base_env.scene, stage)
            if customer_runtime is not None:
                for name, row in customers.items():
                    prim = stage.GetPrimAtPath(row["prim_path"])
                    row["settled_runtime_position_m"] = _vec(customer_runtime.position(name))
                    row["settled_world_bounds"] = _world_bounds(prim)
            step_zero(observe_steps)
            observed = _dish_sample(session.base_env.scene, stage)
            if customer_runtime is not None:
                for name, row in customers.items():
                    prim = stage.GetPrimAtPath(row["prim_path"])
                    row["observed_runtime_position_m"] = _vec(customer_runtime.position(name))
                    row["observed_world_bounds"] = _world_bounds(prim)
            report.update(
                status="diagnostic_completed",
                actual_device=str(session.device),
                physics_steps=int(session.base_env.sim.current_time_step_index),
                spawn_notes=list(_SPAWN_NOTES),
                dishes_initial=initial,
                dishes_settled=settled,
                dishes_observed=observed,
                support_raycasts=support,
                customers=customers,
                customer_spawn_error=customer_error,
                possible_agents=list(session.possible_agents),
                restaurant_visual_prim_count=visual_prim_count,
                restaurant_visual_loaded=visual_prim_count > 10,
            )
            _write(report_path, report)
    except BaseException as exc:
        report.update(
            status="failed",
            error=f"{type(exc).__name__}: {exc}",
            traceback=traceback.format_exc(),
            spawn_notes=list(_SPAWN_NOTES),
        )
        _write(report_path, report)
        raise
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--products-root", type=Path, required=True)
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--settle-steps", type=int, default=300)
    parser.add_argument("--observe-steps", type=int, default=60)
    args = parser.parse_args(argv)
    report = run_probe(
        args.output_root,
        products_root=args.products_root,
        device=args.device,
        settle_steps=args.settle_steps,
        observe_steps=args.observe_steps,
    )
    print(json.dumps({
        "status": report["status"],
        "report": str(Path(args.output_root).resolve() / "service_layout_probe_report.json"),
        "scoreable": report["scoreable"],
        "customer_spawn_error": report.get("customer_spawn_error"),
    }, indent=2, sort_keys=True), flush=True)
    return 0 if report["status"] == "diagnostic_completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
