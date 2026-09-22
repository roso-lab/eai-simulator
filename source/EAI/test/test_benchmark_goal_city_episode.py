"""Offline tests for the formal City GoalSession episode layer.

Everything here runs without Isaac/Kit: the native runner, city runtime and
simulation stepping are fakes injected through the ``driver`` context dict that
``demo.city_benchmark.service_probe._run_bound_episode`` would provide, while
the GoalSession admission chain, the FormalGoalEpisodeRunner loop and every
contract validator are the real implementations.
"""

from copy import deepcopy
import inspect
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from demo.benchmark.goal_city_episode import (
    stamp_arrival_times,
    CITY_BACKEND_ID,
    CITY_EVALUATOR_REF,
    CITY_EPISODE_PLAN_ID,
    CITY_NATIVE_OBSERVATION_REF,
    CITY_ROBOT_REGIONS,
    CityNativeGoalBackend,
    CityWitnessParticipant,
    build_acceptance_evidence,
    build_city_episode_plan,
    city_business_evaluator,
)
from demo.benchmark.goal_driven_tasks import (
    _CITY_NATIVE_AFFECTED_PERSON_ASSEMBLY,
    _CITY_NATIVE_FOLLOWING_VEHICLE_IDS,
    build_goal_task_package,
)
from demo.benchmark.goal_episode_runner import FormalGoalEpisodeRunner
from demo.city_benchmark import service_probe
from demo.city_benchmark.task_logic import default_deadline_policy, evaluate_city_acceptance


ROSTER_IDS = sorted(_CITY_NATIVE_AFFECTED_PERSON_ASSEMBLY)
VEHICLE_IDS = list(_CITY_NATIVE_FOLLOWING_VEHICLE_IDS)
DT = 0.05


class _FakePeople:
    def __init__(self):
        self.resumed: list[str] = []

    def resume(self, person_id):
        self.resumed.append(person_id)


class _FakePeopleController:
    def __init__(self, arrived=True, city=None, arrive_at_s=None):
        self._arrived = arrived
        self._city = city
        self.arrive_at_s = arrive_at_s

    def readback(self, person_id, regions):
        if self.arrive_at_s is not None and self._city is not None:
            return {"arrived": float(self._city.elapsed) >= float(self.arrive_at_s)}
        return {"arrived": self._arrived}


class _FakeCity:
    def __init__(self, start_s=10.0):
        self.elapsed = float(start_s)
        self.people = _FakePeople()
        self._episode_service = SimpleNamespace(
            people_controller=_FakePeopleController(city=self), assembly_regions={})

    def following_vehicle_pose(self, vehicle_id):
        return (0.0, 0.0, 0.0)


class _FakeAssignmentAdapter:
    def __init__(self, runner):
        self._runner = runner

    def now_s(self):
        return float(self._runner.city.elapsed)


