"""Formal-directory and baseline qualification gates for WP15--16."""

from __future__ import annotations

from dataclasses import dataclass, asdict
import re
from typing import Iterable, Mapping

from algorithm.heterocoord.registry import (
    PRIMARY_SYSTEM_COLUMNS,
    REGISTRY,
    implementation_condition_complete,
    validate_implementation_register,
)


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
    job_system_id: str = ""
    comparison_condition_id: str = ""
    information_rights_group: str = ""
    code_version: str = ""
    source_version: str = ""
    model_version: str = ""

    @property
    def rankable(self) -> bool:
        # A baseline is only comparable when its observed contract covers at
        # least one registered condition.  Empty or duplicated coverage is a
        # registration defect, not a zero-result baseline.
        coverage = tuple(self.condition_coverage)
        matches = [item for item in REGISTRY if item["algorithm_id"] == self.baseline_id]
        return len(matches) == 1 and self.rankable_against(matches[0])

    def rankable_against(self, registration: Mapping[str, object]) -> bool:
        """Check evidence against one trusted implementation-register row."""

        coverage = tuple(self.condition_coverage)
        return bool(implementation_condition_complete(registration)
                    and registration.get("algorithm_id") == self.baseline_id
                    and registration.get("job_system_id") == self.job_system_id
                    and registration.get("source_reference") == self.source_reference
                    and registration.get("adapter_version") == self.adapter_id
                    and registration.get("comparison_condition_id") == self.comparison_condition_id
                    and registration.get("information_rights_group") == self.information_rights_group
                    and registration.get("code_version") == self.code_version
                    and registration.get("source_version") == self.source_version
                    and registration.get("model_version") == self.model_version
                    and self.adapter_id and self.checkpoint_digest and self.observation_contract
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


def baseline_release_status(
    baselines: Iterable[BaselineQualificationV3],
    *,
    implementation_register: Iterable[Mapping[str, object]] = REGISTRY,
) -> dict[str, object]:
    rows = tuple(baselines)
    if len({r.baseline_id for r in rows}) != len(rows):
        raise ValueError("duplicate baseline identity")
    registered_rows = tuple(dict(item) for item in implementation_register)
    validate_implementation_register(registered_rows)
    by_id = {item["algorithm_id"]: item for item in registered_rows}
    exact_roster = tuple(item.baseline_id for item in rows) == PRIMARY_SYSTEM_COLUMNS
    readiness = {
        item.baseline_id: (
            item.rankable_against(by_id[item.baseline_id])
            if item.baseline_id in by_id else False
        )
        for item in rows
    }
    ready = exact_roster and all(readiness.values())
    return {
        "status": "ready" if ready else "na",
        "reason": (
            "exact_primary_roster_qualified"
            if ready
            else "primary_roster_mismatch"
            if not exact_roster
            else "primary_qualification_incomplete"
        ),
        "expected_order": list(PRIMARY_SYSTEM_COLUMNS),
        "baselines": {
            item.baseline_id: {
                "rankable": readiness[item.baseline_id],
                "physical_status": item.physical_status,
                "adapter_id": item.adapter_id,
                "comparison_condition_id": item.comparison_condition_id,
                "information_rights_group": item.information_rights_group,
            }
            for item in rows
        },
    }


__all__ = ["FormalCaseV3", "BaselineQualificationV3", "formal_case_status", "baseline_release_status"]
