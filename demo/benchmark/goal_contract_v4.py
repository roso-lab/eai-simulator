"""Protocol contract v4: explicit milestone/constraint schema and v3->v4 migrator.

Batch 2A of the protocol conformance plan.  Protocol section 2.6 requires
every formal instance to bind a versioned task contract whose
``contract_milestones`` (id, semantics, weight, external predicate,
activation rule, stability duration, finality, monotonicity, visibility and
anti-double-count rule) become the frozen result axis of :math:`Q_i(t)`,
while protocol section 3.1 adds the three obligation classes
(``capability_constraints``, ``role_constraints``, required milestones) that
later feed the constraint-satisfaction vector.

This module is additive and strictly separated from the frozen v2/v3 public
task contract (viewer precheck 2026-09-24):

* a v4 task is the *exact* pre-v4 public task plus four top-level addon
  fields (``contract_version``, ``contract_milestones``,
  ``capability_constraints``, ``role_constraints``); the pre-v4 reader
  :func:`demo.benchmark.goal_driven_contract.validate_goal_task_spec`
  keeps rejecting v4 payloads (extra fields, unknown version), so v3 reads
  v4 fail-closed and v3->v4 happens only through the explicit migrator here;
* ``contract_milestones`` coexist with ``required_outcomes`` under a stable
  one-to-one ``outcome_ref`` mapping - outcomes are never renamed, replaced,
  or made to drive milestone verdicts;
* the migrator never derives protocol *semantics*.  ``weight``,
  ``activation_rule``, ``stability_duration_s``, ``monotonicity`` and
  ``anti_double_count_rule`` must come from an explicit author
  ``milestone_specs`` entry; a missing spec or missing semantic field fails
  the migration with an explicit list.  No provisional defaults are
  generated (stricter than the viewer suggestion, to avoid polluting formal
  :math:`Q(t)` denominators with guessed semantics);
* the GoalSession participant surface (six actions / eight events) is
  untouched; the constraint-satisfaction vector only gets a three-state
  *gate* here - the cap/role/mile verdicts themselves are batch 2B;
* every artifact is release-gated false: contract v4 is schema and
  migration tooling, not a protocol score.
"""

from __future__ import annotations

from copy import deepcopy
import math
from typing import Any, Mapping

from .goal_driven_contract import (
    GOAL_CONTRACT_VERSION,
    GOAL_DEPLOYMENT_CONTRACT_VERSION,
    validate_goal_task_spec,
)
from .goal_protocol_deployment import NOT_APPLICABLE_UNTIL_CONTRACT_V4
from .provenance import canonical_sha256


CONTRACT_V4_SCHEMA = "eai.benchmark.goal_task_spec_v4"
CONTRACT_V4_VERSION = 4
MIGRATOR_VERSION = 1

# The four top-level addon fields that distinguish a v4 task from the frozen
# pre-v4 public task.  ``contract_version`` is deliberately independent of the
# legacy ``version`` field (the deployment switch stays at 2/3 semantics).
CONTRACT_VERSION_FIELD = "contract_version"
MILESTONES_FIELD = "contract_milestones"
CAPABILITY_CONSTRAINTS_FIELD = "capability_constraints"
ROLE_CONSTRAINTS_FIELD = "role_constraints"
_V4_ADDON_FIELDS = frozenset({
    CONTRACT_VERSION_FIELD, MILESTONES_FIELD,
    CAPABILITY_CONSTRAINTS_FIELD, ROLE_CONSTRAINTS_FIELD,
})
_PRE_V4_SOURCE_VERSIONS = frozenset({GOAL_CONTRACT_VERSION,
                                     GOAL_DEPLOYMENT_CONTRACT_VERSION})

# Protocol 2.6 milestone record.  ``predicate_ref`` names the external
# predicate (protocol's `predicate`); the evaluator/reader that owns the
# predicate is bound in batch 2B, never inferred here.
MILESTONE_FIELDS = frozenset({
    "milestone_id", "semantic_description", "predicate_ref", "weight",
    "activation_rule", "required_for_success", "stability_duration_s",
    "monotonicity", "visibility", "anti_double_count_rule", "outcome_ref",
})
CAPABILITY_CONSTRAINT_FIELDS = frozenset({
    "constraint_id", "milestone_id", "capability_type", "threshold",
    "tolerance", "aggregation_rule", "activation_rule", "evidence_fields",
})
ROLE_CONSTRAINT_FIELDS = frozenset({
    "constraint_id", "milestone_id", "required_role", "cardinality",
    "activation_rule", "evidence_fields",
})
_CARDINALITY_FIELDS = frozenset({"count", "comparator"})

