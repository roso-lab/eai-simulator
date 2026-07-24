"""USD asset validation used by the Env DIY authoring workflow.

The validator deliberately imports USD lazily.  This keeps the model and unit
tests usable outside an Isaac Sim process while making the actual checks use
the same ``Usd.Stage.Open`` composition machinery as the preview.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping


@dataclass(frozen=True, init=False)
class AssetIssue:
    """One actionable problem found while checking an asset."""

    code: str
    message: str
    path: str | None = None
    prim_path: str | None = None
    severity: str = "error"
    context: Mapping[str, str] = field(default_factory=dict)
    asset_path: str | None = None
    target_path: str | None = None

    def __init__(
        self,
        code: str,
        message: str,
        prim_path: str | None = None,
        asset_path: str | None = None,
        target_path: str | None = None,
        *,
        path: str | None = None,
        severity: str = "error",
        context: Mapping[str, str] | None = None,
    ) -> None:
        resolved_asset_path = path if path is not None else asset_path
        object.__setattr__(self, "code", code)
        object.__setattr__(self, "message", message)
        object.__setattr__(self, "prim_path", prim_path)
        object.__setattr__(self, "asset_path", resolved_asset_path)
        object.__setattr__(self, "target_path", target_path if target_path is not None else prim_path)
        object.__setattr__(self, "path", resolved_asset_path)
        object.__setattr__(self, "severity", severity)
        object.__setattr__(self, "context", context or {})

    def __str__(self) -> str:
        location = self.path or "USD asset"
        if self.prim_path:
            location = f"{location}{self.prim_path}"
        return f"[{self.severity}] {self.code}: {location}: {self.message}"


@dataclass(init=False)
class AssetValidationReport:
    """Result of validating one USD file and its composed layer stack."""

    asset_path: str
    issues: list[AssetIssue] = field(default_factory=list)
    default_prim: str | None = None
    expected_roots: tuple[str, ...] = ()
    _forced_ok: bool | None = field(default=None, repr=False)

    def __init__(
        self,
        asset_path: str | None = None,
        *args: Any,
        issues: list[AssetIssue] | None = None,
        default_prim: str | None = None,
        expected_roots: tuple[str, ...] = (),
        path: str | None = None,
        ok: bool | None = None,
    ) -> None:
        if args:
            # Support both the implementation order
            # ``(path, issues, default_prim, expected_roots)`` and the
            # documented result order ``(path, ok, issues, default_prim,
            # expected_roots)``.
            if isinstance(args[0], bool):
                ok = args[0]
                if len(args) > 1:
                    issues = args[1]
                if len(args) > 2:
                    default_prim = args[2]
                if len(args) > 3:
                    expected_roots = args[3]
            else:
                issues = args[0]
                if len(args) > 1:
                    default_prim = args[1]
                if len(args) > 2:
                    expected_roots = args[2]
        self.asset_path = str(path if path is not None else (asset_path or ""))
        self.issues = list(issues or [])
        self.default_prim = default_prim
        self.expected_roots = tuple(expected_roots)
        self._forced_ok = ok

    @property
    def ok(self) -> bool:
        return self.valid

    @property
    def path(self) -> str:
        return self.asset_path

    @property
    def valid(self) -> bool:
        if self._forced_ok is False:
            return False
        return not any(issue.severity.lower() == "error" for issue in self.issues)

    @property
    def errors(self) -> tuple[AssetIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity.lower() == "error")

    @property
    def warnings(self) -> tuple[AssetIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity.lower() != "error")

    def add_issue(self, issue: AssetIssue) -> None:
        if issue not in self.issues:
            self.issues.append(issue)

    def format_diagnostics(self) -> str:
        if not self.issues:
            return f"[ok] {self.asset_path}"
        return "\n".join(str(issue) for issue in self.issues)

    def as_dict(self) -> dict[str, Any]:
        return {
            "asset_path": self.asset_path,
            "path": self.asset_path,
            "valid": self.valid,
            "ok": self.ok,
            "default_prim": self.default_prim,
            "expected_roots": list(self.expected_roots),
            "issues": [
                {
                    "code": issue.code,
                    "message": issue.message,
                    "path": issue.path,
                    "prim_path": issue.prim_path,
                    "severity": issue.severity,
                    "context": dict(issue.context),
                    "asset_path": issue.asset_path,
                    "target_path": issue.target_path,
                }
                for issue in self.issues
            ],
        }


_VALIDATION_CACHE: dict[tuple[str, int, tuple[str, ...], tuple[str, ...], bool], AssetValidationReport] = {}


def clear_validation_cache() -> None:
    """Drop cached successful reports (useful after repairing an asset)."""

    _VALIDATION_CACHE.clear()


def _open_stage(path: Path):
    """Open a stage without changing USD's diagnostic output behavior."""

    from pxr import Usd

    return Usd.Stage.Open(str(path))


