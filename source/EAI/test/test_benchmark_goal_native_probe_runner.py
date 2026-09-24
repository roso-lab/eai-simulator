"""Offline tests for the native admission probe harness (no Isaac/ROS/torch)."""

import json

from demo.benchmark.goal_native_probe_runner import (
    HOST_PROBE_MODE,
    NATIVE_PROBE_EVIDENCE_SCHEMA,
    OFFLINE_PROBE_MODE,
    archive_probe_evidence,
    build_probe_harness,
    probe_collection_plan,
    registered_probe_scenes,
    validate_native_clock,
)
from demo.benchmark.goal_reader_city import (
    CITY_DEV_CASES,
    CITY_ENTITY_IDS,
    CITY_FACT_IDS,
    CITY_MEASUREMENT_POINTS,
    CITY_PREDICATE_BINDINGS,
    CITY_SOURCE_VERSIONS,
    query_city_development_case,
)
from demo.benchmark.goal_reader_factory import (
    FACTORY_ENTITY_IDS,
    FACTORY_MEASUREMENT_POINTS,
    query_factory_development_case,
)
from demo.benchmark.goal_reader_house import (
    HOUSE_DEV_CASES,
    HOUSE_ENTITY_IDS,
    HOUSE_FACT_IDS,
    HOUSE_MEASUREMENT_POINTS,
    HOUSE_PREDICATE_BINDINGS,
    HOUSE_SOURCE_VERSIONS,
    query_house_development_case,
)
from demo.benchmark.goal_reader_warehouse import (
    WAREHOUSE_ENTITY_IDS,
    WAREHOUSE_MEASUREMENT_POINTS,
    query_warehouse_development_case,
)

_ORACLE_FIELDS = ("feasibility_label", "oracle_status", "alternative_path_gate",
                  "platform_valid", "quality_failure", "measurement_id",
                  "validation_scope", "explored_state_count",
                  "registration_sha256", "state_sha256", "evidence_refs")


def _point_facts(points, measurement_id):
    point = next(p for p in points if p["id"] == measurement_id)
    return set(point["required_true"])


def _fake_native_provider(*, facts, time_s, entity_ids=None, entity_versions=None,
                          clock_epoch="offline-episode-1",
                          clock_id="offline-native-clock"):
    versions = dict(entity_versions or {entity: 1 for entity in entity_ids or ()})

    def provide(context):
        return {
            "time_s": time_s,
            "entity_versions": dict(versions),
            "true_facts": sorted(facts),
            "clock_epoch": clock_epoch,
            "clock_id": clock_id,
        }
    return provide


def _consistent_probe(predicate_bindings, source_id):
    expected = sorted(fact for fact, binding in predicate_bindings.items()
                      if binding["source_id"] == source_id)

    def probe(state):
        facts = set(state["true_facts"])
        return {fact: fact in facts for fact in expected}
    return probe


def _clock_sample(**overrides):
    row = {"measurement_id": "m1", "clock_epoch": "e1",
           "clock_id": "c1", "time_s": 1.0}
    row.update(overrides)
    return row


def test_unregistered_scene_is_rejected():
    for scene_key in ("restaurant", "not_a_scene"):
        try:
            build_probe_harness(scene_key)
        except ValueError:
            pass
        else:
            raise AssertionError(f"harness accepted unregistered scene: {scene_key}")
        try:
            probe_collection_plan(scene_key)
        except ValueError:
            pass
        else:
            raise AssertionError(f"plan accepted unregistered scene: {scene_key}")
    scenes = registered_probe_scenes()
    assert "modern_house_nvd" in scenes and "city" in scenes
    assert "warehouse_nvd" in scenes and "factory" in scenes


