"""Project-local process bootstrap for HeteroCoord experiments.

The shared ``env_isaaclab`` environment may contain editable EAI installs from
another checkout.  This bootstrap does not modify that environment.  It gives
each child process an explicit HeteroCoord source/asset boundary and always
runs it from this repository root.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Mapping, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOTS = (
    PROJECT_ROOT / "source" / "EAI",
    PROJECT_ROOT / "source" / "EAI_assets",
    PROJECT_ROOT / "source" / "EAI_hmrs",
)
ARTIFACT_ROOT = PROJECT_ROOT / "artifacts" / "project_runtime"
RUNTIME_TMP_ROOT = PROJECT_ROOT / "tmp" / "project_runtime"
ASSET_CACHE_ROOTS = {
    "EAI_RUNTIME_ASSET_CACHE": RUNTIME_TMP_ROOT / "runtime-assets",
    "EAI_LIDAR_ASSET_CACHE": RUNTIME_TMP_ROOT / "lidar-assets",
}


def _is_external_eai_source(entry: str) -> bool:
    """Identify another checkout's EAI source entry without naming its path."""

    try:
        path = Path(entry).expanduser().resolve()
    except (OSError, RuntimeError):
        return False
    if path.is_relative_to(PROJECT_ROOT):
        return False
    return path.name in {"EAI", "EAI_assets", "EAI_hmrs"} and path.parent.name == "source"


def _validate_project_layout() -> None:
    root = PROJECT_ROOT.resolve()
    required = (*SOURCE_ROOTS, PROJECT_ROOT / "usd",
                PROJECT_ROOT / "source" / "EAI_assets" / "EAI_assets" / "controller")
    for path in required:
        resolved = path.resolve()
        if not path.is_dir() or not resolved.is_relative_to(root):
            raise RuntimeError(
                f"project runtime path is missing or escapes HeteroCoord: {path} -> {resolved}")
    for output in (ARTIFACT_ROOT, RUNTIME_TMP_ROOT, *ASSET_CACHE_ROOTS.values()):
        resolved = output.resolve()
        if not resolved.is_relative_to(root):
            raise RuntimeError(
                f"project output path escapes HeteroCoord: {output} -> {resolved}")


