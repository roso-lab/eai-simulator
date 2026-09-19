"""Read-only House adapters for a caller-owned Isaac session.

Native identity is instrumented from verified deployment/native composition
and PhysX tensors, NOT inferred from RGB or prim names. These adapters do not
launch Kit, plan a route, mutate actors, or decide business acceptance.
"""

from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path

from demo.benchmark import goal_native_objects
from demo.benchmark.goal_deployment import validate_goal_deployment
from demo.benchmark.provenance import canonical_sha256, file_sha256
from . import live_path


SCENE_KEY = "modern_house_nvd"


class NativeHouseBackendBlocked(ValueError):
    """Missing or incoherent evidence; no substitute claim may be emitted."""


def _require(condition, reason):
    if not condition:
        raise NativeHouseBackendBlocked(reason)


def _sha(value):
    _require(isinstance(value, str) and len(value) == 64
             and all(c in "0123456789abcdef" for c in value), "invalid SHA-256")
    return value


def _text(value):
    _require(isinstance(value, str) and bool(value.strip()), "nonempty identity required")
    return value


def _clock(session):
    sim = session.base_env.sim
    step, now = sim.current_time_step_index, sim.current_time
    _require(type(step) is int and step >= 0, "invalid native physics step")
    _require(type(now) in (int, float) and math.isfinite(now) and now >= 0,
             "invalid native simulation time")
    return {"physics_step": step, "sim_time_s": float(now)}


def _sources(extra=()):
    paths = (Path(__file__), Path(live_path.__file__), Path(goal_native_objects.__file__),
             Path(__file__).with_name("domain_adapter.py"),
             Path(goal_native_objects.__file__).with_name("reference_domain_providers.py"),
             Path(__file__).resolve().parents[2] / "simulator.py", *map(Path, extra))
    return {str(p.resolve()): file_sha256(p) for p in paths}


def _confirm_sources(files):
    for path, digest in files.items():
        _require(Path(path).is_file() and file_sha256(path) == digest,
                 f"native source changed or missing: {path}")


def _observer_position(session, agent):
    view = session.base_env.scene.articulations[agent].root_physx_view
    poses = view.get_root_transforms().detach().cpu().tolist()
    _require(len(poses) == 1 and len(poses[0]) == 7
             and all(math.isfinite(x) for x in poses[0]), "ambiguous/nonfinite camera producer pose")
    return poses[0][:3]


def _native_query(session, stage):
    import omni.physx
    from pxr import UsdGeom

    _require(session.env_cfg.sim.enable_scene_query_support is True,
             "scene queries disabled; enable before scene creation")
    _require(omni.physx.get_physx_interface().is_readback_suppressed() is False,
             "DirectGPU CPU queries use stale poses; dynamic visibility/clearance unknown")
    _require(UsdGeom.GetStageMetersPerUnit(stage) == 1.
             and UsdGeom.GetStageUpAxis(stage) == "Z", "metric Z-up stage required")
    session.base_env.sim.physics_sim_view.update_articulations_kinematic()
    return omni.physx.get_physx_scene_query_interface()


