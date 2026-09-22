"""Offline checks for the opt-in seven-robot City pool (bound_native_v2).

The hard gate is hash invariance: the default (abstract_v1) and bound_native_v1
City packages must stay byte-for-byte unchanged while bound_native_v2 only adds
the opt-in roster, its spawn validator, and the probe-side pool switch.
"""
import json
from contextlib import nullcontext as _nullcontext

import pytest

from demo.benchmark.goal_driven_fixtures import robot_physical_profile
from demo.benchmark.goal_driven_tasks import (
    CITY_SPAWN_CLEARANCE_M,
    CITY_V2_SEMANTIC_MAP_PATH,
    CITY_V2_SPAWN_XY_M,
    build_goal_task_package,
    build_goal_task_spec,
    validate_city_spawn_points,
)
from demo.benchmark.provenance import canonical_sha256
from demo.city_benchmark import service_probe


# canonical_sha256 digests captured before the bound_native_v2 change; any edit
# that perturbs the default or v1 City authoring output breaks this gate.
_FROZEN_CITY_SHA256 = {
    ("abstract_v1", "public_task"): "10b1b9b40f5b3f1b7ca74be879cacff14c65085561eac5c7866de352237aecfb",
    ("abstract_v1", "hidden_evaluator"): "34f86e46d25383294eb351d9cf7b7dc871c5a104862a8b868c2227d6be680f30",
    ("bound_native_v1", "public_task"): "b62dd182990e6be116d190e0f29ff62613001347c1952d00ac4edd927561b19a",
    ("bound_native_v1", "hidden_evaluator"): "8a915e54b46e8390c5fa087df69bab0a7413b13df46b0f5ca4d22dbf14813373",
}
_V2_CORE_IDS = ("go2_1", "carter_1", "iris_1")
_V2_NEW_IDS = ("go2_2", "carter_2", "lite3_1", "iris_2")
_V1_PROBE_SPAWNS = [
    ("go2", "GO2_VELOCITY_RSL_CFG", [-40.0, 55.0, 0.62]),
    ("carter", "CARTER_DIFF_CFG", [-69.0, 25.0, 0.22]),
    ("iris", "PEGASUS_IRIS_POSITION_CFG", [-55.0, 20.0, 3.0]),
]
_V2_PROBE_SPAWNS = {
    "go2_2": ("go2", "GO2_VELOCITY_RSL_CFG", [-72.0, 40.0, 0.62]),
    "carter_2": ("carter", "CARTER_DIFF_CFG", [-55.0, 58.0, 0.22]),
    "lite3_1": ("lite3", "LITE3_VELOCITY_RSL_CFG", [-70.0, 55.0, 0.35]),
    "iris_2": ("iris", "PEGASUS_IRIS_POSITION_CFG", [-48.0, 57.0, 3.0]),
}


def test_default_and_native_v1_city_hashes_are_frozen():
    for profile in ("abstract_v1", "bound_native_v1"):
        bundle = build_goal_task_package("city", city_roster_profile=profile)
        assert canonical_sha256(bundle["public_task"]) == _FROZEN_CITY_SHA256[(profile, "public_task")]
        assert (canonical_sha256(bundle["hidden_evaluator"])
                == _FROZEN_CITY_SHA256[(profile, "hidden_evaluator")])
        assert canonical_sha256(build_goal_task_spec("city", city_roster_profile=profile)) \
            == _FROZEN_CITY_SHA256[(profile, "public_task")]


def test_v2_package_builds_with_seven_robot_pool_and_unchanged_budgets():
    bundle = build_goal_task_package("city", city_roster_profile="bound_native_v2")
    public = bundle["public_task"]
    assert public["task_id"] == "CITY-GOAL-NATIVE-02"
    ids = [row["id"] for row in public["candidate_robot_pool"]]
    assert len(ids) == 7 and len(set(ids)) == len(ids)
    assert ids[:3] == list(_V2_CORE_IDS) and sorted(ids[3:]) == sorted(_V2_NEW_IDS)
    v1 = build_goal_task_package("city", city_roster_profile="bound_native_v1")["public_task"]
    assert public["budgets"] == v1["budgets"]
    assert public["budgets"]["max_active_agents"] == 3
    assert public["skill_catalog_version"] == v1["skill_catalog_version"]
    assert public["execution_contract"] == v1["execution_contract"]


