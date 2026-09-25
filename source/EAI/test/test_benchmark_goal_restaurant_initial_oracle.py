"""Synthetic fixtures for the positive-only Restaurant initial adapter."""

from copy import deepcopy
import hashlib
import json

import pytest

from demo.benchmark.goal_restaurant_deployment_v2 import build_restaurant_deployment_v2
from demo.benchmark.provenance import canonical_sha256
from demo.benchmark import goal_restaurant_initial_oracle as oracle


def _hex(label):
    return canonical_sha256({"synthetic_fixture": label})


def _fixture():
    contract = build_restaurant_deployment_v2()
    public, deployment = contract["public_task"], contract["deployment"]
    pool = {row["id"]: deepcopy(row) for row in public["candidate_robot_pool"]}
    regions = deepcopy(deployment["spawn_regions"])
    roots = {}
    root_domains = {}
    public_spawns = public["public_world"]["robot_spawn_positions"]
    for agent in pool:
        xyz = [float(value) for value in public_spawns[agent]]
        root = [*xyz, 1.0, 0.0, 0.0, 0.0, *([0.0] * 6)]
        roots[agent] = {
            "public_agent_id": agent,
            "native_agent_id": agent.removeprefix("restaurant_"),
            "physics_step": 962,
            "time_s": 0.0,
            "root_state_w": root,
        }
        root_domains[agent] = {
            "center": list(root),
            "abs_tolerance": [1e-3] * 13,
        }

    dependency_manifest = {
        path: _hex(path) for path in sorted(oracle._REQUIRED_DEPENDENCIES)
    }
    controllers = {
        path: dependency_manifest[path] for path in oracle._CONTROLLER_PATHS
    }
    authorities = {
        "evidence_scope": "synthetic_fixture",
        "task_condition_binding_sha256": _hex("condition"),
        "execution_dependency_manifest": dependency_manifest,
        "asset_manifest_sha256": _hex("assets"),
        "controller_source_sha256": controllers,
        "runtime_profile_sha256": _hex("runtime-profile"),
    }
    versions = {agent: 0 for agent in pool}
    certificate = {
        "schema": oracle.CERTIFICATE_SCHEMA,
        "version": oracle.CERTIFICATE_VERSION,
        "visibility": "evaluator_only",
        "qualification": {
            "scope": "synthetic_fixture",
            "evidence_refs": ["fixture:stage57-positive-domain"],
            "calibration_manifest_sha256": _hex("calibration"),
            "allowed_runtime_profile_sha256": [authorities["runtime_profile_sha256"]],
            "calibration_trials": [],
        },
        "identities": {
            "public_task_sha256": canonical_sha256(public),
            "deployment_sha256": canonical_sha256(deployment),
            "task_condition_binding_sha256": authorities[
                "task_condition_binding_sha256"],
            "execution_dependency_manifest_sha256": canonical_sha256(
                dependency_manifest),
            "asset_manifest_sha256": authorities["asset_manifest_sha256"],
            "controller_source_sha256": controllers,
        },
        "execution_dependency_paths": list(dependency_manifest),
        "initial_domain": {
            "assessment_epoch": 0,
            "time_s": 0.0,
            "roster_sha256": canonical_sha256(pool),
            "roster_version": 0,
            "capability_versions": versions,
            "resource_versions": {},
            "deployment_change_versions": {},
            "robot_regions_sha256": canonical_sha256(regions),
            "root_domains": root_domains,
        },
        "action_domains": oracle._expected_actions(),
        "registration_sha256": "",
    }
    certificate["registration_sha256"] = oracle._build_registration(
        certificate)["registration_sha256"]
    certificate_sha256 = canonical_sha256(certificate)

    archive = {"path": "/synthetic/epoch.json", "sha256": _hex("epoch")}
    initial_regions = {
        agent: {
            "status": "measured_region_membership",
            "region_id": region,
            "region_error_m": 0.0,
            "region_tolerance_m": 0.5,
            "source": deployment["profile"],
        }
        for agent, region in regions.items()
    }
    state = {
        "schema": "eai.restaurant.synthetic_initial_state_fixture",
        "version": 1,
        "status": "measured",
        "task_id": public["task_id"],
        "public_task_sha256": canonical_sha256(public),
        "deployment_profile": deployment["profile"],
        "deployment_sha256": canonical_sha256(deployment),
        "episode_id": "synthetic-stage57",
        "origin_physics_step": 962,
        "time_s": 0.0,
        "native_initial_roots": roots,
        "initial_regions": initial_regions,
        "robot_regions": regions,
        "platform_gaps": [],
        "participant_error": False,
        "all_eight_initial_regions_available": True,
        "source_epoch_archive": archive,
        "post_warmup_sample_measured": True,
        "warmup_stability_certified": False,
    }
    runtime = {"time_s": 0.0, "robot_regions": regions, "object_states": {}}
    gate = {
        "enabled": True,
        "status": "required",
        "assessment_epoch": 0,
        "assessment_id": "deployment-assessment-0",
        "trigger_event_sequence": 0,
        "trigger_event_type": "episode_started",
        "roster_version": 0,
        "capability_versions": versions,
        "current_agent_ids": sorted(pool),
        "current_robot_pool": pool,
        "resource_versions": {},
        "deployment_change_versions": {},
        "runtime_state": runtime,
        "runtime_state_sha256": canonical_sha256(runtime),
        "public_task_sha256": canonical_sha256(public),
        "completed_outcome_ids": [],
        "external_evaluation": None,
        "oracle_evaluations": [],
        "history": [],
        "terminated": False,
        "termination": None,
    }
    trusted = {
        "session": {
            "episode_id": state["episode_id"],
            "current_time_s": 0.0,
            "runtime_state": runtime,
            "deployment_assessment": gate,
        },
        "deployment_binding": {
            "schema": "eai.restaurant.deployment_ack",
            "version": 2,
            "profile": deployment["profile"],
            "deployment_sha256": canonical_sha256(deployment),
            "public_task_sha256": canonical_sha256(public),
            "episode_id": state["episode_id"],
            "native_initial_state_sha256": canonical_sha256(state),
            "native_epoch_archive": archive,
        },
        "native_initial_state": state,
    }
    return certificate, certificate_sha256, authorities, trusted


