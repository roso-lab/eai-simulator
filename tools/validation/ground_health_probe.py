"""Bounded Go2 flat health/approach diagnostic; never executes stairs.

Generate a diagnostic ScenarioSpec without Isaac using --generate-scenario.
Running requires --scenario-file and --output. Default mode is zero-command
health observation. GPU execution must be coordinated separately.
"""

from __future__ import annotations

import argparse
from collections import deque
from dataclasses import dataclass
import hashlib
import inspect
import json
import math
from pathlib import Path
import statistics
import sys
import time

# Support both direct-file and module invocation without importing Isaac.
REPO = Path(__file__).resolve().parents[2]
if not __package__:
    for directory in (REPO, REPO / "source/EAI", REPO / "source/EAI_assets",
                      REPO / "source/EAI_hmrs"):
        sys.path.insert(0, str(directory))

from demo.benchmark.vertical_traversal import advance_ground_health


START = (-1.95, -8.60, 0.40)
# A 1 cm offset from the historical candidate avoids the schema's conservative
# spawn/target AABB overlap (0.55 m robot + 0.05 m target at exactly 0.60 m).
GOAL = (-1.95, -7.99)
ZERO = (0.0, 0.0, 0.0)
RAY_LENGTH = 2.0
FLAT_HEIGHT_TOLERANCE = 0.02
FLAT_NORMAL_Z = math.cos(math.radians(5))


def build_flat_go2_scenario():
    """New fixture only; the graph is descriptive and is never executed here.

    Target z=0 is required Factory 2-D map metadata, NOT measured support. All
    health heights and flat-route gates use live PhysX hits, never this field.
    F-INS-01 retains the existing three vertical-link exclusion envelopes; this
    diagnostic does not constitute an F-INS benchmark episode.
    """
    return {
        "version": 2, "task_name": "go2_flat_ground_health_probe",
        "benchmark_task_id": "F-INS-01", "scene_key": "factory", "seed": 17,
        "robots": [{"id": "go2_1", "type": "go2",
                    "controller": {"mode": "default", "cfg": "GO2_VELOCITY_RSL_CFG"},
                    "attachments": [], "spawn_pose": {
                        "position": list(START), "rotation": [1.0, 0.0, 0.0, 0.0]}}],
        "composition": {"entities": [], "targets": [
            {"id": "flat_probe_goal", "position": [*GOAL, 0.0], "radius_m": 0.05}]},
        "phase_graph": {"nodes": [{"id": "flat_approach", "skill_key": "navigate",
            "preferred_agent": "go2_1", "target_ref": "flat_probe_goal",
            "object_ref": None, "prerequisites": [], "timeout_s": 30.0}]},
    }


def vector(values, size):
    result = tuple(float(x) for x in values)
    if len(result) != size or not all(math.isfinite(x) for x in result):
        raise ValueError(f"expected {size} finite coordinates")
    return result


def extract_root_state(root_state):
    """Return XYZ/WXYZ from a complete Isaac 13-value root_state_w row."""
    if getattr(root_state, "ndim", 1) > 1 or (
            isinstance(root_state, (tuple, list)) and len(root_state) == 1):
        root_state = root_state[0]
    row = vector(root_state, 13)
    return row[:3], row[3:7]


def bounded_flat_command(position, quaternion, *, speed_m_s, dt_s, goal=GOAL):
    """World XY error -> body [vx, vy, wz], capped at 0.10 m/s; no yaw turn."""
    if not math.isfinite(speed_m_s) or not 0 <= speed_m_s <= 0.10 or dt_s <= 0:
        raise ValueError("invalid command speed/dt")
    w, x, y, z = vector(quaternion, 4)
    norm = math.hypot(w, x, y, z)
    if norm < 1e-12:
        raise ValueError("zero quaternion")
    w, x, y, z = (v / norm for v in (w, x, y, z))
    yaw = math.atan2(2 * (w*z + x*y), 1 - 2 * (y*y + z*z))
    px, py = vector(position[:2], 2)
    gx, gy = vector(goal, 2)
    dx, dy = gx - px, gy - py
    distance = math.hypot(dx, dy)
    if distance <= 0.05:
        return ZERO
    scale = min(speed_m_s, distance / dt_s) / distance
    return (scale * (math.cos(yaw)*dx + math.sin(yaw)*dy),
            scale * (-math.sin(yaw)*dx + math.cos(yaw)*dy), 0.0)


def unknown(reason, **evidence):
    return {"status": "unknown", "support_z_m": None, "reason": reason, **evidence}


