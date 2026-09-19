"""Freeze leakage-safe scenario-pool splits and summarize matching evidence.

This is intentionally a protocol tool, not an evaluator or executor.  It
freezes a *within-scene* development/test split from a settled pool, keeps
paired conditions together, and additionally joins accidental duplicate
layouts.  Its summary only counts execution evidence whose input SHA-256 still
matches the frozen instance bytes.  Therefore a changed scenario cannot inherit
an old successful report.

The resulting split is not a held-out-scene benchmark: it is a reproducible
within-Factory family holdout that supports development discipline while the
project gains more scenes and a physical manipulation stack.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

if __package__ in (None, ""):
    import common
    from warehouse_benchmark import protocol_scope_metadata, validate_protocol_scope
else:
    from . import common
    from .warehouse_benchmark import protocol_scope_metadata, validate_protocol_scope

from demo.benchmark.provenance import execution_code_provenance, validate_execution_provenance


PROTOCOL_SCHEMA = 2
EXECUTION_TIERS = {
    "navigation_execution_validated",
    "kinematic_execution_validated",
}


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_sha256(data: Any) -> str:
    encoded = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load_pool(pool_dir: Path) -> dict[str, Any]:
    manifest = pool_dir / "pool.json"
    if not manifest.is_file():
        raise ValueError(f"pool manifest does not exist: {manifest}")
    data = json.loads(manifest.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("instances"), list):
        raise ValueError(f"invalid pool manifest: {manifest}")
    return data


def _safe_instance_path(pool_dir: Path, record: dict[str, Any]) -> Path:
    relative = record.get("file")
    if not isinstance(relative, str) or not relative:
        raise ValueError(f"record {record.get('id', '<unknown>')} has no instance file")
    root = pool_dir.resolve()
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"record {record.get('id', '<unknown>')} escapes pool directory") from exc
    if not candidate.is_file():
        raise ValueError(f"record {record.get('id', '<unknown>')} instance file is missing: {candidate}")
    return candidate


def _records_by_id(records: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for record in records:
        identifier = record.get("id")
        if not isinstance(identifier, str) or not identifier:
            raise ValueError("every pool record must have a non-empty string id")
        if identifier in indexed:
            raise ValueError(f"duplicate pool record id: {identifier}")
        indexed[identifier] = record
    return indexed


def connected_split_groups(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Join records by family and by layout signature.

    A family contains intentionally paired conditions.  A matching layout
    signature across families is an accidental duplicate until demonstrated
    otherwise, so it must not leak across a development/test boundary either.
    """
    indexed = _records_by_id(records)
    parent = {identifier: identifier for identifier in indexed}

    def find(identifier: str) -> str:
        while parent[identifier] != identifier:
            parent[identifier] = parent[parent[identifier]]
            identifier = parent[identifier]
        return identifier

    def union(left: str, right: str) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for field in ("family_id", "layout_signature"):
        first_by_value: dict[str, str] = {}
        for identifier, record in indexed.items():
            value = record.get(field)
            if value is None or value == "":
                continue
            key = str(value)
            previous = first_by_value.setdefault(key, identifier)
            union(previous, identifier)

    members_by_root: dict[str, list[str]] = defaultdict(list)
    for identifier in indexed:
        members_by_root[find(identifier)].append(identifier)

    groups: list[dict[str, Any]] = []
    for members in members_by_root.values():
        members = sorted(members)
        member_records = [indexed[identifier] for identifier in members]
        group_id = "group_" + hashlib.sha256("\0".join(members).encode("utf-8")).hexdigest()[:16]
        groups.append(
            {
                "id": group_id,
                "members": members,
                "family_ids": sorted(
                    {str(record["family_id"]) for record in member_records if record.get("family_id") not in (None, "")}
                ),
                "layout_signatures": sorted(
                    {str(record["layout_signature"]) for record in member_records if record.get("layout_signature") not in (None, "")}
                ),
            }
        )
    return sorted(groups, key=lambda item: item["id"])


