"""Issuer trust-boundary tests; fixtures do not certify native execution."""

from copy import deepcopy

import pytest

from demo.benchmark import goal_restaurant_oracle_qualification as qualification
from demo.benchmark import goal_restaurant_initial_oracle as oracle
from demo.benchmark.provenance import canonical_sha256
from source.EAI.test.test_benchmark_goal_restaurant_initial_oracle import _fixture


def _manifest():
    certificate, _digest, authority, _trusted = _fixture()
    certificate["version"] = 2
    certificate["coexistence_domain"] = oracle.build_restaurant_initial_coexistence_domain()
    certificate["initial_domain"]["root_domains"] = {
        key: value for key, value in certificate["initial_domain"]["root_domains"].items()
        if key in qualification.CARTERS}
    profiles = {"local": {"fixture_profile": "local"}, "ljy": {"fixture_profile": "ljy"}}
    certificate["qualification"].update(
        scope="native_empirical_domain", evidence_refs=[], calibration_manifest_sha256="",
        allowed_runtime_profile_sha256=sorted(canonical_sha256(value) for value in profiles.values()))
    certificate["registration_sha256"] = ""
    authorities = {}
    for host, profile in profiles.items():
        authorities[host] = {**deepcopy(authority), "evidence_scope": "native_empirical_domain",
                             "runtime_profile_sha256": canonical_sha256(profile)}
    return {"schema": qualification.MANIFEST_SCHEMA, "version": 2,
            "run": {"device": "cpu", "warmup_ticks": 240, "max_ticks": 4200,
                    "timeout_s": 900, "native_retries": 0},
            "certificate_template": certificate, "runtime_profiles": profiles,
            "authorities_by_host": authorities, "communication_contract": {"fixture": True},
            "trials": [{"host": host, "seed": 97, "episode_id": f"fixture-{host}",
                        "output": f"/fixture/{host}"} for host in profiles]}


def _reviews(manifest):
    result = []
    for trial in manifest["trials"]:
        raw = {"fixture.json": canonical_sha256(trial)}
        result.append({"schema": qualification.REVIEW_SCHEMA, "version": 2,
                       "accepted": True, "host": trial["host"], "seed": trial["seed"],
                       "episode_id": trial["episode_id"],
                       "manifest_sha256": canonical_sha256(manifest), "raw_files": raw,
                       "raw_archive_sha256": canonical_sha256(raw),
                       "runtime_profile_sha256": canonical_sha256(manifest["runtime_profiles"][trial["host"]]),
                       **qualification.FLAGS})
    return result


def _issue(manifest, reviews):
    return qualification.issue_certificate(
        manifest, expected_manifest_sha256=canonical_sha256(manifest),
        reviews=reviews, expected_review_sha256=[canonical_sha256(row) for row in reviews])


def test_issuer_uses_all_externally_pinned_fixture_reviews_without_promoting_release():
    manifest = _manifest()
    result = _issue(manifest, _reviews(manifest))
    certificate = result["certificate"]
    assert certificate["version"] == 2
    assert len(certificate["qualification"]["calibration_trials"]) == 2
    assert result["certificate_sha256"] == canonical_sha256(certificate)
    assert all(result[key] is False for key in qualification.FLAGS)
    assert manifest["certificate_template"]["qualification"]["calibration_trials"] == []


@pytest.mark.parametrize("fault", ["rejected", "duplicate", "raw_digest", "profile", "manifest", "release"])
def test_issuer_rejects_incomplete_or_inconsistent_reviews_even_when_pinned(fault):
    manifest = _manifest()
    reviews = _reviews(manifest)
    if fault == "rejected":
        reviews[0]["accepted"] = False
    elif fault == "duplicate":
        reviews[1] = deepcopy(reviews[0])
    elif fault == "raw_digest":
        reviews[0]["raw_files"]["fixture.json"] = "0" * 64
    elif fault == "profile":
        reviews[0]["runtime_profile_sha256"] = "0" * 64
    elif fault == "manifest":
        reviews[0]["manifest_sha256"] = "0" * 64
    else:
        reviews[0]["scoreable"] = True
    with pytest.raises(ValueError):
        _issue(manifest, reviews)


