"""Requested-target public measurements, route proposals and execution failures."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from demo.benchmark.goal_city_assembly import CityRequestedTargetRouteProvider
from demo.benchmark.goal_city_autonomous import build_city_autonomous_contract
from demo.benchmark.goal_city_episode import CityNativeGoalBackend
from demo.benchmark.goal_city_runtime_observations import project_city_observation
from demo.city_benchmark.guide_admission import CityGuideAdmissionError
from source.EAI.test.test_benchmark_goal_city_runtime_observations import _snapshot
from source.EAI.test.test_benchmark_goal_city_runtime_observations import (
    PublicObservationAdapter, _native_context, _runtime)
from source.EAI.test.test_benchmark_goal_city_start_clock import _twenty_second_guide
from source.EAI.test.test_benchmark_goal_city_episode import _make_ctx, _trusted_report_for


def _raw_affordance(public, row):
    ids = [r["id"] for r in public["public_world"]["regions"] if r["kind"] == "assembly_candidate"]
    people = [e["id"] for e in row["entities"] if e["kind"] == "person"]
    return {"schema": "eai.city.assembly_affordance", "version": 1,
            "profile": "requested_target_v1", "episode_id": row["episode_id"],
            "source_id": row["source_id"], "captured_at_s": row["captured_at_s"],
            "valid_until_s": row["captured_at_s"] + .8, "semantic_map_sha256": "a" * 64,
            "private_binding": "DO_NOT_PUBLISH",
            "candidates": [{"region_id": region, "min": [0, 0, 0], "max": [2, 2, 3],
                            "capacity": 1, "occupancy_count": 0, "reservation_count": 0,
                            "status": "measured", "reason_codes": [],
                            "bound_person_ids": ["DO_NOT_PUBLISH"]} for region in ids],
            "routes": [{"person_id": person, "region_id": region, "status": "admissible",
                        "reason_codes": [], "length_m": 3., "estimated_travel_s": 2.,
                        "evidence_ref": f"measured:{person}:{region}", "state_sha256": "b" * 64,
                        "assembly_waypoints": ["DO_NOT_PUBLISH"]}
                       for person in people for region in ids]}


def _project(public, row):
    return project_city_observation(row, public, native_episode_id="native-episode",
                                    native_source_id="isaac.city.runtime", clock_epoch_s=10.,
                                    recipient_service_id="city_dispatch")


def test_affordance_projection_keeps_all_choices_without_private_binding():
    public, _ = build_city_autonomous_contract()
    row = _snapshot()
    row["assembly_affordance"] = _raw_affordance(public, row)
    result = _project(public, row)
    assert result["version"] == 2
    assert result["valid_until_s"] == pytest.approx(.8)
    assert len(result["assembly_affordance"]["routes"]) == 4
    assert "DO_NOT_PUBLISH" not in json.dumps(result)
    row["assembly_affordance"]["routes"].reverse()
    row["assembly_affordance"]["candidates"].reverse()
    assert result == _project(public, row)


@pytest.mark.parametrize("case", ["stale", "partial", "private_reason", "capacity", "foreign"])
def test_bad_affordance_never_crosses_public_boundary(case):
    public, _ = build_city_autonomous_contract()
    row = _snapshot()
    raw = row["assembly_affordance"] = _raw_affordance(public, row)
    if case == "stale":
        raw["captured_at_s"] -= .1
    elif case == "partial":
        raw["routes"].pop()
    elif case == "private_reason":
        raw["routes"][0]["reason_codes"] = ["private route detail"]
    elif case == "capacity":
        raw["candidates"][0]["capacity"] = 2
    else:
        raw["episode_id"] = "foreign"
    with pytest.raises(ValueError):
        _project(public, row)


def _route(provider, start, goal):
    return provider(person_id="person", current_xyz=start, target_region_id="target",
                    target_xyz=goal, body_half_extents_m=[.3, .3, .9], body_center_offset_m=[0, 0, .9])


def test_route_proposal_preserves_exact_pose_and_requested_destination():
    obstacles = [{"kind": "building", "bounds_m": [[-.5, -.5, 0], [.5, .5, 3]]}]
    provider = CityRequestedTargetRouteProvider({"static_obstacles": obstacles,
                                                "author_person_target": "unused"})
    start, goal = (-2.1, .1, .3), (2.1, .1, .3)
    result = _route(provider, start, goal)
    assert result[0] == start and result[-1] == goal
    assert any(abs(point[1]) > .5 for point in result)
    assert _route(provider, (0., 0., .3), goal) is None  # Never relocate blocked start.
    assert _route(provider, start, (2.1, .1, 1.3)) is None  # No invented stairs.


@pytest.mark.parametrize("infrastructure,qualification", [(False, False), (True, False), (False, True)])
def test_typed_guide_rejection_is_not_silently_retried(tmp_path, infrastructure, qualification):
    public, _ = build_city_autonomous_contract()
    task = _twenty_second_guide(public)
    ctx, runner, steps, _roles = _make_ctx(tmp_path, start_s=10.)
    calls = []

    def reject(*args, **kwargs):
        calls.append(kwargs)
        raise CityGuideAdmissionError(
            ["target_body_outside_region" if qualification else
             "route_measurement_unknown" if infrastructure else "target_capacity_full"],
            infrastructure_failure=infrastructure, report={"private": "DO_NOT_PUBLISH"})

    runner.guide_people = reject
    backend = CityNativeGoalBackend(ctx, public_task=public, clock_epoch_s=10., strict_dispatch_clock=True)
    trusted = _trusted_report_for(task, episode_id="rejected", current_time_s=0.)
    if infrastructure or qualification:
        with pytest.raises(CityGuideAdmissionError):
            backend.execute(task, episode_id="rejected", trusted_report=trusted)
    else:
        result = backend.execute(task, episode_id="rejected", trusted_report=trusted)
        assert result["status"] == "failed" and result["completed_at_s"] == 0.
        assert result["city_admission_outcome"]["reason_codes"] == ["target_capacity_full"]
        assert "DO_NOT_PUBLISH" not in json.dumps(result)
    assert len(calls) == 1 and not steps
    record = next(r for r in backend.evidence if r["kind"] == "guide_admission_failure")
    assert record["classification"] == (
        "infrastructure_evidence_failure" if infrastructure or qualification else "execution_condition_failure")


@pytest.mark.parametrize("case", ["unchanged", "blind", "changed", "expired", "conflict", "unavailable"])
def test_optional_cited_affordance_is_bound_to_executed_state(tmp_path, case):
    public, _ = build_city_autonomous_contract()
    task = _twenty_second_guide(public)
    ctx, _runner, _steps, _roles = _make_ctx(tmp_path, start_s=10.)
    person, region = task["arguments"]["person_ids"][0], task["arguments"]["assembly_region_id"]
    route = {"person_id": person, "region_id": region, "status": "admissible", "state_sha256": "a" * 64}
    snapshot = {"captured_at_s": 0., "valid_until_s": 1., "routes": [route]}
    current = deepcopy(snapshot)
    ctx["city"]._episode_service = SimpleNamespace(
        guidance_profile="requested_target_v1", assembly_affordances=lambda: current)
    backend = CityNativeGoalBackend(ctx, public_task=public, clock_epoch_s=10.)
    task["evidence_refs"] = [] if case == "blind" else ["measurement"]
    report = _trusted_report_for(task, episode_id="evidence", current_time_s=0.)["session"]
    report["consumed_message_ids"] = ["measurement"]
    payload = {"message_id": "measurement", "valid_until_s": 1.,
               "data": {"estimate": {"assembly_affordance": snapshot}}}
    report["events"] = [{"type": "observation", "payload": payload}]
    if case == "changed":
        current["routes"][0]["state_sha256"] = "b" * 64
    if case == "unavailable":
        current["routes"][0].update(status="unavailable", state_sha256="b" * 64,
                                     reason_codes=["route_measurement_unknown"])
    if case == "expired":
        ctx["city"].elapsed = 11.
    if case == "conflict":
        extra = deepcopy(payload)
        extra["message_id"] = "other"
        extra["data"]["estimate"]["assembly_affordance"]["routes"][0]["state_sha256"] = "c" * 64
        task["evidence_refs"].append("other")
        report["consumed_message_ids"].append("other")
        report["events"].append({"type": "observation", "payload": extra})
    dispatch = report["tasks"][task["task_id"]]
    if case == "unavailable":
        with pytest.raises(CityGuideAdmissionError) as caught:
            backend._check_public_guide_evidence(task, dispatch, report)
        assert caught.value.infrastructure_failure
        assert caught.value.reason_codes == ("route_measurement_unknown",)
    elif case in {"changed", "expired", "conflict"}:
        with pytest.raises(CityGuideAdmissionError) as caught:
            backend._check_public_guide_evidence(task, dispatch, report)
        assert not caught.value.infrastructure_failure
        assert caught.value.reason_codes == ("affordance_expired" if case == "expired" else "affordance_changed",)
    else:
        backend._check_public_guide_evidence(task, dispatch, report)
        assert (backend._guide_evidence_checked is None) == (case == "blind")


@pytest.mark.parametrize("claim", ["occupant_ids", "reservation_ids"])
def test_cited_individual_admission_does_not_guarantee_batch_capacity(tmp_path, claim):
    public, _ = build_city_autonomous_contract()
    task = _twenty_second_guide(public)
    task["arguments"]["person_ids"] = ["person_a", "person_b"]
    task["evidence_refs"] = ["measurement"]
    region = task["arguments"]["assembly_region_id"]
    ctx, runner, _steps, _roles = _make_ctx(tmp_path, start_s=10.)
    routes = [{"person_id": person, "region_id": region, "status": "admissible",
               "state_sha256": "a" * 64} for person in task["arguments"]["person_ids"]]
    candidate = {"region_id": region, "capacity": 2, "occupant_ids": [],
                 "reservation_ids": [], "occupancy_count": 0, "reservation_count": 0}
    candidate[claim] = ["existing_person"]
    candidate["occupancy_count" if claim == "occupant_ids" else "reservation_count"] = 1
    current = {"captured_at_s": 0., "valid_until_s": 1., "routes": routes,
               "candidates": [candidate]}
    ctx["city"]._episode_service = SimpleNamespace(
        guidance_profile="requested_target_v1", assembly_affordances=lambda: current)
    backend = CityNativeGoalBackend(ctx, public_task=public, clock_epoch_s=10.)
    trusted = _trusted_report_for(task, episode_id="batch", current_time_s=0.)
    ledger = trusted["session"]
    ledger["consumed_message_ids"] = ["measurement"]
    ledger["events"] = [{"type": "observation", "payload": {
        "message_id": "measurement", "valid_until_s": 1.,
        "data": {"estimate": {"assembly_affordance": deepcopy(current)}}}}]

    def reject(*args, **kwargs):
        raise CityGuideAdmissionError(["target_capacity_full"],
                                      infrastructure_failure=False, report={})

    runner.guide_people = reject
    result = backend.execute(task, episode_id="batch", trusted_report=trusted)
    assert result["status"] == "failed"
    assert not backend._guide_evidence_checked["batch_fits"]
    assert backend.evidence[-1]["classification"] == "execution_condition_failure"


@pytest.mark.parametrize("case", ["invalid_target", "invalid_person", "missing_native_target", "missing_native_person"])
def test_requested_public_identity_errors_are_separate_from_native_contract_gaps(tmp_path, case):
    public, _ = build_city_autonomous_contract()
    task = _twenty_second_guide(public)
    ctx, runner, steps, _roles = _make_ctx(tmp_path, start_s=10.)
    ctx["city"]._episode_service.guidance_profile = "requested_target_v1"
    person = task["arguments"]["person_ids"][0]
    runner.host.observation = lambda ref: {"entities": [{"id": person, "kind": "person"}]}
    if case == "invalid_target":
        task["arguments"]["assembly_region_id"] = "not_a_public_candidate"
    if case == "invalid_person":
        task["arguments"]["person_ids"] = ["invented_person"]
    calls = []

    def reject(*args, **kwargs):
        calls.append(kwargs)
        raise CityGuideAdmissionError(
            ["target_region_unknown" if case == "missing_native_target" else "person_not_in_roster"],
            infrastructure_failure=True, report={})

    runner.guide_people = reject
    backend = CityNativeGoalBackend(ctx, public_task=public, clock_epoch_s=10.)
    trusted = _trusted_report_for(task, episode_id="identity", current_time_s=0.)
    if case.startswith("missing_native"):
        with pytest.raises(CityGuideAdmissionError) as caught:
            backend.execute(task, episode_id="identity", trusted_report=trusted)
        assert caught.value.infrastructure_failure and len(calls) == 1
    else:
        result = backend.execute(task, episode_id="identity", trusted_report=trusted)
        assert result["status"] == "failed" and not calls
        assert backend.evidence[-1]["classification"] == "execution_condition_failure"
    assert not steps


class AssemblyChoiceAdapter(PublicObservationAdapter):
    """Partial diagnostic; both choices depend only on received measurements."""

    def __init__(self, member_index=0, *, preference="shortest"):
        super().__init__(member_index)
        self.preference = preference
        self.chosen = None

    def act(self, events):
        if self.assessed and not self.planned:
            estimate = self.message["data"]["estimate"]
            routes = estimate["assembly_affordance"]["routes"]
            choices = [row for row in routes if row["status"] == "admissible"]
            if not choices:
                raise RuntimeError("diagnostic has no measured admissible person-target pair")
            ordering = ((lambda r: (r["length_m"], r["person_id"], r["region_id"]))
                        if self.preference == "shortest" else
                        (lambda r: (r["region_id"], r["person_id"])))
            selected = min(choices, key=ordering)
            self.chosen = deepcopy(selected)
            # Base helper emits one participant-created task from its first
            # observed person/candidate. Reorder only our local received copy.
            estimate["entities"].sort(key=lambda row: row["id"] != selected["person_id"])
            estimate["assembly_candidates"].sort(
                key=lambda row: row["region_id"] != selected["region_id"])
        return super().act(events)


@pytest.mark.parametrize("member,preference", [(0, "shortest"), (1, "candidate_id")])
def test_requested_target_profile_integrates_with_metered_runner(tmp_path, member, preference):
    from demo.benchmark.goal_city_autonomous import run_city_autonomous_context

    public, _ = build_city_autonomous_contract()
    ctx, _steps = _native_context(tmp_path / "ctx")
    enabled = []

    def raw():
        row = _snapshot(ctx["city"].elapsed)
        result = _raw_affordance(public, row)
        result["routes"][-1]["length_m"] = 1.
        return result

    service = SimpleNamespace(guidance_profile="requested_target_v1",
                              enable_requested_target_guidance=lambda **kwargs: enabled.append(kwargs),
                              assembly_affordances=raw,
                              people_controller=ctx["city"]._episode_service.people_controller,
                              assembly_regions=ctx["city"]._episode_service.assembly_regions)
    ctx["city"]._episode_service = service
    adapter = AssemblyChoiceAdapter(member, preference=preference)
    runtime = _runtime()
    result, _backend = run_city_autonomous_context(
        ctx, adapter, output_root=tmp_path / "episode", episode_id="requested-target",
        communication_runtime=runtime,
        guide_route_provider=CityRequestedTargetRouteProvider({"static_obstacles": []}))
    assert enabled and result["error"] is None
    assert result["skill_receipt_count"] == 1
    assert adapter.chosen["region_id"] == ("assembly_candidate_004" if preference == "shortest"
                                           else "assembly_candidate_001")
    assert adapter.plan["tasks"][0]["assigned_agents"] == [public["candidate_robot_pool"][member]["id"]]
    assert runtime.report()["messages"]["requested-target:city-runtime:0"]["recipients"]["cloud"]["consumption"]
    assert not result["scoreable"]


def test_failed_admission_reason_is_metered_without_renewing_snapshot(tmp_path, monkeypatch):
    from demo.benchmark.goal_city_autonomous import run_city_autonomous_context
    from demo.benchmark import goal_city_episode

    evaluated = []
    original = goal_city_episode.evaluate_city_acceptance

    def capture(envelope, *args, **kwargs):
        result = original(envelope, *args, **kwargs)
        evaluated.append((deepcopy(envelope), result))
        return result

    monkeypatch.setattr(goal_city_episode, "evaluate_city_acceptance", capture)

    ctx, _ = _native_context(tmp_path / "ctx")

    def reject(*args, **kwargs):
        raise CityGuideAdmissionError(["target_capacity_full"], infrastructure_failure=False,
                                      report={"private": "DO_NOT_PUBLISH"})

    ctx["runner"].guide_people = reject
    runtime, adapter = _runtime(), PublicObservationAdapter()
    root = tmp_path / "episode"
    result, backend = run_city_autonomous_context(
        ctx, adapter, output_root=root, episode_id="rejection", communication_runtime=runtime)
    assert result["error"] is None and result["skill_receipt_count"] == 1
    outcomes = [m for m in runtime.report()["messages"].values()
                if "skill_outcome" in m["payload"].get("estimate", {})]
    assert len(outcomes) == 1
    payload = outcomes[0]["payload"]
    assert outcomes[0]["size_bytes"] > 0 and outcomes[0]["direction"] == "uplink"
    assert payload["estimate"]["skill_outcome"]["reason_codes"] == ["target_capacity_full"]
    assert "DO_NOT_PUBLISH" not in json.dumps(payload)
    assert payload["valid_until_s"] == 1.  # Original capture expiry, not a refresh.
    assert any("skill_outcome" in message["data"]["estimate"] for message in adapter.observations)
    # The context entry has no native archive wrapper. Inspect the same raw
    # envelope producer used by that wrapper, without normalizing its values.
    raw = goal_city_episode.build_acceptance_evidence(
        backend.evidence, ctx, clock_epoch_s=backend.clock_epoch_s)
    assert raw["responder_dispatch"]["arrival_time_s"] is None
    assert evaluated and "arrival_time_s" not in evaluated[-1][0]["responder_dispatch"]
    assert "responder_timing_missing" in json.dumps(evaluated[-1][1])
