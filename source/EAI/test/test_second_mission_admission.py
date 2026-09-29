"""Second-mission plan admission: interface gate in, capability mismatch out.

The interface admission mode proves only schema well-formedness, robot/skill
reference existence, and the frozen action vocabulary.  Capability, mobility
and region mismatches pass the gate and become execution measurements recorded
by ``second_mission_constraint_violation`` (the task-organization measurement
point).  Default ``full`` admission keeps rejecting the same plans, so these
tests pin both directions for every published second-mission package.
"""

from copy import deepcopy

import pytest

from demo.benchmark.goal_driven_contract import (
    validate_goal_action,
    validate_goal_plan,
)
from demo.benchmark.goal_house_episode import second_mission_constraint_violation
from demo.benchmark.goal_session import GoalSession
from demo.benchmark.goal_session_audit import (
    AuditedGoalSession,
    replay_goal_session_audit,
)
from demo.benchmark.provenance import canonical_sha256
from demo.benchmark.second_mission_episode import second_mission_builders


def _packages() -> list[tuple[str, dict]]:
    return sorted((task_id, build()) for task_id, build in second_mission_builders().items())


def _action(identifier: str, kind: str, payload: dict) -> dict:
    return {
        "schema": "eai.benchmark.goal_action",
        "version": 1,
        "action_id": identifier,
        "submitted_at_s": 0.0,
        "type": kind,
        "payload": deepcopy(payload),
    }


def _replace_agent(plan: dict, source: str, target: str) -> dict:
    """Swap one pool robot for another everywhere it appears in the plan."""
    replaced = deepcopy(plan)
    replaced["assigned_team"] = [
        target if agent == source else agent for agent in replaced["assigned_team"]]
    for task in replaced["tasks"]:
        task["assigned_agents"] = [
            target if agent == source else agent for agent in task["assigned_agents"]]
        if task["skill"] == "synchronize":
            task["arguments"]["agent_ids"] = [
                target if agent == source else agent
                for agent in task["arguments"]["agent_ids"]]
        # Keep carrier bindings consistent with the swapped assignment so the
        # remaining mismatch is purely capability-shaped; binding/assignment
        # divergence is itself structurally rejected in both modes.
        for binding in (task.get("arguments") or {}).get("carrier_bindings") or []:
            if binding.get("agent_id") == source:
                binding["agent_id"] = target
    return replaced


def _capability_mismatch(public: dict, plan: dict) -> tuple[str, str, str]:
    """Find a pool robot swap that the execution layer records as a violation.

    Probes each (task, agent, candidate) combination with the stable public
    ``second_mission_constraint_violation`` contract; the returned reason is
    the organization failure the dispatch layer will record.
    """
    pool = {row["id"]: row for row in public["candidate_robot_pool"]}
    for task in plan["tasks"]:
        for agent in task["assigned_agents"]:
            for candidate in sorted(pool):
                if candidate == agent or candidate in task["assigned_agents"]:
                    continue
                if candidate in plan["assigned_team"]:
                    continue  # keep assigned_team/agent lists unique
                probe = deepcopy(task)
                probe["assigned_agents"] = [
                    candidate if member == agent else member
                    for member in task["assigned_agents"]]
                reason = second_mission_constraint_violation(public, probe)
                if reason is not None:
                    return agent, candidate, reason
    raise AssertionError("no capability mismatch is constructible for this package")


def test_interface_admission_measures_every_second_mission_mismatch() -> None:
    """Each second mission: mismatch passes interface, fails execution, full rejects."""
    for task_id, bundle in _packages():
        public = bundle["public_task"]
        api = bundle["participant_api"]
        plan = bundle["hidden_evaluator"]["witness_plans"][0]["plan"]

        # Control: the unmodified witness plan stays legal under both gates.
        assert validate_goal_plan(plan, task_spec=public)["validated"] is True
        assert validate_goal_plan(
            plan, task_spec=public, admission="interface")["validated"] is True

        source, target, reason = _capability_mismatch(public, plan)
        mismatched = _replace_agent(plan, source, target)

        # Full admission (author/witness fail-closed semantics) rejects it.
        with pytest.raises(ValueError):
            validate_goal_plan(mismatched, task_spec=public)

        # Interface admission is structural only and lets the plan through.
        admitted = validate_goal_plan(mismatched, task_spec=public, admission="interface")
        assert admitted["validated"] is True
        assert admitted["constraint_review"]["admission"] == "interface"
        assert admitted["constraint_review"]["execution_validated"] is False

        # The execution layer records the organization failure for the swap.
        assert reason.startswith("constraint_violation:")
        violating = [task for task in mismatched["tasks"]
                     if second_mission_constraint_violation(public, task) is not None]
        assert violating, task_id

        # Session ledger: default full session blocks, interface session admits.
        blocked = GoalSession(public, api)
        blocked.submit_action(_action(
            f"{task_id}-select-full", "select_team", {"agent_ids": mismatched["assigned_team"]}))
        with pytest.raises(ValueError):
            blocked.submit_action(_action(
                f"{task_id}-plan-full", "submit_plan", {"plan": mismatched}))

        measured = GoalSession(public, api, admission="interface")
        measured.submit_action(_action(
            f"{task_id}-select", "select_team", {"agent_ids": mismatched["assigned_team"]}))
        measured.submit_action(_action(
            f"{task_id}-plan", "submit_plan", {"plan": mismatched}))


