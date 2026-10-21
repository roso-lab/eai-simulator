"""Clear retired annotations and migrate to one current map package."""
from __future__ import annotations

import argparse
from copy import deepcopy
from pathlib import Path

from .map_project import MapProject, write_json, read_json
from .scene_catalog import discover_scene_projects


def clean_project(root, *, apply=False):
    project = MapProject(root)
    old_revision = project.saved_revision
    old_save_id = read_json(project.root / old_revision / "manifest.json")["revision"]
    needs_migration = old_revision != "current" or (project.root / "revisions").exists()
    graph = {key: (deepcopy(doc.nodes), deepcopy(doc.edges)) for key, doc in project.documents.items()}
    before_points = deepcopy(project.semantic_points)
    counts = project.clear_legacy_semantics()
    changed = any(n for group in counts.values() for n in group.values())
    legacy_export = read_json(project.root / old_revision / "semantic-footprints.json")
    stale_export = bool(legacy_export.get("visual_review")) and not any(
        (doc.semantics or {}).get(key) for doc in project.documents.values() for key in ("regions", "entities"))
    changed = changed or stale_export or needs_migration
    if apply and changed:
        project.save()
        reopened = MapProject(root)
        if (any((reopened.document_for(key).nodes, reopened.document_for(key).edges) != value
                for key, value in graph.items()) or reopened.semantic_points != before_points):
            raise RuntimeError("cleanup altered retained graph or semantic points")
    return {"scene_key": project.document.scene_key, "project": str(project.root),
            "removed": counts, "old_revision": old_revision,
            "new_revision": project.saved_revision, "old_save_id": old_save_id,
            "new_save_id": project.manifest.get("save_id", old_save_id),
            "storage_migration": needs_migration, "changed": changed, "applied": apply and changed,
            "retained": {key: {"nodes": len(doc.nodes), "edges": len(doc.edges)}
                         for key, doc in project.documents.items()},
            "semantic_points": len(project.semantic_points)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--asset-root", type=Path, required=True)
    parser.add_argument("--apply", action="store_true", help="overwrite current packages (default: dry run)")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    report = {"apply": args.apply, "projects": [], "errors": []}
    for key, root in discover_scene_projects(args.asset_root).items():
        try:
            row = clean_project(root, apply=args.apply)
            report["projects"].append(row)
            print(f"{row['scene_key']}: {'published' if row['applied'] else 'planned' if row['changed'] else 'already clean'}", flush=True)
        except (ValueError, OSError, RuntimeError) as error:
            report["errors"].append({"project": key, "error": str(error)})
        # Preserve completed per-project outcomes even if a later map fails.
        write_json(args.report, report)
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
