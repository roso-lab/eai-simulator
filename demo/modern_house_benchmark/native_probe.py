"""Serial, bounded House sensor/path diagnostic using the reusable session API.

Both snapshots are real observations separated by simulation steps. Between
them the author-side intervention repositions the deployed armchair with the
native paired-support fixed joints and withdraws every ground robot from the
resident corridor; the after snapshot then measures whatever scene actually
resulted. The intervention is an author diagnostic action, never a business
execution: ``scoreable`` stays False and no household task outcome is claimed.
Camera capture does not require ROS.
"""

import argparse
import fcntl
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from demo.benchmark import goal_physical_probe as physical
from demo.benchmark.provenance import canonical_sha256, file_sha256


ROOT = Path(__file__).resolve().parents[2]
POINTS = [[4., 3.5, .25], [3.5, 3.5, .25], [3.5, 9.5, .25],
          [3.5, 12.3, .25], [4.5, 12.3, .25]]
SETTLE_STEPS = 120
BETWEEN_STEPS = 30
INTERVENTION = {
    "carry_object_id": "living_armchair_01",
    "carry_mode_id": "paired_support",
    # 载具为双 M20（house_ground_1=m20_1 + house_ground_3=m20_2）。v5 实测
    # house_ground_2（Carter，reach 0.6、根 z 0）在扶手椅 support 锚点高度下
    # 3D 水平预算仅 0.24 m，2D standoff（0.55*reach=0.33）与其碰撞体前伸/
    # 锚点内缩叠加后必然超限——与 Warehouse 阶段18 v10-v14 的 Carter 结论
    # 同型（见 STAGE18_REVIEW）。双 M20 reach 0.8、根 z≈0.527，standoff
    # 0.44 m 在 0.75 m 水平预算内；容量 8+8 kg ≥ 扶手椅 14 kg（每侧 7）。
    "carriers": {"house_ground_1": "support_left", "house_ground_3": "support_right"},
    "parking_preferred_xy_m": [5., 15.],
    "approach_max_steps": 6000,
    "align_max_steps": 1500,
    "carry_max_steps": 1500,
    "withdrawal_max_steps": 3000,
    "approach_speed_mps": .35,
    "carry_speed_mps": .15,
    "withdrawal_speed_mps": .25,
    "stop_radius_m": .15,
    "corridor_width_m": .9,
    "withdrawal_clearance_margin_m": .1,
}
# 接近段路径点（住户动线中心线）：南载具沿走廊直上；北载具走廊→客厅
# 西侧绕开扶手椅（椅 AABB x∈[4.55,5.45]、y∈[13.07,13.93]，绕行列 x=3.95
# 与椅西缘留 ~0.16 m 间隙）再北上东折。最终 standoff 点由锚点外法向现场
# 计算后追加。
APPROACH_WAYPOINTS = {
    "house_ground_1": [[3.5, 3.5], [3.5, 9.5], [3.5, 12.3], [4.5, 12.3]],
    "house_ground_3": [[4.0, 3.5], [3.5, 3.5], [3.5, 9.5], [3.5, 12.3],
                       [4.3, 12.3], [3.85, 13.5], [3.85, 15.4], [5.0, 15.4]],
}
INTERVENTION_SCOPE = "author_side_furniture_intervention_and_full_ground_withdrawal_between_snapshots"


def _sources():
    paths = [Path(__file__), Path(__file__).with_name("native_adapters.py"),
             Path(__file__).with_name("live_path.py"),
             Path(__file__).with_name("intervention_plan.py"),
             ROOT / "demo/benchmark/reference_domain_providers.py",
             ROOT / "demo/benchmark/goal_native_attachment.py",
             ROOT / "demo/benchmark/goal_attachment_probe.py"]
    return {str(p): file_sha256(p) for p in paths}


