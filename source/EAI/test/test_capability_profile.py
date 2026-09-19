"""Capability profile and radar data contract tests."""

import pytest

from tools.scenario_generation.capability_profile import (
    RADAR_AXES,
    all_robot_capability_profiles,
    profile_for_dossier,
)


def _dossier():
    return {
        "robot_key": "m20",
        "controller": {"default_cfg": "M20_CFG", "family": "rsl_rl",
                       "command_semantics": "velocity_command"},
        "morphology": {"locomotion": "legged_plus_wheels", "domain": "ground"},
        "sensor_mounts": ["lidar", "realsense_d455"],
        "selected_attachments": [],
        "capabilities": {"navigation": {"value": True}, "aerial_scan": {"value": False}},
        "sensing": {"capabilities": {"lidar": {"value": True}}},
    }


def test_unmeasured_profile_keeps_every_radar_axis_unknown():
    profile = profile_for_dossier(_dossier())
    assert [item["axis"] for item in profile["radar"]] == list(RADAR_AXES)
    assert all(item["status"] == "unknown" and item["normalized"] is None
               for item in profile["radar"])
    assert profile["measurement_count"] == 0
    assert profile["hard_constraints"]["supports_navigation"]


def test_measured_axis_retains_raw_evidence_and_averages_repeats():
    measurements = [
        {"axis": "scene_traversability", "raw_value": 9, "unit": "successes/10",
         "normalized": 0.9, "protocol_id": "factory-nav-v1", "sample_count": 10,
         "evidence_id": "ev-a", "evidence_tier": "E6"},
        {"axis": "scene_traversability", "raw_value": 8, "unit": "successes/10",
         "normalized": 0.8, "protocol_id": "factory-nav-v1", "sample_count": 10,
         "evidence_id": "ev-b", "evidence_tier": "E6"},
    ]
    radar = profile_for_dossier(_dossier(), measurements)["radar"][0]
    assert radar["status"] == "measured"
    assert radar["normalized"] == pytest.approx(0.85)
    assert radar["measurements"][0]["raw_value"] == 9
    assert radar["measurements"][1]["evidence_id"] == "ev-b"
    assert profile_for_dossier(_dossier(), measurements)["passing_evidence_tier"] == "E6"


def test_invalid_measurement_fails_closed():
    with pytest.raises(ValueError, match="normalized"):
        profile_for_dossier(_dossier(), [{
            "axis": "motion_efficiency", "raw_value": 1, "unit": "ratio",
            "normalized": 2, "protocol_id": "p", "sample_count": 1, "evidence_id": "e",
        }])


def test_all_profiles_can_be_exported_without_inventing_scores():
    profiles = all_robot_capability_profiles({"m20": _dossier(), "cf2x": {
        **_dossier(), "robot_key": "cf2x", "morphology": {"locomotion": "multirotor", "domain": "aerial"},
        "controller": {"default_cfg": "CF2X_CFG", "family": "skrl", "command_semantics": "thrust"},
        "capabilities": {"navigation": {"value": True}, "aerial_scan": {"value": True}},
    }})
    assert set(profiles) == {"m20", "cf2x"}
    assert all(item["measurement_count"] == 0 for item in profiles.values())
    assert all(axis["normalized"] is None for profile in profiles.values() for axis in profile["radar"])
