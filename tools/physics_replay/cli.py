#!/usr/bin/env python3
"""Command-line inspection and session validation for replay episodes."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any


if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.physics_replay.replayer import PhysicsReplayer
from tools.physics_replay.store import EpisodeReader
from tools.physics_replay.types import TargetSpec


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect and validate EAI physics replay episodes."
    )
    commands = parser.add_subparsers(dest="command", required=True)

    inspect = commands.add_parser("inspect", help="inspect an episode without Isaac Sim")
    inspect.add_argument("--episode", required=True, type=Path)

    validate = commands.add_parser("validate", help="validate targets in an Isaac session")
    validate.add_argument("--episode", required=True, type=Path)
    validate.add_argument("--env", required=True, help="saved EAI environment name")
    validate.add_argument("--device", default="cuda:0")
    validate.add_argument("--num-envs", type=int, default=1)

    return parser


def _target_specs(reader: EpisodeReader) -> tuple[TargetSpec, ...]:
    specs: list[TargetSpec] = []
    for target in reader.manifest.targets:
        path = str(target.get("prim_path", "")).strip()
        channels = tuple(target.get("channels", ()))
        if not path or not channels:
            raise ValueError("manifest target is missing prim_path or channels")
        specs.append(TargetSpec(path, channels, int(target.get("env_id", 0))))
    return tuple(specs)


def _inspect(args: argparse.Namespace) -> int:
    try:
        payload = EpisodeReader(args.episode).summary()
    except (OSError, ValueError) as exc:
        print(f"replay: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _validate(args: argparse.Namespace) -> int:
    try:
        reader = EpisodeReader(args.episode)
        specs = _target_specs(reader)
        from simulator import SimulatorLaunchConfig, open_simulator_session

        config = SimulatorLaunchConfig(
            env=args.env,
            num_envs=args.num_envs,
            device=args.device,
            headless=True,
            enable_ros_bridge_extension=False,
        )
        with open_simulator_session(config) as session:
            payload = PhysicsReplayer.from_session(session, specs).validate_session(reader)
    except (OSError, ValueError, RuntimeError, ImportError) as exc:
        print(f"replay: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "inspect":
        return _inspect(args)
    if args.command == "validate":
        return _validate(args)
    raise ValueError(f"unknown command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
