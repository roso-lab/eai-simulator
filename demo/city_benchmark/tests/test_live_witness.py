"""Focused live-witness checks; no Isaac, CUDA, ROS or reference backend."""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from demo.city_benchmark.episode_adapter import CityEpisodeEvidenceHost
from demo.city_benchmark.live_witness import (
    CityDemoRuntimeWitnessAdapter,
    CityIncidentReceiver,
    CityLiveEpisodeWitness,
    CityRuntimeWitnessBlocked,
    LiveEvidence,
    assess_city_runtime,
    bind_city_runtime,
)
from demo.city_benchmark.native_runner import CityNativeServiceRunner


def evidence(episode_id, ref, kind, entities, time_s, before="pending", after="applied"):
    return LiveEvidence(
        episode_id=episode_id, evidence_ref=ref, kind=kind,
        source_id="isaac.city.service_endpoint",
        entity_ids=tuple(entities), observed_time_s=time_s,
        status_before={"status": before}, status_after={"status": after},
    )


def make_host():
    host = CityEpisodeEvidenceHost(
        admission=lambda *args: True, episode_id="ep-1",
        source_id="isaac.city.service_endpoint")
    host.advance(10.0)
    return host


def test_current_city_runtime_shape_is_fail_closed():
    runtime = SimpleNamespace(
        people=SimpleNamespace(actor_ids=("person_00",)),
        traffic=[("pose",)],
    )
    assessment = assess_city_runtime(runtime)
    assert not assessment.ready
    codes = {blocker["code"] for blocker in assessment.blockers}
    assert {
        "episode_identity_missing", "service_witness_host_missing",
        "people_source_is_background_only", "vehicle_source_is_scene_pose_only",
        "incident_receipt_endpoint_missing",
    } <= codes
    with pytest.raises(CityRuntimeWitnessBlocked) as exc:
        bind_city_runtime(runtime)
    assert exc.value.assessment.as_dict()["scoreable"] is False


def test_live_entrypoint_rejects_reference_sources_and_forged_status():
    with pytest.raises(ValueError, match="reference"):
        LiveEvidence("ep-1", "ref", "service_ack", "reference", ("person_00",), 1.0,
                     {"status": "pending"}, {"status": "applied"})
    with pytest.raises(ValueError, match="status change"):
        evidence("ep-1", "ref", "service_ack", ("person_00",), 1.0,
                 before="ack", after="ack")


def test_live_episode_wires_people_ack_arrival_as_runtime_witness():
    host = make_host()
    runtime = SimpleNamespace(episode_id="ep-1", city_service_witness=host)
    witness = bind_city_runtime(runtime)
    observation = evidence("ep-1", "obs", "observation", ("person_00",), 8.0,
                           before="hidden", after="delivered")
    witness.deliver_observation({
        "id": "obs", "producer_agent_id": "iris_1", "region_id": "road_surface",
        "captured_at_s": 8.0, "delivered_at_s": 9.0, "valid_until_s": 30.0,
        "entities": [{"id": "person_00", "kind": "person"}],
    }, observation)
    for role in ("guidance_contact",):
        host.record_role_occupancy("go2_1", role, "road_surface",
                                   start_time_s=8.0, end_time_s=10.0)
    request = witness.guide_people(
        agent_id="go2_1", region_id="road_surface", assembly_region_id="assembly_1",
        person_ids=("person_00",), observation_refs=("obs",))
    host.advance(12.0)
    host.record_role_occupancy("go2_1", "guidance_contact", "road_surface",
                               start_time_s=10.0, end_time_s=12.0)
    witness.record_actor_ack(
        request["request_id"], "person_00", time_s=12.0,
        evidence=evidence("ep-1", "ack-1", "service_ack", ("person_00",), 12.0))
    host.advance(18.0)
    witness.record_person_readback(
        request["request_id"], "person_00", arrival_time_s=18.0,
        assembly_region_id="assembly_1",
        trajectory_evidence=evidence("ep-1", "pose-1", "person_world_readback", ("person_00",), 18.0,
                                     before="en_route", after="arrived"),
        safety_evidence=evidence("ep-1", "safe-1", "person_world_readback", ("person_00",), 18.0,
                                 before="uncleared", after="clear"),
    )
    result = witness.review(request["request_id"])
    assert result["status"] == "evidence_complete"
    assert result["evidence_class"] == "runtime"
    assert result["live_witness"] is True
    assert result["scoreable"] is False


