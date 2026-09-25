"""Runtime Restaurant reassessment adapter for post-change assessment epochs.

The adapter composes one externally pinned initial feasibility certificate
(the same artifact consumed by ``RestaurantInitialPositiveOracle``) with
explicitly issued run-state extensions.  The initial certificate's action and
coexistence domains calibrate only the pristine epoch-zero state, so every
later assessment epoch triggered by ``member_exit``/``capability_change``/
milestone change is out of that domain unless an extension pinned by its own
independent digest declares the post-change roster, fact partition, and
remaining budget.  Missing extensions, run-state domain drift, solver
failures, and unattested negative solver answers become platform failures
with a null label, mirroring the initial oracle's fail-closed discipline; a
negative label is emitted only when the extension also carries a verified
alternative-path completeness certificate.  Judgements are frozen per
(episode, assessment epoch) and later outcomes never rewrite them.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import math
from typing import Any

from .goal_alternative_path_certificate import (
    gate_not_deployable_result,
    validate_alternative_path_certificate,
)
from .goal_remaining_feasibility_oracle import (
    freeze_oracle_registration,
    query_remaining_feasibility,
)
from .goal_restaurant_initial_oracle import (
    HARD_BUDGET_MS,
    RestaurantInitialPositiveOracle,
    _build_registration,
    _validate_authorities,
    validate_restaurant_initial_positive_certificate,
)
from .provenance import canonical_sha256


EXTENSION_SCHEMA = "eai.restaurant.reassessment_extension"
EXTENSION_VERSION = 1
REASSESSMENT_REPORT_SCHEMA = "eai.benchmark.restaurant_reassessment_report"
EVALUATOR_REF = "restaurant_runtime_reassessment_v1"
MEASUREMENT_ID = "restaurant_runtime_reassessment"
REASSESSMENT_TRIGGERS = ("member_exit", "capability_change", "milestone_state_change")
_ROSTER_TRIGGERS = ("member_exit", "capability_change")

_RETURN_FIELDS = (
    "assessment_id", "assessment_epoch", "feasibility_label", "oracle_status",
    "platform_valid", "evidence_refs", "evaluator_ref",
)
_EXTENSION_FIELDS = frozenset({
    "schema", "version", "visibility", "bound_certificate_sha256", "episode_id",
    "qualification", "run_state_domains", "registration_sha256",
})
_RUN_STATE_FIELDS = frozenset({
    "assessment_epoch", "trigger_event_type", "measurement_id", "measurement_point",
    "time_s", "remaining_time_ms", "max_active_agents", "roster_version",
    "capability_versions", "current_agent_ids", "resource_versions",
    "deployment_change_versions",
})


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return deepcopy(dict(value))


def _exact_fields(value: Mapping[str, Any], fields: set[str], label: str) -> None:
    if set(value) != fields:
        raise ValueError(f"{label} fields mismatch")


def _digest(value: Any, label: str) -> str:
    if (not isinstance(value, str) or len(value) != 64
            or any(char not in "0123456789abcdef" for char in value)):
        raise ValueError(f"{label} must be a lowercase SHA-256")
    return value


def _finite(value: Any, label: str) -> float:
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(float(value))):
        raise ValueError(f"{label} must be finite")
    return float(value)


def _string_list(value: Any, label: str) -> list[str]:
    if (not isinstance(value, list)
            or any(not isinstance(item, str) or not item for item in value)
            or len(value) != len(set(value))):
        raise ValueError(f"{label} must contain distinct nonempty strings")
    return list(value)


def _version_map(value: Any, label: str) -> dict[str, int]:
    versions = _mapping(value, label)
    if any(not isinstance(key, str) or not key or type(version) is not int or version < 0
           for key, version in versions.items()):
        raise ValueError(f"{label} is invalid")
    return versions


def _freeze_extension_registration(
    extension: Mapping[str, Any], certificate: Mapping[str, Any],
) -> dict[str, Any]:
    """Freeze the bounded reassessment registration declared by one extension.

    The transition model is the certificate's own bounded construction model;
    only the measurement points and validation scope come from the extension,
    so an extension can narrow coverage but never invent new physics.
    """
    base = _build_registration(certificate)
    scope = extension["qualification"]["scope"]
    validation_scope = (
        "native_remaining_task" if scope == "native_empirical_domain"
        else "synthetic_fixture_only"
    )
    return freeze_oracle_registration(
        base["model"],
        task_sha256=certificate["identities"]["public_task_sha256"],
        measurement_points=[
            deepcopy(domain["measurement_point"])
            for domain in extension["run_state_domains"]
        ],
        validation={
            "status": "verified",
            "scope": validation_scope,
            "evidence_refs": list(extension["qualification"]["evidence_refs"]),
        },
    )


def validate_restaurant_reassessment_extension(
    extension: Any, *, expected_extension_sha256: str,
    certificate: Mapping[str, Any], certificate_sha256: str,
) -> dict[str, Any]:
    """Validate one run-state domain extension against its independent pin.

    ``certificate`` must already be the validated initial certificate and
    ``certificate_sha256`` its canonical digest, so an extension is always a
    domain extension of that exact certificate rather than a new authority.
    """
    expected = _digest(expected_extension_sha256, "expected extension digest")
    row = _mapping(extension, "extension")
    if (not _EXTENSION_FIELDS <= set(row)
            or not set(row) <= _EXTENSION_FIELDS | {"alternative_path_certificate"}):
        raise ValueError("extension fields mismatch")
    if (row["schema"] != EXTENSION_SCHEMA
            or type(row["version"]) is not int or row["version"] != EXTENSION_VERSION
            or row["visibility"] != "evaluator_only"):
        raise ValueError("extension schema, version, or visibility is invalid")
    if canonical_sha256(row) != expected:
        raise ValueError("extension differs from the separately pinned digest")
    if row["bound_certificate_sha256"] != certificate_sha256:
        raise ValueError("extension is not bound to the supplied initial certificate")
    if not isinstance(row["episode_id"], str) or not row["episode_id"]:
        raise ValueError("extension episode_id must be a nonempty string")

    qualification = _mapping(row["qualification"], "extension qualification")
    _exact_fields(qualification, {
        "scope", "evidence_refs", "extension_manifest_sha256",
        "allowed_runtime_profile_sha256", "extension_trials",
    }, "extension qualification")
    if qualification["scope"] not in {"synthetic_fixture", "native_empirical_domain"}:
        raise ValueError("extension qualification scope is unsupported")
    prefix = "fixture:" if qualification["scope"] == "synthetic_fixture" else "native:"
    refs = _string_list(qualification["evidence_refs"], "extension evidence_refs")
    if not refs or any(not ref.startswith(prefix) for ref in refs):
        raise ValueError("extension evidence refs do not match their scope")
    _digest(qualification["extension_manifest_sha256"], "extension manifest digest")
    profiles = _string_list(
        qualification["allowed_runtime_profile_sha256"],
        "extension allowed runtime profiles")
    if not profiles:
        raise ValueError("extension requires an allowed runtime profile")
    for profile in profiles:
        _digest(profile, "extension allowed runtime profile digest")
    if not set(profiles) <= set(
            certificate["qualification"]["allowed_runtime_profile_sha256"]):
        raise ValueError("extension broadens the certificate runtime profile domain")
    trials = qualification["extension_trials"]
    if not isinstance(trials, list):
        raise ValueError("extension_trials must be a list")
    if qualification["scope"] == "synthetic_fixture":
        if trials:
            raise ValueError("synthetic fixture cannot claim native extension trials")
    else:
        slots: set[tuple[str, int]] = set()
        archives: set[str] = set()
        covered: set[str] = set()
        for trial in trials:
            trial = _mapping(trial, "extension trial")
            _exact_fields(trial, {
                "host", "seed", "raw_archive_sha256", "runtime_profile_sha256",
            }, "extension trial")
            if trial["host"] not in {"local", "ljy"} or type(trial["seed"]) is not int:
                raise ValueError("invalid extension trial host or seed")
            archive = _digest(trial["raw_archive_sha256"], "extension archive digest")
            runtime_profile = _digest(
                trial["runtime_profile_sha256"], "extension runtime profile digest")
            if runtime_profile not in profiles:
                raise ValueError("extension trial uses a runtime profile outside the allowlist")
            slot = (trial["host"], trial["seed"])
            if slot in slots or archive in archives:
                raise ValueError("extension trials must use unique slots and raw archives")
            slots.add(slot)
            archives.add(archive)
            covered.add(runtime_profile)
        if not trials or covered != set(profiles):
            raise ValueError("native extension requires a trial for every runtime profile")

    domains = row["run_state_domains"]
    if not isinstance(domains, list) or not domains:
        raise ValueError("extension must declare at least one run-state domain")
    full8 = set(certificate["initial_domain"]["capability_versions"])
    epochs: set[int] = set()
    for domain in domains:
        domain = _mapping(domain, "run_state_domain")
        _exact_fields(domain, set(_RUN_STATE_FIELDS), "run_state_domain")
        epoch = domain["assessment_epoch"]
        if type(epoch) is not int or epoch < 1 or epoch in epochs:
            raise ValueError("run-state domains must cover distinct post-initial epochs")
        epochs.add(epoch)
        if domain["trigger_event_type"] not in REASSESSMENT_TRIGGERS:
            raise ValueError("run-state domain trigger is not a reassessment trigger")
        point = _mapping(domain["measurement_point"], "run-state measurement_point")
        _exact_fields(point, {
            "id", "trigger", "required_true", "required_false", "varying_facts",
        }, "run-state measurement_point")
        measurement_id = domain["measurement_id"]
        if (not isinstance(measurement_id, str) or not measurement_id
                or point["id"] != measurement_id
                or point["trigger"] != domain["trigger_event_type"]):
            raise ValueError("measurement point identity differs from its run-state domain")
        time_s = _finite(domain["time_s"], "run-state time_s")
        remaining = domain["remaining_time_ms"]
        if type(remaining) is not int or not 0 <= remaining <= HARD_BUDGET_MS:
            raise ValueError("run-state remaining budget is invalid")
        if (time_s <= 0.0
                or not math.isclose(
                    time_s * 1000.0 + remaining, HARD_BUDGET_MS,
                    rel_tol=0.0, abs_tol=1e-6)):
            raise ValueError("run-state time and remaining budget disagree")
        if type(domain["max_active_agents"]) is not int or domain["max_active_agents"] < 1:
            raise ValueError("run-state max_active_agents is invalid")
        if (type(domain["roster_version"]) is not int or domain["roster_version"] < (
                1 if domain["trigger_event_type"] in _ROSTER_TRIGGERS else 0)):
            raise ValueError("run-state roster_version is invalid for its trigger")
        agents = _string_list(domain["current_agent_ids"], "run-state current_agent_ids")
        if not set(agents) <= full8:
            raise ValueError("run-state roster leaves the certificate full8 domain")
        versions = _mapping(domain["capability_versions"], "run-state capability_versions")
        if set(versions) != set(agents) or any(
                type(version) is not int or version < 0 for version in versions.values()):
            raise ValueError("run-state capability versions do not cover the roster")
        for field in ("resource_versions", "deployment_change_versions"):
            _version_map(domain[field], f"run-state {field}")

    registration = _freeze_extension_registration(row, certificate)
    if row["registration_sha256"] != registration["registration_sha256"]:
        raise ValueError("extension registration identity is invalid")
    alternative = row.get("alternative_path_certificate")
    if alternative is not None:
        validate_alternative_path_certificate(alternative, registration)
    return deepcopy(row)


def _failure(
    *, episode_id: str, assessment_id: str, assessment_epoch: int,
    quality_failure: str, certificate_sha256: str | None,
) -> dict[str, Any]:
    return {
        "assessment_id": assessment_id,
        "assessment_epoch": assessment_epoch,
        "feasibility_label": None,
        "oracle_status": "failed",
        "platform_valid": False,
        "evidence_refs": [],
        "evaluator_ref": EVALUATOR_REF,
        "episode_id": episode_id,
        "measurement_id": None,
        "visibility": "evaluator_only",
        "quality_failure": quality_failure,
        "certificate_sha256": certificate_sha256,
        "extension_sha256": None,
        "scoreable": False,
        "execution_validated": False,
        "business_outcomes_validated": False,
        "feasibility_oracle_verified": False,
        "release_ready": False,
    }


def _public_result(result: Mapping[str, Any]) -> dict[str, Any]:
    return {field: deepcopy(result[field]) for field in _RETURN_FIELDS}


class RestaurantReassessmentOracle:
    """Post-change assessment evaluator composed from the initial certificate.

    Epoch zero keeps delegating to a ``RestaurantInitialPositiveOracle`` built
    from the same inputs, so a no-change episode returns the initial label.
    Epochs after a reassessment trigger require an extension that was
    explicitly issued for that exact run-time state; the initial certificate's
    domain is never extrapolated onto a changed roster.
    """

    def __init__(
        self, certificate: Any, *, expected_certificate_sha256: str,
        trusted_authorities: Any,
        extensions: Any = (), expected_extension_sha256: Any = (),
    ) -> None:
        self._certificate: dict[str, Any] | None = None
        self._registration: dict[str, Any] | None = None
        self._authorities: dict[str, Any] | None = None
        self._certificate_sha256 = (
            expected_certificate_sha256 if isinstance(expected_certificate_sha256, str) else None)
        self._configuration_error: str | None = None
        self._results: list[dict[str, Any]] = []
        self._cache: dict[tuple[str, str, int], dict[str, Any]] = {}
        self._extensions: dict[tuple[str, int], dict[str, Any]] = {}
        self._extension_summaries: list[dict[str, Any]] = []
        try:
            row = validate_restaurant_initial_positive_certificate(
                certificate, expected_certificate_sha256=expected_certificate_sha256)
            digest = canonical_sha256(row)
            authorities = _validate_authorities(deepcopy(trusted_authorities))
            if (row["identities"]["task_condition_binding_sha256"]
                    != authorities["task_condition_binding_sha256"]
                    or row["identities"]["asset_manifest_sha256"]
                    != authorities["asset_manifest_sha256"]
                    or row["identities"]["controller_source_sha256"]
                    != authorities["controller_source_sha256"]
                    or row["identities"]["execution_dependency_manifest_sha256"]
                    != canonical_sha256(authorities["execution_dependency_manifest"])
                    or row["execution_dependency_paths"]
                    != list(authorities["execution_dependency_manifest"])):
                raise ValueError("execution_authority_mismatch")
            raw_extensions = list(extensions)
            pins = list(expected_extension_sha256)
            if len(raw_extensions) != len(pins):
                raise ValueError("every extension needs its own independently pinned digest")
            summaries: list[dict[str, Any]] = []
            epoch_records: dict[tuple[str, int], dict[str, Any]] = {}
            for raw, pin in zip(raw_extensions, pins):
                normalized = validate_restaurant_reassessment_extension(
                    raw, expected_extension_sha256=pin, certificate=row,
                    certificate_sha256=digest)
                registration = _freeze_extension_registration(normalized, row)
                summaries.append({
                    "extension_sha256": pin,
                    "episode_id": normalized["episode_id"],
                    "assessment_epochs": sorted(
                        domain["assessment_epoch"]
                        for domain in normalized["run_state_domains"]),
                    "registration_sha256": registration["registration_sha256"],
                })
                for domain in normalized["run_state_domains"]:
                    key = (normalized["episode_id"], domain["assessment_epoch"])
                    if key in epoch_records:
                        raise ValueError("two extensions cover the same reassessment epoch")
                    epoch_records[key] = {
                        "extension_sha256": pin,
                        "registration": registration,
                        "scope": normalized["qualification"]["scope"],
                        "allowed_runtime_profiles": list(
                            normalized["qualification"]["allowed_runtime_profile_sha256"]),
                        "alternative_path_certificate": normalized.get(
                            "alternative_path_certificate"),
                        "domain": deepcopy(domain),
                    }
            self._certificate = row
            self._registration = _build_registration(row)
            self._authorities = authorities
            self._certificate_sha256 = digest
            self._extensions = epoch_records
            self._extension_summaries = summaries
        except (KeyError, TypeError, ValueError) as exc:
            self._configuration_error = f"configuration_invalid:{type(exc).__name__}"
        self._initial = RestaurantInitialPositiveOracle(
            certificate, expected_certificate_sha256=expected_certificate_sha256,
            trusted_authorities=trusted_authorities)

    def _evaluate(self, episode_id: str, trusted_report: Mapping[str, Any]) -> dict[str, Any]:
        report = _mapping(trusted_report, "trusted_report")
        session = _mapping(report.get("session"), "session")
        gate = _mapping(session.get("deployment_assessment"), "deployment_assessment")
        assessment_id = gate.get("assessment_id")
        assessment_epoch = gate.get("assessment_epoch")
        if (not isinstance(assessment_id, str) or not assessment_id
                or type(assessment_epoch) is not int or assessment_epoch < 0):
            raise ValueError("trusted report lacks an assessment identity")

        def fail(reason: str) -> dict[str, Any]:
            return _failure(
                episode_id=episode_id, assessment_id=assessment_id,
                assessment_epoch=assessment_epoch, quality_failure=reason,
                certificate_sha256=self._certificate_sha256)

        if self._configuration_error is not None:
            return fail(self._configuration_error)
        if assessment_epoch == 0:
            delegated = self._initial(episode_id, trusted_report)
            result = deepcopy(dict(delegated))
            result.update({
                "evaluator_ref": EVALUATOR_REF,
                "episode_id": episode_id,
                "measurement_id": MEASUREMENT_ID,
                "visibility": "evaluator_only",
                "certificate_sha256": self._certificate_sha256,
                "extension_sha256": None,
                "qualification_scope": self._certificate["qualification"]["scope"],
                "delegated_evaluator_ref": delegated["evaluator_ref"],
                "execution_validated": False,
                "business_outcomes_validated": False,
                "feasibility_oracle_verified": False,
                "release_ready": False,
                "scoreable": False,
            })
            return result

        record = self._extensions.get((episode_id, assessment_epoch))
        if record is None:
            return fail("runtime_epoch_outside_qualified_domain")
        certificate = self._certificate
        authorities = self._authorities
        domain = record["domain"]
        assert certificate is not None and authorities is not None
        if session.get("episode_id") != episode_id:
            return fail("runtime_report_identity_mismatch")
        if record["scope"] != certificate["qualification"]["scope"]:
            return fail("extension_qualification_scope_mismatch")
        if authorities["evidence_scope"] != certificate["qualification"]["scope"]:
            return fail("qualification_scope_authority_mismatch")
        if authorities["runtime_profile_sha256"] not in certificate[
                "qualification"]["allowed_runtime_profile_sha256"]:
            return fail("runtime_profile_outside_qualified_domain")
        if authorities["runtime_profile_sha256"] not in record["allowed_runtime_profiles"]:
            return fail("runtime_profile_outside_qualified_domain")
        if (gate.get("status") != "required"
                or gate.get("external_evaluation") is not None
                or gate.get("terminated") is not False):
            return fail("reassessment_epoch_not_pending")
        if gate.get("public_task_sha256") != certificate["identities"]["public_task_sha256"]:
            return fail("task_identity_mismatch")
        if gate.get("trigger_event_type") != domain["trigger_event_type"]:
            return fail("trigger_event_type_mismatch")
        triggered_at = gate.get("triggered_at_s")
        if (isinstance(triggered_at, bool) or not isinstance(triggered_at, (int, float))
                or not math.isfinite(triggered_at) or triggered_at > domain["time_s"]):
            return fail("runtime_state_domain_mismatch")
        current_ids = gate.get("current_agent_ids")
        if (not isinstance(current_ids, list)
                or len(current_ids) != len(set(current_ids))
                or set(current_ids) != set(domain["current_agent_ids"])):
            return fail("runtime_roster_domain_mismatch")
        pool = gate.get("current_robot_pool")
        if not isinstance(pool, Mapping) or set(pool) != set(current_ids):
            return fail("runtime_roster_domain_mismatch")
        if (gate.get("roster_version") != domain["roster_version"]
                or gate.get("capability_versions") != domain["capability_versions"]
                or gate.get("resource_versions") != domain["resource_versions"]
                or gate.get("deployment_change_versions")
                != domain["deployment_change_versions"]):
            return fail("runtime_version_domain_mismatch")
        runtime_state = gate.get("runtime_state")
        if (not isinstance(runtime_state, Mapping)
                or session.get("current_time_s") != domain["time_s"]
                or runtime_state.get("time_s") != domain["time_s"]
                or not isinstance(runtime_state.get("robot_regions"), Mapping)
                or set(runtime_state["robot_regions"]) != set(current_ids)
                or runtime_state.get("object_states") != {}
                or gate.get("runtime_state_sha256") != canonical_sha256(runtime_state)):
            return fail("runtime_state_domain_mismatch")

        try:
            solver_robots = []
            for agent in current_ids:
                public = pool[agent]
                solver_robots.append({
                    "id": agent,
                    "capabilities": list(public["public_capabilities"]),
                    "payload_kg": public["physical_profile"]["effective_payload_kg"],
                })
            solver_state = {
                "time_s": domain["time_s"],
                "true_facts": list(domain["measurement_point"]["required_true"]),
                "resources": {"time_ms": domain["remaining_time_ms"]},
                "max_active_agents": domain["max_active_agents"],
                "robots": solver_robots,
                "roster_version": gate["roster_version"],
                "capability_versions": {
                    agent: gate["capability_versions"][agent] for agent in current_ids},
            }
            grounding = {
                "status": "verified",
                "state_sha256": canonical_sha256(solver_state),
                "evidence_refs": [
                    f"certificate:{self._certificate_sha256}",
                    f"extension:{record['extension_sha256']}",
                    f"runtime-state:{canonical_sha256(runtime_state)}",
                ],
            }
            solver_result = query_remaining_feasibility(
                record["registration"],
                measurement_id=domain["measurement_id"],
                episode_id=episode_id,
                assessment_id=assessment_id,
                assessment_epoch=assessment_epoch,
                task_sha256=certificate["identities"]["public_task_sha256"],
                state=solver_state,
                grounding=grounding,
            )
        except Exception as exc:
            return fail(f"reassessment_solver_failed:{type(exc).__name__}")
        if (solver_result.get("oracle_status") != "verified"
                or solver_result.get("platform_valid") is not True):
            return fail(
                f"reassessment_solver_failed:"
                f"{solver_result.get('quality_failure') or 'solver_not_verified'}")
        if solver_result.get("feasibility_label") == "not_deployable":
            gated = gate_not_deployable_result(
                solver_result, record["alternative_path_certificate"],
                record["registration"])
            if (gated.get("feasibility_label") != "not_deployable"
                    or gated.get("oracle_status") != "verified"):
                return fail(gated.get("quality_failure")
                            or "not_deployable_without_certificate")
            solver_result = gated
        elif solver_result.get("feasibility_label") != "deployable":
            return fail("reassessment_solver_failed:unexpected_label")

        result = deepcopy(dict(solver_result))
        if result.get("alternative_path_gate") == "verified":
            result["alternative_path_certificate_sha256"] = result["certificate_sha256"]
        result.update({
            "evaluator_ref": EVALUATOR_REF,
            "evidence_refs": [
                f"certificate:{self._certificate_sha256}",
                f"extension:{record['extension_sha256']}",
                f"runtime-state:{canonical_sha256(runtime_state)}",
            ],
            "quality_failure": None,
            "episode_id": episode_id,
            "measurement_id": domain["measurement_id"],
            "visibility": "evaluator_only",
            "certificate_sha256": self._certificate_sha256,
            "extension_sha256": record["extension_sha256"],
            "qualification_scope": record["scope"],
            "execution_validated": False,
            "business_outcomes_validated": False,
            "feasibility_oracle_verified": record["scope"] == "native_empirical_domain",
            "release_ready": False,
            "scoreable": False,
        })
        return result

    def __call__(self, episode_id: str, trusted_report: Mapping[str, Any]) -> dict[str, Any]:
        gate = trusted_report.get("session", {}).get("deployment_assessment", {})
        key = (episode_id, gate.get("assessment_id"), gate.get("assessment_epoch"))
        if key in self._cache:
            return _public_result(self._cache[key])
        try:
            result = self._evaluate(episode_id, trusted_report)
        except (KeyError, TypeError, ValueError) as exc:
            assessment_id = gate.get("assessment_id")
            assessment_epoch = gate.get("assessment_epoch")
            if (not isinstance(assessment_id, str) or not assessment_id
                    or type(assessment_epoch) is not int or assessment_epoch < 0):
                raise ValueError("trusted report lacks an assessment identity") from exc
            result = _failure(
                episode_id=episode_id, assessment_id=assessment_id,
                assessment_epoch=assessment_epoch,
                quality_failure=f"trusted_input_failed:{type(exc).__name__}",
                certificate_sha256=self._certificate_sha256)
        self._cache[key] = deepcopy(result)
        self._results.append(deepcopy(result))
        return _public_result(result)

    def reassessment_report(self) -> dict[str, Any]:
        """Return digests and outcomes only; never expose the construction."""
        return {
            "schema": REASSESSMENT_REPORT_SCHEMA,
            "version": 1,
            "visibility": "evaluator_only",
            "certificate_sha256": self._certificate_sha256,
            "registration_sha256": (
                self._registration.get("registration_sha256")
                if self._registration is not None else None),
            "extension_bindings": deepcopy(self._extension_summaries),
            "qualification_scope": (
                self._certificate["qualification"]["scope"]
                if self._certificate is not None else None),
            "configuration_status": (
                "validated" if self._configuration_error is None else "failed"),
            "results": deepcopy(self._results),
            "execution_validated": False,
            "business_outcomes_validated": False,
            "feasibility_oracle_verified": False,
            "release_ready": False,
            "scoreable": False,
        }


__all__ = [
    "EVALUATOR_REF",
    "EXTENSION_SCHEMA",
    "EXTENSION_VERSION",
    "MEASUREMENT_ID",
    "REASSESSMENT_REPORT_SCHEMA",
    "REASSESSMENT_TRIGGERS",
    "RestaurantReassessmentOracle",
    "validate_restaurant_reassessment_extension",
]
