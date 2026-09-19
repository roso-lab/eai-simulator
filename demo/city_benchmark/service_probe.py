"""Bounded serial probe for the concrete City episode service runtime."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from functools import partial
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterator, Mapping
import uuid

from .native_probe import _serial_lock, _write


ROOT = Path(__file__).resolve().parents[2]
SCHEMA = "eai.city.native_service_probe_v1"
MAX_EPISODE_S = 45.0


def _jsonable(value: Any) -> Any:
    if hasattr(value, "as_dict") and callable(value.as_dict):
        return _jsonable(value.as_dict())
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _hashes() -> dict[str, str]:
    paths = [ROOT / path for path in (
        "demo/city/runtime.py", "demo/city/people.py", "demo/city/usd_scene.py",
        "demo/city_benchmark/live_witness.py", "demo/city_benchmark/native_runner.py",
        "demo/city_benchmark/service_probe.py", "demo/city/main.py", "simulator.py",
        "usd/scene/city/city_static.usd", "usd/scene/city/city_scenario.json",
        "usd/demo/city/vehicles/car_e4087350c4d84d8391de09b405c96488.usd",
        "usd/demo/city/vehicles/car_03a6d639648540e3ad45ed38c9b19d1f.usd",
    )]
    return {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def _selection(seed: int) -> dict[str, Any]:
    """Build the probe selection without importing City/Kit modules early."""
    return {
        "version": 1, "scene_key": "modern_house_nvd", "task_name": "city_eai_demo",
        "robots": [
            {"type": "go2", "controller": {"mode": "default", "cfg": "GO2_VELOCITY_RSL_CFG"},
             "spawn_pose": {"position": [-69.0, 49.0, 0.62], "rotation": [1, 0, 0, 0]},
             "attachments": []},
            {"type": "carter", "controller": {"mode": "default", "cfg": "CARTER_DIFF_CFG"},
             "spawn_pose": {"position": [-65.0, 49.0, 0.22], "rotation": [1, 0, 0, 0]},
             "attachments": []},
            {"type": "iris", "controller": {"mode": "default", "cfg": "PEGASUS_IRIS_POSITION_CFG"},
             "spawn_pose": {"position": [-61.0, 49.0, 3.0], "rotation": [1, 0, 0, 0]},
             "attachments": []},
        ],
        "scenario_seed": int(seed),
    }


def _checkpoint(path: Path, report: Mapping[str, Any]) -> None:
    _write(path, _jsonable(report))


@contextmanager
def _service_report_guard(path: Path, report: dict[str, Any]) -> Iterator[None]:
    """Keep partial service evidence if a later native phase fails."""
    try:
        yield
    except BaseException as exc:
        report.update(
            status="blocked",
            blocker=f"{type(exc).__name__}: {exc}",
            full_city_service="pending_formal_episode_geometry",
        )
        _checkpoint(path, report)
        raise
    else:
        _checkpoint(path, report)


def prepare_probe(output_root: Path, *, device: str = "cpu", seed: int = 17,
                  overwrite: bool = False) -> dict[str, Any]:
    path = output_root / "service_probe_prepare.json"
    if path.exists() and not overwrite:
        raise FileExistsError("City service probe is already prepared; use a fresh directory")
    report = {"schema": SCHEMA, "version": 1, "status": "prepared",
              "device": str(device), "seed": int(seed), "scoreable": False,
              "formal_episode_binding": False,
              "geometry_scope": "runtime_selected_candidate_diagnostic",
              "source_hashes": _hashes(),
              "bounded": {"max_steps_per_phase": 800, "max_episode_s": MAX_EPISODE_S},
              "requires": ["scene_query_support", "cpu_current_physx_queries",
                           "one_city_person", "one_following_vehicle"]}
    _write(path, report)
    return report


def _load_prepare(output_root: Path) -> dict[str, Any]:
    path = output_root / "service_probe_prepare.json"
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError("run --prepare before --execute") from exc
    if result.get("schema") != SCHEMA or result.get("status") != "prepared":
        raise RuntimeError("invalid City service probe prepare record")
    return result


def _bounds_half(row: Mapping[str, Any], margin: float = 0.05) -> tuple[float, float, float]:
    return tuple((float(row["max"][axis]) - float(row["min"][axis])) * 0.5 + margin
                 for axis in range(3))


def _assembly_region(target, half, offset):
    # Capacity one: contain the full bound around the destination, with 10cm
    # free space. Derive from the person geometry, not a generic square that
    # can accidentally include a neighbouring lamp.
    extent = [half[i]+abs(offset[i])+.1 for i in range(2)]
    return {"min": [target[0]-extent[0], target[1]-extent[1], target[2]-.05],
            "max": [target[0]+extent[0], target[1]+extent[1], target[2]+offset[2]+half[2]+.1],
            "capacity": 1, "query_min_z": target[2]+.02}


def _step(session: Any, city: Any, actions: Mapping[str, Any], count: int = 1) -> None:
    from .native_probe import _robot_tensor_pose
    for _ in range(count):
        if city.elapsed + float(session.base_env.step_dt) > MAX_EPISODE_S:
            raise RuntimeError("City service probe exceeded its episode deadline")
        before = {"time_s": city.elapsed, "position_m": list(_robot_tensor_pose(session))}
        session.env.step(actions)
        city.step(dt=float(session.base_env.step_dt))
        session._city_diagnostic_role_interval = [before, {
            "time_s": city.elapsed, "position_m": list(_robot_tensor_pose(session)),
            "physics_step": int(session.base_env.sim.current_time_step_index)}]


def _role(runner: Any, role: str, session: Any, trace: dict) -> None:
    now = runner.adapter.now_s()
    samples = session._city_diagnostic_role_interval
    if (samples[-1]["time_s"] != now or samples[0]["time_s"] >= now
            or any(math.dist(row["position_m"][:2], [-69., 49.]) > 2. for row in samples)):
        raise RuntimeError("native robot left diagnostic role region or interval is stale")
    runner.record_role(role, start_time_s=samples[0]["time_s"], end_time_s=now)
    trace.setdefault("role_intervals", []).append({"role": role,
        "agent_id": "go2_1", "source": "articulation.data.root_pos_w", "samples": samples,
        "diagnostic_region_center_xy": [-69., 49.], "radius_m": 2.})


def _rotation_sweep_half(half, angle):
    """Axis-aligned bound of a rectangle over the full signed turn interval."""
    lower, upper = sorted((0., float(angle)))
    x, y, z = half
    critical = [lower, upper]
    for k in range(-3, 4):
        for base in (math.atan2(y, x), -math.atan2(y, x),
                     math.atan2(x, y), -math.atan2(x, y)):
            value = base+k*math.pi
            if lower <= value <= upper:
                critical.append(value)
    return (max(x*abs(math.cos(t))+y*abs(math.sin(t)) for t in critical),
            max(x*abs(math.sin(t))+y*abs(math.cos(t)) for t in critical), z)


def _divert_candidates(city, vehicle_id, reader, half, offset):
    """Bounded author candidates selected at request time, never formal routes."""
    current = city.following_vehicle_pose(vehicle_id)
    state = city._traffic_state[vehicle_id]
    _, yaw = state["active_route"].sample(state["distance"], loop=state["active_route"] is state["base_route"])
    attempts = []
    # `half` is the CURRENT world AABB, not the initial heading's AABB.
    # Rotate this conservative rectangle through the actual commanded turn;
    # maxima include all intermediate headings, not only the endpoints.
    sweep_half = half
    for forward in (6., 3.):
        for lateral in (.5, -.5, 1., -1., 2., -2.):
            endpoint = (current[0] + forward*math.cos(yaw)-lateral*math.sin(yaw),
                        current[1] + forward*math.sin(yaw)+lateral*math.cos(yaw), current[2])
            sweep_half = _rotation_sweep_half(half, math.atan2(lateral, forward))
            points = tuple(tuple(p[i]+offset[i] for i in range(3)) for p in (current, endpoint))
            result = reader.read_route(f"{vehicle_id}:candidate:{len(attempts)}", points,
                half_extents_m=sweep_half, episode_id=city.episode_id, time_s=city.elapsed,
                moving_entity_id=vehicle_id)
            attempts.append({"endpoint": endpoint, "readback": result,
                             "initial_world_half_extents_m": list(half),
                             "turn_angle_rad": math.atan2(lateral, forward)})
            if result.get("status") == "measured" and result.get("clear") is True:
                return (endpoint,), sweep_half, attempts
    return None, sweep_half, attempts


def _select_access(reader_factory, candidates, *, episode_id, time_s):
    for index, center in enumerate(candidates):
        region_id = f"rescue_access_{index}"
        row = {"min": [center[0] - 1.0, center[1] - 1.0, center[2] + .05],
               "max": [center[0] + 1.0, center[1] + 1.0, center[2] + 2.5]}
        reader = reader_factory({region_id: row})
        result = reader.read(region_id, episode_id=episode_id, time_s=time_s)
        if result.get("status") == "measured" and result.get("clear") is True:
            return region_id, row
    raise RuntimeError("no measured-clear rescue access region candidate")


def _execute(output_root: Path, prepared: Mapping[str, Any], *, run_id: str,
             headless: bool) -> dict[str, Any]:
    from simulator import SimulatorLaunchConfig, open_simulator_session

    seed, device = int(prepared["seed"]), str(prepared["device"])
    report = {"schema": SCHEMA, "version": 1, "status": "starting",
              "run_id": run_id, "episode_id": run_id, "scoreable": False,
              "execution_validated": False, "device_requested": device,
              "formal_episode_binding": False,
              "geometry_scope": "runtime_selected_candidate_diagnostic",
              "full_city_service": "pending_formal_episode_geometry",
              "trace": {"geometry": None, "services": {}}}

    def configure(env_cfg):
        from demo.city.main import configure_city_env_cfg

        configure_city_env_cfg(env_cfg, seed=seed, use_flow=False, people=1)
        env_cfg.sim.enable_scene_query_support = True
        report["query_support_configured"] = True

    config = SimulatorLaunchConfig(
        env="city_service_probe", resolved_env_name="EAI-Interactive-v0",
        selection_data=_selection(seed), num_envs=1, device=device, seed=seed,
        headless=headless, enable_ros_bridge_extension=False, disable_orsus_ros_env=True,
        env_cfg_hook=partial(configure))
    report_path = output_root / "service_probe_report.json"
    _checkpoint(output_root / "service_probe_start.json", report)
    with open_simulator_session(config) as session, _service_report_guard(report_path, report):
        import omni.physx
        from demo.city.main import _zero_actions
        from demo.city.runtime import CityEaiRuntime, CityRuntimeConfig
        from demo.city.usd_scene import CityNativeRegionReader, world_envelopes
        from demo.city_benchmark.live_witness import CityIncidentReceiver
        from demo.city_benchmark.native_runner import CityNativeServiceRunner

        city = CityEaiRuntime.from_current_stage(
            config=CityRuntimeConfig(seed=seed, use_flow=False, episode_id=run_id))
        city.reset()
        actions = _zero_actions(session)
        _step(session, city, actions)
        if not city.scene.people or not city._traffic_state:
            raise RuntimeError("City service probe requires one person and one following vehicle")
        person_id = city.people.actor_ids[0]
        person_record = next(row for row in city.scene.people if row["actor_id"] == person_id)
        vehicle_id = next(iter(city._traffic_state))
        entity_paths = {row["actor_id"]: row["path"] for row in city.scene.people}
        entity_paths.update({key: value["prim_path"] for key, value in city._traffic_state.items()})
        entity_paths["incident_vehicle"] = str(city.incident_vehicle[0].GetAttr().GetPrim().GetPath())

        def envelopes():
            return world_envelopes(city.stage, entity_paths)

        query = omni.physx.get_physx_scene_query_interface()
        ignored_paths = tuple(entity_paths.values())

        def reader_factory(regions):
            return CityNativeRegionReader(
                query=query, regions=regions, envelope_reader=envelopes,
                scene_queries_enabled=session.env_cfg.sim.enable_scene_query_support is True,
                device=str(session.device), current_gpu_geometry=False,
                ignored_collision_paths=ignored_paths)

        current_person = city.people.position(person_id)
        assembly_target = tuple(float(value) for value in person_record["waypoints"][1])
        assembly = {"min": [assembly_target[0] - .7, assembly_target[1] - .7,
                            assembly_target[2] - .05],
                    "max": [assembly_target[0] + .7, assembly_target[1] + .7,
                            assembly_target[2] + 2.4], "capacity": 1,
                    "query_min_z": assembly_target[2] + .05}
        incident = city.scene.plan.sample(city.elapsed).car_position
        hazard = {"min": [incident[0] - 4.0, incident[1] - 4.0, city.config.road_height + .05],
                  "max": [incident[0] + 4.0, incident[1] + 4.0, city.config.road_height + 3.0]}
        candidates = [(assembly_target[0] + dx, assembly_target[1] + dy, assembly_target[2])
                      for dx, dy in ((4, 0), (-4, 0), (0, 4), (0, -4), (6, 6), (-6, -6))]
        access_id, access = _select_access(
            reader_factory, candidates, episode_id=run_id, time_s=city.elapsed)
        regions = {"assembly_1": assembly, "hazard": hazard, access_id: access}
        reader = reader_factory(regions)
        initial_envelopes = envelopes()
        vehicle_half_raw = _bounds_half(initial_envelopes[vehicle_id], margin=.1)
        person_half_raw = _bounds_half(initial_envelopes[person_id], margin=.05)
        vehicle_half = (vehicle_half_raw[0], vehicle_half_raw[1], max(.05, vehicle_half_raw[2] - .05))
        person_half = (person_half_raw[0], person_half_raw[1], max(.05, person_half_raw[2] - .05))
        vehicle_pose = city.following_vehicle_pose(vehicle_id)
        vehicle_center = tuple((float(initial_envelopes[vehicle_id]["min"][axis])
                                + float(initial_envelopes[vehicle_id]["max"][axis])) * .5
                               for axis in range(3))
        person_center = tuple((float(initial_envelopes[person_id]["min"][axis])
                               + float(initial_envelopes[person_id]["max"][axis])) * .5
                              for axis in range(3))
        vehicle_offset = tuple(vehicle_center[axis] - vehicle_pose[axis] + (0.08 if axis == 2 else 0.0)
                               for axis in range(3))
        person_offset = tuple(person_center[axis] - current_person[axis] + (0.05 if axis == 2 else 0.0)
                              for axis in range(3))
        assembly = _assembly_region(assembly_target, person_half, person_offset)
        regions["assembly_1"] = assembly
        reader = reader_factory(regions)
        route = city._traffic_state[vehicle_id]["base_route"]
        ahead, yaw = route.sample(city._traffic_state[vehicle_id]["distance"] + 8.0, loop=True)
        lateral = (-math.sin(yaw) * 4.0, math.cos(yaw) * 4.0)
        divert = ((vehicle_pose[0] + lateral[0], vehicle_pose[1] + lateral[1], vehicle_pose[2]),
                  (ahead[0] + lateral[0], ahead[1] + lateral[1], vehicle_pose[2]))
        divert_check = reader.read_route(
            f"vehicle:{vehicle_id}:divert-preflight",
            tuple(tuple(point[axis] + vehicle_offset[axis] for axis in range(3))
                  for point in (vehicle_pose, *divert)),
            half_extents_m=vehicle_half, episode_id=run_id, time_s=city.elapsed,
            moving_entity_id=vehicle_id)
        report["trace"]["geometry"] = {
            "assembly": assembly, "hazard": hazard, "access": access,
            "vehicle_id": vehicle_id, "person_id": person_id,
            "initial_envelopes": initial_envelopes, "vehicle_pose": vehicle_pose,
            "vehicle_offset": vehicle_offset, "person_offset": person_offset,
            "vehicle_half": vehicle_half, "person_half": person_half,
            "divert_preflight": divert_check}
        _checkpoint(report_path, report)
        # This candidate is not activated. Recheck/select at the actual divert
        # request time after earlier services have changed world state.

        receiver = CityIncidentReceiver(episode_id=run_id, service_id="city_dispatch")
        assignments = {service: {"agent_id": "go2_1", "region_id": "road_surface"}
                       for service in ("guide_people", "control_traffic", "report_incident")}
        city.bind_city_service_sources(
            affected_people={person_id: {
                "speed_mps": 1.0, "motion": "walk", "assembly_region_id": "assembly_1",
                "assembly_waypoints": [assembly_target], "body_half_extents_m": person_half,
                "body_center_offset_m": person_offset}},
            following_vehicle_ids=(vehicle_id,),
            vehicle_divert_routes={vehicle_id: divert},
            vehicle_half_extents_m={vehicle_id: vehicle_half},
            vehicle_center_offsets_m={vehicle_id: vehicle_offset},
            assembly_regions={"assembly_1": assembly}, hazard_region_id="hazard",
            access_region_id=access_id, region_reader=reader,
            assignment=assignments, incident_receiver=receiver)
        runner = CityNativeServiceRunner(
            city, episode_id=run_id, agent_id="go2_1", region_id="road_surface",
            assembly_region_id="assembly_1", recipient_service_id="city_dispatch",
            role_source_id="isaac.robot_tensor_role", admission_source_id="isaac.native_region_reader",
            scene_query_support=True, clearance_query_provenance="episode_timestamped_physx_and_usd",
            physx_readback_suppressed=False)
        if runner.readiness["status"] != "ready":
            raise RuntimeError("City native runner is not ready: " + ",".join(runner.readiness["blockers"]))

        trace = report["trace"]
        trace["geometry"].update({
            "assembly": assembly, "hazard": hazard, "access": access,
            "divert": [list(point) for point in divert],
            "vehicle_half_extents_m": list(vehicle_half),
            "person_half_extents_m": list(person_half),
            "person_asset": {
                "path": person_record.get("source"),
                "sha256": person_record.get("source_hash"),
                "unchanged_during_spawn": person_record.get("source_unchanged") is True,
                "support_binding": person_record.get("support_binding"),
            },
        })
        _checkpoint(report_path, report)
        observation = runner.observe()
        _role(runner, "guidance_contact", session, trace)
        start_person = city.people.position(person_id)
        trace["guidance_admission"] = {
            "time_s": city.elapsed,
            "world_sample": city._episode_service._world_samples[-1],
            "route": city._episode_service._person_route(person_id),
            "person_id": person_id,
            "target_region_id": "assembly_1",
        }
        _checkpoint(report_path, report)
        guidance = runner.guide_people((person_id,), observation_ref=observation)
        trace["services"]["guide_people"] = {
            "status": "command_accepted_pending_readback",
            "request": guidance["request"],
            "position_before": list(start_person),
        }
        _checkpoint(report_path, report)
        for _ in range(800):
            _step(session, city, actions)
            if city._episode_service.person_readback(person_id)["arrived"]:
                break
        else:
            raise RuntimeError("affected person did not reach assembly within 800 steps")
        person_row = runner.record_person_arrival(
            guidance["request"]["request_id"], person_id, previous_position=start_person,
            assembly_region_id="assembly_1", arrived=False)
        trace["services"]["guide_people"] = {
            "status": "readback_recorded",
            "request": guidance["request"], "readback": person_row,
            "review": runner.review(guidance["request"]["request_id"])}
        _checkpoint(report_path, report)

        for mode in ("hold", "release", "divert"):
            observation = runner.observe()
            _role(runner, "traffic_control_point", session, trace)
            before = city.following_vehicle_pose(vehicle_id)
            if mode == "divert":
                current_bounds = envelopes()[vehicle_id]
                current_half = _bounds_half(current_bounds, margin=.1)
                current_half = (*current_half[:2], max(.05, current_half[2]-.05))
                chosen, swept_half, attempts = _divert_candidates(city, vehicle_id, reader, current_half, vehicle_offset)
                trace["divert_candidates"] = attempts
                _checkpoint(report_path, report)
                if chosen is None:
                    raise RuntimeError("all bounded divert candidates are blocked")
                city._episode_service.divert_routes[vehicle_id] = chosen
                city._episode_service.vehicle_half_extents[vehicle_id] = swept_half
            controlled = runner.control_following_traffic((vehicle_id,), mode, observation_ref=observation)
            trace["services"][f"traffic_{mode}"] = {
                "status": "command_accepted_pending_readback",
                "request": controlled["request"],
                "position_before": list(before),
            }
            _checkpoint(report_path, report)
            reaction_steps = max(1, math.ceil(5.1 / float(session.base_env.step_dt)))
            _step(session, city, actions, reaction_steps)
            readback = runner.record_vehicle_readback(
                controlled["request"]["request_id"], vehicle_id,
                previous_position=before, status_before=mode)
            trace["services"][f"traffic_{mode}"] = {
                "status": "readback_recorded",
                "request": controlled["request"], "position_before": list(before),
                "position_after": list(city.following_vehicle_pose(vehicle_id)),
                "readback": readback, "review": runner.review(controlled["request"]["request_id"])}
            _checkpoint(report_path, report)

        observation = runner.observe()
        _role(runner, "incident_observer", session, trace)
        incident_result = runner.report_incident(observation_ref=observation)
        trace["services"]["report_incident"] = {
            "status": "receipt_recorded_pending_continuous_access",
            **incident_result,
        }
        _checkpoint(report_path, report)
        receipt_time = city.elapsed
        _step(session, city, actions, max(1, math.ceil(.5 / float(session.base_env.step_dt))))
        access_result = runner.record_continuous_access(
            incident_result["request"], start_time_s=receipt_time, end_time_s=city.elapsed)
        trace["services"]["report_incident"] = {
            "status": "continuous_access_recorded", **incident_result, "access": access_result,
            "review": runner.review(incident_result["request"]["request_id"])}
        _checkpoint(report_path, report)
        reviews = [row["review"] for row in trace["services"].values()]
        report.update(status="completed", execution_validated=True,
                      candidate_witness="evidence_complete"
                      if all(row.get("status") == "evidence_complete" for row in reviews) else "pending",
                      full_city_service="pending_formal_episode_geometry",
                      device_actual=str(session.device), episode_time_s=city.elapsed,
                      source_hashes_after=_hashes())
        report["sources_unchanged"] = report["source_hashes_after"] == prepared["source_hashes"]
        if not report["sources_unchanged"]:
            raise RuntimeError("City service probe sources changed after prepare")
        _checkpoint(report_path, report)
    return report


def execute_probe(output_root: Path, *, device: str = "cpu", seed: int = 17,
                  headless: bool = True, overwrite: bool = False) -> dict[str, Any]:
    with _serial_lock():
        prepared = _load_prepare(output_root)
        if prepared["device"] != device or int(prepared["seed"]) != int(seed):
            raise ValueError("execute device/seed must match prepare")
        if prepared["source_hashes"] != _hashes():
            raise RuntimeError("City service probe sources changed; prepare again")
        if (output_root / "service_probe_start.json").exists() and not overwrite:
            raise FileExistsError("City service probe launch already exists")
        return _execute(output_root, prepared, run_id=str(uuid.uuid4()), headless=headless)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", action="store_true")
    mode.add_argument("--execute", action="store_true")
    parser.add_argument("--device", choices=("cpu", "cuda:0"), default="cpu")
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--gui", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    if args.prepare:
        result = prepare_probe(args.output_root, device=args.device, seed=args.seed,
                               overwrite=args.overwrite)
    else:
        result = execute_probe(args.output_root, device=args.device, seed=args.seed,
                               headless=not args.gui, overwrite=args.overwrite)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if args.prepare or result.get("status") == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
