import hashlib
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from benchmark.evaluator.room06_livt04 import Room06LivT04Evaluator
from benchmark.room06_runtime_preflight_v1 import FORMAL_GATES, PREFLIGHT_SCHEMA
from benchmark.room06_task_runtime_v1 import (
    BOOK_BATCH_ID,
    HEAVY_OBJECT_ID,
    HORIZON_TICKS,
    Room06TaskRuntime,
    Room06TaskRuntimeError,
    normalize_room06_task_setup,
    task_definition_digest,
)
from tools.benchmark_branch.receipt_material import assemble_oracle_receipt_material


RUNTIME_BINDING_DIGEST = "a" * 64
SCENE_DIGEST = "b" * 64


def _canonical_digest(value):
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _definitions():
    return {
        BOOK_BATCH_ID: {
            "kind": "book_batch",
            "count": 3,
            "book_ids": ["book-c", "book-a", "book-b"],
            "source_semantic_point": "floor1-bookshelf",
            "destination_semantic_points": [
                "floor2-bookshelf-3",
                "floor2-bookshelf-1",
                "floor2-bookshelf-2",
            ],
        },
        HEAVY_OBJECT_ID: {
            "kind": "logical_load",
            "size_xyz_m": [0.8, 0.5, 0.4],
            "mass_kg": 24,
            "source_semantic_point": "floor1-coffee table",
            "destination_semantic_point": "floor1-reading desk",
            "minimum_holders": 2,
        },
    }


def _setup(definitions=None):
    definitions = definitions or _definitions()
    return {
        "schema": "heterocoord.room06-task-setup.v1",
        "task_id": "liv-t-04",
        "objects": [
            {"entity_id": entity_id, "definition": deepcopy(definitions[entity_id])}
            for entity_id in (BOOK_BATCH_ID, HEAVY_OBJECT_ID)
        ],
    }


def _bindings(definitions=None):
    definitions = definitions or _definitions()
    return {
        "schema": "heterocoord.task-object-bindings.v1",
        "scene_digest": SCENE_DIGEST,
        "objects": [
            {
                "entity_id": entity_id,
                "origin": "task_generated",
                "definition_ref": f"task-setup:liv-t-04/{entity_id}",
                "definition_digest": task_definition_digest(
                    entity_id, definitions[entity_id]
                ),
            }
            for entity_id in (BOOK_BATCH_ID, HEAVY_OBJECT_ID)
        ],
    }


def _preflight(
    node_id="node:transition",
    digest=RUNTIME_BINDING_DIGEST,
    definitions=None,
):
    definitions = definitions or _definitions()
    setup = _setup(definitions)
    return {
        "schema": PREFLIGHT_SCHEMA,
        "preflight_passed": True,
        "scene_smoke_may_start": True,
        "room_specific_feasibility": False,
        "runtime_binding_digest": digest,
        "transition_handoff_node_id": node_id,
        "scene": {"scene_asset_digest": SCENE_DIGEST},
        "task_setup": {
            "task_setup_digest": _canonical_digest(setup),
            "definition_digests": {
                entity_id: task_definition_digest(
                    entity_id, definitions[entity_id]
                )
                for entity_id in (BOOK_BATCH_ID, HEAVY_OBJECT_ID)
            },
        },
        "semantic_map": {
            "transition_handoff_node_id": node_id,
            "route_verified": True,
            "cross_floor_edges": [
                {
                    "edge_id": "edge:stairs",
                    "source": "node:floor1",
                    "target": node_id,
                    "source_level": "1F",
                    "target_level": "2F",
                }
            ],
        },
        "formal_gates": deepcopy(FORMAL_GATES),
    }


def _runtime(**overrides):
    values = {
        "task_setup": _setup(),
        "task_object_bindings": _bindings(),
        "preflight_report": _preflight(),
        "runtime_binding_digest": RUNTIME_BINDING_DIGEST,
    }
    values.update(overrides)
    return Room06TaskRuntime(**values)


