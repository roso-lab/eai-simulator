"""Release-index assembly for paper experiment artifacts and endpoints.

The release index is an audit product.  It joins immutable branch artifacts
and metric records, preserves missingness, and reports coverage/support gates;
it does not pool repeats or calculate inferential intervals.  A downstream
statistics stage can consume the indexed records after the release gates pass.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from .artifacts import BranchArtifactStore, StoredBranchArtifact
from .metrics import MetricObservation, MetricRegistry, MetricStatus
from .protocol import BranchRole, RunKey
from .task_catalog import TaskAnchor, catalog_digest


class ReleaseError(ValueError):
    """Raised when an index record cannot be admitted to a release."""


class ReleaseStatus(str, Enum):
    READY = "ready"
    PENDING = "pending"
    INVALID = "invalid"
    NA = "N/A"


def _digest_payload(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class IndexedMetric:
    """Metric observation plus the strata needed for paper risk sets."""

    observation: MetricObservation
    anchor_id: str
    domain: str
    primary_label: str
    relation_signature: tuple[str, ...]
    p3_subtype: str | None
    condition_id: str
    branch_role: BranchRole
    base_instance_id: str
    repeat_id: str
    support_ids: tuple[str, ...] = ()
    risk_set: str = ""
    artifact_digest: str = ""
    system_id: str = ""
    checkpoint_round: str = ""
    untreated_history_id: str = ""

    def __post_init__(self) -> None:
        for name in ("anchor_id", "domain", "primary_label", "condition_id", "base_instance_id", "repeat_id"):
            if not str(getattr(self, name)).strip():
                raise ReleaseError(f"{name} is required")
        object.__setattr__(self, "branch_role", BranchRole(self.branch_role))
        object.__setattr__(self, "relation_signature", tuple(str(item) for item in self.relation_signature))
        object.__setattr__(self, "support_ids", tuple(str(item) for item in self.support_ids))

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.observation.to_dict(),
            "anchor_id": self.anchor_id,
            "domain": self.domain,
            "primary_label": self.primary_label,
            "relation_signature": list(self.relation_signature),
            "p3_subtype": self.p3_subtype,
            "condition_id": self.condition_id,
            "branch_role": self.branch_role.value,
            "base_instance_id": self.base_instance_id,
            "repeat_id": self.repeat_id,
            "support_ids": list(self.support_ids),
            "risk_set": self.risk_set,
            "artifact_digest": self.artifact_digest,
            "system_id": self.system_id, "checkpoint_round": self.checkpoint_round,
            "untreated_history_id": self.untreated_history_id,
        }


@dataclass(frozen=True)
class ReleaseReport:
    """Serializable audit view of one release-index snapshot."""

    status: ReleaseStatus
    reason: str
    catalog_digest: str
    plan_digest: str
    artifact_digests: tuple[str, ...]
    records: tuple[IndexedMetric, ...]
    metric_counts: Mapping[str, Mapping[str, int]]
    coverage: Mapping[str, Mapping[str, int]]
    gates: Mapping[str, bool]

    @property
    def inferentially_ready(self) -> bool:
        # This product proves indexing only. Qualification requires a separate
        # registered-design/evaluator/physical/replay/precision release review.
        return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "benchmark.release.v2",
            "qualification": "artifact_index_only", "inferentially_ready": False,
            "status": self.status.value,
            "reason": self.reason,
            "catalog_digest": self.catalog_digest,
            "plan_digest": self.plan_digest,
            "artifact_digests": list(self.artifact_digests),
            "records": [record.to_dict() for record in self.records],
            "metric_counts": {key: dict(value) for key, value in self.metric_counts.items()},
            "coverage": {key: dict(value) for key, value in self.coverage.items()},
            "gates": dict(self.gates),
        }


class ReleaseIndex:
    """Join artifacts and metric records without erasing unsupported cells."""

    def __init__(
        self,
        catalog: Iterable[TaskAnchor],
        *,
        metric_registry: MetricRegistry | None = None,
        plan_digest: str = "",
    ) -> None:
        self.catalog = tuple(catalog)
        self._anchors = {item.anchor_id: item for item in self.catalog}
        if len(self._anchors) != len(self.catalog):
            raise ReleaseError("catalog anchor IDs must be unique")
        self.registry = metric_registry or MetricRegistry()
        self.plan_digest = str(plan_digest)
        self._records: list[IndexedMetric] = []
        self._artifact_digests: set[str] = set()
        self._artifact_keys: set[str] = set()
        self._artifacts: dict[str, StoredBranchArtifact] = {}

    @property
    def records(self) -> tuple[IndexedMetric, ...]:
        return tuple(self._records)

    @property
    def artifact_digests(self) -> tuple[str, ...]:
        return tuple(sorted(self._artifact_digests))

    def add_artifact(self, artifact: Any) -> str:
        """Admit a previously verified StoredBranchArtifact or artifact path."""

        if isinstance(artifact, (str, Path)):
            raise ReleaseError("read artifacts through BranchArtifactStore before indexing")
        if not isinstance(artifact, StoredBranchArtifact):
            raise ReleaseError("artifact must be a StoredBranchArtifact read through its store")
        try:
            verified = BranchArtifactStore(artifact.path.parent).read(artifact.run_key)
        except (ValueError, OSError) as exc:
            raise ReleaseError("artifact verification failed") from exc
        if verified.path != artifact.path or verified.payload != artifact.payload:
            raise ReleaseError("artifact changed since verification")
        payload = verified.payload
        digest = str(payload.get("artifact_digest", ""))
        if not digest:
            raise ReleaseError("artifact_digest is required")
        run_key = payload.get("run_key")
        if not isinstance(run_key, Mapping):
            raise ReleaseError("artifact run_key is required")
        key = "/".join(str(run_key.get(name, "")) for name in ("base_instance_id", "system_id", "condition_id", "repeat_id", "checkpoint_round", "branch_role", "untreated_history_id"))
        if key in self._artifact_keys:
            raise ReleaseError(f"duplicate artifact run key: {key}")
        self._artifact_keys.add(key)
        self._artifact_digests.add(digest)
        self._artifacts[digest] = verified
        return digest

    def add_artifact_by_key(self, store: BranchArtifactStore, run_key: RunKey) -> str:
        return self.add_artifact(store.read(run_key))

    def add_metric(self, record: IndexedMetric) -> None:
        spec = self.registry.get(record.observation.metric_id)
        anchor = self._anchors.get(record.anchor_id)
        if anchor is None:
            raise ReleaseError(f"unknown anchor_id: {record.anchor_id}")
        if anchor.domain != record.domain or anchor.primary_label != record.primary_label:
            raise ReleaseError("metric strata do not match catalog anchor")
        if tuple(record.relation_signature) != tuple(anchor.relation_signature):
            raise ReleaseError("metric relation_signature does not match catalog anchor")
        if record.p3_subtype != anchor.p3_subtype:
            raise ReleaseError("metric p3_subtype does not match catalog anchor")
        if record.artifact_digest not in self._artifacts:
            raise ReleaseError("metric requires an admitted verified artifact")
        artifact = self._artifacts[record.artifact_digest]
        key = artifact.run_key
        if (record.base_instance_id, record.system_id, record.condition_id, record.repeat_id,
            record.checkpoint_round, record.branch_role, record.untreated_history_id) != (
            key.base_instance_id, key.system_id, key.condition_id, key.repeat_id,
            key.checkpoint_round, key.branch_role, key.untreated_history_id):
            raise ReleaseError("metric/artifact full run identity mismatch")
        hashes = {str(e.get("evidence_hash", "")) for e in artifact.payload.get("evaluation", {}).get("evidence", ())}
        if record.observation.status is MetricStatus.VALID and (
            not record.observation.evidence_hash or record.observation.evidence_hash not in hashes):
            raise ReleaseError("metric evidence is absent from artifact evaluator evidence")
        if record.condition_id != spec.condition:
            raise ReleaseError("metric condition does not match registered endpoint")
        if spec.relation is not None:
            relation_match = spec.relation in record.relation_signature
            if spec.relation in {"P3-J", "P3-S"}:
                relation_match = record.p3_subtype == spec.relation and "P3" in record.relation_signature
            if not relation_match:
                raise ReleaseError(f"metric {spec.metric_id} is not eligible for anchor {record.anchor_id}")
        identity = (record.observation.metric_id, record.anchor_id, record.condition_id, record.branch_role, record.base_instance_id, record.repeat_id, record.system_id, record.checkpoint_round, record.untreated_history_id)
        if any((item.observation.metric_id, item.anchor_id, item.condition_id, item.branch_role, item.base_instance_id, item.repeat_id, item.system_id, item.checkpoint_round, item.untreated_history_id) == identity for item in self._records):
            raise ReleaseError("duplicate metric record identity")
        self._records.append(record)

    def add_observation(
        self,
        observation: MetricObservation,
        *,
        anchor_id: str,
        condition_id: str,
        branch_role: BranchRole,
        base_instance_id: str,
        repeat_id: str,
        support_ids: Iterable[str] = (),
        risk_set: str = "",
        artifact_digest: str = "",
        system_id: str = "", checkpoint_round: str = "", untreated_history_id: str = "",
    ) -> IndexedMetric:
        anchor = self._anchors.get(anchor_id)
        if anchor is None:
            raise ReleaseError(f"unknown anchor_id: {anchor_id}")
        record = IndexedMetric(
            observation=observation,
            anchor_id=anchor_id,
            domain=anchor.domain,
            primary_label=anchor.primary_label,
            relation_signature=anchor.relation_signature,
            p3_subtype=anchor.p3_subtype,
            condition_id=condition_id,
            branch_role=branch_role,
            base_instance_id=base_instance_id,
            repeat_id=repeat_id,
            support_ids=tuple(support_ids),
            risk_set=risk_set,
            artifact_digest=artifact_digest,
            system_id=system_id, checkpoint_round=checkpoint_round,
            untreated_history_id=untreated_history_id,
        )
        self.add_metric(record)
        return record

    def finalize(self, *, expected_plan_digest: str | None = None) -> ReleaseReport:
        """Build an audit report; pending/NA records never become zeroes."""

        if expected_plan_digest is not None and str(expected_plan_digest) != self.plan_digest:
            return self._report(ReleaseStatus.INVALID, "plan_digest_mismatch")
        metric_counts: dict[str, dict[str, int]] = {}
        for record in self._records:
            counts = metric_counts.setdefault(record.observation.metric_id, {status.value: 0 for status in MetricStatus})
            counts[record.observation.status.value] += 1
        coverage: dict[str, dict[str, int]] = {}
        for anchor in self.catalog:
            key = f"{anchor.domain}/{anchor.primary_label}/{anchor.typical_or_common}"
            counts = coverage.setdefault(key, {"planned": 0, "observed": 0, "valid": 0, "N/A": 0, "pending": 0, "invalid": 0})
            counts["planned"] += 1
            rows = [item for item in self._records if item.anchor_id == anchor.anchor_id]
            counts["observed"] += len(rows)
            for row in rows:
                counts[row.observation.status.value] += 1
        has_invalid = any(item.observation.status is MetricStatus.INVALID for item in self._records)
        has_pending = any(item.observation.status is MetricStatus.PENDING for item in self._records)
        gates = {
            "catalog_present": bool(self.catalog),
            "plan_bound": bool(self.plan_digest),
            "artifacts_indexed": bool(self._artifact_digests),
            "no_invalid_records": not has_invalid,
            "all_observed_records_have_evidence": all(bool(item.observation.evidence_hash) or item.observation.status in {MetricStatus.NA, MetricStatus.PENDING} for item in self._records),
            "missingness_explicit": all(item.observation.status is not MetricStatus.NA or bool(item.observation.na_reason) for item in self._records),
        }
        status = ReleaseStatus.INVALID if has_invalid else ReleaseStatus.PENDING if has_pending or not self._records else ReleaseStatus.READY
        reason = "invalid_metric_record" if has_invalid else "pending_evidence_or_observation" if status is ReleaseStatus.PENDING else "artifact_index_ready_qualification_pending"
        return ReleaseReport(status, reason, catalog_digest(self.catalog), self.plan_digest, self.artifact_digests, tuple(self._records), metric_counts, coverage, gates)

    def _report(self, status: ReleaseStatus, reason: str) -> ReleaseReport:
        return ReleaseReport(status, reason, catalog_digest(self.catalog), self.plan_digest, self.artifact_digests, tuple(self._records), {}, {}, {})


__all__ = ["IndexedMetric", "ReleaseError", "ReleaseIndex", "ReleaseReport", "ReleaseStatus"]
