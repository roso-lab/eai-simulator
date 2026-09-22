"""Execute a scenario's supported phase DAG legs with deterministic control.

Navigation tracks A* waypoints with proportional heading/velocity commands.
The optional ``kinematic`` transport mode writes an object's root pose above a
carrier after it reaches the object.  This is an explicitly *non-physical*
oracle: it demonstrates control-loop and logical-goal completion, but it does
not demonstrate grasping, contact dynamics, or arm IK.  Contact is not a
failure condition: the runtime instead terminates only after a bounded,
diagnosed lack of waypoint progress.  Reports therefore use
``kinematic_execution_validated`` rather than overloading the offline
feasibility oracle's label.

Warehouse and Modern House default to a bounded kinematic aerial motion
baseline. Every commanded and measured segment still passes the continuous
3-D clearance gate, and scan completion still requires measured hover dwell
and a live frame. This isolates task-level coordination from provider
controller convergence without claiming flight dynamics or dynamic avoidance.
"""

import argparse
from copy import deepcopy
import json
import math
import sys
import time
from pathlib import Path
from functools import partial
from contextlib import ExitStack, contextmanager

from tools.scenario_generation import common
from demo.benchmark.control import (
    WAYPOINT_HEADING_PROGRESS_RAD, WAYPOINT_PROGRESS_M, advance_hover, advance_view_alignment,
    scan_hover_goal, update_waypoint_progress,
)
from demo.benchmark.camera_validation import validate_camera_windows
from demo.benchmark.control_step_evidence import ControlStepEvidence
from demo.benchmark.aerial_navigation import (
    aerial_route_points, oriented_box_aabb, plan_local_aerial_detour,
    resolve_aerial_command,
    scene_aerial_voxel_map,
)
from demo.benchmark.observations import ObservationStore
from demo.benchmark.scheduling import EpisodeScheduler, FAILURE_STATES, TERMINAL_STATES
from demo.benchmark.provenance import (
    RUNTIME_DEFAULTS, capture_request, capture_runtime, finish_provenance,
)

if str(common.REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(common.REPO_ROOT))


def _yaw_from_quat(q):
    w, x, y, z = q
    return math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))


def navigation_state(robot, *, body_index=None):
    """Read the moving navigation body when the articulation root is fixed."""
    state = (robot.data.root_state_w[0] if body_index is None
             else robot.data.body_state_w[0, body_index])
    return (float(state[0]), float(state[1]), float(state[2]),
            _yaw_from_quat(state[3:7].tolist()))


def step_with_control_evidence(env, commands, articulations, recorder, *, step):
    """Archive the single-environment submission and immediate root readback.

    Copy before stepping: controllers may mutate command tensors. Read before
    any caller-owned kinematic pose override. These are input/state records,
    not actuator forces or evidence that every auxiliary command was applied.
    """
    submitted = {name: value[0].tolist() for name, value in commands.items()}
    result = env.step(commands)
    post_states = {name: asset.data.root_state_w[0].tolist()
                   for name, asset in articulations.items()}
    recorder.record(step, submitted, post_states)
    return result


ROOT_STATE_COMPONENTS = (
    "position_x", "position_y", "position_z",
    "quaternion_w", "quaternion_x", "quaternion_y", "quaternion_z",
    "linear_velocity_x", "linear_velocity_y", "linear_velocity_z",
    "angular_velocity_x", "angular_velocity_y", "angular_velocity_z",
)

AERIAL_KINEMATIC_SPEED_M_S = 0.6


def kinematic_aerial_step(position, goal, dt, *, speed_m_s=AERIAL_KINEMATIC_SPEED_M_S):
    """Advance a bounded 3-D baseline without overshooting its admitted goal."""
    if len(position) != 3 or len(goal) != 3:
        raise ValueError("kinematic aerial step requires finite XYZ values and positive dt/speed")
    values = tuple(float(value) for value in (*position, *goal, dt, speed_m_s))
    if not all(math.isfinite(value) for value in values) or dt <= 0 or speed_m_s <= 0:
        raise ValueError("kinematic aerial step requires finite XYZ values and positive dt/speed")
    delta = tuple(float(goal[index]) - float(position[index]) for index in range(3))
    distance = math.sqrt(sum(value * value for value in delta))
    if distance <= speed_m_s * dt:
        return tuple(float(value) for value in goal)
    scale = speed_m_s * dt / distance
    return tuple(float(position[index]) + delta[index] * scale for index in range(3))


def kinematic_aerial_speed_report(previous, current, dt, *, speed_m_s=AERIAL_KINEMATIC_SPEED_M_S):
    """Audit one measured kinematic step against its declared speed bound."""
    if len(previous) != 3 or len(current) != 3:
        raise ValueError("kinematic aerial speed audit requires finite XYZ values and positive dt/speed")
    values = tuple(float(value) for value in (*previous, *current, dt, speed_m_s))
    if not all(math.isfinite(value) for value in values) or dt <= 0 or speed_m_s <= 0:
        raise ValueError("kinematic aerial speed audit requires finite XYZ values and positive dt/speed")
    distance = math.dist(previous, current)
    maximum_distance = speed_m_s * dt
    return {
        "validated": distance <= maximum_distance + 1e-6,
        "distance_m": distance,
        "maximum_distance_m": maximum_distance,
        "speed_limit_m_s": float(speed_m_s),
    }


def finite_root_state_report(states):
    """Describe non-finite root-state components without emitting NaN JSON."""
    invalid_entities = []
    for entity, raw_state in states.items():
        values = raw_state.tolist() if hasattr(raw_state, "tolist") else list(raw_state)
        invalid = []
        for index, value in enumerate(values):
            numeric = float(value)
            if math.isfinite(numeric):
                continue
            invalid.append({
                "component": (ROOT_STATE_COMPONENTS[index]
                              if index < len(ROOT_STATE_COMPONENTS) else f"component_{index}"),
                "value": "nan" if math.isnan(numeric) else ("inf" if numeric > 0 else "-inf"),
            })
        if invalid:
            invalid_entities.append({"entity": entity, "non_finite_components": invalid})
    return {"validated": not invalid_entities, "invalid_entities": invalid_entities}


def json_safe_finite_values(values, *, digits=2):
    """Round finite values and replace invalid physics values with null."""
    result = []
    for value in values:
        numeric = float(value)
        result.append(round(numeric, digits) if math.isfinite(numeric) else None)
    return result


def explicit_final_ownership(spec, carrying, *, runtime_object_ids,
                             ownership_object_ids=None):
    """Build a complete ownership snapshot for every managed scene object.

    ``carrying`` only contains objects with an active owner, so serializing it
    directly loses released objects.  An absent runtime object is a different
    condition from an observed unowned object and must fail before a report can
    contain a misleading ``null`` value. Static landmarks that are not
    transport objects are outside this ownership ledger.
    """
    composition = spec.get("composition") if isinstance(spec, dict) else None
    entities = composition.get("entities") if isinstance(composition, dict) else None
    if not isinstance(entities, list):
        raise ValueError("final ownership requires a composition entity list")

    declared_object_ids = []
    for entity in entities:
        if not isinstance(entity, dict) or not isinstance(entity.get("id"), str) or not entity["id"]:
            raise ValueError("final ownership contains an invalid declared object id")
        object_id = entity["id"]
        if object_id in declared_object_ids:
            raise ValueError(f"final ownership contains duplicate declared object '{object_id}'")
        declared_object_ids.append(object_id)

    if ownership_object_ids is None:
        object_ids = declared_object_ids
    else:
        object_ids = sorted(ownership_object_ids)
        if (len(object_ids) != len(set(object_ids))
                or any(not isinstance(object_id, str) or not object_id for object_id in object_ids)
                or any(object_id not in declared_object_ids for object_id in object_ids)):
            raise ValueError("final ownership contains an invalid managed object set")

    try:
        runtime_ids = set(runtime_object_ids)
    except TypeError as exc:
        raise ValueError("final ownership requires runtime object ids") from exc
    if any(not isinstance(object_id, str) or not object_id for object_id in runtime_ids):
        raise ValueError("final ownership contains an invalid runtime object id")
    missing = [object_id for object_id in object_ids if object_id not in runtime_ids]
    if missing:
        raise RuntimeError(
            "cannot emit final ownership: missing runtime objects " + ", ".join(sorted(missing)))

    if not isinstance(carrying, dict):
        raise ValueError("final ownership requires an ownership mapping")
    undeclared = sorted(set(carrying) - set(object_ids))
    if undeclared:
        raise ValueError(
            "final ownership contains undeclared active objects " + ", ".join(undeclared))

    snapshot = {}
    for object_id in object_ids:
        if object_id not in carrying:
            # This is an explicit observation that a present object has no
            # active owner. It is intentionally different from a missing key.
            snapshot[object_id] = None
            continue
        owner = carrying[object_id]
        if isinstance(owner, str) and owner:
            snapshot[object_id] = owner
        elif (isinstance(owner, (list, tuple)) and owner
              and len(set(owner)) == len(owner)
              and all(isinstance(member, str) and member for member in owner)):
            snapshot[object_id] = list(owner)
        else:
            raise ValueError(f"final ownership has an invalid owner for '{object_id}'")
    return snapshot


def create_scene_participant_session(spec, *, scenario_sha256, participant_adapter=None):
    """Create the public scene participant without exposing evaluator truth."""
    from demo.benchmark.participant_runtime import ParticipantSession, PublicTaskGreedyAdapter
    from demo.benchmark.scene_challenge import build_scene_runtime_contract

    public_task, participant_api = build_scene_runtime_contract(
        spec, scenario_sha256=scenario_sha256)
    adapter = participant_adapter or PublicTaskGreedyAdapter(public_task)
    participant_session = ParticipantSession(
        adapter=adapter, api=participant_api, task_spec=public_task)
    participant_session.emit("episode_started", time_s=0.0, payload={
        "fleet": [robot["id"] for robot in public_task["initial_fleet"]],
        "objectives": public_task["objectives"],
        "resources": public_task["resources"],
    })
    controlled_nodes = set(public_task["participant"]["controlled_objectives"])
    return participant_session, public_task, controlled_nodes


@contextmanager
def report_execution_exception(report_path):
    try:
        yield
    except Exception as error:
        import traceback
        traceback.print_exc()
        if report_path:
            common.write_json(str(Path(report_path).with_suffix(".failure.json")), {
                "status": "runtime_exception", "exception_type": type(error).__name__,
                "message": str(error), "traceback": traceback.format_exc(),
                "execution_validation": {"validated": False, "tier": "execution_failed"},
            })
        raise


def _kahn_order(nodes):
    pending = {node["id"]: set(node["prerequisites"]) for node in nodes}
    ordered = []
    while pending:
        ready = sorted(key for key, deps in pending.items() if not deps & pending.keys())
        if not ready:
            raise ValueError("phase_graph contains a cycle")
        for key in ready:
            ordered.append(next(node for node in nodes if node["id"] == key))
            del pending[key]
    return ordered



_SUPPORTED_SKILLS = {"navigate", "transport_object", "joint_transport", "aerial_scan", "wait"}
PATH_TRACKING_RESERVE_M = 0.15
# NVD proxy walls sit at occupancy-cell granularity and the warehouse aisles
# rub robot disks at corners; a wider planning reserve keeps execution drift
# off those corners. Factory keeps its tuned 0.15 m baseline.
NVD_TRACKING_RESERVE_EXTRA_M = 0.10


def tracking_reserve_for(scene_key):
    if str(scene_key or "").endswith("_nvd"):
        return PATH_TRACKING_RESERVE_M + NVD_TRACKING_RESERVE_EXTRA_M
    return PATH_TRACKING_RESERVE_M


def configure_render_period(env_cfg, *, period_s):
    if not math.isfinite(period_s) or period_s <= 0:
        raise ValueError("render period must be finite and positive")
    control_dt = float(env_cfg.sim.dt) * env_cfg.decimation
    control_steps = max(1, math.ceil(period_s / control_dt - 1e-9))
    env_cfg.sim.render_interval = control_steps * env_cfg.decimation


def configure_benchmark_environment(env_cfg, *, period_s, inventory_variant):
    if period_s:
        configure_render_period(env_cfg, period_s=period_s)
    if inventory_variant is not None:
        from demo.factory_benchmark.inventory_scene import configure_variant
        configure_variant(env_cfg, path=inventory_variant["path"], sha256=inventory_variant["sha256"])


def summarize_scheduled_trajectory(data, trajectory, scheduling):
    from EAI.scenario_spec.evaluation import derive_success_predicates, json_key
    from EAI.scenario_spec.runtime_evaluator import summarize_trajectory

    skipped = {node for node, state in scheduling.get("states", {}).items()
               if state.get("status") == "skipped"}
    active_data = {**data, "phase_graph": {"nodes": [
        node for node in data["phase_graph"]["nodes"] if node["id"] not in skipped
    ]}}
    active_keys = {json_key(predicate) for predicate in derive_success_predicates(active_data)}
    filtered = [{**sample, "satisfied": {key: value for key, value in sample["satisfied"].items()
                                        if key in active_keys}} for sample in trajectory]
    return {**summarize_trajectory(filtered), "skipped_phase_nodes": sorted(skipped)}


def reconcile_episode_end(scheduling_report, *, steps_taken, control_dt_s):
    """Include the final completed physics step in the episode time window."""
    if (not isinstance(steps_taken, int) or steps_taken < 0
            or not math.isfinite(control_dt_s) or control_dt_s <= 0):
        raise ValueError("episode end reconciliation requires nonnegative steps and positive dt")
    measured_end_s = steps_taken * control_dt_s
    scheduling_report["time_s"] = max(
        float(scheduling_report.get("time_s", 0.0)), measured_end_s)
    return scheduling_report


def executor_capabilities():
    return {
        "navigate": "closed_loop_waypoint_navigation",
        "aerial_scan": "position_dwell_with_live_camera_frame_evidence",
        "transport_object": "kinematic_opt_in_only",
        "joint_transport": "kinematic_opt_in_only",
        "handoff_object": "unsupported",
        "wait": "simulation_time_hold",
    }


def joint_event_validation(plan, events, *, recovered_by=None, trajectory=None):
    """Validate idealized two-robot ownership transitions for joint carries.

    This is a logical state-machine check.  It records that both declared
    robots owned the object together between pickup and release; it does not
    infer contact forces, grasp quality, or inverse-kinematics success.
    """
    entries = [entry for entry in plan if entry.get("skill") == "joint_transport"]
    violations = []
    participants = set()
    recovered = []
    formation = {}
    for entry in entries:
        team = tuple(entry.get("team") or entry.get("agents") or ())
        if len(team) != 2 or len(set(team)) != 2:
            violations.append(f"{entry['node']}: joint transport requires two distinct agents")
            continue
        participants.update(team)
        if entry["node"] in (recovered_by or {}):
            from .transport_evidence import joint_recovery_evidence

            recovery_violations = joint_recovery_evidence(entry, plan, events, recovered_by)
            violations.extend(recovery_violations)
            if not recovery_violations:
                recovered.append(entry["node"])
            continue
        pickup = next((event for event in events
                       if event.get("event") == "pickup"
                       and event.get("node") == entry["node"]
                       and event.get("object") == entry.get("object")), None)
        release = next((event for event in events
                        if event.get("event") == "release"
                        and event.get("node") == entry["node"]
                       and event.get("object") == entry.get("object")), None)
        expected = list(team)
        if pickup is None:
            violations.append(f"{entry['node']}: missing ordered joint pickup")
        else:
            owner_after = pickup.get("owner_after")
            if sorted(owner_after or []) != sorted(expected) or pickup.get("owner_before") is not None:
                violations.append(f"{entry['node']}: invalid joint ownership transition at pickup")
            if sorted(pickup.get("agents") or []) != sorted(expected):
                violations.append(f"{entry['node']}: pickup does not name both team members")
        if release is None:
            violations.append(f"{entry['node']}: missing ordered joint release")
        else:
            if pickup is not None and events.index(release) <= events.index(pickup):
                violations.append(f"{entry['node']}: joint release precedes pickup")
            if sorted(release.get("owner_before") or []) != sorted(expected) or release.get("owner_after") is not None:
                violations.append(f"{entry['node']}: invalid joint ownership transition at release")
        if pickup is not None and release is not None and trajectory is not None:
            from .joint_transport import measure_joint_formation

            report = measure_joint_formation(
                trajectory, members=team, start_step=pickup["step"], end_step=release["step"],
                max_separation_m=entry["team_max_separation_m"])
            report["formation_hold_steps"] = dict(entry.get("formation_hold_steps") or {})
            formation[entry["node"]] = report
            if not report["validated"]:
                violations.append(f"{entry['node']}: measured joint formation exceeded its bound")
    return {
        "required": bool(entries),
        "nodes": [entry["node"] for entry in entries],
        "participants": sorted(participants),
        "recovered_nodes": recovered,
        "formation": formation,
        "validated": bool(entries) and not violations,
        "violations": violations,
        "model": "idealized_shared_ownership",
    }


def stall_action(*, step, last_progress_step, replans, stall_window, max_replans):
    """Choose recovery only from task progress, never from contact events."""
    if step - last_progress_step <= stall_window:
        return "continue"
    return "replan" if replans < max_replans else "deadlock"


def auto_step_budget(*, route_cells, stall_window, max_replans,
                     ground_distance_m=0.0, ground_speed_m_s=0.55,
                     ground_tracking_factor=3.0,
                     aerial_distance_m=0.0, aerial_hover_s=0.0,
                     declared_hold_s=0.0, control_dt_s=0.02,
                     aerial_speed_m_s=AERIAL_KINEMATIC_SPEED_M_S):
    """Budget ground routes, bounded aerial motion, holds, and stall diagnosis."""
    values = (ground_distance_m, ground_speed_m_s, ground_tracking_factor,
              aerial_distance_m, aerial_hover_s, declared_hold_s,
              control_dt_s, aerial_speed_m_s)
    if (route_cells < 0 or stall_window < 1 or max_replans < 0
            or not all(math.isfinite(float(value)) for value in values)
            or ground_distance_m < 0 or ground_speed_m_s <= 0 or ground_tracking_factor < 1
            or any(float(value) < 0 for value in values[3:6])
            or control_dt_s <= 0 or aerial_speed_m_s <= 0):
        raise ValueError("auto step budget inputs must be finite and nonnegative with positive rates")
    route_cell_floor_steps = int(route_cells * 12)
    ground_distance_steps = math.ceil(
        ground_distance_m / (ground_speed_m_s * control_dt_s) * ground_tracking_factor)
    route_tracking_steps = max(route_cell_floor_steps, ground_distance_steps)
    aerial_motion_steps = math.ceil(aerial_distance_m / (aerial_speed_m_s * control_dt_s))
    aerial_hover_steps = math.ceil(aerial_hover_s / control_dt_s)
    declared_hold_steps = math.ceil(declared_hold_s / control_dt_s)
    deadlock_reserve_steps = stall_window * (max_replans + 1)
    estimated_steps = (route_tracking_steps + aerial_motion_steps
                       + aerial_hover_steps + declared_hold_steps)
    max_steps = max(3000, min(25000, estimated_steps + deadlock_reserve_steps))
    return {
        "route_cells": route_cells,
        "route_cell_floor_steps": route_cell_floor_steps,
        "ground_distance_m": ground_distance_m,
        "ground_speed_m_s": ground_speed_m_s,
        "ground_tracking_factor": ground_tracking_factor,
        "ground_distance_steps": ground_distance_steps,
        "route_tracking_steps": route_tracking_steps,
        "aerial_distance_m": aerial_distance_m,
        "aerial_motion_steps": aerial_motion_steps,
        "aerial_hover_s": aerial_hover_s,
        "aerial_hover_steps": aerial_hover_steps,
        "declared_hold_s": declared_hold_s,
        "declared_hold_steps": declared_hold_steps,
        "control_dt_s": control_dt_s,
        "aerial_speed_m_s": aerial_speed_m_s,
        "deadlock_reserve_steps": deadlock_reserve_steps,
        "max_steps": max_steps,
    }


def steering_command(*, distance, heading_error, speed, k_turn):
    """Return a conservative planar command for one waypoint.

    Turning in place for large heading errors prevents the tracker from
    cutting inflated-grid corners.  Forward speed is also tapered near the
    waypoint so a robot does not repeatedly overshoot a manipulation pose.
    """
    wz = max(-1.2, min(1.2, k_turn * heading_error))
    if abs(heading_error) >= 0.65:
        return 0.0, wz
    heading_scale = max(0.0, 1.0 - abs(heading_error) / 0.65)
    distance_scale = max(0.2, min(1.0, distance / 1.0))
    return speed * heading_scale * distance_scale, wz


def velocity_policy_command(*, distance, heading_error, speed, k_turn,
                            minimum_distance_scale=0.2):
    """Project waypoint velocity into the policy's body-frame command axes."""
    if (not math.isfinite(minimum_distance_scale)
            or not 0 < minimum_distance_scale <= 1):
        raise ValueError("minimum distance scale must be in (0, 1]")
    distance_scale = max(minimum_distance_scale, min(1.0, distance / 1.0))
    magnitude = speed * distance_scale
    vx = magnitude * math.cos(heading_error)
    vy = magnitude * math.sin(heading_error)
    wz = max(-1.2, min(1.2, k_turn * heading_error))
    return vx, vy, wz


