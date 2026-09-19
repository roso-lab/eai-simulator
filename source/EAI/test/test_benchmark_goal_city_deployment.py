"""City deployment overlay stays source-bound and fail-closed without Isaac."""

from copy import deepcopy
from pathlib import Path

import pytest

from demo.benchmark.goal_city_deployment import (
    _continuous_raster_check,
    _ground_profile_review,
    build_city_deployment_overlay,
    load_city_deployment_public,
)
from demo.benchmark.provenance import canonical_sha256, file_sha256
from demo.city_benchmark.prepare import prepare


ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def city_package(tmp_path_factory):
    package = tmp_path_factory.mktemp("city-deployment") / "prepared"
    prepare(package)
    return package


@pytest.fixture(scope="module")
def public_task(city_package):
    return load_city_deployment_public(city_package)


@pytest.fixture(scope="module")
def overlay(public_task, city_package):
    return build_city_deployment_overlay(public_task, city_package_dir=city_package)


def test_public_loader_returns_only_verified_public_task(public_task):
    assert public_task["mission"]["scene_key"] == "city"
    assert public_task["task_id"] == "CITY-GOAL-01"
    assert "private_authoring" not in public_task
    assert "integration_manifest" not in public_task


def test_common_overlay_covers_exact_public_identities(public_task, overlay):
    assert overlay["scene_key"] == "city"
    assert overlay["public_task_sha256"] == canonical_sha256(public_task)
    assert [row["id"] for row in overlay["object_bindings"]] == [
        row["id"] for row in public_task["public_world"]["objects"]
    ]
    assert [row["id"] for row in overlay["region_bindings"]] == [
        row["id"] for row in public_task["public_world"]["regions"]
    ]
    assert [row["id"] for row in overlay["route_bindings"]] == [
        row["id"] for row in public_task["public_world"]["routes"]
    ]
    assert all(row["public_robot"] == robot for row, robot in zip(
        overlay["robot_spawns"], public_task["candidate_robot_pool"], strict=True,
    ))
    assert len(overlay["static_obstacle_bindings"]) == 246
    assert {row["kind"] for row in overlay["static_obstacle_bindings"]} == {
        "building", "roadside_obstacle", "bus_shelter",
    }
    assert all(row["task_object"] is False and row["mass_policy"] == "not_declared_do_not_infer"
               for row in overlay["static_obstacle_bindings"])


def test_route_direction_and_map_evidence_are_not_promoted(public_task, overlay):
    expected = {row["id"]: row for row in public_task["public_world"]["routes"]}
    actual = {row["id"]: row for row in overlay["route_bindings"]}
    for identifier, row in actual.items():
        assert row["from_region_id"] == expected[identifier]["from_region_id"]
        assert row["to_region_id"] == expected[identifier]["to_region_id"]
        assert row["direction"] == "preserve_public_from_to"
        assert row["physical_route_validated"] is False
    ground = actual["city_ground_road_network"]
    assert ground["status"] == "map_checked_with_static_conflicts"
    assert len(ground["geometry"]["routes"]) == 12
    assert all(row["point_order"] == "declared_travel_direction"
               for row in ground["geometry"]["routes"])
    assert any(not row["occupancy_vertex_check"]["all_sampled_points_free"]
               for row in ground["geometry"]["routes"])
    assert any(not row["continuous_raster_check"]["all_supercover_cells_free"]
               for row in ground["geometry"]["routes"])
    for route in ground["geometry"]["routes"]:
        assert route["continuous_raster_check"]["segment_count"] == len(route["points_xy_m"]) - 1
        assert {row["id"] for row in route["ground_profile_reviews"]} == {"go2_1", "carter_1"}
        for review in route["ground_profile_reviews"]:
            assert review["inflation_policy"] == "consume_packaged_inflation_once_no_second_raster_dilation"
            assert review["existing_inflation_covers_radius"] is False
            assert review["additional_inflation_required_m"] > 0
            assert review["declared_width_margin_m"] > 0
            assert review["admission_status"] == "blocked"
    assert actual["city_aerial_road_network"]["status"] == "declared_envelope_unvalidated"
    assert actual["approach_building_001"]["status"] == "declared_endpoints_unrouted"


