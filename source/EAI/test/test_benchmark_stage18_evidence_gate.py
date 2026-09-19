"""Mutated archive evidence must not inherit a runner's success flag."""
from copy import deepcopy
import hashlib
import json
import math

import numpy as np
import pytest

from demo.benchmark import stage18_evidence_gate as gate
from demo.benchmark.provenance import canonical_sha256, file_sha256


def write(path, row):
    path.write_text(json.dumps(row))


def seal(row):
    return {**row, "sha256": canonical_sha256(row)}


def bind_process(root, report_name):
    process = gate._read(root / "process_result.json")
    process["report_sha256"] = file_sha256(root / report_name)
    write(root / "process_result.json", process)


@pytest.fixture
def archive(tmp_path):
    source = tmp_path / "source.py"
    source.write_text("# frozen source")
    request = {"source_files": {str(source): file_sha256(source)}}
    write(tmp_path / "backend_request.json", request)
    report = {"run_id": "episode", "status": "diagnostic_completed", "scoreable": False,
              "backend_request_sha256": canonical_sha256(request),
              "runtime_source_files": dict(request["source_files"]),
              "object_application": {"source_files": dict(request["source_files"])}}
    for phase, step in (("before", 10), ("after", 20)):
        frame = np.full((2, 2, 4), step, dtype=np.uint8)
        np.save(tmp_path / f"{phase}_frame.npy", frame, allow_pickle=False)
        digest = hashlib.sha256(frame.tobytes()).hexdigest()
        cfg = {"frame_shape": [2, 2, 4], "frame_dtype": "uint8", "frame_sha256": digest,
               "native_capture": {"episode_id": "episode", "physics_step": step, "sim_time_s": step / 10}}
        sample = {"physics_step": step, "sim_time_s": step / 10,
                  "frame": {"frame_sha256": digest}, "measurement_status": "blocked",
                  "source_files": dict(request["source_files"])}
        row = {"status": "measured", "blockers": [],
               "observation": {"observation_id": phase, "timestamp_s": step / 10, "sensor_config": cfg},
               "detector_provenance": {"status": "measured", "samples": [sample]},
               "path_provenance": {"status": "measured", "samples": [sample]}}
        row["sha256"] = canonical_sha256(row)
        path = tmp_path / f"{phase}.json"
        write(path, row)
        report[phase] = {"sha256": file_sha256(path)}
    write(tmp_path / "backend_report.json", report)
    write(tmp_path / "process_result.json", {"returncode": 0, "timed_out": False,
        "run_id": "episode", "report_sha256": file_sha256(tmp_path / "backend_report.json")})
    return tmp_path


def reseal(root, row):
    row.pop("sha256")
    row["sha256"] = canonical_sha256(row)
    write(root / "after.json", row)
    report = gate._read(root / "backend_report.json")
    report["after"]["sha256"] = file_sha256(root / "after.json")
    write(root / "backend_report.json", report)
    bind_process(root, "backend_report.json")


