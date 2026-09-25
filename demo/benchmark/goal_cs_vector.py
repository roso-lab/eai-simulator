"""Protocol 3.1 constraint-satisfaction vector: committed-plan evidence and cap/role/mile verdicts.

Batch 2B of the protocol conformance plan.  Protocol section 3.1 ("约束检查器
与可解释证据" / "核心结果与定义") fixes one three-dimensional vector per
episode,

.. math::

    \\mathbf{CS}_{is}=(G_{is}\\tfrac{\\sum c^{cap}}{|\\mathcal C^{cap}|},
    G_{is}\\tfrac{\\sum c^{role}}{|\\mathcal C^{role}|},
    G_{is}\\tfrac{\\sum y_m(H)}{|\\mathcal M^{req}|}),

with the entry gate
:math:`G_{is}=\\mathbf 1[T^{state}=deployable \\land D=deployable \\land
O^{commit}=1]`.  This module implements the three finite checks the protocol
assigns to the evaluator: structural capability thresholds from a
``committed_plan`` plus a robot registry, platform-role cardinalities from a
platform registry, and required-milestone outcomes from caller-supplied
external predicate results.  Three pieces come together here:

* :func:`extract_committed_plan_evidence` derives the protocol 2.7
  ``committed_plan`` evidence from a GoalSession report.  The locked version
  is the *last parsable accepted* ``submit_plan``/``revise_plan`` before the
  first accepted ``dispatch_skill``; when the episode never dispatched, it is
  the last parsable plan at or before ``dispatch_deadline_s`` (or the last
  parsable plan overall when no deadline is supplied - the same committed
  reading the batch-0 ``no_committed_plan`` label uses).  Unparsable reports
  or no qualifying plan reuse the batch-0 word ``no_committed_plan``.
* :func:`constraint_satisfaction_vector` judges the activated obligations.
  Activation follows the batch-2A deterministic vocabulary
  (``episode_start_active`` only); any other declared activation rule is an
  explicit exogenous-evaluation gap, never a guessed verdict.  A dispatch
  that is inconsistent with the locked committed plan zeroes the affected
  capability/role obligations (protocol 3.1 step 5); an inconsistency whose
  task carries no parsable milestone reference cannot be attributed, so it
  conservatively affects every obligation judged against that plan.
* :func:`register_predicate_binding` / :func:`validate_predicate_bindings`
  seal a milestone ``predicate_ref`` -> trusted evaluator binding
  (``evaluator_ref`` plus a parameter digest) against the exact contract
  hash.  A required milestone without a bound evaluator is reported
  ``predicate_unbound`` and is never guessed from an unprovenanced result.

Interpretation notes the protocol text leaves open, frozen here and pinned by
tests:

* ``single_actor`` means the obligation is borne by exactly one actor whose
  own capability value meets the threshold (no coalition, no summing);
  ``exists``/``all`` judge the bearers individually, ``coalition_sum`` sums
  bearer values within the contract tolerance.  This keeps the four
  aggregation rules of protocol 2.6/3.1 mutually distinct.
* numeric tolerance/boundary are parsed from the frozen ``tolerance`` string
  (``numeric tolerance <x>``/``boundary inclusive|exclusive``); a missing
  pattern means zero tolerance with inclusive boundary.
* a numeric registry value never evidences a functional (string) threshold
  and vice versa - the mismatch fails closed instead of coercing.

The GoalSession six-action/eight-event frozen surface is untouched: this
module only reads session reports.  ``milestone_ref`` is derived from
explicit goal references in plan tasks (``milestone_id``/``outcome_id``);
the frozen v2/v3 plan schema carries none, so its evidence rows keep
``milestone_ref=None`` and capability/role obligations stay unattributed
until contracts and plans publish the mapping - a documented gap, not a
fabricated verdict.  Everything here is raw judgement evidence:
release-gated false, never a protocol score.
"""

from __future__ import annotations

import math
import re
from copy import deepcopy
from typing import Any, Mapping

from .goal_contract_v4 import (
    ACTIVATION_EPISODE_START,
    CAPABILITY_CONSTRAINTS_FIELD,
    CONTRACT_V4_RELEASE,
    CS_GATE_NO_COMMITTED_PLAN_STATUS,
    CS_GATE_NO_CONTRACT_STATUS,
    MILESTONES_FIELD,
    ROLE_CONSTRAINTS_FIELD,
    validate_contract_v4,
)
from .goal_protocol_deployment import (
    CONSTRAINT_FAILURE_LABELS,
    TRUTH_INFEASIBLE,
    normalize_oracle_truth_label,
)
from .provenance import canonical_sha256


COMMITTED_PLAN_EVIDENCE_SCHEMA = "eai.benchmark.committed_plan_evidence"
COMMITTED_PLAN_EVIDENCE_VERSION = 1
CS_VECTOR_SCHEMA = "eai.benchmark.cs_vector"
CS_VECTOR_VERSION = 1
PREDICATE_BINDING_SCHEMA = "eai.benchmark.predicate_binding_registry"
PREDICATE_BINDING_VERSION = 1

# Extractor statuses and lock rules.
EVIDENCE_STATUS_LOCKED = "committed_plan_locked"
EVIDENCE_STATUS_NO_PLAN = CS_GATE_NO_COMMITTED_PLAN_STATUS
LOCK_RULE_BEFORE_FIRST_DISPATCH = "last_parsable_plan_before_first_dispatch"
LOCK_RULE_BEFORE_DEADLINE = "last_parsable_plan_before_deadline"
LOCK_RULE_NO_DISPATCH_NO_DEADLINE = "last_parsable_plan_no_dispatch_no_deadline"

# Judge statuses and component statuses.
CS_STATUS_COMPUTED = "computed"
CS_STATUS_TEAM_STATE_INFEASIBLE = "team_state_infeasible_not_defined"
COMPONENT_COMPUTED = "computed"
COMPONENT_ZEROED_BY_GATE = "zeroed_by_gate"
COMPONENT_NA_EMPTY_SET = "N/A_empty_activation_set"
COMPONENT_NOT_EVALUABLE_NO_CONTRACT = "not_evaluable_no_contract_v4"
COMPONENT_NOT_DEFINED_INFEASIBLE = "not_defined_team_state_infeasible"

# Protocol failure-label vocabulary (drift guard against batch 0).
FAILURE_LABEL_CAPABILITY = "capability_constraint_violation"
FAILURE_LABEL_ROLE = "role_constraint_violation"
FAILURE_LABEL_MILESTONE = "required_milestone_unmet"
assert {FAILURE_LABEL_CAPABILITY, FAILURE_LABEL_ROLE,
        FAILURE_LABEL_MILESTONE, EVIDENCE_STATUS_NO_PLAN} <= set(
            CONSTRAINT_FAILURE_LABELS)

