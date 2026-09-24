"""Phase 1 offline contract acceptance for the p2p participant service.

Covers the frozen interface in ``P2P_ENDPOINT_INTERFACE_DESIGN.zh-CN.md``:
the endpoint/direction derivation table (exhaustive), every frozen publish
error code, host-side pool/direction/message_id ownership, the visibility
boundary (delivery observations only; expiry/drops are evaluator-only), the
consume four-step with the frozen ``consume_message`` verb, a byte-identical
golden fixture that replays through ``validate_communication_report``, the
Goal v2 vocabulary zero-change lock, and the development-track marker.
Pure Python: no Isaac, ROS, GPU, or network.
"""

from copy import deepcopy
import json

import pytest

from demo.benchmark import goal_driven_contract
from demo.benchmark.communication import (
    CommunicationRuntime,
    validate_communication_report,
)
from demo.benchmark.communication_capacity_experiments import (
    COMMUNICATION_FORMAL_BLOCKER,
    bytes_by_direction,
)
from demo.benchmark.development_track_marker import validate_development_marker
from demo.benchmark.goal_communication_tool import (
    COMMUNICATION_TOOL_SCHEMA,
    CONSUME_ERROR_CODES,
    DEFAULT_CHANNELS,
    ERROR_CODES,
    INGRESS_ACCOUNTING_SCOPE,
    PUBLISH_ERROR_CODES,
    CommunicationEndpointTable,
    GoalCommunicationToolError,
    GoalCommunicationTools,
    communication_message_observation,
    derive_pool_direction,
    is_communication_tool_call,
    submit_communication_tool_call,
)
from demo.benchmark.goal_driven_tasks import build_goal_task_package
from demo.benchmark.goal_session import GoalSession


def _public_task(budget=10.0, robots=("r1", "r2", "r3", "r4")):
    return {
        "task_id": "comm-contract-mini",
        "budgets": {"simulation_s": budget},
        "candidate_robot_pool": [{"id": robot} for robot in robots],
    }


def _endpoint_table():
    return CommunicationEndpointTable(
        edges={"edge_1": ["r1", "r2"], "edge_2": ["r3"]},
        robots=["r1", "r2", "r3", "r4"])


def _pool_contract():
    return {
        "pools": {
            "backhaul_uplink": {"capacity_bps": 250.0, "fixed_delay_s": 0.5},
            "backhaul_downlink": {"capacity_bps": 300.0, "ttl_s": 20.0},
            "p2p": {"capacity_bps": 120.0, "fixed_delay_s": 0.25},
        },
        "access": {"robot:r2": {"capacity_bps": 90.0}},
    }


def build_service(*, contract=None, budget=10.0, episode_id="comm-test",
                  channels=DEFAULT_CHANNELS):
    return GoalCommunicationTools(
        _public_task(budget=budget), episode_id=episode_id,
        endpoints=_endpoint_table(),
        runtime=CommunicationRuntime(deepcopy(contract)),
        channels=channels)


def publish_call(call_id="c1", now=1.0, producer="robot:r1",
                 recipient="robot:r2", channel="relay", estimate=None,
                 valid_until=6.0, information_id=None, **extra_arguments):
    arguments = {
        "producer_id": producer,
        "recipients": [recipient],
        "channel": channel,
        "estimate": {"note": "x"} if estimate is None else estimate,
        "valid_until_s": valid_until,
    }
    if information_id is not None:
        arguments["information_id"] = information_id
    arguments.update(extra_arguments)
    return {
        "schema": COMMUNICATION_TOOL_SCHEMA,
        "version": 1,
        "call_id": call_id,
        "submitted_at_s": now,
        "operation": "publish",
        "arguments": arguments,
    }


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _error_code(service, call, **kwargs):
    with pytest.raises(GoalCommunicationToolError) as excinfo:
        service.submit(call, **kwargs)
    return excinfo.value


# ---------------------------------------------------------------------------
# Goal vocabulary zero-change lock (frozen sets read from the current source)
# ---------------------------------------------------------------------------
def test_goal_action_vocabulary_is_locked():
    # ``_ACTION_FIELDS``/``_EVENT_FIELDS`` live in goal_driven_contract (the
    # frozen source goal_session consumes); reading them locks them in place
    # without modifying any vocabulary.
    assert dict(goal_driven_contract._ACTION_FIELDS) == {
        "select_team": {"agent_ids"},
        "submit_plan": {"plan"},
        "revise_plan": {"trigger_event_sequence", "replaced_plan_id",
                        "reason", "plan"},
        "dispatch_skill": {"task_id", "evidence_refs"},
        "cancel_skill": {"task_id", "reason"},
        "consume_message": {"message_id"},
    }
    assert set(goal_driven_contract._EVENT_FIELDS) == {
        "episode_started", "observation", "member_join", "member_exit",
        "capability_change", "communication_status", "action_result", "tick"}


def test_goal_event_payload_fields_are_locked():
    assert dict(goal_driven_contract._EVENT_FIELDS) == {
        "episode_started": {"public_task_digest"},
        "observation": {"message_id", "producer_id", "kind", "data",
                        "valid_until_s"},
        "member_join": {"agent_id", "capability_declaration",
                        "capability_version"},
        "member_exit": {"agent_id"},
        "capability_change": {"agent_id", "capability_declaration",
                              "capability_version"},
        "communication_status": {"status", "available_bytes"},
        "action_result": {"action_id", "status", "reason"},
        "tick": {"state_digest"},
    }