@pytest.fixture
def warehouse_archive(tmp_path):
    wrapper_source = tmp_path / "warehouse_wrapper.py"
    wrapper_source.write_text("# frozen warehouse wrapper")
    attachment_source = tmp_path / "warehouse_attachment.py"
    attachment_source.write_text("# frozen warehouse attachment")
    wrapper_sources = {str(wrapper_source): file_sha256(wrapper_source)}
    attachment_sources = {str(attachment_source): file_sha256(attachment_source)}
    attachment_request = {"run_id": "warehouse-episode", "source_files": attachment_sources}
    write(tmp_path / "request.json", attachment_request)
    request = {"run_id": "warehouse-episode", "source_files": wrapper_sources,
               "attachment_request_sha256": canonical_sha256(attachment_request),
               "lift_height_m": .12, "scoreable": False}
    write(tmp_path / "witness_request.json", request)

    joint = {"prim_path": "/World/box/joint", "joint_enabled": True,
             "local_pos0": [0., 0., 0.]}
    receipt_base = {"status": "committed", "scoreable": False, "token": "hold",
                    "object_id": "box", "stage_identifier": "stage",
                    "edit_layer_identifier": "stage", "joint_readback": [joint]}
    original = seal({**receipt_base, "checks": {"physics_step": 10}})
    apex_joint = {**joint, "local_pos0": [0., 0., .12]}
    apex = seal({**receipt_base, "joint_readback": [apex_joint], "checks": {"physics_step": 10},
                 "lift_actuation": {"physics_step": 18, "target_offset_m": .12,
                                    "body_pose_writes": 0,
                                    "baseline_local_pos0": {"/World/box/joint": [0., 0., 0.]}}})
    final = seal({**receipt_base, "checks": {"physics_step": 10},
                  "lift_actuation": {"physics_step": 35, "target_offset_m": 0.,
                                     "body_pose_writes": 0,
                                     "baseline_local_pos0": {"/World/box/joint": [0., 0., 0.]}}})
    detach = seal({"status": "detached", "already_detached": False, "token": "hold",
                   "object_id": "box", "attachment_receipt_sha256": final["sha256"],
                   "attachment_physics_step": 35, "removed_joint_paths": ["/World/box/joint"],
                   "before_removal_readback": [joint]})
    release = {"source": "physx", "body_path": "/World/box", "active": True,
               "physics_step": 50, "position_m": [1., 0., .15],
               "rotation_wxyz": [1., 0., 0., 0.]}
    dock = seal({"state": "ready", "episode_id": "warehouse-episode", "object_id": "box",
                 "object_runtime_path": "/World/box", "object_pose_m": [1., 0., .15],
                 "object_aabb_xyz_m": [.8, -.15, 0., 1.2, .15, .3],
                 "target_aabb_xy_m": [.5, -.5, 1.5, .5], "physics_step": 50,
                 "release_step": 40, "removed_joint_paths": ["/World/box/joint"],
                 "enabled_object_joints": [], "attachment_receipt_sha256": final["sha256"],
                 "detach_receipt_sha256": detach["sha256"]})
    samples = [
        {"phase": "attached_lifted_transport", "source": "physx", "active": True,
         "body_path": "/World/box", "physics_step": 20, "position_m": [0., 0., .25],
         "rotation_wxyz": [1., 0., 0., 0.], "object_bottom_z_m": .1,
         "carrier": {"source": "physx", "active": True, "physics_step": 20,
                     "position_m": [0., 1., .5]}},
        {"phase": "attached_lifted_transport", "source": "physx", "active": True,
         "body_path": "/World/box", "physics_step": 30, "position_m": [1., 0., .25],
         "rotation_wxyz": [1., 0., 0., 0.], "object_bottom_z_m": .1,
         "carrier": {"source": "physx", "active": True, "physics_step": 30,
                     "position_m": [1., 1., .5]}},
    ]
    object_row = {"public_id": "box", "usd": {"world_mesh_bounds_m": {"size": [.4, .3, .3]}}}
    report = {
        "run_id": "warehouse-episode", "status": "diagnostic_completed", "scoreable": False,
        "request_sha256": canonical_sha256(attachment_request),
        "witness_request_sha256": canonical_sha256(request),
        "object_application": {"source_files": attachment_sources},
        "initial_objects": {"objects": [object_row]},
        "final_objects": {"objects": [deepcopy(object_row)]},
        "attachment_receipt": original, "apex_attachment_receipt": apex,
        "actuated_attachment_receipt": final, "detach_receipt": detach,
        "samples": samples,
        "carry_readback": {
            "pre_attach_pose_m": [0., 0., .15], "last_attached_pose_m": [1., 0., .25],
            "object_displacement_m": math.sqrt(1.01),
            "carrier_pre_transport_pose_m": [0., 1., .5],
            "carrier_last_transport_pose_m": [1., 1., .5], "carrier_displacement_m": 1.,
            "attachment_anchor_quality": [{"anchor_error_m": 0., "orientation_error_rad": 0.}],
            "physics_step": 30},
        "lift_evidence": {"status": "measured", "object": {"source": "physx", "active": True,
                            "position_m": [0., 0., .25], "rotation_wxyz": [1., 0., 0., 0.]},
                          "bottom_z_m": .1, "target_offset_m": .12, "body_pose_writes": 0,
                          "minimum_transport_bottom_z_m": .1},
        "release_step": 40, "release_readback": release, "dock_receipt": dock,
    }
    write(tmp_path / "warehouse_witness_report.json", report)
    write(tmp_path / "process_result.json", {"returncode": 0, "timed_out": False,
        "run_id": "warehouse-episode", "report_sha256": file_sha256(tmp_path / "warehouse_witness_report.json")})
    return tmp_path


