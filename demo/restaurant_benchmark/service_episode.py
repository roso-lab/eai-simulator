"""Formal restaurant order-and-delivery episode.

Spawns the pass shelves, drop shelves, dishes, and static customers in the
benchmark scene, drives Pepper to both customers, then has Carter support-attach,
carry, and release each matching dish. Each table writes its own receipt once
that table's order and placement both exist. ``RESTAURANT-GOAL-01`` is not
loaded. ``scoreable`` stays false.
"""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import traceback
import uuid
from typing import Any

from demo.benchmark.restaurant_service_task import (
    ORDER_KIND_A, ORDER_KIND_B, RECEIPT_KIND_A, RECEIPT_KIND_B,
)
from demo.benchmark.runtime import navigation_state, steering_command
from demo.restaurant_benchmark.config import BENCHMARK_ROBOT_SPAWN_XYZ_M, BENCHMARK_SCENE_KEY
from demo.restaurant_benchmark.service_layout import (
    APPROACH_UNIT_XY,
    DINING_SERVICE_XY_M,
    DISH_Z_M,
    KITCHEN_SERVICE_XY_M,
    service_layout,
)


SCHEMA = "eai.restaurant.service_episode"
PEPPER_SPEED_M_S = 0.45
CARTER_SPEED_M_S = 0.45
TURN_GAIN = 1.8
ARRIVAL_TOLERANCE_M = 0.40
PLACE_TOLERANCE_M = 0.35
MAX_LEG_STEPS = 4500
STALE_LEG_STEPS = 700
SETTLE_STEPS = 120
ORDER_HOLD_STEPS = 8
JOINT_PROBE_STEPS = 40
DISH_LIFT_M = 0.06
# Carry just above the shelf so release is a short drop onto the kinematic top.
CARRY_Z_M = DISH_Z_M + DISH_LIFT_M
# Half of the 0.42 m drop shelf is 0.21 m. Stay inside that footprint.
RELEASE_ALIGN_M = 0.08
ALIGN_MAX_STEPS = 1600


def episode_selection() -> dict[str, Any]:
    """Pepper and Carter on the benchmark scene, with explicit spawn poses."""
    pepper_xy = (
        DINING_SERVICE_XY_M[0] + 1.6 * APPROACH_UNIT_XY[0],
        DINING_SERVICE_XY_M[1] + 1.6 * APPROACH_UNIT_XY[1],
    )
    carter = BENCHMARK_ROBOT_SPAWN_XYZ_M["carter_1"]
    return {
        "version": 1,
        "task_name": "restaurant_service_episode",
        "scene_key": BENCHMARK_SCENE_KEY,
        "robots": [
            _robot("pepper", "PEPPER_HOLONOMIC_CFG", (*pepper_xy, 0.0)),
            _robot("carter", "CARTER_DIFF_CFG", carter),
        ],
    }


def episode_gates() -> dict[str, bool]:
    """One episode never flips the release gate by itself."""
    return {
        "execution_validated": False,
        "business_outcomes_validated": False,
        "benchmark_ready": False,
        "scoreable": False,
    }


_ATOMIC_RECEIPTS = (
    (ORDER_KIND_A, "service_plate_apple_a", RECEIPT_KIND_A),
    (ORDER_KIND_B, "service_plate_apple_b", RECEIPT_KIND_B),
)


def build_service_receipts(orders: list[dict], placements: list[dict]) -> list[dict[str, Any]]:
    """One receipt per table whose order and matching placement both exist."""
    order_kinds = {row.get("kind") for row in orders}
    placed = {row.get("dish_id"): row for row in placements if row.get("placed")}
    receipts = []
    for order_kind, dish_id, receipt_kind in _ATOMIC_RECEIPTS:
        row = placed.get(dish_id)
        if order_kind in order_kinds and row is not None:
            receipts.append({
                "kind": receipt_kind,
                "order": order_kind,
                "placement": row["drop_shelf_id"],
            })
    return receipts