def test_live_guidance_serializes_same_tick_capacity_claims():
    host = CityEpisodeEvidenceHost(
        admission=lambda service, ids, *_: service != "guide_people" or len(set(ids)) <= 1,
        episode_id="ep-1", source_id="isaac.city.service_endpoint")
    host.advance(10.0)
    witness = CityLiveEpisodeWitness(host, episode_id="ep-1")
    witness.deliver_observation({
        "id": "obs", "producer_agent_id": "iris_1", "region_id": "road_surface",
        "captured_at_s": 8.0, "delivered_at_s": 9.0, "valid_until_s": 30.0,
        "entities": [{"id": "p1", "kind": "person"}, {"id": "p2", "kind": "person"}],
    }, evidence("ep-1", "obs", "observation", ("p1", "p2"), 8.0,
                before="hidden", after="delivered"))
    host.record_role_occupancy("go2_1", "guidance_contact", "road_surface",
                               start_time_s=8.0, end_time_s=10.0)

    def submit(person_id):
        return witness.guide_people(
            agent_id="go2_1", region_id="road_surface", assembly_region_id="assembly_1",
            person_ids=(person_id,), observation_refs=("obs",))

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda person: (person, _attempt(submit, person)), ("p1", "p2")))
    accepted = [row for _, row in outcomes if isinstance(row, dict)]
    rejected = [row for _, row in outcomes if isinstance(row, ValueError)]
    assert len(accepted) == len(rejected) == 1
    assert "not admitted" in str(rejected[0])
    assert host.request_ids() == (accepted[0]["request_id"],)
    assert host.admit("guide_people", ("p1",), "road_surface", "assembly_2", None, 10.) is False


def test_rejected_live_registration_leaves_no_backend_request():
    host = CityEpisodeEvidenceHost(
        admission=lambda *args: True, episode_id="ep-1",
        source_id="isaac.city.service_endpoint", require_episode_rosters=True)
    host.advance(10.0)
    witness = CityLiveEpisodeWitness(host, episode_id="ep-1")
    witness.deliver_observation({
        "id": "obs", "producer_agent_id": "iris_1", "region_id": "road_surface",
        "captured_at_s": 8.0, "delivered_at_s": 9.0, "valid_until_s": 30.0,
        "entities": [{"id": "p1", "kind": "person"}],
    }, evidence("ep-1", "obs", "observation", ("p1",), 8.0,
                before="hidden", after="delivered"))
    host.record_role_occupancy("go2_1", "guidance_contact", "road_surface",
                               start_time_s=8.0, end_time_s=10.0)
    arguments = {"agent_id": "go2_1", "region_id": "road_surface",
                 "assembly_region_id": "assembly_1", "person_ids": ("p1",),
                 "observation_refs": ("obs",)}
    with pytest.raises(ValueError, match="missing episode-owned person roster"):
        witness.guide_people(**arguments)
    assert host.request_ids() == () and witness.backend._requests == {}
    host.bind_affected_person_roster({"p1": {
        "id": "p1", "kind": "person", "episode_id": "ep-1",
        "source_id": "isaac.city.service_endpoint"}})
    assert witness.guide_people(**arguments)["request_id"] == "city_service_0001"