def live_evidence(episode, ref, kind, entities, time):
    return {"episode_id": episode, "evidence_ref": ref, "kind": kind,
            "source_id": "isaac.city", "entity_ids": list(entities), "observed_time_s": time,
            "status_before": {"status": "before"}, "status_after": {"status": "after"}}


def service_request(request_id, service, submitted, *, entities=(), mode=None,
                    target=None, recipient=None):
    return {"request_id": request_id, "service": service, "agent_id": "go2_1",
            "region_id": "road", "entity_ids": list(entities), "observation_refs": ["obs-1"],
            "mode": mode, "recipient_service_id": recipient, "target_region_id": target,
            "submitted_time_s": submitted, "status": "pending",
            "evidence_class": "request_only", "scoreable": False}


def reviewed(request, evidence):
    return {**deepcopy(request), "status": "evidence_complete", "evidence_class": "runtime",
            "evidence": evidence, "live_witness": True, "scoreable": False}


def native_region(episode, region_id, region, time, *, dynamic=None):
    dynamic = deepcopy(dynamic or {})
    query_min = list(region["min"])
    if "query_min_z" in region:
        query_min[2] = max(query_min[2], region["query_min_z"])
    return {"region_id": region_id, "episode_id": episode, "observed_time_s": time,
            "source": "native_city_region_reader", "status": "measured",
            "clear": not dynamic, "region_aabb_m": {"min": list(region["min"]),
                                                        "max": list(region["max"])},
            "static_query_aabb_m": {"min": query_min, "max": list(region["max"])},
            "region_metadata": ({"capacity": region["capacity"],
                                 "query_min_z": region["query_min_z"]}
                                if "capacity" in region else {}),
            "static_hits": [], "dynamic_envelopes": dynamic,
            "all_dynamic_envelopes": deepcopy(dynamic),
            "query_context": {"scene_queries_enabled": True, "device": "cpu",
                              "current_gpu_geometry": False}}


