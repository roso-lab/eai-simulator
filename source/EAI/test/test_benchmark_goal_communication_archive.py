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


_V4_BYTE_ACCOUNTING = {
    "schema": "eai.benchmark.communication_byte_accounting",
    "version": 1,
    "treated_copy_semantics": "completed_whole_bytes_at_report_horizon",
    "ordinary_pool_copy_semantics": "whole_copy_charged_at_service_start",
    "total_physical_progress": False,
}


def _treatment(**changes):
    row = {
        "schema": "eai.benchmark.communication_publication_treatment",
        "version": 1,
        "semantics": "shared_fifo_anchored_window_v1",
        "binding_sha256": "a" * 64,
        "event_id": "stage59-byte-semantics",
        "trigger": "stage59-byte-semantics",
        "operation": "drop",
        "delay_s": 0.0,
        "drop": True,
        "bytes_per_window": 1000,
        "window_s": 100.0,
        "publication_validity_s": 100.0,
    }
    row.update(changes)
    return row


def _mixed_v4_report():
    runtime = CommunicationRuntime({"pools": {
        "backhaul_uplink": {"capacity_bps": 4.0}}})
    runtime.publish(
        message_id="treated-drop", producer_id="robot", recipients=["host"],
        channel="result", estimate={"value": "t"}, valid_until_s=100.0,
        pool="backhaul_uplink", direction="uplink",
        publication_treatment=_treatment())
    ordinary = runtime.publish(
        message_id="ordinary", producer_id="robot", recipients=["host"],
        channel="result", estimate={"value": "x" * 100}, valid_until_s=100.0,
        pool="backhaul_uplink", direction="uplink")
    runtime.advance(1.25)
    assert ordinary["size_bytes"] == 214
    return runtime.report()


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
    assert analysis["version"] == 2
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
    assert tool["byte_accounting"] is None
    assert tool["total_bytes_interpretation"] == "legacy_unspecified"
    assert tool["total_physical_progress"] is False
    assert analysis["byte_accounting"] is None
    assert analysis["total_bytes_interpretation"] == "legacy_unspecified"
    assert analysis["total_physical_progress"] is False


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


def test_mixed_v4_byte_semantics_survive_shared_archive_reduction():
    shared = _mixed_v4_report()
    analysis = build_communication_analysis(
        {}, episode_id="episode-stage59-shared", scene_key="city",
        transport_report=shared)

    transport = analysis["transport"]
    assert transport["total_bytes_served"] == 214
    assert 4.0 * shared["time_s"] == 5.0
    assert transport["byte_accounting"] == _V4_BYTE_ACCOUNTING
    assert transport["total_bytes_interpretation"] == "scheduler_charged_bytes"
    assert transport["total_physical_progress"] is False
    assert analysis["byte_accounting"] == _V4_BYTE_ACCOUNTING
    assert analysis["total_bytes_interpretation"] == "scheduler_charged_bytes"
    assert analysis["total_physical_progress"] is False


@pytest.mark.parametrize("corruption", ["missing", "tampered"])
def test_independent_v4_semantics_fail_closed_per_tool(corruption):
    valid = _mixed_v4_report()
    invalid = deepcopy(valid)
    if corruption == "missing":
        invalid.pop("byte_accounting")
    else:
        invalid["byte_accounting"]["total_physical_progress"] = True

    analysis = build_communication_analysis(
        {"valid_tool": valid, "invalid_tool": invalid},
        episode_id=f"episode-stage59-{corruption}", scene_key="city")

    assert analysis["tools"]["valid_tool"]["status"] == "observed"
    assert analysis["tools"]["valid_tool"]["total_bytes_served"] == 214
    assert analysis["tools"]["invalid_tool"]["status"] == "invalid"
    assert "v4 communication report failed replay validation" in analysis[
        "tools"]["invalid_tool"]["quality_failure"]
    assert analysis["byte_accounting"] == _V4_BYTE_ACCOUNTING
    assert analysis["quality_failures"]

    failed_only = build_communication_analysis(
        {"invalid_tool": invalid},
        episode_id=f"episode-stage59-only-{corruption}", scene_key="city")
    assert failed_only["byte_accounting"] is None
    assert failed_only["total_bytes_interpretation"] == "unavailable"
    assert failed_only["total_physical_progress"] is False


@pytest.mark.parametrize("invalid_version", [5, True, "3", None])
def test_explicit_unknown_or_malformed_tool_version_fails_closed(invalid_version):
    valid = _mixed_v4_report()
    invalid = deepcopy(valid)
    invalid["version"] = invalid_version
    invalid.pop("byte_accounting")

    analysis = build_communication_analysis(
        {"valid_tool": valid, "invalid_tool": invalid},
        episode_id=f"episode-stage59-version-{invalid_version!r}", scene_key="city")
    assert analysis["tools"]["valid_tool"]["status"] == "observed"
    assert analysis["tools"]["invalid_tool"]["status"] == "invalid"
    assert "unsupported version" in analysis["tools"]["invalid_tool"]["quality_failure"]
    assert analysis["byte_accounting"] == _V4_BYTE_ACCOUNTING

    failed_only = build_communication_analysis(
        {"invalid_tool": invalid},
        episode_id=f"episode-stage59-version-only-{invalid_version!r}", scene_key="city")
    assert failed_only["byte_accounting"] is None
    assert failed_only["total_bytes_interpretation"] == "unavailable"
    assert failed_only["total_physical_progress"] is False


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


def test_scoring_reader_preserves_historical_v1_analysis_without_upgrade(tmp_path):
    analysis = build_communication_analysis(
        {"pose_tools": _runtime_report()}, episode_id="legacy-analysis-v1",
        scene_key="city")
    analysis["version"] = 1
    for field in ("byte_accounting", "total_bytes_interpretation",
                  "total_physical_progress"):
        analysis.pop(field)
        analysis["tools"]["pose_tools"].pop(field)
    (tmp_path / "communication_analysis.json").write_text(
        json.dumps(analysis), encoding="utf-8")

    observed = communication_analysis_observations(tmp_path)
    assert observed["status"] == "observed"
    assert observed["report"] == analysis
