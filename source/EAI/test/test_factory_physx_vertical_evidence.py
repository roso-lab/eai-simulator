"""Factory F-VERT-01 PhysX evidence adapter and fail-closed validation."""

import json
import math

from demo.benchmark.provenance import file_sha256
from demo.factory_benchmark.physx_vertical_evidence import (
    PhysxReferenceCapture, capture_contact_sample, capture_raycast_patch,
    validate_physx_evidence,
)
from demo.factory_benchmark.vertical_challenge import DEFAULT_SCENARIO, compile_challenge


STATIC = "/World/Factory/Floor"
BODY_PATHS = {name: f"/World/envs/env_0/go2_1/{name}"
              for name in ("FL_foot", "FR_foot", "RL_foot", "RR_foot")}


class _Query:
    def __init__(self, hit):
        self.hit = hit

    def raycast_all(self, origin, direction, distance, callback):
        callback(self.hit)


class _ContactView:
    def get_contact_data(self, *, dt):
        assert math.isclose(dt, 0.005)
        return ([[10.0], [float("nan")]],
                [[0.0, 0.0, 0.0], [float("nan")] * 3],
                [[0.0, 0.0, 1.0], [float("nan")] * 3],
                [[-0.001], [float("nan")]], [[1]], [[0]])


def _raycast(query=None):
    hit = {"collision": STATIC, "rigid_body": STATIC,
           "position": [0.0, 0.0, -0.1], "normal": [0.0, 0.0, 1.0],
           "distance": 0.1}
    return capture_raycast_patch(
        query or _Query(hit), [0.0, 0.0, 0.0], offsets_xy_m=[[0.0, 0.0]],
        robot_roots=["/World/envs/env_0/go2_1"],
        static_collision_paths=[STATIC],
        classify=lambda item: ("dynamic" if "Moving" in item["collision_path"] else "static"))


def _contact():
    return capture_contact_sample(
        {name: _ContactView() for name in BODY_PATHS}, body_paths=BODY_PATHS,
        static_collision_paths=[STATIC], physics_dt_s=0.005,
        body_positions_world_m={name: [0.0, 0.0, 0.0] for name in BODY_PATHS}, capacity=2)


def _complete_capture():
    capture = PhysxReferenceCapture(
        scenario_sha256="s" * 64, asset_sha256="a" * 64,
        robot={"id": "go2_1", "foot_body_paths": list(BODY_PATHS.values())},
        runtime={"isaac_runtime": True, "physics_dt_s": 0.005, "control_dt_s": 0.02},
        static_collision_paths=[STATIC])
    for step in (1, 2):
        capture.append(
            step=step, simulation_time_s=step * 0.02,
            root_state_w=[0.0, 0.0, 0.32, 1.0, 0.0, 0.0, 0.0] + [0.0] * 6,
            raycast=_raycast(), contact=_contact(),
            support={"status": "measured", "support_z_m": 0.0,
                     "calibrated": True, "evidence_kind": "calibrated_foot_contact",
                     "calibration_id": "c" * 64})
    return capture.finish()


def test_raycast_is_geometry_only_and_rejects_unresolved_nearer_hits():
    evidence = _raycast()
    assert evidence["status"] == "observed"
    assert evidence["contact_evidence"] is False

    dynamic = {"collision": "/World/MovingForklift", "rigid_body": "/World/MovingForklift",
               "position": [0.0, 0.0, -0.05], "normal": [0.0, 0.0, 1.0],
               "distance": 0.05}
    result = _raycast(_Query(dynamic))
    assert result["status"] == "unknown"
    assert result["reason"] == "nonstatic_or_unverified_collider"


def test_contact_adapter_preserves_exact_identity_and_raw_contacts():
    result = _contact()
    assert result["status"] == "observed"
    assert tuple(foot["body_path"] for foot in result["feet"]) == tuple(BODY_PATHS.values())
    assert result["calibrated"] is False


def test_missing_runtime_and_static_geometry_never_become_physical_success():
    assert validate_physx_evidence(None)["status"] == "pending"
    static_only = {"schema": "eai.factory_stair_static_measurement", "version": 1,
                   "status": "measured"}
    result = validate_physx_evidence(static_only)
    assert result["status"] == "unknown"
    assert result["execution_validated"] is False


def test_complete_isaac_record_is_observed_but_acceptance_remains_pending(tmp_path):
    record = _complete_capture()
    record["scenario_sha256"] = file_sha256(DEFAULT_SCENARIO)
    assert record["validation"]["status"] == "observed"
    assert record["validation"]["execution_validated"] is False
    assert record["validation"]["acceptance_status"] == "pending"

    path = tmp_path / "physx.json"
    path.write_text(json.dumps(record), encoding="utf-8")
    bundle = compile_challenge(physx_evidence_path=path)
    assert bundle["public_task"]["release_status"] == "pending"
    assert bundle["public_task"]["execution_validated"] is False
    assert bundle["public_task"]["release_gate"]["scoreable"] is False
    assert bundle["hidden_evaluator"]["support_truth"]["status"] == "observed"
    assert bundle["hidden_evaluator"]["reference_physical_acceptance"]["status"] == "pending"
    assert bundle["hidden_evaluator"]["reference_physical_acceptance"]["physx_evidence_status"] == "observed"
    assert bundle["public_task"]["scene"]["scenario_sha256"] == file_sha256(DEFAULT_SCENARIO)


def test_invalid_calibration_identity_is_unknown():
    record = _complete_capture()
    record["samples"][1]["support"]["calibration_id"] = "d" * 64
    result = validate_physx_evidence(record)
    assert result["status"] == "unknown"
    assert "support_calibration_id_changed" in result["reason"]
