"""Preregistered MCR replay for communication experiment rows.

The communication evidence producer may opt into this mapping only when an
evaluator froze a sealed contract-v4 before execution.  This module does not
author weights, infer a success predicate, or turn boundary samples into a
continuous trajectory.  Its first version supports terminal ``MCR_comm``
only; every other task-performance metric remains outside this boundary.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import math
from typing import Any

from .goal_contract_v4 import (
    ACTIVATION_EPISODE_START,
    MILESTONES_FIELD,
    validate_contract_v4,
    validate_frozen_v4,
)
from .goal_progress_evidence import SCHEMA as PROGRESS_EVIDENCE_SCHEMA
from .goal_progress_evidence import replay_progress_evidence
from .provenance import canonical_sha256


TASK_PERFORMANCE_MAPPING_SCHEMA = (
    "eai.benchmark.communication_task_performance_mapping")
TASK_PERFORMANCE_MAPPING_VERSION = 1
TASK_PERFORMANCE_METHOD_ID = "frozen-v4-terminal-mcr-v1"
TASK_PERFORMANCE_REPLAY_SCHEMA = (
    "eai.benchmark.communication_task_performance_replay")
TASK_PERFORMANCE_REPLAY_VERSION = 1

_MAPPING_FIELDS = frozenset({
    "schema", "version", "method_id", "metric_name",
    "frozen_contract_v4", "progress_source", "sample_selector",
    "predicate_binding", "unknown_policy", "revocation_policy",
    "stability_policy", "eligible_termination_reasons",
    "higher_is_better", "release", "scoreable", "execution_validated",
    "business_outcomes_validated",
})
_PROGRESS_SOURCE_FIELDS = frozenset({
    "schema", "version", "evaluator_ref", "clock_source",
})
_REPLAY_FIELDS = frozenset({
    "schema", "version", "status", "na_reason", "metric_name",
    "SR_comm", "MCR_comm", "AUP_comm", "e_step",
    "mapping_spec_sha256", "frozen_contract_v4_seal_sha256",
    "episode_id", "public_task_sha256", "progress_report_sha256",
    "terminal_sample_sequence", "eligible_termination",
    "unknown_predicate_ids", "monotonicity_violation_predicate_ids",
    "unselected_metrics", "raw_only", "not_protocol_score",
    "release", "scoreable", "execution_validated",
    "business_outcomes_validated",
})
_RELEASE = {
    "release": False,
    "scoreable": False,
    "execution_validated": False,
    "business_outcomes_validated": False,
}
_UNSELECTED = {"SR_comm": None, "AUP_comm": None, "e_step": None}
_ANTI_DOUBLE_COUNT_RULE = "first_external_satisfaction_only"
_HEX = frozenset("0123456789abcdef")


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a nonempty string")
    return value


def _exact_fields(value: Mapping[str, Any], expected: frozenset[str],
                  label: str) -> None:
    missing = expected - set(value)
    extra = set(value) - expected
    if missing or extra:
        raise ValueError(
            f"{label} fields mismatch; missing={sorted(missing)}, "
            f"extra={sorted(extra)}")


def _sha256(value: Any, label: str) -> str:
    if (not isinstance(value, str) or len(value) != 64
            or value != value.lower() or set(value) - _HEX):
        raise ValueError(f"{label} must be a lowercase SHA-256")
    return value


def _string_list(value: Any, label: str) -> list[str]:
    if (not isinstance(value, list)
            or any(not isinstance(item, str) or not item.strip()
                   for item in value)
            or len(set(value)) != len(value)):
        raise ValueError(f"{label} must contain unique nonempty strings")
    return list(value)


def validate_communication_task_performance_mapping(
    mapping: Mapping[str, Any], *, contract_sha256: str, task_id: str,
) -> str:
    """Validate the closed, evaluator-authored Stage71 mapping.

    The public task itself is not needed at manifest-freeze time: contract-v4
    already reports the digest of its exact pre-v4 base task, which must equal
    the manifest's frozen ``contract_sha256``.
    """
    if not isinstance(mapping, Mapping):
        raise ValueError("task_performance_mapping must be an object")
    _exact_fields(mapping, _MAPPING_FIELDS, "task_performance_mapping")
    if (mapping.get("schema") != TASK_PERFORMANCE_MAPPING_SCHEMA
            or mapping.get("version") != TASK_PERFORMANCE_MAPPING_VERSION
            or mapping.get("method_id") != TASK_PERFORMANCE_METHOD_ID):
        raise ValueError("task_performance_mapping schema/version/method is unknown")
    if mapping.get("metric_name") != "MCR_comm":
        raise ValueError("Stage71 task_performance_mapping supports only MCR_comm")
    if mapping.get("sample_selector") != "terminal":
        raise ValueError("task_performance_mapping sample_selector must be terminal")
    if (mapping.get("predicate_binding")
            != "contract_outcome_ref_equals_progress_predicate_id"):
        raise ValueError("task_performance_mapping predicate_binding is unknown")
    if mapping.get("unknown_policy") != "metric_na":
        raise ValueError("task_performance_mapping unknown_policy is unknown")
    if (mapping.get("revocation_policy")
            != "monotonic_contract_violation_na"):
        raise ValueError("task_performance_mapping revocation_policy is unknown")
    if mapping.get("stability_policy") != "require_zero_duration":
        raise ValueError("task_performance_mapping stability_policy is unknown")
    if mapping.get("higher_is_better") is not True:
        raise ValueError("task_performance_mapping higher_is_better must be true")
    for flag, expected in _RELEASE.items():
        if mapping.get(flag) is not expected:
            raise ValueError(f"task_performance_mapping {flag} must be false")

    source = mapping.get("progress_source")
    if not isinstance(source, Mapping):
        raise ValueError("task_performance_mapping progress_source must be an object")
    _exact_fields(source, _PROGRESS_SOURCE_FIELDS, "progress_source")
    if (source.get("schema") != PROGRESS_EVIDENCE_SCHEMA
            or source.get("version") != 1):
        raise ValueError("task_performance_mapping progress source is unsupported")
    _text(source.get("evaluator_ref"), "progress_source evaluator_ref")
    if source.get("clock_source") not in {
            "contract_clock", "native_simulation"}:
        raise ValueError("task_performance_mapping clock_source is unsupported")

    reasons = mapping.get("eligible_termination_reasons")
    if (not isinstance(reasons, list) or not reasons
            or any(not isinstance(reason, str) or not reason.strip()
                   for reason in reasons)
            or len(set(reasons)) != len(reasons)):
        raise ValueError(
            "eligible_termination_reasons must be unique nonempty strings")

    frozen = mapping.get("frozen_contract_v4")
    if not isinstance(frozen, Mapping):
        raise ValueError("task_performance_mapping frozen_contract_v4 is missing")
    validate_frozen_v4(frozen)
    report = validate_contract_v4(frozen["task"])
    _sha256(contract_sha256, "manifest contract_sha256")
    if report.get("base_task_sha256") != contract_sha256:
        raise ValueError("frozen contract-v4 base task differs from manifest")
    if report.get("task_id") != _text(task_id, "manifest task_id"):
        raise ValueError("frozen contract-v4 task_id differs from manifest")
    for milestone in frozen["task"][MILESTONES_FIELD]:
        milestone_id = milestone["milestone_id"]
        if milestone["activation_rule"] != ACTIVATION_EPISODE_START:
            raise ValueError(
                f"contract milestone {milestone_id} is not episode-start active")
        if milestone["monotonicity"] != "monotonic":
            raise ValueError(
                f"contract milestone {milestone_id} is not monotonic")
        stability = milestone["stability_duration_s"]
        if isinstance(stability, bool) or not isinstance(stability, (int, float)):
            raise ValueError(
                f"contract milestone {milestone_id} stability is invalid")
        if not math.isfinite(float(stability)) or float(stability) != 0.0:
            raise ValueError(
                f"contract milestone {milestone_id} must have zero stability")
        if milestone["anti_double_count_rule"] != _ANTI_DOUBLE_COUNT_RULE:
            raise ValueError(
                f"contract milestone {milestone_id} anti-double-count rule "
                "is unsupported")
    return canonical_sha256(mapping)


def _result_base(
    mapping: Mapping[str, Any], progress_report: Mapping[str, Any],
    public_task: Mapping[str, Any], episode_id: str,
) -> dict[str, Any]:
    frozen = mapping["frozen_contract_v4"]
    terminal = progress_report["samples"][-1]
    return {
        "schema": TASK_PERFORMANCE_REPLAY_SCHEMA,
        "version": TASK_PERFORMANCE_REPLAY_VERSION,
        "status": None,
        "na_reason": None,
        "metric_name": "MCR_comm",
        "SR_comm": None,
        "MCR_comm": None,
        "AUP_comm": None,
        "e_step": None,
        "mapping_spec_sha256": canonical_sha256(mapping),
        "frozen_contract_v4_seal_sha256": frozen["seal_sha256"],
        "episode_id": episode_id,
        "public_task_sha256": canonical_sha256(public_task),
        "progress_report_sha256": canonical_sha256(progress_report),
        "terminal_sample_sequence": terminal["sequence"],
        "eligible_termination": None,
        "unknown_predicate_ids": [],
        "monotonicity_violation_predicate_ids": [],
        "unselected_metrics": deepcopy(_UNSELECTED),
        "raw_only": True,
        "not_protocol_score": True,
        **_RELEASE,
    }


def validate_communication_task_performance_replay(
    replay: Mapping[str, Any], mapping: Mapping[str, Any], *,
    episode_id: str, public_task_sha256: str,
) -> str:
    """Validate a row-embedded replay claim without trusting its metric."""
    if not isinstance(replay, Mapping):
        raise ValueError("task performance replay must be an object")
    _exact_fields(replay, _REPLAY_FIELDS, "task performance replay")
    if (replay.get("schema") != TASK_PERFORMANCE_REPLAY_SCHEMA
            or replay.get("version") != TASK_PERFORMANCE_REPLAY_VERSION):
        raise ValueError("task performance replay schema/version is unknown")
    if replay.get("metric_name") != "MCR_comm":
        raise ValueError("task performance replay metric is not MCR_comm")
    mapping_sha = canonical_sha256(mapping)
    if replay.get("mapping_spec_sha256") != mapping_sha:
        raise ValueError("task performance replay mapping digest differs")
    frozen = mapping.get("frozen_contract_v4")
    seal = frozen.get("seal_sha256") if isinstance(frozen, Mapping) else None
    if replay.get("frozen_contract_v4_seal_sha256") != seal:
        raise ValueError("task performance replay contract seal differs")
    if replay.get("episode_id") != _text(episode_id, "episode_id"):
        raise ValueError("task performance replay episode differs")
    _sha256(public_task_sha256, "public_task_sha256")
    if replay.get("public_task_sha256") != public_task_sha256:
        raise ValueError("task performance replay public task differs")
    _sha256(replay.get("progress_report_sha256"),
            "task performance progress report digest")
    sequence = replay.get("terminal_sample_sequence")
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
        raise ValueError("task performance terminal sequence is invalid")
    if not isinstance(replay.get("eligible_termination"), bool):
        raise ValueError("task performance eligible_termination must be boolean")
    unknown = _string_list(
        replay.get("unknown_predicate_ids"), "unknown_predicate_ids")
    violations = _string_list(
        replay.get("monotonicity_violation_predicate_ids"),
        "monotonicity_violation_predicate_ids")
    outcomes = {row["outcome_ref"]
                for row in mapping["frozen_contract_v4"]["task"][
                    MILESTONES_FIELD]}
    if set(unknown) - outcomes or set(violations) - outcomes:
        raise ValueError("task performance replay references unknown predicates")
    if replay.get("unselected_metrics") != _UNSELECTED:
        raise ValueError("task performance replay unselected metrics differ")
    for metric in ("SR_comm", "AUP_comm", "e_step"):
        if replay.get(metric) is not None:
            raise ValueError(f"task performance replay {metric} must be null")
    if replay.get("raw_only") is not True or replay.get(
            "not_protocol_score") is not True:
        raise ValueError("task performance replay must remain raw-only")
    for flag, expected in _RELEASE.items():
        if replay.get(flag) is not expected:
            raise ValueError(f"task performance replay {flag} must be false")

    status = replay.get("status")
    value = replay.get("MCR_comm")
    if status == "computed":
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or not 0.0 <= float(value) <= 1.0):
            raise ValueError("computed task performance MCR is invalid")
        if (replay.get("na_reason") is not None
                or replay.get("eligible_termination") is not True
                or unknown or violations):
            raise ValueError("computed task performance replay is inconsistent")
    elif status == "na_ineligible_termination_reason":
        if value is not None or replay.get("eligible_termination") is not False:
            raise ValueError("ineligible termination replay is inconsistent")
    elif status == "na_monotonicity_violation":
        if (value is not None or replay.get("eligible_termination") is not True
                or not violations):
            raise ValueError("monotonicity N/A replay is inconsistent")
    elif status == "na_unknown_terminal_predicates":
        if (value is not None or replay.get("eligible_termination") is not True
                or not unknown or violations):
            raise ValueError("unknown predicate N/A replay is inconsistent")
    else:
        raise ValueError("task performance replay status is unknown")
    if status != "computed":
        _text(replay.get("na_reason"), "task performance replay na_reason")
    return canonical_sha256(replay)


def replay_communication_task_performance(
    mapping: Mapping[str, Any], progress_report: Mapping[str, Any],
    public_task: Mapping[str, Any], *, episode_id: str,
) -> dict[str, Any]:
    """Recompute the preregistered terminal MCR from raw progress facts."""
    if not isinstance(public_task, Mapping):
        raise ValueError("public_task must be an object")
    if not isinstance(progress_report, Mapping):
        raise ValueError("progress_report must be an object")
    episode_id = _text(episode_id, "episode_id")
    validate_communication_task_performance_mapping(
        mapping, contract_sha256=canonical_sha256(public_task),
        task_id=_text(public_task.get("task_id"), "public task_id"))
    replay = replay_progress_evidence(
        progress_report, public_task, episode_id=episode_id)
    if replay.get("validated") is not True or replay.get("status") != "observed":
        raise ValueError("progress evidence is not an observed replay source")
    samples = progress_report.get("samples")
    if (not isinstance(samples, list) or not samples
            or samples[0].get("boundary") != "initial"
            or samples[-1].get("boundary") != "terminal"):
        raise ValueError("progress evidence lacks initial/terminal boundaries")
    source = mapping["progress_source"]
    for sample in samples:
        if (sample.get("evaluator_ref") != source["evaluator_ref"]
                or sample.get("clock_source") != source["clock_source"]):
            raise ValueError(
                "progress sample evaluator/clock differs from frozen mapping")

    frozen = mapping["frozen_contract_v4"]["task"]
    milestones = list(frozen[MILESTONES_FIELD])
    outcomes = {row["outcome_ref"] for row in milestones}
    if set(progress_report.get("predicate_ids", [])) != outcomes:
        raise ValueError(
            "progress predicate ids differ from frozen contract-v4 outcomes")

    terminal_predicates = samples[-1]["predicates"]
    seen_true = {outcome_id: False for outcome_id in outcomes}
    false_after_true: set[str] = set()
    unknown_after_true: set[str] = set()
    for sample in samples:
        predicates = sample["predicates"]
        for outcome_id in outcomes:
            value = predicates[outcome_id]["satisfied"]
            if seen_true[outcome_id] and value is False:
                false_after_true.add(outcome_id)
            elif seen_true[outcome_id] and value is None:
                unknown_after_true.add(outcome_id)
            if value is True:
                seen_true[outcome_id] = True

    positive_unknown = sorted(
        milestone["outcome_ref"] for milestone in milestones
        if float(milestone["weight"]) > 0.0
        and terminal_predicates[milestone["outcome_ref"]]["satisfied"] is None)
    unknown_ids = sorted(set(positive_unknown) | unknown_after_true)
    violation_ids = sorted(false_after_true)
    termination = progress_report.get("termination", {})
    eligible = termination.get("reason") in set(
        mapping["eligible_termination_reasons"])

    result = _result_base(mapping, progress_report, public_task, episode_id)
    result["eligible_termination"] = eligible
    result["unknown_predicate_ids"] = unknown_ids
    result["monotonicity_violation_predicate_ids"] = violation_ids
    if not eligible:
        result["status"] = "na_ineligible_termination_reason"
        result["na_reason"] = (
            "actual termination reason is outside the preregistered domain")
        return result
    if violation_ids:
        result["status"] = "na_monotonicity_violation"
        result["na_reason"] = (
            "a monotonic contract predicate changed from true to false")
        return result
    if unknown_ids:
        result["status"] = "na_unknown_terminal_predicates"
        result["na_reason"] = (
            "positive-weight terminal predicates or post-true samples are unknown")
        return result

    total_weight = sum(float(row["weight"]) for row in milestones)
    completed_weight = sum(
        float(row["weight"]) for row in milestones
        if terminal_predicates[row["outcome_ref"]]["satisfied"] is True)
    result["status"] = "computed"
    result["MCR_comm"] = completed_weight / total_weight
    return result


__all__ = [
    "TASK_PERFORMANCE_MAPPING_SCHEMA", "TASK_PERFORMANCE_MAPPING_VERSION",
    "TASK_PERFORMANCE_METHOD_ID", "TASK_PERFORMANCE_REPLAY_SCHEMA",
    "TASK_PERFORMANCE_REPLAY_VERSION",
    "replay_communication_task_performance",
    "validate_communication_task_performance_mapping",
    "validate_communication_task_performance_replay",
]
