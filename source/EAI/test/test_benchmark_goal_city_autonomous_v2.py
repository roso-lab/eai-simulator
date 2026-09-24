"""Goal-side integration tests for the explicit City support-footprint profile."""

from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace

import pytest

from demo.benchmark import goal_city_autonomous as autonomous
from demo.benchmark.goal_city_autonomous import (
    build_city_autonomous_contract,
    run_city_autonomous_context,
)
from demo.benchmark.goal_city_episode import stamp_arrival_times
from demo.city_benchmark.guide_admission import REQUESTED_TARGET_V2_PROFILE
from source.EAI.test.test_benchmark_goal_city_assembly_v2 import _fixture
from source.EAI.test.test_benchmark_goal_city_autonomous import _IdleAdapter
from source.EAI.test.test_benchmark_goal_city_runtime_observations import (
    _native_context,
    _runtime,
)
from source.EAI.test.test_benchmark_goal_city_episode import ROSTER_IDS


def test_v2_context_rejects_missing_or_mixed_inputs(tmp_path):
    provider = SimpleNamespace(semantic_map_sha256="a" * 64)
    footprint = {"profile": REQUESTED_TARGET_V2_PROFILE}
    support = lambda: {}
    cases = [
        ({"guide_execution_profile": "future"}, "unsupported"),
        ({"guide_execution_profile": REQUESTED_TARGET_V2_PROFILE}, "route provider"),
        ({"guide_route_provider": provider,
          "guide_execution_profile": REQUESTED_TARGET_V2_PROFILE}, "metered"),
        ({"guide_route_provider": provider,
          "guide_execution_profile": REQUESTED_TARGET_V2_PROFILE,
          "communication_runtime": object()}, "footprint"),
        ({"guide_route_provider": provider,
          "guide_execution_profile": "requested_target_v1",
          "communication_runtime": object(),
          "assembly_footprint_profile": footprint,
          "assembly_support_reader": support}, "require requested-target v2"),
    ]
    for kwargs, message in cases:
        with pytest.raises(ValueError, match=message):
            run_city_autonomous_context(
                {}, _IdleAdapter(), output_root=tmp_path / "invalid", **kwargs)