def navigation_arrived(data, entry, position):
    """Check the current pose using the evaluator's declared visit predicate."""
    from EAI.scenario_spec.evaluation import evaluate_predicate

    predicate = entry.get("arrival_predicate")
    return predicate is not None and evaluate_predicate(
        data, predicate, positions={entry["agent"]: position})


def route_waypoints(grid, path, goal, *, preserve_goal=False):
    """Keep route turns while collapsing collinear runs of grid cells."""
    selected = []
    for index, cell in enumerate(path):
        if index in {0, len(path) - 1}:
            selected.append(cell)
            continue
        previous = path[index - 1]
        following = path[index + 1]
        incoming = (cell[0] - previous[0], cell[1] - previous[1])
        outgoing = (following[0] - cell[0], following[1] - cell[1])
        if incoming != outgoing:
            selected.append(cell)
    points = [grid._occupancy.world((cell[0] + 0.5) * grid.resolution,
                                    (cell[1] + 0.5) * grid.resolution) for cell in selected]
    if preserve_goal and grid.passable(*grid.cell(*goal[:2])):
        endpoint = tuple(goal[:2])
        if not points or math.dist(points[-1], endpoint) > 1e-9:
            points.append(endpoint)
    return points


def intermediate_waypoint_tolerance(configured_tolerance, grid_resolution):
    """Keep waypoint switching local to the planned grid corridor."""
    if not all(math.isfinite(value) and value > 0
               for value in (configured_tolerance, grid_resolution)):
        raise ValueError("waypoint tolerance and grid resolution must be finite and positive")
    return min(configured_tolerance, grid_resolution / 2)


def within_interaction_standoff(position, target, standoff):
    """Return whether two measured planar poses satisfy an interaction radius."""
    values = (*position[:2], *target[:2], standoff)
    if (len(position) < 2 or len(target) < 2
            or not all(math.isfinite(value) for value in values)
            or standoff <= 0):
        raise ValueError("interaction range requires finite planar poses and a positive standoff")
    return math.dist(position[:2], target[:2]) <= standoff


def planning_radius(robot):
    """Return the footprint already defined by the feasibility oracle."""
    from EAI.scenario_spec.oracle import _robot_radius

    return _robot_radius(robot)


def _declared_carry_center_z(robot, entity):
    """Resolve a carried object's center from declared support geometry.

    Newer scene records may provide an absolute carrier support or mount
    reference height. Older records only declare the carrier spawn pose, which
    is the deterministic fallback. An object-local grasp/support anchor, when
    present, is subtracted from that support height; no free lift offset is
    introduced here.
    """
    pose = robot.get("spawn_pose") or {}
    position = pose.get("position") or []
    if len(position) < 3:
        raise ValueError(f"robot {robot.get('id', '<unknown>')} has no declared spawn height")
    support_z = robot.get("carry_support_z_m", robot.get("mount_reference_z_m", position[2]))
    if (isinstance(support_z, bool) or not isinstance(support_z, (int, float))
            or not math.isfinite(support_z)):
        raise ValueError(f"robot {robot.get('id', '<unknown>')} carry support height is invalid")
    anchor_z = entity.get("grasp_anchor_local_z_m", entity.get("support_anchor_local_z_m", 0.0))
    if (isinstance(anchor_z, bool) or not isinstance(anchor_z, (int, float))
            or not math.isfinite(anchor_z)):
        raise ValueError(f"object {entity.get('id', '<unknown>')} carry anchor height is invalid")
    size = entity.get("size_m") or []
    if len(size) < 3 or isinstance(size[2], bool) or not isinstance(size[2], (int, float)):
        raise ValueError(f"object {entity.get('id', '<unknown>')} has no declared height")
    if not math.isfinite(float(size[2])) or float(size[2]) <= 0:
        raise ValueError(f"object {entity.get('id', '<unknown>')} height is invalid")
    # ``support_z`` is the carrier-side support/contact height and
    # ``anchor_z`` is the object-local point used for that contact.  Convert
    # the local point to the object's center so a support anchor below center
    # produces the correct small lift instead of a free offset.
    return float(support_z) + float(size[2]) / 2.0 - float(anchor_z)


def _paired_support_yaw(positions, team, fallback):
    """Orient a jointly carried object from the two declared support points."""
    if len(team) < 2:
        return fallback
    first, second = positions[team[0]], positions[team[1]]
    dx, dy = second[0] - first[0], second[1] - first[1]
    if math.hypot(dx, dy) <= 1e-9:
        return fallback
    return math.atan2(dy, dx)


def rebind_arrival_predicate(entry):
    """Keep a navigation visit predicate bound to the executing agent.

    A participant allocation may change the executing agent; the evaluator
    samples exactly one robot per predicate, so a stale binding would either
    crash on an unsampled robot or score a robot that never moved.
    """
    predicate = entry.get("arrival_predicate")
    if predicate is None:
        return None
    entry["arrival_predicate"] = {
        "fn": "robot_visited_zone",
        "args": [entry["agent"], predicate["args"][1]],
    }
    return entry["arrival_predicate"]


def bind_evaluation_agents(data, plan):
    """Bind navigation predicates to the agents selected during execution."""
    effective = deepcopy(data)
    nodes = {node["id"]: node for node in effective["phase_graph"]["nodes"]}
    for entry in plan:
        node = nodes.get(entry.get("node"))
        if (node is not None and entry.get("skill") == "navigate"
                and isinstance(entry.get("agent"), str) and entry["agent"]):
            node["preferred_agent"] = entry["agent"]
    return effective


def _passable_route(grid, start_xy, goal_xy):
    """A* path with build_plan's nearest-passable fallback for blocked ends."""
    path = grid.path(start_xy, goal_xy)
    if path is None:
        # start or goal may sit on an occupied cell (platform projection,
        # dropped-object footprint); fall back to the nearest passable cell
        # for whichever endpoint blocked.
        for is_start, probe in ((True, start_xy), (False, goal_xy)):
            nearest = grid.nearest_passable(probe)
            if nearest is None:
                continue
            fixed = grid._occupancy.world(
                (nearest[0] + 0.5) * grid.resolution,
                (nearest[1] + 0.5) * grid.resolution)
            path = grid.path(fixed, goal_xy) if is_start else grid.path(start_xy, fixed)
            if path is not None:
                break
    return path


def rebuild_transport_routes(entry, *, spec, occupancy, member_positions, object_position=None):
    """Rebuild transport legs for a participant-selected agent or team.

    Routes are re-planned from each member's live position to the object's
    live position (relay legs and dropped objects may have moved it), then to
    the node goal. Footprint, planning-radius, and carry-height bookkeeping is
    refreshed for the newly assigned agents. This mutates only planning state;
    it moves no robot or object and reads no evaluator truth. Raises ValueError
    when the selected team leaves no routable leg, which callers must treat as
    a diagnosed node failure instead of a partial silent execution.
    """
    from EAI.scenario_spec.geometry import static_blocked_grid
    from EAI.scenario_spec.oracle import InflatedGrid

    if entry.get("skill") not in {"transport_object", "joint_transport"}:
        raise ValueError("route rebuild applies to transport objectives only")
    team = list(entry.get("team") or [entry["agent"]])
    if not team:
        raise ValueError(f"{entry['node']}: transport requires at least one assigned agent")
    missing = [member for member in team if member not in member_positions]
    if missing:
        raise ValueError(f"{entry['node']}: missing live positions for {missing}")

    robots = {robot["id"]: robot for robot in spec["robots"]}
    leader = robots[entry["agent"]]
    footprint_radius = planning_radius(leader)
    blocked = static_blocked_grid(
        occupancy, spec, ground_tracking_reserve_m=entry["ground_tracking_reserve_m"])
    grid = InflatedGrid(occupancy, footprint_radius, blocked)

    entities = {entity["id"]: entity for entity in spec["composition"]["entities"]}
    entity = entities[entry["object"]]
    pickup = tuple(object_position if object_position is not None
                   else entry["pickup_goal"])[:2]
    goal = tuple(entry["goal"])[:2]

    def leg(start_xy, goal_xy):
        path = _passable_route(grid, tuple(start_xy)[:2], goal_xy)
        if path is None:
            raise ValueError(
                f"{entry['node']}: no route for {entry['agent']} between "
                f"{tuple(start_xy)[:2]} and {goal_xy}")
        return route_waypoints(grid, path, goal_xy)

    summary = {
        "node": entry["node"],
        "team": list(team),
        "skill": entry["skill"],
        "planning_radius_m": footprint_radius,
        "pickup_position_m": [round(pickup[0], 3), round(pickup[1], 3)],
    }
    entry["pickup_goal"] = pickup
    entry["agent_radius"] = footprint_radius
    entry["route_planning_radius_m"] = footprint_radius
    entry["path_waypoint_tolerance_m"] = grid.resolution / 2
    support_members = team if entry["skill"] == "joint_transport" else [entry["agent"]]
    entry["carry_z"] = sum(
        _declared_carry_center_z(robots[member], entity) for member in support_members
    ) / len(support_members)
    if entry["skill"] == "joint_transport":
        from .joint_transport import joint_formation_limit, plan_joint_rendezvous

        member_radii = {member: planning_radius(robots[member]) for member in team}
        rendezvous = plan_joint_rendezvous(
            occupancy=occupancy, blocked=blocked, members=team, radii=member_radii,
            starts=member_positions, pickup=pickup, delivery=goal,
            object_radius=math.hypot(entity["size_m"][0], entity["size_m"][1]) / 2)
        entry["team_pickup_slots"] = rendezvous["pickup_slots"]
        entry["team_delivery_slots"] = rendezvous["delivery_slots"]
        entry["team_terminal_tolerance_m"] = rendezvous["terminal_tolerance_m"]
        entry["team_planning_radii_m"] = member_radii
        entry["team_max_separation_m"] = joint_formation_limit(
            entry["team_pickup_slots"], entry["team_delivery_slots"])
        entry["formation_hold_steps"] = {member: 0 for member in team}
        entry["object_planar_radius_m"] = math.hypot(entity["size_m"][0], entity["size_m"][1]) / 2
        entry["team_to_object"] = {
            member: route_waypoints(rendezvous["grids"][member],
                                    rendezvous["paths"][member][0],
                                    entry["team_pickup_slots"][member], preserve_goal=True)
            for member in team}
        entry["team_to_target"] = {
            member: route_waypoints(rendezvous["grids"][member],
                                    rendezvous["paths"][member][1],
                                    entry["team_delivery_slots"][member], preserve_goal=True)
            for member in team}
        summary["pickup_slots_m"] = entry["team_pickup_slots"]
        summary["delivery_slots_m"] = entry["team_delivery_slots"]
        summary["member_planning_radii_m"] = member_radii
        summary["max_team_separation_m"] = entry["team_max_separation_m"]
        summary["team_to_object_waypoints"] = {
            member: len(route) for member, route in entry["team_to_object"].items()}
        summary["team_to_target_waypoints"] = {
            member: len(route) for member, route in entry["team_to_target"].items()}
    else:
        planar_half_diagonal = math.hypot(
            entity["size_m"][0] / 2, entity["size_m"][1] / 2)
        entry["interaction_standoff"] = footprint_radius + planar_half_diagonal + 0.1
        entry["to_object"] = leg(member_positions[team[0]], pickup)
        entry["to_target"] = leg(pickup, goal)
        summary["interaction_standoff_m"] = entry["interaction_standoff"]
        summary["to_object_waypoints"] = len(entry["to_object"])
        summary["to_target_waypoints"] = len(entry["to_target"])
    return summary


def plan_lifecycle_release(entries, *, affected_agent, carrying, reason):
    """Decide forced releases for an interrupted lifecycle assignment.

    Ownership may only change through runtime pickup/release events, so an
    exit while an object is carried must drop it at the carrier's position
    before the affected nodes can fail or be reassigned. This returns audit
    records; the caller owns the physics release and pose writes.
    """
    releases = []
    for entry in entries:
        object_ref = entry.get("object")
        if not object_ref:
            continue
        owner = carrying.get(object_ref)
        if owner is None:
            continue
        owners = list(owner) if isinstance(owner, (list, tuple)) else [owner]
        if affected_agent not in owners:
            continue
        releases.append({
            "node": entry["node"],
            "object": object_ref,
            "owner_before": owners,
            "owner_after": None,
            "reason": reason,
            "agent": affected_agent,
        })
    return releases


def plan_member_exit_release(entries, *, failed_agent, carrying):
    """Compatibility wrapper for the member-exit release policy."""
    return plan_lifecycle_release(
        entries, affected_agent=failed_agent, carrying=carrying,
        reason="member_exit")


def planning_template(scene_key, occupancy):
    """Resolve the planning template for the selected scene."""
    from EAI.scenario_spec.templates import template_for

    return template_for(scene_key or "factory", occupancy)


def relay_event_validation(plan, events):
    """Validate ordered ownership transfer for repeated-object transports."""
    by_object = {}
    for entry in plan:
        if entry.get("skill") == "transport_object" and entry.get("object"):
            by_object.setdefault(entry["object"], []).append(entry)
    relays = {key: value for key, value in by_object.items()
              if len(value) > 1 and len({entry["agent"] for entry in value}) > 1}
    violations = []
    participants = set()
    for object_ref, legs in relays.items():
        cursor = -1
        prior_entry = None
        prior_release_index = None
        prior_release_event = None
        for entry in legs:
            if prior_entry is not None and prior_entry["agent"] == entry["agent"]:
                violations.append(
                    f"{object_ref}: consecutive relay legs use {entry['agent']}")
            participants.add(entry["agent"])
            matched = {}
            for kind in ("pickup", "release"):
                match = next((index for index, event in enumerate(events)
                              if index > cursor
                              and event.get("event") == kind
                              and event.get("node") == entry["node"]
                              and event.get("agent") == entry["agent"]
                              and event.get("object") == object_ref), None)
                if match is None:
                    violations.append(
                        f"{object_ref}: missing ordered {kind} for {entry['node']}")
                    break
                cursor = match
                matched[kind] = events[match]
                if kind == "pickup" and prior_release_index is not None:
                    clearance = next((event for event in events[prior_release_index + 1:match]
                                      if event.get("event") == "node_complete"
                                      and event.get("agent") != entry["agent"]
                                      and event.get("node") in entry["prerequisites"]), None)
                    if clearance is None:
                        violations.append(
                            f"{object_ref}: receiver pickup lacks an ordered sender-clear prerequisite")
                    team = events[match].get("team_positions") or {}
                    sender_xy = team.get(prior_entry["agent"])
                    receiver_xy = team.get(entry["agent"])
                    required = prior_entry["agent_radius"] + entry["agent_radius"] + 0.15
                    if sender_xy is None or receiver_xy is None:
                        violations.append(f"{object_ref}: pickup lacks team-position evidence")
                    elif math.dist(sender_xy, receiver_xy) < required:
                        violations.append(
                            f"{object_ref}: handoff clearance is below {required:.2f} m")
                    if prior_release_event is not None:
                        released_xy = prior_release_event.get("position")
                        pickup_xy = events[match].get("object_position")
                        if released_xy is None or pickup_xy is None or math.dist(
                                released_xy, pickup_xy) > 0.05:
                            violations.append(
                                f"{object_ref}: receiver did not pick up at the release location")
                if kind == "release":
                    prior_release_index = match
                    prior_release_event = events[match]
            pickup = matched.get("pickup")
            release = matched.get("release")
            if pickup is not None and (
                    pickup.get("owner_before") is not None
                    or pickup.get("owner_after") != entry["agent"]):
                violations.append(f"{object_ref}: invalid ownership transition at pickup")
            if release is not None and (
                    release.get("owner_before") != entry["agent"]
                    or release.get("owner_after") is not None):
                violations.append(f"{object_ref}: invalid ownership transition at release")
            prior_entry = entry
    return {
        "required": bool(relays),
        "objects": sorted(relays),
        "participants": sorted(participants),
        "validated": bool(relays) and not violations,
        "violations": violations,
    }


def validate_runtime_fault_events(data, *, participant_enabled=None,
                                  lifecycle_supported_events=()):
    """Validate lifecycle interventions at the Isaac executor boundary.

    The protocol harness can model all three lifecycle event names.  The
    single-session Isaac executor has a narrower contract: a member may leave
    only when another already-loaded articulation can take over.  Dynamic
    spawning and controller/sensor reconfiguration are rejected before any
    physical completion can be reported.

    ``participant_enabled=None`` preserves the legacy pure contract helper's
    loose inspection mode.  The real executor passes a boolean and therefore
    applies every boundary check below.
    """
    supported_hooks = set(lifecycle_supported_events)
    allowed_hooks = {
        "member_join", "capability_change", "prepare_preloaded_articulations"}
    if supported_hooks - allowed_hooks:
        raise ValueError("unknown lifecycle callback event")
    supported_dynamic = supported_hooks & {"member_join", "capability_change"}
    reserves = set((data.get("runtime_lifecycle") or {}).get(
        "preloaded_reserve_agent_ids") or ())
    if reserves and "prepare_preloaded_articulations" not in supported_hooks:
        raise ValueError(
            "preloaded reserve inventory requires an Isaac preparation/readback callback")
    events = data.get("fault_events") or []
    if not isinstance(events, list):
        raise ValueError("fault_events must be a list")
    if not events:
        return {"enabled": bool(reserves), "supported": [], "unsupported": []}
    robot_ids = {robot.get("id") for robot in data.get("robots", ())}
    if None in robot_ids:
        raise ValueError("every runtime fault fleet member must have an id")
    if len({event.get("id") for event in events}) != len(events):
        raise ValueError("runtime fault event ids must be unique")
    scene_key = data.get("scene_key", "factory")
    supported = []
    for event in events:
        if not isinstance(event, dict):
            raise ValueError("runtime fault events must be objects")
        event_id = event.get("id")
        kind = event.get("kind")
        time_s = event.get("time_s")
        if not isinstance(event_id, str) or not event_id:
            raise ValueError("runtime fault event id must be a nonempty string")
        if (isinstance(time_s, bool) or not isinstance(time_s, (int, float))
                or not math.isfinite(float(time_s)) or float(time_s) < 0):
            raise ValueError(f"{event_id}: runtime fault time_s must be finite and nonnegative")
        if kind in {"member_join", "capability_change"}:
            if kind not in supported_dynamic:
                raise ValueError(
                    f"{event_id}: {kind} is unsupported by the Isaac single-session runtime; "
                    "the event must remain unknown rather than becoming a protocol-only success")
            if participant_enabled is not True:
                raise ValueError(
                    f"{event_id}: {kind} requires the public participant runtime boundary")
            lifecycle = data.get("runtime_lifecycle") or {}
            initial = set(lifecycle.get("initial_agent_ids") or robot_ids)
            reserves = set(lifecycle.get("preloaded_reserve_agent_ids") or ())
            agent = event.get("agent")
            if kind == "member_join" and agent not in reserves:
                raise ValueError(
                    f"{event_id}: member_join requires a declared preloaded reserve")
            if kind == "capability_change" and agent not in initial | reserves:
                raise ValueError(
                    f"{event_id}: capability_change names an unknown physical agent")
            declaration = event.get("capability_declaration")
            version = event.get("capability_version")
            if not isinstance(declaration, dict) or not declaration:
                raise ValueError(f"{event_id}: {kind} requires a capability declaration")
            if (type(version) is not int
                    or (kind == "member_join" and version != 1)
                    or (kind == "capability_change" and version < 2)):
                raise ValueError(f"{event_id}: invalid capability_version")
            supported.append({
                "id": event_id, "kind": kind, "time_s": float(time_s),
                "agent": agent,
                "implementation": (
                    "preloaded_articulation_activation_with_readback"
                    if kind == "member_join"
                    else "controller_reconfiguration_with_versioned_readback"),
            })
            continue
        if kind != "member_exit":
            raise ValueError(f"{event_id}: unsupported runtime lifecycle event kind {kind!r}")
        agent = event.get("agent")
        replacement = event.get("replacement_agent")
        if agent not in robot_ids:
            raise ValueError(f"{event_id}: member_exit names an unknown agent {agent!r}")
        robot_by_id = {robot.get("id"): robot for robot in data.get("robots", ())}
        if participant_enabled is None and any(
                robot.get("type") in {"cf2x", "iris", "pegasus"}
                for robot in data.get("robots", ())):
            raise ValueError("member_exit currently requires a ground-only fleet")
        if replacement not in robot_ids or replacement == agent:
            if participant_enabled is None:
                # Historical contract-only callers used a member-exit marker
                # without a recovery branch.  The live executor always passes
                # a boolean and therefore never takes this compatibility path.
                supported.append({
                    "id": event_id, "kind": kind, "time_s": float(time_s),
                    "agent": agent, "replacement_agent": replacement,
                    "implementation": "legacy_contract_only",
                })
                continue
            raise ValueError(
                f"{event_id}: member_exit requires a different already-declared replacement_agent")
        if (robot_by_id[agent].get("type") in {"cf2x", "iris", "pegasus"}
                or robot_by_id[replacement].get("type") in {"cf2x", "iris", "pegasus"}):
            raise ValueError(
                f"{event_id}: member_exit replacement must use preloaded ground articulations")
        if scene_key in {"warehouse_nvd", "modern_house_nvd"} and participant_enabled is False:
            raise ValueError(
                f"{scene_key}: member_exit requires the public participant runtime boundary")
        supported.append({
            "id": event_id,
            "kind": kind,
            "time_s": float(time_s),
            "agent": agent,
            "replacement_agent": replacement,
            "implementation": "preloaded_existing_articulation_replacement",
        })
    return {"enabled": True, "supported": supported, "unsupported": []}


