"""Offline episode-host conformance; no Isaac or claimed physical success."""
from copy import deepcopy

import pytest

from demo.city_benchmark.episode_adapter import CityEpisodeEvidenceHost
from demo.city_benchmark.episode_adapter import CityEpisodeServiceWitness
from demo.city_benchmark.live_witness import CityDemoRuntimeWitnessAdapter
from demo.city_benchmark.service_backend import CityServiceBackend


@pytest.fixture
def episode():
    host = CityEpisodeEvidenceHost(admission=lambda *args: True)
    host.advance(10.0)
    host.deliver_observation({
        "id": "obs", "producer_agent_id": "iris_1", "region_id": "road_surface",
        "captured_at_s": 8.0, "delivered_at_s": 9.0, "valid_until_s": 30.0,
        "entities": [{"id": "p1", "kind": "person"},
                     {"id": "v1", "kind": "vehicle", "following_incident": True,
                      "is_accident_vehicle": False}],
    })
    host.record_role_occupancy("go2_1", "guidance_contact", "road_surface",
                               start_time_s=8.0, end_time_s=10.0)
    host.record_role_occupancy("go2_1", "traffic_control_point", "road_surface",
                               start_time_s=8.0, end_time_s=10.0)
    host.record_role_occupancy("go2_1", "incident_observer", "road_surface",
                               start_time_s=8.0, end_time_s=10.0)
    return host, CityServiceBackend(host.as_source())


def _guide(host, backend):
    request = backend.guide_people(agent_id="go2_1", region_id="road_surface",
                                   assembly_region_id="assembly_1", person_ids=("p1",),
                                   observation_refs=("obs",))
    host.register_request(request, evidence_class="runtime")
    return request["request_id"]


def test_host_never_invents_approval_or_physical_evidence(episode):
    host, backend = episode
    request_id = _guide(host, backend)
    assert host.service_update(request_id) is None
    assert backend.review(request_id)["status"] == "pending"
    host.advance(12.0)
    host.record_role_occupancy("go2_1", "guidance_contact", "road_surface",
                               start_time_s=10.0, end_time_s=12.0)
    host.record_actor_ack(request_id, "p1", time_s=12.0, evidence_ref="guide_endpoint_1")
    assert backend.review(request_id)["status"] == "pending"
    host.advance(18.0)
    host.record_person_readback(request_id, "p1", arrival_time_s=18.0,
                                assembly_region_id="assembly_1", measured_trajectory=True,
                                safe_route_and_assembly=True, trajectory_ref="human_pose_1",
                                safety_ref="assembly_clear_1")
    result = backend.review(request_id)
    assert result["status"] == "evidence_complete" and result["scoreable"] is False
    assert result["evidence"]["actors"][0]["trajectory_evidence_ref"] == "human_pose_1"
    result["evidence"]["actors"][0]["id"] = "injected"
    assert host.service_update(request_id)["actors"][0]["id"] == "p1"


def test_live_roster_rejects_forged_episode_and_source_provenance():
    host = CityEpisodeEvidenceHost(
        admission=lambda *args: True, episode_id="episode", source_id="isaac.city",
        require_episode_rosters=True)
    valid = {"id": "p1", "kind": "person", "episode_id": "episode",
             "source_id": "isaac.city"}
    for changed, message in (({"episode_id": "other"}, "episode provenance"),
                             ({"source_id": "participant"}, "source provenance")):
        with pytest.raises(ValueError, match=message):
            host.bind_affected_person_roster({"p1": {**valid, **changed}})
    host.bind_affected_person_roster({"p1": valid})
    assert host.roster_ids("person") == ("p1",)


