"""Synthetic fixtures for the positive-only Restaurant initial adapter."""

from copy import deepcopy
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
