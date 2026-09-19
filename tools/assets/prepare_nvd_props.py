"""Build an ASCII-safe, local shadow for the benchmark's NVD props.

The input NVD download is treated as read-only.  USD layers are copied through
OpenUSD and their asset paths are rewritten relative to the shadow.  Other
closure files are placed in a content-addressed blob directory and exposed by
symlink, so repeated textures are stored once while every symlink target stays
inside the audit boundary.

The output layout deliberately preserves the registry paths after replacing
the USD-reserved ``@`` package delimiter with ``_v``::

    <shadow>/XR_Content_NVD_v10010/Assets/XR/...
    <shadow>/SimReady_Warehouse_02_NVD_v10010/Assets/simready_content/...

No physics, transforms, stage units, or geometry are authored by this tool.
The source props are visual-only assets; runtime scenario code remains the
owner of any physics policy.  Beyond the USD reference closure, the shadow
also carries every source-tree MDL module reached through ``import``/``using``
edges (for example ``GlassWithVolume``/``GlassUtils`` for the glass and water
families), because USD closure collection cannot see MDL-to-MDL imports.

Example::

    python tools/assets/prepare_nvd_props.py \
      --output-root tmp/codex_resume_20260916/nvd_props_shadow_v1 \
      --report tmp/codex_resume_20260916/nvd_props_report_v1.json

Use ``--dry-run`` to validate source closure and print the planned mapping
without creating the shadow.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Iterable

from pxr import Sdf, Usd, UsdGeom, UsdUtils

if __package__ in (None, ""):
    from usd_closure import USD_SUFFIXES, audit, collect_closure
else:
    from .usd_closure import USD_SUFFIXES, audit, collect_closure


DEFAULT_SOURCE_ROOT = Path("/home/pupil/下载/NVD_assets")
DEFAULT_KEYS = (
    "cardbox_c",
    "wooden_crate_a",
    "mh_armchair",
    "mh_bookstack",
    "mh_bowl_brass",
    "nvd_pallet_asm",
)

# These are the exact source paths represented by the tracked registry.  The
# first five are XR_Content_NVD assets; the pallet keeps its SimReady registry
# path and is rebuilt from the untouched raw package so its transitive closure
# does not point back to the old sanitized tree.
PROP_SOURCES = {
    "cardbox_c": "XR_Content_NVD@10010/Assets/XR/Stages/Indoor/Warehouse/Containers/Cardboard/Cardbox_C2.usd",
    "wooden_crate_a": "XR_Content_NVD@10010/Assets/XR/Stages/Indoor/Warehouse/Containers/Wooden/WoodenCrate_A1.usd",
    "mh_armchair": "XR_Content_NVD@10010/Assets/XR/Stages/Indoor/Modern_House/SubUSDs/Armchair.usd",
    "mh_bookstack": "XR_Content_NVD@10010/Assets/XR/Stages/Indoor/Modern_House/SubUSDs/BookStack_01.usd",
    "mh_bowl_brass": "XR_Content_NVD@10010/Assets/XR/Stages/Indoor/Modern_House/SubUSDs/BlackandBrassBowl_Large.usd",
    "nvd_pallet_asm": "SimReady_Warehouse_02_NVD@10010/Assets/simready_content/common_assets/props/pallet_asm/pallet_asm_base.usd",
}

# Registry paths use the ASCII-safe shadow spelling.  The source map below
# intentionally retains ``@`` because it addresses the untouched download;
# the report compares both forms so a future registry drift is visible.
CURRENT_REGISTRY_PATHS = {
    "cardbox_c": "XR_Content_NVD_v10010/Assets/XR/Stages/Indoor/Warehouse/Containers/Cardboard/Cardbox_C2.usd",
    "wooden_crate_a": "XR_Content_NVD_v10010/Assets/XR/Stages/Indoor/Warehouse/Containers/Wooden/WoodenCrate_A1.usd",
    "mh_armchair": "XR_Content_NVD_v10010/Assets/XR/Stages/Indoor/Modern_House/SubUSDs/Armchair.usd",
    "mh_bookstack": "XR_Content_NVD_v10010/Assets/XR/Stages/Indoor/Modern_House/SubUSDs/BookStack_01.usd",
    "mh_bowl_brass": "XR_Content_NVD_v10010/Assets/XR/Stages/Indoor/Modern_House/SubUSDs/BlackandBrassBowl_Large.usd",
    "nvd_pallet_asm": "SimReady_Warehouse_02_NVD_v10010/Assets/simready_content/common_assets/props/pallet_asm/pallet_asm_base.usd",
}

# SimReady's generic material templates use ``texture_2d()`` with no asset
# argument for an intentionally untextured input.  The repository closure
# walker quite correctly treats an empty authored path as unresolved in its
# general-purpose mode.  For this provider-specific preparation, map only
# that empty MDL resource to a runtime module sentinel; no file is required
# and the original empty constructor remains unchanged in the copied MDL.
EMPTY_MDL_ALIAS = {"": "OmniPBR.mdl"}

_SAFE_COMPONENT = re.compile(r"[^A-Za-z0-9._-]")


def sanitize_component(component: str) -> str:
    """Make one path component safe for USD asset identifiers and shells."""

    # ``@`` has a meaningful USD package-path role, so retain the registry's
    # established spelling for package versions.
    component = component.replace("@", "_v")
    component = _SAFE_COMPONENT.sub("_", component)
    if not component or component in {".", ".."}:
        raise ValueError(f"unsafe empty path component: {component!r}")
    return component


def sanitize_relative(path: Path) -> Path:
    if path.is_absolute():
        raise ValueError(f"expected a relative path: {path}")
    result = Path(*(sanitize_component(part) for part in path.parts))
    if "@" in result.as_posix() or not result.as_posix().isascii():
        raise AssertionError(f"sanitized path is not ASCII-safe: {result}")
    return result


def _stage_bbox(path: Path) -> dict:
    stage = Usd.Stage.Open(str(path))
    if stage is None:
        raise ValueError(f"cannot open USD stage: {path}")
    root = stage.GetDefaultPrim()
    if not root:
        root = next(iter(stage.Traverse()), None)
    if not root:
        raise ValueError(f"USD stage has no root prim: {path}")
    cache = UsdGeom.BBoxCache(
        Usd.TimeCode.Default(),
        [UsdGeom.Tokens.default_, UsdGeom.Tokens.proxyPrim],
    )
    size = cache.ComputeWorldBound(root).ComputeAlignedRange().GetSize()
    return {
        "size_stage_units": [float(value) for value in size],
        "size_m": [float(value) * float(UsdGeom.GetStageMetersPerUnit(stage)) for value in size],
        "meters_per_unit": float(UsdGeom.GetStageMetersPerUnit(stage)),
        "up_axis": str(UsdGeom.GetStageUpAxis(stage)),
        "default_prim": str(root.GetPath()),
        "rigid_body_prims": sum(prim.HasAPI("PhysicsRigidBodyAPI") for prim in stage.Traverse()),
    }


def _relative_target(source: Path, target: Path, mapping: dict[Path, Path]) -> str:
    try:
        resolved_target = mapping[source.resolve()]
    except KeyError as exc:
        raise ValueError(f"closure file has no output mapping: {source}") from exc
    return Path(os.path.relpath(resolved_target, target.parent)).as_posix()


def _rewrite_layers(
    layers: dict[Path, Sdf.Layer],
    edges: list[dict],
    mapping: dict[Path, Path],
    boundary: Path,
) -> tuple[dict[Path, Sdf.Layer], list[dict]]:
    by_owner: dict[Path, dict[str, dict]] = {}
    for edge in edges:
        by_owner.setdefault(Path(edge["owner"]).resolve(), {})[edge["authored"]] = edge

    rewritten: dict[Path, Sdf.Layer] = {}
    repairs: list[dict] = []
    for source, layer in layers.items():
        source = source.resolve()
        target = mapping[source]
        copy = Sdf.Layer.CreateAnonymous()
        copy.TransferContent(layer)
        owner_edges = by_owner.get(source, {})

        def replace(authored):
            edge = owner_edges.get(authored)
            if edge is None or edge["status"] == "runtime":
                return authored
            if edge["status"] != "resolved" or not edge["paths"]:
                raise ValueError(f"unresolved dependency while rewriting {source}: {authored}")
            relative = _relative_target(Path(edge["paths"][0]), target, mapping)
            if "<UDIM>" in edge["replacement"]:
                relative = str(Path(relative).with_name(Path(edge["replacement"]).name))
            if edge["replacement"] != authored or relative != authored:
                repairs.append({
                    "layer": target.relative_to(boundary).as_posix(),
                    "authored": authored,
                    "rewritten": relative,
                })
            return relative

        UsdUtils.ModifyAssetPaths(copy, replace)
        metadata = copy.customLayerData
        metrics = metadata.get("metricsAssembler", {})
        if isinstance(metrics, dict):
            for entry in metrics.values():
                if isinstance(entry, dict) and isinstance(entry.get("write_layer"), str):
                    entry["write_layer"] = replace(entry["write_layer"])
            copy.customLayerData = metadata
        rewritten[source] = copy
    return rewritten, repairs


def _sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


# MDL stdlib and Kit-builtin modules that never resolve inside the source
# download; only modules present as ``<name>.mdl`` under the source root are
# treated as shadow-copyable dependencies.
_MDL_BUILTIN_MODULES = frozenset({
    "base", "tex", "state", "anno", "math", "df", "misc", "dist", "nvidia",
    "OmniPBR", "OmniGlass",
})
_MDL_IMPORT_RE = re.compile(
    r"^\s*(?:using\s+([A-Za-z_]\w*)\s+import"
    r"|import\s+::([A-Za-z_]\w*)::"
    r"|import\s+([A-Za-z_]\w*)::)",
    re.M,
)


def _mdl_module_closure(files: set[Path], source_root: Path) -> list[str]:
    """Grow ``files`` with every source-tree MDL module imported by it.

    USD closure collection cannot see ``import``/``using`` edges between MDL
    files, so glass/water families previously shipped without modules such as
    ``GlassWithVolume`` and compiled red at runtime.  Returns the added module
    names for the report.
    """
    index: dict[str, Path] = {}
    for path in source_root.rglob("*.mdl"):
        if ".thumbs" in path.parts:
            continue
        index.setdefault(path.stem, path.resolve())
    pending = [path for path in files if path.suffix.lower() == ".mdl"]
    added: list[str] = []
    seen = set(pending)
    while pending:
        current = pending.pop()
        text = current.read_text(encoding="utf-8", errors="ignore")
        for groups in _MDL_IMPORT_RE.findall(text):
            module = next((group for group in groups if group), None)
            if (module is None or module in _MDL_BUILTIN_MODULES
                    or module in index and index[module] in seen):
                continue
            resolved = index.get(module)
            if resolved is None:
                continue
            files.add(resolved)
            seen.add(resolved)
            pending.append(resolved)
            added.append(module)
    return sorted(set(added))


def _write_shadow(
    files: set[Path],
    layers: dict[Path, Sdf.Layer],
    mapping: dict[Path, Path],
    rewritten: dict[Path, Sdf.Layer],
    output_root: Path,
) -> dict:
    blobs = output_root / ".blobs"
    output_root.mkdir(parents=True)
    blobs.mkdir()
    blob_by_digest: dict[tuple[str, str], Path] = {}
    materialized = 0
    symlinks = 0
    for source in sorted(files):
        source = source.resolve()
        target = mapping[source]
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() or target.is_symlink():
            raise FileExistsError(f"refusing to overwrite output: {target}")
        if source in rewritten:
            temporary = target.with_name(target.name + ".tmp")
            rewritten[source].Export(str(temporary))
            os.replace(temporary, target)
            materialized += 1
            continue
        # MDL contains relative texture constructors.  Keep the file at its
        # authored relative location so those constructors continue to resolve
        # from the MDL owner; only the large binary texture resources use the
        # content-addressed symlink path below.
        if source.suffix.lower() == ".mdl":
            shutil.copy2(source, target)
            materialized += 1
            continue
        digest = _sha256(source)
        suffix = source.suffix.lower() or ".bin"
        blob_key = (digest, suffix)
        blob = blob_by_digest.get(blob_key)
        if blob is None:
            blob = blobs / f"{digest}{suffix}"
            if not blob.exists():
                shutil.copy2(source, blob)
            blob_by_digest[blob_key] = blob
        target.symlink_to(os.path.relpath(blob, target.parent))
        symlinks += 1
    return {"materialized_usd_and_mdl_files": materialized, "symlinked_files": symlinks,
            "unique_blobs": len(blob_by_digest)}


def prepare(
    *,
    source_root: Path,
    output_root: Path,
    keys: Iterable[str],
    dry_run: bool = False,
) -> dict:
    source_root = source_root.expanduser().resolve()
    output_root = output_root.expanduser().resolve()
    if not source_root.is_dir():
        raise FileNotFoundError(f"source root does not exist: {source_root}")
    if output_root.exists():
        raise FileExistsError(f"output already exists; choose a new directory: {output_root}")
    keys = tuple(keys)
    unknown = [key for key in keys if key not in PROP_SOURCES]
    if unknown:
        raise ValueError(f"unknown NVD prop keys: {unknown}")

    roots = []
    for key in keys:
        path = (source_root / PROP_SOURCES[key]).resolve()
        if not path.is_file():
            raise FileNotFoundError(f"source for {key} does not exist: {path}")
        roots.append(path)
    files, layers, edges = collect_closure(roots, aliases=EMPTY_MDL_ALIAS)
    missing = [edge for edge in edges if edge["status"] == "missing"]
    if missing:
        raise ValueError(f"unresolved dependencies; no files changed: {missing}")
    mdl_modules_added = _mdl_module_closure(files, source_root)

    mapping: dict[Path, Path] = {}
    for source in files:
        source = source.resolve()
        if not source.is_relative_to(source_root):
            raise ValueError(f"closure dependency escapes source root: {source}")
        mapping[source] = output_root / sanitize_relative(source.relative_to(source_root))
    for target in mapping.values():
        if not target.as_posix().isascii() or "@" in target.as_posix():
            raise AssertionError(f"output path is not ASCII-safe: {target}")

    rewritten, repairs = _rewrite_layers(layers, edges, mapping, output_root)
    source_bboxes = {key: _stage_bbox(roots[index]) for index, key in enumerate(keys)}
    report = {
        "schema": "eai.benchmark.nvd_prop_shadow",
        "version": 1,
        "dry_run": dry_run,
        "source_root": str(source_root),
        "output_root": str(output_root),
        "registry_env": "EAI_NVD_ASSET_ROOT",
        "registry_root_value": str(output_root),
        "keys": list(keys),
        "roots": {
            key: {
                "registry_path": CURRENT_REGISTRY_PATHS[key],
                "shadow_relative_path": sanitize_relative(Path(PROP_SOURCES[key])).as_posix(),
                "source": str(roots[index]),
                "shadow": str(mapping[roots[index]]),
                "bbox": source_bboxes[key],
            }
            for index, key in enumerate(keys)
        },
        "closure": {
            "file_count": len(files),
            "usd_layer_count": len(layers),
            "edge_count": len(edges),
            "missing": missing,
            "mdl_import_modules_added": mdl_modules_added,
            "runtime_modules": sorted({edge["authored"] for edge in edges
                                        if edge["status"] == "runtime" and edge["authored"]}),
            "provider_ignored_empty_mdl_resources": sum(
                edge["authored"] == "" and edge["replacement"] == "OmniPBR.mdl"
                for edge in edges
            ),
        },
        "repairs": repairs,
        "missing_files": [],
        "registry_mapping_required": {
            key: {
                "current_registry_path": CURRENT_REGISTRY_PATHS[key],
                "shadow_path": sanitize_relative(Path(PROP_SOURCES[key])).as_posix(),
                "change_required": CURRENT_REGISTRY_PATHS[key]
                != sanitize_relative(Path(PROP_SOURCES[key])).as_posix(),
            }
            for key in keys
        },
    }
    if not dry_run:
        report["write"] = _write_shadow(files, layers, mapping, rewritten, output_root)
        closure = audit(roots=[mapping[root] for root in roots], boundary=output_root, hashes=True)
        # The provider's empty texture_2d() constructors are intentional
        # untextured inputs.  The general audit tool reports those empty MDL
        # arguments as missing; all actual file dependencies remain subject to
        # the normal audit result and boundary checks.
        empty_mdl_missing = [entry for entry in closure["missing"]
                             if entry["asset"] == "" and entry["layer"].endswith(".mdl")]
        closure["missing"] = [entry for entry in closure["missing"] if entry not in empty_mdl_missing]
        closure["provider_ignored_empty_mdl_resources"] = len(empty_mdl_missing)
        closure["validated"] = not (closure["missing"] or closure["absolute_references"]
                                     or closure["outside_boundary"])
        report["shadow_closure"] = closure
        report["shadow_bboxes"] = {
            key: _stage_bbox(mapping[roots[index]]) for index, key in enumerate(keys)
        }
        for key in keys:
            before = report["roots"][key]["bbox"]
            after = report["shadow_bboxes"][key]
            if before["meters_per_unit"] != after["meters_per_unit"] or before["up_axis"] != after["up_axis"]:
                raise AssertionError(f"stage unit/up-axis changed for {key}")
            if any(abs(a - b) > 1e-5 for a, b in zip(before["size_stage_units"], after["size_stage_units"])):
                raise AssertionError(f"bbox changed for {key}: {before} -> {after}")
        if not closure["validated"]:
            raise ValueError(f"shadow closure did not validate: {closure}")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--keys", nargs="+", default=DEFAULT_KEYS, choices=tuple(PROP_SOURCES))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = prepare(source_root=args.source_root, output_root=args.output_root,
                         keys=args.keys, dry_run=args.dry_run)
    except (FileNotFoundError, FileExistsError, ValueError, AssertionError) as exc:
        print(f"[nvd-props] ERROR: {exc}", file=sys.stderr)
        return 2
    if args.report:
        report_path = args.report.expanduser().resolve()
        if report_path.exists():
            print(f"[nvd-props] ERROR: refusing to overwrite report: {report_path}", file=sys.stderr)
            return 2
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "output_root": str(args.output_root.expanduser().resolve()),
        "dry_run": args.dry_run,
        "keys": list(args.keys),
        "file_count": report["closure"]["file_count"],
        "missing": report["closure"]["missing"],
        "validated": report.get("shadow_closure", {}).get("validated", args.dry_run),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
