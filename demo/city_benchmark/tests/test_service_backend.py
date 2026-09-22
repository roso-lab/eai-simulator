"""Conformance checks for the city service boundary, without Isaac or ROS."""
import pytest

from demo.city_benchmark.service_backend import (
    CityRuntimeReadbackAdapter, CityServiceBackend, CityServiceRequestAdapter,
)


class TrustedSource:
    def __init__(self):
        self.clock = 10.0
        self.role = True
        self.role_agent = "go2_1"
        self.role_until = None
        self.admitted = True
        self.snapshots = {"obs": {
            "id": "obs", "producer_agent_id": "iris_1", "region_id": "road_surface",
            "captured_at_s": 8.0, "delivered_at_s": 9.0, "valid_until_s": 30.0,
            "entities": [{"id": "p1", "kind": "person"},
                         {"id": "v1", "kind": "vehicle", "following_incident": True,
                          "is_accident_vehicle": False},
                         {"id": "accident", "kind": "vehicle", "following_incident": False,
                          "is_accident_vehicle": True}],
        }}
        self.latest = 8.0
        self.updates = {}

    def now_s(self):
        return self.clock

    def observation(self, ref):
        return self.snapshots.get(ref)

    def latest_delivered_capture_s(self, entity_id, now_s):
        return self.latest

    def role_active(self, agent_id, role, region_id, now_s):
        return (self.role and agent_id == self.role_agent and region_id == "road_surface"
                and (self.role_until is None or now_s <= self.role_until))

    def role_interval(self, agent_id, role, region_id, as_of_s):
        if agent_id != self.role_agent or region_id != "road_surface":
            return None
        return {"start_time_s": 1.0,
                "end_time_s": min(as_of_s, self.role_until) if self.role_until is not None else as_of_s}

    def admit(self, service, ids, region_id, target_region_id, mode, now_s):
        return self.admitted and (service != "guide_people" or target_region_id == "assembly_1")

    def service_update(self, request_id):
        return self.updates.get(request_id)


@pytest.fixture
def source_and_backend():
    source = TrustedSource()
    return source, CityServiceBackend(source)


def guide(backend, **overrides):
    args = dict(agent_id="go2_1", region_id="road_surface", assembly_region_id="assembly_1",
                person_ids=("p1",), observation_refs=("obs",))
    args.update(overrides)
    return backend.guide_people(**args)


def test_guide_requires_trusted_arrival_after_independent_ack(source_and_backend):
    source, backend = source_and_backend
    request = guide(backend)
    assert request["status"] == "pending" and request["scoreable"] is False
    source.clock = 32.0
    source.updates[request["request_id"]] = {
        "request_id": request["request_id"], "service": "guide_people", "evidence_class": "runtime",
        "actors": [{"id": "p1", "ack_time_s": 20.0}]}
    assert backend.review(request["request_id"])["status"] == "pending"
    source.clock = 40.0
    source.updates[request["request_id"]]["actors"][0].update(
        arrival_time_s=39.0, assembly_region_id="assembly_1",
        measured_trajectory=True, safe_route_and_assembly=True)
    result = backend.review(request["request_id"])
    assert result["status"] == "evidence_complete" and result["scoreable"] is False
    result["evidence"]["actors"][0]["id"] = "wrong"
    assert backend.review(request["request_id"])["evidence"]["actors"][0]["id"] == "p1"


