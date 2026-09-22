"""Offline checks for the City P0 aerial guidance modality (bound_native_v3).

Covers the four coordinated layers of the modality without Isaac/Kit:

* ``skill_ontology``: ``aerial_broadcast`` first publishes in skill catalog v4
  while the frozen v2/v3 vocabularies stay exact and unknown revisions fail
  closed.
* ``goal_driven_tasks``/``goal_driven_contract``: the bound_native_v3 roster
  (CITY-GOAL-NATIVE-03) reuses the seven-robot v2 pool with the broadcast
  capability on the iris members only, pins the v4 catalog cross-version
  fail-closed, and keeps the four frozen City package hashes untouched.
* ``goal_city_episode``: the backend executes an ``aerial_broadcast`` dispatch
  through member dispatch (capable iris succeeds; an assignment whose declared
  capabilities do not cover the task fails deterministically at the execution
  layer with a factual receipt, the window still consumed).
* ``build_acceptance_evidence``: the B parallel path - the broadcast ACK and
  the ground guide contact confirmation are two legal sources of the
  per-person contact-confirmation component, the earlier acknowledgement
  timestamps it, and arrival still fails closed without the ground evidence.
"""

from copy import deepcopy
import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest

from demo.benchmark.goal_city_episode import (
    CITY_BACKEND_ID,
    CITY_NATIVE_OBSERVATION_REF,
    CityNativeGoalBackend,
    build_acceptance_evidence,
    build_city_episode_plan,
)
from demo.benchmark.goal_driven_contract import (
    GOAL_PLAN_VERSION,
    validate_goal_plan,
    validate_goal_task_spec,
)
from demo.benchmark.goal_driven_tasks import (
    _CITY_NATIVE_AFFECTED_PERSON_ASSEMBLY,
    CITY_AERIAL_BROADCAST_DURATION_S,
    build_goal_task_package,
    build_goal_task_spec,
)
from demo.benchmark.provenance import canonical_sha256
from demo.benchmark.skill_ontology import (
    CITY_AERIAL_SKILL_CATALOG_VERSION,
    CITY_NATIVE_SKILL_CATALOG_VERSION,
    SKILL_CATALOG_SCHEMA,
    SKILL_CATALOG_VERSION,
    public_skill_catalog,
    required_capabilities,
    skill_definition,
    skill_names,
)
from demo.city_benchmark.task_logic import (
    default_deadline_policy,
    evaluate_city_acceptance,
)


ROSTER_IDS = sorted(_CITY_NATIVE_AFFECTED_PERSON_ASSEMBLY)
DT = 0.05
# canonical_sha256 digests frozen before the aerial modality: the default and
# bound_native_v1 City packages must stay byte-for-byte unchanged.
_FROZEN_CITY_SHA256 = {
    ("abstract_v1", "public_task"): "10b1b9b40f5b3f1b7ca74be879cacff14c65085561eac5c7866de352237aecfb",
    ("abstract_v1", "hidden_evaluator"): "34f86e46d25383294eb351d9cf7b7dc871c5a104862a8b868c2227d6be680f30",
    ("bound_native_v1", "public_task"): "b62dd182990e6be116d190e0f29ff62613001347c1952d00ac4edd927561b19a",
    ("bound_native_v1", "hidden_evaluator"): "8a915e54b46e8390c5fa087df69bab0a7413b13df46b0f5ca4d22dbf14813373",
}


@pytest.fixture(scope="module")
def v3_package():
    return build_goal_task_package("city", city_roster_profile="bound_native_v3")


@pytest.fixture(scope="module")
def v3_public(v3_package):
    return v3_package["public_task"]


# -- 1. the bound_native_v3 package -----------------------------------------


