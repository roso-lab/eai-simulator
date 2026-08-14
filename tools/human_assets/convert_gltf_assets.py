#!/usr/bin/env python3
"""Convert allowlisted urban-sim human GLTF assets to validated USD files."""

from __future__ import annotations

import argparse
import asyncio
import base64
import errno
import hashlib
import json
import math
import os
import re
import shutil
import stat
import struct
import sys
import tempfile
from collections import Counter
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import unquote_to_bytes, urlsplit


_MOTION_IDS = {
    "2023_09_04T16_30_39-greeting-09-certificate-kawaguchi-fps_30.gltf": "bow",
    "2023_09_04T16_40_16-15_01-fps_30.gltf": "walk_and_look",
    "2023_09_04T16_44_42-120_04-fps_30.gltf": "dance",
    "2023_09_04T16_48_11-B4_-_stand_to_walk_back-fps_30.gltf": "walk_backward",
    "synbody_jog426.fbx.gltf": "jog",
}

_MOTION_PROVIDER_STEMS = {
    "dance": "motion_120_04",
    "walk_and_look": "motion_15_01",
    "walk_backward": "stand_to_walk_back",
}

_ACTIVITIES = {
    "BikeMan.fbx.gltf": ("bike-man", "cyclist"),
    "eScooterWoman.fbx.gltf": ("escooter-woman", "scooter_rider"),
    "skateboardMan1.fbx.gltf": ("skateboard-man-1", "skateboarder"),
    "free3DVersion.gltf": ("wheelchair-rider", "wheelchair"),
}

_RPM_MOTION_IDS = {
    "rp_aaron_rigged_001_motion.gltf": "phone_call",
    "rp_amit_rigged_008_yup_t_motion.gltf": "long_stride_walk",
    "rp_amit_rigged_009_yup_t_motion.gltf": "walk_and_text",
    "rp_amit_rigged_010_yup_t_motion.gltf": "stagger_walk",
    "rp_amit_rigged_011_yup_t_motion.gltf": "hit_reaction_retreat",
    "rp_amit_rigged_012_yup_t_motion.gltf": "forward_dive",
}

_KIND_ORDER = {
    "synbody_motion": 0,
    "synbody_character": 1,
    "rigid_activity": 2,
    "rpm_character": 3,
    "rpm_character_motion": 4,
    "rpm_motion": 5,
}

_BAD_PERCENT_ESCAPE = re.compile(r"%(?![0-9a-fA-F]{2})")
_WINDOWS_DRIVE_PATH = re.compile(r"^[a-zA-Z]:")
_GLB_JSON_CHUNK = 0x4E4F534A
_GLB_BIN_CHUNK = 0x004E4942


@dataclass(frozen=True)
class ConversionCandidate:
    id: str
    kind: str
    source: Path
    output: Path
    profile: str
    activity_type: str | None = None


@dataclass(frozen=True)
class ConversionResult:
    id: str
    kind: str
    source: str
    output: str
    enabled: bool
    source_sha256: str | None = None
    output_sha256: str | None = None
    source_dependencies: tuple[Mapping[str, str], ...] = field(default_factory=tuple)
    output_dependencies: tuple[Mapping[str, str], ...] = field(default_factory=tuple)
    error: str | None = None
    details: Mapping[str, Any] = field(default_factory=dict)


class UnsafeTargetPathError(RuntimeError):
    """Raised when target I/O could escape through a symbolic link."""


class UnsafeSourcePathError(RuntimeError):
    """Raised when source I/O could escape through a symbolic link."""


class UnsafeGltfDependencyError(RuntimeError):
    """Raised when a GLTF dependency URI escapes its candidate directory."""


def lexical_absolute(path: str | Path) -> Path:
    """Return an absolute, normalized path without resolving symbolic links."""
    return Path(os.path.abspath(os.fspath(path)))


def ensure_safe_target_path(path: str | Path, target_root: str | Path) -> Path:
    """Require a lexically contained path with no symbolic-link components."""
    root = lexical_absolute(target_root)
    candidate = lexical_absolute(path)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise UnsafeTargetPathError("unsafe target path") from exc

    current = candidate
    while True:
        if current.is_symlink():
            raise UnsafeTargetPathError("unsafe target path")
        if current == current.parent:
            break
        current = current.parent
    return candidate


def ensure_target_directory(path: str | Path, target_root: str | Path) -> Path:
    """Create a target directory only after and before lexical safety checks."""
    directory = ensure_safe_target_path(path, target_root)
    directory.mkdir(parents=True, exist_ok=True)
    return ensure_safe_target_path(directory, target_root)


