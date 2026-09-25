"""Batch 2B acceptance: committed-plan evidence, CS vector, predicate bindings.

``demo/benchmark/goal_cs_vector.py`` implements the protocol 3.1 constraint
verdicts on top of the batch-2A frozen v4 schema:

* the committed-plan evidence extractor locks the *last parsable accepted*
  ``submit_plan``/``revise_plan`` before the first accepted dispatch (or
  before the submission deadline when the episode never dispatched), reuses
  the batch-0 ``no_committed_plan`` word when nothing qualifies, and lists
  every actual dispatch inconsistent with the locked version;
* the judge computes
  ``CS = (G*sum(c_cap)/|C_cap|, G*sum(c_role)/|C_role|, G*sum(y_m)/|M_req|)``
  with ``G = 1[T_state=deployable and D=deployable and O_commit=1]``,
  protocol aggregation semantics (single_actor / coalition_sum / exists /
  all), role cardinalities from the platform registry, required-milestone
  outcomes from caller-supplied external predicate results, dispatch
  inconsistencies zeroing the affected obligations, empty activation sets
  N/A and defined components zeroed by ``G=0``;
* the predicate-binding registry seals milestone ``predicate_ref`` ->
  trusted evaluator bindings against the exact contract hash, and an
  unbound milestone is ``predicate_unbound`` - never guessed from a raw,
  unprovenanced result.

Everything stays release-gated false: judgement evidence, not a score.
"""

from copy import deepcopy
import functools

import pytest

from demo.benchmark.goal_contract_v4 import (
    CS_GATE_NO_COMMITTED_PLAN_STATUS,
    CS_GATE_NO_CONTRACT_STATUS,
    migrate_v3_to_contract_v4,
)
from demo.benchmark.goal_cs_vector import (
    COMMITTED_PLAN_EVIDENCE_SCHEMA,
    COMPONENT_NA_EMPTY_SET,
    COMPONENT_NOT_DEFINED_INFEASIBLE,
    COMPONENT_ZEROED_BY_GATE,
    CS_STATUS_COMPUTED,
    CS_STATUS_TEAM_STATE_INFEASIBLE,
    EVIDENCE_STATUS_LOCKED,
    EVIDENCE_STATUS_NO_PLAN,
    FAILURE_LABEL_CAPABILITY,
    FAILURE_LABEL_MILESTONE,
    GATE_REASON_DECLARATION,
    GATE_REASON_TRUTH,
    LOCK_RULE_BEFORE_DEADLINE,
    LOCK_RULE_BEFORE_FIRST_DISPATCH,
    LOCK_RULE_NO_DISPATCH_NO_DEADLINE,
    REASON_CAP_UNMET,
    REASON_COALITION_BELOW,
    REASON_DISPATCH_INCONSISTENT,
    REASON_MILESTONE_RESULT_MISSING,
    REASON_MILESTONE_UNMET,
    REASON_NO_SUBTASK_FOR_MILESTONE,
    REASON_PREDICATE_UNBOUND,
    REASON_ROLE_CARDINALITY,
    REASON_SINGLE_ACTOR_BEARERS,
    constraint_satisfaction_vector,
    extract_committed_plan_evidence,
    register_predicate_binding,
    validate_predicate_bindings,
)
from demo.benchmark.goal_protocol_deployment import (
    CONSTRAINT_FAILURE_LABELS,
    NOT_APPLICABLE_UNTIL_CONTRACT_V4,
)
from demo.benchmark.provenance import canonical_sha256


FACTORY_OUTCOMES = ("anomaly_confirmed", "response_area_served",
                    "maintenance_handoff_recorded")


# ---------------------------------------------------------------------------
# Fixtures: shape-faithful session reports and v4 contracts
# ---------------------------------------------------------------------------