def test_v2_new_members_copy_core_skills_except_the_lite3_decoy():
    rows = {row["id"]: row for row in build_goal_task_spec(
        "city", city_roster_profile="bound_native_v2")["candidate_robot_pool"]}
    assert rows["go2_2"]["public_capabilities"] == rows["go2_1"]["public_capabilities"]
    assert rows["carter_2"]["public_capabilities"] == rows["carter_1"]["public_capabilities"]
    assert rows["iris_2"]["public_capabilities"] == rows["iris_1"]["public_capabilities"]
    for identifier in ("go2_2", "carter_2"):
        assert {"guide_people", "control_following_traffic"} <= set(rows[identifier]["public_capabilities"])
    assert "report_incident" in rows["iris_2"]["public_capabilities"]
    assert rows["lite3_1"]["public_capabilities"] == ["ground_navigation", "observe"]
    assert not ({"guide_people", "control_following_traffic", "report_incident"}
                & set(rows["lite3_1"]["public_capabilities"]))
    assert rows["lite3_1"]["mobility"] == "ground"


def test_v2_new_members_use_goal_driven_fixture_profiles():
    rows = {row["id"]: row for row in build_goal_task_spec(
        "city", city_roster_profile="bound_native_v2")["candidate_robot_pool"]}
    for identifier, robot_type in (("go2_2", "go2"), ("carter_2", "carter"),
                                   ("lite3_1", "lite3"), ("iris_2", "iris")):
        assert rows[identifier]["physical_profile"] == robot_physical_profile(robot_type)
    assert rows["lite3_1"]["physical_profile"]["configuration_id"] == "benchmark_lite3_navigation_v1"
    # The core three keep their frozen city-specific profiles.
    assert rows["go2_1"]["physical_profile"]["configuration_id"] == "city_go2_navigation_v1"
    assert rows["carter_1"]["physical_profile"]["configuration_id"] == "city_carter_navigation_v1"
    assert rows["iris_1"]["physical_profile"]["configuration_id"] == "city_iris_camera_v1"


def test_validate_city_spawn_points_accepts_the_frozen_v2_anchors():
    report = validate_city_spawn_points(CITY_V2_SPAWN_XY_M)
    assert report["validated"] is True
    assert report["point_count"] == 4 and sorted(report["accepted"]) == sorted(_V2_NEW_IDS)
    assert report["clearance_m"] == CITY_SPAWN_CLEARANCE_M == 1.5
    # Bare tuples and 3D spawn positions are accepted when the XY is clean.
    validate_city_spawn_points([(-72.0, 40.0), [-55.0, 58.0, 0.22], (-70.0, 55.0, 0.35)])
    # The build itself gates on the same validator, so the anchors must pass.
    assert set(CITY_V2_SPAWN_XY_M) == set(_V2_NEW_IDS)


def test_validate_city_spawn_points_synthetic_positive_and_negative_cases():
    synthetic = {
        "bindings": [{"id": "road_surface", "geometry": {"type": "Polygon", "coordinates": [
            [[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 10.0], [0.0, 0.0]],
            [[4.0, 4.0], [6.0, 4.0], [6.0, 6.0], [4.0, 6.0], [4.0, 4.0]],
        ]}}],
        "static_obstacles": [{"id": "roadside_999", "kind": "roadside_obstacle",
                              "bounds_m": [[8.0, 2.0, 0.2], [9.0, 3.0, 2.0]]}],
    }
    report = validate_city_spawn_points([(2.0, 2.0), (7.5, 5.0)], semantic_map=synthetic)
    assert report["semantic_map_source"] == "caller_supplied" and report["point_count"] == 2
    with pytest.raises(ValueError, match="outside the road_surface polygon"):
        validate_city_spawn_points([(20.0, 5.0)], semantic_map=synthetic)
    with pytest.raises(ValueError, match="inside a road_surface hole"):
        validate_city_spawn_points([(5.0, 5.0)], semantic_map=synthetic)
    # Obstacle box x[8,9] y[2,3] inflated by 1.5 m keeps (9.9, 3.6) blocked.
    with pytest.raises(ValueError, match=r"static obstacles \['roadside_999'\]"):
        validate_city_spawn_points([(9.9, 3.6)], semantic_map=synthetic)
    with pytest.raises(ValueError, match="not a two-coordinate point"):
        validate_city_spawn_points([("oops",)], semantic_map=synthetic)
    with pytest.raises(ValueError, match="at least one point"):
        validate_city_spawn_points([], semantic_map=synthetic)


def test_validate_city_spawn_points_rejects_the_sketched_unclean_spots():
    # The initially sketched carter_2/iris_2 coordinates fall inside a building
    # block hole of the frozen recovery_binding_v2 road polygon; the frozen
    # anchors are the nearest clean road_surface points instead.
    with pytest.raises(ValueError, match="inside a road_surface hole"):
        validate_city_spawn_points([(-55.0, 45.0)])
    with pytest.raises(ValueError):
        validate_city_spawn_points([(-48.0, 52.0)])