def under(path, root):
    return path == root or path.startswith(root.rstrip("/") + "/")


class SupportRaycaster:
    """SDK raycast_all adapter with injectable query/classifier for pure tests.

    The nearest non-robot hit must be identifiable static collision geometry.
    Do not skip a nearer obstacle/steep face to claim the floor beneath it.
    Callback objects are copied immediately (their native lifetime is bounded).
    """

    def __init__(self, query, robot_roots, classify):
        self.query, self.robot_roots, self.classify = query, tuple(robot_roots), classify

    def cast(self, origin):
        hits, errors = [], []

        def report(hit):
            if len(hits) >= 256:
                errors.append("hit_limit")
                return False
            try:
                get = hit.get if isinstance(hit, dict) else lambda k: getattr(hit, k, None)
                hits.append({"collision": str(get("collision") or ""),
                             "rigid_body": str(get("rigid_body") or ""),
                             "position": list(vector(get("position"), 3)),
                             "normal": list(vector(get("normal"), 3)),
                             "distance": float(get("distance"))})
            except (TypeError, ValueError, OverflowError):
                errors.append("malformed_hit")
            return True

        try:
            origin = vector(origin, 3)
            if not self.robot_roots:
                return unknown("robot_exclusion_unavailable")
            self.query.raycast_all(origin, (0.0, 0.0, -1.0), RAY_LENGTH, report)
            if errors:
                return unknown(errors[0], hits=hits)
            candidates, excluded = [], []
            for hit in hits:
                paths = [hit[k] for k in ("collision", "rigid_body") if hit[k]]
                if not paths or any(not p.startswith("/") for p in paths):
                    return unknown("unidentified_hit", hits=hits)
                if any(under(p, root) for p in paths for root in self.robot_roots):
                    excluded.append(hit)
                    continue
                kind = self.classify(hit)
                if kind == "robot":
                    excluded.append(hit)
                    continue
                hit["kind"] = kind
                distance = hit["distance"]
                expected = (origin[0], origin[1], origin[2] - distance)
                if (not math.isfinite(distance) or not 0 <= distance <= RAY_LENGTH
                        or math.dist(hit["position"], expected) > 0.005):
                    return unknown("inconsistent_hit", hits=hits)
                candidates.append(hit)
            if not candidates:
                return unknown("no_nonrobot_hit", excluded_hits=excluded)
            nearest = min(candidates, key=lambda hit: hit["distance"])
            evidence = {"origin_xyz_m": origin, "hit": nearest, "excluded_hits": excluded}
            normal = nearest["normal"]
            if nearest["kind"] != "static":
                return unknown("nonstatic_or_unverified_collider", **evidence)
            if abs(math.hypot(*normal) - 1) > 0.01 or normal[2] < FLAT_NORMAL_Z:
                return unknown("not_flat_upward_surface", **evidence)
            return {"status": "measured", "support_z_m": nearest["position"][2], **evidence}
        except Exception as exc:
            return unknown(f"raycast_error:{type(exc).__name__}:{exc}")

    def patch(self, root_xyz, radius):
        # Sparse geometric samples, not proof of continuous support/contact.
        offsets = [(0.0, 0.0)] + [(radius * math.cos(i*math.pi/4),
                                  radius * math.sin(i*math.pi/4)) for i in range(8)]
        rays = [self.cast((root_xyz[0]+dx, root_xyz[1]+dy, root_xyz[2]))
                for dx, dy in offsets]
        if any(ray["status"] != "measured" for ray in rays):
            return unknown("incomplete_support_patch", rays=rays)
        heights = [ray["support_z_m"] for ray in rays]
        if max(heights) - min(heights) > FLAT_HEIGHT_TOLERANCE:
            return unknown("nonflat_support_patch", rays=rays)
        return {"status": "measured", "support_z_m": heights[0], "rays": rays}


def map_route_check(data, occupancy, start_xy, goal_xy, radius):
    from EAI.scenario_spec.geometry import ground_surface_swept_disk_clearance

    clearance = ground_surface_swept_disk_clearance(data, start_xy, goal_xy, radius)
    if clearance and clearance["clearance_m"] <= 0:
        raise ValueError(f"2-D vertical exclusion: {clearance}")
    count = max(1, math.ceil(math.dist(start_xy, goal_xy) / (occupancy.resolution / 2)))
    # Inflate by half the sample spacing to cover between successive map tests.
    envelope = radius + math.dist(start_xy, goal_xy) / count / 2
    for index in range(count + 1):
        xy = [a + (b-a)*index/count for a, b in zip(start_xy, goal_xy)]
        if not occupancy.rectangle_free(*xy, envelope, envelope):
            raise ValueError("flat route intersects occupied/unknown map cells")
    return {"status": "clear", "map_sha256": occupancy.sha256,
            "vertical_exclusion_clearance": clearance, "radius_m": radius}


