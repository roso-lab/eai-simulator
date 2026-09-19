"""Bounded Isaac execution for an explicit Factory vertical objective.

This is a probe executor, not a general locomotion planner.  It follows a
declared XY target with a capped velocity command and accepts a stair only when
calibrated live support, full quaternion and support-relative height progress
remain healthy. A raw PhysX foot-contact sampler is wired into the execution
loop for fixed-budget zero-command calibration records; it cannot authorize
stair motion until its evidence is independently calibrated.
No teleport, reset recovery, bbox-as-floor or target-Z shortcut is used.
"""

from __future__ import annotations

import argparse
import copy
from contextlib import ExitStack
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import statistics
import sys
import time

REPO = Path(__file__).resolve().parents[2]
for directory in (REPO, REPO / "source/EAI", REPO / "source/EAI_assets", REPO / "source/EAI_hmrs"):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from demo.benchmark.vertical_traversal import (
    VerticalProbe, advance_ground_health, advance_vertical_progress,
    decode_foot_contact_buffer, foot_body_indices,
)


SUPPORT_OBSERVATION_SCHEMA = "factory_stair_support_observation_v1"
MICRO_PROBE_LOG_SCHEMA = "factory_stair_micro_probe_v1"
FOOT_SUPPORT_CALIBRATION_SCHEMA = "factory_stair_foot_support_calibration_v1"


@dataclass(frozen=True)
class SupportObservationSpec:
    """Declare the evidence source used to observe physical support.

    A navigation footprint is deliberately absent from this object.  A
    calibrated support observation must identify its evidence source and
    calibrated XY sample offsets; a profile's conservative navigation radius
    is never accepted as a contact or support aperture.
    """

    schema: str
    evidence_kind: str
    calibrated: bool
    calibration_id: str
    offsets_xy_m: tuple[tuple[float, float], ...]

    @classmethod
    def unavailable(cls):
        return cls(SUPPORT_OBSERVATION_SCHEMA, "none", False, "", ())

    def as_record(self):
        return {
            "schema": self.schema,
            "evidence_kind": self.evidence_kind,
            "calibrated": self.calibrated,
            "calibration_id": self.calibration_id,
            "offsets_xy_m": [list(offset) for offset in self.offsets_xy_m],
        }


@dataclass(frozen=True)
class FootSupportCalibration:
    """Runtime calibration for one exact foot/contact stream.

    The calibration is scoped to the current simulator session and controller
    configuration. It does not promote a robot model or controller globally.
    """

    schema: str
    calibration_id: str
    observation: SupportObservationSpec
    body_paths: tuple[str, ...]
    static_collision_paths: tuple[str, ...]
    reference_clearance_m: float
    calibration_minimum_stance_feet: int
    runtime_minimum_stance_feet: int
    minimum_normal_force_n: float
    maximum_support_spread_m: float
    sample_count: int

    def as_record(self):
        return {
            "schema": self.schema,
            "calibration_id": self.calibration_id,
            "observation": self.observation.as_record(),
            "body_paths": list(self.body_paths),
            "static_collision_paths": list(self.static_collision_paths),
            "reference_clearance_m": self.reference_clearance_m,
            "calibration_minimum_stance_feet": self.calibration_minimum_stance_feet,
            "runtime_minimum_stance_feet": self.runtime_minimum_stance_feet,
            "minimum_normal_force_n": self.minimum_normal_force_n,
            "maximum_support_spread_m": self.maximum_support_spread_m,
            "sample_count": self.sample_count,
            "scope": "current_simulator_session_and_controller_configuration",
            "execution_validated": False,
        }


def support_observation_error(observation):
    """Return why a support observation cannot be admitted, or ``None``.

    This is intentionally strict.  The current vertical executor has no
    calibrated foot/contact stream, so its runtime observation remains
    unavailable and the probe stops before issuing a movement command.
    """

    if not isinstance(observation, SupportObservationSpec):
        return "invalid_support_observation"
    if observation.schema != SUPPORT_OBSERVATION_SCHEMA:
        return "unsupported_support_observation_schema"
    if not observation.calibrated:
        return "uncalibrated_support_observation"
    if observation.evidence_kind not in {"foot_contact", "contact_patch"}:
        return "missing_foot_or_contact_evidence"
    if not observation.calibration_id:
        return "missing_support_calibration_id"
    if not observation.offsets_xy_m:
        return "missing_calibrated_support_points"
    try:
        for offset in observation.offsets_xy_m:
            _finite(offset, 2, "support observation offset")
    except (TypeError, ValueError, OverflowError):
        return "invalid_calibrated_support_points"
    return None


def _stance_contact(foot, *, static_collision_paths, minimum_normal_force_n):
    """Select the strongest near-horizontal static contact for one foot."""

    candidates = []
    for contact in foot.get("contacts", ()): 
        try:
            normal = _finite(contact["normal_world"], 3, "contact normal")
            force = float(contact["normal_force_N"])
            point = _finite(contact["position_world_m"], 3, "contact position")
            separation = float(contact["separation_m"])
            norm = math.sqrt(sum(value * value for value in normal))
            if (contact.get("collision_path") in static_collision_paths
                    and math.isfinite(force) and force >= minimum_normal_force_n
                    and math.isfinite(separation) and separation <= 0.01
                    and norm > 1e-9
                    and abs(normal[2]) / norm >= math.cos(math.radians(30))):
                candidates.append((force, point, contact["collision_path"]))
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
    return max(candidates, default=None, key=lambda item: item[0])