def _as_paths(value: str | Iterable[str] | None) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(str(item) for item in value)


def _prim_valid(prim: Any) -> bool:
    try:
        return bool(prim and prim.IsValid())
    except Exception:
        return bool(prim)


def _prim_path(prim: Any) -> str | None:
    try:
        return str(prim.GetPath())
    except Exception:
        return None


def _layer_path(layer: Any) -> Path | None:
    for attr in ("realPath", "identifier"):
        try:
            value = getattr(layer, attr)
            if callable(value):
                value = value()
            if value:
                return Path(str(value)).expanduser()
        except Exception:
            continue
    return None


def _iter_layers(stage: Any) -> list[Any]:
    layers: list[Any] = []
    for method_name in ("GetLayerStack", "GetUsedLayers"):
        method = getattr(stage, method_name, None)
        if not callable(method):
            continue
        try:
            values = method(includeSessionLayers=False) if method_name == "GetLayerStack" else method()
        except TypeError:
            values = method()
        except Exception:
            continue
        for layer in values or ():
            if layer not in layers:
                layers.append(layer)
    root = getattr(stage, "GetRootLayer", lambda: None)()
    if root is not None and root not in layers:
        layers.insert(0, root)
    return layers


def _check_sublayers(stage: Any, report: AssetValidationReport) -> None:
    seen: set[str] = set()
    for layer in _iter_layers(stage):
        parent = _layer_path(layer)
        try:
            sublayers = list(getattr(layer, "subLayerPaths", ()) or ())
        except Exception:
            sublayers = []
        for raw_path in sublayers:
            raw = str(raw_path)
            resolved = Path(raw)
            if not resolved.is_absolute() and parent is not None:
                resolved = parent.parent / resolved
            resolved = resolved.expanduser()
            key = str(resolved)
            if key in seen:
                continue
            seen.add(key)
            if not resolved.is_file():
                report.add_issue(
                    AssetIssue(
                        "missing_sublayer",
                        f"sublayer does not exist: {raw}",
                        path=report.asset_path,
                        context={"sublayer": key},
                    )
                )


def _check_external_dependencies(stage: Any, report: AssetValidationReport) -> None:
    """Report missing external references exposed by Sdf layers.

    ``GetExternalReferences`` is available on current USD builds; older builds
    only expose ``GetExternalAssetDependencies``.  Both are optional so the
    validator remains compatible with Isaac Sim releases with either API.
    """

    for layer in _iter_layers(stage):
        parent = _layer_path(layer)
        for method_name in ("GetExternalReferences", "GetExternalAssetDependencies"):
            method = getattr(layer, method_name, None)
            if not callable(method):
                continue
            try:
                dependencies = method() or ()
            except Exception:
                continue
            for raw_path in dependencies:
                raw = str(raw_path)
                if not raw:
                    # Sdf reports an empty asset path for internal references;
                    # those are checked through Stage composition errors.
                    continue
                resolved = Path(raw)
                if not resolved.is_absolute() and parent is not None:
                    resolved = parent.parent / resolved
                if not resolved.expanduser().is_file():
                    report.add_issue(
                        AssetIssue(
                            "unresolved_reference",
                            f"external USD reference does not exist: {raw}",
                            path=report.asset_path,
                            context={"reference": str(resolved.expanduser())},
                        )
                    )


def _check_composition_errors(stage: Any, report: AssetValidationReport) -> None:
    method = getattr(stage, "GetCompositionErrors", None)
    if not callable(method):
        return
    try:
        errors = method() or ()
    except Exception as exc:
        report.add_issue(
            AssetIssue("composition_check_failed", f"could not query composition errors: {exc}", path=report.asset_path)
        )
        return
    for error in errors:
        detail = str(error)
        report.add_issue(AssetIssue("composition_error", detail, path=report.asset_path))
        lowered = detail.lower()
        if any(token in lowered for token in ("unresolved", "reference", "payload", "cannot open", "failed to open")):
            report.add_issue(AssetIssue("unresolved_reference", detail, path=report.asset_path))