def test_v2_profile_agrees_across_service_initial_intake_and_projection(
        tmp_path, monkeypatch):
    from demo.benchmark import goal_city_initial_qualification as qualification

    public, raw, people = _fixture()
    ctx, _steps = _native_context(tmp_path / "ctx")
    observed = ROSTER_IDS[0]
    assert observed in people
    # The controlled native observation exposes one person, so the public
    # matrix must contain exactly that person's four choices.
    raw["routes"] = [row for row in raw["routes"] if row["person_id"] == observed]
    raw.update(episode_id="native-episode", source_id="isaac.city.runtime",
               captured_at_s=10.0, valid_until_s=11.0,
               assembly_support_measurement={
                   "schema": "eai.city.assembly_support", "version": 1,
                   "private_ray": "RAW_SUPPORT_MUST_STAY_PRIVATE"})
    footprint = {
        "schema": "eai.city.assembly_footprint_profile", "version": 2,
        "profile": REQUESTED_TARGET_V2_PROFILE,
        "semantic_map_sha256": raw["semantic_map_sha256"],
        "candidate_geometry_sha256": raw["candidate_geometry_sha256"],
        "candidates": [{key: deepcopy(row[key]) for key in (
            "region_id", "footprint", "center_xy_m", "radius_m",
            "support_plane_z_m", "capacity", "geometry_sha256")}
                       for row in raw["candidates"]],
    }
    previous = ctx["city"]._episode_service
    enabled = []
    service = SimpleNamespace(
        guidance_profile=None,
        people_controller=previous.people_controller,
        assembly_regions=previous.assembly_regions,
        assembly_affordances=lambda: deepcopy(raw),
    )

    def enable(**kwargs):
        enabled.append(kwargs)
        service.guidance_profile = kwargs["profile"]

    service.enable_requested_target_guidance = enable
    ctx["city"]._episode_service = service
    initial = {
        "schema": "eai.benchmark.city_initial_qualification", "version": 1,
        "episode_id": "v2-profile", "profile": REQUESTED_TARGET_V2_PROFILE,
        "execution_profile_enabled": True,
        "oracle_status": "failed", "feasibility_label": None,
        "platform_valid": False, "scoreable": False,
        "execution_validated": False, "business_outcomes_validated": False,
    }

    def capture(context, task, *, episode_id, clock_epoch_s):
        del task
        assert context["city"]._episode_service.guidance_profile == \
            REQUESTED_TARGET_V2_PROFILE
        assert episode_id == "v2-profile" and clock_epoch_s == 10.0
        return deepcopy(initial)

    monkeypatch.setattr(qualification, "capture_city_initial_qualification", capture)
    root = tmp_path / "episode"
    result, backend = run_city_autonomous_context(
        ctx, _IdleAdapter(), output_root=root, episode_id="v2-profile",
        communication_runtime=_runtime(),
        guide_route_provider=SimpleNamespace(semantic_map_sha256="a" * 64),
        guide_execution_profile=REQUESTED_TARGET_V2_PROFILE,
        assembly_footprint_profile=footprint,
        assembly_support_reader=lambda: {"trusted": True})

    assert "missing_deployment_assessment" in result["error"]
    assert service.guidance_profile == backend._ctx["city"]._episode_service.guidance_profile \
        == REQUESTED_TARGET_V2_PROFILE
    assert enabled[0]["profile"] == REQUESTED_TARGET_V2_PROFILE
    assert enabled[0]["assembly_footprint_profile"] == footprint
    assert callable(enabled[0]["assembly_support_reader"])
    assert json.loads((root / "city_initial_qualification.json").read_text()) == initial
    report = json.loads((root / "execution_observer_report.json").read_text())
    assert report["profile"]["assembly_profile"] == REQUESTED_TARGET_V2_PROFILE
    native = report["samples"][0]["native_observation"]["assembly_affordance"]
    projected = report["samples"][0]["public_projection"]
    assert native["assembly_support_measurement"]["private_ray"] == \
        "RAW_SUPPORT_MUST_STAY_PRIVATE"
    assert "RAW_SUPPORT_MUST_STAY_PRIVATE" not in json.dumps(projected)
    assert projected["assembly_affordance"]["profile"] == REQUESTED_TARGET_V2_PROFILE
    assert len(projected["assembly_affordance"]["routes"]) == 4
    assert report["scoreable"] is report["execution_validated"] is \
        report["business_outcomes_validated"] is False


def test_v2_arrival_stamp_uses_service_footprint_readback():
    controller = SimpleNamespace(
        motion_readback=lambda person_id: {"path_finished": person_id == "p1"},
        readback=lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("v2 must not use the legacy AABB controller readback")))
    service = SimpleNamespace(
        guidance_profile=REQUESTED_TARGET_V2_PROFILE,
        person_readback=lambda person_id: {"arrived": person_id == "p1"},
        people_controller=controller, assembly_regions={})
    city = SimpleNamespace(elapsed=7.5, _episode_service=service)
    stamps = {}
    stamp_arrival_times({"city": city, "roster_ids": ["p1"]}, stamps)
    assert stamps == {"p1": 7.5}


