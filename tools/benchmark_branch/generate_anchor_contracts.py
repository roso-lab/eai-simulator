"""Regenerate the author-approved 24-anchor contract projection."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from .pilot_contract import build_author_approved_draft_contract_set, build_pilot_contract_set, validate_pilot_contract_set


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _project_input(path: Path, root: Path, name: str) -> tuple[Path, str]:
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"{name} does not exist: {path}") from exc
    if not resolved.is_file() or not resolved.is_relative_to(root):
        raise ValueError(f"{name} must resolve to a regular file inside project root")
    return resolved, resolved.relative_to(root).as_posix()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--decision-sheet", type=Path, required=True)
    parser.add_argument("--additional-binding-source", type=Path, action="append", default=[])
    parser.add_argument("--pilot-candidates", type=Path)
    parser.add_argument("--paper-sha", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    args = parser.parse_args(argv)
    root = args.project_root.resolve(strict=True)
    catalog_path, catalog_relative = _project_input(args.catalog, root, "catalog")
    decision_path, decision_relative = _project_input(
        args.decision_sheet, root, "decision sheet"
    )
    additional_sources = [
        _project_input(path, root, "additional binding source")
        for path in args.additional_binding_source
    ]
    pilot_source = (
        None
        if args.pilot_candidates is None
        else _project_input(args.pilot_candidates, root, "pilot candidates")
    )
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    result = build_author_approved_draft_contract_set(
        catalog,
        paper_sha=args.paper_sha,
        source_catalog=catalog_relative,
    )
    result["source_catalog_sha256"] = _sha256(catalog_path)
    result["binding_decision_sha256"] = _sha256(decision_path)
    result["binding_sources"] = [
        {"path": relative, "sha256": _sha256(path)}
        for path, relative in [(decision_path, decision_relative), *additional_sources]
    ]
    if pilot_source is not None:
        pilot_path, _pilot_relative = pilot_source
        candidates = json.loads(pilot_path.read_text(encoding="utf-8"))
        # The old file supplies selection only. This is a new projection at
        # the explicitly supplied paper identity, not a rewrite of its release.
        candidates["paper_sha"] = args.paper_sha
        candidates["source_catalog"] = catalog_relative
        pilots = build_pilot_contract_set(candidates, catalog)
        result["contracts"].extend(pilots["contracts"])
        result["candidate_count"] = len(result["contracts"])
        result["pilot_selection_sha256"] = _sha256(pilot_path)
        result["count_scope"] = "draft/operator counts exclude six historical pilots"
    validate_pilot_contract_set(result)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
