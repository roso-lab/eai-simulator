"""Offline pure-Python checks for the scripted reassessment injection adapter.

These tests never import Isaac: they drive a real ``AuditedGoalSession``
through the injector with stub fleet/pump objects, replay the collected
session into the runtime-epoch collection, and exercise the offline
issue/verify chain against the synthetic certificate fixture reused from
``test_benchmark_goal_restaurant_reassessment``.
"""

from copy import deepcopy
import json

import pytest

from demo.benchmark.goal_restaurant_deployment_v2 import build_restaurant_deployment_v2
from demo.benchmark.goal_restaurant_reassessment import RestaurantReassessmentOracle
from demo.benchmark.goal_restaurant_reassessment_collection import (
    build_extension_input,
    collect_reassessment_epoch_inputs,
    review_reassessment_collection,
)
from demo.benchmark.goal_restaurant_reassessment_injection import (
    PENDING_EXTENSION_EVALUATOR_REF,
    ScriptedReassessmentInjector,
    established_readiness_facts,
    issue_reassessment_extension,
    parse_injection_script,
)
from demo.benchmark.goal_session_audit import AuditedGoalSession
from demo.benchmark.provenance import canonical_sha256


GO2 = "restaurant_go2_1"
M20_1 = "restaurant_m20_1"


def _contract():
    return build_restaurant_deployment_v2()


def _spawn_regions():
    deployment = _contract()["deployment"]
    return dict(deployment["spawn_regions"])


def _session(episode_id="injection-test-episode"):
    contract = _contract()
    session = AuditedGoalSession(
        contract["public_task"], contract["participant_api"], episode_id=episode_id)
    session.initialize_deployment_runtime_state(_spawn_regions(), {})
    return session


def _decide(session):
    gate = session.report()["deployment_assessment"]
    assert gate["status"] == "required"
    return session.submit_action({
        "schema": "eai.benchmark.goal_action", "version": 1,
        "action_id": f"assessment-{gate['assessment_epoch']}",
        "submitted_at_s": session.report()["current_time_s"],
        "type": "deployment_assessment",
        "payload": {
            "assessment_id": gate["assessment_id"],
            "assessment_epoch": gate["assessment_epoch"],
            "roster_version": gate["roster_version"],
            "capability_versions": deepcopy(gate["capability_versions"]),
            "decision": "deployable", "reason_codes": [], "evidence_refs": [],
        },
    })


class _StubReadiness:
    def __init__(self, *, with_receipt):
        self.with_receipt = with_receipt

    def snapshot(self):
        observations = {
            "obs-kitchen": {"region_id": "kitchen_service"},
            "obs-dining": {"region_id": "dining_service"},
        }
        receipt = ({"observation_refs": sorted(observations)} if self.with_receipt
                   else None)
        return {"readiness_service": {"observations": observations,
                                      "receipt": receipt}}


class _StubPump:
    """Minimal pump surface the injector consumes: ticks + readiness."""

    def __init__(self, session, regions, *, dt, with_receipt=True, decide=True):
        self.session = session
        self.regions = dict(regions)
        self.dt = dt
        self.readiness = _StubReadiness(with_receipt=with_receipt)
        self.decide = decide
        self.ticks = 0

    def step_once(self):
        self.ticks += 1
        now = round(self.ticks * self.dt, 9)
        current = set(self.session.report()["current_agents"])
        regions = {agent: region for agent, region in self.regions.items()
                   if agent in current}
        self.session.update_runtime_state(now, regions, {})
        gate = self.session.report()["deployment_assessment"]
        if self.decide and gate["status"] == "required" and not gate["terminated"]:
            _decide(self.session)
        return {"index": self.ticks - 1, "time_s": now}


def test_parse_script_validates_and_preserves_order():
    events = parse_injection_script([
        {"at_s": 74, "event_type": "member_exit", "agent_id": GO2},
        {"at_s": 78.5, "event_type": "capability_change", "agent_id": M20_1,
         "remove_capabilities": ["relay"]},
    ])
    assert [event.at_s for event in events] == [74.0, 78.5]
    assert events[1].remove_capabilities == ("relay",)