def test_v3_package_pins_native03_and_the_v4_catalog(v3_package, v3_public):
    assert v3_public["task_id"] == "CITY-GOAL-NATIVE-03"
    assert v3_public["skill_catalog_version"] == \
        f"{SKILL_CATALOG_SCHEMA}.v{CITY_AERIAL_SKILL_CATALOG_VERSION}"
    # The participant API publishes exactly the v4 catalog for this task.
    assert v3_package["participant_api"]["skill_catalog"] == \
        public_skill_catalog(version=CITY_AERIAL_SKILL_CATALOG_VERSION)


def test_v3_pool_adds_aerial_broadcast_to_the_iris_members_only(v3_public):
    rows = {row["id"]: row for row in v3_public["candidate_robot_pool"]}
    assert len(rows) == 7
    assert "aerial_broadcast" in rows["iris_1"]["public_capabilities"]
    assert "aerial_broadcast" in rows["iris_2"]["public_capabilities"]
    for identifier in ("go2_1", "go2_2", "carter_1", "carter_2", "lite3_1"):
        assert "aerial_broadcast" not in rows[identifier]["public_capabilities"]
    # Every other v2 roster declaration is reused unchanged.
    v2_rows = {row["id"]: row for row in build_goal_task_spec(
        "city", city_roster_profile="bound_native_v2")["candidate_robot_pool"]}
    for identifier, row in rows.items():
        expected = list(row["public_capabilities"])
        if identifier in ("iris_1", "iris_2"):
            expected = sorted(set(expected) - {"aerial_broadcast"})
        assert v2_rows[identifier]["public_capabilities"] == expected
        assert v2_rows[identifier]["interfaces"] == row["interfaces"]
        assert v2_rows[identifier]["mobility"] == row["mobility"]


def test_v3_reuses_v2_spawn_disclosure_budgets_and_broadcast_window(v3_public):
    v2 = build_goal_task_package("city", city_roster_profile="bound_native_v2")["public_task"]
    assert v3_public["public_world"]["robot_spawn_positions"] == \
        v2["public_world"]["robot_spawn_positions"]
    assert v3_public["budgets"] == v2["budgets"]
    assert v3_public["budgets"]["max_active_agents"] == 7
    durations = v3_public["execution_contract"]["skill_durations_s"]
    assert durations["aerial_broadcast"] == CITY_AERIAL_BROADCAST_DURATION_S
    # The aerial window is shorter than the frozen ground ACK window so the
    # modality's speed advantage stays measurable.
    assert durations["aerial_broadcast"] < durations["guide_people"]
    # Every frozen v2/v3 duration keeps its historical value.
    assert {k: v for k, v in durations.items() if k != "aerial_broadcast"} == \
        v2["execution_contract"]["skill_durations_s"]


def test_v3_witnesses_are_reused_with_the_aerial_pool_note(v3_package):
    witnesses = v3_package["hidden_evaluator"]["witness_plans"]
    v1_witnesses = build_goal_task_package(
        "city", city_roster_profile="bound_native_v1")["hidden_evaluator"]["witness_plans"]
    assert len(witnesses) == 2
    for witness in witnesses:
        assert witness["id"].startswith("city_native_witness_")
        assert set(witness["plan"]["assigned_team"]) <= {"go2_1", "carter_1", "iris_1"}
        assert "bound_native_v3" in witness["pool_note"]
        assert "aerial_broadcast" in witness["pool_note"]
    stripped = [{key: row[key] for key in ("id", "satisfies_outcomes", "outcome_support", "plan")}
                for row in witnesses]
    assert stripped == v1_witnesses  # v3 adds only the pool_note annotation


def test_the_four_frozen_city_package_hashes_are_unchanged(v3_package):
    del v3_package  # building the v3 package must not perturb the frozen ones
    for profile in ("abstract_v1", "bound_native_v1"):
        bundle = build_goal_task_package("city", city_roster_profile=profile)
        assert canonical_sha256(bundle["public_task"]) == \
            _FROZEN_CITY_SHA256[(profile, "public_task")]
        assert canonical_sha256(bundle["hidden_evaluator"]) == \
            _FROZEN_CITY_SHA256[(profile, "hidden_evaluator")]
        assert canonical_sha256(build_goal_task_spec("city", city_roster_profile=profile)) \
            == _FROZEN_CITY_SHA256[(profile, "public_task")]


