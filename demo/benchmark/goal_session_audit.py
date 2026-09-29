"""Opt-in call journal and independent replay for :class:`GoalSession`.

The audit wrapper uses composition so one host-level method call produces one
journal entry. Internal ``GoalSession`` events remain implementation details
and are verified through returned values and the final report instead of being
recorded a second time. Expected rejections and exceptions are evidence: replay
requires the same exception type and message and does not require every call,
skill, or business outcome to succeed.
"""

from __future__ import annotations

from copy import deepcopy
import math
from typing import Any, Mapping

from .goal_driven_contract import build_goal_participant_api
from .goal_session import GoalSession
from .provenance import canonical_sha256


GOAL_SESSION_AUDIT_SCHEMA = "eai.benchmark.goal_session_audit"
GOAL_SESSION_AUDIT_REPLAY_SCHEMA = "eai.benchmark.goal_session_audit_replay"
GOAL_SESSION_AUDIT_VERSION = 1

_METHOD_ARGUMENT_FIELDS = {
    "submit_action": {"action"},
    "record_action_rejection": {"action", "reason"},
    "emit": {"type", "time_s", "payload"},
    "validate_runtime_state": {"time_s", "robot_regions", "object_states"},
    "update_runtime_state": {"time_s", "robot_regions", "object_states"},
    "complete_skill": {"task_id", "time_s", "status", "receipt"},
    "complete_skill_delivery": {
        "task_id", "physical_completed_at_s", "delivered_at_s", "status", "receipt"},
    "record_deployment_assessment_evaluation": {"evaluation"},
    "initialize_deployment_runtime_state": {"robot_regions", "object_states"},
    "record_business_outcome": {
        "outcome_id", "status", "evidence_refs", "evaluator_ref"},
}
_CALL_FIELDS = {
    "index", "method", "arguments", "special_floats", "outcome", "result",
    "exception", "previous_call_sha256", "call_sha256",
}
_JOURNAL_FIELDS = {
    "schema", "version", "episode_id", "public_task_sha256",
    "participant_api_sha256", "calls", "final_report",
    "final_report_sha256", "execution_validated",
    "business_outcomes_validated", "scoreable", "integrity_scope",
    "journal_sha256",
}
_REPLAY_FIELDS = {
    "schema", "version", "episode_id", "journal_sha256", "calls_replayed",
    "returned_calls_replayed", "raised_calls_replayed", "final_report_sha256",
    "validated", "error", "execution_validated",
    "business_outcomes_validated", "scoreable", "integrity_scope",
}
_HASH_CHARS = frozenset("0123456789abcdef")