@pytest.mark.parametrize("rows, message", [
    ([], "at least one event"),
    ([{"at_s": 74, "event_type": "milestone_state_change", "agent_id": GO2}],
     "session contract"),
    ([{"at_s": 74, "event_type": "resource_status", "agent_id": GO2}], "unsupported"),
    ([{"at_s": 74, "event_type": "member_exit", "agent_id": "foreign"}], "full8"),
    ([{"at_s": 74, "event_type": "member_exit", "agent_id": GO2},
      {"at_s": 74, "event_type": "member_exit", "agent_id": M20_1}], "strictly increase"),
    ([{"at_s": 74, "event_type": "member_exit", "agent_id": GO2},
      {"at_s": 80, "event_type": "capability_change", "agent_id": M20_1}], "nonempty"),
    ([{"at_s": 74, "event_type": "member_exit", "agent_id": GO2,
       "remove_capabilities": ["relay"]}], "does not remove"),
    ([{"at_s": 0, "event_type": "member_exit", "agent_id": GO2}], "positive"),
])
def test_parse_script_rejects_invalid_rows(rows, message):
    with pytest.raises(ValueError, match=message):
        parse_injection_script(rows)


def test_established_facts_follow_the_readiness_service():
    assert established_readiness_facts({"receipt": {"x": 1}, "observations": {}}) == [
        "dining_ready", "kitchen_ready", "readiness_receipt_committed"]
    observations = {"a": {"region_id": "kitchen_service"},
                    "b": {"region_id": "dining_service"},
                    "c": {"region_id": "restaurant_staging"}}
    assert established_readiness_facts({"receipt": None,
                                        "observations": observations}) == [
        "dining_ready", "kitchen_ready"]
    assert established_readiness_facts({"receipt": None, "observations": {}}) == []


def test_member_exit_and_capability_change_freeze_collection_rows():
    session = _session()
    _decide(session)  # epoch-0 declaration at t=0
    pump = _StubPump(session, _spawn_regions(), dt=0.02, with_receipt=True,
                     decide=False)
    injector = ScriptedReassessmentInjector(
        _context_stub(session), pump,
        [{"at_s": 74.0, "event_type": "member_exit", "agent_id": GO2},
         {"at_s": 78.0, "event_type": "capability_change", "agent_id": M20_1,
          "remove_capabilities": ["relay"]}],
        episode_id="injection-test-episode")
    session.update_runtime_state(74.0, _spawn_regions(), {})
    first = injector.apply_event(injector.script[0])
    assert first["assessment_epoch"] == 1
    assert first["trigger_event_type"] == "member_exit"
    assert first["remaining_time_ms"] == 240_000 - 74_000
    assert GO2 not in first["current_agent_ids"]
    assert len(first["current_agent_ids"]) == 7
    assert set(first["runtime_state"]["robot_regions"]) == set(
        first["current_agent_ids"])
    assert first["runtime_state"]["time_s"] == 74.0
    assert first["established_facts"] == [
        "dining_ready", "kitchen_ready", "readiness_receipt_committed"]
    boundary_gate = first["oracle_boundary_session_report"]["deployment_assessment"]
    assert boundary_gate["status"] == "required"
    assert boundary_gate["external_evaluation"] is None
    assert boundary_gate["assessment_epoch"] == 1
    # The post-emit snapshot refresh clears the v3 stale-snapshot flag, so a
    # participant dispatch after the new decision is admissible again.
    assert session.report()["runtime_snapshot_refresh_required"] is False
    # The runtime evaluation stays fail-closed until an extension is issued.
    gate = session.report()["deployment_assessment"]
    assert gate["external_evaluation"]["evaluator_ref"] == (
        PENDING_EXTENSION_EVALUATOR_REF)
    assert gate["external_evaluation"]["feasibility_label"] is None
    _decide(session)
    regions = {agent: region for agent, region in _spawn_regions().items()
               if agent != GO2}
    session.update_runtime_state(78.0, regions, {})
    second = injector.apply_event(injector.script[1])
    assert second["assessment_epoch"] == 2
    assert second["trigger_event_type"] == "capability_change"
    assert second["roster_version"] == 2
    assert second["capability_versions"][M20_1] == 1
    pool = second["oracle_boundary_session_report"]["deployment_assessment"][
        "current_robot_pool"]
    assert "relay" not in pool[M20_1]["public_capabilities"]
    assert set(second["runtime_state"]["robot_regions"]) == set(
        second["current_agent_ids"]) == set(regions)
    _decide(session)
    collection = collect_reassessment_epoch_inputs(
        session.report(), episode_id="injection-test-episode")
    assert collection["epoch_count"] == 2
    assert [row["assessment_epoch"] for row in collection["run_state_domains"]] == [1, 2]
    assert [row["trigger_event_type"] for row in collection["run_state_domains"]] == [
        "member_exit", "capability_change"]
    review = review_reassessment_collection(
        collection, expected_episode_id="injection-test-episode")
    assert review["status"] == "verified"
    assert review["review_digest"]