class _FakeRunner:
    """Minimal native runner double: request rows, acks, readbacks, reviews."""

    def __init__(self, city, *, guide_not_admitted_first=0, omit_contact_acks=()):
        self.city = city
        self.calls: list[tuple] = []
        self.agent_id = "go2_1"
        self.adapter = _FakeAssignmentAdapter(self)
        self.host = SimpleNamespace(
            record_role_occupancy=lambda *a, **k: self.calls.append(
                ("record_role", a[0], a[1])))
        self._request_count = 0
        self._observation_count = 0
        self._guide_failures = int(guide_not_admitted_first)
        self._omit_acks = set(omit_contact_acks)
        self._guide_requests: dict[str, dict] = {}
        self._traffic_requests: dict[str, dict] = {}

    def observe(self):
        self._observation_count += 1
        return f"city_native_observation_{self._observation_count:09d}"

    def _request_row(self, service, *, entity_ids, observation_ref, mode=None,
                     recipient=None, target_region_id=None):
        self._request_count += 1
        return {"request_id": f"city_service_{self._request_count:04d}",
                "service": service, "agent_id": "go2_1", "region_id": "road_surface",
                "entity_ids": list(entity_ids),
                "observation_refs": [observation_ref], "mode": mode,
                "recipient_service_id": recipient, "target_region_id": target_region_id,
                "submitted_time_s": float(self.city.elapsed), "status": "pending",
                "evidence_class": "request_only", "scoreable": False}

    def guide_people(self, person_ids, *, observation_ref, assembly_region_id):
        self.calls.append(("guide_people", tuple(person_ids), observation_ref,
                           assembly_region_id))
        if self._guide_failures > 0:
            self._guide_failures -= 1
            raise ValueError(
                "service route, authority or capacity not admitted: "
                + json.dumps({"service": "guide_people"}))
        row = self._request_row("guide_people", entity_ids=person_ids,
                                observation_ref=observation_ref,
                                target_region_id=assembly_region_id)
        self._guide_requests[row["request_id"]] = row
        return {"request": row, "actions": [], "scoreable": False}

    def guidance_contact(self, request_id):
        row = self._guide_requests[request_id]
        now = float(self.city.elapsed)
        acks = [{"id": person_id, "ack_time_s": now,
                 "ack_evidence_ref": f"{request_id}:{person_id}:ack"}
                for person_id in row["entity_ids"] if person_id not in self._omit_acks]
        complete = len(acks) == len(row["entity_ids"])
        return {"request_id": request_id,
                "status": "contact_confirmed" if complete else "pending",
                "agent_id": "go2_1", "ack_time_s": now if complete else None,
                "evidence_refs": [ack["ack_evidence_ref"] for ack in acks],
                "actor_acks": acks, "business_outcome_validated": False,
                "scoreable": False}

    def control_following_traffic(self, vehicle_ids, mode, *, observation_ref):
        self.calls.append(("control_following_traffic", tuple(vehicle_ids), mode,
                           observation_ref))
        row = self._request_row("control_traffic", entity_ids=vehicle_ids,
                                observation_ref=observation_ref, mode=mode)
        self._traffic_requests[row["request_id"]] = row
        return {"request": row, "actions": [], "scoreable": False}

    def record_vehicle_readback(self, request_id, vehicle_id, *, previous_position,
                                status_before):
        self.calls.append(("record_vehicle_readback", request_id, vehicle_id,
                           status_before))
        return {"vehicle_id": vehicle_id, "position_before": list(previous_position),
                "position_after": list(previous_position),
                "measured_trajectory": True, "no_hazard_ingress": True,
                "rescue_access_preserved": True,
                "trajectory_check_time_s": float(self.city.elapsed),
                "trajectory_evidence_ref": f"{request_id}:{vehicle_id}:trajectory"}

    def report_incident(self, *, observation_ref):
        self.calls.append(("report_incident", observation_ref))
        row = self._request_row("report_incident", entity_ids=(),
                                observation_ref=observation_ref,
                                recipient="city_dispatch")
        self._traffic_requests[row["request_id"]] = row
        return {"request": row,
                "receipt": {"request_id": row["request_id"], "service": "report_incident",
                            "received_time_s": float(self.city.elapsed),
                            "receipt_evidence_ref": f"{row['request_id']}:receipt",
                            "source_id": "city_dispatch"},
                "scoreable": False}

    def record_continuous_access(self, request, *, start_time_s, end_time_s):
        self.calls.append(("record_continuous_access", request["request_id"],
                           start_time_s, end_time_s))
        return {"evidence_ref": f"{request['request_id']}:access",
                "measured_clear_throughout": True,
                "rescue_and_evacuation_routes_admitted": True}

    def record_person_arrival(self, request_id, person_id, *, previous_position,
                               assembly_region_id, arrived):
        self.calls.append(("record_person_arrival", request_id, person_id, arrived))
        return {"person_id": person_id, "arrived": bool(arrived),
                "safe_route_and_assembly": True}

    def review(self, request_id):
        self.calls.append(("review", request_id))
        now = float(self.city.elapsed)
        row = self._guide_requests.get(request_id) or self._traffic_requests[request_id]
        if row["service"] == "control_traffic":
            actors = [{"id": vehicle_id, "ack_time_s": row["submitted_time_s"],
                       "ack_evidence_ref": f"{request_id}:{vehicle_id}:ack"}
                      for vehicle_id in row["entity_ids"]]
            return {**deepcopy(row), "status": "evidence_complete",
                    "evidence_class": "runtime",
                    "evidence": {"request_id": request_id, "evidence_class": "runtime",
                                 "actors": actors}}
        receipt = {"received_time_s": row["submitted_time_s"],
                   "receipt_evidence_ref": f"{request_id}:receipt"}
        if row["service"] == "guide_people":
            actors = [{"id": person_id, "arrival_time_s": row["submitted_time_s"] + 1.0,
                       "safe_route_and_assembly": True}
                      for person_id in row["entity_ids"]]
            return {**deepcopy(row), "status": "evidence_complete",
                    "evidence_class": "runtime",
                    "evidence": {"request_id": request_id, "evidence_class": "runtime",
                                 "receipt": receipt, "actors": actors}}
        return {**deepcopy(row), "status": "evidence_complete",
                "evidence_class": "runtime",
                "evidence": {"request_id": request_id, "evidence_class": "runtime",
                             "receipt": receipt}}


