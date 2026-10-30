"""Provider-backed resources owned by selectable EAI scenes."""

from __future__ import annotations

import argparse
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any, Mapping


OCCUPANCY_MAP = "occupancy_map"

SCENE_RESOURCE_PATHS: Mapping[str, Mapping[str, tuple[str, ...]]] = {
    scene_key: {
        OCCUPANCY_MAP: (
            f"scene/{scene_key}/{scene_key}_map.yaml",
            f"scene/{scene_key}/{scene_key}_map.png",
        )
    }
    for scene_key in (
        "plane",
        "warehouse",
        "factory",
        "airs",
        "desert",
        "hospital",
    )
}

SCENE_RESOURCE_REMOTE_PATHS = frozenset(
    f"usd/{relative_path}"
    for resources in SCENE_RESOURCE_PATHS.values()
    for paths in resources.values()
    for relative_path in paths
)


def scene_resource_relative_paths(scene_key: str, resource: str) -> tuple[str, ...]:
    """Return provider-relative paths for one declared scene resource."""
    scene = str(scene_key).strip().casefold()
    resource_key = str(resource).strip().casefold()
    try:
        resources = SCENE_RESOURCE_PATHS[scene]
    except KeyError as exc:
        raise ValueError(f"Unknown EAI scene key: {scene_key!r}") from exc
    try:
        return resources[resource_key]
    except KeyError as exc:
        raise ValueError(
            f"Scene {scene!r} has no declared resource {resource!r}; "
            f"available resources: {', '.join(sorted(resources))}"
        ) from exc


def ensure_scene_resource(
    scene_key: str,
    resource: str,
    *,
    asset_resolver: Any | None = None,
) -> tuple[Path, ...]:
    """Ensure one declared scene resource through the shared asset resolver."""
    if asset_resolver is None:
        from EAI_assets import asset_resolver

    relative_paths = scene_resource_relative_paths(scene_key, resource)
    local_paths = tuple(asset_resolver.usd_root() / relative_path for relative_path in relative_paths)
    asset_resolver.ensure_usd_files_for_paths([str(path) for path in local_paths])
    missing = tuple(path for path in local_paths if not path.is_file())
    if missing:
        raise FileNotFoundError(
            f"EAI scene resource {scene_key!r}/{resource!r} is incomplete after resolution: "
            + ", ".join(str(path) for path in missing)
        )
    return local_paths


def scene_resource_manifest() -> dict[str, dict[str, list[str]]]:
    """Return a JSON-serializable copy of the declared scene resource registry."""
    return {
        scene: {resource: list(paths) for resource, paths in resources.items()}
        for scene, resources in SCENE_RESOURCE_PATHS.items()
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python simulator.py assets",
        description="List or ensure provider-backed EAI scene resources.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    list_parser = commands.add_parser("list", help="List declared scene resources")
    list_parser.add_argument("--format", choices=("json", "text"), default="text")

    ensure_parser = commands.add_parser("ensure", help="Ensure one declared scene resource")
    ensure_parser.add_argument("--scene", required=True, choices=tuple(SCENE_RESOURCE_PATHS))
    ensure_parser.add_argument("--resource", required=True, choices=(OCCUPANCY_MAP,))
    ensure_parser.add_argument("--format", choices=("json", "paths"), default="paths")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "list":
        manifest = scene_resource_manifest()
        if args.format == "json":
            print(json.dumps(manifest, sort_keys=True))
        else:
            for scene, resources in manifest.items():
                for resource, paths in resources.items():
                    print(scene, resource, *paths)
        return 0

    try:
        # Resolver progress belongs on stderr so stdout remains machine-readable.
        with redirect_stdout(sys.stderr):
            paths = ensure_scene_resource(args.scene, args.resource)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"[EAI Assets] Scene resource request failed: {exc}", file=sys.stderr)
        return 1

    if args.format == "json":
        print(
            json.dumps(
                {
                    "scene": args.scene,
                    "resource": args.resource,
                    "paths": [str(path) for path in paths],
                    "primary_path": str(paths[0]),
                },
                sort_keys=True,
            )
        )
    else:
        print(*(str(path) for path in paths), sep="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
