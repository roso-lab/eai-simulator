"""Install only referenced assets locally and rewrite USD through the OpenUSD API."""

import argparse
import json
import os
import shutil
from pathlib import Path

from pxr import Sdf, UsdUtils

if __package__ in (None, ""):
    from usd_closure import audit, collect_closure
else:
    from .usd_closure import audit, collect_closure


def relocate(roots, *, source_root, destination, boundary, backup, aliases=None):
    source_root, destination = Path(source_root).resolve(), Path(destination).resolve()
    boundary, backup = Path(boundary).resolve(), Path(backup).resolve()
    if not destination.is_relative_to(boundary):
        raise ValueError("Destination must be inside the USD boundary")
    files, layers, edges = collect_closure(roots, aliases=aliases)
    missing = [edge for edge in edges if edge["status"] == "missing"]
    if missing:
        raise ValueError(f"Unresolved dependencies; no files changed: {missing}")
    mapping = {}
    for path in files:
        if path.is_relative_to(boundary):
            mapping[path] = path
        elif path.is_relative_to(source_root):
            mapping[path] = destination / path.relative_to(source_root)
        else:
            raise ValueError(f"Dependency outside the supplied source root: {path}")
    for source, target in mapping.items():
        if source != target and target.exists():
            raise FileExistsError(f"Refusing to overwrite an existing asset: {target}")
    by_owner = {}
    for edge in edges:
        by_owner.setdefault(edge["owner"], {})[edge["authored"]] = edge
    rewritten = {}
    repairs = []
    for source, layer in layers.items():
        target = mapping[source]
        copy = Sdf.Layer.CreateAnonymous()
        copy.TransferContent(layer)
        changes = []

        def replace(authored):
            edge = by_owner[source].get(authored)
            if edge is None or edge["status"] == "runtime":
                return authored
            resolved = edge["paths"][0]
            relative = Path(os.path.relpath(mapping[resolved], target.parent)).as_posix()
            if "<UDIM>" in edge["replacement"]:
                relative = str(Path(relative).with_name(Path(edge["replacement"]).name))
            if relative != authored:
                changes.append((authored, relative))
            if edge["replacement"] != authored:
                repairs.append({"layer": target.relative_to(boundary).as_posix(),
                                "authored": authored, "replacement": relative})
            return relative

        UsdUtils.ModifyAssetPaths(copy, replace)
        metadata = copy.customLayerData
        metrics = metadata.get("metricsAssembler", {})
        if isinstance(metrics, dict):
            for entry in metrics.values():
                if isinstance(entry, dict) and isinstance(entry.get("write_layer"), str):
                    entry["write_layer"] = replace(entry["write_layer"])
            copy.customLayerData = metadata
        if source != target or changes:
            rewritten[source] = copy
    for source, target in sorted(mapping.items()):
        target.parent.mkdir(parents=True, exist_ok=True)
        if source == target and source in rewritten:
            saved = backup / source.relative_to(boundary)
            saved.parent.mkdir(parents=True, exist_ok=True)
            if saved.exists():
                raise FileExistsError(f"Backup already exists: {saved}")
            shutil.copy2(source, saved)
        if source in rewritten:
            temporary = target.with_name(target.stem + ".relocating" + target.suffix)
            rewritten[source].Export(str(temporary))
            os.replace(temporary, target)
        elif source != target:
            shutil.copy2(source, target)
    for source, layer in layers.items():
        if source == mapping[source] and source in rewritten:
            layer.Reload()
    report = audit([mapping[Path(root).resolve()] for root in roots], boundary=boundary, hashes=True)
    report["repairs"] = repairs
    report["copied_files"] = sum(source != target for source, target in mapping.items())
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("roots", nargs="+")
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--boundary", type=Path, default=Path(__file__).resolve().parents[2] / "usd")
    parser.add_argument("--backup", type=Path, required=True)
    parser.add_argument("--aliases", type=Path, help="JSON mapping authored paths to source-root-relative repair paths")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    aliases = {}
    if args.aliases:
        aliases = {key: str((args.source_root / value).resolve()) for key, value in
                   json.loads(args.aliases.read_text(encoding="utf-8")).items()}
    report = relocate(args.roots, source_root=args.source_root, destination=args.destination,
                      boundary=args.boundary, backup=args.backup, aliases=aliases)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("validated", "file_count", "total_bytes", "copied_files", "missing")}))
    return int(not report["validated"])


if __name__ == "__main__":
    raise SystemExit(main())