def test_error_code_registry_matches_the_frozen_tables():
    assert PUBLISH_ERROR_CODES == frozenset({
        "envelope_invalid", "version_unsupported",
        "call_id_invalid_or_reused", "call_id_collision",
        "submission_time_mismatch", "budget_exhausted", "operation_unknown",
        "producer_unknown", "producer_not_current_member",
        "recipient_unknown", "recipient_not_current_member",
        "recipient_invalid", "endpoint_pair_forbidden", "channel_unknown",
        "estimate_not_canonical_json", "validity_invalid",
        "transport_internal",
    })
    assert CONSUME_ERROR_CODES == frozenset({
        "consume_unknown_message", "consume_not_delivered", "consume_stale",
        "consume_already_consumed",
    })
    assert ERROR_CODES == PUBLISH_ERROR_CODES | CONSUME_ERROR_CODES


# ---------------------------------------------------------------------------
# Endpoint table and direction derivation (exhaustive)
# ---------------------------------------------------------------------------
ALLOWED_PAIRS = {
    # robot -> robot (frozen table row 1; self pairs included literally)
    ("robot:r1", "robot:r1"): ("p2p", "p2p"),
    ("robot:r1", "robot:r2"): ("p2p", "p2p"),
    ("robot:r1", "robot:r3"): ("p2p", "p2p"),
    ("robot:r1", "robot:r4"): ("p2p", "p2p"),
    ("robot:r2", "robot:r1"): ("p2p", "p2p"),
    ("robot:r2", "robot:r2"): ("p2p", "p2p"),
    ("robot:r2", "robot:r3"): ("p2p", "p2p"),
    ("robot:r2", "robot:r4"): ("p2p", "p2p"),
    ("robot:r3", "robot:r1"): ("p2p", "p2p"),
    ("robot:r3", "robot:r2"): ("p2p", "p2p"),
    ("robot:r3", "robot:r3"): ("p2p", "p2p"),
    ("robot:r3", "robot:r4"): ("p2p", "p2p"),
    ("robot:r4", "robot:r1"): ("p2p", "p2p"),
    ("robot:r4", "robot:r2"): ("p2p", "p2p"),
    ("robot:r4", "robot:r3"): ("p2p", "p2p"),
    ("robot:r4", "robot:r4"): ("p2p", "p2p"),
    # robot -> owning edge or cloud (row 2)
    ("robot:r1", "cloud"): ("backhaul_uplink", "uplink"),
    ("robot:r2", "cloud"): ("backhaul_uplink", "uplink"),
    ("robot:r3", "cloud"): ("backhaul_uplink", "uplink"),
    ("robot:r4", "cloud"): ("backhaul_uplink", "uplink"),
    ("robot:r1", "edge:edge_1"): ("backhaul_uplink", "uplink"),
    ("robot:r2", "edge:edge_1"): ("backhaul_uplink", "uplink"),
    ("robot:r3", "edge:edge_2"): ("backhaul_uplink", "uplink"),
    # edge -> cloud (row 3)
    ("edge:edge_1", "cloud"): ("backhaul_uplink", "uplink"),
    ("edge:edge_2", "cloud"): ("backhaul_uplink", "uplink"),
    # cloud -> edge or robot (row 4)
    ("cloud", "edge:edge_1"): ("backhaul_downlink", "downlink"),
    ("cloud", "edge:edge_2"): ("backhaul_downlink", "downlink"),
    ("cloud", "robot:r1"): ("backhaul_downlink", "downlink"),
    ("cloud", "robot:r2"): ("backhaul_downlink", "downlink"),
    ("cloud", "robot:r3"): ("backhaul_downlink", "downlink"),
    ("cloud", "robot:r4"): ("backhaul_downlink", "downlink"),
    # edge -> jurisdiction robot (row 5)
    ("edge:edge_1", "robot:r1"): ("backhaul_downlink", "downlink"),
    ("edge:edge_1", "robot:r2"): ("backhaul_downlink", "downlink"),
    ("edge:edge_2", "robot:r3"): ("backhaul_downlink", "downlink"),
}


def test_direction_derivation_table_is_exhaustive():
    table = _endpoint_table()
    endpoints = sorted(table.endpoints())
    assert endpoints == ["cloud", "edge:edge_1", "edge:edge_2", "robot:r1",
                         "robot:r2", "robot:r3", "robot:r4"]
    allowed = forbidden = 0
    for producer in endpoints:
        for recipient in endpoints:
            if (producer, recipient) in ALLOWED_PAIRS:
                allowed += 1
                assert derive_pool_direction(
                    table, producer, recipient) == ALLOWED_PAIRS[(producer, recipient)]
            else:
                forbidden += 1
                with pytest.raises(GoalCommunicationToolError) as excinfo:
                    derive_pool_direction(table, producer, recipient)
                assert excinfo.value.error_code == "endpoint_pair_forbidden"
    assert (allowed, forbidden) == (34, 15)
    assert len(ALLOWED_PAIRS) == 34


