"""Fail-closed preflight for the unified 30-scene semantic-map descriptor."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def validate(path: Path, expected: int, categories: tuple[int, int, int]) -> list[str]:
    failures: list[str] = []
    doc = json.loads(path.read_text())
    scenes = doc.get("scenes")
    if not isinstance(scenes, list) or len(scenes) != expected:
        failures.append(f"scene count {len(scenes) if isinstance(scenes, list) else None} != {expected}")
        return failures
    if doc.get("schema") != "eai.semantic_map.scene_batch" or doc.get("version") != 2:
        failures.append("descriptor schema/version is not eai.semantic_map.scene_batch v2")
    keys, ids, level_ids = set(), set(), set()
    counts = {"living": 0, "production": 0, "service": 0}
    for index, scene in enumerate(scenes):
        where = f"scenes[{index}]"
        key = scene.get("scene_key")
        sid = scene.get("id")
        category = scene.get("category")
        if not isinstance(key, str) or key in keys or key.count(".") != 1:
            failures.append(f"{where}: scene_key must be unique category.slug")
        keys.add(key)
        if not isinstance(sid, str) or sid in ids:
            failures.append(f"{where}: id must be globally unique")
        ids.add(sid)
        if category not in counts:
            failures.append(f"{where}: invalid category {category!r}")
        else:
            counts[category] += 1
        usd = scene.get("usd")
        if not isinstance(usd, str) or not (Path(doc["scene_roots"][category]) / usd).is_file():
            failures.append(f"{where}: runtime USD missing: {usd}")
        levels_rows = scene.get("levels")
        if not isinstance(levels_rows, list) or not levels_rows:
            failures.append(f"{where}: at least one level/band is required")
            continue
        previous = None
        for level in levels_rows:
            lid = level.get("id")
            full = f"{key}:{lid}"
            if full in level_ids or not isinstance(lid, str):
                failures.append(f"{where}: duplicate/invalid level id {lid!r}")
            level_ids.add(full)
            z = level.get("support_z_m")
            if not isinstance(z, (int, float)):
                failures.append(f"{where}/{lid}: support_z_m must be numeric")
            elif previous is not None and z <= previous:
                failures.append(f"{where}: levels must be ordered by support_z_m")
            else:
                previous = z
            model = level.get("support_model", scene.get("support_model", "flat"))
            if model not in {"flat", "multi_band", "heightfield"}:
                failures.append(f"{where}/{lid}: invalid support_model {model!r}")
            if model == "heightfield" and level.get("support_z_range_m") is None:
                failures.append(f"{where}/{lid}: heightfield requires support_z_range_m")
        bounds = scene.get("navigation_bounds_xy")
        if bounds is not None and (not isinstance(bounds, list) or len(bounds) != 4
                                   or not bounds[0] < bounds[2] or not bounds[1] < bounds[3]):
            failures.append(f"{where}: invalid navigation_bounds_xy")
        if scene.get("geometry_source") not in {None, "auto", "collision_geometry",
                                                 "static_mesh_fallback", "audited_static_fallback"}:
            failures.append(f"{where}: invalid geometry_source")
    if tuple(counts.values()) != categories:
        failures.append(f"category counts {counts} != {dict(zip(counts, categories))}")
    if not failures:
        print(json.dumps({"ok": True, "descriptor_sha256": digest(path),
                          "scene_count": len(scenes), "category_counts": counts,
                          "level_count": len(level_ids)}, ensure_ascii=False))
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--descriptor", type=Path, required=True)
    parser.add_argument("--expected-scenes", type=int, default=30)
    parser.add_argument("--categories", default="10,10,10")
    args = parser.parse_args()
    categories = tuple(int(x) for x in args.categories.split(","))
    failures = validate(args.descriptor, args.expected_scenes, categories)
    if failures:
        print(json.dumps({"ok": False, "failures": failures}, ensure_ascii=False))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
