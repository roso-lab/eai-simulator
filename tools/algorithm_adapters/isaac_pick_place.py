"""Bounded native-decision -> TaskRunner -> EAI UR5 pick/place integration.

Run with the Isaac Lab interpreter. Native policy inference uses its isolated
interpreter, and its exact assignment dispatches the existing PickPlaceExecutor
host. One robot/one supplied task is an integration case, not a multi-robot
benchmark score. Execution uses idealized follow-proxy, not contact grasp.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from algorithm.common_coordination.contract import AlgorithmCondition, Assignment, ExecutionFeedback, FailureCode
from algorithm.system.coordination.native_runtime import sha256_file
from task.contracts import AlgorithmDecision, EaiTaskPackage, TaskRunRequest
from task.runner import TaskRunner


def native_decision_from_report(report: dict) -> AlgorithmDecision:
    """Deserialize a successful native receipt without replacing idle or errors."""
    if report.get("status") != "native_decision_returned":
        raise RuntimeError("native adapter did not return a decision; no skill dispatched")
    result = report["result"]
    decision = AlgorithmDecision(
        AlgorithmCondition(**result["condition"]),
        tuple(Assignment(**item) for item in result["assignments"]),
        tuple(result["unassigned_tasks"]), report["provenance"],
    )
    if len(decision.assignments) != 1 or decision.unassigned_tasks:
        raise RuntimeError("native policy did not dispatch the supported task; no skill substituted")
    return decision


def case_document() -> dict:
    return {
        "task_id": "native-ur5-pick-place-integration", "scene_id": "isolated-ur5-workbench",
        "robots": [{"robot_id": "ur5_1", "capabilities": ["manipulate"],
                    "position": [0.0, 0.0, 0.0], "metadata": {"yaw_rad": 0.0}}],
        "tasks": [{"task_id": "move_carton", "description": "Pick and place the workbench carton",
                   "required_capabilities": ["manipulate"], "target_ref": "/World/Object",
                   "metadata": {"position": [0.45, 0.0, 0.0], "duration_s": 30.0,
                                "target_position": [0.425, 0.01, 0.475],
                                "pick_place": {"object_position_world_m": [0.45, 0.0, 0.475],
                                               "approach_degrees": 15.0, "timeout_s": 100.0,
                                               "mode": "cartesian_pose"}}}],
        "metadata": {"position_source": "declared_spawn_xy_projection",
                     "sadcher": {"start_position": [0.0, 0.0, 0.0],
                                 "end_position": [0.0, 0.0, 0.0]},
                     "evidence_scope": "one_robot_one_skill_integration"},
    }


def physical_options(task: EaiTaskPackage, execution_path: Path) -> SimpleNamespace:
    """Validate the bounded fixture and translate explicit task physical parameters."""
    if len(task.graph.tasks) != 1 or len(task.robots) != 1 or task.robots[0].capability.robot_id != "ur5_1":
        raise ValueError("this bounded host supports exactly one supplied task and ur5_1")
    node = task.graph.tasks[0]
    if node.target_ref != "/World/Object":
        raise ValueError("bounded fixture requires target_ref=/World/Object")
    robot = task.robots[0]
    if robot.position != (0.0, 0.0, 0.0) or robot.metadata.get("yaw_rad", 0.0) != 0.0:
        raise ValueError("bounded UR5 pedestal fixture requires zero ground-projected robot pose")
    config = node.metadata.get("pick_place", {})

    def vector(value, name):
        if not isinstance(value, (list, tuple)) or len(value) != 3 or any(
            isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in value
        ):
            raise ValueError(f"{name} requires three finite coordinates")
        return list(value)

    position = vector(config.get("object_position_world_m"), "pick_place.object_position_world_m")
    target = vector(node.metadata.get("target_position"), "target_position")
    if position[2] <= .075 or abs(target[2] - position[2]) > 1e-9:
        raise ValueError("bounded workbench requires supported equal-height pick/place positions")
    declared_position = node.metadata.get("position")
    if declared_position is not None and vector(declared_position, "position")[:2] != position[:2]:
        raise ValueError("declared task position XY disagrees with physical object position")
    angle, timeout = config.get("approach_degrees"), config.get("timeout_s")
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
           for v in (angle, timeout)) or timeout <= 0:
        raise ValueError("explicit finite approach_degrees and positive timeout_s required")
    mode = config.get("mode")
    if mode != "cartesian_pose":
        raise ValueError("explicit supported pick_place.mode required")
    return SimpleNamespace(arm="ur5", backend="arm", mode=mode, trials=1,
                           approach_degrees=angle, timeout_s=timeout, gui=False, video=None,
                           object_position=position, place_offset=None, place_position=target,
                           report=execution_path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--algorithm", default="sadcher")
    parser.add_argument("--algorithm-python", type=Path, required=True)
    parser.add_argument("--task", type=Path, help="Optional explicit supported one-task package")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--native-timeout-s", type=float, default=120.0,
                        help="Wall-clock budget for isolated native inference")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not math.isfinite(args.native_timeout_s) or args.native_timeout_s <= 0:
        parser.error("--native-timeout-s must be positive and finite")
    if args.output.exists() and any(args.output.iterdir()):
        parser.error("--output must be a new or empty evidence directory")
    args.output.mkdir(parents=True, exist_ok=True)
    task_path = args.task or args.output / "task.json"
    if args.task is None:
        task_path.write_text(json.dumps(case_document(), indent=2) + "\n")
    config = args.config or args.output / "adapter_config.json"
    if args.config is None:
        if args.algorithm != "sadcher":
            raise ValueError("non-SADCHER algorithms require an explicit adapter config")
        config.write_text(json.dumps({"skill_names": ["manipulate", "inspect", "transport"]}) + "\n")
    task = EaiTaskPackage.from_json(task_path)
    if len(task.graph.tasks) != 1 or len(task.robots) != 1 or task.robots[0].capability.robot_id != "ur5_1":
        raise ValueError("this bounded host supports exactly one supplied task and ur5_1")
    raw_path = args.output / "native_decision.json"
    execution_path = args.output / "physical_execution.json"
    launchers = []
    native_report = {}
    physical_report = {}
    native_feedback_adapter = None
    native_feedback = None
    options = physical_options(task, execution_path)
    started = time.monotonic()
    input_hashes = {"task_sha256": sha256_file(task_path),
                    "adapter_config_sha256": sha256_file(config)}
    physical_execution_started = False

    def provider(package, robots):
        nonlocal native_report, native_feedback_adapter
        if args.algorithm == "bt_capabilities":
            # This ROS-free client stays alive for the whole Isaac task. The
            # decision-only CLI intentionally closes its native session.
            from benchmark.native_algorithms import build_native_adapter
            native_feedback_adapter = build_native_adapter(
                args.algorithm, source_root=args.source_root, **json.loads(config.read_text()))
            decision = native_feedback_adapter(package, robots)
            native_report = {
                "schema": "heterocoord.native_adapter_run.v1", "algorithm": args.algorithm,
                "status": "native_decision_returned", "evidence_scope": "native_decision_only",
                "physical_success": None, "simulation_acceptance_passed": False,
                "result": {"condition": decision.condition.to_dict(),
                           "assignments": [vars(item) for item in decision.assignments],
                           "unassigned_tasks": list(decision.unassigned_tasks)},
                "provenance": dict(decision.provenance),
            }
            raw_path.write_text(json.dumps(native_report, indent=2) + "\n")
            if len(decision.assignments) != 1 or decision.unassigned_tasks:
                raise RuntimeError("native policy did not dispatch the supported task; no skill substituted")
            return decision
        command = [str(args.algorithm_python), "-m", "tools.algorithm_adapters.run",
                   "--algorithm", args.algorithm, "--task", str(task_path.resolve()),
                   "--config", str(config.resolve()), "--report", str(raw_path.resolve())]
        if args.source_root:
            command.extend(["--source-root", str(args.source_root.resolve())])
        try:
            process = subprocess.run(
                command, cwd=ROOT, capture_output=True, text=True,
                timeout=args.native_timeout_s,
            )
        except subprocess.TimeoutExpired as exc:
            def decoded(value):
                return value.decode(errors="replace") if isinstance(value, bytes) else value or ""
            (args.output / "native_process.log").write_text(decoded(exc.stdout) + decoded(exc.stderr))
            native_report = {"status": "native_timeout", "timeout_s": args.native_timeout_s}
            raise RuntimeError("native inference exceeded wall-clock budget; no skill dispatched") from None
        (args.output / "native_process.log").write_text(process.stdout + process.stderr)
        if raw_path.is_file():
            native_report = json.loads(raw_path.read_text())
        if process.returncode:
            raise RuntimeError(f"native adapter failed (exit {process.returncode}); no skill dispatched")
        return native_decision_from_report(native_report)

    class Executor:
        def execute(self, assignment, node, *, scene):
            nonlocal physical_report, native_feedback, physical_execution_started
            if assignment.robot_id != "ur5_1" or assignment.task_id != node.task_id:
                raise ValueError("native decision cannot be bound to this physical host")
            if (sha256_file(task_path) != input_hashes["task_sha256"]
                    or sha256_file(config) != input_hashes["adapter_config_sha256"]):
                raise RuntimeError("input changed after native inference; no skill dispatched")
            import os
            os.environ.setdefault("OMNI_KIT_ACCEPT_EULA", "YES")
            from isaaclab.app import AppLauncher
            launcher = AppLauncher(headless=True, device="cpu")
            launchers.append(launcher)
            from tools.validation import unified_pick_place_smoke as host
            options.source_sha256 = {name: sha256_file(ROOT / name) for name in host.SOURCE_FILES}
            physical_execution_started = True
            physical_report = host.run(options)
            execution_path.write_text(json.dumps(physical_report, indent=2) + "\n")
            passed = physical_report.get("passed") is True
            feedback = ExecutionFeedback(node.task_id, assignment.robot_id,
                                     "success" if passed else "failure",
                                     FailureCode.NONE if passed else FailureCode.EXECUTION_FAILURE,
                                     world_changes={"physical_receipt_sha256": sha256_file(execution_path)})
            if native_feedback_adapter is not None:
                native_feedback = native_feedback_adapter.feedback(feedback, succeeded=passed)
                (args.output / "native_feedback.json").write_text(json.dumps(native_feedback, indent=2) + "\n")
                if native_feedback["tree_state"] != ("SUCCEEDED" if passed else "FAILED"):
                    raise RuntimeError("native BT did not consume physical completion feedback")
            return feedback

    class Evaluator:
        def evaluate(self, **_):
            # The existing host evaluates measured lift, release, final pose,
            # collision trajectory and actuator stability independently of plan.
            passed = physical_report.get("passed") is True
            return {"status": "success" if passed else "failure",
                    "evidence_scope": "idealized_follow_proxy_pick_place; not contact grasp",
                    "receipt_sha256": sha256_file(execution_path),
                    "benchmark_scores_available": False}

    report = {"schema": "heterocoord.native_physical_integration.v1", "algorithm": args.algorithm,
              "started_at_utc": datetime.now(timezone.utc).isoformat(),
              "passed": False, "benchmark_scores_available": False,
              "native_timeout_s": args.native_timeout_s, **input_hashes}
    try:
        result = TaskRunner(executor=Executor(), evaluator=Evaluator()).run(TaskRunRequest(task), provider)
        report.update(passed=result.verdict["status"] == "success", result=result.to_dict(),
                      native_provenance=native_report.get("provenance"))
    except Exception as exc:
        import traceback
        traceback.print_exc()
        report.update(error_type=type(exc).__name__, error=str(exc))
    finally:
        if native_feedback_adapter is not None:
            try:
                native_feedback_adapter.close()
            except Exception as exc:
                report.update(passed=False, native_close_error=str(exc))
        report["native_feedback"] = native_feedback
        report["native_status"] = native_report.get("status")
        report["native_provenance"] = native_report.get("provenance", native_report.get("native_source_provenance"))
        report["native_decision_receipt_sha256"] = sha256_file(raw_path) if raw_path.is_file() else None
        report["physical_execution_performed"] = physical_execution_started
        report["physical_execution_completed"] = bool(physical_report)
        report["physical_receipt_sha256"] = sha256_file(execution_path) if execution_path.is_file() else None
        report["elapsed_wall_s"] = time.monotonic() - started
        report["input_files_unchanged"] = (
            input_hashes["task_sha256"] == sha256_file(task_path)
            and input_hashes["adapter_config_sha256"] == sha256_file(config)
        )
        if not report["input_files_unchanged"]:
            report.update(passed=False, input_integrity_error="input changed during execution")
        report["physical_parameters_from_task"] = {
            "object_position_world_m": options.object_position,
            "place_position_world_m": options.place_position,
            "approach_degrees": options.approach_degrees,
            "timeout_s": options.timeout_s, "mode": options.mode,
        }
        (args.output / "integration.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
        print(json.dumps({"passed": report["passed"], "output": str(args.output)}), flush=True)
        for launcher in launchers:
            import omni.kit.app
            omni.kit.app.get_app().post_quit(0 if report["passed"] else 1)
            launcher.app.close(wait_for_replicator=False, skip_cleanup=True)
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