def deterministic_split(groups: Iterable[dict[str, Any]], *, split_seed: int, test_fraction: float) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    groups = list(groups)
    if len(groups) < 2:
        raise ValueError("at least two independent family/layout groups are required for a development/test split")
    if not 0.0 < test_fraction < 1.0:
        raise ValueError("test_fraction must be strictly between 0 and 1")
    ranked = sorted(
        groups,
        key=lambda item: hashlib.sha256(f"{split_seed}\0{item['id']}".encode("utf-8")).hexdigest(),
    )
    test_count = max(1, min(len(ranked) - 1, math.ceil(len(ranked) * test_fraction)))
    return sorted(ranked[test_count:], key=lambda item: item["id"]), sorted(ranked[:test_count], key=lambda item: item["id"])


def _scope_for_pool(
    pool: dict[str, Any],
    scope: str | None = None,
    scene_key: str | None = None,
) -> str:
    """Resolve one protocol scope while rejecting mixed declarations."""
    pool_scene_key = pool.get("scene_key")
    if pool_scene_key not in (None, "") and scene_key not in (None, "") \
            and pool_scene_key != scene_key:
        raise ValueError(
            f"pool scene_key {pool_scene_key!r} conflicts with requested scene_key {scene_key!r}"
        )
    requested_scene_key = scene_key if scene_key not in (None, "") else pool_scene_key
    if scope is not None:
        metadata = protocol_scope_metadata(scope)
        if requested_scene_key not in (None, "") \
                and requested_scene_key not in metadata["scene_keys"]:
            raise ValueError(
                f"protocol scope {scope!r} cannot use scene_key {requested_scene_key!r}"
            )
        return scope
    return {
        "warehouse_nvd": "warehouse",
        "modern_house_nvd": "modern_house",
    }.get(requested_scene_key, "factory")


def _scene_key_for_scope(
    pool: dict[str, Any], scope: str, scene_key: str | None = None
) -> str:
    """Return the single scene key represented by a protocol scope."""
    requested = scene_key if scene_key not in (None, "") else pool.get("scene_key")
    allowed = protocol_scope_metadata(scope)["scene_keys"]
    if requested not in (None, ""):
        if requested not in allowed:
            raise ValueError(
                f"protocol scope {scope!r} cannot use scene_key {requested!r}"
            )
        return requested
    # All supported scopes currently represent one concrete scene.  This also
    # gives legacy Factory pools an explicit scene in newly frozen protocols.
    if len(allowed) != 1:
        raise ValueError(f"protocol scope {scope!r} has no unique scene_key")
    return allowed[0]