def test_native_controller_identity_is_checked_before_ack():
    adapter = CityDemoRuntimeWitnessAdapter.__new__(CityDemoRuntimeWitnessAdapter)
    adapter.episode_id = "ep-1"
    adapter.source_id = "isaac.city.runtime"
    adapter.person_stop = None
    adapter.vehicle_stop = None
    adapter.now_s = lambda: 10.
    adapter.guide_person = lambda _: {
        "entity_id": "other", "episode_id": "ep-1", "source_id": "isaac.city.runtime",
        "status_before": "waiting", "status_after": "guidance_applied"}
    adapter.control_vehicle = lambda _, mode: {
        "vehicle_id": "other", "episode_id": "ep-1", "source_id": "isaac.city.runtime",
        "status_before": "flowing", "status_after": mode,
        "observed_time_s": 10., "evidence_ref": "wrong-controller"}
    acknowledged = []
    witness = SimpleNamespace(
        guide_people=lambda **_: {"request_id": "guidance"},
        control_following_traffic=lambda **_: {"request_id": "traffic"},
        record_actor_ack=lambda *args, **kwargs: acknowledged.append((args, kwargs)),
        record_partial_failure=lambda *args, **kwargs: None)
    with pytest.raises(CityRuntimeWitnessBlocked,
                       match="guidance request guidance partially failed at ack") as person_error:
        adapter.submit_guidance(witness, agent_id="go2_1", region_id="road_surface",
                                assembly_region_id="assembly_1", person_ids=("p1",),
                                observation_refs=("obs",))
    assert "person controller acknowledgement identity mismatch" in str(person_error.value.__cause__)
    with pytest.raises(CityRuntimeWitnessBlocked,
                       match="traffic request traffic partially failed at ack") as vehicle_error:
        adapter.submit_traffic(witness, agent_id="go2_1", region_id="road_surface",
                               vehicle_ids=("v1",), mode="hold", observation_refs=("obs",))
    assert "vehicle controller acknowledgement identity mismatch" in str(vehicle_error.value.__cause__)
    assert acknowledged == []


