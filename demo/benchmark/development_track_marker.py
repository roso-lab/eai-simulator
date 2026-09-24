"""Development-track evidence markers separating development from release.

External asset gaps (for example the Factory frozen-USD hash mismatch) block
formal release, formal native episodes, and scoreable publication only.  They
must not block contract development, reader implementation, scoring-sidecar
work, or non-release smoke evidence.  This module stamps every development
run with an explicit marker so its outputs can never be mistaken for formal
benchmark results:

- ``asset_status`` distinguishes frozen-verified assets from development
  assets that still lack a trusted provider version.
- ``evidence_class`` keeps raw/diagnostic/contract evidence out of formal
  statistics regardless of asset status.
- ``release_eligible`` is always ``False`` here; formal eligibility requires
  the independent release gates (native readers, alternative-path
  certificates, frozen assets, verified oracle evidence) and is decided by
  the release review, never asserted by a run marker.

``unverified`` remains a platform evidence state only: a marker never feeds
the participant model, never creates a third task truth, and never promotes
the three release flags.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy

from .provenance import canonical_sha256

DEVELOPMENT_TRACK_SCHEMA = "eai.benchmark.development_track_marker"
DEVELOPMENT_TRACK_VERSION = 1
ASSET_STATUSES = ("frozen_verified", "development_unverified")
EVIDENCE_CLASSES = ("raw", "diagnostic", "contract")
FACTORY_FIXTURE_HASH_MISMATCH = "factory_fixture_hash_mismatch"
_RELEASE = {"execution_validated": False, "business_outcomes_validated": False,
            "scoreable": False}


def _nonempty_strings(values: object, field: str, *, unique: bool = True,
                      allow_empty: bool = False) -> list[str]:
    if not isinstance(values, (list, tuple)):
        raise ValueError(f"{field} requires a list of strings")
    items = [item for item in values]
    if not allow_empty and not items:
        raise ValueError(f"{field} requires nonempty strings")
    if any(not isinstance(item, str) or not item.strip() for item in items):
        raise ValueError(f"{field} requires nonempty strings")
    if unique and len(set(items)) != len(items):
        raise ValueError(f"{field} must not contain duplicates")
    return items


def build_development_marker(
    scene_key: str,
    *,
    asset_status: str,
    evidence_class: str,
    infrastructure_blockers: Iterable[str] = (),
) -> dict[str, object]:
    """Stamp one scene's development run with its isolation boundary."""
    if not isinstance(scene_key, str) or not scene_key.strip():
        raise ValueError("scene_key must be a nonempty string")
    if asset_status not in ASSET_STATUSES:
        raise ValueError(f"asset_status must be one of {ASSET_STATUSES}")
    if evidence_class not in EVIDENCE_CLASSES:
        raise ValueError(f"evidence_class must be one of {EVIDENCE_CLASSES}")
    blockers = sorted(set(_nonempty_strings(
        list(infrastructure_blockers), "infrastructure_blockers", allow_empty=True)))
    if asset_status == "development_unverified" and not blockers:
        raise ValueError(
            "development_unverified assets must record at least one blocker")
    marker: dict[str, object] = {
        "schema": DEVELOPMENT_TRACK_SCHEMA,
        "version": DEVELOPMENT_TRACK_VERSION,
        "visibility": "evaluator_only",
        "scene_key": scene_key,
        "asset_status": asset_status,
        "evidence_class": evidence_class,
        "infrastructure_blockers": blockers,
        # A run marker can only ever restrict release.  Formal eligibility is
        # decided by the separate release review over native evidence.
        "release_eligible": False,
        **_RELEASE,
    }
    marker["marker_sha256"] = canonical_sha256(
        {key: value for key, value in marker.items()})
    return marker


def factory_hash_mismatch_marker(
    *,
    evidence_class: str = "contract",
) -> dict[str, object]:
    """Marker for Factory work under the frozen-evaluator hash mismatch."""
    return build_development_marker(
        "factory",
        asset_status="development_unverified",
        evidence_class=evidence_class,
        infrastructure_blockers=[FACTORY_FIXTURE_HASH_MISMATCH],
    )


def validate_development_marker(marker: Mapping[str, object]) -> dict[str, object]:
    """Fail closed on malformed, tampered, or release-promoting markers."""
    if not isinstance(marker, Mapping):
        raise ValueError("marker must be a mapping")
    rebuilt = build_development_marker(
        marker.get("scene_key", ""),
        asset_status=marker.get("asset_status", ""),
        evidence_class=marker.get("evidence_class", ""),
        infrastructure_blockers=marker.get("infrastructure_blockers", []),
    )
    if (marker.get("schema") != DEVELOPMENT_TRACK_SCHEMA
            or marker.get("version") != DEVELOPMENT_TRACK_VERSION
            or marker.get("visibility") != "evaluator_only"
            or marker.get("marker_sha256") != rebuilt["marker_sha256"]
            or marker.get("release_eligible") is not False
            or any(marker.get(flag) is not False for flag in _RELEASE)):
        raise ValueError("marker identity, digest, or release flags are invalid")
    return rebuilt


def merge_development_markers(
    markers: Iterable[Mapping[str, object]],
) -> dict[str, object]:
    """Aggregate per-scene markers into a run-level isolation boundary.

    The run boundary is release-restrictive by construction: any missing
    scene component, any development-unverified asset, and any blocker keep
    the whole run on the development track with all flags false.
    """
    rows: list[dict[str, object]] = []
    scenes: list[str] = []
    for marker in markers:
        row = validate_development_marker(marker)
        rows.append(row)
        scenes.append(row["scene_key"])
    blockers = sorted({blocker for row in rows
                       for blocker in row["infrastructure_blockers"]})
    unverified = sorted({row["scene_key"] for row in rows
                         if row["asset_status"] == "development_unverified"})
    classes = sorted({row["evidence_class"] for row in rows})
    return {
        "schema": "eai.benchmark.development_track_boundary",
        "version": DEVELOPMENT_TRACK_VERSION,
        "visibility": "evaluator_only",
        "scene_markers": {row["scene_key"]: deepcopy(row) for row in rows},
        "scenes": sorted(set(scenes)),
        "development_unverified_scenes": unverified,
        "evidence_classes": classes,
        "infrastructure_blockers": blockers,
        "release_eligible": False,
        **_RELEASE,
    }


def stamp_evidence_record(
    record: Mapping[str, object],
    marker: Mapping[str, object],
) -> dict[str, object]:
    """Attach a validated marker to a raw evidence record.

    The stamp classifies the record as development evidence and blocks the
    ``formal_statistics`` key from ever being asserted by the record itself.
    """
    validated = validate_development_marker(marker)
    stamped = deepcopy(dict(record))
    stamped["development_track"] = deepcopy(validated)
    stamped["evidence_role"] = "development_track_evidence"
    stamped["formal_statistics"] = False
    return stamped


__all__ = [
    "ASSET_STATUSES",
    "DEVELOPMENT_TRACK_SCHEMA",
    "DEVELOPMENT_TRACK_VERSION",
    "EVIDENCE_CLASSES",
    "FACTORY_FIXTURE_HASH_MISMATCH",
    "build_development_marker",
    "factory_hash_mismatch_marker",
    "merge_development_markers",
    "stamp_evidence_record",
    "validate_development_marker",
]
