"""Pure vertical-probe admission and measured progress; not a locomotion controller.

Morphology admits a *probe*, never establishes a tested climbing capability.
The existing 2-D executor must not use these helpers to bypass its surface gate.
"""

from dataclasses import dataclass
import math


FOOT_CONTACT_BODIES = {
    "go2": ("FL_foot", "FR_foot", "RL_foot", "RR_foot"),
    "b2": ("FL_foot", "FR_foot", "RL_foot", "RR_foot"),
    # Lite3's canonical USD uses uppercase link names and HL/HR hind-leg
    # prefixes. These are articulation body names, not visual prim aliases.
    "lite3": ("FL_FOOT", "FR_FOOT", "HL_FOOT", "HR_FOOT"),
}


def foot_body_indices(robot_type, body_names):
    """Resolve actual articulation bodies, never a navigation footprint.

    M20 wheels and unverified legged assets deliberately have no foot mapping.
    B2's collider prims may share foot names; only articulation body names belong
    here, not all USD prim names.
    """
    if robot_type not in FOOT_CONTACT_BODIES:
        raise ValueError("unsupported_foot_contact_layout")
    expected = FOOT_CONTACT_BODIES[robot_type]
    if any(body_names.count(name) != 1 for name in expected):
        raise ValueError("missing_or_ambiguous_foot_body")
    return {name: body_names.index(name) for name in expected}


def decode_foot_contact_buffer(raw, *, collision_paths, capacity):
    """Decode one body's PhysX six-buffer tuple without importing Isaac/Torch.

    Inputs are host lists and exact, resolved static filter identities. Unused
    buffer slots can contain NaNs; only indexed contacts are evidence. Reaching
    capacity is conservatively unknown since the API provides no overflow bit.
    This validates a raw sample, NOT support, contact persistence or calibration.
    """
    try:
        if (not isinstance(capacity, int) or isinstance(capacity, bool) or capacity <= 0
                or not collision_paths or len(set(collision_paths)) != len(collision_paths)
                or any(not isinstance(path, str) or not path.startswith("/")
                       or any(char in path for char in "*?[]") for path in collision_paths)):
            raise ValueError("unidentified_contact_filter")
        forces, points, normals, separations, counts, starts = raw
        if (any(len(buf) != capacity for buf in (forces, points, normals, separations))
                or len(counts) != 1 or len(starts) != 1
                or len(counts[0]) != len(collision_paths)
                or len(starts[0]) != len(collision_paths)):
            raise ValueError("contact_buffer_shape_mismatch")
        used, contacts = set(), []
        for path, count, start in zip(collision_paths, counts[0], starts[0]):
            if not isinstance(count, int) or isinstance(count, bool) or count < 0:
                raise ValueError("invalid_contact_count")
            # Empty pairs have no defined start index or contact position.
            if count == 0:
                continue
            if (not isinstance(start, int) or isinstance(start, bool) or start < 0
                    or start + count > capacity):
                raise ValueError("invalid_contact_range")
            for index in range(start, start + count):
                if index in used:
                    raise ValueError("overlapping_contact_ranges")
                used.add(index)
                if len(forces[index]) != 1 or len(separations[index]) != 1:
                    raise ValueError("contact_scalar_shape_mismatch")
                force, separation = float(forces[index][0]), float(separations[index][0])
                point = _point(points[index], "contact position")
                normal = _point(normals[index], "contact normal")
                if (not math.isfinite(force) or force < 0 or not math.isfinite(separation)
                        or math.hypot(*normal) < 1e-12):
                    raise ValueError("invalid_contact_values")
                contacts.append({"collision_path": path, "position_world_m": list(point),
                                 "normal_world": list(normal), "normal_force_N": force,
                                 "separation_m": separation})
        if len(used) >= capacity:
            raise ValueError("contact_buffer_capacity_reached")
        return {"status": "observed", "contacts": contacts, "contact_count": len(used),
                "buffer_capacity": capacity, "buffer_saturated": False}
    except (TypeError, ValueError, IndexError, OverflowError) as exc:
        return {"status": "unknown", "reason": str(exc), "contacts": [],
                "buffer_saturated": str(exc) == "contact_buffer_capacity_reached"}