def test_guidance_contact_can_finish_before_independent_arrival(source_and_backend):
    source, backend = source_and_backend
    request = guide(backend)
    source.clock = 12.0
    source.updates[request["request_id"]] = {
        "request_id": request["request_id"], "service": "guide_people",
        "evidence_class": "runtime",
        "actors": [{"id": "p1", "ack_time_s": 12.0, "ack_evidence_ref": "native-ack-p1"}],
    }
    contact = backend.guidance_contact(request["request_id"])
    assert contact == {
        "request_id": request["request_id"], "status": "contact_confirmed",
        "agent_id": "go2_1", "ack_time_s": 12.0,
        "evidence_refs": ["native-ack-p1"],
        "actor_acks": [{"id": "p1", "ack_time_s": 12.0,
                        "ack_evidence_ref": "native-ack-p1"}],
        "business_outcome_validated": False, "scoreable": False,
    }
    assert backend.review(request["request_id"])["status"] == "pending"
    source.clock = 40.0
    source.updates[request["request_id"]]["actors"][0].update(
        arrival_time_s=39.0, assembly_region_id="assembly_1",
        measured_trajectory=True, safe_route_and_assembly=True)
    assert backend.review(request["request_id"])["status"] == "evidence_complete"


@pytest.mark.parametrize("change,expected", [
    (lambda source, actor: actor.pop("ack_evidence_ref"), "evidence missing"),
    (lambda source, actor: actor.update(ack_time_s=31.0), "timed_out"),
    (lambda source, actor: setattr(source, "role_until", 11.0), "role lost"),
    (lambda source, actor: actor.update(id="other"), "identity"),
    (lambda source, actor: source.updates[next(iter(source.updates))].update(
        evidence_class="reference") or setattr(source, "clock", 12.0), "reference_contact_only"),
])
def test_guidance_contact_rejects_untrusted_or_late_ack(source_and_backend, change, expected):
    source, backend = source_and_backend
    request = guide(backend)
    source.clock = 32.0
    actor = {"id": "p1", "ack_time_s": 12.0, "ack_evidence_ref": "native-ack-p1"}
    source.updates[request["request_id"]] = {
        "request_id": request["request_id"], "service": "guide_people",
        "evidence_class": "runtime", "actors": [actor]}
    change(source, actor)
    if expected in {"timed_out", "reference_contact_only"}:
        assert backend.guidance_contact(request["request_id"])["status"] == expected
    else:
        with pytest.raises(ValueError, match=expected):
            backend.guidance_contact(request["request_id"])


def test_guidance_contact_does_not_promote_historical_ack_after_budget(source_and_backend):
    source, backend = source_and_backend
    request = guide(backend)
    source.clock = 181.0
    source.updates[request["request_id"]] = {
        "request_id": request["request_id"], "service": "guide_people",
        "evidence_class": "runtime",
        "actors": [{"id": "p1", "ack_time_s": 12.0, "ack_evidence_ref": "native-ack-p1"}],
    }
    assert backend.guidance_contact(request["request_id"])["status"] == "timed_out"


def test_guidance_contact_stays_timed_out_after_late_backfill(source_and_backend):
    source, backend = source_and_backend
    request = guide(backend)
    source.clock = 32.0
    assert backend.review(request["request_id"])["status"] == "timed_out"
    source.updates[request["request_id"]] = {
        "request_id": request["request_id"], "service": "guide_people",
        "evidence_class": "runtime",
        "actors": [{"id": "p1", "ack_time_s": 12.0, "ack_evidence_ref": "ack-p1"}],
    }
    assert backend.guidance_contact(request["request_id"])["status"] == "timed_out"


def test_confirmed_contact_survives_later_arrival_timeout(source_and_backend):
    source, backend = source_and_backend
    request = guide(backend)
    source.clock = 12.0
    source.updates[request["request_id"]] = {
        "request_id": request["request_id"], "service": "guide_people",
        "evidence_class": "runtime",
        "actors": [{"id": "p1", "ack_time_s": 12.0, "ack_evidence_ref": "ack-p1"}],
    }
    confirmed = backend.guidance_contact(request["request_id"])
    source.clock = 133.0
    assert backend.review(request["request_id"])["status"] == "timed_out"
    assert backend.guidance_contact(request["request_id"]) == confirmed


