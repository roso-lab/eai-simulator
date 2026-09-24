"""Contract tests for the opt-in Restaurant after-observation observer."""

from copy import deepcopy

import pytest

from demo.benchmark.communication import CommunicationRuntime
from demo.benchmark.goal_driven_contract import build_goal_participant_api
from demo.benchmark.goal_driven_tasks import build_goal_task_package
from demo.benchmark.goal_restaurant_interventions import (
    GoalRestaurantInterventions,
    verify_restaurant_intervention_report,
)
from demo.benchmark.goal_session import GoalSession


EPISODE_ID = "restaurant-intervention-test"


def _public(factor_id="communication_quality", factor_level="immediate"):
    return build_goal_task_package(
        "restaurant", factor_id=factor_id, factor_level=factor_level,
    )["public_task"]


def _runtime():
    return CommunicationRuntime({
        "pools": {
            "backhaul_uplink": {"capacity_bps": None},
            "backhaul_downlink": {"capacity_bps": None},
            "p2p": {"capacity_bps": None},
        },
    })


def _initial_state(public):
    regions = {
        row["id"]: "restaurant_staging"
        for row in public["candidate_robot_pool"]
    }
    objects = {
        row["id"]: {
            "region_id": row["physical_properties"]["initial_region_id"]}
        for row in public["public_world"]["objects"]
    }
    return {"robot_regions": regions, "object_states": objects}


def _action(action_id, action_type, payload, now=0.0):
    return {
        "schema": "eai.benchmark.goal_action",
        "version": 1,
        "action_id": action_id,
        "submitted_at_s": now,
        "type": action_type,
        "payload": deepcopy(payload),
    }


class _Host:
    def __init__(self, public, selected):
        self.session = GoalSession(
            public, build_goal_participant_api(public), episode_id=EPISODE_ID)
        state = _initial_state(public)
        self.session.update_runtime_state(
            0.0, state["robot_regions"], state["object_states"])
        self.session.submit_action(_action(
            "select", "select_team", {"agent_ids": list(selected)}))

    def trusted_report(self):
        return {"session": self.session.report()}

    def emit(self, event_type, time_s, payload):
        return self.session.emit(event_type, time_s, payload)

    def update_runtime_state(self, time_s, robot_regions, object_states):
        return self.session.update_runtime_state(
            time_s, robot_regions, object_states)

    def advance_to(self, time_s):
        state = self.session.report()["runtime_state"]
        self.session.update_runtime_state(
            time_s, state["robot_regions"], state["object_states"])


def _task(*, task_id="arbitrary-observation-node", region="kitchen_service",
          assigned=None, skill="observe"):
    return {
        "task_id": task_id,
        "skill": skill,
        "arguments": {"region_id": region},
        "assigned_agents": list(assigned or ["restaurant_carter_1"]),
    }


def _result(task, *, now=10.0, status="succeeded", producer=None):
    producer = producer or task["assigned_agents"][0]
    receipt = {
        "backend_id": "test.restaurant.backend",
        "receipt_ref": f"{EPISODE_ID}/{task['task_id']}",
        "task_id": task["task_id"],
        "action_id": f"dispatch:{task['task_id']}",
        "status": status,
        "completed_at_s": now,
        "episode_id": EPISODE_ID,
        "evidence_refs": [f"sha256:evidence:{task['task_id']}"],
    }
    result = {"status": status, "completed_at_s": now, "receipt": receipt}
    if status == "succeeded":
        region = task["arguments"]["region_id"]
        result["trusted_observation"] = {
            "schema": "eai.restaurant.readiness_service.v1",
            "episode_id": EPISODE_ID,
            "kind": "native_observation",
            "region_id": region,
            "observation_id": f"{EPISODE_ID}:{region}:17",
            "producer_id": producer,
            "simulation_step": 17,
            "time_s": now - 0.25,
            "evidence_digest": f"sha256:scene:{region}",
        }
    return result


def _native_observation(task, result):
    fact = result["trusted_observation"]
    return {
        "kind": "observation",
        "task_id": task["task_id"],
        "region_id": fact["region_id"],
        "producer_id": fact["producer_id"],
        "service_record": deepcopy(fact),
        "evidence_ref": result["receipt"]["evidence_refs"][0],
    }