def _context_stub(session):
    class _Context:
        pass
    context = _Context()
    context.session = session
    context.binding = {"episode_id": session.report()["episode_id"]}
    return context


def test_injector_loop_drives_stub_pump_until_all_epochs_decided():
    session = _session("loop-test-episode")
    pump = _StubPump(session, _spawn_regions(), dt=0.05, with_receipt=False)
    injector = ScriptedReassessmentInjector(
        _context_stub(session), pump,
        [{"at_s": 0.1, "event_type": "member_exit", "agent_id": GO2},
         {"at_s": 0.2, "event_type": "capability_change", "agent_id": M20_1,
          "remove_capabilities": ["relay"]}],
        episode_id="loop-test-episode")
    injection = injector.run(max_ticks=20)
    assert injection["injection_failures"] == []
    assert injection["decided_runtime_epochs"] == 2
    assert [record["assessment_epoch"] for record in injection["epoch_records"]] == [1, 2]
    # No receipt in the stub service: only the inspection facts are established.
    assert all(record["established_facts"] == ["dining_ready", "kitchen_ready"]
               for record in injection["epoch_records"])
    collection = injection["reassessment_collection"]
    review = review_reassessment_collection(collection,
                                            expected_episode_id="loop-test-episode")
    assert review["status"] == "verified"
    assert review["quality_failures"] == []
    assert review["review_digest"]


def test_apply_event_fails_closed_before_boundary_or_without_region():
    session = _session()
    _decide(session)
    pump = _StubPump(session, _spawn_regions(), dt=0.02, decide=False)
    injector = ScriptedReassessmentInjector(
        _context_stub(session), pump,
        [{"at_s": 74.0, "event_type": "member_exit", "agent_id": GO2}],
        episode_id="injection-test-episode")
    with pytest.raises(ValueError, match="before the scripted boundary"):
        injector.apply_event(injector.script[0])
    session.update_runtime_state(74.0, {}, {})
    with pytest.raises(ValueError, match="no measured region"):
        injector.apply_event(injector.script[0])


