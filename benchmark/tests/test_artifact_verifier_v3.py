from __future__ import annotations

import hashlib

import pytest

from benchmark.artifact_verifier_v3 import (
    ArtifactVerificationError,
    ArtifactVerificationStatus,
    verify_artifact_manifest_v3,
)


def _entry(path: str, content: bytes) -> dict[str, object]:
    return {"path": path, "sha256": hashlib.sha256(content).hexdigest(), "size_bytes": len(content)}


def test_artifact_manifest_verifies_exact_bytes(tmp_path):
    content = b"receipt-v3"
    (tmp_path / "receipt.bin").write_bytes(content)
    results = verify_artifact_manifest_v3({"receipt": _entry("receipt.bin", content)}, tmp_path)
    assert len(results) == 1
    assert results[0].status is ArtifactVerificationStatus.VALID
    assert results[0].valid


def test_artifact_manifest_detects_mutation_and_missing_file(tmp_path):
    content = b"receipt-v3"
    path = tmp_path / "receipt.bin"
    path.write_bytes(content)
    manifest = {"receipt": _entry("receipt.bin", content)}
    path.write_bytes(b"mutated")
    assert verify_artifact_manifest_v3(manifest, tmp_path)[0].status is ArtifactVerificationStatus.MISMATCH
    path.unlink()
    assert verify_artifact_manifest_v3(manifest, tmp_path)[0].status is ArtifactVerificationStatus.MISSING


def test_artifact_manifest_rejects_escape_directory_and_symlink(tmp_path):
    content = b"receipt-v3"
    (tmp_path / "receipt.bin").write_bytes(content)
    (tmp_path / "folder").mkdir()
    outside = tmp_path.parent / "outside-heterocoord-v3.bin"
    outside.write_bytes(content)
    try:
        (tmp_path / "link.bin").symlink_to(tmp_path / "receipt.bin")
        results = verify_artifact_manifest_v3(
            {
                "escape": _entry("../outside-heterocoord-v3.bin", content),
                "directory": _entry("folder", content),
                "symlink": _entry("link.bin", content),
            },
            tmp_path,
        )
        assert {item.status for item in results} == {ArtifactVerificationStatus.UNSAFE_PATH}
    finally:
        outside.unlink(missing_ok=True)


def test_artifact_manifest_rejects_malformed_inputs(tmp_path):
    with pytest.raises(ArtifactVerificationError, match="64-hex"):
        verify_artifact_manifest_v3({"receipt": {"path": "receipt.bin", "sha256": "bad"}}, tmp_path)
    with pytest.raises(ArtifactVerificationError, match="nonempty mapping"):
        verify_artifact_manifest_v3({}, tmp_path)
    with pytest.raises(ArtifactVerificationError, match="chunk_size"):
        verify_artifact_manifest_v3({"receipt": _entry("receipt.bin", b"x")}, tmp_path, chunk_size=0)