def runtime_lifecycle_preflight(data, *, participant_enabled,
                                lifecycle_supported_events=()):
    """Return structured unknown results before scenario validation/Isaac.

    Scenario schema validation intentionally knows the published fault
    vocabulary, while this preflight also handles an authoring attempt to put
    ``member_join`` or ``capability_change`` in a runtime episode.  Returning
    a report lets the caller fail closed without presenting a bare exception
    that could be mistaken for a physical runtime result.
    """
    supported_hooks = set(lifecycle_supported_events)
    supported_dynamic = supported_hooks & {"member_join", "capability_change"}
    unknown = []
    reserves = ((data.get("runtime_lifecycle") or {}).get(
        "preloaded_reserve_agent_ids") or ()) if isinstance(data, dict) else ()
    if reserves and "prepare_preloaded_articulations" not in supported_hooks:
        unknown.append({
            "event": "runtime_lifecycle_inventory_unsupported",
            "status": "unknown", "validated": False,
            "kind": "prepare_preloaded_articulations", "fault_id": None,
            "agent": None, "replacement_agent": None, "time_s": 0.0,
            "reason": "preloaded reserve inventory has no Isaac preparation/readback callback",
            "physical_membership_changed": False,
            "capability_changed": False,
            "task_completion_claimed": False,
        })
    for event in data.get("fault_events", ()) if isinstance(data, dict) else ():
        if not isinstance(event, dict):
            continue
        kind = event.get("kind")
        reason = None
        if kind in {"member_join", "capability_change"} and kind not in supported_dynamic:
            reason = "dynamic lifecycle event is unsupported by the Isaac single-session runtime"
        elif (kind == "member_exit" and participant_enabled is False
              and data.get("scene_key") in {"warehouse_nvd", "modern_house_nvd"}):
            reason = "Warehouse/House member_exit requires the public participant runtime boundary"
        elif kind == "member_exit" and not event.get("replacement_agent"):
            reason = "member_exit has no preloaded replacement_agent"
        if reason:
            unknown.append({
                "event": "runtime_lifecycle_unsupported",
                "status": "unknown", "validated": False,
                "kind": kind, "fault_id": event.get("id"),
                "agent": event.get("agent"),
                "replacement_agent": event.get("replacement_agent"),
                "time_s": event.get("time_s"), "reason": reason,
                "physical_membership_changed": False,
                "capability_changed": False,
                "task_completion_claimed": False,
            })
    return {
        "schema": "eai.benchmark.isaac_runtime_lifecycle",
        "version": 1,
        "supported_events": [
            "member_exit_preloaded_replacement",
            *(f"{kind}_physical_callback" for kind in sorted(supported_hooks)),
        ],
        "unsupported_events": sorted(
            {"member_join", "capability_change"} - supported_dynamic),
        "events": unknown,
        "validated": not unknown,
        "unknown_event_count": len(unknown),
        "claims": {"dynamic_spawn": False,
                   "capability_reconfiguration": False,
                   "physical_task_completion": False},
    }


def normalize_communication_schedule(schedule):
    """Validate evaluator-owned future channel changes without exposing them."""
    if schedule is None:
        return []
    if not isinstance(schedule, (list, tuple)):
        raise ValueError("communication schedule must be a list")
    rows = []
    previous = 0.0
    allowed_conditions = {"delay_s", "drop", "bytes_per_window", "window_s"}
    for index, item in enumerate(schedule):
        if not isinstance(item, dict) or set(item) - {"time_s", "conditions", "channel"}:
            raise ValueError("communication schedule entry has unknown fields")
        time_s = item.get("time_s")
        conditions = item.get("conditions")
        channel = item.get("channel")
        if (isinstance(time_s, bool) or not isinstance(time_s, (int, float))
                or not math.isfinite(float(time_s)) or float(time_s) < previous):
            raise ValueError("communication schedule times must be finite and monotonic")
        if (not isinstance(conditions, dict) or not conditions
                or set(conditions) - allowed_conditions):
            raise ValueError("communication schedule conditions are invalid")
        if channel is not None and (not isinstance(channel, str) or not channel):
            raise ValueError("communication schedule channel must be a nonempty string")
        previous = float(time_s)
        rows.append({"index": index, "time_s": previous,
                     "conditions": deepcopy(conditions), "channel": channel})
    return rows


def execution_contract(data, *, transport_mode, participant_enabled=None,
                       aerial_motion_mode="controller",
                       lifecycle_supported_events=()):
    """Reject a DAG that this executor cannot faithfully account for.

    Offline feasibility can existentially choose an eligible robot when a
    phase leaves ``preferred_agent`` unset.  A concrete rollout cannot: its
    allocation must be frozen in the episode so success is attributable to a
    reproducible policy/run.  Likewise, silently skipping unsupported DAG
    nodes would turn a partial rollout into a false positive.
    """
    if aerial_motion_mode not in {"controller", "kinematic"}:
        raise ValueError("aerial_motion_mode must be controller or kinematic")
    unsupported = sorted({node["skill_key"] for node in data["phase_graph"]["nodes"]}
                         - _SUPPORTED_SKILLS)
    if unsupported:
        raise ValueError(
            "executor does not support phase skills "
            f"{unsupported}; refusing to score a partial DAG")
    unassigned = sorted(node["id"] for node in data["phase_graph"]["nodes"]
                        if node["preferred_agent"] is None)
    if unassigned:
        raise ValueError(
            "executor requires an explicit preferred_agent for reproducible "
            f"allocation (nodes: {', '.join(unassigned)})")
    has_transport = any(node["skill_key"] in {"transport_object", "joint_transport"}
                        for node in data["phase_graph"]["nodes"])
    has_joint_transport = any(node["skill_key"] == "joint_transport"
                              for node in data["phase_graph"]["nodes"])
    lifecycle_contract = validate_runtime_fault_events(
        data, participant_enabled=participant_enabled,
        lifecycle_supported_events=lifecycle_supported_events)
    if data.get("fault_events") and participant_enabled is None:
        # Keep direct offline callers backwards-compatible while still using
        # the strict validator in the actual executor below.
        if data.get("benchmark_task_id") not in (None, "F-COV-01"):
            raise ValueError(
                "member_exit currently supports scene-neutral tasks and F-COV-01 only")
    elif data.get("fault_events") and data.get("scene_key", "factory") == "factory":
        if data.get("benchmark_task_id") not in (None, "F-COV-01"):
            raise ValueError(
                "member_exit currently supports scene-neutral tasks and F-COV-01 only")
    for node in data["phase_graph"]["nodes"]:
        from EAI.scenario_spec.validation import validate_node_timing
        validate_node_timing(node)
        if node.get("wait_yaw_rad") is not None:
            from EAI.hmrs_env.env_diy import catalog
            robot = next((item for item in data.get("robots", [])
                          if item["id"] == node["preferred_agent"]), None)
            controller = ((robot.get("controller") or {}).get("cfg")
                          or catalog.default_controller_cfg(robot["type"])) if robot else None
            supported = {("simple_car", "SIMPLE_CAR_PLANAR_CFG"), ("carter", "CARTER_DIFF_CFG"),
                         ("scout", "SCOUT_DIFF_CFG")}
            if robot is None or (robot["type"], controller) not in supported:
                raise ValueError(f"{node['id']}: wait_yaw_rad requires a supported stationary-turn controller")
        if node.get("scan_yaw_rad") is not None:
            from EAI.hmrs_env.env_diy import catalog
            robot = next((item for item in data.get("robots", [])
                          if item["id"] == node["preferred_agent"]), None)
            controller = ((robot.get("controller") or {}).get("cfg")
                          or catalog.default_controller_cfg(robot["type"])) if robot else None
            supported = {("iris", "PEGASUS_IRIS_POSITION_CFG"), ("pegasus", "PEGASUS_X4_POSITION_CFG")}
            if robot is None or (robot["type"], controller) not in supported:
                raise ValueError(f"{node['id']}: scan_yaw_rad requires a paired EAI goal_yaw controller")
        if node["skill_key"] != "joint_transport":
            continue
        team = node.get("agents") or []
        if len(team) != 2 or len(set(team)) != 2:
            raise ValueError(
                f"joint_transport node '{node['id']}' requires exactly two distinct agents")
        if node.get("preferred_agent") not in team:
            raise ValueError(
                f"joint_transport node '{node['id']}' preferred_agent must be one of agents")
    if has_transport and transport_mode != "kinematic":
        raise ValueError(
            "transport_object/joint_transport requires --transport-mode kinematic; physical "
            "grasping and transport are not implemented")
    has_aerial_scan = any(node["skill_key"] == "aerial_scan"
                          for node in data["phase_graph"]["nodes"])
    return {
        "tier": "kinematic_execution" if has_transport else "navigation_execution",
        "transport_mode": transport_mode if has_transport else "none",
        "physical_grasp": False,
        "arm_ik": False,
        "contact_dynamics": False,
        "payload_dynamics": False,
        "mass_affects_execution": False,
        "manipulation_model": "scripted_root_pose",
        "joint_transport_model": "idealized_shared_ownership" if has_joint_transport else "none",
        "aerial_scan_model": (
            "goal_navigation_measured_hover_and_camera_frame"
            if has_aerial_scan else "none"
        ),
        "aerial_motion_mode": aerial_motion_mode if has_aerial_scan else "none",
        "aerial_motion_model": (
            "bounded_kinematic_step_after_continuous_3d_admission"
            if has_aerial_scan and aerial_motion_mode == "kinematic"
            else "configured_robot_controller" if has_aerial_scan else "none"
        ),
        "aerial_scan_hover_confirmed": False,
        "skill_support": executor_capabilities(),
        "node_timing_version": 1,
        "node_timeout_clock": "simulation_time_since_ready_including_resource_wait",
        "contact_events_are_allowed": True,
        "deadlock_monitoring": "runtime progress watchdog (configured in the report)",
        "supports": (
            "closed-loop waypoint tracking and predicate evaluation"
            + (" with kinematic object relocation" if has_transport else "")
            + (" with goal navigation, measured hover dwell, and live camera frame evidence"
               if has_aerial_scan else "")
            + (" with idealized two-robot shared ownership" if has_joint_transport else "")
        ),
        "does_not_establish": [
            "physical grasp success",
            "arm reachability or collision-free motion",
            "object contact dynamics",
            "payload-mass effects on allocation, motion, stability, or energy",
            "contact-force safety or task-specific unsafe-contact classification",
            "aerial perception, image-based localization, or sensor fidelity",
            *(["flight dynamics, actuator limits, disturbance rejection, or dynamic-obstacle safety"]
              if has_aerial_scan and aerial_motion_mode == "kinematic" else []),
        ],
        "runtime_lifecycle": {
            "supported": [
                "member_exit_preloaded_replacement",
                *(f"{kind}_physical_callback"
                  for kind in sorted(set(lifecycle_supported_events))),
            ],
            "unsupported": sorted(
                {"member_join", "capability_change"}
                - set(lifecycle_supported_events)),
            "fault_contract": lifecycle_contract,
            "physical_completion_claimed_by_lifecycle": False,
        },
    }


def build_plan(data, occupancy, template, *, ground_tracking_reserve_m=None):
    """Build a reproducible, stateful plan for every supported DAG node.

    ``ground_tracking_reserve_m`` normally comes from the scene policy. A
    vertical probe may override it for its ground approach: the declared
    stair envelope is intentionally handled by the separate 3-D support
    protocol, and adding the ordinary tracking reserve to that envelope can
    close a valid pre-entry stance for wider legged robots.
    """
    from EAI.scenario_spec.geometry import static_blocked_grid, validate_ground_surface_access
    from EAI.scenario_spec.oracle import InflatedGrid

    validate_ground_surface_access(data)
    reserve_m = (tracking_reserve_for(data.get("scene_key"))
                 if ground_tracking_reserve_m is None
                 else float(ground_tracking_reserve_m))
    if not math.isfinite(reserve_m) or reserve_m < 0:
        raise ValueError("ground_tracking_reserve_m must be finite and nonnegative")
    blocked = static_blocked_grid(
        occupancy, data, ground_tracking_reserve_m=reserve_m)
    grids = {}

    def grid_for(radius):
        if radius not in grids:
            grids[radius] = InflatedGrid(occupancy, radius, blocked)
        return grids[radius]

    object_positions = {
        entity["id"]: tuple(entity["spawn_pose"]["position"])
        for entity in data["composition"]["entities"]
    }
    entities = {entity["id"]: entity for entity in data["composition"]["entities"]}
    targets = {target["id"]: target for target in data["composition"]["targets"]}

    def point_of(ref):
        if ref in object_positions:
            return object_positions[ref]
        for target in data["composition"]["targets"]:
            if target["id"] == ref:
                return tuple(target["position"])
        raise ValueError(f"unknown point {ref}")

    def planar_route_distance(start_point, route):
        points = [tuple(start_point[:2]), *(tuple(point[:2]) for point in route)]
        return sum(math.dist(first, second) for first, second in zip(points[:-1], points[1:]))

    robots = {r["id"]: r for r in data["robots"]}
    planned_positions = {
        robot_id: tuple(robot["spawn_pose"]["position"])
        for robot_id, robot in robots.items()
    }
    plan = []
    for node in _kahn_order(data["phase_graph"]["nodes"]):
        robot = robots[node["preferred_agent"]]
        footprint_radius = planning_radius(robot)
        route_planning_radius = footprint_radius
        grid = grid_for(route_planning_radius)
        start = planned_positions[robot["id"]]
        goal = point_of(node["target_ref"])

        def waypoints(start_xy, goal_xy, *, preserve_goal=False):
            start_xy, goal_xy = start_xy[:2], goal_xy[:2]
            path = grid.path(start_xy, goal_xy)
            if path is None:
                # start or goal may sit on an occupied cell (platform
                # projection, marker footprint); fall back to the nearest
                # passable cell for whichever blocked.
                for is_start, probe in ((True, start_xy), (False, goal_xy)):
                    nearest = grid.nearest_passable(probe)
                    if nearest is None:
                        continue
                    fixed = grid._occupancy.world(
                        (nearest[0] + 0.5) * grid.resolution,
                        (nearest[1] + 0.5) * grid.resolution)
                    if is_start:
                        path = grid.path(fixed, goal_xy)
                    else:
                        path = grid.path(start_xy, fixed)
                    if path is not None:
                        break
            if path is None:
                raise ValueError(f"{node['id']}: no route for {robot['id']}")
            return route_waypoints(grid, path, goal_xy, preserve_goal=preserve_goal)

        entry = {"node": node["id"], "agent": robot["id"], "skill": node["skill_key"],
                 "prerequisites": list(node["prerequisites"]), "waypoints": None,
                 "object": node.get("object_ref"), "target_ref": node["target_ref"],
                 "observation_ref": node.get("observation_ref") or node["target_ref"],
                 "goal": goal,
                 "phase": "wait", "carry_z": None, "release_z": None,
                 "interaction_standoff": None, "terminal_tolerance": None,
                 "arrival_predicate": ({"fn": "robot_visited_zone",
                                        "args": [robot["id"], node["target_ref"]]}
                                       if node["skill_key"] == "navigate"
                                       and node["target_ref"] in targets else None),
                 "pickup_goal": None, "agent_radius": footprint_radius,
                 "route_planning_radius_m": route_planning_radius,
                 "ground_tracking_reserve_m": reserve_m,
                 "path_waypoint_tolerance_m": grid.resolution / 2,
                 "team": list(node.get("agents") or []),
                 "recovery_for": node.get("recovery_for"),
                 "scan_goal": (scan_hover_goal(goal, scan_height_m=node.get("scan_height_m"))
                               if node["skill_key"] == "aerial_scan" else None),
                 "scan_yaw_rad": node.get("scan_yaw_rad"),
                 "wait_yaw_rad": node.get("wait_yaw_rad"),
                 "hover_elapsed_s": 0.0, "timeout_s": node.get("timeout_s", 60.0),
                 "duration_s": node.get("duration_s"), "resources": list(node.get("resources") or []),
                 "planned_ground_distance_m": 0.0}
        if node["skill_key"] == "wait":
            entry["waypoints"] = []
            plan.append(entry)
            continue
        if node["skill_key"] == "joint_transport":
            team = list(node["agents"])
            entry["agent"] = node["preferred_agent"]
            entry["team"] = team
            object_start = point_of(node["object_ref"])
            entry["pickup_goal"] = object_start
            team_starts = {member: planned_positions[member] for member in team}
            rebuild_transport_routes(entry, spec=data, occupancy=occupancy,
                                     member_positions=planned_positions,
                                     object_position=object_start)
            entry["planned_ground_distance_m"] = sum(
                planar_route_distance(team_starts[member], entry["team_to_object"][member])
                + planar_route_distance(entry["team_pickup_slots"][member],
                                        entry["team_to_target"][member])
                for member in team)
            entity = entities[node["object_ref"]]
            entry["carry_z"] = sum(
                _declared_carry_center_z(robots[member], entity) for member in team
            ) / len(team)
            carry_half = entity["size_m"][2] / 2
            entry["release_z"] = (targets[node["target_ref"]]["position"][2] + carry_half
                                  if node["target_ref"] in targets else
                                  entities[node["target_ref"]]["spawn_pose"]["position"][2])
            object_positions[node["object_ref"]] = goal
            for member in team:
                planned_positions[member] = entry["team_delivery_slots"][member]
        elif node["skill_key"] == "transport_object":
            object_start = point_of(node["object_ref"])
            entry["pickup_goal"] = object_start
            entry["to_object"] = waypoints(start, object_start)
            entry["to_target"] = waypoints(object_start, goal)
            entry["planned_ground_distance_m"] = (
                planar_route_distance(start, entry["to_object"])
                + planar_route_distance(object_start, entry["to_target"]))
            entity = entities[node["object_ref"]]
            carry_half = entity["size_m"][2] / 2
            planar_half_diagonal = math.hypot(
                entity["size_m"][0] / 2, entity["size_m"][1] / 2)
            entry["carry_z"] = _declared_carry_center_z(robot, entity)
            entry["interaction_standoff"] = (
                footprint_radius + planar_half_diagonal + 0.1)
            if node["target_ref"] in targets:
                entry["release_z"] = targets[node["target_ref"]]["position"][2] + carry_half
            else:
                entry["release_z"] = entities[node["target_ref"]]["spawn_pose"]["position"][2]
            object_positions[node["object_ref"]] = goal
        else:
            entry["waypoints"] = waypoints(
                start, goal, preserve_goal=entry["arrival_predicate"] is not None)
            if node["skill_key"] != "aerial_scan":
                entry["planned_ground_distance_m"] = planar_route_distance(
                    start, entry["waypoints"])
            if node["target_ref"] in entities:
                entity = entities[node["target_ref"]]
                planar_half_diagonal = math.hypot(
                    entity["size_m"][0] / 2, entity["size_m"][1] / 2)
                entry["terminal_tolerance"] = (
                    footprint_radius + planar_half_diagonal + 0.1)
            elif entry["arrival_predicate"] is not None:
                entry["terminal_tolerance"] = targets[node["target_ref"]]["radius_m"]
        if node["skill_key"] != "joint_transport":
            planned_positions[robot["id"]] = goal
        plan.append(entry)
    return plan


# Diagnostic tracks are part of the task contract, not a generic scene label.
# Keeping this mapping next to runtime admission prevents an indoor DAG from
# being accidentally run under another scene's evaluator.
_NON_FACTORY_DIAGNOSTIC_TRACKS = {
    "warehouse_nvd": ("W-DAG-01", "warehouse_benchmark"),
    "modern_house_nvd": ("MH-DAG-01", "modern_house_benchmark"),
}


def _validate_benchmark_task_spec(spec):
    """Validate a declared diagnostic track before simulator startup.

    Factory keeps its scene-owned validator because its tracks have distinct
    admission rules. The indoor DAGs share the scene-challenge compiler, so
    their static profile review is the appropriate runtime gate; geometry and
    physical evidence remain separate release checks.
    """
    task_id = spec.get("benchmark_task_id")
    if task_id is None:
        return None
    scene_key = spec.get("scene_key")
    if scene_key == "factory":
        from demo.factory_benchmark.tasks import validate_factory_scenario
        return validate_factory_scenario(spec, task_id)
    try:
        expected_task, package = _NON_FACTORY_DIAGNOSTIC_TRACKS[scene_key]
    except KeyError as exc:
        raise ValueError(
            f"benchmark_task_id {task_id!r} is unsupported for scene_key={scene_key!r}") from exc
    if task_id != expected_task:
        raise ValueError(
            f"scene_key={scene_key!r} requires benchmark_task_id={expected_task!r}, "
            f"got {task_id!r}")
    from demo.benchmark.scene_challenge import review_task_design
    owner = __import__(f"demo.{package}.scenario", fromlist=["scenario"])
    design = review_task_design(spec, owner.TASK_PROFILE, owner.TASK_BLUEPRINT)
    if not design["validated"]:
        raise ValueError(f"{task_id}: task design admission failed: {design['findings']}")
    return {"task_id": task_id, "validated": True, "design": design}