@pytest.fixture
def city_archive(tmp_path):
    episode = "city-episode"
    source = tmp_path / "city_source.py"
    source.write_text("# frozen city source")
    sources = {str(source): file_sha256(source)}
    prepared = {"schema": "eai.city.native_service_probe_v1", "version": 1,
                "status": "prepared", "scoreable": False, "device": "cpu",
                "source_hashes": sources}
    write(tmp_path / "service_probe_prepare.json", prepared)

    geometry = {
        "assembly": {"min": [0., 0., 0.], "max": [2., 2., 2.],
                     "capacity": 1, "query_min_z": .05},
        "hazard": {"min": [2., 2., 0.], "max": [4., 4., 2.]},
        "access": {"min": [5., 5., 0.], "max": [6., 6., 2.]},
        "divert": [[0., 0., 0.], [1., 1., 0.]],
        "vehicle_half_extents_m": [1., .5, .5], "person_half_extents_m": [.3, .3, 1.],
        "person_asset": {"path": str(source), "sha256": file_sha256(source),
                         "unchanged_during_spawn": True,
                         "support_binding": {
                             "source": "authored_collidable_sidewalk_triangles",
                             "height_m": 0., "sample_spacing_m": .25,
                             "corridor_half_width_m": .5,
                             "samples": [
                                 {"position_m": [.5, .5, 0.], "collision_path": "/sidewalk"},
                                 {"position_m": [1., 1., 0.], "collision_path": "/sidewalk"},
                             ]}},
    }
    guide = service_request("guide", "guide_people", 1., entities=("person",),
                            target="assembly_1")
    person_trajectory = live_evidence(episode, "person-trajectory", "person_world_readback", ("person",), 3.)
    person_safety = live_evidence(episode, "person-safety", "person_world_readback", ("person",), 3.)
    guide_actor = {"id": "person", "ack_time_s": 2., "arrival_time_s": 3.,
                   "assembly_region_id": "assembly_1", "measured_trajectory": True,
                   "safe_route_and_assembly": True,
                   "trajectory_evidence_ref": "person-trajectory", "safety_evidence_ref": "person-safety"}
    person_envelope = {"person": {"prim_path": "/World/person", "min": [.7, .7, 0.],
                                   "max": [1.3, 1.3, 1.8]}}
    assembly_readback = native_region(episode, "assembly_1", geometry["assembly"], 3.,
                                      dynamic=person_envelope)
    services = {"guide_people": {
        "status": "readback_recorded", "request": guide,
        "readback": {"person_id": "person", "position_before": [.5, .5, 0.],
                     "position_after": [1., 1., 0.], "measured_trajectory": True,
                     "arrived_at_assembly": True, "safe_route_and_assembly": True,
                     "native_readback": {"person_id": "person", "position_xyz": [1., 1., 0.],
                                         "assembly_region_id": "assembly_1", "path_finished": True,
                                         "inside_assembly": True, "arrived": True,
                                         "safe_route_and_assembly": True,
                                         "trajectory_evidence_ref": "person-trajectory",
                                         "safety_evidence_ref": "person-safety",
                                         "world_readback": assembly_readback},
                     "trajectory_evidence": person_trajectory, "safety_evidence": person_safety},
        "review": reviewed(guide, {"request_id": "guide", "service": "guide_people",
                                   "evidence_class": "runtime", "episode_id": episode,
                                   "source_id": "isaac.city", "actors": [guide_actor]})}}
    for index, mode in enumerate(("hold", "release", "divert"), start=1):
        request = service_request(f"traffic-{mode}", "control_traffic", 3. + index,
                                  entities=("vehicle",), mode=mode)
        checked = 9. + index
        refs = {name: live_evidence(episode, f"{mode}-{name}", "traffic_world_readback",
                                    ("vehicle",), checked)
                for name in ("trajectory", "hazard", "access")}
        actor = {"id": "vehicle", "ack_time_s": 5. + index,
                 "trajectory_check_time_s": checked, "applied_mode": mode,
                 "measured_trajectory": True, "no_hazard_ingress": True,
                 "rescue_access_preserved": True,
                 **{f"{name}_evidence_ref": value["evidence_ref"] for name, value in refs.items()}}
        before = [0., 0., 0.]
        after = [0., 0., 0.] if mode == "hold" else [float(index), 0., 0.]
        times = [5. + index, checked]
        trajectory_samples = [{"time_s": times[0], "position_xyz": before, "mode": mode},
                              {"time_s": times[1], "position_xyz": after, "mode": mode}]
        safety_samples = []
        for time in times:
            safety_samples.append({
                "time_s": time,
                "hazard": native_region(episode, "hazard", geometry["hazard"], time),
                "access": native_region(episode, "access", geometry["access"], time),
            })
        readback = {"vehicle_id": "vehicle", "position_before": before,
                    "position_after": after, "measured_trajectory": True,
                    "no_hazard_ingress": True, "rescue_access_preserved": True,
                    "trajectory_check_time_s": checked,
                    "native_readback": {
                        "no_hazard_ingress": True, "rescue_access_preserved": True,
                        "trajectory_samples": trajectory_samples, "safety_samples": safety_samples,
                        "hazard_evidence_ref": refs["hazard"]["evidence_ref"],
                        "access_evidence_ref": refs["access"]["evidence_ref"],
                        "hazard_readback": deepcopy(safety_samples[-1]["hazard"]),
                        "access_readback": deepcopy(safety_samples[-1]["access"]),
                    },
                    **{f"{name}_evidence": value for name, value in refs.items()}}
        services[f"traffic_{mode}"] = {
            "status": "readback_recorded", "request": request,
            "position_before": readback["position_before"], "position_after": readback["position_after"],
            "readback": readback,
            "review": reviewed(request, {"request_id": request["request_id"],
                                         "service": "control_traffic", "evidence_class": "runtime",
                                         "episode_id": episode, "source_id": "isaac.city",
                                         "actors": [actor]})}

    incident = service_request("incident", "report_incident", 20., recipient="dispatch")
    receipt = {"request_id": "incident", "episode_id": episode,
               "recipient_service_id": "dispatch", "observation_refs": ["obs-1"],
               "received_time_s": 21., "status": "received",
               "receipt_evidence_ref": "incident-receipt", "source_id": "dispatch.receiver"}
    access_evidence = live_evidence(episode, "access-window", "access_readback", (), 22.)
    access_samples = [native_region(episode, "access", geometry["access"], time)
                      for time in (20.5, 22.)]
    access = {"measured_clear_throughout": True,
              "rescue_and_evacuation_routes_admitted": True,
              "evidence_ref": "access-window", "sample_count": len(access_samples),
              "samples": access_samples, "evidence": access_evidence}
    incident_evidence = {"request_id": "incident", "service": "report_incident",
                         "evidence_class": "runtime", "episode_id": episode,
                         "source_id": "isaac.city",
                         "receipt": {"recipient_service_id": "dispatch", "observation_refs": ["obs-1"],
                                     "received_time_s": 21.,
                                     "receipt_evidence_ref": "incident-receipt"},
                         "continuous_access": {"start_time_s": 20.5, "end_time_s": 22.,
                                               "measured_clear_throughout": True,
                                               "rescue_and_evacuation_routes_admitted": True,
                                               "access_evidence_ref": "access-window"}}
    services["report_incident"] = {"status": "continuous_access_recorded", "request": incident,
                                    "receipt": receipt, "access": access,
                                    "review": reviewed(incident, incident_evidence)}
    report = {"schema": prepared["schema"], "version": 1, "status": "completed",
              "run_id": episode, "episode_id": episode, "scoreable": False,
              "execution_validated": True, "candidate_witness": "evidence_complete",
              "query_support_configured": True, "formal_episode_binding": False,
              "device_requested": "cpu", "sources_unchanged": True,
              "source_hashes_after": sources,
              "trace": {"geometry": geometry, "services": services}}
    write(tmp_path / "service_probe_report.json", report)
    return tmp_path