def test_episode_v2_builds_native_support_closure_and_archives_profile(
        tmp_path, monkeypatch):
    public, _api = build_city_autonomous_contract()
    regions = [row for row in public["public_world"]["regions"]
               if row["kind"] == "assembly_candidate"]
    centres = [(-35.0, 51.25), (0.5, 74.75), (60.0, 44.0), (35.0, -65.25)]
    semantic_map = {
        "schema": "eai.city.semantic_map", "version": 1,
        "coordinate_frame": "city_world", "up_axis": "Z", "meters_per_unit": 1.0,
        "surface_heights_m": {"sidewalks": 0.3}, "static_obstacles": [],
        "regions": [{"id": row["id"], "kind": "assembly_candidate",
                     "resource_capacity": 1} for row in regions],
        "bindings": [{"id": row["id"], "geometry": {
            "type": "Point", "coordinates": list(center)}, "candidate_radius_m": 0.75}
                     for row, center in zip(sorted(regions, key=lambda item: item["id"]), centres)],
    }
    map_path = tmp_path / "semantic_map.json"
    map_path.write_text(json.dumps(semantic_map), encoding="utf-8")
    captured = {}
    support_calls = []

    monkeypatch.setattr("demo.city_benchmark.service_probe.prepare_probe",
                        lambda *args, **kwargs: None)

    def fake_support(stage, query, **kwargs):
        support_calls.append((stage, query, kwargs))
        return {"schema": "eai.city.assembly_support", "version": 1}

    monkeypatch.setattr("demo.city_benchmark.assembly_support.measure_assembly_supports",
                        fake_support)
    monkeypatch.setattr(autonomous, "build_acceptance_evidence", lambda *args, **kwargs: {})
    monkeypatch.setattr(autonomous, "_refresh_scoring_archive_index",
                        lambda *args, **kwargs: None)

    def fake_context(ctx, adapter, **kwargs):
        del adapter
        captured.update(kwargs)
        kwargs["assembly_support_reader"]()
        backend = SimpleNamespace(clock_epoch_s=10.0, evidence=[])
        return ({"episode_id": "goal-v2", "error": "expected-stop", "turns": 1,
                 "skill_receipt_count": 0, "session_audit_validated": True}, backend)

    monkeypatch.setattr(autonomous, "run_city_autonomous_context", fake_context)

    def execute_probe(root, *, device, driver, pool):
        del device, pool
        episode_root = tmp_path / "goal_autonomous_episode"
        episode_root.mkdir(parents=True, exist_ok=True)
        report_path = tmp_path / "probe_report.json"
        reader = SimpleNamespace(
            query="QUERY", scene_queries_enabled=True, device="cpu",
            current_gpu_geometry=False)
        city = SimpleNamespace(
            elapsed=10.0, stage="STAGE",
            _episode_service=SimpleNamespace(region_reader=reader))
        runner = SimpleNamespace(adapter=SimpleNamespace(
            episode_id="native-episode", source_id="native-source"))
        ctx = {"report_path": report_path, "report": {"trace": {}},
               "city": city, "runner": runner,
               "session": SimpleNamespace(device="cpu")}
        driver(ctx)
        assert episode_root.joinpath("goal_city_autonomous_source.json").is_file()
        return ctx["report"]

    monkeypatch.setattr("demo.city_benchmark.service_probe.execute_probe", execute_probe)
    result = autonomous.run_city_autonomous_episode(
        tmp_path, _IdleAdapter(), communication_runtime=object(),
        guide_semantic_map_path=map_path,
        guide_execution_profile=REQUESTED_TARGET_V2_PROFILE)

    assert captured["guide_execution_profile"] == REQUESTED_TARGET_V2_PROFILE
    assert captured["assembly_footprint_profile"]["profile"] == REQUESTED_TARGET_V2_PROFILE
    assert support_calls[0][:2] == ("STAGE", "QUERY")
    params = support_calls[0][2]
    assert params["episode_id"] == "native-episode"
    assert params["source_id"] == "native-source"
    assert params["native_clock"]() == 10.0
    source = json.loads((tmp_path / "goal_autonomous_episode" /
                         "goal_city_autonomous_source.json").read_text())
    assert source["guide_admission_profile"] == REQUESTED_TARGET_V2_PROFILE
    assert source["scoreable"] is source["execution_validated"] is \
        source["business_outcomes_validated"] is False
    assert result["trace"]["goal_autonomous_episode"]["guide_admission_profile"] == \
        REQUESTED_TARGET_V2_PROFILE