def test_v3_reduced_episode_plan_still_validates(v3_public):
    plan = build_city_episode_plan(v3_public)
    review = validate_goal_plan(plan, task_spec=v3_public)
    assert review["validated"] is True
    broadcasts = [task for task in plan["tasks"] if task["skill"] == "aerial_broadcast"]
    assert len(broadcasts) == 1
    assert broadcasts[0]["assigned_agents"] == ["iris_1"]
    assert broadcasts[0]["task_id"] == "aerial_broadcast_roster"
    assert sorted(broadcasts[0]["arguments"]["person_ids"]) == sorted(ROSTER_IDS)
    guide_ids = [task["task_id"] for task in plan["tasks"] if task["skill"] == "guide_people"]
    assert broadcasts[0]["prerequisites"] == [guide_ids[0]]
    assert plan["tasks"].index(broadcasts[0]) > plan["tasks"].index(
        next(task for task in plan["tasks"] if task["task_id"] == guide_ids[-1]))


# -- 2. cross-version catalog admission --------------------------------------


@pytest.mark.parametrize("bad_catalog", ["eai.skills.v3", "eai.skills.v2"])
def test_native03_catalog_tampering_is_rejected(bad_catalog):
    spec = deepcopy(build_goal_task_spec("city", city_roster_profile="bound_native_v3"))
    spec["skill_catalog_version"] = bad_catalog
    with pytest.raises(ValueError, match="unsupported skill catalog"):
        validate_goal_task_spec(spec)


def test_pinned_native_tasks_cannot_borrow_a_foreign_catalog():
    v1 = deepcopy(build_goal_task_spec("city", city_roster_profile="bound_native_v1"))
    for bad_catalog in ("eai.skills.v4", "eai.skills.v2"):
        v1["skill_catalog_version"] = bad_catalog
        with pytest.raises(ValueError, match="unsupported skill catalog"):
            validate_goal_task_spec(v1)
    v3 = deepcopy(build_goal_task_spec("city", city_roster_profile="bound_native_v3"))
    v3["task_id"] = "CITY-GOAL-NATIVE-01"
    with pytest.raises(ValueError, match="unsupported skill catalog"):
        validate_goal_task_spec(v3)
    # The default catalog still serves ordinary tasks of every other scene.
    factory = build_goal_task_spec("factory")
    assert validate_goal_task_spec(factory)["validated"] is True


def test_broadcast_plan_task_is_legal_under_v4_only(v3_public):
    durations = v3_public["execution_contract"]["skill_durations_s"]

    def broadcast_task(task_id, agents):
        return {"task_id": task_id, "skill": "aerial_broadcast",
                "arguments": {"person_ids": list(ROSTER_IDS[:2]),
                              "observation_refs": [CITY_NATIVE_OBSERVATION_REF]},
                "prerequisites": [],
                "required_capabilities": sorted(
                    {"aerial_broadcast", *required_capabilities("aerial_broadcast")}),
                "assigned_agents": agents, "evidence_refs": [],
                "execution": {"start_s": 0.0,
                              "duration_s": float(durations["aerial_broadcast"])}}

    plan = {"schema": "eai.benchmark.goal_plan", "version": GOAL_PLAN_VERSION,
            "plan_id": "aerial_broadcast_legal_check",
            "assigned_team": ["iris_1", "go2_1"],
            "tasks": [broadcast_task("broadcast_north", ["iris_1"])]}
    assert validate_goal_plan(plan, task_spec=v3_public)["validated"] is True
    # The same task stays illegal under the frozen v3 catalog of NATIVE-02
    # (rejected as an unknown capability before the constraint model's own
    # unknown-skill check can even run).
    v2_public = build_goal_task_spec("city", city_roster_profile="bound_native_v2")
    with pytest.raises(ValueError, match="unknown required capability|unknown skill"):
        validate_goal_plan(plan, task_spec=v2_public)