# Atomic violation reasons (stable, machine-readable).
REASON_DISPATCH_INCONSISTENT = "dispatch_inconsistent_with_committed_plan"
REASON_NO_SUBTASK_FOR_MILESTONE = "no_committed_subtask_for_milestone"
REASON_REGISTRY_AGENT_MISSING = "robot_registry_missing_agent"
REASON_SINGLE_ACTOR_BEARERS = "single_actor_requires_exactly_one_bearer"
REASON_CAP_UNMET = "capability_threshold_unmet"
REASON_CAP_UNMET_ANY = "capability_threshold_unmet_by_any_bearer"
REASON_CAP_UNMET_SOME = "capability_threshold_unmet_by_some_bearer"
REASON_CAP_NOT_DECLARED = "capability_not_declared_by_bearer"
REASON_CAP_NOT_DECLARED_ANY = "capability_not_declared_by_any_bearer"
REASON_CAP_NOT_DECLARED_SOME = "capability_not_declared_by_some_bearer"
REASON_COALITION_BELOW = "coalition_sum_below_threshold"
REASON_ROLE_CARDINALITY = "role_cardinality_unmet"
REASON_PREDICATE_UNBOUND = "predicate_unbound"
REASON_MILESTONE_RESULT_MISSING = "milestone_result_missing"
REASON_MILESTONE_UNMET = "required_milestone_unmet"

# Gate failure reasons.
GATE_REASON_NO_CONTRACT = "not_evaluable_no_contract_v4"
GATE_REASON_NO_PLAN = "no_committed_plan"
GATE_REASON_TRUTH = "truth_state_not_deployable_or_unknown"
GATE_REASON_DECLARATION = "declared_decision_not_deployable"

_PLAN_ACTION_TYPES = frozenset({"submit_plan", "revise_plan"})
_GOAL_REFERENCE_KEYS = ("milestone_id", "outcome_id")
_REGISTRY_ENTRY_FIELDS = frozenset({"role_id", "capabilities"})
_BINDING_ENTRY_FIELDS = frozenset({"predicate_ref", "evaluator_ref",
                                   "parameters_digest"})
_BINDING_INPUT_FIELDS = frozenset({"evaluator_ref", "parameters_digest"})
# Release annotations ride along outside the seal (constant false flags) and
# are tolerated when a sealed registry is read back.
_REGISTRY_ANNOTATION_FIELDS = frozenset(CONTRACT_V4_RELEASE) | {
    "not_protocol_score"}
_TOLERANCE_PATTERN = re.compile(
    r"numeric\s+tolerance\s+(-?\d+(?:\.\d+)?)", re.IGNORECASE)
_BOUNDARY_PATTERN = re.compile(r"boundary\s+(inclusive|exclusive)",
                               re.IGNORECASE)
_CS_FORMULA = (
    "CS = (G*sum(c_cap)/|C_cap|, G*sum(c_role)/|C_role|, "
    "G*sum(y_m)/|M_req|); G = 1[T_state=deployable and D=deployable "
    "and O_commit=1]; empty activation set -> N/A, never 0 or 1")


# ---------------------------------------------------------------------------
# Small typed readers (fail closed on caller-shaped input)
# ---------------------------------------------------------------------------


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return value


