"""Compare real fresh-process Factory prefixes using the existing exact journal.

No recorded poses/actions are injected into the new process. Both runs execute
the same fixed coordinator schedule. Coverage is named measured channels, not
a claim of complete hidden PhysX or causal-system state.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import random

from tools.project_runtime import PROJECT_ROOT, activate_project_runtime, provenance_report
from tools.validation.project_local_node_route import digest, write, persist_failure


def run(output, reference=None):
    activate_project_runtime()
    output = output.resolve()
    if not output.is_relative_to(PROJECT_ROOT):
        raise ValueError("output must remain in project")
    if reference is not None:
        reference = reference.resolve()
        if not reference.is_relative_to(PROJECT_ROOT):
            raise ValueError("reference must remain in project")
    output.mkdir(parents=True, exist_ok=False)
    report = {"schema": "heterocoord.fresh_process_probe.v1", "pid": os.getpid(),
              "status": "starting", "passed": False, "provenance": provenance_report(),
              "paper_score_eligible": False, "strict_causal_replay_qualified": False,
              "missing_coverage": ["contact_solver_hidden_state", "active_skill_state", "task_and_algorithm_history",
                                   "transport_and_locks_and_event_state"],
              "session_close_observed": False}
    write(output / "result.json", report)
    from simulator import SimulatorLaunchConfig, open_simulator_session
    selection_path = PROJECT_ROOT / "source/EAI_hmrs/EAI_hmrs/envs/EAI-Factory-Z1-v0.json"
    config = SimulatorLaunchConfig(env="EAI-Factory-Z1-v0", resolved_env_name="EAI-Factory-Z1-v0",
        selection_data=json.loads(selection_path.read_text()), device="cpu", seed=0, headless=True,
        enable_ros_bridge_extension=False, disable_orsus_ros_env=True,
        app_launcher_args={"headless": True, "device": "cpu"})
    with open_simulator_session(config) as session, persist_failure(output, report):
        import numpy as np
        import torch
        import omni.usd
        from tools.physics_replay.live import LiveCheckpointBackend
        from tools.physics_replay.reconstruction import ReconstructionRecorder, ReconstructionVerifier
        env = session.base_env
        backend = LiveCheckpointBackend(session, output / "readback")
        paths = {selection_path, Path(__file__).resolve(), PROJECT_ROOT / "tools/project_runtime.py"}
        for layer in omni.usd.get_context().get_stage().GetUsedLayers():
            if layer.realPath:
                path = Path(layer.realPath).resolve()
                if not path.is_relative_to(PROJECT_ROOT):
                    raise ValueError(f"loaded layer outside project: {path}")
                paths.add(path)
        # Freeze the actual EAI implementation, not just the top-level runner.
        paths.update(p for root in (PROJECT_ROOT / "source/EAI/EAI", PROJECT_ROOT / "source/EAI_assets/EAI_assets",
                                    PROJECT_ROOT / "source/EAI_hmrs/EAI_hmrs") for p in root.rglob("*.py"))
        fingerprint = {"source": {str(p.relative_to(PROJECT_ROOT)): digest(p) for p in sorted(paths)},
                       "seed": 0, "device": "cpu", "checkpoint_step": 40, "steps": 60,
                       "schedule": "40 neutral then 20 Carter base_velocity [0.12,0,0]",
                       "coverage": "all registered articulation channels, physical clock, command, Python/NumPy/Torch RNG"}
        report["fingerprint"] = fingerprint
        journal = (ReconstructionVerifier(reference, fingerprint, checkpoint="common-start")
                   if reference is not None else ReconstructionRecorder(output / "journal", fingerprint))
        try:
            for index in range(60):
                value = [0., 0., 0.] if index < 40 else [.12, 0., 0.]
                command = torch.tensor([value], device=env.device)
                env.set_command("carter_1", "base_velocity", command)
                env.step({"carter_1": command})
                frame = backend.read_physics()
                frame["clock"] = np.array([env.common_step_counter, env.sim.current_time, env.sim.current_time_step_index])
                frame["command/carter"] = np.array(value)
                frame["rng/torch"] = torch.get_rng_state()
                python_rng = random.getstate()
                frame["rng/python"] = np.asarray(python_rng[1], dtype=np.uint64)
                frame["rng/python/gauss"] = np.asarray([python_rng[2] is not None, python_rng[2] or 0.])
                numpy_rng = np.random.get_state()
                frame["rng/numpy"] = numpy_rng[1]
                frame["rng/numpy/metadata"] = np.array(numpy_rng[2:])
                state = {"tick": int(env.common_step_counter), "schedule_index": index,
                         "phase": "neutral" if index < 40 else "drive"}
                checkpoint = "common-start" if index == 39 else None
                if reference is None:
                    journal.append(frame, task_state=state, checkpoint=checkpoint)
                else:
                    journal.compare(frame, task_state=state, checkpoint=checkpoint)
            if reference is None:
                journal.close(complete=True)
                report["passed"] = True
                report["status"] = "recorded"
            else:
                report["verification"] = journal.report()
                report["passed"] = report["verification"]["prefix_verified"] and report["verification"]["original_continuation_verified"]
                report["status"] = "verified"
        finally:
            if reference is not None:
                report["verification"] = journal.report()
                journal.close()
            elif not journal.closed:
                journal.close(complete=False)
            write(output / "result.json", report)
        print(json.dumps({k: v for k, v in report.items() if k not in {"fingerprint", "provenance"}}), flush=True)
    report["session_close_observed"] = True
    write(output / "result.json", report)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reference", type=Path)
    args = parser.parse_args()
    raise SystemExit(run(args.output, args.reference))