def _observer(factor_id="communication_quality", factor_level="immediate", *,
              selected=("restaurant_carter_1",), callbacks=None):
    public = _public(factor_id, factor_level)
    host = _Host(public, selected)
    host.advance_to(10.0)
    runtime = _runtime()
    observer = GoalRestaurantInterventions(
        public, episode_id=EPISODE_ID, runtime=runtime,
        lifecycle_callbacks=callbacks)
    return public, host, runtime, observer


def _drain(host, observer):
    while observer.has_pending_work():
        wakeup = observer.next_wakeup_s()
        assert wakeup is not None
        host.advance_to(wakeup)
        observer.advance(host=host)


def _participant_observations(host):
    return [event for event in host.session.report()["events"]
            if event["type"] == "observation"]


def test_trigger_uses_successful_observation_facts_not_witness_ids_or_order():
    _public_task, host, runtime, observer = _observer(
        selected=("restaurant_carter_1", "restaurant_carter_2"))
    unrelated = _task(task_id="looks-like-a-witness", skill="relay")
    observer.after_skill(
        unrelated, {"status": "succeeded", "completed_at_s": 10.0}, host=host)
    failed = _task(task_id="restaurant_kitchen_inspection_observe")
    observer.after_skill(failed, _result(failed, status="failed"), host=host)
    assert observer.report()["trigger"]["status"] == "untaken"
    assert runtime.report()["messages"] == {}

    multi = _task(
        task_id="participant-chosen-node-93", region="dining_service",
        assigned=["restaurant_carter_2", "restaurant_carter_1"])
    observer.after_skill(
        multi, _result(multi, producer="restaurant_carter_2"), host=host)
    report = observer.report()
    assert report["trigger"]["status"] == "fired"
    assert report["trigger"]["task_id"] == "participant-chosen-node-93"
    assert report["trigger"]["region_id"] == "dining_service"
    assert report["trigger"]["producer_id"] == "restaurant_carter_2"
    assert len(runtime.report()["messages"]) == 1

    second = _task(task_id="another-observe", region="kitchen_service")
    observer.after_skill(second, _result(second), host=host)
    assert len(runtime.report()["messages"]) == 1


def test_successful_observation_requires_native_fact_and_receipt_binding():
    _public_task, host, _runtime_value, observer = _observer()
    task = _task()
    result = _result(task)
    del result["trusted_observation"]
    with pytest.raises(ValueError, match="trusted_observation"):
        observer.after_skill(task, result, host=host)
    assert observer.report()["trigger"]["status"] == "untaken"

    result = _result(task)
    result["trusted_observation"]["producer_id"] = "restaurant_iris_1"
    with pytest.raises(ValueError, match="outside the observing team"):
        observer.after_skill(task, result, host=host)
    assert observer.report()["trigger"]["status"] == "untaken"


@pytest.mark.parametrize("level,visible", [
    ("immediate", True),
    ("delayed", True),
    ("bandwidth_limited", True),
    ("dropped", False),
    ("stale", False),
])
def test_shared_transport_alone_controls_participant_visibility(level, visible):
    _public_task, host, runtime, observer = _observer(factor_level=level)
    task = _task()
    observer.after_skill(task, _result(task), host=host)
    assert observer.runtime is runtime
    assert _participant_observations(host) == []
    _drain(host, observer)

    observations = _participant_observations(host)
    assert bool(observations) is visible
    report = observer.report()
    message_id = report["delivery"]["message_id"]
    delivery = runtime.report()["messages"][message_id]["recipients"]["cloud"]
    if visible:
        assert len(observations) == 1
        payload = observations[0]["payload"]
        assert payload["kind"] == "communication_message"
        assert payload["data"]["recipient_id"] == "cloud"
        estimate = payload["data"]["estimate"]
        assert estimate["kind"] == "restaurant_service_observation"
        assert "scene_observation" not in estimate
        assert "visual" not in estimate
        assert report["delivery"]["delivered_at_s"] is not None
        assert report["delivery"]["emitted_at_s"] is not None
    else:
        assert delivery["status"] in {"dropped", "expired"}
        assert report["delivery"]["emitted_at_s"] is None
    assert report["measurement_valid"] is True
    assert report["intervention_valid"] is True
    assert report["errors"] == []