def calibrate_foot_support(samples, *, static_collision_paths,
                           minimum_samples=10,
                           calibration_minimum_stance_feet=3,
                           runtime_minimum_stance_feet=1,
                           minimum_normal_force_n=1.0,
                           maximum_floor_spread_m=0.04,
                           maximum_root_drift_m=0.04,
                           maximum_step_m=0.20):
    """Calibrate support from a stationary, zero-command contact window.

    The trailing stable window must expose the same four identified feet. At
    least three feet must carry a finite near-horizontal static contact in every
    admitted sample, and each foot must participate in at least 80 percent of
    that window. Earlier settling samples stay in the raw log but do not define
    the calibration. The result is valid only for the current runtime session.
    """

    if (not isinstance(minimum_samples, int) or isinstance(minimum_samples, bool)
            or minimum_samples < 2 or len(samples) < minimum_samples):
        raise ValueError("insufficient_support_calibration_samples")
    samples = list(samples[-minimum_samples:])
    static_collision_paths = tuple(sorted(static_collision_paths))
    if (not static_collision_paths
            or any(not isinstance(path, str) or not path.startswith("/")
                   for path in static_collision_paths)):
        raise ValueError("invalid_static_collision_paths")
    if calibration_minimum_stance_feet not in (3, 4):
        raise ValueError("invalid_calibration_minimum_stance_feet")
    if runtime_minimum_stance_feet not in (1, 2, 3, 4):
        raise ValueError("invalid_runtime_minimum_stance_feet")
    for value, label in (
            (minimum_normal_force_n, "minimum_normal_force_n"),
            (maximum_floor_spread_m, "maximum_floor_spread_m"),
            (maximum_root_drift_m, "maximum_root_drift_m"),
            (maximum_step_m, "maximum_step_m")):
        if not math.isfinite(float(value)) or float(value) <= 0:
            raise ValueError(f"invalid_{label}")

    expected_names = None
    expected_paths = None
    foot_hits = {}
    offsets = {}
    support_heights = []
    clearances = []
    root_positions = []
    for row in samples:
        contact = row.get("contact") if isinstance(row, dict) else None
        root = _finite(row.get("root_xyz_m"), 3, "calibration root position")
        quaternion = _finite(
            row.get("root_quaternion_wxyz"), 4, "calibration root quaternion")
        qnorm = math.sqrt(sum(value * value for value in quaternion))
        if qnorm < 1e-9:
            raise ValueError("invalid_calibration_root_quaternion")
        _, qx, qy, _ = (value / qnorm for value in quaternion)
        tilt = math.acos(max(-1.0, min(1.0, 1 - 2 * (qx * qx + qy * qy))))
        if tilt > math.radians(20):
            raise ValueError("unstable_support_calibration_pose")
        if not isinstance(contact, dict) or contact.get("status") != "observed":
            raise ValueError("unobserved_support_calibration_contact")
        feet = contact.get("feet")
        if not isinstance(feet, list) or len(feet) != 4:
            raise ValueError("support_calibration_requires_four_feet")
        names = tuple(foot.get("body_name") for foot in feet)
        paths = tuple(foot.get("body_path") for foot in feet)
        if (len(set(names)) != 4 or len(set(paths)) != 4
                or any(not isinstance(path, str) or not path.startswith("/") for path in paths)):
            raise ValueError("unresolved_support_calibration_feet")
        if expected_names is None:
            expected_names, expected_paths = names, paths
            foot_hits = {name: 0 for name in names}
            offsets = {name: [] for name in names}
        elif names != expected_names or paths != expected_paths:
            raise ValueError("support_calibration_foot_identity_changed")

        stance = []
        for foot in feet:
            selected = _stance_contact(
                foot, static_collision_paths=static_collision_paths,
                minimum_normal_force_n=minimum_normal_force_n)
            body_position = _finite(
                foot.get("body_position_world_m"), 3, "calibration foot position")
            offsets[foot["body_name"]].append(
                (body_position[0] - root[0], body_position[1] - root[1]))
            if selected is not None:
                foot_hits[foot["body_name"]] += 1
                stance.append(selected)
        if len(stance) < calibration_minimum_stance_feet:
            raise ValueError("insufficient_supporting_feet_during_calibration")
        heights = [item[1][2] for item in stance]
        if max(heights) - min(heights) > maximum_floor_spread_m:
            raise ValueError("nonplanar_support_calibration_surface")
        support_z = statistics.median(heights)
        support_heights.append(support_z)
        clearances.append(root[2] - support_z)
        root_positions.append(root)

    required_hits = math.ceil(len(samples) * 0.8)
    if any(count < required_hits for count in foot_hits.values()):
        raise ValueError("insufficient_per_foot_contact_persistence")
    if max(support_heights) - min(support_heights) > maximum_floor_spread_m:
        raise ValueError("support_height_drift_during_calibration")
    first_root = root_positions[0]
    if max(math.dist(first_root, root) for root in root_positions) > maximum_root_drift_m:
        raise ValueError("root_drift_during_support_calibration")
    reference_clearance = statistics.median(clearances)
    if not 0.15 <= reference_clearance <= 0.8:
        raise ValueError("invalid_calibrated_reference_clearance")
    if max(clearances) - min(clearances) > maximum_root_drift_m:
        raise ValueError("clearance_drift_during_support_calibration")

    calibrated_offsets = tuple(
        (statistics.median(value[0] for value in offsets[name]),
         statistics.median(value[1] for value in offsets[name]))
        for name in expected_names)
    identity = {
        "schema": FOOT_SUPPORT_CALIBRATION_SCHEMA,
        "body_paths": list(expected_paths),
        "static_collision_paths": list(static_collision_paths),
        "reference_clearance_m": reference_clearance,
        "calibration_minimum_stance_feet": calibration_minimum_stance_feet,
        "runtime_minimum_stance_feet": runtime_minimum_stance_feet,
        "minimum_normal_force_n": float(minimum_normal_force_n),
        "maximum_support_spread_m": float(maximum_step_m + maximum_floor_spread_m),
        "sample_count": len(samples),
        "offsets_xy_m": [list(value) for value in calibrated_offsets],
    }
    calibration_id = hashlib.sha256(json.dumps(
        identity, sort_keys=True, separators=(",", ":"),
        allow_nan=False).encode("utf-8")).hexdigest()
    observation = SupportObservationSpec(
        SUPPORT_OBSERVATION_SCHEMA, "foot_contact", True,
        calibration_id, calibrated_offsets)
    return FootSupportCalibration(
        FOOT_SUPPORT_CALIBRATION_SCHEMA, calibration_id, observation,
        expected_paths, static_collision_paths, reference_clearance,
        calibration_minimum_stance_feet, runtime_minimum_stance_feet,
        float(minimum_normal_force_n),
        float(maximum_step_m + maximum_floor_spread_m), len(samples))


def support_from_calibrated_contacts(contact, calibration):
    """Derive the live support height from calibrated PhysX foot contacts."""

    if not isinstance(calibration, FootSupportCalibration):
        return {"status": "unknown", "reason": "missing_foot_support_calibration"}
    if support_observation_error(calibration.observation) is not None:
        return {"status": "unknown", "reason": "invalid_foot_support_calibration"}
    if not isinstance(contact, dict) or contact.get("status") != "observed":
        return {"status": "unknown", "reason": (
            contact.get("reason", "missing_foot_contact_observation")
            if isinstance(contact, dict) else "missing_foot_contact_observation")}
    feet = contact.get("feet")
    if not isinstance(feet, list) or tuple(
            foot.get("body_path") for foot in feet) != calibration.body_paths:
        return {"status": "unknown", "reason": "foot_contact_identity_changed"}
    stance = []
    for foot in feet:
        selected = _stance_contact(
            foot, static_collision_paths=calibration.static_collision_paths,
            minimum_normal_force_n=calibration.minimum_normal_force_n)
        if selected is not None:
            stance.append((foot["body_name"], *selected))
    if len(stance) < calibration.runtime_minimum_stance_feet:
        return {"status": "unknown", "reason": "insufficient_supporting_feet",
                "stance_feet": [item[0] for item in stance]}
    heights = [item[2][2] for item in stance]
    spread = max(heights) - min(heights)
    if spread > calibration.maximum_support_spread_m:
        return {"status": "unknown", "reason": "support_patch_gap",
                "height_spread_m": spread,
                "allowed_spread_m": calibration.maximum_support_spread_m,
                "stance_feet": [item[0] for item in stance]}
    return {
        "status": "measured",
        "support_z_m": statistics.median(heights),
        "height_min_m": min(heights),
        "height_max_m": max(heights),
        "height_spread_m": spread,
        "stance_feet": [item[0] for item in stance],
        "stance_count": len(stance),
        "progress_eligible": len(stance) >= 2,
        "collision_paths": sorted({item[3] for item in stance}),
        "evidence_kind": "calibrated_foot_contact",
        "calibration_id": calibration.calibration_id,
        "calibrated": True,
    }


def support_terminal_reason(support):
    """Map a support sample to an explicit terminal class.

    ``support_patch_gap`` is kept distinct from generic unknown support so a
    diagnostic can attribute the stop to a multi-level observation window;
    neither class is a posture failure.
    """

    if not isinstance(support, dict):
        return "support_unknown"
    if support.get("status") == "measured":
        return None
    if support.get("status") == "temporarily_unobserved":
        return None
    if support.get("reason") == "support_patch_gap":
        return "support_patch_gap"
    return "support_unknown"