def support_from_foot_observation(observation):
    """Keep raw contact acquisition separate from calibrated support evidence.

    Four reporting feet need not all be in stance during locomotion. Neither one
    contact nor four finite foot positions define a calibrated support plane.
    A future calibration must validate force/normal conventions, contact shape
    identities, gait transitions and physical-step coverage before admission.
    """
    reason = "uncalibrated_support_observation"
    if not isinstance(observation, dict) or observation.get("status") != "observed":
        reason = (observation.get("reason", "missing_foot_contact_observation")
                  if isinstance(observation, dict) else "missing_foot_contact_observation")
    return {"status": "unknown", "reason": reason, "support_z_m": None,
            "evidence_kind": "raw_foot_contacts", "calibrated": False}


def _positive(value, name, *, zero=False):
    if not math.isfinite(value) or (value < 0 if zero else value <= 0):
        raise ValueError(f"{name} must be finite and {'nonnegative' if zero else 'positive'}")
    return value


def _point(value, name):
    if len(value) != 3 or not all(math.isfinite(component) for component in value):
        raise ValueError(f"{name} must contain three finite coordinates")
    return tuple(float(component) for component in value)


@dataclass(frozen=True)
class VerticalProbe:
    """Measured walkable endpoints and narrowest corridor, not USD bbox extrema."""

    link_id: str
    kind: str
    start_support_xyz: tuple[float, float, float]
    goal_support_xyz: tuple[float, float, float]
    clear_width_m: float
    maximum_slope_rad: float  # maximum measured local slope, not average rise/run
    maximum_step_m: float

    def __post_init__(self):
        if not self.link_id or self.kind not in {"stair", "ramp"}:
            raise ValueError("probe requires a named stair or ramp link")
        for name in ("start_support_xyz", "goal_support_xyz"):
            object.__setattr__(self, name, _point(getattr(self, name), name))
        _positive(self.clear_width_m, "clear_width_m")
        _positive(self.maximum_step_m, "maximum_step_m", zero=True)
        _positive(self.maximum_slope_rad, "maximum_slope_rad", zero=True)
        if self.maximum_slope_rad >= math.pi / 2:
            raise ValueError("maximum_slope_rad must be below pi/2")
        if abs(self.goal_support_xyz[2] - self.start_support_xyz[2]) < 1e-6:
            raise ValueError("a vertical probe must change support height")


def admit_vertical_probe(probe, *, mobility, physical_radius_m,
                         clearance_reserve_m=0.0, slope_limit_rad=None,
                         step_limit_m=None):
    """Separate legged stairs from wheeled low ramps using explicit test limits.

    Limits are experiment constraints supplied by the configuration owner. They
    are not inferred from a robot name or from a map label, and are not evidence
    of controller success. No existing configuration is promoted by this check.
    """
    _positive(physical_radius_m, "physical_radius_m")
    _positive(clearance_reserve_m, "clearance_reserve_m", zero=True)
    reasons = []
    if mobility not in {"quadruped", "biped", "wheeled"}:
        reasons.append("unsupported_ground_mobility")
    if probe.kind == "stair" and mobility not in {"quadruped", "biped"}:
        reasons.append("stairs_require_legged_mobility")
    if probe.clear_width_m < 2 * (physical_radius_m + clearance_reserve_m):
        reasons.append("insufficient_clear_width")
    for name, limit, measured in (("slope", slope_limit_rad, probe.maximum_slope_rad),
                                  ("step", step_limit_m, probe.maximum_step_m)):
        if limit is None:
            reasons.append(f"missing_{name}_limit")
        else:
            _positive(limit, f"{name}_limit", zero=True)
            if name == "slope" and limit >= math.pi / 2:
                raise ValueError("slope_limit must be below pi/2")
            if measured > limit:
                reasons.append(f"{name}_limit_exceeded")
    return {"protocol": "vertical_probe_v1", "link_id": probe.link_id,
            "admitted_for_probe": not reasons, "reasons": reasons,
            "execution_validated": False,
            "requires": ["surface_following_executor", "support_height_measurement",
                         "configuration_and_controller_specific_physics_evidence"]}