def test_delayed_message_is_invisible_until_actual_delivery():
    _public_task, host, _runtime_value, observer = _observer(
        factor_level="delayed")
    task = _task()
    observer.after_skill(task, _result(task), host=host)
    host.advance_to(14.99)
    observer.advance(host=host)
    assert _participant_observations(host) == []
    assert observer.has_pending_work() is True
    _drain(host, observer)
    assert len(_participant_observations(host)) == 1


def _lifecycle_callback(kind, calls):
    def apply(event, *, trusted_report):
        calls.append(deepcopy(event))
        state = deepcopy(trusted_report["session"]["runtime_state"])
        agent = event["agent"]
        if kind == "member_exit":
            state["robot_regions"].pop(agent)
        elif kind == "member_join":
            state["robot_regions"][agent] = "restaurant_staging"
        return {"runtime_state": state, "evidence": {"native_applied": True}}
    return apply


@pytest.mark.parametrize("level", ["member_exit", "capability_change"])
def test_team_factor_targets_selected_active_relay_member_once(level):
    calls = []
    callbacks = {level: _lifecycle_callback(level, calls)}
    _public_task, host, _runtime_value, observer = _observer(
        "team_continuity", level,
        selected=("restaurant_carter_2", "restaurant_carter_1"),
        callbacks=callbacks)
    task = _task(task_id="solo-ground-observation")
    observer.after_skill(task, _result(task), host=host)

    assert len(calls) == 1
    assert calls[0]["agent"] == "restaurant_carter_1"
    events = host.session.report()["events"]
    lifecycle = [event for event in events if event["type"] == level]
    assert len(lifecycle) == 1
    report = observer.report()
    assert report["lifecycle"]["status"] == "applied"
    assert report["lifecycle"]["agent_id"] == "restaurant_carter_1"
    assert host.session.report()["runtime_snapshot_refresh_required"] is False
    if level == "member_exit":
        assert "restaurant_carter_1" not in host.session.report()["current_agents"]
    else:
        declaration = host.session.report()["current_robot_declarations"][
            "restaurant_carter_1"]
        assert "relay" not in declaration["public_capabilities"]

    second = _task(task_id="second", region="dining_service")
    observer.after_skill(second, _result(second), host=host)
    assert len(calls) == 1


def test_member_join_without_explicit_native_callback_is_unsupported_not_ghost():
    _public_task, host, _runtime_value, observer = _observer(
        "team_continuity", "member_join",
        selected=("restaurant_carter_1",))
    before = host.session.report()["current_agents"]
    task = _task()
    observer.after_skill(task, _result(task), host=host)

    report = observer.report()
    assert report["trigger"]["status"] == "fired"
    assert report["trigger"]["intervention_status"] == "unsupported"
    assert report["lifecycle"]["status"] == "unsupported"
    assert report["lifecycle"]["failure_class"] == "infrastructure_failure"
    assert report["measurement_valid"] is False
    assert {row["code"] for row in report["errors"]} >= {
        "intervention_not_applied"}
    assert host.session.report()["current_agents"] == before
    assert not any(event["type"] == "member_join"
                   for event in host.session.report()["events"])


def test_member_join_callback_must_supply_real_post_mutation_snapshot():
    calls = []
    callback = _lifecycle_callback("member_join", calls)
    _public_task, host, _runtime_value, observer = _observer(
        "team_continuity", "member_join",
        selected=("restaurant_carter_1",),
        callbacks={"member_join": callback})
    task = _task()
    observer.after_skill(task, _result(task), host=host)

    joined = "restaurant_hidden_joiner_1"
    assert calls[0]["agent"] == joined
    session = host.session.report()
    assert joined in session["current_agents"]
    assert session["runtime_state"]["robot_regions"][joined] == "restaurant_staging"
    assert observer.report()["lifecycle"]["status"] == "applied"


def test_report_is_evaluator_only_and_untaken_is_not_an_intervention():
    public, _host, _runtime_value, observer = _observer()
    original_public = deepcopy(public)
    profile = observer.profile()
    report = observer.report()
    assert profile["visibility"] == report["visibility"] == "evaluator_only"
    assert profile["public_task_sha256"] == report["public_task_sha256"]
    assert report["trigger"] == {
        "status": "untaken",
        "reason": "no qualifying successful observation",
    }
    assert report["delivery"]["status"] == "not_published"
    assert report["intervention_valid"] is False
    assert report["measurement_valid"] is False
    assert report["errors"][0]["code"] == "trigger_untaken"
    assert report["scoreable"] is False
    assert report["release_eligible"] is False
    assert public == original_public


