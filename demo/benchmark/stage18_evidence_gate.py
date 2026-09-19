"""Review archived Stage18 diagnostics; never authorize formal scoring.

This gate checks evidence contents and source fingerprints independently of
the runner's success label. Historical diagnostics and current source coverage
are separate fields. The original archived reports are never rewritten.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path

from .provenance import canonical_sha256, file_sha256


def _read(path):
    return json.loads(Path(path).read_text())


def _require(condition, reason):
    if not condition:
        raise ValueError(reason)


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _mapping(value, reason):
    _require(isinstance(value, dict), reason)
    return value


def _vector(value, size, reason):
    _require(isinstance(value, (list, tuple)) and len(value) == size
             and all(_number(item) for item in value), reason)
    return [float(item) for item in value]


def _close(left, right, *, tolerance=1e-6):
    return _number(left) and _number(right) and math.isclose(
        left, right, rel_tol=1e-6, abs_tol=tolerance)


def _same_vector(left, right, *, tolerance=1e-6):
    return (isinstance(left, (list, tuple)) and isinstance(right, (list, tuple))
            and len(left) == len(right)
            and all(_close(a, b, tolerance=tolerance) for a, b in zip(left, right)))


def _merge_sources(*inventories):
    merged = {}
    for inventory in inventories:
        _require(isinstance(inventory, dict) and inventory, "missing frozen source inventory")
        for path, digest in inventory.items():
            _require(isinstance(path, str) and path and isinstance(digest, str) and digest,
                     "invalid frozen source inventory")
            _require(path not in merged or merged[path] == digest,
                     f"conflicting source fingerprint: {path}")
            merged[path] = digest
    return merged


def _rotated_half_extents(size, rotation):
    size = _vector(size, 3, "invalid object size")
    rotation = _vector(rotation, 4, "invalid object rotation")
    _require(all(value > 0 for value in size), "invalid object size")
    _require(_close(sum(value * value for value in rotation), 1., tolerance=1e-5),
             "object rotation is not normalized")
    w, x, y, z = rotation
    matrix = (
        (1 - 2 * (y*y + z*z), 2 * (x*y - z*w), 2 * (x*z + y*w)),
        (2 * (x*y + z*w), 1 - 2 * (x*x + z*z), 2 * (y*z - x*w)),
        (2 * (x*z - y*w), 2 * (y*z + x*w), 1 - 2 * (x*x + y*y)),
    )
    return [sum(abs(matrix[axis][item]) * size[item] / 2 for item in range(3))
            for axis in range(3)]


def _envelope(position, rotation, size):
    position = _vector(position, 3, "invalid object position")
    half = _rotated_half_extents(size, rotation)
    return [position[axis] - half[axis] for axis in range(3)] + \
        [position[axis] + half[axis] for axis in range(3)]


def _process(root, report_name):
    process = _read(root / "process_result.json")
    report = _read(root / report_name)
    _require(process.get("returncode") == 0 and process.get("timed_out") is False,
             "worker failed or timed out")
    _require(process.get("run_id") == report.get("run_id")
             and process.get("report_sha256") == file_sha256(root / report_name),
             "worker/report binding differs")
    _require(report.get("scoreable") is False, "diagnostic attempted scoring promotion")
    return report


def _current_sources(files):
    _require(isinstance(files, dict) and files, "missing frozen source inventory")
    return [path for path, digest in files.items()
            if not Path(path).is_file() or file_sha256(path) != digest]


def house(root):
    import numpy as np
    report = _process(root, "backend_report.json")
    request = _read(root / "backend_request.json")
    _require(report.get("backend_request_sha256") == canonical_sha256(request), "House request mismatch")
    _require(report.get("status") == "diagnostic_completed", "House diagnostic incomplete")
    request_sources = _mapping(request.get("source_files"), "missing House request sources")
    runtime_sources = _mapping(report.get("runtime_source_files"), "missing House runtime sources")
    _require(set(request_sources) <= set(runtime_sources), "House runtime source coverage incomplete")
    sources = _merge_sources(request_sources, runtime_sources,
                             report["object_application"].get("source_files"))
    steps, observations = [], []
    for phase in ("before", "after"):
        path = root / f"{phase}.json"
        _require(report[phase]["sha256"] == file_sha256(path), "House snapshot file changed")
        row = _read(path)
        _require(row.get("sha256") == canonical_sha256({k: v for k, v in row.items() if k != "sha256"}),
                 "House snapshot seal changed")
        _require(row.get("status") == "measured" and not row.get("blockers"), "House backend not measured")
        obs = row["observation"]
        frame = np.load(root / f"{phase}_frame.npy", allow_pickle=False)
        cfg = obs["sensor_config"]
        _require(frame.size > 0 and hashlib.sha256(frame.tobytes()).hexdigest() == cfg["frame_sha256"]
                 and list(frame.shape) == cfg["frame_shape"] and str(frame.dtype) == cfg["frame_dtype"],
                 "House archived pixels differ from observation")
        stamp = cfg["native_capture"]
        _require(stamp["episode_id"] == report["run_id"], "House frame belongs to another episode")
        for key in ("detector_provenance", "path_provenance"):
            samples = row[key]["samples"]
            _require(row[key]["status"] == "measured" and len(samples) == 1, "House provenance incomplete")
            sample = samples[0]
            _require(sample["physics_step"] == stamp["physics_step"]
                     and sample["sim_time_s"] == stamp["sim_time_s"] == obs["timestamp_s"]
                     and sample["frame"]["frame_sha256"] == cfg["frame_sha256"], "House mixed-step readback")
            sources = _merge_sources(sources, sample.get("source_files"))
        _require(row["path_provenance"]["samples"][0]["measurement_status"] in
                 {"blocked", "current_resident_corridor_clear"}, "House path unknown")
        steps.append(stamp["physics_step"])
        observations.append(obs["observation_id"])
    _require(steps[1] > steps[0] and len(set(observations)) == 2, "House snapshots are not independent in time")
    return {"status": "diagnostic_only", "physics_steps": steps,
            "changed_sources": _current_sources(sources),
            "pending": ["business_intervention_and_closeout", "formal_goal_collector_episode"]}


def warehouse(root):
    from .goal_native_attachment import _check_seal
    from demo.warehouse_benchmark.native_witness_probe import require_anchor_quality
    report = _process(root, "warehouse_witness_report.json")
    request = _read(root / "witness_request.json")
    attachment_request = _read(root / "request.json")
    attachment_digest = canonical_sha256(attachment_request)
    _require(report.get("witness_request_sha256") == canonical_sha256(request)
             and request.get("attachment_request_sha256") == attachment_digest
             and report.get("request_sha256") == attachment_digest,
             "Warehouse request mismatch")
    _require(report.get("status") == "diagnostic_completed", "Warehouse diagnostic incomplete")
    for key in ("attachment_receipt", "apex_attachment_receipt", "actuated_attachment_receipt", "detach_receipt", "dock_receipt"):
        _check_seal(report[key])
    sources = _merge_sources(request.get("source_files"), attachment_request.get("source_files"),
                             report["object_application"].get("source_files"))

    carry, lift = report["carry_readback"], report["lift_evidence"]
    before = _vector(carry.get("pre_attach_pose_m"), 3, "Warehouse object start pose missing")
    after = _vector(carry.get("last_attached_pose_m"), 3, "Warehouse object end pose missing")
    carrier_before = _vector(carry.get("carrier_pre_transport_pose_m"), 3,
                             "Warehouse carrier start pose missing")
    carrier_after = _vector(carry.get("carrier_last_transport_pose_m"), 3,
                            "Warehouse carrier end pose missing")
    object_displacement = math.dist(before, after)
    carrier_displacement = math.dist(carrier_before, carrier_after)
    _require(object_displacement > .05 and carrier_displacement > .05
             and _close(carry.get("object_displacement_m"), object_displacement)
             and _close(carry.get("carrier_displacement_m"), carrier_displacement),
             "Warehouse transport summary differs from poses")
    require_anchor_quality(carry["attachment_anchor_quality"])

    dock = report["dock_receipt"]
    object_id = dock.get("object_id")
    initial = [row for row in report["initial_objects"]["objects"]
               if row.get("public_id") == object_id]
    final_objects = [row for row in report["final_objects"]["objects"]
                     if row.get("public_id") == object_id]
    _require(len(initial) == len(final_objects) == 1, "Warehouse object readback missing")
    size = _vector(initial[0]["usd"]["world_mesh_bounds_m"].get("size"), 3,
                   "Warehouse object size missing")
    _require(_same_vector(size, final_objects[0]["usd"]["world_mesh_bounds_m"].get("size")),
             "Warehouse object size changed")

    samples = report.get("samples")
    _require(isinstance(samples, list) and len(samples) >= 2, "Warehouse transport samples missing")
    sample_steps, bottoms = [], []
    for sample in samples:
        position = _vector(sample.get("position_m"), 3, "Warehouse sample object pose missing")
        rotation = _vector(sample.get("rotation_wxyz"), 4, "Warehouse sample object rotation missing")
        carrier = _mapping(sample.get("carrier"), "Warehouse sample carrier missing")
        step = sample.get("physics_step")
        _require(sample.get("phase") == "attached_lifted_transport"
                 and sample.get("source") == carrier.get("source") == "physx"
                 and sample.get("active") is carrier.get("active") is True
                 and type(step) is int and step == carrier.get("physics_step"),
                 "Warehouse transport sample provenance differs")
        bottom = _envelope(position, rotation, size)[2]
        _require(_close(sample.get("object_bottom_z_m"), bottom),
                 "Warehouse sample envelope differs")
        sample_steps.append(step)
        bottoms.append(bottom)
    _require(all(right > left for left, right in zip(sample_steps, sample_steps[1:])),
             "Warehouse transport samples are not ordered")
    _require(_same_vector(samples[-1]["position_m"], after)
             and _same_vector(samples[-1]["carrier"]["position_m"], carrier_after)
             and samples[-1]["physics_step"] == carry.get("physics_step"),
             "Warehouse carry summary differs from final sample")
    minimum_bottom = min(bottoms)
    _require(minimum_bottom >= .02 and _close(lift.get("minimum_transport_bottom_z_m"), minimum_bottom),
             "Warehouse lift clearance differs from samples")

    lifted = _mapping(lift.get("object"), "Warehouse lift object readback missing")
    lifted_bottom = _envelope(lifted.get("position_m"), lifted.get("rotation_wxyz"), size)[2]
    _require(lift.get("status") == "measured" and lifted.get("source") == "physx"
             and lifted.get("active") is True and _close(lift.get("bottom_z_m"), lifted_bottom)
             and _close(lift.get("target_offset_m"), request.get("lift_height_m"))
             and lift.get("body_pose_writes") == 0,
             "Warehouse lift readback differs")

    original, final = report["attachment_receipt"], report["actuated_attachment_receipt"]
    apex, detach = report["apex_attachment_receipt"], report["detach_receipt"]
    identity = ("token", "object_id", "stage_identifier", "edit_layer_identifier")
    apex_actuation, final_actuation = apex.get("lift_actuation"), final.get("lift_actuation")
    _require(all(row.get("status") == "committed" and row.get("scoreable") is False
                 for row in (original, apex, final))
             and all(apex.get(key) == final.get(key) == original.get(key) for key in identity)
             and isinstance(original.get("joint_readback"), list) and original["joint_readback"]
             and isinstance(apex.get("joint_readback"), list)
             and len(apex["joint_readback"]) == len(original["joint_readback"])
             and final["joint_readback"] == original["joint_readback"]
             and isinstance(apex_actuation, dict) and isinstance(final_actuation, dict)
             and _close(apex_actuation.get("target_offset_m"), request.get("lift_height_m"))
             and _close(final_actuation.get("target_offset_m"), 0)
             and apex_actuation.get("body_pose_writes") == 0
             and final_actuation.get("body_pose_writes") == 0,
             "Warehouse lift target roundtrip drift")
    for baseline_row, apex_row in zip(original["joint_readback"], apex["joint_readback"]):
        path = baseline_row.get("prim_path")
        baseline = apex_actuation.get("baseline_local_pos0", {}).get(path)
        unchanged = {key: value for key, value in baseline_row.items() if key != "local_pos0"}
        apex_unchanged = {key: value for key, value in apex_row.items() if key != "local_pos0"}
        _require(_same_vector(baseline, baseline_row.get("local_pos0"))
                 and apex_unchanged == unchanged
                 and _close(math.dist(_vector(apex_row.get("local_pos0"), 3,
                                                     "Warehouse apex target missing"), baseline),
                            apex_actuation["target_offset_m"]),
                 "Warehouse apex target differs from lift receipt")
    _require(final_actuation.get("baseline_local_pos0") == apex_actuation.get("baseline_local_pos0"),
             "Warehouse final lift baseline differs")
    joint_paths = [row.get("prim_path") for row in final["joint_readback"]]
    _require(detach.get("status") == "detached" and detach.get("already_detached") is False
             and detach.get("token") == final.get("token") and detach.get("object_id") == object_id
             and detach.get("attachment_receipt_sha256") == final.get("sha256")
             and detach.get("attachment_physics_step") == final_actuation.get("physics_step")
             and detach.get("removed_joint_paths") == joint_paths
             and detach.get("before_removal_readback") == final["joint_readback"],
             "Warehouse detach receipt differs")

    release = _mapping(report.get("release_readback"), "Warehouse release readback missing")
    release_step = report.get("release_step")
    release_envelope = _envelope(release.get("position_m"), release.get("rotation_wxyz"), size)
    target = _vector(dock.get("target_aabb_xy_m"), 4, "Warehouse dock target missing")
    _require(dock.get("state") == "ready" and dock.get("episode_id") == report.get("run_id")
             and dock.get("object_id") == final.get("object_id")
             and release.get("source") == "physx" and release.get("active") is True
             and dock.get("object_runtime_path") == release.get("body_path")
             and dock.get("physics_step") == release.get("physics_step")
             and type(release_step) is int and dock["physics_step"] > release_step
             and dock["release_step"] == report["release_step"]
             and _same_vector(dock.get("object_pose_m"), release.get("position_m"))
             and _same_vector(dock.get("object_aabb_xyz_m"), release_envelope)
             and target[0] <= release_envelope[0] <= release_envelope[3] <= target[2]
             and target[1] <= release_envelope[1] <= release_envelope[4] <= target[3]
             and dock.get("removed_joint_paths") == detach.get("removed_joint_paths")
             and dock.get("enabled_object_joints") == []
             and dock["attachment_receipt_sha256"] == final["sha256"]
             and dock["detach_receipt_sha256"] == detach["sha256"], "Warehouse dock evidence chain differs")
    return {"status": "diagnostic_only", "object_displacement_m": object_displacement,
            "minimum_transport_bottom_z_m": minimum_bottom,
            "changed_sources": _current_sources(sources),
            "pending": ["paired_formation_and_carry", "safe_hold_reacquire", "complete_order_business_episode"]}


def _city_pending(reason):
    raise ValueError(f"City diagnostic_pending_until_auditable_trace: {reason}")


def _city_evidence(value, *, episode_id, kind, entity_ids):
    if not isinstance(value, dict):
        _city_pending(f"missing {kind} evidence")
    if (value.get("episode_id") != episode_id or value.get("kind") != kind
            or value.get("entity_ids") != list(entity_ids)
            or not isinstance(value.get("evidence_ref"), str) or not value["evidence_ref"]
            or not isinstance(value.get("source_id"), str) or not value["source_id"]
            or not _number(value.get("observed_time_s"))
            or not isinstance(value.get("status_before"), dict) or not value["status_before"]
            or not isinstance(value.get("status_after"), dict) or not value["status_after"]
            or value["status_before"] == value["status_after"]):
        _city_pending(f"invalid {kind} evidence")
    return value


def _city_region(value, *, episode_id, region_id, geometry, geometry_key=None,
                 require_clear=False):
    if not isinstance(value, dict):
        _city_pending(f"missing native region readback: {region_id}")
    geometry_key = geometry_key or ("assembly" if region_id == "assembly_1" else region_id)
    region = _mapping(geometry.get(geometry_key), f"City geometry region missing: {region_id}")
    low = _vector(region.get("min"), 3, f"City {region_id} minimum missing")
    high = _vector(region.get("max"), 3, f"City {region_id} maximum missing")
    readback_box = _mapping(value.get("region_aabb_m"), f"City {region_id} readback AABB missing")
    query_box = _mapping(value.get("static_query_aabb_m"),
                         f"City {region_id} query AABB missing")
    query_low = list(low)
    if _number(region.get("query_min_z")):
        query_low[2] = max(query_low[2], float(region["query_min_z"]))
    context = value.get("query_context")
    dynamic = value.get("dynamic_envelopes")
    if (value.get("episode_id") != episode_id or value.get("region_id") != region_id
            or value.get("source") != "native_city_region_reader"
            or value.get("status") != "measured" or not _number(value.get("observed_time_s"))
            or not _same_vector(readback_box.get("min"), low)
            or not _same_vector(readback_box.get("max"), high)
            or not _same_vector(query_box.get("min"), query_low)
            or not _same_vector(query_box.get("max"), high)
            or not isinstance(value.get("static_hits"), list)
            or not isinstance(dynamic, dict)
            or not isinstance(value.get("all_dynamic_envelopes"), dict)
            or not isinstance(context, dict)
            or context.get("scene_queries_enabled") is not True
            or context.get("device") != "cpu"
            or context.get("current_gpu_geometry") is not False):
        _city_pending(f"invalid native region readback: {region_id}")
    if require_clear and (value.get("clear") is not True or value["static_hits"] or dynamic):
        _city_pending(f"native region is not continuously clear: {region_id}")
    return value


def _city_support(person_asset, before, after):
    support = person_asset.get("support_binding")
    if not isinstance(support, dict):
        _city_pending("person support binding missing")
    height = support.get("height_m")
    spacing = support.get("sample_spacing_m")
    samples = support.get("samples")
    if (support.get("source") != "authored_collidable_sidewalk_triangles"
            or not _number(height) or not _number(spacing) or spacing <= 0
            or not isinstance(samples, list) or not samples):
        _city_pending("person support binding invalid")
    points = []
    for sample in samples:
        if not isinstance(sample, dict) or not isinstance(sample.get("collision_path"), str) \
                or not sample["collision_path"]:
            _city_pending("person support sample provenance missing")
        point = _vector(sample.get("position_m"), 3, "person support sample position missing")
        if not _close(point[2], height):
            _city_pending("person support sample height differs")
        points.append(point)
    if (not _close(before[2], height) or not _close(after[2], height)
            or any(min(math.dist(position[:2], point[:2]) for point in points) > spacing + 1e-6
                   for position in (before, after))):
        _city_pending("person trajectory is not bound to the sidewalk support")


def _city_service(row, *, key, service, episode_id):
    if not isinstance(row, dict):
        _city_pending(f"missing service {key}")
    request, review = row.get("request"), row.get("review")
    if not isinstance(request, dict) or not isinstance(review, dict):
        _city_pending(f"missing request/review for {key}")
    identities = ("request_id", "service", "agent_id", "region_id", "entity_ids",
                  "observation_refs", "mode", "recipient_service_id", "target_region_id",
                  "submitted_time_s")
    if (request.get("service") != service or request.get("status") != "pending"
            or request.get("evidence_class") != "request_only" or request.get("scoreable") is not False
            or not isinstance(request.get("request_id"), str) or not request["request_id"]
            or not isinstance(request.get("observation_refs"), list) or not request["observation_refs"]
            or len(request["observation_refs"]) != len(set(request["observation_refs"]))
            or not _number(request.get("submitted_time_s"))
            or any(review.get(field) != request.get(field) for field in identities)
            or review.get("status") != "evidence_complete"
            or review.get("evidence_class") != "runtime" or review.get("live_witness") is not True
            or review.get("scoreable") is not False):
        _city_pending(f"incomplete service review for {key}")
    evidence = review.get("evidence")
    if (not isinstance(evidence, dict) or evidence.get("request_id") != request["request_id"]
            or evidence.get("service") != service or evidence.get("evidence_class") != "runtime"
            or evidence.get("episode_id") != episode_id
            or not isinstance(evidence.get("source_id"), str) or not evidence["source_id"]):
        _city_pending(f"service evidence identity differs for {key}")
    return request, evidence


def _city_people(row, *, episode_id, geometry):
    request, evidence = _city_service(row, key="guide_people", service="guide_people",
                                      episode_id=episode_id)
    ids = request.get("entity_ids")
    actors = evidence.get("actors")
    readback = row.get("readback")
    if (row.get("status") != "readback_recorded" or not isinstance(ids, list) or not ids
            or not isinstance(request.get("target_region_id"), str) or not request["target_region_id"]
            or not isinstance(actors, list) or {actor.get("id") for actor in actors} != set(ids)
            or len(actors) != len(ids) or not isinstance(readback, dict)
            or readback.get("person_id") not in ids
            or readback.get("measured_trajectory") is not True
            or readback.get("arrived_at_assembly") is not True
            or readback.get("safe_route_and_assembly") is not True):
        _city_pending("people guidance readback incomplete")
    before = _vector(readback.get("position_before"), 3, "City person start pose missing")
    after = _vector(readback.get("position_after"), 3, "City person end pose missing")
    trajectory = _city_evidence(readback.get("trajectory_evidence"), episode_id=episode_id,
                                kind="person_world_readback", entity_ids=(readback["person_id"],))
    safety = _city_evidence(readback.get("safety_evidence"), episode_id=episode_id,
                            kind="person_world_readback", entity_ids=(readback["person_id"],))
    actor = next(item for item in actors if item.get("id") == readback["person_id"])
    if (not _number(actor.get("ack_time_s")) or not _number(actor.get("arrival_time_s"))
            or not request["submitted_time_s"] <= actor["ack_time_s"] <= actor["arrival_time_s"]
            or actor.get("assembly_region_id") != request["target_region_id"]
            or actor.get("measured_trajectory") is not True
            or actor.get("safe_route_and_assembly") is not True
            or actor.get("trajectory_evidence_ref") != trajectory["evidence_ref"]
            or actor.get("safety_evidence_ref") != safety["evidence_ref"]):
        _city_pending("people guidance receipt differs from world readback")
    native = readback.get("native_readback")
    if not isinstance(native, dict):
        _city_pending("people native readback missing")
    assembly_id = request["target_region_id"]
    if assembly_id != "assembly_1":
        _city_pending("people assembly region identity differs")
    world = _city_region(native.get("world_readback"), episode_id=episode_id,
                         region_id=assembly_id, geometry=geometry)
    envelopes = world["dynamic_envelopes"]
    envelope = envelopes.get(readback["person_id"])
    region = geometry["assembly"]
    envelope_low = _vector(envelope.get("min") if isinstance(envelope, dict) else None, 3,
                           "City person envelope minimum missing")
    envelope_high = _vector(envelope.get("max") if isinstance(envelope, dict) else None, 3,
                            "City person envelope maximum missing")
    if (native.get("person_id") != readback["person_id"]
            or native.get("assembly_region_id") != assembly_id
            or not _same_vector(native.get("position_xyz"), after)
            or native.get("path_finished") is not True or native.get("inside_assembly") is not True
            or native.get("arrived") is not True or native.get("safe_route_and_assembly") is not True
            or native.get("trajectory_evidence_ref") != trajectory["evidence_ref"]
            or native.get("safety_evidence_ref") != safety["evidence_ref"]
            or world.get("static_hits") != [] or set(envelopes) != {readback["person_id"]}
            or not isinstance(envelope, dict)
            or world.get("region_metadata", {}).get("capacity") != region.get("capacity")
            or not all(region["min"][axis] <= envelope_low[axis]
                       <= envelope_high[axis] <= region["max"][axis]
                       for axis in range(3))):
        _city_pending("people assembly envelope readback differs")
    if (not _close(actor["arrival_time_s"], world["observed_time_s"])
            or not _close(trajectory["observed_time_s"], world["observed_time_s"])
            or not _close(safety["observed_time_s"], world["observed_time_s"])):
        _city_pending("people readback time differs")
    _city_support(geometry["person_asset"], before, after)


def _city_traffic(row, *, key, mode, episode_id):
    request, evidence = _city_service(row, key=key, service="control_traffic",
                                      episode_id=episode_id)
    ids, actors, readback = request.get("entity_ids"), evidence.get("actors"), row.get("readback")
    if (row.get("status") != "readback_recorded" or request.get("mode") != mode
            or not isinstance(ids, list) or not ids or not isinstance(actors, list)
            or {actor.get("id") for actor in actors} != set(ids) or len(actors) != len(ids)
            or not isinstance(readback, dict) or readback.get("vehicle_id") not in ids
            or readback.get("measured_trajectory") is not True
            or readback.get("no_hazard_ingress") is not True
            or readback.get("rescue_access_preserved") is not True):
        _city_pending(f"traffic readback incomplete for {mode}")
    before = _vector(readback.get("position_before"), 3, "City vehicle start pose missing")
    after = _vector(readback.get("position_after"), 3, "City vehicle end pose missing")
    if not _same_vector(row.get("position_before"), before) or not _same_vector(row.get("position_after"), after):
        _city_pending(f"traffic pose summary differs for {mode}")
    refs = {}
    for field in ("trajectory", "hazard", "access"):
        item = _city_evidence(readback.get(f"{field}_evidence"), episode_id=episode_id,
                              kind="traffic_world_readback", entity_ids=(readback["vehicle_id"],))
        refs[field] = item["evidence_ref"]
    actor = next(item for item in actors if item.get("id") == readback["vehicle_id"])
    if (not _number(actor.get("ack_time_s")) or not _number(actor.get("trajectory_check_time_s"))
            or not request["submitted_time_s"] <= actor["ack_time_s"] <= actor["trajectory_check_time_s"]
            or not _close(actor["trajectory_check_time_s"], readback.get("trajectory_check_time_s"))
            or actor.get("applied_mode") != mode or actor.get("measured_trajectory") is not True
            or actor.get("no_hazard_ingress") is not True
            or actor.get("rescue_access_preserved") is not True
            or actor.get("trajectory_evidence_ref") != refs["trajectory"]
            or actor.get("hazard_evidence_ref") != refs["hazard"]
            or actor.get("access_evidence_ref") != refs["access"]):
        _city_pending(f"traffic receipt differs from world readback for {mode}")


def _city_traffic_trace(row, *, mode, episode_id, geometry):
    readback = row["readback"]
    vehicle_id = readback["vehicle_id"]
    native = readback.get("native_readback")
    if not isinstance(native, dict):
        _city_pending(f"continuous traffic trace missing for {mode}")
    trajectory = native.get("trajectory_samples")
    safety = native.get("safety_samples")
    final_hazard = native.get("hazard_readback")
    final_access = native.get("access_readback")
    hazard_id = final_hazard.get("region_id") if isinstance(final_hazard, dict) else None
    access_id = final_access.get("region_id") if isinstance(final_access, dict) else None
    if (not isinstance(trajectory, list) or len(trajectory) < 2
            or not isinstance(safety, list) or len(safety) != len(trajectory)
            or not isinstance(hazard_id, str) or not hazard_id
            or not isinstance(access_id, str) or not access_id
            or native.get("no_hazard_ingress") is not True
            or native.get("rescue_access_preserved") is not True
            or native.get("hazard_evidence_ref") != readback["hazard_evidence"]["evidence_ref"]
            or native.get("access_evidence_ref") != readback["access_evidence"]["evidence_ref"]):
        _city_pending(f"continuous traffic trace incomplete for {mode}")
    times = []
    for index, (actor_sample, safety_sample) in enumerate(zip(trajectory, safety)):
        if not isinstance(actor_sample, dict) or not isinstance(safety_sample, dict):
            _city_pending(f"invalid traffic sample for {mode}")
        time_s = actor_sample.get("time_s")
        position = _vector(actor_sample.get("position_xyz"), 3,
                           f"City traffic sample pose missing for {mode}")
        sample_mode = actor_sample.get("mode")
        if (not _number(time_s) or not isinstance(sample_mode, str) or not sample_mode
                or (index > 0 and sample_mode != mode)
                or not _close(safety_sample.get("time_s"), time_s)):
            _city_pending(f"traffic actor/safety sample differs for {mode}")
        hazard = _city_region(safety_sample.get("hazard"), episode_id=episode_id,
                              region_id=hazard_id, geometry=geometry, geometry_key="hazard")
        access = _city_region(safety_sample.get("access"), episode_id=episode_id,
                              region_id=access_id, geometry=geometry, geometry_key="access",
                              require_clear=True)
        if vehicle_id in hazard["dynamic_envelopes"]:
            _city_pending(f"traffic entered hazard during {mode}")
        if (not _close(hazard["observed_time_s"], time_s)
                or not _close(access["observed_time_s"], time_s)):
            _city_pending(f"traffic region sample time differs for {mode}")
        times.append(float(time_s))
    actor = next(item for item in row["review"]["evidence"]["actors"]
                 if item.get("id") == vehicle_id)
    if (not all(right > left for left, right in zip(times, times[1:]))
            or not _close(times[0], actor["ack_time_s"])
            or not _close(times[-1], actor["trajectory_check_time_s"])
            or not _same_vector(trajectory[0]["position_xyz"], readback["position_before"])
            or not _same_vector(trajectory[-1]["position_xyz"], readback["position_after"])
            or native.get("hazard_readback") != safety[-1]["hazard"]
            or native.get("access_readback") != safety[-1]["access"]):
        _city_pending(f"traffic trace endpoints differ for {mode}")


def _city_incident(row, *, episode_id, geometry):
    request, evidence = _city_service(row, key="report_incident", service="report_incident",
                                      episode_id=episode_id)
    receipt, access = row.get("receipt"), row.get("access")
    stored_receipt, stored_access = evidence.get("receipt"), evidence.get("continuous_access")
    if (row.get("status") != "continuous_access_recorded" or not isinstance(receipt, dict)
            or not isinstance(access, dict) or not isinstance(stored_receipt, dict)
            or not isinstance(stored_access, dict)
            or receipt.get("status") != "received" or receipt.get("request_id") != request["request_id"]
            or receipt.get("episode_id") != episode_id
            or receipt.get("recipient_service_id") != request.get("recipient_service_id")
            or receipt.get("observation_refs") != request.get("observation_refs")
            or not _number(receipt.get("received_time_s"))
            or receipt["received_time_s"] < request["submitted_time_s"]
            or stored_receipt.get("recipient_service_id") != receipt["recipient_service_id"]
            or stored_receipt.get("observation_refs") != receipt["observation_refs"]
            or not _close(stored_receipt.get("received_time_s"), receipt["received_time_s"])
            or stored_receipt.get("receipt_evidence_ref") != receipt.get("receipt_evidence_ref")):
        _city_pending("incident receipt identity differs")
    access_evidence = _city_evidence(access.get("evidence"), episode_id=episode_id,
                                     kind="access_readback", entity_ids=())
    start, end = stored_access.get("start_time_s"), stored_access.get("end_time_s")
    samples = access.get("samples")
    if (not _number(start) or not _number(end) or not start < end
            or not start <= receipt["received_time_s"] <= end
            or access.get("measured_clear_throughout") is not True
            or access.get("rescue_and_evacuation_routes_admitted") is not True
            or stored_access.get("measured_clear_throughout") is not True
            or stored_access.get("rescue_and_evacuation_routes_admitted") is not True
            or stored_access.get("access_evidence_ref") != access.get("evidence_ref")
            or access.get("evidence_ref") != access_evidence["evidence_ref"]
            or not _close(access_evidence["observed_time_s"], end)
            or not isinstance(samples, list) or len(samples) < 2
            or access.get("sample_count") != len(samples)):
        _city_pending("continuous access readback differs")
    times = []
    access_region_id = samples[0].get("region_id") if isinstance(samples[0], dict) else None
    if not isinstance(access_region_id, str) or not access_region_id:
        _city_pending("continuous access region identity missing")
    for sample in samples:
        checked = _city_region(sample, episode_id=episode_id, region_id=access_region_id,
                               geometry=geometry, geometry_key="access", require_clear=True)
        times.append(float(checked["observed_time_s"]))
    if (not all(right > left for left, right in zip(times, times[1:]))
            or not _close(times[0], start) or not _close(times[-1], end)):
        _city_pending("continuous access interval differs from samples")


def city(root):
    prepared = _read(root / "service_probe_prepare.json")
    report = _read(root / "service_probe_report.json")
    _require(report.get("scoreable") is False, "City diagnostic attempted scoring promotion")
    if (report.get("status") != "completed" or report.get("sources_unchanged") is not True):
        _city_pending(report.get("blocker", report.get("status", "incomplete")))
    if (prepared.get("schema") != report.get("schema") or prepared.get("version") != report.get("version")
            or prepared.get("status") != "prepared" or prepared.get("scoreable") is not False
            or prepared.get("device") != report.get("device_requested")
            or prepared.get("source_hashes") != report.get("source_hashes_after")):
        _city_pending("prepare/report binding differs")
    episode_id = report.get("episode_id")
    if (not isinstance(episode_id, str) or not episode_id or report.get("run_id") != episode_id
            or report.get("execution_validated") is not True
            or report.get("candidate_witness") != "evidence_complete"
            or report.get("query_support_configured") is not True
            or report.get("formal_episode_binding") is not False):
        _city_pending("episode execution flags incomplete")
    geometry = report.get("trace", {}).get("geometry")
    if not isinstance(geometry, dict):
        _city_pending("geometry trace missing")
    for key in ("assembly", "hazard", "access"):
        region = geometry.get(key)
        if not isinstance(region, dict):
            _city_pending(f"geometry region missing: {key}")
        low = _vector(region.get("min"), 3, f"City {key} minimum missing")
        high = _vector(region.get("max"), 3, f"City {key} maximum missing")
        if not all(a < b for a, b in zip(low, high)):
            _city_pending(f"geometry region invalid: {key}")
    divert = geometry.get("divert")
    if not isinstance(divert, list) or len(divert) < 2:
        _city_pending("vehicle divert trace missing")
    for point in divert:
        _vector(point, 3, "City divert point missing")
    _vector(geometry.get("vehicle_half_extents_m"), 3, "City vehicle envelope missing")
    _vector(geometry.get("person_half_extents_m"), 3, "City person envelope missing")
    person_asset = geometry.get("person_asset")
    if (not isinstance(person_asset, dict) or person_asset.get("unchanged_during_spawn") is not True
            or not isinstance(person_asset.get("path"), str)
            or not isinstance(person_asset.get("sha256"), str)):
        _city_pending("person asset provenance missing")
    services = report.get("trace", {}).get("services", {})
    required = {"guide_people", "traffic_hold", "traffic_release", "traffic_divert", "report_incident"}
    if not isinstance(services, dict) or set(services) != required:
        _city_pending("service trace set incomplete")
    _city_people(services["guide_people"], episode_id=episode_id, geometry=geometry)
    for mode in ("hold", "release", "divert"):
        row = services[f"traffic_{mode}"]
        _city_traffic(row, key=f"traffic_{mode}", mode=mode, episode_id=episode_id)
        _city_traffic_trace(row, mode=mode, episode_id=episode_id, geometry=geometry)
    _city_incident(services["report_incident"], episode_id=episode_id, geometry=geometry)
    city_sources = _merge_sources(prepared["source_hashes"], report["source_hashes_after"],
                                  {person_asset["path"]: person_asset["sha256"]})
    return {"status": "diagnostic_only",
            "changed_sources": _current_sources(city_sources),
            "pending": ["formal_geometry_binding", "two_independent_business_witnesses", "factor_freeze"]}


def review(*, house_root, warehouse_root, city_root=None):
    result = {"schema": "eai.benchmark.stage18_evidence_gate", "scoreable": False,
              "release_status": "pending", "scenes": {}}
    for name, fn, root in (("house", house, house_root), ("warehouse", warehouse, warehouse_root),
                           ("city", city, city_root)):
        if root is None:
            result["scenes"][name] = {"status": "pending", "blockers": ["diagnostic_not_supplied"]}
            continue
        try:
            row = fn(Path(root))
            row["current_source_coverage"] = not row["changed_sources"]
            result["scenes"][name] = row
        except (ValueError, KeyError, TypeError, OSError) as exc:
            result["scenes"][name] = {"status": "blocked", "blockers": [str(exc)]}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--house-root", type=Path, required=True)
    parser.add_argument("--warehouse-root", type=Path, required=True)
    parser.add_argument("--city-root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = review(house_root=args.house_root, warehouse_root=args.warehouse_root, city_root=args.city_root)
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2, ensure_ascii=False)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
