import json
from copy import deepcopy

import pytest

from demo.benchmark.communication import CommunicationRuntime
from demo.benchmark.goal_communication_archive import (
    AGGREGATION_PER_TOOL_SUM,
    AGGREGATION_SHARED_TRANSPORT,
    COMMUNICATION_FORMAL_BLOCKER,
    build_communication_analysis,
)
from demo.benchmark.goal_scoring_adapter import (
    communication_analysis_observations,
)


def _runtime_report(pool_mode: bool = True, *, advance_s: float = 6.0):
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
    runtime.advance(advance_s)
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


def test_shared_runtime_ledger_is_counted_exactly_once():
    """Route decision 2026-09-24: one sealed transport archive, one count.

    Pose, camera, and communication tools reuse one CommunicationRuntime, so
    their tool reports are duplicate views of the same ledger.  With
    ``transport_report`` the episode totals come from that ledger once; the
    tool entries survive only as service-audit summaries.
    """
    shared = _runtime_report(advance_s=5.0)
    analysis = build_communication_analysis(
        {"pose_tools": deepcopy(shared), "camera_tools": deepcopy(shared),
         "communication_tools": deepcopy(shared)},
        episode_id="episode-shared-1", scene_key="city",
        transport_report=deepcopy(shared))
    assert analysis["aggregation"] == AGGREGATION_SHARED_TRANSPORT
    # Two publications, not six: the shared ledger is counted once.
    assert analysis["event_totals"] == {
        "published": 2, "delivered": 2, "dropped": 0, "expired": 0}
    transport = analysis["transport"]
    assert transport["status"] == "observed"
    assert transport["event_counts"]["published"] == 2
    assert transport["bytes_by_direction"] == {
        "uplink": transport["total_bytes_served"]}
    assert analysis["direction_breakdown"] == "complete"
    # Per-tool service audits stay visible without re-entering the totals.
    for name in ("pose_tools", "camera_tools", "communication_tools"):
        assert analysis["tools"][name]["status"] == "observed"
        assert analysis["tools"][name]["event_counts"]["published"] == 2
    assert analysis["quality_failures"] == []
    assert analysis["scoreable"] is False


def test_independent_runtimes_keep_the_per_tool_sum():
    """Regression: without a shared transport the historical sum stands."""
    reports = {name: _runtime_report(advance_s=5.0)
               for name in ("pose_tools", "camera_tools", "communication_tools")}
    analysis = build_communication_analysis(
        reports, episode_id="episode-independent-1", scene_key="city")
    assert analysis["aggregation"] == AGGREGATION_PER_TOOL_SUM
    assert "transport" not in analysis
    assert analysis["event_totals"]["published"] == 6
    assert analysis["event_totals"]["delivered"] == 6
    assert analysis["direction_breakdown"] == "complete"


def test_tampered_transport_report_is_refused_from_the_aggregation():
    shared = _runtime_report()
    tampered = deepcopy(shared)
    tampered["ledger"] = tampered["ledger"][:-1]
    with pytest.raises(ValueError, match="transport report failed replay validation"):
        build_communication_analysis(
            {"pose_tools": deepcopy(shared)}, episode_id="episode-tamper-1",
            scene_key="city", transport_report=tampered)
    with pytest.raises(ValueError, match="transport report must be a mapping"):
        build_communication_analysis(
            {"pose_tools": deepcopy(shared)}, episode_id="episode-tamper-2",
            scene_key="city", transport_report=[shared])


def test_transport_only_aggregation_is_allowed_without_tool_reports():
    """A runner-held runtime with no tool views still aggregates once."""
    analysis = build_communication_analysis(
        {}, episode_id="episode-transport-only", scene_key="city",
        transport_report=_runtime_report())
    assert analysis["aggregation"] == AGGREGATION_SHARED_TRANSPORT
    assert analysis["tools"] == {}
    assert analysis["event_totals"]["published"] == 2
    assert analysis["transport"]["status"] == "observed"
    # The development-track evidence discipline is unchanged in shared mode.
    assert analysis["development_track"]["infrastructure_blockers"] == [
        COMMUNICATION_FORMAL_BLOCKER]
    assert analysis["scoreable"] is False