@dataclass(frozen=True)
class ProbeLimits:
    mode: str = "health"
    duration_s: float = 10.0
    settle_s: float = 2.0
    speed: float = 0.10
    max_steps: int = 1500
    wall_time_s: float = 120.0

    def __post_init__(self):
        if (self.mode not in {"health", "approach"}
                or not all(math.isfinite(x) for x in
                           (self.duration_s, self.settle_s, self.speed, self.wall_time_s))
                or not 0.5 <= self.settle_s < self.duration_s <= 30
                or not 0 <= self.speed <= 0.10 or not 1 <= self.max_steps <= 10000
                or not 1 <= self.wall_time_s <= 600):
            raise ValueError("require 0.5 <= settle < duration <= 30 s, speed 0..0.10, "
                             "steps 1..10000, wall-time 1..600 s")


def run_control_loop(driver, limits, emit, *, clock=time.monotonic):
    """Injectable live loop. No recovery, teleport, reset, or extra failure step."""
    dt = float(driver.dt)
    if not math.isfinite(dt) or not 0 < dt <= 0.1:
        raise ValueError("unknown/unsupported control dt; no inferred timestep")
    budget = min(limits.max_steps, math.floor(limits.duration_s / dt + 1e-9))
    state, window = {}, deque(maxlen=max(2, math.ceil(0.5 / dt)))
    reference, floor = None, None
    unknown_elapsed = arrival_elapsed = 0.0
    applied = None  # Initial reset observation precedes the first probe command.
    started = clock()
    result, steps = "step_budget", 0
    try:
        while True:
            sample = driver.measure()
            support = sample["support"]
            valid = True
            try:
                position, quaternion = extract_root_state(sample["root_state_w"])
                if not sample["joint_pos"] or len(sample["joint_pos"]) != len(sample["joint_vel"]):
                    raise ValueError("joint state missing/malformed")
                vector(sample["joint_pos"], len(sample["joint_pos"]))
                vector(sample["joint_vel"], len(sample["joint_pos"]))
            except (TypeError, ValueError, OverflowError):
                position, quaternion, valid = (0.0, 0.0, math.nan), (0.0,)*4, False
            measured = support.get("status") == "measured" and support.get("support_z_m") is not None
            # Before a settled reference exists, invoke the helper with support
            # unknown so only tilt/invalid-state diagnostics are possible. 1.0
            # is an unused helper argument, NOT an inferred clearance baseline.
            health = advance_ground_health(state if steps else {}, root_z_m=position[2],
                quaternion_wxyz=quaternion, dt_s=dt, reference_clearance_m=reference or 1.0,
                support_z_m=support["support_z_m"] if reference is not None and measured else None)
            if not valid:
                health = {**health, "status": "invalid_state", "failed": True,
                          "reasons": ["nonfinite_or_malformed_root_or_joint_state"]}
            if measured and valid and health.get("tilt_rad", math.pi) < math.radians(20):
                clearance = position[2] - support["support_z_m"]
                velocities = sample["root_state_w"][7:]
                stable = (0.15 <= clearance <= 0.8 and math.hypot(*velocities[:3]) < 0.15
                          and math.hypot(*velocities[3:]) < 0.3)
                if stable:
                    window.append((clearance, support["support_z_m"]))
                else:
                    window.clear()
            else:
                window.clear()
            unknown_elapsed = 0.0 if measured else unknown_elapsed + (dt if steps else 0.0)
            elapsed = steps * dt
            reason = None
            if health["failed"]:
                reason = health["status"]
            elif unknown_elapsed + 1e-9 >= 0.5:
                reason = "support_unknown"
            elif reference is None and elapsed + 1e-9 >= limits.settle_s:
                if (len(window) != window.maxlen
                        or max(c for c, _ in window) - min(c for c, _ in window) > 0.03
                        or max(z for _, z in window) - min(z for _, z in window) > FLAT_HEIGHT_TOLERANCE):
                    reason = "settled_reference_unknown"
                else:
                    reference = statistics.median(c for c, _ in window)
                    floor = statistics.median(z for _, z in window)
                    state.clear()
                    health = advance_ground_health(state, root_z_m=position[2],
                        quaternion_wxyz=quaternion, dt_s=dt,
                        reference_clearance_m=reference, support_z_m=support["support_z_m"])
            if reference is not None and measured and abs(support["support_z_m"] - floor) > FLAT_HEIGHT_TOLERANCE:
                reason = reason or "support_level_changed"
            if valid and (abs(position[0] - START[0]) > 0.15
                          or not START[1] - 0.15 <= position[1] <= GOAL[1] + 0.10):
                reason = reason or "left_flat_corridor"
            command, route = ZERO, None
            if reason is None and reference is not None and health["status"] == "healthy":
                if limits.mode == "approach":
                    arrival_elapsed = (arrival_elapsed + dt
                        if math.dist(position[:2], GOAL) <= 0.05 else 0.0)
                    if arrival_elapsed + 1e-9 >= 0.5:
                        reason = "flat_goal_observed"
                    else:
                        route = driver.check_route(position, floor)
                        if route["status"] != "clear":
                            reason = "flat_route_unverified"
                        else:
                            command = bounded_flat_command(position, quaternion,
                                speed_m_s=limits.speed, dt_s=dt)
            else:
                arrival_elapsed = 0.0
            if not driver.is_running():
                reason = reason or "application_stopped"
            if clock() - started >= limits.wall_time_s:
                reason = reason or "wall_time_budget"
            if steps >= budget:
                if (limits.mode == "health" and reference is not None
                        and health["status"] == "healthy"
                        and steps >= math.floor(limits.duration_s / dt + 1e-9)):
                    reason = reason or "flat_health_observed"
                else:
                    reason = reason or "step_or_sim_time_budget"
            if reason:
                command = ZERO
            emit({"type": "sample", "step": steps, "sim_time_s": elapsed,
                  "dt_s": dt, "phase": "settle" if reference is None else limits.mode,
                  "command_applied_body_vx_vy_wz": applied,
                  "next_command_body_vx_vy_wz": command, "next_command_will_step": not bool(reason),
                  **sample, "ground_health": health, "reference_clearance_m": reference,
                  "height_diagnosis_available": reference is not None and measured,
                  "route_check": route, "stop_reason": reason})
            if reason:
                result = reason
                break
            driver.step(command)
            applied, steps = command, steps + 1
    finally:
        # Zero the command latch then stop physics; never run invalid data through
        # the policy for an extra "stop step". The session owns final cleanup.
        driver.stop()
    summary = {"type": "summary", "outcome": result, "steps": steps,
               "sim_time_s": steps*dt, "reference_clearance_m": reference,
               "flat_diagnostic_completed": result in {"flat_health_observed", "flat_goal_observed"},
               "execution_validated": False, "climbing_validated": False,
               "contact_evidence": "unknown: geometric rays are not contact force measurements"}
    emit(summary)
    return summary