def test_guidance_contact_requires_every_target_ack_and_unique_evidence(source_and_backend):
    source, backend = source_and_backend
    source.snapshots["obs"]["entities"].append({"id": "p2", "kind": "person"})
    request = guide(backend, person_ids=("p1", "p2"))
    source.clock = 12.0
    update = {"request_id": request["request_id"], "service": "guide_people",
              "evidence_class": "runtime", "actors": [
                  {"id": "p1", "ack_time_s": 11.0, "ack_evidence_ref": "ack-p1"}]}
    source.updates[request["request_id"]] = update
    assert backend.guidance_contact(request["request_id"])["status"] == "pending"
    update["actors"].append({"id": "p2", "ack_time_s": 12.0,
                             "ack_evidence_ref": "ack-p2"})
    update["actors"][1]["ack_evidence_ref"] = "ack-p1"
    with pytest.raises(ValueError, match="duplicate guidance acknowledgement evidence"):
        backend.guidance_contact(request["request_id"])
    update["actors"][1]["ack_evidence_ref"] = "ack-p2"
    assert backend.guidance_contact(request["request_id"])["ack_time_s"] == 12.0


def test_guidance_contact_uses_original_role_interval_after_reentry(source_and_backend):
    source, backend = source_and_backend
    request = guide(backend)
    source.clock = 25.0
    source.updates[request["request_id"]] = {
        "request_id": request["request_id"], "service": "guide_people",
        "evidence_class": "runtime",
        "actors": [{"id": "p1", "ack_time_s": 12.0, "ack_evidence_ref": "ack-p1",
                    "arrival_time_s": 24.0, "assembly_region_id": "assembly_1",
                    "measured_trajectory": True, "safe_route_and_assembly": True}],
    }
    def interval(agent_id, role, region_id, as_of_s):
        if (agent_id, role, region_id) != ("go2_1", "guidance_contact", "road_surface"):
            return None
        if as_of_s < 15.0:
            return {"start_time_s": 10.0, "end_time_s": as_of_s}
        return {"start_time_s": 20.0, "end_time_s": as_of_s}
    source.role_interval = interval
    assert backend.guidance_contact(request["request_id"])["status"] == "contact_confirmed"
    assert backend.review(request["request_id"])["status"] == "evidence_complete"


@pytest.mark.parametrize("change,expected", [
    (lambda s: s.snapshots["obs"].update(delivered_at_s=11.0), "future"),
    (lambda s: s.snapshots["obs"].update(valid_until_s=10.0), "expired"),
    (lambda s: setattr(s, "latest", 9.0), "superseded"),
    (lambda s: setattr(s, "role", False), "field role"),
    (lambda s: setattr(s, "admitted", False), "not admitted"),
])
def test_guide_rejects_visibility_and_field_gaps(source_and_backend, change, expected):
    source, backend = source_and_backend
    change(source)
    with pytest.raises(ValueError, match=expected):
        guide(backend)


def test_unobserved_duplicate_and_wrong_entity_cannot_be_guided(source_and_backend):
    _, backend = source_and_backend
    for ids in (("hidden_person",), ("p1", "p1"), ("v1",)):
        with pytest.raises(ValueError):
            guide(backend, person_ids=ids)
    guide(backend)
    with pytest.raises(ValueError, match="duplicate service request"):
        guide(backend)


def test_reference_guidance_is_never_runtime_evidence(source_and_backend):
    source, backend = source_and_backend
    request = guide(backend)
    source.clock = 30.0
    source.updates[request["request_id"]] = {
        "request_id": request["request_id"], "service": "guide_people", "evidence_class": "reference",
        "actors": [{"id": "p1", "ack_time_s": 11.0, "arrival_time_s": 29.0,
                    "assembly_region_id": "assembly_1", "measured_trajectory": True,
                    "safe_route_and_assembly": True}]}
    result = backend.review(request["request_id"])
    assert result["status"] == "reference_complete" and result["scoreable"] is False


