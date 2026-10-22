"""Durable, typed storage for :class:`ReplayStateRegistryV3` snapshots.

The artifact format is deliberately data-only: canonical JSON plus raw ndarray
bytes.  It never uses pickle, eval, imports named by data, or object arrays.
Loading requires an external digest and external frozen string metadata; an
artifact cannot authenticate itself.
"""

from __future__ import annotations

import ctypes
import errno
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import stat
import tempfile
from typing import Any, Mapping

import numpy as np

from .replay_state_v3 import ReplayStateRegistryV3


FORMAT_VERSION = "heterocoord.registry-checkpoint.v3"
MANIFEST_NAME = "manifest.json"
BLOB_DIRECTORY = "blobs"
MAX_PATH_BYTES = 4096
MAX_PATH_COMPONENT_BYTES = 255
MAX_MANIFEST_BYTES = 8 * 1024 * 1024
MAX_ARRAY_BYTES = 64 * 1024 * 1024
MAX_TOTAL_ARRAY_BYTES = 256 * 1024 * 1024
MAX_ARRAY_DIMS = 8
MAX_DIMENSION = 10_000_000
MAX_TREE_DEPTH = 64
MAX_TREE_NODES = 1_000_000
MAX_STRING_BYTES = 4 * 1024 * 1024
MAX_METADATA_ENTRIES = 1024


def _digest_bytes(value: bytes) -> str:
    return sha256(value).hexdigest()