def json_safe(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    return value


def file_evidence(path):
    path = Path(path)
    if not path.is_file():
        return {"path": str(path), "sha256": None, "status": "unavailable"}
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024*1024), b""):
            digest.update(block)
    return {"path": str(path.resolve()), "sha256": digest.hexdigest()}


def configure_probe(env_cfg):
    env_cfg.sim.enable_scene_query_support = True
    env_cfg.episode_length_s = 3600.0  # No auto-reset may hide a terminal fall.


class IsaacDriver:
    def __init__(self, session, scenario, occupancy, radius):
        import torch
        import omni.usd
        from omni.physx import get_physx_scene_query_interface
        from pxr import UsdPhysics

        self.torch, self.session = torch, session
        self.scenario, self.occupancy, self.radius = scenario, occupancy, radius
        if session.num_envs != 1 or session.possible_agents != ["go2_1"]:
            raise ValueError("probe requires exactly one go2_1")
        self.robot = session.base_env.scene.articulations["go2_1"]
        self.dt = session.base_env.step_dt
        self.stage = omni.usd.get_context().get_stage()
        # V2 builder creates these direct children under the single environment.
        self.robot_roots = [f"/World/envs/env_0/{name}" for name in session.possible_agents]
        if not all(self.stage.GetPrimAtPath(path).IsValid() for path in self.robot_roots):
            raise ValueError("cannot resolve exact robot roots for ray exclusion")

        def classify(hit):
            prim = self.stage.GetPrimAtPath(hit["collision"])
            if not prim.IsValid() or not prim.HasAPI(UsdPhysics.CollisionAPI):
                return "unknown"
            kind = "static"
            while prim.IsValid() and not prim.IsPseudoRoot():
                if prim.HasAPI(UsdPhysics.ArticulationRootAPI):
                    return "robot"
                if prim.HasAPI(UsdPhysics.RigidBodyAPI):
                    if UsdPhysics.RigidBodyAPI(prim).GetRigidBodyEnabledAttr().Get():
                        kind = "dynamic_or_kinematic"
                prim = prim.GetParent()
            return kind

        self.rays = SupportRaycaster(get_physx_scene_query_interface(), self.robot_roots, classify)

    def measure(self):
        data = self.robot.data
        root = data.root_state_w[0].detach().cpu().tolist()
        try:
            xyz, quaternion = extract_root_state(root)
            support = self.rays.patch(xyz, self.radius)
        except (TypeError, ValueError):
            xyz, quaternion = root[:3], root[3:7]
            support = unknown("invalid_root_state")
        return {"root_state_w": root, "root_xyz_m": xyz, "root_quaternion_wxyz": quaternion,
                "root_state_layout": "link XYZ/WXYZ, COM linear/angular world velocity",
                "joint_names": list(self.robot.joint_names),
                "joint_pos": data.joint_pos[0].detach().cpu().tolist(),
                "joint_vel": data.joint_vel[0].detach().cpu().tolist(),
                "support": support, "contact_evidence": {"status": "unknown", "forces": None}}

    def check_route(self, position, floor):
        try:
            evidence = map_route_check(self.scenario, self.occupancy, position[:2], GOAL, self.radius)
            # Recheck the entire remaining short corridor before every moving
            # command. This is sparse conservative flat admission, not a 3-D planner.
            count = max(1, math.ceil(math.dist(position[:2], GOAL) / 0.10))
            patches = [self.rays.patch((position[0]+(GOAL[0]-position[0])*i/count,
                                       position[1]+(GOAL[1]-position[1])*i/count,
                                       position[2]), self.radius) for i in range(count+1)]
            if any(p["status"] != "measured" or abs(p["support_z_m"]-floor) > FLAT_HEIGHT_TOLERANCE
                   for p in patches):
                return {"status": "unknown", "reason": "route_support_not_flat", "patches": patches}
            return {**evidence, "patches": patches}
        except Exception as exc:
            return {"status": "unknown", "reason": str(exc)}

    def step(self, command):
        # DirectMARL step accepts external controller commands, NOT 12 joint actions.
        with self.torch.inference_mode():
            result = self.session.env.step({"go2_1": self.torch.tensor(
                [command], dtype=self.torch.float32, device=self.session.device)})
        if any(bool(flags["go2_1"].any()) for flags in result[2:4]):
            raise RuntimeError("unexpected termination/reset; terminal sample cannot be trusted")

    def is_running(self):
        return self.session.simulation_app.is_running() and self.session.base_env.sim.is_playing()

    def stop(self):
        self.session.base_env.set_command("go2_1", "base_velocity",
            self.torch.zeros((1, 3), device=self.session.device))
        self.session.base_env.sim.stop()

    def provenance(self):
        cfg = self.session.env_cfg.controllers["go2_1"]
        if isinstance(cfg, (list, tuple)):
            cfg = cfg[0]
        source = inspect.getsourcefile(cfg.compute_action_from_command_func)
        return {"controller_source": file_evidence(source) if source else None,
                "controller_weights": file_evidence(cfg.model_path),
                "scene_layers": [file_evidence(layer.realPath) if layer.realPath else
                    {"identifier": layer.identifier, "sha256": None, "status": "anonymous_or_remote"}
                    for layer in self.stage.GetUsedLayers()],
                "robot_exclusion_roots": self.robot_roots,
                "physics_dt_s": self.session.env_cfg.sim.dt,
                "control_dt_s": self.dt, "requested_scene_queries": True,
                "scene_hash_limit": "Loaded local USD layers only; not textures or full asset closure"}


