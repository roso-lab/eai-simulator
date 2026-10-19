"""Merge per-level Flash visual reviews into editable batch observations."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


CONFIDENCE = {
    "observed",
    "layout_inferred",
    "category_uncertain",
    "unknown_function",
    "observed_geometry_endpoint_unverified",
}


def iter_reviews(root: Path):
    for path in sorted(root.glob("*.json")):
        if not path.stat().st_size:
            continue
        payload = json.loads(path.read_text())
        rows = payload if isinstance(payload, list) else [payload]
        for row in rows:
            yield path, row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--capture-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    scenes = {}
    source_files = []
    for path, review in iter_reviews(args.input_dir):
        scene_id = review.get("scene_id")
        level_id = review.get("level_id")
        if not scene_id or not level_id:
            raise ValueError(f"missing scene_id/level_id: {path}")
        level = scenes.setdefault(scene_id, {"levels": {}})["levels"]
        if level_id in level:
            raise ValueError(f"duplicate review for {scene_id}/{level_id}: {path}")
        image = (args.capture_root / scene_id / f"{level_id}-material-top.png").resolve()
        if not image.is_file():
            raise FileNotFoundError(f"missing coordinate-aligned evidence: {image}")
        normalized = dict(review)
        for collection in ("regions", "objects"):
            items = []
            for item in review.get(collection, []):
                confidence = item.get("confidence")
                if confidence not in CONFIDENCE:
                    raise ValueError(f"invalid confidence {confidence!r}: {path}")
                bounds = item.get("bounds_norm")
                if not (isinstance(bounds, list) and len(bounds) == 4 and
                        0 <= bounds[0] < bounds[2] <= 1 and
                        0 <= bounds[1] < bounds[3] <= 1):
                    raise ValueError(f"invalid bounds_norm {bounds!r}: {path}")
                item = dict(item)
                supplied_evidence = item.get("evidence")
                visual_note = (supplied_evidence.get("visual_note")
                               if isinstance(supplied_evidence, dict)
                               else supplied_evidence)
                item["evidence"] = {
                    "image": str(image),
                    "visual_note": visual_note,
                    "reviewer": "ZCode GLM-5.3-Flash/max",
                }
                items.append(item)
            normalized[collection] = items
        normalized["review_source"] = str(path.resolve())
        normalized["coordinate_evidence"] = str(image)
        level[level_id] = normalized
        source_files.append(str(path.resolve()))

    output = {
        "schema": "eai.semantic_map.visual_observations",
        "version": 1,
        "coordinate_convention": "top-view normalized u right, v down; world X right, Y up",
        "review_model": "GLM-5.3-Flash",
        "review_thought_level": "max",
        "scenes": scenes,
        "source_files": source_files,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"scenes": len(scenes),
                      "levels": sum(len(v["levels"]) for v in scenes.values()),
                      "output": str(args.output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