def _record_relay(runtime):
    runtime.record_assignment(0)
    runtime.record_milestone("relay_started", 1)
    runtime.record_transition_handoff(
        tick=2,
        observed_node_id="node:transition",
        runtime_binding_digest=RUNTIME_BINDING_DIGEST,
    )
    runtime.record_milestone("relay_complete", 3)


def _complete(runtime, holders=("carrier-a", "carrier-b")):
    _record_relay(runtime)
    runtime.begin_joint_carry(tick=10, robot_ids=holders)
    runtime.complete_joint_carry(tick=20)
    runtime.record_milestone("accepted", 100)
    return runtime.evidence_material()


def test_definition_digest_is_stable_after_normalization():
    first = _definitions()
    second = deepcopy(first)
    second[BOOK_BATCH_ID]["book_ids"] = ["book-b", "book-c", "book-a"]
    second[BOOK_BATCH_ID]["destination_semantic_points"] = list(
        reversed(second[BOOK_BATCH_ID]["destination_semantic_points"])
    )
    second[HEAVY_OBJECT_ID]["mass_kg"] = 24.0

    assert task_definition_digest(BOOK_BATCH_ID, first[BOOK_BATCH_ID]) == (
        task_definition_digest(BOOK_BATCH_ID, second[BOOK_BATCH_ID])
    )
    assert task_definition_digest(HEAVY_OBJECT_ID, first[HEAVY_OBJECT_ID]) == (
        task_definition_digest(HEAVY_OBJECT_ID, second[HEAVY_OBJECT_ID])
    )


def test_normalized_task_setup_digest_is_stable_but_source_digest_is_exact():
    first = _definitions()
    second = deepcopy(first)
    second[BOOK_BATCH_ID]["book_ids"] = ["book-b", "book-c", "book-a"]
    second[BOOK_BATCH_ID]["destination_semantic_points"] = list(
        reversed(second[BOOK_BATCH_ID]["destination_semantic_points"])
    )
    second_setup = _setup(second)
    second_setup["objects"].reverse()

    normalized_first = normalize_room06_task_setup(
        _setup(first), _bindings(first)
    )
    normalized_second = normalize_room06_task_setup(
        second_setup, _bindings(second)
    )

    assert normalized_first["task_setup_digest"] == normalized_second[
        "task_setup_digest"
    ]
    assert normalized_first["definition_digests"] == normalized_second[
        "definition_digests"
    ]
    assert normalized_first["source_task_setup_digest"] != normalized_second[
        "source_task_setup_digest"
    ]


@pytest.mark.parametrize(
    "mutation, expected",
    [
        ("digest", "definition digest mismatch"),
        ("missing", "definition schema mismatch"),
        ("extra", "task_setup must contain exactly"),
        ("duplicate", "task_setup entity is duplicated"),
        ("wrong_id", "unsupported task-generated entity"),
        ("nonfinite", "finite canonical JSON"),
    ],
)
def test_task_setup_tampering_fails_closed(mutation, expected):
    definitions = _definitions()
    setup = _setup(definitions)
    bindings = _bindings(definitions)
    if mutation == "digest":
        bindings["objects"][0]["definition_digest"] = "f" * 64
    elif mutation == "missing":
        del setup["objects"][0]["definition"]["count"]
    elif mutation == "extra":
        setup["objects"].append(
            {"entity_id": "extra", "definition": {"kind": "extra"}}
        )
    elif mutation == "duplicate":
        setup["objects"][1]["entity_id"] = BOOK_BATCH_ID
    elif mutation == "wrong_id":
        setup["objects"][0]["entity_id"] = "book_batch_wrong"
    else:
        setup["objects"][1]["definition"]["mass_kg"] = float("nan")

    with pytest.raises(Room06TaskRuntimeError, match=expected):
        normalize_room06_task_setup(setup, bindings)


def test_single_holder_is_hard_rejected_without_success_interval():
    runtime = _runtime()
    _record_relay(runtime)

    with pytest.raises(Room06TaskRuntimeError, match="at least 2 distinct holders"):
        runtime.begin_joint_carry(tick=10, robot_ids=["carrier-a"])

    evidence = runtime.evidence_material()
    assert evidence["runtime_state"] == "failed"
    assert evidence["streams"]["hold_intervals"] == []
    assert evidence["safety_failures"][0]["code"] == (
        "joint_hold_below_minimum_at_lift"
    )