def test_constructor_rejects_a_transport_without_the_shared_uplink_pool():
    public = _public()
    with pytest.raises(ValueError, match="backhaul_uplink"):
        GoalRestaurantInterventions(
            public, episode_id=EPISODE_ID, runtime=CommunicationRuntime())


def test_independent_verifier_binds_first_native_fact_to_wire_payload():
    public, host, runtime, observer = _observer(factor_level="delayed")
    first = _task(task_id="arbitrary-first", region="dining_service")
    first_result = _result(first)
    observer.after_skill(first, first_result, host=host)
    _drain(host, observer)
    later = _task(task_id="arbitrary-later", region="kitchen_service")
    later_result = _result(later, now=12.0)
    verification = verify_restaurant_intervention_report(
        observer.report(), public_task=public, episode_id=EPISODE_ID,
        transport_report=runtime.report(),
        native_observations=[
            _native_observation(later, later_result),
            _native_observation(first, first_result),
        ])
    assert verification["validated"] is True
    assert verification["measurement_valid"] is True
    assert verification["native_observation_id"] == \
        first_result["trusted_observation"]["observation_id"]


def test_independent_verifier_rejects_self_consistent_report_payload_tamper():
    public, host, runtime, observer = _observer()
    task = _task()
    result = _result(task)
    observer.after_skill(task, result, host=host)
    _drain(host, observer)
    report = observer.report()
    message_id = report["delivery"]["message_id"]
    transport = runtime.report()
    transport["messages"][message_id]["payload"]["estimate"][
        "region_id"] = "dining_service"
    with pytest.raises(ValueError, match="transport report failed replay|payload differs"):
        verify_restaurant_intervention_report(
            report, public_task=public, episode_id=EPISODE_ID,
            transport_report=transport,
            native_observations=[_native_observation(task, result)])


def test_independent_verifier_keeps_unsupported_join_measurement_invalid():
    public, host, runtime, observer = _observer(
        "team_continuity", "member_join",
        selected=("restaurant_carter_1",))
    task = _task()
    result = _result(task)
    observer.after_skill(task, result, host=host)
    _drain(host, observer)
    verification = verify_restaurant_intervention_report(
        observer.report(), public_task=public, episode_id=EPISODE_ID,
        transport_report=runtime.report(),
        native_observations=[_native_observation(task, result)])
    assert verification["validated"] is True
    assert verification["measurement_valid"] is False


def test_historical_fresh_emission_remains_valid_after_transport_ttl():
    public, host, runtime, observer = _observer()
    task = _task()
    result = _result(task)
    observer.after_skill(task, result, host=host)
    _drain(host, observer)
    assert observer.report()["delivery"]["status"] == "delivered"

    host.advance_to(100.0)
    observer.advance(host=host)
    final_transport = runtime.report()
    message_id = observer.report()["delivery"]["message_id"]
    assert final_transport["messages"][message_id]["recipients"]["cloud"][
        "status"] == "expired"
    verification = verify_restaurant_intervention_report(
        observer.report(), public_task=public, episode_id=EPISODE_ID,
        transport_report=final_transport,
        native_observations=[_native_observation(task, result)])
    assert verification["validated"] is True
    assert verification["measurement_valid"] is True


def test_delivery_that_expires_before_observer_pump_is_not_visible_or_valid():
    public, host, runtime, observer = _observer()
    task = _task()
    result = _result(task)
    observer.after_skill(task, result, host=host)
    # A synchronous participant may continue physical work without yielding at
    # the delivery instant. Advancing beyond TTL must not retroactively expose it.
    host.advance_to(100.0)
    observer.advance(host=host)
    report = observer.report()
    assert _participant_observations(host) == []
    assert report["delivery"]["status"] == "expired"
    assert report["measurement_valid"] is False
    verification = verify_restaurant_intervention_report(
        report, public_task=public, episode_id=EPISODE_ID,
        transport_report=runtime.report(),
        native_observations=[_native_observation(task, result)])
    assert verification["validated"] is True
    assert verification["measurement_valid"] is False
