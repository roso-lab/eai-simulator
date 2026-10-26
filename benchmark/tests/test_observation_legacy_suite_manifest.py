"""Static proof that the 926-line camera suite was mapped to the new host."""

from __future__ import annotations

import ast
import json
from pathlib import Path


def test_legacy_suite_mapping_has_no_unadapted_cases():
    root = Path(__file__).resolve().parents[2]
    manifest = json.loads(
        (root / "benchmark/observation_legacy_suite_manifest.v1.json").read_text())
    assert manifest["source"]["line_count"] == 926
    assert manifest["source"]["branch"] == "feature/benchmark-ondemand-vision"
    assert manifest["missing"] == []

    available: set[str] = set()
    for relpath in manifest["adapted_test_files"]:
        tree = ast.parse((root / relpath).read_text(encoding="utf-8"))
        available.update(
            node.name for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"))
    mapped = manifest["test_mapping"]
    assert set(mapped) == {row for row in mapped}
    assert all(row["test"] in available for row in mapped.values())