def test_unregistered_or_malformed_endpoints_are_forbidden_or_unknown():
    table = _endpoint_table()
    for bogus in ("edge:edge_9", "robot:r9", "r1", "edge_1", "cloud_9",
                  "robot:", "edge:", "", "satellite:x"):
        with pytest.raises(GoalCommunicationToolError) as excinfo:
            derive_pool_direction(table, bogus, "robot:r1")
        assert excinfo.value.error_code == "endpoint_pair_forbidden"
        with pytest.raises(GoalCommunicationToolError) as excinfo:
            derive_pool_direction(table, "cloud", bogus)
        assert excinfo.value.error_code == "endpoint_pair_forbidden"
    assert not table.is_registered(None)


def test_endpoint_registry_rejects_invalid_registrations():
    with pytest.raises(ValueError, match="more than one edge"):
        CommunicationEndpointTable(edges={"e1": ["r1"], "e2": ["r1"]},
                                   robots=["r1"])
    with pytest.raises(ValueError, match="not a registered robot"):
        CommunicationEndpointTable(edges={"e1": ["rX"]}, robots=["r1"])
    with pytest.raises(ValueError, match="duplicate robot registration"):
        CommunicationEndpointTable(edges={"e1": ["r1"]}, robots=["r1", "r1"])
    with pytest.raises(ValueError, match="must not contain ':'"):
        CommunicationEndpointTable(edges={"e:1": ["r1"]}, robots=["r1"])
    with pytest.raises(ValueError, match="outside the public candidate pool"):
        GoalCommunicationTools(_public_task(robots=("r1", "r2", "r3")),
                               episode_id="x", endpoints=_endpoint_table())
    with pytest.raises(ValueError, match="channels"):
        GoalCommunicationTools(_public_task(), episode_id="x",
                               endpoints={"robots": ["r1"]}, channels=["a", "a"])
    with pytest.raises(ValueError, match="runtime"):
        GoalCommunicationTools(_public_task(), episode_id="x",
                               endpoints={"robots": ["r1"]}, runtime=object())


def test_publish_response_echoes_derived_pool_and_direction():
    service = build_service(contract=_pool_contract())
    cases = [
        ("robot:r1", "robot:r2", "p2p", "p2p"),
        ("robot:r1", "cloud", "backhaul_uplink", "uplink"),
        ("robot:r2", "edge:edge_1", "backhaul_uplink", "uplink"),
        ("edge:edge_1", "cloud", "backhaul_uplink", "uplink"),
        ("cloud", "edge:edge_1", "backhaul_downlink", "downlink"),
        ("cloud", "robot:r3", "backhaul_downlink", "downlink"),
        ("edge:edge_2", "robot:r3", "backhaul_downlink", "downlink"),
    ]
    seen = set()
    for index, (producer, recipient, pool, direction) in enumerate(cases):
        response = service.submit(
            publish_call(f"c{index}", producer=producer, recipient=recipient,
                         valid_until=50.0),
            time_s=1.0)
        assert response["accepted"] is True
        assert response["pool"] == pool
        assert response["direction"] == direction
        assert response["operation"] == "publish"
        assert response["execution_validated"] is False
        assert response["message_id"] == f"comm-test:comm:{index}"
        assert response["recipients"] == [recipient]
        seen.add((pool, direction))
    assert seen == {("p2p", "p2p"), ("backhaul_uplink", "uplink"),
                    ("backhaul_downlink", "downlink")}


# ---------------------------------------------------------------------------
# Every frozen publish error code is exercised deterministically
# ---------------------------------------------------------------------------
def _rejection_scenario(code):
    service = build_service()
    call = publish_call()
    kwargs = {"time_s": 1.0}
    if code == "envelope_invalid":
        call = dict(publish_call())
        call["pool"] = "p2p"
    elif code == "version_unsupported":
        call = dict(publish_call())
        call["version"] = 2
    elif code == "call_id_invalid_or_reused":
        call = publish_call(call_id="   ")
    elif code == "call_id_collision":
        call = publish_call(call_id="goal-7")
        kwargs["reserved_call_ids"] = ["goal-7"]
    elif code == "submission_time_mismatch":
        call = publish_call(now=2.0)
    elif code == "budget_exhausted":
        service = build_service(budget=5.0)
        call = publish_call(now=5.0, valid_until=9.0)
        kwargs["time_s"] = 5.0
    elif code == "operation_unknown":
        call = dict(publish_call())
        call["operation"] = "consume"
    elif code == "producer_unknown":
        call = publish_call(producer="robot:r9")
    elif code == "producer_not_current_member":
        kwargs["current_agents"] = {"r2", "r3", "r4"}
    elif code == "recipient_unknown":
        call = publish_call(recipient="edge:edge_9")
    elif code == "recipient_not_current_member":
        kwargs["current_agents"] = {"r1", "r3", "r4"}
    elif code == "recipient_invalid":
        call = publish_call()
        call["arguments"] = {**call["arguments"],
                             "recipients": ["robot:r2", "robot:r3"]}
    elif code == "endpoint_pair_forbidden":
        call = publish_call(producer="edge:edge_1", recipient="edge:edge_2")
    elif code == "channel_unknown":
        call = publish_call(channel="wifi")
    elif code == "estimate_not_canonical_json":
        call = publish_call(estimate={"x": float("nan")})
    elif code == "validity_invalid":
        call = publish_call(valid_until=1.0)
    else:
        raise AssertionError(f"unexpected code {code}")
    return service, call, kwargs


