"""Same-tick native scene queries owned by the full Restaurant executor.

This producer never steps physics and never accepts participant-supplied
measurements. Ground readiness preserves the existing support predicate and
0.25m observation tolerance. Aerial/local surface probes report the actual
downward PhysX ray only: they are not camera images, line-of-sight proofs, or
kitchen/dining business observations. Full public observe-domain admission is
still a separate platform gate.
"""

from __future__ import annotations

from copy import deepcopy
import math
from typing import Any, Mapping

from .goal_restaurant_fleet import (
    FLEET_CONTROLLER_REGISTRY, CLOSED_VALIDATION_FLAGS,
    RESTAURANT_PUBLIC_TASK_SHA256, RESTAURANT_TASK_ID,
)
from .goal_restaurant_episode import (
    MINIMUM_REGION_SUPPORT_SAMPLES,
    REGION_OBSERVATION_TOLERANCE_M,
    REGION_SUPPORT_RAY_DISTANCE_M,
    REGION_SUPPORT_RAY_HEIGHT_M,
    REGION_SUPPORT_SAMPLE_OFFSETS_M,
)
from .provenance import canonical_sha256
from demo.restaurant_benchmark import contact_probe, route_admission, runtime_probe


class RestaurantObservationCoverageError(RuntimeError):
    """Missing platform query coverage, not a model decision or no-hit result."""


def _finite_vector(value: Any, *, length: int, label: str) -> list[float]:
    if (not isinstance(value, (list, tuple)) or len(value) != length
            or any(isinstance(x, bool) or not isinstance(x, (int, float))
                   or not math.isfinite(x) for x in value)):
        raise ValueError(f"{label} must have {length} finite components")
    return list(value)