def test_valid_blocked_path_is_diagnostic_but_never_scoreable(archive):
    result = gate.review(house_root=archive, warehouse_root=archive / "absent")
    assert result["scenes"]["house"]["status"] == "diagnostic_only"
    assert result["scenes"]["house"]["current_source_coverage"]
    assert result["scenes"]["warehouse"]["status"] == "blocked"
    assert result["release_status"] == "pending" and result["scoreable"] is False


@pytest.mark.parametrize("change", ["pixels", "mixed_step", "same_id", "unknown", "other_episode"])
def test_rehashed_but_incoherent_native_evidence_is_rejected(archive, change):
    row = gate._read(archive / "after.json")
    if change == "pixels":
        np.save(archive / "after_frame.npy", np.zeros((2, 2, 4), dtype=np.uint8), allow_pickle=False)
    elif change == "mixed_step":
        row["path_provenance"]["samples"][0]["physics_step"] -= 1
    elif change == "same_id":
        row["observation"]["observation_id"] = "before"
    elif change == "unknown":
        row["path_provenance"]["samples"][0]["measurement_status"] = "unknown"
    else:
        row["observation"]["sensor_config"]["native_capture"]["episode_id"] = "other"
    reseal(archive, row)
    with pytest.raises(ValueError):
        gate.house(archive)


def test_changed_source_does_not_erase_history_or_claim_current_coverage(archive):
    (archive / "source.py").write_text("# new source")
    result = gate.review(house_root=archive, warehouse_root=archive / "absent")
    house = result["scenes"]["house"]
    assert house["status"] == "diagnostic_only" and not house["current_source_coverage"]
    assert house["changed_sources"] == [str(archive / "source.py")]


