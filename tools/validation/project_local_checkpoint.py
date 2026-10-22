"""Bounded real checkpoint/readback and sham continuation of the local Factory.

Uses existing replay components, keeps failed attempts, and explicitly rejects
the inference that physical readback alone proves complete causal restoration.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import traceback

from tools.project_runtime import PROJECT_ROOT, activate_project_runtime, provenance_report
from tools.validation.project_local_node_route import write


def native_clock_snapshot(env):
    """Read independent native/context/environment clocks without advancing.

    These getters are distinct in the installed Isaac runtime. Rewriting a
    Python counter cannot establish that the native simulation was restored.
    """
    import omni.timeline
    from isaacsim.core.simulation_manager import SimulationManager
    return {
        "context_time_s": float(env.sim.current_time),
        "context_physics_steps": int(env.sim.current_time_step_index),
        "native_time_s": float(SimulationManager.get_simulation_time()),
        "native_physics_steps": int(SimulationManager.get_num_physics_steps()),
        "timeline_time_s": float(omni.timeline.get_timeline_interface().get_current_time()),
        "environment_steps": int(env.common_step_counter),
        "environment_physics_steps": int(env._sim_step_counter),
    }


def run(output):
    activate_project_runtime()
    output = output.resolve()
    if not output.is_relative_to(PROJECT_ROOT):
        raise ValueError("output must remain in project")
    output.mkdir(parents=True, exist_ok=False)
    report = {"schema": "heterocoord.checkpoint_probe.v1", "status": "starting",
              "provenance": provenance_report(), "passed": False,
              "strict_causal_replay_qualified": False, "paper_score_eligible": False,
              "tolerance": 1e-5, "sham_steps": 20,
              "missing_participants": ["clock", "skill", "task", "algorithm", "transport", "locks", "events"],
              "session_close_observed": False}
    pin_paths = (Path(__file__), PROJECT_ROOT / "tools/physics_replay/live.py",
                 PROJECT_ROOT / "tools/physics_replay/runtime_state.py")
    report["probe_source_sha256"] = {str(p.relative_to(PROJECT_ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                                    for p in pin_paths}
    report["clock_policy"] = "read_only_native_context_environment_getters_no_counter_rewrite"
    write(output / "result.json", report)
    from simulator import SimulatorLaunchConfig, open_simulator_session
    path = PROJECT_ROOT / "source/EAI_hmrs/EAI_hmrs/envs/EAI-Factory-Z1-v0.json"
    config = SimulatorLaunchConfig(
        env="EAI-Factory-Z1-v0", resolved_env_name="EAI-Factory-Z1-v0",
        selection_data=json.loads(path.read_text()), device="cpu", headless=True,
        seed=0, enable_ros_bridge_extension=False, disable_orsus_ros_env=True,
        app_launcher_args={"headless": True, "device": "cpu"})
    try:
        with open_simulator_session(config) as session:
            try:
                import torch
                from tools.physics_replay.live import LiveCheckpointBackend, state_errors
                from tools.physics_replay.runtime_state import EnvironmentBuffersComponent, ActuatorStateComponent
                from tools.physics_replay.stage_state import StageStateComponent, SleepStateComponent
                env = session.base_env
                zero = torch.zeros((1, 3), device=env.device)
                for _ in range(40):
                    env.step({"carter_1": zero})
                stage = StageStateComponent(session)
                backend = LiveCheckpointBackend(session, output / "checkpoint", tolerance=report["tolerance"],
                    components=(stage, SleepStateComponent(stage), EnvironmentBuffersComponent(env), ActuatorStateComponent(env)))
                report["component_names"] = [c.name for c in backend.registry.components]
                report["source_time_s"] = float(env.sim.current_time)
                report["source_tick"] = int(env.common_step_counter)
                report["source_clocks"] = native_clock_snapshot(env)
                checkpoint = backend.capture(report["source_tick"], "pre-command-sham")
                report["checkpoint_id"] = checkpoint.identifier
                report["status"] = "captured"
                write(output / "result.json", report)
                command = torch.tensor([[.12, 0., 0.]], device=env.device)
                reference = []
                for _ in range(report["sham_steps"]):
                    env.set_command("carter_1", "base_velocity", command)
                    env.step({"carter_1": command})
                    reference.append(backend.read_physics())
                report["restore"] = backend.restore(checkpoint.identifier)
                report["restored_time_s"] = float(env.sim.current_time)
                report["restored_tick"] = int(env.common_step_counter)
                report["restored_clocks"] = native_clock_snapshot(env)
                report["clock_channel_matches"] = {key: value == report["restored_clocks"][key]
                                                    for key, value in report["source_clocks"].items()}
                report["clock_restored"] = all(report["clock_channel_matches"].values())
                maxima = {}
                for index in range(report["sham_steps"]):
                    env.set_command("carter_1", "base_velocity", command)
                    env.step({"carter_1": command})
                    errors = state_errors(reference[index], backend.read_physics())
                    for key, value in errors.items():
                        maxima[key] = max(maxima.get(key, 0.), value)
                report["sham_trajectory_errors"] = maxima
                report["sham_within_tolerance"] = bool(maxima) and all(v <= report["tolerance"] for v in maxima.values())
                report["passed"] = report["restore"]["passed"] and report["clock_restored"] and report["sham_within_tolerance"]
                report["status"] = "completed"
            except BaseException as exc:
                report.update(status="failed", passed=False, error_type=type(exc).__name__,
                              error=str(exc), traceback=traceback.format_exc())
            finally:
                # Persist before Kit shutdown: Python continuation may terminate.
                write(output / "result.json", report)
                print(json.dumps({k: v for k, v in report.items()
                                  if k not in {"provenance", "traceback", "sham_trajectory_errors"}}), flush=True)
        report["session_close_observed"] = True
        write(output / "result.json", report)
    except BaseException as exc:
        report.update(status="failed", passed=False, error_type=type(exc).__name__,
                      error=str(exc), traceback=traceback.format_exc())
        write(output / "result.json", report)
        raise
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    raise SystemExit(run(parser.parse_args().output))