def _raw_hit(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or type(value.get("hit")) is not bool:
        raise RuntimeError("PhysX query did not return a valid hit/no-hit result")
    hit = runtime_probe._hit_record(value)
    if hit["hit"]:
        _finite_vector(hit.get("position_m"), length=3, label="hit position")
        _finite_vector(hit.get("normal"), length=3, label="hit normal")
        if not isinstance(value.get("collision"), str) or not value["collision"]:
            raise RuntimeError("PhysX hit lacks a collision prim identity")
        if "distance_m" in hit:
            distance = hit["distance_m"]
            if not math.isfinite(distance) or distance < 0:
                raise RuntimeError("PhysX hit distance is invalid")
    # A no-hit is an actual measurement. It is not a sensor/infrastructure
    # exception and must survive in the raw evidence.
    return hit


class RestaurantNativeObservationProvider:
    """Trusted fleet + PhysX query; output remains private until metered delivery."""

    def __init__(self, fleet: Any, *, scene_query: Any = None):
        self.fleet = fleet
        self.session = fleet.session
        if self.session.env_cfg.sim.enable_scene_query_support is not True:
            raise RestaurantObservationCoverageError("native scene queries are not enabled")
        if scene_query is None:
            import omni.physx
            scene_query = omni.physx.get_physx_scene_query_interface()
        if not callable(getattr(scene_query, "raycast_closest", None)):
            raise RestaurantObservationCoverageError("native PhysX scene query is unavailable")
        self._query = scene_query

    def _sample(self, producer_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        if producer_id not in FLEET_CONTROLLER_REGISTRY:
            raise ValueError("unknown full8 observation producer")
        try:
            lifecycle = self.fleet.episode_metadata()
        except RuntimeError as exc:
            raise RestaurantObservationCoverageError(
                "observation requires the post-warmup episode epoch") from exc
        if (lifecycle.get("schema") != "eai.restaurant.native_episode_epoch"
                or lifecycle.get("version") != 1
                or lifecycle.get("task_id") != RESTAURANT_TASK_ID
                or lifecycle.get("public_task_sha256") != RESTAURANT_PUBLIC_TASK_SHA256
                or not isinstance(lifecycle.get("episode_id"), str)
                or not lifecycle["episode_id"]
                or lifecycle.get("lifecycle_state") != "post_warmup"
                or lifecycle.get("native_initial_state_ready") is not True):
            raise RestaurantObservationCoverageError("observation requires the post-warmup episode epoch")
        snapshot = self.fleet.snapshot()
        epoch = snapshot.get("episode", {})
        if any(epoch.get(key) != lifecycle.get(key) for key in (
                "schema", "version", "task_id", "public_task_sha256",
                "lifecycle_state", "native_initial_state_ready", "episode_id",
                "origin_physics_step", "origin_time_s", "archive")):
            raise RuntimeError("native observation epoch mismatch")
        step, time_s = snapshot.get("physics_step"), snapshot.get("time_s")
        origin = lifecycle.get("origin_physics_step")
        dt = float(self.session.base_env.sim.get_physics_dt())
        if (type(step) is not int or type(origin) is not int or step < origin
                or isinstance(time_s, bool) or not isinstance(time_s, (int, float))
                or not math.isfinite(time_s) or not math.isfinite(dt) or dt <= 0
                or lifecycle.get("origin_time_s") != 0.0
                or not math.isclose(time_s, (step - origin) * dt,
                                    rel_tol=0, abs_tol=1e-9)):
            raise RuntimeError("native observation clock mismatch")
        sample = snapshot["agents"][producer_id]
        if (sample.get("public_agent_id") != producer_id
                or sample.get("native_agent_id")
                != FLEET_CONTROLLER_REGISTRY[producer_id]["native_agent_id"]
                or sample.get("physics_step") != snapshot.get("physics_step")
                or sample.get("time_s") != snapshot.get("time_s")):
            raise RuntimeError("native observation sample identity/clock mismatch")
        root = _finite_vector(sample.get("root_state_w"), length=13, label="native root")
        if not math.isclose(sum(x*x for x in root[3:7]), 1., abs_tol=1e-3):
            raise ValueError("native root quaternion is not normalized")
        stamp = {"simulation_step": sample["physics_step"], "time_s": sample["time_s"],
                 "root_state_w": root}
        return deepcopy(sample), stamp

    def _check_unchanged(self, producer_id: str, sample: Mapping[str, Any]) -> None:
        after = self.fleet.snapshot()["agents"][producer_id]
        if after != sample:
            raise RuntimeError("native state changed while reading PhysX scene observations")

    def ground_readiness(self, producer_id: str, region_id: str) -> dict[str, Any]:
        """Measure maintained service support, including false/out-of-region results.

        This preserves the legacy scene_observation format so independently
        checked successful ground measurements can enter readiness-v2. The
        caller must still bind a real dispatched observe and prior arrival.
        """
        if region_id not in {"kitchen_service", "dining_service"}:
            raise RestaurantObservationCoverageError("region has no ground-readiness query binding")
        sample, stamp = self._sample(producer_id)
        if FLEET_CONTROLLER_REGISTRY[producer_id]["command_profile"] == "aerial_position":
            raise RestaurantObservationCoverageError("aerial observation is not ground readiness")
        center = list(route_admission.KITCHEN_SERVICE_XY_M if region_id == "kitchen_service"
                      else route_admission.DINING_SERVICE_XY_M)
        root_xy = stamp["root_state_w"][:2]
        error = math.dist(root_xy, center)
        samples = []
        for dx, dy in REGION_SUPPORT_SAMPLE_OFFSETS_M:
            origin = [center[0] + dx, center[1] + dy, REGION_SUPPORT_RAY_HEIGHT_M]
            hit = _raw_hit(self._query.raycast_closest(
                tuple(origin), (0., 0., -1.), REGION_SUPPORT_RAY_DISTANCE_M))
            passed = (hit["hit"] is True
                      and hit.get("collision") == contact_probe.GROUND_COLLISION_PATH
                      and abs(hit["position_m"][2]) <= .02
                      and hit["normal"][2] >= .99)
            samples.append({"origin_m": origin, "direction": [0., 0., -1.],
                            "distance_m": REGION_SUPPORT_RAY_DISTANCE_M,
                            "hit": hit, "passed": passed})
        self._check_unchanged(producer_id, sample)
        passed_count = sum(row["passed"] for row in samples)
        return {
            "schema": "eai.restaurant.native_region_observation", "version": 1,
            "region_id": region_id, "producer_id": producer_id,
            "query_kind": "episode_timestamped_physx_ground_support_raycast",
            "scene_queries_enabled": True,
            "expected_collision_path": contact_probe.GROUND_COLLISION_PATH,
            "region_center_xy_m": center, "observed_robot_xy_m": root_xy,
            "region_error_m": error, "region_tolerance_m": REGION_OBSERVATION_TOLERANCE_M,
            "support_samples": samples, "minimum_support_samples": MINIMUM_REGION_SUPPORT_SAMPLES,
            "passed_support_samples": passed_count, "stamp": stamp,
            "scene_observation_validated": (error <= REGION_OBSERVATION_TOLERANCE_M
                                             and passed_count >= MINIMUM_REGION_SUPPORT_SAMPLES),
        }

    def local_surface_probe(self, producer_id: str) -> dict[str, Any]:
        """Actual local downward ray for any member; no business/visibility claim."""
        sample, stamp = self._sample(producer_id)
        origin = list(stamp["root_state_w"][:3])
        # Extends below the world floor, while keeping the actual native root
        # as origin. Robot self-hits/obstacles are retained, never filtered into
        # a fabricated ground success.
        distance = max(REGION_SUPPORT_RAY_DISTANCE_M,
                       origin[2] + REGION_SUPPORT_RAY_DISTANCE_M)
        hit = _raw_hit(self._query.raycast_closest(tuple(origin), (0., 0., -1.), distance))
        self._check_unchanged(producer_id, sample)
        row = {
            "schema": "eai.restaurant.native_local_surface_probe", "version": 1,
            "producer_id": producer_id, "native_agent_id": sample["native_agent_id"],
            "query_kind": "physx_local_downward_ray", "stamp": stamp,
            "origin_m": origin, "direction": [0., 0., -1.], "distance_m": distance,
            "hit": hit, "visibility_verified": False, "camera_image_produced": False,
            "business_ground_readiness": False, **CLOSED_VALIDATION_FLAGS,
        }
        row["evidence_ref"] = "sha256:" + canonical_sha256(row)
        return row

    def aerial_overview_probe(self, producer_id: str) -> dict[str, Any]:
        """Bind a raw aerial ray to the overview; this proves no visual coverage.

        Region membership is a measured 3D fact, independent of whether the
        downward ray hits ground, furniture, the robot itself or nothing.
        """
        if (producer_id not in FLEET_CONTROLLER_REGISTRY
                or FLEET_CONTROLLER_REGISTRY[producer_id]["command_profile"]
                != "aerial_position"):
            raise RestaurantObservationCoverageError("overview probe requires an aerial producer")
        raw = self.local_surface_probe(producer_id)
        raw.pop("evidence_ref")
        center = list(route_admission.OVERVIEW_ANCHOR_XYZ_M)
        error = math.dist(raw["stamp"]["root_state_w"][:3], center)
        row = {**raw, "schema": "eai.restaurant.native_aerial_overview_probe",
               "region_id": "restaurant_overview", "region_center_xyz_m": center,
               "region_error_m": error,
               "region_tolerance_m": route_admission.AERIAL_FINAL_TOLERANCE_M,
               "region_membership_validated": error <= route_admission.AERIAL_FINAL_TOLERANCE_M}
        row["evidence_ref"] = "sha256:" + canonical_sha256(row)
        return row

    @staticmethod
    def bind_transport_stamp(scene: Mapping[str, Any]) -> dict[str, Any]:
        """Admit only independently rechecked ground facts to readiness transport."""
        from .goal_restaurant_business_v2 import _verify_observation_physics

        producer = scene["producer_id"]
        if producer not in FLEET_CONTROLLER_REGISTRY:
            raise ValueError("unknown observation producer")
        if FLEET_CONTROLLER_REGISTRY[producer]["command_profile"] == "aerial_position":
            raise ValueError("aerial probe is not a ground-readiness observation")
        region = scene.get("region_id")
        if region not in {"kitchen_service", "dining_service"}:
            raise ValueError("observation has no ground-readiness region")
        center = list(route_admission.KITCHEN_SERVICE_XY_M if region == "kitchen_service"
                      else route_admission.DINING_SERVICE_XY_M)
        _verify_observation_physics(
            {"producer_id": producer, "region_id": region, "stamp": scene["stamp"],
             "scene_observation": scene}, producer=producer, region=region,
            region_bindings={region: {"center_xy_m": center}})
        return {**deepcopy(dict(scene)), "stamp": {
            **deepcopy(dict(scene["stamp"])), "producer_id": producer,
            "native_agent_id": FLEET_CONTROLLER_REGISTRY[producer]["native_agent_id"],
        }}
