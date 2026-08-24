#!/usr/bin/env python3
"""Validate release links and version-domain wording in public README files."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RELEASE_NAME = "v0.1.0-beta.1"
SOURCE_TAG_URL = f"https://rosolab.com/roso-lab/eai-simulator/-/tags/{RELEASE_NAME}"
GITHUB_RELEASE_URL = f"https://github.com/roso-lab/eai-simulator/releases/tag/{RELEASE_NAME}"
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
        if GITHUB_RELEASE_URL in text:
            return _fail(f"{rel} links to the GitHub Release page before a release object exists")
        if SOURCE_TAG_URL not in text:
            return _fail(f"{rel} does not link to the {RELEASE_NAME} source tag")
        if not re.search(rf"release-{re.escape(RELEASE_NAME).replace('\\-', '--')}", text):
            return _fail(f"{rel} is missing the {RELEASE_NAME} release badge")
    print(f"PASS: README release links point to the {RELEASE_NAME} GitLab source tag")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