@pytest.mark.parametrize("service", ("guide_people", "control_traffic"))
@pytest.mark.parametrize("failure", ("controller", "ack", "stop"))
def test_native_partial_batch_stops_and_cannot_promote(service, failure):
    host = make_host()
    ids = ("p1", "p2") if service == "guide_people" else ("v1", "v2")
    kind = "person" if service == "guide_people" else "vehicle"
    witness = CityLiveEpisodeWitness(host, episode_id="ep-1")
    witness.deliver_observation({
        "id": "obs", "producer_agent_id": "iris_1", "region_id": "road_surface",
        "captured_at_s": 8., "delivered_at_s": 9., "valid_until_s": 30.,
        "entities": [{"id": entity_id, "kind": kind,
                      "following_incident": kind == "vehicle",
                      "is_accident_vehicle": False} for entity_id in ids],
    }, evidence("ep-1", "obs", "observation", ids, 8.,
                before="hidden", after="delivered"))
    role = "guidance_contact" if kind == "person" else "traffic_control_point"
    host.record_role_occupancy("go2_1", role, "road_surface",
                               start_time_s=8., end_time_s=10.)
    calls = []

    def control(entity_id, mode=None):
        calls.append(("control", entity_id))
        if entity_id == ids[1] and failure == "controller":
            raise RuntimeError("control failed after possible actuation")
        return ({"entity_id": entity_id, "status_before": "waiting",
                 "status_after": "guidance_applied", "episode_id": "ep-1",
                 "source_id": "isaac.city.service_endpoint"} if kind == "person" else
                {"vehicle_id": entity_id, "status_before": "flowing",
                 "status_after": "hold", "observed_time_s": 10.,
                 "evidence_ref": f"ack:{entity_id}", "episode_id": "ep-1",
                 "source_id": "isaac.city.service_endpoint"})

    def stop(entity_id):
        calls.append(("stop", entity_id))
        if entity_id == ids[0] and failure == "stop":
            raise RuntimeError("stop endpoint failed")
        return {"entity_id" if kind == "person" else "vehicle_id": entity_id,
                "status_before": "guidance_applied" if kind == "person" else "hold",
                "status_after": "paused" if kind == "person" else "hold",
                "position_xyz": [1., 2., 0.], "observed_time_s": 10.,
                "evidence_ref": f"stop:{entity_id}", "episode_id": "ep-1",
                "source_id": "isaac.city.service_endpoint"}

    adapter = CityDemoRuntimeWitnessAdapter(
        SimpleNamespace(elapsed=10., people=SimpleNamespace(
            actor_ids=ids, play_action=lambda *args, **kwargs: None,
            set_path_speed=lambda *args: None)),
        episode_id="ep-1", source_id="isaac.city.service_endpoint",
        affected_people={person_id: {"speed_mps": 1.} for person_id in ids},
        person_controller=control if kind == "person" else None,
        vehicle_controller=control if kind == "vehicle" else None,
        person_stop=stop if kind == "person" else None,
        vehicle_stop=stop if kind == "vehicle" else None)
    if failure == "ack":
        original = witness.record_actor_ack

        def reject_second(request_id, entity_id, **kwargs):
            if entity_id == ids[1]:
                raise ValueError("endpoint ack failed")
            return original(request_id, entity_id, **kwargs)

        witness.record_actor_ack = reject_second
    if failure == "stop":
        # The second actor fails to ACK, then the first stop fails.
        original = witness.record_actor_ack

        def reject_second(request_id, entity_id, **kwargs):
            if entity_id == ids[1]:
                raise ValueError("endpoint ack failed")
            return original(request_id, entity_id, **kwargs)

        witness.record_actor_ack = reject_second
    kwargs = {"agent_id": "go2_1", "region_id": "road_surface",
              "observation_refs": ("obs",)}
    with pytest.raises(CityRuntimeWitnessBlocked, match="partially failed"):
        if kind == "person":
            adapter.submit_guidance(witness, assembly_region_id="assembly_1",
                                    person_ids=ids, **kwargs)
        else:
            adapter.submit_traffic(witness, vehicle_ids=ids, mode="hold", **kwargs)
    assert calls == [("control", ids[0]), ("control", ids[1]),
                     ("stop", ids[1]), ("stop", ids[0])]
    request_id, = host.request_ids()
    review = witness.review(request_id)
    assert review["status"] == "partial_failed"
    assert review["live_witness"] is False and review["scoreable"] is False
    partial = review["evidence"]["partial_failure"]
    assert partial["failed_actor_id"] == ids[1]
    assert partial["attempted_ids"] == list(ids)
    assert partial["failure_stage"] == ("controller" if failure == "controller" else "ack")
    assert [row["status"] for row in partial["stops"]] == (
        ["stopped", "stop_unconfirmed"] if failure == "stop" else ["stopped", "stopped"])
    assert partial["all_stopped"] is (failure != "stop")
    assert all("evidence_ref" not in row for row in partial["stops"] if row["status"] != "stopped")
    assert witness.review(request_id) == review
    if kind == "person":
        assert witness.backend.guidance_contact(request_id)["status"] == "partial_failed"


def _attempt(submit, person_id):
    try:
        return submit(person_id)
    except ValueError as exc:
        return exc


