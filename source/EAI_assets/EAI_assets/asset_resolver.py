from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import shutil
import socket
import stat
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from EAI_assets.asset_requirements import (
    AssetRequirement,
    RequirementGraph,
    RequirementKind,
    RequirementState,
    attachment_requirement_id,
    resolve_card_requirement,
    resolve_selection,
)

DEFAULT_HF_REPO = "HuangQIjun/eai-simulator-assets"
DEFAULT_REPO_TYPE = "dataset"
DEFAULT_HF_REVISION = "main"
HF_BASE_URL = "https://huggingface.co"

_HUMAN_CHECKSUM_ALGORITHM = "sha256-path-content-v1"
_HUMAN_CHECKSUM_RELATIVE_PATH = Path("human/pack-checksums.json")
_HUMAN_PACK_ROOTS = {
    "usd/human/characters/**": "usd/human/characters",
    "usd/human/activities/**": "usd/human/activities",
    "usd/human/motions/**": "usd/human/motions",
}
_HUMAN_ROOT_METADATA = {
    "usd/human/manifest.json",
    "usd/human/manifest.schema.json",
    "usd/human/audit-summary.json",
    "usd/human/pack-checksums.json",
}

_HF_STRONG_STATUS_RE = re.compile(
    r"\bhttp(?:[ \t]+(?:error|status(?:[ \t]+code)?))?[ \t]+"
    r"(?P<http_status>401|403)(?=$|[ \t:])|"
    r"\b(?P<unauthorized_status>401)(?:[ \t]+(?:client[ \t]+error:[ \t]+)?|:[ \t]+)unauthorized\b|"
    r"\b(?P<forbidden_status>403)(?:[ \t]+(?:client[ \t]+error:[ \t]+)?|:[ \t]+)forbidden\b",
    re.IGNORECASE,
)
_HF_REVISION_ERROR_RE = re.compile(
    r"\b(?:revision|ref|commit)\b[^\r\n]{0,160}\b(?:not\s+found|unknown|does\s+not\s+exist)\b|"
    r"\b(?:not\s+found|unknown|does\s+not\s+exist)\b[^\r\n]{0,160}\b(?:revision|ref|commit)\b",
    re.IGNORECASE,
)
_HF_ACCESS_CONTEXT_RE = re.compile(
    r"\b(?:gated\s+(?:repository|repo|model|dataset)|"
    r"(?:repository|repo|model|dataset)(?:\s+\w+){0,3}\s+gated|"
    r"(?:unauthorized|forbidden)\s+(?:access|request)|"
    r"(?:access|request)(?:\s+\w+){0,2}\s+(?:unauthorized|forbidden))\b",
    re.IGNORECASE,
)
_HF_CREDENTIAL_CONTEXT_RE = re.compile(
    r"\b(?:(?:invalid|expired|missing|required)(?:\s+\w+)?\s+token|"
    r"token(?:\s+(?:is|was|has))?\s+(?:invalid|expired|missing|required)|"
    r"login(?:\s+is)?\s+required|not\s+logged\s+in|please\s+log\s+in|auth\s+login)\b",
    re.IGNORECASE,
)
_HF_NETWORK_CONTEXT_RE = re.compile(
    r"\b(?:connection(?:\s+(?:refused|reset|aborted|failed))?|"
    r"connect(?:ion)?\s+timeout|read\s+timeout|timed\s+out|"
    r"temporary\s+failure\s+in\s+name\s+resolution|name\s+or\s+service\s+not\s+known|"
    r"network\s+is\s+unreachable|no\s+route\s+to\s+host|proxy\s+error|"
    r"dns\s+(?:error|failure)|max\s+retries\s+exceeded)\b",
    re.IGNORECASE,
)

_USD_EXTENSIONS = (".usd", ".usda", ".usdc")
_LOCAL_ASSET_EXTENSIONS = _USD_EXTENSIONS + (
    ".mdl",
    ".png",
    ".jpg",
    ".jpeg",
    ".dds",
    ".exr",
    ".hdr",
)
_CONTROLLER_ASSET_EXTENSIONS = (
    ".py",
    ".yaml",
    ".yml",
    ".json",
    ".onnx",
    ".pt",
    ".pth",
    ".ckpt",
    ".jit",
    ".pkl",
    ".engine",
    ".safetensors",
    ".npz",
    ".npy",
)
_DEPENDENCY_ATTRS = ("asset_dependencies", "usd_dependencies", "eai_asset_dependencies")
_PATH_ATTRS = (
    "usd_path",
    "walk_animation_usd_path",
)
_CONTROLLER_PATH_ATTRS = (
    "model_path",
    "nav_model_path",
    "locomotion_model_path",
)
_CONTROLLER_BUNDLE_DEPENDENCIES = {
    ("traditional", "z1_ik"): (
        Path("traditional/manipulator_ik/manipulator_ik.py"),
    ),
    ("traditional", "ur5_ik"): (
        Path("traditional/manipulator_ik/__init__.py"),
        Path("traditional/manipulator_ik/manipulator_ik.py"),
    ),
    # SKRL bundles pull in their shared rl_cfg module
    ("rl", "g1_skrl"): (
        Path("rl/rl_cfg/__init__.py"),
        Path("rl/rl_cfg/g1_skrl_flat_ppo_cfg.yaml"),
    ),
    ("rl", "quadcopter_goal_skrl"): (
        Path("rl/rl_cfg/__init__.py"),
        Path("rl/rl_cfg/quadcopter_goal_skrl_ppo_cfg.yaml"),
    ),
}


class AssetDownloadError(RuntimeError):
    """Raised when an asset download fails for a non-authorization reason."""


class AssetDownloadAccessError(AssetDownloadError):
    """Raised when the gated Hugging Face asset repo cannot be accessed."""


class AssetDownloadNetworkError(AssetDownloadError):
    """Raised when Hugging Face cannot be reached through the current network."""


class AssetIntegrityError(RuntimeError):
    """Raised when a staged asset pack does not match its release checksum."""


def _walk_asset_tree(
    root: Path,
    *,
    description: str,
) -> Iterable[tuple[str, list[str], list[str]]]:
    def fail(error: OSError) -> None:
        raise AssetIntegrityError(f"Cannot inspect {description}: {error}") from error

    try:
        yield from os.walk(root, followlinks=False, onerror=fail)
    except AssetIntegrityError:
        raise
    except OSError as exc:
        raise AssetIntegrityError(f"Cannot inspect {description}: {exc}") from exc


