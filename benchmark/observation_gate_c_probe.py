"""Gate C probe: one real builder-camera frame through the observation chain.

Runs on GPU (cuda:0) under the standing CUDA diagnostic authorization
(2026-10-23): one shot into a fresh output directory, fail-stop without
retry, identity pre/post checks, telemetry cleanup of only newly added
PIDs, and all seven formal gates stay false.

Chain under test: builder-default D455 camera prim → render product +
Replicator RGB annotator (BuilderCameraBackend callbacks) → GoalCameraTools
→ CameraObservationRuntime with a real BranchLedger and content-addressed
artifact store → ObservationReceipt.  This is the live capture path; the
fake backend stays in CPU tests and never stands in for this evidence.

Usage:
    python -m benchmark.observation_gate_c_probe \
        --output artifacts/observation/gate-c-<date>-<nn> [--device cuda:0]
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

FORMAL_GATES_FALSE = {
    "physical_checkpoint_qualified": False,
    "history_initialization_qualified": False,
    "branch_isolation_qualified": False,
    "closed_loop_audit_qualified": False,
    "statistical_widening_qualified": False,
    "paper_score_eligible": False,
    "rankable": False,
}


def _gpu_state() -> dict:
    def query(*nvidia_args: str) -> str | None:
        out = subprocess.run(["nvidia-smi", *nvidia_args], capture_output=True, text=True)
        return out.stdout.strip() if out.returncode == 0 else None
    return {
        "uuid": query("--query-gpu=uuid", "--format=csv,noheader"),
        "compute_apps": query("--query-compute-apps=pid,process_name",
                              "--format=csv,noheader") or "",
    }


def _transmitter_pids() -> set[int]:
    out = subprocess.run(["pgrep", "-f", "omni.telemetry.transmitter"],
                         capture_output=True, text=True)
    if out.returncode != 0:
        return set()
    return {int(line) for line in out.stdout.split() if line.strip().isdigit()}


def _write(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")


def _run_probe() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--robot", default="carter")
    parser.add_argument("--controller-cfg", default="CARTER_DIFF_CFG")
    parser.add_argument("--camera-name", default="Camera_OmniVision_OV9782_Color",
                        help="camera prim name under the robot (D455 color default, "
                             "'Camera' for the aerial builtin)")
    parser.add_argument("--width", type=int, default=320)
    parser.add_argument("--height", type=int, default=320)
    args = parser.parse_args()

    output = args.output
    if output.exists():
        print(f"GATE_C_FAIL output directory already exists: {output}", file=sys.stderr)
        return 2
    if args.device != "cuda:0":
        print("GATE_C_FAIL frozen authorization covers cuda:0 only", file=sys.stderr)
        return 2

    # ------------------------------------------------------------------
    # Pre-flight (before any output directory is created).
    # ------------------------------------------------------------------
    gpu_before = _gpu_state()
    transmitters_before = _transmitter_pids()
    preflight = {
        "device": args.device,
        "gpu_uuid": gpu_before["uuid"],
        "compute_apps_baseline": gpu_before["compute_apps"],
        "transmitter_pids_before": sorted(transmitters_before),
        "interpreter": sys.executable,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    if gpu_before["uuid"] is None or gpu_before["compute_apps"] is None:
        print("GATE_C_FAIL nvidia-smi preflight query failed", file=sys.stderr)
        return 2
    if gpu_before["compute_apps"]:
        print("GATE_C_FAIL selected GPU has live compute apps; evidence would be polluted",
              file=sys.stderr)
        return 2
    if transmitters_before:
        print("GATE_C_FAIL pre-existing telemetry transmitter; refusing to run",
              file=sys.stderr)
        return 2

    output.mkdir(parents=True)
    _write(output / "preflight.json", preflight)
    report: dict = {"status": "running", **preflight, "formal_gates": dict(FORMAL_GATES_FALSE)}
    _write(output / "report.json", report)

    # ------------------------------------------------------------------
    # Isaac session with the builder's default D455 camera mounted.
    # ------------------------------------------------------------------
    from simulator import SimulatorLaunchConfig, open_simulator_session

    robot_id = f"{args.robot}_1"

    def hook(cfg):
        import isaaclab.sim as sim
        from isaaclab.assets import AssetBaseCfg
        payload = getattr(cfg.scene, f"realsense_{robot_id}", None)
        if payload is not None:
            # The observation skill attaches its own render product; the
            # legacy ROS publication graphs must stay dark headlessly.
            payload.enable_camera_publish = False
            payload.enable_imu_publish = False
            payload.__post_init__()
        # Build the ground IMMEDIATELY on the live stage as a plain local
        # two-triangle UsdGeom.Mesh with a collision API: the stair scanners
        # resolve their raycast prim before deferred scene assets spawn
        # ("Invalid mesh prim path" unless a Plane/Mesh prim exists there),
        # and both the grid-world ground USD and the default terrain pull
        # remote assets unavailable offline.  A dome light is mandatory
        # headlessly (without it downward cameras capture near-black frames).
        cfg.scene.terrain = None
        cfg.scene.ground = None
        import omni.usd as _omni_usd
        from pxr import UsdGeom, UsdPhysics, Vt
        _stage = _omni_usd.get_context().get_stage()
        _mesh = UsdGeom.Mesh.Define(_stage, "/World/ground")
        _half = 100.0
        _mesh.CreatePointsAttr(Vt.Vec3fArray([
            (-_half, -_half, 0.0), (_half, -_half, 0.0),
            (_half, _half, 0.0), (-_half, _half, 0.0)]))
        _mesh.CreateFaceVertexCountsAttr(Vt.IntArray([3, 3]))
        _mesh.CreateFaceVertexIndicesAttr(Vt.IntArray([0, 1, 2, 0, 2, 3]))
        UsdPhysics.CollisionAPI.Apply(_mesh.GetPrim())
        _light = sim.DomeLightCfg(intensity=2000.0)
        _light.func("/World/GateCLight", _light)
        # A distinctive target in front of the camera proves the frame saw
        # the scene rather than an empty/black render.
        cfg.scene.gate_c_target = AssetBaseCfg(
            prim_path="/World/GateCTarget",
            spawn=sim.CuboidCfg(size=(0.6, 0.6, 0.8),
                                visual_material=sim.PreviewSurfaceCfg(diffuse_color=(0.8, 0.1, 0.05))),
            init_state=AssetBaseCfg.InitialStateCfg(pos=(2.0, 0.0, 0.4)))

    selection = {
        "version": 1,
        "task_name": "observation_gate_c",
        "scene_key": "plane",
        "robots": [{
            "type": args.robot,
            "controller": {"mode": "manual", "cfg": args.controller_cfg},
            "visual": {"x": 0.0, "y": 0.0},
            "attachments": [],
        }],
    }
    config = SimulatorLaunchConfig(
        env="observation_gate_c", resolved_env_name="observation_gate_c",
        selection_data=selection, num_envs=1, device=args.device, headless=True,
        enable_ros_bridge_extension=False, disable_orsus_ros_env=True,
        app_launcher_args={"enable_cameras": True}, env_cfg_hook=hook)

    with open_simulator_session(config) as session:
        import torch
        import omni.usd
        from PIL import Image

        from benchmark.evidence import BranchLedger
        from benchmark.observation_artifacts import ObservationArtifactStore
        from algorithm.skills.observation.communication import CommunicationRuntime
        from benchmark.protocol import BranchRole, RunKey
        from algorithm.skills.contracts import SkillRequest
        from algorithm.skills.observation import (
            BuilderCameraBackend, BuilderCameraBinding, GoalCameraTelemetry, GoalCameraTools)
        from algorithm.skills.observation.runtime import CameraObservationRuntime

        # --------------------------------------------------------------
        # Locate the builder-mounted default camera prim by traversal.
        # --------------------------------------------------------------
        stage = omni.usd.get_context().get_stage()
        camera_prims = []
        for prim in stage.Traverse():
            if prim.GetName() == args.camera_name and prim.GetTypeName() == "Camera":
                path = str(prim.GetPath())
                if path.startswith(f"/World/envs/env_0/{robot_id}/"):
                    camera_prims.append(path)
        if len(camera_prims) != 1:
            report["status"] = "failed"
            report["error"] = f"expected exactly one builder color camera, found {camera_prims}"
            _write(output / "report.json", report)
            print(f"GATE_C_FAIL {report['error']}", file=sys.stderr)
            return 1
        camera_prim = camera_prims[0]

        # --------------------------------------------------------------
        # BuilderCameraBackend wired to Isaac render products/annotators.
        # --------------------------------------------------------------
        import omni.replicator.core as rep

        binding = BuilderCameraBinding(
            robot_id=robot_id, sensor_id="cam", camera_prim=camera_prim,
            publication={"source": "builder_registered_default_d455",
                         "color_camera": "Camera_OmniVision_OV9782_Color"},
            calibration={"note": "builder asset intrinsics live on the USD camera prim"})

        def create_render_product(binding_, width, height, encoding):
            import numpy as np
            product = rep.create.render_product(binding_.camera_prim, (width, height),
                                                force_new=True)
            annotator = rep.AnnotatorRegistry.get_annotator("rgb", device="cpu")
            annotator.attach([product.path])
            # Headless sims do not auto-render external replicator products;
            # drive render cycles until the annotator actually has pixels.
            for _ in range(30):
                session.base_env.sim.render()
                data = annotator.get_data()
                if data is not None and getattr(data, "size", 0) > 0:
                    break
            else:
                raise RuntimeError("rgb annotator stayed empty after warm-up renders")
            return {"product": product, "annotator": annotator,
                    "width": width, "height": height, "encoding": encoding}

        def destroy_render_product(handle):
            try:
                handle["annotator"].detach([handle["product"].path])
            finally:
                handle["product"].destroy()

        def read_annotator(handle, binding_, request, time_s):
            import numpy as np
            data = handle["annotator"].get_data()
            if data is None:
                raise RuntimeError("rgb annotator returned no data")
            # Replicator can hand out zero-copy fabric views whose reported
            # strides do not match Pillow's expectations ("tile cannot extend
            # outside image"); materialize a clean contiguous buffer first.
            pixels = np.array(data, copy=True)
            if pixels.ndim == 4 and pixels.shape[0] == 1:
                pixels = pixels[0]
            if pixels.ndim != 3 or pixels.shape[-1] < 3:
                raise RuntimeError(f"unexpected rgb annotator shape {pixels.shape}")
            pixels = np.ascontiguousarray(pixels[:, :, :3]).astype("uint8")
            image = Image.fromarray(pixels)
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            return {"payload": buffer.getvalue(), "capture_sim_time_s": float(time_s),
                    "frame_id": f"gate-c-{binding_.robot_id}-{int(round(time_s * 100))}"}

        sim_dt = float(session.base_env.physics_dt)
        backend = BuilderCameraBackend(
            {(robot_id, "cam"): binding},
            create_render_product=create_render_product,
            destroy_render_product=destroy_render_product,
            read_annotator=read_annotator,
            physics_step=lambda t: int(round(t / sim_dt)))

        public_task = {
            "task_id": "observation_gate_c_probe",
            "mission": {"scene_key": "plane"},
            "candidate_robot_pool": [{"id": robot_id}],
            "budgets": {"simulation_s": 60.0},
        }
        catalog = {robot_id: {"cam": {
            "member": "primary", "modalities": ["rgb"], "encodings": ["png"],
            "max_width": 1920, "max_height": 1080, "max_bytes": 1_000_000,
        }}}
        tools = GoalCameraTools(
            public_task, episode_id="gate-c", sensor_catalog=catalog, backend=backend,
            telemetry=GoalCameraTelemetry(CommunicationRuntime(), message_prefix="gate-c"),
            validity_s=30.0)
        ledger = BranchLedger(RunKey("gate-c", "observation.camera", "builder_default_camera",
                                     "r0", "round0", BranchRole.FACT))
        store = ObservationArtifactStore()
        runtime = CameraObservationRuntime(tools, ledger=ledger, artifact_store=store)

        def settle_and_render(steps: int):
            zero = torch.zeros((1, 3), device=session.device)
            for _ in range(steps):
                session.base_env.step({robot_id: zero})
                session.base_env.sim.render()

        def fail(message: str) -> int:
            # Failure evidence: the request row carries the exact reason the
            # capture chain rejected the frame; keep it in the artifact.
            try:
                _write(output / "tools_report_on_failure.json", tools.report())
                _write(output / "events_on_failure.json", events if "events" in dir() else [])
            except Exception as dump_error:  # noqa: BLE001 - diagnostics only
                report["failure_dump_error"] = f"{type(dump_error).__name__}: {dump_error}"
            report.update(status="failed", error=message)
            _write(output / "report.json", report)
            print(f"GATE_C_FAIL {message}", file=sys.stderr)
            return 1

        try:
            settle_and_render(60)
            sim_time = float(session.base_env.sim.current_time)
            request = SkillRequest(
                "gate-c-1", "eai.observation.camera", robot_id,
                parameters={"camera_ref": "cam", "mode": "capture",
                            "deadline": sim_time + 10.0,
                            "resolution": {"width": args.width, "height": args.height},
                            "quality": {"encoding": "png", "max_bytes": 800_000}})
            accepted = runtime.request(request, time_s=sim_time)
            if not accepted.get("accepted"):
                return fail(f"request rejected: {accepted}")
            settle_and_render(5)
            capture_time = float(session.base_env.sim.current_time)
            events = runtime.tick(capture_time)
            frames = [e for e in events if e.get("kind") == "camera_frame"]
            if len(frames) != 1:
                return fail("expected one delivered frame, got "
                            f"{len(frames)}: {[e.get('kind') for e in events]}")

            receipt = runtime.receipt("gate-c-1")
            mapping = receipt.to_mapping()
            artifact = store.get(mapping["payload_sha256"])
            image = Image.open(io.BytesIO(artifact))
            import numpy as np
            pixels = np.asarray(image.convert("RGB"), dtype="float32")
            frame_stats = {
                "size_bytes": mapping["byte_length"],
                "transport_size_bytes": mapping["transport_size_bytes"],
                "width": image.size[0], "height": image.size[1],
                "mean_channel_rgb": [round(float(pixels[..., c].mean()), 2) for c in range(3)],
                "std": round(float(pixels.std()), 2),
            }
            # The gate must prove a live scene, not an empty render.
            if mapping["byte_length"] < 1_000 or frame_stats["std"] < 5.0:
                return fail(f"frame looks empty: {frame_stats}")

            (output / "frame.png").write_bytes(artifact)
            (output / "artifact.sha256").write_text(mapping["payload_sha256"] + "\n")
            _write(output / "receipt.json", mapping)
            # Keep the persisted chain self-describing.  ``BranchLedger``
            # includes this version in each event hash, while ``to_dict``
            # intentionally stays a generic record serializer; adding the
            # same value to the Gate C artifact lets an independent reader
            # select the verifier schema without relying on the report.
            _write(output / "ledger_events.json", [
                {"schema_version": ledger.schema_version, **row.to_dict()}
                for row in ledger.events
            ])
            report.update(
                status="passed",
                robot_id=robot_id, camera_prim=camera_prim,
                capture_sim_time_s=mapping.get("capture_sim_time_s"),
                frame=frame_stats, receipt_status=mapping["status"],
                ledger_digest=ledger.digest(),
                ledger_rows=[{"sequence": r.sequence, "kind": r.kind} for r in ledger.events],
                communication_validated=True,
                execution_validated=False, scoreable=False)
            _write(output / "report.json", report)
            print(f"GATE_C_PASSED receipt={mapping['status']} bytes={mapping['byte_length']} "
                  f"camera={camera_prim} -> {output}", flush=True)
            return 0
        finally:
            tools.close()
            gpu_after = _gpu_state()
            # This hook runs before the simulator context exits; keep the
            # qualifier explicit so an in-session self PID is not mistaken
            # for a post-context cleanup proof.
            report["compute_apps_during_session_teardown"] = gpu_after["compute_apps"]
            report["gpu_uuid_during_session_teardown"] = gpu_after["uuid"]
            _write(output / "report.json", report)


def _write_postflight(output: Path) -> None:
    """Record checks made after the Isaac simulator context has exited."""
    gpu = _gpu_state()
    transmitters = _transmitter_pids()
    report_path = output / "report.json"
    if not report_path.exists():
        return
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report.update({
        "compute_apps_after_context": gpu["compute_apps"],
        "gpu_uuid_after_context": gpu["uuid"],
        "transmitter_pids_after_context": sorted(transmitters),
        # Keep the descriptive alias used by the handoff artifact readers.
        "transmitter_processes_after_context": sorted(transmitters),
        "postflight_clean": gpu["uuid"] == report.get("gpu_uuid")
        and not gpu["compute_apps"] and not transmitters,
    })
    _write(report_path, report)
    _write(output / "postflight.json", {
        "schema": "heterocoord.observation.gate_c_postflight.v1",
        "gpu_uuid": gpu["uuid"],
        "compute_apps_after_context": gpu["compute_apps"],
        "transmitter_pids_after_context": sorted(transmitters),
        "transmitter_processes_after_context": sorted(transmitters),
        "clean": report["postflight_clean"],
    })


def main() -> int:
    output = None
    if "--output" in sys.argv:
        index = sys.argv.index("--output")
        if index + 1 < len(sys.argv):
            output = Path(sys.argv[index + 1])
    result = _run_probe()
    if output is not None:
        _write_postflight(output)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