# Protocol 3.1 fixes the aggregation vocabulary; same-class robots never sum
# unless the contract says coalition_sum explicitly.
AGGREGATION_RULES = ("single_actor", "coalition_sum", "exists", "all")
ROLE_CARDINALITY_COMPARATORS = ("at_least", "exactly")
# Monotonicity: the batch-1 curve builder scores monotone steps; a declared
# non_monotonic milestone is schema-legal but refused by the Q(t) interface.
MONOTONICITY_VALUES = ("monotonic", "non_monotonic")
VISIBILITY_VALUES = ("public", "evaluator_only")
# The only activation vocabulary this batch can evaluate deterministically:
# active from episode start (frozen with the episode working set).  Any other
# rule text is a declared exogenous activation whose evaluation is batch 2B.
ACTIVATION_EPISODE_START = "episode_start_active"

# Migrator author spec.  The five semantic fields must be author_explicit;
# the optional ones have documented structural derivations only.
AUTHOR_REQUIRED_SPEC_FIELDS = (
    "weight", "activation_rule", "stability_duration_s", "monotonicity",
    "anti_double_count_rule",
)
AUTHOR_OPTIONAL_SPEC_FIELDS = (
    "milestone_id", "semantic_description", "predicate_ref",
    "required_for_success", "visibility",
)
MILESTONE_SPEC_FIELDS = frozenset(AUTHOR_REQUIRED_SPEC_FIELDS
                                  + AUTHOR_OPTIONAL_SPEC_FIELDS)

# CS gate vocabulary.  The no-contract word is imported from the batch-0
# placeholder so the two modules can never drift apart.
CS_VECTOR_GATE_SCHEMA = "eai.benchmark.cs_vector_gate"
CS_VECTOR_GATE_VERSION = 1
CS_GATE_NO_CONTRACT_STATUS = NOT_APPLICABLE_UNTIL_CONTRACT_V4
CS_GATE_NO_COMMITTED_PLAN_STATUS = "no_committed_plan"
CS_GATE_DEFERRED_STATUS = "deferred_to_constraint_verifier_batch_2b"
CS_COMPONENT_NAMES = ("cap", "role", "mile")

# Release gate: contract v4 authoring/migration never claims scoreability.
CONTRACT_V4_RELEASE = {"release_eligible": False, "scoreable": False,
                       "execution_validated": False}


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return value


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a nonempty string")
    return value


def _string_list(value: Any, label: str, *, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list) or (not allow_empty and not value):
        raise ValueError(f"{label} must be a nonempty string list")
    if any(not isinstance(item, str) or not item for item in value):
        raise ValueError(f"{label} must contain nonempty strings")
    if len(value) != len(set(value)):
        raise ValueError(f"{label} must contain unique values")
    return list(value)


def _finite_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{label} must be a finite number")
    return result


def _nonnegative_number(value: Any, label: str) -> float:
    if _finite_number(value, label) < 0:
        raise ValueError(f"{label} must be a finite nonnegative number")
    return float(value)