def build_plan_report(plan_specs, *, dispatches=(), declarations=None,
                      deployment=None):
    """Build a synthetic but shape-faithful GoalSession report.

    ``plan_specs`` entries are ``{"at", "type"?, "plan_id", "tasks"}`` where
    each task row is ``{"task_id", "agents", "milestone_id"?}``; the optional
    milestone id lands in the task arguments as an explicit goal reference.
    ``dispatches`` entries are ``{"task_id", "at", "agents",
    "plan_version", "milestone_id"?}`` accepted after all plans.
    """
    events = [{"sequence": 0, "time_s": 0.0, "type": "episode_started",
               "payload": {}}]
    actions: list[dict] = []
    plans: list[dict] = []
    tasks: dict[str, dict] = {}
    sequence = 1
    version = 0

    def accept(action_type, at, payload):
        nonlocal sequence
        action_id = f"action-{sequence}"
        actions.append({"action_id": action_id, "type": action_type,
                        "payload": payload})
        events.append({"sequence": sequence, "time_s": at,
                       "type": "action_result",
                       "payload": {"action_id": action_id,
                                   "status": "accepted"}})
        sequence += 1
        return action_id

    def submit_plan_spec(spec):
        nonlocal version
        version += 1
        tasks_payload = []
        for row in spec["tasks"]:
            arguments = {}
            if row.get("milestone_id") is not None:
                arguments["milestone_id"] = row["milestone_id"]
            tasks_payload.append({
                "task_id": row["task_id"], "skill": "observe",
                "arguments": arguments, "prerequisites": [],
                "required_capabilities": ["observe"],
                "assigned_agents": list(row["agents"]), "evidence_refs": [],
                "execution": {"start_s": spec["at"] + 1.0,
                               "duration_s": 10.0},
            })
        plan = {"schema": "eai.benchmark.goal_plan", "version": 1,
                "plan_id": spec["plan_id"],
                "assigned_team": sorted({agent for row in spec["tasks"]
                                         for agent in row["agents"]}),
                "tasks": tasks_payload}
        action_id = accept(spec.get("type", "submit_plan"), spec["at"],
                           {"plan": plan})
        plans.append({"plan": plan, "version": version,
                      "action_id": action_id, "submitted_at_s": spec["at"],
                      "roster_version": 0,
                      "visible_event_sequence": sequence - 1})
        for task in tasks_payload:
            tasks[task["task_id"]] = {
                "task": deepcopy(task), "plan_id": spec["plan_id"],
                "plan_version": version, "status": "scheduled",
                "dispatch_action_id": None, "cancel_action_id": None,
                "invalidated_by": [], "reservations": [], "receipt": None,
            }

    def dispatch_spec(dispatch):
        action_id = accept("dispatch_skill", dispatch["at"],
                           {"task_id": dispatch["task_id"],
                            "evidence_refs": []})
        arguments = {}
        if dispatch.get("milestone_id") is not None:
            arguments["milestone_id"] = dispatch["milestone_id"]
        task_payload = {
            "task_id": dispatch["task_id"], "skill": "observe",
            "arguments": arguments, "prerequisites": [],
            "required_capabilities": [], "evidence_refs": [],
            "assigned_agents": list(dispatch["agents"]),
            "execution": {"start_s": dispatch["at"],
                          "duration_s": 10.0},
        }
        row = tasks.get(dispatch["task_id"])
        if row is None:
            row = {"task": task_payload, "plan_id": "plan-late",
                   "status": "scheduled", "cancel_action_id": None,
                   "invalidated_by": [], "reservations": [], "receipt": None}
            tasks[dispatch["task_id"]] = row
        else:
            row["task"]["assigned_agents"] = list(dispatch["agents"])
        row.update({"status": "pending", "dispatch_action_id": action_id,
                    "dispatched_at_s": dispatch["at"],
                    "plan_version": dispatch["plan_version"]})

    # Interleave plan submissions and dispatches on the accepted-action
    # timeline (stable by time): the lock rule reads event sequence order.
    timeline = [("plan", spec) for spec in plan_specs]
    timeline += [("dispatch", dispatch) for dispatch in dispatches]
    timeline.sort(key=lambda item: item[1]["at"])
    for kind, item in timeline:
        (submit_plan_spec if kind == "plan" else dispatch_spec)(item)
    report = {
        "task_id": "factory-task", "episode_id": "episode-0",
        "current_time_s": 100.0, "state_version": 1, "roster_version": 0,
        "plan_version": len(plans),
        "current_plan_id": plans[-1]["plan"]["plan_id"] if plans else None,
        "events": events, "actions": actions, "plans": plans, "tasks": tasks,
        "current_robot_declarations": declarations if declarations is not None else {
            "factory_aerial_1": {"id": "factory_aerial_1", "type": "iris",
                                 "mobility": "aerial",
                                 "public_capabilities": ["aerial_navigation",
                                                         "observe"]},
            "factory_ground_1": {"id": "factory_ground_1", "type": "m20",
                                 "mobility": "ground",
                                 "public_capabilities": ["ground_navigation",
                                                         "pick"]},
        },
    }
    if deployment is not None:
        report["deployment_assessment"] = deployment
    return report


@functools.lru_cache(maxsize=1)
def _factory_public_cached():
    from demo.benchmark.goal_driven_tasks import build_goal_task_package

    return build_goal_task_package("factory")["public_task"]


def _v4_task(capability_constraints=(), role_constraints=()):
    source = deepcopy(_factory_public_cached())
    specs = {outcome_id: {"weight": 1.0,
                          "activation_rule": "episode_start_active",
                          "stability_duration_s": 5.0,
                          "monotonicity": "monotonic",
                          "anti_double_count_rule":
                              "first_external_satisfaction_only"}
             for outcome_id in FACTORY_OUTCOMES}
    task_v4 = migrate_v3_to_contract_v4(
        source, milestone_specs=specs)["task_v4"]
    task_v4["capability_constraints"] = [deepcopy(row)
                                         for row in capability_constraints]
    task_v4["role_constraints"] = [deepcopy(row) for row in role_constraints]
    return task_v4


def _cap_constraint(milestone_id="anomaly_confirmed", **overrides):
    row = {
        "constraint_id": "cap-lift", "milestone_id": milestone_id,
        "capability_type": "lift_kg", "threshold": 10.0,
        "tolerance": "unit=kg; numeric tolerance 0.0; boundary inclusive",
        "aggregation_rule": "single_actor",
        "activation_rule": "episode_start_active",
        "evidence_fields": ["committed_plan.capability_value",
                            "dispatch_log.robot_ids"],
    }
    row.update(overrides)
    return row


def _role_constraint(milestone_id="anomaly_confirmed", **overrides):
    row = {
        "constraint_id": "role-uav", "milestone_id": milestone_id,
        "required_role": "uav",
        "cardinality": {"count": 1, "comparator": "at_least"},
        "activation_rule": "episode_start_active",
        "evidence_fields": ["committed_plan.role_id", "platform_registry.role"],
    }
    row.update(overrides)
    return row


def _agent(role_id, **capabilities):
    return {"role_id": role_id, "capabilities": dict(capabilities)}