def ensure_safe_source_path(path: str | Path, source_root: str | Path) -> Path:
    """Require a lexically contained source with no symbolic-link components."""
    root = lexical_absolute(source_root)
    candidate = lexical_absolute(path)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise UnsafeSourcePathError("unsafe source path") from exc

    current = candidate
    while True:
        if current.is_symlink():
            raise UnsafeSourcePathError("unsafe source path")
        if current == current.parent:
            break
        current = current.parent
    return candidate


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def _has_symlink_component(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return True
    current = path
    while current != root:
        if current.is_symlink():
            return True
        current = current.parent
    return False


def _content_sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _open_regular_file(path: Path):
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(os.fspath(path), flags)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise OSError(errno.EINVAL, "path is not a regular file")
        stream = os.fdopen(descriptor, "rb")
    except Exception:
        os.close(descriptor)
        raise
    return stream


def _read_regular_file(path: Path) -> bytes:
    with _open_regular_file(path) as stream:
        return stream.read()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with _open_regular_file(path) as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _inventory_sha256(records: Iterable[Mapping[str, str]]) -> str:
    normalized = sorted(
        (dict(record) for record in records),
        key=lambda record: tuple(sorted(record.items())),
    )
    content = json.dumps(
        normalized,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return _content_sha256(content)


def _data_uri_content(uri: str) -> bytes:
    header, separator, payload = uri.partition(",")
    if not separator or not header[:5].casefold() == "data:":
        raise ValueError("image URI is not a data URI")
    if header.rpartition(";")[2].casefold() == "base64":
        return base64.b64decode(payload, validate=True)
    if _BAD_PERCENT_ESCAPE.search(payload):
        raise ValueError("data URI contains an invalid percent escape")
    return unquote_to_bytes(payload)


def _unsafe_gltf_dependency() -> UnsafeGltfDependencyError:
    return UnsafeGltfDependencyError("unsafe GLTF dependency URI")


def _relative_gltf_uri_path(uri: str) -> str:
    if "\0" in uri or "\\" in uri or _BAD_PERCENT_ESCAPE.search(uri):
        raise _unsafe_gltf_dependency()
    try:
        parsed = urlsplit(uri)
    except ValueError as exc:
        raise _unsafe_gltf_dependency() from exc
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        raise _unsafe_gltf_dependency()
    try:
        decoded = unquote_to_bytes(parsed.path).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _unsafe_gltf_dependency() from exc
    parts = PurePosixPath(decoded).parts
    if (
        not decoded
        or "\0" in decoded
        or "\\" in decoded
        or decoded.startswith("/")
        or decoded.startswith("//")
        or _WINDOWS_DRIVE_PATH.match(decoded)
        or ".." in parts
    ):
        raise _unsafe_gltf_dependency()
    return decoded


def _gltf_dependency_path(uri: str, *, source: Path) -> Path:
    decoded = _relative_gltf_uri_path(uri)
    try:
        dependency = ensure_safe_source_path(source.parent / decoded, source.parent)
    except UnsafeSourcePathError as exc:
        raise _unsafe_gltf_dependency() from exc
    try:
        dependency = ensure_safe_source_path(dependency, source.parent)
    except UnsafeSourcePathError as exc:
        raise _unsafe_gltf_dependency() from exc
    return dependency


def _gltf_uri_content(uri: str, *, source: Path) -> bytes:
    if uri[:5].casefold() == "data:":
        return _data_uri_content(uri)
    dependency = _gltf_dependency_path(uri, source=source)
    try:
        return _read_regular_file(dependency)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise _unsafe_gltf_dependency() from exc
        raise FileNotFoundError(
            "GLTF dependency is missing or not a regular file"
        ) from exc


def _parse_glb(content: bytes) -> tuple[Mapping[str, Any], bytes | None]:
    if len(content) < 12:
        raise ValueError("GLB header is truncated")
    magic, version, declared_length = struct.unpack_from("<4sII", content)
    if magic != b"glTF" or version != 2 or declared_length != len(content):
        raise ValueError("GLB header is invalid")

    offset = 12
    chunk_index = 0
    json_chunk: bytes | None = None
    binary_chunk: bytes | None = None
    while offset < len(content):
        if offset + 8 > len(content):
            raise ValueError("GLB chunk header is truncated")
        chunk_length, chunk_type = struct.unpack_from("<II", content, offset)
        if chunk_length % 4:
            raise ValueError("GLB chunk layout is invalid")
        offset += 8
        chunk_end = offset + chunk_length
        if chunk_end > len(content):
            raise ValueError("GLB chunk is truncated")
        chunk = content[offset:chunk_end]
        offset = chunk_end
        if json_chunk is None:
            if chunk_type != _GLB_JSON_CHUNK:
                raise ValueError("GLB chunk layout is invalid")
            json_chunk = chunk
        elif chunk_type == _GLB_JSON_CHUNK:
            raise ValueError("GLB chunk layout is invalid")
        elif chunk_type == _GLB_BIN_CHUNK:
            if binary_chunk is not None or chunk_index != 1:
                raise ValueError("GLB chunk layout is invalid")
            binary_chunk = chunk
        chunk_index += 1
    if json_chunk is None:
        raise ValueError("GLB has no JSON chunk")
    json_text = json_chunk.decode("utf-8")
    leading_whitespace = re.match(r"[ \t\r\n]*", json_text)
    document_start = leading_whitespace.end() if leading_whitespace is not None else 0
    document, document_end = json.JSONDecoder().raw_decode(
        json_text,
        idx=document_start,
    )
    if json_text[document_end:] != " " * (len(json_text) - document_end):
        raise ValueError("GLB JSON padding is invalid")
    if not isinstance(document, dict):
        raise ValueError("GLTF document must be an object")
    return document, binary_chunk


def _read_gltf_document(
    source: str | Path,
    *,
    source_root: str | Path,
) -> tuple[Path, Mapping[str, Any], bytes | None]:
    source = ensure_safe_source_path(source, source_root)
    source = ensure_safe_source_path(source, source_root)
    try:
        content = _read_regular_file(source)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise UnsafeSourcePathError("unsafe source path") from exc
        raise FileNotFoundError("GLTF source is missing or not a regular file") from exc
    if source.suffix.casefold() == ".glb":
        document, binary_chunk = _parse_glb(content)
    else:
        document = json.loads(content.decode("utf-8"))
        if not isinstance(document, dict):
            raise ValueError("GLTF document must be an object")
        binary_chunk = None
    return source, document, binary_chunk


def _nonnegative_int(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"GLTF {field_name} must be a nonnegative integer")
    return value


def _load_gltf_resources(
    source: str | Path,
    *,
    source_root: str | Path,
) -> tuple[Path, Mapping[str, Any], list[bytes], list[int]]:
    source, document, binary_chunk = _read_gltf_document(
        source, source_root=source_root
    )
    raw_buffers = document.get("buffers", [])
    if not isinstance(raw_buffers, list):
        raise ValueError("GLTF buffers must be a list")
    buffers: list[bytes] = []
    buffer_lengths: list[int] = []
    for index, raw in enumerate(raw_buffers):
        if not isinstance(raw, dict):
            raise ValueError("GLTF buffer must be an object")
        byte_length = _nonnegative_int(
            raw.get("byteLength"), field_name="buffer byteLength"
        )
        uri = raw.get("uri")
        if isinstance(uri, str):
            content = _gltf_uri_content(uri, source=source)
            if len(content) != byte_length:
                raise ValueError("GLTF buffer byteLength does not match its content")
        elif uri is None and index == 0 and binary_chunk is not None:
            content = binary_chunk
            if not byte_length <= len(content) <= byte_length + 3:
                raise ValueError("GLB BIN chunk does not match buffer byteLength")
            if any(content[byte_length:]):
                raise ValueError("GLB BIN padding is invalid")
        else:
            raise ValueError("GLTF buffer has no URI")
        buffers.append(content)
        buffer_lengths.append(byte_length)

    raw_views = document.get("bufferViews", [])
    if not isinstance(raw_views, list):
        raise ValueError("GLTF bufferViews must be a list")
    for raw in raw_views:
        if not isinstance(raw, dict):
            raise ValueError("GLTF bufferView must be an object")
        buffer_index = _nonnegative_int(
            raw.get("buffer"), field_name="bufferView buffer"
        )
        byte_offset = _nonnegative_int(
            raw.get("byteOffset", 0), field_name="bufferView byteOffset"
        )
        byte_length = _nonnegative_int(
            raw.get("byteLength"), field_name="bufferView byteLength"
        )
        if (
            buffer_index >= len(buffers)
            or byte_offset + byte_length > buffer_lengths[buffer_index]
        ):
            raise ValueError("GLTF bufferView is out of bounds")
    return source, document, buffers, buffer_lengths


def _buffer_view_content(
    document: Mapping[str, Any],
    buffers: list[bytes],
    view_index: Any,
) -> bytes:
    view_index = _nonnegative_int(view_index, field_name="image bufferView")
    raw_views = document.get("bufferViews", [])
    if not isinstance(raw_views, list) or view_index >= len(raw_views):
        raise ValueError("GLTF image bufferView is out of bounds")
    view = raw_views[view_index]
    buffer_index = int(view["buffer"])
    start = int(view.get("byteOffset", 0))
    end = start + int(view["byteLength"])
    return buffers[buffer_index][start:end]


def preflight_gltf_dependencies(
    source: str | Path,
    *,
    source_root: str | Path,
) -> None:
    """Validate every GLTF/GLB buffer and image dependency before conversion."""
    source, document, buffers, _buffer_lengths = _load_gltf_resources(
        source, source_root=source_root
    )
    raw_images = document.get("images", [])
    if not isinstance(raw_images, list):
        raise ValueError("GLTF images must be a list")
    for image in raw_images:
        if not isinstance(image, dict):
            raise ValueError("GLTF image must be an object")
        uri = image.get("uri")
        if isinstance(uri, str):
            _gltf_uri_content(uri, source=source)
        elif "bufferView" in image:
            _buffer_view_content(document, buffers, image["bufferView"])
        else:
            raise ValueError("GLTF image has no URI or bufferView")


def gltf_dependency_records(
    source: str | Path,
    *,
    source_root: str | Path,
) -> tuple[dict[str, str], ...]:
    """Attest every contained external buffer/image dependency of one GLTF."""
    source, document, _binary_chunk = _read_gltf_document(
        source,
        source_root=source_root,
    )
    records: dict[str, dict[str, str]] = {}
    for collection_name in ("buffers", "images"):
        raw_items = document.get(collection_name, [])
        if not isinstance(raw_items, list):
            raise ValueError(f"GLTF {collection_name} must be a list")
        for raw in raw_items:
            if not isinstance(raw, dict):
                raise ValueError(f"GLTF {collection_name[:-1]} must be an object")
            uri = raw.get("uri")
            if not isinstance(uri, str) or uri[:5].casefold() == "data:":
                continue
            relative = PurePosixPath(_relative_gltf_uri_path(uri)).as_posix()
            records[relative] = {
                "path": relative,
                "sha256": _content_sha256(_gltf_uri_content(uri, source=source)),
            }
    return tuple(records[path] for path in sorted(records))


def gltf_image_inventory(
    source: str | Path,
    *,
    source_root: str | Path,
) -> list[dict[str, str]]:
    """Return logical names and content hashes for images referenced by a GLTF."""
    source, document, buffers, _buffer_lengths = _load_gltf_resources(
        source, source_root=source_root
    )

    inventory: list[dict[str, str]] = []
    raw_images = document.get("images", [])
    if not isinstance(raw_images, list):
        raise ValueError("GLTF images must be a list")
    for index, image in enumerate(raw_images):
        if not isinstance(image, dict):
            raise ValueError("GLTF image must be an object")
        uri = image.get("uri")
        if isinstance(uri, str):
            content = _gltf_uri_content(uri, source=source)
            fallback_name = (
                f"image-{index}"
                if uri[:5].casefold() == "data:"
                else Path(_relative_gltf_uri_path(uri)).stem
            )
        elif "bufferView" in image:
            content = _buffer_view_content(document, buffers, image["bufferView"])
            fallback_name = f"image-{index}"
        else:
            raise ValueError(f"GLTF image {index} has no URI or bufferView")
        logical_name = str(image.get("name") or fallback_name)
        inventory.append(
            {
                "logical_name": logical_name,
                "content_sha256": _content_sha256(content),
            }
        )
    return inventory


def output_image_inventory(
    output_root: str | Path,
    *,
    target_root: str | Path,
) -> list[dict[str, str]]:
    """Return stable logical-name/hash records for converted image files."""
    suffixes = {".bmp", ".jpeg", ".jpg", ".png", ".tga", ".tif", ".tiff", ".webp"}
    records = []
    safe_output_root = ensure_safe_target_path(output_root, target_root)
    for path in sorted(safe_output_root.rglob("*")):
        path = ensure_safe_target_path(path, target_root)
        if path.is_file() and path.suffix.lower() in suffixes:
            path = ensure_safe_target_path(path, target_root)
            records.append(
                {
                    "logical_name": path.stem,
                    "content_sha256": _content_sha256(path.read_bytes()),
                }
            )
    return records


def output_texture_sha256(
    output_root: str | Path,
    *,
    target_root: str | Path,
    logical_names: Iterable[str] | None = None,
) -> str:
    """Hash the exact output texture multiset, optionally scoped by logical name."""
    records = output_image_inventory(output_root, target_root=target_root)
    if logical_names is not None:
        allowed = frozenset(str(name) for name in logical_names)
        records = [record for record in records if record["logical_name"] in allowed]
    return _inventory_sha256(records)


def validate_texture_fidelity(
    source_images: Iterable[Mapping[str, str]],
    output_images: Iterable[Mapping[str, str]],
) -> dict[str, Any]:
    """Compare image mappings without performing I/O or mutating the inputs."""

    def grouped(
        records: tuple[Mapping[str, str], ...],
    ) -> dict[str, Counter[str]]:
        values: dict[str, Counter[str]] = {}
        for record in records:
            values.setdefault(record["logical_name"], Counter())[
                record["content_sha256"]
            ] += 1
        return values

    source_records = tuple(source_images)
    output_records = tuple(output_images)
    source_pairs = Counter(
        (record["logical_name"], record["content_sha256"])
        for record in source_records
    )
    output_pairs = Counter(
        (record["logical_name"], record["content_sha256"])
        for record in output_records
    )
    source_by_name = grouped(source_records)
    output_by_name = grouped(output_records)
    collisions = sorted(
        name
        for name, source_hashes in source_by_name.items()
        if len(source_hashes) > 1
        and output_by_name.get(name, Counter()) != source_hashes
    )
    missing = sorted(set(source_by_name).difference(output_by_name))
    extra = sorted(set(output_by_name).difference(source_by_name))
    mismatched = sorted(
        name
        for name in set(source_by_name).intersection(output_by_name)
        if name not in collisions and source_by_name[name] != output_by_name[name]
    )
    enabled = source_pairs == output_pairs
    if collisions:
        reason = f"texture collision: merged logical names {collisions}"
    elif missing:
        reason = f"missing output texture mappings: {missing}"
    elif extra:
        reason = f"extra output texture mappings: {extra}"
    elif mismatched:
        reason = f"texture mapping mismatch: {mismatched}"
    else:
        reason = "texture mappings verified"
    return {
        "collisions": collisions,
        "enabled": enabled,
        "extra": extra,
        "mismatched": mismatched,
        "missing": missing,
        "output_count": len(output_records),
        "reason": reason,
        "source_count": len(source_records),
    }


def _build_unfiltered_conversion_plan(
    source_root: str | Path,
    target_root: str | Path,
) -> tuple[ConversionCandidate, ...]:
    source_root = ensure_safe_source_path(source_root, source_root)
    target_root = lexical_absolute(target_root)
    candidates: list[ConversionCandidate] = []

    character_root = source_root / "assets/pedestrians/characters_yup"
    for source in sorted(character_root.glob("*.gltf")):
        character_id = source.stem.removesuffix("_people_baked")
        if character_id == "0000001":
            # Duplicates the native registry asset synbody-0000001; the migrated
            # duplicate record was removed from the registry, so never re-convert it.
            continue
        profile = "smplx_70" if source.stem.endswith("_people_baked") else "synbody_55"
        candidates.append(
            ConversionCandidate(
                id=f"synbody-gltf-{character_id}",
                kind="synbody_character",
                source=source,
                output=target_root / f"characters/synbody_gltf/{character_id}/character.usd",
                profile=profile,
                activity_type="pedestrian",
            )
        )

    rpm_root = source_root / "assets/pedestrians/RPtest_GLTF"
    for source in sorted(rpm_root.glob("*.gltf")):
        stem = source.name.removesuffix(".gltf")
        if source.name == "rp_idle_sophia.fbx.gltf":
            continue
        if stem.endswith("_model"):
            kind = "rpm_character"
            candidate_id = _slug(stem.removesuffix("_model"))
            output = target_root / f"characters/rpm/{candidate_id}/character.usd"
        elif stem.endswith("_motion"):
            kind = "rpm_motion"
            candidate_id = _RPM_MOTION_IDS.get(source.name)
            if candidate_id is None:
                continue
            output = target_root / f"motions/rpm/{candidate_id}.usd"
        else:
            continue
        candidates.append(
            ConversionCandidate(
                id=candidate_id,
                kind=kind,
                source=source,
                output=output,
                profile="rpm_87",
                activity_type="pedestrian" if kind != "rpm_motion" else None,
            )
        )

    activity_root = source_root / "assets/pedestrians/special_agents"
    for source in sorted(activity_root.glob("*.gltf")):
        activity = _ACTIVITIES.get(source.name)
        if activity is None:
            continue
        candidate_id, activity_type = activity
        candidates.append(
            ConversionCandidate(
                id=candidate_id,
                kind="rigid_activity",
                source=source,
                output=target_root / f"activities/{activity_type}/{candidate_id}/character.usd",
                profile="rigid_1",
                activity_type=activity_type,
            )
        )

    for source in sorted((source_root / "assets/objects").glob("static biker-*.glb")):
        candidate_id = _slug(source.stem)
        candidates.append(
            ConversionCandidate(
                id=candidate_id,
                kind="rigid_activity",
                source=source,
                output=target_root / f"activities/static_biker/{candidate_id}/character.usd",
                profile="rigid_1",
                activity_type="static_biker",
            )
        )

    motion_root = source_root / "assets/pedestrians/motions_yup"
    for source in sorted(motion_root.glob("*.gltf")):
        motion_id = _MOTION_IDS.get(source.name)
        if motion_id is None:
            continue
        candidates.append(
            ConversionCandidate(
                id=motion_id,
                kind="synbody_motion",
                source=source,
                output=target_root
                / "motions/sources"
                / f"{_MOTION_PROVIDER_STEMS.get(motion_id, motion_id)}.usd",
                profile="smplx_70",
            )
        )

    candidates.sort(key=lambda item: (_KIND_ORDER[item.kind], item.id, item.source.as_posix()))
    return tuple(candidates)


def build_conversion_plan_with_rejections(
    source_root: str | Path,
    target_root: str | Path,
) -> tuple[tuple[ConversionCandidate, ...], tuple[ConversionCandidate, ...]]:
    """Partition allowlisted inputs into safe and symlinked candidates."""
    resolved_source_root = ensure_safe_source_path(source_root, source_root)
    candidates = _build_unfiltered_conversion_plan(resolved_source_root, target_root)
    safe: list[ConversionCandidate] = []
    rejected: list[ConversionCandidate] = []
    for candidate in candidates:
        destination = (
            rejected
            if _has_symlink_component(candidate.source, resolved_source_root)
            else safe
        )
        destination.append(candidate)
    return tuple(safe), tuple(rejected)


def build_conversion_plan(
    source_root: str | Path,
    target_root: str | Path,
) -> tuple[ConversionCandidate, ...]:
    """Return a stable, symlink-safe plan without importing Isaac Sim."""
    candidates, _rejected = build_conversion_plan_with_rejections(
        source_root, target_root
    )
    return candidates


_DIRECTORY_OWNING_KINDS = frozenset(
    {
        "synbody_character",
        "rpm_character",
        "rpm_character_motion",
        "rigid_activity",
    }
)


def _output_dependency_relative_path(value: str) -> PurePosixPath:
    if not value or "\0" in value or "\\" in value:
        raise UnsafeTargetPathError("unsafe target path")
    relative = PurePosixPath(value)
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or relative.as_posix() != value
    ):
        raise UnsafeTargetPathError("unsafe target path")
    return relative


def output_dependency_records(
    kind: str,
    output: str | Path,
    *,
    target_root: str | Path,
    dependency_paths: Iterable[str] | None = None,
) -> tuple[dict[str, str], ...]:
    """Attest candidate-owned converted sidecars, excluding the main USD."""
    target_root = ensure_safe_target_path(target_root, target_root)
    output = ensure_safe_target_path(output, target_root)
    output_root = ensure_safe_target_path(output.parent, target_root)

    if kind in _DIRECTORY_OWNING_KINDS:
        candidates = []
        for path in sorted(output_root.rglob("*")):
            path = ensure_safe_target_path(path, target_root)
            mode = path.lstat().st_mode
            if stat.S_ISDIR(mode):
                continue
            if not stat.S_ISREG(mode):
                raise UnsafeTargetPathError("unsafe target path")
            if path != output:
                candidates.append(path)
    elif kind == "synbody_motion":
        candidates = []
    elif kind == "rpm_motion":
        if dependency_paths is None:
            texture_root = ensure_safe_target_path(output_root / "textures", target_root)
            candidates = []
            if texture_root.exists():
                for path in sorted(texture_root.rglob("*")):
                    path = ensure_safe_target_path(path, target_root)
                    mode = path.lstat().st_mode
                    if stat.S_ISDIR(mode):
                        continue
                    if not stat.S_ISREG(mode):
                        raise UnsafeTargetPathError("unsafe target path")
                    candidates.append(path)
        else:
            candidates = []
            for path in dependency_paths:
                relative = _output_dependency_relative_path(path)
                if not relative.parts or relative.parts[0] != "textures":
                    raise UnsafeTargetPathError("unsafe target path")
                candidates.append(
                    ensure_safe_target_path(output_root / relative, target_root)
                )
    else:
        raise ValueError(f"unknown conversion candidate kind: {kind}")

    records: dict[str, dict[str, str]] = {}
    for path in candidates:
        path = ensure_safe_target_path(path, target_root)
        relative = path.relative_to(output_root).as_posix()
        if relative in records:
            raise ValueError("duplicate output dependency path")
        records[relative] = {"path": relative, "sha256": _file_sha256(path)}
    return tuple(records[path] for path in sorted(records))


def _failed_texture_fidelity(exc: Exception) -> dict[str, Any]:
    return {
        "collisions": [],
        "enabled": False,
        "extra": [],
        "mismatched": [],
        "missing": [],
        "output_count": 0,
        "reason": f"texture fidelity validation failed: {type(exc).__name__}",
        "source_count": 0,
    }


def _validated_staged_entries(
    root: Path,
    *,
    target_root: Path,
) -> tuple[tuple[Path, bool], ...]:
    root = ensure_safe_target_path(root, target_root)
    if not root.is_dir():
        raise RuntimeError("converter did not produce a staged output directory")
    entries: list[tuple[Path, bool]] = []
    for path in sorted(root.rglob("*")):
        path = ensure_safe_target_path(path, target_root)
        mode = path.lstat().st_mode
        if stat.S_ISDIR(mode):
            entries.append((path, True))
        elif stat.S_ISREG(mode):
            entries.append((path, False))
        else:
            raise UnsafeTargetPathError("unsafe target path")
    return tuple(entries)


def _remove_private_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


def _ensure_target_directory_tracked(
    path: Path,
    *,
    target_root: Path,
) -> list[Path]:
    directory = ensure_safe_target_path(path, target_root)
    missing: list[Path] = []
    current = directory
    while current != target_root and not current.exists():
        missing.append(current)
        current = current.parent
    ensure_target_directory(directory, target_root)
    return list(reversed(missing))


def _remove_created_directories(directories: Iterable[Path]) -> None:
    for directory in reversed(tuple(directories)):
        try:
            directory.rmdir()
        except OSError:
            pass


def _promote_owned_directory(
    staged_output: Path,
    live_output: Path,
    *,
    staging_root: Path,
    target_root: Path,
    verifier: Callable[[], None],
) -> None:
    staged_directory = ensure_safe_target_path(staged_output.parent, target_root)
    _validated_staged_entries(staged_directory, target_root=target_root)
    live_directory = ensure_safe_target_path(live_output.parent, target_root)
    created_directories = _ensure_target_directory_tracked(
        live_directory.parent,
        target_root=target_root,
    )
    backup = staging_root / "backup" / "owned-directory"
    backup.parent.mkdir(parents=True, exist_ok=True)
    had_live_directory = os.path.lexists(live_directory)
    backed_up = False
    installed = False
    if had_live_directory:
        ensure_safe_target_path(live_directory, target_root)
    try:
        if had_live_directory:
            os.replace(live_directory, backup)
            backed_up = True
        os.replace(staged_directory, live_directory)
        installed = True
        verifier()
    except Exception:
        if installed and os.path.lexists(live_directory):
            _remove_private_path(live_directory)
        if backed_up and os.path.lexists(backup):
            os.replace(backup, live_directory)
        _remove_created_directories(created_directories)
        raise
    else:
        if os.path.lexists(backup):
            _remove_private_path(backup)


def _leaf_promotion_entries(
    candidate: ConversionCandidate,
    staged_output: Path,
    live_output: Path,
    *,
    target_root: Path,
) -> list[tuple[Path, Path]]:
    entries = _validated_staged_entries(
        staged_output.parent,
        target_root=target_root,
    )
    if candidate.kind == "synbody_motion":
        if any(path != staged_output for path, _is_directory in entries):
            raise RuntimeError("unexpected staged output leaf")
        return [(staged_output, live_output)]

    if candidate.kind != "rpm_motion":
        raise ValueError(f"unknown conversion candidate kind: {candidate.kind}")
    promotion = [(staged_output, live_output)]
    for path, is_directory in entries:
        if path == staged_output:
            continue
        relative = path.relative_to(staged_output.parent)
        if not relative.parts or relative.parts[0] != "textures":
            raise RuntimeError("unexpected staged output leaf")
        if not is_directory:
            promotion.append((path, live_output.parent / relative))
    return promotion


def _promote_owned_leaves(
    candidate: ConversionCandidate,
    staged_output: Path,
    live_output: Path,
    *,
    staging_root: Path,
    target_root: Path,
    verifier: Callable[[], None],
) -> None:
    promotion = _leaf_promotion_entries(
        candidate,
        staged_output,
        live_output,
        target_root=target_root,
    )
    pending: list[tuple[Path, Path]] = []
    for staged, live in promotion:
        live = ensure_safe_target_path(live, target_root)
        if candidate.kind == "rpm_motion" and live != live_output and os.path.lexists(live):
            live = ensure_safe_target_path(live, target_root)
            try:
                matching = _file_sha256(staged) == _file_sha256(live)
            except OSError as exc:
                raise RuntimeError("conflicting shared sidecar") from exc
            if not matching:
                raise RuntimeError("conflicting shared sidecar")
        pending.append((staged, live))

    backup_root = staging_root / "backup" / "leaves"
    backup_root.mkdir(parents=True, exist_ok=True)
    backups: list[tuple[Path, Path]] = []
    installed: list[Path] = []
    created_directories: list[Path] = []
    try:
        for index, (staged, live) in enumerate(pending):
            created_directories.extend(
                _ensure_target_directory_tracked(
                    live.parent,
                    target_root=target_root,
                )
            )
            live = ensure_safe_target_path(live, target_root)
            if os.path.lexists(live):
                backup = backup_root / str(index)
                os.replace(live, backup)
                backups.append((live, backup))
            os.replace(staged, live)
            installed.append(live)
        verifier()
    except Exception:
        for live in reversed(installed):
            if os.path.lexists(live):
                _remove_private_path(live)
        for live, backup in reversed(backups):
            if os.path.lexists(backup):
                os.replace(backup, live)
        _remove_created_directories(created_directories)
        raise
    else:
        for _live, backup in backups:
            if os.path.lexists(backup):
                _remove_private_path(backup)


def _promote_candidate(
    candidate: ConversionCandidate,
    staged_output: Path,
    live_output: Path,
    *,
    staging_root: Path,
    target_root: Path,
    verifier: Callable[[], None],
) -> None:
    if candidate.kind in _DIRECTORY_OWNING_KINDS:
        _promote_owned_directory(
            staged_output,
            live_output,
            staging_root=staging_root,
            target_root=target_root,
            verifier=verifier,
        )
    else:
        _promote_owned_leaves(
            candidate,
            staged_output,
            live_output,
            staging_root=staging_root,
            target_root=target_root,
            verifier=verifier,
        )


def convert_candidates(
    candidates: Iterable[ConversionCandidate],
    *,
    converter: Callable[[ConversionCandidate], None],
    validator: Callable[[ConversionCandidate], Mapping[str, Any] | None] | None = None,
    source_root: str | Path,
    target_root: str | Path,
    force: bool = False,
) -> tuple[ConversionResult, ...]:
    """Convert candidates independently so one failure never aborts a batch."""
    source_root = ensure_safe_source_path(source_root, source_root)
    target_root = ensure_safe_target_path(target_root, target_root)
    results: list[ConversionResult] = []
    for candidate in candidates:
        staging_root: Path | None = None
        details: dict[str, Any] = {}
        try:
            live_output = ensure_safe_target_path(candidate.output, target_root)
            if os.path.lexists(live_output) and not force:
                raise RuntimeError(
                    "output already exists; rerun with --force to reconvert"
                )
            preflight_gltf_dependencies(candidate.source, source_root=source_root)
            source = ensure_safe_source_path(candidate.source, source_root)
            source_sha256 = _file_sha256(source)
            source_dependencies = gltf_dependency_records(
                source,
                source_root=source_root,
            )

            ensure_target_directory(target_root, target_root)
            staging_root = Path(
                tempfile.mkdtemp(prefix=".human-conversion-", dir=target_root)
            )
            relative_output = live_output.relative_to(target_root)
            staged_output = staging_root / "work" / relative_output
            ensure_target_directory(staged_output.parent, target_root)
            staged_candidate = replace(candidate, output=staged_output)
            converter(staged_candidate)

            staged_output = ensure_safe_target_path(staged_output, target_root)
            try:
                with _open_regular_file(staged_output) as stream:
                    output_size = os.fstat(stream.fileno()).st_size
            except OSError as exc:
                raise RuntimeError(
                    "converter did not produce a non-empty USD file"
                ) from exc
            if output_size == 0:
                raise RuntimeError("converter did not produce a non-empty USD file")

            details = (
                dict(validator(staged_candidate) or {})
                if validator is not None
                else {}
            )
            try:
                source_images = gltf_image_inventory(
                    candidate.source,
                    source_root=source_root,
                )
                output_images = output_image_inventory(
                    staged_output.parent,
                    target_root=target_root,
                )
                fidelity = validate_texture_fidelity(
                    source_images,
                    output_images,
                )
            except Exception as exc:
                fidelity = _failed_texture_fidelity(exc)
            details["texture_fidelity"] = fidelity
            source = ensure_safe_source_path(candidate.source, source_root)
            if _file_sha256(source) != source_sha256:
                raise RuntimeError("source changed during conversion")
            if (
                gltf_dependency_records(source, source_root=source_root)
                != source_dependencies
            ):
                raise RuntimeError("source dependencies changed during conversion")
            staged_output = ensure_safe_target_path(staged_output, target_root)
            output_sha256 = _file_sha256(staged_output)
            output_dependencies = output_dependency_records(
                candidate.kind,
                staged_output,
                target_root=target_root,
            )
            if not fidelity["enabled"]:
                raise RuntimeError(str(fidelity["reason"]))

            def verify_installed_output() -> None:
                installed_output = ensure_safe_target_path(live_output, target_root)
                if _file_sha256(installed_output) != output_sha256:
                    raise RuntimeError("installed output changed during promotion")
                dependency_paths = (
                    None
                    if candidate.kind in _DIRECTORY_OWNING_KINDS
                    else tuple(record["path"] for record in output_dependencies)
                )
                installed_dependencies = output_dependency_records(
                    candidate.kind,
                    installed_output,
                    target_root=target_root,
                    dependency_paths=dependency_paths,
                )
                if installed_dependencies != output_dependencies:
                    raise RuntimeError(
                        "installed output dependencies changed during promotion"
                    )

            _promote_candidate(
                candidate,
                staged_output,
                live_output,
                staging_root=staging_root,
                target_root=target_root,
                verifier=verify_installed_output,
            )
        except Exception as exc:  # each error belongs in the audit result
            results.append(
                ConversionResult(
                    id=candidate.id,
                    kind=candidate.kind,
                    source=candidate.source.as_posix(),
                    output=candidate.output.as_posix(),
                    enabled=False,
                    error=f"{type(exc).__name__}: {exc}",
                    details=details,
                )
            )
        else:
            results.append(
                ConversionResult(
                    id=candidate.id,
                    kind=candidate.kind,
                    source=candidate.source.as_posix(),
                    output=candidate.output.as_posix(),
                    enabled=True,
                    source_sha256=source_sha256,
                    output_sha256=output_sha256,
                    source_dependencies=source_dependencies,
                    output_dependencies=output_dependencies,
                    details=details,
                )
            )
        finally:
            if staging_root is not None and staging_root.exists():
                shutil.rmtree(staging_root)
    return tuple(sorted(results, key=lambda result: (_KIND_ORDER[result.kind], result.id)))


class IsaacAssetConverter:
    """Synchronous wrapper around Kit's asynchronous asset converter."""

    def __init__(self) -> None:
        import omni.kit.asset_converter

        self._module = omni.kit.asset_converter

    def __call__(self, candidate: ConversionCandidate) -> None:
        context = self._module.AssetConverterContext()
        context.ignore_animations = False
        context.ignore_materials = False
        context.keep_all_materials = True
        context.export_preview_surface = True
        context.create_world_as_default_root_prim = True
        context.use_meter_as_world_unit = True
        context.convert_stage_up_z = True
        context.disabling_instancing = True
        task = self._module.get_instance().create_converter_task(
            candidate.source.as_posix(), candidate.output.as_posix(), None, context
        )
        success = asyncio.get_event_loop().run_until_complete(task.wait_until_finished())
        if not success:
            status = task.get_status() if hasattr(task, "get_status") else "unknown"
            raise RuntimeError(f"asset converter failed with status {status}")


def validate_converted_usd(candidate: ConversionCandidate) -> Mapping[str, Any]:
    """Require the structural USD capabilities advertised by a candidate."""
    from pxr import Usd, UsdGeom, UsdSkel, UsdUtils

    stage = Usd.Stage.Open(candidate.output.as_posix())
    if stage is None:
        raise ValueError("USD stage could not be opened")
    if not stage.GetDefaultPrim():
        raise ValueError("USD stage has no default prim")
    _, _, unresolved = UsdUtils.ComputeAllDependencies(candidate.output.as_posix())
    if unresolved:
        raise ValueError(f"USD has unresolved dependencies: {sorted(map(str, unresolved))}")

    meshes = [prim for prim in stage.Traverse() if prim.IsA(UsdGeom.Mesh)]
    skeletons = [prim for prim in stage.Traverse() if prim.IsA(UsdSkel.Skeleton)]
    animations = [prim for prim in stage.Traverse() if prim.IsA(UsdSkel.Animation)]
    if candidate.kind != "synbody_motion" and not meshes:
        raise ValueError("character/activity USD contains no mesh")
    if candidate.kind in {"synbody_character", "rpm_character", "rpm_character_motion", "rpm_motion"}:
        if not skeletons:
            raise ValueError("articulated USD contains no skeleton")
    if candidate.kind in {"synbody_motion", "rpm_character_motion", "rpm_motion"} and not animations:
        raise ValueError("motion USD contains no UsdSkelAnimation")

    expected_joints = {"synbody_55": {55, 56}, "smplx_70": {70}, "rpm_87": {87}}
    allowed = expected_joints.get(candidate.profile)
    if allowed is not None and skeletons:
        counts = {len(UsdSkel.Skeleton(prim).GetJointsAttr().Get() or []) for prim in skeletons}
        if not counts.intersection(allowed):
            raise ValueError(f"skeleton joint counts {sorted(counts)} do not match {candidate.profile}")

    animation_times: list[float] = []
    for prim in animations:
        animation = UsdSkel.Animation(prim)
        rotation_times = animation.GetRotationsAttr().GetTimeSamples()
        if not rotation_times:
            raise ValueError("UsdSkelAnimation has no rotation samples")
        if not animation.GetTranslationsAttr().GetTimeSamples():
            raise ValueError("UsdSkelAnimation has no translation samples")
        if not animation.GetScalesAttr().GetTimeSamples():
            raise ValueError("UsdSkelAnimation has no scale samples")
        animation_times.extend(rotation_times)

    if meshes:
        bounds = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_]).ComputeWorldBound(
            stage.GetDefaultPrim()
        ).ComputeAlignedRange()
        values = tuple(bounds.GetMin()) + tuple(bounds.GetMax())
        if bounds.IsEmpty() or not all(math.isfinite(value) for value in values):
            raise ValueError("character/activity USD has invalid bounds")

    details: dict[str, Any] = {
        "animation_count": len(animations),
        "mesh_count": len(meshes),
        "skeleton_joint_counts": sorted(
            len(UsdSkel.Skeleton(prim).GetJointsAttr().Get() or []) for prim in skeletons
        ),
    }
    if animation_times:
        duration = (stage.GetEndTimeCode() - stage.GetStartTimeCode()) / stage.GetTimeCodesPerSecond()
        if not math.isfinite(duration) or duration <= 0.0:
            raise ValueError("motion USD has no positive duration")
        details.update(
            {
                "duration": duration,
                "sample_end": max(animation_times),
                "sample_start": min(animation_times),
                "time_codes_per_second": stage.GetTimeCodesPerSecond(),
            }
        )
    return details


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--target-root", type=Path, required=True)
    parser.add_argument("--kind", action="append", choices=tuple(_KIND_ORDER))
    parser.add_argument("--id", action="append", dest="ids")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--result-json", type=Path)
    return parser.parse_args()


