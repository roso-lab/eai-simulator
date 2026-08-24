#!/usr/bin/env python3
"""Validate public release links in README files."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RELEASE_NAME = "v0.1.0-beta.1"
PUBLIC_RELEASE_URL = f"https://github.com/roso-lab/eai-simulator/releases/tag/{RELEASE_NAME}"
INTERNAL_SOURCE_TAG_URL = f"https://rosolab.com/roso-lab/eai-simulator/-/tags/{RELEASE_NAME}"
BADGE_RELEASE_NAME = re.escape(RELEASE_NAME).replace(r"\-", "--")
README_FILES = [ROOT / "README.md", ROOT / "docs" / "README.zh-CN.md"]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _fail(message: str) -> int:
    print(f"FAIL: {message}", file=sys.stderr)
    return 1


def main() -> int:
    for path in README_FILES:
        text = _read(path)
        rel = path.relative_to(ROOT)
        if INTERNAL_SOURCE_TAG_URL in text:
            return _fail(f"{rel} links to the internal GitLab source tag instead of the public GitHub release")
        if PUBLIC_RELEASE_URL not in text:
            return _fail(f"{rel} does not link to the {RELEASE_NAME} public GitHub release")
        if not re.search(rf"release-{BADGE_RELEASE_NAME}", text):
            return _fail(f"{rel} is missing the {RELEASE_NAME} release badge")
    print(f"PASS: README release links point to the {RELEASE_NAME} public GitHub release")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
