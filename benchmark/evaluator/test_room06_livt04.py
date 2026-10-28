from __future__ import annotations

from types import SimpleNamespace

import pytest

from benchmark.d4_first_event_v1 import (
    FACT_KEY as D4_FIRST_EVENT_FACT_KEY,
    OBSERVATION_SCHEMA as D4_OBSERVATION_SCHEMA,
)
from benchmark.evaluator.room06_livt04 import (
    DIAGNOSTIC_RUNTIME_BINDING,
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
    return SimpleNamespace(
        task_id="liv-t-04",
        scene_key="room_06",
        system_id="emos-zero",
        condition_id="C0",
        repeat_id="r1",
    )


def _d4_observation():
    return {
        "schema": D4_OBSERVATION_SCHEMA,
        "registration_id": "room06-loss-v1",
        "panel_id": "room06-loss",
        "cell_id": "room06-cell",
        "event_id": "task-function-loss",
        "event_eligible": True,
        "eligibility_evidence_digest": "1" * 64,
        "response": {"tick": 120, "evidence_digest": "2" * 64},
        "work_confirmation": {"tick": 115, "evidence_digest": "3" * 64},
        "terminal": None,
        "safety": {"flag": False, "evidence_digest": None},
        "observed_through_tick": 200,
        "timeline_digest": "4" * 64,
    }


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
                 "subject": DIAGNOSTIC_RUNTIME_BINDING["transition_handoff_node_id"],
                 "tick": 2},
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
        "runtime_binding": dict(DIAGNOSTIC_RUNTIME_BINDING),
    }


def test_collects_explicit_material_and_reduces_oracle_facts():
    material = Room06LivT04Evaluator().collect_receipt_material(_runtime(), _job())
    assert material["episode"]["evaluator_version"] == EVALUATOR_VERSION
    assert material["episode"]["system_id"] == "emos-zero"
    assert material["facts"]["obligations"] == [{"relation": "P2", "outcome": "completed"}]
    assert material["facts"]["success"] is True
    assert material["facts"]["d6"]["success"] is True


def test_episode_cannot_override_branch_job_system_identity():
    runtime = _runtime()
    runtime["episode"]["system_id"] = "another-system"

    with pytest.raises(
        Room06LivT04EvaluatorError,
        match="system_id disagrees with the branch job",
    ):
        Room06LivT04Evaluator().collect_receipt_material(runtime, _job())


@pytest.mark.parametrize(
    ("key", "value"),
    [("condition_id", "another-condition"), ("repeat_id", "r2")],
)
def test_episode_condition_and_repeat_must_match_branch_job(key, value):
    runtime = _runtime()
    runtime["episode"][key] = value

    with pytest.raises(
        Room06LivT04EvaluatorError,
        match=rf"episode {key} disagrees with the branch job",
    ):
        Room06LivT04Evaluator().collect_receipt_material(runtime, _job())


def test_direct_evaluator_validates_detaches_and_preserves_d4_observation():
    runtime = _runtime()
    observation = _d4_observation()
    runtime["base_facts"][D4_FIRST_EVENT_FACT_KEY] = observation
    material = Room06LivT04Evaluator().collect_receipt_material(runtime, _job())
    observation["response"]["tick"] = 199

    assert material["facts"][D4_FIRST_EVENT_FACT_KEY]["response"]["tick"] == 120
    assert material["facts"][D4_FIRST_EVENT_FACT_KEY]["work_confirmation"]["tick"] == 115
    assert material["facts"]["d4_items"] == []


@pytest.mark.parametrize("mutation", ["extra", "missing", "coverage"])
def test_direct_evaluator_rejects_malformed_d4_observation(mutation):
    runtime = _runtime()
    observation = _d4_observation()
    if mutation == "extra":
        observation["latency"] = 5
    elif mutation == "missing":
        del observation["work_confirmation"]
    else:
        observation["observed_through_tick"] = 119
    runtime["base_facts"][D4_FIRST_EVENT_FACT_KEY] = observation

    with pytest.raises(Room06LivT04EvaluatorError):
        Room06LivT04Evaluator().collect_receipt_material(runtime, _job())


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


def test_transition_event_must_match_frozen_handoff_observation_node():
    runtime = _runtime()
    runtime["streams"]["task_events"][0]["subject"] = "topology_transition_node"
    with pytest.raises(Room06LivT04EvaluatorError, match="frozen handoff observation node"):
        Room06LivT04Evaluator().collect_receipt_material(runtime, _job())

    runtime = _runtime()
    frozen = dict(DIAGNOSTIC_RUNTIME_BINDING)
    frozen["transition_handoff_node_id"] = "node:handoff"
    with pytest.raises(Room06LivT04EvaluatorError, match="disagrees with the frozen plan"):
        Room06LivT04Evaluator(runtime_binding=frozen).collect_receipt_material(
            runtime, _job()
        )


def test_case_bound_evaluator_requires_plan_runtime_binding():
    case = {
        "task_id": "liv-t-04",
        "anchor_id": "liv-t-04",
        "scene": {
            "scene_id": "room_06",
            "horizon": 15000,
            "entity_ids": ["book_batch_01", "heavy_object"],
            "zone_ids": [],
        },
        "evaluator": {
            "evaluator_id": "heterocoord.evaluator.room06-livt04",
            "evaluator_version": EVALUATOR_VERSION,
        },
    }
    with pytest.raises(Room06LivT04EvaluatorError, match="requires frozen"):
        Room06LivT04Evaluator(case_manifest=case, obligation_binding=BOUNDING)


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