def test_host_probe_factories_must_cover_every_registered_source():
    try:
        build_probe_harness("modern_house_nvd", probe_factories={})
    except ValueError:
        pass
    else:
        raise AssertionError("harness accepted an incomplete probe factory set")
    factories = {source: _consistent_probe(HOUSE_PREDICATE_BINDINGS, source)
                 for source in HOUSE_SOURCE_VERSIONS}
    factories["house_unknown_source"] = _consistent_probe(
        HOUSE_PREDICATE_BINDINGS, "house_physx_objects")
    try:
        build_probe_harness("modern_house_nvd", probe_factories=factories)
    except ValueError:
        pass
    else:
        raise AssertionError("harness accepted an unknown probe source")
    harness = build_probe_harness("modern_house_nvd", probe_factories={
        source: _consistent_probe(HOUSE_PREDICATE_BINDINGS, source)
        for source in HOUSE_SOURCE_VERSIONS})
    assert harness.probe_mode == HOST_PROBE_MODE


def test_house_full_chain_with_deterministic_fake_probes_is_verified():
    harness = build_probe_harness("modern_house_nvd")
    assert harness.probe_mode == OFFLINE_PROBE_MODE
    result = harness.run_measurement(
        "full_team_initial",
        native_state_provider=_fake_native_provider(
            facts=_point_facts(HOUSE_MEASUREMENT_POINTS, "house_initial"),
            time_s=3.0, entity_ids=HOUSE_ENTITY_IDS))
    assert result["oracle_status"] == "verified"
    assert result["feasibility_label"] == "deployable"
    assert result["grounding_status"] == "verified"
    assert result["grounding_evidence_refs"]
    assert result["scene_key"] == "modern_house_nvd"
    assert result["case_id"] == "full_team_initial"
    assert result["measurement_id"] == "house_initial"
    marker = result["development_track"]
    assert marker["asset_status"] == "development_unverified"
    assert marker["evidence_class"] == "diagnostic"
    assert marker["infrastructure_blockers"] == ["house_native_admission_pending"]
    assert marker["release_eligible"] is False
    assert result["formal_statistics"] is False
    assert result["scoreable"] is False
    clock = result["native_clock"]
    assert clock["measurement_id"] == "house_initial"
    assert clock["clock_epoch"] == "offline-episode-1"
    assert clock["clock_id"] == "offline-native-clock"
    assert clock["time_s"] == 3.0
    assert len(harness.results) == 1


def test_city_full_chain_with_deterministic_fake_probes_is_verified():
    harness = build_probe_harness("city")
    result = harness.run_measurement(
        "full_team_initial",
        native_state_provider=_fake_native_provider(
            facts=_point_facts(CITY_MEASUREMENT_POINTS, "city_initial"),
            time_s=5.0, entity_ids=CITY_ENTITY_IDS))
    assert result["oracle_status"] == "verified"
    assert result["feasibility_label"] == "deployable"
    marker = result["development_track"]
    assert marker["infrastructure_blockers"] == ["city_native_admission_pending"]
    assert marker["release_eligible"] is False


def test_warehouse_and_factory_scenes_run_full_chain():
    for scene_key, entities, points, case_id, synthetic_query in (
            ("warehouse_nvd", WAREHOUSE_ENTITY_IDS, WAREHOUSE_MEASUREMENT_POINTS,
             "strong_single_robot_path", query_warehouse_development_case),
            ("factory", FACTORY_ENTITY_IDS, FACTORY_MEASUREMENT_POINTS,
             "full_team_initial", query_factory_development_case)):
        harness = build_probe_harness(scene_key)
        measurement_id = dict(harness.spec.dev_cases)[case_id][0]
        result = harness.run_measurement(
            case_id,
            native_state_provider=_fake_native_provider(
                facts=_point_facts(points, measurement_id),
                time_s=2.0, entity_ids=entities))
        synthetic = synthetic_query(case_id)
        assert result["oracle_status"] == "verified"
        assert result["feasibility_label"] == synthetic["feasibility_label"]
        assert result["oracle_status"] == synthetic["oracle_status"]


