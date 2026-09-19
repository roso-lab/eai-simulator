"""Configuration-level Factory smoke input coverage tests."""

from tools.scenario_generation.configuration_smoke import (
    PROTOCOLS,
    build_record,
    build_suite,
    validate_suite,
)
from EAI.scenario_spec.validation import validate_scenario


def test_factory_smoke_suite_covers_all_connected_robots():
    suite = validate_suite(build_suite())
    assert suite["robot_count"] == 14
    assert len(suite["records"]) == 14
    assert {record["robot_key"] for record in suite["records"]} == {
        "carter", "simple_car", "pepper", "go2", "b2", "m20", "scout", "mushr_v2",
        "g1", "cf2x", "iris", "pegasus", "lite3", "coco",
    }


def test_protocol_primitive_matches_robot_family():
    records = validate_suite(build_suite())["records"]
    by_key = {record["robot_key"]: record for record in records}
    assert by_key["mushr_v2"]["protocol"]["id"] == "ackermann_navigation_v1"
    assert by_key["m20"]["protocol"]["id"] == "legged_navigation_v2"
    assert by_key["g1"]["protocol"]["id"] == "biped_navigation_v2"
    assert by_key["cf2x"]["protocol"]["id"] == "aerial_scan_v1"
    assert by_key["simple_car"]["sensor_tools"]["attachments"] == ["navigation_io"]
    assert by_key["scout"]["scenario"]["composition"]["targets"][0]["id"] == "scout_navigation_target"
    assert by_key["go2"]["scenario"]["composition"]["targets"][0]["position"] == [-1.95, -7.0, 0.0]
    for robot_key in ("cf2x", "iris", "pegasus"):
        assert not any(item.startswith("protocol_not_executable:")
                       for item in by_key[robot_key]["execution"]["blockers"])
    assert set(record["protocol"]["id"] for record in records) <= set(PROTOCOLS)


def test_generated_scenarios_pass_generic_v2_validation():
    for record in validate_suite(build_suite())["records"]:
        validate_scenario(record["scenario"])


def test_smoke_identity_matches_capability_profile():
    from tools.scenario_generation.capability_profile import profile_for_dossier
    from tools.scenario_generation.robot_dossier import robot_dossier, dossier_snapshot_hash

    record = build_record("mushr_v2")
    dossier = robot_dossier("mushr_v2", attachments=record["sensor_tools"]["attachments"])
    assert record["version"] == 2
    assert record["configuration_id"] == profile_for_dossier(dossier)["identity"]["configuration_id"]
    assert record["dossier_snapshot_sha256"] == dossier_snapshot_hash(dossier)


def test_missing_controller_is_explicitly_blocked(monkeypatch, tmp_path):
    monkeypatch.setenv("EAI_CONTROLLER_ROOT", str(tmp_path))
    record = build_record("carter")
    assert record["execution"]["status"] == "blocked"
    assert any(item.startswith("controller_asset_missing:")
               for item in record["execution"]["blockers"])


def test_aerial_position_smoke_rejects_direct_rotor_controller(monkeypatch):
    from tools.scenario_generation import robot_dossier

    original = robot_dossier.robot_dossier

    def rotor_dossier(robot_key, **kwargs):
        result = original(robot_key, **kwargs)
        result["controller"]["default_cfg"] = "PEGASUS_IRIS_ROTOR_CFG"
        return result

    monkeypatch.setattr(robot_dossier, "robot_dossier", rotor_dossier)
    record = build_record("iris")
    assert record["execution"]["status"] == "blocked"
    assert "protocol_not_executable:aerial_controller_has_no_paired_goal_position_adapter" in record["execution"]["blockers"]


def test_failed_physics_gate_is_not_executable():
    record = build_record("carter", physics_gate={"status": "failed", "reasons": ["base_support"]})
    assert record["execution"]["status"] == "not_executable"
    assert "physics_gate_failed:base_support" in record["execution"]["blockers"]


def test_geometry_failure_blocks_smoke_before_isaac(monkeypatch):
    from tools.scenario_generation import configuration_smoke

    monkeypatch.setitem(configuration_smoke._TARGET_BY_PROTOCOL, "biped_navigation_v2",
                        ("biped_navigation_target", (-4.95, 0.4, 0.0)))
    record = build_record("g1")
    assert record["execution"]["status"] == "blocked"
    assert "overlaps" in record["geometry_gate"]["reason"]


def test_suite_rejects_spawn_metadata_drift():
    import pytest

    suite = build_suite()
    record = next(item for item in suite["records"] if item["robot_key"] == "mushr_v2")
    assert record["spawn"]["position_xy_m"] == record["scenario"]["robots"][0]["spawn_pose"]["position"][:2]
    record["spawn"]["position_xy_m"] = [-3.45, -5.6]
    with pytest.raises(ValueError, match="metadata differs"):
        validate_suite(suite)


def test_suite_rejects_dossier_digest_used_as_configuration_identity():
    import pytest

    suite = build_suite()
    record = suite["records"][0]
    record["configuration_id"] = f'{record["robot_key"]}@{record["dossier_snapshot_sha256"][:16]}'
    with pytest.raises(ValueError, match="identity differs"):
        validate_suite(suite)
