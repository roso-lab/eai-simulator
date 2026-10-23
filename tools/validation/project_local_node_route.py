"""Real project-local Factory task through the canonical symbolic navigation API.

The authored L route and endpoint contract are fixed before launch. This is
integration evidence, not certified catalog or paper-score evidence. Each run
requires a new output directory and retains failures before native shutdown.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
from pathlib import Path
import traceback
from contextlib import contextmanager

from tools.project_runtime import PROJECT_ROOT, activate_project_runtime, gpu_runtime_report, provenance_report
from tools.validation.project_local_command_loop import _state


CONTRACT = {
    "task_id": "factory-carter-authored-L-v1", "scene_key": "factory",
    "robot_id": "carter_1", "target_ref": "node:finish",
    "points_m": [[-3., 0., 0.], [-2.4, 0., 0.], [-2.4, .6, 0.]],
    "arrival_radius_m": .12, "maximum_final_speed_m_s": .04,
    "timeout_s": 25., "settle_steps": 40, "max_steps": 1500,
    "cruise_speed_m_s": .18, "max_yaw_rate_rad_s": .6,
    "required_route": ["node:start", "node:corner", "node:finish"],
    "evaluation": "measured ordered node visits, endpoint and stopped motion",
    "scope": "canonical_navigation_integration_only",
}


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")
    temporary.replace(path)


@contextmanager
def session_close_marker_env(path):
    previous = os.environ.get("HETEROCOORD_SESSION_CLOSE_MARKER")
    os.environ["HETEROCOORD_SESSION_CLOSE_MARKER"] = str(path)
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("HETEROCOORD_SESSION_CLOSE_MARKER", None)
        else:
            os.environ["HETEROCOORD_SESSION_CLOSE_MARKER"] = previous


@contextmanager
def persist_failure(output, report):
    """Seal failures before the enclosing native session can terminate Python."""
    try:
        yield
    except BaseException as exc:
        report.update(status="failed", passed=False, error_type=type(exc).__name__,
                      error=str(exc), traceback=traceback.format_exc())
        write(output / "result.json", report)
        raise


def evaluate(samples, contract=CONTRACT):
    """Independent evaluator uses measured states, not executor success flags."""
    if not samples:
        return {"passed": False, "reason": "no_measured_states"}
    points, radius = contract["points_m"], contract["arrival_radius_m"]
    cursor, visits = 0, []
    for sample in samples:
        if cursor < len(points) and math.dist(sample["position_m"][:2], points[cursor][:2]) <= radius:
            visits.append({"node": contract["required_route"][cursor], "tick": sample["step"]})
            cursor += 1
    final = samples[-1]
    error = math.dist(final["position_m"][:2], points[-1][:2])
    stopped = final["speed_m_s"] <= contract["maximum_final_speed_m_s"]
    monotonic = all(a["simulation_time_s"] < b["simulation_time_s"] and a["step"] < b["step"]
                    for a, b in zip(samples, samples[1:]))
    return {"passed": cursor == len(points) and error <= radius and stopped and monotonic,
            "ordered_visits": visits, "endpoint_error_m": error,
            "final_speed_m_s": final["speed_m_s"], "stopped": stopped,
            "clock_strictly_increasing": monotonic}


def run(output, *, metered=False, device="cuda:0"):
    activate_project_runtime()
    output = output.resolve()
    if not output.is_relative_to(PROJECT_ROOT):
        raise ValueError("output must remain in project")
    output.mkdir(parents=True, exist_ok=False)
    write(output / "contract.json", CONTRACT)
    if metered:
        from benchmark.calibration_runtime_v3 import MEASUREMENT_PLAN, MeteredNavigationProtocol
        write(output / "measurement_plan.json", {**MEASUREMENT_PLAN, "contract": CONTRACT})
    gpu = gpu_runtime_report(device)
    if device.startswith("cuda"):
        # Factory normally protects itself with a CPU PhysX fallback. Formal
        # GPU runs opt into the registered GPU path and then fail closed if
        # Isaac still changes the actual device.
        os.environ["EAI_FACTORY_GPU_PHYSICS"] = "1"
    report = {"schema": "heterocoord.node_route_integration.v1", "contract": CONTRACT,
              "provenance": provenance_report(), "status": "starting", "passed": False,
              "requested_physics_device": device, "gpu": gpu,
              "paper_score_eligible": False, "physical_skill_qualified": False,
              "checkpoint_replay_qualified": False, "samples": [], "commands": [],
              "session_close_observed": False}
    write(output / "result.json", report)
    selection_path = PROJECT_ROOT / "source/EAI_hmrs/EAI_hmrs/envs/EAI-Factory-Z1-v0.json"
    try:
        from simulator import SimulatorLaunchConfig, open_simulator_session
        from algorithm.skills.navigation import FrozenNavigationTopology, NavigationPose, ground_navigation_profile
        from task.executors.navigation import NavigationExecutor
        from task.executors.navigation_backends import PlanarVelocityNavigationAdapter
        selection = json.loads(selection_path.read_text())
        config = SimulatorLaunchConfig(
            env="EAI-Factory-Z1-v0", resolved_env_name="EAI-Factory-Z1-v0",
            selection_data=selection, device=device, seed=17 if metered else 0, headless=True,
            enable_ros_bridge_extension=False, disable_orsus_ros_env=True,
            app_launcher_args={"headless": True, "device": device})
        close_marker = output / "session_close.json"
        with session_close_marker_env(close_marker), open_simulator_session(config) as session, persist_failure(output, report):
            import torch
            import omni.usd
            env = session.base_env
            actual_device = str(session.device)
            if actual_device != gpu["actual_device"]:
                raise RuntimeError(
                    f"Isaac runtime changed requested GPU {gpu['actual_device']} to {actual_device}")
            report["actual_physics_device"] = actual_device
            paths = {selection_path, Path(__file__).resolve(), output / "contract.json",
                     PROJECT_ROOT / "tools/project_runtime.py", PROJECT_ROOT / "simulator.py",
                     PROJECT_ROOT / "tools/validation/project_local_command_loop.py"}
            if metered:
                paths.update({output / "measurement_plan.json",
                              PROJECT_ROOT / "benchmark/calibration_runtime_v3.py",
                              PROJECT_ROOT / "benchmark/transport_v3.py",
                              PROJECT_ROOT / "tools/validation/project_local_resource_calibration.py"})
            # Snapshot actual loaded project layers; external runtime layers are
            # separately disclosed, never represented as local assets.
            outside = []
            for layer in omni.usd.get_context().get_stage().GetUsedLayers():
                if layer.realPath:
                    path = Path(layer.realPath).resolve()
                    if path.is_relative_to(PROJECT_ROOT):
                        paths.add(path)
                    else:
                        outside.append(str(path))
            for relative in ("source/EAI_assets/EAI_assets/scene/factory.py",
                             "source/EAI_assets/EAI_assets/scene/factory_runtime.py",
                             "source/EAI_assets/EAI_assets/controller/traditional/carter_diff/carter_diff.py",
                             "source/EAI_hmrs/EAI_hmrs/env_builder.py",
                             "task/executors/navigation.py", "task/executors/navigation_backends.py",
                             "algorithm/skills/navigation/node_route.py"):
                paths.add(PROJECT_ROOT / relative)
            inventory = {str(p.relative_to(PROJECT_ROOT)): digest(p) for p in sorted(paths)}
            # Keep executable inputs as well as hashes; later fixes must not
            # make the exact runner used by an earlier receipt unrecoverable.
            for path in paths:
                if path.suffix in {".py", ".json"} and path != output / "contract.json":
                    saved = output / "input_sources" / path.relative_to(PROJECT_ROOT)
                    saved.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(path, saved)
            stamps = {p: (p.stat().st_size, p.stat().st_mtime_ns, p.stat().st_ctime_ns) for p in paths}
            report.update(source_inventory=inventory, external_runtime_layers=outside)
            if outside:
                raise ValueError(f"loaded USD layers escape project: {outside}")
            def source_reader():
                for p, stamp in stamps.items():
                    stat = p.stat()
                    if (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns) != stamp:
                        raise ValueError(f"frozen source changed during execution: {p}")
                return CONTRACT["scene_key"], inventory
            ids = CONTRACT["required_route"]
            document = {"schema": "eai.navigation.topology", "version": 1,
                        "scene_key": "factory", "source": inventory,
                        "coordinate_frame": "scene_world", "meters_per_unit": 1., "up_axis": "Z",
                        "nodes": [{"id": name, "position_xyz": point, "target_refs": []}
                                  for name, point in zip(ids, CONTRACT["points_m"])],
                        "edges": [{"id": f"edge:{i}", "from": ids[i], "to": ids[i+1], "directed": True}
                                  for i in range(2)]}
            topology = FrozenNavigationTopology(document, scene_key="factory", source_digests=inventory)
            write(output / "topology.json", document)
            report.update(topology_digest=topology.product_digest, route_graph_digest=topology.route_graph_digest)
            robot = CONTRACT["robot_id"]
            zero = torch.zeros((1, 3), device=env.device)
            actions = {robot: zero}
            for _ in range(CONTRACT["settle_steps"]):
                env.step(actions)
            report["samples"].append(_state(session, robot))
            def pose_reader():
                state = _state(session, robot)
                return NavigationPose(tuple(state["position_m"]), state["yaw_rad"])
            def seq():
                return int(env.common_step_counter)
            def now():
                return float(env.sim.current_time)
            def velocity_reader():
                state = _state(session, robot)
                return (*state["linear_velocity_m_s"], *state["angular_velocity_rad_s"])
            def sink(command):
                if command.command_type != "base_velocity":
                    raise ValueError("unexpected controller command")
                value = command.payload["velocity"]
                actions[robot] = torch.tensor([value], device=env.device)
                env.set_command(robot, "base_velocity", actions[robot])
                report["commands"].append({"tick": seq(), "value": list(value)})
            backend = PlanarVelocityNavigationAdapter(
                robot, pose_reader=pose_reader, command_sink=sink, sequence_reader=seq,
                time_reader=now, velocity_reader=velocity_reader,
                profile=ground_navigation_profile(arrival_radius_m=CONTRACT["arrival_radius_m"]),
                cruise_speed_m_s=CONTRACT["cruise_speed_m_s"], max_yaw_rate_rad_s=CONTRACT["max_yaw_rate_rad_s"])
            executor = NavigationExecutor(topology=topology, simulation_time=now,
                source_reader=source_reader, snapshot_validator=lambda s, motion: s == seq(),
                timeout_s=CONTRACT["timeout_s"])
            executor.register_adapter(backend)
            def request(intent):
                return {"request_id": f"request/{seq()}", "task_id": CONTRACT["task_id"],
                        "skill_id": "eai.navigation.node_route", "skill_version": "simulator.skill.v1",
                        "robot_id": robot, "target_ref": CONTRACT["target_ref"],
                        "parameters": {"intent": intent, "world_state_seq": seq(),
                                       "route_graph_digest": topology.route_graph_digest}}
            report["submitted_request"] = request("start")
            protocol = MeteredNavigationProtocol(CONTRACT) if metered else None
            episode_start = now()
            dispatched = False
            if not metered:
                executor.dispatch(request("start"))
                dispatched = True
            try:
                response = None
                for tick in range(CONTRACT["max_steps"]):
                    first_dispatch = tick == 0 and not metered
                    if protocol is not None:
                        authorized = protocol.advance(tick)
                        if protocol.terminal_feedback is not None or now() - episode_start >= CONTRACT["timeout_s"]:
                            break
                        if authorized and not dispatched:
                            executor.dispatch(request("start"))
                            dispatched = first_dispatch = True
                    active = dispatched and (response is None or response.state["status"] == "pending")
                    if active:
                        executor.prepare(request("start" if first_dispatch else "continue"))
                    env.step(actions)
                    if dispatched and executor.needs_host_tick(CONTRACT["task_id"]):
                        response = executor.after_step(CONTRACT["task_id"])
                    report["samples"].append(_state(session, robot))
                    if protocol is not None:
                        protocol.observe(tick + 1, report["samples"][-1], None if response is None else response.state)
                    if not metered and response.state["status"] != "pending":
                        break
                if protocol is not None:
                    report["resource_trace"] = protocol.result(start_time_s=episode_start,
                        end_time_s=now(), physics_step_s=float(env.step_dt))
                    report["resource_trace"]["horizon_s"] = CONTRACT["timeout_s"]
                    report["resource_trace"]["capped_physical_elapsed_s"] = min(
                        now() - episode_start, CONTRACT["timeout_s"])
                    report["measured_task_evaluation"] = evaluate(report["samples"])
                    report["measured_navigation_state"] = None if response is None else dict(response.state)
                if response is None:
                    raise RuntimeError("no navigation command was delivered within the horizon")
                if response.state["status"] == "pending":
                    executor.prepare(request("stop"))
                for _ in range(250):
                    if not executor.needs_host_tick(CONTRACT["task_id"]):
                        break
                    env.step(actions)
                    response = executor.after_step(CONTRACT["task_id"])
                    report["samples"].append(_state(session, robot))
                report["navigation_state"] = dict(response.state)
                report["execution_evidence"] = dict(response.private_evidence)
            finally:
                env.set_command(robot, "base_velocity", zero)
                for _ in range(30):
                    env.step({robot: zero})
                    report["samples"].append(_state(session, robot))
            report["evaluation"] = evaluate(report["samples"])
            report["inputs_unchanged"] = all(digest(p) == inventory[str(p.relative_to(PROJECT_ROOT))] for p in paths)
            state = report["navigation_state"]
            report["passed"] = (state["status"] == "completed" and state["stop_confirmed"]
                and not state["quarantined"] and report["evaluation"]["passed"] and report["inputs_unchanged"])
            if metered:
                trace = report["resource_trace"]
                report["passed"] = bool(report["passed"] and report["measured_task_evaluation"]["passed"]
                    and trace["terminal_feedback"] and trace["terminal_feedback"]["status"] == "completed"
                    and trace["terminal_feedback"]["stop_confirmed"] is True
                    and report["measured_navigation_state"]["stop_confirmed"] is True
                    and trace["traffic_complete"] and trace["accounting_consistent"]
                    and trace["physical_elapsed_s"] <= CONTRACT["timeout_s"])
            report["status"] = "completed"
            write(output / "result.json", report)
            print(json.dumps({k: report[k] for k in ("passed", "evaluation", "navigation_state")}), flush=True)
        try:
            marker = json.loads(close_marker.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            marker = {}
        report["session_close_receipt"] = marker
        report["session_close_observed"] = bool(
            marker.get("cleanup_callbacks_complete") is True
            and marker.get("app_close_requested") is True
            and marker.get("status") in {"app_close_requested", "app_close_returned"}
        )
        write(output / "result.json", report)
    except BaseException as exc:
        report.update(status="failed", passed=False, error=str(exc), traceback=traceback.format_exc())
        write(output / "result.json", report)
        raise
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--metered", action="store_true")
    args = parser.parse_args()
    raise SystemExit(run(args.output, metered=args.metered, device=args.device))