def _canonical_json(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")


def _validate_path(path: str | os.PathLike[str]) -> Path:
    target = Path(path)
    encoded = os.fsencode(target)
    if not target.name or len(encoded) > MAX_PATH_BYTES or len(os.fsencode(target.name)) > MAX_PATH_COMPONENT_BYTES:
        raise ValueError("checkpoint artifact path exceeds the registered path limits")
    parent = target.parent
    try:
        parent_info = parent.lstat()
    except OSError as exc:
        raise ValueError("checkpoint artifact parent must already exist") from exc
    if not stat.S_ISDIR(parent_info.st_mode) or stat.S_ISLNK(parent_info.st_mode):
        raise ValueError("checkpoint artifact parent must be a real directory")
    return target


def _validate_metadata(metadata: Mapping[str, str], *, name: str) -> dict[str, str]:
    if not isinstance(metadata, Mapping) or len(metadata) > MAX_METADATA_ENTRIES:
        raise TypeError(f"{name} must be a bounded string-to-string mapping")
    result: dict[str, str] = {}
    for key, value in metadata.items():
        if not isinstance(key, str) or not key or not isinstance(value, str):
            raise TypeError(f"{name} must contain only nonempty string keys and string values")
        if len(key.encode("utf-8")) > MAX_STRING_BYTES or len(value.encode("utf-8")) > MAX_STRING_BYTES:
            raise ValueError(f"{name} string exceeds the registered byte limit")
        result[key] = value
    return dict(sorted(result.items()))


def _validate_snapshot_schema(snapshot: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    if not isinstance(snapshot, Mapping):
        raise TypeError("registry snapshot must be a participant mapping")
    for key in snapshot:
        if not isinstance(key, str) or not key.strip():
            raise ValueError("snapshot participant IDs must be nonempty strings")
        if len(key.encode("utf-8")) > MAX_STRING_BYTES:
            raise ValueError("snapshot participant ID exceeds the registered byte limit")
    missing = set(ReplayStateRegistryV3.REQUIRED_PARTICIPANTS) - set(snapshot)
    if missing:
        raise ValueError(f"registry checkpoint missing required participants: {sorted(missing)}")
    result: dict[str, Mapping[str, Any]] = {}
    for participant, payload in snapshot.items():
        if not isinstance(payload, Mapping) or set(payload) != {"version", "state"}:
            raise ValueError(f"snapshot participant schema mismatch for {participant}")
        if not isinstance(payload["version"], str) or not payload["version"].strip():
            raise ValueError(f"snapshot participant version missing for {participant}")
        if len(payload["version"].encode("utf-8")) > MAX_STRING_BYTES:
            raise ValueError(f"snapshot participant version exceeds the byte limit for {participant}")
        if not isinstance(payload["state"], Mapping):
            raise ValueError(f"snapshot participant state must be a mapping for {participant}")
        result[participant] = payload
    return dict(sorted(result.items()))


class _Encoder:
    def __init__(self) -> None:
        self.blobs: dict[str, bytes] = {}
        self.blob_specs: dict[str, dict[str, Any]] = {}
        self._active: set[int] = set()
        self._nodes = 0
        self._total_array_bytes = 0

    def encode(self, value: Any, *, depth: int = 0) -> Any:
        self._nodes += 1
        if self._nodes > MAX_TREE_NODES or depth > MAX_TREE_DEPTH:
            raise ValueError("checkpoint state exceeds the registered tree limits")
        if value is None or type(value) is bool or type(value) is int:
            if type(value) is int and value.bit_length() > 4096:
                raise ValueError("checkpoint integer exceeds the registered bit limit")
            return value
        if type(value) is float:
            if not math.isfinite(value):
                raise ValueError("nonfinite checkpoint scalar is unsupported")
            return value
        if type(value) is str:
            if len(value.encode("utf-8")) > MAX_STRING_BYTES:
                raise ValueError("checkpoint string exceeds the registered byte limit")
            return value
        if isinstance(value, np.generic):
            raise TypeError("NumPy scalar checkpoints are unsupported; use a Python scalar or ndarray")
        if type(value).__module__.split(".", 1)[0] == "torch":
            raise TypeError("Torch tensors are unsupported; device-preserving restore is not implemented")
        if isinstance(value, np.ndarray):
            return self._encode_array(value)
        if isinstance(value, (Mapping, list, tuple)):
            identity = id(value)
            if identity in self._active:
                raise ValueError("cyclic checkpoint state is unsupported")
            self._active.add(identity)
            try:
                if isinstance(value, Mapping):
                    if any(not isinstance(key, str) for key in value):
                        raise TypeError("checkpoint mapping keys must be strings")
                    if any(len(key.encode("utf-8")) > MAX_STRING_BYTES for key in value):
                        raise ValueError("checkpoint mapping key exceeds the registered byte limit")
                    items = [[key, self.encode(value[key], depth=depth + 1)] for key in sorted(value)]
                    return {"__codec__": "dict", "items": items}
                items = [self.encode(item, depth=depth + 1) for item in value]
                return {"__codec__": "tuple" if isinstance(value, tuple) else "list", "items": items}
            finally:
                self._active.remove(identity)
        raise TypeError(f"unsupported checkpoint state value: {type(value).__name__}")

    def _encode_array(self, value: np.ndarray) -> dict[str, str]:
        if value.ndim > MAX_ARRAY_DIMS or any(type(size) is not int or size < 0 or size > MAX_DIMENSION for size in value.shape):
            raise ValueError("checkpoint ndarray shape exceeds the registered limits")
        if value.dtype.kind not in {"b", "i", "u", "f", "c"} or value.dtype.hasobject:
            raise TypeError(f"unsupported checkpoint ndarray dtype: {value.dtype}")
        if (value.nbytes > MAX_ARRAY_BYTES
                or self._total_array_bytes + value.nbytes > MAX_TOTAL_ARRAY_BYTES):
            raise ValueError("checkpoint ndarray bytes exceed the registered limits")
        if value.dtype.kind in {"f", "c"} and not bool(np.isfinite(value).all()):
            raise ValueError("nonfinite checkpoint ndarray is unsupported")
        # ``np.ascontiguousarray`` promotes a zero-dimensional ndarray to
        # shape ``(1,)``.  Serializing directly in C order preserves the
        # registered dtype and original shape while still producing canonical
        # contiguous bytes for non-contiguous views.
        raw = value.tobytes(order="C")
        if len(raw) != value.nbytes:
            raise ValueError("checkpoint ndarray byte count is inconsistent")
        if len(raw) > MAX_ARRAY_BYTES or self._total_array_bytes + len(raw) > MAX_TOTAL_ARRAY_BYTES:
            raise ValueError("checkpoint ndarray bytes exceed the registered limits")
        blob_id = f"array-{len(self.blobs):06d}"
        filename = f"{blob_id}.bin"
        self.blobs[filename] = raw
        self.blob_specs[blob_id] = {
            "file": f"{BLOB_DIRECTORY}/{filename}",
            "dtype": value.dtype.str,
            "shape": list(value.shape),
            "nbytes": len(raw),
            "sha256": _digest_bytes(raw),
        }
        self._total_array_bytes += len(raw)
        return {"__codec__": "ndarray", "id": blob_id}


class _Decoder:
    def __init__(self, artifact: Path, blob_specs: Mapping[str, Any]) -> None:
        self.artifact = artifact
        self.blob_specs = blob_specs
        self._nodes = 0
        self._used_blobs: set[str] = set()
        self._total_array_bytes = 0

    def decode(self, value: Any, *, depth: int = 0) -> Any:
        self._nodes += 1
        if self._nodes > MAX_TREE_NODES or depth > MAX_TREE_DEPTH:
            raise ValueError("checkpoint state exceeds the registered tree limits")
        if value is None or type(value) is bool:
            return value
        if type(value) is int:
            if value.bit_length() > 4096:
                raise ValueError("checkpoint integer exceeds the registered bit limit")
            return value
        if type(value) is str:
            if len(value.encode("utf-8")) > MAX_STRING_BYTES:
                raise ValueError("checkpoint string exceeds the registered byte limit")
            return value
        if type(value) is float:
            if not math.isfinite(value):
                raise ValueError("nonfinite checkpoint scalar is unsupported")
            return value
        if not isinstance(value, dict):
            raise ValueError("malformed typed checkpoint node")
        fields = set(value)
        if fields == {"__codec__", "id"}:
            if value["__codec__"] != "ndarray":
                raise ValueError("unknown typed checkpoint node")
            return self._decode_array(value["id"])
        if fields != {"__codec__", "items"}:
            raise ValueError("malformed typed checkpoint node")
        codec = value["__codec__"]
        if not isinstance(codec, str):
            raise ValueError("unknown typed checkpoint node")
        if codec not in {"dict", "list", "tuple"} or not isinstance(value["items"], list):
            raise ValueError("unknown typed checkpoint node")
        if codec == "dict":
            result: dict[str, Any] = {}
            for pair in value["items"]:
                if not isinstance(pair, list) or len(pair) != 2 or not isinstance(pair[0], str) or pair[0] in result:
                    raise ValueError("malformed checkpoint mapping entry")
                if len(pair[0].encode("utf-8")) > MAX_STRING_BYTES:
                    raise ValueError("checkpoint mapping key exceeds the registered byte limit")
                result[pair[0]] = self.decode(pair[1], depth=depth + 1)
            return result
        items = [self.decode(item, depth=depth + 1) for item in value["items"]]
        return tuple(items) if codec == "tuple" else items

    def _decode_array(self, blob_id: Any) -> np.ndarray:
        if (not _valid_blob_id(blob_id) or blob_id in self._used_blobs
                or blob_id not in self.blob_specs):
            raise ValueError("checkpoint ndarray reference is missing or reused")
        spec = self.blob_specs[blob_id]
        if not isinstance(spec, Mapping) or set(spec) != {"file", "dtype", "shape", "nbytes", "sha256"}:
            raise ValueError("checkpoint ndarray manifest schema mismatch")
        filename = f"{BLOB_DIRECTORY}/{blob_id}.bin"
        if (spec["file"] != filename or type(spec["nbytes"]) is not int
                or not isinstance(spec["sha256"], str) or len(spec["sha256"]) != 64
                or any(character not in "0123456789abcdef" for character in spec["sha256"])):
            raise ValueError("checkpoint ndarray identity is malformed")
        shape = spec["shape"]
        if not isinstance(shape, list) or len(shape) > MAX_ARRAY_DIMS or any(type(size) is not int or size < 0 or size > MAX_DIMENSION for size in shape):
            raise ValueError("checkpoint ndarray shape exceeds the registered limits")
        try:
            dtype = np.dtype(spec["dtype"])
        except (TypeError, ValueError) as exc:
            raise ValueError("checkpoint ndarray dtype is invalid") from exc
        if dtype.kind not in {"b", "i", "u", "f", "c"} or dtype.hasobject:
            raise ValueError("checkpoint ndarray dtype is unsupported")
        elements = math.prod(shape)
        expected_bytes = elements * dtype.itemsize
        if expected_bytes != spec["nbytes"] or expected_bytes > MAX_ARRAY_BYTES or self._total_array_bytes + expected_bytes > MAX_TOTAL_ARRAY_BYTES:
            raise ValueError("checkpoint ndarray byte count or limit mismatch")
        path = self.artifact / filename
        _require_regular_file(path, expected_size=expected_bytes)
        raw = path.read_bytes()
        if _digest_bytes(raw) != spec["sha256"]:
            raise ValueError("checkpoint ndarray checksum mismatch")
        array = np.frombuffer(raw, dtype=dtype).reshape(tuple(shape)).copy()
        if dtype.kind in {"f", "c"} and not bool(np.isfinite(array).all()):
            raise ValueError("nonfinite checkpoint ndarray is unsupported")
        self._used_blobs.add(blob_id)
        self._total_array_bytes += expected_bytes
        return array


def _require_regular_file(path: Path, *, expected_size: int | None = None) -> os.stat_result:
    try:
        info = path.lstat()
    except OSError as exc:
        raise ValueError(f"checkpoint artifact file missing: {path.name}") from exc
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise ValueError(f"checkpoint artifact entry is not a regular file: {path.name}")
    if expected_size is not None and info.st_size != expected_size:
        raise ValueError(f"checkpoint artifact file size mismatch: {path.name}")
    return info


def _valid_blob_id(value: Any) -> bool:
    return (isinstance(value, str) and len(value) == 12
            and value.startswith("array-")
            and all(character in "0123456789" for character in value[6:]))


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_file(path: Path, data: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        offset = 0
        while offset < len(data):
            offset += os.write(descriptor, data[offset:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_publish_noreplace(source: Path, target: Path) -> None:
    """Linux atomic directory publish with RENAME_NOREPLACE."""
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise RuntimeError("atomic no-replace directory publish is unavailable")
    renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    renameat2.restype = ctypes.c_int
    result = renameat2(-100, os.fsencode(source), -100, os.fsencode(target), 1)
    if result != 0:
        error = ctypes.get_errno()
        if error == errno.EEXIST:
            raise FileExistsError(f"checkpoint artifact already exists: {target}")
        raise OSError(error, os.strerror(error), str(target))


def write_registry_checkpoint(path: str | os.PathLike[str], snapshot: Mapping[str, Any], *,
                              metadata: Mapping[str, str]) -> str:
    """Write one new durable registry snapshot and return its manifest digest."""
    target = _validate_path(path)
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"checkpoint artifact already exists: {target}")
    frozen_metadata = _validate_metadata(metadata, name="metadata")
    participants = _validate_snapshot_schema(snapshot)
    encoder = _Encoder()
    encoded_participants = {
        name: {"version": payload["version"], "state": encoder.encode(payload["state"])}
        for name, payload in participants.items()
    }
    manifest = {
        "format_version": FORMAT_VERSION,
        "metadata": frozen_metadata,
        "required_participants": list(ReplayStateRegistryV3.REQUIRED_PARTICIPANTS),
        "participants": encoded_participants,
        "arrays": encoder.blob_specs,
    }
    manifest_bytes = _canonical_json(manifest)
    if len(manifest_bytes) > MAX_MANIFEST_BYTES:
        raise ValueError("checkpoint manifest exceeds the registered byte limit")
    artifact_digest = _digest_bytes(manifest_bytes)
    temporary = Path(tempfile.mkdtemp(prefix=f".{target.name}.tmp-", dir=target.parent))
    published = False
    try:
        blobs = temporary / BLOB_DIRECTORY
        blobs.mkdir(mode=0o700)
        for filename, data in encoder.blobs.items():
            _write_file(blobs / filename, data)
        _fsync_directory(blobs)
        _write_file(temporary / MANIFEST_NAME, manifest_bytes)
        _fsync_directory(temporary)
        _atomic_publish_noreplace(temporary, target)
        published = True
        _fsync_directory(target.parent)
        return artifact_digest
    finally:
        if not published and temporary.exists():
            # The temporary directory was created by this function and never
            # exposed as the artifact. Remove only its known bounded contents.
            for child in (temporary / BLOB_DIRECTORY).glob("*.bin") if (temporary / BLOB_DIRECTORY).is_dir() else ():
                child.unlink(missing_ok=True)
            if (temporary / BLOB_DIRECTORY).is_dir():
                (temporary / BLOB_DIRECTORY).rmdir()
            (temporary / MANIFEST_NAME).unlink(missing_ok=True)
            temporary.rmdir()


def read_registry_checkpoint(path: str | os.PathLike[str], *, expected_digest: str,
                             expected_metadata: Mapping[str, str]) -> dict[str, Any]:
    """Verify a complete artifact before decoding and returning its snapshot."""
    target = _validate_path(path)
    if not isinstance(expected_digest, str) or len(expected_digest) != 64 or any(c not in "0123456789abcdef" for c in expected_digest):
        raise ValueError("external expected_digest must be a lowercase SHA-256 hex string")
    frozen_metadata = _validate_metadata(expected_metadata, name="expected_metadata")
    try:
        target_info = target.lstat()
    except OSError as exc:
        raise ValueError("checkpoint artifact directory is missing") from exc
    if not stat.S_ISDIR(target_info.st_mode) or stat.S_ISLNK(target_info.st_mode):
        raise ValueError("checkpoint artifact must be a real directory")
    blobs = target / BLOB_DIRECTORY
    try:
        blobs_info = blobs.lstat()
    except OSError as exc:
        raise ValueError("checkpoint blob directory is missing") from exc
    if not stat.S_ISDIR(blobs_info.st_mode) or stat.S_ISLNK(blobs_info.st_mode):
        raise ValueError("checkpoint blob directory must be a real directory")
    manifest_path = target / MANIFEST_NAME
    info = _require_regular_file(manifest_path)
    if info.st_size > MAX_MANIFEST_BYTES:
        raise ValueError("checkpoint manifest exceeds the registered byte limit")
    manifest_bytes = manifest_path.read_bytes()
    if _digest_bytes(manifest_bytes) != expected_digest:
        raise ValueError("checkpoint manifest digest mismatch")
    try:
        manifest = json.loads(manifest_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("checkpoint manifest is truncated or invalid") from exc
    if not isinstance(manifest, dict) or set(manifest) != {"format_version", "metadata", "required_participants", "participants", "arrays"}:
        raise ValueError("checkpoint manifest schema mismatch")
    if manifest["format_version"] != FORMAT_VERSION:
        raise ValueError("unsupported checkpoint format version")
    if manifest["metadata"] != frozen_metadata:
        raise ValueError("checkpoint metadata differs from external expected metadata")
    if manifest["required_participants"] != list(ReplayStateRegistryV3.REQUIRED_PARTICIPANTS):
        raise ValueError("checkpoint required participant schema mismatch")
    participants = _validate_snapshot_schema(manifest["participants"])
    arrays = manifest["arrays"]
    if not isinstance(arrays, dict) or any(not _valid_blob_id(key) for key in arrays):
        raise ValueError("checkpoint ndarray index is malformed")
    expected_root_entries = {MANIFEST_NAME, BLOB_DIRECTORY}
    if {entry.name for entry in target.iterdir()} != expected_root_entries:
        raise ValueError("checkpoint artifact contains unexpected root entries")
    expected_blob_files = {f"{key}.bin" for key in arrays}
    if {entry.name for entry in blobs.iterdir()} != expected_blob_files:
        raise ValueError("checkpoint artifact contains missing or unexpected blob files")
    decoder = _Decoder(target, arrays)
    decoded = {
        participant: {"version": payload["version"], "state": decoder.decode(payload["state"])}
        for participant, payload in participants.items()
    }
    if decoder._used_blobs != set(arrays):
        raise ValueError("checkpoint contains unreferenced ndarray blobs")
    return _validate_snapshot_schema(decoded)


__all__ = ["write_registry_checkpoint", "read_registry_checkpoint"]