def compute_asset_pack_checksum(pack_root: str | os.PathLike[str]) -> dict[str, int | str]:
    """Compute one pack checksum; callers must not mutate staging concurrently."""

    root = Path(pack_root)
    try:
        root_status = root.lstat()
    except FileNotFoundError as exc:
        raise AssetIntegrityError(f"Asset pack root is missing: {root}") from exc
    if stat.S_ISLNK(root_status.st_mode):
        raise AssetIntegrityError(f"Asset pack root must not be a symlink: {root}")
    if not stat.S_ISDIR(root_status.st_mode):
        raise AssetIntegrityError(f"Asset pack root is not a directory: {root}")

    files: list[tuple[str, Path]] = []
    try:
        for directory, dirnames, filenames in _walk_asset_tree(root, description=f"asset pack {root}"):
            directory_path = Path(directory)
            for name in dirnames:
                child = directory_path / name
                child_status = child.lstat()
                if stat.S_ISLNK(child_status.st_mode):
                    raise AssetIntegrityError(f"Asset pack contains a symlink: {child}")
                if not stat.S_ISDIR(child_status.st_mode):
                    raise AssetIntegrityError(f"Asset pack contains a special file: {child}")
            for name in filenames:
                child = directory_path / name
                child_status = child.lstat()
                if stat.S_ISLNK(child_status.st_mode):
                    raise AssetIntegrityError(f"Asset pack contains a symlink: {child}")
                if not stat.S_ISREG(child_status.st_mode):
                    raise AssetIntegrityError(f"Asset pack contains a special file: {child}")
                files.append((child.relative_to(root).as_posix(), child))
    except AssetIntegrityError:
        raise
    except OSError as exc:
        raise AssetIntegrityError(f"Cannot inspect asset pack {root}: {exc}") from exc

    aggregate = hashlib.sha256()
    size_bytes = 0
    open_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    for relative_path, path in sorted(files, key=lambda item: item[0]):
        file_digest = hashlib.sha256()
        descriptor = os.open(path, open_flags)
        try:
            descriptor_status = os.fstat(descriptor)
            if not stat.S_ISREG(descriptor_status.st_mode):
                raise AssetIntegrityError(f"Asset pack contains a special file: {path}")
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                while chunk := stream.read(1024 * 1024):
                    size_bytes += len(chunk)
                    file_digest.update(chunk)
        finally:
            os.close(descriptor)
        encoded_path = relative_path.encode("utf-8")
        aggregate.update(len(encoded_path).to_bytes(8, "big"))
        aggregate.update(encoded_path)
        aggregate.update(file_digest.digest())

    return {
        "file_count": len(files),
        "size_bytes": size_bytes,
        "sha256": aggregate.hexdigest(),
    }


class AssetStatus:
    __slots__ = ("requirement", "state", "missing_paths", "message")

    def __init__(
        self,
        requirement: AssetRequirement,
        state: RequirementState,
        missing_paths: tuple[str, ...] = (),
        message: str = "",
    ) -> None:
        self.requirement = requirement
        self.state = state
        self.missing_paths = tuple(missing_paths)
        self.message = message


def requirement_local_paths(requirement: AssetRequirement) -> tuple[Path, ...]:
    root = controller_root() if requirement.kind is RequirementKind.CONTROLLER else usd_root()
    paths = tuple(root / path for path in requirement.relative_paths)
    if requirement.kind is RequirementKind.CONTROLLER:
        return tuple(Path(path) for path in _expand_controller_asset_paths(paths))
    return paths


def inspect_requirement(requirement: AssetRequirement) -> AssetStatus:
    missing = tuple(str(path) for path in requirement_local_paths(requirement) if not path.exists())
    if not missing:
        return AssetStatus(requirement, RequirementState.READY)
    return AssetStatus(
        requirement,
        RequirementState.MISSING,
        missing,
        f"{requirement.label} is missing: {', '.join(missing)}",
    )


def inspect_graph(graph: RequirementGraph) -> tuple[AssetStatus, ...]:
    return tuple(inspect_requirement(item) for item in graph.requirements)


def _status_for_download_error(requirement: AssetRequirement, exc: Exception) -> AssetStatus:
    message = f"{requirement.label} ({requirement.id}): {exc}"
    original = exc.__cause__ if exc.__cause__ is not None else exc
    if isinstance(exc, AssetDownloadAccessError):
        state = (
            RequirementState.AUTH_REQUIRED
            if _hf_error_status_code(original) == 401 or _has_hf_credential_context(original)
            else RequirementState.ACCESS_PENDING
        )
    else:
        state = RequirementState.FAILED
    return AssetStatus(requirement, state, tuple(str(path) for path in requirement_local_paths(requirement)), message)


def download_requirement(
    requirement: AssetRequirement,
    *,
    downloader: Callable[..., Any] | None = None,
    progress: Callable[[str], None] | None = None,
) -> AssetStatus:
    """Download one requirement without prompting or reading a token from UI."""

    current = inspect_requirement(requirement)
    if current.state is RequirementState.READY:
        return current
    if progress is not None:
        progress(requirement.id)
    missing_paths = tuple(str(path) for path in requirement_local_paths(requirement))
    if not missing_paths:
        return AssetStatus(requirement, RequirementState.READY)
    remote_root = "controller" if requirement.kind is RequirementKind.CONTROLLER else "usd"
    local_root = controller_root() if remote_root == "controller" else usd_root()
    patterns = _allow_patterns_for_paths(missing_paths)
    repo_id = os.environ.get("EAI_ASSETS_HF_REPO", DEFAULT_HF_REPO)
    download = downloader or _download_from_hf
    try:
        _download_and_install_assets(
            lambda local_dir: download(
                repo_id=repo_id,
                repo_type=DEFAULT_REPO_TYPE,
                local_dir=str(local_dir),
                allow_patterns=patterns,
                interactive_auth=False,
            ),
            patterns=patterns,
            remote_root=remote_root,
            local_root=local_root,
            required_paths=missing_paths,
        )
    except AssetIntegrityError:
        raise
    except TypeError as exc:
        # Keep test/custom downloaders compatible with the historical kwargs.
        if "interactive_auth" not in str(exc):
            return _status_for_download_error(requirement, exc)
        try:
            _download_and_install_assets(
                lambda local_dir: download(
                    repo_id=repo_id,
                    repo_type=DEFAULT_REPO_TYPE,
                    local_dir=str(local_dir),
                    allow_patterns=patterns,
                ),
                patterns=patterns,
                remote_root=remote_root,
                local_root=local_root,
                required_paths=missing_paths,
            )
        except AssetIntegrityError:
            raise
        except Exception as retry_exc:
            return _status_for_download_error(requirement, retry_exc)
    except Exception as exc:
        return _status_for_download_error(requirement, exc)
    result = inspect_requirement(requirement)
    if result.state is RequirementState.READY:
        if progress is not None:
            progress(f"ready:{requirement.id}")
        return result
    return AssetStatus(
        requirement,
        RequirementState.FAILED,
        result.missing_paths,
        f"Download completed but required files are still missing for {requirement.id}: {', '.join(result.missing_paths)}",
    )


