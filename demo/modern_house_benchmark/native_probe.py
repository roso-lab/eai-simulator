"""Serial, bounded House sensor/path diagnostic using the reusable session API.

Both snapshots are real observations separated by simulation steps. No object
or robot is repositioned, and an unchanged blocked path is a valid measurement,
not a completed household task. Camera capture does not require ROS.
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


def _sources():
    paths = [Path(__file__), Path(__file__).with_name("native_adapters.py"),
             Path(__file__).with_name("live_path.py"),
             ROOT / "demo/benchmark/reference_domain_providers.py"]
    return {str(p): file_sha256(p) for p in paths}


def prepare(root):
    request = physical.prepare_probe(root, scene="modern_house_nvd", steps=120, device="cpu")
    payload = {"run_id": request["run_id"], "request_sha256": canonical_sha256(request),
               "source_files": _sources(), "points": POINTS, "settle_steps": 120,
               "between_steps": 30, "scoreable": False}
    physical.write_json(Path(root) / "backend_request.json", payload)
    return payload


def load(root, *, rebuild):
    request, deployment, selection = physical.load_probe(root, rebuild=rebuild)
    payload = json.loads((root / "backend_request.json").read_text())
    if (payload["run_id"] != request["run_id"]
            or payload["request_sha256"] != canonical_sha256(request)
            or payload["source_files"] != _sources()
            or payload["points"] != POINTS
            or payload["settle_steps"] != 120 or payload["between_steps"] != 30
            or payload["scoreable"] is not False):
        raise ValueError("House diagnostic input/source changed")
    return request, deployment, selection, payload


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
              "scope": "two_native_snapshots_without_business_intervention"}

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

            def step(count):
                for _ in range(count):
                    if not session.simulation_app.is_running():
                        raise RuntimeError("application stopped")
                    actions = {a: torch.zeros((1, 3), device=session.device) for a in session.possible_agents}
                    for a, goal in aerial.items():
                        session.base_env.set_command(a, "goal_position", goal)
                        actions[a] = goal.clone()
                    session.env.step(actions)
                for _ in range(12):
                    session.base_env.sim.render()

            occupancy = provider_map(scene_key="modern_house_nvd")
            item_codes = {r["public_id"]: r["public_id"] for r in deployment["object_instances"]}
            report["camera"] = {"prim_path": cameras[0], "producer": agent, "resolution": [320, 240],
                                "source": "replicator.rgb.native_robot_camera", "ros_enabled": False}
            report["runtime_source_files"] = physical.runtime_sources(stage)
            for label, count in (("before", payload["settle_steps"]), ("after", payload["between_steps"])):
                step(count)
                result = probe_native_backends(session=session, stage=stage, deployment=deployment,
                    occupancy=occupancy, sensor_manager=NativeCamera(label), agent=agent,
                    episode_id=request["run_id"], item_codes=item_codes, max_range_m=10.,
                    points=payload["points"], output_path=root / f"{label}.json")
                report[label] = {"status": result["status"], "sha256": file_sha256(root / f"{label}.json"),
                                 "blockers": result["blockers"]}
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
    parser.add_argument("--timeout-s", type=float, default=300.)
    args = parser.parse_args()
    root = args.output_root.absolute()
    result = worker(root) if args.worker else execute(root, args.timeout_s) if args.execute else prepare(root)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if not args.execute or result["diagnostic_completed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
