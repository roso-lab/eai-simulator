"""Public native intake, communication causality and no-author-plan regressions."""
from copy import deepcopy
import json

import pytest

from demo.benchmark.communication import CommunicationRuntime, validate_communication_report
from demo.benchmark.goal_city_autonomous import (
    build_city_autonomous_contract, run_city_autonomous_context)
from demo.benchmark.goal_city_runtime_observations import project_city_observation
from demo.benchmark.goal_driven_contract import GOAL_PLAN_VERSION
from demo.benchmark.skill_ontology import required_capabilities
from source.EAI.test.test_benchmark_goal_city_autonomous import _action, _manifest
from source.EAI.test.test_benchmark_goal_city_episode import _make_ctx, ROSTER_IDS, VEHICLE_IDS


def _runtime(*, delay=0.0, drop=False, ttl=None):
    uplink = {"fixed_delay_s": delay, "active_drop": drop}
    if ttl is not None:
        uplink["ttl_s"] = ttl
    return CommunicationRuntime({"pools": {"backhaul_uplink": uplink,
                                            "backhaul_downlink": {}}})


def _snapshot(time_s=10.0):
    return {"id": f"native-observation-{time_s}", "episode_id": "native-episode",
            "source_id": "isaac.city.runtime", "producer_agent_id": "city_runtime",
            "region_id": "road_surface", "captured_at_s": time_s,
            "delivered_at_s": time_s, "valid_until_s": time_s + 1.0,
            "private_plan": "DO_NOT_PUBLISH",
            "entities": [
                {"id": ROSTER_IDS[0], "kind": "person", "position_xyz": [1., 2., 0.],
                 "episode_id": "native-episode", "source_id": "isaac.city.runtime",
                 "assembly_region_id": "DO_NOT_PUBLISH", "mode": "DO_NOT_PUBLISH"},
                {"id": VEHICLE_IDS[0], "kind": "vehicle", "position_xyz": [-1., 4., 0.],
                 "episode_id": "native-episode", "source_id": "isaac.city.runtime",
                 "following_incident": True, "is_accident_vehicle": False,
                 "assignment_rows": {"agent_id": "DO_NOT_PUBLISH"}}]}


def _native_context(path):
    ctx, runner, steps, roles = _make_ctx(path, start_s=10.0)
    runner.adapter.episode_id = "native-episode"
    runner.adapter.source_id = "isaac.city.runtime"
    observations = {}

    def observe():
        row = _snapshot(float(ctx["city"].elapsed))
        assert row["id"] not in observations, "duplicate same-tick native capture"
        observations[row["id"]] = row
        runner._observation_ref = row["id"]
        return row["id"]

    runner.observe = observe
    runner.host.observation = lambda ref: deepcopy(observations.get(ref))
    ctx["guidance_observation"] = observe()
    return ctx, steps


class PublicObservationAdapter:
    """Diagnostic partial policy built only from initialize and delivered data."""
    def __init__(self, member_index=0):
        self.index = member_index
        self.observations = []
        self.assessed = self.planned = self.dispatched = False
        self.gate = self.plan = self.message = None
        self.calls = []

    def manifest(self):
        return _manifest(f"public-intake-{self.index}")

    def initialize(self, public_task, participant_api):
        self.public = deepcopy(public_task)

    def act(self, events):
        now = max(e["time_s"] for e in events)
        self.calls.append(now)
        for event in events:
            if event["type"] == "deployment_assessment_required":
                self.gate = event["payload"]
            elif event["type"] == "observation":
                payload = event["payload"]
                estimate = payload.get("data", {}).get("estimate", {})
                if estimate.get("kind") == "city_runtime_observation":
                    self.observations.append(deepcopy(payload))
                    self.message = payload
        if not self.assessed:
            if self.message is None:
                return []
            self.assessed = True
            gate = self.gate
            return [
                _action("consume-initial", "consume_message", {
                    "message_id": self.message["message_id"]}, now),
                _action("assessment", "deployment_assessment", {
                    **{k: gate[k] for k in ("assessment_id", "assessment_epoch",
                                           "roster_version", "capability_versions")},
                    "decision": "deployable", "reason_codes": [], "evidence_refs": []}, now)]
        if not self.planned:
            estimate = self.message["data"]["estimate"]
            person = next(e["id"] for e in estimate["entities"] if e["kind"] == "person")
            agent = self.public["candidate_robot_pool"][self.index]["id"]
            message_id = self.message["message_id"]
            self.plan = {
                "schema": "eai.benchmark.goal_plan", "version": GOAL_PLAN_VERSION,
                "plan_id": "public-plan", "assigned_team": [agent],
                "tasks": [{"task_id": "public-guide", "skill": "guide_people",
                           "assigned_agents": [agent], "prerequisites": [],
                           "required_capabilities": sorted(required_capabilities("guide_people")),
                           "evidence_refs": [message_id],
                           "arguments": {"person_ids": [person], "observation_refs": [message_id],
                                         "assembly_region_id": estimate["assembly_candidates"][0]["region_id"]},
                           "execution": {"start_s": now, "duration_s": 30.0}}]}
            self.planned = True
            return [_action("select", "select_team", {"agent_ids": [agent]}, now),
                    _action("plan", "submit_plan", {"plan": self.plan}, now)]
        if not self.dispatched:
            self.dispatched = True
            return [_action("dispatch", "dispatch_skill", {
                "task_id": "public-guide", "evidence_refs": [self.message["message_id"]]}, now)]
        return []

    def close(self):
        pass