def vertical_support_terminal_reason(route_index, surface_route_index, support):
    """Apply support fail-closed policy only after entering the vertical route."""
    if route_index < surface_route_index:
        return None
    return support_terminal_reason(support)


def zero_body_command():
    """The only command permitted after support evidence becomes unusable."""

    return (0.0, 0.0, 0.0)


def support_stop_decision(support):
    """Return the fail-closed stop command for an unusable support sample."""

    reason = support_terminal_reason(support)
    if reason is None:
        return None
    observed_reason = support.get("reason") if isinstance(support, dict) else None
    return {
        "status": reason,
        "reason": observed_reason or reason,
        "command_body_xyz_m_s": list(zero_body_command()),
        "posture_failure": False,
    }


class FootSupportTracker:
    """Bridge a bounded gait contact gap without inventing support evidence."""

    def __init__(self, calibration, *, maximum_unobserved_s=0.08):
        if not isinstance(calibration, FootSupportCalibration):
            raise ValueError("foot support tracker requires calibration")
        if not math.isfinite(maximum_unobserved_s) or maximum_unobserved_s <= 0:
            raise ValueError("maximum_unobserved_s must be positive")
        self.calibration = calibration
        self.maximum_unobserved_s = float(maximum_unobserved_s)
        self.unobserved_s = 0.0
        self.last_measured = None
        self.current_support = {
            "status": "unknown", "reason": "no_post_calibration_contact"}

    def observe(self, contact, *, dt_s):
        if not math.isfinite(dt_s) or dt_s < 0:
            raise ValueError("support tracker dt_s must be nonnegative")
        support = support_from_calibrated_contacts(contact, self.calibration)
        if support.get("status") == "measured":
            self.unobserved_s = 0.0
            self.last_measured = dict(support)
            self.current_support = support
            return support
        if (support.get("reason") == "insufficient_supporting_feet"
                and self.last_measured is not None):
            self.unobserved_s += dt_s
            if self.unobserved_s <= self.maximum_unobserved_s + 1e-12:
                self.current_support = {
                    "status": "temporarily_unobserved",
                    "reason": "gait_contact_gap",
                    "unobserved_s": self.unobserved_s,
                    "maximum_unobserved_s": self.maximum_unobserved_s,
                    "previous_support_z_m": self.last_measured["support_z_m"],
                    "support_z_m": None,
                    "stance_feet": support.get("stance_feet", []),
                    "evidence_kind": "bounded_contact_gap_no_height_evidence",
                    "calibration_id": self.calibration.calibration_id,
                    "calibrated": True,
                }
                return self.current_support
            support = dict(support, reason="support_contact_gap_timeout",
                           unobserved_s=self.unobserved_s,
                           maximum_unobserved_s=self.maximum_unobserved_s)
        self.current_support = support
        return support

    def current(self):
        return dict(self.current_support)


MICRO_PROBE_STAGES = {
    # The shipped Go2 RSL policy has a low-speed dead band in this scene.  The
    # micro probes are motion diagnostics, so they must use the same command
    # floor as the reproducible navigation baseline; a 0.10 m/s cap can leave
    # the robot standing while the command stream still looks valid.
    "pre_edge": {
        "goal_xy_m": (1.5, -3.00),
        "speed_cap_m_s": 0.55,
        "max_steps": 900,
        "purpose": "entry stability observation before the first tread",
    },
    "first_tread": {
        "goal_xy_m": (1.5, -3.33),
        "speed_cap_m_s": 0.55,
        "max_steps": 900,
        "purpose": "independent first-tread support observation",
    },
    "second_tread": {
        "goal_xy_m": (1.5, -3.73),
        "speed_cap_m_s": 0.55,
        "max_steps": 1100,
        "purpose": "independent second-tread support observation",
    },
}


def micro_probe_record(stage):
    """Return a JSON-safe, observation-only micro-probe declaration."""

    if stage == "full":
        return {"schema": MICRO_PROBE_LOG_SCHEMA, "stage": "full",
                "independent": False, "execution_validated": False,
                "success_claim_allowed": True}
    if stage not in MICRO_PROBE_STAGES:
        raise ValueError(f"unknown micro-probe stage: {stage}")
    spec = MICRO_PROBE_STAGES[stage]
    return {
        "schema": MICRO_PROBE_LOG_SCHEMA,
        "stage": stage,
        "independent": True,
        "goal_xy_m": list(spec["goal_xy_m"]),
        "speed_cap_m_s": spec["speed_cap_m_s"],
        "max_steps": spec["max_steps"],
        "purpose": spec["purpose"],
        "execution_validated": False,
        "success_claim_allowed": False,
    }


def control_step_timestamp(control_step, step_dt):
    """Return the post-step time for one environment/control step.

    ``physics_dt`` belongs to PhysX impulse-to-force conversion. A sampler
    record is emitted after ``env.step`` and is indexed by ``step_dt``.
    """

    if (not isinstance(control_step, int) or isinstance(control_step, bool)
            or control_step <= 0 or not math.isfinite(float(step_dt)) or step_dt <= 0):
        raise ValueError("control step and step_dt must be positive")
    return float(control_step) * float(step_dt)


def resolve_probe_budget(requested_steps, calibration_steps, stage_limit):
    """Resolve a total budget without silently shortening calibration."""

    values = (requested_steps, calibration_steps, stage_limit)
    if any(not isinstance(value, int) or isinstance(value, bool) or value < 0
           for value in values):
        raise ValueError("probe budgets must be nonnegative integers")
    if requested_steps < 1 or stage_limit < 1:
        raise ValueError("probe budget must contain at least one step")
    effective_steps = min(requested_steps, stage_limit)
    if calibration_steps >= effective_steps:
        raise ValueError("calibration budget exceeds effective probe budget")
    return {"requested_steps": requested_steps, "stage_limit": stage_limit,
            "effective_steps": effective_steps,
            "calibration_steps": calibration_steps,
            "execution_steps": effective_steps - calibration_steps}


def step_outcome_reason(outcome, robot_id):
    """Classify a DirectMARLEnv step result, failing closed if malformed."""

    if not isinstance(outcome, (tuple, list)) or len(outcome) < 4:
        return "invalid_step_result"
    for index, reason in ((2, "env_terminated"), (3, "env_truncated")):
        flags = outcome[index]
        if not isinstance(flags, dict) or robot_id not in flags:
            return "invalid_step_result"
        value = flags[robot_id]
        try:
            if hasattr(value, "any"):
                any_value = value.any()
            elif isinstance(value, (list, tuple)):
                if len(value) != 1:
                    raise ValueError("step flag must be scalar")
                any_value = value[0]
            else:
                any_value = value
            if hasattr(any_value, "item"):
                any_value = any_value.item()
            if (not isinstance(any_value, bool)
                    and (not isinstance(any_value, int) or any_value not in (0, 1))):
                raise ValueError("step flag must be boolean")
            if bool(any_value):
                return reason
        except (TypeError, ValueError, RuntimeError):
            return "invalid_step_result"
    return None


def _finite(values, n, label):
    result = tuple(float(value) for value in values)
    if len(result) != n or not all(math.isfinite(value) for value in result):
        raise ValueError(f"{label} must contain {n} finite values")
    return result


def _under(path, root):
    return path == root or path.startswith(root.rstrip("/") + "/")