def _visible(query, observer, target, body_path, observer_body_paths):
    distance = math.dist(observer, target)
    _require(distance > 1e-6, "observer inside target; visibility ambiguous")
    direction = tuple((b-a)/distance for a, b in zip(observer, target))
    hits, excluded = [], []
    def collect(raw):
        collision = raw.get("collision") if isinstance(raw, dict) else getattr(raw, "collision", None)
        length = raw.get("distance") if isinstance(raw, dict) else getattr(raw, "distance", None)
        _require(isinstance(collision, str) and collision.startswith("/")
                 and type(length) in (int, float) and math.isfinite(length)
                 and 0 <= length <= distance + .001, "invalid native visibility hit")
        row = {"collision": collision, "distance": length}
        own = any(collision == p or collision.startswith(p + "/") for p in observer_body_paths)
        (excluded if own else hits).append(row)
        return True
    complete = query.raycast_all(tuple(observer), direction, distance + .001, collect, False)
    _require(type(complete) is bool and complete == bool(hits or excluded),
             "native visibility query completion mismatch")
    if not hits:
        return {"visible": False, "reason": "target_surface_not_hit", "distance_m": distance}
    hit = min(hits, key=lambda row: row["distance"])
    collision, length = hit.get("collision"), hit.get("distance")
    _require(isinstance(collision, str) and collision.startswith("/")
             and type(length) in (int, float) and math.isfinite(length)
             and 0 <= length <= distance + .001, "invalid native visibility hit")
    visible = collision == body_path or collision.startswith(body_path + "/")
    return {"visible": visible, "reason": "target_surface_hit" if visible else "occluded",
            "distance_m": distance, "hit_distance_m": length, "collision": collision,
            "excluded_observer_hits": excluded}


def _snapshot(session):
    """Author-only raw tensors; include every idle robot and loose object."""
    def values(tensor):
        if hasattr(tensor, "detach"):
            tensor = tensor.detach().cpu()
        result = tensor.tolist() if hasattr(tensor, "tolist") else deepcopy(tensor)
        def finite(row):
            return all(finite(v) for v in row) if isinstance(row, (list, tuple)) else (
                type(row) in (int, float) and math.isfinite(row))
        _require(finite(result), "nonfinite native scene snapshot")
        return result
    scene = session.base_env.scene
    robots, objects = {}, {}
    for name, asset in scene.articulations.items():
        view = asset.root_physx_view
        robots[name] = {"body_paths": deepcopy(view.link_paths),
                        "body_transforms_xyzw": values(view.get_link_transforms()),
                        "root_transforms_xyzw": values(view.get_root_transforms())}
    for name, asset in scene.rigid_objects.items():
        view = asset.root_physx_view
        objects[name] = {"body_paths": list(view.prim_paths),
                         "transforms_xyzw": values(view.get_transforms())}
    return {"robots": robots, "objects": objects, **_clock(session),
            "source": "root_physx_view", "scope": "author_only_full_scene_readback"}


def capture_native_observation(session, store, sensor_manager, *, episode_id,
                               observation_id, agent, target_ref, valid_for_s=1.):
    """Capture through the existing camera manager without advancing physics.

    The stamp records the physics step DURING acquisition; it does not claim
    render-product freshness or visibility. Use native simulation time in the
    subsequent provider request, not an unrelated scheduler clock.
    """
    before = _clock(session)
    pixels = sensor_manager.capture_camera_frame(agent)
    _require(pixels is not None, "native camera frame unavailable")
    position = _observer_position(session, agent)
    _require(_clock(session) == before, "physics advanced during frame acquisition")
    record = store.publish_frame(
        observation_id=observation_id, producer=agent, sensor="camera",
        sensor_config={"source": "Isaac camera manager",
                       "native_capture": {"episode_id": _text(episode_id), **before}},
        timestamp_s=before["sim_time_s"], frame_id=f"{agent}/camera_optical_frame",
        target_ref=target_ref, position_m=position, frame=pixels, valid_for_s=valid_for_s)
    return record, pixels


