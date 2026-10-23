import pytest
import math

from benchmark.dynamic_v3 import (
    DynamicDispositionV3,
    DynamicEventDispositionV3,
    PhysicalFunctionLossAdapterV3,
    WorkItemV3,
    dynamic_item_outcomes_v3,
    evaluate_dynamic_value_outcomes_v3,
    freeze_affected_work_closure,
    score_d4_outcomes_v3,
    make_dynamic_event_disposition_v3,
)
from benchmark.records_v3 import Branch, IdentityV3, RecordStatus
from benchmark.dynamic_v3 import PhysicalFunctionLossResultV3
from benchmark.dynamic_value_v3 import (
    DynamicValueRegistrationV3,
    DynamicValueTimelineV3,
    OutcomeStateV3,
    OutcomeValueV3,
)


def identity():
    return IdentityV3(
        protocol_version="v3", scorebook_digest="score", catalog_digest="catalog",
        design_digest="design", task_id="task", anchor_id="anchor", domain="d",
        relation_signature=("r",), difficulty="easy", condition_id="nominal",
        system_id="sys", base_instance_id="base", repeat_id="0", branch=Branch.FACT,
        checkpoint_id="cp",
    )


def test_closure_freezes_precedence_service_and_shared_resource_edges():
    single = WorkItemV3.from_value({"item_id": "single", "shared_robot_ids": "robot-1"})
    assert single.shared_robot_ids == ("robot-1",)
    assert single.all_shared_resources == ("robot-1",)
    closure = freeze_affected_work_closure(
        (
            {"item_id": "root", "precedence": ("pre",), "shared_resources": ("tool-1",)},
            {"item_id": "pre", "kind": "deadline"},
            {"item_id": "service", "service_dependencies": ("root",)},
            {"item_id": "shared", "shared_resources": ("tool-1",)},
            {"item_id": "unrelated", "shared_resources": ("tool-2",)},
        ),
        ("root",), checkpoint_tick=7,
    )
    assert closure.item_ids == ("pre", "root", "service", "shared")
    assert "shared_resource" in closure.edge_families
    assert "unrelated" not in closure.item_ids
    assert closure.closure_digest
    robot_closure = freeze_affected_work_closure((
        {"item_id": "a", "shared_robot_ids": "r1"},
        {"item_id": "b", "shared_robot_ids": "r1"},
    ), ("a",), checkpoint_tick=0)
    assert robot_closure.item_ids == ("a", "b")
    assert robot_closure.edge_families["shared_robot"] == (("a", "b"),)
    collision = freeze_affected_work_closure((
        {"item_id": "robot", "shared_robot_ids": "same"},
        {"item_id": "tool", "shared_tools": "same"},
    ), ("robot",), checkpoint_tick=0)
    assert collision.item_ids == ("robot",)
    with pytest.raises(ValueError, match="does not match"):
        type(closure)(closure.checkpoint_tick, closure.root_ids, closure.items, "bad", closure.edge_families)
    with pytest.raises(ValueError, match="unknown work"):
        freeze_affected_work_closure(({"item_id": "a", "precedence": ("missing",)},), ("a",), checkpoint_tick=0)
    with pytest.raises(ValueError, match="duplicate"):
        freeze_affected_work_closure(({"item_id": "a"}, {"item_id": "a"}), ("a",), checkpoint_tick=0)
    with pytest.raises(ValueError):
        WorkItemV3("bad", work_weight=math.inf)


def test_legacy_closure_digest_can_be_rehydrated_without_new_resource_fields():
    items = (WorkItemV3("a"),)
    import hashlib, json
    payload = {"checkpoint_tick": 0, "root_ids": ["a"], "items": [items[0].to_legacy_dict()], "edge_families": {"precedence": [], "service": [], "shared_resource": []}}
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    closure = type(freeze_affected_work_closure(items, ("a",), checkpoint_tick=0))(0, ("a",), items, digest, {"precedence": (), "service": (), "shared_resource": ()})
    assert closure.canonical_version == "v1_legacy"
    assert closure.to_canonical_dict(include_digest=False) == payload


def test_closure_metadata_is_detached_and_deep_frozen():
    metadata = {"nested": {"x": 1}}
    closure = freeze_affected_work_closure((WorkItemV3("a", metadata=metadata),), ("a",), checkpoint_tick=0)
    metadata["nested"]["x"] = 9
    assert closure.items[0].to_dict()["metadata"] == {"nested": {"x": 1}}
    with pytest.raises(TypeError):
        closure.items[0].metadata["nested"]["x"] = 4