def prepare(root):
    request = physical.prepare_probe(root, scene="modern_house_nvd", steps=SETTLE_STEPS, device="cpu")
    payload = {"run_id": request["run_id"], "request_sha256": canonical_sha256(request),
               "source_files": _sources(), "points": POINTS, "settle_steps": SETTLE_STEPS,
               "between_steps": BETWEEN_STEPS, "scoreable": False,
               "intervention_scope": INTERVENTION_SCOPE, "intervention": dict(INTERVENTION)}
    physical.write_json(Path(root) / "backend_request.json", payload)
    return payload


def load(root, *, rebuild):
    request, deployment, selection = physical.load_probe(root, rebuild=rebuild)
    payload = json.loads((root / "backend_request.json").read_text())
    if (payload["run_id"] != request["run_id"]
            or payload["request_sha256"] != canonical_sha256(request)
            or payload["source_files"] != _sources()
            or payload["points"] != POINTS
            or payload["settle_steps"] != SETTLE_STEPS or payload["between_steps"] != BETWEEN_STEPS
            or payload["scoreable"] is not False
            or payload["intervention_scope"] != INTERVENTION_SCOPE
            or payload["intervention"] != INTERVENTION):
        raise ValueError("House diagnostic input/source changed")
    return request, deployment, selection, payload


def _root_state(session, runtime_id):
    return session.base_env.scene.articulations[runtime_id].data.root_state_w[0].detach().cpu().tolist()


def _yaw(state):
    w, qx, qy, qz = state[3:7]
    return math.atan2(2*(w*qz+qx*qy), 1-2*(qy*qy+qz*qz))


def _seek_command(state, target_xy, speed_limit, stop_radius, yaw_limit=.3):
    """Turn-then-drive world-frame seek; no lateral velocity command.

    v8 实测：直接给含横向分量的机体系指令时 M20 RL 策略几乎不动或漂移
    （两载具 1800 步位移 <2 m 且方向错误）。warehouse 单机 probe 的前向
    为主指令可正常驱动，故这里先原地转向（|误差|>0.35 rad 时纯 yaw），
    对齐后只给前向 + 小 yaw 修正。
    """
    dx, dy = float(target_xy[0])-state[0], float(target_xy[1])-state[1]
    distance = math.hypot(dx, dy)
    if distance <= stop_radius:
        return [0., 0., 0.]
    heading = math.atan2(dy, dx)
    yaw = _yaw(state)
    error = math.atan2(math.sin(heading-yaw), math.cos(heading-yaw))
    if abs(error) > .35:
        return [0., 0., max(-yaw_limit*1.5, min(yaw_limit*1.5, 1.5*error))]
    forward = speed_limit*max(.2, math.cos(error))
    return [forward, 0., max(-yaw_limit, min(yaw_limit, 1.5*error))]


def _align_command(state, target_yaw, tolerance=.12, yaw_limit=.3):
    """In-place yaw alignment; returns None when already aligned."""
    error = math.atan2(math.sin(target_yaw-_yaw(state)), math.cos(target_yaw-_yaw(state)))
    if abs(error) <= tolerance:
        return None
    return [0., 0., max(-yaw_limit*1.5, min(yaw_limit*1.5, 1.5*error))]