def test_validate_native_clock_contract_directly():
    ok = validate_native_clock([
        _clock_sample(time_s=1.0),
        _clock_sample(measurement_id="m2", time_s=1.0),
        _clock_sample(measurement_id="m3", time_s=2.5)])
    assert ok["status"] == "verified"
    assert ok["violations"] == []
    assert ok["clock_epochs"] == ["e1"]
    assert ok["sample_count"] == 3
    assert validate_native_clock([])["status"] == "verified"
    backwards = validate_native_clock([
        _clock_sample(time_s=2.0), _clock_sample(measurement_id="m2", time_s=1.5)])
    assert backwards["status"] == "failed"
    assert any(v.startswith("time_moved_backwards:") for v in backwards["violations"])
    negative = validate_native_clock([_clock_sample(time_s=-0.5)])
    assert negative["status"] == "failed"
    assert any(v.startswith("negative_time_s:") for v in negative["violations"])
    nonfinite = validate_native_clock([_clock_sample(time_s=float("nan"))])
    assert nonfinite["status"] == "failed"
    assert any("time_s_not_finite_number" in v for v in nonfinite["violations"])
    mixed = validate_native_clock([
        _clock_sample(), _clock_sample(measurement_id="m2", clock_id="c2")])
    assert any(v.startswith("clock_epoch_mixed_clocks:") for v in mixed["violations"])
    rebound = validate_native_clock([
        _clock_sample(time_s=5.0), _clock_sample(clock_epoch="e2", time_s=0.0)])
    assert any(v.startswith("measurement_rebound_across_clock_epochs:")
               for v in rebound["violations"])
    restart = validate_native_clock([
        _clock_sample(time_s=5.0),
        _clock_sample(measurement_id="m2", clock_epoch="e2", time_s=0.0)])
    assert restart["status"] == "verified"


def test_native_clock_time_backwards_fails_closed():
    harness = build_probe_harness("city")
    first = harness.run_measurement(
        "full_team_initial",
        native_state_provider=_fake_native_provider(
            facts=_point_facts(CITY_MEASUREMENT_POINTS, "city_initial"),
            time_s=5.0, entity_ids=CITY_ENTITY_IDS))
    assert first["oracle_status"] == "verified"
    second = harness.run_measurement(
        "full_team_initial",
        native_state_provider=_fake_native_provider(
            facts=_point_facts(CITY_MEASUREMENT_POINTS, "city_initial"),
            time_s=4.5, entity_ids=CITY_ENTITY_IDS))
    assert second["oracle_status"] == "failed"
    assert second["feasibility_label"] is None
    assert second["platform_valid"] is False
    assert second["failure_category"] == "native_clock_violation"
    assert second["quality_failure"].startswith("native_clock_violation:")
    assert second["native_clock"]["time_s"] == 4.5
    assert len(harness.clock_samples) == 2


def test_native_clock_negative_time_fails_closed():
    harness = build_probe_harness("modern_house_nvd")
    result = harness.run_measurement(
        "full_team_initial",
        native_state_provider=_fake_native_provider(
            facts=_point_facts(HOUSE_MEASUREMENT_POINTS, "house_initial"),
            time_s=-1.0, entity_ids=HOUSE_ENTITY_IDS))
    assert result["oracle_status"] == "failed"
    assert result["feasibility_label"] is None
    assert result["failure_category"] == "native_clock_violation"
    assert "negative_time_s" in result["quality_failure"]


def test_probe_exception_is_classified_probe_error():
    def raising_probe(state):
        raise RuntimeError("native physx query unavailable")

    factories = {source: _consistent_probe(HOUSE_PREDICATE_BINDINGS, source)
                 for source in HOUSE_SOURCE_VERSIONS}
    factories["house_physx_objects"] = raising_probe
    harness = build_probe_harness("modern_house_nvd", probe_factories=factories)
    result = harness.run_measurement(
        "full_team_initial",
        native_state_provider=_fake_native_provider(
            facts=_point_facts(HOUSE_MEASUREMENT_POINTS, "house_initial"),
            time_s=3.0, entity_ids=HOUSE_ENTITY_IDS))
    assert result["oracle_status"] == "failed"
    assert result["feasibility_label"] is None
    assert result["failure_category"] == "probe_error"
    assert result["quality_failure"].startswith("probe_error:house_physx_objects:")
    assert "RuntimeError" in result["quality_failure"]