def _make_ctx(tmp_path, *, start_s=10.0, guide_not_admitted_first=0,
              omit_contact_acks=()):
    city = _FakeCity(start_s=start_s)
    runner = _FakeRunner(city, guide_not_admitted_first=guide_not_admitted_first,
                         omit_contact_acks=omit_contact_acks)
    step_calls: list[int] = []
    role_calls: list[str] = []

    def step(session, stepped_city, actions, count=1):
        step_calls.append(int(count))
        stepped_city.elapsed = float(stepped_city.elapsed) + DT * int(count)

    def role(runner_arg, role_name, session, trace):
        role_calls.append(role_name)

    trace: dict = {}
    ctx = dict(
        session=SimpleNamespace(device="cpu"), city=city, actions={}, runner=runner,
        report={"schema": "eai.city.native_service_probe_v1", "trace": trace},
        report_path=Path(tmp_path) / "service_probe_report.json",
        prepared={}, run_id="city-goal-offline-run", binding={}, specs=[],
        roster_ids=list(ROSTER_IDS), vehicle_ids=tuple(VEHICLE_IDS),
        window={"control_ack_deadline_s": 10.0, "trajectory_reaction_window_s": 5.0},
        endpoint={"service_id": "city_dispatch", "receiver_entrypoint": "x",
                  "receipt_deadline_s": 15.0, "continuous_access_min_span_s": 0.5},
        dt=DT, guidance_observation="city_native_observation_initial",
        person_start={person_id: (0.0, 0.0, 0.0) for person_id in ROSTER_IDS},
        step=step, role=role, trace=trace)
    return ctx, runner, step_calls, role_calls


def _trusted_report_for(task, *, episode_id="episode-x", status="pending",
                        action_id="dispatch-0", current_time_s=0.0, plan_id="plan-1"):
    return {"session": {"episode_id": episode_id, "current_time_s": current_time_s,
                        "current_plan_id": plan_id,
                        "tasks": {task["task_id"]: {
                            "status": status, "task": deepcopy(task), "plan_id": plan_id,
                            "dispatch_action_id": action_id}}}}


@pytest.fixture(scope="module")
def city_package():
    return build_goal_task_package("city", city_roster_profile="bound_native_v1")


def test_reduced_plan_validates_and_matches_frozen_native_roster(city_package):
    plan = build_city_episode_plan(city_package["public_task"])

    assert plan["plan_id"] == CITY_EPISODE_PLAN_ID
    assert plan["assigned_team"] == ["go2_1", "iris_1"]
    guides = [task for task in plan["tasks"] if task["skill"] == "guide_people"]
    controls = [task for task in plan["tasks"] if task["skill"] == "control_following_traffic"]
    reports = [task for task in plan["tasks"] if task["skill"] == "report_incident"]
    assert len(guides) == len(_CITY_NATIVE_AFFECTED_PERSON_ASSEMBLY) and len(controls) == 1 \
        and len(reports) == 1
    assert not any(task["skill"] == "aerial_broadcast" for task in plan["tasks"])

    guided_people = [person for task in guides for person in task["arguments"]["person_ids"]]
    assert sorted(guided_people) == ROSTER_IDS
    for task in guides:
        assembly = task["arguments"]["assembly_region_id"]
        for person_id in task["arguments"]["person_ids"]:
            assert _CITY_NATIVE_AFFECTED_PERSON_ASSEMBLY[person_id] == assembly
        assert task["assigned_agents"] == ["go2_1"]
        assert task["arguments"]["observation_refs"] == [CITY_NATIVE_OBSERVATION_REF]
    # First two groups are prerequisite-free; the rest serialize after the first.
    assert guides[0]["prerequisites"] == [] and guides[1]["prerequisites"] == []
    for task in guides[2:]:
        assert task["prerequisites"] == [guides[0]["task_id"]]
    control = controls[0]
    assert control["arguments"]["vehicle_ids"] == VEHICLE_IDS
    assert control["arguments"]["mode"] == "hold"
    assert control["prerequisites"] == [guides[0]["task_id"]]
    assert control["assigned_agents"] == ["go2_1"]
    report = reports[0]
    assert report["assigned_agents"] == ["iris_1"]
    assert report["arguments"]["recipient_service_id"] == "city_dispatch"
    assert sorted(report["prerequisites"]) == sorted(
        [task["task_id"] for task in guides] + [control["task_id"]])
    # Serial public-clock scheduling with the contract's skill durations.
    durations = city_package["public_task"]["execution_contract"]["skill_durations_s"]
    clock = 0.0
    for task in plan["tasks"]:
        assert task["execution"]["start_s"] == clock
        assert task["execution"]["duration_s"] == durations[task["skill"]]
        clock += durations[task["skill"]]
    assert clock <= city_package["public_task"]["budgets"]["simulation_s"]


