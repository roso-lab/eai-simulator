"""Serial Isaac probe for one real Warehouse carton carry and dock receipt.

This is a diagnostic witness runner. It reuses the reviewed native-object
deployment and fixed-point implementation, but owns the episode loop and dock
receiver here. It never reports business success or scoreability. The paired
crate case is deliberately reported as a remaining gap.
"""

from __future__ import annotations

from contextlib import contextmanager
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

from demo.benchmark import goal_attachment_probe as attachment
from demo.benchmark import goal_native_attachment as native_attachment
from demo.benchmark.goal_native_objects import configure_goal_objects, read_goal_objects
from demo.benchmark.provenance import canonical_sha256, file_sha256
from .dock_receiver import EpisodeDockReceiver, dock_geometry, envelope_bounds
from .native_carry import shift_lift_target


ROOT = Path(__file__).resolve().parents[2]
OBJECT = "order_carton_01"
MEMBER = "warehouse_ground_3"
M20_RUNTIME_ID = "m20_2"
M20_COMMAND_NAME = "base_velocity"
M20_CRUISE_SPEED_MPS = 0.55
M20_APPROACH_RADIUS_M = 1.0
M20_APPROACH_SPEED_MPS = 0.35
M20_YAW_RATE_RPS = 0.6
M20_STOP_RADIUS_M = 0.15
TRANSPORT_PROGRESS_EPSILON_M = 0.02
TRANSPORT_STALL_WINDOW_STEPS = 300
OUTPUT_SCHEMA = "eai.benchmark.warehouse_native_witness_probe_v1"