def test_version_binding_mismatch_fails_closed():
    harness = build_probe_harness("city")
    facts = _point_facts(CITY_MEASUREMENT_POINTS, "city_initial")
    incomplete = {entity: 1 for entity in CITY_ENTITY_IDS}
    del incomplete["person_1"]
    missing = harness.run_measurement(
        "full_team_initial",
        native_state_provider=_fake_native_provider(
            facts=facts, time_s=5.0, entity_versions=incomplete))
    assert missing["oracle_status"] == "failed"
    assert missing["feasibility_label"] is None
    assert missing["failure_category"] == "version_binding_mismatch"
    assert "missing_entities:person_1" in missing["quality_failure"]
    ghost = {**{entity: 1 for entity in CITY_ENTITY_IDS}, "ghost_entity": 1}
    extra = harness.run_measurement(
        "full_team_initial",
        native_state_provider=_fake_native_provider(
            facts=facts, time_s=5.5, entity_versions=ghost))
    assert extra["failure_category"] == "version_binding_mismatch"
    assert "unknown_entities:ghost_entity" in extra["quality_failure"]


def test_archive_probe_evidence_schema_marker_and_release_flags(tmp_path):
    house = build_probe_harness("modern_house_nvd")
    ok = house.run_measurement(
        "full_team_initial",
        native_state_provider=_fake_native_provider(
            facts=_point_facts(HOUSE_MEASUREMENT_POINTS, "house_initial"),
            time_s=3.0, entity_ids=HOUSE_ENTITY_IDS))
    city = build_probe_harness("city")
    bad = city.run_measurement(
        "full_team_initial",
        native_state_provider=_fake_native_provider(
            facts=_point_facts(CITY_MEASUREMENT_POINTS, "city_initial"),
            time_s=-1.0, entity_ids=CITY_ENTITY_IDS))
    output = tmp_path / "nested" / "probe_evidence.json"
    archive = archive_probe_evidence([ok, bad], output)
    assert archive["schema"] == NATIVE_PROBE_EVIDENCE_SCHEMA
    assert archive["release_eligible"] is False
    assert archive["formal_statistics"] is False
    for flag in ("execution_validated", "business_outcomes_validated", "scoreable"):
        assert archive[flag] is False
    assert archive["measurement_count"] == 2
    assert archive["native_clock_validation"]["status"] == "failed"
    assert archive["development_track"]["schema"] == "eai.benchmark.development_track_boundary"
    assert sorted(archive["scene_keys"]) == ["city", "modern_house_nvd"]
    rows = {row["measurement_id"]: row for row in archive["measurements"]}
    house_row = rows["house_initial"]
    assert house_row["feasibility_label"] == "deployable"
    assert house_row["oracle_status"] == "verified"
    assert house_row["grounding"]["status"] == "verified"
    assert house_row["grounding"]["evidence_refs"]
    assert house_row["evidence_refs"]
    assert house_row["native_clock"]["clock_epoch"] == "offline-episode-1"
    assert house_row["native_clock"]["time_s"] == 3.0
    assert house_row["development_track"]["scene_key"] == "modern_house_nvd"
    assert house_row["formal_statistics"] is False
    assert house_row["release_eligible"] is False
    city_row = rows["city_initial"]
    assert city_row["oracle_status"] == "failed"
    assert city_row["feasibility_label"] is None
    assert city_row["failure_category"] == "native_clock_violation"
    stored = json.loads(output.read_text(encoding="utf-8"))
    assert stored == archive
    assert len(stored["evidence_sha256"]) == 64


