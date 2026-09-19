"""Service authoring/intake boundaries; no runtime, replay, simulator or scoring."""

from copy import deepcopy
import inspect

import pytest

from demo.benchmark.goal_service_catalog import (
    public_service_proposals, review_service_requirements, validate_service_proposals,
)
from demo.benchmark.skill_ontology import public_skill_catalog, validate_skill_name


@pytest.fixture
def public_task():
    return {
        "candidate_robot_pool": [
            {"id": "rover", "public_capabilities": ["ground_navigation", "relay", "synchronize"]},
            {"id": "drone", "public_capabilities": ["aerial_navigation", "observe", "relay"]},
        ],
        "public_world": {"regions": [{"id": region} for region in ("plaza", "assembly", "ingress")]},
        "required_outcomes": [{"id": identifier} for identifier in (
            "exposed_people_accounted_for", "secondary_traffic_protected", "response_access_preserved")],
    }


def _observation(identifier="people_obs", *, entities=None, capture=2.0, delivery=3.0, expiry=10.0):
    return {
        "id": identifier, "producer_agent_id": "drone", "region_id": "plaza",
        "captured_at_s": capture, "delivered_at_s": delivery, "valid_until_s": expiry,
        "entities": entities if entities is not None else [
            {"id": "person_1", "kind": "person"}, {"id": "rover", "kind": "robot"}],
    }


def _request(identifier="guide_people"):
    context = {
        "current_time_s": 5.0,
        "observations": [
            _observation(),
            _observation("traffic_obs", entities=[{
                "id": "following_1", "kind": "vehicle", "following_incident": True,
                "is_accident_vehicle": False}]),
        ],
        "field_roles": [{"agent_id": "rover", "role": "guidance_contact", "region_id": "plaza",
                         "observation_refs": ["people_obs"]}],
    }
    arguments = {"person_ids": ["person_1"], "observation_refs": ["people_obs"],
                 "assembly_region_id": "assembly", "now_s": 5.0}
    if identifier == "control_traffic":
        arguments = {"vehicle_ids": ["following_1"], "mode": "hold", "observation_refs": ["traffic_obs"]}
        context["field_roles"][0]["role"] = "traffic_control_point"
    elif identifier == "report_incident":
        arguments = {"observation_refs": ["people_obs"], "recipient_service_id": "dispatch", "time_s": 5.0}
        context["field_roles"][0]["role"] = "incident_observer"
    return {"id": identifier, "arguments": arguments, "reference_context": context}


def test_service_catalog_does_not_activate_skills_or_modify_capabilities(public_task):
    original_task, original_skills = deepcopy(public_task), public_skill_catalog()
    proposals = public_service_proposals()
    assert validate_service_proposals(proposals)["validated"]
    assert proposals["registers_active_skills"] is False
    assert proposals["adds_robot_capabilities"] is False
    assert set(proposals["proposals"]) == {"guide_people", "control_traffic", "report_incident"}
    review = review_service_requirements(list(proposals["proposals"]), public_task)
    assert review["integration_required"] is True
    assert review["available_main_skills"] == []
    for identifier, proposal in proposals["proposals"].items():
        assert proposal["activation_status"] == "proposed"
        assert proposal["runtime_status"] == "runtime_pending"
        assert proposal["is_active_skill"] is False
        assert proposal["outcome_boundary"]["acknowledgement_is_business_success"] is False
        if identifier == "control_traffic":
            with pytest.raises(ValueError):
                validate_skill_name(identifier)
        else:
            assert validate_skill_name(identifier) == identifier
    assert public_task == original_task
    assert public_skill_catalog() == original_skills