def test_offline_episode_runs_all_native_skills_through_the_session(tmp_path, city_package):
    ctx, runner, step_calls, role_calls = _make_ctx(tmp_path)
    plan = build_city_episode_plan(city_package["public_task"])
    # Mirror the driver's stepping hook: arrival stamps are collected during
    # every simulation advance, not only during the evaluator's final wait.
    arrival_stamps: dict[str, float] = {}
    native_step = ctx["step"]

    def stamped_step(session, city, actions, count=1):
        native_step(session, city, actions, count)
        stamp_arrival_times(ctx, arrival_stamps)

    ctx["step"] = stamped_step
    # People reach assembly after the last guide group acknowledges (~60 s);
    # arriving earlier than an acknowledgement is causally impossible.
    ctx["city"]._episode_service.people_controller.arrive_at_s = 65.0
    backend = CityNativeGoalBackend(ctx, plan)
    episode_root = tmp_path / "episode"
    formal = FormalGoalEpisodeRunner(
        city_package["public_task"], city_package["participant_api"],
        CityWitnessParticipant(plan), backend, output_root=episode_root,
        episode_id="city-goal-offline-001",
        evaluator=city_business_evaluator(backend.evidence, ctx,
                                          arrival_stamps=arrival_stamps))
    result = formal.run(initial_state={"robot_regions": dict(CITY_ROBOT_REGIONS),
                                       "object_states": {}})

    assert result["error"] is None, json.dumps(
        json.loads((episode_root / "goal_session_report.json").read_text())["report"]["tasks"],
        default=str)
    assert result["skill_receipt_count"] == len(plan["tasks"])
    guide_tasks = [task for task in plan["tasks"] if task["skill"] == "guide_people"]
    control_task = next(task for task in plan["tasks"]
                        if task["skill"] == "control_following_traffic")
    report_task = next(task for task in plan["tasks"] if task["skill"] == "report_incident")

    # Terminal receipts complete exactly at the admitted window ends, in order.
    receipts = json.loads((episode_root / "skill_receipts.json").read_text())
    expected_fields = {"backend_id", "receipt_ref", "task_id", "action_id", "status",
                       "completed_at_s", "evidence_refs", "episode_id"}
    assert [row["task_id"] for row in receipts] == \
        [task["task_id"] for task in plan["tasks"]]
    for receipt, task in zip(receipts, plan["tasks"]):
        assert set(receipt) == expected_fields
        assert receipt["backend_id"] == CITY_BACKEND_ID
        assert receipt["status"] == "succeeded"
        assert receipt["episode_id"] == "city-goal-offline-001"
        assert receipt["completed_at_s"] == task["execution"]["start_s"] + \
            task["execution"]["duration_s"]
        assert receipt["evidence_refs"] and len(receipts) and \
            len(receipt["evidence_refs"]) == len(set(receipt["evidence_refs"]))
        assert receipt["completed_at_s"] <= \
            city_package["public_task"]["budgets"]["simulation_s"]

    # The native call sequence per skill follows the concrete recipes.
    calls = [call[0] for call in runner.calls]
    assert calls.count("guide_people") == len(guide_tasks)
    assert calls.count("control_following_traffic") == 1
    assert calls.count("record_vehicle_readback") == len(VEHICLE_IDS)
    assert calls.count("report_incident") == 1
    assert calls.count("record_continuous_access") == 1
    assert ctx["city"].people.resumed == \
        [person for task in guide_tasks for person in task["arguments"]["person_ids"]]
    control_index = calls.index("control_following_traffic")
    assert "record_vehicle_readback" not in calls[:control_index]
    report_index = calls.index("report_incident")
    assert control_index < calls.index("review")  # traffic review before the report
    assert calls.index("record_continuous_access") < len(calls) - 1
    # Fresh native observations are requested once the clock has advanced.
    observations = [(call[0], call[2] if len(call) > 2 else call[1])
                    for call in runner.calls
                    if call[0] in {"guide_people", "control_following_traffic",
                                   "report_incident"}]
    assert observations[0][1] == "city_native_observation_initial"
    assert all(call[1] != "city_native_observation_initial" for call in observations[1:])
    # Field roles precede every service family.
    # Member dispatch: every service is rebound to the plan's assigned agent
    # with a recorded field-role interval instead of the host role helper.
    dispatch_rows = [row for row in runner.calls if row[0] == "record_role"]
    assert [row[2] for row in dispatch_rows].count("guidance_contact") >= len(guide_tasks)
    assert "traffic_control_point" in [row[2] for row in dispatch_rows]
    assert "incident_observer" in [row[2] for row in dispatch_rows]
    dispatch_trace = ctx["trace"].get("member_dispatch", [])
    assert len(dispatch_trace) == len(plan["tasks"])
    assert all(row["agent_id"] == "go2_1" for row in dispatch_trace[:-1])
    assert dispatch_trace[-1]["agent_id"] == "iris_1"
    assert runner.agent_id == "iris_1"

    # Runtime state stays the declared shape: robots in the initial regions.
    snapshots = json.loads((episode_root / "runtime_snapshots.json").read_text())
    assert snapshots[0]["robot_regions"] == CITY_ROBOT_REGIONS
    assert snapshots[0]["object_states"] == {}
    for snapshot in snapshots[1:]:
        assert snapshot["robot_regions"] == CITY_ROBOT_REGIONS
        assert snapshot["object_states"] == {}

    # Business evaluation maps acceptance outcomes onto the session ledger.
    business = json.loads((episode_root / "business_evidence.json").read_text())["outcomes"]
    assert set(business) == {"incident_assessed", "exposed_people_accounted_for",
                             "secondary_traffic_protected", "response_access_preserved"}
    for outcome_id, row in business.items():
        assert row["evaluator_ref"] == CITY_EVALUATOR_REF
        assert row["status"] in {"validated", "failed"}
    # The evaluator awaited assembly arrivals and recorded native readbacks,
    # so the people outcome validates like the other three.
    assert business["exposed_people_accounted_for"]["status"] == "validated"
    assert business["incident_assessed"]["status"] == "validated"
    assert business["secondary_traffic_protected"]["status"] == "validated"
    assert business["response_access_preserved"]["status"] == "validated"
    # The archived acceptance envelope derives only from trusted readbacks.
    envelope = build_acceptance_evidence(backend.evidence, ctx)
    acceptance = evaluate_city_acceptance(
        envelope, default_deadline_policy(),
        expected_affected_person_ids=set(ROSTER_IDS),
        expected_following_vehicle_ids=set(VEHICLE_IDS))
    assert acceptance["outcomes"]["exposed_people_accounted_for"]["success"] is True
    assert acceptance["outcomes"]["secondary_traffic_protected"]["success"] is True
    assert sorted(row["person_id"] for row in envelope["people"]) == ROSTER_IDS
    assert all(row["arrived_at_admitted_assembly"] is True for row in envelope["people"])
    assert all(row["arrival_time_s"] is not None for row in envelope["people"])
    assert sorted(row["vehicle_id"] for row in envelope["traffic"]) == sorted(VEHICLE_IDS)
    assert all(row["following_incident"] is True and
               row["entered_exclusion_boundary"] is False for row in envelope["traffic"])