@pytest.mark.parametrize("code", sorted(PUBLISH_ERROR_CODES - {"transport_internal"}))
def test_each_publish_error_code_is_deterministic(code):
    service, call, kwargs = _rejection_scenario(code)
    with pytest.raises(GoalCommunicationToolError) as excinfo:
        service.submit(call, **kwargs)
    error = excinfo.value
    assert isinstance(error, ValueError)
    assert error.error_code == code
    assert code in str(error)
    row = service.report()["calls"][-1]
    assert row["status"] == "rejected"
    assert row["error_code"] == code
    assert row["accounting_scope"] == INGRESS_ACCOUNTING_SCOPE


def test_more_envelope_and_shape_rejections():
    # Each attempt burns its call_id (ids are globally unique), so every
    # rejection scenario uses a fresh one.
    service = build_service()
    assert _error_code(service, "not-a-mapping", time_s=1.0).error_code == "envelope_invalid"
    wrong_schema = dict(publish_call("e2"))
    wrong_schema["schema"] = "eai.benchmark.goal_pose_tool_call"
    assert _error_code(service, wrong_schema, time_s=1.0).error_code == "envelope_invalid"
    missing = publish_call("e3")
    del missing["arguments"]["estimate"]
    assert _error_code(service, missing, time_s=1.0).error_code == "envelope_invalid"
    bad_info = publish_call("e4")
    bad_info["arguments"]["information_id"] = 7
    assert _error_code(service, bad_info, time_s=1.0).error_code == "envelope_invalid"
    not_object_args = dict(publish_call("e5"))
    not_object_args["arguments"] = ["producer_id"]
    assert _error_code(service, not_object_args, time_s=1.0).error_code == "envelope_invalid"
    assert _error_code(service, publish_call("e6", now="soon"), time_s=1.0).error_code == "submission_time_mismatch"
    assert _error_code(service, publish_call("e7", estimate=42), time_s=1.0).error_code == "estimate_not_canonical_json"
    for index, recipients in enumerate(([], "robot:r2", ["robot:r2", "robot:r2"], [7])):
        call = publish_call(f"e{8 + index}")
        call["arguments"] = {**call["arguments"], "recipients": recipients}
        assert _error_code(service, call, time_s=1.0).error_code == "recipient_invalid"


def test_transport_internal_records_a_platform_gap(monkeypatch):
    service = build_service(contract=_pool_contract())

    def boom(**_kwargs):
        raise RuntimeError("transport exploded")

    monkeypatch.setattr(service.runtime, "publish", boom)
    with pytest.raises(GoalCommunicationToolError) as excinfo:
        service.submit(publish_call(), time_s=1.0)
    assert excinfo.value.error_code == "transport_internal"
    assert service.report()["calls"][-1]["error_code"] == "transport_internal"
    assert service.communication_report()["messages"] == {}


def test_call_id_is_globally_unique_once_seen():
    service = build_service()
    assert service.submit(publish_call("c1"), time_s=1.0)["accepted"] is True
    assert _error_code(service, publish_call("c1"), time_s=1.0).error_code == (
        "call_id_invalid_or_reused")
    # A rejected call also burns its call id: ids are globally unique.
    assert _error_code(service, publish_call("c2", channel="wifi"),
                       time_s=1.0).error_code == "channel_unknown"
    assert _error_code(service, publish_call("c2"), time_s=1.0).error_code == (
        "call_id_invalid_or_reused")
    assert service.owns_call("c1") and not service.owns_call("c9")


def test_participants_cannot_inject_pool_direction_or_message_id():
    service = build_service()
    for index, field in enumerate(("pool", "direction", "message_id",
                                   "access_robot_id")):
        error = _error_code(service, publish_call(f"ij{index}", **{field: "injected"}),
                            time_s=1.0)
        assert error.error_code == "envelope_invalid"
        assert "cannot be injected" in str(error)
    top_level = dict(publish_call("ij-top"))
    top_level["pool"] = "p2p"
    assert _error_code(service, top_level, time_s=1.0).error_code == "envelope_invalid"
    # The accepted path always echoes host-derived values, never inputs.
    response = service.submit(publish_call("ok-1"), time_s=1.0)
    assert (response["pool"], response["direction"]) == ("p2p", "p2p")
    assert response["message_id"] == "comm-test:comm:0"


def test_publish_rejected_after_budget_while_consume_stays_allowed():
    service = build_service(budget=5.0)
    response = service.submit(publish_call("b1", now=1.0, valid_until=20.0),
                              time_s=1.0)
    observations = service.advance(1.0)
    assert len(observations) == 1
    assert _error_code(service, publish_call("b2", now=5.0, valid_until=9.0),
                       time_s=5.0).error_code == "budget_exhausted"
    receipt = service.consume(response["message_id"], action_id="consume-b1",
                              time_s=6.0)
    assert receipt["consumer_id"] == "consume-b1"


def test_wire_size_is_computed_from_public_payload_fields():
    service = build_service()
    estimate = {"bearing": 1.25, "tags": ["a", 2.5], "ok": True}
    response = service.submit(
        publish_call("s1", estimate=estimate, valid_until=6.0), time_s=1.0)
    payload = {
        "message_id": response["message_id"],
        "producer_id": "robot:r1",
        "published_at_s": 1.0,
        "valid_until_s": 6.0,
        "estimate": estimate,
    }
    expected = len(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                              separators=(",", ":"), allow_nan=False)
                   .encode("utf-8"))
    assert response["size_bytes"] == expected


