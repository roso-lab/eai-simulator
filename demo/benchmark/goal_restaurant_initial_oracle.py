"""Positive-only initial Restaurant feasibility adapter.

The adapter accepts one externally pinned, evaluator-only construction
certificate.  Its validation is an internal consistency check, not a native
calibration process: a caller must supply the expected certificate digest and
the exact execution authorities separately.  Missing or mismatched evidence,
an out-of-domain initial state, and every non-positive solver result become a
platform failure with a null label.  This under-approximate adapter never emits
``not_deployable`` and never returns its construction or root domains.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
from typing import Any

from .goal_remaining_feasibility_oracle import (
    freeze_oracle_registration,
    query_remaining_feasibility,
)
from .provenance import canonical_sha256


CERTIFICATE_SCHEMA = "eai.restaurant.initial_positive_certificate"
CERTIFICATE_VERSION = 1
CERTIFICATE_VERSION_V2 = 2
EVALUATOR_REF = "restaurant_initial_positive_v1"
EVALUATOR_REF_V2 = "restaurant_initial_positive_v2"
MEASUREMENT_ID = "restaurant_initial_positive"
HARD_BUDGET_MS = 240_000
CONSTRUCTION_COMPLETION_MS = 82_000
NATIVE_ROOT_TOLERANCE_LIMIT = [0.01] * 3 + [0.005] * 4 + [0.01] * 6
TASK_ID = "RESTAURANT-GOAL-NATIVE-02"
DEPLOYMENT_PROFILE = "metric_regions_v2"
COEXISTENCE_SCHEMA = "eai.restaurant.initial_coexistence_domain"
COEXISTENCE_VERSION = 1
COEXISTENCE_WARMUP_TICKS = 240
COEXISTENCE_HOLD_BINDING = "pre_warmup_measured_root_xyz_and_yaw"

CARTER_1 = "restaurant_carter_1"
CARTER_2 = "restaurant_carter_2"
KITCHEN_ROUTE = "restaurant_spawn_carter_1_to_kitchen_service"
DINING_ROUTE = "restaurant_spawn_carter_2_to_dining_service"

_FACTS = ("kitchen_ready", "dining_ready", "readiness_receipt_committed")
_RETURN_FIELDS = (
    "assessment_id", "assessment_epoch", "feasibility_label", "oracle_status",
    "platform_valid", "evidence_refs", "evaluator_ref",
)
_CONTROLLER_PATHS = (
    "controller/rl/go2_rsl_rl/go2_rsl_rl.py",
    "controller/rl/lite3_rsl_rl/lite3_rsl_rl.py",
    "controller/rl/m20_nav_rsl/m20_nav_rsl.py",
    "controller/traditional/carter_diff/carter_diff.py",
    "controller/traditional/pegasus_multirotor/pegasus_multirotor.py",
)
_REQUIRED_DEPENDENCIES = frozenset({
    "demo/benchmark/communication.py",
    "demo/benchmark/goal_restaurant_conditions.py",
    "demo/benchmark/goal_remaining_feasibility_oracle.py",
    "demo/benchmark/goal_restaurant_autonomous_v2.py",
    "demo/benchmark/goal_restaurant_deployment_v2.py",
    "demo/benchmark/goal_restaurant_execution_v2.py",
    "demo/benchmark/goal_restaurant_fleet.py",
    "demo/benchmark/goal_restaurant_initial_oracle.py",
    "demo/benchmark/goal_restaurant_native_observation.py",
    "demo/benchmark/goal_restaurant_native_state.py",
    "demo/benchmark/goal_restaurant_public_policy.py",
    "demo/benchmark/goal_restaurant_public_run.py",
    "demo/benchmark/goal_restaurant_publication_treatment.py",
    "demo/benchmark/goal_session.py",
    "demo/benchmark/goal_session_audit.py",
    "source/EAI_hmrs/EAI_hmrs/env_builder.py",
    *_CONTROLLER_PATHS,
})
_EXCLUDED_DEPENDENCIES = frozenset({
    "source/EAI/test/test_benchmark_goal_restaurant_initial_oracle.py",
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


def _finite(value: Any, label: str, *, nonnegative: bool = False) -> float:
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or nonnegative and value < 0):
        raise ValueError(f"{label} must be finite")
    return float(value)


def _string_list(value: Any, label: str) -> list[str]:
    if (not isinstance(value, list)
            or any(not isinstance(item, str) or not item for item in value)
            or len(value) != len(set(value))):
        raise ValueError(f"{label} must contain distinct nonempty strings")
    return list(value)


def _validate_dependency_manifest(value: Any) -> dict[str, str]:
    manifest = _mapping(value, "execution_dependency_manifest")
    if not manifest:
        raise ValueError("execution dependency manifest must not be empty")
    normalized: dict[str, str] = {}
    for path, digest in manifest.items():
        if not isinstance(path, str) or not path:
            raise ValueError("execution dependency paths must be nonempty strings")
        pure = PurePosixPath(path)
        if (pure.is_absolute() or str(pure) != path or ".." in pure.parts
                or any(char in path for char in "*?[]{}")
                or pure.suffix != ".py"
                or pure.parts[0] in {"tmp", "outputs", "artifacts"}
                or path in _EXCLUDED_DEPENDENCIES):
            raise ValueError("execution dependency manifest contains a mutable or nonliteral path")
        normalized[path] = _digest(digest, f"dependency digest for {path}")
    if not _REQUIRED_DEPENDENCIES <= set(normalized):
        raise ValueError("execution dependency manifest is missing a required stable source")
    return dict(sorted(normalized.items()))


def _validate_root_domain(value: Any, label: str) -> dict[str, list[float]]:
    domain = _mapping(value, label)
    _exact_fields(domain, {"center", "abs_tolerance"}, label)
    center, tolerance = domain["center"], domain["abs_tolerance"]
    if not isinstance(center, list) or not isinstance(tolerance, list):
        raise ValueError(f"{label} vectors must be lists")
    if len(center) != 13 or len(tolerance) != 13:
        raise ValueError(f"{label} must cover the complete 13D root")
    return {
        "center": [_finite(item, f"{label} center") for item in center],
        "abs_tolerance": [
            _finite(item, f"{label} tolerance", nonnegative=True)
            for item in tolerance
        ],
    }


def build_restaurant_initial_coexistence_domain() -> dict[str, Any]:
    """Build the maintained full8 initial coexistence contract for v2 certificates."""
    from .goal_restaurant_deployment_v2 import build_restaurant_deployment_v2

    contract = build_restaurant_deployment_v2()
    deployment = contract["deployment"]
    robots = {row["id"]: row for row in contract["public_task"]["candidate_robot_pool"]}
    spawn_regions = deployment["spawn_regions"]
    if set(spawn_regions) != set(robots) or len(robots) != 8:
        raise RuntimeError("maintained Restaurant coexistence roster is not full8")
    bindings: dict[str, dict[str, Any]] = {}
    for agent, region_id in spawn_regions.items():
        geometry = deployment["region_bindings"].get(region_id)
        if not isinstance(geometry, Mapping) or geometry.get("geometry_status") != "defined":
            raise RuntimeError("maintained Restaurant spawn geometry is unavailable")
        center = geometry.get("center_xyz_m", geometry.get("center_xy_m"))
        expected_dimensions = 3 if robots[agent]["mobility"] == "aerial" else 2
        if not isinstance(center, list) or len(center) != expected_dimensions:
            raise RuntimeError("maintained Restaurant spawn geometry has the wrong dimension")
        bindings[agent] = {
            "region_id": region_id,
            "center_m": [_finite(value, f"spawn center for {agent}") for value in center],
            "tolerance_m": _finite(
                geometry.get("tolerance_m"), f"spawn tolerance for {agent}",
                nonnegative=True),
        }
    return {
        "schema": COEXISTENCE_SCHEMA,
        "version": COEXISTENCE_VERSION,
        "warmup_ticks": COEXISTENCE_WARMUP_TICKS,
        "hold_binding": COEXISTENCE_HOLD_BINDING,
        "spawn_region_bindings": bindings,
    }


def _root_yaw(root: list[float], label: str) -> float:
    if len(root) != 13:
        raise ValueError(f"{label} must cover the complete 13D root")
    w, x, y, z = root[3:7]
    norm = math.sqrt(w * w + x * x + y * y + z * z)
    if not math.isclose(norm, 1.0, rel_tol=0.0, abs_tol=1e-3):
        raise ValueError(f"{label} quaternion must be normalized")
    w, x, y, z = (value / norm for value in (w, x, y, z))
    return math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))


def _epoch_agent_root(
    value: Any, *, agent: str, native_agent: str,
    physics_step: int, time_s: float,
) -> list[float]:
    sample = _mapping(value, f"epoch agent {agent}")
    vector = sample.get("root_state_w")
    if (sample.get("public_agent_id") != agent
            or sample.get("native_agent_id") != native_agent
            or sample.get("physics_step") != physics_step
            or sample.get("time_s") != time_s
            or not isinstance(vector, list) or len(vector) != 13):
        raise ValueError(f"epoch agent {agent} identity or clock mismatch")
    root = [_finite(item, f"epoch root for {agent}") for item in vector]
    _root_yaw(root, f"epoch root for {agent}")
    return root


def validate_restaurant_initial_coexistence(
    state: Any, coexistence_domain: Any,
) -> dict[str, Any]:
    """Validate v2 full8 coexistence and its byte-pinned warmup archive.

    This helper checks evidence structure only. It does not issue a certificate
    or return an oracle feasibility label.
    """
    expected = build_restaurant_initial_coexistence_domain()
    domain = _mapping(coexistence_domain, "coexistence_domain")
    if domain != expected:
        raise ValueError("coexistence domain differs from the maintained deployment")
    row = _mapping(state, "native_initial_state")
    from .goal_restaurant_fleet import FLEET_CONTROLLER_REGISTRY

    bindings = domain["spawn_region_bindings"]
    expected_agents = set(bindings)
    if set(FLEET_CONTROLLER_REGISTRY) != expected_agents:
        raise ValueError("coexistence domain differs from the maintained full8 controller roster")
    roots = _mapping(row.get("native_initial_roots"), "native initial roots")
    memberships = _mapping(row.get("initial_regions"), "initial regions")
    robot_regions = _mapping(row.get("robot_regions"), "robot regions")
    if (set(roots) != expected_agents or set(memberships) != expected_agents
            or set(robot_regions) != expected_agents):
        raise ValueError("coexistence state does not cover exactly the full8 roster")
    origin_step = row.get("origin_physics_step")
    if type(origin_step) is not int or origin_step < 0 or row.get("time_s") != 0.0:
        raise ValueError("coexistence state has an invalid post-warmup epoch")
    normalized_roots: dict[str, list[float]] = {}
    for agent, binding in bindings.items():
        root = _epoch_agent_root(
            roots[agent], agent=agent,
            native_agent=FLEET_CONTROLLER_REGISTRY[agent]["native_agent_id"],
            physics_step=origin_step, time_s=0.0)
        center = binding["center_m"]
        error = math.dist(root[:len(center)], center)
        membership = _mapping(memberships[agent], f"initial membership for {agent}")
        recorded_error = _finite(
            membership.get("region_error_m"), f"initial region error for {agent}",
            nonnegative=True)
        if (error > binding["tolerance_m"]
                or membership.get("status") != "measured_region_membership"
                or membership.get("region_id") != binding["region_id"]
                or membership.get("region_center_m") != center
                or membership.get("region_tolerance_m") != binding["tolerance_m"]
                or not math.isclose(recorded_error, error, rel_tol=0.0, abs_tol=1e-12)
                or robot_regions[agent] != binding["region_id"]):
            raise ValueError("initial region membership differs from measured maintained geometry")
        normalized_roots[agent] = root

    source = _mapping(row.get("source_epoch_archive"), "source_epoch_archive")
    _exact_fields(source, {"path", "sha256"}, "source_epoch_archive")
    archive_path = source.get("path")
    if not isinstance(archive_path, str) or not archive_path:
        raise ValueError("source epoch archive path must be nonempty")
    expected_sha = _digest(source.get("sha256"), "source epoch archive digest")
    try:
        archive_bytes = Path(archive_path).read_bytes()
    except OSError as exc:
        raise ValueError("source epoch archive is unavailable") from exc
    if hashlib.sha256(archive_bytes).hexdigest() != expected_sha:
        raise ValueError("source epoch archive bytes differ from the pinned digest")
    try:
        archive = json.loads(archive_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("source epoch archive is malformed") from exc
    archive = _mapping(archive, "source epoch archive")
    if (archive.get("schema") != "eai.restaurant.native_episode_warmup_archive"
            or archive.get("version") != 1
            or archive.get("episode_id") != row.get("episode_id")
            or archive.get("task_id") != row.get("task_id")
            or archive.get("public_task_sha256") != row.get("public_task_sha256")
            or archive.get("deployment_profile") != row.get("deployment_profile")
            or archive.get("deployment_sha256") != row.get("deployment_sha256")
            or archive.get("controller_instances_preserved") is not True
            or archive.get("post_warmup_sample_measured") is not True
            or any(archive.get(flag) is not False for flag in (
                "scoreable", "execution_validated", "business_outcomes_validated",
                "feasibility_oracle_verified", "release_ready",
            ))):
        raise ValueError("source epoch archive identity or provenance mismatch")
    warmup = archive.get("warmup_ticks")
    if (not isinstance(warmup, list)
            or len(warmup) != domain["warmup_ticks"]
            or [tick.get("warmup_index") if isinstance(tick, Mapping) else None
                for tick in warmup] != list(range(domain["warmup_ticks"]))
            or any(not isinstance(tick, Mapping) for tick in warmup)):
        raise ValueError("source epoch archive does not contain exactly 240 full8 warmup ticks")
    previous_step: int | None = None
    for index, tick in enumerate(warmup):
        tick_step = tick.get("physics_step")
        tick_time = tick.get("time_s")
        tick_agents = _mapping(tick.get("agents"), "warmup tick agents")
        if (tick.get("schema") != "eai.restaurant.native_fleet_tick"
                or tick.get("version") != 1
                or tick.get("warmup_index") != index
                or type(tick_step) is not int
                or set(tick_agents) != expected_agents
                or (previous_step is not None and tick.get("start_physics_step") != previous_step)
                or previous_step is not None and tick_step <= previous_step):
            raise ValueError("source epoch archive warmup tick identity or clock mismatch")
        tick_time = _finite(tick_time, "warmup tick time", nonnegative=True)
        for agent in bindings:
            _epoch_agent_root(
                tick_agents[agent], agent=agent,
                native_agent=FLEET_CONTROLLER_REGISTRY[agent]["native_agent_id"],
                physics_step=tick_step, time_s=tick_time)
        previous_step = tick_step
    post = _mapping(archive.get("post_warmup_sample"), "post_warmup_sample")
    post_agents = _mapping(post.get("agents"), "post_warmup agents")
    if (post.get("physics_step") != origin_step or post.get("time_s") != 0.0
            or set(post_agents) != expected_agents
            or warmup[-1].get("physics_step") != origin_step):
        raise ValueError("source epoch archive post-warmup sample mismatch")
    for agent in bindings:
        archived_root = _epoch_agent_root(
            post_agents[agent], agent=agent,
            native_agent=FLEET_CONTROLLER_REGISTRY[agent]["native_agent_id"],
            physics_step=origin_step, time_s=0.0)
        if archived_root != normalized_roots[agent]:
            raise ValueError("source epoch archive root differs from native initial state")

    old_epoch = _mapping(archive.get("old_epoch"), "old_epoch")
    pre_step = old_epoch.get("pre_warmup_physics_step")
    pre_agents = _mapping(old_epoch.get("pre_warmup_agents"), "pre-warmup agents")
    hold_targets = _mapping(
        archive.get("hold_targets_before_warmup"), "pre-warmup hold targets")
    hold_yaws = _mapping(archive.get("hold_yaws_before_warmup"), "pre-warmup hold yaws")
    if (type(pre_step) is not int or pre_step < 0 or set(pre_agents) != expected_agents
            or set(hold_targets) != expected_agents or set(hold_yaws) != expected_agents):
        raise ValueError("source epoch archive pre-warmup roster mismatch")
    pre_time = old_epoch.get("pre_warmup_time_s")
    pre_time = _finite(pre_time, "pre-warmup time")
    if old_epoch.get("origin_physics_step") != pre_step:
        raise ValueError("source epoch archive old origin differs from pre-warmup step")
    previous_step = pre_step
    for tick in warmup:
        if tick.get("start_physics_step") != previous_step:
            raise ValueError("source epoch archive warmup steps are not contiguous")
        previous_step = tick["physics_step"]
    for agent in bindings:
        pre_root = _epoch_agent_root(
            pre_agents[agent], agent=agent,
            native_agent=FLEET_CONTROLLER_REGISTRY[agent]["native_agent_id"],
            physics_step=pre_step, time_s=pre_time)
        target = hold_targets[agent]
        yaw = _finite(hold_yaws[agent], f"pre-warmup hold yaw for {agent}")
        if not isinstance(target, list) or len(target) != 3:
            raise ValueError("hold provenance has an invalid target")
        target = [_finite(value, f"pre-warmup hold target for {agent}")
                  for value in target]
        if (target != pre_root[:3]
                or not math.isclose(yaw, _root_yaw(pre_root, f"pre-warmup root for {agent}"),
                                    rel_tol=0.0, abs_tol=1e-12)):
            raise ValueError("hold provenance differs from the measured pre-warmup root")
    return {
        "schema": "eai.restaurant.initial_coexistence_validation",
        "version": 1,
        "source_epoch_archive_sha256": expected_sha,
        "warmup_ticks": domain["warmup_ticks"],
        "origin_physics_step": origin_step,
        "agent_ids": sorted(expected_agents),
        "robot_regions_sha256": canonical_sha256(robot_regions),
    }


def _expected_actions() -> dict[str, Any]:
    return {
        "window_semantics": "start_inclusive_end_exclusive",
        "kitchen_inspection": {
            "agent_id": CARTER_1,
            "route_id": KITCHEN_ROUTE,
            "from_region_id": "restaurant_spawn_carter_1",
            "to_region_id": "kitchen_service",
            "navigation_window_ms": [0, 60_000],
            "observation_window_ms": [61_000, 71_000],
            "observation_mode": "service_ground_readiness",
        },
        "dining_inspection": {
            "agent_id": CARTER_2,
            "route_id": DINING_ROUTE,
            "from_region_id": "restaurant_spawn_carter_2",
            "to_region_id": "dining_service",
            "navigation_window_ms": [0, 60_000],
            "observation_window_ms": [61_000, 71_000],
            "observation_mode": "service_ground_readiness",
        },
        "readiness_receipt": {
            "agent_id": CARTER_1,
            "region_id": "kitchen_service",
            "relay_window_ms": [72_000, 82_000],
            "message_kind": "restaurant_readiness_receipt",
        },
        "construction_completion_limit_ms": CONSTRUCTION_COMPLETION_MS,
        "hard_budget_ms": HARD_BUDGET_MS,
    }


def _build_registration(certificate: Mapping[str, Any]) -> dict[str, Any]:
    scope = certificate["qualification"]["scope"]
    validation_scope = (
        "native_remaining_task" if scope == "native_empirical_domain"
        else "synthetic_fixture_only"
    )
    model = {
        "fact_ids": list(_FACTS),
        "terminal_all": list(_FACTS),
        "resource_bounds": {"time_ms": HARD_BUDGET_MS},
        "transitions": [
            {
                "id": "qualified_parallel_service_inspections",
                "requires_all": [], "requires_absent": [],
                "adds": ["kitchen_ready", "dining_ready"], "deletes": [],
                "capability_slots": [
                    {"requires_all": ["ground_navigation", "observe"],
                     "min_payload_kg": 0},
                    {"requires_all": ["ground_navigation", "observe"],
                     "min_payload_kg": 0},
                ],
                # Includes navigation, the [60,61) scheduling gap, observation,
                # and the [71,72) gap before the relay may begin.
                "resource_costs": {"time_ms": 72_000},
            },
            {
                "id": "qualified_readiness_receipt",
                "requires_all": ["kitchen_ready", "dining_ready"],
                "requires_absent": [],
                "adds": ["readiness_receipt_committed"], "deletes": [],
                "capability_slots": [
                    {"requires_all": ["relay"], "min_payload_kg": 0},
                ],
                "resource_costs": {"time_ms": 10_000},
            },
        ],
    }
    return freeze_oracle_registration(
        model,
        task_sha256=certificate["identities"]["public_task_sha256"],
        measurement_points=[{
            "id": MEASUREMENT_ID,
            "trigger": "initial_deployment",
            "required_true": [],
            "required_false": list(_FACTS),
            "varying_facts": [],
        }],
        validation={
            "status": "verified",
            "scope": validation_scope,
            "evidence_refs": list(certificate["qualification"]["evidence_refs"]),
        },
    )


def _validate_certificate(
    certificate: Any, *, expected_certificate_sha256: str,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    expected = _digest(expected_certificate_sha256, "expected certificate digest")
    row = _mapping(certificate, "certificate")
    version = row.get("version")
    if type(version) is not int or version not in {CERTIFICATE_VERSION, CERTIFICATE_VERSION_V2}:
        raise ValueError("certificate schema, version, or visibility is invalid")
    certificate_fields = {
        "schema", "version", "visibility", "qualification", "identities",
        "execution_dependency_paths", "initial_domain", "action_domains",
        "registration_sha256",
    }
    if version == CERTIFICATE_VERSION_V2:
        certificate_fields.add("coexistence_domain")
    _exact_fields(row, certificate_fields, "certificate")
    if (row["schema"] != CERTIFICATE_SCHEMA
            or row["visibility"] != "evaluator_only"):
        raise ValueError("certificate schema, version, or visibility is invalid")
    actual = canonical_sha256(row)
    if actual != expected:
        raise ValueError("certificate differs from the separately pinned digest")

    qualification = _mapping(row["qualification"], "qualification")
    _exact_fields(qualification, {
        "scope", "evidence_refs", "calibration_manifest_sha256",
        "allowed_runtime_profile_sha256", "calibration_trials",
    }, "qualification")
    if qualification["scope"] not in {"synthetic_fixture", "native_empirical_domain"}:
        raise ValueError("certificate qualification scope is unsupported")
    prefix = "fixture:" if qualification["scope"] == "synthetic_fixture" else "native:"
    refs = _string_list(qualification["evidence_refs"], "qualification evidence_refs")
    if not refs or any(not ref.startswith(prefix) for ref in refs):
        raise ValueError("qualification evidence refs do not match their scope")
    _digest(qualification["calibration_manifest_sha256"], "calibration manifest digest")
    profiles = _string_list(
        qualification["allowed_runtime_profile_sha256"],
        "allowed runtime profile digests")
    if not profiles:
        raise ValueError("qualification requires an allowed runtime profile")
    for profile in profiles:
        _digest(profile, "allowed runtime profile digest")
    trials = qualification["calibration_trials"]
    if not isinstance(trials, list):
        raise ValueError("calibration_trials must be a list")
    if qualification["scope"] == "synthetic_fixture":
        if trials:
            raise ValueError("synthetic fixture cannot claim native calibration trials")
    elif version == CERTIFICATE_VERSION:
        slots = set()
        for trial in trials:
            trial = _mapping(trial, "calibration trial")
            _exact_fields(trial, {"host", "seed", "raw_archive_sha256"}, "calibration trial")
            if trial["host"] not in {"local", "ljy"} or type(trial["seed"]) is not int:
                raise ValueError("invalid calibration host or seed")
            _digest(trial["raw_archive_sha256"], "calibration archive digest")
            slots.add((trial["host"], trial["seed"]))
        if (len(trials) != 6 or len({trial["raw_archive_sha256"] for trial in trials}) != 6
                or slots != {(host, seed) for host in ("local", "ljy")
                             for seed in (17, 41, 73)}):
            raise ValueError("native certificate requires all six preregistered calibration slots")
        # Shape checks cannot authenticate archives or their outcomes. That is
        # the independent issuer's job, bound by the externally pinned digest.
    else:
        slots: set[tuple[str, int]] = set()
        archive_digests: set[str] = set()
        covered_profiles: set[str] = set()
        for trial in trials:
            trial = _mapping(trial, "calibration trial")
            _exact_fields(trial, {
                "host", "seed", "raw_archive_sha256", "runtime_profile_sha256",
            }, "calibration trial")
            if trial["host"] not in {"local", "ljy"} or type(trial["seed"]) is not int:
                raise ValueError("invalid calibration host or seed")
            archive_digest = _digest(
                trial["raw_archive_sha256"], "calibration archive digest")
            runtime_profile = _digest(
                trial["runtime_profile_sha256"], "calibration runtime profile digest")
            if runtime_profile not in profiles:
                raise ValueError("calibration trial uses a runtime profile outside the allowlist")
            slot = (trial["host"], trial["seed"])
            if slot in slots or archive_digest in archive_digests:
                raise ValueError("v2 calibration trials must use unique slots and raw archives")
            slots.add(slot)
            archive_digests.add(archive_digest)
            covered_profiles.add(runtime_profile)
        if not trials or covered_profiles != set(profiles):
            raise ValueError("v2 native certificate requires a trial for every runtime profile")

    identities = _mapping(row["identities"], "identities")
    _exact_fields(identities, {
        "public_task_sha256", "deployment_sha256",
        "task_condition_binding_sha256", "execution_dependency_manifest_sha256",
        "asset_manifest_sha256", "controller_source_sha256",
    }, "identities")
    for field in (
        "public_task_sha256", "deployment_sha256", "task_condition_binding_sha256",
        "execution_dependency_manifest_sha256", "asset_manifest_sha256",
    ):
        _digest(identities[field], field)
    controllers = _mapping(identities["controller_source_sha256"], "controller identities")
    if set(controllers) != set(_CONTROLLER_PATHS):
        raise ValueError("certificate must bind exactly the five controller sources")
    for path, digest in controllers.items():
        _digest(digest, f"controller digest for {path}")

    paths = _string_list(row["execution_dependency_paths"], "execution dependency paths")
    if paths != sorted(paths) or not _REQUIRED_DEPENDENCIES <= set(paths):
        raise ValueError("certificate dependency paths are incomplete or not sorted")

    domain = _mapping(row["initial_domain"], "initial_domain")
    _exact_fields(domain, {
        "assessment_epoch", "time_s", "roster_sha256", "roster_version",
        "capability_versions", "resource_versions", "deployment_change_versions",
        "robot_regions_sha256", "root_domains",
    }, "initial_domain")
    if domain["assessment_epoch"] != 0 or domain["time_s"] != 0.0:
        raise ValueError("certificate is initial-epoch only")
    _digest(domain["roster_sha256"], "roster digest")
    _digest(domain["robot_regions_sha256"], "robot regions digest")
    if type(domain["roster_version"]) is not int or domain["roster_version"] < 0:
        raise ValueError("roster_version must be a nonnegative integer")
    roots = _mapping(domain["root_domains"], "root_domains")
    versions = _mapping(domain["capability_versions"], "capability_versions")
    if version == CERTIFICATE_VERSION:
        if set(roots) != set(versions) or len(roots) != 8 or {CARTER_1, CARTER_2} - set(roots):
            raise ValueError("certificate must bind all eight root and capability domains")
    else:
        coexistence = _mapping(row["coexistence_domain"], "coexistence_domain")
        maintained_coexistence = build_restaurant_initial_coexistence_domain()
        if coexistence != maintained_coexistence:
            raise ValueError("coexistence domain differs from the maintained deployment")
        full8 = set(coexistence["spawn_region_bindings"])
        if set(roots) != {CARTER_1, CARTER_2} or set(versions) != full8:
            raise ValueError("v2 certificate must separate Carter action roots from full8 coexistence")
        row["coexistence_domain"] = maintained_coexistence
    domain["root_domains"] = {
        agent: _validate_root_domain(value, f"root domain for {agent}")
        for agent, value in roots.items()
    }
    if qualification["scope"] == "native_empirical_domain" and any(
            tolerance > limit for root in domain["root_domains"].values()
            for tolerance, limit in zip(root["abs_tolerance"], NATIVE_ROOT_TOLERANCE_LIMIT)):
        if version == CERTIFICATE_VERSION:
            raise ValueError("native root domain exceeds the preregistered v1 tolerance envelope")
        raise ValueError("native Carter root domain exceeds the preregistered tolerance envelope")
    for agent, version in versions.items():
        if type(version) is not int or version < 0:
            raise ValueError(f"capability version for {agent} must be nonnegative")
    for field in ("resource_versions", "deployment_change_versions"):
        values = _mapping(domain[field], field)
        if any(not isinstance(key, str) or not key or type(version) is not int or version < 0
               for key, version in values.items()):
            raise ValueError(f"{field} is invalid")

    if row["action_domains"] != _expected_actions():
        raise ValueError("certificate action domains differ from the bounded construction")
    if CONSTRUCTION_COMPLETION_MS > HARD_BUDGET_MS:
        raise ValueError("construction exceeds the public hard budget")
    registration = _build_registration(row)
    if row["registration_sha256"] != registration["registration_sha256"]:
        raise ValueError("certificate registration identity is invalid")
    return row, registration, actual


def validate_restaurant_initial_positive_certificate(
    certificate: Any, *, expected_certificate_sha256: str,
) -> dict[str, Any]:
    """Validate a private certificate against a separately pinned digest."""
    row, _registration, _digest_value = _validate_certificate(
        certificate, expected_certificate_sha256=expected_certificate_sha256)
    return deepcopy(row)


def _validate_authorities(value: Any) -> dict[str, Any]:
    authorities = _mapping(value, "trusted_authorities")
    _exact_fields(authorities, {
        "evidence_scope", "task_condition_binding_sha256",
        "execution_dependency_manifest", "asset_manifest_sha256",
        "controller_source_sha256", "runtime_profile_sha256",
    }, "trusted_authorities")
    if authorities["evidence_scope"] not in {"synthetic_fixture", "native_empirical_domain"}:
        raise ValueError("trusted authority evidence_scope is unsupported")
    _digest(authorities["task_condition_binding_sha256"], "task condition authority")
    _digest(authorities["asset_manifest_sha256"], "asset manifest authority")
    _digest(authorities["runtime_profile_sha256"], "runtime profile authority")
    manifest = _validate_dependency_manifest(authorities["execution_dependency_manifest"])
    controllers = _mapping(authorities["controller_source_sha256"], "controller authorities")
    if set(controllers) != set(_CONTROLLER_PATHS):
        raise ValueError("trusted authorities must bind exactly five controller sources")
    for path, digest in controllers.items():
        _digest(digest, f"controller authority for {path}")
        if manifest.get(path) != digest:
            raise ValueError("controller authority differs from the execution dependency manifest")
    authorities["execution_dependency_manifest"] = manifest
    authorities["controller_source_sha256"] = dict(sorted(controllers.items()))
    return authorities


def _failure(
    *, episode_id: str, assessment_id: str, assessment_epoch: int,
    quality_failure: str, certificate_sha256: str | None, evaluator_ref: str,
) -> dict[str, Any]:
    return {
        "assessment_id": assessment_id,
        "assessment_epoch": assessment_epoch,
        "feasibility_label": None,
        "oracle_status": "failed",
        "platform_valid": False,
        "evidence_refs": [],
        "evaluator_ref": evaluator_ref,
        "episode_id": episode_id,
        "measurement_id": MEASUREMENT_ID,
        "visibility": "evaluator_only",
        "quality_failure": quality_failure,
        "certificate_sha256": certificate_sha256,
        "scoreable": False,
        "execution_validated": False,
        "business_outcomes_validated": False,
        "feasibility_oracle_verified": False,
        "release_ready": False,
    }


def _public_result(result: Mapping[str, Any]) -> dict[str, Any]:
    return {field: deepcopy(result[field]) for field in _RETURN_FIELDS}


class RestaurantInitialPositiveOracle:
    """Initial assessment evaluator for one pinned positive construction."""

    def __init__(
        self, certificate: Any, *, expected_certificate_sha256: str,
        trusted_authorities: Any,
    ) -> None:
        self._certificate = None
        self._registration = None
        self._authorities = None
        self._certificate_sha256 = (
            expected_certificate_sha256 if isinstance(expected_certificate_sha256, str) else None)
        self._evaluator_ref = (
            EVALUATOR_REF_V2
            if isinstance(certificate, Mapping)
            and type(certificate.get("version")) is int
            and certificate.get("version") == CERTIFICATE_VERSION_V2
            else EVALUATOR_REF
        )
        self._configuration_error: str | None = None
        self._results: list[dict[str, Any]] = []
        self._cache: dict[tuple[str, str, int], dict[str, Any]] = {}
        try:
            row, registration, digest = _validate_certificate(
                deepcopy(certificate),
                expected_certificate_sha256=expected_certificate_sha256,
            )
            authorities = _validate_authorities(deepcopy(trusted_authorities))
            self._certificate = row
            self._registration = registration
            self._authorities = authorities
            self._certificate_sha256 = digest
        except (KeyError, TypeError, ValueError) as exc:
            self._configuration_error = f"configuration_invalid:{type(exc).__name__}"

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
                certificate_sha256=self._certificate_sha256,
                evaluator_ref=self._evaluator_ref)

        if self._configuration_error is not None:
            return fail(self._configuration_error)
        certificate = self._certificate
        registration = self._registration
        authorities = self._authorities
        assert certificate is not None and registration is not None and authorities is not None
        scope = certificate["qualification"]["scope"]
        expected_state_schema = (
            "eai.restaurant.native_initial_state"
            if scope == "native_empirical_domain"
            else "eai.restaurant.synthetic_initial_state_fixture"
        )
        if authorities["evidence_scope"] != scope:
            return fail("qualification_scope_authority_mismatch")
        if authorities["runtime_profile_sha256"] not in certificate[
                "qualification"]["allowed_runtime_profile_sha256"]:
            return fail("runtime_profile_outside_qualified_domain")

        identities = certificate["identities"]
        manifest = authorities["execution_dependency_manifest"]
        if (
            identities["task_condition_binding_sha256"]
            != authorities["task_condition_binding_sha256"]
            or identities["execution_dependency_manifest_sha256"]
            != canonical_sha256(manifest)
            or certificate["execution_dependency_paths"] != list(manifest)
            or identities["asset_manifest_sha256"] != authorities["asset_manifest_sha256"]
            or identities["controller_source_sha256"]
            != authorities["controller_source_sha256"]
        ):
            return fail("execution_authority_mismatch")

        domain = certificate["initial_domain"]
        certificate_version = certificate["version"]
        coexistence_domain = certificate.get("coexistence_domain")
        expected_agents = (
            set(coexistence_domain["spawn_region_bindings"])
            if certificate_version == CERTIFICATE_VERSION_V2
            else set(domain["root_domains"])
        )
        state = _mapping(report.get("native_initial_state"), "native_initial_state")
        binding = _mapping(report.get("deployment_binding"), "deployment_binding")
        runtime_state = _mapping(session.get("runtime_state"), "runtime_state")
        gate_runtime = _mapping(gate.get("runtime_state"), "assessment runtime_state")
        if (
            session.get("episode_id") != episode_id
            or binding.get("episode_id") != episode_id
            or state.get("episode_id") != episode_id
            or session.get("current_time_s") != 0.0
            or gate.get("status") != "required"
            or gate.get("external_evaluation") is not None
            or gate.get("history")
            or gate.get("oracle_evaluations")
            or assessment_epoch != domain["assessment_epoch"]
            or state.get("time_s") != domain["time_s"]
            or state.get("origin_physics_step") is None
        ):
            return fail("not_pristine_initial_assessment")
        if runtime_state != gate_runtime or runtime_state.get("time_s") != 0.0:
            return fail("initial_runtime_snapshot_mismatch")

        if (
            gate.get("public_task_sha256") != identities["public_task_sha256"]
            or state.get("public_task_sha256") != identities["public_task_sha256"]
            or binding.get("public_task_sha256") != identities["public_task_sha256"]
            or state.get("deployment_sha256") != identities["deployment_sha256"]
            or binding.get("deployment_sha256") != identities["deployment_sha256"]
            or state.get("task_id") != TASK_ID
            or state.get("deployment_profile") != DEPLOYMENT_PROFILE
            or binding.get("schema") != "eai.restaurant.deployment_ack"
            or binding.get("version") != 2
            or binding.get("profile") != DEPLOYMENT_PROFILE
        ):
            return fail("task_or_deployment_identity_mismatch")
        if (
            state.get("schema") != expected_state_schema
            or state.get("version") != 1
            or state.get("status") != "measured"
            or state.get("platform_gaps") != []
            or state.get("participant_error") is not False
            or state.get("all_eight_initial_regions_available") is not True
            or state.get("post_warmup_sample_measured") is not True
        ):
            return fail("initial_state_quality_failed")

        current_pool = _mapping(gate.get("current_robot_pool"), "current robot pool")
        current_ids = gate.get("current_agent_ids")
        roots = _mapping(state.get("native_initial_roots"), "native initial roots")
        initial_regions = _mapping(state.get("initial_regions"), "initial_regions")
        robot_regions = _mapping(state.get("robot_regions"), "robot_regions")
        if (
            not isinstance(current_ids, list)
            or set(current_ids) != expected_agents
            or (certificate_version == CERTIFICATE_VERSION_V2
                and len(current_ids) != len(expected_agents))
            or set(current_pool) != expected_agents
            or set(roots) != expected_agents
            or set(initial_regions) != expected_agents
            or set(robot_regions) != expected_agents
            or canonical_sha256(current_pool) != domain["roster_sha256"]
            or canonical_sha256(robot_regions) != domain["robot_regions_sha256"]
            or runtime_state.get("robot_regions") != robot_regions
            or runtime_state.get("object_states") != {}
            or gate.get("runtime_state_sha256") != canonical_sha256(runtime_state)
        ):
            return fail("full8_initial_domain_mismatch")
        if (
            gate.get("roster_version") != domain["roster_version"]
            or gate.get("capability_versions") != domain["capability_versions"]
            or gate.get("resource_versions") != domain["resource_versions"]
            or gate.get("deployment_change_versions")
            != domain["deployment_change_versions"]
        ):
            return fail("initial_version_domain_mismatch")

        for agent, root_domain in domain["root_domains"].items():
            sample = roots.get(agent)
            vector = sample.get("root_state_w") if isinstance(sample, Mapping) else None
            if (not isinstance(sample, Mapping)
                    or sample.get("public_agent_id") != agent
                    or sample.get("physics_step") != state.get("origin_physics_step")
                    or sample.get("time_s") != 0.0
                    or not isinstance(vector, list) or len(vector) != 13):
                return fail("invalid_full8_root_state")
            try:
                values = [_finite(item, f"root sample for {agent}") for item in vector]
            except ValueError:
                return fail("invalid_full8_root_state")
            if any(abs(value - center) > tolerance for value, center, tolerance in zip(
                    values, root_domain["center"], root_domain["abs_tolerance"])):
                return fail("initial_root_outside_qualified_domain")
            membership = initial_regions[agent]
            try:
                membership_error = _finite(
                    membership.get("region_error_m"),
                    f"initial region error for {agent}", nonnegative=True)
                membership_tolerance = _finite(
                    membership.get("region_tolerance_m"),
                    f"initial region tolerance for {agent}", nonnegative=True)
            except (AttributeError, ValueError):
                return fail("invalid_full8_initial_region_membership")
            if (not isinstance(membership, Mapping)
                    or membership.get("status") != "measured_region_membership"
                    or membership.get("region_id") != robot_regions[agent]
                    or membership_error > membership_tolerance):
                return fail("invalid_full8_initial_region_membership")

        archive = state.get("source_epoch_archive")
        try:
            archive_sha256 = (
                _digest(archive.get("sha256"), "native epoch archive digest")
                if isinstance(archive, Mapping) else None)
        except ValueError:
            archive_sha256 = None
        if (
            not isinstance(archive, Mapping)
            or not isinstance(archive.get("path"), str) or not archive["path"]
            or archive_sha256 is None
            or binding.get("native_epoch_archive") != archive
            or binding.get("native_initial_state_sha256") != canonical_sha256(state)
        ):
            return fail("native_epoch_join_mismatch")
        if certificate_version == CERTIFICATE_VERSION_V2:
            try:
                validate_restaurant_initial_coexistence(state, coexistence_domain)
            except (KeyError, OSError, TypeError, ValueError):
                return fail("initial_coexistence_validation_failed")

        try:
            solver_robots = []
            for agent in (CARTER_1, CARTER_2):
                public = current_pool[agent]
                capabilities = public["public_capabilities"]
                payload = public["physical_profile"]["effective_payload_kg"]
                solver_robots.append({
                    "id": agent,
                    "capabilities": list(capabilities),
                    "payload_kg": payload,
                })
            solver_state = {
                "time_s": 0.0,
                "true_facts": [],
                "resources": {"time_ms": HARD_BUDGET_MS},
                "max_active_agents": 2,
                "robots": solver_robots,
                "roster_version": gate["roster_version"],
                "capability_versions": {
                    agent: gate["capability_versions"][agent]
                    for agent in (CARTER_1, CARTER_2)
                },
            }
            grounding = {
                "status": "verified",
                "state_sha256": canonical_sha256(solver_state),
                "evidence_refs": [
                    f"certificate:{self._certificate_sha256}",
                    f"native-epoch:{archive['sha256']}",
                ],
            }
            solver_result = query_remaining_feasibility(
                registration,
                measurement_id=MEASUREMENT_ID,
                episode_id=episode_id,
                assessment_id=assessment_id,
                assessment_epoch=assessment_epoch,
                task_sha256=identities["public_task_sha256"],
                state=solver_state,
                grounding=grounding,
            )
        except Exception as exc:
            return fail(f"positive_solver_failed:{type(exc).__name__}")
        if (solver_result.get("oracle_status") != "verified"
                or solver_result.get("feasibility_label") != "deployable"
                or solver_result.get("platform_valid") is not True):
            return fail("positive_construction_not_established")

        result = deepcopy(dict(solver_result))
        result.update({
            "evaluator_ref": self._evaluator_ref,
            "evidence_refs": [
                f"certificate:{self._certificate_sha256}",
                f"initial-state:{canonical_sha256(state)}",
            ],
            "certificate_sha256": self._certificate_sha256,
            "qualification_scope": scope,
            "execution_validated": False,
            "business_outcomes_validated": False,
            "feasibility_oracle_verified": scope == "native_empirical_domain",
            "release_ready": False,
            "scoreable": False,
            "visibility": "evaluator_only",
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
                episode_id=episode_id,
                assessment_id=assessment_id,
                assessment_epoch=assessment_epoch,
                quality_failure=f"trusted_input_failed:{type(exc).__name__}",
                certificate_sha256=self._certificate_sha256,
                evaluator_ref=self._evaluator_ref,
            )
        self._cache[key] = deepcopy(result)
        self._results.append(deepcopy(result))
        return _public_result(result)

    def report(self) -> dict[str, Any]:
        """Return hashes and outcomes only; never expose the construction."""
        return {
            "schema": "eai.restaurant.initial_positive_evidence",
            "version": 1,
            "visibility": "evaluator_only",
            "certificate_sha256": self._certificate_sha256,
            "registration_sha256": (
                self._registration.get("registration_sha256")
                if self._registration is not None else None),
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
    "RestaurantInitialPositiveOracle",
    "build_restaurant_initial_coexistence_domain",
    "validate_restaurant_initial_coexistence",
    "validate_restaurant_initial_positive_certificate",
]