def test_guide_retries_step_and_renew_role_until_admitted(tmp_path, city_package):
    ctx, runner, step_calls, role_calls = _make_ctx(
        tmp_path, guide_not_admitted_first=3)
    plan = build_city_episode_plan(city_package["public_task"])
    backend = CityNativeGoalBackend(ctx, plan)
    first_guide = next(task for task in plan["tasks"] if task["skill"] == "guide_people")
    result = backend.execute(first_guide, episode_id="episode-retry",
                             trusted_report=_trusted_report_for(
                                 first_guide, episode_id="episode-retry"))

    assert result["status"] == "succeeded"
    assert result["completed_at_s"] == first_guide["execution"]["start_s"] + \
        first_guide["execution"]["duration_s"]
    # Three rejections -> three one-second step batches, then the admission.
    assert [count for count in step_calls if count == int(1.0 / DT)] == [int(1.0 / DT)] * 3
    assert [row for row in runner.calls
            if row[0] == "record_role" and row[2] == "guidance_contact"].__len__() == 1 + 3
    guide_calls = [call for call in runner.calls if call[0] == "guide_people"]
    assert len(guide_calls) == 4
    # Each retry submits a renewed native observation.
    assert guide_calls[0][2] == "city_native_observation_initial"
    renewed = [call[2] for call in guide_calls[1:]]
    assert len(renewed) == 3 and len(set(renewed)) == 3
    assert all(ref.startswith("city_native_observation_") for ref in renewed)