def test_pool_ttl_shortens_effective_validity_in_response():
    service = build_service(contract=_pool_contract())
    shortened = service.submit(
        publish_call("t1", producer="cloud", recipient="robot:r3",
                     channel="dispatch", valid_until=100.0, now=1.0),
        time_s=1.0)
    assert shortened["valid_until_s"] == pytest.approx(21.0)  # 1.0 + ttl 20.0
    untouched = service.submit(publish_call("t2", valid_until=40.0, now=1.0),
                               time_s=1.0)
    assert untouched["valid_until_s"] == pytest.approx(40.0)


# ---------------------------------------------------------------------------
# Visibility boundary
# ---------------------------------------------------------------------------
def test_delivery_observation_shape_and_no_state_leak():
    service = build_service(contract=_pool_contract())
    response = service.submit(
        publish_call("v1", valid_until=6.0, information_id="info-1"), time_s=1.0)
    # The p2p access bottleneck delays delivery; pump once it has landed but
    # before the declared validity ends.
    observations = service.advance(5.0)
    assert [row["message_id"] for row in observations] == [response["message_id"]]
    payload = observations[0]
    assert set(payload) == {"message_id", "producer_id", "kind", "data",
                            "valid_until_s"}
    assert payload["kind"] == "communication_message"
    assert payload["producer_id"] == "robot:r1"
    assert set(payload["data"]) == {"recipient_id", "estimate",
                                    "information_id"}
    assert payload["data"]["recipient_id"] == "robot:r2"
    assert payload["data"]["information_id"] == "info-1"
    assert "published_at_s" not in payload["data"]
    # Participant-visible surfaces never expose pool/queue state or delivery
    # diagnostics; direction/pool in the response are the sanctioned echo.
    blob = json.dumps({"profile": service.public_profile(),
                       "response": response,
                       "observations": observations}, sort_keys=True)
    for leak in ("available_bytes", "drop_reason", "queued",
                 "conditions", "scheduled_delivery_at_s", "pool_utilization",
                 "bytes_served", "delivered_at_s"):
        assert leak not in blob
    assert set(service.public_profile()) == {
        "schema", "version", "episode_id", "public_task_sha256", "optional",
        "endpoints", "allowed_directions", "channels", "validity",
        "call_schema", "call_version", "call_fields", "consumption", "cost"}
    assert service.public_profile()["optional"] is True
    assert service.public_profile()["call_fields"] == [
        "call_id", "submitted_at_s", "operation", "arguments"]
    # communication_status stays unemitted by this service (design ruling R3).
    assert {row["kind"] for row in observations} == {"communication_message"}


def test_observation_constructor_rejects_malformed_payloads():
    with pytest.raises(ValueError, match="recipient_id"):
        communication_message_observation(
            {"message_id": "m", "producer_id": "p", "valid_until_s": 1.0,
             "estimate": {}}, recipient_id=" ")
    with pytest.raises(ValueError, match="estimate"):
        communication_message_observation(
            {"message_id": "m", "producer_id": "p", "valid_until_s": 1.0,
             "estimate": "x"}, recipient_id="robot:r2")


def test_expired_message_is_never_a_participant_event():
    contract = {"pools": {"p2p": {"capacity_bps": 1000.0, "fixed_delay_s": 2.0}}}
    service = build_service(contract=contract)
    response = service.submit(publish_call("e1", valid_until=2.5, now=1.0),
                              time_s=1.0)
    assert service.advance(5.0) == []
    delivery = service.communication_report()["messages"][
        response["message_id"]]["recipients"]["robot:r2"]
    assert delivery["status"] == "expired"  # evaluator-only evidence
    with pytest.raises(GoalCommunicationToolError) as excinfo:
        service.consume(response["message_id"], action_id="a1", time_s=5.0)
    assert excinfo.value.error_code == "consume_not_delivered"


def test_delivered_but_stale_before_pump_is_suppressed():
    contract = {"pools": {"p2p": {"capacity_bps": 1000.0, "fixed_delay_s": 0.5}}}
    service = build_service(contract=contract)
    response = service.submit(publish_call("s1", valid_until=2.0, now=1.0),
                              time_s=1.0)
    # Delivered at ~1.5 but the host first pumps at 3.0: expired messages are
    # not participant events, so the observation is suppressed forever.
    assert service.advance(3.0) == []
    assert service.advance(4.0) == []
    with pytest.raises(GoalCommunicationToolError) as excinfo:
        service.consume(response["message_id"], action_id="a1", time_s=4.0)
    assert excinfo.value.error_code == "consume_not_delivered"


def test_dropped_message_is_not_a_participant_event():
    contract = {"pools": {"p2p": {"capacity_bps": 100.0, "active_drop": True}}}
    service = build_service(contract=contract)
    response = service.submit(publish_call("d1"), time_s=1.0)
    observations = service.advance(3.0)
    assert observations == []
    blob = json.dumps(observations)
    assert "drop" not in blob and "active" not in blob
    delivery = service.communication_report()["messages"][
        response["message_id"]]["recipients"]["robot:r2"]
    assert delivery["status"] == "dropped"


