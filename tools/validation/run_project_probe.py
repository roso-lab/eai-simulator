"""Supervise a native probe; a Kit exit code of zero is not success evidence."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

from tools.project_runtime import PROJECT_ROOT, cleanup_isaac_telemetry, runtime_environment


MODULES = {
    "navigation": "tools.validation.project_local_node_route",
    "resource-calibration": "tools.validation.project_local_resource_calibration",
    "checkpoint": "tools.validation.project_local_checkpoint",
    "reconstruction": "tools.validation.project_local_reconstruction",
    "active-reconstruction": "tools.validation.project_local_task_reconstruction",
}


def _read_session_close_marker(path):
    try:
        marker = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(marker, dict):
        return None
    return marker


def assess_receipt(path, returncode, session_close_path=None):
    try:
        result = json.loads(path.read_text())
    except (OSError, ValueError):
        return {"passed": False, "reason": "missing_or_invalid_receipt", "process_returncode": returncode}
    marker = _read_session_close_marker(session_close_path) if session_close_path is not None else None
    marker_closed = bool(
        marker is not None
        and marker.get("cleanup_callbacks_complete") is True
        and marker.get("app_close_requested") is True
        and marker.get("status") in {"app_close_requested", "app_close_returned"}
    )
    session_closed = result.get("session_close_observed") is True or marker_closed
    passed = (returncode == 0 and result.get("passed") is True
              and result.get("status") in {"completed", "verified", "recorded"}
              and session_closed)
    reason = "receipt_and_process_pass" if passed else (
        "session_close_not_observed" if returncode == 0 and result.get("passed") is True
        and result.get("status") in {"completed", "verified", "recorded"}
        and not session_closed else "probe_failed_or_incomplete")
    return {"passed": passed, "reason": reason,
            "process_returncode": returncode, "receipt_status": result.get("status"),
            "receipt_passed": result.get("passed"), "session_close_observed": session_closed,
            "paper_score_eligible": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("probe", choices=MODULES)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--branch", choices=("continue", "hold"))
    parser.add_argument("--sham-evidence", type=Path)
    parser.add_argument("--device", default="cuda:0")
    args = parser.parse_args(argv)
    output = args.output.resolve()
    if not output.is_relative_to(PROJECT_ROOT) or output.exists():
        parser.error("output must be a new project-local directory")
    if args.reference is not None and args.probe not in {"reconstruction", "active-reconstruction"}:
        parser.error("reference applies only to reconstruction")
    if args.probe != "navigation" and args.device != "cuda:0":
        parser.error("--device is currently supported only for the GPU navigation probe")
    command = [sys.executable, "-m", MODULES[args.probe], "--output", str(output)]
    if args.probe == "navigation":
        if not args.device.startswith("cuda"):
            parser.error("formal project probes require a CUDA device; CPU is diagnostic-only")
        command.extend(("--device", args.device))
    if args.reference is not None:
        reference = args.reference.resolve()
        if not reference.is_relative_to(PROJECT_ROOT) or not reference.is_dir():
            parser.error("reference must be an existing project-local journal")
        command.extend(("--reference", str(reference)))
    if args.branch is not None or args.sham_evidence is not None:
        if args.probe != "active-reconstruction" or args.branch is None or args.sham_evidence is None or args.reference is None:
            parser.error("branch requires active-reconstruction, reference and sham evidence")
        sham = args.sham_evidence.resolve()
        if not sham.is_relative_to(PROJECT_ROOT) or not sham.is_file():
            parser.error("sham evidence must be an existing project-local receipt")
        command.extend(("--branch", args.branch, "--sham-evidence", str(sham)))
    output.mkdir(parents=True)
    # The child owns a fresh subdirectory; supervisor logs survive native exits.
    child = output / "run"
    command[command.index("--output") + 1] = str(child)
    with (output / "native.log").open("x") as log:
        child_env = runtime_environment()
        if args.probe == "navigation":
            child_env["EAI_FACTORY_GPU_PHYSICS"] = "1"
        try:
            process = subprocess.run(command, cwd=PROJECT_ROOT, env=child_env,
                                     stdout=log, stderr=subprocess.STDOUT, timeout=300)
            result = assess_receipt(
                child / "result.json", process.returncode,
                child / "session_close.json",
            )
        except subprocess.TimeoutExpired:
            result = {"passed": False, "reason": "native_timeout", "paper_score_eligible": False}
        finally:
            result = {**result, "telemetry_cleanup": cleanup_isaac_telemetry()}
    result["command"] = command
    (output / "supervisor.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