class _NativeSessionBackend:
    def __init__(self, *, session, stage, occupancy, episode_id):
        self.session, self.stage, self.occupancy = session, stage, occupancy
        self.episode_id = _text(episode_id)
        self.map_sha = _sha(getattr(occupancy, "sha256", None))
        self.stage_identifier = _text(stage.GetRootLayer().identifier)
        self.source_files = _sources()
        self.samples = []
        self.errors = []
        self._bound = False

    def _bind(self, context, schema):
        _require(not self._bound, "native adapter already bound")
        _require(context.get("schema") == schema and context.get("scene_key") == SCENE_KEY,
                 "native adapter binding scene/schema mismatch")
        task = context.get("public_task", {})
        _require(task.get("scene", {}).get("scene_key") == SCENE_KEY,
                 "House public task required")
        _require(not {"domain_contract", "hidden_domain_contract", "hidden_evaluator"} & set(task),
                 "hidden evaluator contract forbidden")
        self._bound = True

    def _frame(self, request, schema):
        _require(self._bound, "native adapter not bound")
        _require(not self.errors, "native adapter suppressed after prior readback error")
        _require(request.get("schema") == schema and request.get("scene_key") == SCENE_KEY,
                 "native frame request scene/schema mismatch")
        _confirm_sources(self.source_files)
        _require(self.stage.GetRootLayer().identifier == self.stage_identifier,
                 "native stage changed")
        _require(getattr(self.occupancy, "sha256", None) == self.map_sha,
                 "occupancy changed after binding")
        obs = request["observation"]
        cfg = obs["sensor_config"]
        clock = _clock(self.session)
        stamp = cfg.get("native_capture", {})
        _require(stamp.get("episode_id") == self.episode_id
                 and type(stamp.get("physics_step")) is int
                 and stamp.get("physics_step") == clock["physics_step"]
                 and type(stamp.get("sim_time_s")) in (int, float)
                 and stamp.get("sim_time_s") == clock["sim_time_s"],
                 "missing/stale native frame episode/physics-step capture")
        _require(obs.get("observation_id") == request.get("observation_id")
                 and obs.get("quality", {}).get("frame_acquired") is True,
                 "unbound or unacquired frame")
        _require(obs["timestamp_s"] == clock["sim_time_s"] == request["time_s"]
                 and obs["valid_until_s"] >= request["time_s"],
                 "frame and native simulation clocks differ")
        frame = request.get("frame")
        _require(frame is not None and getattr(frame, "size", 0) > 0,
                 "frame pixels unavailable")
        _require(hashlib.sha256(frame.tobytes()).hexdigest() == _sha(cfg.get("frame_sha256"))
                 and list(frame.shape) == cfg.get("frame_shape")
                 and str(frame.dtype) == cfg.get("frame_dtype"), "frame pixels/provenance mismatch")
        return {"episode_id": self.episode_id, "scene_key": SCENE_KEY,
                "stage_identifier": self.stage_identifier, **clock,
                "frame": {"observation_id": obs["observation_id"],
                          "frame_sha256": cfg["frame_sha256"], "frame_id": _text(obs.get("frame_id")),
                          "producer": _text(obs.get("producer")), "sensor_source": _text(cfg.get("source")),
                          "timestamp_s": obs["timestamp_s"],
                          "physics_step_basis": "acquisition_not_render_freshness"},
                "occupancy": {"sha256": self.map_sha,
                              "source": str(getattr(self.occupancy, "path", "host_bound_occupancy")),
                              "scope": "static_map_binding_not_dynamic_clearance"},
                "source_files": deepcopy(self.source_files)}

    def _unchanged(self, provenance):
        _require(_clock(self.session) == {k: provenance[k] for k in ("physics_step", "sim_time_s")},
                 "physics advanced during native readback")
        _require(self.stage.GetRootLayer().identifier == self.stage_identifier
                 and self.occupancy.sha256 == self.map_sha, "stage/occupancy changed during readback")
        _confirm_sources(self.source_files)

    def provenance(self):
        result = {"schema": "eai.benchmark.house_native_backend.v1",
                  "status": "blocked" if self.errors else "measured" if self.samples else "pending",
                  "samples": deepcopy(self.samples), "blockers": list(self.errors),
                  "scoreable": False, "visual_recognition_validated": False,
                  "business_outcomes_validated": False}
        result["sha256"] = canonical_sha256(result)
        return result