def test_live_incident_receipt_requires_runtime_receipt_and_access_readback():
    host = make_host()
    runtime = SimpleNamespace(episode_id="ep-1", city_service_witness=host)
    witness = CityLiveEpisodeWitness.from_runtime(runtime)
    observation = evidence("ep-1", "obs", "observation", ("vehicle_00",), 8.0,
                           before="unseen", after="observed")
    witness.deliver_observation({
        "id": "obs", "producer_agent_id": "iris_1", "region_id": "road_surface",
        "captured_at_s": 8.0, "delivered_at_s": 9.0, "valid_until_s": 30.0,
        "entities": [{"id": "vehicle_00", "kind": "vehicle",
                      "following_incident": True, "is_accident_vehicle": False}],
    }, observation)
    host.record_role_occupancy("go2_1", "incident_observer", "road_surface",
                               start_time_s=8.0, end_time_s=10.0)
    request = witness.report_incident(
        agent_id="go2_1", region_id="road_surface", observation_refs=("obs",),
        recipient_service_id="city_dispatch")
    host.advance(18.0)
    witness.record_receipt(
        request["request_id"], recipient_service_id="city_dispatch", observation_refs=["obs"],
        received_time_s=18.0,
        evidence=evidence("ep-1", "receipt-1", "service_receipt", (), 18.0,
                          before="queued", after="received"))
    host.advance(20.0)
    witness.record_access_readback(
        request["request_id"], start_time_s=17.0, end_time_s=20.0,
        evidence=evidence("ep-1", "access-1", "access_readback", (), 20.0,
                          before="blocked", after="clear"))
    result = witness.review(request["request_id"])
    assert result["live_witness"] is True
    assert result["evidence"]["receipt"]["receipt_evidence_ref"] == "receipt-1"


def test_live_traffic_path_requires_entity_bound_world_readback():
    host = make_host()
    runtime = SimpleNamespace(episode_id="ep-1", city_service_witness=host)
    witness = CityLiveEpisodeWitness.from_runtime(runtime)
    witness.deliver_observation({
        "id": "obs", "producer_agent_id": "iris_1", "region_id": "road_surface",
        "captured_at_s": 8.0, "delivered_at_s": 9.0, "valid_until_s": 30.0,
        "entities": [{"id": "vehicle_00", "kind": "vehicle",
                      "following_incident": True, "is_accident_vehicle": False}],
    }, evidence("ep-1", "obs", "observation", ("vehicle_00",), 8.0,
                 before="unseen", after="observed"))
    host.record_role_occupancy("go2_1", "traffic_control_point", "road_surface",
                               start_time_s=8.0, end_time_s=10.0)
    request = witness.control_following_traffic(
        agent_id="go2_1", region_id="road_surface", vehicle_ids=("vehicle_00",),
        mode="hold", observation_refs=("obs",))
    host.advance(12.0)
    host.record_role_occupancy("go2_1", "traffic_control_point", "road_surface",
                               start_time_s=10.0, end_time_s=12.0)
    witness.record_actor_ack(
        request["request_id"], "vehicle_00", time_s=12.0, applied_mode="hold",
        evidence=evidence("ep-1", "ack-v", "service_ack", ("vehicle_00",), 12.0))
    host.advance(18.0)
    with pytest.raises(ValueError, match="bind an entity"):
        witness.record_traffic_readback(
            request["request_id"], "vehicle_00", trajectory_check_time_s=18.0,
            trajectory_evidence=evidence("ep-1", "traj", "traffic_world_readback", (), 18.0),
            hazard_evidence=evidence("ep-1", "haz", "traffic_world_readback", ("vehicle_00",), 18.0),
            access_evidence=evidence("ep-1", "access", "traffic_world_readback", ("vehicle_00",), 18.0),
        )


def test_live_readback_rejects_wrong_entity_and_duplicate_refs_without_partial_acceptance():
    host = make_host()
    witness = CityLiveEpisodeWitness(host, episode_id="ep-1")
    wrong = evidence("ep-1", "wrong", "service_ack", ("vehicle_other",), 10.0)
    with pytest.raises(ValueError, match="every observed entity"):
        witness.record_actor_ack("missing", "vehicle_00", time_s=10.0, evidence=wrong)

    duplicate_a = evidence(
        "ep-1", "same", "traffic_world_readback", ("vehicle_00",), 10.0,
        before="flowing", after="checked")
    duplicate_b = evidence(
        "ep-1", "same", "traffic_world_readback", ("vehicle_00",), 10.0,
        before="unknown", after="clear")
    with pytest.raises(ValueError, match="distinct evidence"):
        witness._accept_batch((
            (duplicate_a, "traffic_world_readback", ("vehicle_00",), None),
            (duplicate_b, "traffic_world_readback", ("vehicle_00",), None),
        ))
    witness._accept(duplicate_a, "traffic_world_readback", ("vehicle_00",))


