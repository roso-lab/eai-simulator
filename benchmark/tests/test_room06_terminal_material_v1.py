import hashlib
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from benchmark.d4_first_event_v1 import FACT_KEY as D4_FIRST_EVENT_FACT_KEY
from benchmark.evaluator.room06_livt04 import (
    EVALUATOR_VERSION,
    Room06LivT04Evaluator,
)
from benchmark.room06_runtime_preflight_v1 import FORMAL_GATES, PREFLIGHT_SCHEMA
from benchmark.room06_task_runtime_v1 import (
    BOOK_BATCH_ID,
    HEAVY_OBJECT_ID,
    HORIZON_TICKS,
    Room06TaskRuntime,
    task_definition_digest,
)
from benchmark.room06_terminal_material_v1 import (
    ASSEMBLER_SCHEMA,
    D6_BITS_UNIT,
    D6_TIME_UNIT,
    Room06TerminalMaterialError,
    assemble_room06_terminal_payload,
)


RUNTIME_BINDING_DIGEST = "a" * 64
SCENE_DIGEST = "b" * 64
OBLIGATION_BINDING = {
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


def _setup():
    definitions = _definitions()
    return {
        "schema": "heterocoord.room06-task-setup.v1",
        "task_id": "liv-t-04",
        "objects": [
            {"entity_id": entity_id, "definition": deepcopy(definitions[entity_id])}
            for entity_id in (BOOK_BATCH_ID, HEAVY_OBJECT_ID)
        ],
    }


def _bindings():
    definitions = _definitions()
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


def _preflight():
    setup = _setup()
    definitions = _definitions()
    return {
        "schema": PREFLIGHT_SCHEMA,
        "preflight_passed": True,
        "scene_smoke_may_start": True,
        "room_specific_feasibility": False,
        "runtime_binding_digest": RUNTIME_BINDING_DIGEST,
        "transition_handoff_node_id": "node:transition",
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
            "transition_handoff_node_id": "node:transition",
            "route_verified": True,
            "cross_floor_edges": [
                {
                    "edge_id": "edge:stairs",
                    "source": "node:floor1",
                    "target": "node:transition",
                    "source_level": "1F",
                    "target_level": "2F",
                }
            ],
        },
        "formal_gates": deepcopy(FORMAL_GATES),
    }


def _runtime():
    return Room06TaskRuntime(
        task_setup=_setup(),
        task_object_bindings=_bindings(),
        preflight_report=_preflight(),
        runtime_binding_digest=RUNTIME_BINDING_DIGEST,
    )


def _record_relay(runtime):
    runtime.record_assignment(0)
    runtime.record_milestone("relay_started", 1)
    runtime.record_transition_handoff(
        tick=2,
        observed_node_id="node:transition",
        runtime_binding_digest=RUNTIME_BINDING_DIGEST,
    )
    runtime.record_milestone("relay_complete", 3)


def _complete_evidence():
    runtime = _runtime()
    _record_relay(runtime)
    runtime.begin_joint_carry(
        tick=10, robot_ids=["carrier-a", "carrier-b"]
    )
    runtime.complete_joint_carry(tick=20)
    runtime.record_milestone("accepted", 100)
    return runtime.evidence_material()


def _failed_evidence():
    runtime = _runtime()
    _record_relay(runtime)
    with pytest.raises(ValueError, match="at least 2 distinct holders"):
        runtime.begin_joint_carry(tick=10, robot_ids=["carrier-a"])
    return runtime.evidence_material()


def _incomplete_evidence():
    runtime = _runtime()
    runtime.record_assignment(0)
    runtime.record_milestone("relay_started", 1)
    runtime.finalize_at_horizon(tick=HORIZON_TICKS)
    return runtime.evidence_material()


def _episode():
    return {
        "status": "valid_terminal",
        "condition_id": "C0",
        "branch": "fact",
        "repeat_id": "r1",
        "checkpoint_id": "cp-room06",
        "evaluator_version": EVALUATOR_VERSION,
        "system_id": "emos-zero",
        "base_instance_id": "room06-base",
    }


def _d1():
    return {
        "validity": 1.0,
        "organization": 1.0,
        "infeasible": 0.0,
        "complete_plan": True,
    }


