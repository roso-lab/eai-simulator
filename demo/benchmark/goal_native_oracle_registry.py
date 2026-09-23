"""Fail-closed completeness audit for the five native remaining-task oracles.

This module is an evaluator-side release gate.  It does not create oracle
registrations, infer a feasibility label, or inspect a participant plan.  A
registration and its trusted-state contract are supplied by the evaluator;
the audit only verifies that every frozen measurement point has a verified
binary result with native evidence and that all identities remain bound.

``unverified`` is deliberately treated as an evidence failure.  It is never
returned as a task truth and cannot contribute to a release-ready report.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy
from typing import Any

from .provenance import canonical_sha256


NATIVE_ORACLE_REGISTRY_SCHEMA = "eai.benchmark.native_oracle_registry_audit"
NATIVE_ORACLE_REGISTRY_VERSION = 1
NATIVE_REMAINING_TASK_SCOPE = "native_remaining_task"
EXPECTED_SCENES = (
    "factory",
    "warehouse_nvd",
    "modern_house_nvd",
    "city",
    "restaurant",
)
_BINARY_LABELS = {"deployable", "not_deployable"}
_HEX = frozenset("0123456789abcdef")


def _is_nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and not (set(value) - _HEX)


def _digest(record: Mapping[str, Any], field: str) -> str | None:
    if not isinstance(record, Mapping):
        return None
    value = record.get(field)
    if not _is_sha256(value):
        return None
    frozen = {key: deepcopy(item) for key, item in record.items() if key != field}
    try:
        return canonical_sha256(frozen)
    except (TypeError, ValueError):
        return None


def _as_scene_map(value: Any, *, component: str) -> dict[str, Any]:
    """Normalize a list or scene-keyed map without hiding duplicate records."""
    if value is None:
        return {}
    if isinstance(value, Mapping):
        # A bundled entry is accepted as one record; otherwise maps are keyed
        # by scene.  The caller still checks the record's own scene identity.
        if any(key in value for key in ("registration", "state_contract", "trusted_state_contract",
                                        "release_review")):
            scene = value.get("scene_key")
            return {scene: value} if _is_nonempty_string(scene) else {"": value}
        return {str(key): item for key, item in value.items()}
    if not isinstance(value, Iterable) or isinstance(value, (str, bytes)):
        return {"": {"__invalid_component__": component, "value": value}}
    result: dict[str, Any] = {}
    for item in value:
        if not isinstance(item, Mapping):
            result.setdefault("", []).append(item)
            continue
        scene = item.get("scene_key")
        if not _is_nonempty_string(scene):
            result.setdefault("", []).append(item)
            continue
        if scene in result:
            # Keep both rows so the duplicate is visible to the report.
            previous = result[scene]
            if isinstance(previous, list):
                previous.append(item)
            else:
                result[scene] = [previous, item]
        else:
            result[scene] = item
    return result


def _component_for_scene(index: Mapping[str, Any], scene: str) -> tuple[Any, list[str]]:
    value = index.get(scene)
    if isinstance(value, list):
        return value[0] if value else None, ["duplicate_scene_component"] if len(value) > 1 else []
    return value, []


def _entry_components(entry: Any) -> tuple[Any, Any, Any]:
    if not isinstance(entry, Mapping):
        return None, None, None
    registration = entry.get("registration")
    contract = entry.get("state_contract", entry.get("trusted_state_contract"))
    review = entry.get("release_review")
    # A direct registration is useful for callers that pass separate component
    # maps and harmless for bundled entries.
    if registration is None and entry.get("schema") == "eai.benchmark.remaining_feasibility_registration":
        registration = entry
    return registration, contract, review


def _hash_from_review(review: Mapping[str, Any], name: str) -> Any:
    if name in review:
        return review.get(name)
    aliases = {
        "task_sha256": ("public_task_sha256",),
        "registration_sha256": (),
        "contract_sha256": ("state_contract_sha256", "trusted_state_contract_sha256"),
    }
    for alias in aliases.get(name, ()):
        if alias in review:
            return review.get(alias)
    nested = review.get("registration") if name == "registration_sha256" else None
    if isinstance(nested, Mapping):
        return nested.get(name)
    nested = review.get("state_contract") if name == "contract_sha256" else None
    if isinstance(nested, Mapping):
        return nested.get(name)
    return None


def _review_rows(review: Mapping[str, Any]) -> Any:
    for key in ("results", "measurement_results", "measurements", "oracle_results"):
        if key in review:
            return review[key]
    return None


def _evidence_refs(value: Any) -> bool:
    return (isinstance(value, list) and bool(value)
            and all(_is_nonempty_string(item) for item in value)
            and len(set(value)) == len(value))


def _failure(kind: str, scene: str | None, reason: str, **extra: Any) -> dict[str, Any]:
    row = {"kind": kind, "scene_key": scene, "reason": reason}
    row.update(extra)
    return row


def _audit_scene(scene: str, registration: Any, contract: Any, review: Any) -> dict[str, Any]:
    invalid: list[str] = []
    failed: list[str] = []
    missing: list[str] = []
    duplicate_measurements: list[str] = []
    result: dict[str, Any] = {
        "scene_key": scene,
        "status": "invalid",
        "missing_measurement_ids": missing,
        "failed_measurement_ids": failed,
        "duplicate_measurement_ids": duplicate_measurements,
        "invalid": invalid,
        "failed": failed,
    }

    if not isinstance(registration, Mapping):
        invalid.append("missing_registration")
        result["status"] = "invalid"
        return result
    if not isinstance(contract, Mapping):
        invalid.append("missing_state_contract")
    if not isinstance(review, Mapping):
        failed.append("missing_release_review")

    registration_scene = registration.get("scene_key")
    if registration_scene is not None and registration_scene != scene:
        invalid.append("registration_scene_mismatch")
    if registration.get("visibility") != "evaluator_only":
        invalid.append("registration_not_evaluator_only")
    if registration.get("schema") != "eai.benchmark.remaining_feasibility_registration":
        invalid.append("invalid_registration_schema")
    if not isinstance(registration.get("task_sha256"), str) or not registration["task_sha256"].strip():
        invalid.append("missing_task_hash")
    if registration.get("registration_sha256") != _digest(registration, "registration_sha256"):
        invalid.append("registration_hash_mismatch")

    validation = registration.get("validation")
    if not isinstance(validation, Mapping):
        invalid.append("missing_registration_validation")
    else:
        if validation.get("status") != "verified":
            invalid.append("registration_validation_not_verified")
        if validation.get("scope") != NATIVE_REMAINING_TASK_SCOPE:
            invalid.append("registration_validation_scope_mismatch")
        if not _evidence_refs(validation.get("evidence_refs")):
            invalid.append("empty_registration_evidence")

    points = registration.get("measurement_points")
    point_ids: list[str] = []
    if not isinstance(points, list) or not points:
        invalid.append("missing_measurement_points")
    else:
        for point in points:
            point_id = point.get("id") if isinstance(point, Mapping) else None
            if not _is_nonempty_string(point_id):
                invalid.append("invalid_measurement_id")
                continue
            point_ids.append(point_id)
        duplicates = sorted({item for item in point_ids if point_ids.count(item) > 1})
        if duplicates:
            duplicate_measurements.extend(duplicates)
            invalid.append("duplicate_measurement_points")

    if isinstance(contract, Mapping):
        if contract.get("visibility") != "evaluator_only":
            invalid.append("state_contract_not_evaluator_only")
        if contract.get("schema") != "eai.benchmark.trusted_state_contract":
            invalid.append("invalid_state_contract_schema")
        if contract.get("scene_key") != scene:
            invalid.append("state_contract_scene_mismatch")
        if contract.get("contract_sha256") != _digest(contract, "contract_sha256"):
            invalid.append("contract_hash_mismatch")
        if contract.get("registration_sha256") != registration.get("registration_sha256"):
            invalid.append("contract_registration_hash_mismatch")
        if contract.get("task_sha256") != registration.get("task_sha256"):
            invalid.append("contract_task_hash_mismatch")
        for flag in ("execution_validated", "business_outcomes_validated", "scoreable"):
            if contract.get(flag) is not False:
                invalid.append(f"contract_release_flag_not_false:{flag}")

    if isinstance(review, Mapping):
        if review.get("task_sha256") is not None and review.get("task_sha256") != registration.get("task_sha256"):
            invalid.append("review_task_hash_mismatch")
        if _hash_from_review(review, "task_sha256") != registration.get("task_sha256"):
            invalid.append("review_task_hash_missing_or_mismatch")
        if _hash_from_review(review, "registration_sha256") != registration.get("registration_sha256"):
            invalid.append("review_registration_hash_missing_or_mismatch")
        if isinstance(contract, Mapping) and _hash_from_review(review, "contract_sha256") != contract.get("contract_sha256"):
            invalid.append("review_contract_hash_missing_or_mismatch")
        if review.get("oracle_release_ready") is not True:
            failed.append("release_review_not_ready")
        for key in ("missing_measurement_ids", "failed_measurement_ids"):
            summary = review.get(key)
            if summary is not None:
                if not isinstance(summary, list) or any(not _is_nonempty_string(item) for item in summary):
                    invalid.append(f"invalid_review_{key}")
                elif summary:
                    failed.append(f"review_{key}")

        rows = _review_rows(review)
        if not isinstance(rows, list) or not rows:
            failed.append("missing_measurement_results")
        else:
            seen: set[str] = set()
            for row in rows:
                measurement_id = row.get("measurement_id") if isinstance(row, Mapping) else None
                if not _is_nonempty_string(measurement_id):
                    invalid.append("measurement_result_missing_id")
                    continue
                if measurement_id in seen:
                    duplicate_measurements.append(measurement_id)
                    invalid.append("duplicate_measurement_results")
                    continue
                seen.add(measurement_id)
                if measurement_id not in point_ids:
                    invalid.append(f"unregistered_measurement:{measurement_id}")
                    continue
                label = row.get("feasibility_label")
                status = row.get("oracle_status")
                scope = row.get("validation_scope")
                if status != "verified":
                    failed.append(measurement_id)
                    # ``failed`` and ``unverified`` are platform evidence
                    # states.  Keep them out of structural-invalid so the
                    # report distinguishes an absent/failed reader from a
                    # malformed third task truth.
                    if label == "unverified":
                        failed.append(f"unverified_measurement:{measurement_id}")
                elif label not in _BINARY_LABELS:
                    failed.append(measurement_id)
                    invalid.append(f"non_binary_measurement:{measurement_id}")
                elif scope != NATIVE_REMAINING_TASK_SCOPE:
                    failed.append(measurement_id)
                    invalid.append(f"measurement_scope_mismatch:{measurement_id}")
                if not _evidence_refs(row.get("evidence_refs")):
                    failed.append(measurement_id)
                    invalid.append(f"empty_measurement_evidence:{measurement_id}")
                if row.get("registration_sha256") not in (None, registration.get("registration_sha256")):
                    invalid.append(f"measurement_registration_hash_mismatch:{measurement_id}")
                if isinstance(contract, Mapping) and row.get("contract_sha256") not in (None, contract.get("contract_sha256")):
                    invalid.append(f"measurement_contract_hash_mismatch:{measurement_id}")
            missing.extend(sorted(set(point_ids) - seen))
            if missing:
                failed.append("missing_measurement_results")

    # Preserve stable ordering and do not leak duplicate failure entries.
    result["missing_measurement_ids"] = sorted(set(missing))
    result["failed_measurement_ids"] = sorted(set(item for item in failed if item in point_ids))
    result["failed"] = sorted(set(failed))
    result["invalid"] = sorted(set(invalid))
    if duplicate_measurements:
        result["duplicate_measurement_ids"] = sorted(set(duplicate_measurements))
    result["status"] = "verified" if not invalid and not failed and not missing else (
        "invalid" if invalid else "failed")
    return result


def audit_native_oracle_registry(
    registrations: Any,
    state_contracts: Any = None,
    release_reviews: Any = None,
    *,
    expected_scenes: Iterable[str] = EXPECTED_SCENES,
) -> dict[str, Any]:
    """Audit five-scene native oracle completeness and return JSON-ready data.

    Inputs may be three scene-keyed maps/lists or a single list of bundled
    records with ``registration``, ``state_contract`` and ``release_review``.
    Malformed inputs produce a failed report rather than an exception.  The
    report is evaluator-only and always keeps release flags false.
    """
    required = tuple(expected_scenes)
    expected = list(dict.fromkeys(required))
    report: dict[str, Any] = {
        "schema": NATIVE_ORACLE_REGISTRY_SCHEMA,
        "version": NATIVE_ORACLE_REGISTRY_VERSION,
        "visibility": "evaluator_only",
        "status": "failed",
        "expected_scenes": expected,
        "covered_scenes": [],
        "missing_scenes": [],
        "incomplete_scenes": [],
        "duplicate_scenes": [],
        "duplicate_measurements": [],
        "scenes": {},
        "invalid": [],
        "failed": [],
        "oracle_release_ready": False,
        "execution_validated": False,
        "business_outcomes_validated": False,
        "scoreable": False,
    }
    if len(expected) != len(required) or any(not _is_nonempty_string(scene) for scene in required):
        report["invalid"].append(_failure("registry", None, "invalid_expected_scenes"))
        report["status"] = "invalid"
        return report

    if state_contracts is None and release_reviews is None:
        bundled = _as_scene_map(registrations, component="bundled")
        reg_map: dict[str, Any] = {}
        contract_map: dict[str, Any] = {}
        review_map: dict[str, Any] = {}
        for scene, value in bundled.items():
            if isinstance(value, list):
                reg_map[scene] = value
                continue
            reg, contract, review = _entry_components(value)
            reg_map[scene] = reg
            contract_map[scene] = contract
            review_map[scene] = review
    else:
        reg_map = _as_scene_map(registrations, component="registration")
        contract_map = _as_scene_map(state_contracts, component="state_contract")
        review_map = _as_scene_map(release_reviews, component="release_review")

    all_scenes = set(reg_map) | set(contract_map) | set(review_map)
    present_components = {
        scene: tuple(name for name, index in (
            ("registration", reg_map), ("state_contract", contract_map), ("release_review", review_map))
            if scene in index)
        for scene in all_scenes
    }
    report["covered_scenes"] = sorted(scene for scene in expected
                                      if len(present_components.get(scene, ())) == 3)
    report["missing_scenes"] = sorted(set(expected) - set(report["covered_scenes"]))
    report["incomplete_scenes"] = [
        {"scene_key": scene, "missing_components": [name for name in
         ("registration", "state_contract", "release_review")
         if name not in present_components.get(scene, ())]}
        for scene in expected
        if scene in all_scenes and scene not in report["covered_scenes"]
    ]
    report["duplicate_scenes"] = sorted(scene for scene in all_scenes
                                         if any(isinstance(index.get(scene), list) and len(index[scene]) > 1
                                                for index in (reg_map, contract_map, review_map)))
    for scene in sorted(all_scenes | set(expected)):
        if scene not in all_scenes:
            # Missing scenes are represented by ``missing_scenes``; do not
            # manufacture an invalid registration row for an absent record.
            continue
        registration, registration_duplicates = _component_for_scene(reg_map, scene)
        contract, contract_duplicates = _component_for_scene(contract_map, scene)
        review, review_duplicates = _component_for_scene(review_map, scene)
        if scene not in expected:
            report["invalid"].append(_failure("scene", scene, "unexpected_scene"))
            continue
        scene_result = _audit_scene(scene, registration, contract, review)
        duplicate_reasons = registration_duplicates + contract_duplicates + review_duplicates
        if duplicate_reasons:
            scene_result["invalid"].extend(duplicate_reasons)
            scene_result["invalid"] = sorted(set(scene_result["invalid"]))
            scene_result["status"] = "invalid"
        report["scenes"][scene] = scene_result
        report["duplicate_measurements"].extend(scene_result.get("duplicate_measurement_ids", []))
        for reason in scene_result.get("invalid", []):
            report["invalid"].append(_failure("scene", scene, reason))
        for reason in scene_result.get("failed", []):
            report["failed"].append(_failure("scene", scene, reason))
    report["duplicate_measurements"] = sorted(set(report["duplicate_measurements"]))
    # Measurement IDs are registry identities, not merely local list indexes;
    # reusing one across scenes makes coverage ambiguous and is fail-closed.
    measurement_scenes: dict[str, list[str]] = {}
    for scene, scene_result in report["scenes"].items():
        review = review_map.get(scene)
        if isinstance(review, list):
            review = review[0] if review else None
        if not isinstance(review, Mapping):
            continue
        rows = _review_rows(review)
        if not isinstance(rows, list):
            continue
        for row in rows:
            measurement_id = row.get("measurement_id") if isinstance(row, Mapping) else None
            if _is_nonempty_string(measurement_id):
                measurement_scenes.setdefault(measurement_id, []).append(scene)
    for measurement_id, scenes in measurement_scenes.items():
        if len(set(scenes)) <= 1:
            continue
        report["duplicate_measurements"].append(measurement_id)
        for scene in sorted(set(scenes)):
            scene_result = report["scenes"][scene]
            scene_result["duplicate_measurement_ids"] = sorted(set(
                scene_result.get("duplicate_measurement_ids", []) + [measurement_id]))
            scene_result["invalid"] = sorted(set(
                scene_result.get("invalid", []) + [f"duplicate_measurement_across_scenes:{measurement_id}"]))
            scene_result["status"] = "invalid"
            report["invalid"].append(_failure("scene", scene,
                                               f"duplicate_measurement_across_scenes:{measurement_id}"))
    report["duplicate_measurements"] = sorted(set(report["duplicate_measurements"]))
    report["oracle_release_ready"] = (
        not report["missing_scenes"]
        and not report["duplicate_scenes"]
        and not report["duplicate_measurements"]
        and not report["invalid"]
        and not report["failed"]
        and set(report["scenes"]) == set(expected)
        and all(row["status"] == "verified" for row in report["scenes"].values())
    )
    report["status"] = "verified" if report["oracle_release_ready"] else (
        "invalid" if report["invalid"] else "failed")
    return report


def audit_native_oracle_completeness(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Descriptive alias for callers that use the completeness terminology."""
    return audit_native_oracle_registry(*args, **kwargs)


__all__ = [
    "EXPECTED_SCENES",
    "NATIVE_ORACLE_REGISTRY_SCHEMA",
    "NATIVE_ORACLE_REGISTRY_VERSION",
    "NATIVE_REMAINING_TASK_SCOPE",
    "audit_native_oracle_registry",
    "audit_native_oracle_completeness",
]