class FakePeople:
    actor_ids = ("person_00",)

    def __init__(self):
        self.positions = {"person_00": (1.0, 2.0, 0.3)}
        self.actions = []

    def position(self, actor_id):
        return self.positions[actor_id]

    def play_action(self, actor_id, motion, *, loop, playback_speed):
        self.actions.append(("play_action", actor_id, motion, loop, playback_speed))

    def set_path_speed(self, actor_id, speed):
        self.actions.append(("set_path_speed", actor_id, speed))


class FakeAttr:
    def __init__(self, value):
        self.value = value

    def Get(self):
        return self.value


def test_city_runtime_wrapper_reads_actual_poses_drives_human_and_exposes_blockers():
    people = FakePeople()
    runtime = SimpleNamespace(elapsed=4.0, people=people, traffic=[(FakeAttr((3.0, 4.0, 0.2)),)])
    receiver = CityIncidentReceiver(episode_id="ep-1", service_id="city_dispatch")
    adapter = CityDemoRuntimeWitnessAdapter(
        runtime, episode_id="ep-1", affected_people={"person_00": {"speed_mps": 1.8}},
        incident_receiver=receiver)
    assert adapter.observe_people()[0]["position_xyz"] == [1.0, 2.0, 0.3]
    assert adapter.observe_vehicles()[0]["id"] == "following_vehicle_00"
    action = adapter.guide_person("person_00")
    assert action["status_after"] == "guidance_applied"
    assert people.actions[0][0] == "play_action"
    readiness = adapter.readiness()
    assert readiness["status"] == "blocked"
    assert "missing_live_capability:following_vehicle_source" in readiness["blockers"]
    assert "missing_live_capability:dynamic_hazard_and_assembly_access" in readiness["blockers"]
    assert "scene_query_support_disabled_or_not_verified" in readiness["blockers"]
    assert "dynamic_clearance_unknown_under_direct_gpu" in readiness["blockers"]
    assert CityDemoRuntimeWitnessAdapter.scene_query_requirement()["required_value"] is True


def test_city_runtime_wrapper_records_bounded_actual_human_trace():
    people = FakePeople()

    class Runtime:
        elapsed = 0.0
        traffic = []

        def __init__(self):
            self.people = people

        def step(self, *, dt):
            self.elapsed += dt
            x, y, z = people.positions["person_00"]
            people.positions["person_00"] = (x + dt, y, z)

    adapter = CityDemoRuntimeWitnessAdapter(Runtime(), episode_id="ep-1",
                                             affected_people={"person_00": {"speed_mps": 1.8}})
    trace = adapter.bounded_person_trace("person_00", duration_s=0.3, dt_s=0.1)
    assert len(trace) == 3
    assert trace[-1]["position_xyz"][0] > trace[0]["position_xyz"][0]
    assert trace[-1]["time_s"] == pytest.approx(0.3)


def test_runtime_roster_requires_explicit_episode_and_source_provenance():
    people = FakePeople()
    adapter = CityDemoRuntimeWitnessAdapter(
        SimpleNamespace(elapsed=1.0, people=people, traffic=[]), episode_id="ep-1",
        affected_person_roster=lambda: {
            "person_00": {"id": "person_00", "kind": "person", "speed_mps": 1.0}},
        following_vehicle_roster=lambda: {
            "vehicle_00": {"id": "vehicle_00", "kind": "vehicle",
                           "following_incident": True, "is_accident_vehicle": False}})
    with pytest.raises(CityRuntimeWitnessBlocked, match="provenance mismatch"):
        adapter.episode_rosters()