# -- 3. backend execution: capable iris succeeds, mismatched dispatch fails --


class _FakeCity:
    def __init__(self, start_s=10.0):
        self.elapsed = float(start_s)


class _FakeAdapter:
    def __init__(self, city):
        self._city = city

    def now_s(self):
        return float(self._city.elapsed)


class _FakeRunner:
    """Minimal double: broadcast needs only dispatch, observation and clock."""

    def __init__(self, city):
        self.city = city
        self.agent_id = "go2_1"
        self.adapter = _FakeAdapter(city)
        self.calls: list[tuple] = []
        self.host = SimpleNamespace(
            record_role_occupancy=lambda *a, **k: self.calls.append(
                ("record_role", a[0], a[1])))
        self._observations = 0

    def observe(self):
        self._observations += 1
        return f"city_native_observation_{self._observations:09d}"


def _make_ctx(tmp_path, start_s=10.0):
    city = _FakeCity(start_s)
    runner = _FakeRunner(city)
    step_counts: list[int] = []

    def step(session, stepped_city, actions, count=1):
        step_counts.append(int(count))
        stepped_city.elapsed = float(stepped_city.elapsed) + DT * int(count)

    ctx = dict(
        session=SimpleNamespace(device="cpu"), city=city, actions={}, runner=runner,
        report={"schema": "eai.city.native_service_probe_v1", "trace": {}},
        report_path=Path(tmp_path) / "service_probe_report.json",
        roster_ids=list(ROSTER_IDS), vehicle_ids=(),
        dt=DT, guidance_observation="city_native_observation_initial",
        person_start={person_id: (0.0, 0.0, 0.0) for person_id in ROSTER_IDS},
        step=step, trace={})
    return ctx, runner, step_counts


def _trusted_report_for(task, *, episode_id="episode-aerial", action_id="dispatch-0",
                        current_time_s=10.0, plan_id="plan-1"):
    return {"session": {"episode_id": episode_id, "current_time_s": current_time_s,
                        "current_plan_id": plan_id,
                        "tasks": {task["task_id"]: {
                            "status": "pending", "task": deepcopy(task), "plan_id": plan_id,
                            "dispatch_action_id": action_id}}}}


def _broadcast_task(v3_public, task_id, agents, *, start_s=10.0, duration_s=None):
    durations = v3_public["execution_contract"]["skill_durations_s"]
    return {"task_id": task_id, "skill": "aerial_broadcast",
            "arguments": {"person_ids": list(ROSTER_IDS),
                          "observation_refs": [CITY_NATIVE_OBSERVATION_REF]},
            "prerequisites": [],
            "required_capabilities": sorted(
                {"aerial_broadcast", *required_capabilities("aerial_broadcast")}),
            "assigned_agents": list(agents), "evidence_refs": [],
            "execution": {"start_s": float(start_s),
                          "duration_s": float(durations["aerial_broadcast"]
                                              if duration_s is None else duration_s)}}