def test_each_public_robot_has_a_distinct_static_candidate_with_frozen_profile(public_task, overlay):
    robots = {row["id"]: row for row in public_task["candidate_robot_pool"]}
    spawns = overlay["robot_spawns"]
    assert {row["id"] for row in spawns} == set(robots)
    assert len({row["instance_id"] for row in spawns}) == len(spawns)
    assert len({tuple(row["position_xyz_m"]) for row in spawns}) == len(spawns)
    for row in spawns:
        assert row["instance_id"] == f"city::{row['id']}"
        assert row["physical_profile"] == robots[row["id"]]["physical_profile"]
        assert row["occupancy_check"]["classification"] == "free"
        assert row["static_obstacle_point_overlaps"] == []
        assert row["status"] == "map_checked_static_candidate"
        assert row["physical_spawn_validated"] is False
        assert row["orientation_wxyz"] is None
    assert overlay["dynamic_occupancy_policy"]["policy"] == "blocked_until_current_layers_are_admitted"
    assert overlay["dynamic_occupancy_policy"]["unknown_space_policy"] == "blocked"


def test_services_remain_pending_requirements_without_activation(overlay):
    services = {row["id"]: row for row in overlay["pending_service_requirements"]}
    assert set(services) == {"guide_people", "control_traffic", "report_incident"}
    assert all(row["activated"] is False for row in services.values())
    assert services["guide_people"]["participant_operation"] == "guide_people"
    assert services["guide_people"]["integration_contract"] == {
        "method": "CityServiceRequestAdapter.guide_people",
        "signature": "guide_people(*, person_ids, observation_refs, assembly_region_id, now_s=None)",
        "response_reference_method": "CityResponseServices.guide_people",
        "parameters": ["person_ids", "observation_refs", "assembly_region_id", "now_s"],
        "request_ack_scope": "trusted_adapter_request_ack_only",
        "required_trusted_source_callback": {
            "method": "CityServiceSource.role_interval",
            "adapter_parameter": "CityRuntimeReadbackAdapter.field_role_interval",
            "signature": "role_interval(agent_id, role, region_id, as_of_s)",
            "trust_boundary": "episode_owned_historical_role_readback_not_participant_supplied",
        },
        "role_continuity_requirement": "submit_through_each_actor_ack",
        "post_ack_trajectory_role_requirement": "original_member_may_exit_after_its_ack",
        "assignment_transfer": "unsupported_new_request_required",
        "external_effect_evidence": ["measured_person_trajectory", "assembly_arrival"],
    }
    assert "guidance_acknowledgement" in services["guide_people"]["receipt_requirements"]
    assert services["control_traffic"]["participant_operation"] == "control_following_traffic"
    assert services["control_traffic"]["integration_contract"]["parameters"] == [
        "vehicle_ids", "mode", "observation_refs",
    ]
    assert services["control_traffic"]["integration_contract"]["role_continuity_requirement"] == (
        "submit_through_each_actor_ack"
    )
    assert services["control_traffic"]["integration_contract"][
        "post_ack_trajectory_role_requirement"
    ] == "original_member_may_exit_after_its_ack"
    assert "traffic_control_acknowledgement" in services["control_traffic"]["receipt_requirements"]
    assert services["report_incident"]["participant_operation"] == "report_incident"
    assert services["report_incident"]["integration_contract"]["parameters"] == [
        "observation_refs", "recipient_service_id", "time_s",
    ]
    assert services["report_incident"]["integration_contract"]["role_continuity_requirement"] == (
        "submit_only"
    )
    assert all(row["integration_contract"]["assignment_transfer"]
               == "unsupported_new_request_required" for row in services.values())
    assert all(row["integration_contract"]["required_trusted_source_callback"]["adapter_parameter"]
               == "CityRuntimeReadbackAdapter.field_role_interval" for row in services.values())
    assert "response_service_receipt" in services["report_incident"]["receipt_requirements"]
    assert all(row["reference_only"] is True for row in services.values())
    assert all(row["request_ack_is_business_outcome"] is False for row in services.values())
    assert all(row["outcome_statuses"] == [
        "pending", "timed_out", "reference_complete", "evidence_complete",
    ] for row in services.values())
    assert all(row["trusted_readback_physically_authenticated"] is False
               for row in services.values())
    assert overlay["execution_validated"] is False
    assert overlay["scoreable"] is False
    assert all(f"pending_city_service:{name}" in overlay["blockers"] for name in services)