def _d4():
    return {
        "schema": "heterocoord.d4-first-event-observation.v1",
        "registration_id": "d4-reg-room06",
        "panel_id": "d4-panel-room06",
        "cell_id": "d4-cell-room06",
        "event_id": "registered-function-loss",
        "event_eligible": True,
        "eligibility_evidence_digest": "1" * 64,
        "response": {"tick": 5, "evidence_digest": "2" * 64},
        "work_confirmation": {"tick": 7, "evidence_digest": "3" * 64},
        "terminal": None,
        "safety": {"flag": False, "evidence_digest": None},
        "observed_through_tick": 100,
        "timeline_digest": "4" * 64,
    }


def _d6():
    return {
        "transmitted_bits": 99_216,
        "sim_time_s": 7.74,
        "bits_unit": D6_BITS_UNIT,
        "time_unit": D6_TIME_UNIT,
    }


def _assemble(evidence=None, **overrides):
    values = {
        "runtime_evidence": evidence or _complete_evidence(),
        "episode": _episode(),
        "d1": _d1(),
        "d4_first_event_observation": _d4(),
        "d6_measurement": _d6(),
        "obligation_binding": deepcopy(OBLIGATION_BINDING),
    }
    values.update(overrides)
    return assemble_room06_terminal_payload(**values)


def _job():
    return SimpleNamespace(
        task_id="liv-t-04",
        scene_key="room_06",
        system_id="emos-zero",
        condition_id="C0",
        repeat_id="r1",
    )


@pytest.mark.parametrize(
    "evidence, expected_base, expected_final",
    [
        (_complete_evidence, True, True),
        (_failed_evidence, False, False),
        (_incomplete_evidence, False, False),
    ],
)
def test_terminal_states_derive_base_success_and_evaluator_final_success(
    evidence, expected_base, expected_final
):
    payload = _assemble(evidence())

    assert payload["base_facts"]["base_task_success"] is expected_base
    assert payload["assembler_schema"] == ASSEMBLER_SCHEMA
    assert payload["formal_gates"] == FORMAL_GATES
    assert payload["base_facts"][D4_FIRST_EVENT_FACT_KEY] == _d4()
    assert D4_FIRST_EVENT_FACT_KEY not in {
        key for key in payload if key != "base_facts"
    }
    assert payload["base_facts"]["d4_items"] == []
    material = Room06LivT04Evaluator(
        obligation_binding=OBLIGATION_BINDING,
        runtime_binding=payload["runtime_binding"],
    ).collect_receipt_material(payload, _job())
    assert material["facts"]["success"] is expected_final
    assert material["facts"]["d6"] == {
        "success": expected_final,
        "bits": 99_216,
        "time": 7.74,
    }
    assert material["facts"][D4_FIRST_EVENT_FACT_KEY] == _d4()
    assert material["facts"]["d4_items"] == []


def test_output_is_json_detached_and_digest_bound_to_normalized_evidence():
    evidence = _complete_evidence()
    episode = _episode()
    d1 = _d1()
    d4 = _d4()
    d6 = _d6()
    binding = deepcopy(OBLIGATION_BINDING)
    payload = _assemble(
        evidence,
        episode=episode,
        d1=d1,
        d4_first_event_observation=d4,
        d6_measurement=d6,
        obligation_binding=binding,
    )
    expected_streams = deepcopy(payload["streams"])
    expected_d4 = deepcopy(payload["base_facts"][D4_FIRST_EVENT_FACT_KEY])
    expected_binding = deepcopy(payload["obligation_binding"])
    evidence["streams"]["milestones"].clear()
    episode["condition_id"] = "mutated"
    d1["validity"] = 0
    d4["response"] = None
    d6["transmitted_bits"] = 0
    binding["obligations"].clear()

    assert payload["streams"] == expected_streams
    assert payload["base_facts"][D4_FIRST_EVENT_FACT_KEY] == expected_d4
    assert payload["obligation_binding"] == expected_binding
    assert payload["episode"]["condition_id"] == "C0"
    assert payload["base_facts"]["d1"]["validity"] == 1.0
    assert payload["base_facts"]["d6"]["bits"] == 99_216
    assert len(payload["runtime_evidence_digest"]) == 64


@pytest.mark.parametrize(
    "field",
    ["base_task_success", "success", "obligations", "streams", "runtime_binding"],
)
def test_caller_cannot_inject_derived_or_runtime_owned_top_level_inputs(field):
    inputs = {
        "runtime_evidence": _complete_evidence(),
        "episode": _episode(),
        "d1": _d1(),
        "d4_first_event_observation": _d4(),
        "d6_measurement": _d6(),
        "obligation_binding": deepcopy(OBLIGATION_BINDING),
        field: {},
    }
    with pytest.raises(TypeError, match="unexpected keyword argument"):
        assemble_room06_terminal_payload(**inputs)