def test_catalog_matches_city_proposal_ids_and_actual_adapter_signatures():
    # Read-only signature checks; importing these pure modules starts no runtime.
    from demo.city_benchmark.contracts import required_services
    from demo.city_benchmark.response import CityResponseServices
    from demo.city_benchmark.traffic import TrafficControlRuntime

    proposals = public_service_proposals()["proposals"]
    assert set(proposals) == {row["id"] for row in required_services()}
    for proposal in proposals.values():
        name = proposal["adapter_reference"]["entry_point"].rsplit(".", 1)[1]
        signature = inspect.signature(getattr(CityResponseServices, name))
        assert set(proposal["parameters"]) == set(signature.parameters) - {"self"}
        assert set(proposal["adapter_reference"]["parameter_bindings"].values()) <= set(signature.parameters)
    assert set(proposals["control_traffic"]["parameters"]["mode"]["values"]) == TrafficControlRuntime.VALID_MODES
    # The public proposal, like the current adapter, requires explicit IDs and
    # observation references instead of implicitly enumerating a private roster.
    assert proposals["guide_people"]["parameters"]["person_ids"]["required"] is True


@pytest.mark.parametrize("mutation", ["top_extra", "parameter_extra", "adapter_extra", "role_extra", "outcome_extra", "activate", "disable_expiry", "allow_accident", "boolean_version"])
def test_closed_catalog_rejects_nested_extras_and_weakened_boundaries(mutation):
    catalog = public_service_proposals()
    proposal = catalog["proposals"]["guide_people"]
    if mutation == "top_extra":
        catalog["runtime"] = "enabled"
    elif mutation == "parameter_extra":
        proposal["parameters"]["person_ids"]["trusted"] = True
    elif mutation == "adapter_extra":
        proposal["adapter_reference"]["auto_dispatch"] = True
    elif mutation == "role_extra":
        proposal["field_role_contract"]["required_fields"]["agent_id"]["default"] = "rover"
    elif mutation == "outcome_extra":
        proposal["outcome_boundary"]["score"] = 1.0
    elif mutation == "activate":
        proposal["activation_status"] = "active"
    elif mutation == "disable_expiry":
        proposal["dynamic_reference_policy"]["require_delivered"] = False
    elif mutation == "allow_accident":
        proposal["dynamic_reference_policy"]["accident_vehicle_control_allowed"] = True
    else:
        catalog["version"] = True
    with pytest.raises(ValueError):
        validate_service_proposals(catalog)


def test_core_supply_pending_extensions_and_unknown_names_are_distinct(public_task):
    report = review_service_requirements(["observe", "synchronize", "guide_people", "fire_suppression"], public_task)
    assert report["available_main_skills"] == ["observe"]
    assert report["missing_main_skills"] == ["guide_people", "synchronize"]
    assert report["proposed_services"] == []
    assert report["unknown_requirements"] == ["fire_suppression"]
    assert report["all_requirements_declared_in_main_ontology"] is False
    assert report["execution_validated"] is False
    assert all(row["runtime_authorized"] is False for row in report["requirements"])


def test_core_declaration_review_does_not_claim_plan_or_execution_validation(public_task):
    report = review_service_requirements(["ground_navigation", {"id": "observe"}], public_task)
    assert report["all_requirements_declared_in_main_ontology"] is True
    assert report["public_task_schema_validated"] is False
    assert report["business_outcomes_validated"] is False
    assert report["requirements"][1]["declared_candidate_ids"] == ["drone"]


def test_city_legacy_requirements_report_unimplemented_argument_bindings(public_task):
    from demo.city_benchmark.contracts import required_services

    requirements = required_services()
    before = deepcopy(requirements)
    report = review_service_requirements(requirements, public_task)
    rows = {row["id"]: row for row in report["requirements"]}
    assert rows["guide_people"]["legacy_parameter_mapping"]["observed_person_ids"] == "person_ids"
    assert rows["guide_people"]["unmapped_legacy_parameters"] == ["route_reference"]
    assert rows["control_traffic"]["unmapped_legacy_parameters"] == ["ingress_region_id", "route_reference"]
    assert rows["control_traffic"]["additional_adapter_parameters"] == ["observation_refs", "vehicle_ids"]
    assert rows["control_traffic"]["participant_operation"] == "control_following_traffic"
    assert rows["control_traffic"]["requested_public_skill"] == "control_following_traffic"
    assert rows["control_traffic"]["canonical_proposal_id"] == "control_traffic"
    assert rows["report_incident"]["additional_adapter_parameters"] == ["time_s"]
    assert requirements == before