def test_projection_strips_hidden_fields_and_preserves_source_age():
    public, _api = build_city_autonomous_contract()
    row = _snapshot()
    result = project_city_observation(
        row, public, native_episode_id="native-episode", native_source_id="isaac.city.runtime",
        clock_epoch_s=10., recipient_service_id="city_dispatch")
    assert "DO_NOT_PUBLISH" not in json.dumps(result)
    assert result["captured_at_s"] == 0.0 and result["valid_until_s"] == 1.0
    assert result["entities"][0]["position_m"] == [1., 2., 0.]
    assert result["assembly_candidates"][0]["safety_status"] == "unverified"
    reversed_row = deepcopy(row)
    reversed_row["entities"].reverse()
    assert result == project_city_observation(
        reversed_row, public, native_episode_id="native-episode", native_source_id="isaac.city.runtime",
        clock_epoch_s=10., recipient_service_id="city_dispatch")
    for key in ("following_incident", "is_accident_vehicle"):
        incomplete = deepcopy(row)
        del incomplete["entities"][1][key]
        with pytest.raises(ValueError, match=key):
            project_city_observation(
                incomplete, public, native_episode_id="native-episode",
                native_source_id="isaac.city.runtime", clock_epoch_s=10.,
                recipient_service_id="city_dispatch")
    row["entities"][0]["episode_id"] = "foreign"
    with pytest.raises(ValueError, match="entity source/episode"):
        project_city_observation(
            row, public, native_episode_id="native-episode", native_source_id="isaac.city.runtime",
            clock_epoch_s=10., recipient_service_id="city_dispatch")


@pytest.mark.parametrize("delay,member", [(0.0, 0), (0.2, 1)])
def test_public_intake_reaches_actual_runner_and_is_metered_and_consumed(tmp_path, delay, member):
    ctx, steps = _native_context(tmp_path / "ctx")
    original_step = ctx["step"]
    adapter = PublicObservationAdapter(member)
    runtime = _runtime(delay=delay)
    root = tmp_path / "episode"
    result, backend = run_city_autonomous_context(
        ctx, adapter, output_root=root, episode_id="public-intake", communication_runtime=runtime)
    assert result["error"] is None
    assert result["skill_receipt_count"] == 1
    assert result["session_audit_validated"] is True
    assert ctx["step"] is original_step
    assert adapter.observations and "DO_NOT_PUBLISH" not in json.dumps(adapter.observations)
    transport = json.loads((root / "communication_transport_report.json").read_text())
    validate_communication_report(transport)
    initial = transport["messages"]["public-intake:city-runtime:0"]
    assert initial["size_bytes"] > 0
    assert initial["direction"] == "uplink"
    assert initial["recipients"]["cloud"]["consumption"] is not None
    observer = json.loads((root / "execution_observer_report.json").read_text())
    assert observer["consumed_action_ids"] == ["consume-initial"]
    assert observer["samples"][0]["public_projection"]["captured_at_s"] == 0.0
    if delay:
        assert adapter.calls[0] == 0.0
        assert initial["recipients"]["cloud"]["delivered_at_s"] >= delay
    assert any(row["kind"] == "dispatch_clock" for row in backend.evidence)
    assert result["scoreable"] is False


@pytest.mark.parametrize("kwargs", [{"drop": True}, {"delay": 1.2}, {"delay": .2, "ttl": .1}])
def test_lost_or_expired_intake_never_reaches_participant(tmp_path, kwargs):
    ctx, _steps = _native_context(tmp_path / "ctx")
    adapter = PublicObservationAdapter()
    root = tmp_path / "episode"
    result, backend = run_city_autonomous_context(
        ctx, adapter, output_root=root, episode_id="lost-intake", communication_runtime=_runtime(**kwargs))
    assert "missing_deployment_assessment" in result["error"]
    assert adapter.observations == []
    assert result["skill_receipt_count"] == 0
    assert not any(row.get("kind") == "guide" for row in backend.evidence)
    report = json.loads((root / "execution_observer_report.json").read_text())
    assert len(report["samples"]) == 1  # no silent freshness refresh


def test_bad_transport_configuration_restores_context_step(tmp_path):
    ctx, _steps = _native_context(tmp_path / "ctx")
    original = ctx["step"]
    with pytest.raises(ValueError, match="backhaul_uplink"):
        run_city_autonomous_context(
            ctx, PublicObservationAdapter(), output_root=tmp_path / "episode",
            communication_runtime=CommunicationRuntime())
    assert ctx["step"] is original