@pytest.mark.parametrize(
    "field",
    ["success", "obligations", "base_task_success", "completed", "physical_success"],
)
def test_episode_rejects_success_and_process_status_injection(field):
    episode = _episode()
    episode[field] = True
    with pytest.raises(Room06TerminalMaterialError, match="episode schema mismatch"):
        _assemble(episode=episode)


@pytest.mark.parametrize(
    "field",
    [
        "submitted_bits",
        "offered_bits",
        "host_wall_time_s",
        "model_cost",
        "api_cost",
    ],
)
def test_d6_rejects_untransmitted_or_host_and_model_cost_fields(field):
    d6 = _d6()
    d6[field] = 1
    with pytest.raises(Room06TerminalMaterialError, match="d6_measurement schema mismatch"):
        _assemble(d6_measurement=d6)


@pytest.mark.parametrize(
    "mutation, expected",
    [
        (lambda row: row.update({"transmitted_bits": -1}), "non-negative integer"),
        (lambda row: row.update({"transmitted_bits": True}), "non-negative integer"),
        (lambda row: row.update({"sim_time_s": -0.1}), "valid range"),
        (lambda row: row.update({"sim_time_s": float("nan")}), "valid range"),
        (lambda row: row.update({"sim_time_s": True}), "finite number"),
        (lambda row: row.update({"bits_unit": "submitted_bit"}), "bits_unit"),
        (lambda row: row.update({"time_unit": "host_wall_second"}), "time_unit"),
    ],
)
def test_d6_numeric_and_unit_boundaries_fail_closed(mutation, expected):
    d6 = _d6()
    mutation(d6)
    with pytest.raises(Room06TerminalMaterialError, match=expected):
        _assemble(d6_measurement=d6)


@pytest.mark.parametrize("field", ["validity", "organization", "infeasible"])
@pytest.mark.parametrize("value", [True, -0.1, 1.1, float("nan"), float("inf")])
def test_d1_rejects_bool_nonfinite_and_out_of_range_values(field, value):
    d1 = _d1()
    d1[field] = value
    with pytest.raises(Room06TerminalMaterialError):
        _assemble(d1=d1)


def test_d1_rejects_missing_and_extra_fields_and_nonboolean_complete_plan():
    for d1 in (
        {key: value for key, value in _d1().items() if key != "validity"},
        {**_d1(), "score": 1.0},
        {**_d1(), "complete_plan": 1},
    ):
        with pytest.raises(Room06TerminalMaterialError):
            _assemble(d1=d1)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda row: row.update({"anchor_id": "liv-t-03"}),
        lambda row: row.update({"extra": True}),
        lambda row: row.update({"obligations": []}),
        lambda row: row["obligations"][0].update({"relation": "P3"}),
        lambda row: row["obligations"][0].update({"subtype": "J"}),
        lambda row: row["obligations"][0].update({"predicate_ids": ["deadline"]}),
        lambda row: row["obligations"].append(deepcopy(row["obligations"][0])),
    ],
)
def test_wrong_extra_or_missing_frozen_obligation_binding_is_rejected(mutation):
    binding = deepcopy(OBLIGATION_BINDING)
    mutation(binding)
    with pytest.raises(Room06TerminalMaterialError):
        _assemble(obligation_binding=binding)


