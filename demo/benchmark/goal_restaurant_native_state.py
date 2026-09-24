"""Post-warmup native initial state for the public Restaurant full8 task.

The adapter consumes only the fleet controller's frozen episode epoch.  It
never reads a witness plan or invents region membership from a nearest center,
fallback label, or participant-provided content.
"""

from __future__ import annotations

from copy import deepcopy
import math
from typing import Any, Mapping

from .goal_restaurant_fleet import (
    CLOSED_VALIDATION_FLAGS,
    FLEET_CONTROLLER_REGISTRY,
    RESTAURANT_PUBLIC_TASK_SHA256,
    RESTAURANT_TASK_ID,
)


SCHEMA = "eai.restaurant.native_initial_state"
MAINTAINED_REGION_SOURCE = "restaurant_carter_route_A-E_start"


def _platform_gap(reason: str, *, detail: str | None = None) -> dict[str, Any]:
    row: dict[str, Any] = {
        "schema": SCHEMA,
        "version": 1,
        "status": "platform_gap",
        "task_id": RESTAURANT_TASK_ID,
        "public_task_sha256": RESTAURANT_PUBLIC_TASK_SHA256,
        "native_initial_roots": {},
        "initial_regions": {},
        "robot_regions": {},
        "platform_gaps": [{"reason": reason, **({"detail": detail} if detail else {})}],
        "participant_error": False,
        "all_eight_initial_regions_available": False,
        **CLOSED_VALIDATION_FLAGS,
    }
    return row


def _is_finite_root(value: Any) -> bool:
    if not isinstance(value, list) or len(value) < 13:
        return False
    if any(isinstance(item, bool) or not isinstance(item, (int, float))
           or not math.isfinite(float(item)) for item in value):
        return False
    w, x, y, z = (float(item) for item in value[3:7])
    return math.isclose(math.sqrt(w * w + x * x + y * y + z * z),
                        1.0, rel_tol=0.0, abs_tol=1e-3)