def test_duplicate_holder_id_is_hard_rejected():
    runtime = _runtime()
    _record_relay(runtime)

    with pytest.raises(Room06TaskRuntimeError, match="distinct robot IDs"):
        runtime.begin_joint_carry(
            tick=10, robot_ids=["carrier-a", "carrier-a"]
        )

    evidence = runtime.evidence_material()
    assert evidence["streams"]["hold_intervals"] == []
    assert evidence["safety_failures"][0]["code"] == (
        "joint_hold_duplicate_or_invalid_holder"
    )


def test_malformed_unhashable_holder_is_audited_without_secondary_error():
    runtime = _runtime()
    _record_relay(runtime)

    with pytest.raises(Room06TaskRuntimeError, match="robot_id"):
        runtime.begin_joint_carry(tick=10, robot_ids=[["not-a-robot-id"]])

    evidence = runtime.evidence_material()
    assert evidence["runtime_state"] == "failed"
    assert evidence["streams"]["hold_intervals"] == []
    assert evidence["safety_failures"][0]["holder_ids"] == []


@pytest.mark.parametrize(
    "events,tick,expected",
    [
        ([], 10, "complete relay evidence chain"),
        (
            [
                ("milestone", "relay_started", 1),
                ("assignment", "assigned", 2),
                ("transition", "node:transition", 3),
                ("milestone", "relay_complete", 4),
            ],
            10,
            "strictly after the ordered relay chain",
        ),
        (
            [
                ("assignment", "assigned", 0),
                ("transition", "node:transition", 1),
                ("milestone", "relay_started", 2),
                ("milestone", "relay_complete", 3),
            ],
            10,
            "strictly after the ordered relay chain",
        ),
        (
            [
                ("assignment", "assigned", 0),
                ("milestone", "relay_started", 1),
                ("transition", "node:transition", 1),
                ("milestone", "relay_complete", 2),
            ],
            10,
            "strictly after the ordered relay chain",
        ),
        (
            [
                ("assignment", "assigned", 0),
                ("milestone", "relay_started", 1),
                ("transition", "node:transition", 2),
                ("milestone", "relay_complete", 3),
            ],
            3,
            "strictly after the ordered relay chain",
        ),
    ],
)
def test_joint_carry_rejects_missing_misordered_or_same_tick_relay_chain(
    events, tick, expected
):
    runtime = _runtime()
    for kind, value, event_tick in events:
        if kind == "assignment":
            runtime.record_assignment(event_tick)
        elif kind == "milestone":
            runtime.record_milestone(value, event_tick)
        else:
            runtime.record_transition_handoff(
                tick=event_tick,
                observed_node_id=value,
                runtime_binding_digest=RUNTIME_BINDING_DIGEST,
            )

    with pytest.raises(Room06TaskRuntimeError, match=expected):
        runtime.begin_joint_carry(
            tick=tick, robot_ids=["carrier-a", "carrier-b"]
        )

    assert runtime.state == "pending"
    runtime.finalize_at_horizon(tick=HORIZON_TICKS)
    evidence = runtime.evidence_material()
    assert evidence["streams"]["hold_intervals"] == []
    assert all(
        row["name"] != "bilateral_support_established"
        for row in evidence["streams"]["task_events"]
    )


def test_joint_carry_starts_strictly_after_completed_relay():
    runtime = _runtime()
    _record_relay(runtime)

    runtime.begin_joint_carry(
        tick=4, robot_ids=["carrier-a", "carrier-b"]
    )
    runtime.complete_joint_carry(tick=5)
    runtime.record_milestone("accepted", 6)

    evidence = runtime.evidence_material()
    assert evidence["streams"]["hold_intervals"] == [
        {
            "object_id": HEAVY_OBJECT_ID,
            "robot_id": "carrier-a",
            "start_tick": 4,
            "end_tick": 5,
        },
        {
            "object_id": HEAVY_OBJECT_ID,
            "robot_id": "carrier-b",
            "start_tick": 4,
            "end_tick": 5,
        },
    ]