def _evidence(subtasks=(), consistency=(), *, declared="deployable",
              truth="deployable", status=EVIDENCE_STATUS_LOCKED):
    return {
        "schema": COMMITTED_PLAN_EVIDENCE_SCHEMA, "version": 1,
        "protocol_section": "2.7+3.1", "status": status,
        "no_committed_plan": status == EVIDENCE_STATUS_NO_PLAN,
        "plan_version": 1, "plan_id": "plan-1", "locked_at_s": 5.0,
        "lock_rule": LOCK_RULE_BEFORE_FIRST_DISPATCH,
        "first_dispatch_at_s": 7.0, "plan_sha256": "a" * 64,
        "assigned_team": sorted({agent for _, _, robots in subtasks
                                 for agent in robots}),
        "subtask_rows": [
            {"task_id": task_id, "subtask_id": task_id,
             "milestone_ref": milestone_ref, "robot_ids": list(robots),
             "declared_capabilities": {}, "role_id": None, "role_ids": [],
             "plan_sha256": "a" * 64}
            for task_id, milestone_ref, robots in subtasks],
        "dispatch_rows": [],
        "consistency": [dict(row) for row in consistency],
        "declared_decision": declared, "truth_state": truth,
    }


def _results(**overrides):
    results = {outcome_id: True for outcome_id in FACTORY_OUTCOMES}
    results.update(overrides)
    return results


def _full_bindings(task):
    return register_predicate_binding(task, {
        outcome_id: {"evaluator_ref": f"evaluator://{outcome_id}/v1",
                     "parameters_digest": "a" * 64}
        for outcome_id in FACTORY_OUTCOMES})


def _judge(task, evidence, *, registry=None, results=None,
           bindings="auto"):
    if bindings == "auto" and task is not None:
        bindings = _full_bindings(task)
    elif bindings == "auto":
        bindings = None
    return constraint_satisfaction_vector(
        task, evidence, milestone_results=results, robot_registry=registry,
        predicate_bindings=bindings)


# ---------------------------------------------------------------------------
# Extractor: committed-plan evidence
# ---------------------------------------------------------------------------


def test_extractor_locks_last_parsable_plan_before_first_dispatch():
    report = build_plan_report([
        {"at": 2.0, "type": "submit_plan", "plan_id": "plan-1",
         "tasks": [{"task_id": "t1", "agents": ["factory_ground_1"]}]},
        {"at": 5.0, "type": "revise_plan", "plan_id": "plan-2",
         "tasks": [{"task_id": "t2", "agents": ["factory_aerial_1"]}]},
        {"at": 9.0, "type": "revise_plan", "plan_id": "plan-3",
         "tasks": [{"task_id": "t3", "agents": ["factory_ground_1"]}]},
    ], dispatches=[{"task_id": "t2", "at": 7.0,
                    "agents": ["factory_aerial_1"], "plan_version": 2}])
    evidence = extract_committed_plan_evidence(report)
    assert evidence["status"] == EVIDENCE_STATUS_LOCKED
    assert evidence["no_committed_plan"] is False
    assert evidence["plan_version"] == 2
    assert evidence["plan_id"] == "plan-2"
    assert evidence["locked_at_s"] == 5.0
    assert evidence["first_dispatch_at_s"] == 7.0
    assert evidence["lock_rule"] == LOCK_RULE_BEFORE_FIRST_DISPATCH
    # The sealed hash is the canonical hash of the exact locked plan payload.
    assert evidence["plan_sha256"] == canonical_sha256(
        report["plans"][1]["plan"])
    assert [row["task_id"] for row in evidence["subtask_rows"]] == ["t2"]
    # The first locked plan also exposes the dispatch it precedes.
    assert [row["task_id"] for row in evidence["dispatch_rows"]] == ["t2"]


def test_extractor_without_plan_reuses_batch0_no_committed_plan_word():
    evidence = extract_committed_plan_evidence(build_plan_report([]))
    assert evidence["status"] == CS_GATE_NO_COMMITTED_PLAN_STATUS == (
        EVIDENCE_STATUS_NO_PLAN)
    # The word is the exact batch-0 constraint-failure vocabulary entry.
    assert evidence["status"] in CONSTRAINT_FAILURE_LABELS
    assert evidence["no_committed_plan"] is True
    assert evidence["plan_version"] is None
    assert evidence["subtask_rows"] == []
    # Dispatching without ever committing a plan is equally commitless.
    dispatch_only = build_plan_report([], dispatches=[
        {"task_id": "t1", "at": 3.0, "agents": ["factory_ground_1"],
         "plan_version": 1}])
    assert extract_committed_plan_evidence(dispatch_only)["status"] == (
        EVIDENCE_STATUS_NO_PLAN)
    # A report without a parsable action/event ledger fails closed the same
    # way instead of guessing from partial data.
    unparsable = extract_committed_plan_evidence({"events": None,
                                                  "actions": []})
    assert unparsable["status"] == EVIDENCE_STATUS_NO_PLAN
    assert unparsable["no_committed_plan_reason"] == (
        "report_missing_action_event_ledger")


def test_extractor_no_dispatch_locks_last_plan_before_deadline():
    specs = [
        {"at": 5.0, "plan_id": "plan-early",
         "tasks": [{"task_id": "t1", "agents": ["factory_ground_1"]}]},
        {"at": 40.0, "plan_id": "plan-late",
         "tasks": [{"task_id": "t2", "agents": ["factory_aerial_1"]}]},
    ]
    report = build_plan_report(specs)
    evidence = extract_committed_plan_evidence(report, dispatch_deadline_s=30.0)
    assert evidence["status"] == EVIDENCE_STATUS_LOCKED
    assert evidence["plan_id"] == "plan-early"
    assert evidence["lock_rule"] == LOCK_RULE_BEFORE_DEADLINE
    # Without a cutoff the batch-0 committed reading takes the last plan.
    evidence = extract_committed_plan_evidence(report)
    assert evidence["plan_id"] == "plan-late"
    assert evidence["lock_rule"] == LOCK_RULE_NO_DISPATCH_NO_DEADLINE
    # A cutoff every plan misses leaves nothing to lock.
    evidence = extract_committed_plan_evidence(report, dispatch_deadline_s=4.0)
    assert evidence["status"] == EVIDENCE_STATUS_NO_PLAN
    assert evidence["no_committed_plan_reason"] == "no_plan_action_before_deadline"