def run_probe(args, limits):
    from EAI.scenario_spec import load_scenario
    from EAI.scenario_spec.geometry import provider_map, validate_geometry, validate_connectivity
    from EAI.scenario_spec.schema import ROBOT_PROFILES

    scenario = load_scenario(args.scenario_file)
    if scenario != load_generated_scenario():
        raise ValueError("only this tool's unmodified diagnostic scenario is admitted")
    radius = ROBOT_PROFILES["go2"]["radius_m"]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Never overwrite a frozen fixture or a previous evidence trace.
    with args.output.open("x", encoding="utf-8") as output:
        def emit(row):
            output.write(json.dumps(json_safe(row), allow_nan=False) + "\n")
            output.flush()

        emit({"type": "manifest", "protocol": "go2_flat_ground_health_probe_v1",
              "limits": vars(limits), "requested_device": args.device,
              "scenario": scenario, "scenario_file": file_evidence(args.scenario_file),
              "tool": file_evidence(__file__), "helper": file_evidence(inspect.getsourcefile(advance_ground_health)),
              "execution_validated": False, "nonfinite_encoding": "null; health fails invalid_state",
              "flat_diagnostic_only": True, "target_z_usage": "2-D schema metadata only",
              "settle_thresholds": {"window_s": 0.5, "clearance_range_m": [0.15, 0.8],
                "clearance_spread_m": 0.03, "max_speed_m_s": 0.15, "max_angular_speed_rad_s": 0.3,
                "max_tilt_degrees": 20}, "support_tolerance_m": FLAT_HEIGHT_TOLERANCE})
        try:
            occupancy = provider_map(ensure=False, scene_key="factory")
            validate_geometry(scenario, occupancy)
            validate_connectivity(scenario, occupancy)
            emit({"type": "map_admission", **map_route_check(scenario, occupancy, START[:2], GOAL, radius)})
            from simulator import SimulatorLaunchConfig, open_simulator_session

            config = SimulatorLaunchConfig(env=scenario["task_name"], scenario_file=str(args.scenario_file),
                seed=17, device=args.device, headless=args.headless,
                enable_ros_bridge_extension=False, env_cfg_hook=configure_probe)
            with open_simulator_session(config) as session:
                try:
                    if session.device != args.device:
                        raise RuntimeError("requested physics device changed; refusing the probe")
                    driver = IsaacDriver(session, scenario, occupancy, radius)
                    emit({"type": "runtime", "actual_device": session.device, **driver.provenance()})
                    summary = run_control_loop(driver, limits, emit)
                    return 0 if summary["flat_diagnostic_completed"] else 2
                except BaseException as exc:
                    # Flush before Kit teardown, which can terminate the process.
                    emit({"type": "error", "outcome": "failed_closed", "error": str(exc),
                          "execution_validated": False})
                    raise
        except Exception as exc:
            emit({"type": "error", "outcome": "failed_closed", "error": str(exc),
                  "execution_validated": False})
            print(f"Probe failed closed: {exc}", file=sys.stderr)
            return 2


