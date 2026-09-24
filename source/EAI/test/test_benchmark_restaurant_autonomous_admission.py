"""Coverage failures must never be converted into participant decisions."""

from copy import deepcopy

import pytest

from demo.benchmark.goal_driven_tasks import build_goal_task_package
from demo.benchmark.goal_restaurant_autonomous_admission import (
    RestaurantPlatformCoverageError,
    build_restaurant_autonomous_admission,
    require_restaurant_autonomous_coverage,
    validate_restaurant_autonomous_admission,
)
from demo.benchmark.provenance import canonical_sha256


def test_full_condition_keeps_original_identity_and_all_candidates():
    before = build_goal_task_package("restaurant")
    report = build_restaurant_autonomous_admission()
    assert len(report["candidate_ids"]) == 8
    assert report["task_package_sha256"] == canonical_sha256(before)
    assert report["original_public_task_sha256"] == canonical_sha256(before["public_task"])
    assert report["effective_public_task_sha256"] != report["original_public_task_sha256"]
    assert before == build_goal_task_package("restaurant")
    assert validate_restaurant_autonomous_admission(report) == report


def test_inventory_identifies_real_missing_routes_and_stationary_iris():
    report = build_restaurant_autonomous_admission()
    rows = {(r["agent_id"], r["skill"]): r for r in report["coverage"]}
    carter = rows["restaurant_carter_1", "ground_navigation"]
    routes = {r["route_id"]: r["registered"] for r in carter["routes"]}
    assert routes["restaurant_staging_to_kitchen"]
    assert not routes["restaurant_kitchen_to_dining"]
    assert not routes["restaurant_dining_to_kitchen"]
    assert not rows["restaurant_iris_1", "aerial_navigation"]["handler_registered"]
    assert not rows["restaurant_iris_1", "observe"]["handler_registered"]
    assert rows["restaurant_iris_1", "relay"]["handler_registered"]
    assert not rows["restaurant_m20_1", "ground_navigation"]["handler_registered"]


def test_block_before_participant_without_false_team_infeasibility():
    with pytest.raises(RestaurantPlatformCoverageError) as exc:
        require_restaurant_autonomous_coverage()
    report = exc.value.report
    assert report["status"] == "platform_blocked"
    assert report["participant_called"] is False
    assert report["participant_decision"] is None
    assert report["feasibility_label"] is None
    assert report["oracle_status"] == "failed"
    for flag in ("execution_validated", "business_outcomes_validated", "scoreable"):
        assert report[flag] is False


@pytest.mark.parametrize("field,value", [
    ("status", "ready"), ("candidate_ids", ["restaurant_carter_1"]),
    ("coverage_gaps", []), ("remaining_gates", []),
    ("scoreable", True), ("scoreable", 0), ("source_sha256", {}),
    ("feasibility_label", "not_deployable"), ("unexpected", True),
])
def test_recomputed_self_hash_cannot_authorize_modified_report(field, value):
    report = deepcopy(build_restaurant_autonomous_admission())
    report[field] = value
    report.pop("report_sha256")
    report["report_sha256"] = canonical_sha256(report)
    with pytest.raises(ValueError, match="maintained source"):
        validate_restaurant_autonomous_admission(report)