def main(argv=None, *, expected_scene=None, asset_check=None, participant_adapter=None,
         domain_evidence_provider=None, domain_adapter_id=None,
         lifecycle_callbacks=None, communication_contract=None,
         communication_schedule=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario", nargs="?")
    parser.add_argument("--scenario", dest="scenario_file", help="V2 episode JSON for this demo")
    parser.add_argument("--steps", type=int, default=0,
                        help="Max control steps (0 = auto from route length)")
    parser.add_argument("--speed", type=float, default=0.55)
    parser.add_argument("--k-turn", type=float, default=1.5)
    parser.add_argument("--waypoint-tol", type=float, default=0.35)
    parser.add_argument("--path-waypoint-tol", type=float, default=0.55,
                        help="Tolerance for intermediate grid-cell waypoints; final goals keep "
                             "--waypoint-tol or the manipulation stand-off")
    parser.add_argument("--stall-window", type=int, default=150,
                        help="Steps without measured progress before replanning; single transport uses waypoint completion")
    parser.add_argument("--max-replans", type=int, default=12,
                        help="Recovery replans allowed before a no-progress deadlock is reported")
    parser.add_argument("--transport-mode", choices=["none", "kinematic"], default="none",
                        help="Transport implementation. 'kinematic' relocates the object by root-pose "
                             "override; it is not physical grasp validation.")
    parser.add_argument(
        "--aerial-motion-mode", choices=["controller", "kinematic"],
        default=("kinematic" if expected_scene in {"warehouse_nvd", "modern_house_nvd"}
                 else "controller"),
        help="Aerial motion implementation. NVD demos default to a bounded kinematic baseline; "
             "controller runs the configured physical controller diagnostic.",
    )
    parser.add_argument("--device", default="cpu")
    display = parser.add_mutually_exclusive_group()
    display.add_argument("--gui", action="store_true",
                        help="Run with the Isaac Sim viewport instead of headless")
    display.add_argument("--headless", dest="gui", action="store_false")
    parser.set_defaults(gui=False)
    parser.add_argument("--camera-mode", choices=["active", "overview"], default="active",
                        help="GUI camera framing: chase the active robot or show the whole task")
    parser.add_argument("--follow", default="",
                        help="Robot id to follow in GUI mode (default: the currently active executor)")
    parser.add_argument("--report")
    parser.add_argument("--archive-camera-frames", action="store_true",
                        help="Archive RGB arrays, PNG previews and render-product camera parameters beside the report")
    parser.add_argument("--inventory-label-id", type=int, choices=range(50),
                        help="Factory printed-inventory-label experiment; the detector receives only sensor pixels")
    parser.add_argument("--inventory-label-state", choices=["visible", "covered", "absent"], default="visible")
    parser.add_argument("--inventory-dispatch-mode", choices=["none", "shared", "withheld", "delayed"], default="none")
    parser.add_argument("--inventory-message-delay-s", type=float, default=60.0)
    parser.add_argument("--inventory-dispatch-policy", choices=["immediate", "wait_for_message"],
                        default="immediate")
    parser.add_argument("--inventory-dispatch-wait-s", type=float, default=65.0)
    parser.add_argument("--participant-mode", choices=["none", "public_greedy"], default="none",
                        help="Scene-neutral public participant adapter for allocation, navigation, "
                             "transport-team selection and delivered-observation consumption")
    parser.add_argument("--execution-request", help=argparse.SUPPRESS)
    parser.add_argument("--wall-timeout-s", type=float, default=0.0,
                        help="Control-loop wall time budget; 0 disables it. Exhaustion writes a failure report.")
    parser.add_argument("--render-period-s", type=float, default=0.0,
                        help="Explicit benchmark render period, rounded up to control steps; 0 keeps the EAI default.")
    args = parser.parse_args(argv)
    if bool(args.scenario) == bool(args.scenario_file):
        parser.error("Supply exactly one scenario: positional or --scenario")
    args.scenario = args.scenario or args.scenario_file
    if args.archive_camera_frames and not args.report:
        parser.error("--archive-camera-frames requires --report")
    if args.inventory_label_id is not None and not args.archive_camera_frames:
        parser.error("inventory-label experiments require --archive-camera-frames and --report")
    if args.inventory_label_id is None and args.inventory_label_state != "visible":
        parser.error("--inventory-label-state requires --inventory-label-id")
    if args.inventory_dispatch_mode != "none" and args.inventory_label_id is None:
        parser.error("inventory dispatch requires an archived printed-label experiment")
    if not math.isfinite(args.inventory_message_delay_s) or args.inventory_message_delay_s < 0:
        parser.error("--inventory-message-delay-s must be finite and nonnegative")
    if not math.isfinite(args.inventory_dispatch_wait_s) or args.inventory_dispatch_wait_s < 0:
        parser.error("--inventory-dispatch-wait-s must be finite and nonnegative")
    if args.inventory_dispatch_mode == "none" and args.inventory_dispatch_policy != "immediate":
        parser.error("inventory dispatch policy requires an enabled dispatch mode")
    if participant_adapter is not None and args.inventory_dispatch_mode == "none" and args.participant_mode == "none":
        parser.error("a participant adapter requires an enabled inventory dispatch mode")
    if args.participant_mode != "none" and expected_scene == "factory":
        parser.error("--participant-mode is for non-Factory scene demos")
    if args.stall_window < 1:
        parser.error("--stall-window must be at least 1")
    if args.waypoint_tol <= 0 or args.path_waypoint_tol <= 0:
        parser.error("waypoint tolerances must be positive")
    if args.max_replans < 0:
        parser.error("--max-replans must be non-negative")
    if not math.isfinite(args.wall_timeout_s) or args.wall_timeout_s < 0:
        parser.error("--wall-timeout-s must be finite and non-negative")
    if not math.isfinite(args.render_period_s) or args.render_period_s < 0:
        parser.error("--render-period-s must be finite and non-negative")

    scenario_path = Path(args.scenario).resolve()
    execution_request = capture_request(
        scenario_path, {key: getattr(args, key) for key in RUNTIME_DEFAULTS})
    if args.execution_request:
        supplied_request = json.loads(Path(args.execution_request).read_text(encoding="utf-8"))
        if supplied_request.get("sha256") != execution_request["sha256"]:
            raise ValueError("execution request differs from pre-launch scenario, sources or options")
        execution_request = supplied_request
    elif args.report:
        common.write_json(str(Path(args.report).with_suffix(".request.json")), execution_request)

    # Do this before importing/loading the scenario so an unsupported runtime
    # lifecycle kind still gets a machine-readable unknown result even though
    # the published ScenarioSpec schema only accepts member_exit faults.
    raw_scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
    raw_lifecycle = raw_scenario.get("runtime_lifecycle") or {}
    auto_lifecycle_callbacks = (
        lifecycle_callbacks is None
        and args.participant_mode != "none"
        and isinstance(raw_lifecycle, dict)
        and (bool(raw_lifecycle.get("preloaded_reserve_agent_ids"))
             or any(isinstance(event, dict)
                    and event.get("kind") in {"member_exit", "capability_change"}
                    for event in raw_scenario.get("fault_events", ())))
    )
    lifecycle_supported_events = {
        kind for kind in (
            "prepare_preloaded_articulations", "member_join", "capability_change")
        if callable(getattr(lifecycle_callbacks, kind, None))
    }
    if auto_lifecycle_callbacks:
        lifecycle_supported_events.update({
            "prepare_preloaded_articulations", "member_join", "capability_change"})
    lifecycle_preflight = runtime_lifecycle_preflight(
        raw_scenario, participant_enabled=(args.participant_mode != "none"),
        lifecycle_supported_events=lifecycle_supported_events)
    if lifecycle_preflight["events"]:
        rejection = {
            "status": "unsupported_runtime_lifecycle_event",
            "runtime_lifecycle": lifecycle_preflight,
            "execution_validation": {
                "validated": False,
                "tier": "execution_failed",
                "physical_completion_claimed": False,
            },
        }
        if args.report:
            common.write_json(args.report, rejection)
        print(json.dumps(rejection, ensure_ascii=False))
        return 1

    from EAI.scenario_spec import load_scenario
    from EAI.scenario_spec.geometry import provider_map

    spec = load_scenario(str(scenario_path))
    if expected_scene is not None and spec.get("scene_key", "factory") != expected_scene:
        raise ValueError(f"This demo requires scene_key={expected_scene!r}")
    inventory_variant = None
    if args.inventory_label_id is not None:
        if spec.get("scene_key") != "factory" or spec.get("benchmark_task_id") != "F-INS-01":
            raise ValueError("inventory labels currently require Factory F-INS-01")
        from demo.factory_benchmark.inventory_scene import prepare_variant
        inventory_variant = prepare_variant(args.inventory_label_id, args.inventory_label_state)
        common.write_json(str(Path(args.report).with_suffix(".inventory_scene.json")), inventory_variant)
    if spec.get("benchmark_task_id") is not None:
        _validate_benchmark_task_spec(spec)
    if asset_check is not None:
        assets = asset_check()
        if not assets["validated"]:
            raise ValueError(f"Scene assets are incomplete; run this demo with --check-assets: {assets['missing']}")
    generic_participant = args.participant_mode != "none"
    domain_contract = None
    if generic_participant:
        from demo.benchmark.domain_evaluator import build_domain_contract
        # Freeze evaluator truth before participant allocation can mutate the
        # runtime preferred-agent fields used by the execution plan.
        domain_contract = build_domain_contract(spec)
    contract = execution_contract(
        spec, transport_mode=args.transport_mode,
        participant_enabled=generic_participant,
        aerial_motion_mode=args.aerial_motion_mode,
        lifecycle_supported_events=lifecycle_supported_events)
    from EAI.scenario_spec.geometry import ground_surface_exclusions
    contract["ground_surface_exclusions"] = ground_surface_exclusions(spec)
    contract["deadlock_monitoring"] = {
        "enabled": True,
        "criterion": "no measured waypoint distance/heading progress for navigation; bounded recovery replans",
        "distance_progress_m": WAYPOINT_PROGRESS_M,
        "heading_progress_rad": WAYPOINT_HEADING_PROGRESS_RAD,
        "transport_progress_criterion": "current-waypoint completion",
        "joint_transport_progress_criterion": "per-member measured waypoint distance/heading progress",
        "stall_window_steps": args.stall_window,
        "max_replans": args.max_replans,
    }
    contract["ground_route_tracking"] = {
        "physical_footprint_radius_source": "scenario_spec.oracle._robot_radius",
        "planning_reserve_m": PATH_TRACKING_RESERVE_M,
        "intermediate_waypoint_tolerance": "min(configured tolerance, occupancy resolution / 2)",
        "surface_validation": "per-control-step swept disk against declared ground exclusions",
    }
    occupancy = provider_map(scene_key=spec.get("scene_key", "factory"))
    from EAI.scenario_spec.schema import ROBOT_PROFILES
    has_aerial_routes = any(
        ROBOT_PROFILES[robot["type"]]["mobility"] == "aerial" for robot in spec.get("robots", ()))
    aerial_map = scene_aerial_voxel_map(spec.get("scene_key", "factory")) if has_aerial_routes else None
    contract["aerial_route_admission"] = {
        "enabled": has_aerial_routes,
        "model": "continuous_swept_aabb_against_voxel_product",
        "unknown_space_policy": "reject",
        "body_envelope": "isotropic scenario planning radius; not a measured aerodynamic envelope",
        "margin_m": 0.1,
        "limitations": ["No 3-D route search or prediction of moving obstacles",
                        "Voxel product completeness and RL tracking are not certified by route admission"],
    }
    template = planning_template(spec.get("scene_key", "factory"), occupancy)
    plan = build_plan(spec, occupancy, template)
    dispatch_enabled = args.inventory_dispatch_mode != "none"
    participant_session = None
    participant_controlled_nodes = set()
    if dispatch_enabled:
        from demo.benchmark.participant_runtime import ParticipantSession, interpret_dispatch_actions
        from demo.factory_benchmark.baseline_solver import BaselineDispatchAdapter
        from demo.factory_benchmark.challenge import build_participant_api, build_public_task_spec
        from demo.factory_benchmark.inventory_dispatch import (
            DISPATCH_NODE, VIEW_NODES, delivered_messages, dispatch_report, dispatch_trajectory,
            preflight_dispatch, retarget_entry, validate_dispatch_scene_bindings, view_messages,
        )
        preflight_dispatch(spec, occupancy, template)
        public_task = build_public_task_spec(
            spec, scenario_sha256=execution_request["scenario_sha256"])
        participant_api = build_participant_api(public_task)
        adapter = participant_adapter or BaselineDispatchAdapter(
            policy=args.inventory_dispatch_policy,
            max_wait_s=args.inventory_dispatch_wait_s,
        )
        participant_session = ParticipantSession(
            adapter=adapter, api=participant_api, task_spec=public_task)
        participant_controlled_nodes = set(
            public_task["participant"]["controlled_objectives"])
    if generic_participant:
        participant_session, public_task, participant_controlled_nodes = (
            create_scene_participant_session(
                spec,
                scenario_sha256=execution_request["scenario_sha256"],
                participant_adapter=participant_adapter,
            )
        )
    if (communication_contract is not None or communication_schedule is not None) \
            and not generic_participant:
        raise ValueError("runtime communication requires the public participant boundary")
    normalized_communication_schedule = normalize_communication_schedule(
        communication_schedule)
    communication_boundary = None
    if generic_participant:
        from demo.benchmark.communication import ParticipantCommunicationBoundary
        communication_boundary = ParticipantCommunicationBoundary(
            participant_session, contract=communication_contract)

    control_budget = {"mode": "explicit", "max_steps": args.steps}

    from simulator import SimulatorLaunchConfig, open_simulator_session

    ros_bridge_required = any(
        attachment.get("type") in {"navigation_io", "keyboard", "camera"}
        for robot in spec.get("robots", ())
        for attachment in robot.get("attachments", ())
        if isinstance(attachment, dict)
    )
    launcher_args = {"headless": not args.gui, "enable_cameras": True, "device": args.device,
                     "num_envs": 1, "seed": spec.get("seed", 0)}
    config = SimulatorLaunchConfig(
        env=None, scenario_file=str(scenario_path), num_envs=1, device=args.device,
        seed=spec.get("seed", 0), headless=not args.gui,
        enable_ros_bridge_extension=ros_bridge_required, disable_orsus_ros_env=True,
        app_launcher_args=launcher_args,
        env_cfg_hook=partial(configure_benchmark_environment, period_s=args.render_period_s,
                             inventory_variant=inventory_variant),
    )
    print("[executor] opening simulator session (including asset preflight)", flush=True)
    with open_simulator_session(config) as session, report_execution_exception(args.report), ExitStack() as capture_stack:
        if dispatch_enabled:
            validate_dispatch_scene_bindings(spec)
        import math as _m

        import torch

        from EAI.scenario_spec.runtime_evaluator import (
            PredicateEvaluator,
        )
        from EAI.scenario_spec.schema import ROBOT_PROFILES

        evaluator = PredicateEvaluator(spec)
        env = session.env
        base_env = session.base_env
        resolved_lifecycle_callbacks = lifecycle_callbacks
        if auto_lifecycle_callbacks:
            from demo.benchmark.isaac_env_lifecycle import build_env_lifecycle_callbacks

            resolved_lifecycle_callbacks = build_env_lifecycle_callbacks(base_env, spec)
        from EAI.hmrs_ros.aerial_sensor_suite import get_aerial_sensor_manager
        from EAI.hmrs_ros.simple_car_sensor_suite import get_simple_car_sensor_manager

        aerial_sensor_manager = get_aerial_sensor_manager(base_env)
        simple_car_sensor_manager = get_simple_car_sensor_manager(base_env)
        frame_archive = None
        camera_archive_setup = None
        if args.archive_camera_frames:
            from demo.benchmark.camera_capture import CameraFrameArchive
            frame_archive = capture_stack.enter_context(CameraFrameArchive(
                Path(args.report).with_suffix(".frames"), (aerial_sensor_manager, simple_car_sensor_manager)))
            before_reset = {name: robot.root_physx_view.get_root_transforms()[0].cpu().tolist()
                            for name, robot in base_env.scene.articulations.items()}
            env.reset()
            camera_archive_setup = {
                "reset_after_render_graph_setup": True,
                "native_pose_format": "world_xyz_quaternion_xyzw",
                "native_poses_before_reset": before_reset,
                "native_poses_after_reset": {
                    name: robot.root_physx_view.get_root_transforms()[0].cpu().tolist()
                    for name, robot in base_env.scene.articulations.items()},
            }
        observation_store = ObservationStore()
        # Domain semantics must arrive through a scene-owned adapter.  The
        # shared executor can collect poses and frames, but it must not infer
        # SKU, household identity, dock state, or resident access from a
        # successful scheduler node.
        from demo.benchmark.domain_adapters import SceneDomainEvidenceAdapter
        domain_adapter = SceneDomainEvidenceAdapter(
            spec["scene_key"], observation_store=observation_store,
            adapter_id=domain_adapter_id, collector=domain_evidence_provider)
        domain_runtime_binding = None
        if generic_participant:
            # Bind only the participant-visible packet.  The evaluator-owned
            # domain contract, including object-to-SKU bindings, remains out of
            # reach of the live detector/service adapter.
            domain_runtime_binding = domain_adapter.bind_runtime(
                base_env=base_env, occupancy=occupancy, public_task=public_task)
        marker_frame_results = []

        def record_marker_detections(observation_id, frame):
            if inventory_variant is None:
                return
            from demo.benchmark.marker_detection import detect_markers
            observation = observation_store.get(observation_id)
            result = detect_markers(frame, observation["sensor_config"]["camera_calibration"])
            result["source_observation_id"] = observation_id
            marker_frame_results.append(result)
            for index, detection in enumerate(result["detections"]):
                if detection["pose_valid"]:
                    observation_store.publish_detection(
                        detection_id=f"{observation_id}:marker:{index}", source_observation_id=observation_id,
                        detector_source=result["detector"], detector_version=result["detector_version"],
                        target_id=detection["target_id"], estimated_position_m=detection["estimated_position_m"],
                        confidence=None, timestamp_s=observation["timestamp_s"])

        observations_by_node = {}
        observation_ids_by_node = {}
        communication_schedule_index = 0

        def publish_runtime_observation(node_id, observation_id):
            if communication_boundary is None:
                return
            consumers = sorted(
                entry["node"] for entry in plan
                if node_id in entry.get("prerequisites", ()))
            communication_boundary.publish_observation(
                observation_store.get(observation_id), consumer_ids=consumers)

        def apply_communication_actions(actions, *, time_s):
            """Commit only message consumption outside a phase-ready turn."""
            for action in actions:
                if action["type"] != "consume_message":
                    execution_events.append({
                        "event": "participant_action_rejected",
                        "time_s": time_s, "action_id": action["action_id"],
                        "reason": "communication delivery turn accepts consume_message only",
                    })
                    continue
                payload = action["payload"]
                try:
                    consumed = communication_boundary.consume(
                        payload["message_id"], consumer_id=payload["consumer_id"],
                        time_s=time_s)
                    observation_store.consume(
                        payload["message_id"], consumer=payload["consumer_id"],
                        timestamp_s=time_s)
                except (KeyError, TypeError, ValueError) as error:
                    execution_events.append({
                        "event": "participant_message_consume_rejected",
                        "time_s": time_s, "action_id": action["action_id"],
                        "message_id": payload.get("message_id"),
                        "consumer_id": payload.get("consumer_id"),
                        "reason": str(error),
                    })
                    continue
                execution_events.append({
                    "event": "participant_message_consumed",
                    "time_s": time_s, "action_id": action["action_id"],
                    **consumed,
                })

        def communication_prerequisites_consumed(entry):
            if communication_boundary is None:
                return True
            required = [
                observations_by_node[prerequisite]
                for prerequisite in entry.get("prerequisites", ())
                if prerequisite in observations_by_node
            ]
            return all(any(
                event["consumer"] == entry["node"]
                for event in observation_store.get(observation_id)["consumed_by"])
                for observation_id in required)

        dispatch_messages = []
        dispatch_decision = None
        dispatch_visible_message_ids = set()
        dispatch_consumed_message_ids = set()
        dispatch_phase_ready_emitted = False
        from tools.scenario_generation.benchmark_mass_audit import audit_benchmark_masses
        mass_audit = audit_benchmark_masses(base_env)
        if not mass_audit["validated"]:
            rejection = {"status": "benchmark_mass_rejected", "benchmark_mass_audit": mass_audit,
                         "execution_validation": {"validated": False, "tier": "execution_failed"}}
            if args.report:
                common.write_json(args.report, rejection)
            print(json.dumps(rejection, ensure_ascii=False))
            return 1
        robot_specs = {robot["id"]: robot for robot in spec["robots"]}
        robot_types = {identifier: ROBOT_PROFILES.get(robot["type"], {}).get("mobility", "wheeled")
                       for identifier, robot in robot_specs.items()}
        navigation_bodies = {}
        for robot_spec in spec["robots"]:
            if robot_spec["type"] == "pepper":
                robot = base_env.scene.articulations[robot_spec["id"]]
                body_ids, _ = robot.find_bodies("base_link", preserve_order=True)
                if len(body_ids) != 1:
                    raise ValueError("Pepper navigation requires exactly one moving base_link")
                navigation_bodies[robot_spec["id"]] = body_ids[0]

        tracking_grids = {}

        def robot_state(name):
            robot = base_env.scene.articulations[name]
            return navigation_state(robot, body_index=navigation_bodies.get(name))

        runtime_lifecycle = None
        lifecycle_startup_evidence = None
        if generic_participant:
            from demo.benchmark.participant_runtime import IsaacRuntimeLifecycleBoundary
            missing_articulations = sorted(set(robot_specs) - set(base_env.scene.articulations))
            if missing_articulations:
                raise ValueError(
                    "runtime lifecycle is missing configured articulations: "
                    + ", ".join(missing_articulations))
            prepare_lifecycle = getattr(
                resolved_lifecycle_callbacks, "prepare_preloaded_articulations", None)
            if callable(prepare_lifecycle):
                expected_agents = set(robot_specs)
                lifecycle_startup_evidence = (
                    prepare_lifecycle(
                        active_agent_ids={
                            robot["id"] for robot in public_task["initial_fleet"]},
                        expected_actual_agent_ids=expected_agents,
                        expected_capability_versions={
                            agent_id: 1 for agent_id in expected_agents},
                    )
                )
            member_join_callback = getattr(
                resolved_lifecycle_callbacks, "member_join", None)
            capability_change_callback = getattr(
                resolved_lifecycle_callbacks, "capability_change", None)
            member_exit_deactivate_callback = getattr(
                resolved_lifecycle_callbacks,
                "deactivate_preloaded_articulation", None)
            runtime_lifecycle = IsaacRuntimeLifecycleBoundary(
                participant_session,
                available_agent_ids=set(robot_specs),
                actual_agent_ids=set(robot_specs),
                robot_types=robot_types,
                member_join_callback=(
                    member_join_callback if callable(member_join_callback) else None),
                capability_change_callback=(
                    capability_change_callback
                    if callable(capability_change_callback) else None),
                member_exit_deactivate_callback=(
                    member_exit_deactivate_callback
                    if callable(member_exit_deactivate_callback) else None),
            )

        def virtual_base_command(name, target_x, target_y):
            robot = base_env.scene.articulations[name]
            position_x, position_y, _, _ = robot_state(name)
            root_yaw = navigation_state(robot)[3]
            delta_x, delta_y = target_x - position_x, target_y - position_y
            return velocity_policy_command(
                distance=_m.hypot(delta_x, delta_y),
                heading_error=_m.atan2(delta_y, delta_x) - root_yaw,
                speed=args.speed, k_turn=0.0)

        def write_object_pose(ref, x, y, z, yaw):
            handle = base_env.scene.rigid_objects.get(f"scenario_{ref}")
            if handle is None:
                return
            pose = torch.zeros((1, 7), device=session.device)
            pose[0, 0], pose[0, 1], pose[0, 2] = x, y, z
            pose[0, 3] = _m.cos(yaw / 2)
            pose[0, 6] = _m.sin(yaw / 2)
            vel = torch.zeros((1, 6), device=session.device)
            ids = torch.tensor([0], device=session.device)
            handle.write_root_pose_to_sim(pose, env_ids=ids)
            handle.write_root_velocity_to_sim(vel, env_ids=ids)

        def object_state(ref):
            handle = base_env.scene.rigid_objects.get(f"scenario_{ref}")
            if handle is None:
                raise RuntimeError(f"missing rigid object for '{ref}'")
            state = handle.data.root_state_w[0]
            return float(state[0]), float(state[1]), float(state[2])

        def capture_live_camera_frame(agent):
            for manager in (aerial_sensor_manager, simple_car_sensor_manager):
                capture = getattr(manager, "capture_camera_frame", None)
                if not callable(capture):
                    continue
                frame = capture(agent)
                if frame is not None:
                    return frame, manager.__class__.__name__
            return None, None

        class KinematicCarryPhysics:
            """Temporarily make a scripted carried object non-interacting.

            The executor's kinematic tier is deliberately non-physical.  A
            dynamic collider teleported through a carrier would inject contact
            impulses into both the robot and the scene, so collision and
            gravity are disabled only while ownership is active and restored
            on release.
            """

            def __init__(self):
                self._saved = {}

            def attach(self, ref):
                if ref in self._saved:
                    raise RuntimeError(f"object '{ref}' is already carried")
                handle = base_env.scene.rigid_objects.get(f"scenario_{ref}")
                if handle is None:
                    raise RuntimeError(f"missing rigid object for '{ref}'")
                import omni.usd
                from pxr import PhysxSchema, Usd, UsdPhysics

                prim_paths = tuple(handle.root_physx_view.prim_paths)
                if len(prim_paths) != 1:
                    raise RuntimeError(
                        f"scripted carry requires one prim for '{ref}', got {prim_paths}")
                stage = omni.usd.get_context().get_stage()
                root = stage.GetPrimAtPath(prim_paths[0])
                if not root.IsValid():
                    raise RuntimeError(f"invalid carried-object prim {prim_paths[0]}")
                saved = {"collision": [], "gravity": None}
                for prim in Usd.PrimRange(root):
                    if prim.HasAPI(UsdPhysics.CollisionAPI):
                        attr = UsdPhysics.CollisionAPI(prim).GetCollisionEnabledAttr()
                        saved["collision"].append((attr, attr.Get()))
                        attr.Set(False)
                rigid = PhysxSchema.PhysxRigidBodyAPI(root)
                if not rigid:
                    rigid = PhysxSchema.PhysxRigidBodyAPI.Apply(root)
                gravity = rigid.GetDisableGravityAttr()
                saved["gravity"] = (gravity, gravity.Get())
                gravity.Set(True)
                self._saved[ref] = saved

            def release(self, ref):
                saved = self._saved.pop(ref, None)
                if saved is None:
                    raise RuntimeError(f"object '{ref}' is not currently carried")
                for attr, value in saved["collision"]:
                    attr.Set(True if value is None else value)
                gravity, value = saved["gravity"]
                gravity.Set(False if value is None else value)

            def restore_all(self):
                for ref in tuple(self._saved):
                    self.release(ref)

        carry_physics = KinematicCarryPhysics()

        def sample_positions():
            pos = {n[9:]: (float(b.data.root_state_w[0][0]), float(b.data.root_state_w[0][1]))
                   for n, b in base_env.scene.rigid_objects.items() if n.startswith("scenario_")}
            pos.update({n: robot_state(n)[:2] for n in base_env.scene.articulations})
            return pos

        pose_trajectory = []

        def record_sample(step):
            positions = sample_positions()
            pose_trajectory.append({"step": step, "positions": positions,
                                    "robot_poses_xyzyaw": {
                                        name: robot_state(name)
                                        for name in base_env.scene.articulations}})
            return evaluator.trajectory_step(step, positions)

        def replan(entry, x, y):
            from EAI.scenario_spec.geometry import static_blocked_grid
            from EAI.scenario_spec.oracle import InflatedGrid
            # A recovery route must honour the same static/kinematic barriers
            # that the original feasibility certificate used.
            grid = InflatedGrid(
                occupancy, entry["route_planning_radius_m"],
                static_blocked_grid(
                    occupancy, spec,
                    ground_tracking_reserve_m=entry["ground_tracking_reserve_m"]),
            )
            # Execution (contacts, shoving) can push a robot into cells the
            # inflated grid considers impassable. Escape to the nearest
            # passable cell first so replanning always has a legal start.
            start = (x, y)
            if not grid.passable(*grid.cell(x, y)):
                s = grid.nearest_passable(start)
                if s is None:
                    return False
                start = grid._occupancy.world((s[0] + .5) * grid.resolution,
                                              (s[1] + .5) * grid.resolution)
            route_goal = (entry["pickup_goal"]
                          if entry["skill"] == "transport_object"
                          and entry["phase"] == "to_object"
                          else entry["goal"])
            route_goal = route_goal[:2]
            path = grid.path(start, route_goal)
            if path is None:
                n = grid.nearest_passable(route_goal)
                if n is None:
                    return False
                g = grid._occupancy.world((n[0] + .5) * grid.resolution,
                                          (n[1] + .5) * grid.resolution)
                path = grid.path(start, g)
            if path is None:
                return False
            fresh = route_waypoints(
                grid, path, route_goal, preserve_goal=entry["arrival_predicate"] is not None)
            if entry["skill"] == "transport_object":
                if entry["phase"] == "to_object":
                    entry["to_object"] = fresh
                else:
                    entry["to_target"] = fresh
            else:
                entry["waypoints"] = fresh
            entry.pop("aerial_route_admitted_for_agent", None)
            return True

        gui_cam = None
        if args.gui:
            try:
                import omni.kit.viewport.utility as _vp_util
                _vp = _vp_util.get_active_viewport()
                from pxr import Gf as _Gf, UsdGeom as _UsdGeom
                import omni.usd as _omni_usd
                _stage = _omni_usd.get_context().get_stage()
                if args.camera_mode == "overview":
                    points = [tuple(robot["spawn_pose"]["position"][:2]) for robot in spec["robots"]]
                    points += [tuple(entity["spawn_pose"]["position"][:2])
                               for entity in spec["composition"]["entities"]]
                    points += [tuple(target["position"][:2])
                               for target in spec["composition"]["targets"]]
                    xs, ys = zip(*points)
                    center_x = (min(xs) + max(xs)) / 2
                    center_y = (min(ys) + max(ys)) / 2
                    span = max(max(xs) - min(xs), max(ys) - min(ys), 12.0)
                    camera_path = "/World/EaiTaskOverviewCamera"
                    camera = _UsdGeom.Camera.Define(_stage, camera_path)
                    xform = _UsdGeom.Xformable(camera.GetPrim())
                    xform.ClearXformOpOrder()
                    xform.AddTranslateOp().Set(
                        _Gf.Vec3d(center_x, center_y, max(24.0, span * 1.6)))
                    camera.CreateFocalLengthAttr().Set(20.0)
                    camera.CreateClippingRangeAttr().Set(_Gf.Vec2f(0.1, 2000.0))
                    _vp.set_active_camera(camera_path)
                    session.simulation_app.update()
                    gui_cam = {"vp": _vp, "prim": None, "last": "overview", "tick": 0}
                    print("[executor] GUI mode: fixed overview camera shows the whole task")
                else:
                    _cam_prim = _stage.GetPrimAtPath("/OmniverseKit_Persp")
                    gui_cam = {"vp": _vp, "prim": _cam_prim, "last": None, "tick": 0}
                    print("[executor] GUI mode: chase camera will follow the active robot")
            except Exception as _exc:
                print(f"[executor] GUI camera unavailable: {_exc}")
                gui_cam = None

        done_ids = set()
        participant_ready = set()
        carrying = {}
        execution_events = []

        def process_domain_frame(observation_id, frame, *, node_id, time_s):
            outcome = domain_adapter.process_frame(
                observation_id=observation_id, frame=frame,
                node_id=node_id, time_s=time_s)
            execution_events.append({
                "event": "domain_frame_processed",
                "node": node_id,
                "observation_id": observation_id,
                "time_s": time_s,
                "status": outcome["status"],
                "detection_ids": outcome.get("detection_ids", []),
                **({"reason": outcome["reason"]} if outcome.get("reason") else {}),
            })
            return outcome

        def process_domain_event(event_type, *, node_id, time_s):
            outcome = domain_adapter.process_event(
                event_type=event_type, node_id=node_id, time_s=time_s)
            execution_events.append({
                "event": "domain_runtime_event_processed",
                "domain_event_type": event_type,
                "node": node_id,
                "time_s": time_s,
                "status": outcome["status"],
                **({"reason": outcome["reason"]} if outcome.get("reason") else {}),
            })
            return outcome

        stall = {e["node"]: {"step": 0, "ll": -1} for e in plan}
        replans = {e["node"]: 0 for e in plan}
        deadlocks = []
        from EAI.scenario_spec.geometry import ground_surface_swept_disk_clearance
        ground_tracking = {
            name: {"physical_radius_m": planning_radius(robot_specs[name]),
                   "minimum_swept_clearance_m": None, "surface_id": None}
            for name in robot_specs if robot_types.get(name) != "aerial"
        }
        previous_ground_positions = {
            name: robot_state(name)[:2] for name in ground_tracking
        }
        ground_tracking_violations = []
        physics_state_failure = None
        trajectory = [record_sample(0)]
        steps_taken = 0
        all_node_ids = {e["node"] for e in plan}
        control_dt = float(base_env.step_dt)
        control_step_evidence = ControlStepEvidence(control_dt)
        render_period_s = float(session.env_cfg.sim.dt) * session.env_cfg.sim.render_interval
        # Auto step budget uses the measured control period and the authored
        # sequential witness. The deadlock reserve remains separate from
        # nominal ground, aerial, hover, and wait work.
        if args.steps <= 0:
            ground_route_cells = sum(
                len(entry.get("waypoints") or []) + len(entry.get("to_object") or [])
                + len(entry.get("to_target") or [])
                + sum(len(route) for route in (entry.get("team_to_object") or {}).values())
                + sum(len(route) for route in (entry.get("team_to_target") or {}).values())
                for entry in plan if entry["skill"] != "aerial_scan")
            aerial_positions = {
                robot["id"]: tuple(robot["spawn_pose"]["position"])
                for robot in spec["robots"]}
            aerial_distance_m = 0.0
            aerial_node_count = 0
            for entry in plan:
                if entry["skill"] != "aerial_scan":
                    continue
                points = aerial_route_points(entry, aerial_positions[entry["agent"]])
                aerial_distance_m += sum(
                    math.dist(start, end) for start, end in zip(points[:-1], points[1:]))
                aerial_positions[entry["agent"]] = tuple(points[-1])
                aerial_node_count += 1
            control_budget = {"mode": "auto", **auto_step_budget(
                route_cells=ground_route_cells,
                stall_window=args.stall_window,
                max_replans=args.max_replans,
                ground_distance_m=sum(
                    float(entry["planned_ground_distance_m"]) for entry in plan),
                ground_speed_m_s=args.speed,
                aerial_distance_m=aerial_distance_m,
                aerial_hover_s=0.5 * aerial_node_count,
                declared_hold_s=sum(
                    float(entry.get("duration_s") or 0.0)
                    for entry in plan if entry["skill"] == "wait"),
                control_dt_s=control_dt,
            )}
            args.steps = control_budget["max_steps"]
            print(f"[executor] auto budget: {control_budget}")
        scheduler = EpisodeScheduler(plan)
        fault_events = sorted(spec.get("fault_events", []), key=lambda item: (item["time_s"], item["id"]))
        next_fault_index = 0
        failed_agents = set()
        failed_nodes = set()
        recovered_nodes = set()
        runtime_lifecycle_failed = False
        aerial_holds = {name: robot_state(name)[:3] for name, kind in robot_types.items() if kind == "aerial"}
        kinematic_aerial_targets = dict(aerial_holds)
        kinematic_aerial_next_poses = {}
        aerial_admission_events = []
        aerial_tracking_violations = []
        aerial_previous_positions = {}

        def aerial_obstacle_boxes():
            boxes = []
            for entity in spec["composition"]["entities"]:
                handle = base_env.scene.rigid_objects.get(f"scenario_{entity['id']}")
                if handle is None:
                    position = entity["spawn_pose"]["position"]
                    rotation = entity["spawn_pose"]["rotation"]
                else:
                    state = handle.data.root_state_w[0]
                    position, rotation = state[:3].tolist(), state[3:7].tolist()
                boxes.append(oriented_box_aabb(position, rotation, entity["size_m"]))
            return boxes

        def mark_node_done(entry):
            done_ids.update(scheduler.satisfied_nodes)
            recovered_nodes.update(scheduler.recovered_by)
            if generic_participant:
                process_domain_event(
                    "node_completed", node_id=entry["node"],
                    time_s=current_time_s)
            if dispatch_enabled and entry["node"] in VIEW_NODES:
                dispatch_messages.extend(view_messages(
                    marker_frame_results, observation_store.report()["observations"],
                    node=entry["node"], published_at_s=current_time_s))

        def hold_aerial(name, position):
            if args.aerial_motion_mode == "kinematic":
                kinematic_aerial_targets[name] = tuple(float(value) for value in position)
            base_env.set_command(name, "goal_position", torch.tensor(
                [position], device=session.device, dtype=torch.float32))

        def prepare_kinematic_aerial_motion():
            if args.aerial_motion_mode != "kinematic":
                return
            kinematic_aerial_next_poses.clear()
            for name, goal in kinematic_aerial_targets.items():
                robot = base_env.scene.articulations[name]
                next_position = kinematic_aerial_step(
                    robot_state(name)[:3], goal, control_dt)
                kinematic_aerial_next_poses[name] = torch.cat((
                    torch.tensor([next_position], device=session.device, dtype=torch.float32),
                    robot.data.root_quat_w[:1].clone(),
                ), dim=1)

        def apply_kinematic_aerial_motion():
            if args.aerial_motion_mode != "kinematic":
                return
            ids = torch.tensor([0], device=session.device)
            for name, pose in kinematic_aerial_next_poses.items():
                robot = base_env.scene.articulations[name]
                robot.write_root_pose_to_sim(pose, env_ids=ids)
                robot.write_root_velocity_to_sim(
                    torch.zeros((1, 6), device=session.device, dtype=torch.float32),
                    env_ids=ids,
                )
                base_env.set_command(name, "goal_position", pose[:, :3])

        def command_aerial_goal(entry, position, goal):
            # A waypoint can be consumed within its XY tolerance before the
            # altitude settles. Recheck the actual segment being commanded,
            # rather than assuming the drone reached the planned 3-D vertex.
            agent = entry["agent"]
            radius = planning_radius(robot_specs[agent])
            decision = resolve_aerial_command(
                aerial_map, position, goal,
                queued_waypoints=entry.get("aerial_detour_queue") or (),
                half_extents_m=(radius, radius, radius), margin_m=0.1,
                obstacle_aabbs=aerial_obstacle_boxes(),
                waypoint_tolerance_m=max(0.2, radius))
            entry["aerial_detour_queue"] = decision["queued_waypoints"]
            commanded_goal = decision["command_goal"]
            admission = decision["route"]
            if not admission["admitted"]:
                event = {"event": "aerial_command_not_admitted", "node": entry["node"],
                         "agent": agent, "step": step - 1, "time_s": current_time_s,
                         "command_goal": list(commanded_goal), **admission}
                aerial_admission_events.append(event)
                execution_events.append(event)
                scheduler.fail(entry["node"], "aerial_command_not_admitted")
                deadlocks.append({"node": entry["node"], "agent": agent,
                                  "reason": "aerial_command_not_admitted", "admission": admission})
                hold_aerial(agent, position)
                return False
            hold_aerial(agent, commanded_goal)
            return True

        print("[executor] freezing runtime assets and effective conditions", flush=True)
        runtime_snapshot = capture_runtime(
            session, occupancy, plan,
            input_file_hashes=getattr(aerial_map, "input_file_hashes", {}))
        if args.report:
            common.write_json(str(Path(args.report).with_suffix(".runtime.json")), runtime_snapshot)
        loop_started = time.monotonic()
        last_progress_log = loop_started
        wall_time_exhausted = False
        print(f"[executor] control loop started: dt={control_dt}, max_steps={args.steps}", flush=True)
        for step in range(1, args.steps + 1):
            commands = {a: torch.zeros((session.num_envs, 3), device=session.device)
                        for a in session.possible_agents}
            current_time_s = (step - 1) * control_dt
            scheduler.set_time(current_time_s)
            while (communication_schedule_index < len(normalized_communication_schedule)
                   and normalized_communication_schedule[communication_schedule_index]["time_s"]
                   <= current_time_s + 1e-9):
                change = normalized_communication_schedule[communication_schedule_index]
                communication_schedule_index += 1
                communication_boundary.set_conditions(
                    change["conditions"], time_s=change["time_s"],
                    channel=change["channel"])
            if communication_boundary is not None:
                delivered = communication_boundary.advance(current_time_s)
                if delivered:
                    apply_communication_actions(
                        participant_session.request_actions(), time_s=current_time_s)
            wall_elapsed = time.monotonic() - loop_started
            if args.wall_timeout_s and wall_elapsed >= args.wall_timeout_s:
                wall_time_exhausted = True
                scheduler.cancel_remaining("episode_wall_time_exhausted")
                trajectory.append(record_sample(steps_taken))
                break
            if time.monotonic() - last_progress_log >= 10.0:
                last_progress_log = time.monotonic()
                print(json.dumps({"executor_step": step, "simulation_time_s": current_time_s,
                                  "wall_elapsed_s": round(wall_elapsed, 2),
                                  "nodes": {node: state["status"] for node, state in scheduler.states.items()},
                                  "joint_progress": {entry["node"]: {
                                      "phase": entry["phase"],
                                      "remaining_waypoints": {
                                          member: len(route) for member, route in
                                          (entry.get("team_to_object") if entry["phase"] == "joint_to_object"
                                           else entry.get("team_to_target") or {}).items()}}
                                      for entry in plan if entry["skill"] == "joint_transport"
                                      and scheduler.states[entry["node"]]["status"] == "running"}}),
                      flush=True)
            while next_fault_index < len(fault_events) and (
                    fault_events[next_fault_index]["time_s"] <= current_time_s + 1e-9):
                fault = fault_events[next_fault_index]
                next_fault_index += 1
                agent = fault["agent"]
                affected_entries = [
                    entry for entry in plan
                    if agent in (entry["team"] or [entry["agent"]])
                    and scheduler.states[entry["node"]]["status"] not in TERMINAL_STATES
                ]
                affected_nodes = [entry["node"] for entry in affected_entries]
                affected_statuses = {node: scheduler.states[node]["status"] for node in affected_nodes}
                runtime_lifecycle_result = None
                if generic_participant:
                    participant_affected_nodes = [
                        node_id for node_id in affected_nodes
                        if node_id in participant_controlled_nodes
                    ]
                    from demo.benchmark.participant_runtime import RuntimeLifecycleUnsupported
                    try:
                        runtime_lifecycle_result = runtime_lifecycle.apply_event(
                            fault, affected_node_ids=participant_affected_nodes,
                            pose_reader=robot_state)
                    except RuntimeLifecycleUnsupported as error:
                        runtime_lifecycle_failed = True
                        runtime_lifecycle_result = error.result
                        execution_events.append({
                            "step": step, "time_s": current_time_s,
                            **runtime_lifecycle_result,
                        })
                        break
                if fault["kind"] == "member_join":
                    execution_events.append({
                        "step": step, "event": "member_join",
                        "fault_id": fault["id"], "agent": agent,
                        "affected_nodes": [], "time_s": current_time_s,
                        "runtime_lifecycle": runtime_lifecycle_result,
                    })
                    continue
                interruption_kind = fault["kind"]
                if interruption_kind == "member_exit":
                    failed_agents.add(agent)
                # Ownership may only change through pickup/release events: an
                # interrupted assignment must drop an object before the node
                # can fail or be reassigned.
                entities_by_id = {
                    entity["id"]: entity for entity in spec["composition"]["entities"]}
                for release in plan_lifecycle_release(
                        affected_entries, affected_agent=agent, carrying=carrying,
                        reason=interruption_kind):
                    ox, oy, oz = object_state(release["object"])
                    drop_z = entities_by_id[release["object"]]["spawn_pose"]["position"][2]
                    carry_physics.release(release["object"])
                    carrying.pop(release["object"], None)
                    write_object_pose(release["object"], ox, oy, drop_z, 0.0)
                    execution_events.append({
                        "step": step, "event": "forced_release", "time_s": current_time_s,
                        "fault_id": fault["id"], **release,
                        "position": [round(ox, 3), round(oy, 3)],
                        "object_position": [round(ox, 3), round(oy, 3), round(drop_z, 3)],
                        "carry_position_z_m": round(oz, 3),
                    })
                for node_id in affected_nodes:
                    scheduler.fail(node_id, f"{interruption_kind}:{agent}")
                    failed_nodes.add(node_id)
                execution_events.append({
                    "step": step,
                    "event": interruption_kind,
                    "fault_id": fault["id"],
                    "agent": agent,
                    "replacement_agent": fault.get("replacement_agent"),
                    "affected_nodes": affected_nodes,
                    "affected_node_statuses": affected_statuses,
                    "time_s": current_time_s,
                    "runtime_lifecycle": runtime_lifecycle_result,
                })
                if generic_participant:
                    participant_actions = participant_session.request_actions()
                    reassignments = [action for action in participant_actions if action["type"] == "reassign"]
                    for action in reassignments:
                        target_node = action["payload"]["node_id"]
                        target_entry = next((candidate for candidate in plan
                                             if candidate["node"] == target_node), None)
                        if target_entry is not None and scheduler.states[target_node]["status"] in FAILURE_STATES:
                            target_entry = next((candidate for candidate in plan
                                                 if candidate.get("recovery_for") == target_node
                                                 and scheduler.states[candidate["node"]]["status"] not in TERMINAL_STATES),
                                                target_entry)
                        if target_entry is None:
                            continue
                        from demo.benchmark.participant_runtime import (
                            apply_scene_assignment_action,
                            replacement_assignment_matches,
                        )
                        mapped_action = action
                        if target_entry["node"] != target_node:
                            mapped_action = dict(action)
                            mapped_action["payload"] = dict(action["payload"])
                            mapped_action["payload"]["node_id"] = target_entry["node"]
                        replacement_agent = fault.get("replacement_agent")
                        replacement_match = (
                            replacement_assignment_matches(
                                target_entry,
                                agent_ids=action["payload"]["agent_ids"],
                                replacement_agent=replacement_agent)
                            if interruption_kind == "member_exit" else True)
                        if not replacement_match:
                            execution_events.append({
                                "step": step, "event": "participant_reassign_rejected",
                                "source_fault_id": fault["id"], "node": target_entry["node"],
                                "agent_ids": list(action["payload"]["agent_ids"]),
                                "action_id": action["action_id"], "time_s": current_time_s,
                                "reason": "assignment does not use the declared replacement_agent",
                                "replacement_agent": replacement_agent,
                                "replacement_match": False,
                            })
                            continue
                        try:
                            apply_scene_assignment_action(mapped_action, entry=target_entry,
                                                          task_spec=public_task,
                                                          carried_objects=frozenset(carrying),
                                                          active_agent_ids=runtime_lifecycle.active_agent_ids)
                        except ValueError as error:
                            execution_events.append({
                                "step": step, "event": "participant_reassign_rejected",
                                "source_fault_id": fault["id"], "node": target_entry["node"],
                                "agent_ids": list(action["payload"]["agent_ids"]),
                                "action_id": action["action_id"], "time_s": current_time_s,
                                "reason": str(error),
                            })
                            continue
                        execution_events.append({
                            "step": step, "event": "participant_reassign",
                            "source_fault_id": fault["id"], "node": target_entry["node"],
                            "agent_ids": list(action["payload"]["agent_ids"]),
                            "action_id": action["action_id"], "time_s": current_time_s,
                            "replacement_agent": replacement_agent,
                            "replacement_match": True,
                        })
            if runtime_lifecycle_failed:
                scheduler.cancel_remaining("unsupported_runtime_lifecycle_event")
                for command in commands.values():
                    command.zero_()
                for name in aerial_holds:
                    hold_aerial(name, robot_state(name)[:3])
                carry_physics.restore_all()
                carrying.clear()
                prepare_kinematic_aerial_motion()
                step_with_control_evidence(
                    env, commands, base_env.scene.articulations,
                    control_step_evidence, step=step)
                apply_kinematic_aerial_motion()
                steps_taken = step
                trajectory.append(record_sample(step))
                break
            active_nodes = set(scheduler.advance((step - 1) * control_dt))
            done_ids.update(scheduler.satisfied_nodes)
            active_agents = {member for entry in plan if entry["node"] in active_nodes
                             for member in entry["team"] or [entry["agent"]]}
            for name in aerial_holds:
                if name in active_agents:
                    aerial_holds[name] = robot_state(name)[:3]
                else:
                    hold_aerial(name, aerial_holds[name])
            if any(state["status"] == "timed_out" for state in scheduler.states.values()):
                scheduler.cancel_remaining("episode_node_timeout")
                for name in aerial_holds:
                    hold_aerial(name, robot_state(name)[:3])
                carry_physics.restore_all()
                carrying.clear()
                prepare_kinematic_aerial_motion()
                step_with_control_evidence(
                    env, commands, base_env.scene.articulations,
                    control_step_evidence, step=step)
                apply_kinematic_aerial_motion()
                steps_taken = step
                trajectory.append(record_sample(step))
                break
            for entry in plan:
                if entry["node"] not in active_nodes:
                    continue
                if not communication_prerequisites_consumed(entry):
                    continue
                if (generic_participant and entry["node"] in participant_controlled_nodes
                        and entry["node"] not in participant_ready):
                    from demo.benchmark.participant_runtime import (
                        apply_scene_assignment_action,
                        apply_scene_navigation_action,
                        eligible_public_agents,
                    )
                    eligible_agents = eligible_public_agents(
                        public_task, node_id=entry["node"], excluded=failed_agents,
                        active_agent_ids=(runtime_lifecycle.active_agent_ids
                                          if runtime_lifecycle is not None else None))
                    participant_session.emit("phase_ready", time_s=current_time_s, payload={
                        "node_id": entry["node"], "eligible_agent_ids": eligible_agents,
                    })
                    actions = participant_session.request_actions()
                    assignments = [action for action in actions if action["type"] in {"allocate", "reassign"}]
                    navigation = [action for action in actions if action["type"] == "navigate_goal"]
                    if entry["skill"] in {"transport_object", "joint_transport"}:
                        if navigation:
                            # The delivery target is task semantics, not a
                            # participant choice; ignore-and-run would silently
                            # score a different task than the one published.
                            scheduler.fail(entry["node"], "participant_action_rejected")
                            execution_events.append({
                                "step": step, "time_s": current_time_s,
                                "event": "participant_action_rejected",
                                "node": entry["node"],
                                "action_id": navigation[0]["action_id"],
                                "reason": "transport objectives do not accept navigate_goal",
                            })
                            participant_ready.add(entry["node"])
                            continue
                        try:
                            if assignments:
                                apply_scene_assignment_action(
                                    assignments[0], entry=entry, task_spec=public_task,
                                    carried_objects=frozenset(carrying),
                                    active_agent_ids=runtime_lifecycle.active_agent_ids)
                            rebuild = rebuild_transport_routes(
                                entry, spec=spec, occupancy=occupancy,
                                member_positions={
                                    member: robot_state(member)[:2]
                                    for member in (entry["team"] or [entry["agent"]])},
                                object_position=object_state(entry["object"])[:2])
                        except ValueError as error:
                            scheduler.fail(entry["node"], "participant_route_unavailable")
                            deadlocks.append({
                                "node": entry["node"], "agent": entry["agent"],
                                "phase": entry["phase"], "step": step,
                                "reason": "participant_route_unavailable",
                                "detail": str(error),
                            })
                            continue
                        execution_events.append({
                            "step": step, "time_s": current_time_s,
                            "event": "participant_transport_routes_rebuilt",
                            "participant_action_id": entry.get("participant_action_id"),
                            **rebuild,
                        })
                    else:
                        if assignments:
                            apply_scene_assignment_action(
                                assignments[0], entry=entry, task_spec=public_task,
                                active_agent_ids=runtime_lifecycle.active_agent_ids)
                        if navigation:
                            x0, y0 = robot_state(entry["agent"])[:2]
                            apply_scene_navigation_action(
                                navigation[0], entry=entry, task_spec=public_task,
                                active_agent_ids=runtime_lifecycle.active_agent_ids)
                            entry["team"] = [entry["agent"]]
                            entry["route_planning_radius_m"] = planning_radius(robot_specs[entry["agent"]])
                            entry["ground_tracking_reserve_m"] = tracking_reserve_for(spec.get("scene_key"))
                        if entry.get("arrival_predicate") is not None:
                            rebind_arrival_predicate(entry)
                        if navigation:
                            replan(entry, x0, y0)
                    participant_ready.add(entry["node"])
                agent = entry["agent"]
                if agent in failed_agents:
                    continue
                x, y, z, yaw = robot_state(agent)
                st = stall[entry["node"]]

                was_waiting = entry["phase"] == "wait"
                if was_waiting and dispatch_enabled and entry["node"] == DISPATCH_NODE:
                    ready_at_s = scheduler.states[entry["node"]]["started_at_s"]
                    if not dispatch_phase_ready_emitted:
                        participant_session.emit("phase_ready", time_s=ready_at_s, payload={
                            "node_id": entry["node"], "eligible_agent_ids": [agent],
                        })
                        dispatch_phase_ready_emitted = True
                    visible_messages, channel_ledger = delivered_messages(
                        dispatch_messages, mode=args.inventory_dispatch_mode,
                        delay_s=args.inventory_message_delay_s, time_s=current_time_s)
                    visible_by_id = {message["message_id"]: message for message in visible_messages}
                    for message_id in sorted(visible_by_id.keys() - dispatch_visible_message_ids):
                        message = visible_by_id[message_id]
                        participant_session.emit("observation_message", time_s=current_time_s, payload={
                            "message_id": message_id,
                            "producer_id": message["producer"],
                            "published_at_s": message["published_at_s"],
                            "valid_until_s": message["valid_until_s"],
                            "estimate": message["estimate"],
                        })
                        dispatch_visible_message_ids.add(message_id)
                    participant_session.emit("tick", time_s=current_time_s, payload={
                        "robot_states": {
                            name: {"position_m": list(robot_state(name)[:3]),
                                   "yaw_rad": robot_state(name)[3]}
                            for name in base_env.scene.articulations
                        },
                        "phase_states": {
                            name: state["status"] for name, state in scheduler.states.items()
                        },
                        "resource_states": dict(scheduler.reservations),
                    })
                    participant_actions = participant_session.request_actions()
                    interpreted = interpret_dispatch_actions(
                        participant_actions, node_id=entry["node"], agent_id=agent,
                        visible_message_ids=set(visible_by_id),
                        consumed_message_ids=dispatch_consumed_message_ids)
                    dispatch_consumed_message_ids = interpreted["consumed_message_ids"]
                    if interpreted["navigate_action"] is None:
                        wait_action = interpreted["wait_action"]
                        if wait_action is not None and not entry.get("dispatch_wait_recorded"):
                            entry["dispatch_wait_recorded"] = True
                            execution_events.append({
                                "event": "inventory_dispatch_wait_started",
                                "step": step - 1,
                                "time_s": current_time_s,
                                "node": entry["node"],
                                "agent": agent,
                                "adapter": participant_session.adapter_manifest,
                                "action_id": wait_action["action_id"],
                            })
                        continue
                    committed_action = interpreted["navigate_action"]
                    action_payload = committed_action["payload"]
                    dispatch_decision = {
                        "target_ref": action_payload["target_ref"],
                        "reason": "participant_action",
                        "used_message_ids": sorted(dispatch_consumed_message_ids),
                        "available_message_ids": sorted(visible_by_id),
                        "time_s": current_time_s,
                        "mode": args.inventory_dispatch_mode,
                        "delay_s": args.inventory_message_delay_s,
                        "channel_ledger": channel_ledger,
                        "decision_policy": participant_session.adapter_manifest["id"],
                        "ready_at_s": ready_at_s,
                        "waited_s": current_time_s - ready_at_s,
                        "max_wait_s": args.inventory_dispatch_wait_s,
                        "commit_reason": "participant_navigation_action",
                        "committed_action_id": committed_action["action_id"],
                        "actor": participant_session.adapter_manifest,
                        "step": step - 1,
                        "node": entry["node"],
                        "agent": agent,
                        "start_position_m": [x, y, z],
                    }
                    retarget_entry(entry, spec, dispatch_decision["target_ref"])
                    dispatch_decision["route_planned"] = replan(entry, x, y)
                    dispatch_decision["waypoints"] = list(entry["waypoints"] or [])
                    execution_events.append({"event": "inventory_dispatch_decision", **dispatch_decision})
                    trajectory.append(record_sample(step - 1))
                    if not dispatch_decision["route_planned"]:
                        scheduler.fail(entry["node"], "dispatch_route_unavailable")
                        deadlocks.append({"node": entry["node"], "reason": "dispatch_route_unavailable"})
                        continue
                elif was_waiting and communication_boundary is None:
                    for prerequisite in entry["prerequisites"]:
                        observation_id = observations_by_node.get(prerequisite)
                        if observation_id is None:
                            continue
                        observation_store.consume(
                            observation_id,
                            consumer=entry["node"],
                            timestamp_s=(step - 1) * control_dt,
                        )
                        execution_events.append({
                            "step": step,
                            "event": "observation_consumed",
                            "node": entry["node"],
                            "observation_id": observation_id,
                            "consumer": entry["node"],
                        })

                if (robot_types.get(agent) == "aerial"
                        and entry["skill"] in {"navigate", "aerial_scan"}
                        and entry.get("aerial_route_admitted_for_agent") != agent):
                    radius = planning_radius(robot_specs[agent])
                    route_points = aerial_route_points(entry, (x, y, z))
                    admission = aerial_map.check_route(
                        route_points,
                        half_extents_m=(radius, radius, radius), margin_m=0.1,
                        obstacle_aabbs=aerial_obstacle_boxes())
                    if not admission["admitted"] and len(route_points) >= 2:
                        detour = plan_local_aerial_detour(
                            aerial_map, route_points[0], route_points[-1],
                            half_extents_m=(radius, radius, radius), margin_m=0.1,
                            obstacle_aabbs=aerial_obstacle_boxes())
                        if detour["admitted"]:
                            entry["aerial_detour_queue"] = list(detour["points"][1:-1])
                            admission = {**detour["route"], "local_detour": True,
                                         "detour_points": detour["points"]}
                    event = {"step": step - 1, "time_s": current_time_s,
                             "event": "aerial_route_admission", "node": entry["node"],
                             "agent": agent, **admission}
                    aerial_admission_events.append(event)
                    execution_events.append(event)
                    if not admission["admitted"]:
                        scheduler.fail(entry["node"], "aerial_route_not_admitted")
                        deadlocks.append({"node": entry["node"], "agent": agent,
                                          "step": step, "reason": "aerial_route_not_admitted",
                                          "admission": admission})
                        hold_aerial(agent, (x, y, z))
                        break
                    entry["aerial_route_admitted_for_agent"] = agent
                    aerial_previous_positions[agent] = (x, y, z)

                if entry.get("scan_yaw_rad") is not None:
                    base_env.set_command(agent, "goal_yaw", torch.tensor(
                        [entry["scan_yaw_rad"]], device=session.device, dtype=torch.float32))

                if entry["phase"] == "wait":
                    entry["phase"] = (
                        "joint_to_object" if entry["skill"] == "joint_transport" else
                        "to_object" if entry["skill"] == "transport_object" else
                        ("aligning_view" if entry.get("wait_yaw_rad") is not None else "holding")
                        if entry["skill"] == "wait" else
                        "scan_travel" if entry["skill"] == "aerial_scan" else "travel")
                    entry["hold_position"] = (x, y, z)
                    st["step"] = step

                if entry["phase"] in {"aligning_view", "holding"} and entry.get("wait_yaw_rad") is not None:
                    aligned, yaw_command = advance_view_alignment(entry, yaw, control_dt)
                    commands[agent][0, 2] = yaw_command
                    if entry["phase"] == "aligning_view":
                        if not aligned:
                            continue
                        entry["phase"] = "holding"
                        entry["holding_started_at_s"] = current_time_s
                        trajectory.append(record_sample(step - 1))
                        execution_events.append({
                            "event": "observation_window_started", "node": entry["node"], "agent": agent,
                            "step": step, "time_s": current_time_s, "wait_yaw_rad": entry["wait_yaw_rad"],
                            "measured_yaw_rad": yaw, "yaw_error_rad": entry["alignment_yaw_error_rad"],
                            "alignment_duration_s": entry["alignment_elapsed_s"],
                        })

                if entry["phase"] == "holding":
                    if robot_types.get(agent) == "aerial":
                        hold_aerial(agent, entry["hold_position"])
                    elapsed = (current_time_s - entry["holding_started_at_s"] if "holding_started_at_s" in entry
                               else scheduler.elapsed_running(entry["node"]))
                    if entry["skill"] == "wait":
                        next_observation_s = entry.setdefault("next_observation_s", 0.0)
                        if elapsed + 1e-9 >= next_observation_s:
                            frame, sensor_source = capture_live_camera_frame(agent)
                            if frame is not None:
                                observation_id = f"{entry['node']}:frame:{step}"
                                observation_store.publish_frame(
                                    observation_id=observation_id,
                                    producer=agent,
                                    sensor="camera",
                                    sensor_config={
                                        "source": sensor_source,
                                        "robot": agent,
                                        "node": entry["node"],
                                        "observer_yaw_rad": yaw,
                                        "render_period_s": render_period_s,
                                        "target_ref": entry["observation_ref"],
                                        "coverage_sample": True,
                                        **(frame_archive.store(agent, observation_id, frame) if frame_archive else {}),
                                    },
                                    timestamp_s=(step - 1) * control_dt,
                                    frame_id=f"{agent}/camera_optical_frame",
                                    target_ref=entry["observation_ref"],
                                    position_m=[x, y, z],
                                    frame=frame,
                                    valid_for_s=max(1.0, entry.get("duration_s", 1.0)),
                                )
                                record_marker_detections(observation_id, frame)
                                if generic_participant:
                                    process_domain_frame(
                                        observation_id, frame, node_id=entry["node"],
                                        time_s=(step - 1) * control_dt)
                                observations_by_node[entry["node"]] = observation_id
                                observation_ids_by_node.setdefault(entry["node"], []).append(observation_id)
                                publish_runtime_observation(entry["node"], observation_id)
                                execution_events.append({
                                    "step": step,
                                    "event": "coverage_observation",
                                    "node": entry["node"],
                                    "agent": agent,
                                    "target_ref": entry["observation_ref"],
                                    "observation_id": observation_id,
                                    "observation_source": "live_isaac_rgb_frame",
                                    "sensor_source": sensor_source,
                                })
                                next_observation_s += 1.0
                            else:
                                next_observation_s = elapsed + 1.0
                            entry["next_observation_s"] = next_observation_s
                    if elapsed + 1e-9 >= entry["duration_s"]:
                        execution_events.append({"step": step, "event": "node_complete",
                                                 "node": entry["node"], "agent": agent,
                                                 "duration_s": elapsed})
                        scheduler.complete(entry["node"])
                        mark_node_done(entry)
                    continue

                # Joint transport is an idealized team state machine: both
                # declared robots reach the object, acquire shared logical
                # ownership, move together, then release at the target.
                if entry["skill"] == "joint_transport":
                    team = entry["team"]
                    routes = (entry["team_to_object"] if entry["phase"] == "joint_to_object"
                              else entry["team_to_target"])
                    if entry["phase"] == "release_settle":
                        ox, oy, oz = object_state(entry["object"])
                        if _m.hypot(ox - entry["goal"][0], oy - entry["goal"][1]) > 0.05:
                            write_object_pose(entry["object"], entry["goal"][0], entry["goal"][1],
                                              entry["release_z"], entry["release_yaw"])
                            continue
                        carry_physics.release(entry["object"])
                        carrying.pop(entry["object"], None)
                        trajectory.append(record_sample(step - 1))
                        execution_events.append({
                            "step": step, "event": "release", "node": entry["node"],
                            "agent": entry["agent"], "agents": list(team), "object": entry["object"],
                            "position": [round(ox, 3), round(oy, 3)],
                            "object_position": [round(ox, 3), round(oy, 3), round(oz, 3)],
                            "target_ref": entry["target_ref"],
                            "target": [round(entry["goal"][0], 3), round(entry["goal"][1], 3)],
                            "owner_before": list(team), "owner_after": None,
                            "confirmed_after_sim_step": True,
                        })
                        execution_events.append({
                            "step": step, "event": "node_complete", "node": entry["node"],
                            "agent": entry["agent"], "agents": list(team),
                            "position": [round(x, 3), round(y, 3)],
                        })
                        scheduler.complete(entry["node"])
                        mark_node_done(entry)
                        continue
                    if entry["phase"] in {"joint_to_object", "joint_carry"}:
                        slots = (entry["team_pickup_slots"] if entry["phase"] == "joint_to_object"
                                 else entry["team_delivery_slots"])
                        for member in team:
                            if (not routes[member] and math.dist(robot_state(member)[:2], slots[member])
                                    > entry["team_terminal_tolerance_m"]):
                                routes[member].append(slots[member])
                    if entry["phase"] == "joint_to_object" and all(not route for route in routes.values()):
                        owner_before = carrying.get(entry["object"])
                        if owner_before is not None:
                            raise RuntimeError(
                                f"object '{entry['object']}' is already owned by {owner_before}")
                        carry_physics.attach(entry["object"])
                        carrying[entry["object"]] = list(team)
                        team_positions = {
                            name: [round(robot_state(name)[0], 3), round(robot_state(name)[1], 3)]
                            for name in team
                        }
                        execution_events.append({
                            "step": step, "event": "pickup", "node": entry["node"],
                            "agent": entry["agent"], "agents": list(team), "object": entry["object"],
                            "position": [round(x, 3), round(y, 3)],
                            "object_position": [round(value, 3)
                                                for value in object_state(entry["object"])[:2]],
                            "owner_before": owner_before, "owner_after": list(team),
                            "team_positions": team_positions,
                        })
                        entry["phase"] = "joint_carry"
                        continue
                    if entry["phase"] == "joint_carry" and all(not route for route in routes.values()):
                        if carrying.get(entry["object"]) != list(team):
                            raise RuntimeError(
                                f"joint team {team} cannot release '{entry['object']}' owned by "
                                f"{carrying.get(entry['object'])!r}")
                        positions = {member: robot_state(member) for member in team}
                        leader_state = positions[entry["agent"]]
                        write_object_pose(entry["object"], entry["goal"][0], entry["goal"][1],
                                          entry["release_z"], leader_state[3])
                        entry["release_yaw"] = _paired_support_yaw(
                            positions, team, leader_state[3])
                        entry["phase"] = "release_settle"
                        continue
                    if entry["phase"] == "joint_carry" and carrying.get(entry["object"]):
                        positions = {member: robot_state(member) for member in team}
                        cx = sum(state[0] for state in positions.values()) / len(positions)
                        cy = sum(state[1] for state in positions.values()) / len(positions)
                        write_object_pose(
                            entry["object"], cx, cy, entry["carry_z"],
                            _paired_support_yaw(positions, team, positions[team[0]][3]))
                    else:
                        positions = {member: robot_state(member) for member in team}
                    for member, route in routes.items():
                        if not route:
                            continue
                        rx, ry, rz, ryaw = robot_state(member)
                        tx, ty = route[0]
                        dist = _m.hypot(tx - rx, ty - ry)
                        member_stall = st.setdefault("members", {}).setdefault(
                            (entry["phase"], member), {"step": step, "replans": 0})
                        if dist <= (entry["team_terminal_tolerance_m"] if len(route) == 1 else
                                    intermediate_waypoint_tolerance(
                                        args.path_waypoint_tol,
                                        entry["path_waypoint_tolerance_m"] * 2)):
                            route.pop(0)
                            st["step"] = step
                            st["ll"] = sum(len(item) for item in routes.values())
                            member_stall["step"] = step
                            member_stall.pop("progress_waypoint", None)
                            continue
                        desired = _m.atan2(ty - ry, tx - rx)
                        err = (desired - ryaw + _m.pi) % (2 * _m.pi) - _m.pi
                        update_waypoint_progress(
                            member_stall, waypoint=(tx, ty), distance_m=dist,
                            heading_error_rad=err, step=step)
                        action = stall_action(
                            step=step, last_progress_step=member_stall["step"],
                            replans=member_stall["replans"], stall_window=args.stall_window,
                            max_replans=args.max_replans)
                        if action == "replan":
                            from EAI.scenario_spec.geometry import static_blocked_grid
                            from .joint_transport import joint_route_grid
                            obstacles = [(robot_state(other)[:2], entry["team_planning_radii_m"][other])
                                         for other in team if other != member]
                            if entry["phase"] == "joint_to_object":
                                obstacles.append((object_state(entry["object"])[:2],
                                                  entry["object_planar_radius_m"]))
                            member_grid = joint_route_grid(
                                occupancy, static_blocked_grid(occupancy, spec,
                                    ground_tracking_reserve_m=entry["ground_tracking_reserve_m"]),
                                entry["team_planning_radii_m"][member], obstacles=obstacles)
                            endpoint = (entry["team_pickup_slots"] if entry["phase"] == "joint_to_object"
                                        else entry["team_delivery_slots"])[member]
                            path = _passable_route(member_grid, (rx, ry), endpoint)
                            if path is not None and member_grid.passable(*member_grid.cell(*endpoint)):
                                routes[member] = route_waypoints(
                                    member_grid, path, endpoint, preserve_goal=True)
                                member_stall["replans"] += 1
                                replans[entry["node"]] += 1
                                member_stall["step"] = step
                                member_stall.pop("progress_waypoint", None)
                                continue
                            action = "deadlock"
                        if action == "deadlock":
                            scheduler.fail(entry["node"], "navigation_deadlock")
                            deadlocks.append({"node": entry["node"], "agent": member,
                                              "phase": entry["phase"], "step": step,
                                              "last_progress_step": member_stall["step"],
                                              "distance_to_waypoint_m": round(dist, 3),
                                              "waypoint": [tx, ty],
                                              "replans": member_stall["replans"],
                                              "reason": "joint_member_no_progress"})
                            break
                        if entry["phase"] == "joint_carry":
                            from .joint_transport import formation_hold_required

                            if formation_hold_required(
                                    member=member, positions=positions, target=(tx, ty),
                                    max_separation_m=entry["team_max_separation_m"]):
                                entry["formation_hold_steps"][member] += 1
                                member_stall["step"] = step
                                continue
                        if member in navigation_bodies:
                            v, vy, wz = virtual_base_command(member, tx, ty)
                            commands[member][0, 1] = vy
                        elif robot_types.get(member) == "quadruped":
                            v, vy, wz = velocity_policy_command(
                                distance=dist, heading_error=err,
                                speed=args.speed, k_turn=args.k_turn,
                                minimum_distance_scale=(
                                    0.75 if robot_specs[member]["type"] == "m20" else 0.2))
                            commands[member][0, 1] = vy
                        else:
                            v, wz = steering_command(
                                distance=dist, heading_error=err,
                                speed=args.speed, k_turn=args.k_turn)
                        if robot_types.get(member) == "aerial":
                            base_env.set_command(member, "goal_position", torch.tensor(
                                [[tx, ty, max(1.5, rz)]], device=session.device, dtype=torch.float32))
                        else:
                            commands[member][0, 0] = v
                            commands[member][0, 2] = wz
                    continue

                # kinematic carry glue
                if entry["phase"] in ("carry", "to_target") and entry.get("object"):
                    write_object_pose(entry["object"], x, y, entry["carry_z"], yaw)
                    if entry["phase"] == "carry":
                        entry["phase"] = "to_target"

                if entry["phase"] == "release_settle":
                    ox, oy, oz = object_state(entry["object"])
                    if _m.hypot(ox - entry["goal"][0], oy - entry["goal"][1]) > 0.05:
                        write_object_pose(
                            entry["object"], entry["goal"][0], entry["goal"][1],
                            entry["release_z"], entry["release_yaw"])
                        continue
                    carry_physics.release(entry["object"])
                    carrying.pop(entry["object"], None)
                    trajectory.append(record_sample(step - 1))
                    execution_events.append({
                        "step": step, "event": "release", "node": entry["node"],
                        "agent": agent, "object": entry["object"],
                        "position": [round(ox, 3), round(oy, 3)],
                        "object_position": [round(ox, 3), round(oy, 3), round(oz, 3)],
                        "target_ref": entry["target_ref"],
                        "target": [round(entry["goal"][0], 3), round(entry["goal"][1], 3)],
                        "owner_before": agent, "owner_after": None,
                        "confirmed_after_sim_step": True,
                    })
                    execution_events.append({
                        "step": step, "event": "node_complete", "node": entry["node"],
                        "agent": agent, "position": [round(x, 3), round(y, 3)],
                    })
                    scheduler.complete(entry["node"])
                    mark_node_done(entry)
                    continue

                leg = (entry.get("to_object") if entry["phase"] == "to_object"
                       else entry.get("to_target") if entry["phase"] == "to_target"
                       else entry.get("waypoints"))
                if (entry["phase"] == "to_object" and leg is not None
                        and entry.get("interaction_standoff") is not None
                        and within_interaction_standoff(
                            (x, y), object_state(entry["object"]),
                            entry["interaction_standoff"],
                        )):
                    leg.clear()
                if entry["phase"] == "travel" and entry["arrival_predicate"] is not None:
                    if navigation_arrived(spec, entry, (x, y)):
                        trajectory.append(record_sample(step - 1))
                        execution_events.append({
                            "step": step - 1, "event": "node_complete", "node": entry["node"],
                            "agent": agent, "position": [x, y],
                            "arrival_predicate": entry["arrival_predicate"],
                            "confirmed_after_sim_step": True,
                        })
                        scheduler.complete(entry["node"])
                        mark_node_done(entry)
                        continue
                    if not leg:
                        raise RuntimeError(f"{entry['node']}: route exhausted outside target region")
                if entry["phase"] == "scan_travel" and leg is not None and not leg:
                    entry["phase"] = "hover"
                    entry["hover_elapsed_s"] = 0.0
                    entry["hover_previous_in_tolerance"] = False
                    continue
                if entry["phase"] == "hover":
                    if not command_aerial_goal(entry, (x, y, z), entry["scan_goal"]):
                        break
                    if advance_hover(entry, (x, y, z), control_dt, measured_yaw_rad=yaw):
                        frame = (
                            aerial_sensor_manager.capture_camera_frame(agent)
                            if aerial_sensor_manager is not None else None
                        )
                        observation_id = None
                        if frame is not None:
                            observation_id = f"{entry['node']}:frame:{step}"
                            observation_store.publish_frame(
                                observation_id=observation_id,
                                producer=agent,
                                sensor="camera",
                                sensor_config={
                                    "source": "Isaac Sim aerial camera annotator",
                                    "observer_yaw_rad": yaw,
                                    "robot": agent,
                                    "target_ref": entry["observation_ref"],
                                    **(frame_archive.store(agent, observation_id, frame) if frame_archive else {}),
                                },
                                timestamp_s=(step - 1) * control_dt,
                                frame_id=f"{agent}/camera_optical_frame",
                                target_ref=entry["observation_ref"],
                                position_m=[x, y, z],
                                frame=frame,
                                valid_for_s=max(1.0, entry.get("timeout_s", 60.0)),
                            )
                            record_marker_detections(observation_id, frame)
                            if generic_participant:
                                process_domain_frame(
                                    observation_id, frame, node_id=entry["node"],
                                    time_s=(step - 1) * control_dt)
                            observations_by_node[entry["node"]] = observation_id
                            observation_ids_by_node.setdefault(entry["node"], []).append(observation_id)
                            publish_runtime_observation(entry["node"], observation_id)
                        execution_events.append({
                            "step": step - 1, "event": "aerial_scan", "node": entry["node"],
                            "agent": agent, "target_ref": entry["observation_ref"],
                            "hover_duration_s": entry["hover_elapsed_s"],
                            "hover_goal": list(entry["scan_goal"]),
                            "measured_position": [x, y, z],
                            "position_error_m": entry["hover_distance_m"],
                            "scan_yaw_rad": entry.get("scan_yaw_rad"),
                            "measured_yaw_rad": yaw,
                            "yaw_error_rad": entry.get("hover_yaw_error_rad"),
                            "observation_produced": observation_id is not None,
                            "observation_ids": ([observation_id]
                                                 if observation_id is not None else []),
                            "observation_source": (
                                "live_isaac_rgb_frame" if observation_id is not None else None
                            ),
                            "confirmed_after_sim_step": True,
                        })
                        execution_events.append({
                            "step": step - 1, "event": "node_complete", "node": entry["node"],
                            "agent": agent, "position": [round(x, 3), round(y, 3)],
                        })
                        scheduler.complete(entry["node"])
                        mark_node_done(entry)
                    continue
                if entry["phase"] == "travel" and leg is not None and not leg:
                    execution_events.append({
                        "step": step, "event": "node_complete", "node": entry["node"],
                        "agent": agent, "position": [round(x, 3), round(y, 3)],
                    })
                    scheduler.complete(entry["node"])
                    mark_node_done(entry)
                    continue
                if entry["phase"] == "to_object" and leg is not None and not leg:
                    pickup_position = object_state(entry["object"])
                    pickup_distance = math.dist((x, y), pickup_position[:2])
                    if pickup_distance > entry["interaction_standoff"]:
                        scheduler.fail(entry["node"], "pickup_out_of_reach")
                        deadlocks.append({"node": entry["node"], "agent": agent,
                                          "step": step, "reason": "pickup_out_of_reach",
                                          "distance_m": pickup_distance,
                                          "standoff_m": entry["interaction_standoff"]})
                        break
                    owner_before = carrying.get(entry["object"])
                    if owner_before is not None:
                        raise RuntimeError(
                            f"object '{entry['object']}' is already owned by {owner_before}")
                    carry_physics.attach(entry["object"])
                    carrying[entry["object"]] = agent
                    team_positions = {
                        name: [round(robot_state(name)[0], 3), round(robot_state(name)[1], 3)]
                        for name in {item["agent"] for item in plan}
                    }
                    execution_events.append({
                        "step": step, "event": "pickup", "node": entry["node"],
                        "agent": agent, "object": entry["object"],
                        "position": [round(x, 3), round(y, 3)],
                        "object_position": [round(value, 3)
                                            for value in pickup_position[:2]],
                        "interaction_distance_m": pickup_distance,
                        "interaction_standoff_m": entry["interaction_standoff"],
                        "confirmed_after_sim_step": True,
                        "owner_before": owner_before, "owner_after": agent,
                        "team_positions": team_positions,
                    })
                    entry["phase"] = "carry"
                    continue
                if entry["phase"] == "to_target" and leg is not None and not leg:
                    if carrying.get(entry["object"]) != agent:
                        raise RuntimeError(
                            f"{agent} cannot release '{entry['object']}' owned by "
                            f"{carrying.get(entry['object'])!r}")
                    write_object_pose(
                        entry["object"], entry["goal"][0], entry["goal"][1],
                        entry["release_z"], yaw)
                    entry["release_yaw"] = yaw
                    entry["phase"] = "release_settle"
                    continue
                if not leg:
                    execution_events.append({
                        "step": step, "event": "node_complete", "node": entry["node"],
                        "agent": agent, "position": [round(x, 3), round(y, 3)],
                    })
                    scheduler.complete(entry["node"])
                    mark_node_done(entry)
                    continue

                if robot_types.get(agent) == "quadruped" and len(leg) > 1:
                    from EAI.scenario_spec.geometry import static_blocked_grid
                    from EAI.scenario_spec.oracle import InflatedGrid
                    from .route_tracking import advance_visible_waypoints

                    key = (entry["route_planning_radius_m"], entry["ground_tracking_reserve_m"])
                    if key not in tracking_grids:
                        tracking_grids[key] = InflatedGrid(occupancy, key[0], static_blocked_grid(
                            occupancy, spec, ground_tracking_reserve_m=key[1]))
                    advanced = advance_visible_waypoints(
                        leg, position=(x, y), grid=tracking_grids[key])
                    if advanced:
                        st["step"] = step
                        st["ll"] = len(leg)
                        st.pop("progress_waypoint", None)
                        execution_events.append({"event": "visible_waypoints_advanced",
                                                 "step": step, "node": entry["node"],
                                                 "agent": agent, "count": advanced})
                tx, ty = leg[0]
                dx, dy = tx - x, ty - y
                dist = _m.hypot(dx, dy)
                if entry["phase"] == "to_target" and entry.get("interaction_standoff") is not None:
                    target_dist = _m.hypot(
                        entry["goal"][0] - x,
                        entry["goal"][1] - y,
                    )
                    if target_dist <= entry["interaction_standoff"]:
                        entry["to_target"] = []
                        continue
                terminal_waypoint = len(leg) == 1
                tolerance = (args.waypoint_tol if terminal_waypoint else
                             intermediate_waypoint_tolerance(
                                 args.path_waypoint_tol,
                                 entry["path_waypoint_tolerance_m"] * 2))
                if terminal_waypoint:
                    if entry["phase"] in {"to_object", "to_target"}:
                        tolerance = max(tolerance, entry.get("interaction_standoff") or 0.0)
                    elif entry["phase"] == "travel":
                        tolerance = max(tolerance, entry.get("terminal_tolerance") or 0.0)
                if dist <= tolerance and not (
                        terminal_waypoint and entry["phase"] == "travel"
                        and entry["arrival_predicate"] is not None):
                    leg.pop(0)
                    st["step"] = step
                    st["ll"] = len(leg)
                    continue

                # Contacts are permitted.  A deadlock is instead a sustained
                # absence of waypoint completion after bounded route recovery.
                if len(leg) != st["ll"]:
                    st["ll"] = len(leg)
                    st["step"] = step
                if entry["skill"] in {"navigate", "aerial_scan"}:
                    heading_error = (_m.atan2(dy, dx) - yaw + _m.pi) % (2 * _m.pi) - _m.pi
                    update_waypoint_progress(st, waypoint=(tx, ty), distance_m=dist,
                                             heading_error_rad=heading_error, step=step)
                action = stall_action(
                    step=step,
                    last_progress_step=st["step"],
                    replans=replans[entry["node"]],
                    stall_window=args.stall_window,
                    max_replans=args.max_replans,
                )
                if action == "replan":
                    if replan(entry, x, y):
                        replans[entry["node"]] += 1
                        st["step"] = step
                        st["ll"] = -1
                        st.pop("progress_waypoint", None)
                        continue
                    action = "deadlock"
                if action == "deadlock":
                    scheduler.fail(entry["node"], "navigation_deadlock")
                    deadlocks.append(
                        {
                            "node": entry["node"],
                            "agent": agent,
                            "phase": entry["phase"],
                            "step": step,
                            "last_progress_step": st["step"],
                            "replans": replans[entry["node"]],
                            "distance_to_waypoint_m": round(dist, 3),
                            "waypoint": [round(tx, 3), round(ty, 3)],
                            "reason": ("replan_failed" if replans[entry["node"]] < args.max_replans
                                       else "no_waypoint_progress_after_replans"),
                        }
                    )
                    break

                if robot_types.get(agent) == "aerial":
                    altitude = entry["scan_goal"][2] if entry["scan_goal"] else max(1.5, z)
                    if not command_aerial_goal(entry, (x, y, z), (tx, ty, altitude)):
                        break
                    continue

                desired = _m.atan2(dy, dx)
                err = (desired - yaw + _m.pi) % (2 * _m.pi) - _m.pi
                if agent in navigation_bodies:
                    commands[agent][0] = torch.tensor(
                        virtual_base_command(agent, tx, ty), device=session.device)
                    continue
                if 100 < step - st["step"] <= 150:
                    commands[agent][0, 2] = (
                        0.8 if robot_specs[agent]["type"] in {"carter", "scout", "simple_car"}
                        else 0.0)
                    continue
                if robot_types.get(agent) == "quadruped":
                    v, vy, wz = velocity_policy_command(
                        distance=dist, heading_error=err,
                        speed=args.speed, k_turn=args.k_turn,
                        minimum_distance_scale=(
                            0.75 if robot_specs[agent]["type"] == "m20" else 0.2))
                    commands[agent][0, 1] = vy
                else:
                    v, wz = steering_command(
                        distance=dist, heading_error=err,
                        speed=args.speed, k_turn=args.k_turn)
                commands[agent][0, 0] = v
                commands[agent][0, 2] = wz

            if deadlocks:
                scheduler.cancel_remaining("episode_deadlock")
                for command in commands.values():
                    command.zero_()
                for name in aerial_holds:
                    hold_aerial(name, robot_state(name)[:3])
                carry_physics.restore_all()
                carrying.clear()
                prepare_kinematic_aerial_motion()
                step_with_control_evidence(
                    env, commands, base_env.scene.articulations,
                    control_step_evidence, step=step)
                apply_kinematic_aerial_motion()
                steps_taken = step
                trajectory.append(record_sample(step))
                break
            prepare_kinematic_aerial_motion()
            step_with_control_evidence(
                env, commands, base_env.scene.articulations,
                control_step_evidence, step=step)
            apply_kinematic_aerial_motion()
            root_states = {
                **{f"articulation:{name}": asset.data.root_state_w[0]
                   for name, asset in base_env.scene.articulations.items()},
                **{f"rigid_object:{name}": asset.data.root_state_w[0]
                   for name, asset in base_env.scene.rigid_objects.items()},
            }
            state_health = finite_root_state_report(root_states)
            if not state_health["validated"]:
                physics_state_failure = {
                    "step": step,
                    "simulation_time_s": current_time_s + control_dt,
                    "reason": "non_finite_root_state",
                    **state_health,
                }
                scheduler.cancel_remaining("non_finite_physics_state")
                steps_taken = step
                break
            for name, tracking in ground_tracking.items():
                current_position = robot_state(name)[:2]
                clearance = ground_surface_swept_disk_clearance(
                    spec, previous_ground_positions[name], current_position,
                    tracking["physical_radius_m"])
                previous_ground_positions[name] = current_position
                if clearance is None:
                    continue
                if (tracking["minimum_swept_clearance_m"] is None
                        or clearance["clearance_m"] < tracking["minimum_swept_clearance_m"]):
                    tracking.update(minimum_swept_clearance_m=clearance["clearance_m"],
                                    surface_id=clearance["surface_id"])
                if clearance["clearance_m"] < -1e-9 and not any(
                        item["agent"] == name for item in ground_tracking_violations):
                    active_entry = next((candidate for candidate in plan
                                         if candidate["node"] in active_nodes
                                         and name in (candidate["team"] or [candidate["agent"]])), None)
                    violation = {
                        "step": step, "agent": name,
                        "node": active_entry["node"] if active_entry else None,
                        "surface_id": clearance["surface_id"],
                        "swept_disk_clearance_m": clearance["clearance_m"],
                        "physical_radius_m": tracking["physical_radius_m"],
                    }
                    ground_tracking_violations.append(violation)
                    execution_events.append({"event": "ground_route_tracking_violation", **violation})
                    if active_entry is not None:
                        scheduler.fail(active_entry["node"], "ground_route_tracking_violation")
                    deadlocks.append({
                        **violation,
                        "reason": "planning_disk_left_validated_ground_corridor",
                    })
            for name, previous in list(aerial_previous_positions.items()):
                current = robot_state(name)[:3]
                radius = planning_radius(robot_specs[name])
                if args.aerial_motion_mode == "kinematic":
                    speed = kinematic_aerial_speed_report(previous, current, control_dt)
                    if not speed["validated"]:
                        violation = {
                            "step": step, "agent": name,
                            "event": "aerial_kinematic_speed_violation",
                            "reason": "measured_step_exceeds_kinematic_speed_limit",
                            **speed,
                        }
                        aerial_tracking_violations.append(violation)
                        execution_events.append(violation)
                        scheduler.cancel_remaining("aerial_kinematic_speed_violation")
                        deadlocks.append(violation)
                tracking = aerial_map.check_route(
                    [previous, current], half_extents_m=(radius, radius, radius),
                    margin_m=0., obstacle_aabbs=aerial_obstacle_boxes())
                aerial_previous_positions[name] = current
                if not tracking["admitted"]:
                    violation = {"step": step, "agent": name,
                                 "event": "aerial_route_tracking_violation", **tracking}
                    aerial_tracking_violations.append(violation)
                    execution_events.append(violation)
                    scheduler.cancel_remaining("aerial_route_tracking_violation")
                    deadlocks.append({"reason": "aerial_route_tracking_violation", **violation})
                    hold_aerial(name, current)
            steps_taken = step
            if gui_cam and gui_cam["prim"] and step % 3 == 0:
                target = args.follow or next(
                    (e["agent"] for e in plan
                     if e["node"] not in done_ids
                     and set(e["prerequisites"]) <= done_ids), None)
                if target and target != gui_cam["last"]:
                    gui_cam["last"] = target
                    print(f"[executor] camera -> {target}")
                if target:
                    rx, ry, rz, ryaw = robot_state(target)
                    xf = _UsdGeom.XformCommonAPI(gui_cam["prim"])
                    back = 4.5
                    xf.SetTranslate(_Gf.Vec3d(rx - back * _m.cos(ryaw), ry - back * _m.sin(ryaw), rz + 2.6))
            if step % 10 == 0 or done_ids >= all_node_ids:
                trajectory.append(record_sample(step))
            if done_ids >= all_node_ids:
                break

        if runtime_lifecycle is not None and next_fault_index < len(fault_events):
            runtime_lifecycle_failed = True
            for pending_fault in fault_events[next_fault_index:]:
                pending_result = runtime_lifecycle.mark_unobserved(
                    pending_fault,
                    reason="episode ended before the scheduled lifecycle boundary was observed")
                execution_events.append({
                    "event": "runtime_lifecycle_unsupported",
                    "step": steps_taken, "time_s": float(scheduler.time_s),
                    **pending_result,
                })
            next_fault_index = len(fault_events)

        if (physics_state_failure is None
                and (not pose_trajectory or pose_trajectory[-1]["step"] != steps_taken)):
            trajectory.append(record_sample(steps_taken))
        evaluation_spec = spec
        if generic_participant:
            evaluation_spec = bind_evaluation_agents(spec, plan)
            effective_evaluator = PredicateEvaluator(evaluation_spec)
            trajectory = [
                effective_evaluator.trajectory_step(sample["step"], sample["positions"])
                for sample in pose_trajectory
            ]
        if dispatch_enabled and dispatch_decision is not None:
            evaluation_spec, trajectory = dispatch_trajectory(spec, pose_trajectory, dispatch_decision)
        verdict = summarize_scheduled_trajectory(evaluation_spec, trajectory, scheduler.report())
        all_completed = done_ids >= all_node_ids
        if not all_completed and physics_state_failure is None:
            scheduler.cancel_remaining("episode_steps_exhausted")
        relay_validation = relay_event_validation(plan, execution_events)
        joint_validation = joint_event_validation(
            plan, execution_events, recovered_by=scheduler.recovered_by,
            trajectory=pose_trajectory)
        scheduling_report = reconcile_episode_end(
            scheduler.report(), steps_taken=steps_taken, control_dt_s=control_dt)
        scheduling_report["failed_nodes"] = sorted(failed_nodes)
        scheduling_report["recovered_nodes"] = sorted(recovered_nodes)
        camera_window_validation = validate_camera_windows(spec, {
            "scheduling": scheduling_report, "events": execution_events,
            "observation_store": observation_store.report(), "control_dt_s": control_dt,
            "pose_trajectory": {"frame": "world", "position_unit": "m", "samples": pose_trajectory},
        })
        if communication_boundary is not None:
            from demo.benchmark.communication import validate_communication_report
            communication_report = communication_boundary.report()
            communication_replay = validate_communication_report(
                communication_report, expected_initial_contract=communication_contract)
        else:
            communication_report = None
            communication_replay = {
                "schema": "eai.benchmark.communication_replay",
                "validated": True, "isaac_integrated": False,
                "mismatches": [], "published_count": 0,
            }
        communication_schedule_validation = {
            "required": bool(normalized_communication_schedule),
            "applied_count": communication_schedule_index,
            "scheduled_count": len(normalized_communication_schedule),
            "remaining_count": (
                len(normalized_communication_schedule) - communication_schedule_index),
            "validated": (
                communication_schedule_index == len(normalized_communication_schedule)),
        }
        factory_metrics = None
        coverage_validation = True
        if spec.get("scene_key") == "factory":
            from demo.factory_benchmark.evaluation import (
                coverage_freshness_metrics,
                coverage_member_exit_metrics,
                inspection_observation_metrics,
                robot_spacing_metrics,
                scheduling_metrics,
                shared_resource_metrics,
            )
            shared_resources = {
                resource.removeprefix("shared:")
                for entry in plan for resource in entry.get("resources", ())
            }
            factory_metrics = {
                "scheduling": scheduling_metrics(
                    scheduling_report,
                    required_nodes=all_node_ids,
                ),
                "shared_resources": shared_resource_metrics(scheduling_report, shared_resources),
                "robot_spacing": robot_spacing_metrics(pose_trajectory),
            }
            inspection_metrics = {}
            for scan_entry in (entry for entry in plan if entry["node"] in observations_by_node):
                consumers = [
                    candidate["node"] for candidate in plan
                    if scan_entry["node"] in candidate.get("prerequisites", ())
                    and not (dispatch_enabled and candidate["node"] == DISPATCH_NODE)
                ]
                inspection_metrics[scan_entry["node"]] = inspection_observation_metrics(
                    observation_store.report(),
                    target_ref=scan_entry["observation_ref"],
                    required_consumers=consumers,
                    observation_ids=observation_ids_by_node.get(scan_entry["node"], ()),
                )
            factory_metrics["inspection_observation"] = inspection_metrics
            if spec.get("benchmark_task_id") == "F-INS-01":
                required_observation_nodes = {
                    entry["node"] for entry in plan
                    if entry["skill"] == "aerial_scan" or entry["skill"] == "wait"
                }
                produced_observation_nodes = required_observation_nodes & set(observations_by_node)
                inspection_chain_validated = (
                    required_observation_nodes == produced_observation_nodes
                    and all(item.get("observation_chain_validated")
                            for item in inspection_metrics.values())
                )
                factory_metrics["inspection_observation_validation"] = {
                    "required_nodes": sorted(required_observation_nodes),
                    "produced_nodes": sorted(produced_observation_nodes),
                    "observation_chain_validated": inspection_chain_validated,
                    "target_identification_validated": False,
                    "target_localization_validated": False,
                }
            if spec.get("benchmark_task_id") == "F-COV-01":
                coverage_sites = sorted({
                    entry["observation_ref"] for entry in plan if entry["skill"] == "wait"
                })
                coverage_metrics = coverage_freshness_metrics(
                    observation_store.report(),
                    site_ids=coverage_sites,
                    now_s=float(scheduling_report["time_s"]),
                    freshness_bound_s=30.0,
                )
                factory_metrics["coverage"] = coverage_metrics
                factory_metrics["member_exit_recovery"] = coverage_member_exit_metrics(
                    scheduling_report,
                    execution_events=execution_events,
                    fault_events=fault_events,
                    plan=plan,
                    observation_report=observation_store.report(),
                )
                coverage_validation = coverage_metrics["validated"]
                if fault_events:
                    coverage_validation = (
                        coverage_validation
                        and factory_metrics["member_exit_recovery"]["validated"]
                    )
        aerial_nodes = {entry["node"] for entry in plan if entry["skill"] == "aerial_scan"}
        aerial_scan_events = {event.get("node") for event in execution_events
                              if event.get("event") == "aerial_scan"}
        aerial_scan_hover_validated = aerial_nodes <= aerial_scan_events
        aerial_scan_observation_events = [
            event for event in execution_events
            if event.get("event") == "aerial_scan" and event.get("node") in aerial_nodes
        ]
        aerial_scan_observation_validated = (
            not aerial_nodes
            or (len(aerial_scan_observation_events) == len(aerial_nodes)
                and all(event.get("observation_produced") is True
                        and bool(event.get("observation_ids"))
                        for event in aerial_scan_observation_events))
        )
        aerial_scan_observation_kinds = {
            event["node"]: (
                observation_store.get(event["observation_ids"][0])
                .get("target_estimate", {}).get("kind")
                if event.get("observation_ids") else None
            )
            for event in aerial_scan_observation_events
        }
        contract["aerial_scan_hover_confirmed"] = (
            aerial_scan_hover_validated if aerial_nodes else False)
        collaboration_validated = (
            (relay_validation["validated"] if relay_validation["required"] else True)
            and (joint_validation["validated"] if joint_validation["required"] else True)
            and coverage_validation
            and camera_window_validation["validated"]
            and aerial_scan_observation_validated
            and communication_replay["validated"]
            and communication_schedule_validation["validated"]
            and (factory_metrics is None
                 or factory_metrics.get("inspection_observation_validation", {}).get(
                     "observation_chain_validated", True)))
        node_only_task = all(entry["skill"] in {"wait", "aerial_scan"} for entry in plan)
        predicates_valid = verdict.get("verdict") == "success" or (
            node_only_task and verdict.get("verdict") == "no_predicates")
        timed_out = any(state["status"] == "timed_out" for state in scheduler.states.values())
        execution_validated = bool(
            not deadlocks and not runtime_lifecycle_failed and not timed_out
            and not wall_time_exhausted and all_completed and predicates_valid
            and collaboration_validated and not carrying and physics_state_failure is None)
        control_loop_wall_time_s = round(time.monotonic() - loop_started, 6)
        execution_provenance = finish_provenance(
            execution_request, runtime_snapshot, scenario_path=scenario_path)
        execution_validated = execution_validated and execution_provenance["validated"]
        carry_physics.restore_all()
        runtime_object_ids = {
            name[len("scenario_"):]
            for name in base_env.scene.rigid_objects
            if isinstance(name, str) and name.startswith("scenario_")
        }
        final_ownership = explicit_final_ownership(
            spec, carrying, runtime_object_ids=runtime_object_ids,
            ownership_object_ids={
                node["object_ref"] for node in spec["phase_graph"]["nodes"]
                if node.get("skill_key") in {"transport_object", "joint_transport"}
                and node.get("object_ref") is not None
            })
        fused_marker_views = []
        if inventory_variant is not None:
            from demo.benchmark.marker_detection import fuse_marker_views
            fused_marker_views = fuse_marker_views(marker_frame_results, observation_store.report()["observations"])
        runtime_lifecycle_report = (
            runtime_lifecycle.report() if runtime_lifecycle is not None else {
                "enabled": False,
                "validated": not bool(fault_events),
                "reason": "generic participant runtime disabled",
                "events": [],
            })
        runtime_lifecycle_report["startup_evidence"] = lifecycle_startup_evidence
        report = {
            "camera_archive_setup": camera_archive_setup,
            "camera_window_validation": camera_window_validation,
            "execution_provenance": execution_provenance,
            "benchmark_mass_audit": mass_audit,
            "status": ("physics_state_invalid" if physics_state_failure is not None
                       else "unsupported_runtime_lifecycle_event" if runtime_lifecycle_failed
                       else "wall_time_exhausted" if wall_time_exhausted else "timed_out" if timed_out else "deadlocked" if deadlocks
                       else "executed" if all_completed else "steps_exhausted"),
            "physics_state_health": {
                "validated": physics_state_failure is None,
                "failure": physics_state_failure,
                "scope": "articulation_and_rigid_object_root_state_after_each_control_step",
            },
            "scheduling": scheduling_report,
            "factory_metrics": factory_metrics,
            "control_dt_s": control_dt,
            "control_step_evidence": control_step_evidence.report(),
            "rendering": {"requested_period_s": args.render_period_s or None,
                          "actual_period_s": render_period_s,
                          "physics_dt_s": float(session.env_cfg.sim.dt),
                          "native_sensor_rate_validated": False},
            "control_loop_wall_time_s": control_loop_wall_time_s,
            "control_loop_wall_budget_s": args.wall_timeout_s or None,
            "steps_taken": steps_taken,
            "control_budget": control_budget,
            "replans": replans,
            "deadlocks": deadlocks,
            "events": execution_events,
            "communication": {
                "enabled": communication_boundary is not None,
                "runtime": communication_report,
                "replay": communication_replay,
                "schedule_application": communication_schedule_validation,
                "factor_scope": (
                    "participant observation delivery and explicit consumption"
                    if communication_boundary is not None else "disabled"),
            },
            "runtime_lifecycle": runtime_lifecycle_report,
            "aerial_route_validation": {
                "required": has_aerial_routes,
                "admissions": aerial_admission_events,
                "tracking_violations": aerial_tracking_violations,
                "validated": (bool(aerial_admission_events)
                              and all(event["admitted"] for event in aerial_admission_events)
                              and not aerial_tracking_violations) if has_aerial_routes else None,
                "tracking_model": "per-control-step measured swept planning-radius box and kinematic speed bound",
                "kinematic_speed_limit_m_s": (
                    AERIAL_KINEMATIC_SPEED_M_S if args.aerial_motion_mode == "kinematic" else None),
                "dynamic_obstacle_prediction": False,
            },
            "navigation_state_sources": {
                name: "body:base_link" if name in navigation_bodies else "articulation_root"
                for name in base_env.scene.articulations},
            "ground_route_tracking_validation": {
                "validated": not ground_tracking_violations,
                "sample_period_s": control_dt,
                "model": "measured-center segment swept physical planning disk versus declared exclusion AABBs",
                "planning_reserve_m": PATH_TRACKING_RESERVE_M,
                "robots": ground_tracking,
                "violations": ground_tracking_violations,
                "does_not_establish": [
                    "contact-force safety",
                    "continuous-time collision freedom",
                    "full 3-D body collision freedom",
                ],
            },
            "pose_trajectory": {
                "frame": "world", "position_unit": "m", "yaw_unit": "rad",
                "sample_interval_steps": 10,
                "additional_samples": "initial, navigation arrival, failure and final states",
                "samples": pose_trajectory,
            },
            "relay_validation": relay_validation,
            "joint_transport_validation": joint_validation,
            "aerial_scan_validation": {
                "required": bool(aerial_nodes),
                "nodes": sorted(aerial_nodes),
                "hover_nodes": sorted(aerial_scan_events & aerial_nodes),
                "hover_validated": aerial_scan_hover_validated,
                "observation_validated": aerial_scan_observation_validated,
                "observation_chain_validated": aerial_scan_observation_validated,
                "target_identification_validated": False,
                "target_localization_validated": False,
                "observation_estimate_kinds": aerial_scan_observation_kinds,
                "validated": aerial_scan_observation_validated,
                "model": "goal_navigation_measured_hover_and_camera_frame",
                "observation_produced": bool(aerial_scan_observation_events)
                and all(event.get("observation_produced") for event in aerial_scan_observation_events),
                "reason": ("no sensor observation callback is connected"
                           if aerial_nodes and not aerial_scan_observation_validated else None),
            },
            "final_ownership": final_ownership,
            "observation_store": observation_store.report(),
            "domain_runtime_binding": domain_runtime_binding,
            "final_poses": {n: json_safe_finite_values(robot_state(n)[:3])
                            for n in base_env.scene.articulations},
            "evaluation": verdict,
            "inventory_detection": {
                "required": inventory_variant is not None, "frames": marker_frame_results,
                "fused_view_estimates": fused_marker_views,
                "world_variant": inventory_variant,
                "policy_inputs": ["RGB pixels", "render-product camera parameters", "public dictionary and printed side length"],
                "business_task_validated": False,
                "limitations": ["Independent truth evaluation and downstream decision use remain separate",
                                "No expected marker ID or target world pose is passed to the decoder"]},
            "inventory_dispatch": (
                dispatch_report(dispatch_messages, dispatch_decision,
                                participant=participant_session.report())
                                if dispatch_enabled else None),
            "participant_protocol": (
                participant_session.report() if generic_participant else None),
            "execution_validation": {
                **contract,
                "tier": ("kinematic_execution_validated" if contract["transport_mode"] == "kinematic"
                         else "navigation_execution_validated") if execution_validated else "execution_failed",
                "validated": execution_validated,
            },
        }
        if dispatch_enabled:
            from demo.benchmark.provenance import canonical_sha256
            report["inventory_dispatch"]["resolved_scenario_sha256"] = canonical_sha256(evaluation_spec)
        if generic_participant:
            from demo.benchmark.domain_evaluator import evaluate_domain_evidence
            from demo.benchmark.provenance import canonical_sha256
            if domain_contract is None:
                raise RuntimeError("participant domain contract was not frozen before execution")
            # Preserve actual transport events/poses for partial scoring. No
            # SKU, resident-safety or dock-service evidence is invented from
            # successful scheduler states or privileged object names.
            domain_adapter.collect(
                phase="episode_finalized",
                time_s=float(scheduling_report["time_s"]),
                context={
                    "scheduling": scheduling_report,
                    "execution_events": execution_events,
                    "pose_trajectory": {"samples": pose_trajectory},
                    "plan": plan,
                    "collection_scope": "final_episode_observations_only",
                },
            )
            domain_evidence = domain_adapter.report(
                contract_sha256=canonical_sha256(domain_contract))
            # Keep the adapter's self-hash intact.  The evaluator consumes the
            # same service ledger at the report top level, while retaining the
            # nested copy makes the adapter record independently auditable.
            service_events = domain_evidence.get("domain_service_events")
            if service_events is not None:
                report["domain_service_events"] = service_events
            report["domain_evidence"] = domain_evidence
            report["domain_evaluation"] = evaluate_domain_evidence(
                report, scenario=spec, contract=domain_contract, occupancy=occupancy)
            report["business_task_success"] = report["domain_evaluation"]["business_success"]
        print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
        if args.report:
            Path(args.report).parent.mkdir(parents=True, exist_ok=True)
            Path(args.report).write_text(
                json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        return 0 if execution_validated else 1


if __name__ == "__main__":
    raise SystemExit(main())