def test_traffic_requires_following_identity_ack_and_reaction_trajectory(source_and_backend):
    source, backend = source_and_backend
    with pytest.raises(ValueError, match="following traffic"):
        backend.control_following_traffic(agent_id="go2_1", region_id="road_surface",
                                          vehicle_ids=("accident",), mode="hold", observation_refs=("obs",))
    request = backend.control_following_traffic(agent_id="go2_1", region_id="road_surface",
                                                vehicle_ids=("v1",), mode="hold", observation_refs=("obs",))
    source.clock = 18.0
    source.updates[request["request_id"]] = {
        "request_id": request["request_id"], "service": "control_traffic", "evidence_class": "runtime",
        "actors": [{"id": "v1", "ack_time_s": 12.0, "trajectory_check_time_s": 16.0,
                    "applied_mode": "hold", "measured_trajectory": True, "no_hazard_ingress": True,
                    "rescue_access_preserved": True}]}
    assert backend.review(request["request_id"])["status"] == "pending"
    source.updates[request["request_id"]]["actors"][0]["trajectory_check_time_s"] = 18.0
    assert backend.review(request["request_id"])["status"] == "evidence_complete"


def test_report_requires_exact_external_receipt_and_continuous_access(source_and_backend):
    source, backend = source_and_backend
    request = backend.report_incident(agent_id="go2_1", region_id="road_surface",
                                      observation_refs=("obs",), recipient_service_id="city_dispatch")
    source.clock = 20.0
    update = {"request_id": request["request_id"], "service": "report_incident",
              "evidence_class": "runtime", "receipt": {
                  "recipient_service_id": "city_dispatch", "observation_refs": ["obs"],
                  "received_time_s": 19.0}}
    source.updates[request["request_id"]] = update
    assert backend.review(request["request_id"])["status"] == "pending"
    source.clock = 26.0
    update["continuous_access"] = {"start_time_s": 18.0, "end_time_s": 25.0,
                                   "measured_clear_throughout": True,
                                   "rescue_and_evacuation_routes_admitted": True}
    assert backend.review(request["request_id"])["status"] == "evidence_complete"


@pytest.mark.parametrize("mutate", [
    lambda u: u["receipt"].update(recipient_service_id="other"),
    lambda u: u["receipt"].update(observation_refs=["other"]),
    lambda u: u["receipt"].update(received_time_s=9.0),
])
def test_forged_or_reversed_receipt_rejected(source_and_backend, mutate):
    source, backend = source_and_backend
    request = backend.report_incident(agent_id="go2_1", region_id="road_surface",
                                      observation_refs=("obs",), recipient_service_id="city_dispatch")
    source.clock = 20.0
    update = {"request_id": request["request_id"], "service": "report_incident",
              "evidence_class": "runtime", "receipt": {
                  "recipient_service_id": "city_dispatch", "observation_refs": ["obs"],
                  "received_time_s": 19.0}}
    mutate(update)
    source.updates[request["request_id"]] = update
    with pytest.raises(ValueError):
        backend.review(request["request_id"])


def test_missing_readback_times_out_without_world_success(source_and_backend):
    source, backend = source_and_backend
    request = backend.report_incident(agent_id="go2_1", region_id="road_surface",
                                      observation_refs=("obs",), recipient_service_id="city_dispatch")
    source.clock = 26.0
    assert backend.review(request["request_id"])["status"] == "timed_out"


def test_first_review_after_episode_budget_cannot_promote_historical_readback(source_and_backend):
    source, backend = source_and_backend
    request = guide(backend)
    source.updates[request["request_id"]] = {
        "request_id": request["request_id"], "service": "guide_people", "evidence_class": "runtime",
        "actors": [{"id": "p1", "ack_time_s": 20.0, "arrival_time_s": 39.0,
                    "assembly_region_id": "assembly_1", "measured_trajectory": True,
                    "safe_route_and_assembly": True}],
    }
    source.clock = 181.0
    result = backend.review(request["request_id"])
    assert result["status"] == "timed_out"
    assert result["scoreable"] is False
    assert backend.review(request["request_id"])["scoreable"] is False