def download_graph(
    graph: RequirementGraph,
    *,
    requirements: Iterable[str] | None = None,
    downloader: Callable[..., Any] | None = None,
    progress: Callable[[str], None] | None = None,
) -> tuple[AssetStatus, ...]:
    selected = set(requirements) if requirements is not None else None
    return tuple(
        download_requirement(item, downloader=downloader, progress=progress)
        for item in graph.requirements
        if selected is None or item.id in selected
    )


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def usd_root() -> Path:
    configured = os.environ.get("EAI_USD_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    return repo_root() / "usd"


def controller_root() -> Path:
    configured = os.environ.get("EAI_CONTROLLER_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    return repo_root() / "source" / "EAI_assets" / "EAI_assets" / "controller"


def asset_path(relative_path: str | os.PathLike[str]) -> str:
    rel = Path(relative_path)
    if rel.is_absolute():
        return str(rel)
    parts = rel.parts
    if parts and parts[0] == "usd":
        rel = Path(*parts[1:])
    return str(usd_root() / rel)


def controller_path(relative_path: str | os.PathLike[str]) -> str:
    rel = Path(relative_path)
    if rel.is_absolute():
        return str(rel)
    parts = rel.parts
    if parts and parts[0] == "controller":
        rel = Path(*parts[1:])
    return str(controller_root() / rel)


def collect_usd_asset_paths(cfg: Any) -> list[str]:
    paths: list[str] = []
    seen_objects: set[int] = set()
    seen_paths: set[str] = set()

    def add_path(value: Any, *, allow_non_usd: bool = False) -> None:
        if not isinstance(value, (str, os.PathLike)):
            return
        path = str(value)
        lower = path.lower()
        allowed_extensions = _LOCAL_ASSET_EXTENSIONS if allow_non_usd else _USD_EXTENSIONS
        if not lower.endswith(allowed_extensions):
            return
        if _is_local_usd_path(path):
            normalized = _normalize_local_usd_path(path)
        elif _is_existing_external_local_asset_path(path):
            normalized = str(Path(path).expanduser().resolve())
        else:
            return
        if normalized not in seen_paths:
            seen_paths.add(normalized)
            paths.append(normalized)

    def visit(obj: Any) -> None:
        if obj is None or isinstance(obj, (str, bytes, int, float, bool)):
            return
        obj_id = id(obj)
        if obj_id in seen_objects:
            return
        seen_objects.add(obj_id)

        if isinstance(obj, dict):
            for key, value in obj.items():
                if key in _PATH_ATTRS:
                    add_path(value)
                elif key in _DEPENDENCY_ATTRS:
                    for dep in _as_iterable(value):
                        add_path(dep, allow_non_usd=True)
                visit(value)
            return

        if isinstance(obj, (list, tuple, set, frozenset)):
            for item in obj:
                visit(item)
            return

        for attr in _PATH_ATTRS:
            if hasattr(obj, attr):
                add_path(getattr(obj, attr))
        for attr in _DEPENDENCY_ATTRS:
            if hasattr(obj, attr):
                for dep in _as_iterable(getattr(obj, attr)):
                    add_path(dep, allow_non_usd=True)

        namespace = getattr(obj, "__dict__", None)
        if isinstance(namespace, dict):
            for value in namespace.values():
                visit(value)
            return

        slots = getattr(obj, "__slots__", ())
        if isinstance(slots, str):
            slots = (slots,)
        for slot in slots:
            if hasattr(obj, slot):
                visit(getattr(obj, slot))

    visit(cfg)
    return paths


def collect_controller_asset_paths(cfg: Any) -> list[str]:
    paths: list[str] = []
    seen_objects: set[int] = set()
    seen_paths: set[str] = set()

    def add_path(value: Any) -> None:
        if not isinstance(value, (str, os.PathLike)):
            return
        path = str(value)
        if not path.lower().endswith(_CONTROLLER_ASSET_EXTENSIONS):
            return
        if not _is_local_controller_path(path):
            return
        normalized = _normalize_local_controller_path(path)
        if normalized not in seen_paths:
            seen_paths.add(normalized)
            paths.append(normalized)

    def visit(obj: Any) -> None:
        if obj is None or isinstance(obj, (str, bytes, int, float, bool)):
            return
        obj_id = id(obj)
        if obj_id in seen_objects:
            return
        seen_objects.add(obj_id)

        if isinstance(obj, dict):
            for key, value in obj.items():
                if key in _CONTROLLER_PATH_ATTRS:
                    add_path(value)
                visit(value)
            return

        if isinstance(obj, (list, tuple, set, frozenset)):
            for item in obj:
                visit(item)
            return

        for attr in _CONTROLLER_PATH_ATTRS:
            if hasattr(obj, attr):
                add_path(getattr(obj, attr))

        namespace = getattr(obj, "__dict__", None)
        if isinstance(namespace, dict):
            for value in namespace.values():
                visit(value)
            return

        slots = getattr(obj, "__slots__", ())
        if isinstance(slots, str):
            slots = (slots,)
        for slot in slots:
            if hasattr(obj, slot):
                visit(getattr(obj, slot))

    visit(cfg)
    return paths


def ensure_usd_assets_for_cfg(
    cfg: Any,
    *,
    downloader: Callable[..., Any] | None = None,
) -> list[str]:
    paths = collect_usd_asset_paths(cfg)
    return ensure_usd_assets_for_paths(paths, downloader=downloader)


def ensure_usd_assets_for_paths(
    paths: Iterable[str],
    *,
    downloader: Callable[..., Any] | None = None,
) -> list[str]:
    return _ensure_asset_paths(
        paths,
        asset_label="USD assets",
        remote_root="usd",
        local_root=usd_root(),
        downloader=downloader,
    )


def ensure_controller_package_available(
    *,
    downloader: Callable[..., Any] | None = None,
) -> list[str]:
    package_init = controller_root() / "__init__.py"
    if package_init.exists():
        return []
    return ensure_controller_assets_for_paths([str(package_init)], downloader=downloader)


def ensure_controller_assets_for_paths(
    paths: Iterable[str],
    *,
    downloader: Callable[..., Any] | None = None,
) -> list[str]:
    return _ensure_asset_paths(
        _expand_controller_asset_paths(paths),
        asset_label="controller assets",
        remote_root="controller",
        local_root=controller_root(),
        downloader=downloader,
    )


def ensure_controller_module_available(
    module_name: str,
    *,
    downloader: Callable[..., Any] | None = None,
) -> list[str]:
    prefix = "EAI_assets.controller."
    if not isinstance(module_name, str) or not module_name.startswith(prefix):
        raise ValueError(f"Not an on-demand controller module: {module_name!r}")

    relative_parts = tuple(part for part in module_name[len(prefix) :].split(".") if part)
    if len(relative_parts) < 2 or any(not part.isidentifier() for part in relative_parts):
        raise ValueError(f"Cannot resolve controller bundle for module: {module_name!r}")

    bundle_parts = relative_parts[:2]
    module_parts = relative_parts if len(relative_parts) > 2 else (*bundle_parts, bundle_parts[-1])
    module_path = controller_root().joinpath(*module_parts).with_suffix(".py")
    return ensure_controller_assets_for_paths([str(module_path)], downloader=downloader)


def _expand_controller_asset_paths(paths: Iterable[str | os.PathLike[str]]) -> list[str]:
    expanded: list[str] = []
    seen: set[str] = set()

    def normalize(path: str | os.PathLike[str]) -> Path:
        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            parts = candidate.parts
            if parts and parts[0] == "controller":
                candidate = Path(*parts[1:])
            candidate = controller_root() / candidate
        return candidate

    def add(path: str | os.PathLike[str]) -> None:
        normalized = str(normalize(path))
        if normalized not in seen:
            seen.add(normalized)
            expanded.append(normalized)

    for path in paths:
        local_path = normalize(path)
        add(local_path)
        remote = _remote_path_for_local(str(local_path))
        parts = remote.parts
        if len(parts) < 3 or parts[0] != "controller":
            continue
        for dependency in _CONTROLLER_BUNDLE_DEPENDENCIES.get((parts[1], parts[2]), ()):
            add(dependency)
    return expanded


def verify_human_asset_packs(
    download_root: str | os.PathLike[str],
    allow_patterns: Iterable[str],
    checksum_manifest: Mapping[str, Any] | str | os.PathLike[str],
) -> None:
    """Verify only the requested stable human packs in a staged download."""

    manifest = _load_human_checksum_manifest(checksum_manifest)
    requested = _requested_human_pack_patterns(allow_patterns)
    packs = manifest["packs"]
    for pattern in requested:
        expected = packs.get(pattern)
        if not isinstance(expected, Mapping):
            raise AssetIntegrityError(f"missing checksum for requested human pack: {pattern}")
        remote_root = _HUMAN_PACK_ROOTS[pattern]
        if expected.get("root") != remote_root:
            raise AssetIntegrityError(
                f"checksum root mismatch for {pattern}: expected {remote_root!r}, got {expected.get('root')!r}"
            )
        staged_root = Path(download_root) / remote_root
        if not staged_root.exists():
            raise AssetIntegrityError(f"missing staged pack root for {pattern}: {staged_root}")
        actual = compute_asset_pack_checksum(staged_root)
        expected_checksum = {
            "file_count": expected.get("file_count"),
            "size_bytes": expected.get("size_bytes"),
            "sha256": expected.get("sha256"),
        }
        if actual != expected_checksum:
            raise AssetIntegrityError(
                f"checksum mismatch for {pattern}: expected {expected_checksum!r}, got {actual!r}"
            )


def _load_human_checksum_manifest(
    checksum_manifest: Mapping[str, Any] | str | os.PathLike[str],
) -> Mapping[str, Any]:
    if isinstance(checksum_manifest, Mapping):
        manifest = checksum_manifest
    else:
        path = Path(checksum_manifest)
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise AssetIntegrityError(f"Required human checksum manifest is missing: {path}") from exc
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise AssetIntegrityError(f"Cannot read human checksum manifest {path}: {exc}") from exc
    if not isinstance(manifest, Mapping):
        raise AssetIntegrityError("Human pack-checksums.json must contain a JSON object")
    version = manifest.get("version")
    if type(version) is not int or version != 1:
        raise AssetIntegrityError("Human pack-checksums.json has an unsupported version")
    if manifest.get("algorithm") != _HUMAN_CHECKSUM_ALGORITHM:
        raise AssetIntegrityError("Human pack-checksums.json has an unsupported checksum algorithm")
    revision = _hf_revision()
    if manifest.get("revision") != revision:
        raise AssetIntegrityError(
            f"Human pack-checksums.json revision mismatch: expected {revision!r}, got {manifest.get('revision')!r}; "
            "checkout/provide matching checksum metadata for the selected revision before downloading"
        )
    packs = manifest.get("packs")
    if not isinstance(packs, Mapping):
        raise AssetIntegrityError("Human pack-checksums.json is missing the packs object")
    for pattern, entry in packs.items():
        if not isinstance(pattern, str) or pattern not in _HUMAN_PACK_ROOTS:
            raise AssetIntegrityError(f"Human pack-checksums.json has an unknown pack key: {pattern!r}")
        if not isinstance(entry, Mapping):
            raise AssetIntegrityError(f"Human pack-checksums.json pack entry must be an object: {pattern}")
        expected_root = _HUMAN_PACK_ROOTS[pattern]
        if entry.get("root") != expected_root:
            raise AssetIntegrityError(
                f"Human pack-checksums.json root mismatch for {pattern}: expected {expected_root!r}"
            )
        for field in ("file_count", "size_bytes"):
            value = entry.get(field)
            if type(value) is not int or value < 0:
                raise AssetIntegrityError(
                    f"Human pack-checksums.json {field} must be a non-negative integer for {pattern}"
                )
        sha256 = entry.get("sha256")
        if not isinstance(sha256, str) or re.fullmatch(r"[0-9a-f]{64}", sha256) is None:
            raise AssetIntegrityError(
                f"Human pack-checksums.json sha256 must be 64 lowercase hex characters for {pattern}"
            )
    return manifest


def _requested_human_pack_patterns(allow_patterns: Iterable[str]) -> list[str]:
    requested: list[str] = []
    seen: set[str] = set()
    for pattern in allow_patterns:
        if pattern in _HUMAN_PACK_ROOTS and pattern not in seen:
            seen.add(pattern)
            requested.append(pattern)
    return requested


class _human_download_target_dir:
    def __init__(self, *, local_root: Path) -> None:
        self._local_root = local_root
        self._temp_dir: tempfile.TemporaryDirectory[str] | None = None

    def __enter__(self) -> Path:
        human_root = self._local_root / "human"
        human_root.mkdir(parents=True, exist_ok=True)
        self._temp_dir = tempfile.TemporaryDirectory(
            prefix=".eai-human-assets-",
            dir=human_root,
        )
        return Path(self._temp_dir.name)

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._temp_dir is not None:
            self._temp_dir.cleanup()


def _download_and_install_assets(
    download_action: Callable[[Path], None],
    *,
    patterns: list[str],
    remote_root: str,
    local_root: Path,
    required_paths: Iterable[str],
) -> None:
    local_required_paths = _local_required_paths(
        required_paths,
        remote_root=remote_root,
        local_root=local_root,
    )
    human_patterns = _requested_human_pack_patterns(patterns)
    if not human_patterns:
        with _download_target_dir(local_root=local_root, remote_root=remote_root) as local_dir:
            download_action(local_dir)
            _sync_external_asset_root(local_dir, remote_root=remote_root, local_root=local_root)
        return

    transaction_roots = _transaction_roots_for_patterns(patterns, remote_root=remote_root)
    checksum_path = local_root / _HUMAN_CHECKSUM_RELATIVE_PATH
    checksum_manifest = _load_human_checksum_manifest(checksum_path)
    with _human_download_target_dir(local_root=local_root) as local_dir:
        download_action(local_dir)
        verify_human_asset_packs(local_dir, human_patterns, checksum_manifest)
        _validate_staged_required_files(
            local_dir,
            local_required_paths,
            remote_root=remote_root,
            local_root=local_root,
            transaction_roots=transaction_roots,
        )
        if not all((local_dir / root).exists() for root in transaction_roots):
            return
        _install_staged_asset_roots(
            local_dir,
            transaction_roots,
            remote_root=remote_root,
            local_root=local_root,
            required_paths=local_required_paths,
        )


def _local_required_paths(
    required_paths: Iterable[str],
    *,
    remote_root: str,
    local_root: Path,
) -> tuple[str, ...]:
    local_paths: list[str] = []
    for required_path in required_paths:
        candidate = Path(required_path).expanduser()
        if not candidate.is_absolute():
            parts = candidate.parts
            if parts and parts[0] == remote_root:
                candidate = Path(*parts[1:])
            candidate = local_root / candidate
        local_paths.append(str(candidate))
    return tuple(local_paths)


def _staged_required_remote_paths(
    required_path: str,
    *,
    remote_root: str,
    local_root: Path,
    transaction_roots: tuple[Path, ...],
) -> tuple[Path, ...]:
    candidate = Path(required_path).expanduser()
    if any(part in {".", ".."} for part in candidate.parts):
        raise AssetIntegrityError(f"Required asset path escapes {remote_root}: {required_path}")
    if not candidate.is_absolute():
        try:
            remote_path = _remote_path_for_local(required_path)
        except ValueError:
            return ()
        if (
            remote_path.is_absolute()
            or len(remote_path.parts) < 2
            or any(part in {".", ".."} for part in remote_path.parts)
            or remote_path.parts[0] != remote_root
        ):
            return (remote_path,)
        candidate = local_root.joinpath(*remote_path.parts[1:])

    seen: set[Path] = set()
    identities = (
        candidate,
        candidate.parent.resolve() / candidate.name,
        candidate.resolve(),
    )
    for identity in identities:
        try:
            relative_path = identity.relative_to(local_root)
        except ValueError:
            continue
        remote_path = Path(remote_root) / relative_path
        if remote_path in seen:
            continue
        seen.add(remote_path)
        if remote_path.is_absolute() or len(remote_path.parts) < 2:
            return (remote_path,)
        if any(remote_path == root or root in remote_path.parents for root in transaction_roots):
            return (remote_path,)
    return ()


def _validate_staged_required_files(
    download_root: Path,
    required_paths: Iterable[str],
    *,
    remote_root: str,
    local_root: Path,
    transaction_roots: Iterable[Path],
) -> None:
    staged_roots = tuple(transaction_roots)
    remote_paths: list[Path] = []
    invalid_paths: list[Path] = []
    for required_path in required_paths:
        for remote_path in _staged_required_remote_paths(
            required_path,
            remote_root=remote_root,
            local_root=local_root,
            transaction_roots=staged_roots,
        ):
            if (
                remote_path.is_absolute()
                or len(remote_path.parts) < 2
                or any(part in {".", ".."} for part in remote_path.parts)
            ):
                raise AssetIntegrityError(f"Required asset path escapes {remote_root}: {required_path}")
            if remote_path.parts[0] != remote_root:
                continue
            if not any(remote_path == root or root in remote_path.parents for root in staged_roots):
                continue
            remote_paths.append(remote_path)
            target_status = _lstat_staged_path(download_root, remote_path)
            if target_status is None:
                invalid_paths.append(remote_path)
            elif not stat.S_ISREG(target_status.st_mode):
                invalid_paths.append(remote_path)

    if invalid_paths:
        required_text = "\n".join(f"  - {path.as_posix()}" for path in remote_paths)
        raise FileNotFoundError(
            "Asset download completed without all requested staged files:\n"
            f"{required_text}"
        )


def _lstat_staged_path(download_root: Path, remote_path: Path) -> os.stat_result | None:
    current = download_root
    try:
        current_status = current.lstat()
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(current_status.st_mode):
        raise AssetIntegrityError(f"Transactional staging root must not be a symlink: {download_root}")
    if not stat.S_ISDIR(current_status.st_mode):
        raise AssetIntegrityError(f"Transactional staging root is not a directory: {download_root}")
    for part in remote_path.parts:
        current /= part
        try:
            current_status = current.lstat()
        except (FileNotFoundError, NotADirectoryError):
            return None
        if stat.S_ISLNK(current_status.st_mode):
            raise AssetIntegrityError(f"Transactional asset path contains a symlink: {remote_path}")
    return current_status


def _transaction_roots_for_patterns(
    patterns: Iterable[str],
    *,
    remote_root: str,
) -> list[Path]:
    roots: list[Path] = []
    for pattern in patterns:
        if pattern.endswith("/**"):
            root_text = pattern[:-3]
        elif any(character in pattern for character in "*?[]"):
            raise AssetIntegrityError(f"Unsupported transactional asset pattern: {pattern!r}")
        else:
            root_text = pattern
        root = Path(root_text)
        if (
            root.is_absolute()
            or len(root.parts) < 2
            or root.parts[0] != remote_root
            or any(part in {".", ".."} for part in root.parts)
        ):
            raise AssetIntegrityError(f"Transactional asset root escapes {remote_root}: {pattern!r}")
        for existing in roots:
            if root == existing or existing in root.parents or root in existing.parents:
                raise AssetIntegrityError(
                    f"Refusing overlapping transactional asset patterns: {existing.as_posix()!r} and {pattern!r}"
                )
        roots.append(root)
    return roots


def _install_staged_asset_roots(
    download_root: Path,
    remote_roots: Iterable[Path],
    *,
    remote_root: str,
    local_root: Path,
    required_paths: Iterable[str] = (),
) -> None:
    staged_roots = list(remote_roots)
    required_local_paths = tuple(Path(path) for path in required_paths)
    backup_root = _preflight_staged_asset_install(
        download_root,
        staged_roots,
        remote_root=remote_root,
        local_root=local_root,
    )
    records: list[dict[str, Any]] = []
    try:
        for index, staged_root in enumerate(staged_roots):
            source = download_root / staged_root
            destination = local_root.joinpath(*staged_root.parts[1:])
            try:
                source.resolve().relative_to(download_root.resolve())
                destination.parent.resolve().relative_to(local_root.resolve())
            except ValueError as exc:
                raise AssetIntegrityError(f"Transactional asset root escapes its staging or local root: {staged_root}") from exc
            destination.parent.mkdir(parents=True, exist_ok=True)
            backup = backup_root / f"{index}-{destination.name}"
            record = {
                "source": source,
                "destination": destination,
                "backup": backup,
                "backed_up": False,
                "installed": False,
            }
            records.append(record)
            if os.path.lexists(destination):
                backup.parent.mkdir(parents=True, exist_ok=True)
                record["backed_up"] = True
                os.replace(destination, backup)
            record["installed"] = True
            os.replace(source, destination)
        missing_paths = tuple(path for path in required_local_paths if not path.exists())
        if missing_paths:
            missing_text = "\n".join(f"  - {path}" for path in missing_paths)
            raise FileNotFoundError(
                "Asset transaction completed without all requested local files:\n"
                f"{missing_text}"
            )
    except BaseException:
        for record in reversed(records):
            source = record["source"]
            destination = record["destination"]
            backup = record["backup"]
            if record["installed"] and os.path.lexists(destination):
                source.parent.mkdir(parents=True, exist_ok=True)
                os.replace(destination, source)
            if record["backed_up"] and os.path.lexists(backup):
                destination.parent.mkdir(parents=True, exist_ok=True)
                os.replace(backup, destination)
        raise


def _preflight_staged_asset_install(
    download_root: Path,
    remote_roots: Iterable[Path],
    *,
    remote_root: str,
    local_root: Path,
) -> Path:
    staged_roots = list(remote_roots)
    _validate_staged_asset_roots(download_root, staged_roots)
    backup_root = download_root / ".eai-human-backups"
    if os.path.lexists(backup_root):
        raise AssetIntegrityError(f"Transactional reserved backup path already exists: {backup_root}")
    for staged_root in staged_roots:
        if not staged_root.parts or staged_root.parts[0] != remote_root:
            raise AssetIntegrityError(f"Transactional asset root escapes {remote_root}: {staged_root}")
        destination = local_root.joinpath(*staged_root.parts[1:])
        _validate_transaction_destination_parent(local_root, destination.parent)
    return backup_root


def _validate_transaction_destination_parent(local_root: Path, destination_parent: Path) -> None:
    try:
        relative_parent = destination_parent.relative_to(local_root)
    except ValueError as exc:
        raise AssetIntegrityError(f"Transactional destination escapes its local root: {destination_parent}") from exc

    current = local_root
    for part in (None, *relative_parent.parts):
        if part is not None:
            current /= part
        try:
            current_status = current.lstat()
        except FileNotFoundError:
            return
        if stat.S_ISLNK(current_status.st_mode):
            raise AssetIntegrityError(f"Transactional destination contains a symlink: {current}")
        if not stat.S_ISDIR(current_status.st_mode):
            raise AssetIntegrityError(f"Transactional destination contains a non-directory ancestor: {current}")


def _validate_staged_asset_roots(download_root: Path, remote_roots: Iterable[Path]) -> None:
    for remote_root in remote_roots:
        root_status = _lstat_staged_path(download_root, remote_root)
        staged_root = download_root / remote_root
        if root_status is None:
            raise AssetIntegrityError(f"Transactional asset root is missing: {remote_root}")
        if stat.S_ISREG(root_status.st_mode):
            continue
        if not stat.S_ISDIR(root_status.st_mode):
            raise AssetIntegrityError(f"Transactional asset root is a special file: {remote_root}")

        description = f"transactional asset root {remote_root}"
        try:
            for directory, dirnames, filenames in _walk_asset_tree(staged_root, description=description):
                directory_path = Path(directory)
                for name in dirnames:
                    child = directory_path / name
                    child_status = child.lstat()
                    if stat.S_ISLNK(child_status.st_mode):
                        raise AssetIntegrityError(f"Transactional asset root contains a symlink: {child}")
                    if not stat.S_ISDIR(child_status.st_mode):
                        raise AssetIntegrityError(f"Transactional asset root contains a special file: {child}")
                for name in filenames:
                    child = directory_path / name
                    child_status = child.lstat()
                    if stat.S_ISLNK(child_status.st_mode):
                        raise AssetIntegrityError(f"Transactional asset root contains a symlink: {child}")
                    if not stat.S_ISREG(child_status.st_mode):
                        raise AssetIntegrityError(f"Transactional asset root contains a special file: {child}")
        except AssetIntegrityError:
            raise
        except OSError as exc:
            raise AssetIntegrityError(f"Cannot inspect {description}: {exc}") from exc


def _ensure_asset_paths(
    paths: Iterable[str],
    *,
    asset_label: str,
    remote_root: str,
    local_root: Path,
    downloader: Callable[..., Any] | None = None,
) -> list[str]:
    requested_paths = _local_required_paths(
        paths,
        remote_root=remote_root,
        local_root=local_root,
    )
    missing = [path for path in requested_paths if not Path(path).exists()]
    if not missing:
        return []

    patterns = _allow_patterns_for_paths(missing)
    if not _auto_download_enabled():
        missing_text = "\n".join(f"  - {path}" for path in missing)
        raise FileNotFoundError(
            f"Missing local {asset_label} and EAI_ASSETS_AUTO_DOWNLOAD=0:\n"
            f"{missing_text}\n"
            f"Required HF patterns: {', '.join(patterns)}"
        )

    repo_id = os.environ.get("EAI_ASSETS_HF_REPO", DEFAULT_HF_REPO)
    download = downloader or _download_from_hf
    print(
        f"[EAI Assets] Missing {asset_label} detected. Downloading only required bundles: "
        + ", ".join(patterns)
    )

    def download_action(local_dir: Path) -> None:
        while True:
            try:
                download(
                    repo_id=repo_id,
                    repo_type=DEFAULT_REPO_TYPE,
                    local_dir=str(local_dir),
                    allow_patterns=patterns,
                )
                break
            except AssetIntegrityError:
                raise
            except AssetDownloadAccessError as exc:
                _prompt_retry_after_access_request(repo_id, patterns, exc)

    _download_and_install_assets(
        download_action,
        patterns=patterns,
        remote_root=remote_root,
        local_root=local_root,
        required_paths=requested_paths,
    )

    still_missing = [path for path in requested_paths if not Path(path).exists()]
    if still_missing:
        missing_text = "\n".join(f"  - {path}" for path in still_missing)
        raise FileNotFoundError(
            _hf_download_error_message(
                repo_id,
                patterns,
                RuntimeError(
                    f"HF asset download completed but these {asset_label} files are still missing:\n"
                    f"{missing_text}"
                ),
            )
        )
    return patterns


def _allow_patterns_for_paths(paths: Iterable[str]) -> list[str]:
    patterns: list[str] = []
    seen: set[str] = set()
    for path in paths:
        remote = _remote_path_for_local(path)
        parts = remote.parts
        remote_text = remote.as_posix()
        if remote_text in _HUMAN_ROOT_METADATA:
            pattern = remote_text
        elif (
            len(parts) >= 3
            and parts[:2] == ("usd", "human")
            and (Path(*parts[:3]).as_posix() + "/**") in _HUMAN_PACK_ROOTS
        ):
            pattern = Path(*parts[:3]).as_posix() + "/**"
        elif parts and parts[0] == "controller":
            pattern = _controller_allow_pattern(remote)
        elif len(parts) == 3 and parts[0] == "usd" and remote.suffix.lower() in _USD_EXTENSIONS:
            bundle = Path(parts[0], parts[1])
            pattern = bundle.as_posix().rstrip("/") + "/**"
        elif len(parts) >= 4 and parts[0:2] == ("usd", "payloads"):
            bundle = Path(*parts[:4])
            pattern = bundle.as_posix().rstrip("/") + "/**"
        elif len(parts) >= 3 and parts[0] == "usd":
            bundle = Path(parts[0], parts[1], parts[2])
            pattern = bundle.as_posix().rstrip("/") + "/**"
        elif len(parts) >= 2:
            bundle = Path(parts[0], parts[1])
            pattern = bundle.as_posix().rstrip("/") + "/**"
        else:
            bundle = remote.parent
            pattern = bundle.as_posix().rstrip("/") + "/**"
        if pattern not in seen:
            seen.add(pattern)
            patterns.append(pattern)
    return patterns


def _controller_allow_pattern(remote: Path) -> str:
    parts = remote.parts
    if remote.name == "__init__.py" and len(parts) <= 3:
        return remote.as_posix()
    if len(parts) <= 2:
        return "controller/**"
    if len(parts) >= 4:
        return Path(parts[0], parts[1], parts[2]).as_posix() + "/**"
    return Path(parts[0], parts[1]).as_posix() + "/**"


def _hf_revision() -> str:
    return os.environ.get("EAI_ASSETS_HF_REVISION", DEFAULT_HF_REVISION).strip() or DEFAULT_HF_REVISION


def _hf_error_status_code(exc: Exception) -> int | None:
    for source in (exc, getattr(exc, "response", None)):
        status_code = getattr(source, "status_code", None)
        if type(status_code) is int:
            return status_code
    for line in _hf_diagnostic_text(exc).splitlines():
        status_match = _HF_STRONG_STATUS_RE.search(line)
        if status_match is None:
            continue
        revision_match = _HF_REVISION_ERROR_RE.search(line)
        if revision_match is not None and revision_match.start() < status_match.start():
            continue
        status_text = next(value for value in status_match.groupdict().values() if value is not None)
        return int(status_text)
    return None


def _hf_diagnostic_text(exc: Exception) -> str:
    if isinstance(exc, subprocess.CalledProcessError):
        return _subprocess_diagnostic_text(exc)
    return _format_exception_for_message(exc)


def _has_hf_context(exc: Exception, pattern: re.Pattern[str]) -> bool:
    for line in _hf_diagnostic_text(exc).splitlines():
        context_match = pattern.search(line)
        if context_match is None:
            continue
        revision_match = _HF_REVISION_ERROR_RE.search(line)
        if revision_match is not None and revision_match.start() < context_match.start():
            continue
        return True
    return False


def _has_hf_credential_context(exc: Exception) -> bool:
    return _has_hf_context(exc, _HF_CREDENTIAL_CONTEXT_RE)


def _is_hf_access_error(exc: Exception) -> bool:
    status_code = _hf_error_status_code(exc)
    if status_code is not None:
        return status_code in {401, 403}
    return _has_hf_context(exc, _HF_ACCESS_CONTEXT_RE) or _has_hf_credential_context(exc)


def _exception_chain(exc: BaseException) -> Iterable[BaseException]:
    pending = [exc]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        for linked in (current.__cause__, current.__context__):
            if linked is not None:
                pending.append(linked)


def _is_hf_network_error(exc: Exception) -> bool:
    network_errnos = {
        errno.ECONNABORTED,
        errno.ECONNREFUSED,
        errno.ECONNRESET,
        errno.EHOSTUNREACH,
        errno.ENETDOWN,
        errno.ENETUNREACH,
        errno.ETIMEDOUT,
    }
    network_class_tokens = ("connect", "network", "proxy", "timeout")
    diagnostics: list[str] = []
    for current in _exception_chain(exc):
        if isinstance(current, (ConnectionError, TimeoutError, socket.gaierror)):
            return True
        if isinstance(current, OSError) and current.errno in network_errnos:
            return True
        module_root = type(current).__module__.split(".", 1)[0]
        class_name = type(current).__name__.lower()
        if module_root in {"httpcore", "httpx", "requests", "urllib3"} and any(
            token in class_name for token in network_class_tokens
        ):
            return True
        diagnostics.append(str(current))
    return _HF_NETWORK_CONTEXT_RE.search("\n".join(diagnostics)) is not None


def _normalize_hf_download_error(
    repo_id: str,
    allow_patterns: list[str],
    exc: Exception,
) -> AssetDownloadError:
    if isinstance(exc, AssetDownloadError):
        return exc
    if _is_hf_access_error(exc):
        return AssetDownloadAccessError(_hf_access_error_message(repo_id, allow_patterns, exc))
    if _is_hf_network_error(exc):
        return AssetDownloadNetworkError(_hf_network_error_message(repo_id, allow_patterns, exc))
    return AssetDownloadError(_hf_download_error_message(repo_id, allow_patterns, exc))


def _download_from_hf(
    *,
    repo_id: str,
    repo_type: str,
    local_dir: str,
    allow_patterns: list[str],
    interactive_auth: bool = True,
) -> None:
    revision = _hf_revision()
    if not _hf_token_available():
        if not interactive_auth:
            raise AssetDownloadAccessError(
                _hf_access_error_message(repo_id, allow_patterns, RuntimeError("No Hugging Face token found."))
            )
        _run_hf_auth_login(repo_id, allow_patterns)
        if not _hf_token_available():
            raise AssetDownloadAccessError(
                _hf_access_error_message(repo_id, allow_patterns, RuntimeError("No Hugging Face token found."))
            )

    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        _download_with_hf_cli(
            repo_id=repo_id,
            repo_type=repo_type,
            local_dir=local_dir,
            allow_patterns=allow_patterns,
            revision=revision,
        )
        return

    try:
        snapshot_download(
            repo_id=repo_id,
            repo_type=repo_type,
            local_dir=local_dir,
            allow_patterns=allow_patterns,
            revision=revision,
        )
    except Exception as exc:
        raise _normalize_hf_download_error(repo_id, allow_patterns, exc) from exc


def _download_with_hf_cli(
    *,
    repo_id: str,
    repo_type: str,
    local_dir: str,
    allow_patterns: list[str],
    revision: str | None = None,
) -> None:
    pinned_revision = revision or _hf_revision()
    cmd = [
        "hf",
        "download",
        repo_id,
        "--type",
        repo_type,
        "--revision",
        pinned_revision,
        "--local-dir",
        local_dir,
    ]
    for pattern in allow_patterns:
        cmd.extend(["--include", pattern])
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except (subprocess.CalledProcessError, OSError) as exc:
        raise _normalize_hf_download_error(repo_id, allow_patterns, exc) from exc


def _hf_access_error_message(repo_id: str, allow_patterns: list[str], exc: Exception) -> str:
    repo_url = _hf_repo_url(repo_id)
    patterns = "\n".join(f"  - {pattern}" for pattern in allow_patterns)
    return (
        "[EAI Assets] Failed to download required assets from Hugging Face.\n\n"
        "This project uses a gated Hugging Face asset repository. "
        "Open the repository page, fill out and submit the access request form. "
        "Wait until your Hugging Face account is approved, then retry the download.\n\n"
        f"Repository: {repo_url}\n"
        f"Access request form: {repo_url}\n"
        "If you are not logged in yet, log in from this terminal:\n"
        "  hf auth login\n\n"
        "Required asset bundles:\n"
        f"{patterns}\n\n"
        f"Original error: {_format_exception_for_message(exc)}"
    )


def _hf_download_error_message(repo_id: str, allow_patterns: list[str], exc: Exception) -> str:
    patterns = "\n".join(f"  - {pattern}" for pattern in allow_patterns)
    return (
        "[EAI Assets] Failed to download required assets from Hugging Face.\n\n"
        f"Repository: {_hf_repo_url(repo_id)}\n"
        f"Revision: {_hf_revision()}\n"
        "Required asset bundles:\n"
        f"{patterns}\n\n"
        f"Original error: {_format_exception_for_message(exc)}"
    )


def _hf_network_error_message(repo_id: str, allow_patterns: list[str], exc: Exception) -> str:
    patterns = "\n".join(f"  - {pattern}" for pattern in allow_patterns)
    proxy_variables = (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    )
    proxy_state = (
        "configured" if any(os.environ.get(name) for name in proxy_variables) else "not configured"
    )
    return (
        "[EAI Assets] Unable to reach Hugging Face while downloading required assets.\n\n"
        "Check the network connection, DNS, firewall, and proxy settings, then run the same command again.\n"
        f"Proxy environment variables: {proxy_state}\n\n"
        f"Repository: {_hf_repo_url(repo_id)}\n"
        f"Revision: {_hf_revision()}\n"
        "Required asset bundles:\n"
        f"{patterns}\n\n"
        f"Original error: {_format_exception_for_message(exc)}"
    )


def _hf_repo_url(repo_id: str) -> str:
    return f"{HF_BASE_URL}/datasets/{repo_id}"


def hf_repo_url() -> str:
    return _hf_repo_url(os.environ.get("EAI_ASSETS_HF_REPO", DEFAULT_HF_REPO))


def _hf_token_available() -> bool:
    if os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN"):
        return True
    hf_home = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")).expanduser()
    return (hf_home / "token").exists() or (hf_home / "stored_tokens").exists()


def _run_hf_auth_login(repo_id: str, allow_patterns: list[str]) -> None:
    print(
        "\n[EAI Assets] Hugging Face login is required before downloading gated assets.\n"
        f"Repository: {_hf_repo_url(repo_id)}\n"
        "If you do not have access yet, open the repository page above, fill out and submit the request form, "
        "and wait until your Hugging Face account is approved.\n"
        "Starting `hf auth login` now. After login completes, EAI will continue the asset download.\n"
        "Required asset bundles:\n"
        + "\n".join(f"  - {pattern}" for pattern in allow_patterns)
        + "\n"
    )
    try:
        subprocess.run(["hf", "auth", "login"], check=True)
    except FileNotFoundError as exc:
        raise AssetDownloadError(_hf_download_error_message(repo_id, allow_patterns, exc)) from exc
    except subprocess.CalledProcessError as exc:
        raise _normalize_hf_download_error(repo_id, allow_patterns, exc) from exc


def _prompt_retry_after_access_request(repo_id: str, allow_patterns: list[str], exc: Exception) -> None:
    repo_url = _hf_repo_url(repo_id)
    message = str(exc) if isinstance(exc, AssetDownloadAccessError) else _hf_access_error_message(repo_id, allow_patterns, exc)
    print(message)
    while True:
        choice = input(
            f"\n[EAI Assets] Access request form: {repo_url}\n"
            "[EAI Assets] Fill out and submit the access request in your browser. "
            "Wait until your Hugging Face account is approved, then type `r` to retry downloading. "
            "Type `q` to stop: "
        ).strip().lower()
        if choice == "r":
            print("[EAI Assets] Retrying Hugging Face asset download...")
            return
        if choice == "q":
            raise AssetDownloadAccessError(message) from exc
        print("Please type `r` to retry or `q` to stop.")


def _format_exception_for_message(exc: Exception) -> str:
    diagnostics = _subprocess_diagnostic_text(exc)
    if diagnostics:
        return diagnostics
    return str(exc)


def _subprocess_diagnostic_text(exc: Exception) -> str:
    diagnostics: list[str] = []
    for attribute in ("stdout", "output", "stderr"):
        value = getattr(exc, attribute, None)
        if not value:
            continue
        if isinstance(value, bytes):
            diagnostic = value.decode(errors="replace").strip()
        else:
            diagnostic = str(value).strip()
        if diagnostic and diagnostic not in diagnostics:
            diagnostics.append(diagnostic)
    return "\n".join(diagnostics)


class _download_target_dir:
    def __init__(self, *, local_root: Path, remote_root: str) -> None:
        self._temp_dir: tempfile.TemporaryDirectory[str] | None = None
        root = local_root
        self._direct_path = root.parent if root.name == remote_root else None

    def __enter__(self) -> Path:
        if self._direct_path is not None:
            return self._direct_path
        self._temp_dir = tempfile.TemporaryDirectory(prefix="eai-assets-")
        return Path(self._temp_dir.name)

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._temp_dir is not None:
            self._temp_dir.cleanup()


def _sync_external_usd_root(download_dir: Path) -> None:
    _sync_external_asset_root(download_dir, remote_root="usd", local_root=usd_root())


def _sync_external_asset_root(download_dir: Path, *, remote_root: str, local_root: Path) -> None:
    if local_root.name == remote_root:
        return
    source = download_dir / remote_root
    if source.exists():
        local_root.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, local_root, dirs_exist_ok=True)


def _auto_download_enabled() -> bool:
    value = os.environ.get("EAI_ASSETS_AUTO_DOWNLOAD", "1").strip().lower()
    return value not in {"0", "false", "no", "off"}


def _is_local_usd_path(path: str) -> bool:
    if "://" in path:
        return False
    try:
        remote = _remote_path_for_local(path)
    except ValueError:
        return False
    return bool(remote.parts) and remote.parts[0] == "usd"


def _is_existing_external_local_asset_path(path: str) -> bool:
    if "://" in path:
        return False
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        return False
    try:
        _remote_path_for_local(path)
    except ValueError:
        return candidate.exists()
    return False


def _is_local_controller_path(path: str) -> bool:
    if "://" in path:
        return False
    try:
        remote = _remote_path_for_local(path)
    except ValueError:
        return False
    return bool(remote.parts) and remote.parts[0] == "controller"


def _normalize_local_usd_path(path: str) -> str:
    remote = _remote_path_for_local(path)
    rel = Path(*remote.parts[1:])
    return str(usd_root() / rel)


def _normalize_local_controller_path(path: str) -> str:
    remote = _remote_path_for_local(path)
    rel = Path(*remote.parts[1:])
    return str(controller_root() / rel)


def _remote_path_for_local(path: str) -> Path:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        parts = candidate.parts
        if parts and parts[0] in {"usd", "controller"}:
            return candidate
        return Path("usd") / candidate

    for remote_root, local_root in (("usd", usd_root()), ("controller", controller_root())):
        try:
            rel = candidate.resolve().relative_to(local_root)
        except ValueError:
            continue
        return Path(remote_root) / rel
    raise ValueError(path)


def _as_iterable(value: Any) -> Iterable[Any]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes, os.PathLike)):
        return (value,)
    if isinstance(value, dict):
        return value.values()
    try:
        return tuple(value)
    except TypeError:
        return (value,)