@pytest.mark.parametrize("identifier", ["guide_people", "control_traffic", "report_incident"])
def test_well_formed_visible_requests_remain_unactivated(public_task, identifier):
    request = _request(identifier)
    before = deepcopy(request)
    result = review_service_requirements([request], public_task)["requirements"][0]
    if identifier == "control_traffic":
        assert result["status"] == "requires_integration"
        assert result["catalog_kind"] == "service_proposal"
    else:
        assert result["status"] == "registered_runtime_pending"
        assert result["catalog_kind"] == "active_city_service"
    assert result["runtime_status"] == "runtime_pending"
    review = result["request_review"]
    assert review["arguments_and_references_validated"] is True
    assert review["runtime_authorized"] is False
    assert review["business_outcomes_validated"] is False
    assert review["evidence_provenance_validated"] is False
    assert review["field_role_agent_ids"] == ["rover"]
    assert request == before


@pytest.mark.parametrize("problem", ["unobserved", "undelivered", "expired", "future_capture", "wrong_kind"])
def test_dynamic_people_refs_require_fresh_delivered_identity(public_task, problem):
    request = _request()
    observation = request["reference_context"]["observations"][0]
    if problem == "unobserved":
        request["arguments"]["person_ids"] = ["hidden_person"]
    elif problem == "undelivered":
        observation["delivered_at_s"] = 6.0
    elif problem == "expired":
        observation["valid_until_s"] = 5.0
    elif problem == "future_capture":
        observation.update(captured_at_s=6.0, delivered_at_s=7.0)
    else:
        request["arguments"]["person_ids"] = ["following_1"]
    with pytest.raises(ValueError):
        review_service_requirements([request], public_task)


def test_newer_expired_actor_state_cannot_be_replaced_by_old_long_lived_snapshot(public_task):
    request = _request()
    request["reference_context"]["observations"].append(
        _observation("newer_person", capture=4, delivery=4, expiry=4.5))
    with pytest.raises(ValueError, match="expired"):
        review_service_requirements([request], public_task)


@pytest.mark.parametrize("identifier", ["guide_people", "control_traffic"])
def test_dynamic_ids_must_appear_in_cited_observations_not_only_somewhere_in_snapshot(public_task, identifier):
    request = _request(identifier)
    request["arguments"]["observation_refs"] = ["traffic_obs" if identifier == "guide_people" else "people_obs"]
    with pytest.raises(ValueError, match="bound to cited observation_refs"):
        review_service_requirements([request], public_task)


@pytest.mark.parametrize("kind", ["accident", "not_following", "unknown_classification", "old_accident_relabelled"])
def test_following_traffic_service_cannot_control_accident_vehicle(public_task, kind):
    request = _request("control_traffic")
    observations = request["reference_context"]["observations"]
    vehicle = observations[1]["entities"][0]
    if kind == "accident":
        vehicle["is_accident_vehicle"] = True
    elif kind == "not_following":
        vehicle["following_incident"] = False
    elif kind == "unknown_classification":
        vehicle.pop("is_accident_vehicle")
    else:
        observations.append(_observation("old_accident", capture=0, delivery=0, expiry=1, entities=[
            {"id": "following_1", "kind": "vehicle", "following_incident": False, "is_accident_vehicle": True}]))
    with pytest.raises(ValueError):
        review_service_requirements([request], public_task)


@pytest.mark.parametrize("problem", ["expired", "undelivered", "unknown", "clock_mismatch"])
def test_incident_report_cannot_use_stale_or_future_evidence(public_task, problem):
    request = _request("report_incident")
    observation = request["reference_context"]["observations"][0]
    if problem == "expired":
        observation["valid_until_s"] = 5.0
    elif problem == "undelivered":
        observation["delivered_at_s"] = 6.0
    elif problem == "unknown":
        request["arguments"]["observation_refs"] = ["hidden_incident_truth"]
    else:
        request["arguments"]["time_s"] = 4.0
    with pytest.raises(ValueError):
        review_service_requirements([request], public_task)


