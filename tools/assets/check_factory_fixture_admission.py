#!/usr/bin/env python
"""Read-only admission check for the frozen Factory evaluator USD.

The evaluator fixture binds an exact USD SHA256.  This command never copies,
rewrites, or replaces an asset; it reports every supplied candidate so an
asset-version mismatch remains an infrastructure finding rather than a model
failure.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_factory_fixture_admission(
    asset_path: str | Path,
    fixture_path: str | Path,
    candidates: Iterable[str | Path] = (),
) -> dict[str, Any]:
    """Return a JSON-ready, non-mutating admission report."""
    asset = Path(asset_path).expanduser().resolve()
    fixture = Path(fixture_path).expanduser().resolve()
    try:
        payload = json.loads(fixture.read_text(encoding="utf-8"))
        expected = payload["source_hashes"]["usd_sha256"]
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        return {"status": "invalid", "reason": f"fixture_invalid:{type(exc).__name__}",
                "asset": str(asset), "fixture": str(fixture),
                "expected_sha256": None, "candidates": []}
    if not isinstance(expected, str) or len(expected) != 64:
        return {"status": "invalid", "reason": "fixture_usd_hash_invalid",
                "asset": str(asset), "fixture": str(fixture),
                "expected_sha256": expected, "candidates": []}

    paths = []
    for value in (asset, *[Path(item).expanduser().resolve() for item in candidates]):
        if value not in paths:
            paths.append(value)
    rows = []
    for path in paths:
        row: dict[str, Any] = {"path": str(path), "exists": path.is_file(),
                               "sha256": None, "matches_fixture": False}
        if path.is_file():
            try:
                row["sha256"] = _sha256(path)
                row["matches_fixture"] = row["sha256"] == expected
                row["bytes"] = path.stat().st_size
            except OSError as exc:
                row["read_error"] = f"{type(exc).__name__}"
        rows.append(row)
    matches = [row for row in rows if row["matches_fixture"]]
    actual = next((row["sha256"] for row in rows if row["path"] == str(asset)), None)
    if matches:
        status, reason = "ready", "matching_frozen_asset_found"
    elif actual is None:
        status, reason = "blocked", "factory_usd_missing"
    else:
        status, reason = "blocked", "factory_usd_hash_mismatch"
    return {"status": status, "reason": reason, "asset": str(asset),
            "fixture": str(fixture), "expected_sha256": expected,
            "actual_sha256": actual, "matching_candidates": [row["path"] for row in matches],
            "candidates": rows, "mutated": False,
            "execution_validated": False, "business_outcomes_validated": False,
            "scoreable": False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--asset", type=Path, required=True)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, action="append", default=[])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = inspect_factory_fixture_admission(args.asset, args.fixture, args.candidate)
    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0 if report["status"] == "ready" else 2


if __name__ == "__main__":
    raise SystemExit(main())