def test_incomplete_contact_ack_fails_the_guide_honestly(tmp_path, city_package):
    ctx, runner, step_calls, _role = _make_ctx(
        tmp_path, omit_contact_acks=[ROSTER_IDS[0]])
    plan = build_city_episode_plan(city_package["public_task"])
    backend = CityNativeGoalBackend(ctx, plan)
    person = next(task["arguments"]["person_ids"][0]
                  for task in plan["tasks"] if task["skill"] == "guide_people"
                  and ROSTER_IDS[0] in task["arguments"]["person_ids"])
    assert person == ROSTER_IDS[0]
    first_guide = next(task for task in plan["tasks"]
                       if task["skill"] == "guide_people"
                       and ROSTER_IDS[0] in task["arguments"]["person_ids"])
    result = backend.execute(first_guide, episode_id="episode-contact",
                             trusted_report=_trusted_report_for(
                                 first_guide, episode_id="episode-contact"))

    assert result["status"] == "failed"
    assert result["receipt"]["status"] == "failed"
    assert result["completed_at_s"] >= float(ctx["city"].elapsed) - DT
    assert result["receipt"]["evidence_refs"]
    assert backend.evidence[-1]["acknowledged"] is False


def test_business_evaluator_maps_acceptance_predicates(tmp_path):
    ctx, _runner, _steps, _roles = _make_ctx(tmp_path)
    now = 10.0
    log = [{"kind": "episode_observation", "observation_ref": "obs_warmup",
            "delivered_at_s": now}]
    for index, person_id in enumerate(ROSTER_IDS):
        log.append({
            "kind": "guide", "person_ids": [person_id],
            "observation_ref": "obs_warmup", "acknowledged": person_id != ROSTER_IDS[-1],
            "request": {"request_id": f"guide_{index}", "agent_id": "go2_1",
                        "submitted_time_s": now + index,
                        "entity_ids": [person_id]},
            "contact": {"status": "contact_confirmed", "ack_time_s": now + index + 0.5,
                        "actor_acks": [{"id": person_id, "ack_time_s": now + index + 0.5}]}})
    log.append({
        "kind": "control", "vehicle_ids": VEHICLE_IDS, "mode": "hold",
        "observation_ref": "obs_warmup",
        "request": {"request_id": "control_0", "submitted_time_s": 80.0},
        "readbacks": {vehicle_id: {"measured_trajectory": True, "no_hazard_ingress": True,
                                   "trajectory_check_time_s": 85.1}
                      for vehicle_id in VEHICLE_IDS},
        "review": {"status": "evidence_complete",
                   "evidence": {"actors": [{"id": vehicle_id, "ack_time_s": 80.0}
                                           for vehicle_id in VEHICLE_IDS]}}})
    log.append({
        "kind": "report", "observation_ref": "obs_report",
        "request": {"request_id": "report_0", "submitted_time_s": 86.0},
        "receipt": {"received_time_s": 86.0}, "receipt_time_s": 86.0,
        "access": {"measured_clear_throughout": True,
                   "rescue_and_evacuation_routes_admitted": True},
        "access_end_time_s": 86.55,
        "review": {"status": "evidence_complete"}})

    envelope = build_acceptance_evidence(log, ctx)
    # Structural parity with the maintained city acceptance fixture.
    assert set(envelope) == {"incident_assessed", "responder_dispatch", "people",
                             "traffic", "incident_report", "response_access"}
    assert all(row["arrived_at_admitted_assembly"] is False for row in envelope["people"])
    for index, person_id in enumerate(ROSTER_IDS):
        _runner._guide_requests[f"guide_{index}"] = {
            "service": "guide_people", "entity_ids": [person_id],
            "submitted_time_s": now + index}
    # People reach assembly only after every acknowledgement (now + 3.5 + a
    # margin), so the wait loop must step and stamp arrival after the acks.
    ctx["city"]._episode_service.people_controller.arrive_at_s = now + 30.0
    evaluator = city_business_evaluator(log, ctx)
    outcomes = evaluator("episode-eval", {"session": {"business_outcomes": [
        {"id": "incident_assessed"}, {"id": "exposed_people_accounted_for"},
        {"id": "secondary_traffic_protected"}, {"id": "response_access_preserved"}]}})
    assert outcomes["incident_assessed"]["status"] == "validated"
    assert outcomes["secondary_traffic_protected"]["status"] == "validated"
    assert outcomes["response_access_preserved"]["status"] == "validated"
    # The evaluator awaited arrivals and recorded native readbacks, so the
    # people outcome upgrades to validated (fake controller reports arrived).
    assert outcomes["exposed_people_accounted_for"]["status"] == "validated"
    assert all(row["evaluator_ref"] == CITY_EVALUATOR_REF for row in outcomes.values())
    assert any(row.get("kind") == "arrival" and row["arrived"] for row in log)
    # Fail-closed variant: people never arrive -> outcome stays failed.
    log_blocked = [row for row in log if row.get("kind") != "arrival"]
    ctx["city"]._episode_service.people_controller.arrive_at_s = None
    ctx["city"]._episode_service.people_controller._arrived = False
    outcomes_blocked = city_business_evaluator(log_blocked, ctx)(
        "episode-eval", {"session": {"business_outcomes": [
            {"id": "exposed_people_accounted_for"}]}})
    assert outcomes_blocked["exposed_people_accounted_for"]["status"] == "failed"