def _evaluator(certificate=None, digest=None, authorities=None):
    base, base_digest, base_authorities, _trusted = _fixture()
    return oracle.RestaurantInitialPositiveOracle(
        certificate if certificate is not None else base,
        expected_certificate_sha256=digest if digest is not None else base_digest,
        trusted_authorities=authorities if authorities is not None else base_authorities,
    )


def _rebind_state(trusted):
    state = trusted["native_initial_state"]
    trusted["deployment_binding"]["native_initial_state_sha256"] = canonical_sha256(state)


def _epoch_agent(agent, root, *, physics_step, time_s):
    return {
        "public_agent_id": agent,
        "native_agent_id": agent.removeprefix("restaurant_"),
        "physics_step": physics_step,
        "time_s": time_s,
        "root_state_w": list(root),
    }


def _write_epoch(tmp_path, trusted, archive):
    encoded = (json.dumps(archive, ensure_ascii=False, indent=2,
                          sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
    path = tmp_path / "synthetic-v2-epoch.json"
    path.write_bytes(encoded)
    reference = {"path": str(path), "sha256": hashlib.sha256(encoded).hexdigest()}
    trusted["native_initial_state"]["source_epoch_archive"] = reference
    trusted["deployment_binding"]["native_epoch_archive"] = deepcopy(reference)
    _rebind_state(trusted)
    return path


def _v2_fixture(tmp_path):
    certificate, _digest, authorities, trusted = _fixture()
    state = trusted["native_initial_state"]
    coexistence = oracle.build_restaurant_initial_coexistence_domain()
    certificate["version"] = oracle.CERTIFICATE_VERSION_V2
    certificate["coexistence_domain"] = coexistence
    certificate["initial_domain"]["root_domains"] = {
        agent: certificate["initial_domain"]["root_domains"][agent]
        for agent in (oracle.CARTER_1, oracle.CARTER_2)
    }
    for agent, binding in coexistence["spawn_region_bindings"].items():
        state["initial_regions"][agent]["region_center_m"] = list(binding["center_m"])
        state["initial_regions"][agent]["region_tolerance_m"] = binding["tolerance_m"]

    origin_step = state["origin_physics_step"]
    pre_step = origin_step - coexistence["warmup_ticks"]
    pre_agents = {
        agent: _epoch_agent(
            agent, sample["root_state_w"], physics_step=pre_step, time_s=0.0)
        for agent, sample in state["native_initial_roots"].items()
    }
    ticks = []
    for index in range(coexistence["warmup_ticks"]):
        physics_step = pre_step + index + 1
        time_s = (index + 1) * 0.02
        ticks.append({
            "schema": "eai.restaurant.native_fleet_tick",
            "version": 1,
            "warmup_index": index,
            "start_physics_step": physics_step - 1,
            "physics_step": physics_step,
            "time_s": time_s,
            "agents": {
                agent: _epoch_agent(
                    agent, sample["root_state_w"],
                    physics_step=physics_step, time_s=time_s)
                for agent, sample in state["native_initial_roots"].items()
            },
        })
    post_agents = {
        agent: _epoch_agent(
            agent, sample["root_state_w"], physics_step=origin_step, time_s=0.0)
        for agent, sample in state["native_initial_roots"].items()
    }
    archive = {
        "schema": "eai.restaurant.native_episode_warmup_archive",
        "version": 1,
        "episode_id": state["episode_id"],
        "task_id": state["task_id"],
        "public_task_sha256": state["public_task_sha256"],
        "deployment_profile": state["deployment_profile"],
        "deployment_sha256": state["deployment_sha256"],
        "old_epoch": {
            "origin_physics_step": pre_step,
            "pre_warmup_physics_step": pre_step,
            "pre_warmup_time_s": 0.0,
            "pre_warmup_agents": pre_agents,
        },
        "warmup_ticks": ticks,
        "post_warmup_sample": {
            "physics_step": origin_step,
            "time_s": 0.0,
            "agents": post_agents,
        },
        "hold_targets_before_warmup": {
            agent: list(sample["root_state_w"][:3])
            for agent, sample in pre_agents.items()
        },
        "hold_yaws_before_warmup": {agent: 0.0 for agent in pre_agents},
        "controller_instances_preserved": True,
        "post_warmup_sample_measured": True,
        "warmup_stability_certified": False,
        "scoreable": False,
        "execution_validated": False,
        "business_outcomes_validated": False,
        "feasibility_oracle_verified": False,
        "release_ready": False,
    }
    path = _write_epoch(tmp_path, trusted, archive)
    certificate["registration_sha256"] = oracle._build_registration(
        certificate)["registration_sha256"]
    return certificate, canonical_sha256(certificate), authorities, trusted, archive, path


def test_pinned_synthetic_certificate_proves_only_the_positive_fixture():
    certificate, digest, authorities, trusted = _fixture()
    assert oracle.validate_restaurant_initial_positive_certificate(
        certificate, expected_certificate_sha256=digest) == certificate
    evaluator = oracle.RestaurantInitialPositiveOracle(
        certificate, expected_certificate_sha256=digest,
        trusted_authorities=authorities)

    result = evaluator(trusted["session"]["episode_id"], trusted)
    assert result["oracle_status"] == "verified"
    assert result["feasibility_label"] == "deployable"
    assert result["platform_valid"] is True
    evidence = evaluator.report()
    assert evidence["qualification_scope"] == "synthetic_fixture"
    assert evidence["results"][0]["feasibility_oracle_verified"] is False
    assert evidence["release_ready"] is False and evidence["scoreable"] is False


@pytest.mark.parametrize("drift", ["pin", "task", "root", "roster", "epoch", "runtime"])
def test_hash_task_domain_roster_and_epoch_drift_fail_closed(drift):
    certificate, digest, authorities, trusted = _fixture()
    if drift == "pin":
        digest = "0" * 64
    elif drift == "task":
        trusted["session"]["deployment_assessment"]["public_task_sha256"] = "0" * 64
    elif drift == "root":
        trusted["native_initial_state"]["native_initial_roots"][
            "restaurant_m20_1"]["root_state_w"][0] += 1.0
        _rebind_state(trusted)
    elif drift == "roster":
        trusted["session"]["deployment_assessment"]["current_robot_pool"].pop(
            "restaurant_iris_2")
    elif drift == "epoch":
        trusted["session"]["deployment_assessment"]["assessment_epoch"] = 1
    else:
        authorities["runtime_profile_sha256"] = _hex("foreign-runtime-profile")
    evaluator = oracle.RestaurantInitialPositiveOracle(
        certificate, expected_certificate_sha256=digest,
        trusted_authorities=authorities)
    result = evaluator(trusted["session"]["episode_id"], trusted)
    assert result["oracle_status"] == "failed"
    assert result["feasibility_label"] is None
    assert result["platform_valid"] is False


def test_insufficient_or_changed_operational_windows_fail_certificate_validation():
    certificate, _digest, authorities, trusted = _fixture()
    certificate["action_domains"]["construction_completion_limit_ms"] = 240_001
    digest = canonical_sha256(certificate)
    evaluator = oracle.RestaurantInitialPositiveOracle(
        certificate, expected_certificate_sha256=digest,
        trusted_authorities=authorities)
    result = evaluator(trusted["session"]["episode_id"], trusted)
    assert result["oracle_status"] == "failed"
    assert evaluator.report()["configuration_status"] == "failed"


@pytest.mark.parametrize("solver_result", [
    {"oracle_status": "verified", "feasibility_label": "not_deployable",
     "platform_valid": True},
    {"oracle_status": "failed", "feasibility_label": None,
     "platform_valid": False, "quality_failure": "oracle_search_budget_exhausted"},
])
def test_generic_negative_and_search_failure_are_suppressed(monkeypatch, solver_result):
    certificate, digest, authorities, trusted = _fixture()
    monkeypatch.setattr(oracle, "query_remaining_feasibility",
                        lambda *args, **kwargs: deepcopy(solver_result))
    evaluator = oracle.RestaurantInitialPositiveOracle(
        certificate, expected_certificate_sha256=digest,
        trusted_authorities=authorities)
    result = evaluator(trusted["session"]["episode_id"], trusted)
    assert result["oracle_status"] == "failed"
    assert result["feasibility_label"] is None
    assert "not_deployable" not in json.dumps(evaluator.report())


def test_later_outcomes_cannot_mutate_the_cached_initial_result():
    certificate, digest, authorities, trusted = _fixture()
    evaluator = oracle.RestaurantInitialPositiveOracle(
        certificate, expected_certificate_sha256=digest,
        trusted_authorities=authorities)
    first = evaluator(trusted["session"]["episode_id"], trusted)
    trusted["session"]["deployment_assessment"]["history"].append(
        {"participant_decision": "not_deployable"})
    trusted["session"]["future_outcomes"] = {"task": "failed"}
    trusted["native_initial_state"]["native_initial_roots"][
        "restaurant_carter_1"]["root_state_w"][0] += 100.0
    second = evaluator(trusted["session"]["episode_id"], trusted)
    assert first == second
    assert first["feasibility_label"] == "deployable"
    assert len(evaluator.report()["results"]) == 1


def test_public_result_and_evidence_report_never_expose_construction_or_roots():
    certificate, digest, authorities, trusted = _fixture()
    evaluator = oracle.RestaurantInitialPositiveOracle(
        certificate, expected_certificate_sha256=digest,
        trusted_authorities=authorities)
    result = evaluator(trusted["session"]["episode_id"], trusted)
    serialized = json.dumps({"result": result, "report": evaluator.report()})
    for hidden in (
        "action_domains", "root_domains", "root_state_w",
        oracle.KITCHEN_ROUTE, oracle.DINING_ROUTE,
        "qualified_parallel_service_inspections",
    ):
        assert hidden not in serialized


def test_synthetic_certificate_cannot_qualify_a_native_state_schema():
    certificate, digest, authorities, trusted = _fixture()
    trusted["native_initial_state"]["schema"] = "eai.restaurant.native_initial_state"
    _rebind_state(trusted)
    evaluator = oracle.RestaurantInitialPositiveOracle(
        certificate, expected_certificate_sha256=digest,
        trusted_authorities=authorities)
    result = evaluator(trusted["session"]["episode_id"], trusted)
    assert result["oracle_status"] == "failed"
    assert result["feasibility_label"] is None


@pytest.mark.parametrize("path", [
    "tmp/runtime.json",
    "demo/benchmark/*.py",
    "calibration/initial-oracle-certificate.json",
])
def test_dependency_authority_rejects_outputs_wildcards_and_certificate_json(path):
    certificate, digest, authorities, trusted = _fixture()
    authorities["execution_dependency_manifest"][path] = _hex(path)
    evaluator = oracle.RestaurantInitialPositiveOracle(
        certificate, expected_certificate_sha256=digest,
        trusted_authorities=authorities)
    result = evaluator(trusted["session"]["episode_id"], trusted)
    assert result["oracle_status"] == "failed"
    assert evaluator.report()["configuration_status"] == "failed"


@pytest.mark.parametrize("mutation", ["missing_slot", "duplicate_slot", "duplicate_archive", "broadened_domain"])
def test_native_certificate_shape_cannot_omit_trials_or_expand_v1_domain(mutation):
    # Deliberately fictional native-labelled inputs exercise validation only;
    # this test neither creates a real certificate nor evaluates a native state.
    certificate, _, _, _ = _fixture()
    qualification = certificate["qualification"]
    qualification["scope"] = "native_empirical_domain"
    qualification["evidence_refs"] = ["native:fictional-schema-test-only"]
    qualification["calibration_trials"] = [
        {"host": host, "seed": seed, "raw_archive_sha256": _hex(f"{host}-{seed}")}
        for host in ("local", "ljy") for seed in (17, 41, 73)]
    if mutation == "missing_slot":
        qualification["calibration_trials"].pop()
    elif mutation == "duplicate_slot":
        qualification["calibration_trials"][-1] = qualification["calibration_trials"][0]
    elif mutation == "duplicate_archive":
        qualification["calibration_trials"][-1]["raw_archive_sha256"] = qualification[
            "calibration_trials"][0]["raw_archive_sha256"]
    else:
        certificate["initial_domain"]["root_domains"][oracle.CARTER_1]["abs_tolerance"][0] = .011
    certificate["registration_sha256"] = oracle._build_registration(certificate)["registration_sha256"]
    with pytest.raises(ValueError, match="calibration slots|tolerance envelope"):
        oracle.validate_restaurant_initial_positive_certificate(
            certificate, expected_certificate_sha256=canonical_sha256(certificate))


def test_v2_separates_carter_action_roots_from_full8_coexistence(tmp_path):
    certificate, digest, authorities, trusted, _archive, _path = _v2_fixture(tmp_path)

    validated = oracle.validate_restaurant_initial_positive_certificate(
        certificate, expected_certificate_sha256=digest)
    assert set(validated["initial_domain"]["root_domains"]) == {
        oracle.CARTER_1, oracle.CARTER_2}
    assert set(validated["initial_domain"]["capability_versions"]) == set(
        validated["coexistence_domain"]["spawn_region_bindings"])
    coexistence = oracle.validate_restaurant_initial_coexistence(
        trusted["native_initial_state"], validated["coexistence_domain"])
    assert coexistence["warmup_ticks"] == 240
    assert len(coexistence["agent_ids"]) == 8

    evaluator = oracle.RestaurantInitialPositiveOracle(
        certificate, expected_certificate_sha256=digest,
        trusted_authorities=authorities)
    result = evaluator(trusted["session"]["episode_id"], trusted)
    assert result["oracle_status"] == "verified"
    assert result["feasibility_label"] == "deployable"
    assert result["evaluator_ref"] == oracle.EVALUATOR_REF_V2
    evidence = evaluator.report()["results"][0]
    assert evidence["qualification_scope"] == "synthetic_fixture"
    assert evidence["feasibility_oracle_verified"] is False


@pytest.mark.parametrize("mutation", [
    "archive_bytes", "missing_archive", "warmup_count", "hold_target",
    "post_root", "membership", "archive_identity", "controller_preservation",
    "warmup_roster", "native_roster_identity", "release_flag",
])
def test_v2_epoch_bytes_hold_and_membership_fail_closed(tmp_path, mutation):
    certificate, digest, authorities, trusted, archive, path = _v2_fixture(tmp_path)
    if mutation == "archive_bytes":
        path.write_bytes(path.read_bytes() + b" ")
    elif mutation == "missing_archive":
        path.unlink()
    elif mutation == "warmup_count":
        archive["warmup_ticks"].pop()
        _write_epoch(tmp_path, trusted, archive)
    elif mutation == "hold_target":
        archive["hold_targets_before_warmup"][oracle.CARTER_1][0] += 0.1
        _write_epoch(tmp_path, trusted, archive)
    elif mutation == "post_root":
        archive["post_warmup_sample"]["agents"][oracle.CARTER_1][
            "root_state_w"][0] += 0.1
        _write_epoch(tmp_path, trusted, archive)
    elif mutation == "membership":
        trusted["native_initial_state"]["initial_regions"][oracle.CARTER_1][
            "region_error_m"] = 0.1
        _rebind_state(trusted)
    elif mutation == "archive_identity":
        archive["episode_id"] = "foreign-episode"
        _write_epoch(tmp_path, trusted, archive)
    elif mutation == "controller_preservation":
        archive["controller_instances_preserved"] = False
        _write_epoch(tmp_path, trusted, archive)
    elif mutation == "warmup_roster":
        archive["warmup_ticks"][0]["agents"].pop("restaurant_iris_2")
        _write_epoch(tmp_path, trusted, archive)
    elif mutation == "native_roster_identity":
        archive["old_epoch"]["pre_warmup_agents"]["restaurant_m20_1"][
            "native_agent_id"] = "m20_2"
        _write_epoch(tmp_path, trusted, archive)
    else:
        archive["release_ready"] = True
        _write_epoch(tmp_path, trusted, archive)

    evaluator = oracle.RestaurantInitialPositiveOracle(
        certificate, expected_certificate_sha256=digest,
        trusted_authorities=authorities)
    result = evaluator(trusted["session"]["episode_id"], trusted)
    assert result["oracle_status"] == "failed"
    assert result["feasibility_label"] is None
    assert evaluator.report()["results"][0]["quality_failure"] == (
        "initial_coexistence_validation_failed")


@pytest.mark.parametrize("mutation", [
    "idle_action_root", "missing_capability", "foreign_coexistence",
])
def test_v2_certificate_keeps_exact_action_and_coexistence_domains(tmp_path, mutation):
    certificate, _digest, _authorities, _trusted, _archive, _path = _v2_fixture(tmp_path)
    if mutation == "idle_action_root":
        certificate["initial_domain"]["root_domains"]["restaurant_m20_1"] = deepcopy(
            certificate["initial_domain"]["root_domains"][oracle.CARTER_1])
    elif mutation == "missing_capability":
        certificate["initial_domain"]["capability_versions"].pop("restaurant_iris_2")
    else:
        certificate["coexistence_domain"]["spawn_region_bindings"][
            "restaurant_m20_1"]["tolerance_m"] += 0.1
    certificate["registration_sha256"] = oracle._build_registration(
        certificate)["registration_sha256"]
    with pytest.raises(ValueError, match="separate Carter|coexistence domain"):
        oracle.validate_restaurant_initial_positive_certificate(
            certificate, expected_certificate_sha256=canonical_sha256(certificate))


@pytest.mark.parametrize("mutation", [
    "missing_profile", "duplicate_slot", "duplicate_archive", "foreign_profile",
])
def test_v2_native_trials_cover_each_profile_without_fixed_seed_grid(tmp_path, mutation):
    certificate, _digest, _authorities, _trusted, _archive, _path = _v2_fixture(tmp_path)
    qualification = certificate["qualification"]
    profile_a, profile_b = _hex("runtime-a"), _hex("runtime-b")
    qualification["scope"] = "native_empirical_domain"
    qualification["evidence_refs"] = ["native:stage60-v2-shape-only"]
    qualification["allowed_runtime_profile_sha256"] = [profile_a, profile_b]
    qualification["calibration_trials"] = [
        {"host": "local", "seed": 97, "raw_archive_sha256": _hex("local-97"),
         "runtime_profile_sha256": profile_a},
        {"host": "ljy", "seed": 97, "raw_archive_sha256": _hex("ljy-97"),
         "runtime_profile_sha256": profile_b},
    ]
    if mutation == "missing_profile":
        qualification["calibration_trials"].pop()
    elif mutation == "duplicate_slot":
        qualification["calibration_trials"][1]["host"] = "local"
    elif mutation == "duplicate_archive":
        qualification["calibration_trials"][1]["raw_archive_sha256"] = (
            qualification["calibration_trials"][0]["raw_archive_sha256"])
    else:
        qualification["calibration_trials"][1]["runtime_profile_sha256"] = _hex(
            "foreign-runtime")
    certificate["registration_sha256"] = oracle._build_registration(
        certificate)["registration_sha256"]
    with pytest.raises(ValueError, match="every runtime profile|unique slots|outside the allowlist"):
        oracle.validate_restaurant_initial_positive_certificate(
            certificate, expected_certificate_sha256=canonical_sha256(certificate))


def test_v2_native_trials_accept_one_unique_slot_per_allowed_profile(tmp_path):
    certificate, _digest, _authorities, _trusted, _archive, _path = _v2_fixture(tmp_path)
    qualification = certificate["qualification"]
    profiles = [_hex("runtime-a"), _hex("runtime-b")]
    qualification.update({
        "scope": "native_empirical_domain",
        "evidence_refs": ["native:stage60-v2-shape-only"],
        "allowed_runtime_profile_sha256": profiles,
        "calibration_trials": [
            {"host": host, "seed": 97,
             "raw_archive_sha256": _hex(f"{host}-97"),
             "runtime_profile_sha256": profile}
            for host, profile in zip(("local", "ljy"), profiles)
        ],
    })
    certificate["registration_sha256"] = oracle._build_registration(
        certificate)["registration_sha256"]
    digest = canonical_sha256(certificate)
    assert oracle.validate_restaurant_initial_positive_certificate(
        certificate, expected_certificate_sha256=digest) == certificate


def test_v1_schema_and_evaluator_reference_remain_unchanged():
    certificate, digest, authorities, trusted = _fixture()
    assert "coexistence_domain" not in certificate
    validated = oracle.validate_restaurant_initial_positive_certificate(
        certificate, expected_certificate_sha256=digest)
    assert validated["version"] == 1
    assert len(validated["initial_domain"]["root_domains"]) == 8
    result = oracle.RestaurantInitialPositiveOracle(
        certificate, expected_certificate_sha256=digest,
        trusted_authorities=authorities)(trusted["session"]["episode_id"], trusted)
    assert result["evaluator_ref"] == oracle.EVALUATOR_REF