def test_extractor_lists_dispatch_consistency_issues():
    report = build_plan_report([
        {"at": 2.0, "plan_id": "plan-1", "tasks": [
            {"task_id": "t1", "agents": ["factory_ground_1"],
             "milestone_id": "anomaly_confirmed"},
            {"task_id": "t2", "agents": ["factory_aerial_1"]},
        ]},
        {"at": 15.0, "type": "revise_plan", "plan_id": "plan-2",
         "tasks": [{"task_id": "t9", "agents": ["factory_aerial_1"],
                    "milestone_id": "response_area_served"}]},
    ], dispatches=[
        # A clean dispatch matching the locked plan never appears.
        {"task_id": "t2", "at": 6.0, "agents": ["factory_aerial_1"],
         "plan_version": 1},
        # Same task, different robots than the locked assignment.
        {"task_id": "t1", "at": 8.0,
         "agents": ["factory_ground_1", "factory_aerial_1"],
         "plan_version": 1, "milestone_id": "anomaly_confirmed"},
        # Work first introduced by a later revision, absent from the lock.
        {"task_id": "t9", "at": 20.0, "agents": ["factory_aerial_1"],
         "plan_version": 2, "milestone_id": "response_area_served"},
    ])
    evidence = extract_committed_plan_evidence(report)
    issues = {row["task_id"]: row for row in evidence["consistency"]}
    assert set(issues) == {"t1", "t9"}
    assert issues["t1"]["issue"] == "agent_mismatch"
    assert issues["t1"]["locked_plan_agent_ids"] == ["factory_ground_1"]
    assert issues["t1"]["dispatched_agent_ids"] == [
        "factory_ground_1", "factory_aerial_1"]
    assert issues["t1"]["milestone_ref"] == "anomaly_confirmed"
    assert issues["t9"]["issue"] == "task_not_in_locked_plan"
    assert issues["t9"]["locked_plan_agent_ids"] is None
    assert issues["t9"]["milestone_ref"] == "response_area_served"
    assert issues["t9"]["dispatched_plan_version"] == 2


def test_extractor_subtask_rows_join_pool_declarations_and_goal_refs():
    report = build_plan_report([
        {"at": 2.0, "plan_id": "plan-1", "tasks": [
            {"task_id": "t1", "agents": ["factory_ground_1"],
             "milestone_id": "anomaly_confirmed"},
            {"task_id": "t2", "agents": ["factory_aerial_1"]},
            {"task_id": "t3", "agents": ["factory_ground_1",
                                         "factory_aerial_1"]},
        ]},
    ])
    evidence = extract_committed_plan_evidence(report)
    rows = {row["task_id"]: row for row in evidence["subtask_rows"]}
    # Protocol 2.7 field name is exposed alongside the session task id.
    assert rows["t1"]["subtask_id"] == "t1"
    # Goal references are read from explicit keys only, never guessed.
    assert rows["t1"]["milestone_ref"] == "anomaly_confirmed"
    assert rows["t2"]["milestone_ref"] is None
    # Pool capability join and platform category.
    assert rows["t1"]["declared_capabilities"] == {
        "factory_ground_1": ["ground_navigation", "pick"]}
    assert rows["t1"]["role_id"] == "m20"
    assert rows["t1"]["role_ids"] == ["m20"]
    # A mixed coalition has no single platform category.
    assert rows["t3"]["role_id"] is None
    assert rows["t3"]["role_ids"] == ["iris", "m20"]
    assert all(row["plan_sha256"] == evidence["plan_sha256"]
               for row in rows.values())


def test_extractor_reads_declaration_and_truth_from_deployment_archive():
    report = build_plan_report([
        {"at": 2.0, "plan_id": "plan-1",
         "tasks": [{"task_id": "t1", "agents": ["factory_ground_1"]}]},
    ], deployment={
        "history": [{"decision": "deployable",
                     "external_evaluation": None}],
        "oracle_evaluations": [
            {"assessment_id": "a0", "feasibility_label": "not_deployable"},
            {"assessment_id": "a1", "feasibility_label": "deployable"}],
    })
    evidence = extract_committed_plan_evidence(report)
    assert evidence["declared_decision"] == "deployable"
    # The legacy truth word is dual-read onto the protocol vocabulary.
    assert evidence["truth_state"] == "deployable"


# ---------------------------------------------------------------------------
# Judge: gate, components, aggregation semantics
# ---------------------------------------------------------------------------


def test_judge_without_contract_v4_keeps_batch0_placeholder_word():
    result = _judge(None, _evidence(), registry={})
    assert result["status"] == CS_GATE_NO_CONTRACT_STATUS
    assert result["status"] == NOT_APPLICABLE_UNTIL_CONTRACT_V4
    assert result["gate"]["G"] is None
    assert all(entry["rate"] is None
               for entry in result["components"].values())
    assert result["vector"] == {"cap": None, "role": None, "mile": None}