def _check_articulation_root(stage: Any, report: AssetValidationReport) -> None:
    try:
        from pxr import UsdPhysics

        api_type = UsdPhysics.ArticulationRootAPI
    except Exception:
        api_type = None
    for prim in getattr(stage, "Traverse", lambda: ())() or ():
        has_api = getattr(prim, "HasAPI", None)
        if callable(has_api) and api_type is not None:
            try:
                if has_api(api_type):
                    return
            except Exception:
                pass
        if callable(getattr(prim, "GetAttribute", None)):
            try:
                attr = prim.GetAttribute("physxArticulation:articulationEnabled")
                if attr and attr.IsValid():
                    return
            except Exception:
                pass
    report.add_issue(
        AssetIssue(
            "missing_articulation_root",
            "no UsdPhysics articulation root was found",
            path=report.asset_path,
        )
    )


def validate_usd_asset(
    path: str | Path,
    *,
    expected_roots: str | Iterable[str] | None = None,
    expected_root: str | None = None,
    expected_body_paths: str | Iterable[str] | None = None,
    expected_children: str | Iterable[str] | None = None,
    require_articulation_root: bool = False,
    use_cache: bool = True,
    cache: dict | None = None,
) -> AssetValidationReport:
    """Validate a USD file and its composed dependencies.

    ``expected_roots`` are checked against ``stage.GetPrimAtPath``.  The
    optional ``expected_body_paths`` is useful for mount hosts such as
    ``/Host/base_link``.  USD warnings remain visible on the process diagnostic
    stream; this function only records structured diagnostics in addition.
    """

    asset_path = str(Path(path).expanduser().resolve())
    roots = _as_paths(expected_roots)
    if expected_root:
        roots = tuple(dict.fromkeys((*roots, expected_root)))
    body_paths = _as_paths(expected_body_paths)
    child_paths = _as_paths(expected_children)
    file_path = Path(asset_path)
    report = AssetValidationReport(asset_path, expected_roots=roots)
    if not file_path.is_file():
        report.add_issue(AssetIssue("missing_file", f"USD file does not exist: {asset_path}", path=asset_path))
        return report
    try:
        stat = file_path.stat()
    except OSError as exc:
        report.add_issue(AssetIssue("unreadable_file", f"could not stat USD file: {exc}", path=asset_path))
        return report
    cache_store = cache if cache is not None else _VALIDATION_CACHE
    cache_key = (asset_path, stat.st_mtime_ns, roots, (*body_paths, *child_paths), require_articulation_root)
    if use_cache and cache_key in cache_store:
        cached = cache_store[cache_key]
        return AssetValidationReport(
            cached.asset_path,
            list(cached.issues),
            cached.default_prim,
            cached.expected_roots,
        )
    try:
        stage = _open_stage(file_path)
    except Exception as exc:
        report.add_issue(AssetIssue("stage_open_failed", f"Usd.Stage.Open failed: {exc}", path=asset_path))
        return report
    if stage is None:
        report.add_issue(AssetIssue("stage_open_failed", "Usd.Stage.Open returned no stage", path=asset_path))
        return report

    default_prim = getattr(stage, "GetDefaultPrim", lambda: None)()
    if _prim_valid(default_prim):
        report.default_prim = _prim_path(default_prim)
    else:
        report.add_issue(AssetIssue("default_prim_missing", "stage has no valid defaultPrim", path=asset_path))
    if roots and report.default_prim and report.default_prim not in roots:
        report.add_issue(
            AssetIssue(
                "default_prim_unexpected",
                f"defaultPrim is {report.default_prim!r}; expected one of {roots!r}",
                path=asset_path,
                prim_path=report.default_prim,
            )
        )
    for root in roots + body_paths + child_paths:
        prim = getattr(stage, "GetPrimAtPath", lambda _path: None)(root)
        if not _prim_valid(prim):
            report.add_issue(
                AssetIssue(
                    "expected_root_missing" if root in roots else "expected_child_missing" if root in child_paths else "expected_body_missing",
                    f"expected prim does not exist: {root}",
                    path=asset_path,
                    prim_path=root,
                )
            )
    _check_composition_errors(stage, report)
    _check_sublayers(stage, report)
    _check_external_dependencies(stage, report)
    if require_articulation_root:
        _check_articulation_root(stage, report)
    if use_cache and report.valid:
        cache_store[cache_key] = report
    return report


# Short aliases used by callers that treat this as a generic asset checker.
validate_asset = validate_usd_asset
check_usd_asset = validate_usd_asset


__all__ = [
    "AssetIssue",
    "AssetValidationReport",
    "check_usd_asset",
    "clear_validation_cache",
    "validate_asset",
    "validate_usd_asset",
]