def _atomic_write_text(
    path: Path,
    content: str,
    *,
    target_root: Path,
) -> None:
    path = ensure_safe_target_path(path, target_root)
    ensure_target_directory(path.parent, target_root)
    path = ensure_safe_target_path(path, target_root)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        temporary = ensure_safe_target_path(temporary, target_root)
        path = ensure_safe_target_path(path, target_root)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def run_conversion_session(
    plan: Iterable[ConversionCandidate],
    *,
    simulation_app,
    converter: Callable[[ConversionCandidate], None],
    validator: Callable[[ConversionCandidate], Mapping[str, Any] | None] | None,
    result_json: Path | None = None,
    source_root: str | Path,
    target_root: str | Path,
    force: bool = False,
) -> int:
    """Convert, persist diagnostics, then close Kit (which may terminate the process)."""
    exit_code = 1
    try:
        source_root = ensure_safe_source_path(source_root, source_root)
        target_root = ensure_safe_target_path(target_root, target_root)
        if result_json is not None:
            ensure_safe_target_path(result_json, target_root)
            ensure_target_directory(result_json.parent, target_root)
            ensure_safe_target_path(result_json, target_root)
        results = convert_candidates(
            plan,
            converter=converter,
            validator=validator,
            source_root=source_root,
            target_root=target_root,
            force=force,
        )
        document = [asdict(result) for result in results]
        output = json.dumps(document, indent=2, sort_keys=True) + "\n"
        if result_json:
            safe_result_json = ensure_safe_target_path(result_json, target_root)
            _atomic_write_text(
                safe_result_json,
                output,
                target_root=target_root,
            )
        print(output, end="", flush=True)
        exit_code = 0 if all(result.enabled for result in results) else 1
    finally:
        sys.stdout.flush()
        sys.stderr.flush()
        simulation_app.close(exit_code=exit_code)
    return exit_code


def main() -> int:
    args = _parse_args()
    plan = build_conversion_plan(args.source_root, args.target_root)
    if args.kind:
        plan = tuple(item for item in plan if item.kind in args.kind)
    if args.ids:
        plan = tuple(item for item in plan if item.id in args.ids)
    if args.plan_only:
        print(json.dumps([asdict(item) for item in plan], default=str, indent=2, sort_keys=True))
        return 0

    from isaacsim import SimulationApp

    simulation_app = SimulationApp({"headless": True})
    return run_conversion_session(
        plan,
        simulation_app=simulation_app,
        converter=IsaacAssetConverter(),
        validator=validate_converted_usd,
        result_json=args.result_json,
        source_root=args.source_root,
        target_root=args.target_root,
        force=args.force,
    )


if __name__ == "__main__":
    raise SystemExit(main())