def test_judge_no_committed_plan_zeroes_defined_components():
    task = _v4_task([_cap_constraint()], [_role_constraint()])
    for evidence in (None, {}, _evidence(status=EVIDENCE_STATUS_NO_PLAN)):
        result = _judge(task, evidence, registry={})
        assert result["status"] == CS_GATE_NO_COMMITTED_PLAN_STATUS
        assert result["gate"]["G"] == 0
        # Protocol: G=0 zeroes every *defined* component (never N/A).
        assert result["components"]["cap"]["rate"] == 0.0
        assert result["components"]["cap"]["status"] == COMPONENT_ZEROED_BY_GATE
        assert result["components"]["role"]["rate"] == 0.0
        assert result["components"]["mile"]["rate"] == 0.0
        assert result["vector"] == {"cap": 0.0, "role": 0.0, "mile": 0.0}


def test_judge_gate_zero_on_refusal_or_unlabelled_truth():
    task = _v4_task([_cap_constraint()], [])
    registry = {"strong": _agent("ugv", lift_kg=12.0)}
    subtasks = [("t1", "anomaly_confirmed", ["strong"])]
    cases = (
        (_evidence(subtasks, declared="not_deployable"),
         [GATE_REASON_DECLARATION]),
        # An unlabelled oracle truth cannot enter the risk set: fail closed
        # to G=0 rather than an N/A that dodges constraint evaluation.
        (_evidence(subtasks, truth=None, declared=None),
         [GATE_REASON_TRUTH, GATE_REASON_DECLARATION]),
    )
    for evidence, reasons in cases:
        result = _judge(task, evidence, registry=registry)
        assert result["status"] == CS_STATUS_COMPUTED
        assert result["gate"]["G"] == 0
        assert result["gate"]["failure_reasons"] == reasons
        assert result["components"]["cap"]["rate"] == 0.0
        # The raw obligation outcome stays auditable atomically: the bearer
        # alone would satisfy the threshold, the gate zeroes the vector.
        assert result["atomic_verdicts"]["capability"][0]["satisfied"] == 1


def test_judge_team_state_infeasible_leaves_vector_undefined():
    task = _v4_task([_cap_constraint()], [])
    result = _judge(
        task, _evidence([("t1", "anomaly_confirmed", ["strong"])],
                        truth="team_state_infeasible"),
        registry={"strong": _agent("ugv", lift_kg=12.0)})
    assert result["status"] == CS_STATUS_TEAM_STATE_INFEASIBLE
    assert all(entry["rate"] is None
               for entry in result["components"].values())
    assert all(entry["status"] == COMPONENT_NOT_DEFINED_INFEASIBLE
               for entry in result["components"].values())
    # Not-defined is distinct from the zeroed components of a G=0 episode.
    assert result["atomic_verdicts"]["capability"] == []


def test_judge_single_actor_threshold_semantics():
    task = _v4_task([_cap_constraint()])  # 10 kg, single_actor, zero tolerance
    # Protocol example: a single 5 kg robot fails a 10 kg single-actor duty.
    weak = _judge(task, _evidence([("t1", "anomaly_confirmed", ["weak"])]),
                  registry={"weak": _agent("ugv", lift_kg=5.0)})
    verdict = weak["atomic_verdicts"]["capability"][0]
    assert verdict["satisfied"] == 0
    assert verdict["violation_reason"] == REASON_CAP_UNMET
    assert verdict["failure_label"] == FAILURE_LABEL_CAPABILITY
    assert weak["vector"]["cap"] == 0.0
    # A single 10 kg bearer satisfies it alone.
    strong = _judge(task, _evidence([("t1", "anomaly_confirmed", ["strong"])]),
                    registry={"strong": _agent("ugv", lift_kg=10.0)})
    assert strong["atomic_verdicts"]["capability"][0]["satisfied"] == 1
    assert strong["vector"]["cap"] == 1.0
    # Two bearers are a coalition, not a single actor: the contract demanded
    # single-actor execution, so summing is refused even if each suffices.
    pair = _judge(task, _evidence([("t1", "anomaly_confirmed",
                                    ["a", "b"])]),
                  registry={"a": _agent("ugv", lift_kg=10.0),
                            "b": _agent("ugv", lift_kg=10.0)})
    verdict = pair["atomic_verdicts"]["capability"][0]
    assert verdict["satisfied"] == 0
    assert verdict["violation_reason"] == REASON_SINGLE_ACTOR_BEARERS


def test_judge_coalition_sum_protocol_example_with_tolerance():
    # Protocol: two 5 kg robots satisfy a 10 kg duty only when the contract
    # explicitly says coalition_sum and the tolerance rule holds.
    task = _v4_task([_cap_constraint(aggregation_rule="coalition_sum")])
    two = _judge(task, _evidence([("t1", "anomaly_confirmed", ["a", "b"])]),
                 registry={"a": _agent("ugv", lift_kg=5.0),
                           "b": _agent("ugv", lift_kg=5.0)})
    assert two["vector"]["cap"] == 1.0
    assert two["atomic_verdicts"]["capability"][0]["evidence"][
        "coalition_sum"] == 10.0
    # One 5 kg robot still fails under coalition_sum (5 < 10).
    one = _judge(task, _evidence([("t1", "anomaly_confirmed", ["a"])]),
                 registry={"a": _agent("ugv", lift_kg=5.0)})
    assert one["vector"]["cap"] == 0.0
    assert one["atomic_verdicts"]["capability"][0]["violation_reason"] == (
        REASON_COALITION_BELOW)
    # Frozen tolerance: sum 9.6 is inside +/-0.5 inclusive, 9.4 is outside.
    tolerant = _v4_task([_cap_constraint(
        aggregation_rule="coalition_sum",
        tolerance="unit=kg; numeric tolerance 0.5; boundary inclusive")])
    inside = _judge(tolerant,
                    _evidence([("t1", "anomaly_confirmed", ["a", "b"])]),
                    registry={"a": _agent("ugv", lift_kg=4.8),
                              "b": _agent("ugv", lift_kg=4.8)})
    assert inside["vector"]["cap"] == 1.0
    outside = _judge(tolerant,
                     _evidence([("t1", "anomaly_confirmed", ["a", "b"])]),
                     registry={"a": _agent("ugv", lift_kg=4.7),
                               "b": _agent("ugv", lift_kg=4.7)})
    assert outside["vector"]["cap"] == 0.0