def test_member_exit_suppresses_inflight_delivery_but_keeps_bytes():
    contract = {"pools": {"p2p": {"capacity_bps": 1000.0, "fixed_delay_s": 1.0}}}
    service = build_service(contract=contract)
    response = service.submit(publish_call("m1", valid_until=10.0, now=1.0),
                              time_s=1.0)
    observations = service.advance(3.0, current_agents={"r1", "r3", "r4"})
    assert observations == []
    message = service.communication_report()["messages"][response["message_id"]]
    assert message["recipients"]["robot:r2"]["status"] == "delivered"
    assert message["size_bytes"] > 0  # bytes already metered, not rolled back
    # The robot is still a roster member in a later pump: delivery emits then.
    later = service.advance(3.0, current_agents={"r1", "r2", "r3", "r4"})
    assert [row["message_id"] for row in later] == [response["message_id"]]


# ---------------------------------------------------------------------------
# Consume: frozen verb, four-step prevalidation, stable codes
# ---------------------------------------------------------------------------
def test_consume_rejection_codes_and_idempotence():
    contract = {"pools": {
        "p2p": {"capacity_bps": 1000.0, "fixed_delay_s": 1.0}}}
    service = build_service(contract=contract)
    with pytest.raises(GoalCommunicationToolError) as excinfo:
        service.consume("missing", action_id="a0", time_s=1.0)
    assert excinfo.value.error_code == "consume_unknown_message"
    in_flight = service.submit(
        publish_call("k1", valid_until=4.0, now=1.0), time_s=1.0)
    with pytest.raises(GoalCommunicationToolError) as excinfo:
        service.consume(in_flight["message_id"], action_id="a1", time_s=1.0)
    assert excinfo.value.error_code == "consume_not_delivered"
    short_lived = service.submit(
        publish_call("k2", valid_until=3.5, now=1.0), time_s=1.0)
    # Both p2p copies share the pool, so the second lands slightly later.
    observations = service.advance(3.0)
    assert {row["message_id"] for row in observations} == {
        in_flight["message_id"], short_lived["message_id"]}
    assert service.owns_message(in_flight["message_id"])
    assert service.can_consume(in_flight["message_id"], time_s=3.0)
    receipt = service.consume(in_flight["message_id"], action_id="a1",
                              time_s=3.0)
    assert receipt["consumer_id"] == "a1"
    retried = service.consume(in_flight["message_id"], action_id="a1",
                              time_s=3.1)
    assert retried == receipt  # action-id retry idempotence
    with pytest.raises(GoalCommunicationToolError) as excinfo:
        service.consume(in_flight["message_id"], action_id="a2", time_s=3.1)
    assert excinfo.value.error_code == "consume_already_consumed"
    with pytest.raises(GoalCommunicationToolError) as excinfo:
        service.consume(short_lived["message_id"], action_id="a3", time_s=4.0)
    assert excinfo.value.error_code == "consume_stale"
    with pytest.raises(ValueError, match="already bound to another message"):
        service.consume(short_lived["message_id"], action_id="a1", time_s=4.0)
    rows = service.report()["consume_attempts"]
    codes = [row["error_code"] for row in rows
             if row["status"] == "rejected" and row["error_code"] is not None]
    assert codes == ["consume_unknown_message", "consume_not_delivered",
                     "consume_already_consumed", "consume_stale"]
    assert not service.can_consume(short_lived["message_id"], time_s=4.0)


@pytest.fixture(scope="module")
def bundle():
    return build_goal_task_package("warehouse_nvd")


def test_consume_four_step_with_goal_session_and_unique_message_ids(bundle):
    ids = [row["id"] for row in bundle["public_task"]["candidate_robot_pool"]]
    service = GoalCommunicationTools(
        bundle["public_task"], episode_id="comm-goal",
        endpoints={"edges": {"edge_1": [ids[0]]}, "robots": ids[:3]},
        runtime=CommunicationRuntime())
    session = GoalSession(bundle["public_task"], bundle["participant_api"],
                          episode_id="comm-goal")
    first = service.submit(
        publish_call("g1", producer=f"robot:{ids[0]}", recipient=f"robot:{ids[1]}",
                     valid_until=5.0, now=0.0), time_s=0.0)
    second = service.submit(
        publish_call("g2", producer=f"robot:{ids[0]}", recipient=f"robot:{ids[2]}",
                     valid_until=5.0, now=0.0), time_s=0.0)
    assert [first["message_id"], second["message_id"]] == [
        "comm-goal:comm:0", "comm-goal:comm:1"]
    observations = service.advance(0.0)
    assert len(observations) == 2
    # Host-allocated ids pass the GoalSession observation uniqueness check.
    for observation in observations:
        session.emit("observation", 0.0, observation)
    with pytest.raises(ValueError, match="already been delivered"):
        session.emit("observation", 0.0, observations[0])
    # Steps 1/2: ownership and current delivered-and-fresh prevalidation.
    assert service.owns_message(first["message_id"])
    assert not service.owns_message("comm-goal:comm:99")
    assert service.can_consume(first["message_id"], time_s=0.0)
    # Step 3: the frozen consume_message goal action.
    action = {
        "schema": "eai.benchmark.goal_action", "version": 1,
        "action_id": "consume-1", "submitted_at_s": 0.0,
        "type": "consume_message",
        "payload": {"message_id": first["message_id"]},
    }
    assert session.submit_action(action)["accepted"] is True
    # Step 4: transport consume with consumer_id = action id.
    receipt = service.consume(first["message_id"], action_id="consume-1",
                              time_s=0.0)
    assert receipt["consumer_id"] == "consume-1"
    assert first["message_id"] in session.report()["consumed_message_ids"]