def test_interface_admission_still_rejects_structurally_broken_plans() -> None:
    """Unknown robots/skills, foreign-catalog skills, and shape errors stay fatal."""
    bundle = _packages()[0][1]
    public = bundle["public_task"]
    api = bundle["participant_api"]
    plan = bundle["hidden_evaluator"]["witness_plans"][0]["plan"]

    def unknown_robot_team(probe: dict) -> None:
        probe["assigned_team"] = ["no_such_robot"]

    def unknown_global_skill(probe: dict) -> None:
        probe["tasks"][0]["skill"] = "teleport"

    def skill_outside_task_catalog(probe: dict) -> None:
        task = probe["tasks"][0]
        task["skill"] = "aerial_broadcast"
        task["arguments"] = {"person_ids": ["person_a"], "observation_refs": ["obs_a"]}
        task["required_capabilities"] = ["aerial_navigation", "relay"]

    def missing_field(probe: dict) -> None:
        del probe["tasks"][0]["execution"]

    def missing_required_argument(probe: dict) -> None:
        arguments = probe["tasks"][0]["arguments"]
        arguments.pop("region_id", None)
        arguments.pop("route_ids", None)

    def unknown_required_capability(probe: dict) -> None:
        probe["tasks"][0]["required_capabilities"] = ["levitation", "observe"]

    def assigned_outside_team(probe: dict) -> None:
        pool = {row["id"] for row in public["candidate_robot_pool"]}
        outsider = sorted(pool - set(probe["assigned_team"]) - {"factory_iris_1"})[0]
        probe["tasks"][0]["assigned_agents"] = [outsider]

    for label, mutate in (
        ("unknown robot id", unknown_robot_team),
        ("unknown global skill", unknown_global_skill),
        ("skill outside the task skill catalog", skill_outside_task_catalog),
        ("missing plan-task field", missing_field),
        ("missing required argument", missing_required_argument),
        ("unknown required capability", unknown_required_capability),
        ("assigned agent outside the team", assigned_outside_team),
    ):
        probe = deepcopy(plan)
        mutate(probe)
        with pytest.raises(ValueError):
            validate_goal_plan(probe, task_spec=public, admission="interface")

    # The six-action vocabulary stays closed under interface admission.
    with pytest.raises(ValueError):
        validate_goal_action(
            _action("vocab-probe", "execute_task", {"task_id": plan["tasks"][0]["task_id"]}),
            api=api, task_spec=public, admission="interface")

    # Robot-reference existence is structural and survives in both modes.
    probe = deepcopy(plan)
    unknown_robot_team(probe)
    with pytest.raises(ValueError):
        validate_goal_plan(probe, task_spec=public)


