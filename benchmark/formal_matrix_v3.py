"""Formal-directory and baseline qualification gates for WP15--16."""

from __future__ import annotations

from dataclasses import dataclass, asdict
import re
from typing import Iterable, Mapping


def _digest(value: str, name: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError(f"{name} must be a lowercase SHA-256")


@dataclass(frozen=True)
class FormalCaseV3:
    family_id: str
    case_id: str
    anchor_id: str
    domain: str
    certification_status: str
    oracle_status: str
    scene_digest: str
    roster_digest: str
    topology_digest: str
    evaluator_id: str
    catalog_digest: str = ""
    contract_digest: str = ""
    oracle_proof_digest: str = ""
    artifact_manifest_digest: str = ""
    common_start_digest: str = ""
    physical_evidence_digest: str = ""

    def __post_init__(self) -> None:
        if self.certification_status not in {"planned", "certified", "rejected", "incomplete"}:
            raise ValueError("unsupported formal case certification status")
        if self.oracle_status not in {"not_yet_certified", "deployable", "team_state_infeasible", "feasibility_unverified"}:
            raise ValueError("unsupported formal case oracle status")
        if self.certification_status == "certified":
            if self.oracle_status not in {"deployable", "team_state_infeasible"}:
                raise ValueError("certified formal case requires a terminal oracle status")
            for name in ("catalog_digest", "contract_digest", "oracle_proof_digest", "artifact_manifest_digest", "common_start_digest", "physical_evidence_digest"):
                _digest(getattr(self, name), name)

    @property
    def certified(self) -> bool:
        return self.certification_status == "certified" and self.oracle_status in {"deployable", "team_state_infeasible"}

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class BaselineQualificationV3:
    baseline_id: str
    source_reference: str
    adapter_id: str | None
    checkpoint_digest: str | None
    physical_status: str
    observation_contract: str | None
    condition_coverage: tuple[str, ...]
    system_digest: str = ""
    task_family_digest: str = ""
    starting_team_digest: str = ""
    budget_digest: str = ""
    observation_interface_digest: str = ""
    artifact_manifest_digest: str = ""

    @property
    def rankable(self) -> bool:
        # A baseline is only comparable when its observed contract covers at
        # least one registered condition.  Empty or duplicated coverage is a
        # registration defect, not a zero-result baseline.
        coverage = tuple(self.condition_coverage)
        return bool(self.adapter_id and self.checkpoint_digest and self.observation_contract
                    and self.physical_status == "qualified"
                    and coverage and len(set(coverage)) == len(coverage)
                    and all(_valid_digest(value) for value in (
                        self.system_digest, self.task_family_digest, self.starting_team_digest,
                        self.budget_digest, self.observation_interface_digest,
                        self.artifact_manifest_digest)))


def _valid_digest(value: str) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"[0-9a-f]{64}", value))


def formal_case_status(cases: Iterable[FormalCaseV3], *, min_cases_per_family: int = 30) -> dict[str, object]:
    grouped: dict[str, list[FormalCaseV3]] = {}
    seen = set()
    for case in cases:
        key = (case.family_id, case.case_id)
        if key in seen or not all(getattr(case, name) for name in ("family_id", "case_id", "anchor_id", "domain", "scene_digest", "roster_digest", "topology_digest", "evaluator_id")):
            raise ValueError("formal cases require unique complete identities")
        seen.add(key)
        grouped.setdefault(case.family_id, []).append(case)
    if not grouped:
        return {"status": "na", "reason": "empty_formal_directory", "families": {}}
    report: dict[str, dict[str, object]] = {}
    for family, rows in sorted(grouped.items()):
        uncertified = [row.case_id for row in rows if not row.certified]
        report[family] = {"case_count": len(rows), "certified_count": len(rows) - len(uncertified), "uncertified_case_ids": uncertified, "eligible": len(rows) >= min_cases_per_family and not uncertified}
    eligible = all(bool(row["eligible"]) for row in report.values())
    return {"status": "ready" if eligible else "na", "reason": "formal_directory_frozen" if eligible else "certification_or_family_floor_missing", "families": report}


def baseline_release_status(baselines: Iterable[BaselineQualificationV3]) -> dict[str, object]:
    rows = tuple(baselines)
    if len({r.baseline_id for r in rows}) != len(rows):
        raise ValueError("duplicate baseline identity")
    return {"status": "ready" if rows and all(item.rankable for item in rows) else "na", "baselines": {item.baseline_id: {"rankable": item.rankable, "physical_status": item.physical_status, "adapter_id": item.adapter_id} for item in rows}}


__all__ = ["FormalCaseV3", "BaselineQualificationV3", "formal_case_status", "baseline_release_status"]