def test_fail_closed_when_interface_is_not_connected():
    call = publish_call()
    with pytest.raises(ValueError,
                       match="communication tool interface was not connected"):
        submit_communication_tool_call(None, call, time_s=1.0)
    assert is_communication_tool_call(call)
    assert not is_communication_tool_call({"schema": "eai.benchmark.goal_action"})
    assert not is_communication_tool_call("publish")


# ---------------------------------------------------------------------------
# Three-pool ledger, access constraints, direction completeness, ingress split
# ---------------------------------------------------------------------------
def test_three_pool_ledger_access_constraints_and_direction_completeness():
    service = build_service(contract=_pool_contract())
    submitted = [
        publish_call("p1", producer="robot:r1", recipient="robot:r2",
                     channel="relay", valid_until=60.0, now=0.0),
        publish_call("p2", producer="robot:r2", recipient="cloud",
                     channel="telemetry", valid_until=60.0, now=0.0),
        publish_call("p3", producer="cloud", recipient="edge:edge_1",
                     channel="plan", valid_until=60.0, now=0.0),
        publish_call("p4", producer="cloud", recipient="robot:r3",
                     channel="dispatch", valid_until=60.0, now=0.0),
        publish_call("p5", producer="edge:edge_2", recipient="robot:r3",
                     channel="plan", valid_until=60.0, now=0.0),
        publish_call("p6", producer="edge:edge_1", recipient="cloud",
                     channel="observation", valid_until=60.0, now=0.0),
    ]
    responses = [service.submit(call, time_s=0.0) for call in submitted]
    service.advance(30.0)
    transport = service.communication_report()
    ledger = transport["ledger"]
    served = [row for row in ledger if row["event"] == "bytes_served"]
    assert served
    # Every served row carries the explicit host-derived direction.
    assert all(row["direction"] is not None for row in served)
    breakdown = bytes_by_direction(ledger)
    assert breakdown["direction_complete"] is True
    assert breakdown["bytes_by_direction"] is not None
    assert set(breakdown["bytes_by_pool"]) == {
        "p2p", "backhaul_uplink", "backhaul_downlink"}
    # Each served row's pool/direction matches the frozen derivation table.
    for row in served:
        message = transport["messages"][row["message_id"]]
        pool, direction = service.endpoints.derive(
            message["payload"]["producer_id"], row["recipient"])
        assert (row["pool"], row["direction"]) == (pool, direction)
    # Pool bytes are exactly the published message bytes; ingress stays out.
    total_message_bytes = sum(m["size_bytes"] for m in transport["messages"].values())
    assert breakdown["total_bytes"] == total_message_bytes
    report = service.report()
    ingress_total = report["ingress"]["total_call_bytes"]
    assert ingress_total == sum(row["size_bytes"] for row in report["calls"])
    assert ingress_total > 0
    # p2p spans both robot access endpoints; the tighter one bottlenecks.
    p2p_delivery = transport["messages"][responses[0]["message_id"]]["recipients"]["robot:r2"]
    assert p2p_delivery["access_endpoints"] == ["robot:r1", "robot:r2"]
    assert p2p_delivery["scheduled_finish_at_s"] == pytest.approx(
        responses[0]["size_bytes"] / 90.0)
    # robot:r2's shared last-mile access serializes its next uplink.
    uplink_delivery = transport["messages"][responses[1]["message_id"]]["recipients"]["cloud"]
    assert uplink_delivery["access_endpoints"] == ["robot:r2"]
    assert uplink_delivery["scheduled_finish_at_s"] == pytest.approx(
        responses[0]["size_bytes"] / 90.0 + responses[1]["size_bytes"] / 90.0)
    assert validate_communication_report(transport)["validated"] is True


# ---------------------------------------------------------------------------
# Golden fixture: byte-identical reruns and replay validation
# ---------------------------------------------------------------------------
def _golden_contract():
    return {
        "pools": {
            "backhaul_uplink": {"capacity_bps": 250.0, "fixed_delay_s": 0.5},
            "backhaul_downlink": {"capacity_bps": 300.0, "ttl_s": 20.0},
            "p2p": {"capacity_bps": 120.0, "fixed_delay_s": 0.25},
        },
        "access": {"robot:r1": {"capacity_bps": 100.0}},
    }


def _golden_episode_report():
    service = GoalCommunicationTools(
        _public_task(budget=100.0), episode_id="golden",
        endpoints=_endpoint_table(),
        runtime=CommunicationRuntime(_golden_contract()))
    alpha = {"bearing": 1.25, "quality": 0.5}
    first = service.submit(
        publish_call("g1", now=1.0, producer="robot:r1", recipient="robot:r2",
                     channel="relay", estimate=alpha, valid_until=30.0,
                     information_id="info-alpha"), time_s=1.0)
    service.submit(
        publish_call("g2", now=1.0, producer="robot:r1", recipient="robot:r3",
                     channel="relay", estimate=alpha, valid_until=30.0,
                     information_id="info-alpha"), time_s=1.0)
    service.submit(
        publish_call("g3", now=2.0, producer="robot:r2", recipient="cloud",
                     channel="telemetry", estimate={"battery": 0.8},
                     valid_until=25.0), time_s=2.0)
    fourth = service.submit(
        publish_call("g4", now=2.0, producer="cloud", recipient="robot:r3",
                     channel="dispatch", estimate={"task": "t-9"},
                     valid_until=50.0), time_s=2.0)
    service.submit(
        publish_call("g5", now=3.0, producer="edge:edge_1", recipient="cloud",
                     channel="observation", estimate={"queue_len": 2},
                     valid_until=26.0), time_s=3.0)
    observations = service.advance(4.0)
    service.consume(observations[0]["message_id"], action_id="consume-g3",
                    time_s=4.0)
    service.submit(
        publish_call("g6", now=4.5, producer="edge:edge_2",
                     recipient="robot:r3", channel="plan",
                     estimate={"order": 7}, valid_until=40.0), time_s=4.5)
    later = service.advance(9.0)
    service.consume(fourth["message_id"], action_id="consume-g4", time_s=9.0)
    service.advance(30.0)
    report = service.report()
    assert first["message_id"] == "golden:comm:0"
    return report, observations, later