def test_service_probe_driver_injection_surface():
    for func in (service_probe._run_bound_episode, service_probe._execute,
                 service_probe.execute_probe):
        signature = inspect.signature(func)
        assert "driver" in signature.parameters
        assert signature.parameters["driver"].default is None
    # _execute forwards the injected driver into the bound episode.
    assert "driver=driver" in inspect.getsource(service_probe._execute)
    assert "ensure_driver_compatible(prepared, driver)" in \
        inspect.getsource(service_probe._run_bound_episode)
    # The injected context carries the documented host handles.
    source = inspect.getsource(service_probe._run_bound_episode)
    for key in ("guidance_observation", "person_start", "roster_ids", "vehicle_ids",
                "step=_step", "role=_role"):
        assert key in source


def test_ensure_driver_compatible_rejects_fault_probe_combination():
    with pytest.raises(RuntimeError, match="cannot combine with fault_probe"):
        service_probe.ensure_driver_compatible(
            {"fault_probe": "traffic_second_controller"}, lambda ctx: None)
    # A driver-less prepare and a clean prepare both stay compatible.
    service_probe.ensure_driver_compatible(
        {"fault_probe": "traffic_second_controller"}, None)
    service_probe.ensure_driver_compatible({"fault_probe": None}, lambda ctx: None)
    # A driver cannot silently replace the diagnostic (non-formal) sequence.
    assert "driver injection requires a frozen episode binding" in \
        inspect.getsource(service_probe._execute)


def test_goal_city_episode_cli_entrypoint_executes():
    """``python -m demo.benchmark.goal_city_episode`` must run main().

    Regression guard: the module once shipped without the
    ``__main__`` guard, so ``python -m`` silently imported it, exited 0
    and produced no output at all while every direct-call test stayed
    green.
    """
    import os
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-m", "demo.benchmark.goal_city_episode", "--help"],
        capture_output=True, text=True, timeout=120,
        cwd=str(Path(__file__).resolve().parents[3]),
        env={**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
    )
    assert result.returncode == 0
    assert "--output-root" in result.stdout


def test_city_teamweaver_participant_allocates_and_sequences(city_package):
    """Real TeamWeaver allocator drives team selection and per-task agents."""
    from demo.benchmark.goal_external_adapters import CityTeamWeaverParticipant

    public = city_package["public_task"]
    plan = build_city_episode_plan(public)
    participant = CityTeamWeaverParticipant(plan)
    participant.initialize(public, city_package["participant_api"])

    opening = participant.act([{"type": "episode_started", "time_s": 0.0}])
    assert [row["type"] for row in opening] == ["select_team", "submit_plan"]
    # Faithful translation: assignments may be capable or not (user
    # principle - incapable ones surface as execution failures); every task
    # must simply carry exactly one assigned agent.
    allocated = opening[1]["payload"]["plan"]
    for task in allocated["tasks"]:
        assert len(task["assigned_agents"]) == 1
    # Team is exactly the allocated agents, pool-ordered, within the budget.
    team = opening[0]["payload"]["agent_ids"]
    assert set(team) == {task["assigned_agents"][0] for task in allocated["tasks"]}
    assert len(team) <= public["budgets"]["max_active_agents"]
    assert participant.trace[0]["planner"] == "TeamWeaverPipeline"
    assert participant.trace[1]["event"] == "plan_initial"
    assert participant.trace[1]["solver"] in {"hungarian", "miqp"}

    # Dispatches sequence one per terminal result, witness-mirrored.
    first = participant.act([{"type": "action_result", "time_s": 20.0,
                              "payload": {"action_id": "tw_city_plan",
                                          "status": "accepted"}}])
    assert [row["type"] for row in first] == ["dispatch_skill"]
    assert first[0]["payload"]["task_id"] == allocated["tasks"][0]["task_id"]
    idle = participant.act([{"type": "action_result", "time_s": 20.5,
                             "payload": {"action_id": "other", "status": "accepted"}}])
    assert idle == []