def test_audit_journal_replays_interface_admission_with_the_violation_receipt() -> None:
    """An interface-mode audit journal replays under the same admission gate."""
    bundle = next(build() for task_id, build in sorted(second_mission_builders().items())
                  if task_id == "FACTORY-FIRE-01")
    public, api = bundle["public_task"], bundle["participant_api"]
    plan = bundle["hidden_evaluator"]["witness_plans"][0]["plan"]
    source, target, _ = _capability_mismatch(public, plan)
    mismatched = _replace_agent(plan, source, target)
    violating_task_id = next(task["task_id"] for task in mismatched["tasks"]
                             if second_mission_constraint_violation(public, task) is not None)

    session = AuditedGoalSession(
        public, api, episode_id="admission-audit", admission="interface")
    session.submit_action(_action(
        "admission-select", "select_team", {"agent_ids": mismatched["assigned_team"]}))
    session.submit_action(_action(
        "admission-plan", "submit_plan", {"plan": mismatched}))
    session.submit_action(_action(
        "admission-dispatch", "dispatch_skill",
        {"task_id": violating_task_id, "evidence_refs": []}))
    receipt = {
        "backend_id": "admission-test-backend",
        "receipt_ref": f"admission-test/{violating_task_id}",
        "task_id": violating_task_id,
        "action_id": "admission-dispatch",
        "status": "failed",
        "completed_at_s": 0.5,
        "episode_id": "admission-audit",
        "evidence_refs": [f"admission-test:{violating_task_id}:failed"],
    }
    completion = session.complete_skill(
        violating_task_id, time_s=0.5, status="failed", receipt=receipt)
    assert completion["accepted"] is True

    journal = session.audit_journal()
    assert journal["plan_admission"] == "interface"
    # The constraint-violation failure receipt is journaled verbatim.
    completed_calls = [call for call in journal["calls"] if call["method"] == "complete_skill"]
    assert len(completed_calls) == 1
    assert completed_calls[0]["arguments"]["status"] == "failed"
    assert completed_calls[0]["arguments"]["receipt"]["receipt_ref"] == receipt["receipt_ref"]
    # session.report() keeps the failed status and the terminal receipt; the
    # admission association lives in the journal's plan_admission marker.
    final = journal["final_report"]
    assert final["tasks"][violating_task_id]["status"] == "failed"
    assert final["tasks"][violating_task_id]["receipt"]["status"] == "failed"

    # Replay rebuilds the ledger under the recorded admission mode.
    replay = replay_goal_session_audit(journal, public, api)
    assert replay["validated"] is True
    assert replay["calls_replayed"] == len(journal["calls"])

    # The journal admission marker is load-bearing: relabelling it as full and
    # re-sealing the digest must break the replay, because full admission
    # rejects the mismatched plan that the original gate accepted.
    tampered = deepcopy(journal)
    tampered["plan_admission"] = "full"
    tampered["journal_sha256"] = canonical_sha256({
        key: value for key, value in tampered.items() if key != "journal_sha256"})
    with pytest.raises(ValueError):
        replay_goal_session_audit(tampered, public, api)


def test_audit_journal_without_admission_field_stays_full_mode() -> None:
    """Archives written before interface admission existed remain readable."""
    from demo.benchmark.goal_session_audit import validate_goal_session_audit

    bundle = next(build() for task_id, build in sorted(second_mission_builders().items())
                  if task_id == "FACTORY-FIRE-01")
    public, api = bundle["public_task"], bundle["participant_api"]
    legacy = AuditedGoalSession(public, api).audit_journal()
    legacy.pop("plan_admission")
    legacy["journal_sha256"] = canonical_sha256({
        key: value for key, value in legacy.items() if key != "journal_sha256"})
    # A pre-interface archive (no marker) validates and replays under full.
    validated = validate_goal_session_audit(legacy)
    assert "plan_admission" not in validated
    replay = replay_goal_session_audit(legacy, public, api)
    assert replay["validated"] is True
    # New full-mode journals record the marker explicitly.
    fresh = AuditedGoalSession(public, api).audit_journal()
    assert fresh["plan_admission"] == "full"


def test_carrier_binding_mismatch_is_rejected_in_both_admission_modes() -> None:
    """A plan may not dispatch to robot A while the backend would execute B."""
    from copy import deepcopy

    import pytest

    from demo.benchmark.goal_driven_contract import validate_goal_plan
    from demo.benchmark.second_mission_episode import second_mission_builders

    bundle = next(build() for task_id, build in sorted(second_mission_builders().items())
                  if task_id == "RESTAURANT-SERVICE-01")
    public = bundle["public_task"]
    plan = deepcopy(bundle["hidden_evaluator"]["witness_plans"][0]["plan"])
    bound = next(task for task in plan["tasks"]
                 if "carrier_bindings" in (task.get("arguments") or {}))
    other = next(row["id"] for row in public["candidate_robot_pool"]
                 if row["id"] not in bound["assigned_agents"])
    swapped = deepcopy(plan)
    task = next(row for row in swapped["tasks"] if row["task_id"] == bound["task_id"])
    task["assigned_agents"] = [other]
    task["arguments"]["carrier_bindings"][0]["agent_id"] = bound["assigned_agents"][0]
    swapped["assigned_team"] = sorted(set(swapped["assigned_team"]) | {other})
    for mode in (None, "interface"):
        kwargs = {"admission": mode} if mode else {}
        with pytest.raises(ValueError, match="carrier bindings must match"):
            validate_goal_plan(swapped, task_spec=public, **kwargs)
    # The untouched witness plan still passes both modes.
    validate_goal_plan(plan, task_spec=public)
    validate_goal_plan(plan, task_spec=public, admission="interface")