def test_incident_receiver_validates_payload_and_owns_receipt_state():
    receiver = CityIncidentReceiver(episode_id="ep-1", service_id="city_dispatch")
    payload = {"request_id": "req-1", "episode_id": "ep-1",
               "recipient_service_id": "city_dispatch", "observation_refs": ["obs-1"],
               "submitted_time_s": 10.0}
    receipt = receiver.receive(payload, now_s=11.0)
    assert receipt["status"] == "received"
    assert receiver.receipt("req-1")["receipt_evidence_ref"] == receipt["receipt_evidence_ref"]
    assert receiver.receive(payload, now_s=12.0) == receipt
    with pytest.raises(ValueError, match="recipient"):
        receiver.receive({**payload, "recipient_service_id": "other"}, now_s=11.0)


def test_native_runner_traces_real_actor_and_receiver_request_without_promoting_success():
    people = FakePeople()

    class Runtime:
        elapsed = 4.0
        traffic = []

        def __init__(self):
            self.people = people

        def step(self, *, dt):
            self.elapsed += dt
            x, y, z = people.positions["person_00"]
            people.positions["person_00"] = (x + dt, y, z)

    receiver = CityIncidentReceiver(episode_id="ep-1", service_id="city_dispatch")
    runtime = Runtime()
    runner = CityNativeServiceRunner(
        runtime, episode_id="ep-1", agent_id="go2_1", region_id="road_surface",
        assembly_region_id="assembly_1", recipient_service_id="city_dispatch",
        affected_people={"person_00": {"speed_mps": 1.8}},
        admission=lambda *args: True, role_source_id="isaac.field_role",
        admission_source_id="isaac.scene_query", incident_receiver=receiver,
        scene_query_support=True,
        clearance_query_provenance="isaac.scene_query.episode_timestamped",
        physx_readback_suppressed=False)
    with pytest.raises(ValueError, match="already has an episode service runner"):
        CityNativeServiceRunner(
            runtime, episode_id="ep-1", agent_id="go2_1", region_id="road_surface",
            assembly_region_id="assembly_1", recipient_service_id="city_dispatch")
    assert runner.readiness["status"] == "blocked"
    assert "missing_live_capability:following_vehicle_source" in runner.readiness["blockers"]
    observation_ref = runner.observe()
    runner.record_role("guidance_contact", start_time_s=3.0, end_time_s=4.0)
    guided = runner.guide_people(("person_00",), observation_ref=observation_ref)
    assert guided["actions"][0]["action"]["status_after"] == "guidance_applied"
    runner.record_role("incident_observer", start_time_s=3.0, end_time_s=4.0)
    report = runner.report_incident(observation_ref=observation_ref)
    assert report["receipt"]["status"] == "received"
    assert runner.review(report["request"]["request_id"])["status"] == "pending"
    trace = runner.bounded_people_trace("person_00", duration_s=0.2, dt_s=0.1)
    assert len(trace) == 2