class HouseNativeSceneDetector(_NativeSessionBackend):
    """Ideal scene sensor: bounded range plus current PhysX line of sight.

    ``item_codes`` explicitly maps goal public object IDs to public item codes.
    These are author bindings, never hidden acceptance truth or name matching.
    The goal deployment must already have been spawned in this session using
    configure_goal_objects. Legacy fixed-DAG object IDs are NOT aliased.
    """

    def __init__(self, *, deployment, item_codes, max_range_m, **host):
        super().__init__(**host)
        validate_goal_deployment(deployment)
        _require(deployment["scene_key"] == SCENE_KEY, "House deployment required")
        self.deployment = deepcopy(deployment)
        objects = {row["public_id"] for row in deployment["object_instances"]}
        _require(isinstance(item_codes, dict) and item_codes and set(item_codes) <= objects,
                 "explicit deployed public object/item-code bindings required")
        self.item_codes = {_text(k): _text(v) for k, v in item_codes.items()}
        _require(type(max_range_m) in (int, float) and math.isfinite(max_range_m)
                 and 0 < max_range_m <= 10., "explicit bounded native sensor range (0, 10m] required")
        self.max_range_m = float(max_range_m)
        self.source_files.update(deployment["scene_overlay"]["source_files"])

    def bind_runtime(self, context):
        self._bind(context, "eai.benchmark.reference_detector_binding_v1")
        codes = {r["item_code"] for r in context["public_task"]["domain_requirements"]["items"]}
        _require(set(self.item_codes.values()) <= codes, "item codes outside public requirements")

    def detect(self, request):
        try:
            p = self._frame(request, "eai.benchmark.reference_detector_frame_v1")
            observer = _observer_position(self.session, request["observation"]["producer"])
            observer_paths = self.session.base_env.scene.articulations[
                request["observation"]["producer"]].root_physx_view.link_paths
            _require(len(observer_paths) == 1 and observer_paths[0]
                     and all(isinstance(p, str) and p.startswith("/") for p in observer_paths[0]),
                     "unbound observer rigid bodies")
            _require(request["observation"]["target_estimate"]["position_m"] == observer,
                     "frame producer pose differs from current native observer")
            query = _native_query(self.session, self.stage)
            readback = goal_native_objects.read_goal_objects(
                self.stage, self.session.base_env.scene, self.deployment)
            _require(readback["deployment_sha256"] == self.deployment["sha256"]
                     and readback["stage_identifier"] == self.stage_identifier,
                     "native deployment/stage mismatch")
            rows = readback["objects"]
            indexed = {r["public_id"]: r for r in rows}
            _require(len(indexed) == len(rows), "duplicate native identity")
            claims, bindings, visibility = [], [], []
            expected = {r["public_id"]: r for r in self.deployment["object_instances"]}
            for identifier, code in self.item_codes.items():
                row = indexed.get(identifier, {})
                pose, asset = row.get("physx", {}), row.get("native_source", {})
                _require(row.get("status") == "measured" and pose.get("status") == "measured"
                         and pose.get("source") == "root_physx_view"
                         and pose.get("prim_path") == expected[identifier]["prim_path"]
                         and row.get("runtime_id") == expected[identifier]["runtime_id"]
                         and asset.get("status") == "composed_reference_verified",
                         f"native object binding/readback unavailable: {identifier}")
                position = pose.get("position_m")
                _require(isinstance(position, list) and len(position) == 3
                         and all(type(x) in (int, float) and math.isfinite(x) for x in position),
                         "nonfinite native object pose")
                _sha(asset.get("sha256"))
                if math.dist(observer, position) > self.max_range_m:
                    visibility.append({"public_id": identifier, "visible": False, "reason": "out_of_range"})
                    continue
                sight = _visible(query, observer, position, pose["prim_path"], observer_paths[0])
                visibility.append({"public_id": identifier, **sight})
                if not sight["visible"]:
                    continue
                claim = {"detection_id": f"{self.episode_id}:{request['observation_id']}:native:{identifier}",
                         "detector_source": "ideal_scene_sensor.physx_range_raycast",
                         "detector_version": self.source_files[str(Path(__file__).resolve())],
                         "target_id": identifier, "item_code": code,
                         "estimated_position_m": list(position), "confidence": None,
                         "timestamp_s": request["time_s"]}
                claims.append(claim)
                bindings.append({"detection_id": claim["detection_id"], "public_id": identifier,
                                 "body_path": pose["prim_path"], "native_source": deepcopy(asset),
                                 "position_m": list(position)})
            self._unchanged(p)
            self.samples.append({**p, "source": "root_physx_view.get_transforms",
                                 "deployment_sha256": self.deployment["sha256"], "bindings": bindings,
                                 "native_object_readback": deepcopy(readback),
                                 "claims_sha256": canonical_sha256(claims),
                                 "observer_position_m": observer, "max_range_m": self.max_range_m,
                                 "sensor_model": "ideal_omnidirectional_robot_root_range_and_surface_ray",
                                 "visibility": visibility,
                                 "scope": "ideal_scene_sensor_not_rgb_recognition"})
            return claims
        except Exception as exc:
            self.errors.append(f"{type(exc).__name__}:{exc}")
            raise