def _nonempty_str(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a nonempty string")
    return value


def _finite_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{label} must be a finite number")
    return result


def _optional_time(value: Any, label: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{label} must be a finite number")
    return result


def _milestone_ref_of(task: Mapping[str, Any] | None) -> str | None:
    """Derive the milestone reference from explicit goal references only.

    The frozen v2/v3 plan task schema has no milestone field; a reference is
    only ever read from an explicit ``milestone_id``/``outcome_id`` key on
    the task or inside its arguments.  Free text, skills and object/region
    ids are never mined for a reference (protocol: the mapping is a system
    obligation, not an evaluator guess).
    """
    if not isinstance(task, Mapping):
        return None
    sources: list[Mapping[str, Any]] = [task]
    arguments = task.get("arguments")
    if isinstance(arguments, Mapping):
        sources.append(arguments)
    for source in sources:
        for key in _GOAL_REFERENCE_KEYS:
            value = source.get(key)
            if isinstance(value, str) and value:
                return value
    return None


def _release(**extra: Any) -> dict[str, Any]:
    return {"not_protocol_score": True, **CONTRACT_V4_RELEASE, **extra}


# ---------------------------------------------------------------------------
# Committed-plan evidence extractor (protocol 2.7 / 3.1 lock rule)
# ---------------------------------------------------------------------------


def _accepted_plan_and_dispatch_ledger(
        session_report: Mapping[str, Any]) -> tuple[list[dict[str, Any]],
                                                    dict[str, Any] | None,
                                                    str | None]:
    """Read accepted plan submissions and the first accepted dispatch.

    Returns ``(plan_events, first_dispatch, unparsable_reason)`` where
    ``plan_events`` are ``{"sequence", "time_s", "action_id"}`` rows sorted by
    event sequence and ``first_dispatch`` carries the sequence/time of the
    first accepted ``dispatch_skill``.  A report without parsable
    events/actions lists yields the fail-closed reason instead.
    """
    events = session_report.get("events")
    raw_actions = session_report.get("actions")
    if not isinstance(events, list) or not isinstance(raw_actions, list):
        return [], None, "report_missing_action_event_ledger"
    action_types: dict[str, str] = {}
    for row in raw_actions:
        if isinstance(row, Mapping) and isinstance(row.get("action_id"), str):
            action_types[row["action_id"]] = row.get("type")
    plan_events: list[dict[str, Any]] = []
    first_dispatch: dict[str, Any] | None = None
    for event in events:
        if not isinstance(event, Mapping) or event.get("type") != "action_result":
            continue
        payload = event.get("payload")
        if not isinstance(payload, Mapping) or payload.get("status") != "accepted":
            continue
        sequence = event.get("sequence")
        if type(sequence) is not int:
            continue
        time_s = _optional_time(event.get("time_s"),
                                "accepted action_result time_s")
        action_type = action_types.get(payload.get("action_id"))
        if action_type == "dispatch_skill" and first_dispatch is None:
            first_dispatch = {"sequence": sequence,
                              "time_s": time_s if time_s is not None else 0.0}
        elif action_type in _PLAN_ACTION_TYPES:
            plan_events.append({
                "sequence": sequence,
                "time_s": time_s if time_s is not None else 0.0,
                "action_id": payload.get("action_id"),
            })
    plan_events.sort(key=lambda row: row["sequence"])
    return plan_events, first_dispatch, None


def _stored_plans(session_report: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    rows = session_report.get("plans")
    stored: dict[str, Mapping[str, Any]] = {}
    if not isinstance(rows, list):
        return stored
    for row in rows:
        if (isinstance(row, Mapping) and isinstance(row.get("action_id"), str)
                and isinstance(row.get("plan"), Mapping)):
            stored[row["action_id"]] = row
    return stored


def _parsable_plan(plan: Any) -> bool:
    """Structural parsability: the fields the protocol 2.7 evidence needs."""
    if not isinstance(plan, Mapping):
        return False
    if not isinstance(plan.get("plan_id"), str) or not plan["plan_id"]:
        return False
    team = plan.get("assigned_team")
    if not isinstance(team, list) or not team or any(
            not isinstance(agent, str) or not agent for agent in team):
        return False
    tasks = plan.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        return False
    for task in tasks:
        if not isinstance(task, Mapping):
            return False
        if not isinstance(task.get("task_id"), str) or not task["task_id"]:
            return False
        agents = task.get("assigned_agents")
        if not isinstance(agents, list) or not agents or any(
                not isinstance(agent, str) or not agent for agent in agents):
            return False
    try:
        canonical_sha256(dict(plan))
    except (TypeError, ValueError):
        return False
    return True


def _declarations(session_report: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    rows = session_report.get("current_robot_declarations")
    if not isinstance(rows, Mapping):
        return {}
    return {agent: row for agent, row in rows.items()
            if isinstance(agent, str) and agent and isinstance(row, Mapping)}


def _subtask_rows(plan: Mapping[str, Any],
                  declarations: dict[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    plan_sha256 = canonical_sha256(dict(plan))
    rows: list[dict[str, Any]] = []
    for task in plan["tasks"]:
        robot_ids = [agent for agent in task["assigned_agents"]
                     if isinstance(agent, str) and agent]
        declared: dict[str, list[str]] = {}
        for agent in robot_ids:
            entry = declarations.get(agent)
            capabilities = entry.get("public_capabilities") if entry else None
            if isinstance(capabilities, list):
                declared[agent] = sorted(
                    capability for capability in capabilities
                    if isinstance(capability, str) and capability)
        categories = sorted({
            entry.get("type") for entry in (declarations.get(a) for a in robot_ids)
            if isinstance(entry, Mapping)
            and isinstance(entry.get("type"), str) and entry.get("type")})
        rows.append({
            "subtask_id": task["task_id"],
            "task_id": task["task_id"],
            "milestone_ref": _milestone_ref_of(task),
            "robot_ids": robot_ids,
            "declared_capabilities": declared,
            "role_id": categories[0] if len(categories) == 1 else None,
            "role_ids": categories,
            "plan_sha256": plan_sha256,
        })
    return rows


def _dispatch_rows(session_report: Mapping[str, Any]) -> list[dict[str, Any]]:
    tasks = session_report.get("tasks")
    rows: list[dict[str, Any]] = []
    if not isinstance(tasks, Mapping):
        return rows
    for task_id, row in tasks.items():
        if not isinstance(row, Mapping) or not isinstance(task_id, str):
            continue
        if row.get("dispatch_action_id") is None and row.get("dispatched_at_s") is None:
            continue
        task = row.get("task")
        agents = task.get("assigned_agents") if isinstance(task, Mapping) else None
        rows.append({
            "task_id": task_id,
            "agent_ids": ([agent for agent in agents
                           if isinstance(agent, str) and agent]
                          if isinstance(agents, list) else []),
            "dispatched_at_s": _optional_time(row.get("dispatched_at_s"),
                                              "dispatched_at_s"),
            "plan_version": row.get("plan_version"),
            "milestone_ref": _milestone_ref_of(task),
        })
    rows.sort(key=lambda row: (row["dispatched_at_s"] is None,
                               row["dispatched_at_s"] or 0.0,
                               row["task_id"]))
    return rows


def _consistency_rows(locked_plan: Mapping[str, Any], locked_version: Any,
                      dispatches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Compare every actual dispatch against the locked committed plan.

    Protocol 3.1: a dispatch whose robots/coalition differ from the locked
    ``committed_plan`` version - including work first introduced by a later
    revision - is an inconsistency that later zeroes the affected capability
    or role obligations.
    """
    locked_tasks = {task["task_id"]: task for task in locked_plan["tasks"]}
    entries: list[dict[str, Any]] = []
    for dispatch in dispatches:
        locked_task = locked_tasks.get(dispatch["task_id"])
        issue: str | None = None
        locked_agents: list[str] | None = None
        if locked_task is None:
            issue = "task_not_in_locked_plan"
        elif dispatch.get("plan_version") != locked_version:
            issue = "dispatched_plan_version_differs"
            locked_agents = list(locked_task["assigned_agents"])
        elif sorted(dispatch.get("agent_ids") or []) != sorted(
                locked_task["assigned_agents"]):
            issue = "agent_mismatch"
            locked_agents = list(locked_task["assigned_agents"])
        if issue is None:
            continue
        entries.append({
            "task_id": dispatch["task_id"],
            "issue": issue,
            "milestone_ref": dispatch.get("milestone_ref"),
            "locked_plan_version": locked_version,
            "locked_plan_agent_ids": locked_agents,
            "dispatched_plan_version": dispatch.get("plan_version"),
            "dispatched_agent_ids": list(dispatch.get("agent_ids") or []),
        })
    return entries


def _deployment_terms(session_report: Mapping[str, Any]) -> tuple[str | None,
                                                                  str | None]:
    """Read the latest declaration D and oracle truth T_state, if archived.

    Only the initial (episode-start) deployment boundary is interpreted;
    later reassessment epochs are a documented gap and simply leave the most
    recent archived words in place.
    """
    deployment = session_report.get("deployment_assessment")
    declared: str | None = None
    truth: str | None = None
    if not isinstance(deployment, Mapping):
        return declared, truth
    history = deployment.get("history")
    if isinstance(history, list):
        for row in reversed(history):
            if isinstance(row, Mapping) and isinstance(row.get("decision"), str):
                declared = row["decision"]
                break
    evaluations = deployment.get("oracle_evaluations")
    if isinstance(evaluations, list):
        for row in reversed(evaluations):
            if isinstance(row, Mapping):
                label = normalize_oracle_truth_label(row.get("feasibility_label"))
                if label is not None:
                    truth = label
                    break
    return declared, truth


def extract_committed_plan_evidence(session_report: Mapping[str, Any], *,
                                    dispatch_deadline_s: float | None = None
                                    ) -> dict[str, Any]:
    """Derive the protocol 2.7 ``committed_plan`` evidence from a session report.

    Lock rule (protocol 3.1): the last parsable accepted
    ``submit_plan``/``revise_plan`` before the first accepted
    ``dispatch_skill``; with no dispatch in the episode, the last parsable
    plan at or before ``dispatch_deadline_s`` (plan-submission cutoff), or
    the last parsable plan overall when no cutoff is supplied.  Unparsable
    reports, no qualifying plan, or no parsable stored plan payload yield
    ``status=no_committed_plan`` - the exact batch-0 failure word.  The
    output also carries the latest archived deployment declaration and
    oracle truth label (``declared_decision``/``truth_state``) so the CS
    gate can evaluate :math:`G` without a second archive read.
    """
    session_report = _mapping(session_report, "session_report")
    deadline = _optional_time(dispatch_deadline_s, "dispatch_deadline_s")
    base: dict[str, Any] = {
        "schema": COMMITTED_PLAN_EVIDENCE_SCHEMA,
        "version": COMMITTED_PLAN_EVIDENCE_VERSION,
        "protocol_section": "2.7+3.1",
        "status": EVIDENCE_STATUS_LOCKED,
        "no_committed_plan": False,
        "no_committed_plan_reason": None,
        "plan_version": None,
        "plan_id": None,
        "locked_at_s": None,
        "lock_rule": None,
        "first_dispatch_at_s": None,
        "plan_sha256": None,
        "assigned_team": None,
        "subtask_rows": [],
        "dispatch_rows": [],
        "consistency": [],
        "declared_decision": None,
        "truth_state": None,
    }

    def no_plan(reason: str) -> dict[str, Any]:
        result = {**base, "status": EVIDENCE_STATUS_NO_PLAN,
                  "no_committed_plan": True,
                  "no_committed_plan_reason": reason}
        declared, truth = _deployment_terms(session_report)
        result["declared_decision"] = declared
        result["truth_state"] = truth
        return _release(**result)

    plan_events, first_dispatch, unparsable = _accepted_plan_and_dispatch_ledger(
        session_report)
    if unparsable is not None:
        return no_plan(unparsable)
    if first_dispatch is not None:
        base["first_dispatch_at_s"] = first_dispatch["time_s"]
    stored = _stored_plans(session_report)
    dispatches = _dispatch_rows(session_report)
    base["dispatch_rows"] = dispatches
    declared, truth = _deployment_terms(session_report)
    base["declared_decision"] = declared
    base["truth_state"] = truth

    if first_dispatch is not None:
        candidates = [event for event in plan_events
                      if event["sequence"] < first_dispatch["sequence"]]
        lock_rule = LOCK_RULE_BEFORE_FIRST_DISPATCH
        empty_reason = "no_plan_action_before_first_dispatch"
    elif deadline is not None:
        candidates = [event for event in plan_events
                      if event["time_s"] is not None
                      and event["time_s"] <= deadline]
        lock_rule = LOCK_RULE_BEFORE_DEADLINE
        empty_reason = "no_plan_action_before_deadline"
    else:
        candidates = list(plan_events)
        lock_rule = LOCK_RULE_NO_DISPATCH_NO_DEADLINE
        empty_reason = "no_plan_action_in_episode"

    locked: Mapping[str, Any] | None = None
    locked_event: dict[str, Any] | None = None
    for event in reversed(candidates):  # last parsable wins
        row = stored.get(event["action_id"])
        if row is not None and _parsable_plan(row.get("plan")):
            locked, locked_event = row, event
            break
    if locked is None or locked_event is None:
        reason = empty_reason if not candidates else "no_parsable_stored_plan_payload"
        return no_plan(reason)

    plan = locked["plan"]
    locked_version = locked.get("version")
    base.update({
        "plan_version": locked_version,
        "plan_id": plan["plan_id"],
        "locked_at_s": _optional_time(locked.get("submitted_at_s"),
                                      "submitted_at_s")
        if locked.get("submitted_at_s") is not None else locked_event["time_s"],
        "lock_rule": lock_rule,
        "plan_sha256": canonical_sha256(dict(plan)),
        "assigned_team": list(plan["assigned_team"]),
        "subtask_rows": _subtask_rows(plan, _declarations(session_report)),
        "consistency": _consistency_rows(plan, locked_version, dispatches),
    })
    return _release(**base)


# ---------------------------------------------------------------------------
# Predicate-binding registry (milestone predicate_ref -> trusted evaluator)
# ---------------------------------------------------------------------------


def register_predicate_binding(contract_v4: Mapping[str, Any],
                               bindings: Mapping[str, Mapping[str, Any]]
                               ) -> dict[str, Any]:
    """Seal milestone ``predicate_ref`` bindings to trusted evaluator ids.

    ``bindings`` maps contract milestone ids to ``{"evaluator_ref": str,
    "parameters_digest": str}``; the predicate reference itself is read from
    the validated contract (never from the caller), and the sealed registry
    is bound to the exact contract hash.  Partial registration is allowed -
    unbound milestones are later reported ``predicate_unbound`` rather than
    guessed.  The seal makes post-hoc edits detectable.
    """
    report = validate_contract_v4(contract_v4)
    bindings = _mapping(bindings, "predicate bindings")
    milestone_refs = {milestone["milestone_id"]: milestone["predicate_ref"]
                      for milestone in contract_v4[MILESTONES_FIELD]}
    unknown = sorted(set(bindings) - set(milestone_refs))
    if unknown:
        raise ValueError(
            f"predicate bindings reference unknown milestones: {unknown}")
    sealed: dict[str, dict[str, str]] = {}
    for milestone_id, entry in bindings.items():
        entry = _mapping(entry, f"predicate binding {milestone_id}")
        _check_exact_fields(entry, _BINDING_INPUT_FIELDS,
                            f"predicate binding {milestone_id}")
        sealed[milestone_id] = {
            "predicate_ref": milestone_refs[milestone_id],
            "evaluator_ref": _nonempty_str(
                entry["evaluator_ref"],
                f"predicate binding {milestone_id} evaluator_ref"),
            "parameters_digest": _nonempty_str(
                entry["parameters_digest"],
                f"predicate binding {milestone_id} parameters_digest"),
        }
    payload = {
        "schema": PREDICATE_BINDING_SCHEMA,
        "version": PREDICATE_BINDING_VERSION,
        "task_id": report["task_id"],
        "task_sha256": report["task_sha256"],
        "bindings": sealed,
    }
    return _release(**payload, seal_sha256=canonical_sha256(payload))


def validate_predicate_bindings(contract_v4: Mapping[str, Any],
                                registry: Mapping[str, Any]) -> dict[str, Any]:
    """Verify a sealed registry against the exact contract; tamper-fail.

    Checks the envelope, the seal, the contract hash binding and that every
    sealed ``predicate_ref`` still equals the contract's milestone reference
    (contract drift breaks the seal first, this check names the mismatch).
    Returns the bound/unbound milestone split for the CS judge.
    """
    if contract_v4 is None:
        raise ValueError("predicate bindings require contract v4")
    report = validate_contract_v4(contract_v4)
    registry = _mapping(registry, "predicate binding registry")
    _check_exact_fields(registry, {"schema", "version", "task_id",
                                   "task_sha256", "bindings", "seal_sha256"},
                        "predicate binding registry",
                        allow=_REGISTRY_ANNOTATION_FIELDS)
    if (registry["schema"] != PREDICATE_BINDING_SCHEMA
            or registry["version"] != PREDICATE_BINDING_VERSION):
        raise ValueError("unsupported predicate binding registry envelope")
    if registry["task_id"] != report["task_id"]:
        raise ValueError(
            "predicate binding registry is sealed to a different task "
            "(task_id mismatch)")
    if registry["task_sha256"] != report["task_sha256"]:
        raise ValueError(
            "predicate binding registry is sealed to a different contract "
            "version (task_sha256 mismatch)")
    expected_seal = canonical_sha256({
        "schema": registry["schema"], "version": registry["version"],
        "task_id": registry["task_id"],
        "task_sha256": registry["task_sha256"],
        "bindings": registry["bindings"],
    })
    if registry["seal_sha256"] != expected_seal:
        raise ValueError(
            "predicate binding registry seal mismatch: bindings differ from "
            "the sealed snapshot")
    milestone_refs = {milestone["milestone_id"]: milestone["predicate_ref"]
                      for milestone in contract_v4[MILESTONES_FIELD]}
    rows = _mapping(registry["bindings"], "predicate binding registry bindings")
    unknown = sorted(set(rows) - set(milestone_refs))
    if unknown:
        raise ValueError(
            f"predicate binding registry references unknown milestones: {unknown}")
    for milestone_id, entry in rows.items():
        entry = _mapping(entry, f"predicate binding {milestone_id}")
        _check_exact_fields(entry, _BINDING_ENTRY_FIELDS,
                            f"predicate binding {milestone_id}")
        _nonempty_str(entry["evaluator_ref"],
                      f"predicate binding {milestone_id} evaluator_ref")
        _nonempty_str(entry["parameters_digest"],
                      f"predicate binding {milestone_id} parameters_digest")
        if entry["predicate_ref"] != milestone_refs[milestone_id]:
            raise ValueError(
                f"predicate binding {milestone_id} predicate_ref differs from "
                "the contract milestone predicate_ref")
    bound = sorted(rows)
    unbound = sorted(set(milestone_refs) - set(rows))
    return _release(validated=True, bound_milestone_ids=bound,
                    unbound_milestone_ids=unbound,
                    seal_sha256=registry["seal_sha256"])


def _check_exact_fields(value: Mapping[str, Any], expected: frozenset | set,
                        label: str, *,
                        allow: frozenset | set = frozenset()) -> None:
    missing = set(expected) - set(value)
    extra = set(value) - set(expected) - set(allow)
    if missing or extra:
        raise ValueError(
            f"{label} fields mismatch; missing={sorted(missing)}, "
            f"extra={sorted(extra)}")


# ---------------------------------------------------------------------------
# CS three-dimensional judge
# ---------------------------------------------------------------------------


def _parse_tolerance(text: str) -> tuple[float, str]:
    match = _TOLERANCE_PATTERN.search(text)
    tolerance = 0.0
    if match:
        tolerance = float(match.group(1))
        if tolerance < 0:
            raise ValueError("numeric tolerance must be nonnegative")
    boundary_match = _BOUNDARY_PATTERN.search(text)
    boundary = (boundary_match.group(1).lower()
                if boundary_match is not None else "inclusive")
    return tolerance, boundary


def _validate_robot_registry(registry: Any) -> dict[str, dict[str, Any]]:
    if registry is None:
        return {}
    registry = _mapping(registry, "robot_registry")
    cleaned: dict[str, dict[str, Any]] = {}
    for agent, entry in registry.items():
        agent = _nonempty_str(agent, "robot_registry agent id")
        entry = _mapping(entry, f"robot_registry entry {agent}")
        _check_exact_fields(entry, _REGISTRY_ENTRY_FIELDS,
                            f"robot_registry entry {agent}")
        capabilities = _mapping(entry["capabilities"],
                                f"robot_registry entry {agent} capabilities")
        parsed: dict[str, Any] = {}
        for capability_type, value in capabilities.items():
            capability_type = _nonempty_str(
                capability_type, f"robot_registry {agent} capability type")
            if isinstance(value, bool):
                parsed[capability_type] = value
            elif isinstance(value, (int, float)) and math.isfinite(float(value)):
                parsed[capability_type] = float(value)
            elif isinstance(value, str) and value:
                parsed[capability_type] = value
            else:
                raise ValueError(
                    f"robot_registry {agent} capability {capability_type} "
                    "must be a boolean, finite number or nonempty string")
        cleaned[agent] = {
            "role_id": _nonempty_str(entry["role_id"],
                                     f"robot_registry entry {agent} role_id"),
            "capabilities": parsed,
        }
    return cleaned


def _validate_milestone_results(results: Any,
                                milestone_ids: set[str]) -> dict[str, bool]:
    if results is None:
        return {}
    results = _mapping(results, "milestone_results")
    unknown = sorted(set(results) - milestone_ids)
    if unknown:
        raise ValueError(
            f"milestone_results reference unknown milestones: {unknown}")
    cleaned: dict[str, bool] = {}
    for milestone_id, value in results.items():
        if type(value) is not bool:
            raise ValueError(
                f"milestone_results[{milestone_id}] must be a boolean "
                "external predicate result")
        cleaned[milestone_id] = value
    return cleaned


def _parse_locked_evidence(plan_evidence: Mapping[str, Any]
                           ) -> dict[str, Any]:
    subtask_rows = plan_evidence.get("subtask_rows")
    if not isinstance(subtask_rows, list):
        raise ValueError(
            "locked plan evidence requires a subtask_rows list")
    parsed_subtasks: list[dict[str, Any]] = []
    for index, row in enumerate(subtask_rows):
        row = _mapping(row, f"subtask_rows {index}")
        robots = row.get("robot_ids")
        if not isinstance(robots, list) or any(
                not isinstance(agent, str) or not agent for agent in robots):
            raise ValueError(
                f"subtask_rows {index} robot_ids must be a string list")
        milestone_ref = row.get("milestone_ref")
        if milestone_ref is not None and not (
                isinstance(milestone_ref, str) and milestone_ref):
            raise ValueError(
                f"subtask_rows {index} milestone_ref must be a nonempty "
                "string or null")
        parsed_subtasks.append({
            "task_id": row.get("task_id") if isinstance(row.get("task_id"), str)
            else row.get("subtask_id"),
            "milestone_ref": milestone_ref,
            "robot_ids": list(robots),
        })
    consistency = plan_evidence.get("consistency")
    if not isinstance(consistency, list):
        raise ValueError("locked plan evidence requires a consistency list")
    parsed_consistency: list[dict[str, Any]] = []
    for index, row in enumerate(consistency):
        row = _mapping(row, f"consistency {index}")
        milestone_ref = row.get("milestone_ref")
        if milestone_ref is not None and not (
                isinstance(milestone_ref, str) and milestone_ref):
            raise ValueError(
                f"consistency {index} milestone_ref must be a nonempty "
                "string or null")
        parsed_consistency.append(dict(row))
    return {"subtask_rows": parsed_subtasks,
            "consistency": parsed_consistency}


def _bearers_for_milestone(subtask_rows: list[dict[str, Any]],
                           milestone_id: str) -> list[str]:
    bearers: list[str] = []
    for row in subtask_rows:
        if row["milestone_ref"] != milestone_id:
            continue
        for agent in row["robot_ids"]:
            if agent not in bearers:
                bearers.append(agent)
    return sorted(bearers)


def _affecting_inconsistencies(consistency: list[dict[str, Any]],
                               milestone_id: str) -> list[dict[str, Any]]:
    """Protocol 3.1: an unattributable (null-ref) inconsistency is global.

    A dispatch inconsistency whose task carries a parsable milestone
    reference affects the obligations of that milestone; without a reference
    the actual dispatch cannot be reconciled with any obligation of the
    committed plan, so it conservatively affects all of them (fail closed -
    mismatched dispatches must not dodge constraint evaluation).
    """
    return [entry for entry in consistency
            if entry.get("milestone_ref") is None
            or entry.get("milestone_ref") == milestone_id]


def _declared_flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value:
        return True
    if isinstance(value, (int, float)):
        raise ValueError(
            "a numeric registry value cannot evidence a functional "
            "(string) threshold; fix the registry or the contract")
    return False


def _numeric_value(value: Any, agent: str, capability_type: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(
            f"robot_registry {agent} capability {capability_type} must be a "
            "finite number for a numeric threshold")
    return float(value)


def _capability_verdict(constraint: Mapping[str, Any],
                        bearers: list[str],
                        registry: dict[str, dict[str, Any]],
                        consistency: list[dict[str, Any]]) -> dict[str, Any]:
    milestone_id = constraint["milestone_id"]
    row: dict[str, Any] = {
        "constraint_id": constraint["constraint_id"],
        "milestone_id": milestone_id,
        "capability_type": constraint["capability_type"],
        "threshold": constraint["threshold"],
        "aggregation_rule": constraint["aggregation_rule"],
        "bearers": list(bearers),
        "active": True,
        "satisfied": 0,
        "violation_reason": None,
        "failure_label": None,
        "evidence": {},
    }
    inconsistent = _affecting_inconsistencies(consistency, milestone_id)
    if inconsistent:
        row["violation_reason"] = REASON_DISPATCH_INCONSISTENT
        row["failure_label"] = FAILURE_LABEL_CAPABILITY
        row["evidence"]["inconsistent_dispatches"] = [
            {key: entry.get(key) for key in
             ("task_id", "issue", "milestone_ref", "dispatched_agent_ids")}
            for entry in inconsistent]
        return row
    if not bearers:
        row["violation_reason"] = REASON_NO_SUBTASK_FOR_MILESTONE
        row["failure_label"] = FAILURE_LABEL_CAPABILITY
        return row
    missing = [agent for agent in bearers if agent not in registry]
    if missing:
        row["violation_reason"] = REASON_REGISTRY_AGENT_MISSING
        row["failure_label"] = FAILURE_LABEL_CAPABILITY
        row["evidence"]["missing_registry_agents"] = missing
        return row

    capability_type = constraint["capability_type"]
    aggregation = constraint["aggregation_rule"]
    threshold = constraint["threshold"]
    values = {agent: registry[agent]["capabilities"].get(capability_type)
              for agent in bearers}
    row["evidence"]["capability_values"] = dict(values)
    if isinstance(threshold, str):
        declared = {agent: _declared_flag(value)
                    for agent, value in values.items()}
        row["evidence"]["declared"] = dict(declared)
        if aggregation == "single_actor":
            if len(bearers) != 1:
                row["violation_reason"] = REASON_SINGLE_ACTOR_BEARERS
            elif declared[bearers[0]]:
                row["satisfied"] = 1
            else:
                row["violation_reason"] = REASON_CAP_NOT_DECLARED
        elif aggregation == "exists":
            if any(declared.values()):
                row["satisfied"] = 1
            else:
                row["violation_reason"] = REASON_CAP_NOT_DECLARED_ANY
        elif aggregation == "all":
            if all(declared.values()):
                row["satisfied"] = 1
            else:
                row["violation_reason"] = REASON_CAP_NOT_DECLARED_SOME
        else:
            raise ValueError(
                f"constraint {constraint['constraint_id']}: coalition_sum "
                "requires a numeric threshold")
    else:
        tolerance, boundary = _parse_tolerance(constraint["tolerance"])
        numeric = {agent: _numeric_value(value, agent, capability_type)
                   for agent, value in values.items()}
        row["evidence"]["numeric_tolerance"] = tolerance
        row["evidence"]["boundary"] = boundary
        row["evidence"]["capability_values"] = dict(numeric)

        def meets(value: float | None) -> bool:
            if value is None:
                return False
            return (value >= float(threshold) - tolerance
                    if boundary == "inclusive"
                    else value > float(threshold) - tolerance)

        if aggregation == "single_actor":
            if len(bearers) != 1:
                row["violation_reason"] = REASON_SINGLE_ACTOR_BEARERS
            elif meets(numeric[bearers[0]]):
                row["satisfied"] = 1
            else:
                row["violation_reason"] = REASON_CAP_UNMET
        elif aggregation == "exists":
            if any(meets(value) for value in numeric.values()):
                row["satisfied"] = 1
            else:
                row["violation_reason"] = REASON_CAP_UNMET_ANY
        elif aggregation == "all":
            if all(meets(value) for value in numeric.values()):
                row["satisfied"] = 1
            else:
                row["violation_reason"] = REASON_CAP_UNMET_SOME
        else:  # coalition_sum
            total = sum(value for value in numeric.values()
                        if value is not None)
            row["evidence"]["coalition_sum"] = total
            if meets(total):
                row["satisfied"] = 1
            else:
                row["violation_reason"] = REASON_COALITION_BELOW
    if not row["satisfied"]:
        row["failure_label"] = FAILURE_LABEL_CAPABILITY
    return row


def _role_verdict(constraint: Mapping[str, Any],
                  bearers: list[str],
                  registry: dict[str, dict[str, Any]],
                  consistency: list[dict[str, Any]]) -> dict[str, Any]:
    milestone_id = constraint["milestone_id"]
    row: dict[str, Any] = {
        "constraint_id": constraint["constraint_id"],
        "milestone_id": milestone_id,
        "required_role": constraint["required_role"],
        "cardinality": deepcopy(dict(constraint["cardinality"])),
        "bearers": list(bearers),
        "active": True,
        "satisfied": 0,
        "violation_reason": None,
        "failure_label": None,
        "evidence": {},
    }
    inconsistent = _affecting_inconsistencies(consistency, milestone_id)
    if inconsistent:
        row["violation_reason"] = REASON_DISPATCH_INCONSISTENT
        row["failure_label"] = FAILURE_LABEL_ROLE
        return row
    if not bearers:
        row["violation_reason"] = REASON_NO_SUBTASK_FOR_MILESTONE
        row["failure_label"] = FAILURE_LABEL_ROLE
        return row
    missing = [agent for agent in bearers if agent not in registry]
    if missing:
        row["violation_reason"] = REASON_REGISTRY_AGENT_MISSING
        row["failure_label"] = FAILURE_LABEL_ROLE
        row["evidence"]["missing_registry_agents"] = missing
        return row
    roles = {agent: registry[agent]["role_id"] for agent in bearers}
    row["evidence"]["bearer_roles"] = roles
    matching = sorted(agent for agent, role in roles.items()
                      if role == constraint["required_role"])
    row["evidence"]["matching_role_agents"] = matching
    count = constraint["cardinality"]["count"]
    comparator = constraint["cardinality"]["comparator"]
    if comparator == "at_least":
        satisfied = len(matching) >= count
    else:
        satisfied = len(matching) == count
    if satisfied:
        row["satisfied"] = 1
    else:
        row["violation_reason"] = REASON_ROLE_CARDINALITY
        row["failure_label"] = FAILURE_LABEL_ROLE
    return row


def _milestone_verdict(milestone: Mapping[str, Any],
                       results: dict[str, bool],
                       bound_ids: set[str] | None) -> dict[str, Any]:
    milestone_id = milestone["milestone_id"]
    row: dict[str, Any] = {
        "milestone_id": milestone_id,
        "predicate_ref": milestone["predicate_ref"],
        "required_for_success": True,
        "active": True,
        "satisfied": 0,
        "status": None,
        "violation_reason": None,
        "failure_label": None,
        "evidence": {},
    }
    if bound_ids is not None and milestone_id not in bound_ids:
        # Never guess: without a sealed trusted-evaluator binding the
        # external predicate result has no provenance, even if a value was
        # supplied.
        row["status"] = REASON_PREDICATE_UNBOUND
        row["violation_reason"] = REASON_PREDICATE_UNBOUND
        row["failure_label"] = FAILURE_LABEL_MILESTONE
        return row
    if milestone_id not in results:
        row["status"] = REASON_MILESTONE_RESULT_MISSING
        row["violation_reason"] = REASON_MILESTONE_RESULT_MISSING
        row["failure_label"] = FAILURE_LABEL_MILESTONE
        return row
    if results[milestone_id]:
        row["satisfied"] = 1
        row["status"] = "satisfied"
    else:
        row["status"] = "unmet"
        row["violation_reason"] = REASON_MILESTONE_UNMET
        row["failure_label"] = FAILURE_LABEL_MILESTONE
    return row


def _component(verdicts: list[dict[str, Any]], gate: int) -> dict[str, Any]:
    total = len(verdicts)
    if total == 0:
        # Protocol: an empty activation set is N/A, never 0 or 1 (and never
        # zeroed by the gate - only *defined* components are zeroed).
        return {"rate": None, "status": COMPONENT_NA_EMPTY_SET,
                "satisfied": None, "total": 0,
                "zeroed_by_gate": False}
    satisfied = sum(row["satisfied"] for row in verdicts)
    rate = gate * (satisfied / total)
    return {"rate": rate,
            "status": COMPONENT_COMPUTED if gate else COMPONENT_ZEROED_BY_GATE,
            "satisfied": satisfied, "total": total,
            "zeroed_by_gate": gate == 0}


def _components_result(status: str, gate: int | None, *,
                       cap: dict[str, Any] | None = None,
                       role: dict[str, Any] | None = None,
                       mile: dict[str, Any] | None = None,
                       gate_terms: dict[str, Any] | None = None,
                       gate_reasons: list[str] | None = None,
                       atomic: dict[str, Any] | None = None,
                       working_set: dict[str, Any] | None = None) -> dict[str, Any]:
    component_status = {
        CS_GATE_NO_CONTRACT_STATUS: COMPONENT_NOT_EVALUABLE_NO_CONTRACT,
        CS_STATUS_TEAM_STATE_INFEASIBLE: COMPONENT_NOT_DEFINED_INFEASIBLE,
    }.get(status)
    components: dict[str, dict[str, Any]] = {}
    for name, verdicts in (("cap", cap), ("role", role), ("mile", mile)):
        if verdicts is None:
            components[name] = {"rate": None,
                                "status": component_status
                                or COMPONENT_NOT_EVALUABLE_NO_CONTRACT,
                                "satisfied": None, "total": None,
                                "zeroed_by_gate": False}
        else:
            components[name] = _component(verdicts, 1 if gate == 1 else 0)
    return _release(
        schema=CS_VECTOR_SCHEMA,
        version=CS_VECTOR_VERSION,
        protocol_section="3.1",
        status=status,
        formula=_CS_FORMULA,
        gate={"G": gate, "terms": gate_terms or {},
              "failure_reasons": gate_reasons or []},
        components=components,
        vector={name: entry["rate"] for name, entry in components.items()},
        atomic_verdicts=atomic or {"capability": [], "role": [], "milestone": []},
        activation_working_set=working_set or {},
    )


def constraint_satisfaction_vector(contract_v4: Mapping[str, Any] | None,
                                   plan_evidence: Mapping[str, Any] | None, *,
                                   milestone_results: Mapping[str, bool] | None,
                                   robot_registry: Mapping[str, Mapping[str, Any]] | None,
                                   predicate_bindings: Mapping[str, Any] | None = None
                                   ) -> dict[str, Any]:
    """Judge the protocol 3.1 three-dimensional constraint-satisfaction vector.

    Gate: :math:`G=1` only when the archived oracle truth is ``deployable``,
    the system declaration is ``deployable`` and the committed-plan evidence
    extractor locked a parsable plan (:math:`O^{commit}=1`).  A
    ``team_state_infeasible`` truth leaves the whole vector undefined (the
    instance belongs to the deployment metrics only); any other gate failure
    on a deployable-truth episode zeroes every defined component.  Component
    activation follows the deterministic ``episode_start_active`` rule; any
    other declared activation is excluded as an explicit exogenous gap.

    Capability/role bearers are the locked plan's subtask rows whose
    ``milestone_ref`` equals the constraint milestone; capability values and
    platform roles are read from ``robot_registry`` (the pool's public
    capability/payload table), never guessed.  Milestone outcomes come from
    caller-supplied external predicate results; when a sealed predicate
    registry is supplied, an unbound milestone is ``predicate_unbound``
    instead of trusting an unprovenanced value.
    """
    if contract_v4 is None:
        return _components_result(
            CS_GATE_NO_CONTRACT_STATUS, None,
            gate_terms={"truth_state": None, "declared_decision": None,
                        "committed_plan_locked": False},
            gate_reasons=[GATE_REASON_NO_CONTRACT])
    report = validate_contract_v4(contract_v4)

    committed = False
    evidence: dict[str, Any] = {"subtask_rows": [], "consistency": []}
    truth_state = None
    declared_decision = None
    if plan_evidence is None or (
            isinstance(plan_evidence, Mapping) and not plan_evidence):
        pass
    else:
        plan_evidence = _mapping(plan_evidence, "plan_evidence")
        status = plan_evidence.get("status")
        if status == EVIDENCE_STATUS_NO_PLAN:
            pass
        elif status == EVIDENCE_STATUS_LOCKED:
            committed = True
            evidence = _parse_locked_evidence(plan_evidence)
        else:
            raise ValueError(
                "plan_evidence must be a committed-plan-evidence result "
                "(status committed_plan_locked or no_committed_plan)")
        truth_state = plan_evidence.get("truth_state")
        declared_decision = plan_evidence.get("declared_decision")
        if truth_state is not None and not isinstance(truth_state, str):
            raise ValueError("plan_evidence truth_state must be a string or null")
        if declared_decision is not None and not isinstance(
                declared_decision, str):
            raise ValueError(
                "plan_evidence declared_decision must be a string or null")

    gate_terms = {"truth_state": truth_state,
                  "declared_decision": declared_decision,
                  "committed_plan_locked": committed}
    if truth_state == TRUTH_INFEASIBLE:
        # Protocol: only oracle-deployable instances define the vector.
        return _components_result(
            CS_STATUS_TEAM_STATE_INFEASIBLE, None, gate_terms=gate_terms,
            gate_reasons=["team_state_infeasible_vector_not_defined"])

    gate_reasons: list[str] = []
    if not committed:
        gate_reasons.append(GATE_REASON_NO_PLAN)
    if truth_state != "deployable":
        # Unknown or non-deployable truth cannot enter the vector's risk set;
        # fail closed to G=0 instead of an N/A that dodges evaluation.
        gate_reasons.append(GATE_REASON_TRUTH)
    if declared_decision != "deployable":
        gate_reasons.append(GATE_REASON_DECLARATION)
    gate = int(not gate_reasons)
    # A missing/unparsable committed plan keeps its protocol failure word as
    # the judge status; the components are still emitted (zeroed by G=0) so
    # the atomic obligations stay auditable.
    judge_status = (CS_GATE_NO_COMMITTED_PLAN_STATUS if not committed
                    else CS_STATUS_COMPUTED)

    milestone_ids = {milestone["milestone_id"]
                     for milestone in contract_v4[MILESTONES_FIELD]}
    results = _validate_milestone_results(milestone_results, milestone_ids)
    registry = _validate_robot_registry(robot_registry)
    bound_ids: set[str] | None = None
    if predicate_bindings is not None:
        binding_report = validate_predicate_bindings(
            contract_v4, predicate_bindings)
        bound_ids = set(binding_report["bound_milestone_ids"])

    active_cap = [constraint for constraint
                  in contract_v4[CAPABILITY_CONSTRAINTS_FIELD]
                  if constraint["activation_rule"] == ACTIVATION_EPISODE_START]
    active_role = [constraint for constraint
                   in contract_v4[ROLE_CONSTRAINTS_FIELD]
                   if constraint["activation_rule"] == ACTIVATION_EPISODE_START]
    required_milestones = [milestone for milestone
                           in contract_v4[MILESTONES_FIELD]
                           if milestone["required_for_success"]
                           and milestone["activation_rule"] == ACTIVATION_EPISODE_START]

    def excluded(rows: list[Mapping[str, Any]], id_field: str) -> list[str]:
        return [row[id_field] for row in rows
                if row["activation_rule"] != ACTIVATION_EPISODE_START]

    cap_verdicts = [
        _capability_verdict(constraint,
                            _bearers_for_milestone(
                                evidence["subtask_rows"],
                                constraint["milestone_id"]),
                            registry, evidence["consistency"])
        for constraint in active_cap]
    role_verdicts = [
        _role_verdict(constraint,
                      _bearers_for_milestone(
                          evidence["subtask_rows"],
                          constraint["milestone_id"]),
                      registry, evidence["consistency"])
        for constraint in active_role]
    mile_verdicts = [_milestone_verdict(milestone, results, bound_ids)
                     for milestone in required_milestones]

    working_set = {
        "capability_constraint_ids": [row["constraint_id"] for row in active_cap],
        "role_constraint_ids": [row["constraint_id"] for row in active_role],
        "required_milestone_ids": [row["milestone_id"]
                                   for row in required_milestones],
        "excluded_exogenous_activation": {
            "capability_constraint_ids": excluded(
                contract_v4[CAPABILITY_CONSTRAINTS_FIELD], "constraint_id"),
            "role_constraint_ids": excluded(
                contract_v4[ROLE_CONSTRAINTS_FIELD], "constraint_id"),
            "milestone_ids": excluded(contract_v4[MILESTONES_FIELD],
                                      "milestone_id"),
            "note": ("non episode_start activation rules are declared "
                     "exogenous evaluation gaps; never guessed here"),
        },
        "contract_task_sha256": report["task_sha256"],
    }
    return _components_result(
        judge_status, gate, cap=cap_verdicts, role=role_verdicts,
        mile=mile_verdicts, gate_terms=gate_terms,
        gate_reasons=gate_reasons,
        atomic={"capability": cap_verdicts, "role": role_verdicts,
                "milestone": mile_verdicts},
        working_set=working_set)


__all__ = [
    "COMMITTED_PLAN_EVIDENCE_SCHEMA", "COMMITTED_PLAN_EVIDENCE_VERSION",
    "COMPONENT_COMPUTED", "COMPONENT_NA_EMPTY_SET",
    "COMPONENT_NOT_DEFINED_INFEASIBLE", "COMPONENT_NOT_EVALUABLE_NO_CONTRACT",
    "COMPONENT_ZEROED_BY_GATE", "CS_STATUS_COMPUTED",
    "CS_STATUS_TEAM_STATE_INFEASIBLE", "CS_VECTOR_SCHEMA", "CS_VECTOR_VERSION",
    "EVIDENCE_STATUS_LOCKED", "EVIDENCE_STATUS_NO_PLAN",
    "FAILURE_LABEL_CAPABILITY", "FAILURE_LABEL_MILESTONE", "FAILURE_LABEL_ROLE",
    "GATE_REASON_DECLARATION", "GATE_REASON_NO_CONTRACT", "GATE_REASON_NO_PLAN",
    "GATE_REASON_TRUTH", "LOCK_RULE_BEFORE_DEADLINE",
    "LOCK_RULE_BEFORE_FIRST_DISPATCH", "LOCK_RULE_NO_DISPATCH_NO_DEADLINE",
    "PREDICATE_BINDING_SCHEMA", "PREDICATE_BINDING_VERSION",
    "REASON_CAP_NOT_DECLARED", "REASON_CAP_NOT_DECLARED_ANY",
    "REASON_CAP_NOT_DECLARED_SOME", "REASON_CAP_UNMET", "REASON_CAP_UNMET_ANY",
    "REASON_CAP_UNMET_SOME", "REASON_COALITION_BELOW",
    "REASON_DISPATCH_INCONSISTENT", "REASON_MILESTONE_RESULT_MISSING",
    "REASON_MILESTONE_UNMET", "REASON_NO_SUBTASK_FOR_MILESTONE",
    "REASON_PREDICATE_UNBOUND", "REASON_REGISTRY_AGENT_MISSING",
    "REASON_ROLE_CARDINALITY", "REASON_SINGLE_ACTOR_BEARERS",
    "constraint_satisfaction_vector", "extract_committed_plan_evidence",
    "register_predicate_binding", "validate_predicate_bindings",
]