def test_probe_collection_plan_covers_all_points_and_sources():
    plan = probe_collection_plan("modern_house_nvd")
    assert plan["schema"] == "eai.benchmark.native_probe_collection_plan"
    assert [p["id"] for p in plan["measurement_points"]] == [
        p["id"] for p in HOUSE_MEASUREMENT_POINTS]
    assert set(plan["sources"]) == set(HOUSE_SOURCE_VERSIONS)
    measured = []
    for source_id, row in plan["sources"].items():
        assert row["source_version"] == HOUSE_SOURCE_VERSIONS[source_id]
        assert row["predicates"] and row["entity_ids"]
        assert set(row["entity_ids"]) <= set(HOUSE_ENTITY_IDS)
        measured.extend(row["predicates"])
    assert sorted(measured) == sorted(HOUSE_FACT_IDS)
    for point in plan["measurement_points"]:
        assert point["facts_to_measure"] == sorted(HOUSE_FACT_IDS)
        assert point["required_entity_version_ids"] == sorted(HOUSE_ENTITY_IDS)
    assert {case["case_id"] for case in plan["contrast_cases"]} == set(HOUSE_DEV_CASES)
    for case in plan["contrast_cases"]:
        assert case["measurement_id"] == HOUSE_DEV_CASES[case["case_id"]][0]
        assert case["robots"] and case["resources"]
    city_plan = probe_collection_plan("city")
    assert set(city_plan["sources"]) == set(CITY_SOURCE_VERSIONS)
    city_measured = [fact for row in city_plan["sources"].values()
                     for fact in row["predicates"]]
    assert sorted(city_measured) == sorted(CITY_FACT_IDS)
    assert len(city_plan["measurement_points"]) == len(CITY_MEASUREMENT_POINTS)


def test_native_fake_probe_mode_matches_synthetic_backend():
    house = build_probe_harness("modern_house_nvd")
    for case_id in ("full_team_initial", "budget_exhausted"):
        synthetic = query_house_development_case(case_id)
        measurement_id = HOUSE_DEV_CASES[case_id][0]
        native = house.run_measurement(
            case_id,
            native_state_provider=_fake_native_provider(
                facts=_point_facts(HOUSE_MEASUREMENT_POINTS, measurement_id),
                time_s=3.0, entity_ids=HOUSE_ENTITY_IDS))
        for field in _ORACLE_FIELDS:
            assert native.get(field) == synthetic.get(field), (case_id, field)
    city = build_probe_harness("city")
    for case_id in ("full_team_initial", "ground_exits_aerial_remains"):
        synthetic = query_city_development_case(case_id)
        measurement_id = CITY_DEV_CASES[case_id][0]
        native = city.run_measurement(
            case_id,
            native_state_provider=_fake_native_provider(
                facts=_point_facts(CITY_MEASUREMENT_POINTS, measurement_id),
                time_s=5.0, entity_ids=CITY_ENTITY_IDS))
        for field in _ORACLE_FIELDS:
            assert native.get(field) == synthetic.get(field), (case_id, field)


def test_probe_disagreeing_with_provider_facts_fails_closed():
    factories = {source: _consistent_probe(CITY_PREDICATE_BINDINGS, source)
                 for source in CITY_SOURCE_VERSIONS}
    factories["city_people_tracking"] = lambda state: {"people_accounted": True}
    harness = build_probe_harness("city", probe_factories=factories)
    result = harness.run_measurement(
        "full_team_initial",
        native_state_provider=_fake_native_provider(
            facts=_point_facts(CITY_MEASUREMENT_POINTS, "city_initial"),
            time_s=5.0, entity_ids=CITY_ENTITY_IDS))
    assert result["oracle_status"] == "failed"
    assert result["feasibility_label"] is None
    assert result["quality_failure"] == "trusted_state_grounding_failed"
    assert "disagrees with state" in result["grounding_quality_failure"]
