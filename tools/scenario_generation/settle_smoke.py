"""Run a short zero-command settle simulation of a scenario instance.

Opens the environment through the reusable session API, steps physics with
zero commands, and reports whether scenario entities and robots stay near
their spawn poses. A passing report is the `spawn_validated` tier -- it says
the instance assembles and settles without exploding, nothing more.

Requires a display for GUI mode (Isaac Sim rendering); use --headless only on
setups where headless initialization is known to work.
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    import common
else:
    from . import common

if str(common.REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(common.REPO_ROOT))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario")
    parser.add_argument("--steps", type=int, default=100, help="Physics steps with zero commands")
    parser.add_argument("--drift-tol", type=float, default=0.5,
                        help="Allowed root displacement in metres during settle")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--gpu-physics", action="store_true",
                        help="Set EAI_FACTORY_GPU_PHYSICS=1 (skips the factory CPU fallback)")
    parser.add_argument("--report", help="Also write the settle report JSON to this path")
    parser.add_argument("--ros-bridge", action="store_true",
                        help="Enable the ROS2 bridge extension (default off: settle is pure physics; "
                             "bridge startup without a live ROS environment kills headless runs)")
    args = parser.parse_args(argv)

    import os

    if args.gpu_physics:
        os.environ["EAI_FACTORY_GPU_PHYSICS"] = "1"

    scenario_path = Path(args.scenario).resolve()
    scenario_sha256 = hashlib.sha256(scenario_path.read_bytes()).hexdigest()
    from tools.scenario_generation.robot_dossier import all_dossiers_snapshot_hash
    dossier_snapshot_sha256 = all_dossiers_snapshot_hash()

    try:
        import torch

        from simulator import SimulatorLaunchConfig, open_simulator_session
        from EAI.scenario_spec import load_scenario

        spec = load_scenario(str(scenario_path))
        launcher_args = {"headless": args.headless, "device": args.device,
                         "num_envs": 1, "seed": spec.get("seed", 0)}
        if args.headless:
            # Plain headless.kit cannot initialize the Replicator SDG pipeline
            # on this setup and the environment build dies in OmniGraph; the
            # rendering experience (what sensor selections already use) works.
            launcher_args["enable_cameras"] = True
        config = SimulatorLaunchConfig(
            env=None,
            scenario_file=str(scenario_path),
            num_envs=1,
            device=args.device,
            seed=spec.get("seed", 0),
            headless=args.headless,
            enable_ros_bridge_extension=args.ros_bridge,
            disable_orsus_ros_env=not args.ros_bridge,
            app_launcher_args=launcher_args,
        )
        with open_simulator_session(config) as session:
            env = session.env
            base_env = session.base_env
            from tools.scenario_generation.benchmark_mass_audit import audit_benchmark_masses
            mass_audit = audit_benchmark_masses(base_env)

            from EAI.scenario_spec.runtime_evaluator import (
                PredicateEvaluator,
                summarize_trajectory,
            )

            evaluator = PredicateEvaluator(spec)

            def root_xy(handle):
                state = handle.data.root_state_w[0]
                return float(state[0]), float(state[1]), float(state[2])

            tracked = {}
            for name, body in base_env.scene.rigid_objects.items():
                if name.startswith("scenario_"):
                    tracked[name] = root_xy(body)
            for name, robot in base_env.scene.articulations.items():
                tracked[name] = root_xy(robot)

            def sample_entity_positions():
                positions = {name[len("scenario_"):]: (root_xy(body)[0], root_xy(body)[1])
                             for name, body in base_env.scene.rigid_objects.items()
                             if name.startswith("scenario_")}
                positions.update({name: (root_xy(robot)[0], root_xy(robot)[1])
                                  for name, robot in base_env.scene.articulations.items()})
                return positions

            # Zero commands per agent, matching the launcher's loop shape.
            actions = {
                agent: torch.zeros((session.num_envs, 3), device=session.device)
                for agent in session.possible_agents
            }
            trajectory = [evaluator.trajectory_step(0, sample_entity_positions())]
            for step in range(1, args.steps + 1):
                env.step(actions)
                if step % max(1, args.steps // 10) == 0 or step == args.steps:
                    trajectory.append(evaluator.trajectory_step(
                        step, sample_entity_positions()))
            predicate_summary = summarize_trajectory(trajectory)

            report = {"status": "spawn_validated", "steps": args.steps,
                      "benchmark_mass_audit": mass_audit,
                      "drift_tol_m": args.drift_tol, "entities": {},
                      "scenario_sha256": scenario_sha256,
                      "robot_dossier_snapshot_sha256": dossier_snapshot_sha256,
                      "evaluation": predicate_summary}
            worst = 0.0
            for name, (x0, y0, z0) in tracked.items():
                handle = base_env.scene.rigid_objects.get(name) or base_env.scene.articulations[name]
                x1, y1, z1 = root_xy(handle)
                drift = (abs(x1 - x0) ** 2 + abs(y1 - y0) ** 2) ** 0.5
                z_drop = abs(z1 - z0)
                worst = max(worst, drift, z_drop)  # z collapse is a settle failure too
                report["entities"][name] = {"spawn": [x0, y0, z0], "final": [x1, y1, z1],
                                             "drift_m": round(drift, 4),
                                             "z_drop_m": round(z_drop, 4)}
            if worst > args.drift_tol or not mass_audit["validated"]:
                report["status"] = "settle_rejected"
            if hashlib.sha256(scenario_path.read_bytes()).hexdigest() != scenario_sha256:
                report["status"] = "settle_rejected_input_changed"
            if all_dossiers_snapshot_hash() != dossier_snapshot_sha256:
                report["status"] = "settle_rejected_dossier_changed"
            print(json.dumps(report, ensure_ascii=False, indent=2))
            if args.report:
                Path(args.report).parent.mkdir(parents=True, exist_ok=True)
                Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                                             encoding="utf-8")
            return 0 if report["status"] == "spawn_validated" else 1
    except Exception as exc:
        payload = {"status": "rejected", "error": str(exc),
                   "scenario_sha256": scenario_sha256,
                   "robot_dossier_snapshot_sha256": dossier_snapshot_sha256}
        print(json.dumps(payload, ensure_ascii=False), file=sys.stderr)
        if args.report:
            Path(args.report).parent.mkdir(parents=True, exist_ok=True)
            Path(args.report).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                                         encoding="utf-8")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
