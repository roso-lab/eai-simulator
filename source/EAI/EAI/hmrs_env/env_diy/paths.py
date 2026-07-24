from __future__ import annotations

from pathlib import Path


def find_repo_root(start: Path | None = None) -> Path:
    current = (start or Path(__file__)).resolve()
    search_root = current if current.is_dir() else current.parent
    for candidate in (search_root, *search_root.parents):
        if (candidate / "source" / "EAI").is_dir() and (candidate / "source" / "EAI_hmrs").is_dir():
            return candidate
    raise RuntimeError(f"Could not locate eai-simulator repository root from {current}")


REPO_ROOT = find_repo_root()
