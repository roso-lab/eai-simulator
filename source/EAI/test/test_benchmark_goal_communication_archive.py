import json

from demo.benchmark.communication import CommunicationRuntime
from demo.benchmark.goal_communication_archive import (
    COMMUNICATION_FORMAL_BLOCKER,
    build_communication_analysis,
)
from demo.benchmark.goal_scoring_adapter import (
    communication_analysis_observations,
)


def _runtime_report(pool_mode: bool = True):
    runtime = CommunicationRuntime({"pools": {
        "backhaul_uplink": {"capacity_bps": 20000.0, "ttl_s": 8.0}}} if pool_mode else None)
    runtime.publish(
        message_id="m1", producer_id="robot_1", recipients=["edge_1"],
        channel="observation", estimate={"k": 1}, valid_until_s=6.0,
        pool="backhaul_uplink" if pool_mode else None,
        direction="uplink" if pool_mode else None)
    runtime.publish(
        message_id="m2", producer_id="robot_1", recipients=["edge_1"],
        channel="observation", estimate={"k": 2}, valid_until_s=6.0,
        pool="backhaul_uplink" if pool_mode else None,
        direction="uplink" if pool_mode else None)
    runtime.advance(6.0)
    return runtime.report()


def test_analysis_folds_tool_ledgers_with_direction_intact():
    analysis = build_communication_analysis(
        {"pose_tools": _runtime_report()}, episode_id="episode-comm-1",
        scene_key="city")
    assert analysis["schema"] == "eai.benchmark.communication_analysis"
    assert analysis["status"] == "ok"
    tool = analysis["tools"]["pose_tools"]
    assert tool["status"] == "observed"
    assert tool["event_counts"]["published"] == 2
    assert tool["direction_complete"] is True
    assert tool["bytes_by_direction"]["uplink"] > 0
    assert analysis["event_totals"]["published"] == 2
    assert analysis["direction_breakdown"] == "complete"
    assert analysis["quality_failures"] == []
    assert analysis["development_track"]["infrastructure_blockers"] == [
        COMMUNICATION_FORMAL_BLOCKER]
    assert analysis["scoreable"] is False


def test_analysis_fails_closed_when_direction_metadata_is_missing():
    analysis = build_communication_analysis(
        {"pose_tools": _runtime_report(pool_mode=False)}, episode_id="e2",
        scene_key="warehouse_nvd")
    tool = analysis["tools"]["pose_tools"]
    assert tool["direction_complete"] is False
    assert tool["bytes_by_direction"] is None
    # Totals and pool-free byte counts survive; the breakdown does not guess.
    assert tool["total_bytes_served"] > 0
    assert analysis["direction_breakdown"] == "fail_closed_null"


def test_malformed_tool_report_is_a_quality_failure_not_zero_evidence():
    analysis = build_communication_analysis(
        {"pose_tools": {"schema": "eai.benchmark.communication", "ledger": None}},
        episode_id="e3", scene_key="factory")
    assert analysis["tools"]["pose_tools"]["status"] == "invalid"
    assert analysis["quality_failures"]
    assert analysis["event_totals"]["published"] == 0
    assert analysis["scoreable"] is False


def test_analysis_requires_at_least_one_tool_report():
    for bad in ({}, None, {"": _runtime_report()}):
        try:
            build_communication_analysis(bad, episode_id="e4", scene_key="city")
        except ValueError:
            continue
        raise AssertionError(f"accepted invalid tool reports: {bad!r}")


def test_sidecar_observes_the_archived_analysis(tmp_path):
    analysis = build_communication_analysis(
        {"pose_tools": _runtime_report()}, episode_id="episode-arch",
        scene_key="city")
    (tmp_path / "communication_analysis.json").write_text(
        json.dumps(analysis), encoding="utf-8")
    observed = communication_analysis_observations(tmp_path)
    assert observed["status"] == "observed"
    assert observed["report"]["episode_id"] == "episode-arch"
    assert observed["scoreable"] is False