def build_protocol(
    pool_dir: Path,
    pool: dict[str, Any],
    *,
    split_seed: int,
    test_fraction: float,
    required_label: str | None = "spawn_validated",
    scope: str | None = None,
    scene_key: str | None = None,
) -> dict[str, Any]:
    records = list(pool["instances"])
    _records_by_id(records)
    effective_pool = pool
    if pool.get("scene_key") in (None, "") and scene_key in (None, ""):
        record_scene_keys = {
            str(record["scene_key"])
            for record in records
            if record.get("scene_key") not in (None, "")
        }
        if len(record_scene_keys) > 1:
            raise ValueError(
                "protocol pool records declare multiple scene_key values: "
                + ", ".join(sorted(record_scene_keys))
            )
        if record_scene_keys:
            effective_pool = {**pool, "scene_key": next(iter(record_scene_keys))}
    protocol_scope = _scope_for_pool(effective_pool, scope, scene_key)
    protocol_scene_key = _scene_key_for_scope(effective_pool, protocol_scope, scene_key)
    validate_protocol_scope(records, scope=protocol_scope, pool_scene_key=protocol_scene_key)
    eligible = [record for record in records if required_label is None or required_label in record.get("labels", [])]
    if not eligible:
        label_note = "all records" if required_label is None else f"records labelled {required_label!r}"
        raise ValueError(f"pool has no eligible {label_note}")

    groups = connected_split_groups(eligible)
    development_groups, test_groups = deterministic_split(
        groups, split_seed=split_seed, test_fraction=test_fraction
    )
    group_by_member = {
        member: group["id"]
        for group in groups
        for member in group["members"]
    }
    frozen_instances = []
    for record in sorted(eligible, key=lambda item: item["id"]):
        instance_path = _safe_instance_path(pool_dir, record)
        frozen_instances.append(
            {
                "id": record["id"],
                "group_id": group_by_member[record["id"]],
                "file": record["file"],
                "scenario_sha256": file_sha256(instance_path),
                "family_id": record.get("family_id"),
                "layout_signature": record.get("layout_signature"),
                "skeleton": record.get("skeleton", "unknown"),
                "robot_dossier_snapshot_sha256": record.get("robot_dossier_snapshot_sha256"),
                "payload_policy_hash": record.get("payload_policy_hash"),
                "condition": record.get("condition"),
            }
        )
    excluded = [
        {"id": record["id"], "reason": f"missing_required_label:{required_label}"}
        for record in sorted(records, key=lambda item: item["id"])
        if record not in eligible
    ]
    from EAI.scenario_spec.benchmark_physics import BENCHMARK_PHYSICS_CONTRACT
    return {
        "schema": PROTOCOL_SCHEMA,
        "benchmark_physics_contract": dict(BENCHMARK_PHYSICS_CONTRACT),
        "execution_sources": execution_code_provenance(),
        "frozen_at_utc": datetime.now(timezone.utc).isoformat(),
        "purpose": "fixed within-scene family/layout-held-out evaluation",
        "scope": protocol_scope_metadata(protocol_scope),
        "scene_key": protocol_scene_key,
        "scope_limitations": [
            f"All included instances use the same {protocol_scope} scene scope; this is not a held-out-scene generalization test.",
            "Paired conditions and matching layout signatures are kept in one split group to prevent leakage.",
            "Kinematic execution evidence is reported separately from navigation evidence and is not physical manipulation evidence.",
        ],
        "pool": {
            "name": pool.get("pool", pool_dir.name),
            "manifest_sha256_at_freeze": canonical_sha256(pool),
            "template": pool.get("template"),
            "eligible_required_label": required_label,
        },
        "split": {
            "unit": "connected components of family_id and layout_signature",
            "method": "sha256_ranked_groups",
            "seed": split_seed,
            "test_fraction_requested": test_fraction,
            "development_groups": development_groups,
            "test_groups": test_groups,
        },
        "instances": frozen_instances,
        "excluded": excluded,
        "aggregation_policy": {
            "execution_input_provenance": "latest pool execution report must carry the frozen scenario_sha256",
            "success_definition": "execution_validation.validated is true and tier is a supported execution tier",
            "tiers_are_reported_separately": True,
            "retries": "summary uses the latest matching attempt; execution_history retains earlier attempts",
            "test_evidence_cutoff": "test-split evidence must be attempted_at on or after frozen_at_utc",
        },
    }


def _empty_counts() -> dict[str, Any]:
    return {
        "frozen_instances": 0,
        "spawn_validated": 0,
        "current_input": 0,
        "changed_or_missing_input": 0,
        "attempted_with_matching_input": 0,
        "pre_freeze_execution": 0,
        "not_executable_by_current_harness": 0,
        "validated_execution": 0,
        "attempt_not_validated": 0,
        "unattempted": 0,
        "tiers": {},
    }


def _validated_tier(attempt: Any) -> str | None:
    if not isinstance(attempt, dict):
        return None
    report = attempt.get("report_body")
    validation = report.get("execution_validation") if isinstance(report, dict) else None
    if not isinstance(validation, dict) or validation.get("validated") is not True:
        return None
    tier = validation.get("tier")
    return tier if tier in EXECUTION_TIERS else None


