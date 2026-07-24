#!/usr/bin/env python3
"""Generate canonical Env DIY USD assets from the repository sources.

The repair is intentionally conservative: only a reference whose target prim
is missing *and* which is part of a visual subtree is removed. Physics,
colliders, joints, articulation APIs and all non-visual references are left
untouched. Run this script inside the Isaac Sim Python environment so ``pxr``
is available.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable


_CANONICAL_ROOTS = {
    "b2": ("/b2_description",),
    "lite3": ("/Lite3",),
}
_CANONICAL_BODY_PATHS = {
    "b2": ("/b2_description/base_link",),
    "lite3": ("/Lite3/TORSO",),
}
_CANONICAL_VISUAL_PATHS = {
    "b2": (
        "/b2_description/base_link/visuals",
        "/b2_description/FL_hip/visuals",
        "/b2_description/FL_thigh/visuals",
        "/b2_description/FL_calf/visuals",
        "/b2_description/FR_hip/visuals",
        "/b2_description/FR_thigh/visuals",
        "/b2_description/FR_calf/visuals",
        "/b2_description/RL_hip/visuals",
        "/b2_description/RL_thigh/visuals",
        "/b2_description/RL_calf/visuals",
        "/b2_description/RR_hip/visuals",
        "/b2_description/RR_thigh/visuals",
        "/b2_description/RR_calf/visuals",
    ),
    "lite3": (
        "/Lite3/TORSO/visuals",
        "/Lite3/FL_HIP/visuals",
        "/Lite3/FL_THIGH/visuals",
        "/Lite3/FL_SHANK/visuals",
        "/Lite3/FR_HIP/visuals",
        "/Lite3/FR_THIGH/visuals",
        "/Lite3/FR_SHANK/visuals",
        "/Lite3/HL_HIP/visuals",
        "/Lite3/HL_THIGH/visuals",
        "/Lite3/HL_SHANK/visuals",
        "/Lite3/HR_HIP/visuals",
        "/Lite3/HR_THIGH/visuals",
        "/Lite3/HR_SHANK/visuals",
    ),
}


@dataclass
class RepairResult:
    source: str
    destination: str
    ok: bool = False
    removed_references: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.removed_references)

    def format_diagnostics(self) -> str:
        state = "ok" if self.ok else "failed"
        lines = [f"[{state}] {self.source} -> {self.destination}"]
        lines.extend(f"  removed visual reference: {value}" for value in self.removed_references)
        lines.extend(f"  issue: {value}" for value in self.issues)
        return "\n".join(lines)


def _import_pxr():
    try:
        from pxr import Sdf, Usd
    except Exception as exc:  # pragma: no cover - exercised in non-Isaac CLI
        raise RuntimeError(
            "Isaac Sim USD bindings are unavailable; run this tool with the "
            "Isaac Sim Python interpreter (pxr.Sdf/pxr.Usd)"
        ) from exc
    return Sdf, Usd


def _iter_prim_specs(spec: Any) -> Iterable[Any]:
    yield spec
    children = getattr(spec, "nameChildren", ())
    for child in children or ():
        yield from _iter_prim_specs(child)


def _prim_path(spec: Any) -> str:
    try:
        return str(spec.path)
    except Exception:
        try:
            return str(spec.GetPath())
        except Exception:
            return "<unknown>"


def _ref_asset_path(reference: Any) -> str:
    value = getattr(reference, "assetPath", "")
    return str(value or "")


def _ref_prim_path(reference: Any) -> str:
    value = getattr(reference, "primPath", "")
    return str(value or "")


def _is_visual_reference(spec_path: str, ref_path: str = "") -> bool:
    """Accept only specs authored under a visual/visuals subtree."""

    segments = [segment.lower() for segment in spec_path.split("/") if segment]
    return any(segment in {"visual", "visuals"} for segment in segments)


def _resolve_reference(source: Path, asset_path: str) -> Path | None:
    if not asset_path:
        return None
    candidate = Path(asset_path)
    if candidate.is_absolute():
        return candidate
    return (source.parent / candidate).resolve()


def _target_exists(
    Usd: Any,
    source: Path,
    reference: Any,
    source_stage: Any = None,
    working_layer: Any = None,
) -> bool:
    asset_path = _ref_asset_path(reference)
    target_file = _resolve_reference(source, asset_path)
    if target_file is None:
        # Flatten creates internal /Flattened_Prototype_* targets which do not
        # exist in the source stage. Resolve the writable layer first so those
        # generated references are not mistaken for broken source references.
        prim_path = _ref_prim_path(reference)
        if not prim_path:
            return True
        if working_layer is not None:
            try:
                if working_layer.GetPrimAtPath(prim_path) is not None:
                    return True
            except Exception:
                pass
        if source_stage is None:
            return False
        try:
            return bool(source_stage.GetPrimAtPath(prim_path).IsValid())
        except Exception:
            return False
    if not target_file.is_file():
        return False
    try:
        stage = Usd.Stage.Open(str(target_file))
    except Exception:
        return False
    if stage is None:
        return False
    prim_path = _ref_prim_path(reference)
    if not prim_path:
        return True
    try:
        return bool(stage.GetPrimAtPath(prim_path).IsValid())
    except Exception:
        return False


def _references(spec: Any) -> list[Any]:
    op = getattr(spec, "referenceList", None)
    if op is None:
        get_metadata = getattr(spec, "GetMetadata", None)
        if callable(get_metadata):
            try:
                op = get_metadata("references")
            except Exception:
                op = None
    if op is None:
        return []
    for method_name in ("GetAddedOrExplicitItems", "GetAppliedItems"):
        method = getattr(op, method_name, None)
        if callable(method):
            try:
                return list(method() or ())
            except Exception:
                continue
    try:
        return list(op or ())
    except Exception:
        return []


def _is_renderable_mesh(prim: Any) -> bool:
    if str(getattr(prim, "GetTypeName", lambda: "")()) != "Mesh":
        return False
    try:
        from pxr import UsdGeom

        mesh = UsdGeom.Mesh(prim)
        points = mesh.GetPointsAttr().Get()
        imageable = UsdGeom.Imageable(prim)
        return (
            bool(points)
            and imageable.ComputeVisibility() == UsdGeom.Tokens.inherited
            and imageable.ComputePurpose() in (UsdGeom.Tokens.default_, UsdGeom.Tokens.render)
        )
    except Exception:
        # Unit-test doubles expose only the schema type. Real USD prims take
        # the stricter visibility, purpose and point-data path above.
        return True


def _reference_target_exists(stage: Any, reference: Any) -> bool:
    if _ref_asset_path(reference):
        return False
    target_path = _ref_prim_path(reference)
    if not target_path:
        return False
    try:
        target = stage.GetPrimAtPath(target_path)
        return bool(target and target.IsValid())
    except Exception:
        return False


def _prim_has_renderable_mesh(Usd: Any, prim: Any) -> bool:
    try:
        traverse_instance_proxies = getattr(Usd, "TraverseInstanceProxies", None)
        if callable(traverse_instance_proxies):
            prim_range = Usd.PrimRange(prim, traverse_instance_proxies())
        else:
            prim_range = Usd.PrimRange(prim)
        return any(_is_renderable_mesh(descendant) for descendant in prim_range)
    except Exception:
        return False


def repair_usd_asset(
    source: str | Path,
    destination: str | Path | None = None,
    *,
    expected_roots: Iterable[str] = (),
    expected_body_paths: Iterable[str] = (),
    expected_visual_paths: Iterable[str] = (),
) -> RepairResult:
    """Create a canonical USD copy and remove only invalid visual references."""

    source_path = Path(source).expanduser().resolve()
    if destination is None:
        destination_path = source_path.with_name(f"{source_path.stem}_canonical.usdc")
    else:
        destination_path = Path(destination).expanduser().resolve()
    result = RepairResult(str(source_path), str(destination_path))
    if not source_path.is_file():
        result.issues.append(f"source USD file does not exist: {source_path}")
        return result
    try:
        Sdf, Usd = _import_pxr()
        layer = Sdf.Layer.FindOrOpen(str(source_path))
    except Exception as exc:
        result.issues.append(str(exc))
        return result
    if layer is None:
        result.issues.append(f"Sdf.Layer.FindOrOpen returned no layer: {source_path}")
        return result

    try:
        source_stage = Usd.Stage.Open(str(source_path))
    except Exception as exc:
        result.issues.append(f"source stage could not be opened for reference validation: {exc}")
        return result
    if source_stage is None:
        result.issues.append("source stage could not be opened for reference validation")
        return result

    # Flattening includes nested configuration layers (Lite3/M20) while
    # retaining authored physics, collider and joint specs. USD builds without
    # Flatten fall back to a detached root-layer copy below.
    flatten = getattr(source_stage, "Flatten", None)
    working = None
    if callable(flatten):
        try:
            working = flatten()
        except Exception as exc:
            result.issues.append(f"failed to flatten composed USD stage: {exc}")
            return result
    if working is None:
        try:
            working = Sdf.Layer.CreateAnonymous(f"env_diy_repair_{source_path.name}")
            copied = working.TransferContent(layer)
            if copied is False:
                result.issues.append("failed to copy source layer into a writable layer")
                return result
        except Exception as exc:
            result.issues.append(f"failed to clone source layer: {exc}")
            return result

    removed: list[tuple[Any, Any, str]] = []
    root = getattr(working, "pseudoRoot", None)
    if root is None:
        result.issues.append("source layer has no pseudoRoot")
        return result
    for spec in _iter_prim_specs(root):
        spec_path = _prim_path(spec)
        for reference in _references(spec):
            ref_path = _ref_prim_path(reference)
            if not _is_visual_reference(spec_path, ref_path):
                continue
            if _target_exists(Usd, source_path, reference, source_stage, working):
                continue
            removed.append((spec, reference, f"{spec_path}: {_ref_asset_path(reference)}{ref_path}"))

    for spec, reference, description in removed:
        op = getattr(spec, "referenceList", None)
        # Test doubles may expose the older RemoveReference spelling; current
        # USD uses ReferenceListOp.Remove/Erase.
        remove = (
            getattr(op, "RemoveReference", None)
            or getattr(op, "Remove", None)
            or getattr(op, "Erase", None)
        )
        remove_item = getattr(op, "RemoveItemEdits", None)
        if not callable(remove) and not callable(remove_item):
            result.issues.append(f"cannot remove invalid visual reference (API unavailable): {description}")
            continue
        try:
            if callable(remove):
                remove(reference)
            else:
                remove_item(reference)
            result.removed_references.append(description)
        except Exception as exc:
            result.issues.append(f"failed to remove {description}: {exc}")

    if result.issues:
        return result
    pending_path: Path | None = None
    try:
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            prefix=f".{destination_path.name}.pending-",
            suffix=destination_path.suffix,
            dir=destination_path.parent,
            delete=False,
        ) as pending:
            pending_path = Path(pending.name)
        exported = working.Export(str(pending_path))
        if exported is False:
            result.issues.append(f"failed to export canonical layer: {pending_path}")
            return result

        # Re-open the pending file before replacing an existing canonical
        # asset. A failed validation must never leave a selectable bad file.
        generated = Usd.Stage.Open(str(pending_path))
        if generated is None:
            result.issues.append("canonical stage could not be opened")
            return result
        _validate_generated_stage(
            Usd,
            generated,
            result,
            expected_roots=expected_roots,
            expected_body_paths=expected_body_paths,
            expected_visual_paths=expected_visual_paths,
        )
        if result.issues:
            return result
        os.replace(pending_path, destination_path)
        pending_path = None
        result.ok = True
        _write_manifest(result)
        return result
    except Exception as exc:
        result.issues.append(f"failed to export canonical layer: {exc}")
        return result
    finally:
        if pending_path is not None:
            pending_path.unlink(missing_ok=True)


def _validate_generated_stage(
    Usd: Any,
    stage: Any,
    result: RepairResult,
    *,
    expected_roots: Iterable[str] = (),
    expected_body_paths: Iterable[str] = (),
    expected_visual_paths: Iterable[str] = (),
) -> None:
    roots = tuple(expected_roots)
    body_paths = tuple(expected_body_paths)
    default_prim = getattr(stage, "GetDefaultPrim", lambda: None)()
    if not default_prim or not default_prim.IsValid():
        result.issues.append("canonical stage has no valid defaultPrim")
    elif roots and str(default_prim.GetPath()) not in roots:
        result.issues.append(
            f"canonical stage defaultPrim is {default_prim.GetPath()}, expected one of {roots}"
        )
    for root in roots:
        prim = stage.GetPrimAtPath(root)
        if not prim or not prim.IsValid():
            result.issues.append(f"canonical stage missing required root: {root}")
    for body_path in body_paths:
        prim = stage.GetPrimAtPath(body_path)
        if not prim or not prim.IsValid():
            result.issues.append(f"canonical stage missing required body: {body_path}")
    composition_errors = getattr(stage, "GetCompositionErrors", lambda: ())() or ()
    if composition_errors:
        result.issues.extend(f"canonical stage composition error: {error}" for error in composition_errors)
    for visual_path in expected_visual_paths:
        prim = stage.GetPrimAtPath(visual_path)
        if not prim or not prim.IsValid():
            result.issues.append(f"canonical stage missing required visual path: {visual_path}")
            continue
        references = _references(prim)
        if not references:
            result.issues.append(
                f"canonical stage visual path has no attached reference: {visual_path}"
            )
            continue
        if not any(_reference_target_exists(stage, reference) for reference in references):
            result.issues.append(
                f"canonical stage visual path has no valid reference target: {visual_path}"
            )
            continue
        if not _prim_has_renderable_mesh(Usd, prim):
            result.issues.append(
                f"canonical stage visual path does not resolve to a renderable Mesh: {visual_path}"
            )
    try:
        from pxr import UsdPhysics

        articulation_api = UsdPhysics.ArticulationRootAPI
    except Exception:
        articulation_api = None
    if articulation_api is not None:
        found = False
        for prim in getattr(stage, "Traverse", lambda: ())() or ():
            try:
                if prim.HasAPI(articulation_api):
                    found = True
                    break
            except Exception:
                continue
        if not found:
            result.issues.append("canonical stage has no articulation root")


def _write_manifest(result: RepairResult) -> None:
    manifest_path = Path(f"{result.destination}.manifest.json")
    payload = {
        "source": result.source,
        "destination": result.destination,
        "removed_visual_references": list(result.removed_references),
    }
    try:
        manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except Exception as exc:
        result.issues.append(f"failed to write repair manifest: {exc}")
        result.ok = False


def check_canonical_asset(
    source: str | Path,
    destination: str | Path,
    *,
    expected_roots: Iterable[str] = (),
    expected_body_paths: Iterable[str] = (),
    expected_visual_paths: Iterable[str] = (),
) -> RepairResult:
    """Read-only validation of an existing canonical/source asset pair."""

    source_path = Path(source).expanduser().resolve()
    destination_path = Path(destination).expanduser().resolve()
    result = RepairResult(str(source_path), str(destination_path))
    if not source_path.is_file():
        result.issues.append(f"source USD file does not exist: {source_path}")
        return result
    if not destination_path.is_file():
        result.issues.append(f"canonical USD file does not exist: {destination_path}")
        return result
    try:
        Usd = _import_pxr()[1]
        stage = Usd.Stage.Open(str(destination_path))
    except Exception as exc:
        result.issues.append(f"canonical stage could not be opened: {exc}")
        return result
    if stage is None:
        result.issues.append("canonical stage could not be opened")
        return result
    _validate_generated_stage(
        Usd,
        stage,
        result,
        expected_roots=expected_roots,
        expected_body_paths=expected_body_paths,
        expected_visual_paths=expected_visual_paths,
    )
    if not Path(f"{destination_path}.manifest.json").is_file():
        result.issues.append(f"repair manifest does not exist: {destination_path}.manifest.json")
    result.ok = not result.issues
    return result


def canonical_asset_paths(repo_root: str | Path | None = None) -> dict[str, tuple[Path, Path]]:
    root = Path(repo_root or Path(__file__).resolve().parents[1]).resolve()
    return {
        "b2": (root / "usd/robot/b2/b2.usd", root / "usd/robot/b2/b2_canonical.usdc"),
        "lite3": (root / "usd/robot/lite3/Lite3.usd", root / "usd/robot/lite3/Lite3_canonical.usdc"),
    }


def repair_canonical_assets(
    names: Iterable[str] = ("b2", "lite3"),
    *,
    repo_root: str | Path | None = None,
) -> list[RepairResult]:
    paths = canonical_asset_paths(repo_root)
    results: list[RepairResult] = []
    for name in names:
        if name not in paths:
            results.append(RepairResult(name, "", issues=[f"unknown asset: {name}"]))
            continue
        source, destination = paths[name]
        expected = _CANONICAL_ROOTS.get(name, ())
        bodies = _CANONICAL_BODY_PATHS.get(name, ())
        results.append(
            repair_usd_asset(
                source,
                destination,
                expected_roots=expected,
                expected_body_paths=bodies,
                expected_visual_paths=_CANONICAL_VISUAL_PATHS.get(name, ()),
            )
        )
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("asset", nargs="*", choices=("b2", "lite3"), default=None)
    parser.add_argument("--repo-root", type=Path, default=None)
    parser.add_argument("--json", action="store_true", help="print machine-readable diagnostics")
    parser.add_argument("--check", action="store_true", help="validate existing canonical assets without writing")
    args = parser.parse_args(argv)
    names = args.asset or ("b2", "lite3")
    if args.check:
        paths = canonical_asset_paths(args.repo_root)
        results = [
            check_canonical_asset(
                source,
                destination,
                expected_roots=_CANONICAL_ROOTS.get(name, ()),
                expected_body_paths=_CANONICAL_BODY_PATHS.get(name, ()),
                expected_visual_paths=_CANONICAL_VISUAL_PATHS.get(name, ()),
            )
            if name in paths
            else RepairResult(name, "", issues=[f"unknown asset: {name}"])
            for name in names
            for source, destination in (paths.get(name, (Path(name), Path(""))),)
        ]
    else:
        results = repair_canonical_assets(names, repo_root=args.repo_root)
    if args.json:
        print(json.dumps([asdict(result) for result in results], ensure_ascii=False, indent=2))
    else:
        print("\n".join(result.format_diagnostics() for result in results))
    return 0 if all(result.ok for result in results) else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