def test_judge_exists_and_all_functional_thresholds():
    subtasks = [("t1", "anomaly_confirmed", ["a", "b"])]
    registry = {"a": _agent("ugv", has_arm=True), "b": _agent("ugv")}
    exists = _v4_task([_cap_constraint(
        capability_type="has_arm", threshold="has_arm",
        aggregation_rule="exists")])
    result = _judge(exists, _evidence(subtasks), registry=registry)
    assert result["vector"]["cap"] == 1.0
    every = _v4_task([_cap_constraint(
        capability_type="has_arm", threshold="has_arm",
        aggregation_rule="all")])
    result = _judge(every, _evidence(subtasks), registry=registry)
    assert result["vector"]["cap"] == 0.0
    assert result["atomic_verdicts"]["capability"][0]["violation_reason"] == (
        "capability_not_declared_by_some_bearer")
    # A numeric registry value never evidences a functional threshold.
    with pytest.raises(ValueError, match="functional"):
        _judge(exists, _evidence(subtasks),
               registry={"a": _agent("ugv", has_arm=3), "b": _agent("ugv")})


def test_judge_role_cardinality_from_registry_categories():
    task = _v4_task([], [_role_constraint()])
    satisfied = _judge(task,
                       _evidence([("t1", "anomaly_confirmed", ["u1"])]),
                       registry={"u1": _agent("uav", lift_kg=1.0)})
    assert satisfied["vector"]["role"] == 1.0
    # Correct capability but the wrong platform category: the role
    # obligation is 0 even though the bearer could physically do the work.
    wrong = _judge(task,
                   _evidence([("t1", "anomaly_confirmed", ["g1"])]),
                   registry={"g1": _agent("ugv", lift_kg=12.0)})
    verdict = wrong["atomic_verdicts"]["role"][0]
    assert verdict["satisfied"] == 0
    assert verdict["violation_reason"] == REASON_ROLE_CARDINALITY
    assert wrong["vector"]["role"] == 0.0
    # Exactly-two cardinality with three matching bearers fails.
    exact = _v4_task([], [_role_constraint(
        cardinality={"count": 2, "comparator": "exactly"})])
    result = _judge(exact,
                    _evidence([("t1", "anomaly_confirmed",
                                ["u1", "u2", "u3"])]),
                    registry={name: _agent("uav")
                              for name in ("u1", "u2", "u3")})
    assert result["vector"]["role"] == 0.0


def test_judge_required_milestones_from_external_results():
    task = _v4_task()
    met = _judge(task, _evidence(), registry={}, results=_results())
    assert met["vector"]["mile"] == 1.0
    assert [row["status"] for row in met["atomic_verdicts"]["milestone"]] == (
        ["satisfied"] * 3)
    unmet = _judge(task, _evidence(), registry={},
                   results=_results(anomaly_confirmed=False))
    assert unmet["vector"]["mile"] == pytest.approx(2 / 3)
    rows = {row["milestone_id"]: row
            for row in unmet["atomic_verdicts"]["milestone"]}
    assert rows["anomaly_confirmed"]["satisfied"] == 0
    assert rows["anomaly_confirmed"]["violation_reason"] == (
        REASON_MILESTONE_UNMET)
    assert rows["anomaly_confirmed"]["failure_label"] == FAILURE_LABEL_MILESTONE
    # Equal weights per milestone: milestone weights never enter CS^mile.
    assert unmet["components"]["mile"]["total"] == 3
    # A missing external result is an evaluator gap recorded per milestone,
    # never silently satisfied.
    missing = _judge(task, _evidence(), registry={},
                     results={"anomaly_confirmed": True})
    rows = {row["milestone_id"]: row
            for row in missing["atomic_verdicts"]["milestone"]}
    assert rows["response_area_served"]["violation_reason"] == (
        REASON_MILESTONE_RESULT_MISSING)
    assert missing["vector"]["mile"] == pytest.approx(1 / 3)


def test_judge_dispatch_inconsistency_zeroes_affected_obligations():
    task = _v4_task([
        _cap_constraint(),
        _cap_constraint(milestone_id="response_area_served",
                        constraint_id="cap-response"),
    ], [_role_constraint()])
    registry = {"r1": _agent("uav", lift_kg=12.0)}
    subtasks = [("t1", "anomaly_confirmed", ["r1"]),
                ("t2", "response_area_served", ["r1"])]
    attributed = [{"task_id": "t2", "issue": "task_not_in_locked_plan",
                   "milestone_ref": "response_area_served",
                   "locked_plan_version": 1, "locked_plan_agent_ids": None,
                   "dispatched_plan_version": 2,
                   "dispatched_agent_ids": ["r2"]}]
    result = _judge(task, _evidence(subtasks, attributed), registry=registry)
    rows = {row["constraint_id"]: row
            for row in result["atomic_verdicts"]["capability"]}
    # The unaffected milestone's obligation keeps its own verdict.
    assert rows["cap-lift"]["satisfied"] == 1
    assert rows["cap-response"]["satisfied"] == 0
    assert rows["cap-response"]["violation_reason"] == REASON_DISPATCH_INCONSISTENT
    assert result["vector"]["cap"] == 0.5
    assert result["vector"]["role"] == 1.0
    # An unattributable inconsistency (no milestone reference on the
    # dispatched task) cannot be reconciled with any obligation of the
    # committed plan and therefore zeroes all of them.
    unattributed = [dict(attributed[0], milestone_ref=None)]
    result = _judge(task, _evidence(subtasks, unattributed),
                    registry=registry)
    assert all(row["satisfied"] == 0
               for row in result["atomic_verdicts"]["capability"])
    assert result["vector"]["cap"] == 0.0


