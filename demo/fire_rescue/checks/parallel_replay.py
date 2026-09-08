"""Two independent checks per job: bounded task logic and physical reconstruction."""

from __future__ import annotations

import argparse
from functools import partial
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import traceback


def write_report(path, report):
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--rounds", type=int, default=2)
    parser.add_argument("--steps", type=int, default=400, help="Bounded task steps; not a mission success target.")
    parser.add_argument("--algorithm", default="algorithm.emos.example_policy:assign")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--timeout", type=float, default=600.0, help="Seconds per job, including its child processes.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--job-worker", action="store_true", help=argparse.SUPPRESS)
    mode.add_argument("--task-worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--gate", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--slot", type=int, default=0, help=argparse.SUPPRESS)
    parser.add_argument("--seed", type=int, default=0, help=argparse.SUPPRESS)
    parser.add_argument("--hazard", type=int, default=1, help=argparse.SUPPRESS)
    return parser


def worker_arguments(args):
    return ["--workers", str(args.workers), "--steps", str(args.steps), "--algorithm", args.algorithm,
            "--gate", str(args.gate), "--slot", str(args.slot), "--seed", str(args.seed),
            "--hazard", str(args.hazard)]


def run_task_worker(args):
    from simulator import SimulatorLaunchConfig, open_simulator_session
    from demo.fire_rescue.config import FireRescueConfig
    from demo.fire_rescue.experiment import configure_fire_rescue_env_cfg, run_robot_baseline_experiment

    config = SimulatorLaunchConfig(
        env="EAI-Factory-v0", num_envs=1, seed=args.seed, device="cpu", headless=True,
        enable_ros_bridge_extension=False, disable_orsus_ros_env=True,
        env_cfg_hook=partial(configure_fire_rescue_env_cfg, exact=True),
    )
    with open_simulator_session(config) as session:
        report = {"logic_passed": False, "pid": os.getpid(), "seed": args.seed, "num_envs": 1,
                  "headless": True, "full_task_physical_rewind_verified": False}
        try:
            from demo.fire_rescue.runtime.experiment_loop import run_robot_factory_platform

            write_report(args.gate / f"ready_{args.slot}.json", {"pid": os.getpid()})
            deadline = time.monotonic() + 180.0
            while not all((args.gate / f"ready_{slot}.json").is_file() for slot in range(args.workers)):
                if (args.gate / "abort.json").exists() or time.monotonic() >= deadline:
                    raise RuntimeError("another task worker failed to reach the scene-start barrier")
                time.sleep(0.05)
            timing = {"first_step": None, "last_step": None, "calls": 0}

            def bounded_platform(**kwargs):
                kwargs["args_cli"].max_steps = args.steps
                environment = kwargs["env"]
                original = environment.step
                owned = vars(environment).get("step")

                def step(*values, **options):
                    if timing["first_step"] is None:
                        timing["first_step"] = time.monotonic()
                    result = original(*values, **options)
                    timing["last_step"] = time.monotonic()
                    timing["calls"] += 1
                    return result

                environment.step = step
                try:
                    return run_robot_factory_platform(**kwargs)
                finally:
                    if owned is None:
                        delattr(environment, "step")
                    else:
                        environment.step = owned

            task_config = FireRescueConfig(
                trials=1, trial_hazard_ids=str(args.hazard), auto_fire_delay=0.0, headless=True,
                task_checkpoints=True, replay_exact=True, assignment_policy=args.algorithm,
                branch_checkpoint_dir=args.output / "checkpoints",
            )
            steps = run_robot_baseline_experiment(session, task_config, run_platform=bounded_platform)
            index = json.loads((args.output / "checkpoints/trial_01/task_checkpoints.json").read_text(encoding="utf-8"))
            completed = index["state"]["completed"]
            passed = (index["algorithm"] == args.algorithm and "assignment_applied" in completed
                      and timing["calls"] == steps and (steps == args.steps or index["result"] == "success"))
            report.update(logic_passed=passed, steps=steps, timing=timing, task_index=index,
                          mission_success=index["result"] == "success", bounded_run_not_full_mission=True,
                          physical_attachment_mode=True, algorithm=args.algorithm)
            if not passed:
                raise RuntimeError("bounded task did not apply its algorithm and save the assignment checkpoint")
        except Exception:
            report["error"] = traceback.format_exc()
            print(report["error"], file=sys.stderr, flush=True)
        finally:
            write_report(args.output / "task.json", report)
            if not report["logic_passed"]:
                import omni.kit.app

                omni.kit.app.get_app().post_quit(1)


def run_job_worker(args):
    report = {"passed": False, "pid": os.getpid(), "seed": args.seed,
              "full_task_physical_rewind_verified": False,
              "scope": "task/algorithm/checkpoint smoke plus a separate controlled carry reconstruction fixture"}
    try:
        task_directory = args.output / "task"
        task_directory.mkdir()
        command = [sys.executable, "-m", "demo.fire_rescue.checks.parallel_replay", "--task-worker", "--output", str(task_directory),
                   *worker_arguments(args)]
        with (task_directory / "isaac.log").open("w", encoding="utf-8") as log:
            result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT)
        task = json.loads((task_directory / "task.json").read_text(encoding="utf-8"))
        report["task"] = task
        if result.returncode != 0 or not task["logic_passed"]:
            raise RuntimeError(f"task subprocess failed: exit {result.returncode}; see task/isaac.log")
        print(f"[Parallel] task checkpoint passed: {args.output}", flush=True)
        command = [sys.executable, "-m", "demo.fire_rescue.checks.reconstruct_physics",
                   "--output", str(args.output / "fidelity"), "--seed", str(args.seed)]
        with (args.output / "fidelity.log").open("w", encoding="utf-8") as log:
            result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT)
        fidelity = json.loads((args.output / "fidelity/comparison.json").read_text(encoding="utf-8"))
        report["fidelity"] = fidelity
        report["passed"] = result.returncode == 0 and fidelity["passed"]
    except Exception:
        report["error"] = traceback.format_exc()
    finally:
        if not report["passed"]:
            write_report(args.gate / "abort.json", {"failed_slot": args.slot})
        write_report(args.output / "job.json", report)
    return 0 if report["passed"] else 1