def test_golden_fixture_is_byte_identical_and_replay_valid():
    first_report, first_observations, first_later = _golden_episode_report()
    second_report, second_observations, second_later = _golden_episode_report()
    assert _canonical(first_report) == _canonical(second_report)
    assert _canonical(first_observations) == _canonical(second_observations)
    assert _canonical(first_later) == _canonical(second_later)
    # Deterministic delivery split: the p2p pool serializes the fan-out
    # copies behind the robot:r1 access capacity and the shared uplink pool
    # serializes g5 behind g3, so g1/g2/g5 land across the two pumps.
    assert [row["kind"] for row in first_observations] == [
        "communication_message"] * 3
    assert [row["message_id"] for row in first_observations] == [
        "golden:comm:2", "golden:comm:0", "golden:comm:3"]
    assert [row["message_id"] for row in first_later] == [
        "golden:comm:4", "golden:comm:1", "golden:comm:5"]
    result = validate_communication_report(first_report["communication"])
    assert result["validated"] is True
    assert result["published_count"] == 6
    # Bind the replay to the frozen initial contract as an evaluator would.
    bound = validate_communication_report(
        first_report["communication"],
        expected_initial_contract=CommunicationRuntime(
            _golden_contract()).report()["initial_contract"])
    assert bound["validated"] is True
    # Fan-out copies share information_id and aggregate AoI per recipient.
    items = first_report["communication"]["aoi"]["continuous"]["items"]
    alpha = [row for row in items if row["information_id"] == "info-alpha"]
    assert sorted(row["recipient"] for row in alpha) == [
        "robot:r2", "robot:r3"]


# ---------------------------------------------------------------------------
# Development marker and release flags
# ---------------------------------------------------------------------------
def test_report_stamped_with_contract_marker_and_false_release_flags():
    report, _, _ = _golden_episode_report()
    track = report["development_track"]
    assert track["evidence_class"] == "contract"
    assert track["infrastructure_blockers"] == [COMMUNICATION_FORMAL_BLOCKER]
    assert track["release_eligible"] is False
    for flag in ("execution_validated", "business_outcomes_validated",
                 "scoreable"):
        assert track[flag] is False
        assert report[flag] is False
    assert report["release_eligible"] is False
    assert report["formal_statistics"] is False
    assert report["evidence_role"] == "development_track_evidence"
    assert report["visibility"] == "evaluator_only"
    validate_development_marker(track)
    # Untimed components stay null instead of being inferred.
    assert report["timing"]["wall_clock_s"] is None
    assert report["timing"]["notification_delivery_delay_s"] is None
    assert report["timing"]["planning_compute_duration_s"] is None


def test_content_borne_consumer_ids_cannot_change_authorization():
    # Viewer audit P0-1: a participant-controlled estimate must never alter
    # consume permissions. A message whose content carries an empty (or
    # foreign) consumer_ids list stays a generic once-per-recipient message.
    contract = {"pools": {"p2p": {"capacity_bps": 1000.0,
                                  "fixed_delay_s": 1.0}}}
    service = build_service(contract=contract)
    response = service.submit(
        publish_call("auth1", estimate={"consumer_ids": []}, valid_until=6.0,
                     now=1.0), time_s=1.0)
    observations = service.advance(3.0)
    assert [row["message_id"] for row in observations] == [response["message_id"]]
    assert service.can_consume(response["message_id"], time_s=3.0)
    receipt = service.consume(response["message_id"], action_id="a1",
                              time_s=3.0)
    assert receipt["consumer_id"] == "a1"


def test_ttl_truncation_is_consistent_across_receipt_observation_and_stale():
    # Viewer audit P0-2: the pool-TTL-truncated validity must be the single
    # validity the participant sees and the stale check enforces.
    service = build_service(contract=_pool_contract())
    response = service.submit(
        publish_call("ttl1", producer="cloud", recipient="robot:r3",
                     channel="dispatch", valid_until=100.0, now=1.0),
        time_s=1.0)
    assert response["valid_until_s"] == pytest.approx(21.0)  # 1.0 + ttl 20
    observations = service.advance(10.0)
    assert [row["message_id"] for row in observations] == [response["message_id"]]
    assert observations[0]["valid_until_s"] == pytest.approx(21.0)
    # Between effective (21) and requested (101) validity: stale, not fresh.
    assert not service.can_consume(response["message_id"], time_s=22.0)
    with pytest.raises(GoalCommunicationToolError) as excinfo:
        service.consume(response["message_id"], action_id="a9", time_s=22.0)
    assert excinfo.value.error_code == "consume_stale"