def test_manifest_and_review_pins_are_independent_not_self_seals():
    manifest = _manifest()
    reviews = _reviews(manifest)
    with pytest.raises(ValueError, match="independent pin"):
        qualification.validate_calibration_manifest(manifest, expected_manifest_sha256="0" * 64)
    with pytest.raises(ValueError, match="independent pin"):
        qualification.issue_certificate(manifest, expected_manifest_sha256=canonical_sha256(manifest),
            reviews=reviews, expected_review_sha256=["0" * 64] * 2)


@pytest.mark.parametrize("fault", ["missing_host", "duplicate_slot", "expanded_region", "wrong_action_actor",
                                  "expanded_root", "changed_window", "retry"])
def test_preregistered_scope_cannot_silently_drop_hosts_or_expand_domains(fault):
    manifest = _manifest()
    if fault == "missing_host":
        manifest["trials"].pop()
    elif fault == "duplicate_slot":
        manifest["trials"].append(deepcopy(manifest["trials"][0]))
    elif fault == "expanded_region":
        manifest["certificate_template"]["coexistence_domain"]["spawn_region_bindings"]["restaurant_m20_1"]["tolerance_m"] = 100
    elif fault == "wrong_action_actor":
        roots = manifest["certificate_template"]["initial_domain"]["root_domains"]
        roots["restaurant_m20_1"] = roots.pop("restaurant_carter_1")
    elif fault == "expanded_root":
        manifest["certificate_template"]["initial_domain"]["root_domains"]["restaurant_carter_1"]["abs_tolerance"][0] = 100.
    elif fault == "changed_window":
        manifest["certificate_template"]["action_domains"]["kitchen_inspection"]["navigation_window_ms"][1] = 90000
    else:
        manifest["run"]["native_retries"] = 1
    with pytest.raises(ValueError):
        qualification.validate_calibration_manifest(manifest, expected_manifest_sha256=canonical_sha256(manifest))


def _trajectory():
    template = _manifest()["certificate_template"]
    agents = {}
    for agent, binding in template["coexistence_domain"]["spawn_region_bindings"].items():
        center = binding["center_m"]
        xyz = [*center, 0.0] if len(center) == 2 else center
        agents[agent] = {"root_state_w": [*xyz, 1., 0., 0., 0., *([0.] * 6)]}
    row = {"physics_step": 966, "time_s": .02, "agents": agents}
    row["evidence_ref"] = "sha256:" + canonical_sha256(row)
    tick = {"index": 0, "start_time_s": 0., "time_s": .02, "fleet_tick_advanced": True,
            "fleet_tick_physics_step": 966, "region_record_ref": row["evidence_ref"]}
    return {"ticks": [tick], "region_records": [row]}, template


def test_coexistence_checks_actual_idle_space_not_idle_microstate():
    pump, template = _trajectory()
    row = pump["region_records"][0]
    row["agents"]["restaurant_m20_1"]["root_state_w"][0] += .2
    row.pop("evidence_ref")
    row["evidence_ref"] = "sha256:" + canonical_sha256(row)
    pump["ticks"][0]["region_record_ref"] = row["evidence_ref"]
    assert qualification._check_coexistence_trajectory(pump, template)["tick_count"] == 1


@pytest.mark.parametrize("fault", ["outside", "missing_actor", "stale_tick", "missing_tick", "digest"])
def test_coexistence_rejects_drift_or_sampling_gaps(fault):
    pump, template = _trajectory()
    row = pump["region_records"][0]
    if fault == "outside":
        row["agents"]["restaurant_m20_1"]["root_state_w"][0] += 2
    elif fault == "missing_actor":
        row["agents"].pop("restaurant_m20_1")
    elif fault == "stale_tick":
        row["physics_step"] -= 4
    elif fault == "missing_tick":
        pump["ticks"] = []
    else:
        row["evidence_ref"] = "bad"
    if fault != "digest":
        row.pop("evidence_ref")
        row["evidence_ref"] = "sha256:" + canonical_sha256(row)
        if pump["ticks"]:
            pump["ticks"][0]["region_record_ref"] = row["evidence_ref"]
    with pytest.raises(ValueError):
        qualification._check_coexistence_trajectory(pump, template)


