"""Author diagnostic connecting a goal plan to one real navigation invocation.

This is a deployment probe, not a business task or a solver. Its independent
region/route names are bound to the maintained Warehouse navigation smoke.
It never assigns those coordinates to the unbound order-task regions. Only the
selected Carter is instantiated; other public candidates are not exercised.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from .goal_driven_contract import GOAL_PLAN_VERSION, build_goal_participant_api, validate_goal_plan
from .goal_driven_tasks import build_goal_task_package
from .provenance import REPO_ROOT, canonical_sha256, file_sha256


SCENARIO = REPO_ROOT / "demo/warehouse_benchmark/scenarios/navigation_smoke.json"
_FILES = {"public_task.json", "participant_api.json", "author_plan.json",
          "scenario.json", "diagnostic_binding.json"}


def _write(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        stream.write("\n")


def _probe_inputs():
    scenario = json.loads(SCENARIO.read_text(encoding="utf-8"))
    robots = scenario["robots"]
    nodes = scenario["phase_graph"]["nodes"]
    if (scenario["scene_key"] != "warehouse_nvd" or len(robots) != 1
            or robots[0]["type"] != "carter" or len(nodes) != 1
            or nodes[0]["skill_key"] != "navigate" or nodes[0]["prerequisites"]
            or nodes[0]["preferred_agent"] != robots[0]["id"]
            or scenario["composition"]["entities"]):
        raise ValueError("probe requires the maintained single-Carter navigation fixture")
    target = next(row for row in scenario["composition"]["targets"]
                  if row["id"] == nodes[0]["target_ref"])
    start = robots[0]["spawn_pose"]["position"]
    goal = target["position"]
    agent = "warehouse_ground_2"
    public = deepcopy(build_goal_task_package("warehouse_nvd")["public_task"])
    public["task_id"] = "WAREHOUSE-NAV-DEPLOYMENT-PROBE-01"
    public["mission"] = {
        "scene_key": "warehouse_nvd",
        "goal": "检查仓库地面导航接口到现场到达证据的部署链路。",
        "context": "作者专用单机器人诊断；不执行订单任务、不评价异构团队或业务完成。",
    }
    public["required_outcomes"] = [
        {"id": "probe_region_arrival", "description": "选定机器人进入诊断目标区域。",
         "acceptance": "使用仿真步后的实际位置检查目标半径。",
         "evidence_expectations": ["post_step_region_arrival"]},
        {"id": "probe_trace_available", "description": "诊断保留可追踪的位置与输入身份。",
         "acceptance": "记录仿真时间、位置轨迹及运行输入哈希；不代替碰撞或业务证据。",
         "evidence_expectations": ["pose_trace", "execution_provenance"]},
    ]
    public["public_world"] = {
        "regions": [
            {"id": "navigation_probe_start", "kind": "diagnostic_start",
             "public_description": f"诊断起点，world米坐标 {start}。",
             "access_modes": ["ground_navigation"], "resource_capacity": 1},
            {"id": "navigation_probe_goal", "kind": "diagnostic_goal",
             "public_description": f"诊断目标，world米坐标 {goal}，半径 {target['radius_m']} m。",
             "access_modes": ["ground_navigation"], "resource_capacity": 1},
        ],
        "objects": [], "vertical_links": [],
        "routes": [{"id": "navigation_probe_route", "from_region_id": "navigation_probe_start",
                    "to_region_id": "navigation_probe_goal", "access_modes": ["ground_navigation"],
                    "length_m": math.dist(start[:2], goal[:2]), "clearance_m": [1.5, 2.2],
                    "max_step_height_m": 0.0, "resource_id": "navigation_probe_lane", "capacity": 1}],
        "public_notes": "坐标来自维护的导航诊断；长度为两端XY直线距离。净空和机器人尺寸仍为实验声明，真实路径另由膨胀占据图准入。这里的区域不映射订单任务区域。",
    }
    public["business_constraints"] = [
        "本实例只用于公开计划、后端派发和位置读回的接入诊断，不进入业务题包或评分集。",
        "只部署选定的Carter；其余候选未被实例化或验证。禁止运输、抓取或事故服务。",
        "在60秒仿真时窗内到达；接纳计划与任务完成是不同状态，必须使用当前运行的绑定回执。",
    ]
    public["budgets"]["simulation_s"] = 60.0
    public["budgets"]["max_active_agents"] = 1
    for row in public["candidate_robot_pool"]:
        if row["id"] == agent:
            row["public_capabilities"] = ["ground_navigation"]
            row["interfaces"] = []
            row["physical_profile"]["configuration_id"] = "carter_z1_navigation_probe_not_payload_calibration"
            row["physical_profile"]["effective_payload_kg"] = 0.0
            row["physical_profile"]["reach_m"] = 0.0
            row["physical_profile"]["grasp_interfaces"] = []
    plan = {
        "schema": "eai.benchmark.goal_plan", "version": GOAL_PLAN_VERSION,
        "plan_id": "author_navigation_probe", "assigned_team": [agent],
        "tasks": [{"task_id": "probe_navigation", "skill": "ground_navigation",
                   "arguments": {"region_id": "navigation_probe_goal", "route_ids": ["navigation_probe_route"]},
                   "prerequisites": [], "required_capabilities": ["ground_navigation"],
                   "assigned_agents": [agent], "evidence_refs": [],
                   "execution": {"start_s": 0.0, "duration_s": 60.0}}],
    }
    api = build_goal_participant_api(public)
    validate_goal_plan(plan, task_spec=public,
                       context={"current_time_s": 0.0, "robot_regions": {agent: "navigation_probe_start"},
                                "object_states": {}})
    binding = {
        "schema": "eai.benchmark.navigation_probe_binding", "version": 1,
        "visibility": "author_only", "scope": "single_navigation_deployment_diagnostic",
        "source_scenario": str(SCENARIO), "source_scenario_sha256": file_sha256(SCENARIO),
        "public_task_sha256": canonical_sha256(public), "plan_sha256": canonical_sha256(plan),
        "scene_key": "warehouse_nvd", "agent_id": agent, "runtime_agent_id": robots[0]["id"],
        "task_id": "probe_navigation", "runtime_node_id": nodes[0]["id"],
        "start_position_m": start, "goal_position_m": goal, "goal_radius_m": target["radius_m"],
        "whole_task_scene_binding_validated": False, "robot_envelope_measured": False,
        "business_outcomes_validated": False, "scoreable": False,
    }
    return {"public_task.json": public, "participant_api.json": api, "author_plan.json": plan,
            "scenario.json": scenario, "diagnostic_binding.json": binding}


def prepare_navigation_probe(output_root):
    """Create a new author-owned diagnostic directory without replacing inputs."""
    payloads = _probe_inputs()
    root = Path(output_root).absolute()
    root.mkdir(parents=True, exist_ok=False)
    for name, value in payloads.items():
        _write(root / name, value)
    return {"output_root": str(root), "diagnostic_only": True, "scoreable": False}


def load_navigation_probe(output_root):
    """Rebuild the reviewed mapping; stale sources or a swapped target fail."""
    root = Path(output_root).absolute()
    if any(path.is_symlink() for path in (root, *root.parents)):
        raise ValueError("probe directory cannot traverse a symlink")
    expected = _probe_inputs()
    loaded = {}
    for name in _FILES:
        path = root / name
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"probe requires a regular file: {name}")
        loaded[name] = json.loads(path.read_text(encoding="utf-8"))
        if canonical_sha256(loaded[name]) != canonical_sha256(expected[name]):
            raise ValueError(f"probe input differs from its reviewed source binding: {name}")
    return loaded


def execute_navigation_probe(output_root, *, device="cuda:0", process_timeout_s=480.0):
    """Dispatch once, run the real backend, and bind its readback to the ledger.

    This host is trusted. The participant never supplies receipts, launch options,
    snapshots or report locations. No old report can stand in for this launch.
    The initial symbolic position comes from the frozen spawn declaration; the
    backend auditor independently checks actual pose readback and arrival.
    """
    from .goal_session import GoalSession
    from .navigation_deployment_review import audit_navigation_deployment

    if (isinstance(process_timeout_s, bool) or not isinstance(process_timeout_s, (int, float))
            or not math.isfinite(process_timeout_s) or process_timeout_s <= 0):
        raise ValueError("process_timeout_s must be positive and finite")
    if device not in {"cpu", "cuda:0"}:
        raise ValueError("probe device must be cpu or cuda:0")
    root = Path(output_root).absolute()
    inputs = load_navigation_probe(root)
    binding = inputs["diagnostic_binding.json"]
    # Navigation probe inputs do not expose a request file; bind the diagnostic
    # identity to the reviewed binding and output root for this one run.
    episode_id = f"navigation-probe:{canonical_sha256({'binding': binding, 'root': str(root)})[:32]}"
    session = GoalSession(inputs["public_task.json"], inputs["participant_api.json"],
                          episode_id=episode_id)
    session.update_runtime_state(0.0, {binding["agent_id"]: "navigation_probe_start"}, {})
    actions = [
        ("probe_select", "select_team", {"agent_ids": [binding["agent_id"]]}),
        ("probe_submit", "submit_plan", {"plan": inputs["author_plan.json"]}),
        ("probe_dispatch", "dispatch_skill", {"task_id": binding["task_id"], "evidence_refs": []}),
    ]
    for action_id, kind, payload in actions:
        session.submit_action({"schema": "eai.benchmark.goal_action", "version": 1,
                               "action_id": action_id, "submitted_at_s": 0.0,
                               "type": kind, "payload": payload})
    run = root / "run"
    run.mkdir(exist_ok=False)
    _write(run / "ledger_before_launch.json", session.report())
    report_path = run / "navigation_report.json"
    command = [sys.executable, "-m", "demo.warehouse_benchmark.main", "--scenario", str(root / "scenario.json"),
               "--device", device, "--headless", "--steps", "1800", "--wall-timeout-s", "120",
               "--report", str(report_path)]
    launch = {"command": command, "dispatched_action_id": "probe_dispatch",
              "episode_id": episode_id,
              "diagnostic_binding_sha256": canonical_sha256(binding),
              "input_sha256": {name: file_sha256(root / name) for name in sorted(_FILES)},
              "process_timeout_s": process_timeout_s, "initial_state_basis": "frozen_spawn_declaration"}
    _write(run / "launch_request.json", launch)
    started = time.monotonic()
    timed_out = False
    with (run / "isaac.log").open("x", encoding="utf-8") as log:
        process = subprocess.Popen(command, cwd=REPO_ROOT, stdout=log, stderr=subprocess.STDOUT,
                                   start_new_session=True)
        try:
            returncode = process.wait(timeout=process_timeout_s)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(process.pid, signal.SIGTERM)
            try:
                returncode = process.wait(timeout=10.0)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                returncode = process.wait(timeout=10.0)
    process_result = {"returncode": returncode, "timed_out": timed_out,
                      "wall_time_s": time.monotonic() - started}
    _write(run / "process_result.json", process_result)
    # Detect input mutation during startup/execution before using any receipt.
    if any(file_sha256(root / name) != digest for name, digest in launch["input_sha256"].items()):
        raise ValueError("navigation probe inputs changed during backend execution")
    try:
        review = audit_navigation_deployment(report_path, scenario_path=root / "scenario.json",
                                             expected_scene=binding["scene_key"])
    except ValueError as exc:
        review = {"navigation_validated": False, "status": "rejected", "error": str(exc),
                  "physical_control_validated": False, "scoreable": False}
    _write(run / "deployment_review.json", review)
    completion = {"accepted": False, "status": "pending", "reason": "backend evidence not admitted"}
    if returncode == 0 and not timed_out and review["navigation_validated"]:
        raw = json.loads(report_path.read_text(encoding="utf-8"))
        completion_time = float(raw["scheduling"]["time_s"])
        receipt = {
            "backend_id": "warehouse_navigation_probe",
            "receipt_ref": "sha256:" + file_sha256(run / "deployment_review.json"),
            "task_id": binding["task_id"], "action_id": "probe_dispatch", "status": "succeeded",
            "completed_at_s": completion_time, "episode_id": episode_id,
            "evidence_refs": ["sha256:" + file_sha256(report_path),
                              "sha256:" + file_sha256(run / "launch_request.json")],
        }
        completion = session.complete_skill(binding["task_id"], time_s=completion_time,
                                            status="succeeded", receipt=receipt)
        session.update_runtime_state(completion_time, {binding["agent_id"]: "navigation_probe_goal"}, {})
    _write(run / "ledger_after_review.json", session.report())
    result = {"schema": "eai.benchmark.goal_navigation_probe", "version": 1,
              "scope": "author_navigation_diagnostic_not_business_task",
              "episode_id": episode_id, "process": process_result, "completion": completion,
              "navigation_validated": review["navigation_validated"],
              "physical_control_validated": False, "business_outcomes_validated": False,
              "whole_task_scene_binding_validated": False, "scoreable": False,
              "output_root": str(root)}
    _write(run / "summary.json", result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--execute", action="store_true", help="execute an already prepared probe once")
    parser.add_argument("--device", choices=["cpu", "cuda:0"], default="cuda:0")
    parser.add_argument("--process-timeout-s", type=float, default=480.0)
    args = parser.parse_args(argv)
    if args.execute:
        result = execute_navigation_probe(args.output_root, device=args.device,
                                          process_timeout_s=args.process_timeout_s)
    else:
        result = prepare_navigation_probe(args.output_root)
    print(json.dumps(result, indent=2))
    return int(args.execute and not result["completion"]["accepted"])


if __name__ == "__main__":
    raise SystemExit(main())