def test_v2_witnesses_stay_on_the_core_roster_with_pool_note():
    witnesses = build_goal_task_package(
        "city", city_roster_profile="bound_native_v2")["hidden_evaluator"]["witness_plans"]
    v1_witnesses = build_goal_task_package(
        "city", city_roster_profile="bound_native_v1")["hidden_evaluator"]["witness_plans"]
    assert len(witnesses) == 2
    for witness in witnesses:
        assert witness["id"].startswith("city_native_witness_")
        assert set(witness["plan"]["assigned_team"]) <= set(_V2_CORE_IDS)
        assert "bound_native_v2" in witness["pool_note"]
        assert "lite3_1" in witness["pool_note"]
    stripped = [{key: row[key] for key in ("id", "satisfies_outcomes", "outcome_support", "plan")}
                for row in witnesses]
    assert stripped == v1_witnesses  # v2 adds only the pool_note annotation


def test_city_roster_profile_scoping_and_rejection():
    with pytest.raises(ValueError, match="City roster profile applies only"):
        build_goal_task_package("factory", city_roster_profile="bound_native_v2")
    with pytest.raises(ValueError, match="unsupported City roster profile"):
        build_goal_task_package("city", city_roster_profile="bound_native_v3")


def test_service_probe_default_selection_is_unchanged_v1():
    for pool in (None, "v1"):
        selection = service_probe._selection(17, pool) if pool else service_probe._selection(17)
        assert selection["version"] == 1 and selection["task_name"] == "city_eai_demo"
        assert selection["scenario_seed"] == 17
        assert [(row["type"], row["controller"]["cfg"], row["spawn_pose"]["position"])
                for row in selection["robots"]] == _V1_PROBE_SPAWNS


def test_service_probe_v2_selection_adds_four_spawn_anchored_members():
    default = service_probe._selection(17)
    v2 = service_probe._selection(17, pool="v2")
    assert len(v2["robots"]) == 7 and v2["robots"][:3] == default["robots"]
    for row in v2["robots"][3:]:
        robot_type, cfg, position = _V2_PROBE_SPAWNS[
            f"lite3_1" if row["type"] == "lite3" else f"{row['type']}_2"]
        assert row["controller"] == {"mode": "default", "cfg": cfg}
        assert row["spawn_pose"]["position"] == position
        assert row["attachments"] == []
    # Spawn anchors mirror the goal-side validated CITY_V2_SPAWN_XY_M mapping.
    assert {identifier: tuple(spec[2][:2]) for identifier, spec in _V2_PROBE_SPAWNS.items()} \
        == {identifier: tuple(xy) for identifier, xy in CITY_V2_SPAWN_XY_M.items()}
    with pytest.raises(ValueError, match="unsupported City service probe pool"):
        service_probe._selection(17, "v3")


def test_service_probe_prepare_records_pool_and_execute_must_match(tmp_path, monkeypatch):
    # The shared canonical serial lock belongs to live probe runs; contract
    # checks follow the existing repo pattern of disabling it in tests.
    monkeypatch.setattr(service_probe, "_serial_lock", _nullcontext)
    report = service_probe.prepare_probe(tmp_path, pool="v2")
    assert report["pool"] == "v2"
    stored = json.loads((tmp_path / "service_probe_prepare.json").read_text(encoding="utf-8"))
    assert stored == report and stored["pool"] == "v2"
    with pytest.raises(ValueError, match="execute pool must match prepare"):
        service_probe.execute_probe(tmp_path, pool="v1")
    default_root = tmp_path / "default"
    assert service_probe.prepare_probe(default_root)["pool"] == "v1"


def test_service_probe_cli_pool_flag_defaults_to_v1(tmp_path, capsys):
    assert service_probe.main(["--output-root", str(tmp_path), "--prepare"]) == 0
    capsys.readouterr()
    assert json.loads((tmp_path / "service_probe_prepare.json").read_text(encoding="utf-8"))["pool"] == "v1"
    v2_root = tmp_path / "v2"
    assert service_probe.main(["--output-root", str(v2_root), "--prepare", "--pool", "v2"]) == 0
    capsys.readouterr()
    assert json.loads((v2_root / "service_probe_prepare.json").read_text(encoding="utf-8"))["pool"] == "v2"


def test_v2_anchors_resolve_against_the_frozen_recovery_binding_map():
    assert CITY_V2_SEMANTIC_MAP_PATH.is_file()
    validate_city_spawn_points(list(CITY_V2_SPAWN_XY_M.values()))