def test_closure_edges_and_physical_result_are_fail_closed_when_constructed_directly():
    with pytest.raises(ValueError):
        PhysicalFunctionLossResultV3(RecordStatus.VALID, "", "r", "f", True, 0, evidence_hash="h")
    with pytest.raises(ValueError):
        PhysicalFunctionLossResultV3(RecordStatus.UNKNOWN, "e", "r", "f", True, 0)


def test_physical_adapter_returns_explicit_unknown_and_requests_safe_stop():
    class Fleet:
        def __init__(self):
            self.calls = []

        def request_safe_stop(self, *, tick, reason):
            self.calls.append((tick, reason))

    event = {"event_id": "loss", "robot_id": "r1", "function": "navigate", "dependency_version": "dep", "aftermath_contract": "retain"}
    fleet = Fleet()
    result = PhysicalFunctionLossAdapterV3(fleet=fleet).verify(
        event, available=False, tick=4,
        receipt={"event_id": "loss", "robot_id": "r1", "function": "navigate", "available": False,
                 "tick": 4, "dependency_version": "dep", "aftermath_contract": "retain",
                 "evidence_hash": "h", "guard_verified": True, "world_preserved": False},
    )
    assert result.status is RecordStatus.UNKNOWN
    assert result.safe_stop_requested is True
    assert fleet.calls == [(4, "event_operation_unverified")]
    malformed = PhysicalFunctionLossAdapterV3(fleet=fleet).verify({}, available=False, tick=5, receipt={"evidence_hash": "h", "guard_verified": True, "world_preserved": True})
    assert malformed.status is RecordStatus.INCOMPLETE
    assert fleet.calls[-1] == (5, "event_operation_unverified")


def test_safe_stop_is_an_event_disposition_and_never_an_item_score_status():
    event = {"event_id": "loss", "robot_id": "r1", "function": "navigate", "dependency_version": "dep", "aftermath_contract": "retain"}
    result = PhysicalFunctionLossAdapterV3().verify(event, available=False, tick=4, receipt={"event_id": "loss", "robot_id": "r1", "function": "navigate", "available": False, "tick": 4, "dependency_version": "dep", "aftermath_contract": "retain", "evidence_hash": "h", "guard_verified": True, "world_preserved": False}, request_safe_stop=False)
    with pytest.raises(ValueError, match="safe-stop request"):
        make_dynamic_event_disposition_v3(result, disposition=DynamicDispositionV3.SAFE_STOP, closure_digest="closure")
    result = PhysicalFunctionLossAdapterV3(fleet=type("Fleet", (), {"request_safe_stop": lambda self, **kwargs: None})()).verify(event, available=False, tick=4, receipt={"event_id": "loss", "robot_id": "r1", "function": "navigate", "available": False, "tick": 4, "dependency_version": "dep", "aftermath_contract": "retain", "evidence_hash": "h", "guard_verified": True, "world_preserved": False})
    disposition = make_dynamic_event_disposition_v3(result, disposition=DynamicDispositionV3.SAFE_STOP, closure_digest="closure", evidence_digests=("stop-cert",), safe_stop_certificate_digest="stop-cert", reason="guard-preserving stop")
    assert disposition.disposition is DynamicDispositionV3.SAFE_STOP
    assert disposition.physical_status is RecordStatus.UNKNOWN
    assert "safe_stop" not in {item.value for item in RecordStatus}
    with pytest.raises(ValueError, match="certificate"):
        DynamicEventDispositionV3("loss", DynamicDispositionV3.SAFE_STOP, 4, "closure", ("stop-cert",), "stop", RecordStatus.UNKNOWN)
    with pytest.raises(ValueError, match="nonempty strings"):
        DynamicEventDispositionV3("loss", DynamicDispositionV3.SAFE_STOP, 4, "closure", "stop-cert", "stop", RecordStatus.UNKNOWN, "stop-cert")
    with pytest.raises(ValueError, match="valid physical"):
        DynamicEventDispositionV3("loss", DynamicDispositionV3.SAFE_STOP, 4, "closure", ("stop-cert",), "stop", RecordStatus.VALID, "stop-cert")
    with pytest.raises(ValueError, match="valid physical"):
        DynamicEventDispositionV3("loss", DynamicDispositionV3.RECOVERED, 4, "closure", ("evidence",), "", RecordStatus.UNKNOWN)
    with pytest.raises(ValueError, match="safe_stop_requested"):
        PhysicalFunctionLossResultV3(RecordStatus.UNKNOWN, "e", "r", "f", False, 4, reason="unknown", safe_stop_requested="yes")


