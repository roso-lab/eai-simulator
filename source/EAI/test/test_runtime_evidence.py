import numpy as np
import pytest
from benchmark_evidence_support import frozen_attempt

from tools.scenario_generation.robot_dossier import all_robot_dossiers, dossier_snapshot_hash
from tools.scenario_generation.runtime_evidence import (
    import_execution_attempt, import_physics_measurements, import_settle_report,
    merge_runtime_evidence,
)

SCENARIO = "a" * 64
POLICY = "b" * 64


def _attempt(snapshot, tmp_path):
    return {
        **frozen_attempt(tmp_path, scenario_sha256=SCENARIO,
                         options={"transport_mode": "kinematic"}, tier="kinematic_execution_validated"),
        "robot_dossier_snapshot_sha256": snapshot,
        "payload_policy_hash": POLICY,
    }


def test_execution_import_requires_all_frozen_provenance(tmp_path):
    evidence = import_execution_attempt(_attempt("c" * 64, tmp_path), scenario_sha256=SCENARIO,
                                        robot_dossier_snapshot_sha256="c" * 64, payload_policy_hash=POLICY)
    assert evidence["tier"] == "E6"
    assert evidence["validation_status"] == "kinematic_execution_validated"
    with pytest.raises(ValueError, match="scenario hash"):
        import_execution_attempt({**_attempt("c" * 64, tmp_path), "scenario_sha256": "d" * 64},
                                 scenario_sha256=SCENARIO, robot_dossier_snapshot_sha256="c" * 64,
                                 payload_policy_hash=POLICY)
    with pytest.raises(ValueError, match="payload policy"):
        import_execution_attempt({**_attempt("c" * 64, tmp_path), "payload_policy_hash": "e" * 64},
                                 scenario_sha256=SCENARIO, robot_dossier_snapshot_sha256="c" * 64,
                                 payload_policy_hash=POLICY)


def test_settle_import_is_e5_and_requires_drift_and_hashes():
    report = {"status": "spawn_validated", "scenario_sha256": SCENARIO,
              "robot_dossier_snapshot_sha256": "c" * 64,
              "entities": {"go2_1": {"drift_m": 0.02}}}
    evidence = import_settle_report(report, scenario_sha256=SCENARIO,
                                    robot_dossier_snapshot_sha256="c" * 64)
    assert evidence["tier"] == "E5"
    assert evidence["artifact"]["max_drift_m"] == 0.02
    with pytest.raises(ValueError, match="drift"):
        import_settle_report({**report, "entities": {}}, scenario_sha256=SCENARIO,
                             robot_dossier_snapshot_sha256="c" * 64)


def test_physx_measurement_import_requires_finite_body_coverage():
    evidence = import_physics_measurements(
        {"bodies/transforms_xyzw": np.zeros((2, 7)),
         "bodies/velocities": np.zeros((2, 6)),
         "contacts/net_force_N": np.zeros((2, 3))},
        scenario_sha256=SCENARIO, robot_dossier_snapshot_sha256="c" * 64,
        expected_body_paths=["/World/go2_1", "/World/cargo"],
    )
    assert evidence["tier"] == "E5"
    with pytest.raises(ValueError, match="non-finite"):
        import_physics_measurements({"bodies/transforms_xyzw": np.array([[np.nan]])},
                                    scenario_sha256=SCENARIO,
                                    robot_dossier_snapshot_sha256="c" * 64,
                                    expected_body_paths=["/World/go2_1"])


def test_merge_updates_only_status_supported_by_imported_evidence(tmp_path):
    dossier = all_robot_dossiers()["go2"]
    snapshot = dossier_snapshot_hash(dossier)
    evidence = import_execution_attempt(_attempt(snapshot, tmp_path), scenario_sha256=SCENARIO,
                                        robot_dossier_snapshot_sha256=snapshot, payload_policy_hash=POLICY)
    merged = merge_runtime_evidence(dossier, evidence)
    assert merged["validation_status"]["overall"] == "kinematic_execution_validated"
    assert merged["validation_status"]["runtime_validated"] is True
    physics = import_physics_measurements({"bodies/transforms_xyzw": np.zeros((1, 7))},
                                          scenario_sha256=SCENARIO,
                                          robot_dossier_snapshot_sha256=snapshot,
                                          expected_body_paths=["/World/go2_1"])
    observed = merge_runtime_evidence(dossier, physics)
    assert observed["validation_status"]["overall"] == "registered"
    assert len(observed["evidence"]) == len(dossier["evidence"]) + 1
