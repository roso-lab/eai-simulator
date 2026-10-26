"""Regenerate the author-approved 24-anchor contract projection."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from .pilot_contract import build_author_approved_draft_contract_set, build_pilot_contract_set, validate_pilot_contract_set


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--decision-sheet", type=Path, required=True)
    parser.add_argument("--additional-binding-source", type=Path, action="append", default=[])
    parser.add_argument("--pilot-candidates", type=Path)
    parser.add_argument("--paper-sha", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    result = build_author_approved_draft_contract_set(
        catalog,
        paper_sha=args.paper_sha,
        source_catalog=str(args.catalog.resolve()),
    )
    result["source_catalog_sha256"] = _sha256(args.catalog)
    result["binding_decision_sha256"] = _sha256(args.decision_sheet)
    result["binding_sources"] = [
        {"path": str(path.resolve()), "sha256": _sha256(path)}
        for path in [args.decision_sheet, *args.additional_binding_source]
    ]
    if args.pilot_candidates is not None:
        candidates = json.loads(args.pilot_candidates.read_text(encoding="utf-8"))
        # The old file supplies selection only. This is a new projection at
        # the explicitly supplied paper identity, not a rewrite of its release.
        candidates["paper_sha"] = args.paper_sha
        candidates["source_catalog"] = str(args.catalog.resolve())
        pilots = build_pilot_contract_set(candidates, catalog)
        result["contracts"].extend(pilots["contracts"])
        result["candidate_count"] = len(result["contracts"])
        result["pilot_selection_sha256"] = _sha256(args.pilot_candidates)
        result["count_scope"] = "draft/operator counts exclude six historical pilots"
    validate_pilot_contract_set(result)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
