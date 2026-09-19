"""Pure tests for the complete EAI robot configuration coverage matrix."""

import json
import pytest

from tools.scenario_generation.capability_profile import (
    RADAR_AXES,
    validate_configuration_coverage,
)
from tools.scenario_generation.robot_dossier import all_robot_dossiers
from tools.scenario_generation.export_capability_profiles import _html, build_report


def test_all_connected_robot_types_have_explicit_control_coverage():
    dossiers = all_robot_dossiers()
    rows = validate_configuration_coverage(dossiers, expected_robot_keys=dossiers)
    assert len(rows) == 14
    assert {row["locomotion_mode"] for row in rows} >= {
        "differential", "ackermann", "legged", "legged_plus_wheels",
        "multirotor_rotor", "multirotor_thrust_torque",
    }
    assert all(set(row["radar_status"]) == set(RADAR_AXES) for row in rows)
    assert all(all(status == "unknown" for status in row["radar_status"].values()) for row in rows)
    assert all(set(row["manipulator_deployment"]) == {"ur5", "ur5_bare", "z1", "kinova_gen3"}
               for row in rows)


def test_configuration_coverage_rejects_missing_robot_type():
    dossiers = all_robot_dossiers()
    dossiers.pop("carter")
    with pytest.raises(ValueError, match="missing"):
        validate_configuration_coverage(dossiers, expected_robot_keys=all_robot_dossiers())


def test_html_export_contains_radar_axes_without_coercing_unknown_scores():
    output = _html(build_report())
    assert "<svg class=\"radar\"" in output
    assert "unknown" in output
    assert "polygon" not in output
    assert "Manipulator deployment evidence" in output
    assert "dynamic=blocked" in output
    assert "candidate" in output


def test_measurement_overlay_is_bound_to_selected_configuration(tmp_path):
    from benchmark_evidence_support import configuration_attempt
    from tools.scenario_generation.record_configuration_microtests import derive_records
    from tools.scenario_generation.export_capability_profiles import build_report
    from tools.scenario_generation.robot_dossier import robot_dossier

    dossier = robot_dossier("simple_car", attachments=["camera"])
    from tools.scenario_generation.capability_profile import profile_for_dossier
    configuration_id = profile_for_dossier(dossier)["identity"]["configuration_id"]
    scenario, attempt = configuration_attempt(tmp_path, skill="wait", attachments=["camera"])
    record, = derive_records(scenario, attempt)
    path = tmp_path / "measurements.json"
    path.write_text(json.dumps({"records": [record]}), encoding="utf-8")
    report = build_report(path)
    assert report["profiles"]["simple_car"]["identity"]["configuration_id"] == configuration_id
    assert report["profiles"]["simple_car"]["passing_evidence_tier"] == "E6"
    bad = dict(record, configuration_id="simple_car@" + "0" * 16)
    path.write_text(json.dumps({"records": [bad]}), encoding="utf-8")
    with pytest.raises(ValueError, match="configuration_id"):
        build_report(path)