@pytest.mark.parametrize("problem", ["wrong_region", "wrong_role", "no_pose", "nonpublic_agent", "self_assertion_extra"])
def test_field_role_needs_matching_observed_presence_not_an_assertion(public_task, problem):
    request = _request()
    context = request["reference_context"]
    role = context["field_roles"][0]
    if problem == "wrong_region":
        role["region_id"] = "ingress"
    elif problem == "wrong_role":
        role["role"] = "traffic_control_point"
    elif problem == "no_pose":
        context["observations"][0]["entities"] = [{"id": "person_1", "kind": "person"}]
    elif problem == "nonpublic_agent":
        role["agent_id"] = "hidden_reserve"
    else:
        role["arrived"] = True
    with pytest.raises(ValueError):
        review_service_requirements([request], public_task)


def test_missing_field_role_is_an_open_integration_requirement(public_task):
    request = _request()
    request["reference_context"]["field_roles"] = []
    report = review_service_requirements([request], public_task)
    review = report["requirements"][0]["request_review"]
    assert review["field_role_reference_status"] == "missing"
    assert "field_role_binding" in review["remaining_requirements"]
    assert report["integration_required"] is True


def test_newer_robot_pose_invalidates_previous_field_role_location(public_task):
    request = _request()
    newer = _observation("newer_pose", capture=4, delivery=4,
                         entities=[{"id": "rover", "kind": "robot"}])
    newer["region_id"] = "ingress"
    request["reference_context"]["observations"].append(newer)
    with pytest.raises(ValueError, match="latest observed robot presence"):
        review_service_requirements([request], public_task)


def test_future_vehicle_classification_cannot_affect_current_public_review(public_task):
    request = _request("control_traffic")
    request["reference_context"]["observations"].append(_observation(
        "future_private_observation", capture=6, delivery=7, entities=[{
            "id": "following_1", "kind": "vehicle", "following_incident": False,
            "is_accident_vehicle": True}]))
    report = review_service_requirements([request], public_task)
    refs = report["requirements"][0]["request_review"]["observation_refs"]
    assert "future_private_observation" not in refs
    assert report["requirements"][0]["runtime_authorized"] is False


@pytest.mark.parametrize("problem", ["context_extra", "observation_extra", "entity_extra", "empty_persons", "duplicate_persons", "unknown_mode", "boolean_time", "nan_time", "absent_persons"])
def test_concrete_request_nested_schemas_fail_closed(public_task, problem):
    request = _request("control_traffic" if problem == "unknown_mode" else "guide_people")
    if problem == "context_extra":
        request["reference_context"]["allow_future"] = True
    elif problem == "observation_extra":
        request["reference_context"]["observations"][0]["trusted"] = True
    elif problem == "entity_extra":
        request["reference_context"]["observations"][0]["entities"][0]["ground_truth"] = True
    elif problem == "empty_persons":
        request["arguments"]["person_ids"] = []
    elif problem == "duplicate_persons":
        request["arguments"]["person_ids"] *= 2
    elif problem == "unknown_mode":
        request["arguments"]["mode"] = "ram_accident_vehicle"
    elif problem == "boolean_time":
        request["reference_context"]["current_time_s"] = True
    elif problem == "nan_time":
        request["reference_context"]["current_time_s"] = float("nan")
    else:
        request["arguments"].pop("person_ids")
    with pytest.raises(ValueError):
        review_service_requirements([request], public_task)


def test_forged_capability_and_unknown_requirement_fields_are_rejected(public_task):
    with pytest.raises(ValueError, match="closed schema"):
        review_service_requirements([{"id": "guide_people", "activation_status": "active"}], public_task)
    with pytest.raises(ValueError, match="unknown service"):
        request = _request()
        request["id"] = "extinguish_fire"
        review_service_requirements([request], public_task)
    public_task["candidate_robot_pool"][0]["public_capabilities"].append("guide_people")
    report = review_service_requirements(["guide_people"], public_task)
    assert report["requirements"][0]["status"] == "registered_runtime_pending"


def test_returned_catalog_does_not_share_mutable_policy_data():
    first = public_service_proposals()
    first["proposals"]["guide_people"]["field_role_contract"]["evidence_requirements"].clear()
    second = public_service_proposals()
    assert second["proposals"]["guide_people"]["field_role_contract"]["evidence_requirements"]
    assert validate_service_proposals(second)["validated"]