class StairSupportRaycaster:
    """Static geometry diagnostic; ray intersections are not foot contacts."""

    def __init__(self, query, stage, robot_roots, *, maximum_step_m):
        from pxr import UsdPhysics
        self.query, self.stage = query, stage
        self.robot_roots = tuple(robot_roots)
        self.maximum_step_m = float(maximum_step_m)
        self.UsdPhysics = UsdPhysics

    def _classify(self, hit):
        prim = self.stage.GetPrimAtPath(hit["collision"])
        if not prim.IsValid() or not prim.HasAPI(self.UsdPhysics.CollisionAPI):
            return "unknown"
        if self.UsdPhysics.CollisionAPI(prim).GetCollisionEnabledAttr().Get() is False:
            return "disabled"
        while prim.IsValid() and not prim.IsPseudoRoot():
            if prim.HasAPI(self.UsdPhysics.ArticulationRootAPI):
                return "robot"
            if prim.HasAPI(self.UsdPhysics.RigidBodyAPI):
                enabled = self.UsdPhysics.RigidBodyAPI(prim).GetRigidBodyEnabledAttr().Get()
                return "dynamic_or_kinematic" if enabled else "static"
            prim = prim.GetParent()
        return "static"

    def cast(self, origin):
        origin = _finite(origin, 3, "ray origin")
        hits = []

        def report(hit):
            if len(hits) >= 256:
                return False
            get = hit.get if isinstance(hit, dict) else lambda key: getattr(hit, key, None)
            try:
                position = _finite(get("position"), 3, "hit position")
                normal = _finite(get("normal"), 3, "hit normal")
                distance = float(get("distance"))
                if not math.isfinite(distance):
                    return True
                hits.append({"collision": str(get("collision") or ""),
                             "rigid_body": str(get("rigid_body") or ""),
                             "position": list(position), "normal": list(normal),
                             "distance": distance})
            except (TypeError, ValueError, OverflowError):
                pass
            return True

        try:
            self.query.raycast_all(origin, (0.0, 0.0, -1.0), 2.5, report)
        except Exception as exc:
            return {"status": "unknown", "reason": f"raycast_error:{type(exc).__name__}:{exc}"}
        candidates = []
        for hit in hits:
            paths = [hit["collision"], hit["rigid_body"]]
            if not all(path.startswith("/") for path in paths if path):
                return {"status": "unknown", "reason": "unidentified_hit", "hits": hits}
            if any(_under(path, root) for path in paths if path
                   for root in self.robot_roots):
                continue
            kind = self._classify(hit)
            if kind != "static":
                continue
            if hit["distance"] < 0 or hit["distance"] > 2.5:
                continue
            normal = hit["normal"]
            norm = math.sqrt(sum(value * value for value in normal))
            if norm < 1e-9 or normal[2] / norm < math.cos(math.radians(65)):
                continue
            expected_z = origin[2] - hit["distance"]
            if abs(hit["position"][2] - expected_z) > 0.01:
                return {"status": "unknown", "reason": "inconsistent_hit", "hits": hits}
            candidates.append(hit)
        if not candidates:
            return {"status": "unknown", "reason": "no_static_support", "hits": hits}
        nearest = min(candidates, key=lambda item: item["distance"])
        return {"status": "measured", "support_z_m": nearest["position"][2],
                "collision": nearest["collision"], "normal": nearest["normal"],
                "distance": nearest["distance"]}

    def patch(self, root_xyz, observation):
        error = support_observation_error(observation)
        if error is not None:
            return {"status": "unknown", "reason": error,
                    "observation": (observation.as_record()
                                     if isinstance(observation, SupportObservationSpec)
                                     else None)}
        offsets = list(observation.offsets_xy_m)
        rays = [self.cast((root_xyz[0] + dx, root_xyz[1] + dy, root_xyz[2]))
                for dx, dy in offsets]
        if any(ray["status"] != "measured" for ray in rays):
            return {"status": "unknown", "reason": "incomplete_support_patch", "rays": rays}
        heights = [ray["support_z_m"] for ray in rays]
        spread = max(heights) - min(heights)
        allowed_spread = self.maximum_step_m + 0.04
        if spread > allowed_spread:
            return {"status": "unknown", "reason": "support_patch_gap", "height_spread_m": spread,
                    "allowed_spread_m": allowed_spread, "rays": rays}
        return {"status": "unknown", "reason": "raycast_is_not_contact_evidence",
                "geometry_support_z_m": heights[0],
                "height_min_m": min(heights), "height_max_m": max(heights),
                "height_spread_m": spread, "rays": rays}