def test_source_files_bind_every_package_artifact_and_listed_local_source(overlay, city_package):
    source_files = overlay["source_files"]
    package_files = {path.resolve() for path in city_package.rglob("*") if path.is_file()}
    assert package_files <= {Path(path) for path in source_files}
    assert all(file_sha256(path) == digest for path, digest in source_files.items())
    expected_code = {
        ROOT / "demo/benchmark/goal_city_deployment.py",
        ROOT / "demo/benchmark/goal_city_intake.py",
        ROOT / "demo/benchmark/provenance.py",
        ROOT / "demo/city_benchmark/service_backend.py",
        ROOT / "demo/city_benchmark/response.py",
    }
    assert {path.resolve() for path in expected_code} <= {Path(path) for path in source_files}
    assert overlay["source_context"] == {
        "city_package_dir": str(city_package.resolve()),
        "source_verification_requested": True,
    }
    assert overlay["source_verification_requested"] is True
    assert overlay["source_verification"] == {
        "requested": True,
        "local_source_status": "listed_local_bytes_verified",
        "checked_local_file_count": 24,
        "package_artifact_integrity_validated": True,
    }


def test_swapped_public_task_is_rejected_before_an_overlay_is_built(public_task, city_package):
    altered = deepcopy(public_task)
    altered["candidate_robot_pool"][0]["physical_profile"]["body_size_m"][0] += 0.1
    with pytest.raises(ValueError, match="differs from the validated preparation package"):
        build_city_deployment_overlay(altered, city_package_dir=city_package)


def test_continuous_supercover_rejects_free_endpoints_with_an_occupied_middle_cell():
    metadata = {
        "resolution_m": 1.0, "origin_xyz_m": [0.0, 0.0, 0.0],
        "width": 5, "height": 1,
    }

    def read_cell(column, row):
        outside = not (0 <= column < 5 and row == 0)
        occupied = column == 2 and row == 0
        return {
            "pixel_column_row": [column, row],
            "value": None if outside else 0 if occupied else 254,
            "classification": "outside" if outside else "occupied" if occupied else "free",
        }

    assert read_cell(0, 0)["classification"] == read_cell(4, 0)["classification"] == "free"
    result = _continuous_raster_check([[0.5, 0.5], [4.5, 0.5]], metadata, read_cell)
    assert result["all_supercover_cells_free"] is False
    assert result["classification_counts"]["occupied"] == 1
    assert result["blocked_segment_indices"] == [0]


def test_profile_larger_than_existing_inflation_stays_blocked_on_an_all_free_route():
    robot = {
        "id": "wide_ground_1", "type": "wide_ground",
        "physical_profile": {"body_size_m": [2.0, 2.0, 1.0]},
    }
    route = {"clearance_m": [4.0, 4.0]}
    raster = {"all_supercover_cells_free": True}
    review = _ground_profile_review(robot, route, raster, 0.45)
    assert review["conservative_radius_m"] == pytest.approx(2 ** 0.5)
    assert review["existing_inflation_covers_radius"] is False
    assert review["additional_inflation_required_m"] == pytest.approx(2 ** 0.5 - 0.45)
    assert review["declared_width_margin_m"] > 0
    assert review["static_raster_status"] == "blocked"
    assert "conservative_envelope_radius_exceeds_existing_map_inflation" in review["blockers"]
