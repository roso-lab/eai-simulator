"""Focused checks for the goal ledger split and external process boundary."""

import sys

import pytest

from demo.benchmark.algorithm_adapter import run_algorithm_subprocess
from demo.benchmark.goal_driven_contract import (
    GOAL_ACTION_VERSION,
    GOAL_CONTRACT_VERSION,
    GOAL_PLAN_VERSION,
)
from demo.benchmark.goal_driven_tasks import build_goal_task_package
from demo.benchmark.goal_session import GoalSession


def test_goal_protocol_versions_are_independent_and_explicit():
    assert GOAL_CONTRACT_VERSION == 2
    assert GOAL_PLAN_VERSION == 3
    assert GOAL_ACTION_VERSION == 1


def test_business_outcome_is_recorded_separately_from_skill_execution():
    public = build_goal_task_package("warehouse_nvd")["public_task"]
    session = GoalSession(public)
    initial = session.report()
    assert initial["skill_execution"]["task_statuses"] == {}
    assert initial["business_task_success"] is None
    outcome_ids = [row["id"] for row in public["required_outcomes"]]

    session.record_business_outcome(
        outcome_ids[0], status="validated", evidence_refs=["detector://one"],
        evaluator_ref="warehouse-evaluator/attempt-1")
    partial = session.report()
    assert partial["business_outcomes_validated"] is False
    assert partial["business_task_success"] is None
    assert partial["business_outcomes"][0]["status"] == "validated"
    assert partial["execution_validated"] is False

    session.record_business_outcome(
        outcome_ids[1], status="failed", evidence_refs=["dock://two"],
        evaluator_ref="warehouse-evaluator/attempt-1")
    final = session.report()
    assert final["business_outcomes_validated"] is False
    assert final["business_task_success"] is False
    assert final["skill_execution"]["succeeded_task_ids"] == []
    repeat = session.record_business_outcome(
        outcome_ids[1], status="failed", evidence_refs=["dock://two"],
        evaluator_ref="warehouse-evaluator/attempt-1")
    assert repeat["idempotent"] is True
    with pytest.raises(ValueError, match="terminal business outcome"):
        session.record_business_outcome(
            outcome_ids[1], status="validated", evidence_refs=["dock://revised"],
            evaluator_ref="warehouse-evaluator/attempt-2")


def test_business_success_requires_all_outcomes_and_keeps_execution_separate():
    public = build_goal_task_package("warehouse_nvd")["public_task"]
    session = GoalSession(public)
    for index, row in enumerate(public["required_outcomes"]):
        result = session.record_business_outcome(
            row["id"], status="validated", evidence_refs=[f"evidence://{row['id']}"],
            evaluator_ref="warehouse-evaluator/attempt-2")
        if index == len(public["required_outcomes"]) - 1:
            assert result["business_task_success"] is True
        else:
            assert result["business_task_success"] is None
    report = session.report()
    assert report["business_outcomes_validated"] is True
    assert report["business_task_success"] is True
    assert report["execution_validated"] is False
    assert report["skill_execution"]["validated"] is False


def test_subprocess_boundary_accepts_public_json_only():
    code = (
        "import json,sys; p=json.load(sys.stdin); "
        "print(json.dumps({'echo': p['context']['value']}))"
    )
    result = run_algorithm_subprocess(
        [sys.executable, "-c", code], manifest={"id": "algo"},
        context={"value": 7}, timeout_s=2)
    assert result["status"] == "completed"
    assert result["output"] == {"echo": 7}


def test_subprocess_boundary_classifies_timeout_and_rejects_hidden_data():
    code = "import time; time.sleep(2)"
    result = run_algorithm_subprocess(
        [sys.executable, "-c", code], manifest={"id": "algo"},
        context={}, timeout_s=0.05)
    assert result["status"] == "timeout"
    assert result["failure_class"] == "algorithm_timeout"

    invalid = run_algorithm_subprocess(
        [sys.executable, "-c", "print('not-json')"],
        manifest={"id": "algo"}, context={}, timeout_s=1)
    assert invalid["status"] == "invalid"
    assert invalid["failure_class"] == "algorithm_protocol_invalid"

    crashed = run_algorithm_subprocess(
        [sys.executable, "-c", "raise RuntimeError('planner crash')"],
        manifest={"id": "algo"}, context={}, timeout_s=1)
    assert crashed["status"] == "failed"
    assert crashed["failure_class"] == "algorithm_exception"

    non_finite = run_algorithm_subprocess(
        [sys.executable, "-c", "print('{\"metric\": NaN}')"],
        manifest={"id": "algo"}, context={}, timeout_s=1)
    assert non_finite["status"] == "invalid"
    assert non_finite["failure_class"] == "algorithm_protocol_invalid"

    invalid_utf8 = run_algorithm_subprocess(
        [sys.executable, "-c", "import sys; sys.stdout.buffer.write(bytes([255]))"],
        manifest={"id": "algo"}, context={}, timeout_s=1)
    assert invalid_utf8["status"] == "invalid"
    assert invalid_utf8["failure_class"] == "algorithm_protocol_invalid"

    with pytest.raises(ValueError, match="hidden evaluator"):
        run_algorithm_subprocess(
            [sys.executable, "-c", "print('{}')"],
            manifest={"id": "algo", "hidden_evaluator": True}, context={}, timeout_s=1)