def test_judge_empty_activation_sets_are_na_not_zero_or_one():
    task = _v4_task()  # no obligations authored; all milestones required
    result = _judge(task, _evidence(), registry={}, results=_results())
    assert result["components"]["cap"]["rate"] is None
    assert result["components"]["cap"]["status"] == COMPONENT_NA_EMPTY_SET
    assert result["components"]["role"]["rate"] is None
    assert result["components"]["role"]["status"] == COMPONENT_NA_EMPTY_SET
    assert result["components"]["mile"]["rate"] == 1.0
    assert result["vector"] == {"cap": None, "role": None, "mile": 1.0}
    # G=0 zeroes only defined components; the empty sets stay N/A.
    refused = _judge(task, _evidence(declared="not_deployable"),
                     registry={}, results=_results())
    assert refused["components"]["cap"]["rate"] is None
    assert refused["components"]["cap"]["status"] == COMPONENT_NA_EMPTY_SET
    assert refused["components"]["mile"]["rate"] == 0.0
    assert refused["components"]["mile"]["status"] == COMPONENT_ZEROED_BY_GATE


def test_judge_vector_rates_and_atomic_audit_structure():
    task = _v4_task([
        _cap_constraint(),  # anomaly: bearer alone lifts 10 kg -> satisfied
        _cap_constraint(milestone_id="response_area_served",
                        constraint_id="cap-heavy", threshold=30.0),
    ], [_role_constraint(milestone_id="maintenance_handoff_recorded")])
    subtasks = [("t1", "anomaly_confirmed", ["strong"]),
                ("t2", "response_area_served", ["strong"]),
                ("t3", "maintenance_handoff_recorded", ["u1"])]
    registry = {"strong": _agent("ugv", lift_kg=12.0),
                "u1": _agent("uav", lift_kg=1.0)}
    results = {"anomaly_confirmed": True, "response_area_served": False,
               "maintenance_handoff_recorded": True}
    result = _judge(task, _evidence(subtasks), registry=registry,
                    results=results)
    assert result["status"] == CS_STATUS_COMPUTED
    assert result["gate"]["G"] == 1
    assert result["vector"]["cap"] == 0.5
    assert result["vector"]["role"] == 1.0
    assert result["vector"]["mile"] == pytest.approx(2 / 3)
    # Atomic audit trail: ids, binary verdicts and reasons per obligation.
    capability_rows = result["atomic_verdicts"]["capability"]
    assert [row["constraint_id"] for row in capability_rows] == [
        "cap-lift", "cap-heavy"]
    assert [row["satisfied"] for row in capability_rows] == [1, 0]
    assert capability_rows[1]["violation_reason"] == REASON_CAP_UNMET
    role_rows = result["atomic_verdicts"]["role"]
    assert role_rows[0]["constraint_id"] == "role-uav"
    assert role_rows[0]["satisfied"] == 1
    assert role_rows[0]["violation_reason"] is None
    milestone_rows = result["atomic_verdicts"]["milestone"]
    assert [row["milestone_id"] for row in milestone_rows] == list(
        FACTORY_OUTCOMES)
    # Denominators never move with subtask splitting: 2/1/3.
    assert [result["components"][name]["total"]
            for name in ("cap", "role", "mile")] == [2, 1, 3]
    working_set = result["activation_working_set"]
    assert working_set["capability_constraint_ids"] == ["cap-lift",
                                                        "cap-heavy"]
    assert working_set["required_milestone_ids"] == list(FACTORY_OUTCOMES)
    assert working_set["excluded_exogenous_activation"] == {
        "capability_constraint_ids": [], "role_constraint_ids": [],
        "milestone_ids": [],
        "note": ("non episode_start activation rules are declared "
                 "exogenous evaluation gaps; never guessed here"),
    }


def test_judge_exogenous_activation_stays_excluded_never_guessed():
    task = _v4_task([_cap_constraint(activation_rule="exogenous_event:relay")])
    result = _judge(task, _evidence([("t1", "anomaly_confirmed", ["x"])]),
                    registry={"x": _agent("ugv", lift_kg=99.0)})
    # The non-episode-start obligation is excluded from the working set and
    # the cap component is N/A - the exogenous evaluation is a declared gap.
    assert result["components"]["cap"]["rate"] is None
    assert result["components"]["cap"]["status"] == COMPONENT_NA_EMPTY_SET
    assert result["activation_working_set"]["capability_constraint_ids"] == []
    assert result["activation_working_set"]["excluded_exogenous_activation"][
        "capability_constraint_ids"] == ["cap-lift"]
    # Unattributed subtask rows (frozen v2 plans carry no milestone refs)
    # leave obligations explicitly unattributed instead of guessed.
    task = _v4_task([_cap_constraint()])
    result = _judge(task, _evidence([("t1", None, ["x"])]),
                    registry={"x": _agent("ugv", lift_kg=99.0)})
    verdict = result["atomic_verdicts"]["capability"][0]
    assert verdict["satisfied"] == 0
    assert verdict["violation_reason"] == REASON_NO_SUBTASK_FOR_MILESTONE


