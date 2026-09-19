"""Pure Factory business metric tests."""

from demo.factory_benchmark.evaluation import (
    coverage_freshness_metrics,
    coverage_member_exit_metrics,
    inspection_detection_metrics,
    inspection_observation_metrics,
    robot_spacing_metrics,
    scheduling_metrics,
    shared_resource_metrics,
)


def _scheduling():
    return {
        "time_s": 8.0,
        "states": {
            "west": {"status": "succeeded", "ready_at_s": 0, "started_at_s": 0,
                     "finished_at_s": 4},
            "south": {"status": "succeeded", "ready_at_s": 0, "started_at_s": 4,
                      "finished_at_s": 8},
        },
        "resource_requirements": {
            "west": ["shared:midband"], "south": ["shared:midband"]
        },
    }


def test_traffic_metrics_measure_wait_throughput_and_shared_occupancy():
    scheduling = _scheduling()
    assert scheduling_metrics(scheduling, required_nodes=["west", "south"])["validated"]
    metrics = shared_resource_metrics(scheduling, ["midband"])
    assert metrics["reservation_conflicts"] == []
    assert metrics["occupancy"]["shared:midband"][1]["start_s"] == 4
    assert robot_spacing_metrics([
        {"positions": {"a": [0, 0], "b": [0.8, 0]}}
    ], minimum_m=0.5)["validated"]


def test_inspection_metrics_require_actual_consumption():
    report = {"observations": [{
        "observation_id": "obs_1", "target_ref": "crate_a", "timestamp_s": 1,
        "consumed_by": [{"consumer": "route_confirmation", "timestamp_s": 2}],
    }]}
    metrics = inspection_observation_metrics(
        report, target_ref="crate_a", required_consumers=["route_confirmation"],
    )
    assert metrics["validated"]
    assert metrics["accuracy_validated"] is False


def test_inspection_metrics_can_isolate_one_view_from_shared_subject():
    report = {"observations": [
        {"observation_id": "aerial", "target_ref": "inspection_site", "consumed_by": []},
        {"observation_id": "ground", "target_ref": "inspection_site", "consumed_by": []},
    ]}
    metrics = inspection_observation_metrics(
        report, target_ref="inspection_site", observation_ids=["aerial"],
    )
    assert metrics["observation_ids"] == ["aerial"]
    assert metrics["observation_count"] == 1


def test_detection_metrics_compare_claims_to_evaluator_truth():
    report = {
        "observations": [
            {"observation_id": "frame_1", "target_ref": "inspection_site", "frame_id": "view_a"},
            {"observation_id": "frame_2", "target_ref": "inspection_site", "frame_id": "view_b"},
        ],
        "detections": [
            {"detection_id": "det_1", "source_observation_id": "frame_1",
             "target_id": "crate_a", "estimated_position_m": [1.0, 2.0, 0.4]},
            {"detection_id": "det_2", "source_observation_id": "frame_2",
             "target_id": "crate_a", "estimated_position_m": [1.01, 2.0, 0.4]},
        ],
    }
    metrics = inspection_detection_metrics(
        report, target_ref="inspection_site", expected_target_id="crate_a",
        ground_truth_position_m=[1.0, 2.0, 0.4], localization_tolerance_m=0.05,
        require_second_view=True,
    )
    assert metrics["identification_validated"]
    assert metrics["localization_validated"]
    assert metrics["second_view_validated"]
    assert metrics["validated"]


def test_detection_metrics_fail_closed_without_detector_output():
    metrics = inspection_detection_metrics(
        {"observations": [], "detections": []}, target_ref="inspection_site",
        expected_target_id="crate_a", ground_truth_position_m=[1, 2, 0.4],
        localization_tolerance_m=0.05,
    )
    assert metrics["validated"] is False
    assert metrics["ground_truth_source"] == "evaluator_only"


def test_coverage_metrics_fail_when_a_site_has_no_fresh_observation():
    report = {"observations": [{"target_ref": "storage", "timestamp_s": 8}]}
    metrics = coverage_freshness_metrics(report, site_ids=["storage", "dock"],
                                         now_s=10, freshness_bound_s=3)
    assert metrics["stale_sites"] == ["dock"]
    assert metrics["validated"] is False


def test_coverage_metrics_measure_periodic_gap_and_final_age():
    report = {"observations": [
        {"target_ref": "storage", "timestamp_s": 1},
        {"target_ref": "storage", "timestamp_s": 3},
        {"target_ref": "storage", "timestamp_s": 5},
    ]}
    metrics = coverage_freshness_metrics(
        report, site_ids=["storage"], now_s=6, freshness_bound_s=2,
    )
    assert metrics["max_gap_by_site_s"]["storage"] == 2
    assert metrics["max_gap_s"] == 2
    assert metrics["validated"]


def test_navigation_success_and_unattributed_timestamps_do_not_prove_recovery():
    scheduling = {
        "states": {
            "storage_watch": {"status": "failed", "started_at_s": 10.0},
            "storage_takeover_arrive": {"status": "succeeded", "started_at_s": 12.0},
        },
    }
    metrics = coverage_member_exit_metrics(
        scheduling,
        execution_events=[{
            "event": "member_exit", "fault_id": "fault_1",
            "affected_nodes": ["storage_watch"],
        }],
        fault_events=[{
            "id": "fault_1", "kind": "member_exit", "time_s": 10.0,
            "agent": "simple_car_1", "replacement_agent": "simple_car_4",
        }],
        plan=[{
            "node": "storage_watch", "agent": "simple_car_1",
            "target_ref": "storage_site",
        }, {
            "node": "storage_takeover_arrive", "agent": "simple_car_4",
            "target_ref": "storage_site", "recovery_for": "storage_watch",
        }],
        observation_report={"observations": [
            {"target_ref": "storage_site", "timestamp_s": 9.0},
            {"target_ref": "storage_site", "timestamp_s": 13.0},
        ]},
    )
    assert not metrics["validated"]
    assert metrics["recoveries"][0]["recovery_latency_s"] is None


def test_member_exit_metrics_are_optional_for_baseline_coverage():
    metrics = coverage_member_exit_metrics(
        {"states": {}}, execution_events=[], fault_events=[], plan=[],
        observation_report={"observations": []},
    )
    assert metrics["required"] is False
    assert metrics["validated"] is True
