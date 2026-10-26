from __future__ import annotations

from types import SimpleNamespace

import pytest

from benchmark.evaluator.room06_livt04 import (
    EVALUATOR_VERSION,
    Room06LivT04Evaluator,
    Room06LivT04EvaluatorError,
    build_diagnostic_runtime_fixture,
)
from tools.benchmark_branch.receipt_material import collect_receipt_material


BOUNDING = {
    "schema": "heterocoord.oracle-obligation-binding.v1",
    "anchor_id": "liv-t-04",
    "obligations": [{
        "obligation_id": "relay",
        "relation": "P2",
        "predicate_ids": ["upper_shelf_delivery_after_transition_handoff"],
    }],
}


def _job():
    return SimpleNamespace(task_id="liv-t-04", scene_key="room_06")


def _runtime(*, late=False, holds=2, integrity=True):
    return {
        "episode": {
            "status": "valid_terminal", "condition_id": "C0", "branch": "fact",
            "repeat_id": "r1", "checkpoint_id": "cp-0",
            "evaluator_version": EVALUATOR_VERSION,
        },
        "streams": {
            "zone_intervals": [],
            "hold_intervals": [
                {"object_id": "heavy_object", "robot_id": f"carrier-{i}",
                 "start_tick": 10, "end_tick": 20}
                for i in range(holds)
            ],
            "milestones": [
                {"label": "relay_started", "tick": 1},
                {"label": "relay_complete", "tick": 3},
                {"label": "heavy_carry_complete", "tick": 20},
                {"label": "accepted", "tick": 15001 if late else 100},
            ],
            "task_events": [
                {"name": "transition_handoff_observed",
                 "subject": "topology_transition_node", "tick": 2},
                {"name": "bilateral_support_established",
                 "subject": "heavy_object", "tick": 10},
            ],
            "identity_records": {},
            "integrity_confirmed": integrity,
        },
        "base_facts": {
            "d1": {"validity": 1.0, "organization": 1.0,
                   "infeasible": 0.0, "complete_plan": True},
            "base_task_success": True,
            "d4_items": [],
            "d6": {"bits": 10.0, "time": 1.0},
        },
        "obligation_binding": BOUNDING,
    }


def test_collects_explicit_material_and_reduces_oracle_facts():
    material = Room06LivT04Evaluator().collect_receipt_material(_runtime(), _job())
    assert material["episode"]["evaluator_version"] == EVALUATOR_VERSION
    assert material["facts"]["obligations"] == [{"relation": "P2", "outcome": "completed"}]
    assert material["facts"]["success"] is True
    assert material["facts"]["d6"]["success"] is True


@pytest.mark.parametrize("kwargs", [{"late": True}, {"holds": 1}])
def test_deadline_or_joint_hold_failure_is_not_hidden(kwargs):
    material = Room06LivT04Evaluator().collect_receipt_material(_runtime(**kwargs), _job())
    assert material["facts"]["success"] is False
    assert material["facts"]["d6"]["success"] is False


def test_missing_integrity_for_missing_hold_is_fail_closed():
    with pytest.raises(Room06LivT04EvaluatorError, match="oracle success evidence unresolved"):
        Room06LivT04Evaluator().collect_receipt_material(
            _runtime(holds=0, integrity=False), _job()
        )


def test_rejects_zone_or_non_task_object_invention():
    runtime = _runtime()
    runtime["streams"]["zone_intervals"] = [{
        "zone_id": "invented-zone", "holder_id": "carrier-0",
        "enter_tick": 1, "exit_tick": 2,
    }]
    with pytest.raises(Room06LivT04EvaluatorError, match="zone_intervals must be empty"):
        Room06LivT04Evaluator().collect_receipt_material(runtime, _job())

    runtime = _runtime()
    runtime["streams"]["hold_intervals"][0]["object_id"] = "book_batch_01"
    with pytest.raises(Room06LivT04EvaluatorError, match="heavy_object"):
        Room06LivT04Evaluator().collect_receipt_material(runtime, _job())


def test_rejects_derived_success_supplied_by_caller():
    runtime = _runtime()
    runtime["base_facts"]["success"] = True
    with pytest.raises(Room06LivT04EvaluatorError, match="derived success"):
        Room06LivT04Evaluator().collect_receipt_material(runtime, _job())


def test_diagnostic_fixture_uses_same_generic_receipt_hook():
    """The CPU fixture exercises the executor's public adapter seam only."""

    evaluator = Room06LivT04Evaluator()
    material = collect_receipt_material(
        evaluator, build_diagnostic_runtime_fixture(), _job()
    )
    assert set(material) == {"episode", "streams", "facts"}
    assert material["facts"]["success"] is True
    assert material["facts"]["d6"]["success"] is True


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [({"late": True}, False), ({"holders": 1}, False)],
)
def test_diagnostic_fixture_never_masks_oracle_failure(kwargs, expected):
    material = Room06LivT04Evaluator().collect_receipt_material(
        build_diagnostic_runtime_fixture(**kwargs), _job()
    )
    assert material["facts"]["success"] is expected
    assert material["facts"]["d6"]["success"] is expected