def test_missing_relay_rejection_does_not_advance_or_poison_ledger():
    runtime = _runtime()

    with pytest.raises(Room06TaskRuntimeError, match="complete relay evidence chain"):
        runtime.begin_joint_carry(
            tick=10, robot_ids=["carrier-a", "carrier-b"]
        )

    _record_relay(runtime)
    runtime.begin_joint_carry(
        tick=4, robot_ids=["carrier-a", "carrier-b"]
    )
    runtime.complete_joint_carry(tick=5)
    runtime.record_milestone("accepted", 6)
    assert runtime.evidence_material()["runtime_state"] == "complete"


def test_two_distinct_holders_produce_half_open_intervals():
    evidence = _complete(_runtime())

    assert evidence["streams"]["hold_intervals"] == [
        {
            "object_id": HEAVY_OBJECT_ID,
            "robot_id": "carrier-a",
            "start_tick": 10,
            "end_tick": 20,
        },
        {
            "object_id": HEAVY_OBJECT_ID,
            "robot_id": "carrier-b",
            "start_tick": 10,
            "end_tick": 20,
        },
    ]
    assert evidence["safety_failures"] == []


def test_substitution_adds_before_release_without_coverage_gap():
    runtime = _runtime()
    _record_relay(runtime)
    runtime.begin_joint_carry(
        tick=10, robot_ids=["carrier-a", "carrier-b"]
    )
    runtime.substitute_holder(
        tick=15,
        outgoing_robot_id="carrier-a",
        incoming_robot_id="carrier-c",
    )
    runtime.complete_joint_carry(tick=20)
    runtime.record_milestone("accepted", 100)

    evidence = runtime.evidence_material()

    assert evidence["streams"]["hold_intervals"] == [
        {
            "object_id": HEAVY_OBJECT_ID,
            "robot_id": "carrier-a",
            "start_tick": 10,
            "end_tick": 15,
        },
        {
            "object_id": HEAVY_OBJECT_ID,
            "robot_id": "carrier-b",
            "start_tick": 10,
            "end_tick": 20,
        },
        {
            "object_id": HEAVY_OBJECT_ID,
            "robot_id": "carrier-c",
            "start_tick": 15,
            "end_tick": 20,
        },
    ]
    assert evidence["safety_failures"] == []


def test_release_below_two_closes_all_intervals_and_records_safety_failure():
    runtime = _runtime()
    _record_relay(runtime)
    runtime.begin_joint_carry(
        tick=10, robot_ids=["carrier-a", "carrier-b"]
    )

    runtime.release_holder(tick=15, robot_id="carrier-a")

    evidence = runtime.evidence_material()
    assert evidence["runtime_state"] == "failed"
    assert evidence["streams"]["hold_intervals"] == [
        {
            "object_id": HEAVY_OBJECT_ID,
            "robot_id": "carrier-a",
            "start_tick": 10,
            "end_tick": 15,
        },
        {
            "object_id": HEAVY_OBJECT_ID,
            "robot_id": "carrier-b",
            "start_tick": 10,
            "end_tick": 15,
        },
    ]
    assert evidence["safety_failures"][0]["code"] == (
        "joint_hold_below_minimum_during_carry"
    )
    with pytest.raises(Room06TaskRuntimeError, match="state is failed"):
        runtime.add_holder(tick=16, robot_id="carrier-c")


def test_same_tick_release_fails_safely_without_zero_length_intervals():
    runtime = _runtime()
    _record_relay(runtime)
    runtime.begin_joint_carry(
        tick=10, robot_ids=["carrier-a", "carrier-b"]
    )

    runtime.release_holder(tick=10, robot_id="carrier-a")

    evidence = runtime.evidence_material()
    assert evidence["runtime_state"] == "failed"
    assert evidence["streams"]["hold_intervals"] == []
    assert evidence["safety_failures"][0]["code"] == (
        "joint_hold_below_minimum_during_carry"
    )