def load_generated_scenario():
    from EAI.scenario_spec import validate_scenario
    return validate_scenario(build_flat_go2_scenario())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generate-scenario", type=Path, help="write a NEW diagnostic JSON and exit; no Isaac")
    parser.add_argument("--scenario-file", type=Path, help="read the generated diagnostic JSON")
    parser.add_argument("--output", type=Path, help="NEW flushed JSONL evidence file; separate from Kit stdout")
    parser.add_argument("--mode", choices=("health", "approach"), default="health")
    parser.add_argument("--duration-s", type=float, default=10.0, help="total simulation budget, including settle; <=30")
    parser.add_argument("--settle-s", type=float, default=2.0)
    parser.add_argument("--speed", type=float, default=0.10, help="approach planar speed ceiling; <=0.10 m/s")
    parser.add_argument("--max-steps", type=int, default=1500)
    parser.add_argument("--wall-time-s", type=float, default=120.0, help="loop wall budget; use timeout for startup/SDK stalls")
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--headless", action="store_true")
    args = parser.parse_args(argv)
    try:
        limits = ProbeLimits(args.mode, args.duration_s, args.settle_s, args.speed,
                             args.max_steps, args.wall_time_s)
        if args.generate_scenario:
            if args.scenario_file or args.output:
                parser.error("generation is separate from execution")
            scenario = load_generated_scenario()
            args.generate_scenario.parent.mkdir(parents=True, exist_ok=True)
            with args.generate_scenario.open("x", encoding="utf-8") as output:
                json.dump(scenario, output, indent=2, allow_nan=False)
                output.write("\n")
            print(f"Generated {args.generate_scenario}; map/support not yet admitted; no Isaac started.")
            return 0
        if not args.scenario_file or not args.output:
            parser.error("execution requires --scenario-file and --output")
        if args.scenario_file.resolve() == args.output.resolve():
            parser.error("scenario and evidence paths must differ")
        return run_probe(args, limits)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