def test_capable_iris_broadcast_succeeds_with_per_person_acks(tmp_path, v3_public):
    ctx, runner, step_counts = _make_ctx(tmp_path, start_s=10.0)
    task = _broadcast_task(v3_public, "broadcast_roster", ["iris_1"],
                           start_s=10.0, duration_s=15.0)
    backend = CityNativeGoalBackend(ctx, task, public_task=v3_public)
    result = backend.execute(task, episode_id="episode-aerial",
                             trusted_report=_trusted_report_for(task))

    assert result["status"] == "succeeded"
    window_end = task["execution"]["start_s"] + task["execution"]["duration_s"]
    assert result["completed_at_s"] == window_end == 25.0
    assert step_counts  # the world settled to the reserved window end
    # Member dispatch rebound the native services and the field role.
    assert ("record_role", "iris_1", "aerial_broadcaster") in runner.calls
    assert runner.agent_id == "iris_1"
    dispatch = ctx["trace"]["member_dispatch"][-1]
    assert dispatch["task_id"] == "broadcast_roster"
    assert dispatch["agent_id"] == "iris_1"
    assert dispatch["role"] == "aerial_broadcaster"
    # Evidence: one ACK per person, arrival stamp, trusted observation.
    row = backend.evidence[-1]
    assert row["kind"] == "broadcast" and row["task_id"] == "broadcast_roster"
    assert row["skill"] == "aerial_broadcast"
    assert row["person_ids"] == ROSTER_IDS
    assert row["arrival_stamp"] == 10.0
    assert row["observation_ref"] == "city_native_observation_initial"
    assert [ack["id"] for ack in row["actor_acks"]] == ROSTER_IDS
    for ack in row["actor_acks"]:
        assert ack["ack_time_s"] == 10.0
        assert ack["ack_evidence_ref"] == "city_native_observation_initial"
    # Receipt: exact field set, evidence/receipt refs, episode id.
    receipt = result["receipt"]
    assert set(receipt) == {"backend_id", "receipt_ref", "task_id", "action_id",
                            "status", "completed_at_s", "episode_id", "evidence_refs"}
    assert receipt["backend_id"] == CITY_BACKEND_ID
    assert receipt["status"] == "succeeded"
    assert receipt["episode_id"] == "episode-aerial"
    assert receipt["evidence_refs"] == ["broadcast:broadcast_roster"]
    assert receipt["receipt_ref"] == "broadcast:episode-aerial:broadcast_roster"
    assert result["runtime_state"] == {"robot_regions": {
        agent: "road_surface" for agent in
        (row["id"] for row in v3_public["candidate_robot_pool"])}, "object_states": {}}


def test_mismatched_broadcast_dispatch_fails_at_the_execution_layer(tmp_path, v3_public):
    ctx, runner, step_counts = _make_ctx(tmp_path, start_s=10.0)
    task = _broadcast_task(v3_public, "broadcast_ground_dispatch", ["go2_1"],
                           start_s=10.0, duration_s=15.0)
    backend = CityNativeGoalBackend(ctx, task, public_task=v3_public)
    result = backend.execute(task, episode_id="episode-aerial",
                             trusted_report=_trusted_report_for(task))

    assert result["status"] == "failed"
    window_end = task["execution"]["start_s"] + task["execution"]["duration_s"]
    # The failed attempt still consumes its reserved window (the world settles
    # to the window end) and reports the same runtime-state shape.
    assert result["completed_at_s"] == window_end == 25.0
    assert step_counts
    assert set(result["runtime_state"]) == {"robot_regions", "object_states"}
    receipt = result["receipt"]
    assert receipt["status"] == "failed"
    assert receipt["episode_id"] == "episode-aerial"
    assert receipt["receipt_ref"] == "capability:episode-aerial:broadcast_ground_dispatch"
    assert receipt["evidence_refs"] == ["capability:broadcast_ground_dispatch"]
    # Factual observables only: the receipt records what the declared contract
    # did not produce, with no prescription attached.
    failure = backend.evidence[-1]
    assert failure["kind"] == "capability_execution_failure"
    assert failure["agent_id"] == "go2_1"
    assert "aerial_broadcast" in failure["reason"]
    assert "lacks" in failure["reason"]


# -- 4. B parallel path in the acceptance envelope ----------------------------


def _guide_row(person_id, *, ack_s, request_s=10.5, status="contact_confirmed"):
    return {"kind": "guide", "person_ids": [person_id],
            "observation_ref": "obs_ground",
            "request": {"request_id": f"guide_{person_id}", "agent_id": "go2_1",
                        "submitted_time_s": request_s},
            "contact": {"status": status, "ack_time_s": ack_s if status == "contact_confirmed" else None,
                        "actor_acks": [{"id": person_id, "ack_time_s": ack_s}
                                       if status == "contact_confirmed" else {}]}}