def test_full_native_binding_uses_roster_specs_vehicle_controller_and_world_readbacks():
    people = FakePeople()
    vehicle_positions = {"vehicle_00": (5.0, 2.0, 0.2)}
    controller_calls = []

    class Runtime:
        elapsed = 4.0
        traffic = []
        episode_id = "ep-1"

        def __init__(self):
            self.people = people

        def step(self, *, dt):
            self.elapsed += dt

        def city_service_native_binding(self):
            return {
                "episode_id": self.episode_id, "source_id": "isaac.city.runtime",
                "affected_person_roster": lambda: {
                    "person_00": {"id": "person_00", "kind": "person",
                                  "episode_id": "ep-1", "source_id": "isaac.city.runtime",
                                  "speed_mps": 1.4, "motion": "walk"}},
                "following_vehicle_roster": lambda: {
                    "vehicle_00": {"id": "vehicle_00", "kind": "vehicle",
                                   "episode_id": "ep-1", "source_id": "isaac.city.runtime",
                                   "following_incident": True, "is_accident_vehicle": False}},
                "person_controller": self.control_person,
                "person_stop": lambda person_id: {
                    "entity_id": person_id, "episode_id": "ep-1",
                    "source_id": "isaac.city.runtime", "status_before": "guidance_applied",
                    "status_after": "paused", "position_xyz": [1., 2., .3],
                    "observed_time_s": self.elapsed, "evidence_ref": f"stop:{person_id}"},
                "person_readback": lambda person_id: {},
                "vehicle_controller": self.control_vehicle,
                "vehicle_stop": lambda vehicle_id: self.control_vehicle(vehicle_id, "hold"),
                "vehicle_pose_reader": lambda vehicle_id: vehicle_positions[vehicle_id],
                "hazard_readback": lambda vehicle_id, now: {
                    "no_hazard_ingress": True, "rescue_access_preserved": True,
                    "hazard_evidence_ref": f"hazard:{vehicle_id}:{now}",
                    "access_evidence_ref": f"access:{vehicle_id}:{now}"},
                "access_readback": lambda request, start, end: {
                    "measured_clear_throughout": True,
                    "rescue_and_evacuation_routes_admitted": True,
                    "evidence_ref": f"continuous:{start}:{end}"},
                "admission": lambda *args: True,
                "incident_receiver": CityIncidentReceiver(
                    episode_id="ep-1", service_id="city_dispatch"),
                }

        def control_person(self, person_id):
            people.actions.append(("set_path_speed", person_id, 1.4))
            return {"entity_id": person_id, "status_before": "awaiting_guidance",
                    "status_after": "guidance_applied", "position_xyz": [1.0, 2.0, 0.3],
                    "source_id": "isaac.city.runtime", "episode_id": "ep-1"}

        def control_vehicle(self, vehicle_id, mode):
            controller_calls.append((vehicle_id, mode))
            return {"vehicle_id": vehicle_id, "episode_id": "ep-1",
                    "source_id": "isaac.city.runtime",
                    "status_before": "flowing", "status_after": mode,
                    "observed_time_s": self.elapsed,
                    "evidence_ref": f"controller:{vehicle_id}:{mode}"}

    runtime = Runtime()
    runner = CityNativeServiceRunner(
        runtime, episode_id="ep-1", agent_id="go2_1", region_id="road_surface",
        assembly_region_id="assembly_1", recipient_service_id="city_dispatch",
        role_source_id="isaac.field_role", admission_source_id="isaac.scene_query",
        scene_query_support=True,
        clearance_query_provenance="isaac.scene_query.episode_timestamped",
        physx_readback_suppressed=False)
    assert runner.readiness["status"] == "ready"
    observation_ref = runner.observe()
    runner.record_role("guidance_contact", start_time_s=3.0, end_time_s=4.0)
    guided = runner.guide_people(("person_00",), observation_ref=observation_ref)
    assert guided["actions"][0]["action"]["status_after"] == "guidance_applied"
    assert people.actions[-1] == ("set_path_speed", "person_00", 1.4)
    runner.record_role("traffic_control_point", start_time_s=3.0, end_time_s=4.0)
    controlled = runner.control_following_traffic(
        ("vehicle_00",), "hold", observation_ref=observation_ref)
    assert controller_calls == [("vehicle_00", "hold")]
    assert controlled["actions"][0]["evidence"]["kind"] == "service_ack"
    runtime.elapsed = 9.0
    vehicle_positions["vehicle_00"] = (5.5, 2.0, 0.2)
    runner.host.advance(9.0)
    readback = runner.record_vehicle_readback(
        controlled["request"]["request_id"], "vehicle_00",
        previous_position=(5.0, 2.0, 0.2), status_before="hold")
    refs = {readback[key].evidence_ref for key in
            ("trajectory_evidence", "hazard_evidence", "access_evidence")}
    assert len(refs) == 3
    assert runner.review(controlled["request"]["request_id"])["status"] == "evidence_complete"