def test_dynamic_outcomes_preserve_missingness_and_require_two_physical_links():
    closure = freeze_affected_work_closure((WorkItemV3("a", shared_resources=("tool",)), WorkItemV3("b", kind="time_valued", shared_resources=("tool",))), ("a",), checkpoint_tick=0)
    rows = dynamic_item_outcomes_v3(identity(), closure, {
        "a": {"operation_evidence_hash": "op", "remaining_feasibility_hash": "feas", "service_fraction": .8},
    })
    assert rows[0].status is RecordStatus.VALID
    assert rows[0].extra_metadata["closure_digest"] == closure.closure_digest
    assert rows[1].status is RecordStatus.INCOMPLETE
    assert rows[1].invalid_reason == "missing_dynamic_item_observation"


def test_d4_bridge_keeps_deadline_early_completion_neutral_and_requires_service_value():
    closure = freeze_affected_work_closure(
        (WorkItemV3("deadline"), WorkItemV3("supply", kind="continuous_supply", shared_resources=("tool",)),
         WorkItemV3("other", shared_resources=("tool",))),
        ("deadline", "supply"), checkpoint_tick=0,
    )
    rows = dynamic_item_outcomes_v3(identity(), closure, {
        "deadline": {"operation_evidence_hash": "op1", "remaining_feasibility_hash": "f1", "legal_completion_fraction": 1.0},
        "other": {"operation_evidence_hash": "op3", "remaining_feasibility_hash": "f3", "legal_completion_fraction": 0.0},
        "supply": {"operation_evidence_hash": "op2", "remaining_feasibility_hash": "f2", "legal_completion_fraction": 1.0, "service_fraction": .2},
    })
    result = score_d4_outcomes_v3(rows, expected_count=3)
    assert result.status == "valid"
    assert result.point == pytest.approx(100 * (1 + .6 + 0) / 3)


def test_registered_value_evaluator_feeds_existing_dynamic_consumer():
    closure = freeze_affected_work_closure(
        (WorkItemV3("a", deadline_tick=10, shared_resources=("tool",)),
         WorkItemV3("b", deadline_tick=10, shared_resources=("tool",))),
        ("a",), checkpoint_tick=0,
    )
    registrations = {
        item: DynamicValueRegistrationV3(item, "deadline", 5, 10, (OutcomeValueV3("done", 1., True),), deadline_tick=10)
        for item in ("a", "b")
    }
    timelines = {
        "a": DynamicValueTimelineV3(
            "a", 10,
            (OutcomeStateV3("done", 5, False, "a0"), OutcomeStateV3("done", 6, True, "a1")),
            operation_evidence_hash="op-a", remaining_feasibility_hash="feas-a", progress_evidence_hash="prog-a",
        ),
    }
    rows, evaluations = evaluate_dynamic_value_outcomes_v3(identity(), closure, registrations, timelines)
    from dataclasses import replace
    mismatched = {**registrations, "b": replace(registrations["b"], horizon_tick=11)}
    with pytest.raises(ValueError, match="common onset/window"):
        evaluate_dynamic_value_outcomes_v3(identity(), closure, mismatched, timelines)
    before_checkpoint = freeze_affected_work_closure(
        closure.items, ("a",), checkpoint_tick=6)
    with pytest.raises(ValueError, match="common onset/window"):
        evaluate_dynamic_value_outcomes_v3(identity(), before_checkpoint, registrations, timelines)
    assert [row.status for row in rows] == [RecordStatus.VALID, RecordStatus.INCOMPLETE]
    assert set(evaluations) == {"a"}
    assert score_d4_outcomes_v3(rows).status == "incomplete"
    timelines["b"] = DynamicValueTimelineV3(
        "b", 10, (OutcomeStateV3("done", 5, False, "b0"),),
        operation_evidence_hash="op-b", remaining_feasibility_hash="feas-b", progress_evidence_hash="prog-b",
    )
    rows, evaluations = evaluate_dynamic_value_outcomes_v3(identity(), closure, registrations, timelines)
    result = score_d4_outcomes_v3(rows, expected_count=2)
    assert result.status == "valid" and result.point == 50
    assert all(row.extra_metadata["physical_qualified"] is False for row in rows)