class FootContactSampler:
    """Audit one live articulation using PhysX's underlying ContactSensor API.

    Construct views after the session's reset; never reset/respawn for sensors.
    Each exact foot has one view with exact static collider filters. The resolved
    identities must match, including order, before any buffer is attributed.
    Samples cover the last physics substep only, not all decimated substeps.
    They intentionally remain uncalibrated even when all four buffers are valid.
    """

    def __init__(self, articulation, robot_type, robot_root, rays, *, physics_dt, step_dt):
        self.articulation = articulation
        self.views = {}
        self.settings = None
        self.reason = None
        self.physics_dt = float(physics_dt)
        self.step_dt = float(step_dt)
        self.last_control_step = 0
        self.capacity = 64
        self.collision_paths = []
        self.body_paths = {}
        try:
            self.indices = foot_body_indices(robot_type, list(articulation.body_names))
            if (not math.isfinite(self.physics_dt) or self.physics_dt <= 0
                    or not math.isfinite(self.step_dt) or self.step_dt <= 0):
                raise ValueError("invalid_physics_dt")
            from pxr import PhysxSchema, Usd
            from isaacsim.core.simulation_manager import SimulationManager
            import carb

            link_paths = articulation.root_physx_view.link_paths
            if len(link_paths) != 1:
                raise ValueError("contact_sampler_requires_single_articulation")
            for name, index in self.indices.items():
                path = str(link_paths[0][index])
                prim = rays.stage.GetPrimAtPath(path)
                if (not _under(path, robot_root) or prim.GetName() != name
                        or not prim.HasAPI(rays.UsdPhysics.RigidBodyAPI)
                        or not prim.HasAPI(PhysxSchema.PhysxContactReportAPI)):
                    raise ValueError("unverified_foot_contact_body")
                self.body_paths[name] = path
            for prim in Usd.PrimRange.Stage(rays.stage, Usd.TraverseInstanceProxies()):
                path = str(prim.GetPath())
                if (not any(_under(path, root) for root in rays.robot_roots)
                        and prim.HasAPI(rays.UsdPhysics.CollisionAPI)
                        and rays._classify({"collision": path}) == "static"):
                    self.collision_paths.append(path)
            self.collision_paths.sort()
            if not self.collision_paths:
                raise ValueError("no_resolved_static_contact_filters")
            self.settings = carb.settings.get_settings()
            self.previous_disable_processing = self.settings.get_as_bool("/physics/disableContactProcessing")
            # Same setting as ContactSensor.__init__; restore on session exit.
            self.settings.set_bool("/physics/disableContactProcessing", False)
            physics_view = SimulationManager.get_physics_sim_view()
            for name, path in self.body_paths.items():
                view = physics_view.create_rigid_contact_view(
                    path, filter_patterns=self.collision_paths,
                    max_contact_data_count=self.capacity)
                filters = list(view.filter_paths)
                # PhysX versions expose either flat or per-sensor filter paths.
                if len(filters) == 1 and isinstance(filters[0], (list, tuple)):
                    filters = list(filters[0])
                if (view.sensor_count != 1 or list(view.sensor_paths) != [path]
                        or view.filter_count != len(self.collision_paths)
                        or filters != self.collision_paths
                        or view.max_contact_data_count != self.capacity):
                    raise ValueError("unresolved_contact_view_identity")
                self.views[name] = view
        except Exception as exc:
            self.reason = f"contact_setup_error:{type(exc).__name__}:{exc}"

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.views.clear()
        if self.settings is not None:
            self.settings.set_bool("/physics/disableContactProcessing", self.previous_disable_processing)

    def as_record(self):
        return {"schema": "factory_foot_contact_raw_v1", "ready": self.reason is None,
                "reason": self.reason, "body_paths": self.body_paths,
                "static_collision_filters": self.collision_paths,
                "physics_dt_s": self.physics_dt, "step_dt_s": self.step_dt,
                "buffer_capacity_per_foot": self.capacity,
                "sampling": "last_physics_substep_of_control_step",
                "sample_phase": "after_env_step",
                "calibrated": False, "execution_validated": False}

    def sample(self, *, control_step, simulation_time_s):
        record = {"schema": "factory_foot_contact_raw_v1", "status": "unknown",
                  "control_step": control_step, "simulation_time_s": simulation_time_s,
                  "physics_dt_s": self.physics_dt, "step_dt_s": self.step_dt,
                  "feet": [],
                  "calibrated": False, "support_z_m": None,
                  "sampling": "last_physics_substep_of_control_step",
                  "sample_phase": "after_env_step"}
        if self.reason is not None:
            return dict(record, reason=self.reason)
        try:
            expected_time = control_step_timestamp(control_step, self.step_dt)
        except ValueError:
            return dict(record, reason="invalid_control_timestamp")
        if (not math.isfinite(simulation_time_s)
                or abs(float(simulation_time_s) - expected_time) > 1e-9 * max(1.0, expected_time)
                or control_step <= self.last_control_step):
            return dict(record, reason="invalid_control_timestamp")
        self.last_control_step = control_step
        try:
            positions = self.articulation.data.body_pos_w[0].detach().cpu().tolist()
            for name, index in self.indices.items():
                view = self.views[name]
                # Impulse-to-force conversion uses physics_dt, NEVER step_dt.
                raw = [value.detach().cpu().tolist()
                       for value in view.get_contact_data(dt=self.physics_dt)]
                decoded = decode_foot_contact_buffer(
                    raw, collision_paths=self.collision_paths, capacity=self.capacity)
                net = _finite(view.get_net_contact_forces(dt=self.physics_dt)[0]
                              .detach().cpu().tolist(), 3, "net contact force")
                matrix = view.get_contact_force_matrix(dt=self.physics_dt)[0].detach().cpu().tolist()
                if len(matrix) != len(self.collision_paths):
                    raise ValueError("contact_force_matrix_shape_mismatch")
                matrix = [_finite(row, 3, "pair contact force") for row in matrix]
                filtered = [sum(row[axis] for row in matrix) for axis in range(3)]
                decoded.update({"body_name": name, "body_path": self.body_paths[name],
                                "body_position_world_m": list(_finite(positions[index], 3, "foot position")),
                                "net_contact_force_N": list(net),
                                "filtered_contact_force_N": filtered,
                                "unattributed_force_residual_N": [a - b for a, b in zip(net, filtered)],
                                "pair_forces_N": {path: list(force) for path, force in
                                    zip(self.collision_paths, matrix) if any(force)}})
                record["feet"].append(decoded)
            if any(foot["status"] != "observed" for foot in record["feet"]):
                return dict(record, reason="incomplete_foot_contact_buffers")
            return dict(record, status="observed")
        except Exception as exc:
            return dict(record, reason=f"contact_sample_error:{type(exc).__name__}:{exc}")


def joint_state_record(articulation):
    """Record finite joint measurements and commanded targets for calibration."""
    try:
        names = list(articulation.joint_names)
        result = {"status": "observed", "names": names}
        for field in ("joint_pos", "joint_vel", "joint_pos_target", "joint_vel_target"):
            values = getattr(articulation.data, field)[0].detach().cpu().tolist()
            result[field] = list(_finite(values, len(names), field))
        return result
    except Exception as exc:
        return {"status": "unknown", "reason": f"joint_sample_error:{type(exc).__name__}:{exc}"}


def bounded_command(position, quaternion, goal_xy, *, speed=0.10, dt=0.05):
    position = _finite(position, 3, "position")
    q = _finite(quaternion, 4, "quaternion")
    norm = math.sqrt(sum(value * value for value in q))
    if norm < 1e-9:
        raise ValueError("zero quaternion")
    w, qx, qy, qz = (value / norm for value in q)
    yaw = math.atan2(2 * (w * qz + qx * qy), 1 - 2 * (qy * qy + qz * qz))
    dx, dy = float(goal_xy[0]) - position[0], float(goal_xy[1]) - position[1]
    distance = math.hypot(dx, dy)
    if distance <= 0.12:
        return (0.0, 0.0, 0.0)
    scale = min(float(speed), distance / dt) / distance
    heading = math.atan2(dy, dx)
    error = (heading - yaw + math.pi) % (2 * math.pi) - math.pi
    return (scale * (math.cos(yaw) * dx + math.sin(yaw) * dy),
            scale * (-math.sin(yaw) * dx + math.cos(yaw) * dy),
            max(-0.6, min(0.6, 1.5 * error)))


def configure_vertical(env_cfg):
    env_cfg.sim.enable_scene_query_support = True
    env_cfg.episode_length_s = 3600.0