def _finalize_counts(counts: dict[str, Any]) -> dict[str, Any]:
    frozen = counts["frozen_instances"]
    attempted = counts["attempted_with_matching_input"]
    executable = frozen - counts["changed_or_missing_input"] - counts["not_executable_by_current_harness"]
    result = dict(counts)
    result["execution_coverage_of_frozen"] = attempted / frozen if frozen else None
    result["execution_coverage_of_executable_instances"] = attempted / executable if executable else None
    result["validated_rate_among_matching_attempts"] = (
        counts["validated_execution"] / attempted if attempted else None
    )
    result["complete_matching_execution"] = (
        frozen > 0
        and counts["changed_or_missing_input"] == 0
        and counts["not_executable_by_current_harness"] == 0
        and counts["validated_execution"] == frozen
    )
    return result


def _attempt_is_on_or_after(attempt: dict[str, Any], cutoff: str) -> bool:
    """Return false for missing/malformed timestamps rather than guessing."""
    attempted_at = attempt.get("attempted_at")
    if not isinstance(attempted_at, str) or not isinstance(cutoff, str):
        return False
    try:
        attempt_time = datetime.fromisoformat(attempted_at.replace("Z", "+00:00"))
        cutoff_time = datetime.fromisoformat(cutoff.replace("Z", "+00:00"))
    except ValueError:
        return False
    if attempt_time.tzinfo is None or cutoff_time.tzinfo is None:
        return False
    return attempt_time >= cutoff_time