class IsaacResidentPathBackend(_NativeSessionBackend):
    """Query a caller-chosen resident route, including idle robots and props."""

    def __init__(self, *, points, **host):
        super().__init__(**host)
        self.points = deepcopy(points)  # no default route or participant decisions

    def bind_runtime(self, context):
        self._bind(context, "eai.benchmark.modern_house_path_binding_v1")
        _require(context.get("base_env") is self.session.base_env
                 and context.get("occupancy") is self.occupancy
                 and context.get("occupancy_sha256") == self.map_sha, "path host binding mismatch")

    def measure_path(self, request):
        try:
            p = self._frame(request, "eai.benchmark.modern_house_path_request_v1")
            _require(request.get("occupancy_sha256") == self.map_sha, "path occupancy mismatch")
            query = _native_query(self.session, self.stage)
            context = {"episode_id": self.episode_id, "stage_identifier": self.stage_identifier,
                       "frame_id": "USD_world", "meters_per_unit": 1.,
                       "physics_step": p["physics_step"], "time_s": p["sim_time_s"],
                       "readback_frozen": True, "kinematics_current": True,
                       "scene_queries_enabled": True, "dynamic_query_pose_current": True}
            floors = live_path.static_floor_colliders(self.stage, self.points, width_m=.9)
            measured = live_path.measure_resident_path(
                query, points=self.points,
                context=context, support_colliders=floors)
            cover = live_path.static_support_cover(self.stage, measured)
            snapshot = _snapshot(self.session)
            self._unchanged(p)
            sample = {**p, "source": "physx_scene_queries_with_current_cpu_readback",
                      "phase": request.get("phase"), "measurement": measured,
                      "support_cover": cover, "scene_snapshot": snapshot}
            self.samples.append(sample)
            _require(measured["status"] in {"clear_volume_support_sampled", "blocked"}
                     and all(c["status"] != "unknown" for s in measured["segments"] for c in s["checks"]),
                     "resident query incomplete; measurement unknown")
            obstruction = any("resident_volume_obstructed" in b for b in measured["blockers"])
            unsupported = "support_samples_missing_or_uneven" in measured["blockers"]
            _require(obstruction or unsupported or cover["status"] in {"covered", "gap"},
                     "continuous support geometry unknown")
            status = "blocked" if obstruction or unsupported or cover["status"] == "gap" else "clear"
            sample["measurement_status"] = status
            # Full native receipts stay in provenance(), archived by the host.
            return {"observation_id": request["observation_id"], "occupancy_sha256": self.map_sha,
                    "points": deepcopy(self.points), "sample_time_s": request["time_s"],
                    "measurement_status": status}
        except Exception as exc:
            self.errors.append(f"{type(exc).__name__}:{exc}")
            raise