def file_hash(path):
    path = Path(path)
    return {"path": str(path.resolve()), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def _ground_route_to(scenario, occupancy, template, goal_xy):
    """Plan a ground-only approach while retaining the Factory stair exclusion."""
    from demo.benchmark.runtime import build_plan

    ground = copy.deepcopy(scenario)
    ground["benchmark_task_id"] = "F-INS-01"
    ground["composition"]["targets"] = [{
        "id": "__vertical_ground_approach__",
        "position": [float(goal_xy[0]), float(goal_xy[1]), 0.0],
        "radius_m": 0.35,
    }]
    node = copy.deepcopy(ground["phase_graph"]["nodes"][0])
    node["target_ref"] = "__vertical_ground_approach__"
    node.pop("vertical", None)
    ground["phase_graph"]["nodes"] = [node]
    # The stair envelope remains an explicit 3-D support objective. Do not
    # apply the ordinary ground tracking reserve to its 2-D exclusion while
    # routing to the pre-entry stance: for a wider robot that reserve can
    # consume the last valid stance cell before the stair, even though the
    # stair itself is wide enough. The surface segment is still admitted only
    # by the calibrated contact protocol below.
    return [tuple(point) for point in build_plan(
        ground, occupancy, template, ground_tracking_reserve_m=0.0
    )[0]["waypoints"]]


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--max-steps", type=int, default=3000)
    parser.add_argument("--calibration-steps", type=int, default=40,
                        help="Fixed-budget zero-command contact samples before route execution")
    parser.add_argument("--wall-timeout-s", type=float, default=180.0)
    parser.add_argument("--speed", type=float, default=0.55,
                        help="Capped body-frame planar command in m/s; matches the navigation baseline")
    parser.add_argument("--micro-probe-stage", choices=("full", *MICRO_PROBE_STAGES),
                        default="full",
                        help="Run one independent observation-only entry/first/second-tread probe")
    return parser


def run(args):
    from EAI.scenario_spec import load_scenario
    from EAI.scenario_spec.schema import ROBOT_PROFILES
    from EAI.scenario_spec.templates import FACTORY_STAIRS
    from simulator import SimulatorLaunchConfig, open_simulator_session

    scenario = load_scenario(args.scenario)
    if not math.isfinite(args.speed) or args.speed <= 0 or args.speed > 1.0:
        raise ValueError("--speed must be finite and in (0, 1.0]")
    if args.max_steps < 1 or args.calibration_steps < 0:
        raise ValueError("--max-steps must be positive and --calibration-steps nonnegative")
    if 0 < args.calibration_steps < 10:
        raise ValueError("--calibration-steps must be zero or at least 10")
    if args.calibration_steps >= args.max_steps:
        raise ValueError("--max-steps must exceed --calibration-steps")
    nodes = [node for node in scenario["phase_graph"]["nodes"] if node.get("vertical")]
    if scenario.get("scene_key") != "factory" or scenario.get("benchmark_task_id") != "F-VERT-01":
        raise ValueError("vertical_task requires Factory F-VERT-01")
    if len(nodes) != 1 or nodes[0]["vertical"]["kind"] != "stair":
        raise ValueError("vertical_task requires exactly one explicit stair objective")
    node = nodes[0]
    link = next((item for item in FACTORY_STAIRS if item.id == node["vertical"]["link_id"]), None)
    if link is None or link.kind != "stair":
        raise ValueError("scenario stair link is not a registered Factory stair")
    robot = next(item for item in scenario["robots"] if item["id"] == node["preferred_agent"])
    robot_profile = ROBOT_PROFILES[robot["type"]]
    if robot_profile["mobility"] not in {"quadruped", "biped"}:
        raise ValueError("stair task requires legged mobility")
    target = next(item for item in scenario["composition"]["targets"] if item["id"] == node["target_ref"])
    micro_probe = micro_probe_record(args.micro_probe_stage)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        def emit(record):
            stream.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
            stream.flush()

        emit({"type": "manifest", "protocol": "factory_stair_surface_following_v1",
              "scenario": scenario, "scenario_file": file_hash(args.scenario),
              "link": {"id": link.id, "kind": link.kind, "aabb_xy_m": list(link.aabb),
                       "declared_z_bounds_m": [link.low_z, link.high_z]},
              "navigation": {"footprint_radius_m": float(robot_profile["radius_m"]),
                              "semantic": "ground-route-admission-only"},
              "support_observation": {
                  "schema": SUPPORT_OBSERVATION_SCHEMA,
                  "requirement": "calibrated_foot_or_contact_evidence",
                  "available_in_this_executor": True,
                  "calibrated_in_this_executor": False,
                  "zero_command_calibration_supported": True,
                  "runtime_calibration_configured": args.calibration_steps > 0,
                  "fail_closed": True,
              },
              "micro_probe": micro_probe,
              "execution_validated": False,
              "timing": {"control_step_dt_s": None,
                         "physics_dt_s": None,
                         "contact_force_conversion": "physics_dt_s",
                         "pose_and_event_clock": "control_step_dt_s"},
              "limitations": ["bounded velocity baseline", "no manipulation", "no global 3-D planner"]})
        from EAI.scenario_spec.geometry import provider_map, validate_connectivity, validate_geometry
        from EAI.scenario_spec.templates import factory_template
        from demo.benchmark.runtime import build_plan
        occupancy = provider_map(scene_key="factory")
        validate_geometry(scenario, occupancy)
        validate_connectivity(scenario, occupancy)
        # Use the same reproducible inflated-grid route as the benchmark
        # runtime.  A hand-written single approach point can terminate on the
        # wrong side of a wall or at the stair envelope without ever reaching
        # the measured surface.  The vertical executor owns only the final
        # surface-following evidence; route admission remains shared.
        template = factory_template(occupancy)
        planned = build_plan(scenario, occupancy, template)
        if len(planned) != 1 or planned[0]["node"] != node["id"]:
            raise ValueError("vertical task did not produce exactly one matching route")
        full_route = [tuple(point) for point in planned[0]["waypoints"]]
        if not full_route:
            raise ValueError("vertical task route is empty")
        approach_points = [tuple(point) for point in node["vertical"].get("approach_waypoints", ())]
        if not approach_points:
            # The measured main-stair entry is the north landing.  Keeping a
            # ground approach point outside the stair envelope prevents the
            # 2-D planner from cutting through the folded lower flight.
            approach_points = [(1.5, -0.35)]
        for point in approach_points:
            if not all(math.isfinite(value) for value in point):
                raise ValueError("vertical approach waypoint must be finite")
        ground_route = []
        previous = None
        for point in approach_points:
            segment = _ground_route_to(scenario, occupancy, template, point)
            if previous is not None and segment and math.dist(segment[0], previous) <= 1e-6:
                segment = segment[1:]
            ground_route.extend(segment)
            previous = point
        if not ground_route:
            raise ValueError("vertical ground approach route is empty")
        # The landing is measured at z≈0.06 and is reached from the north;
        # only after this point may the executor accumulate vertical progress.
        surface_entry = (1.5, -2.25)
        if args.micro_probe_stage == "full":
            probe_goal_xy = tuple(target["position"][:2])
            probe_speed = args.speed
            probe_max_steps = args.max_steps
        else:
            stage_spec = MICRO_PROBE_STAGES[args.micro_probe_stage]
            probe_goal_xy = tuple(stage_spec["goal_xy_m"])
            probe_speed = min(args.speed, stage_spec["speed_cap_m_s"])
        budget = resolve_probe_budget(
            args.max_steps, args.calibration_steps,
            args.max_steps if args.micro_probe_stage == "full" else stage_spec["max_steps"])
        probe_max_steps = budget["effective_steps"]
        planned_route = ground_route + [surface_entry, probe_goal_xy]
        config = SimulatorLaunchConfig(
            env=scenario["task_name"], scenario_file=str(args.scenario), seed=scenario["seed"],
            device=args.device, headless=args.headless, enable_ros_bridge_extension=False,
            env_cfg_hook=configure_vertical)
        started = time.monotonic()
        with open_simulator_session(config) as session, ExitStack() as probe_stack:
            import omni.usd
            import torch
            from omni.physx import get_physx_scene_query_interface
            from pxr import UsdPhysics
            if session.num_envs != 1:
                raise ValueError("vertical task requires one environment")
            env = session.env
            base_env = session.base_env
            articulation = base_env.scene.articulations[robot["id"]]
            stage = omni.usd.get_context().get_stage()
            roots = [f"/World/envs/env_0/{item['id']}" for item in scenario["robots"]]
            if not all(stage.GetPrimAtPath(path).IsValid() for path in roots):
                raise ValueError("cannot resolve exact robot roots for support exclusion")
            rays = StairSupportRaycaster(get_physx_scene_query_interface(), stage, roots,
                                         maximum_step_m=0.20)
            contact_sampler = probe_stack.enter_context(FootContactSampler(
                articulation, robot["type"], roots[scenario["robots"].index(robot)],
                rays, physics_dt=float(base_env.physics_dt), step_dt=float(base_env.step_dt)))
            emit({"type": "support_sampler_setup", "sampler": contact_sampler.as_record(),
                  "calibration_steps": args.calibration_steps,
                  "timing": {"control_step_dt_s": float(base_env.step_dt),
                              "physics_dt_s": float(base_env.physics_dt),
                              "contact_force_conversion": "physics_dt_s",
                              "pose_and_event_clock": "control_step_dt_s"},
                  "execution_validated": False})
            dt = float(base_env.step_dt)
            navigation_radius_m = float(robot_profile["radius_m"])
            support_observation = SupportObservationSpec.unavailable()
            support_calibration = None
            support_tracker = None
            probe = VerticalProbe(
                link.id, "stair", (link.aabb[0], link.aabb[1], link.low_z),
                (target["position"][0], target["position"][1], target["position"][2]),
                2.0, math.radians(60), 0.20)
            state, reference_clearance = {}, None
            route = []
            for point in planned_route:
                if not route or math.dist(route[-1], point) > 1e-6:
                    route.append(point)
            target_xy = tuple(probe_goal_xy)
            if math.dist(route[-1], target_xy) > 1e-9:
                route.append(target_xy)
            # Vertical traversal begins after the surface-entry waypoint has
            # been reached and the final stair goal becomes active.
            surface_route_index = len(route) - 1
            emit({"type": "route_admission", "route": [list(point) for point in route],
                  "ground_waypoint_count": surface_route_index,
                  "surface_entry_xy_m": list(surface_entry),
                  "target_xy_m": list(target_xy), "speed_cap_m_s": probe_speed,
                  "navigation_footprint_radius_m": navigation_radius_m,
                  "support_observation": support_observation.as_record(),
              "micro_probe": micro_probe,
              "budget": budget,
                  "planner": "shared_inflated_grid_plus_declared_surface_entry",
                  "vertical_progress_enabled_from_route_index": surface_route_index})
            route_index = 0
            result = "step_budget"
            records = 0
            terminal_emitted = False
            calibration_steps = budget["calibration_steps"]
            calibration_health_state = {}
            calibration_rows = []
            last_contact = {"status": "unknown", "reason": "no_post_step_sample"}
            if contact_sampler.reason is not None:
                result = "contact_setup_error"
                emit({"type": "terminal", "status": result, "step": 0,
                      "reason": contact_sampler.reason, "phase": "setup",
                      "execution_validated": False})
                terminal_emitted = True
            for control_step in range(1, calibration_steps + 1):
                if terminal_emitted:
                    break
                if time.monotonic() - started > args.wall_timeout_s:
                    result = "wall_timeout"
                    break
                with torch.inference_mode():
                    outcome = env.step({robot["id"]: torch.tensor(
                        [[0.0, 0.0, 0.0]], dtype=torch.float32, device=session.device)})
                done_reason = step_outcome_reason(outcome, robot["id"])
                contact = contact_sampler.sample(
                    control_step=control_step,
                    simulation_time_s=control_step_timestamp(control_step, dt))
                last_contact = contact
                joints = (joint_state_record(articulation) if done_reason is None else
                          {"status": "unavailable_after_env_step_termination",
                           "reason": done_reason})
                emit({"type": "calibration_sample", "step": control_step,
                      "simulation_time_s": control_step_timestamp(control_step, dt),
                      "command_body_xyz_m_s": [0.0, 0.0, 0.0],
                      "contact": contact, "joint_state": joints,
                      "step_outcome": done_reason,
                      "post_step_state_available": done_reason is None,
                      "execution_validated": False})
                if done_reason is not None:
                    result = done_reason
                    emit({"type": "terminal", "status": result, "step": control_step,
                          "reason": result, "phase": "calibration",
                          "execution_validated": False})
                    terminal_emitted = True
                    break
                raw = articulation.data.root_state_w[0].detach().cpu().tolist()
                if len(raw) != 13 or not all(math.isfinite(float(value)) for value in raw):
                    result = "invalid_root_state"
                    emit({"type": "terminal", "step": control_step, "reason": result,
                          "phase": "calibration", "execution_validated": False})
                    terminal_emitted = True
                    break
                quaternion = [float(value) for value in raw[3:7]]
                if math.sqrt(sum(value * value for value in quaternion)) < 1e-9:
                    result = "invalid_root_quaternion"
                    emit({"type": "terminal", "step": control_step, "reason": result,
                          "phase": "calibration", "execution_validated": False})
                    terminal_emitted = True
                    break
                calibration_health = advance_ground_health(
                    calibration_health_state, root_z_m=raw[2], quaternion_wxyz=raw[3:7],
                    dt_s=dt, reference_clearance_m=.32, support_z_m=None,
                    maximum_tilt_rad=math.radians(60), dwell_s=.5)
                if calibration_health["failed"]:
                    result = "ground_health_failed"
                    emit({"type": "terminal", "step": control_step, "reason": result,
                          "phase": "calibration", "health": calibration_health,
                          "execution_validated": False})
                    terminal_emitted = True
                    break
                if contact["status"] != "observed":
                    result = "contact_calibration_unavailable"
                    emit({"type": "terminal", "status": result, "step": control_step,
                          "reason": contact.get("reason", result),
                          "phase": "calibration", "contact": contact,
                          "execution_validated": False})
                    terminal_emitted = True
                    break
                if joints["status"] != "observed":
                    result = "joint_calibration_unavailable"
                    emit({"type": "terminal", "status": result, "step": control_step,
                          "reason": joints.get("reason", result),
                          "phase": "calibration", "joint_state": joints,
                          "execution_validated": False})
                    terminal_emitted = True
                    break
                calibration_rows.append({
                    "root_xyz_m": [float(value) for value in raw[:3]],
                    "root_quaternion_wxyz": [float(value) for value in raw[3:7]],
                    "contact": contact,
                })
            if calibration_steps and not terminal_emitted:
                try:
                    support_calibration = calibrate_foot_support(
                        calibration_rows,
                        static_collision_paths=contact_sampler.collision_paths,
                        minimum_samples=10,
                        maximum_step_m=probe.maximum_step_m)
                    support_observation = support_calibration.observation
                    reference_clearance = support_calibration.reference_clearance_m
                    support_tracker = FootSupportTracker(support_calibration)
                    support_tracker.observe(last_contact, dt_s=0.0)
                    emit({"type": "support_calibration",
                          "status": "calibrated",
                          "calibration": support_calibration.as_record(),
                          "execution_validated": False})
                except (TypeError, ValueError, OverflowError) as exc:
                    result = "contact_calibration_rejected"
                    emit({"type": "terminal", "status": result,
                          "reason": f"{type(exc).__name__}:{exc}",
                          "phase": "calibration",
                          "execution_validated": False})
                    terminal_emitted = True
            elif not terminal_emitted:
                emit({"type": "support_calibration", "status": "unavailable",
                      "reason": "zero_calibration_budget",
                      "support_observation": support_observation.as_record(),
                      "execution_validated": False})
            if result == "wall_timeout" and not terminal_emitted:
                emit({"type": "terminal", "status": result,
                      "reason": result, "phase": "calibration",
                      "execution_validated": False,
                      "wall_time_s": time.monotonic() - started})
                terminal_emitted = True
            for control_step in range(calibration_steps + 1, probe_max_steps + 1):
                if terminal_emitted:
                    break
                if time.monotonic() - started > args.wall_timeout_s:
                    result = "wall_timeout"
                    break
                raw = articulation.data.root_state_w[0].detach().cpu().tolist()
                if len(raw) != 13 or not all(math.isfinite(float(value)) for value in raw):
                    result = "invalid_root_state"
                    emit({"type": "terminal", "step": control_step, "reason": result})
                    terminal_emitted = True
                    break
                xyz, quaternion = raw[:3], raw[3:7]
                current_goal = route[route_index]
                if (math.dist(xyz[:2], current_goal) <= 0.15
                        and route_index + 1 < len(route)):
                    route_index += 1
                    current_goal = route[route_index]
                    emit({"type": "route_waypoint_reached", "step": control_step,
                          "route_index": route_index, "goal_xy_m": list(current_goal)})
                vertical_active = route_index >= surface_route_index
                support = (support_tracker.current()
                    if support_tracker is not None
                    else rays.patch(xyz, support_observation))
                support_stop = (support_stop_decision(support)
                                if vertical_support_terminal_reason(
                                    route_index, surface_route_index, support) is not None
                                else None)
                if support_stop is not None:
                    # This can only be reached before a command if the first
                    # state is already at the vertical route. A prior post-step
                    # sample normally catches this boundary immediately.
                    result = support_stop["status"]
                    emit({"type": "sample", "step": control_step,
                          "simulation_time_s": control_step_timestamp(control_step, dt),
                          "root_xyz_m": xyz,
                          "root_quaternion_wxyz": quaternion, "support": support,
                          "contact": last_contact, "route_index": route_index,
                          "goal_xy_m": list(current_goal),
                          "command_body_xyz_m_s": list(zero_body_command()),
                          "command_kind": "support_failure_stop"})
                    records += 1
                    with torch.inference_mode():
                        stop_outcome = env.step({robot["id"]: torch.tensor(
                            [list(zero_body_command())], dtype=torch.float32,
                            device=session.device)})
                    emit({"type": "terminal", "status": result, "step": control_step,
                          "reason": result, "support_reason": support_stop["reason"],
                          "zero_command": list(zero_body_command()), "records": records,
                          "step_outcome": step_outcome_reason(stop_outcome, robot["id"]),
                          "execution_validated": False, "posture_failure": False,
                          "wall_time_s": time.monotonic() - started})
                    terminal_emitted = True
                    break
                command = bounded_command(xyz, quaternion, current_goal,
                                          speed=probe_speed, dt=dt)
                with torch.inference_mode():
                    outcome = env.step({robot["id"]: torch.tensor([command], dtype=torch.float32, device=session.device)})
                contact = contact_sampler.sample(
                    control_step=control_step,
                    simulation_time_s=control_step_timestamp(control_step, dt))
                done_reason = step_outcome_reason(outcome, robot["id"])
                if done_reason is not None:
                    result = done_reason
                    emit({"type": "terminal", "status": result, "step": control_step,
                          "reason": result, "phase": "execution",
                          "contact": contact, "command_body_xyz_m_s": list(command),
                          "contact_phase": "after_env_step",
                          "execution_validated": False})
                    terminal_emitted = True
                    break
                raw = articulation.data.root_state_w[0].detach().cpu().tolist()
                if len(raw) != 13 or not all(math.isfinite(float(value)) for value in raw):
                    result = "invalid_root_state"
                    emit({"type": "terminal", "step": control_step, "reason": result,
                          "phase": "post_step", "contact": contact,
                          "execution_validated": False})
                    terminal_emitted = True
                    break
                xyz, quaternion = raw[:3], raw[3:7]
                last_contact = contact
                current_goal = route[route_index]
                if (math.dist(xyz[:2], current_goal) <= 0.15
                        and route_index + 1 < len(route)):
                    route_index += 1
                    current_goal = route[route_index]
                    emit({"type": "route_waypoint_reached", "step": control_step,
                          "route_index": route_index, "goal_xy_m": list(current_goal)})
                vertical_active = route_index >= surface_route_index
                support = (support_tracker.observe(contact, dt_s=dt)
                    if support_tracker is not None
                    else rays.patch(xyz, support_observation))
                support_stop = (support_stop_decision(support)
                                if vertical_support_terminal_reason(
                                    route_index, surface_route_index, support) is not None
                                else None)
                if reference_clearance is None and support["status"] == "measured":
                    reference_clearance = xyz[2] - support["support_z_m"]
                    if not 0.15 <= reference_clearance <= 0.8:
                        result = "invalid_reference_clearance"
                        emit({"type": "terminal", "step": control_step, "support": support,
                              "contact": contact, "reason": result,
                              "execution_validated": False})
                        terminal_emitted = True
                        break
                health = advance_ground_health(
                    state, root_z_m=xyz[2], quaternion_wxyz=quaternion, dt_s=dt,
                    reference_clearance_m=reference_clearance or 0.32,
                    support_z_m=support.get("support_z_m") if reference_clearance is not None and support["status"] == "measured" else None,
                    maximum_tilt_rad=math.radians(60), dwell_s=0.5)
                progress_health = (health if vertical_active
                                   and support.get("progress_eligible") is True else {
                    "status": "support_unknown", "failed": False,
                    "height_observed": False, "support_z_m": None})
                progress = advance_vertical_progress(
                    state, probe,
                    root_xyz=xyz, reference_clearance_m=reference_clearance or 0.32,
                    health=progress_health, dt_s=dt)
                emit({"type": "sample", "step": control_step,
                      "simulation_time_s": control_step_timestamp(control_step, dt),
                      "root_xyz_m": xyz,
                      "root_quaternion_wxyz": quaternion, "support": support,
                      "contact": contact, "health": health, "progress": progress,
                      "route_index": route_index, "goal_xy_m": list(current_goal),
                      "command_body_xyz_m_s": list(command),
                      "command_kind": "regular"})
                records += 1
                if support_stop is not None:
                    result = support_stop["status"]
                    with torch.inference_mode():
                        stop_outcome = env.step({robot["id"]: torch.tensor(
                            [list(zero_body_command())], dtype=torch.float32,
                            device=session.device)})
                    emit({"type": "terminal", "status": result, "step": control_step,
                          "reason": result, "support_reason": support_stop["reason"],
                          "zero_command": list(zero_body_command()),
                          "step_outcome": step_outcome_reason(stop_outcome, robot["id"]),
                          "records": records, "execution_validated": False,
                          "posture_failure": False,
                          "wall_time_s": time.monotonic() - started})
                    terminal_emitted = True
                    break
                if health["failed"]:
                    result = "ground_health_failed"
                    emit({"type": "terminal", "step": control_step, "reason": result,
                          "health": health, "execution_validated": False})
                    terminal_emitted = True
                    break
                if (args.micro_probe_stage != "full"
                        and route_index == len(route) - 1
                        and math.dist(xyz[:2], current_goal) <= 0.15):
                    result = "micro_probe_boundary_reached"
                    emit({"type": "terminal", "status": result, "step": control_step,
                          "reason": result, "micro_probe": micro_probe,
                          "execution_validated": False,
                          "success_claim_allowed": False})
                    terminal_emitted = True
                    break
                if progress["complete"]:
                    if args.micro_probe_stage != "full":
                        result = "micro_probe_boundary_reached"
                        emit({"type": "terminal", "status": result, "step": control_step,
                              "reason": result, "micro_probe": micro_probe,
                              "execution_validated": False,
                              "success_claim_allowed": False})
                        terminal_emitted = True
                        break
                    result = "success"
                    break
            if not terminal_emitted:
                emit({"type": "terminal", "status": result, "records": records,
                      "execution_validated": result == "success",
                      "wall_time_s": time.monotonic() - started})
            return 0 if result == "success" else 2


def main(argv=None):
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