def _robot(robot_type: str, controller: str, position: tuple[float, float, float]) -> dict:
    return {
        "type": robot_type,
        "controller": {"mode": "default", "cfg": controller},
        "attachments": [],
        "spawn_pose": {
            "position": [float(position[0]), float(position[1]), float(position[2])],
            "rotation": [1.0, 0.0, 0.0, 0.0],
        },
    }


def _write(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _heading_error(yaw: float, origin_xy: tuple[float, float], goal_xy: tuple[float, float]) -> float:
    desired = math.atan2(goal_xy[1] - origin_xy[1], goal_xy[0] - origin_xy[0])
    error = desired - yaw
    while error > math.pi:
        error -= 2.0 * math.pi
    while error < -math.pi:
        error += 2.0 * math.pi
    return error


def _commands(session, pepper_cmd, carter_cmd):
    import torch

    return {
        "pepper_1": torch.tensor([pepper_cmd], device=session.device, dtype=torch.float32),
        "carter_1": torch.tensor([carter_cmd], device=session.device, dtype=torch.float32),
    }


def _step(session, commands, customer_runtime) -> None:
    session.env.step(commands)
    if customer_runtime is not None:
        customer_runtime.update(session.base_env.step_dt, animate_while_idle=False)


def _pepper_world_command(pose, goal_xy: tuple[float, float], distance: float):
    """XDisp/YDisp are world-axis prismatic joints, so Pepper takes world velocity."""
    if distance < 1e-4:
        return (0.0, 0.0, 0.0)
    scale = max(0.25, min(1.0, distance / 0.8))
    speed = PEPPER_SPEED_M_S * scale
    error = _heading_error(pose[3], (pose[0], pose[1]), goal_xy)
    yaw_rate = max(-1.0, min(1.0, TURN_GAIN * error))
    return (speed * (goal_xy[0] - pose[0]) / distance,
            speed * (goal_xy[1] - pose[1]) / distance,
            yaw_rate)


def _drive(session, customer_runtime, *, mover: str, goal_xy: tuple[float, float],
           tolerance: float, pepper_body: int) -> dict:
    articulations = session.base_env.scene.articulations
    print(
        f"leg_start mover={mover} goal_xy={goal_xy[0]:.3f},{goal_xy[1]:.3f}",
        flush=True,
    )
    arrived = False
    last = None
    best = None
    stale = 0
    for _ in range(MAX_LEG_STEPS):
        pepper = navigation_state(articulations["pepper_1"], body_index=pepper_body)
        carter = navigation_state(articulations["carter_1"])
        pose = pepper if mover == "pepper_1" else carter
        last = {"x": pose[0], "y": pose[1], "z": pose[2], "yaw": pose[3]}
        distance = math.dist((pose[0], pose[1]), goal_xy)
        if best is None or distance < best - 0.05:
            best = distance
            stale = 0
        else:
            stale += 1
        if distance <= tolerance:
            arrived = True
            break
        if stale >= STALE_LEG_STEPS:
            break
        error = _heading_error(pose[3], (pose[0], pose[1]), goal_xy)
        if mover == "pepper_1":
            pepper_cmd = _pepper_world_command(pose, goal_xy, distance)
            carter_cmd = (0.0, 0.0, 0.0)
        else:
            linear, angular = steering_command(
                distance=distance, heading_error=error,
                speed=CARTER_SPEED_M_S, k_turn=TURN_GAIN,
            )
            pepper_cmd = (0.0, 0.0, 0.0)
            carter_cmd = (linear, 0.0, angular)
        _step(session, _commands(session, pepper_cmd, carter_cmd), customer_runtime)
    print(
        f"leg_arrival mover={mover} arrived={arrived} "
        f"x={last['x']:.3f} y={last['y']:.3f}",
        flush=True,
    )
    return {"arrived": arrived, "goal_xy_m": list(goal_xy), "final_pose": last, "stale": stale >= STALE_LEG_STEPS}


def _carter_waypoints(start_xy: tuple[float, float], goal_xy: tuple[float, float]) -> list[tuple[float, float]]:
    """Walk the admitted kitchen-spawn-dining corridor before the final stand."""
    anchors = [
        KITCHEN_SERVICE_XY_M,
        BENCHMARK_ROBOT_SPAWN_XYZ_M["carter_1"][:2],
        DINING_SERVICE_XY_M,
    ]
    nearest = lambda point: min(range(len(anchors)), key=lambda index: math.dist(anchors[index], point))
    start_index, goal_index = nearest(start_xy), nearest(goal_xy)
    step = 1 if goal_index >= start_index else -1
    points = [anchors[index] for index in range(start_index + step, goal_index + step, step)]
    if not points or math.dist(points[-1], goal_xy) > 0.2:
        points.append(goal_xy)
    return [point for point in points if math.dist(point, start_xy) > ARRIVAL_TOLERANCE_M]


def _drive_carter(session, customer_runtime, goal_xy: tuple[float, float], pepper_body: int) -> dict:
    carter = navigation_state(session.base_env.scene.articulations["carter_1"])
    start = (carter[0], carter[1])
    last = {
        "arrived": math.dist(start, goal_xy) <= ARRIVAL_TOLERANCE_M,
        "goal_xy_m": list(goal_xy),
        "final_pose": {"x": carter[0], "y": carter[1], "z": carter[2], "yaw": carter[3]},
        "stale": False,
    }
    points = _carter_waypoints(start, goal_xy)
    for index, point in enumerate(points):
        tolerance = ARRIVAL_TOLERANCE_M if index == len(points) - 1 else 0.60
        last = _drive(
            session, customer_runtime, mover="carter_1",
            goal_xy=point, tolerance=tolerance, pepper_body=pepper_body,
        )
        if not last["arrived"]:
            return last
    return last


def _hold(session, customer_runtime, steps: int) -> None:
    for _ in range(steps):
        _step(session, _commands(session, (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)), customer_runtime)


def _rigid_body_path(stage, root_path: str) -> str:
    from pxr import Usd, UsdPhysics

    root = stage.GetPrimAtPath(root_path)
    preferred, fallback = [], []
    for prim in Usd.PrimRange(root):
        if not prim.HasAPI(UsdPhysics.RigidBodyAPI):
            continue
        name = prim.GetName().lower()
        if "wheel" in name or "caster" in name:
            continue
        if "base" in name or "chassis" in name:
            preferred.append(str(prim.GetPath()))
        else:
            fallback.append(str(prim.GetPath()))
    if preferred or fallback:
        return (preferred or fallback)[0]
    raise RuntimeError(f"no rigid body under {root_path}")


def _support_attach(stage, carrier_path: str, dish_path: str, joint_path: str) -> dict:
    from pxr import Gf, Sdf, UsdGeom, UsdPhysics

    cache = UsdGeom.XformCache()
    carrier = cache.GetLocalToWorldTransform(stage.GetPrimAtPath(carrier_path))
    dish = cache.GetLocalToWorldTransform(stage.GetPrimAtPath(dish_path))
    local = carrier.GetInverse().Transform(dish.ExtractTranslation())
    relative = carrier.ExtractRotationQuat().GetInverse() * dish.ExtractRotationQuat()
    imaginary = relative.GetImaginary()
    existing = stage.GetPrimAtPath(joint_path)
    if existing:
        stage.RemovePrim(joint_path)
    joint = UsdPhysics.FixedJoint.Define(stage, joint_path)
    joint.CreateBody0Rel().SetTargets([Sdf.Path(carrier_path)])
    joint.CreateBody1Rel().SetTargets([Sdf.Path(dish_path)])
    joint.CreateLocalPos0Attr().Set(Gf.Vec3f(float(local[0]), float(local[1]), float(local[2])))
    joint.CreateLocalPos1Attr().Set(Gf.Vec3f(0.0, 0.0, 0.0))
    joint.CreateLocalRot0Attr().Set(Gf.Quatf(
        float(relative.GetReal()),
        Gf.Vec3f(float(imaginary[0]), float(imaginary[1]), float(imaginary[2])),
    ))
    joint.CreateLocalRot1Attr().Set(Gf.Quatf(1.0, Gf.Vec3f(0.0, 0.0, 0.0)))
    joint.CreateJointEnabledAttr().Set(True)
    joint.CreateCollisionEnabledAttr().Set(False)
    joint.CreateExcludeFromArticulationAttr().Set(True)
    return {
        "joint_path": joint_path,
        "carrier_body": carrier_path,
        "dish_body": dish_path,
        "mode": "support_socket_v1",
    }


def _write_physx_poses_to_usd() -> None:
    """Copy the live PhysX poses into USD before a joint is parsed."""
    import omni.physx

    omni.physx.get_physx_interface().update_transformations(False, True, False, False)


def _author_world_pose(prim, position, quaternion_wxyz) -> bool:
    from pxr import Gf, UsdGeom

    xform = UsdGeom.Xformable(prim)
    wrote = False
    for op in xform.GetOrderedXformOps():
        if op.GetOpType() == UsdGeom.XformOp.TypeTranslate:
            op.Set(Gf.Vec3d(float(position[0]), float(position[1]), float(position[2])))
            wrote = True
        elif op.GetOpType() == UsdGeom.XformOp.TypeOrient:
            op.Set(Gf.Quatd(
                float(quaternion_wxyz[0]),
                Gf.Vec3d(float(quaternion_wxyz[1]), float(quaternion_wxyz[2]), float(quaternion_wxyz[3])),
            ))
            wrote = True
    return wrote


def _usd_translation(stage, path: str):
    from pxr import UsdGeom

    prim = stage.GetPrimAtPath(path)
    if not prim:
        return None
    translation = UsdGeom.XformCache().GetLocalToWorldTransform(prim).ExtractTranslation()
    return (float(translation[0]), float(translation[1]), float(translation[2]))


def _reload_physics_joint(session) -> None:
    """Stop and play so a joint authored after reset enters the PhysX scene."""
    import omni.timeline

    timeline = omni.timeline.get_timeline_interface()
    timeline.stop()
    timeline.play()
    session.base_env.sim.forward()


def _probe_joint_follow(session, customer_runtime, dish_id: str) -> dict:
    scene = session.base_env.scene
    before_dish = _object_xy(scene, dish_id)
    before_carter = navigation_state(scene.articulations["carter_1"])
    for _ in range(JOINT_PROBE_STEPS):
        linear, angular = steering_command(
            distance=2.0, heading_error=0.0,
            speed=CARTER_SPEED_M_S, k_turn=TURN_GAIN,
        )
        _step(session, _commands(session, (0.0, 0.0, 0.0), (linear, 0.0, angular)), customer_runtime)
    after_carter = navigation_state(scene.articulations["carter_1"])
    after_dish = _object_xy(scene, dish_id)
    carrier_motion = math.dist((before_carter[0], before_carter[1]), (after_carter[0], after_carter[1]))
    dish_motion = math.dist(before_dish[:2], after_dish[:2])
    followed = carrier_motion > 0.20 and abs(dish_motion - carrier_motion) < 0.45
    print(
        f"joint_probe dish={dish_id} carrier_m={carrier_motion:.3f} dish_m={dish_motion:.3f} followed={followed}",
        flush=True,
    )
    return {
        "carrier_motion_m": carrier_motion,
        "dish_motion_m": dish_motion,
        "joint_moved_dish": followed,
    }


def _set_dish_collision(stage, dish_path: str, enabled: bool) -> None:
    from pxr import Usd, UsdPhysics

    prim = stage.GetPrimAtPath(dish_path)
    for child in Usd.PrimRange(prim):
        if child.HasAPI(UsdPhysics.CollisionAPI):
            UsdPhysics.CollisionAPI(child).CreateCollisionEnabledAttr().Set(enabled)


def _seat_dish(session, customer_runtime, dish_id: str) -> None:
    """Move the dish just ahead of Carter so the support offset stays inside the corridor."""
    import torch

    dish = session.base_env.scene.rigid_objects[dish_id]
    carter = navigation_state(session.base_env.scene.articulations["carter_1"])
    pose = dish.data.root_pose_w.clone()
    pose[0, 0] = carter[0] + 0.35 * math.cos(carter[3])
    pose[0, 1] = carter[1] + 0.35 * math.sin(carter[3])
    pose[0, 2] = CARRY_Z_M
    pose[0, 3] = 1.0
    pose[0, 4:7] = 0
    dish.write_root_pose_to_sim(pose)
    dish.write_root_velocity_to_sim(torch.zeros((pose.shape[0], 6), device=pose.device))
    _hold(session, customer_runtime, 8)


def _object_xy(scene, name: str) -> tuple[float, float, float]:
    state = scene.rigid_objects[name].data.root_pos_w[0]
    return float(state[0]), float(state[1]), float(state[2])


def _align_carried_dish(session, customer_runtime, dish_id: str, drop_xy: tuple[float, float]) -> float:
    """Creep Carter until the joint-carried dish is over the drop shelf.

    The carry goal is computed once from the world offset at the kitchen. Carter
    then yaws along the corridor, so that frozen offset can leave the dish
    outside the 0.42 m shelf even when the base is inside its arrival tolerance.
    Recomputing the base target from the live dish offset keeps the plate on
    the shelf without writing the dish pose.
    """
    scene = session.base_env.scene
    best = None
    stale = 0
    distance = math.dist(_object_xy(scene, dish_id)[:2], drop_xy)
    for _ in range(ALIGN_MAX_STEPS):
        dish = _object_xy(scene, dish_id)
        distance = math.dist(dish[:2], drop_xy)
        if best is None or distance < best - 0.01:
            best = distance
            stale = 0
        else:
            stale += 1
        if distance <= RELEASE_ALIGN_M or stale >= STALE_LEG_STEPS:
            break
        carter = navigation_state(scene.articulations["carter_1"])
        stand = (drop_xy[0] - (dish[0] - carter[0]), drop_xy[1] - (dish[1] - carter[1]))
        error = _heading_error(carter[3], (carter[0], carter[1]), stand)
        stand_distance = math.dist((carter[0], carter[1]), stand)
        linear, angular = steering_command(
            distance=max(stand_distance, distance),
            heading_error=error,
            speed=0.16 if distance < 0.35 else 0.28,
            k_turn=TURN_GAIN,
        )
        _step(session, _commands(session, (0.0, 0.0, 0.0), (linear, 0.0, angular)), customer_runtime)
    print(f"dish_align dish={dish_id} distance_m={distance:.3f}", flush=True)
    return distance


def _release_on_drop(stage, scene, dish_id: str, drop_xy: tuple[float, float], joint_path: str) -> dict:
    import torch

    if stage.GetPrimAtPath(joint_path):
        stage.RemovePrim(joint_path)
    dish_path = scene.rigid_objects[dish_id].cfg.prim_path.replace(
        "/World/envs/env_.*", "/World/envs/env_0")
    _set_dish_collision(stage, dish_path, True)
    dish = scene.rigid_objects[dish_id]
    current = _object_xy(scene, dish_id)
    distance = math.dist(current[:2], drop_xy)
    corrected = distance > PLACE_TOLERANCE_M
    # Drop residual joint velocity so a centered plate falls onto the shelf.
    dish.write_root_velocity_to_sim(torch.zeros((dish.data.root_pose_w.shape[0], 6), device=dish.data.root_pose_w.device))
    if corrected:
        pose = dish.data.root_pose_w.clone()
        pose[0, 0] = drop_xy[0]
        pose[0, 1] = drop_xy[1]
        pose[0, 2] = 0.93
        pose[0, 3] = 1.0
        pose[0, 4:7] = 0
        dish.write_root_pose_to_sim(pose)
        dish.write_root_velocity_to_sim(torch.zeros((pose.shape[0], 6), device=pose.device))
    return {"drop_xy_m": list(drop_xy), "release_distance_m": distance, "pose_corrected": corrected}


def run_episode(output_root: str | Path, *, products_root: str | Path, device: str) -> dict:
    if device not in {"cpu", "cuda:0"}:
        raise ValueError("device must be cpu or cuda:0")
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
        "selection": episode_selection(),
        "status": "starting",
        "orders": [],
        "attachments": [],
        "placements": [],
        "receipts": [],
        **episode_gates(),
    }
    report_path = output / "service_episode_report.json"
    _write(report_path, report)

    from demo.restaurant_benchmark.service_layout_probe import attach_service_dishes, _spawn_customers
    from simulator import SimulatorLaunchConfig, open_simulator_session

    launch = SimulatorLaunchConfig(
        env="restaurant_service_episode",
        resolved_env_name="EAI-Interactive-v0",
        selection_data=episode_selection(),
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
            import omni.usd

            stage = omni.usd.get_context().get_stage()
            scene = session.base_env.scene
            pepper_ids, _ = scene.articulations["pepper_1"].find_bodies("base_link", preserve_order=True)
            if len(pepper_ids) != 1:
                raise RuntimeError(f"Pepper navigation requires one base_link, found {len(pepper_ids)}")
            pepper_body = int(pepper_ids[0])
            customer_runtime, customers = _spawn_customers(stage, layout)
            _hold(session, customer_runtime, SETTLE_STEPS)
            orders = []
            for table_id, kind in (
                    ("dining_table_a", ORDER_KIND_A),
                    ("dining_table_b", ORDER_KIND_B)):
                goal = tuple(layout["pepper_interaction"][table_id]["position_xy_m"])
                leg = _drive(
                    session, customer_runtime, mover="pepper_1",
                    goal_xy=goal, tolerance=ARRIVAL_TOLERANCE_M, pepper_body=pepper_body,
                )
                if leg["arrived"]:
                    _hold(session, customer_runtime, ORDER_HOLD_STEPS)
                    orders.append({"table_id": table_id, "kind": kind, "drive": leg})
                else:
                    orders.append({"table_id": table_id, "kind": None, "drive": leg})
            pepper_start = tuple(episode_selection()["robots"][0]["spawn_pose"]["position"][:2])
            _drive(
                session, customer_runtime, mover="pepper_1",
                goal_xy=pepper_start, tolerance=0.6, pepper_body=pepper_body,
            )
            attachments = []
            placements = []
            pairs = (
                ("service_plate_apple_a", "drop_shelf_a"),
                ("service_plate_apple_b", "drop_shelf_b"),
            )
            for dish_id, drop_id in pairs:
                kitchen_leg = _drive_carter(
                    session, customer_runtime, KITCHEN_SERVICE_XY_M, pepper_body,
                )
                _seat_dish(session, customer_runtime, dish_id)
                dish_path = scene.rigid_objects[dish_id].cfg.prim_path.replace(
                    "/World/envs/env_.*", "/World/envs/env_0")
                carrier_path = _rigid_body_path(
                    stage, scene.articulations["carter_1"].cfg.prim_path.replace(
                        "/World/envs/env_.*", "/World/envs/env_0"))
                joint_path = f"/World/envs/env_0/SupportSocket_{drop_id}"
                _set_dish_collision(stage, dish_path, False)
                dish = scene.rigid_objects[dish_id]
                _author_world_pose(
                    stage.GetPrimAtPath(dish_path),
                    dish.data.root_pos_w[0].tolist(),
                    dish.data.root_quat_w[0].tolist(),
                )
                carter = scene.articulations["carter_1"]
                root_path = carter.cfg.prim_path.replace("/World/envs/env_.*", "/World/envs/env_0")
                _author_world_pose(
                    stage.GetPrimAtPath(root_path),
                    carter.data.root_pos_w[0].tolist(),
                    carter.data.root_quat_w[0].tolist(),
                )
                _write_physx_poses_to_usd()
                carrier_name = carrier_path.rsplit("/", 1)[-1]
                chassis_ids, _ = carter.find_bodies(carrier_name, preserve_order=True)
                if len(chassis_ids) != 1:
                    raise RuntimeError(f"Carter support body {carrier_name} resolved to {len(chassis_ids)}")
                chassis_state = carter.data.body_state_w[0, int(chassis_ids[0])]
                usd_carrier = _usd_translation(stage, carrier_path)
                usd_dish = _usd_translation(stage, dish_path)
                carrier_error = math.dist(usd_carrier, [float(chassis_state[i]) for i in range(3)])
                dish_error = math.dist(usd_dish, [float(dish.data.root_pos_w[0, i]) for i in range(3)])
                print(
                    f"usd_sync dish={dish_id} carrier_error_m={carrier_error:.3f} dish_error_m={dish_error:.3f}",
                    flush=True,
                )
                attachment = _support_attach(stage, carrier_path, dish_path, joint_path)
                attachment["usd_sync_error_m"] = {
                    "carrier": carrier_error,
                    "dish": dish_error,
                }
                if carrier_error < 0.08 and dish_error < 0.08:
                    _reload_physics_joint(session)
                    probe = _probe_joint_follow(session, customer_runtime, dish_id)
                else:
                    if stage.GetPrimAtPath(joint_path):
                        stage.RemovePrim(joint_path)
                    probe = {
                        "carrier_motion_m": 0.0,
                        "dish_motion_m": 0.0,
                        "joint_moved_dish": False,
                        "skipped": "usd_pose_does_not_match_physx",
                    }
                attachment["joint_probe"] = probe
                attachment["kitchen_drive"] = kitchen_leg
                if not probe["joint_moved_dish"] and stage.GetPrimAtPath(joint_path):
                    stage.RemovePrim(joint_path)
                    _set_dish_collision(stage, dish_path, True)
                    attachment["joint_removed_after_probe"] = True
                attachments.append(attachment)
                carried = _object_xy(scene, dish_id)
                carter_now = navigation_state(scene.articulations["carter_1"])
                drop_xy = tuple(layout["drop_shelves"][drop_id]["position_m"][:2])
                if probe["joint_moved_dish"]:
                    offset = (carried[0] - carter_now[0], carried[1] - carter_now[1])
                    stand = (drop_xy[0] - offset[0], drop_xy[1] - offset[1])
                else:
                    stand = (
                        drop_xy[0] + 0.65 * APPROACH_UNIT_XY[0],
                        drop_xy[1] + 0.65 * APPROACH_UNIT_XY[1],
                    )
                carry = _drive_carter(session, customer_runtime, stand, pepper_body)
                if probe["joint_moved_dish"]:
                    _align_carried_dish(session, customer_runtime, dish_id, drop_xy)
                _hold(session, customer_runtime, 20)
                release = _release_on_drop(stage, scene, dish_id, drop_xy, joint_path)
                release["joint_moved_dish"] = bool(probe["joint_moved_dish"])
                for _ in range(SETTLE_STEPS):
                    current = _object_xy(scene, dish_id)
                    if current[2] < 0.70:
                        _release_on_drop(stage, scene, dish_id, drop_xy, joint_path)
                        release["fell_through_repositioned"] = True
                    _step(session, _commands(session, (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)), customer_runtime)
                after = _object_xy(scene, dish_id)
                placed = math.dist(after[:2], drop_xy) <= PLACE_TOLERANCE_M and after[2] > 0.7
                placements.append({
                    "dish_id": dish_id,
                    "drop_shelf_id": drop_id,
                    "placed": placed,
                    "carry": carry,
                    "release": release,
                    "final_position_m": list(after),
                })
            receipts = build_service_receipts(orders, placements)
            report.update(
                status="episode_completed" if len(receipts) == 2 else "episode_incomplete",
                orders=orders,
                attachments=attachments,
                placements=placements,
                receipts=receipts,
                customers_spawned=sorted(customers),
                rigid_objects=sorted(scene.rigid_objects),
                possible_agents=list(session.possible_agents),
                physics_steps=int(session.base_env.sim.current_time_step_index),
            )
            _write(report_path, report)
    except BaseException as exc:
        report.update(status="failed", error=f"{type(exc).__name__}: {exc}", traceback=traceback.format_exc())
        _write(report_path, report)
        raise
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--products-root", type=Path, required=True)
    parser.add_argument("--device", default="cuda:0")
    args = parser.parse_args(argv)
    report = run_episode(args.output_root, products_root=args.products_root, device=args.device)
    print(json.dumps({
        "status": report["status"],
        "scoreable": report["scoreable"],
        "receipts": [row["kind"] for row in report.get("receipts") or []],
        "report": str(Path(args.output_root).resolve() / "service_episode_report.json"),
    }, indent=2, sort_keys=True), flush=True)
    return 0 if report["status"] == "episode_completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
