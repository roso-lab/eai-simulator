"""Hash-bound resolver for the published ``room_06`` scene bundle."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

ROOM06_USD_RELATIVE_PATH = "scene/Living and public space/room_06/room_06.usda"
EXPECTED_ROOM06_USD_SHA256 = (
    "e67affcbd6d67941feb8bc7728d6be4cd74ad63d414a8b65db771337b76e300a"
)


@dataclass(frozen=True)
class Room06SceneAsset:
    path: Path
    sha256: str
    size_bytes: int


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def resolve_room06_scene(
    path: str | Path | None = None,
    *,
    expected_sha256: str = EXPECTED_ROOM06_USD_SHA256,
) -> Room06SceneAsset:
    """Resolve the logical provider path and reject missing or changed USD bytes."""

    from EAI_assets.asset_resolver import resolve_local_asset_path

    candidate = (
        Path(path).expanduser().resolve()
        if path is not None
        else resolve_local_asset_path(ROOM06_USD_RELATIVE_PATH).resolve()
    )
    if not candidate.is_file():
        raise FileNotFoundError(
            "room_06 USD is missing; install the scene bundle under "
            f"{ROOM06_USD_RELATIVE_PATH!r} in EAI_USD_ROOT: {candidate}"
        )
    actual = _sha256(candidate)
    if actual != expected_sha256:
        raise RuntimeError(
            "room_06 USD digest mismatch: "
            f"expected {expected_sha256}, observed {actual} at {candidate}"
        )
    return Room06SceneAsset(
        path=candidate,
        sha256=actual,
        size_bytes=candidate.stat().st_size,
    )


__all__ = [
    "EXPECTED_ROOM06_USD_SHA256",
    "ROOM06_USD_RELATIVE_PATH",
    "Room06SceneAsset",
    "resolve_room06_scene",
]