def advance_ground_health(state, *, root_z_m, quaternion_wxyz, dt_s,
                          reference_clearance_m, support_z_m=None,
                          minimum_clearance_ratio=0.45,
                          maximum_tilt_rad=math.radians(60), dwell_s=0.5):
    """Diagnose sustained low clearance/tilt after settling in simulation time.

    reference_clearance_m is the settled root-to-support distance, NOT spawn z.
    support_z_m must be measured from the actual support surface; unknown support
    leaves the height diagnosis unknown. Tilt remains observable without it.
    Create one state per robot and reset it for each episode/settle transition.
    """
    _positive(dt_s, "dt_s")
    _positive(reference_clearance_m, "reference_clearance_m")
    _positive(dwell_s, "dwell_s")
    _positive(minimum_clearance_ratio, "minimum_clearance_ratio")
    _positive(maximum_tilt_rad, "maximum_tilt_rad")
    if minimum_clearance_ratio >= 1 or maximum_tilt_rad >= math.pi / 2:
        raise ValueError("clearance ratio must be below 1 and tilt limit below pi/2")
    valid = (math.isfinite(root_z_m) and len(quaternion_wxyz) == 4
             and all(math.isfinite(value) for value in quaternion_wxyz)
             and (support_z_m is None or math.isfinite(support_z_m)))
    if not valid:
        state["unhealthy_elapsed_s"] = 0.0
        return {"status": "invalid_state", "failed": True, "height_observed": False,
                "reasons": ["non_finite_or_malformed_pose"]}
    norm = math.hypot(*quaternion_wxyz)
    if norm < 1e-12:
        state["unhealthy_elapsed_s"] = 0.0
        return {"status": "invalid_state", "failed": True, "height_observed": False,
                "reasons": ["zero_quaternion"]}
    _, qx, qy, _ = (value / norm for value in quaternion_wxyz)
    tilt = math.acos(max(-1.0, min(1.0, 1 - 2 * (qx * qx + qy * qy))))
    clearance = None if support_z_m is None else root_z_m - support_z_m
    reasons = []
    if tilt > maximum_tilt_rad:
        reasons.append("excessive_tilt")
    if clearance is not None and clearance < reference_clearance_m * minimum_clearance_ratio:
        reasons.append("low_support_clearance")
    state["unhealthy_elapsed_s"] = (
        state.get("unhealthy_elapsed_s", 0.0) + dt_s if reasons else 0.0)
    failed = state["unhealthy_elapsed_s"] + 1e-12 >= dwell_s
    status = ("fallen" if failed else "unstable" if reasons else
              "healthy" if clearance is not None else "support_unknown")
    return {"status": status, "failed": failed, "reasons": reasons,
            "height_observed": clearance is not None,
            "support_z_m": support_z_m, "support_clearance_m": clearance, "tilt_rad": tilt,
            "unhealthy_elapsed_s": state["unhealthy_elapsed_s"]}