@pytest.mark.parametrize(
    "mutation, expected",
    [
        (lambda row: row.update({"schema": "wrong"}), "schema"),
        (lambda row: row.update({"task_id": "wrong"}), "liv-t-04"),
        (lambda row: row.update({"room_specific_feasibility": True}), "feasibility"),
        (
            lambda row: row["formal_gates"].update({"runtime_qualified": True}),
            "formal gates",
        ),
        (
            lambda row: row.update({"runtime_binding_digest": "c" * 64}),
            "digest mismatch",
        ),
        (
            lambda row: row.update({"transition_handoff_node_id": "node:other"}),
            "transition node mismatch",
        ),
        (
            lambda row: row["runtime_binding"].update({"scene_id": "room_05"}),
            "identity mismatch",
        ),
        (
            lambda row: row["streams"]["identity_records"].update(
                {"runtime_binding_digest": "c" * 64}
            ),
            "identity runtime binding digest mismatch",
        ),
        (
            lambda row: row["streams"].update({"integrity_confirmed": False}),
            "integrity",
        ),
        (
            lambda row: row["streams"].update(
                {
                    "zone_intervals": [
                        {
                            "zone_id": "invented",
                            "holder_id": "robot",
                            "enter_tick": 1,
                            "exit_tick": 2,
                        }
                    ]
                }
            ),
            "zone_intervals",
        ),
        (
            lambda row: row["task_setup"].update({"task_setup_digest": "c" * 64}),
            "task_setup digest mismatch",
        ),
        (
            lambda row: row["streams"]["hold_intervals"][0].update(
                {"object_id": "other"}
            ),
            "object identity mismatch",
        ),
    ],
)
def test_runtime_evidence_identity_stream_and_gate_tampering_fails_closed(
    mutation, expected
):
    evidence = _complete_evidence()
    mutation(evidence)
    with pytest.raises(Room06TerminalMaterialError, match=expected):
        _assemble(evidence)


def test_complete_state_string_without_accepted_evidence_is_rejected():
    evidence = _complete_evidence()
    evidence["streams"]["milestones"] = [
        row for row in evidence["streams"]["milestones"] if row["label"] != "accepted"
    ]
    with pytest.raises(Room06TerminalMaterialError, match="milestone chain"):
        _assemble(evidence)


@pytest.mark.parametrize("mutation", ["missing_failure", "missing_event", "wrong_tick"])
def test_failed_state_requires_matching_safety_failure_and_event(mutation):
    evidence = _failed_evidence()
    if mutation == "missing_failure":
        evidence["safety_failures"] = []
    elif mutation == "missing_event":
        evidence["streams"]["task_events"] = [
            row
            for row in evidence["streams"]["task_events"]
            if row["name"] != "joint_hold_safety_failure"
        ]
    else:
        event = next(
            row
            for row in evidence["streams"]["task_events"]
            if row["name"] == "joint_hold_safety_failure"
        )
        event["tick"] += 1
        evidence["streams"]["task_events"].sort(
            key=lambda row: (row["tick"], row["name"], row["subject"])
        )
    with pytest.raises(Room06TerminalMaterialError):
        _assemble(evidence)


@pytest.mark.parametrize("mutation", ["missing", "wrong_tick", "wrong_subject"])
def test_incomplete_state_requires_exact_horizon_event(mutation):
    evidence = _incomplete_evidence()
    event = next(
        row
        for row in evidence["streams"]["task_events"]
        if row["name"] == "task_horizon_reached"
    )
    if mutation == "missing":
        evidence["streams"]["task_events"].remove(event)
    elif mutation == "wrong_tick":
        event["tick"] -= 1
    else:
        event["subject"] = "other-task"
    with pytest.raises(Room06TerminalMaterialError):
        _assemble(evidence)


@pytest.mark.parametrize(
    "value",
    [
        None,
        {},
        {**_d4(), "response_tick": 5},
        {key: value for key, value in _d4().items() if key != "work_confirmation"},
        {**_d4(), "response": {"tick": True, "evidence_digest": "2" * 64}},
        {
            **_d4(),
            "response": {"tick": 101, "evidence_digest": "2" * 64},
        },
        {**_d4(), "timeline_digest": "not-a-digest"},
    ],
)
def test_d4_observation_is_required_and_rejects_malformed_or_mixed_fields(value):
    with pytest.raises(Room06TerminalMaterialError):
        _assemble(d4_first_event_observation=value)


def test_d4_response_and_work_confirmation_may_be_explicitly_unobserved():
    d4 = _d4()
    d4["response"] = None
    d4["work_confirmation"] = None
    payload = _assemble(d4_first_event_observation=d4)
    observation = payload["base_facts"][D4_FIRST_EVENT_FACT_KEY]
    assert observation["response"] is None
    assert observation["work_confirmation"] is None


def test_evaluator_consumes_payload_and_preserves_d4_diagnostic_as_separate_fact():
    payload = _assemble()
    material = Room06LivT04Evaluator().collect_receipt_material(payload, _job())
    assert material["facts"]["success"] is True
    assert material["facts"]["d4_items"] == []
    assert material["facts"][D4_FIRST_EVENT_FACT_KEY] == _d4()
    assert D4_FIRST_EVENT_FACT_KEY not in material["facts"]["d4_items"]