def _broadcast_row(person_ids, *, ack_s, observation_ref="obs_broadcast"):
    return {"kind": "broadcast", "task_id": "broadcast_probe",
            "skill": "aerial_broadcast", "person_ids": list(person_ids),
            "actor_acks": [{"id": person_id, "ack_time_s": ack_s,
                            "ack_evidence_ref": observation_ref}
                           for person_id in person_ids],
            "arrival_stamp": ack_s, "observation_ref": observation_ref}


def test_b_path_envelope_unions_both_ack_sources_and_takes_the_earlier(tmp_path):
    ctx, _runner, _steps = _make_ctx(tmp_path)
    log = [{"kind": "episode_observation", "observation_ref": "obs_warmup",
            "delivered_at_s": 10.0},
           _guide_row(ROSTER_IDS[0], ack_s=12.0),
           _guide_row(ROSTER_IDS[1], ack_s=12.0),
           _broadcast_row([ROSTER_IDS[1], ROSTER_IDS[2]], ack_s=11.0)]
    envelope = build_acceptance_evidence(log, ctx)
    people = {row["person_id"]: row for row in envelope["people"]}
    assert sorted(people) == ROSTER_IDS
    # Ground-only person: exactly the historical behavior.
    assert people[ROSTER_IDS[0]]["guidance_acknowledged"] is True
    assert people[ROSTER_IDS[0]]["guidance_time_s"] == 12.0
    assert people[ROSTER_IDS[0]]["observation_ref"] == "obs_ground"
    # Dual-source person: acknowledged by the union, timestamped by the
    # earlier (broadcast) acknowledgement, provenance stays ground-first.
    assert people[ROSTER_IDS[1]]["guidance_acknowledged"] is True
    assert people[ROSTER_IDS[1]]["guidance_time_s"] == 11.0
    assert people[ROSTER_IDS[1]]["observation_ref"] == "obs_ground"
    # Broadcast-only person: the ACK is a legal contact-confirmation source,
    # but no route admission or ground request time is fabricated for it.
    broadcast_only = people[ROSTER_IDS[2]]
    assert broadcast_only["guidance_acknowledged"] is True
    assert broadcast_only["guidance_time_s"] == 11.0
    assert broadcast_only["observation_ref"] == "obs_broadcast"
    assert "route_admitted" not in broadcast_only
    assert "guidance_request_time_s" not in broadcast_only
    # Person without any source keeps the bare fail-closed row.
    assert people[ROSTER_IDS[3]] == {"person_id": ROSTER_IDS[3]}
    # Without broadcast rows the envelope is exactly the historical shape.
    no_broadcast = build_acceptance_evidence(
        [row for row in log if row.get("kind") != "broadcast"], ctx)
    reverted = {row["person_id"]: row for row in no_broadcast["people"]}
    assert reverted[ROSTER_IDS[1]]["guidance_time_s"] == 12.0
    assert reverted[ROSTER_IDS[2]] == {"person_id": ROSTER_IDS[2]}
    assert reverted[ROSTER_IDS[0]] == people[ROSTER_IDS[0]]