def test_runtime_adapter_wires_episode_owned_callbacks(source_and_backend):
    source, _ = source_and_backend
    adapter = CityRuntimeReadbackAdapter(
        clock=source.now_s, observation_lookup=source.observation,
        latest_capture=source.latest_delivered_capture_s, field_role=source.role_active,
        field_role_interval=source.role_interval,
        admission=source.admit, readback=source.service_update)
    assert CityServiceBackend(adapter).report_incident(
        agent_id="go2_1", region_id="road_surface", observation_refs=("obs",),
        recipient_service_id="city_dispatch")["status"] == "pending"


def test_acknowledged_guidance_still_times_out_without_arrival(source_and_backend):
    source, backend = source_and_backend
    request = guide(backend)
    source.updates[request["request_id"]] = {
        "request_id": request["request_id"], "service": "guide_people", "evidence_class": "runtime",
        "actors": [{"id": "p1", "ack_time_s": 11.0}]}
    source.clock = 132.0
    assert backend.review(request["request_id"])["status"] == "timed_out"


def test_late_arrival_does_not_pass_after_episode_budget(source_and_backend):
    source, backend = source_and_backend
    request = guide(backend)
    source.clock = 181.0
    source.updates[request["request_id"]] = {
        "request_id": request["request_id"], "service": "guide_people", "evidence_class": "runtime",
        "actors": [{"id": "p1", "ack_time_s": 20.0, "arrival_time_s": 181.0,
                    "assembly_region_id": "assembly_1", "measured_trajectory": True,
                    "safe_route_and_assembly": True}]}
    with pytest.raises(ValueError, match="arrival outside causal interval"):
        backend.review(request["request_id"])


def test_duplicate_and_missing_traffic_readback_cannot_complete(source_and_backend):
    source, backend = source_and_backend
    request = backend.control_following_traffic(agent_id="go2_1", region_id="road_surface",
                                                vehicle_ids=("v1",), mode="hold", observation_refs=("obs",))
    source.clock = 18.0
    actor = {"id": "v1", "ack_time_s": 12.0, "trajectory_check_time_s": 18.0,
             "applied_mode": "hold", "measured_trajectory": True, "no_hazard_ingress": True,
             "rescue_access_preserved": True}
    update = {"request_id": request["request_id"], "service": "control_traffic",
              "evidence_class": "runtime", "actors": []}
    source.updates[request["request_id"]] = update
    with pytest.raises(ValueError, match="exactly once"):
        backend.review(request["request_id"])
    update["actors"] = [actor, actor]
    with pytest.raises(ValueError, match="exactly once"):
        backend.review(request["request_id"])
    update["actors"] = [actor]
    actor["no_hazard_ingress"] = False
    assert backend.review(request["request_id"])["status"] == "pending"


def test_request_adapter_uses_current_deployment_assignment_and_clock(source_and_backend):
    source, backend = source_and_backend
    adapter = CityServiceRequestAdapter(
        backend, lambda service: {"agent_id": "go2_1", "region_id": "road_surface"})
    with pytest.raises(ValueError, match="trusted episode clock"):
        adapter.report_incident(["obs"], "city_dispatch", time_s=11.0)
    assert adapter.guide_people(person_ids=["p1"], observation_refs=["obs"],
                                assembly_region_id="assembly_1", now_s=10.0)["status"] == "pending"
    assert adapter.control_following_traffic(["v1"], "hold", observation_refs=["obs"])[
        "status"] == "pending"
    assert adapter.report_incident(["obs"], "city_dispatch", time_s=10.0)["status"] == "pending"