def test_traffic_needs_endpoint_ack_and_world_trajectory_after_reaction_window(episode):
    host, backend = episode
    request = backend.control_following_traffic(agent_id="go2_1", region_id="road_surface",
                                                vehicle_ids=("v1",), mode="hold", observation_refs=("obs",))
    host.register_request(request, evidence_class="runtime")
    request_id = request["request_id"]
    with pytest.raises(ValueError, match="service acknowledgement"):
        host.record_traffic_readback(request_id, "v1", trajectory_check_time_s=10.0,
                                     measured_trajectory=True, no_hazard_ingress=True,
                                     rescue_access_preserved=True, trajectory_ref="vehicle_pose",
                                     hazard_ref="hazard", access_ref="access")
    host.advance(12.0)
    host.record_role_occupancy("go2_1", "traffic_control_point", "road_surface",
                               start_time_s=10.0, end_time_s=12.0)
    with pytest.raises(ValueError, match="mode mismatch"):
        host.record_actor_ack(request_id, "v1", time_s=12.0, applied_mode="divert",
                              evidence_ref="traffic_endpoint")
    host.record_actor_ack(request_id, "v1", time_s=12.0, applied_mode="hold",
                          evidence_ref="traffic_endpoint")
    host.advance(18.0)
    host.record_traffic_readback(request_id, "v1", trajectory_check_time_s=18.0,
                                 measured_trajectory=True, no_hazard_ingress=True,
                                 rescue_access_preserved=True, trajectory_ref="vehicle_pose",
                                 hazard_ref="hazard", access_ref="access")
    assert backend.review(request_id)["status"] == "evidence_complete"


def test_report_receipt_and_continuous_access_are_independent(episode):
    host, backend = episode
    request = backend.report_incident(agent_id="go2_1", region_id="road_surface",
                                      observation_refs=("obs",), recipient_service_id="city_dispatch")
    host.register_request(request, evidence_class="runtime")
    request_id = request["request_id"]
    host.advance(18.0)  # The reporting robot has left its role after submission.
    with pytest.raises(ValueError, match="exact request"):
        host.record_receipt(request_id, recipient_service_id="wrong", observation_refs=["obs"],
                            received_time_s=18.0, receipt_ref="external_receipt")
    host.record_receipt(request_id, recipient_service_id="city_dispatch", observation_refs=["obs"],
                        received_time_s=18.0, receipt_ref="external_receipt")
    assert backend.review(request_id)["status"] == "pending"
    host.advance(20.0)
    host.record_access_readback(request_id, start_time_s=17.0, end_time_s=20.0,
                                measured_clear_throughout=True,
                                rescue_and_evacuation_routes_admitted=True,
                                evidence_ref="world_route_interval")
    assert backend.review(request_id)["status"] == "evidence_complete"


def test_role_gap_cannot_be_patched_by_rejoining_before_ack(episode):
    host, backend = episode
    request_id = _guide(host, backend)
    host.advance(12.0)
    host.record_role_occupancy("go2_1", "guidance_contact", "road_surface",
                               start_time_s=11.0, end_time_s=12.0)
    host.record_actor_ack(request_id, "p1", time_s=12.0, evidence_ref="ack")
    host.advance(18.0)
    host.record_person_readback(request_id, "p1", arrival_time_s=18.0,
                                assembly_region_id="assembly_1", measured_trajectory=True,
                                safe_route_and_assembly=True, trajectory_ref="pose", safety_ref="safety")
    with pytest.raises(ValueError, match="role lost before service acknowledgement"):
        backend.review(request_id)


def test_undelivered_future_expired_and_superseded_observations_rejected(episode):
    host, backend = episode
    future = deepcopy(host.observation("obs"))
    future["id"] = "future"
    future["delivered_at_s"] = 11.0
    with pytest.raises(ValueError, match="undelivered"):
        host.deliver_observation(future)
    newer = deepcopy(host.observation("obs"))
    newer.update(id="newer", captured_at_s=9.0, delivered_at_s=10.0)
    host.deliver_observation(newer)
    with pytest.raises(ValueError, match="superseded"):
        backend.guide_people(agent_id="go2_1", region_id="road_surface",
                             assembly_region_id="assembly_1", person_ids=("p1",),
                             observation_refs=("obs",))
    host.advance(30.0)
    host.record_role_occupancy("go2_1", "guidance_contact", "road_surface",
                               start_time_s=10.0, end_time_s=30.0)
    with pytest.raises(ValueError, match="expired"):
        backend.guide_people(agent_id="go2_1", region_id="road_surface",
                             assembly_region_id="assembly_1", person_ids=("p1",),
                             observation_refs=("newer",))