def probe_native_backends(*, session, stage, deployment, occupancy, sensor_manager,
                          agent, episode_id, item_codes, max_range_m, points, output_path):
    """One in-session author diagnostic; no stepping, closeout, or solver.

    Uses the real reference-provider binding/frame schemas. The small public
    packet labels diagnostic roles only; it is not the released business task.
    Archive this result inside the session: Kit teardown may exit the process.
    The report and native snapshots are AUTHOR-ONLY; only filtered claims are
    eligible for the participant observation store. This function does not
    publish claims to any participant or decide success.
    """
    from demo.benchmark.observations import ObservationStore
    from demo.benchmark.reference_domain_providers import (
        ReferenceFrameDetector, ModernHousePathReferenceProvider)

    report = {"schema": "eai.benchmark.house_native_diagnostic.v1", "episode_id": episode_id,
              "status": "pending", "scope": "author_only_native_backend_microdiagnostic",
              "scoreable": False, "business_outcomes_validated": False, "blockers": []}
    detector = path = None
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    _require(not output.exists(), "diagnostic report already exists; use a fresh path")
    try:
        host = dict(session=session, stage=stage, occupancy=occupancy, episode_id=episode_id)
        detector = HouseNativeSceneDetector(deployment=deployment, item_codes=item_codes,
                                            max_range_m=max_range_m, **host)
        path = IsaacResidentPathBackend(points=points, **host)
        public = {"scene": {"scene_key": SCENE_KEY}, "domain_requirements": {"items": [
            {"id": identifier, "item_code": code, "quantity": 1, "consumer_node": "diagnostic_only"}
            for identifier, code in item_codes.items()]}, "objectives": [
                {"id": "room_entry_scan", "target_ref": "entry_scan_zone"},
                {"id": "hall_release", "target_ref": "diagnostic_only"},
                {"id": "room_condition_scan", "target_ref": "room_review_zone",
                 "prerequisites": ["hall_release"]}]}
        binding = {"schema": "eai.benchmark.domain_runtime_binding_v1", "scene_key": SCENE_KEY,
                   "base_env": session.base_env, "occupancy": occupancy, "public_task": public}
        detector_provider = ReferenceFrameDetector(SCENE_KEY, backend=detector, scan_node_ids=["room_entry_scan"])
        path_provider = ModernHousePathReferenceProvider(path_backend=path)
        detector_provider.bind_runtime(binding)
        path_provider.bind_runtime(binding)
        record, pixels = capture_native_observation(
            session, ObservationStore(), sensor_manager, episode_id=episode_id,
            observation_id=f"{episode_id}:native-diagnostic:{_clock(session)['physics_step']}",
            agent=agent, target_ref="entry_scan_zone")
        report["observation"] = record
        context = {"schema": "eai.benchmark.domain_frame_v1", "scene_key": SCENE_KEY,
                   "observation_id": record["observation_id"], "observation": record,
                   "frame": pixels, "node_id": "room_entry_scan", "time_s": record["timestamp_s"]}
        for name, run in (("detector", lambda: detector_provider(context)),
                          ("path", lambda: path_provider.process_frame(context))):
            try:
                value = run()
                report[name] = {"status": "measured", **({"claims": value} if name == "detector" else {})}
            except Exception as exc:
                report[name] = {"status": "unknown", "reason": f"{type(exc).__name__}:{exc}"}
                report["blockers"].append(f"{name}:{type(exc).__name__}:{exc}")
        report["status"] = "unknown" if report["blockers"] else "measured"
    except Exception as exc:
        report["status"] = "unknown"
        report["blockers"].append(f"{type(exc).__name__}:{exc}")
    finally:
        report["detector_provenance"] = detector.provenance() if detector is not None else None
        report["path_provenance"] = path.provenance() if path is not None else None
        report["sha256"] = canonical_sha256(report)
        # Remain inside the caller's live session on both success and failure.
        with output.open("x") as stream:
            stream.write(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
    return report


__all__ = ["HouseNativeSceneDetector", "IsaacResidentPathBackend",
           "NativeHouseBackendBlocked", "capture_native_observation", "probe_native_backends"]
