"""Run a bounded one-scene Isaac diagnostic from project-local EAI sources."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import traceback


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.project_runtime import activate_project_runtime, provenance_report


def _vector(value):
    if hasattr(value, "detach"):
        value = value.detach()
    if hasattr(value, "cpu"):
        value = value.cpu()
    if hasattr(value, "tolist"):
        value = value.tolist()
    while isinstance(value, (tuple, list)) and len(value) == 1:
        value = value[0]
    return [float(item) for item in value]


def _positions(session) -> dict[str, list[float]]:
    articulations = getattr(getattr(session.base_env, "scene", None), "articulations", {})
    result = {}
    for name in session.possible_agents:
        robot = articulations.get(name)
        position = getattr(getattr(robot, "data", None), "root_pos_w", None)
        if position is not None:
            result[name] = _vector(position)[:3]
    return result


def _write(path: Path, payload: dict) -> None:
    resolved = path.expanduser().resolve()
    if not resolved.is_relative_to(ROOT):
        raise ValueError("smoke evidence must stay inside HeteroCoord")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    temporary = resolved.with_suffix(resolved.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(resolved)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", default="EAI-Factory-Z1-v0")
    parser.add_argument("--steps", type=int, default=8)
    parser.add_argument("--device", default="cpu")
    parser.add_argument(
        "--output", type=Path,
        default=ROOT / "artifacts" / "project_runtime" / "single_scene_smoke.json")
    args = parser.parse_args()
    if not 1 <= args.steps <= 200:
        parser.error("--steps must be in [1, 200]")

    activate_project_runtime()
    report = {
        "schema": "heterocoord.single_scene_diagnostic.v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "environment": args.env,
        "requested_steps": args.steps,
        "requested_device": args.device,
        "claim_scope": "diagnostic_scene_attachment",
        "physical_qualification": False,
        "status": "failed_before_session",
        "provenance": provenance_report(),
    }
    try:
        from benchmark.eai_adapter import EaiSessionRuntime
        from simulator import SimulatorLaunchConfig, open_simulator_session

        selection_path = (
            ROOT / "source" / "EAI_hmrs" / "EAI_hmrs" / "envs" / f"{args.env}.json")
        selection = json.loads(selection_path.read_text(encoding="utf-8"))
        config = SimulatorLaunchConfig(
            env=args.env,
            num_envs=1,
            device=args.device,
            seed=0,
            headless=True,
            enable_ros_bridge_extension=False,
            disable_orsus_ros_env=True,
            resolved_env_name=args.env,
            selection_data=selection,
            app_launcher_args={"headless": True, "device": args.device},
        )
        with open_simulator_session(config) as session:
            runtime = EaiSessionRuntime(session)
            report.update({
                "status": "session_opened",
                "runtime_environment": session.env_name,
                "runtime_device": str(session.device),
                "agents": list(session.possible_agents),
                "positions_before": _positions(session),
                "simulation_time_before_s": runtime.sim_time_s(),
            })
            for _ in range(args.steps):
                session.env.step(runtime.neutral_actions())
            report.update({
                "status": "diagnostic_steps_completed",
                "completed_steps": args.steps,
                "positions_after": _positions(session),
                "simulation_time_after_s": runtime.sim_time_s(),
                # Persist before Kit shutdown. Some native shutdown paths end
                # the process before Python resumes after the context manager.
                "session_close_observed": False,
            })
            _write(args.output, report)
        report["session_close_observed"] = True
        _write(args.output, report)
        return 0
    except BaseException as exc:
        report.update({
            "status": "failed",
            "error_type": type(exc).__name__,
            "error": str(exc),
            "traceback": traceback.format_exc(),
        })
        _write(args.output, report)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