def _boolean(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{label} must be a boolean")
    return value


def _enum(value: Any, options: tuple[str, ...], label: str) -> str:
    if value not in options or not isinstance(value, str):
        raise ValueError(f"{label} must be one of {list(options)}")
    return value


def _check_exact(value: Mapping[str, Any], expected: frozenset | set, label: str) -> None:
    missing = set(expected) - set(value)
    extra = set(value) - set(expected)
    if missing or extra:
        raise ValueError(f"{label} fields mismatch; missing={sorted(missing)}, extra={sorted(extra)}")


def _validate_milestones(milestones: Any, outcome_ids: list[str]
                         ) -> tuple[list[dict[str, Any]], dict[str, str]]:
    if not isinstance(milestones, list) or not milestones:
        raise ValueError("contract_milestones must be a nonempty list")
    rows: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, raw in enumerate(milestones):
        milestone = _mapping(raw, f"contract milestone {index}")
        _check_exact(milestone, MILESTONE_FIELDS, f"contract milestone {index}")
        milestone_id = _string(milestone["milestone_id"],
                               f"contract milestone {index} milestone_id")
        if milestone_id in seen_ids:
            raise ValueError(f"contract milestone ids must be unique: {milestone_id}")
        seen_ids.add(milestone_id)
        row: dict[str, Any] = {"milestone_id": milestone_id}
        _string(milestone["semantic_description"],
                f"contract milestone {milestone_id} semantic_description")
        _string(milestone["predicate_ref"],
                f"contract milestone {milestone_id} predicate_ref")
        row["weight"] = _nonnegative_number(
            milestone["weight"], f"contract milestone {milestone_id} weight")
        _string(milestone["activation_rule"],
                f"contract milestone {milestone_id} activation_rule")
        _boolean(milestone["required_for_success"],
                 f"contract milestone {milestone_id} required_for_success")
        _nonnegative_number(
            milestone["stability_duration_s"],
            f"contract milestone {milestone_id} stability_duration_s")
        _enum(milestone["monotonicity"], MONOTONICITY_VALUES,
              f"contract milestone {milestone_id} monotonicity")
        _enum(milestone["visibility"], VISIBILITY_VALUES,
              f"contract milestone {milestone_id} visibility")
        _string(milestone["anti_double_count_rule"],
                f"contract milestone {milestone_id} anti_double_count_rule")
        outcome_ref = _string(milestone["outcome_ref"],
                              f"contract milestone {milestone_id} outcome_ref")
        if outcome_ref not in outcome_ids:
            raise ValueError(
                f"contract milestone {milestone_id} outcome_ref "
                f"references unknown required outcome: {outcome_ref}")
        rows.append(row)
    outcome_refs = [milestone["outcome_ref"] for milestone in milestones]
    if len(outcome_refs) != len(set(outcome_refs)):
        raise ValueError(
            "contract milestones must map one-to-one onto required outcomes; "
            "multiple milestones reference the same outcome")
    uncovered = sorted(set(outcome_ids) - set(outcome_refs))
    if uncovered:
        raise ValueError(
            "contract milestones must map one-to-one onto required outcomes; "
            f"outcomes without a milestone: {uncovered}")
    total_weight = sum(row["weight"] for row in rows)
    if not total_weight > 0:
        raise ValueError("total contract milestone weight must be positive")
    return rows, {ref: milestone["milestone_id"] for ref, milestone
                  in zip(outcome_refs, milestones)}


def _validate_constraints(rows: Any, *, label: str, fields: frozenset,
                          milestone_ids: set[str],
                          seen_constraint_ids: set[str]) -> int:
    if not isinstance(rows, list):
        raise ValueError(f"{label} must be a list")
    for index, raw in enumerate(rows):
        constraint = _mapping(raw, f"{label} {index}")
        _check_exact(constraint, fields, f"{label} {index}")
        constraint_id = _string(constraint["constraint_id"],
                                f"{label} {index} constraint_id")
        if constraint_id in seen_constraint_ids:
            raise ValueError(
                f"constraint ids must be globally unique across capability and "
                f"role obligations: {constraint_id}")
        seen_constraint_ids.add(constraint_id)
        milestone_id = _string(constraint["milestone_id"],
                               f"{label} {constraint_id} milestone_id")
        if milestone_id not in milestone_ids:
            raise ValueError(
                f"{label} {constraint_id} references unknown contract "
                f"milestone: {milestone_id}")
        _string(constraint["activation_rule"],
                f"{label} {constraint_id} activation_rule")
        _string_list(constraint["evidence_fields"],
                     f"{label} {constraint_id} evidence_fields")
        if label.startswith("capability"):
            _string(constraint["capability_type"],
                    f"{label} {constraint_id} capability_type")
            threshold = constraint["threshold"]
            if isinstance(threshold, bool):
                raise ValueError(
                    f"{label} {constraint_id} threshold must be a finite number "
                    "or a nonempty string")
            if isinstance(threshold, str):
                _string(threshold, f"{label} {constraint_id} threshold")
            else:
                _finite_number(threshold, f"{label} {constraint_id} threshold")
            _string(constraint["tolerance"],
                    f"{label} {constraint_id} tolerance")
            _enum(constraint["aggregation_rule"], AGGREGATION_RULES,
                  f"{label} {constraint_id} aggregation_rule")
        else:
            _string(constraint["required_role"],
                    f"{label} {constraint_id} required_role")
            cardinality = _mapping(constraint["cardinality"],
                                   f"{label} {constraint_id} cardinality")
            _check_exact(cardinality, _CARDINALITY_FIELDS,
                         f"{label} {constraint_id} cardinality")
            count = cardinality["count"]
            if isinstance(count, bool) or not isinstance(count, int) or count < 1:
                raise ValueError(
                    f"{label} {constraint_id} cardinality count must be a "
                    "positive integer")
            _enum(cardinality["comparator"], ROLE_CARDINALITY_COMPARATORS,
                  f"{label} {constraint_id} cardinality comparator")
    return len(rows)


def validate_contract_v4(task_v4: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a contract-v4 task under a closed, fail-closed schema.

    The payload must be exactly a valid pre-v4 public task (versions 2/3,
    re-validated by the frozen v3-era closed schema) plus the four addon
    fields.  Milestone weights are finite and nonnegative with a positive
    total, ``aggregation_rule`` is restricted to the protocol enum, and every
    ``outcome_ref`` must resolve to an existing ``required_outcomes`` id
    under a one-to-one mapping.
    """
    task = _mapping(task_v4, "contract v4 task")
    missing = _V4_ADDON_FIELDS - set(task)
    if missing:
        raise ValueError(
            f"contract v4 addon fields missing: {sorted(missing)}")
    contract_version = task[CONTRACT_VERSION_FIELD]
    if (isinstance(contract_version, bool)
            or contract_version != CONTRACT_V4_VERSION):
        raise ValueError(
            f"contract v4 requires {CONTRACT_VERSION_FIELD} == {CONTRACT_V4_VERSION}")
    base = {key: value for key, value in task.items()
            if key not in _V4_ADDON_FIELDS}
    # Fail-closed reuse of the frozen pre-v4 reader: the stripped payload
    # must satisfy the exact v2/v3 closed schema (no extra keys survive).
    base_report = validate_goal_task_spec(base)
    outcome_ids = [row["id"] for row in base["required_outcomes"]]
    milestone_rows, outcome_map = _validate_milestones(
        task[MILESTONES_FIELD], outcome_ids)
    milestone_ids = {row["milestone_id"] for row in milestone_rows}
    seen_constraint_ids: set[str] = set()
    capability_count = _validate_constraints(
        task[CAPABILITY_CONSTRAINTS_FIELD], label="capability constraint",
        fields=CAPABILITY_CONSTRAINT_FIELDS, milestone_ids=milestone_ids,
        seen_constraint_ids=seen_constraint_ids)
    role_count = _validate_constraints(
        task[ROLE_CONSTRAINTS_FIELD], label="role constraint",
        fields=ROLE_CONSTRAINT_FIELDS, milestone_ids=milestone_ids,
        seen_constraint_ids=seen_constraint_ids)
    return {
        "schema": CONTRACT_V4_SCHEMA,
        "task_id": base["task_id"],
        "validated": True,
        "contract_version": CONTRACT_V4_VERSION,
        "source_contract_version": base["version"],
        "task_sha256": canonical_sha256(dict(task)),
        "base_task_sha256": base_report["sha256"],
        "milestone_count": len(milestone_rows),
        "outcome_count": len(outcome_ids),
        "outcome_milestone_map": outcome_map,
        "total_milestone_weight": sum(row["weight"] for row in milestone_rows),
        "episode_start_active_milestone_count": sum(
            1 for milestone in task[MILESTONES_FIELD]
            if milestone["activation_rule"] == ACTIVATION_EPISODE_START),
        "capability_constraint_count": capability_count,
        "role_constraint_count": role_count,
        **CONTRACT_V4_RELEASE,
    }


def _validate_milestone_specs(specs: Mapping[str, Any], outcome_ids: list[str]) -> None:
    """Fail the migration unless every outcome has an explicit author spec.

    Collects the complete failure list (unknown spec keys, outcomes without a
    spec, outcomes whose spec misses required semantic fields) so the author
    sees every gap at once.  Semantic fields are never defaulted.
    """
    unknown_specs = sorted(set(specs) - set(outcome_ids))
    problems: list[str] = []
    if unknown_specs:
        problems.append(
            f"milestone_specs reference unknown outcomes: {unknown_specs}")
    missing_specs = sorted(set(outcome_ids) - set(specs))
    if missing_specs:
        problems.append(
            "migration requires an explicit author milestone spec per "
            f"required outcome; missing milestone_specs for outcomes: {missing_specs}")
    for outcome_id in outcome_ids:
        if outcome_id not in specs:
            continue
        spec = _mapping(specs[outcome_id], f"milestone spec {outcome_id}")
        extra = sorted(set(spec) - MILESTONE_SPEC_FIELDS)
        if extra:
            problems.append(
                f"milestone spec {outcome_id} has unknown fields: {extra}")
        absent = [field for field in AUTHOR_REQUIRED_SPEC_FIELDS
                  if field not in spec]
        if absent:
            problems.append(
                f"milestone spec {outcome_id} is missing explicit semantic "
                f"fields (never defaulted): {absent}")
    if problems:
        raise ValueError("; ".join(problems))


def migrate_v3_to_contract_v4(public_task_v3: Mapping[str, Any], *,
                              milestone_specs: Mapping[str, Mapping[str, Any]]
                              ) -> dict[str, Any]:
    """Explicitly migrate a pre-v4 public task to contract v4.

    ``public_task_v3`` is any task the frozen pre-v4 reader accepts
    (contract versions 2 and 3 - the current public task family); a v4
    payload is rejected by that reader, so re-migration and loose dual reads
    are impossible.  ``milestone_specs`` maps every ``required_outcomes`` id
    to an author-supplied spec.  The five semantic fields
    (``weight``, ``activation_rule``, ``stability_duration_s``,
    ``monotonicity``, ``anti_double_count_rule``) must be explicitly present
    per outcome; any gap fails the migration with the full missing list and
    no provisional default is generated.  Structural derivations are limited
    to: ``milestone_id``/``outcome_ref`` default to the outcome id (stable
    one-to-one mapping), ``semantic_description`` copies the public outcome
    description, ``predicate_ref`` falls back to an explicitly namespaced
    ``legacy_business_outcome:<id>`` reference (it never claims to be the
    hidden evaluator's predicate), ``required_for_success`` maps the v3
    terminal requirement (every ``required_outcomes`` entry is terminal in
    v3), and ``visibility`` defaults to ``public`` (v3 outcomes are public
    task fields).  Every field's origin is recorded in the provenance.

    ``capability_constraints``/``role_constraints`` cannot be derived from a
    pre-v4 task at all; the migrated contract carries empty lists and the
    authoring of obligations is a separate explicit step.
    """
    specs = _mapping(milestone_specs, "milestone_specs")
    base_report = validate_goal_task_spec(public_task_v3)
    source = deepcopy(dict(public_task_v3))
    if source["version"] not in _PRE_V4_SOURCE_VERSIONS:
        raise ValueError("unsupported pre-v4 source contract version")
    outcome_ids = [row["id"] for row in source["required_outcomes"]]
    _validate_milestone_specs(specs, outcome_ids)

    milestones: list[dict[str, Any]] = []
    field_sources: dict[str, dict[str, str]] = {}
    for outcome_id in outcome_ids:
        spec = specs[outcome_id]
        milestone_id = spec.get("milestone_id", outcome_id)
        milestones.append({
            "milestone_id": milestone_id,
            "semantic_description": spec.get(
                "semantic_description",
                next(row["description"] for row in source["required_outcomes"]
                     if row["id"] == outcome_id)),
            "predicate_ref": spec.get(
                "predicate_ref", f"legacy_business_outcome:{outcome_id}"),
            "weight": spec["weight"],
            "activation_rule": spec["activation_rule"],
            "required_for_success": spec.get("required_for_success", True),
            "stability_duration_s": spec["stability_duration_s"],
            "monotonicity": spec["monotonicity"],
            "visibility": spec.get("visibility", "public"),
            "anti_double_count_rule": spec["anti_double_count_rule"],
            "outcome_ref": outcome_id,
        })
        field_sources[milestone_id] = {
            "milestone_id": ("author_explicit" if "milestone_id" in spec
                             else "migrator_structural"),
            "semantic_description": ("author_explicit" if "semantic_description" in spec
                                     else "v3_outcome_copy"),
            "predicate_ref": ("author_explicit" if "predicate_ref" in spec
                              else "migrator_structural"),
            "weight": "author_explicit",
            "activation_rule": "author_explicit",
            "required_for_success": ("author_explicit" if "required_for_success" in spec
                                     else "v3_structural_required_outcomes"),
            "stability_duration_s": "author_explicit",
            "monotonicity": "author_explicit",
            "visibility": ("author_explicit" if "visibility" in spec
                           else "v3_public_outcome"),
            "anti_double_count_rule": "author_explicit",
            "outcome_ref": "migrator_structural",
        }

    task_v4 = {
        **source,
        CONTRACT_VERSION_FIELD: CONTRACT_V4_VERSION,
        MILESTONES_FIELD: milestones,
        CAPABILITY_CONSTRAINTS_FIELD: [],
        ROLE_CONSTRAINTS_FIELD: [],
    }
    report = validate_contract_v4(task_v4)
    return {
        "task_v4": task_v4,
        "provenance": {
            "migrator_version": MIGRATOR_VERSION,
            "source_task_id": source["task_id"],
            "source_contract_version": source["version"],
            "source_task_sha256": base_report["sha256"],
            "task_v4_sha256": report["task_sha256"],
            "semantic_field_policy": (
                "weight, activation_rule, stability_duration_s, monotonicity "
                "and anti_double_count_rule are never defaulted; each comes "
                "from an explicit author milestone spec"),
            "provisional_fields": [],
            "milestone_field_sources": field_sources,
        },
        **CONTRACT_V4_RELEASE,
    }


def freeze_contract_v4(task_v4: Mapping[str, Any]) -> dict[str, Any]:
    """Seal a validated contract-v4 task with a canonical SHA-256 seal."""
    validate_contract_v4(task_v4)
    payload = {
        "schema": CONTRACT_V4_SCHEMA,
        "contract_version": CONTRACT_V4_VERSION,
        "task": deepcopy(dict(task_v4)),
    }
    return {**payload, "seal_sha256": canonical_sha256(payload)}


def validate_frozen_v4(frozen: Mapping[str, Any]) -> dict[str, Any]:
    """Verify a sealed contract: exact envelope, intact seal, valid content."""
    frozen = _mapping(frozen, "frozen contract v4")
    _check_exact(frozen, {"schema", "contract_version", "task", "seal_sha256"},
                 "frozen contract v4")
    if (frozen["schema"] != CONTRACT_V4_SCHEMA
            or frozen["contract_version"] != CONTRACT_V4_VERSION):
        raise ValueError("unsupported frozen contract v4 envelope")
    expected_seal = canonical_sha256({
        "schema": frozen["schema"],
        "contract_version": frozen["contract_version"],
        "task": frozen["task"],
    })
    if frozen["seal_sha256"] != expected_seal:
        raise ValueError(
            "frozen contract v4 seal mismatch: content differs from the "
            "sealed snapshot")
    report = validate_contract_v4(frozen["task"])
    return {"validated": True, "task_sha256": report["task_sha256"],
            "seal_sha256": expected_seal, **CONTRACT_V4_RELEASE}


def active_milestones_for_progress(
        contract_v4: Mapping[str, Any]) -> tuple[list[str], dict[str, float]]:
    """Freeze the episode-start working set for the batch-1 Q(t) builder.

    Returns ``(sorted_milestone_ids, weights)`` ready for
    :func:`demo.benchmark.goal_progress_curve.build_progress_curve`.  Only
    milestones activated by the deterministic ``episode_start_active`` rule
    enter the working set; milestones with any other declared activation
    rule are excluded (their exogenous evaluation is batch 2B).  A declared
    ``non_monotonic`` episode-start milestone raises instead of being fed to
    the monotone-step builder.
    """
    validate_contract_v4(contract_v4)
    active_ids: list[str] = []
    weights: dict[str, float] = {}
    for milestone in contract_v4[MILESTONES_FIELD]:
        if milestone["activation_rule"] != ACTIVATION_EPISODE_START:
            continue
        if milestone["monotonicity"] != "monotonic":
            raise ValueError(
                f"contract milestone {milestone['milestone_id']} is declared "
                "non_monotonic; the monotone-step Q(t) builder cannot score "
                "it (event-aware verification is required)")
        active_ids.append(milestone["milestone_id"])
        weights[milestone["milestone_id"]] = float(milestone["weight"])
    return sorted(active_ids), weights


def cs_vector_gate(contract_v4: Mapping[str, Any] | None,
                   committed_plan_evidence: Mapping[str, Any] | None
                   ) -> dict[str, Any]:
    """Three-state gate for the protocol 3.1 CS vector.

    Batch 2A decides only the gate, never the verdicts:

    * no contract v4 -> ``not_applicable_until_contract_v4`` (the exact
      batch-0 placeholder word);
    * contract v4 without committed-plan evidence -> ``no_committed_plan``;
    * both present -> ``deferred_to_constraint_verifier_batch_2b``: the
      cap/role/mile judgements belong to the batch-2B constraint verifier.

    No component is ever reported as a numeric failure here, and the gate
    output never becomes a protocol score.
    """
    if contract_v4 is None:
        return _gate_result(CS_GATE_NO_CONTRACT_STATUS,
                            "not_evaluable_no_contract_v4",
                            detail=("capability/role obligations and weighted "
                                    "milestones require contract v4; v3 "
                                    "required_outcomes cannot substitute"))
    validate_contract_v4(contract_v4)
    if committed_plan_evidence is None or (
            isinstance(committed_plan_evidence, Mapping)
            and not committed_plan_evidence):
        return _gate_result(
            CS_GATE_NO_COMMITTED_PLAN_STATUS, "no_committed_plan",
            detail=("contract v4 is present but no committed-plan evidence "
                    "was supplied for the gate"))
    if not isinstance(committed_plan_evidence, Mapping):
        raise ValueError("committed_plan_evidence must be a mapping or None")
    return _gate_result(
        CS_GATE_DEFERRED_STATUS, "pending_constraint_verifier_batch_2b",
        detail=("gate inputs are complete; cap/role/mile per-obligation "
                "verdicts are implemented by the batch-2B constraint "
                "verifier over the frozen v4 schema"))


def _gate_result(status: str, component_status: str, *, detail: str) -> dict[str, Any]:
    return {
        "schema": CS_VECTOR_GATE_SCHEMA,
        "version": CS_VECTOR_GATE_VERSION,
        "protocol_section": "3.1",
        "status": status,
        "components": {name: {"status": component_status, "rate": None}
                       for name in CS_COMPONENT_NAMES},
        "detail": detail,
        "not_protocol_score": True,
        **CONTRACT_V4_RELEASE,
    }


__all__ = [
    "ACTIVATION_EPISODE_START", "AGGREGATION_RULES", "AUTHOR_OPTIONAL_SPEC_FIELDS",
    "AUTHOR_REQUIRED_SPEC_FIELDS", "CAPABILITY_CONSTRAINT_FIELDS",
    "CAPABILITY_CONSTRAINTS_FIELD", "CONTRACT_V4_RELEASE", "CONTRACT_V4_SCHEMA",
    "CONTRACT_V4_VERSION", "CONTRACT_VERSION_FIELD", "CS_COMPONENT_NAMES",
    "CS_GATE_DEFERRED_STATUS", "CS_GATE_NO_COMMITTED_PLAN_STATUS",
    "CS_GATE_NO_CONTRACT_STATUS", "CS_VECTOR_GATE_SCHEMA", "CS_VECTOR_GATE_VERSION",
    "MIGRATOR_VERSION", "MILESTONE_FIELDS", "MILESTONES_FIELD",
    "MILESTONE_SPEC_FIELDS", "MONOTONICITY_VALUES",
    "ROLE_CARDINALITY_COMPARATORS", "ROLE_CONSTRAINT_FIELDS",
    "ROLE_CONSTRAINTS_FIELD", "VISIBILITY_VALUES",
    "active_milestones_for_progress", "cs_vector_gate", "freeze_contract_v4",
    "migrate_v3_to_contract_v4", "validate_contract_v4", "validate_frozen_v4",
]