def test_issue_and_verify_end_to_end_against_synthetic_certificate():
    from test_benchmark_goal_restaurant_reassessment import _context

    ctx = _context()
    session = _session("synthetic-r1-reassessment")
    _decide(session)
    pump = _StubPump(session, _spawn_regions(), dt=0.02, with_receipt=True,
                     decide=False)
    injector = ScriptedReassessmentInjector(
        _context_stub(session), pump,
        [{"at_s": 74.0, "event_type": "member_exit", "agent_id": GO2},
         {"at_s": 78.0, "event_type": "capability_change", "agent_id": M20_1,
          "remove_capabilities": ["relay"]}],
        episode_id="synthetic-r1-reassessment")
    session.update_runtime_state(74.0, _spawn_regions(), {})
    injector.apply_event(injector.script[0])
    _decide(session)
    regions = {agent: region for agent, region in _spawn_regions().items()
               if agent != GO2}
    session.update_runtime_state(78.0, regions, {})
    injector.apply_event(injector.script[1])
    _decide(session)
    collection = collect_reassessment_epoch_inputs(
        session.report(), episode_id="synthetic-r1-reassessment")
    review = review_reassessment_collection(collection,
                                            expected_episode_id="synthetic-r1-reassessment")
    extension_input = build_extension_input(
        collection, review, certificate_sha256=ctx["certificate_sha256"])
    facts = {record["assessment_epoch"]: record["established_facts"]
             for record in injector.records}
    raw = canonical_sha256({"synthetic_raw_archive": True})
    issuance = issue_reassessment_extension(
        extension_input, certificate=ctx["certificate"],
        established_facts_by_epoch=facts, host="local", seed=17,
        raw_archive_sha256=raw,
        runtime_profile_sha256=ctx["authorities"]["runtime_profile_sha256"])
    assert issuance["extension"]["schema"] == "eai.restaurant.reassessment_extension"
    oracle = RestaurantReassessmentOracle(
        ctx["certificate"], expected_certificate_sha256=ctx["certificate_sha256"],
        trusted_authorities=ctx["authorities"],
        extensions=[issuance["extension"]],
        expected_extension_sha256=[issuance["extension_sha256"]])
    for record in injector.records:
        result = oracle("synthetic-r1-reassessment",
                        {"session": record["oracle_boundary_session_report"]})
        assert result["oracle_status"] == "verified"
        assert result["feasibility_label"] == "deployable"
        assert result["platform_valid"] is True
    report = oracle.reassessment_report()
    assert report["configuration_status"] == "validated"
    assert report["release_ready"] is False and report["scoreable"] is False
    assert json.dumps(report).count("deployable") >= 2


def test_issue_rejects_foreign_runtime_profile_and_unknown_facts():
    from test_benchmark_goal_restaurant_reassessment import _context

    ctx = _context()
    collection = collect_reassessment_epoch_inputs(
        {"episode_id": "synthetic-r1-reassessment", "report": {
            "episode_id": "synthetic-r1-reassessment",
            "deployment_assessment": {"history": [{
                "assessment_epoch": 1, "trigger_event_type": "member_exit",
                "trigger_event_sequence": 11, "triggered_at_s": 74.0,
                "current_agent_ids": [
                    agent for agent in sorted(_spawn_regions()) if agent != GO2],
                "roster_version": 1,
                "capability_versions": {
                    agent: 0 for agent in sorted(_spawn_regions()) if agent != GO2},
                "resource_versions": {}, "deployment_change_versions": {},
            }]}}}, episode_id="synthetic-r1-reassessment")
    review = review_reassessment_collection(collection)
    assert review["status"] == "verified"
    extension_input = build_extension_input(
        collection, review, certificate_sha256=ctx["certificate_sha256"])
    with pytest.raises(ValueError, match="outside the certificate allowlist"):
        issue_reassessment_extension(
            extension_input, certificate=ctx["certificate"],
            established_facts_by_epoch={1: ["kitchen_ready"]}, host="local",
            seed=17, raw_archive_sha256="a" * 64,
            runtime_profile_sha256="b" * 64)
    with pytest.raises(ValueError, match="unknown facts"):
        issue_reassessment_extension(
            extension_input, certificate=ctx["certificate"],
            established_facts_by_epoch={1: ["not_a_fact"]}, host="local",
            seed=17, raw_archive_sha256="a" * 64,
            runtime_profile_sha256=ctx["authorities"]["runtime_profile_sha256"])
    with pytest.raises(ValueError, match="no established facts"):
        issue_reassessment_extension(
            extension_input, certificate=ctx["certificate"],
            established_facts_by_epoch={}, host="local", seed=17,
            raw_archive_sha256="a" * 64,
            runtime_profile_sha256=ctx["authorities"]["runtime_profile_sha256"])