def summarize_protocol(
    pool_dir: Path,
    pool: dict[str, Any],
    protocol: dict[str, Any],
    *,
    split_name: str,
    scope: str | None = None,
    scene_key: str | None = None,
) -> dict[str, Any]:
    if protocol.get("schema") != PROTOCOL_SCHEMA:
        raise ValueError(f"unsupported protocol schema: {protocol.get('schema')!r}")
    if split_name not in {"development", "test", "all"}:
        raise ValueError(f"unsupported split name: {split_name}")
    split = protocol.get("split")
    frozen = protocol.get("instances")
    if not isinstance(split, dict) or not isinstance(frozen, list):
        raise ValueError("invalid benchmark protocol")
    declared_scope = protocol.get("scope")
    if isinstance(declared_scope, dict):
        declared_name = declared_scope.get("name")
        if not isinstance(declared_name, str):
            raise ValueError("benchmark protocol has no valid scope")
        if scope is not None and scope != declared_name:
            raise ValueError(
                f"requested scope {scope!r} conflicts with protocol scope {declared_name!r}"
            )
        protocol_scope = _scope_for_pool(pool, declared_name, scene_key or protocol.get("scene_key"))
    else:
        protocol_scope = _scope_for_pool(pool, scope, scene_key or protocol.get("scene_key"))
    protocol_scene_key = _scene_key_for_scope(
        pool, protocol_scope, scene_key or protocol.get("scene_key")
    )
    validate_protocol_scope(
        pool["instances"], scope=protocol_scope, pool_scene_key=protocol_scene_key
    )
    group_key = "development_groups" if split_name == "development" else "test_groups"
    selected_groups = (
        {group["id"] for group in split["development_groups"] + split["test_groups"]}
        if split_name == "all"
        else {group["id"] for group in split.get(group_key, [])}
    )
    test_group_ids = {group["id"] for group in split.get("test_groups", [])}
    frozen_at = protocol.get("frozen_at_utc")
    if not isinstance(frozen_at, str):
        raise ValueError("protocol lacks frozen_at_utc; create a new protocol instead of reusing it")
    frozen_records = [record for record in frozen if record.get("group_id") in selected_groups]
    current_records = _records_by_id(pool["instances"])
    total = _empty_counts()
    by_skeleton: dict[str, dict[str, Any]] = {}

    for frozen_record in frozen_records:
        skeleton = str(frozen_record.get("skeleton", "unknown"))
        counts = by_skeleton.setdefault(skeleton, _empty_counts())
        for bucket in (total, counts):
            bucket["frozen_instances"] += 1
        current = current_records.get(frozen_record["id"])
        instance_path: Path | None = None
        if current is not None:
            try:
                instance_path = _safe_instance_path(pool_dir, frozen_record)
            except ValueError:
                instance_path = None
        input_matches = instance_path is not None and file_sha256(instance_path) == frozen_record["scenario_sha256"]
        if not input_matches:
            for bucket in (total, counts):
                bucket["changed_or_missing_input"] += 1
            continue
        for bucket in (total, counts):
            bucket["current_input"] += 1
            if current is not None and "spawn_validated" in current.get("labels", []):
                bucket["spawn_validated"] += 1

        attempt = current.get("execution") if current is not None else None
        if not isinstance(attempt, dict) or attempt.get("scenario_sha256") != frozen_record["scenario_sha256"]:
            for bucket in (total, counts):
                bucket["unattempted"] += 1
            continue
        if frozen_record["group_id"] in test_group_ids and not _attempt_is_on_or_after(attempt, frozen_at):
            for bucket in (total, counts):
                bucket["pre_freeze_execution"] += 1
                bucket["unattempted"] += 1
            continue
        if attempt.get("status") == "not_executable":
            for bucket in (total, counts):
                bucket["not_executable_by_current_harness"] += 1
            continue
        tier = _validated_tier(attempt)
        try:
            if not isinstance(protocol.get("execution_sources"), dict):
                raise ValueError("legacy protocol lacks frozen execution sources")
            validate_execution_provenance(attempt, expected_sources=protocol["execution_sources"])
            if attempt.get("status") != "completed" or attempt.get("outcome", {}).get("returncode") != 0:
                raise ValueError("execution did not finish successfully")
            for key in ("robot_dossier_snapshot_sha256", "payload_policy_hash"):
                if attempt.get(key) != frozen_record.get(key):
                    raise ValueError(f"execution differs from frozen {key}")
        except (ValueError, TypeError):
            tier = None
        if protocol.get("benchmark_physics_contract") is not None:
            from EAI.scenario_spec.benchmark_physics import BENCHMARK_PHYSICS_CONTRACT, has_current_mass_audit
            if protocol["benchmark_physics_contract"] != BENCHMARK_PHYSICS_CONTRACT:
                raise ValueError("unsupported frozen benchmark physics contract")
            if not has_current_mass_audit(attempt.get("report_body")):
                tier = None
        for bucket in (total, counts):
            bucket["attempted_with_matching_input"] += 1
            if tier is None:
                bucket["attempt_not_validated"] += 1
            else:
                bucket["validated_execution"] += 1
                bucket["tiers"][tier] = bucket["tiers"].get(tier, 0) + 1

    return {
        "schema": PROTOCOL_SCHEMA,
        "protocol_pool_manifest_sha256": protocol.get("pool", {}).get("manifest_sha256_at_freeze"),
        "frozen_at_utc": frozen_at,
        "current_pool_manifest_sha256": canonical_sha256(pool),
        "split": split_name,
        "summary": _finalize_counts(total),
        "by_skeleton": {key: _finalize_counts(value) for key, value in sorted(by_skeleton.items())},
        "interpretation": [
            "A valid execution is counted only when its frozen input SHA-256 matches the current scenario bytes.",
            "Pre-launch sources, requested conditions, runtime snapshot and report digest must agree; legacy unbound evidence is excluded.",
            "For test groups, attempts dated before frozen_at_utc are retained as audit history but excluded from results.",
            "Validation tiers remain separate; kinematic execution must not be interpreted as physical manipulation success.",
            "This report is a within-scene protocol audit, not a cross-scene benchmark score.",
        ],
    }