def test_city_emos_participant_allocates_and_sequences(city_package):
    """Real EMOS discussion manager drives team selection and per-task agents."""
    from demo.benchmark.goal_external_adapters import CityEmosParticipant

    public = city_package["public_task"]
    plan = build_city_episode_plan(public)
    participant = CityEmosParticipant(plan)
    participant.initialize(public, city_package["participant_api"])
    assert participant.manifest()["id"] == "city-native-emos"

    opening = participant.act([{"type": "episode_started", "time_s": 0.0}])
    assert [row["type"] for row in opening] == ["select_team", "submit_plan"]
    assert opening[0]["action_id"] == "emos_city_select"
    assert opening[1]["action_id"] == "emos_city_plan"
    # Faithful translation: assignments may be capable or not; each task
    # carries exactly one assigned agent.
    allocated = opening[1]["payload"]["plan"]
    for task in allocated["tasks"]:
        assert len(task["assigned_agents"]) == 1
    # Team is exactly the allocated agents, pool-ordered, within the budget.
    team = opening[0]["payload"]["agent_ids"]
    assert set(team) == {task["assigned_agents"][0] for task in allocated["tasks"]}
    assert len(team) <= public["budgets"]["max_active_agents"]
    # The allocation came from the EMOS discussion manager boundary.
    assert participant.trace[0]["planner"] == "EMOSDiscussionManager"
    events = [row["event"] for row in participant.trace]
    assert "emos_assignment" in events and "team_selected" in events
    rounds = [row for row in participant.trace if row["event"] == "emos_assignment"]
    assert rounds and all(row["manager"] == "EMOSDiscussionManager" for row in rounds)
    pool_ids = {row["id"] for row in public["candidate_robot_pool"]}
    assert all(agent in pool_ids for row in rounds
               for agent in row["assignment"].values())

    # Dispatches sequence one per terminal result, witness-mirrored.
    first = participant.act([{"type": "action_result", "time_s": 20.0,
                              "payload": {"action_id": "emos_city_plan",
                                          "status": "accepted"}}])
    assert [row["type"] for row in first] == ["dispatch_skill"]
    assert first[0]["action_id"] == "emos_city_dispatch_0"
    assert first[0]["payload"]["task_id"] == allocated["tasks"][0]["task_id"]
    idle = participant.act([{"type": "action_result", "time_s": 20.5,
                             "payload": {"action_id": "other", "status": "accepted"}}])
    assert idle == []


def test_city_emos_llm_flag_recorded(city_package, monkeypatch):
    """use_llm_discussion=True records the dongli wiring without ever tracing the key."""
    from demo.benchmark.goal_external_adapters import CityEmosParticipant

    public = city_package["public_task"]
    plan = build_city_episode_plan(public)
    participant = CityEmosParticipant(plan, use_llm_discussion=True)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "dummy-offline-emos-key")
    # initialize must record the requested discussion mode without raising
    # and without any network call.
    participant.initialize(public, city_package["participant_api"])

    modes = [row for row in participant.trace if row.get("event") == "llm_discussion"]
    assert len(modes) == 1
    # The EMOS brain LLM path decides which recording is honest; both are.
    from emos import engine as emos_engine
    if emos_engine.LLM_AVAILABLE:
        assert modes[0]["mode"] == "dongli"
        assert modes[0]["model"]
        assert modes[0]["base_url"]
    else:
        assert modes[0]["mode"] == "llm_discussion_unavailable"
    # The dongli key never enters the trace.
    assert "dummy-offline-emos-key" not in json.dumps(participant.trace)


def test_city_episode_cli_algorithm_choices_include_emos(capsys):
    """--help exposes the emos/emos_llm participant choices."""
    from demo.benchmark import goal_city_episode as episode

    with pytest.raises(SystemExit) as exc:
        episode.main(["--help"])
    assert exc.value.code == 0
    assert "{witness,teamweaver,teamweaver_llm,emos,emos_llm}" in capsys.readouterr().out


def test_team_usage_summary_derives_from_trusted_ledger(city_package):
    """Every episode archives the facts a 'fewer robots' score would need."""
    from demo.benchmark.goal_episode_runner import team_usage_summary

    public = city_package["public_task"]
    report = {"session": {
        "actions": [
            {"type": "select_team", "payload": {"agent_ids": ["go2_1", "iris_1"]}},
            {"type": "dispatch_skill", "payload": {"task_id": "t1"}},
        ],
        "plans": {"p1": {"plan": {"tasks": [
            {"task_id": "t1", "assigned_agents": ["go2_1"]},
            {"task_id": "t2", "assigned_agents": ["iris_1"]},
        ]}}},
    }}
    usage = team_usage_summary(report, public)
    assert usage["selected_team"] == ["go2_1", "iris_1"]
    assert usage["selected_team_size"] == 2
    assert usage["plan_assigned_agents"] == ["go2_1", "iris_1"]
    assert usage["active_agents"] == ["go2_1"]
    assert usage["active_team_size"] == 1
    assert usage["activation_cost_proxy"] > 0
    assert usage["scoreable"] is False
