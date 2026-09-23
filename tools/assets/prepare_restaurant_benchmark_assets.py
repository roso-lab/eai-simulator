#!/usr/bin/env python
"""Prepare or verify the frozen Restaurant candidate without changing it.

The v7 bundle is an immutable local candidate.  This command only copies the
four hash-bound files byte-for-byte and runs the existing fail-closed resolver;
it never regenerates the overlay or changes resolver constants.  It also checks
that the overlay's relative source reference resolves on the current machine,
which catches the known clean-worktree path-closure failure before Isaac starts.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import hashlib
from pathlib import Path
from typing import Any


PRODUCT_FILES = (
    "restaurant_benchmark_bench.usda",
    "restaurant_benchmark_map.png",
    "restaurant_benchmark_map.yaml",
    "bench_meta.json",
)
_REFERENCE = re.compile(r"@([^@]+Restaurant_Only\.usda)@")


def _source_reference(overlay: Path) -> Path:
    text = overlay.read_text(encoding="utf-8")
    match = _REFERENCE.search(text)
    if match is None:
        raise RuntimeError("overlay has no Restaurant_Only.usda source reference")
    return (overlay.parent / match.group(1)).resolve()


def inspect_candidate(root: str | Path) -> dict[str, Any]:
    candidate = Path(root).expanduser().resolve()
    missing = [name for name in PRODUCT_FILES if not (candidate / name).is_file()]
    if missing:
        return {"status": "failed", "reason": "candidate_files_missing",
                "root": str(candidate), "missing": missing}
    source = _source_reference(candidate / PRODUCT_FILES[0])
    result: dict[str, Any] = {
        "root": str(candidate),
        "overlay_source_reference": str(source),
        "source_closure_exists": source.is_file(),
        "hash_resolver": "not_run",
    }
    try:
        from EAI_assets.restaurant_benchmark_products import resolve_restaurant_benchmark_products

        resolve_restaurant_benchmark_products(candidate)
    except Exception as exc:  # the report must preserve the exact gate failure
        result.update(status="failed", hash_resolver="failed",
                      reason=f"{type(exc).__name__}: {exc}")
        if not source.is_file():
            result["reason"] = "source_closure_missing"
        return result
    result.update(status="ready" if source.is_file() else "failed",
                  hash_resolver="passed")
    if not source.is_file():
        result["reason"] = "source_closure_missing"
    return result


def materialize_source_closure(candidate: str | Path, source_usd: str | Path) -> dict[str, Any]:
    """Place the explicitly supplied source at the frozen overlay reference.

    The overlay bytes and product hashes are never edited. Existing destination
    bytes must match exactly; this prevents a clean host from silently binding a
    different scene source under the same frozen candidate.
    """
    candidate_root = Path(candidate).expanduser().resolve()
    source = Path(source_usd).expanduser().resolve()
    overlay = candidate_root / PRODUCT_FILES[0]
    if not source.is_file():
        raise RuntimeError(f"source USD does not exist: {source}")
    destination = _source_reference(overlay)
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    if destination.exists():
        if not destination.is_file():
            raise RuntimeError(f"source closure destination is not a file: {destination}")
        destination_hash = hashlib.sha256(destination.read_bytes()).hexdigest()
        if destination_hash != source_hash:
            raise RuntimeError(
                f"source closure bytes differ at {destination}: "
                f"expected {source_hash}, found {destination_hash}")
        copied = False
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        copied = True
    return {"source": str(source), "destination": str(destination),
            "sha256": source_hash, "copied": copied}


def copy_candidate(source: str | Path, target: str | Path,
                   *, source_usd: str | Path | None = None) -> dict[str, Any]:
    source_root = Path(source).expanduser().resolve()
    target_root = Path(target).expanduser().resolve()
    if source_usd is None:
        source_report = inspect_candidate(source_root)
        # Copying a candidate with an unresolved source would create a misleading
        # apparently-ready directory, so require source closure before copying.
        if source_report.get("status") != "ready":
            raise RuntimeError(json.dumps(source_report, sort_keys=True))
    else:
        # A frozen overlay may resolve relative to the eventual target layout,
        # so validate its four hash-bound files before copying and materialize
        # the reviewed source only after the target path exists.
        from EAI_assets.restaurant_benchmark_products import resolve_restaurant_benchmark_products
        resolve_restaurant_benchmark_products(source_root)
    target_root.mkdir(parents=True, exist_ok=True)
    for name in PRODUCT_FILES:
        shutil.copyfile(source_root / name, target_root / name)
    closure = None
    if source_usd is not None:
        closure = materialize_source_closure(target_root, source_usd)
    result = inspect_candidate(target_root)
    if result.get("status") != "ready":
        raise RuntimeError(json.dumps(result, sort_keys=True))
    if closure is not None:
        result["source_closure_materialized"] = closure
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-root", type=Path, required=True)
    parser.add_argument("--copy-to", type=Path)
    parser.add_argument(
        "--source-usd", type=Path,
        help="explicit Restaurant_Only.usda used to materialize the frozen overlay reference",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.source_usd is not None and args.copy_to is None:
        closure = materialize_source_closure(args.candidate_root, args.source_usd)
    else:
        closure = None
    result = (copy_candidate(args.candidate_root, args.copy_to, source_usd=args.source_usd)
              if args.copy_to is not None
              else inspect_candidate(args.candidate_root))
    if closure is not None:
        result["source_closure_materialized"] = closure
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("status") == "ready" else 2


if __name__ == "__main__":
    raise SystemExit(main())