@pytest.mark.parametrize(
    "preflight,digest,expected",
    [
        (_preflight(node_id="topology_transition_node"), RUNTIME_BINDING_DIGEST,
         "literal placeholder"),
        (_preflight(), "c" * 64, "runtime_binding_digest mismatch"),
        ({**_preflight(), "transition_handoff_node_id": "node:fake"},
         RUNTIME_BINDING_DIGEST, "internally inconsistent"),
    ],
)
def test_missing_fake_or_mismatched_transition_binding_is_rejected(
    preflight, digest, expected
):
    with pytest.raises(Room06TaskRuntimeError, match=expected):
        _runtime(preflight_report=preflight, runtime_binding_digest=digest)


@pytest.mark.parametrize(
    "mutation,expected",
    [
        ("scene", "scene digest disagrees"),
        ("setup", "task_setup_digest mismatch"),
        ("definitions", "task definition digests mismatch"),
    ],
)
def test_preflight_must_bind_exact_task_setup(mutation, expected):
    preflight = _preflight()
    if mutation == "scene":
        preflight["scene"]["scene_asset_digest"] = "c" * 64
    elif mutation == "setup":
        preflight["task_setup"]["task_setup_digest"] = "c" * 64
    else:
        preflight["task_setup"]["definition_digests"][BOOK_BATCH_ID] = "c" * 64

    with pytest.raises(Room06TaskRuntimeError, match=expected):
        _runtime(preflight_report=preflight)


def test_transition_observation_rejects_placeholder_fake_node_and_digest():
    runtime = _runtime()
    runtime.record_assignment(0)
    runtime.record_milestone("relay_started", 1)

    for node, digest, expected in (
        ("topology_transition_node", RUNTIME_BINDING_DIGEST, "literal placeholder"),
        ("node:fake", RUNTIME_BINDING_DIGEST, "does not match"),
        ("node:transition", "c" * 64, "runtime_binding_digest mismatch"),
    ):
        with pytest.raises(Room06TaskRuntimeError, match=expected):
            runtime.record_transition_handoff(
                tick=2,
                observed_node_id=node,
                runtime_binding_digest=digest,
            )


def test_duplicate_and_backward_evidence_are_rejected():
    runtime = _runtime()
    runtime.record_assignment(1)
    with pytest.raises(Room06TaskRuntimeError, match="duplicate milestone"):
        runtime.record_assignment(2)
    with pytest.raises(Room06TaskRuntimeError, match="precedes ledger tick"):
        runtime.record_milestone("relay_started", 0)


def test_acceptance_requires_complete_ordered_chain_and_horizon():
    runtime = _runtime()
    with pytest.raises(Room06TaskRuntimeError, match="before joint carry"):
        runtime.record_milestone("accepted", 100)

    runtime = _runtime()
    _record_relay(runtime)
    runtime.begin_joint_carry(
        tick=10, robot_ids=["carrier-a", "carrier-b"]
    )
    runtime.complete_joint_carry(tick=20)
    with pytest.raises(Room06TaskRuntimeError, match="after the liv-t-04 horizon"):
        runtime.record_milestone("accepted", HORIZON_TICKS + 1)