def test_false_world_readback_and_reference_are_not_runtime_business_success(episode):
    host, backend = episode
    request = backend.control_following_traffic(agent_id="go2_1", region_id="road_surface",
                                                vehicle_ids=("v1",), mode="hold", observation_refs=("obs",))
    host.register_request(request, evidence_class="reference")
    host.advance(12.0)
    host.record_role_occupancy("go2_1", "traffic_control_point", "road_surface",
                               start_time_s=10.0, end_time_s=12.0)
    host.record_actor_ack(request["request_id"], "v1", time_s=12.0,
                          applied_mode="hold", evidence_ref="reference_ack")
    host.advance(18.0)
    host.record_traffic_readback(request["request_id"], "v1", trajectory_check_time_s=18.0,
                                 measured_trajectory=True, no_hazard_ingress=False,
                                 rescue_access_preserved=True, trajectory_ref="reference_pose",
                                 hazard_ref="reference_hazard", access_ref="reference_access")
    assert backend.review(request["request_id"])["status"] == "pending"


def test_missing_admission_and_role_observation_cannot_be_promoted():
    host = CityEpisodeEvidenceHost(admission=lambda *args: False)
    host.advance(10.0)
    backend = CityServiceBackend(host.as_source())
    with pytest.raises(ValueError, match="field role"):
        backend.report_incident(agent_id="go2_1", region_id="road_surface",
                                observation_refs=("unknown",), recipient_service_id="dispatch")
    host.record_role_occupancy("go2_1", "incident_observer", "road_surface",
                               start_time_s=9.0, end_time_s=10.0)
    with pytest.raises(ValueError, match="not delivered"):
        backend.report_incident(agent_id="go2_1", region_id="road_surface",
                                observation_refs=("unknown",), recipient_service_id="dispatch")


def test_reference_readback_remains_reference_only(episode):
    host, backend = episode
    request = backend.control_following_traffic(agent_id="go2_1", region_id="road_surface",
                                                vehicle_ids=("v1",), mode="hold", observation_refs=("obs",))
    host.register_request(request, evidence_class="reference")
    host.advance(12.0)
    host.record_role_occupancy("go2_1", "traffic_control_point", "road_surface",
                               start_time_s=10.0, end_time_s=12.0)
    host.record_actor_ack(request["request_id"], "v1", time_s=12.0,
                          applied_mode="hold", evidence_ref="reference_ack")
    host.advance(18.0)
    host.record_traffic_readback(request["request_id"], "v1", trajectory_check_time_s=18.0,
                                 measured_trajectory=True, no_hazard_ingress=True,
                                 rescue_access_preserved=True, trajectory_ref="reference_pose",
                                 hazard_ref="reference_hazard", access_ref="reference_access")
    result = backend.review(request["request_id"])
    assert result["status"] == "reference_complete" and result["scoreable"] is False


def test_demo_runtime_source_is_fail_closed_at_episode_service_boundary():
    class Runtime:
        elapsed = 0.0
        people = None
        traffic = []

    adapter = CityDemoRuntimeWitnessAdapter(Runtime(), episode_id="city_episode_003")
    witness = CityEpisodeServiceWitness.from_city_runtime(
        adapter, episode_id="city_episode_003")
    report = witness.readiness
    assert report["status"] == "blocked"
    assert report["real_witness_ready"] is False
    assert report["scoreable"] is False
    assert "missing_live_capability:affected_person_source" in report["blockers"]
    assert "missing_live_capability:external_response_receipt" in report["blockers"]