def build_restaurant_native_initial_state(fleet: Any) -> dict[str, Any]:
    """Bind full8 post-warmup roots and only proven initial-region membership."""
    from demo.benchmark.goal_driven_tasks import build_goal_task_package
    from demo.benchmark.provenance import canonical_sha256
    from demo.restaurant_benchmark import (
        config,
        navigation_probe,
        route_admission,
    )

    if not hasattr(fleet, "episode_metadata") or not hasattr(fleet, "snapshot"):
        return _platform_gap("missing_restaurant_fleet_lifecycle")
    try:
        metadata = fleet.episode_metadata()
    except Exception as exc:  # A lifecycle/platform failure is never participant error.
        return _platform_gap("post_warmup_epoch_unavailable", detail=str(exc))
    if not isinstance(metadata, Mapping):
        return _platform_gap("foreign_or_invalid_post_warmup_epoch")
    contract = None
    expected_id, expected_sha = RESTAURANT_TASK_ID, RESTAURANT_PUBLIC_TASK_SHA256
    if metadata.get("deployment_profile") is not None:
        from .goal_restaurant_deployment_v2 import (
            DEPLOYMENT_PROFILE, build_restaurant_deployment_v2,
        )
        if metadata["deployment_profile"] != DEPLOYMENT_PROFILE:
            return _platform_gap("unsupported_deployment_profile")
        contract = build_restaurant_deployment_v2()
        expected_id = contract["deployment"]["task_id"]
        expected_sha = contract["deployment"]["public_task_sha256"]
        if metadata.get("deployment_sha256") != canonical_sha256(contract["deployment"]):
            return _platform_gap("deployment_companion_identity_mismatch")

    def gap(reason: str, *, detail: str | None = None) -> dict[str, Any]:
        return {**_platform_gap(reason, detail=detail), "task_id": expected_id,
                "public_task_sha256": expected_sha}

    if (metadata.get("schema") != "eai.restaurant.native_episode_epoch"
            or metadata.get("version") != 1
            or metadata.get("lifecycle_state") != "post_warmup"
            or metadata.get("native_initial_state_ready") is not True
            or metadata.get("origin_time_s") != 0.0
            or metadata.get("task_id") != expected_id
            or metadata.get("public_task_sha256") != expected_sha):
        return gap("foreign_or_invalid_post_warmup_epoch")

    public = (contract["public_task"] if contract else
              build_goal_task_package("restaurant")["public_task"])
    if (public.get("task_id") != expected_id
            or canonical_sha256(public) != expected_sha
            or config.BENCHMARK_DEPLOYMENT_PROFILE != "bound_native_v2"):
        return gap("maintained_restaurant_task_or_profile_drifted")
    expected_ids = tuple(FLEET_CONTROLLER_REGISTRY)
    public_spawns = public.get("public_world", {}).get("robot_spawn_positions")
    expected_spawns = {
        public_id: list(config.BENCHMARK_ROBOT_SPAWN_XYZ_M[binding["spawn_key"]])
        for public_id, binding in FLEET_CONTROLLER_REGISTRY.items()
    }
    if (not isinstance(public_spawns, Mapping)
            or dict(public_spawns) != expected_spawns):
        return gap("maintained_restaurant_spawn_contract_drifted")

    try:
        snapshot = fleet.snapshot()
    except Exception as exc:
        return gap("post_warmup_snapshot_unavailable", detail=str(exc))
    if not isinstance(snapshot, Mapping):
        return gap("post_warmup_snapshot_epoch_mismatch")
    if contract and any(snapshot.get("episode", {}).get(key) != metadata.get(key)
                        for key in ("episode_id", "task_id", "public_task_sha256",
                                    "deployment_profile", "deployment_sha256", "archive")):
        return gap("post_warmup_snapshot_epoch_mismatch")
    agents = snapshot.get("agents") if isinstance(snapshot, Mapping) else None
    frozen = metadata.get("post_warmup_sample")
    frozen_agents = frozen.get("agents") if isinstance(frozen, Mapping) else None
    origin_step = metadata.get("origin_physics_step")
    if (snapshot.get("schema") != "eai.restaurant.native_fleet_snapshot"
            or snapshot.get("physics_step") != origin_step
            or snapshot.get("time_s") != 0.0
            or not isinstance(frozen, Mapping)
            or frozen.get("physics_step") != origin_step
            or frozen.get("time_s") != 0.0
            or not isinstance(agents, Mapping)
            or not isinstance(frozen_agents, Mapping)
            or tuple(agents) != expected_ids
            or tuple(frozen_agents) != expected_ids):
        return gap("post_warmup_snapshot_epoch_mismatch")

    native_roots: dict[str, dict[str, Any]] = {}
    initial_regions: dict[str, dict[str, Any]] = {}
    robot_regions: dict[str, str] = {}
    gaps: list[dict[str, Any]] = []
    for public_id in expected_ids:
        binding = FLEET_CONTROLLER_REGISTRY[public_id]
        sample = agents[public_id]
        frozen_sample = frozen_agents[public_id]
        root = sample.get("root_state_w") if isinstance(sample, Mapping) else None
        if (not isinstance(sample, Mapping)
                or sample.get("public_agent_id") != public_id
                or sample.get("native_agent_id") != binding["native_agent_id"]
                or sample.get("physics_step") != origin_step
                or sample.get("time_s") != 0.0
                or not _is_finite_root(root)
                or sample != frozen_sample):
            return gap(
                "invalid_or_changed_post_warmup_root", detail=public_id)
        root = [float(value) for value in root]
        anchor = expected_spawns[public_id]
        native_roots[public_id] = {
            "public_agent_id": public_id,
            "native_agent_id": binding["native_agent_id"],
            "physics_step": origin_step,
            "time_s": 0.0,
            "root_state_w": root,
            "maintained_spawn_xyz_m": list(anchor),
            "post_warmup_displacement_from_spawn_m": math.dist(root[:3], anchor),
            "spawn_contract_source": "public_task_and_bound_native_v2_config",
            "deployment_profile": config.BENCHMARK_DEPLOYMENT_PROFILE,
        }

        # Only Carter1 has an independently maintained metric initial-region
        # binding: the A-E route start is the Restaurant staging center.  The
        # distributed full8 spawn anchors do not thereby become staging, and
        # the Iris spawn anchors are far from the maintained overview center.
        if contract is not None:
            region = contract["deployment"]["spawn_regions"][public_id]
            geometry = contract["deployment"]["region_bindings"][region]
            center = geometry.get("center_xyz_m", geometry.get("center_xy_m"))
            error = math.dist(root[:len(center)], center)
            admitted = error <= geometry["tolerance_m"]
            initial_regions[public_id] = {
                "status": "measured_region_membership" if admitted else "platform_gap",
                "region_id": region if admitted else None,
                "region_center_m": list(center), "region_error_m": error,
                "region_tolerance_m": geometry["tolerance_m"],
                "source": contract["deployment"]["profile"],
            }
            if admitted:
                robot_regions[public_id] = region
            else:
                gaps.append({"public_agent_id": public_id,
                             "reason": "maintained_initial_region_membership_failed"})
        elif public_id == "restaurant_carter_1":
            center = list(navigation_probe.ROUTES["A-E"]["start_xy_m"])
            error = math.dist(root[:2], center)
            admitted = error <= navigation_probe.FINAL_TOLERANCE_M
            initial_regions[public_id] = {
                "status": ("measured_region_membership" if admitted
                           else "platform_gap"),
                "region_id": "restaurant_staging" if admitted else None,
                "region_center_xy_m": center,
                "region_error_m": error,
                "region_tolerance_m": navigation_probe.FINAL_TOLERANCE_M,
                "source": MAINTAINED_REGION_SOURCE,
            }
            if admitted:
                robot_regions[public_id] = "restaurant_staging"
            else:
                gaps.append({"public_agent_id": public_id,
                             "reason": "maintained_initial_region_membership_failed"})
        else:
            initial_regions[public_id] = {
                "status": "platform_gap",
                "region_id": None,
                "reason": "missing_maintained_spawn_to_initial_region_binding",
            }
            gaps.append({
                "public_agent_id": public_id,
                "reason": "missing_maintained_spawn_to_initial_region_binding",
            })

    return {
        "schema": SCHEMA,
        "version": 1,
        "status": "platform_gap" if gaps else "measured",
        "task_id": expected_id,
        "public_task_sha256": expected_sha,
        **({"deployment_profile": metadata["deployment_profile"],
            "deployment_sha256": metadata["deployment_sha256"]} if contract else {}),
        "episode_id": metadata["episode_id"],
        "origin_physics_step": origin_step,
        "time_s": 0.0,
        "native_initial_roots": native_roots,
        "initial_regions": initial_regions,
        "robot_regions": robot_regions,
        "platform_gaps": gaps,
        "participant_error": False,
        "all_eight_initial_regions_available": len(robot_regions) == len(expected_ids),
        "source_epoch_archive": deepcopy(metadata["archive"]),
        "post_warmup_sample_measured": True,
        "warmup_stability_certified": False,
        "known_threshold_distinction": {
            "m20_navigation_final_tolerance_m": route_admission.GOAL_FINAL_TOLERANCE_M,
            "legacy_ground_observation_tolerance_m": navigation_probe.FINAL_TOLERANCE_M,
            "thresholds_equated": False,
            "admission_promoted": False,
        },
        **CLOSED_VALIDATION_FLAGS,
    }


__all__ = ["build_restaurant_native_initial_state"]
