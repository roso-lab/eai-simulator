#!/usr/bin/env python3
"""Create a local, content-addressed room06 full-task probe input.

The generated request is ready for ``prepare_full_task`` once a live
``room06_task_driver`` is registered in the Isaac environment.  This script
does not run Isaac or change any formal gate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.validation.room06_livt04_gpu_probe import (  # noqa: E402
    FORMAL_GATES,
    INPUT_SCHEMA,
    canonical_digest,
    directory_digest,
)


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def file_ref(path: Path) -> dict[str, str]:
    path = path.resolve()
    return {"path": str(path), "sha256": file_digest(path)}


def hook_ref(path: Path, factory: str) -> dict[str, str]:
    value = file_ref(path)
    value["factory"] = factory
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--scene-usd", type=Path, required=True)
    parser.add_argument("--semantic-root", type=Path, required=True)
    parser.add_argument("--robot-roster", type=Path, required=True)
    parser.add_argument("--binding", type=Path, required=True)
    parser.add_argument("--task-setup", type=Path, required=True)
    parser.add_argument("--hook", type=Path, required=True)
    parser.add_argument("--hook-factory", default="collect_room06_runtime_hook")
    parser.add_argument("--runtime-source", type=Path, required=True)
    parser.add_argument("--runtime-source-factory", default="create_room06_runtime_source")
    parser.add_argument("--gpu-uuid", required=True)
    parser.add_argument("--seed", type=int, default=20261028)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    scene = args.scene_usd.resolve()
    body = {
        "schema": INPUT_SCHEMA,
        "input_digest": None,
        "mode": "full_task",
        "scene_ref": {**file_ref(scene)},
        "requested_device": "cuda:0",
        "expected_gpu_uuid": args.gpu_uuid,
        "headless": True,
        "seed": args.seed,
        "diagnostic_steps": 64,
        "package_ref": {
            **file_ref(args.package),
            "package_digest": json.loads(args.package.read_text(encoding="utf-8"))["package_digest"],
        },
        "preflight_inputs": {
            "semantic_root": {"path": str(args.semantic_root.resolve()), "sha256": directory_digest(args.semantic_root)},
            "scene_usd": file_ref(scene),
            "robot_roster": file_ref(args.robot_roster),
            "binding": file_ref(args.binding),
            "task_setup": file_ref(args.task_setup),
        },
        "runtime_hook_ref": hook_ref(args.hook, args.hook_factory),
        "runtime_source_ref": hook_ref(args.runtime_source, args.runtime_source_factory),
        "formal_gates": dict(FORMAL_GATES),
    }
    body["input_digest"] = canonical_digest({key: value for key, value in body.items() if key != "input_digest"})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output.resolve()), "input_digest": body["input_digest"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
