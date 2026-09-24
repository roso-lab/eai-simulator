"""Alternative-path completeness certificates for remaining-task oracles.

A ``not_deployable`` answer from the finite-state solver is only as strong as
the transition model's completeness: "search found no path" proves physical
infeasibility only when the model provably covers every legal completion
path class from the trusted state.  This module freezes that completeness
claim as a reviewable certificate.

The certificate never produces a feasibility label.  It gates the solver's
``not_deployable`` answers: without a verified certificate attached to the
registration, a not-deployable result must be downgraded to a platform
evidence failure (``oracle_status=failed``, ``feasibility_label=null``).
Release flags stay false; certificates are evaluator-only.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy
from typing import Any

from .development_track_marker import build_development_marker
from .provenance import canonical_sha256

ALTERNATIVE_PATH_SCHEMA = "eai.benchmark.alternative_path_certificate"
ALTERNATIVE_PATH_VERSION = 1
REVIEW_STATUSES = ("pending", "verified")
_CERT_RELEASE = {"execution_validated": False, "business_outcomes_validated": False,
                 "scoreable": False}


def _nonempty_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} requires a nonempty string")
    return value


def _unique_texts(values: Any, field: str) -> list[str]:
    if (not isinstance(values, (list, tuple)) or not values
            or any(not isinstance(item, str) or not item.strip() for item in values)
            or len(set(values)) != len(values)):
        raise ValueError(f"{field} requires unique nonempty strings")
    return list(values)


def build_alternative_path_certificate(
    scene_key: str,
    registration: Mapping[str, Any],
    *,
    path_classes: Mapping[str, Mapping[str, Any]],
    covered_measurement_ids: Iterable[str],
    uncovered_path_policy: str,
    independent_review: str = "pending",
    evidence_refs: Iterable[str] = (),
) -> dict[str, Any]:
    """Freeze a model-completeness claim for one registered oracle.

    ``path_classes`` maps a stable class id (for example
    ``single_robot_substitute`` or ``dual_robot_load_combination``) to a
    description with the legal transitions it covers.
    ``uncovered_path_policy`` must state how paths outside the enumerated
    classes are handled; anything other than ``treated_as_uncovered`` makes
    the certificate invalid because silent holes cannot be audited.
    """
    _nonempty_text(scene_key, "scene_key")
    if not isinstance(registration, Mapping):
        raise ValueError("registration must be a mapping")
    registration_sha256 = _nonempty_text(
        registration.get("registration_sha256"), "registration_sha256")
    covered = _unique_texts(list(covered_measurement_ids), "covered_measurement_ids")
    if not isinstance(path_classes, Mapping) or not path_classes:
        raise ValueError("path_classes must enumerate at least one class")
    normalized_classes: dict[str, dict[str, Any]] = {}
    for class_id, row in path_classes.items():
        _nonempty_text(class_id, "path class id")
        if not isinstance(row, Mapping):
            raise ValueError(f"path class {class_id} must be a mapping")
        transitions = _unique_texts(row.get("covered_transitions", []),
                                    f"path class {class_id} transitions")
        _nonempty_text(row.get("description"), f"path class {class_id} description")
        normalized_classes[class_id] = {
            "description": row["description"],
            "covered_transitions": transitions,
        }
    model_transitions = {t["id"] for t in registration.get("model", {}).get(
        "transitions", []) if isinstance(t, Mapping) and isinstance(t.get("id"), str)}
    declared = {transition for row in normalized_classes.values()
                for transition in row["covered_transitions"]}
    if not declared <= model_transitions:
        raise ValueError("path classes reference unregistered transitions")
    if uncovered_path_policy != "treated_as_uncovered":
        raise ValueError("uncovered paths must be explicitly treated as uncovered")
    if independent_review not in REVIEW_STATUSES:
        raise ValueError(f"independent_review must be one of {REVIEW_STATUSES}")
    refs = _unique_texts(list(evidence_refs), "evidence_refs")
    if independent_review == "verified" and not refs:
        raise ValueError("verified review requires evidence references")
    registered_points = [point["id"] for point in
                         registration.get("measurement_points", [])
                         if isinstance(point, Mapping) and isinstance(point.get("id"), str)]
    unknown = set(covered) - set(registered_points)
    if unknown:
        raise ValueError(f"covered points not registered: {sorted(unknown)}")
    certificate: dict[str, Any] = {
        "schema": ALTERNATIVE_PATH_SCHEMA,
        "version": ALTERNATIVE_PATH_VERSION,
        "visibility": "evaluator_only",
        "scene_key": scene_key,
        "registration_sha256": registration_sha256,
        "path_classes": normalized_classes,
        "covered_measurement_ids": sorted(covered),
        "uncovered_path_policy": uncovered_path_policy,
        "independent_review": independent_review,
        "evidence_refs": refs,
        # A certificate can only restrict release; formal readiness is the
        # release review's decision over the full native evidence stack.
        "release_eligible": False,
        **_CERT_RELEASE,
    }
    certificate["certificate_sha256"] = canonical_sha256(
        {key: value for key, value in certificate.items()})
    return certificate


def validate_alternative_path_certificate(
    certificate: Mapping[str, Any],
    registration: Mapping[str, Any],
) -> dict[str, Any]:
    """Re-derive and seal-check a certificate against its registration."""
    rebuilt = build_alternative_path_certificate(
        certificate.get("scene_key", ""),
        registration,
        path_classes=certificate.get("path_classes", {}),
        covered_measurement_ids=certificate.get("covered_measurement_ids", []),
        uncovered_path_policy=certificate.get("uncovered_path_policy", ""),
        independent_review=certificate.get("independent_review", ""),
        evidence_refs=certificate.get("evidence_refs", []),
    )
    if (certificate.get("schema") != ALTERNATIVE_PATH_SCHEMA
            or certificate.get("version") != ALTERNATIVE_PATH_VERSION
            or certificate.get("visibility") != "evaluator_only"
            or certificate.get("certificate_sha256") != rebuilt["certificate_sha256"]
            or certificate.get("registration_sha256")
            != registration.get("registration_sha256")
            or certificate.get("release_eligible") is not False
            or any(certificate.get(flag) is not False for flag in _CERT_RELEASE)):
        raise ValueError("certificate identity, digest, or release flags invalid")
    return rebuilt


def gate_not_deployable_result(
    result: Mapping[str, Any],
    certificate: Mapping[str, Any] | None,
    registration: Mapping[str, Any],
) -> dict[str, Any]:
    """Downgrade unsupported not_deployable answers to platform failures.

    A verified ``deployable`` needs no completeness certificate (one legal
    path was exhibited by the search itself).  A verified ``not_deployable``
    does: without a certificate whose independent review is verified and
    whose covered points include the measurement, the answer becomes
    ``oracle_status=failed`` with a null label and a stable reason.
    """
    gated = deepcopy(dict(result))
    if gated.get("oracle_status") != "verified":
        return gated
    if gated.get("feasibility_label") == "deployable":
        gated["alternative_path_gate"] = "not_required"
        return gated
    if certificate is None:
        gated.update(feasibility_label=None, oracle_status="failed",
                     platform_valid=False,
                     quality_failure="not_deployable_without_certificate")
        gated["alternative_path_gate"] = "missing_certificate"
        return gated
    try:
        validate_alternative_path_certificate(certificate, registration)
    except ValueError as exc:
        gated.update(feasibility_label=None, oracle_status="failed",
                     platform_valid=False,
                     quality_failure=f"invalid_certificate:{exc}")
        gated["alternative_path_gate"] = "invalid_certificate"
        return gated
    if certificate["independent_review"] != "verified":
        gated.update(feasibility_label=None, oracle_status="failed",
                     platform_valid=False,
                     quality_failure="certificate_review_pending")
        gated["alternative_path_gate"] = "review_pending"
        return gated
    measurement_id = gated.get("measurement_id")
    if measurement_id not in certificate["covered_measurement_ids"]:
        gated.update(feasibility_label=None, oracle_status="failed",
                     platform_valid=False,
                     quality_failure="measurement_not_covered_by_certificate")
        gated["alternative_path_gate"] = "uncovered_measurement"
        return gated
    gated["alternative_path_gate"] = "verified"
    gated["certificate_sha256"] = certificate["certificate_sha256"]
    return gated


def certificate_development_marker(
    scene_key: str, *, evidence_class: str = "contract",
    infrastructure_blockers: Iterable[str] = ("alternative_path_native_review_pending",),
) -> dict[str, Any]:
    return build_development_marker(
        scene_key, asset_status="development_unverified",
        evidence_class=evidence_class,
        infrastructure_blockers=list(infrastructure_blockers))


__all__ = [
    "ALTERNATIVE_PATH_SCHEMA", "ALTERNATIVE_PATH_VERSION", "REVIEW_STATUSES",
    "build_alternative_path_certificate", "certificate_development_marker",
    "gate_not_deployable_result", "validate_alternative_path_certificate",
]