def _strict_fields(value: Any, fields: set[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise ValueError(f"{label} fields are incomplete or unknown")
    return value


def _sha256(value: Any, label: str) -> str:
    if (not isinstance(value, str) or len(value) != 64
            or any(char not in _HASH_CHARS for char in value)):
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _encode_special_floats(value: Any) -> tuple[Any, list[dict[str, Any]]]:
    """Keep the journal strict JSON while preserving rejected NaN/Inf calls."""
    special: list[dict[str, Any]] = []

    def visit(item: Any, path: list[str | int]) -> Any:
        if isinstance(item, float) and not math.isfinite(item):
            label = "nan" if math.isnan(item) else ("positive_infinity" if item > 0
                                                     else "negative_infinity")
            special.append({"path": list(path), "value": label})
            return None
        if isinstance(item, Mapping):
            if any(not isinstance(key, str) for key in item):
                raise ValueError("audited GoalSession mapping keys must be strings")
            return {key: visit(child, [*path, key]) for key, child in item.items()}
        if isinstance(item, (list, tuple)):
            return [visit(child, [*path, index]) for index, child in enumerate(item)]
        return deepcopy(item)

    encoded = visit(value, [])
    canonical_sha256(encoded)
    return encoded, special


def _decode_special_floats(
    value: Any, special: list[Mapping[str, Any]],
) -> Any:
    replacements: dict[tuple[str | int, ...], float] = {}
    names = {
        "nan": float("nan"),
        "positive_infinity": float("inf"),
        "negative_infinity": float("-inf"),
    }
    for row in special:
        _strict_fields(row, {"path", "value"}, "special float")
        path = row["path"]
        if (not isinstance(path, list)
                or any(isinstance(part, bool) or not isinstance(part, (str, int))
                       for part in path)
                or row["value"] not in names):
            raise ValueError("special float path or value is invalid")
        key = tuple(path)
        if key in replacements:
            raise ValueError("special float paths must be unique")
        replacements[key] = names[row["value"]]
    consumed: set[tuple[str | int, ...]] = set()

    def visit(item: Any, path: tuple[str | int, ...]) -> Any:
        if path in replacements:
            if item is not None:
                raise ValueError("special float placeholder must be null")
            consumed.add(path)
            return replacements[path]
        if isinstance(item, Mapping):
            return {key: visit(child, (*path, key)) for key, child in item.items()}
        if isinstance(item, list):
            return [visit(child, (*path, index)) for index, child in enumerate(item)]
        return deepcopy(item)

    decoded = visit(value, ())
    if consumed != set(replacements):
        raise ValueError("special float path does not reference an argument value")
    return decoded


def _call_digest(row: Mapping[str, Any]) -> str:
    return canonical_sha256({key: deepcopy(value) for key, value in row.items()
                             if key != "call_sha256"})


def _journal_digest(journal: Mapping[str, Any]) -> str:
    return canonical_sha256({key: deepcopy(value) for key, value in journal.items()
                             if key != "journal_sha256"})


class AuditedGoalSession:
    """Composition proxy that records each external ``GoalSession`` mutation."""

    def __init__(self, task_spec: Mapping[str, Any], api: Mapping[str, Any] | None = None,
                 *, episode_id: str | None = None, admission: str = "full"):
        effective_api = (deepcopy(dict(api)) if api is not None
                         else build_goal_participant_api(task_spec))
        self._public_task = deepcopy(dict(task_spec))
        self._participant_api = effective_api
        self._session = GoalSession(
            self._public_task, self._participant_api, episode_id=episode_id,
            admission=admission)
        self._episode_id = episode_id
        self._calls: list[dict[str, Any]] = []

    def _invoke(self, method: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        encoded, special = _encode_special_floats(deepcopy(dict(arguments)))
        previous = self._calls[-1]["call_sha256"] if self._calls else None
        base = {
            "index": len(self._calls),
            "method": method,
            "arguments": encoded,
            "special_floats": special,
            "previous_call_sha256": previous,
        }
        try:
            result = getattr(self._session, method)(**deepcopy(dict(arguments)))
        except Exception as exc:
            row = {
                **base,
                "outcome": "raised",
                "result": None,
                "exception": {"type": type(exc).__name__, "message": str(exc)},
            }
            row["call_sha256"] = _call_digest(row)
            self._calls.append(row)
            raise
        row = {
            **base,
            "outcome": "returned",
            "result": deepcopy(result),
            "exception": None,
        }
        row["call_sha256"] = _call_digest(row)
        self._calls.append(row)
        return deepcopy(result)

    def assert_compatible(self, task_spec: Mapping[str, Any], api: Mapping[str, Any]) -> None:
        self._session.assert_compatible(task_spec, api)

    def report(self) -> dict[str, Any]:
        return self._session.report()

    def submit_action(self, action: Mapping[str, Any]) -> dict[str, Any]:
        return self._invoke("submit_action", {"action": action})

    def record_action_rejection(
        self, action: Mapping[str, Any], *, reason: str,
    ) -> dict[str, Any]:
        return self._invoke(
            "record_action_rejection", {"action": action, "reason": reason})

    def emit(self, type: str, time_s: float,
             payload: Mapping[str, Any]) -> dict[str, Any]:
        return self._invoke(
            "emit", {"type": type, "time_s": time_s, "payload": payload})

    def validate_runtime_state(
        self, time_s: float, robot_regions: Mapping[str, str],
        object_states: Mapping[str, Mapping[str, Any]],
    ) -> dict[str, Any]:
        return self._invoke("validate_runtime_state", {
            "time_s": time_s,
            "robot_regions": robot_regions,
            "object_states": object_states,
        })

    def update_runtime_state(
        self, time_s: float, robot_regions: Mapping[str, str],
        object_states: Mapping[str, Mapping[str, Any]],
    ) -> dict[str, Any]:
        return self._invoke("update_runtime_state", {
            "time_s": time_s,
            "robot_regions": robot_regions,
            "object_states": object_states,
        })

    def initialize_deployment_runtime_state(
        self, robot_regions: Mapping[str, str],
        object_states: Mapping[str, Mapping[str, Any]],
    ) -> dict[str, Any]:
        return self._invoke("initialize_deployment_runtime_state", {
            "robot_regions": robot_regions, "object_states": object_states,
        })

    def complete_skill(
        self, task_id: str, *, time_s: float, status: str,
        receipt: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        return self._invoke("complete_skill", {
            "task_id": task_id,
            "time_s": time_s,
            "status": status,
            "receipt": receipt,
        })

    def complete_skill_delivery(
        self,
        task_id: str,
        *,
        physical_completed_at_s: float,
        delivered_at_s: float,
        status: str,
        receipt: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        return self._invoke("complete_skill_delivery", {
            "task_id": task_id,
            "physical_completed_at_s": physical_completed_at_s,
            "delivered_at_s": delivered_at_s,
            "status": status,
            "receipt": receipt,
        })

    def record_deployment_assessment_evaluation(
        self, evaluation: Mapping[str, Any],
    ) -> dict[str, Any]:
        return self._invoke(
            "record_deployment_assessment_evaluation",
            {"evaluation": evaluation})

    def record_business_outcome(
        self, outcome_id: str, *, status: str,
        evidence_refs: list[str], evaluator_ref: str,
    ) -> dict[str, Any]:
        return self._invoke("record_business_outcome", {
            "outcome_id": outcome_id,
            "status": status,
            "evidence_refs": evidence_refs,
            "evaluator_ref": evaluator_ref,
        })

    def audit_journal(self) -> dict[str, Any]:
        """Seal an immutable snapshot without preventing later host calls."""
        final_report = self.report()
        journal = {
            "schema": GOAL_SESSION_AUDIT_SCHEMA,
            "version": GOAL_SESSION_AUDIT_VERSION,
            "episode_id": self._episode_id,
            "public_task_sha256": canonical_sha256(self._public_task),
            "participant_api_sha256": canonical_sha256(self._participant_api),
            "calls": deepcopy(self._calls),
            "final_report": final_report,
            "final_report_sha256": canonical_sha256(final_report),
            "execution_validated": False,
            "business_outcomes_validated": False,
            "scoreable": False,
            "integrity_scope": "internal_consistency_not_physical_authentication",
        }
        journal["journal_sha256"] = _journal_digest(journal)
        return validate_goal_session_audit(journal)


def validate_goal_session_audit(journal: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the closed schema, hash chain, and final report binding."""
    journal = _strict_fields(journal, _JOURNAL_FIELDS, "goal session audit")
    if (journal.get("schema") != GOAL_SESSION_AUDIT_SCHEMA
            or type(journal.get("version")) is not int
            or journal["version"] != GOAL_SESSION_AUDIT_VERSION):
        raise ValueError("goal session audit schema/version mismatch")
    episode_id = journal["episode_id"]
    if episode_id is not None and (
            not isinstance(episode_id, str) or not episode_id.strip()):
        raise ValueError("goal session audit episode_id is invalid")
    _sha256(journal["public_task_sha256"], "public_task_sha256")
    _sha256(journal["participant_api_sha256"], "participant_api_sha256")
    if any(journal.get(flag) is not False for flag in (
            "execution_validated", "business_outcomes_validated", "scoreable")):
        raise ValueError("goal session audit cannot promote release flags")
    if journal.get("integrity_scope") != (
            "internal_consistency_not_physical_authentication"):
        raise ValueError("goal session audit integrity scope is invalid")
    calls = journal["calls"]
    if not isinstance(calls, list):
        raise ValueError("goal session audit calls must be a list")
    previous = None
    for index, row in enumerate(calls):
        row = _strict_fields(row, _CALL_FIELDS, "goal session audit call")
        if type(row["index"]) is not int or row["index"] != index:
            raise ValueError("goal session audit call index is invalid")
        method = row["method"]
        if method not in _METHOD_ARGUMENT_FIELDS:
            raise ValueError("goal session audit method is invalid")
        arguments = row["arguments"]
        if (not isinstance(arguments, Mapping)
                or set(arguments) != _METHOD_ARGUMENT_FIELDS[method]):
            raise ValueError("goal session audit method arguments are incomplete or unknown")
        if not isinstance(row["special_floats"], list):
            raise ValueError("goal session audit special_floats must be a list")
        _decode_special_floats(arguments, row["special_floats"])
        if row["previous_call_sha256"] != previous:
            raise ValueError("goal session audit call hash chain is broken")
        if row["outcome"] == "returned":
            if row["exception"] is not None:
                raise ValueError("returned audit call cannot contain an exception")
            canonical_sha256(row["result"])
        elif row["outcome"] == "raised":
            if row["result"] is not None:
                raise ValueError("raised audit call cannot contain a result")
            exception = _strict_fields(
                row["exception"], {"type", "message"}, "audit exception")
            if (not isinstance(exception["type"], str) or not exception["type"]
                    or not isinstance(exception["message"], str)):
                raise ValueError(
                    "audit exception type must be nonempty and message must be a string")
        else:
            raise ValueError("goal session audit call outcome is invalid")
        digest = _sha256(row["call_sha256"], "call_sha256")
        if digest != _call_digest(row):
            raise ValueError("goal session audit call digest mismatch")
        previous = digest
    if not isinstance(journal["final_report"], Mapping):
        raise ValueError("goal session audit final_report must be an object")
    final_digest = _sha256(
        journal["final_report_sha256"], "final_report_sha256")
    if final_digest != canonical_sha256(journal["final_report"]):
        raise ValueError("goal session audit final report digest mismatch")
    digest = _sha256(journal["journal_sha256"], "journal_sha256")
    if digest != _journal_digest(journal):
        raise ValueError("goal session audit journal digest mismatch")
    return deepcopy(dict(journal))


def replay_goal_session_audit(
    journal: Mapping[str, Any], public_task: Mapping[str, Any],
    participant_api: Mapping[str, Any],
) -> dict[str, Any]:
    """Independently replay every archived call from the original inputs."""
    journal = validate_goal_session_audit(journal)
    if canonical_sha256(public_task) != journal["public_task_sha256"]:
        raise ValueError("goal session audit public task digest mismatch")
    if canonical_sha256(participant_api) != journal["participant_api_sha256"]:
        raise ValueError("goal session audit participant API digest mismatch")
    session = GoalSession(
        public_task, participant_api, episode_id=journal["episode_id"])
    returned = 0
    raised = 0
    for row in journal["calls"]:
        arguments = _decode_special_floats(
            row["arguments"], row["special_floats"])
        try:
            actual = getattr(session, row["method"])(**arguments)
        except Exception as exc:
            if row["outcome"] != "raised":
                raise ValueError(
                    f"goal session replay call {row['index']} unexpectedly raised "
                    f"{type(exc).__name__}: {exc}") from exc
            expected = row["exception"]
            if (type(exc).__name__ != expected["type"]
                    or str(exc) != expected["message"]):
                raise ValueError(
                    f"goal session replay exception mismatch at call {row['index']}") from exc
            raised += 1
            continue
        if row["outcome"] != "returned":
            raise ValueError(
                f"goal session replay call {row['index']} unexpectedly returned")
        if canonical_sha256(actual) != canonical_sha256(row["result"]):
            raise ValueError(
                f"goal session replay result mismatch at call {row['index']}")
        returned += 1
    final_report = session.report()
    if canonical_sha256(final_report) != journal["final_report_sha256"]:
        raise ValueError("goal session replay final report mismatch")
    replay = {
        "schema": GOAL_SESSION_AUDIT_REPLAY_SCHEMA,
        "version": GOAL_SESSION_AUDIT_VERSION,
        "episode_id": journal["episode_id"],
        "journal_sha256": journal["journal_sha256"],
        "calls_replayed": len(journal["calls"]),
        "returned_calls_replayed": returned,
        "raised_calls_replayed": raised,
        "final_report_sha256": canonical_sha256(final_report),
        "validated": True,
        "error": None,
        "execution_validated": False,
        "business_outcomes_validated": False,
        "scoreable": False,
        "integrity_scope": "internal_consistency_not_physical_authentication",
    }
    _strict_fields(replay, _REPLAY_FIELDS, "goal session audit replay")
    return replay


__all__ = [
    "AuditedGoalSession", "GOAL_SESSION_AUDIT_REPLAY_SCHEMA",
    "GOAL_SESSION_AUDIT_SCHEMA", "GOAL_SESSION_AUDIT_VERSION",
    "replay_goal_session_audit", "validate_goal_session_audit",
]
