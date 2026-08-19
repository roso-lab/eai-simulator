"""Download and verify the db-CBS double-integrator motion primitives."""

from __future__ import annotations

import argparse
import base64
import hashlib
import os
from pathlib import Path
import tempfile
from urllib.request import Request, urlopen


MOTION_FILENAME = "double_integrator_0_sorted.msgpack"
MOTION_URL = (
    "https://tubcloud.tu-berlin.de/public.php/webdav/"
    "double_integrator_0_sorted.msgpack"
)
MOTION_WEBDAV_USERNAME = "CijbRaJadf6JwH3"
MOTION_SIZE = 1_192_789
MOTION_SHA256 = "66b6a39765d554105d9ecd6b1bd2244673568e116c749871fe8936338d83454e"
MOTION_PATH = Path(__file__).resolve().parent / "native" / "motions" / MOTION_FILENAME


class MotionPrimitiveError(RuntimeError):
    """Raised when the motion-primitive payload cannot be installed safely."""


def _file_metadata(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def _validate(path: Path, *, expected_size: int, expected_sha256: str) -> None:
    actual_size, actual_sha256 = _file_metadata(path)
    if actual_size != expected_size or actual_sha256 != expected_sha256.lower():
        raise MotionPrimitiveError(
            f"Invalid motion primitives at {path}: expected {expected_size} bytes "
            f"and SHA-256 {expected_sha256.lower()}, got {actual_size} bytes and "
            f"SHA-256 {actual_sha256}."
        )


def fetch_motion_primitives(
    *,
    target: Path = MOTION_PATH,
    url: str = MOTION_URL,
    expected_size: int = MOTION_SIZE,
    expected_sha256: str = MOTION_SHA256,
    username: str | None = MOTION_WEBDAV_USERNAME,
    password: str = "",
    timeout: float = 60.0,
) -> Path:
    """Ensure that *target* exists and exactly matches the recorded payload."""

    target = Path(target)
    if target.exists():
        _validate(
            target,
            expected_size=expected_size,
            expected_sha256=expected_sha256,
        )
        return target

    target.parent.mkdir(parents=True, exist_ok=True)
    request = Request(url, headers={"User-Agent": "EAI-Simulator/db-CBS"})
    if username is not None:
        credentials = base64.b64encode(f"{username}:{password}".encode()).decode()
        request.add_header("Authorization", f"Basic {credentials}")

    temporary_path: Path | None = None
    try:
        file_descriptor, temporary_name = tempfile.mkstemp(
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
        )
        temporary_path = Path(temporary_name)
        with os.fdopen(file_descriptor, "wb") as output, urlopen(
            request, timeout=timeout
        ) as response:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)

        _validate(
            temporary_path,
            expected_size=expected_size,
            expected_sha256=expected_sha256,
        )
        os.replace(temporary_path, target)
        temporary_path = None
    except MotionPrimitiveError:
        raise
    except Exception as exc:
        raise MotionPrimitiveError(
            f"Unable to download motion primitives from {url}: {exc}"
        ) from exc
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)

    return target


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Download and verify the db-CBS motion primitives."
    )
    parser.add_argument("--target", type=Path, default=MOTION_PATH)
    args = parser.parse_args()

    try:
        target = fetch_motion_primitives(target=args.target)
    except MotionPrimitiveError as exc:
        parser.exit(1, f"error: {exc}\n")
    print(f"Verified db-CBS motion primitives: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