def test_guidance_exit_before_ack_cannot_borrow_later_arrival(source_and_backend):
    source, backend = source_and_backend
    request = guide(backend)
    source.role_until = 12.0
    source.clock = 40.0
    source.updates[request["request_id"]] = {
        "request_id": request["request_id"], "service": "guide_people", "evidence_class": "runtime",
        "actors": [{"id": "p1", "ack_time_s": 20.0, "arrival_time_s": 39.0,
                    "assembly_region_id": "assembly_1", "measured_trajectory": True,
                    "safe_route_and_assembly": True}]}
    with pytest.raises(ValueError, match="role lost before service acknowledgement"):
        backend.review(request["request_id"])
    # Replacement is an independent current request, never a reinterpretation of the old one.
    source.role_agent = "carter_1"
    source.role_until = None
    source.clock = 41.0
    source.snapshots["obs"]["valid_until_s"] = 60.0
    replacement = backend.guide_people(
        agent_id="carter_1", region_id="road_surface", assembly_region_id="assembly_1",
        person_ids=("p1",), observation_refs=("obs",))
    assert replacement["request_id"] != request["request_id"]
    with pytest.raises(ValueError, match="role lost before service acknowledgement"):
        backend.review(request["request_id"])


def test_traffic_exit_before_ack_rejected_but_exit_after_ack_allowed(source_and_backend):
    source, backend = source_and_backend
    request = backend.control_following_traffic(agent_id="go2_1", region_id="road_surface",
                                                vehicle_ids=("v1",), mode="hold", observation_refs=("obs",))
    source.clock = 18.0
    actor = {"id": "v1", "ack_time_s": 12.0, "trajectory_check_time_s": 18.0,
             "applied_mode": "hold", "measured_trajectory": True,
             "no_hazard_ingress": True, "rescue_access_preserved": True}
    source.updates[request["request_id"]] = {
        "request_id": request["request_id"], "service": "control_traffic",
        "evidence_class": "runtime", "actors": [actor]}
    source.role_until = 11.0
    with pytest.raises(ValueError, match="role lost before service acknowledgement"):
        backend.review(request["request_id"])
    source.role_until = 13.0
    assert backend.review(request["request_id"])["status"] == "evidence_complete"


def test_report_receipt_can_arrive_after_observer_exits(source_and_backend):
    source, backend = source_and_backend
    request = backend.report_incident(agent_id="go2_1", region_id="road_surface",
                                      observation_refs=("obs",), recipient_service_id="city_dispatch")
    source.role_until = 11.0
    source.clock = 20.0
    source.updates[request["request_id"]] = {
        "request_id": request["request_id"], "service": "report_incident",
        "evidence_class": "runtime", "receipt": {
            "recipient_service_id": "city_dispatch", "observation_refs": ["obs"],
            "received_time_s": 19.0},
        "continuous_access": {"start_time_s": 18.0, "end_time_s": 20.0,
                              "measured_clear_throughout": True,
                              "rescue_and_evacuation_routes_admitted": True}}
    assert backend.review(request["request_id"])["status"] == "evidence_complete"


def test_guidance_arrival_can_follow_confirmed_role_exit(source_and_backend):
    source, backend = source_and_backend
    request = guide(backend)
    source.role_until = 13.0
    source.clock = 40.0
    source.updates[request["request_id"]] = {
        "request_id": request["request_id"], "service": "guide_people", "evidence_class": "runtime",
        "actors": [{"id": "p1", "ack_time_s": 12.0, "arrival_time_s": 39.0,
                    "assembly_region_id": "assembly_1", "measured_trajectory": True,
                    "safe_route_and_assembly": True}]}
    assert backend.review(request["request_id"])["status"] == "evidence_complete"


def test_instant_role_flag_without_occupancy_interval_rejected(source_and_backend):
    source, backend = source_and_backend
    source.role_interval = lambda *args: None
    with pytest.raises(ValueError, match="time-bounded field role"):
        guide(backend)
