"""Bounded native two-carrier Warehouse crate microdiagnostic.

Preparation freezes the inherited native-witness inputs plus this probe.  The
worker uses one caller-owned simulator session and the canonical serial lock.
It applies author-only initial-state overrides before scene creation, then uses
only controller commands and ideal fixed-point connector targets at runtime.
The result is never formal route admission, load-sharing evidence, or a
business outcome.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from copy import deepcopy
import fcntl
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from demo.benchmark import goal_attachment_probe as attachment_probe
from demo.benchmark import goal_native_attachment as native_attachment
from demo.benchmark.goal_native_objects import configure_goal_objects, read_goal_objects
from demo.benchmark.provenance import canonical_sha256, file_sha256
from . import native_witness_probe as witness
from .native_carry import shift_lift_target


ROOT = Path(__file__).resolve().parents[2]
OBJECT = "order_crate_01"
M20_MEMBER = "warehouse_ground_1"
CARTER_MEMBER = "warehouse_ground_3"
M20_RUNTIME_ID = "m20_1"
CARTER_RUNTIME_ID = "m20_2"  # 第二载具亦为 M20（v15 双 M20 载具，名字保留以少动调用点）
COMMAND_NAME = "base_velocity"
ROUTE_CENTER_M = (-10.625, -11.725, 0.30)
ROUTE_YAW_RAD = math.pi / 2
OBJECT_POSE = {
    "position": list(ROUTE_CENTER_M),
    "rotation": [math.sqrt(0.5), 0.0, 0.0, math.sqrt(0.5)],
}
# v15：双 M20 载具（ground_1=m20_1 东 / ground_3=m20_2 西）。阶段18 v10–v14
# 测明 Carter 在 0.25 m 支撑锚点下任一朝向几何不可行（水平预算 0.24 m）；
# 配对选择属被测模型方案空间，此处仅为可靠技能后端诊断换用可行对。
# 站位取两侧 reach/间隙窗口中心（东 [-9.84,-9.56] / 西 [-11.73,-11.45]），
# 腿式静置散布典型 ±0.07 m、罕见 ±0.2 m——罕见越界时按 fail-closed 重跑。
CARRIER_POSES = {
    # v14 站位（v10-v13 四轮实测收敛）。经验规律：Carter（轮式刹车）静置稳定，
    # v11/v13 两验证 spawn -11.120 → settled -11.114/-11.115（前缘间隙 +0.031，
    # 到 support_right reach 0.580 ≤ 0.6）。M20（腿式）距箱 ≤0.1 m 时会滑至
    # 接触为止（v10/-0.020、v11/-0.070、v13/-0.100 三次均停在同一触箱位
    # -9.860，穿透 ~3 mm），而 v12 远距出生（间隙 0.24）静置 +0.019 稳定——
    # 近距存在局部滑移，避让窗口不存在。故 M20 取 v12 已验证的远距站位
    # -9.620：settled ≈-9.60，左缘 -10.05 与箱面间隙 ≈0.25；到 support_left
    # reach 0.724 ≤ 0.8。代价：实测联合 x 包络 ≈2.55 m 超出公开声明 2.37 m
    # ——按 fail-closed 如实申报，本 probe 为微诊断且 formal_route_admission
    # 设计即为 false；紧凑队形（M20 转 90°）需朝向感知运输控制，未在本诊断
    # 采用。不改冻结的模式包络与通路净空。
    # v2/v3 实测：静置漂移是确定性的（同种子同结果）：东侧 spawn-0.1604、
    # 西侧 spawn-0.1953（-x 地板滑动至平衡）。据此精调：东 -9.680 → settled
    # -9.8404，左缘 -10.286 与箱面 -10.302 间隙 +0.016、reach 0.489；西
    # -11.450 → settled -11.6453，右缘 -11.200 与箱左 -10.956 间隙 +0.244、
    # reach 0.716。
    # v5/v6 复盘：运输期间编队内确定性内移 ~4.6 cm，东载具后轮在末态
    # 入箱 1.4 cm；出生再东移 5 cm 对冲（reach 0.696 ≤ 0.797）。
    M20_MEMBER: {"position": [-9.570, -11.725, 0.52], "rotation": [1.0, 0.0, 0.0, 0.0]},
    CARTER_MEMBER: {"position": [-11.450, -11.725, 0.52], "rotation": [1.0, 0.0, 0.0, 0.0]},
}
CARRIERS = {M20_MEMBER: "support_left", CARTER_MEMBER: "support_right"}
RUNTIME_IDS = {M20_MEMBER: M20_RUNTIME_ID, CARTER_MEMBER: CARTER_RUNTIME_ID}
# Y 向联合声明含静置偏航公差：箱本地 y 半宽 0.45 m + 实测静置偏航使联合 y
# 达 0.903 m，按 0.91 声明；X 向 2.37 m 为冻结上限，v11 实测 ≈2.34 m。
PUBLIC_UNION_XY_M = [2.37, 0.91]
LIFT_HEIGHT_M = 0.04
LIFT_SPEED_MPS = 0.05
TRANSPORT_DISTANCE_M = 0.65
TRANSPORT_SPEED_MPS = 0.55
TRANSPORT_MIN_M = 0.50
TRANSPORT_MAX_M = 1.00
MAX_TRANSPORT_STEPS = 900
STALL_WINDOW_STEPS = 300
PROGRESS_EPSILON_M = 0.02
OUTPUT_SCHEMA = "eai.benchmark.warehouse_paired_native_probe_v1"
REPORT_NAME = "paired_native_report.json"


def _write(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n")


def _host(value):
    if hasattr(value, "detach"):
        value = value.detach()
    if hasattr(value, "cpu"):
        value = value.cpu()
    if hasattr(value, "tolist"):
        value = value.tolist()
    if isinstance(value, (tuple, list)):
        return [_host(item) for item in value]
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("nonfinite native readback")
    return number


def _row_by_public_id(deployment, public_id):
    rows = [row for row in deployment["robot_instances"] if row["public_id"] == public_id]
    if len(rows) != 1:
        raise ValueError(f"expected one declared robot: {public_id}")
    return rows[0]


def _object_row(deployment):
    rows = [row for row in deployment["object_instances"] if row["public_id"] == OBJECT]
    if len(rows) != 1:
        raise ValueError("expected one declared crate")
    return rows[0]


def _public_pair_contract(deployment):
    obj = _object_row(deployment)
    physical = obj["physical_properties"]
    if physical["mass_kg"] != 12 or physical["size_m"] != [0.9, 0.65, 0.6]:
        raise ValueError("frozen crate mass/size changed")
    modes = {row["id"]: row for row in physical["handling_modes"]}
    paired = modes.get("paired_support")
    if paired is None or paired["slots"] != [
        {"point_id": "support_left", "load_fraction": 0.5},
        {"point_id": "support_right", "load_fraction": 0.5},
    ]:
        raise ValueError("frozen paired-support slots changed")
    expected = {
        M20_MEMBER: ("m20", M20_RUNTIME_ID, 8, 0.8),
        CARTER_MEMBER: ("m20", CARTER_RUNTIME_ID, 8, 0.8),
    }
    carriers = []
    for public_id, (kind, runtime_id, capacity, reach) in expected.items():
        row = _row_by_public_id(deployment, public_id)
        profile = row["public_declaration"]["physical_profile"]
        if (row["type"], row["runtime_id"], profile["effective_payload_kg"], profile["reach_m"]) != (
                kind, runtime_id, capacity, reach):
            raise ValueError(f"frozen carrier profile changed: {public_id}")
        if "support_socket_v1" not in profile["grasp_interfaces"]:
            raise ValueError(f"carrier lacks declared support connector: {public_id}")
        carriers.append({
            "public_id": public_id,
            "runtime_id": runtime_id,
            "type": kind,
            "point_id": CARRIERS[public_id],
            "ideal_assigned_load_kg": 6.0,
            "public_capacity_kg": capacity,
            "public_reach_m": reach,
            "connector": "support_socket_v1",
            "mechanism": "ideal_fixed_point_connector_not_IK",
        })
    return {
        "object_id": OBJECT,
        "mass_kg": 12.0,
        "mode_id": "paired_support",
        "public_union_xy_m": PUBLIC_UNION_XY_M,
        "carriers": carriers,
        "load_sharing_measured": False,
        "formal_route_admission": False,
    }


def _paired_selection(base_request, deployment, base_selection):
    selection = deepcopy(base_selection)
    changes = []
    if len(selection["robots"]) != len(deployment["robot_instances"]):
        raise ValueError("selection/deployment robot inventory changed")
    for index, robot in enumerate(selection["robots"]):
        deployed = deployment["robot_instances"][index]
        runtime_id, public_id = deployed["runtime_id"], deployed["public_id"]
        inherited = base_request["spawn_adjustment"]
        if runtime_id == inherited["runtime_id"]:
            robot["spawn_pose"] = deepcopy(inherited["original_spawn_pose"])
        if public_id not in CARRIER_POSES:
            continue
        expected_type = "m20"
        if robot["type"] != expected_type or runtime_id != RUNTIME_IDS[public_id]:
            raise ValueError(f"paired carrier selection identity changed: {public_id}")
        original = deepcopy(robot["spawn_pose"])
        robot["spawn_pose"] = deepcopy(CARRIER_POSES[public_id])
        changes.append({
            "public_id": public_id,
            "runtime_id": runtime_id,
            "original_spawn_pose": original,
            "author_diagnostic_spawn_pose": deepcopy(robot["spawn_pose"]),
        })
    if len(changes) != 2:
        raise ValueError("paired carrier overrides incomplete")
    obj = _object_row(deployment)
    object_change = {
        "public_id": OBJECT,
        "runtime_id": obj["runtime_id"],
        "original_init_state": {
            "position": deepcopy(obj["scene_binding"]["deployment_directive"]["position_m"]),
            "rotation": deepcopy(obj["scene_binding"]["deployment_directive"]["rotation_wxyz"]),
        },
        "author_diagnostic_init_state": deepcopy(OBJECT_POSE),
        "applied_after_native_configuration": True,
    }
    return selection, {
        "formal_task_spawn": False,
        "runtime_pose_writes": 0,
        "reason": "bounded_author_paired_connector_microdiagnostic",
        "carrier_overrides": changes,
        "object_override": object_change,
        "inherited_single_probe_adjustment": deepcopy(base_request["spawn_adjustment"]),
        "restored_nonparticipant_to_formal_spawn": {
            "public_id": base_request["spawn_adjustment"]["public_id"],
            "runtime_id": base_request["spawn_adjustment"]["runtime_id"],
            "spawn_pose": deepcopy(base_request["spawn_adjustment"]["original_spawn_pose"]),
        },
    }


def _sources():
    paths = [
        Path(__file__),
        Path(witness.__file__),
        Path(__file__).with_name("native_carry.py"),
        Path(__file__).with_name("paired_shapes.py"),
        ROOT / "demo/benchmark/goal_native_attachment.py",
        ROOT / "demo/benchmark/goal_native_objects.py",
        ROOT / "demo/benchmark/goal_native_occupancy.py",
        ROOT / "demo/benchmark/goal_live_occupancy.py",
        ROOT / "source/EAI/EAI/controllers/base.py",
        ROOT / "source/EAI/EAI/hmrs_env/multi_robot_direct_env.py",
        ROOT / "source/EAI_hmrs/EAI_hmrs/controller_loader.py",
        ROOT / "source/EAI_hmrs/EAI_hmrs/env_builder.py",
        ROOT / "source/EAI_assets/EAI_assets/controller/traditional/carter_diff/carter_diff.py",
        ROOT / "source/EAI_assets/EAI_assets/controller/rl/m20_rough_rsl/m20_rough_rsl.py",
        ROOT / "source/EAI_assets/EAI_assets/controller/rl/m20_rough_rsl/model/m20_rough.onnx",
    ]
    return {str(path.resolve()): file_sha256(path) for path in paths}


def _paired_request(base_request, deployment, selection, overrides):
    return {
        "schema": OUTPUT_SCHEMA + ".request",
        "version": 1,
        "run_id": base_request["run_id"],
        "scene_key": "warehouse_nvd",
        "gui": base_request["gui"],
        "device": base_request["device"],
        "base_attachment_request_sha256": canonical_sha256(base_request),
        "base_witness_request_sha256": canonical_sha256(witness._witness_request(base_request)),
        "deployment_sha256": deployment["sha256"],
        "selection_sha256": canonical_sha256(selection),
        "source_files": _sources(),
        "initial_pose_overrides": overrides,
        "candidate": {
            "route_frame": "warehouse_global_plus_X",
            "object_pose": deepcopy(OBJECT_POSE),
            "carrier_poses": deepcopy(CARRIER_POSES),
            "public_union_xy_m": PUBLIC_UNION_XY_M,
            "public_contract": _public_pair_contract(deployment),
        },
        "settle_steps": 240,
        "lift_height_m": LIFT_HEIGHT_M,
        "lift_speed_mps": LIFT_SPEED_MPS,
        "transport_distance_m": TRANSPORT_DISTANCE_M,
        "transport_speed_mps": TRANSPORT_SPEED_MPS,
        "max_transport_steps": MAX_TRANSPORT_STEPS,
        "release_steps": 60,
        "scope": "ideal_dual_connector_lift_short_straight_transport_lower_detach_microdiagnostic",
        "runtime_body_pose_writes": 0,
        "formal_route_admission": False,
        "load_or_force_sharing_measured": False,
        "scoreable": False,
    }


def prepare_probe(output_root: Path, *, gui: bool = False, device: str = "cuda:0") -> dict:
    """Freeze a unique paired-probe directory without starting Isaac."""
    witness.prepare_probe(output_root, gui=gui, device=device)
    base_request, deployment, base_selection = witness.load_probe(output_root)
    selection, overrides = _paired_selection(base_request, deployment, base_selection)
    request = _paired_request(base_request, deployment, selection, overrides)
    _write(Path(output_root) / "paired_selection.json", selection)
    _write(Path(output_root) / "paired_request.json", request)
    return request


def load_probe(output_root, *, rebuild=True):
    root = Path(output_root)
    base_request, deployment, base_selection = witness.load_probe(root, rebuild=rebuild)
    selection = json.loads((root / "paired_selection.json").read_text())
    request = json.loads((root / "paired_request.json").read_text())
    expected_selection, overrides = _paired_selection(base_request, deployment, base_selection)
    expected = _paired_request(base_request, deployment, expected_selection, overrides)
    if selection != expected_selection or request != expected:
        raise ValueError("paired probe request/source/selection changed")
    for path, digest in request["source_files"].items():
        if file_sha256(Path(path)) != digest:
            raise ValueError(f"paired probe source changed: {path}")
    return request, deployment, selection


@contextmanager
def _archive_report(output_root: Path, report: dict):
    try:
        yield
    except BaseException as exc:
        report.update(status="blocked", blocker=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        # This context exits before the simulator session, so Kit teardown
        # cannot discard the last physical samples.
        _write(output_root / REPORT_NAME, report)


def _controller_contracts(session):
    configs = getattr(session.base_env, "_controller_configs", None)
    if not isinstance(configs, dict):
        raise ValueError("controller configuration inventory unavailable")
    result = {}
    for runtime_id, expected_dim, kind in (
            (M20_RUNTIME_ID, 16, "M20_RSL"), (CARTER_RUNTIME_ID, 16, "M20_RSL")):
        if runtime_id not in session.possible_agents or runtime_id not in configs:
            raise ValueError(f"required paired runtime absent: {runtime_id}")
        cfg = configs[runtime_id]
        if not callable(getattr(cfg, "compute_action_from_command_func", None)):
            raise ValueError(f"controller command conversion unavailable: {runtime_id}")
        if not callable(getattr(cfg, "apply_action_func", None)):
            raise ValueError(f"controller action application unavailable: {runtime_id}")
        if getattr(cfg, "command_name", None) != COMMAND_NAME:
            raise ValueError(f"paired controller is not {COMMAND_NAME} driven: {runtime_id}")
        result[runtime_id] = {
            "controller_kind": kind,
            "external_command": "body_frame_[vx,vy,wz]",
            "command_name": COMMAND_NAME,
            "expected_computed_action_dim": expected_dim,
            "apply_action_callable": True,
        }
    return result


def _commands(session, velocities, aerial_goals):
    import torch

    actions = {agent: torch.zeros((session.num_envs, 3), dtype=torch.float32,
                                  device=session.device)
               for agent in session.possible_agents}
    setter = getattr(session.base_env, "set_command", None)
    if not callable(setter):
        raise ValueError("episode command setter unavailable")
    for runtime_id in (M20_RUNTIME_ID, CARTER_RUNTIME_ID):
        value = velocities.get(runtime_id, [0.0, 0.0, 0.0])
        command = torch.tensor(value, dtype=torch.float32, device=session.device)
        if tuple(command.shape) != (3,) or not all(math.isfinite(float(x)) for x in value):
            raise ValueError(f"invalid carrier command: {runtime_id}")
        command = command.unsqueeze(0).repeat(session.num_envs, 1)
        setter(runtime_id, COMMAND_NAME, command)
        actions[runtime_id] = command
    for agent, goal in aerial_goals.items():
        setter(agent, "goal_position", goal)
        actions[agent] = goal.clone()
    return actions


def _action_readback(session):
    actions = getattr(session.base_env, "_last_computed_actions", None)
    if not isinstance(actions, dict):
        raise ValueError("computed controller actions unavailable")
    result = {}
    for runtime_id, expected in ((M20_RUNTIME_ID, 16), (CARTER_RUNTIME_ID, 16)):
        action = actions.get(runtime_id)
        shape = tuple(getattr(action, "shape", ()))
        if len(shape) != 2 or shape[0] != session.num_envs or shape[-1] != expected:
            raise ValueError(f"unexpected computed action for {runtime_id}: {shape}")
        result[runtime_id] = {
            "computed_action_dim": expected,
            "computed_action": _host(action[0]),
        }
    return result


def _step(session, live, *, velocities=None, aerial_goals=None):
    values = velocities or {}
    session.env.step(_commands(session, values, aerial_goals or {}))
    live.step = int(session.base_env.sim.current_time_step_index)
    return float(session.base_env.sim.current_time), _action_readback(session)


def _aabb_intersects(first, second, *, epsilon=1e-4):
    if (not isinstance(first, list) or not isinstance(second, list)
            or len(first) != 2 or len(second) != 2):
        raise ValueError("complete AABB pair required")
    values = [*first[0], *first[1], *second[0], *second[1]]
    if len(values) != 12 or any(type(x) not in (int, float) or not math.isfinite(x) for x in values):
        raise ValueError("finite AABB coordinates required")
    if any(first[0][i] >= first[1][i] or second[0][i] >= second[1][i] for i in range(3)):
        raise ValueError("nondegenerate AABBs required")
    return all(first[0][i] <= second[1][i] + epsilon
               and second[0][i] <= first[1][i] + epsilon for i in range(3))


def _geometry_blueprint(stage, live, deployment, *, session):
    """Measure enabled collider geometry in each actual PhysX body frame."""
    from pxr import UsdPhysics
    from demo.benchmark import goal_native_occupancy as occupancy

    prims = occupancy._prims(stage)
    roots = {}
    actual_paths = {}
    for row in deployment["robot_instances"]:
        public_id = row["public_id"]
        root = stage.GetPrimAtPath(row["prim_path"])
        if not root:
            raise ValueError(f"robot root missing: {public_id}")
        roots[public_id] = root
        actual_paths[public_id] = list(live.robots[public_id][2])
    obj = _object_row(deployment)
    root = stage.GetPrimAtPath(obj["prim_path"])
    if not root:
        raise ValueError("crate root missing")
    roots[OBJECT] = root
    actual_paths[OBJECT] = [obj["prim_path"]]

    blueprint = {}
    for public_id, root in roots.items():
        descendants = [prim for prim in prims if prim.GetPath().HasPrefix(root.GetPath())]
        bodies = {str(prim.GetPath()): prim for prim in descendants
                  if prim.HasAPI(UsdPhysics.RigidBodyAPI)}
        if set(bodies) != set(actual_paths[public_id]):
            raise ValueError(f"USD/PhysX body inventory mismatch: {public_id}")
        grouped = {path: [] for path in bodies}
        disabled = []
        for prim in descendants:
            if not prim.HasAPI(UsdPhysics.CollisionAPI):
                continue
            enabled = UsdPhysics.CollisionAPI(prim).GetCollisionEnabledAttr().Get()
            if enabled not in (True, False):
                raise ValueError(f"unknown collision enable state: {prim.GetPath()}")
            cursor = prim
            while cursor and str(cursor.GetPath()) not in bodies:
                cursor = cursor.GetParent()
            if not cursor:
                if enabled:
                    raise ValueError(f"enabled collider lacks measured body: {prim.GetPath()}")
                disabled.append(str(prim.GetPath()))
                continue
            if enabled:
                grouped[str(cursor.GetPath())].append(prim)
            else:
                disabled.append(str(prim.GetPath()))
        measured = []
        for body_path, colliders in grouped.items():
            if not colliders:
                continue
            from .paired_shapes import body_geometry
            bounds, evidence = body_geometry(stage, bodies[body_path], colliders, session=session)
            measured.append({
                "body_path": body_path,
                "local_collision_bounds_m": bounds,
                "collision_paths": [str(prim.GetPath()) for prim in colliders],
                "collider_measurements": evidence,
            })
        if not measured:
            raise ValueError(f"no enabled measurable collider geometry: {public_id}")
        blueprint[public_id] = {"root_path": str(root.GetPath()), "bodies": measured,
                                "disabled_collision_paths": disabled}
    return blueprint


def _collision_inventory(stage, deployment):
    """Collect read-only USD collision metadata before native shape cooking.

    This inventory is diagnostic context for an asset/cooking failure. It is
    deliberately not used to admit a route or construct a substitute shape.
    """
    from pxr import UsdGeom, UsdPhysics
    from demo.benchmark import goal_native_occupancy as occupancy

    prims = occupancy._prims(stage)
    rows = []
    for item in [*deployment["robot_instances"], *deployment["object_instances"]]:
        root = stage.GetPrimAtPath(item["prim_path"])
        if not root:
            continue
        for prim in prims:
            if not prim.GetPath().HasPrefix(root.GetPath()) or not prim.HasAPI(UsdPhysics.CollisionAPI):
                continue
            collision = UsdPhysics.CollisionAPI(prim)
            enabled = collision.GetCollisionEnabledAttr().Get()
            row = {
                "public_id": item["public_id"],
                "prim_path": str(prim.GetPath()),
                "type_name": str(prim.GetTypeName()),
                "collision_enabled": enabled,
                "applied_schemas": list(prim.GetAppliedSchemas()),
                "parent_path": str(prim.GetParent().GetPath()),
            }
            for name in ("physics:approximation", "physics:meshResolution", "physics:restOffset",
                         "physics:contactOffset"):
                attr = prim.GetAttribute(name)
                if attr and attr.HasAuthoredValueOpinion():
                    value = attr.Get()
                    row[name] = str(value) if value is not None else None
            if prim.IsA(UsdGeom.Mesh):
                mesh = UsdGeom.Mesh(prim)
                points = mesh.GetPointsAttr().Get()
                row["mesh_point_count"] = len(points) if points is not None else None
                extent = mesh.GetExtentAttr().Get()
                row["mesh_extent"] = [list(value) for value in extent] if extent is not None else None
            xform = UsdGeom.Xformable(prim)
            row["xform_ops"] = [str(op.GetOpType()) for op in xform.GetOrderedXformOps()]
            row["reset_xform_stack"] = bool(xform.GetResetXformStack())
            row["transform_time_varying"] = bool(xform.TransformMightBeTimeVarying())
            rows.append(row)
    return rows


def _geometry_snapshot(live, blueprint):
    """Combine frozen collider bounds with current PhysX body transforms."""
    from demo.benchmark import goal_live_occupancy as occupancy

    poses = {}
    for public_id, (_, view, paths) in live.robots.items():
        transforms = _host(view.get_link_transforms()[0])
        if len(paths) != len(transforms):
            raise ValueError(f"live articulation transform inventory changed: {public_id}")
        poses[public_id] = {
            path: {"position_xyz_m": transform[:3],
                   "orientation_wxyz": [transform[6], *transform[3:6]]}
            for path, transform in zip(paths, transforms)
        }
    obj_path = blueprint[OBJECT]["root_path"]
    obj_pose = live(obj_path)
    poses[OBJECT] = {obj_path: {"position_xyz_m": obj_pose["position_m"],
                               "orientation_wxyz": obj_pose["rotation_wxyz"]}}

    entities = {}
    for public_id, definition in blueprint.items():
        bodies = []
        for body in definition["bodies"]:
            pose = poses.get(public_id, {}).get(body["body_path"])
            if pose is None:
                raise ValueError(f"current body pose missing: {body['body_path']}")
            world = occupancy._world_bounds({**pose,
                "local_collision_bounds_m": body["local_collision_bounds_m"]})
            bodies.append({**deepcopy(body), **deepcopy(pose), "world_aabb_m": world})
        union = [[min(row["world_aabb_m"][0][i] for row in bodies) for i in range(3)],
                 [max(row["world_aabb_m"][1][i] for row in bodies) for i in range(3)]]
        entities[public_id] = {"bodies": bodies, "world_aabb_m": union}
    return entities


def _geometry_check(entities):
    required = {OBJECT, M20_MEMBER, CARTER_MEMBER}
    if not required.issubset(entities):
        raise ValueError("paired geometry inventory incomplete")
    pairs = []
    collisions = []
    names = sorted(entities)
    for index, first_id in enumerate(names):
        for second_id in names[index + 1:]:
            if first_id not in required and second_id not in required:
                continue
            hits = []
            for first in entities[first_id]["bodies"]:
                for second in entities[second_id]["bodies"]:
                    if _aabb_intersects(first["world_aabb_m"], second["world_aabb_m"]):
                        hits.append({"first_body": first["body_path"],
                                     "second_body": second["body_path"]})
            row = {"first_entity": first_id, "second_entity": second_id,
                   "status": "collision" if hits else "separated", "body_hits": hits}
            pairs.append(row)
            collisions.extend([row] if hits else [])
    union = [[min(entities[key]["world_aabb_m"][0][i] for key in required) for i in range(3)],
             [max(entities[key]["world_aabb_m"][1][i] for key in required) for i in range(3)]]
    result = {
        "status": "blocked" if collisions else "measured_separated",
        "method": "actual_enabled_collider_body_local_bounds_plus_current_PhysX_link_poses",
        "conservative_test": "pairwise_world_AABB_touch_or_overlap_blocks",
        "entities": deepcopy(entities),
        "pair_checks": pairs,
        "formation_union_world_aabb_m": union,
        "collision_cooking_and_contact_forces_measured": False,
        "formal_route_admission": False,
    }
    return result


def _record_geometry_check(report, live, blueprint, phase):
    """Archive measured or unknown geometry before enforcing the clear gate."""
    try:
        result = _geometry_check(_geometry_snapshot(live, blueprint))
        result["phase"] = phase
    except Exception as exc:
        result = {"phase": phase, "status": "blocked",
                  "reason": f"{type(exc).__name__}: {exc}",
                  "formal_route_admission": False}
        report["geometry_checks"].append(result)
        raise ValueError(f"candidate geometry unknown during {phase}") from exc
    report["geometry_checks"].append(result)
    if result["status"] != "measured_separated":
        raise ValueError(f"candidate measured collider AABBs collide or touch during {phase}")
    return result


def _pose_evidence(live):
    return {
        public_id: live(live.bindings["members"][public_id]["body_path"])
        for public_id in (M20_MEMBER, CARTER_MEMBER)
    }


def _sample(session, live, receipt, phase, commands, actions, now):
    poses = _pose_evidence(live)
    obj = live(live.bindings["objects"][OBJECT]["body_path"])
    anchors = attachment_probe.anchor_quality(receipt, live.snapshot()["bodies"])
    witness.require_anchor_quality(anchors)
    return {
        "phase": phase,
        "time_s": now,
        "physics_step": live.step,
        "commands": {runtime_id: {"name": COMMAND_NAME, "frame": "body",
                                  "value": deepcopy(commands[runtime_id])}
                     for runtime_id in (M20_RUNTIME_ID, CARTER_RUNTIME_ID)},
        "computed_actions": actions,
        "carrier_poses": poses,
        "object_pose": obj,
        "joint_readback": deepcopy(receipt["joint_readback"]),
        "attachment_anchor_quality": anchors,
        "attachment_receipt_sha256": receipt["sha256"],
    }


def _lift(session, stage, live, receipt, *, direction, active, aerial_goals, samples):
    step_height = min(0.002, LIFT_SPEED_MPS * float(session.base_env.step_dt))
    count = math.ceil(LIFT_HEIGHT_M / step_height)
    increment = direction * LIFT_HEIGHT_M / count
    zeros = {M20_RUNTIME_ID: [0.0, 0.0, 0.0], CARTER_RUNTIME_ID: [0.0, 0.0, 0.0]}
    for _ in range(count):
        receipt = shift_lift_target(stage, receipt, delta_z_m=increment,
                                    pose_provider=live, active_member_ids=active)
        now, actions = _step(session, live, velocities=zeros, aerial_goals=aerial_goals)
        samples.append(_sample(session, live, receipt,
            "dual_joint_lift" if direction > 0 else "dual_joint_lower",
            zeros, actions, now))
    for _ in range(30):
        _step(session, live, velocities=zeros, aerial_goals=aerial_goals)
    return receipt


def _run_locked(output_root: Path, request: dict, deployment: dict, selection: dict):
    from simulator import SimulatorLaunchConfig, open_simulator_session

    report = {
        "schema": OUTPUT_SCHEMA,
        "version": 1,
        "run_id": request["run_id"],
        "paired_request_sha256": canonical_sha256(request),
        "status": "starting",
        "scoreable": False,
        "formal_route_admission": False,
        "business_outcomes_validated": False,
        "load_or_force_sharing": {"status": "unknown", "reason": "no_joint_force_measurement"},
        "runtime_body_pose_writes": 0,
        "samples": [],
        "lift_samples": [],
        "geometry_checks": [],
    }

    def configure(env_cfg):
        env_cfg.sim.enable_scene_query_support = True
        env_cfg.episode_length_s = 3600.0
        env_cfg.sim.render_interval = max(1, round(0.1 / env_cfg.sim.dt))
        report["object_application"] = configure_goal_objects(env_cfg, deployment)
        obj = _object_row(deployment)
        cfg = getattr(env_cfg.scene, obj["runtime_id"], None)
        if cfg is None or getattr(cfg, "init_state", None) is None:
            raise ValueError("configured crate init_state unavailable for author override")
        original = {"position": list(cfg.init_state.pos), "rotation": list(cfg.init_state.rot)}
        expected = request["initial_pose_overrides"]["object_override"]["original_init_state"]
        if original != expected:
            raise ValueError("configured crate initial state differs from frozen original")
        cfg.init_state.pos = tuple(OBJECT_POSE["position"])
        cfg.init_state.rot = tuple(OBJECT_POSE["rotation"])
        report["applied_initial_pose_override"] = {
            "phase": "configuration_before_scene_creation",
            "original": original,
            "override": deepcopy(OBJECT_POSE),
            "runtime_pose_writes": 0,
        }

    config = SimulatorLaunchConfig(
        env=selection["task_name"], resolved_env_name="EAI-Interactive-v0",
        selection_data=selection, num_envs=1, device=request["device"], seed=18,
        headless=not request["gui"], enable_ros_bridge_extension=False,
        disable_orsus_ros_env=True, env_cfg_hook=configure,
    )
    _write(output_root / "paired_native_start.json", report)
    try:
        with open_simulator_session(config) as session, _archive_report(output_root, report):
            import omni.usd

            stage = omni.usd.get_context().get_stage()
            live = attachment_probe.LiveBodies(session.base_env.scene, deployment)
            live.step = int(session.base_env.sim.current_time_step_index)
            report["actual_device"] = session.device
            report["controller_contracts"] = _controller_contracts(session)
            report["public_pair_contract"] = _public_pair_contract(deployment)
            report["initial_object_readback"] = read_goal_objects(
                stage, session.base_env.scene, deployment)
            active = set(live.bindings["members"])
            aerial_goals = {
                row["runtime_id"]: session.base_env.scene.articulations[
                    row["runtime_id"]].data.root_pos_w.clone()
                for row in deployment["robot_instances"]
                if row["public_declaration"]["mobility"] == "aerial"
            }
            zeros = {M20_RUNTIME_ID: [0.0, 0.0, 0.0],
                     CARTER_RUNTIME_ID: [0.0, 0.0, 0.0]}
            for _ in range(request["settle_steps"]):
                _step(session, live, velocities=zeros, aerial_goals=aerial_goals)
            report["settled_object_readback"] = read_goal_objects(
                stage, session.base_env.scene, deployment)
            crate_rows = [row for row in report["settled_object_readback"]["objects"]
                          if row["public_id"] == OBJECT]
            if len(crate_rows) != 1 or crate_rows[0]["status"] != "measured":
                raise ValueError("native crate geometry/mass/pose readback unresolved")

            report["collision_inventory"] = _collision_inventory(stage, deployment)
            _write(output_root / REPORT_NAME, report)
            try:
                blueprint = _geometry_blueprint(stage, live, deployment, session=session)
            except Exception as exc:
                report["geometry_checks"].append({
                    "phase": "collider_inventory",
                    "status": "blocked",
                    "reason": f"{type(exc).__name__}: {exc}",
                    "formal_route_admission": False,
                })
                raise ValueError("candidate collider inventory unknown") from exc
            _record_geometry_check(
                report, live, blueprint, "settled_before_attachment")
            before = live(live.bindings["objects"][OBJECT]["body_path"])
            carriers_before = _pose_evidence(live)
            prepared = native_attachment.prepare_attachment(
                stage, deployment["public_task"], object_id=OBJECT,
                mode_id="paired_support", carriers=CARRIERS,
                active_member_ids=active, body_bindings=live.bindings,
                pose_provider=live)
            receipt = native_attachment.commit_attachment(
                stage, prepared, active_member_ids=active, pose_provider=live)
            if len(receipt["joint_readback"]) != 2:
                raise ValueError("paired attachment did not commit exactly two joints")
            report["attachment_receipt"] = receipt
            report["attachment_step"] = live.step
            _write(output_root / REPORT_NAME, report)

            receipt = _lift(session, stage, live, receipt, direction=1,
                            active=active, aerial_goals=aerial_goals,
                            samples=report["lift_samples"])
            report["apex_attachment_receipt"] = receipt
            apex = live(live.bindings["objects"][OBJECT]["body_path"])
            _record_geometry_check(report, live, blueprint, "dual_joint_lift_apex")
            lift_delta = apex["position_m"][2] - before["position_m"][2]
            report["lift_evidence"] = {
                "status": "measured",
                "object_before": before,
                "object_apex": apex,
                "vertical_displacement_m": lift_delta,
                "target_offset_m": LIFT_HEIGHT_M,
                "joint_count": 2,
                "body_pose_writes": 0,
            }
            if lift_delta < 0.015:
                raise ValueError("dual-joint target changed but crate did not lift")

            transport_start = deepcopy(apex)
            transport_carriers = _pose_evidence(live)
            best_progress = 0.0
            last_progress_step = 0
            velocities = {
                M20_RUNTIME_ID: [TRANSPORT_SPEED_MPS, 0.0, 0.0],
                CARTER_RUNTIME_ID: [TRANSPORT_SPEED_MPS, 0.0, 0.0],
            }
            for transport_step in range(1, MAX_TRANSPORT_STEPS + 1):
                now, actions = _step(session, live, velocities=velocities,
                                     aerial_goals=aerial_goals)
                row = _sample(session, live, receipt, "dual_controller_straight_transport",
                              velocities, actions, now)
                report["samples"].append(row)
                position = row["object_pose"]["position_m"]
                progress = position[0] - transport_start["position_m"][0]
                lateral = abs(position[1] - transport_start["position_m"][1])
                if lateral > 0.15:
                    raise ValueError("paired straight transport exceeded lateral bound")
                if progress >= TRANSPORT_DISTANCE_M:
                    break
                if progress >= best_progress + PROGRESS_EPSILON_M:
                    best_progress = progress
                    last_progress_step = transport_step
                elif transport_step - last_progress_step >= STALL_WINDOW_STEPS:
                    raise ValueError("paired controllers produced no bounded crate progress")
            carried = live(live.bindings["objects"][OBJECT]["body_path"])
            carriers_after = _pose_evidence(live)
            object_distance = math.dist(transport_start["position_m"], carried["position_m"])
            carrier_distances = {
                key: math.dist(transport_carriers[key]["position_m"],
                               carriers_after[key]["position_m"])
                for key in (M20_MEMBER, CARTER_MEMBER)
            }
            anchor_quality = attachment_probe.anchor_quality(
                receipt, live.snapshot()["bodies"])
            witness.require_anchor_quality(anchor_quality)
            report["transport_evidence"] = {
                "status": "measured",
                "object_start": transport_start,
                "object_end": carried,
                "object_displacement_m": object_distance,
                "carrier_start": transport_carriers,
                "carrier_end": carriers_after,
                "carrier_displacement_m": carrier_distances,
                "attachment_anchor_quality": anchor_quality,
                "sample_count": len(report["samples"]),
                "per_carrier_commands_actions_poses_recorded": True,
            }
            if not TRANSPORT_MIN_M <= object_distance <= TRANSPORT_MAX_M:
                raise ValueError("crate displacement outside bounded 0.5-1.0 m microprobe")
            if any(distance < 0.25 for distance in carrier_distances.values()):
                raise ValueError("one carrier did not physically participate in transport")
            _record_geometry_check(report, live, blueprint, "attached_transport_end")

            receipt = _lift(session, stage, live, receipt, direction=-1,
                            active=active, aerial_goals=aerial_goals,
                            samples=report["lift_samples"])
            report["lowered_attachment_receipt"] = receipt
            lowered = live(live.bindings["objects"][OBJECT]["body_path"])
            report["lower_evidence"] = {
                "status": "measured",
                "object": lowered,
                "target_offset_m": receipt["lift_actuation"]["target_offset_m"],
                "vertical_return_error_m": abs(
                    (lowered["position_m"][2] - carried["position_m"][2]) + lift_delta),
                "body_pose_writes": 0,
            }
            if abs(receipt["lift_actuation"]["target_offset_m"]) > 1e-9:
                raise ValueError("connector target did not return to committed baseline")
            _record_geometry_check(report, live, blueprint, "lowered_before_detach")

            report["detach_receipt"] = native_attachment.detach_attachment(stage, receipt)
            report["release_step"] = live.step
            for _ in range(request["release_steps"]):
                _step(session, live, velocities=zeros, aerial_goals=aerial_goals)
            remaining = [row["prim_path"] for row in receipt["joint_readback"]
                         if stage.GetPrimAtPath(row["prim_path"])]
            report["release_evidence"] = {
                "status": "measured" if not remaining else "blocked",
                "post_release_physics_step": live.step,
                "object": live(live.bindings["objects"][OBJECT]["body_path"]),
                "carrier_poses": _pose_evidence(live),
                "remaining_joint_paths": remaining,
                "detach_receipt_sha256": report["detach_receipt"]["sha256"],
            }
            if remaining or live.step <= report["release_step"]:
                raise ValueError("paired detach/post-release evidence incomplete")
            report["final_object_readback"] = read_goal_objects(
                stage, session.base_env.scene, deployment)
            report.update(
                status="diagnostic_completed",
                paired_native_sequence_observed=True,
                physical_task_execution_validated=False,
                business_outcomes_validated=False,
                formal_route_admission=False,
                scoreable=False,
            )
            _write(output_root / REPORT_NAME, report)
    except BaseException as exc:
        report.update(status="blocked", blocker=f"{type(exc).__name__}: {exc}",
                      paired_native_sequence_observed=False,
                      physical_task_execution_validated=False,
                      business_outcomes_validated=False,
                      formal_route_admission=False, scoreable=False)
    _write(output_root / REPORT_NAME, report)
    return report


def run_probe(output_root: Path):
    """Run inside the fresh lock-owning Kit worker."""
    attachment_probe._worker_lock()
    root = Path(output_root).absolute()
    request, deployment, selection = load_probe(root, rebuild=False)
    if any(name == "pxr" or name.startswith("pxr.") for name in sys.modules):
        raise RuntimeError("OpenUSD must not be imported before AppLauncher")
    launch = json.loads((root / "launch.json").read_text())
    expected_command = [sys.executable, "-m", "demo.warehouse_benchmark.paired_native_probe",
                        "--worker", "--output-root", str(root)]
    if (launch.get("command") != expected_command
            or launch.get("run_id") != request["run_id"]
            or launch.get("paired_request_sha256") != canonical_sha256(request)):
        raise ValueError("paired worker launch/request mismatch")
    return _run_locked(root, request, deployment, selection)


def execute_probe(output_root: Path, *, timeout_s=600.0):
    root = Path(output_root).absolute()
    request, _, _ = load_probe(root)
    if not math.isfinite(timeout_s) or timeout_s <= 0:
        raise ValueError("timeout must be positive")
    lock_path = ROOT / "tmp/goal_physical_probe.lock"
    lock_path.parent.mkdir(exist_ok=True)
    with lock_path.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("another goal physical scene is running") from exc
        command = [sys.executable, "-m", "demo.warehouse_benchmark.paired_native_probe",
                   "--worker", "--output-root", str(root)]
        launch = {"command": command, "run_id": request["run_id"],
                  "paired_request_sha256": canonical_sha256(request),
                  "source_files": deepcopy(request["source_files"]),
                  "timeout_s": timeout_s}
        with (root / "launch.json").open("x") as stream:
            json.dump(launch, stream, indent=2)
        started = time.monotonic()
        timed_out, interrupted = False, None
        with (root / "paired_native.log").open("x") as log:
            process = subprocess.Popen(
                command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                start_new_session=True, pass_fds=(lock.fileno(),),
                env={**os.environ, "EAI_GOAL_PHYSICAL_WORKER_LOCK_FD": str(lock.fileno())})

            def terminate_parent(signum, frame):
                raise KeyboardInterrupt("paired probe parent terminated")

            def stop():
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGTERM)
                try:
                    return process.wait(timeout=10.0)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    return process.wait(timeout=10.0)

            previous = signal.signal(signal.SIGTERM, terminate_parent)
            try:
                code = process.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                timed_out = True
                code = stop()
            except BaseException as exc:
                interrupted = exc
                code = stop()
            finally:
                signal.signal(signal.SIGTERM, previous)
        report_path = root / REPORT_NAME
        report = json.loads(report_path.read_text()) if report_path.is_file() else {}
        result = {
            "run_id": request["run_id"],
            "returncode": code,
            "timed_out": timed_out,
            "wall_time_s": time.monotonic() - started,
            "paired_request_sha256": canonical_sha256(request),
            "report_sha256": file_sha256(report_path) if report_path.is_file() else None,
            "diagnostic_completed": (code == 0 and not timed_out
                and report.get("status") == "diagnostic_completed"
                and report.get("paired_request_sha256") == canonical_sha256(request)
                and report.get("run_id") == request["run_id"]),
            "formal_route_admission": False,
            "scoreable": False,
        }
        _write(root / "paired_process_result.json", result)
        if interrupted:
            raise interrupted
        load_probe(root, rebuild=False)
        return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--gui", action="store_true")
    parser.add_argument("--device", choices=("cpu", "cuda:0"), default="cuda:0")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--execute", action="store_true")
    modes.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--timeout-s", type=float, default=600.0)
    args = parser.parse_args(argv)
    if args.worker:
        result = run_probe(args.output_root)
    elif args.execute:
        result = execute_probe(args.output_root, timeout_s=args.timeout_s)
    else:
        result = prepare_probe(args.output_root, gui=args.gui, device=args.device)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