def _write(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n")


@contextmanager
def _archive_report(output_root, report):
    try:
        yield
    except BaseException as exc:
        report.update(status="blocked", blocker=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        # Save before Kit teardown, which may exit the process directly.
        _write(output_root / "warehouse_witness_report.json", report)


def _target(deployment):
    x0, y0, x1, y1 = dock_geometry(deployment)
    return [(x0+x1)/2, (y0+y1)/2, 0.15]


def _witness_request(request):
    sources = [Path(__file__), Path(__file__).with_name("dock_receiver.py"),
               Path(__file__).with_name("native_carry.py")]
    return {"schema": OUTPUT_SCHEMA + ".request", "run_id": request["run_id"],
            "attachment_request_sha256": canonical_sha256(request), "seed": 17,
            "settle_steps": 240, "max_transport_steps": 2400, "release_steps": 60,
            "source_files": {str(path.resolve()): file_sha256(path) for path in sources},
            "lift_height_m": .12, "lift_speed_mps": .05,
            "scope": "ideal_connector_lift_transport_release_microdiagnostic", "scoreable": False}


def prepare_probe(output_root: Path, *, gui: bool = False, device: str = "cuda:0") -> dict:
    """Prepare the source-bound attachment deployment without starting Isaac."""
    request = attachment.prepare_probe(output_root, gui=gui, device=device)
    witness = _witness_request(request)
    _write(Path(output_root) / "witness_request.json", witness)
    return witness


def load_probe(output_root, *, rebuild=True):
    request, deployment, selection = attachment.load_probe(output_root, rebuild=rebuild)
    witness = json.loads((Path(output_root) / "witness_request.json").read_text())
    if witness != _witness_request(request):
        raise ValueError("Warehouse witness source/request changed")
    return request, deployment, selection


def _commands(session, *, velocity=None, aerial_goals=None):
    """Build external controller commands, not low-level joint actions.

    ``M20_ROUGH_RSL_CFG`` consumes a body-frame ``base_velocity`` command and
    computes the 16-dimensional leg/wheel action through its RSL policy.  The
    three values below must therefore stay outside the low-level action
    contract; ``MultiRobotDirectEnv`` performs that conversion on ``step``.
    """
    import torch

    actions = {agent: torch.zeros((1, 3), device=session.device)
               for agent in session.possible_agents}
    if velocity is not None:
        if M20_RUNTIME_ID not in actions:
            raise ValueError(f"required M20 runtime agent is absent: {M20_RUNTIME_ID}")
        command = torch.tensor(velocity, dtype=torch.float32, device=session.device)
        if tuple(command.shape) != (3,):
            raise ValueError("M20 base_velocity command must be [vx, vy, wz]")
        setter = getattr(getattr(session, "base_env", None), "set_command", None)
        if not callable(setter):
            raise ValueError("M20 runtime has no episode command setter")
        setter(M20_RUNTIME_ID, M20_COMMAND_NAME, command.unsqueeze(0))
        actions[M20_RUNTIME_ID][0] = command
    for agent, goal in (aerial_goals or {}).items():
        session.base_env.set_command(agent, "goal_position", goal)
        actions[agent] = goal.clone()
    return actions


def _m20_controller_contract(session) -> dict:
    """Verify that the live M20 path accepts velocity commands and emits joints."""
    if M20_RUNTIME_ID not in session.possible_agents:
        raise ValueError(f"required M20 runtime agent is absent: {M20_RUNTIME_ID}")
    configs = getattr(session.base_env, "_controller_configs", None)
    cfg = configs.get(M20_RUNTIME_ID) if isinstance(configs, dict) else None
    if cfg is None:
        raise ValueError("M20 controller configuration is unavailable")
    command_name = getattr(cfg, "command_name", None)
    if command_name != M20_COMMAND_NAME:
        raise ValueError(
            f"M20 controller command_name must be {M20_COMMAND_NAME!r}, got {command_name!r}")
    if not callable(getattr(cfg, "compute_action_from_command_func", None)):
        raise ValueError("M20 controller has no command-to-action hook")
    if not callable(getattr(cfg, "apply_action_func", None)):
        raise ValueError("M20 controller has no low-level action hook")
    return {"runtime_id": M20_RUNTIME_ID, "command_name": command_name,
            "command_frame": "M20_base", "computed_action_dim": 16,
            "conversion": "base_velocity_to_rsl_16d_joint_targets"}


def _m20_action_readback(session) -> dict:
    """Read the action produced by the controller after one real env.step."""
    actions = getattr(session.base_env, "_last_computed_actions", None)
    action = actions.get(M20_RUNTIME_ID) if isinstance(actions, dict) else None
    if action is None or len(action.shape) != 2 or action.shape[-1] != 16:
        shape = tuple(getattr(action, "shape", ()))
        raise ValueError(f"M20 controller did not produce a 16D action after step: {shape}")
    return {"computed_action_dim": int(action.shape[-1]),
            "computed_action": action[0].detach().cpu().tolist()}


def _step(session, live, *, velocity=None, aerial_goals=None):
    session.env.step(_commands(session, velocity=velocity, aerial_goals=aerial_goals))
    live.step = int(session.base_env.sim.current_time_step_index)
    return float(session.base_env.sim.current_time)


def _lift(session, stage, live, receipt, *, direction, active, aerial_goals, samples):
    count = math.ceil(.12 / min(.002, .05 * float(session.base_env.step_dt)))
    for _ in range(count):
        receipt = shift_lift_target(stage, receipt, delta_z_m=direction * .12 / count,
                                    pose_provider=live, active_member_ids=active)
        now = _step(session, live, aerial_goals=aerial_goals)
        samples.append({"phase": "lift" if direction > 0 else "lower", "time_s": now,
                        "object": live(live.bindings["objects"][OBJECT]["body_path"]),
                        "carrier": live(live.bindings["members"][MEMBER]["body_path"]),
                        "joint_readback": receipt["joint_readback"],
                        "actuation_receipt_sha256": receipt["sha256"],
                        "target_offset_m": receipt["lift_actuation"]["target_offset_m"]})
    for _ in range(30):
        _step(session, live, aerial_goals=aerial_goals)
    return receipt


def _carry_velocity(position, target, rotation_wxyz=(1., 0., 0., 0.)):
    dx, dy = target[0] - position[0], target[1] - position[1]
    distance = math.hypot(dx, dy)
    if distance <= M20_STOP_RADIUS_M:
        return [0.0, 0.0, 0.0]
    # Use the public ground-navigation baseline as a command trial. Stage14's
    # low-speed observation concerned Go2, not a calibrated M20 threshold.
    if distance >= M20_APPROACH_RADIUS_M:
        speed = M20_CRUISE_SPEED_MPS
    else:
        speed = max(M20_APPROACH_SPEED_MPS,
                    M20_CRUISE_SPEED_MPS * distance / M20_APPROACH_RADIUS_M)
    w, x, y, z = rotation_wxyz
    yaw = math.atan2(2 * (w*z + x*y), 1 - 2 * (y*y + z*z))
    error = math.atan2(math.sin(math.atan2(dy, dx) - yaw),
                       math.cos(math.atan2(dy, dx) - yaw))
    # Wheel-assisted locomotion must turn toward the transport direction.
    # A lateral-only command can stall with the carton fixed in front.
    forward = speed * max(0., math.cos(error)) if abs(error) < .7 else 0.
    return [forward, 0., max(-M20_YAW_RATE_RPS, min(M20_YAW_RATE_RPS, 1.5*error))]


def run_probe(output_root: Path) -> dict:
    """Fresh Kit worker; never rebuild OpenUSD inputs before AppLauncher."""
    attachment._worker_lock()
    request, deployment, selection = load_probe(output_root, rebuild=False)
    if any(name == "pxr" or name.startswith("pxr.") for name in sys.modules):
        raise RuntimeError("OpenUSD must not be imported before AppLauncher")
    launch = json.loads((output_root / "launch.json").read_text())
    if launch.get("witness_request_sha256") != canonical_sha256(_witness_request(request)):
        raise ValueError("Warehouse worker launch contract changed")
    return _run_locked(output_root, request, deployment, selection)


def _run_locked(output_root: Path, request: dict, deployment: dict, selection: dict) -> dict:
    from simulator import SimulatorLaunchConfig, open_simulator_session

    report = {
        "schema": OUTPUT_SCHEMA, "version": 1, "run_id": request["run_id"],
        "scene_key": "warehouse_nvd", "request_sha256": canonical_sha256(request),
        "witness_request_sha256": canonical_sha256(_witness_request(request)),
        "status": "starting", "scoreable": False,
        "physical_task_execution_validated": False, "business_outcomes_validated": False,
        "query_support_configured": False, "samples": [], "lift_samples": [],
        "query_evidence": {
            "status": "unknown",
            "reason": "carried_clearance_not_collected; DirectGPU_cpu_scene_queries_can_be_stale",
            "diagnostic_device": "cpu",
        },
        "paired_crate": {"status": "blocked", "reason": "paired_fixed_point_physics_not_executed"},
    }

    def configure(env_cfg):
        # This must happen before scene creation. Headless default False makes
        # empty overlap/ray results indistinguishable from free space.
        env_cfg.sim.enable_scene_query_support = True
        env_cfg.episode_length_s = 3600.0
        env_cfg.sim.render_interval = max(1, round(0.1 / env_cfg.sim.dt))
        report["query_support_configured"] = env_cfg.sim.enable_scene_query_support is True
        report["object_application"] = configure_goal_objects(env_cfg, deployment)

    config = SimulatorLaunchConfig(
        env=selection["task_name"], resolved_env_name="EAI-Interactive-v0",
        selection_data=selection, num_envs=1, device=request["device"], seed=17,
        headless=not request["gui"], enable_ros_bridge_extension=False,
        disable_orsus_ros_env=True, env_cfg_hook=configure,
    )
    _write(output_root / "native_witness_start.json", report)
    try:
        with open_simulator_session(config) as session, _archive_report(output_root, report):
            import omni.usd

            stage = omni.usd.get_context().get_stage()
            live = attachment.LiveBodies(session.base_env.scene, deployment)
            live.step = int(session.base_env.sim.current_time_step_index)
            report["m20_control_interface"] = _m20_controller_contract(session)
            report["m20_control_profile"] = {
                "command_name": M20_COMMAND_NAME,
                "command_frame": "M20_base",
                "cruise_speed_mps": M20_CRUISE_SPEED_MPS,
                "approach_radius_m": M20_APPROACH_RADIUS_M,
                "approach_speed_mps": M20_APPROACH_SPEED_MPS,
                "yaw_rate_limit_rps": M20_YAW_RATE_RPS,
                "stop_radius_m": M20_STOP_RADIUS_M,
                "navigation_mode": "turn_then_forward_no_lateral_command",
                "progress_epsilon_m": TRANSPORT_PROGRESS_EPSILON_M,
                "stall_window_steps": TRANSPORT_STALL_WINDOW_STEPS,
            }
            report["actual_device"] = session.device
            if session.device == "cpu":
                report["query_evidence"] = {
                    "status": "diagnostic_candidate",
                    "reason": "CPU_PhysX_selected_but_no_clearance_query_is_asserted_by_this_probe",
                    "diagnostic_device": "cpu",
                }
            else:
                report["query_evidence"] = {
                    "status": "unknown",
                    "reason": "DirectGPU_cpu_scene_queries_can_read_stale_actor_poses",
                    "diagnostic_device": "cpu",
                }
            report["initial_objects"] = read_goal_objects(stage, session.base_env.scene, deployment)
            before = live(live.bindings["objects"][OBJECT]["body_path"])
            target = _target(deployment)
            active = set(live.bindings["members"])
            aerial_goals = {
                row["runtime_id"]: session.base_env.scene.articulations[row["runtime_id"]].data.root_pos_w.clone()
                for row in deployment["robot_instances"]
                if row["public_declaration"]["mobility"] == "aerial"
            }
            for _ in range(240):
                _step(session, live, aerial_goals=aerial_goals)
            before = live(live.bindings["objects"][OBJECT]["body_path"])
            _write(output_root / "warehouse_witness_report.json", report)
            prepared = native_attachment.prepare_attachment(
                stage, deployment["public_task"], object_id=OBJECT, mode_id="single_grip",
                carriers={MEMBER: "center_grip"}, active_member_ids=active,
                body_bindings=live.bindings, pose_provider=live)
            receipt = native_attachment.commit_attachment(
                stage, prepared, active_member_ids=active, pose_provider=live)
            report["attachment_receipt"] = receipt
            report["attachment_step"] = live.step
            receipt = _lift(session, stage, live, receipt, direction=1, active=active,
                            aerial_goals=aerial_goals, samples=report["lift_samples"])
            report["apex_attachment_receipt"] = receipt
            report["transport_mode"] = "ideal_fixed_point_constraint_lift"
            size = next(row["physical_properties"]["size_m"] for row in deployment["object_instances"]
                        if row["public_id"] == OBJECT)
            lifted = live(live.bindings["objects"][OBJECT]["body_path"])
            lift_bottom = envelope_bounds(lifted, size)[2]
            report["lift_evidence"] = {"status": "measured", "object": lifted,
                                       "bottom_z_m": lift_bottom, "floor_z_m": 0.,
                                       "target_offset_m": .12, "body_pose_writes": 0}
            if lift_bottom < .03:
                raise ValueError("object did not clear ground under connector lift")

            # Move from the live object pose toward the compiled dock target;
            # every sample is read after a real env.step and uses the native
            # object tensor, so an unchanging object fails closed.
            distance = math.dist(before["position_m"][:2], target[:2])
            max_steps = _witness_request(request)["max_transport_steps"]
            report["transport_step_budget"] = max_steps
            report["transport_initial_distance_m"] = distance
            carrier_before_transport = live(live.bindings["members"][MEMBER]["body_path"])
            best_distance = distance
            last_progress_step = 0
            transport_blocker = None
            for transport_step in range(1, max_steps + 1):
                current = live(live.bindings["objects"][OBJECT]["body_path"])
                carrier = live(live.bindings["members"][MEMBER]["body_path"])
                velocity = _carry_velocity(current["position_m"], target, carrier["rotation_wxyz"])
                now = _step(session, live, velocity=velocity, aerial_goals=aerial_goals)
                sample = live(live.bindings["objects"][OBJECT]["body_path"])
                sample["time_s"] = now
                sample["phase"] = "attached_lifted_transport"
                sample["carrier"] = live(live.bindings["members"][MEMBER]["body_path"])
                sample["object_bottom_z_m"] = envelope_bounds(sample, size)[2]
                sample["m20_control"] = {
                    "command_name": M20_COMMAND_NAME,
                    "command_frame": "M20_base",
                    "command_body_velocity": velocity,
                    **_m20_action_readback(session),
                }
                report["samples"].append(sample)
                remaining = math.dist(sample["position_m"][:2], target[:2])
                if remaining <= M20_STOP_RADIUS_M:
                    break
                if remaining <= best_distance - TRANSPORT_PROGRESS_EPSILON_M:
                    best_distance = remaining
                    last_progress_step = transport_step
                elif transport_step - last_progress_step >= TRANSPORT_STALL_WINDOW_STEPS:
                    transport_blocker = "M20 transport made no bounded object progress after a valid base_velocity-to-action conversion"
                    break
            report["transport_final_distance_m"] = math.dist(
                live(live.bindings["objects"][OBJECT]["body_path"])["position_m"][:2],
                target[:2])
            carried = live(live.bindings["objects"][OBJECT]["body_path"])
            carrier_after = live(live.bindings["members"][MEMBER]["body_path"])
            carrier_displacement = math.dist(
                carrier_before_transport["position_m"], carrier_after["position_m"])
            report["carry_readback"] = {
                "pre_attach_pose_m": before["position_m"],
                "last_attached_pose_m": carried["position_m"],
                "object_displacement_m": math.dist(before["position_m"], carried["position_m"]),
                "carrier_pre_transport_pose_m": carrier_before_transport["position_m"],
                "carrier_last_transport_pose_m": carrier_after["position_m"],
                "carrier_displacement_m": carrier_displacement,
                "attachment_anchor_quality": attachment.anchor_quality(receipt, live.snapshot()["bodies"]),
                "physics_step": live.step,
            }
            if transport_blocker:
                raise ValueError(transport_blocker)
            require_anchor_quality(report["carry_readback"]["attachment_anchor_quality"])
            if carrier_displacement <= 0.05:
                raise ValueError(
                    "M20 base_velocity was accepted but carrier did not move; "
                    "solver/controller scheduling is unresolved")
            if report["carry_readback"]["object_displacement_m"] <= 0.05:
                raise ValueError(
                    "M20 carrier moved but object did not follow the committed fixed point; "
                    "fixed-joint solver scheduling is unresolved")

            report["lift_evidence"]["minimum_transport_bottom_z_m"] = min(
                row["object_bottom_z_m"] for row in report["samples"])
            if report["lift_evidence"]["minimum_transport_bottom_z_m"] < .02:
                raise ValueError("object lost ground clearance during transport")
            receipt = _lift(session, stage, live, receipt, direction=-1, active=active,
                            aerial_goals=aerial_goals, samples=report["lift_samples"])
            report["actuated_attachment_receipt"] = receipt

            report["detach_receipt"] = native_attachment.detach_attachment(stage, receipt)
            report["release_step"] = live.step
            for _ in range(60):
                _step(session, live, velocity=[0.0, 0.0, 0.0], aerial_goals=aerial_goals)
            released = live(live.bindings["objects"][OBJECT]["body_path"])
            report["release_readback"] = released
            receiver = EpisodeDockReceiver(deployment=deployment, live=live, object_id=OBJECT)
            try:
                report["dock_receipt"] = receiver.receive(
                    episode_id=request["run_id"], step=live.step, release_step=report["release_step"],
                    stage=stage, attachment_receipt=receipt, detach_receipt=report["detach_receipt"])
            except ValueError as exc:
                report["dock_receipt"] = {"status": "rejected", "reason": str(exc)}
            report["final_objects"] = read_goal_objects(stage, session.base_env.scene, deployment)
            report["status"] = "diagnostic_completed"
            report["physical_task_execution_validated"] = False
            report["business_outcomes_validated"] = False
            _write(output_root / "warehouse_witness_report.json", report)
    except BaseException as exc:
        report.update(status="blocked", blocker=f"{type(exc).__name__}: {exc}")
        report["physical_task_execution_validated"] = False
        report["business_outcomes_validated"] = False
    _write(output_root / "warehouse_witness_report.json", report)
    return report


def require_anchor_quality(rows):
    """Fail closed when recorded constraints no longer describe a stable hold."""
    if not rows or any(type(row.get(key)) not in (int, float)
                       or not math.isfinite(row[key]) or not 0 <= row[key] <= limit
                       for row in rows for key, limit in (
                           ("anchor_error_m", .01), ("orientation_error_rad", .01))):
        raise ValueError("carry attachment frame error exceeds 1 cm / .01 rad")


def execute_probe(output_root, *, timeout_s=600.):
    root = Path(output_root).absolute()
    request, _, _ = load_probe(root)
    witness_hash = canonical_sha256(_witness_request(request))
    if not math.isfinite(timeout_s) or timeout_s <= 0:
        raise ValueError("timeout must be positive")
    lock_path = ROOT / "tmp/goal_physical_probe.lock"
    lock_path.parent.mkdir(exist_ok=True)
    with lock_path.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("another goal physical scene is running") from exc
        command = [sys.executable, "-m", "demo.warehouse_benchmark.native_witness_probe",
                   "--worker", "--output-root", str(root)]
        with (root / "launch.json").open("x") as launch:
            json.dump({"command": command, "run_id": request["run_id"],
                       "witness_request_sha256": witness_hash, "timeout_s": timeout_s}, launch)
        started = time.monotonic()
        timed_out, interrupted = False, None
        with (root / "isaac.log").open("x") as log:
            process = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                start_new_session=True, pass_fds=(lock.fileno(),),
                env={**os.environ, "EAI_GOAL_PHYSICAL_WORKER_LOCK_FD": str(lock.fileno())})
            def terminate_parent(signum, frame):
                raise KeyboardInterrupt("Warehouse probe parent terminated")
            def stop():
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGTERM)
                try:
                    return process.wait(timeout=10.)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    return process.wait(timeout=10.)
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
        report_path = root / "warehouse_witness_report.json"
        report = json.loads(report_path.read_text()) if report_path.is_file() else {}
        result = {"run_id": request["run_id"], "returncode": code, "timed_out": timed_out,
                  "wall_time_s": time.monotonic()-started, "scoreable": False,
                  "report_sha256": file_sha256(report_path) if report_path.is_file() else None,
                  "diagnostic_completed": code == 0 and not timed_out
                    and report.get("status") == "diagnostic_completed"
                    and report.get("witness_request_sha256") == witness_hash
                    and report.get("run_id") == request["run_id"]}
        _write(root / "process_result.json", result)
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
    parser.add_argument("--timeout-s", type=float, default=600.)
    args = parser.parse_args(argv)
    if args.worker:
        result = run_probe(args.output_root)
    elif args.execute:
        result = execute_probe(args.output_root, timeout_s=args.timeout_s)
    else:
        result = prepare_probe(args.output_root, gui=args.gui, device=args.device)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if (result.get("status") == "diagnostic_completed" if args.worker else
                 result.get("diagnostic_completed") if args.execute else True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
