from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable, Iterable

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
HF_BASE_URL = "https://huggingface.co"

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
}


class AssetDownloadAccessError(RuntimeError):
    """Raised when the gated Hugging Face asset repo cannot be accessed."""


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
    lowered = str(exc).lower()
    if isinstance(exc, AssetDownloadAccessError):
        state = RequirementState.AUTH_REQUIRED if "token" in lowered or "login" in lowered else RequirementState.ACCESS_PENDING
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
        with _download_target_dir(local_root=local_root, remote_root=remote_root) as local_dir:
            download(
                repo_id=repo_id,
                repo_type=DEFAULT_REPO_TYPE,
                local_dir=str(local_dir),
                allow_patterns=patterns,
                interactive_auth=False,
            )
            _sync_external_asset_root(local_dir, remote_root=remote_root, local_root=local_root)
    except TypeError as exc:
        # Keep test/custom downloaders compatible with the historical kwargs.
        if "interactive_auth" not in str(exc):
            return _status_for_download_error(requirement, exc)
        try:
            with _download_target_dir(local_root=local_root, remote_root=remote_root) as local_dir:
                download(
                    repo_id=repo_id,
                    repo_type=DEFAULT_REPO_TYPE,
                    local_dir=str(local_dir),
                    allow_patterns=patterns,
                )
                _sync_external_asset_root(local_dir, remote_root=remote_root, local_root=local_root)
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


def _ensure_asset_paths(
    paths: Iterable[str],
    *,
    asset_label: str,
    remote_root: str,
    local_root: Path,
    downloader: Callable[..., Any] | None = None,
) -> list[str]:
    missing = [path for path in paths if not Path(path).exists()]
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
    with _download_target_dir(local_root=local_root, remote_root=remote_root) as local_dir:
        while True:
            try:
                download(
                    repo_id=repo_id,
                    repo_type=DEFAULT_REPO_TYPE,
                    local_dir=str(local_dir),
                    allow_patterns=patterns,
                )
                break
            except AssetDownloadAccessError as exc:
                _prompt_retry_after_access_request(repo_id, patterns, exc)
            except Exception as exc:
                _prompt_retry_after_access_request(repo_id, patterns, exc)
        _sync_external_asset_root(local_dir, remote_root=remote_root, local_root=local_root)

    still_missing = [path for path in missing if not Path(path).exists()]
    if still_missing:
        missing_text = "\n".join(f"  - {path}" for path in still_missing)
        raise FileNotFoundError(
            _hf_access_error_message(
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
        if parts and parts[0] == "controller":
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


def _download_from_hf(
    *,
    repo_id: str,
    repo_type: str,
    local_dir: str,
    allow_patterns: list[str],
    interactive_auth: bool = True,
) -> None:
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
        )
        return

    snapshot_download(
        repo_id=repo_id,
        repo_type=repo_type,
        local_dir=local_dir,
        allow_patterns=allow_patterns,
    )


def _download_with_hf_cli(
    *,
    repo_id: str,
    repo_type: str,
    local_dir: str,
    allow_patterns: list[str],
) -> None:
    cmd = [
        "hf",
        "download",
        repo_id,
        "--type",
        repo_type,
        "--local-dir",
        local_dir,
    ]
    for pattern in allow_patterns:
        cmd.extend(["--include", pattern])
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as exc:
        raise AssetDownloadAccessError(_hf_access_error_message(repo_id, allow_patterns, exc)) from exc


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
        raise AssetDownloadAccessError(_hf_access_error_message(repo_id, allow_patterns, exc)) from exc
    except subprocess.CalledProcessError as exc:
        raise AssetDownloadAccessError(_hf_access_error_message(repo_id, allow_patterns, exc)) from exc


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
    stderr = getattr(exc, "stderr", None)
    if stderr:
        if isinstance(stderr, bytes):
            return stderr.decode(errors="replace").strip()
        return str(stderr).strip()
    return str(exc)


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