def stop_process_group(process):
    for action in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, action)
        except ProcessLookupError:
            break
        if action == signal.SIGTERM:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
    process.wait(timeout=5)


def summarize_round(jobs, workers):
    timings = [job.get("task", {}).get("timing", {}) for job in jobs]
    intervals = [(entry.get("first_step"), entry.get("last_step")) for entry in timings]
    overlap = 0.0
    if len(intervals) == workers and all(start is not None and end is not None for start, end in intervals):
        overlap = max(0.0, min(end for start, end in intervals) - max(start for start, end in intervals))
    pids = [job.get("task", {}).get("pid") for job in jobs]
    independent = None not in pids and len(set(pids)) == workers
    passed = (len(jobs) == workers and all(job.get("passed") and job.get("exit_code") == 0 for job in jobs)
              and independent and (workers == 1 or overlap > 0))
    return {"passed": passed, "independent_task_processes": independent,
            "task_step_interval_overlap_s": overlap, "jobs": jobs}


def run_parallel(args):
    if args.output is None:
        root = Path("tmp/replay")
        root.mkdir(parents=True, exist_ok=True)
        args.output = Path(tempfile.mkdtemp(prefix="parallel_replay_", dir=root))
    else:
        args.output.mkdir(parents=True, exist_ok=False)
    summary = {"passed": False, "workers": args.workers, "rounds_requested": args.rounds,
               "algorithm": args.algorithm, "rounds": [], "full_task_physical_rewind_verified": False}
    print(f"[Parallel] {args.workers} isolated workers x {args.rounds} rounds: {args.output}", flush=True)
    for round_index in range(args.rounds):
        round_directory = args.output / f"round_{round_index + 1:02d}"
        gate = round_directory / "gate"
        gate.mkdir(parents=True)
        running = []
        jobs = []
        try:
            for slot in range(args.workers):
                output = round_directory / f"worker_{slot + 1:02d}"
                output.mkdir()
                worker = argparse.Namespace(**vars(args))
                worker.gate, worker.slot = gate, slot
                worker.seed, worker.hazard = round_index * args.workers + slot, round_index % 4 + 1
                command = [sys.executable, "-m", "demo.fire_rescue.checks.parallel_replay", "--job-worker",
                           "--output", str(output), *worker_arguments(worker)]
                log = (output / "worker.log").open("w", encoding="utf-8")
                try:
                    process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
                                               env={**os.environ, "PYTHONHASHSEED": str(worker.seed),
                                                    "CUBLAS_WORKSPACE_CONFIG": ":4096:8", "PYTHONUNBUFFERED": "1"})
                except BaseException:
                    log.close()
                    raise
                running.append({"process": process, "log": log, "output": output,
                                "deadline": time.monotonic() + args.timeout, "collected": False})
            while any(not job["collected"] for job in running):
                for job in running:
                    if job["collected"]:
                        continue
                    process = job["process"]
                    timed_out = time.monotonic() >= job["deadline"] and process.poll() is None
                    if timed_out:
                        stop_process_group(process)
                    if process.poll() is None:
                        continue
                    path = job["output"] / "job.json"
                    try:
                        report = json.loads(path.read_text(encoding="utf-8"))
                    except (OSError, ValueError):
                        report = {"passed": False, "error": "missing or invalid job report"}
                    report.update(exit_code=process.returncode, timed_out=timed_out, output=str(job["output"]))
                    if timed_out or process.returncode != 0:
                        report["passed"] = False
                    jobs.append(report)
                    job["collected"] = True
                    if not report["passed"]:
                        write_report(gate / "abort.json", {"error": "peer job failed"})
                    print(f"[Parallel] {'PASS' if report['passed'] else 'FAIL'} {job['output']}", flush=True)
                time.sleep(0.1)
        finally:
            for job in running:
                stop_process_group(job["process"])
                job["log"].close()
        summary["rounds"].append(summarize_round(jobs, args.workers))
        write_report(args.output / "parallel.json", summary)
    summary["passed"] = all(result["passed"] for result in summary["rounds"])
    write_report(args.output / "parallel.json", summary)
    print(f"[Parallel] {'PASS' if summary['passed'] else 'FAIL'}: {args.output / 'parallel.json'}", flush=True)
    return summary


def main():
    import math

    parser = build_parser()
    args = parser.parse_args()
    if args.workers < 1 or args.rounds < 1 or args.steps < 1 or not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("workers, rounds, steps and finite timeout must be positive")
    if (args.job_worker or args.task_worker) and (args.output is None or args.gate is None):
        parser.error("internal workers require an output directory and round gate")
    if args.task_worker:
        run_task_worker(args)
        return 0
    if args.job_worker:
        return run_job_worker(args)
    return 0 if run_parallel(args)["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
