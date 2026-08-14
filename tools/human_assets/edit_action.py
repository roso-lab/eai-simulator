#!/usr/bin/env python3
"""Create and update human action draft files."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
EAI_ASSETS_SOURCE = REPO_ROOT / "source/EAI_assets"
if str(EAI_ASSETS_SOURCE) not in sys.path:
    sys.path.insert(0, str(EAI_ASSETS_SOURCE))

from EAI_assets.humans.action_authoring import CANONICAL_PROFILES


def build_draft(
    *,
    action_id: str,
    fps: float = 30.0,
    duration: float,
    profile: str = "smplx_70",
    label: str | None = None,
    loop: bool = False,
) -> dict[str, Any]:
    """Create a minimal action draft with start/end identity keyframes.

    The caller adds per-keyframe joint quaternions before publishing.
    """
    if profile not in CANONICAL_PROFILES:
        raise ValueError(f"unknown source profile: {profile}")
    canonical_joints = list(CANONICAL_PROFILES[profile])

    identity = [0.0, 0.0, 0.0, 1.0]
    identity_joints = {joint: identity for joint in canonical_joints}

    return {
        "version": 1,
        "action_id": str(action_id),
        "source_profile": profile,
        "label": label,
        "fps": float(fps),
        "loop": bool(loop),
        "keyframes": [
            {"time": 0.0, "joints": dict(identity_joints)},
            {"time": float(duration), "joints": dict(identity_joints)},
        ],
    }


def save_draft(path: Path, draft: dict[str, Any]) -> None:
    """Atomically write an action draft to *path*."""
    path = Path(path)
    content = json.dumps(draft, indent=2, sort_keys=True, ensure_ascii=False)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="Create a new empty keyframe draft")
    init.add_argument("--action-id", required=True, help="Action identifier")
    init.add_argument("--duration", type=float, required=True, help="Action duration in seconds")
    init.add_argument("--fps", type=float, default=30.0)
    init.add_argument("--profile", default="smplx_70", choices=list(CANONICAL_PROFILES))
    init.add_argument("--label")
    init.add_argument("--loop", action="store_true")
    init.add_argument("output", type=Path, help="Output file (.json)")

    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if args.command == "init":
        draft = build_draft(
            action_id=args.action_id,
            fps=args.fps,
            duration=args.duration,
            profile=args.profile,
            label=args.label,
            loop=args.loop,
        )
        save_draft(args.output, draft)
        print(f"draft written to {args.output}")  # noqa: T201
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