def worker(root):
    descriptor = int(os.environ["EAI_GOAL_PHYSICAL_WORKER_LOCK_FD"])
    held, expected = os.fstat(descriptor), (ROOT / "tmp/goal_physical_probe.lock").stat()
    if (held.st_dev, held.st_ino) != (expected.st_dev, expected.st_ino):
        raise ValueError("not the canonical scene lock")
    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    request, deployment, selection, payload = load(root, rebuild=False)
    if "pxr" in sys.modules:
        raise RuntimeError("Kit must own USD imports")
    from simulator import SimulatorLaunchConfig, open_simulator_session
    report = {"run_id": request["run_id"], "backend_request_sha256": canonical_sha256(payload),
              "status": "starting", "scoreable": False, "business_outcomes_validated": False,
              "scope": "two_native_snapshots_around_author_intervention"}

    def save():
        physical.write_json(root / "backend_report.json", report)

    def configure(cfg):
        from demo.benchmark.goal_native_objects import configure_goal_objects
        cfg.sim.enable_scene_query_support = True
        cfg.sim.render_interval = max(1, round(.1 / cfg.sim.dt))
        cfg.episode_length_s = 3600.
        report["object_application"] = configure_goal_objects(cfg, deployment)
        save()

    config = SimulatorLaunchConfig(env=selection["task_name"], resolved_env_name="EAI-Interactive-v0",
        selection_data=selection, num_envs=1, device="cpu", seed=7, headless=True,
        enable_ros_bridge_extension=False, disable_orsus_ros_env=True, env_cfg_hook=configure)
    save()
    with open_simulator_session(config) as session:
        try:
            import torch
            import omni.usd
            import omni.replicator.core as rep
            from pxr import UsdGeom
            from EAI.scenario_spec.geometry import provider_map
            from demo.benchmark import goal_native_attachment as attachment
            from demo.benchmark.goal_attachment_probe import LiveBodies, anchor_quality
            from .intervention_plan import (
                corridor_distance, intervention_record_complete, new_intervention_record,
                plan_furniture_parking, plan_withdrawal_targets, record_intervention_event)
            from .native_adapters import probe_native_backends
            stage = omni.usd.get_context().get_stage()
            aerial = {r["runtime_id"]: session.base_env.scene.articulations[r["runtime_id"]].data.root_pos_w.clone()
                      for r in deployment["robot_instances"] if r["public_declaration"]["mobility"] == "aerial"}
            agent = next(iter(aerial))
            cameras = [str(p.GetPath()) for p in stage.Traverse()
                       if p.IsA(UsdGeom.Camera) and str(p.GetPath()).startswith(f"/World/envs/env_0/{agent}/")]
            if len(cameras) != 1:
                raise ValueError(f"ambiguous native robot camera: {cameras}")
            render_product = rep.create.render_product(cameras[0], (320, 240))
            annotator = rep.AnnotatorRegistry.get_annotator("rgb")
            annotator.attach([render_product])

            class NativeCamera:
                def __init__(self, label):
                    self.label = label

                def capture_camera_frame(self, name):
                    if name != agent:
                        raise ValueError("camera producer mismatch")
                    import numpy as np
                    from PIL import Image
                    pixels = annotator.get_data().copy()
                    if pixels.size:
                        np.save(root / f"{self.label}_frame.npy", pixels, allow_pickle=False)
                        Image.fromarray(pixels).save(root / f"{self.label}_frame.png")
                    return pixels

            def step(count, ground_velocity=None):
                for _ in range(count):
                    if not session.simulation_app.is_running():
                        raise RuntimeError("application stopped")
                    actions = {a: torch.zeros((1, 3), device=session.device) for a in session.possible_agents}
                    for a, goal in aerial.items():
                        session.base_env.set_command(a, "goal_position", goal)
                        actions[a] = goal.clone()
                    if ground_velocity:
                        for runtime_id, velocity in ground_velocity.items():
                            actions[runtime_id][0] = torch.tensor(velocity, device=session.device)
                    session.env.step(actions)
                for _ in range(12):
                    session.base_env.sim.render()

            def clock():
                return {"physics_step": int(session.base_env.sim.current_time_step_index),
                        "time_s": float(session.base_env.sim.current_time)}

            def record_event(name, **extra):
                return record_intervention_event(report["intervention"], name,
                                                 **clock(), extra=extra or None)

            occupancy = provider_map(scene_key="modern_house_nvd")
            map_sha = occupancy.sha256
            item_codes = {r["public_id"]: r["public_id"] for r in deployment["object_instances"]}
            report["camera"] = {"prim_path": cameras[0], "producer": agent, "resolution": [320, 240],
                                "source": "replicator.rgb.native_robot_camera", "ros_enabled": False}
            report["runtime_source_files"] = physical.runtime_sources(stage)

            rows = {r["public_id"]: r for r in deployment["robot_instances"]}
            carriers = dict(payload["intervention"]["carriers"])
            # 双 M20 载具（v6 起）：house_ground_1=m20_1 + house_ground_3=m20_2。
            # v5 实测 Carter（reach 0.6、根 z 0）在扶手椅 support 锚点高度下
            # 水平预算仅 0.24 m，2D standoff 0.33 m 即超限——与 Warehouse 阶段18
            # v10-v14 的 Carter 结论同型。双 M20 reach 0.8（水平预算 0.75）、
            # 容量 8+8 ≥ 14 kg。
            if ({rows[member]["type"] for member in carriers} != {"m20"}
                    or set(carriers) != {"house_ground_1", "house_ground_3"}):
                raise ValueError("reviewed carrier identities changed")
            ground = [r for r in deployment["robot_instances"]
                      if r["public_declaration"]["mobility"] == "ground"]
            obj = next(r for r in deployment["object_instances"]
                       if r["public_id"] == payload["intervention"]["carry_object_id"])
            parking = plan_furniture_parking(
                occupancy, payload["intervention"]["parking_preferred_xy_m"],
                obj["physical_properties"]["size_m"])
            if not parking["admitted"]:
                raise ValueError("no occupancy-admitted furniture parking cell")
            report["intervention"] = new_intervention_record(
                {"scope": INTERVENTION_SCOPE, "carriers": carriers,
                 "object_id": obj["public_id"],
                 "mode_id": payload["intervention"]["carry_mode_id"],
                 "parking": parking,
                 "corridor_points_m": [p[:2] for p in POINTS],
                 "corridor_width_m": payload["intervention"]["corridor_width_m"]},
                map_sha)
            report["intervention"]["occupancy_sha256"]["before_probe"] = map_sha
            save()

            def probe(phase, output_name, closeout_time_s=None):
                result = probe_native_backends(session=session, stage=stage, deployment=deployment,
                    occupancy=occupancy, sensor_manager=NativeCamera(phase), agent=agent,
                    episode_id=request["run_id"], item_codes=item_codes, max_range_m=10.,
                    points=payload["points"], output_path=root / output_name, phase=phase,
                    closeout_time_s=closeout_time_s)
                return {"status": result["status"], "sha256": file_sha256(root / output_name),
                        "blockers": result["blockers"], "phase": result["phase"]}

            # --- Phase 1: undisturbed before snapshot -----------------------
            step(payload["settle_steps"])
            report["before"] = probe("before", "before.json")
            record_event("before_probe_completed", probe_status=report["before"]["status"])
            save()

            # --- Phase 2: author intervention --------------------------------
            live = LiveBodies(session.base_env.scene, deployment)
            live.step = int(session.base_env.sim.current_time_step_index)
            grasp = {point["id"]: point["position_m"]
                     for point in obj["physical_properties"]["grasp_points"]}

            def object_state():
                return live(live.bindings["objects"][obj["public_id"]]["body_path"])

            def anchor_xy(member, pose):
                delta = attachment._rotate(pose["rotation_wxyz"], grasp[carriers[member]])
                return [pose["position_m"][0]+delta[0], pose["position_m"][1]+delta[1]]

            def drive_ground(targets, speed, budget):
                stop = payload["intervention"]["stop_radius_m"]
                for _ in range(budget):
                    if not session.simulation_app.is_running():
                        raise RuntimeError("application stopped")
                    commands = {rows[member]["runtime_id"]: _seek_command(
                        _root_state(session, rows[member]["runtime_id"]), target, speed, stop)
                        for member, target in targets.items()}
                    step(1, commands)
                    live.step = int(session.base_env.sim.current_time_step_index)
                    if all(math.dist(_root_state(session, rows[member]["runtime_id"])[:2], target) <= stop
                           for member, target in targets.items()):
                        return True
                return False

            def drive_waypoints(chains, speed, budget):
                """Per-member waypoint following with the turn-then-drive seek."""
                remaining = {member: list(chain) for member, chain in chains.items()}
                stop = payload["intervention"]["stop_radius_m"]
                for _ in range(budget):
                    if not session.simulation_app.is_running():
                        raise RuntimeError("application stopped")
                    commands = {}
                    for member, chain in remaining.items():
                        if not chain:
                            continue
                        state = _root_state(session, rows[member]["runtime_id"])
                        if math.dist(state[:2], chain[0]) <= stop:
                            chain.pop(0)
                            continue
                        commands[rows[member]["runtime_id"]] = _seek_command(
                            state, chain[0], speed, stop)
                    if commands:
                        step(1, commands)
                        live.step = int(session.base_env.sim.current_time_step_index)
                    if all(not chain for chain in remaining.values()):
                        return True
                return all(not chain for chain in remaining.values())

            # Stand each carrier off along its grasp outward normal, inside reach.
            pose = object_state()
            approach = {}
            for member in carriers:
                ax, ay = anchor_xy(member, pose)
                dx, dy = ax-pose["position_m"][0], ay-pose["position_m"][1]
                norm = math.hypot(dx, dy) or 1.
                reach = rows[member]["physical_profile"]["reach_m"]
                approach[member] = [ax + dx/norm*.55*reach, ay + dy/norm*.55*reach]
            # 走廊路径点（住户动线中心线，0.9 m 宽）绕开墙体与扶手椅；南载具
            # m20_1 沿走廊直上，北载具 m20_2 走廊→客厅西侧绕椅北上。路径点
            # 仅用于接近段；attach 前载具原地转向对齐搬运方向（此时物体未
            # 连接，转身自由），搬运段两载具纯前向驱动。
            first, second = sorted(carriers)
            approach_chains = {
                member: list(APPROACH_WAYPOINTS.get(member, ())) + [approach[member]]
                for member in carriers}
            # 走廊仅 0.9 m 宽，两台 M20（各 0.55 m 宽）串行通过：先发者走完
            # 全程并完成对齐，后发者再进入走廊。绕行列 x=3.85 与先发者的
            # 南侧停驻位（x≈5±0.45）保持 ≥0.25 m 间隙。
            report["intervention"]["approach_trail"] = []
            def _trail(tag):
                report["intervention"]["approach_trail"].append({
                    "tag": tag,
                    "positions": {m: [round(float(v), 3) for v in
                                      _root_state(session, rows[m]["runtime_id"])[:3]]
                                  for m in carriers}})
            approach_reached = (drive_waypoints(
                {first: approach_chains[first]},
                payload["intervention"]["approach_speed_mps"],
                payload["intervention"]["approach_max_steps"]) and drive_waypoints(
                {second: approach_chains[second]},
                payload["intervention"]["approach_speed_mps"],
                payload["intervention"]["approach_max_steps"]))
            _trail("approach_done")
            parking_xy = parking["position_xy_m"]
            carry_yaw = math.atan2(parking_xy[1]-pose["position_m"][1],
                                   parking_xy[0]-pose["position_m"][0])
            for _ in range(payload["intervention"]["align_max_steps"]):
                if not session.simulation_app.is_running():
                    raise RuntimeError("application stopped")
                commands = {}
                for member in carriers:
                    cmd = _align_command(
                        _root_state(session, rows[member]["runtime_id"]), carry_yaw)
                    if cmd is not None:
                        commands[rows[member]["runtime_id"]] = cmd
                if not commands:
                    break
                step(1, commands)
                live.step = int(session.base_env.sim.current_time_step_index)

            # 证据优先：先落盘接近结果（含实测锚点距离与当时根位姿），再尝试
            # attach——attach 失败时报告仍保留定位数据，不吞掉诊断信息。
            record_event("carriers_approach_completed", approach_targets=approach,
                         anchors_within_reach=approach_reached,
                         anchor_distances_m={
                             member: round(math.dist(
                                 _root_state(session, rows[member]["runtime_id"])[:2],
                                 anchor_xy(member, object_state())), 4)
                             for member in carriers},
                         carrier_root_xy_m={
                             member: [round(float(v), 3) for v in
                                      _root_state(session, rows[member]["runtime_id"])[:2]]
                             for member in carriers})
            prepared = attachment.prepare_attachment(
                stage, deployment["public_task"], object_id=obj["public_id"],
                mode_id=payload["intervention"]["carry_mode_id"], carriers=carriers,
                active_member_ids=set(live.bindings["members"]),
                body_bindings=live.bindings, pose_provider=live)
            record_event("attachment_prepared",
                         physics_step_of_samples=prepared["preflight"]["physics_step"])
            receipt = attachment.commit_attachment(
                stage, prepared, active_member_ids=set(live.bindings["members"]), pose_provider=live)
            record_event("attachment_committed",
                         joint_paths=[row["prim_path"] for row in receipt["joint_readback"]])
            report["intervention"]["attachment_receipt"] = receipt
            save()

            parking_xy = parking["position_xy_m"]
            record_event("carry_started", object_position_m=list(object_state()["position_m"]))
            carry_speed = payload["intervention"]["carry_speed_mps"]
            carry_steps = 0
            for _ in range(payload["intervention"]["carry_max_steps"]):
                if not session.simulation_app.is_running():
                    raise RuntimeError("application stopped")
                # 双载具已对齐搬运方向：纯前向 + 微小航向保持。不给横向指令，
                # 也不让任一载具在连接期间大幅转向（会经固定关节与另一侧
                # 载具对抗）。
                commands = {}
                for member in carriers:
                    yaw = _yaw(_root_state(session, rows[member]["runtime_id"]))
                    err = math.atan2(math.sin(carry_yaw-yaw), math.cos(carry_yaw-yaw))
                    commands[rows[member]["runtime_id"]] = [
                        carry_speed, 0.0, max(-.1, min(.1, 1.5*err))]
                step(1, commands)
                live.step = int(session.base_env.sim.current_time_step_index)
                carry_steps += 1
                if math.dist(object_state()["position_m"][:2], parking_xy) <= payload["intervention"]["stop_radius_m"]:
                    break
            final_object = object_state()
            record_event("carry_completed", steps=carry_steps,
                         object_position_m=list(final_object["position_m"]),
                         reached_target=math.dist(final_object["position_m"][:2], parking_xy)
                         <= payload["intervention"]["stop_radius_m"],
                         anchor_quality=anchor_quality(receipt, live.snapshot()["bodies"]))
            detach = attachment.detach_attachment(stage, receipt)
            record_event("attachment_detached", removed_joint_paths=detach["removed_joint_paths"])
            report["intervention"]["detach_receipt"] = detach
            save()

            # Withdraw every ground robot, including the idle third member.
            withdrawal = plan_withdrawal_targets(
                occupancy, POINTS,
                [{"public_id": r["public_id"], "position_xy": _root_state(session, r["runtime_id"])[:2],
                  "body_size_xy": r["physical_profile"]["body_size_m"]} for r in ground],
                parking_xy=parking_xy, width_m=payload["intervention"]["corridor_width_m"],
                clearance_margin_m=payload["intervention"]["withdrawal_clearance_margin_m"])
            if not withdrawal["admitted"]:
                raise ValueError("no occupancy-admitted withdrawal cell for a ground robot")
            report["intervention"]["plan"]["withdrawal"] = withdrawal
            record_event("withdrawal_started")
            targets_by_id = {row["public_id"]: row["position_xy_m"] for row in withdrawal["targets"]}
            drive_ground({r["public_id"]: targets_by_id[r["public_id"]] for r in ground},
                         payload["intervention"]["withdrawal_speed_mps"],
                         payload["intervention"]["withdrawal_max_steps"])
            required = {row["public_id"]: row["required_corridor_clearance_m"]
                        for row in withdrawal["targets"]}
            clearances = {r["public_id"]: round(corridor_distance(
                _root_state(session, rows[r["public_id"]]["runtime_id"])[:2], POINTS,
                width_m=payload["intervention"]["corridor_width_m"]), 4) for r in ground}
            record_event("withdrawal_completed", corridor_clearance_m=clearances,
                         required_clearance_m=required,
                         all_clear=all(clearances[r["public_id"]] >= required[r["public_id"]]
                                       for r in ground))
            save()

            if occupancy.sha256 != map_sha:
                raise ValueError("occupancy map changed during the intervention")
            report["intervention"]["occupancy_sha256"]["after_closeout"] = occupancy.sha256
            report["intervention"]["occupancy_sha256"]["asserted_unchanged"] = True
            closeout_time_s = float(session.base_env.sim.current_time)
            record_event("closeout_recorded", node_id="hall_release",
                         time_basis="native_simulation_time")
            if not intervention_record_complete(report["intervention"]):
                raise ValueError("intervention timing record incomplete")
            save()

            # --- Phase 3: after snapshot, downstream of the closeout --------
            step(payload["between_steps"])
            report["after"] = probe("after", "after.json", closeout_time_s=closeout_time_s)
            save()
            for path, digest in report["runtime_source_files"].items():
                if file_sha256(path) != digest:
                    raise ValueError(f"runtime source changed: {path}")
            report["status"] = ("diagnostic_completed" if all(report[k]["status"] == "measured"
                                for k in ("before", "after")) else "blocked")
            annotator.detach([render_product.path])
        except BaseException as exc:
            import traceback
            report.update(status="blocked", blocker=f"{type(exc).__name__}: {exc}", traceback=traceback.format_exc())
        finally:
            save()  # Kit can terminate during session cleanup.
    return report


