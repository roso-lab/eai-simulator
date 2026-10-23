"""Fail-closed verification of external artifact bytes for v3 releases.

The release bundle intentionally stores labels and digests, not guessed file
paths.  This module verifies a caller-supplied typed manifest against a local
artifact root.  A valid result proves byte integrity only; it does not prove
evaluator authenticity or physical qualification.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping


class ArtifactVerificationError(ValueError):
    pass


class ArtifactVerificationStatus(str, Enum):
    VALID = "valid"
    MISSING = "missing"
    MISMATCH = "mismatch"
    UNSAFE_PATH = "unsafe_path"
    INVALID = "invalid"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ArtifactVerificationV3:
    label: str
    relative_path: str
    expected_sha256: str
    status: ArtifactVerificationStatus
    actual_sha256: str | None = None
    size_bytes: int | None = None
    reason: str = ""

    @property
    def valid(self) -> bool:
        return self.status is ArtifactVerificationStatus.VALID

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "relative_path": self.relative_path,
            "expected_sha256": self.expected_sha256,
            "status": self.status.value,
            "actual_sha256": self.actual_sha256,
            "size_bytes": self.size_bytes,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ArtifactVerificationReceiptV3:
    """Content-integrity receipt bound to one release bundle identity."""

    bundle_digest: str
    source_commit: str
    verifications: tuple[ArtifactVerificationV3, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.bundle_digest, str) or not self.bundle_digest.strip():
            raise ArtifactVerificationError("receipt bundle_digest is required")
        if not isinstance(self.source_commit, str) or not self.source_commit.strip():
            raise ArtifactVerificationError("receipt source_commit is required")
        if not isinstance(self.verifications, (list, tuple)) or not self.verifications:
            raise ArtifactVerificationError("receipt verifications must be nonempty")
        if any(not isinstance(item, ArtifactVerificationV3) for item in self.verifications):
            raise ArtifactVerificationError("receipt verifications must be typed")
        object.__setattr__(self, "verifications", tuple(self.verifications))

    @property
    def valid(self) -> bool:
        return all(item.valid for item in self.verifications)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "artifact-verification.v3",
            "bundle_digest": self.bundle_digest,
            "source_commit": self.source_commit,
            "verifications": [item.to_dict() for item in self.verifications],
            "valid": self.valid,
        }

    def digest(self) -> str:
        encoded = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def _manifest_entry(label: Any, entry: Any) -> tuple[str, str, str, int | None]:
    if not isinstance(label, str) or not label.strip():
        raise ArtifactVerificationError("artifact label must be a nonempty string")
    if not isinstance(entry, Mapping):
        raise ArtifactVerificationError(f"artifact manifest entry {label!r} must be a mapping")
    relative_path = entry.get("path")
    digest = entry.get("sha256")
    size_bytes = entry.get("size_bytes")
    if not isinstance(relative_path, str) or not relative_path.strip():
        raise ArtifactVerificationError(f"artifact {label!r} path is required")
    if Path(relative_path).is_absolute() or "\\" in relative_path:
        raise ArtifactVerificationError(f"artifact {label!r} path must be relative and POSIX-like")
    if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
        raise ArtifactVerificationError(f"artifact {label!r} sha256 must be a 64-hex digest")
    if size_bytes is not None and (type(size_bytes) is not int or size_bytes < 0):
        raise ArtifactVerificationError(f"artifact {label!r} size_bytes must be a nonnegative integer")
    return label.strip(), relative_path, digest.lower(), size_bytes


def verify_artifact_manifest_v3(
    manifest: Mapping[str, Mapping[str, Any]],
    root: str | Path,
    *,
    chunk_size: int = 1024 * 1024,
) -> tuple[ArtifactVerificationV3, ...]:
    """Verify manifest entries without inferring paths from digest labels."""

    if not isinstance(manifest, Mapping) or not manifest:
        raise ArtifactVerificationError("artifact manifest must be a nonempty mapping")
    if type(chunk_size) is not int or chunk_size <= 0:
        raise ArtifactVerificationError("chunk_size must be a positive integer")
    root_path = Path(root)
    try:
        root_resolved = root_path.resolve(strict=True)
    except OSError as exc:
        raise ArtifactVerificationError("artifact root is unavailable") from exc
    if not root_resolved.is_dir():
        raise ArtifactVerificationError("artifact root must be a directory")

    results: list[ArtifactVerificationV3] = []
    for raw_label, raw_entry in manifest.items():
        label, relative_path, expected, expected_size = _manifest_entry(raw_label, raw_entry)
        candidate = root_resolved / Path(relative_path)
        try:
            if candidate.is_symlink():
                results.append(ArtifactVerificationV3(label, relative_path, expected, ArtifactVerificationStatus.UNSAFE_PATH, reason="symlink_not_allowed"))
                continue
            candidate_resolved = candidate.resolve(strict=False)
            candidate_resolved.relative_to(root_resolved)
        except (OSError, ValueError):
            results.append(ArtifactVerificationV3(label, relative_path, expected, ArtifactVerificationStatus.UNSAFE_PATH, reason="path_escape"))
            continue
        try:
            before = candidate_resolved.stat()
        except FileNotFoundError:
            results.append(ArtifactVerificationV3(label, relative_path, expected, ArtifactVerificationStatus.MISSING, reason="file_missing"))
            continue
        except OSError:
            results.append(ArtifactVerificationV3(label, relative_path, expected, ArtifactVerificationStatus.UNKNOWN, reason="stat_failed"))
            continue
        if not candidate_resolved.is_file():
            results.append(ArtifactVerificationV3(label, relative_path, expected, ArtifactVerificationStatus.UNSAFE_PATH, reason="not_regular_file"))
            continue
        if expected_size is not None and before.st_size != expected_size:
            results.append(ArtifactVerificationV3(label, relative_path, expected, ArtifactVerificationStatus.MISMATCH, size_bytes=before.st_size, reason="size_mismatch"))
            continue
        digest = hashlib.sha256()
        try:
            with candidate_resolved.open("rb") as handle:
                while True:
                    block = handle.read(chunk_size)
                    if not block:
                        break
                    digest.update(block)
            after = candidate_resolved.stat()
        except OSError:
            results.append(ArtifactVerificationV3(label, relative_path, expected, ArtifactVerificationStatus.UNKNOWN, reason="read_failed"))
            continue
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            results.append(ArtifactVerificationV3(label, relative_path, expected, ArtifactVerificationStatus.UNKNOWN, size_bytes=after.st_size, reason="file_changed_during_hash"))
            continue
        actual = digest.hexdigest()
        status = ArtifactVerificationStatus.VALID if actual == expected else ArtifactVerificationStatus.MISMATCH
        results.append(ArtifactVerificationV3(label, relative_path, expected, status, actual_sha256=actual, size_bytes=after.st_size, reason="" if status is ArtifactVerificationStatus.VALID else "sha256_mismatch"))
    return tuple(results)


__all__ = [
    "ArtifactVerificationError",
    "ArtifactVerificationStatus",
    "ArtifactVerificationV3",
    "ArtifactVerificationReceiptV3",
    "verify_artifact_manifest_v3",
]
