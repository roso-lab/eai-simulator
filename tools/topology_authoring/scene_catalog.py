"""Scene identities shared by the browser catalog and the Isaac launcher."""
from pathlib import Path, PurePosixPath


def discover_scene_projects(asset_root):
    """Return scene-local map projects keyed by their scene-relative path.

    A release package keeps the editable map beside the USD root at
    ``scene/<category>/<scene>/semantic_map``.  Do not follow symlinks here:
    discovery must not make a portable launcher depend on files outside its
    asset root.
    """
    root = Path(asset_root).resolve()
    scene_root = root / "scene"
    if not scene_root.is_dir():
        return {}
    projects = {}
    for category in sorted(scene_root.iterdir()):
        if not category.is_dir() or category.is_symlink():
            continue
        for scene in sorted(category.iterdir()):
            project = scene / "semantic_map" / "project.json"
            if (scene.is_dir() and not scene.is_symlink() and project.is_file()
                    and not project.is_symlink()):
                projects[scene.relative_to(scene_root).as_posix()] = project.parent
    return projects


def source_usd(manifest):
    provenance = manifest.get("provenance", {})
    for row in (provenance, provenance.get("semantic-footprints.json", {}),
                provenance.get("semantic-manifest.json", {})):
        if row.get("source_usd"):
            return str(row["source_usd"])
    return None


def scene_metadata(manifest):
    source = source_usd(manifest)
    parts = PurePosixPath(source.replace("\\", "/")).parts if source else ()
    if "scene" in parts:
        parts = parts[parts.index("scene"):]
    if len(parts) >= 4 and parts[0] == "scene":
        return {"category": parts[1], "scene_name": parts[2],
                "source_path": PurePosixPath(*parts).as_posix()}
    return {"category": "Other", "scene_name": manifest["scene_key"],
            "source_path": source}


def resolve_scene(manifest, asset_root):
    """Resolve the bound root USD under the configured usd/ asset root.

    Historic projects may record an absolute path from another checkout. Only
    its scene-relative suffix is reused; no arbitrary USD is substituted.
    """
    relative = scene_metadata(manifest)["source_path"]
    if not relative:
        raise ValueError("Map project has no source USD binding")
    root = Path(asset_root).resolve()
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("Scene source must be a path inside the usd/ asset root")
    resolved = (root / path).resolve()
    if not resolved.is_relative_to(root):
        raise ValueError("Scene source escapes the usd/ asset root")
    if not resolved.is_file() or resolved.suffix.lower() not in {".usd", ".usda", ".usdc"}:
        raise ValueError(f"Root USD does not exist: {relative}")
    return resolved