def test_horizon_miss_exports_complete_partial_ledger_without_acceptance():
    runtime = _runtime()
    runtime.record_assignment(0)
    runtime.record_milestone("relay_started", 1)
    runtime.finalize_at_horizon(tick=HORIZON_TICKS)

    evidence = runtime.evidence_material()

    assert evidence["runtime_state"] == "incomplete"
    assert evidence["streams"]["integrity_confirmed"] is True
    assert {row["label"] for row in evidence["streams"]["milestones"]} == {
        "assigned",
        "relay_started",
    }
    assert evidence["streams"]["task_events"][-1] == {
        "name": "task_horizon_reached",
        "subject": "liv-t-04",
        "tick": HORIZON_TICKS,
    }
    assert all(
        row["label"] != "accepted"
        for row in evidence["streams"]["milestones"]
    )
    material = assemble_oracle_receipt_material(
        episode={
            "status": "valid_terminal",
            "condition_id": "C0",
            "branch": "fact",
            "repeat_id": "r1",
            "checkpoint_id": "cp0",
        },
        streams=evidence["streams"],
        base_facts={
            "d1": {
                "complete_plan": True,
                "validity": 1.0,
                "organization": 1.0,
                "infeasible": 0.0,
            },
            "base_task_success": False,
            "d4_items": [],
            "d6": {"bits": 10.0, "time": float(HORIZON_TICKS)},
        },
        anchor_id="liv-t-04",
        obligation_binding={
            "schema": "heterocoord.oracle-obligation-binding.v1",
            "anchor_id": "liv-t-04",
            "obligations": [
                {
                    "obligation_id": "relay",
                    "relation": "P2",
                    "predicate_ids": [
                        "upper_shelf_delivery_after_transition_handoff"
                    ],
                }
            ],
        },
    )
    assert material["facts"]["success"] is False
    assert material["facts"]["d6"]["success"] is False


def test_streams_are_consumed_by_existing_oracle_receipt_schema():
    runtime = _runtime()
    _record_relay(runtime)
    runtime.begin_joint_carry(
        tick=10, robot_ids=["carrier-a", "carrier-b"]
    )
    runtime.substitute_holder(
        tick=15,
        outgoing_robot_id="carrier-a",
        incoming_robot_id="carrier-c",
    )
    runtime.complete_joint_carry(tick=20)
    runtime.record_milestone("accepted", 100)
    evidence = runtime.evidence_material()

    episode = {
        "status": "valid_terminal",
        "condition_id": "C0",
        "branch": "fact",
        "repeat_id": "r1",
        "checkpoint_id": "cp0",
    }
    base_facts = {
        "d1": {
            "complete_plan": True,
            "validity": 1.0,
            "organization": 1.0,
            "infeasible": 0.0,
        },
        "base_task_success": True,
        "d4_items": [],
        "d6": {"bits": 10.0, "time": 1.0},
    }
    obligation_binding = {
        "schema": "heterocoord.oracle-obligation-binding.v1",
        "anchor_id": "liv-t-04",
        "obligations": [
            {
                "obligation_id": "relay",
                "relation": "P2",
                "predicate_ids": [
                    "upper_shelf_delivery_after_transition_handoff"
                ],
            }
        ],
    }
    material = assemble_oracle_receipt_material(
        episode=episode,
        streams=evidence["streams"],
        base_facts=base_facts,
        anchor_id="liv-t-04",
        obligation_binding=obligation_binding,
    )
    evaluator_material = Room06LivT04Evaluator(
        runtime_binding=evidence["runtime_binding"]
    ).collect_receipt_material(
        {
            "episode": episode,
            "streams": evidence["streams"],
            "base_facts": base_facts,
            "obligation_binding": obligation_binding,
            "runtime_binding": evidence["runtime_binding"],
        },
        SimpleNamespace(task_id="liv-t-04", scene_key="room_06"),
    )

    assert material["facts"]["success"] is True
    assert evaluator_material == {
        **material,
        "episode": {
            **material["episode"],
            "evaluator_version": Room06LivT04Evaluator.evaluator_version,
        },
    }
    assert material["facts"]["obligations"] == [
        {"relation": "P2", "outcome": "completed"}
    ]
    assert evidence["formal_gates"] == FORMAL_GATES
    assert evidence["room_specific_feasibility"] is False
    assert evidence["streams"]["milestones"][0] == {
        "label": "assigned",
        "tick": 0,
    }
    assert all(
        row["name"] != "assigned"
        for row in evidence["streams"]["task_events"]
    )
    assert evidence["runtime_binding"] == {
        "schema": "heterocoord.room06-runtime-binding.v1",
        "anchor_id": "liv-t-04",
        "scene_id": "room_06",
        "transition_handoff_node_id": "node:transition",
        "runtime_binding_digest": RUNTIME_BINDING_DIGEST,
    }
    assert "score" not in evidence
    assert "self_reported_score" not in evidence