def test_house_rejects_blocked_report_and_tracks_all_runtime_sources(archive):
    report = gate._read(archive / "backend_report.json")
    report["status"] = "blocked"
    report["blocker"] = "producer declared incomplete"
    write(archive / "backend_report.json", report)
    bind_process(archive, "backend_report.json")
    with pytest.raises(ValueError, match="House diagnostic incomplete"):
        gate.house(archive)

    report["status"] = "diagnostic_completed"
    extra = archive / "late_runtime.py"
    extra.write_text("# frozen runtime")
    report["runtime_source_files"][str(extra)] = file_sha256(extra)
    write(archive / "backend_report.json", report)
    bind_process(archive, "backend_report.json")
    extra.write_text("# changed runtime")
    result = gate.review(house_root=archive, warehouse_root=archive / "absent")
    assert result["scenes"]["house"]["status"] == "diagnostic_only"
    assert not result["scenes"]["house"]["current_source_coverage"]
    assert result["scenes"]["house"]["changed_sources"] == [str(extra)]


@pytest.mark.parametrize("change", ["stationary_summary", "clearance", "dock", "detach"])
def test_warehouse_recomputes_physical_content_and_receipts(warehouse_archive, change):
    assert gate.warehouse(warehouse_archive)["status"] == "diagnostic_only"
    report = gate._read(warehouse_archive / "warehouse_witness_report.json")
    if change == "stationary_summary":
        report["carry_readback"]["last_attached_pose_m"] = \
            list(report["carry_readback"]["pre_attach_pose_m"])
    elif change == "clearance":
        report["samples"][0]["object_bottom_z_m"] = -.1
    elif change == "dock":
        dock = report["dock_receipt"]
        dock["object_pose_m"] = [100., 100., .15]
        dock["object_aabb_xyz_m"] = [99.8, 99.8, 0., 100.2, 100.2, .3]
        dock.pop("sha256")
        dock["sha256"] = canonical_sha256(dock)
    else:
        detach = report["detach_receipt"]
        detach["status"] = "blocked"
        detach.pop("sha256")
        detach["sha256"] = canonical_sha256(detach)
        dock = report["dock_receipt"]
        dock["detach_receipt_sha256"] = detach["sha256"]
        dock.pop("sha256")
        dock["sha256"] = canonical_sha256(dock)
    write(warehouse_archive / "warehouse_witness_report.json", report)
    bind_process(warehouse_archive, "warehouse_witness_report.json")
    with pytest.raises(ValueError):
        gate.warehouse(warehouse_archive)


def test_warehouse_tracks_nested_attachment_sources(warehouse_archive):
    source = warehouse_archive / "warehouse_attachment.py"
    source.write_text("# changed attachment")
    result = gate.review(house_root=warehouse_archive / "absent",
                         warehouse_root=warehouse_archive)
    warehouse = result["scenes"]["warehouse"]
    assert warehouse["status"] == "diagnostic_only"
    assert not warehouse["current_source_coverage"]
    assert warehouse["changed_sources"] == [str(source)]


@pytest.mark.parametrize("change", ["prepare", "people", "traffic", "incident"])
def test_city_requires_prepare_bound_auditable_trace(city_archive, change):
    assert gate.city(city_archive)["status"] == "diagnostic_only"
    prepared = gate._read(city_archive / "service_probe_prepare.json")
    report = gate._read(city_archive / "service_probe_report.json")
    if change == "prepare":
        path = next(iter(prepared["source_hashes"]))
        prepared["source_hashes"][path] = "0" * 64
    elif change == "people":
        envelope = report["trace"]["services"]["guide_people"]["readback"] \
            ["native_readback"]["world_readback"]["dynamic_envelopes"]["person"]
        envelope["max"][0] = 3.
    elif change == "traffic":
        hazard = report["trace"]["services"]["traffic_divert"]["readback"] \
            ["native_readback"]["safety_samples"][0]["hazard"]
        hazard["dynamic_envelopes"]["vehicle"] = {
            "prim_path": "/World/vehicle", "min": [2.5, 2.5, 0.], "max": [3., 3., 1.]}
    else:
        access = report["trace"]["services"]["report_incident"]["access"]
        access["samples"][0]["static_hits"] = [{"prim_path": "/World/blocked"}]
    write(city_archive / "service_probe_prepare.json", prepared)
    write(city_archive / "service_probe_report.json", report)
    with pytest.raises(ValueError, match="diagnostic_pending_until_auditable_trace"):
        gate.city(city_archive)
