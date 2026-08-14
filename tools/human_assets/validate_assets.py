#!/usr/bin/env python3
"""Structural validation of every enabled asset and motion in the human catalog.

Produces a deterministic JSON report suitable for automated acceptance.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
EAI_ASSETS_SOURCE = REPO_ROOT / "source/EAI_assets"
if str(EAI_ASSETS_SOURCE) not in sys.path:
    sys.path.insert(0, str(EAI_ASSETS_SOURCE))

from EAI_assets.humans import HumanAssetRegistry


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate(manifest_path: Path) -> dict[str, Any]:
    registry = HumanAssetRegistry.load(
        manifest_path,
        file_policy="require",
    )
    human_root = registry.human_root

    assets = list(registry.assets())
    seen_motions: dict[str, Any] = {}
    for asset in assets:
        for motion_id in asset.motions:
            if motion_id not in seen_motions:
                try:
                    seen_motions[motion_id] = registry.motion(motion_id)
                except KeyError:
                    pass

    validated_assets = []
    for asset in assets:
        path = human_root / asset.usd_path
        validated_assets.append(
            {
                "id": asset.id,
                "activity_type": asset.activity_type,
                "articulated": asset.articulated,
                "path_following": asset.path_following,
                "can_play_actions": asset.can_play_actions,
                "motions": list(asset.motions),
                "sha256": _sha256(path) if path.is_file() else None,
                "skeleton_signature": asset.skeleton_signature,
                "scale": list(asset.scale),
                "yaw_offset": asset.yaw_offset,
                "content_up_axis": asset.content_up_axis,
            }
        )

    selectable_assets = [a for a in validated_assets if a["path_following"]]
    articulated_assets = [a for a in validated_assets if a["articulated"]]

    validated_motions = []
    for motion in seen_motions.values():
        path = human_root / motion.usd_path
        validated_motions.append(
            {
                "id": motion.id,
                "semantic": motion.semantic,
                "duration": motion.duration,
                "loop": motion.loop,
                "sha256": _sha256(path) if path.is_file() else None,
            }
        )

    return {
        "version": 1,
        "manifest_path": str(manifest_path.resolve()),
        "selectable_unique_assets": len(selectable_assets),
        "articulated_assets": len(articulated_assets),
        "total_assets": len(validated_assets),
        "total_motions": len(validated_motions),
        "assets": validated_assets,
        "motions": validated_motions,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", "-o", type=Path)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    report = validate(args.manifest)
    content = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(content, encoding="utf-8")
    else:
        print(content)  # noqa: T201
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