def execute(root, timeout_s):
    _, _, _, payload = load(root, rebuild=True)
    if not math.isfinite(timeout_s) or timeout_s <= 0:
        raise ValueError("positive timeout required")
    with (ROOT / "tmp/goal_physical_probe.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        command = [sys.executable, "-m", "demo.modern_house_benchmark.native_probe", "--worker", "--output-root", str(root)]
        with (root / "backend_launch.json").open("x") as stream:
            json.dump({"command": command, "request_sha256": canonical_sha256(payload), "timeout_s": timeout_s}, stream)
        started, timed_out = time.monotonic(), False
        with (root / "isaac.log").open("x") as log:
            process = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                start_new_session=True, pass_fds=(lock.fileno(),),
                env={**os.environ, "EAI_GOAL_PHYSICAL_WORKER_LOCK_FD": str(lock.fileno())})
            def stop():
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGTERM)
                try:
                    return process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    return process.wait(timeout=10)
            def interrupted(signum, frame):
                raise KeyboardInterrupt("House diagnostic terminated")
            previous = signal.signal(signal.SIGTERM, interrupted)
            try:
                code = process.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                timed_out, code = True, stop()
            except BaseException:
                stop()
                raise
            finally:
                signal.signal(signal.SIGTERM, previous)
        path = root / "backend_report.json"
        report = json.loads(path.read_text()) if path.exists() else {}
        result = {"run_id": payload["run_id"], "returncode": code, "timed_out": timed_out,
                  "wall_time_s": time.monotonic()-started, "scoreable": False,
                  "report_sha256": file_sha256(path) if path.exists() else None,
                  "diagnostic_completed": code == 0 and not timed_out and report.get("status") == "diagnostic_completed"
                    and report.get("backend_request_sha256") == canonical_sha256(payload)}
        physical.write_json(root / "process_result.json", result)
        load(root, rebuild=False)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--execute", action="store_true")
    mode.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--timeout-s", type=float, default=1200.)
    args = parser.parse_args()
    root = args.output_root.absolute()
    result = worker(root) if args.worker else execute(root, args.timeout_s) if args.execute else prepare(root)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if not args.execute or result["diagnostic_completed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
