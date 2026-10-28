#!/usr/bin/env python3
"""Build a content-addressed room06 fixed package from canonical inputs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmark.room06_fixed_package_v1 import build_room06_fixed_package


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--semantic-root", type=Path, required=True)
    parser.add_argument("--scene-usd", type=Path, required=True)
    parser.add_argument("--robot-roster", type=Path, required=True)
    parser.add_argument("--preflight-binding", type=Path, required=True)
    parser.add_argument("--task-setup", type=Path, required=True)
    parser.add_argument("--deployment-state", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--contract-artifact", type=Path, required=True)
    parser.add_argument("--case-id", default="room06-livt04-c0-r1")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    package = build_room06_fixed_package(
        semantic_root=args.semantic_root,
        scene_usd=args.scene_usd,
        robot_roster=args.robot_roster,
        preflight_binding=args.preflight_binding,
        task_setup=args.task_setup,
        deployment_state=args.deployment_state,
        catalog=args.catalog,
        contract_artifact=args.contract_artifact,
        case_id=args.case_id,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(package, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"package_digest": package["package_digest"], "output": str(args.output), "readiness": package["readiness"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