def _construction():
    from demo.benchmark.goal_restaurant_deployment_v2 import build_restaurant_deployment_v2
    from demo.benchmark.goal_restaurant_public_policy import PublicRestaurantParticipant

    contract = build_restaurant_deployment_v2()
    participant = PublicRestaurantParticipant()
    participant.initialize_deployment(contract["deployment"])
    participant.initialize(contract["public_task"], contract["participant_api"])
    tasks, visible, observations = {}, [], {}
    for spec in participant.plan["tasks"]:
        ident = spec["task_id"]
        start = spec["execution"]["start_s"]
        tasks[ident] = {"task": spec, "physical_started_at_s": start,
                        "physical_terminal_at_s": start + 1,
                        "terminal_delivery": {"status": "succeeded", "message_id": ident,
                                              "result_delivered_at_s": start + 2}}
        if spec["skill"] == "observe":
            observation = {"producer_id": spec["assigned_agents"][0], "region_id": spec["arguments"]["region_id"]}
            observations[ident] = observation
            visible.append({**observation, "task_id": ident, "service_observation_id": ident,
                            "result_message_id": ident, "result_delivered_at_s": start + 2,
                            "valid_until_s": 91.})
    service = {"observations": observations,
               "relay_publications": [{"message_id": "relay", "time_s": 72., "observation_refs": list(observations)}],
               "receipt": {"relay_message_id": "relay", "time_s": 73., "observation_refs": list(observations)}}
    return {"tasks": tasks, "visible_readiness_observations": visible,
            "readiness": {"readiness_service": service}}


def test_calibration_binds_distinct_public_policy_actors_and_delivered_observations():
    assert qualification._check_construction(_construction()) == 1.


@pytest.mark.parametrize("fault", ["duplicate_actor", "missing_task", "prerequisite", "capability",
    "schedule", "subject", "delivery", "publication_refs", "receipt_refs", "expired"])
def test_calibration_rejects_self_consistent_but_wrong_construction(fault):
    pump = _construction()
    tasks = pump["tasks"]
    service = pump["readiness"]["readiness_service"]
    if fault == "duplicate_actor":
        for ident in ("nav-1", "observe-1"):
            tasks[ident]["task"]["assigned_agents"] = ["restaurant_carter_1"]
    elif fault == "missing_task":
        tasks.pop("observe-1")
    elif fault == "prerequisite":
        tasks["relay"]["task"]["prerequisites"] = ["observe-0"]
    elif fault == "capability":
        tasks["observe-1"]["task"]["required_capabilities"] = []
    elif fault == "schedule":
        tasks["observe-1"]["task"]["execution"]["start_s"] = 50.
    elif fault == "subject":
        tasks["observe-1"]["task"]["arguments"]["subject_ref"] = "kitchen_service"
    elif fault == "delivery":
        pump["visible_readiness_observations"][1]["result_message_id"] = "observe-0"
    elif fault == "publication_refs":
        service["relay_publications"][0]["observation_refs"] = ["observe-0", "observe-0"]
    elif fault == "receipt_refs":
        service["receipt"]["observation_refs"] = ["observe-0"]
    else:
        pump["visible_readiness_observations"][1]["valid_until_s"] = 70.
    with pytest.raises(ValueError):
        qualification._check_construction(pump)


@pytest.mark.parametrize("fault", [None, "all_plan_tasks_terminal", "bounded_tick_limit_reached",
                                  "trusted_episode_horizon_termination", "excess_ticks", "no_ticks"])
def test_bounded_calibration_completion(fault):
    report = {"all_plan_tasks_terminal": True, "bounded_tick_limit_reached": False,
              "trusted_episode_horizon_termination": False}
    pump = {"ticks": [{}] * 3}
    if fault in report:
        report[fault] = not report[fault]
    elif fault == "excess_ticks":
        pump["ticks"].append({})
    elif fault == "no_ticks":
        pump["ticks"] = []
    if fault is None:
        qualification._check_bounded_completion(report, pump, {"max_ticks": 3})
    else:
        with pytest.raises(ValueError, match="bounded run"):
            qualification._check_bounded_completion(report, pump, {"max_ticks": 3})