def test_b_path_does_not_change_the_business_outcome_predicates(tmp_path):
    ctx, _runner, _steps = _make_ctx(tmp_path)
    log = [{"kind": "episode_observation", "observation_ref": "obs_warmup",
            "delivered_at_s": 10.0},
           # One ground guide row anchors the responder/decision timing the
           # acceptance evaluator reads from the first guide request.
           _guide_row(ROSTER_IDS[0], ack_s=12.0),
           _broadcast_row(ROSTER_IDS, ack_s=11.0)]
    envelope = build_acceptance_evidence(log, ctx)
    acceptance = evaluate_city_acceptance(
        envelope, default_deadline_policy(),
        expected_affected_person_ids=set(ROSTER_IDS),
        expected_following_vehicle_ids={"following_vehicle_00"})
    outcome = acceptance["outcomes"]["exposed_people_accounted_for"]
    assert outcome["success"] is False
    failures = " ".join(outcome["failure_codes"])
    # Every person is broadcast-acknowledged, yet the unchanged business
    # semantics still fail closed on route admission, timing and arrival.
    for person_id in ROSTER_IDS[1:]:
        assert f"person:{person_id}:evacuation_route_not_admitted" in failures
        assert f"person:{person_id}:assembly_arrival_missing" in failures
    # The single ground-guided person still fails on the missing arrival
    # readback: broadcast ACKs never replace the arrival evidence.
    assert "person:" + ROSTER_IDS[0] + ":assembly_arrival_missing" in failures


# -- 5. skill catalog versioning ----------------------------------------------


def test_aerial_broadcast_publishes_only_in_catalog_v4():
    v4 = public_skill_catalog(version=CITY_AERIAL_SKILL_CATALOG_VERSION)
    v3 = public_skill_catalog(version=CITY_NATIVE_SKILL_CATALOG_VERSION)
    v2 = public_skill_catalog(version=SKILL_CATALOG_VERSION)
    assert "aerial_broadcast" in v4["skills"]
    assert "aerial_broadcast" not in v3["skills"]
    assert "aerial_broadcast" not in v2["skills"]
    assert "aerial_broadcast" not in skill_names(version=SKILL_CATALOG_VERSION)
    assert v4["skills"]["aerial_broadcast"]["terminal_semantics"] == \
        "aerial_arrival_and_broadcast_acknowledged_while_field_role_held"
    # The v3 guide_people override keeps applying identically at v4.
    for field in ("timeout_semantics", "evidence_types", "terminal_semantics",
                  "later_business_evidence"):
        assert v4["skills"]["guide_people"][field] == v3["skills"]["guide_people"][field]
    assert "terminal_semantics" not in v2["skills"]["guide_people"]
    # The frozen v2/v3 vocabularies stay exactly their historical selves.
    assert set(v2["skills"]) == set(v3["skills"])


def test_unknown_catalog_versions_fail_closed():
    for version in (5, 1, 0, -1):
        with pytest.raises(ValueError, match="unsupported skill catalog version"):
            public_skill_catalog(version=version)
    with pytest.raises(ValueError, match="unsupported skill catalog version"):
        skill_definition("guide_people", version=5)
    with pytest.raises(ValueError, match="not published by skill catalog version 2"):
        skill_definition("aerial_broadcast", version=SKILL_CATALOG_VERSION)
    with pytest.raises(ValueError, match="not published by skill catalog version 3"):
        skill_definition("aerial_broadcast", version=CITY_NATIVE_SKILL_CATALOG_VERSION)
    # Version-independent capability resolution serves every revision.
    assert required_capabilities("aerial_broadcast") == ("aerial_navigation", "relay")


# -- CLI surface ---------------------------------------------------------------


def test_city_episode_cli_pool_choices_include_v3(capsys):
    from demo.benchmark import goal_city_episode as episode

    with pytest.raises(SystemExit) as exc:
        episode.main(["--help"])
    assert exc.value.code == 0
    assert "{v1,v2,v3}" in capsys.readouterr().out


def test_v3_pool_reuses_the_frozen_v2_physical_probe_stage():
    from demo.benchmark import goal_city_episode as episode

    source = inspect.getsource(episode.run_city_episode)
    # The v3 goal roster runs on the unchanged v2 physical stage.
    assert 'probe_pool = "v2" if pool == "v3" else pool' in source
    assert '"v3": "bound_native_v3"' in source
    # The service probe's own frozen pool boundary stays v1/v2.
    from demo.city_benchmark import service_probe
    with pytest.raises(ValueError, match="unsupported City service probe pool"):
        service_probe._selection(17, "v3")
