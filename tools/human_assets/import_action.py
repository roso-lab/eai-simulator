#!/usr/bin/env python3
"""Convert and publish one animated GLTF/GLB human action."""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
EAI_ASSETS_SOURCE = REPO_ROOT / "source/EAI_assets"
if str(EAI_ASSETS_SOURCE) not in sys.path:
    sys.path.insert(0, str(EAI_ASSETS_SOURCE))

from EAI_assets.humans.action_authoring import CANONICAL_PROFILES

_KNOWN_EXTENSIONS = {".gltf", ".glb"}
class ActionImportError(ValueError):
    """Raised when an action cannot be imported."""


def _canonical_joint_names(profile: str) -> tuple[str, ...]:
    if profile not in CANONICAL_PROFILES:
        raise ActionImportError(f"unknown source profile: {profile}")
    return CANONICAL_PROFILES[profile]


def inspect_gltf_source(source_path: Path) -> tuple[dict[str, Any], tuple[Path, ...]]:
    from tools.human_assets.convert_gltf_assets import (
        _read_gltf_document,
        gltf_dependency_records,
        preflight_gltf_dependencies,
    )

    source_root = source_path.parent
    try:
        preflight_gltf_dependencies(source_path, source_root=source_root)
        _, document, _ = _read_gltf_document(
            source_path, source_root=source_root
        )
        records = gltf_dependency_records(source_path, source_root=source_root)
    except Exception as exc:
        raise ActionImportError(f"could not inspect glTF source: {exc}") from exc
    dependencies = tuple(source_root / record["path"] for record in records)
    return dict(document), dependencies


def build_import_plan(
    source_path: Path,
    *,
    action_id: str,
    source_profile: str = "smplx_70",
) -> dict[str, Any]:
    """Validate one animated GLTF source and preserve it for conversion."""
    source_path = Path(source_path).resolve()
    suffix = source_path.suffix.lower()
    if suffix not in _KNOWN_EXTENSIONS:
        raise ActionImportError(
            f"unrecognised source format '{suffix}'; expected glTF (.gltf / .glb)"
        )
    if not source_path.is_file():
        raise ActionImportError(f"glTF source not found: {source_path}")

    _canonical_joint_names(source_profile)
    document, dependencies = inspect_gltf_source(source_path)
    animations = document.get("animations", [])
    if not isinstance(animations, list) or not animations or not any(
        isinstance(animation, dict) and animation.get("channels")
        for animation in animations
    ):
        raise ActionImportError(f"glTF source has no animations: {source_path}")

    return {
        "action_id": str(action_id),
        "source": source_path.as_posix(),
        "source_profile": source_profile,
        "dependencies": [path.as_posix() for path in dependencies],
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Path to .gltf or .glb clip")
    parser.add_argument("--action-id", required=True, help="Action identifier")
    parser.add_argument(
        "--profile",
        default="smplx_70",
        choices=list(CANONICAL_PROFILES),
        help="Source skeleton profile",
    )
    parser.add_argument("--human-root", type=Path, required=True)
    parser.add_argument("--replace", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        plan = build_import_plan(
            args.source,
            action_id=args.action_id,
            source_profile=args.profile,
        )
    except ActionImportError as exc:
        print(f"error: {exc}", file=__import__("sys").stderr)  # noqa: T201
        return 1

    from isaacsim import SimulationApp

    simulation_app = SimulationApp({"headless": True})
    try:
        from tools.human_assets.convert_gltf_assets import (
            ConversionCandidate,
            IsaacAssetConverter,
            validate_converted_usd,
        )
        from EAI_assets.humans.action_authoring import HumanActionPublisher

        with tempfile.TemporaryDirectory(prefix="eai-human-action-") as temporary:
            candidate = ConversionCandidate(
                id=args.action_id,
                kind="synbody_motion",
                source=Path(plan["source"]),
                output=Path(temporary) / "converted.usd",
                profile=args.profile,
            )
            IsaacAssetConverter()(candidate)
            validate_converted_usd(candidate)
            result = HumanActionPublisher(args.human_root).publish_usd_action(
                candidate.output,
                action_id=args.action_id,
                source_profile=args.profile,
                replace=args.replace,
            )
        print(result.animation_path.as_posix())  # noqa: T201
        return 0
    except Exception as exc:
        print(f"error: {exc}", file=__import__("sys").stderr)  # noqa: T201
        return 1
    finally:
        simulation_app.close()


if __name__ == "__main__":
    raise SystemExit(main())