def advance_vertical_progress(state, probe, *, root_xyz, reference_clearance_m,
                              health, dt_s, xy_tolerance_m=0.15,
                              height_tolerance_m=0.08, progress_delta_m=0.02,
                              dwell_s=0.5):
    """Require healthy XYZ arrival and continuous dwell, for ascent or descent.

    Height progress uses a best-so-far signed rise/fall. Oscillations do not
    repeatedly count as progress. A caller still needs a bounded timeout and
    contact/route validation; this helper does not issue robot commands.
    """
    root = _point(root_xyz, "root_xyz")
    for name, value in (("reference_clearance_m", reference_clearance_m),
                        ("dt_s", dt_s), ("xy_tolerance_m", xy_tolerance_m),
                        ("height_tolerance_m", height_tolerance_m),
                        ("progress_delta_m", progress_delta_m), ("dwell_s", dwell_s)):
        _positive(value, name)
    rise = probe.goal_support_xyz[2] - probe.start_support_xyz[2]
    if height_tolerance_m >= abs(rise) / 2:
        raise ValueError("height tolerance must distinguish the start and goal levels")
    inferred_support_z = root[2] - reference_clearance_m
    directed_height = math.copysign(1.0, rise) * (inferred_support_z - probe.start_support_xyz[2])
    healthy = (health.get("status") == "healthy" and not health.get("failed", True)
               and health.get("height_observed") is True)
    best = state.get("best_height_progress_m", 0.0)
    made_progress = healthy and directed_height >= best + progress_delta_m
    if made_progress:
        state["best_height_progress_m"] = directed_height
    xy_error = math.dist(root[:2], probe.goal_support_xyz[:2])
    height_error = abs(inferred_support_z - probe.goal_support_xyz[2])
    support_z = health.get("support_z_m")
    support_at_goal = (support_z is not None and math.isfinite(support_z)
                       and abs(support_z - probe.goal_support_xyz[2]) <= height_tolerance_m)
    arrived = (healthy and support_at_goal and xy_error <= xy_tolerance_m
               and height_error <= height_tolerance_m)
    state["arrival_elapsed_s"] = state.get("arrival_elapsed_s", 0.0) + dt_s if arrived else 0.0
    return {"complete": state["arrival_elapsed_s"] + 1e-12 >= dwell_s,
            "made_height_progress": made_progress,
            "height_progress_m": directed_height,
            "best_height_progress_m": state.get("best_height_progress_m", 0.0),
            "height_error_m": height_error, "xy_error_m": xy_error,
            "arrival_elapsed_s": state["arrival_elapsed_s"]}


def summarize_vertical_episode(report, *, robot_id, reference_root_z_m=None):
    """Extract existing XYZ evidence without inventing support or quaternion data.

    Current pose_trajectory stores XYZYAW; yaw cannot diagnose tilt. Legacy final
    XYZ reports cannot diagnose the time/contact of a collapse. This read-only
    summary intentionally leaves the physical health verdict unknown.
    """
    if reference_root_z_m is not None and not math.isfinite(reference_root_z_m):
        raise ValueError("reference_root_z_m must be finite")
    poses = []
    invalid_samples = 0
    for sample in report.get("pose_trajectory", {}).get("samples", []):
        pose = sample.get("robot_poses_xyzyaw", {}).get(robot_id)
        if pose is None:
            pose = sample.get("positions", {}).get(robot_id)
        if pose is None:
            continue
        try:
            xyz = _point(pose[:3], "sample pose")
        except (ValueError, TypeError):
            invalid_samples += 1
            continue
        poses.append({"step": sample.get("step"), "xyz_m": list(xyz)})
    final_raw = report.get("final_poses", {}).get(robot_id)
    final = None
    if final_raw is not None:
        try:
            final = list(_point(final_raw[:3], "final pose"))
        except (ValueError, TypeError):
            invalid_samples += 1
    if final is None and poses:
        final = poses[-1]["xyz_m"]
    lowest = min(poses, key=lambda sample: sample["xyz_m"][2]) if poses else None
    all_heights = [sample["xyz_m"][2] for sample in poses]
    if final is not None:
        all_heights.append(final[2])
    minimum = min(all_heights) if all_heights else None
    return {"robot_id": robot_id, "episode_status": report.get("status"),
            "health_verdict": "unknown", "execution_validated": False,
            "missing_evidence": ["measured_support_height", "root_quaternion_wxyz",
                                 "continuous_health_samples", "contact_trace"],
            "finite_trajectory_samples": len(poses), "invalid_pose_samples": invalid_samples,
            "final_xyz_m": final, "minimum_observed_root_z_m": minimum,
            "lowest_trajectory_sample": lowest,
            "reference_root_z_m": reference_root_z_m,
            "maximum_observed_root_height_drop_m": (
                max(0.0, reference_root_z_m - minimum)
                if reference_root_z_m is not None and minimum is not None else None),
            "deadlocks": [row for row in report.get("deadlocks", [])
                          if row.get("agent") == robot_id]}
