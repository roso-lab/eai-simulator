"""Offline synthetic contrasts for the Restaurant runtime reassessment oracle.

Every fixture here is a synthetic offline construction: the initial v1
certificate fixture mirrors ``test_benchmark_goal_restaurant_initial_oracle``
and the extensions are explicitly issued run-state documents.  The four R1
contrast classes exercised are: still feasible after a redundant-capability
member exit; infeasible after the unique critical capability is lost without
a substitute; physically terminal but with the readiness receipt unfinished;
and a run-time state outside any issued extension domain.
"""

from copy import deepcopy
import json

import pytest

from demo.benchmark.goal_alternative_path_certificate import (
    build_alternative_path_certificate,
)
from demo.benchmark.goal_restaurant_deployment_v2 import build_restaurant_deployment_v2
from demo.benchmark.provenance import canonical_sha256
from demo.benchmark import goal_restaurant_initial_oracle as initial
from demo.benchmark import goal_restaurant_reassessment as reassessment


def _hex(label):
    return canonical_sha256({"synthetic_fixture": label})


def _context(profiles=None):
    """Build a synthetic v1 certificate, authorities, and pristine report."""
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
        root_domains[agent] = {"center": list(root), "abs_tolerance": [1e-3] * 13}

    runtime_profile = profiles[0] if profiles is not None else _hex("runtime-profile")
    dependency_manifest = {
        path: _hex(path) for path in sorted(initial._REQUIRED_DEPENDENCIES)
    }
    controllers = {
        path: dependency_manifest[path] for path in initial._CONTROLLER_PATHS
    }
    authorities = {
        "evidence_scope": "synthetic_fixture",
        "task_condition_binding_sha256": _hex("condition"),
        "execution_dependency_manifest": dependency_manifest,
        "asset_manifest_sha256": _hex("assets"),
        "controller_source_sha256": controllers,
        "runtime_profile_sha256": runtime_profile,
    }
    versions = {agent: 0 for agent in pool}
    certificate = {
        "schema": initial.CERTIFICATE_SCHEMA,
        "version": initial.CERTIFICATE_VERSION,
        "visibility": "evaluator_only",
        "qualification": {
            "scope": "synthetic_fixture",
            "evidence_refs": ["fixture:stage57-positive-domain"],
            "calibration_manifest_sha256": _hex("calibration"),
            "allowed_runtime_profile_sha256": (
                list(profiles) if profiles is not None else [runtime_profile]),
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
        "action_domains": initial._expected_actions(),
        "registration_sha256": "",
    }
    certificate["registration_sha256"] = initial._build_registration(
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
    episode_id = "synthetic-r1-reassessment"
    state = {
        "schema": "eai.restaurant.synthetic_initial_state_fixture",
        "version": 1,
        "status": "measured",
        "task_id": public["task_id"],
        "public_task_sha256": canonical_sha256(public),
        "deployment_profile": deployment["profile"],
        "deployment_sha256": canonical_sha256(deployment),
        "episode_id": episode_id,
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
            "episode_id": episode_id,
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
            "episode_id": episode_id,
            "native_initial_state_sha256": canonical_sha256(state),
            "native_epoch_archive": archive,
        },
        "native_initial_state": state,
    }
    return {
        "episode_id": episode_id,
        "certificate": certificate,
        "certificate_sha256": certificate_sha256,
        "authorities": authorities,
        "trusted": trusted,
        "public": public,
        "pool": pool,
        "regions": regions,
    }


def _extension(ctx, *, episode_id=None, epoch=1, trigger="member_exit", remaining=None,
               facts=(), at_s=30.0, max_active=2, roster_version=None,
               version_bumps=None, alt=False, allowed_profiles=None,
               bound_certificate=None):
    """Issue one synthetic run-state extension for an explicit post-change epoch."""
    certificate = ctx["certificate"]
    full8 = sorted(certificate["initial_domain"]["capability_versions"])
    if remaining is None:
        remaining = [agent for agent in full8 if agent != initial.CARTER_1]
    remaining = sorted(remaining)
    facts = sorted(facts)
    point = {
        "id": f"restaurant_reassessment_epoch_{epoch}",
        "trigger": trigger,
        "required_true": facts,
        "required_false": [],
        "varying_facts": [fact for fact in initial._FACTS if fact not in facts],
    }
    capability_versions = {agent: 0 for agent in remaining}
    capability_versions.update(version_bumps or {})
    domain = {
        "assessment_epoch": epoch,
        "trigger_event_type": trigger,
        "measurement_id": point["id"],
        "measurement_point": point,
        "time_s": float(at_s),
        "remaining_time_ms": int(round(initial.HARD_BUDGET_MS - at_s * 1000)),
        "max_active_agents": max_active,
        "roster_version": (
            roster_version if roster_version is not None
            else (1 if trigger in ("member_exit", "capability_change") else 0)),
        "capability_versions": capability_versions,
        "current_agent_ids": remaining,
        "resource_versions": {},
        "deployment_change_versions": {},
    }
    extension = {
        "schema": reassessment.EXTENSION_SCHEMA,
        "version": reassessment.EXTENSION_VERSION,
        "visibility": "evaluator_only",
        "bound_certificate_sha256": (
            bound_certificate if bound_certificate is not None
            else ctx["certificate_sha256"]),
        "episode_id": episode_id if episode_id is not None else ctx["episode_id"],
        "qualification": {
            "scope": "synthetic_fixture",
            "evidence_refs": ["fixture:stage-r1-reassessment"],
            "extension_manifest_sha256": _hex("extension-manifest"),
            "allowed_runtime_profile_sha256": (
                list(allowed_profiles) if allowed_profiles is not None
                else [ctx["authorities"]["runtime_profile_sha256"]]),
            "extension_trials": [],
        },
        "run_state_domains": [domain],
        "registration_sha256": "",
    }
    extension["registration_sha256"] = reassessment._freeze_extension_registration(
        extension, certificate)["registration_sha256"]
    if alt:
        registration = reassessment._freeze_extension_registration(
            extension, certificate)
        extension["alternative_path_certificate"] = build_alternative_path_certificate(
            "restaurant", registration,
            path_classes={"remaining_service_and_relay_slots": {
                "description": "Every legal remaining completion staffs the two "
                               "service inspection slots and the single readiness "
                               "relay slot with currently declared capabilities.",
                "covered_transitions": [
                    "qualified_parallel_service_inspections",
                    "qualified_readiness_receipt"],
            }},
            covered_measurement_ids=[point["id"]],
            uncovered_path_policy="treated_as_uncovered",
            independent_review="verified",
            evidence_refs=["fixture:alternative-path-completeness"],
        )
    return extension


def _runtime_report(ctx, extension, *, pool_overrides=None, gate_overrides=None,
                    runtime_overrides=None):
    """Build the trusted post-change session report for one extension epoch."""
    domain = extension["run_state_domains"][0]
    pool = {agent: deepcopy(ctx["pool"][agent]) for agent in domain["current_agent_ids"]}
    pool.update({agent: deepcopy(row) for agent, row in (pool_overrides or {}).items()})
    runtime = {
        "time_s": domain["time_s"],
        "robot_regions": {
            agent: ctx["regions"][agent] for agent in domain["current_agent_ids"]},
        "object_states": {},
    }
    runtime.update(runtime_overrides or {})
    gate = {
        "enabled": True,
        "status": "required",
        "assessment_epoch": domain["assessment_epoch"],
        "assessment_id": f"deployment-assessment-{domain['assessment_epoch']}",
        "trigger_event_sequence": 7,
        "trigger_event_type": domain["trigger_event_type"],
        "triggered_at_s": domain["time_s"] - 0.5,
        "roster_version": domain["roster_version"],
        "capability_versions": dict(domain["capability_versions"]),
        "current_agent_ids": list(domain["current_agent_ids"]),
        "current_robot_pool": pool,
        "resource_versions": dict(domain["resource_versions"]),
        "deployment_change_versions": dict(domain["deployment_change_versions"]),
        "public_task_sha256": canonical_sha256(ctx["public"]),
        "completed_outcome_ids": [],
        "external_evaluation": None,
        "oracle_evaluations": [],
        "history": [{"assessment_epoch": 0, "decision": "deployable"}],
        "terminated": False,
        "termination": None,
    }
    gate.update(gate_overrides or {})
    gate["runtime_state"] = runtime
    gate["runtime_state_sha256"] = canonical_sha256(runtime)
    return {"session": {
        "episode_id": extension["episode_id"],
        "current_time_s": domain["time_s"],
        "runtime_state": deepcopy(runtime),
        "deployment_assessment": gate,
    }}


def _oracle(ctx, extensions=(), pins=None):
    extensions = list(extensions)
    pins = pins if pins is not None else [canonical_sha256(ext) for ext in extensions]
    return reassessment.RestaurantReassessmentOracle(
        ctx["certificate"], expected_certificate_sha256=ctx["certificate_sha256"],
        trusted_authorities=ctx["authorities"],
        extensions=extensions, expected_extension_sha256=pins)


def test_redundant_capability_member_exit_stays_deployable():
    ctx = _context()
    extension = _extension(ctx)  # restaurant_carter_1 exits at t=30
    evaluator = _oracle(ctx, [extension])
    result = evaluator(extension["episode_id"], _runtime_report(ctx, extension))
    assert result["oracle_status"] == "verified"
    assert result["feasibility_label"] == "deployable"
    assert result["platform_valid"] is True
    assert result["evaluator_ref"] == reassessment.EVALUATOR_REF
    rows = evaluator.reassessment_report()["results"]
    assert rows[0]["extension_sha256"] == canonical_sha256(extension)
    assert rows[0]["feasibility_oracle_verified"] is False


@pytest.mark.parametrize("with_completeness_certificate", [True, False])
def test_unique_relay_capability_loss_without_substitute(with_completeness_certificate):
    ctx = _context()
    # Both inspections are physically done, every relay-capable member has
    # exited, and only restaurant_lite3_1 (no relay) remains.
    extension = _extension(
        ctx, remaining=["restaurant_lite3_1"],
        facts=["kitchen_ready", "dining_ready"], at_s=82.0,
        alt=with_completeness_certificate)
    evaluator = _oracle(ctx, [extension])
    result = evaluator(extension["episode_id"], _runtime_report(ctx, extension))
    if with_completeness_certificate:
        assert result["oracle_status"] == "verified"
        assert result["feasibility_label"] == "not_deployable"
        assert result["platform_valid"] is True
        row = evaluator.reassessment_report()["results"][0]
        assert row["alternative_path_gate"] == "verified"
    else:
        # Same discipline as the initial oracle: an unattested negative solver
        # answer is a platform failure, never a formal negative label.
        assert result["oracle_status"] == "failed"
        assert result["feasibility_label"] is None
        assert result["platform_valid"] is False
        row = evaluator.reassessment_report()["results"][0]
        assert row["quality_failure"] == "not_deployable_without_certificate"


def test_physical_endgame_with_unfinished_receipt_stays_deployable():
    ctx = _context()
    full8 = sorted(ctx["certificate"]["initial_domain"]["capability_versions"])
    extension = _extension(
        ctx, trigger="milestone_state_change", remaining=full8,
        facts=["kitchen_ready", "dining_ready"], at_s=82.0, roster_version=0)
    evaluator = _oracle(ctx, [extension])
    result = evaluator(extension["episode_id"], _runtime_report(ctx, extension))
    assert result["oracle_status"] == "verified"
    assert result["feasibility_label"] == "deployable"
    assert result["platform_valid"] is True


def test_capability_change_epoch_uses_the_bumped_declaration():
    ctx = _context()
    full8 = sorted(ctx["certificate"]["initial_domain"]["capability_versions"])
    reduced = deepcopy(ctx["pool"]["restaurant_carter_2"])
    reduced["public_capabilities"] = ["ground_navigation", "observe"]
    extension = _extension(
        ctx, trigger="capability_change", remaining=full8,
        version_bumps={"restaurant_carter_2": 1})
    report = _runtime_report(
        ctx, extension, pool_overrides={"restaurant_carter_2": reduced})
    evaluator = _oracle(ctx, [extension])
    result = evaluator(extension["episode_id"], report)
    assert result["oracle_status"] == "verified"
    assert result["feasibility_label"] == "deployable"


def test_uncovered_epoch_or_foreign_episode_fails_closed():
    ctx = _context()
    evaluator = _oracle(ctx, [_extension(ctx, episode_id="other-episode")])
    local = _extension(ctx)
    result = evaluator(local["episode_id"], _runtime_report(ctx, local))
    assert result["oracle_status"] == "failed"
    assert result["feasibility_label"] is None
    assert evaluator.reassessment_report()["results"][0]["quality_failure"] == (
        "runtime_epoch_outside_qualified_domain")
    # An epoch nobody issued an extension for is equally out of domain.
    later = _extension(ctx, epoch=2)
    second = evaluator(local["episode_id"], _runtime_report(ctx, later))
    assert second["oracle_status"] == "failed"
    assert second["assessment_epoch"] == 2


def test_missing_extension_evidence_fails_closed():
    ctx = _context()
    evaluator = _oracle(ctx)
    extension = _extension(ctx)
    result = evaluator(extension["episode_id"], _runtime_report(ctx, extension))
    assert result["oracle_status"] == "failed"
    assert result["feasibility_label"] is None
    assert result["platform_valid"] is False
    report = evaluator.reassessment_report()
    assert report["configuration_status"] == "validated"
    assert report["results"][0]["quality_failure"] == (
        "runtime_epoch_outside_qualified_domain")


def test_pristine_no_change_epoch_matches_the_initial_oracle_label():
    ctx = _context()
    evaluator = _oracle(ctx)
    baseline = initial.RestaurantInitialPositiveOracle(
        ctx["certificate"], expected_certificate_sha256=ctx["certificate_sha256"],
        trusted_authorities=ctx["authorities"])(ctx["episode_id"], ctx["trusted"])
    reassessed = evaluator(ctx["episode_id"], ctx["trusted"])
    assert baseline["feasibility_label"] == reassessed["feasibility_label"] == "deployable"
    assert baseline["oracle_status"] == reassessed["oracle_status"] == "verified"
    assert reassessed["evaluator_ref"] == reassessment.EVALUATOR_REF
    assert baseline["evaluator_ref"] != reassessed["evaluator_ref"]
    row = evaluator.reassessment_report()["results"][0]
    assert row["delegated_evaluator_ref"] == baseline["evaluator_ref"]
    assert row["extension_sha256"] is None
    # A no-longer-pristine epoch without any issued extension stays failed.
    drifted = deepcopy(ctx["trusted"])
    drifted["session"]["deployment_assessment"]["assessment_epoch"] = 1
    assert evaluator(ctx["episode_id"], drifted)["oracle_status"] == "failed"


def test_judgements_are_frozen_per_assessment_epoch():
    ctx = _context()
    first = _extension(ctx, epoch=1, at_s=30.0)
    remaining_after_exit = [
        agent for agent in sorted(
            ctx["certificate"]["initial_domain"]["capability_versions"])
        if agent != initial.CARTER_1]
    second = _extension(
        ctx, epoch=2, trigger="milestone_state_change",
        remaining=remaining_after_exit,
        facts=["kitchen_ready", "dining_ready"], at_s=82.0, roster_version=1)
    evaluator = _oracle(ctx, [first, second])
    report = _runtime_report(ctx, first)
    frozen = evaluator(ctx["episode_id"], report)
    assert frozen["feasibility_label"] == "deployable"
    report["session"]["deployment_assessment"]["history"].append({"late": True})
    report["session"]["deployment_assessment"]["runtime_state"]["robot_regions"][
        "restaurant_carter_2"] = "kitchen_service"
    report["session"]["future_outcomes"] = {"tasks": "failed"}
    assert evaluator(ctx["episode_id"], report) == frozen
    milestone = evaluator(ctx["episode_id"], _runtime_report(ctx, second))
    assert milestone["feasibility_label"] == "deployable"
    assert milestone["assessment_epoch"] == 2
    results = evaluator.reassessment_report()["results"]
    assert [row["assessment_epoch"] for row in results] == [1, 2]


@pytest.mark.parametrize("tamper", [
    "certificate_pin", "extension_pin", "foreign_binding", "epoch_zero_domain",
])
def test_tampered_certificate_or_extension_is_rejected(tamper):
    ctx = _context()
    extension = _extension(ctx)
    if tamper == "certificate_pin":
        evaluator = reassessment.RestaurantReassessmentOracle(
            ctx["certificate"], expected_certificate_sha256="0" * 64,
            trusted_authorities=ctx["authorities"],
            extensions=[extension],
            expected_extension_sha256=[canonical_sha256(extension)])
    elif tamper == "extension_pin":
        pin = canonical_sha256(extension)
        extension["run_state_domains"][0]["remaining_time_ms"] += 1
        evaluator = _oracle(ctx, [extension], pins=[pin])
    elif tamper == "foreign_binding":
        extension = _extension(ctx, bound_certificate="2" * 64)
        evaluator = _oracle(ctx, [extension])
    else:
        extension["run_state_domains"][0]["assessment_epoch"] = 0
        evaluator = _oracle(ctx, [extension])
    assert evaluator.reassessment_report()["configuration_status"] == "failed"
    result = evaluator(extension["episode_id"], _runtime_report(ctx, extension))
    assert result["oracle_status"] == "failed"
    assert result["feasibility_label"] is None
    assert result["platform_valid"] is False


@pytest.mark.parametrize("drift", [
    "roster_version", "capability_versions", "current_agent_ids",
    "runtime_time", "object_states", "trigger_type", "terminated",
])
def test_runtime_state_drift_from_the_issued_domain_fails_closed(drift):
    ctx = _context()
    extension = _extension(ctx)
    gate_overrides, runtime_overrides = {}, {}
    if drift == "roster_version":
        gate_overrides["roster_version"] = 2
    elif drift == "capability_versions":
        gate_overrides["capability_versions"] = {
            **extension["run_state_domains"][0]["capability_versions"],
            "restaurant_carter_2": 3}
    elif drift == "current_agent_ids":
        gate_overrides["current_agent_ids"] = sorted(
            set(extension["run_state_domains"][0]["current_agent_ids"])
            - {"restaurant_go2_1"})
    elif drift == "runtime_time":
        runtime_overrides["time_s"] = 31.0
    elif drift == "object_states":
        runtime_overrides["object_states"] = {"tray": {"region_id": "kitchen_service"}}
    elif drift == "trigger_type":
        gate_overrides["trigger_event_type"] = "capability_change"
    else:
        gate_overrides["terminated"] = True
    evaluator = _oracle(ctx, [extension])
    result = evaluator(extension["episode_id"], _runtime_report(
        ctx, extension, gate_overrides=gate_overrides,
        runtime_overrides=runtime_overrides))
    assert result["oracle_status"] == "failed"
    assert result["feasibility_label"] is None
    assert result["platform_valid"] is False


def test_runtime_profile_must_stay_inside_the_extension_allowlist():
    profile_a, profile_b = _hex("profile-a"), _hex("profile-b")
    ctx = _context(profiles=[profile_a, profile_b])
    extension = _extension(ctx, allowed_profiles=[profile_b])
    evaluator = _oracle(ctx, [extension])
    result = evaluator(extension["episode_id"], _runtime_report(ctx, extension))
    assert result["oracle_status"] == "failed"
    assert result["feasibility_label"] is None
    rows = evaluator.reassessment_report()["results"]
    assert rows[-1]["quality_failure"] == "runtime_profile_outside_qualified_domain"


@pytest.mark.parametrize("mutation", [
    "bad_partition", "empty_domains", "broadened_profile", "time_budget",
    "registration", "roster_leaves_full8",
])
def test_extension_validation_rejects_structural_tampering(mutation):
    ctx = _context()
    extension = _extension(ctx)
    if mutation == "bad_partition":
        point = extension["run_state_domains"][0]["measurement_point"]
        point["required_false"] = ["readiness_receipt_committed"]
    elif mutation == "empty_domains":
        extension["run_state_domains"] = []
    elif mutation == "broadened_profile":
        extension["qualification"]["allowed_runtime_profile_sha256"] = [
            _hex("foreign-profile")]
    elif mutation == "time_budget":
        extension["run_state_domains"][0]["remaining_time_ms"] -= 1000
    elif mutation == "registration":
        extension["registration_sha256"] = "0" * 64
    else:
        extension["run_state_domains"][0]["current_agent_ids"] = ["foreign_robot"]
    with pytest.raises(ValueError):
        reassessment.validate_restaurant_reassessment_extension(
            extension, expected_extension_sha256=canonical_sha256(extension),
            certificate=ctx["certificate"], certificate_sha256=ctx["certificate_sha256"])


def test_solver_failure_and_search_budget_are_platform_failures(monkeypatch):
    ctx = _context()
    extension = _extension(ctx)
    monkeypatch.setattr(reassessment, "query_remaining_feasibility", lambda *a, **k: {
        "oracle_status": "failed", "feasibility_label": None,
        "platform_valid": False, "quality_failure": "oracle_search_budget_exhausted"})
    evaluator = _oracle(ctx, [extension])
    result = evaluator(extension["episode_id"], _runtime_report(ctx, extension))
    assert result["oracle_status"] == "failed"
    assert result["feasibility_label"] is None
    rows = evaluator.reassessment_report()["results"]
    assert rows[0]["quality_failure"] == (
        "reassessment_solver_failed:oracle_search_budget_exhausted")


def test_reassessment_report_schema_hides_construction_and_stays_unreleased():
    ctx = _context()
    extension = _extension(ctx)
    evaluator = _oracle(ctx, [extension])
    verified = evaluator(extension["episode_id"], _runtime_report(ctx, extension))
    assert verified["platform_valid"] is True
    report = evaluator.reassessment_report()
    assert report["schema"] == reassessment.REASSESSMENT_REPORT_SCHEMA
    assert report["version"] == 1
    assert report["visibility"] == "evaluator_only"
    assert report["configuration_status"] == "validated"
    binding = report["extension_bindings"][0]
    assert binding["extension_sha256"] == canonical_sha256(extension)
    assert binding["episode_id"] == ctx["episode_id"]
    assert binding["assessment_epochs"] == [1]
    for flag in ("execution_validated", "business_outcomes_validated",
                 "feasibility_oracle_verified", "release_ready", "scoreable"):
        assert report[flag] is False
    serialized = json.dumps(report)
    for hidden in ("action_domains", "root_domains", "root_state_w",
                   initial.KITCHEN_ROUTE, initial.DINING_ROUTE,
                   "qualified_parallel_service_inspections"):
        assert hidden not in serialized
