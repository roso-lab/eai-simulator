"""Move editable map projects beside their USD roots without personal paths.

The release layout is ``usd/scene/<category>/<scene>/semantic_map``.  It keeps
the current editable revision and base grids, rewrites USD bindings to
``scene/...`` paths, and bundles JSON-referenced historical evidence beneath
``evidence/``.  USD assets themselves are never copied.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil

from .map_project import MapProject, read_json, write_json
from .scene_catalog import scene_metadata


def file_digest(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def project_sources(catalog, office_project):
    """Select one current project per scene, with the revised office winning."""
    sources = {}
    for path in sorted(Path(catalog).iterdir()):
        if (path / "project.json").is_file():
            project = MapProject(path)
            sources[project.document.scene_key] = project
    office = MapProject(office_project)
    sources[office.document.scene_key] = office
    return sources


class EvidenceCopier:
    def __init__(self, destination, asset_root):
        self.destination = Path(destination)
        self.asset_root = Path(asset_root).resolve()
        self.evidence = self.destination / "evidence"
        self.copied = {}
        self.omissions = []

    def rewrite(self, value, field=None):
        if isinstance(value, dict):
            return {self.rewrite(key): self.rewrite(item, key) for key, item in value.items()}
        if isinstance(value, list):
            return [self.rewrite(item, field) for item in value]
        if not isinstance(value, str) or not Path(value).is_absolute():
            return value
        source = Path(value)
        try:
            resolved = source.resolve()
        except OSError:
            self.omissions.append({"field": field, "path": value, "reason": "unresolvable"})
            return "historical-source-not-bundled"
        if resolved.is_relative_to(self.asset_root):
            return resolved.relative_to(self.asset_root).as_posix()
        if field == "import_directory":
            return "historical-import-package-not-bundled"
        if not resolved.is_file():
            self.omissions.append({"field": field, "path": value, "reason": "not-a-file"})
            return "historical-source-not-bundled"
        return self.copy_evidence(resolved)

    def copy_evidence(self, source):
        source = Path(source).resolve()
        if source in self.copied:
            return self.copied[source]
        name = f"{file_digest(source)[:16]}-{source.name}"
        relative = Path("evidence") / name
        destination = self.destination / relative
        self.copied[source] = relative.as_posix()
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.suffix.lower() == ".json":
            try:
                write_json(destination, self.rewrite(read_json(source)))
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                self.omissions.append({"field": "evidence", "path": str(source), "reason": str(exc)})
                self.copied[source] = "historical-source-not-bundled"
        else:
            shutil.copy2(source, destination)
        return self.copied[source]


def usage_readme(manifest):
    metadata = scene_metadata(manifest)
    return f"""# Editable semantic map: {manifest['scene_key']}

This directory is the scene-local editable map package for
`{metadata['source_path']}`. Start the integrated launcher from the repository
root; it discovers this map automatically:

```bash
python tools/topology_authoring/launch.py --scene {manifest['scene_key']}
```

`project.json` points to the current complete revision. Edit only through the
map tool, which writes a new revision atomically. `base/` contains the immutable
geometry grids used by every revision. `evidence/` preserves historical visual
observations referenced by the migrated revision; it is not USD content.
"""


def migrate_project(project, asset_root, *, dry_run=False):
    source = project.root
    manifest = read_json(source / "project.json")
    metadata = scene_metadata(manifest)
    if not metadata["source_path"] or not metadata["source_path"].startswith("scene/"):
        raise ValueError(f"{source}: no scene-relative source USD")
    target = Path(asset_root).resolve() / Path(metadata["source_path"]).parent / "semantic_map"
    if target.exists():
        raise ValueError(f"refusing to overwrite existing map package: {target}")
    if dry_run:
        return {"scene_key": project.document.scene_key, "target": str(target), "evidence": 0, "omissions": []}

    target.mkdir(parents=True)
    copier = EvidenceCopier(target, asset_root)
    migrated_manifest = copier.rewrite(manifest)
    revision_rel = Path(manifest["current_revision"])
    shutil.copytree(source / "base", target / "base")
    shutil.copytree(source / revision_rel, target / revision_rel)

    # Rewrite all JSON within the one supported, current revision.  Its manifest
    # must then bind the rewritten files, while binary grids/previews remain exact.
    revision = target / revision_rel
    for path in sorted(revision.glob("*.json")):
        if path.name != "manifest.json":
            write_json(path, copier.rewrite(read_json(path)))
    revision_manifest = read_json(revision / "manifest.json")
    revision_manifest["files"] = {
        name: file_digest(revision / name) for name in revision_manifest["files"]
    }
    write_json(revision / "manifest.json", revision_manifest)
    write_json(target / "project.json", migrated_manifest)
    (target / "README.md").write_text(usage_readme(migrated_manifest), encoding="utf-8")

    # Validate both content hashes and editable map invariants after rewriting.
    MapProject(target)
    return {"scene_key": project.document.scene_key, "target": str(target),
            "evidence": len(copier.copied), "omissions": copier.omissions}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--office-project", type=Path, required=True,
                        help="revised office project that supersedes catalog office")
    parser.add_argument("--asset-root", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    reports = [migrate_project(project, args.asset_root, dry_run=args.dry_run)
               for _key, project in sorted(project_sources(args.catalog, args.office_project).items())]
    print(json.dumps({"projects": reports, "count": len(reports)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