def _write_new_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    except FileExistsError as exc:
        raise ValueError(f"refusing to overwrite existing frozen protocol: {path}") from exc


def _write_report(path: Path | None, data: dict[str, Any]) -> None:
    rendered = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    if path is None:
        print(rendered, end="")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(rendered, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    freeze = subparsers.add_parser("freeze", help="create a non-overwriting fixed split manifest")
    freeze.add_argument("--pool", required=True, help="pool directory containing pool.json")
    freeze.add_argument("--output", required=True, help="new protocol JSON path; must not already exist")
    freeze.add_argument("--split-seed", type=int, required=True, help="integer used to rank leakage-safe groups")
    freeze.add_argument("--test-fraction", type=float, default=0.25)
    freeze.add_argument("--include-unsettled", action="store_true", help="include records without spawn_validated")
    freeze.add_argument("--scope", choices=["factory", "warehouse", "modern_house"],
                        help="protocol scope; inferred from pool.scene_key when omitted")
    freeze.add_argument("--scene-key", choices=["factory", "warehouse_nvd", "modern_house_nvd"],
                        help="concrete scene key; must belong to --scope")
    summary = subparsers.add_parser("summarize", help="summarize current matching execution evidence")
    summary.add_argument("--pool", required=True)
    summary.add_argument("--protocol", required=True)
    summary.add_argument("--split", choices=["development", "test", "all"], default="test")
    summary.add_argument("--output", help="write a replaceable derived report instead of stdout")
    summary.add_argument("--scope", choices=["factory", "warehouse", "modern_house"],
                         help="protocol scope; inferred from the frozen protocol when omitted")
    summary.add_argument("--scene-key", choices=["factory", "warehouse_nvd", "modern_house_nvd"],
                         help="concrete scene key; must belong to the protocol scope")
    summary.add_argument("--fail-on-incomplete", action="store_true")
    ids = subparsers.add_parser("ids", help="print frozen instance IDs for a protocol split")
    ids.add_argument("--protocol", required=True)
    ids.add_argument("--split", choices=["development", "test", "all"], required=True)

    args = parser.parse_args(argv)
    if args.command == "freeze":
        pool_dir = Path(args.pool).resolve()
        pool = load_pool(pool_dir)
        protocol = build_protocol(
            pool_dir,
            pool,
            split_seed=args.split_seed,
            test_fraction=args.test_fraction,
            required_label=None if args.include_unsettled else "spawn_validated",
            scope=args.scope,
            scene_key=args.scene_key,
        )
        _write_new_json(Path(args.output).resolve(), protocol)
        print(
            f"[protocol] frozen={len(protocol['instances'])} "
            f"development_groups={len(protocol['split']['development_groups'])} "
            f"test_groups={len(protocol['split']['test_groups'])} output={Path(args.output).resolve()}"
        )
        return 0
    if args.command == "ids":
        protocol = json.loads(Path(args.protocol).read_text(encoding="utf-8"))
        split = protocol["split"]
        selected_groups = (
            {group["id"] for group in split["development_groups"] + split["test_groups"]}
            if args.split == "all"
            else {group["id"] for group in split[f"{args.split}_groups"]}
        )
        for record in protocol["instances"]:
            if record["group_id"] in selected_groups:
                print(record["id"])
        return 0

    pool_dir = Path(args.pool).resolve()
    pool = load_pool(pool_dir)
    protocol = json.loads(Path(args.protocol).read_text(encoding="utf-8"))
    report = summarize_protocol(
        pool_dir, pool, protocol, split_name=args.split,
        scope=args.scope, scene_key=args.scene_key,
    )
    _write_report(Path(args.output).resolve() if args.output else None, report)
    return 1 if args.fail_on_incomplete and not report["summary"]["complete_matching_execution"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
