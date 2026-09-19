"""Offline USD dependency traversal, including variants, UDIM tiles and MDL resources."""

from __future__ import annotations

import glob
import hashlib
import json
import re
from pathlib import Path

from pxr import Sdf, UsdUtils

RUNTIME_MODULES = frozenset({
    "OmniPBR", "OmniGlass", "OmniSurface", "OmniSurfacePresets",
    "anno", "base", "builtins", "df", "math", "state", "tex", "limits", "scene",
    "nvidia/core_definitions",
})
USD_SUFFIXES = {".usd", ".usda", ".usdc"}
MDL_TOKENS = re.compile(
    r'//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\])*"|::|[A-Za-z_][A-Za-z_0-9]*|[^\s]', re.DOTALL)


def usd_paths(layer):
    """Use OpenUSD's asset visitor on a copy so the source stays read-only."""
    paths = set()
    scratch = Sdf.Layer.CreateAnonymous()
    scratch.TransferContent(layer)

    def strip_non_dependencies(path):
        spec = scratch.GetObjectAtPath(path)
        if isinstance(spec, Sdf.PrimSpec):
            for field in ("referenceList", "payloadList"):
                operation = getattr(spec, field)
                operation.deletedItems = []
                operation.orderedItems = []

    scratch.Traverse("/", strip_non_dependencies)

    def collect(path):
        if path:
            paths.add(path)
        return path

    UsdUtils.ModifyAssetPaths(scratch, collect)
    metrics = scratch.customLayerData.get("metricsAssembler", {})
    if isinstance(metrics, dict):
        for entry in metrics.values():
            if isinstance(entry, dict) and isinstance(entry.get("write_layer"), str):
                collect(entry["write_layer"])
    return sorted(paths)


def mdl_paths(path):
    """Read literal MDL resource constructors and module imports without executing MDL."""
    tokens = [match.group() for match in MDL_TOKENS.finditer(path.read_text(encoding="utf-8"))
              if not match.group().startswith(("//", "/*"))]
    resources = set()
    for index, token in enumerate(tokens):
        if token in {"texture_2d", "texture_3d", "texture_cube", "light_profile", "bsdf_measurement"}:
            if tokens[index + 1:index + 2] == ["("]:
                argument = tokens[index + 2]
                if argument.startswith('"'):
                    resources.add(json.loads(argument))
                elif argument != ")":
                    raise ValueError(f"Computed MDL resource needs compiler inspection: {path}")
        if token in {"import", "using"} and tokens[index + 1:index + 2] == ["::"]:
            parts = []
            cursor = index + 2
            while cursor < len(tokens):
                parts.append(tokens[cursor])
                if tokens[cursor + 1:cursor + 2] != ["::"]:
                    break
                cursor += 2
            if parts[0] in RUNTIME_MODULES:
                resources.add(parts[0] + ".mdl")
            else:
                module = parts if token == "using" else parts[:-1]
                resources.add("/".join(module) + ".mdl")
    return sorted(resources)


def resolve_paths(owner, authored):
    if authored in {name + ".mdl" for name in RUNTIME_MODULES}:
        return [], "runtime"
    if ":" in authored and not Path(authored).is_absolute():
        return [], "missing"
    candidate = (owner.parent / authored).resolve()
    if "<UDIM>" in authored:
        matches = sorted(Path(item).resolve() for item in glob.glob(
            str(candidate).replace("<UDIM>", "[1-9][0-9][0-9][0-9]")))
    else:
        matches = [candidate] if candidate.is_file() else []
    return matches, "resolved" if matches else "missing"


def collect_closure(roots, *, aliases=None):
    aliases = aliases or {}
    pending = [Path(root).resolve() for root in roots]
    files, edges, layers = set(), [], {}
    while pending:
        owner = pending.pop()
        if owner in files:
            continue
        if not owner.is_file():
            raise FileNotFoundError(owner)
        files.add(owner)
        if owner.suffix.lower() in USD_SUFFIXES:
            layer = Sdf.Layer.FindOrOpen(str(owner))
            if layer is None:
                raise ValueError(f"Cannot open USD layer: {owner}")
            layers[owner] = layer
            dependencies = usd_paths(layer)
        elif owner.suffix.lower() == ".mdl":
            dependencies = mdl_paths(owner)
        else:
            continue
        for authored in dependencies:
            replacement = aliases.get(authored, authored)
            resolved, status = resolve_paths(owner, replacement)
            edges.append({"owner": owner, "authored": authored, "replacement": replacement,
                          "paths": resolved, "status": status})
            pending.extend(resolved)
    return files, layers, edges


def audit(roots, *, boundary, hashes=False):
    boundary = Path(boundary).resolve()
    files, layers, edges = collect_closure(roots)

    def display(path):
        return path.relative_to(boundary).as_posix() if path.is_relative_to(boundary) else str(path)

    missing = [{"layer": display(edge["owner"]), "asset": edge["authored"]}
               for edge in edges if edge["status"] == "missing"]
    absolute = [{"layer": display(edge["owner"]), "asset": edge["authored"]}
                for edge in edges if Path(edge["authored"]).is_absolute() or ":" in edge["authored"]]
    outside = sorted(str(path) for path in files if not path.is_relative_to(boundary))
    entries = []
    for path in sorted(files):
        entry = {"path": display(path), "bytes": path.stat().st_size}
        if hashes:
            with path.open("rb") as stream:
                entry["sha256"] = hashlib.file_digest(stream, "sha256").hexdigest()
        entries.append(entry)
    return {"roots": [display(Path(root).resolve()) for root in roots],
            "validated": not (missing or absolute or outside),
            "layer_count": len(layers), "file_count": len(files),
            "total_bytes": sum(entry["bytes"] for entry in entries),
            "missing": missing, "absolute_references": absolute, "outside_boundary": outside,
            "runtime_modules": sorted({edge["authored"] for edge in edges if edge["status"] == "runtime"}),
            "files": entries}