def runtime_environment(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return an isolated environment that resolves EAI from this checkout."""

    _validate_project_layout()
    env = dict(os.environ if base is None else base)
    inherited = [
        item for item in env.get("PYTHONPATH", "").split(os.pathsep)
        if item and not _is_external_eai_source(item)
    ]
    local = [str(PROJECT_ROOT), *(str(path) for path in SOURCE_ROOTS)]
    env["PYTHONPATH"] = os.pathsep.join(dict.fromkeys((*local, *inherited)))
    env["EAI_USD_ROOT"] = str(PROJECT_ROOT / "usd")
    env["EAI_CONTROLLER_ROOT"] = str(
        PROJECT_ROOT / "source" / "EAI_assets" / "EAI_assets" / "controller")
    env["EAI_ASSETS_AUTO_DOWNLOAD"] = "0"
    # Optional NVD props are admitted only through a project-owned shadow root.
    # Never inherit a machine-global asset override into a formal run.
    env.pop("EAI_NVD_ASSET_ROOT", None)
    env["HETEROCOORD_PROJECT_ROOT"] = str(PROJECT_ROOT)
    env["HETEROCOORD_ARTIFACT_ROOT"] = str(ARTIFACT_ROOT)
    env["TMPDIR"] = str(RUNTIME_TMP_ROOT)
    env.update({name: str(path) for name, path in ASSET_CACHE_ROOTS.items()})
    return env


def activate_project_runtime(*, enforce_cwd: bool = True) -> dict[str, str]:
    """Activate the same boundary in the current process and return its env.

    ``enforce_cwd`` is enabled for experiment entry points.  Import/resource
    helpers can disable it while still receiving the exact project-local
    source and asset environment, so a caller's already-resolved relative
    paths are not silently rebound to the repository root.
    """

    env = runtime_environment()
    root = PROJECT_ROOT.resolve()
    external = []
    for package in ("EAI", "EAI_assets", "EAI_hmrs"):
        module = sys.modules.get(package)
        origin = getattr(module, "__file__", None) if module is not None else None
        if origin is None:
            continue
        try:
            local = Path(origin).resolve().is_relative_to(root)
        except OSError:
            local = False
        if not local:
            external.append(f"{package}={origin}")
    if external:
        raise RuntimeError(
            "external EAI packages are already loaded; start a fresh project-runtime "
            "process instead of mixing live package objects: " + ", ".join(external))
    if enforce_cwd:
        os.chdir(PROJECT_ROOT)
    # ``dict.update`` cannot remove inherited overrides that are intentionally
    # absent from the sanitized child environment.  Clear this machine-global
    # asset escape hatch before applying the project-local values.
    os.environ.pop("EAI_NVD_ASSET_ROOT", None)
    os.environ.update(env)
    for path in reversed((PROJECT_ROOT, *SOURCE_ROOTS)):
        value = str(path)
        while value in sys.path:
            sys.path.remove(value)
        sys.path.insert(0, value)
    importlib.invalidate_caches()
    ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
    RUNTIME_TMP_ROOT.mkdir(parents=True, exist_ok=True)
    return env


def provenance_report() -> dict[str, object]:
    """Import all EAI packages and fail if any resolves outside this project."""

    env = activate_project_runtime()
    packages: dict[str, str] = {}
    for name in ("EAI", "EAI_assets", "EAI_hmrs"):
        module = importlib.import_module(name)
        origin = Path(module.__file__).resolve()
        if not origin.is_relative_to(PROJECT_ROOT):
            raise RuntimeError(f"{name} resolved outside HeteroCoord: {origin}")
        packages[name] = str(origin)
    return {
        "schema": "heterocoord.project_runtime.v1",
        "project_root": str(PROJECT_ROOT),
        "cwd": str(Path.cwd().resolve()),
        "python": str(Path(sys.executable).resolve()),
        "packages": packages,
        "asset_roots": {
            "usd": env["EAI_USD_ROOT"],
            "controllers": env["EAI_CONTROLLER_ROOT"],
            **{name: env[name] for name in ASSET_CACHE_ROOTS},
        },
        "artifact_root": env["HETEROCOORD_ARTIFACT_ROOT"],
        "external_editable_environment_modified": False,
    }


def gpu_runtime_report(device: str) -> dict[str, object]:
    """Collect immutable GPU identity required by formal Isaac evidence."""
    requested = str(device)
    if not requested.startswith("cuda"):
        raise RuntimeError(f"formal Isaac evidence requires a CUDA device, got {requested!r}")
    import torch
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is unavailable for the requested formal Isaac run")
    index = torch.cuda.current_device()
    if ":" in requested:
        try:
            index = int(requested.rsplit(":", 1)[1])
        except ValueError as exc:
            raise RuntimeError(f"invalid CUDA device {requested!r}") from exc
    if index < 0 or index >= torch.cuda.device_count():
        raise RuntimeError(f"CUDA device index {index} is unavailable")
    props = torch.cuda.get_device_properties(index)
    result = subprocess.run(
        ["nvidia-smi", "--query-gpu=uuid", "--format=csv,noheader,nounits", "-i", str(index)],
        check=False, capture_output=True, text=True, timeout=10,
    )
    uuid = result.stdout.strip().splitlines()[0].strip() if result.returncode == 0 and result.stdout.strip() else ""
    if not uuid:
        raise RuntimeError("GPU UUID could not be collected; refusing unidentifiable formal evidence")
    return {
        "requested_device": requested, "actual_device": f"cuda:{index}",
        "cuda_visible_device_count": torch.cuda.device_count(), "gpu_index": index,
        "gpu_name": str(props.name), "gpu_uuid": uuid,
        "memory_total_bytes": int(props.total_memory), "cuda_version": str(torch.version.cuda or ""),
    }


def cleanup_isaac_telemetry() -> dict[str, object]:
    """Stop the known NVIDIA telemetry leak after an Isaac run."""
    killed = subprocess.run(
        ["pkill", "-9", "-f", "omni.telemetry.transmitter"],
        check=False, capture_output=True, text=True,
    )
    remaining = subprocess.run(
        ["pgrep", "-af", "omni.telemetry.transmitter"],
        check=False, capture_output=True, text=True,
    )
    return {"command": "pkill -9 -f omni.telemetry.transmitter",
            "returncode": killed.returncode, "remaining": remaining.stdout.strip()}


def _write_json(path: Path, payload: Mapping[str, object]) -> None:
    resolved = path.expanduser().resolve()
    if not resolved.is_relative_to(PROJECT_ROOT):
        raise ValueError("runtime reports must be written inside HeteroCoord")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    temporary = resolved.with_suffix(resolved.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(resolved)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    doctor = subparsers.add_parser("doctor", help="verify project-local EAI imports")
    doctor.add_argument("--output", type=Path)
    execute = subparsers.add_parser("exec", help="run a command from the project boundary")
    execute.add_argument("argv", nargs=argparse.REMAINDER)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "doctor":
        report = provenance_report()
        encoded = json.dumps(report, indent=2, sort_keys=True)
        print(encoded)
        if args.output is not None:
            _write_json(args.output, report)
        return 0

    command = list(args.argv)
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        raise SystemExit("exec requires a command")
    if command[0] in {"python", "python3"}:
        command[0] = sys.executable
    env = runtime_environment()
    ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
    RUNTIME_TMP_ROOT.mkdir(parents=True, exist_ok=True)
    return subprocess.run(command, cwd=PROJECT_ROOT, env=env, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