def test_judge_fails_closed_on_malformed_inputs():
    task = _v4_task([_cap_constraint()], [])
    registry = {"strong": _agent("ugv", lift_kg=12.0)}
    subtasks = [("t1", "anomaly_confirmed", ["strong"])]
    # Non-boolean external predicate result.
    with pytest.raises(ValueError, match="boolean"):
        _judge(task, _evidence(subtasks), registry=registry,
               results={"anomaly_confirmed": 1})
    # Result for a milestone the contract does not carry.
    with pytest.raises(ValueError, match="unknown milestones"):
        _judge(task, _evidence(subtasks), registry=registry,
               results=_results(ghost_outcome=True))
    # Registry entry with fields outside the auditable surface.
    with pytest.raises(ValueError, match="fields mismatch"):
        _judge(task, _evidence(subtasks),
               registry={"strong": {"role_id": "ugv", "capabilities": {},
                                    "note": "extra"}})
    # Evidence that is not an extractor result is a caller error.
    with pytest.raises(ValueError, match="committed-plan-evidence"):
        _judge(task, {"plan_version": 2, "plan_sha256": "deadbeef"},
               registry=registry)
    missing_rows = _evidence(subtasks)
    del missing_rows["subtask_rows"]
    with pytest.raises(ValueError, match="subtask_rows"):
        _judge(task, missing_rows, registry=registry)


# ---------------------------------------------------------------------------
# Predicate-binding registry
# ---------------------------------------------------------------------------


def test_predicate_binding_registry_seals_and_detects_tampering():
    task = _v4_task()
    registry = register_predicate_binding(task, {
        "anomaly_confirmed": {"evaluator_ref": "evaluator://anomaly/v3",
                              "parameters_digest": "b" * 64}})
    report = validate_predicate_bindings(task, registry)
    assert report["validated"] is True
    assert report["bound_milestone_ids"] == ["anomaly_confirmed"]
    assert report["unbound_milestone_ids"] == [
        "maintenance_handoff_recorded", "response_area_served"]
    # The sealed predicate_ref is read from the contract, never the caller.
    assert registry["bindings"]["anomaly_confirmed"]["predicate_ref"] == (
        "legacy_business_outcome:anomaly_confirmed")
    # Tampering with the sealed binding breaks the seal.
    tampered = deepcopy(registry)
    tampered["bindings"]["anomaly_confirmed"]["evaluator_ref"] = "evil://x"
    with pytest.raises(ValueError, match="seal mismatch"):
        validate_predicate_bindings(task, tampered)
    # A drifted contract is no longer the sealed contract.
    other = _v4_task()
    other["contract_milestones"][0]["weight"] = 9.0
    with pytest.raises(ValueError, match="different contract"):
        validate_predicate_bindings(other, registry)
    # Registration rejects unknown milestones and malformed binding entries.
    with pytest.raises(ValueError, match="unknown milestones"):
        register_predicate_binding(task, {
            "ghost": {"evaluator_ref": "e", "parameters_digest": "d"}})
    with pytest.raises(ValueError, match="fields mismatch"):
        register_predicate_binding(task, {
            "anomaly_confirmed": {"evaluator_ref": "e"}})


def test_judge_unbound_predicate_is_never_guessed_from_raw_results():
    task = _v4_task()
    registry = register_predicate_binding(task, {
        "anomaly_confirmed": {"evaluator_ref": "evaluator://anomaly/v3",
                              "parameters_digest": "b" * 64}})
    # Raw boolean results exist for every milestone, but only the bound one
    # has a trusted evaluator: the unbound ones are predicate_unbound.
    result = _judge(task, _evidence(), registry={}, results=_results(),
                    bindings=registry)
    rows = {row["milestone_id"]: row
            for row in result["atomic_verdicts"]["milestone"]}
    assert rows["anomaly_confirmed"]["satisfied"] == 1
    for milestone_id in ("response_area_served",
                         "maintenance_handoff_recorded"):
        assert rows[milestone_id]["status"] == REASON_PREDICATE_UNBOUND
        assert rows[milestone_id]["violation_reason"] == REASON_PREDICATE_UNBOUND
        assert rows[milestone_id]["satisfied"] == 0
        assert rows[milestone_id]["failure_label"] == FAILURE_LABEL_MILESTONE
    assert result["vector"]["mile"] == pytest.approx(1 / 3)
    # A tampered registry is refused before any verdict is produced.
    tampered = deepcopy(registry)
    tampered["bindings"]["anomaly_confirmed"]["parameters_digest"] = "x" * 64
    with pytest.raises(ValueError, match="seal mismatch"):
        _judge(task, _evidence(), registry={}, results=_results(),
               bindings=tampered)


def test_release_flags_stay_false_on_every_output():
    task = _v4_task([_cap_constraint()], [_role_constraint()])
    outputs = [
        extract_committed_plan_evidence(build_plan_report([])),
        extract_committed_plan_evidence(build_plan_report([
            {"at": 2.0, "plan_id": "plan-1",
             "tasks": [{"task_id": "t1", "agents": ["factory_ground_1"]}]}])),
        register_predicate_binding(task, {}),
        validate_predicate_bindings(task, register_predicate_binding(task, {})),
        _judge(None, None),
        _judge(task, None, registry={}),
        _judge(task, _evidence(), registry={
            "r1": _agent("uav", lift_kg=12.0)}, results=_results()),
    ]
    for output in outputs:
        assert output["release_eligible"] is False
        assert output["scoreable"] is False
        assert output["execution_validated"] is False
        assert output["not_protocol_score"] is True
